# Phase 2: Plugin System & Evaluation — Beads 5–7

---

## Bead 5: Plugin Contract

**Status:** ⬜ Not started
**Dependencies:** Bead 1 (types.py)
**Estimated effort:** Small

### Purpose
Define the abstract base class that all artifact-type evaluator plugins must implement, plus the plugin registry that discovers and loads them. This is the extensibility backbone — every new artifact type is a new plugin.

### Files to Edit

```
src/plugins/base.py       # Full implementation (replace stub)
src/plugins/registry.py   # Full implementation (replace stub)
```

### Detailed Specifications

#### `src/plugins/base.py`

```python
from abc import ABC, abstractmethod
from src.types import (
    Diff, GateResult, SoftEvalResult, ConfidenceProfile,
    BaselineSnapshot, DeltaSummary, PreflightResult
)

class GuardrailConfig:
    """Plugin-specific guardrail rules."""
    protected_patterns: list[str]      # Glob patterns this plugin protects
    max_diff_lines: int | None         # Override global max if set
    forbidden_extensions: list[str]    # File extensions this plugin rejects
    required_files: list[str]          # Files that must exist for this plugin to work

class EvaluatorPlugin(ABC):
    """Abstract contract for artifact-type evaluator plugins."""

    @property
    @abstractmethod
    def name(self) -> str:
        """Plugin identifier, e.g., 'code', 'workflow', 'document'."""

    @property
    @abstractmethod
    def confidence_profile(self) -> ConfidenceProfile:
        """Default evidence quality for this artifact type."""

    @property
    @abstractmethod
    def description(self) -> str:
        """Human-readable description of what this plugin evaluates."""

    @abstractmethod
    def discover_targets(self, paths: list[str], exclude: list[str]) -> list[str]:
        """Find evaluable artifacts in target paths.

        Args:
            paths: Directories/files to search
            exclude: Glob patterns to exclude

        Returns:
            List of file paths this plugin can evaluate.
        """

    @abstractmethod
    def preflight(self, targets: list[str]) -> PreflightResult:
        """Verify tools/deps needed for evaluation are available.

        Example: code plugin checks pytest, ruff are installed.
        Returns PreflightResult with pass/fail and messages.
        """

    @abstractmethod
    def baseline(self, targets: list[str]) -> BaselineSnapshot:
        """Capture current state metrics for all targets.

        This runs once at the start. The snapshot is used for
        before/after comparison in the final report.

        Returns:
            BaselineSnapshot with metric names, values, and metadata.
        """

    @abstractmethod
    def hard_gates(self, diff: Diff, targets: list[str]) -> GateResult:
        """Run deterministic pass/fail checks.

        These are non-negotiable. Any failure = immediate rejection.
        Examples: tests pass, build succeeds, schema valid.

        Returns:
            GateResult with all_passed bool and per-gate details.
        """

    @abstractmethod
    def soft_evaluate(self, diff: Diff, targets: list[str],
                      criteria: dict) -> SoftEvalResult:
        """Run scored evaluation metrics.

        Returns per-metric scores (0.0-1.0) and a composite.
        The criteria dict comes from the versioned criteria file.

        Returns:
            SoftEvalResult with scores dict, has_deterministic flag, composite.
        """

    @abstractmethod
    def summarize_delta(self, baseline: BaselineSnapshot,
                        current: BaselineSnapshot) -> DeltaSummary:
        """Compare baseline to current state for final report.

        Returns human-readable summary of what improved/regressed.
        """

    def guardrails(self) -> GuardrailConfig:
        """Return artifact-specific guardrail rules.

        Default implementation returns empty guardrails.
        Override in plugins that need specific protections.
        """
        return GuardrailConfig(
            protected_patterns=[],
            max_diff_lines=None,
            forbidden_extensions=[],
            required_files=[]
        )
```

#### `src/plugins/registry.py`

