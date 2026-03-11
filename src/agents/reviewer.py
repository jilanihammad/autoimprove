"""Reviewer agent — evaluates diffs against eval anchors and product context.

Replaces the generic LLM judge with a product-aware reviewer that
understands the user's definition of 'better' and 'worse'.
"""

from __future__ import annotations

from dataclasses import dataclass

import click

from src.agents.base import BaseAgent
from src.backlog import BacklogItem
from src.config import Config


@dataclass
class ReviewResult:
    verdict: str  # "accept" or "reject"
    reasoning: str
    score: float  # 0.0–1.0
    confidence: float  # 0.0–1.0


class ReviewerAgent(BaseAgent):
    def __init__(self, config: Config) -> None:
        super().__init__(config, "reviewer")

    def run(
        self,
        diff_text: str,
        item: BacklogItem,
        eval_anchors_judge: str,
        file_summaries: dict[str, str],
    ) -> ReviewResult:
        """Review a diff and return accept/reject with reasoning."""
        prompt = self._build_prompt(diff_text, item, eval_anchors_judge, file_summaries)
        result = self.invoke(prompt, "/tmp")

        if not result.success:
            return ReviewResult("reject", f"Reviewer failed: {result.error}", 0.0, 0.0)

        return self._parse_review(result.output)

    def _build_prompt(
        self,
        diff_text: str,
        item: BacklogItem,
        eval_anchors: str,
        file_summaries: dict[str, str],
    ) -> str:
        summaries = "\n".join(
            f"- `{fp}`: {summary}" for fp, summary in file_summaries.items()
        ) if file_summaries else "No summaries available."

        # Truncate diff if massive
        if len(diff_text) > 6000:
            diff_text = diff_text[:6000] + "\n... (truncated)"

        return f"""You are a senior code reviewer. Evaluate whether this diff is a genuine improvement.

## Task That Was Attempted
**{item.title}**: {item.description}

## Context for Affected Files
{summaries}

{eval_anchors}

## The Diff
{diff_text}

## Your Review
Evaluate this diff against the eval anchors above. Consider:
1. Does it achieve the stated task?
2. Does it violate any must-preserve constraints?
3. Is it actually better by the project owner's definition?
4. Are there any regressions, even subtle ones?
5. Is the change focused and minimal, or does it include unnecessary modifications?

Respond ONLY with JSON (no markdown fences):
{{
  "verdict": "accept" or "reject",
  "reasoning": "2-3 sentences explaining your decision",
  "score": 0.0 to 1.0 (0.5 = neutral, higher = better),
  "confidence": 0.0 to 1.0 (how sure you are)
}}"""

    def _parse_review(self, output: str) -> ReviewResult:
        parsed = self.parse_json(output)
        if isinstance(parsed, dict):
            return ReviewResult(
                verdict=parsed.get("verdict", "reject"),
                reasoning=parsed.get("reasoning", "Could not parse review"),
                score=float(parsed.get("score", 0.0)),
                confidence=float(parsed.get("confidence", 0.0)),
            )
        return ReviewResult("reject", "Could not parse reviewer output", 0.0, 0.0)
