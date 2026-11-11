# scripts/ — developer tooling

This directory is the project's **engineering tool layer**. It is not product code and is not included in the
npm package. npm scripts in package.json call these files; tests live under tests/.

## Rules for new scripts

1. Name checkers check-<topic>.mjs, test runners test*.mjs, build steps build-*.mjs.
2. Exit 0 only on success; exit non-zero on failure. Write failures to stderr.
3. Do not write product files; temporary output goes under coverage/ or an OS temp directory.
4. Register the script in package.json or in an existing composed check. A script with no caller is dead.
5. Add one row to the table below with its caller and side effects.
6. Bootstrap scripts (build.mjs, typecheck.mjs, test.mjs, ensure-build.mjs) stay JavaScript. Do not make
   them TypeScript or import a compiled artifact before the build exists.

## Script inventory

### Build and packaging

| Script           | Purpose                                                                                      | Main caller                                | Writes                                                                    |
| ---------------- | -------------------------------------------------------------------------------------------- | ------------------------------------------ | ------------------------------------------------------------------------- |
| build.mjs        | Compile runtime, host, or all targets through tsc; optional client build                     | npm run build / build:host / build:runtime | lib/                                                                      |
| build-extension.mjs  | Build the extension zip; --check fails a stale committed zip after a byte comparison with the current sources | npm run build:extension / check:extension          | extension zip (build only)                                                    |
| build-client.mjs | Build the hand-written client half into lib/client.js; --check verifies freshness            | build.mjs, check-build.mjs                 | lib/client.js                                                             |
| build.sh         | Deprecated convenience wrapper for optional DSH_CHECKOUT linking; canonical is npm run build | manual only                                | node_modules links, lib/ via build.mjs                                    |
| ensure-build.mjs | Ensure lib/ exists and is fresh before tests/checks                                          | imported by checkers and test helpers      | lib/                                                                      |
| gen-routes.mjs   | Render the worker/GUI route tables from config/routing.json; header carries the source hash  | npm run build                              | src/plan/routes.generated.ts, runtime/core/dispatcher/routes.generated.ts |
| publish-extension.mjs | Upload one built extension version to extensions.blender.org (multipart version_file + release_notes); dry-run and token-missing fail closed | npm run publish:extension / Manual CD | network call only on a real publish |
| build-win-helper.mjs | Build and self-test the Windows hidden-desktop helper with MSVC; Windows only | npm run build:win-helper / Windows CI | tools/win-hidden-desktop/out/ |
| check-win-helper.mjs | Verify the staged helper's version, source commit, source/protocol, and sha256 against HEAD, then optionally that npm pack includes it | npm run check:win-helper / Windows CI / Manual CD | none (reads vendor/win-hidden-desktop) |

### Test entry points

| Script                    | Purpose                                                                          | Main caller               | Writes                |
| ------------------------- | -------------------------------------------------------------------------------- | ------------------------- | --------------------- |
| test.mjs                  | One entry for unit, integration, contract, python, extension-unit and e2e modes      | npm test / test:*         | none (test temp dirs) |
| install-smoke.mjs         | Pack, install into a clean temp dir, load entry and check doctor/health          | npm run smoke:install     | temp install tree     |
| python_function_budget.py | Emit Python function line counts as JSON; invoked by the function-budget checker | check-function-budget.mjs | stdout only           |

### Quality gates

| Script                      | Purpose                                                                      | Main caller                                  | Writes                                   |
| --------------------------- | ---------------------------------------------------------------------------- | -------------------------------------------- | ---------------------------------------- |
| check-any.mjs               | Explicit-any AST gate                                                        | npm run lint / check:any                     | stdout only                              |
| check-behavior-matrix.mjs   | Validate the behavior coverage matrix against live catalogs (tools, routes, addon commands, plan ops) | npm run test:matrix | coverage/matrix artifacts when requested |
| check-build.mjs             | Verify lib/ is current and entry/client are synchronized                     | npm run check:build                          | stdout only                              |
| check-coverage.mjs          | Single coverage gate: JS global/changed, critical/execution, Python, E2E     | npm run check:coverage                       | stdout only                              |
| check-doc-paths.mjs         | Check doc links, commands, inbound links and nonexistent path refs; --enforce ratchets against config/doc-links-baseline.json (orphan list intentionally empty) | npm run check:doc-paths / check:finalization | stdout only |
| check-exceptions.mjs        | Validate config/exceptions.json: kind enum, path exists, owned and unexpired | npm run lint / check:exceptions              | stdout only                              |
| check-plan-ids.mjs          | Inventory plan-ID-shaped tokens; warn by default, --enforce ratchets against config/plan-id-baseline.json | npm run check:plan-ids | stdout only |
| check-ports.mjs             | Reject the legacy 9877 Gateway default in current docs/config; tests, archives and migration history are exempt | npm run lint | stdout only |
| check-routes.mjs            | Assert the generated route tables match config/routing.json byte for byte    | npm run check:routes / check:finalization    | stdout only                              |
| check-function-budget.mjs   | File and function line budgets; 80-120 line functions register in config/function-budget-allowlist.json | check:function-budget / check:finalization | stdout only |
| check-git-hygiene.mjs       | Ensure lib/ is untracked; requires a git checkout                            | npm run check:git-hygiene / CI               | stdout only                              |
| check-import-boundaries.mjs | Layer boundaries for host, runtime and addon                                 | check:finalization                           | stdout only                              |
| check-no-shims.mjs          | No flat compatibility shims or retired dual-entry files; config/facades.json lists registered public facades | check:finalization | stdout only |
| check-parity.mjs            | Contract/catalog/package parity snapshot                                     | npm run test:parity                          | stdout only                              |
| check-python-static.mjs     | Ruff baseline plus pure-logic pyright                                        | npm run check:python-static                  | stdout only                              |
| check-runtime-layout.mjs    | modules.json is the only Python module map; every module resolves            | check:finalization                           | stdout only                              |
| check-structure.mjs         | Entry/file map, module budgets and import cycles                             | npm run check:structure / check:finalization | stdout only                              |
| engine-probe.sh             | Manual engine/HTTP probe; not part of the pytest or npm gate                 | manual                                       | none                                     |

### Typecheck and development utilities

| Script          | Purpose                                                                                       | Main caller                     | Writes                                |
| --------------- | --------------------------------------------------------------------------------------------- | ------------------------------- | ------------------------------------- |
| setup-dev.mjs   | One-command dev environment: Python discovery, .venv, requirements, npm deps, --check/--force | npm run setup:dev               | .venv/ and node_modules/ when missing |
| typecheck.mjs   | Reproducible tsc lookup and typecheck entry                                                   | npm run typecheck / typecheck:* | lib/runtime when build is requested   |
| sync_counts.mjs | Refresh catalog counts in display text; --dry reports drift                                   | manual plus single-source test  | docs/AGENTS text (normal mode)        |

## Checked scripts and type coverage

The following scripts are included in scripts/tsconfig.json and run through npm run typecheck:scripts:

- check-behavior-matrix.mjs
- check-coverage.mjs
- check-structure.mjs

They use // @ts-check and JSDoc at dynamic boundaries. The config starts with strict disabled so the
existing JavaScript can be checked without a mass rewrite; the next tightening step is to enable
strictNullChecks and noImplicitAny file by file. Do not add // @ts-nocheck to make this pass.

Build/test bootstrap scripts intentionally stay out of this config.

## Removed

The following were deleted in the scripts cleanup because they had no callers and no current docs:

- kit_converge.py
- kit_migrate.py
- kit_uniform_api.py
- pyrun.mjs
