# Tasks

## 1. 前置与基线

- [x] 1.1 把 `fix/blender-exe-diagnostics` 推送到仓库并合并到 `main`（此步**不发布**）；验证：`origin/main` 含修复提交（或 squash 后等价提交），`main` 上 `npm test` 与 `npm run check:structure` 全绿，且尚未打 tag / 发布（CHANGELOG 的 v3.0.6 节可先存在但不得标记为已发布）。
- [x] 1.2 从合并后的 `main` 开治理分支，把 `openspec/`（config.yaml + 本 change）纳入版本控制；验证：`git status` 仅存在预期改动，`openspec validate converge-3-0-terminal-state` 仍通过。
- [x] 1.3 记录基线：保存 `typecheck`、`test:unit`、`test:contract`、`test:integration`、`check:parity`、`test:matrix` 的当前输出到 PR 描述；验证：基线可复现（同一 commit 二次运行结果一致）。

## 2. P0 缺陷修复：qc_render worker 路由（独立 PR，先于 S1）

- [x] 2.1 写失败测试：`planHeadlessTarget('qc_render_views')` 应返回 `{api:'dsh_qc_render_api', sub:'render_views'}` 且 preload 含 `qc_render`；验证：测试当前为红（对照 `src/plan/headless-target.ts` 的 `qc_` 行与 runtime `plan-call.ts` 的 `handleQcRender`）。
- [x] 2.2 修复 worker 路由（`qc_render_*` → `dsh_qc_render_api` + preload `qc_render`），保留 GUI 侧行为不变；验证：2.1 的测试转绿，全量 `test:unit` 无新增失败。
- [x] 2.3 在 CHANGELOG 的 Unreleased 记录行为变更（worker 下 `qc_render_*` 从报错变为可用）并在 PR 描述声明这是本 change 唯一的行为变更；验证：README/操作教程无需改（GUI 路径不变），CHANGELOG diff 存在。

## 3. S1 契约网

- [ ] 3.1 新增 `config/exceptions.json` 与 `scripts/check-exceptions.mjs`（path 必须存在、reviewBy 过期失败、kind 枚举），接入 `npm run lint`；验证：故意写入一条不存在的 path 时脚本失败，删除后通过。
- [ ] 3.2 合并三处分散例外源（`check-structure.mjs` 的 `BUDGET_EXEMPT`、`config/coverage-baseline.json` 残留的 `runtime/catalog.ts` 键、python 静态门 allowlist）到 3.1 的登记表；验证：`check:structure`、`test:contract`、`check:python-static` 全绿，且 `grep -r "runtime/catalog\.ts" --include=*.md --include=*.mjs --include=*.json` 只剩历史说明。
- [ ] 3.3 新增 `config/routing.json` 单一路由表、`scripts/gen-routes.mjs` 与两个 generated 文件，接入 build 与 `check:routes`；替换 `src/plan/headless-target.ts` 与 `runtime/core/dispatcher/ext-routing.ts`/`plan-call.ts` 的硬编码表；验证：生成物 hash 校验通过，删除一行源数据后 `check:routes` 失败。
- [ ] 3.4 新增 catalog ↔ Python dispatch parity 测试（`ast` 解析字面 dict 键），族→模块映射来自 3.3 的 routing 表；验证：故意改名一个 py op 时测试为红，恢复后为绿。
- [ ] 3.5 addon 命令真源：handshake capabilities 由 `registry.COMMAND_NAMES` 派生（删除硬编码 16）、`protocol.py` 删除 3 个无人消费再导出、host `runtime/catalog/commands.ts` parity 改为双向；验证：`npm run test:addon-unit` 与 python 侧 `test_addon_command_registry.py` 真断言通过，`sync_counts` 后命令数仍为 38/39 口径。
- [ ] 3.6 更新 `tests/BEHAVIOR_MATRIX.md` 与四把锁（`check-behavior-matrix.mjs`、python dispatch 测试、E2E、matrix doc）中的命令数口径；验证：`npm run test:matrix` 通过。
- [ ] 3.7 更新 `docs/architecture.md` 的契约/路由/例外章节与 `AGENTS.md` 的计数口径说明（38 注册 + ping 线 = 39）；验证：`check:doc-paths` 通过，人工抽查两处链接可点。

