# Design

## Context

See `proposal.md` - Why。这里只记录影响实现方式的现状与约束。

- 根 `tsconfig.json` 是 host 工程：`include: ["src"]`、`rootDir: "src"`、`outDir: "lib"`、`declarationDir: "lib/types"`、`types: ["node"]`、NodeNext、ES2023。`scripts/typecheck.mjs:33` 把它的路径固定为 `path.join(PKG, "tsconfig.json")`，`scripts/check-build.mjs:70` 也 extends 它，所以根 `tsconfig.json` 不能删、不能改名。
- `tsconfig.runtime.json` 是 runtime 工程：`rootDir: "runtime"`、`outDir: "lib/runtime"`、`include: ["runtime/**/*.ts"]`、`exclude: ["runtime/**/*.d.ts", "runtime/ext/venv/**", "runtime/ext/node_modules/**"]`、`noEmitOnError: true`、`inlineSources: true`。构建入口 `scripts/build.mjs:20-22` 的 `TARGETS.runtime.config` 与 `package.json` 的 `typecheck` / `typecheck:runtime` 都写死这个名字。
- `tsconfig.test.json` extends `./tsconfig.json`，覆盖 `module: ESNext`、`moduleResolution: Bundler`、`rootDir: "."`、`noEmit: true`、`declaration: true`、`sourceMap: false`，`include: ["tests/**/*.ts", "tests/**/*.d.ts"]`。
- `tsconfig.scripts.json` 是独立工程（不 extends）：`allowJs: true`、`checkJs: true`、`noEmit: true`、`strict: false`，只 include 三个 .mjs：`scripts/check-coverage.mjs`、`scripts/check-structure.mjs`、`scripts/check-behavior-matrix.mjs`；`typecheck:scripts` 用裸 `tsc -p tsconfig.scripts.json`，与其余工程走 `scripts/typecheck.mjs` 解析编译器的方式不一致。
- 工作区当前有一个未提交的止血改动：`runtime/tsconfig.json` 是只有 `{ "extends": "../tsconfig.runtime.json" }` 的两行适配器；`package.json` 的 `files` 已加 `"!runtime/tsconfig.json"`；`CONTRIBUTING.md:61-64` 与 `docs/zh/CONTRIBUTING.md:56-58` 把它描述为“两行适配文件”；`docs/architecture.md` 已恢复 HEAD。本 change 要把适配器升级成真实配置，并重写这两段说明。
- 引用面已盘点：活引用在 `scripts/build.mjs:21-22`、`package.json` 的 `typecheck` / `typecheck:runtime` / `typecheck:test` / `typecheck:scripts`、`scripts/ensure-build.mjs:36-37`、`scripts/check-doc-paths.mjs:36-44`、`scripts/README.md:73`、`docs/architecture.md:54`、`tests/types/shims.d.ts:5`、`CONTRIBUTING.md`、`docs/zh/CONTRIBUTING.md`；历史记录（`CHANGELOG.md` 旧条目、`openspec/changes/gateway-topology-3-1/design.md:225`、归档 change）不得改。
- 编辑器：`.gitignore:40` 忽略 `.vscode/`；本机 `.vscode/settings.json` 是 JSONC（含注释/尾逗号风险），当前只有 `python.languageServer`、`basedpyright.analysis.configFilePath`、`ruff.configuration` 三个键，没有 `typescript.tsdk`。`package.json` 的 `typescript` 是 devDependency（^6.0.3），但编辑器默认用自带 TS。
- 发布：`package.json` 的 `files` 含 `tests`，且已排除 `!tests/**/*.ts` / `!tests/**/*.mts`，但不排除 `tests/tsconfig.json`；`scripts/` 不入包。
- 并发：另一个会话正在把版本升到 `3.1.0-alpha.1`（约 28 个文件），其中 `package.json` 的 `version`/`description` 与 `CHANGELOG.md` 会与本 change 重叠。

## Goals / Non-Goals

**Goals:**

