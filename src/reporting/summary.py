"""Summary report generation — the primary deliverable of a run."""

from __future__ import annotations

from src.eval.criteria import CriteriaManager
from src.eval.search_memory import SearchMemory
from src.plugins.base import EvaluatorPlugin
from src.reporting.experiment_log import ExperimentLog
from src.run_context import RunContext


def generate_summary(
    run_ctx: RunContext,
    experiment_log: ExperimentLog,
    criteria_mgr: CriteriaManager,
    search_mem: SearchMemory,
    plugin: EvaluatorPlugin,
) -> str:
    """Generate the final Markdown summary report."""
    stats = experiment_log.get_stats()
    lines: list[str] = []

    # 1. Run Summary
    lines.append(f"# AutoImprove Run Report — {run_ctx.run_id}\n")
    lines.append(f"- **Duration**: {run_ctx.elapsed_minutes():.1f} minutes")
    lines.append(f"- **Stop reason**: {run_ctx.stop_reason or 'completed'}")
    lines.append(f"- **Agent**: {run_ctx.config.agent_command}")
    lines.append(f"- **Plugin**: {plugin.name}")
    lines.append(f"- **Source branch**: {run_ctx.source_branch}")
    lines.append("")

    # 2. Results Overview
    lines.append("## Results Overview\n")
    lines.append(f"| Metric | Value |")
    lines.append(f"|--------|-------|")
    lines.append(f"| Total iterations | {stats['total']} |")
    lines.append(f"| Accepted | {stats['accepted']} |")
    lines.append(f"| Rejected | {stats['rejected']} |")
    lines.append(f"| Accept rate | {stats['accept_rate']:.0%} |")
    lines.append(f"| Avg accepted score | {stats['avg_accepted_score']:.2f} |")
    lines.append(f"| Avg accepted confidence | {stats['avg_accepted_confidence']:.2f} |")
    lines.append("")

    # 3. Accepted Changes
    accepted = experiment_log.get_accepted()
    if accepted:
        lines.append("## Accepted Changes\n")
        for e in accepted:
            lines.append(f"### Iteration {e.iteration}")
            lines.append(f"- **Hypothesis**: {e.hypothesis}")
            lines.append(f"- **Score**: {e.composite_score:.2f}" if e.composite_score else "- **Score**: N/A")
            lines.append(f"- **Confidence**: {e.confidence:.2f}" if e.confidence else "- **Confidence**: N/A")
            lines.append(f"- **Files**: {', '.join(e.files_modified)}")
            lines.append(f"- **Diff**: +{e.diff_lines_added}/-{e.diff_lines_removed} lines")
            lines.append("")

    # 4. Rejected Changes (compact)
    rejected = experiment_log.get_rejected()
    if rejected:
        lines.append("## Rejected Changes\n")
        lines.append("| Iter | Hypothesis | Reason | Score | Confidence |")
        lines.append("|------|-----------|--------|-------|------------|")
        for e in rejected:
            score = f"{e.composite_score:.2f}" if e.composite_score else "-"
            conf = f"{e.confidence:.2f}" if e.confidence else "-"
            lines.append(f"| {e.iteration} | {e.hypothesis[:50]} | {e.reason} | {score} | {conf} |")
        lines.append("")

    # 5. Rejection Reasons
    reasons = stats.get("rejection_reasons", {})
    if reasons:
        total_rej = sum(reasons.values())
        lines.append("## Rejection Reasons\n")
        lines.append("| Reason | Count | % |")
        lines.append("|--------|-------|---|")
        for reason, count in sorted(reasons.items(), key=lambda x: -x[1]):
            pct = count / total_rej * 100 if total_rej else 0
            lines.append(f"| {reason} | {count} | {pct:.0f}% |")
        lines.append("")

    # 6. Criteria Proposals
    proposals = criteria_mgr.get_proposals()
    if proposals:
        lines.append("## Criteria Evolution Proposals\n")
        for p in proposals:
            lines.append(f"### Iteration {p.iteration}")
            lines.append(f"- **Rationale**: {p.rationale}")
            for c in p.changes:
                lines.append(f"  - {c.get('action', '?')}: {c.get('reason', '')}")
            lines.append("")

    # 7. Most Modified Files
    files = stats.get("most_modified_files", [])
    if files:
        lines.append("## Most Modified Files\n")
        lines.append("| File | Modifications |")
        lines.append("|------|--------------|")
        for f, count in files:
            lines.append(f"| {f} | {count} |")
        lines.append("")

    # 8. How to Apply
    lines.append("## How to Apply\n")
    lines.append(f"```bash")
    lines.append(f"# Apply accepted changes")
    lines.append(f"uv run autoimprove merge {run_ctx.run_id}")
    lines.append(f"")
    lines.append(f"# Or discard")
    lines.append(f"uv run autoimprove discard {run_ctx.run_id}")
    lines.append(f"")
    lines.append(f"# View diff")
    lines.append(f"git diff {run_ctx.baseline_sha}..{run_ctx.accepted_state_sha}")
    lines.append(f"```")

    return "\n".join(lines)
