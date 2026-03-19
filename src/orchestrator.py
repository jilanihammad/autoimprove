"""Orchestrator — core loop: init → ground → iterate → wrap-up.

Implements the grounding phase (Bead 13), autonomous loop (Bead 14),
and stop conditions (Bead 15).
"""

from __future__ import annotations

import json
import re
import signal
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import click

from src import git_ops
from src.agent_bridge import AgentBridge, AgentRequest
from src.config import Config
from src.eval.criteria import CriteriaItem, CriteriaManager
from src.eval.engine import AcceptanceEngine
from src.eval.eval_anchors import EvalAnchors, load_eval_anchors
from src.eval.llm_judge import LLMJudge
from src.eval.search_memory import SearchMemory
from src.plugins.base import EvaluatorPlugin
from src.plugins.registry import PluginRegistry
from src.preflight import run_preflight
from src.project_memory import ProjectMemory, build_run_summary
from src.repo_index import get_or_generate_index
from src.reporting.experiment_log import TSVLogger
from src.run_context import RunContext
from src.types import Decision, RunStatus

# Global flag for graceful shutdown
_stop_requested = False
_force_quit = False


def _signal_handler(signum: int, frame: object) -> None:
    global _stop_requested, _force_quit
    if _stop_requested:
        _force_quit = True
        click.echo("\nForce quitting...", err=True)
        sys.exit(1)
    _stop_requested = True
    click.echo("\nStopping after current iteration... (press Ctrl+C again to force quit)", err=True)


# ======================================================================
# Top-level entry point
# ======================================================================


