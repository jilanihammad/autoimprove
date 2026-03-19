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


class IterationStrategy(str, Enum):
    """How the orchestrator handles accept/reject for an artifact type."""

    AUTO = "auto"            # High confidence — auto-accept above threshold
    INTERACTIVE = "interactive"  # Medium — show diff + score, ask user per change
    BATCH = "batch"          # Low — collect N proposals, let user pick best
    PREVIEW = "preview"      # Very low — generate before/after, require approval


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
class SemanticDiff:
    """Human/LLM-readable description of changes to a non-text artifact.

    Used instead of raw git diffs for binary files (PPTX, DOCX, XLSX)
    where ``git diff`` produces opaque output.
    """

    summary: str  # e.g. "3 slides modified, 1 deleted"
    sections: list[dict[str, str]] = field(default_factory=list)
    # Each dict: {"location": "Slide 3", "change": "Removed 47 words..."}
    metrics: dict[str, tuple] = field(default_factory=dict)
    # e.g. {"word_count": (340, 293), "slide_count": (12, 11)}

    def as_text(self) -> str:
        """Format as readable text for inclusion in prompts."""
        lines = [f"## Change Summary\n{self.summary}\n"]
        if self.sections:
            lines.append("## Detailed Changes")
            for s in self.sections:
                loc = s.get("location", "Unknown")
                change = s.get("change", "")
                lines.append(f"- **{loc}**: {change}")
        if self.metrics:
            lines.append("\n## Metrics")
            for name, (before, after) in self.metrics.items():
                lines.append(f"- {name}: {before} → {after}")
        return "\n".join(lines)


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
