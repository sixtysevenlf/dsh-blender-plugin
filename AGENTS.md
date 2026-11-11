# AGENTS.md — repository guide for AI assistants

> Loaded automatically by DSH, Claude Code, Cursor, and Codex. Read it before your first edit (~3 minutes).
> Humans: [README.md](README.md) (English) · [docs/zh/README.md](docs/zh/README.md) (Chinese).
> Contribution entry: [CONTRIBUTING.md](CONTRIBUTING.md) · [docs/zh/CONTRIBUTING.md](docs/zh/CONTRIBUTING.md).

This repository is the **DSH × Blender plugin** (v3.1.0-alpha.1): a DeepSeek Harness plugin that drives a live
Blender session over a loopback TCP bridge. Model surface: **6 tools** (`blender_session` / `blender_op` / `blender_py` / `blender_see` / `blender_run` / `blender_inspect`); planning surface:
**34 families / 228 ops**; Blender addon **v2.0.0 / protocol 12** (Blender extension install); Gateway `127.0.0.1:39877`, addon dials the Gateway (no inbound port).
Ports, config keys, and paths are facts of [docs/配置参考.md](docs/配置参考.md) — never invent them.

## 1. Commands (single source)

Every other document links here instead of repeating commands.

| Task | Command |
|---|---|
| Install dependencies | `npm run setup:dev` (npm ci + fake-bpy stubs + venv; bare `npm ci --legacy-peer-deps` is the CI minimal path) |
| Build `runtime/` + `src/` → `lib/` | `npm run build` (optional `DSH_CHECKOUT` links DSH host packages) |
| Build / verify the addon extension | `npm run build:extension` / `npm run check:extension` |
| Typecheck | `npm run typecheck`; addon typed-core `npm run typecheck:extension` |
| Unit tests | `npm run test:unit`; addon unit `npm run test:extension-unit` |
| Integration tests | `npm run test:integration` |
| Contract tests | `npm run test:contract` |
| Full suite (Vitest + pytest) | `npm test` |
| Python tests only | `npm run test:python` with `PYTHON=/path/to/venv/python` |
| Lint / format gate | `npm run lint` / `npm run format:check` |
| Python static gate | `npm run check:python-static` (plain ruff + pure-logic pyright; no allowlist) |
| Package contents check | `npm pack --dry-run` |
| Refresh counts after catalog edits | `node scripts/sync_counts.mjs` then update `tests/fixtures/capability.lock.json` ([tests/MIGRATION.md](tests/MIGRATION.md)) |

Addon typing: install `requirements-typecheck.txt` (fake-bpy stubs + NumPy for legacy imports; type-only; behavior is real-Blender E2E).
Addon IDE: root `pyrightconfig.json` = whole-addon editor config; it ignores legacy `tests/e2e/blender/native` and `runtime/py` diagnostics. `pyrightconfig.extension.json` = typed-core gate; `pyrightconfig.python.json` = pure-logic runtime scope; `pyproject.toml` = pytest/Ruff configuration.

Node **24.9** is the minimum supported version; CI runs on Node 24. Python tests need a venv with `pytest`, selected through
`PYTHON` (`PYTHONIOENCODING=utf-8`, `PYTHONUTF8=1` on Windows); layers and fixtures:
[tests/README.md](tests/README.md).

## 2. Architecture map

Flow: DSH host → `src/` tools → backend HTTP → engine dispatch → Blender addon → bpy.

| Layer | Path |
|---|---|
| Host entry | `src/index.ts` — entry-only; re-exports the plugin API and frame cache |
| Host modules | `src/plugin.ts`, `src/tools/*` (one file per tool), `src/plan/*`, `src/host/*`, `src/ui/*`, `src/contracts/*`, `src/tui/*`, `src/plugin-lifecycle.ts`, `src/plugin-surfaces.ts` |
| Backend HTTP | `runtime/http/*` — routes, lease, host views |
| Backend engine | `runtime/core/*` (dispatch, kernel, scripts), `runtime/execution/*` (headless, jobs, workers), `runtime/transport/*` |
| Backend language | `runtime/**/*.ts` compiled to `lib/runtime/**/*.js`; `lib/` is never committed |
| Python domains | `runtime/py/{core,geometry,assembly,render,export}` (registry `runtime/py/modules.json`; no flat `runtime/*.py` after the v3 domain split) |
| Native helper | `native/win-hidden-desktop/` — C++17 helper for Windows virtual-gui; `npm run build:win-helper` writes `native/win-hidden-desktop/out/` (gitignored), and releases stage the binary under `vendor/win-hidden-desktop/` |
| Blender addon | `extension/dsh_blender_bridge/` package and the committed `extension/dsh_blender_bridge.zip` |
| Built output | `lib/` built by `prepack`; source maps carry inline sources |

## 3. Hard rules

- **Installed means verified.** GUI route: `doctor` returns `kind=ok`. Headless-only route:
  `blender_run(kind="headless")` submit + `wait`/`collect` returns outputs. Never report success otherwise.
