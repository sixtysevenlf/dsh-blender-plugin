# T13 — AI collaboration docs + human entry refactor

## Scope
Plan §4.8 (all eight files and the language rules), T13 exit criteria, and gate E in §8.5. Documentation
only: no product code, no plan checkbox edits, no branch switch.

## Result
PASS for the T13 deliverables and for gates E1, E2, E3, E5 as far as this worktree can verify them.
E4 is OPEN (documentation PR larger than the 400-line target and no independent reviewer). E6 is PARTIAL
(baseline recorded, four collaboration metrics not instrumented). No gate outside E is claimed.

## Acceptance

| ID | Result | Evidence | Reviewer | Date |
|---|---|---|---|---|
| T13.1 AGENTS.md English, 119 lines, command single source, map, rules, routing, DoD, disclosure | PASS | AGENTS.md, agents-doc.log | author self-check | 2026-10-30 |
| T13.2 docs/adr/README.md with inline template + 4 ADRs (runtime TS, test stack, addon package/release, lib build/release) | PASS | docs/adr/, docs-tree.txt | author self-check | 2026-10-30 |
| T13.3 docs/refactor/spec.md with problem/goals/non-goals/constraints/SHALL/WHEN-THEN/matrix link/rollback/Metrics, every gate traceable | PASS | docs/refactor/spec.md, spec-review.md | author self-check | 2026-10-30 |
| T13.4 PR template with scope, matrix row, test evidence, risk, rollback, disclosure, checklist | PASS | .github/pull_request_template.md | author self-check | 2026-10-30 |
| T13.5 tests/README.md English, 66 lines, three layers, commands, fixture rules | PASS | tests/README.md | author self-check | 2026-10-30 |
| T13.6 README top links zh-README; README.zh-CN.md renamed to zh-README.md with top English link; references updated; no duplicate | PASS | README.md, zh-README.md, docs-bilingual.test.ts, docs-check.log | author self-check | 2026-10-30 |
| T13.7 CONTRIBUTING top zh link; full zh-CONTRIBUTING.md with top English link and zh-README link; no Chinese summary | PASS | CONTRIBUTING.md, zh-CONTRIBUTING.md | author self-check | 2026-10-30 |
| T13.8 delete CLAUDE.md; engineering-principles.md and metrics.md absent (never tracked); merge useful rules into AGENTS/spec; no separate ADR template file | PASS | docs-tree.txt, spec §9, AGENTS §3 | author self-check | 2026-10-30 |
| T13.9 docs contract tests check commands, sections, line limits, cross-links, stale files, links, deleted tools | PASS | tests/unit/js/contract/agents-doc.test.ts, docs-bilingual.test.ts, test.log | author self-check | 2026-10-30 |
| T13.10 evidence pack with raw logs | PASS | this directory | author self-check | 2026-10-30 |

## Gate E state

| Gate | State | Note |
|---|---|---|
| E1 AGENTS rules | PASS | Test lives at `tests/unit/js/contract/agents-doc.test.ts` (allowed by the task and consistent with plan §6.2) rather than the literal `tests/contract/` path in the gate text. |
| E2 decidable spec | PASS (self-check) | spec-review.md; R2 self-check only. |
| E3 AI assets | PASS | 4 ADRs + index + spec + PR template cross-link; no engineering-principles/metrics/CLAUDE. |
| E4 review and size | OPEN | Docs-only diff exceeds 400 lines and 800 lines; needs a Lead exception and an independent reviewer. |
| E5 docs facts | PASS for links/commands/limits/renames/deleted tools | Behavior-matrix rows are owned by T14; C3 stays open. |
| E6 metrics baseline | PARTIAL | Delivery metrics measured from git; review time, rework, flaky, agent first-pass are labelled not instrumented. |

## Residual gaps

1. **Target-state docs.** T4, T6, T7, T8, T9, and T15 are not merged in this worktree. AGENTS.md §2 lists
   the target files with a Current column; ADR 0001/0003/0004 describe the 3.0.0 target and say so. Do not
   read those documents as a description of the current tree.
2. **Behavior matrix.** tests/BEHAVIOR_MATRIX.md is a T14-owned stub (vocabulary + empty rows). C3 and the
   matrix-row half of E5 cannot be verified before T14.
3. **Lint.** `npm run lint` does not exist yet (T5); AGENTS.md marks it planned, and the contract test
   enforces the marker.
4. **lib/ and legacy tests.** `lib/` is still committed and `test:legacy` still exists in this worktree;
   T15/T16 remove them.
5. **Metrics.** Review time, rework rate, flaky rate, and agent first-pass rate need GitHub/CI
   instrumentation; only delivery metrics have measured values.
6. **E4.** The diff size needs a Lead exception; no independent review has happened yet.
7. **Full suite.** `npm test` (pytest) and the E2E Blender layer were not run; this change does not touch
   Python or container behavior. Contract, unit, and typecheck are green.
