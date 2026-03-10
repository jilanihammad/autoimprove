"""Experiment logging — structured per-iteration audit trail.

Implemented in Bead 16.
"""

from __future__ import annotations


class ExperimentLog:
    """Append-only log of every iteration with full evidence.

    Written to disk after every iteration for crash recovery.
    Primary input for the summary report.
    """

    def __init__(self) -> None:
        raise NotImplementedError("ExperimentLog — implemented in Bead 16")
