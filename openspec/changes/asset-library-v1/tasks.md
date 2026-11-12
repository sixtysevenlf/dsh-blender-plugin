# Tasks

## 1. 契约接线、per-op 元数据与骨架

- [ ] 1.1 先落 E1：在 `runtime/catalog/data.ts` 增加 `PlanOpMeta`（`name/readonly/requires_session/write_scope/summary?`），把全部 239 个 op 从 family 级派生到 op 级；`PLAN_READ_ONLY_OPS` 与 `src/tools/op/read-only.ts` 改由元数据生成。
- [ ] 1.2 在 `runtime/catalog/data.ts` 增加 `asset` family（`asset_save/list/get/import/selftest/help`）与 per-op 元数据：`list/get/help` 只读无会话，`save/import/selftest` 需要会话；用 `npm run test:contract` 验证 families=37、ops=245。
- [ ] 1.3 在 `config/contracts.json` 增加 `asset` 的 `pythonTables` / `families` 映射；用 `catalog-dispatch-parity` 测试验证 catalog op 与 Python dispatch 字面键对拍。
- [ ] 1.4 在 `config/routing.json` 增加 `asset_` 路由（`dsh_asset_api`、`ensureAsset`、`preload:"asset"`），运行 `node scripts/gen-routes.mjs`；用 `node scripts/check-routes.mjs` 验证哈希。
- [ ] 1.5 在 `runtime/py/modules.json` 登记 `asset` 域/模块，在 `runtime/paths.ts` 增加 `ASSET_PATH`；用路径/manifest 测试验证可解析。
- [ ] 1.6 新建 `runtime/py/asset/asset.py`，通过 `_KIT.register("asset", ...)` 暴露六个 op 的骨架；用一次 Blender `execute_code` 冒烟验证 `K.dsh_asset_api["dispatch"]` 可调用且未知 op 有结构化错误。
- [ ] 1.7 在 `runtime/core/engine-api/` 增加 `ensureAsset`，并在 `runtime/engine.ts` 只导出消费者需要的符号；用 `npm run typecheck` 与 `npm run check:structure` 验证 facade/模块预算。
- [ ] 1.8 依赖检查：`plugin-home` 尚未落地时只记录依赖，不复制路径解析；确认 `<home>/assets` 与 `vision/` 同级的约定与测试夹具。

## 2. AssetRef、保存、查询、导入

- [ ] 2.1 新建纯逻辑 `runtime/py/asset/ref.py`：`AssetRef` 的 source 白名单、绝对/相对路径校验、realpath 包含检查、39 种 id_type 映射与 `bpy.data` 集合解析；用 pytest 覆盖合法/非法 source、路径逃逸、当前文件 ref。
- [ ] 2.2 新建 `runtime/py/asset/naming.py`：文件名清理、`-2/-3` 冲突后缀、.blend 头版本解析；用 pytest 覆盖非法字符、Windows 保留名、后缀竞争、4.2/5.2 头解析。
- [ ] 2.3 实现 `asset_save` 的 `id_type + name` 唯一 ID 选择与 object/material/collection 便捷参数；用 pytest 覆盖多选、空选、非法类型、`unsupported_asset_type`。
- [ ] 2.4 实现保存的当前文件 realpath 保护、同目录临时文件、`os.link` 不覆盖落位、`O_EXCL` 兜底与临时文件清理；用 Blender 测试覆盖目标=当前文件、并发同名、写入失败不留半成品。
- [ ] 2.5 实现保存时的 `asset_mark`、description/tags、来源版本 metadata 与缩略图 best-effort；用 4.2/4.5/5.2 各一次保存验证预览持久化、空集合 missing、材质回执含实际耗时。
- [ ] 2.6 实现 `asset_list`：`source=dsh/user/essentials/current_file/all`、library/type/query/tags/limit/offset、稳定排序与分页；文件库走 headless 子进程，`current_file` 只读 live `bpy.data`，无会话返回 `asset_session_required`。
- [ ] 2.7 实现 `asset_get`：接受 `AssetRef`，返回元数据与三态缩略图；读 `image_pixels_float` 写 128×128 临时 PNG 并返回 `images:[path]`；列表不返回像素。
- [ ] 2.8 实现 `asset_import`：外部文件默认 append、集合带子对象、实际名称/renamed 映射、失败回滚所有新建数据块；`current_file` 返回 `asset_already_in_scene`，不复制；`essentials`/在线资产无本地路径时 `asset_not_local`。
- [ ] 2.9 实现 `asset_selftest` / `asset_help`：目录可写、头解析、保存-查询-导入闭环、临时目录清理；用 `blender_op(op="asset_selftest")` 验证不污染场景与资产库。
- [ ] 2.10 实现查询的 headless 队列复用：批量扫描/缩略图走 `kind=headless`，单次 `asset_get` 用短生命周期查询进程；用测试验证不占 GUI 场景、不排在 render 队列后面。

## 3. host 配置、选择协议与 home

