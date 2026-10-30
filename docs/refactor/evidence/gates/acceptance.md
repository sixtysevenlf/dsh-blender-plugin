# Section 8 gate audit (pre-audit)

This directory is the consolidated evidence index for the release gates in
`docs/refactor-plan-v3.md` §8. It does **not** replace the per-task evidence
packs under `docs/refactor/evidence/<TASK-ID>/`; it points at them and adds the
cross-task logs/artifacts that the gate text asks for.

Status: **Lead pre-audit, before the independent non-author review.** The
independent review is being written to `independent-review.md`; any status it
changes wins. A gate is only PASS when every clause of its §8 text has
reproducible evidence. Missing evidence or a missing sub-case is FAIL, not
"looks fine".

- Commit: the gate log commit plus this audit are based on `7806a5b`
  (refactor branch; parent 13cc256).
- Plan: `D:/DSH/dsh-blender-plugin-refactor-plan-v3.md` §8.
- Environment: Node `v22.17.0` (`C:/Users/villa/node22`), Python
  `C:/Users/villa/e2e/dsh-venv/Scripts/python.exe`, Blender 4.2.10
  (`C:/Users/villa/e2e/blender-4.2.10/blender.exe`), Windows 11.

## Reproducing the machine-readable artifacts

From the repo root, after `npm run build`:

```sh
node docs/refactor/evidence/gates/generate-gate-artifacts.mjs
```

It writes `matrix-report.json` (parsed from tests/BEHAVIOR_MATRIX.md),
`catalog-report.json` (34 families / 228 ops / fingerprint / lock match),
`catalog-lock.diff` (empty when the committed lock matches the live catalog),
`command-matrix.json` (39 commands), `route-matrix.json` (29 routes),
`tool-matrix.json` (15 tools) and `protocol-info.json` (addon protocol 11,
bl_info 1.11, read from the addon sources).

`node docs/refactor/evidence/gates/make-compat.mjs <v2.6.2-worktree>` writes
`compat-base.json`, `compat-v300.json`, `compat.diff` and
`tool-schema.diff`. The diff may only contain the declared plugin/addon
version changes; any undeclared difference exits non-zero.

Raw logs in `logs/`:

| Log | Command |
|---|---|
| contract.log | `npm run test:contract` (23 files / 173 tests) |
| a1-tool-execution.log | tool-execution.test.ts verbose (15 tool names) |
| a3-command-contract.log | command-contract.test.ts verbose |
| a4-protocol-11.log | protocol-contract.test.ts + fake-addon.test.ts verbose |
| a5-routes.log | route-contract.test.ts + backend/server-routes verbose |
| a6-surface-fit.log / -python.log | surface-fit.test.ts + pytest surface fit |
| a8-security-lossless.log | security.test.ts + lossless.test.ts verbose |
| e1-e5-docs.log | agents-doc.test.ts + docs-bilingual.test.ts verbose |
| parity.log | `npm run test:parity` (all checks ok) |
| structure.log | `npm run check:structure` (32/32 files, 147 edges, 0 cycles) |
| build-check.log | `npm run check:build` (62 .ts, 0 @ts-nocheck, lib untracked) |
| matrix.log | `npm run test:matrix` (347 rows / 345 covered / 0 planned / 2 exempt) |
| full-test-after-gate-fixes.log | `npm test` (354 passed / 2 skipped; pytest 295 passed) |
| typecheck.log / lint.log / format.log | full gates after the T16 parity + count-sync fix |

Other artifacts: `docs-tree.txt`, `b2-largest-modules.txt`,
`b7-retired-api.txt`, `protocol-info.json`, `catalog-lock.diff`,
`compat*.json`, `compat.diff`, `tool-schema.diff`.

## Gate status

