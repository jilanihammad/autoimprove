# AutoImprove Architecture

Reference for AI agents and developers working in this codebase. Read this before making changes.

---

## What This Is

AutoImprove is an autonomous iterative improvement system for code, workflows, and documents. It follows the **modify -> evaluate -> keep/discard -> repeat** loop. An AI coding agent makes changes in an isolated git worktree, those changes are evaluated (tests, lint, LLM judgment), and only improvements that pass all checks are committed. The user's branch is never touched until they explicitly merge.

---

## High-Level Flow

```
User creates: program.md + eval_anchors.yaml + config.yaml
                              |
                    autoimprove run
                              |
                      ┌───────┴───────┐
                      │   PREFLIGHT   │  Validate env, git, agent, tools
                      └───────┬───────┘
                              |
                 ┌────────────┴────────────┐
                 │  CREATE RUN CONTEXT     │  Isolated worktree, run ID, state
                 └────────────┬────────────┘
                              |
              ┌───────────────┴───────────────┐
              │     GROUNDING PHASE           │
              │  Indexer -> Analyst -> Backlog │
              │  (user approves themes)       │
              └───────────────┬───────────────┘
                              |
              ┌───────────────┴───────────────┐
              │     ITERATION LOOP            │
              │  For each backlog item:       │
              │    Coder modifies files        │
              │    Gates + Reviewer (parallel) │
              │    Accept -> commit            │
              │    Reject -> revert            │
              └───────────────┬───────────────┘
                              |
              ┌───────────────┴───────────────┐
              │     FINALIZE                  │
              │  Summary report, memory save  │
              │  User: merge or discard       │
              └───────────────────────────────┘
```

---

## Source Layout

```
src/
├── cli.py                    # Click CLI entry point. Commands: run, merge, discard, status, calibrate
├── orchestrator.py           # Top-level run_autoimprove(). Routes to single or multi-agent mode
├── multi_orchestrator.py     # Multi-agent pipeline: grounding + iteration loop
├── run_context.py            # Run lifecycle: ID, worktree, state persistence, crash recovery
├── config.py                 # Pydantic config schema, loaded from config.yaml
├── types.py                  # Shared dataclasses: Decision, Diff, GateResult, BaselineSnapshot, etc.
├── git_ops.py                # Git worktree, commit, tag, revert, diff operations (subprocess)
├── agent_bridge.py           # Agent-agnostic CLI invocation (prompt -> subprocess -> parse output)
├── policy.py                 # Guardrails: diff size, protected paths, secrets, dependency changes
├── preflight.py              # Pre-run validation: git clean, agent reachable, tools available
├── project_memory.py         # Cross-run learning: run summaries, calibrations, threshold adjustment
├── repo_index.py             # Simple file-tree index (used in single-agent mode)
├── backlog.py                # BacklogItem + Backlog: prioritized task queue with status tracking
│
├── agents/                   # Specialized LLM sub-agents (multi-agent mode)
│   ├── base.py               # BaseAgent: prompt construction, JSON parsing, subprocess invocation
│   ├── indexer.py            # IndexerAgent: per-file semantic summaries, cached by git blob SHA
│   ├── analyst.py            # AnalystAgent: reads index + program.md -> produces backlog
│   ├── modifier.py           # ModifierAgent: makes focused changes for one backlog item
│   ├── coder.py              # CoderAgent: alias for single-agent mode
│   └── reviewer.py           # ReviewerAgent: evaluates changes against eval anchors
│
├── eval/                     # Evaluation pipeline
│   ├── engine.py             # AcceptanceEngine: policy -> gates -> score -> confidence -> decision
│   ├── criteria.py           # CriteriaManager: versioned rubrics, proposal capture
│   ├── llm_judge.py          # LLM-as-judge: pairwise comparison, repeated judging, aggregation
│   ├── search_memory.py      # Hypothesis tracking, file churn detection, anti-repetition
│   ├── eval_anchors.py       # Load eval_anchors.yaml: better_means, worse_means, must_preserve
│   └── baseline.py           # Baseline snapshot capture
│
├── plugins/                  # Artifact-type evaluators (extensible)
│   ├── base.py               # EvaluatorPlugin ABC: discover, preflight, baseline, hard_gates, soft_evaluate
│   ├── registry.py           # Plugin discovery: built-in + entry points + extra directories
│   ├── code_plugin.py        # Code: tests, lint, typecheck, complexity (HIGH confidence)
│   ├── document_plugin.py    # Docs/slides/sheets: LLM-primary evaluation (LOW confidence)
│   ├── workflow_plugin.py    # Workflows: validation, execution (MEDIUM confidence)
│   └── agent_plugin.py       # Agent instructions: prompt clarity, safety (MEDIUM confidence)
│
└── reporting/
    ├── terminal.py           # Rich terminal UI and progress display
    ├── summary.py            # Final report generation (summary.md)
    └── experiment_log.py     # Per-iteration structured logging (TSV/JSON)
```

