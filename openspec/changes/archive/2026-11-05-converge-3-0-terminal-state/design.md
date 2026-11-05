# Design

## Context

本 change 的动机与范围见 [proposal.md](proposal.md)。设计只回答"怎么收敛"，并记录三个不能从代码里推断的前提：

1. **重构已完成，终态定义在仓库外**。v3.0.0–v3.0.5 的终态条款写在 `D:\DSH\refactor-workspace\docs\refactor\refactor-finalization-plan-v1.md` §1.1–1.3，证据与债务台账也在那里。按用户决定 **A**，条款与决策迁入本 change，`D:\DSH` 只保留证据与已执行计划。
2. **结构门禁已全绿，但这不等于行为完整**。2026-11-04 的首调修复（commit `ad0b245`，随 3.0.6 发布）发现 `GET /status` 未透出 `addonError`，拆分期写好的 presenter 判定在生产中不可达——`check-structure`（83 文件 / 330 边 / 0 环）对此完全无感。本 change 新增的门禁必须以"行为契约"而非"文件形状"为对象。
3. **顺序与发布约束**：`fix/blender-exe-diagnostics` 已就绪（28 文件 / +1579 −121），先合并到 `main` 但**不发布**；本 change 从合并后的 `main` 开分支，完成后与修复一起作为 **3.0.6** 发布（2026-11-05 用户决定，取代此前"治理 = 3.0.7"的口径）。该分支已改动 `runtime/config.ts`、`runtime/http/routes/health.ts`、`docs/操作教程.md`、`tests/BEHAVIOR_MATRIX.md`。

仓库约束（来自 AGENTS.md，设计不得违反）：契约计数锁定 **15 tools / 34 families / 228 ops / 38 addon commands / 29 routes / protocol 11**；一次只迁一个模块或边界、移动代码不改行为、旧文件同变更删除；不新增规则文件；测试与文档随变更更新，双语文档同步。

## Goals / Non-Goals

**Goals:**

- 每个可被引用的"真源"只有一个：op 目录、路由表、命令数、例外登记。
- 结构门禁与行为契约门禁分离：前者证明"拆得动"，后者证明"拆得对"。
- 让下一次改动不必先理解重构历史：代码注释描述职责，不描述"从哪拆出"。
- 把终态条款、例外登记、迁移决策写进仓库，使本次 change 归档后即成为历史。

**Non-Goals:**

- 不改任何用户可见行为（唯一的例外是已确认的路由缺陷，见 D3，按 bug fix 独立 PR）。
- 不做全量重构式的"顺手清理"：只处理 proposal S1–S4 列举的已知项。
- 不把 `D:\DSH` 的计划/规格全文搬进仓库，也不重建计划文档体系。
- 不追求"函数都短"：105 个 80–120 行函数中，只拆有内聚收益的。
- 不处理 `scripts-strictening-plan-v1` 的 P2、Steam 注册表/非枚举库根。

## Decisions

### D0 终态条款（迁入仓库）

以下条款来自 finalization plan §1.1–1.3，保留原编号以便与 `D:\DSH` 证据对照；"当前裁定"是本次审计的结论。

