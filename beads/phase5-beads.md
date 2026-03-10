# Phase 5: Expansion & Polish — Beads 20–23

---

## Bead 20: Workflow Plugin

**Status:** ⬜ Not started
**Dependencies:** Bead 5 (plugin contract), Bead 9 (acceptance engine)
**Estimated effort:** Medium-Large

### Purpose
Implement the evaluator plugin for AI workflow artifacts — both n8n workflows (JSON-based) and custom workflows (AWS Lambda + Step Functions, etc.). This plugin has a mixed confidence profile: some things can be validated deterministically (schema, node config), others require LLM judgment (error handling quality, architecture).

### Files to Edit

```
src/plugins/workflow_plugin.py    # Full implementation (replace stub)
```

### Detailed Specifications

#### `src/plugins/workflow_plugin.py`

**Class: `WorkflowPlugin(EvaluatorPlugin)`**

**Properties:**
- `name` → `"workflow"`
- `confidence_profile` → `ConfidenceProfile.MEDIUM`
- `description` → `"Evaluates AI workflow artifacts: n8n workflows, Step Functions, Lambda-based pipelines."`

**Method Implementations:**

1. **`discover_targets(paths, exclude)`**
   - Finds workflow-related files:
     - `*.json` files that contain n8n workflow structure (check for `"nodes"` and `"connections"` keys)
     - `*.yaml` / `*.yml` files that look like Step Functions definitions (check for `"States"` key)
     - `*.json` files matching `*workflow*`, `*step-function*`, `*state-machine*` patterns
     - Lambda handler files: `*.py`, `*.js`, `*.ts` in directories named `lambda/`, `functions/`, `handlers/`
   - Returns list of discovered workflow files

2. **`preflight(targets)`**
   - For n8n workflows: check if `n8n` CLI or validation tools are available (optional)
   - For Step Functions: check if `aws` CLI is available (optional, for `stepfunctions validate-state-machine`)
   - For Lambda: check if relevant runtime tools are available (python, node)
   - Most checks are optional (warnings, not fatal) — workflow evaluation relies heavily on LLM judge

3. **`baseline(targets)`**
   - For each workflow file, capture:
     - **Schema validity**: is the JSON/YAML parseable and structurally valid?
     - **Node count**: number of nodes/states
     - **Connection count**: number of connections/transitions
     - **Error handling coverage**: % of nodes/states with error handling configured
       - n8n: check for `continueOnFail`, error output connections
       - Step Functions: check for `Catch` and `Retry` blocks
     - **Complexity score**: based on node count, branching, nesting depth
   - Returns `BaselineSnapshot` with all metrics

4. **`hard_gates(diff, targets)`**
   - Gate 1: **JSON/YAML parseable** — modified workflow files must be valid JSON/YAML
   - Gate 2: **Schema valid** — n8n workflows must have `nodes` array and `connections` object; Step Functions must have `States` object
   - Gate 3: **No orphaned nodes** — all nodes must be connected (no floating nodes)
   - Gate 4: **Lambda syntax valid** — if Lambda handlers modified, they must be parseable (no syntax errors)
   - Returns `GateResult`

5. **`soft_evaluate(diff, targets, criteria)`**
   - Metric 1: **Error handling coverage delta** — did error handling improve?
   - Metric 2: **Complexity delta** — did workflow get simpler (fewer unnecessary nodes)?
   - Metric 3: **Node configuration completeness** — are all required fields populated?
   - `has_deterministic = True` (for schema/structure metrics)
   - Note: most meaningful evaluation comes from LLM judge (architecture, reliability, security)

6. **`summarize_delta(baseline, current)`**
   - Compares workflow metrics between baseline and current
   - Example: "Error handling coverage: 60% → 85% (+25%). Node count: 12 → 10 (-2 unnecessary nodes removed)."

7. **`guardrails()`**
   - `protected_patterns`: `["*.tfstate", "*.terraform*"]` (infrastructure state files)
   - `forbidden_extensions`: `[".zip", ".jar"]` (deployment artifacts)

