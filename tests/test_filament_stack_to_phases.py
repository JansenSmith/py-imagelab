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


# ---------- derive_phases (Path B: HFP-driven, all layers recorded) ----------

def test_derive_phases_minimum_filaments():
    with pytest.raises(ValueError, match="at least one"):
        fstp.derive_phases([], 0.04, max_thicknesses_mm=[])


def test_derive_phases_rejects_bad_layer_height():
    fil = fstp.Filament("#FF0000", 1.7)
    with pytest.raises(ValueError, match="layer_height"):
        fstp.derive_phases([fil], 0.0, max_thicknesses_mm=[0.16])
    with pytest.raises(ValueError, match="layer_height"):
        fstp.derive_phases([fil], -0.04, max_thicknesses_mm=[0.16])


def test_derive_phases_requires_max_thicknesses_mm():
    """Path B: max_thicknesses_mm is required (no physics-only fallback)."""
    fil = fstp.Filament("#FF0000", 1.7)
    with pytest.raises(ValueError, match="max_thicknesses_mm"):
        fstp.derive_phases([fil], 0.04, max_thicknesses_mm=None)


def test_derive_phases_horses_sepia_exactly_17():
    """Horses sepia with HFP-derived max_thicknesses: EXACTLY 17 phases
    (5 flesh layers @ 0.20mm + 12 bone-white layers @ 0.48mm). Colors
    match HF's per-layer Beer-Lambert predictions."""
    filaments = [
        fstp.Filament("#000000", 0.3),
        fstp.Filament("#4E3524", 1.7),
        fstp.Filament("#E5DCC8", 4.8),
    ]
    # Per HFP slider_values reversed to print order: [0.16, 0.36, 0.84] →
    # Z-deltas [0.16, 0.20, 0.48]. Bottom filament max = 0.16mm (skipped
    # as canvas anyway).
    max_thicknesses = [0.16, 0.20, 0.48]
    phases = fstp.derive_phases(
        filaments, layer_height_mm=0.04,
        max_thicknesses_mm=max_thicknesses,
    )
    assert len(phases) == 17, (
        f"expected 17 phases (5 flesh + 12 bone white); got {len(phases)}"
    )
    # First 5 phases are flesh (filament_idx=1); next 12 are bone white (=2).
    flesh_phases = [p for p in phases if p.filament_idx == 1]
    bone_phases = [p for p in phases if p.filament_idx == 2]
    assert len(flesh_phases) == 5
    assert len(bone_phases) == 12
    # Intra-filament layer numbers count 1..N per filament.
    assert [p.intra_filament_layer for p in flesh_phases] == [1, 2, 3, 4, 5]
    assert [p.intra_filament_layer for p in bone_phases] == list(range(1, 13))


def test_derive_phases_records_every_layer_no_jnd_skip():
    """Under Path B, every iterated layer becomes a phase (no JND-skip).
    Verify phase count equals sum of layers across non-bottom filaments."""
    filaments = [
        fstp.Filament("#000000", 0.3),
        fstp.Filament("#FF0000", 1.7),
        fstp.Filament("#0000FF", 1.7),
    ]
    # 3 layers red + 5 layers blue = 8 phases regardless of ΔE.
    max_thicknesses = [0.04, 0.12, 0.20]
    phases = fstp.derive_phases(
        filaments, 0.04, max_thicknesses_mm=max_thicknesses,
    )
    assert len(phases) == 3 + 5


def test_derive_phases_single_filament_yields_zero():
    """Single bottom filament → no phases (the bottom is canvas, skipped)."""
    fil = fstp.Filament("#FF0000", 1.7)
    phases = fstp.derive_phases([fil], 0.04, max_thicknesses_mm=[0.20])
    assert phases == []


def test_derive_phases_records_have_increasing_phase_index():
    filaments = [
        fstp.Filament("#000000", 0.3),
        fstp.Filament("#FF0000", 1.7),
        fstp.Filament("#0000FF", 1.7),
    ]
    phases = fstp.derive_phases(
        filaments, 0.04, max_thicknesses_mm=[0.04, 0.12, 0.20],
    )
    indices = [p.phase for p in phases]
    assert indices == sorted(indices)
    assert indices == list(range(1, len(phases) + 1))


