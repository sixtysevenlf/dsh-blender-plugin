# Legacy test migration ledger (T11)

Every file that lived in `tests/` before the refactor is listed here with a disposition. Status is `migrated` / `merged` / `moved` / `replaced` / `rewritten`; there is no `pending` row. The legacy `npm run test:legacy` chain was removed from `package.json` in T11.

Targets were written first, then each legacy file was deleted (plan section 6.7). Unit and integration tests run in the default `npm test`; every real-Blender/GUI E2E case is release-gated and excluded from the default pytest collection.

**E2E status (final):** the archived-case + legacy-runner intermediate is gone as of T11b.9. Every E2E legacy file now targets a native module: H headless (`tests/e2e/blender/native/`, `test_native_e2e.py`), G GUI (`tests/e2e/gui/native/`, `test_native_gui.py`), B host backend (`tests/e2e/gui/test_qc_backend.py`) or M manual probe (`tools/gui-channel-probe.mjs`). `npm run test:e2e` and `npm run test:e2e:gui` are green; C4/C8 evidence is in T11b.9.

## 1. Unit layer (JS)

| Legacy file | Disposition | Target | Status | Notes |
| --- | --- | --- | --- | --- |
| tests/capability_lock.mjs | split | tests/unit/js/contract/catalog.test.ts + tests/fixtures/capability.lock.json | migrated | catalog/tool budget assertions read the moved capability fixture |
| tests/single_source_selftest.mjs | migrated | tests/unit/js/contract/single-source.test.ts | migrated |  |
| tests/lossless_guard.mjs | merged | tests/unit/js/contract/lossless.test.ts | migrated | merged with lossless/sanitize receipts contract tests |
| tests/discoverability_selftest.mjs | split | tests/unit/js/contract/description-budget.test.ts | migrated |  |
| tests/guidance_selftest.mjs | migrated | tests/unit/js/contract/guidance.test.ts | migrated |  |
| tests/schema_resolve_selftest.mjs | split | tests/unit/js/contract/tool-schema.test.ts | migrated | snapshots replaced by explicit assertions |
| tests/client_bundle_selftest.mjs | migrated | tests/unit/js/contract/client-bundle.test.ts | migrated |  |
| tests/sidebar_injection_selftest.mjs | migrated | tests/unit/js/contract/sidebar-injection.test.ts | migrated | asserts the v2.3 removed-pane contract |
| tests/tool_cards_selftest.mjs | migrated | tests/unit/js/contract/tool-cards.test.ts | migrated |  |
| tests/skill_bundle_selftest.mjs | migrated | tests/unit/js/contract/skill-bundle.test.ts | migrated |  |
| tests/route_contract_selftest.mjs | migrated | tests/unit/js/contract/route-contract.test.ts | migrated |  |
| tests/frame_dedupe_selftest.mjs | migrated | tests/unit/js/frame-dedupe.test.ts | migrated |  |
| tests/headless_file_guard_selftest.mjs | migrated | tests/unit/js/headless-file-guard.test.ts | migrated |  |
| tests/headless_hard_exit_selftest.mjs | migrated | tests/unit/js/headless-hard-exit.test.ts | migrated |  |
| tests/lease_stale_selftest.mjs | migrated | tests/unit/js/lease-stale.test.ts | migrated |  |
| tests/provenance_stale_selftest.mjs | migrated | tests/unit/js/provenance.test.ts | migrated |  |
| tests/scene_hygiene_selftest.mjs | migrated | tests/unit/js/scene-hygiene.test.ts | migrated |  |
| tests/workdir_shared_selftest.mjs | migrated | tests/unit/js/workdir.test.ts | migrated |  |
| tests/shape_search_selftest.mjs | migrated | tests/unit/js/shape-search.test.ts | migrated |  |
| tests/ledger_recover_selftest.mjs | migrated | tests/unit/js/ledger-recover.test.ts | migrated |  |
| tests/linux_selftest.mjs | merged | tests/unit/js/platform-paths.test.ts | merged | merged with mac_selftest.mjs into a platform decision table |
| tests/mac_selftest.mjs | merged | tests/unit/js/platform-paths.test.ts | merged | merged with linux_selftest.mjs; macOS branch skipIf on this host |
| tests/windows_hide_selftest.mjs | migrated | tests/unit/js/static/windows-hide.test.ts | migrated | static source guard |`

## 2. Integration layer (JS)

| Legacy file | Disposition | Target | Status | Notes |
| --- | --- | --- | --- | --- |
| tests/host_routes_selftest.mjs | split | tests/integration/js/host-selection.test.ts + host-jobs.test.ts + host-scene-items.test.ts | migrated | CORS/deleted-route assertions included |
| tests/lease_scope_selftest.mjs | migrated | tests/integration/js/lease-scope.test.ts | migrated |  |
| tests/ledger_recover_e2e_selftest.mjs | split | tests/integration/js/ledger-recover-e2e.test.ts | migrated | recovery from jobs index.jsonl; real-headless branch lives in E2E layer |
| tests/launch_selftest.mjs | split | tests/integration/js/launch.test.ts | migrated | cross-platform subset; POSIX fake-blender spawn is release-gated E2E (exempt here) |
| tests/backend_probe_selftest.mjs | migrated | tests/integration/js/backend-probe.test.ts | migrated |  |
| tests/selection_store_selftest.mjs | migrated | tests/integration/js/selection-store.test.ts | migrated | imports the pure module, no backend needed |
| tests/dsh_api_compat_probe.mjs | migrated | tests/integration/js/dsh-api-compat.test.ts | migrated | matrix-driven; host-package probe skips when no DSH tree is installed |
| tests/tui_host_selftest.mjs | split | tests/integration/js/tui-selection.test.ts + tui-footer.test.ts + tui-commands.test.ts | migrated |  |
| tests/view_diag_selftest_runner.mjs | moved | tests/e2e/blender/native/view_diag.py + tests/e2e/gui/native/view_capture.py | migrated | T11b.9 final shape: headless diagnostics + GUI OpenGL capture split |
| tests/gui_channel_probe.mjs | moved | tools/gui-channel-probe.mjs | migrated | T11b.7 manual probe (M layer): not a pytest test; needs a real GUI Blender + connected add-on + backend |`

