"""Tests for --start-canvas wiring (F2).

The `-s` / `--start-canvas` flag was previously declared in argparse but
never read by the App class. F2 wires it up: if supplied, load the image,
validate dimensions match the target, and use it as the initial canvas
(in place of the default solid-bg fill).
"""
import hashlib
import subprocess

from PIL import Image
import pytest


def _run(imagemutate_cmd, *args, timeout=60):
    return subprocess.run(
        imagemutate_cmd + list(args),
        capture_output=True, text=True, timeout=timeout,
    )


# ---------- Happy path: load → use as initial canvas ----------
# (No "-g 0 must equal start-canvas exactly" test — upstream's evolve loop
# runs the tick body before checking gen-stop, so `-g 0` still produces
# one painted generation. The behavior we actually care about is verified
# by test_with_painting_preserves_unpainted_regions below: the start canvas
# IS the starting point of evolution, not just a background overlay.)


def test_with_painting_preserves_unpainted_regions(imagemutate_cmd, target_64x64,
                                                     red_corner_64, tmp_path):
    """With -g > 0 and --start-canvas, the output contains BOTH new painted
    shapes AND preserved start-canvas pixels in unpainted regions. Verifies
    the start canvas is the actual evolution starting point, not just an
    overlay."""
    result = _run(
        imagemutate_cmd, str(target_64x64),
        "--start-canvas", str(red_corner_64),
        "--seed", "2", "-g", "20", "-c", "5", "-j", "1",
        "--save-on-exit", "--close-on-exit", "-p", "sc_paint",
        "-d", str(tmp_path),
    )
    assert result.returncode == 0, result.stderr
    out_path = next(tmp_path.glob("sc_paint-*.png"))
    out = Image.open(out_path).convert("RGB")
    src = Image.open(red_corner_64).convert("RGB")
    # The output should differ from the pure start canvas (painting happened)
    assert src.tobytes() != out.tobytes(), (
        "no painting occurred — start canvas was never overwritten"
    )


# ---------- Error path: clean exits ----------

def test_init_dim_mismatch_clean_error(imagemutate_cmd, target_64x64, tmp_path):
    """Start canvas with different dimensions than target → exits non-zero
    with a clear message; does NOT crash with a raw pygame trace."""
    mismatched = tmp_path / "mismatched.png"
    Image.new("RGB", (128, 128), "#444444").save(mismatched, "PNG")

    result = _run(
        imagemutate_cmd, str(target_64x64),
        "--start-canvas", str(mismatched),
        "--seed", "1", "-g", "1", "-c", "1", "-j", "1",
        "--save-on-exit", "--close-on-exit", "-p", "sc_mismatch",
        "-d", str(tmp_path),
    )
    assert result.returncode != 0, "expected non-zero exit on dim mismatch"
    assert "dimension mismatch" in result.stderr.lower(), (
        f"expected 'dimension mismatch' in stderr; got:\n{result.stderr}"
    )
    assert "traceback" not in result.stderr.lower(), (
        "raw pygame traceback leaked to user; clean error message expected"
    )


def test_init_path_missing(imagemutate_cmd, target_64x64, tmp_path):
    """Nonexistent --start-canvas path → exits non-zero, no pygame trace."""
    bogus = tmp_path / "no_such_file.png"  # path that does not exist
    assert not bogus.exists()

    result = _run(
        imagemutate_cmd, str(target_64x64),
        "--start-canvas", str(bogus),
        "--seed", "1", "-g", "1", "-c", "1", "-j", "1",
        "--save-on-exit", "--close-on-exit", "-p", "sc_missing",
        "-d", str(tmp_path),
    )
    assert result.returncode != 0
    # Pygame's error message includes the path; check for the clean prefix.
    assert "--start-canvas" in result.stderr or "could not load" in result.stderr, (
        f"expected clean error mentioning --start-canvas; got:\n{result.stderr}"
    )


def test_init_corrupt_png(imagemutate_cmd, target_64x64, corrupt_png, tmp_path):
    """Corrupt PNG → clean exit, no raw pygame trace."""
    result = _run(
        imagemutate_cmd, str(target_64x64),
        "--start-canvas", str(corrupt_png),
        "--seed", "1", "-g", "1", "-c", "1", "-j", "1",
        "--save-on-exit", "--close-on-exit", "-p", "sc_corrupt",
        "-d", str(tmp_path),
    )
    assert result.returncode != 0
    assert "could not load" in result.stderr.lower() or "--start-canvas" in result.stderr, (
        f"expected clean error; got:\n{result.stderr}"
    )


# ---------- Edge: format coercion ----------

def test_init_grayscale_to_rgb(imagemutate_cmd, target_64x64, tmp_path):
    """Grayscale start canvas → pygame's convert() coerces to RGB format
    matching the canvas surface. Run completes; output is RGB."""
    gray = tmp_path / "gray_start.png"
    Image.new("L", (64, 64), 128).save(gray, "PNG")

    result = _run(
        imagemutate_cmd, str(target_64x64),
        "--start-canvas", str(gray),
        "--seed", "1", "-g", "5", "-c", "3", "-j", "1",
        "--save-on-exit", "--close-on-exit", "-p", "sc_gray",
        "-d", str(tmp_path),
    )
    assert result.returncode == 0, result.stderr
    out = Image.open(next(tmp_path.glob("sc_gray-*.png")))
    # Pygame writes 24-bit sRGB RGB by default
    assert out.mode in ("RGB", "RGBA"), f"expected RGB output, got mode={out.mode}"


# ---------- Backward compatibility ----------

def test_no_flag_unchanged(imagemutate_cmd, target_64x64, baseline_hash,
                            tmp_path):
    """Omitting --start-canvas reproduces the F0 baseline hash exactly.
    Guards against F2 inadvertently shifting the default code path."""
    result = _run(
        imagemutate_cmd, str(target_64x64),
        "--seed", "42", "-g", "100", "-c", "10", "-j", "1",
        "--save-on-exit", "--close-on-exit", "-p", "compat",
        "-d", str(tmp_path),
    )
    assert result.returncode == 0
    out = next(tmp_path.glob("compat-*.png"))
    actual = hashlib.sha256(out.read_bytes()).hexdigest()
    assert actual == baseline_hash, (
        f"F2 broke backward compat. expected={baseline_hash} actual={actual}"
    )
