# Spec Delta

## Purpose

定义 DSH 与 Blender 之间的资产库能力：把当前文件中的对象、材质、集合及其他可资产化 ID 保存为可复用资产；通过统一 `AssetRef` 查询/导入 DSH 库、用户 Asset Library、内置 Essentials 和当前文件资产；在 Asset Browser 侧栏把所见资产发送到 DSH 草稿，并对跨 Blender 版本的读取与缩略图给出明确边界。

## ADDED Requirements

### Requirement: 统一资产引用与所见即所得

系统 SHALL 用 `AssetRef` 表示资产，至少包含 `source`（`dsh` / `user` / `essentials` / `current_file`）、`library`（本地 `.blend` 绝对路径或空）、`id_type` 和 `name`。一个 `.blend` 中的多个资产 MUST 各自成为独立引用；系统 MUST NOT 用文件路径作为资产唯一键。

Asset Browser 中可见的本地资产 MUST 能通过查询/导入链路使用；系统 MUST NOT 只支持 DSH 库而静默忽略用户库、Essentials 或当前文件资产。

#### Scenario: 一个文件包含多个资产

- **WHEN** 一个 `.blend` 同时包含对象资产 `Chair` 和材质资产 `Wood`
- **THEN** `asset_list` 返回两行不同 `AssetRef`，二者的 `library` 相同但 `id_type` / `name` 不同

#### Scenario: 用户库资产可查询

- **WHEN** 用户在 Blender 偏好中注册了包含资产的 Asset Library，并查询 `source="user"`
- **THEN** 该库中的资产以 `AssetRef` 返回，可交给 `asset_import`

#### Scenario: 当前文件资产不要求复制

- **WHEN** 用户选择当前文件中已存在的资产并调用 `asset_import`
- **THEN** 系统返回 `asset_already_in_scene` 和实际 ID 名，不创建副本，也不从同一文件再次 append

### Requirement: 本地资产保存语义

系统 SHALL 把当前文件中的对象、材质、集合或其他可资产化 ID 保存到 addon 配置的可写资产库，每个资产对应一个独立 `.blend` 文件。保存 MUST 只新建文件，MUST NOT 覆盖已有资产；同名时使用确定性的 `-2`、`-3` 后缀，并在回执中返回实际路径和 `AssetRef`。

系统 SHALL 支持按 `id_type + name` 保存任意可资产化 ID 类型，并保留 object / material / collection 的便捷参数；不可资产化或不支持的类型 MUST 返回 `unsupported_asset_type`，MUST NOT 伪造成功。

#### Scenario: 保存新对象资产

- **WHEN** 用户对当前文件中的对象 `Chair` 执行 `asset_save`，目标名在库中不存在
- **THEN** 系统创建对应 `.blend`，保留资产元数据，并返回 `AssetRef`、实际路径和缩略图状态

#### Scenario: 同名资产不覆盖

- **WHEN** 库中已有 `chair.blend`，用户再次保存名为 `chair` 的对象资产
- **THEN** 系统创建 `chair-2.blend`，原 `chair.blend` 保持不变，回执返回新路径

#### Scenario: 保存非对象资产

- **WHEN** 用户用 `id_type="WORLD"` 和名称保存一个可资产化的 World
- **THEN** 系统保存该 ID 并返回对应 `AssetRef`；若该类型不可资产化，则返回 `unsupported_asset_type`

### Requirement: 原子写入与当前文件保护

系统 SHALL 以同目录临时文件加原子落位的方式写入资产文件；保存失败时 MUST NOT 留下半成品，也 MUST NOT 破坏库中已有文件。系统 MUST 使用不覆盖已有目标的落位方式，MUST NOT 用无条件覆盖的替换实现“只新建”。

当目标路径 realpath 等于当前 Blender 已打开文件时，系统 MUST 在写入前拒绝，并返回可执行错误；MUST NOT 调用已知会崩溃的覆盖写入。

#### Scenario: 保存中途失败

- **WHEN** 写入资产文件过程中发生磁盘或权限错误
- **THEN** 系统返回结构化保存错误，库目录不出现不完整资产文件，已有同名资产不受影响

#### Scenario: 拒绝写入当前打开的文件

- **WHEN** 目标资产路径与当前 Blender 已打开的 `.blend` 相同
- **THEN** 系统拒绝保存并返回 `asset_path_invalid`，不调用会崩溃的写入

#### Scenario: 并发创建同名文件

- **WHEN** 两个保存请求同时尝试创建同一个目标文件名
- **THEN** 只有一个成功，另一个使用下一个后缀或返回 `asset_exists_race`，不覆盖先成功的文件

### Requirement: 资产库位置与 home

系统 SHALL 以 Blender addon 偏好中的 Asset Library + Subfolder 作为保存目标的唯一真源；默认库为 `<DSH_HOME>/dsh-blender/assets`，首次保存时创建并注册。系统 MUST NOT 在 host 配置中维护 `assetLibraryDir` 或等价的第二份资产库路径。