def run_autoimprove(config: Config, dry_run: bool = False, preview: bool = False) -> None:
    """Top-level entry point for an AutoImprove run."""
    repo_path = str(Path.cwd().resolve())

    # 1. Preflight
    click.echo("Running preflight checks...")
    pf = run_preflight(config, repo_path)
    for c in pf.checks:
        icon = "✓" if c.passed else ("✗" if c.fatal else "⚠")
        click.echo(f"  {icon} {c.name}: {c.message}")
    if not pf.passed:
        click.echo("\nPreflight failed. Fix errors above and retry.", err=True)
        raise SystemExit(1)
    click.echo()

    # 2. Create run context
    run_ctx = RunContext(config, repo_path)
    run_ctx.initialize()
    click.echo(f"Run ID: {run_ctx.run_id}")
    click.echo(f"Worktree: {run_ctx.worktree_path}")
    click.echo(f"Time budget: {config.time_budget_minutes} minutes")
    click.echo()

    # 3. Discover plugins
    registry = PluginRegistry()
    registry.discover_all(extra_plugin_dirs=config.extra_plugin_dirs)
    detected = registry.detect_plugins_for_paths(config.target_paths, config.exclude_paths)
    if not detected:
        click.echo("No evaluable artifacts found in target paths.", err=True)
        run_ctx.cleanup()
        raise SystemExit(1)

    # Support multi-artifact: detect all plugins, pick primary for single-plugin flow
    all_detected = detected
    plugin_name = list(detected.keys())[0]
    plugin = registry.get(plugin_name)
    targets = detected[plugin_name]
    if len(detected) > 1:
        click.echo("Detected artifact types:")
        for pname, ptargets in detected.items():
            click.echo(f"  {pname}: {len(ptargets)} targets")
    else:
        click.echo(f"Plugin: {plugin.name} ({len(targets)} targets)")

    # 3b. Generate repo index
    click.echo("Generating codebase index...", nl=False)
    index_path = run_ctx.run_dir / "repo_index.md"
    repo_index = get_or_generate_index(targets, str(run_ctx.worktree_path), index_path)
    click.echo(f" done ({len(repo_index)} chars)")

    # 4. Setup components
    agent = AgentBridge(config)
    llm_judge = LLMJudge(config)
    engine = AcceptanceEngine(config, plugin, llm_judge)
    criteria_mgr = CriteriaManager(run_ctx.criteria_dir)
    search_mem = SearchMemory(run_ctx.search_memory_path)
    project_mem = ProjectMemory(repo_path)

    if project_mem.runs:
        click.echo(f"Project memory: {len(project_mem.runs)} previous run(s) loaded")
    else:
        click.echo("Project memory: first run")

    # 4b. Load eval anchors + merge calibrations from memory
    anchors = load_eval_anchors(repo_path)
    anchors.calibrations = project_mem.calibrations
    if anchors.better_means or anchors.worse_means or anchors.must_preserve:
        click.echo(f"Eval anchors: {len(anchors.better_means)} better, {len(anchors.worse_means)} worse, {len(anchors.must_preserve)} must-preserve")
    if anchors.calibrations:
        click.echo(f"Calibrations: {len(anchors.calibrations)} from past feedback")

    # 4c. Distill calibration lessons for eval pipeline
    calibration_lessons = project_mem.get_calibration_lessons(plugin.name)
    if calibration_lessons.get("threshold_delta", 0.0) != 0.0:
        click.echo(f"Calibration threshold adjustment: {calibration_lessons['threshold_delta']:+.2f}")

    try:
        if config.orchestration_mode == "multi":
            # Multi-agent pipeline
            from src.multi_orchestrator import run_multi_agent_grounding, run_multi_agent_loop

            program_path = Path(repo_path) / "program.md"
            program_md = program_path.read_text() if program_path.exists() else ""

            summaries, backlog = run_multi_agent_grounding(
                run_ctx, config, targets, program_md, anchors, project_mem,
                plugin=plugin, calibration_lessons=calibration_lessons,
                all_plugins=all_detected if len(all_detected) > 1 else None,
                registry=registry if len(all_detected) > 1 else None,
            )

            if not backlog.has_pending():
                click.echo("Analyst produced no actionable items.", err=True)
                run_ctx.stop_reason = "Empty backlog"
            elif dry_run:
                # Dry-run: show backlog + what would happen, don't iterate
                click.echo("\n── DRY RUN RESULTS ──")
                click.echo(f"Backlog: {backlog.summary()}")
                click.echo(f"Plugin: {plugin.name} ({plugin.description})")
                click.echo(f"Strategy: {plugin.iteration_strategy().value}")
                click.echo("\nTop 5 proposed improvements:")
                for i, item in enumerate(backlog.items[:5]):
                    click.echo(f"  {i+1}. [{item.priority:.1f}] {item.title}")
                    click.echo(f"     Files: {', '.join(item.files[:3])}")
                click.echo("\nNo changes committed. Run without --dry-run to apply.")
                run_ctx.stop_reason = "dry_run"
            else:
                signal.signal(signal.SIGINT, _signal_handler)
                run_multi_agent_loop(
                    run_ctx, plugin, config, targets, backlog,
                    summaries, anchors, search_mem,
                    program_md=program_md, project_mem=project_mem,
                    calibration_lessons=calibration_lessons,
                    preview=preview,
                    registry=registry if len(all_detected) > 1 else None,
                )
        else:
            # Single-agent mode (original)
            criteria = run_grounding_phase(run_ctx, plugin, agent, criteria_mgr, targets, config)
            if dry_run:
                click.echo("\n── DRY RUN RESULTS ──")
                click.echo(f"Plugin: {plugin.name}")
                click.echo(f"Criteria: {len(criteria_mgr.get_current().items)} items")
                click.echo("No changes committed. Run without --dry-run to apply.")
                run_ctx.stop_reason = "dry_run"
            else:
                signal.signal(signal.SIGINT, _signal_handler)
                run_autonomous_loop(run_ctx, plugin, agent, engine, criteria_mgr, search_mem, config, targets, project_mem, anchors, repo_index, calibration_lessons)

    except KeyboardInterrupt:
        run_ctx.stop_reason = "User interrupt"
    except Exception as e:
        run_ctx.status = RunStatus.FAILED
        run_ctx.stop_reason = str(e)
        click.echo(f"\nRun failed: {e}", err=True)
    finally:
        signal.signal(signal.SIGINT, signal.SIG_DFL)

    # 7. Finalize
    run_ctx.finalize(run_ctx.stop_reason or "completed")

    # 8. Save to project memory
    try:
        baseline_path = run_ctx.baseline_path
        baseline_metrics = {}
        if baseline_path.exists():
            with open(baseline_path) as f:
                baseline_metrics = json.load(f).get("metrics", {})
        summary = build_run_summary(run_ctx, search_mem, plugin.name, baseline_metrics)
        project_mem.record_run(summary)
        click.echo(f"Project memory updated ({len(project_mem.runs)} total runs)")
    except Exception:
        pass  # Memory save is best-effort

    click.echo()
    click.echo("═" * 50)
    click.echo(f"AUTOIMPROVE STOPPED: {run_ctx.stop_reason or 'completed'}")
    click.echo("═" * 50)
    click.echo(f"Iterations: {run_ctx.current_iteration}")
    click.echo(f"Accepted: {run_ctx.total_accepts}  Rejected: {run_ctx.total_rejects}")
    click.echo(f"Duration: {run_ctx.elapsed_minutes():.1f} minutes")

    if run_ctx.total_accepts > 0:
        click.echo()
        _print_review_instructions(run_ctx)


