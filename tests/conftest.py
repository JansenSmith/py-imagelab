"""Shared pytest fixtures for py-imagelab tests."""
import pathlib
import shutil

import pytest
from PIL import Image


FIXTURES_DIR = pathlib.Path(__file__).parent / "fixtures"


def _fixture_path(name):
    """Return absolute path to a fixture file."""
    p = FIXTURES_DIR / name
    if not p.exists():
        raise FileNotFoundError(f"fixture not found: {p}")
    return p


@pytest.fixture
def target_64x64():
    """64x64 deterministic radial-gradient sRGB PNG (1718B)."""
    return _fixture_path("target_64.png")


@pytest.fixture
def target_3band():
    """64x192 three-band sRGB PNG: top black / middle red / bottom blue."""
    return _fixture_path("target_3band_64x192.png")


@pytest.fixture
def red_corner_64():
    """64x64 black PNG with a 16x16 red square in the top-left corner.
    Used for init-canvas / start-canvas tests."""
    return _fixture_path("red_corner_64.png")


@pytest.fixture
def corrupt_png():
    """A truncated PNG byte stream. Decoders should fail cleanly."""
    return _fixture_path("corrupt.png")


@pytest.fixture
def horses_sepia_hfp():
    """Synthetic minimal HFP for horses sepia — contains only the four fields
    F7's `_load_filaments_from_hfp` reads (layer_height, filament_set,
    slider_values). No embedded image (real HFPs are ~30MB dominated by
    base64 image data); no copyright weight. Slider values match the artist's
    horses sepia HFP as of 2026-07-02.

    Filaments (top→bottom in HFP JSON convention):
      Bone White (TD 4.8), Flesh (TD 1.7), Black (TD 0.3)
    slider_values (top→bottom): [0.84, 0.36, 0.16]
    → Per-filament max thickness (print order, bottom→top):
      Black canvas: 0.16mm (skipped by derive_phases)
      Flesh: 0.20mm = 5 layers @ 0.04mm
      Bone White: 0.48mm = 12 layers @ 0.04mm
    → Total derived phases for this stack: exactly 17.
    """
    return _fixture_path("horses_sepia.hfp")


@pytest.fixture
def baseline_hash():
    """The expected SHA256 of the canonical baseline imagemutate run output.

    Generated against the source tree at the F0 PR's base commit (upstream sha
    cited in PR body). Re-generate ONLY when an intentional upstream behavior
    change lands. See tests/README.md for the regeneration command.
    """
    return _fixture_path("baseline_hash.txt").read_text().strip()


@pytest.fixture
def brush_solid(tmp_path):
    """Factory: brush_solid('#FF0000') -> path to a solid-color PNG.

    Default size is 128 (not 64) so the brush is strictly larger than the
    upstream brush-sample-rect logic in drawing.py expects; with size equal
    to canvas dimensions, the original code triggers ValueError in
    rng.integers(radius*2, max_sample_size). Tests that exercise the
    degenerate-size code path should pass a smaller `size` explicitly.

    Always 24-bit sRGB RGB (Pillow's "RGB" mode = png color-type 2).
    pygame.transform.smoothscale rejects indexed / paletted surfaces.

    Brushes are written to a per-test tmp_path so they're cleaned up
    automatically. Returns pathlib.Path.
    """
    counter = {"n": 0}

    def _factory(hex_color, size=128):
        counter["n"] += 1
        out = tmp_path / f"brush_{counter['n']}_{hex_color.lstrip('#')}.png"
        img = Image.new("RGB", (size, size), hex_color)
        img.save(out, "PNG")
        return out

    return _factory


@pytest.fixture
def imagemutate_cmd():
    """Locate the imagemutate entry point — installed by pip install -e .
    Returns the command list prefix suitable for subprocess.run().
    """
    path = shutil.which("imagemutate")
    if path is None:
        pytest.skip("imagemutate not on PATH; install with `pip install -e .`")
    return [path]
