# T6 — runtime/engine.mjs split (§4.4)

## Scope
Split the 3,189-line single-closure `runtime/engine.mjs` into the modules named by
plan §4.4 while freezing the public engine contract:

- `runtime/core/{engine,kernel,output,scripts,progress,evidence,plan-dispatch,flags,render-state,dispatch,errors,server-compat}.mjs`
- `runtime/execution/{jobs,ledger,workers,txn,preset,trajectory}.mjs`
- `runtime/transport/probes.mjs`
- `runtime/engine.mjs` remains a thin public facade; `createEngine` only assembles
  the split factories and returns the public surface.

## Result
PASS (author self-check). Export surface, `createEngine()` key surface and key
order are identical to base 19828ed; the full layered suite is green.

## Acceptance

| ID | Result | Evidence | Reviewer | Date |
|---|---|---|---|---|
| T6.1 every §4.4 target file exists (plus `execution/headless.mjs`, see review) | PASS | line-counts.txt | author self-check | 2026-10-30 |
| T6.2 `runtime/engine.mjs` is a 23-line re-export facade; `core/engine.mjs` (132 lines) only assembles/dispatches and returns the surface | PASS | line-counts.txt, review.md | author self-check | 2026-10-30 |
| T6.3 behavior frozen: 87 vs 87 public exports, no missing/extra; 32 vs 32 `createEngine()` keys, same order; worker/job key order equal | PASS | parity.log | author self-check | 2026-10-30 |
| T6.4 §8 B2 size gate: largest new module 457 lines (`execution/headless.mjs`), all normal modules ≤600, none >1,000 | PASS | line-counts.txt | author self-check | 2026-10-30 |
| T6.5 full layered suite: 12 Vitest files / 44 tests passed; pytest 5 passed | PASS | test.log | author self-check | 2026-10-30 |
| T6.6 existing import path preserved: `runtime/engine.mjs` still exports 87 names and the real `AddonClient`; discoverability/provenance selftest 52/52 | PASS | test.log, discoverability.log | author self-check | 2026-10-30 |
| T6.7 syntax/build gate for every split module: `node --check` 43 modules OK; engine import smoke 87 exports | PASS | build.log | author self-check | 2026-10-30 |

## Notes
- No real Blender is needed for the T6 suite; the fake addon supplies the channel.
- `test.log` is the raw `node scripts/test.mjs` output (Vitest + pytest).
- The legacy `test:legacy` suite is **not** an acceptance gate here; its
  source-grep/copy fixtures are migrated in T11 (see review.md).
