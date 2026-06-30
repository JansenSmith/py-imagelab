#!/usr/bin/env python3
"""filament_stack_to_phases — FDM filament-painting wrapper around imagephase.

Translates a filament stack (layer height + ordered hex/TD list) into the
phase-mode inputs that `imagephase` expects:

  • One brush PNG per derived phase (solid color = Beer-Lambert blend at
    that print depth).
  • An init-canvas PNG (solid bottom-filament color).
  • A shell script `phase_run.sh` invoking `imagephase` with all flags.
  • A `phases.json` capturing schedule provenance (hex, opacity, ΔE-to-prior,
    source filament index, intra-filament layer index).

Per-filament phase count is derived via Beer-Lambert iteration; the cascade
stops on the FIRST of:
  • ΔE_lab(layer_color, prev_layer_color) < deltae_threshold  (perceptual
    convergence — "the next layer would look the same as the last")
  • cumulative opacity for this filament > opacity_ceiling     (hard
    fallback — saturation is asymptotic, this is the practical ceiling)

Default ΔE formula is CIE 1976 (Euclidean Lab distance). Kromacut uses the
same formula at the same 2.3 threshold in production; see CITATIONS.md.
ΔE 2000 was considered (more perceptually uniform) and deferred — research
showed no clear advantage at this threshold for FDM filament hues.

Default canvas-init color is the bottom filament's pure hex (assumed
saturated since the print's base layers stack to ≥99% opacity in any
realistic configuration).
"""
import argparse
import json
import math
import os
import subprocess
import sys
from pathlib import Path

import cv2
import numpy as np
from PIL import Image


# ---------- defaults (calibrated against Kromacut; see CITATIONS.md) ----------

DEFAULT_DELTAE_THRESHOLD = 2.3   # CIE 1976 "just noticeable difference"
DEFAULT_OPACITY_CEILING = 0.85   # asymptote at which adding layers stops
                                  # changing the perceived color much
DEFAULT_BRUSH_SIZE = 256          # solid-color brush PNG dimensions
DEFAULT_OUT_DIR = "./phases_out/"


# ---------- color math ----------

def hex_to_rgb(hex_str):
    """'#RRGGBB' or 'RRGGBB' → (R, G, B) int tuple in 0-255."""
    s = hex_str.lstrip('#').lower()
    if len(s) != 6 or not all(c in '0123456789abcdef' for c in s):
        raise ValueError(f"invalid hex color: {hex_str!r} "
                         f"(expected '#RRGGBB' or 'RRGGBB')")
    return tuple(int(s[i:i + 2], 16) for i in (0, 2, 4))


def rgb_to_hex(rgb):
    """(R, G, B) int → '#RRGGBB' uppercase."""
    return '#{:02X}{:02X}{:02X}'.format(*(int(round(c)) for c in rgb))


def rgb_to_lab(rgb):
    """sRGB tuple (0-255) → CIE Lab tuple via OpenCV's well-tested cvtColor.
    OpenCV scales Lab to 0-255 internally for uint8 input — for distance
    math the absolute scale doesn't matter, but it's a different unit than
    "standard" Lab (which has L in 0-100). Distances should be compared
    consistently against thresholds computed in the same unit.

    We use uint8 → cv2 returns L in [0,255] where L=255 means white.
    Threshold of 2.3 stays meaningful: 2.3 cv2-Lab-units ≈ 2.3/2.55 ≈
    0.9 standard Lab units. Practically: tweaked via Kromacut comparison
    if needed.

    For consistency with Kromacut's convention, we map cv2's [0,255] L
    back to standard [0,100] by dividing by 2.55. a/b channels are already
    centered: cv2 adds 128 to fit into uint8.
    """
    arr = np.array([[list(rgb)]], dtype=np.uint8)  # shape (1,1,3)
    lab = cv2.cvtColor(arr, cv2.COLOR_RGB2LAB)
    L, a, b = lab[0, 0]
    return (float(L) * 100.0 / 255.0,
            float(a) - 128.0,
            float(b) - 128.0)


def deltaE_76(rgb_a, rgb_b):
    """CIE 1976 Lab ΔE (Euclidean distance in Lab). Matches Kromacut."""
    lab_a = rgb_to_lab(rgb_a)
    lab_b = rgb_to_lab(rgb_b)
    return math.sqrt(sum((x - y) ** 2 for x, y in zip(lab_a, lab_b)))


