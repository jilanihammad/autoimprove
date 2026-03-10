"""Preflight — pre-run environment validation.

Implemented in Bead 4.
"""

from __future__ import annotations


def run_preflight() -> None:
    """Validate environment before starting a run.

    Checks: git repo, clean state, Python version, agent command,
    target paths, config validity, disk space, secrets, program.md.
    Fails fast with clear, actionable error messages.
    """
    raise NotImplementedError("Preflight — implemented in Bead 4")
