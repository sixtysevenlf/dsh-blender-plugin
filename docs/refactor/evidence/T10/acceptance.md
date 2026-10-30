# T10 acceptance (author)

Verdict: T10 per plan 7.2 is met as a test deliverable: the default pytest suite is green
(253 passed, exit 0), real Blender 4.2.10 headless and GUI E2E logs exist, the 39-command
matrix is generated from the real dispatch table and cross-checked against
runtime/catalog.mjs, and the packaging evidence for plan 8.2 B4 (zip manifest,
register/unregister, launcher contract) is present. No addon/runtime/src product code was
changed. Independent non-author R1 review is still pending, and the plan 6.11 coverage
gate C5 is NOT met (see open items) — this document does not claim otherwise.

Plan authority: D:/DSH/dsh-blender-plugin-refactor-plan-v3.md 5.3 / 6.11 / 7.2 (T10) /
8.0 / 8.1 (A3) / 8.2 (B4) / 8.3 (C6). The plan file and its checkboxes were not edited.

Date: 2026-10-30. Reviewer column: "self" = author self-check with real command output;
"R1" = non-author review required by plan 8.0; "R2" = coverage/quality gate owner.

## T10 completion criteria (plan 7.2: unit / integration / E2E / packaging; 39 names complete)

| ID | WHEN/THEN criterion | Result | Evidence | Reviewer | Date |
|---|---|---|---|---|---|
| T10-U1 | WHEN unit tests run with bpy stubs THEN add-on config (env fallbacks, backend URL, auto-start, port probe) is verified | PASS | tests/unit/python/test_addon_config.py (11 tests); test.log | self | 2026-10-30 |
| T10-U2 | WHEN unit tests run THEN protocol registry (39 ordered names), groups, gates and pause policy are frozen | PASS | tests/unit/python/test_addon_protocol.py (6); test.log | self | 2026-10-30 |
| T10-U3 | WHEN unit tests run THEN selection/collection payload shape, caps, posting errors and sub-selection are verified | PASS | tests/unit/python/test_addon_selection.py (15); test.log | self | 2026-10-30 |
| T10-U4 | WHEN unit tests run THEN the human/agent activity state machine, windows, tick throttle and handler registration are verified | PASS | tests/unit/python/test_addon_activity.py (11); test.log | self | 2026-10-30 |
| T10-U5 | WHEN unit tests run THEN premium status/headers and job parameter parsing (prompt/image/quality/bbox/retry) are verified | PASS | tests/unit/python/test_addon_premium.py (19); test.log | self | 2026-10-30 |
| T10-U6 | WHEN unit tests run THEN assets/io safe file names, texture matching and download/import error paths are verified | PASS | tests/unit/python/test_addon_assets_io.py (11); test.log | self | 2026-10-30 |
| T10-U7 | WHEN unit tests run THEN the package manifest/layout/39-module budget is frozen | PASS | tests/unit/python/test_addon_manifest.py (4); test.log | self | 2026-10-30 |
| T10-I1 | WHEN integration tests run THEN the real dispatch table resolves all 39 commands and forwards params as kwargs | PASS | tests/integration/python/test_addon_dispatch.py (11); test.log | self | 2026-10-30 |
| T10-I2 | WHEN integration tests run THEN the pause gate blocks writes with AI_PAUSED and keeps reads/ping/whitelist working | PASS | test_addon_dispatch.py::test_pause_gate_*; matrix pause tests; test.log | self | 2026-10-30 |
| T10-I3 | WHEN integration tests run THEN socket framing (partial frames, UTF-8 split, sequential commands, disconnect) is verified | PASS | tests/integration/python/test_addon_protocol_frames.py (6); test.log | self | 2026-10-30 |
| T10-I4 | WHEN integration tests run THEN handler exceptions and unknown commands return the flat error envelope | PASS | test_addon_dispatch.py::test_unknown_command_and_handler_exception_shapes; matrix error tests | self | 2026-10-30 |
| T10-I5 | WHEN integration tests run THEN all 39 command names execute through dispatch with flat success/error envelopes and per-command pause semantics | PASS | tests/integration/python/test_addon_command_matrix.py (138 tests); test.log | self | 2026-10-30 |
| T10-E1 | WHEN real Blender 4.2.10 headless runs THEN the committed zip installs and the add-on enables | PASS | tests/e2e/blender/test_addon_e2e.py (7 tests); e2e-headless.log; e2e/blender-headless.stdout.log | self | 2026-10-30 |
| T10-E2 | WHEN real Blender runs THEN get_addon_info returns v1.11/protocol 11/16 capabilities | PASS | e2e/blender-headless.results.json (check get_addon_info) | self | 2026-10-30 |
| T10-E3 | WHEN real Blender runs THEN execute_code performs a real bpy cube command | PASS | results.json (execute_bpy_command); e2e-headless.log | self | 2026-10-30 |
| T10-E4 | WHEN real Blender runs THEN send_selection_to_dsh POSTs the v1 selection payload to a live loopback backend | PASS | results.json (selection_send); e2e-headless.log | self | 2026-10-30 |
| T10-E5 | WHEN real Blender runs THEN the pause gate rejects writes and serves get_scene_info | PASS | results.json (pause_gate) | self | 2026-10-30 |
| T10-E6 | WHEN real Blender runs THEN disable/remove leaves no scene props, owned timers or package directory | PASS | results.json (uninstall: residue false, timer_registered false, package_dir_removed true) | self | 2026-10-30 |
| T10-E7 | WHEN real GUI Blender runs THEN the Connect operator starts the socket server and a client gets real command responses | PASS | tests/e2e/gui/test_gui_connect.py (4); e2e-gui.log; e2e/blender-gui.stdout.log | self | 2026-10-30 |
| T10-P1 | WHEN packaging is checked THEN the zip has exactly 39 package files, no cache/old single-file entries | PASS | test_addon_packaging.py (4); build.log; command-matrix.json | self | 2026-10-30 |
| T10-P2 | WHEN packaging is checked THEN bl_info/addon version is (1,11) and protocol is 11 in source and zip | PASS | test_addon_packaging.py; test_addon_manifest.py; build.log (build-addon --check) | self | 2026-10-30 |
| T10-P3 | WHEN the package is imported from a throw-away extraction directory THEN it works without relying on cwd | PASS | test_addon_packaging.py::test_zip_installs_as_package_and_registers_from_any_cwd | self | 2026-10-30 |
| T10-P4 | WHEN register/unregister runs twice THEN there are no registered classes, timers, load_post handlers or Scene props left | PASS | test_addon_packaging.py::test_register_unregister_leaves_no_residue_and_can_run_twice | self | 2026-10-30 |

