# Tests

Three layers, one rule: a test belongs to the cheapest layer that can still catch the defect.
The layers are Unit, Integration, and End-to-end. A "legacy" selftest script is **not** a fourth layer:
those scripts were retired from the default chain in T11 and every one is mapped in
[MIGRATION.md](MIGRATION.md).

## Unit

`tests/unit/js` (Vitest) and `tests/unit/python` (pytest).

Unit tests never start Blender, the HTTP backend, the add-on socket, a browser, or a DSH
process; no real network, no fixed ports, no `process.exit`. They cover pure logic, contracts,
fixtures and static source guards.

Run: `npm run test:unit`, `npm run test:contract`, or `npm run test:python`.

## Integration

`tests/integration/js` and `tests/integration/python`.

Integration tests may start the local backend (`lib/runtime/server.js`), the fake add-on socket, or a
fake DSH host context, but never a real Blender GUI. Ports are ephemeral, every server is stopped
in a fixture, and assertions are explicit (no retrying to hide flakes).

Run: `npm run test:integration`.

## End-to-end

`tests/e2e/blender` and `tests/e2e/gui`.

Real Blender 4.2 and the packaged add-on, plus a GUI/backend probe. These tests are gated with
`pytest.mark.blender` / `pytest.mark.gui` and are excluded from the default collection by
`pytest.ini`. They run on release machines, not in pull-request CI.

T11b final shape (see plan §6.5.1):
- H native headless modules: `tests/e2e/blender/native/*.py` run inside Blender by
  `native_runner.py`; host launcher `test_native_e2e.py`.
- G native GUI modules: `tests/e2e/gui/native/*.py` need a real window (OpenGL capture);
  host launcher `test_native_gui.py`.
- B host-side backend test: `tests/e2e/gui/test_qc_backend.py` talks HTTP to a running backend,
  no Blender; skips with a reason when the backend is absent.
- M manual probe: `node tools/gui-channel-probe.mjs` against a GUI Blender + add-on + backend.

Some archived-case wrappers still exist as an intermediate state (`MIGRATION.md`); T11b.8/T11b.9
converts and removes them.

Headless native: `DSH_BLENDER_EXE=/path/to/blender pytest tests/e2e -m "blender and not gui" -v`
GUI + backend: `DSH_BLENDER_EXE=/path/to/blender pytest tests/e2e -m gui -v`

## Default run

`npm test` = Vitest (unit + integration) + pytest (unit + integration, `blender`/`gui` excluded).
`node scripts/test.mjs js|python|unit|integration|contract` selects a slice.

## Fixtures and helpers

`tests/fixtures` holds stable snapshots (`capability.lock.json`, `plan_readonly.baseline.json`).
`tests/helpers` holds the fake add-on/backend/DSH context, the `bpy` stub, docs helpers, and the
shared `backend.ts` process harness. Keep helpers free of assertions.

## Adding a test

1. Pick the layer by dependency, not by convenience.
2. Put the file under `tests/<layer>/<js|python>/` with a descriptive name.
3. Reuse `tests/helpers`; add a fixture only when two tests need the same non-trivial setup.
4. Never use `retry`, a bare `skip`, or `pytest.ini` overrides to make a red test green.