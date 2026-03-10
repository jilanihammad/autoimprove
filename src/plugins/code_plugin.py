"""Code evaluator plugin — tests, lint, complexity, build.

Implemented in Bead 6.
"""

from __future__ import annotations


class CodePlugin:
    """Evaluates code quality with deterministic metrics.

    Hard gates: tests, build, typecheck, lint.
    Soft metrics: complexity, coverage, duplication, LOC.
    Confidence profile: HIGH.
    """

    def __init__(self) -> None:
        raise NotImplementedError("CodePlugin — implemented in Bead 6")
