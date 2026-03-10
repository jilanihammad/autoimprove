"""Workflow evaluator plugin — n8n, Step Functions, Lambda pipelines.

Implemented in Bead 20.
"""

from __future__ import annotations


class WorkflowPlugin:
    """Evaluates AI workflow artifacts with mixed deterministic/judgment signals.

    Hard gates: schema valid, parseable, no orphaned nodes.
    Soft metrics: error handling coverage, complexity.
    Confidence profile: MEDIUM.
    """

    def __init__(self) -> None:
        raise NotImplementedError("WorkflowPlugin — implemented in Bead 20")