## 4. S2 facade 与双入口收口

- [ ] 4.1 生成 engine.ts 消费者清单（check-build、check-parity、install-smoke、`catalog/logic.ts`、测试文件）并冻结为清单文件；验证：清单与 `grep -rn "runtime/engine" tests scripts src runtime` 结果一致，约 32 个名字。
- [ ] 4.2 按 4.1 裁剪 `runtime/engine.ts`（87 → ~20 导出，删除 38 个 py `*_PATH` 与其余 facade-dead），内部导入改直接模块；验证：`npm run typecheck`、`check:build`、`check:parity`、install-smoke 全绿，`grep` 无残留导入失败。
- [ ] 4.3 扩展 `check-no-shims`：覆盖公共 facade 登记（导出必须登记、新增需写消费者）；验证：添加未登记导出时脚本失败。
- [ ] 4.4 删除中间层：`src/host/backend/port-holders.ts`、`protocol.py` 三导出、`runtime/core/engine-api/frame.ts` 转发、`qc_impl/api.py` 的 `exec(compile(...))` 兜底、合并 `recovery` 两层（`runtime/recover.ts` 与 `execution/ledger/recover.ts`）；验证：相关单测与 `test:unit` 全绿，`check-no-shims` 通过。
- [ ] 4.5 addon 凭据单一所有者（scene props 或 prefs 二选一，~8 处 `if prefs else scene prop` 收敛），补命令级测试；验证：`npm run test:addon-unit` 通过，GUI E2E 里设置/读取凭据路径人工确认。
- [ ] 4.6 删除 `runtime/config.ts:20-21` 的 `detectBlenderExe` 再导出，测试改为直接 import `runtime/blender-detect.js`；验证：`npm run typecheck` 与 `test:unit` 通过，`grep` 无 `from './config.js'` 的 detectBlenderExe 导入。
- [ ] 4.7 处理 E2 串行税：启动探测不阻塞串行 addon 队列 / 与 lifecycle diagnose 去重，再回调预算到 `/status 5s、doctor 4s`；验证：`tests/integration/js/doctor-bounded.test.ts` 通过且记录改造前后耗时（贴到 PR）。
- [ ] 4.8 更新 `docs/architecture.md` 的 facade、双入口、收税点章节；验证：`check:doc-paths` 通过。

## 5. S3 死代码与软带登记

- [ ] 5.1 按设计 D9 清单逐项删除（5 个未用导出、`__unusedAsRecord`、重复 `ReceiptContext`、`import_model_files` 链、`compat.py`、`core/render-state.ts`），每项先 `grep` 消费者；验证：`grep` 命中为 0，`npm run typecheck` 与 `test:unit` 通过。
- [ ] 5.2 `package.json` 删除 `runtime/ext/pkg.tgz` 打包条目，清理工作区 85 个陈旧 `.pyc`；验证：`npm pack --dry-run` 文件清单不再含 pkg.tgz，`git status` 无新增缓存文件。
- [ ] 5.3 105 个 80–120 行函数逐类登记理由与 reviewBy（分派表 / 线性流水线 / 生成式目录），只拆有内聚收益者；验证：`check-function-budget` 通过且白名单条目全部带 reviewBy。
- [ ] 5.4 更新 `docs/architecture.md` 例外登记表与 5.3 的分类说明；验证：`check:doc-paths` 与 `check-exceptions` 通过。

## 6. S4 文档与叙事

