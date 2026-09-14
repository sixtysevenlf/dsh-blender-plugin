# DSH × Blender — Direct Realtime Plugin (Shareable Edition)

> 🌐 **Language:** **English (this page)** · [简体中文](README.zh-CN.md)

> **Let an AI model actually drive Blender** — no clicking, no screenshots-into-prompt, no MCP server.
> One direct TCP channel gives the model 10 primitives: **see the viewport / edit the scene / watch over time / run an inner search loop / profile & fix render perf / decimate objects safely / offload heavy work to a headless process / operate the channel itself.**
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

# 3) Inject into DSH (dsh-super-injector dev tools; or wire it into your profile bundles)
#    dev_build_plugin   {"dir":"<absolute path to this package>"}
#    dev_inject_plugin  {"dir":"<absolute path to this package>"}

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
│   ├── view.py                 # custom-view capture (matrices + offscreen + hand-written PNG)
│   └── _probe_tools.mjs        # addon compatibility probe
├── docs/                       # tutorial / config reference / mechanics & pitfalls (zh)
├── tests/                      # reproducible checks (matrix cross-check, PNG bytes, lease)
├── lib/                        # prebuilt host output (rebuild if your DSH differs)
└── dsh-blender.config.example.json
```

---

## 11. Self-tests

[`tests/README.md`](tests/README.md) has the full list. The key ones:

1. `blender_rt_headless {preload:"view", script:"print('HEADLESS ' + K.dsh_view_api['selftest']())"}` → `ok:true`, matrix deltas ≈1e-7, `first_px == [25,153,51,255]`.
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
