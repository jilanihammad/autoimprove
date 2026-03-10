# Document Evaluation Profile

## Confidence Profile: LOW

Documents, spreadsheets, and presentations have minimal deterministic
signals. Evaluation relies primarily on LLM judgment. The system is
honest about this — confidence scores are lower, and more judge runs
are used to compensate.

## Hard Gates (pass/fail, non-negotiable)
- **Parseable**: file can be read without errors
- **Structure intact**: no corruption (e.g., no circular refs in spreadsheets)
- **Not empty**: file must not be emptied

## Soft Metrics (limited deterministic signals)
- **Word count delta**: conciseness without content loss
- **Structure score**: heading count, section organization
- **Readability score**: Flesch-Kincaid grade level (text documents only)

## LLM Judge Criteria (primary evaluation method)
- **Clarity**: writing is clear and unambiguous
- **Completeness**: all necessary information is present
- **Structure**: well-organized with logical flow
- **Audience fit**: appropriate for the target audience
- **Actionability**: reader knows what to do next

## Notes
- LLM judge carries ~80% weight, deterministic ~20%
- 3+ judge runs required; scores aggregated with variance tracking
- High judge variance triggers confidence penalty
- Acceptance threshold is lower (0.3) to account for inherent subjectivity
- Spreadsheet evaluation focuses on structure, naming, formula clarity
- Presentation evaluation focuses on slide flow, text clarity, visual structure