def beer_lambert_blend(filament_rgb, opacity, under_rgb):
    """Per-channel sRGB blend: C_out = C_filament * opacity + C_under * T.

    Note: applied directly in sRGB rather than linear RGB — matches
    HueForge's and Kromacut's convention. The art-project notes on
    hueforge_technical.md document the rationale (color filters and TDs
    are calibrated in sRGB end-to-end)."""
    T = 1.0 - opacity
    return tuple(
        f * opacity + u * T
        for f, u in zip(filament_rgb, under_rgb)
    )


def layer_opacity(thickness_mm, td_mm):
    """Beer-Lambert opacity at given cumulative thickness.

    T = 10 ** (-thickness / TD)
    opacity = 1 - T

    TD = thickness at which a solid block blocks ~90% of light (opacity=0.9).
    """
    T = 10.0 ** (-thickness_mm / td_mm)
    return 1.0 - T


# ---------- phase derivation ----------

class Filament:
    def __init__(self, hex_color, td_mm, name=None):
        self.hex = hex_color
        self.rgb = hex_to_rgb(hex_color)
        self.td = float(td_mm)
        self.name = name or f"{hex_color}@TD{td_mm}"

    def __repr__(self):
        return f"Filament({self.hex}, TD={self.td})"


class DerivedPhase:
    """One step in the print cascade: paint this filament with this
    cumulative thickness on top of the prior color, yielding this rgb."""
    def __init__(self, phase_idx, filament_idx, intra_filament_layer,
                 cumulative_thickness, opacity, rgb, delta_e_to_prior):
        self.phase = phase_idx            # 1-indexed for output convenience
        self.filament_idx = filament_idx  # 0-indexed
        self.intra_filament_layer = intra_filament_layer  # 1-indexed
        self.cumulative_thickness = cumulative_thickness
        self.opacity = opacity
        self.rgb = rgb
        self.hex = rgb_to_hex(rgb)
        self.delta_e_to_prior = delta_e_to_prior

    def to_dict(self):
        return {
            'phase': self.phase,
            'filament_idx': self.filament_idx,
            'intra_filament_layer': self.intra_filament_layer,
            'cumulative_thickness_mm': round(self.cumulative_thickness, 6),
            'opacity': round(self.opacity, 6),
            'rgb': list(self.rgb),
            'hex': self.hex,
            'delta_e_to_prior': round(self.delta_e_to_prior, 4),
        }


