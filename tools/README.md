# tools

Sibling tooling for `py-imagelab`. Each script here is a thin layer on top of one of the apps in `src/apps/` and is shipped via `pip install -e .` only when the file is present.

## filament_stack_to_phases

FDM filament-painting wrapper around `imagephase`. Reads a HueForge `.hfp` file (required) and produces a per-layer palette that matches HF's per-layer Beer-Lambert prediction:

- One brush PNG per derived phase (each phase = one printed layer's blended color).
- An init-canvas PNG matching the target image dimensions, painted in the bottom filament's pure hex (assumed saturated by the piece's backing).
- `phase_run.sh` — invokes `imagephase` with all flags wired.
- `phases.json` — full provenance (per-phase hex, opacity, ΔE-to-prior, source filament index, intra-filament layer index, HFP-derived per-filament max thicknesses).

```bash
python tools/filament_stack_to_phases.py \
  --target inputs/horses.png \
  --hfp /path/to/horses.hfp \
  --out-dir ./horses_phases/
# then:
bash horses_phases/phase_run.sh

# or run everything in one shot:
python tools/filament_stack_to_phases.py \
  --target inputs/horses.png \
  --hfp /path/to/horses.hfp \
  --out-dir ./horses_phases/ \
  --run
```

### Flags

| Flag | Default | Meaning |
|---|---|---|
| `--target <path>` | required | Image to evolve against (passed to `imagephase` as the target). |
| `--hfp <path>` | required | HueForge `.hfp` file. F7 reads `filament_set`, `layer_height`, and `slider_values`. **Non-HFP invocation is not currently supported** — F7 errors out with a clear message. |
| `--layer-height <mm>` | (from HFP) | Overrides the HFP `layer_height` if supplied. |
| `--filament <hex>:<TD>` (repeatable) | (from HFP) | Overrides the HFP `filament_set` if supplied. |
| `--out-dir <path>` | `./phases_out/` | Where to write brushes, init_canvas, phase_run.sh, phases.json. |
| `--nozzle-mm <float>` | OFF | When supplied, activates the nozzle-derived radius schedule (F8). Generates per-phase `--phase-max-radius` + `--phase-min-radius` flags in `phase_run.sh`. |
| `--print-max-dim-mm <float>` | `242.0` | Physical max dimension of the printed piece in mm (longest side, orientation-independent). Default is the artist's most common max-dim. |
| `--tower-safety-factor <float>` | `3.0` | Multiplier on `--nozzle-mm` yielding the topmost-phase minimum reliable tower diameter. See `CITATIONS.md`. |
| `--run` | OFF | After generation, invoke `bash phase_run.sh` automatically. |

### How phases get derived (Path B' — HFP-driven, saturated-endpoint interpolation, 2026-07-02)

Phase derivation reads HF's `slider_values` from the HFP file to determine layer counts per filament, then produces phase colors by **linear interpolation from the previous filament's saturated color to the current filament's saturated pure color**:

```
phase_color = (i / N) * filament_pure + (1 - i / N) * under_color
```

At layer i = N, phase_color = filament_pure. At layer i = 1, phase_color is 1/N of the way from under toward pure. **Every layer becomes one phase.**

For horses sepia (HFP slider_values [0.84, 0.36, 0.16] → reversed to print order [0.16, 0.36, 0.84]):

1. Start with the bottom filament's pure color as `canvas` (the piece backing is assumed to fully saturate the bottom color).
2. Black canvas: max_thickness = 0.16mm (skipped; canvas absorbs it).
3. Flesh: max_thickness = 0.20mm → **5 phases** interpolating black → pure flesh (`#4E3524`). Phase 5 = pure flesh.
4. Bone White: max_thickness = 0.48mm → **12 phases** interpolating pure flesh → pure bone-white (`#E5DCC8`). Phase 17 = pure bone-white.
5. Total: **exactly 17 phases** spanning canvas color through saturated flesh to saturated bone-white — matches HF's preview tonal range.

### Why interpolation instead of Beer-Lambert

Strict Beer-Lambert at HFP-supplied thicknesses produces a palette that bottoms out at ~#3E3730 for horses (12 layers of TD-4.8 bone-white over dark sepia is only 20.6% opaque). HF's own preview renders bright pixels at ~#E3D7C4 (near-pure bone-white). The Beer-Lambert palette doesn't span the tonal range HF's actual per-pixel algorithm produces.

Path B' treats each filament's HFP-supplied thickness as "reaches saturation at the top layer" and interpolates the intermediate layers linearly. This is a HEURISTIC — not HF-internal-accurate, but produces the palette range imagephase needs to reproduce HF-preview-looking output.

We do NOT know what HF uses internally. HF's per-pixel algorithm varies both stack height and filament composition per pixel. See `CITATIONS.md` "Path B' — saturated-endpoint linear interpolation" section for the full rationale.

### Why HFP is required (background)

