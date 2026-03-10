# AutoImprove v2 — Revised Architecture & Implementation Plan

## A. Critique of Current Design

The v1 draft captures the right vision but has real gaps that would bite you in production:

1. **Evaluation is hand-wavy.** "Composite score" hides the critical question: what does "better" mean, and how confident are we? No distinction between hard failures (tests broke) and soft improvements (code reads better). This is the single most important thing to get right.

2. **No workspace isolation.** Running directly on the user's branch is dangerous. One bad revert corrupts working state. No way to run multiple improvement sessions or safely abort mid-run.

3. **Bash orchestration won't scale.** The loop has real state: run history, criteria versions, candidate lifecycle, search memory. Bash can't manage this cleanly. Error handling in bash for a multi-step autonomous system is a nightmare.

4. **Profiles are passive documents, not executable contracts.** A markdown file can't tell the system how to actually run tests, validate a workflow, or score a document. You need executable evaluator plugins, not just description files.

5. **Criteria drift is uncontrolled.** Letting the agent rewrite its own eval criteria every 5 iterations is like letting a student rewrite the exam. Needs formal versioning and comparison isolation.

6. **"Agent identifies highest-impact improvement" is a prayer, not a mechanism.** No search memory, no hypothesis tracking, no anti-repetition. The agent will retry the same failing ideas.

7. **No preflight.** The system assumes everything works. No validation that tools exist, tests run, paths are valid, or the agent is reachable before burning time.

8. **Artifact types are treated uniformly.** Code has tests; a PowerPoint does not. The architecture pretends they're the same. It should explicitly model the confidence spectrum.

What's good and preserved:
- The core loop concept (modify → evaluate → keep/discard)
- `program.md` as human-edited intent
- Git-based checkpointing
- Agent-agnostic design
- Human-in-the-loop grounding before autonomous mode
- Time-budgeted execution

---

## B. Revised Architecture

### File/Folder Structure

```
autoimprove/
├── autoimprove.sh              # Thin shell entrypoint (delegates to Python)
├── pyproject.toml              # Python project + dependencies
│
├── program.md                  # Human-edited: project context, goals, constraints
├── config.yaml                 # Runtime config: agent, budgets, policies
│
├── src/
│   ├── __init__.py
│   ├── cli.py                  # CLI entrypoint, argument parsing, user prompts
│   ├── orchestrator.py         # Core loop: init → ground → iterate → wrap-up
│   ├── run_context.py          # Run state: IDs, worktree, paths, budget tracking
│   ├── git_ops.py              # Git: worktree, commit, tag, revert, diff
│   ├── agent_bridge.py         # Agent-agnostic interface (invoke any CLI agent)
│   ├── preflight.py            # Pre-run validation & safety checks
│   ├── policy.py               # Guardrails: secrets, protected paths, diff limits
│   │
│   ├── eval/
│   │   ├── __init__.py
│   │   ├── engine.py           # Acceptance decision engine (gates → score → confidence)
│   │   ├── baseline.py         # Baseline snapshot capture
│   │   ├── criteria.py         # Criteria versioning & management
│   │   ├── llm_judge.py        # LLM-as-judge: pairwise comparison, aggregation
│   │   └── search_memory.py    # Hypothesis tracking, anti-repetition
│   │
│   ├── plugins/                # Artifact-type evaluator plugins
│   │   ├── __init__.py
│   │   ├── base.py             # Abstract plugin contract
│   │   ├── code_plugin.py      # Code: tests, lint, complexity, build
│   │   ├── workflow_plugin.py  # Workflows: validation, execution, schema
│   │   ├── document_plugin.py  # Docs/slides/spreadsheets: LLM-judge primary
│   │   └── registry.py         # Plugin discovery & registration
│   │
│   └── reporting/
│       ├── __init__.py
│       ├── experiment_log.py   # Per-iteration structured logging
│       ├── summary.py          # Final report generation
│       └── terminal.py         # Terminal output formatting
│
├── profiles/                   # Default eval strategy hints (human-readable)
│   ├── code.md
│   ├── workflow.md
│   └── document.md
│
└── README.md
```

### Why Python for Orchestration

- The loop manages structured state (run context, criteria versions, search memory, experiment log) — Python has native data structures, JSON/YAML handling, and error handling for this.
- Subprocess management for calling agents, running tests, invoking LLM APIs is cleaner in Python than bash.
- Plugin system requires dynamic dispatch — bash can't do this.
- The eval engine has real logic (gate checks, score aggregation, confidence calculation) that would be unmaintainable in bash.
- `autoimprove.sh` remains as a convenience wrapper: validates Python/uv are available, then calls `uv run python -m src.cli`.

