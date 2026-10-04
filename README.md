# DSH × Blender — Direct Realtime Plugin (Shareable Edition)

> 🤖 **Installing with an AI agent?** Don't let it guess — send it the repo URL together with this:
> "Read §2 Requirements, §3 Install and §9 Troubleshooting in the README, plus AGENTS.md, *before* doing anything. Then run `doctor` — it is not installed until `kind=ok`; if it fails, walk §9."
>
> Ready-to-paste prompt: [docs/AI-安装提示词.md](docs/AI-安装提示词.md)

---

## Support · Buy me a coffee

This plugin is **open source and free** (BSD-3-Clause): every feature is available to everyone — no paywall, no license key, no "sponsor edition".
If it saved you some time, you're welcome to buy me a coffee: **it's a voluntary donation, not a purchase**.

| Alipay (支付宝) | WeChat 赞赏码 |
|---|---|
| <a href="docs/images/sponsor-alipay.jpg"><img src="docs/images/sponsor-alipay.jpg" alt="Alipay donation QR code" width="280"></a> | <a href="docs/images/sponsor-wechat-reward.png"><img src="docs/images/sponsor-wechat-reward.png" alt="WeChat reward QR code" width="280"></a> |
| Open Alipay → Scan | Open WeChat → Scan |

**A few things, so there's no misunderstanding:**

- **A donation is not a purchase** — it buys no service, license, customization or priority. Issues and PRs are triaged on their own merits, regardless of donations.
- **Donations don't change the license** — the code stays BSD-3-Clause for everyone, with identical features.
- These are **personal appreciation codes** for small, voluntary gifts only; **not** for business or commercial payments. For commercial support, custom work or collaboration, open an issue and we'll do it properly (contract + invoice).
- Please don't repost, forward or reuse the QR codes for anything outside this project.
- The images live in the repo under `docs/images/` — **click one to open the full-size original** (1080×1621 / 1027×1027); if GitHub image loading is unreliable from mainland China, just open the files directly from the repository.

---

