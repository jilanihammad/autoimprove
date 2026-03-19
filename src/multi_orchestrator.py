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
from src.agents.modifier import ModifierAgent
from src.agents.indexer import IndexerAgent
from src.agents.reviewer import ReviewerAgent
from src.backlog import Backlog, BacklogItem
from src.config import Config
from src.eval.eval_anchors import EvalAnchors, load_eval_anchors
from src.eval.search_memory import SearchMemory
from src.plugins.base import EvaluatorPlugin
from src.plugins.registry import PluginRegistry
from src.policy import check_file_scope, check_policy
from src.project_memory import ProjectMemory
from src.reporting.experiment_log import TSVLogger
from src.run_context import RunContext
from src.types import Decision, IterationStrategy, RunStatus, SemanticDiff


def run_multi_agent_grounding(
    run_ctx: RunContext,
    config: Config,
    targets: list[str],
    program_md: str,
    eval_anchors: EvalAnchors,
    project_mem: ProjectMemory,
    plugin: EvaluatorPlugin | None = None,
    calibration_lessons: dict | None = None,
    all_plugins: dict[str, list[str]] | None = None,
    registry: PluginRegistry | None = None,
) -> tuple[dict[str, str], Backlog]:
    """Run indexer + analyst agents during grounding phase.

    Returns (semantic_summaries, backlog).
    """
    click.echo("\n── Multi-Agent Grounding ──")

    # 0. Set baseline SHA
    sha = git_ops.get_head_sha(str(run_ctx.worktree_path), short=False)
    run_ctx.set_baseline(sha)
    run_ctx.status = RunStatus.RUNNING

    # 1. Indexer — index ALL artifact targets when multi-artifact
    click.echo("Phase 1: Semantic Indexing")
    indexer = IndexerAgent(config)
    cache_path = run_ctx.run_dir.parent.parent / "index_cache.json"
    indexer_hint = plugin.indexer_prompt_hint() if plugin else ""
    # Combine targets from all active plugins so the analyst sees everything
    all_targets = list(targets)
    if all_plugins:
        for pname, ptargets in all_plugins.items():
            for t in ptargets:
                if t not in all_targets:
                    all_targets.append(t)
    summaries = indexer.run(all_targets, str(run_ctx.worktree_path), cache_path, indexer_hint=indexer_hint)
    semantic_index = indexer.format_index(summaries, all_targets, str(run_ctx.worktree_path))

    # Save index
    index_path = run_ctx.run_dir / "semantic_index.md"
    index_path.write_text(semantic_index)
    click.echo(f"  ✓ Indexed {len(summaries)} files\n")

    # 2. Analyst
    click.echo("Phase 2: Analysis & Backlog")
    analyst = AnalystAgent(config)
    cal_analyst_ctx = (calibration_lessons or {}).get("analyst_context", "")

    # For multi-artifact runs, combine categories from all active plugins
    combined_categories = None
    combined_role = ""
    if all_plugins and registry and len(all_plugins) > 1:
        combined_categories = []
        roles = []
        for pname in all_plugins:
            try:
                p = registry.get(pname)
                for cat in p.analyst_categories():
                    # Tag categories with plugin name to allow routing
                    tagged = dict(cat)
                    tagged["name"] = f"{pname}:{cat['name']}"
                    tagged["description"] = f"[{pname}] {cat.get('description', '')}"
                    combined_categories.append(tagged)
                roles.append(p.analyst_role())
            except KeyError:
                continue
        combined_role = " and ".join(roles) if roles else ""
    raw_items = analyst.run(
        semantic_index=semantic_index,
        program_md=program_md,
        eval_anchors_agent=eval_anchors.for_agent_prompt(),
        project_memory=project_mem.get_prompt_context(),
        working_dir=str(run_ctx.worktree_path),
        analyst_role=combined_role or (plugin.analyst_role() if plugin else ""),
        analyst_categories=combined_categories or (plugin.analyst_categories() if plugin else None),
        calibration_context=cal_analyst_ctx,
    )

    backlog = Backlog()
    if raw_items:
        backlog.load_from_analyst(raw_items)
    click.echo(f"  ✓ Backlog: {len(backlog.items)} tasks identified\n")

    # Save backlog
    backlog.save(run_ctx.run_dir / "backlog.json")

    # ── Interactive theme-based approval ──
    if config.grounding_mode != "auto" and backlog.has_pending():
        # Group by category into themes — combine all plugin theme maps
        combined_theme_map = plugin.theme_map() if plugin else {}
        if all_plugins and registry:
            for pname in all_plugins:
                try:
                    combined_theme_map.update(registry.get(pname).theme_map())
                except KeyError:
                    continue
        themes = _group_into_themes(backlog, combined_theme_map)

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
    program_md: str = "",
    project_mem: ProjectMemory | None = None,
    calibration_lessons: dict | None = None,
    preview: bool = False,
    registry: PluginRegistry | None = None,
) -> None:
    """Multi-agent iteration loop: pick task → coder → (gates || reviewer) → decide."""
    coder = ModifierAgent(config)
    reviewer = ReviewerAgent(config)
    # Try loading plugin-specific eval anchors (sectioned format)
    plugin_anchors = load_eval_anchors(str(run_ctx.worktree_path.parent), plugin.name)
    if plugin_anchors.better_means or plugin_anchors.worse_means:
        anchors_judge = plugin_anchors.for_judge_prompt()
        anchors_agent = plugin_anchors.for_agent_prompt()
    else:
        anchors_judge = eval_anchors.for_judge_prompt()
        anchors_agent = eval_anchors.for_agent_prompt()

    # Enrich prompts with calibration lessons
    cal = calibration_lessons or {}
    if cal.get("judge_context"):
        anchors_judge = anchors_judge + "\n\n" + cal["judge_context"]
    cal_analyst_ctx = cal.get("analyst_context", "")
    wd = str(run_ctx.worktree_path)
    tsv = TSVLogger(run_ctx.run_dir / "experiment_log.tsv")
    regeneration_count = 0

    # Preview mode: collect proposals instead of committing
    preview_proposals: list[dict] = []  # Only used when preview=True
    max_preview = 5  # Collect up to 5 proposals in preview mode

    while True:
        # Backlog regeneration: when backlog is empty, re-run analyst
        if not backlog.has_pending():
            if regeneration_count >= config.max_backlog_regenerations:
                run_ctx.stop_reason = f"Backlog exhausted after {regeneration_count} regeneration(s)"
                break

            click.echo(f"\n── Backlog Regeneration ({regeneration_count + 1}/{config.max_backlog_regenerations}) ──")
            completed = "\n".join(
                f"- [DONE] {i.title}" for i in backlog.items if i.status == "done"
            ) + "\n" + "\n".join(
                f"- [FAILED] {i.title}: {i.last_rejection_reason[:80]}" for i in backlog.items if i.status in ("failed", "skipped")
            )

            analyst = AnalystAgent(config)
            semantic_index = (run_ctx.run_dir / "semantic_index.md").read_text() if (run_ctx.run_dir / "semantic_index.md").exists() else ""
            raw_items = analyst.run(
                semantic_index=semantic_index,
                program_md=program_md,
                eval_anchors_agent=anchors_agent,
                project_memory=project_mem.get_prompt_context() if project_mem else "",
                working_dir=wd,
                completed_work=completed,
                analyst_role=plugin.analyst_role(),
                analyst_categories=plugin.analyst_categories(),
                calibration_context=cal_analyst_ctx,
            )

            if raw_items:
                added = backlog.merge_new_items(raw_items)
                click.echo(f"  ✓ Analyst proposed {len(raw_items)} items, {added} new after dedup")
            else:
                click.echo("  ✗ Analyst produced no new items")

            regeneration_count += 1
            if not backlog.has_pending():
                run_ctx.stop_reason = "No new backlog items after regeneration"
                break
            continue

        # Check stop conditions
        if run_ctx.is_budget_exhausted():
            run_ctx.stop_reason = f"Time budget exhausted ({run_ctx.elapsed_minutes():.1f}/{config.time_budget_minutes} min)"
            break
        if config.max_iterations and run_ctx.current_iteration >= config.max_iterations:
            run_ctx.stop_reason = f"Max iterations reached ({run_ctx.current_iteration})"
            break

        item = backlog.next()
        if not item:
            continue  # will hit the regeneration check at top of loop

        # Route to correct plugin for multi-artifact runs
        active_plugin = plugin
        if registry and item.plugin_name:
            try:
                active_plugin = registry.get(item.plugin_name)
            except KeyError:
                pass  # Fall back to default plugin

        budget = run_ctx.budget_remaining_minutes()
        plugin_tag = f" [{active_plugin.name}]" if active_plugin.name != plugin.name else ""
        click.echo(f"\n┌─ Iteration {run_ctx.current_iteration} ({budget:.0f}min remaining) ─────────────────")
        click.echo(f"│ 📋 Task: {item.title} (priority: {item.priority:.1f}){plugin_tag}")

        # Read relevant files for the coder
        file_contents = _read_files(item.files, wd)
        if not file_contents:
            click.echo("│ ⚠ No readable files for this task, skipping")
            backlog.mark_failed(item, "files_unreadable")
            run_ctx.current_iteration += 1
            continue

        # Coder agent (uses active_plugin for multi-artifact routing)
        result = coder.run_in(
            item, file_contents, anchors_agent, wd,
            modifier_role=active_plugin.modifier_role(),
            modifier_constraints=active_plugin.modifier_constraints(),
        )

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
            tsv.log(run_ctx.current_iteration - 1, "reject_coder", None, None, [], item.title)
            # Revert any partial working-tree changes from the failed agent
            if run_ctx.accepted_state_sha:
                git_ops.revert_to_commit(wd, run_ctx.accepted_state_sha)
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
            tsv.log(run_ctx.current_iteration - 1, "reject_empty", None, None, [], item.title)
            continue

        click.echo(f" {len(diff.files_changed)} files, +{diff.lines_added}/-{diff.lines_removed} lines")

        # File scope enforcement
        if config.enforce_file_scope and item.files:
            scope_result = check_file_scope(diff, item.files)
            if not scope_result.passed:
                out_of_scope = [v.file for v in scope_result.violations if v.file]
                click.echo(f"│ ❌ REJECTED: files outside task scope: {', '.join(out_of_scope[:3])}")
                backlog.mark_failed(item, f"file_scope: {', '.join(out_of_scope[:2])}")
                search_mem.record_attempt(
                    iteration=run_ctx.current_iteration, hypothesis=item.title,
                    files_targeted=item.files, files_modified=diff.files_changed,
                    outcome="rejected_scope", reason=f"Out-of-scope files: {out_of_scope[:3]}",
                    score=None, confidence=None,
                )
                run_ctx.record_reject()
                click.echo(f"└─ Accepts: {run_ctx.total_accepts}, Rejects: {run_ctx.total_rejects}")
                tsv.log(run_ctx.current_iteration - 1, "reject_scope", None, None, diff.files_changed, item.title)
                # Revert out-of-scope working-tree changes
                if run_ctx.accepted_state_sha:
                    git_ops.revert_to_commit(wd, run_ctx.accepted_state_sha)
                continue

        # Get semantic diff for non-text artifacts if plugin supports it
        sem_diff = _get_semantic_diff(active_plugin, diff.files_changed, wd)
        sem_diff_text = sem_diff.as_text() if sem_diff else ""

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
                    _run_gates_and_policy, active_plugin, diff, targets, wd, config,
                )
                review_future = pool.submit(
                    reviewer.run, diff.raw_diff[:6000], item, anchors_judge,
                    {f: summaries.get(f, "") for f in diff.files_changed},
                    active_plugin.reviewer_focus(),
                    sem_diff_text,
                )

                gate_result = gates_future.result()
                eval_status["gates"] = "✓ done" if gate_result["passed"] else f"✗ failed: {', '.join(gate_result['failures'][:2])}"

                review_result = review_future.result()
                eval_status["reviewer"] = f"✓ {review_result.verdict}" if review_result.verdict == "accept" else f"✗ {review_result.verdict}"

        eval_time = time.monotonic() - t0
        click.echo(f"│ Evaluation complete ({eval_time:.0f}s)")

        # Decision
        hypothesis = item.title
        strategy = active_plugin.iteration_strategy()

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
        elif strategy in (IterationStrategy.INTERACTIVE, IterationStrategy.PREVIEW):
            # Show diff and ask user before accepting
            click.echo(f"│ 🔍 Reviewer says ACCEPT (score: {review_result.score:.2f})")
            click.echo(f"│    Reasoning: {review_result.reasoning[:120]}")
            click.echo(f"│    Files: {', '.join(diff.files_changed[:5])}")
            if sem_diff_text:
                click.echo(f"│    Changes: {sem_diff_text[:200]}")
            user_choice = click.prompt(
                "│ Accept this change? [y/n/q]", default="y"
            ).strip().lower()
            if user_choice == "q":
                run_ctx.stop_reason = "User quit during interactive review"
                break
            elif user_choice == "y":
                sha = git_ops.commit(wd, f"autoimprove: {hypothesis[:80]}")
                run_ctx.record_accept(sha)
                run_ctx.current_composite_score = review_result.score
                backlog.mark_done(item)
                click.echo(f"│ ✅ ACCEPTED (user-approved)")
                search_mem.record_attempt(
                    iteration=run_ctx.current_iteration, hypothesis=hypothesis,
                    files_targeted=item.files, files_modified=diff.files_changed,
                    outcome="accepted", reason=f"User-approved: {review_result.reasoning[:150]}",
                    score=review_result.score, confidence=review_result.confidence,
                )
            else:
                click.echo(f"│ ❌ REJECTED by user")
                backlog.mark_failed(item, "user_rejected")
                run_ctx.record_reject()
                search_mem.record_attempt(
                    iteration=run_ctx.current_iteration, hypothesis=hypothesis,
                    files_targeted=item.files, files_modified=diff.files_changed,
                    outcome="rejected_user", reason="User rejected during interactive review",
                    score=review_result.score, confidence=review_result.confidence,
                )
        elif preview:
            # Preview mode: collect proposal without committing
            preview_proposals.append({
                "hypothesis": hypothesis,
                "files": diff.files_changed[:5],
                "score": review_result.score,
                "confidence": review_result.confidence,
                "reasoning": review_result.reasoning[:200],
                "diff_summary": f"+{diff.lines_added}/-{diff.lines_removed} in {len(diff.files_changed)} files",
            })
            click.echo(f"│ 📋 PROPOSAL #{len(preview_proposals)} (score: {review_result.score:.2f})")
            backlog.mark_done(item)
            run_ctx.current_iteration += 1
            search_mem.record_attempt(
                iteration=run_ctx.current_iteration, hypothesis=hypothesis,
                files_targeted=item.files, files_modified=diff.files_changed,
                outcome="preview_proposed", reason=review_result.reasoning[:200],
                score=review_result.score, confidence=review_result.confidence,
            )
            # Check if we've collected enough proposals
            if len(preview_proposals) >= max_preview:
                run_ctx.stop_reason = f"Preview: collected {len(preview_proposals)} proposals"
                break
        else:
            # AUTO strategy: accept without user intervention
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

        # Determine if this iteration was accepted
        was_accepted = (
            review_result.verdict == "accept"
            and (not gate_result or gate_result["passed"])
            and search_mem.hypotheses
            and search_mem.hypotheses[-1].outcome == "accepted"
        )

        # Log to TSV
        if gate_result and not gate_result["passed"]:
            tsv.log(run_ctx.current_iteration - 1, "reject_gate", None, None, diff.files_changed, hypothesis)
        elif not was_accepted:
            tsv.log(run_ctx.current_iteration - 1, "reject_review", review_result.score, review_result.confidence, diff.files_changed, hypothesis)
        else:
            tsv.log(run_ctx.current_iteration - 1, "accept", review_result.score, review_result.confidence, diff.files_changed, hypothesis)

        # Revert if rejected
        if not was_accepted:
            if run_ctx.accepted_state_sha:
                git_ops.revert_to_commit(wd, run_ctx.accepted_state_sha)

        run_ctx.save_state()
        search_mem.save()
        backlog.save(run_ctx.run_dir / "backlog.json")

    # Preview mode: show collected proposals and let user pick
    if preview and preview_proposals:
        click.echo("\n── PREVIEW RESULTS ──")
        click.echo(f"Collected {len(preview_proposals)} proposals:\n")
        for i, prop in enumerate(sorted(preview_proposals, key=lambda p: -p["score"])):
            click.echo(f"  {i+1}. [{prop['score']:.2f}] {prop['hypothesis'][:80]}")
            click.echo(f"     Files: {', '.join(prop['files'][:3])}")
            click.echo(f"     {prop['diff_summary']}")
            click.echo(f"     Reasoning: {prop['reasoning'][:100]}")
            click.echo()
        click.echo("Changes are in the worktree but NOT committed.")
        click.echo(f"To apply:  uv run autoimprove merge {run_ctx.run_id}")
        click.echo(f"To discard: uv run autoimprove discard {run_ctx.run_id}")


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


