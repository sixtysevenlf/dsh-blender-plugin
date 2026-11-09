# Spec Delta

## Purpose

定义 `blender_run(kind=render)` 的可选渲染 pass、每帧 manifest 与有界缓存，让渲染产物成为可复核、可复用、可被视觉分析消费的稳定数据集，同时保持未请求 pass 时的旧行为不变。

## ADDED Requirements

### Requirement: Render passes are opt-in and declarative

`blender_run(kind=render)` 必须（SHALL）接受可选的 `passes` 数组，允许值为 `beauty`、`depth`、`normal`、`object_index`、`alpha`。未提供 `passes` 时只输出 beauty 并保持 3.1 的旧产物行为；未知 pass、重复 pass 或空数组必须拒绝并列出允许值。render 仍然只接受声明式参数，不得接受 script。

#### Scenario: Default render is unchanged
- **WHEN** 提交 render 且不传 `passes`
- **THEN** 产物与 3.1 的 render 相同，不要求 manifest，也不写 pass 缓存

#### Scenario: Unknown pass is rejected
- **WHEN** 提交 `passes=["depth","cryptomatte"]`
- **THEN** 提交失败，回执列出 `beauty/depth/normal/object_index/alpha`，不启动 Blender

### Requirement: Pass artifacts are self-describing

当请求 pass 时，系统必须（SHALL）为每一帧写一个 `manifest.json`，至少包含 pass 名、文件路径、分辨率、dtype、值域、颜色空间、坐标系、深度 near/far 或 normal 编码方式，以及 object_index→对象名映射。manifest 必须最后写入；文件缺失或解码失败的帧必须标记为 degraded，不得静默当作成功。

#### Scenario: Depth manifest decodes to meters
- **WHEN** 请求 `depth`
- **THEN** manifest 给出 16-bit PNG 的 near/far 与解码公式，调用方无需猜测值域

#### Scenario: Object index maps to names
- **WHEN** 请求 `object_index`
- **THEN** manifest 提供 index→对象名的稳定映射，回执不包含原始索引图数组

### Requirement: Tool responses carry metadata, not raw pass arrays

`blender_run` 的 submit/status/collect 回执必须（SHALL）只包含 manifest 路径、pass 元数据、文件大小与摘要；不得把 depth/normal/object_index 的原始数组或 base64 图片塞进回执。需要像素分析时，调用方应把 manifest 路径交给 `vision_*`。

#### Scenario: Collect returns manifest references
- **WHEN** 带 pass 的 render 完成并执行 `collect`
- **THEN** 回执列出每帧的 manifest 路径和 pass 文件元数据，不包含像素数据

### Requirement: Cache key covers every render input

render pass 缓存必须（SHALL）以 scene 身份（规范化 .blend 路径、文件大小与 mtime）、frame、camera、resolution、samples、engine、pass set 和 manifest 版本组成键；未保存场景或无法确定 scene 身份时不得命中缓存。任一键成分变化必须视为 miss，不得返回旧产物。

#### Scenario: Same inputs hit
- **WHEN** 两次提交完全相同的 scene/frame/camera/resolution/samples/engine/pass set
- **THEN** 第二次返回同一个 completed job、`cache_hit=true`，不启动 Blender、不占 GPU slot

#### Scenario: Changed file misses
- **WHEN** .blend 文件大小或 mtime 改变后再次提交相同参数
- **THEN** 返回 miss 并重新渲染，不复用旧缓存

### Requirement: Cache is bounded and self-cleaning

render pass 缓存必须（SHALL）遵守可配置上限：默认最多 8 条、总大小 4 GiB、TTL 7 天；按最近使用时间淘汰，超限或过期的条目在启动和写入后清理。单条超过 2 GiB 时不得写入缓存，但必须正常返回产物并附带 `cache_skipped` warning。缓存目录和索引写入必须原子化。

#### Scenario: LRU eviction
- **WHEN** 写入第 9 条缓存且总大小未超限
- **THEN** 最久未使用的条目被淘汰，新条目可读

#### Scenario: Oversized entry is not cached
- **WHEN** 单条 pass 产物超过 2 GiB
- **THEN** 作业正常完成并返回产物，回执带 `cache_skipped=oversize`，缓存索引不新增该条

#### Scenario: TTL expiry
- **WHEN** 某条缓存超过 7 天未被访问
- **THEN** 下一次清理删除该条，不再命中

### Requirement: Cache settings come from the documented config

缓存上限必须（SHALL）来自 `cache.render.maxEntries`、`cache.render.maxBytes`、`cache.render.ttlDays`；设置变更后在下一次 Gateway 启动时生效，并在启动时立即按新上限淘汰。设置值非法时不得自行截断，必须拒绝或使用文档默认值并在 `doctor` 报告。

#### Scenario: Reduced limit evicts on startup
- **WHEN** 缓存有 8 条，用户把 `maxEntries` 改为 4 并重启 Gateway
- **THEN** 启动后只保留最近使用的 4 条，其余条目被删除

#### Scenario: Invalid cache setting is visible
- **WHEN** `maxBytes` 低于文档下限
- **THEN** Gateway 不采用该值，`doctor` 报告字段、非法值与允许范围

### Requirement: Failed renders never poison the cache

render 作业失败、被取消或 manifest 未完成时，系统必须（SHALL）不写入或保留可命中的缓存条目；已存在的旧条目保持原样。缓存命中时如果发现文件缺失或 manifest 损坏，必须删除该条目并回退为 miss。

#### Scenario: Canceled render leaves no entry
- **WHEN** 带 pass 的 render 在写入过程中被取消
- **THEN** 不产生可命中条目，下一次同参数提交重新渲染

#### Scenario: Corrupt hit falls back
- **WHEN** 缓存索引指向的 manifest 不存在或无法解析
- **THEN** 该条目被删除，作业按 miss 重新渲染，回执说明缓存损坏