**Internal helpers:**

- `_detect_workflow_type(file_path: str) -> str` — returns "n8n", "step_functions", "lambda", "unknown"
- `_validate_n8n_schema(content: dict) -> tuple[bool, list[str]]` — validates n8n workflow structure
- `_validate_step_functions_schema(content: dict) -> tuple[bool, list[str]]` — validates SF structure
- `_count_error_handling(content: dict, workflow_type: str) -> float` — returns coverage ratio 0.0-1.0
- `_compute_workflow_complexity(content: dict) -> float` — based on nodes, branches, nesting

### Acceptance Criteria
- [ ] Plugin discovers n8n workflows, Step Functions definitions, and Lambda handlers
- [ ] Baseline captures schema validity, node count, error handling coverage, complexity
- [ ] Hard gates catch invalid JSON/YAML, broken schema, orphaned nodes
- [ ] Soft evaluation measures error handling and complexity deltas
- [ ] Graceful handling when validation tools aren't available (fall back to basic checks)
- [ ] Plugin registers correctly with PluginRegistry

### Notes
- Workflow evaluation is inherently mixed: structure is deterministic, quality is judgment-based
- The LLM judge handles the subjective aspects (is this workflow well-designed? secure? maintainable?)
- n8n workflow validation can leverage the n8n MCP tools if available in the environment
- Step Functions validation is best-effort without AWS credentials — focus on structural checks
- Lambda handler evaluation overlaps with the code plugin — for files that are both Lambda handlers AND code, the workflow plugin focuses on integration aspects while the code plugin handles code quality

---

## Bead 21: Document Plugin

**Status:** ⬜ Not started
**Dependencies:** Bead 5 (plugin contract), Bead 8 (LLM judge), Bead 9 (acceptance engine)
**Estimated effort:** Medium

### Purpose
Implement the evaluator plugin for document artifacts: Word docs, Markdown files, spreadsheets (Excel/CSV), and presentations (PowerPoint). This plugin has a LOW confidence profile — evaluation is primarily LLM-judgment-based with minimal deterministic signals.

### Files to Edit

```
src/plugins/document_plugin.py    # Full implementation (replace stub)
```

### Detailed Specifications

#### `src/plugins/document_plugin.py`

**Class: `DocumentPlugin(EvaluatorPlugin)`**

**Properties:**
- `name` → `"document"`
- `confidence_profile` → `ConfidenceProfile.LOW`
- `description` → `"Evaluates documents, spreadsheets, and presentations. Primarily LLM-judgment-based."`

**Method Implementations:**

1. **`discover_targets(paths, exclude)`**
   - Finds document files:
     - Markdown: `*.md`, `*.mdx`
     - Word: `*.docx` (requires `python-docx` for reading)
     - Excel: `*.xlsx`, `*.xls`, `*.csv` (requires `openpyxl` for reading)
     - PowerPoint: `*.pptx` (requires `python-pptx` for reading)
     - Plain text: `*.txt`, `*.rst`
   - Excludes: `README.md` in root (usually project meta, not a work document), `CHANGELOG.md`, `LICENSE`
   - Returns list of document file paths

2. **`preflight(targets)`**
   - Check for optional libraries based on file types found:
     - `.docx` files → check `python-docx` importable
     - `.xlsx` files → check `openpyxl` importable
     - `.pptx` files → check `python-pptx` importable
   - Missing libraries = warning (can still evaluate markdown/text files)
   - No fatal checks — documents can always be evaluated at some level

3. **`baseline(targets)`**
   - For each document, capture:
     - **Parseable**: can the file be read without errors?
     - **Word count**: total words
     - **Structure score**: heading count, section count, list count (for structured docs)
     - **Readability score**: if text-based, compute Flesch-Kincaid or similar
     - **For spreadsheets**: row count, column count, formula count, named range count
     - **For presentations**: slide count, total text length, image count
   - Returns `BaselineSnapshot`