| Gate | Status | Evidence | Remaining gap |
|---|---|---|---|
| A1 15 tool execution semantics | FAIL | `logs/a1-tool-execution.log` (15 tools), `contract.log`, `tool-schema.diff` (empty vs v2.6.2), T2/T14 evidence | Tool schemas match v2.6.2, but per-tool timeout/backend-unreachable cases are not individually exercised |
| A2 family/op contract | PASS | `catalog-report.json` (`lockMatchesCommitted: true`), `catalog-lock.diff` (empty), `logs/contract.log`, T2 evidence | Regenerated lock matches the committed fixture; unique family ops and dispatch prefixes are asserted |
| A3 39 addon commands | FAIL | `command-matrix.json` (39 rows), `logs/a3-command-contract.log` | 39 rows are covered by matrix references, but no per-command request/response/error/pause execution record |
| A4 protocol 11 | PASS | `logs/a4-protocol-11.log` — handshake, mismatch, partial frame, disconnect, timeout, error, pause; protocol 11 in `protocol-info.json` | R1 non-author review |
| A5 29 HTTP routes | FAIL | `route-matrix.json`, `logs/a5-routes.log` | Route set + representative lease/CORS/error tests, but not every route's normal/illegal/error/CORS case |
| A6 surface_fit honesty | PASS | `logs/a6-surface-fit.log`, `logs/a6-surface-fit-python.log` | R1 non-author review |
| A7 v2.6.2 vs 3.0.0 compat snapshot | PASS | `compat-base.json`, `compat-v300.json`, `compat.diff` (only the declared 2.6.2→3.0.0 and addon 1.10→1.11 changes), `tool-schema.diff` (empty) | Commands compared by name/description; protocol envelope shape is covered by A4 |
| A8 errors/lossless/security | FAIL | `logs/a8-security-lossless.log` | Lossless, file= guard, zip traversal, shell and lease covered; no explicit "no raw stack/secret" assertion |
| B1 entry/file map | PASS | `logs/structure.log` (32/32, 147 edges, 0 cycles) | — |
| B2 engine/server split | PASS | `b2-largest-modules.txt` (max 531 lines; engine 225, server 35), T6/T7 evidence | — |
| B3 Python domain packages | PASS | T8 acceptance/test.log (39 modules, 38 shims, Blender import smoke) | R2 author self-check |
| B4 addon package split | PASS | T9/T10 acceptance, zip sha256, real Blender install | R1 non-author review |
| B5 Phase R migration/build/pack | PENDING | T15f evidence, `logs/parity.log`, build/pack/install logs | B1 3-task regression is D4, still running |
| B6 repo and release package | PASS | T16 pack-list/install-smoke/size logs; npm pack 3.2 MB / 829 files | R1 non-author review |
| B7 no duplicates/dead code | FAIL | `b7-retired-api.txt`, T11 migration | Retired `blender_rt_pick` still appears in historical comments/llms.txt; no formal duplicate report; found and fixed a stale `sync_counts.mjs` import |
| C1 three independent layers | PASS | T12 C1 logs (unit/integration/e2e) | — |
| C2 PR gate in clean clone | PASS | T16/clean-clone.log, T12 clean clone | — |
| C3 behavior matrix | PASS | `matrix-report.json`, `logs/matrix.log` (345 covered / 0 planned / 2 approved constraints) | — |
| C4 legacy test migration | PASS | tests/MIGRATION.md (67/67, no pending), test:legacy removed | — |
| C5 coverage | FAIL | T12 coverage/diff-cover; JS diff 32% (8232 changed / 5545 missing), Python addon 32% vs 80/70 target | Issue #52 is not a valid §8.0 EXEMPT; release is blocked |
| C6 real Blender E2E | PASS | T16 E2E headless 29 / GUI 6 + 2 approved skips, install migration checks | R1 non-author review |
| C7 skip/flaky governance | PASS | Issue #53 approves the 2 GUI conditional skips; no retry masking | — |
| C8 test quality spot-check | FAIL | T12 review | Only author spot-check; no independent spot-check of the "no 800-line selftest" claim |
| D1 clean environment | PASS | T16 clean-clone.log | R1 non-author review |
| D2 release package | PASS | T16 pack-list/pack-actual/size, install smoke | R1 non-author review |
| D3 addon install/migration | PASS | T16 install-migration log, real Blender | R1 non-author review |
| D4 B1 small regression | PENDING | `/opt/bench/runs/b1/t16-d4.log`, PID 948 | P0 bw_0001 93.75 done; P0 bw_0023 in EVAL; 4 runs pending |
| D5 release and rollback | BLOCKED | T16 rollback-check, release-notes draft | User deferred tag/Release/npm publish; not executed |
| D6 release docs | PASS | T16 docs-diff, CHANGELOG/MIGRATIONS/README, T15f rollback | — |
| E1 AGENTS rules | PASS | `logs/e1-e5-docs.log` (agents-doc 36 doc tests) | — |
| E2 decidable spec | PASS | T13 spec-review.md; spec §5/§8 mapping | — |
| E3 AI assets | PASS | `docs-tree.txt` (4 ADRs + index + spec + PR template) | — |
| E4 review and size | FAIL | T13 review; PR sizes in spec Metrics | Lead exceptions still needed for >400-line PRs; independent R1 reviews not complete |
| E5 docs fact check | PASS | `logs/e1-e5-docs.log`, docs-tree | Behavior-matrix coverage is C3 |
| E6 metrics baseline | FAIL | spec §9 | Delivery metrics measured; review time, rework, flaky, agent first-pass are "not instrumented" |
| F1 all gates | FAIL | this table | A1/A3/A5/A8/B7/C5/C8/E4/E6 fail; B5/D4 pending; D5 blocked |
| F2 evidence archive | FAIL | T0-T16 evidence + this dir | Index/checksums for the new gate artifacts not yet finalized; independent review missing |
| F3 fact sources synced | FAIL | T16 docs; plan §7.2 not ticked | plan checkboxes still open; Notion mirror not updated |
| F4 sign-off | PENDING | independent-review.md (in progress) | Needs independent review + Lead signature |

## Summary

- PASS: A2, A4, A6, A7, B1, B2, B3, B4, B6, C1, C2, C3, C4, C6, C7, D1, D2,
  D3, D6, E1, E2, E3, E5.
- FAIL: A1, A3, A5, A8, B7, C5, C8, E4, E6, F1, F2, F3.
- PENDING: B5, D4, F4. BLOCKED: D5.
- Release verdict: **BLOCKED**. C5 alone blocks release per §8.0; A1/A3/A5/A8
  and the R1 reviews also remain open. Plan §7.2 is deliberately not ticked.
