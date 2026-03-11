"""Project memory — persistent cross-run learning.

Stores a compact summary of every run so subsequent runs can avoid
re-trying failed ideas, build on successful patterns, and track
the project's improvement trajectory over time.

Lives at ``.autoimprove/memory.json`` in the target project.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path


@dataclass
class RunSummary:
    """Compact record of a single AutoImprove run."""

    run_id: str
    timestamp: str
    duration_minutes: float
    plugin: str
    total_accepts: int
    total_rejects: int
    stop_reason: str

    # What was tried and what happened
    accepted_hypotheses: list[dict] = field(default_factory=list)
    rejected_hypotheses: list[dict] = field(default_factory=list)

    # Metrics before/after
    baseline_metrics: dict[str, float] = field(default_factory=dict)
    final_metrics: dict[str, float] = field(default_factory=dict)

    # Criteria used
    criteria_summary: list[dict] = field(default_factory=list)

    # Files that improved vs resisted improvement
    improved_files: list[str] = field(default_factory=list)
    resistant_files: list[str] = field(default_factory=list)


class ProjectMemory:
    """Reads/writes cross-run memory for a project."""

    MAX_HYPOTHESES_PER_RUN = 20
    MAX_RUNS = 50
    MAX_CALIBRATIONS = 100

    def __init__(self, project_root: str) -> None:
        self.memory_path = Path(project_root) / ".autoimprove" / "memory.json"
        self.runs: list[RunSummary] = []
        self.calibrations: list[dict] = []
        self._load()

    def _load(self) -> None:
        if not self.memory_path.exists():
            return
        try:
            with open(self.memory_path) as f:
                data = json.load(f)
            self.runs = [RunSummary(**r) for r in data.get("runs", [])]
            self.calibrations = data.get("calibrations", [])
        except (json.JSONDecodeError, TypeError, KeyError):
            self.runs = []
            self.calibrations = []

    def save(self) -> None:
        self.memory_path.parent.mkdir(parents=True, exist_ok=True)
        trimmed = self.runs[-self.MAX_RUNS:]
        cals = self.calibrations[-self.MAX_CALIBRATIONS:]
        with open(self.memory_path, "w") as f:
            json.dump({
                "runs": [asdict(r) for r in trimmed],
                "calibrations": cals,
            }, f, indent=2)

    def record_run(self, summary: RunSummary) -> None:
        self.runs.append(summary)
        self.save()

    def record_calibration(
        self, run_id: str, hypothesis: str, direction: str, explanation: str
    ) -> None:
        """Record user feedback on a false positive/negative.

        Args:
            direction: 'positive' (was accepted but shouldn't have been)
                       or 'negative' (was rejected but should have been accepted)
            explanation: user's reason why
        """
        self.calibrations.append({
            "run_id": run_id,
            "hypothesis": hypothesis,
            "direction": direction,
            "explanation": explanation,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        })
        self.save()

    def get_prompt_context(self, max_runs: int = 5) -> str:
        """Generate a summary for inclusion in the agent's improvement prompt."""
        if not self.runs:
            return ""

        lines = ["## Previous AutoImprove Runs"]
        recent = self.runs[-max_runs:]

        for run in reversed(recent):
            lines.append(
                f"\n### Run {run.run_id} ({run.timestamp[:10]}, "
                f"{run.duration_minutes:.0f}min, "
                f"{run.total_accepts} accepted / {run.total_rejects} rejected)"
            )

            if run.accepted_hypotheses:
                lines.append("Accepted changes:")
                for h in run.accepted_hypotheses[-5:]:
                    score = f" (score: {h['score']:.2f})" if h.get("score") else ""
                    lines.append(f"  ✓ {h['hypothesis'][:120]}{score}")

            if run.rejected_hypotheses:
                lines.append("Rejected (do NOT retry these):")
                for h in run.rejected_hypotheses[-5:]:
                    lines.append(f"  ✗ {h['hypothesis'][:100]} — {h.get('reason', '?')[:60]}")

            if run.resistant_files:
                lines.append(f"Files that resisted improvement: {', '.join(run.resistant_files[:5])}")

        # Aggregate patterns across all runs
        all_rejected = []
        for run in self.runs:
            all_rejected.extend(h["hypothesis"] for h in run.rejected_hypotheses)
        if all_rejected:
            lines.append(f"\nTotal hypotheses rejected across all runs: {len(all_rejected)}")

        return "\n".join(lines)


def build_run_summary(
    run_ctx: "RunContext",
    search_mem: "SearchMemory",
    plugin_name: str,
    baseline_metrics: dict[str, float],
) -> RunSummary:
    """Build a RunSummary from the completed run's state."""
    accepted = []
    rejected = []
    for h in search_mem.hypotheses:
        entry = {
            "hypothesis": h.hypothesis,
            "files": h.files_actually_modified[:5],
            "reason": h.reason[:100],
            "score": h.composite_score,
        }
        if h.outcome == "accepted":
            accepted.append(entry)
        else:
            rejected.append(entry)

    # Trim to keep memory compact
    max_h = ProjectMemory.MAX_HYPOTHESES_PER_RUN
    accepted = accepted[-max_h:]
    rejected = rejected[-max_h:]

    # Identify resistant files: modified 2+ times, never accepted
    resistant = [
        fc.file_path for fc in search_mem.file_churn.values()
        if fc.modification_count >= 2 and not fc.net_improvement
    ]

    # Identify improved files: accepted at least once
    improved = list({
        f for h in search_mem.hypotheses
        if h.outcome == "accepted"
        for f in h.files_actually_modified
    })

    return RunSummary(
        run_id=run_ctx.run_id,
        timestamp=datetime.now(timezone.utc).isoformat(),
        duration_minutes=run_ctx.elapsed_minutes(),
        plugin=plugin_name,
        total_accepts=run_ctx.total_accepts,
        total_rejects=run_ctx.total_rejects,
        stop_reason=run_ctx.stop_reason or "completed",
        accepted_hypotheses=accepted,
        rejected_hypotheses=rejected,
        baseline_metrics=baseline_metrics,
        improved_files=improved[:20],
        resistant_files=resistant[:10],
    )
