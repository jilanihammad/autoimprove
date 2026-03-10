"""Policy enforcement — guardrails applied to every candidate diff.

Runs BEFORE hard gates (cheaper, catches obvious problems).  All checks
run without short-circuiting so every violation is reported at once.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from fnmatch import fnmatch
from pathlib import PurePosixPath

from src.config import Config
from src.plugins.base import GuardrailConfig
from src.types import Diff

# Well-known dependency files
_DEPENDENCY_FILES = frozenset(
    {
        "package.json",
        "package-lock.json",
        "yarn.lock",
        "pnpm-lock.yaml",
        "requirements.txt",
        "Pipfile",
        "Pipfile.lock",
        "pyproject.toml",
        "Cargo.toml",
        "Cargo.lock",
        "go.mod",
        "go.sum",
        "Gemfile",
        "Gemfile.lock",
    }
)

# Default secret patterns (always checked in addition to config)
_DEFAULT_SECRET_PATTERNS = [
    r"AKIA[0-9A-Z]{16}",
    r"-----BEGIN (RSA |EC |DSA )?PRIVATE KEY-----",
    r"ghp_[a-zA-Z0-9]{36}",
    r"sk-[a-zA-Z0-9]{48}",
]


@dataclass
class PolicyViolation:
    """A single policy violation."""

    rule: str
    severity: str  # "fatal" or "warning"
    file: str | None = None
    line: int | None = None
    message: str = ""


@dataclass
class PolicyResult:
    """Aggregate result of all policy checks."""

    passed: bool = True
    violations: list[PolicyViolation] = field(default_factory=list)
    fatal_count: int = 0
    warning_count: int = 0


def _fail(result: PolicyResult, rule: str, msg: str, *, file: str | None = None) -> None:
    result.violations.append(PolicyViolation(rule=rule, severity="fatal", file=file, message=msg))
    result.fatal_count += 1
    result.passed = False


def _warn(result: PolicyResult, rule: str, msg: str, *, file: str | None = None) -> None:
    result.violations.append(PolicyViolation(rule=rule, severity="warning", file=file, message=msg))
    result.warning_count += 1


def check_policy(
    diff: Diff,
    config: Config,
    plugin_guardrails: GuardrailConfig | None = None,
) -> PolicyResult:
    """Validate a candidate diff against all policy rules."""
    result = PolicyResult()
    pg = plugin_guardrails or GuardrailConfig()

    # 1. Empty diff
    if not diff.files_changed:
        _fail(result, "empty_diff", "No changes were made.")
        return result  # nothing else to check

    # 2. Diff size
    total = diff.lines_added + diff.lines_removed
    limit = pg.max_diff_lines if pg.max_diff_lines is not None else config.max_diff_lines
    if total > limit:
        _fail(result, "diff_size", f"Diff too large: {total} lines (max {limit}).")

    # 3. Protected paths
    all_protected = list(config.protected_paths) + list(pg.protected_patterns)
    for f in diff.files_changed:
        name = PurePosixPath(f).name
        for pat in all_protected:
            if fnmatch(f, pat) or fnmatch(name, pat):
                _fail(result, "protected_path", f"Protected path modified: {f} (pattern: {pat})", file=f)
                break

    # 4. Forbidden extensions
    for f in diff.files_changed:
        ext = PurePosixPath(f).suffix
        if ext in pg.forbidden_extensions:
            _fail(result, "forbidden_extension", f"Forbidden file type: {f} ({ext})", file=f)

    # 5. Secret patterns (scan added lines only)
    patterns = list(config.secret_patterns) + _DEFAULT_SECRET_PATTERNS
    compiled = [re.compile(p) for p in patterns]
    for line in diff.raw_diff.splitlines():
        if not line.startswith("+") or line.startswith("+++"):
            continue
        for pat, regex in zip(patterns, compiled):
            if regex.search(line):
                _fail(result, "secret_detected", f"Potential secret in diff (pattern: {pat}).")
                break

    # 6. Dependency changes
    if not config.allow_dependency_changes:
        for f in diff.files_changed:
            name = PurePosixPath(f).name
            if name in _DEPENDENCY_FILES:
                _fail(result, "dependency_change", f"Dependency file modified: {f}", file=f)

    # 7. Excluded paths
    for f in diff.files_changed:
        for ep in config.exclude_paths:
            if f.startswith(ep) or fnmatch(f, ep):
                _fail(result, "excluded_path", f"File in excluded path modified: {f}", file=f)
                break

    return result
