# Spec Delta

## Purpose

定义 Gateway 直属的本地视觉运行时：它用一个隔离的 Python/OpenCV worker 把图片变成可复核数字，向 `blender_op` 提供不依赖 GUI 会话的读取、配准、检测与序列分析能力，同时把安装、许可和失败边界说清楚。

## ADDED Requirements

### Requirement: The vision worker is a private Gateway child

Gateway 必须（SHALL）拥有至多一个视觉 worker 子进程，通过 stdin/stdout 或私有管道通信；该进程不得监听 TCP 端口，不得启动 Blender，不得调用 bpy。worker 崩溃、超时或协议错乱时，Gateway 必须在不影响主进程和其他作业的前提下结束它，并按有界次数重启。

#### Scenario: Worker has no listener
- **WHEN** worker 处于 ready 状态
- **THEN** 进程不拥有任何 listening socket，`blender_op(op="vision_status")` 报告进程 id、启动时间与协议版本

#### Scenario: Worker crash is isolated
- **WHEN** 一个视觉请求执行中 worker 异常退出
- **THEN** 该请求返回结构化失败，Gateway 仍可服务其他工具，随后最多重启一次并重新校验

### Requirement: OpenCV runs in an isolated managed venv

系统必须（SHALL）把 OpenCV 安装到 `$DSH_HOME/dsh-blender/vision/venv`，不得把 `cv2` 装进 Blender Python，也不得对系统 Python 执行全局 pip。支持 Python 3.11、3.12、3.13，优先 3.13；没有可用解释器时，必须返回带 `stage`、`root_cause`、`fix`、`fallback` 的结构化失败，而不是静默禁用视觉。

#### Scenario: First install is automatic
- **WHEN** 插件首次激活且机器存在受支持的 Python
- **THEN** 后台创建 venv、安装 headless OpenCV 与 NumPy、执行一次导入和小轮廓校验，依次经过 `resolving_python`、`creating_venv`、`installing`、`verifying`、`ready`

#### Scenario: No supported Python
- **WHEN** 机器上没有 3.11–3.13 的系统 Python，且托管解释器也不可用
- **THEN** `vision_status` 报告 `python_missing`，给出安装版本与命令，且 `vision_*` 返回 `vision_unavailable` 而非成功空结果

#### Scenario: Install is resumable
- **WHEN** 安装过程中网络中断或进程被杀
- **THEN** 下次启动从上次阶段继续或重试，已完成的 venv/轮子不重复下载，失败不会留下半可用的 ready 状态

### Requirement: Vision ops are host-local and session-free

`blender_op` 必须（SHALL）暴露 `vision_status`、`vision_analyze`、`vision_compare`、`vision_detect`、`vision_track`、`vision_selftest` 和 `vision_help` 七个 op。它们全部是 host-local、read-only，不需要 GUI 会话，不得触发 Blender 启动、lease 或虚拟显示。

#### Scenario: Vision works without Blender
- **WHEN** Gateway 在线、Blender 未连接，调用 `blender_op(op="vision_analyze", args={path:"ref.png"})`
- **THEN** 请求由 Gateway 的视觉路径处理并返回数字结果，不返回 `stage=session`

#### Scenario: Catalog lists the family
- **WHEN** 调用 `blender_op(op="catalog")`
- **THEN** catalog 包含 `vision` 家族与上述七个 op，且该家族的会话要求为 host-local

### Requirement: Analysis returns bounded numbers, never raw arrays

`vision_analyze`、`vision_compare` 与 `vision_track` 必须（SHALL）只返回标量、小数组、路径和 manifest 引用；不得在工具回执中返回完整像素、完整 mask、完整光流场或逐帧原始数组。所有数值必须带单位、坐标系或方法说明，无法计算的字段返回 `null` 并说明原因。

#### Scenario: Analyze a reference image
- **WHEN** 对一张参考图调用 `vision_analyze`
- **THEN** 回执包含尺寸、颜色统计、清晰度/曝光指标、前景轮廓的 bbox/面积/周长与限量的线条/连通域摘要，不包含像素矩阵

#### Scenario: Compare render to reference
- **WHEN** 调用 `vision_compare` 比较渲染图与参考图
- **THEN** 回执包含配准变换、IoU、差异统计、变化 bbox 与使用的对齐方法；原始差异图只以 home 下的预览路径出现

### Requirement: DNN detection is an opt-in feature flag

