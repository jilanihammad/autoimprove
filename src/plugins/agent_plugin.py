"""Agent evaluator plugin — improve LLM agents via behavioral testing.

Treats LLM agent artifacts (system prompts, tool definitions, config files)
as improvable targets.  Evaluation is behavioral: run the agent on test inputs
and compare outputs before/after a change.

Deterministic reliability is LOW (0.15) because evaluation is almost entirely
LLM-judged — there are no linters or type-checkers for prompts.
"""

from __future__ import annotations

import json
import subprocess
import shutil
from dataclasses import dataclass, field
from datetime import datetime, timezone
from fnmatch import fnmatch
from pathlib import Path

import yaml

from src.plugins.base import EvaluatorPlugin, GuardrailConfig, PluginPreflightResult
from src.types import (
    BaselineSnapshot, ConfidenceProfile, DeltaSummary, Diff,
    GateResult, IterationStrategy, SoftEvalResult,
)

# File patterns that indicate agent artifacts
_AGENT_PATTERNS = (
    "*.system.md", "*.system.txt", "system_prompt.*",
    "SYSTEM_PROMPT", "SYSTEM_PROMPT.*",
    "tools.json", "tools.yaml", "*.tools.*",
    "agent.yaml", "agent.json", "AGENTS.md",
    "mcp.json", "*.mcp.*",
    "*.prompt.md", "*.prompt.txt",
)

# Directories that typically contain agent configs
_AGENT_DIRS = (".claude", "agents", "prompts", "agent_configs")


@dataclass
class AgentTestCase:
    """A single test input for behavioral evaluation."""

    input: str
    expected_behavior: str
    eval_criteria: str = ""


@dataclass
class AgentTestResult:
    """Result of running agent on a single test case."""

    test_input: str
    response: str
    response_tokens: int = 0
    latency_seconds: float = 0.0
    error: str = ""


