"""Abstract base class for evaluator plugins.

Implemented in Bead 5.
"""

from __future__ import annotations


class EvaluatorPlugin:
    """Contract that all artifact-type evaluator plugins must implement.

    Methods: discover_targets, preflight, baseline, hard_gates,
    soft_evaluate, summarize_delta, guardrails.
    """

    def __init__(self) -> None:
        raise NotImplementedError("EvaluatorPlugin — implemented in Bead 5")
