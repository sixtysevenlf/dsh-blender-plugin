# 0002. Test stack: Vitest, pytest, and an in-house Blender harness

- **Status**: Accepted
- **Date**: 2026-10-30
- **Deciders**: plugin maintainers
- **Target**: layers C1-C8

## Context

The repository tests three different runtimes: the TypeScript host and backend on Node, the Python
runtime and Blender addon logic, and the real Blender application (registration, operators, bpy, export).
A single runner cannot cover all three honestly. The old suite is a flat set of `*_selftest.mjs` and
`*_selftest.py` scripts, some over a thousand lines, run through a hand-written chain in
`package.json`; it is slow to select, hard to run per file, and reports no per-test structure.

The refactor plan (§6.2, §6.9, §6.10) defines three layers — unit, integration, end-to-end — and forbids
a fourth "contract" layer: contract tests live inside unit and integration. The plan also rejects external
Blender test frameworks.

## Decision

- **JavaScript and TypeScript: Vitest.** Unit tests run with a fake DSH host and a fake addon over
  in-memory transport; integration tests run the real HTTP server against the fake addon. Vitest loads
  TypeScript directly, so tests import `src/` and `runtime/` sources.
- **Python: pytest** under a project virtualenv. The interpreter is selected with the `PYTHON`
  environment variable so Windows, Linux, and CI use the same command; `PYTHONIOENCODING=utf-8` and
  `PYTHONUTF8=1` are set on Windows.
- **Blender: an in-house harness.** The end-to-end layer starts a real Blender in background mode,
  installs the addon package from the release zip, registers it, connects over the socket, and drives
  commands and bpy execution. It runs only on demand and on release candidates, never in the pull-request
  gate.
- Fixtures are hermetic: no network, no real DSH provider, no user profile, no committed binary assets
  beyond small JSON, Python, and blend fixtures.

### Third-party maintenance assessment

| Option | State in 2026-10 | Why it is not the backbone |
|---|---|---|
| `pytest-blender` 3.0.8 | blocked on upstream issue #103 | The plugin cannot serve our install-from-zip flow reliably; adopting it would couple the release gate to an unresolved bug. |
| `blender-addon-tester` | last commit 2025-03-20 | Effectively unmaintained for over a year; our addon targets Blender 4.2 LTS and 5.x, and the tool's supported matrix lags. |
| `fake-bpy-module` | maintained | Provides import stubs only: no `bpy` runtime, no addon registration, no operators, no render. Useful for editor completion, not for acceptance. |

The in-house harness is roughly 300 lines of Python plus fixtures. It is small enough to own and it tests
the exact installation path users take. We revisit the table once a year and on every Blender LTS release.

## Consequences

- Pull requests stay fast: unit, integration, and Python unit tests need neither Blender nor a display.
- The release gate is slower but honest: the end-to-end job proves zip install, Connect, doctor, bpy
  execution, export, and uninstall on the supported Blender versions.
- We own the harness. When Blender changes its extension or addon loading rules, we fix it ourselves.
- The three layers can run independently, which is what gate C1 checks.

## Alternatives considered

- **pytest-blender as the addon harness.** Blocked upstream and does not match the zip install flow.
- **blender-addon-tester.** Stale maintenance and an older Blender matrix.
- **fake-bpy only.** Cannot prove registration, socket behavior, or rendering; it would turn acceptance
  into a mock test.
- **One Jest/Vitest suite for everything.** Cannot execute Blender's Python runtime or the addon, and
  would hide the Python/bpy boundary behind mocks.

## Links

- Specification: [docs/refactor/spec.md](../refactor/spec.md), gates C1-C8
- Test layers and fixtures: [tests/README.md](../../tests/README.md)
- Behavior matrix (owned by T14): [tests/BEHAVIOR_MATRIX.md](../../tests/BEHAVIOR_MATRIX.md)
