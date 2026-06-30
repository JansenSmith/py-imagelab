"""imagephase — phased painting app (sibling to imagemutate).

Paint with one brush per phase; advance to the next phase on plateau-then-
switch. Different paradigm from imagemutate's uniform-random brush selection.

Architecture: subclasses imagemutate's App and overrides only the methods
that need phase-aware behavior. The shared library code (canvas, drawing,
mutation, geometry, compare) is inherited unchanged. The phase state
machine + plateau detector live in imagelab.phasing as pure functions for
testability.

Required CLI:
    --phases N
    --phase-brushes p1.png p2.png ... pN.png        (or --phase-brushes-multi)
    --start-canvas seed.png

Phase mode REQUIRES an explicit --start-canvas. Without it, phase 0 would
paint over the default solid-bg, which is almost never what the artist
wants for FDM-faithful work.

By default imagephase sets --brush-alpha 255 and --brush-blend-mode opaque
(FDM-faithful: overlapping shapes don't darken). Both are overridable.
"""
import logging
import os
import sys

import pygame

from apps.imagemutate import (
    App as MutateApp,
    HUD_FONT_SIZE,
    HUD_PADDING,
)
from imagelab import mutation
from imagelab.compare import get_match_percentage, match_score
from imagelab.phasing import PhaseState


log = logging.getLogger("imagelab.phase")


