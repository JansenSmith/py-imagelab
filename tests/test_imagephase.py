"""Integration tests for the imagephase app (F3).

Unit tests for the underlying phasing library live in test_phasing.py;
these exercise the full CLI + App + library wiring end-to-end.
"""
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
    """Factory: solid_canvas(size_tuple, hex) -> path to a solid-color
    canvas PNG. Used to seed --start-canvas for phase mode."""
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


# ---------- 3-band synthetic: phase mode actually paints in phases ----------

def test_3band_synthetic_each_band_majority_correct(
    imagephase_cmd, target_3band, brush_solid, solid_canvas, tmp_path,
):
    """Target is 3 horizontal bands (black/red/blue). Each phase uses the
    matching brush. After enough gens-per-phase, each 64-pixel-tall band in
    the output should be majority-its-color (>60% of pixels exactly the
    brush color). Loose threshold accounts for shape overlap at band edges
    and the GA's stochastic nature."""
    canvas = solid_canvas((64, 192), "#000000")
    p_black = brush_solid("#000000", size=128)
    p_red = brush_solid("#FF0000", size=128)
    p_blue = brush_solid("#0000FF", size=128)

    result = _run(
        imagephase_cmd, str(target_3band),
        "--start-canvas", str(canvas),
        "--phases", "3",
        "--phase-brushes", str(p_black), str(p_red), str(p_blue),
        "--phase-max-radius", "25", "20", "16",
        "--phase-min-radius", "4", "3", "2",
        "--phase-max-gens", "60", "60", "60",
        "--plateau-window", "30", "--plateau-delta", "0.01",
        "--seed", "5", "-c", "8", "-j", "1",
        "-g", "400",  # safety ceiling
        "--save-on-exit", "--close-on-exit", "-p", "band",
        "-d", str(tmp_path),
    )
    assert result.returncode == 0, (
        f"imagephase exited {result.returncode}\nstderr:\n{result.stderr}"
    )
    out_path = next(tmp_path.glob("band-*.png"))
    out = Image.open(out_path).convert("RGB")

    def band_majority(y_start, y_end, expected_rgb):
        """Fraction of pixels close to expected_rgb (per-channel tolerance 4).
        pygame.transform.smoothscale's bilinear interpolation slightly mutes
        saturated channels (#FF0000 brush → ~(253,0,0) on canvas), so exact
        equality is too strict."""
        total = 0
        match = 0
        er, eg, eb = expected_rgb
        for y in range(y_start, y_end):
            for x in range(out.width):
                total += 1
                r, g, b = out.getpixel((x, y))
                if abs(r - er) <= 4 and abs(g - eg) <= 4 and abs(b - eb) <= 4:
                    match += 1
        return match / total

    # Three 64-pixel-tall bands.
    black_frac = band_majority(0, 64, (0, 0, 0))
    red_frac = band_majority(64, 128, (255, 0, 0))
    blue_frac = band_majority(128, 192, (0, 0, 255))

    # Loose threshold: ≥60% of each band should be its phase color exactly.
    # GA cross-contamination at band edges is expected; 60% leaves room for
    # stochastic shape placement noise.
    assert black_frac > 0.6, f"black band only {black_frac:.1%} pure"
    assert red_frac > 0.6, f"red band only {red_frac:.1%} pure"
    assert blue_frac > 0.6, f"blue band only {blue_frac:.1%} pure"


# ---------- Phase advance triggers ----------

def test_plateau_unstuck_on_noise_target(
    imagephase_cmd, brush_solid, solid_canvas, tmp_path,
):
    """A target that's essentially un-matchable (random noise) with tight
    plateau bounds should still advance phases via the plateau detector
    rather than getting stuck."""
    # Make a noise target via Pillow.
    import random
    random.seed(13)
    target = tmp_path / "noise.png"
    img = Image.new("RGB", (32, 32))
    pixels = [
        (random.randint(0, 255), random.randint(0, 255), random.randint(0, 255))
        for _ in range(32 * 32)
    ]
    img.putdata(pixels)
    img.save(target, "PNG")

    canvas = solid_canvas((32, 32), "#000000")
    p1 = brush_solid("#888888", size=64)
    p2 = brush_solid("#CCCCCC", size=64)

    # Tight bounds: small window + tiny delta + a safety cap so we don't run
    # forever if the plateau detector is broken. Plateau should trip within
    # ~window gens.
    result = _run(
        imagephase_cmd, str(target),
        "--start-canvas", str(canvas),
        "--phases", "2",
        "--phase-brushes", str(p1), str(p2),
        "--phase-max-radius", "10", "8",
        "--phase-min-radius", "2", "2",
        "--phase-max-gens", "100", "100",  # safety net only
        "--plateau-window", "20", "--plateau-delta", "0.001",
        "--seed", "9", "-c", "3", "-j", "1",
        "-g", "500",
        "--save-on-exit", "--close-on-exit", "-p", "noise",
        "-d", str(tmp_path),
    )
    assert result.returncode == 0, result.stderr
    # Look for AT LEAST ONE plateau-triggered advance or final. If only
    # max_gens fires, the plateau detector wasn't actually engaging on the
    # noisy/unmatchable target — that'd suggest a logic bug. Phase 1 may
    # legitimately hit max_gens first (the GA finds some small improvements
    # via the adaptive radius schedule); phase 2's final should be plateau
    # since the late-game has less room to improve.
    assert "reason=plateau" in result.stderr, (
        f"expected at least one plateau-triggered advance; got:\n{result.stderr}"
    )


