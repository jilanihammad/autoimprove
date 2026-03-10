"""Run context — manages run lifecycle, state, and directory structure.

Implemented in Bead 3.
"""

from __future__ import annotations


class RunContext:
    """Central state object for an AutoImprove run.

    Created once during initialization, passed to all components.
    Tracks: run ID, worktree paths, iteration count, accepted state,
    budget consumption, and provides commit/revert operations.
    """

    def __init__(self) -> None:
        raise NotImplementedError("RunContext — implemented in Bead 3")
