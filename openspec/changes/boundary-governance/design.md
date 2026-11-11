# Design

## Context

See `proposal.md` - Why for the gap inventory and motivation. This section records only the facts and constraints that shape the implementation.

- 当前 Python 静态门：`scripts/check-python-static.mjs` 用固定参数跑 `ruff`（`runtime/py`、`tests`、`extension/dsh_blender_bridge`）和 `pyright -p pyrightconfig.python.json`；pyright 的两个配置文件（`pyrightconfig.python.json` 13 条 include、`pyrightconfig.extension.json` 14 条 include）都是手写清单。
- 当前边界门：`scripts/check-import-boundaries.mjs` 4 段硬编码（`src → lib/runtime`、Python 域间正则、addon `assets/io.py`、addon `ui/panels`）。Python 段只看直接导入，只遍历 `runtime/py/modules.json` 里已登记的域；addon 段只看直接导入。
- 当前 TS 边界：`eslint.config.mjs` 的 3 组 `no-restricted-imports`（`:79-104` entry、`:105-121` ui、`:122-145` 基础层）；`TS_FILES` 只含 `src/**/*.ts`、`runtime/**/*.ts`、`tests/**/*.ts`，`JS_FILES` 只含 `scripts/**/*.mjs` 与根 `*.mjs`。实测 `src/client/index.js`（1175 行，含 `require('react')`，是浏览器 bundle 的源文件）匹配不到任何配置块，`calculateConfigForFile` 返回 0 条 rule、0 个 globals。
- 实测模块依赖（本次 design 的矩阵来源，方法：解析相对 import 并解析到实际文件）：
  - `src` 依赖边：`entry → {plugin, utils}`；`plugin → {constants, contracts, headless, host, tools, tui, ui, utils}`；`tools → {constants, contracts, host, ui, utils}`；`headless → {constants, contracts, host, tools, ui}`；`host → {constants, contracts}`；`contracts → {constants, host, ui}`；`ui → {contracts}`；`tui → {contracts}`；`utils → {contracts}`；`constants → {contracts}`；`client → {}`。`constants/contracts/host/ui` 构成 4 元循环。
  - `runtime` 目录级依赖边：`root → {catalog, core, display, execution, gateway, http, launch, transport}`；`catalog → {core}`；`core → {catalog, display, execution, launch, transport}`；`execution → {core, transport}`；`gateway → {core, http, transport}`；`http → {catalog, core}`；`launch → {core}`；`display → {}`；`transport → {}`。`core/config/catalog/display/execution/launch/paths/proc_env/staging/blender-detect` 构成 10 元循环（`config.ts` 等根级文件见下）。
- Python 侧已入库 `.py` 共 350 个：`runtime/py` 168、`extension/dsh_blender_bridge` 74、`tests` 104、`scripts` 3、`docs/examples` 1。`scripts` 与 `docs/examples` 不在当前 ruff 参数里；实测这些文件现在 ruff 全绿。
- 约束：
  - `AGENTS.md:71` "No new rule files."：规则进 `AGENTS.md` 或 `docs/architecture.md`，本次不新建规则文档、不启用 ADR。
  - `pyproject.toml:1-4` 声明该文件是 Python 工具配置的 single home；加 `[tool.importlinter]` 时必须同步改这段说明。
  - 仓库 Node `>=24.9`、ESLint `^10.13.0`、pyright `1.1.414`；本地 Windows + CI Ubuntu。
  - `repo-config-layout` 正在同时改 `package.json`、`CONTRIBUTING.md`、`docs/architecture.md`；本变更落在独立 worktree、基于最新 main，避免行级冲突。
  - 现有 `scripts/test.mjs:78-84` 会以子进程调用 `check-python-static.mjs`，脚本必须保持可直接执行。

## Goals / Non-Goals

**Goals:**

