"""Shared pytest fixtures for py-imagelab tests."""
import pathlib
import shutil
import subprocess

import pytest


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
def baseline_hash():
    """The expected SHA256 of the canonical baseline imagemutate run output.

    Generated against the source tree at the F0 PR's base commit (upstream sha
    cited in PR body). Re-generate ONLY when an intentional upstream behavior
    change lands. See tests/README.md for the regeneration command.
    """
    return _fixture_path("baseline_hash.txt").read_text().strip()


@pytest.fixture
def brush_solid(tmp_path):
    """Factory: brush_solid('#FF0000') -> path to a 64x64 solid-color PNG.

    Brushes are written to a per-test tmp_path so they're cleaned up
    automatically. Returns pathlib.Path. Requires `magick` (ImageMagick) on PATH.
    """
    counter = {"n": 0}

    def _factory(hex_color):
        counter["n"] += 1
        out = tmp_path / f"brush_{counter['n']}_{hex_color.lstrip('#')}.png"
        subprocess.run(
            ["magick", "-size", "64x64", f"xc:{hex_color}", str(out)],
            check=True,
        )
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
