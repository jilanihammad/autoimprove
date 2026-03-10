# Phase 3: Core Loop — Beads 11–15

---

## Bead 11: Agent Bridge

**Status:** ⬜ Not started
**Dependencies:** Bead 1 (config.py, types.py)
**Estimated effort:** Medium

### Purpose
Provide an agent-agnostic interface for invoking any CLI-based coding agent (Claude Code, Codex CLI, Kiro CLI, or custom). The bridge handles prompt construction, invocation, response capture, and timeout management. The rest of the system never calls an agent directly — it always goes through this bridge.

### Files to Edit

```
src/agent_bridge.py    # Full implementation (replace stub)
```

### Detailed Specifications

#### `src/agent_bridge.py`

**Dataclasses:**

```python
@dataclass
class AgentRequest:
    prompt: str                     # The full prompt to send to the agent
    working_dir: str                # Directory the agent should operate in (worktree)
    timeout_seconds: int            # Max time for this invocation
    context_files: list[str]        # Files the agent should read (included in prompt or via tool)
    mode: str                       # "modify" (make changes) or "analyze" (read-only response)

@dataclass
class AgentResponse:
    success: bool                   # Did the agent complete without error?
    output: str                     # Agent's text output (stdout)
    error: str | None               # Error message if failed
    duration_seconds: float         # How long the invocation took
    files_modified: list[str]       # Files changed (detected via git diff after invocation)
```

**Class: `AgentBridge`**

```python
class AgentBridge:
    def __init__(self, config: Config):
        self.agent_command = config.agent_command
        self.timeout = config.agent_timeout_seconds
```

**Methods:**

1. **`invoke(request: AgentRequest) -> AgentResponse`**
   - Core method: sends a prompt to the agent and captures the result
   - Implementation varies by agent type (detected from `agent_command`):
     - `claude` → `claude --print "{prompt}"` with `--allowedTools` for file editing
     - `codex` → `codex --prompt "{prompt}"` or pipe via stdin
     - `kiro-cli` → `kiro-cli chat --message "{prompt}"`
     - Custom → `{agent_command} "{prompt}"` (generic subprocess)
   - Sets `cwd` to `request.working_dir` (the worktree)
   - Captures stdout and stderr
   - Enforces timeout via `subprocess.run(timeout=...)`
   - After invocation, runs `git diff --name-only` to detect which files were modified
   - Returns `AgentResponse`

2. **`build_improvement_prompt(program_md: str, search_memory_summary: str, iteration: int, criteria_summary: str, previous_outcomes: list[str]) -> str`**
   - Constructs the prompt for an improvement iteration
   - Template:
     ```
     # AutoImprove — Iteration {iteration}

     ## Your Instructions
     {program_md contents}

     ## Evaluation Criteria
     Your changes will be evaluated against these criteria:
     {criteria_summary}

     ## Previous Attempts (search memory)
     {search_memory_summary — what was tried, what worked, what failed}

     ## Your Task
     1. Review the codebase in your working directory
     2. Identify the single highest-impact improvement you can make
     3. Write a brief hypothesis: what you'll change and why
     4. Make the changes
     5. Verify your changes don't break anything obvious

     ## Constraints
     - Make focused, bounded changes (not sweeping rewrites)
     - Do not modify files outside the target paths
     - Do not add new dependencies unless absolutely necessary
     - Your changes will be automatically evaluated — focus on quality over quantity
     ```

3. **`build_grounding_prompt(program_md: str, profile_md: str, artifact_summary: str) -> str`**
   - Constructs the prompt for the grounding phase
   - Template:
     ```
     # AutoImprove — Grounding Phase

     ## Project Context
     {program_md contents}

     ## Evaluation Profile
     {profile_md contents}

     ## Current Artifacts
     {artifact_summary — file list, basic stats}

     ## Your Task
     Analyze the project and propose:
     1. **Evaluation Criteria**: A rubric for judging improvements. For each criterion:
        - Name
        - Description (what it measures)
        - Weight (0.0-1.0, must sum to 1.0 for scored items)
        - Type: "hard_gate" (pass/fail) or "scored"
        - Metric type: "deterministic" (can be measured by tools) or "judgment" (requires LLM evaluation)
     2. **Improvement Hypotheses**: Ranked list of potential improvements, each with:
        - Description
        - Expected impact (high/medium/low)
        - Files likely affected
        - Risk level

     Respond in JSON format:
     {
       "criteria": [...],
       "hypotheses": [...]
     }
     ```

