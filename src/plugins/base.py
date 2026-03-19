"""Abstract base class for artifact-type evaluator plugins.

Every artifact type (code, workflow, document) implements this contract.
The plugin system is the extensibility backbone — adding a new artifact
type means writing a new plugin, nothing else.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field

from src.types import (
    BaselineSnapshot, ConfidenceProfile, DeltaSummary, Diff, GateResult,
    IterationStrategy, SemanticDiff, SoftEvalResult,
)


@dataclass
class GuardrailConfig:
    """Plugin-specific guardrail rules, merged with global policy."""

    protected_patterns: list[str] = field(default_factory=list)
    max_diff_lines: int | None = None
    forbidden_extensions: list[str] = field(default_factory=list)
    required_files: list[str] = field(default_factory=list)


@dataclass
class PluginPreflightResult:
    """Result of a plugin's preflight check."""

    passed: bool
    available_tools: list[str] = field(default_factory=list)
    missing_tools: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)


class EvaluatorPlugin(ABC):
    """Contract that all artifact-type evaluator plugins must implement."""

    @property
    @abstractmethod
    def name(self) -> str:
        """Plugin identifier, e.g. ``'code'``, ``'workflow'``, ``'document'``."""

    @property
    @abstractmethod
    def confidence_profile(self) -> ConfidenceProfile:
        """Default evidence quality for this artifact type."""

    @property
    @abstractmethod
    def description(self) -> str:
        """Human-readable description of what this plugin evaluates."""

    @abstractmethod
    def discover_targets(self, paths: list[str], exclude: list[str]) -> list[str]:
        """Find evaluable artifacts in *paths*, excluding *exclude* patterns."""

    @abstractmethod
    def preflight(self, targets: list[str]) -> PluginPreflightResult:
        """Verify tools/deps needed for evaluation are available."""

    @abstractmethod
    def baseline(self, targets: list[str], working_dir: str) -> BaselineSnapshot:
        """Capture current-state metrics for all targets."""

    @abstractmethod
    def hard_gates(self, diff: Diff, targets: list[str], working_dir: str) -> GateResult:
        """Run deterministic pass/fail checks.  Any failure → immediate reject."""

    @abstractmethod
    def soft_evaluate(
        self, diff: Diff, targets: list[str], criteria: dict, working_dir: str
    ) -> SoftEvalResult:
        """Run scored evaluation.  Returns per-metric scores (0.0–1.0)."""

    @abstractmethod
    def summarize_delta(self, baseline: BaselineSnapshot, current: BaselineSnapshot) -> DeltaSummary:
        """Compare baseline to current state for the final report."""

    def guardrails(self) -> GuardrailConfig:
        """Return artifact-specific guardrail rules (override if needed)."""
        return GuardrailConfig()

    def deterministic_metric_reliability(self) -> float:
        """0.0 (no deterministic signals) to 1.0 (strong deterministic signals).
        Used by the engine to weight deterministic vs LLM judge scores."""
        return 0.5

    def build_judge_prompt(
        self, current_snapshot: str, candidate_diff: str, rubric_text: str, eval_anchors: str,
    ) -> str | None:
        """Optional: plugin-specific judge prompt. Returns None to use default."""
        return None

    def judge_perspectives(self) -> list[dict] | None:
        """Optional: return multiple judge perspectives for subjective domains.
        Each dict has 'role' and 'instruction' keys.
        When provided, the judge runs each perspective instead of repeating the same prompt."""
        return None

    def semantic_diff(self, before_path: str, after_path: str) -> SemanticDiff | None:
        """Return a human/LLM-readable diff for non-text artifacts.

        Override for binary file types (PPTX, DOCX, XLSX) where git diff
        produces opaque output.  Return ``None`` to fall back to git diff.
        """
        return None

    def iteration_strategy(self) -> IterationStrategy:
        """How should the orchestrator handle accept/reject for this artifact?

        Plugins with strong deterministic signals (code) use AUTO.
        Plugins relying on LLM judgment should use INTERACTIVE or BATCH.
        """
        match self.confidence_profile:
            case ConfidenceProfile.HIGH:
                return IterationStrategy.AUTO
            case ConfidenceProfile.MEDIUM:
                return IterationStrategy.INTERACTIVE
            case ConfidenceProfile.LOW:
                return IterationStrategy.BATCH
            case _:
                return IterationStrategy.AUTO

    # ------------------------------------------------------------------
    # Prompt templates — override these to adapt agents to your artifact type
    # ------------------------------------------------------------------

    def indexer_prompt_hint(self) -> str:
        """What should the indexer look for in this artifact type?
        Injected into the indexer agent prompt as guidance."""
        return (
            "For each file, summarize:\n"
            "- **Purpose**: What this file/module does (1 sentence)\n"
            "- **Key abstractions**: Main classes, functions, or patterns\n"
            "- **Dependencies**: What it imports from / is used by\n"
            "- **Complexity hotspots**: Anything notably complex or fragile"
        )

    def analyst_categories(self) -> list[dict[str, str]]:
        """Categories for backlog items.  Each dict has ``name`` and ``description``.
        The analyst agent uses these to categorize proposed improvements."""
        return [{"name": "general", "description": "General improvements"}]

    def analyst_role(self) -> str:
        """Role description injected into the analyst prompt."""
        return "a senior reviewer"

    def modifier_role(self) -> str:
        """Role description for the agent making changes."""
        return "You are an expert at improving this type of artifact."

    def modifier_constraints(self) -> list[str]:
        """Rules the modifier agent must follow."""
        return [
            "Make exactly ONE focused change.",
            "Do NOT modify any files not listed in the task.",
            "Keep changes minimal and focused.",
        ]

    def reviewer_focus(self) -> str:
        """What should the reviewer evaluate?  Injected into the reviewer prompt."""
        return "Evaluate whether this change is a genuine improvement."

    def theme_map(self) -> dict[str, tuple[str, str, str]]:
        """Map category names to ``(display_name, icon, goal)`` for interactive grounding.
        Falls back to generating from ``analyst_categories()``."""
        return {
            cat["name"]: (
                cat["name"].replace("_", " ").title(),
                "🔧",
                cat.get("description", ""),
            )
            for cat in self.analyst_categories()
        }