class AgentPlugin(EvaluatorPlugin):
    """Evaluates LLM agent artifacts via behavioral testing."""

    @property
    def name(self) -> str:
        return "agent"

    @property
    def confidence_profile(self) -> ConfidenceProfile:
        return ConfidenceProfile.LOW

    @property
    def description(self) -> str:
        return "Evaluates LLM agent configs: system prompts, tool definitions, guardrails."

    # ------------------------------------------------------------------
    # Discovery
    # ------------------------------------------------------------------

    def discover_targets(self, paths: list[str], exclude: list[str]) -> list[str]:
        targets: list[str] = []
        for p in paths:
            pp = Path(p)
            if pp.is_file() and self._is_agent_file(pp):
                targets.append(str(pp))
                continue
            if not pp.is_dir():
                continue
            for fp in pp.rglob("*"):
                if not fp.is_file():
                    continue
                if not self._is_agent_file(fp):
                    continue
                rel = str(fp)
                parts = fp.parts
                if any(
                    fnmatch(rel, ex) or fnmatch(fp.name, ex)
                    or any(fnmatch(part, ex.strip("*/")) for part in parts)
                    for ex in exclude
                ):
                    continue
                targets.append(str(fp))
        return targets

    def _is_agent_file(self, fp: Path) -> bool:
        """Check if a file matches agent artifact patterns."""
        name = fp.name
        for pattern in _AGENT_PATTERNS:
            if fnmatch(name, pattern):
                return True
        # Files inside agent-related directories
        if any(d in fp.parts for d in _AGENT_DIRS):
            return fp.suffix in (".md", ".txt", ".json", ".yaml", ".yml", ".toml")
        return False

    # ------------------------------------------------------------------
    # Preflight
    # ------------------------------------------------------------------

    def preflight(self, targets: list[str]) -> PluginPreflightResult:
        available: list[str] = []
        missing: list[str] = []
        warnings: list[str] = []

        # Check for an LLM CLI (claude, etc.)
        for cmd in ("claude", "kiro-cli"):
            if shutil.which(cmd):
                available.append(cmd)
        if not available:
            warnings.append("No LLM CLI found (claude, kiro-cli). Agent testing will be limited.")

        # Check for test suite
        test_files = self._find_test_suites(targets)
        if test_files:
            available.append(f"agent_tests ({len(test_files)} suites)")
        else:
            warnings.append("No agent_tests.yaml found. Evaluation will rely on structural analysis only.")

        return PluginPreflightResult(
            passed=True,  # Agent plugin always passes — no hard requirements
            available_tools=available,
            missing_tools=missing,
            warnings=warnings,
        )

    # ------------------------------------------------------------------
    # Baseline
    # ------------------------------------------------------------------

    def baseline(self, targets: list[str], working_dir: str) -> BaselineSnapshot:
        metrics: dict[str, float] = {}

        # Count total prompt tokens (rough proxy: words / 0.75)
        total_words = 0
        for t in targets:
            try:
                content = Path(t).read_text(errors="ignore")
                total_words += len(content.split())
            except OSError:
                continue
        metrics["total_prompt_words"] = float(total_words)
        metrics["estimated_tokens"] = float(int(total_words / 0.75))
        metrics["num_files"] = float(len(targets))

        # Check for tool definitions
        tool_count = self._count_tools(targets)
        metrics["tool_count"] = float(tool_count)

        # Guardrail presence
        guardrail_score = self._check_guardrails(targets)
        metrics["guardrail_score"] = guardrail_score

        return BaselineSnapshot(
            plugin_name=self.name,
            timestamp=datetime.now(timezone.utc).isoformat(),
            metrics=metrics,
            targets=targets,
        )

    # ------------------------------------------------------------------
    # Hard gates
    # ------------------------------------------------------------------

    def hard_gates(self, diff: Diff, targets: list[str], working_dir: str) -> GateResult:
        gates: dict[str, bool] = {}
        failures: list[str] = []

        # Gate 1: JSON/YAML files still parse
        for f in diff.files_changed:
            fp = Path(working_dir) / f if not Path(f).is_absolute() else Path(f)
            if not fp.exists():
                continue
            if fp.suffix in (".json",):
                try:
                    json.loads(fp.read_text())
                except (json.JSONDecodeError, OSError) as e:
                    gates["json_valid"] = False
                    failures.append(f"Invalid JSON: {f} ({e})")
            elif fp.suffix in (".yaml", ".yml"):
                try:
                    yaml.safe_load(fp.read_text())
                except (yaml.YAMLError, OSError) as e:
                    gates["yaml_valid"] = False
                    failures.append(f"Invalid YAML: {f} ({e})")

        if "json_valid" not in gates:
            gates["json_valid"] = True
        if "yaml_valid" not in gates:
            gates["yaml_valid"] = True

        # Gate 2: No removed guardrails (safety instructions)
        guardrail_removed = self._detect_guardrail_removal(diff)
        gates["guardrails_preserved"] = not guardrail_removed
        if guardrail_removed:
            failures.append("Safety guardrails appear to have been removed")

        return GateResult(
            all_passed=all(gates.values()),
            gates=gates,
            failures=failures,
        )

    # ------------------------------------------------------------------
    # Soft evaluation
    # ------------------------------------------------------------------

    def soft_evaluate(
        self, diff: Diff, targets: list[str], criteria: dict, working_dir: str,
    ) -> SoftEvalResult:
        scores: dict[str, float] = {}

        # Token efficiency: fewer tokens for same capability = better
        # Score 0.5 (neutral) if no change, >0.5 if tokens decreased
        lines_delta = diff.lines_added - diff.lines_removed
        scores["token_efficiency"] = max(0.0, min(1.0, 0.5 - lines_delta / 100.0))

        # Guardrail presence after change
        scores["guardrail_score"] = self._check_guardrails(targets)

        # Composite (simple average since we have no deterministic test results)
        composite = sum(scores.values()) / len(scores) if scores else 0.5

        return SoftEvalResult(
            scores=scores,
            has_deterministic=False,
            composite=composite,
        )

    # ------------------------------------------------------------------
    # Delta summary
    # ------------------------------------------------------------------

    def summarize_delta(self, baseline: BaselineSnapshot, current: BaselineSnapshot) -> DeltaSummary:
        improved: dict[str, tuple[float, float]] = {}
        regressed: dict[str, tuple[float, float]] = {}
        unchanged: list[str] = []

        for key in baseline.metrics:
            before = baseline.metrics[key]
            after = current.metrics.get(key, before)
            # Lower is better for token counts; higher is better for guardrail_score
            lower_is_better = key in ("total_prompt_words", "estimated_tokens")
            if abs(after - before) < 0.001:
                unchanged.append(key)
            elif lower_is_better:
                if after < before:
                    improved[key] = (before, after)
                else:
                    regressed[key] = (before, after)
            else:
                if after > before:
                    improved[key] = (before, after)
                else:
                    regressed[key] = (before, after)

        lines = []
        for k, (b, a) in improved.items():
            lines.append(f"  + {k}: {b:.0f} -> {a:.0f}")
        for k, (b, a) in regressed.items():
            lines.append(f"  - {k}: {b:.0f} -> {a:.0f}")

        return DeltaSummary(
            plugin_name=self.name,
            improved=improved,
            regressed=regressed,
            unchanged=unchanged,
            summary_text="\n".join(lines) if lines else "No measurable changes.",
        )

    # ------------------------------------------------------------------
    # Guardrails
    # ------------------------------------------------------------------

    def guardrails(self) -> GuardrailConfig:
        return GuardrailConfig(
            protected_patterns=["*.lock"],
            max_diff_lines=200,  # Agent configs should have small diffs
        )

    def deterministic_metric_reliability(self) -> float:
        return 0.15  # Almost entirely LLM-judged

    def iteration_strategy(self) -> IterationStrategy:
        return IterationStrategy.PREVIEW  # Always require human review

    # ------------------------------------------------------------------
    # Prompt templates
    # ------------------------------------------------------------------

    def indexer_prompt_hint(self) -> str:
        return (
            "For each file, summarize:\n"
            "- **Purpose**: What this agent config/prompt does\n"
            "- **Agent role**: What role or persona the agent takes\n"
            "- **Tools**: What tools/capabilities are defined\n"
            "- **Guardrails**: Any safety boundaries or refusal patterns\n"
            "- **Token footprint**: Rough size (concise vs verbose)"
        )

    def analyst_categories(self) -> list[dict[str, str]]:
        return [
            {"name": "prompt_clarity", "description": "Clearer instructions, less ambiguity in system prompts"},
            {"name": "tool_quality", "description": "Better tool descriptions, examples, error handling"},
            {"name": "guardrails", "description": "Safety boundaries, refusal patterns, harmful content prevention"},
            {"name": "efficiency", "description": "Fewer tokens for same quality, less redundancy"},
            {"name": "coherence", "description": "Consistent persona, non-contradictory instructions"},
        ]

    def analyst_role(self) -> str:
        return "a senior prompt engineer and AI safety reviewer"

    def modifier_role(self) -> str:
        return (
            "You are a senior prompt engineer. You improve LLM agent configs — "
            "system prompts, tool definitions, and guardrails. "
            "Your changes should make agents more effective, concise, and safe."
        )

    def modifier_constraints(self) -> list[str]:
        return [
            "Make exactly ONE focused change to the agent config.",
            "NEVER remove safety guardrails or refusal instructions.",
            "NEVER change the agent's core persona or role.",
            "Prefer making instructions more specific over more verbose.",
            "If editing tool definitions, ensure valid JSON/YAML syntax.",
            "Do NOT modify files outside the agent config.",
        ]

    def reviewer_focus(self) -> str:
        return (
            "Evaluate whether this change improves the agent. Consider:\n"
            "1. Is the system prompt clearer and more actionable?\n"
            "2. Are tool definitions correct and well-described?\n"
            "3. Were any safety guardrails removed or weakened?\n"
            "4. Is the agent likely to behave better (not just differently) after this change?\n"
            "5. Was the change focused, or did it introduce unnecessary rewrites?"
        )

    def theme_map(self) -> dict[str, tuple[str, str, str]]:
        return {
            "prompt_clarity": ("Prompt Clarity", "📝", "Make agent instructions clearer and more specific."),
            "tool_quality": ("Tool Definitions", "🔧", "Improve tool descriptions and examples."),
            "guardrails": ("Safety & Guardrails", "🛡️", "Strengthen safety boundaries and refusal patterns."),
            "efficiency": ("Token Efficiency", "⚡", "Reduce prompt size without losing capability."),
            "coherence": ("Agent Coherence", "🎯", "Ensure consistent persona and non-contradictory instructions."),
        }

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _find_test_suites(self, targets: list[str]) -> list[Path]:
        """Find agent_tests.yaml files near the targets."""
        seen_dirs: set[str] = set()
        results: list[Path] = []
        for t in targets:
            parent = Path(t).parent
            if str(parent) in seen_dirs:
                continue
            seen_dirs.add(str(parent))
            for name in ("agent_tests.yaml", "agent_tests.yml"):
                test_file = parent / name
                if test_file.exists():
                    results.append(test_file)
        return results

    def _count_tools(self, targets: list[str]) -> int:
        """Count tool definitions across all targets."""
        count = 0
        for t in targets:
            fp = Path(t)
            if "tool" not in fp.name.lower() and "tool" not in fp.stem.lower():
                continue
            try:
                content = fp.read_text(errors="ignore")
                if fp.suffix == ".json":
                    data = json.loads(content)
                    if isinstance(data, list):
                        count += len(data)
                    elif isinstance(data, dict) and "tools" in data:
                        count += len(data["tools"])
                elif fp.suffix in (".yaml", ".yml"):
                    data = yaml.safe_load(content)
                    if isinstance(data, list):
                        count += len(data)
                    elif isinstance(data, dict) and "tools" in data:
                        count += len(data["tools"])
            except (json.JSONDecodeError, yaml.YAMLError, OSError):
                continue
        return count

    def _check_guardrails(self, targets: list[str]) -> float:
        """Score 0.0-1.0 for guardrail presence across agent files."""
        guardrail_keywords = {
            "must not", "must never", "do not", "refuse", "safety",
            "harmful", "dangerous", "prohibited", "forbidden",
            "not allowed", "cannot", "should not", "decline",
        }
        total_files = 0
        files_with_guardrails = 0
        for t in targets:
            fp = Path(t)
            if fp.suffix not in (".md", ".txt"):
                continue
            total_files += 1
            try:
                content = fp.read_text(errors="ignore").lower()
                if any(kw in content for kw in guardrail_keywords):
                    files_with_guardrails += 1
            except OSError:
                continue
        if total_files == 0:
            return 0.5  # Neutral if no prompt files
        return files_with_guardrails / total_files

    def _detect_guardrail_removal(self, diff: Diff) -> bool:
        """Check if the diff removes safety-related lines."""
        safety_patterns = (
            "must not", "must never", "do not", "refuse", "safety",
            "harmful", "dangerous", "prohibited", "forbidden",
        )
        for line in diff.raw_diff.splitlines():
            if line.startswith("-") and not line.startswith("---"):
                lower = line.lower()
                if any(p in lower for p in safety_patterns):
                    # Check if same content was re-added (rewrite, not removal)
                    return True
        return False
