# AutoImprove

Autonomous iterative improvement for code, workflows, and documents.

An AI agent makes changes to your code, those changes get evaluated (tests, lint, LLM judgment), and only improvements that pass all checks are kept. Rinse and repeat. You come back to a report of what changed and why.

Based on the [autoresearch](https://github.com/karpathy/autoresearch) methodology: **modify → evaluate → keep/discard → repeat**.

---

## What Does It Actually Do?

You have a project. You want it improved — cleaner code, better error handling, fewer lint warnings, whatever. Instead of doing it yourself, you:

1. Tell AutoImprove what to improve (a short `program.md` file)
2. Set a time budget (e.g., "run for 2 hours")
3. Walk away

AutoImprove will:
- Spin up an isolated git worktree (your branch is **never touched**)
- Call an AI agent (Claude, Codex, Kiro, etc.) to make one small improvement
- Evaluate that change: do tests still pass? Did lint get better? Does an LLM judge think it's actually better?
- If yes → keep it. If no → throw it away.
- Repeat until the time budget runs out

When it's done, you get a report. If you like the changes, you merge them. If not, you discard them. Your original code is untouched until you say so.

---

## Setup (One-Time)

AutoImprove is a standalone tool. You clone it once, install it, and then use it on any project.

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

You do NOT copy your project into the AutoImprove folder. Instead, you add two small files to your project, then run AutoImprove from your project directory.

### Step 1: Add config files to your project

Go to your project root and create two files:

```bash
cd ~/my-project   # your actual project
```

**`program.md`** — Tell the agent what to improve:

```markdown
# AutoImprove Program

## Project Context
Python web API using FastAPI. ~5000 LOC.
Auth module in src/auth/, API handlers in src/api/.
Tests in tests/ (pytest).

## Improvement Goals
- Add proper error handling to all API endpoints (they currently have bare try/except)
- Reduce cyclomatic complexity in src/auth/permissions.py
- Remove duplicated validation logic — extract into src/utils/validators.py

## Constraints
- Do not modify test files
- Preserve all public API signatures
- Do not add new external dependencies
```

**`config.yaml`** — Configure the run:

```yaml
# Which AI agent to use
agent_command: "claude"

# How long to run (wall clock)
time_budget_minutes: 60

# What to improve (relative paths from your project root)
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

AutoImprove requires a clean working tree (no uncommitted changes). This is a safety measure.

```bash
git status          # should show "nothing to commit, working tree clean"
git add -A && git commit -m "pre-autoimprove snapshot"   # if needed
```

### Step 3: Run AutoImprove

From your project directory, run the AutoImprove CLI:

```bash
# Interactive mode — agent proposes criteria, you review and approve
cd ~/my-project
uv run --project ~/autoimprove autoimprove run

# Auto mode — skips the review step, uses sensible defaults
uv run --project ~/autoimprove autoimprove run --auto

# With overrides
uv run --project ~/autoimprove autoimprove run --auto --time 30 --agent "claude"
```

> **Tip:** Create a shell alias to make this shorter:
> ```bash
> alias autoimprove='uv run --project ~/autoimprove autoimprove'
> ```
> Then just: `autoimprove run --auto`

### Step 4: Review results

When the run finishes, you'll see a summary:

```
══════════════════════════════════════════════════
AUTOIMPROVE STOPPED: Time budget exhausted (60.1/60 minutes)
══════════════════════════════════════════════════
Iterations: 24
Accepted: 8  Rejected: 16
Duration: 60.1 minutes

To apply changes:  uv run autoimprove merge 20260310-143022-a7f3b2
To discard:        uv run autoimprove discard 20260310-143022-a7f3b2
```

A detailed report is saved to `.autoimprove/runs/<run_id>/summary.md`.

### Step 5: Merge or discard

```bash
# Look at what changed
git diff autoimprove/<run_id>/baseline..autoimprove/<run_id>/final

# Happy with it? Merge into your branch
uv run --project ~/autoimprove autoimprove merge <run_id>

# Don't like it? Throw it away (logs are preserved)
uv run --project ~/autoimprove autoimprove discard <run_id>
```

---

## What Happens During a Run

```
1. PREFLIGHT        Checks: git clean? agent available? paths exist? no secrets?
       ↓
2. BASELINE         Captures current metrics: test pass rate, lint score, complexity
       ↓
3. GROUNDING        Agent analyzes your code, proposes evaluation criteria
   (interactive)    You review: "Accept these criteria? [y/n/edit]"
       ↓
4. AUTONOMOUS LOOP  Repeats until time/iteration budget runs out:
   ┌─────────────────────────────────────────────────────────┐
   │  Agent picks highest-impact improvement                 │
   │  Agent makes changes in isolated worktree               │
   │  Policy check: diff size ok? no secrets? no protected?  │
   │  Hard gates: tests pass? build ok? typecheck ok?        │
   │  Soft eval: lint better? complexity lower?              │
   │  LLM judge: is this actually an improvement?            │
   │  Confidence check: are we sure enough?                  │
   │  → ACCEPT (commit + keep) or REJECT (revert + discard) │
   └─────────────────────────────────────────────────────────┘
       ↓
5. REPORT           Summary: what improved, what was rejected, why
```

All changes happen in a **git worktree** — a separate copy of your repo. Your actual branch is never modified. The worktree lives at `.autoimprove/runs/<run_id>/worktree/`.

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
```

---

## Full `config.yaml` Reference

```yaml
# ── Agent ──
agent_command: "claude"           # CLI command for the AI agent
agent_timeout_seconds: 300        # Max time per agent invocation

# ── Budgets ──
time_budget_minutes: 120          # Total wall-clock time for the run
# max_iterations: null            # Optional: hard cap on iteration count

# ── Stop conditions ──
max_consecutive_rejections: 5     # Stop after N rejections in a row
max_file_churn: 3                 # Stop if same file modified N times with no gain
min_confidence_threshold: 0.3     # Stop if avg confidence drops below this

# ── Evaluation ──
llm_judge_model: "claude-sonnet"  # Model for LLM-as-judge scoring
llm_judge_runs: 3                 # Repeated judging for low-confidence artifacts
grounding_mode: interactive       # interactive = you approve criteria; auto = defaults
eval_refinement_interval: 5       # Iterations between criteria review proposals

# ── Targets ──
target_paths:                     # Directories/files the agent may improve
  - src/
exclude_paths:                    # Excluded from evaluation and modification
  - tests/
  - node_modules/
  - .autoimprove/

# ── Policy / guardrails ──
protected_paths:                  # Glob patterns the agent must never modify
  - "*.lock"
  - "migrations/"
max_diff_lines: 500               # Max lines changed per iteration
allow_dependency_changes: false   # Block changes to package.json, requirements.txt, etc.
secret_patterns:                  # Regex patterns to detect leaked secrets
  - "AKIA[0-9A-Z]{16}"

# ── Confidence thresholds per artifact type ──
confidence_thresholds:
  code: 0.6                       # Code has strong signals → higher bar
  workflow: 0.4
  document: 0.3                   # Docs rely on LLM judgment → lower bar
```

---

## Supported Artifact Types

| Type | What It Evaluates | Hard Gates | How Confident |
|------|-------------------|-----------|---------------|
| **Code** | Python, JS/TS, Go, Rust, etc. | Tests pass, build ok, typecheck, lint | High — deterministic metrics |
| **Workflow** | n8n workflows, Step Functions | Valid schema, no orphaned nodes | Medium — mixed signals |
| **Document** | Markdown, .docx, .xlsx, .csv | Parseable, not empty | Low — mostly LLM judgment |

AutoImprove auto-detects which plugin to use based on what's in your `target_paths`.

---

## FAQ

**Q: Does it modify my code directly?**
No. Everything happens in an isolated git worktree. Your branch is untouched until you run `autoimprove merge`.

**Q: What if the agent breaks my tests?**
That change gets rejected. Tests passing is a hard gate — if tests fail, the change is thrown away and the agent tries something else.

**Q: What agents work with this?**
Any CLI agent that can edit files when given a prompt. Tested with Claude Code (`claude`), but Codex and Kiro should work too. You can also use a custom command.

**Q: Can I run it overnight?**
Yes, that's the intended use case. Set `time_budget_minutes: 480` (8 hours), run `autoimprove run --auto`, and check the results in the morning.

**Q: What if I want to stop it early?**
Press Ctrl+C. It finishes the current iteration cleanly, then stops. Press Ctrl+C again to force quit.

**Q: Where are the logs?**
`.autoimprove/runs/<run_id>/` in your project directory. Contains: experiment log (every iteration), summary report, criteria, search memory, and frozen config.

**Q: Do I need to add `.autoimprove/` to `.gitignore`?**
Yes. Add `.autoimprove/` to your project's `.gitignore`. The run data is local — you don't want it in version control.

---

## License

MIT
