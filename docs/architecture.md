# Architecture

This is the single architecture guide for the DSH x Blender plugin. It explains the current system
shape, the boundaries that must stay stable, and the constraints that determine how changes are made.
Git history and the local refactor workspace keep older rationale outside the published repository.

## 1. System shape

The plugin is a DeepSeek Harness host plugin, not a standalone Blender add-on.

    DSH host
      -> src/ tools
      -> Gateway HTTP (127.0.0.1:39877)
      -> engine dispatch
      ^ WebSocket /addon (the addon dials out)
      -> Blender addon
      -> bpy

There are three logical processes and one inbound endpoint:

- the DSH host and the Gateway run in Node (the Gateway may be embedded in the host);
- the Blender addon runs inside Blender and dials the Gateway; it has no inbound listener;
- the addon is the only process that calls bpy;
- the Gateway owns jobs, leases, sessions, receipts, and routing;
- the host owns the model-facing tools and the headless/fallback policy.

The public model surface is 6 tools (blender_session, blender_op, blender_py, blender_see,
blender_run, blender_inspect), 36 planning families, 239 operations, 25 addon commands
(25 registered commands; the transport-only ping makes 26 wire commands), 29 HTTP routes, and
protocol 12. These counts are facts, not goals; changing one requires updating the catalog, lock,
tests, and migration notes in the same change.

## 2. Host plugin (src/)

