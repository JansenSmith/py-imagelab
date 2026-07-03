# tools/CITATIONS.md

Bibliography for `tools/` calibration defaults. Future maintainers extend this rather than re-running the same searches.

## ΔE formula choice — CIE 1976 vs 2000

**Decision:** ship `filament_stack_to_phases` with CIE 1976 (Euclidean Lab distance) as the convergence metric, threshold `2.3`.

**Why:** matches Kromacut's production implementation. The plan considered CIE 2000 on theoretical grounds (more perceptually uniform in saturated hues) but research showed Kromacut uses 1976 and ships it at the same threshold, against the same FDM use case.

### Sources consulted

#### Kromacut — `nextBestColor.ts`

- **URL:** https://github.com/vycdev/Kromacut/blob/HEAD/src/lib/nextBestColor.ts
- **Accessed:** 2026-06-29
- **Relevant extract:**
  ```typescript
  function deltaELab(a: Lab, b: Lab): number {
      return Math.sqrt((a.L - b.L) ** 2 + (a.a - b.a) ** 2 + (a.b - b.b) ** 2);
  }
  ```
- **Relevance:** straight Euclidean Lab distance = CIE 1976. Used throughout Kromacut's color-matching and palette-reduction logic — the closest open-source comparator to the use case `filament_stack_to_phases` serves.

#### Kromacut — `color.ts` (rgb→Lab implementation)

