# Proposal

## Why

仓库的静态检查与模块边界目前是"快照"，不是"准入规则"：新增代码可以合法地落在检查范围之外，而且没有任何 gate 会因此失败。

Python 侧：

- ruff 根目录硬编码在 `scripts/check-python-static.mjs:47`（`runtime/py`、`tests`、`extension/dsh_blender_bridge`），漏掉 4 个已入库的 `.py`：`scripts/catalog_dispatch_keys.py`、`scripts/python_function_budget.py`、`docs/examples/chair-backrest/run.py`、`tools/skill_probe.py`。实测这 4 个文件当前 ruff 全绿，补范围不会引入债务。
- pyright 白名单（`pyrightconfig.python.json` 13 条、`pyrightconfig.extension.json` 14 条）只是文件清单：没有任何检查保证列出的文件仍存在、仍值得列，也没有规则说明新文件何时该进来。
- 模块边界只有 `scripts/check-import-boundaries.mjs` 的 4 条硬编码正则：Python 域间检查只看直接导入、只认 `modules.json` 已声明的域（新域默认不在检查内）；addon 两条规则同样只看直接导入。实测 `ui/panels` 到 `integrations` 存在间接链路（`ui.panels.index → protocol → command_registry → commands.assets_polyhaven → integrations.polyhaven_client`），当前检查完全看不到。

TypeScript 侧：

- 边界规则只有 `eslint.config.mjs:79-145` 的 3 组 `no-restricted-imports`，覆盖 `src/index.ts`、`src/ui/**`、`src/constants.ts + src/contracts/** + src/host/exec-context.ts` 共 4 个文件集合；`src/{client,headless,host(其余),tools,tui,utils}` 与全部 runtime 子目录没有任何边界约束，形式是 deny-list，新目录默认放行。
- `src/client/index.js`（1175 行，浏览器 bundle 的源文件）不属于 `TS_FILES` 也不属于 `JS_FILES`：实测 ESLint 给它算出 0 条 rule、0 个 globals，等于完全没被 lint；它也不在任何 tsconfig 的 include 里。`eslint.config.mjs:12-13` 的注释却写着 "Boundary directions are enforced here"。
- `check-import-boundaries.mjs` 里唯一的 TS 规则是 `src → lib/runtime` 的一条正则，与 eslint 规则并列，形成两个边界权威。

实测的现状决定了目标：`src` 已存在 4 个区域的循环（`constants ↔ contracts ↔ host ↔ ui`），`runtime` 已存在 10 个模块的目录级循环（`core/config/catalog/display/execution/launch/paths/proc_env/staging/blender-detect`）。所以本次不做"恢复 DAG"式的架构重排，而是把**已存在的依赖显式登记**、让**新依赖默认失败**，把"新目录/新模块自动获得全部权限"改成"必须显式准入"。

为什么现在做：两个工具的 spike 已经完成。import-linter 2.16 在本仓库可解析 `runtime.py`（namespace portion）与 `dsh_blender_bridge`，建图 0.079s，3 条候选契约基线全绿；其 `exhaustive` 实测会把未登记的 `runtime.py._facade` 判失败，正是需要的准入机制。eslint-plugin-boundaries 7.4.2（peer `eslint >=6`、`node >=18.18`）与本仓库 `eslint ^10.13.0`、Node `>=24.9` 兼容。与此同时仓库正在同时进行 3.1.0-alpha.1 发布和 `repo-config-layout` 迁移，每拖一轮，新增目录就会再变成一条未登记边。

## What Changes

**Python 静态范围与边界**

- `pyproject.toml` 增加 `[tool.importlinter]`，落 3 条契约：runtime 五个域互不依赖（`layers` + `exhaustive = true`，只豁免跨域门面模块 `_facade`）；`assets/io.py` 不得导入 `commands`；`ui/panels` 不得导入 `integrations`（先设 `allow_indirect_imports = true` 与现有直接导入语义对齐，收紧留到后续 change）。契约走 grimp 的导入图，替代正则。
- `requirements-dev.txt` 固定 `import-linter==2.16`（与 `ruff==0.17.3` 同风格精确 pin）。
- 新增 `scripts/check-python-imports.mjs`：按 `PYTHON` / `.venv` / `lint-imports` 的控制台脚本定位执行，设置 `PYTHONPATH=<repo>;<repo>/extension`，失败时给出可执行的修复提示；对应 `npm run check:python-imports`，接入 `.github/workflows/pr.yml`。
- `scripts/check-python-static.mjs` 从"跑 ruff + pyright"升级为"范围准入检查"：ruff 根扩到 `scripts/`、`docs/examples/`、`tools/`（覆盖全部 346 个已入库 `.py`）；启动时断言每个 `git ls-files '*.py'` 都落在某个 ruff 根下、每个 pyright include 路径都存在，失败信息直接告诉开发者"把文件放进哪、或把根加到哪里"。
- `scripts/check-import-boundaries.mjs` 退役：Python 两段迁到 import-linter，`src → lib/runtime` 一段迁到 ESLint 的 `no-restricted-imports`（`src/host/runtime-bridge.ts` 用文件级 override 豁免）。

