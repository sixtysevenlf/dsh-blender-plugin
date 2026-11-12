# Design

## Context

3.1 的运行时已经是「DSH host → Gateway → addon」单端点拓扑，模型面固定为六个工具，规划面由 `blender_op` 的 catalog + Python dispatch 承担。资产库 v1 只补一个 `asset` family 和 Asset Browser 侧栏，不新增模型工具、HTTP route 或 addon wire command。

可复用的既有结构：

- catalog 唯一数据表：`runtime/catalog/data.ts`；`config/contracts.json` 把 family 映射到 Python dispatch 表；`config/routing.json` 经 `scripts/gen-routes.mjs` 生成路由。
- Python 模块唯一登记源：`runtime/py/modules.json`；`runtime/paths.ts` 的 `pyModuleFile()` 从它生成路径。
- Blender 侧 Python 由 `KERNEL_BOOTSTRAP` 注入 `K.dsh_kit`；新模块用 `_KIT.register(name, version, ops)` 注册 `K.dsh_<name>_api`。
- 选择协议：addon `commands/selection.py` POST `/host/selection`，host `runtime/selectionstore.ts` 消毒，Web/TUI 从 `/host/selection/latest` 取草稿；现有 `kind` 只有 `objects` / `collection`。
- 扩展注册：`extension/dsh_blender_bridge/ui/panels/index.py`、`ui/operators.py`、`commands/selection.py`；面板用 `Panel.draw` 的异常包装。
- `blender-vision-opencv` 的 `plugin-home` 定义 home：`DSH_BLENDER_HOME` → `$DSH_HOME/dsh-blender` → `~/.dsh/dsh-blender`，并在其中创建 `assets/`；本 change 依赖它，不复制路径解析。

已实测的关键事实（Blender 4.2.23 / 4.5.16 / 5.2.4）：

- `ID.asset_data` / `ID.asset_generate_preview()` 可用；预览 128×128，可随 `.blend` 持久化。
- `bpy.data.libraries.load(path, link=True, assets_only=True)` 只返回名称；把 `data_to.<collection>` 赋值为名称后能读 `ID.preview`，因此预览不是 GUI 专属，但需要加载 ID；headless 子进程可完成，退出即清理。
- Asset Browser 的 `FILE_BROWSER` + `TOOL_PROPS` 面板可注册并绘制；真实选中由 `context.selected_assets` 提供，`context.asset` 不能单独依赖。
- `AssetRepresentation.id_type` 在三个版本都是 39 种；`.blend` 可包含多个资产，因此不能用文件路径当资产唯一键。
- 保存到当前打开的 `.blend` 目标会崩溃（4.2 EXCEPTION_ACCESS_VIOLATION）；保存必须拒绝当前文件并做原子落位。
- 4.2 不能读 5.2 文件，4.5 可以；不能按版本号预判，必须实际读取失败再拒绝。

## Goals / Non-Goals

**Goals:**

- 用一个稳定 `AssetRef` 统一 DSH 库、用户 Asset Library、内置 Essentials 和当前文件资产；Asset Browser 里看到的资产都能查询/导入。
- 保存只新建、不覆盖；路径、命名、冲突后缀、原子写入、当前文件保护都有确定行为。
- 查询只读：不打开文件替换当前场景、不返回原始像素、不污染 GUI 选择；文件库查询在 headless 子进程中完成。
- 当前文件资产直接使用，不默认复制；外部文件默认 append，返回实际名称与重命名映射。
- 缩略图 best-effort；`asset_get` 最多一张；缺失/不可读如实报告。
- 保存位置唯一真源在 Blender 侧（addon 偏好）；host 删除 `assetLibraryDir`，只保留 plugin-home。
- catalog / contract / routing / modules 四张表可对拍；新增 family 仍受全 catalog 可解析门禁约束。
- E1 的 per-op 元数据在 v1 落地，asset 的只读/会话/写作用域不再靠 family 级猜测。

**Non-Goals:**

