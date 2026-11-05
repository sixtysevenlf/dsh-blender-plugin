# Design

## Context

See [proposal.md](proposal.md) for motivation, evidence F1–F4 and scope. This design covers the _how_.

审计基线（2026-11-05，基于已合并 main `7ed9ff1` / 已发布 v3.0.6）：

- 结构终态已达成：`npm run check:finalization` / `check:structure` / `check:exceptions` / `check-plan-ids --enforce` / `test:matrix` / `check:addon` / `check:build` / `test:parity` / `check:python-static` 全绿；计数 15 tools / 34 families / 228 ops / 38 registered commands（+ping=39 wire）/ 29 routes / protocol 11。
- 已有可复用的契约机器：`config/routing.json` → `scripts/gen-routes.mjs` 生成两张路由表；`config/contracts.json` 是 catalog ↔ Python 分派键的链接层，由 `scripts/catalog_dispatch_keys.py`（stdlib ast，不 import bpy）抽取，`tests/unit/js/contract/catalog-dispatch-parity.test.ts` 消费；`config/exceptions.json` 是唯一例外登记表（`scripts/check-exceptions.mjs`）；`config/doc-links-baseline.json` 是文档链接基线；`tests/BEHAVIOR_MATRIX.md` 由 `scripts/check-behavior-matrix.mjs` 校验。
- 约束：AGENTS.md 契约计数不得漂移；例外只能进 `config/exceptions.json`；不新增规则文件；行为修复必须 test-first；`tests/BEHAVIOR_MATRIX.md` 的 contract source 用 `file:symbol`，禁止行号。

## Goals / Non-Goals

**Goals:**

- F1 用最小行为改动让 `qc_compare_auto` / `qc_align_iou` 真正可分派，并让"advertised-but-undispatchable"这类漂移在门禁层不可能再出现。
- F2 把三类未入册例外变成带 owner/reviewBy 且被门禁消费的登记，而不是 prose。
- F3 把 47 个文件里的"从 X 拆出 / 行为冻结"叙事改成职责描述，addon 悬空注释一并修掉。
- F4 完成上一 change 的归档、旧 worktree/本地 checkout 清理、仓库外 evidence 索引收口。
- 全程保持 228 ops 等计数不变；F1 之外零行为变化。

**Non-Goals:**

- 不执行 `scripts-strictening-plan-v1`（F5，独立计划）。
- 不为了消除 D1 去重命名 `_polyhaven_*` / `_polypizza_*`（本次登记 + 门禁；改名留到真正触碰这些模块时）。
- 不改变 GUI E2E 策略（`tests/e2e/gui` 仍不在 PR CI），不新增真实 GUI 用例。
- 不 bump 版本、不改安装命令、不发布；只写 CHANGELOG 的 Unreleased/下一 patch 分节。
- 不把 `D:\DSH` 的计划/规格全文搬进仓库。

## Decisions

### D1 — F1 修复：补齐分派键，而不是改 catalog 名

- `runtime/py/render/qc_impl/api.py` 的 `qc_ops()` 增加 `"compare_auto": qc_compare_auto`（与既有 `"compare"` 同一实现；两者是历史命名，保留用户可见名）。
- 新增路径级薄封装 `qc_align_iou_op(ref_path, ref_box=None, render_path=None, render_box=None, pad_frac=0.05)`：走 `qc_load` → `qc_mask_auto` → 掩膜 bbox 紧裁 → `qc_align_iou`，只返回 JSON 安全的标量（`iou/inter/union/ref_px/render_px/scale`），丢弃 mask/canvas 数组；注册为 `"align_iou"`。
- 理由：catalog 是用户契约，228 个 op 名与计数不能减；`qc_align_iou` 函数本身已存在且 docstring 就是"对齐后算 IoU"，缺的是路径级入口。替代方案：(a) 从 catalog 删两个 op —— 计数与用户可见能力变化，否决；(b) `align_iou` 直接映射 `_align_search` —— 语义是"搜索对齐"而非 bbox 对齐，否决；(c) 继续用 `catalogExtras` 带 owner 声明 —— 用户可见缺陷仍在，否决。
- test-first：先把 `config/contracts.json` 的 qc `catalogExtras` 删掉，parity 测试应为红；补键后转绿；再加 `tests/e2e/blender/native/qc_dispatch.py` 端到端验证。

### D2 — 链接层语义：`catalogExtras` → `hostOps`