---

## C. Revised Core Loop

```
PREFLIGHT
  1. Validate environment (tools, agent, paths, evaluator plugins)
  2. Secret scan target paths
  3. Verify git state is clean (or snapshot)
  4. Create isolated run:
     - Generate run ID (timestamp + short hash)
     - Create git worktree: .autoimprove/runs/<run_id>/worktree
     - All work happens in worktree, never on user's branch
  5. Initialize run context (budget, config, state)

BASELINE
  6. Run artifact-type plugin.baseline() on target paths
  7. Save baseline snapshot: .autoimprove/runs/<run_id>/baseline.json
  8. Git tag in worktree: autoimprove/<run_id>/baseline

GROUNDING (human-in-loop)
  9. Agent reads program.md + relevant profile(s)
  10. Agent analyzes artifacts, proposes:
      - Eval criteria (rubric items, weights, hard gates)
      - Improvement hypotheses (ranked by expected impact)
  11. User reviews, edits, approves criteria
  12. Criteria saved as version 1: criteria_v1.json
  13. User confirms → autonomous mode begins

AUTONOMOUS LOOP (repeats until stop condition)
  14. HYPOTHESIS SELECTION
      - Agent consults search memory (past attempts, outcomes)
      - Selects highest-impact untried hypothesis
      - Writes bounded change proposal (what, why, expected impact, files)

  15. CANDIDATE GENERATION
      - Agent makes changes in worktree
      - Changes captured as candidate diff

  16. GUARDRAIL VALIDATION (policy.py)
      - Diff size within limits?
      - No protected path violations?
      - No secrets introduced?
      - No forbidden file types?
      - No dependency changes (unless allowed)?
      - FAIL → reject candidate immediately, log reason, continue

  17. HARD-GATE EVALUATION (plugin-specific)
      - Code: tests pass? build succeeds? typecheck passes?
      - Workflow: schema valid? nodes valid? connections intact?
      - Document: parseable? structure intact?
      - FAIL on any hard gate → reject candidate, log, continue

  18. SOFT EVALUATION (plugin-specific + LLM judge)
      - Automated metrics: complexity delta, coverage delta, lint delta, etc.
      - LLM-as-judge: pairwise comparison (candidate vs current accepted state)
        - Separate context: judge sees diff + criteria, NOT the improvement prompt
        - For subjective artifacts: run judge N times, aggregate scores
      - Per-metric scores + composite weighted score

  19. CONFIDENCE CALCULATION
      - Based on evidence quality:
        - High: all metrics deterministic (code with full test suite)
        - Medium: mix of deterministic + LLM judgment
        - Low: primarily LLM judgment (documents, presentations)
      - Penalized by: judge variance, small diff, no deterministic signals
      - Confidence score attached to the accept/reject decision

  20. ACCEPT/REJECT DECISION (engine.py)
      - All hard gates passed? (required)
      - Composite score > current accepted state score? (required)
      - Confidence above threshold for artifact type? (required)
      - → ACCEPT: commit in worktree, tag autoimprove/<run_id>/iter_N, update accepted state
      - → REJECT: revert worktree to accepted state, log full evidence

  21. LOGGING
      - Log to experiment_log.json: hypothesis, diff, gate results, scores,
        confidence, decision, reasoning, criteria version, cost, duration

  22. SEARCH MEMORY UPDATE
      - Record: hypothesis → outcome mapping
      - Track: files churned, ideas tried, failure patterns
      - Feed back into step 14 for next iteration

  23. CRITERIA REVIEW (every N iterations, configurable)
      - Agent proposes criteria changes as a FORMAL PROPOSAL
      - Proposal includes: what changed, why, expected impact
      - In v1: proposals are logged but NOT auto-applied
        (human reviews proposals in the final report)
      - Scores are always tagged with criteria version used

  24. STOP CONDITION CHECK
      - Time budget exhausted?
      - Cost budget exhausted?
      - Max iterations reached?
      - N consecutive rejections with no accept?
      - Repeated churn on same files (same files modified 3+ times with no net improvement)?
      - Confidence trending below threshold?
      - → STOP or CONTINUE

WRAP-UP
  25. Generate summary report:
      - Before/after metrics (baseline vs final accepted state)
      - Accepted changes: what, why, evidence, confidence
      - Rejected changes: what, why, evidence
      - Criteria evolution proposals (for human review)
      - Cost/time breakdown per accepted improvement
      - Git diff: baseline → final
  26. Tag final state: autoimprove/<run_id>/final
  27. User decides: merge worktree changes back to their branch, or discard
  28. Print results to terminal
```