```python
class PluginRegistry:
    """Discovers, registers, and retrieves evaluator plugins."""

    _plugins: dict[str, EvaluatorPlugin]  # name -> instance

    def __init__(self):
        self._plugins = {}

    def register(self, plugin: EvaluatorPlugin) -> None:
        """Register a plugin instance. Raises if name already registered."""

    def get(self, name: str) -> EvaluatorPlugin:
        """Get plugin by name. Raises KeyError if not found."""

    def list_plugins(self) -> list[str]:
        """Return list of registered plugin names."""

    def discover_and_register_defaults(self) -> None:
        """Import and register all built-in plugins (code, workflow, document)."""

    def detect_plugins_for_paths(self, paths: list[str],
                                  exclude: list[str]) -> dict[str, list[str]]:
        """Auto-detect which plugins apply to which paths.

        Runs discover_targets() on each registered plugin.
        Returns: {plugin_name: [list of target files]}
        Only includes plugins that found at least one target.
        """
```

### Additional Types Needed in `src/types.py`
Add these if not already present from Bead 1:

```python
@dataclass
class BaselineSnapshot:
    plugin_name: str
    timestamp: str                    # ISO format
    metrics: dict[str, float]         # metric_name -> value
    raw_data: dict                    # Plugin-specific raw data
    targets: list[str]               # Files evaluated

@dataclass
class DeltaSummary:
    plugin_name: str
    improved: dict[str, tuple[float, float]]   # metric -> (before, after)
    regressed: dict[str, tuple[float, float]]
    unchanged: list[str]
    summary_text: str                 # Human-readable paragraph
```

