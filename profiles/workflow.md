# Workflow Evaluation Profile

## Confidence Profile: MEDIUM

Workflows have a mix of deterministic and judgment-based signals.
Schema validation and structural checks are objective; architecture
quality and error handling design require LLM judgment.

## Hard Gates (pass/fail, non-negotiable)
- **Parseable**: JSON/YAML must be valid
- **Schema valid**: required structure present (nodes+connections for n8n, States for Step Functions)
- **No orphaned nodes**: all nodes/states must be connected
- **Lambda syntax valid**: handler files must parse without errors

## Soft Metrics (scored 0.0–1.0)
- **Error handling coverage**: percentage of nodes/states with error handling
- **Complexity delta**: fewer unnecessary nodes/states
- **Node configuration completeness**: all required fields populated

## LLM Judge Criteria (primary for quality assessment)
- **Error handling quality**: are failures handled gracefully?
- **Efficiency**: minimal unnecessary steps?
- **Reliability**: handles edge cases and transient failures?
- **Readability**: workflow is easy to understand?
- **Security**: no exposed secrets or unsafe operations?

## Notes
- Deterministic metrics carry ~40% weight, LLM judge ~60%
- 2 judge runs recommended for variance detection
- n8n workflows validated structurally; Step Functions checked for Catch/Retry blocks