- [ ] 6.1 归档 3 篇被取代文档到 `docs/archive/` 并写 `docs/archive/README.md`；验证：入链全部指向新位置，`check:doc-paths` 通过。
- [ ] 6.2 `skills/blender-modeling/SKILL.md` 重编号（消除 3→9 断号）、修正 "plannner" 拼写与版本行；验证：标题序号连续，`check:doc-paths` 通过。
- [ ] 6.3 曲面算法单一来源：删除 `docs/曲面算法.md`，以 `skills/blender-modeling/references/曲面算法.md` 为真源，更新 4 处入链；验证：`grep -rn "docs/曲面算法" ` 只剩 CHANGELOG 历史条目。
- [ ] 6.4 用户可见 MCP / Claude 文案改 Bridge（保留上游署名、`BLENDERMCP_*` env 兼容与许可证文本）；验证：`grep -rn "Claude\|MCP" addon/dsh_blender_bridge --include=*.py` 只剩署名/兼容/历史注释，`test:addon-unit` 通过。
- [ ] 6.5 清理代码/测试/脚本中的重构计划编号（800+ 处 ID 形状 token），新增 `check-plan-ids`（先 warn 后 enforce，豁免 CHANGELOG/archive/architecture 历史说明）；验证：enforce 模式下零命中，`npm run lint` 通过。
- [ ] 6.6 扩展 `check-doc-paths`：链接存在、非 archive 文档有入链（中英对应文件互指计入，中文与英文侧都必须有入链）、禁止引用不存在路径；验证：warn 模式收集存量后转 enforced，全绿。
- [ ] 6.7 `tests/BEHAVIOR_MATRIX.md` 行号引用改为"文件 + 符号名"，matrix 门禁校验符号存在；验证：故意改一个符号名时门禁失败，恢复后通过。
- [ ] 6.8 双语文档同步（`docs/zh/` 与英文侧），更新 `docs/architecture.md` 的叙事历史说明；验证：两语言版本链接互指正确。

## 7. S5 开发环境一键配置

- [ ] 7.1 写 `scripts/setup-dev.mjs`（Python 发现顺序、`.venv` 创建/复用、安装 `requirements-dev.txt` + `requirements-typecheck.txt`、`--check` / `--force`、失败非零带提示），并在 `package.json` 加 `setup:dev`；验证：删除 `.venv` 后 `npm run setup:dev` 一次成功，重复运行幂等，`npm run setup:dev -- --check` 通过。
- [ ] 7.2 CI 两处内联 venv 步骤改为调用脚本（`.github/workflows/pr.yml:88-96`、`release.yml:63-69`）；验证：workflow diff 只保留脚本调用，本地等价命令通过，无法本地验证的 CI 行为在 PR 描述注明。
- [ ] 7.3 文档收敛：`CONTRIBUTING.md`、`README.md`、`docs/zh/CONTRIBUTING.md` 的安装命令改为 `npm run setup:dev`，`scripts/README.md` 登记新脚本；验证：文档命令照抄可用，中英文档互链正确。
- [ ] 7.4 干净环境验收（Windows；CI 覆盖 Linux）：setup 后跑 `npm run typecheck` + `test:unit` + `test:python`；验证：三项全绿，输出贴 PR。

## 8. 集成验收

- [ ] 8.1 全门禁：`npm run typecheck`、`test:unit`、`test:integration`、`test:contract`、`test:addon-unit`、`check:python-static`、`build`、`check:build`、`check:parity`、`test:matrix`、`check:exceptions`、`check:routes`、`check:plan-ids`；验证：全部 exit 0，输出贴 PR。
- [ ] 8.2 `npm pack --dry-run` 与 install smoke（`check:addon` + zip 69 条目）通过；验证：打包清单与 addon zip 均为预期。
- [ ] 8.3 确认契约计数不变（15 / 34 / 228 / 38 / 29 / protocol 11）并在 PR 声明；验证：`sync_counts` 输出无差异。
- [ ] 8.4 写 CHANGELOG **3.0.6** 条目（分节：缺陷修复 / 治理（含 engine.ts 公开面变化）/ 开发环境脚本）与 PR 模板（refactor.md）披露；验证：CHANGELOG 结构通过现有发布检查，PR 描述含 AI 披露节。

## Workflow follow-up

- 流程：推送治理分支 → 开 PR（refactor.md + AI 披露）→ 合并 `main` → 发布 **3.0.6**（含修复、治理与 setup 脚本）→ 归档 change（`openspec archive converge-3-0-terminal-state`），确认 `openspec/changes/archive/` 出现本 change、`openspec/specs/` 保持为空。
- 确认 `D:\DSH` 只保留证据与已执行计划，不再新增叙事。