def derive_phases(filaments, layer_height_mm,
                  deltae_threshold=DEFAULT_DELTAE_THRESHOLD,
                  opacity_ceiling=DEFAULT_OPACITY_CEILING,
                  include_no_op_layers=False,
                  deltae_fn=deltaE_76):
    """Walk filament stack bottom-up. Return a list of DerivedPhase.

    The bottom filament is assumed pre-saturated — the print's base layers
    stack to ~100% opacity, so the visible color is the bottom filament's
    pure hex. We start the canvas at that color and don't add a phase for
    the bottom filament itself unless `include_no_op_layers` is set.

    For each filament above the bottom, iterate layer-by-layer applying
    Beer-Lambert against a FIXED `under_rgb` (the color of the stack below
    this filament — i.e., the previous filament's final rendered color, or
    the bottom-filament canvas for filament[1]). Record a phase whenever
    the new layer's color differs from the last recorded color by ≥ ΔE
    threshold. Stop when (a) opacity ceiling reached OR (b) we've recorded
    at least one phase AND the latest layer is within threshold of the
    last recorded color (perceptual convergence).
    """
    if len(filaments) < 1:
        raise ValueError("at least one filament required")
    if layer_height_mm <= 0:
        raise ValueError(f"layer_height must be > 0, got {layer_height_mm}")

    # canvas_rgb = the last RECORDED phase color (or initial bottom
    # filament for the first iteration). Used for distinctness check —
    # "is this new layer different enough from the last recorded phase
    # to deserve its own phase?"
    canvas_rgb = filaments[0].rgb
    phases = []
    phase_counter = 0

    for fi, fil in enumerate(filaments):
        # Skip bottom filament unless include_no_op (every layer of it
        # blends to canvas_rgb itself — no visible change).
        if fi == 0 and not include_no_op_layers:
            continue

        # under_rgb stays fixed for this filament's whole sweep — it's the
        # color of the stack BELOW this filament. Beer-Lambert at each
        # cumulative thickness gives the visible color when looking through
        # `thickness` mm of this filament on top of `under_rgb`.
        under_rgb = canvas_rgb

        # prev_layer_rgb = the previous LAYER's color (any layer, recorded
        # or not). Used for convergence check — "did this iteration's color
        # change enough from the prior iteration to make further iteration
        # worthwhile?" Note: None on iter 1 so the convergence check is
        # skipped on the first layer.
        prev_layer_rgb = None
        last_layer_rgb = None  # for carry-forward to next filament
        intra = 0

        while True:
            intra += 1
            thickness = intra * layer_height_mm
            opacity = layer_opacity(thickness, fil.td)
            layer_rgb = beer_lambert_blend(fil.rgb, opacity, under_rgb)
            last_layer_rgb = layer_rgb

            # Distinctness check (against last recorded phase): is this
            # layer perceptually distinguishable from the prior recorded
            # phase color? If yes, record it.
            de_from_canvas = deltae_fn(layer_rgb, canvas_rgb)
            if de_from_canvas >= deltae_threshold or include_no_op_layers:
                phase_counter += 1
                phases.append(DerivedPhase(
                    phase_idx=phase_counter,
                    filament_idx=fi,
                    intra_filament_layer=intra,
                    cumulative_thickness=thickness,
                    opacity=opacity,
                    rgb=layer_rgb,
                    delta_e_to_prior=de_from_canvas,
                ))
                canvas_rgb = layer_rgb

            # Convergence check (against prev LAYER): is this iteration's
            # color change too small to make further iteration worthwhile?
            # Skipped on layer 1 (no prior layer to compare to).
            if (prev_layer_rgb is not None
                    and not include_no_op_layers):
                de_consecutive = deltae_fn(layer_rgb, prev_layer_rgb)
                if de_consecutive < deltae_threshold:
                    break

            # Opacity ceiling: filament is asymptotically saturated.
            if opacity >= opacity_ceiling:
                break
            # Safety net.
            if intra > 1000:
                break

            prev_layer_rgb = layer_rgb

    return phases


# ---------- CLI ----------

def _parse_filament_arg(s):
    """'#000000:0.3' or '000000:0.3' → Filament(...)."""
    if ':' not in s:
        raise argparse.ArgumentTypeError(
            f"--filament expects '<hex>:<TD>', got {s!r}"
        )
    parts = s.split(':')
    if len(parts) != 2:
        raise argparse.ArgumentTypeError(
            f"--filament expects exactly one ':', got {s!r}"
        )
    hex_str, td_str = parts
    try:
        # Validate hex via hex_to_rgb (raises ValueError on bad input).
        hex_to_rgb(hex_str)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(str(exc))
    try:
        td = float(td_str)
    except ValueError:
        raise argparse.ArgumentTypeError(
            f"--filament TD must be a number, got {td_str!r}"
        )
    if td <= 0:
        raise argparse.ArgumentTypeError(
            f"--filament TD must be > 0, got {td}"
        )
    return Filament(hex_str, td)


def _load_filaments_from_hfp(hfp_path):
    """Parse a HueForge .hfp JSON file. Returns (layer_height, filaments).

    HFP `filament_set` JSON list order is reversed from print order:
    index 0 is the TOP filament; the last is the BOTTOM. We reverse to
    get print order (bottom-up).

    Does NOT use `slider_values` — the F7 contract is "derive phases from
    Beer-Lambert convergence," ignoring the HFP artist's swap schedule.
    """
    with open(hfp_path, 'r') as f:
        data = json.load(f)
    layer_height = float(data.get('layer_height') or 0.04)
    filament_set = data.get('filament_set') or []
    # Reverse to print order:
    filaments = []
    for f in reversed(filament_set):
        hex_color = f.get('Color') or f.get('color')
        td = f.get('Transmissivity') or f.get('transmissivity') or f.get('TD')
        name = f.get('Name') or f.get('name')
        if not hex_color or td is None:
            continue
        fil = Filament(hex_color, td, name=name)
        filaments.append(fil)
    if not filaments:
        raise ValueError(
            f"no filaments parseable from HFP: {hfp_path}"
        )
    return layer_height, filaments