### Other Important Files

| File | Purpose |
|------|---------|
| `program.md` | User-written project context, goals, constraints. Fed to analyst and coder agents. |
| `eval_anchors.yaml` | User-defined "better_means", "worse_means", "must_preserve". Drives reviewer and LLM judge. |
| `config.yaml` | Runtime config: agent command, time budget, target paths, policy, thresholds. |
| `profiles/` | Default eval strategy hints per artifact type (code.md, workflow.md, document.md). |
| `pyproject.toml` | Python project metadata. Entry point: `src.cli:main`. Build: hatchling. |
| `tests/e2e/` | End-to-end test with mock agent against a synthetic project. |

---

## Two Orchestration Modes

### Multi-Agent (default, `orchestration_mode: multi`)

Uses 4 specialized agents with focused context windows:

1. **IndexerAgent** — Scans files in batches, produces per-file semantic summaries (purpose, abstractions, dependencies, complexity). Cached by git blob SHA so re-runs skip unchanged files.

2. **AnalystAgent** — Reads the semantic index + program.md + eval anchors + project memory. Produces a prioritized backlog of 10-20 specific improvements with files, categories, and priorities.

3. **ModifierAgent** — Gets ONE backlog item + only the relevant file contents. Makes a focused change. No repo index, no search memory — minimal context.

4. **ReviewerAgent** — Evaluates the diff against eval anchors. Does NOT see the modifier's prompt (context separation). Returns verdict, score, confidence, reasoning.

### Single-Agent (legacy, `orchestration_mode: single`)

One agent handles everything: proposes a hypothesis, makes changes, and the evaluation engine judges. Uses `agent_bridge.py` directly. Simpler but hits context limits on larger codebases.

---

## Evaluation Pipeline

Every candidate change goes through 3 layers, with layers 2 and 3 running in parallel:

### Layer 1: Policy (instant, deterministic)
**Module:** `src/policy.py`

- Diff size within `max_diff_lines`?
- No `protected_paths` modified?
- No secrets matching `secret_patterns`?
- No dependency file changes (unless `allow_dependency_changes: true`)?
- File scope: did the coder only touch files assigned by the backlog item?

Fail = immediate reject, no evaluation wasted.

### Layer 2: Hard Gates (deterministic, seconds-minutes)
**Module:** `src/plugins/<type>_plugin.py` -> `hard_gates()`

Plugin-specific pass/fail checks:
- **Code:** tests pass, lint clean, typecheck passes
- **Workflow:** schema valid, nodes valid
- **Document:** parseable, structure intact

Fail on any gate = reject.

### Layer 3: Reviewer Agent (LLM-based, product-aware)
**Module:** `src/agents/reviewer.py`

- Does the change achieve the stated task?
- Does it violate `must_preserve` constraints?
- Is it better by the user's definition (`better_means` / `worse_means`)?
- Score (0-1), confidence (0-1), verdict (accept/reject), reasoning

### Decision Logic
**Module:** `src/eval/engine.py`

```
All hard gates pass?  ──no──>  REJECT
        |yes
Reviewer says accept? ──no──>  REJECT
        |yes
Confidence >= threshold? ─no─> REJECT (low_confidence)
        |yes
        ACCEPT -> git commit in worktree
```

The iteration strategy varies by plugin confidence profile:
- **AUTO** (code, HIGH confidence): accept without user input
- **INTERACTIVE** (workflow, MEDIUM): show diff, ask user per change
- **PREVIEW** (document, LOW): collect proposals, user picks

---

## Plugin System

All artifact-type evaluators implement `EvaluatorPlugin` (defined in `src/plugins/base.py`):

```python
class EvaluatorPlugin(ABC):
    name: str                          # "code", "document", "workflow", "agent"
    confidence_profile: ConfidenceProfile

    def discover_targets(paths, exclude) -> list[str]    # Find evaluable files
    def preflight(targets, working_dir) -> PreflightResult  # Check tools exist
    def baseline(targets, working_dir) -> BaselineSnapshot  # Capture current metrics
    def hard_gates(diff, targets, working_dir) -> GateResult  # Pass/fail checks
    def soft_evaluate(diff, targets, criteria, wd) -> SoftEvalResult  # Scored metrics
    def iteration_strategy() -> IterationStrategy        # AUTO/INTERACTIVE/PREVIEW
    def analyst_categories() -> list[dict]               # Categories for backlog
    def modifier_role() -> str                           # Coder agent system prompt
    def reviewer_focus() -> str                          # Reviewer focus areas
```

Plugins are registered via `PluginRegistry` which discovers built-in plugins and supports entry-point-based extension.

---

## State & Persistence

### Per-Run (`.autoimprove/runs/<run_id>/`)

