"""Regression tests for the `--brush-mode {texture,shape}` feature.

`texture` (default) preserves the legacy behavior — random sub-rect of the
brush image is sampled and scaled to fit the polygon shape. `shape` forces
the full brush image to be sampled every stroke; downstream (canvas.py)
skips the polygon-mask blend so the brush image's own alpha channel drives
the stroke silhouette.

These tests monkeypatch `imagelab.rng.integers` to capture (low, high)
call args, and monkeypatch `get_random_clip_rect` to observe the
sample_size the draw path hands it. No pygame rendering, no image
fixtures, no conftest.py.
"""
import pytest

from imagelab import rng
from imagelab import drawing


class _FakeSurface:
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


class _FakeBrush:
    """Stand-in for a pygame Surface just enough for the sample_size
    logic in drawing.py. Reports a fixed size; get_rect returns a
    minimal object compatible with get_random_clip_rect."""
    def __init__(self, size=(64, 64)):
        self._size = size

    def get_size(self):
        return self._size

    def get_rect(self):
        return _FakeSurface._Rect()


class _RngRecorder:
    """Replaces rng.integers to record every (low, high) call and return
    a deterministic value from the requested range."""

    def __init__(self):
        self.calls = []

    def __call__(self, low, high, *args, **kwargs):
        self.calls.append((int(low), int(high)))
        return int(low)


class _ClipRectRecorder:
    """Replaces get_random_clip_rect to observe the sample_size passed in.
    Returns a plausible 4-tuple so the draw path continues without pygame."""

    def __init__(self):
        self.sample_sizes = []

    def __call__(self, rect, w, h, constrain=False):
        self.sample_sizes.append((int(w), int(h)))
        return (0, 0, int(w), int(h))


@pytest.fixture
def observe_rng_and_clip(monkeypatch):
    rng_rec = _RngRecorder()
    clip_rec = _ClipRectRecorder()
    monkeypatch.setattr(rng, "integers", rng_rec)
    monkeypatch.setattr(rng, "choice", lambda seq, size=None: seq[0])
    monkeypatch.setattr(drawing, "get_random_clip_rect", clip_rec)

    # Stub the CanvasAction execution — we only care about the params dict.
    class _FakeAction:
        opcode = "s"

        def __init__(self, params):
            self.params = params

        def run(self, canvas, origin=(0, 0)):
            return None

    monkeypatch.setattr(drawing, "CanvasActionDrawShape", _FakeAction)
    return rng_rec, clip_rec


def _find_sample_call(rng_rec, brush_size):
    """Locate the rng.integers call that resolved the random sub-rect size.
    The 'texture' path calls rng.integers(radius*2, max_sample_size) as its
    LAST call after the radius/edges/rotation/pos calls.
    """
    # In texture mode the sample_size call has both operands larger than the
    # radius integer draws (which are bounded above by max_radius) — grab the
    # last one, which is the sample_size draw right before get_random_clip_rect.
    return rng_rec.calls[-1] if rng_rec.calls else None


def test_texture_mode_random_sub_rect(observe_rng_and_clip):
    """Default mode: sample_size is drawn from [radius*2, min(brush_size)].

    Under our rng monkeypatch every rng.integers(low, high) returns `low`,
    so radius resolves to 1, radius*2 to 2, and the sample_size rng call
    fires as rng.integers(2, 64). Assertion pins the (radius*2, brush_size)
    pattern, not specific numbers.
    """
    rng_rec, _ = observe_rng_and_clip
    drawing.draw_random_polygon(
        _FakeSurface(),
        max_radius=20,
        brush_images=[_FakeBrush((64, 64))],
        brush_mode='texture',
    )
    # Last rng.integers call is the sample_size draw: (radius*2, brush_size).
    # radius=1 (from monkeypatched rng returning low), so (2, 64).
    # Sample_size rng call fires as rng.integers(radius*2, brush_size)
    # = (2, 64). Note: (1, 361) brush_rotation call runs afterward.
    assert (2, 64) in rng_rec.calls, (
        f"expected (2, 64) rng call, got {rng_rec.calls}"
    )


def test_texture_mode_is_default(observe_rng_and_clip):
    """Omitting brush_mode kwarg defaults to 'texture' — sample_size still
    goes through rng.integers."""
    rng_rec, _ = observe_rng_and_clip
    drawing.draw_random_polygon(
        _FakeSurface(),
        max_radius=20,
        brush_images=[_FakeBrush((64, 64))],
    )
    assert (2, 64) in rng_rec.calls, (
        f"expected (2, 64) rng call in default mode, got {rng_rec.calls}"
    )


def test_shape_mode_full_brush_sample(observe_rng_and_clip):
    """shape mode: sample_rect covers full brush, no rng draw for sample,
    no get_random_clip_rect call. Sample rect is fixed (0, 0, brush, brush)."""
    rng_rec, clip_rec = observe_rng_and_clip
    drawing.draw_random_polygon(
        _FakeSurface(),
        max_radius=20,
        brush_images=[_FakeBrush((64, 64))],
        brush_mode='shape',
    )
    # No rng.integers call for the sample_size — the (2, 64) pattern
    # must be absent (brush_rotation (1, 361) still fires).
    assert (2, 64) not in rng_rec.calls, (
        f"texture-mode sample_size rng call fired in shape mode: "
        f"{rng_rec.calls}"
    )
    # No get_random_clip_rect call in shape mode — sample rect is set
    # directly to avoid the constrain=True degenerate-range crash.
    assert clip_rec.sample_sizes == [], (
        f"shape mode called get_random_clip_rect: {clip_rec.sample_sizes}"
    )


