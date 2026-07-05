"""Regression tests for min_radius enforcement in the draw path.

Prior to this fix, `get_random_polygon`, `get_random_circle`, and
`get_random_word` sampled radius uniformly from [1, max_radius], ignoring
the `min_radius` option threaded down from `imagemutate`. That made phase
schedules with max == min still emit tiny (radius-1) strokes.

These tests monkeypatch `imagelab.rng.integers` to capture the exact
bounds the geometry helpers pass to it, verifying the correct low bound
is applied — no image rendering or fixtures required, so this file has
no conftest.py dependencies.
"""
import types

import pytest

from imagelab import rng
from imagelab.geometry import (
    get_random_polygon,
    get_random_circle,
    get_random_word,
)


class _FakeSurface:
    """Minimal stand-in for a pygame Surface — only needs get_rect()."""

    class _Rect:
        size = (256, 256)

        @property
        def width(self):
            return 256

        @property
        def height(self):
            return 256

        left = 0
        top = 0
        right = 256
        bottom = 256

    def get_rect(self):
        return self._Rect()


class _RadiusCaptureRNG:
    """Replaces rng.integers to record every (low, high) call and return
    a deterministic value from the requested range."""

    def __init__(self):
        self.calls = []

    def __call__(self, low, high, *args, **kwargs):
        self.calls.append((int(low), int(high)))
        # deterministic pick: always return low
        return int(low)


@pytest.fixture
def capture_rng(monkeypatch):
    fake = _RadiusCaptureRNG()
    monkeypatch.setattr(rng, "integers", fake)
    # Provide a stub choice() so word/color helpers don't crash under the
    # monkeypatch.
    monkeypatch.setattr(rng, "choice", lambda seq, size=None: seq[0] if seq else None)
    return fake


def _polygon_radius_call(capture):
    """Return the (low, high) call that corresponds to the radius draw.

    The first rng.integers call inside get_random_polygon is always the
    radius sampler (line: `radius = rng.integers(low, max_radius + 1)`).
    """
    assert capture.calls, "expected at least one rng.integers call"
    return capture.calls[0]


def test_polygon_respects_min_radius(capture_rng):
    """max=40, min=30 → rng.integers called with (30, 41)."""
    get_random_polygon(_FakeSurface(), max_radius=40, min_radius=30)
    assert _polygon_radius_call(capture_rng) == (30, 41)


def test_polygon_default_min_radius_is_1(capture_rng):
    """Absent min_radius → historical behavior: rng.integers(1, max+1)."""
    get_random_polygon(_FakeSurface(), max_radius=40)
    assert _polygon_radius_call(capture_rng) == (1, 41)


def test_polygon_min_equal_max_collapses_to_fixed_size(capture_rng):
    """max=min=6 → rng.integers(6, 7) → always 6. Fixed-size phase."""
    get_random_polygon(_FakeSurface(), max_radius=6, min_radius=6)
    assert _polygon_radius_call(capture_rng) == (6, 7)


def test_polygon_min_exceeds_max_is_clamped(capture_rng):
    """Defensive: if caller passes min > max (e.g. adaptive-cheat shrinks
    max below min), low is clamped to max so rng.integers never gets an
    empty range."""
    get_random_polygon(_FakeSurface(), max_radius=10, min_radius=50)
    low, high = _polygon_radius_call(capture_rng)
    assert (low, high) == (10, 11)


def test_circle_respects_min_radius(capture_rng):
    """Circle uses half-open [low, max_radius) convention historically;
    the fix preserves that upper bound, only changing the lower."""
    get_random_circle(_FakeSurface(), max_radius=40, min_radius=30)
    assert _polygon_radius_call(capture_rng) == (30, 40)


def test_circle_default_min_radius_is_1(capture_rng):
    get_random_circle(_FakeSurface(), max_radius=40)
    assert _polygon_radius_call(capture_rng) == (1, 40)


def test_word_respects_min_radius(capture_rng):
    """Word helper has same convention as polygon: [low, max_radius+1)."""
    get_random_word(
        _FakeSurface(), words=["a"], max_radius=40, min_radius=30
    )
    # First call is rng.choice via monkeypatch (word pick) — but we replaced
    # choice separately, so it doesn't hit integers. First integers call is
    # the radius draw.
    assert _polygon_radius_call(capture_rng) == (30, 41)