| File | What It Stores |
|------|---------------|
| `worktree/` | Isolated git worktree — all changes happen here |
| `config.yaml` | Frozen copy of config for this run |
| `semantic_index.md` | Per-file semantic summaries from IndexerAgent |
| `backlog.json` | Prioritized task list from AnalystAgent |
| `baseline.json` | Pre-run metrics snapshot |
| `search_memory.json` | Every hypothesis attempted, outcomes, file churn |
| `accepted_state.json` | Current run state (iteration count, SHA, scores) |
| `experiment_log.tsv` | Per-iteration log: task, decision, score, files |
| `summary.md` | Final report |

### Cross-Run (`.autoimprove/`)

| File | What It Stores |
|------|---------------|
| `memory.json` | Run summaries, user calibrations, learned patterns |
| `index_cache.json` | Semantic summaries cached by git blob SHA |

### Crash Recovery

`RunContext` saves state to `accepted_state.json` after every iteration. On restart, it can resume from the last accepted commit SHA.

---

## Key Data Types (`src/types.py`)

| Type | Purpose |
|------|---------|
| `Decision` | Enum: ACCEPT / REJECT |
| `Diff` | files_changed, lines_added/removed, raw_diff |
| `GateResult` | all_passed, per-gate results, failure list |
| `SoftEvalResult` | per-metric scores, has_deterministic flag, composite |
| `BaselineSnapshot` | plugin_name, timestamp, metrics dict |
| `SemanticDiff` | Human-readable diff for binary files (PPTX, DOCX) |
| `RunStatus` | INITIALIZING -> GROUNDING -> RUNNING -> STOPPED/COMPLETED/FAILED |
| `IterationStrategy` | AUTO / INTERACTIVE / BATCH / PREVIEW |
| `ConfidenceProfile` | HIGH / MEDIUM / LOW |

---

## Agent Invocation

All agents are invoked as CLI subprocesses via `src/agent_bridge.py` (single-agent) or `src/agents/base.py` (multi-agent). No LLM SDK dependency — any CLI that accepts a prompt and edits files works.

The `config.agent_command` setting controls which CLI to use (e.g., `claude`, `codex`, `kiro-cli`).

JSON parsing from agent output uses multiple fallback strategies: markdown fence extraction, balanced bracket matching, and full-output parse.

---

## Stop Conditions

The loop exits when any of these triggers (checked in `orchestrator.should_stop()`):

1. **Time budget exhausted** — `config.time_budget_minutes`
2. **Max iterations reached** — `config.max_iterations`
3. **Too many consecutive rejections** — `config.max_consecutive_rejections`
4. **File churn** — same file modified N times with no improvement (`config.max_file_churn`)
5. **Confidence trending down** — average confidence below `config.min_confidence_threshold`
6. **Backlog exhausted** — all items done/failed/skipped, and no new items after analyst regeneration (`config.max_backlog_regenerations`)
7. **User interrupt** — Ctrl+C (first press stops cleanly, second force-quits)

---

## Cross-Run Learning

### Project Memory (`src/project_memory.py`)

After each run, a summary is saved to `.autoimprove/memory.json`:
- What was tried, accepted, rejected, and why
- Which files resisted improvement
- Which approaches worked

On subsequent runs, the analyst agent sees this history to avoid re-proposing failed ideas.

### Calibration

`autoimprove calibrate <run_id>` lets the user flag false positives ("this was accepted but shouldn't have been"). Feedback is stored in memory and injected into future reviewer/analyst prompts. Acceptance thresholds can be adjusted based on calibration patterns.

---

## Tech Stack

| Component | Technology |
|-----------|-----------|
| Language | Python 3.10+ |
| CLI | Click |
| Config validation | Pydantic 2.0+ |
| Config format | YAML (PyYAML) |
| Terminal UI | Rich |
| Build | Hatchling |
| Package manager | uv |
| Testing | pytest |
| Linting | ruff |
| VCS | git (subprocess calls) |
| Agent invocation | Any CLI (subprocess) |

---

## Common Modification Patterns

### Adding a new plugin

1. Create `src/plugins/<name>_plugin.py` implementing `EvaluatorPlugin`
2. Register it in `src/plugins/registry.py` -> `_load_builtin_plugins()`
3. Add a profile in `profiles/<name>.md`
4. Add confidence threshold in config schema (`src/config.py`)

### Adding a new agent

1. Create `src/agents/<name>.py` extending `BaseAgent` from `src/agents/base.py`
2. Wire it into the orchestrator (`multi_orchestrator.py` or `orchestrator.py`)

### Changing evaluation logic

- Policy checks: `src/policy.py`
- Hard gates: the relevant plugin's `hard_gates()` method
- LLM judgment: `src/eval/llm_judge.py` or `src/agents/reviewer.py`
- Accept/reject decision: `src/eval/engine.py`

### Changing the iteration loop

- Multi-agent: `src/multi_orchestrator.py` -> `run_multi_agent_loop()`
- Single-agent: `src/orchestrator.py` -> `run_autonomous_loop()`
- Stop conditions: `src/orchestrator.py` -> `should_stop()`
