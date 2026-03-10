# Phase 1: Foundation — Beads 1–4

---

## Bead 1: Project Scaffolding

**Status:** ⬜ Not started
**Dependencies:** None
**Estimated effort:** Small

### Purpose
Set up the Python project structure, CLI entrypoint, config schema, and all `__init__.py` files so that every subsequent bead has a place to land. This is the skeleton — no business logic yet.

### Files to Create

```
autoimprove/
├── autoimprove.sh                # Shell entrypoint wrapper
├── pyproject.toml                # Python project config + dependencies
├── config.yaml                   # Default runtime config (template)
├── program.md                    # Template program.md with placeholder content
│
├── src/
│   ├── __init__.py               # Package init, version string
│   ├── cli.py                    # CLI entrypoint: argument parsing, config loading
│   ├── config.py                 # Config schema (dataclass/pydantic), loader, validator
│   ├── types.py                  # Shared type definitions used across all modules
│   ├── orchestrator.py           # Stub only — filled in Bead 14
│   ├── run_context.py            # Stub only — filled in Bead 3
│   ├── git_ops.py                # Stub only — filled in Bead 2
│   ├── agent_bridge.py           # Stub only — filled in Bead 11
│   ├── preflight.py              # Stub only — filled in Bead 4
│   ├── policy.py                 # Stub only — filled in Bead 7
│   │
│   ├── eval/
│   │   ├── __init__.py
│   │   ├── engine.py             # Stub
│   │   ├── baseline.py           # Stub
│   │   ├── criteria.py           # Stub
│   │   ├── llm_judge.py          # Stub
│   │   └── search_memory.py      # Stub
│   │
│   ├── plugins/
│   │   ├── __init__.py
│   │   ├── base.py               # Stub
│   │   ├── code_plugin.py        # Stub
│   │   ├── workflow_plugin.py    # Stub
│   │   ├── document_plugin.py    # Stub
│   │   └── registry.py           # Stub
│   │
│   └── reporting/
│       ├── __init__.py
│       ├── experiment_log.py     # Stub
│       ├── summary.py            # Stub
│       └── terminal.py           # Stub
│
└── profiles/
    ├── code.md                   # Default code eval profile (human-readable)
    ├── workflow.md               # Default workflow eval profile
    └── document.md               # Default document eval profile
```

### Detailed Specifications

#### `pyproject.toml`
- Project name: `autoimprove`
- Python: `>=3.10`
- Dependencies:
  - `pyyaml` — config parsing
  - `pydantic>=2.0` — config schema validation
  - `rich` — terminal output formatting
  - `click` — CLI framework
- Dev dependencies:
  - `pytest`
  - `ruff` — linting
- Entry point: `autoimprove = "src.cli:main"`
- Build system: `hatchling`

#### `autoimprove.sh`
- Thin wrapper: checks `uv` is installed, then runs `uv run python -m src.cli "$@"`
- If `uv` not found, falls back to `python -m src.cli "$@"`
- Must be executable (`chmod +x`)

#### `config.yaml` (default template)
Full schema as defined in the architecture plan section E. All fields with sensible defaults. Comments explaining each field.

#### `src/config.py`
- Pydantic `BaseModel` for config schema with all fields from config.yaml
- `load_config(path: str) -> Config` — loads and validates YAML
- `validate_config(config: Config) -> list[str]` — returns list of validation errors (empty = valid)
- Fields:
  - `agent_command: str` (default: "claude")
  - `agent_timeout_seconds: int` (default: 300)
  - `time_budget_minutes: int` (required, no default — user must set)
  - `cost_budget_usd: float | None` (default: None)
  - `max_iterations: int | None` (default: None)
  - `max_consecutive_rejections: int` (default: 5)
  - `max_file_churn: int` (default: 3)
  - `min_confidence_threshold: float` (default: 0.3)
  - `eval_refinement_interval: int` (default: 5)
  - `llm_judge_model: str` (default: "claude-sonnet")
  - `llm_judge_runs: int` (default: 3)
  - `grounding_mode: Literal["interactive", "auto"]` (default: "interactive")
  - `target_paths: list[str]` (required)
  - `exclude_paths: list[str]` (default: ["tests/", "node_modules/", ".autoimprove/"])
  - `protected_paths: list[str]` (default: ["*.lock", "migrations/"])
  - `max_diff_lines: int` (default: 500)
  - `allow_dependency_changes: bool` (default: False)
  - `secret_patterns: list[str]` (default: ["AKIA[0-9A-Z]{16}"])
  - `confidence_thresholds: dict[str, float]` (default: {"code": 0.6, "workflow": 0.4, "document": 0.3})