The 2026-07-01 empirical investigation into HF's "good enough" indicator (`art/imagelab-rendering-bugs.md#bug-4`) showed that HF's per-filament stop criterion involves target-image color per pixel — not derivable from physics alone.

Since HF has already computed the correct per-filament layer counts for a given target image and written them to the HFP file, the pragmatic answer is: **use HF's own answer**. F7 reads `slider_values` directly.

Non-HFP fallback deferred as an improvement; typical filament-painting workflow uses HFP so this doesn't affect real usage.

### ΔE choice — CIE 1976 (matches Kromacut)

Default: **CIE 1976** (Euclidean Lab distance). This matches Kromacut's `deltaELab` (see `CITATIONS.md`). Used only for the `delta_e_to_prior` field in `phases.json` provenance; not consumed for iteration control (Path B uses HFP thicknesses directly).

### Limitations + caveats

- **Beer-Lambert in sRGB.** Math is applied directly in sRGB rather than linear RGB — matches HueForge's and Kromacut's convention. Slightly less physically accurate than linear-space blending, but the calibration of filament colors + TDs is end-to-end sRGB, so the round-trip works out.
- **Bottom filament is assumed saturated.** The print's base layers stack to ~100% opacity via the piece backing, so this is safe for the artist's typical piece structure. If your piece has no backing and the bottom filament is thin, the visible bottom color may be lighter than F7 assumes.
- **HFP-required.** Non-HFP invocation errors out; there is no physics-only fallback in the current implementation.
- **Radius schedule only active with `--nozzle-mm`.** Without that flag, the generated `phase_run.sh` omits `--phase-max-radius` / `--phase-min-radius` and `imagephase`'s defaults apply (which may produce features smaller than the printer can resolve). Always supply `--nozzle-mm` for FDM use.

### Radius schedule (F8 — nozzle-derived)

When `--nozzle-mm` is supplied, per-phase max and min radii are derived from the print's physical constraints rather than guessed. Bottom phases get large strokes (broad color coverage); top phases get small strokes that stay above the printer's minimum reliable feature size.

```
pixels_per_mm    = image_max_dim_px / print_max_dim_mm
min_tower_mm     = nozzle_mm × tower_safety_factor
min_radius_top   = max(2, ceil(min_tower_mm × pixels_per_mm / 2))

max_radius_phase_0 = ceil(print_max_dim_mm × 0.12  × pixels_per_mm)   # ~12% of print max-dim
max_radius_phase_N = max(min_radius_top × 3,
                         ceil(print_max_dim_mm × 0.025 × pixels_per_mm))  # ~2.5%
# linear-interpolated across phases

min_radius_top    = (above)                    # topmost phase only
min_radius_other  = max(2, floor(min_radius_top × 0.5))
```

Worked example for horses (242×195mm print, 600×746px target, 0.4mm nozzle, safety 3.0):

- `pixels_per_mm = 746/242 ≈ 3.08`
- `min_tower_mm = 0.4 × 3.0 = 1.2mm`
- `min_radius_top = max(2, ceil(1.2 × 3.08 / 2)) = max(2, ceil(1.85)) = 2 px`
- `max_radius_phase_0 ≈ ceil(242 × 0.12 × 3.08) ≈ 90 px`
- `max_radius_phase_N ≈ max(6, ceil(242 × 0.025 × 3.08)) = max(6, 19) = 19 px`

If `pixels_per_mm` lands outside the 2.0–5.0 sweet spot (recommended ~3 px/mm), a stderr warning surfaces — too coarse means blocky features; too fine means GA convergence wastes compute with no fidelity gain.

**Tower-safety-factor 3.0 is research-derived**, not empirical for this exact rig. Per CITATIONS.md, three independent strands (HueForge slicer floor, Kromacut default, structural pin minimum guides) converge on 1.0–1.2mm minimum tower diameter for a 0.4mm nozzle, with 1.2mm (factor 3.0) at the upper-safe end of consumer/community consensus. If a future calibration print on the exact Bambu X1C + filament-painting rig produces a refined value, update the default + the citation.

### Sample output (horses sepia — Path B)

Running against the horses sepia HFP produces exactly 17 phases: 5 flesh layers on the black canvas, then 12 bone-white layers on top of the flesh stack. Colors trace HF's per-layer Beer-Lambert prediction end-to-end.

```
filament_stack_to_phases: 17 phases derived from 3 filaments at layer_height=0.04mm
  (HFP-driven, max thicknesses per filament: [0.16, 0.2, 0.48]mm)
  phase  1: #040302  (filament 2 4E3524, layer 1, opacity=0.053, ...)
  phase  2: #080504  (filament 2 4E3524, layer 2, opacity=0.103, ...)
  ...
  phase  5: #100B08  (filament 2 4E3524, layer 5, opacity=0.237, ...)
  phase  6: #171310  (filament 3 E5DCC8, layer 1, opacity=0.019, ...)
  ...
  phase 17: #4F4844  (filament 3 E5DCC8, layer 12, opacity=0.206, ...)
```

Radius columns appear when `--nozzle-mm` is supplied.
