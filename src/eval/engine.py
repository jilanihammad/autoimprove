"""Acceptance decision engine — policy → gates → score → confidence → decide.

Every accept/reject decision flows through this engine.  It orchestrates
policy checks, hard gates, soft evaluation, LLM judging, and confidence
calculation into a single decision with full evidence.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field

from src.config import Config
from src.eval.llm_judge import AggregatedJudgeResult, JudgeRubricItem, LLMJudge
from src.plugins.base import EvaluatorPlugin
from src.policy import PolicyResult, check_policy
from src.types import ConfidenceProfile, Decision, Diff, GateResult, SoftEvalResult

# Reason codes
REASON_POLICY_VIOLATION = "policy_violation"
REASON_HARD_GATE_FAILURE = "hard_gate_failure"
REASON_NO_IMPROVEMENT = "no_improvement"
REASON_LOW_CONFIDENCE = "low_confidence"
REASON_ACCEPTED = "accepted"

_BASE_CONFIDENCE = {
    ConfidenceProfile.HIGH: 0.8,
    ConfidenceProfile.MEDIUM: 0.5,
    ConfidenceProfile.LOW: 0.3,
}


@dataclass
class AcceptanceEvidence:
    policy_result: PolicyResult | None = None
    gate_result: GateResult | None = None
    soft_eval_result: SoftEvalResult | None = None
    judge_result: AggregatedJudgeResult | None = None
    confidence_breakdown: dict[str, float] = field(default_factory=dict)
    criteria_version: int = 1
    duration_seconds: float = 0.0
    current_state_score: float | None = None


@dataclass
class AcceptanceDecision:
    decision: Decision
    reason: str
    detail: dict = field(default_factory=dict)
    composite_score: float | None = None
    confidence: float | None = None
    evidence: AcceptanceEvidence = field(default_factory=AcceptanceEvidence)


class AcceptanceEngine:
    """Orchestrates the full accept/reject decision pipeline."""

    def __init__(
        self, config: Config, plugin: EvaluatorPlugin, llm_judge: LLMJudge
    ) -> None:
        self.config = config
        self.plugin = plugin
        self.llm_judge = llm_judge

    def evaluate(
        self,
        diff: Diff,
        targets: list[str],
        current_state_score: float,
        criteria: dict,
        criteria_version: int,
        working_dir: str = "",
    ) -> AcceptanceDecision:
        """Run the full decision pipeline."""
        start = time.monotonic()
        evidence = AcceptanceEvidence(
            criteria_version=criteria_version,
            current_state_score=current_state_score,
        )

        def _reject(reason: str, detail: dict | None = None) -> AcceptanceDecision:
            evidence.duration_seconds = time.monotonic() - start
            return AcceptanceDecision(
                decision=Decision.REJECT,
                reason=reason,
                detail=detail or {},
                evidence=evidence,
            )

        # ── Step 0: Policy ──
        policy_result = check_policy(diff, self.config, self.plugin.guardrails())
        evidence.policy_result = policy_result
        if not policy_result.passed:
            return _reject(REASON_POLICY_VIOLATION, {
                "violations": [v.message for v in policy_result.violations if v.severity == "fatal"]
            })

        # ── Step 1: Hard gates ──
        gate_result = self.plugin.hard_gates(diff, targets, working_dir)
        evidence.gate_result = gate_result
        if not gate_result.all_passed:
            return _reject(REASON_HARD_GATE_FAILURE, {
                "failures": gate_result.failures,
                "gates": gate_result.gates,
            })

        # ── Step 2: Soft evaluation ──
        soft_result = self.plugin.soft_evaluate(diff, targets, criteria, working_dir)
        evidence.soft_eval_result = soft_result

        # ── Step 2b: LLM judge ──
        judge_result: AggregatedJudgeResult | None = None
        rubric = self._build_rubric_from_criteria(criteria)
        if rubric:
            num_runs = self._judge_runs_for_profile()
            snapshot = self._get_current_snapshot(targets, working_dir)
            try:
                judge_result = self.llm_judge.repeated_judge(
                    current_snapshot=snapshot,
                    candidate_diff=diff.raw_diff[:6000],
                    rubric=rubric,
                    criteria_version=criteria_version,
                    num_runs=num_runs,
                )
            except Exception:
                # Judge failed — continue with deterministic only, confidence penalty applied later
                judge_result = None
        evidence.judge_result = judge_result

        # ── Step 2c: Composite score ──
        composite = self._compute_composite(soft_result, judge_result, criteria)

        if composite <= current_state_score:
            return _reject(REASON_NO_IMPROVEMENT, {
                "current": current_state_score,
                "candidate": composite,
            })

        # ── Step 3: Confidence ──
        confidence, breakdown = self._calculate_confidence(
            soft_result, judge_result, diff
        )
        evidence.confidence_breakdown = breakdown

        threshold = self.config.confidence_thresholds.get(
            self.plugin.name, 0.5
        )
        if confidence < threshold:
            return _reject(REASON_LOW_CONFIDENCE, {
                "confidence": confidence,
                "threshold": threshold,
            })

        # ── Step 4: Accept ──
        evidence.duration_seconds = time.monotonic() - start
        return AcceptanceDecision(
            decision=Decision.ACCEPT,
            reason=REASON_ACCEPTED,
            detail={"composite": composite, "confidence": confidence},
            composite_score=composite,
            confidence=confidence,
            evidence=evidence,
        )

    # ------------------------------------------------------------------
    # Confidence
    # ------------------------------------------------------------------

    def _calculate_confidence(
        self,
        soft_result: SoftEvalResult,
        judge_result: AggregatedJudgeResult | None,
        diff: Diff,
    ) -> tuple[float, dict[str, float]]:
        base = _BASE_CONFIDENCE.get(self.plugin.confidence_profile, 0.5)
        breakdown: dict[str, float] = {"base": base}
        conf = base

        if judge_result and soft_result.has_deterministic:
            # Check agreement: both say improved (> 0.5)?
            det_improved = soft_result.composite > 0.5
            judge_improved = judge_result.mean_composite > 0.5
            if det_improved == judge_improved:
                conf += 0.1
                breakdown["deterministic_judge_agreement"] = 0.1

        if judge_result and judge_result.variance > 0.05:
            conf -= 0.2
            breakdown["judge_variance_penalty"] = -0.2

        if diff.lines_added + diff.lines_removed < 5:
            conf -= 0.05
            breakdown["small_diff_penalty"] = -0.05

        if not soft_result.has_deterministic:
            conf -= 0.15
            breakdown["no_deterministic_penalty"] = -0.15

        if judge_result and judge_result.agreement_ratio > 0.9:
            conf += 0.05
            breakdown["high_judge_agreement"] = 0.05

        conf = max(0.0, min(1.0, conf))
        return conf, breakdown

    # ------------------------------------------------------------------
    # Composite scoring
    # ------------------------------------------------------------------

    def _compute_composite(
        self,
        soft_result: SoftEvalResult,
        judge_result: AggregatedJudgeResult | None,
        criteria: dict,
    ) -> float:
        det_score = soft_result.composite
        judge_score = judge_result.mean_composite if judge_result else None

        if det_score is not None and judge_score is not None:
            # Weight split depends on confidence profile
            if self.plugin.confidence_profile == ConfidenceProfile.HIGH:
                det_w, judge_w = 0.6, 0.4
            elif self.plugin.confidence_profile == ConfidenceProfile.LOW:
                det_w, judge_w = 0.2, 0.8
            else:
                det_w, judge_w = 0.4, 0.6
            return det_score * det_w + judge_score * judge_w

        if judge_score is not None:
            return judge_score
        return det_score if det_score is not None else 0.0

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _judge_runs_for_profile(self) -> int:
        if self.plugin.confidence_profile == ConfidenceProfile.HIGH:
            return 1
        if self.plugin.confidence_profile == ConfidenceProfile.MEDIUM:
            return 2
        return self.config.llm_judge_runs

    def _build_rubric_from_criteria(self, criteria: dict) -> list[JudgeRubricItem]:
        items = criteria.get("items", [])
        rubric: list[JudgeRubricItem] = []
        for item in items:
            if isinstance(item, dict) and not item.get("is_hard_gate", False):
                rubric.append(JudgeRubricItem(
                    name=item.get("name", ""),
                    description=item.get("description", ""),
                    weight=item.get("weight", 0.0),
                ))
        return rubric

    def _get_current_snapshot(self, targets: list[str], working_dir: str) -> str:
        """Read relevant file contents for the judge."""
        lines: list[str] = []
        max_per_file = 200
        total_chars = 0
        max_total = 8000

        for t in targets[:10]:  # Limit files
            try:
                with open(t, errors="ignore") as f:
                    content = f.readlines()[:max_per_file]
                file_text = "".join(content)
                if total_chars + len(file_text) > max_total:
                    break
                lines.append(f"--- {t} ---\n{file_text}")
                total_chars += len(file_text)
            except OSError:
                continue

        return "\n".join(lines) if lines else "(no files readable)"