- 不做远端 / 同步 / 团队共享资产库；不做覆盖、删除、重命名、版本历史、批量迁移。
- 不把 Asset Browser 的全部功能搬进 DSH；不做资产市场、下载、许可证管理。
- 不新增第七个模型工具；不新增 addon wire command；不新增 HTTP route。
- 不在 v1 做 5.3 专项适配、超大库性能基准、复杂材质缩略图的异步补写。
- 不把 `SelectionEvent` 抽象提前到本 change；它保留在 `openspec/future/selection-event`。
- 不默认复制当前文件资产；不把在线资产当本地资产。

## Decisions

### D1 — 统一 AssetRef

资产身份是 `AssetRef`，不是文件路径，也不是名称：

```ts
type AssetSource = 'dsh' | 'user' | 'essentials' | 'current_file'

type AssetRef = {
  source: AssetSource
  library: string | null // dsh/user: 绝对 .blend 路径；essentials/current_file: null 或真实路径
  id_type: AssetIdType // 39 种 Blender ID 类型之一
  name: string // ID 名；同一 .blend 内唯一
}
```

- `dsh`：`<home>/assets` 下的文件。
- `user`：Blender 偏好 `filepaths.asset_libraries` 中已注册的库；`library` 必须是该库内的绝对路径。
- `essentials`：Blender 内置 Essentials；不保证有可写路径。
- `current_file`：当前打开文件里的资产；`library` 可为当前 `bpy.data.filepath`，不用于导入复制。
- 多资产 `.blend` 每个资产一行；`library + id_type + name` 是选择/查询键。
- `asset_list` 返回 `ref` 加展示元数据（`tags`、`sourceVersion`、`thumbnail`）；`asset_get` / `asset_import` 直接吃 `ref`。
- tags 是筛选/展示信息，不是身份；`asset_import` 只用 `ref`，忽略 tags。

### D2 — 保存位置：Blender 偏好是唯一真源

- 删除 host `assetLibraryDir` 与 `DSH_BLENDER_ASSET_LIBRARY`；host 不再保存第二份资产库路径。
- addon 偏好新增：
  - `asset_library_name`：Blender Asset Library 名；默认 `DSH Assets`；
  - `asset_subfolder`：库内子目录；默认空；
  - 解析后的 `resolved_library_path` 只读显示。
- 默认库：`<home>/assets`；首次保存时创建目录并调用 `bpy.ops.preferences.asset_library_add(directory=...)` 注册。home 来自 `plugin-home`；addon 通过握手或 `DSH_BLENDER_HOME` 取得，自己不再推导第三套路径。
- 保存只允许写到 addon 配置的可写库（默认 DSH 库）；Essentials、当前文件和只读库拒绝 `asset_library_readonly`。
- 若库目录不存在且无法创建、或 addon 未配置可写库，返回 `asset_library_unavailable` 与可执行提示。

### D3 — 保存：只新建、原子落位、当前文件保护

`asset_save` 顺序：

1. 解析 `id_type + name` 或 object/material/collection 便捷参数，确定唯一 ID；多选/空选拒绝。
2. 检查 `id_type` 可资产化；`asset_mark()`（若未标记），设置 description/tags；可选生成缩略图。
3. 目标根 = addon 配置的库 + subfolder；文件名由 ID 名清理（去路径分隔符/控制字符/首尾空格和点；始终补 `.blend`；Windows 保留名可因后缀使用）。
4. 目标 realpath 等于 `bpy.data.filepath` → `asset_path_invalid`，不调用会崩溃的写入。
5. 写同目录临时文件 `<library>/.tmp-<uuid>.blend`，用 `bpy.data.libraries.write`。
6. **不覆盖落位**：优先 `os.link(temp, target)`（目标存在则 `FileExistsError`）；失败则用 `os.open(target, O_CREAT|O_EXCL)` 预留后 `os.replace(temp, target)` 替换自己预留的空文件；两种方式都不覆盖他人文件。
7. 目标已存在时尝试 `-2`、`-3`… 最多 100 次；仍冲突返回 `asset_exists_race`。
8. 成功后删除临时文件，返回 `{ok, ref, path, id_type, name, sourceVersion, thumbnail, elapsedMs}`。

- 写入失败清理临时文件，不留半成品；已存在的目标文件不受影响。
- 缩略图失败不回滚资产文件；回执标 `thumbnail.available=false` 与原因。
- `thumbnail=false` 是显式快速路径；材质慢是已知成本，不伪装成快。