### Acceptance Criteria
- [ ] `EvaluatorPlugin` ABC defined with all 8 methods
- [ ] `GuardrailConfig` dataclass defined
- [ ] `PluginRegistry` can register, retrieve, and list plugins
- [ ] `discover_and_register_defaults()` imports built-in plugins (even if they're stubs)
- [ ] `detect_plugins_for_paths()` correctly maps plugins to paths
- [ ] Attempting to instantiate `EvaluatorPlugin` directly raises `TypeError`
- [ ] `BaselineSnapshot` and `DeltaSummary` types added to `types.py`

### Notes
- The plugin contract is the most important interface in the system — changing it later is expensive
- `criteria: dict` in `soft_evaluate` is intentionally untyped — criteria structure varies by plugin
- `guardrails()` has a default implementation so plugins can opt out
- Plugins are singletons within a run — one instance per plugin type

---

## Bead 6: Code Plugin

**Status:** ⬜ Not started
**Dependencies:** Bead 5 (base.py, registry.py)
**Estimated effort:** Large

### Purpose
Implement the first concrete evaluator plugin for code artifacts. This is the most feature-rich plugin because code has the strongest deterministic signals (tests, lint, type-check, complexity). It serves as the reference implementation for all future plugins.

### Files to Edit

```
src/plugins/code_plugin.py    # Full implementation (replace stub)
```

### Detailed Specifications

#### `src/plugins/code_plugin.py`

**Class: `CodePlugin(EvaluatorPlugin)`**

**Properties:**
- `name` → `"code"`
- `confidence_profile` → `ConfidenceProfile.HIGH`
- `description` → `"Evaluates code quality: tests, lint, complexity, type coverage, build success."`

**Method Implementations:**

1. **`discover_targets(paths, exclude)`**
   - Walks `paths`, finds files matching: `*.py`, `*.js`, `*.ts`, `*.jsx`, `*.tsx`, `*.java`, `*.go`, `*.rs`, `*.rb`, `*.c`, `*.cpp`, `*.h`
   - Excludes files matching `exclude` patterns
   - Returns list of code file paths

2. **`preflight(targets)`**
   - Detects project type from files present:
     - `pyproject.toml` or `setup.py` → Python project
     - `package.json` → Node.js project
     - `Cargo.toml` → Rust project
     - `go.mod` → Go project
   - Checks for available tools based on project type:
     - Python: `pytest` (or `unittest`), `ruff` (or `flake8`), `mypy` (optional)
     - Node: `npm test` / `jest` / `vitest`, `eslint` (optional), `tsc` (optional)
   - Returns `PreflightResult` with which tools are available
   - Missing optional tools = warning, missing test runner = fatal

3. **`baseline(targets)`**
   - Runs all available metrics and captures current state:
     - **Test results**: run test suite, capture pass/fail/skip counts, pass rate
     - **Lint score**: run linter, count errors/warnings, compute score (1.0 - errors/total_lines)
     - **Complexity**: if `radon` available (Python), compute average cyclomatic complexity; otherwise skip
     - **Build/typecheck**: run build command if applicable, capture pass/fail
   - Returns `BaselineSnapshot` with all metrics

4. **`hard_gates(diff, targets)`**
   - Gate 1: **Tests pass** — run test suite, all must pass (or same pass count as baseline)
   - Gate 2: **Build succeeds** — if project has a build step, it must succeed
   - Gate 3: **Type-check passes** — if type-checker available and was passing at baseline
   - Gate 4: **No new lint errors** — lint error count must not increase from accepted state
   - Returns `GateResult` with per-gate pass/fail

5. **`soft_evaluate(diff, targets, criteria)`**
   - Metric 1: **Lint score delta** — did lint warnings decrease?
   - Metric 2: **Complexity delta** — did average complexity decrease?
   - Metric 3: **Test coverage delta** — did coverage increase? (if coverage tool available)
   - Metric 4: **Code duplication** — did duplication decrease? (if tool available)
   - Metric 5: **Lines of code delta** — net change (less code for same functionality = better)
   - Each metric scored 0.0-1.0 relative to baseline
   - `has_deterministic = True` (always, for code)
   - Composite = weighted average using criteria weights

6. **`summarize_delta(baseline, current)`**
   - Compares each metric between baseline and current
   - Generates human-readable summary, e.g.:
     - "Test pass rate: 95% → 98% (+3%)"
     - "Lint errors: 42 → 31 (-26%)"
     - "Avg complexity: 8.2 → 7.1 (-13%)"

7. **`guardrails()`**
   - `protected_patterns`: `["*.lock", "*.min.js", "*.min.css"]`
   - `forbidden_extensions`: `[".exe", ".dll", ".so", ".pyc"]`
   - `required_files`: `[]` (detected dynamically)

**Internal helpers:**

- `_detect_project_type(targets) -> str` — returns "python", "node", "rust", "go", "unknown"
- `_run_tests(worktree_path) -> TestResult` — runs appropriate test command, parses output
- `_run_linter(worktree_path) -> LintResult` — runs linter, parses output
- `_run_typecheck(worktree_path) -> TypecheckResult` — runs type-checker if available
- `_compute_complexity(targets) -> float` — average cyclomatic complexity

**Dataclasses (internal to this module):**
```python
@dataclass
class TestResult:
    passed: int
    failed: int
    skipped: int
    errors: int
    pass_rate: float        # passed / (passed + failed)
    output: str             # Raw test output

@dataclass
class LintResult:
    errors: int
    warnings: int
    score: float            # 1.0 - (errors / total_lines)
    output: str

@dataclass
class TypecheckResult:
    passed: bool
    errors: int
    output: str
```

### Acceptance Criteria
- [ ] Plugin implements all 7 abstract methods from `EvaluatorPlugin`
- [ ] `discover_targets` finds code files across common languages
- [ ] `preflight` detects project type and available tools
- [ ] `baseline` captures test results, lint score, and complexity
- [ ] `hard_gates` runs tests + build + typecheck + lint check
- [ ] `soft_evaluate` returns scored metrics with `has_deterministic=True`
- [ ] Plugin registers correctly with `PluginRegistry`
- [ ] Graceful degradation: if a tool isn't available, skip that metric (don't crash)

### Notes
- Start with Python project support as the primary path — it's the most common for the target user
- Node.js support is secondary but important (many workflows are JS/TS)
- Tool detection should use `shutil.which()` — don't assume tools are installed
- Test runner detection order: check for `pytest.ini`/`pyproject.toml [tool.pytest]` first, then fall back to `python -m pytest`, then `python -m unittest`
- All subprocess calls should have timeouts (use `config.agent_timeout_seconds` as a reasonable default)
- Capture stdout/stderr from all tool runs for debugging — store in raw_data

---

## Bead 7: Policy Enforcement

**Status:** ⬜ Not started
**Dependencies:** Bead 1 (config.py, types.py), Bead 2 (git_ops.py)
**Estimated effort:** Medium

### Purpose
Implement the guardrail layer that validates every candidate diff BEFORE it reaches the evaluation engine. This is the safety net — it catches policy violations that should result in immediate rejection without wasting time on evaluation.

### Files to Edit

```
src/policy.py    # Full implementation (replace stub)
```

### Detailed Specifications

#### `src/policy.py`

