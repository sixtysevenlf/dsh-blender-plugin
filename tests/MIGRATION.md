# Legacy test migration ledger (T11)

Every file that lived in `tests/` before the refactor is listed here with a disposition. Status is `migrated` / `merged` / `moved` / `replaced` / `rewritten`; there is no `pending` row. The legacy `npm run test:legacy` chain was removed from `package.json` in T11.

Targets were written first, then each legacy file was deleted (plan section 6.7). Unit and integration tests run in the default `npm test`; every real-Blender/GUI E2E case is release-gated and excluded from the default pytest collection.

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
| tests/view_diag_selftest_runner.mjs | moved | tests/e2e/blender/test_view_diag.py | migrated | launcher superseded by tests/e2e/blender/legacy_runner.py |
| tests/gui_channel_probe.mjs | moved | tests/e2e/gui/test_gui_channel.py + cases/gui_channel_probe.mjs | migrated | gui-marked; skipped unless DSH_BLENDER_GUI_E2E=1 |`

## 3. Unit layer (Python)

| Legacy file | Disposition | Target | Status | Notes |
| --- | --- | --- | --- | --- |
| tests/gate_eval_selftest.py | migrated | tests/unit/python/test_gate_eval.py | migrated | AST whitelist + 15 equivalence + 11 rejection cases |
| tests/generator_dispatch_selftest.py | migrated | tests/unit/python/test_generator_dispatch.py | migrated | code-vs-script and nested-args field bugs pinned |
| tests/modules_selftest.py | replaced | tests/unit/python/test_import_smoke.py + per-module tests | migrated | Blender-only modules stay in the Blender E2E layer; dependency boundary pinned |`

## 4. End-to-end layer (Blender, release-gated)

Each wrapper is `pytest.mark.blender`, executes the archived case in a real Blender 4.2 `--background --factory-startup` process via `tests/e2e/blender/legacy_runner.py`, and keeps the original assertions in `tests/e2e/blender/cases/`. The runner recreates the legacy `blender_rt_headless(preload=...)` environment with `runtime/py/core/offline_bootstrap.py`. `qc_selftest.py` additionally needs a GUI Blender + backend and is `pytest.mark.gui`.

