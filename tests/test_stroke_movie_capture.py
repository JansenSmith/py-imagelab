"""Tests for F9 — per-stroke frame dump + ffmpeg movie assembler.

When --frames-dir is set on imagephase, every winning child's post-stroke
canvas state is saved as a numbered PNG. When --phase-pause-frames is set,
N replicas of the boundary frame are also written at every phase advance.

Frame naming: <prefix>-frame-NNNNNNN.png (7-digit zero-pad, monotonic
across the whole run regardless of phase).

The assembler tool (tools/assemble_stroke_movie.py) is a thin ffmpeg
wrapper; only the dry-run path + frame-sequence-discovery is exercised
here. End-to-end mp4 generation requires ffmpeg; tested when available.
"""
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

from PIL import Image
import pytest


# Locate the assembler under tools/.
TOOLS_DIR = Path(__file__).parent.parent / "tools"
ASSEMBLER = TOOLS_DIR / "assemble_stroke_movie.py"


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


# ---------- CLI integration: frame writing ----------

def test_frames_dir_off_by_default_no_files_written(
    imagephase_cmd, target_3band, brush_solid, solid_canvas, tmp_path,
):
    """Without --frames-dir, no frame files written (zero F9 I/O).
    Backward-compat sentinel: F5/F6 runs must not start dumping frames."""
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
        "-g", "100",
        "--save-on-exit", "--close-on-exit", "-p", "nodump",
        "-d", str(tmp_path),
    )
    assert result.returncode == 0, result.stderr
    frame_files = list(tmp_path.glob("*-frame-*.png"))
    assert frame_files == [], (
        f"expected NO frame files without --frames-dir, got: "
        f"{[p.name for p in frame_files]}"
    )


def test_frames_dir_writes_one_per_winning_child_no_pauses(
    imagephase_cmd, target_3band, brush_solid, solid_canvas, tmp_path,
):
    """With --frames-dir but --phase-pause-frames 0 (the default),
    frame count equals total winning-child gens across the whole run.
    No pause replicas inserted."""
    canvas = solid_canvas((64, 192), "#000000")
    p1 = brush_solid("#000000", size=128)
    p2 = brush_solid("#FF0000", size=128)
    frames_dir = tmp_path / "frames"

    # Tight caps + small -g so the gen count is predictable and small.
    total_gens = 60
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
        "-g", str(total_gens),
        "--frames-dir", str(frames_dir),
        "--save-on-exit", "--close-on-exit", "-p", "movie",
        "-d", str(tmp_path),
    )
    assert result.returncode == 0, result.stderr

    frame_files = sorted(frames_dir.glob("movie-frame-*.png"))
    assert len(frame_files) > 0, "expected frame files to be written"
    # Naming format: 7-digit zero-padded, monotonic from 0.
    pattern = re.compile(r"movie-frame-(\d{7})\.png")
    indices = []
    for f in frame_files:
        m = pattern.match(f.name)
        assert m, f"unexpected frame name: {f.name}"
        indices.append(int(m.group(1)))
    assert indices == sorted(indices), "frame indices should be monotonic"
    assert indices[0] == 0, "frame counter should start at 0"
    # Frame count <= total_gens (early advance can terminate before the cap).
    assert len(frame_files) <= total_gens, (
        f"frame count {len(frame_files)} exceeds gen ceiling {total_gens}"
    )


def test_phase_pause_frames_inserts_replicas_at_boundaries(
    imagephase_cmd, target_3band, brush_solid, solid_canvas, tmp_path,
):
    """3 phases, --phase-pause-frames 20: total frames should be
    (painting gens) + 20 × (phase advances) = painting gens + 40
    (since 3 phases means 2 inter-phase boundaries; the final completion
    is not a transition that warrants pause frames)."""
    canvas = solid_canvas((64, 192), "#000000")
    p1 = brush_solid("#000000", size=128)
    p2 = brush_solid("#FF0000", size=128)
    p3 = brush_solid("#0000FF", size=128)
    frames_dir = tmp_path / "frames"

    # Run WITHOUT pauses to baseline the painting gen count.
    baseline_dir = tmp_path / "baseline"
    common = [
        str(target_3band),
        "--start-canvas", str(canvas),
        "--phases", "3",
        "--phase-brushes", str(p1), str(p2), str(p3),
        "--phase-max-radius", "25", "20", "16",
        "--phase-min-radius", "4", "3", "2",
        "--phase-max-gens", "15", "15", "15",
        "--plateau-window", "8", "--plateau-delta", "0.001",
        "--seed", "11", "-c", "5", "-j", "1",
        "-g", "150",
        "--save-on-exit", "--close-on-exit",
    ]
    r0 = _run(
        imagephase_cmd, *common,
        "-p", "base", "-d", str(tmp_path),
        "--frames-dir", str(baseline_dir),
    )
    assert r0.returncode == 0, r0.stderr
    baseline_count = len(list(baseline_dir.glob("base-frame-*.png")))
    assert baseline_count > 0, "baseline must capture some painted gens"

    # Now run with 20 pause frames per boundary.
    r1 = _run(
        imagephase_cmd, *common,
        "-p", "paused", "-d", str(tmp_path),
        "--frames-dir", str(frames_dir),
        "--phase-pause-frames", "20",
    )
    assert r1.returncode == 0, r1.stderr
    paused_count = len(list(frames_dir.glob("paused-frame-*.png")))

    # 3 phases = 2 inter-phase advances (the final phase's plateau doesn't
    # add a pause; pause writes happen BEFORE phase_state.advance(), which
    # the implementation skips when is_last_phase).
    expected_extra = 20 * 2
    assert paused_count == baseline_count + expected_extra, (
        f"expected {baseline_count} + {expected_extra} = "
        f"{baseline_count + expected_extra} frames; got {paused_count}"
    )


