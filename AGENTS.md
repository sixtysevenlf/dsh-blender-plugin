# AGENTS.md — repository guide for AI assistants

> Loaded automatically by DSH, Claude Code, Cursor, and Codex. Read it before your first edit (~3 minutes).
> Humans: [README.md](README.md) (English) · [zh-README.md](zh-README.md) (Chinese).
> Contribution entry: [CONTRIBUTING.md](CONTRIBUTING.md) · [zh-CONTRIBUTING.md](zh-CONTRIBUTING.md).

This repository is the **DSH × Blender plugin** (v2.6.2): a DeepSeek Harness plugin that drives a live
Blender session over a loopback TCP bridge. Model surface: **15 tools**; planning surface:
**34 families / 228 ops**; Blender addon **v1.11 / protocol 11** (package + zip install); backend `127.0.0.1:9877`, addon `127.0.0.1:9876`.
Ports, config keys, and paths are facts of [docs/配置参考.md](docs/配置参考.md) — never invent them.

## 1. Commands (single source)

Every other document links here instead of repeating commands. The `Status` column is honest: `planned`
rows do not exist in `package.json` yet and must keep the marker until their task lands.

| Task | Command | Status |
|---|---|---|
| Install dependencies | `npm ci --legacy-peer-deps` | available |
| Build host `src/` → `lib/` | `DSH_CHECKOUT=/path/to/dsh-harness npm run build` | available; needs a DSH checkout |
| Build / verify the addon zip | `npm run build:addon` / `npm run check:addon` | available (T9) |
| Typecheck | `npm run typecheck` | available |
| Unit tests | `npm run test:unit` | available |
| Integration tests | `npm run test:integration` | available |
| Contract tests | `npm run test:contract` | available |
| Full suite (Vitest + pytest) | `npm test` | available |
| Python tests only | `npm run test:python` with `PYTHON=/path/to/venv/python` | available |
| Legacy selftests (retired in T11; see [tests/MIGRATION.md](tests/MIGRATION.md)) | — | retired |
| Lint / format gate | `npm run lint` | available (T5) |
| Package contents check | `npm pack --dry-run` | available |
| Refresh counts after catalog edits | `node scripts/sync_counts.mjs` then update `tests/fixtures/capability.lock.json` ([tests/MIGRATION.md](tests/MIGRATION.md)) | available |

Node **22** is the CI and test baseline. Python tests need a venv with `pytest`, selected through
`PYTHON` (`PYTHONIOENCODING=utf-8`, `PYTHONUTF8=1` on Windows); layers and fixtures:
[tests/README.md](tests/README.md).

## 2. Architecture map

Target layout (refactor plan §4). Flow: DSH host → `src/` tools → backend HTTP → engine dispatch →
Blender addon → bpy. The **Current** column is the truth in this worktree until the listed task lands;
never describe a target file as existing.

| Layer | Target | Current (2026-10-30) |
|---|---|---|
| Host entry | `src/index.ts` ≤150 lines, import/apply/export only | `src/index.ts` 2,888 lines, 15 tools inline (**T4**) |
| Host modules | `src/tools/*` (one file per tool), `src/plan/*`, `src/host/*`, `src/ui/*`, `src/contracts/*`, `src/tui-host.ts` | absent until **T4**; `src/tui-host.ts` present |
| Backend HTTP | `runtime/http/*` for routes, lease, host views | `runtime/server.ts` facade, compiled to `lib/runtime/server.js` (**T7/T15** done) |
| Backend engine | `runtime/core/*`, `runtime/execution/*`, `runtime/transport/*` | `runtime/engine.ts` facade + `runtime/core/engine.ts` assembly (**T6/T15** done) |
| Backend language | `runtime/**/*.ts` compiled to `lib/runtime/**/*.js` (**T15**) | `runtime/**/*.ts` (60 modules, strict) built to `lib/runtime/**/*.js` (**T15** done) |
| Python domains | `runtime/py/{core,geometry,assembly,render,export}` plus shims | 40 flat `runtime/*.py` files (**T8**) |
| Blender addon | `addon/dsh_blender_bridge/` package, zip install | package + committed zip, 39 modules (**T9** done) |
| Built output | `lib/` built by `prepack`, not committed | `lib/` removed from git (**T15** done); `prepack` rebuilds it, source maps carry inline sources |

## 3. Hard rules

- **Installed means verified.** GUI route: `doctor` returns `kind=ok`. Headless-only route:
  `blender_rt_worker(op="start")` plus `see` returns a frame. Never report success otherwise.
- **Install, config, and troubleshooting facts** live in [docs/操作教程.md](docs/操作教程.md) and
  [docs/配置参考.md](docs/配置参考.md). Read them before advising.
