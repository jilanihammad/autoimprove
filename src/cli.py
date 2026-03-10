"""CLI entrypoint for AutoImprove."""

from __future__ import annotations

from pathlib import Path

import click

from src import __version__
from src.config import load_config, validate_config


@click.group()
@click.version_option(version=__version__, prog_name="autoimprove")
def main() -> None:
    """AutoImprove — autonomous iterative improvement for any work artifact."""


@main.command()
@click.option(
    "-c",
    "--config",
    "config_path",
    default="config.yaml",
    type=click.Path(exists=False),
    help="Path to config.yaml.",
)
@click.option("-t", "--time", "time_budget", type=int, default=None, help="Time budget in minutes.")
@click.option("-a", "--agent", "agent_cmd", type=str, default=None, help="Agent command to use.")
@click.option(
    "--auto", "auto_mode", is_flag=True, default=False, help="Skip interactive grounding."
)
def run(
    config_path: str,
    time_budget: int | None,
    agent_cmd: str | None,
    auto_mode: bool,
) -> None:
    """Start an autonomous improvement run."""
    cfg_path = Path(config_path)
    if not cfg_path.exists():
        click.echo(f"Error: config file not found at {cfg_path}", err=True)
        raise SystemExit(1)

    config = load_config(cfg_path)

    # CLI overrides
    if time_budget is not None:
        config = config.model_copy(update={"time_budget_minutes": time_budget})
    if agent_cmd is not None:
        config = config.model_copy(update={"agent_command": agent_cmd})
    if auto_mode:
        config = config.model_copy(update={"grounding_mode": "auto"})

    warnings = validate_config(config)
    for w in warnings:
        click.echo(f"Warning: {w}", err=True)

    # Delegate to orchestrator (implemented in Bead 14)
    from src.orchestrator import run_autoimprove

    run_autoimprove(config)


@main.command()
@click.argument("run_id")
def merge(run_id: str) -> None:
    """Merge accepted changes from a completed run into your branch."""
    raise NotImplementedError("Merge command — implemented in Bead 19")


@main.command()
@click.argument("run_id")
def discard(run_id: str) -> None:
    """Discard a run's changes and clean up the worktree."""
    raise NotImplementedError("Discard command — implemented in Bead 19")


@main.command()
def status() -> None:
    """Show status of all AutoImprove runs."""
    raise NotImplementedError("Status command — implemented in Bead 19")


if __name__ == "__main__":
    main()
