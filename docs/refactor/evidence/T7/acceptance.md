# T7 — runtime/server.mjs split (§4.5)

## Scope
Split the 1,009-line `runtime/server.mjs` into the §4.5 layout:

- `runtime/server.mjs` — process entry only: read port, `createEngine`,
  `createSelectionStore`, `createRouter`, `http.createServer`, `engine.start`,
  `startServer` (listen/shutdown), signals.
- `runtime/http/{config,lease,response,host,router,lifecycle,jobs}.mjs`
- `runtime/http/routes/*.mjs` — one module per route group plus the
  `CONTRACT_ROUTES` registry (29 paths).

Routes, lease and host handling are separate modules; no route body is in
`server.mjs`.

## Result
PASS (author self-check). 29-route contract and lease semantics are unchanged;
base-vs-split HTTP responses are identical on 25 requests after normalizing only
timing/pid/port fields.

## Acceptance

| ID | Result | Evidence | Reviewer | Date |
|---|---|---|---|---|
| T7.1 every §4.5 target file exists; `server.mjs` is a 35-line entry (engine/selection store/router/listen/shutdown only) | PASS | line-counts.txt, review.md | author self-check | 2026-10-30 |
| T7.2 route/lease/host are separated: `http/lease.mjs` (127), `http/host.mjs` (151), `http/router.mjs` (24), 16 route modules; no `p === '/health'`-style dispatch in `server.mjs` | PASS | line-counts.txt, route-contract test | author self-check | 2026-10-30 |
| T7.3 29-route contract unchanged: `CONTRACT_ROUTES` has 29 unique paths; route-contract Vitest 4/4; startup route list matches base; legacy route selftest exits 0 (skips without backend) | PASS | test.log, test-routes.log, parity.log | author self-check | 2026-10-30 |
| T7.4 base-vs-split HTTP parity: 25 requests (health/who/status/doctor/commands/host/410/404/plan/cmd/lease/release/loop/perf/opt/txn/preset/job/worker/act/view/headless) identical after normalizing only timing/pid/port strings | PASS | parity.log | author self-check | 2026-10-30 |
| T7.5 lease semantics preserved: 5 focused Vitest lease tests; legacy lease selftest 20/20 through the real headless write path with Blender 4.2.10 | PASS | test.log, lease-selftest.log | author self-check | 2026-10-30 |
| T7.6 §8 B2 size gate: largest server/http module 151 lines, all normal modules ≤600, none >1,000 | PASS | line-counts.txt | author self-check | 2026-10-30 |
| T7.7 full layered suite: 12 Vitest files / 44 tests passed; pytest 5 passed | PASS | test.log | author self-check | 2026-10-30 |
| T7.8 syntax/build gate: `node --check` OK for all split server modules | PASS | build.log | author self-check | 2026-10-30 |

## Known gaps (not acceptance failures of T7)
- `tests/provenance_stale_selftest.mjs` fails because its fixture copies only
  top-level `runtime/*.mjs`; the split imports `runtime/core/evidence.mjs`.
  This is a legacy fixture migration item for **T11**, not a runtime defect.
  Raw log: `provenance-selftest.log`. The same provenance behavior passes in
  the 52-check discoverability selftest (T6 evidence).
- The legacy `npm run test:legacy` chain was not run as a whole; it is a T11
  deliverable and contains other source-grep fixtures.
- Independent non-author review (R1) was not performed in this worktree; the
  table records author self-check only.

## Notes
- `test.log` is the raw `node scripts/test.mjs` output (Vitest + pytest).
- The parity runner and fake addon are test-only; they never bind 9876/9877.
- No behavior change is claimed for paths not exercised by parity (see
  review.md residual risks).