# ======================================================================
# Grounding Phase (Bead 13)
# ======================================================================


def run_grounding_phase(
    run_ctx: RunContext,
    plugin: EvaluatorPlugin,
    agent: AgentBridge,
    criteria_mgr: CriteriaManager,
    targets: list[str],
    config: Config,
) -> None:
    """Interactive grounding: baseline → agent analysis → criteria approval."""
    run_ctx.status = RunStatus.GROUNDING
    click.echo("\n── Grounding Phase ──")

    # 1. Capture baseline
    click.echo("Capturing baseline metrics...")
    click.echo("  ⏳ Running tests...", nl=False)
    baseline = plugin.baseline(targets, str(run_ctx.worktree_path))
    click.echo(" done")
    with open(run_ctx.baseline_path, "w") as f:
        json.dump({
            "plugin_name": baseline.plugin_name,
            "timestamp": baseline.timestamp,
            "metrics": baseline.metrics,
            "targets": baseline.targets,
        }, f, indent=2)

    sha = git_ops.get_head_sha(str(run_ctx.worktree_path), short=False)
    run_ctx.set_baseline(sha)

    click.echo("Baseline metrics:")
    for k, v in baseline.metrics.items():
        click.echo(f"  {k}: {v:.2f}")
    click.echo()

    # 2. Determine criteria
    if config.grounding_mode == "auto":
        click.echo("Auto mode: using default criteria")
        items = _default_criteria_for_plugin(plugin.name)
        criteria = criteria_mgr.create_initial(items, plugin.name, "Auto-generated defaults")
    else:
        # Try agent-proposed criteria
        criteria = _interactive_grounding(run_ctx, plugin, agent, criteria_mgr, targets, config)

    click.echo(f"\nCriteria v{criteria.version} ({len(criteria.items)} items):")
    for item in criteria.items:
        tag = "[GATE]" if item.is_hard_gate else f"[w={item.weight:.2f}]"
        click.echo(f"  {tag} {item.name}: {item.description}")

    # 3. Set initial state
    run_ctx.current_composite_score = 0.0
    run_ctx.status = RunStatus.RUNNING
    run_ctx.save_state()
    click.echo("\nGrounding complete. Starting autonomous loop.\n")