`features.dnn` 必须（SHALL）是插件配置中的布尔开关，默认 `false`，并在 DSH 插件设置中可见；Gateway 可以读取 `DSH_BLENDER_DNN=off/on` 覆盖，`vision_status` 必须报告生效值与来源。该开关关闭时，`vision_detect` 必须在启动 worker 之前返回 `stage=config`、`code=dnn_disabled`、`retryable=false`，并给出启用步骤；不得自动启用、不得下载模型、不得改用其他检测路径。关闭开关不得删除已下载模型，也不得影响 `vision_analyze`、`vision_compare`、`vision_track` 等不依赖 DNN 的 op。

#### Scenario: Detection is off by default
- **WHEN** 全新安装后未改设置，调用 `vision_detect`
- **THEN** 回执为 `stage=config`、`code=dnn_disabled`，不启动 worker、不发起网络请求，修复说明指向 DSH 插件设置

#### Scenario: Enabling does not download
- **WHEN** 用户只打开 `features.dnn`，models 目录没有任何 `.onnx`
- **THEN** 功能保持开启，但不自动下载；`vision_detect` 返回 `dnn_no_model`，配置卡提示选择预设

#### Scenario: Selftest skips DNN when unavailable
- **WHEN** `features.dnn=false` 或没有可用模型时调用 `vision_selftest`
- **THEN** DNN 检查标记为 skipped，其他自检照常执行，不把 DNN 缺失算作 selftest 失败

### Requirement: Detection models are declared by OpenCV 5 models.yml

`vision_detect` 必须（SHALL）从 `$DSH_HOME/dsh-blender/vision/models/models.yml` 读取模型条目，key 是模型 id；条目字段遵循 OpenCV 5 `samples/dnn/models.yml` 语义：`model`、可选 `mean`/`scale`/`rgb`、`width`、`height`、可选 `labels`、`postprocessing`、可选 `background_label_id`。`model`、`width`、`height`、`postprocessing` 必填；`mean`/`scale`/`rgb` 省略时使用 OpenCV DNN 默认值（mean 0、scale 1、rgb false）；`postprocessing` 本期只接受 `yolov5`。`labels` 可选，省略时只返回数字 class id；存在时必须一行一个类名、UTF-8、无表头、行号即 class id，忽略文件末尾的单个换行。

#### Scenario: OpenCV-style entry is used directly
- **WHEN** models.yml 的 `yolov5n` 条目写有 `model`、`width`、`height`、`rgb`、`mean`、`scale`、`labels` 与 `postprocessing: yolov5`
- **THEN** worker 用 `cv.FileStorage` 读取这些字段并按它们预处理，不按模型名猜 mean/scale/layout

#### Scenario: YOLOv5 output rows follow the input size
- **WHEN** models.yml 的 `width`/`height` 不是 640，ONNX 输出为 `(1,N,85)`
- **THEN** 按实际 N 解码，不假设固定的 25200 行，也不返回空检测

### Requirement: Users can extend the model directory

模型目录必须（SHALL）允许用户扩展：用户把 `.onnx` 放入 models 目录并添加一条 models.yml 条目后，无需改代码即可通过模型 id 使用；`vision_status` 必须同时列出已配置模型与未配置的 `.onnx`。`vision_detect` 的 `model` 参数只接受 id，不接受路径或 URL；models.yml 中的 `model` 字段与可选的 `labels` 字段必须是 models 目录内的相对路径，解析后必须仍在目录内，拒绝绝对路径、`..`、URL 与逃出目录的 symlink。宿主不得解析 YAML；worker 使用 `cv.FileStorage` 读取。

#### Scenario: User adds a model without code changes
- **WHEN** 用户把 `my-detector.onnx` 与 `my-detector.txt` 放入 models 目录，并在 models.yml 增加 `my-detector` 条目
- **THEN** `vision_status` 列出该模型，`vision_detect(args={path:"frame.png", model:"my-detector"})` 按条目中的 width/height/mean/scale/labels 推理并返回带 label 的检测

#### Scenario: Model id is not a path
- **WHEN** 调用方传入 `../outside.onnx`、绝对路径或 URL 作为 `model`
- **THEN** 参数校验拒绝并返回 `vision_model_unsupported`，不读取 models 目录之外的文件

### Requirement: Model configuration errors are explicit

条目缺失、models.yml 无法解析、必填字段缺失，或 model 文件/labels 文件缺失、labels 行数不匹配时，`vision_detect` 必须（SHALL）返回 `vision_model_unconfigured`，并给出文件、key（能取得行号时带行号）与可复制示例；不自动猜 preprocessing，不静默降级到其他模型。错误语义必须稳定：models 目录没有任何 `.onnx` 时返回 `dnn_no_model`；请求 id 有同名 `.onnx` 但无条目、条目不完整或 model 文件缺失时返回 `vision_model_unconfigured`；请求 id 无同名 `.onnx` 且不在 models.yml 时返回 `vision_model_missing` 并列出可用 id；`postprocessing` 不是 `yolov5`、输出形状不匹配或模型 id 含路径/URL 时返回 `vision_model_unsupported`。

