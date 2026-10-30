# Section 8 gate audit

**This file is the Lead pre-audit. The independent non-author review in
`independent-review.md` is authoritative wherever the two differ.** The
independent review was written after this table's first draft, found several
overstated claims (C6 doctor/export, A3, A1, A8), and its statuses are the ones
recorded below.

- Plan authority: `D:/DSH/dsh-blender-plugin-refactor-plan-v3.md` §8.
- Tree: branch `refactor`, HEAD `bc76135` plus the A1 failure-matrix test and
  the T16 D3 correction (no release tag).
- Independent review: `independent-review.md`, 2026-10-30 22:19–22:30 +08:00,
  non-author reviewer, no product/plan/tag/release changes.
- Environment: Windows 11, Node v22.17.0, npm 10.9.2, Python 3.11
  (`C:/Users/villa/e2e/dsh-venv`), Blender 4.2.10
  (`C:/Users/villa/e2e/blender-4.2.10`).

## Status summary (independent review)

| Status | Gates |
|---|---|
| PASS (19) | A2 A3 A4 B1 B2 B3 B6 C1 C2 C3 C4 C7 C8 D1 D6 E1 E2 E3 E5 |
| PARTIAL (8) | A5 A6 A7 B4 B7 C6 D2 D3 |
| FAIL (8) | A1 A8 C5 E4 E6 F1 F2 F3 |
| PENDING (3) | B5 D4 F4 |
| BLOCKED (1) | D5 |

Release verdict: **BLOCKED**. C5 alone blocks release under §8.0, and issue #52
is not a valid EXEMPT (only platform/version conditions qualify). F1 cannot be
PASS; F4 cannot be signed.

## Gate status

