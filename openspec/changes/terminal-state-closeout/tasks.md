# Tasks

## 1. 准备（分支、基线、先红）

- [x] 1.1 同步并开分支：`git fetch origin --tags`、`git switch main`、`git pull --ff-only` 到 `7ed9ff1`，再从 main 开 `chore/terminal-state-closeout`；验证 `git rev-parse --short HEAD` 与 `origin/main` 一致、`git status --porcelain` 只有本地未跟踪产物。
- [x] 1.2 记录基线：在分支上依次跑 `npm run check:finalization`、`npm run check:structure`、`npm run check:exceptions`、`node scripts/check-plan-ids.mjs --enforce`、`npm run test:matrix`、`npm run check:python-static`；验证全部 exit 0，并把 346 行矩阵 / 228 ops / 38 commands 等计数贴进 PR 草稿。
- [x] 1.3 先红（F1）：删除 `config/contracts.json` 中 qc 家族的 `catalogExtras`（`compare_auto`、`align_iou` 两条）；验证 `npx vitest run tests/unit/js/contract/catalog-dispatch-parity.test.ts` 失败，报错点名这两个缺失的 Python 分派键。

## 2. F1 qc 两个 op 可分派

- [x] 2.1 在 `runtime/py/render/qc_impl/api.py` 的 `qc_ops()` 注册 `"compare_auto": qc_compare_auto`；验证 1.3 的 parity 测试中 `compare_auto` 不再缺失。
- [x] 2.2 新增路径级封装 `qc_align_iou_op(ref_path, ref_box=None, render_path=None, render_box=None, pad_frac=0.05)`（`qc_load` → `qc_mask_auto` → `qc_align_iou`，只返回 JSON 标量）并注册 `"align_iou"`；验证 parity 测试转绿、`npm run check:python-static` 通过。
- [x] 2.3 新增原生 E2E `tests/e2e/blender/native/qc_dispatch.py`（合成 128×128 RGB PNG，经 `K.dsh_qc_api["dispatch"]` 调 `compare_auto`/\`align_iou\`，断言非 `unknown qc op`、同图 IoU≈1、同高半宽窄图明显更低——纯平移会被掩膜 bbox 紧裁对齐吃掉），并在 `tests/e2e/blender/native_manifest.py` 注册（id `qc_dispatch`、preloads `("qc", "imgtools")`、timeout 300）；验证 `DSH_BLENDER_EXE=<path> npm run test:e2e` 中该 case 通过。
- [x] 2.4 更新 `tests/BEHAVIOR_MATRIX.md` 的 `OP-qc-compare_auto` / `OP-qc-align_iou` 两行：behavior 改为"dispatch 成功"、unit 指向 parity 测试、e2e 指向 `qc_dispatch`；验证 `npm run test:matrix` 输出仍是 346 行（344 covered / 2 exempt）且计数不变。
- [x] 2.5 在 `CHANGELOG.md` 的 Unreleased/下一 patch 分节记录"行为修复：qc_compare_auto / qc_align_iou 可分派"；验证 `npm run prettier -- --check CHANGELOG.md`（或项目 `format:check`）通过且没有改动版本号/安装命令。

## 3. F2 链接层与例外有主化

- [x] 3.1 修改 `config/contracts.json`：qc 不再有 `catalogExtras`；shape 的 `"search"` 从 `catalogExtras` 改为 `hostOps`（带 reason）；同步改 `tests/unit/js/contract/catalog-dispatch-parity.test.ts`，拒绝任何 `catalogExtras` 残留、校验 `hostOps` 与 Python 表互斥；新增两条 fixture 用例（缺键必红、host-side 合法）；验证 `npm run test:contract` 全绿。
- [x] 3.2 给 `config/doc-links-baseline.json` 增加 `review: {owner, reviewBy, reason}`；改 `scripts/check-doc-paths.mjs`：`missingRefs` 非空时缺 review / reason 为空 / reviewBy 过期即失败；同步 `tests/unit/js/contract/doc-paths-gates.test.ts` 的正反用例；验证 `npm run check:doc-paths` 通过、fixture 缺 review 时为红。
- [x] 3.3 在 `config/exceptions.json` 增加 8 条 `python-private-import` 登记（addon `commands/assets_polyhaven.py`、`commands/material.py`、`commands/polyhaven/{download,hdri,material,model}.py`、`commands/polypizza/{client,download}.py`，每文件一条，`detail` 列出该文件的 `_polyhaven_*` / `_polypizza_*` 名字及目标 `integrations/*_client` 模块，owner `addon-integrations`，reviewBy 2027-06-30），并在 `scripts/check-exceptions.mjs` 的 KINDS 增加该 kind；验证 `npm run check:exceptions` OK（12 entries）且没有过期项。
- [x] 3.4 新增 `tests/unit/python/test_private_import_contract.py`（纯 ast）：扫描 `addon/dsh_blender_bridge/**` 中导入 `_polyhaven_*` / `_polypizza_*` 的 `ImportFrom`，要求每对 (file, name) 被 3.3 的登记覆盖、且无 stale 项；验证 `npm run test:python` 通过；临时把一条登记改错名时应失败并回滚。
- [x] 3.5 更新 `docs/architecture.md` 的例外/函数预算摘要表（新增 `python-private-import`、doc baseline review、hostOps 说明）；验证 `npm run check:doc-paths` 通过且摘要与 `config/exceptions.json` 条目数一致。

## 4. F3 注释去历史化

- [x] 4.1 枚举并改写叙事头：`grep -rn "从 .* 拆出\|行为冻结" src runtime addon tests scripts` 命中的 40 + 23 个文件（去重后逐文件）以及 `addon/dsh_blender_bridge/protocol.py` 顶部悬空引用，统一为"现在负责什么"，历史最多一句 `History:`；验证同一条 grep 只剩 0 处（`tests/MIGRATION.md` 等明确历史文档除外则列入 allowlist 并在任务记录说明）。
- [x] 4.2 在 `docs/architecture.md` 记录注释约定（职责优先、历史单行、不写步骤 ID）；验证 `npm run check:doc-paths` 通过。
- [x] 4.3 验证 4.1 未改代码：`git diff` 仅注释行变化（逐文件 review），并跑 `npm run typecheck`、`npm run lint`、`npm run format:check` 全绿。

## 5. F4 流程与工作区收尾

- [x] 5.1 在 `.gitignore` 增加 `/.dsh/` 与 `/.dsh-ops/`；验证 `git status --porcelain` 不再显示这两项、`npm pack --dry-run` 内容不含它们。
- [x] 5.2 清理旧 worktree：先 `git worktree list` 记录 22 个旧项，仅对干净项 `git worktree remove`，最后 `git worktree prune`；验证 `git worktree list` 只剩当前工作树（或明确保留的项）。
- [x] 5.3 归档上一 change：`npx openspec archive converge-3-0-terminal-state --yes`；验证 `npx openspec list --json` 只剩 `terminal-state-closeout`，且 `openspec/changes/archive/` 下出现归档目录、`git status` 记录删除+新增。
- [x] 5.4 刷新仓库外证据索引：`D:\DSH\refactor-workspace\docs\refactor\evidence\README.md` 列出 48 个目录，17 个缺项目录标注 `historical container` 或补齐四件套，写明 T9/T10/T14 的 F4.4 重封；验证 README 中目录数与实际 `ls evidence` 一致（操作项，不进 CI）。
- [x] 5.5 在 `CHANGELOG.md` 同分节补"治理：例外有主化/注释去叙事/归档与工作区收尾"；验证 `format:check` 通过。

## 6. 集成验收

- [x] 6.1 全量门禁：`npm run check:finalization`、`check:structure`、`check:exceptions`、`check-plan-ids --enforce`、`test:matrix`、`check:doc-paths`、`typecheck`、`lint`、`format:check`、`test:unit`、`test:contract`、`test:python`、`check:python-static`、`check:addon`、`check:build`、`test:parity` 全部 exit 0；验证计数仍是 15/34/228/38(+ping)/29/protocol 11。
- [x] 6.2 契约校验：`npx openspec validate terminal-state-closeout --strict` valid（无 warning），`npx openspec status --change terminal-state-closeout --json` 显示 proposal/specs/design/tasks 全 done。
- [x] 6.3 真机验证：在带 Blender 的发布机器跑 `DSH_BLENDER_EXE=<path> npm run test:e2e`，确认 `qc_dispatch` case 通过并记录日志路径；无 Blender 环境不得用 skip 冒充通过。
- [x] 6.4 打包与安装冒烟：`npm pack --dry-run` 检查 files 清单（无 openspec/、无 .dsh、无运行时产物）；按 `docs/操作教程.md` 的安装步骤做一次本地 addon 安装冒烟（若本机有 Blender）。
- [ ] 6.5 开 PR：用 `.github/PULL_REQUEST_TEMPLATE/refactor.md`，写清 F1 行为修复与 F2–F4 治理、测试证据与 AI 披露；验证 PR checks 全绿、`mergeStateStatus` CLEAN。

## Workflow follow-up

- 合并 PR；发布（是否发 3.0.7）与 tag 由用户决定，发布后按 `docs/release-checklist.md` 执行。
- 本 change 满足审查/发布条件后用 `/openspec-archive-change` 归档，归档后确认 `openspec list` 为空。
- F5 `scripts-strictening-plan-v1` 保持独立计划，不在本 change 内执行。