#### `src/types.py`
Shared types used across modules:
- `Decision` enum: `ACCEPT`, `REJECT`
- `ConfidenceProfile` enum: `HIGH`, `MEDIUM`, `LOW`
- `RunStatus` enum: `INITIALIZING`, `GROUNDING`, `RUNNING`, `STOPPED`, `COMPLETED`, `FAILED`
- `ExperimentOutcome` dataclass: `decision`, `reason`, `composite_score`, `confidence`, `evidence`, `duration_seconds`, `criteria_version`
- `Diff` dataclass: `files_changed: list[str]`, `lines_added: int`, `lines_removed: int`, `raw_diff: str`
- `GateResult` dataclass: `all_passed: bool`, `gates: dict[str, bool]`, `failures: list[str]`
- `SoftEvalResult` dataclass: `scores: dict[str, float]`, `has_deterministic: bool`, `composite: float`

#### `src/cli.py`
- Uses `click` for CLI
- Commands:
  - `autoimprove run` — main command (delegates to orchestrator, stubbed for now)
  - `autoimprove merge <run_id>` — stub
  - `autoimprove discard <run_id>` — stub
  - `autoimprove status` — stub (show current/recent runs)
- `run` command prompts user for:
  - Time budget (if not in config)
  - Target paths (if not in config)
  - Agent command (if not in config)
- Loads config.yaml, validates, passes to orchestrator

#### `program.md` (template)
```markdown
# AutoImprove Program

## Project Context
[Describe what this project is, its purpose, tech stack, etc.]

## Improvement Goals
[List specific improvement objectives, e.g.:]
- Reduce cyclomatic complexity in [module]
- Improve error handling in [component]
- Refactor [area] for better readability

## Constraints
[List things the agent must NOT do:]
- Do not modify test files
- Preserve all public API signatures
- Do not change database schema

## Artifact Types
[Specify which evaluator plugins apply:]
- code
```

#### Profile files (`profiles/*.md`)
Human-readable descriptions of evaluation strategies per artifact type. These are context for the agent, not executable code. Include:
- What metrics matter for this artifact type
- What constitutes a "hard gate" vs "soft improvement"
- What the confidence profile is and why
- Example rubric items

### Acceptance Criteria
- [ ] `uv sync` succeeds with no errors
- [ ] `uv run python -m src.cli --help` prints help text with all commands
- [ ] `uv run python -m src.cli run --help` prints run-specific help
- [ ] Config loads and validates from config.yaml
- [ ] All stub files exist and are importable (no import errors)
- [ ] `autoimprove.sh` runs and delegates to Python correctly
- [ ] All `__init__.py` files present in every package directory

### Notes
- Stubs should contain the class/function signatures with `pass` or `raise NotImplementedError` bodies
- Every stub should have a docstring explaining what it will do (for context when we return to it)
- This bead is purely structural — no business logic

---

## Bead 2: Git Operations

**Status:** ⬜ Not started
**Dependencies:** None (can be built in parallel with Bead 1)
**Estimated effort:** Medium

### Purpose
Implement all git operations needed by the system: worktree management, commits, tags, reverts, diff extraction, and run ID generation. This is the foundation for workspace isolation and the checkpoint/revert cycle.

### Files to Edit

```
src/git_ops.py    # Full implementation (replace stub from Bead 1)
```

### Detailed Specifications

#### `src/git_ops.py`

**Imports:** `subprocess`, `os`, `hashlib`, `datetime`, `pathlib`

**Functions:**

1. `generate_run_id() -> str`
   - Format: `YYYYMMDD-HHMMSS-<6char_hash>`
   - Hash derived from: timestamp + random bytes
   - Example: `20260310-143022-a7f3b2`

2. `ensure_git_repo(path: str) -> bool`
   - Verify the given path is inside a git repository
   - Returns True/False

3. `is_repo_clean(path: str) -> bool`
   - Check `git status --porcelain` is empty
   - Returns True if clean, False if dirty

4. `get_current_branch(path: str) -> str`
   - Returns current branch name via `git rev-parse --abbrev-ref HEAD`

5. `get_head_sha(path: str) -> str`
   - Returns current HEAD commit SHA (short, 8 chars)

6. `create_worktree(repo_path: str, worktree_path: str, branch_name: str) -> str`
   - Creates a new git worktree at `worktree_path` from current HEAD
   - Creates a new branch `autoimprove/<run_id>` for the worktree
   - Returns the worktree path
   - Raises `GitError` if worktree creation fails

