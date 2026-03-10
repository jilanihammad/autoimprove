# Phase 2: Plugin System & Evaluation — Beads 8–10

---

## Bead 8: LLM Judge

**Status:** ⬜ Not started
**Dependencies:** Bead 1 (config.py, types.py)
**Estimated effort:** Large

### Purpose
Implement the LLM-as-judge evaluation layer. This is the primary evaluation method for subjective artifact types (documents, presentations) and a supplementary signal for deterministic types (code, workflows). The design prioritizes controlled, auditable judgment over naive "ask the LLM if it's better."

### Files to Edit

```
src/eval/llm_judge.py    # Full implementation (replace stub)
```

### Detailed Specifications

#### `src/eval/llm_judge.py`

**Core Design Principles (must be preserved in implementation):**
1. Context separation: judge NEVER sees the improvement hypothesis or agent reasoning
2. Pairwise comparison: always candidate vs. current accepted state, not vs. abstract ideal
3. Structured output: per-rubric-item scores + reasoning, not a single number
4. Repeated judging: configurable N runs with aggregation for low-confidence artifacts
5. Rubric versioning: every call tagged with criteria version

**Dataclasses:**

```python
@dataclass
class JudgeRubricItem:
    name: str               # e.g., "readability", "error_handling", "clarity"
    description: str        # What this item measures
    weight: float           # 0.0-1.0, weights sum to 1.0 across all items

@dataclass
class JudgeScore:
    rubric_item: str        # Name of the rubric item
    score: float            # 0.0-1.0
    reasoning: str          # Brief explanation from the judge

@dataclass
class JudgeResult:
    scores: list[JudgeScore]          # Per-rubric-item scores
    composite_score: float            # Weighted average
    raw_response: str                 # Full LLM response (for audit)
    model: str                        # Which model was used
    criteria_version: int             # Which criteria version was used

@dataclass
class AggregatedJudgeResult:
    individual_results: list[JudgeResult]   # All N judge runs
    mean_scores: dict[str, float]           # rubric_item -> mean score
    mean_composite: float                   # Mean of composite scores
    variance: float                         # Variance of composite scores
    agreement_ratio: float                  # % of runs that agree on direction (improved vs not)
    is_stable: bool                         # True if variance < threshold
```

**Class: `LLMJudge`**

```python
class LLMJudge:
    def __init__(self, config: Config):
        self.model = config.llm_judge_model
        self.default_runs = config.llm_judge_runs
        self.timeout = config.agent_timeout_seconds
```

**Methods:**

1. **`pairwise_compare(current_snapshot: str, candidate_diff: str, rubric: list[JudgeRubricItem], criteria_version: int) -> JudgeResult`**
   - Constructs a judge prompt with:
     - The current state (relevant file contents or summary)
     - The candidate diff (what changed)
     - The rubric items with descriptions
     - Instructions to score each rubric item 0.0-1.0 with brief reasoning
   - CRITICAL: prompt does NOT include why the change was made or what the agent intended
   - Calls the LLM API
   - Parses structured response into `JudgeResult`
   - Handles parse failures gracefully (retry once, then return low-confidence result)

2. **`repeated_judge(current_snapshot: str, candidate_diff: str, rubric: list[JudgeRubricItem], criteria_version: int, num_runs: int | None = None) -> AggregatedJudgeResult`**
   - Runs `pairwise_compare` N times (default: `self.default_runs`)
   - Aggregates results:
     - Mean score per rubric item
     - Mean composite score
     - Variance of composite scores
     - Agreement ratio: what % of runs scored composite > 0.5 (i.e., "improved")
   - `is_stable = variance < 0.05` (configurable threshold)

3. **`build_judge_prompt(current_snapshot: str, candidate_diff: str, rubric: list[JudgeRubricItem]) -> str`**
   - Constructs the full prompt string
   - Template:
     ```
     You are an expert evaluator. Compare the CURRENT state with the PROPOSED CHANGES
     and score each criterion.

     ## Current State
     {current_snapshot}

     ## Proposed Changes (diff)
     {candidate_diff}

     ## Evaluation Criteria
     For each criterion below, provide:
     - score: a number from 0.0 (much worse) to 1.0 (much better), where 0.5 = no change
     - reasoning: one sentence explaining your score

     Criteria:
     {for each rubric item: "- {name}: {description} (weight: {weight})"}

     Respond in JSON format:
     {
       "scores": [
         {"rubric_item": "name", "score": 0.0-1.0, "reasoning": "..."},
         ...
       ]
     }
     ```

