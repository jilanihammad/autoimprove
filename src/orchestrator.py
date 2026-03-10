"""Orchestrator — core loop: init → ground → iterate → wrap-up.

Implemented across Beads 13 (grounding), 14 (autonomous loop), 15 (stop conditions).
"""

from __future__ import annotations

from src.config import Config


def run_autoimprove(config: Config) -> None:
    """Top-level entry point for an AutoImprove run.

    Sequence:
        1. Preflight checks
        2. Create isolated run context (worktree)
        3. Capture baseline
        4. Grounding phase (human-in-loop or auto)
        5. Autonomous improvement loop
        6. Generate reports
        7. Print results
    """
    raise NotImplementedError("Orchestrator — implemented in Beads 13-15")
