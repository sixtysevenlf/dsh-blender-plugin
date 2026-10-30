# v3.0.0 release notes (draft)

> Status: release candidate. Tag, GitHub Release, and npm publish are intentionally deferred until the Gate C5 decision (see docs/refactor/evidence/T12/exemptions.md).

## Breaking changes

- **The Blender addon is now a zip package.** Install `addon/dsh_blender_bridge.zip` (package root `dsh_blender_bridge/`, `bl_info` v1.11, protocol 11). The old single-file `dsh_blender_bridge.py` is no longer the install entry and is not shipped. Disable/remove the old single-file addon in Blender Preferences first, then install the zip, enable it, and press Connect in the N panel.
- **`lib/` is no longer tracked in git.** Runtime source is strict TypeScript under `runtime/**/*.ts` and compiles to `lib/runtime/**/*.js`. Git users run `npm ci --legacy-peer-deps && npm run build`; npm installs still ship `lib/` via `prepack`.
- Directly requiring the old `runtime/*.mjs` files from a git checkout no longer works (runtime has no `.mjs`); use the package entry or built `lib/runtime/*.js`.

## Highlights

- `src/index.ts` is a 13-line entry-only file; 15 tool handlers live in `src/tools/*`; 32/32 target files, 147 import edges, 0 cycles.
- `runtime/engine` / `runtime/server` are facades over `core/`, `execution/`, `http/`, `transport/`; 87 engine exports and 29 routes preserved; largest new module 457 lines.
- Python runtime split into `runtime/py/{core,geometry,assembly,render,export}` with flat re-export shims; `offline_bootstrap` isolates each module namespace (fixes the `_num` helper collision that broke assembly export).
- Addon split into `addon/dsh_blender_bridge/` (39 modules, max 665 lines) plus a committed zip; launcher validates package/zip, protocol 11, addon version >= 1.11.
- Runtime migrated to strict TypeScript (Phase R): 62 modules, no `@ts-nocheck`, no new explicit `any`; `SERVER_PATH` points at `lib/runtime/server.js`.
- Three test layers: unit (37 files / 251 passed), integration (17 files / 102 passed), python (295 passed); real Blender 4.2.10 E2E headless 29 passed and GUI 6 passed; behavior matrix 347 rows.
- CI workflows for PR gates and release E2E; clean-clone gate logs in `docs/refactor/evidence/T12/clean-clone.log`.

## Upgrade

1. Stop DSH and Blender.
2. Blender: disable/remove the old single-file `dsh_blender_bridge.py`, install `addon/dsh_blender_bridge.zip`, enable **DSH Blender Bridge**, open the N panel and press Connect.
3. DSH: install plugin v3.0.0 into the profile, restart DSH.
4. Verify: `blender_rt_cmd get_addon_info` -> addon `[1,11]`, protocol `11`; `blender_viewport(op="doctor")` -> `kind=ok`; `blender_rt_plan(op="catalog")` -> 34 families / 228 ops.

Full upgrade, uninstall, and rollback steps: [MIGRATIONS.md](../../../MIGRATIONS.md).

## Compatibility

- 15 tools, 34 families / 228 ops, 39 addon commands, protocol 11: unchanged from v2.6.2.
- No modeling features were added in v3.0.0.
- B1 regression uses v2.6.1 only and is archived separately; v3.0.0 scores are not mixed with it.

## Known limitations

- C5 coverage target (80/70) is not met: JS diff 32%, Python addon 32%; issue #52 tracks the gap and ratchet.
- T16 release actions (tag `v3.0.0`, GitHub Release, npm publish) are pending the C5 decision.
- B1 3x P0/P1 regression (D4) is still running at the time this note was written; see `docs/refactor/evidence/T16/b1-regression/`.