- `config/contracts.json` 删除 qc 的 `catalogExtras`；shape 家族现有的 `"search"` 从 `catalogExtras` 改名为 `hostOps`（带 reason：由 `runtime/core/plan-dispatch.ts` 在 dispatcher 前处理）。
- `tests/unit/js/contract/catalog-dispatch-parity.test.ts`：非 host-side 家族的每个 catalog op 必须映射到字面 Python 键；`hostOps` 必须 ⊆ catalog ops 且不出现在 Python 表；任何 `catalogExtras` 残留直接失败。
- 理由：`catalogExtras` 同时承载"host-side 合法例外"和"已知 bug 漂移"，语义含混才让 F1 藏了这么久；拆成显式 `hostOps` 后，"缺少分派键"只有一个出口——红。

### D3 — F1 的验证分层

- Unit/contract（PR CI）：parity 测试（红→绿）+ 既有 `routes-generated` 测试；不新增依赖。
- E2E（release 机器）：新增 `tests/e2e/blender/native/qc_dispatch.py`，用 Blender 自带 numpy 合成 128×128 RGB PNG（白色背景 + 黑色色块），经 `K.dsh_qc_api["dispatch"]` 调用 `compare_auto` 与 `align_iou`：断言 qc 路由非 `unknown qc op`、同图 IoU≈1、同高半宽窄图 IoU 明显更低（纯平移会被掩膜 bbox 紧裁对齐吃掉）；在 `tests/e2e/blender/native_manifest.py` 注册（id `qc_dispatch`，preloads `("qc", "imgtools")`，timeout 300）。
- `tests/BEHAVIOR_MATRIX.md` 的 `OP-qc-compare_auto` / `OP-qc-align_iou` 行：behavior 改为"dispatch 成功"；unit 指向 parity 测试；e2e 指向 native case；status 保持 covered（不再只靠 catalog 契约 + 租约）。
- 理由：pytest 环境没有 numpy/bpy（`requirements-dev.txt` 故意不含），在 unit 层 import qc_impl 会破坏现有依赖边界；真实行为只能在 Blender E2E 里诚实覆盖。PR CI 用 parity 守住"键存在"，release 用 E2E 守住"调用成功"。

### D4 — F2 例外有主化

- **(a) catalogExtras**：由 D1/D2 消除；shape 的 host-side op 走 `hostOps`，不占"例外"。
- **(b) doc-links baseline**：`config/doc-links-baseline.json` 增加顶层 `review: {owner, reviewBy, reason}`；`scripts/check-doc-paths.mjs` 在 `missingRefs` 非空时要求 review 存在、reason 非空、reviewBy 未过期，否则失败；`tests/unit/js/contract/doc-paths-gates.test.ts` 同步断言。7 条历史引用继续冻结（都是 v2.3 契约/MIGRATIONS 指向的历史路径），不删文档内容。
- **(c) D1 私有导入**：`config/exceptions.json` 新增 kind `python-private-import`；涉及 8 个 addon 文件（`commands/assets_polyhaven.py`、`commands/material.py`、`commands/polyhaven/{download,hdri,material,model}.py`、`commands/polypizza/{client,download}.py`），每个文件一条，`path` 为该文件，`detail` 列出该文件导入的 `_polyhaven_*` / `_polypizza_*` 名字及目标 `integrations/*_client` 模块（合计 41 对导入 / 22 个唯一名），owner `addon-integrations`，reviewBy 2027-06-30。新增 `tests/unit/python/test_private_import_contract.py`（纯 ast，不 import bpy）：扫描 `addon/dsh_blender_bridge/**` 中所有从 `integrations/*_client.py` 导入的 `_` 私有名的 `ImportFrom`，要求每一对 (file, name) 都被登记覆盖，且登记不得有 stale 项；期望计数从 `config/exceptions.json` 推导。`scripts/check-exceptions.mjs` 的 KINDS 增加该 kind；`docs/architecture.md` 的例外摘要表更新。
- 理由：AGENTS.md 规定 `config/exceptions.json` 是唯一 gate 例外数据源，不能为 D1 另开 registry；把"消费"放进 pytest 保证 kind 不是死数据。

### D5 — F3 注释去历史化

