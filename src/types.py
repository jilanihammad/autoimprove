"""Shared type definitions used across all AutoImprove modules."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum


class Decision(str, Enum):
    """Accept or reject a candidate change."""

    ACCEPT = "accepted"
    REJECT = "rejected"


class ConfidenceProfile(str, Enum):
    """Default evidence quality for an artifact type."""

    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


class RunStatus(str, Enum):
    """Lifecycle status of an AutoImprove run."""

    INITIALIZING = "initializing"
    GROUNDING = "grounding"
    RUNNING = "running"
    STOPPED = "stopped"
    COMPLETED = "completed"
    FAILED = "failed"


# ---------------------------------------------------------------------------
# Data containers
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Diff:
    """Represents a git diff between two states."""

    files_changed: list[str]
    lines_added: int
    lines_removed: int
    raw_diff: str


@dataclass
class GateResult:
    """Result of running hard-gate checks."""

    all_passed: bool
    gates: dict[str, bool] = field(default_factory=dict)
    failures: list[str] = field(default_factory=list)


@dataclass
class SoftEvalResult:
    """Result of scored (soft) evaluation metrics."""

    scores: dict[str, float] = field(default_factory=dict)
    has_deterministic: bool = False
    composite: float = 0.0


@dataclass
class BaselineSnapshot:
    """Captured metrics at the start of a run."""

    plugin_name: str
    timestamp: str
    metrics: dict[str, float] = field(default_factory=dict)
    raw_data: dict = field(default_factory=dict)
    targets: list[str] = field(default_factory=list)


@dataclass
class DeltaSummary:
    """Before/after comparison for the final report."""

    plugin_name: str
    improved: dict[str, tuple[float, float]] = field(default_factory=dict)
    regressed: dict[str, tuple[float, float]] = field(default_factory=dict)
    unchanged: list[str] = field(default_factory=list)
    summary_text: str = ""


@dataclass
class ExperimentOutcome:
    """Outcome of a single iteration, stored in the experiment log."""

    decision: Decision
    reason: str
    composite_score: float | None = None
    confidence: float | None = None
    evidence: dict = field(default_factory=dict)
    duration_seconds: float = 0.0
    criteria_version: int = 1
