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

当请求 pass 时，系统必须（SHALL）为每一帧写一个 `manifest.json`，至少包含 pass 名、文件路径、分辨率、dtype、值域、颜色空间、坐标系、深度 near/far 或 normal 编码方式，以及 object_index→对象名映射。manifest 必须最后写入；文件缺失或解码失败的帧必须标记为 degraded，不得静默当作成功。manifest 必须足以让 `vision_pass` 直接解码 depth 为米、normal 为相机空间向量、object_index 为对象名；不得要求分析器按文件名、颜色分布或固定 8-bit 假设猜测格式。depth 必须声明 `noGeometryValue`（默认 0）与 `clampedAt`：near 映射到 1/65535、不 clamp，原始 0 只表示无几何。

#### Scenario: Depth manifest decodes to meters
- **WHEN** 请求 `depth`
- **THEN** manifest 给出 16-bit PNG 的 near/far、解码公式、`noGeometryValue:0` 与 `clampedAt:"far"`，调用方无需猜测值域

#### Scenario: Normal is camera space on every engine
- **WHEN** 请求 `normal` 且引擎为 Cycles（Normal pass 是世界空间）或 EEVEE
- **THEN** 写盘前统一做 `world @ R` 转换并重新归一化，manifest 声明的坐标系是相机空间，E2E 在地面法线上验证点积

#### Scenario: Object index maps to names
- **WHEN** 请求 `object_index` 且 `engine=CYCLES`
- **THEN** manifest 提供 index→对象名的稳定映射，回执不包含原始索引图数组

### Requirement: object_index declares its engine precondition

`object_index` 依赖 Cycles 的 Object Index 输出。系统必须（SHALL）只在该 pass 的输出真实存在时产出索引图；引擎为 EEVEE 或 Workbench 时必须失败并返回 `pass_unsupported`，`root_cause` 点名该 pass 与当前引擎，`fix` 明确提示改用 `CYCLES` 或去掉该 pass。不得静默省略该 pass，也不得产出全 0 或全 1 的索引图。该检查必须在 Blender 脚本内按实际 `scene.render.engine` 断言，不能只靠提交前的 socket 探测。该规则在 4.2 / 4.5 / 5.x 三支上一致（实测三版在 EEVEE 与 Workbench 下均无 Object Index 输出），属于引擎前置条件，不得实现为版本分叉。

#### Scenario: EEVEE rejects object_index
- **WHEN** 请求 `passes=["object_index"]` 且引擎为 `BLENDER_EEVEE` / `BLENDER_EEVEE_NEXT` / `BLENDER_WORKBENCH`
- **THEN** 提交失败并返回 `pass_unsupported`，`root_cause` 说明该 pass 需要 Cycles 并给出当前引擎，不产生部分产物

#### Scenario: Cycles produces object_index on every supported Blender
- **WHEN** 请求 `passes=["object_index"]` 且 `engine=CYCLES`，分别在本仓支持的 4.2 / 4.5 / 5.x 上
- **THEN** 每帧产出 16-bit 索引 PNG，`index = round(pixel*65535)`，非零索引与 `manifest.objectIndex.mapping` 一致

#### Scenario: Manifest is analyzable without guessing
- **WHEN** 调用方把一帧的 manifest 交给 `vision_pass`
- **THEN** `vision_pass` 能按 manifest 解码 depth/normal/object_index 并返回有界统计；字段缺失时显式失败，而不是猜格式或跳过该帧

### Requirement: Tool responses carry metadata, not raw pass arrays

`blender_run` 的 submit/status/collect 回执必须（SHALL）只包含 manifest 路径、pass 元数据、文件大小与摘要；不得把 depth/normal/object_index 的原始数组或 base64 图片塞进回执。需要像素分析时，调用方应把 manifest 路径交给 `vision_pass`（图像级分析仍用 `vision_analyze` / `vision_compare`）。

#### Scenario: Collect returns manifest references
- **WHEN** 带 pass 的 render 完成并执行 `collect`
- **THEN** 回执列出每帧的 manifest 路径和 pass 文件元数据，不包含像素数据

### Requirement: Cache key covers every render input