- **URL:** https://github.com/vycdev/Kromacut/blob/HEAD/src/lib/color.ts
- **Accessed:** 2026-06-29
- **Relevant extract:** standard CIE 1976 Lab implementation with D65 illuminant, L in [0, 100], a/b in [-128, 128]. Cube-root piecewise as per CIE spec.
- **Relevance:** confirms threshold `2.3` is against standard Lab units (not opencv's [0, 255] uint8 packing). Our `rgb_to_lab` normalizes cv2's L by `/2.55` and shifts a/b by `-128` to match this convention.

#### Project notes — `hueforge_technical.md` (in artist's private project)

- **Accessed:** 2026-06-29
- **Summary:** HueForge applies Beer-Lambert directly in sRGB (not linear RGB) because filament color hex codes and TDs are calibrated end-to-end in sRGB. The non-physical part of the math cancels out empirically. We follow the same convention.

### What was NOT chosen

- **CIE 2000 (ciede2000):** ~50 LoC of additional arithmetic with non-uniformity correction terms (lightness/chroma/hue weighting). Better perceptual uniformity for saturated colors. Not used because:
  - Kromacut works fine without it (the strongest production-evidence signal).
  - The cost (5× per-comparison arithmetic) is trivial in absolute terms but the LoC adds review surface.
  - For the FDM filament colors actually in use (largely earth tones, blacks, whites, occasional saturated primaries), 1976's non-uniformity is not the dominant noise source — opacity-ceiling and JND-threshold choices dominate.

  Future work: if a colorful filament stack (e.g., bright red + bright green) shows phase-count anomalies vs HueForge's preview, swap to CIE 2000 via an opt-in `--deltae-formula 2000` flag.

## Opacity ceiling — 0.85 (LEGACY — no longer consumed by derive_phases)

**Original decision:** `0.85` matched Kromacut's documented "or opacity > 0.85, whichever comes first" stop condition for layer-by-layer color blending.

**Superseded 2026-07-02 by Path B (HFP-driven).** `derive_phases` no longer uses an opacity ceiling; iteration stops when the HFP-supplied per-filament thickness cap is reached. The constant is retained in the module for potential future fallback but is not currently consumed. See "Path B" section below.

## JND threshold — 2.3 (LEGACY — no longer consumed by derive_phases)

**Original decision:** `2.3` ΔE units, matches Kromacut. Common Just-Noticeable-Difference for CIE 1976 in image-processing literature is in the 1.0–2.5 range depending on viewing conditions; Kromacut's 2.3 sits at the upper end (forgiving — fewer false-distinct calls) and works in production. Adopting their choice means filament-painting decisions translate between tools.

**Superseded 2026-07-02 by Path B.** The old per-layer ΔE convergence check (which used this threshold) was buggy — fired after ~2 layers of flesh on horses sepia, killing the palette. Path B drops it entirely and records EVERY iterated layer, giving a per-layer HF-aligned palette.

## Path B' — saturated-endpoint linear interpolation (2026-07-02)

**Decision:** `derive_phases` interpolates each filament's phase colors linearly from the previous filament's saturated color to the current filament's saturated pure color, across the HFP-supplied layer count.

```
for layer i in 1..N:
    fraction = i / N
    phase_color = fraction * filament_pure + (1 - fraction) * under_color
```

At layer i = N, phase_color = filament_pure. At layer i = 1, phase_color is 1/N of the way from under toward pure.

For horses sepia (17 phases from HFP): 5 flesh phases interpolate black → pure flesh; 12 bone-white phases interpolate pure flesh → pure bone-white. Palette spans `#000000` through `#4E3524` (pure flesh) to `#E5DCC8` (pure bone-white) — matching HF's preview tonal range.

**Why this is a heuristic and not physics:**

Strict Beer-Lambert at HFP-supplied thicknesses produces a palette that bottoms out at ~#3E3730 for horses (12 layers of TD-4.8 bone-white over dark sepia is only 20.6% opaque). HF's own preview renders bright pixels at ~#E3D7C4 (near-pure bone-white). The ~70% brightness gap means Beer-Lambert-per-HFP-layer doesn't produce the palette HF's actual per-pixel algorithm renders.

We DO NOT know what HF uses internally. HF's per-pixel algorithm varies both stack HEIGHT and filament COMPOSITION per pixel — bright pixels probably get only bone-white deposited (no black or flesh underneath) and reach near-pure bone-white color; dark pixels get only black. The palette range of a HF-rendered print is the range across ALL per-pixel compositions.

Rather than reimplement HF's per-pixel logic (which would be Path C — significant reinvention, artist flagged as maybe-nonsense), Path B' assumes each filament's HFP-supplied thickness "reaches saturation" at the top layer. This is a MODELING SHORTCUT that captures the desired tonal endpoints without claiming physics accuracy.

**Alternative heuristics considered but NOT chosen:**

- **Scaled Beer-Lambert** (compute raw opacity, then scale so opacity_at_N = 1.0). Same endpoints, different intermediate curve (exponential rather than linear). Held as a fallback if visual A/B shows linear interpolation looks poor.
- **HF color-core screenshot sampling**: exact palette but messy (requires screenshot capture + color sampling). Deferred as `[lo/lo]` improvement if the linear heuristic proves inadequate for some future piece.
- **Path C (reimplement HF's per-pixel algorithm)**: multi-hour reinvention; explicitly out of scope.

Documented as a heuristic; not claimed as HF-internal-algorithm-accurate.

## Path B — HFP-driven iteration (2026-07-02)

**Decision:** `derive_phases` reads HueForge slider values from the `--hfp` file and iterates each filament to that exact thickness, recording every layer as a phase.

**Why physics-only calibration doesn't work:** Empirical HF data gathered 2026-07-01 disproves both "constant opacity per filament" and "constant per-layer ΔE" hypotheses. HF's "good enough" indicator fires at radically different opacities depending on background color:

| Case | Layers to HF indicator | Cumulative opacity |
|---|---|---|
| Wine red (TD 1.7) over saturated black | 4 | 19.5% |
| Wine red (TD 1.7) over saturated flesh (dark sepia) | 4 | 19.5% |
| Wine red (TD 1.7) over saturated bone white | 10 | 41.8% |
| Natural (TD ~21) over saturated black | 53 | 20.8% |
| Natural (TD ~21) over saturated bone white | 1 | 0.44% |
| Bone white (TD 4.8) over saturated black | 12 | 20.6% |
| Bone white (TD 4.8) over saturated flesh | 12 | 20.6% |
| Flesh (TD 1.7) over saturated bone white | 11 | 44.9% |
| Flesh (TD 1.7) over saturated black | 5 | 23.7% |

Two regimes emerge: dark backgrounds cluster at ~20% opacity; bright backgrounds vary wildly (0.44% to 44.9%). HF's algorithm almost certainly involves target-image color per pixel, making it non-derivable from physics alone.

**Stack-order finding:** Adding bone white BELOW flesh didn't change wine red's iteration count over flesh (still 4 layers). Confirms that only the DIRECTLY-BELOW filament's top color matters, not deeper stack. F7's model already handles this correctly (`under_rgb = last color of previous filament's stack`).

**Path B mechanism:** F7 reads `slider_values` from the HFP JSON (aligned with `filament_set`, top→bottom), reverses to print order, and computes per-filament max thickness as the Z-delta from the previous filament's top. For horses sepia (slider_values [0.84, 0.36, 0.16] → reversed [0.16, 0.36, 0.84]):

- Black (bottom, canvas): max thickness 0.16mm (skipped)
- Flesh: 0.36 - 0.16 = 0.20mm → 5 layers at 0.04mm
- Bone White: 0.84 - 0.36 = 0.48mm → 12 layers

**Total phases: exactly 17** (5 flesh + 12 bone white), each colored via cumulative Beer-Lambert with the actual top-of-previous-filament as the under_rgb.

**Non-HFP invocation:** No fallback engineered. F7 errors out with a clear message directing the user to provide `--hfp`. Fallback design deferred as improvement; typical artist workflow uses HFP so this doesn't affect real usage.

**Sources consulted for the HF empirical data:**

- Artist HF observations 2026-07-01 and 2026-07-02 on horses sepia HFP + inserted test filaments (wine red PolyLite TD 1.7; natural PolyLite TD ~21 pending measurement).
- `art/hueforge_technical.md` (Beer-Lambert formula, TD convention).
- Cross-verified against Kromacut source (`src/lib/nextBestColor.ts`) for ΔE formula consistency.

## Tower safety factor — 3.0 (F8)

**Decision:** ship `filament_stack_to_phases` with `--tower-safety-factor 3.0` (= 1.2mm minimum tower diameter on a 0.4mm nozzle) for the topmost-phase shape radius. Conservative range: 2.5–3.0.

**Why:** filament-painting "towers" are short (~1–2mm tall on 0.04mm layers in HRM mode), thermally stable (slow speed, AMS pauses), and adhere on top of a continuous painted base — not isolated on bare bed. So they're more forgiving than the generic "isolated functional pin" case (where 3mm is recommended). Sub-line-width features are silently dropped by the slicer (HueForge FAQ). 1.2mm sits at the intersection of the conservative-but-still-printable threshold and filament-painting-specific tolerances.

### Sources consulted

#### HueForge Wiki — FAQ

- **URL:** https://hueforge.wiki/index.php/FAQ
- **Accessed:** 2026-06-29
- **Relevant extract:** "Portions of the image that are generally thinner than your line width will be ignored by the slicer and therefore will have one or two fewer layers than what HueForge expects due to these spikes."
- **Relevance:** Establishes the absolute slicer floor — features < line width (~0.42mm Bambu default) are silently dropped. Sets safety_factor lower bound at ~1.0× nozzle.

#### Kromacut — `dither line width` default

- **URL:** https://github.com/vycdev/Kromacut
- **Accessed:** 2026-06-29
- **Relevant extract:** "Dither line width ... Controls the minimum dot size for the dither pattern in mm. This should roughly match your printer's line/nozzle width so dither dots are actually printable. **Default: 0.42 mm**."
- **Relevance:** Sibling tool's empirical default for minimum printable dot on a 0.4mm nozzle = line width (factor ≈ 1.05). This is the slicer-resolved floor, not a safe tower minimum.

#### Voxel Magic — Minimum Requirements for PLA / PETG / ABS

- **URL:** https://voxel-magic.com/minimum-requirements-for-making-your-design-3d-printable-in-pla-petg-and-abs
- **Accessed:** 2026-06-29
- **Relevant extract:** "For pins/tabs with a 0.4 mm nozzle, the thickness should be ≥ 1.2 mm with a length ≥ 2.0 mm ... minimum vertical-wire diameter of 1.2 mm is recommended to ensure stability."
- **Relevance:** Direct empirical recommendation: vertical pin min = 1.2mm on a 0.4mm nozzle. Factor = 3.0. Primary anchor for the chosen default.

#### Pollen AM — Minimum Feature Size

- **URL:** https://www.pollen.am/design_for_3d_printing_minimum_feature_size/
- **Accessed:** 2026-06-29
- **Relevant extract:** "The minimum reliable pin diameter is 1mm. However, a pin can be designed to 0.8mm, but even then, risk breaking."
- **Relevance:** Lower-bound corroboration — 1.0mm is the edge of reliability; 0.8mm is risky. Factor 2.5 is the lowest defensible.

#### Protolabs / Hubs — FDM design guide

- **URL:** https://www.hubs.com/knowledge-base/how-design-parts-fdm-3d-printing/
- **Accessed:** 2026-06-29
- **Relevant extract:** "Vertical pins with a diameter under 3 mm will probably deform when printed ... Pins should be at least 1 mm in diameter and kept as short as possible."
- **Relevance:** Conservative industrial guide (3mm). Applies to functional pins of meaningful height, not 1–2mm filament-painting tower nubs. Justifies NOT picking factor 7.5 — that's overkill for this use case.

#### Mandarin3D — Wall Thickness Guide

- **URL:** https://mandarin3d.com/blog/wall-thickness-guide-minimum-and-optimal-measurements
- **Accessed:** 2026-06-29
- **Relevant extract:** "Wall thickness should be a multiple of your nozzle diameter, with a 0.4mm nozzle working best with walls that are 0.8mm, 1.2mm, 1.6mm ..."
- **Relevance:** 1.2mm = 3-line wall, common slicer-friendly minimum for closed perimeters. Cross-validates factor 3.0 from a different angle (perimeter packing rather than pin stability).

#### Bambu Lab Forum — Default Line Width of 0.42

- **URL:** https://forum.bambulab.com/t/default-line-width-of-0-42/20400
- **Accessed:** 2026-06-29
- **Relevant extract:** "Default line width 0.42mm for 0.4mm nozzle."
- **Relevance:** Confirms actual extruded line width on the target machine. Establishes slicer-dropped-feature floor at ~0.42mm.

### Convergence assessment

**Convergent.** Three independent strands — HueForge slicer floor ~0.42mm, Kromacut default 0.42mm, structural pin minimum 1.0–1.2mm — all point to a defensible tower minimum in the 1.0–1.2mm band on a 0.4mm nozzle. **1.2mm (factor 3.0)** is the upper-safe end of the consumer/community consensus and matches the placeholder. NO empirical print-test calibration improvement entry needed (per plan §9 decision tree case 1: convergent research → use median; placeholder retained).

### What was NOT chosen

- **Factor 1.0–1.5 (0.4–0.6mm):** equals raw line width / slicer floor. Prints will *attempt* features but reliability drops sharply; doesn't account for short-tower wobble during AMS swap pauses.
- **Factor 2.0 (0.8mm):** Pollen AM and Voxel Magic both flag this as risky / breakage-prone. Too aggressive for a default.
- **Factor 5.0–7.5 (2.0–3.0mm):** Protolabs / Hubs industrial guideline. Aimed at functional pins with height >> diameter that must survive handling. Filament-painting "towers" are 1–2mm tall and supported by the painted base below; this would suppress legitimate detail.
- **Smaller nozzle (0.2mm) recommendations:** out of scope; the constraint is X1C + 0.4mm.

## Print max-dim — 242mm (F8)

**Decision:** ship `filament_stack_to_phases` with `--print-max-dim-mm 242.0` default. NOT a universal value — overridden per piece. Chosen as the artist's most common max-dim across their pieces (e.g., wolves 242×206, depose 229×201, harrington 242×161). Pieces outside this nominal scale (mechEng 250×141, encre_marquet ~200) need the explicit override flag.

No external sources consulted — this is a piece-set median, not a calibration. Documented here so future maintainers don't mistake it for a research-derived value.