- 每个需要类型检查的 TS 范围在源码旁有一个 `tsconfig.json`，编辑器按就近发现即可用同一组编译选项；CLI 与编辑器读同一份文件。
- 根级 TS 工程配置从 4 个收敛为 1 个（根 `tsconfig.json` 只服务 `src/`），其余三个配置分别落在 `runtime/`、`tests/`、`scripts/`，旧文件在同一变更删除。
- 每个受版本控制的 `.ts`/`.mts`/`.cts`（`.d.ts` 除外）恰好属于一个可发现工程，白名单只有根 `vitest.config.ts`，并由脚本机检。
- 所有活引用指向新路径；历史记录不被改写。
- `setup-dev` 能一键把 VS Code 指向仓库 TypeScript，并在文件已存在时安全退化为打印手动键。
- 发布产物零 tsconfig；构建产物逐字节不变。

**Non-Goals:**

- 不引入 project references / `tsc -b`：`src/host/runtime-bridge.ts:8-11` 从 `../../lib/runtime/{backend_probe,win_proc,file_guard,ports}.js` re-export，src 依赖构建产物而不是 runtime 工程；`composite` 隐含 declaration + incremental 并和 tests 的 `noEmit` 冲突；根 `tsconfig.json` 还被 `scripts/typecheck.mjs:33` 与 `scripts/check-build.mjs:70` 硬依赖。收益低于改动面。
- 不新增 `tsconfig.base.json` 或任何多层 extends 基座：相对路径（`include`/`rootDir`/`outDir`）按声明文件所在目录解析，多一层基座只会增加“改根配置漂移子工程”的隐性耦合。
- 不移动 pyright 配置：`pyrightconfig.extension.json` 与 `pyrightconfig.python.json` 靠 `-p` 调用，编辑器只认根 `pyrightconfig.json`，移动后要重算两份 include 和两处 `venvPath`，收益为零。
- 不让 `.vscode/` 入库，不承诺编辑器一定使用仓库 TS；不为其它编辑器提供配置。
- 不改运行时/插件/addon 行为，不重构业务代码。

## Decisions

### D1 — 目标布局：配置贴源码，根只留 host 工程

**决定**：

| 位置 | 角色 | 关键设置 |
|---|---|---|
| 根 `tsconfig.json` | host 工程（不变） | `include: ["src"]`，`rootDir: "src"`，`outDir: "lib"` |
| `runtime/tsconfig.json` | runtime 真实配置（原 `tsconfig.runtime.json` 内容迁入） | `rootDir: "."`、`outDir: "../lib/runtime"`、`include: ["**/*.ts"]`、`exclude: ["**/*.d.ts", "ext/venv/**", "ext/node_modules/**"]`，其余选项逐字保留（`noEmitOnError`、`inlineSources`、`declaration`、`sourceMap`） |
| `tests/tsconfig.json` | tests 工程（原 `tsconfig.test.json` 内容迁入） | `extends: "../tsconfig.json"`；覆盖 `module: ESNext`、`moduleResolution: Bundler`、`rootDir: ".."`、`noEmit: true`、`declaration: true`、`sourceMap: false`；`include: ["**/*.ts", "**/*.d.ts"]`（相对 tests/ 等价旧 `tests/**/*.ts`） |
| `scripts/tsconfig.json` | scripts 工程（原 `tsconfig.scripts.json` 内容迁入） | 独立配置；`include: ["check-coverage.mjs", "check-structure.mjs", "check-behavior-matrix.mjs"]` |

- 相对路径必须按声明文件所在目录重算：runtime 的 `rootDir` 从 `"runtime"` 改为 `"."`，`outDir` 从 `"lib/runtime"` 改为 `"../lib/runtime"`；tests 的 `rootDir` 显式写 `".."`（继承的 `"."` 会按 base 配置目录解析成仓库根，显式写 `..` 才能让语义不依赖根配置）。
- tests 的 `include: ["**/*.ts", "**/*.d.ts"]` 不能省略：`extends` 会继承根配置的 `include: ["src"]`（按根目录解析），不覆盖就会把 host 源码拉进 tests 工程。
- 删除 `tsconfig.runtime.json`、`tsconfig.test.json`、`tsconfig.scripts.json`；`runtime/tsconfig.json` 从两行 adapter 升级为真实配置。
- 根目录允许的工程配置文件固定为：`package.json`、`tsconfig.json`、`vitest.config.ts`、`eslint.config.mjs`、`pyproject.toml`、`.prettierrc.json`、`pyrightconfig.json`、`pyrightconfig.extension.json`、`pyrightconfig.python.json`。TS 工程配置从 7 个（4 tsconfig + 3 pyright）降到 4 个（1 tsconfig + 3 pyright）。