4. **`build_criteria_review_prompt(current_criteria: str, experiment_history: str, iteration: int) -> str`**
   - Constructs the prompt for criteria review proposals
   - Asks the agent to propose changes to criteria based on what it's learned

5. **`_detect_agent_type() -> str`**
   - Returns "claude", "codex", "kiro", or "generic" based on `self.agent_command`
   - Used to customize invocation flags

6. **`_invoke_subprocess(command: list[str], cwd: str, timeout: int, input_text: str | None = None) -> tuple[str, str, int, float]`**
   - Low-level subprocess runner
   - Returns (stdout, stderr, returncode, duration_seconds)
   - Handles `subprocess.TimeoutExpired` → returns error response

### Agent-Specific Invocation Details

| Agent | Command Pattern | Stdin? | Notes |
|-------|----------------|--------|-------|
| Claude Code | `claude --print -p "{prompt}" --cwd {dir}` | No | Uses `--print` for non-interactive, `--allowedTools` for file ops |
| Codex CLI | `codex -q --prompt "{prompt}"` | No | Quiet mode, auto-approve changes |
| Kiro CLI | `kiro-cli chat --message "{prompt}"` | No | Single-message mode |
| Generic | `{cmd}` with prompt piped to stdin | Yes | Fallback for unknown agents |

### Acceptance Criteria
- [ ] `invoke()` works with at least Claude Code and a generic subprocess
- [ ] Prompt is passed correctly (no truncation, escaping issues)
- [ ] Timeout is enforced — agent can't run forever
- [ ] `files_modified` correctly detected via git diff after invocation
- [ ] `build_improvement_prompt` includes search memory and criteria
- [ ] `build_grounding_prompt` produces parseable JSON response from agent
- [ ] Agent errors (non-zero exit, timeout) produce `AgentResponse(success=False)`
- [ ] Working directory is set to worktree path

### Notes
- The agent bridge is the most agent-specific code in the system — everything else is agent-agnostic
- Prompt construction is separated from invocation so prompts can be logged/audited
- The `--print` / non-interactive mode is critical — we can't have the agent asking for user input during autonomous mode
- Large prompts may need to be written to a temp file and referenced, rather than passed as CLI args (shell arg length limits)
- The bridge does NOT parse the agent's code changes — it only detects which files changed via git

---

## Bead 12: Search Memory

**Status:** ⬜ Not started
**Dependencies:** Bead 1 (types.py)
**Estimated effort:** Small-Medium

### Purpose
Track every hypothesis the agent attempts, its outcome, and patterns of success/failure. This prevents the agent from retrying the same failing ideas and helps it prioritize promising directions. The search memory is fed back into the agent's prompt each iteration.

### Files to Edit

```
src/eval/search_memory.py    # Full implementation (replace stub)
```

### Detailed Specifications

#### `src/eval/search_memory.py`

**Dataclasses:**

```python
@dataclass
class HypothesisRecord:
    iteration: int                  # Which iteration this was
    hypothesis: str                 # What the agent proposed to do
    files_targeted: list[str]       # Files the agent intended to modify
    files_actually_modified: list[str]  # Files that were actually changed
    outcome: str                    # "accepted", "rejected_policy", "rejected_gate", "rejected_score", "rejected_confidence"
    reason: str                     # Brief reason for outcome
    composite_score: float | None   # Score if evaluation reached that stage
    confidence: float | None        # Confidence if calculated
    timestamp: str                  # ISO format

@dataclass
class FileChurnRecord:
    file_path: str
    modification_count: int         # How many times modified across iterations
    net_improvement: bool           # Did modifications result in net improvement?
    iterations: list[int]           # Which iterations modified this file

@dataclass
class PatternRecord:
    pattern: str                    # e.g., "modifying auth.py breaks test_auth"
    occurrences: int                # How many times observed
    first_seen: int                 # Iteration first observed
    last_seen: int                  # Iteration last observed
```