### D4 — 查询：跨库、只读、headless

`asset_list` 参数：

```ts
{
  source?: 'dsh' | 'user' | 'essentials' | 'current_file' | 'all' // 默认 dsh
  library?: string       // user 库名或绝对路径；all 时可选
  id_type?: AssetIdType  // 单个类型
  query?: string         // 名称/描述子串
  tags?: string[]        // AND
  limit?: number         // 默认 50，最大 200
  offset?: number
  refresh?: boolean
}
```

- `dsh` / `user` / `essentials`：Gateway 启动短生命周期 headless 子进程，逐个 `.blend` 用 `libraries.load(link=True, assets_only=True)` 读元数据/预览状态；子进程退出即清理，不污染 GUI 场景。
- `current_file`：只在有 GUI 会话时读 `bpy.data` 中 `asset_data` 非空的 ID；没有会话返回 `asset_session_required`，不偷偷打开文件。
- `all`：`dsh + user + essentials`，不自动包含 `current_file`（当前文件必须显式选，避免把未保存状态混进列表）。
- `user` 的 `library` 必须在 Blender 已注册库中；拒绝任意路径，防止把查询变成文件系统扫描。
- 跳过：非 `.blend`、符号链接、当前打开文件、损坏文件；损坏文件不使整次列表失败，返回行内 `readable:false`。
- 稳定排序：`source, library, id_type, name`；分页基于排序后的结果。
- 列表只返回 `thumbnail: "embedded"|"missing"|"unreadable"`，不返回像素数组。
- `asset_get` 接受 `ref`；返回同一套元数据 + 可选缩略图；`current_file` 直接读 live ID；文件库走 headless。
- 批量扫描/缩略图生成使用 Gateway 的 `headless` 作业队列；单次 `asset_get` 使用短生命周期查询进程，避免只读查询排在大量 render 作业后面。

### D5 — 版本：读头，实际失败再结构化拒绝

- 读取 `.blend` 头 12 字节的 `BLENDER` + 3 位版本，得到 `sourceVersion`（如 `4.2`）；当前文件用 `bpy.app.version_string`。
- 头不合法 → `asset_invalid_file`。
- 头合法但 `libraries.load` 因版本失败 → `asset_version_unsupported`，带 `path`、`sourceVersion`、`currentVersion`、`hint`；列表行标 `readable:false`，不整次失败。
- 不按版本号预判；4.5 能读 5.2 的实测路径保留。
- v1 不降级、不自动重存、不修改原文件。

### D6 — 缩略图：三态，asset_get 最多一张

- 保存时同步 `asset_generate_preview()`，写入同一 `.blend` 才能持久化；对象/集合通常快，材质 1–22 秒波动，回执带实际 `ms`。
- 空集合预览尺寸 0 → `available:false, reason:"empty_collection"`；不生成占位图。
- `asset_list` 只报 `embedded/missing/unreadable`；`asset_get` 读 `image_pixels_float`（128×128，65536）写临时 PNG，返回 `images:[path]`。
- host 的 `planImagePaths` 白名单加入 `asset_get`；最多附图 1 张；host 附图后再清理临时文件（失败也 best-effort 清理）。
- 列表/详情不把原始数组塞进 tool response。

### D7 — 导入：外部 append，当前文件直接使用

`asset_import` 接受一个或多个 `AssetRef`（最多 20），逐个处理：

- `current_file`：资产已在当前场景，返回 `{ok:true, status:"asset_already_in_scene", name, id_type}`；不复制、不 append。agent 直接用 `bpy.data`。
- `dsh` / `user` / `essentials`：默认 `link=false` append；集合连同子对象；`link=true` 可选。
- 导入前快照 `bpy.data.objects/materials/collections/meshes/images/node_groups/armatures/actions` 等；失败时删除本次新建的所有数据块，返回结构化错误，不留部分导入。
- 成功返回每个资产的 `requestedName`、`actualNames`、`renamed`、`linked`；不假设源名等于实际名。
- `essentials` 无本地路径或 Blender 不提供可导入 `full_path` → `asset_not_local`；不做网络下载。
- 在线/未下载资产 → `asset_not_local`；v1 不访问网络。

