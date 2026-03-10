"""Document evaluator plugin — docs, spreadsheets, presentations.

Implemented in Bead 21.
"""

from __future__ import annotations


class DocumentPlugin:
    """Evaluates documents primarily via LLM judgment.

    Hard gates: parseable, structure intact, not empty.
    Soft metrics: word count, readability, structure score.
    Confidence profile: LOW.
    """

    def __init__(self) -> None:
        raise NotImplementedError("DocumentPlugin — implemented in Bead 21")