def _interactive_grounding(run_ctx, plugin, agent, criteria_mgr, targets, config):
    """Try agent-proposed criteria with user approval, fall back to defaults."""
    # Read program.md
    program_path = Path(run_ctx.repo_path) / "program.md"
    program_md = program_path.read_text() if program_path.exists() else ""

    # Read profile
    profile_path = Path(run_ctx.repo_path) / "profiles" / f"{plugin.name}.md"
    profile_md = profile_path.read_text() if profile_path.exists() else ""

    artifact_summary = f"{len(targets)} files detected for plugin '{plugin.name}'"

    prompt = agent.build_grounding_prompt(program_md, profile_md, artifact_summary)
    request = AgentRequest(
        prompt=prompt,
        working_dir=str(run_ctx.worktree_path),
        timeout_seconds=config.agent_timeout_seconds,
        mode="analyze",
    )

    click.echo("Asking agent to propose evaluation criteria...")
    response = agent.invoke(request)

    if response.success:
        items = _parse_criteria_from_response(response.output)
        if items:
            click.echo("\nAgent-proposed criteria:")
            for item in items:
                tag = "[GATE]" if item.is_hard_gate else f"[w={item.weight:.2f}]"
                click.echo(f"  {tag} {item.name}: {item.description}")

            choice = click.prompt("\nAccept criteria? [y/n/defaults]", default="y")
            if choice.lower() == "y":
                return criteria_mgr.create_initial(items, plugin.name, "Agent-proposed, user-approved")
            if choice.lower() == "n":
                click.echo("Run aborted.")
                run_ctx.cleanup()
                raise SystemExit(0)

    click.echo("Using default criteria.")
    items = _default_criteria_for_plugin(plugin.name)
    return criteria_mgr.create_initial(items, plugin.name, "Default criteria")


def _parse_criteria_from_response(output: str) -> list[CriteriaItem] | None:
    """Try to extract criteria items from agent JSON response."""
    try:
        match = re.search(r"\{.*\}", output, re.DOTALL)
        if not match:
            return None
        data = json.loads(match.group(0))
        raw_items = data.get("criteria", [])
        items: list[CriteriaItem] = []
        for r in raw_items:
            items.append(CriteriaItem(
                name=r.get("name", "unknown"),
                description=r.get("description", ""),
                weight=float(r.get("weight", 0.0)),
                is_hard_gate=bool(r.get("is_hard_gate", False)),
                metric_type=r.get("metric_type", "judgment"),
            ))
        return items if items else None
    except (json.JSONDecodeError, KeyError, TypeError, ValueError):
        return None


def _default_criteria_for_plugin(plugin_name: str) -> list[CriteriaItem]:
    if plugin_name == "code":
        return CriteriaManager.default_code_criteria()
    if plugin_name == "document":
        return CriteriaManager.default_document_criteria()
    if plugin_name == "workflow":
        return CriteriaManager.default_workflow_criteria()
    return CriteriaManager.default_code_criteria()


# ======================================================================
# Autonomous Loop (Bead 14)
# ======================================================================