4. **`_call_llm(prompt: str) -> str`**
   - Abstracted LLM API call
   - For v1: use subprocess to call the agent CLI in a non-interactive "single prompt" mode
   - Alternative: direct API call via `anthropic` or `openai` Python SDK
   - Implementation note: start with subprocess approach for agent-agnosticism
   - Must handle: timeouts, rate limits, malformed responses
   - Returns raw response string

5. **`_parse_judge_response(raw: str) -> list[JudgeScore]`**
   - Extracts JSON from LLM response (handle markdown code blocks)
   - Validates structure: each score has rubric_item, score (0-1), reasoning
   - Returns parsed scores
   - On parse failure: raises `JudgeParseError` with raw response attached

**Exception: `JudgeParseError(Exception)`**
- `raw_response: str`
- `parse_error: str`

**Exception: `JudgeLLMError(Exception)`**
- `model: str`
- `error: str`

### LLM API Strategy (v1)

For v1, the LLM judge uses a **subprocess call** to the configured agent CLI:
- Construct a temp file with the judge prompt
- Run: `<agent_command> --print "$(cat /tmp/judge_prompt.txt)"` or equivalent
- Parse stdout as the response

This keeps the system agent-agnostic. Future versions can add direct SDK support.

Alternative approach if subprocess is unreliable:
- Use the `anthropic` Python SDK directly for Claude models
- Use the `openai` Python SDK for OpenAI models
- Detect from `config.llm_judge_model` which SDK to use

**Recommendation:** Implement both, with subprocess as default and SDK as opt-in via config flag `llm_judge_method: "subprocess" | "sdk"`.

### Acceptance Criteria
- [ ] `pairwise_compare` produces structured `JudgeResult` with per-rubric scores
- [ ] `repeated_judge` runs N times and correctly aggregates
- [ ] Variance and agreement ratio calculated correctly
- [ ] Judge prompt does NOT contain improvement hypothesis or agent reasoning
- [ ] JSON parsing handles markdown code blocks and edge cases
- [ ] Parse failures are caught and produce low-confidence results (not crashes)
- [ ] LLM call has timeout handling
- [ ] All results tagged with criteria version and model used
- [ ] Raw LLM responses stored for audit

### Notes
- The judge is the most expensive component per-call — repeated judging multiplies cost
- For code artifacts (high confidence), default to 1 judge run; for documents (low confidence), default to 3
- The prompt template is critical — small changes can significantly affect judge behavior. Version it.
- Temperature should be > 0 for repeated judging (to get variance signal). Recommend 0.3-0.5.
- Context window management: if current_snapshot + diff exceeds model context, truncate the snapshot (keep diff intact)

---

## Bead 9: Acceptance Engine

**Status:** ⬜ Not started
**Dependencies:** Bead 5 (base.py), Bead 6 (code_plugin.py), Bead 7 (policy.py), Bead 8 (llm_judge.py)
**Estimated effort:** Large

### Purpose
Implement the core decision-making engine that determines whether a candidate change is accepted or rejected. This is the brain of the system — it orchestrates policy checks, hard gates, soft evaluation, LLM judging, and confidence calculation into a single accept/reject decision with full evidence.

### Files to Edit

```
src/eval/engine.py    # Full implementation (replace stub)
```

### Detailed Specifications

#### `src/eval/engine.py`

**Dataclasses:**

```python
@dataclass
class AcceptanceDecision:
    decision: Decision                    # ACCEPT or REJECT
    reason: str                           # Primary reason code
    detail: dict                          # Structured detail for logging
    composite_score: float | None         # Final composite score (None if rejected before scoring)
    confidence: float | None              # Confidence in the decision (None if rejected before confidence)
    evidence: AcceptanceEvidence          # Full evidence bundle

@dataclass
class AcceptanceEvidence:
    policy_result: PolicyResult | None
    gate_result: GateResult | None
    soft_eval_result: SoftEvalResult | None
    judge_result: AggregatedJudgeResult | None
    confidence_breakdown: dict[str, float]   # Factor -> adjustment
    criteria_version: int
    duration_seconds: float
    current_state_score: float | None        # Score of the current accepted state
```