render pass 缓存必须（SHALL）以 scene 身份（规范化 .blend 路径、文件大小、mtime 与前 64 KiB 哈希）、frame、camera、resolution、samples、engine、pass set、beauty format 和 manifest 版本组成键；未保存场景或无法确定 scene 身份时不得命中缓存。任一键成分变化必须视为 miss，不得返回旧产物。

#### Scenario: Same inputs hit
- **WHEN** 两次提交完全相同的 scene/frame/camera/resolution/samples/engine/pass set
- **THEN** 第二次返回同一个 completed job、`cache_hit=true`，不启动 Blender、不占 GPU slot

#### Scenario: Changed file misses
- **WHEN** .blend 文件大小或 mtime 改变后再次提交相同参数
- **THEN** 返回 miss 并重新渲染，不复用旧缓存

### Requirement: Cache is bounded and self-cleaning

render pass 缓存必须（SHALL）遵守可配置上限：默认最多 8 条、总大小 4 GiB、TTL 7 天；按最近使用时间淘汰，超限或过期的条目在启动和写入后清理。提升前必须按帧预统计总字节，整单超过 `cache.render.maxBytes` 时不得写入缓存，但必须正常返回产物并附带 `cache_skipped` warning。缓存目录和索引写入必须原子化；staging 位于 `home/cache/render/.staging/`，不得清理 legacy `home/queue/staging` 的产物；命中时仍把帧复制到用户 `outdir` 并按 sha256 校验。

#### Scenario: LRU eviction
- **WHEN** 写入第 9 条缓存且总大小未超限
- **THEN** 最久未使用的条目被淘汰，新条目可读

#### Scenario: Oversized entry is not cached
- **WHEN** 一单 pass 产物的总字节超过 `cache.render.maxBytes`
- **THEN** 作业正常完成并返回产物，回执带 `skipped:"oversize"`，缓存索引不新增该条

#### Scenario: TTL expiry
- **WHEN** 某条缓存超过 7 天未被访问
- **THEN** 下一次清理删除该条，不再命中

### Requirement: Cache settings come from the documented config

缓存上限必须（SHALL）来自 `cache.render.maxEntries`、`cache.render.maxBytes`、`cache.render.ttlDays`；设置变更后在下一次 Gateway 启动时生效，并在启动时立即按新上限淘汰。设置值非法时不得在宿主侧自行截断，必须原值透传、使用文档默认值并在 `doctor` 的 `cache.render.issues[]` 报告字段、原值与允许范围。

#### Scenario: Reduced limit evicts on startup
- **WHEN** 缓存有 8 条，用户把 `maxEntries` 改为 4 并重启 Gateway
- **THEN** 启动后只保留最近使用的 4 条，其余条目被删除

#### Scenario: Invalid cache setting is visible
- **WHEN** `maxBytes` 低于文档下限
- **THEN** Gateway 不采用该值，`doctor` 报告字段、非法值与允许范围

### Requirement: Failed renders never poison the cache

render 作业失败、被取消或 manifest 未完成时，系统必须（SHALL）不写入或保留可命中的缓存条目；已存在的旧条目保持原样。`degraded:true`、非空 `degraded` 数组、或 `requestedPasses` 未被实际 `passes` 全覆盖的帧整单拒绝提升（产物留在 `home/jobs/<jobId>/`，回执 `skipped:"degraded"`）。缓存命中时如果发现文件缺失或 manifest 损坏，必须删除该条目并回退为 miss。

#### Scenario: Canceled render leaves no entry
- **WHEN** 带 pass 的 render 在写入过程中被取消
- **THEN** 不产生可命中条目，下一次同参数提交重新渲染

#### Scenario: Degraded frames are never promoted
- **WHEN** 某一帧的 manifest 标了 `degraded:["depth"]`
- **THEN** 整单不提升，帧留在 job 目录，回执 `skipped:"degraded"`；下一次同参数提交重新渲染

#### Scenario: Corrupt hit falls back
- **WHEN** 缓存索引指向的 manifest 不存在或无法解析
- **THEN** 该条目被删除，作业按 miss 重新渲染，回执说明缓存损坏
