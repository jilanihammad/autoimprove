"""Terminal output formatting using rich.

Gracefully degrades if rich is not available.
"""

from __future__ import annotations

try:
    from rich.console import Console
    from rich.panel import Panel
    from rich.table import Table

    _console = Console()
    _HAS_RICH = True
except ImportError:
    _HAS_RICH = False

import click


def print_banner() -> None:
    text = """
    ╔═══════════════════════════════════╗
    ║         A U T O I M P R O V E     ║
    ║   Autonomous Iterative Improvement║
    ╚═══════════════════════════════════╝
    """
    if _HAS_RICH:
        _console.print(Panel(text.strip(), style="bold blue"))
    else:
        click.echo(text)


def print_run_config(config: object, run_id: str) -> None:
    if _HAS_RICH:
        table = Table(title=f"Run: {run_id}")
        table.add_column("Setting", style="cyan")
        table.add_column("Value")
        for key in ("agent_command", "time_budget_minutes", "grounding_mode"):
            table.add_row(key, str(getattr(config, key, "?")))
        _console.print(table)
    else:
        click.echo(f"Run: {run_id}")
        for key in ("agent_command", "time_budget_minutes", "grounding_mode"):
            click.echo(f"  {key}: {getattr(config, key, '?')}")


def print_baseline_summary(baseline: dict) -> None:
    if _HAS_RICH:
        table = Table(title="Baseline Metrics")
        table.add_column("Metric", style="cyan")
        table.add_column("Value", justify="right")
        for k, v in baseline.items():
            table.add_row(k, f"{v:.2f}" if isinstance(v, float) else str(v))
        _console.print(table)
    else:
        click.echo("Baseline Metrics:")
        for k, v in baseline.items():
            click.echo(f"  {k}: {v}")


def print_preflight_report(result: object) -> None:
    checks = getattr(result, "checks", [])
    for c in checks:
        icon = "✓" if c.passed else ("✗" if c.fatal else "⚠")
        if _HAS_RICH:
            style = "green" if c.passed else ("red" if c.fatal else "yellow")
            _console.print(f"  [{style}]{icon}[/] {c.name}: {c.message}")
        else:
            click.echo(f"  {icon} {c.name}: {c.message}")


def print_grounding_criteria(criteria: object) -> None:
    items = getattr(criteria, "items", [])
    if _HAS_RICH:
        table = Table(title="Evaluation Criteria")
        table.add_column("Name", style="cyan")
        table.add_column("Description")
        table.add_column("Weight", justify="right")
        table.add_column("Gate")
        table.add_column("Type")
        for item in items:
            w = "-" if item.is_hard_gate else f"{item.weight:.2f}"
            gate = "✓" if item.is_hard_gate else ""
            table.add_row(item.name, item.description, w, gate, item.metric_type)
        _console.print(table)
    else:
        for item in items:
            tag = "[GATE]" if item.is_hard_gate else f"[w={item.weight:.2f}]"
            click.echo(f"  {tag} {item.name}: {item.description}")


def print_grounding_hypotheses(hypotheses: list[dict]) -> None:
    click.echo("Improvement Hypotheses (ranked by expected impact):")
    for i, h in enumerate(hypotheses, 1):
        impact = h.get("expected_impact", "?").upper()
        click.echo(f"  {i}. [{impact}] {h.get('description', '?')}")


def print_iteration_result(iteration: int, decision: str, run_ctx: object) -> None:
    budget = getattr(run_ctx, "budget_remaining_minutes", lambda: 0)()
    accepts = getattr(run_ctx, "total_accepts", 0)
    rejects = getattr(run_ctx, "total_rejects", 0)
    line = f"[Iter {iteration}] {decision} | Budget: {budget:.0f}min | A:{accepts} R:{rejects}"
    click.echo(line)


def print_stop_banner(reason: str) -> None:
    if _HAS_RICH:
        _console.print(Panel(f"AUTOIMPROVE STOPPED: {reason}", style="bold yellow"))
    else:
        click.echo("═" * 50)
        click.echo(f"AUTOIMPROVE STOPPED: {reason}")
        click.echo("═" * 50)


def print_final_summary(stats: dict, run_id: str) -> None:
    click.echo(f"\nRun {run_id} complete:")
    click.echo(f"  Iterations: {stats.get('total', 0)}")
    click.echo(f"  Accepted: {stats.get('accepted', 0)}")
    click.echo(f"  Rejected: {stats.get('rejected', 0)}")
    click.echo(f"  Accept rate: {stats.get('accept_rate', 0):.0%}")


def print_error(message: str) -> None:
    if _HAS_RICH:
        _console.print(Panel(message, style="bold red", title="Error"))
    else:
        click.echo(f"ERROR: {message}", err=True)


def print_warning(message: str) -> None:
    if _HAS_RICH:
        _console.print(f"[yellow]⚠ {message}[/]")
    else:
        click.echo(f"⚠ {message}", err=True)


def prompt_user(question: str, choices: list[str] | None = None) -> str:
    if choices:
        return click.prompt(question, type=click.Choice(choices))
    return click.prompt(question)
