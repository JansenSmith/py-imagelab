"""Regression test for the degenerate brush-sample-range bug in
src/imagelab/drawing.py.

Pre-fix: when a brush image's smaller dimension equals 2 * max-radius,
``rng.integers(radius*2, max_sample_size)`` is called with ``low == high``,
which numpy raises ``ValueError: low >= high`` on. Any imagemutate run that
supplied a brush at exactly canvas size (with the default radius) would
crash.

Post-fix: the degenerate range is detected and ``sample_size = radius*2``
is used directly (no sampling needed — the brush sample rect already
fits exactly).

This test is self-contained: it does not depend on tests/conftest.py or
any other fixture, so the PR can land on its own without F0's full test
scaffolding in place.
"""
import shutil
import subprocess

import pytest


def _make_solid_png(path, hex_color, size):
    subprocess.run(
        ["magick", "-size", f"{size}x{size}", f"xc:{hex_color}",
         "-define", "png:color-type=2", str(path)],
        check=True,
    )


def test_brush_sample_range_no_longer_crashes(tmp_path):
    """A 64x64 brush + 64x64 target + default --radius=40 (radius*2=80,
    max_sample_size=80, no sampling range) used to crash. Verify it now
    completes."""
    if shutil.which("imagemutate") is None:
        pytest.skip("imagemutate not on PATH; install with `pip install -e .`")
    if shutil.which("magick") is None:
        pytest.skip("ImageMagick `magick` not on PATH")

    target = tmp_path / "target.png"
    brush = tmp_path / "brush.png"
    _make_solid_png(target, "#888888", 64)
    _make_solid_png(brush, "#FF0000", 64)

    # Small run; the crash happens on the first child rendered when a brush
    # is selected, so 3 gens × 2 children is plenty to surface it.
    result = subprocess.run(
        ["imagemutate", str(target),
         "-b", str(brush),
         "--seed", "1", "-g", "3", "-c", "2", "-j", "1",
         "--save-on-exit", "--close-on-exit", "-p", "smr",
         "-d", str(tmp_path)],
        capture_output=True, text=True, timeout=60,
    )

    assert "low >= high" not in result.stderr, (
        "degenerate brush sample range still raises ValueError(low >= high):\n"
        + result.stderr
    )
    assert "high <= 0" not in result.stderr, (
        "degenerate clip-rect placement still raises ValueError(high <= 0):\n"
        + result.stderr
    )
    assert result.returncode == 0, (
        f"imagemutate exited {result.returncode}\n"
        f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    )


# ---------- Unit tests for the geometry.py guard ----------

def test_get_random_clip_rect_equal_dims_constrained():
    """When the clip dimension equals the rect dimension on an axis, the
    only valid position is 0. Pre-fix, rng.integers(0, 0) raised high<=0."""
    from imagelab.geometry import get_random_clip_rect
    import pygame
    pygame.init()
    try:
        rect = pygame.Rect(0, 0, 64, 64)
        result = get_random_clip_rect(rect, 64, 64, constrain=True)
        assert result == (0, 0, 64, 64)
    finally:
        pygame.quit()


def test_get_random_clip_rect_clip_larger_than_rect_constrained():
    """Clip dimension larger than rect dimension also has only position=0
    as valid. Pre-fix this crashed too."""
    from imagelab.geometry import get_random_clip_rect
    import pygame
    pygame.init()
    try:
        rect = pygame.Rect(0, 0, 64, 64)
        result = get_random_clip_rect(rect, 100, 100, constrain=True)
        # x_pos and y_pos pinned to 0; clip dims passed through.
        assert result == (0, 0, 100, 100)
    finally:
        pygame.quit()


def test_get_random_clip_rect_strictly_smaller_constrained_still_works():
    """Strictly-smaller clip retains the original randomized behavior."""
    from imagelab.geometry import get_random_clip_rect
    import pygame
    pygame.init()
    try:
        rect = pygame.Rect(0, 0, 64, 64)
        result = get_random_clip_rect(rect, 32, 32, constrain=True)
        x, y, w, h = result
        assert (w, h) == (32, 32)
        assert 0 <= x <= 32   # rect.width - clip_width = 32
        assert 0 <= y <= 32
    finally:
        pygame.quit()
