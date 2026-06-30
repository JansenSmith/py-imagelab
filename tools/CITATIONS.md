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

## Opacity ceiling — 0.85

**Decision:** `0.85` matches Kromacut's documented "or opacity > 0.85, whichever comes first" stop condition for layer-by-layer color blending. No other research source consulted; this is the canonical reference for the same FDM use case.

## JND threshold — 2.3

**Decision:** `2.3` ΔE units, matches Kromacut. Common Just-Noticeable-Difference for CIE 1976 in image-processing literature is in the 1.0–2.5 range depending on viewing conditions; Kromacut's 2.3 sits at the upper end (forgiving — fewer false-distinct calls) and works in production. Adopting their choice means filament-painting decisions translate between tools.