### D8 — Asset Browser 侧栏：所见即所得，只发送

新增 `extension/dsh_blender_bridge/ui/panels/asset_browser.py`：

- `bl_space_type = "FILE_BROWSER"`、`bl_region_type = "TOOL_PROPS"`、`bl_label = "DSH Assets"`。
- `draw` 用 `context.selected_assets` 读全部选中资产（不按来源过滤），用 `context.asset` 取活动资产；不访问可能不存在的 `space.params`。
- 显示 name / id_type / 来源库 / 文件；多选显示数量和前几个名称；无资产空态；发送按钮仅在 1–20 个选中时启用。
- 超过 20 个：禁用发送并显示“最多 20 个”，不静默截断。
- 只读：不写场景、不改变选择、不生成缩略图、不解析版本。
- 配套 `DSHBB_OT_SendAssetToDSH` 与 `commands/selection.py` 的 `build_asset_payload(selected_assets)` / `send_asset_to_dsh()`；不新增 wire command。
- 保存和导入不在面板实现，统一走 `blender_op` 的 `asset` family，避免 addon 与 runtime/py 两份资产逻辑。

### D9 — 选择协议：kind=asset，不新增 route

`runtime/selectionstore.ts` 的 `SanitizedSelection.kind` 扩展为 `'objects' | 'collection' | 'asset'`，新增：

```ts
type SelectionAsset = {
  source: 'dsh' | 'user' | 'essentials' | 'current_file'
  library: string | null
  id_type: string
  name: string
}
type SelectionAssets = SelectionAsset[] // 1..20
```

- 载荷只带 refs 与可选 `tags` 显示信息；**不带** `sourceVersion` / `thumbnail`（版本/缩略图由 `asset_get` 查，避免面板加载 ID）。
- `library` 长度 ≤ 500，`name` ≤ 200；`source`/`id_type` 白名单；空数组、超过 20、字段非法整条拒绝。
- 多选按 `selected_assets` 顺序去重，活动资产排第一。
- `kind=asset` 时 `count=assets.length`、`names=assets.map(a=>a.name)`、`objects/collections` 为空。
- TUI `src/tui/selection.ts` 与 Web `src/client/index.js` 增加 asset 文本/chip 分支；v1 先直接实现，未来的 `openspec/future/selection-event` 再统一为 envelope。
- 不新增 `/host/selection` route，不改变 `consume` 语义；返回仍为 `{ok, seq, received_at}`。

### D10 — catalog / routing / per-op 元数据

- `runtime/catalog/data.ts` 增加 family：

```ts
{
  f: 'asset', prefix: 'asset_', requires_session: false,
  write_scope: 'library',
  when: '本地/用户/内置/当前文件资产：保存、查询、导入',
  not: '不是 Asset Browser 完整替代；不做远端库、覆盖、删除',
  ops: [
    { name:'save', readonly:false, requires_session:true,  write_scope:'library' },
    { name:'list', readonly:true,  requires_session:false, write_scope:'none' },
    { name:'get',  readonly:true,  requires_session:false, write_scope:'none' },
    { name:'import', readonly:false, requires_session:true, write_scope:'scene' },
    { name:'selftest', readonly:false, requires_session:true, write_scope:'library' },
    { name:'help', readonly:true,  requires_session:false, write_scope:'none' }
  ]
}
```

- 新增 `PlanOpMeta`；`PLAN_READ_ONLY_OPS` 与 `src/tools/op/read-only.ts` 从 op 元数据派生，`asset_list/get/help` 自动进入只读清单，租约门禁豁免。
- `asset_list(source=current_file)` 在无会话时返回 `asset_session_required`；这是唯一条件性会话行为，catalog 仍标 `requires_session:false`，由 runtime 解释 source。
- `config/routing.json` 增加 `asset_` route（`api:"dsh_asset_api"`、`ensure:"ensureAsset"`、`preload:"asset"`）；`config/contracts.json` 增加 `asset` dispatch table；`runtime/py/modules.json` 登记 `asset` 域/模块；`runtime/paths.ts` 增加 `ASSET_PATH`。
- `runtime/core/engine-api/` 增加 `ensureAsset`；`runtime/engine.ts` 只导出消费者需要的符号。
- 计数：37 families / 245 ops / 29 routes / 25 registered + 26 wire commands / 六工具不变。

