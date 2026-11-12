# Proposal

## Why

Blender 的 Asset Browser 能浏览、拖入资产，但当前插件没有一条稳定链路把资产保存成可复用文件，也没有让模型在 `blender_op` 里查询、导入这些资产。用户与 agent 只能重复建模或手工找文件；3.1 的六工具面、Gateway 和扩展面板已经就位，资产库是 `gateway-topology-3-1` 留下的后续能力，现在适合先固定 v1 的行为与边界。

本机已在 Blender 4.2.23 LTS / 4.5.16 LTS / 5.2.4 LTS 实测：`ID.asset_data` / `asset_generate_preview()` 可用，预览可随 `.blend` 持久化；`BlendDataLibraries.load(assets_only=True, link=True)` 可无打开场景地读取资产元数据和 128×128 预览；Asset Browser 的 `FILE_BROWSER` + `TOOL_PROPS` 面板能拿到真实选中资产。这些事实足以支撑 v1，但也暴露了版本兼容、空集合预览、材质预览慢、引用失效等必须写进契约的问题。

v1 同时要解决一个边界问题：Asset Browser 里不只有 DSH 本地库，还有用户自己的 Blender Asset Library、内置 Essentials 和当前文件资产。用户要求「所见即所得」：面板里看得到的资产，选中后都要能导入，不能只认 DSH 本地目录。

## What Changes

- 新增 **`asset` plan family**（`blender_op`，不新增模型工具）：
  - `asset_save`：把当前文件里的对象、材质、集合或其他可资产化 ID 保存到 DSH 资产库；只新建文件，不覆盖。
  - `asset_list` / `asset_get`：按 `AssetRef` 查询 DSH 库、用户 Asset Library、内置 Essentials 和当前文件；只读，不打开文件替换当前场景。
  - `asset_import`：按 `AssetRef` 导入；外部文件默认 append，当前文件资产返回 `asset_already_in_scene` 直接使用，不默认复制。
  - `asset_selftest` / `asset_help`：与现有 family 的自检 / 帮助约定一致。
- 统一 **`AssetRef`**：`{source, library, id_type, name}`。`source` 为 `dsh` / `user` / `essentials` / `current_file`；`library` 是绝对 `.blend` 路径（`essentials` 和当前文件可为空）。多资产 `.blend` 每个资产一行，不再用文件路径当唯一 ID。
- 支持 **Asset Browser 的 39 种 ID 类型**：`asset_save` 用 `id_type + name` 泛化，保留 object/material/collection 便捷参数；`asset_import` 按 `AssetRef` 解析；不支持的类型返回 `unsupported_asset_type`，在线资产返回 `asset_not_local`。
- 新增 **Asset Browser 侧边栏**：在 `FILE_BROWSER` / `TOOL_PROPS` 显示当前选中的资产（支持多选，1–20 个）并发送到 DSH 草稿；保存和导入仍由 `blender_op` 完成；超过 20 个时禁用发送并提示，不静默丢弃。
- 扩展现有 **`/host/selection` 协议**，允许 `selection.kind="asset"`，载荷只带 `AssetRef[]` 和可选 tags 显示信息，不带 sourceVersion / thumbnail；不新增 HTTP route，不新增模型工具，不新增 addon wire command。
- **保存位置唯一真源在 Blender 侧**：addon 偏好设置 Asset Library + Subfolder，默认库为 `<DSH_HOME>/dsh-blender/assets`（与 `vision/` 同级），首次保存时创建并注册；host 不再有 `assetLibraryDir`，只通过 `plugin-home`（`blender-vision-opencv`）解析 home。
- 缩略图：保存时 best-effort 生成并持久化 128×128 预览；查询只返回 `embedded` / `missing` / `unreadable`；`asset_get` 最多附图 1 张临时 PNG，由 host 附图白名单处理后清理；列表不返回像素。
- 版本：资产文件带来源 Blender 版本；实际读取因版本不兼容失败时返回 `asset_version_unsupported`，不预判版本、不把底层“不是 blend 文件”直接暴露。
- **E1 前置**：新增 `PlanOpMeta` per-op 元数据，把六工具的只读/会话/写作用域从 family 级派生到 op 级；`asset_list/get/help` 只读，不要求 GUI 会话（`current_file` 除外）；`asset_save/import/selftest` 需要会话并声明写作用域。
- 批量扫描/缩略图查询走 Gateway 的 headless 队列；单次 `asset_get` 使用短生命周期 headless 查询进程，避免只读查询排到大量 render 作业后面；GUI 进程内不 load/remove 外部库。

## Capabilities

### New Capabilities

- `blender/asset-library`: 本地资产库的保存、查询、导入、缩略图、版本兼容、Asset Browser 侧边栏发送，以及跨库 `AssetRef` 的所见即所得行为。

### Modified Capabilities

- `blender/plugin-home`: 资产库默认根复用 `plugin-home` 的 `<home>/assets`；不新增第二套路径解析。
- `plan-op-dispatch`: 新增 per-op 元数据与只读/会话/写作用域派生；现有“全 catalog 可解析”要求不变。

## Impact

- **Extension**：`extension/dsh_blender_bridge` 新增 Asset Browser 面板 / operator / `AssetRef` helper；`FILE_BROWSER` 面板与现有 3D 侧栏并存；不改模型工具名；命令面仍为 25 registered / 26 wire。
- **Catalog / Python**：`runtime/catalog/data.ts` 增加 `asset` family 与 `PlanOpMeta`；新增 `runtime/py/asset/` 域并注册到 `runtime/py/modules.json`；ops 从 239 增至 245，families 从 36 增至 37；`config/contracts.json` 与契约测试同步。
- **Host**：删除 `assetLibraryDir`；`runtime/selectionstore.ts`、`runtime/http/host/routes.ts` 与 Web/TUI 草稿支持 `kind=asset`；HTTP route 仍为 29。addon 通过 `plugin-home` 的 home 创建默认库；host 不维护资产库第二真源。
- **依赖**：本 change 依赖 `blender-vision-opencv` 的 `plugin-home`（`DSH_BLENDER_HOME` → `$DSH_HOME/dsh-blender` → `~/.dsh/dsh-blender`）；在它落地前不实现资产库，避免复制路径解析。
- **兼容性**：4.2 不能读 5.2 资产文件，4.5 可以（实测）；v1 不做跨版本自动降级，只做创建版本记录、实际失败时的结构化拒绝与可执行提示。
- **测试 / 文档**：新增 4.2/4.5/5.2 可用的资产 fixture、`AssetRef` 跨库、当前文件直接使用、空集合 / 材质缩略图 / 同名冲突 / 版本拒绝 / 引用刷新测试；更新 CHANGELOG、操作教程、架构文档、配置参考和 `tests/BEHAVIOR_MATRIX` 计数。
- **非目标（v1 不做）**：远端 / 同步资产库、覆盖已有资产、资产删除 / 重命名、资产版本历史、批量迁移整个库、5.3 专项适配、超大库性能基准、把 Asset Browser 的全部功能搬进 DSH、默认复制当前文件资产、把 `SelectionEvent` 抽象提前到本 change。
