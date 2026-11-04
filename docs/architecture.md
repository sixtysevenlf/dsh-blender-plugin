# Architecture

This is the single architecture guide for the DSH x Blender plugin. It explains the current system
shape, the boundaries that must stay stable, and the constraints that determine how changes are made.
Git history and the local refactor workspace keep older rationale outside the published repository.

## 1. System shape

The plugin is a DeepSeek Harness host plugin, not a standalone Blender add-on.

    DSH host
      -> src/ tools
      -> backend HTTP (127.0.0.1:9877)
      -> engine dispatch
      -> Blender addon (127.0.0.1:9876)
      -> bpy

There are two processes and one TCP bridge:

- the DSH host and backend run in Node;
- the Blender addon runs inside Blender;
- the addon is the only process that calls bpy;
- the backend owns jobs, leases, sessions, receipts, and routing;
- the host owns the model-facing tools and the headless/fallback policy.

The public model surface is 15 tools, 34 planning families, 228 operations, 38 addon commands
(38 registered commands; the transport-only ping makes 39 wire commands), 29 HTTP routes, and
protocol 11. These counts are facts, not goals; changing one requires updating the catalog, lock,
tests, and migration notes in the same change.

## 2. Host plugin (src/)

src/index.ts is entry-only. It exports the plugin API and wires the host modules; it must not grow
feature logic. The rest of the tree is split by responsibility:

- src/tools/: one tool per directory with registration, execution, and presenter/receipt pieces;
- src/plan/: plan state, render guard, external checks, shape search;
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
- runtime/http/: server, routes, lease, host views;
- runtime/transport/: frames, sockets, protocol helpers;
- runtime/contracts/: generated or checked contract tables;
- runtime/py/: the Blender-side Python library described below.

The runtime build precedes the host build because host modules import boundary helpers from the built
runtime. The backend entry used by SERVER_PATH is lib/runtime/server.js. Source maps are emitted so a
crash still points at a TypeScript line.

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

## 5. Blender addon (addon/dsh_blender_bridge/)

The addon is a package, not a single file. Its modules cover protocol, command registration, selection,
recorders, assets, integrations, and UI. The release artifact is a deterministic zip built from that
package.

The addon version is 1.11 and protocol is 11. A register/unregister round trip is part of the tests.
`addon/dsh_blender_bridge/command_registry.py` is the only command truth: 38 registered commands
plus the transport-only `ping` (39 wire commands). `get_addon_info` derives its handshake
capabilities from `command_registry.COMMAND_NAMES`, and the host catalog
(`runtime/catalog/commands.ts`) is parity-checked in both directions against it.
The launcher and plugin refuse an incompatible protocol and name the expected addon version. Existing
users migrate by removing the old single-file addon and installing the release zip; the backend and
protocol do not change during that migration.

## 6. Build, package, and release

- npm run build regenerates the route tables from config/routing.json, then compiles runtime, host,
  and client.
- lib/ is generated and is not committed.
- prepack builds lib/; CI's check:build verifies the artifact.
- npm pack produces the published tarball: lib/, runtime Python, addon package, skills, docs, and tests.
- The addon zip is built from addon/dsh_blender_bridge/ and attached to the release.
- npm run smoke:install installs the tarball into a clean directory and checks that the entry loads,
  the backend starts from lib/runtime/server.js, and doctor returns kind=ok.
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

- 15 tools / 34 families / 228 ops / 38 addon commands / 29 routes / protocol 11 stay stable unless a
  declared migration changes them. The 38 addon commands are the registered dispatch surface; the
  transport-only ping is the 39th wire command.
- src/index.ts stays entry-only; production files stay under the line budgets enforced by
  check:structure.
- Functions target <=80 lines and may not exceed 120 without a recorded exception; the function budget
  has no stale entries.
- runtime/py implementation files stay <=1,000 lines; TS and addon files stay <=600 lines. Size
  exceptions live in config/exceptions.json (kind=file-budget, with reason/owner/reviewBy) and are
  checked by npm run check:exceptions; the plan catalog data table runtime/catalog/data.ts is the
  single registered one.
- Every Python module is in modules.json; there are no flat shims and no second registry.
- No explicit any in foundation host/runtime files; type debt is a ledger, not a whole-file exemption.
- Coverage floors are enforced by check:coverage; E2E scenario coverage is separate from line coverage.
- Every behavioral change needs a failing test first and evidence in the pull request.

### Contract and routing sources

The op surface has two derived data sources; neither is hand-maintained inside the dispatch code:

- `config/routing.json` is the single routing table (op prefix or exact alias, strip length, python
  api, GUI vs worker side, preload, timeout, ensure hook). `scripts/gen-routes.mjs` renders
  `src/plan/routes.generated.ts` (headless worker) and
  `runtime/core/dispatcher/routes.generated.ts` (GUI). Both files start with an AUTO-GENERATED header
  plus the source sha256. `npm run build` regenerates them and `npm run check:routes` fails when a
  generated file is missing, stale, or hand-edited. Generated tables are excluded from the size
  scanners and validated by `check:routes`.
- `config/contracts.json` is the catalog <-> Python link layer: family -> python api -> declared
  extraction of the literal dispatch table. `scripts/catalog_dispatch_keys.py` parses those tables
  with the Python ast module (no import, no bpy), and
  `tests/unit/js/contract/catalog-dispatch-parity.test.ts` fails when a catalog op has no python
  dispatch key. Host-side families, aliases, and python-only keys are declared explicitly in that
  file, so undeclared drift fails instead of being skipped.

### Exceptions

`config/exceptions.json` is the machine-readable registry behind every known exception in this
guide. Each entry has `id`, `kind`, `path`, `reason`, `owner`, and `reviewBy`; `npm run
check:exceptions` (part of `npm run lint`) rejects an unknown kind, a missing path, a duplicate id,
a reason shorter than 16 characters, and an expired `reviewBy`. Kinds: `file-budget`,
`function-budget`, `python-static`, `single-line-reexport`, `facade-export`, `doc-path`,
`matrix-row`. Only `file-budget` (`runtime/catalog/data.ts`) has an entry today; `python-static`
exists for future owned static-check waivers but currently has no entries because the static gate has
no checked-in baseline.

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
| Small public facades plus implementation packages | Stable public imports and preload names; facades must stay boring |
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
