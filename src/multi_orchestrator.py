"""Multi-agent orchestrator — indexer → analyst → coder → reviewer pipeline.

Replaces the single-agent loop with specialized sub-agents that each
get a focused context window.
"""

from __future__ import annotations

import json
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import click

from src import git_ops
from src.agents.analyst import AnalystAgent
from src.agents.coder import CoderAgent
from src.agents.indexer import IndexerAgent
from src.agents.reviewer import ReviewerAgent
from src.backlog import Backlog, BacklogItem
from src.config import Config
from src.eval.eval_anchors import EvalAnchors
from src.eval.search_memory import SearchMemory
from src.plugins.base import EvaluatorPlugin
from src.policy import check_policy
from src.project_memory import ProjectMemory
from src.run_context import RunContext
from src.types import Decision, RunStatus


def run_multi_agent_grounding(
    run_ctx: RunContext,
    config: Config,
    targets: list[str],
    program_md: str,
    eval_anchors: EvalAnchors,
    project_mem: ProjectMemory,
) -> tuple[dict[str, str], Backlog]:
    """Run indexer + analyst agents during grounding phase.

    Returns (semantic_summaries, backlog).
    """
    click.echo("\n── Multi-Agent Grounding ──")

    # 0. Set baseline SHA
    sha = git_ops.get_head_sha(str(run_ctx.worktree_path), short=False)
    run_ctx.set_baseline(sha)
    run_ctx.status = RunStatus.RUNNING

    # 1. Indexer
    click.echo("Phase 1: Semantic Indexing")
    indexer = IndexerAgent(config)
    cache_path = run_ctx.run_dir.parent.parent / "index_cache.json"
    summaries = indexer.run(targets, str(run_ctx.worktree_path), cache_path)
    semantic_index = indexer.format_index(summaries, targets, str(run_ctx.worktree_path))

    # Save index
    index_path = run_ctx.run_dir / "semantic_index.md"
    index_path.write_text(semantic_index)
    click.echo(f"  ✓ Indexed {len(summaries)} files\n")

    # 2. Analyst
    click.echo("Phase 2: Analysis & Backlog")
    analyst = AnalystAgent(config)
    raw_items = analyst.run(
        semantic_index=semantic_index,
        program_md=program_md,
        eval_anchors_agent=eval_anchors.for_agent_prompt(),
        project_memory=project_mem.get_prompt_context(),
        working_dir=str(run_ctx.worktree_path),
    )

    backlog = Backlog()
    if raw_items:
        backlog.load_from_analyst(raw_items)
    click.echo(f"  ✓ Backlog: {len(backlog.items)} tasks identified\n")

    # Save backlog
    backlog.save(run_ctx.run_dir / "backlog.json")

    # ── Interactive theme-based approval ──
    if config.grounding_mode != "auto" and backlog.has_pending():
        # Group by category into themes
        themes = _group_into_themes(backlog)

        click.echo("── Improvement Plan ──\n")
        click.echo("The analyst identified these improvement areas:\n")

        theme_nums = []
        for i, (theme_name, icon, goal, items) in enumerate(themes):
            theme_nums.append(i + 1)
            avg_p = sum(it.priority for it in items) / len(items)
            impact = "high" if avg_p >= 0.85 else "medium" if avg_p >= 0.7 else "lower"
            click.echo(f"  {i+1}. {icon}  {theme_name} ({len(items)} tasks, {impact} impact)")
            click.echo(f"     Goal: {goal}")
            click.echo(f"     Tasks:")
            for it in items[:4]:
                click.echo(f"       - {it.title}")
            if len(items) > 4:
                click.echo(f"       ... and {len(items) - 4} more")
            click.echo()

        choice = click.prompt(
            f"Approve which themes? (e.g. '1,2' or 'all' or 'q' to quit)",
            default="all",
        ).strip().lower()

        if choice == "q":
            click.echo("Run aborted by user.")
            run_ctx.cleanup()
            raise SystemExit(0)

        if choice != "all":
            try:
                approved = {int(x.strip()) for x in choice.split(",")}
            except ValueError:
                approved = set(theme_nums)

            # Remove items from non-approved themes
            approved_categories = set()
            for i, (_, _, _, items) in enumerate(themes):
                if (i + 1) in approved:
                    approved_categories.update(it.category for it in items)

            removed = [it for it in backlog.items if it.category not in approved_categories]
            backlog.items = [it for it in backlog.items if it.category in approved_categories]
            click.echo(f"  ✓ Approved {len(backlog.items)} tasks, removed {len(removed)}")
        else:
            click.echo(f"  ✓ All {len(backlog.items)} tasks approved")

        # Show eval anchors for approval
        click.echo("\n── Evaluation Criteria ──")
        click.echo("Changes will be judged by these rules:\n")
        if eval_anchors.better_means:
            click.echo("  BETTER means:")
            for b in eval_anchors.better_means:
                click.echo(f"    ✓ {b}")
        if eval_anchors.worse_means:
            click.echo("  WORSE means:")
            for w in eval_anchors.worse_means:
                click.echo(f"    ✗ {w}")
        if eval_anchors.must_preserve:
            click.echo("  MUST preserve:")
            for m in eval_anchors.must_preserve:
                click.echo(f"    🔒 {m.get('description', str(m))}")

        eval_ok = click.prompt("\nAccept these evaluation criteria? [y/n]", default="y").strip().lower()
        if eval_ok == "n":
            click.echo("Edit eval_anchors.yaml in your project root and re-run.")
            run_ctx.cleanup()
            raise SystemExit(0)

        click.echo("\n✓ Backlog and criteria approved. Starting improvement loop.\n")

    # Save final backlog
    backlog.save(run_ctx.run_dir / "backlog.json")

    return summaries, backlog