- 每种语言只有一个边界执行权威：Python 用 import-linter，TypeScript 用 eslint-plugin-boundaries；旧的 `check-import-boundaries.mjs` 在同一变更内退役。
- 两类"默认放行"改为"默认拒绝"：Python 新增模块/域必须违反或显式登记；TS 新增文件/目录必须归属于已声明 element，新增跨 element 依赖必须显式登记。
- 检查范围本身可机检：每个已入库 `.py` 都在 ruff 根内；每个 pyright include 都真实存在；每次改动都有契约测试防止配置回退。
- 基线全绿、零产品行为变化：ruff/pyright 通过，import-linter 3 契约 KEPT，eslint boundary 规则在既有代码上通过。
- 规则落在既有权威文档（`AGENTS.md`、`docs/architecture.md`），中英文档同步。

**Non-Goals:**

- 不把 `src` 或 `runtime` 重排成 DAG，不修既有循环；只冻结已实测的边。
- 不扩大 pyright 覆盖到 `runtime/py` 全量（动态 `__all__` 门面会让它等于改架构）。
- 不采用 tach，不引入守卫进程/IDE 插件。
- 不引入按文件豁免（per-file waivers）；需要例外时只允许"显式允许边"或修复代码。
- 不移动 tsconfig/工程配置（归 `repo-config-layout`）、不改产品行为/API/协议计数。

## Decisions

### D1. Python 边界工具选 import-linter 2.16，不用 tach

Spike 证据（`tmp/spike` 已清理，结论记录在此）：

- import-linter：grimp 能解析 `runtime.py`（namespace portion，168 个内部模块）与 `dsh_blender_bridge`（74 个）；`lint-imports` 建图 0.079s，"Analyzed 286 files, 1118 dependencies"（286 = 242 内部 + 44 external）；3 条候选契约 KEPT；`exhaustive = true` 实测把未登记的 `runtime.py._facade` 判为 BROKEN，加 `exhaustive_ignores = ["_facade"]` 后全绿。
- tach 0.35.3：基线也能跑（1.9s 含进程启动），但探针显示未登记的新模块（`runtime/py/newdom/`）与根级游离文件（`runtime/py/loose_probe.py`）**完全不被检查**，只有已声明模块内部的越界依赖会 FAIL；且它提示 `tach sync` 更新配置，哲学是"配置追随代码"，与本次目标相反。

替代方案：tach（放弃，缺 admission）；pytest-archon（0.0.7，过新）；pyarchunit（无法核实）；ruff TID（只管相对导入风格，不管层）。结论：import-linter 的 `exhaustive` 是唯一在工具内提供"新增模块即失败"的选项，且配置可进 `pyproject.toml`，符合仓库 single-home 约定。

### D2. import-linter 契约与配置

`pyproject.toml` 追加（同时改文件头注释，说明它现在是 pytest + Ruff + import-linter 的 single home）：

```toml
[tool.importlinter]
root_packages = ["runtime.py", "dsh_blender_bridge"]
include_external_packages = true

[[tool.importlinter.contracts]]
name = "runtime domains are independent"
type = "layers"
containers = ["runtime.py"]
layers = ["core | geometry | assembly | render | export"]
exhaustive = true
exhaustive_ignores = ["_facade"]

[[tool.importlinter.contracts]]
name = "addon assets must not import commands"
type = "forbidden"
source_modules = ["dsh_blender_bridge.assets.io"]
forbidden_modules = ["dsh_blender_bridge.commands"]

[[tool.importlinter.contracts]]
name = "addon ui panels must not import integrations"
type = "forbidden"
source_modules = ["dsh_blender_bridge.ui.panels"]
forbidden_modules = ["dsh_blender_bridge.integrations"]
allow_indirect_imports = true
```

设计要点：

