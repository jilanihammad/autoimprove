# Phase 4: Reporting & UX — Beads 16–19

---

## Bead 16: Experiment Logging

**Status:** ⬜ Not started
**Dependencies:** Bead 14 (autonomous loop)
**Estimated effort:** Small-Medium

### Purpose
Implement structured per-iteration logging that captures the full evidence trail for every experiment. This is the audit log — it must contain enough detail to explain every accept/reject decision after the fact.

### Files to Edit

```
src/reporting/experiment_log.py    # Full implementation (replace stub)
```

### Detailed Specifications

#### `src/reporting/experiment_log.py`

**Dataclass: `ExperimentEntry`**

```python
@dataclass
class ExperimentEntry:
    iteration: int
    timestamp: str                          # ISO format
    hypothesis: str                         # What the agent proposed
    files_modified: list[str]               # Files changed in this iteration
    diff_stats: dict                        # {lines_added, lines_removed, files_changed_count}
    diff_snippet: str                       # First 200 lines of diff (for quick review)

    # Decision
    decision: str                           # "accepted" or "rejected"
    reason: str                             # Reason code
    reason_detail: str                      # Human-readable explanation

    # Evaluation results
    policy_result: dict | None              # Policy check results (if reached)
    gate_results: dict | None               # Hard gate results (if reached)
    soft_scores: dict | None                # Soft evaluation scores (if reached)
    judge_scores: dict | None               # LLM judge scores (if reached)
    judge_variance: float | None            # Judge variance (if multiple runs)
    composite_score: float | None           # Final composite score
    confidence: float | None                # Confidence in decision
    confidence_breakdown: dict | None       # Factor-by-factor confidence calculation

    # Metadata
    criteria_version: int                   # Which criteria version was used
    agent_duration_seconds: float           # How long the agent took
    eval_duration_seconds: float            # How long evaluation took
    total_duration_seconds: float           # Total iteration time

    # Context
    accepted_state_score_before: float      # Score before this iteration
    accepted_state_score_after: float       # Score after (same if rejected)
    cumulative_accepts: int                 # Running total of accepts
    cumulative_rejects: int                 # Running total of rejects
    budget_remaining_minutes: float         # Time remaining after this iteration
```

**Class: `ExperimentLog`**

```python
class ExperimentLog:
    def __init__(self, log_path: Path):
        self.log_path = log_path
        self.entries: list[ExperimentEntry] = []
```

**Methods:**

1. **`append(entry: ExperimentEntry) -> None`**
   - Adds entry to in-memory list
   - Immediately appends to JSON file on disk (append-safe, not full rewrite)
   - File format: JSON array, one entry per element

2. **`get_all() -> list[ExperimentEntry]`**
   - Returns all entries

3. **`get_accepted() -> list[ExperimentEntry]`**
   - Returns only accepted entries

4. **`get_rejected() -> list[ExperimentEntry]`**
   - Returns only rejected entries

5. **`get_by_iteration(iteration: int) -> ExperimentEntry | None`**
   - Returns entry for a specific iteration

6. **`get_stats() -> dict`**
   - Returns summary statistics:
     ```python
     {
         "total_iterations": int,
         "accepted": int,
         "rejected": int,
         "accept_rate": float,
         "avg_composite_score": float,        # Of accepted entries
         "avg_confidence": float,             # Of accepted entries
         "total_agent_time_seconds": float,
         "total_eval_time_seconds": float,
         "rejection_reasons": dict[str, int], # reason -> count
         "most_modified_files": list[tuple[str, int]],  # (file, count)
     }
     ```

7. **`save() -> None`**
   - Full write of all entries to disk (for crash recovery)

8. **`load(cls, log_path: Path) -> ExperimentLog`** (classmethod)
   - Loads from disk

**Helper function:**

9. **`create_entry(iteration: int, hypothesis: str, diff: Diff, decision: AcceptanceDecision, agent_response: AgentResponse, run_ctx: RunContext) -> ExperimentEntry`**
   - Factory function that assembles an `ExperimentEntry` from all the components
   - Truncates diff to first 200 lines for `diff_snippet`
   - Computes `total_duration_seconds` from agent + eval durations

### File Format