- [ ] 3.1 删除 host `assetLibraryDir` / `DSH_BLENDER_ASSET_LIBRARY` 及对应配置测试；只保留 `plugin-home` 的 home 解析；更新配置参考与 README，确认没有第二真源。
- [ ] 3.2 在 addon 偏好增加 `asset_library_name` / `asset_subfolder` / `resolved_library_path`；默认库 `<home>/assets`，首次保存创建并 `asset_library_add` 注册；用 headless 与 GUI 各验证一次。
- [ ] 3.3 在 `runtime/selectionstore.ts` 扩展 `kind:"asset"` 与 `SelectionAsset[1..20]` 消毒：source/id_type 白名单、library ≤ 500、name ≤ 200、空数组/超 20 整条拒绝；用 unit test 覆盖边界。
- [ ] 3.4 在 `src/tui/selection.ts` 与 `src/client/index.js` 增加 asset 文本/chip 分支（来源、名称、导入提示；多选数量）；用同一 fixture 对拍两套输出，验证与 objects/collection 不回归。
- [ ] 3.5 验证 `POST /host/selection`、`/latest`、`/consume` 仍为 29 routes；asset 只走既有路由；用 contract 与 host route 测试验证。
- [ ] 3.6 在 `commands/selection.py` 增加 `build_asset_payload(selected_assets)` / `send_asset_to_dsh()`；校验 1–20、去重、只带 refs/tags，不带 sourceVersion/thumbnail。

## 4. 扩展 Asset Browser 面板

- [ ] 4.1 新建 `extension/dsh_blender_bridge/ui/panels/asset_browser.py`（`FILE_BROWSER` + `TOOL_PROPS`），无资产安全空态、有资产显示 name/id_type/来源/文件；4.2/4.5/5.2 GUI 冒烟。
- [ ] 4.2 在 `ui/operators.py` 增加 `DSHBB_OT_SendAssetToDSH` 并注册，poll 检查 `context.selected_assets`；1–20 可发送，21 禁用并提示；用 GUI 单选/多选真实资产验证草稿文本。
- [ ] 4.3 验证面板覆盖 DSH 库、用户 Asset Library、Essentials 和当前文件；不按来源过滤、不生成缩略图、不解析版本；无选中不崩。
- [ ] 4.4 扩展 manifest 版本到 2.1.0（协议 12 不变），重建 `extension/dsh_blender_bridge.zip`；验证命令数仍为 25 registered / 26 wire。
- [ ] 4.5 为面板/操作符补 Python 单测与 GUI 标记测试；用 `npm run test:python` 与 `pytest -m "blender and gui"` 验证；不跑 5.3、不做大库基准。

## 5. 文档、计数与变更说明

- [ ] 5.1 更新 `docs/architecture.md`（asset family、AssetRef、39 类型、selection kind=asset、37/245）、`docs/配置参考.md`（删除 assetLibraryDir，写 addon 偏好与 home/assets）、`docs/操作教程.md`（保存/查询/导入/当前文件直接使用/侧栏发送/版本限制）；用 `npm run check:doc-paths` 验证。
- [ ] 5.2 更新 `README.md` 与 `docs/zh/README.md` 的能力清单和计数，保持中英同步；用 `npm run check:doc-paths` 与人工对读。
- [ ] 5.3 更新 `CHANGELOG.md` 的 3.1.0-alpha 区段（新增资产库、跨库 AssetRef、当前文件直接使用、材质缩略图耗时、选择协议扩展）；用现有格式门禁验证。
- [ ] 5.4 更新 `tests/BEHAVIOR_MATRIX.md` 与 `tests/fixtures/capability.lock.json`，运行 `node scripts/sync_counts.mjs`；用 `npm run test:matrix` 验证 37 families / 245 ops / 29 routes / 25 commands。
- [ ] 5.5 在文档中明确：`SelectionEvent` 抽象在 `openspec/future/selection-event`，本 change 不实现；当前 asset 发送是直接分支，未来再统一。

## 6. 集成与验收

- [ ] 6.1 增加资产库 E2E：4.2/4.5/5.2 各保存对象、材质、集合和一个非对象 ID；重开查询、导入、同名冲突与空集合；用三套 `DSH_BLENDER_EXE` 跑 `pytest -m "blender and not gui"` 并记录日志。
- [ ] 6.2 增加跨库 E2E：DSH 库、用户 Asset Library、Essentials、当前文件各一条 `AssetRef` 查询/导入；当前文件断言 `asset_already_in_scene`，不产生 `.001` 副本。
- [ ] 6.3 增加跨版本 E2E：5.2 保存的资产在 4.2 读取返回 `asset_version_unsupported`，4.5 读取成功；记录实测矩阵。
- [ ] 6.4 增加 Asset Browser GUI E2E：单选/多选/21 个上限 → 面板显示 → 发送 → DSH 草稿文本；用 4.2/4.5/5.2 的 GUI 运行，不跑 5.3、不做大库基准。
- [ ] 6.5 增加只读/租约测试：别处持写租约时 `asset_list/get/help` 不被 409；`asset_save` 仍要求会话；无 GUI 查询 `current_file` 返回 `asset_session_required`。
- [ ] 6.6 跑完整门禁：`npm run lint`、`format:check`、`typecheck`、`check:python-static`、`test:unit`、`test:contract`、`test:integration`、`test:python`、`test:matrix`、`check:structure`、`check:finalization`、`check:extension`、`check:python-imports`、`check:build`；全部通过并记录输出。
- [ ] 6.7 做干净构建与安装冒烟：`rm -rf lib && npm run build`、`npm pack --dry-run`、`npm run smoke:install`；验证包内无 openspec/.dsh/.dsh-ops/.review-tmp，doctor/health/status 正常。
- [ ] 6.8 用 `openspec validate asset-library-v1 --strict` 与 `openspec status --change asset-library-v1` 确认 4/4 artifacts 完成，再交给用户审阅。

## Workflow follow-up

- 本 change 只写规划产物；实现从 `/openspec-apply-change` 开始，按任务组逐个落地，不在此阶段改源码。
- 依赖 `blender-vision-opencv` 的 `plugin-home`；如果它尚未落地，先完成 1.8 的依赖检查，不复制路径解析。
- 实现完成后按项目要求开 PR、补 AI disclosure、合并；发布与归档按用户 release 流程执行，不自动 archive。
