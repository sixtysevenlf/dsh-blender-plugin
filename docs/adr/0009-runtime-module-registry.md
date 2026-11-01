# 0009. One runtime Python module registry

- **Status**: Accepted
- **Date**: 2026-11-01
- **Deciders**: plugin maintainers
- **Target**: finalization F2.0/F2.3; enforced by `check-runtime-layout`

## Context

The runtime needs to map a public Python module name to a real file in
`runtime/py/<domain>/`. Before 3.0 that map existed implicitly in the flat
`runtime/<name>.py` files and in the path-based loader. T8 added
`runtime/paths.ts` with a hand-written `PY_MODULE_DOMAIN` record, while
`runtime/py/core/offline_bootstrap.py` had its own domain list and fallback.
The 38 flat shims repeated the same mapping a third time. Three copies can drift
silently: a new module is added to one and missed by the others, or a shim is
kept after its implementation moved.

The 3.0 facade split (ADR 0007/0008) made the implementation layout explicit,
which makes a single registry practical.

## Decision

- `runtime/py/modules.json` is the only module registry. Each entry names the
  public module, its domain, and its implementation file.
- `runtime/paths.ts` reads the registry (or is generated from it), and
  `pyModuleFile` / `pyModuleNames` / `pyModuleExists` derive from it; the
  hand-written `PY_MODULE_DOMAIN` record is removed.
- `runtime/py/core/offline_bootstrap.py` resolves modules through the same
  registry; it has no independent domain list or flat fallback after F2.3.
- `tests/unit/python/test_runtime_layout.py`,
  `tests/unit/js/contract/runtime-layout.test.ts` and the native E2E manifest
  assert that every registry entry exists on disk and every on-disk module is
  registered. A missing or stale entry fails the layout gate.
- F2.3 deletes the 38 flat `runtime/*.py` shims and moves `generator.py` /
  `uv_tools.py` into `geometry`. The registry is the migration map: remove the
  shim entry only after every importer uses the domain path.

## Consequences

- Adding a module is a one-line registry change plus the implementation file;
  there is no third copy to update.
- The registry is data, not logic; it is the right place for the loader's
  stable public names.
- The 38 shims disappear in F2.3; old flat imports become a documented breaking
  change in MIGRATIONS/CHANGELOG (Policy B).
- The layout tests become the drift guard: a registry/disk mismatch is a test
  failure, not a runtime surprise in Blender.
- The loader remains path-based; the registry chooses the path, it does not
  change the preload mechanism.

## Alternatives considered

- **Keep the three maps and add a sync script.** Rejected: a sync script still
  has to be run and can be forgotten; one source plus tests is simpler.
- **Generate `paths.ts` from the filesystem at build time.** Rejected: the
  published package must have a deterministic module list; a build-time
  filesystem scan makes the public surface depend on checkout contents.
- **Keep flat shims as the registry.** Rejected: the shims are the migration
  artifact Policy B removes; they cannot be the permanent source of truth.
