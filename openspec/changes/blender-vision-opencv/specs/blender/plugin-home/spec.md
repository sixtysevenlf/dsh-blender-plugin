# Spec Delta

## Purpose

定义插件唯一、可预测的持久数据根与配置入口，让用户级配置、缓存、作业状态和视觉运行时不再散落在包目录、系统临时区或 Blender 安装目录，并让 DSH 插件设置成为缓存的正式编辑入口。

## ADDED Requirements

### Requirement: Home is resolved deterministically

系统必须（SHALL）按固定优先级解析数据根：`DSH_BLENDER_HOME` 显式指定值最高；否则使用 `$DSH_HOME/dsh-blender`（`DSH_HOME` 未设置时使用 `~/.dsh/dsh-blender`）。解析结果必须在 `doctor` 与 `vision_status` 中报告来源，且同一进程内保持不变。

#### Scenario: Explicit home wins
- **WHEN** 用户设置 `DSH_BLENDER_HOME=D:/data/dsh-blender`
- **THEN** 所有持久数据写入该目录，`doctor` 报告 `home.source=env`

#### Scenario: Default home follows DSH_HOME
- **WHEN** 未设置 `DSH_BLENDER_HOME` 且 `DSH_HOME=C:/Users/me/.dsh`
- **THEN** 数据根为 `C:/Users/me/.dsh/dsh-blender`

### Requirement: Home is created idempotently

插件激活或首次启动 Gateway 时，系统必须（SHALL）幂等创建 home 及其子目录 `logs/`、`state/`、`jobs/`、`sessions/`、`queue/`、`cache/`、`vision/`；已存在目录不得被清空或覆盖。创建发生在插件 `apply()` 与 Gateway 启动时，导入配置本身不得创建目录。创建失败必须返回结构化错误（`doctor.config.home.layout = {code:"home_unwritable", path, reason, fix}`），不能静默降级到包目录或系统临时区；只读 op 继续可用，各写入方跳过自己的目录。

#### Scenario: Fresh install creates the layout
- **WHEN** 插件第一次在干净机器上激活
- **THEN** home 及规定子目录存在，已有用户文件保持不变

#### Scenario: Unwritable home is explicit
- **WHEN** home 所在磁盘只读或权限不足
- **THEN** 插件返回 `home_unwritable`，包含路径、原因与修复建议，不把数据写到包目录；`doctor` 原样转出该结构，`vision_status` 等只读 op 仍可调用

### Requirement: Persistent data lives under home

所有插件持久数据必须（SHALL）位于 home 下，包括配置、状态、作业记录、会话记录、队列、缓存、视觉 venv 与模型；插件不得把用户数据写入 npm 包目录、Blender 安装目录或系统 Python。系统临时目录只允许存放可丢弃的单次进程文件。

#### Scenario: Job artifacts use home
- **WHEN** 一个 render 或 headless 作业完成
- **THEN** 作业记录、日志和缓存引用指向 home 下的路径，`npm pack` 内容不增加用户数据

#### Scenario: Legacy workDir is not the new default
- **WHEN** 机器上存在旧版 `dsh-blender-rt` 工作目录
- **THEN** 新数据仍写入 home，旧目录只作为迁移来源被读取，且不会在未确认时删除

### Requirement: Settings are edited through the DSH config schema

插件必须（SHALL）在 DSH 插件配置 schema 中暴露 `features.dnn`、`cache.render.maxEntries`、`cache.render.maxBytes`、`cache.render.ttlDays`、`vision.python`、`vision.venv`、`vision.model`、`vision.dnn.backend` 与 `vision.dnn.device`，使 DSH 插件设置 UI 可以编辑并校验它们；未设置时使用文档默认值。`features.dnn` 默认 `false`，是 DNN 检测的唯一开关；DNN 字段保存后必须经 `$DSH_HOME/dsh-blender/vision/runtime.json` 热应用，不要求重启 Gateway；`cache.render.*` 仍在下一次 Gateway 启动生效。`doctor` 与 `vision_status` 必须报告生效值和来源。

#### Scenario: Cache limit is editable
- **WHEN** 用户在 DSH 插件设置中把 `cache.render.maxEntries` 改为 4 并保存
- **THEN** 下一次 Gateway 启动使用 4 作为缓存条数上限，`doctor` 报告该值和配置来源

