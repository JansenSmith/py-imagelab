"""Regression sentinel for the white-stencil leak in CanvasActionDrawShape.run().

Pre-fix history: `canvas.py:267-272` stamped the polygon stencil in pure
white (255,255,255) before blitting the brush. When the brush was rotated
+ scaled, it didn't always fully cover the white stencil, so white pixels
of the stencil leaked through at the polygon's edges. The bug was masked
by the original code's unconditional `pygame.BLEND_MIN` (min(white, brush)
= brush per channel), so it was only visible when F1's
`--brush-blend-mode opaque` skipped BLEND_MIN.

Fix: the polygon now defines an alpha mask (no visible color) on an
SRCALPHA shape_surface; the brush supplies all visible color; BLEND_RGBA_MULT
clips brush to polygon shape. The stencil contributes zero color → no leak
possible at any blend mode.

This test exercises the exact conditions that previously produced visible
white speckles (opaque blend mode, full alpha, rotated brush, dark canvas)
and asserts that NO pure-white pixel appears on the output canvas.
"""
import subprocess
import shutil

from PIL import Image
import pytest


@pytest.fixture
def imagemutate_cmd():
    path = shutil.which("imagemutate")
    if path is None:
        pytest.skip("imagemutate not on PATH; install with `pip install -e .`")
    return [path]


@pytest.fixture
def solid_canvas(tmp_path):
    """Factory: solid_canvas(size, '#RRGGBB') -> path to a solid-color PNG."""
    counter = {"n": 0}

    def _factory(size, hex_color):
        counter["n"] += 1
        out = tmp_path / f"canvas_{counter['n']}_{hex_color.lstrip('#')}.png"
        Image.new("RGB", size, hex_color).save(out, "PNG")
        return out

    return _factory


def test_no_white_specks_in_opaque_brush_strokes(
    imagemutate_cmd, target_64x64, brush_solid, solid_canvas, tmp_path,
):
    """Render strokes against a black canvas with a non-white brush using
    --brush-alpha 255 --brush-blend-mode opaque -S triangle. Pre-fix:
    visible white specks scattered across the output. Post-fix: no
    (255,255,255) pixel anywhere on the canvas (the brush is dark red,
    the canvas starts black, neither contributes white).
    """
    canvas = solid_canvas((64, 64), "#000000")
    # Brush is a single dark red — should be the ONLY non-black color
    # appearing on the canvas. Any pure-white pixel indicates a stencil leak.
    brush = brush_solid("#4E0000", size=128)

    result = subprocess.run(
        imagemutate_cmd + [
            str(target_64x64),
            "--start-canvas", str(canvas),
            "-b", str(brush),
            "--brush-alpha", "255",
            "--brush-blend-mode", "opaque",
            "-S", "triangle",
            "--seed", "11", "-c", "10", "-j", "1", "-g", "80",
            "--save-on-exit", "--close-on-exit", "-p", "leak_sentinel",
            "-d", str(tmp_path),
        ],
        capture_output=True, text=True, timeout=60,
    )
    assert result.returncode == 0, result.stderr

    out_path = next(tmp_path.glob("leak_sentinel-*.png"))
    img = Image.open(out_path).convert("RGB")
    pixels = list(img.getdata())

    white_pixels = [p for p in pixels if p == (255, 255, 255)]
    assert white_pixels == [], (
        f"found {len(white_pixels)} pure-white pixels in output — "
        f"stencil-leak regression. Brush was dark red on black canvas; "
        f"no white pixel should appear anywhere."
    )