def get_arg_parser():
    parser = argparse.ArgumentParser(
        prog='filament_stack_to_phases',
        description=(
            "Translate an FDM filament stack into imagephase phase-mode "
            "inputs (brushes, init canvas, run script, phase manifest)."
        ),
    )
    parser.add_argument(
        '--target', required=True,
        help="Target image to evolve toward (passed as imagephase's target).",
    )
    parser.add_argument(
        '--layer-height', type=float, default=None,
        help="Layer height in mm (e.g. 0.04). Required unless --hfp is used.",
    )
    parser.add_argument(
        '--filament', action='append', dest='filaments',
        type=_parse_filament_arg, default=[],
        help="Repeatable; one per filament in print order (bottom→top). "
             "Format: '<hex>:<TD>'. Required unless --hfp is used.",
    )
    parser.add_argument(
        '--hfp', default=None,
        help="Optional convenience: HueForge .hfp file. Pre-fills "
             "--layer-height + --filament from the HFP's filament_set "
             "(reversed to print order). Does NOT use slider_values — "
             "phase counts are always re-derived from Beer-Lambert.",
    )
    parser.add_argument(
        '--out-dir', default=DEFAULT_OUT_DIR,
        help=f"Output directory (default: {DEFAULT_OUT_DIR})",
    )
    parser.add_argument(
        '--deltae-threshold', type=float, default=DEFAULT_DELTAE_THRESHOLD,
        help=f"Perceptual convergence floor in CIE 1976 ΔE units "
             f"(default: {DEFAULT_DELTAE_THRESHOLD}, matches Kromacut JND)",
    )
    parser.add_argument(
        '--opacity-ceiling', type=float, default=DEFAULT_OPACITY_CEILING,
        help=f"Per-filament opacity ceiling at which iteration stops "
             f"(default: {DEFAULT_OPACITY_CEILING}, matches Kromacut)",
    )
    parser.add_argument(
        '--include-no-op-layers', action='store_true',
        help="Disable auto-skip of layers whose color matches the canvas "
             "within --deltae-threshold. Default: skip (no point painting "
             "what's already there). Use for debugging the cascade.",
    )
    parser.add_argument(
        '--run', action='store_true',
        help="Invoke `imagephase` directly with the generated config. "
             "Otherwise, only emits `phase_run.sh` (executable) for the "
             "artist to inspect / customize before invoking.",
    )
    return parser


def write_brush_png(rgb, path, size=DEFAULT_BRUSH_SIZE):
    """Solid-color sRGB RGB PNG (24-bit; pygame.smoothscale needs this)."""
    int_rgb = tuple(int(round(c)) for c in rgb)
    Image.new('RGB', (size, size), int_rgb).save(str(path), 'PNG')


def write_init_canvas(rgb, target_size, path):
    """Solid-color init canvas matching the target image's dimensions."""
    int_rgb = tuple(int(round(c)) for c in rgb)
    Image.new('RGB', target_size, int_rgb).save(str(path), 'PNG')


def emit_phase_run_sh(out_dir, target_path, init_canvas_path,
                       brush_paths, prefix='fdm'):
    """Write a shell script invoking `imagephase` with all wired flags."""
    n = len(brush_paths)
    brush_args = ' '.join(str(p) for p in brush_paths)
    script = f"""#!/usr/bin/env bash
# Generated by filament_stack_to_phases.
# Edit as needed before running; defaults are sensible but per-piece
# tuning may improve results (e.g. --phase-max-radius / --phase-min-radius
# from F8 derivation, or --plateau-window / --plateau-delta tuning).
set -euo pipefail

imagephase {target_path} \\
  --start-canvas {init_canvas_path} \\
  --phases {n} \\
  --phase-brushes {brush_args} \\
  -S triangle \\
  --adaptive-cheat-mode --compare-strategy lab \\
  -o 500 \\
  --save-on-exit --close-on-exit -p {prefix} \\
  -d "$(dirname "$0")" \\
  "$@"
"""
    sh = out_dir / 'phase_run.sh'
    sh.write_text(script)
    sh.chmod(0o755)
    return sh