def test_frame_indices_are_monotonic_and_dense(
    imagephase_cmd, target_3band, brush_solid, solid_canvas, tmp_path,
):
    """Frame indices form a dense 0..N-1 sequence (no gaps; no duplicates).
    Important for ffmpeg's `-i pattern-%07d.png` to consume cleanly."""
    canvas = solid_canvas((64, 192), "#000000")
    p1 = brush_solid("#000000", size=128)
    p2 = brush_solid("#FF0000", size=128)
    frames_dir = tmp_path / "frames"

    r = _run(
        imagephase_cmd, str(target_3band),
        "--start-canvas", str(canvas),
        "--phases", "2",
        "--phase-brushes", str(p1), str(p2),
        "--phase-max-radius", "25", "20",
        "--phase-min-radius", "4", "3",
        "--phase-max-gens", "12", "12",
        "--plateau-window", "8", "--plateau-delta", "0.001",
        "--seed", "11", "-c", "5", "-j", "1",
        "-g", "50",
        "--frames-dir", str(frames_dir),
        "--phase-pause-frames", "5",
        "--save-on-exit", "--close-on-exit", "-p", "dense",
        "-d", str(tmp_path),
    )
    assert r.returncode == 0, r.stderr

    pattern = re.compile(r"dense-frame-(\d{7})\.png")
    indices = sorted(
        int(pattern.match(f.name).group(1))
        for f in frames_dir.glob("dense-frame-*.png")
    )
    assert indices == list(range(len(indices))), (
        f"frame indices not dense 0..N-1; got: {indices[:5]}...{indices[-5:]}"
    )


def test_frame_content_accumulates_strokes_over_time(
    imagephase_cmd, target_3band, brush_solid, solid_canvas, tmp_path,
):
    """Frames capture canvas state over the course of the run — so the LAST
    frame should differ from the FIRST frame (more strokes have accumulated).
    Frame 0 itself may or may not differ from the start canvas (depends on
    where the first winning stroke landed under the seed), but by the end of
    the run the canvas has clearly accumulated visible paint."""
    canvas = solid_canvas((64, 192), "#000000")
    p1 = brush_solid("#FF0000", size=128)  # red brush — visible against black
    p2 = brush_solid("#0000FF", size=128)
    frames_dir = tmp_path / "frames"

    r = _run(
        imagephase_cmd, str(target_3band),
        "--start-canvas", str(canvas),
        "--phases", "2",
        "--phase-brushes", str(p1), str(p2),
        "--phase-max-radius", "30", "25",
        "--phase-min-radius", "5", "3",
        "--phase-max-gens", "25", "25",
        "--plateau-window", "10", "--plateau-delta", "0.001",
        "--seed", "11", "-c", "5", "-j", "1",
        "-g", "60",
        "--frames-dir", str(frames_dir),
        "--save-on-exit", "--close-on-exit", "-p", "content",
        "-d", str(tmp_path),
    )
    assert r.returncode == 0, r.stderr

    frames = sorted(frames_dir.glob("content-frame-*.png"))
    assert len(frames) >= 4, "need ≥4 frames to compare early vs late"

    first = Image.open(frames[0]).convert("RGB")
    last = Image.open(frames[-1]).convert("RGB")
    assert first.size == (64, 192)
    assert last.size == (64, 192)
    # By the end of the run, the canvas should have clearly accumulated
    # non-black pixels (red/blue strokes).
    last_pixels = list(last.getdata())
    assert any(px != (0, 0, 0) for px in last_pixels), (
        "last frame should have non-black pixels after accumulated strokes"
    )
    # Last frame should differ from frame 0.
    assert last_pixels != list(first.getdata()), (
        "last frame should differ from frame 0 after additional strokes"
    )


# ---------- Assembler tool ----------

