# AutoImprove

Autonomous iterative improvement for code, workflows, and documents.

An AI agent makes changes to your code, those changes get evaluated (tests, lint, LLM judgment), and only improvements that pass all checks are kept. Rinse and repeat. You come back to a report of what changed and why.

Based on the [autoresearch](https://github.com/karpathy/autoresearch) methodology: **modify → evaluate → keep/discard → repeat**.

---

## What Does It Actually Do?

You have a project. You want it improved — cleaner code, better error handling, fewer lint warnings, whatever. Instead of doing it yourself, you:

1. Tell AutoImprove what to improve (`program.md` + `eval_anchors.yaml`)
2. Set a time budget (e.g., "run for 2 hours")
3. Walk away

AutoImprove will:
- Spin up an isolated git worktree (your branch is **never touched**)
- Index your codebase semantically (what each file does, not just file names)
- Analyze the code and produce a prioritized improvement backlog
- For each task: a coder agent makes the change, a reviewer agent evaluates it, and deterministic gates (tests, lint, typecheck) verify nothing broke
- If everything passes → keep it. If not → throw it away and move to the next task.
- Repeat until the time budget or backlog runs out

When it's done, you get a report. If you like the changes, you merge them. If not, you discard them. Your original code is untouched until you say so.

---

## Architecture: Multi-Agent Pipeline

AutoImprove uses 4 specialized LLM agents orchestrated by a Python loop. Each agent gets a focused context window instead of one overloaded agent trying to do everything.

```
GROUNDING PHASE (runs once per run):

  Indexer Agent ─── reads files in batches, produces per-file
  │                 semantic summaries (purpose, abstractions,
  │                 dependencies, complexity hotspots)
  ▼
  Analyst Agent ── reads semantic index + program.md + eval anchors
                   + project memory → produces prioritized backlog
                   of 10-20 specific, actionable improvements

ITERATION LOOP (repeats until budget/backlog exhausted):

  Orchestrator (Python) picks highest-priority pending task
  │
  ▼
  Coder Agent ──── gets ONE task + only the relevant files
  │                no repo index, no search memory bloat
  │                makes focused change
  ▼
  ┌───────────────┬────────────────┐
  │               │                │
  Hard Gates      Reviewer Agent   │  ← run in parallel
  (tests, lint,   (eval anchors +  │
  typecheck)      product context) │
  │               │                │
  └───────┬───────┴────────────────┘
          ▼
     Accept / Reject
```

### Why Multi-Agent?

A single agent with program.md + eval anchors + repo index + search memory + project memory + criteria in one prompt hits context limits and loses focus. The multi-agent approach:

- **Coder gets ~10x smaller context** — just the task and the files it needs to touch
- **Reviewer is purpose-built** for evaluation with the user's actual definition of "better"
- **Analyst runs once** and produces a backlog, so iterations are fast
- **Hard gates run in parallel** with the reviewer — if tests fail, we skip the review entirely

You can fall back to single-agent mode with `orchestration_mode: single` in config.

---

## Setup (One-Time)

```bash
# 1. Clone AutoImprove somewhere on your machine
git clone <this-repo-url> ~/autoimprove
cd ~/autoimprove

# 2. Install dependencies
uv sync

# That's it. AutoImprove is now installed.
```

### Prerequisites

- **Python 3.10+**
- **[uv](https://docs.astral.sh/uv/)** (Python package manager)
- **git** (your project must be a git repo with a clean working tree)
- **An AI coding agent** — one of:
  - [Claude Code](https://docs.anthropic.com/en/docs/claude-code) (`claude` command)
  - [Codex CLI](https://github.com/openai/codex) (`codex` command)
  - [Kiro CLI](https://kiro.dev) (`kiro-cli` command)
  - Or any custom CLI that accepts a prompt and edits files

---

## Using It On Your Project

### Step 1: Add config files to your project

Go to your project root and create three files:

**`program.md`** — Tell the agent what to improve:

```markdown
# AutoImprove Program

## Project Context
Python web API using FastAPI. ~5000 LOC.
Auth module in src/auth/, API handlers in src/api/.
Tests in tests/ (pytest).

## Improvement Goals
- Add proper error handling to all API endpoints
- Reduce cyclomatic complexity in src/auth/permissions.py
- Remove duplicated validation logic

## Constraints
- Do not modify test files
- Preserve all public API signatures
- Do not add new external dependencies
```

**`eval_anchors.yaml`** — Define what "better" actually means for your project:

```yaml
better_means:
  - "Fewer lines of code for the same functionality"
  - "Error messages that tell the user what went wrong and how to fix it"
  - "Functions under 50 lines with clear single responsibility"

worse_means:
  - "Adding abstraction layers that don't reduce duplication"
  - "Changing variable names without structural improvement"
  - "Moving code between files without reducing coupling"

must_preserve:
  - description: "All API response shapes must remain unchanged"
    files: ["src/api/"]
  - description: "Authentication flow must not change"
    files: ["src/auth/"]
  - description: "All existing tests must pass"
    test: "pytest tests/"
```

This is the most important file. It tells the LLM judge and reviewer agent what the project owner (you) considers an improvement vs. a regression. Without it, the system uses generic code quality heuristics.

**`config.yaml`** — Configure the run:

```yaml
# Which AI agent to use
agent_command: "claude"

# How long to run (wall clock)
time_budget_minutes: 60

# Orchestration
orchestration_mode: multi         # multi (default) or single

# What to improve
target_paths:
  - src/

# What NOT to touch
exclude_paths:
  - tests/
  - node_modules/
  - .autoimprove/

# Safety rails
protected_paths:
  - "*.lock"
  - "migrations/"
max_diff_lines: 500
allow_dependency_changes: false
```

### Step 2: Make sure your git repo is clean

```bash
git status          # should show "nothing to commit, working tree clean"
git add -A && git commit -m "pre-autoimprove snapshot"   # if needed
```

### Step 3: Run AutoImprove

```bash
# Interactive mode — analyst proposes backlog, you review
cd ~/my-project
uv run --project ~/autoimprove autoimprove run

# Auto mode — skips review steps
uv run --project ~/autoimprove autoimprove run --auto

# With overrides
uv run --project ~/autoimprove autoimprove run --auto --time 30 --agent "claude"
```

### Step 4: Watch it work

The multi-agent pipeline shows detailed progress:

```
── Multi-Agent Grounding ──
Phase 1: Semantic Indexing
  ⏳ Indexing batch 1/3 (8 files)... 8 summaries (45s)
  ⏳ Indexing batch 2/3 (8 files)... 8 summaries (38s)
  ⏳ Indexing batch 3/3 (4 files)... 4 summaries (22s)
  ✓ Indexed 20 files

Phase 2: Analysis & Backlog
  ⏳ Analyst reviewing codebase... 15 issues identified (62s)
  ✓ Backlog: 0 done, 0 failed/skipped, 15 pending of 15 total
  [0.9] Add input validation to /api/chat endpoint (validation)
  [0.9] Extract shared CTC computation helper (complexity)
  [0.8] Add retry logic to LLM provider calls (error_handling)
  ...

┌─ Iteration 0 (108min remaining) ─────────────────
│ 📋 Task: Add input validation to /api/chat endpoint (priority: 0.9)
│ ⏳ Coder working on: Add input validation to /api/chat endpoint... 185s
│ ⏳ Checking diff... 2 files, +34/-8 lines
│ ⏳ Evaluating (gates + reviewer in parallel)... 67s
│ ✅ ACCEPTED (score: 0.78, confidence: 0.72)
│    Reviewer: Adds proper validation for session ID and message body...
│    Files: agent/server.js
└─ Accepts: 1, Rejects: 0 | Backlog: 1 done, 0 failed, 14 pending
```

### Step 5: Review and merge

```bash
# Look at what changed
git diff autoimprove/<run_id>/baseline..autoimprove/<run_id>/final

# Happy with it? Merge into your branch
uv run --project ~/autoimprove autoimprove merge <run_id>

# Don't like it? Throw it away
uv run --project ~/autoimprove autoimprove discard <run_id>
```

### Step 6: Calibrate (optional but recommended)

After reviewing a run's changes, tell AutoImprove what it got wrong:

```bash
uv run --project ~/autoimprove autoimprove calibrate <run_id>
```

This walks you through accepted changes and lets you flag false positives ("this was accepted but shouldn't have been") with an explanation. Your feedback is stored in project memory and shapes future evaluations — the reviewer agent learns what you actually consider an improvement.

---

## Commands

```bash
autoimprove run                    # Start a run (interactive grounding)
autoimprove run --auto             # Start a run (skip grounding, use defaults)
autoimprove run --auto --time 30   # 30-minute budget
autoimprove run --agent codex      # Use a different agent
autoimprove status                 # List all runs
autoimprove merge <run_id>         # Apply accepted changes to your branch
autoimprove discard <run_id>       # Throw away changes (logs kept)
autoimprove calibrate <run_id>     # Review and flag false positives/negatives
```

---

## How Evaluation Works

Every candidate change goes through a 3-layer evaluation:

### Layer 1: Policy (deterministic, instant)
- Diff size within limits?
- No protected paths modified?
- No secrets in the diff?
- No dependency file changes?

### Layer 2: Hard Gates (deterministic, seconds-minutes)
- Tests pass? (Jest, pytest, npm test — auto-detected)
- Lint clean? (ESLint, ruff — auto-detected)
- Typecheck passes? (tsc, mypy — auto-detected)

### Layer 3: Reviewer Agent (LLM, product-aware)
- Does the change achieve the stated task?
- Does it violate any `must_preserve` constraints from `eval_anchors.yaml`?
- Is it actually better by the project owner's definition (`better_means` / `worse_means`)?
- Are there subtle regressions?

Layers 2 and 3 run **in parallel**. If hard gates fail, the reviewer result is discarded.

### Calibration Memory

When you run `autoimprove calibrate`, your feedback ("this was a false positive because X") is stored in `.autoimprove/memory.json` and injected into future reviewer prompts. Over multiple runs, the system learns your preferences.

---

## Project Memory

AutoImprove remembers across runs. The file `.autoimprove/memory.json` stores:

- **Run summaries**: what was tried, what was accepted/rejected, why
- **Calibrations**: your feedback on false positives/negatives
- **Patterns**: which files resist improvement, which approaches work

On subsequent runs, the analyst agent sees this history and avoids re-proposing failed ideas. The reviewer agent sees your calibration feedback and adjusts its scoring.

---

## Full `config.yaml` Reference

```yaml
# ── Agent ──
agent_command: "claude"           # CLI command for the AI agent
agent_timeout_seconds: 600        # Max time per agent invocation

# ── Budgets ──
time_budget_minutes: 120          # Total wall-clock time for the run
# max_iterations: null            # Optional: hard cap on iteration count

# ── Stop conditions ──
max_consecutive_rejections: 10    # Stop after N rejections in a row
max_file_churn: 3                 # Stop if same file modified N times with no gain
min_confidence_threshold: 0.3     # Stop if avg confidence drops below this

# ── Orchestration ──
orchestration_mode: multi         # multi = 4-agent pipeline; single = original single-agent
grounding_mode: interactive       # interactive = you approve; auto = use defaults

# ── Evaluation ──
llm_judge_model: "claude-sonnet"  # Model for LLM-as-judge (single mode only)
llm_judge_runs: 3                 # Repeated judging for low-confidence artifacts
eval_refinement_interval: 5       # Iterations between criteria review proposals

# ── Targets ──
target_paths:
  - src/
exclude_paths:
  - tests/
  - "**/node_modules/**"
  - .autoimprove/

# ── Policy / guardrails ──
protected_paths:
  - "*.lock"
  - "migrations/"
max_diff_lines: 500
allow_dependency_changes: false
secret_patterns:
  - "AKIA[0-9A-Z]{16}"

# ── Confidence thresholds per artifact type ──
confidence_thresholds:
  code: 0.6
  workflow: 0.4
  document: 0.3
```

---

## Supported Languages & Tools

| Language | Tests | Lint | Typecheck |
|----------|-------|------|-----------|
| **Python** | pytest | ruff | mypy |
| **Node.js/TypeScript** | Jest, npm test | ESLint | tsc --noEmit |
| **Go, Rust, Java, etc.** | — | — | — |

Auto-detected from `package.json`, `pyproject.toml`, `Cargo.toml`, `go.mod`, or file extensions.

---

## File Structure

```
your-project/
├── program.md              # What to improve (required)
├── eval_anchors.yaml       # What "better" means (recommended)
├── config.yaml             # Run configuration (required)
└── .autoimprove/           # Created by AutoImprove (add to .gitignore)
    ├── memory.json         # Cross-run memory + calibrations
    ├── latest -> runs/...  # Symlink to most recent run
    └── runs/
        └── 20260311-143022-a7f3b2/
            ├── worktree/           # Isolated git worktree
            ├── semantic_index.md   # Codebase map from indexer
            ├── backlog.json        # Prioritized tasks from analyst
            ├── baseline.json       # Pre-run metrics
            ├── search_memory.json  # Hypothesis tracking
            ├── accepted_state.json # Run state (crash recovery)
            ├── config.yaml         # Frozen config for this run
            └── summary.md          # Final report
```

---

## FAQ

**Q: Does it modify my code directly?**
No. Everything happens in an isolated git worktree. Your branch is untouched until you run `autoimprove merge`.

**Q: What if the agent breaks my tests?**
That change gets rejected. Tests passing is a hard gate — if tests fail, the change is thrown away and the system moves to the next backlog item.

**Q: What's the difference between `program.md` and `eval_anchors.yaml`?**
`program.md` tells the agent *what* to improve (goals, context, constraints). `eval_anchors.yaml` tells the reviewer *how to judge* whether a change is actually better. Both are important — program.md without eval anchors means the reviewer uses generic heuristics.

**Q: What agents work with this?**
Any CLI agent that can edit files when given a prompt. Tested with Claude Code (`claude`) and Kiro CLI (`kiro-cli`). Codex should work too.

**Q: Can I run it overnight?**
Yes. Set `time_budget_minutes: 480`, run `autoimprove run --auto`, check results in the morning.

**Q: What if I want to stop it early?**
Press Ctrl+C. It finishes the current iteration cleanly, then stops.

**Q: How does calibration work?**
After a run, run `autoimprove calibrate <run_id>`. It walks you through accepted changes and lets you flag mistakes. Your feedback persists in `.autoimprove/memory.json` and shapes future reviewer evaluations.

**Q: Multi-agent vs single-agent — when to use which?**
Multi-agent (default) is better for most projects — focused context, better evaluation. Single-agent is simpler and may work for very small codebases (<500 LOC) where context overflow isn't an issue. Set `orchestration_mode: single` in config.

---

## License

MIT
