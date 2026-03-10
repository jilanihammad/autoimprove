"""Acceptance decision engine — policy → gates → score → confidence → decide.

Implemented in Bead 9.
"""

from __future__ import annotations


class AcceptanceEngine:
    """Orchestrates the full accept/reject decision pipeline.

    Pipeline: policy check → hard gates → soft eval → LLM judge →
    confidence calculation → accept or reject with full evidence.
    """

    def __init__(self) -> None:
        raise NotImplementedError("AcceptanceEngine — implemented in Bead 9")
