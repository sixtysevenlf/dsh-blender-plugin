# T14 — Behavior coverage matrix

## Scope

§6.13 / T14 and §8.3 C3: a behavior coverage matrix for 15 model tools, 39 addon
commands, 34 plan families / 228 op entries, 29 HTTP routes, protocol 11, lifecycle,
config, install/upgrade, security, Blender 4.2/5.x, and DSH current/previous. The
matrix is checked against live source by scripts/check-behavior-matrix.mjs; unit and
integration tests are deterministic and do not start Blender, a GUI, or the network.
The one real-Blender E2E test is separate from unit/integration.

## Result

PASS for the T14 functional scope. The matrix has 347 rows: 345 covered, 0 planned,
2 exempt. The release gate is clean at planned=0. The two exemptions are real
version/platform constraints (Blender 5.x unavailable; previous DSH unavailable
without network), not missing tests.

Caveat: "covered" means the row has a real unit, integration, or E2E test reference,
not that every command/op/route was executed against a live Blender install. The e2e
column names C6 for per-row live coverage. Only the Blender 4.2 headless E2E was run
here; GUI/rendering and Blender 5.x were not. Non-author R1 review is still PENDING.

## Acceptance

| ID | Result | Evidence | Reviewer | Date |
|---|---|---|---|---|
| T14.1 Matrix covers all required surfaces with exact source-derived counts | PASS | tests/BEHAVIOR_MATRIX.md · matrix-report.json | author self-check | 2026-10-30 |
| T14.2 Checker rejects missing/duplicate IDs, bad statuses, uncovered rows, invalid exemptions, and planned rows at release | PASS | scripts/check-behavior-matrix.mjs · checker.log · matrix-gate-test.log | author self-check | 2026-10-30 |
| T14.3 Focused tests added for tool execution, presenters, command catalog/dispatch, op catalog mapping, protocol, lifecycle/config, security, backend routes, package/install, and the matrix gate | PASS | 10 new Vitest files + fake-backend helper · test.log | author self-check | 2026-10-30 |
| T14.4 Real Blender 4.2 LTS headless E2E passes | PASS | tests/e2e/blender_42_headless.mjs · e2e-blender42.log | author self-check | 2026-10-30 |
| T14.5 Full existing Vitest suite passes under Node 22 with no product behavior change | PASS | test.log · package.json (test:matrix only) | author self-check | 2026-10-30 |
| T14.6 Evidence bundle complete with hashes | PASS | env.txt · commands.txt · counts.json · artifacts.sha256 | author self-check | 2026-10-30 |
| R1 Non-author review of C6 interpretation and exemptions | PENDING | review.md | — | — |

## Counts

| Surface | Count |
|---|---:|
| Model tools | 15 |
| Addon commands | 39 |
| Plan families | 34 |
| Plan op entries | 228 |
| HTTP routes | 29 |
| Protocol rows | 8 |
| Lifecycle rows | 8 |
| Config rows | 5 |
| Install/upgrade rows | 6 |
| Security rows | 5 |
| Environment rows | 4 |
| **Total rows** | **347** |
| Covered | 345 |
| Planned | 0 |
| Exempt | 2 |

Source of truth: 15 vTool registrations in src/index.ts; COMMAND_CATALOG in
runtime/catalog.mjs backed by addon/dsh_blender_bridge.py; PLAN_CATALOG in
runtime/catalog.mjs; the 29-route startup list in runtime/server.mjs; addon protocol
11 / 16 capabilities.

## Exact checker output

```
OK behavior matrix: 347 rows (345 covered, 0 planned, 2 exempt) · source 15 tools / 39 commands / 34 families / 228 ops / 29 routes
```

## Exact test output (tail)

```
 Test Files  19 passed (19)
      Tests  116 passed (116)
   Start at  19:39:30
   Duration  5.86s (transform 695ms, setup 0ms, collect 4.36s, tests 11.27s, environment 3ms, prepare 3.88s)
```

The full log is test.log. It includes harmless MSYS `ps` fatal-error lines emitted
during lifecycle child cleanup; the suite exits 0.

## Real Blender 4.2 E2E

Command:

```
DSH_BLENDER_EXE=D:/DSH/tools/blender-win/blender-4.2.10-windows-x64/blender.exe \
  node tests/e2e/blender_42_headless.mjs
```

Observed:

```
PASS Blender 4.2 headless E2E · 351ms · D:\DSH\tmp\dsh-t14-blender42-36520
```

The script asserts result.ok, exitCode 0, and the real Blender stdout marker
`T14_BLENDER_E2E 4.2.`. It is not part of the Vitest unit/integration suite.

## New test files

- tests/helpers/fake-backend.mjs
- tests/unit/js/contract/tool-execution.test.ts (20 tests)
- tests/unit/js/contract/tool-presenter.test.ts (5)
- tests/unit/js/contract/op-contract.test.ts (8)
- tests/unit/js/contract/command-contract.test.ts (9)
- tests/unit/js/contract/protocol-contract.test.ts (9)
- tests/unit/js/contract/lifecycle-config.test.ts (13)
- tests/unit/js/contract/security.test.ts (10)
- tests/unit/js/contract/behavior-matrix.test.ts (2; positive + negative release gate)
- tests/integration/js/backend-routes.test.ts (7)
- tests/integration/js/package-contract.test.ts (3)
- tests/e2e/blender_42_headless.mjs (outside Vitest)

## Unresolved planned rows

None. `--release` passes with planned=0.

## Exempt rows

| ID | Issue | Owner | Reason | Review |
|---|---|---|---|---|
| ENV-BLENDER-5X | T14-ENV-BLENDER-5X | refactor/T14 owner | no Blender 5.x runtime installed in this worktree | 2026-12-31 |
| ENV-DSH-PREVIOUS | T14-ENV-DSH-PREVIOUS | refactor/T14 owner | previous DSH version not installed; obtaining it needs network | 2026-12-31 |

ENV-BLENDER-42 is covered by the real headless E2E above and is not exempt.

## Notes

- Full suite was run directly as `node node_modules/vitest/vitest.mjs run
  --configLoader runner` because the worktree's node_modules symlink can make
  scripts/test.mjs fail with EPERM while writing .vite-temp. The same test files and
  assertions run.
- No product source (src/, lib/, runtime/, addon/) was changed. The only non-test
  change is a package.json script, `test:matrix`, wiring the checker into the
  release gate.
- T8/T9 are not landed in this worktree; tests target the current .mjs runtime and
  single-file addon. The checker will force matrix updates when those refactors land.
- The R1 non-author review is pending; this acceptance is an author self-check, not
  an independent approval.
