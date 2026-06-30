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
import datetime
import json
import logging
import os
import sys

import pygame

from apps.imagemutate import (
    App as MutateApp,
    HUD_FONT_SIZE,
    HUD_PADDING,
    OUTPUT_MODE_IMAGE,
    OUTPUT_MODE_INSTRUCTIONS,
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
    # F9: per-stroke frame dump. Monotonic counter across the whole run
    # (every winning-child + every phase-pause replica advances it).
    _frame_counter = 0

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

        # F9: per-stroke frame dump (after the winning child has been applied
        # to canvas). No-op when --frames-dir is not set.
        self._save_stroke_frame()

        # Record match-% after the winning child was applied.
        score = match_score(self.target_surface, self.canvas.surface)
        pct = get_match_percentage(score)
        self.phase_state.record_gen(pct)

        # Plateau / cap check.
        should, reason = self.phase_state.should_advance()
        if not should:
            return

        # F5: phase-boundary checkpoint save. Triggered only when --save-gen
        # is active. Fires regardless of the periodic-modulo schedule — the
        # boundary is always a notable artifact even if the periodic save
        # would have happened at a different gen.
        if self.options.get('save_gen') is not None:
            self._save_image_with_phase(end=True)

        # F6: finalize per-phase stats before advancing. Captures gens_used,
        # match_at_start, match_at_end, clock_time, advance_reason into the
        # PhaseState's _finalized_stats list for end-of-run summary + JSON.
        window_delta = self.phase_state.window_delta()
        stats = self.phase_state.finalize_current_phase(reason)

        if self.phase_state.is_last_phase:
            # Final phase complete. advance_reason in stats already records
            # 'plateau' / 'max_gens'; but the user-facing label "final"
            # captures that this is the end of the run.
            log.info(
                "imagephase: final phase complete (reason=%s, gens=%d, match=%.3f%%)",
                reason, stats['gens_used'], stats['match_at_end'],
            )
            print(
                f"PHASE final reason={reason} gens={stats['gens_used']} "
                f"match-start={stats['match_at_start']:.3f}% "
                f"match-end={stats['match_at_end']:.3f}% "
                f"window-delta={window_delta:.4f}",
                file=sys.stderr,
            )
            self._final_phase_complete = True
            return

        # F9: bake the phase boundary into the frame sequence as held copies.
        # Fires only on inter-phase transitions (the final phase's completion
        # is not a transition since there's nothing to transition to — its
        # last-frame state is already captured as the last stroke frame).
        self._save_phase_pause_frames()

        old_phase = self.phase_state.current_phase
        self.phase_state.advance()
        log.info(
            "PHASE advance %d→%d reason=%s gens=%d match=%.3f%%",
            old_phase + 1, self.phase_state.current_phase + 1, reason,
            stats['gens_used'], stats['match_at_end'],
        )
        print(
            f"PHASE advance {old_phase+1}→{self.phase_state.current_phase+1} "
            f"reason={reason} gens={stats['gens_used']} "
            f"match-start={stats['match_at_start']:.3f}% "
            f"match-end={stats['match_at_end']:.3f}% "
            f"window-delta={window_delta:.4f}",
            file=sys.stderr,
        )

    def evolution_complete(self):
        """Override: phase completion overrides the gen/seconds/match-% checks."""
        if self._final_phase_complete:
            return True
        return super().evolution_complete()

    def save(self, output_mode=None):
        """Override: image saves use phase-aware naming when phase mode is
        active. Routes ALL image-mode saves (periodic, --save-on-exit, hotkey)
        through `_save_image_with_phase`. Instruction-mode (JSON) saves go
        through `_save_instructions_with_phase` (F6) which injects per-shape
        phase tagging and a top-level `phase_stats` array.

        Filename pattern: <prefix>-c<C>-p<P>-g<G>.png (no `-end-` marker on
        regular saves; only phase-boundary saves in handle_evolution_tick
        set end=True)."""
        if output_mode is None:
            output_mode = (
                OUTPUT_MODE_INSTRUCTIONS
                if self.options.get('instructions') else OUTPUT_MODE_IMAGE
            )
        if self.phase_state is None:
            return super().save(output_mode)
        if output_mode == OUTPUT_MODE_IMAGE:
            return self._save_image_with_phase(end=False)
        return self._save_instructions_with_phase()

    def _save_instructions_with_phase(self):
        """F6: extend imagemutate's instruction-mode save with phase metadata.
        Top-level adds `phase_stats: [...]` (one record per finalized phase,
        plus an in-progress record for the current phase if applicable).
        Each shape in `history` gains a `phase` field (1-indexed) reflecting
        which phase it was painted in."""
        savefile = self.get_save_file_name('json')
        os.makedirs(os.path.dirname(savefile) or '.', exist_ok=True)
        canvas_data = self.canvas.serialize()
        # Tag each shape's params dict with its phase. Shapes are stored in
        # apply order; phase 1 owns the first phase_stats[0]['gens_used'],
        # phase 2 owns the next phase_stats[1]['gens_used'], etc.
        self._tag_history_with_phase(canvas_data.get('history', []))

        phase_stats = self.phase_state.get_stats_list()
        # If a phase is still in-progress (mid-run save), append a
        # provisional record so the active phase is represented in the
        # output. advance_reason='in_progress' marks it as non-final.
        if (self.phase_state.gens_in_current > 0 and
                (not phase_stats
                 or phase_stats[-1]['phase'] != self.phase_state.current_phase + 1)):
            phase_stats.append({
                'phase': self.phase_state.current_phase + 1,
                'gens_used': self.phase_state.gens_in_current,
                'match_at_start': self.phase_state._phase_start_match,
                'match_at_end': self.phase_state._last_match,
                'clock_time': None,  # not finalized
                'advance_reason': 'in_progress',
            })

        output_data = {
            "version": 2,  # bumped from imagemutate's v1 — adds phase_stats
            "seed": self.options.get('seed'),
            "timestamp": datetime.datetime.utcnow().isoformat(),
            "gen_stop": self.options.get('gen_stop'),
            "children": self.options.get('children'),
            "radius": self.options.get('radius'),
            "shape": self.options.get('shape'),
            "phase_stats": phase_stats,
            **canvas_data,
        }
        with open(savefile, 'w') as out:
            out.write(json.dumps(output_data))
        return savefile

    def print_profiler(self):
        """Override: print imagemutate's profiler block, then append a per-
        phase stats table (F6). Called once at evolve() end."""
        super().print_profiler()
        if self.phase_state is None:
            return
        stats_list = self.phase_state.get_stats_list()
        if not stats_list:
            return
        # Compact table aimed at log readability + scripting.
        print("===== phase stats =====")
        header = (
            f"{'phase':>5}  {'gens':>6}  {'match-start':>11}  "
            f"{'match-end':>10}  {'clock(s)':>9}  reason"
        )
        print(header)
        print("-" * len(header))
        for s in stats_list:
            ct = s['clock_time']
            ct_str = f"{ct:9.2f}" if ct is not None else f"{'?':>9}"
            print(
                f"{s['phase']:>5}  {s['gens_used']:>6}  "
                f"{s['match_at_start']:>10.3f}%  {s['match_at_end']:>9.3f}%  "
                f"{ct_str}  {s['advance_reason']}"
            )
        print("-" * len(header))

    def _tag_history_with_phase(self, history):
        """Mutate each shape entry in `history` to include a 'phase' field
        (1-indexed) based on cumulative gens-per-phase.

        Each history entry is `[opcode, params_dict]` per CanvasAction's
        __json__ format (canvas.py line 145)."""
        def _tag(entry, phase_idx):
            # Defensive: skip entries that aren't the expected
            # [opcode, params_dict] shape (e.g., future schema changes).
            if (isinstance(entry, list) and len(entry) >= 2
                    and isinstance(entry[1], dict)):
                entry[1]['phase'] = phase_idx

        cumulative = 0
        for stats in self.phase_state.get_stats_list():
            gens_in_phase = stats['gens_used']
            phase_idx = stats['phase']
            for shape_idx in range(cumulative, cumulative + gens_in_phase):
                if shape_idx >= len(history):
                    break
                _tag(history[shape_idx], phase_idx)
            cumulative += gens_in_phase
        # Leftover shapes belong to the in-progress (current) phase.
        in_progress_phase = self.phase_state.current_phase + 1
        for shape_idx in range(cumulative, len(history)):
            _tag(history[shape_idx], in_progress_phase)

    def _save_image_with_phase(self, end=False):
        """Save canvas with imagephase's phase-aware naming convention:
            <prefix>-c<C>-p<P>[-end]-g<G>.png
        end=True is for phase-boundary saves (always fired on phase advance
        when --save-gen is active, regardless of the modulo schedule).
        Returns the path written, or None if no canvas to save."""
        if self.canvas is None or self.canvas.surface is None:
            return None
        prefix = (self.options.get('prefix')
                  or self.get_default_save_file_prefix())
        children = self.options.get('children', 0)
        phase_n = (self.phase_state.current_phase + 1) if self.phase_state else 0
        gen = self.current_generation
        end_marker = '-end' if end else ''
        name = (
            f"{prefix}-c{children:06d}-p{phase_n:02d}{end_marker}"
            f"-g{gen:06d}.png"
        )
        save_dir = self.options.get('save_directory', '.')
        path = os.path.join(save_dir, name)
        os.makedirs(os.path.dirname(path) or '.', exist_ok=True)
        pygame.image.save(self.canvas.surface, path)
        return path

    def _frames_dir(self):
        """Return frames-dir Path if --frames-dir is set and a canvas exists,
        else None. Centralizes the no-op guard for F9 hooks."""
        frames_dir = self.options.get('frames_dir')
        if not frames_dir:
            return None
        if self.canvas is None or self.canvas.surface is None:
            return None
        return frames_dir

    def _frame_path(self, frames_dir):
        """Build the next frame's path. Advances the monotonic counter."""
        prefix = (self.options.get('prefix')
                  or self.get_default_save_file_prefix())
        name = f"{prefix}-frame-{self._frame_counter:07d}.png"
        self._frame_counter += 1
        return os.path.join(frames_dir, name)

    def _save_stroke_frame(self):
        """F9: write the post-winning-child canvas state as one frame in the
        movie sequence. No-op when --frames-dir is unset."""
        frames_dir = self._frames_dir()
        if frames_dir is None:
            return None
        os.makedirs(frames_dir, exist_ok=True)
        path = self._frame_path(frames_dir)
        pygame.image.save(self.canvas.surface, path)
        return path

    def _save_phase_pause_frames(self):
        """F9: write N replicas of the current canvas at a phase boundary so
        playback shows a visible pause at the transition. N is taken from
        --phase-pause-frames (default 0 = no pause). No-op when --frames-dir
        is unset or pauses == 0."""
        frames_dir = self._frames_dir()
        if frames_dir is None:
            return 0
        n = int(self.options.get('phase_pause_frames') or 0)
        if n <= 0:
            return 0
        os.makedirs(frames_dir, exist_ok=True)
        for _ in range(n):
            path = self._frame_path(frames_dir)
            pygame.image.save(self.canvas.surface, path)
        return n

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
