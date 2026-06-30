# py-imagelab

Evolves a target image by iteratively painting random shapes onto a canvas and keeping mutations that improve the match. Uses a genetic algorithm approach — each generation spawns N children, and the closest match to the target survives.

**How it works:** py-imagelab is a blind evolutionary process — a million monkeys painting at random, kept honest by a simple rule: only improvements survive. Every shape placed is genuinely random; the algorithm has no knowledge of the target beyond a pixel-level score. The result is emergent, not directed.

If you'd rather produce great-looking output faster than simulate evolution faithfully, `--adaptive-cheat-mode` scales radius and children automatically as the image converges — smaller shapes, more attempts, better late-run results. It works. It just isn't pure.

## Setup

Requires Python 3.8+.

```bash
python -m venv env
source env/bin/activate       # Windows: env\Scripts\activate
pip install -e .
```

## Tools

### imagemutate

Evolve a canvas toward a target image or movie.

```bash
imagemutate <target_path> [options]
```

Common options:

| Flag | Description |
|------|-------------|
| `-g N` | Stop at generation N (default 1000) |
| `-c N` | Children per generation (default 25) |
| `-r N` | Max shape radius (default 40) |
| `-S <shape>` | Shape type: circle, polygon, triangle, etc. |
| `-W word1 word2` | Use words instead of shapes |
| `-b img1 img2` | Use images as brushes inside shapes |
| `-i` | Save drawing instructions (JSON) instead of image — includes seed, run parameters, and full history for replay |
| `-d <dir>` | Output directory |
| `-N` | No display (headless) |
| `-v` | Verbose output |
| `--seed N` | Fix the random seed for reproducible runs |
| `--compare-strategy` | `euclidean` (default) or `lab` (perceptually weighted) |
| `-j N` | Use N parallel worker processes for child generation (default: 1, serial); brief startup cost on first generation; most beneficial on longer runs; end-of-run stats include worker utilization. **Note:** the display may feel sluggish during each generation while workers compute — this is expected. |
| `--adaptive-cheat-mode` | Scale radius down and children up as match % improves — better art, less pure simulation |
| `--min-radius N` | Floor for adaptive radius (default: `max(radius // 8, 5)`) |
| `-p <prefix>` | Prefix output filenames (default: `img`) — useful for namespacing runs, e.g. `-p fox-lab` |
| `-o N` | Auto-save every N generations |
| `-t <template>` | Full filename template — supports `%PREFIX`, `%GENERATION`, `%CHILDREN`, `%FRAME` |
| `--save-on-exit` | Automatically save output when evolution completes |
| `--close-on-exit` | Close the display immediately when evolution completes (default: stay open) |

While running:

| Key | Action |
|-----|--------|
| `Esc` | Quit |
| `Space` | Print stats to console |
| `S` | Save image |
| `A` | Save instructions file (JSON) |
| `Enter` | Save (uses default output mode) |
| `H` | Toggle highlight overlay (shows current working region) |
| `I` | Toggle info overlay (gen / child / radius on screen) |
| `←` / `→` | Decrease / increase children count |
| `↑` / `↓` | Increase / decrease max radius |
| `Shift` + arrow | Adjust by 10 instead of 1 |

## Examples

```bash
# Basic run — circles, 1000 generations
imagemutate sample/images/fox-720x1080.jpg -d output -r 150 -c 100 -g 1000

# Parallel — 8 workers, best for longer runs
imagemutate sample/images/fox-720x1080.jpg -d output -r 150 -c 100 -g 1000 -j 8

# Perceptual color scoring
imagemutate sample/images/fox-720x1080.jpg -d output -r 150 -c 100 -g 1000 -j 8 --compare-strategy lab

# Triangles instead of circles
imagemutate sample/images/fox-720x1080.jpg -d output -r 150 -c 100 -g 1000 -j 8 -S triangle

# Words as shapes
imagemutate sample/images/fox-720x1080.jpg -d output -r 150 -c 100 -g 1000 -j 8 -W f o x

# Use another image as a brush texture inside shapes
imagemutate sample/images/fox-720x1080.jpg -d output -r 150 -c 100 -g 1000 -j 8 -b sample/images/oranges.jpg

# Reproducible run with fixed seed
imagemutate sample/images/fox-720x1080.jpg -d output -r 150 -c 100 -g 1000 --seed 42

# Save instructions for replay instead of image
imagemutate sample/images/fox-720x1080.jpg -d output -r 150 -c 100 -g 1000 -i

# Adaptive cheat mode — converges faster, looks better, less pure
imagemutate sample/images/fox-720x1080.jpg -d output -r 150 -c 100 -g 1000 -j 8 --adaptive-cheat-mode
```

### imagephase

A sibling app to `imagemutate` that paints in **phases** — one brush per phase, advance to next phase on plateau-then-switch. Different paradigm from imagemutate's uniform-random brush selection: instead of mixing all brushes throughout the run, each phase uses a single brush until the match percentage stops improving meaningfully, then moves on.

