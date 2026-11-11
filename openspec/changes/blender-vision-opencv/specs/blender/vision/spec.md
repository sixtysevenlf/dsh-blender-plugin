# Spec Delta

## Purpose

定义 Gateway 直属的本地视觉运行时：它用一个隔离的 Python/OpenCV worker 把图片变成可复核数字，向 `blender_op` 提供不依赖 GUI 会话的读取、配准、检测、序列运动、序列稳定性与 render manifest 语义分析能力，同时把安装、许可和失败边界说清楚。

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

`blender_op` 必须（SHALL）暴露 `vision_status`、`vision_analyze`、`vision_compare`、`vision_detect`、`vision_track`、`vision_pass`、`vision_selftest` 和 `vision_help` 八个 op。它们全部是 host-local、read-only，不需要 GUI 会话，不得触发 Blender 启动、lease 或虚拟显示。

#### Scenario: Vision works without Blender
- **WHEN** Gateway 在线、Blender 未连接，调用 `blender_op(op="vision_analyze", args={path:"ref.png"})`
- **THEN** 请求由 Gateway 的视觉路径处理并返回数字结果，不返回 `stage=session`

#### Scenario: Catalog lists the family
- **WHEN** 调用 `blender_op(op="catalog")`
- **THEN** catalog 包含 `vision` 家族与上述八个 op，且该家族的会话要求为 host-local

### Requirement: Analysis returns bounded numbers, never raw arrays

`vision_analyze`、`vision_compare` 与 `vision_track` 必须（SHALL）只返回标量、小数组、路径和 manifest 引用；不得在工具回执中返回完整像素、完整 mask、完整光流场或逐帧原始数组。所有数值必须带单位、坐标系或方法说明，无法计算的字段返回 `null` 并说明原因。

#### Scenario: Analyze a reference image
- **WHEN** 对一张参考图调用 `vision_analyze`
- **THEN** 回执包含尺寸、颜色统计、清晰度/曝光指标、前景轮廓的 bbox/面积/周长与限量的线条/连通域摘要，不包含像素矩阵

#### Scenario: Compare render to reference
- **WHEN** 调用 `vision_compare` 比较渲染图与参考图
- **THEN** 回执包含配准变换、IoU、差异统计、变化 bbox 与使用的对齐方法；原始差异图只以 home 下的预览路径出现

### Requirement: Sequence analysis reports stability and reference deviation

`vision_track` 必须（SHALL）在逐帧运动/轨迹之外，返回序列稳定性摘要：相邻帧位移与亮度差的鲁棒统计、闪烁/抖动/跳变的计数或分数；当调用方提供参考序列时，返回同长度轨迹的 RMSE 与最大偏差，缺失帧必须显式列出而不是按 0 处理。所有摘要必须有界，不得返回完整光流场或逐帧原始图像。

#### Scenario: Stable sequence has low jitter
- **WHEN** 对一段静态或匀速序列调用 `vision_track`
- **THEN** 回执给出 jitter/flicker/jump 指标与每帧轨迹摘要，不返回光流矩阵

#### Scenario: Reference sequence is explicit
- **WHEN** `vision_track` 提供长度与待测序列不一致的参考序列
- **THEN** 回执返回 `stage=input` 的结构化错误或显式缺失帧列表，不静默截断、不把缺帧当 0

### Requirement: Pass analysis consumes render manifests

`vision_pass` 必须（SHALL）读取 1–240 个 `manifest.json`，按 manifest 声明的 dtype、值域、颜色空间与解码公式处理 `depth`、`normal`、`object_index` 和 `alpha`，不得按文件名、颜色分布或固定 8-bit 假设猜测格式。depth 必须以米返回范围与分位数；normal 必须以相机空间单位向量返回均值/角分布；object_index 必须按 manifest 的 index→对象名映射返回每对象可见像素、占画面比例、bbox 与前后顺序，并明确前后顺序不是隐藏面重建。

#### Scenario: Depth is decoded to meters
- **WHEN** manifest 声明 16-bit depth PNG、near/far 与解码公式
- **THEN** `vision_pass` 用该公式返回米制 min/max/分位数与 ROI 统计，不把 0–65535 原值当米

#### Scenario: Object coverage is visible-only
- **WHEN** manifest 的 object_index 包含多个对象
- **THEN** 回执列出每对象可见像素/占比/bbox 与按中位深度的前后顺序，并注明这是可见覆盖与顺序，不是精确遮挡或穿透深度

### Requirement: Pass analysis is bounded and fails explicitly

回执必须（SHALL）逐帧有界并给出跨帧聚合、缺帧/越界警告；不得返回原始深度、法线、索引图或逐帧大数组。manifest 缺 pass、字段不完整、尺寸不一致或文件损坏时返回 `stage=input`、`code=vision_pass_manifest_invalid`；请求的 pass 不在 manifest 中返回 `vision_pass_unsupported`；单次最多 240 个 manifest、64 个对象，超限截断并 warning。