def emit_phases_json(out_dir, phases, layer_height, filaments,
                      deltae_threshold, opacity_ceiling):
    """Write phases.json with full provenance for reproducibility."""
    doc = {
        'version': 1,
        'layer_height_mm': layer_height,
        'deltae_threshold': deltae_threshold,
        'deltae_formula': 'CIE-1976',
        'opacity_ceiling': opacity_ceiling,
        'filaments': [
            {'idx': i, 'hex': f.hex, 'td': f.td, 'name': f.name}
            for i, f in enumerate(filaments)
        ],
        'phases': [p.to_dict() for p in phases],
    }
    out_path = out_dir / 'phases.json'
    out_path.write_text(json.dumps(doc, indent=2))
    return out_path


def run():
    parser = get_arg_parser()
    args = parser.parse_args()

    if args.hfp:
        try:
            hfp_layer_h, hfp_filaments = _load_filaments_from_hfp(args.hfp)
        except (FileNotFoundError, ValueError, json.JSONDecodeError) as exc:
            print(f"error: failed to read --hfp {args.hfp!r}: {exc}",
                  file=sys.stderr)
            sys.exit(1)
        if args.layer_height is None:
            args.layer_height = hfp_layer_h
        if not args.filaments:
            args.filaments = hfp_filaments

    if args.layer_height is None:
        print("error: --layer-height is required (or supply --hfp)",
              file=sys.stderr)
        sys.exit(1)
    if not args.filaments:
        print("error: at least one --filament is required (or supply --hfp)",
              file=sys.stderr)
        sys.exit(1)
    if not os.path.exists(args.target):
        print(f"error: --target {args.target!r} does not exist",
              file=sys.stderr)
        sys.exit(1)

    # Derive phases.
    try:
        phases = derive_phases(
            args.filaments, args.layer_height,
            deltae_threshold=args.deltae_threshold,
            opacity_ceiling=args.opacity_ceiling,
            include_no_op_layers=args.include_no_op_layers,
        )
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        sys.exit(1)

    if not phases:
        print(
            "warning: derived 0 phases. The filament stack produced no "
            "perceptually-distinct layers above the bottom filament. "
            "Check that filaments differ from each other and from "
            "the bottom by at least --deltae-threshold.",
            file=sys.stderr,
        )
        sys.exit(2)

    # Set up output dir.
    out_dir = Path(args.out_dir)
    brushes_dir = out_dir / 'brushes'
    brushes_dir.mkdir(parents=True, exist_ok=True)

    # Read target dims for init_canvas sizing.
    target_img = Image.open(args.target)
    target_size = target_img.size

    # Write brushes.
    brush_paths = []
    for p in phases:
        path = brushes_dir / f"phase_{p.phase:02d}.png"
        write_brush_png(p.rgb, path)
        brush_paths.append(path)

    # Write init canvas.
    init_path = out_dir / 'init_canvas.png'
    write_init_canvas(args.filaments[0].rgb, target_size, init_path)

    # Write phases.json + phase_run.sh.
    phases_json = emit_phases_json(
        out_dir, phases, args.layer_height, args.filaments,
        args.deltae_threshold, args.opacity_ceiling,
    )
    sh = emit_phase_run_sh(out_dir, args.target, init_path, brush_paths)

    # Friendly summary to stderr.
    print(
        f"filament_stack_to_phases: {len(phases)} phases derived from "
        f"{len(args.filaments)} filaments at layer_height={args.layer_height}mm "
        f"(ΔE threshold={args.deltae_threshold}, opacity ceiling={args.opacity_ceiling})",
        file=sys.stderr,
    )
    for p in phases:
        f = args.filaments[p.filament_idx]
        print(
            f"  phase {p.phase:>2}: {p.hex}  "
            f"(filament {p.filament_idx+1} {f.hex}, "
            f"layer {p.intra_filament_layer}, "
            f"opacity={p.opacity:.3f}, ΔE-to-prior={p.delta_e_to_prior:.2f})",
            file=sys.stderr,
        )
    print(f"  → wrote {len(brush_paths)} brush PNGs to {brushes_dir}/",
          file=sys.stderr)
    print(f"  → wrote {init_path}", file=sys.stderr)
    print(f"  → wrote {phases_json}", file=sys.stderr)
    print(f"  → wrote {sh}", file=sys.stderr)

    if args.run:
        print(f"\nrunning: bash {sh}", file=sys.stderr)
        result = subprocess.run(['bash', str(sh)])
        sys.exit(result.returncode)
    else:
        print(
            f"\nReady. Inspect {sh.name}, then run:\n  bash {sh}",
            file=sys.stderr,
        )

    return 0


if __name__ == '__main__':
    sys.exit(run())