def run_autonomous_loop(
    run_ctx: RunContext,
    plugin: EvaluatorPlugin,
    agent: AgentBridge,
    engine: AcceptanceEngine,
    criteria_mgr: CriteriaManager,
    search_mem: SearchMemory,
    config: Config,
    targets: list[str],
    project_mem: ProjectMemory,
    anchors: EvalAnchors,
    repo_index: str,
    calibration_lessons: dict | None = None,
) -> None:
    """The heart of AutoImprove: iterate until a stop condition is met."""
    program_path = Path(run_ctx.repo_path) / "program.md"
    program_md = program_path.read_text() if program_path.exists() else ""
    project_memory_context = project_mem.get_prompt_context()
    # Enrich judge anchors with calibration context
    cal_judge_ctx = (calibration_lessons or {}).get("judge_context", "")
    anchors_for_judge = anchors.for_judge_prompt()
    if cal_judge_ctx:
        anchors_for_judge = anchors_for_judge + "\n\n" + cal_judge_ctx
    anchors_for_agent = anchors.for_agent_prompt()
    calibration_threshold_delta = (calibration_lessons or {}).get("threshold_delta", 0.0)
    tsv = TSVLogger(run_ctx.run_dir / "experiment_log.tsv")

    while True:
        # Check stop conditions
        should, reason = should_stop(run_ctx, search_mem, config)
        if should:
            run_ctx.stop_reason = reason
            break

        iteration = run_ctx.current_iteration
        budget = run_ctx.budget_remaining_minutes()
        click.echo(f"\n┌─ Iteration {iteration} ({'%.0f' % budget}min remaining) ─────────────────")

        # 1. Build prompt
        click.echo("│ ⏳ Building prompt...", nl=False)
        prompt = agent.build_improvement_prompt(
            program_md=program_md,
            search_memory_summary=search_mem.get_summary_for_prompt(),
            iteration=iteration,
            criteria_summary=criteria_mgr.get_current().to_summary_string(),
            previous_outcomes=_recent_outcomes(search_mem),
            project_memory=project_memory_context,
            eval_anchors=anchors_for_agent,
            repo_index=repo_index,
        )
        click.echo(" done")

        # 2. Invoke agent
        click.echo(f"│ ⏳ Agent working (timeout: {config.agent_timeout_seconds}s)...", nl=False)
        t0 = time.monotonic()
        request = AgentRequest(
            prompt=prompt,
            working_dir=str(run_ctx.worktree_path),
            timeout_seconds=config.agent_timeout_seconds,
            mode="modify",
        )
        response = agent.invoke(request)
        agent_time = time.monotonic() - t0
        click.echo(f" {agent_time:.0f}s")

        if not response.success:
            reason_short = (response.error or "Unknown error")[:120]
            click.echo(f"│ ✗ Agent failed: {reason_short}")
            search_mem.record_attempt(
                iteration=iteration,
                hypothesis="Agent invocation failed",
                files_targeted=[], files_modified=[],
                outcome="rejected_agent_error",
                reason=response.error or "Unknown error",
                score=None, confidence=None,
            )
            run_ctx.record_reject()
            click.echo(f"└─ REJECTED (agent error) | Accepts: {run_ctx.total_accepts}, Rejects: {run_ctx.total_rejects}")

            # Check for repeated identical failures — ask user for help
            if config.grounding_mode != "auto" and _should_ask_user(search_mem, "rejected_agent_error"):
                action = _ask_user_for_help(
                    "Agent keeps failing with the same error",
                    response.error or "Unknown error",
                )
                if action == "stop":
                    run_ctx.stop_reason = "User stopped after repeated agent errors"
                    break
                elif action == "skip":
                    continue
            continue

        # 3. Capture diff
        click.echo("│ ⏳ Checking diff...", nl=False)
        diff = git_ops.get_diff(str(run_ctx.worktree_path), run_ctx.accepted_state_sha)

        if not diff.files_changed:
            click.echo(" no changes")
            search_mem.record_attempt(
                iteration=iteration,
                hypothesis="No changes made",
                files_targeted=[], files_modified=[],
                outcome="rejected_empty",
                reason="Agent made no changes",
                score=None, confidence=None,
            )
            run_ctx.record_reject()
            click.echo(f"└─ REJECTED (no changes) | Accepts: {run_ctx.total_accepts}, Rejects: {run_ctx.total_rejects}")
            continue

        click.echo(f" {len(diff.files_changed)} files, +{diff.lines_added}/-{diff.lines_removed} lines")

        # 4. Extract hypothesis
        hypothesis = _extract_hypothesis(response.output)
        click.echo(f"│ 💡 Hypothesis: {hypothesis[:100]}")

        # 5. Evaluate
        click.echo("│ ⏳ Running evaluation (tests, lint, typecheck, LLM judge)...", nl=False)
        t0 = time.monotonic()
        criteria_dict = criteria_mgr.get_current().to_dict()
        decision = engine.evaluate(
            diff=diff,
            targets=targets,
            current_state_score=run_ctx.current_composite_score,
            criteria=criteria_dict,
            criteria_version=criteria_mgr.get_current().version,
            working_dir=str(run_ctx.worktree_path),
            eval_anchors_text=anchors_for_judge,
            calibration_threshold_delta=calibration_threshold_delta,
        )
        eval_time = time.monotonic() - t0
        click.echo(f" {eval_time:.0f}s")

        # 6. Act on decision
        score_str = f"{decision.composite_score:.2f}" if decision.composite_score else "-"
        conf_str = f"{decision.confidence:.2f}" if decision.confidence else "-"

        if decision.decision == Decision.ACCEPT:
            sha = git_ops.commit(
                str(run_ctx.worktree_path),
                f"autoimprove: iter {iteration} — {hypothesis[:80]}",
            )
            run_ctx.record_accept(sha)
            if decision.composite_score is not None:
                run_ctx.current_composite_score = decision.composite_score
            click.echo(f"│ ✅ ACCEPTED (score: {score_str}, confidence: {conf_str})")
            click.echo(f"│    Files: {', '.join(diff.files_changed[:5])}")
            tsv.log(iteration, "accept", decision.composite_score, decision.confidence, diff.files_changed, hypothesis)
        else:
            run_ctx.record_reject()
            detail_str = json.dumps(decision.detail)[:150]
            click.echo(f"│ ❌ REJECTED: {decision.reason}")
            click.echo(f"│    Detail: {detail_str}")
            tsv.log(iteration, f"reject_{decision.reason}", decision.composite_score, decision.confidence, diff.files_changed, hypothesis)

        click.echo(f"└─ Accepts: {run_ctx.total_accepts}, Rejects: {run_ctx.total_rejects} | Score: {score_str}, Conf: {conf_str}")

        # 7. Update search memory
        search_mem.record_attempt(
            iteration=iteration,
            hypothesis=hypothesis,
            files_targeted=diff.files_changed,
            files_modified=response.files_modified,
            outcome=decision.reason,
            reason=json.dumps(decision.detail)[:200],
            score=decision.composite_score,
            confidence=decision.confidence,
        )

        # 8. Criteria review (every N iterations)
        if (
            iteration > 0
            and config.eval_refinement_interval > 0
            and iteration % config.eval_refinement_interval == 0
        ):
            _do_criteria_review(agent, criteria_mgr, search_mem, config, run_ctx, iteration)

        # 8b. Check for repeated rejections — ask user for guidance
        if (
            config.grounding_mode != "auto"
            and decision.decision == Decision.REJECT
            and _should_ask_user(search_mem, decision.reason)
        ):
            action = _ask_user_for_help(
                f"Last 3 attempts rejected for the same reason: {decision.reason}",
                json.dumps(decision.detail, indent=2)[:500],
            )
            if action == "stop":
                run_ctx.stop_reason = "User stopped after repeated rejections"
                break

        # 9. Save state
        run_ctx.save_state()
        search_mem.save()