- **Install, config, and troubleshooting facts** live in [docs/操作教程.md](docs/操作教程.md) and
  [docs/配置参考.md](docs/配置参考.md). Read them before advising.
- **Only the bundled addon is supported** (`extension/dsh_blender_bridge.zip`, v2.0.0 / protocol 12).
  MCP for Blender is unsupported since v2.3; migration is in [MIGRATIONS.md](MIGRATIONS.md).
- **Tool and op names come from the catalog**: `blender_op(op="catalog")`; fix from `did_you_mean`.
- **Not installed / not covered**: landmark and face (no cv2 or MediaPipe), OCR, DXF,
  Open3D / manifold3d / glTF-Validator. Never document them as supported.
- **GUI first, headless jobs.** Work over one minute goes to `blender_run(action="submit", kind=render|headless)`;
  poll by job id — a client timeout is not a failed job.
- **Verdicts are three-state** (`pass` / `fail` / `degraded`); `degraded` is not a pass; delivery gates
  use `gate_preflight` / `gate_checkpoint` / `gate_deliver` and `deliver_export(require_gate=true)`.
- **Migrate in slices** ([architecture](docs/architecture.md)): one module or boundary per change,
  no behavior change while moving code, and the old file is deleted in the same change.
- **No new rule files.** A lesson learned goes into this file or the architecture guide.

## 4. Task routing

| Task | Start here |
|---|---|
| Install, deploy, configure, troubleshoot | [docs/操作教程.md](docs/操作教程.md) → [docs/配置参考.md](docs/配置参考.md) → [MIGRATIONS.md](MIGRATIONS.md) |
| Use the plugin or model a scene | [skills/blender-modeling/SKILL.md](skills/blender-modeling/SKILL.md) → [docs/操作教程.md](docs/操作教程.md) |
| Host side (`src/`) | [src/index.ts](src/index.ts) → [tests/README.md](tests/README.md) → [architecture](docs/architecture.md) |
| Backend (`runtime/`) | [runtime/engine.ts](runtime/engine.ts) · [runtime/gateway.ts](runtime/gateway.ts) → [tests/README.md](tests/README.md) |
| Blender addon | [extension/README.md](extension/README.md) → [docs/宿主集成-接口契约-v2.3增补.md](docs/宿主集成-接口契约-v2.3增补.md) |
| Catalog, op, or route change | section 1 count-sync commands → [tests/README.md](tests/README.md) contract tests |
| Tests, CI, coverage | [tests/README.md](tests/README.md) |
| Architecture and invariants | [docs/architecture.md](docs/architecture.md): read the relevant section first |

## 5. Definition of done

- Typecheck and the tests for the touched layer pass; contract counts stay 6 tools / 34 families /
  228 ops / 25 addon commands / 29 routes / protocol 12 unless the change declares a new count.
  The addon count is 25 **registered** commands; the transport-only `ping` makes 26 wire commands.
  The single truth is `extension/dsh_blender_bridge/command_registry.py` (see architecture §5).
- New behavior has a failing test first. No `skip`, no retry, and no relaxed assertion to hide a failure.
- Docs are updated in the same change, and both language versions stay in sync.
- No secrets, no build artifacts, no `node_modules` or venv; `lib/` is regenerated, never hand-edited.
- The pull request uses one of the templates in [.github/PULL_REQUEST_TEMPLATE/](.github/PULL_REQUEST_TEMPLATE/): `bug_fix.md`, `feature.md`, `docs_update.md`, or `refactor.md`.

## 6. Disclosure

Agent-authored work must be disclosed in two places; hiding it, or presenting it as human work, is a
review failure.

- **Commit**: add the trailer `AI-Assisted: <assistant> (<model>)`, for example
  `AI-Assisted: DeepSeek Harness (deepseek-flash)`. Keep an existing `Co-Authored-By:` trailer.
- **Pull request**: fill the disclosure section of the template with the assistant, the model, and
  which parts it wrote or reviewed. Write `none` when no agent was involved.

## 7. Facts and where they live

| Fact | Source |
|---|---|
| Install, upgrade, uninstall, troubleshooting | [README.md](README.md) · [docs/zh/README.md](docs/zh/README.md) · [docs/操作教程.md](docs/操作教程.md) |
| Install prompt for another AI assistant | [docs/AI-安装提示词.md](docs/AI-安装提示词.md) |
| Config keys, ports, environment variables | [docs/配置参考.md](docs/配置参考.md) |
| Tool parameters, ops, families | `blender_op(op="catalog")` · [docs/操作教程.md](docs/操作教程.md) |
| Addon and host integration contract | [docs/宿主集成-接口契约-v2.3增补.md](docs/宿主集成-接口契约-v2.3增补.md) |
| Architecture and constraints | [docs/architecture.md](docs/architecture.md) |
| Test layers and fixtures | [tests/README.md](tests/README.md) |
| Version history and migrations | [CHANGELOG.md](CHANGELOG.md) · [MIGRATIONS.md](MIGRATIONS.md) |