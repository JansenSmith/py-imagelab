# tests

pytest scaffolding for py-imagelab. Captures upstream's current behavior as a
regression baseline; subsequent features can verify they don't accidentally
drift the default output.

## Running locally

The pygame paths used by imagemutate currently require an active display
server. Two options:

```bash
# 1. If you're at a workstation with a real display:
pytest -q

# 2. Headless (CI-style) — requires `xvfb` installed on the system:
xvfb-run -a pytest -q
```

CI uses option 2 (Ubuntu runner has xvfb available via `apt-get install -y xvfb`).

## Fixtures

| Fixture | Description |
|---|---|
| `target_64x64` | 64×64 deterministic radial-gradient sRGB PNG; used as the baseline run target |
| `target_3band` | 64×192 three-band PNG (black / red / blue) for future phase-mode tests |
| `red_corner_64` | 64×64 black PNG with a red corner square; used for start-canvas tests |
| `corrupt_png` | Truncated PNG byte stream — decoders must fail cleanly |
| `baseline_hash` | The expected SHA256 of the canonical baseline output |
| `brush_solid(hex)` | Factory producing a temp 64×64 solid-color brush PNG (uses `magick` from ImageMagick) |
| `imagemutate_cmd` | Locates the installed `imagemutate` entry point |

## Regenerating the baseline

Only do this when an upstream behavior change is **intentional**.

```bash
# 1. Run imagemutate with the same parameters used by the test
rm -rf /tmp/baseline_gen && mkdir -p /tmp/baseline_gen
imagemutate tests/fixtures/target_64.png \
  --seed 42 -g 100 -c 10 -j 1 \
  --save-on-exit --close-on-exit -p baseline -d /tmp/baseline_gen

# 2. Hash the output
sha256sum /tmp/baseline_gen/baseline-*.png

# 3. Write the new hash into tests/fixtures/baseline_hash.txt
# (no trailing whitespace except a single newline)

# 4. Document the intentional behavior change in the PR body
```