## 3. Unit layer (Python)

| Legacy file | Disposition | Target | Status | Notes |
| --- | --- | --- | --- | --- |
| tests/gate_eval_selftest.py | migrated | tests/unit/python/test_gate_eval.py | migrated | AST whitelist + 15 equivalence + 11 rejection cases |
| tests/generator_dispatch_selftest.py | migrated | tests/unit/python/test_generator_dispatch.py | migrated | code-vs-script and nested-args field bugs pinned |
| tests/modules_selftest.py | replaced | tests/unit/python/test_import_smoke.py + per-module tests | migrated | Blender-only modules stay in the Blender E2E layer; dependency boundary pinned |`

## 4. End-to-end layer (final T11b shape)

The archived-case + wrapper intermediate is gone: the legacy runner modules,
upstream_support.py, tests/e2e/blender/cases/ and tests/e2e/gui/cases/ were deleted
in T11b.9. The final layers are:

- H native headless modules: tests/e2e/blender/native/*.py (plus native/upstream/*.py),
  run inside a real Blender 4.2 --background process by native_runner.py; host launcher
  test_native_e2e.py.
- G native GUI modules: tests/e2e/gui/native/*.py (OpenGL capture/evidence), run in a
  real non-background Blender window by the same runner; host launcher test_native_gui.py.
- B host-side backend test: tests/e2e/gui/test_qc_backend.py talks HTTP to a running
  backend and never starts Blender; skips with a reason when no backend is listening.
- M manual probe: tools/gui-channel-probe.mjs, not collected by pytest.
- Existing native E2E: test_addon_e2e.py, test_gui_connect.py, blender_42_headless.mjs.

Commands: npm run test:e2e (H + addon; 76 passed on Blender 4.2.10) and
npm run test:e2e:gui (G + B; 9 passed / 1 skipped without a backend, 10 passed with a
running backend).

| Legacy file | Disposition | Target | Status | Notes |
| --- | --- | --- | --- | --- |
| tests/assembly_selftest.py | migrated | tests/e2e/blender/native/assembly.py | native | native/assembly.py (H) |
| tests/assembly_export_selftest.py | migrated | tests/e2e/blender/native/assembly_export.py | native | native/assembly_export.py (H) |
| tests/assembly_repair_selftest.py | migrated | tests/e2e/blender/native/assembly_repair.py | native | native/assembly_repair.py (H) |
| tests/audit_file_selftest.py | migrated | tests/e2e/blender/native/audit_file.py | native | native/audit_file.py (H) |
| tests/audit_review_regression.py | migrated | tests/e2e/blender/native/audit_review.py | native | native/audit_review.py (H) |
| tests/blockout_selftest.py | migrated | tests/e2e/blender/native/blockout.py | native | native/blockout.py (H) |
| tests/blockout_refsheet_selftest.py | migrated | tests/e2e/blender/native/blockout_refsheet.py | native | native/blockout_refsheet.py (H) |
| tests/contract_selftest.py | migrated | tests/e2e/blender/native/contract.py + tests/e2e/gui/native/contract_evidence.py | native | native/contract.py (H core) + gui/native/contract_evidence.py (G evidence) |
| tests/deliver_selftest.py | migrated | tests/e2e/blender/native/deliver.py | native | native/deliver.py (H) |
| tests/evalharness_selftest.py | migrated | tests/e2e/blender/native/evalharness.py | native | native/evalharness.py (H) |
| tests/gate_review_regression.py | migrated | tests/e2e/blender/native/gate_review.py | native | native/gate_review.py (H) |
| tests/geo_selftest.py | migrated | tests/e2e/blender/native/geo.py | native | native/geo.py (H) |
| tests/img_digest_selftest.py | migrated | tests/e2e/blender/native/imgtools.py | native | native/imgtools.py (H) |
| tests/mate_selftest.py | migrated | tests/e2e/blender/native/mate.py | native | native/mate.py (H) |
| tests/motion_selftest.py | migrated | tests/e2e/blender/native/motion.py | native | native/motion.py (H) |
| tests/p0_real_modeling_selftest.py | migrated | tests/e2e/blender/native/p0_real_modeling.py | native | native/p0_real_modeling.py (H) |
| tests/plan_selftest.py | migrated | tests/e2e/blender/native/plan.py | native | native/plan.py (H) |
| tests/qc_selftest.py | migrated | tests/e2e/gui/test_qc_backend.py | native | gui/test_qc_backend.py (B; no Blender, skips without a backend) |
| tests/shapesolve_selftest.py | migrated | tests/e2e/blender/native/shapesolve.py | native | native/shapesolve.py (H) |
| tests/surface_selftest.py | migrated | tests/e2e/blender/native/surface.py | native | native/surface.py (H) |
| tests/txn_selftest.py | migrated | tests/e2e/blender/native/txn.py | native | native/txn.py (H) |
| tests/txn_restore_selftest.py | migrated | tests/e2e/blender/native/txn_restore.py | native | native/txn_restore.py (H) |
| tests/view_diag_selftest.py | migrated | tests/e2e/blender/native/view_diag.py + tests/e2e/gui/native/view_capture.py | native | native/view_diag.py (H core) + gui/native/view_capture.py (G capture) |
| tests/visualreport_selftest.py | migrated | tests/e2e/blender/native/visualreport.py | native | native/visualreport.py (H) |
| tests/upstream_integration_selftest.py | split | tests/e2e/blender/native/upstream/upstream_{sculpt,mesh_fix,uv,print,sweep,audit_smoke,material_render,gate,assembly_vehicle,shape,pipeline}.py | native | T11a split the 856-line / 23-function script; T11b.8 moved the 11 topics to the native runner and deleted the wrappers, upstream_support.py and cases/. 138/138 checks (127 clauses + 11 clean-scene guards) true on Blender 4.2.10 headless. |

## 5. Moved or kept files

| Legacy file | Disposition | Target | Status | Notes |
| --- | --- | --- | --- | --- |
| tests/skill_probe.py | moved | tools/skill_probe.py | moved | manual probe, not part of the pytest stack |
| tests/engine_probe.sh | moved | scripts/engine-probe.sh | moved | manual probe, not part of the pytest stack |
| tests/png_decode.py | moved | tests/helpers/png_decode.py | moved | shared helper |
| tests/capability.lock.json | moved | tests/fixtures/capability.lock.json | moved | byte-identical duplicate removed |
| tests/plan_readonly.baseline.json | moved | tests/fixtures/plan_readonly.baseline.json | moved | byte-identical duplicate removed |
| tests/README.md | kept | tests/README.md | rewritten | English rewrite, <=80 lines, three-layer description |`

## 6. Removed scripts (package.json)

Removed: `test:legacy` and its 22 per-selftest aliases (`test:matrix`, `test:discover`, `test:fileguard`, `test:hardexit`, `test:lease`, `test:workdir`, `test:provenance`, `test:recover`, `test:recover:e2e`, `test:hygiene`, `test:scope`, `test:generator`, `test:dedupe`, `test:diag`, `test:lossless`, `test:mac`, `test:linux`, `test:gui`, `test:lossless:live`, `test:dsh-api`, `test:routes`, `test:schema`, `test:search`). Retained entry points: `test`, `test:unit`, `test:integration`, `test:contract`, `test:python`, `test:watch` and the build/check/pack scripts. Note: the old `test:matrix` alias was removed with the legacy selftests; the current `test:matrix` in package.json points at the behavior-matrix gate, not the removed per-case alias.


## 7. Row count

Rows: 23 unit JS + 10 integration JS + 3 unit Python + 25 E2E + 6 moved/kept = **67**, matching the 67 files inventoried before T11. No row is `pending`; the only deferred execution is the release-gated real-Blender E2E run, which the plan excludes from PR CI.