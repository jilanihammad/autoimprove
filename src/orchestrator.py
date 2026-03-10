"""Orchestrator — core loop: init → ground → iterate → wrap-up.

Implements the grounding phase (Bead 13), autonomous loop (Bead 14),
and stop conditions (Bead 15).
"""

from __future__ import annotations

import json
import re
import signal
import sys
from datetime import datetime, timezone
from pathlib import Path

import click

from src import git_ops
from src.agent_bridge import AgentBridge, AgentRequest
from src.config import Config
from src.eval.criteria import CriteriaItem, CriteriaManager
from src.eval.engine import AcceptanceEngine
from src.eval.llm_judge import LLMJudge
from src.eval.search_memory import SearchMemory
from src.plugins.base import EvaluatorPlugin
from src.plugins.registry import PluginRegistry
from src.preflight import run_preflight
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


def run_autoimprove(config: Config) -> None:
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
    registry.discover_and_register_defaults()
    detected = registry.detect_plugins_for_paths(config.target_paths, config.exclude_paths)
    if not detected:
        click.echo("No evaluable artifacts found in target paths.", err=True)
        run_ctx.cleanup()
        raise SystemExit(1)

    plugin_name = list(detected.keys())[0]
    plugin = registry.get(plugin_name)
    targets = detected[plugin_name]
    click.echo(f"Plugin: {plugin.name} ({len(targets)} targets)")

    # 4. Setup components
    agent = AgentBridge(config)
    llm_judge = LLMJudge(config)
    engine = AcceptanceEngine(config, plugin, llm_judge)
    criteria_mgr = CriteriaManager(run_ctx.criteria_dir)
    search_mem = SearchMemory(run_ctx.search_memory_path)

    try:
        # 5. Grounding phase
        criteria = run_grounding_phase(run_ctx, plugin, agent, criteria_mgr, targets, config)

        # 6. Autonomous loop
        signal.signal(signal.SIGINT, _signal_handler)
        run_autonomous_loop(run_ctx, plugin, agent, engine, criteria_mgr, search_mem, config, targets)

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
    click.echo()
    click.echo("═" * 50)
    click.echo(f"AUTOIMPROVE STOPPED: {run_ctx.stop_reason or 'completed'}")
    click.echo("═" * 50)
    click.echo(f"Iterations: {run_ctx.current_iteration}")
    click.echo(f"Accepted: {run_ctx.total_accepts}  Rejected: {run_ctx.total_rejects}")
    click.echo(f"Duration: {run_ctx.elapsed_minutes():.1f} minutes")
    click.echo()
    click.echo(f"To apply changes:  uv run autoimprove merge {run_ctx.run_id}")
    click.echo(f"To discard:        uv run autoimprove discard {run_ctx.run_id}")


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
    baseline = plugin.baseline(targets, str(run_ctx.worktree_path))
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
) -> None:
    """The heart of AutoImprove: iterate until a stop condition is met."""
    program_path = Path(run_ctx.repo_path) / "program.md"
    program_md = program_path.read_text() if program_path.exists() else ""

    while True:
        # Check stop conditions
        should, reason = should_stop(run_ctx, search_mem, config)
        if should:
            run_ctx.stop_reason = reason
            break

        iteration = run_ctx.current_iteration

        # 1. Build prompt
        prompt = agent.build_improvement_prompt(
            program_md=program_md,
            search_memory_summary=search_mem.get_summary_for_prompt(),
            iteration=iteration,
            criteria_summary=criteria_mgr.get_current().to_summary_string(),
            previous_outcomes=_recent_outcomes(search_mem),
        )

        # 2. Invoke agent
        request = AgentRequest(
            prompt=prompt,
            working_dir=str(run_ctx.worktree_path),
            timeout_seconds=config.agent_timeout_seconds,
            mode="modify",
        )
        response = agent.invoke(request)

        if not response.success:
            search_mem.record_attempt(
                iteration=iteration,
                hypothesis="Agent invocation failed",
                files_targeted=[], files_modified=[],
                outcome="rejected_agent_error",
                reason=response.error or "Unknown error",
                score=None, confidence=None,
            )
            run_ctx.record_reject()
            _print_iteration(iteration, "REJECTED:agent", "Agent failed", run_ctx)
            continue

        # 3. Capture diff
        diff = git_ops.get_diff(str(run_ctx.worktree_path), run_ctx.accepted_state_sha)

        if not diff.files_changed:
            search_mem.record_attempt(
                iteration=iteration,
                hypothesis="No changes made",
                files_targeted=[], files_modified=[],
                outcome="rejected_empty",
                reason="Agent made no changes",
                score=None, confidence=None,
            )
            run_ctx.record_reject()
            _print_iteration(iteration, "REJECTED:empty", "No changes", run_ctx)
            continue

        # 4. Extract hypothesis
        hypothesis = _extract_hypothesis(response.output)

        # 5. Evaluate
        criteria_dict = criteria_mgr.get_current().to_dict()
        decision = engine.evaluate(
            diff=diff,
            targets=targets,
            current_state_score=run_ctx.current_composite_score,
            criteria=criteria_dict,
            criteria_version=criteria_mgr.get_current().version,
            working_dir=str(run_ctx.worktree_path),
        )

        # 6. Act on decision
        if decision.decision == Decision.ACCEPT:
            sha = git_ops.commit(
                str(run_ctx.worktree_path),
                f"autoimprove: iter {iteration} — {hypothesis[:80]}",
            )
            run_ctx.record_accept(sha)
            if decision.composite_score is not None:
                run_ctx.current_composite_score = decision.composite_score
            tag = "ACCEPTED"
        else:
            run_ctx.record_reject()
            tag = f"REJECTED:{decision.reason}"

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

        # 9. Print progress
        score_str = f"{decision.composite_score:.2f}" if decision.composite_score else "-"
        conf_str = f"{decision.confidence:.2f}" if decision.confidence else "-"
        _print_iteration(
            iteration, tag,
            f"{hypothesis[:60]} (score:{score_str} conf:{conf_str})",
            run_ctx,
        )

        # 10. Save state
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

    # 7. No improvements possible (10 consecutive rejections)
    if len(search_mem.hypotheses) >= 10:
        last_10 = search_mem.hypotheses[-10:]
        if all(h.outcome != "accepted" for h in last_10):
            return True, f"No further improvements found after {len(last_10)} attempts"

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
