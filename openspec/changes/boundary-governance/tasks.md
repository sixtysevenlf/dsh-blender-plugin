# Tasks

## 0. 准备

- [ ] 0.1 从最新 main 建独立 worktree（或把当前分支 rebase 到最新 main），确认 `repo-config-layout` 对 `package.json`/`CONTRIBUTING.md`/`docs/architecture.md` 的并发改动不会与本变更重叠；跑 `npm run setup:dev` 与基线门禁 `npm run lint`、`npm run check:python-static`，记录基线输出。验证：工作区基于最新 main、两条命令均 exit 0。

## 1. Python 范围准入

- [ ] 1.1 先写失败测试：新增 `tests/unit/js/contract/python-static-scope.test.ts`，用子进程覆盖三件事——(a) 真实 tracked 列表下 `node scripts/check-python-static.mjs --scope-only` exit 0；(b) 通过 `CHECK_PYTHON_TRACKED_FILES` 注入不在 roots 下的路径（如 `tools/x.py`）时 exit 1 且 stderr 点名该文件；(c) 真实 `pyrightconfig.python.json`/`pyrightconfig.extension.json` 的 include 全部存在。验证：实现前该测试红（`--scope-only`/seam 不存在），`npx vitest run tests/unit/js/contract/python-static-scope.test.ts` 最终绿。
- [ ] 1.2 改 `scripts/check-python-static.mjs`：`PYTHON_ROOTS` 扩为 `runtime/py`、`tests`、`extension/dsh_blender_bridge`、`scripts`、`docs/examples`、`tools`（同一常量驱动 ruff 参数与断言）；用 `git ls-files '*.py'` 断言每个 tracked 文件都落在某个 root 下；断言两个 pyright 配置的 include 存在、是文件、无重复；新增 `--scope-only`、`CHECK_PYTHON_TRACKED_FILES` 测试 seam、main guard 与可执行的错误提示。验证：`npm run check:python-static` 绿（ruff 输出确认扫到新增根），`node scripts/check-python-static.mjs --scope-only` exit 0。
- [ ] 1.3 更新 `AGENTS.md` §1 的 Python static gate 行（改为 ruff + 范围准入 + pure-logic pyright）与 §3 硬规则（新增 Python 模块/域必须落进 ruff 根或 import-linter 已声明层），更新 `scripts/README.md` 的 `check-python-static.mjs` 行。验证：`npm run lint`（含 `check-doc-paths`）通过，且文档中的命令可复制执行。

## 2. Python 边界（import-linter）

- [ ] 2.1 `requirements-dev.txt` 增加 `import-linter==2.16`；`pyproject.toml` 增加 `[tool.importlinter]` 三段契约（runtime 域 independence + exhaustive + `exhaustive_ignores = ["_facade"]`；addon assets/io→commands；addon ui/panels→integrations 且 `allow_indirect_imports = true`）并同步改文件头注释（single home 改为 pytest + Ruff + import-linter）。验证：`npm run setup:dev` 成功；`python -m pip show import-linter` 显示 2.16；在仓库根以 `PYTHONPATH=<repo>;<repo>/extension` 手工跑 `lint-imports --no-cache --no-logo` 得到 3 contracts kept。
- [ ] 2.2 新增 `scripts/check-python-imports.mjs`（解析 `PYTHON` → `.venv` → `python`/`python3`；先找解释器同目录的 `lint-imports`/`lint-imports.exe`再回退 PATH；设置含 `extension` 的 `PYTHONPATH`；`--no-cache`；失败提示 `npm run setup:dev`）；`package.json` 增加 `check:python-imports`；`.github/workflows/pr.yml` 的 `static-and-tests` 在 `Lint` 之后增加该步骤。验证：`npm run check:python-imports` 绿（3 keys kept，建图 <1s）。
- [ ] 2.3 删除 `scripts/check-import-boundaries.mjs` 的 Python 两段（`src → lib/runtime` 段保留到 group 3），脚本输出改为只覆盖剩余检查。验证：`npm run check:import-boundaries` 绿；临时在 `runtime/py/core` 加一行 `from ..geometry import geo`（不提交）后 `npm run check:python-imports` 必须 FAIL 并指出文件，还原后复绿。
- [ ] 2.4 新增 `tests/unit/js/contract/boundary-governance.test.ts` 的第一部分：`pyproject.toml` 三段契约的 name/type/关键字段、`root_packages`、`exhaustive`/`exhaustive_ignores`/`allow_indirect_imports`；`package.json` 有 `check:python-imports`、`pr.yml` 有该步骤、`AGENTS.md` 与 `scripts/README.md` 有条目。验证：`npx vitest run tests/unit/js/contract/boundary-governance.test.ts` 绿。
- [ ] 2.5 文档：`docs/architecture.md` §8 新增 "Static scope and import boundaries" 小节，写清 ruff 根、pyright include 断言、import-linter 契约、无 per-file waivers、如何新增 Python 模块/域；`AGENTS.md` §1 加 `check:python-imports` 行；`CONTRIBUTING.md` 与 `docs/zh/CONTRIBUTING.md` 新增 "Boundary rules"/"边界规则" 一节并先写入 Python 部分；`scripts/README.md` 增加新脚本行。验证：`npm run lint`（`check-doc-paths`）通过，中英文两节的命令与路径一致。