**Reason codes (string constants):**
```python
REASON_POLICY_VIOLATION = "policy_violation"
REASON_HARD_GATE_FAILURE = "hard_gate_failure"
REASON_NO_IMPROVEMENT = "no_improvement"
REASON_LOW_CONFIDENCE = "low_confidence"
REASON_ACCEPTED = "accepted"
```

**Class: `AcceptanceEngine`**

```python
class AcceptanceEngine:
    def __init__(self, config: Config, plugin: EvaluatorPlugin,
                 llm_judge: LLMJudge):
        self.config = config
        self.plugin = plugin
        self.llm_judge = llm_judge
```

**Methods:**

1. **`evaluate(diff: Diff, targets: list[str], current_state_score: float, criteria: dict, criteria_version: int) -> AcceptanceDecision`**

   This is the main entry point. Implements the full decision pipeline:

   ```
   Step 0: Policy check (policy.py)
     → REJECT if any fatal violation

   Step 1: Hard gates (plugin.hard_gates)
     → REJECT if any gate fails

   Step 2: Soft evaluation
     2a: Plugin deterministic metrics (plugin.soft_evaluate)
     2b: LLM judge (pairwise comparison)
         - Determine judge runs based on plugin confidence profile
         - HIGH confidence → 1 run
         - MEDIUM confidence → 2 runs
         - LOW confidence → config.llm_judge_runs (default 3)
     2c: Compute composite score (weighted sum of metrics + judge)

   Step 3: Score comparison
     → REJECT if composite <= current_state_score

   Step 4: Confidence calculation
     → REJECT if confidence < threshold for this plugin type

   Step 5: ACCEPT
   ```

2. **`_calculate_confidence(plugin: EvaluatorPlugin, soft_result: SoftEvalResult, judge_result: AggregatedJudgeResult | None, diff: Diff) -> tuple[float, dict]`**

   Returns `(confidence_score, breakdown_dict)`.

   Base confidence from plugin profile:
   - HIGH → 0.8
   - MEDIUM → 0.5
   - LOW → 0.3

   Adjustments:
   - `deterministic_judge_agreement`: +0.1 if deterministic metrics and judge agree on direction
   - `judge_variance_penalty`: -0.2 if `judge_result.variance > 0.05`
   - `small_diff_penalty`: -0.05 if `diff.lines_added + diff.lines_removed < 5`
   - `no_deterministic_penalty`: -0.15 if `not soft_result.has_deterministic`
   - `high_judge_agreement`: +0.05 if `judge_result.agreement_ratio > 0.9`

   Clamp to [0.0, 1.0].

   Breakdown dict records each adjustment for logging.

3. **`_compute_composite_score(soft_result: SoftEvalResult, judge_result: AggregatedJudgeResult | None, criteria: dict) -> float`**

   - If both deterministic and judge scores available:
     - Use criteria weights (default: 60% deterministic, 40% judge for code; 20%/80% for documents)
   - If only deterministic: use deterministic only
   - If only judge: use judge only (with confidence penalty applied elsewhere)
   - Returns weighted composite 0.0-1.0

4. **`_get_current_snapshot_for_judge(targets: list[str], worktree_path: str) -> str`**
   - Reads relevant file contents from worktree for the judge
   - Truncates if too large (keep first N lines per file)
   - Returns concatenated string with file headers

5. **`_build_rubric_from_criteria(criteria: dict) -> list[JudgeRubricItem]`**
   - Extracts rubric items from the criteria dict
   - Maps criteria fields to `JudgeRubricItem` objects

