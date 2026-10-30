# T8 — Phase E: split runtime/*.py into five domain packages

## Scope
Plan §4.7: move the 39 listed `runtime/*.py` modules into
`runtime/py/{core,geometry,assembly,render,export}`, add package `__init__.py`
files, leave the old flat paths as one-version re-export-only shims, update engine
injection and cross-module file lookup, and explicitly handle the two unlisted
modules `generator.py` / `uv_tools.py`. `runtime/ext/open3d_check.py` moves to the
export domain. No Node/JS behavior change and no tool/op/route/schema rename.

## Result
**PASS for the stated T8 scope** (author self-check; no independent review claimed).
39 real modules + 6 package `__init__.py` files + `assembly_features.json` land
under `runtime/py/`; 38 old flat paths are pure shims; `generator.py` and
`uv_tools.py` stay real flat modules. Engine injection, preload checks, open3d
runner, contract features JSON, qc→qc_render and qc_render→montage resolution all
use the new layout. Python imports and all 40 flat paths were import-smoked inside
Blender 4.2.10.

## Acceptance

| ID | Result | Evidence | Reviewer | Date |
|---|---|---|---|---|
| T8.1 Five domain packages exist with exactly the §4.7 module sets; assembly JSON moved with assembly; open3d moved to export | PASS | test.log (pytest), tests/unit/python/test_runtime_layout.py, runtime/py/ | author self-check | 2026-10-30 |
| T8.2 All 38 old flat `runtime/*.py` are one-version re-export-only shims (no functions/classes/assignments) | PASS | tests/unit/python/test_runtime_layout.py + tests/unit/js/contract/runtime-layout.test.ts; test.log | author self-check | 2026-10-30 |
| T8.3 Old imports work in both forms: repo-root `runtime.<name>` and flat `<name>` with only `runtime/` on sys.path | PASS | test.log (blender_import_all, blender_import_flat; pytest subprocess test) | author self-check | 2026-10-30 |
| T8.4 Engine reads/injects real package files (`pyModuleFile`), preload error lists real names, open3d runner path updated | PASS | build.log (engine import/syntax), runtime/engine.mjs, runtime/paths.mjs, runtime-layout.test.ts | author self-check | 2026-10-30 |
| T8.5 Cross-package file lookup works: assembly_features.json, qc→qc_render, qc_render→montage | PASS | test.log (blender_domain_loaders), runtime/py/core/kit.py `find_runtime_file` | author self-check | 2026-10-30 |
| T8.6 offline bootstrap resolves modules across all five packages (vehicle/audit/qc APIs) | PASS | test.log (blender_offline_bootstrap) | author self-check | 2026-10-30 |
| T8.7 generator.py / uv_tools.py remain real flat modules and import; documented in review.md | PASS | test.log (blender_import_all/flat), runtime/paths.mjs map `'.'` | author self-check | 2026-10-30 |
| T8.8 No module-level scene modification; Python sources import no Node APIs; shims are import-only | PASS | tests/unit/python/test_runtime_layout.py AST checks | author self-check | 2026-10-30 |
| T8.9 Python suite green (14 passed) and JS suite green (34 passed) | PASS | test.log: pytest 14 passed; vitest 10 files / 34 tests | author self-check | 2026-10-30 |
| T8.10 npm package dry-run includes `runtime/py/**` and flat shims, excludes ext/venv and pycache | PASS | build.log: 46 runtime/py entries, 40 flat runtime .py, no forbidden entries | author self-check | 2026-10-30 |

## Not claimed / follow-ups
- No independent review (B3 is R2; author self-check only).
- Legacy Blender selftests that exec flat source by path were not migrated; pytest/CI
  does not run them. See review.md residual risk 1.
- Old offline-bootstrap CLI path and old `runtime/assembly_features.json` path are
  intentionally not shimmed (see review.md risks 2–4).
- `scripts/test.mjs` JS phase is blocked in this worktree by the shared
  `node_modules` junction EPERM; `--configLoader runner` was used. See env.txt.
