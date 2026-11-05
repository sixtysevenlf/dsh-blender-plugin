# Proposal

## Why

项目在 v3.0.0–v3.0.5 完成了一次大规模重构，但**终态从未在仓库内落地**：计划、规格、门禁、证据、债务台账只存在于仓库外的 `D:\DSH`（其中 spec.md 自我声明 "local working draft, not shipped … not OpenSpec; OpenSpec adoption is future work"）。仓库里留下 800+ 个无词表可查的重构计划编号（`F1.1`/`T6`/`B9`…，代码注释用"从哪拆出"代替职责描述），以及 28 个文件里的"行为冻结 / shim / 中间态"叙事。更关键的是：**结构门禁全绿不等于行为完整**。

### Evidence（来自 2026-11-04 的首调修复，commit `ad0b245`，随 3.0.6 发布）

- **E1 生产死代码**：`GET /status` 未透出 `addonError`，拆分期写好的 presenter 判定在生产中不可达 → `doctor` 长期对用户报 `ok:true`（修复后在 `runtime/http/routes/health.ts` 留下注释说明）。当时 `check-structure` 仍全绿（83 文件 / 330 边 / 0 环）——结构门禁只证明"拆得动"，不证明"拆得对"。
- **E2 遗留层收税**：修复报告 §8.3 记录，预算只能收紧到 `/status 3.5s、doctor 3s`（报告建议 3–5s），因为 `host.ts` 的未 await 启动探测（占用串行 addon 队列头最多 3s）与 lifecycle 失败后 diagnose（≈2.5s）吃掉了窗口；清理这两处后可回调 5s/4s。
- **E3 叙事离仓是稳定习惯**：3.0.5（`__file__`）与 3.0.6（首调）两次高质量报告都只存在于 `D:\DSH`；仓库里只有 commit message + CHANGELOG。对日常修复来说够用（`ad0b245` 的 commit message 与 AI 披露质量高），但**跨切面、多轮、有终态定义的清理**缺少可被后续轮次引用的结构。
- **E4 开发环境不能一键复现**：`.venv` 不入库（这是对的），但仓库没有一键开发环境入口；venv 的创建与依赖安装只存在于 `.github/workflows/pr.yml:88-96` 与 `release.yml:63-69` 的内联步骤里，换设备需要手工还原（2026-11-05 用户在另一台设备上发现）。

为什么是现在：`fix/blender-exe-diagnostics` 已就绪（28 文件 / +1579 −121），先合并到 `main` 但**不发布**；在它合并前开清理分支会直接冲突（`runtime/config.ts`、`runtime/http/routes/health.ts`、`docs/操作教程.md`、`tests/BEHAVIOR_MATRIX.md`）。合并后从 `main` 开分支完成本 change，与修复一起作为 **3.0.6** 发布（2026-11-05 用户决定）。

## What Changes

行为中性：契约计数保持 **15 tools / 34 families / 228 ops / 38 addon commands / 29 routes / protocol 11** 不变；每个切片一个 PR，移动代码不改行为，旧文件在同一变更内删除。

### S1 契约网（防"全绿但不对"）

- catalog（`runtime/catalog/data.ts`）↔ `runtime/py/modules.json` ↔ Python dispatch 表三方的 parity 测试。
- 路由表的单一来源或 parity：`src/plan/headless-target.ts`（手维护 24 行 + 8 别名）、`runtime/core/dispatcher/ext-routing.ts`、`runtime/core/dispatcher/plan-call.ts`，覆盖 `pipe_`、`calib_`、`qc_render_*`（后者当前在 worker 路径上路由到错误的 API 模块，需先确认影响再定处理）。
- addon 命令数的唯一真源（38 注册命令 + ping 线 = 39），消除 handshake 里硬编码的 16 名 capabilities；同时修掉 `protocol.X == registry.X` 的自证测试。

### S2 facade / 双入口收口

- `runtime/engine.ts` 从 87 个导出收敛到约 20 个（含整块 38 个 Python `*_PATH` 常量），删 `src/host/backend/port-holders.ts`、`addon/dsh_blender_bridge/protocol.py` 的 3 个无人消费再导出、`runtime/core/engine-api/frame.ts` 的转发层；合并 `recovery` 两层；addon 凭据单一所有者；去掉 `qc_impl/api.py` 的 `exec(compile(...))` 二次加载。
- 摘掉 E2 的串行税（启动探测 / 失败后 diagnose 去重或移出队列），随后把 `/status`、`doctor` 预算回调到 5s / 4s。

### S3 死代码与未登记例外

- 删除：5 个未用导出、`__unusedAsRecord`、重复的 `ReceiptContext`、`import_model_files` 链、`compat.py`、`core/render-state.ts`、`pkg.tgz` 打包条目、85 个陈旧 `.pyc`。
- 用**例外登记表**替代幽灵条目 `runtime/catalog.ts`（`docs/architecture.md:134`、`scripts/check-structure.mjs:12,268,273`、`config/coverage-baseline.json:40` 仍引用一个不存在的文件）；登记"单行再导出"判据（如 `runtime/config.ts:20-21` 的 `detectBlenderExe` re-export）；105 个 80–120 行函数登记理由或拆分。

