# AutoImprove — Beads Reference

> **⚠️ `bd` is the source of truth for task tracking, status, and dependencies.**
> Use `bd ready` to see what's unblocked, `bd list --all` for full status, `bd show <id>` for details.
> The markdown files in this directory are supplementary reference specs only — do not update status here.

## Overview
23 beads across 5 phases. Each bead is a self-contained unit of work with clear inputs, outputs, dependencies, and acceptance criteria.

## Phase Summary

| Phase | Beads | Description | Status |
|-------|-------|-------------|--------|
| 1 | 1–4 | Foundation | ⬜ |
| 2 | 5–10 | Plugin System & Evaluation | ⬜ |
| 3 | 11–15 | Core Loop | ⬜ |
| 4 | 16–19 | Reporting & UX | ⬜ |
| 5 | 20–23 | Expansion & Polish | ⬜ |

## Quick Reference

```bash
bd ready                        # What can I work on now?
bd list --all                   # Full status of all beads
bd show autoimprove-i2l.N       # Details for bead N
bd update autoimprove-i2l.N --claim   # Claim a bead to start work
bd close autoimprove-i2l.N      # Mark bead complete
```

## Bead Index

| Bead | bd ID | Name | Phase |
|------|-------|------|-------|
| 1 | autoimprove-i2l.1 | Project Scaffolding | 1 |
| 2 | autoimprove-i2l.2 | Git Operations | 1 |
| 3 | autoimprove-i2l.3 | Run Context | 1 |
| 4 | autoimprove-i2l.4 | Preflight | 1 |
| 5 | autoimprove-i2l.5 | Plugin Contract | 2 |
| 6 | autoimprove-i2l.6 | Code Plugin | 2 |
| 7 | autoimprove-i2l.7 | Policy Enforcement | 2 |
| 8 | autoimprove-i2l.8 | LLM Judge | 2 |
| 9 | autoimprove-i2l.9 | Acceptance Engine | 2 |
| 10 | autoimprove-i2l.10 | Criteria Management | 2 |
| 11 | autoimprove-i2l.11 | Agent Bridge | 3 |
| 12 | autoimprove-i2l.12 | Search Memory | 3 |
| 13 | autoimprove-i2l.13 | Grounding Phase | 3 |
| 14 | autoimprove-i2l.14 | Autonomous Loop | 3 |
| 15 | autoimprove-i2l.15 | Stop Conditions | 3 |
| 16 | autoimprove-i2l.16 | Experiment Logging | 4 |
| 17 | autoimprove-i2l.17 | Summary Report | 4 |
| 18 | autoimprove-i2l.18 | Terminal Output | 4 |
| 19 | autoimprove-i2l.19 | Merge/Discard CLI | 4 |
| 20 | autoimprove-i2l.20 | Workflow Plugin | 5 |
| 21 | autoimprove-i2l.21 | Document Plugin | 5 |
| 22 | autoimprove-i2l.22 | README + Templates | 5 |
| 23 | autoimprove-i2l.23 | End-to-End Test | 5 |

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

## Detailed Specs (supplementary reference)

See individual bead files in this directory for full implementation details:
- `phase1-beads.md` — Beads 1–4 (Foundation)
- `phase2a-beads.md` — Beads 5–7 (Plugin Contract, Code Plugin, Policy)
- `phase2b-beads.md` — Beads 8–10 (LLM Judge, Acceptance Engine, Criteria)
- `phase3-beads.md` — Beads 11–15 (Agent Bridge, Search Memory, Grounding, Loop, Stop Conditions)
- `phase4-beads.md` — Beads 16–19 (Logging, Reports, Terminal, Merge/Discard)
- `phase5-beads.md` — Beads 20–23 (Workflow Plugin, Document Plugin, README, E2E Test)
