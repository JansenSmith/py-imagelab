"""Tests for tools/filament_stack_to_phases.py (F7).

Unit tests on the Beer-Lambert / ΔE / phase-derivation pure functions;
integration tests on the CLI (`python tools/filament_stack_to_phases.py ...`).
"""
import json
import math
import shutil
import subprocess
import sys
from pathlib import Path

from PIL import Image
import pytest

# Import the tool directly. tools/ isn't a package; add to sys.path.
TOOLS_DIR = Path(__file__).parent.parent / "tools"
sys.path.insert(0, str(TOOLS_DIR))
import filament_stack_to_phases as fstp  # noqa: E402


# ---------- hex_to_rgb / rgb_to_hex roundtrip ----------

@pytest.mark.parametrize("hex_in,rgb_out", [
    ("#000000", (0, 0, 0)),
    ("FFFFFF", (255, 255, 255)),
    ("#4E3524", (78, 53, 36)),
    ("e5dcc8", (229, 220, 200)),
])
def test_hex_to_rgb(hex_in, rgb_out):
    assert fstp.hex_to_rgb(hex_in) == rgb_out


@pytest.mark.parametrize("bad", ["", "#GGGGGG", "#12345", "12345678", "not-a-hex"])
def test_hex_to_rgb_rejects_invalid(bad):
    with pytest.raises(ValueError):
        fstp.hex_to_rgb(bad)


def test_rgb_to_hex_roundtrip():
    for h in ["#000000", "#FFFFFF", "#4E3524", "#E5DCC8"]:
        assert fstp.rgb_to_hex(fstp.hex_to_rgb(h)) == h.upper()


# ---------- Beer-Lambert opacity ----------

def test_layer_opacity_at_TD_equals_90pct():
    """At thickness = TD: T = 10^-1 = 0.1, opacity = 0.9 (90% blocked).
    This is the canonical TD definition."""
    assert fstp.layer_opacity(0.3, 0.3) == pytest.approx(0.9)
    assert fstp.layer_opacity(1.7, 1.7) == pytest.approx(0.9)
    assert fstp.layer_opacity(4.8, 4.8) == pytest.approx(0.9)


def test_layer_opacity_at_zero_thickness_is_zero():
    assert fstp.layer_opacity(0.0, 1.7) == 0.0


def test_layer_opacity_grows_with_thickness():
    td = 1.7
    op1 = fstp.layer_opacity(0.04, td)
    op2 = fstp.layer_opacity(0.08, td)
    op3 = fstp.layer_opacity(0.12, td)
    assert op1 < op2 < op3
    assert op1 < 1.0 and op3 < 1.0  # opacity is asymptotic


# ---------- Beer-Lambert blend ----------

def test_beer_lambert_blend_full_opacity_returns_filament():
    """At opacity=1.0, T=0, result == filament color (under fully blocked)."""
    out = fstp.beer_lambert_blend((255, 0, 0), 1.0, (0, 0, 0))
    assert out == pytest.approx((255, 0, 0))


def test_beer_lambert_blend_zero_opacity_returns_under():
    """At opacity=0, T=1, result == under color (filament fully transparent)."""
    out = fstp.beer_lambert_blend((255, 0, 0), 0.0, (10, 20, 30))
    assert out == pytest.approx((10, 20, 30))


def test_beer_lambert_blend_half_opacity_is_midpoint():
    out = fstp.beer_lambert_blend((200, 100, 50), 0.5, (0, 0, 0))
    assert out == pytest.approx((100, 50, 25))


# ---------- ΔE 76 ----------

def test_deltaE_76_identical_colors_zero():
    assert fstp.deltaE_76((128, 128, 128), (128, 128, 128)) == pytest.approx(0.0)


def test_deltaE_76_meaningful_difference_above_threshold():
    """Two visibly different colors should ΔE > 2.3 (JND)."""
    de = fstp.deltaE_76((0, 0, 0), (50, 50, 50))
    assert de > 2.3


def test_deltaE_76_symmetric():
    a, b = (100, 50, 25), (75, 200, 150)
    assert fstp.deltaE_76(a, b) == pytest.approx(fstp.deltaE_76(b, a))


# ---------- derive_phases ----------

def test_derive_phases_minimum_filaments():
    with pytest.raises(ValueError, match="at least one"):
        fstp.derive_phases([], 0.04)


def test_derive_phases_rejects_bad_layer_height():
    fil = fstp.Filament("#FF0000", 1.7)
    with pytest.raises(ValueError, match="layer_height"):
        fstp.derive_phases([fil], 0.0)
    with pytest.raises(ValueError, match="layer_height"):
        fstp.derive_phases([fil], -0.04)


