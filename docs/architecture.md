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

The public model surface is 15 tools, 34 planning families, 228 operations, 38 addon commands,
29 HTTP routes, and protocol 11. These counts are facts, not goals; changing one requires updating
the catalog, lock, tests, and migration notes in the same change.

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
The launcher and plugin refuse an incompatible protocol and name the expected addon version. Existing
users migrate by removing the old single-file addon and installing the release zip; the backend and
protocol do not change during that migration.

## 6. Build, package, and release

- npm run build compiles runtime, host, and client.
- lib/ is generated and is not committed.
- prepack builds lib/ and checks the artifact.
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
claim that all runtime Python is typed. The ruff baseline is an explicit debt ledger with reason,
owner, and expiry, not a permanent blanket ignore.

## 8. Quality gates and invariants

These are the rules that protect the architecture:

- 15 tools / 34 families / 228 ops / 38 addon commands / 29 routes / protocol 11 stay stable unless a
  declared migration changes them.
- src/index.ts stays entry-only; production files stay under the line budgets enforced by
  check:structure.
- Functions target <=80 lines and may not exceed 120 without a recorded exception; the function budget
  has no stale entries.
- runtime/py implementation files stay <=1,000 lines; TS and addon files stay <=600 lines. The only
  data-table exception is runtime/catalog.ts.
- Every Python module is in modules.json; there are no flat shims and no second registry.
- No explicit any in foundation host/runtime files; type debt is a ledger, not a whole-file exemption.
- Coverage floors are enforced by check:coverage; E2E scenario coverage is separate from line coverage.
- Every behavioral change needs a failing test first and evidence in the pull request.

## 9. Key choices and consequences

| Choice | Consequence |
|---|---|
| Strict TypeScript runtime compiled to lib/ | Type errors fail before tests; package ships JS, not backend TS; build is required before backend/E2E |
| Vitest + pytest + real-Blender harness | Fast inner loop; E2E stays on-demand/release because it needs Blender |
| Addon is a package plus a deterministic zip | Reviewable modules and stable install artifact; users must remove the old single-file addon |
| lib/ is generated, not committed | Review diffs stay clean; prepack and install smoke must verify the tarball |
| Python domains plus one modules.json registry | One place to add a module; no flat shims or duplicate path maps |
| Small public facades plus implementation packages | Stable public imports and preload names; facades must stay boring |
| File, function, and coverage budgets | Reviewable size and measured debt; exceptions need owner, reason, and expiry |

## 10. How to change safely

1. Read AGENTS.md for commands and hard rules.
2. For a boundary or contract change, update the code, the catalog/lock, the contract tests, and the
   migration notes together.
3. Add or update a failing test before the behavior change. Keep unit tests free of Blender, display,
   network, and user profile dependencies.
4. Run the layer gates for what changed, then the full test and static gates. Run real-Blender E2E for
   addon, protocol, native runner, or bpy behavior changes.
5. If a size, coverage, or type exception is unavoidable, add an owned, expiring entry rather than a
   blanket ignore.
6. Keep release steps out of the foundation plan; release has its own checklist and human sign-off.