**TypeScript 边界（两个 Phase）**

- Phase 1（`src`）：`eslint-plugin-boundaries@7.4.2` 进入 devDependencies；`eslint.config.mjs` 用 elements 声明 `entry / plugin / tools / host / contracts / ui / tui / headless / client / utils / constants`，`element-types` 用"实测依赖矩阵 + `default: 'disallow'`"，`no-unknown-files: error` 保证新增文件必须归属某个 element；`src/client/index.js` 新增真正的 lint 配置块（browser globals）。现有 3 组 `no-restricted-imports` 被矩阵取代，语义不丢。
- Phase 2（`runtime`）：同一插件声明 runtime 的目录 element 与根级文件 element，矩阵来自实测依赖，`default: 'disallow'`。现有循环不重构，只把边登记下来；新增 `runtime` 模块或目录时未登记就会失败。
- 两个 Phase 完成后删除 `scripts/check-import-boundaries.mjs`，`lint` / `check:finalization` 里不再出现两个边界权威。

**配套（同一变更内完成）**

- 契约测试（`tests/unit/js/contract/`）：pyproject 契约存在且字段完整；eslint elements 覆盖每个 `src` 子目录与根文件、`no-unknown-files` 开启；ruff 根覆盖所有 tracked `.py`；pyright include 全部存在；CI/package.json 引用新命令；旧 script 不再被引用。
- 文档与规则（双语同步）：`AGENTS.md` §1 命令表 + §3 硬规则（"新增 Python 模块/目录、TS 目录时必须更新哪份声明"）；`docs/architecture.md` §2/§4/§5/§8（边界执行栈与例外政策）；`CONTRIBUTING.md` 与 `docs/zh/CONTRIBUTING.md` 增加 "Boundary rules" 一节；`scripts/README.md` 清单；4 个 PR 模板 checklist 各加一行。
- 不新增规则文件（遵守 `AGENTS.md:71`），不启用 ADR，不移动 tsconfig（`repo-config-layout` 负责），不改产品行为/API/协议计数。

## Capabilities

### New Capabilities

无。

### Modified Capabilities

无。本变更只影响仓库工具、CI 与开发文档，不改变插件、runtime、addon 的任何对外行为或接口；按 OpenSpec 规则在 `.openspec.yaml` 设 `skip_specs: true`（与同类的 `repo-config-layout` 一致）。持久的规范性规则写进 `AGENTS.md` 与 `docs/architecture.md`，不新建规则文档。

## Impact

- **Python 工具链**：`pyproject.toml`、`requirements-dev.txt`、`scripts/check-python-static.mjs`、新增 `scripts/check-python-imports.mjs`、删除 `scripts/check-import-boundaries.mjs`。
- **TypeScript 工具链**：`package.json`（devDependencies、scripts、删除 `check:import-boundaries`）、`package-lock.json`、`eslint.config.mjs`。
- **CI**：`.github/workflows/pr.yml` 增加 Python 边界步骤；现有 `Lint` / `Python static gate` 步骤保持。
- **测试**：新增 `tests/unit/js/contract/boundary-governance.test.ts`、`tests/unit/js/contract/python-static-scope.test.ts`；`scripts/test.mjs` 对 `check-python-static.mjs` 的调用保持不变（脚本增加 main guard 后仍可直接执行）。
- **文档**：`AGENTS.md`、`docs/architecture.md`、`CONTRIBUTING.md`、`docs/zh/CONTRIBUTING.md`、`scripts/README.md`、`.github/PULL_REQUEST_TEMPLATE/{bug_fix,feature,docs_update,refactor}.md`。
- **风险与协调**：`repo-config-layout` 正在同时改 `package.json`、`CONTRIBUTING.md`、`docs/architecture.md`，实施需基于最新 main 在独立 worktree 落地，避免行级冲突；开启 eslint-plugin-boundaries 后若暴露既有未声明边，按"登记为显式允许边或修复"处理，不引入按文件豁免；ESLint 10 与插件 7.4.2 的实际兼容性在 Phase 1 第一步用一条真实 lint 命令验证。
- **不变**：6 tools / 34 families / 228 ops / 25 addon commands / 29 routes / protocol 12；`lib/` 产物逐字节不变。