| Legacy file | Disposition | Target wrapper | Status | Notes |
| --- | --- | --- | --- | --- |
| tests/assembly_selftest.py | migrated | tests/e2e/blender/test_assembly.py | migrated | Archived case + wrapper; executed in CI only on the release machine, not in this PR run. |
| tests/assembly_export_selftest.py | migrated | tests/e2e/blender/test_assembly_export.py | migrated | Archived case + wrapper; executed in CI only on the release machine, not in this PR run. |
| tests/assembly_repair_selftest.py | migrated | tests/e2e/blender/test_assembly_repair.py | migrated | Archived case + wrapper; executed in CI only on the release machine, not in this PR run. |
| tests/audit_file_selftest.py | migrated | tests/e2e/blender/test_audit_file.py | migrated | Archived case + wrapper; executed in CI only on the release machine, not in this PR run. |
| tests/audit_review_regression.py | migrated | tests/e2e/blender/test_audit_review.py | migrated | Archived case + wrapper; executed in CI only on the release machine, not in this PR run. |
| tests/blockout_selftest.py | migrated | tests/e2e/blender/test_blockout.py | migrated | Archived case + wrapper; executed in CI only on the release machine, not in this PR run. |
| tests/blockout_refsheet_selftest.py | migrated | tests/e2e/blender/test_blockout_refsheet.py | migrated | Archived case + wrapper; executed in CI only on the release machine, not in this PR run. |
| tests/contract_selftest.py | migrated | tests/e2e/blender/test_contract.py | migrated | Archived case + wrapper; executed in CI only on the release machine, not in this PR run. |
| tests/deliver_selftest.py | migrated | tests/e2e/blender/test_deliver.py | migrated | Archived case + wrapper; executed in CI only on the release machine, not in this PR run. |
| tests/evalharness_selftest.py | migrated | tests/e2e/blender/test_evalharness.py | migrated | Archived case + wrapper; executed in CI only on the release machine, not in this PR run. |
| tests/gate_review_regression.py | migrated | tests/e2e/blender/test_gate_review.py | migrated | Archived case + wrapper; executed in CI only on the release machine, not in this PR run. |
| tests/geo_selftest.py | migrated | tests/e2e/blender/test_geo.py | migrated | Archived case + wrapper; executed in this task on Blender 4.2.10 (evidence/T11/e2e.log). |
| tests/img_digest_selftest.py | migrated | tests/e2e/blender/test_imgtools.py | migrated | Archived case + wrapper; executed in CI only on the release machine, not in this PR run. |
| tests/mate_selftest.py | migrated | tests/e2e/blender/test_mate.py | migrated | Archived case + wrapper; executed in CI only on the release machine, not in this PR run. |
| tests/motion_selftest.py | migrated | tests/e2e/blender/test_motion.py | migrated | Archived case + wrapper; executed in CI only on the release machine, not in this PR run. |
| tests/p0_real_modeling_selftest.py | migrated | tests/e2e/blender/test_p0_real_modeling.py | migrated | Archived case + wrapper; executed in CI only on the release machine, not in this PR run. |
| tests/plan_selftest.py | migrated | tests/e2e/blender/test_plan.py | migrated | Archived case + wrapper; executed in this task on Blender 4.2.10 (evidence/T11/e2e.log) with preload=view. |
| tests/qc_selftest.py | migrated | tests/e2e/blender/test_qc.py | migrated | Archived case + wrapper; executed in CI only on the release machine, not in this PR run. gui; skipped unless DSH_BLENDER_GUI_E2E=1. |
| tests/shapesolve_selftest.py | migrated | tests/e2e/blender/test_shapesolve.py | migrated | Archived case + wrapper; executed in CI only on the release machine, not in this PR run. |
| tests/surface_selftest.py | migrated | tests/e2e/blender/test_surface.py | migrated | Archived case + wrapper; executed in CI only on the release machine, not in this PR run. |
| tests/txn_selftest.py | migrated | tests/e2e/blender/test_txn.py | migrated | Archived case + wrapper; executed in CI only on the release machine, not in this PR run. |
| tests/txn_restore_selftest.py | migrated | tests/e2e/blender/test_txn_restore.py | migrated | Archived case + wrapper; executed in CI only on the release machine, not in this PR run. |
| tests/upstream_integration_selftest.py | migrated | tests/e2e/blender/test_upstream_integration.py | migrated | Archived case + wrapper; NOT split into six domain files because the script's main() is not separable without rewriting its 856 lines. The six-domain split from plan 6.5 is recorded as a deliberate deviation, not as pending work: one wrapper keeps the full assertion set. Splitting it would run the same script six times and add no coverage. |
| tests/view_diag_selftest.py | migrated | tests/e2e/blender/test_view_diag.py | migrated | Archived case + wrapper; executed in CI only on the release machine, not in this PR run. |
| tests/visualreport_selftest.py | migrated | tests/e2e/blender/test_visualreport.py | migrated | Archived case + wrapper; executed in CI only on the release machine, not in this PR run. |`

## 5. Moved or kept files

| Legacy file | Disposition | Target | Status | Notes |
| --- | --- | --- | --- | --- |
| tests/skill_probe.py | moved | tools/skill_probe.py | moved | manual probe, not part of the pytest stack |
| tests/engine_probe.sh | moved | scripts/engine-probe.sh | moved | manual probe, not part of the pytest stack |
| tests/png_decode.py | moved | tests/helpers/png_decode.py | moved | shared helper |
| tests/capability.lock.json | moved | tests/fixtures/capability.lock.json | moved | byte-identical duplicate removed |
| tests/plan_readonly.baseline.json | moved | tests/fixtures/plan_readonly.baseline.json | moved | byte-identical duplicate removed |
| tests/tests/README.md | kept | tests/README.md | rewritten | English rewrite, <=80 lines, three-layer description |`

## 6. Removed scripts (package.json)

Removed: `test:legacy` and its 22 per-selftest aliases (`test:matrix`, `test:discover`, `test:fileguard`, `test:hardexit`, `test:lease`, `test:workdir`, `test:provenance`, `test:recover`, `test:recover:e2e`, `test:hygiene`, `test:scope`, `test:generator`, `test:dedupe`, `test:diag`, `test:lossless`, `test:mac`, `test:linux`, `test:gui`, `test:lossless:live`, `test:dsh-api`, `test:routes`, `test:schema`, `test:search`). Retained entry points: `test`, `test:unit`, `test:integration`, `test:contract`, `test:python`, `test:watch` and the build/check/pack scripts.


## 7. Row count

Rows: 23 unit JS + 10 integration JS + 3 unit Python + 25 E2E + 6 moved/kept = **67**, matching the 67 files inventoried before T11. No row is `pending`; the only deferred execution is the release-gated real-Blender E2E run, which the plan excludes from PR CI.