| 条款 | 内容 | 当前裁定 |
|---|---|---|
| §1.1.1 | 无扁平的 runtime/*.py 过渡 shim | 已闭合（runtime 根 0 个 .py） |
| §1.1.2 | 仅 `src/index.ts` 与 `runtime/engine.ts` 两个公共 facade；其余"中间层"只能是单行再导出 | 未闭合：engine.ts 87 导出仅 32 个被外部使用；`src/host/backend/port-holders.ts`、`protocol.py` 三导出、`engine-api/frame.ts` 转发层属残留 |
| §1.1.3 | 同一能力没有第二条路径 | 主要闭合；遗留两处：`qc_impl/api.py` 的 `exec(compile(...))` 现场加载、addon 凭据在 scene props 与 prefs 存两份 |
| §1.1.4 | 无"无主例外" | 未闭合：`runtime/catalog.ts` 已不存在，但 architecture.md、check-structure、coverage-baseline 仍引用；105 个函数落在 80–120 行软带而白名单为空 |
| §1.1.5 | 函数预算（硬 ≤120 行；80–120 需理由） | 硬约束闭合（>120 = 0）；软带未登记 |
| §1.1.6 | 模块预算（TS ≤600 行；py ≤1000 行） | 闭合（284/284、167/167） |
| §1.1.7 | 依赖方向单向、无环 | 闭合（0 import cycle；py 0 跨域；src 只经 runtime-bridge） |
| §1.1.8 | 每个计数只有一个真源 | 未闭合：catalog 与 modules.json / py dispatch 无链接；命令 38 vs 39；handshake 硬编码 16 |
| §1.2 | 测试终态：无恒绿、无自证、无 skip 掩盖 | 部分：`protocol.X == registry.X` 为自证；op 契约只做前缀子串匹配 |
| §1.3 | 文档终态：无历史叙事、无死链、无重复 | 未闭合（proposal S4） |

设计原则：这些条款是**验收口径**，不是要新写的规则文件——最终落在 `docs/architecture.md` 一节 + 本次 change 的 tasks 验收里。

### D1 叙事归属（决定 A）

日常修复 → commit message + CHANGELOG（`ad0b245` 证明足够）；跨切面清理 → 本 change；终态条款、例外登记、迁移决策 → `docs/architecture.md` + 本 change 归档。

替代方案：B 只在仓库外维护 → 被否决，因为代码目录里的"F1.1/T6/B9"对任何新人都不可解，且外部文档无法作为验收依据。C 只搬进 architecture.md → 部分采纳（终态条款与例外登记），但清理过程需要 tasks 的可执行性，归 OpenSpec。

### D2 op 目录与 Python 派发的真源

**决定**：catalog（`runtime/catalog/data.ts`）保留为人读的目录（when/not/sk/key 是作者写的，不可生成）；新增一层**可校验链接**：

- 新增 `config/contracts.json`（或复用现有 config/ 模式）登记：family → prefix → py api 模块 → 规范 op 列表来源；
- 测试用 Python `ast` 解析各域 `dispatch.py` / `*_api.py` 中的字面 dict 键（不 import、不依赖 bpy），与 catalog 的 `ops[]` 求差集；
- 差集非空即失败；别名（如 `render_views` / `qc_render_views`）必须在登记表里显式声明。

**为什么不用 codegen 反向生成 catalog**：catalog 的 ops 是小写短名（`scan`、`apply`），与实际 dispatch 键有前缀/别名差异；生成会把别名实现细节泄漏进用户文档。codegen 只用于路由表（D3）。

替代方案：只在 JS 侧断言 family 前缀（现状的做法）→ 被否决，重命名 py op 仍会静默通过。

### D3 路由表单一来源

**决定**：新建单一数据源 `config/routing.json`，字段 `{prefix, strip, api, preload, timeoutMs, side: gui|worker|both}`，由 `scripts/gen-routes.mjs` 生成：

- `src/plan/routes.generated.ts`（host/worker 侧）
- `runtime/core/dispatcher/routes.generated.ts`（GUI 侧，含 `ensure` 映射）

生成文件带 header 与 hash；`check:routes` 断言生成物与源一致（等同 `sync_counts` 模式：改源 → 重生成 → 提交）。

**已确认缺陷并入此决策**：worker 侧 `qc_` 前缀统一路由到 `dsh_qc_api`，而 GUI 侧 `qc_render_*` 路由到 `dsh_qc_render_api` + `preload=['qc','qc_render']`。已核实 `dsh_qc_api` 的 dispatch 表里 `render_views` 是转接函数，依赖 `K.dsh_qc_render_api` 或旧扁平路径的 `exec` 兜底；worker 只 preload `qc,imgtools` 时该兜底不可用 → **返回错误而非静默错误结果**。修复：worker 路由对 `qc_render_*` 改为 `dsh_qc_render_api` + preload `qc_render`。这是行为变更（从报错到可用），按 **独立 bug-fix PR（test-first）** 先行合并，不夹带在清理 PR 里；修复后用 D3 的 parity 测试锁死。

**alternative**：三张表继续各自维护 + parity 测试 → 否决，因为本次审计正是三表漂移（24 / 21 / 特殊分支）的结果。

### D4 `runtime/engine.ts` 收敛

**决定**：以"仓库内实际消费者"为准裁剪公开面（check-build、check-parity、install-smoke、`catalog/logic.ts`、16 个测试文件是既有消费者清单）：

- 保留被 `lib/runtime/engine.js` 外部路径导入的名字（约 20 个）；
- 删除 55 个 facade-dead 导出，尤其整块 38 个 py `*_PATH` 常量（内部已从 `../paths.js` 直接导入）；
- `check-no-shims` 扩展到 facade 登记：任何公共 facade 的导出必须出现在登记表里，新增需说明消费者；
- 删除不在登记表里的 `*_PATH` 导出后，若测试/脚本仍导入则视为消费者 → 先迁到直接模块再删。

替代方案：整体删除 engine.ts → 否决（finalization §1.1.2 明确它是两个允许的公共 facade 之一；check-build/parity 也依赖它）。

### D5 单行再导出与 `runtime/config.ts` 缝

**决定**：`runtime/config.ts:20-21` 的 `import/export { detectBlenderExe }` 删除；测试改为直接 import `runtime/blender-detect.js`。规则写入例外登记：单行再导出只允许两类——(a) 公共 facade 的稳定入口；(b) 同一次变更内的过渡，且必须登记 review 期限。`config.ts` 不符合，删除。

### D6 addon 命令真源

**决定**：以 `addon/dsh_blender_bridge/registry.py` 的 `COMMAND_NAMES` 为唯一真源（38 注册 + ping 线 = 39，spec 与 AGENTS 的 38/39 差异按此口径解释并写清）。

- handshake 的 capabilities 从 registry 派生，删除硬编码 16；
- `protocol.py` 只保留 `PAUSE_SAFE_COMMANDS` 的实际使用，删除 3 个无人消费的再导出；
- host 侧 `runtime/catalog/commands.ts` 与 registry 的 parity 改为双向（现为单向）；
- 断言 `protocol.X == registry.X` 的测试改为"dispatch 表键来自 registry"的真断言。

### D7 例外登记表

**决定**：`config/exceptions.json`（机器可读）+ `docs/architecture.md` 一节（人读摘要）。条目字段：`{id, kind, path, reason, owner, reviewBy}`。现有三处例外源合并进来：`check-structure.mjs` 的 `BUDGET_EXEMPT`、`config/coverage-baseline.json` 的 stale key、python 静态门 allowlist。新增 `check-exceptions.mjs`：path 必须存在、reviewBy 过期即失败、kind 必须来自枚举。

替代方案：继续分散在各 gate 脚本 → 否决（幽灵 `runtime/catalog.ts` 就是分散的直接后果）。注意 AGENTS.md 禁止的是"新规则文件"，`config/*.json` 是门禁数据文件，不是规则文档。

### D8 E2 收税点

**决定**：两处并行处理，先测量再改预算。

- `src/host/backend/index.ts` 的启动探测改为不阻塞串行队列（后台推进 + 首次使用等待），或与 `lifecycle` 的 diagnose 去重；
- 用 `tests/integration/js/doctor-bounded.test.ts` 记录改造前后耗时；
- 预算恢复为报告建议的 `/status 5s、doctor 4s`（先改代码后调预算，不许为省事保留 3.5/3）。

### D9 死代码与陈旧产物

**决定**：按审计清单删除，每个删除点先 grep 消费者（含 tests/scripts/docs），同变更删测试；不保留 `@deprecated` 兼容层。清单：5 个未用导出、`__unusedAsRecord`、重复 `ReceiptContext`、`import_model_files` 链、`compat.py`、`core/render-state.ts`、`package.json` 的 `runtime/ext/pkg.tgz` 条目、85 个 `.pyc`（gitignored，仅工作区清理）。

### D10 文档终端态

**决定**：

- 3 篇被取代文档移入 `docs/archive/`（保留 git 历史与可追溯性），并在 `docs/archive/README.md` 写替代者；
- `SKILL.md` 重编号（3→9 的断号按 4–8 编号）；`曲面算法` 收敛为**单一文件**：以 `skills/blender-modeling/references/曲面算法.md` 为真源（它随 skill 分发），删除 `docs/曲面算法.md`，更新 4 处入链（`docs/zh/README.md`、`docs/操作教程.md`、`CHANGELOG` 历史条目不动）；
- 用户可见的 MCP / Claude 文案改为 Bridge；**保留**上游署名与许可证文本（`config.py` 的 `BLENDERMCP_*` env 兼容、Hyper3D/FAL 归因）；
- 计划编号：代码注释改写为职责描述；新增 `check-plan-ids`（ID 形状 token 只允许出现在 CHANGELOG、archive、`docs/architecture.md` 的历史说明里）；
- `check-doc-paths` 扩展：链接目标存在 + 非 archive 文档至少一个入链 + 禁止引用不存在路径（`runtime/catalog.ts` 类）。**入链要求适用于中英对应文件**：每个非 archive 文档（含 `docs/zh/` 与英文侧）都必须至少有一个入链，中英对应文件互指计入；
- `BEHAVIOR_MATRIX` 的行号引用改为"文件 + 符号名"，门禁用 grep 校验符号存在。

### D11 软带函数与预算

**决定**：105 个 80–120 行函数不整体拆分。例外登记中逐类登记理由（分派表、线性动画/几何流水线、生成式目录）；只拆分内聚上真正独立的部分（如可纯函数化的几何计算）。门槛不降，白名单必须有 reviewBy。

### D12 切片与 PR 边界

**决定**：S1–S4 各一个 PR（S2/S3 可按 D4、D9 拆成两个），加一个独立的 qc 路由 bug-fix PR。每个 PR：先写失败测试（契约/parity 类）→ 改代码 → 全门禁 → CHANGELOG 条目 → 双语文档。计数若有变化必须在 PR 描述里声明并同步 `sync_counts`。

### D13 版本与流程口径（已定）

- **单次 3.0.6 = 修复（`fix/blender-exe-diagnostics`）+ 本 change 的治理**；不再使用 3.0.7（2026-11-05 用户决定）。
- 流程：推送修复分支 → 合并到 `main`（**不发布**）→ 从 `main` 开治理分支 → 完成 tasks → 推送并开 PR → 合并到 `main` → 发布 3.0.6。
- CHANGELOG 分节：缺陷修复 / 治理（无行为变化） / 开发环境脚本；`runtime/engine.ts` 公开面变化在治理节声明。
- `check-doc-paths` 的入链要求适用于中英对应文件（同日决定，见 D10）。

### D14 开发环境一键配置

**决定**：新增 `scripts/setup-dev.mjs`（Node 24 已是最低要求，天然跨平台），由 `npm run setup:dev` 调用：

- 发现 Python：`PYTHON` env → `python` → `python3` → Windows `py -3`；失败给出可执行提示；
- 创建/复用 `.venv`（`--force` 重建），安装 `requirements-dev.txt` + `requirements-typecheck.txt`（fake-bpy stubs 供 pyright）；
- 检查 Node 24 与 `node_modules` 状态，必要时执行 `npm ci --legacy-peer-deps`；
- `--check` 只验证不写，输出 `PYTHON` 用法与下一步命令；重复运行幂等；失败非零退出并给修复建议。

**CI 单一来源**：`.github/workflows/pr.yml:88-96` 与 `release.yml:63-69` 的内联 venv 步骤改为调用该脚本（本地与 CI 同一实现，避免两处漂移）。替代方案：shell 脚本（Windows 不通用）、Python 脚本（配置环境前先要有 Python，失败模式更糟）均被否决。

**文档**：`CONTRIBUTING.md` 安装命令收敛为 `npm run setup:dev`，`README.md` 与 `docs/zh/` 同步；`.venv` 与 `node_modules` 仍不入库。

## Risks / Trade-offs

- **[与 fix 分支冲突]** → 顺序硬约束：修复先合并到 `main`（不发布），再从 `main` 开治理分支；S4 触碰 `docs/操作教程.md` 与 `BEHAVIOR_MATRIX` 的 PR 放在 S1/S2 之后。
- **[单次发布范围变大]** → 3.0.6 同时含缺陷修复、治理与 dev 脚本：发布说明分节，回滚按 PR 粒度；setup 脚本与治理代码无耦合，可独立 revert。
- **[engine.ts 删除导出破坏外部使用者]** → 仓库内消费者清单已枚举（check-build、parity、install-smoke、16 测试文件）；仓库外第三方未知，故在 CHANGELOG 标注可能影响并以补丁版本发布（3.0.6），不做 major。
- **[codegen 路由表增加构建步骤]** → 生成物带 hash 并由 `check:routes` 校验；`npm run build` 中插入生成，失败即中止；替代方案（parity-only）留给 D3 的 open question 之外的场景。
- **[Python AST 解析脆弱]**（dispatch 表由动态构造时解析会漏） → 先跑一次扫描确认所有表为字面 dict；解析不到的表进入例外登记并标注原因，不许静默跳过。
- **[文档门禁误报]** → 新门禁先在 `--warn` 模式跑一个 PR 收集存量，再转 enforced；archive/CHANGELOG 明确豁免。
- **[addon 凭据单一所有者动 UI 面板]** → 先补 headless/命令级测试，面板走真机 E2E；无法覆盖的分支登记为 exception 而非跳过。
- **[105 函数登记变成垃圾桶]** → 每条必须写理由 + reviewBy；`check-exceptions` 过期失败。

## Migration Plan

1. **前置（不在本 change 内）**：推送 `fix/blender-exe-diagnostics` 并合并到 `main`（**不发布**）；确认 `main` 上全门禁绿。
2. **P0 缺陷修复（独立 PR）**：qc_render worker 路由 → 先写失败测试（worker 路径调用应为 dsh_qc_render_api），修复，合并。行为变更在 CHANGELOG 声明。
3. **S1**：`config/exceptions.json` + `check-exceptions`；`config/routing.json` + codegen + `check:routes`；catalog/dispatch parity 测试；addon 命令真源与 handshake 修正。
4. **S2**：engine.ts 收敛 + facade 登记；删除 port-holders / protocol 三导出 / frame 转发 / exec 二次加载；合并 recovery 两层；凭据单一所有者；E2 探测与 diagnose 去重后回调预算。
5. **S3**：死代码删除（D9 清单）；软带函数登记（D11）。
6. **S4**：文档归档、重编号、曲面算法单源、MCP/Claude 文案、计划编号清理、`check-plan-ids` 与文档门禁转 enforced、matrix 符号化。
7. **S5**：`scripts/setup-dev.mjs` + `npm run setup:dev`；两个 workflow 改为复用；文档安装命令收敛为单一命令。
8. **收尾**：`node scripts/sync_counts.mjs`（若计数不变则仅确认）→ 全门禁 → CHANGELOG 3.0.6（分节）→ 推送治理分支、开 PR、合并 `main`、发布 3.0.6 → 归档 change。

**Rollback**：每个 PR 独立可 revert（无数据迁移、无 schema 变更）；codegen 与门禁同 PR 加入，revert 即回到旧表；engine.ts 若需回退，恢复导出清单即可（导出是纯转发）。文档归档可 `git mv` 回原路径。