**备选与理由**：

- 保留根配置 + 各目录两行 adapter（现状止血）：编辑器选项同源，但“逻辑唯一落点”仍是根文件，adapter 会随根配置漂移，且新增 `tests/`、`scripts/` 时仍要回根改文件 → 否。
- project references：见 Non-Goals，构建模型与 noEmit/vitest 现状冲突。
- `tsconfig.base.json`：多一层间接，相对路径陷阱更多。

### D2 — 引用收敛：一次改完，分类对待

**同步点（全部在同一变更内改）**：

- `scripts/build.mjs:21-22`：`runtime.config` 改为 `runtime/tsconfig.json`（`compile()` 用 `path.join(ROOT, spec.config)`，无需改逻辑）。
- `package.json` 脚本：`typecheck` 改为 `node scripts/typecheck.mjs -p runtime/tsconfig.json && node scripts/build.mjs runtime && node scripts/typecheck.mjs && node scripts/typecheck.mjs -p tests/tsconfig.json`；`typecheck:runtime` 用 `-p runtime/tsconfig.json`；`typecheck:test` 用 `-p tests/tsconfig.json`；`typecheck:scripts` 改为 `node scripts/typecheck.mjs -p scripts/tsconfig.json`，去掉裸 `tsc`，让编译器解析只有 `scripts/typecheck.mjs` 一个入口（`check:finalization` 会调用它，等价覆盖）。
- `scripts/ensure-build.mjs:36-37`：`WATCH_FILES` 改为 `["package.json", "tsconfig.json", "runtime/tsconfig.json", "tests/tsconfig.json", "scripts/tsconfig.json"]`；不新增死条目（`WATCH_DIRS` 已递归收集 `runtime/`、`scripts/`、`tests/helpers` 的时间）。
- `scripts/check-doc-paths.mjs:36-44` 的 `ROOT_FILES`：删除 `tsconfig.test.json`、`tsconfig.scripts.json`、`tsconfig.runtime.json`，保留 `tsconfig.json`；嵌套配置不放进根文件白名单，由 D3 的存在性规则负责。
- `tests/types/shims.d.ts:5` 注释：改为让 `tests/tsconfig.json` 能对 tests 下的 TS 做严格检查。
- `docs/architecture.md:54`：只说“runtime 用 `runtime/tsconfig.json` 编译”，编辑器/工程布局说明不放在架构文档。
- `scripts/README.md:73`：改为 `scripts/tsconfig.json`。
- `CONTRIBUTING.md:61-64` 与 `docs/zh/CONTRIBUTING.md:56-58`：重写为最终布局（根 + runtime/tests/scripts 各自就近配置、编辑器发现规则、D4 的一键配置、D3 的 checker），中英同步。
- `CHANGELOG.md`：在 3.1.0 的 Unreleased/alpha 区新增一条迁移说明；与发布会话冲突时以 rebase 后的版本为准，只加不删。

**不改**：`CHANGELOG.md` 旧条目、`openspec/changes/gateway-topology-3-1/design.md:225`、`openspec/changes/archive/**`。D3 的禁用名扫描把这三类文件作为白名单，其余活文件出现旧名即失败。

### D3 — 布局检查器：`scripts/check-tsconfig-layout.mjs`

用 TypeScript 编译器 API（`node_modules/typescript` 的 `readConfigFile` + `parseJsonConfigFileContent`）而不是反复 spawn `tsc`，保证快且与真实解析一致。规则：

