"""Tests for the phase HUD overlay (F4).

Approach: render the phase bar directly on a mock surface and verify the
text region changes per phase advance. We don't try to OCR — just hash
the relevant pixel region; different phase ⇒ different rendered text ⇒
different hash.
"""
import hashlib
import os

from PIL import Image
import pytest

import pygame
import pygame.freetype

from apps.imagemutate import HUD_FONT_SIZE, HUD_PADDING, STATS_FONT_PATH
from apps.imagephase import App as PhaseApp
from imagelab.phasing import PhaseState


@pytest.fixture
def stub_app(tmp_path):
    """Build a minimal imagephase App with a configured phase_state and a
    real pygame font for rendering. Avoids running the full pipeline."""
    pygame.init()
    pygame.display.set_mode((1, 1))  # required for font init on some platforms

    # Use the bundled font path that imagemutate uses, via the app's normal
    # initialize_display path — but we skip a bunch of setup. Set up just
    # enough to call render_hud.
    app = PhaseApp(options={
        'verbose': False,
        'workers': 1,
        'radius': 40,
    })
    # Manually populate the bits render_hud reads.
    app.hud_font = pygame.freetype.Font(
        os.path.normpath(STATS_FONT_PATH), HUD_FONT_SIZE,
    )
    app.phase_state = PhaseState(
        phase_brushes=[["black.png"], ["red.png"], ["blue.png"]],
        phase_max_radius=[100, 60, 30],
        phase_min_radius=[8, 6, 4],
        phase_max_gens=[float('inf')] * 3,
        plateau_window=10,
        plateau_delta=0.005,
    )
    yield app
    pygame.quit()


def _hash_phase_bar_region(surface):
    """Hash the pixel rectangle where the phase bar should land.
    Layout (from imagephase.render_hud / _render_phase_bar):
      bottom area: 2-line imagemutate HUD (height = 2*line_h + padding)
      above that: 1-line phase bar (height = line_h)
    """
    w, h = surface.get_size()
    line_h = HUD_FONT_SIZE + HUD_PADDING * 2
    existing_hud_h = line_h * 2 + HUD_PADDING
    phase_bar_top = h - existing_hud_h - line_h

    # Extract the strip and hash its pixels.
    strip = pygame.Surface((w, line_h))
    strip.blit(surface, (0, 0), (0, phase_bar_top, w, line_h))
    return hashlib.sha256(pygame.image.tostring(strip, "RGB")).hexdigest()


def test_phase_bar_changes_per_advance(stub_app):
    """Rendering at phase 0, then advancing and rendering again, should
    yield a different pixel hash for the phase-bar region. Different phase
    index ⇒ different displayed text ⇒ different hash."""
    # Need to do a real-display surface so .convert and renders work.
    # Use a large enough surface to contain HUD + a phase bar.
    surface = pygame.display.set_mode((800, 200))

    surface.fill((40, 40, 40))
    # stash extra phase advance state needed by parent's render_hud
    stub_app._evolution_complete = False
    stub_app.current_generation = 5
    stub_app.options['gen_stop'] = 1000
    stub_app._current_radius = 40
    stub_app._current_children = 10
    stub_app.current_child = 0

    stub_app.render_hud(surface)
    hash_phase0 = _hash_phase_bar_region(surface)

    # Advance to phase 1 and re-render.
    stub_app.phase_state.advance()
    surface.fill((40, 40, 40))
    stub_app.render_hud(surface)
    hash_phase1 = _hash_phase_bar_region(surface)

    assert hash_phase0 != hash_phase1, (
        "phase-bar region hash unchanged between phase 0 and phase 1 — "
        "phase indicator may not be rendering or refreshing"
    )

    # Advance to phase 2 — different again.
    stub_app.phase_state.advance()
    surface.fill((40, 40, 40))
    stub_app.render_hud(surface)
    hash_phase2 = _hash_phase_bar_region(surface)
    assert hash_phase2 != hash_phase0
    assert hash_phase2 != hash_phase1


def test_phase_bar_omitted_when_phase_state_none(stub_app):
    """If phase_state is None (shouldn't happen in normal imagephase use, but
    guard anyway), _render_phase_bar is a no-op and doesn't crash."""
    surface = pygame.display.set_mode((800, 200))
    surface.fill((40, 40, 40))
    stub_app.phase_state = None
    stub_app._evolution_complete = False
    stub_app.current_generation = 5
    stub_app.options['gen_stop'] = 1000
    stub_app._current_radius = 40
    stub_app._current_children = 10
    stub_app.current_child = 0
    # Should not raise.
    stub_app.render_hud(surface)
