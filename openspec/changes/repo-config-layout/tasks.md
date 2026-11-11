# Tasks

## 1. 准备与基线

- [x] 1.1 从最新 main 建独立 worktree 与分支：先 `git fetch origin --tags`，再 `git worktree add ../dsh-blender-plugin-repo-config -b chore/repo-config-layout <base>`；确认共享工作区不提交、不清理；验证 `git -C ../dsh-blender-plugin-repo-config status` 干净（只有 `local-workspace/` 等明确保留的未跟踪项）。
- [x] 1.2 记录基线：在 worktree 记录 `git rev-parse HEAD`；跑 `npm run build`、`npm run typecheck`、`npm run lint`、`npm run format:check`、`npm run check:build`、`npm run test:unit`、`npm run test:contract`；对 `tsconfig.json`、`tsconfig.runtime.json`、`tsconfig.test.json`、`tsconfig.scripts.json` 跑 `<tsc> --showConfig` 并把 `fileNames` 存成 `local-workspace/repo-config-layout-baseline/*.json`；对 `lib/runtime/**` 的 `.js`/`.d.ts`/`.js.map` 记录 sha256；验证所有命令 exit 0 且基线目录文件齐全。
- [x] 1.3 确认与 3.1.0-alpha.1 发布并发边界：记录共享工作区 `package.json` 的 `version`/`description` 与 `CHANGELOG.md` 当前内容；确认 worktree 基于发布合并后的最新基线，避免在脏工作区提交；验证 worktree 内 `package.json` 不含共享工作区的 release 改动之外的差异。
- [x] 1.4 盘点未提交止血改动的最终去向：`runtime/tsconfig.json` 两行 adapter（S1 升级为真实配置）、`package.json` 的 `"!runtime/tsconfig.json"`（保留）、`CONTRIBUTING.md` 与 `docs/zh/CONTRIBUTING.md` 两段（S1/S4 重写）、`docs/architecture.md` 已恢复 HEAD；验证清单记录在 PR 草稿，不在本 change 中重复搬运临时说明。

## 2. S1 runtime 工程迁移

- [x] 2.1 写 `runtime/tsconfig.json` 真实配置：`rootDir: "."`、`outDir: "../lib/runtime"`、`include: ["**/*.ts"]`、`exclude: ["**/*.d.ts", "ext/venv/**", "ext/node_modules/**"]`，其余编译选项从 `tsconfig.runtime.json` 逐字保留；删除 `tsconfig.runtime.json`；验证新配置的 `--showConfig` `fileNames` 排序集合与 1.2 的旧基线完全相等。
- [x] 2.2 改 runtime 引用：`scripts/build.mjs:20-22` 的 `TARGETS.runtime.config`、`package.json` 的 `typecheck`/`typecheck:runtime`、`scripts/ensure-build.mjs:36-37` 的 `WATCH_FILES`、`scripts/check-doc-paths.mjs:36-44` 的 `ROOT_FILES`、`docs/architecture.md:54`、`CONTRIBUTING.md` 与 `docs/zh/CONTRIBUTING.md` 的 runtime 段；验证活文件中不再出现 `tsconfig.runtime.json`（历史白名单除外），`npm run typecheck` exit 0。
- [x] 2.3 构建产物等价验证：迁移前后各跑 `node scripts/build.mjs runtime`，对 `lib/runtime/**` 的 `.js`/`.d.ts`/`.js.map` 做 sha256 对比，要求逐字节相同；验证 `git diff --stat` 不出现 `lib/`（`lib/` 不提交但哈希需一致），不一致时回滚该片并调查 source map 的 `sources` 差异。
- [x] 2.4 跑切片门禁并提交 S1：`npm run typecheck`、`npm run lint`、`npm run format:check`、`npm run check:build`、`npm run test:unit`、`npm run test:contract`、`node scripts/check-doc-paths.mjs --enforce`；验证全部 exit 0，提交信息带 `AI-Assisted: DeepSeek Harness (deepseek-flash)` trailer。

## 3. S2 tests 工程迁移