### Acceptance Criteria
- [ ] Full pipeline: policy → gates → soft eval → judge → confidence → decision
- [ ] Policy violations cause immediate REJECT before any evaluation
- [ ] Hard gate failures cause immediate REJECT before soft eval
- [ ] Composite score correctly combines deterministic + judge scores
- [ ] Confidence calculation applies all adjustments correctly
- [ ] Confidence threshold is plugin-type-specific (from config)
- [ ] `AcceptanceDecision` contains full evidence for logging
- [ ] Judge runs scale with confidence profile (1 for HIGH, 3 for LOW)
- [ ] All reason codes used correctly

### Notes
- This is the most critical module — every accept/reject flows through here
- The evidence bundle must be complete enough to explain any decision in the final report
- Timing: record `duration_seconds` for the full evaluation (for cost reporting)
- The engine does NOT modify git state — it only returns a decision. The orchestrator acts on it.
- Edge case: if LLM judge fails (timeout, parse error), fall back to deterministic-only with confidence penalty

---

## Bead 10: Criteria Management

**Status:** ⬜ Not started
**Dependencies:** Bead 1 (config.py, types.py)
**Estimated effort:** Medium

### Purpose
Manage versioned evaluation criteria — the rubric that defines what "better" means for a given run. Criteria are set during the grounding phase, immutable within a run (v1 policy), and proposals for changes are captured for human review.

### Files to Edit

```
src/eval/criteria.py    # Full implementation (replace stub)
```

### Detailed Specifications

#### `src/eval/criteria.py`

**Dataclasses:**

```python
@dataclass
class CriteriaItem:
    name: str                   # e.g., "test_pass_rate", "readability", "error_handling"
    description: str            # What this criterion measures
    weight: float               # 0.0-1.0, weights should sum to 1.0
    is_hard_gate: bool          # If True, this is a pass/fail gate, not a scored metric
    metric_type: str            # "deterministic" or "judgment"

@dataclass
class CriteriaVersion:
    version: int                # 1, 2, 3, ...
    created_at: str             # ISO timestamp
    items: list[CriteriaItem]
    plugin_name: str            # Which plugin these criteria apply to
    notes: str                  # Human-readable description of this version

@dataclass
class CriteriaProposal:
    proposed_at: str            # ISO timestamp
    iteration: int              # Which iteration proposed this
    changes: list[dict]         # List of {action: "add"|"remove"|"modify", item: CriteriaItem, reason: str}
    rationale: str              # Why the agent thinks criteria should change
    status: str                 # "pending" (always, in v1)
```

**Class: `CriteriaManager`**

```python
class CriteriaManager:
    def __init__(self, criteria_dir: Path):
        self.criteria_dir = criteria_dir   # .autoimprove/runs/<run_id>/criteria/
        self._versions: dict[int, CriteriaVersion] = {}
        self._proposals: list[CriteriaProposal] = []
```

**Methods:**

1. **`create_initial(items: list[CriteriaItem], plugin_name: str, notes: str = "") -> CriteriaVersion`**
   - Creates version 1 of the criteria
   - Validates: weights sum to ~1.0 (within 0.01 tolerance)
   - Validates: at least one item
   - Saves to `criteria_dir/criteria_v1.json`
   - Returns the created version

2. **`get_current() -> CriteriaVersion`**
   - Returns the highest version number
   - In v1, this is always version 1 (criteria are immutable within a run)

3. **`get_version(version: int) -> CriteriaVersion`**
   - Returns a specific version
   - Raises `KeyError` if version doesn't exist

4. **`record_proposal(iteration: int, changes: list[dict], rationale: str) -> CriteriaProposal`**
   - Records a criteria change proposal from the agent
   - Saves to `criteria_dir/proposal_iter_{iteration}.json`
   - Does NOT apply the changes (v1 policy)
   - Returns the proposal

5. **`get_proposals() -> list[CriteriaProposal]`**
   - Returns all proposals, sorted by iteration

6. **`get_weights_dict() -> dict[str, float]`**
   - Returns `{item.name: item.weight}` for the current version
   - Convenience method for the acceptance engine

7. **`get_hard_gates() -> list[str]`**
   - Returns names of items where `is_hard_gate=True`
   - Convenience method for the acceptance engine

8. **`to_rubric_items() -> list[JudgeRubricItem]`**
   - Converts current criteria to `JudgeRubricItem` list for the LLM judge
   - Only includes items where `metric_type == "judgment"` or all items (configurable)