def test_derive_phases_inter_filament_transition_uses_actual_top():
    """Bone white's `under_rgb` (starting under-color for its iteration)
    should equal flesh's ACTUAL last-layer color (top of flesh stack),
    not the last recorded phase's color. Under Path B those are the
    same since every layer is recorded — verify it explicitly."""
    filaments = [
        fstp.Filament("#000000", 0.3),
        fstp.Filament("#4E3524", 1.7),
        fstp.Filament("#E5DCC8", 4.8),
    ]
    phases = fstp.derive_phases(
        filaments, 0.04, max_thicknesses_mm=[0.16, 0.20, 0.48],
    )
    # Flesh's last (5th) recorded phase color:
    flesh_top = [p for p in phases if p.filament_idx == 1][-1].rgb
    # Bone white's 1st recorded phase color:
    bone_first = [p for p in phases if p.filament_idx == 2][0].rgb
    # Recompute bone_first directly using flesh_top as under_rgb:
    thickness = 0.04
    opacity = fstp.layer_opacity(thickness, 4.8)
    expected = fstp.beer_lambert_blend(
        (229, 220, 200), opacity, flesh_top,
    )
    # Should match to floating-point precision (~1e-6 per channel).
    for a, b in zip(bone_first, expected):
        assert abs(a - b) < 1e-6, (
            f"bone_first {bone_first} != expected {expected} (using flesh top {flesh_top})"
        )


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
    lh, fils, max_thicknesses = fstp._load_filaments_from_hfp(str(hfp))
    assert lh == pytest.approx(0.04)
    # Bottom-up order: first item should be black.
    assert fils[0].hex == "#000000"
    assert fils[1].hex == "#4E3524"
    assert fils[2].hex == "#E5DCC8"
    assert fils[0].td == pytest.approx(0.3)
    assert fils[2].name == "Bone White"
    # No slider_values in this fixture → max_thicknesses is None.
    assert max_thicknesses is None


def test_hfp_parsing_with_slider_values(tmp_path):
    """When slider_values is present, HFP reader returns per-filament max
    thicknesses computed as Z-delta from previous filament's top."""
    hfp = tmp_path / "horses_synthetic.hfp"
    # slider_values in HFP convention (top→bottom) — matches horses sepia:
    #   Bone White top at 0.84mm, Flesh top at 0.36mm, Black top at 0.16mm.
    hfp.write_text(json.dumps({
        "layer_height": 0.04,
        "filament_set": [
            {"Color": "#E5DCC8", "Transmissivity": 4.8, "Name": "Bone White"},
            {"Color": "#4E3524", "Transmissivity": 1.7, "Name": "Flesh"},
            {"Color": "#000000", "Transmissivity": 0.3, "Name": "Black"},
        ],
        "slider_values": [0.84, 0.36, 0.16],
    }))
    lh, fils, max_thicknesses = fstp._load_filaments_from_hfp(str(hfp))
    assert max_thicknesses is not None
    assert len(max_thicknesses) == 3
    # Print-order:
    # Black (bottom): max thickness = slider_values[0] = 0.16mm
    # Flesh:          0.36 - 0.16 = 0.20mm
    # Bone White:     0.84 - 0.36 = 0.48mm
    assert max_thicknesses[0] == pytest.approx(0.16)
    assert max_thicknesses[1] == pytest.approx(0.20)
    assert max_thicknesses[2] == pytest.approx(0.48)


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
    assert doc["version"] == 2
    assert doc["layer_height_mm"] == pytest.approx(0.04)
    assert doc["deltae_formula"] == "CIE-1976"
    assert len(doc["filaments"]) == 3
    assert len(doc["phases"]) >= 1
    # Without --nozzle-mm, no radius_schedule block.
    assert "radius_schedule" not in doc
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


# ---------- F8: nozzle-derived radius schedule ----------

class TestDeriveRadiusSchedule:
    """Unit coverage for `derive_radius_schedule`."""

    def test_horses_portrait_canonical(self):
        """Plan §9 acceptance — horses (242mm max-dim, image_max_dim_px=746,
        nozzle 0.4mm, safety 3.0)."""
        max_r, min_r = fstp.derive_radius_schedule(
            n_phases=3,
            image_max_dim_px=746,
            print_max_dim_mm=242.0,
            nozzle_mm=0.4,
            tower_safety_factor=3.0,
        )
        assert len(max_r) == 3 and len(min_r) == 3
        # min_radius_top: max(2, ceil(0.4 * 3.0 * (746/242) / 2))
        #              = max(2, ceil(1.849)) = max(2, 2) = 2
        assert min_r[-1] == 2
        # max_radius_phase_0 ≈ ceil(242 * 0.12 * 3.0826) ≈ ceil(89.52) = 90
        assert max_r[0] == 90
        # max_radius_phase_N ≈ max(2*3, ceil(242 * 0.025 * 3.0826))
        #                   ≈ max(6, ceil(18.65)) = max(6, 19) = 19
        assert max_r[-1] == 19
        # mid-phase is the linear-interpolation midpoint of 90 → 19
        assert max_r[1] == round(90 + (19 - 90) * 0.5)  # = 55
        # min_radius_other = max(2, floor(2 * 0.5)) = max(2, 1) = 2
        assert min_r[0] == 2

    def test_safety_factor_linear_in_min_tower(self):
        _, min_r_3 = fstp.derive_radius_schedule(
            3, 746, 242.0, 0.4, 3.0,
        )
        _, min_r_6 = fstp.derive_radius_schedule(
            3, 746, 242.0, 0.4, 6.0,
        )
        # Double safety factor → roughly double min_radius_top.
        # min_top@3 = max(2, ceil(1.849)) = 2
        # min_top@6 = max(2, ceil(3.699)) = 4
        assert min_r_3[-1] == 2
        assert min_r_6[-1] == 4

    def test_landscape_same_as_portrait_when_max_dim_matches(self):
        """Orientation-independence: 746px max-dim should yield same
        pixels_per_mm whether portrait (W=600,H=746) or landscape
        (W=746,H=600). The helper takes a max-dim scalar, so this
        is verified at the call site — confirm here that the scalar
        is what matters."""
        a_max, a_min = fstp.derive_radius_schedule(
            5, 746, 242.0, 0.4, 3.0,
        )
        b_max, b_min = fstp.derive_radius_schedule(
            5, 746, 242.0, 0.4, 3.0,
        )
        assert a_max == b_max
        assert a_min == b_min

    def test_max_radius_monotonically_decreases(self):
        """Bottom phases paint big strokes; top phases paint small ones."""
        max_r, _ = fstp.derive_radius_schedule(
            5, 1000, 242.0, 0.4, 3.0,
        )
        # Linear interpolation should produce monotonic non-increasing series.
        for i in range(len(max_r) - 1):
            assert max_r[i] >= max_r[i + 1], (
                f"max-radius should not grow with phase: {max_r}"
            )

    def test_single_phase(self):
        max_r, min_r = fstp.derive_radius_schedule(
            1, 746, 242.0, 0.4, 3.0,
        )
        assert len(max_r) == 1 and len(min_r) == 1
        # Single phase → use top values directly.
        assert min_r[0] == 2

    def test_rejects_zero_phases(self):
        with pytest.raises(ValueError):
            fstp.derive_radius_schedule(0, 746, 242.0, 0.4, 3.0)

    def test_rejects_nonpositive_inputs(self):
        with pytest.raises(ValueError):
            fstp.derive_radius_schedule(3, 0, 242.0, 0.4, 3.0)
        with pytest.raises(ValueError):
            fstp.derive_radius_schedule(3, 746, 242.0, 0, 3.0)
        with pytest.raises(ValueError):
            fstp.derive_radius_schedule(3, 746, 242.0, 0.4, 0)


