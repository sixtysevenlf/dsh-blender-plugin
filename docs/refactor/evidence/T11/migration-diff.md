# T11 migration diff (C4 evidence)

Branch `task/t11`, base `65e2508` + `a679f16`, staged diff at commit time.

## Summary

```
 130 files changed, 3148 insertions(+), 5521 deletions(-)
```

- Deleted legacy files: 37
- Added files: 57 (new layered tests, E2E wrappers, archived E2E cases, evidence)
- Renamed/moved files: 29
- Modified files: 7

## Deleted legacy tests (all 67 dispositions in tests/MIGRATION.md)

```
tests/backend_probe_selftest.mjs
tests/capability.lock.json
tests/capability_lock.mjs
tests/client_bundle_selftest.mjs
tests/discoverability_selftest.mjs
tests/dsh_api_compat_probe.mjs
tests/frame_dedupe_selftest.mjs
tests/gate_eval_selftest.py
tests/generator_dispatch_selftest.py
tests/guidance_selftest.mjs
tests/headless_file_guard_selftest.mjs
tests/headless_hard_exit_selftest.mjs
tests/host_routes_selftest.mjs
tests/launch_selftest.mjs
tests/lease_scope_selftest.mjs
tests/lease_stale_selftest.mjs
tests/ledger_recover_e2e_selftest.mjs
tests/ledger_recover_selftest.mjs
tests/linux_selftest.mjs
tests/lossless_guard.mjs
tests/mac_selftest.mjs
tests/modules_selftest.py
tests/plan_readonly.baseline.json
tests/provenance_stale_selftest.mjs
tests/route_contract_selftest.mjs
tests/scene_hygiene_selftest.mjs
tests/schema_resolve_selftest.mjs
tests/selection_store_selftest.mjs
tests/shape_search_selftest.mjs
tests/sidebar_injection_selftest.mjs
tests/single_source_selftest.mjs
tests/skill_bundle_selftest.mjs
tests/tool_cards_selftest.mjs
tests/tui_host_selftest.mjs
tests/view_diag_selftest_runner.mjs
tests/windows_hide_selftest.mjs
tests/workdir_shared_selftest.mjs
```

## Renamed / moved

```
tests/engine_probe.sh -> scripts/engine-probe.sh
tests/assembly_export_selftest.py -> tests/e2e/blender/cases/assembly_export_selftest.py
tests/assembly_repair_selftest.py -> tests/e2e/blender/cases/assembly_repair_selftest.py
tests/assembly_selftest.py -> tests/e2e/blender/cases/assembly_selftest.py
tests/audit_file_selftest.py -> tests/e2e/blender/cases/audit_file_selftest.py
tests/audit_review_regression.py -> tests/e2e/blender/cases/audit_review_regression.py
tests/blockout_refsheet_selftest.py -> tests/e2e/blender/cases/blockout_refsheet_selftest.py
tests/blockout_selftest.py -> tests/e2e/blender/cases/blockout_selftest.py
tests/contract_selftest.py -> tests/e2e/blender/cases/contract_selftest.py
tests/deliver_selftest.py -> tests/e2e/blender/cases/deliver_selftest.py
tests/evalharness_selftest.py -> tests/e2e/blender/cases/evalharness_selftest.py
tests/gate_review_regression.py -> tests/e2e/blender/cases/gate_review_regression.py
tests/geo_selftest.py -> tests/e2e/blender/cases/geo_selftest.py
tests/img_digest_selftest.py -> tests/e2e/blender/cases/img_digest_selftest.py
tests/mate_selftest.py -> tests/e2e/blender/cases/mate_selftest.py
tests/motion_selftest.py -> tests/e2e/blender/cases/motion_selftest.py
tests/p0_real_modeling_selftest.py -> tests/e2e/blender/cases/p0_real_modeling_selftest.py
tests/plan_selftest.py -> tests/e2e/blender/cases/plan_selftest.py
tests/qc_selftest.py -> tests/e2e/blender/cases/qc_selftest.py
tests/shapesolve_selftest.py -> tests/e2e/blender/cases/shapesolve_selftest.py
tests/surface_selftest.py -> tests/e2e/blender/cases/surface_selftest.py
tests/txn_restore_selftest.py -> tests/e2e/blender/cases/txn_restore_selftest.py
tests/txn_selftest.py -> tests/e2e/blender/cases/txn_selftest.py
tests/upstream_integration_selftest.py -> tests/e2e/blender/cases/upstream_integration_selftest.py
tests/view_diag_selftest.py -> tests/e2e/blender/cases/view_diag_selftest.py
tests/visualreport_selftest.py -> tests/e2e/blender/cases/visualreport_selftest.py
tests/gui_channel_probe.mjs -> tests/e2e/gui/cases/gui_channel_probe.mjs
tests/png_decode.py -> tests/helpers/png_decode.py
tests/skill_probe.py -> tools/skill_probe.py
```