def run_multi_agent_loop(
    run_ctx: RunContext,
    plugin: EvaluatorPlugin,
    config: Config,
    targets: list[str],
    backlog: Backlog,
    summaries: dict[str, str],
    eval_anchors: EvalAnchors,
    search_mem: SearchMemory,
) -> None:
    """Multi-agent iteration loop: pick task → coder → (gates || reviewer) → decide."""
    coder = CoderAgent(config)
    reviewer = ReviewerAgent(config)
    anchors_judge = eval_anchors.for_judge_prompt()
    anchors_agent = eval_anchors.for_agent_prompt()
    wd = str(run_ctx.worktree_path)

    while backlog.has_pending():
        # Check stop conditions
        if run_ctx.is_budget_exhausted():
            run_ctx.stop_reason = f"Time budget exhausted ({run_ctx.elapsed_minutes():.1f}/{config.time_budget_minutes} min)"
            break
        if config.max_iterations and run_ctx.current_iteration >= config.max_iterations:
            run_ctx.stop_reason = f"Max iterations reached ({run_ctx.current_iteration})"
            break

        item = backlog.next()
        if not item:
            run_ctx.stop_reason = "Backlog exhausted"
            break

        budget = run_ctx.budget_remaining_minutes()
        click.echo(f"\n┌─ Iteration {run_ctx.current_iteration} ({budget:.0f}min remaining) ─────────────────")
        click.echo(f"│ 📋 Task: {item.title} (priority: {item.priority:.1f})")

        # Read relevant files for the coder
        file_contents = _read_files(item.files, wd)
        if not file_contents:
            click.echo("│ ⚠ No readable files for this task, skipping")
            backlog.mark_failed(item, "files_unreadable")
            run_ctx.current_iteration += 1
            continue

        # Coder agent
        result = coder.run_in(item, file_contents, anchors_agent, wd)

        if not result.success:
            reason = (result.error or "unknown")[:120]
            click.echo(f"│ ✗ Coder failed: {reason}")
            backlog.mark_failed(item, f"coder_error: {reason}")
            search_mem.record_attempt(
                iteration=run_ctx.current_iteration, hypothesis=item.title,
                files_targeted=item.files, files_modified=[],
                outcome="rejected_agent_error", reason=reason,
                score=None, confidence=None,
            )
            run_ctx.record_reject()
            click.echo(f"└─ REJECTED (coder error) | Accepts: {run_ctx.total_accepts}, Rejects: {run_ctx.total_rejects}")
            continue

        # Capture diff
        click.echo("│ ⏳ Checking diff...", nl=False)
        diff = git_ops.get_diff(wd, run_ctx.accepted_state_sha)

        if not diff.files_changed:
            click.echo(" no changes")
            backlog.mark_failed(item, "no_changes")
            search_mem.record_attempt(
                iteration=run_ctx.current_iteration, hypothesis=item.title,
                files_targeted=item.files, files_modified=[],
                outcome="rejected_empty", reason="No changes made",
                score=None, confidence=None,
            )
            run_ctx.record_reject()
            click.echo(f"└─ REJECTED (no changes) | Accepts: {run_ctx.total_accepts}, Rejects: {run_ctx.total_rejects}")
            continue

        click.echo(f" {len(diff.files_changed)} files, +{diff.lines_added}/-{diff.lines_removed} lines")

        # Parallel: hard gates + reviewer
        click.echo("│ ⏳ Evaluating (gates + reviewer in parallel)...", nl=False)
        t0 = time.monotonic()

        gate_result = None
        review_result = None

        from rich.live import Live
        from rich.table import Table

        eval_status = {"gates": "🔄 running...", "reviewer": "🔄 running..."}

        def build_eval_table() -> Table:
            t = Table(show_header=False, box=None, padding=(0, 1))
            t.add_row("│", f"  Hard gates: {eval_status['gates']}")
            t.add_row("│", f"  Reviewer:   {eval_status['reviewer']}")
            return t

        with Live(build_eval_table(), refresh_per_second=2):
            with ThreadPoolExecutor(max_workers=2) as pool:
                gates_future = pool.submit(
                    _run_gates_and_policy, plugin, diff, targets, wd, config,
                )
                review_future = pool.submit(
                    reviewer.run, diff.raw_diff[:6000], item, anchors_judge,
                    {f: summaries.get(f, "") for f in diff.files_changed},
                )

                gate_result = gates_future.result()
                eval_status["gates"] = "✓ done" if gate_result["passed"] else f"✗ failed: {', '.join(gate_result['failures'][:2])}"

                review_result = review_future.result()
                eval_status["reviewer"] = f"✓ {review_result.verdict}" if review_result.verdict == "accept" else f"✗ {review_result.verdict}"

        eval_time = time.monotonic() - t0
        click.echo(f"│ Evaluation complete ({eval_time:.0f}s)")

        # Decision
        hypothesis = item.title

        if gate_result and not gate_result["passed"]:
            click.echo(f"│ ❌ REJECTED: hard gate failure")
            click.echo(f"│    Failures: {', '.join(gate_result['failures'][:3])}")
            backlog.mark_failed(item, f"hard_gate: {', '.join(gate_result['failures'][:2])}")
            run_ctx.record_reject()
            search_mem.record_attempt(
                iteration=run_ctx.current_iteration, hypothesis=hypothesis,
                files_targeted=item.files, files_modified=diff.files_changed,
                outcome="rejected_gate", reason=str(gate_result["failures"][:2]),
                score=None, confidence=None,
            )
        elif review_result.verdict != "accept":
            click.echo(f"│ ❌ REJECTED: reviewer — {review_result.reasoning[:120]}")
            backlog.mark_failed(item, f"reviewer: {review_result.reasoning[:100]}")
            run_ctx.record_reject()
            search_mem.record_attempt(
                iteration=run_ctx.current_iteration, hypothesis=hypothesis,
                files_targeted=item.files, files_modified=diff.files_changed,
                outcome="rejected_review", reason=review_result.reasoning[:200],
                score=review_result.score, confidence=review_result.confidence,
            )
        else:
            # Accept
            sha = git_ops.commit(wd, f"autoimprove: {hypothesis[:80]}")
            run_ctx.record_accept(sha)
            run_ctx.current_composite_score = review_result.score
            backlog.mark_done(item)
            click.echo(f"│ ✅ ACCEPTED (score: {review_result.score:.2f}, confidence: {review_result.confidence:.2f})")
            click.echo(f"│    Reviewer: {review_result.reasoning[:120]}")
            click.echo(f"│    Files: {', '.join(diff.files_changed[:5])}")
            search_mem.record_attempt(
                iteration=run_ctx.current_iteration, hypothesis=hypothesis,
                files_targeted=item.files, files_modified=diff.files_changed,
                outcome="accepted", reason=review_result.reasoning[:200],
                score=review_result.score, confidence=review_result.confidence,
            )

        click.echo(f"└─ Accepts: {run_ctx.total_accepts}, Rejects: {run_ctx.total_rejects} | Backlog: {backlog.summary()}")

        # Revert if rejected
        if review_result.verdict != "accept" or (gate_result and not gate_result["passed"]):
            if run_ctx.accepted_state_sha:
                git_ops.revert_to_commit(wd, run_ctx.accepted_state_sha)

        run_ctx.save_state()
        search_mem.save()
        backlog.save(run_ctx.run_dir / "backlog.json")


