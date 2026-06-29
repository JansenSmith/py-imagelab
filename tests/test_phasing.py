"""Unit tests for src/imagelab/phasing.py — pure-function library that
the imagephase app builds on. Tested in isolation (no pygame, no IO)."""
from collections import deque

import pytest

from imagelab.phasing import PhaseState, is_plateau


# ---------- is_plateau pure function ----------

def test_plateau_full_no_improvement():
    """All-identical history → plateau."""
    history = [50.0] * 50
    assert is_plateau(history, delta=0.005) is True


def test_plateau_full_with_improvement():
    """Monotonic-rising history with drift > delta → not a plateau."""
    history = [50.0 + 0.001 * i for i in range(50)]  # drift = 0.049
    assert is_plateau(history, delta=0.005) is False


def test_plateau_partial_history():
    """Window shorter than 2 → returns False (insufficient data)."""
    assert is_plateau([], delta=0.005) is False
    assert is_plateau([42.0], delta=0.005) is False


def test_plateau_transient_spike():
    """Mid-window spike that doesn't last — terminal ≈ initial, but spread is
    large. variance co-condition rules it out as a plateau."""
    history = [50.0] * 20 + [55.0] + [50.0] * 19  # spike of +5 in the middle
    # drift = 0  (within delta)
    # spread = 5.0  (way above 2*delta=0.01)
    assert is_plateau(history, delta=0.005) is False


def test_plateau_exact_threshold_below():
    """drift == delta - epsilon AND spread < 2*delta → plateau."""
    delta = 0.005
    # drift = 0.004 < delta; spread = 0.004 < 2*delta = 0.010
    history = [50.0] * 49 + [50.004]
    assert is_plateau(history, delta=delta) is True


def test_plateau_exact_threshold_above():
    """drift > delta → not a plateau."""
    delta = 0.005
    history = [50.0] * 49 + [50.01]  # drift = 0.01 > delta
    assert is_plateau(history, delta=delta) is False


def test_plateau_spread_just_above_2delta():
    """spread > 2*delta blocks plateau even if drift is fine."""
    delta = 0.005
    # drift = 0, but spread = 0.02 > 2*delta = 0.01
    history = [50.0, 50.02] + [50.0] * 48
    assert is_plateau(history, delta=delta) is False


# ---------- PhaseState construction ----------

def _basic_state(phases=3, window=10):
    return PhaseState(
        phase_brushes=[[f"phase_{i}.png"] for i in range(phases)],
        phase_max_radius=[100 - 30 * i for i in range(phases)],
        phase_min_radius=[10 - 2 * i for i in range(phases)],
        phase_max_gens=[float('inf')] * phases,
        plateau_window=window,
        plateau_delta=0.005,
    )


def test_phasestate_validates_length_match():
    """Mismatched array lengths → ValueError at construction."""
    with pytest.raises(ValueError, match="phase array length mismatch"):
        PhaseState(
            phase_brushes=[["a.png"], ["b.png"], ["c.png"]],
            phase_max_radius=[100, 50],  # only 2!
            phase_min_radius=[10, 5, 2],
            phase_max_gens=[float('inf')] * 3,
        )


def test_phasestate_validates_at_least_one_phase():
    with pytest.raises(ValueError, match="at least one phase"):
        PhaseState(
            phase_brushes=[],
            phase_max_radius=[],
            phase_min_radius=[],
            phase_max_gens=[],
        )


def test_phasestate_initial_state():
    s = _basic_state()
    assert s.current_phase == 0
    assert s.gens_in_current == 0
    assert s.total_phases == 3
    assert s.is_last_phase is False
    assert s.current_brushes() == ["phase_0.png"]
    assert s.current_max_radius() == 100
    assert s.current_min_radius() == 10


# ---------- record_gen + should_advance ----------

def test_no_advance_before_window_full():
    """Before plateau_window samples have been recorded, no plateau-advance."""
    s = _basic_state(window=10)
    for _ in range(9):
        s.record_gen(50.0)  # plateau-like data but not enough samples yet
    advance, reason = s.should_advance()
    assert advance is False
    assert reason == ''


def test_advance_on_plateau_with_full_window():
    s = _basic_state(window=10)
    for _ in range(10):
        s.record_gen(50.0)  # fully stagnant
    advance, reason = s.should_advance()
    assert advance is True
    assert reason == 'plateau'


def test_no_advance_with_improvement():
    s = _basic_state(window=10)
    for i in range(10):
        s.record_gen(50.0 + 0.5 * i)  # 4.5%-point drift across window
    advance, reason = s.should_advance()
    assert advance is False


def test_advance_on_max_gens_cap():
    s = PhaseState(
        phase_brushes=[["a.png"], ["b.png"]],
        phase_max_radius=[100, 50],
        phase_min_radius=[10, 5],
        phase_max_gens=[20, float('inf')],
        plateau_window=200,
        plateau_delta=0.005,
    )
    for _ in range(20):
        s.record_gen(50.0)
    advance, reason = s.should_advance()
    assert advance is True
    assert reason == 'max_gens'


def test_max_gens_takes_precedence_over_plateau():
    """If both max_gens and plateau would fire on the same gen, max_gens wins.
    (Practically they say the same thing — advance — but reason should be
    'max_gens' for clearer logging.)"""
    s = PhaseState(
        phase_brushes=[["a.png"], ["b.png"]],
        phase_max_radius=[100, 50],
        phase_min_radius=[10, 5],
        phase_max_gens=[10, float('inf')],
        plateau_window=10,
        plateau_delta=0.005,
    )
    for _ in range(10):
        s.record_gen(50.0)
    advance, reason = s.should_advance()
    assert advance is True
    assert reason == 'max_gens'  # not 'plateau'


# ---------- advance() behavior ----------

def test_advance_resets_state():
    s = _basic_state()
    for _ in range(5):
        s.record_gen(50.0)
    assert s.gens_in_current == 5
    assert len(s.match_history) == 5

    s.advance()

    assert s.current_phase == 1
    assert s.gens_in_current == 0
    assert len(s.match_history) == 0
    assert s.current_brushes() == ["phase_1.png"]
    assert s.current_max_radius() == 70
    assert s.current_min_radius() == 8


def test_advance_progresses_through_all_phases():
    s = _basic_state(phases=3)
    s.advance()
    assert s.current_phase == 1
    assert s.is_last_phase is False
    s.advance()
    assert s.current_phase == 2
    assert s.is_last_phase is True


def test_advance_past_last_phase_raises():
    s = _basic_state(phases=2)
    s.advance()  # → phase 1, which is last
    assert s.is_last_phase is True
    with pytest.raises(IndexError, match="cannot advance past last phase"):
        s.advance()


def test_single_phase_is_already_last():
    s = _basic_state(phases=1)
    assert s.is_last_phase is True
    with pytest.raises(IndexError):
        s.advance()