## Modified files

```
AGENTS.md
package.json
scripts/check-structure.mjs
scripts/pyrun.mjs
scripts/sync_counts.mjs
scripts/test.mjs
tests/README.md
```

## Added files (grouped)

### docs/refactor/evidence/T11 (10)

```
docs/refactor/evidence/T11/build.log
docs/refactor/evidence/T11/commands.txt
docs/refactor/evidence/T11/e2e.log
docs/refactor/evidence/T11/env.txt
docs/refactor/evidence/T11/integration.log
docs/refactor/evidence/T11/lint.log
docs/refactor/evidence/T11/pytest.log
docs/refactor/evidence/T11/review.md
docs/refactor/evidence/T11/typecheck.log
docs/refactor/evidence/T11/unit.log
```

### other (2)

```
plan_graph.mmd
tests/MIGRATION.md
```

### tests/e2e/blender (wrappers + runner) (27)

```
tests/e2e/blender/legacy.py
tests/e2e/blender/legacy_runner.py
tests/e2e/blender/test_assembly.py
tests/e2e/blender/test_assembly_export.py
tests/e2e/blender/test_assembly_repair.py
tests/e2e/blender/test_audit_file.py
tests/e2e/blender/test_audit_review.py
tests/e2e/blender/test_blockout.py
tests/e2e/blender/test_blockout_refsheet.py
tests/e2e/blender/test_contract.py
tests/e2e/blender/test_deliver.py
tests/e2e/blender/test_evalharness.py
tests/e2e/blender/test_gate_review.py
tests/e2e/blender/test_geo.py
tests/e2e/blender/test_imgtools.py
tests/e2e/blender/test_mate.py
tests/e2e/blender/test_motion.py
tests/e2e/blender/test_p0_real_modeling.py
tests/e2e/blender/test_plan.py
tests/e2e/blender/test_qc.py
tests/e2e/blender/test_shapesolve.py
tests/e2e/blender/test_surface.py
tests/e2e/blender/test_txn.py
tests/e2e/blender/test_txn_restore.py
tests/e2e/blender/test_upstream_integration.py
tests/e2e/blender/test_view_diag.py
tests/e2e/blender/test_visualreport.py
```

### tests/e2e/gui (1)

```
tests/e2e/gui/test_gui_channel.py
```

### tests/helpers (2)

```
tests/helpers/backend.ts
tests/helpers/fake-http.ts
```

### tests/integration (12)

```
tests/integration/js/backend-probe.test.ts
tests/integration/js/dsh-api-compat.test.ts
tests/integration/js/host-jobs.test.ts
tests/integration/js/host-scene-items.test.ts
tests/integration/js/host-selection.test.ts
tests/integration/js/launch.test.ts
tests/integration/js/lease-scope.test.ts
tests/integration/js/ledger-recover-e2e.test.ts
tests/integration/js/selection-store.test.ts
tests/integration/js/tui-commands.test.ts
tests/integration/js/tui-footer.test.ts
tests/integration/js/tui-selection.test.ts
```

### tests/unit (3)

```
tests/unit/python/test_gate_eval.py
tests/unit/python/test_generator_dispatch.py
tests/unit/python/test_import_smoke.py
```
