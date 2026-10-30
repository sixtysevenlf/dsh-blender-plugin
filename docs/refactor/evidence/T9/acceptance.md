# T9 acceptance (author)

Verdict: the add-on package split, zip packaging, launcher package handling,
tests and install docs are implemented. **Gate B4 structural criteria pass**
(except the launcher end-to-end check, which is partial on this Windows host),
but full T9 acceptance is **not** claimed: GUI Connect/live selection E2E, a
green launcher selftest, and the required non-author R1 review remain open.

Plan authority: `D:/DSH/dsh-blender-plugin-refactor-plan-v3.md` §5.1/§5.2/
§5.3/§7.2 (T9)/§8.2 (B4). The plan file and its checkboxes were not edited.

## §5.1 target layout

All required package paths exist under `addon/dsh_blender_bridge/`:

| Required | Present |
|---|---|
| `__init__.py`, `config.py`, `protocol.py`, `server.py`, `compat.py` | yes |
| `commands/{core,scene,selection,activity,material,telemetry}.py` | yes |
| `commands/{assets_polyhaven,assets_sketchfab,assets_polypizza}.py` | yes |
| `commands/{generate_hyper3d,generate_hunyuan,generate_premium}.py` | yes |
| `recorders/{user_edit,activity,edit_capture}.py` | yes |
| `assets/io.py` | yes |
| `integrations/{polyhaven_client,sketchfab_client,polypizza_client,hyper3d_client,hunyuan_client}.py` | yes |
| `premium/{client,jobs,ui}.py` | yes |
| `ui/{preferences,panels,operators,lists}.py` | yes |
| package-root and every subpackage `__init__.py` | yes (39 .py files total) |
| old `addon/dsh_blender_bridge.py` removed, no duplicate implementation | yes (staged delete; no single-file add-on remains) |

Status: **PASS**.

Guidance deviations (behavior preserved; see review.md gaps 4-6):
`_read_sub_selection` is in `compat.py`; Hyper3D/Sketchfab integration
modules hold endpoint constants while orchestration remains in the command
modules; three modules exceed the <=600-line target but none exceed 1000.

## Behavior freeze

- `bl_info` name/version/blender tuple preserved; version `(1, 11)`.
- `ADDON_PROTOCOL_VERSION = 11`.
- 39 contract command names = 23 base + 16 gated; exact dispatch table and
  gated-toggle behavior preserved.
- `ping` handshake, success/error envelopes, unknown-command message and
  pause gate/error object preserved (tests and real Blender smoke).
- Registration order and unregister cleanup preserved; real Blender
  `SMOKE_RESIDUE False`; zip smoke disable leaves no prop.

Status: **PASS for the checked surface**, not a full 39-command behavior matrix
(network/integration commands were not exercised live).

## §5.3 tests

| Required test type | Evidence | Status |
|---|---|---|
| Unit pytest + bpy stubs: config, command table, selection payload, activity state machine, premium params, assets/io | 11 tests in `tests/unit/python/test_addon_package.py`; all pass | **PASS** |
| Integration pytest + fake socket: dispatch, pause gate, framing, error returns, 39 names | 6 tests in `tests/integration/python/test_addon_dispatch.py`; all pass; real `socket.socketpair` framing test | **PASS** |
| E2E real Blender: install zip, enable, Connect, `get_addon_info`, selection send, one bpy command, uninstall | Real Blender 4.2.10: zip install/enable/disable, `get_addon_info`, `execute_code` (`(4,2,10)`), scene info, selection payload, register/unregister pass. GUI **Connect** and live `send_selection_to_dsh` POST were not run (background add-on intentionally refuses server start). | **PARTIAL** |
| Packaging: zip manifest, bl_info version, CWD-independent import, register/unregister no residue | 2 tests in `tests/integration/python/test_addon_packaging.py`; all pass | **PASS** |

Full `pytest -v`: **24 passed** (includes 5 pre-existing tests).

## Gate B4

| B4 criterion | Result |
|---|---|
| single `dsh_blender_bridge.py` is no longer the install entry | **PASS** — deleted from the tree; package is the only implementation |
| zip contains `__init__.py`/`bl_info` | **PASS** — `dsh_blender_bridge/__init__.py` at package root, bl_info `[1,11]`, 39 entries |
| 39 commands can register | **PASS** — all 39 dispatch in tests; real Blender reports 39 and registers |
| launcher verifies add-on v1.10/protocol 11 | **PARTIAL** — boot script compiles and contains the package/version/protocol checks; `node tests/launch_selftest.mjs` is 22 pass / 12 fail on this Windows host because its `fake-blender.sh` cannot spawn. Full launcher E2E not demonstrated. |
| evidence: zip manifest + register/unregister log | **PASS** — `build.log`, `test.log` (direct and zip smokes) |

## Other completion conditions

| Condition | Result |
|---|---|
| No file >1000 lines | **PASS** (max 665 lines, `commands/assets_polyhaven.py`) |
| Ordinary modules <=600 lines (target) | **PARTIAL** — 612 (`commands/core.py`, `integrations/polyhaven_client.py`) and 665 (`commands/assets_polyhaven.py`) |
| `runtime/launch.mjs` launches/verifies module | **PASS for code/boot compile**, PARTIAL for spawn E2E (above) |
| addon/README + install docs minimally updated | **PASS** — addon/README.md, README.md, README.zh-CN.md, CONTRIBUTING.md, AGENTS.md, MIGRATIONS.md, docs/* install notes, host contract path, engine hint, llms.txt |
| zip is the install artifact and is tracked | **PASS** — `.gitignore` exception `!addon/dsh_blender_bridge.zip`; build/check deterministic; sha256 `e8f6b73ef8f1acaeaddfdcadbfed2a2f28cd6113be807da65a6c9e64ba178f86` |
| plan checkboxes not edited | **PASS** — no plan file change |
| commit with AI-assisted disclosure; clean tree | pending at evidence-writing time; see final report / git log |

## Honest gaps

1. Non-author independent review is **not done** (R1 open).
2. Launcher selftest 12 failures are an environment/harness limitation on
   Windows, not a green run; no live launcher spawn E2E.
3. GUI Connect and a live selection POST to the DSH backend were not exercised.
4. No live third-party API calls (Poly Haven/Sketchfab/Poly Pizza/Hyper3D/
   Hunyuan/premium/telemetry).
5. Blender 5.x extension layout not installed; only Blender 4.2.10 legacy
   add-ons path was tested.
6. Three modules exceed the 600-line target (still <1000).
7. Acceptance evidence is author-produced; do not present it as independent
   verification.

## Evidence index

- `docs/refactor/evidence/T9/env.txt`
- `docs/refactor/evidence/T9/commands.txt`
- `docs/refactor/evidence/T9/test.log`
- `docs/refactor/evidence/T9/build.log`
- `docs/refactor/evidence/T9/review.md`
- `docs/refactor/evidence/T9/acceptance.md`
- `docs/refactor/evidence/T9/artifacts.sha256`