---

## D. Revised Component Breakdown

### 1. Plugin Contract (`plugins/base.py`)

Every artifact type implements this interface:

```python
class EvaluatorPlugin(ABC):
    """Contract for artifact-type evaluator plugins."""

    name: str                          # e.g., "code", "workflow", "document"
    confidence_profile: str            # "high", "medium", "low" — default evidence quality

    def discover_targets(self, paths: list[str]) -> list[str]:
        """Find evaluable artifacts in target paths."""

    def preflight(self, targets: list[str]) -> PreflightResult:
        """Verify tools/deps needed for evaluation are available."""

    def baseline(self, targets: list[str]) -> BaselineSnapshot:
        """Capture current state metrics."""

    def hard_gates(self, diff: Diff, targets: list[str]) -> GateResult:
        """Run deterministic pass/fail checks. Returns pass/fail + reasons."""

    def soft_evaluate(self, diff: Diff, targets: list[str],
                      criteria: CriteriaVersion) -> SoftEvalResult:
        """Run scored evaluation. Returns per-metric scores."""

    def summarize_delta(self, baseline: BaselineSnapshot,
                        current: BaselineSnapshot) -> DeltaSummary:
        """Compare baseline to current for final report."""

    def guardrails(self) -> GuardrailConfig:
        """Return artifact-specific guardrail rules."""
```

### Confidence Profiles by Artifact Type

| Artifact Type | Hard Gates | Soft Metrics | Primary Signal | Default Confidence |
|---|---|---|---|---|
| Code | tests, build, typecheck, lint | complexity, coverage, duplication | Deterministic | High |
| Workflow (n8n) | schema validation, node config | execution success, error handling | Mixed | Medium |
| Workflow (custom) | deploy/build, integration tests | architecture, error handling | Mixed | Medium |
| Document | parseable, structure intact | clarity, completeness, tone | LLM judgment | Low |
| Spreadsheet | formulas valid, no circular refs | model clarity, naming, structure | Mixed | Medium-Low |
| Presentation | parseable, slide count stable | clarity, visual structure, flow | LLM judgment | Low |

The system is honest: for documents and presentations, confidence is inherently lower. The acceptance threshold adjusts accordingly — the system requires more judge agreement (repeated judging, higher consensus) before accepting changes to low-confidence artifact types.

### 2. Acceptance Decision Engine (`eval/engine.py`)

```
DECISION = f(hard_gates, soft_score, confidence)

Step 1: HARD GATES (binary)
  - All gates must pass. Any failure → REJECT immediately.
  - Gates are plugin-specific and non-negotiable.

Step 2: SOFT SCORING (numeric)
  - Weighted composite of plugin metrics + LLM judge scores.
  - Weights defined in criteria version.
  - Score must exceed current accepted state score.

Step 3: CONFIDENCE (numeric, 0-1)
  - Base confidence = plugin.confidence_profile
  - Adjustments:
    + Deterministic metrics agree with LLM judge → boost
    - High judge variance across repeated runs → penalty
    - Small diff with large claimed improvement → penalty
    - No deterministic signals available → penalty
  - Must exceed artifact-type-specific threshold.

Step 4: DECISION
  - ACCEPT if: gates passed AND score improved AND confidence ≥ threshold
  - REJECT otherwise
  - Log: decision, all gate results, all scores, confidence, reasoning
```

### 3. LLM-as-Judge (`eval/llm_judge.py`)

Design principles:
- **Context separation**: The judge prompt contains ONLY the diff, the current accepted state, and the evaluation criteria. It does NOT see the improvement hypothesis or the agent's reasoning. This prevents the judge from rubber-stamping the agent's intent.
- **Pairwise comparison**: Judge compares candidate vs. current accepted state, not candidate vs. abstract ideal. This grounds judgment in concrete before/after.
- **Repeated judging**: For low-confidence artifact types (documents, presentations), run the judge 3-5 times and aggregate. If scores diverge significantly, confidence is penalized.
- **Rubric versioning**: Every judge call is tagged with the criteria version used. Scores from different criteria versions are never directly compared.
- **Structured output**: Judge returns per-rubric-item scores + brief reasoning, not a single number. This makes the evidence auditable.

