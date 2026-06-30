"""Tests for F6 — per-phase stats output + live-progress stdout.

Covers:
- PhaseState's finalize_current_phase / get_stats_list / window_delta API.
- imagephase's formalized per-advance stderr line schema.
- End-of-run per-phase stats table on stdout.
- `-i` JSON includes `phase_stats: [...]` and tags each shape with `phase: int`.
"""
import json
import re
import shutil
import subprocess

from PIL import Image
import pytest

from imagelab.phasing import PhaseState


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


# ---------- PhaseState stats API ----------

def test_finalize_records_all_fields():
    ps = PhaseState(
        phase_brushes=[["a.png"], ["b.png"]],
        phase_max_radius=[100, 50],
        phase_min_radius=[10, 5],
        phase_max_gens=[float('inf')] * 2,
        plateau_window=10,
        plateau_delta=0.005,
    )
    ps.record_gen(50.0)
    ps.record_gen(55.0)
    ps.record_gen(60.0)
    rec = ps.finalize_current_phase('plateau')
    assert rec['phase'] == 1
    assert rec['gens_used'] == 3
    assert rec['match_at_start'] == pytest.approx(50.0)
    assert rec['match_at_end'] == pytest.approx(60.0)
    assert rec['clock_time'] >= 0
    assert rec['advance_reason'] == 'plateau'


def test_get_stats_list_accumulates_across_advances():
    ps = PhaseState(
        phase_brushes=[["a.png"], ["b.png"], ["c.png"]],
        phase_max_radius=[100, 50, 25],
        phase_min_radius=[10, 5, 3],
        phase_max_gens=[float('inf')] * 3,
        plateau_window=10,
        plateau_delta=0.005,
    )
    for _ in range(5):
        ps.record_gen(40.0)
    ps.finalize_current_phase('plateau')
    ps.advance()
    for _ in range(3):
        ps.record_gen(60.0)
    ps.finalize_current_phase('max_gens')

    stats = ps.get_stats_list()
    assert len(stats) == 2
    assert [s['phase'] for s in stats] == [1, 2]
    assert [s['gens_used'] for s in stats] == [5, 3]
    assert stats[0]['advance_reason'] == 'plateau'
    assert stats[1]['advance_reason'] == 'max_gens'


def test_advance_resets_start_tracking():
    """After advance, match_at_start of the new phase comes from the FIRST
    record_gen of that phase — not bleeding from the previous phase."""
    ps = PhaseState(
        phase_brushes=[["a.png"], ["b.png"]],
        phase_max_radius=[100, 50],
        phase_min_radius=[10, 5],
        phase_max_gens=[float('inf')] * 2,
        plateau_window=10,
        plateau_delta=0.005,
    )
    ps.record_gen(40.0)
    ps.record_gen(50.0)
    ps.finalize_current_phase('plateau')
    ps.advance()
    ps.record_gen(70.0)
    rec = ps.finalize_current_phase('plateau')
    assert rec['match_at_start'] == pytest.approx(70.0)
    assert rec['match_at_end'] == pytest.approx(70.0)


def test_window_delta_empty_history():
    ps = PhaseState(
        phase_brushes=[["a.png"]],
        phase_max_radius=[100],
        phase_min_radius=[10],
        phase_max_gens=[float('inf')],
    )
    assert ps.window_delta() == 0.0


def test_window_delta_with_history():
    ps = PhaseState(
        phase_brushes=[["a.png"]],
        phase_max_radius=[100],
        phase_min_radius=[10],
        phase_max_gens=[float('inf')],
        plateau_window=10,
    )
    ps.record_gen(50.0)
    ps.record_gen(55.0)
    ps.record_gen(52.0)
    assert ps.window_delta() == pytest.approx(5.0)


# ---------- imagephase integration: stderr per-advance schema ----------

def test_advance_stderr_includes_all_fields(
    imagephase_cmd, target_3band, brush_solid, solid_canvas, tmp_path,
):
    """The per-advance stderr line must include match-start, match-end, and
    window-delta (added in F6; F3 was missing some)."""
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
        "--phase-max-gens", "15", "15",
        "--plateau-window", "10", "--plateau-delta", "0.001",
        "--seed", "1", "-c", "5", "-j", "1",
        "-g", "100",
        "--save-on-exit", "--close-on-exit", "-p", "stats_e2e",
        "-d", str(tmp_path),
    )
    assert result.returncode == 0, result.stderr

    # Required fields in the advance/final stderr lines.
    for required in ("reason=", "gens=", "match-start=", "match-end=", "window-delta="):
        assert required in result.stderr, (
            f"per-advance stderr missing '{required}':\n{result.stderr}"
        )