- `layers` 单行用 `|` 表示五个域是"同层 siblings"（互不依赖），不是分层顺序。import-linter 的 layers 契约**包含间接导入**（官方文档明确："This includes indirect imports"）；spike 在真实树上已全绿，所以这条比现有正则更严格且当前满足。
- `exhaustive` 只支持带 `containers` 的 layers 契约；它使 `runtime/py` 下任何未列出的模块失败——这就是 Python 侧的 admission。`_facade` 是跨域门面（只做 `importlib` 动态导入），静态图看不到它的真实依赖，显式豁免并注释原因。
- `assets.io → commands` 用默认（检查间接导入）；spike 当前 KEPT。
- `ui/panels → integrations` 设 `allow_indirect_imports = true`：现有语义是"直接导入禁止"，而真实树存在间接链路（`ui.panels.index → protocol → command_registry → commands.assets_polyhaven → integrations.polyhaven_client`）。这里先与现状对齐，收紧（改成查间接）需要先重构 addon 导入链，作为后续 change，不在本次范围。
- `include_external_packages = true` 与 spike 一致；外部包只进入图，不参与契约。

### D3. import-linter 的调用方式与依赖管理

- `requirements-dev.txt` 增加 `import-linter==2.16`（与 `ruff==0.17.3` 一样精确 pin，避免契约语义随 minor 漂移）；`setup-dev` 的 requirements hash marker 会自动触发一次重装。
- 新增 `scripts/check-python-imports.mjs`：Python 解释器按 `PYTHON` → `.venv` → `python`/`python3` 解析（与 `check-python-static.mjs` 同一顺序）；`lint-imports` 先找"解释器同目录下的 `lint-imports`/`lint-imports.exe`"，再回退 `PATH`；`env.PYTHONPATH = <repo><delimiter><repo>/extension`（保留已有 PYTHONPATH 前缀）；`spawnSync` 继承 stdio，退出码原样返回；找不到工具时提示 `npm run setup:dev`。
  - 这样本地（`.venv/Scripts`）与 CI（setup-python 的 `bin` 同目录、或 PATH）都能工作；CI static job 用系统 `python` 安装 `requirements-dev`，addon job 用 `.venv`，两条路径都覆盖。
- `npm run check:python-imports` 调用该脚本；CI 加在 `static-and-tests` 的 `Lint` 之后（该 job 第 39 行已安装 `requirements-dev.txt`），`addon` job 的 `check:python-static` 保持。

### D4. 范围准入放进 check-python-static.mjs

- 常量 `PYTHON_ROOTS = ['runtime/py','tests','extension/dsh_blender_bridge','scripts','docs/examples']` 同时用于 ruff 参数与断言，避免两处漂移。
- tracked 列表来源 = `git ls-files '*.py'`（只检查已入库文件；本地未追踪的 WIP 文件不误报，`tmp/` 也不会被卷进来）。
- 断言 1：每个 tracked `.py` 必须以某个 root + `/` 开头，否则列出文件并提示"移到已有 root 或显式加 root 并说明原因"。
- 断言 2：`pyrightconfig.python.json` / `pyrightconfig.extension.json` 的每个 include 必须存在且是文件、无重复；失败信息给文件路径。
- 断言 3（隐式）：`git ls-files` 失败或返回空时直接报错，不允许"空列表=通过"。
- 脚本支持 `--scope-only`：只跑上述断言后退出（CI 调试与测试用）；`CHECK_PYTHON_TRACKED_FILES` 环境变量作为测试 seam（换行分隔，仅测试使用，头注释说明）。主逻辑用 `process.argv[1] === fileURLToPath(import.meta.url)` guard 保护，保持 `scripts/test.mjs` 直接执行的方式不变。
- pyright 仍只做"清单存在性"，不做范围扩张；白名单的"该不该进"仍是 review 决策。

### D5. TS Phase 1：src 元素矩阵（冻结 + 默认拒绝）

`eslint.config.mjs` 引入 `eslint-plugin-boundaries`：

