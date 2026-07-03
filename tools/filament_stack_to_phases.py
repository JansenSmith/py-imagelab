#!/usr/bin/env python3
"""filament_stack_to_phases — FDM filament-painting wrapper around imagephase.

**HFP-driven, saturated-endpoint interpolation (2026-07-02).** Reads a
HueForge `.hfp` file (required) and produces phase-mode inputs that
`imagephase` expects:

  • One brush PNG per derived phase (solid color = interpolation between
    previous-filament saturated color and current-filament saturated color).
  • An init-canvas PNG (solid bottom-filament color — assumed saturated by
    the piece backing).
  • A shell script `phase_run.sh` invoking `imagephase` with all flags.
  • A `phases.json` capturing schedule provenance.

Per-filament phase count is determined by HueForge's `slider_values` from
the HFP file. Each non-canvas filament produces N phases whose colors are
interpolated from the previous filament's saturated color to the current
filament's saturated color. Two interpolation modes are available via
`--interpolation-mode`:

  1. `scaled-bl` (default): scaled Beer-Lambert curve
     raw_i = 1 - 10^(-i * layer_height / TD)
     fraction = raw_i / raw_N              # scaled so top = 1.0
     Early layers gain color faster (steep at start, flat approaching
     saturation) — preserves BL's exponential shape.

  2. `linear`: linear RGB interpolation
     fraction = i / N
     Equal-spaced fractions across the range.

Both modes produce identical endpoints (fraction=0 at canvas, fraction=1
at saturated filament color). They differ in intermediate-layer curve
shape. Artist visual A/B (2026-07-02) preferred `scaled-bl` for slightly
tighter alignment with HF preview mid-tones; `linear` remains available
as an alternate for future comparison work.

For horses sepia HFP (slider_values [0.84, 0.36, 0.16] → reversed to print
order [0.16, 0.36, 0.84], deltas [0.16, 0.20, 0.48]mm):
    - Black canvas: 0.16mm (skipped, canvas absorbs)
    - Flesh:        5 phases, black → pure flesh (#4E3524)
    - Bone white:   12 phases, pure flesh → pure bone-white (#E5DCC8)
    - Total: exactly 17 phases spanning full tonal range

**Why interpolation instead of strict Beer-Lambert:** strict Beer-Lambert
at HFP-supplied thicknesses produces a palette that bottoms out at "warm
medium-dark" (~#3E3730 for horses sepia) because 12 layers of TD-4.8
bone-white over dark sepia is only 20.6% opaque. HF's own preview renders
bright pixels near-pure bone-white (~#E3D7C4). Both interpolation modes
trade Beer-Lambert end-magnitude accuracy for tonal-range match. These
are HEURISTICS, not HF-internals-accurate. See tools/CITATIONS.md.

**Why HFP-required (no physics-only fallback):** empirical HF data
(2026-07-01) disproved every physics-derivable stop criterion. HFP's
slider_values are treated as authoritative for layer counts.

The `opacity` field in each phase's provenance stores the interpolation
fraction, NOT a raw Beer-Lambert opacity.
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


INTERPOLATION_MODES = ('scaled-bl', 'linear')
DEFAULT_INTERPOLATION_MODE = 'scaled-bl'


def derive_phases(filaments, layer_height_mm, max_thicknesses_mm,
                  deltae_fn=deltaE_76,
                  interpolation_mode=DEFAULT_INTERPOLATION_MODE):
    """Walk filament stack bottom-up. Return a list of DerivedPhase.

    Path B' (2026-07-02): HFP-driven, saturated-endpoint linear
    interpolation.

    For each non-canvas filament, F7 iterates as many layers as HFP's
    slider_values assigned to it and produces N phases whose colors are
    the linear interpolation from the previous filament's TOP saturated
    color to the current filament's pure saturated color:

        phase_color = (i / N) * filament_pure + (1 - i / N) * under_color

    At layer i = N, phase_color exactly equals filament_pure (the top of
    that filament's stack is treated as fully saturated). At layer i = 1,
    phase_color is 1/N of the way from under_color to filament_pure.

    **This is a heuristic, not Beer-Lambert physics.** Strict Beer-Lambert
    at HFP-supplied thicknesses produces a palette that bottoms out at
    "warm medium-dark" (~#3E3730 for horses sepia) because 12 layers of
    TD-4.8 bone-white over dark sepia is only 20.6% opaque. But HF's own
    preview renders bright pixels near-pure bone-white (~#E3D7C4). Path B'
    trades Beer-Lambert accuracy for tonal-range match with HF's preview,
    treating each filament's HFP-assigned thickness as "reaches saturation
    at the top layer."

    Alternative heuristics considered but not chosen:
      - Scaled Beer-Lambert (scale opacity so opacity_at_N = 1.0): same
        endpoints, different curve shape. Held as a fallback if linear
        interpolation looks poor in visual A/B.
      - Reimplementing HF's per-pixel algorithm inside F7 (Path C):
        significant reinvention; explicitly out of scope.

    See tools/CITATIONS.md "Path B' — saturated-endpoint interpolation"
    section and art/imagelab-rendering-bugs.md#bug-4 for context.

    The bottom filament is assumed pre-saturated (the print's base layers
    stack to ~100% opacity thanks to the piece's backing), so its layers
    are skipped — the canvas starts at its pure hex.

    For horses sepia HFP (max thicknesses [0.16, 0.20, 0.48] mm), this
    produces EXACTLY 17 phases: 5 flesh interpolated black→pure-flesh,
    then 12 bone-white interpolated pure-flesh→pure-bone-white.
    """
    if len(filaments) < 1:
        raise ValueError("at least one filament required")
    if layer_height_mm <= 0:
        raise ValueError(f"layer_height must be > 0, got {layer_height_mm}")
    if max_thicknesses_mm is None:
        raise ValueError(
            "max_thicknesses_mm is required (Path B / HFP-driven). "
            "F7 no longer supports physics-only fallback; supply --hfp "
            "or explicit per-filament max thicknesses."
        )
    if len(max_thicknesses_mm) < len(filaments):
        raise ValueError(
            f"max_thicknesses_mm has {len(max_thicknesses_mm)} entries but "
            f"there are {len(filaments)} filaments; expected one per filament"
        )
    if interpolation_mode not in INTERPOLATION_MODES:
        raise ValueError(
            f"interpolation_mode must be one of {INTERPOLATION_MODES}, "
            f"got {interpolation_mode!r}"
        )

    canvas_rgb = filaments[0].rgb
    phases = []
    phase_counter = 0

    for fi, fil in enumerate(filaments):
        # Skip bottom filament — it IS the canvas.
        if fi == 0:
            continue

        # under_rgb: color at the TOP of the previous filament's stack
        # (treated as saturated pure color of the previous filament, per
        # Path B'). Stays fixed for this filament's whole sweep.
        under_rgb = canvas_rgb
        max_thickness = float(max_thicknesses_mm[fi])
        n_layers = int(round(max_thickness / layer_height_mm))

        # Safety net.
        if n_layers <= 0:
            continue
        if n_layers > 1000:
            n_layers = 1000

        # Precompute for scaled-bl mode: raw BL opacity at max thickness
        # is the denominator that scales each layer's opacity so the top
        # layer reaches fraction 1.0 (saturated).
        if interpolation_mode == 'scaled-bl':
            raw_opacity_max = layer_opacity(max_thickness, fil.td)

        for intra in range(1, n_layers + 1):
            thickness = intra * layer_height_mm

            if interpolation_mode == 'scaled-bl':
                raw_opacity_i = layer_opacity(thickness, fil.td)
                fraction = min(raw_opacity_i / raw_opacity_max, 1.0)
            else:  # 'linear'
                fraction = intra / n_layers

            layer_rgb = tuple(
                fraction * f + (1.0 - fraction) * u
                for f, u in zip(fil.rgb, under_rgb)
            )

            # For provenance: `opacity` field now stores the interpolation
            # fraction (equivalent to "assumed opacity" under Path B'),
            # NOT the raw Beer-Lambert opacity. See docstring for rationale.
            phase_counter += 1
            phases.append(DerivedPhase(
                phase_idx=phase_counter,
                filament_idx=fi,
                intra_filament_layer=intra,
                cumulative_thickness=thickness,
                opacity=fraction,
                rgb=layer_rgb,
                delta_e_to_prior=deltae_fn(layer_rgb, canvas_rgb),
            ))
            canvas_rgb = layer_rgb

        # Inter-filament transition: propagate the saturated top-of-stack
        # color (= filament_pure under Path B'). Bone white's iteration
        # will start with this as under_rgb.
        canvas_rgb = tuple(float(c) for c in fil.rgb)

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
    """Parse a HueForge .hfp JSON file.
    Returns (layer_height, filaments, max_thicknesses_mm).

    HFP `filament_set` JSON list order is reversed from print order:
    index 0 is the TOP filament; the last is the BOTTOM. We reverse to
    get print order (bottom-up).

    HFP `slider_values` array is aligned with filament_set (also top→bottom):
    each entry is the Z-height at which that filament STOPS being deposited
    (i.e., the top of its stack in the color-transition zone). We reverse
    alongside filament_set and compute per-filament max thickness as the
    Z-delta from the previous filament's top: `slider_values[i] - slider_values[i-1]`
    (bottom filament's max = slider_values[0]).

    For horses sepia (slider_values reversed to print order [0.16, 0.36, 0.84]):
      black:      0.16mm (canvas — F7 skips this iteration anyway)
      flesh:      0.36 - 0.16 = 0.20mm  (5 layers @ 0.04mm)
      bone white: 0.84 - 0.36 = 0.48mm  (12 layers @ 0.04mm)

    Total non-canvas layers: 17. F7 records each as one phase.
    """
    with open(hfp_path, 'r') as f:
        data = json.load(f)
    layer_height = float(data.get('layer_height') or 0.04)
    filament_set = data.get('filament_set') or []
    slider_values = data.get('slider_values') or []

    # Reverse both to print order (bottom → top).
    filaments = []
    print_order_sliders = list(reversed(slider_values))
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

    # Compute per-filament max thickness from slider_values (in print order).
    # max_thicknesses[fi] = slider_values[fi] - slider_values[fi-1] for fi >= 1;
    # bottom filament's max = slider_values[0].
    max_thicknesses = None
    if print_order_sliders and len(print_order_sliders) >= len(filaments):
        max_thicknesses = []
        for i in range(len(filaments)):
            if i == 0:
                max_thicknesses.append(float(print_order_sliders[0]))
            else:
                delta = float(print_order_sliders[i]) - float(print_order_sliders[i - 1])
                max_thicknesses.append(delta)

    return layer_height, filaments, max_thicknesses


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
        '--hfp', default=None,
        help="Required. HueForge .hfp file. Reads filament_set, "
             "layer_height, and slider_values. Path B (2026-07-02): F7 "
             "iterates each filament to the exact thickness HF assigned "
             "and records EVERY layer as a phase, producing a per-layer "
             "palette that matches HF's Beer-Lambert prediction.",
    )
    parser.add_argument(
        '--layer-height', type=float, default=None,
        help="Layer height in mm (e.g. 0.04). Read from --hfp if omitted; "
             "supplying this flag overrides the HFP value.",
    )
    parser.add_argument(
        '--filament', action='append', dest='filaments',
        type=_parse_filament_arg, default=[],
        help="Repeatable; one per filament in print order (bottom→top). "
             "Format: '<hex>:<TD>'. Read from --hfp if omitted; supplying "
             "these flags overrides the HFP filament_set.",
    )
    parser.add_argument(
        '--out-dir', default=DEFAULT_OUT_DIR,
        help=f"Output directory (default: {DEFAULT_OUT_DIR})",
    )
    parser.add_argument(
        '--interpolation-mode', default=DEFAULT_INTERPOLATION_MODE,
        choices=INTERPOLATION_MODES,
        help=f"How phase colors are interpolated between the previous "
             f"filament's saturated color and the current filament's "
             f"saturated color. 'scaled-bl' (default) uses a scaled "
             f"Beer-Lambert curve — steep gain at first, flat approaching "
             f"saturation. 'linear' uses equal-spaced RGB fractions. Both "
             f"produce identical endpoints (canvas → saturated filament); "
             f"they differ in intermediate-layer curve shape. Artist visual "
             f"A/B 2026-07-02 preferred scaled-bl for horses sepia.",
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
                      max_thicknesses_mm, radius_schedule=None):
    """Write phases.json with full provenance for reproducibility.

    Schema v2:
      - `radius_schedule` block appears when F8 derivation is active.
      - `max_thicknesses_mm` block records the HFP-derived per-filament
        thickness cap that governed iteration (Path B).
    """
    doc = {
        'version': 2,
        'layer_height_mm': layer_height,
        'deltae_formula': 'CIE-1976',
        'max_thicknesses_mm': list(max_thicknesses_mm),
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

    # Path B (2026-07-02): F7 requires --hfp for HueForge-aligned output.
    # See tools/CITATIONS.md and art/imagelab-rendering-bugs.md#bug-4.
    if not args.hfp:
        print(
            "error: F7 requires --hfp for HueForge-aligned output.\n"
            "       Provide a HueForge .hfp file; F7 reads slider_values to\n"
            "       iterate each filament to the exact thickness HF assigned,\n"
            "       producing a per-layer palette that matches HF's Beer-Lambert\n"
            "       prediction. Non-HFP invocation is not currently supported.\n"
            "       See tools/CITATIONS.md and art/imagelab-rendering-bugs.md#bug-4.",
            file=sys.stderr,
        )
        sys.exit(1)

    args._hfp_max_thicknesses = None
    try:
        hfp_layer_h, hfp_filaments, hfp_max_thicknesses = (
            _load_filaments_from_hfp(args.hfp)
        )
    except (FileNotFoundError, ValueError, json.JSONDecodeError) as exc:
        print(f"error: failed to read --hfp {args.hfp!r}: {exc}",
              file=sys.stderr)
        sys.exit(1)
    args._hfp_max_thicknesses = hfp_max_thicknesses

    if hfp_max_thicknesses is None:
        print(
            f"error: --hfp {args.hfp!r} is missing 'slider_values' or has "
            f"fewer entries than filaments. Path B requires HF's slider "
            f"values to determine per-filament layer counts.",
            file=sys.stderr,
        )
        sys.exit(1)

    if args.layer_height is None:
        args.layer_height = hfp_layer_h
    if not args.filaments:
        args.filaments = hfp_filaments

    if not os.path.exists(args.target):
        print(f"error: --target {args.target!r} does not exist",
              file=sys.stderr)
        sys.exit(1)

    # Derive phases (HFP-driven, requires max_thicknesses_mm).
    try:
        phases = derive_phases(
            args.filaments, args.layer_height,
            max_thicknesses_mm=args._hfp_max_thicknesses,
            interpolation_mode=args.interpolation_mode,
        )
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        sys.exit(1)

    if not phases:
        print(
            "warning: derived 0 phases. Under Path B (HFP-driven), this "
            "means the HFP had only a bottom filament with no non-canvas "
            "layers, or per-filament max thicknesses are all zero. "
            "Check --hfp file's filament_set and slider_values.",
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
        args._hfp_max_thicknesses,
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
        f"(HFP-driven, max thicknesses per filament: "
        f"{[round(t, 3) for t in args._hfp_max_thicknesses]}mm)",
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
