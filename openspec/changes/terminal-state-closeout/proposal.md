# Proposal

## Why

对 `converge-3-0-terminal-state` 的终态审计（2026-11-05，基于已合并的 main `7ed9ff1` / 已发布 v3.0.6）结论：**结构终态已达成**（§1.1 八条全部 PASS，门禁全绿、计数不变），但**完整终态还差 4 类收口，其中一项是真实用户可见缺陷**。

### Evidence（2026-11-05 审计，逐条可复现）

- **F1 行为：catalog 有两个 op 实际不可用。** `runtime/catalog/data.ts:101-102` 声明 `qc_compare_auto`、`qc_align_iou`；`config/routing.json` 的 qc 行（prefix `qc_`、strip 3、api `dsh_qc_api`）把它们切成 `compare_auto` / `align_iou`；但 `runtime/py/render/qc_impl/api.py:110-119` 的 `qc_ops()` 只有 `compare` / `align_search` / `align_rotate`，`runtime/core/kit.py:250-265` 的 `wrap_dispatch` 不做别名归一 → 实际返回 `unknown qc op`。`config/contracts.json:210-213` 把它声明为 "known pre-existing drift"，但无 owner、无 reviewBy、无 GitHub issue；`tests/BEHAVIOR_MATRIX.md` 对应两行标 covered，实际只覆盖 catalog 契约与写租约，不覆盖 dispatch 成功。**这正是"结构门禁全绿 ≠ 行为完整"的残留实例。**
- **F2 例外无主。** (a) 上述 catalogExtras 在 prose 中声明，未进 `config/exceptions.json`；(b) `config/doc-links-baseline.json` 有 7 条历史缺失引用（frozen v2.3 契约 5 条 + MIGRATIONS 2 条），由 `tests/unit/js/contract/doc-paths-gates.test.ts:74-85` 逐条断言，但 baseline 无 owner/review 日期；(c) 仓库外 debt ledger 的 D1：41 处私有名跨模块导入 / 8 文件 / 22 名字（全部 `_polyhaven_*` / `_polypizza_*`），台账自己写明 "No owner/expiry assigned yet"。三项都违反"例外必须有主"的原则，只是不在 §1.1.4 的枚举类型内。
- **F3 叙事残留。** 40 个 TS 文件仍以"从 X 拆出"开头（如 `runtime/catalog/logic.ts:1`、`runtime/core/dispatcher/plan-call.ts:1`），23 个文件含"行为冻结"；`addon/dsh_blender_bridge/protocol.py` 顶部注释仍引用不存在的 `addon_manager.py` / `trajectory`。`check-plan-ids` 只清 ID，清不到这类叙事。
- **F4 流程/工作区未收尾。** `converge-3-0-terminal-state` 已 Complete（41/41）但未 archive；本地注册 22 个旧 refactor worktree（含 1 个 prunable）；本地 checkout 落后 main 3 个提交；仓库外证据库 48 个目录中 17 个按 §1.3 四件套启发式缺项，且 `evidence/README.md` 只索引 11 个任务。

为什么现在：结构终态刚达成、3.0.6 刚发布，遗留项上下文最新，一次收口成本最低；F1 若不修，则"catalog 有 228 个 op"的对外声明在 2/228 的 op 上不成立，会持续误导用户与后续 agent；F2/F3/F4 不修则下一次审计仍只能判 PARTIAL。上一 change 已完成但未归档，正好在本 change 收尾时归档。

## What Changes

### F1 catalog ↔ dispatch 契约补齐（唯一行为变更）

- 先写失败测试：断言 catalog 声明的每个 `qc` 前缀 op 都能落到 `dsh_qc_api` 的真实分派键；现有 `qc_compare_auto`、`qc_align_iou` 在修复前为红。
- 二选一修复（design 定）：给 Python dispatch 表补等价入口，或以真实分派键修正 catalog/路由；保持 228 ops 计数与用户可见 op 名不减少（除非证明保留无意义）。
- 把 `tests/BEHAVIOR_MATRIX.md` 的 OP-qc-* 行从"catalog 契约 + 租约"升级为"dispatch 成功"断言；修复后从 `config/contracts.json` 移除 catalogExtras（漂移消失）。
- 新增通用 parity 测试：catalog 的每个 op 在其声明入口解析到存在的 API 键；worker/headless 入口与主入口对同一 op 使用一致的 API 与 preload，不支持时返回明确错误而非误路由。同类问题不再只能靠人工审计发现。

