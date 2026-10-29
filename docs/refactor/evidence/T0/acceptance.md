# T0 — Standard environment, baseline verification, surface_fit decision

## Scope
§7.1 A: fix the standard Node 22 + DSH host dependency story, make `npm test` and
`npm run typecheck` runnable, confirm the frozen 34/228/15/39/11 surface, and decide
how the catalog-declared `surface_fit` op is handled.

## Result
PASS. The new layered test entry point (`npm test`) runs the Vitest unit/integration
suite and pytest; the legacy 28-step suite also passes on Windows when pointed at a
real Blender 4.2.10 and a UTF-8 Python stdout. surface_fit is now an explicit M3
refusal rather than a missing registration.

## Acceptance

| ID | Result | Evidence | Reviewer | Date |
|---|---|---|---|---|
| T0.1 `npm run typecheck` exits 0 on a standard Node 22 | PASS | typecheck.log | author self-check | 2026-10-30 |
| T0.2 `npm test` runs the new stack (Vitest 30 + pytest 5) and exits 0 | PASS | test.log | author self-check | 2026-10-30 |
| T0.3 Legacy 28-step suite passes on Windows + Blender 4.2.10 | PASS | legacy.log | author self-check | 2026-10-30 |
| T0.4 Frozen surface confirmed: 34 families / 228 ops / 15 tools / 39 addon commands / protocol 11 | PASS | counts.json + `node scripts/sync_counts.mjs --dry` | author self-check | 2026-10-30 |
| T0.5 surface_fit decision recorded and implemented (honest `ok:false`, no fake success) | PASS | runtime/surface.py + tests/unit/js/contract/surface-fit.test.ts + T2/test.log | author self-check | 2026-10-30 |

## Notes
- The standard Node binary at D:/DSH/tools was copied to
  C:/Users/villa/AppData/Local/node22test because the DSH file sandbox lets that
  node.exe write only inside D:/DSH (EPERM on D:/project and TEMP). The binary and
  version are unchanged.
- The legacy suite initially failed at the Python selftest with
  `UnicodeEncodeError: 'gbk' codec can't encode character '/u2713'`; setting
  PYTHONIOENCODING=utf-8/PYTHONUTF8=1 made it pass. This is an environment fix,
  not a product-logic change.
- D2 of the legacy lease test needs a real Blender executable; the Windows
  portable Blender 4.2.10 was installed under D:/DSH/tools for this purpose.
- `npm test` now has a Python dependency: install requirements-dev.txt into a venv
  and point `PYTHON` at it.
