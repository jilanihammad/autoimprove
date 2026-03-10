"""LLM-as-judge — pairwise comparison with context separation.

Design principles:
1. Judge NEVER sees the improvement hypothesis or agent reasoning
2. Pairwise comparison: candidate vs current accepted state
3. Structured output: per-rubric-item scores + reasoning
4. Repeated judging with aggregation for low-confidence artifacts
5. Every call tagged with criteria version
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import tempfile
import time
from dataclasses import dataclass, field
from statistics import mean, variance as stat_variance

from src.config import Config


class JudgeParseError(Exception):
    def __init__(self, raw_response: str, parse_error: str) -> None:
        self.raw_response = raw_response
        self.parse_error = parse_error
        super().__init__(f"Failed to parse judge response: {parse_error}")


class JudgeLLMError(Exception):
    def __init__(self, model: str, error: str) -> None:
        self.model = model
        self.error = error
        super().__init__(f"LLM judge error ({model}): {error}")


@dataclass
class JudgeRubricItem:
    name: str
    description: str
    weight: float


@dataclass
class JudgeScore:
    rubric_item: str
    score: float  # 0.0 (much worse) to 1.0 (much better), 0.5 = no change
    reasoning: str


@dataclass
class JudgeResult:
    scores: list[JudgeScore]
    composite_score: float
    raw_response: str
    model: str
    criteria_version: int


@dataclass
class AggregatedJudgeResult:
    individual_results: list[JudgeResult]
    mean_scores: dict[str, float]
    mean_composite: float
    variance: float
    agreement_ratio: float  # fraction of runs that agree on direction (> 0.5)
    is_stable: bool


class LLMJudge:
    """Controlled LLM-based evaluation with context separation."""

    VARIANCE_THRESHOLD = 0.05

    def __init__(self, config: Config) -> None:
        self.model = config.llm_judge_model
        self.default_runs = config.llm_judge_runs
        self.timeout = config.agent_timeout_seconds
        self.agent_command = config.agent_command

    def pairwise_compare(
        self,
        current_snapshot: str,
        candidate_diff: str,
        rubric: list[JudgeRubricItem],
        criteria_version: int,
    ) -> JudgeResult:
        """Single pairwise comparison. Judge sees only state + diff + rubric."""
        prompt = self.build_judge_prompt(current_snapshot, candidate_diff, rubric)
        raw = self._call_llm(prompt)
        try:
            scores = self._parse_judge_response(raw, rubric)
        except JudgeParseError:
            # Retry once with more explicit instructions
            retry_prompt = prompt + "\n\nIMPORTANT: You MUST respond with valid JSON only. No markdown, no explanation outside the JSON."
            raw = self._call_llm(retry_prompt)
            scores = self._parse_judge_response(raw, rubric)

        composite = self._compute_composite(scores, rubric)
        return JudgeResult(
            scores=scores,
            composite_score=composite,
            raw_response=raw,
            model=self.model,
            criteria_version=criteria_version,
        )

    def repeated_judge(
        self,
        current_snapshot: str,
        candidate_diff: str,
        rubric: list[JudgeRubricItem],
        criteria_version: int,
        num_runs: int | None = None,
    ) -> AggregatedJudgeResult:
        """Run judge N times and aggregate results."""
        n = num_runs or self.default_runs
        results: list[JudgeResult] = []

        for _ in range(n):
            try:
                r = self.pairwise_compare(current_snapshot, candidate_diff, rubric, criteria_version)
                results.append(r)
            except (JudgeParseError, JudgeLLMError):
                continue  # skip failed runs

        if not results:
            # All runs failed — return low-confidence empty result
            return AggregatedJudgeResult(
                individual_results=[],
                mean_scores={},
                mean_composite=0.0,
                variance=1.0,
                agreement_ratio=0.0,
                is_stable=False,
            )

        # Aggregate per-rubric scores
        score_lists: dict[str, list[float]] = {}
        for r in results:
            for s in r.scores:
                score_lists.setdefault(s.rubric_item, []).append(s.score)

        mean_scores = {k: mean(v) for k, v in score_lists.items()}
        composites = [r.composite_score for r in results]
        mean_comp = mean(composites)
        var = stat_variance(composites) if len(composites) > 1 else 0.0
        agree_count = sum(1 for c in composites if c > 0.5)
        agreement = agree_count / len(composites)

        return AggregatedJudgeResult(
            individual_results=results,
            mean_scores=mean_scores,
            mean_composite=mean_comp,
            variance=var,
            agreement_ratio=agreement,
            is_stable=var < self.VARIANCE_THRESHOLD,
        )

    def build_judge_prompt(
        self,
        current_snapshot: str,
        candidate_diff: str,
        rubric: list[JudgeRubricItem],
    ) -> str:
        rubric_lines = "\n".join(
            f"- {r.name}: {r.description} (weight: {r.weight:.2f})"
            for r in rubric
        )
        # Truncate snapshot if too large (keep diff intact)
        max_snapshot = 8000
        if len(current_snapshot) > max_snapshot:
            current_snapshot = current_snapshot[:max_snapshot] + "\n... (truncated)"

        return f"""You are an expert evaluator. Compare the CURRENT state with the PROPOSED CHANGES and score each criterion.