#### Scenario: Invalid manifest fails explicitly
- **WHEN** manifest 缺少 depth 的 near/far 或 normal 编码
- **THEN** 回执为 `stage=input`、`code=vision_pass_manifest_invalid`，不猜格式、不跳过该帧后返回成功

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

### Requirement: DNN configuration is hot-applied without a Gateway restart

DNN 相关配置（`features.dnn`、`vision.python`、`vision.venv`、`vision.model`、`vision.dnn.backend`、`vision.dnn.device`）必须（SHALL）由宿主写入 `$DSH_HOME/dsh-blender/vision/runtime.json`（原子写、不含密钥），Gateway 在 `/doctor` 与每次 `vision_*` 请求边界读取并应用，不要求重启 Gateway。快照必须带 opaque revision；`vision_status.dnn.config` 必须报告 active/pending revision、phase、waitingFor 与 lastSwitchError。`DSH_BLENDER_DNN=off/on` 必须优先并锁定 `features.dnn`，快照不得覆盖 env。Gateway 不可达时宿主必须报告 `gateway_unreachable`，不得声称配置已生效。

#### Scenario: Idle save applies without restart
- **WHEN** 没有视觉请求在途，用户修改 `vision.venv` 并保存
- **THEN** 宿主写快照并触发 Gateway reconcile；下一次 `vision_detect` 使用新 venv，`vision_status.dnn.config.activeRevision` 等于新 revision，全程未重启 Gateway

#### Scenario: Env lock wins
- **WHEN** 进程环境设置 `DSH_BLENDER_DNN=on`，用户在配置卡保存 `features.dnn=false`
- **THEN** 配置卡显示 env 锁定，`dnn.apply` 返回 `dnn_env_locked`，快照不覆盖 `features.dnn`，检测仍按 env 的 on 生效

#### Scenario: Unreachable Gateway is not reported applied
- **WHEN** Gateway 不可达时用户点“保存并应用”
- **THEN** 回执为 `gateway_unreachable`、`applied=false`、`savedForNextStart=true`，配置卡不显示已生效

### Requirement: DNN config switching never interrupts in-flight work

需要换 worker 的 DNN 配置变更（`vision.python`、`vision.venv`、`vision.dnn.backend`、`vision.dnn.device`）必须（SHALL）两阶段提交：没有 worker-backed 请求在途时立即切换；有 `vision_analyze`/`vision_compare`/`vision_detect`/`vision_track`/`vision_selftest` 在途时只登记 pending，在途请求继续用旧 revision 完成，归零后才提交。pending 期间新的 worker-backed 请求必须返回 `stage=config`、`code=dnn_config_switching`、`retryable=true`，并带 active/pending revision 与 waitingFor。仅 `features.dnn` 或 `vision.model`/models.yml 变化不得重启 worker。

#### Scenario: Detection in flight defers the switch
- **WHEN** 一个 `vision_detect` 正在执行，用户把 `backend` 从 `opencv` 改为 `onnxruntime`
- **THEN** 该检测用旧 revision 完成并带旧 `configRevision`；新的 `vision_analyze` 返回 `dnn_config_switching`；检测结束后 pending 自动提交，下一次请求使用新配置

#### Scenario: Feature toggle does not restart the shared worker
- **WHEN** 用户把 `features.dnn` 从 true 改为 false，同时有 `vision_analyze` 在途
- **THEN** 新的 `vision_detect` 立即返回 `dnn_disabled`；在途 `vision_analyze` 继续用旧 worker 完成；`vision_analyze` 后续仍可使用共享 worker，不因该开关被中断

#### Scenario: Model-only change is immediate
- **WHEN** 用户只修改 `vision.model` 或 models.yml，没有换 venv/backend/device
- **THEN** 配置立即提交并失效模型缓存，不产生 `dnn_config_switching`，在途请求不受影响

### Requirement: DNN config switch failures are explicit and recoverable

旧 worker 无法优雅关闭时必须（SHALL）保持旧 active、`phase=failed`、`code=dnn_config_switch_failed`，保留 pending 供重试/取消，新的 worker-backed 请求返回该错误，不得落到半关闭进程或写半个配置。新配置提交后 worker 启动失败时必须保留新 active，`vision_status` 报告 `worker.lastStartError` 与 `previous` 配置，请求返回 `vision_custom_env_invalid`/`dnn_backend_unavailable`/`dnn_device_unavailable`；不得静默回退旧配置。宿主必须支持覆盖 pending、取消 pending 与显式回滚上一版。

