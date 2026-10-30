# Tests

Three layers, one runner per runtime. Contract tests are **not** a fourth layer; they live inside the
unit and integration trees (`tests/unit/js/contract/`, `tests/integration/js/contract/`).

## Layers

| Layer | Runtime | Location | Needs |
|---|---|---|---|
| Unit | Node / TypeScript, Python | `tests/unit/{js,python}/` | nothing external |
| Integration | Node / TypeScript, Python | `tests/integration/{js,python}/` | loopback sockets, fake addon, fake DSH host |
| End-to-end | real Blender, GUI | `tests/e2e/{blender,gui}/` | a Blender executable; run on demand and at release |

The unit and integration layers must pass without Blender, a display, the network, or a DSH provider.
The end-to-end layer installs the addon zip into a real Blender and is never part of the pull-request
gate.

## Commands

The canonical command table is [AGENTS.md §1](../AGENTS.md#1-commands-single-source). The layer commands
are:

```text
npm run test:unit          # Vitest, tests/unit
npm run test:integration   # Vitest, tests/integration
npm run test:contract      # Vitest, contract tests only
npm run test:python        # pytest, PYTHON must point at the venv interpreter
npm test                   # Vitest plus pytest
```

Python runs under a virtualenv: `PYTHON=/path/to/venv/bin/python`, with `PYTHONIOENCODING=utf-8` and
`PYTHONUTF8=1` on Windows. On this repository's Windows development machine the interpreter is
`D:/DSH/tools/dsh-test-venv/Scripts/python.exe`.

## Layout

```text
tests/
  unit/js/            Vitest suites for src/ and runtime/
  unit/js/contract/   contracts: catalog, tool schema, lossless, client bundle, routes
  unit/python/        pytest for runtime/*.py
  integration/js/     server and transport against the fake addon
  integration/python/ pytest across module boundaries
  e2e/blender/        real Blender: install zip, register, Connect, bpy, export
  e2e/gui/            optional GUI probes
  fixtures/           small JSON/Python/blend fixtures, capability lock, baselines
  helpers/            fake addon, fake DSH host, package paths
```

**Current state (2026-10-30):** `unit/js` and `integration/js` exist. The `python` and `e2e` trees
are target directories created by T11/T12; the legacy `*_selftest.*` files at the root of `tests/` are
shrink-only until `test:legacy` is removed (C4).

## Fixture rules

- Deterministic: no clock, random seed, or network in the fixture; fixed ids and byte-stable JSON.
- Small: JSON, Python, and tiny `.blend` files only; no renders, video, or model downloads.
- No user state: never read the real DSH profile, `~`, or the Blender user preferences.
- Golden files are reviewed like code; regenerate only with the documented script and say why in the PR.
- The capability lock is generated, never hand-edited.

## Where the rest lives

- Coverage requirements and gate definitions: [docs/refactor/spec.md](../docs/refactor/spec.md) §5.3.
- Row-level behavior coverage: [BEHAVIOR_MATRIX.md](BEHAVIOR_MATRIX.md) (owned by T14).
- Test-stack rationale: [ADR 0002](../docs/adr/0002-test-stack.md).
