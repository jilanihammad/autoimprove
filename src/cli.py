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


@main.command()
@click.argument("run_id")
def calibrate(run_id: str) -> None:
    """Review a run's accepted changes and flag false positives/negatives."""
    repo_path = str(Path.cwd().resolve())
    run_dir = Path(repo_path) / ".autoimprove" / "runs" / run_id
    mem_path = run_dir / "search_memory.json"

    if not mem_path.exists():
        click.echo(f"Run not found: {run_id}", err=True)
        raise SystemExit(1)

    from src.eval.search_memory import SearchMemory
    from src.project_memory import ProjectMemory

    search_mem = SearchMemory.load(mem_path)
    project_mem = ProjectMemory(repo_path)

    accepted = [h for h in search_mem.hypotheses if h.outcome == "accepted"]
    rejected = [h for h in search_mem.hypotheses if h.outcome != "accepted"]

    if not accepted and not rejected:
        click.echo("No hypotheses to review.")
        return

    count = 0

    if accepted:
        click.echo(f"\n── Accepted Changes ({len(accepted)}) ──")
        click.echo("Flag any that should NOT have been accepted.\n")
        for h in accepted:
            score = f" (score: {h.composite_score:.2f})" if h.composite_score else ""
            click.echo(f"  [{h.iteration}] {h.hypothesis[:100]}{score}")
            files = ", ".join(h.files_actually_modified[:3])
            if files:
                click.echo(f"       Files: {files}")
            flag = click.prompt("  Flag as bad? [y/N/q]", default="n").strip().lower()
            if flag == "q":
                break
            if flag == "y":
                reason = click.prompt("  Why was this bad?")
                project_mem.record_calibration(run_id, h.hypothesis, "positive", reason)
                count += 1

    if rejected and click.confirm("\nReview rejected changes too?", default=False):
        click.echo(f"\n── Rejected Changes ({len(rejected)}) ──")
        click.echo("Flag any that SHOULD have been accepted.\n")
        for h in rejected[:20]:
            click.echo(f"  [{h.iteration}] {h.hypothesis[:100]} — {h.outcome}")
            flag = click.prompt("  Should have been accepted? [y/N/q]", default="n").strip().lower()
            if flag == "q":
                break
            if flag == "y":
                reason = click.prompt("  Why was this actually good?")
                project_mem.record_calibration(run_id, h.hypothesis, "negative", reason)
                count += 1

    click.echo(f"\n✓ Recorded {count} calibration(s). These will shape future evaluations.")


if __name__ == "__main__":
    main()
