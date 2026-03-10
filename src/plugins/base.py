"""Abstract base class for artifact-type evaluator plugins.

Every artifact type (code, workflow, document) implements this contract.
The plugin system is the extensibility backbone — adding a new artifact
type means writing a new plugin, nothing else.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field

from src.types import BaselineSnapshot, ConfidenceProfile, DeltaSummary, Diff, GateResult, SoftEvalResult


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