#### Scenario: Unconfigured ONNX is explicit
- **WHEN** models 目录有 `my-detector.onnx`，但 models.yml 没有对应条目
- **THEN** 回执为 `vision_model_unconfigured`，列出缺失字段与可复制示例，不加载该文件、不猜预处理

#### Scenario: Missing models are distinguishable
- **WHEN** `features.dnn=true`，models 目录完全为空
- **THEN** 回执为 `dnn_no_model`，而不是 `vision_model_unconfigured` 或空检测结果

### Requirement: YOLOv5 presets are listed when no model is configured

`features.dnn` 已启用（包括开关刚保存、Gateway 尚未重启）且 models 目录没有任何 `.onnx` 时，DSH 插件设置必须（SHALL）显示可下载预设的复选框列表：仅 YOLOv5 v7.0 的 `n/s/m/l/x`，默认勾选 `n`，并展示每个文件的大小、来源与 GPL-3.0。列表只展示可下载项，不自动开始下载。

#### Scenario: Enable with no models offers presets
- **WHEN** 用户打开 `features.dnn` 且 models 目录为空
- **THEN** 配置卡显示 `yolov5n` 默认勾选、`yolov5s/m/l/x` 可多选的列表，未确认前不下载任何文件

### Requirement: Preset downloads require explicit human confirmation

只有人类确认后，宿主才可以下载；下载 URL、字节数与 sha256 必须（SHALL）固定在宿主代码中，不读 models.yml，不随 npm 分发。下载必须写 `.part`、校验 sha256 与字节数、原子 rename，并写入 labels 与 models.yml 条目；已有同名 key 不得覆盖。

#### Scenario: Human confirms the download
- **WHEN** 用户勾选 `yolov5n` 与 `yolov5s` 并确认
- **THEN** 宿主依次下载、校验、原子落盘，写 `coco80.txt` 与 models.yml 条目；完成并重启 Gateway 后 `vision_detect` 可使用这两个模型

### Requirement: Declining a preset download keeps detection disabled

用户点“暂不下载”时，系统必须（SHALL）保持 `features.dnn` 开启、不得删除已有模型，并在设置页保留警告与下载入口。此后 `vision_detect` 返回 `dnn_no_model`（没有 `.onnx`）或 `vision_model_unconfigured`（有 `.onnx` 但未配置）；agent 不得自动下载、不得在调用中触发下载、不得改用路径/URL 或其他模型兜底。worker 不得发起任何网络请求。

#### Scenario: Decline keeps the feature on
- **WHEN** `features.dnn` 已生效，用户点“暂不下载”
- **THEN** `features.dnn` 仍为 true，`vision_detect` 返回 `dnn_no_model`，回执只给出人工下载/放置步骤；不自动下载、不静默改用其他模型

### Requirement: Vision failures are actionable

所有视觉失败必须（SHALL）返回 `stage`、`code`、`root_cause`、`fix`、`fallback` 与 `evidence`。`stage` 至少区分 `config`（home/Python/设置）、`vision`（worker 协议/崩溃）与 `input`（图片/参数）；不得把安装失败、图片解码失败或模型缺失折叠成 `backend_error` 或空结果。

#### Scenario: Corrupt image is input failure
- **WHEN** 图片路径存在但不是可解码图片
- **THEN** 回执为 `stage=input`、`code=vision_invalid_image`，包含路径与解码错误，不回退到 Blender

#### Scenario: Install failure names the step
- **WHEN** OpenCV 轮子安装失败
- **THEN** 回执为 `stage=config`、`code=opencv_install_failed`，包含失败的阶段、日志路径、代理/镜像建议和离线安装 fallback

### Requirement: Vision runtime is read-only and sandboxed

视觉 worker 必须（SHALL）只读取调用方提供的图片路径和 home 下的模型/缓存，只写入 home 下的缓存、预览和日志；不得执行调用方 Python、不得发起任何网络请求、不得读取与请求无关的用户文件。模型下载只能由宿主在人类确认后执行，worker 不参与下载。图片解码必须有尺寸与像素上限，超限返回 `vision_image_too_large`。

#### Scenario: No arbitrary code
- **WHEN** 调用方尝试通过 `vision_analyze` 传入脚本、URL 或模型路径
- **THEN** 参数校验拒绝该字段，worker 不执行任何调用方代码

#### Scenario: Oversized image is rejected
- **WHEN** 图片解码后的像素数超过文档上限
- **THEN** 返回 `vision_image_too_large`，包含尺寸与上限，不进行部分分析