### D11 — 错误契约

统一返回 `{ok:false, error:<code>, stage, hint, next?, details?}`：

| code                        | 触发                      | 可执行提示                           |
| --------------------------- | ------------------------- | ------------------------------------ |
| `asset_library_unavailable` | 目录不能创建/不可写       | 检查 addon 资产库设置与权限          |
| `asset_library_readonly`    | 选中的库不可写            | 换 DSH 库或可写库                    |
| `asset_path_invalid`        | 目标等于当前文件/路径非法 | 换目录或文件名                       |
| `asset_exists_race`         | 并发创建导致后缀耗尽      | 重试或换名                           |
| `asset_not_found`           | ref 不存在                | 先 `asset_list`                      |
| `asset_invalid_file`        | 文件头不是 .blend         | 检查文件是否被截断                   |
| `asset_version_unsupported` | 实际读取因版本失败        | 用创建该资产的 Blender 版本          |
| `asset_import_failed`       | 导入异常/回滚             | 检查数据块名与场景                   |
| `asset_already_in_scene`    | 当前文件资产直接使用      | 用 `bpy.data`；要复制需未来显式 copy |
| `asset_not_local`           | 在线/未下载资产           | 先下载到本地库                       |
| `asset_session_required`    | 当前文件查询无 GUI 会话   | 启动/附着 Blender 会话               |
| `unsupported_asset_type`    | 该 ID 类型不可资产化/导入 | 换类型或用 Asset Browser             |
| `asset_too_many`            | 选择超过 20               | 分批发送                             |

所有保存/导入失败不得声称 `ok:true`；所有可恢复错误必须给下一步。

## Risks / Trade-offs

- **跨库扫描成本** → `asset_list` 默认只扫 DSH；`all` 显式才扫用户库/Essentials；批量走 headless 队列。
- **材质缩略图慢**（1–22 秒）→ 同步生成、回执如实报告、`thumbnail=false` 快速路径；v1 不做异步补写。
- **AssetRef 安全** → user/library 必须来自已注册库；realpath 包含检查；拒绝任意路径和 symlink 逃逸。
- **当前文件崩溃** → realpath 相等保护 + 原子落位 + 回归测试。
- **跨版本读取** → 只做实际失败才结构化拒绝；文档列实测矩阵，不承诺未测组合。
- **选择协议临时分支** → v1 在 TUI/Web 直接加 asset 分支，未来由 `selection-event` 统一；用 fixture 锁住两套输出一致。
- **catalog 计数变化** → 36→37 / 239→245；同步 `sync_counts`、BEHAVIOR_MATRIX、contracts、README/llms。
- **host 删除 assetLibraryDir** → 文档/配置/测试同步删除，避免继续出现第二真源。

## Migration Plan

1. 先落 E1 `PlanOpMeta` 与只读派生，确保 `asset_list/get/help` 不进租约门禁。
2. 落 catalog / contracts / routing / modules / paths 与 `ensureAsset`，跑 parity/check-routes。
3. 实现 `runtime/py/asset/` 的 `AssetRef`、命名、保存、查询、导入、selftest；先纯逻辑/文件级测试，再 headless 预览。
4. 接 host：删除 `assetLibraryDir`，插件 home 注入 addon；`selectionstore` 扩展 `kind=asset`；TUI/Web 文案。
5. 加 Asset Browser 面板/发送 operator；4.2/4.5/5.2 GUI 冒烟，不跑 5.3、不做大库基准。
6. 文档、CHANGELOG、矩阵、双语同步；全门禁。
7. 本 change 不合并/不发布；由 `/openspec-apply-change` 实施。

回滚：删除 `asset` family/模块/路由行/面板/选择分支并恢复 host 配置；不改既有 objects/collection 语义。

## Open Questions

（无。v1 的范围、AssetRef、保存语义、当前文件直接使用、host 删除 assetLibraryDir、默认 `<home>/assets`、侧栏边界、版本策略均已在上述决策中固定；实现细节在 tasks 中按测试驱动展开。）
