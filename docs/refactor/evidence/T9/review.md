# T9 add-on package split — author self-review

Status: implementation complete in the worktree, **author self-review only**.
The required non-author independent review for R1 items has **not** been
performed; this file is not a release sign-off and must not be cited as one.

Plan authority: `D:/DSH/dsh-blender-plugin-refactor-plan-v3.md` §5.1, §5.2,
§5.3, §7.2 (T9) and §8.2 gate B4. The plan file was not edited.

## Scope delivered

- `addon/dsh_blender_bridge.py` (old 7,515-line single file) is removed from
  the worktree and staged as a delete. It is no longer an install entry.
- `addon/dsh_blender_bridge/` is a 39-module Python package with a package-root
  `__init__.py`; every subpackage has an `__init__.py`.
- `addon/dsh_blender_bridge.zip` is the install artifact: one package root
  `dsh_blender_bridge/`, no `__pycache__`/`.pyc`, 39 entries.
- `scripts/build-addon.mjs` builds a deterministic stored zip (fixed
  1980-01-01 timestamp) and has a `--check` manifest/version mode.
- `runtime/launch.mjs` boot script enables/imports the module
  `dsh_blender_bridge`, verifies add-on version >= 1.10 and protocol 11, and
  rejects non-package (single-file) modules in `_addon_meta()`.
- Install docs updated to the package zip: `addon/README.md`, `README.md`,
  `README.zh-CN.md`, `CONTRIBUTING.md`, `AGENTS.md`, `MIGRATIONS.md`,
  `docs/操作教程.md`, `docs/给第三方的安装清单.md`, `docs/AI-安装提示词.md`,
  `docs/部署SOP.md`, the host contract note, `runtime/engine.mjs` error hint,
  and `llms.txt`. Historical `CHANGELOG.md` references were left untouched.

The split keeps the 23 base + 16 gated = 39 contract commands, protocol 11,
pause gate semantics, response envelopes and registration order. Command
implementations were moved into per-domain mixins
(`commands/*.py`) and the server class in `server.py` inherits them; the
socket/queue/dispatch lifecycle remains in `server.py`.

## Verification performed (details in test.log / build.log)

| Check | Result |
|---|---|
| `python -m pytest -v` | **24 passed** in 0.30s, exit 0 |
| Add-on package unit tests | 11 passed (layout, frozen bl_info/protocol, 39 names, pause policy, config, selection payload/caps, activity state machine, premium headers, assets/io, shared Stop flag) |
| Fake-socket integration | 6 passed (real `get_addon_info` shape, all 39 dispatch, unknown/exception shapes, pause gate, framing via `socketpair`, gated-off commands) |
| Packaging | 2 passed (zip manifest exactly matches package files, package root, no cache; extract + import from another CWD + register/unregister no residue) |
| `python -m compileall -q addon/dsh_blender_bridge` | exit 0 |
| AST signature comparison against base `19828ed` | 80/80 old server methods present; 126/126 old top-level functions present; **0 argument-list differences** (one-off script, not committed) |
| Real Blender 4.2.10 direct package smoke | `SMOKE_VERSION (1, 11) 11`; `SMOKE_COMMANDS 39 39`; ping, info, unknown and paused responses exact; `SMOKE_EXEC` ran `bpy.app.version` and returned `(4, 2, 10)`; `SMOKE_SCENE Scene 3`; `SMOKE_SELECTION objects 1`; register/unregister; `SMOKE_RESIDUE False` |
| Real Blender zip install smoke | zip installed, enabled from `.../addons/dsh_blender_bridge/__init__.py`, version `(1, 11) 11`, props present, disable leaves no prop; exit 0 |
| `node scripts/build-addon.mjs` / `--check` | ok, 39 entries, 330,728 bytes, sha256 `e8f6b73ef8f1acaeaddfdcadbfed2a2f28cd6113be807da65a6c9e64ba178f86`, versions `[1,11]/[1,11]/11` |
| `node tests/launch_selftest.mjs` | **22 pass / 12 fail (exit 1)** — NOT a pass; see gap 2 |
| Boot script `py_compile` | exit 0 |
| `sha256sum -c artifacts.sha256` | all 47 entries OK |

## Review findings fixed during self-review

1. `ADDON_MODULE` initially used `__package__.split(".")[0]`, which resolved
   to `addon` when imported as `addon.dsh_blender_bridge`; preferences and
   `DSHBB_AddonPreferences.bl_idname` would break. Fixed to the full
   `__package__` (`addon/dsh_blender_bridge/compat.py:125`), mirroring the old
   top-level `__name__` and supporting Blender 5.x extension module names.
   Covered by `tests/unit/python/test_addon_package.py` and the packaging test.
