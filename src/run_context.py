"""Run context — manages run lifecycle, directory structure, and state.

The ``RunContext`` is the single source of truth for a run.  Created once
during initialisation, it is passed to every component.  State is persisted
to disk after every iteration so the system can recover from crashes.
"""

from __future__ import annotations

import json
import os
import shutil
from datetime import datetime, timezone
from pathlib import Path

from src import git_ops
from src.config import Config
from src.types import RunStatus


class RunContext:
    """Central state object for an AutoImprove run."""

    def __init__(self, config: Config, repo_path: str) -> None:
        self.run_id: str = git_ops.generate_run_id()
        self.repo_path: str = str(Path(repo_path).resolve())
        self.config: Config = config
        self.status: RunStatus = RunStatus.INITIALIZING
        self.current_iteration: int = 0
        self.start_time: datetime = datetime.now(timezone.utc)
        self.stop_reason: str = ""

        # Git state
        self.source_branch: str = git_ops.get_current_branch(self.repo_path)
        self.baseline_sha: str | None = None
        self.accepted_state_sha: str | None = None
        self.current_composite_score: float = 0.0

        # Counters
        self.total_accepts: int = 0
        self.total_rejects: int = 0
        self.consecutive_rejections: int = 0
        self.criteria_version: int = 1

        # Paths
        base = Path(self.repo_path) / ".autoimprove"
        self.runs_dir: Path = base / "runs"
        self.run_dir: Path = self.runs_dir / self.run_id
        self.worktree_path: Path = self.run_dir / "worktree"
        self.config_path: Path = self.run_dir / "config.yaml"
        self.baseline_path: Path = self.run_dir / "baseline.json"
        self.accepted_state_path: Path = self.run_dir / "accepted_state.json"
        self.experiment_log_path: Path = self.run_dir / "experiment_log.json"
        self.search_memory_path: Path = self.run_dir / "search_memory.json"
        self.criteria_dir: Path = self.run_dir / "criteria"
        self.proposals_dir: Path = self.run_dir / "proposals"
        self.summary_path: Path = self.run_dir / "summary.md"
        self.latest_link: Path = base / "latest"

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def initialize(self) -> None:
        """Create directory structure, worktree, and freeze config."""
        for d in (self.run_dir, self.criteria_dir, self.proposals_dir):
            d.mkdir(parents=True, exist_ok=True)

        # Freeze config
        import yaml

        with open(self.config_path, "w") as f:
            yaml.dump(self.config.model_dump(), f, default_flow_style=False)

        # Create worktree
        branch = f"autoimprove/{self.run_id}"
        git_ops.create_worktree(self.repo_path, str(self.worktree_path), branch)

        # Symlink .autoimprove/latest → runs/<run_id>
        if self.latest_link.is_symlink() or self.latest_link.exists():
            self.latest_link.unlink()
        self.latest_link.symlink_to(self.run_dir, target_is_directory=True)

        # Init empty log
        with open(self.experiment_log_path, "w") as f:
            json.dump([], f)

        self.start_time = datetime.now(timezone.utc)
        self.save_state()

    def finalize(self, stop_reason: str = "") -> None:
        """Mark run as completed and tag the final state."""
        self.status = RunStatus.COMPLETED
        self.stop_reason = stop_reason
        if self.accepted_state_sha:
            git_ops.tag(str(self.worktree_path), f"autoimprove/{self.run_id}/final")
        self.save_state()

    def cleanup(self) -> None:
        """Remove the worktree.  Run data (logs, reports) is preserved."""
        git_ops.remove_worktree(self.repo_path, str(self.worktree_path))

    # ------------------------------------------------------------------
    # Baseline
    # ------------------------------------------------------------------

    def set_baseline(self, sha: str) -> None:
        """Record the baseline commit and tag it."""
        self.baseline_sha = sha
        self.accepted_state_sha = sha
        git_ops.tag(str(self.worktree_path), f"autoimprove/{self.run_id}/baseline")
        self.save_state()

    # ------------------------------------------------------------------
    # Iteration tracking
    # ------------------------------------------------------------------

    def record_accept(self, sha: str) -> None:
        """Record an accepted iteration."""
        self.accepted_state_sha = sha
        self.total_accepts += 1
        self.consecutive_rejections = 0
        self.current_iteration += 1
        git_ops.tag(str(self.worktree_path), f"autoimprove/{self.run_id}/iter_{self.total_accepts}")
        self.save_state()

    def record_reject(self) -> None:
        """Record a rejected iteration and revert worktree to accepted state."""
        self.total_rejects += 1
        self.consecutive_rejections += 1
        self.current_iteration += 1
        if self.accepted_state_sha:
            git_ops.revert_to_commit(str(self.worktree_path), self.accepted_state_sha)
        self.save_state()

    # ------------------------------------------------------------------
    # Budget
    # ------------------------------------------------------------------

    def elapsed_minutes(self) -> float:
        """Minutes since the run started."""
        delta = datetime.now(timezone.utc) - self.start_time
        return delta.total_seconds() / 60.0

    def budget_remaining_minutes(self) -> float:
        """Minutes left in the time budget."""
        return self.config.time_budget_minutes - self.elapsed_minutes()

    def is_budget_exhausted(self) -> bool:
        """True if the time budget has been exceeded."""
        return self.elapsed_minutes() >= self.config.time_budget_minutes

    # ------------------------------------------------------------------
    # Persistence
    # ------------------------------------------------------------------

    def _to_dict(self) -> dict:
        return {
            "run_id": self.run_id,
            "repo_path": self.repo_path,
            "source_branch": self.source_branch,
            "status": self.status.value,
            "current_iteration": self.current_iteration,
            "start_time": self.start_time.isoformat(),
            "stop_reason": self.stop_reason,
            "baseline_sha": self.baseline_sha,
            "accepted_state_sha": self.accepted_state_sha,
            "current_composite_score": self.current_composite_score,
            "total_accepts": self.total_accepts,
            "total_rejects": self.total_rejects,
            "consecutive_rejections": self.consecutive_rejections,
            "criteria_version": self.criteria_version,
            "elapsed_minutes": round(self.elapsed_minutes(), 2),
        }

    def save_state(self) -> None:
        """Persist current state to disk for crash recovery."""
        with open(self.accepted_state_path, "w") as f:
            json.dump(self._to_dict(), f, indent=2)

    @classmethod
    def load_state(cls, run_dir: Path) -> RunContext:
        """Reconstruct a RunContext from a saved run directory."""
        state_path = run_dir / "accepted_state.json"
        if not state_path.exists():
            raise FileNotFoundError(f"No state file at {state_path}")

        with open(state_path) as f:
            data = json.load(f)

        config_path = run_dir / "config.yaml"
        from src.config import load_config

        config = load_config(str(config_path))

        ctx = cls.__new__(cls)
        ctx.run_id = data["run_id"]
        ctx.repo_path = data["repo_path"]
        ctx.config = config
        ctx.status = RunStatus(data["status"])
        ctx.current_iteration = data["current_iteration"]
        ctx.start_time = datetime.fromisoformat(data["start_time"])
        ctx.stop_reason = data.get("stop_reason", "")
        ctx.source_branch = data["source_branch"]
        ctx.baseline_sha = data.get("baseline_sha")
        ctx.accepted_state_sha = data.get("accepted_state_sha")
        ctx.current_composite_score = data.get("current_composite_score", 0.0)
        ctx.total_accepts = data["total_accepts"]
        ctx.total_rejects = data["total_rejects"]
        ctx.consecutive_rejections = data["consecutive_rejections"]
        ctx.criteria_version = data.get("criteria_version", 1)

        # Reconstruct paths
        base = Path(ctx.repo_path) / ".autoimprove"
        ctx.runs_dir = base / "runs"
        ctx.run_dir = run_dir
        ctx.worktree_path = run_dir / "worktree"
        ctx.config_path = run_dir / "config.yaml"
        ctx.baseline_path = run_dir / "baseline.json"
        ctx.accepted_state_path = run_dir / "accepted_state.json"
        ctx.experiment_log_path = run_dir / "experiment_log.json"
        ctx.search_memory_path = run_dir / "search_memory.json"
        ctx.criteria_dir = run_dir / "criteria"
        ctx.proposals_dir = run_dir / "proposals"
        ctx.summary_path = run_dir / "summary.md"
        ctx.latest_link = base / "latest"
        return ctx