- **R1 覆盖唯一**：枚举仓库内受版本控制的 `.ts`/`.mts`/`.cts`（跳过 `node_modules`、`lib`、`.venv`、`local-workspace`、`extension/**/__pycache__` 等生成目录），对每个文件要求“恰好出现在一个工程的 `fileNames` 中”。`.d.ts` 不要求覆盖（runtime 工程显式 exclude `.d.ts`，tests 工程 include 它们）；白名单只有根 `vitest.config.ts`，理由写进脚本注释（工具固定位置，import `vitest/config`，不属于任何运行时/测试工程）。
- **R2 工程存在性**：根 `tsconfig.json`、`runtime/tsconfig.json`、`tests/tsconfig.json`、`scripts/tsconfig.json` 必须存在且可解析；根目录不得出现 `tsconfig.*.json`。
- **R3 引用存在**：`scripts/build.mjs` 的 `TARGETS.*.config`、`package.json` 各 `typecheck*` 脚本里的 `-p` 目标、`ensure-build` 的 `WATCH_FILES`、`check-doc-paths` 的 `ROOT_FILES` 必须指向真实存在的文件（用正则提取 `[\w./-]+\.json` 后逐个 `fs.existsSync`）。
- **R4 旧名清零**：扫描活文件（`src/**`、`runtime/**`、`tests/**`（不含 `tests/e2e/blender/native`）、`scripts/**`、`package.json`、`.github/**/*.yml`、`README.md`、`README.zh.md`、`AGENTS.md`、`CONTRIBUTING.md`、`docs/**/*.md`、`docs/zh/**/*.md`、`MIGRATIONS.md` 的新增段）中的 `tsconfig.runtime.json`、`tsconfig.test.json`、`tsconfig.scripts.json`；白名单：`CHANGELOG.md`、`MIGRATIONS.md` 历史段、`openspec/**`。

**接入**：`package.json` 的 `lint` 链追加 `&& node scripts/check-tsconfig-layout.mjs`（PR CI 的 lint 必跑）；`--root <dir>` 参数让测试指向临时 fixture。

**测试**：新增 `tests/unit/js/contract/tsconfig-layout.test.ts`，用临时目录构造 fixture 并 spawn checker，断言：孤儿 `.ts`、一个文件落在两个工程、根出现 `tsconfig.extra.json`、`build.mjs` 指向不存在文件、活文件残留旧名，五种场景均非零退出且消息含具体路径；对当前仓库跑一次断言 exit 0（正向基线）。先红后绿，不用 skip。

**为什么独立脚本**：`check-doc-paths` 只验证文档里出现过的路径，`check-structure` 管目录结构，都不能回答“每个 .ts 是否有家可归”。checker 是脚本 + 测试，不是新的规则文件，符合 AGENTS.md 的约束。

### D4 — `setup-dev` 的编辑器配置步骤

- 新增 `scripts/editor-config.mjs`，导出纯函数 `planEditorConfig({ settingsPath, settingsText, exists, checkOnly, ci })`，返回 `{ action: "skip-ci" | "check-ok" | "check-missing" | "check-partial" | "create" | "manual" , content?, message }`；IO（`mkdir`/写文件/打印）留在 `scripts/setup-dev.mjs` 的 `ensureEditorConfig()`，在 `main()` 里 `ensureNodeModules()` 之后、`printReady()` 之前调用。
- 写入内容（仅当 `.vscode/settings.json` 不存在时创建）：

```json
{
  "js/ts.tsdk.path": "node_modules/typescript/lib",
  "js/ts.tsdk.promptToUseWorkspaceVersion": true
}
```

这两个键名对应当前 VS Code；旧版 VS Code 只认已弃用的 `typescript.tsdk` / `typescript.enablePromptUseWorkspaceTsdk`，不在生成器里同时写，避免新版本出现弃用告警。

- 分支行为：

