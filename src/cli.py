"""CLI entrypoint for AutoImprove."""

from __future__ import annotations

import json
from pathlib import Path

import click

from src import __version__, git_ops
from src.config import load_config, validate_config
from src.run_context import RunContext
from src.types import RunStatus


@click.group()
@click.version_option(version=__version__, prog_name="autoimprove")
def main() -> None:
    """AutoImprove — autonomous iterative improvement for any work artifact."""


@main.command()
@click.option("-c", "--config", "config_path", default="config.yaml", type=click.Path(exists=False), help="Path to config.yaml.")
@click.option("-t", "--time", "time_budget", type=int, default=None, help="Time budget in minutes.")
@click.option("-a", "--agent", "agent_cmd", type=str, default=None, help="Agent command to use.")
@click.option("--auto", "auto_mode", is_flag=True, default=False, help="Skip interactive grounding.")
def run(config_path: str, time_budget: int | None, agent_cmd: str | None, auto_mode: bool) -> None:
    """Start an autonomous improvement run."""
    cfg_path = Path(config_path)
    if not cfg_path.exists():
        click.echo(f"Error: config file not found at {cfg_path}", err=True)
        raise SystemExit(1)

    config = load_config(cfg_path)

    if time_budget is not None:
        config = config.model_copy(update={"time_budget_minutes": time_budget})
    if agent_cmd is not None:
        config = config.model_copy(update={"agent_command": agent_cmd})
    if auto_mode:
        config = config.model_copy(update={"grounding_mode": "auto"})

    warnings = validate_config(config)
    for w in warnings:
        click.echo(f"Warning: {w}", err=True)

    from src.orchestrator import run_autoimprove
    run_autoimprove(config)


@main.command()
@click.argument("run_id")
def merge(run_id: str) -> None:
    """Merge accepted changes from a completed run into your branch."""
    repo_path = str(Path.cwd().resolve())
    run_dir = Path(repo_path) / ".autoimprove" / "runs" / run_id

    if not run_dir.exists():
        click.echo(f"Run not found: {run_id}", err=True)
        raise SystemExit(1)

    ctx = RunContext.load_state(run_dir)
    if ctx.status not in (RunStatus.COMPLETED, RunStatus.STOPPED):
        click.echo(f"Run status is '{ctx.status.value}' — can only merge completed/stopped runs.", err=True)
        raise SystemExit(1)

    if not ctx.accepted_state_sha or ctx.accepted_state_sha == ctx.baseline_sha:
        click.echo("No accepted changes to merge.")
        return

    # Show diff stat
    try:
        diff = git_ops.get_diff(str(ctx.worktree_path), ctx.baseline_sha, ctx.accepted_state_sha)
        click.echo(f"Changes: {len(diff.files_changed)} files, +{diff.lines_added}/-{diff.lines_removed} lines")
    except git_ops.GitError:
        click.echo("Could not compute diff stat.")

    if not click.confirm("Merge these changes into your branch?"):
        click.echo("Aborted.")
        return

    branch = f"autoimprove/{run_id}"
    success = git_ops.merge_branch_to(repo_path, branch, ctx.source_branch)
    if success:
        click.echo(f"✓ Merged into {ctx.source_branch}")
        git_ops.remove_worktree(repo_path, str(ctx.worktree_path))
        click.echo("✓ Worktree cleaned up")
    else:
        click.echo("Merge conflicts detected. Resolve manually and commit.", err=True)


@main.command()
@click.argument("run_id")
def discard(run_id: str) -> None:
    """Discard a run's changes and clean up the worktree."""
    repo_path = str(Path.cwd().resolve())
    run_dir = Path(repo_path) / ".autoimprove" / "runs" / run_id

    if not run_dir.exists():
        click.echo(f"Run not found: {run_id}", err=True)
        raise SystemExit(1)

    ctx = RunContext.load_state(run_dir)
    click.echo(f"Run {run_id}: {ctx.total_accepts} accepts, {ctx.total_rejects} rejects")

    if not click.confirm("Discard this run's changes? (logs are preserved)"):
        click.echo("Aborted.")
        return

    branch = f"autoimprove/{run_id}"
    git_ops.remove_worktree(repo_path, str(ctx.worktree_path))
    try:
        git_ops.delete_branch(repo_path, branch)
    except git_ops.GitError:
        pass
    click.echo(f"✓ Discarded run {run_id}. Logs preserved at {run_dir}")


@main.command()
def status() -> None:
    """Show status of all AutoImprove runs."""
    repo_path = str(Path.cwd().resolve())
    runs_dir = Path(repo_path) / ".autoimprove" / "runs"

    if not runs_dir.exists():
        click.echo("No runs found.")
        return

    runs = sorted(runs_dir.iterdir(), reverse=True)
    if not runs:
        click.echo("No runs found.")
        return

    click.echo(f"{'Run ID':<30} {'Status':<12} {'Iter':<6} {'Accept':<8} {'Reject':<8}")
    click.echo("─" * 74)
    for run_dir in runs:
        state_file = run_dir / "accepted_state.json"
        if not state_file.exists():
            continue
        try:
            with open(state_file) as f:
                data = json.load(f)
            click.echo(
                f"{data.get('run_id', '?'):<30} "
                f"{data.get('status', '?'):<12} "
                f"{data.get('current_iteration', 0):<6} "
                f"{data.get('total_accepts', 0):<8} "
                f"{data.get('total_rejects', 0):<8}"
            )
        except (json.JSONDecodeError, OSError):
            click.echo(f"{run_dir.name:<30} (corrupt state)")


if __name__ == "__main__":
    main()