src/index.ts is entry-only. It exports the plugin API (from src/plugin.ts) and the frame cache
helpers, and wires the host modules; it must not grow feature logic. Its export set is registered in
`config/facades.json` and enforced by `npm run check:no-shims` (see
[Public facades and the dual entry](#public-facades-and-the-dual-entry)). The rest of the tree is
split by responsibility:

- src/tools/: one model-facing tool per directory (registration, execution, presenter/receipt
  pieces) plus a shared session helper;
- src/host/: backend lifecycle and HTTP client;
- src/contracts/: typed contracts and schema helpers;
- src/ui/ and src/tui/: DSH UI cards and terminal behavior;
- src/plugin*.ts: plugin lifecycle and surfaces.

Boundaries are enforced, not just described: `eslint.config.mjs` declares each src
subdirectory as an element and treats `src/index.ts`, `src/constants.ts`, and
`src/plugin*.ts` as file categories, then applies a default-deny matrix. Adding a
directory or root file means registering it; adding a cross-element import means
changing the matrix (see section 8).

Host code uses strict TypeScript. Any value from the backend, addon, model, or user file system is
parsed at one boundary before it enters typed logic. Explicit any is not accepted in foundation files;
the remaining type-debt ledger must have an owner, reason, and expiry.

## 3. Backend runtime (runtime/, built to lib/)

The backend is strict TypeScript under runtime/. It is compiled with runtime/tsconfig.json into
lib/runtime/**/*.js. Published packages contain built JavaScript, not backend TypeScript sources.

Layout:

- runtime/core/: dispatch, kernel, scripts, catalog, engine;
- runtime/execution/: headless, jobs, workers, recovery;
- runtime/http/: routes, lease, host views;
- runtime/gateway/: the Gateway process, addon WebSocket channel, ownership, and lifecycle;
- runtime/transport/: frames, sockets, protocol helpers;
- contract tables: `config/contracts.json` is the catalog <-> Python link layer (described in section 8); there is no separate runtime contract-table directory;
- runtime/py/: the Blender-side Python library described below;
- native/win-hidden-desktop/: the C++17 hidden-desktop helper used by Windows virtual-gui. `npm run build:win-helper` builds it into `native/win-hidden-desktop/out/` (gitignored); releases stage the binary under `vendor/win-hidden-desktop/`, so published packages contain the binary but not the C++ source.

Runtime dependency directions are frozen in `eslint.config.mjs`: each runtime
subdirectory is an element, loose `runtime/*.ts` modules are the `runtime-root`
file category, and the policy matrix allows only the measured edges. Existing cycles
are registered as-is; this gate does not reorder them.

The runtime build precedes the host build because host modules import boundary helpers from the built
runtime. The Gateway entry used by GATEWAY_PATH is lib/runtime/gateway.js. Source maps are emitted so a
crash still points at a TypeScript line.

### Public facades and the dual entry

The build has exactly two public facades, one per entry:

- `src/index.ts` is the host entry consumed by the DSH host: `name`, `inject`, `Config`, `apply`,
  `__internals`, `frameDedupe`, `resetFrameCache`;
- `runtime/engine.ts` is the backend runtime entry, compiled to `lib/runtime/engine.js`. It exposes
  only the consumed surface: `createEngine` plus the catalog tables and helpers, the class-guide
  helpers, flags, `PATH_GUARD`, `PLUGIN_VERSION`, and `engineProvenance`. Internal runtime, src, and
  test code imports implementation modules directly (`runtime/paths.ts`,
  `runtime/core/evidence.ts`, `runtime/transport/probes.ts`, ...); a facade copy that insulates nobody
  is dead code and is removed in the same change.

`config/facades.json` is the checked-in machine-readable inventory: each facade file, its exports, and
for each export the consumer files that use it. `npm run check:no-shims` enforces the registry: the
facade set is exactly the two files, every export is registered with at least one consumer file that
mentions it, an unregistered export fails the gate, a stale registration fails, wildcard exports are
rejected, and the retired list cannot come back. Curation is a review step, not script-generated:
grep the consumers, update the registry and the facade together.

Two entry paths share the same engine core. External scripts, tools, and tests import
`lib/runtime/engine.js` (check-parity, install-smoke, sync_counts, GUI probes, unit and integration
tests); `runtime/gateway/app.ts` imports the created engine and `engineProvenance` from source;
`runtime/catalog/logic.ts` runs the built facade in a child process to fingerprint it.
`engineProvenance` hashes the facade file on disk, so facade edits are observable, and `check:build` /
`ensure-build` keep `lib/runtime/engine.js` as a required artifact. check-parity declares public
surface removals in `DECLARED_ENGINE_REMOVALS`: a declared name that is still present in the current
facade fails, which is what makes the shrink auditable against the base commit.

### GUI sessions and presence

The Gateway keeps an in-memory session per attached GUI Blender instance: owner, caller agents,
scope grants, human pause, human edits, and presence. A session is best-effort state, not scene
data: it disappears on release or Gateway restart and is rebuilt from addon handshakes.

Presence rows hold each caller's display name, colour, stable id and short id, last activity,
view pose, selected `session_uid`s, activity, and held scopes. On every view/selection update the
Gateway pushes the current snapshot to the add-on over the existing internal `execute_code`
transport. `extension/dsh_blender_bridge/presence_draw.py` installs `SpaceView3D`
POST_VIEW/POST_PIXEL draw handlers that draw frusta, short-id labels, and selection boxes; it
never writes scene data, selection, the camera, or the frame, and it does not move the user's view.

A view pose is frozen when the capture succeeds, not re-read from the user's viewport on every
draw. Each pose carries the view matrix, the projection matrix, and the captured width/height, so
the add-on reconstructs the four frustum corners from the projection and reproduces the same lens,
sensor fit, shift, aspect ratio (including 1:1), and orthographic scale as the capture. A
`viewport` observation therefore remains visible after the user moves the viewport; a full-window
`full=true` capture has no independent pose and publishes a transient status-bar notice instead.
Poses without a projection fall back to the lens/aspect approximation and are labelled
`(approx)`.

Per-agent identity is stable across observations: the Gateway hashes the caller key for the
backend id and assigns an `A1`..`A8` short id per GUI session. The add-on defaults to one blue
colour, can use the Gateway's per-agent palette through a preference, can show or hide short ids,
and keeps per-agent hide toggles local to that Blender instance. Its N-panel lists connected
agents with their last activity and a per-row visibility button. The DSH Web client mirrors the
same rows in a right-sidebar tab refreshed from `GET /host/state`; the tab type also contributes
a right-sidebar guide entry, and `/blender-presence` opens the tab without submitting a prompt.
Session presence queries read the same rows from the Gateway, so a read-only query and the
viewport overlay cannot disagree.

### Persistent home and layered configuration

Everything the plugin persists lives under one resolved home: `DSH_BLENDER_HOME`, else `$DSH_HOME/dsh-blender`, else `~/.dsh/dsh-blender`. Resolution happens once per process (`runtime/home.ts`); `ensureHomeLayout()` idempotently creates `logs/`, `state/`, `jobs/`, `sessions/`, `queue/`, `cache/`, and `vision/`, then probes a real write. It runs from plugin `apply()` and from Gateway startup, never at import. An unwritable root returns a structured `home_unwritable` error instead of falling back to the package directory or the OS temp dir; doctor exposes it as `config.home.layout = {code, path, reason, fix}`, read-only ops keep working, and write-heavy subsystems (for example the render cache) skip their own directory instead of failing the whole process.

Queue state, the job ledger, job artifacts, sessions, render-cache entries, and the vision venv/models all use home paths. Legacy `dsh-blender-rt` directories are read-only migration sources: `queue.json`, `jobs/index.jsonl`, and gateway state JSON are copied only when the home target is missing, and the old directory is never deleted, so downgrading keeps working. `workDir` remains a deprecated alias for the temporary-artifact directory (GUI frame PNGs, headless scripts) and defaults to home; `DSH_BLENDER_WORKDIR` no longer moves persistent data.

Configuration is merged in layers (`runtime/config.ts`): built-in defaults, the package `dsh-blender.config.json`, the legacy `~/.dsh/dsh-blender.config.json` (compatibility only), the home config, `DSH_BLENDER_CONFIG`, the serialized DSH plugin settings (`DSH_BLENDER_PLUGIN_CONFIG`), and per-key `DSH_BLENDER_*` overrides. The plugin-settings layer contains only keys whose value differs from the plugin defaults (deleted or untouched fields are not serialized), so it cannot shadow the home config with stale defaults and `configSource()` reports `home`/`default`/`plugin` truthfully. Later layers win per key and every effective value keeps a source label; doctor reports the layer list, the per-key sources, the resolved home, and the DNN feature flag with its source and lock state. DNN fields are additionally hot-applied through `$DSH_HOME/dsh-blender/vision/runtime.json` (host-written atomically with a monotonic revision), which outranks the startup plugin snapshot; `DSH_BLENDER_DNN` stays highest and locks `features.dnn`. The Gateway reconciles that snapshot at `/doctor` and at each `vision_*` boundary; see the vision section below and [配置参考](配置参考.md).
### Render passes and the render-pass cache

`blender_run(kind="render", passes=[...])` is the only path that produces per-frame pass
artifacts and the only path the render cache touches; omitting `passes` keeps the 3.1
behaviour (one beauty output, no manifest, no cache lookup).

- Host validation (`src/tools/run/execute.ts`): `RENDER_PASSES` is the closed set
  (`beauty`/`depth`/`normal`/`object_index`/`alpha`); empty arrays, duplicates and unknown
  names are rejected before the job is posted, and `passes` is accepted only for `render`
  (a `render` job still takes no script).
- Executor (`runtime/core/queue/spawn-executor.ts`): with passes, `buildJobArgs` writes two
  fixed files into the job temp dir — `render_passes.py` (the embedded script exported as
  `RENDER_PASSES_PY` by `runtime/core/queue/render-passes-script.ts`; it lives in TypeScript
  because `runtime/py/*` is a registered Python domain with a frozen module list) and
  `render-config.json` (passes, frames, outdir, camera, resolution, samples, engine, format) —
  then launches `blender -b --factory-startup <file> --python render_passes.py -- <config>`.
  This branch forwards no caller Python and no `--python-expr`, and adds no `-s/-e/-a`.
  The script enables the view-layer passes, builds one compositor File Output per pass
  (`beauty` in the requested format; depth = Z mapped to `[0,1]` as 16-bit PNG; normal =
  `n*0.5+0.5` as 8-bit RGB in camera space; `object_index` = IndexOB divided by 65535 as
  16-bit PNG with a stable `(name,type)`-sorted `pass_index` map; alpha 8-bit), writes
  `manifest.json` last, and prints one `PASSES_DONE {json}` line. A requested pass that
  produced no file is listed in `degraded`; fatal setup problems emit `PASSES_ERROR`
  (e.g. `pass_unsupported`). Non-beauty passes are written with `view_transform="Raw"`, so
  consumers must read them as Non-Color. `object_index` is Cycles-only on every supported
  Blender: 4.2 / 4.5 / 5.x expose no Object Index output under EEVEE or Workbench (4.x keeps
  the socket name but `outputs.get()` resolves only for Cycles; 5.x rebuilds the outputs per
  engine and names it `Object Index`). That is an engine precondition, not a version
  difference — the failure is the same structured `pass_unsupported`, never a silent
  all-zero or all-one index image. The Python text is one program split across
  `render-passes-py-setup.ts` (scene preparation + compositor graph) and
  `render-passes-py-capture.ts` (per-frame capture + entry point) purely for the per-file
  line budget; `render-passes-script.ts` concatenates both. Blender 4.2/4.5 use
  `scene.node_tree` + `base_path`/`file_slots` + `CompositorNode*` math nodes, while 5.x
  needs `scene.compositing_node_group` + `directory`/`file_name`/`file_output_items` +
  `format.media_type="IMAGE"` + `ShaderNode*` math nodes; the script branches internally so
  the manifest contract stays identical (verified by `npm run test:e2e:passes` on 4.2/4.5/5.2).
- The manifest is the only decoding contract (`manifestVersion`, per-pass dtype/range/
  colorspace/encoding, depth near/far + formula, normal coordinate space, object-index map);
  receipts carry paths, sizes and digests only — never pixel arrays or base64.
- Cache (`runtime/core/queue/render-cache-key.ts` + `render-cache.ts`, wired in
  `runtime/core/queue/manager.ts`): the key is sha256 over canonical JSON of the scene
  identity (path, size, mtime), frame, camera, resolution, samples, engine, sorted pass set and
  manifest version. A hit creates a `completed` job with `result.cache.hit` and artifacts
  pointing at the cache, without admission and without spawning Blender. A miss renders into
  `home/cache/render/.staging/<jobId>/`; on success each frame directory is renamed atomically to
  `home/cache/render/<key>/` (manifest pass paths are rewritten to the new home), the index in
  `home/cache/render/index.json` is written temp-file + rename, then TTL/LRU pruning runs. Staging
  lives inside the cache tree on purpose: `home/queue/staging` is the legacy render work root (still
  used for job temp files), is never cleared by the cache, and may belong to another live Gateway.
  Failure, cancellation, an incomplete manifest or one entry over 2 GiB never writes an index
  entry: artifacts move to `home/jobs/<jobId>/` (and are copied to a caller `outdir` when one
  was given) and the receipt carries `cache_skipped`. A corrupt index rebuilds empty; a hit
  whose manifest/artifacts disappeared deletes that entry and falls back to a miss.
- Limits are read once at Gateway start from `cache.render.maxEntries|maxBytes|ttlDays`
  (defaults 8 entries / 4 GiB / 7 days; documented ranges 1–32 / 512 MiB–16 GiB / 1–30 days).
  Out-of-range values fall back to the documented default and are surfaced by doctor, never
  clamped.

### Vision worker and DNN runtime snapshot

`runtime/vision/` owns the long-lived Python worker and the DNN configuration state machine. The host exposes no new HTTP route: the owner-only `/dsh-blender` RPC multiplexes `virtual-gui`, `permission`, and `dnn` (`src/host/dnn-config.ts`). The host writes `$DSH_HOME/dsh-blender/vision/runtime.json` atomically with a monotonic revision and no secrets; the Gateway only reads it, at `/doctor` and at each `vision_*` boundary, and reconciles. Fields that need a new worker (`vision.python`, `vision.venv`, `backend`, `device`) use two-phase switching: idle applies immediately, whereas in-flight requests keep the old revision and new worker-backed calls get `dnn_config_switching` (retryable). A failed start keeps the old active configuration and reports `dnn_config_switch_failed` with a pending revision that can be cancelled or rolled back. Changing only `features.dnn` or `vision.model` does not restart the worker. Probe is a one-shot Python process with a 10 s timeout; it validates imports, execution providers, and `models.yml` without loading ONNX, writing snapshots, or using the network. Model downloads are host-side and single-concurrency, use `.part` → byte/SHA-256 check → atomic rename, and never bypass the pinned URL table.

### Vision ops and pass analysis

The eight `vision_*` ops are host-local and session-free: `runtime/vision/dispatch.ts` validates every
argument (local existing file paths only, 64 MP decode ceiling, bounded ROI/object lists), applies the D6
gate order for worker-backed calls (`dnn_disabled` → `dnn_config_switching` → `dnn_config_switch_failed`
→ custom-env/backend/device errors → `dnn_no_model`), and then either serves the request itself
(`vision_status`, `vision_help`, `vision_pass`) or forwards it to the single worker through the manager.
`runtime/core/plan-dispatch.ts` short-circuits the same eight names before any session resolution, so a
`vision_*` call never becomes `stage=session`, never starts Blender and never reaches `execute_code`.
The `/plan` route treats them as host-local read-only ops: `PLAN_HOST_LOCAL_OPS` in
`runtime/catalog/data.ts` is consumed by `runtime/http/lease.ts`, so they take no lease, need no
session, and require no `write_scope` grant.
Worker-backed Python op modules (`analyze.py`, `compare.py`, `detect.py`, `track.py`, `selftest.py`) live
under `runtime/vision/worker/` next to the protocol/models/inference modules; each exposes
`run(args, ctx)` and raises `VisionError(code, stage, message, fix, retryable, details)`, which the worker
main loop turns into the standard failure envelope and echoes `configRevision` on success.

`vision_pass` is pure host TypeScript (`runtime/vision/pass.ts`) with a bounded PNG decoder
(`runtime/vision/png.ts`): it reads 1–240 render `manifest.json` files, validates the manifest contract
(version/frame/resolution/pass dtype+range/near+far/normal space/object mapping, and pass paths confined to
the manifest directory), decodes depth to meters (`near + (v/65535)*(far-near)`), normals as camera-space
unit vectors, object_index through the manifest mapping (0 = background) and alpha coverage, and returns
bounded per-frame statistics plus cross-frame aggregation. Raw pixels, depth, normals and index arrays are
never returned. A pass missing from a manifest is `vision_pass_manifest_invalid` unless that frame listed it
in `degraded` (then the frame carries a `frame_degraded` warning and its other passes are still analysed); a
pass requested by the caller but absent from every manifest is `vision_pass_unsupported`. Object ordering is
a visible-coverage/median-depth approximation, reported as such, never a hidden-surface reconstruction.

### Animation analysis (animation family)

`runtime/py/animation/` is the sixth Python domain: a lazy `__init__.py`, the `animation.py` facade
(exports `ANIMATION_VERSION`) and the `animation_impl/` package (common, plan, metrics, report,
selftest, help, api). `runtime/py/modules.json` registers the module and `pyproject.toml` adds
`animation` to the exhaustive import-linter layer, so the domain is a sibling of the other five with
no cross-domain imports. `animation_report` samples the evaluated depsgraph frame by frame
(`Object.matrix_world`, `PoseBone.matrix`) — never `Action.fcurves` — and restores
`frame_current`/`frame_subframe` in a `finally` block; `animation_selftest` builds a temporary
keyframed object/armature and removes it in `finally`; `animation_help` exposes generic options only
(no rig-profile examples or control/mechanism/DEF role inference).

GUI routing lives in `config/routing.json` (`prefix: animation_`, `api: dsh_animation_api`,
`ensure: ensureAnimation`, `preload: animation`); `scripts/gen-routes.mjs` renders it, and
`runtime/core/kernel/api.ts` injects the facade through `ensureAnimation` after resolving
`ANIMATION_PATH` (added in `runtime/paths.ts` from `modules.json`). Session semantics are
three-state: `animation_report` and `animation_help` are listed in `PLAN_SESSION_READ_OPS`, so the
host still resolves a ready GUI session (even for `dry_run`) while the Gateway `/plan` route calls
`leaseGate(payload, { readOnly: true })`; they never take the write lease or a scope grant and are not
in `READ_ONLY_OPS` or `PLAN_READ_ONLY_OPS`. `animation_selftest` mutates the scene and stays behind
the normal write gate. Limits (240 samples, 16 objects / 16 contact bones / 16 intersection objects,
64 bones / 64 contact intervals / 64 intersection pairs, 240 reference frames, 200k triangles per
frame) and the metric definitions are documented in [docs/animation-analysis.md](animation-analysis.md).

## 4. Python domain library (runtime/py/)

Python code is grouped by domain, not by a flat file list:

- core: audit, contract, gate, motion, planner, txn, presets, runner, pipeline;
- geometry: geo, blockout, sculpt, mesh fix, surface, shapesolve, generator, UV tools;
- assembly: assembly, vehicle, human, clearance, mate;
- render: qc, qc_render, imgtools, material, visualreport, montage, calib;
- export: deliver, printcheck, open3d check;
- animation: animation_report, animation_selftest, animation_help (evaluated-depsgraph sampling,
  root motion, joints, contacts, intersections, reference trajectories).

runtime/py/modules.json is the only module registry. It maps public module names to domain and
implementation paths. runtime/paths.ts, offline_bootstrap.py, and the layout tests read that registry;
there is no second hand-written table and no flat runtime/<name>.py fallback.

Public facade modules keep the stable name at runtime/py/<domain>/<name>.py. They are small: they use
the shared runtime/py/_facade.py helper and re-export the implementation package. The implementation
lives in the sibling <name>_impl/ package. The public path, preload name, and K.dsh_<name>_api entry are
the contract; the facade is the stable entry, not a temporary shim.

The six domains are independent siblings, and the layers contract is exhaustive: a module under
`runtime/py` that is not listed there fails `npm run check:python-imports` until its domain is
declared. `_facade` is the only declared exception.

## 5. Blender addon (extension/dsh_blender_bridge/)

The addon is a package, not a single file. Its modules cover protocol, command registration, selection,
recorders, assets, integrations, and UI. The release artifact is a deterministic zip built from that
package.

The addon version is 2.0.0 and protocol is 12; it installs only as a Blender extension. A
register/unregister round trip is part of the tests.
`extension/dsh_blender_bridge/command_registry.py` is the only command truth: 25 registered commands
plus the transport-only `ping` (26 wire commands). `get_addon_info` derives its handshake
capabilities from `command_registry.COMMAND_NAMES`, and the host catalog
(`runtime/catalog/commands.ts`) is parity-checked in both directions against it.
The launcher and plugin refuse an incompatible protocol and name the expected addon version. Existing
users migrate by removing the old single-file addon and installing the release zip; the backend and
protocol do not change during that migration.

The addon has two enforced internal boundaries: `assets.io` may not import `commands`, and
`ui.panels` may not import `integrations`. Both are import-linter contracts in `pyproject.toml`.

Credentials have one owner: the add-on preferences. `config.get_credential` reads a process-local
session override (the Hyper3D free-trial button), then the preferences, then the `DSHBB_*` environment
variable with the legacy `BLENDERMCP_*` name as fallback. There is no Scene-property mirror: a saved
.blend cannot silently override the stored key, and the panels point at preferences instead of
rendering a second editor.

## 6. Build, package, and release

- npm run build regenerates the route tables from config/routing.json, then compiles runtime, host,
  and client.
- lib/ is generated and is not committed.
- prepack builds lib/; CI's check:build verifies the artifact.
- npm pack produces the published tarball: lib/, runtime Python, addon package, skills, docs, and tests.
- The extension zip is built from extension/dsh_blender_bridge/ and attached to the release.
- npm run smoke:install installs the tarball into a clean directory and checks that the entry loads,
  the Gateway starts from lib/runtime/gateway.js, and doctor returns kind=ok.
- Release is a separate, human-owned gate: the foundation can be complete before release is approved.

The package must never ship backend TypeScript sources, a committed lib/, a legacy single-file addon,
or a venv/node_modules tree.

## 7. Test layers

Three layers, with a different job each:

- Unit: Vitest and pytest, no Blender, no network, no real display. Host uses fake DSH/addon; Python
  uses fake bpy stubs for type checking and pure logic only.
- Integration: real backend HTTP and in-memory or fake addon; route, lease, job, and contract behavior.
- End-to-end: real Blender 4.2 LTS headless and GUI. The native runner loads modules inside Blender,
  resets the scene per test, writes JSON, and the host pytest launcher asserts the report.

Python static checks are part of the same quality surface: ruff over every tracked Python root
(`runtime/py`, `tests`, `extension/dsh_blender_bridge`, `scripts`, `docs/examples`), plus a narrow
pyright scope over pure-logic runtime modules. The pyright scope is explicit; it is not a claim that
all runtime Python is typed. There is no checked-in ruff baseline: every violation fails the gate.

## 8. Quality gates and invariants

These are the rules that protect the architecture:

- 6 tools / 7 capabilities / 36 families / 239 ops / 25 addon commands / 29 routes / protocol 12 stay stable unless a
  declared migration changes them. The 25 addon commands are the registered dispatch surface; the
  transport-only ping is the 26th wire command.
- src/index.ts stays entry-only; production files stay under the line budgets enforced by
  check:structure.
- The public surface is two registered facades (`src/index.ts`, `runtime/engine.ts`) whose exports and
  consumers are inventoried in `config/facades.json`. `npm run check:no-shims` fails on an
  unregistered facade export, a stale registration, a wildcard export, or a retired file that
  reappears; other multi-statement re-export barrels need a `kind=single-line-reexport` exception.
- Functions target <=80 lines and never exceed 120. The 80-120 soft band is a registered debt
  ledger, not a warning: every band function has an entry in config/function-budget-allowlist.json
  with a classification, a real reason, the class owner, and a future reviewBy. npm run
  check:function-budget fails on an unregistered, unclassified, expired, or stale entry, and cannot
  waive a function over 120 lines; the classes are dispatch-table, linear-pipeline, generated-catalog,
  orchestration, formatting, validation, parsing, and integration-io (see [Exceptions](#exceptions)).
- runtime/py implementation files stay <=1,000 lines; TS and addon files stay <=600 lines. Path-level
  size exceptions live in config/exceptions.json (kind=file-budget, with reason/owner/reviewBy) and are
  checked by npm run check:exceptions; the plan catalog data table runtime/catalog/data.ts is the
  single registered one.
- Every Python module is in modules.json; there are no flat shims and no second registry.
- No explicit any in foundation host/runtime files; type debt is a ledger, not a whole-file exemption.
- Coverage floors are enforced by check:coverage; E2E scenario coverage is separate from line coverage.
- Every behavioral change needs a failing test first and evidence in the pull request.

### Static scope and import boundaries

Python scope and import boundaries are enforced at three points:

- `npm run check:python-static` runs ruff over every tracked Python root (`runtime/py`, `tests`,
  `extension/dsh_blender_bridge`, `scripts`, `docs/examples`), asserts that every tracked `.py`
  lives under one of those roots, and asserts that every include in `pyrightconfig.python.json` and
  `pyrightconfig.extension.json` exists, is a file, and is unique. There is no ruff baseline and no
  per-file waiver.
- `npm run check:python-imports` runs import-linter with the contracts in `pyproject.toml`. The six
  runtime domains (`core`, `geometry`, `assembly`, `render`, `export`, `animation`) are independent siblings,
  and the layers contract is exhaustive: a module under `runtime/py` that is not listed fails the
  contract. `_facade` is the only declared exception because it resolves targets dynamically. The two
  addon contracts forbid `assets.io` from importing `commands` and `ui.panels` from importing
  `integrations`; the latter checks direct imports only to match the current import chain.
- Adding a Python module means placing it under an existing root and domain. Adding a domain means
  registering it in `runtime/py/modules.json` and in the layers contract; the exhaustive check fails
  until both are updated. Do not add a per-file waiver: fix the import instead.

TypeScript boundaries use `eslint-plugin-boundaries` in `eslint.config.mjs`:

- src subdirectories and the runtime subdirectories are elements; root-level source files are file
  categories (`entry`, `constants`, `plugin` for src; `runtime-root` for runtime). The
  `boundaries/dependencies` matrix is default-deny, and `boundaries/no-unknown-files` rejects a file
  that belongs to no registered element or category. `eslint-import-resolver-typescript` resolves
  `.js` specifiers to `.ts` before classification, so the matrix sees real targets.
- `src/client/**/*.js` has its own lint block (browser globals, CommonJS, client element). It is not
  part of the TypeScript project.
- `src` may not import `lib/runtime` internals except through `src/host/runtime-bridge.ts`, enforced by
  `no-restricted-imports` with a one-file override.
- Adding a TypeScript directory or root file means registering it; adding a cross-element import means
  updating the matrix. There are no per-file waivers; the previous ad-hoc boundary checker is retired.

### Comment conventions

New and changed file and module headers describe what the file is responsible for now, not the
refactor step that created it. New and changed source comments never cite T*/F*/B*/C* step IDs or
"split from"/"behavior frozen" narratives; when the split history is genuinely useful it is at most
one `History: split from <file> (<version>)` line. Pre-existing headers outside the files a change
touches are cleaned opportunistically when those files are next edited; a one-off baseline sweep is
allowed only when a change declares it explicitly (the terminal-state closeout did, in a
comment-only diff).
Behavior history belongs in [tests/BEHAVIOR_MATRIX.md](../tests/BEHAVIOR_MATRIX.md) and
[CHANGELOG.md](../CHANGELOG.md), not in the implementation comments. Plan IDs are still allowed
where they identify an accepted item in the plan-ID baseline, but they are identifiers, not file
history.

### Contract and routing sources

The op surface has two derived data sources; neither is hand-maintained inside the dispatch code:

- `config/routing.json` is the single GUI routing table (op prefix or exact alias, strip length,
  python api, preload, timeout, ensure hook). `scripts/gen-routes.mjs` renders
  `runtime/core/dispatcher/routes.generated.ts`, which starts with an AUTO-GENERATED header plus the
  source sha256. The host-side worker table was removed with the 3.1 worker plan channel: headless
  work belongs to `blender_run(kind="headless")`. `npm run build` regenerates the table and
  `npm run check:routes` fails when a generated file is missing, stale, or hand-edited. Generated
  tables are excluded from the size scanners and validated by `check:routes`.
- `config/contracts.json` is the catalog <-> Python link layer: family -> python api -> declared
  extraction of the literal dispatch table. `scripts/catalog_dispatch_keys.py` parses those tables
  with the Python ast module (no import, no bpy), and
  `tests/unit/js/contract/catalog-dispatch-parity.test.ts` fails when a catalog op has no python
  dispatch key. Host-side families (`host`), host-side ops inside a Python family (`hostOps`, each
  with a reason, for example `shape_search` before the dispatcher), aliases, and python-only keys
  (`pythonExtras`) are declared explicitly; the retired `catalogExtras` field fails the parity test,
  so an advertised-but-undispatchable op cannot pass as known drift.

### Exceptions

Exceptions are owned at two levels; an unowned, expired, or stale entry fails a gate.

`config/exceptions.json` is the path-level registry behind every known file exception in this guide.
Each entry has `id`, `kind`, `path`, `reason`, `owner`, and `reviewBy`; `npm run
check:exceptions` (part of `npm run lint`) rejects an unknown kind, a missing path, a duplicate id,
a reason shorter than 16 characters, and an expired `reviewBy`. Kinds: `file-budget`,
`function-budget`, `python-static`, `single-line-reexport`, `facade-export`, `doc-path`,
`matrix-row`, `python-private-import`. Twelve entries exist today: the `file-budget` one for
`runtime/catalog/data.ts`; `single-line-reexport` for `runtime/catalog/index.ts`,
`src/host/runtime-bridge.ts`, and `src/headless/receipts/index.ts` (the documented src-to-lib
bridge and two barrels); and eight `python-private-import` entries for the addon command modules
that reuse private `_polyhaven_*` / `_polypizza_*` helpers from `integrations/*_client.py`.
`tests/unit/python/test_private_import_contract.py` scans the addon with `ast` for every
`_`-prefixed name imported from `integrations/*_client.py`, derives the expected count from
`config/exceptions.json`, and fails on an unregistered pair or a stale entry, so the registry and
the source tree cannot drift apart.
`facade-export` is reserved for the facade registry; `python-static` exists for future owned
static-check waivers but currently has no entries because the static gate has no checked-in baseline.
No `function-budget` entries are used: function-level debt is registered at the second level instead.

`config/doc-links-baseline.json` is the document-side exception list: it freezes seven historical
missing path refs (v2.3 host-contract and MIGRATIONS targets). While it accepts missing refs it must
carry `review: {owner, reviewBy, reason}`; `npm run check:doc-paths -- --enforce` fails on a missing,
empty, or expired review, so the frozen links stay owned instead of becoming a silent allowlist.

`config/function-budget-allowlist.json` is the function-level registry for the 80-120 line soft band.
Every band function has one entry keyed `file::name` with a `classification`, `reason`, class
`owner`, and future `reviewBy` (current ledger: 2027-06-30). Classes and their owners:
`dispatch-table` (runtime), `linear-pipeline` (runtime), `generated-catalog` (catalog),
`orchestration` (runtime), `formatting` (host), `validation` (qa), `parsing` (host),
`integration-io` (addon). `npm run check:function-budget` (also run by `npm run test:unit` and
`npm run test:contract`) fails when a band function is unregistered, its classification is unknown,
its owner differs from the class owner, its `reviewBy` has expired, or its entry went stale
(registered but no longer in the band). Functions over 120 lines always fail and cannot be waived.
`node scripts/check-function-budget.mjs --refresh` rewrites the ledger for the current tree, preserving
classified entries and adding new ones as `unclassified`; unclassified entries fail until a real role
and reason are written, so a refresh cannot silently re-bless debt. Design D11 chose registration over
splitting: the band is catalogued by class and no function was split, because none had an
independently cohesive part worth the churn.

### Addon serial queue and startup probes

The addon bridge serializes requests: `AddonClient` sends one request at a time and the next one waits
behind it, so an unawaited background request at the queue head delays every foreground caller by up
to its full timeout. Two places used to pay that tax: the host startup probe (an unawaited
`get_addon_info` with a 3s timeout issued right after connect) and a failed-call diagnose in
`lifecycle.installAddonSend` (about 2.5s, reused by /doctor instead of diagnosing twice).

Current rules: startup only awaits the socket connect (`addon.ensure()`); no informational request is
queued before the first real call. A failed request may attach one diagnosis, and /doctor reuses the
attached diagnosis. The budgets are therefore honest: `ADDON_STATUS_TIMEOUT_MS = 5000` for /status
and `ADDON_DOCTOR_STATUS_TIMEOUT_MS = 4000` for /doctor in `runtime/core/engine-api/doctor.ts`, with
the 12s viewport start budget in `src/constants.ts`. `tests/integration/js/doctor-bounded.test.ts`
pins the behavior: against a half-open addon both endpoints answer with addon attribution, and the
cold call is less than 2s slower than the warm call (measured 2026-11-05: cold/warm /status
7531/7528ms and /doctor 6540/6521ms; before the fix the cold call paid about 3s more:
8983/6026ms and 8495/5526ms). A new background request at the queue head must be avoided or deduped
with diagnose.

### Coverage policy

Coverage is measured with Vitest's single-file mode plus pytest branch coverage, and the gate reads
only `config/coverage-thresholds.json`. The floors were re-baselined on 2026-11-01 when
`@vitest/coverage-v8` moved from 3 to 5: the provider switched to AST-aware remapping, which changed
the measurement scale on the same tree (branch points 3934 -> 6567, executable lines 17819 -> 6294),
so the previous 73.2/68.7/70.4 floors were no longer comparable. Canonical CI run
[41315467806](https://github.com/sixtysevenlf/dsh-blender-plugin/actions/runs/41315467806) at commit
`8299acf` measured JS 71.91% lines / 55.99% branches / 69.5% functions and 67.698 changed lines
against v2.6.2 (7724 changed / 2495 missing). Applying the unchanged rule (round down to 0.1pp,
subtract 1.5pp) gives the current floors: lines 70.4, branches 54.4, functions 68, changed lines 66.1.
This is a documented policy change required by the measurement-tool upgrade; no tests or modules were
removed, and the previous "never below the prior floor" rule does not apply across a provider change.
Critical contract modules stay >=90/75; execution-module floors are unchanged except
`runtime/execution/headless/process.ts` branches 60 -> 55.6 (re-measured 57.14). Python pure logic is
82.5/74.36 with floors 80/70. Future ratchets use the next canonical CI run and the manual +2/PR
policy; a floor must not be lowered without a documented policy change in this guide.

## 9. Key choices and consequences

| Choice | Consequence |
|---|---|
| Strict TypeScript runtime compiled to lib/ | Type errors fail before tests; package ships JS, not backend TS; build is required before backend/E2E |
| Vitest + pytest + real-Blender harness | Fast inner loop; E2E stays on-demand/release because it needs Blender |
| Addon is a package plus a deterministic zip | Reviewable modules and stable install artifact; users must remove the old single-file addon |
| lib/ is generated, not committed | Review diffs stay clean; check:build and install smoke must verify the artifact |
| Python domains plus one modules.json registry | One place to add a module; no flat shims or duplicate path maps |
| Two registered public facades plus implementation packages | Stable host and runtime entry imports; every export needs a registered consumer, and an unregistered export fails check:no-shims |
| File, function, and coverage budgets | Reviewable size and measured debt; exceptions need owner, reason, and expiry |
| One routing table plus codegen; one catalog <-> Python link table | GUI and worker routes cannot drift from config/routing.json; check:routes rejects stale generated tables, and the parity test rejects undeclared dispatch drift |

## 10. How to change safely

1. Read AGENTS.md for commands and hard rules.
2. For a boundary or contract change, update the code, the catalog/lock, the contract tests, and the
   migration notes together. Routing changes start in config/routing.json and regenerate through
   node scripts/gen-routes.mjs; catalog op changes must also update config/contracts.json.
3. Add or update a failing test before the behavior change. Keep unit tests free of Blender, display,
   network, and user profile dependencies.
4. Run the layer gates for what changed, then the full test and static gates. Run real-Blender E2E for
   addon, protocol, native runner, or bpy behavior changes.
5. If a size, coverage, or type exception is unavoidable, add an owned, expiring entry rather than a
   blanket ignore.
6. Keep release steps out of the foundation plan; release has its own checklist and human sign-off.

## 11. Narrative history

The refactor plan, the working spec, and the debt ledger are historical evidence and live outside git;
this repository documents behavior and the terminal state only. Plan-shaped IDs (D1, F2.3, S3, ...) are
no longer a maintained narrative: config/plan-ids.json carries the remaining exemptions, and
scripts/check-plan-ids.mjs is the ratchet that keeps new ones out.

Two historical analyses are kept for traceability, not as current guidance:
[Procedura fusion analysis](Procedura-融合分析.md) (2026-09-17) and
[flash capability analysis](flash建模上限-整合方案.md). The OpenSpec change archive is the story going
forward.