def test_derive_phases_horses_sepia_in_range():
    """Horses sepia stack: bottom black + dark-brown flesh + bone white.
    Should produce a small but non-zero number of phases (not the
    340-layer disaster of pure-99%-opacity math, not zero either)."""
    filaments = [
        fstp.Filament("#000000", 0.3),
        fstp.Filament("#4E3524", 1.7),
        fstp.Filament("#E5DCC8", 4.8),
    ]
    phases = fstp.derive_phases(filaments, layer_height_mm=0.04)
    assert 1 <= len(phases) <= 30, (
        f"horses sepia phase count out of plausible range: {len(phases)}"
    )
    # Bottom filament should be skipped (canvas_init handles it).
    assert all(p.filament_idx >= 1 for p in phases)


def test_derive_phases_include_no_op_grows_count():
    """With include_no_op_layers=True, every layer (including the bottom
    filament's saturated layers) gets recorded — strictly more phases."""
    filaments = [
        fstp.Filament("#000000", 0.3),
        fstp.Filament("#4E3524", 1.7),
        fstp.Filament("#E5DCC8", 4.8),
    ]
    skip = fstp.derive_phases(filaments, 0.04, include_no_op_layers=False)
    no_skip = fstp.derive_phases(filaments, 0.04, include_no_op_layers=True)
    assert len(no_skip) > len(skip)


def test_derive_phases_single_filament_yields_zero():
    """Single bottom filament → no phases (the bottom is canvas, skipped)."""
    fil = fstp.Filament("#FF0000", 1.7)
    phases = fstp.derive_phases([fil], 0.04)
    assert phases == []


def test_derive_phases_records_have_increasing_phase_index():
    filaments = [
        fstp.Filament("#000000", 0.3),
        fstp.Filament("#FF0000", 1.7),
        fstp.Filament("#0000FF", 1.7),
    ]
    phases = fstp.derive_phases(filaments, 0.04)
    indices = [p.phase for p in phases]
    assert indices == sorted(indices)
    assert indices == list(range(1, len(phases) + 1))


# ---------- HFP convenience reader ----------

def test_hfp_parsing(tmp_path):
    """HFP filament_set is reversed (JSON index 0 = topmost in HF) → our
    parsed list should be in print order (bottom-up)."""
    hfp = tmp_path / "fake.hfp"
    hfp.write_text(json.dumps({
        "layer_height": 0.04,
        # Top-to-bottom in HFP convention:
        "filament_set": [
            {"Color": "#E5DCC8", "Transmissivity": 4.8, "Name": "Bone White"},
            {"Color": "#4E3524", "Transmissivity": 1.7, "Name": "Flesh"},
            {"Color": "#000000", "Transmissivity": 0.3, "Name": "Black"},
        ]
    }))
    lh, fils = fstp._load_filaments_from_hfp(str(hfp))
    assert lh == pytest.approx(0.04)
    # Bottom-up order: first item should be black.
    assert fils[0].hex == "#000000"
    assert fils[1].hex == "#4E3524"
    assert fils[2].hex == "#E5DCC8"
    assert fils[0].td == pytest.approx(0.3)
    assert fils[2].name == "Bone White"


def test_hfp_parsing_missing_file_raises(tmp_path):
    with pytest.raises(FileNotFoundError):
        fstp._load_filaments_from_hfp(str(tmp_path / "no_such.hfp"))


# ---------- CLI integration ----------

@pytest.fixture
def fstp_cmd():
    """Command to invoke the tool — direct python execution since `tools/`
    isn't on PATH as a script."""
    return [sys.executable, str(TOOLS_DIR / "filament_stack_to_phases.py")]


def test_cli_horses_sepia_generates_outputs(fstp_cmd, target_64x64, tmp_path):
    out_dir = tmp_path / "out"
    result = subprocess.run(
        fstp_cmd + [
            "--target", str(target_64x64),
            "--layer-height", "0.04",
            "--filament", "000000:0.3",
            "--filament", "4E3524:1.7",
            "--filament", "E5DCC8:4.8",
            "--out-dir", str(out_dir),
        ],
        capture_output=True, text=True, timeout=30,
    )
    assert result.returncode == 0, result.stderr

    # Expected output files.
    brushes_dir = out_dir / "brushes"
    init_canvas = out_dir / "init_canvas.png"
    phases_json = out_dir / "phases.json"
    sh = out_dir / "phase_run.sh"

    assert brushes_dir.is_dir()
    assert init_canvas.is_file()
    assert phases_json.is_file()
    assert sh.is_file()
    assert os.access(sh, os.X_OK), "phase_run.sh should be executable"

    # phases.json parseable + has expected structure.
    doc = json.loads(phases_json.read_text())
    assert doc["version"] == 1
    assert doc["layer_height_mm"] == pytest.approx(0.04)
    assert doc["deltae_formula"] == "CIE-1976"
    assert len(doc["filaments"]) == 3
    assert len(doc["phases"]) >= 1
    # Brush PNG count matches phase count.
    brush_pngs = sorted(brushes_dir.glob("phase_*.png"))
    assert len(brush_pngs) == len(doc["phases"])
    # Each brush PNG is RGB at default size.
    for png in brush_pngs:
        img = Image.open(png)
        assert img.mode == "RGB"
        assert img.size == (fstp.DEFAULT_BRUSH_SIZE, fstp.DEFAULT_BRUSH_SIZE)

    # init_canvas size matches target.
    target_img = Image.open(target_64x64)
    canvas_img = Image.open(init_canvas)
    assert canvas_img.size == target_img.size
    # init_canvas color == bottom filament hex.
    px = canvas_img.getpixel((0, 0))
    assert px == (0, 0, 0)


