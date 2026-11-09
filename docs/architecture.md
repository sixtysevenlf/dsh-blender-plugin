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
blender_run, blender_inspect), 34 planning families, 228 operations, 25 addon commands
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

Host code uses strict TypeScript. Any value from the backend, addon, model, or user file system is
parsed at one boundary before it enters typed logic. Explicit any is not accepted in foundation files;
the remaining type-debt ledger must have an owner, reason, and expiry.

## 3. Backend runtime (runtime/, built to lib/)

The backend is strict TypeScript under runtime/. It is compiled with tsconfig.runtime.json into
lib/runtime/**/*.js. Published packages contain built JavaScript, not backend TypeScript sources.

Layout:

- runtime/core/: dispatch, kernel, scripts, catalog, engine;
- runtime/execution/: headless, jobs, workers, recovery;
- runtime/http/: routes, lease, host views;
- runtime/gateway/: the Gateway process, addon WebSocket channel, ownership, and lifecycle;
- runtime/transport/: frames, sockets, protocol helpers;
- contract tables: `config/contracts.json` is the catalog <-> Python link layer (described in section 8); there is no separate runtime contract-table directory;
- runtime/py/: the Blender-side Python library described below.

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

## 4. Python domain library (runtime/py/)

Python code is grouped by domain, not by a flat file list:

- core: audit, contract, gate, motion, planner, txn, presets, runner, pipeline;
- geometry: geo, blockout, sculpt, mesh fix, surface, shapesolve, generator, UV tools;
- assembly: assembly, vehicle, human, clearance, mate;
- render: qc, qc_render, imgtools, material, visualreport, montage, calib;
- export: deliver, printcheck, open3d check.

runtime/py/modules.json is the only module registry. It maps public module names to domain and
implementation paths. runtime/paths.ts, offline_bootstrap.py, and the layout tests read that registry;
there is no second hand-written table and no flat runtime/<name>.py fallback.

Public facade modules keep the stable name at runtime/py/<domain>/<name>.py. They are small: they use
the shared runtime/py/_facade.py helper and re-export the implementation package. The implementation
lives in the sibling <name>_impl/ package. The public path, preload name, and K.dsh_<name>_api entry are
the contract; the facade is the stable entry, not a temporary shim.

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

Python static checks are part of the same quality surface: ruff over runtime/py, tests, and the addon,
plus a narrow pyright scope over pure-logic runtime modules. The pyright scope is explicit; it is not a
claim that all runtime Python is typed. There is no checked-in ruff baseline: every violation fails the
gate.

## 8. Quality gates and invariants

These are the rules that protect the architecture:

- 6 tools / 7 capabilities / 34 families / 228 ops / 25 addon commands / 29 routes / protocol 12 stay stable unless a
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