- elements（`mode: 'file'` 用于单文件，`folder`/full 用于目录）：`entry`=`src/index.ts`、`constants`=`src/constants.ts`、`plugin`=`src/plugin*.ts`、`tools`=`src/tools/**`、`host`=`src/host/**`、`contracts`=`src/contracts/**`、`ui`=`src/ui/**`、`tui`=`src/tui/**`、`headless`=`src/headless/**`、`client`=`src/client/**`、`utils`=`src/utils/**`。
- rules 只作用于 `files: ['src/**/*.ts']`（tests 不参与边界矩阵）：

| from | allow |
| --- | --- |
| entry | plugin, utils |
| plugin | constants, contracts, headless, host, tools, tui, ui, utils |
| tools | constants, contracts, host, ui, utils |
| headless | constants, contracts, host, tools, ui |
| host | constants, contracts |
| contracts | constants, host, ui |
| ui | contracts |
| tui | contracts |
| utils | contracts |
| constants | contracts |
| client | （空） |

`default: 'disallow'`；`'boundaries/no-unknown-files': 'error'` 保证 `src` 下新增文件必须被某个 element 覆盖。

为什么要"冻结实测边"而不是"按文档重排"：`constants ↔ contracts ↔ host ↔ ui` 是现存循环，`contracts/tool.ts:5-9` 同时依赖 `constants`、`host/exec-context`、`ui/cards`；重排属于行为重构，违反 AGENTS.md "移动代码时零行为变化"的切片原则。本次的价值是"这些边从此必须显式存在"，后续 refactor 可以逐条 ratchet（矩阵只删不加是安全的收紧）。

现有 3 组 `no-restricted-imports` 由矩阵取代：entry-only 与 ui 叶子层的语义不变，基础层规则变成"contracts 只能依赖 constants/host/ui"（与实测一致）。

`src → lib/runtime` 规则迁进 ESLint：对 `files: ['src/**/*.ts']` 设 `no-restricted-imports` 禁止 `**/lib/runtime/**`；再用后置配置块对 `src/host/runtime-bridge.ts` 把该规则设为 `off`（这是唯一允许的桥）。`lib/**` 同时加入 `boundaries/ignore`，避免插件把构建产物当源码解析。

### D6. src/client 的 lint 覆盖

新增一个独立配置块 `files: ['src/client/**/*.js']`：`js.configs.recommended`、`sourceType: 'script'`（文件用 `require` 而非 ESM）、`globals` = `globals.browser` + `{ require: 'readonly' }`；element `client`，`element-types` 用 `default: 'disallow'`（当前无内部依赖），`no-unknown-files: error`。不做类型检查（不在 tsconfig 内），只补 lint。首次运行可能暴露既有问题：按"修复或明确规则调整（记录理由）"处理，不允许用 eslint-disable 批量静音。

### D7. TS Phase 2：runtime 元素矩阵

同一插件、新配置块 `files: ['runtime/**/*.ts']`：

- elements：目录 element `runtime-catalog/core/display/execution/gateway/http/launch/transport`；根级 `.ts`（`config.ts`、`engine.ts`、`gateway.ts`、`paths.ts`、`ports.ts`、`proc_env.ts`、`staging.ts`、`classes.ts`、`hard_exit.ts`、`file_guard.ts`、`selectionstore.ts`、`win_proc.ts`、`backend_probe.ts`、`blender-detect.ts`、`addon-protocol.ts`、`_probe_tools.ts`）统一为 element `runtime-root`（`mode: 'file'`，pattern `runtime/*.ts`）。`runtime/ext/**` 已被 globalIgnores 排除。
- matrix（所有目录都可导入 `runtime-root`）：

| from | allow |
| --- | --- |
| runtime-root | catalog, core, display, execution, gateway, http, launch, transport |
| catalog | core |
| core | catalog, display, execution, launch, transport |
| display | （仅 root） |
| execution | core, transport |
| gateway | core, http, transport |
| http | catalog, core |
| launch | core |
| transport | （仅 root） |