#### Scenario: Stop failure keeps the old active
- **WHEN** 切换时旧 worker 在 2 秒内未退出且杀进程树失败
- **THEN** 回执与状态为 `dnn_config_switch_failed`，active 仍是旧 revision，pending 保留；新的 worker-backed 请求返回可重试错误，不启动半个新配置

#### Scenario: Startup failure offers explicit rollback
- **WHEN** 新配置提交后 worker 因自定义 venv 缺少 ORT 而启动失败
- **THEN** active 保持新 revision，`vision_status.worker.lastStartError` 指向 `dnn_backend_unavailable`，并返回 `previous` 配置供用户显式回滚；不自动切回旧配置

#### Scenario: Invalid config is not committed
- **WHEN** 保存的候选配置字段非法或类型错误
- **THEN** 快照不被接受，`phase=failed`、`code=dnn_config_invalid`，active 不变

### Requirement: DNN availability probe is non-mutating

DSH 配置卡的“检测可用性”必须（SHALL）经 owner-only `/dsh-blender` RPC 的 `dnn.probe` 使用候选配置启动一次性宿主侧 probe；probe 不得写 runtime 快照、不得改变运行中的 Gateway、不得启动常驻 worker、不得加载 ONNX/创建推理 session/分配 GPU/联网/下载/安装/写模型目录，超时 10 秒。结果必须逐项返回 `pass`/`warn`/`fail` 与 `fix`，至少覆盖解释器版本、cv2/numpy、backend 的 onnxruntime import、provider 与 device、models 目录/.onnx/models.yml/`vision.model`/labels。`ok` 表示没有 fail；warn 不得阻止保存。

#### Scenario: Missing ONNX Runtime is a fail
- **WHEN** `backend=onnxruntime`，候选 venv 没有 `onnxruntime`
- **THEN** probe 的对应检查为 fail，附安装到该 venv 的修复说明，且不启动 Gateway worker、不写快照

#### Scenario: Missing model is a warning
- **WHEN** DNN 关闭或 models 目录没有可用 `.onnx`
- **THEN** probe 的模型检查为 warn，允许保存，并说明保存后 `vision_detect` 会返回 `dnn_no_model`；不自动下载

#### Scenario: Probe has no side effects
- **WHEN** 用户连续点击“检测可用性”且 Gateway 正在运行
- **THEN** `runtime.json`、active/pending revision、worker pid 与模型目录均不变；probe 只返回检查结果

### Requirement: DNN inference is CPU-only by default

`features.dnn` 启用后，默认 `vision.dnn.backend` 必须（SHALL）是 `opencv`，默认 `vision.dnn.device` 必须（SHALL）是 `cpu`；默认路径与默认托管 venv 不得安装、打包或 import `onnxruntime`，也不得安装任何 GPU runtime；只有用户显式选择 `backend=onnxruntime` 并提供自备 venv 时，worker 才从该 venv import ORT。`vision_status` 必须报告 `dnn.experimental=true`、`dnn.backend=opencv`、`dnn.device.used=cpu`。切换到 GPU 必须由用户显式配置自备 venv 和 ONNX Runtime，不得自动探测或静默切换。

#### Scenario: Default install stays CPU-only
- **WHEN** 全新安装启用 `features.dnn`，用户未设置 backend/device/venv，且已有一个可用模型
- **THEN** `vision_detect` 使用 OpenCV DNN CPU 推理，`vision_status` 报告 `backend=opencv`、`device.used=cpu`，进程未 import onnxruntime

#### Scenario: Selftest follows the default backend
- **WHEN** `features.dnn=true` 且存在可用模型，调用 `vision_selftest`
- **THEN** 自检使用当前 backend/device，报告 `provider` 与 `device.used`，不尝试未配置的 GPU

### Requirement: GPU inference is an opt-in user-managed backend

`vision.venv` 非空时，Gateway 必须（SHALL）使用该用户自备 venv 的解释器并跳过托管 venv 的创建与 pip；插件只校验存在、可执行、版本 3.11–3.13 及 `import cv2`、`numpy`（`backend=onnxruntime` 时还需 `import onnxruntime`），失败返回 `vision_custom_env_invalid`，不回退。`vision.dnn.backend=onnxruntime` 必须要求非空 venv 与可导入 ORT，否则返回 `dnn_backend_unavailable`；`device=cpu|auto|gpu` 决定 provider，`gpu` 不可用返回 `dnn_device_unavailable`，`auto` 才可回退 CPU 并报告 `reason`。插件不得安装、升级或删除用户 venv 与驱动。

#### Scenario: Custom venv is used as-is
- **WHEN** 用户把 `vision.venv` 指向一个已安装 OpenCV/NumPy/ONNX Runtime 的 venv
- **THEN** Gateway 直接使用该解释器，`vision_status.env.source=custom`，不在 home 下创建第二个 venv，也不执行 pip