- **Only the bundled addon is supported** (`addon/dsh_blender_bridge.zip`, v1.11 / protocol 11).
  MCP for Blender is unsupported since v2.3; migration is in [MIGRATIONS.md](MIGRATIONS.md).
- **Tool and op names come from the catalog**: `blender_rt_plan(op="catalog")`; fix from `did_you_mean`.
- **Not installed / not covered**: landmark and face (no cv2 or MediaPipe), OCR, DXF,
  Open3D / manifold3d / glTF-Validator. Never document them as supported.
- **GUI first, headless fallback.** Work over one minute goes to `blender_rt_job` or
  `blender_rt_headless(as_job=true)`; a client timeout is not a failed job.
- **Verdicts are three-state** (`pass` / `fail` / `degraded`); `degraded` is not a pass; delivery gates
  use `gate_preflight` / `gate_checkpoint` / `gate_deliver` and `deliver_export(require_gate=true)`.
- **Migrate in slices** ([ADR 0001](docs/adr/0001-runtime-ts.md)): one module or boundary per change,
  no behavior change while moving code, and the old file is deleted in the same change.
- **No new rule files.** A lesson learned goes into this file, an ADR, or
  [docs/refactor/spec.md](docs/refactor/spec.md).

## 4. Task routing

| Task | Start here |
|---|---|
| Install, deploy, configure, troubleshoot | [docs/操作教程.md](docs/操作教程.md) → [docs/配置参考.md](docs/配置参考.md) → [MIGRATIONS.md](MIGRATIONS.md) |
| Use the plugin or model a scene | [skills/blender-modeling/SKILL.md](skills/blender-modeling/SKILL.md) → [docs/操作教程.md](docs/操作教程.md) |
| Host side (`src/`) | [src/index.ts](src/index.ts) → [tests/README.md](tests/README.md) → [ADR 0001](docs/adr/0001-runtime-ts.md) |
| Backend (`runtime/`) | [runtime/engine.ts](runtime/engine.ts) · [runtime/server.ts](runtime/server.ts) → [tests/README.md](tests/README.md) |
| Blender addon | [addon/README.md](addon/README.md) → [docs/宿主集成-接口契约-v2.3增补.md](docs/宿主集成-接口契约-v2.3增补.md) |
| Catalog, op, or route change | section 1 count-sync commands → [tests/README.md](tests/README.md) contract tests |
| Tests, CI, coverage | [tests/README.md](tests/README.md) → [docs/refactor/spec.md](docs/refactor/spec.md) |
| Architecture decision | [docs/adr/README.md](docs/adr/README.md): write an ADR first |
| Refactor task or acceptance | [docs/refactor/spec.md](docs/refactor/spec.md) and `docs/refactor/evidence/<TASK>/` |

## 5. Definition of done

- Typecheck and the tests for the touched layer pass; contract counts stay 15 tools / 34 families /
  228 ops / 39 addon commands / 29 routes / protocol 11 unless the task declares a change.
- New behavior has a failing test first. No `skip`, no retry, and no relaxed assertion to hide a failure.
- Docs are updated in the same change, both language versions stay in sync, and an evidence pack
  exists under `docs/refactor/evidence/<TASK>/` (`env.txt`, `commands.txt`, raw logs, `acceptance.md`).
- No secrets, no build artifacts, no `node_modules` or venv; `lib/` is regenerated, never hand-edited.
- The pull request uses [.github/pull_request_template.md](.github/pull_request_template.md).

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
| Install, upgrade, uninstall, troubleshooting | [README.md](README.md) · [zh-README.md](zh-README.md) · [docs/操作教程.md](docs/操作教程.md) |
| Install prompt for another AI assistant | [docs/AI-安装提示词.md](docs/AI-安装提示词.md) |
| Config keys, ports, environment variables | [docs/配置参考.md](docs/配置参考.md) |
| Tool parameters, ops, families | `blender_rt_plan(op="catalog")` · [docs/操作教程.md](docs/操作教程.md) |
| Addon and host integration contract | [docs/宿主集成-接口契约-v2.3增补.md](docs/宿主集成-接口契约-v2.3增补.md) |
| Decisions and their rationale | [docs/adr/README.md](docs/adr/README.md) |
| Refactor scope, gates, metrics | [docs/refactor/spec.md](docs/refactor/spec.md) |
| Test layers and fixtures | [tests/README.md](tests/README.md) |
| Version history and migrations | [CHANGELOG.md](CHANGELOG.md) · [MIGRATIONS.md](MIGRATIONS.md) |