def test_cli_missing_layer_height_errors(fstp_cmd, target_64x64, tmp_path):
    result = subprocess.run(
        fstp_cmd + [
            "--target", str(target_64x64),
            "--filament", "000000:0.3",
            "--filament", "4E3524:1.7",
            "--out-dir", str(tmp_path / "out"),
        ],
        capture_output=True, text=True, timeout=10,
    )
    assert result.returncode != 0
    assert "--layer-height" in result.stderr


def test_cli_missing_filaments_errors(fstp_cmd, target_64x64, tmp_path):
    result = subprocess.run(
        fstp_cmd + [
            "--target", str(target_64x64),
            "--layer-height", "0.04",
            "--out-dir", str(tmp_path / "out"),
        ],
        capture_output=True, text=True, timeout=10,
    )
    assert result.returncode != 0
    assert "--filament" in result.stderr


def test_cli_bad_filament_format_errors(fstp_cmd, target_64x64, tmp_path):
    result = subprocess.run(
        fstp_cmd + [
            "--target", str(target_64x64),
            "--layer-height", "0.04",
            "--filament", "not-a-hex:1.7",
            "--out-dir", str(tmp_path / "out"),
        ],
        capture_output=True, text=True, timeout=10,
    )
    assert result.returncode != 0


def test_cli_missing_target_errors(fstp_cmd, tmp_path):
    result = subprocess.run(
        fstp_cmd + [
            "--target", str(tmp_path / "nope.png"),
            "--layer-height", "0.04",
            "--filament", "000000:0.3",
            "--filament", "4E3524:1.7",
            "--out-dir", str(tmp_path / "out"),
        ],
        capture_output=True, text=True, timeout=10,
    )
    assert result.returncode != 0
    assert "target" in result.stderr.lower()


def test_cli_hfp_pre_fills_filaments(fstp_cmd, target_64x64, tmp_path):
    """--hfp should pre-fill layer_height + filaments without needing
    explicit flags."""
    hfp = tmp_path / "fake.hfp"
    hfp.write_text(json.dumps({
        "layer_height": 0.04,
        "filament_set": [
            {"Color": "#E5DCC8", "Transmissivity": 4.8},
            {"Color": "#4E3524", "Transmissivity": 1.7},
            {"Color": "#000000", "Transmissivity": 0.3},
        ]
    }))
    out_dir = tmp_path / "out"
    result = subprocess.run(
        fstp_cmd + [
            "--target", str(target_64x64),
            "--hfp", str(hfp),
            "--out-dir", str(out_dir),
        ],
        capture_output=True, text=True, timeout=30,
    )
    assert result.returncode == 0, result.stderr
    assert (out_dir / "phases.json").is_file()


def test_cli_phase_run_sh_has_valid_invocation(fstp_cmd, target_64x64, tmp_path):
    """phase_run.sh should contain a syntactically-valid imagephase
    invocation. We don't actually run imagephase here (would need the
    full display stack); we grep for required flags."""
    out_dir = tmp_path / "out"
    result = subprocess.run(
        fstp_cmd + [
            "--target", str(target_64x64),
            "--layer-height", "0.04",
            "--filament", "000000:0.3",
            "--filament", "4E3524:1.7",
            "--filament", "E5DCC8:4.8",
            "--out-dir", str(out_dir),
        ],
        capture_output=True, text=True, timeout=30,
    )
    assert result.returncode == 0, result.stderr

    sh_content = (out_dir / "phase_run.sh").read_text()
    assert sh_content.startswith("#!"), "phase_run.sh must have shebang"
    assert "imagephase " in sh_content
    assert "--start-canvas" in sh_content
    assert "--phases " in sh_content
    assert "--phase-brushes " in sh_content


import os  # noqa: E402 — used in test_cli_horses_sepia_generates_outputs