# ======================================================================
# Stop Conditions (Bead 15)
# ======================================================================


def should_stop(
    run_ctx: RunContext, search_mem: SearchMemory, config: Config
) -> tuple[bool, str]:
    """Check all stop conditions. Returns (should_stop, reason)."""
    global _stop_requested

    # 1. User interrupt
    if _stop_requested:
        return True, "User requested stop (Ctrl+C)"

    # 2. Time budget
    if run_ctx.is_budget_exhausted():
        elapsed = run_ctx.elapsed_minutes()
        return True, f"Time budget exhausted ({elapsed:.1f}/{config.time_budget_minutes} minutes)"

    # 3. Max iterations
    if config.max_iterations and run_ctx.current_iteration >= config.max_iterations:
        return True, f"Max iterations reached ({run_ctx.current_iteration}/{config.max_iterations})"

    # 4. Consecutive rejections
    if run_ctx.consecutive_rejections >= config.max_consecutive_rejections:
        return True, f"Too many consecutive rejections ({run_ctx.consecutive_rejections}/{config.max_consecutive_rejections})"

    # 5. File churn
    high_churn = search_mem.get_high_churn_files(config.max_file_churn)
    if high_churn:
        f = high_churn[0]
        return True, f"File churn detected: {f.file_path} modified {f.modification_count} times with no net improvement"

    # 6. Confidence trending below threshold
    recent = search_mem.hypotheses[-5:]
    if len(recent) >= 5:
        confs = [h.confidence for h in recent if h.confidence is not None]
        if confs and sum(confs) / len(confs) < config.min_confidence_threshold:
            avg = sum(confs) / len(confs)
            return True, f"Confidence trending below threshold ({avg:.2f} < {config.min_confidence_threshold})"

    # 7. No improvements possible (use config threshold, not hardcoded 10)
    stall_threshold = max(config.max_consecutive_rejections, 10)
    if len(search_mem.hypotheses) >= stall_threshold:
        last_n = search_mem.hypotheses[-stall_threshold:]
        if all(h.outcome != "accepted" for h in last_n):
            return True, f"No further improvements found after {stall_threshold} attempts"

    return False, ""