`experiment_log.json`:
```json
[
  {
    "iteration": 0,
    "timestamp": "2026-03-10T14:30:22-07:00",
    "hypothesis": "Refactor error handling in api/handlers.py",
    "files_modified": ["src/api/handlers.py"],
    "decision": "accepted",
    "reason": "accepted",
    "composite_score": 0.72,
    "confidence": 0.85,
    "criteria_version": 1,
    ...
  },
  ...
]
```

### Acceptance Criteria
- [ ] `ExperimentEntry` captures all fields needed for audit
- [ ] `append()` writes to disk immediately (no data loss on crash)
- [ ] `get_stats()` computes correct summary statistics
- [ ] `create_entry()` correctly assembles from component objects
- [ ] Diff snippet truncated to reasonable size
- [ ] JSON file is human-readable (pretty-printed)
- [ ] `load()` reconstructs full log from disk

### Notes
- The experiment log is append-only during a run — entries are never modified
- Disk writes after every iteration are critical for crash recovery
- The `diff_snippet` is for quick review — full diffs are in git history
- This log is the primary input for the summary report (Bead 17)

---

## Bead 17: Summary Report

**Status:** ⬜ Not started
**Dependencies:** Bead 14 (autonomous loop), Bead 16 (experiment_log)
**Estimated effort:** Medium

### Purpose
Generate the final human-readable summary report that the user reviews when they wake up. This is the primary deliverable of a run — it must clearly communicate what happened, what improved, what was rejected, and what the agent recommends for next time.

### Files to Edit

```
src/reporting/summary.py    # Full implementation (replace stub)
```

### Detailed Specifications

#### `src/reporting/summary.py`

**Function: `generate_summary(run_ctx: RunContext, experiment_log: ExperimentLog, criteria_mgr: CriteriaManager, search_mem: SearchMemory, plugin: EvaluatorPlugin) -> str`**

Returns a Markdown string. Also saves to `run_ctx.summary_path`.

**Report Structure:**

```markdown
# AutoImprove Run Report

## Run Summary
- **Run ID:** {run_id}
- **Started:** {start_time}
- **Duration:** {elapsed} minutes (budget: {budget} minutes)
- **Stop reason:** {stop_reason}
- **Agent:** {agent_command}
- **Plugin:** {plugin_name} (confidence profile: {profile})

## Results Overview
- **Total iterations:** {total}
- **Accepted:** {accepted} ({accept_rate}%)
- **Rejected:** {rejected}
- **Net improvement:** {baseline_score} → {final_score} ({delta:+.2f})

## Before/After Metrics
| Metric | Baseline | Final | Change |
|--------|----------|-------|--------|
| Test pass rate | 95% | 98% | +3% |
| Lint errors | 42 | 31 | -26% |
| Complexity | 8.2 | 7.1 | -13% |
| ... | ... | ... | ... |

## Accepted Changes (chronological)
### Iteration {N}: {hypothesis}
- **Score:** {composite} (confidence: {confidence})
- **Evidence:** {brief evidence summary}
- **Files:** {files_modified}
- **Git diff:** `git diff autoimprove/{run_id}/iter_{N-1}..autoimprove/{run_id}/iter_{N}`

### Iteration {M}: {hypothesis}
...

## Rejected Changes (summary)
| Iter | Hypothesis | Reason | Score | Confidence |
|------|-----------|--------|-------|------------|
| 2 | Added caching to db.py | gate:tests_failed | - | - |
| 4 | Renamed variables | no_improvement | 0.48 | 0.72 |
| ... | ... | ... | ... | ... |

## Rejection Reasons Breakdown
| Reason | Count | % |
|--------|-------|---|
| hard_gate_failure | 3 | 30% |
| no_improvement | 4 | 40% |
| low_confidence | 2 | 20% |
| policy_violation | 1 | 10% |

## Criteria Evolution Proposals
The agent proposed the following changes to evaluation criteria during the run.
These were NOT applied (v1 policy: criteria are immutable within a run).
Review these for your next run:

### Proposal at Iteration {N}
- **Change:** Add "test_coverage" as a new scored criterion (weight: 0.15)
- **Rationale:** "Several accepted changes improved coverage but this wasn't tracked..."
- **Status:** Pending human review

## Cost & Performance
- **Total agent time:** {total_agent_seconds}s ({avg_per_iteration}s avg per iteration)
- **Total eval time:** {total_eval_seconds}s
- **Estimated cost:** ~${estimated_cost} (based on {iterations} iterations)
- **Time per accepted improvement:** {time_per_accept}s avg

## Files Most Modified
| File | Modifications | Net Outcome |
|------|--------------|-------------|
| src/api/handlers.py | 4 | Improved |
| src/utils.py | 3 | No net change |

## How to Apply
To merge these changes into your branch:
```
autoimprove merge {run_id}
```

To discard:
```
autoimprove discard {run_id}
```

To view the full diff:
```
git diff autoimprove/{run_id}/baseline..autoimprove/{run_id}/final
```
```