## Plan 8.1 A3 — 39 add-on command contract

| ID | WHEN/THEN criterion | Result | Evidence | Reviewer | Date |
|---|---|---|---|---|---|
| A3-1 | WHEN the command matrix is produced THEN it has one row per command with name, params, return structure and pause semantics | PASS | docs/refactor/evidence/T10/command-matrix.md (39 rows, 48 lines); command-matrix.json | self | 2026-10-30 |
| A3-2 | WHEN tests run THEN each command request/response/error/pause/read-only behaviour is asserted against the real registry/dispatch | PASS | test_addon_command_matrix.py (138 tests); test.log; matrix-test.log | self | 2026-10-30 |
| A3-3 | WHEN cross-checked THEN the add-on registry matches runtime/catalog.mjs COMMAND_CATALOG except the documented ping / get_polyhaven_asset_preview difference | PASS | test_addon_command_matrix.py::test_matrix_cross_checks_runtime_catalog | self | 2026-10-30 |
| A3-4 | WHEN independently reviewed THEN an R1 reviewer confirms the above | PENDING | this file + review.md | R1 | - |

## Plan 8.2 B4 — add-on package split / zip install / launcher contract

| ID | WHEN/THEN criterion | Result | Evidence | Reviewer | Date |
|---|---|---|---|---|---|
| B4-1 | WHEN the zip is inspected THEN it contains the package __init__, 39 .py files and no old single-file entry | PASS | build.log (build-addon --check entries 39); test_addon_packaging.py::test_zip_manifest_has_package_root_and_all_python_files | self | 2026-10-30 |
| B4-2 | WHEN the repo is inspected THEN addon/dsh_blender_bridge.py no longer exists and is not an install entry | PASS | test_addon_manifest.py; test_addon_packaging.py; git diff (T9 staged delete, unchanged by T10) | self | 2026-10-30 |
| B4-3 | WHEN installed in real Blender THEN the package's 39 commands are importable/registerable (protocol.COMMAND_NAMES == 39, get_addon_info capabilities) | PASS | e2e/blender-headless.results.json; test_addon_e2e.py | self | 2026-10-30 |
| B4-4 | WHEN register/unregister runs in real Blender and in the stub THEN no residue remains | PASS | results.json (uninstall); test_addon_packaging.py; test.log | self | 2026-10-30 |
| B4-5 | WHEN the launcher boot script is compared with the addon THEN it requires the package (not single-file), protocol 11 and version >= (1,10) | PASS | test_addon_launcher_contract.py (3); runtime/launch.mjs source (note: plan says v1.10, product is v1.11 — launcher accepts >=1.10) | self | 2026-10-30 |
| B4-6 | WHEN independently reviewed THEN an R1 reviewer confirms the above | PENDING | this file + review.md | R1 | - |

