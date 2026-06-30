# tools

Sibling tooling for `py-imagelab`. Each script here is a thin layer on top of one of the apps in `src/apps/` and is shipped via `pip install -e .` only when the file is present.

## filament_stack_to_phases

FDM filament-painting wrapper around `imagephase`. Takes a filament stack (layer height + ordered list of `<hex>:<TD>`) and produces:

- One brush PNG per derived phase (Beer-Lambert blend color at that depth).
- An init-canvas PNG matching the target image dimensions, painted in the bottom filament's pure hex (assumed saturated).
- `phase_run.sh` — invokes `imagephase` with all flags wired.
- `phases.json` — full provenance (per-phase hex, opacity, ΔE-to-prior, source filament index, intra-filament layer index, plus the input filament list and threshold values).

```bash
python tools/filament_stack_to_phases.py \
  --target inputs/horses.png \
  --layer-height 0.04 \
  --filament 000000:0.3 \
  --filament 4E3524:1.7 \
  --filament E5DCC8:4.8 \
  --out-dir ./horses_phases/
# then:
bash horses_phases/phase_run.sh
```

Or run everything in one shot:

```bash
python tools/filament_stack_to_phases.py \
  --target inputs/horses.png \
  --hfp /path/to/piece.hfp \
  --out-dir ./horses_phases/ \
  --run
```

### Flags

| Flag | Default | Meaning |
|---|---|---|
| `--target <path>` | required | Image to evolve against (passed to `imagephase` as the target). |
| `--layer-height <mm>` | required (or `--hfp`) | Per-layer height in mm (e.g., `0.04`). |
| `--filament <hex>:<TD>` (repeatable) | required (or `--hfp`) | Print order, bottom → top. Hex format `RRGGBB` or `#RRGGBB`. |
| `--hfp <path>` | optional | Convenience: parse a HueForge `.hfp` JSON to pre-fill `--layer-height` and `--filament`. Reverses `filament_set` to print order. Does NOT use `slider_values` — phase counts are always re-derived from Beer-Lambert here. |
| `--out-dir <path>` | `./phases_out/` | Where to write brushes, init_canvas, phase_run.sh, phases.json. |
| `--deltae-threshold <float>` | `2.3` | CIE 1976 ΔE Just-Noticeable-Difference (JND). Layers within this of the previous recorded phase don't get their own phase. |
| `--opacity-ceiling <float>` | `0.85` | Per-filament opacity at which iteration stops (asymptotic saturation). |
| `--include-no-op-layers` | OFF | Disable both the JND-skip and the convergence-stop. Yields the maximum possible phase count for the stack. Use for debugging the cascade or for very expressive renders. |
| `--run` | OFF | After generation, invoke `bash phase_run.sh` automatically. Without this, the script just generates and exits — the artist runs the shell script when ready. |

### How phases get derived

Phase derivation walks the filament stack bottom-up:

1. Start with the bottom filament's pure color as `canvas`.
2. For each filament above the bottom: iterate layer-by-layer applying Beer-Lambert (`T = 10^(-thickness/TD)`, `opacity = 1 - T`, `C_out = C_filament * opacity + C_under * T`).
3. Record a phase whenever the new layer's color is ≥ `deltae_threshold` ΔE from the last recorded color (perceptually distinguishable from the prior phase).
4. Stop iterating this filament when ANY of:
   - The layer's color is within `deltae_threshold` of the previous layer's color (perceptual convergence — adding more thickness won't change appearance).
   - Cumulative opacity reaches `opacity_ceiling` (asymptotic saturation).
5. Move to the next filament, building on the LAST recorded phase color.

### ΔE choice — what got picked and why

Default: **CIE 1976** (Euclidean Lab distance). This matches Kromacut's `deltaELab` (see `CITATIONS.md`) at the same `2.3` "just noticeable difference" threshold. Kromacut is the closest open-source comparator we have; matching their algorithm + threshold means filament-painting choices map directly between the two tools.

CIE 2000 (`ciede2000`) is more perceptually uniform but adds ~50 LoC of arithmetic for unclear benefit at this threshold. Deferred to a future PR if a use case surfaces — likely a stack with highly saturated complementary colors where 1976's hue non-uniformity matters.

### Limitations + caveats

- **Beer-Lambert in sRGB.** Math is applied directly in sRGB rather than linear RGB — matches HueForge's and Kromacut's convention. Slightly less physically accurate than linear-space blending, but the calibration of filament colors + TDs is end-to-end sRGB, so the round-trip works out.
- **Bottom filament is assumed saturated.** The print's base layers stack to ~100% opacity, so this is safe for any realistic filament-painting configuration. If you want to record bottom-filament layers explicitly, use `--include-no-op-layers`.
- **`--phase-max-radius` / `--phase-min-radius` not yet derived.** The generated `phase_run.sh` doesn't specify per-phase radii; `imagephase`'s defaults apply. F8 (planned) adds nozzle-derived radius math: smaller features for top-of-stack phases that print as isolated towers.
- **Per-piece HFP slider schedules are ignored.** Even with `--hfp`, the artist's `slider_values` choice is intentionally not used — F7's contract is "derive from physics, not from the artist's HF choices." For piece-specific schedule overrides, edit `phase_run.sh` directly.

### Sample output (horses sepia)

```
filament_stack_to_phases: 3 phases derived from 3 filaments at layer_height=0.04mm
  phase  1: #080504  (filament 2 4E3524, layer 2, opacity=0.103, ΔE-to-prior=2.53)
  phase  2: #0C0805  (filament 2 4E3524, layer 3, opacity=0.150, ΔE-to-prior=2.53)
  phase  3: #14100D  (filament 3 E5DCC8, layer 2, opacity=0.038, ΔE-to-prior=2.80)
```

3 phases on a sepia stack (black / dark-brown / off-white) — perceptually similar colors converge quickly. For a stack of bright primaries, expect 6-15 phases.