- [x] 3.1 写 `tests/tsconfig.json`：`extends: "../tsconfig.json"`，覆盖 `module: ESNext`、`moduleResolution: Bundler`、`rootDir: ".."`、`noEmit: true`、`declaration: true`、`sourceMap: false`，`include: ["**/*.ts", "**/*.d.ts"]`；删除 `tsconfig.test.json`；验证 `--showConfig` `fileNames` 与基线相等，并确认没有把根 `["src"]` 继承进 tests 工程。
- [x] 3.2 改 tests 引用：`package.json` 的 `typecheck`/`typecheck:test`、`ensure-build` 的 `WATCH_FILES`、`check-doc-paths` 的 `ROOT_FILES`、`tests/types/shims.d.ts:5` 注释、CONTRIBUTING 中英的 tests 段；验证活文件不再出现 `tsconfig.test.json`（历史白名单除外），`npm run typecheck:test` exit 0。
- [x] 3.3 跑切片门禁并提交 S2：复用 2.4 的命令清单并额外确认 `npm run test:unit` 对 tests 类型断言无回归；验证全部 exit 0 后提交（带 AI trailer）。

## 4. S3 scripts 工程迁移

- [x] 4.1 写 `scripts/tsconfig.json`：独立配置（不 extends），保留 `allowJs`/`checkJs`/`noEmit`/`strict: false` 等选项，`include: ["check-coverage.mjs", "check-structure.mjs", "check-behavior-matrix.mjs"]`；删除 `tsconfig.scripts.json`；验证 `--showConfig` `fileNames` 与基线相等。
- [x] 4.2 改 scripts 引用与工具解析：`package.json` 的 `typecheck:scripts` 改为 `node scripts/typecheck.mjs -p scripts/tsconfig.json`（去掉裸 `tsc`）、`check-doc-paths` 的 `ROOT_FILES`、`scripts/README.md:73`；验证 `npm run typecheck:scripts` 与 `npm run check:finalization` exit 0。
- [x] 4.3 跑切片门禁并提交 S3：复用 2.4 的命令清单；验证全部 exit 0 后提交（带 AI trailer）。

## 5. S4 编辑器一键配置

- [x] 5.1 新增 `scripts/editor-config.mjs`：导出纯函数 `planEditorConfig({ settingsPath, settingsText, exists, checkOnly, ci })` 与写入内容常量，覆盖 `skip-ci`/`check-ok`/`check-missing`/`check-partial`/`create`/`manual` 六个分支；验证 `node --check scripts/editor-config.mjs` 通过。
- [x] 5.2 在 `scripts/setup-dev.mjs` 接入 `ensureEditorConfig()`：位置在 `ensureNodeModules()` 之后、`printReady()` 之前；CI 跳过、`--check` 只读不写、文件缺失才创建、已存在不解析不覆盖、写失败只 warn、`--force` 不覆盖 `.vscode/settings.json`；验证 `node scripts/setup-dev.mjs --help` 与 `--check` 输出符合 design D4 的行为表且 exit 0。
- [x] 5.3 新增 `tests/unit/js/scripts/editor-config.test.ts` 覆盖六个分支（先写失败用例再实现）；验证 `npm run test:unit` 通过且无 skip。
- [x] 5.4 重写 `CONTRIBUTING.md` 与 `docs/zh/CONTRIBUTING.md` 的编辑器段：就近 tsconfig 布局、`setup-dev` 写入 `typescript.tsdk`/`enablePromptUseWorkspaceTsdk`、VS Code 的 “TypeScript: Select TypeScript Version → Use Workspace Version” 步骤、`.vscode/` 被 gitignore 的边界；验证中英两段语义一致且 `node scripts/check-doc-paths.mjs --enforce` exit 0。
- [ ] 5.5 人工验证编辑器生效：备份本地 `.vscode/settings.json` 后临时移走，运行一次 `node scripts/setup-dev.mjs` 确认生成内容，再在 VS Code 执行 “Use Workspace Version” 并打开 `runtime/core/evidence.ts` 确认无 TS2591，最后恢复原设置文件；验证结果记录到 PR（该步骤无法在 CI 自动化，写明人工证据）。

## 6. S5 布局检查器