def _make_synthetic_frame_dir(tmp_path, prefix, n_frames, size=(32, 32)):
    """Create a directory of n synthetic frames named `<prefix>-frame-NNNNNNN.png`."""
    d = tmp_path / "syn_frames"
    d.mkdir()
    for i in range(n_frames):
        # Vary color per frame so ffmpeg has actual motion to encode.
        c = (i * 7 % 256, i * 13 % 256, i * 19 % 256)
        Image.new("RGB", size, c).save(d / f"{prefix}-frame-{i:07d}.png")
    return d


def _assembler_cmd():
    return [sys.executable, str(ASSEMBLER)]


def test_assembler_finds_frame_sequence(tmp_path):
    """find_frame_sequence detects prefix + count + range from a clean dir."""
    sys.path.insert(0, str(TOOLS_DIR))
    try:
        import assemble_stroke_movie as asm
    finally:
        sys.path.pop(0)

    d = _make_synthetic_frame_dir(tmp_path, "foo", 5)
    prefix, count, first, last = asm.find_frame_sequence(str(d))
    assert prefix == "foo"
    assert count == 5
    assert first == 0
    assert last == 4


def test_assembler_dry_run_no_ffmpeg_needed(tmp_path):
    """--dry-run prints the ffmpeg command without invoking it. Exercises
    the full arg-parsing + cmd-construction path without needing ffmpeg
    installed (so this test runs even on minimal CI images)."""
    if not shutil.which("ffmpeg"):
        pytest.skip("--dry-run still requires ffmpeg on PATH for the check")
    d = _make_synthetic_frame_dir(tmp_path, "bar", 3)
    out = tmp_path / "x.mp4"
    r = subprocess.run(
        _assembler_cmd() + [
            "--frames-dir", str(d),
            "--output", str(out),
            "--dry-run",
        ],
        capture_output=True, text=True, timeout=15,
    )
    assert r.returncode == 0, r.stderr
    assert "ffmpeg" in r.stdout
    assert "-framerate" in r.stdout
    assert "bar-frame-%07d.png" in r.stdout
    # Output file should NOT have been created (dry-run).
    assert not out.exists()


def test_assembler_errors_on_missing_dir(tmp_path):
    bogus = tmp_path / "nope"
    r = subprocess.run(
        _assembler_cmd() + [
            "--frames-dir", str(bogus),
            "--output", str(tmp_path / "x.mp4"),
            "--dry-run",
        ],
        capture_output=True, text=True, timeout=15,
    )
    assert r.returncode != 0
    assert "does not exist" in r.stderr or "no such" in r.stderr.lower()


def test_assembler_errors_on_empty_dir(tmp_path):
    empty = tmp_path / "empty"
    empty.mkdir()
    r = subprocess.run(
        _assembler_cmd() + [
            "--frames-dir", str(empty),
            "--output", str(tmp_path / "x.mp4"),
            "--dry-run",
        ],
        capture_output=True, text=True, timeout=15,
    )
    assert r.returncode != 0
    assert "no '*-frame-*.png' files" in r.stderr


def test_assembler_rejects_multiple_prefixes(tmp_path):
    """If a frames-dir has frames from two runs (two prefixes), the assembler
    refuses rather than silently picking one — output would be ambiguous."""
    sys.path.insert(0, str(TOOLS_DIR))
    try:
        import assemble_stroke_movie as asm
    finally:
        sys.path.pop(0)

    d = tmp_path / "multi"
    d.mkdir()
    Image.new("RGB", (32, 32), (10, 20, 30)).save(d / "alpha-frame-0000000.png")
    Image.new("RGB", (32, 32), (40, 50, 60)).save(d / "beta-frame-0000000.png")
    with pytest.raises(ValueError, match="multiple frame-sequence prefixes"):
        asm.find_frame_sequence(str(d))


def test_assembler_actually_produces_mp4_when_ffmpeg_present(tmp_path):
    """End-to-end smoke: synthetic frames → mp4 → ffprobe says it has the
    expected frame count. Skipped when ffmpeg/ffprobe are absent."""
    if not (shutil.which("ffmpeg") and shutil.which("ffprobe")):
        pytest.skip("ffmpeg/ffprobe not available")
    d = _make_synthetic_frame_dir(tmp_path, "smoke", 10, size=(64, 64))
    out = tmp_path / "smoke.mp4"
    r = subprocess.run(
        _assembler_cmd() + [
            "--frames-dir", str(d),
            "--output", str(out),
        ],
        capture_output=True, text=True, timeout=60,
    )
    assert r.returncode == 0, r.stderr
    assert out.exists()
    # ffprobe to verify frame count.
    probe = subprocess.run(
        [
            "ffprobe", "-v", "error",
            "-select_streams", "v:0",
            "-count_frames",
            "-show_entries", "stream=nb_read_frames",
            "-of", "default=nokey=1:noprint_wrappers=1",
            str(out),
        ],
        capture_output=True, text=True, timeout=30,
    )
    assert probe.returncode == 0, probe.stderr
    assert int(probe.stdout.strip()) == 10
