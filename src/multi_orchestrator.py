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
from src.types import Decision


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

    # 1. Indexer
    click.echo("Phase 1: Semantic Indexing")
    indexer = IndexerAgent(config)
    summaries = indexer.run(targets, str(run_ctx.worktree_path))
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
    click.echo(f"  ✓ Backlog: {backlog.summary()}\n")

    # Save backlog
    backlog.save(run_ctx.run_dir / "backlog.json")

    # Print backlog
    for item in backlog.items[:10]:
        click.echo(f"  [{item.priority:.1f}] {item.title} ({item.category})")
        click.echo(f"       Files: {', '.join(item.files[:3])}")

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

        with ThreadPoolExecutor(max_workers=2) as pool:
            gates_future = pool.submit(
                _run_gates_and_policy, plugin, diff, targets, wd, config,
            )
            review_future = pool.submit(
                reviewer.run, diff.raw_diff[:6000], item, anchors_judge,
                {f: summaries.get(f, "") for f in diff.files_changed},
            )

            gate_result = gates_future.result()
            review_result = review_future.result()

        eval_time = time.monotonic() - t0
        click.echo(f" {eval_time:.0f}s")

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
