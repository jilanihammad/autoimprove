"""Experiment logging — structured per-iteration audit trail.

Append-only log written to disk after every iteration for crash recovery.
Primary input for the summary report.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from src.types import Decision, Diff


@dataclass
class ExperimentEntry:
    iteration: int
    timestamp: str
    hypothesis: str
    files_modified: list[str]
    diff_lines_added: int
    diff_lines_removed: int
    diff_snippet: str  # first 200 lines of diff
    decision: str  # accepted / rejected
    reason: str
    reason_detail: str
    composite_score: float | None
    confidence: float | None
    confidence_breakdown: dict[str, float] = field(default_factory=dict)
    criteria_version: int = 1
    agent_duration_seconds: float = 0.0
    eval_duration_seconds: float = 0.0
    total_duration_seconds: float = 0.0
    accepted_state_score_before: float = 0.0
    accepted_state_score_after: float = 0.0
    cumulative_accepts: int = 0
    cumulative_rejects: int = 0
    budget_remaining_minutes: float = 0.0


class ExperimentLog:
    """Append-only log of every iteration with full evidence."""

    def __init__(self, log_path: Path) -> None:
        self.log_path = log_path
        self._entries: list[ExperimentEntry] = []

    def append(self, entry: ExperimentEntry) -> None:
        self._entries.append(entry)
        self.save()

    def get_all(self) -> list[ExperimentEntry]:
        return list(self._entries)

    def get_accepted(self) -> list[ExperimentEntry]:
        return [e for e in self._entries if e.decision == "accepted"]

    def get_rejected(self) -> list[ExperimentEntry]:
        return [e for e in self._entries if e.decision != "accepted"]

    def get_by_iteration(self, iteration: int) -> ExperimentEntry | None:
        for e in self._entries:
            if e.iteration == iteration:
                return e
        return None

    def get_stats(self) -> dict:
        total = len(self._entries)
        accepted = len(self.get_accepted())
        rejected = total - accepted
        accept_rate = accepted / total if total > 0 else 0.0

        # Rejection reasons breakdown
        reasons: dict[str, int] = {}
        for e in self.get_rejected():
            reasons[e.reason] = reasons.get(e.reason, 0) + 1

        # Most modified files
        file_counts: dict[str, int] = {}
        for e in self._entries:
            for f in e.files_modified:
                file_counts[f] = file_counts.get(f, 0) + 1

        # Average scores for accepted
        scores = [e.composite_score for e in self.get_accepted() if e.composite_score is not None]
        confs = [e.confidence for e in self.get_accepted() if e.confidence is not None]

        return {
            "total": total,
            "accepted": accepted,
            "rejected": rejected,
            "accept_rate": accept_rate,
            "avg_accepted_score": sum(scores) / len(scores) if scores else 0.0,
            "avg_accepted_confidence": sum(confs) / len(confs) if confs else 0.0,
            "rejection_reasons": reasons,
            "most_modified_files": sorted(file_counts.items(), key=lambda x: -x[1])[:10],
        }

    def save(self) -> None:
        with open(self.log_path, "w") as f:
            json.dump([asdict(e) for e in self._entries], f, indent=2)

    @classmethod
    def load(cls, log_path: Path) -> ExperimentLog:
        log = cls(log_path)
        if not log_path.exists():
            return log
        with open(log_path) as f:
            data = json.load(f)
        log._entries = [ExperimentEntry(**e) for e in data]
        return log


class TSVLogger:
    """Simple tab-separated experiment log — one row per iteration, human-scannable."""

    HEADER = "iteration\tstatus\tscore\tconfidence\tfiles_changed\thypothesis\n"

    def __init__(self, path: Path) -> None:
        self.path = path
        if not path.exists():
            path.write_text(self.HEADER)

    def log(
        self,
        iteration: int,
        status: str,
        score: float | None,
        confidence: float | None,
        files: list[str],
        hypothesis: str,
    ) -> None:
        score_s = f"{score:.4f}" if score is not None else "-"
        conf_s = f"{confidence:.2f}" if confidence is not None else "-"
        files_s = ",".join(files[:5]) or "-"
        hyp_s = hypothesis[:120].replace("\t", " ").replace("\n", " ")
        line = f"{iteration}\t{status}\t{score_s}\t{conf_s}\t{files_s}\t{hyp_s}\n"
        with open(self.path, "a") as f:
            f.write(line)


def create_entry(
    iteration: int,
    hypothesis: str,
    diff: Diff | None,
    decision: Decision | str,
    reason: str,
    reason_detail: str = "",
    composite_score: float | None = None,
    confidence: float | None = None,
    confidence_breakdown: dict | None = None,
    agent_duration: float = 0.0,
    eval_duration: float = 0.0,
    run_ctx: object | None = None,
) -> ExperimentEntry:
    """Helper to create an ExperimentEntry from iteration data."""
    snippet = ""
    lines_added = 0
    lines_removed = 0
    files: list[str] = []
    if diff:
        lines_added = diff.lines_added
        lines_removed = diff.lines_removed
        files = diff.files_changed
        snippet_lines = diff.raw_diff.splitlines()[:200]
        snippet = "\n".join(snippet_lines)

    dec_str = decision.value if isinstance(decision, Decision) else str(decision)

    entry = ExperimentEntry(
        iteration=iteration,
        timestamp=datetime.now(timezone.utc).isoformat(),
        hypothesis=hypothesis,
        files_modified=files,
        diff_lines_added=lines_added,
        diff_lines_removed=lines_removed,
        diff_snippet=snippet,
        decision=dec_str,
        reason=reason,
        reason_detail=reason_detail,
        composite_score=composite_score,
        confidence=confidence,
        confidence_breakdown=confidence_breakdown or {},
        agent_duration_seconds=agent_duration,
        eval_duration_seconds=eval_duration,
        total_duration_seconds=agent_duration + eval_duration,
    )

    if run_ctx is not None:
        entry.accepted_state_score_before = getattr(run_ctx, "current_composite_score", 0.0)
        entry.cumulative_accepts = getattr(run_ctx, "total_accepts", 0)
        entry.cumulative_rejects = getattr(run_ctx, "total_rejects", 0)
        entry.budget_remaining_minutes = getattr(run_ctx, "budget_remaining_minutes", lambda: 0.0)()

    return entry
