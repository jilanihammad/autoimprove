"""Git operations — worktree management, commits, tags, reverts, diffs.

All functions accept explicit paths and never rely on the process working
directory.  Every subprocess call goes through ``_run_git`` which raises
``GitError`` on failure.
"""

from __future__ import annotations

import hashlib
import os
import subprocess
from datetime import datetime, timezone
from pathlib import Path

from src.types import Diff


# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------


class GitError(Exception):
    """Raised when a git command fails."""

    def __init__(self, command: str, stderr: str, returncode: int) -> None:
        self.command = command
        self.stderr = stderr
        self.returncode = returncode
        super().__init__(f"git {command} failed (rc={returncode}): {stderr.strip()}")


# ---------------------------------------------------------------------------
# Internal helper
# ---------------------------------------------------------------------------


def _run_git(args: list[str], cwd: str, timeout: int = 60) -> subprocess.CompletedProcess[str]:
    """Run a git command and return the result.  Raises ``GitError`` on failure."""
    cmd = ["git", *args]
    try:
        result = subprocess.run(
            cmd,
            cwd=cwd,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired as exc:
        raise GitError(" ".join(args), f"timed out after {timeout}s", -1) from exc

    if result.returncode != 0:
        raise GitError(" ".join(args), result.stderr, result.returncode)
    return result


# ---------------------------------------------------------------------------
# Run ID
# ---------------------------------------------------------------------------


def generate_run_id() -> str:
    """Generate a unique, sortable run identifier.

    Format: ``YYYYMMDD-HHMMSS-<6hex>``  (e.g. ``20260310-143022-a7f3b2``).
    """
    now = datetime.now(timezone.utc)
    ts = now.strftime("%Y%m%d-%H%M%S")
    raw = f"{ts}-{os.getpid()}-{id(now)}".encode()
    short_hash = hashlib.sha256(raw).hexdigest()[:6]
    return f"{ts}-{short_hash}"


# ---------------------------------------------------------------------------
# Repository queries
# ---------------------------------------------------------------------------


def ensure_git_repo(path: str) -> bool:
    """Return ``True`` if *path* is inside a git repository."""
    try:
        _run_git(["rev-parse", "--is-inside-work-tree"], cwd=path)
        return True
    except (GitError, FileNotFoundError):
        return False


def is_repo_clean(path: str) -> bool:
    """Return ``True`` if the working tree has no uncommitted changes."""
    result = _run_git(["status", "--porcelain"], cwd=path)
    return result.stdout.strip() == ""


def get_current_branch(path: str) -> str:
    """Return the name of the current branch."""
    result = _run_git(["rev-parse", "--abbrev-ref", "HEAD"], cwd=path)
    return result.stdout.strip()


def get_head_sha(path: str, short: bool = True) -> str:
    """Return the HEAD commit SHA (8-char short by default)."""
    args = ["rev-parse", "HEAD"]
    if short:
        args = ["rev-parse", "--short=8", "HEAD"]
    result = _run_git(args, cwd=path)
    return result.stdout.strip()


# ---------------------------------------------------------------------------
# Worktree management
# ---------------------------------------------------------------------------


def create_worktree(repo_path: str, worktree_path: str, branch_name: str) -> str:
    """Create a new git worktree with a dedicated branch.

    The worktree is created from the current HEAD of *repo_path*.

    Returns:
        The absolute worktree path.

    Raises:
        GitError: if worktree creation fails.
    """
    abs_wt = str(Path(worktree_path).resolve())
    _run_git(["worktree", "add", "-b", branch_name, abs_wt, "HEAD"], cwd=repo_path)
    return abs_wt


def remove_worktree(repo_path: str, worktree_path: str) -> None:
    """Remove a worktree and prune stale entries."""
    abs_wt = str(Path(worktree_path).resolve())
    try:
        _run_git(["worktree", "remove", abs_wt, "--force"], cwd=repo_path)
    except GitError:
        pass  # may already be removed
    _run_git(["worktree", "prune"], cwd=repo_path)


# ---------------------------------------------------------------------------
# Commit / tag / revert
# ---------------------------------------------------------------------------


def commit(worktree_path: str, message: str, files: list[str] | None = None) -> str:
    """Stage and commit changes.  Returns the new commit SHA.

    If *files* is ``None``, stages everything (``git add -A``).
    """
    if files is None:
        _run_git(["add", "-A"], cwd=worktree_path)
    else:
        _run_git(["add", "--"] + files, cwd=worktree_path)

    _run_git(["commit", "-m", message], cwd=worktree_path)
    return get_head_sha(worktree_path, short=False)


def tag(worktree_path: str, tag_name: str) -> None:
    """Create a lightweight tag at the current HEAD."""
    _run_git(["tag", tag_name], cwd=worktree_path)


def revert_to_commit(worktree_path: str, commit_sha: str) -> None:
    """Hard-reset the worktree to *commit_sha*."""
    _run_git(["reset", "--hard", commit_sha], cwd=worktree_path)


# ---------------------------------------------------------------------------
# Diff
# ---------------------------------------------------------------------------


def _parse_diff_stat(stat_output: str) -> tuple[list[str], int, int]:
    """Parse ``git diff --numstat`` output into (files, added, removed)."""
    files: list[str] = []
    added = 0
    removed = 0
    for line in stat_output.strip().splitlines():
        parts = line.split("\t")
        if len(parts) >= 3:
            a, r, f = parts[0], parts[1], parts[2]
            files.append(f)
            # binary files show "-" for counts
            added += int(a) if a != "-" else 0
            removed += int(r) if r != "-" else 0
    return files, added, removed


def get_diff(worktree_path: str, from_ref: str, to_ref: str | None = None) -> Diff:
    """Return a ``Diff`` between *from_ref* and *to_ref* (or the working tree).

    When *to_ref* is ``None`` (the default), the diff includes uncommitted
    working-tree changes — i.e. ``git diff <from_ref>``.  This is critical
    because agents edit files on disk without committing.
    """
    diff_args = ["diff", "--numstat", from_ref]
    if to_ref is not None:
        diff_args.append(to_ref)
    stat = _run_git(diff_args, cwd=worktree_path)
    files, added, removed = _parse_diff_stat(stat.stdout)

    raw_args = ["diff", from_ref]
    if to_ref is not None:
        raw_args.append(to_ref)
    raw = _run_git(raw_args, cwd=worktree_path)
    return Diff(
        files_changed=files,
        lines_added=added,
        lines_removed=removed,
        raw_diff=raw.stdout,
    )


def get_diff_staged(worktree_path: str) -> Diff:
    """Return a ``Diff`` for currently staged changes."""
    stat = _run_git(["diff", "--cached", "--numstat"], cwd=worktree_path)
    files, added, removed = _parse_diff_stat(stat.stdout)

    raw = _run_git(["diff", "--cached"], cwd=worktree_path)
    return Diff(
        files_changed=files,
        lines_added=added,
        lines_removed=removed,
        raw_diff=raw.stdout,
    )


def get_last_commit_sha(worktree_path: str) -> str:
    """Return the full SHA of the most recent commit."""
    return get_head_sha(worktree_path, short=False)


# ---------------------------------------------------------------------------
# Branch operations (for merge / discard)
# ---------------------------------------------------------------------------


def merge_branch_to(repo_path: str, source_branch: str, target_branch: str) -> bool:
    """Merge *source_branch* into *target_branch*.

    Returns ``True`` on success, ``False`` if there are merge conflicts.
    """
    _run_git(["checkout", target_branch], cwd=repo_path)
    try:
        _run_git(["merge", source_branch, "--no-edit"], cwd=repo_path)
        return True
    except GitError:
        return False


def delete_branch(repo_path: str, branch_name: str) -> None:
    """Force-delete a local branch."""
    _run_git(["branch", "-D", branch_name], cwd=repo_path)
