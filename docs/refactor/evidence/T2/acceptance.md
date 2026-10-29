# T2 — First contract-test batch

## Scope
§7.1 B (second half): catalog/lock parity, tool schema, lossless return channel,
client bundle, backend route contract, and surface_fit consistency.

## Result
PASS. Six contract files contribute 21 tests; all pass on Windows and WSL. The
catalog matches the frozen per-family op counts, all 15 tools keep their schema
budget, every receipt can be sanitized to DSH lossless JSON, the client bundle
registers the expected factory, all family prefixes reach engine dispatch, and
surface_fit is a registered M3 refusal.

## Acceptance

| ID | Result | Evidence | Reviewer | Date |
|---|---|---|---|---|
| T2.1 catalog: 34 families / 228 ops, per-family counts match capability.lock | PASS | catalog.test.ts | author self-check | 2026-10-30 |
| T2.2 tool schema: 15 tools, description budget < 12,000 / 1,600, schema/timeout/presenter preserved | PASS | tool-schema.test.ts | author self-check | 2026-10-30 |
| T2.3 lossless: sanitizer + receipts pass dsh-session snapshotJsonValue and contain no undefined | PASS | lossless.test.ts | author self-check | 2026-10-30 |
| T2.4 client bundle: registered id, apply/inject, no ESM/Node builtin imports | PASS | client-bundle.test.ts | author self-check | 2026-10-30 |
| T2.5 route contract: stable HTTP route set and every family prefix reaches engine dispatch | PASS | route-contract.test.ts | author self-check | 2026-10-30 |
| T2.6 surface_fit: catalog/lock/help/registration agree; explicit M3 refusal | PASS | surface-fit.test.ts + test_surface_fit_contract.py + T2/test.log | author self-check | 2026-10-30 |

## Notes
- The contract tests import the built `lib/index.js` (the user-facing artifact) and
  the plain `runtime/*.mjs` modules; they do not start a backend or Blender.
- A6 is marked R1 in §8; this pack records the implementation and self-check. An
  independent reviewer must sign the R1 acceptance before release.