**Class: `SearchMemory`**

```python
class SearchMemory:
    def __init__(self, memory_path: Path):
        self.memory_path = memory_path
        self.hypotheses: list[HypothesisRecord] = []
        self.file_churn: dict[str, FileChurnRecord] = {}
        self.failure_patterns: list[PatternRecord] = []
        self.success_patterns: list[PatternRecord] = []
```

**Methods:**

1. **`record_attempt(iteration: int, hypothesis: str, files_targeted: list[str], files_modified: list[str], outcome: str, reason: str, score: float | None, confidence: float | None) -> None`**
   - Creates a `HypothesisRecord` and appends to history
   - Updates file churn tracking
   - Detects and records patterns (see `_detect_patterns`)
   - Auto-saves to disk

2. **`get_summary_for_prompt(max_entries: int = 10) -> str`**
   - Generates a human-readable summary for inclusion in the agent's prompt
   - Includes:
     - Last N attempts with outcomes (most recent first)
     - Files with high churn (modified 2+ times without improvement)
     - Known failure patterns ("Don't try X because Y")
     - Known success patterns ("Changes to X tend to be accepted")
   - Format:
     ```
     ## Recent Attempts
     - Iter 5: [ACCEPTED] Refactored error handling in api.py (score: 0.72, confidence: 0.85)
     - Iter 4: [REJECTED:gate] Added caching to db.py — tests failed
     - Iter 3: [REJECTED:score] Renamed variables in utils.py — no measurable improvement

     ## Avoid (known failure patterns)
     - Modifying db.py tends to break test_integration (seen 2x)
     - Pure rename/formatting changes score below threshold

     ## High-Churn Files (modified 2+ times without net gain)
     - utils.py (3 modifications, no net improvement)

     ## What Works
     - Error handling improvements tend to be accepted
     ```

3. **`is_similar_to_previous(hypothesis: str, threshold: float = 0.8) -> bool`**
   - Basic similarity check: does this hypothesis look like something already tried?
   - v1 implementation: keyword overlap ratio
     - Tokenize both hypotheses into words
     - Compute Jaccard similarity
     - Return True if similarity > threshold
   - Future: could use embedding similarity

4. **`get_file_churn(file_path: str) -> FileChurnRecord | None`**
   - Returns churn record for a specific file

5. **`get_high_churn_files(threshold: int = 2) -> list[FileChurnRecord]`**
   - Returns files modified `threshold` or more times without net improvement

6. **`_detect_patterns() -> None`**
   - Analyzes hypothesis history for recurring patterns:
     - If same file appears in 2+ rejected attempts → failure pattern
     - If certain keywords appear in 2+ accepted attempts → success pattern
   - Updates `failure_patterns` and `success_patterns`

7. **`save() -> None`**
   - Serializes full state to `memory_path` (JSON)

8. **`load(cls, memory_path: Path) -> SearchMemory`** (classmethod)
   - Loads from disk

### Acceptance Criteria
- [ ] `record_attempt` correctly tracks all hypothesis data
- [ ] File churn tracking updates on every attempt
- [ ] `get_summary_for_prompt` produces clear, useful text
- [ ] `is_similar_to_previous` catches obvious duplicates
- [ ] Pattern detection identifies recurring failure/success patterns
- [ ] State persists to disk and loads correctly
- [ ] Summary respects `max_entries` limit