def test_shape_mode_full_brush_sample_small_radius(observe_rng_and_clip):
    """shape mode with tiny radius (r=3): sample rect still full brush,
    still no get_random_clip_rect call."""
    _, clip_rec = observe_rng_and_clip
    drawing.draw_random_polygon(
        _FakeSurface(),
        max_radius=3,
        brush_images=[_FakeBrush((128, 128))],
        brush_mode='shape',
    )
    assert clip_rec.sample_sizes == []


def test_circle_shape_mode_full_brush_sample(observe_rng_and_clip):
    """Same skip-clip-rect behavior for draw_random_circle."""
    _, clip_rec = observe_rng_and_clip
    drawing.draw_random_circle(
        _FakeSurface(),
        max_radius=20,
        brush_images=[_FakeBrush((64, 64))],
        brush_mode='shape',
    )
    assert clip_rec.sample_sizes == []


def test_shape_mode_writes_brush_mode_into_action_params(monkeypatch):
    """The `brush_mode` string must propagate into the CanvasAction params
    dict so the downstream canvas render honors it."""
    captured = {}

    monkeypatch.setattr(
        rng, "integers", lambda low, high, *a, **kw: int(low)
    )
    monkeypatch.setattr(rng, "choice", lambda seq, size=None: seq[0])
    monkeypatch.setattr(
        drawing, "get_random_clip_rect",
        lambda rect, w, h, constrain=False: (0, 0, int(w), int(h)),
    )

    class _CaptureAction:
        opcode = "s"

        def __init__(self, params):
            captured['params'] = params

        def run(self, canvas, origin=(0, 0)):
            return None

    monkeypatch.setattr(drawing, "CanvasActionDrawShape", _CaptureAction)

    drawing.draw_random_polygon(
        _FakeSurface(),
        max_radius=20,
        brush_images=[_FakeBrush((64, 64))],
        brush_mode='shape',
    )
    assert captured['params'].get('brush_mode') == 'shape'


def test_texture_mode_writes_brush_mode_into_action_params(monkeypatch):
    """Default mode also propagates 'texture' explicitly — canvas render
    can rely on the key always being present."""
    captured = {}

    monkeypatch.setattr(
        rng, "integers", lambda low, high, *a, **kw: int(low)
    )
    monkeypatch.setattr(rng, "choice", lambda seq, size=None: seq[0])
    monkeypatch.setattr(
        drawing, "get_random_clip_rect",
        lambda rect, w, h, constrain=False: (0, 0, int(w), int(h)),
    )

    class _CaptureAction:
        opcode = "s"

        def __init__(self, params):
            captured['params'] = params

        def run(self, canvas, origin=(0, 0)):
            return None

    monkeypatch.setattr(drawing, "CanvasActionDrawShape", _CaptureAction)

    drawing.draw_random_polygon(
        _FakeSurface(),
        max_radius=20,
        brush_images=[_FakeBrush((64, 64))],
    )
    assert captured['params'].get('brush_mode') == 'texture'


def test_shape_mode_skips_get_random_clip_rect(monkeypatch):
    """Regression sentinel for the integration bug that hit the dogwood
    horses test: get_random_clip_rect(constrain=True) calls rng.integers(0, 0)
    when sample_size == brush_size, which numpy rejects. The shape-mode
    branch must set brush_sample_rect directly and never call
    get_random_clip_rect.

    If a future edit re-introduces the call inside the shape-mode branch,
    the raising monkeypatch below fires and this test fails immediately.
    """
    captured = {}

    def _raising_clip_rect(*args, **kwargs):
        raise AssertionError(
            "shape mode must NOT call get_random_clip_rect — the "
            "constrain=True path fails when clip == rect. "
            f"got args={args!r} kwargs={kwargs!r}"
        )

    monkeypatch.setattr(
        rng, "integers", lambda low, high, *a, **kw: int(low)
    )
    monkeypatch.setattr(rng, "choice", lambda seq, size=None: seq[0])
    monkeypatch.setattr(drawing, "get_random_clip_rect", _raising_clip_rect)

    class _CaptureAction:
        opcode = "s"

        def __init__(self, params):
            captured['params'] = params

        def run(self, canvas, origin=(0, 0)):
            return None

    monkeypatch.setattr(drawing, "CanvasActionDrawShape", _CaptureAction)

    # polygon path
    drawing.draw_random_polygon(
        _FakeSurface(),
        max_radius=20,
        brush_images=[_FakeBrush((64, 64))],
        brush_mode='shape',
    )
    assert captured['params'].get('brush_sample_rect') == (0, 0, 64, 64), (
        f"expected (0, 0, 64, 64) fixed sample rect, "
        f"got {captured['params'].get('brush_sample_rect')!r}"
    )

    # circle path — same bug, same fix
    captured.clear()
    drawing.draw_random_circle(
        _FakeSurface(),
        max_radius=20,
        brush_images=[_FakeBrush((64, 64))],
        brush_mode='shape',
    )
    assert captured['params'].get('brush_sample_rect') == (0, 0, 64, 64), (
        f"expected (0, 0, 64, 64) fixed sample rect, "
        f"got {captured['params'].get('brush_sample_rect')!r}"
    )