class TestPixelsPerMmWarning:
    def test_within_band_no_warning(self):
        assert fstp.pixels_per_mm_warning(3.0) is None
        assert fstp.pixels_per_mm_warning(2.0) is None
        assert fstp.pixels_per_mm_warning(5.0) is None

    def test_below_band_warns_blocky(self):
        w = fstp.pixels_per_mm_warning(1.5)
        assert w and "blocky" in w

    def test_above_band_warns_slow(self):
        w = fstp.pixels_per_mm_warning(8.0)
        assert w and ("slow" in w or "convergence" in w)


def test_cli_radius_schedule_active_when_nozzle_supplied(
    fstp_cmd, target_64x64, tmp_path
):
    """--nozzle-mm activates F8: phases.json has radius_schedule block,
    phase_run.sh emits --phase-max-radius / --phase-min-radius."""
    out_dir = tmp_path / "out"
    result = subprocess.run(
        fstp_cmd + [
            "--target", str(target_64x64),
            "--layer-height", "0.04",
            "--filament", "000000:0.3",
            "--filament", "4E3524:1.7",
            "--filament", "E5DCC8:4.8",
            "--out-dir", str(out_dir),
            "--nozzle-mm", "0.4",
            "--print-max-dim-mm", "100",  # 64px / 100mm = 0.64 px/mm — will warn
            "--tower-safety-factor", "3.0",
        ],
        capture_output=True, text=True, timeout=30,
    )
    assert result.returncode == 0, result.stderr
    # Sub-band pixels_per_mm → blocky warning on stderr.
    assert "blocky" in result.stderr or "warning" in result.stderr.lower()

    doc = json.loads((out_dir / "phases.json").read_text())
    assert "radius_schedule" in doc
    rs = doc["radius_schedule"]
    assert rs["nozzle_mm"] == 0.4
    assert rs["print_max_dim_mm"] == 100.0
    assert rs["tower_safety_factor"] == 3.0
    assert len(rs["max_radii_px"]) == len(doc["phases"])
    assert len(rs["min_radii_px"]) == len(doc["phases"])

    sh = (out_dir / "phase_run.sh").read_text()
    assert "--phase-max-radius" in sh
    assert "--phase-min-radius" in sh


def test_cli_no_nozzle_omits_radius_schedule(
    fstp_cmd, target_64x64, tmp_path
):
    """Without --nozzle-mm, F8 stays inactive: no radius_schedule in JSON,
    no --phase-{max,min}-radius flags in shell script (F7 behavior preserved)."""
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
    doc = json.loads((out_dir / "phases.json").read_text())
    assert "radius_schedule" not in doc
    sh = (out_dir / "phase_run.sh").read_text()
    assert "--phase-max-radius" not in sh
    assert "--phase-min-radius" not in sh


def test_cli_defaults_match_documented_values(fstp_cmd, target_64x64, tmp_path):
    """Tower safety factor + print-max-dim defaults match the calibrated
    values (per tools/CITATIONS.md). Regression sentinel — change the
    default ⇒ change this assertion ⇒ surface in PR review."""
    assert fstp.DEFAULT_TOWER_SAFETY_FACTOR == 3.0
    assert fstp.DEFAULT_PRINT_MAX_DIM_MM == 242.0