## 3. TypeScript Phase 1（src 元素矩阵）

- [ ] 3.1 安装 `eslint-plugin-boundaries@7.4.2` 到 devDependencies 并更新 lockfile；先跑一条真实 lint 验证与 ESLint 10 兼容。验证：`npm ci --legacy-peer-deps` 成功，`npx eslint src/index.ts` 正常退出；若插件在 ESLint 10 下不可用，停止实施并升级决策（不在任务内自行换方案）。
- [ ] 3.2 改 `eslint.config.mjs`：按 design D5 声明 src elements（entry/constants/plugin/tools/host/contracts/ui/tui/headless/client/utils）与 `element-types` 矩阵（`default: 'disallow'`），启用 `boundaries/no-unknown-files: error`；为 `lib/**` 设 `boundaries/ignore`；为 `src/client/**/*.js` 加独立 lint 块（browser globals + `require`，client element，同样 default disallow）。验证：`npm run lint` 在现有代码上绿；若出现矩阵外的真实边，按 design 的"显式允许边"补进矩阵（不允许按文件豁免）。
- [ ] 3.3 用矩阵取代现有 3 组 `no-restricted-imports`（entry-only、ui 叶子、基础层）；把 `src → lib/runtime` 检查迁成 `no-restricted-imports`（`src/**/*.ts` 禁止 `**/lib/runtime/**`），并用后置配置块对 `src/host/runtime-bridge.ts` 关闭该规则。验证：`npm run lint` 绿；临时在 `src/ui/cards.ts` 加 `import '../tools/session/shared.js'`（不提交）必须失败，还原后复绿。
- [ ] 3.4 扩展 `tests/unit/js/contract/boundary-governance.test.ts`：断言 elements 覆盖每个 `src` 子目录与根文件、配置含 `default: 'disallow'` 与 `boundaries/no-unknown-files`、存在 client 块与 `runtime-bridge.ts` override、旧 3 组 `no-restricted-imports` 已不存在。验证：`npx vitest run tests/unit/js/contract/boundary-governance.test.ts` 绿。
- [ ] 3.5 文档：`docs/architecture.md` §2 补 src 边界声明（elements 矩阵位置与默认拒绝）；`CONTRIBUTING.md`/`docs/zh/CONTRIBUTING.md` 的 Boundary rules 补 TS 段；`AGENTS.md` §3 加 TS 规则（新目录/文件必须登记 element，新增跨 element 依赖必须改矩阵）；4 个 PR 模板 checklist 各加一行。验证：`npm run lint` 与 `npm run test:contract` 绿。

## 4. TypeScript Phase 2（runtime）与旧脚本退役

- [ ] 4.1 在 `eslint.config.mjs` 增加 runtime 配置块：目录 elements（catalog/core/display/execution/gateway/http/launch/transport）+ `runtime-root`（根级 `runtime/*.ts`），按 design D7 矩阵设 `default: 'disallow'` 与 `no-unknown-files: error`。验证：`npm run lint` 全仓绿；临时在 `runtime/transport` 加对 `runtime/gateway` 的导入（不提交）必须失败，还原后复绿。
- [ ] 4.2 删除 `scripts/check-import-boundaries.mjs`；从 `package.json` 删除 `check:import-boundaries` 与 `check:finalization` 链中的引用；更新 `scripts/README.md` 删除对应行。验证：`npm run check:finalization` 与 `npm run lint` 绿；`rg "check-import-boundaries" --glob '!openspec/changes/archive/**' --glob '!CHANGELOG.md'` 无 hit。
- [ ] 4.3 文档：`docs/architecture.md` §3 加 runtime 边界声明（矩阵位置、循环已冻结、加新目录的做法）；`AGENTS.md` §5 Definition of done 加"边界声明随代码更新"；`scripts/README.md` 确认无旧行。验证：`npm run lint` 通过。
- [ ] 4.4 扩展契约测试：断言 runtime 目录 elements 全覆盖、旧脚本文件不存在、`check:finalization` 不含旧命令、`package.json` 无 `check:import-boundaries`。验证：`npx vitest run tests/unit/js/contract/boundary-governance.test.ts` 绿。

## 5. 集成验证

- [ ] 5.1 全量门禁：`npm run lint`、`npm run check:python-static`、`npm run check:python-imports`、`npm run typecheck`、`npm run test:unit`、`npm run test:contract`、`npm run build && git diff --exit-code`（构建产物无意外变更）。验证：全部 exit 0，结果记录到 PR 描述。
- [ ] 5.2 变更卫生：`openspec validate boundary-governance` 通过；`npm pack --dry-run` 无新增文件；PR 按 `feature.md`/`refactor.md` 模板填写并加 `AI-Assisted: DeepSeek Harness (deepseek-flash)` trailer 与披露段落。验证：validate/打包命令 exit 0，PR 模板无空缺。

## Workflow follow-up

- 按项目 review 要求通过后归档：`openspec archive boundary-governance`。
- 归档后确认 `openspec/changes/boundary-governance` 从 in-progress 列表消失，主 specs 未被本变更修改。