7. `remove_worktree(repo_path: str, worktree_path: str) -> None`
   - Removes the worktree and prunes
   - `git worktree remove <path> --force`
   - `git worktree prune`

8. `commit(worktree_path: str, message: str, files: list[str] | None = None) -> str`
   - If `files` is None, stages all changes (`git add -A`)
   - If `files` provided, stages only those files
   - Commits with the given message
   - Returns the commit SHA
   - Raises `GitError` if nothing to commit

9. `tag(worktree_path: str, tag_name: str) -> None`
   - Creates a lightweight tag at current HEAD
   - Tag format: `autoimprove/<run_id>/<label>` (e.g., `autoimprove/20260310-143022-a7f3b2/baseline`)

10. `revert_to_commit(worktree_path: str, commit_sha: str) -> None`
    - Hard reset worktree to the given commit: `git reset --hard <sha>`
    - Used when a candidate is rejected — revert to last accepted state

11. `get_diff(worktree_path: str, from_ref: str, to_ref: str = "HEAD") -> Diff`
    - Returns a `Diff` object (from `types.py`) with:
      - `files_changed`: list of file paths
      - `lines_added`: count
      - `lines_removed`: count
      - `raw_diff`: full diff text
    - Uses `git diff --stat` for counts and `git diff` for raw

12. `get_diff_staged(worktree_path: str) -> Diff`
    - Same as above but for staged changes (candidate before commit)

13. `get_last_commit_sha(worktree_path: str) -> str`
    - Returns SHA of the most recent commit

14. `merge_branch_to(repo_path: str, source_branch: str, target_branch: str) -> bool`
    - Merges source into target
    - Returns True on success, False on conflict
    - Used by `autoimprove merge <run_id>`

15. `delete_branch(repo_path: str, branch_name: str) -> None`
    - Deletes a local branch: `git branch -D <name>`
    - Used by `autoimprove discard <run_id>`

**Error handling:**
- Custom `GitError(Exception)` class with:
  - `command: str` — the git command that failed
  - `stderr: str` — error output
  - `returncode: int`
- All subprocess calls use `subprocess.run` with `capture_output=True`, `text=True`
- Check `returncode != 0` and raise `GitError`

**Internal helper:**
- `_run_git(args: list[str], cwd: str) -> subprocess.CompletedProcess`
  - Runs `git <args>` in the given directory
  - Raises `GitError` on failure
  - Logs the command being run (for debugging)

### Acceptance Criteria
- [ ] All 15 functions implemented and handle errors gracefully
- [ ] `GitError` exception class defined with command, stderr, returncode
- [ ] Worktree creation/removal works (test in a temp git repo)
- [ ] Commit + tag + revert cycle works correctly
- [ ] Diff extraction returns correct `Diff` objects
- [ ] Run ID generation produces unique, sortable IDs
- [ ] No direct `os.system` calls — all via `subprocess.run`

### Notes
- All git commands must specify `cwd` explicitly — never rely on the process working directory
- Worktree branch naming: `autoimprove/<run_id>` (e.g., `autoimprove/20260310-143022-a7f3b2`)
- Tag naming: `autoimprove/<run_id>/<label>` where label is `baseline`, `iter_N`, or `final`
- This module has NO dependency on config or any other src module except `types.py`

---

## Bead 3: Run Context

**Status:** ⬜ Not started
**Dependencies:** Bead 1 (config.py, types.py), Bead 2 (git_ops.py)
**Estimated effort:** Medium

### Purpose
Manage the full lifecycle of a run: create the run directory structure, track run state (status, current iteration, accepted state, budget consumption), and provide a central object that all other components reference during execution.

### Files to Edit

```
src/run_context.py    # Full implementation (replace stub from Bead 1)
```

### Detailed Specifications

#### `src/run_context.py`

**Class: `RunContext`**

This is the central state object for a run. Created once during initialization, passed to all components.