**Dataclass: `PolicyResult`**
```python
@dataclass
class PolicyViolation:
    rule: str               # e.g., "protected_path", "diff_size", "secret_detected"
    severity: str           # "fatal" or "warning"
    file: str | None        # File that triggered the violation
    line: int | None        # Line number if applicable
    message: str            # Human-readable description

@dataclass
class PolicyResult:
    passed: bool                        # True if no fatal violations
    violations: list[PolicyViolation]   # All violations found
    fatal_count: int
    warning_count: int
```

**Function: `check_policy(diff: Diff, config: Config, plugin_guardrails: GuardrailConfig | None = None) -> PolicyResult`**

Runs all policy checks against the candidate diff. Does NOT short-circuit — reports all violations.

**Policy Checks:**

1. **`check_diff_size(diff, config)`** (fatal)
   - `diff.lines_added + diff.lines_removed > config.max_diff_lines` → violation
   - Also check plugin-specific override from `plugin_guardrails.max_diff_lines`
   - Message: "Diff exceeds size limit: {actual} lines changed (max: {limit})"

2. **`check_protected_paths(diff, config, plugin_guardrails)`** (fatal)
   - Check each file in `diff.files_changed` against:
     - `config.protected_paths` (global)
     - `plugin_guardrails.protected_patterns` (plugin-specific)
   - Uses `fnmatch` for glob matching
   - Message: "Protected path modified: {file} (matches pattern: {pattern})"

3. **`check_forbidden_extensions(diff, plugin_guardrails)`** (fatal)
   - Check file extensions against `plugin_guardrails.forbidden_extensions`
   - Message: "Forbidden file type: {file} (extension: {ext})"

4. **`check_secret_patterns(diff, config)`** (fatal)
   - Scan the raw diff text (added lines only, lines starting with `+`) for patterns in `config.secret_patterns`
   - Default patterns:
     - `AKIA[0-9A-Z]{16}` (AWS access key)
     - `[a-zA-Z0-9/+=]{40}` preceded by common secret variable names
     - `-----BEGIN (RSA |EC |DSA )?PRIVATE KEY-----`
     - `ghp_[a-zA-Z0-9]{36}` (GitHub personal access token)
     - `sk-[a-zA-Z0-9]{48}` (OpenAI API key)
   - Only scan lines added (starting with `+` in diff), not removed lines
   - Message: "Potential secret detected in {file}: matches pattern '{pattern}'"

5. **`check_dependency_changes(diff, config)`** (fatal if `allow_dependency_changes=False`)
   - Check if any of these files are in `diff.files_changed`:
     - `package.json`, `package-lock.json`, `yarn.lock`, `pnpm-lock.yaml`
     - `requirements.txt`, `Pipfile`, `Pipfile.lock`, `pyproject.toml` (deps section)
     - `Cargo.toml`, `Cargo.lock`
     - `go.mod`, `go.sum`
     - `Gemfile`, `Gemfile.lock`
   - If `config.allow_dependency_changes` is False → fatal violation
   - Message: "Dependency file modified: {file}. Set allow_dependency_changes=true to permit."

6. **`check_excluded_paths(diff, config)`** (fatal)
   - Check if any changed files are in `config.exclude_paths`
   - Message: "File in excluded path was modified: {file}"

7. **`check_empty_diff(diff)`** (fatal)
   - If diff has no changes at all → violation
   - Message: "Empty diff — no changes were made."

### Acceptance Criteria
- [ ] All 7 policy checks implemented
- [ ] `PolicyResult` correctly categorizes fatal vs warning violations
- [ ] Secret scanning only checks added lines (not removed)
- [ ] Protected path matching uses glob patterns correctly
- [ ] Dependency file detection covers Python, Node, Rust, Go, Ruby
- [ ] Plugin-specific guardrails merge with global config
- [ ] All violations reported (no short-circuit)
- [ ] Default secret patterns catch common key formats

### Notes
- Policy check runs BEFORE hard gates — it's cheaper and catches obvious problems
- Secret scanning is regex-based and will have false positives — that's acceptable (better safe than sorry)
- The `+` line detection in diffs must handle the diff format correctly (skip `+++` header lines)
- Policy violations are logged in the experiment log with full details
- This module has no LLM dependency — it's purely deterministic
