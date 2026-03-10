"""Criteria versioning and management.

Implemented in Bead 10.
"""

from __future__ import annotations


class CriteriaManager:
    """Manages versioned evaluation criteria.

    Criteria are set during grounding, immutable within a run (v1 policy).
    Agent proposals for changes are captured but not auto-applied.
    """

    def __init__(self) -> None:
        raise NotImplementedError("CriteriaManager — implemented in Bead 10")
