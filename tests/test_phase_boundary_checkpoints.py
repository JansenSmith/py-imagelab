"""Tests for F5 — phase-boundary checkpoint saves.

When --save-gen (-o) is set AND phase mode is on, imagephase saves an
extra checkpoint at every phase advance regardless of the modulo schedule.
Boundary saves are named with a `-end-` marker; periodic saves use the
same pattern minus `-end-`. Both include the current phase number.

Filename pattern:
  periodic:  <prefix>-c<C>-p<P>-g<G>.png
  boundary:  <prefix>-c<C>-p<P>-end-g<G>.png
"""
import re
import shutil
import subprocess

from PIL import Image
import pytest


@pytest.fixture
def imagephase_cmd():
    path = shutil.which("imagephase")
    if path is None:
        pytest.skip("imagephase not on PATH; install with `pip install -e .`")
    return [path]


@pytest.fixture
def solid_canvas(tmp_path):
    counter = {"n": 0}

    def _factory(size, hex_color):
        counter["n"] += 1
        out = tmp_path / f"canvas_{counter['n']}_{hex_color.lstrip('#')}.png"
        Image.new("RGB", size, hex_color).save(out, "PNG")
        return out

    return _factory


def _run(cmd, *args, timeout=120):
    return subprocess.run(
        cmd + list(args),
        capture_output=True, text=True, timeout=timeout,
    )


def test_one_end_file_per_phase_when_save_gen_set(
    imagephase_cmd, target_3band, brush_solid, solid_canvas, tmp_path,
):
    """3-phase run with --save-gen 50 → exactly 3 *-end-g*.png files
    (one per phase advance / final completion). Periodic schedule fires
    independently."""
    canvas = solid_canvas((64, 192), "#000000")
    p1 = brush_solid("#000000", size=128)
    p2 = brush_solid("#FF0000", size=128)
    p3 = brush_solid("#0000FF", size=128)

    result = _run(
        imagephase_cmd, str(target_3band),
        "--start-canvas", str(canvas),
        "--phases", "3",
        "--phase-brushes", str(p1), str(p2), str(p3),
        "--phase-max-radius", "25", "20", "16",
        "--phase-min-radius", "4", "3", "2",
        # Tight caps to force quick advances so the test runs fast.
        "--phase-max-gens", "30", "30", "30",
        "--plateau-window", "10", "--plateau-delta", "0.001",
        "--seed", "11", "-c", "5", "-j", "1",
        "-g", "300",
        "--save-gen", "50",  # periodic schedule
        "--save-on-exit", "--close-on-exit", "-p", "ckpt",
        "-d", str(tmp_path),
    )
    assert result.returncode == 0, result.stderr

    end_files = sorted(tmp_path.glob("ckpt-*-end-*.png"))
    assert len(end_files) == 3, (
        f"expected exactly 3 phase-end checkpoints, got {len(end_files)}: "
        f"{[p.name for p in end_files]}"
    )
    # Each end-file's name includes a phase index (p01, p02, p03).
    pattern = re.compile(r"ckpt-c\d+-p(\d+)-end-g\d+\.png")
    phases_seen = []
    for f in end_files:
        m = pattern.match(f.name)
        assert m, f"unexpected end-file name: {f.name}"
        phases_seen.append(int(m.group(1)))
    assert phases_seen == [1, 2, 3], (
        f"end-files don't represent each phase in order: {phases_seen}"
    )


def test_no_end_files_without_save_gen(
    imagephase_cmd, target_3band, brush_solid, solid_canvas, tmp_path,
):
    """If --save-gen is NOT set, no boundary saves happen (the plan ties
    boundary saves to --save-gen activation)."""
    canvas = solid_canvas((64, 192), "#000000")
    p1 = brush_solid("#000000", size=128)
    p2 = brush_solid("#FF0000", size=128)

    result = _run(
        imagephase_cmd, str(target_3band),
        "--start-canvas", str(canvas),
        "--phases", "2",
        "--phase-brushes", str(p1), str(p2),
        "--phase-max-radius", "25", "20",
        "--phase-min-radius", "4", "3",
        "--phase-max-gens", "20", "20",
        "--plateau-window", "10", "--plateau-delta", "0.001",
        "--seed", "11", "-c", "5", "-j", "1",
        "-g", "200",
        # NO --save-gen
        "--save-on-exit", "--close-on-exit", "-p", "noend",
        "-d", str(tmp_path),
    )
    assert result.returncode == 0, result.stderr
    end_files = list(tmp_path.glob("noend-*-end-*.png"))
    assert len(end_files) == 0, (
        f"expected NO end-files without --save-gen, got: "
        f"{[p.name for p in end_files]}"
    )


def test_periodic_save_naming_includes_phase(
    imagephase_cmd, target_3band, brush_solid, solid_canvas, tmp_path,
):
    """Periodic saves (the existing -o schedule) include the phase in
    their filename: <prefix>-c<C>-p<P>-g<G>.png (no -end-)."""
    canvas = solid_canvas((64, 192), "#000000")
    p1 = brush_solid("#000000", size=128)
    p2 = brush_solid("#FF0000", size=128)

    result = _run(
        imagephase_cmd, str(target_3band),
        "--start-canvas", str(canvas),
        "--phases", "2",
        "--phase-brushes", str(p1), str(p2),
        "--phase-max-radius", "25", "20",
        "--phase-min-radius", "4", "3",
        "--phase-max-gens", "40", "40",
        "--plateau-window", "10", "--plateau-delta", "0.001",
        "--seed", "11", "-c", "5", "-j", "1",
        "-g", "100",
        "--save-gen", "10",  # frequent periodic
        "--save-on-exit", "--close-on-exit", "-p", "periodic",
        "-d", str(tmp_path),
    )
    assert result.returncode == 0, result.stderr
    periodic_files = list(tmp_path.glob("periodic-*.png"))
    # Filter out -end- files; want only periodic schedule
    periodic_only = [p for p in periodic_files if "-end-" not in p.name]
    assert len(periodic_only) > 0
    # Every periodic file should match the phase-aware pattern.
    pattern = re.compile(r"periodic-c\d+-p\d+-g\d+\.png")
    for f in periodic_only:
        assert pattern.match(f.name), f"unexpected periodic name: {f.name}"
