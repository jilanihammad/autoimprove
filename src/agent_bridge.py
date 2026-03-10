"""Agent bridge — agent-agnostic interface for invoking CLI coding agents.

Implemented in Bead 11.
"""

from __future__ import annotations


class AgentBridge:
    """Invokes any CLI-based coding agent (Claude, Codex, Kiro, custom).

    Handles prompt construction, invocation, response capture, and timeouts.
    """

    def __init__(self) -> None:
        raise NotImplementedError("AgentBridge — implemented in Bead 11")