#### Scenario: Explicit GPU failure does not fall back
- **WHEN** `backend=onnxruntime`、`device=gpu`，但 ORT 没有可用 GPU provider 或 session 创建失败
- **THEN** `vision_detect` 返回 `dnn_device_unavailable`，包含 provider/availableProviders 与文档链接，不切回 CPU、不改用 OpenCV backend

#### Scenario: Auto may fall back with a reason
- **WHEN** `backend=onnxruntime`、`device=auto`，且没有可用 GPU provider
- **THEN** 使用 CPU provider，`vision_status` 与检测回执报告 `device.used=cpu` 与 `reason`，不报错

#### Scenario: Invalid custom venv is rejected
- **WHEN** `vision.venv` 指向不存在目录，或解释器缺少 cv2/numpy/onnxruntime
- **THEN** 返回 `stage=config`、`code=vision_custom_env_invalid`，列出缺失模块与修复说明，不回退托管 venv

### Requirement: Backend and device failures are explicit

backend/device 配置错误、provider 不可用或 session 创建失败必须（SHALL）返回结构化错误，包含 `stage=config`、`code`、`provider`/`availableProviders`、请求与实际使用的 device、根因、修复与文档 URL；不得静默切回 OpenCV/CPU、不得改用其他模型、不得自动安装包。错误优先级为 `dnn_disabled` → `dnn_config_switching` → `dnn_config_switch_failed` → `vision_custom_env_invalid` → `dnn_backend_unavailable` → `dnn_device_unavailable` → 模型错误。

#### Scenario: OpenCV backend rejects GPU request
- **WHEN** `backend=opencv` 且 `device=gpu`（通过配置文件绕过设置校验）
- **THEN** 返回 `dnn_device_unavailable`，说明 OpenCV DNN 后端只有 CPU，并指向自备 ONNX Runtime 的文档

#### Scenario: Status exposes provider diagnostics
- **WHEN** 用户配置了 `backend=onnxruntime`
- **THEN** `vision_status.dnn` 包含 backend、device.requested/used、provider、availableProviders、reason、experimental 与 docs URL；缺失的 ORT/驱动以错误码和修复说明呈现，不崩溃

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

`features.dnn` 已启用（包括刚保存、热应用尚未完成切换）且 models 目录没有任何 `.onnx` 时，DSH 插件设置必须（SHALL）显示可下载预设的复选框列表：仅 YOLOv5 v7.0 的 `n/s/m/l/x`，默认勾选 `n`，并展示每个文件的大小、来源与 GPL-3.0。列表只展示可下载项，不自动开始下载。

#### Scenario: Enable with no models offers presets
- **WHEN** 用户打开 `features.dnn` 且 models 目录为空
- **THEN** 配置卡显示 `yolov5n` 默认勾选、`yolov5s/m/l/x` 可多选的列表，未确认前不下载任何文件

### Requirement: Preset downloads require explicit human confirmation

只有人类确认后，宿主才可以下载；下载 URL、字节数与 sha256 必须（SHALL）固定在宿主代码中，不读 models.yml，不随 npm 分发。下载必须写 `.part`、校验 sha256 与字节数、原子 rename，并写入 labels 与 models.yml 条目；已有同名 key 不得覆盖。

#### Scenario: Human confirms the download
- **WHEN** 用户勾选 `yolov5n` 与 `yolov5s` 并确认
- **THEN** 宿主依次下载、校验、原子落盘，写 `coco80.txt` 与 models.yml 条目；保存并应用（无需重启 Gateway）后 `vision_detect` 可使用这两个模型

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

视觉 worker 必须（SHALL）只读取调用方提供的图片路径和 home 下的模型/缓存，只写入 home 下的缓存、预览和日志；不得执行调用方 Python、不得发起任何网络请求、不得读取与请求无关的用户文件。模型下载只能由宿主在人类确认后执行，worker 不参与下载；自定义 venv 只作为用户提供的运行时被读取，插件不得在其中安装、升级或删除包，也不得修改用户驱动。可用性 probe 必须（SHALL）由宿主启动的一次性进程在候选环境上执行，只读解释器信息与模型目录、不写 runtime/模型/缓存、不联网、不安装包、不加载 ONNX；自定义 venv 中 import 产生的副作用由用户自行承担。图片解码必须有尺寸与像素上限，超限返回 `vision_image_too_large`。

#### Scenario: No arbitrary code
- **WHEN** 调用方尝试通过 `vision_analyze` 传入脚本、URL 或模型路径
- **THEN** 参数校验拒绝该字段，worker 不执行任何调用方代码

#### Scenario: Oversized image is rejected
- **WHEN** 图片解码后的像素数超过文档上限
- **THEN** 返回 `vision_image_too_large`，包含尺寸与上限，不进行部分分析