# ---------- end-of-run stats table ----------

def test_end_of_run_stats_table_on_stdout(
    imagephase_cmd, target_3band, brush_solid, solid_canvas, tmp_path,
):
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
        "--phase-max-gens", "15", "15", "15",
        "--plateau-window", "10", "--plateau-delta", "0.001",
        "--seed", "2", "-c", "5", "-j", "1",
        "-g", "150",
        "--save-on-exit", "--close-on-exit", "-p", "tbl",
        "-d", str(tmp_path),
    )
    assert result.returncode == 0, result.stderr

    assert "===== phase stats =====" in result.stdout
    # Three phase rows.
    rows = re.findall(
        r"^\s*(\d+)\s+(\d+)\s+([\d.]+)%\s+([\d.]+)%\s+([\d.]+)\s+(\w+)",
        result.stdout, flags=re.MULTILINE,
    )
    assert len(rows) == 3, f"expected 3 phase-stats rows, got:\n{result.stdout}"
    phases = [int(r[0]) for r in rows]
    assert phases == [1, 2, 3]


# ---------- -i JSON: phase_stats + per-shape phase tag ----------

def test_instructions_json_has_phase_stats(
    imagephase_cmd, target_3band, brush_solid, solid_canvas, tmp_path,
):
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
        "--phase-max-gens", "10", "10",
        "--plateau-window", "8", "--plateau-delta", "0.001",
        "--seed", "3", "-c", "3", "-j", "1",
        "-g", "60",
        "-i",  # instructions / JSON mode
        "--save-on-exit", "--close-on-exit", "-p", "json",
        "-d", str(tmp_path),
    )
    assert result.returncode == 0, result.stderr

    json_files = list(tmp_path.glob("json-*.json"))
    assert len(json_files) == 1, f"expected one JSON output, got {json_files}"
    data = json.loads(json_files[0].read_text())

    assert data.get('version') == 2, "JSON schema version should be 2 in phase mode"
    assert 'phase_stats' in data
    assert len(data['phase_stats']) == 2
    for ps in data['phase_stats']:
        for key in ('phase', 'gens_used', 'match_at_start', 'match_at_end',
                    'clock_time', 'advance_reason'):
            assert key in ps, f"phase_stats record missing key {key!r}: {ps}"


def test_instructions_json_shapes_have_phase_tag(
    imagephase_cmd, target_3band, brush_solid, solid_canvas, tmp_path,
):
    """Each shape in canvas history gets a 'phase' field tagged based on
    cumulative phase gens. Phase 1's shapes are at indices [0:gens1],
    phase 2's at [gens1:gens1+gens2], etc."""
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
        "--phase-max-gens", "8", "8", "8",
        "--plateau-window", "6", "--plateau-delta", "0.001",
        "--seed", "4", "-c", "3", "-j", "1",
        "-g", "60",
        "-i",
        "--save-on-exit", "--close-on-exit", "-p", "tag",
        "-d", str(tmp_path),
    )
    assert result.returncode == 0, result.stderr
    data = json.loads(next(tmp_path.glob("tag-*.json")).read_text())

    history = data.get('history', [])
    assert len(history) > 0
    # Per CanvasAction.__json__ schema, each entry is [opcode, params_dict].
    # Phase tags should appear in non-decreasing order through history.
    phases_seen = []
    for entry in history:
        assert isinstance(entry, list) and len(entry) >= 2, (
            f"unexpected history entry shape: {entry!r}"
        )
        params = entry[1]
        if isinstance(params, dict) and 'phase' in params:
            phases_seen.append(params['phase'])
    assert len(phases_seen) == len(history), (
        "every shape should be tagged with 'phase'"
    )
    assert phases_seen == sorted(phases_seen), (
        f"phase tags not monotonically non-decreasing: {phases_seen}"
    )
    # Every recorded phase should appear at least once.
    assert set(phases_seen).issubset({1, 2, 3})
