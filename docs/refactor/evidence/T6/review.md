# T6 review

- Reviewer: author (self-check only). An independent non-author review is
  required for R1 items but was **not** performed in this worktree; do not read
  this file as independent review.
- Date: 2026-10-30.

## Decisions

- `runtime/engine.mjs` was kept as a **thin facade** (23 lines of re-exports)
  instead of moving all 87 exports physically into `core/engine.mjs`. This
  preserves every existing import path (`tests/*`, `src/*`, host integration)
  while satisfying the §4.4 goal that engine splitting leaves only assembly in
  the engine entry. `core/engine.mjs` contains the `createEngine` assembly and
  nothing else.
- `core/evidence.mjs` owns `PLUGIN_VERSION`, `sha12`, `ENGINE_SELF`,
  `ENGINE_SOURCE_PATH`, `runtimeFingerprint` and the provenance/hygiene helpers.
  `RUNTIME_DIR` is derived from the module URL; `runtimeFingerprint()` now walks
  the runtime tree recursively (skipping `ext/`) and emits relative keys
  (`engine.mjs`, `core/engine.mjs`, …). Catalog provenance/verify behavior is
  exercised by the 52-check discoverability selftest.
- `execution/headless.mjs` (457 lines) is an **additional** module not listed in
  the §4.4 table. Keeping the synchronous headless runner there holds
  `execution/jobs.mjs` at 353 lines instead of >700; §8 B2 allows ordinary
  modules ≤600 lines and forbids >1,000, so no ADR is required.
- `core/plan-dispatch.mjs` (439 lines) contains `planCall`/`enhancePlanResult`/
  `extCheckOp`/`renderGuardOp`/`shapeSearchOp`, while `core/dispatch.mjs`
  (198 lines) holds only the prefix→ensure→addon/plan dispatch table. This is
  the plan's §4.4 split of the two responsibilities, kept under 600 lines each.
- `readJsonSafe` remains in `runtime/proc_env.mjs` (its pre-existing home and
  export), not duplicated into `core/scripts.mjs` as the §4.4 sketch suggests;
  moving it would have been an unrelated Phase R relocation.
- `jobs.mjs` reuses `E.ledger || createLedger(E)` so `jobs`, `ledger` and the
  headless runner share one `runs` Map, matching the original single closure.
- `core/server-compat.mjs` exports `createPublicMethods(E)` plus
  `createLoopApi(E)`; the loop API is created before `plan-dispatch` so its
  render/qc/shape-search ops receive the same loop functions as the base.

## Residual risks

- Parity is at the public-surface level. Internal call graphs, private helper
  behavior and rare error branches were not exhaustively diffed; the full
  Vitest suite, the 52-check discoverability selftest and the T7 HTTP parity
  run provide the behavioral safety net.
- Fine-grained property descriptors (enumerability, getters) of the exported
  names were not compared; all exports are plain values/functions and no
  consumer uses descriptors.
- The legacy `provenance_stale_selftest.mjs` copies only top-level
  `runtime/*.mjs` into a temp fixture, so it now fails with
  `ERR_MODULE_NOT_FOUND … runtime/core/evidence.mjs` (see T7 log). This is a
  fixture limitation scheduled for T11, not a runtime failure; the same
  provenance behavior passes in `tests/discoverability_selftest.mjs`.
- Blender-backed headless execution was exercised only by the legacy lease
  selftest (with `DSH_BLENDER_EXE` set), not by the fast Vitest suite.
- `node_modules` is a junction; Vitest needs `TEMP/TMP` exported or it fails
  with EPERM on `.vite-temp`. This is an environment prerequisite, not a code
  change.
