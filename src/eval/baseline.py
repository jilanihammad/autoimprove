"""Baseline snapshot capture.

Implemented in Bead 13 (as part of grounding phase).
"""

from __future__ import annotations


def capture_baseline() -> None:
    """Capture current-state metrics for all target artifacts.

    Delegates to the active plugin's baseline() method.
    Saves snapshot to the run directory.
    """
    raise NotImplementedError("Baseline capture — implemented in Bead 13")