### Notes
- The search memory is the agent's "learning" within a run — without it, the agent has no memory between iterations
- Similarity detection is intentionally simple in v1 (keyword overlap) — good enough to catch "refactor utils.py" being tried 3 times
- The prompt summary should be concise — don't dump the entire history, just actionable insights
- File churn is a key stop condition signal (Bead 15 uses it)

---

## Bead 13: Grounding Phase

**Status:** ⬜ Not started
**Dependencies:** Bead 4 (preflight), Bead 6 (code_plugin), Bead 9 (acceptance engine), Bead 10 (criteria), Bead 11 (agent_bridge)
**Estimated effort:** Large

### Purpose
Implement the interactive grounding phase where the agent analyzes the project, proposes evaluation criteria and improvement hypotheses, and the human reviews/approves before autonomous mode begins. This is the critical human-in-the-loop step that grounds the agent's understanding.

### Files to Edit

```
src/orchestrator.py    # Implement grounding phase section (partial — full loop in Bead 14)
```

### Detailed Specifications

#### Grounding Phase Flow (within `orchestrator.py`)

**Function: `run_grounding_phase(run_ctx: RunContext, plugin: EvaluatorPlugin, agent: AgentBridge, criteria_mgr: CriteriaManager) -> CriteriaVersion`**

**Step-by-step:**

1. **Capture baseline**
   - Call `plugin.baseline(targets)` to get current state metrics
   - Save to `run_ctx.baseline_path`
   - Tag baseline in git: `autoimprove/<run_id>/baseline`
   - Set `run_ctx.baseline_sha`
   - Print baseline summary to terminal (using `rich`)

2. **Prepare context for agent**
   - Read `program.md` from repo root
   - Read relevant profile from `profiles/` (based on plugin name)
   - Generate artifact summary: file count, total lines, languages detected
   - Combine into grounding prompt via `agent.build_grounding_prompt()`

3. **Invoke agent for analysis**
   - Call `agent.invoke()` with grounding prompt in "analyze" mode
   - Parse agent's JSON response:
     - `criteria`: list of proposed criteria items
     - `hypotheses`: list of proposed improvement hypotheses
   - Handle parse failures: if agent doesn't return valid JSON, retry once with a more explicit prompt

4. **Present criteria to user**
   - Display proposed criteria in a formatted table (using `rich`):
     ```
     ┌─────────────────┬──────────────────────────────┬────────┬───────────┬──────────────┐
     │ Name            │ Description                  │ Weight │ Hard Gate │ Metric Type  │
     ├─────────────────┼──────────────────────────────┼────────┼───────────┼──────────────┤
     │ test_pass_rate  │ All tests must pass          │ -      │ ✓         │ deterministic│
     │ lint_score      │ Reduction in lint errors     │ 0.20   │           │ deterministic│
     │ readability     │ Code is easier to understand │ 0.25   │           │ judgment     │
     └─────────────────┴──────────────────────────────┴────────┴───────────┴──────────────┘
     ```
   - Display proposed hypotheses:
     ```
     Improvement Hypotheses (ranked by expected impact):
     1. [HIGH] Refactor error handling in api/handlers.py — inconsistent try/except patterns
     2. [MEDIUM] Extract common validation logic into shared utils
     3. [LOW] Improve docstrings in core module
     ```

5. **User interaction** (if `grounding_mode == "interactive"`)
   - Prompt: "Accept these criteria? [y/n/edit]"
   - `y` → proceed with proposed criteria
   - `n` → abort the run
   - `edit` → open criteria as JSON in `$EDITOR` (or print JSON for manual editing, then paste back)
   - After editing, re-validate weights sum to 1.0

6. **Auto mode** (if `grounding_mode == "auto"`)
   - Use default criteria from `CriteriaManager.default_*_criteria()` for the plugin type
   - Skip user interaction
   - Print: "Auto mode: using default criteria for {plugin_name}"

7. **Save criteria**
   - Create `CriteriaVersion` via `criteria_mgr.create_initial()`
   - Save to `run_ctx.criteria_dir/criteria_v1.json`