- 规则：文件/模块头第一行写"现在负责什么"；确需历史的，最多一短句 `History: split from <file> (<version>)`；删除 `T*/F*/B*/C*` 步骤叙事与"行为冻结"字样。覆盖 audit 统计的 41 个"从 X 拆出"、25 个"行为冻结"文件（并集 47）以及 `addon/dsh_blender_bridge/protocol.py` 顶部对 `addon_manager.py`/`trajectory` 的引用。
- 不新增机械门禁：自然语言误报成本高，且 800+ 计划 ID 已由 `check-plan-ids` 管住；约定写入 `docs/architecture.md`（不新增规则文件）。
- 保护：改动仅限注释；diff 审核用"非注释行零变化"自检，然后跑 typecheck/lint/format。

### D6 — F4 流程收尾

- 归档：`npx openspec archive converge-3-0-terminal-state --yes`（本 change 的 F4 最后执行），归档后 `openspec list` 只剩本 change；归档内容进 git。
- worktree/checkout：先 `git worktree list`，只对干净 worktree 执行 `git worktree remove`，最后 `git worktree prune`；`tmp/a2-before` 已 prunable 可直接 prune。本地 `git switch main && git pull --ff-only` 到 `7ed9ff1` 之后。
- igitignore：`.dsh/`、`.dsh-ops/` 是本地会话产物（当前 untracked），加入 `.gitignore`；不动 `docs/refactor/` 既有忽略。
- evidence 索引：`D:\DSH\refactor-workspace\docs\refactor\evidence\README.md` 补 48 目录清单，标注 17 个不完整目录为 `historical container` 或补齐 acceptance/commands/logs/sha256；T9/T10/T14 的 F4.4 重封状态写明。此项是操作项，不进 CI。
- 顺序：F4 放在本 change 的最后（先 F1→F2→F3 合并前完成代码与文档，再归档/清理），避免 archive 后路径引用失效。

### D7 — 版本与发布

- CHANGELOG 记入 Unreleased（或下一个 patch 分节）：`行为修复`（qc 两个 op 可分派）与 `治理`（例外登记/注释/归档）分开列；不改 `llms.txt`/README 的 3.0.6 安装命令，不 bump `package.json`。
- 理由：发布是用户决策；本 change 的完成判据是仓库终态，不是 tag。

## Risks / Trade-offs

- **`qc_align_iou_op` 的语义解释可能与历史意图不同** → 用 E2E 两个断言（同图≈1、同高半宽窄图明显低）锁住可观察行为；若产品语义另有定义，退路是把 op 改为映射 `_align_search`，但需先改 spec 场景。
- **`hostOps` 改造误伤 shape 家族** → fixture 测试同时覆盖"host-side 可声明"和"缺失键报错"两条路径。
- **E2E 不在 PR CI，PR 阶段只证明键存在** → 在 PR 说明里明确分层；release checklist 跑 native `qc_dispatch`；矩阵 e2e 列写明真机。
- **注释批量改写可能误改代码** → 只改注释行；typecheck/lint/format + `git diff` 非注释行检查。
- **归档提前会让后续引用断链** → F4 最后执行；归档后跑 `check-doc-paths` / `check-plan-ids` 再提交。
- **例外登记变垃圾桶** → 新 pytest 要求覆盖精确匹配（无 stale），`check-exceptions` 校验 owner/reviewBy/过期；`docs/architecture.md` 摘要同步。
- **worktree 清理是破坏性本地操作** → 只删干净项，先 list 再逐个 remove，prune 只清元数据；不碰用户未提交内容。

## Migration Plan

1. 从 `main`（含 `7ed9ff1`）开 `chore/terminal-state-closeout`。
2. F1：删 qc `catalogExtras`（红）→ 补 `compare_auto` / `align_iou` 分派与封装（绿）→ parity/matrix/E2E。
3. F2：contracts `hostOps`、doc baseline review、exceptions 8 条 + pytest 扫描器、architecture 摘要。
4. F3：注释与 addon 悬空引用；architecture 约定。
5. F4：归档上一 change、.gitignore、worktree/checkout、evidence README（操作项）。
6. 全量门禁 + `openspec validate --strict`；PR（refactor 模板 + AI 披露）；合并；发布由用户决定。
7. 回滚：每个切片独立 commit，可单独 revert；F1 回滚时同时恢复 contracts 的 qc `catalogExtras`（否则 parity 红），无数据迁移。

## Open Questions

无。版本/发布时间与 `scripts-strictening` 已明确为非目标或用户决策，不影响本设计、spec 与任务拆分。