# ======================================================================
# Helpers
# ======================================================================


def _extract_hypothesis(output: str) -> str:
    """Extract the agent's stated hypothesis from its output."""
    for line in output.splitlines():
        stripped = line.strip()
        if stripped.lower().startswith("hypothesis:"):
            return stripped[len("hypothesis:"):].strip()
        if stripped.lower().startswith("i will"):
            return stripped
    # Fall back to first non-empty line
    for line in output.splitlines():
        stripped = line.strip()
        if stripped and len(stripped) > 10:
            return stripped[:200]
    return "Unknown hypothesis"


def _should_ask_user(search_mem: SearchMemory, current_reason: str, threshold: int = 3) -> bool:
    """Return True if the last N attempts all failed with the same reason."""
    recent = search_mem.hypotheses[-threshold:]
    if len(recent) < threshold:
        return False
    return all(h.outcome == current_reason for h in recent)


def _ask_user_for_help(problem: str, detail: str) -> str:
    """Pause and ask the user what to do. Returns 'retry', 'skip', or 'stop'."""
    click.echo()
    click.echo("═" * 50)
    click.echo(f"⚠  NEEDS YOUR INPUT: {problem}")
    click.echo(f"   Detail: {detail[:300]}")
    click.echo("═" * 50)
    choice = click.prompt(
        "What should I do? [r]etry / [s]kip & continue / [q]uit",
        default="r",
    )
    choice = choice.strip().lower()
    if choice in ("q", "quit", "stop"):
        return "stop"
    if choice in ("s", "skip"):
        return "skip"
    return "retry"


def _recent_outcomes(search_mem: SearchMemory, n: int = 5) -> list[str]:
    return [
        f"Iter {h.iteration}: {h.outcome} — {h.hypothesis[:80]}"
        for h in search_mem.hypotheses[-n:]
    ]


def _print_iteration(iteration: int, tag: str, detail: str, run_ctx: RunContext) -> None:
    budget = run_ctx.budget_remaining_minutes()
    click.echo(
        f"[Iter {iteration}] {tag} — {detail} | "
        f"Budget: {budget:.0f}min | "
        f"Accepts: {run_ctx.total_accepts}, Rejects: {run_ctx.total_rejects}"
    )


