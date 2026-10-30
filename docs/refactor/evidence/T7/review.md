# T7 review

- Reviewer: author (self-check only). An independent non-author review is
  required for R1 items but was **not** performed in this worktree.
- Date: 2026-10-30.

## Decisions

- `http/config.mjs` owns `PORT/HOST/argv`, the global `stats` object, the RTT
  table and request counters. `http/lease.mjs` owns `LEASE`, scope
  normalization, liveness/probe, reclaim, acquire, the read-only op table and
  `leaseGate`, copied verbatim from base lines 77–196. `http/response.mjs`
  owns JSON/NDJSON/readBody and host CORS helpers.
- `http/host.mjs` keeps both `hostStateView` and the `/host/*` handler
  (`createHostRoutes(E)`), matching §4.5's "host.mjs — hostStateView,
  /host/state, /host/scene-items, /host/selection*, /host/jobs*". An earlier
  `routes/host.mjs` generation was merged back into `http/host.mjs` to keep
  that ownership.
- `http/router.mjs` is data-driven: each route module exports a
  `createXRoutes(E)` factory whose handlers were machine-extracted from the
  base `if (p === …)` blocks; `routes/index.mjs` exposes the ordered registry
  and `CONTRACT_ROUTES` (single source for the startup log and contract test).
- `http/lifecycle.mjs` owns `samePortHolders`/`killHolders`/`startServer`
  including the EADDRINUSE reclaim, SIGTERM/SIGKILL escalation and the
  `backend-listen-error.txt` fallback. `server.mjs` only wires modules and
  awaits `engine.start()` before `startServer`.
- Route factories receive a fat `E` context (engine, response helpers, lease
  helpers, stats, router utilities) instead of importing server state directly;
  this keeps the modules acyclic and testable with bare `{}` contexts.

## Residual risks

- HTTP parity covers 25 representative requests with a fake addon. It does not
  cover streaming heartbeat frames, a successful `/view` image response, a
  successful `/headless` NDJSON stream with real Blender progress, or the
  `EADDRINUSE`/`SIGTERM` lifecycle branches. Those bodies were copied
  verbatim from base but are not independently re-executed here.
- `/host/jobs/log`, `/host/scene-items`, `POST /host/selection` and
  `/host/selection/consume` are registered and the contract test asserts their
  paths; only `/host/state`, `/host/jobs`, `/host/selection/latest` and the
  `/host` OPTIONS/CORS behavior are exercised end-to-end in the new Vitest
  integration test.
- Normalization in parity.log drops timing, pid, port, lease-token and
  write-probe-pid fields. These are the only differences observed; structural
  keys, status codes and error strings matched.
- `/host/jobs` reads the shared on-disk ledger, so the integration test asserts
  the envelope shape rather than an empty job list; on a machine with prior
  jobs it still passes.
- No real GUI Blender/addon was used. The fake addon echoes flat-protocol
  responses; framing was already covered by T3.
- Legacy `provenance_stale_selftest.mjs` fails on its non-recursive copy
  fixture (see acceptance.md known gaps); `route_contract_selftest.mjs` exits 0
  by skipping because no backend is running.
- Independent review has not been performed; residual risk acceptance for R1
  remains with the parent/reviewer.
