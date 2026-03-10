"""Policy enforcement — guardrails applied to every candidate diff.

Implemented in Bead 7.
"""

from __future__ import annotations


def check_policy() -> None:
    """Validate a candidate diff against policy rules.

    Checks: diff size, protected paths, forbidden extensions,
    secret patterns, dependency changes, excluded paths, empty diff.
    Runs before hard gates — cheaper and catches obvious problems.
    """
    raise NotImplementedError("Policy enforcement — implemented in Bead 7")