| 场景 | 行为 |
|---|---|
| `process.env.CI` | 跳过，打印 `CI: skip editor settings` |
| `--check`，文件不存在 | 打印“可选，未创建”，exit 0（不写、不失败；编辑器配置不是环境正确性） |
| `--check`，文件存在且有 `js/ts.tsdk.path` | 打印 OK |
| `--check`，文件存在但缺键 | 打印需要手动添加的键，exit 0 |
| 非 `--check`，文件不存在 | 创建 `.vscode/` 并写 JSON；写失败只 warn 不中断 |
| 非 `--check`，文件存在 | 不解析、不覆盖（JSONC 可能含注释）；缺键时打印 snippet，已有键时打印已配置 |
| `--force` | 不覆盖 `.vscode/settings.json`（属于用户本地设置） |

- 验证：`tests/unit/js/scripts/editor-config.test.ts` 覆盖纯函数六个分支（CI、check 缺失/完整/缺键、create、manual）；写文件路径与 VS Code 实际生效由人工验证（见 Migration Plan 的 S4），因为编辑器 TS server 无法在 CI 里自动化。

### D5 — 发布卫生与强验证

- `package.json` 的 `files` 增加 `"!tests/tsconfig.json"`（`"!runtime/tsconfig.json"` 已有；`scripts/` 本就不入包）。
- 包检查：`npm pack --dry-run --json --ignore-scripts`（npm 12 的输出按包名键控，files 在 `j[pkgName].files`；`npm_config_cache` 指向工作区，避免写系统缓存），断言没有任何路径以 `tsconfig.json` 结尾。
- 文件集合等价：迁移前对旧配置跑 `node <tsc> -p <旧配置> --showConfig`，迁移后跑新配置，比较 `fileNames` 排序后的集合，要求完全相等（S1/S2/S3 各做一次）。不用 `--listFiles`（会含 `lib.d.ts`）。
- 产物等价：S1 前先 `sha256sum` 记录 `lib/runtime/**/*.{js,d.ts,js.map}`，S1 后重新构建并比较，要求逐字节相同；host 产物不受 S1 影响。若 source map 因配置路径出现差异，必须定位原因后才能继续，不允许“看起来一样”。
- 常规门禁：每个切片后跑 `npm run typecheck`、`npm run lint`、`npm run format:check`、`npm run build`、`npm run check:build`、`npm run test:unit`、`npm run test:contract`、`node scripts/check-doc-paths.mjs --enforce`、`node scripts/check-structure.mjs`；S6 跑 `npm test`、`npm run test:matrix`、`npm pack` 检查。

### D6 — 并发、切片与代码冻结

- 实施在独立 worktree：`git worktree add ../dsh-blender-plugin-repo-config -b chore/repo-config-layout <base>`；`<base>` 取发布会话合并后的最新 main（至少先 `git fetch`）；不在共享脏工作区提交。
- 现有未提交的止血改动（两行 adapter、`package.json` 排除、CONTRIBUTING 两段）不单独搬运：S1 直接写最终配置，S4 重写 CONTRIBUTING，`package.json` 的排除项保留；共享工作区里的未提交文件保持原样，不提交、不清理，交给用户在需要时处理。
- 切片：S1 runtime、S2 tests、S3 scripts、S4 编辑器配置、S5 checker + 负向测试 + lint 接线、S6 文档/CHANGELOG/包检查/全量门禁。每片一个 commit，提交信息按仓库习惯并带 `AI-Assisted: DeepSeek Harness (deepseek-flash)` trailer（AGENTS.md §6）。
- 回滚：每片只动配置/脚本/文档，`git revert <slice>` 即可；运行时与插件代码零改动。

## Risks / Trade-offs

