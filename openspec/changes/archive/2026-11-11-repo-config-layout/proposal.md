# Proposal

## Why

根目录的工程配置文件正在变成“只能靠人记”的隐性契约。当前 4 个 TypeScript 工程配置（根 `tsconfig.json`、`tsconfig.runtime.json`、`tsconfig.test.json`、`tsconfig.scripts.json`）加 3 个 pyright 配置、`vitest.config.ts`、`eslint.config.mjs` 全部平铺在仓库根；编辑器只按“最近的 tsconfig.json”发现工程，根配置只 `include: ["src"]`，于是 `runtime/`、`tests/`、`scripts/` 在编辑器里都落进 inferred project。TypeScript 6 不再隐式加载 `node_modules/@types/*`，这些文件在 VS Code 里直接报 TS2591：

- `runtime/core/evidence.ts(3,16): Cannot find name 'node:fs'`（同文件 `node:crypto`、`node:path`、`node:url`、`Buffer`、`process` 一并报错）
- `tests/` 与 `scripts/` 同理，只是目前没人报

更麻烦的是配置之间的引用关系散落在脚本里：`scripts/build.mjs` 的 TARGETS、`scripts/ensure-build.mjs` 的 WATCH_FILES、`scripts/check-doc-paths.mjs` 的 ROOT_FILES、`package.json` 的 typecheck 链、`tests/types/shims.d.ts` 的路径、`docs/architecture.md` 与 `scripts/README.md` 的说明，全部硬编码旧文件名。任何一次“把配置挪到该在的位置”都会牵动这些引用，而目前没有机器检查能证明“所有引用都改了、每个 .ts 都有家可归”。

修一处 `runtime/tsconfig.json` 只是止血。按 AGENTS.md“迁移切片、旧文件同一次变更删除”的要求，现在应该一次把工程发现收敛成可枚举、可机检的布局，并让开发环境的一键脚本顺手把编辑器 TypeScript 指到仓库版本（用户已明确要求纳入本 change）。

并发约束：另一个会话正在准备 3.1.0-alpha.1 发布，工作区约 28 个文件被同时修改（版本号、README、CHANGELOG、workflow、测试等），其中 `package.json` 与本次迁移都要碰。本 change 的实施必须在独立 worktree 里做，先 rebase 到最新 main/release 分支，避免与发布提交互相覆盖。

## What Changes

- **就近 tsconfig 成为唯一真相**：`tsconfig.runtime.json` 的内容迁入 `runtime/tsconfig.json`，`tsconfig.test.json` 迁入 `tests/tsconfig.json`，`tsconfig.scripts.json` 迁入 `scripts/tsconfig.json`，旧的根级配置在同一变更中删除。根 `tsconfig.json` 只保留 host/`src` 工程，不再承担“全仓 solution”角色。
- **每个受版本控制的 .ts 恰好属于一个可发现工程**（允许白名单，例如根上的 `vitest.config.ts`；白名单本身写进 checker 并说明理由），每个目录的编辑器工程与 CLI 工程是同一份配置。
- **引用收敛**：`scripts/build.mjs`、`scripts/ensure-build.mjs`、`scripts/check-doc-paths.mjs`、`package.json` 的 typecheck 链、`tests/types/shims.d.ts`、`docs/architecture.md`、`scripts/README.md`、`CONTRIBUTING.md` 与 `docs/zh/CONTRIBUTING.md` 的工程说明改到新路径；CHANGELOG 等历史记录不改。
- **新增布局检查器**（`scripts/` 下新脚本，名称在 design 定）：校验“每个 .ts 恰好一个工程”“配置引用无死路径”“根目录只剩白名单文件”，接入 lint/CI，并配负向 contract 测试（先红后绿）防止回潮。
- **开发环境一键配置编辑器**：`scripts/setup-dev.mjs` 增加编辑器配置步骤——`.vscode/settings.json` 不存在时创建，写入 `js/ts.tsdk.path: "node_modules/typescript/lib"`、`js/ts.tsdk.promptToUseWorkspaceVersion: true` 与 `editor.defaultFormatter: "esbenp.prettier-vscode"`；文件已存在时只补缺失键，保留已有值与可识别的 JSONC 注释，已有键不覆盖；无法安全编辑时打印手动 snippet；CI 跳过；`--check` 只验证不写。formatter 键让编辑器默认格式化器与 `npm run format:check` 一致。文档明确这只是便利，不是仓库保证（`.vscode/` 被 gitignore）。
- **发布卫生**：`package.json` 的 `files` 补齐 `!tests/tsconfig.json` 等排除（`runtime/tsconfig.json` 已有排除），并用 `npm pack --dry-run` 断言 tarball 里没有 tsconfig；`tests/` 仍在包内，因此必须显式排除。
- **零行为变更**：不引入 project references、不移动 pyright 配置、不让 `.vscode/` 入库、不新增 `tsconfig.base.json`；构建产物 `lib/` 必须逐字节不变。

## Capabilities

### New Capabilities

无。本次是仓库工程配置与开发工具变更，不改变插件、运行时或 addon 的任何对外行为；按 OpenSpec 规则在 `.openspec.yaml` 设 `skip_specs: true`，不为通过校验硬造行为需求。

### Modified Capabilities

无（当前 `openspec/specs/` 只有 `plan-op-dispatch`，其需求不受影响）。

## Impact

- **配置与脚本**：根 `tsconfig.json`；`tsconfig.runtime.json` → `runtime/tsconfig.json`；`tsconfig.test.json` → `tests/tsconfig.json`；`tsconfig.scripts.json` → `scripts/tsconfig.json`（旧文件删除）；`package.json`（typecheck 链、`files`）；`scripts/build.mjs`、`scripts/ensure-build.mjs`、`scripts/check-doc-paths.mjs`、`scripts/typecheck.mjs`（如涉及）、`scripts/setup-dev.mjs`；新增布局检查脚本与其测试；`tests/types/shims.d.ts`。
- **文档**：`CONTRIBUTING.md`、`docs/zh/CONTRIBUTING.md`（工程布局 + 编辑器配置）、`scripts/README.md`、`docs/architecture.md` 的引用行、`CHANGELOG.md`（迁移与脚本路径变化）。
- **行为/产物**：无运行时行为变化；`npm run build` 输出的 `lib/**` 与迁移前逐字节相同（sha256 对比）；发布 tarball 不含任何 tsconfig。
- **开发环境**：VS Code 通过 workspace TypeScript 获得同源编译选项；`setup-dev` 可选写入本地 `.vscode/settings.json`（不入库）；其它编辑器不受影响。
- **并发/风险**：与 3.1.0-alpha.1 发布会话重叠，实施需独立 worktree、先 rebase；根目录工具配置（`package.json`、`vitest.config.ts`、`eslint.config.mjs`、`pyproject.toml`、根 `tsconfig.json`）因工具硬依赖保留在根，目标是把 7 个 TS/pyright 配置收敛为 1 个根 tsconfig + 3 个子目录 tsconfig + 3 个根 pyright（pyright 移动收益为负，本 change 不做）。