### F2 例外有主化（无行为变更）

- `config/exceptions.json` 增补三类未入册例外：catalogExtras（若 F1 后仍有）、doc-links baseline 的 7 条历史引用、debt ledger D1（41 私有名导入 / 8 文件 / 22 名）——每项 owner + reviewBy（默认 2027-06-30），或明确关联 GitHub issue；prose 声明不得代替登记。
- D1 二选一（design 定）：把 `_polyhaven_*` / `_polypizza_*` 提升为公共名并删除跨模块私有导入（首选，若工作量可控），或登记为带 owner/expiry 的例外并写清退出条件。

### F3 叙事去历史化（无行为变更）

- 改写 40 个"从 X 拆出"与 23 个"行为冻结"头注释为职责描述（例：`runtime/catalog/logic.ts:1`、`runtime/core/dispatcher/plan-call.ts:1`）；确需历史时用一句 History 说明，不再用拆出叙事代替职责。
- 修 `addon/dsh_blender_bridge/protocol.py` 顶部对不存在模块的引用。
- 在 `docs/architecture.md` 记录注释约定（职责优先）；不新增机械门禁，避免对"拆出"类词汇误报。

### F4 流程与工作区收尾（无行为变更）

- 归档 `converge-3-0-terminal-state`（归档后成为仓库历史）。
- 清理 22 个旧 refactor worktree（含 1 个 prunable）；同步本地 checkout 到 main；把 `.dsh/`、`.dsh-ops/` 本地会话产物加入 `.gitignore`（若确认不入库）。
- 刷新仓库外证据库索引 `D:\DSH\refactor-workspace\docs\refactor\evidence\README.md`：48 个目录 vs 只索引 11 个任务，补齐缺失的 acceptance/commands/logs/sha256 或标注 "historical container"；T9/T10/T14 的 F4.4 重封状态在 README 记明。此项为操作项，不改仓库代码。

### 非目标

- 不执行 `scripts-strictening-plan-v1`（P2，独立计划）。
- 不做行为之外的 UX/功能变更；15 tools / 34 families / 228 ops / 38 commands（+ping=39 wire）/ 29 routes / protocol 11 计数不变。
- 不把 `D:\DSH` 的计划/规格全文搬进仓库（只归档与例外登记）。
- 版本号与发布时间由用户决定；若随下个 patch 发布，CHANGELOG 需单列"行为修复"与"治理"。

## Capabilities

### New Capabilities

- `plan-op-dispatch`: catalog 是 plan op 的对外契约。本 capability 规定：凡 catalog 声明的 op，必须解析到真实存在的 Python API 分派键并返回结果；不可分派的 op 不得出现在 catalog/契约声明中；worker/headless 入口对同一 op 必须与主入口使用一致的 API 与 preload，不支持时返回明确错误而不是误路由。这是 F1 的行为契约，也是本 change 唯一的 spec 级变更。

### Modified Capabilities

无。项目此前没有 spec，F2–F4 不改变行为；未发明需求来满足校验。

## Impact

- **代码/契约**：`runtime/catalog/data.ts`、`config/routing.json`、`runtime/py/render/qc_impl/api.py`（或 `runtime/core/kit.py` 的分派归一）、`config/contracts.json`、`config/exceptions.json`、`config/doc-links-baseline.json`；新增/修改 `tests/unit/js/contract/*` 与 `tests/BEHAVIOR_MATRIX.md`；注释清理 63 个 TS 文件 + `addon/dsh_blender_bridge/protocol.py`。
- **文档**：`docs/architecture.md`（注释约定 + 例外摘要）、`CHANGELOG.md`（若发布）、必要时 `tests/MIGRATION.md`；中英双语入口保持同步。
- **流程**：归档上一 change；本地 worktree 与 checkout 清理；仓库外 evidence README 刷新（不进 CI）。
- **风险**：F1 涉及 Python qc 分派与 routing 两处，需用失败测试锁定；F2 扩大 `exceptions.json` 语义时需同步 `check-exceptions` 与文档；F4 的证据库不在 CI 覆盖内，只能人工核对。
