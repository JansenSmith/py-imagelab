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

# Physical-print defaults (F8 — nozzle-derived radius schedule).
# Calibrated against community research; see CITATIONS.md.
DEFAULT_PRINT_MAX_DIM_MM = 242.0  # artist's most common max-dim across
                                   # pieces; NOT universal — override per
                                   # piece (see art/pieces.md).
DEFAULT_TOWER_SAFETY_FACTOR = 3.0  # nozzle_mm × this = min reliable tower
                                    # diameter on X1C + 0.4mm nozzle at
                                    # filament-painting conditions
                                    # (community-convergent; see CITATIONS.md).
MIN_PIXELS_PER_MM = 2.0           # below this: features print too coarsely
MAX_PIXELS_PER_MM = 5.0           # above this: GA convergence wastefully slow
RADIUS_FRACTION_PHASE_0 = 0.12    # max-radius of bottom phase = 12% of print
                                   # max-dim (large coverage strokes).
RADIUS_FRACTION_PHASE_N = 0.025   # max-radius of top phase = 2.5% of print
                                   # max-dim (small detail strokes), with a
                                   # 3×min-radius-top floor for safety.


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


# ---------- radius schedule (F8) ----------

def derive_radius_schedule(n_phases, image_max_dim_px, print_max_dim_mm,
                            nozzle_mm, tower_safety_factor):
    """Per-phase (max_radius, min_radius) lists in pixels.

    Derivation:
        pixels_per_mm   = image_max_dim_px / print_max_dim_mm
        min_tower_mm    = nozzle_mm × tower_safety_factor
        min_radius_top  = max(2, ceil(min_tower_mm × pixels_per_mm / 2))

        max_radius_phase_0 = ceil(print_max_dim_mm × 0.12  × pixels_per_mm)
        max_radius_phase_N = max(min_radius_top × 3,
                                 ceil(print_max_dim_mm × 0.025 × pixels_per_mm))
        # linear-interpolated across n_phases

        min_radius_top   = (above)  — topmost phase
        min_radius_other = max(2, floor(min_radius_top × 0.5))

    Returns (max_radii, min_radii), each a list of length n_phases.
    """
    if n_phases < 1:
        raise ValueError(f"n_phases must be >= 1, got {n_phases}")
    if image_max_dim_px <= 0 or print_max_dim_mm <= 0:
        raise ValueError("image_max_dim_px and print_max_dim_mm must be > 0")
    if nozzle_mm <= 0 or tower_safety_factor <= 0:
        raise ValueError("nozzle_mm and tower_safety_factor must be > 0")

    pixels_per_mm = image_max_dim_px / print_max_dim_mm
    min_tower_mm = nozzle_mm * tower_safety_factor
    min_radius_top = max(2, math.ceil(min_tower_mm * pixels_per_mm / 2))

    max_r_phase_0 = math.ceil(
        print_max_dim_mm * RADIUS_FRACTION_PHASE_0 * pixels_per_mm
    )
    max_r_phase_N = max(
        min_radius_top * 3,
        math.ceil(print_max_dim_mm * RADIUS_FRACTION_PHASE_N * pixels_per_mm),
    )

    if n_phases == 1:
        max_radii = [max_r_phase_N]
    else:
        # Linear interpolation; phase 0 → max_r_phase_0, phase N-1 → max_r_phase_N.
        max_radii = []
        for i in range(n_phases):
            frac = i / (n_phases - 1)
            r = max_r_phase_0 + (max_r_phase_N - max_r_phase_0) * frac
            max_radii.append(max(min_radius_top, int(round(r))))

    min_radius_other = max(2, math.floor(min_radius_top * 0.5))
    if n_phases == 1:
        min_radii = [min_radius_top]
    else:
        min_radii = [min_radius_other] * (n_phases - 1) + [min_radius_top]

    return max_radii, min_radii


