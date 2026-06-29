"""Phased painting primitives — shared by any app that wants paint-by-color-
bucket evolution with plateau-then-switch progression.

Decoupled from `imagemutate`'s uniform-random paradigm. Used by
`src/apps/imagephase.py` (the dedicated phased-painting app); reusable
by any future app that imports it.

Design:

- `is_plateau(history, delta)` — pure function on a match-percentage history
  window. Returns True when the run has stalled within that window.

- `PhaseState` — small dataclass holding the per-phase brush sets / radii /
  max-gens, plus the rolling match-history and current-phase pointer. The
  consuming App calls `should_advance(match_pct)` after each generation and
  `advance()` when told to. No pygame, no display, no IO — testable in
  isolation.
"""
from collections import deque
from dataclasses import dataclass, field
from typing import Sequence


def is_plateau(history: Sequence[float], delta: float) -> bool:
    """Return True when the match-% history window indicates a plateau.

    Two co-conditions must both hold:
      - terminal-vs-initial gain is below threshold: history[-1] - history[0] < delta
      - max-min spread is below 2x threshold: max(history) - min(history) < 2*delta

    The terminal-vs-initial check is robust to monotonic-improvement detection
    even on noisy match-% trajectories. The variance check rules out windows
    that oscillate around a fixed mean (where terminal ≈ initial but the
    process isn't actually stuck — it's bouncing).

    Returns False if the window is shorter than 2 samples (not enough data
    to judge).
    """
    if len(history) < 2:
        return False
    drift = history[-1] - history[0]
    spread = max(history) - min(history)
    return drift < delta and spread < 2 * delta


@dataclass
class PhaseState:
    """Holds phased-painting state across the run.

    phase_brushes[i] is a list of brush paths for phase i (uniform-random
    pick within a phase preserves imagemutate's existing intra-phase
    brush behavior; the typical FDM case has exactly one brush per phase).

    plateau_window and plateau_delta govern when `should_advance` returns
    True with reason='plateau'. phase_max_gens[i] is an inclusive safety
    cap — when gens_in_current >= phase_max_gens[i], advance fires with
    reason='max_gens' even if the plateau check hasn't tripped.

    No pygame imports here; brush paths are just paths (str / pathlib).
    The consuming App is responsible for loading them into pygame surfaces
    when a phase becomes active.
    """
    phase_brushes: list  # list[list[str|Path]]
    phase_max_radius: list  # list[int]
    phase_min_radius: list  # list[int]
    phase_max_gens: list  # list[int|float]  (float('inf') for "no cap")
    plateau_window: int = 200
    plateau_delta: float = 0.005

    current_phase: int = 0
    gens_in_current: int = 0
    match_history: deque = field(default_factory=lambda: deque(maxlen=200))

    def __post_init__(self):
        self.match_history = deque(maxlen=self.plateau_window)
        n = len(self.phase_brushes)
        if not (len(self.phase_max_radius) == n
                and len(self.phase_min_radius) == n
                and len(self.phase_max_gens) == n):
            raise ValueError(
                f"phase array length mismatch: "
                f"phases={n}, "
                f"max_radius={len(self.phase_max_radius)}, "
                f"min_radius={len(self.phase_min_radius)}, "
                f"max_gens={len(self.phase_max_gens)}"
            )
        if n < 1:
            raise ValueError("at least one phase required")

    @property
    def total_phases(self) -> int:
        return len(self.phase_brushes)

    @property
    def is_last_phase(self) -> bool:
        return self.current_phase >= self.total_phases - 1

    def current_brushes(self):
        return self.phase_brushes[self.current_phase]

    def current_max_radius(self) -> int:
        return self.phase_max_radius[self.current_phase]

    def current_min_radius(self) -> int:
        return self.phase_min_radius[self.current_phase]

    def record_gen(self, match_pct: float) -> None:
        """Call once per generation, after the winning child is applied."""
        self.match_history.append(match_pct)
        self.gens_in_current += 1

    def should_advance(self) -> tuple:
        """Return (should_advance: bool, reason: str).

        reason in {'plateau', 'max_gens', ''}. The caller is responsible
        for invoking `advance()` when this returns True (or for marking
        the run complete if `is_last_phase` is already True).
        """
        cap = self.phase_max_gens[self.current_phase]
        # max_gens takes precedence (cap is a hard ceiling).
        if cap != float('inf') and self.gens_in_current >= cap:
            return True, 'max_gens'
        # Plateau check requires a full window of samples.
        if len(self.match_history) >= self.plateau_window:
            if is_plateau(self.match_history, self.plateau_delta):
                return True, 'plateau'
        return False, ''

    def advance(self) -> None:
        """Move to the next phase. Resets gens_in_current and match_history.
        Caller MUST check is_last_phase before calling advance — calling
        past the last phase is a logic error and raises IndexError."""
        if self.is_last_phase:
            raise IndexError(
                f"cannot advance past last phase "
                f"(current_phase={self.current_phase}, "
                f"total_phases={self.total_phases})"
            )
        self.current_phase += 1
        self.gens_in_current = 0
        self.match_history.clear()