> 🌐 **Language:** **English (this page)** · [简体中文](README.zh-CN.md)
>
> 🧩 **Companion skill: [blender-modeling](https://github.com/sixtysevenlf/dsh-skill-blender-modeling)** — the process and the acceptance criteria for modeling / assembly (multi-agent division of labour, reference-image-driven shapes, six pitfalls, numeric assembly gates). The plugin owns the *channel*; the skill owns *how to build and how to verify*.
>
> 🏷 **Version: v1.0** (package `1.0.0`) — the first stable release. The model-facing surface is frozen at **15 tools**, and the plan channel exposes **28 families · 183 ops**.

> **Let an AI model actually drive Blender** — no clicking, no screenshots-into-prompt, no MCP server.
> One direct TCP channel (addon `127.0.0.1:9876` · backend `127.0.0.1:9877`) gives the model 15 tools: **see the viewport · edit the scene · watch a time window · run an inner search loop · profile and fix render perf · decimate objects safely · offload heavy work to a headless process · batch long work as jobs · roll back · store parameter presets · call any addon command · judge / verify / export through the contract layer · operate the channel itself.**
>
> Acceptance is unchanged and unforgiving: `blender_viewport(op="doctor")` must return `kind=ok`. If it does not, it is not installed — walk §9.

---

## 1. What you get (30-second tour)

| Capability | Tool | Typical latency |
|---|---|---|
| Peek at the viewport | `blender_rt_see` | 50–100 ms |
| **Render from any angle** (no camera object, user viewport untouched) | `blender_rt_see {from, look_at, ...}` | 85–280 ms |
| Change one thing and verify it | `blender_rt_do {see:true}` | ≈105 ms |
| Did it move? Is it right? | `blender_rt_watch` | ≤6 frames/call |
| **Inner loop search** (thousands of iterations, zero model turns) | `blender_rt_loop` | 160 ticks/s |
| Pass through any addon command / list them | `blender_rt_cmd` / `blender_rt_commands` | 25–55 ms |
| Render performance profiling + preset | `blender_rt_perf` | analyze ≈10–20 s |
| Object decimation (safe join, zero geometry loss) | `blender_rt_opt` | ≈1.4 ms/object |
| **Headless process** for heavy renders / batch geometry | `blender_rt_headless` | cold start 0.9–1.2 s |
| **Contract layer + planner + 28 families / 183 ops** | `blender_rt_plan` | AABB sweep 24 ms · BVH 0.17 ms/pair (307 objects) |
| **Hot headless session** (reuse one `blender -b`, `K` persists) | `blender_rt_worker` | pays the 1.1–1.5 s cold start once |
| **Transactions / rollback** | `blender_rt_txn` | snapshot 96.7 MB / 824 ms (300 objects) · mark→revert verified |
| **Preset library** (save / apply / export parameter sets) | `blender_rt_preset` | a stored `render.resolution_x` really takes effect |
| **Job layer** for work longer than one wait window | `blender_rt_job` | `op=wait` blocks for the structured result |
| Channel health / write lease / one-call Blender launch | `blender_viewport` | health 70–100 ms |

Detailed walkthrough (Chinese): [`docs/操作教程.md`](docs/操作教程.md) · configuration reference (Chinese): [`docs/配置参考.md`](docs/配置参考.md) · deployment SOP: [`docs/部署SOP.md`](docs/部署SOP.md). This README covers the same ground in condensed English.

---

## 2. Requirements

1. **Blender 4.x / 5.x running in GUI mode** — this channel needs a live GUI session (`-b` is only used by the headless tools).
2. **Blender addon `MCP for Blender`** (**not bundled**) listening on `127.0.0.1:9876`. It must provide at least:
   `ping`, `get_scene_info`, `get_world_state_snapshot`, `get_object_info(name)`, `get_viewport_screenshot(max_size, filepath, format)`, `execute_code(code)`.
   The **harveyxiacn enhanced `blender_mcp_addon`** is also supported (different wire protocol — the plugin adapts automatically; `addonProtocol`, default `auto`, see [`docs/配置参考.md`](docs/配置参考.md) §6). That implementation has none of the five asset integrations, and the corresponding commands fail loudly instead of pretending.
   Enable it in Blender, then in a 3D viewport press `N` → **MCP for Blender** panel → **Connect**.
   Compatibility probe: `node runtime/_probe_tools.mjs`. Protocol self-test (no Blender needed): `node tests/protocol_selftest.mjs`.
3. **Node.js ≥ 20**.
4. **DSH (DeepSeek Harness)** — this package registers tools as a DSH plugin (`inject: ['tools']`).
   Works with DSH in WSL + Blender on Windows (WSL interop is used to spawn `blender.exe`), with DSH and Blender on the same Windows machine (path mapping degrades gracefully), and — as of v1.0.4 — with **native Linux (not WSL)**: host and Blender share one OS, so no cross-OS path rewriting happens (`/home/…` reaches Blender as-is) and `blender_viewport op=doctor` reports your own distro. Blender is found via `which blender`; set `DSH_BLENDER_EXE` if it lives elsewhere. Default working dir: `os.tmpdir()/dsh-blender-rt` (see `docs/配置参考.md` §5).
5. **macOS** — supported. Host and Blender run on the same machine, so there is no cross-OS path mapping: Blender is auto-detected under `/Applications` (`Blender*.app`, including `~/Applications` and `/Volumes/*/Applications`), and the working dir defaults to `~/.dsh-blender-rt`. Set `DSH_BLENDER_EXE` / `blenderExe` only if you installed Blender somewhere non-standard; `blender_viewport op=doctor` prints what was detected.

---

## 3. Install (5 steps)

```bash
# 1) Build (needs a DSH source checkout to link cordis/schemastery/dsh-tools)
cd dsh-blender-plugin
DSH_CHECKOUT=/path/to/dsh-harness bash scripts/build.sh     # produces lib/

# 2) (optional) configure — auto-detection usually just works
cp dsh-blender.config.example.json dsh-blender.config.json

# 3) Install into DSH as a profile bundle (this package declares dsh.bundle):
#    a) symlink into <profile>/node_modules/@dsh-external/dsh-blender-plugin
#    b) in the profile package.json add "link:<abs path>" to dependencies and
#       "@dsh-external/dsh-blender-plugin" to dsh.profile.bundles
#    c) dry-run: dsh --profile <profile> --dump-config   then restart DSH

# 4) Start Blender and connect the addon — two routes:
#    agent / unattended: blender_viewport(op="launch")   # boot script + spawn + polls 9876, idempotent
#    manual: start Blender → press N → "MCP for Blender" → Connect (127.0.0.1:9876)

# 5) Verify — the ONLY acceptance criterion: doctor returns kind=ok (otherwise it is NOT installed; walk §9)
curl -sS http://127.0.0.1:9877/health    # backend alive + provenance (is this process the disk copy?)
curl -sS http://127.0.0.1:9877/doctor    # FULL round-trip through bpy; "kind":"ok" is the goal
curl -sS http://127.0.0.1:9877/who       # write lease + channel metrics + effective config
```

The plugin auto-starts the backend (`node runtime/server.mjs`, default `127.0.0.1:9877`) and runs a **15-second watchdog** that respawns it if it dies. `blender_viewport op=stop` pauses the watchdog.

---

## 4. First contact (three calls)

```text
blender_viewport(op="doctor")                          # health + effective config
blender_rt_see(max_size=560)                           # what the model sees right now
blender_rt_see(from="9,-9,6", look_at="0,0,1")         # a different angle, user viewport untouched
```

---

## 5. What's new in v1.0

- **First stable release.** The model-facing surface is frozen at **15 tools**; the plan channel is **28 families · 183 ops**. A **capability lock** inside `npm test` pins both numbers and the 15 tool names — losing a family, an op or a tool turns the build red.
- **macOS is a first-class host**: identity path mapping, `.app` auto-detection, `~/.dsh-blender-rt` working dir, and a `/proc`-free backend kill path. `blender_viewport(op="launch")` cold-starts a connected GUI Blender (measured 2.5 s). `npm run test:mac` = **45 assertions**; `npm run test:gui` = **11/11** on a real machine.
- **Five capability families landed — 21 ops, zero new tools**: `sculpt_*` (numpy displacement brushes; `bpy.ops.sculpt.brush_stroke` does not work from Python on Blender 5.2 — don't try it), `fix_*` (repair / decimate), `uv_*` (smart project / unwrap / project / pack), `print_*` (wall thickness + overhang, with a `resolution_mm` warning), `sweep_*` (parallel-transport sweep; bend radius computed before building).
- **Materials and render state**: `material_build` (10 presets) → `material_apply` → `material_bake` (bakes procedural nodes to PNG, engine restored afterwards); `render_state` / `render_wait` / `render_reset` turn "Blender is rendering" into a seconds-level fact instead of a timeout — a write command during a render is refused with `BUSY_RENDER` in ~71 ms.
- **A discoverability ladder instead of prose**: `blender_rt_plan(op="catalog")` is answered **locally** (no Blender round-trip, works under any lease) — every family says when to use it, when *not* to, and the minimal skeleton; `args={classes:true}` gives the 12 modeling classes; `<family>_help` gives operator detail; `args={handoff:true}` returns a paste-ready handoff block for subagents. Unknown op names answer `did_you_mean` with a skeleton, and a plan op sent to the wrong channel is corrected locally.
- **Descriptions are on a budget**: the 15 tool schemas stay under 1,600 characters each and 12,000 in total, with the long tail moved into the catalog (three-level progressive disclosure: tool index → family → operator detail).
- **Runtime hardening**: `workDir` probe + fallback (a read-only `/mnt/d` no longer stops the backend), `EADDRINUSE` named for what it is, render-guard stale marks auto-released after 15 minutes, Blender 5.2 compositor tolerance, and full `stdout` / `stderr` / `traceback` on failures.
- **Offline escape hatch**: `runtime/offline_bootstrap.py` loads runtime modules inside a bare `blender -b` (`kapi(name)` / `install_kernel()`), so the capabilities survive a dead backend — verified: `blender -b --factory-startup --python runtime/offline_bootstrap.py -- vehicle audit` → `OFFLINE_BOOTSTRAP ok · loaded: audit, vehicle`.
- **Reproducibility as a feature**: capability lock, route contract (`npm run test:routes` — 28/28 static, 70/70 live), provenance self-check (is the running process the disk copy?), and WSL / Windows / macOS regression guards inside `npm test`.

---

## 6. Tool reference

All 15 tools require the backend to be running (the plugin starts it for you) and the addon to be connected. Parameter names are the schema's own names, identical on WSL, Windows and macOS.

### `blender_rt_see` — look

One 3D-viewport frame in ~55–160 ms (the cheapest primitive there is); custom views; whole-window screenshots.

`max_size` (longest edge, default 560, cap 1600) · `full` + `area` (whole Blender window / Nth area) ·
`from` `look_at` (`"x,y,z"` — custom view) · `lens` `ortho` `ortho_scale` · `view_size` (`"1280x720"`) ·
`shading` (`WIREFRAME|SOLID|MATERIAL|RENDERED`) · `overlays` · `view_mode` (`viewport` default / `render`) ·
`diagnostics` (force the three self-diagnostics) · `force` (re-send a frame whose hash is unchanged).

Custom view = matrices are built in Python and drawn with `GPUOffScreen.draw_view3d`: **no camera object, no `scene.camera` change, no user-viewport change**; `shading`/`overlays` are restored right after the capture. Validated against `Camera.calc_matrix_camera()` with a max delta of **1.2e-7**; the same spec twice gives an identical MD5. Near-empty frames are self-diagnosing: `coverage_estimate`, `scene_bbox`, `objects_in_frame` and a `frame_looks_empty` warning with a suggested `from`/`look_at` (following the suggestion took coverage from **0.00% → 68.11%**). A frame identical to the previous one is not re-attached unless you pass `force=true`.

### `blender_rt_do` — do (and optionally see)

```text
blender_rt_do(code="K.n = getattr(K,'n',0)+1\nbpy.data.objects['Cube'].rotation_euler.z += 0.4", see=True)
```

Runs on Blender's main thread with `bpy/math/mathutils/Vector` preloaded. `execute_code` gets a fresh namespace every call, so cross-call state must live on the persistent kernel **`K`** (`sys.modules["dsh_rt_kernel"]`). `file=` runs a workspace `.py` (equivalent to `K.run(path)`; WSL, Windows and `.blend`-relative paths all work). Exceptions still return partial `stdout` / `stderr` / `traceback`, and the reported `ms` is *main-thread occupancy* — past ~1 s the receipt tells you to move to headless/worker.

### `blender_rt_watch` — watch over a time window

```text
blender_rt_watch(code="bpy.app.timers.register(tick)", seconds=2.5, fps=4, max_size=420)
```

Optionally runs Python first, then samples the viewport for `seconds` at `fps` (0.2–20 s, 0.5–8 fps) and returns up to 6 evenly-picked frames plus a per-frame hash; identical hashes mean the picture never changed. Keep `fps` low — every frame occupies the main thread for ~55 ms.

### `blender_rt_loop` — inner search loop (the core primitive)

```text
blender_rt_loop(op="start", spec={
  "setup":  "K.ob = bpy.data.objects['Cube']",
  "step":   "ns['params'] = {'sx': 1 + (ns['i'] % 7) * 0.1}",
  "measure":"K.ob.dimensions = (ns['params']['sx'], 1.0, 1.0)\nns['score'] = abs(K.ob.dimensions.x - 2.0)",
  "iterations": 3000, "budget_ms": 20000, "interval": 0, "measure_every": 5,
  "minimize": True, "top_k": 20, "group_key": None
})
blender_rt_loop(op="status", history=12) · op="board" · op="export" (path/top/note) · op="stop" · op="help" · op="bench"
```

Runs hundreds to thousands of iterations inside Blender's main thread (`bpy.app.timers`), with both an iteration and a time cap plus an emergency stop. `setup/step/measure` share a namespace `ns`; `measure` must set `ns["score"]` and may set `ns["params"] / ns["metrics"] / ns["violations"]`. Preloaded: `bpy / K / math / random / numpy / i / frac / penalize / anneal / record`. **Always pass `iterations` or `budget_ms`** — that is your safety valve. The inner loop only optimises the score you wrote, so after it converges, **verify through a different path** and confirm visually with `blender_rt_see`. Two more things worth knowing: it is **GUI-only** (headless `blender -b` never fires `bpy.app.timers`), and when it stops the scene sits at the **last** iteration, not at the best one.
**Rather not hand-write the three code blobs?** Use the declarative op: `blender_rt_plan(op="shape_search", args={objective:"silhouette_iou", ref:"D:/ref/side.png", view:{…}, apply:"ob.location.y = p['dy_mm']/1000.0", params:{dy_mm:{min:-30,max:30,step:5}}, iterations:300, export:"D:/out/best_fit.py"})` — give the objective and the ranges, get the candidate board plus a reproducible script (same inner loop underneath; `dry_run=true` validates and returns the spec only). Full long tail: `blender_rt_plan(op="catalog", args={tool:"rt_loop"})`.

### `blender_rt_cmd` / `blender_rt_commands` — raw addon surface

```text
blender_rt_cmd(name="get_object_info", params={"name": "Cube"}, timeout_ms=120000)
blender_rt_commands()
```

Signatures are the addon's real ones (`get_scene_info()` takes no args; `get_object_info(name)` uses `name`). There is no schema layer to rename things for you. The catalog currently names **34** commands — **19 always-on + 15 gated** by the five integrations (PolyHaven · Sketchfab · Poly Pizza · Hyper3D/Rodin · Hunyuan3D) — including several the MCP layer does not expose: `get_world_state_snapshot`, `drain_human_activity`, `get_telemetry_consent`, `set_telemetry_consent`, `get_addon_info`, plus addon v1.7's `describe_node_type` / `bpy_api_lookup` / `export_scene`. `blender_rt_commands` reports what is actually available right now and each integration's real on/off state — ask it before you call.

### `blender_rt_perf` — render performance

`op="status" | "analyze" | "apply" | "revert" | "help"`. `analyze` differentially measures per-render CPU-side sync vs per-sample GPU cost; `apply` sets `use_persistent_data`, a **denoiser auto-detected from your GPU** (OptiX ↔ OpenImageDenoise), `denoising_use_gpu`, `use_auto_tile=False` and a sample cap; `revert` restores the snapshot taken before `apply`.
Reference numbers (author's scene, 2318 objects): per-render CPU-side sync ≈4.2 s; with persistent data, repeated renders went **14.6 s → 0.79 s**.

### `blender_rt_opt` — safe object decimation

```text
blender_rt_opt(op="analyze")
blender_rt_opt(op="join", args={dry_run=False, save_before="/tmp/pre_join.blend"})
```

Merges only objects sharing collection / material / parent with no modifiers, animation, shape keys, custom properties, instancing or library link. Face and vertex counts are preserved exactly (≈1.4 ms/object of loop-sync cost saved). `dry_run` defaults to **true** — pass `dry_run=false` to really merge, and give `save_before` first.

### `blender_rt_headless` — a separate `blender -b` process

The first path for heavy renders, batch geometry and validation: it does not occupy the GUI channel and does not touch the scene you are looking at.

`script` · `script_file` (`.py`; `file=` also auto-detects `.py`) · `file` (`.blend`, receipt carries `inputFile{size,mtime,md5}`) · `outdir` · `args` · `timeout_ms` (default 180000, cap 1800000) · `engine` (`eevee` default / `cycles` / `keep` / `none`) · `preload` (`"audit,qc_render"` → `K.dsh_*_api`) · `shots` (multi-view in one call) · `out_json` · `env` · `workdir` · `factory_startup` (default true) · `use_user_config` · `bootstrap` (persistent kernel `K`) · `gpu` (`auto` / `true` / `false`, Cycles only) · `include_noise` · `as_job` · `wait_s`.

A script line `print("HEADLESS {json}")` comes back as `result` (single-line JSON). The default prelude injects **EEVEE + ray tracing**; `gpu:"auto"` walks OPTIX→CUDA→HIP→ONEAPI→METAL and every receipt carries a `gpu` field (`before/after/configured/fell_back_to_cpu`) — because headless Cycles used to fall back to CPU **silently** (measured **15.4×** slower).
**Timeouts never swallow results**: the client wait window (`DSH_HEADLESS_WAIT_MS`, default 100 s) returns `{kind:"promoted", jobId:"run-…"}` instead of failing — collect it with `blender_rt_job`. Expecting >100 s? Pass `as_job=true`. Receipts are a single-line JSON envelope plus a human summary; stdout/stderr land in `logs.*`; results over 4 KB are auto-dumped under `<workdir>/results/`.

### `blender_rt_plan` — judgment, acceptance, export and modeling (28 families · 183 ops)

One tool for everything that has to be *decided*, not just executed. **Not sure which op? Ask the catalog first — it is answered locally, with no Blender round-trip:** `op="catalog"` (all families: when to use / when not to / minimal skeleton), `args={family:"audit"}` (one family), `args={tool:"rt_headless"}` (parameter long tail for a heavy tool), `args={classes:true}` (12 modeling classes), `args={handoff:true}` (paste-ready block for subagents), `args={verify:true}` (disk-vs-loaded catalog self-check). Read-only ops are exempt from the write lease; write ops go through it.

- **Contract layer** — `register_component` / `register_connection` (candidate types, parameter ranges, forbidden ops, confidence, required evidence) / `register_envelope`; `check_envelope` / `check_interference` (AABB sweep + BVH refine) / `check_interface`; `destructive_guard` — blocks `boolean_union / weld / merge / apply_transform` while a connection is **not resolved** (returns `Unsupported Destructive Merge`); `evidence` / `ledger` — every capture recorded with path, **md5**, size and view spec; `report` — provenance report.
- **Hypothesis lifecycle** — `verify` gives three-state verdicts: `supported` / `refuted` / **`unresolved`** (external error within tolerance but the deciding parameter is *unidentifiable*); `flip` / `advance` record hypothesis changes; `fingerprint` binds evidence and verdicts to geometry digests, `ledger` marks entries `stale`, and a `supported` verdict **auto-demotes to `unresolved`** once geometry drifts (>10% diagonal or >20% size; animated objects are skipped).
- **Planner** — `plan_load` / `plan_validate` / `plan_order` / `plan_build` (`dry_run` first) / `plan_graph` (mermaid/dot) / `plan_status` / `plan_diag`. Graph = Component (box/cylinder/sphere/mesh_copy) · Connection (candidates, status, forbidden, offset) · Feature (array/grid/mirror). IDE-style diagnostics: `UnresolvedConnection`, `UnsupportedDestructiveMerge`, `MissingComponent`, `Cycle`, `ParamOutOfRange`, `UnknownKind`; hard errors refuse to compile.
- **Inspection and acceptance** — `audit_mesh` / `audit_scene` / `audit_duplicates` / `audit_measure` / `audit_purge_orphans` / `audit_drift`; assembly gates `audit_connectivity` / `audit_gate` (bbox pre-screen + BVH mesh confirmation; visible floater = longest bbox edge ≥ 1% of the model; the `micro_gap_mm` caliber is reported back — 0.3 mm is the single-solid/3D-print default, use 1–2 mm for assemblies designed with clearance) / `audit_snap_floaters` (report-only by default); cross-`.blend` `audit_overlap` and `audit_interference` (intersection **volume estimate in mm³ with a 95% CI**, Monte-Carlo ray parity — open/non-manifold meshes report `unresolved` instead of a plausible-looking wrong number); `print_walls` / `print_overhang` / `print_report` (BVH ray thickness + normal-cone overhang, no 3D Print Toolbox needed); `clear_check` (rule-table paired clearance); `calib_run` (what can my gates actually catch? `baseline_pass` must be true); `face_ratios` / `face_compare`; `gltf_validate` / `ext_mesh_check` (external glTF-Validator and open3d second opinions).
- **Reference-image work** — `img_scan` / `img_rectify` / `img_crop` / `img_annotate` / `img_diff` (turn pixels into numbers); `shape_plan` → `shape_sections` → `shape_loft` → `shape_panels`; `vehicle_sections` → `vehicle_loft` → `vehicle_panels` (proportion gate first, wheel-arch boolean = zero interpenetration); `human_base` / `human_measure` / `human_spec` / `human_head`; `qc_compare` (adaptive mask + centroid/scale alignment, self-IoU **1.0000**), `qc_render_views` (N views in one call, auto-framing from the merged AABB, temporary three-point rig, per-view PNG + ms + md5 + `render_views.jsonl`, cumulative `budget_s`, optional `ref_path` → IoU per view — measured 3 views in **3487 ms**), `montage` (contact sheet + per-cell metrics).
- **Geometry production and delivery** — `sculpt_scan` → `sculpt_setup` → `sculpt_apply` (numpy displacement brushes draw/inflate/pinch/flatten/smooth/crease + voxel/multires/dyntopo + masks; voxel remesh 1986 → 12148 verts in **0.084 s**; mirrored strokes must be separate strokes); `fix_repair` / `fix_decimate` (audit diagnoses, fix treats — a no-op repair returns `ok=false`); `uv_stats` / `uv_smart_project` / `uv_unwrap` / `uv_project` / `uv_pack`; `sweep_analyze` → `sweep_build` (bend radius checked before building; too tight = refused unless `force=true`); `material_scan` / `material_build` (10 presets) / `material_apply` / `material_bake`; `deliver_export` / `deliver_verify` (unit-box normalize, multi-group OBJ+MTL, md5 manifest — one byte of tampering must FAIL); `generator_save` / `generator_run` / `generator_list|get|diff` (the program is the shape: reproduced in a **fresh headless Blender process**, cached by source hash — the source parameter is `code=`, run parameters go in the nested `args=`); `motion_joints` / `motion_joint` / `motion_infer_axis` (two independent evidence paths; conflicting evidence ⇒ `unresolved`, never a pick) / `motion_measure` (kinematic + BVH evidence, **not** physics) / `motion_export_urdf` / `motion_export_usda`; `mate_check` + `fit_help` (contact-area fraction, median single-side gap, penetration depth on the actual mesh; fit classes clearance 0.25 · location 0.15 · press −0.05 · snap 0.20 mm per side, plus ISO 273 from `runtime/assembly_features.json`); `interference_report` (depth/volume/severity, `declared` exemption for registered joints); `gate_plan` → `gate_run` (a project's acceptance spec run in one call; read the **three-state `verdict`** — `degraded` is not a pass); `pipe_run` (with `artifact` references between steps); `render_lock` / `render_status` (cross-process render queue lock); `render_state` / `render_wait` / `render_reset`; `gui_frame` / `gui_shading` / `gui_open` (things that need a real UI context — inside `rt_do` Blender's `context.screen` is `None`).

**The rule that matters**: when two hypotheses fit the visible evidence equally well (measured residuals **184 vs 184**), the verdict is `unresolved` **plus the probe you need** — never a coin flip.
Worked example with numbers (~3 s, headless): [`docs/examples/chair-backrest/`](docs/examples/chair-backrest/); method: [`docs/假设驱动建模-cookbook.md`](docs/假设驱动建模-cookbook.md). Self-tests: `tests/contract_selftest.py` (**33/33**), `tests/plan_selftest.py` (**18/18**).

### `blender_rt_worker` — hot headless session

`op="start" | "exec" | "status" | "stop" | "restart" | "list"` plus `name` (several instances), `code`, `timeout_ms`, `engine`, `gpu`, `purge_prefix` (drop modules before `exec` when you changed user code).

A resident `blender -b` with a blocking accept loop on the main thread and the persistent kernel `K`, so `exec` calls reuse **the same Blender session**. Use it when the same script runs ≥3 times or a single run is >10 s and you iterate: the 1.1–1.5 s cold start (plus up to ~16 s of EEVEE shader compilation) is paid once. It is serial and windowless — GUI-dependent `bpy.ops` may fail there; for one-shot work use `blender_rt_headless`.

### `blender_rt_txn` — transactions / rollback

`op="snapshot" | "restore" | "list" | "prune" | "mark" | "revert" | "marks" | "drop" | "help"` plus `label`, `note`, `objects`, `keep`.

Two levels: **file-level** `snapshot`/`restore` (whole-scene rollback; writes a `.blend` copy with `copy=True`, so the current filepath is untouched — measured **96.7 MB / 824 ms** on a 300-object scene) and **object-level** `mark`/`revert` (transform, material slots, visibility, modifier flags — millisecond-scale, in place). **Boundary:** object-level marks do **not** cover topology/UV/vertex-level changes — after a Boolean, a join or a face deletion, `revert` skips those objects and reports them; use file-level `snapshot`/`restore` for that (restoring discards the current unsaved state).

### `blender_rt_preset` — preset library

`op="save" | "list" | "get" | "apply" | "delete" | "export" | "import" | "help"`.

Turns a parameter combination into a saveable, appliable, shareable asset. `data` uses dot paths: material nodes `{"inputs.Base Color": [1,0,0,1]}`, object properties `{"location": [0,0,1]}`, scene settings `{"render.resolution_x": 640}`. `apply` targets use `MAT:name` / `OBJ:name` / `SCENE` (comma-separated) and reports `applied/skipped/errors`; without `targets` it only previews. `export`/`import` use a single JSON bundle, so presets travel between machines and sessions.

### `blender_rt_job` — job layer

`op="start" | "status" | "collect" | "wait" | "kill" | "list"` plus the same script/file/outdir surface as headless (`script`, `script_file`, `args`, `env`, `file`, `outdir`, `timeout_ms`, `engine`, `preload`, `factory_startup`, `bootstrap`, `workdir`, `include_noise`, `out_json`, `tail`, `id`).

`op=start` returns a `jobId` immediately — long work no longer occupies a client connection and cannot be cut off by a tool timeout. `op=wait` blocks once for the structured result (default 120 s per call, cap 600 s) — **don't hammer `status`**. Runs and jobs share one id space, so a headless `run-…` id can be collected with the same ops; logs land in `<outdir>/jobs/<id>/`. **A client timeout is not a task failure** — the server-side child keeps running and artifacts still land.

### `blender_viewport` — operate the channel

`status` · `doctor` (a real bpy round-trip + three-level diagnosis + effective config) · `who` · `lease` / `release` (`force=true` steals) · `start` / `stop` / `restart` (backend process + 15 s watchdog) · `launch` (one-call GUI Blender: writes a boot script, spawns detached, **polls the addon port** — the only trusted criterion — then runs doctor; idempotent) with `file`, `exe`, `addon_module`, `addon_file`, `wait_ms`, `dry_run`.

The write **lease** protects all write routes (`/act /cmd /loop /perf /opt /headless /txn /preset /job`); read-only ops are exempt. Another session holding it returns `409 {ok:false, error:"leased", holder, expiresInMs}`; the plugin renews only while it already holds it. A crashed session's lease is reclaimed by a liveness probe instead of blocking everyone until the TTL expires.

---

## 7. Recipes

1. **Change-and-look**: `blender_rt_do(code="...", see=True)` — batch 10–30 steps, then take a wider look.
2. **Multi-angle acceptance**: the same `from/look_at/view_size` yields a stable MD5, so it works as an acceptance fingerprint.
3. **Inner-loop fitting**: define `score`, constraints via `penalize`, step size via `anneal`; when it converges, **verify through a different path** (e.g. re-measure with `blender_rt_do`), because the inner loop only optimises the score you wrote.
4. **Render pipeline**: `perf status → analyze → apply → status` (and `revert` if you dislike it); too many objects → recipe 5.
5. **Decimation**: `opt analyze → join (dry_run) → join (save_before=…)` then re-check face/vertex counts.
6. **Offline renders**: `blender_rt_headless {script_file, outdir, timeout_ms, preload:"view,perf", engine:"cycles"}`.
7. **Long work**: `blender_rt_job(op="start")` → go do something else → `op="wait"` (or `op="collect"` for progress). A timeout is not a failure — recover by `runId`.
8. **Before anything destructive**: `blender_rt_txn(op="snapshot")` (topology) or `op="mark"` (transforms/materials only).
9. **Repeat a look you liked**: `blender_rt_preset(op="save")` once, `op="apply"` on another object/scene later.
10. **Don't know which op?** `blender_rt_plan(op="catalog")` — local, cheap, and it tells you when *not* to use a family.

---

## 8. Configuration

Resolution order: **environment → config file → auto-detect → default**. Nothing is hard-coded to a specific machine: working dir, Blender executable and ports are all configurable.

| Setting | Env var | Config key | Default |
|---|---|---|---|
| Working dir (frames/scripts) | `DSH_BLENDER_WORKDIR` | `workDir` | `%LOCALAPPDATA%\dsh-blender-rt` (macOS: `~/.dsh-blender-rt`) |
| Blender executable | `DSH_BLENDER_EXE` | `blenderExe` | auto (Program Files / Steam / `/Applications` / PATH) |
| Addon host/port | `DSH_BLENDER_ADDON_HOST/PORT` | `addonHost`/`addonPort` | `127.0.0.1:9876` |
| Addon wire protocol | `DSH_BLENDER_ADDON_PROTOCOL` | `addonProtocol` | `auto` (`ahujasid` / `category-action`) |
| Backend port | `DSH_BLENDER_HTTP_PORT` | `httpPort` | `9877` |
| Hot-worker port | `DSH_BLENDER_WORKER_PORT` | `workerPort` | `9879` |
| Blender user config/scripts (GPU prefs) | `DSH_BLENDER_USER_CONFIG` / `DSH_BLENDER_USER_SCRIPTS` | `blenderUserConfig`/`blenderUserScripts` | `null` (headless does not read them) |
| Lease identity / TTL | `DSH_BLENDER_HOLDER` / `DSH_BLENDER_LEASE_TTL_MS` | `holder`/`leaseTtlMs` | `plugin-pid-<pid>` / 600000 ms |
| Headless client wait window | `DSH_HEADLESS_WAIT_MS` | — | 100000 ms (then `{kind:"promoted", jobId}`) |
| Auto-promote headless to the job layer | `DSH_HEADLESS_AUTO_JOB_MS` | — | 0 (off) |

Config file locations: `$DSH_BLENDER_CONFIG` → `<package>/dsh-blender.config.json` → `~/.dsh/dsh-blender.config.json`.
Check the **effective** config any time: `curl -s http://127.0.0.1:9877/who` (look at the `config` block) or `blender_viewport op=doctor`. Full details: [`docs/配置参考.md`](docs/配置参考.md).

The working directory must be readable/writable **from both ends** (Blender is a Windows/macOS process, the host may be WSL/Linux): give the Windows form and the plugin maps the WSL form, and don't point it at a read-only mount or a WSL-internal path. Headless `outdir`/`out_json` accept WSL paths directly (the receipt echoes both real forms). Both `DSH_HEADLESS_*` variables are read once at module load, so restart DSH after changing them.

---

## 9. Troubleshooting

| Symptom | What to run | Likely cause |
|---|---|---|
| "backend unavailable" | `blender_viewport op=start` | backend not running / port taken (`EADDRINUSE` names a stale backend) |
| `blender-unreachable` | `blender_viewport op=launch`, or check Blender's N panel | Blender not running, or addon not connected |
| `main-thread-busy` | wait, or go headless | Blender's main thread is busy (render/modal op) |
| `BUSY_RENDER` on a write | `blender_rt_plan(op="render_wait")` | a render is running; the marker is on disk, so the answer is instant |
| `addon-thread-stuck` | don't spam calls | previous long command still running (`blender_rt_loop op=stop` aborts) |
| `addon-thread-stuck` + a `detected_protocol` field | set `addonProtocol` as the message says, then `blender_viewport op=restart` | the other addon implementation is installed — protocol mismatch (flat vs category/action) |
| Frame/path errors | `doctor` → `config.workDir` (+ `workDir.shared`) | dir not shared between both ends |
| `409 leased` on writes | `blender_viewport op=who` | another session holds the write lease (`force=true` to take over) |
| `blender-exe-missing` | `doctor` → `config.blenderExe` | executable not found — configure it |
| `value is not lossless JSON` | upgrade and **restart DSH** | an old receipt channel hitting the host's lossless-JSON gate |
| A tool returns partial output after an exception | read the returned `stdout`/`stderr`/`traceback` | execution error, not a channel error — the diagnosis is in the same receipt |

Decoding a failure is a three-level diagnosis: TCP unreachable → Blender/addon not up; TCP fine but bpy calls time out → main thread busy; TCP fine but `ping` also fails → the addon client thread is stuck. `/health` additionally reports `provenance` — if `stale=true`, the disk runtime has changed but this process is still the old generation: restart the backend.

---

## 10. Repository layout

```text
dsh-blender-plugin/
├── src/index.ts                # DSH host: the 15 tools, backend watchdog (15 s), lease heartbeat
├── runtime/
│   ├── config.mjs              # env → config file → auto-detect → defaults (WSL / Windows / macOS)
│   ├── engine.mjs              # addon socket client, op routing, command catalog, view/headless
│   ├── server.mjs              # HTTP backend on 127.0.0.1:9877 (+ lease gating, NDJSON streaming)
│   ├── catalog.mjs             # plan catalog: 28 families · 183 ops, tool details, provenance self-check
│   ├── classes.mjs             # 12 modeling classes (first step / chain / forbidden / acceptance gate)
│   ├── addon-protocol.mjs      # flat vs category/action addon protocol adapter
│   ├── paths.mjs · launch.mjs · staging.mjs · proc_env.mjs · backend_probe.mjs
│   ├── kit.py                  # shared kernel: JSON/API boilerplate, units, geometry, receipts
│   ├── view.py                 # custom-view capture (matrices + offscreen + hand-written PNG)
│   ├── perf.py                 # render preset (auto denoiser) + safe object join
│   ├── runner.py               # inner-loop runner v2 (timers, limits, penalize/anneal/boards/export)
│   ├── contract.py             # components/connections/envelopes, checks, destructive guard, ledger
│   ├── planner.py              # Component·Connection·Feature graph → diagnostics → compile to bpy
│   ├── audit.py                # mesh/scene checks, connectivity, interference, drift, duplicates
│   ├── qc.py · qc_render.py    # IoU/compare + multi-view harness (framing, lights, md5, budget)
│   ├── sculpt.py · mesh_fix.py · uv_tools.py · printcheck.py · sweep.py
│   ├── material.py · render_guard.py · gate.py · vehicle.py · shapegen.py · human.py
│   ├── clearance.py · faceeval.py · imgtools.py · calib.py · montage.py
│   ├── deliver.py · motion.py · generator.py · pipeline.py · presets.py · txn.py · worker.py
│   ├── assembly_features.json  # fit classes + ISO 273 table used by mate_check / fit_help
│   ├── offline_bootstrap.py    # escape hatch: load runtime modules inside a bare blender -b
│   ├── _probe_tools.mjs        # addon compatibility probe
│   └── ext/                    # external second opinions (glTF-Validator, open3d venv)
├── docs/                       # tutorial / config reference / SOP / mechanics & pitfalls (zh)
│   ├── 操作教程.md · 配置参考.md · 部署SOP.md · 假设驱动建模-cookbook.md · EEVEE-工作要点.md
│   ├── examples/chair-backrest/  # runnable example + recorded results & evidence images
│   └── images/                 # sponsor QR codes
├── tests/                      # reproducible checks (protocol · lease · capability lock · contract · acceptance)
├── lib/                        # prebuilt host output (rebuild if your DSH differs)
└── dsh-blender.config.example.json
```

---

## 11. Self-tests

[`tests/README.md`](tests/README.md) has the full list. `npm test` runs 13 offline steps (capability lock, single-source, addon protocol, lossless-JSON guard, lease staleness, macOS paths, frame dedupe, discoverability + description budget, workDir sharing, provenance staleness, generator dispatch, guidance, backend probe) and needs neither Blender nor DSH. The ones that exercise real Blender:

1. `npm run test:acceptance` — **62 assertions**, driving the compiled `lib/index.js` through a fake ctx (the code the model actually runs), with real `blender.exe` for the headless cases.
2. `blender_rt_headless {preload:"view", script:"print('HEADLESS ' + K.dsh_view_api['selftest']())"}` → `ok:true`, matrix deltas ≈1e-7, `first_px == [25,153,51,255]`.
3. `tests/contract_selftest.py` → **33/33**: registration, envelope violations, AABB+BVH interference, interface gap, destructive guard blocking, evidence md5 ledger, `unresolved → supported`, flip, report, geometry fingerprint, evidence going `stale` after an edit, verdict auto-demotion on drift.
4. `tests/plan_selftest.py` → **18/18**: diagnostics, topological order, dry-run vs real build, `hidden_when` both states, connection offset, `ParamOutOfRange`, `Cycle`, hard-error refusal, envelope integration.
5. `tests/mate_selftest.py` → **57/57** (fit gate, "not attached at all" refutation, deep-penetration refutation, `samples=2 → unresolved`, interference severity + declared exemption, BVH vs pure-numpy cross-check) · `tests/txn_selftest.py` → **55/55** · `tests/deliver_selftest.py` → **53/53** (unit-box normalize, multi-group OBJ+MTL, manifest md5; one byte or one face of tampering must FAIL).
6. `tests/motion_selftest.py` → **25/25**: axis inference, 8-phase sweep `supported` when clean and `refuted` + offender named when a blocker is placed in the path, per-object `matrix_world` restore, URDF/USDA structural self-check.
7. `tests/upstream_integration_selftest.py` → **63 assertions** covering sculpt / fix / UV / print / sweep, material build→apply→scan→bake, and the render guard's install/mark/clear.
8. `blender_rt_plan(op="qc_render_selftest")` → **29/29** (parts-color restore, per-line `device`, view catalog/aliases, projection cross-check) · `audit_gate_selftest` → assembly-gate synthetic check · `generator_selftest` → compile gate + cache + expiry · `qc_self_check` → `iou_self = 1.0`.
9. `tests/engine_probe.sh` → **16/16** and `tests/view_diag_selftest.py` → **16/16** · `tests/png_decode.py <png>` confirms byte-exact colours (no double gamma, no vertical flip).
10. Channel checks: `/health` (backend + provenance) · `/doctor` (`kind=ok`) · `/who` (lease + metrics), plus a 7-request lease walkthrough against `/lease`, `/act`, `/who`, `/release`.

---

## 12. Security, license, credits

- The HTTP backend binds `127.0.0.1` only — **do not** expose it publicly.
- `execute_code` is intentionally a universal escape hatch: it runs arbitrary Python inside the Blender process. Use it only in a trusted environment.
- The `MCP for Blender` addon is **not** included; obtain it separately and respect its license.
- No human-in-the-loop panel, MJPEG streaming or mouse/keyboard injection is included (that route was deliberately dropped: injecting input can leave the system stuck with a held key).
- License: **BSD-3-Clause** (see `LICENSE`). Copyright holder is set to the repository owner — edit `LICENSE` if you need a different attribution.
- Measured numbers in this README and in `tests/README.md` come from the author's machine and are meant as a baseline, not a guarantee.

## Credits

- **@lurenjia-l** — [dsh-blender-stylized-shading](https://github.com/lurenjia-l/dsh-blender-stylized-shading): their measured EEVEE pitfalls (Shader-to-RGB only sees direct light, diffuse extension darkening, gradient-group controller binding) are folded into `docs/EEVEE-工作要点.md`; their `stylized-shading` skill has been adapted to this plugin direct channel.