## Current State
{current_snapshot}

## Proposed Changes (diff)
{candidate_diff}

## Evaluation Criteria
For each criterion below, provide:
- score: a number from 0.0 (much worse) to 1.0 (much better), where 0.5 = no change
- reasoning: one sentence explaining your score

Criteria:
{rubric_lines}

Respond ONLY with JSON (no markdown fences):
{{
  "scores": [
    {{"rubric_item": "criterion_name", "score": 0.0, "reasoning": "explanation"}},
    ...
  ]
}}"""

    # ------------------------------------------------------------------
    # LLM invocation
    # ------------------------------------------------------------------

    def _call_llm(self, prompt: str) -> str:
        """Call LLM via subprocess to configured agent CLI."""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".md", delete=False) as tmp:
            tmp.write(prompt)
            prompt_file = tmp.name

        try:
            cmd_base = self.agent_command.split()[0].lower()
            if "claude" in cmd_base:
                cmd = f'claude --print -p "$(cat {prompt_file})"'
                result = subprocess.run(
                    cmd, shell=True, capture_output=True, text=True, timeout=self.timeout,
                )
            else:
                result = subprocess.run(
                    [self.agent_command, prompt_file],
                    capture_output=True, text=True, timeout=self.timeout,
                )
        except subprocess.TimeoutExpired as e:
            raise JudgeLLMError(self.model, f"Timed out after {self.timeout}s") from e
        except FileNotFoundError as e:
            raise JudgeLLMError(self.model, f"Command not found: {self.agent_command}") from e
        finally:
            os.unlink(prompt_file)

        if result.returncode != 0:
            raise JudgeLLMError(self.model, result.stderr or f"Exit code {result.returncode}")

        return result.stdout

    # ------------------------------------------------------------------
    # Response parsing
    # ------------------------------------------------------------------

    def _parse_judge_response(
        self, raw: str, rubric: list[JudgeRubricItem]
    ) -> list[JudgeScore]:
        """Extract structured scores from LLM response."""
        # Try to find JSON in the response (handle markdown fences)
        json_str = raw.strip()
        # Strip markdown code fences
        match = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", json_str, re.DOTALL)
        if match:
            json_str = match.group(1)
        else:
            # Try to find raw JSON object
            match = re.search(r"\{.*\}", json_str, re.DOTALL)
            if match:
                json_str = match.group(0)

        try:
            data = json.loads(json_str)
        except json.JSONDecodeError as e:
            raise JudgeParseError(raw, str(e))

        raw_scores = data.get("scores", [])
        if not isinstance(raw_scores, list):
            raise JudgeParseError(raw, "'scores' is not a list")

        scores: list[JudgeScore] = []
        rubric_names = {r.name for r in rubric}
        for item in raw_scores:
            name = item.get("rubric_item", "")
            score_val = item.get("score", 0.5)
            reasoning = item.get("reasoning", "")
            # Clamp score
            score_val = max(0.0, min(1.0, float(score_val)))
            if name in rubric_names:
                scores.append(JudgeScore(rubric_item=name, score=score_val, reasoning=reasoning))

        # Fill missing rubric items with 0.5 (no change)
        scored_names = {s.rubric_item for s in scores}
        for r in rubric:
            if r.name not in scored_names:
                scores.append(JudgeScore(rubric_item=r.name, score=0.5, reasoning="Not evaluated"))

        return scores

    def _compute_composite(
        self, scores: list[JudgeScore], rubric: list[JudgeRubricItem]
    ) -> float:
        weight_map = {r.name: r.weight for r in rubric}
        total_weight = sum(weight_map.values())
        if total_weight == 0:
            return 0.5
        weighted_sum = sum(
            s.score * weight_map.get(s.rubric_item, 0.0) for s in scores
        )
        return weighted_sum / total_weight