class App(MutateApp):
    """Phased-painting App. Extends imagemutate's App."""

    phase_state = None
    phase_brush_surfaces = None
    _final_phase_complete = False

    def __init__(self, options=None):
        super().__init__(options)
        # FDM-faithful defaults — apply AFTER super so they survive set_options.
        # Override is allowed but discouraged; warn loudly on override.
        if self.options:
            if self.options.get('brush_alpha') is None:
                self.options['brush_alpha'] = 255
            elif self.options.get('brush_alpha') != 255:
                print(
                    f"warning: imagephase running with --brush-alpha "
                    f"{self.options['brush_alpha']} (not 255). Phase mode "
                    f"is intended for FDM-faithful opaque painting; non-255 "
                    f"alpha will produce semi-transparent overlap.",
                    file=sys.stderr,
                )
            blend = self.options.get('brush_blend_mode')
            if blend in (None, 'auto'):
                self.options['brush_blend_mode'] = 'opaque'
            elif blend != 'opaque':
                print(
                    f"warning: imagephase running with --brush-blend-mode "
                    f"{blend} (not 'opaque'). Phase mode's intent is "
                    f"top-brush-wins composite.",
                    file=sys.stderr,
                )

    def initialize_image_settings(self):
        """Override to also load phase brush sets + build PhaseState."""
        super().initialize_image_settings()

        if self.start_canvas_surface is None:
            print(
                "error: imagephase requires --start-canvas (-s). "
                "Phase mode needs an explicit initial canvas.",
                file=sys.stderr,
            )
            sys.exit(1)

        phase_brush_paths = self._resolve_phase_brushes()
        n_phases = len(phase_brush_paths)

        # Load brush surfaces per phase.
        self.phase_brush_surfaces = []
        for phase_idx, paths in enumerate(phase_brush_paths):
            surfaces = []
            for p in paths:
                try:
                    surf = pygame.image.load(p)
                except (FileNotFoundError, pygame.error) as exc:
                    print(
                        f"error: could not load phase-{phase_idx+1} brush "
                        f"{p!r}: {exc}",
                        file=sys.stderr,
                    )
                    sys.exit(1)
                surf.convert(self.bit_depth)
                surfaces.append(surf)
            self.phase_brush_surfaces.append(surfaces)

        # Resolve per-phase max/min radius + max_gens (with sensible defaults).
        max_radii = self._resolve_phase_array(
            'phase_max_radius', n_phases, lambda i: 40,
        )
        min_radii = self._resolve_phase_array(
            'phase_min_radius', n_phases, lambda i: 5,
        )
        max_gens = self._resolve_phase_array(
            'phase_max_gens', n_phases, lambda i: float('inf'),
        )

        self.phase_state = PhaseState(
            phase_brushes=phase_brush_paths,
            phase_max_radius=max_radii,
            phase_min_radius=min_radii,
            phase_max_gens=max_gens,
            plateau_window=self.options.get('plateau_window') or 200,
            plateau_delta=self.options.get('plateau_delta') or 0.005,
        )

        # Bind the active brush set to phase 0 so the parent's evolution
        # loop sees the right brushes from the start.
        self.brush_surfaces = self.phase_brush_surfaces[0]

        # Run-start summary (always emitted; matches plan §10.4 visibility).
        print(
            f"imagephase: {n_phases} phases, plateau_window="
            f"{self.phase_state.plateau_window}, plateau_delta="
            f"{self.phase_state.plateau_delta:g}",
            file=sys.stderr,
        )
        for i, paths in enumerate(phase_brush_paths):
            names = ", ".join(os.path.basename(str(p)) for p in paths)
            cap = max_gens[i]
            cap_str = "inf" if cap == float('inf') else str(int(cap))
            print(
                f"  phase {i+1}/{n_phases}: brushes=[{names}] "
                f"max_radius={max_radii[i]} min_radius={min_radii[i]} "
                f"max_gens={cap_str}",
                file=sys.stderr,
            )

    def _resolve_phase_brushes(self):
        """Return list[list[Path]] from --phase-brushes (one per phase) or
        --phase-brushes-multi (semicolon-separated multi-brush groups)."""
        single = self.options.get('phase_brushes')
        multi = self.options.get('phase_brushes_multi')
        n_phases = self.options.get('phases', 0)

        if single and multi:
            print(
                "error: --phase-brushes and --phase-brushes-multi are "
                "mutually exclusive",
                file=sys.stderr,
            )
            sys.exit(1)
        if not single and not multi:
            print(
                "error: imagephase requires --phase-brushes OR "
                "--phase-brushes-multi",
                file=sys.stderr,
            )
            sys.exit(1)

        if multi:
            groups = [g.strip() for g in multi.split(';')]
            parsed = [
                [p.strip() for p in g.split() if p.strip()]
                for g in groups if g.strip()
            ]
        else:
            parsed = [[p] for p in single]

        if n_phases and len(parsed) != n_phases:
            print(
                f"error: --phases is {n_phases} but {len(parsed)} brush "
                f"groups were supplied",
                file=sys.stderr,
            )
            sys.exit(1)
        return parsed

    def _resolve_phase_array(self, opt_key, n_phases, default_fn):
        vals = self.options.get(opt_key)
        if vals is None:
            return [default_fn(i) for i in range(n_phases)]
        if len(vals) != n_phases:
            flag = '--' + opt_key.replace('_', '-')
            print(
                f"error: {flag} has {len(vals)} values "
                f"but --phases is {n_phases}",
                file=sys.stderr,
            )
            sys.exit(1)
        return list(vals)

    def handle_evolution_tick(self, tick):
        """Override: swap brush set + radius to current phase's values
        before delegating to parent; record the gen's match-% and
        check for phase advance after."""
        # Bind current phase's brushes + radii to the attributes/options
        # the parent's handle_evolution_tick reads.
        self.brush_surfaces = self.phase_brush_surfaces[
            self.phase_state.current_phase
        ]
        self.options['radius'] = self.phase_state.current_max_radius()
        self.options['min_radius'] = self.phase_state.current_min_radius()

        super().handle_evolution_tick(tick)

        # Record match-% after the winning child was applied.
        score = match_score(self.target_surface, self.canvas.surface)
        pct = get_match_percentage(score)
        self.phase_state.record_gen(pct)

        # Plateau / cap check.
        should, reason = self.phase_state.should_advance()
        if not should:
            return

        if self.phase_state.is_last_phase:
            log.info(
                "imagephase: final phase complete (reason=%s, gens=%d, match=%.3f%%)",
                reason, self.phase_state.gens_in_current, pct,
            )
            print(
                f"PHASE final reason={reason} gens={self.phase_state.gens_in_current} "
                f"match-end={pct:.3f}%",
                file=sys.stderr,
            )
            self._final_phase_complete = True
            return

        old_phase = self.phase_state.current_phase
        gens_used = self.phase_state.gens_in_current
        self.phase_state.advance()
        log.info(
            "PHASE advance %d→%d reason=%s gens=%d match=%.3f%%",
            old_phase + 1, self.phase_state.current_phase + 1, reason,
            gens_used, pct,
        )
        print(
            f"PHASE advance {old_phase+1}→{self.phase_state.current_phase+1} "
            f"reason={reason} gens={gens_used} match-end={pct:.3f}%",
            file=sys.stderr,
        )

    def evolution_complete(self):
        """Override: phase completion overrides the gen/seconds/match-% checks."""
        if self._final_phase_complete:
            return True
        return super().evolution_complete()

    def render_hud(self, surface):
        """Override: draw a phase-info bar above imagemutate's existing HUD bar.

        Layout:
            ...image...
            [phase: 2/3 | brush: ... | gens-in-phase: 137 | plateau: 0.003/0.010]  ← phase bar
            [gen: 137/inf | child: 5/10 | maxR: 25                              ]  ← imagemutate's status line
            [Esc: quit | Space: stats | Enter/S: snapshot | A: save JSON | ...   ]  ← imagemutate's hotkeys line
        """
        # Draw imagemutate's existing 2-line HUD at the bottom first.
        super().render_hud(surface)
        # Then draw our phase bar above it.
        self._render_phase_bar(surface)

    def _render_phase_bar(self, surface):
        """Draw the phase-progress bar above imagemutate's HUD."""
        if self.hud_font is None or self.phase_state is None:
            return

        ps = self.phase_state
        screen_w, screen_h = surface.get_size()
        line_h = HUD_FONT_SIZE + HUD_PADDING * 2
        existing_hud_h = line_h * 2 + HUD_PADDING  # 2-line bar from parent

        bar = pygame.Surface((screen_w, line_h), pygame.SRCALPHA)
        bar.fill((0, 0, 0, 160))

        brush_paths = ps.current_brushes()
        brush_names = ", ".join(os.path.basename(str(p)) for p in brush_paths)
        # Truncate brush names if absurdly long so we don't blow past screen.
        if len(brush_names) > 60:
            brush_names = brush_names[:57] + "..."

        # Plateau buffer state: max-min spread vs the 2*delta threshold.
        if ps.match_history:
            spread = max(ps.match_history) - min(ps.match_history)
            plateau_str = f"{spread:.3f}/{ps.plateau_delta * 2:.3f}"
        else:
            plateau_str = "—"

        # Truncation rule: at very high phase counts, show N+/M instead of
        # listing — keeps the bar from overflowing.
        if ps.total_phases > 99:
            phase_str = f"phase: {ps.current_phase + 1}/{ps.total_phases}+"
        else:
            phase_str = f"phase: {ps.current_phase + 1}/{ps.total_phases}"

        text = (
            f"{phase_str}  ·  "
            f"brush: {brush_names}  ·  "
            f"gens-in-phase: {ps.gens_in_current}  ·  "
            f"plateau: {plateau_str}"
        )
        # Yellow-ish color distinguishes phase bar from white/gray imagemutate HUD.
        text_surf, _ = self.hud_font.render(text, (220, 220, 100))
        bar.blit(text_surf, (HUD_PADDING, HUD_PADDING))
        surface.blit(bar, (0, screen_h - existing_hud_h - line_h))