**Constructor: `__init__(self, config: Config, repo_path: str)`**
- Generates run ID via `git_ops.generate_run_id()`
- Sets up all paths (see directory structure below)
- Initializes state fields
- Does NOT create directories or worktree yet (that's `initialize()`)

**Properties/Fields:**
```python
run_id: str                          # Generated run ID
repo_path: str                       # Original repo root
config: Config                       # Frozen config for this run
status: RunStatus                    # Current run status
current_iteration: int               # 0-indexed iteration counter
start_time: datetime                 # When the run started
accepted_state_sha: str | None       # Git SHA of current accepted state
baseline_sha: str | None             # Git SHA of baseline
total_accepts: int                   # Count of accepted iterations
total_rejects: int                   # Count of rejected iterations
consecutive_rejections: int          # Current streak of rejections
criteria_version: int                # Current criteria version number

# Paths
run_dir: Path                        # .autoimprove/runs/<run_id>/
worktree_path: Path                  # .autoimprove/runs/<run_id>/worktree/
config_path: Path                    # .autoimprove/runs/<run_id>/config.yaml
baseline_path: Path                  # .autoimprove/runs/<run_id>/baseline.json
accepted_state_path: Path            # .autoimprove/runs/<run_id>/accepted_state.json
experiment_log_path: Path            # .autoimprove/runs/<run_id>/experiment_log.json
search_memory_path: Path             # .autoimprove/runs/<run_id>/search_memory.json
criteria_dir: Path                   # .autoimprove/runs/<run_id>/criteria/
proposals_dir: Path                  # .autoimprove/runs/<run_id>/proposals/
summary_path: Path                   # .autoimprove/runs/<run_id>/summary.md
```

**Methods:**

1. `initialize(self) -> None`
   - Creates the full directory structure under `.autoimprove/runs/<run_id>/`
   - Creates git worktree via `git_ops.create_worktree()`
   - Freezes config to `config.yaml` in run dir
   - Sets status to `INITIALIZING`
   - Creates symlink `.autoimprove/latest` → `runs/<run_id>`

2. `set_baseline(self, sha: str) -> None`
   - Records baseline SHA
   - Tags it via `git_ops.tag()`

3. `record_accept(self, sha: str) -> None`
   - Updates `accepted_state_sha`
   - Increments `total_accepts`
   - Resets `consecutive_rejections` to 0
   - Increments `current_iteration`
   - Tags via `git_ops.tag()` as `iter_N`

4. `record_reject(self) -> None`
   - Increments `total_rejects`
   - Increments `consecutive_rejections`
   - Increments `current_iteration`
   - Reverts worktree to `accepted_state_sha` via `git_ops.revert_to_commit()`

5. `elapsed_minutes(self) -> float`
   - Returns minutes since `start_time`

6. `budget_remaining_minutes(self) -> float`
   - Returns `config.time_budget_minutes - elapsed_minutes()`

7. `is_budget_exhausted(self) -> bool`
   - Returns True if time budget exceeded

8. `save_state(self) -> None`
   - Persists current run state to `accepted_state.json`
   - Fields: run_id, status, current_iteration, total_accepts, total_rejects, accepted_state_sha, elapsed_minutes, criteria_version

9. `load_state(cls, run_dir: Path) -> RunContext` (classmethod)
   - Reconstructs a RunContext from a saved run directory
   - Used for `autoimprove status` and crash recovery

10. `finalize(self) -> None`
    - Sets status to `COMPLETED`
    - Tags final state as `autoimprove/<run_id>/final`
    - Saves final state

11. `cleanup(self) -> None`
    - Removes worktree via `git_ops.remove_worktree()`
    - Called by `autoimprove discard`

**Directory structure created by `initialize()`:**
```
.autoimprove/
├── runs/
│   └── <run_id>/
│       ├── worktree/           # Git worktree
│       ├── config.yaml         # Frozen config
│       ├── criteria/           # Criteria versions (criteria_v1.json, etc.)
│       ├── proposals/          # Criteria change proposals
│       ├── baseline.json       # Filled by baseline.py
│       ├── accepted_state.json # Updated each iteration
│       ├── experiment_log.json # Filled by experiment_log.py
│       ├── search_memory.json  # Filled by search_memory.py
│       └── summary.md          # Filled at wrap-up
└── latest -> runs/<run_id>     # Symlink
```

### Acceptance Criteria
- [ ] `RunContext` can be created from a `Config` object
- [ ] `initialize()` creates full directory structure + worktree
- [ ] `record_accept()` and `record_reject()` correctly update all counters
- [ ] `record_reject()` reverts worktree to last accepted state
- [ ] `save_state()` / `load_state()` round-trip correctly
- [ ] `elapsed_minutes()` and `budget_remaining_minutes()` return correct values
- [ ] Symlink `.autoimprove/latest` points to current run
- [ ] `cleanup()` removes worktree without errors

### Notes
- `RunContext` is the single source of truth for run state — every other component reads from it
- The config is frozen at run start — changes to `config.yaml` in the project root during a run have no effect
- `accepted_state.json` is written after every iteration so we can recover from crashes
- The worktree branch name is `autoimprove/<run_id>`

---

## Bead 4: Preflight

**Status:** ⬜ Not started
**Dependencies:** Bead 1 (config.py), Bead 2 (git_ops.py), Bead 3 (run_context.py)
**Estimated effort:** Small-Medium

### Purpose
Validate the entire environment before starting a run. Catch every possible failure upfront so we never burn time/money on a run that was doomed from the start. Fails fast with clear, actionable error messages.

### Files to Edit

```
src/preflight.py    # Full implementation (replace stub from Bead 1)
```

### Detailed Specifications

#### `src/preflight.py`

**Dataclass: `PreflightResult`**
```python
@dataclass
class PreflightResult:
    passed: bool
    checks: list[PreflightCheck]  # All checks run
    errors: list[str]             # Fatal — run cannot proceed
    warnings: list[str]           # Non-fatal — run can proceed but user should know

@dataclass
class PreflightCheck:
    name: str           # e.g., "git_repo", "agent_command", "target_paths"
    passed: bool
    message: str        # Human-readable result
    fatal: bool         # If True and failed, blocks the run
```

**Function: `run_preflight(config: Config, repo_path: str) -> PreflightResult`**

Runs all checks in order. Does NOT short-circuit — runs all checks and reports all failures at once.

**Checks (in order):**

1. **`check_git_repo`** (fatal)
   - Verify `repo_path` is inside a git repository
   - Uses `git_ops.ensure_git_repo()`
   - Error: "Not a git repository. AutoImprove requires git for workspace isolation and checkpointing."

2. **`check_git_clean`** (fatal)
   - Verify working tree is clean (no uncommitted changes)
   - Uses `git_ops.is_repo_clean()`
   - Error: "Git working tree has uncommitted changes. Please commit or stash before running AutoImprove."

3. **`check_python_version`** (fatal)
   - Verify Python >= 3.10
   - Error: "Python 3.10+ required. Found: {version}"

4. **`check_agent_command`** (fatal)
   - Verify the agent command is resolvable
   - Run `which <agent_command>` or `shutil.which()`
   - Error: "Agent command '{cmd}' not found in PATH. Install it or update config.yaml."

5. **`check_target_paths`** (fatal)
   - Verify all `target_paths` exist and are non-empty
   - Error per path: "Target path '{path}' does not exist or is empty."

6. **`check_exclude_paths_not_overlapping`** (warning)
   - Warn if any `target_paths` are also in `exclude_paths`
   - Warning: "Target path '{path}' is also in exclude_paths — it will be excluded."

7. **`check_config_valid`** (fatal)
   - Run `validate_config()` from config.py
   - Report all validation errors

8. **`check_disk_space`** (warning)
   - Check available disk space at `.autoimprove/` location
   - Warn if < 1GB available
   - Warning: "Low disk space ({available}MB). Worktree creation may fail."

9. **`check_program_md_exists`** (warning)
   - Check if `program.md` exists in repo root
   - Warning: "No program.md found. The agent will have no improvement instructions. Create one for better results."

10. **`check_secret_scan`** (warning)
    - Basic scan of target paths for patterns matching `config.secret_patterns`
    - Warning per match: "Potential secret found in {file}:{line}. Review before running."
    - Does NOT read file contents into memory — uses `grep -rn` with patterns

11. **`check_worktree_path_available`** (fatal)
    - Verify `.autoimprove/runs/` can be created (parent dir writable)
    - Verify no existing worktree conflicts
    - Error: "Cannot create run directory at {path}. Check permissions."

**Function: `print_preflight_report(result: PreflightResult) -> None`**
- Uses `rich` to print a formatted table of all checks
- Green ✓ for passed, Red ✗ for failed fatal, Yellow ⚠ for warnings
- Prints summary: "Preflight: X/Y checks passed. Z warnings."
- If any fatal check failed: "PREFLIGHT FAILED. Cannot proceed."

### Acceptance Criteria
- [ ] All 11 checks implemented
- [ ] Fatal failures prevent run from starting
- [ ] Warnings are displayed but don't block
- [ ] All checks run even if early ones fail (no short-circuit)
- [ ] `PreflightResult` contains full details for programmatic access
- [ ] `print_preflight_report()` produces clear, formatted output
- [ ] Secret scan uses configured patterns from config.yaml

### Notes
- Preflight runs BEFORE `RunContext.initialize()` — it validates that initialization will succeed
- The secret scan is intentionally basic (regex grep) — not a full security audit
- Agent command check just verifies the binary exists, not that it's authenticated/working (that's too expensive for preflight)
- This is the user's first interaction with the system — error messages must be helpful and actionable