def _group_into_themes(
    backlog: Backlog,
    plugin_theme_map: dict[str, tuple[str, str, str]] | None = None,
) -> list[tuple[str, str, str, list]]:
    """Group backlog items into themes. Returns [(name, icon, goal, items)].

    *plugin_theme_map* maps category → (display_name, icon, goal).
    Falls back to treating each category as its own theme.
    """
    theme_map = plugin_theme_map or {}

    groups: dict[str, list] = {}
    theme_meta: dict[str, tuple[str, str]] = {}

    for item in backlog.items:
        # Normalize category: strip plugin prefix, lowercase, add underscores
        cat = item.category.lower().strip()
        # Try exact match first, then normalized (with underscores inserted)
        entry = theme_map.get(cat)
        if not entry:
            # Try adding underscores back: "errorhandling" → "error_handling"
            for key in theme_map:
                if key.replace("_", "") == cat.replace("_", ""):
                    entry = theme_map[key]
                    break
        if entry:
            theme_name, icon, goal = entry
        else:
            # Fallback: use the category name directly
            theme_name = cat.replace("_", " ").title()
            icon = "🔧"
            goal = f"Improvements related to {cat.replace('_', ' ')}."
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


def _get_semantic_diff(
    plugin: EvaluatorPlugin, files_changed: list[str], working_dir: str,
) -> SemanticDiff | None:
    """Try to get a semantic diff from the plugin for changed files.

    Returns the first successful semantic diff, or None.
    Binary artifacts (PPTX, DOCX) benefit from semantic diffs since
    git diff produces opaque output for them.
    """
    wd = Path(working_dir)
    for f in files_changed:
        before = str(wd / f)
        after = before  # Same path, working tree has the modified version
        try:
            result = plugin.semantic_diff(before, after)
            if result is not None:
                return result
        except Exception:
            continue
    return None


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
