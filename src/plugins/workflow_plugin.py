"""Workflow evaluator plugin — n8n, Step Functions, Lambda pipelines.

Mixed confidence profile: some deterministic signals (schema validation)
plus LLM judgment for architecture quality.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from fnmatch import fnmatch
from pathlib import Path

import yaml

from src.plugins.base import EvaluatorPlugin, GuardrailConfig, PluginPreflightResult
from src.types import BaselineSnapshot, ConfidenceProfile, DeltaSummary, Diff, GateResult, SoftEvalResult


class WorkflowPlugin(EvaluatorPlugin):
    """Evaluates AI workflow artifacts with mixed deterministic/judgment signals."""

    @property
    def name(self) -> str:
        return "workflow"

    @property
    def confidence_profile(self) -> ConfidenceProfile:
        return ConfidenceProfile.MEDIUM

    @property
    def description(self) -> str:
        return "Evaluates workflow artifacts: n8n, Step Functions, Lambda pipelines."

    def discover_targets(self, paths: list[str], exclude: list[str]) -> list[str]:
        targets: list[str] = []
        for p in paths:
            pp = Path(p)
            files = pp.rglob("*") if pp.is_dir() else [pp]
            for fp in files:
                if not fp.is_file():
                    continue
                rel = str(fp)
                if any(fnmatch(rel, ex) or fnmatch(fp.name, ex) for ex in exclude):
                    continue
                wtype = self._detect_workflow_type(fp)
                if wtype != "unknown":
                    targets.append(str(fp))
        return targets

    def preflight(self, targets: list[str]) -> PluginPreflightResult:
        return PluginPreflightResult(
            passed=True,
            available_tools=["json", "yaml"],
            warnings=["Workflow evaluation relies heavily on LLM judgment"],
        )

    def baseline(self, targets: list[str], working_dir: str) -> BaselineSnapshot:
        metrics: dict[str, float] = {}
        total_nodes = 0
        total_error_handling = 0.0

        for t in targets:
            wtype = self._detect_workflow_type(Path(t))
            try:
                with open(t) as f:
                    content = json.load(f) if t.endswith(".json") else yaml.safe_load(f)
            except Exception:
                continue

            if wtype == "n8n":
                nodes = content.get("nodes", [])
                total_nodes += len(nodes)
                total_error_handling += self._count_error_handling(content, "n8n")

        metrics["total_nodes"] = float(total_nodes)
        metrics["error_handling_coverage"] = total_error_handling / max(len(targets), 1)
        metrics["workflow_count"] = float(len(targets))

        return BaselineSnapshot(
            plugin_name=self.name,
            timestamp=datetime.now(timezone.utc).isoformat(),
            metrics=metrics,
            raw_data={},
            targets=targets,
        )

    def hard_gates(self, diff: Diff, targets: list[str], working_dir: str) -> GateResult:
        gates: dict[str, bool] = {}
        failures: list[str] = []

        for t in targets:
            if not Path(t).exists():
                continue
            wtype = self._detect_workflow_type(Path(t))
            if wtype == "n8n":
                valid, errs = self._validate_n8n_schema(t)
                gates[f"schema_valid:{Path(t).name}"] = valid
                if not valid:
                    failures.extend(errs)
            elif wtype == "step_functions":
                valid, errs = self._validate_step_functions_schema(t)
                gates[f"schema_valid:{Path(t).name}"] = valid
                if not valid:
                    failures.extend(errs)

        return GateResult(
            all_passed=all(gates.values()) if gates else True,
            gates=gates,
            failures=failures,
        )

    def soft_evaluate(
        self, diff: Diff, targets: list[str], criteria: dict, working_dir: str
    ) -> SoftEvalResult:
        scores: dict[str, float] = {}
        total_eh = 0.0
        count = 0

        for t in targets:
            if not Path(t).exists():
                continue
            try:
                with open(t) as f:
                    content = json.load(f) if t.endswith(".json") else yaml.safe_load(f)
                wtype = self._detect_workflow_type(Path(t))
                total_eh += self._count_error_handling(content, wtype)
                count += 1
            except Exception:
                continue

        scores["error_handling_coverage"] = total_eh / max(count, 1)
        composite = scores["error_handling_coverage"]

        return SoftEvalResult(scores=scores, has_deterministic=True, composite=composite)

    def summarize_delta(self, baseline: BaselineSnapshot, current: BaselineSnapshot) -> DeltaSummary:
        improved: dict[str, tuple[float, float]] = {}
        regressed: dict[str, tuple[float, float]] = {}
        unchanged: list[str] = []

        for key in baseline.metrics:
            before = baseline.metrics[key]
            after = current.metrics.get(key, before)
            if abs(after - before) < 0.001:
                unchanged.append(key)
            elif after > before:
                improved[key] = (before, after)
            else:
                regressed[key] = (before, after)

        return DeltaSummary(
            plugin_name=self.name,
            improved=improved,
            regressed=regressed,
            unchanged=unchanged,
            summary_text=f"{len(improved)} improved, {len(regressed)} regressed",
        )

    def guardrails(self) -> GuardrailConfig:
        return GuardrailConfig(protected_patterns=["*.lock"])

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _detect_workflow_type(self, fp: Path) -> str:
        if fp.suffix == ".json":
            try:
                with open(fp) as f:
                    data = json.load(f)
                if isinstance(data, dict):
                    if "nodes" in data and "connections" in data:
                        return "n8n"
                    if "States" in data or "StartAt" in data:
                        return "step_functions"
            except Exception:
                pass
        elif fp.suffix in (".yaml", ".yml"):
            try:
                with open(fp) as f:
                    data = yaml.safe_load(f)
                if isinstance(data, dict) and ("States" in data or "StartAt" in data):
                    return "step_functions"
            except Exception:
                pass
        return "unknown"

    def _validate_n8n_schema(self, path: str) -> tuple[bool, list[str]]:
        errors: list[str] = []
        try:
            with open(path) as f:
                data = json.load(f)
        except json.JSONDecodeError as e:
            return False, [f"Invalid JSON: {e}"]

        if not isinstance(data, dict):
            return False, ["Root must be an object"]
        if "nodes" not in data:
            errors.append("Missing 'nodes' key")
        if "connections" not in data:
            errors.append("Missing 'connections' key")

        nodes = data.get("nodes", [])
        if isinstance(nodes, list):
            for i, node in enumerate(nodes):
                if not isinstance(node, dict):
                    errors.append(f"Node {i} is not an object")
                elif "type" not in node:
                    errors.append(f"Node {i} missing 'type'")

        return len(errors) == 0, errors

    def _validate_step_functions_schema(self, path: str) -> tuple[bool, list[str]]:
        errors: list[str] = []
        try:
            with open(path) as f:
                data = json.load(f) if path.endswith(".json") else yaml.safe_load(f)
        except Exception as e:
            return False, [f"Parse error: {e}"]

        if not isinstance(data, dict):
            return False, ["Root must be an object"]
        if "StartAt" not in data:
            errors.append("Missing 'StartAt'")
        if "States" not in data:
            errors.append("Missing 'States'")

        return len(errors) == 0, errors

    def _count_error_handling(self, content: dict, wtype: str) -> float:
        if wtype == "n8n":
            nodes = content.get("nodes", [])
            if not nodes:
                return 0.0
            handled = sum(1 for n in nodes if n.get("continueOnFail") or n.get("onError"))
            return handled / len(nodes)
        if wtype == "step_functions":
            states = content.get("States", {})
            if not states:
                return 0.0
            handled = sum(1 for s in states.values() if isinstance(s, dict) and ("Catch" in s or "Retry" in s))
            return handled / len(states)
        return 0.0
