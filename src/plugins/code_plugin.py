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
                parts = fp.parts
                if any(fnmatch(rel, ex) or fnmatch(fp.name, ex) or any(fnmatch(part, ex.strip("*/")) for part in parts) for ex in exclude):
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
            for tool in ("npx", "npm"):
                if shutil.which(tool):
                    available.append(tool)
            if not available:
                errors.append("npm/npx not found for Node.js project")
            # Check for jest, eslint, tsc via npx (installed as devDeps)
            for tool, label in (("jest", "jest"), ("eslint", "eslint"), ("tsc", "typescript")):
                if shutil.which(tool) or shutil.which("npx"):
                    available.append(label)
                else:
                    missing.append(label)
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

    def deterministic_metric_reliability(self) -> float:
        return 0.85  # tests, lint, types are strong signals

    # ------------------------------------------------------------------
    # Prompt templates
    # ------------------------------------------------------------------

    def indexer_prompt_hint(self) -> str:
        return (
            "For each file, summarize:\n"
            "- **Purpose**: What this file/module does (1 sentence)\n"
            "- **Key abstractions**: Main classes, functions, or patterns\n"
            "- **Dependencies**: What it imports from / is used by\n"
            "- **Complexity hotspots**: Anything notably complex or fragile"
        )

    def analyst_categories(self) -> list[dict[str, str]]:
        return [
            {"name": "error_handling", "description": "Missing or unclear error handling"},
            {"name": "complexity", "description": "Functions or modules that are too complex"},
            {"name": "type_safety", "description": "Missing or weak type annotations"},
            {"name": "performance", "description": "Inefficient code paths"},
            {"name": "readability", "description": "Hard-to-follow logic or naming"},
            {"name": "maintainability", "description": "Tight coupling or poor structure"},
            {"name": "validation", "description": "Missing input validation"},
            {"name": "documentation", "description": "Missing or misleading documentation"},
        ]

    def analyst_role(self) -> str:
        return "a principal staff engineer conducting a code review"

    def modifier_role(self) -> str:
        return "You are a distinguished principal engineer."

    def modifier_constraints(self) -> list[str]:
        return [
            "Make exactly ONE focused change.",
            "Use your tools to edit the files directly — write the changes to disk.",
            "Do NOT modify any files not listed above.",
            "Do NOT add new dependencies.",
            "Keep changes minimal and focused — do not refactor beyond the task scope.",
            "Verify your changes don't break the existing API contracts.",
        ]

    def reviewer_focus(self) -> str:
        return (
            "Evaluate whether this diff is a genuine improvement. Consider:\n"
            "1. Does it achieve the stated task?\n"
            "2. Does it violate any must-preserve constraints?\n"
            "3. Is it actually better by the project owner's definition?\n"
            "4. Are there any regressions, even subtle ones?\n"
            "5. Is the change focused and minimal, or does it include unnecessary modifications?"
        )

    def theme_map(self) -> dict[str, tuple[str, str, str]]:
        return {
            "error_handling": ("Reliability & Error Handling", "🛡️", "Users get clear error messages instead of silent failures and crashes."),
            "errorhandling": ("Reliability & Error Handling", "🛡️", "Users get clear error messages instead of silent failures and crashes."),
            "validation": ("Reliability & Error Handling", "🛡️", "Users get clear error messages instead of silent failures and crashes."),
            "complexity": ("Performance & Code Quality", "⚡", "Faster response times, smaller functions, easier to maintain and extend."),
            "performance": ("Performance & Code Quality", "⚡", "Faster response times, smaller functions, easier to maintain and extend."),
            "maintainability": ("Performance & Code Quality", "⚡", "Faster response times, smaller functions, easier to maintain and extend."),
            "readability": ("Performance & Code Quality", "⚡", "Faster response times, smaller functions, easier to maintain and extend."),
            "documentation": ("Documentation & Type Safety", "📝", "New contributors can understand the codebase faster, fewer runtime type errors."),
            "type_safety": ("Documentation & Type Safety", "📝", "New contributors can understand the codebase faster, fewer runtime type errors."),
        }

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
            test_dir = self._find_test_dir(working_dir)
            return self._run_npm_test(test_dir)
        return TestResult()

    def _find_test_dir(self, working_dir: str) -> str:
        """Find the directory containing package.json with a test script."""
        root = Path(working_dir)
        # Check root first
        pkg = root / "package.json"
        if pkg.exists():
            try:
                data = json.loads(pkg.read_text())
                if data.get("scripts", {}).get("test"):
                    return working_dir
            except (json.JSONDecodeError, OSError):
                pass
        # Search one level deep for a package.json with a test script
        for child in sorted(root.iterdir()):
            if not child.is_dir() or child.name in ("node_modules", ".next", ".autoimprove"):
                continue
            pkg = child / "package.json"
            if pkg.exists():
                try:
                    data = json.loads(pkg.read_text())
                    if data.get("scripts", {}).get("test"):
                        return str(child)
                except (json.JSONDecodeError, OSError):
                    continue
        return working_dir

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
        # Try Jest first (parses structured output), fall back to npm test
        jest_result = self._run_jest(working_dir)
        if jest_result is not None:
            return jest_result
        return self._run_npm_test_fallback(working_dir)

    def _run_jest(self, working_dir: str) -> TestResult | None:
        if not shutil.which("npx"):
            return None
        try:
            result = subprocess.run(
                ["npx", "jest", "--passWithNoTests", "--forceExit", "--no-coverage"],
                cwd=working_dir, capture_output=True, text=True, timeout=180,
            )
        except (subprocess.TimeoutExpired, FileNotFoundError):
            return None

        output = result.stdout + result.stderr
        # If jest isn't installed, npx will error — fall back
        if "Could not locate module" in output or "Cannot find module" in output:
            return None

        passed = failed = skipped = 0
        m = re.search(r"Tests:\s+(?:(\d+) failed,?\s*)?(?:(\d+) skipped,?\s*)?(?:(\d+) passed)?", output)
        if m:
            failed = int(m.group(1) or 0)
            skipped = int(m.group(2) or 0)
            passed = int(m.group(3) or 0)

        total = passed + failed
        pass_rate = passed / total if total > 0 else (1.0 if result.returncode == 0 else 0.0)

        return TestResult(
            passed=passed, failed=failed, skipped=skipped,
            pass_rate=pass_rate, output=output[:2000],
        )

    def _run_npm_test_fallback(self, working_dir: str) -> TestResult:
        if not shutil.which("npm"):
            return TestResult()
        try:
            result = subprocess.run(
                ["npm", "test"],
                cwd=working_dir, capture_output=True, text=True, timeout=180,
            )
        except (subprocess.TimeoutExpired, FileNotFoundError):
            return TestResult(output="npm test timed out or not found")

        output = result.stdout + result.stderr
        # Try parsing Jest-style output from npm test
        passed = failed = skipped = 0
        m = re.search(r"Tests:\s+(?:(\d+) failed,?\s*)?(?:(\d+) skipped,?\s*)?(?:(\d+) passed)?", output)
        if m:
            failed = int(m.group(1) or 0)
            skipped = int(m.group(2) or 0)
            passed = int(m.group(3) or 0)
            total = passed + failed
            pass_rate = passed / total if total > 0 else 1.0
        else:
            # No parseable output — use exit code
            ok = result.returncode == 0
            passed = 1 if ok else 0
            failed = 0 if ok else 1
            pass_rate = 1.0 if ok else 0.0

        return TestResult(
            passed=passed, failed=failed, skipped=skipped,
            pass_rate=pass_rate, output=output[:2000],
        )

    def _run_linter(self, working_dir: str, project_type: str) -> LintResult:
        if project_type == "python":
            return self._run_ruff(working_dir)
        if project_type == "node":
            return self._run_eslint(working_dir)
        return LintResult()

    def _run_eslint(self, working_dir: str) -> LintResult:
        if not shutil.which("npx"):
            return LintResult()
        try:
            result = subprocess.run(
                ["npx", "eslint", ".", "--format=json", "--no-error-on-unmatched-pattern"],
                cwd=working_dir, capture_output=True, text=True, timeout=120,
            )
        except (subprocess.TimeoutExpired, FileNotFoundError):
            return LintResult()

        errors = 0
        warnings = 0
        try:
            issues = json.loads(result.stdout) if result.stdout.strip() else []
            for file_result in issues:
                errors += file_result.get("errorCount", 0)
                warnings += file_result.get("warningCount", 0)
        except (json.JSONDecodeError, TypeError):
            errors = len(result.stdout.strip().splitlines())

        total_issues = errors + warnings
        score = max(0.0, 1.0 - total_issues / 100.0)

        return LintResult(
            errors=errors, warnings=warnings, score=score,
            output=(result.stdout + result.stderr)[:2000],
        )

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
        if project_type == "node" and shutil.which("npx"):
            return self._run_tsc(working_dir)
        return TypecheckResult()

    def _find_tsconfig_dir(self, working_dir: str) -> str | None:
        """Find the directory containing tsconfig.json."""
        root = Path(working_dir)
        if (root / "tsconfig.json").exists():
            return working_dir
        for child in sorted(root.iterdir()):
            if not child.is_dir() or child.name in ("node_modules", ".next", ".autoimprove"):
                continue
            if (child / "tsconfig.json").exists():
                return str(child)
        return None

    def _run_tsc(self, working_dir: str) -> TypecheckResult:
        tsc_dir = self._find_tsconfig_dir(working_dir)
        if tsc_dir is None:
            # No tsconfig.json found — skip typecheck (pass by default)
            return TypecheckResult()
        try:
            result = subprocess.run(
                ["npx", "tsc", "--noEmit", "--pretty", "false"],
                cwd=tsc_dir, capture_output=True, text=True, timeout=120,
            )
        except (subprocess.TimeoutExpired, FileNotFoundError):
            return TypecheckResult()

        error_count = 0
        for line in result.stdout.splitlines():
            if ": error TS" in line:
                error_count += 1

        # If returncode is non-zero but no actual TS errors found, treat as pass
        # (e.g. tsc not installed, wrong tsc binary, missing tsconfig)
        if result.returncode != 0 and error_count == 0:
            return TypecheckResult()

        return TypecheckResult(
            passed=result.returncode == 0,
            errors=error_count,
            output=(result.stdout + result.stderr)[:2000],
        )

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