def _print_review_instructions(run_ctx: RunContext) -> None:
    """Print post-run instructions: how to review, test, and accept/discard."""
    wt = run_ctx.worktree_path
    baseline = run_ctx.baseline_sha or "HEAD~1"

    click.echo("── Review Changes ──")
    click.echo()
    click.echo("  See what changed (full diff):")
    click.echo(f"    cd {wt}")
    click.echo(f"    git diff {baseline[:8]}..HEAD")
    click.echo()
    click.echo("  See each change individually:")
    click.echo(f"    cd {wt}")
    click.echo(f"    git log --oneline {baseline[:8]}..HEAD")
    click.echo(f"    git show <commit-sha>")
    click.echo()

    # Detect test commands from the worktree
    test_cmds = _detect_test_commands(str(wt))
    if test_cmds:
        click.echo("── Test Before Accepting ──")
        click.echo()
        for desc, cmd, cwd in test_cmds:
            click.echo(f"  {desc}:")
            click.echo(f"    cd {cwd}")
            click.echo(f"    {cmd}")
        click.echo()

    click.echo("── Accept or Discard ──")
    click.echo()
    click.echo(f"  To apply changes:  uv run autoimprove merge {run_ctx.run_id}")
    click.echo(f"  To discard:        uv run autoimprove discard {run_ctx.run_id}")
    click.echo()


def _detect_test_commands(worktree_path: str) -> list[tuple[str, str, str]]:
    """Detect available test/run commands in the worktree.

    Returns list of (description, command, working_directory).
    """
    root = Path(worktree_path)
    cmds: list[tuple[str, str, str]] = []

    # Check for package.json with scripts (root + one level deep)
    for search_dir in [root] + [d for d in sorted(root.iterdir()) if d.is_dir() and d.name not in ("node_modules", ".next", ".autoimprove", ".git")]:
        pkg_path = search_dir / "package.json"
        if not pkg_path.exists():
            continue
        try:
            data = json.loads(pkg_path.read_text())
        except (json.JSONDecodeError, OSError):
            continue
        scripts = data.get("scripts", {})
        rel = search_dir.relative_to(root) if search_dir != root else Path(".")
        label = str(rel) if str(rel) != "." else "root"

        if "test" in scripts:
            cmds.append((f"Run tests ({label})", "npm test", str(search_dir)))
        if "dev" in scripts:
            cmds.append((f"Start dev server ({label})", "npm run dev", str(search_dir)))
        elif "start" in scripts:
            cmds.append((f"Start app ({label})", "npm start", str(search_dir)))

    # Check for Python projects
    for search_dir in [root] + [d for d in sorted(root.iterdir()) if d.is_dir() and d.name not in ("node_modules", ".next", ".autoimprove", ".git")]:
        if (search_dir / "pyproject.toml").exists() or (search_dir / "setup.py").exists():
            rel = search_dir.relative_to(root) if search_dir != root else Path(".")
            label = str(rel) if str(rel) != "." else "root"
            cmds.append((f"Run tests ({label})", "pytest", str(search_dir)))
            break  # Only add once for Python

    return cmds


def _do_criteria_review(agent, criteria_mgr, search_mem, config, run_ctx, iteration):
    """Ask agent to propose criteria changes (logged only, not applied)."""
    try:
        prompt = agent.build_criteria_review_prompt(
            current_criteria=criteria_mgr.get_current().to_json(),
            experiment_history=search_mem.get_summary_for_prompt(max_entries=20),
            iteration=iteration,
        )
        response = agent.invoke(AgentRequest(
            prompt=prompt,
            working_dir=str(run_ctx.worktree_path),
            timeout_seconds=config.agent_timeout_seconds,
            mode="analyze",
        ))
        if response.success:
            match = re.search(r"\{.*\}", response.output, re.DOTALL)
            if match:
                data = json.loads(match.group(0))
                changes = data.get("changes", [])
                rationale = data.get("rationale", "")
                if changes:
                    criteria_mgr.record_proposal(iteration, changes, rationale)
    except Exception:
        pass  # Criteria review is best-effort