**Helper functions:**

1. **`_format_metrics_table(baseline: BaselineSnapshot, current: BaselineSnapshot, plugin: EvaluatorPlugin) -> str`**
   - Uses `plugin.summarize_delta()` to get before/after
   - Formats as Markdown table

2. **`_format_accepted_changes(entries: list[ExperimentEntry], run_id: str) -> str`**
   - Formats each accepted entry with hypothesis, score, evidence, files, git diff command

3. **`_format_rejected_summary(entries: list[ExperimentEntry]) -> str`**
   - Formats rejected entries as a compact table

4. **`_format_proposals(proposals: list[CriteriaProposal]) -> str`**
   - Formats criteria change proposals

5. **`_format_cost_summary(log: ExperimentLog, run_ctx: RunContext) -> str`**
   - Computes and formats cost/performance metrics

### Acceptance Criteria
- [ ] Report contains all sections listed above
- [ ] Before/after metrics table populated from baseline + final state
- [ ] Accepted changes listed with full evidence
- [ ] Rejected changes summarized in compact table
- [ ] Criteria proposals included for human review
- [ ] Cost/performance section computed correctly
- [ ] Merge/discard commands included with correct run ID
- [ ] Report saved to `run_ctx.summary_path`
- [ ] Markdown renders correctly (test in a viewer)

### Notes
- This report is the user's primary interface with the run results — clarity is paramount
- Keep accepted changes detailed (the user needs to understand what was done)
- Keep rejected changes compact (the user mainly cares about patterns, not individual failures)
- The git diff commands should be copy-pasteable
- Include the stop reason prominently — the user needs to know why the run ended

---

## Bead 18: Terminal Output

**Status:** ⬜ Not started
**Dependencies:** Bead 14 (autonomous loop), Bead 17 (summary report)
**Estimated effort:** Small

### Purpose
Implement all terminal output formatting: progress during the run, final results display, and formatted tables/panels. Uses the `rich` library for professional-looking output.

### Files to Edit

```
src/reporting/terminal.py    # Full implementation (replace stub)
```

### Detailed Specifications

#### `src/reporting/terminal.py`

**Functions:**

1. **`print_banner() -> None`**
   ```
   ╔══════════════════════════════════════╗
   ║         AutoImprove v0.1.0          ║
   ║   Autonomous Iterative Improvement  ║
   ╚══════════════════════════════════════╝
   ```

2. **`print_run_config(config: Config, run_id: str) -> None`**
   - Prints run configuration in a panel:
     ```
     Run: 20260310-143022-a7f3b2
     Agent: claude | Budget: 120 min | Targets: src/, workflows/
     ```

3. **`print_baseline_summary(baseline: BaselineSnapshot) -> None`**
   - Prints baseline metrics in a table

4. **`print_preflight_report(result: PreflightResult) -> None`**
   - Formatted checklist (from Bead 4 spec, implemented here with `rich`)

5. **`print_grounding_criteria(criteria: CriteriaVersion) -> None`**
   - Formatted criteria table for user review

6. **`print_grounding_hypotheses(hypotheses: list[dict]) -> None`**
   - Formatted hypothesis list

7. **`print_iteration_result(iteration: int, decision: AcceptanceDecision, run_ctx: RunContext) -> None`**
   - Single-line progress output:
     ```
     [12:45:22] Iter 5 ✓ ACCEPTED (0.72, conf:0.85) — Refactored error handling | 87min left | 3✓ 2✗
     [12:50:15] Iter 6 ✗ REJECTED:gate — Tests failed | 82min left | 3✓ 3✗
     ```

8. **`print_stop_banner(reason: str) -> None`**
   - Prominent stop message (from Bead 15 spec)

