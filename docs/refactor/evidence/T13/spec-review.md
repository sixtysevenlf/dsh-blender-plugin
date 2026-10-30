# T13 — E2 specification review (self-check)

Reviewer: author (self-check). Gate E2 is R2, so this is the author check; a reviewer samples it later.
Date: 2026-10-30.

## What was checked

1. The specification has every section E2 names: problem, goals, non-goals, constraints, SHALL clauses,
   WHEN/THEN acceptance scenarios, a behavior-matrix link, rollback, and Metrics (sections 1-9).
2. Every plan §8 gate ID has one normative clause in section 5: A1-A8, B1-B7, C1-C8, D1-D6, E1-E6, F1-F4.
3. Every clause states a binary condition and names its evidence, so a reviewer can decide met/not met
   without asking the author.
4. Section 6 turns the clauses into WHEN/THEN review scripts (S1-S12).
5. Section 10 maps every clause group back to the plan gate and the evidence artifact.
6. The Metrics section records a git-derived baseline and marks the four collaboration metrics that are
   not instrumented offline instead of inventing values.

## Traceability

| Plan gate | Spec clause | Evidence expected | State at T13 |
|---|---|---|---|
| A1-A8 | 5.1 A1-A8 | tool/command/route/protocol logs, compat diff | open (T10/T14/T16) |
| B1-B7 | 5.2 B1-B7 | structure report, build/typecheck/parity, pack list | open (T4-T9, T15, T16) |
| C1-C8 | 5.3 C1-C8 | layer logs, CI log, matrix report, coverage | partially open (C1 layers exist; T14/T11/T12) |
| D1-D6 | 5.4 D1-D6 | clean env, tarball, install migration, release | open (T16) |
| E1 | 5.5 E1 | agents-doc.log | met for AGENTS, except gate text names `tests/contract/agents-doc.test.ts` while the file lives at `tests/unit/js/contract/agents-doc.test.ts` (plan §6.2 puts contract tests inside unit/integration, and the task allowed either location) |
| E2 | 5.5 E2 | this file | self-checked, decidable |
| E3 | 5.5 E3 | docs-tree.txt | met (4 ADRs + index + spec + PR template, no separate files) |
| E4 | 5.5 E4 | review.md + PR | open: this documentation PR exceeds the 400-line target and the 800-line split threshold; needs a Lead exception (plan §8.5 E4) and an independent reviewer |
| E5 | 5.5 E5 | docs-check.log | met for links, commands, line limits, renames, and deleted-tool references; matrix rows are T14's |
| E6 | 5.5 E6 | spec §9 | baseline recorded; review time, rework, flaky, and first-pass are labelled not instrumented |

## Honest limits

- The specification describes the 3.0.0 target. Most clauses are not met in this worktree, and the
  document says so at the top; this review does not claim otherwise.
- The architecture map in AGENTS.md is target-state for T4/T6/T7/T8/T9/T15. Its Current column states
  what exists today (src/index.ts 2,888 lines, runtime/engine.mjs 3,189, runtime/server.mjs 1,009,
  16 .mjs, 40 .py, addon 7,515 lines, lib/ committed).
- The behavior matrix is a T14-owned stub with fixed vocabulary; C3 cannot pass until T14 fills it.