#### Scenario: Invalid cache limit is reported, not clamped
- **WHEN** 用户把 `cache.render.maxEntries` 设为 0 或 99
- **THEN** 生效值回落到文档默认值，`doctor` 的 `cache.render.issues` 报出字段、原值与允许范围；宿主不把非法值悄悄改成默认值后当合法值传递

#### Scenario: Invalid env value is ignored
- **WHEN** 进程环境设置 `DSH_BLENDER_DNN=maybe`
- **THEN** 该值被忽略，生效值来自配置/默认，`doctor` 报 `dnn_env_invalid` 并带原值

#### Scenario: DNN is off by default
- **WHEN** 插件全新安装且用户未修改设置
- **THEN** `doctor` 报告 `features.dnn=false` 且来源为默认值，`vision_detect` 返回 `dnn_disabled`，不自动下载模型

#### Scenario: DNN switch checks models immediately
- **WHEN** 用户在 DSH 插件设置中打开 `features.dnn` 并保存，models 目录没有 `.onnx`
- **THEN** 配置卡立即显示下载列表，不等 Gateway 重启；确认前不下载任何模型

#### Scenario: DNN switch applies without restart
- **WHEN** 用户保存 `features.dnn=true`，Gateway 在线且没有视觉请求在途
- **THEN** 宿主写运行时快照并触发 reconcile；`doctor` 与 `vision_status` 报告 `features.dnn=true`、来源为插件设置，全程未重启 Gateway；打开开关本身不触发模型下载

#### Scenario: Busy worker defers a DNN config change
- **WHEN** 一个 `vision_detect` 在途，用户修改 `vision.dnn.backend` 并保存
- **THEN** 配置卡显示“等待当前检测结束”与 pending revision，不显示已生效；在途请求用旧配置完成，空闲后自动切换

#### Scenario: Unreachable Gateway is not shown as applied
- **WHEN** Gateway 不可达时用户点“保存并应用”
- **THEN** 配置卡显示 `gateway_unreachable`，不显示已生效，也不声称已热应用

### Requirement: DNN apply and probe are exposed in settings

DNN 分组必须（SHALL）提供“保存并应用”“检测可用性”“取消等待”“回滚上一版”入口，并显示 phase、activeRevision、pendingRevision、waitingFor 与 lastSwitchError。保存并应用必须写入候选 DNN 配置并调用 owner-only `/dsh-blender` RPC 的 `dnn.apply`；无在途请求时立即生效，有在途请求时登记 pending，配置卡不得把 pending 显示为已生效。检测可用性必须调用 `dnn.probe` 使用候选配置，逐项显示 pass/warn/fail 与 fix，不写快照、不改变运行中的 Gateway；保存并应用后必须自动再跑一次。

#### Scenario: Probe explains a missing dependency
- **WHEN** 候选配置为 `backend=onnxruntime`，自备 venv 缺少 `onnxruntime`
- **THEN** 检测结果显示该项 fail 与安装到该 venv 的 fix；不保存、不启动 worker、不改变当前生效配置

#### Scenario: Pending switch can be cancelled
- **WHEN** 配置卡显示 pending revision，用户点“取消等待”
- **THEN** 宿主用当前 active 配置写新 revision，取消 pending，配置卡恢复显示 active 已生效；在途请求不受影响

#### Scenario: Downloaded model is usable without restart
- **WHEN** 用户确认下载 `yolov5n`，宿主完成校验、写 labels 与 models.yml 条目
- **THEN** 配置卡提示可使用，下一次 `vision_detect` 直接用该模型，不要求重启 Gateway

### Requirement: DNN experimental status and DIY docs are discoverable

DSH 插件设置必须（SHALL）把 `features.dnn` 标为“实验性”，显示影响说明（YOLOv5/GPL-3.0、默认 CPU、结果可能不稳定、GPU 需自备环境），并提供指向 `https://github.com/sixtysevenlf/dsh-blender-plugin/blob/main/docs/vision-custom-backend.md` 的链接。home 初始化时必须（SHALL）在 `vision/models/GPU-DIY.md` 缺失时创建中英双语快捷说明，已存在时不得覆盖；`vision_status.dnn.docs` 必须返回该文件路径与同一 URL。