- [编辑器仍用自带 TS] → 用户不点 “Use Workspace Version” 时提示会消失；缓解：D4 同时写 `js/ts.tsdk.path` 与 `js/ts.tsdk.promptToUseWorkspaceVersion`，CONTRIBUTING 给出 “TypeScript: Select TypeScript Version...” 操作步骤，并明确这是便利而非仓库保证。
- [JSONC 被破坏] → 只创建不覆盖；已有文件只读文本检查键是否存在，不做 JSON.parse/重写。
- [相对路径语义漂移] → 每个工程用 `--showConfig` 的 `fileNames` 集合对比旧配置；S1 额外用 `lib/runtime` sha256 逐字节对比。tests 的 `include` 必须显式覆盖，防止继承 `["src"]`。
- [与 alpha 发布冲突] → 独立 worktree + 先 rebase；`package.json` 只改脚本键与 `files`，不重排/不格式化；`CHANGELOG.md` 只新增一条，冲突时取发布侧版本再追加剧。
- [checker 误报] → 扫描范围显式排除生成目录与未跟踪的 `local-workspace/`；白名单有注释；fixture 测试覆盖误报场景；错误消息给出文件与工程名。
- [setup-dev 写失败] → 非致命 warn，CI 跳过；编辑器配置不阻塞环境创建。
- [checker 依赖 typescript API] → 用 devDependency 的解析结果（`node_modules/typescript`），只在开发/CI（`npm ci` 后）运行，不进入发布产物。
- [历史文档引用旧名] → 明确白名单（`CHANGELOG.md`、`MIGRATIONS.md` 历史段、`openspec/**`），活文档由 D2 一次改完并由 R4 拦截。
- [根目录仍不够“干净”] → 本 change 只收敛 TS 工程配置；`vitest.config.ts`、`eslint.config.mjs`、`pyproject.toml`、3 个 pyright 配置因工具硬依赖保留在根，属于已声明的非目标。

## Migration Plan

1. **准备**：在独立 worktree 记录基线 `git rev-parse HEAD`、基线门禁结果、`lib/runtime` 的 sha256、三个旧配置的 `--showConfig` fileNames JSON（放 `local-workspace/`，不入库）。
2. **S1 runtime**：写 `runtime/tsconfig.json` 真实配置，删 `tsconfig.runtime.json`；改 `build.mjs`、`package.json`（runtime 相关）、`ensure-build`、`check-doc-paths`、`architecture.md`、`CONTRIBUTING` 中英；跑 `typecheck` + `build`，对比 fileNames 与 `lib/runtime` 哈希。
3. **S2 tests**：写 `tests/tsconfig.json`，删 `tsconfig.test.json`；改 `package.json`（typecheck/typecheck:test）、`ensure-build`、`check-doc-paths`、`shims.d.ts`、CONTRIBUTING；对比 fileNames 相等。
4. **S3 scripts**：写 `scripts/tsconfig.json`，删 `tsconfig.scripts.json`；改 `package.json`（typecheck:scripts 走 typecheck.mjs）、`check-doc-paths`、`scripts/README.md`；对比 fileNames 相等。
5. **S4 编辑器配置**：新增 `scripts/editor-config.mjs` 与纯函数测试；`setup-dev.mjs` 接线；重写 CONTRIBUTING 中英的编辑器段；本地删掉 `.vscode/settings.json` 的副本验证一次创建，再在 VS Code 里 “Use Workspace Version” 打开 `runtime/core/evidence.ts` 确认无 TS2591（人工，记录到 PR）。
6. **S5 checker**：实现 `scripts/check-tsconfig-layout.mjs` 与 fixture 测试；接入 `lint`；确认对仓库 exit 0、五种负向 fixture 变红。
7. **S6 收口**：`CHANGELOG.md` 条目、`package.json` 的 `"!tests/tsconfig.json"`、`npm pack` 断言、全量门禁、`git fetch` 后 rebase 到发布侧最新基线，重跑门禁与哈希对比，发 PR（模板用 `refactor.md`，披露 AI 参与）。
8. **回滚**：任一片失败按 commit revert；S1 的 `lib/runtime` 哈希不一致时先回滚该片再调查。

## Open Questions

- 是否把布局 checker 也接进 `check:finalization`（当前只接 `lint`，PR CI 已覆盖）？暂不，若发现 lint 被跳过再单独补。
- 是否需要在 host/doctor 里暴露“编辑器 TS 版本”检查？需要 DSH host 读取本地 `.vscode`，收益低，留待后续 change。
