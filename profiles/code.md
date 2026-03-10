# Code Evaluation Profile

## Confidence Profile: HIGH

Code has the strongest deterministic signals of any artifact type.
Tests, linters, type-checkers, and complexity analyzers provide
objective, repeatable measurements.

## Hard Gates (pass/fail, non-negotiable)
- **Test pass rate**: all tests must pass (or match baseline pass count)
- **Build success**: project must compile/build without errors
- **Type-check**: if a type-checker was passing at baseline, it must still pass
- **No new lint errors**: lint error count must not increase

## Soft Metrics (scored 0.0–1.0)
- **Lint score delta**: reduction in warnings/errors
- **Cyclomatic complexity delta**: lower average complexity
- **Test coverage delta**: increased line/branch coverage
- **Code duplication**: reduced duplicated blocks
- **Lines of code**: less code for equivalent functionality

## LLM Judge Criteria (supplementary)
- **Readability**: is the code easier to understand?
- **Maintainability**: is it easier to extend or modify?
- **Error handling**: are edge cases and failures handled properly?

## Notes
- For code, deterministic metrics carry ~60% weight, LLM judge ~40%
- Single judge run is sufficient (high confidence from deterministic signals)
- Tool detection is automatic: pytest, ruff, mypy, eslint, tsc
