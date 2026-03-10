"""LLM-as-judge — pairwise comparison, repeated judging, aggregation.

Implemented in Bead 8.
"""

from __future__ import annotations


class LLMJudge:
    """Controlled LLM-based evaluation with context separation.

    Design: judge sees only the diff + criteria, never the improvement
    hypothesis. Supports repeated judging with variance tracking.
    """

    def __init__(self) -> None:
        raise NotImplementedError("LLMJudge — implemented in Bead 8")