### 4. Search Memory (`eval/search_memory.py`)

Tracks:
- Every hypothesis attempted (description, files targeted, outcome)
- Failure patterns (e.g., "modifying X always breaks test Y")
- File churn (files modified repeatedly without net improvement)
- Successful patterns (what kinds of changes tend to be accepted)

Used by the agent in hypothesis selection to avoid retrying failed approaches and to prioritize promising directions.

Stored as: `.autoimprove/runs/<run_id>/search_memory.json`

### 5. Criteria Management (`eval/criteria.py`)

- Criteria are versioned: `criteria_v1.json`, `criteria_v2.json`, etc.
- v1 is set during grounding phase (human-approved).
- Agent can propose changes, but in v1 of AutoImprove, proposals are logged only — not auto-applied.
- Every evaluation result is tagged with the criteria version used.
- The final report includes all criteria change proposals for human review.
- Future versions may allow auto-application with stricter controls (e.g., only additive changes, never removing a gate).

**v1 policy**: Criteria are immutable within a run. Proposals are captured for the next run.

### 6. Run Context & Isolation (`run_context.py` + `git_ops.py`)

```
.autoimprove/
├── runs/
│   └── <run_id>/
│       ├── worktree/           # Git worktree (isolated workspace)
│       ├── config.yaml         # Frozen config for this run
│       ├── criteria_v1.json    # Frozen criteria
│       ├── baseline.json       # Baseline snapshot
│       ├── accepted_state.json # Current best state metrics
│       ├── experiment_log.json # Full iteration log
│       ├── search_memory.json  # Hypothesis tracking
│       ├── proposals/          # Criteria change proposals
│       └── summary.md          # Final report
└── latest -> runs/<run_id>     # Symlink to most recent run
```

- Run ID format: `YYYYMMDD-HHMMSS-<short_hash>`
- Worktree is created from current branch HEAD
- All changes happen in worktree — user's branch is untouched
- On completion, user explicitly merges (or discards) via `autoimprove merge <run_id>` or `autoimprove discard <run_id>`

### 7. Preflight (`preflight.py`)

Runs before anything else. Checks:
- Git repo is clean (or user confirms snapshot)
- Agent command is resolvable and responds
- Python/uv available
- Plugin-specific tools available (e.g., pytest for code, node for n8n)
- Target paths exist and are non-empty
- No secrets detected in target paths (basic scan)
- Disk space sufficient for worktree
- Config is valid

Fails fast with clear error messages. No partial runs.

### 8. Policy Enforcement (`policy.py`)

Applied to every candidate diff before evaluation:
- **Protected paths**: files/dirs that must not be modified (configurable)
- **Diff size limits**: max lines changed per iteration (prevents runaway rewrites)
- **Secret scanning**: reject diffs that introduce patterns matching secrets
- **Dependency policy**: reject changes to package.json/requirements.txt/etc. unless explicitly allowed
- **Forbidden file types**: reject binary files, compiled artifacts, etc.
- **Command restrictions**: agent cannot execute arbitrary shell commands outside the defined tool set

---

## E. Config Schema (`config.yaml`)

```yaml
# Agent
agent_command: "claude"           # "claude", "codex", "kiro-cli chat", or custom
agent_timeout_seconds: 300        # Max time per agent invocation

# Budgets
time_budget_minutes: 120
cost_budget_usd: null             # Optional, if trackable
max_iterations: null              # Optional hard cap

# Stop conditions
max_consecutive_rejections: 5
max_file_churn: 3                 # Stop if same file modified 3x with no net gain
min_confidence_threshold: 0.3     # Stop if confidence trends below this

# Evaluation
eval_refinement_interval: 5       # Iterations between criteria review proposals
llm_judge_model: "claude-sonnet"  # Model for LLM-as-judge
llm_judge_runs: 3                 # Repeated judging for low-confidence artifacts
grounding_mode: interactive       # interactive | auto

# Targets
target_paths:
  - src/
  - workflows/
exclude_paths:
  - tests/
  - node_modules/
  - .autoimprove/

# Policy
protected_paths:
  - "*.lock"
  - "migrations/"
max_diff_lines: 500
allow_dependency_changes: false
secret_patterns:                  # Additional patterns beyond defaults
  - "AKIA[0-9A-Z]{16}"
```

---

## F. Revised Implementation Plan

### Phase 1: Foundation (Beads 1–4)