系统 SHALL 通过 `plugin-home` 解析 home（`DSH_BLENDER_HOME` → `$DSH_HOME/dsh-blender` → `~/.dsh/dsh-blender`），MUST NOT 另写一套路径解析。

#### Scenario: 默认库首次保存

- **WHEN** 用户没有手动配置资产库，执行第一次 `asset_save`
- **THEN** 系统在 `<home>/assets` 创建并注册默认库，保存成功后可在 Asset Browser 看到

#### Scenario: 只读库

- **WHEN** addon 配置的目标库是 Essentials 或只读库
- **THEN** 系统返回 `asset_library_readonly`，不尝试写入

### Requirement: 资产查询

系统 SHALL 提供 `asset_list` 和 `asset_get`，支持按来源、库、类型、名称/描述和 tags 筛选。查询 MUST 是只读的：MUST NOT 打开文件替换当前场景，MUST NOT 改变当前选择，MUST NOT 返回原始像素数组。

文件库查询 MUST 在 headless 子进程中完成，子进程退出后不留下场景状态；当前文件查询 MUST 只读 `bpy.data`，在没有 GUI 会话时返回 `asset_session_required`。`all` 来源 MUST 包含 DSH/用户/Essentials，但 MUST NOT 自动包含未保存的当前文件。

#### Scenario: 查询不改变当前场景

- **WHEN** 用户在有未保存修改的场景中执行 `asset_list` 或 `asset_get`
- **THEN** 当前场景的对象、选择状态和文件路径保持不变

#### Scenario: 当前文件查询无会话

- **WHEN** 没有可用的 Blender GUI 会话，用户查询 `source="current_file"`
- **THEN** 系统返回 `asset_session_required`，不尝试打开文件，也不返回空列表冒充成功

#### Scenario: tags 筛选

- **WHEN** 用户执行 `asset_list` 并传入多个 tags
- **THEN** 系统只返回同时具有这些 tags 的资产；tags 只用于筛选/展示，不参与资产身份判断

#### Scenario: 损坏文件不拖垮整次列表

- **WHEN** 扫描到一个损坏或不可读的 `.blend`
- **THEN** 该行标记 `readable=false` 和原因，其他资产仍正常返回

### Requirement: 资产缩略图

系统 SHALL 在保存资产时尽力生成 128×128 预览并随资产文件持久化。缩略图不可用时 MUST 返回 `embedded`、`missing` 或 `unreadable` 的明确状态，MUST NOT 用占位图伪装成真实预览。`asset_list` MUST NOT 返回像素；`asset_get` 最多返回一张临时 PNG。

#### Scenario: 对象资产缩略图持久化

- **WHEN** 用户保存对象资产，随后重新查询
- **THEN** 缩略图状态为 `embedded`，可读取 128×128 预览

#### Scenario: 空集合没有缩略图

- **WHEN** 用户保存没有可见内容的空集合
- **THEN** 回执和查询结果标记 `missing` / `empty_collection`，不生成伪造图片

#### Scenario: 详情只附图一张

- **WHEN** 用户执行 `asset_get` 请求缩略图
- **THEN** 回执最多带一张临时 PNG 路径；host 附图后清理，不把像素数组放进 JSON

### Requirement: 资产导入

系统 SHALL 默认以 append 方式把外部文件资产导入当前场景，并返回实际创建的 Blender 数据块名称。名称冲突时 MUST 返回源名到实际名的映射；集合导入 MUST 连同子对象一起导入。导入失败时 MUST 回滚本次新建的数据块，MUST NOT 留下部分导入。

当前文件资产 MUST 返回 `asset_already_in_scene`，直接使用现有数据块，不默认复制。在线/未下载资产 MUST 返回 `asset_not_local`，不访问网络。

#### Scenario: 对象名称冲突

- **WHEN** 导入名为 `Chair` 的对象资产，当前场景已有同名对象
- **THEN** 系统创建带 Blender 后缀的实际对象，并返回 `Chair` 到实际名称的映射

#### Scenario: 导入集合资产

- **WHEN** 导入包含子对象的集合资产
- **THEN** 系统导入集合及子对象，并返回全部实际名称

#### Scenario: 导入失败不产生部分场景

- **WHEN** 资产文件损坏、版本不兼容或导入过程中出错
- **THEN** 系统返回结构化错误，删除本次新建的数据块，当前场景不残留部分资产

#### Scenario: 当前文件资产直接使用

- **WHEN** 用户导入 `source="current_file"` 的资产
- **THEN** 系统返回 `asset_already_in_scene` 和现有 ID 名，不创建 `.001` 副本

### Requirement: 跨 Blender 版本兼容