| Gate | Status | Evidence | Remaining gap |
|---|---|---|---|
| A1 15 tools × 7 aspects | FAIL | `logs/a1-tool-execution.log` (15 success), `logs/a1-unreachable-matrix.log` (closed-backend matrix added after the review), `tool-schema.diff` (empty vs v2.6.2), `contract.log` | No per-tool param-error/timeout-abort/session-headless matrix; `tool-matrix.json` copies BEHAVIOR_MATRIX statuses |
| A2 family/op contract | PASS | `catalog-report.json` (34/228, fingerprint, lockMatchesCommitted), `catalog-lock.diff` (empty), `contract/catalog.test.ts`, `single-source.test.ts` | Lock stores counts, not a content hash (minor) |
| A3 39 addon commands | PASS | `T10/command-matrix.md` + 138 parametrized matrix tests, `gates/command-matrix.json` (39), `logs/a3-command-contract.log` | The 138 tests monkeypatch recorders; real bpy bodies only via E2E |
| A4 protocol 11 | PASS | `logs/a4-protocol-11.log` (16 tests), `protocol-info.json` (11 / bl_info 1.11) | Artifact name differs from gate text |
| A5 29 HTTP routes | PARTIAL | `logs/a5-routes.log`, `route-matrix.json` (29/29) | No per-route normal/invalid/error no-500 record; matrix statuses derived |
| A6 surface_fit honesty | PARTIAL | `logs/a6-surface-fit.log`, `-python.log` | Tests assert source/AST, do not execute `surface_fit` and assert `ok:false` + reason + next_action |
| A7 v2.6.2↔3.0.0 compat | PARTIAL | `compat.diff` (only declared plugin 2.6.2→3.0.0 / addon 1.10→1.11), `tool-schema.diff` (empty), `compat-*.json` | 39-command snapshot has names+descriptions only, not params/return/pause; 12,000/1,600 budgets not asserted |
| A8 errors/lossless/security | FAIL | `logs/a8-security-lossless.log` | Error receipts lack `next_action`; no stack/secret-suppression test; symlink explicitly not rejected |
| B1 entry/file map | PASS | `logs/structure.log` (13-line index, 32/32, 147 edges, 0 cycles) | — |
| B2 engine/server split | PASS | `b2-largest-modules.txt` (max 531; engine 23 / server 37 entry) | Release note says server 35 vs actual 37 (cosmetic) |
| B3 Python domain packages | PASS | T8 runtime layout tests, 45 .py / 38 shims, 17 pytest | T8 pack lacks test.log/build.log (F2) |
| B4 addon package + zip | PARTIAL | zip 41 entries, bl_info 1.11, 39 commands, T10 packaging tests, T16 install/remove | T9/test.log has 12 failing fake-blender launcher selftests; no dedicated register/unregister log |
| B5 Phase R migration | PENDING | `phase-r/`, `logs/build-check.log`, `logs/parity.log`, pack/install/rollback evidence | B5.12 B1 regression = D4 not executed; R1 review only covers what the reviewer verified |
| B6 release package | PASS | `phase-r/pack-list.txt`, `T16/logs/pack-list.txt`, clean-clone install smoke | Pack list one commit stale (D2) |
| B7 no duplicates/dead code | PARTIAL | `b7-retired-api.txt`, 0 runtime .mjs, 38 re-export shims | Gate names `duplicate-report.json`; that artifact does not exist |
| C1 three independent layers | PASS | T11/T12 unit/integration/pytest/E2E separation | — |
| C2 PR gate in clean clone | PASS | `T12/clean-clone.log` (c9805f5), `T16/clean-clone.log` | Log label omits `--legacy-peer-deps`; contract suite not rerun there |
| C3 behavior matrix | PASS | `matrix-report.json`, `logs/matrix.log` (347 / 345 covered / 0 planned / 2 exempt) | T14 committed report sources stale |
| C4 legacy test migration | PASS | tests/MIGRATION.md 67/67, test:legacy removed | — |
| C5 coverage | FAIL | T12 coverage/diff-cover: JS diff 32%, Python addon 32% vs 80/70 | Issue #52 is not a valid §8.0 EXEMPT; release blocked |
| C6 real Blender E2E | PARTIAL | headless 29 / GUI 6 + 2 approved skips, install/enable/remove | **No doctor and no export case in the E2E results**; T16 log note claiming doctor is wrong (corrected in T16/acceptance.md) |
| C7 skip/flaky/retry | PASS | Issue #53 approves GUI skips; no retry plugin | `T10_SKIP_GUI=1` documented only in code |
| C8 test quality | PASS | T12 review sample; independent sample of 5 suites | Spot-check, not file-by-file |
| D1 clean environment | PASS | `T16/clean-clone.log` | No T16/env.txt; local clone only |
| D2 release package | PARTIAL | `T16/logs/pack.log`, pack-list, entry/install smoke | Pack-list is from a pre-evidence commit; final tree with evidence packs 4.2 MB / 845 files |
| D3 addon install/migration | PARTIAL | `T16/logs/install-migration.log`, GUI E2E; `T16/acceptance.md` corrected to PARTIAL | No Blender-E2E `doctor kind=ok`, no export, no DSH-restart persistence |
| D4 B1 3×P0/P1 regression | PENDING | `/opt/bench/runs/b1/t16-d4.log`, ledger | P0 bw_0001 93.75; P0 bw_0023 done 88.75; P0 bw_0091 running; P1 not started |
| D5 release and rollback | BLOCKED | no tag/Release/npm publish; rollback rehearsal git/build-level | User deferred release; D5 not executed |
| D6 release docs | PASS | CHANGELOG/MIGRATIONS/README, docs-diff, release notes | — |
| E1 AGENTS rules | PASS | `logs/e1-e5-docs.log` (22 agents-doc tests), AGENTS 120 lines | — |
| E2 decidable spec | PASS | T13/spec-review.md, spec §5/§10 | Reviewed at T13 while gates were open |
| E3 AI assets | PASS | 4 ADRs + index + spec + PR template, docs-tree | — |
| E4 review and size | FAIL | T13 review admits oversize PR; merge commits 1.8k–42k lines; `bc76135` adds 2,837 | No PR objects, no non-author R1 records before this review, no Lead exception |
| E5 docs fact check | PASS | `logs/e1-e5-docs.log` (14 docs-bilingual), tests/README 54 lines | Gate names docs-check.log |
| E6 metrics baseline | FAIL | spec §9 | Four of five collaboration metrics have no numeric baseline |
| F1 all gates | FAIL | this table | C5, A1, A8, E4, E6, F2, F3 fail; B5/D4 pending; D5 blocked; 8 partial |
| F2 evidence archive | FAIL | T0-T16 evidence + this dir | No evidence-index, sign-off, checksums or env/commands for gates; several packs lack cited logs; T16 lacks env/commands/review |
| F3 fact sources synced | FAIL | spec.md:245 still says matrix is a stub; evidence README stale; plan unchecked | Spec/evidence index contradicts final state |
| F4 sign-off | PENDING | independent-review.md | No sign-off.md, no author+independent+Lead signatures; signing now would endorse a failing F1 |

## Reproducing the machine-readable artifacts

```sh
node docs/refactor/evidence/gates/generate-gate-artifacts.mjs
node docs/refactor/evidence/gates/make-compat.mjs D:/DSH/worktrees/base-262
```

The first writes `matrix-report.json`, `catalog-report.json`,
`catalog-lock.diff`, `command-matrix.json`, `route-matrix.json`,
`tool-matrix.json`, `protocol-info.json`. The second writes
`compat-base.json`, `compat-v300.json`, `compat.diff`, `tool-schema.diff`.
A non-empty lock or tool-schema diff, or any UNDECLARED line in compat.diff,
fails the generator.

Raw logs in `logs/`: contract (23 files / 173), a1-tool-execution (15),
a1-unreachable-matrix (16), a3-command-contract, a4-protocol-11 (16),
a5-routes (16), a6-surface-fit (+pytest), a8-security-lossless (14),
e1-e5-docs, parity, structure, build-check, matrix, full-test-after-gate-fixes
(370 passed / 2 skipped; pytest 295), typecheck, lint, format.

## Process note

The gate artifacts that A1/A2/A7 lacked were created during the independent
review (22:24–22:27) and committed after it inspected them. That is an F2/E4
finding, not a substitute for evidence present before the release commit. The
T16 claim that the GUI probe checks `doctor` was corrected to PARTIAL in
`docs/refactor/evidence/T16/acceptance.md`; the raw log note was not rewritten.
