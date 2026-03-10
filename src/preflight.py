"""Preflight — validate the environment before starting a run.

Runs ALL checks (no short-circuit) and reports every failure at once.
Fatal failures block the run; warnings are informational.
"""

from __future__ import annotations

import re
import shutil
import sys
from dataclasses import dataclass, field
from pathlib import Path

from src import git_ops
from src.config import Config, validate_config


@dataclass
class PreflightCheck:
    """Result of a single preflight check."""

    name: str
    passed: bool
    message: str
    fatal: bool


@dataclass
class PreflightResult:
    """Aggregate result of all preflight checks."""

    passed: bool = True
    checks: list[PreflightCheck] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


def _add(result: PreflightResult, name: str, ok: bool, msg: str, fatal: bool) -> None:
    result.checks.append(PreflightCheck(name=name, passed=ok, message=msg, fatal=fatal))
    if not ok:
        if fatal:
            result.errors.append(msg)
            result.passed = False
        else:
            result.warnings.append(msg)


def run_preflight(config: Config, repo_path: str) -> PreflightResult:
    """Run all preflight checks.  Returns a ``PreflightResult``."""
    result = PreflightResult()
    rp = str(Path(repo_path).resolve())

    # 1. Git repo
    ok = git_ops.ensure_git_repo(rp)
    _add(result, "git_repo", ok, "Not a git repository." if not ok else "Git repository detected.", fatal=True)

    # 2. Git clean
    if ok:
        clean = git_ops.is_repo_clean(rp)
        _add(
            result,
            "git_clean",
            clean,
            "Uncommitted changes detected. Commit or stash first." if not clean else "Working tree clean.",
            fatal=True,
        )
    else:
        _add(result, "git_clean", False, "Skipped (not a git repo).", fatal=True)

    # 3. Python version
    v = sys.version_info
    py_ok = v >= (3, 10)
    _add(
        result,
        "python_version",
        py_ok,
        f"Python {v.major}.{v.minor} found." if py_ok else f"Python 3.10+ required, found {v.major}.{v.minor}.",
        fatal=True,
    )

    # 4. Agent command
    agent_ok = shutil.which(config.agent_command.split()[0]) is not None
    _add(
        result,
        "agent_command",
        agent_ok,
        f"Agent '{config.agent_command}' found." if agent_ok else f"Agent '{config.agent_command}' not in PATH.",
        fatal=True,
    )

    # 5. Target paths
    for tp in config.target_paths:
        p = Path(rp) / tp
        exists = p.exists()
        _add(
            result,
            f"target_path:{tp}",
            exists,
            f"Target '{tp}' exists." if exists else f"Target '{tp}' does not exist.",
            fatal=True,
        )

    # 6. Overlap check
    for tp in config.target_paths:
        if tp in config.exclude_paths:
            _add(result, f"overlap:{tp}", False, f"'{tp}' is in both target and exclude paths.", fatal=False)

    # 7. Config validation
    warnings = validate_config(config)
    if warnings:
        for w in warnings:
            _add(result, "config_warning", False, w, fatal=False)
    else:
        _add(result, "config_valid", True, "Config valid.", fatal=False)

    # 8. Disk space
    try:
        st = shutil.disk_usage(rp)
        free_mb = st.free // (1024 * 1024)
        low = free_mb < 1024
        _add(
            result,
            "disk_space",
            not low,
            f"Disk: {free_mb}MB free." if not low else f"Low disk space: {free_mb}MB free.",
            fatal=False,
        )
    except OSError:
        _add(result, "disk_space", False, "Could not check disk space.", fatal=False)

    # 9. program.md
    has_program = (Path(rp) / "program.md").exists()
    _add(
        result,
        "program_md",
        has_program,
        "program.md found." if has_program else "No program.md — agent will have no instructions.",
        fatal=False,
    )

    # 10. Secret scan
    for tp in config.target_paths:
        target = Path(rp) / tp
        if not target.exists():
            continue
        files = target.rglob("*") if target.is_dir() else [target]
        for fp in files:
            if not fp.is_file():
                continue
            try:
                text = fp.read_text(errors="ignore")
            except Exception:
                continue
            for pattern in config.secret_patterns:
                if re.search(pattern, text):
                    rel = fp.relative_to(rp)
                    _add(result, f"secret:{rel}", False, f"Potential secret in {rel} (pattern: {pattern}).", fatal=False)

    # 11. Worktree path
    ai_dir = Path(rp) / ".autoimprove"
    try:
        ai_dir.mkdir(parents=True, exist_ok=True)
        _add(result, "worktree_path", True, "Run directory writable.", fatal=False)
    except OSError as e:
        _add(result, "worktree_path", False, f"Cannot create .autoimprove/: {e}", fatal=True)

    return result