系统 SHALL 在资产元数据/回执中记录来源 Blender 版本。实际读取因版本不兼容失败时，系统 MUST 返回结构化 `asset_version_unsupported`，包含路径、来源版本、当前版本和可执行提示；MUST NOT 声称成功，MUST NOT 把底层“不是 blend 文件”直接暴露。系统 MUST NOT 仅凭版本号预判不可读。

#### Scenario: 旧版 Blender 读取新版资产

- **WHEN** Blender 4.2 实际读取由 Blender 5.2 创建的资产并失败
- **THEN** 系统返回 `asset_version_unsupported`，不尝试导入，也不暴露底层错误

#### Scenario: 兼容版本读取

- **WHEN** 读取方 Blender 能成功读取资产文件
- **THEN** 系统正常返回元数据，并保留来源版本

### Requirement: Asset Browser 侧边栏与发送

系统 SHALL 在 Blender Asset Browser 的 `TOOL_PROPS` 区域提供 `DSH Assets` 侧栏，显示当前选中的资产（支持 1–20 个多选）并提供发送到 DSH 的操作；没有选中资产时 MUST 显示空态并禁用发送，MUST NOT 抛异常。超过 20 个时 MUST 禁用发送并提示，MUST NOT 静默丢弃。

侧栏 MUST 只读，MUST NOT 写场景、改变选择、生成缩略图或解析版本；保存/导入 MUST 走 `blender_op` 的 `asset` family。

#### Scenario: 显示全部选中资产

- **WHEN** 用户在 Asset Browser 中选中 DSH 库、用户库或 Essentials 的资产
- **THEN** 面板显示名称/类型/来源，发送按钮可用（数量 1–20）

#### Scenario: 无选中资产

- **WHEN** Asset Browser 没有选中资产
- **THEN** 面板显示空态，发送操作禁用，不产生错误

#### Scenario: 超过 20 个

- **WHEN** 用户选中 21 个资产
- **THEN** 面板禁用发送并提示最多 20 个，不截断后发送

#### Scenario: 发送资产到 DSH 草稿

- **WHEN** 用户点击发送
- **THEN** 系统通过现有 `/host/selection` 发送 `kind="asset"` 与 1–20 个 `AssetRef`，DSH 草稿出现对应引用，不新增 HTTP route

### Requirement: 选择载荷

系统 SHALL 扩展现有 `/host/selection` 支持 `kind="asset"`，载荷包含 1–20 个 `AssetRef` 和可选 tags 展示信息；MUST NOT 包含 sourceVersion、thumbnail 或原始像素。非法 source/id_type/长度、空数组或超过 20 个 MUST 整条拒绝，MUST NOT 猜测或部分接收。

#### Scenario: 合法多选

- **WHEN** 用户发送 2 个资产
- **THEN** 选择载荷 `kind="asset"`，`count=2`，`names` 对应两个资产，草稿文本包含其来源和导入提示

#### Scenario: 超限载荷

- **WHEN** 客户端发送 21 个资产或空数组
- **THEN** host 返回结构化拒绝，不写入选择存储

### Requirement: 操作目录与 per-op 元数据

系统 SHALL 在 `blender_op` 的 `asset` family 暴露 `asset_save`、`asset_list`、`asset_get`、`asset_import`、`asset_selftest`、`asset_help`，不新增模型工具。系统 SHALL 为每个 op 提供 `readonly`、`requires_session`、`write_scope` 元数据；`asset_list/get/help` MUST 是只读且不要求 GUI 会话（`current_file` 查询例外），并自动进入现有只读清单与租约豁免。

#### Scenario: 只读 op 不要求会话

- **WHEN** 别处持有写租约，用户调用 `asset_list` 查询 DSH 库
- **THEN** 查询不被租约拒绝，不需要 ready GUI 会话

#### Scenario: 写 op 需要会话

- **WHEN** 用户在没有会话时调用 `asset_save`
- **THEN** 系统返回结构化的会话缺失错误，不创建文件

#### Scenario: 未知资产操作

- **WHEN** 用户调用不存在的资产操作名
- **THEN** 系统返回结构化未知操作错误，并在可用时给出最接近的操作名

### Requirement: 空库、外部变更与错误契约

系统 SHALL 在资产库为空时返回空列表和库路径，而不是错误；刷新后 MUST 反映外部新增、删除或重命名。所有保存/导入失败 MUST 返回 `{ok:false, error, stage, hint, next?}`，MUST NOT 以 `ok:true` 掩盖失败。

#### Scenario: 空资产库

- **WHEN** 用户对刚创建的空库执行 `asset_list`
- **THEN** 系统返回空列表、库路径和下一步提示

#### Scenario: 外部新增后刷新

- **WHEN** 用户在 DSH 之外向库新增合法资产文件，然后再次查询
- **THEN** 新资产出现在列表中

#### Scenario: 保存失败可执行

- **WHEN** 资产库不可写
- **THEN** 系统返回 `asset_library_unavailable` 和检查权限/切换库的提示，不留下文件