2. `_user_stopped_server` was imported by value into `ui/operators.py` and
   rebound there, so `config._dshbb_ensure_server_running` never saw Stop/Start.
   Fixed by importing the `config` module and mutating
   `config._user_stopped_server` (`ui/operators.py:283`, `:303`), with a unit
   test asserting the flag after `DSHBB_OT_StopServer.execute()`.
3. `_selection_send_state` is rebound in `_record_selection_send`
   (`commands/selection.py:264-270`). UI reads it through the module attribute
   (`ui/panels.py:217`) instead of a stale from-import.
4. `get_tripo_status` is not a server method (it never was); it is the
   `premium_tripo_status` function wired by `premium_tripo_handlers()` in
   `commands/generate_premium.py` and `server.py:402/453`, exactly as before.
5. Old `__name__` uses at former lines 1769/1826/4037/4055/6337 were replaced
   with `compat.ADDON_MODULE`; only the `if __name__ == "__main__"` footer and
   `type(e).__name__` remain.

## Residual risks and gaps

1. **No independent review.** R1 requires a non-author review; only this
   self-review exists. Treat gate B4/R1 as not yet signed off.
2. **Launcher selftest is not green on this Windows host.** 12/12 failures come
   from the test's `fake-blender.sh` spawn path on Windows (argv empty,
   launched=false, pid=null, boot file bytes=0) and assertions derived from that
   fake process. The direct `LAUNCH_BOOT_PY` name-hint/content assertions and
   `py_compile` pass, but a full launcher spawn E2E was not demonstrated here.
   This is an environment/test-harness limitation, not claimed as a pass.
3. **No GUI Connect / live selection E2E.** The add-on refuses to start its
   socket server in `blender -b` by design, so the real Blender smoke covers
   register/unregister, ping, info, `execute_code`, scene info, selection
   payload construction, unknown and pause. The fake-socket test covers framing
   and dispatch, but `send_selection_to_dsh` / `send_collection_to_dsh` were
   not POSTed to a live DSH backend in this task.
4. **Integration clients are thin.** `integrations/hyper3d_client.py` and
   `integrations/sketchfab_client.py` currently hold endpoint constants only;
   orchestration remains in `commands/generate_hyper3d.py` and
   `commands/assets_sketchfab.py`. This follows behavior preservation but is
   less separation than the plan's one-line descriptions suggest.
5. **Plan guidance deviations:** `_read_sub_selection` lives in
   `compat.py` rather than `commands/selection.py`; PolyHaven HTTP/cache code
   lives in `integrations/polyhaven_client.py` while command methods stay in
   `commands/assets_polyhaven.py`. Behavior was prioritized over a strictly
   line-map-shaped split.
6. **Line budgets:** `commands/core.py` 612, `commands/assets_polyhaven.py`
   665 and `integrations/polyhaven_client.py` 612 lines exceed the <=600
   ordinary-module target, while all are below the 1000-line hard cap. Further
   splitting was not done to avoid behavior churn.
7. **No live network/integration calls.** No Poly Haven, Sketchfab, Poly Pizza,
   Hyper3D, Hunyuan, premium or telemetry API was exercised; credentials and
   network behavior were not available. Error handling for those paths is
   therefore covered only by existing code movement and static/signature checks.
8. **Blender 5.x extension layout not tested.** `ADDON_MODULE = __package__`
   is the semantically correct replacement for the old `__name__`, and the
   boot script has a zip fallback, but installation was tested only through the
   legacy `BLENDER_USER_SCRIPTS/addons` path on Blender 4.2.10.
9. **Binary artifact.** The zip is committed; `node scripts/build-addon.mjs
   --check` verifies its manifest/version and the packaging test checks the
   manifest matches the source tree, but a source edit without a rebuild will
   fail that test rather than silently pass.
10. **Preserved but stale branding.** `bl_info["description"]` remains
    "Connect Blender to Claude via MCP" from the original add-on to avoid a
    contract/description change; this may warrant a follow-up docs/branding
    clean-up outside T9's behavior freeze.

## Rollback

Reverting the T9 commit restores the old single file and all previous docs. No
data migration or protocol change is involved. The zip is reproducible with
`node scripts/build-addon.mjs` from the package sources.