| Bead | Component | Description | Depends On |
|---|---|---|---|
| 1 | Project scaffolding | pyproject.toml, src/ structure, CLI entrypoint, config schema + loader | — |
| 2 | Git operations | Worktree create/destroy, commit, tag, revert, diff extraction, run ID generation | — |
| 3 | Run context | Run state management, directory structure, accepted state tracking | 1, 2 |
| 4 | Preflight | Environment validation, tool checks, git state verification | 1, 2, 3 |

### Phase 2: Plugin System & Evaluation (Beads 5–10)

| Bead | Component | Description | Depends On |
|---|---|---|---|
| 5 | Plugin contract | Abstract base class, registry, plugin discovery | 1 |
| 6 | Code plugin | discover, preflight, baseline, hard_gates, soft_evaluate for code | 5 |
| 7 | Policy enforcement | Protected paths, diff limits, secret scanning, guardrail validation | 1, 2 |
| 8 | LLM judge | Pairwise comparison, repeated judging, aggregation, context separation | 1 |
| 9 | Acceptance engine | Hard gates → soft scoring → confidence → decision | 5, 6, 7, 8 |
| 10 | Criteria management | Versioned criteria, proposal capture, score tagging | 1 |

### Phase 3: Core Loop (Beads 11–15)

| Bead | Component | Description | Depends On |
|---|---|---|---|
| 11 | Agent bridge | Agent-agnostic invocation, prompt construction, response parsing | 1 |
| 12 | Search memory | Hypothesis tracking, anti-repetition, outcome logging | 1 |
| 13 | Grounding phase | Interactive criteria negotiation, baseline capture, human approval | 4, 6, 9, 10, 11 |
| 14 | Autonomous loop | Full iteration cycle: hypothesis → candidate → guardrails → gates → eval → decide → log | All Phase 2, 11, 12, 13 |
| 15 | Stop conditions | Time/cost/iteration budget, churn detection, confidence trending, regression counting | 14 |

### Phase 4: Reporting & UX (Beads 16–19)

| Bead | Component | Description | Depends On |
|---|---|---|---|
| 16 | Experiment logging | Structured per-iteration log with full evidence | 14 |
| 17 | Summary report | Before/after delta, accepted/rejected with reasoning, cost breakdown | 14, 16 |
| 18 | Terminal output | Progress during run, final results display | 14, 17 |
| 19 | Merge/discard CLI | `autoimprove merge <run_id>`, `autoimprove discard <run_id>` | 2, 3 |

### Phase 5: Expansion & Polish (Beads 20–23)

| Bead | Component | Description | Depends On |
|---|---|---|---|
| 20 | Workflow plugin | Evaluator for n8n + custom workflows | 5, 9 |
| 21 | Document plugin | Evaluator for docs/slides/spreadsheets (LLM-judge primary) | 5, 8, 9 |
| 22 | README + program.md templates | Setup docs, usage guide, example program.md files | All |
| 23 | End-to-end test | Full run against a sample code project | All |

### Dependency Graph

```
Bead 1 ─┬─ Bead 3 ── Bead 4 ──────────────────── Bead 13 ─── Bead 14 ─┬─ Bead 16 ─── Bead 17 ─── Bead 18
Bead 2 ─┘                                              ↑               │   Bead 19
         ├─ Bead 5 ─── Bead 6 ──┐                      │               └─ Bead 15
         ├─ Bead 7 ─────────────┼─── Bead 9 ───────────┤
         ├─ Bead 8 ─────────────┘                       │
         ├─ Bead 10 ────────────────────────────────────┤
         ├─ Bead 11 ────────────────────────────────────┤
         └─ Bead 12 ────────────────────────────────────┘

Phase 5 (parallel after Phase 3):
Bead 5 + 9 → Bead 20 (workflow plugin)
Bead 5 + 8 + 9 → Bead 21 (document plugin)
All → Bead 22, 23
```

---

## G. Design Principles & Non-Goals

### Principles
1. **Evidence over opinion.** Every accept/reject decision is backed by logged evidence. No silent improvements.
2. **Isolation by default.** User's workspace is never modified during a run. Merge is an explicit human action.
3. **Honest confidence.** The system knows when it's guessing. Low-confidence decisions are flagged, not hidden.
4. **Criteria are sacred.** The optimizer cannot silently change its own objective function. Criteria changes are proposals, not actions.
5. **Agent-agnostic.** The system works with any CLI agent. No coupling to a specific provider.
6. **Fail fast, fail loud.** Preflight catches problems before burning time/money. Every failure has a clear message.
7. **Artifact-aware, not artifact-limited.** The plugin system supports any artifact type, but each plugin is honest about its evaluation reliability.

