# DSH × Blender — Direct Realtime Plugin (Shareable Edition)

> 🌐 **Language:** **English (this page)** · [简体中文](README.zh-CN.md)

> **Let an AI model actually drive Blender** — no clicking, no screenshots-into-prompt, no MCP server.
> One direct TCP channel gives the model 10 primitives: **see the viewport / edit the scene / watch over time / run an inner search loop / profile & fix render perf / decimate objects safely / offload heavy work to a headless process / operate the channel itself.**
>
> Version **0.9.3** — lands the external feedback in [docs/feedback/插件改进交接-2026-09-24.md](docs/feedback/插件改进交接-2026-09-24.md) ([landing record](docs/feedback/改进落地记录-2026-09-24.md)):
> **① Route decision: headless batching is the first path** ([decision doc](docs/headless优先-路线决定.md)); the live-GUI channel stays as an interactive add-on (no tool removed).
> **② Timeouts never swallow results**: the headless client wait window (`DSH_HEADLESS_WAIT_MS`, 100 s) returns `{kind:"promoted", jobId:"run-…"}`; collect with `blender_rt_job(op="collect"|"wait", id=…)`. Expecting >100 s? Pass `as_job=true`.
> **③ Structured receipts**: text block 0 is a single-line JSON envelope (`status/resultJson/resultPath/resultTruncated/stdoutTail/inputFile/shots/pathWarnings/…`), block 1 is the human summary — no more hand-written regexes.
> **④ Observable long jobs**: all three spawn sites inject `PYTHONUNBUFFERED=1`; scripts emit `dsh_stage("building")` heartbeats, and `op=status` reports `stage/idleMs/lines/logBytes/pidAlive`.
> **⑤ Paths**: `outdir/out_json/file` accept WSL paths and echo both real forms; `shots=[…]` renders multiple views in one call (with `coverage_estimate` and a <5% warning).
> **⑥ Job layer**: `op=start` mirrors headless (`script_file/args/env/…`), new `op=wait`, runs and jobs share one id space, stale entries no longer claim to be running.
> Acceptance: `npm run test:acceptance` (62 assertions, real Blender 5.2.2).
>> Version **0.8.0** — default render engine is now **EEVEE + ray tracing** (GPU-only, no device-preference dependency; measured 1.35 s vs 3.13 s per warm frame against Cycles GPU). — hardening from real-world feedback: **GPU semantics** (headless Cycles silently fell back to CPU — measured **15.4×**), a **hot headless session** (`blender_rt_worker`), **streaming long calls** (client `fetch` headers timeout is 300 s — measured `UND_ERR_HEADERS_TIMEOUT`), structured failures, and artifact filtering. See §4.6.
>
> Version **0.5.0** — adds a **contract layer** (hypotheses / ranges / checks / evidence / destructive-op gating) and a **planner** (Component·Connection·Feature graph compiled to bpy), exposed through the new `blender_rt_plan` tool. See §4.5.
>
> Version **0.4.1** — adds an **addon protocol adapter** (`addonProtocol`, default `auto`), so both the flat `MCP for Blender` addon and the `harveyxiacn/blender-mcp` category/action addon work (contributed by [@yihefeikong-rgb](https://github.com/yihefeikong-rgb), PR #2).
>
> Version **0.4.0** · ships its own runtime (Node + Python), all paths resolved **relative to the package**, and **nothing is hard-coded to a specific machine** (`runtime/config.mjs` does env → config file → auto-detect → defaults).

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
| **Headless process** for heavy renders / batch geometry | `blender_rt_headless` | cold start 0.8 s |
| **Contract layer + planner** | **`blender_rt_plan`** | AABB sweep 24 ms · BVH 0.17 ms/pair (307 objects) |
| **Hot headless session** | **`blender_rt_worker`** |
| **Transactions / rollback** | **`blender_rt_txn`** | snapshot 96.7 MB / 824 ms (300 objects) · mark→revert verified | reuse one `blender -b` across calls (no 0.9–1.2 s cold start); `K` persists |
| **配方库 / Presets** | **`blender_rt_preset`** | 存/套用/导出参数配方；实测 720 → 640 真实生效 |
| Channel health check / write lease | `blender_viewport` | health 70–100 ms |

Detailed walkthrough (Chinese, 377 lines): [`docs/操作教程.md`](docs/操作教程.md) · configuration reference (Chinese): [`docs/配置参考.md`](docs/配置参考.md). This README covers the same ground in condensed English.

---

## 2. Requirements

1. **Blender 4.x / 5.x running in GUI mode** — this channel needs a live GUI session (`-b` is only used by the headless tool).
2. **Blender addon `MCP for Blender`** (**not bundled**) listening on `127.0.0.1:9876`. It must provide at least:
   `ping`, `get_scene_info`, `get_world_state_snapshot`, `get_object_info(name)`, `get_viewport_screenshot(max_size, filepath, format)`, `execute_code(code)`.
   The **harveyxiacn enhanced `blender_mcp_addon`** is also supported (different wire protocol — the plugin adapts automatically; `addonProtocol`, default `auto`, see `docs/配置参考.md` §6). That implementation has none of the five asset integrations, and the corresponding commands fail loudly instead of pretending.
   Enable it in Blender, then in a 3D viewport press `N` → **MCP for Blender** panel → **Connect**.
   Compatibility probe: `node runtime/_probe_tools.mjs`. Protocol self-test (no Blender needed): `node tests/protocol_selftest.mjs`.
3. **Node.js ≥ 20**.
4. **DSH (DeepSeek Harness)** — this package registers tools as a DSH plugin (`inject: ['tools']`).
   Works both with DSH in WSL + Blender on Windows (WSL interop is used to spawn `blender.exe`) and with DSH and Blender on the same Windows machine (path mapping degrades gracefully).

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

# 4) Start Blender → press N → "MCP for Blender" → Connect (127.0.0.1:9876)

# 5) Verify
curl -sS http://127.0.0.1:9877/health    # backend alive
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

### 4.6 Hardening from field feedback (v0.6.0)

Four issues reported by a heavy user (22 modelling rounds, 45 pipelines) — all reproduced and fixed:

| Issue | What we measured | What changed |
|---|---|---|
| Headless renders on **CPU silently** | 1821-object project, 480×270/32spp: **CPU 2.78 s vs GPU 0.18 s warm = 15.4×**; `--factory-startup` clears preferences and even `factory_startup=false` did not inherit them | `gpu:"auto"` prelude (OPTIX→CUDA→HIP→ONEAPI→METAL), explicit `gpu` field in every result (`before/after/configured/fell_back_to_cpu`), `use_user_config:true` forwards `BLENDER_USER_CONFIG/SCRIPTS`; `gpu:"true"` fails loudly when no GPU exists |
| No **hot headless** session | the addon's socket server returns early in `background`, so headless meant a cold process every time | new `runtime/worker.py` + tool **`blender_rt_worker`**: a resident `blender -b` with a **blocking accept loop on the main thread** (timers never fire headless — measured 0 in 1.2 s) and the **persistent kernel K** (`K.n` survives across `exec` calls) |
| Long calls die with `fetch failed` | client `fetch` **headers timeout = 300 s** (independent probe: 330 s delay → `fetch failed after 300.9s, cause=UND_ERR_HEADERS_TIMEOUT`); and a client abort does **not** kill the server-side child (artifacts still land) | `/headless` and `/worker` now stream **ndjson**: headers immediately + 15 s heartbeats + the result as the last line |
| Opaque failures & noisy artifacts | — | full stdout/stderr written to files (`logs.*`), `lastException` + `traceback` extracted, `reason` field, a hint when a script contains a literal `\n`, and `outdir` filtering of `__pycache__ / *.pyc / *.blend1|2 / tmp*` (count reported) |

**Long-task semantics**: a client timeout/abort is **not** a task failure — the server-side child keeps running, artifacts still land in `outdir` and the log paths are returned. For work beyond ~5 minutes prefer the hot worker session or write results to a file.

### 4.7 Transactions, QC and observability (v0.7.0)

- **Transactions** — new tool **`blender_rt_txn`**: file-level `snapshot`/`restore` (`copy=True`, so the current filepath is untouched; measured **96.7 MB / 824 ms** on a 300-object scene) and object-level `mark`/`revert` (transforms, materials, visibility, modifier flags — **no topology/UV changes**).
- **Built-in QC** — `runtime/qc.py`, exposed as `blender_rt_plan(op="qc_compare")` (plus `qc_compare_basic`, `qc_self_check`, `qc_robustness_check`, `qc_help`). Optimized beyond the reference implementation: adaptive mask (alpha detection / border-estimated background + Otsu), centroid alignment with a scale+shift search, actionable metrics (Dice / missing / extra / boundary distance / per-band profile), and anti-gaming guards (`iou` vs `iou_fixed`, scale-drift warning, fixed alignment inside the optimizer loop). Self-check: self-IoU **1.0000**, 12 px shift still **1.0000**; a 12 px + 6% perturbation collapses the naive metric to **0.534** while the searched one holds **0.866**.
- **Streaming receipts fixed** (v0.8.9) — `/headless`, `/worker`, `/txn` and `/preset` write a `{"heartbeat":true}` NDJSON line every 15 s before the final object, but the client used to `JSON.parse` the whole body: **any call longer than 15 s degraded to `exit=undefined` even though the subprocess had finished (exitCode=0)**. The client now takes the last non-heartbeat JSON line and reports the heartbeat count. Measured: a 20 s script went from `HEADLESS 失败 · exit=undefined · undefinedms` to `HEADLESS ok · exit=0 · 21406 ms` with the full result/logs; a 20 s `rt_worker op=exec` now returns `WORKER exec ok · 20000ms`. Same release: job-layer `preload`, quoted `args`, a zero-size image guard (`GIF` is silently decoded as 0×0 by Blender), two new error hints (stale `StructRNA` references after `open_mainfile`, `context is incorrect`), and a hint to use `as_job` past 60 s.
- **Multi-view render harness** (v0.8.8, P2-1) — `runtime/qc_render.py`, exposed as `blender_rt_plan(op="qc_render_views", args={file, views[], res, samples, budget_s|thr, outdir, ref_path})`. One call renders N views: auto-framing from the merged AABB of all visible meshes (per-corner solve + `margin`), a temporary fixed three-point rig (SUN key 3.2 / fill 1.0 / rim 2.4 at fixed camera-relative angles — created and deleted inside the call, scene settings restored), per-view PNG + ms + md5, and `<outdir>/render_views.jsonl` (one line per view). A cumulative budget (`budget_s`) stops the run immediately and reports `within_budget=false` while keeping finished images; optional `ref_path` runs `qc_compare` (fixed alignment by default) per view. `args.asJob=true` forwards long runs to the job layer (headless process = scene isolation). Measured on the 350-visible-mesh scene (512×512 / 64 samples / EEVEE+RT): iso **2124 ms** (first frame includes shader compile), front **798 ms**, right **565 ms** — 3 images in **3487 ms**, jsonl 3 lines, 36 px framing margin on a 512 px frame (predicted bbox vs actual alpha bbox agree within 2 px); a 1.0 s budget run stops after one image with `within_budget=false` and keeps it.
- **Observability** — exceptions now return partial `stdout`/`stderr`/`traceback` (marker-wrapped); misleading `diagnosis` no longer attaches to execution errors; `rt_do` reports main-thread occupancy and suggests headless/worker past 1 s.
- **Paths** — `K.win_path / K.wsl_path / K.blend_path / K.out_dir` inside Blender (both GUI and headless), plus `K.run(path, reload_modules=True)` and `blender_rt_do(file=...)`.

### 4.8 Fixes from field feedback (v0.9.1) — rifle-build《93 report》, 14 items

All four P0 "silent failure" reports were reproduced and fixed; D2 (empty custom-view frame) was **not** reproduced — it was an *aiming* mistake
(`from`/`look_at` pointing where no geometry is), so the fix is a self-diagnosing capture instead of a camera change.

- **A1 unknown views are no longer substituted silently** (`qc_render` v3): a non-empty `views` list where *nothing* resolves → `ok:false` + `unknown_views` +
  `available_views` (22-name catalog) + `hint`, and **no image is written**. Counts are always reported (`requested_count / rendered_count / unknown_count`).
  Spelling aliases added (`side_left → left`, `iso_right → iso_br`, …); genuinely ambiguous names (`muzzle_end`, `breech_end`) are **never guessed**.
  Omitting `views` keeps the v0.8.11 default of **3** views (iso/front/right) — this patch does not silently change your default artifact set.
- **A2 headless QC now defaults to `view_transform="Standard"`** (AgX/Filmic washing-out made threshold reads useless). Pass `"scene"` for beauty renders;
  the actually-used transform is echoed in the response **and in every jsonl line**, and the scene's original transform is restored (verified: returns Standard, scene stays AgX).
- **A3 structured results no longer live only in stdout**: `blender_rt_headless(out_json=<path>)`; results >4 KB are auto-dumped to `<workdir>/results/<runId>.json`
  (`resultPath` / `resultBytes`); the `result` shown in the tool text went 2 KB → 6 KB; the plan channel went 4 KB → 12 KB.
- **A4 fake failures are classified**: response `status` ∈ `finished / script_error / blender_error / timeout / gpu_required_missing / blender_exe_missing`
  + `failure_hint`. Blender's exit code stays **0** when a `--python` script raises (measured), so `status` is derived from the receipt plus stderr markers.
  Every run carries a `runId` — a client timeout is not a task failure: recover with `blender_rt_job(op="status"/"collect", id=runId)`.
- **B1/B2 callable APIs**: `K.dsh_*_api("op", {…})` → parsed **dict** (`api.call` alias); `api["dispatch"](op, json_str)` still returns a string.
- **B3 `script_file=<.py>`** on `blender_rt_headless` (and `file=` now auto-detects `.py`), so scripts no longer have to be written → read → passed as strings.
- **B4 env contract**: `env={…}` plus auto-injected `DSH_RUN_ID / DSH_OUTDIR / DSH_ARGS / DSH_SESSION / DSH_PLUGIN_VERSION`, readable as
  `K.args / K.run_id / K.session / K.env` inside the script. (Measured trap: env does **not** cross the WSL→Windows spawn boundary — `WSLENV` is now set and the values are injected in-script too.)
- **C1/C2 file-level operators** (`audit` v4): `audit_overlap(file_a, obj_a, file_b, obj_b)` (BVH face pairs + real intersection segments + bbox) and
  `audit_interference(...)` (intersection **volume estimate in mm³ with a 95% CI** and a three-state verdict; Monte-Carlo ray-parity, sampling only inside the
  intersection of the two world AABBs; open/non-manifold meshes → `unresolved`, never a plausible-looking wrong number).
  `audit_connectivity / audit_gate / audit_measure` now take `file=` (temporary load → same pipeline → cleanup either way, reported in `cleanup`).
- **D1 GUI primitives** (`view` v2): `gui_frame(object=…) / gui_shading(mode=…) / gui_open(path=…) / gui_help()` run in a **real UI context**
  (inside `rt_do` `bpy.context.screen` is `None`). Measured: `gui_frame(object="Cube")` → `framed:"Cube", mode:"selected"`.
- **D2 self-diagnosing custom view**: `coverage_estimate` (background taken as the modal colour, so `background=true` cannot fool it), `objects_in_frame`
  (projects each bbox), `scene_bbox`, and when nothing is in frame a `warning{code:"frame_looks_empty", suggest:{from,look_at,lens}}` —
  following the suggestion took coverage from **0.00% → 68.11%** without pressing Home. Also fixed: the `x-dsh-view` header carried only 5 fields and crashed on non-ASCII (502).
- **E1 render queue/lock** (replaces members' hand-rolled `render_lock.py`): cross-process file lock `<workdir>/locks/render.lock`
  (`op="render_lock"` acquire/release/status; `qc_render_views` acquires automatically and reports `waited_ms`/holder/stale in the response and every jsonl line).
  Measured: second holder with `wait_s=1` → refused with `waited_ms=1001`; wrong-holder release refused; a stale lock (2 h old, TTL 60 s) is broken with `stale_broken:true`.
- **E2 session-scoped defaults**: `DSH_SESSION` (default `plugin-pid-<pid>`) names the default evidence file (`dsh_evidence_<session>.png`) and appears in `status` with `resultsDir`.
- Tool-layer bugs found and fixed while integrating: the plan tool could **silently drop `args`** (the harness sometimes delivers a JSON *string*) and still return `ok:true`;
  `args` arrays were stringified into `"[delta]"`; `perf`/`opt` sent `{}` to zero-arg ops (python got an extra positional argument).
- New regression harnesses: `tests/engine_probe.sh` (**16/16**) and `tests/view_diag_selftest.py` (**16/16**); `qc_render` selftest **29/29**.

## 5. Tool reference

### `blender_rt_see` — look
`max_size` (longest edge, default 560) · `full`+`area` (whole Blender window / Nth area) ·
`from` `look_at` (`"x,y,z"` — custom view) · `lens` `ortho` `ortho_scale` · `view_size` (`"1280x720"`) ·
`shading` (`WIREFRAME|SOLID|MATERIAL|RENDERED`) · `overlays` · `view_mode` (`viewport` default / `render`).

Custom view = matrices are built in Python and drawn with `GPUOffScreen.draw_view3d`: **no camera object, no `scene.camera` change, no user-viewport change**; `shading`/`overlays` are restored right after the capture. Validated against `Camera.calc_matrix_camera()` with a max delta of **1.2e-7**; same spec twice gives an identical MD5.

### `blender_rt_do` — do (and optionally see)
```text
blender_rt_do(code="K.n = getattr(K,'n',0)+1\nbpy.data.objects['Cube'].rotation_euler.z += 0.4", see=True)
```
Runs on Blender's main thread with `bpy/math/mathutils/Vector` preloaded. `execute_code` gets a fresh namespace every call, so state must live on the persistent kernel **`K`** (`sys.modules["dsh_rt_kernel"]`).

### `blender_rt_watch` — watch over time
```text
blender_rt_watch(code="bpy.app.timers.register(tick)", seconds=2.5, fps=4, max_size=420)
```
Returns up to 6 frames plus a per-frame hash; identical hashes mean the picture never changed.

### `blender_rt_loop` — inner search loop (the core primitive)
```text
blender_rt_loop(op="start", spec={
  "setup":  "K.ob = bpy.data.objects['Cube']",
  "step":   "ns['params'] = {'sx': 1 + (ns['i'] % 7) * 0.1}",
  "measure":"K.ob.dimensions = (ns['params']['sx'], 1.0, 1.0)\nns['score'] = abs(K.ob.dimensions.x - 2.0)",
  "iterations": 3000, "budget_ms": 20000, "interval": 0, "measure_every": 5,
  "minimize": True, "top_k": 20
})
blender_rt_loop(op="status") · op="board" · op="export" (path/top) · op="stop" · op="help"
```
`setup/step/measure` share a namespace `ns`; `measure` must set `ns["score"]` and may set `ns["params"] / ns["metrics"] / ns["violations"]`. Preloaded: `bpy / K / math / random / numpy / i / frac / penalize / anneal / record`. **Always pass `iterations` or `budget_ms`** — that is your safety valve.

### `blender_rt_cmd` / `blender_rt_commands` — raw addon surface
```text
blender_rt_cmd(name="get_object_info", params={"name": "Cube"})
blender_rt_commands()
```
Signatures are the addon's real ones (`get_scene_info()` takes no args; `get_object_info(name)` uses `name`). There is no schema layer to rename things for you.

### `blender_rt_perf` — render performance
`op="status" | "analyze" | "apply" | "revert"`. `apply` sets `use_persistent_data`, a **denoiser auto-detected from your GPU** (OptiX ↔ OpenImageDenoise), `denoising_use_gpu`, `use_auto_tile=False` and a sample cap; `revert` restores the snapshot taken before `apply`.
Reference numbers (author's scene, 2318 objects): per-render CPU-side sync ≈4.2 s; with persistent data, repeated renders went **14.6 s → 0.79 s**.

### `blender_rt_opt` — safe object decimation
```text
blender_rt_opt(op="analyze")
blender_rt_opt(op="join", args={dry_run=False, save_before="/tmp/pre_join.blend"})
```
Merges only objects sharing collection / material / parent with no modifiers, animation, shape keys, custom properties, instancing or library link. Face and vertex counts are preserved exactly.

### `blender_rt_headless` — separate `blender -b` process
`script` · `file` · `outdir` · `args` · `timeout_ms` · `factory_startup` (default true) · `bootstrap` (persistent kernel `K`) · `preload` (`"view"`, `"perf"`, …).
A script line `print("HEADLESS {json}")` comes back as `result`. It does not occupy the GUI channel, and it is the right place for Cycles finals and batch work.

### `blender_viewport` — operate the channel
`status` · `doctor` (real bpy round-trip + 3-level diagnosis + effective config) · `who` · `lease` / `release` (`force=true` steals) · `start` / `stop` / `restart`.

---

### `blender_rt_plan` — contract layer + planner

Answers the white-box question: *how does the model justify what it built, and what happens when the evidence is not enough?*

- **Contract ops** (`op=…`): `register_component` / `register_connection` (candidate types, parameter ranges, forbidden ops, confidence, required evidence) / `register_envelope`;
  `check_envelope` / `check_interference` (AABB sweep + BVH refine) / `check_interface`;
  `destructive_guard` — blocks `boolean_union / weld / merge / apply_transform` while a connection is **not resolved** (returns `Unsupported Destructive Merge`);
  `evidence` / `ledger` — every capture recorded with path, **md5**, size, view spec; `report` — provenance report.
- **Hypothesis lifecycle (S2)**: `verify` gives three-state verdicts — `supported` / `refuted` / **`unresolved`** (external error within tolerance but the deciding parameter is *unidentifiable*) — and `flip` / `advance` record hypothesis changes.
- **Planner ops** (`plan_…`): `plan_load` / `plan_validate` / `plan_order` / `plan_build` (`dry_run` first) / `plan_graph` (mermaid/dot).
  Graph = Component (box/cylinder/sphere/mesh_copy) · Connection (candidates, status, forbidden, offset) · Feature (array/grid/mirror).
  IDE-style diagnostics: `UnresolvedConnection`, `UnsupportedDestructiveMerge`, `MissingComponent`, `Cycle`, `ParamOutOfRange`, `UnknownKind`; hard errors refuse to compile.
- **Assembly-level gates (v0.9.0)**: `audit_connectivity` / `audit_gate` — every component is asked *"are you attached to anything else?"* (bbox pre-screen + **BVH mesh confirmation**; visible floater = longest bbox edge ≥ 1% of the model; the `micro_gap_mm` caliber is reported back, 0.3 mm = single-solid/3D-print, use 1–2 mm for assemblies designed with clearance), `audit_drift` (symmetric Chamfer **shape** drift, point-to-surface: translation/scale-invariant by design, `bbox_delta` carries the movement), `audit_measure`, `audit_snap_floaters` (report-only by default; never rips a welded part). `audit_scene` / `audit_mesh` / `audit_duplicates` and `montage` are **actually routed** now — they were documented but had never been wired (v0.9.0 fixes that).
- **Verdicts that expire (v0.9.0)**: `fingerprint` binds evidence and verdicts to geometry digests, `ledger` marks entries `stale`, and `verify` stores a bbox snapshot so a `supported` verdict **auto-demotes to `unresolved`** once geometry drifts (>10% diagonal or >20% size; animated objects are skipped).
- **Fits and interference (v0.9.0)**: `mate_check` measures contact-area fraction / median single-side gap / penetration depth **on the actual mesh** (`fit` classes: clearance 0.25 · location 0.15 · press −0.05 · snap 0.20 mm per side), `fit_help` serves `runtime/assembly_features.json` (9 features + ISO 273 + the shared-nominal rule), `interference_report` adds depth/volume/severity and a `declared` exemption for registered joints.
- **Mechanisms (v0.9.0)**: `motion_joint` / `motion_infer_axis` (two independent evidence paths — rotational symmetry and contact strip; conflicting evidence ⇒ `unresolved`, never a pick) / `motion_measure` (sweep = kinematic+BVH evidence, **not** physics) / `motion_export_urdf` / `motion_export_usda` (single tree, degenerate joints reported as `fixed`, SI units, structural self-check).
- **Generators (v0.9.0)**: `generator_save` / `generator_run` — the program is the shape: reproduce it in a **fresh headless Blender process** (compile gate), compare against `expect` (three-state) and cache by source hash; `generator_list|get|diff` — change the source and the previous receipt is void.

**The rule that matters**: when two hypotheses fit the visible evidence equally well (measured residuals **184 vs 184**), the verdict is `unresolved` **plus the probe you need** — never a coin flip.
Worked example with numbers (~3 s, headless): `docs/examples/chair-backrest/`; method: `docs/假设驱动建模-cookbook.md`. Self-tests: `tests/contract_selftest.py` (**33/33** — 24 baseline + 9 v0.9.0) and `tests/plan_selftest.py` (18/18).

## 6. Recipes

1. **Change-and-look**: `blender_rt_do(code="...", see=True)` — batch 10–30 steps, then take a wider look.
2. **Multi-angle acceptance**: the same `from/look_at/view_size` yields a stable MD5, so it works as an acceptance fingerprint.
3. **Inner-loop fitting**: define `score`, constraints via `penalize`, step size via `anneal`; when it converges, **verify through a different path** (e.g. re-measure with `blender_rt_do`), because the inner loop only optimises the score you wrote.
4. **Render pipeline**: `perf status → analyze → apply → status` (and `revert` if you dislike it).
5. **Decimation**: `opt analyze → join (dry_run) → join (save_before=…)` then re-check face/vertex counts.
6. **Offline renders**: `blender_rt_headless {file, outdir, timeout_ms, preload:"view,perf", factory_startup:false}`.

---

## 7. Configuration

Resolution order: **environment → config file → auto-detect → default**.

| Setting | Env var | Config key | Default |
|---|---|---|---|
| Working dir (frames/scripts) | `DSH_BLENDER_WORKDIR` | `workDir` | `%LOCALAPPDATA%\dsh-blender-rt` (mapped to `/mnt/c/...` in WSL) |
| Blender executable | `DSH_BLENDER_EXE` | `blenderExe` | auto (Program Files / Steam / PATH) |
| Addon host/port | `DSH_BLENDER_ADDON_HOST/PORT` | `addonHost`/`addonPort` | `127.0.0.1:9876` |
| Backend port | `DSH_BLENDER_HTTP_PORT` | `httpPort` | `9877` |
| Lease identity / TTL | `DSH_BLENDER_HOLDER` / `DSH_BLENDER_LEASE_TTL_MS` | `holder`/`leaseTtlMs` | `plugin-pid-<pid>` / 600000 ms |

Config file locations: `$DSH_BLENDER_CONFIG` → `<package>/dsh-blender.config.json` → `~/.dsh/dsh-blender.config.json`.
Check the **effective** config any time: `curl -s http://127.0.0.1:9877/who` (look at the `config` block) or `blender_viewport op=doctor`. Full details: [`docs/配置参考.md`](docs/配置参考.md).

---

## 8. Differences from the author's self-build

The published version only changes machine-bound parts; channel mechanics, all 10 tools, the lease and the inner loop behave identically.

| Item | Author's build | This edition |
|---|---|---|
| Working dir | hard-coded `D:\DSH\blender\tmp` ↔ `/mnt/d/...` | auto-detected + configurable |
| `blender.exe` | hard-coded Steam path | auto-detected + configurable |
| Ports | hard-coded 9876/9877 | configurable (two Blenders side by side) |
| Denoiser default | always `OPTIX` | auto (OptiX ↔ OpenImageDenoise) |
| Platform | author's WSL+Windows shape | WSL+Windows **and** all-Windows |
| Docs | author workspace only | bilingual README + tutorial + config reference |

---

## 9. Troubleshooting

| Symptom | What to run | Likely cause |
|---|---|---|
| "backend unavailable" | `blender_viewport op=start` | backend not running / port taken |
| `blender-unreachable` | check Blender's N panel | Blender not running, or addon not connected |
| `main-thread-busy` | wait, or go headless | Blender's main thread is busy (render/modal op) |
| `addon-thread-stuck` | don't spam calls | previous long command still running (`blender_rt_loop op=stop` abort) |
| `addon-thread-stuck` + a `detected_protocol` field | set `addonProtocol` as the message says, then `blender_viewport op=restart` | the other addon implementation is installed — protocol mismatch (flat vs category/action) |
| Frame/path errors | `doctor` → `config.workDir` | dir not shared between both ends |
| `409 leased` on writes | `blender_viewport op=who` | another session holds the write lease (`force=true` to take over) |
| `blender-exe-missing` | `doctor` → `config.blenderExe` | executable not found — configure it |

Decoding a failure is a three-level diagnosis: TCP unreachable → Blender/addon not up; TCP fine but bpy calls time out → main thread busy; TCP fine but `ping` also fails → the addon client thread is stuck.

---

## 10. Repository layout

```text
dsh-blender-plugin/
├── src/index.ts                # DSH host: 10 tools, backend watchdog, lease heartbeat
├── runtime/
│   ├── config.mjs              # env → config file → auto-detect → defaults
│   ├── engine.mjs              # addon socket client, command catalog, view/headless
│   ├── server.mjs              # HTTP backend on 127.0.0.1:9877 (+ lease gating)
│   ├── runner.py               # inner-loop runner v2 (timers, limits, penalize/anneal/boards/export)
│   ├── perf.py                 # render preset (auto denoiser) + safe object join
│   ├── contract.py             # S1+S2: components/connections/envelopes, checks, destructive guard, evidence ledger, verdicts
│   ├── planner.py              # S3: Component·Connection·Feature graph → diagnostics → compile to bpy
│   ├── worker.py               # v0.6.0: resident `blender -b` (blocking accept on the main thread) for hot sessions
│   ├── view.py                 # custom-view capture (matrices + offscreen + hand-written PNG)
│   └── _probe_tools.mjs        # addon compatibility probe
├── docs/                       # tutorial / config reference / mechanics & pitfalls (zh)
│   ├── 假设驱动建模-cookbook.md   # hypothesis → range → search → evidence → verdict (S0)
│   └── examples/chair-backrest/ # runnable example + recorded results & evidence images
├── tests/                      # reproducible checks (matrix/PNG/lease + contract 24/24 + plan 18/18)
├── lib/                        # prebuilt host output (rebuild if your DSH differs)
└── dsh-blender.config.example.json
```

---

## 11. Self-tests

[`tests/README.md`](tests/README.md) has the full list. The key ones:

1. `blender_rt_headless {preload:"view", script:"print('HEADLESS ' + K.dsh_view_api['selftest']())"}` → `ok:true`, matrix deltas ≈1e-7, `first_px == [25,153,51,255]`.
2. `tests/contract_selftest.py` (headless) → **33/33**: registration, envelope violations, AABB+BVH interference, interface gap, destructive guard blocking, evidence md5 ledger, `unresolved → supported`, flip, report — plus v0.9.0: geometry fingerprint, evidence going `stale` after an edit, verdict auto-demotion on drift.
3. `tests/plan_selftest.py` (headless) → **18/18**: diagnostics, topological order, dry-run vs real build, `hidden_when` both states, connection offset, `ParamOutOfRange`, `Cycle`, hard-error refusal, envelope integration.
4. `tests/mate_selftest.py` (headless) → **57/57**: fit gate (0.1498 mm measured vs 0.1500 designed), "not attached at all" refutation, deep-penetration refutation, `samples=2 → unresolved`, interference severity + declared exemption, BVH vs pure-numpy cross-check.
5. `tests/txn_selftest.py` → **55/55** (pending-edit protocol: accept requires a check + a non-empty `verified`, revert is budget-free but capped, dither guard) · `tests/deliver_selftest.py` → **53/53** (unit-box normalize, multi-group OBJ+MTL, manifest md5; one byte or one face of tampering must FAIL).
6. `tests/motion_selftest.py` → **25/25**: axis inference (0.00° vs the true axis), 8-phase sweep `supported` when clean and `refuted` + offender named when a blocker is placed in the path, per-object `matrix_world` restore, URDF/USDA structural self-check.
7. `blender_rt_plan(op="qc_render_selftest")` → **14 assertions** (parts-color restore, per-line `device`, view catalog/aliases, projection cross-check) · `audit_gate_selftest` → assembly-gate synthetic check · `generator_selftest` → compile gate + cache + expiry.
4. `docs/examples/chair-backrest/run.py` → **3 s**, unresolved → probe → unique support (numbers in §4.5).
2. `tests/png_decode.py <png>` → confirms byte-exact colours (no double gamma, no vertical flip).
3. Lease walkthrough (7 requests) against `/lease`, `/act`, `/who`, `/release`.
4. `/health` + `/doctor` (`kind=ok`) + `/who`.

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
