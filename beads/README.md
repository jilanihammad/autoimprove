# AutoImprove — Beads Tracker

## Overview
23 beads across 5 phases. Each bead is a self-contained unit of work with clear inputs, outputs, dependencies, and acceptance criteria.

## Status Legend
- ⬜ Not started
- 🟡 In progress
- ✅ Complete
- 🔴 Blocked

## Phase Summary

| Phase | Beads | Description | Status |
|-------|-------|-------------|--------|
| 1 | 1–4 | Foundation | ⬜ |
| 2 | 5–10 | Plugin System & Evaluation | ⬜ |
| 3 | 11–15 | Core Loop | ⬜ |
| 4 | 16–19 | Reporting & UX | ⬜ |
| 5 | 20–23 | Expansion & Polish | ⬜ |

## Bead Index

| Bead | Name | Phase | Status | Dependencies |
|------|------|-------|--------|--------------|
| 1 | Project Scaffolding | 1 | ⬜ | None |
| 2 | Git Operations | 1 | ⬜ | None |
| 3 | Run Context | 1 | ⬜ | 1, 2 |
| 4 | Preflight | 1 | ⬜ | 1, 2, 3 |
| 5 | Plugin Contract | 2 | ⬜ | 1 |
| 6 | Code Plugin | 2 | ⬜ | 5 |
| 7 | Policy Enforcement | 2 | ⬜ | 1, 2 |
| 8 | LLM Judge | 2 | ⬜ | 1 |
| 9 | Acceptance Engine | 2 | ⬜ | 5, 6, 7, 8 |
| 10 | Criteria Management | 2 | ⬜ | 1 |
| 11 | Agent Bridge | 3 | ⬜ | 1 |
| 12 | Search Memory | 3 | ⬜ | 1 |
| 13 | Grounding Phase | 3 | ⬜ | 4, 6, 9, 10, 11 |
| 14 | Autonomous Loop | 3 | ⬜ | All Phase 2, 11, 12, 13 |
| 15 | Stop Conditions | 3 | ⬜ | 14 |
| 16 | Experiment Logging | 4 | ⬜ | 14 |
| 17 | Summary Report | 4 | ⬜ | 14, 16 |
| 18 | Terminal Output | 4 | ⬜ | 14, 17 |
| 19 | Merge/Discard CLI | 4 | ⬜ | 2, 3 |
| 20 | Workflow Plugin | 5 | ⬜ | 5, 9 |
| 21 | Document Plugin | 5 | ⬜ | 5, 8, 9 |
| 22 | README + Templates | 5 | ⬜ | All |
| 23 | End-to-End Test | 5 | ⬜ | All |

## Dependency Graph

```
Bead 1 ─┬─ Bead 3 ── Bead 4 ──────────────────── Bead 13 ─── Bead 14 ─┬─ Bead 16 ─── Bead 17 ─── Bead 18
Bead 2 ─┘                                              ↑               │   Bead 19
         ├─ Bead 5 ─── Bead 6 ──┐                      │               └─ Bead 15
         ├─ Bead 7 ─────────────┼─── Bead 9 ───────────┤
         ├─ Bead 8 ─────────────┘                       │
         ├─ Bead 10 ────────────────────────────────────┤
         ├─ Bead 11 ────────────────────────────────────┤
         └─ Bead 12 ────────────────────────────────────┘

Phase 5 (parallel after Phase 3):
Bead 5 + 9 → Bead 20 (workflow plugin)
Bead 5 + 8 + 9 → Bead 21 (document plugin)
All → Bead 22, 23
```

## Files by Bead (quick reference)

See individual bead files in this directory for full details:
- `phase1-beads.md` — Beads 1–4
- `phase2-beads.md` — Beads 5–10
- `phase3-beads.md` — Beads 11–15
- `phase4-beads.md` — Beads 16–19
- `phase5-beads.md` — Beads 20–23