`default: 'disallow'`、`no-unknown-files: error`。既有 10 元循环不修，只登记；根级文件之间同 element，不再细分。

测量方法：先按上表写入配置并运行 `npx eslint runtime`；正则测量可能漏掉 re-export/type-only/动态 import，以 ESLint 的实际 findings 为准补齐"显式允许边"（只加真实存在的边，不允许按文件豁免）。

### D8. 退役 check-import-boundaries.mjs

一次变更内完成三件事：(1) Python 两段 → import-linter；(2) src→lib/runtime → ESLint `no-restricted-imports` + `runtime-bridge.ts` override；(3) 删除 `scripts/check-import-boundaries.mjs`、`package.json` 的 `check:import-boundaries` 与 `check:finalization` 链中的引用、`scripts/README.md` 对应行。旧权威与新权威不同时存在（AGENTS.md 的"旧文件同一变更删除"）。

### D9. 契约测试（静态断言，不引入 TOML 解析依赖）

- `tests/unit/js/contract/boundary-governance.test.ts`：
  - `pyproject.toml`：按 `[[tool.importlinter.contracts]]` 切段，断言 3 个 name、类型/参数（`exhaustive = true`、`exhaustive_ignores` 含 `_facade`、ui 契约含 `allow_indirect_imports = true`）；断言 `root_packages` 两项。
  - `eslint.config.mjs`：包含 `eslint-plugin-boundaries`、`'boundaries/no-unknown-files'`、`default: 'disallow'`；每个 `src` 子目录名出现在 elements 声明里；存在 `src/client` 与 `runtime` 两个配置块；存在 `runtime-bridge.ts` 的 override。
  - `scripts/check-python-static.mjs`：抽出 `PYTHON_ROOTS` 并断言覆盖所有 tracked `.py`（测试自己跑 `git ls-files '*.py'`，与脚本解耦）。
  - `package.json`：`check:python-imports` 存在、`check:import-boundaries` 不存在、`check:finalization` 不含旧命令；`.github/workflows/pr.yml` 含 `npm run check:python-imports`；`AGENTS.md` 与 `scripts/README.md` 有对应条目。
- `tests/unit/js/contract/python-static-scope.test.ts`：以子进程方式验证脚本的 admission 行为——`node scripts/check-python-static.mjs --scope-only` 配合 `CHECK_PYTHON_TRACKED_FILES` 注入 (a) 真实列表 → exit 0；(b) `docs/...` 等未覆盖路径 → exit 1 且 stderr 点名该文件；(c) 不存在的 pyright include（临时 mock 不可行时，改为直接断言真实配置全部存在）。不依赖 ruff/pyright 是否安装。

### D10. 文档与规则落点（遵循 AGENTS.md:71）

- `AGENTS.md` §1 命令表加 `check:python-imports` 行、Python static gate 描述改为"ruff + 范围准入 + pure-logic pyright"；§3 硬规则加两条：Python 新模块/新域必须落进 ruff 根或 import-linter 已声明层；TS 新目录/新文件必须登记 element，新增跨 element 依赖必须改矩阵。§5 Definition of done 加一句"边界声明随代码更新"。
- `docs/architecture.md`：§2/§4/§5 各补一句边界声明；§8 新增 "Static scope and import boundaries" 小节，写清两个工具、契约/矩阵位置、例外政策（无 per-file waivers）、如何加新模块/目录。
- `CONTRIBUTING.md` + `docs/zh/CONTRIBUTING.md`：新增 "Boundary rules" / "边界规则" 一节（双语同步），含流程图式步骤与本地命令。
- `scripts/README.md`：更新 `check-python-static.mjs` 行、新增 `check-python-imports.mjs` 行、删除 `check-import-boundaries.mjs` 行。
- 4 个 PR 模板 checklist 各加一行："新增/移动 Python 模块、TS 目录或跨层依赖时，已更新 import-linter 契约 / eslint boundary 矩阵"（bug_fix 视情况，放在相关 section）。