### S4 文档与叙事

- 移除 800+ 重构计划编号（保留一处历史转述）；归档 3 篇已被取代的文档；修 `docs/architecture.md` 的 contracts / catalog 节；为 `skills/blender-modeling/SKILL.md` 重编号（3→9→12）；`曲面算法` 收敛为单一来源；用户可见的 MCP / Claude 文案改为 Bridge；加文档入链与路径存在性门禁；修正 `BEHAVIOR_MATRIX` 行号漂移（§8.5），让门禁校验引用而不只是列内容。

### S5 开发环境一键配置

- 新增 `scripts/setup-dev.mjs` 与 `npm run setup:dev`：发现 Python（`PYTHON` → `python` → `python3` → Windows `py -3`）、创建/复用 `.venv`、安装 `requirements-dev.txt` + `requirements-typecheck.txt`（fake-bpy stubs）、复用/引导 `npm ci --legacy-peer-deps`；支持 `--check`（只验证）与 `--force`（重建），幂等、失败非零退出并给修复提示。
- **CI 与本地共用同一脚本**：`.github/workflows/pr.yml` 与 `release.yml` 的内联 venv 步骤改为调用该脚本，消除两处漂移。
- 文档收敛：`CONTRIBUTING.md`、`README.md`、`docs/zh/CONTRIBUTING.md` 的安装命令改为一条 `npm run setup:dev`；`.venv` 与 `node_modules` 仍不入库。

### 跨切面

- **叙事进仓库（A）**：终态条款与决策由本 change 的 `proposal` / `design` / `tasks` 承载，归档后成为仓库历史；`D:\DSH` 只保留证据与已执行计划（不搬全文、不搬计划过程）。日常修复仍走 commit message + CHANGELOG；终态不变量与例外登记仍进 `docs/architecture.md`（遵守 AGENTS.md "不新增规则文件"）。
- 债务台账收口：`process-debt.md`、`spec.md`、`debt-ledger` 中的过期条目（D4 patch release 已发布、process-debt 声称无远程 CI 等）标记为已关闭或删除。

### 完成判据

- 新增的 parity / 契约测试在修复前为红、修复后为绿（E1 式回归：先写会失败的测试）。
- `check-no-shims` 覆盖 facade 与中间层（不止 3 个已退役文件），`check-doc-paths` 覆盖文档入链与不存在的路径（含 `runtime/catalog.ts`）。
- 全量门禁绿：typecheck、unit/integration/contract、addon unit、python 静态门、build/parity/matrix；计数不变。
- 行为无变化由测试与真机冒烟证明；发现的真实行为缺陷另开 bug fix，不夹带。
- 干净环境（删掉 `.venv` / `node_modules` 后）`npm run setup:dev` 一条命令可复现 typecheck 与单测所需环境，CI 与本地共用同一实现。

## Capabilities

### New Capabilities

无。本 change 是纯重构 / 治理 / 文档，没有规格级行为变更，不新增 capability。

### Modified Capabilities

无。`openspec list --specs` 为空，项目尚未建立 spec。

（因此 `.openspec.yaml` 置 `skip_specs: true`；未发明需求来满足校验。）

## Impact

- **代码**：`runtime/engine.ts`、`runtime/core/*`、`runtime/execution/*`、`runtime/http/*`、`src/host/*`、`src/plan/*`、`addon/dsh_blender_bridge/*`（删除中间层 / 死代码，不改行为）；新增 parity、契约与文档门禁测试。
- **文档**：`docs/architecture.md`、`AGENTS.md`（仅措辞）、`tests/BEHAVIOR_MATRIX.md`、`skills/blender-modeling/SKILL.md`、28 个含历史叙事文件、3 篇归档；`docs/zh` 与英文侧同步。
- **开发环境**：新增 `scripts/setup-dev.mjs` 与 `npm run setup:dev`；`.github/workflows/pr.yml`、`release.yml` 改用它；`CONTRIBUTING.md`、`README.md`、`docs/zh/CONTRIBUTING.md` 的安装说明收敛为单一命令；`.venv` / `node_modules` 仍不入库。
- **合同**：计数不变；新增"例外登记表"与"单行再导出"判据；`test:parity` / `check:finalization` 增加覆盖。
- **风险**：与 `fix/blender-exe-diagnostics` 的文件重叠（见 Why），必须先把修复合并到 `main`（不发布）再从 `main` 开治理分支；`qc_render_*` worker 路由疑似真实缺陷，需先定性质（若为缺陷则独立修复 PR）。
- **非目标**：`scripts-strictening-plan-v1` 的 P2（独立计划）；任何行为变更；Steam 注册表 / 非枚举库根的小修复（可独立 PR）；将 `D:\DSH` 的 plan/spec 全文搬进仓库（只搬终态不变量与例外登记）；不引入 devcontainer / 容器化开发环境，不把 venv 或依赖产物入库。
