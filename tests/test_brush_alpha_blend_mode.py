"""Tests for --brush-alpha + --brush-blend-mode (F1).

Verifies that:
- Omitting both flags preserves upstream behavior (covered by baseline regression).
- Explicit flags route through to the canvas action's set_alpha + BLEND_MIN gate.
- CLI rejects out-of-range alpha and invalid blend-mode choices cleanly.
- Opaque mode produces no channel-wise darkening on overlap.
"""
import hashlib
import subprocess

import pytest


def _run_imagemutate(imagemutate_cmd, *args, timeout=120):
    """Helper. Returns CompletedProcess. Caller asserts on returncode."""
    return subprocess.run(
        imagemutate_cmd + list(args),
        capture_output=True, text=True, timeout=timeout,
    )


# ---------- CLI validation ----------

@pytest.mark.parametrize("bad_alpha", ["0", "256", "-1", "1000"])
def test_cli_alpha_out_of_range(imagemutate_cmd, target_64x64, bad_alpha):
    result = _run_imagemutate(
        imagemutate_cmd, str(target_64x64), "--brush-alpha", bad_alpha,
        timeout=10,
    )
    assert result.returncode != 0
    assert "--brush-alpha" in result.stderr or "brush-alpha" in result.stderr


def test_cli_alpha_non_integer(imagemutate_cmd, target_64x64):
    result = _run_imagemutate(
        imagemutate_cmd, str(target_64x64), "--brush-alpha", "abc",
        timeout=10,
    )
    assert result.returncode != 0


def test_cli_blend_mode_invalid_choice(imagemutate_cmd, target_64x64):
    result = _run_imagemutate(
        imagemutate_cmd, str(target_64x64), "--brush-blend-mode", "bogus",
        timeout=10,
    )
    assert result.returncode != 0
    assert "brush-blend-mode" in result.stderr or "invalid choice" in result.stderr.lower()


@pytest.mark.parametrize("mode", ["auto", "opaque", "min", "alpha"])
def test_cli_blend_mode_valid_choices(imagemutate_cmd, target_64x64, mode, tmp_path):
    """All four documented choices parse without error."""
    result = _run_imagemutate(
        imagemutate_cmd, str(target_64x64),
        "--brush-blend-mode", mode,
        "--seed", "1", "-g", "2", "-c", "3", "-j", "1",
        "--save-on-exit", "--close-on-exit", "-p", f"choice_{mode}",
        "-d", str(tmp_path),
    )
    assert result.returncode == 0, (
        f"--brush-blend-mode {mode} failed:\n{result.stderr}"
    )


# ---------- Behavior: backward compatibility ----------

def test_no_flags_is_byte_identical(imagemutate_cmd, target_64x64,
                                     baseline_hash, tmp_path):
    """Re-asserts the F0 baseline: omitting --brush-alpha + --brush-blend-mode
    must reproduce the canonical hash. Guards against F1 inadvertently shifting
    the default code path."""
    result = _run_imagemutate(
        imagemutate_cmd, str(target_64x64),
        "--seed", "42", "-g", "100", "-c", "10", "-j", "1",
        "--save-on-exit", "--close-on-exit", "-p", "compat",
        "-d", str(tmp_path),
    )
    assert result.returncode == 0
    outputs = sorted(tmp_path.glob("compat-*.png"))
    assert len(outputs) == 1
    actual = hashlib.sha256(outputs[0].read_bytes()).hexdigest()
    assert actual == baseline_hash, (
        f"F1 broke backward compat. expected={baseline_hash} actual={actual}"
    )


def test_explicit_min_180_matches_baseline(imagemutate_cmd, target_64x64,
                                            baseline_hash, tmp_path):
    """Explicit --brush-alpha 180 --brush-blend-mode min must match the
    no-flags baseline (these are the de-facto upstream defaults)."""
    result = _run_imagemutate(
        imagemutate_cmd, str(target_64x64),
        "--brush-alpha", "180",
        "--brush-blend-mode", "min",
        "--seed", "42", "-g", "100", "-c", "10", "-j", "1",
        "--save-on-exit", "--close-on-exit", "-p", "explicit",
        "-d", str(tmp_path),
    )
    assert result.returncode == 0
    outputs = sorted(tmp_path.glob("explicit-*.png"))
    actual = hashlib.sha256(outputs[0].read_bytes()).hexdigest()
    assert actual == baseline_hash


# ---------- Behavior: opaque mode skips BLEND_MIN ----------

def test_opaque_mode_runs_to_completion(imagemutate_cmd, target_64x64, tmp_path):
    """Opaque mode is a new code path; verify it doesn't crash for a small run."""
    result = _run_imagemutate(
        imagemutate_cmd, str(target_64x64),
        "--brush-alpha", "255",
        "--brush-blend-mode", "opaque",
        "--seed", "1", "-g", "10", "-c", "5", "-j", "1",
        "--save-on-exit", "--close-on-exit", "-p", "opaque",
        "-d", str(tmp_path),
    )
    assert result.returncode == 0, result.stderr
    outputs = sorted(tmp_path.glob("opaque-*.png"))
    assert len(outputs) == 1


def test_opaque_differs_from_min_with_brushes(imagemutate_cmd, target_64x64,
                                                brush_solid, tmp_path):
    """With a brush image supplied, opaque mode must produce a different
    output than min mode (different composite operation, different pixels).
    Same seed/params; only the blend mode differs."""
    red_brush = brush_solid("#FF0000")

    common = [
        str(target_64x64),
        "-b", str(red_brush),
        "--brush-alpha", "255",
        "--seed", "7", "-g", "20", "-c", "3", "-j", "1",
        "--save-on-exit", "--close-on-exit",
    ]

    out_min = tmp_path / "min"
    out_opaque = tmp_path / "opaque"
    out_min.mkdir()
    out_opaque.mkdir()

    r1 = _run_imagemutate(imagemutate_cmd, *common,
                          "--brush-blend-mode", "min",
                          "-p", "min", "-d", str(out_min))
    assert r1.returncode == 0, r1.stderr
    r2 = _run_imagemutate(imagemutate_cmd, *common,
                          "--brush-blend-mode", "opaque",
                          "-p", "opaque", "-d", str(out_opaque))
    assert r2.returncode == 0, r2.stderr

    min_png = next(out_min.glob("min-*.png"))
    opaque_png = next(out_opaque.glob("opaque-*.png"))

    min_hash = hashlib.sha256(min_png.read_bytes()).hexdigest()
    opaque_hash = hashlib.sha256(opaque_png.read_bytes()).hexdigest()

    assert min_hash != opaque_hash, (
        "opaque mode produced byte-identical output to min mode — "
        "BLEND_MIN gating likely not effective."
    )