# ======================================================================
# Helpers
# ======================================================================


def _read_files(files: list[str], working_dir: str) -> dict[str, str]:
    """Read file contents for the coder agent."""
    wd = Path(working_dir)
    contents: dict[str, str] = {}
    for f in files:
        try:
            contents[f] = (wd / f).read_text(errors="ignore")
        except OSError:
            continue
    return contents


# Category → (theme_name, icon, goal)
_THEME_MAP = {
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


def _group_into_themes(backlog: Backlog) -> list[tuple[str, str, str, list]]:
    """Group backlog items into themes. Returns [(name, icon, goal, items)]."""
    groups: dict[str, list] = {}
    theme_meta: dict[str, tuple[str, str]] = {}

    for item in backlog.items:
        theme_name, icon, goal = _THEME_MAP.get(
            item.category, ("Other Improvements", "🔧", "General codebase improvements.")
        )
        if theme_name not in groups:
            groups[theme_name] = []
            theme_meta[theme_name] = (icon, goal)
        groups[theme_name].append(item)

    # Sort themes by total priority (highest first)
    result = []
    for name in sorted(groups, key=lambda n: -sum(it.priority for it in groups[n])):
        icon, goal = theme_meta[name]
        result.append((name, icon, goal, groups[name]))
    return result


def _run_gates_and_policy(
    plugin: EvaluatorPlugin, diff, targets, working_dir, config,
) -> dict:
    """Run policy checks + hard gates. Returns dict with passed/failures."""
    # Policy
    policy_result = check_policy(diff, config, plugin.guardrails())
    if not policy_result.passed:
        return {
            "passed": False,
            "failures": [v.message for v in policy_result.violations if v.severity == "fatal"],
        }

    # Hard gates
    gate_result = plugin.hard_gates(diff, targets, working_dir)
    return {
        "passed": gate_result.all_passed,
        "failures": gate_result.failures,
        "gates": gate_result.gates,
    }
