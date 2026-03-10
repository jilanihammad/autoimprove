"""Search memory — hypothesis tracking and anti-repetition.

Prevents the agent from retrying failed ideas and helps prioritize
promising directions.  Fed back into the agent prompt each iteration.
"""

from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path


@dataclass
class HypothesisRecord:
    iteration: int
    hypothesis: str
    files_targeted: list[str]
    files_actually_modified: list[str]
    outcome: str  # accepted, rejected_policy, rejected_gate, rejected_score, rejected_confidence, rejected_agent_error
    reason: str
    composite_score: float | None
    confidence: float | None
    timestamp: str = ""


@dataclass
class FileChurnRecord:
    file_path: str
    modification_count: int = 0
    net_improvement: bool = False
    iterations: list[int] = field(default_factory=list)


@dataclass
class PatternRecord:
    pattern: str
    occurrences: int = 0
    first_seen: int = 0
    last_seen: int = 0


class SearchMemory:
    """Tracks every hypothesis attempted, its outcome, and patterns."""

    def __init__(self, memory_path: Path) -> None:
        self.memory_path = memory_path
        self.hypotheses: list[HypothesisRecord] = []
        self.file_churn: dict[str, FileChurnRecord] = {}
        self.failure_patterns: list[PatternRecord] = []
        self.success_patterns: list[PatternRecord] = []

    # ------------------------------------------------------------------
    # Recording
    # ------------------------------------------------------------------

    def record_attempt(
        self,
        iteration: int,
        hypothesis: str,
        files_targeted: list[str],
        files_modified: list[str],
        outcome: str,
        reason: str,
        score: float | None,
        confidence: float | None,
    ) -> None:
        rec = HypothesisRecord(
            iteration=iteration,
            hypothesis=hypothesis,
            files_targeted=files_targeted,
            files_actually_modified=files_modified,
            outcome=outcome,
            reason=reason,
            composite_score=score,
            confidence=confidence,
            timestamp=datetime.now(timezone.utc).isoformat(),
        )
        self.hypotheses.append(rec)

        # Update file churn
        accepted = outcome == "accepted"
        for f in files_modified:
            if f not in self.file_churn:
                self.file_churn[f] = FileChurnRecord(file_path=f)
            churn = self.file_churn[f]
            churn.modification_count += 1
            churn.iterations.append(iteration)
            if accepted:
                churn.net_improvement = True

        self._detect_patterns()
        self.save()

    # ------------------------------------------------------------------
    # Prompt generation
    # ------------------------------------------------------------------

    def get_summary_for_prompt(self, max_entries: int = 10) -> str:
        if not self.hypotheses:
            return "No previous attempts."

        lines: list[str] = []

        # Recent attempts
        lines.append("## Recent Attempts")
        recent = self.hypotheses[-max_entries:]
        for h in reversed(recent):
            tag = h.outcome.upper().replace("REJECTED_", "REJECTED:")
            score_str = f"score: {h.composite_score:.2f}" if h.composite_score is not None else ""
            conf_str = f"conf: {h.confidence:.2f}" if h.confidence is not None else ""
            detail = ", ".join(filter(None, [score_str, conf_str]))
            detail_part = f" ({detail})" if detail else ""
            lines.append(f"- Iter {h.iteration}: [{tag}] {h.hypothesis[:120]}{detail_part}")

        # Failure patterns
        if self.failure_patterns:
            lines.append("\n## Avoid (known failure patterns)")
            for p in self.failure_patterns[:5]:
                lines.append(f"- {p.pattern} (seen {p.occurrences}x)")

        # High churn files
        high_churn = self.get_high_churn_files()
        if high_churn:
            lines.append("\n## High-Churn Files (modified 2+ times without net gain)")
            for fc in high_churn[:5]:
                lines.append(f"- {fc.file_path} ({fc.modification_count} modifications, {'improved' if fc.net_improvement else 'no net improvement'})")

        # Success patterns
        if self.success_patterns:
            lines.append("\n## What Works")
            for p in self.success_patterns[:5]:
                lines.append(f"- {p.pattern} (seen {p.occurrences}x)")

        return "\n".join(lines)

    # ------------------------------------------------------------------
    # Similarity detection
    # ------------------------------------------------------------------

    def is_similar_to_previous(self, hypothesis: str, threshold: float = 0.8) -> bool:
        tokens_new = set(_tokenize(hypothesis))
        if not tokens_new:
            return False
        for h in self.hypotheses:
            tokens_old = set(_tokenize(h.hypothesis))
            if not tokens_old:
                continue
            intersection = tokens_new & tokens_old
            union = tokens_new | tokens_old
            if union and len(intersection) / len(union) >= threshold:
                return True
        return False

    # ------------------------------------------------------------------
    # File churn queries
    # ------------------------------------------------------------------

    def get_file_churn(self, file_path: str) -> FileChurnRecord | None:
        return self.file_churn.get(file_path)

    def get_high_churn_files(self, threshold: int = 2) -> list[FileChurnRecord]:
        return [
            fc for fc in self.file_churn.values()
            if fc.modification_count >= threshold and not fc.net_improvement
        ]

    # ------------------------------------------------------------------
    # Pattern detection
    # ------------------------------------------------------------------

    def _detect_patterns(self) -> None:
        # File-based failure patterns
        file_failures: dict[str, int] = {}
        file_successes: dict[str, int] = {}
        for h in self.hypotheses:
            bucket = file_successes if h.outcome == "accepted" else file_failures
            for f in h.files_actually_modified:
                bucket[f] = bucket.get(f, 0) + 1

        self.failure_patterns = []
        for f, count in file_failures.items():
            if count >= 2:
                self.failure_patterns.append(PatternRecord(
                    pattern=f"Modifying {f} tends to be rejected",
                    occurrences=count,
                    first_seen=next(h.iteration for h in self.hypotheses if f in h.files_actually_modified),
                    last_seen=next(h.iteration for h in reversed(self.hypotheses) if f in h.files_actually_modified),
                ))

        # Keyword-based success patterns
        accepted = [h for h in self.hypotheses if h.outcome == "accepted"]
        if len(accepted) >= 2:
            word_counts: dict[str, int] = {}
            for h in accepted:
                for w in _tokenize(h.hypothesis):
                    word_counts[w] = word_counts.get(w, 0) + 1
            self.success_patterns = []
            for w, c in sorted(word_counts.items(), key=lambda x: -x[1]):
                if c >= 2 and len(w) > 4:
                    self.success_patterns.append(PatternRecord(
                        pattern=f"Changes involving '{w}' tend to be accepted",
                        occurrences=c,
                        first_seen=0,
                        last_seen=0,
                    ))
                    if len(self.success_patterns) >= 5:
                        break

    # ------------------------------------------------------------------
    # Persistence
    # ------------------------------------------------------------------

    def save(self) -> None:
        data = {
            "hypotheses": [asdict(h) for h in self.hypotheses],
            "file_churn": {k: asdict(v) for k, v in self.file_churn.items()},
            "failure_patterns": [asdict(p) for p in self.failure_patterns],
            "success_patterns": [asdict(p) for p in self.success_patterns],
        }
        with open(self.memory_path, "w") as f:
            json.dump(data, f, indent=2)

    @classmethod
    def load(cls, memory_path: Path) -> SearchMemory:
        mem = cls(memory_path)
        if not memory_path.exists():
            return mem
        with open(memory_path) as f:
            data = json.load(f)
        mem.hypotheses = [HypothesisRecord(**h) for h in data.get("hypotheses", [])]
        mem.file_churn = {k: FileChurnRecord(**v) for k, v in data.get("file_churn", {}).items()}
        mem.failure_patterns = [PatternRecord(**p) for p in data.get("failure_patterns", [])]
        mem.success_patterns = [PatternRecord(**p) for p in data.get("success_patterns", [])]
        return mem


def _tokenize(text: str) -> list[str]:
    """Simple word tokenizer for similarity comparison."""
    return [w.lower() for w in re.findall(r"[a-zA-Z_][a-zA-Z0-9_]*", text) if len(w) > 2]
