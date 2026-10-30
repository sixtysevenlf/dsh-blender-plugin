# 0001. Rewrite the backend runtime in strict TypeScript

- **Status**: Accepted
- **Date**: 2026-10-30
- **Deciders**: plugin maintainers
- **Target**: 3.0.0, task T15 (Phase R)

## Context

The backend is sixteen flat `.mjs` files under `runtime/` (about 8,000 lines, including
`engine.mjs` at 3,189 and `server.mjs` at 1,009). The host side under `src/` is already strict
TypeScript, but the backend has no compile step: types exist only in a hand-written
`backend_probe.d.mts`, and every boundary (JSON payloads, subprocess output, Python bridge results, file
paths) validates by convention. Contract tests import the `.mjs` files directly, while the released
package serves `lib/` built from `src/` only.

Two problems follow. First, a change in the backend can break a caller that TypeScript would have caught
before tests run; the type declarations drift silently. Second, there is no single build artifact that both
the tests and the package trust: the runtime is interpreted in development and absent from `lib/`, so
packaging a working backend depends on shipping raw source.

The refactor plan (§4.4, §10) splits the backend into `core/`, `execution/`, and `transport/` and
converts it to TypeScript in the same phase. This ADR fixes the language and build decision; the file
layout is part of the plan.

## Decision

The backend runtime SHALL be written in TypeScript under `runtime/` and compiled with
`tsconfig.runtime.json` (strict) into `lib/runtime/**/*.js`.

- At the end of Phase R there are zero `runtime/**/*.mjs` files and zero `runtime/**/*.d.mts` files
  that only exist to declare a JavaScript module.
- No `@ts-nocheck`, no new explicit `any`; boundary parsing goes through typed functions in one place
  per boundary.
- Unit and integration tests import the TypeScript sources. End-to-end tests, the backend spawn, and the
  packed artifact use the built JavaScript from `lib/runtime/`.
- The backend entry that `SERVER_PATH` resolves to is `lib/runtime/server.js`.
- Source maps are emitted so a stack trace in a crash still points at the TypeScript line.
- The published contracts do not move: 15 tools, 34 families, 228 ops, 39 addon commands, 29 HTTP routes,
  protocol 11.

**Migration order** is leaf-first, one module or one dependency edge per change:

1. `platform/` and `transport/` leaves (paths, environment, protocol frames, backend probe).
2. Pure `core/` modules with no I/O (catalog, classes, config).
3. `execution/` modules (jobs, ledger, workers, transactions, presets).
4. `core/engine` assembly and `http/` server last; the old file is deleted in the same change that
   makes the new one authoritative.

A shim may exist during a single change and may only re-export. Tests prove parity before the old file is
removed; behavior does not change while code moves. Rollback is per change: revert the commit, because the
previous module is still present until its replacement lands.

## Consequences

- A type error in the backend now fails `npm run typecheck` before tests run.
- Contributors must build before running the backend or the end-to-end tests; the unit and integration
  layers stay source-first so the inner loop is fast.
- The tarball gains `lib/runtime/**/*.js` and must stop shipping `.ts` sources; the package check in
  [ADR 0004](0004-lib-build-release.md) enforces it.
- Some `.mjs`-only conveniences (top-level dynamic imports of optional modules) need explicit typed
  wrappers; that is accepted as the cost of a checked boundary.
- The diff is large but mechanical; each PR stays reviewable because it moves one module and its test.

## Alternatives considered

- **Keep JavaScript plus JSDoc `checkJs`.** Less work, but nullable and structural typing stays weaker
  than strict TypeScript, and the `d.mts` drift problem remains.
- **One big-bang conversion.** Produces an unreviewable diff, makes bisecting a behavior change
  impossible, and violates the migrate-in-slices rule in [AGENTS.md §3](../../AGENTS.md#3-hard-rules).
- **Convert only new modules.** Leaves two languages and two build paths indefinitely; the plan's
  exit criterion is zero `.mjs`.

## Links

- Specification: [docs/refactor/spec.md](../refactor/spec.md), gates B5 and B6
- Build and packaging: [ADR 0004](0004-lib-build-release.md)
- Commands: [AGENTS.md §1](../../AGENTS.md#1-commands-single-source)
