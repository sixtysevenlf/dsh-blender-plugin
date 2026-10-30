# T15d — engine/core/execution migration

## Scope

§7.2 T15d and §4.4: the engine/core/execution modules moved to .ts; 15 tools / 34 families / 228 ops / 39 commands / protocol 11 unchanged; jobs, lease, txn, and worker regressions pass; engine moved last.

## Result

PASS for the contract and regression criteria. The engine facade is a 23-line re-export/assembly entry (runtime/engine.ts) over runtime/core/engine.ts; parity against the base runtime passes.

## Acceptance

| ID | Result | Evidence | Reviewer | Date |
|---|---|---|---|---|
| T15d.1 engine/core/execution modules migrated to .ts | PASS | runtime/core/**/*.ts · runtime/execution/**/*.ts · build.log | author self-check | 2026-10-30 |
| T15d.2 15 tools / 34 families / 228 ops / 39 commands / protocol 11 unchanged | PASS | parity.json · contract.log (tool-schema, command-contract, op-contract, protocol-contract) | author self-check | 2026-10-30 |
| T15d.3 jobs / lease / txn / worker regressions pass | PASS | test.log (host-jobs, lease-stale, lease-scope, txn/preset contract, worker) | author self-check | 2026-10-30 |
| T15d.4 engine migrated last; facade behavior unchanged | PASS | engine-facade.txt · engine-surface.test.ts green in test.log | author self-check | 2026-10-30 |

## Caveats

- The engine moves landed inside the same review-level commit as the leaves; the local conversion order was leaves -> server/http -> engine/core/execution with engine last, but git history does not expose per-module commits. Contract parity is the behavioural proof.
- Real addon/job rendering was not exercised (no GUI Blender/live addon); job lifecycle is covered by integration tests against a real backend on an ephemeral port.
