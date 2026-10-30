# T8 review

- Reviewer: author (self-check). **No independent reviewer was involved**; B3 is R2
  and this document does not claim an independent review.
- Scope: plan §4.7 — five domains under `runtime/py/`, old flat paths as one-version
  re-export shims, engine injection updated to real files, unlisted modules handled
  explicitly, evidence + package dry-run.

## What was checked

- **Package split matches §4.7 exactly**: core 15, geometry 8, assembly 4 + JSON,
  render 9, export 3. `tests/unit/python/test_runtime_layout.py` asserts the exact
  file sets, and `tests/unit/js/contract/runtime-layout.test.ts` asserts the same
  through `runtime/paths.mjs`.
- **Shims contain no business logic**: all 38 flat shims parse as a single
  `try` with one `from runtime.py.<domain>.<name> import *`, a guarded
  `ModuleNotFoundError` fallback to `from py.<domain>.<name> import *`, and no
  function/class/assignment. Tested in Python AST and by checking that all flat
  modules import from both repo-root and flat-only `sys.path` forms in Blender.
- **Old import paths**: `import runtime.<name>` (repo root on `sys.path`) and
  `import <name>` (only `runtime/` on `sys.path`) both resolve to the real module.
  Function identity is asserted for `kit` and `offline_bootstrap`.
- **Engine injection**: `runtime/engine.mjs` now resolves module source through
  `pyModuleFile(name)`; job/headless preload existence checks and error hints use
  the same map; `runtimeFingerprint()` keeps its old key set (behavior freeze) but
  hashes the real package file for each flat `.py` key. `runtime/paths.mjs` owns the
  single name → domain map (41 names: 39 plan modules + generator/uv_tools).
- **Cross-package loading**: `kit.find_runtime_file()` is the shared resolver for
  `contract._features_path()`, `qc._load_qc_render()`, and
  `qc_render._montage_api()`. Verified inside Blender: features JSON resolves to
  `runtime/py/assembly/assembly_features.json`, qc→qc_render and
  qc_render→montage both load.
- **No import-time scene changes**: AST check over every real module excludes
  module-level `bpy`/`bmesh`/`numpy` name usage; bpy imports remain at module top
  as before (existing code style), but no scene operation runs at import.
- **Unlisted modules**: `generator.py` and `uv_tools.py` remain real flat modules
  (not shims, not moved), because §4.7 does not assign them a domain. They are in the
  engine name map with domain `'.'` and are covered by the flat import smoke.
- **open3d_check**: moved from `runtime/ext/` to `runtime/py/export/`; its old
  unconditional `main()` now has an `if __name__ == "__main__"` guard, and the engine
  external runner path points at the new file. No `runtime/ext/open3d_check.py` shim
  was left (that path was never a §4.7 `runtime/*.py` shim target).
- **offline_bootstrap**: resolves modules across the five package directories and
  accepts either the runtime root or `runtime/py/core` as the runtime dir. Verified
  in Blender with `vehicle audit qc` (APIs present).
- **No Node imports / no JS behavior changes**: Python sources import only
  stdlib/bpy/numpy/mathutils and never Node APIs. No tool/op/route/schema was added
  or renamed; the public module-name strings (`"module": "<name>.py"`, preload
  names, `K.dsh_<name>_api`) are unchanged.

## Residual risks / un-migrated items

1. **Legacy selftests that exec flat source by path were not migrated.** Files such
   as `tests/geo_selftest.py`, `tests/plan_selftest.py`,
   `tests/assembly*_selftest.py`, `tests/surface_selftest.py`,
   `tests/gate_eval_selftest.py`, `tests/contract_selftest.py`,
   `tests/motion_selftest.py`, `tests/view_diag_selftest.py`,
   `tests/audit_file_selftest.py` and the `K.runtime_dir` fallbacks in
   `tests/deliver_selftest.py` / `tests/txn*_selftest.py` still read or exec
   `runtime/<name>.py`. They are outside pytest/CI and were not part of the required
   evidence commands; exec-by-path now depends on whether `runtime.py` or `py` is
   importable. They need a follow-up mechanical migration to the new paths (single
   helper recommended). Not claimed green.
2. **CLI path for offline bootstrap changed.** The documented invocation is now
   `--python runtime/py/core/offline_bootstrap.py`. The old
   `runtime/offline_bootstrap.py` is an import shim and is **not runnable as a
   script** (per the "shims only re-export" rule). External scripts using the old CLI
   path break and must be updated.
3. **assembly_features.json old path is gone** (moved, not shimmed, because it is data
   rather than a Python module). The module-facing lookup is updated; any external
   tool reading `runtime/assembly_features.json` directly must use
   `runtime/py/assembly/assembly_features.json`.
4. **External callers of `runtime/ext/open3d_check.py` break.** The engine was
   updated; no compatibility copy was left because the old location is outside the
   `runtime/*.py` shim contract. A one-version wrapper there is possible if desired.
5. **`runtimeFingerprint()` semantics changed but not its keys.** The plugin status
   diagnostic now hashes the real package file behind each flat `.py` key. The JSON
   key set is unchanged; values change once on this commit.
6. **JS test entry point in this worktree.** `node scripts/test.mjs` (JS phase) is
   blocked by the shared `node_modules` junction: Vite's bundle config loader gets
   EPERM writing `node_modules/.vite-temp`. The suite was run with
   `--configLoader runner`; on a normal checkout `npm test` is expected to work.
   This is environmental, not code.
7. **No independent review**, as stated at the top.