### Non-Goals (v1)
- Real-time collaboration / multi-user
- Distributed / multi-GPU training
- Auto-applying criteria changes within a run
- Visual UI / dashboard (terminal-only for v1)
- Multi-agent coordination (one agent per run)
- Automatic cost tracking (depends on agent provider APIs)

---

## H. Open Risks & Tradeoffs

| Risk | Mitigation | Residual |
|---|---|---|
| LLM judge rubber-stamps improvements | Context separation, pairwise comparison, repeated judging | Judge may still have systematic biases |
| Agent retries same failing approach | Search memory + anti-repetition | Memory is text-based; semantic similarity detection is imperfect |
| Worktree diverges from main branch during long runs | Runs are time-bounded; merge conflicts are user's responsibility | Long runs on active repos may produce unmergeable results |
| Low-confidence artifact types produce noisy results | Confidence thresholds, higher judge consensus requirements | Users may lose trust if too many iterations are rejected |
| Criteria proposals accumulate without review | Final report highlights proposals prominently | User may ignore them |
| Agent generates plausible but subtly wrong code | Hard gates (tests) catch functional regressions; can't catch all logic errors | Tests must exist and be meaningful — garbage tests = garbage gates |
| Cost overruns from repeated LLM judge calls | Cost budget stop condition, configurable judge runs | Exact cost tracking depends on provider |

---

## I. Acceptance Decision Engine — Pseudocode

```python
def decide(candidate_diff, current_state, plugin, criteria, config):
    """Core acceptance decision. Returns AcceptRejectDecision."""

    # ── Step 0: Policy / Guardrails ──
    policy_result = policy.check(candidate_diff, config)
    if not policy_result.passed:
        return REJECT(reason="policy_violation", detail=policy_result.violations)

    # ── Step 1: Hard Gates (binary, non-negotiable) ──
    gate_result = plugin.hard_gates(candidate_diff, current_state.targets)
    if not gate_result.all_passed:
        return REJECT(reason="hard_gate_failure", detail=gate_result.failures)

    # ── Step 2: Soft Scoring ──
    # 2a: Deterministic metrics from plugin
    metric_scores = plugin.soft_evaluate(candidate_diff, current_state.targets, criteria)

    # 2b: LLM-as-judge (pairwise: candidate vs current accepted)
    judge_runs = config.llm_judge_runs if plugin.confidence_profile == "low" else 1
    judge_scores = []
    for _ in range(judge_runs):
        score = llm_judge.pairwise_compare(
            current=current_state.snapshot,
            candidate=candidate_diff,
            criteria=criteria,
            # NOTE: no improvement hypothesis in judge context
        )
        judge_scores.append(score)

    judge_aggregate = aggregate(judge_scores)  # mean + variance
    judge_variance = variance(judge_scores)

    # 2c: Composite score
    composite = weighted_sum(metric_scores, judge_aggregate, criteria.weights)
    current_score = current_state.composite_score

    if composite <= current_score:
        return REJECT(reason="no_improvement", detail={
            "current": current_score, "candidate": composite
        })

    # ── Step 3: Confidence ──
    confidence = plugin.base_confidence
    if metric_scores.has_deterministic and metric_scores.agrees_with(judge_aggregate):
        confidence += 0.1  # deterministic + judge agree
    if judge_variance > VARIANCE_THRESHOLD:
        confidence -= 0.2  # judge is unstable
    if candidate_diff.lines_changed < SMALL_DIFF_THRESHOLD:
        confidence -= 0.05  # small diff, large claim
    if not metric_scores.has_deterministic:
        confidence -= 0.15  # no hard signals

    confidence = clamp(confidence, 0.0, 1.0)
    threshold = config.confidence_thresholds[plugin.name]

    if confidence < threshold:
        return REJECT(reason="low_confidence", detail={
            "confidence": confidence, "threshold": threshold
        })

    # ── Step 4: Accept ──
    return ACCEPT(
        composite_score=composite,
        confidence=confidence,
        evidence={
            "gate_results": gate_result,
            "metric_scores": metric_scores,
            "judge_aggregate": judge_aggregate,
            "judge_variance": judge_variance,
            "criteria_version": criteria.version,
        }
    )
```