# ---------- Required-flag interlocks ----------

def test_no_phases_flag_is_argparse_error(imagephase_cmd, target_3band):
    """argparse requires --phases."""
    result = _run(
        imagephase_cmd, str(target_3band),
        "--start-canvas", str(target_3band),
        "--phase-brushes", str(target_3band),
        timeout=15,
    )
    assert result.returncode != 0
    assert "--phases" in result.stderr


def test_phases_zero_or_negative_rejected(imagephase_cmd, target_3band):
    for bad in ("0", "-1"):
        result = _run(
            imagephase_cmd, str(target_3band),
            "--phases", bad,
            "--phase-brushes", str(target_3band),
            "--start-canvas", str(target_3band),
            timeout=15,
        )
        assert result.returncode != 0, (
            f"--phases {bad} should be rejected; stderr:\n{result.stderr}"
        )


def test_required_start_canvas(imagephase_cmd, target_3band, brush_solid, tmp_path):
    """--phases set without --start-canvas → clean error before pygame inits."""
    p = brush_solid("#FF0000")
    result = _run(
        imagephase_cmd, str(target_3band),
        "--phases", "1",
        "--phase-brushes", str(p),
        # NO --start-canvas
        "-g", "1", "-c", "1", "-j", "1",
        "--save-on-exit", "--close-on-exit", "-p", "nocanvas",
        "-d", str(tmp_path),
        timeout=20,
    )
    assert result.returncode != 0
    assert "--start-canvas" in result.stderr or "start-canvas" in result.stderr


def test_brushes_count_mismatch(
    imagephase_cmd, target_3band, brush_solid, solid_canvas, tmp_path,
):
    """--phases 3 with only 2 brushes → clean error."""
    canvas = solid_canvas((64, 192), "#000000")
    p1 = brush_solid("#FF0000")
    p2 = brush_solid("#00FF00")
    result = _run(
        imagephase_cmd, str(target_3band),
        "--phases", "3",
        "--phase-brushes", str(p1), str(p2),  # only 2
        "--start-canvas", str(canvas),
        "-g", "1", "-c", "1", "-j", "1",
        "--save-on-exit", "--close-on-exit", "-p", "mismatch",
        "-d", str(tmp_path),
        timeout=20,
    )
    assert result.returncode != 0
    assert "phases is 3" in result.stderr or "brush groups" in result.stderr


def test_phase_brushes_and_multi_mutually_exclusive(
    imagephase_cmd, target_3band, brush_solid, solid_canvas, tmp_path,
):
    canvas = solid_canvas((64, 192), "#000000")
    p1 = brush_solid("#FF0000")
    p2 = brush_solid("#00FF00")
    result = _run(
        imagephase_cmd, str(target_3band),
        "--phases", "2",
        "--phase-brushes", str(p1), str(p2),
        "--phase-brushes-multi", f"{p1} ; {p2}",
        "--start-canvas", str(canvas),
        "-g", "1", "-c", "1", "-j", "1",
        "--save-on-exit", "--close-on-exit", "-p", "both",
        "-d", str(tmp_path),
        timeout=20,
    )
    assert result.returncode != 0
    assert "mutually exclusive" in result.stderr


def test_radius_count_mismatch(
    imagephase_cmd, target_3band, brush_solid, solid_canvas, tmp_path,
):
    canvas = solid_canvas((64, 192), "#000000")
    p1 = brush_solid("#FF0000")
    p2 = brush_solid("#00FF00")
    result = _run(
        imagephase_cmd, str(target_3band),
        "--phases", "2",
        "--phase-brushes", str(p1), str(p2),
        "--phase-max-radius", "30",  # only 1
        "--start-canvas", str(canvas),
        "-g", "1", "-c", "1", "-j", "1",
        "--save-on-exit", "--close-on-exit", "-p", "rmismatch",
        "-d", str(tmp_path),
        timeout=20,
    )
    assert result.returncode != 0
    assert "--phase-max-radius" in result.stderr or "values" in result.stderr


# ---------- Backward compat ----------

def test_imagemutate_baseline_still_passes(imagemutate_cmd, target_64x64,
                                             baseline_hash, tmp_path):
    """F3 adds imagephase but must not regress imagemutate. Re-run the F0
    baseline command via imagemutate; assert baseline hash unchanged."""
    import hashlib
    result = subprocess.run(
        imagemutate_cmd + [
            str(target_64x64),
            "--seed", "42", "-g", "100", "-c", "10", "-j", "1",
            "--save-on-exit", "--close-on-exit", "-p", "compat",
            "-d", str(tmp_path),
        ],
        capture_output=True, text=True, timeout=60,
    )
    assert result.returncode == 0, result.stderr
    out = next(tmp_path.glob("compat-*.png"))
    actual = hashlib.sha256(out.read_bytes()).hexdigest()
    assert actual == baseline_hash, (
        f"F3 broke imagemutate's baseline. expected={baseline_hash} actual={actual}"
    )