9. **`print_final_summary(stats: dict, run_id: str) -> None`**
   - Compact terminal summary:
     ```
     ═══ Run Complete ═══
     Iterations: 15 | Accepted: 8 (53%) | Rejected: 7
     Net improvement: 0.45 → 0.72 (+0.27)
     Duration: 118.5 / 120 minutes
     Report: .autoimprove/runs/20260310-143022-a7f3b2/summary.md

     Next steps:
       autoimprove merge 20260310-143022-a7f3b2    # Apply changes
       autoimprove discard 20260310-143022-a7f3b2  # Discard changes
     ```

10. **`print_error(message: str) -> None`**
    - Red error panel

11. **`print_warning(message: str) -> None`**
    - Yellow warning text

12. **`prompt_user(question: str, choices: list[str]) -> str`**
    - Interactive prompt with choices (for grounding phase)
    - Returns selected choice

### Acceptance Criteria
- [ ] All 12 functions implemented using `rich`
- [ ] Banner displays on run start
- [ ] Iteration progress is single-line (doesn't flood terminal)
- [ ] Final summary is clear and includes next-step commands
- [ ] Colors/formatting degrade gracefully in non-color terminals
- [ ] `prompt_user` handles invalid input gracefully

### Notes
- Use `rich.console.Console` as the shared console instance
- Use `rich.table.Table` for tables, `rich.panel.Panel` for panels
- Keep iteration output to ONE line — the user may be watching 100+ iterations scroll by
- Test with `NO_COLOR=1` to ensure graceful degradation

---

## Bead 19: Merge/Discard CLI

**Status:** ⬜ Not started
**Dependencies:** Bead 2 (git_ops), Bead 3 (run_context)
**Estimated effort:** Small

### Purpose
Implement the `autoimprove merge <run_id>` and `autoimprove discard <run_id>` CLI commands that let the user apply or discard run results after reviewing the summary report.

### Files to Edit

```
src/cli.py    # Add merge and discard command implementations (extend Bead 1 stubs)
```

### Detailed Specifications

#### `autoimprove merge <run_id>`

1. Load run context from `.autoimprove/runs/<run_id>/`
2. Verify run status is `COMPLETED` (not `RUNNING` or `FAILED`)
3. Get the source branch: `autoimprove/<run_id>`
4. Get the target branch: the branch that was active when the run started (stored in run context)
5. Show summary: "Merging {N} accepted changes from run {run_id} into {target_branch}"
6. Show the full diff: `git diff {baseline_sha}..{final_sha} --stat`
7. Prompt: "Proceed? [y/n]"
8. If yes:
   - `git_ops.merge_branch_to(repo_path, source_branch, target_branch)`
   - If merge succeeds: "Changes merged successfully."
   - If merge conflicts: "Merge conflicts detected. Resolve manually, then commit."
9. Clean up: remove worktree, optionally delete the run branch

#### `autoimprove discard <run_id>`

1. Load run context from `.autoimprove/runs/<run_id>/`
2. Show summary: "Discarding run {run_id} ({N} accepted changes will be lost)"
3. Prompt: "Are you sure? [y/n]"
4. If yes:
   - Remove worktree: `git_ops.remove_worktree()`
   - Delete branch: `git_ops.delete_branch()`
   - Print: "Run {run_id} discarded. Run data preserved in .autoimprove/runs/{run_id}/"
   - Note: run data (logs, reports) is NOT deleted — only the git worktree/branch

#### `autoimprove status`

1. List all runs in `.autoimprove/runs/`
2. For each, show: run_id, status, iterations, accepts, start time
3. Format as table:
   ```
   Run ID                        Status     Iters  Accepts  Started
   20260310-143022-a7f3b2       completed  15     8        2026-03-10 14:30
   20260309-220015-b8c4d1       completed  22     12       2026-03-09 22:00
   ```

### Acceptance Criteria
- [ ] `merge` correctly merges run branch into original branch
- [ ] `merge` handles conflicts gracefully (doesn't crash, tells user what to do)
- [ ] `discard` removes worktree and branch
- [ ] `discard` preserves run data (logs, reports)
- [ ] Both commands require confirmation before acting
- [ ] `status` lists all runs with key stats
- [ ] Commands fail gracefully if run_id doesn't exist

### Notes
- Merge is the moment of truth — the user is applying autonomous changes to their real branch
- The confirmation prompt must show enough info for the user to make an informed decision
- Run data is never deleted by these commands — it's the audit trail
- Consider adding `autoimprove diff <run_id>` as a convenience (shows full diff without merging)