Useful for any workflow where painting proceeds in distinct color stages — for example, FDM 3D-printing simulation, where each phase corresponds to one printed layer's blended color, and the painted output approximates what the multi-color stack will physically render.

```bash
imagephase <target_path> --start-canvas <seed.png> --phases N --phase-brushes p1.png ... pN.png [options]
```

**Required flags:**

| Flag | Description |
|------|-------------|
| `--phases N` | Number of phases (>=1). |
| `--phase-brushes p1.png p2.png ... pN.png` | One brush per phase, space-separated. Parallel list of length `--phases`. |
| `-s` / `--start-canvas` | Seed image to start evolution from. Phase mode requires this — phase 0 paints over this canvas, not the default solid bg. |

**Phase-specific options:**

| Flag | Description |
|------|-------------|
| `--phase-max-radius R1 R2 ... Rn` | Per-phase starting (max) radius. Default 40 per phase. |
| `--phase-min-radius R1 R2 ... Rn` | Per-phase adaptive-shrink floor. Default 5 per phase. |
| `--phase-max-gens G1 G2 ... Gn` | EXPERIMENTAL per-phase safety cap. Use `inf` for no cap. Default `inf` per phase. |
| `--plateau-window K` | EXPERIMENTAL generations of history to consider for plateau check. Default 200. |
| `--plateau-delta D` | EXPERIMENTAL min match-% gain over the window to NOT count as plateau. Default 0.005. |
| `--phase-brushes-multi "g1 ; g2 ; g3"` | EXPERIMENTAL alternative to `--phase-brushes`. Semicolon-separated phase groups (multiple brushes per phase, picked uniform-random within a phase). Use case: textural variety within a color tier — e.g. three subtly-different oil-paint brushes for one stage, or natural-looking color drift via near-identical hexes. Mutually exclusive with `--phase-brushes`. |
| `-v` / `--verbose` | INFO logging: phase advances with reason, gens-in-phase, match-% at advance. |
| `-vv` / `--debug` | DEBUG logging: INFO + per-gen plateau buffer state, mutator-params hash. |

All `imagemutate` flags also apply (e.g., `-S triangle`, `-j 8`, `--adaptive-cheat-mode`, `--compare-strategy lab`), with one default difference: **imagephase defaults `--brush-alpha 255` and `--brush-blend-mode opaque`** (FDM-faithful, top-brush-wins composite). Both are overridable; non-default values emit a stderr warning since they defeat phase mode's intent.

**Plateau-then-switch:** the plateau detector advances a phase when both conditions hold over the rolling `plateau_window`:
- `history[-1] - history[0] < plateau_delta` (terminal-vs-initial gain too small)
- `max(history) - min(history) < 2 * plateau_delta` (variance too small to be progress)

The variance co-condition rules out windows that oscillate around a fixed mean. If `--phase-max-gens` is reached first, advance happens via the `max_gens` reason instead.

```bash
# Three-phase paint with synthetic targets and brushes
imagephase target.png \
  --start-canvas black-seed.png \
  --phases 3 \
  --phase-brushes phase1.png phase2.png phase3.png \
  --phase-max-radius 80 40 20 --phase-min-radius 8 6 4 \
  --plateau-window 50 --plateau-delta 0.005 \
  -S triangle -j 8 -c 50 \
  --save-on-exit --close-on-exit -p mypaint
```

**Sample stderr output at `-v`:**

```
imagephase: 3 phases, plateau_window=50, plateau_delta=0.005
  phase 1/3: brushes=[phase1.png] max_radius=80 min_radius=8 max_gens=inf
  phase 2/3: brushes=[phase2.png] max_radius=40 min_radius=6 max_gens=inf
  phase 3/3: brushes=[phase3.png] max_radius=20 min_radius=4 max_gens=inf
PHASE advance 1→2 reason=plateau gens=312 match-end=58.402%
PHASE advance 2→3 reason=plateau gens=487 match-end=78.115%
PHASE final reason=plateau gens=611 match-end=91.834%
```

**On-window HUD:** While running, imagephase adds a phase-status bar above imagemutate's existing HUD: `phase: N/M  ·  brush: <name>  ·  gens-in-phase: G  ·  plateau: <spread>/<threshold>` (yellow). Always visible while phase mode is active. The `plateau` column shows the current `max(history) - min(history)` over the rolling window vs the `2 * plateau_delta` threshold — when spread drops below threshold (and terminal-vs-initial gain drops below `plateau_delta`), the phase advances.

### imagereplay

Replay a saved instructions file (`.json` output from `imagemutate -i`).

```bash
imagereplay <input_path> [options]
```

While running: arrow keys scrub through history, `Enter` saves current frame.

### imagemerge

Blend multiple images together.

```bash
imagemerge <image1> <image2> ... [options]
imagemerge <directory> [options]
```

| Flag | Description |
|------|-------------|
| `-o <file>` | Output file (extension sets format: png, jpg, bmp) |
| `-N` | No display |