8. **Set initial accepted state**
   - `run_ctx.accepted_state_sha = run_ctx.baseline_sha`
   - Compute initial composite score from baseline metrics
   - Save to `run_ctx.accepted_state_path`

9. **Transition to autonomous mode**
   - Set `run_ctx.status = RunStatus.RUNNING`
   - Print: "Grounding complete. Starting autonomous improvement loop."
   - Print: "Time budget: {X} minutes. Press Ctrl+C to stop early."

### Acceptance Criteria
- [ ] Baseline captured and saved before agent interaction
- [ ] Agent receives well-formed grounding prompt
- [ ] Agent's JSON response parsed into criteria + hypotheses
- [ ] Criteria displayed in formatted table for user review
- [ ] User can accept, reject, or edit criteria interactively
- [ ] Auto mode uses default criteria without user interaction
- [ ] Criteria saved as version 1 via CriteriaManager
- [ ] Initial accepted state set from baseline
- [ ] Parse failures handled gracefully (retry, then fall back to defaults)
- [ ] Git baseline tag created

### Notes
- The grounding phase is the user's only chance to shape the agent's evaluation criteria before autonomous mode
- If the agent proposes bad criteria (e.g., weights don't sum to 1.0), fix automatically and warn the user
- The hypotheses from grounding are informational — they're fed into search memory as initial ideas but not binding
- This is the most interactive part of the system — terminal UX matters here
- Ctrl+C during grounding should cleanly abort (cleanup worktree)

---

## Bead 14: Autonomous Loop

**Status:** ⬜ Not started
**Dependencies:** All Phase 2 beads (5-10), Bead 11 (agent_bridge), Bead 12 (search_memory), Bead 13 (grounding)
**Estimated effort:** Large

### Purpose
Implement the core autonomous iteration loop: hypothesis selection → candidate generation → guardrail validation → hard gates → soft evaluation → confidence calculation → accept/reject → logging → search memory update → criteria review check → stop condition check. This is the heart of AutoImprove.

### Files to Edit

```
src/orchestrator.py    # Implement autonomous loop section (extends Bead 13 work)
```

### Detailed Specifications

#### Main Loop Function

**Function: `run_autonomous_loop(run_ctx: RunContext, plugin: EvaluatorPlugin, agent: AgentBridge, engine: AcceptanceEngine, criteria_mgr: CriteriaManager, search_mem: SearchMemory, policy_config: Config) -> None`**

**Loop body (one iteration):**

```python
while not should_stop(run_ctx, search_mem, config):
    iteration = run_ctx.current_iteration

    # 1. BUILD PROMPT
    prompt = agent.build_improvement_prompt(
        program_md=read_file(repo_root / "program.md"),
        search_memory_summary=search_mem.get_summary_for_prompt(),
        iteration=iteration,
        criteria_summary=criteria_mgr.get_current().to_summary_string(),
        previous_outcomes=get_recent_outcomes(search_mem, n=5)
    )

    # 2. INVOKE AGENT (candidate generation)
    request = AgentRequest(
        prompt=prompt,
        working_dir=str(run_ctx.worktree_path),
        timeout_seconds=config.agent_timeout_seconds,
        context_files=[],
        mode="modify"
    )
    response = agent.invoke(request)

    if not response.success:
        # Agent failed (timeout, error) — log and continue
        search_mem.record_attempt(
            iteration=iteration,
            hypothesis="Agent invocation failed",
            files_targeted=[], files_modified=[],
            outcome="rejected_agent_error",
            reason=response.error or "Unknown agent error",
            score=None, confidence=None
        )
        run_ctx.record_reject()
        continue

    # 3. CAPTURE DIFF
    diff = git_ops.get_diff(
        str(run_ctx.worktree_path),
        run_ctx.accepted_state_sha
    )

    if not diff.files_changed:
        # Agent made no changes — log and continue
        search_mem.record_attempt(...)
        run_ctx.record_reject()
        continue

    # 4. EXTRACT HYPOTHESIS (from agent output)
    hypothesis = extract_hypothesis_from_output(response.output)

    # 5. EVALUATE (policy → gates → soft → judge → confidence → decision)
    decision = engine.evaluate(
        diff=diff,
        targets=plugin.discover_targets(config.target_paths, config.exclude_paths),
        current_state_score=run_ctx.current_composite_score,
        criteria=criteria_mgr.get_current().to_dict(),
        criteria_version=criteria_mgr.get_current().version
    )

    # 6. ACT ON DECISION
    if decision.decision == Decision.ACCEPT:
        sha = git_ops.commit(
            str(run_ctx.worktree_path),
            f"autoimprove: iter {iteration} — {hypothesis[:80]}"
        )
        run_ctx.record_accept(sha)
        run_ctx.current_composite_score = decision.composite_score
    else:
        run_ctx.record_reject()  # This reverts worktree to accepted state

    # 7. LOG
    log_experiment(run_ctx, iteration, hypothesis, diff, decision, response)

    # 8. UPDATE SEARCH MEMORY
    search_mem.record_attempt(
        iteration=iteration,
        hypothesis=hypothesis,
        files_targeted=diff.files_changed,
        files_modified=response.files_modified,
        outcome=decision.reason,
        reason=str(decision.detail),
        score=decision.composite_score,
        confidence=decision.confidence
    )

    # 9. CRITERIA REVIEW (every N iterations)
    if iteration > 0 and iteration % config.eval_refinement_interval == 0:
        review_prompt = agent.build_criteria_review_prompt(
            current_criteria=criteria_mgr.get_current().to_json(),
            experiment_history=search_mem.get_summary_for_prompt(max_entries=20),
            iteration=iteration
        )
        review_response = agent.invoke(AgentRequest(
            prompt=review_prompt,
            working_dir=str(run_ctx.worktree_path),
            timeout_seconds=config.agent_timeout_seconds,
            context_files=[],
            mode="analyze"
        ))
        if review_response.success:
            proposal = parse_criteria_proposal(review_response.output)
            if proposal:
                criteria_mgr.record_proposal(iteration, proposal.changes, proposal.rationale)

    # 10. PRINT PROGRESS
    print_iteration_summary(iteration, decision, run_ctx)

    # 11. SAVE STATE (crash recovery)
    run_ctx.save_state()
    search_mem.save()
```

**Helper Functions (within orchestrator.py):**

- `extract_hypothesis_from_output(output: str) -> str` — Extracts the agent's stated hypothesis from its output. Looks for patterns like "I will..." or "My hypothesis is..." Falls back to first sentence.

- `log_experiment(run_ctx, iteration, hypothesis, diff, decision, response)` — Appends to experiment_log.json (detailed in Bead 16, but basic structure needed here)

- `print_iteration_summary(iteration, decision, run_ctx)` — One-line terminal output per iteration:
  ```
  [Iter 5/∞] ACCEPTED (score: 0.72, conf: 0.85) — Refactored error handling | Budget: 87min remaining | Accepts: 3, Rejects: 2
  [Iter 6/∞] REJECTED:gate — Tests failed after caching change | Budget: 82min remaining | Accepts: 3, Rejects: 3
  ```

- `parse_criteria_proposal(output: str) -> CriteriaProposal | None` — Parses agent's criteria review response

**Signal handling:**
- Register `SIGINT` (Ctrl+C) handler that:
  - Sets a flag to stop after current iteration completes
  - Does NOT kill mid-iteration (let it finish cleanly)
  - Prints: "Stopping after current iteration... (press Ctrl+C again to force quit)"

### Acceptance Criteria
- [ ] Full iteration cycle executes: prompt → agent → diff → evaluate → decide → log → memory
- [ ] Accepted changes are committed with descriptive messages
- [ ] Rejected changes are reverted to last accepted state
- [ ] Search memory updated after every iteration
- [ ] Criteria review proposals captured at configured intervals
- [ ] Progress printed to terminal after each iteration
- [ ] State saved after each iteration (crash recovery)
- [ ] Ctrl+C gracefully stops after current iteration
- [ ] Agent failures (timeout, error) handled without crashing the loop
- [ ] Empty diffs (agent made no changes) handled gracefully

### Notes
- The loop must be robust — it runs unattended for hours. Every error path must be handled.
- State is saved after EVERY iteration — if the process crashes, we can see what happened
- The agent invocation is the most expensive step — everything else should be fast
- `extract_hypothesis_from_output` is best-effort — if parsing fails, use "Unknown hypothesis"
- The loop should print a running timer showing elapsed time and budget remaining

---

## Bead 15: Stop Conditions

**Status:** ⬜ Not started
**Dependencies:** Bead 14 (autonomous loop)
**Estimated effort:** Small

### Purpose
Implement all stop conditions that determine when the autonomous loop should terminate. This is extracted from the loop for clarity and testability.

### Files to Edit

```
src/orchestrator.py    # Add should_stop() function and stop condition logic
```

### Detailed Specifications

#### `should_stop(run_ctx: RunContext, search_mem: SearchMemory, config: Config) -> tuple[bool, str]`

Returns `(should_stop: bool, reason: str)`.

**Stop conditions (checked in order):**

1. **Time budget exhausted**
   - `run_ctx.is_budget_exhausted()` → True
   - Reason: `"Time budget exhausted ({elapsed:.1f}/{budget} minutes)"`

2. **Cost budget exhausted** (if configured)
   - `run_ctx.estimated_cost > config.cost_budget_usd`
   - Reason: `"Cost budget exhausted (${cost:.2f}/${budget:.2f})"`
   - Note: cost tracking is best-effort in v1

3. **Max iterations reached** (if configured)
   - `run_ctx.current_iteration >= config.max_iterations`
   - Reason: `"Max iterations reached ({current}/{max})"`

4. **Consecutive rejections exceeded**
   - `run_ctx.consecutive_rejections >= config.max_consecutive_rejections`
   - Reason: `"Too many consecutive rejections ({count}/{max}). Agent may be stuck."`

5. **File churn detected**
   - Any file modified `config.max_file_churn` times without net improvement
   - Check via `search_mem.get_high_churn_files(config.max_file_churn)`
   - Reason: `"File churn detected: {file} modified {count} times with no net improvement"`

6. **Confidence trending below threshold**
   - Look at last 5 iterations: if average confidence < `config.min_confidence_threshold`
   - Reason: `"Confidence trending below threshold ({avg:.2f} < {threshold})"`

7. **User interrupt (Ctrl+C)**
   - Flag set by signal handler in Bead 14
   - Reason: `"User requested stop (Ctrl+C)"`

8. **No improvements possible**
   - If last 10 iterations were all rejections AND search memory shows all major hypotheses tried
   - Reason: `"No further improvements found after {count} attempts"`

**Function: `print_stop_reason(reason: str) -> None`**
- Prints the stop reason prominently:
  ```
  ════════════════════════════════════════
  AUTOIMPROVE STOPPED: Time budget exhausted (120.3/120 minutes)
  ════════════════════════════════════════
  ```

### Acceptance Criteria
- [ ] All 8 stop conditions implemented
- [ ] Returns both boolean and human-readable reason
- [ ] Time budget check uses wall clock correctly
- [ ] Consecutive rejection counter resets on accept (verified)
- [ ] File churn detection uses search memory data
- [ ] Confidence trending uses rolling window (last 5 iterations)
- [ ] Stop reason printed clearly to terminal

### Notes
- Stop conditions are checked at the TOP of each iteration (before invoking the agent)
- Multiple stop conditions can be true simultaneously — report the first one hit
- The "no improvements possible" condition is the hardest to detect — keep it conservative (10 consecutive rejections + high similarity in hypotheses)
- Cost tracking in v1 is approximate — based on iteration count × estimated cost per iteration
