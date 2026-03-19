"""Backward-compatible alias — CoderAgent is now ModifierAgent.

Import from ``src.agents.modifier`` for new code.
"""

from src.agents.modifier import ModifierAgent as ModifierAgent

# Backward compatibility: CoderAgent is an alias for ModifierAgent
CoderAgent = ModifierAgent

__all__ = ["CoderAgent", "ModifierAgent"]