def pixels_per_mm_warning(pixels_per_mm):
    """Return a warning string if pixels_per_mm is out of recommended band,
    else None. Recommended sweet spot: ~3 px/mm."""
    if pixels_per_mm < MIN_PIXELS_PER_MM:
        return (
            f"target image too small relative to print "
            f"({pixels_per_mm:.2f} px/mm; recommended ≥ {MIN_PIXELS_PER_MM}). "
            f"Features will print blocky. Consider rescaling to a larger "
            f"max-dim."
        )
    if pixels_per_mm > MAX_PIXELS_PER_MM:
        return (
            f"target image very high resolution "
            f"({pixels_per_mm:.2f} px/mm; recommended ≤ {MAX_PIXELS_PER_MM}). "
            f"GA convergence will be slow with no fidelity gain. Consider "
            f"downscaling."
        )
    return None


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
        '--nozzle-mm', type=float, default=None,
        help="Nozzle diameter in mm (e.g. 0.4). When supplied, F8 derives a "
             "per-phase radius schedule (smallest features on top phase) and "
             "wires it into phase_run.sh via --phase-max-radius / "
             "--phase-min-radius. Without this flag, no radius schedule is "
             "emitted and imagephase's defaults apply (which may produce "
             "features smaller than the printer can resolve).",
    )
    parser.add_argument(
        '--print-max-dim-mm', type=float,
        default=DEFAULT_PRINT_MAX_DIM_MM,
        help=f"Physical max dimension of the printed piece in mm "
             f"(longest side, orientation-independent). Default: "
             f"{DEFAULT_PRINT_MAX_DIM_MM} (the artist's most common "
             f"max-dim). Override per piece — NOT universal.",
    )
    parser.add_argument(
        '--tower-safety-factor', type=float,
        default=DEFAULT_TOWER_SAFETY_FACTOR,
        help=f"Multiplier on --nozzle-mm yielding the minimum reliable "
             f"isolated tower diameter for the topmost phase. Default: "
             f"{DEFAULT_TOWER_SAFETY_FACTOR} (community-convergent for "
             f"Bambu X1C + 0.4mm nozzle at filament-painting conditions; "
             f"see tools/CITATIONS.md).",
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
                       brush_paths, prefix='fdm',
                       max_radii=None, min_radii=None):
    """Write a shell script invoking `imagephase` with all wired flags.

    If max_radii / min_radii supplied (length = n_phases), wires them
    via --phase-max-radius / --phase-min-radius (F8). Otherwise, omits
    those flags and imagephase's defaults apply.
    """
    n = len(brush_paths)
    brush_args = ' '.join(str(p) for p in brush_paths)
    radius_lines = ""
    if max_radii is not None and min_radii is not None:
        if len(max_radii) != n or len(min_radii) != n:
            raise ValueError(
                f"radius schedule length mismatch: phases={n}, "
                f"max_radii={len(max_radii)}, min_radii={len(min_radii)}"
            )
        radius_lines = (
            f"  --phase-max-radius {' '.join(str(r) for r in max_radii)} \\\n"
            f"  --phase-min-radius {' '.join(str(r) for r in min_radii)} \\\n"
        )
    script = f"""#!/usr/bin/env bash
# Generated by filament_stack_to_phases.
# Edit as needed before running; defaults are sensible but per-piece
# tuning may improve results (e.g. --plateau-window / --plateau-delta).
set -euo pipefail

imagephase {target_path} \\
  --start-canvas {init_canvas_path} \\
  --phases {n} \\
  --phase-brushes {brush_args} \\
{radius_lines}  -S triangle \\
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
                      deltae_threshold, opacity_ceiling,
                      radius_schedule=None):
    """Write phases.json with full provenance for reproducibility.

    Schema v2 adds optional `radius_schedule` block when F8 derivation
    is active. Absent → v1-equivalent payload.
    """
    doc = {
        'version': 2,
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
    if radius_schedule is not None:
        doc['radius_schedule'] = radius_schedule
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
    image_max_dim_px = max(target_size)

    # Write brushes.
    brush_paths = []
    for p in phases:
        path = brushes_dir / f"phase_{p.phase:02d}.png"
        write_brush_png(p.rgb, path)
        brush_paths.append(path)

    # Write init canvas.
    init_path = out_dir / 'init_canvas.png'
    write_init_canvas(args.filaments[0].rgb, target_size, init_path)

    # F8 — derive radius schedule when --nozzle-mm supplied.
    max_radii = None
    min_radii = None
    radius_schedule_doc = None
    if args.nozzle_mm is not None:
        try:
            max_radii, min_radii = derive_radius_schedule(
                n_phases=len(phases),
                image_max_dim_px=image_max_dim_px,
                print_max_dim_mm=args.print_max_dim_mm,
                nozzle_mm=args.nozzle_mm,
                tower_safety_factor=args.tower_safety_factor,
            )
        except ValueError as exc:
            print(f"error: radius schedule: {exc}", file=sys.stderr)
            sys.exit(1)
        pixels_per_mm = image_max_dim_px / args.print_max_dim_mm
        warn = pixels_per_mm_warning(pixels_per_mm)
        if warn:
            print(f"warning: {warn}", file=sys.stderr)
        radius_schedule_doc = {
            'nozzle_mm': args.nozzle_mm,
            'print_max_dim_mm': args.print_max_dim_mm,
            'tower_safety_factor': args.tower_safety_factor,
            'image_max_dim_px': image_max_dim_px,
            'pixels_per_mm': round(pixels_per_mm, 4),
            'max_radii_px': max_radii,
            'min_radii_px': min_radii,
        }

    # Write phases.json + phase_run.sh.
    phases_json = emit_phases_json(
        out_dir, phases, args.layer_height, args.filaments,
        args.deltae_threshold, args.opacity_ceiling,
        radius_schedule=radius_schedule_doc,
    )
    sh = emit_phase_run_sh(
        out_dir, args.target, init_path, brush_paths,
        max_radii=max_radii, min_radii=min_radii,
    )

    # Friendly summary to stderr.
    print(
        f"filament_stack_to_phases: {len(phases)} phases derived from "
        f"{len(args.filaments)} filaments at layer_height={args.layer_height}mm "
        f"(ΔE threshold={args.deltae_threshold}, opacity ceiling={args.opacity_ceiling})",
        file=sys.stderr,
    )
    for i, p in enumerate(phases):
        f = args.filaments[p.filament_idx]
        radius_tail = ""
        if max_radii is not None:
            radius_tail = f", radius {min_radii[i]}-{max_radii[i]}px"
        print(
            f"  phase {p.phase:>2}: {p.hex}  "
            f"(filament {p.filament_idx+1} {f.hex}, "
            f"layer {p.intra_filament_layer}, "
            f"opacity={p.opacity:.3f}, ΔE-to-prior={p.delta_e_to_prior:.2f}"
            f"{radius_tail})",
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
