"""Search memory — hypothesis tracking and anti-repetition.

Implemented in Bead 12.
"""

from __future__ import annotations


class SearchMemory:
    """Tracks every hypothesis attempted, its outcome, and patterns.

    Prevents the agent from retrying failed ideas and helps
    prioritize promising directions.
    """

    def __init__(self) -> None:
        raise NotImplementedError("SearchMemory — implemented in Bead 12")