9. **`save(version: CriteriaVersion) -> None`**
   - Serializes to JSON and writes to criteria_dir

10. **`load_all(cls, criteria_dir: Path) -> CriteriaManager`** (classmethod)
    - Loads all criteria versions and proposals from disk
    - Used for crash recovery and report generation

**Default criteria templates (class methods):**

11. **`default_code_criteria() -> list[CriteriaItem]`**
    ```python
    [
        CriteriaItem("test_pass_rate", "All tests must pass", 0.0, is_hard_gate=True, metric_type="deterministic"),
        CriteriaItem("build_success", "Project must build", 0.0, is_hard_gate=True, metric_type="deterministic"),
        CriteriaItem("lint_score", "Reduction in lint errors/warnings", 0.2, is_hard_gate=False, metric_type="deterministic"),
        CriteriaItem("complexity", "Reduction in cyclomatic complexity", 0.2, is_hard_gate=False, metric_type="deterministic"),
        CriteriaItem("readability", "Code is easier to understand", 0.25, is_hard_gate=False, metric_type="judgment"),
        CriteriaItem("maintainability", "Code is easier to maintain/extend", 0.2, is_hard_gate=False, metric_type="judgment"),
        CriteriaItem("error_handling", "Proper error handling and edge cases", 0.15, is_hard_gate=False, metric_type="judgment"),
    ]
    ```

12. **`default_document_criteria() -> list[CriteriaItem]`**
    ```python
    [
        CriteriaItem("parseable", "Document is valid/parseable", 0.0, is_hard_gate=True, metric_type="deterministic"),
        CriteriaItem("clarity", "Writing is clear and unambiguous", 0.3, is_hard_gate=False, metric_type="judgment"),
        CriteriaItem("completeness", "All necessary information is present", 0.25, is_hard_gate=False, metric_type="judgment"),
        CriteriaItem("structure", "Well-organized with logical flow", 0.2, is_hard_gate=False, metric_type="judgment"),
        CriteriaItem("audience_fit", "Appropriate for target audience", 0.15, is_hard_gate=False, metric_type="judgment"),
        CriteriaItem("actionability", "Reader knows what to do next", 0.1, is_hard_gate=False, metric_type="judgment"),
    ]
    ```

13. **`default_workflow_criteria() -> list[CriteriaItem]`**
    ```python
    [
        CriteriaItem("schema_valid", "Workflow schema is valid", 0.0, is_hard_gate=True, metric_type="deterministic"),
        CriteriaItem("error_handling", "Proper error handling on all nodes", 0.25, is_hard_gate=False, metric_type="judgment"),
        CriteriaItem("efficiency", "Minimal unnecessary nodes/steps", 0.2, is_hard_gate=False, metric_type="judgment"),
        CriteriaItem("reliability", "Handles edge cases and failures", 0.25, is_hard_gate=False, metric_type="judgment"),
        CriteriaItem("readability", "Workflow is easy to understand", 0.15, is_hard_gate=False, metric_type="judgment"),
        CriteriaItem("security", "No exposed secrets or unsafe operations", 0.15, is_hard_gate=False, metric_type="judgment"),
    ]
    ```

### Acceptance Criteria
- [ ] `CriteriaVersion` can be created, saved, and loaded from JSON
- [ ] Weight validation catches weights that don't sum to ~1.0
- [ ] `record_proposal` saves proposals without applying them
- [ ] `get_current()` always returns version 1 in v1
- [ ] Default criteria templates provided for code, document, workflow
- [ ] `to_rubric_items()` correctly converts to judge format
- [ ] All files saved as pretty-printed JSON for human readability
- [ ] `load_all()` reconstructs full state from disk

### Notes
- Criteria immutability within a run is a deliberate v1 safety decision
- The agent sees criteria during the grounding phase and can negotiate changes THEN
- During autonomous mode, the agent can only PROPOSE changes, not apply them
- Hard gate items have weight 0.0 because they're pass/fail, not scored
- The weights on scored items should sum to 1.0 (excluding hard gates)
- Proposals are valuable data — they show how the agent's understanding evolves
- Future v2 could allow auto-applying "safe" proposals (e.g., adding a new criterion, never removing one)