- [x] 6.1 实现 `scripts/check-tsconfig-layout.mjs`：用 TypeScript API 的 `readConfigFile` + `parseJsonConfigFileContent` 计算四个工程的 `fileNames`；R1 每个 `.ts`/`.mts`/`.cts`（`.d.ts` 除外）恰好属于一个工程，白名单仅根 `vitest.config.ts`；R2 四个工程存在且根目录无 `tsconfig.*.json`；R3 `build.mjs`/`package.json`/`ensure-build`/`check-doc-paths` 的引用目标存在；R4 活文件无旧配置名（白名单 `CHANGELOG.md`、`MIGRATIONS.md` 历史段、`openspec/**`，以及 checker 自身的负向 fixture 测试）；支持 `--root <dir>`；验证对当前仓库 exit 0。
- [x] 6.2 新增 `tests/unit/js/contract/tsconfig-layout.test.ts`：用临时 fixture 覆盖五种负向场景（孤儿 `.ts`、一文件落两个工程、根出现 `tsconfig.extra.json`、引用目标不存在、活文件残留旧名），断言 checker 非零退出且消息含具体路径；正向对仓库断言 exit 0；先红后绿，不用 skip。
- [x] 6.3 接入 `package.json` 的 `lint` 链（`&& node scripts/check-tsconfig-layout.mjs`），在 `scripts/README.md` 说明 checker 的规则与白名单；验证 `npm run lint` exit 0，且负向 fixture 测试在 `npm run test:unit` 中按预期变红后再修绿。
- [x] 6.4 跑切片门禁并提交 S5：复用 2.4 的命令清单；验证全部 exit 0 后提交（带 AI trailer）。

## 7. S6 发布卫生与收口

- [x] 7.1 `package.json` 的 `files` 增加 `"!tests/tsconfig.json"`（保留 `"!runtime/tsconfig.json"`）；用 `npm pack --dry-run --json --ignore-scripts`（`npm_config_cache` 指向工作区）验证输出中没有任何路径以 `tsconfig.json` 结尾。
- [x] 7.2 `CHANGELOG.md` 新增迁移条目：根级 TS 工程配置 4→1、runtime/tests/scripts 就近配置、`setup-dev` 编辑器配置、布局 checker；验证条目位于 3.1.0 alpha/Unreleased 区、`prettier --check` 通过，且不覆盖发布侧已有条目。
- [x] 7.3 `git fetch` 后 rebase 到发布侧最新基线，处理 `package.json`/`CHANGELOG.md` 冲突（保留双方语义：版本/描述取发布侧，脚本/`files` 取本 change）；验证 rebase 后 `npm run typecheck`、`npm run lint`、`npm run build`、`npm run check:build` 全绿。（实际：`origin/feat/gateway-topology-3-1` 仍等于 base `ba3f536`，无需 rebase；用户要求并入 feat 而非 main。）
- [ ] 7.4 跑全量门禁：`npm test`、`npm run test:matrix`、`npm run check:finalization`、`npm run check:structure`、`node scripts/check-doc-paths.mjs --enforce`、`npm run format:check`、7.1 的包检查；验证全部 exit 0。（2026-11-11 实测：build / typecheck / lint / format / check:build / check:finalization / check:structure / test:matrix / doc-paths 全绿；`npm test` 仅 5 个 integration 文件失败，同一失败在 base `ba3f536` 的主检出上同样复现，属既有环境问题，见 PR 说明。）
- [x] 7.5 复跑强验证：`runtime/tsconfig.json`、`tests/tsconfig.json`、`scripts/tsconfig.json` 的 `--showConfig` `fileNames` 与 1.2 基线集合相等；`lib/runtime/**` sha256 与 S1 前基线逐字节相同；验证比较脚本输出为空差异。
- [ ] 7.6 发 PR（模板 [.github/PULL_REQUEST_TEMPLATE/refactor.md](.github/PULL_REQUEST_TEMPLATE/refactor.md)）：说明切片、验证结果、人工编辑器检查、并发/rebase 处理、非目标（pyright 下沉、project references、`.vscode` 入库）；披露 AI 参与（assistant DeepSeek Harness、model deepseek-flash、写明本 change 由 agent 编写与验证）；验证 CI 全绿。

## Workflow follow-up

- 归档本 change：`openspec archive repo-config-layout`，验证归档后的 spec/change 状态与链接（本 change `skip_specs: true`，无 spec delta 需要同步）。
- 归档前确认与进行中的 `gateway-topology-3-1` 无同文件冲突（本 change 不触碰 gateway topology 的代码与文档）。