4. **`hard_gates(diff, targets)`**
   - Gate 1: **Parseable** — modified files must still be parseable (valid markdown, valid XLSX, etc.)
   - Gate 2: **Structure intact** — for spreadsheets: no circular references introduced; for presentations: slide count didn't drop to 0
   - Gate 3: **Not empty** — file must not be emptied
   - These are minimal gates — most evaluation is soft/judgment
   - Returns `GateResult`

5. **`soft_evaluate(diff, targets, criteria)`**
   - Metric 1: **Word count delta** — significant reduction without content loss = good (conciseness)
   - Metric 2: **Structure score delta** — better headings, sections, organization
   - Metric 3: **Readability delta** — improved readability score
   - `has_deterministic = False` for most documents (readability score is the only deterministic signal)
   - Note: the heavy lifting is done by the LLM judge, not these metrics

6. **`summarize_delta(baseline, current)`**
   - Example: "Word count: 2,450 → 2,100 (-14%, more concise). Structure: added 3 section headings. Readability: grade 12 → grade 10 (more accessible)."

7. **`guardrails()`**
   - `protected_patterns`: `[]` (no default protections for documents)
   - `forbidden_extensions`: `[".pdf"]` (PDFs can't be meaningfully edited by agents)

**Internal helpers:**

- `_detect_document_type(file_path: str) -> str` — returns "markdown", "docx", "xlsx", "pptx", "text", "csv"
- `_read_document_text(file_path: str) -> str` — extracts text content regardless of format
- `_compute_readability(text: str) -> float` — Flesch-Kincaid grade level
- `_compute_structure_score(text: str) -> float` — based on headings, lists, sections
- `_analyze_spreadsheet(file_path: str) -> dict` — row/col/formula counts

### Confidence Implications

This plugin has `ConfidenceProfile.LOW`, which means:
- The acceptance engine will run the LLM judge 3+ times (per config)
- Confidence starts at 0.3 base
- Without deterministic signals agreeing, confidence stays low
- The acceptance threshold for documents is lower (0.3 vs 0.6 for code)
- This is by design — the system is honest that document evaluation is less reliable

### Acceptance Criteria
- [ ] Plugin discovers markdown, docx, xlsx, pptx, csv, txt files
- [ ] Baseline captures word count, structure, readability for text docs
- [ ] Baseline captures row/col/formula counts for spreadsheets
- [ ] Hard gates are minimal but catch corruption (unparseable, empty)
- [ ] Soft evaluation provides basic deterministic signals where possible
- [ ] `has_deterministic` correctly set to False for most document types
- [ ] Graceful degradation when optional libraries not installed
- [ ] Plugin registers correctly with PluginRegistry

### Notes
- Documents are the hardest artifact type to evaluate autonomously — be honest about this
- The LLM judge does most of the work here — the plugin provides structure and basic metrics
- For spreadsheets, the agent can modify formulas, naming, structure — but evaluating whether a financial model is "better" is deeply subjective
- Consider: for v1, spreadsheet and presentation support may be limited to basic structural checks + LLM judge. That's okay.
- The `_read_document_text` helper is critical — it must handle all formats and extract clean text for the judge

---

## Bead 22: README + Templates

**Status:** ⬜ Not started
**Dependencies:** All previous beads
**Estimated effort:** Medium

### Purpose
Write comprehensive documentation: README with setup/usage/examples, and template `program.md` files for common use cases. This is what makes the tool usable by someone who didn't build it.

### Files to Create/Edit

```
README.md                           # Full project documentation
program.md                          # Update template with real examples
profiles/code.md                    # Update with real evaluation details
profiles/workflow.md                # Update with real evaluation details
profiles/document.md                # Update with real evaluation details
examples/
├── program-code-quality.md         # Example program.md for code quality improvement
├── program-refactoring.md          # Example program.md for refactoring
├── program-workflow-optimization.md # Example program.md for workflow improvement
├── program-document-improvement.md  # Example program.md for document improvement
└── config-examples/
    ├── config-aggressive.yaml      # Fast iteration, lower thresholds
    ├── config-conservative.yaml    # Careful, high thresholds
    └── config-overnight.yaml       # Long budget, balanced settings
```

### README Sections

1. **What is AutoImprove** — one-paragraph summary
2. **How it works** — the core loop explained simply, with diagram
3. **Quick start** — 5 commands to get running
4. **Requirements** — Python 3.10+, uv, git, a coding agent
5. **Configuration** — config.yaml reference with all fields explained
6. **Writing program.md** — how to write effective improvement instructions
7. **Artifact types** — what's supported, confidence profiles, what to expect
8. **Commands** — `run`, `merge`, `discard`, `status`
9. **Understanding results** — how to read the summary report
10. **FAQ** — common questions
11. **Architecture** — brief overview for contributors

### Acceptance Criteria
- [ ] README covers all sections listed above
- [ ] Quick start works end-to-end (tested)
- [ ] At least 3 example program.md files for different use cases
- [ ] At least 3 example config.yaml files for different strategies
- [ ] Profile files updated with real evaluation details
- [ ] All commands documented with examples

---

## Bead 23: End-to-End Test

**Status:** ⬜ Not started
**Dependencies:** All previous beads
**Estimated effort:** Medium

### Purpose
Run a full end-to-end test of AutoImprove against a sample code project. This validates the entire system works: preflight → grounding → autonomous loop → reporting → merge/discard. Catches integration issues that unit tests miss.

### Files to Create

```
tests/
├── e2e/
│   ├── test_full_run.py            # End-to-end test script
│   └── sample_project/             # A small Python project to improve
│       ├── main.py                 # Intentionally imperfect code
│       ├── utils.py                # Has lint issues, complexity
│       ├── test_main.py            # Basic tests (some passing)
│       └── pyproject.toml          # Project config
```

### Test Scenarios

1. **Happy path: code improvement run**
   - Set up sample project with known issues (lint errors, high complexity, missing error handling)
   - Run AutoImprove with `grounding_mode: auto`, `time_budget_minutes: 5` (short for testing)
   - Verify:
     - Preflight passes
     - Baseline captured
     - At least 1 iteration runs
     - Experiment log has entries
     - Summary report generated
     - Git tags created
     - Worktree exists and is isolated from main branch

2. **Preflight failure**
   - Run in a non-git directory
   - Verify preflight fails with clear error

3. **Policy violation**
   - Configure protected paths, then manually create a diff that violates
   - Verify policy check catches it

4. **Merge and discard**
   - After a successful run, test `autoimprove merge <run_id>`
   - After another run, test `autoimprove discard <run_id>`
   - Verify git state is correct after each

5. **Crash recovery**
   - Start a run, kill it mid-iteration
   - Verify state files exist and are consistent
   - Verify worktree can be cleaned up

### Sample Project Design

`main.py` — intentionally has:
- No error handling on file operations
- Deeply nested if/else (high complexity)
- Unused imports
- No type hints
- Magic numbers

`utils.py` — intentionally has:
- Duplicated code
- Poor variable names
- No docstrings
- Lint warnings (unused variables, bare except)

`test_main.py` — has:
- 3 passing tests
- 1 skipped test
- Basic coverage

This gives the agent clear improvement targets while having a test suite for hard gates.

### Acceptance Criteria
- [ ] Happy path test completes without errors
- [ ] At least 1 iteration runs and produces a decision
- [ ] Experiment log, summary report, and git tags all created
- [ ] Preflight failure test catches non-git directory
- [ ] Merge and discard commands work correctly
- [ ] Sample project is small enough for fast iteration (< 200 lines total)

### Notes
- The e2e test uses a real agent invocation — it requires a configured agent (claude/codex/kiro)
- For CI, consider a mock agent that makes deterministic changes
- The sample project should be realistic enough to trigger meaningful evaluation but small enough for fast iteration
- Time budget for testing should be very short (5 minutes max)
- This bead is the final validation that everything works together
