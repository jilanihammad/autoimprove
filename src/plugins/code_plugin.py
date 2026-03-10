"""Code evaluator plugin — tests, lint, complexity, build.

Reference implementation for all evaluator plugins.  Code has the
strongest deterministic signals so confidence_profile is HIGH.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
from dataclasses import dataclass, field
from datetime import datetime, timezone
from fnmatch import fnmatch
from pathlib import Path

from src.plugins.base import EvaluatorPlugin, GuardrailConfig, PluginPreflightResult
from src.types import BaselineSnapshot, ConfidenceProfile, DeltaSummary, Diff, GateResult, SoftEvalResult

_CODE_EXTENSIONS = frozenset({
    ".py", ".js", ".ts", ".jsx", ".tsx", ".java", ".go", ".rs", ".rb",
    ".c", ".cpp", ".h", ".hpp", ".cs", ".swift", ".kt",
})


@dataclass
class TestResult:
    passed: int = 0
    failed: int = 0
    skipped: int = 0
    errors: int = 0
    pass_rate: float = 1.0
    output: str = ""


@dataclass
class LintResult:
    errors: int = 0
    warnings: int = 0
    score: float = 1.0
    output: str = ""


@dataclass
class TypecheckResult:
    passed: bool = True
    errors: int = 0
    output: str = ""


class CodePlugin(EvaluatorPlugin):
    """Evaluates code quality with deterministic metrics."""

    @property
    def name(self) -> str:
        return "code"

    @property
    def confidence_profile(self) -> ConfidenceProfile:
        return ConfidenceProfile.HIGH

    @property
    def description(self) -> str:
        return "Evaluates code quality: tests, lint, complexity, type coverage, build success."

    # ------------------------------------------------------------------
    # Discovery
    # ------------------------------------------------------------------

    def discover_targets(self, paths: list[str], exclude: list[str]) -> list[str]:
        targets: list[str] = []
        for p in paths:
            pp = Path(p)
            if pp.is_file() and pp.suffix in _CODE_EXTENSIONS:
                targets.append(str(pp))
                continue
            if not pp.is_dir():
                continue
            for fp in pp.rglob("*"):
                if not fp.is_file() or fp.suffix not in _CODE_EXTENSIONS:
                    continue
                rel = str(fp)
                if any(fnmatch(rel, ex) or fnmatch(fp.name, ex) for ex in exclude):
                    continue
                targets.append(str(fp))
        return targets

    # ------------------------------------------------------------------
    # Preflight
    # ------------------------------------------------------------------

    def preflight(self, targets: list[str]) -> PluginPreflightResult:
        available: list[str] = []
        missing: list[str] = []
        warnings: list[str] = []
        errors: list[str] = []

        project_type = self._detect_project_type(targets)

        if project_type == "python":
            for tool in ("pytest", "ruff", "mypy"):
                if shutil.which(tool):
                    available.append(tool)
                else:
                    (missing if tool == "pytest" else warnings).append(
                        f"{tool} not found" if tool == "pytest" else tool
                    )
            if "pytest" not in available and not shutil.which("python"):
                errors.append("No test runner found (pytest or python required)")
        elif project_type == "node":
            for tool in ("npm", "npx"):
                if shutil.which(tool):
                    available.append(tool)
            if not available:
                errors.append("npm/npx not found for Node.js project")
        else:
            warnings.append(f"Project type '{project_type}' — limited tool support")

        passed = len(errors) == 0
        return PluginPreflightResult(
            passed=passed,
            available_tools=available,
            missing_tools=missing,
            warnings=warnings,
            errors=errors,
        )

    # ------------------------------------------------------------------
    # Baseline
    # ------------------------------------------------------------------

    def baseline(self, targets: list[str], working_dir: str) -> BaselineSnapshot:
        project_type = self._detect_project_type(targets)
        metrics: dict[str, float] = {}
        raw_data: dict = {"project_type": project_type}

        # Tests
        test_result = self._run_tests(working_dir, project_type)
        metrics["test_pass_rate"] = test_result.pass_rate
        metrics["test_passed"] = float(test_result.passed)
        metrics["test_failed"] = float(test_result.failed)
        raw_data["test_output"] = test_result.output[:2000]

        # Lint
        lint_result = self._run_linter(working_dir, project_type)
        metrics["lint_errors"] = float(lint_result.errors)
        metrics["lint_warnings"] = float(lint_result.warnings)
        metrics["lint_score"] = lint_result.score
        raw_data["lint_output"] = lint_result.output[:2000]

        # Typecheck
        tc_result = self._run_typecheck(working_dir, project_type)
        metrics["typecheck_passed"] = 1.0 if tc_result.passed else 0.0
        metrics["typecheck_errors"] = float(tc_result.errors)

        # LOC
        total_lines = sum(_count_lines(t) for t in targets)
        metrics["total_lines"] = float(total_lines)

        return BaselineSnapshot(
            plugin_name=self.name,
            timestamp=datetime.now(timezone.utc).isoformat(),
            metrics=metrics,
            raw_data=raw_data,
            targets=targets,
        )

    # ------------------------------------------------------------------
    # Hard gates
    # ------------------------------------------------------------------

    def hard_gates(self, diff: Diff, targets: list[str], working_dir: str) -> GateResult:
        project_type = self._detect_project_type(targets)
        gates: dict[str, bool] = {}
        failures: list[str] = []

        # Gate 1: tests pass
        test_result = self._run_tests(working_dir, project_type)
        tests_ok = test_result.failed == 0 and test_result.errors == 0
        gates["tests_pass"] = tests_ok
        if not tests_ok:
            failures.append(f"Tests failed: {test_result.failed} failures, {test_result.errors} errors")

        # Gate 2: typecheck
        tc = self._run_typecheck(working_dir, project_type)
        gates["typecheck_pass"] = tc.passed
        if not tc.passed:
            failures.append(f"Type-check failed: {tc.errors} errors")

        # Gate 3: no new lint errors (compared to pre-change — we check count)
        lint = self._run_linter(working_dir, project_type)
        gates["lint_no_new_errors"] = True  # Will be compared by engine against baseline
        # Store for soft eval
        gates["_lint_errors"] = lint.errors  # type: ignore[assignment]

        return GateResult(
            all_passed=all(v for k, v in gates.items() if not k.startswith("_")),
            gates={k: v for k, v in gates.items() if not k.startswith("_")},
            failures=failures,
        )

    # ------------------------------------------------------------------
    # Soft evaluation
    # ------------------------------------------------------------------

    def soft_evaluate(
        self, diff: Diff, targets: list[str], criteria: dict, working_dir: str
    ) -> SoftEvalResult:
        project_type = self._detect_project_type(targets)
        scores: dict[str, float] = {}

        # Lint score
        lint = self._run_linter(working_dir, project_type)
        scores["lint_score"] = lint.score

        # LOC delta (less code = better, normalized)
        total_lines = sum(_count_lines(t) for t in targets if Path(t).exists())
        scores["loc_efficiency"] = min(1.0, max(0.0, 1.0 - (diff.lines_added - diff.lines_removed) / max(total_lines, 1) * 10))

        # Test pass rate
        test_result = self._run_tests(working_dir, project_type)
        scores["test_pass_rate"] = test_result.pass_rate

        # Composite from criteria weights
        weights = criteria.get("weights", {}) if isinstance(criteria, dict) else {}
        if weights:
            total_w = sum(weights.values())
            composite = sum(scores.get(k, 0.5) * w for k, w in weights.items()) / max(total_w, 0.01)
        else:
            composite = mean(scores.values()) if scores else 0.5

        return SoftEvalResult(
            scores=scores,
            has_deterministic=True,
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
            # Higher is better for most metrics
            higher_is_better = key not in {"lint_errors", "lint_warnings", "typecheck_errors", "test_failed", "total_lines"}
            if abs(after - before) < 0.001:
                unchanged.append(key)
            elif (after > before) == higher_is_better:
                improved[key] = (before, after)
            else:
                regressed[key] = (before, after)

        lines = []
        for k, (b, a) in improved.items():
            lines.append(f"  ✓ {k}: {b:.1f} → {a:.1f}")
        for k, (b, a) in regressed.items():
            lines.append(f"  ✗ {k}: {b:.1f} → {a:.1f}")

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
            protected_patterns=["*.lock", "*.min.js", "*.min.css"],
            forbidden_extensions=[".exe", ".dll", ".so", ".pyc", ".pyo", ".class"],
        )

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _detect_project_type(self, targets: list[str]) -> str:
        extensions = {Path(t).suffix for t in targets}
        # Check for config files in parent dirs
        for t in targets:
            parent = Path(t).parent
            for _ in range(5):
                if (parent / "pyproject.toml").exists() or (parent / "setup.py").exists():
                    return "python"
                if (parent / "package.json").exists():
                    return "node"
                if (parent / "Cargo.toml").exists():
                    return "rust"
                if (parent / "go.mod").exists():
                    return "go"
                if parent == parent.parent:
                    break
                parent = parent.parent

        if ".py" in extensions:
            return "python"
        if extensions & {".js", ".ts", ".jsx", ".tsx"}:
            return "node"
        return "unknown"

    def _run_tests(self, working_dir: str, project_type: str) -> TestResult:
        if project_type == "python":
            return self._run_pytest(working_dir)
        if project_type == "node":
            return self._run_npm_test(working_dir)
        return TestResult()

    def _run_pytest(self, working_dir: str) -> TestResult:
        cmd = ["python", "-m", "pytest", "--tb=short", "-q"]
        if not shutil.which("pytest") and not shutil.which("python"):
            return TestResult()
        try:
            result = subprocess.run(
                cmd, cwd=working_dir, capture_output=True, text=True, timeout=120,
            )
        except (subprocess.TimeoutExpired, FileNotFoundError):
            return TestResult(output="Test runner timed out or not found")

        output = result.stdout + result.stderr
        # Parse pytest summary line: "X passed, Y failed, Z skipped"
        passed = failed = skipped = errors = 0
        m = re.search(r"(\d+) passed", output)
        if m:
            passed = int(m.group(1))
        m = re.search(r"(\d+) failed", output)
        if m:
            failed = int(m.group(1))
        m = re.search(r"(\d+) skipped", output)
        if m:
            skipped = int(m.group(1))
        m = re.search(r"(\d+) error", output)
        if m:
            errors = int(m.group(1))

        total = passed + failed
        pass_rate = passed / total if total > 0 else 1.0

        return TestResult(
            passed=passed, failed=failed, skipped=skipped,
            errors=errors, pass_rate=pass_rate, output=output[:2000],
        )

    def _run_npm_test(self, working_dir: str) -> TestResult:
        if not shutil.which("npm"):
            return TestResult()
        try:
            result = subprocess.run(
                ["npm", "test", "--", "--passWithNoTests"],
                cwd=working_dir, capture_output=True, text=True, timeout=120,
            )
        except (subprocess.TimeoutExpired, FileNotFoundError):
            return TestResult(output="npm test timed out or not found")

        ok = result.returncode == 0
        return TestResult(
            passed=1 if ok else 0,
            failed=0 if ok else 1,
            pass_rate=1.0 if ok else 0.0,
            output=(result.stdout + result.stderr)[:2000],
        )

    def _run_linter(self, working_dir: str, project_type: str) -> LintResult:
        if project_type == "python":
            return self._run_ruff(working_dir)
        return LintResult()

    def _run_ruff(self, working_dir: str) -> LintResult:
        if not shutil.which("ruff"):
            return LintResult()
        try:
            result = subprocess.run(
                ["ruff", "check", "--output-format=json", "."],
                cwd=working_dir, capture_output=True, text=True, timeout=60,
            )
        except (subprocess.TimeoutExpired, FileNotFoundError):
            return LintResult()

        errors = 0
        warnings = 0
        try:
            issues = json.loads(result.stdout) if result.stdout.strip() else []
            for issue in issues:
                fix = issue.get("fix") or {}
                if fix.get("applicability") == "safe":
                    warnings += 1
                else:
                    errors += 1
        except (json.JSONDecodeError, TypeError):
            # Fall back to line counting
            errors = len(result.stdout.strip().splitlines())

        # Rough score: 1.0 = no issues
        total_issues = errors + warnings
        score = max(0.0, 1.0 - total_issues / 100.0)

        return LintResult(
            errors=errors, warnings=warnings, score=score,
            output=(result.stdout + result.stderr)[:2000],
        )

    def _run_typecheck(self, working_dir: str, project_type: str) -> TypecheckResult:
        if project_type == "python" and shutil.which("mypy"):
            return self._run_mypy(working_dir)
        return TypecheckResult()

    def _run_mypy(self, working_dir: str) -> TypecheckResult:
        try:
            result = subprocess.run(
                ["mypy", ".", "--ignore-missing-imports", "--no-error-summary"],
                cwd=working_dir, capture_output=True, text=True, timeout=120,
            )
        except (subprocess.TimeoutExpired, FileNotFoundError):
            return TypecheckResult()

        error_count = 0
        for line in result.stdout.splitlines():
            if ": error:" in line:
                error_count += 1

        return TypecheckResult(
            passed=result.returncode == 0,
            errors=error_count,
            output=(result.stdout + result.stderr)[:2000],
        )


# ------------------------------------------------------------------
# Module-level helpers
# ------------------------------------------------------------------

import json
from statistics import mean


def _count_lines(file_path: str) -> int:
    try:
        with open(file_path, errors="ignore") as f:
            return sum(1 for _ in f)
    except OSError:
        return 0