### D11. CI 与本地命令

- `static-and-tests` job 在 `Lint` 后加 step `Python import boundaries: npm run check:python-imports`。
- `addon` job 的 `Python static gate` 仍跑 `check:python-static`（现在含范围准入）。
- `lint` 不追加 Python 命令（保持 Node-only，可在无 Python 环境跑）；Python 门禁由独立命令 + CI 保证。

## Risks / Trade-offs

- [开启 eslint-plugin-boundaries 后暴露未登记边] → 矩阵按实测预填；实际 findings 作为"显式允许边"加入同一变更；不允许按文件豁免；如发现某条边本身应删，留到后续 refactor，不在本次改行为。
- [ESLint 10 与插件 7.4.2 的 peer 只声明 >=6，未必实测兼容] → Phase 1 第一步安装后立即跑 `npx eslint src`；若运行时不兼容，停止并向用户报告，回退方案是排除插件改用自研规则（不在本次默认路径）。
- [repo-config-layout 并发改 package.json / CONTRIBUTING / architecture] → 在独立 worktree 从最新 main 开始；本变更对这些文件只做最小、互不重叠的行级修改；冲突时 rebase 重放。
- [lint-imports 的定位在 Windows/CI 不一致] → 解释器同目录 + PATH 两级发现；CI 步骤本身验证；失败信息指向 `npm run setup:dev`。
- [tracked 列表依赖 git] → CI/本地开发环境都有 git；脚本在 git 失败时报错而不是当成空集通过。
- [把 docs/examples 加进 ruff 后未来新增示例代码被误伤] → 这是预期行为（示例也是入库 Python）；若确实需要更松规则，应在 ruff 配置里按路径设规则（显式，不设 baseline）。
- [契约测试解析 TOML/JS 文本脆弱] → 只断言结构化不变量（切段后的键值、JSON.parse 的配置、tracked 路径前缀），不做全文 golden diff；配置格式若大改，测试失败即提醒开发者更新契约。
- [两个边界权威迁移期风险] → 每个 slice 都在旧的检查仍绿的前提下切一段，最后一次性删除旧脚本；任何时刻 CI 都至少有一个权威在跑。

## Migration Plan

按 slice 顺序落地，每个 slice 自带验证命令，前一个绿了再进下一个：

1. **Python 范围准入**：先写契约/单元测试（红），再改 `check-python-static.mjs`（ruff 根 + 断言 + `--scope-only` + test seam），最后 `npm run check:python-static` 绿。
2. **import-linter**：`requirements-dev.txt` → `pyproject.toml` 契约 → 新 wrapper → package.json/CI → 删旧脚本 Python 两段 → `npm run check:python-imports` 与 `check:finalization` 均绿。
3. **TS Phase 1**：装插件 → 写 elements/矩阵/client 块 → 跑 `npm run lint` 收集真实边并补齐 → 删 3 组旧 `no-restricted-imports` 与 `src→lib` 旧检查 → lint 绿。
4. **TS Phase 2**：加 runtime 块与矩阵 → lint 绿 → 删除旧脚本与所有引用 → `npm run check:finalization` 绿。
5. **收尾**：文档/PR 模板/README；跑 `npm run lint`、`check:python-static`、`check:python-imports`、`typecheck`、`test:contract`、`test:unit`；`openspec validate boundary-governance`；PR 填 AI 声明。

回滚：所有改动都是工具/CI/文档，无运行时行为；整体 revert 即恢复到当前状态。若只回滚某一 slice，保持"旧权威未删"或"新权威已关"的值相等即可。

## Open Questions

- 无阻塞性未知。两个后续项明确不在本次范围：`ui/panels → integrations` 是否收紧为查间接导入（需要 addon 导入链重构）；`src` 基础层循环是否重排为 DAG（需要行为重构）。它们都不改变本次的声明方式或任务拆分。
