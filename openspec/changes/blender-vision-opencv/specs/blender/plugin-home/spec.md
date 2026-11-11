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

插件激活或首次启动 Gateway 时，系统必须（SHALL）幂等创建 home 及其子目录 `logs/`、`state/`、`jobs/`、`sessions/`、`queue/`、`cache/`、`vision/`；已存在目录不得被清空或覆盖。创建失败必须返回结构化错误，不能静默降级到包目录或系统临时区。

#### Scenario: Fresh install creates the layout
- **WHEN** 插件第一次在干净机器上激活
- **THEN** home 及规定子目录存在，已有用户文件保持不变

#### Scenario: Unwritable home is explicit
- **WHEN** home 所在磁盘只读或权限不足
- **THEN** 插件返回 `home_unwritable`，包含路径、原因与修复建议，不把数据写到包目录

### Requirement: Persistent data lives under home

所有插件持久数据必须（SHALL）位于 home 下，包括配置、状态、作业记录、会话记录、队列、缓存、视觉 venv 与模型；插件不得把用户数据写入 npm 包目录、Blender 安装目录或系统 Python。系统临时目录只允许存放可丢弃的单次进程文件。

#### Scenario: Job artifacts use home
- **WHEN** 一个 render 或 headless 作业完成
- **THEN** 作业记录、日志和缓存引用指向 home 下的路径，`npm pack` 内容不增加用户数据

#### Scenario: Legacy workDir is not the new default
- **WHEN** 机器上存在旧版 `dsh-blender-rt` 工作目录
- **THEN** 新数据仍写入 home，旧目录只作为迁移来源被读取，且不会在未确认时删除

### Requirement: Settings are edited through the DSH config schema

插件必须（SHALL）在 DSH 插件配置 schema 中暴露 `features.dnn`、`cache.render.maxEntries`、`cache.render.maxBytes`、`cache.render.ttlDays`、`vision.python` 与 `vision.model`，使 DSH 插件设置 UI 可以编辑并校验它们；未设置时使用文档默认值。`features.dnn` 默认 `false`，是 DNN 检测的唯一开关；`DSH_BLENDER_DNN=off/on` 可覆盖生效值，`doctor` 与 `vision_status` 必须报告生效值和来源。

#### Scenario: Cache limit is editable
- **WHEN** 用户在 DSH 插件设置中把 `cache.render.maxEntries` 改为 4 并保存
- **THEN** 下一次 Gateway 启动使用 4 作为缓存条数上限，`doctor` 报告该值和配置来源

#### Scenario: Invalid setting is rejected
- **WHEN** 用户把 `cache.render.maxEntries` 设为 0 或 99
- **THEN** 设置校验失败并指出字段与允许范围，Gateway 不使用该值

#### Scenario: DNN is off by default
- **WHEN** 插件全新安装且用户未修改设置
- **THEN** `doctor` 报告 `features.dnn=false` 且来源为默认值，`vision_detect` 返回 `dnn_disabled`，不自动下载模型

#### Scenario: DNN switch checks models immediately
- **WHEN** 用户在 DSH 插件设置中打开 `features.dnn` 并保存，models 目录没有 `.onnx`
- **THEN** 配置卡立即显示下载列表，不等 Gateway 重启；确认前不下载任何模型

#### Scenario: DNN switch takes effect after restart
- **WHEN** 用户保存 `features.dnn=true` 后重启 Gateway
- **THEN** 下一次启动报告 `features.dnn=true`、来源为插件设置；打开开关本身不触发模型下载

### Requirement: Effective config has one documented precedence

系统必须（SHALL）按 `DSH_BLENDER_CONFIG` 显式文件 → home 内 `dsh-blender.config.json` → 包内默认配置 的顺序合并配置，DSH 插件设置中的值优先于文件值；环境变量中已有的单项覆盖规则保持不变。`doctor` 必须报告最终生效值及其来源，且不得回显密钥。

#### Scenario: Settings override file
- **WHEN** home 配置文件写 `cache.render.maxEntries=8`，DSH 设置写 4
- **THEN** 生效值为 4，`doctor` 报告来源为插件设置

#### Scenario: No config file still works
- **WHEN** 三个配置文件都不存在
- **THEN** 使用内置默认值启动，不创建项目级配置文件