## Process / scope checks

| ID | WHEN/THEN criterion | Result | Evidence | Reviewer | Date |
|---|---|---|---|---|---|
| OPS-1 | WHEN the work is checked THEN the plan file and its checkboxes are untouched | PASS | git status; no diff for D:/DSH/dsh-blender-plugin-refactor-plan-v3.md | self | 2026-10-30 |
| OPS-2 | WHEN the diff is checked THEN no addon/runtime/src product code changed (tests + evidence only) | PASS | git diff --stat; git status | self | 2026-10-30 |
| OPS-3 | WHEN the evidence pack is checked THEN env/commands/raw test log/build log/matrix/E2E logs/review/acceptance all exist | PASS | docs/refactor/evidence/T10/; artifacts.sha256 | self | 2026-10-30 |
| OPS-4 | WHEN the work is released THEN a non-author review replaces the author self-check | PENDING | review.md ("作者自检，非作者 R1 待做") | R1 | - |

## Open / uncovered items (honest, not exemptions)

1. C5 coverage (plan 6.11 / 8.3): NOT MET. Addon package coverage measured 28% overall
   (4334 statements / 1342 branches) — far below the addon >=80/70 target even though UI
   modules are excluded by the plan. Network-bound command/integration modules, plugin
   registration and some helpers are under-covered because the environment is offline.
   Evidence: coverage.log. This is a C5/R2 gate, not part of T10's completion definition;
   it must be tracked/ratcheted before merge per plan 6.11.
2. C6 real-Blender release flow: T10 covers zip install/enable/Connect/get_addon_info/
   selection/bpy execute/uninstall (headless + GUI). doctor, export_scene and Blender 5.x
   rows were not part of T10 and remain open for the release task.
3. GUI E2E needs an interactive desktop; it briefly opens a Blender window. It is not in
   the default/PR suite and skips on non-desktop CI (T10_SKIP_GUI=1 / CI non-nt). The
   recorded evidence run is a single clean pass with no retry.
4. Blender 4.2's background bpy.ops.preferences.addon_remove raises on
   context.area.tag_redraw (None area). Documented in review.md; the GUI probe covers the
   real operator with a window/area override, so this is an environment limitation, not a
   product defect.
5. Network provider commands (PolyHaven/Hyper3D/Sketchfab/PolyPizza/Hunyuan/Premium) are
   exercised through dispatch/matrix mocks, not against live remote APIs (offline run).
6. Non-author R1 review and R2 coverage-gate decision are pending.
