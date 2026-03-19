# AutoImprove Program

## Project Context
Python CLI tool (~5000 LOC) that autonomously improves code, workflows, and documents.
Multi-agent architecture: indexer → analyst → coder → reviewer, orchestrated by a Python loop.
Key modules:
- `src/orchestrator.py` + `src/multi_orchestrator.py` — core loop and multi-agent pipeline
- `src/agents/` — 4 specialized LLM agents (indexer, analyst, coder, reviewer)
- `src/eval/` — acceptance engine, LLM judge, search memory, eval anchors
- `src/plugins/` — artifact-type evaluators (code, document, workflow)
- `src/cli.py` — Click-based CLI (run, merge, discard, status, calibrate)
- `src/policy.py` — guardrails applied to every candidate diff
- `src/run_context.py` — run lifecycle, state persistence, crash recovery

Tech stack: Python 3.10+, Pydantic, Click, Rich, PyYAML. No external LLM SDKs — agents invoked via subprocess CLI.

## Improvement Goals
- Reduce duplicated logic across the 3 plugins (code, document, workflow share boilerplate in baseline/hard_gates/summarize_delta)
- Improve robustness of JSON parsing from agent output (agents frequently return malformed JSON)
- Add better error messages when agent invocations fail (currently just prints truncated stderr)
- Reduce complexity in `src/orchestrator.py` (the single-agent `run_autonomous_loop` is 100+ lines of deeply nested logic)
- Improve type annotations — several functions use `dict` where a typed dataclass would be clearer

## Constraints
- Do not modify the `EvaluatorPlugin` abstract base class interface (other plugins depend on it)
- Do not change CLI command signatures (users have scripts depending on them)
- Do not change `config.yaml` schema (backward compatibility)
- Do not modify test files
- Do not add new external dependencies
- Preserve all public function signatures in `src/git_ops.py`

## Artifact Types
- code