#### Scenario: Experimental badge and link are visible
- **WHEN** 用户打开 DSH 插件设置中的 DNN 分组
- **THEN** 开关旁显示“实验性”badge 与影响说明，并提供可点击的 GitHub 文档链接；不自动安装 ONNX Runtime 或 GPU 驱动

#### Scenario: Model directory has a bilingual quickstart
- **WHEN** 插件首次创建 home，且 `vision/models/GPU-DIY.md` 不存在
- **THEN** 创建包含中英文小节、默认 CPU、自备 venv/ONNX Runtime 步骤与完整文档链接的快捷说明；再次激活或文件已存在时不覆盖用户内容

### Requirement: Custom DNN backend settings are validated safely

`vision.venv` 默认空（使用托管环境），非空时必须是用户自备 venv 的绝对路径；`vision.dnn.backend` 默认 `opencv`，`vision.dnn.device` 默认 `cpu`。`backend=opencv` 与 `device=auto|gpu` 的组合必须（SHALL）被设置校验拒绝；`backend=onnxruntime` 必须配合非空 `vision.venv`，缺失时 `doctor`/`vision_status` 返回 `dnn_backend_unavailable`；插件不得安装 ONNX Runtime 或驱动。这些字段保存后必须热应用；需要换 worker 时按两阶段切换，配置卡显示 pending，不得中断在途请求。

#### Scenario: Custom backend requires a custom venv
- **WHEN** 用户设置 `vision.dnn.backend=onnxruntime` 但 `vision.venv` 为空
- **THEN** `doctor` 与 `vision_status` 报告 `dnn_backend_unavailable`，提示设置自备 venv 并安装 ORT，不自动安装

#### Scenario: Default inference remains CPU
- **WHEN** 用户启用 `features.dnn` 但未修改 backend/device/venv
- **THEN** `doctor` 与 `vision_status` 报告 `backend=opencv`、`device=cpu`，插件不 import onnxruntime

### Requirement: Effective config has one documented precedence

系统必须（SHALL）按“内置默认 → 包内默认 → 旧用户配置 `~/.dsh/dsh-blender.config.json` → home 内 `dsh-blender.config.json` → `DSH_BLENDER_CONFIG` 显式文件 → DSH 插件设置 → 单项环境变量”的顺序合并配置，并记录每个生效值的来源；DSH 插件设置中的值优先于文件值，插件设置只序列化与默认值不同的键，未改动的字段不得遮蔽 home 配置文件。DNN 字段保存并应用后，`$DSH_HOME/dsh-blender/vision/runtime.json` 的当前 revision 优先于启动时写入的 `DSH_BLENDER_PLUGIN_CONFIG`，但 `DSH_BLENDER_DNN=off/on` 始终最高并锁定 `features.dnn`。`doctor` 必须报告最终生效值及其来源，且不得回显密钥。

#### Scenario: Untouched plugin setting does not shadow home
- **WHEN** home 配置写 `features.dnn=true`、`vision.model=mymodel`，用户从未改过插件设置
- **THEN** 生效值来自 home，`doctor` 的来源为 `home`（或未写时为 `default`），不是 `plugin`

#### Scenario: Settings override file
- **WHEN** home 配置文件写 `cache.render.maxEntries=8`，DSH 设置写 4
- **THEN** 生效值为 4，`doctor` 报告来源为插件设置

#### Scenario: Runtime snapshot wins for DNN fields
- **WHEN** 启动时 `DSH_BLENDER_PLUGIN_CONFIG` 写 `vision.dnn.backend=opencv`，用户随后保存并应用 `onnxruntime`
- **THEN** 生效值为 `onnxruntime`，`doctor` 报告来源为运行时快照，直到再次应用或下次启动

#### Scenario: Env lock overrides the runtime snapshot
- **WHEN** 进程环境设置 `DSH_BLENDER_DNN=off`，而运行时快照写 `features.dnn=true`
- **THEN** 生效值为 `false`，配置卡显示 env 锁定，`dnn.apply` 返回 `dnn_env_locked`，不覆盖 `features.dnn`

#### Scenario: No config file still works
- **WHEN** 三个配置文件都不存在
- **THEN** 使用内置默认值启动，不创建项目级配置文件
