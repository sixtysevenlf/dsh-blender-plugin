# T15b — leaf module migration

## Scope

§7.2 T15b, §4.6 and the T15a inventory: every leaf module moved to .ts (paths, proc_env, file_guard, hard_exit, config, catalog, classes, addon-protocol, backend_probe, transport/probes, staging, recover, selectionstore, launch); the matching unit/integration tests pass; the old .mjs was removed in the same commit.

## Result

PASS: all 60 base modules are .ts under the same paths, the leaf unit/integration tests are green, and the base .mjs files are deleted in commit 0a2a395 (renames detected).

## Acceptance

| ID | Result | Evidence | Reviewer | Date |
|---|---|---|---|---|
| T15b.1 leaf modules migrated to .ts (paths, proc_env, file_guard, hard_exit, config, catalog, classes, addon-protocol, backend_probe, transport/probes, staging, recover, selectionstore, launch) | PASS | inventory.txt · runtime/*.ts | author self-check | 2026-10-30 |
| T15b.2 unit tests for the leaf modules pass | PASS | test.log (platform-paths, workdir, headless-file-guard, headless-hard-exit, backend-probe, launch, selection-store, lease-stale, ledger-recover) | author self-check | 2026-10-30 |
| T15b.3 integration tests for the leaf modules pass | PASS | test.log (fake-addon, backend-probe, launch, selection-store, workdir) | author self-check | 2026-10-30 |
| T15b.4 old .mjs deleted in the same migration commit (rename + delete) | PASS | migration-diff.txt (git show --stat 0a2a395) | author self-check | 2026-10-30 |
| T15b.5 typecheck and contract counts unchanged after the leaf moves | PASS | typecheck.log · contract.log · parity.json | author self-check | 2026-10-30 |

## Caveats

- The migration landed as one review-level commit (0a2a395) although the working order was leaf-first; the plan allows a large mechanical migration commit (§10.3), and the commit message lists the boundaries in order.
- Vitest imports the .ts sources through .js specifiers; Node-based E2E/spawn imports the built lib/runtime JS (T15e).
