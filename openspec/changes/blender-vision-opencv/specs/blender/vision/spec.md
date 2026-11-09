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

### Requirement: Detection uses a fixed, licensed model set

`vision_detect` 必须（SHALL）只支持内置的 YOLOv5 v7.0 ONNX 模型变体 `n/s/m/l/x`，默认 `n`；模型从官方 release 的固定 URL 下载，按内置 sha256 校验后写入 `vision/models/`。模型不得随 npm 包分发，不得接受任意 URL、任意 ONNX 路径或任意脚本。首次使用前必须展示来源与 GPL-3.0 许可，并允许 `vision.autoInstall=false` 时明确失败而非自动下载。

#### Scenario: Default detector downloads once
- **WHEN** 首次调用 `vision_detect` 且默认模型不存在、`autoInstall` 为真
- **THEN** worker 下载 `yolov5n`、校验 sha256、记录来源与许可，并与 OpenCV 一起进入 ready 状态

#### Scenario: Unsupported model is rejected
- **WHEN** 调用方传入 `yolov5x6` 或一个本地 ONNX 路径
- **THEN** 回执返回 `vision_model_unsupported`，列出允许的 `n/s/m/l/x`，不加载该文件

#### Scenario: Offline detection is explicit
- **WHEN** 模型缺失、网络不可用且 `vision.autoInstall=false`
- **THEN** 回执给出模型名、目标路径、许可与手动放置步骤，不返回空检测结果

### Requirement: Vision failures are actionable

所有视觉失败必须（SHALL）返回 `stage`、`code`、`root_cause`、`fix`、`fallback` 与 `evidence`。`stage` 至少区分 `config`（home/Python/设置）、`vision`（worker 协议/崩溃）与 `input`（图片/参数）；不得把安装失败、图片解码失败或模型缺失折叠成 `backend_error` 或空结果。

#### Scenario: Corrupt image is input failure
- **WHEN** 图片路径存在但不是可解码图片
- **THEN** 回执为 `stage=input`、`code=vision_invalid_image`，包含路径与解码错误，不回退到 Blender

#### Scenario: Install failure names the step
- **WHEN** OpenCV 轮子安装失败
- **THEN** 回执为 `stage=config`、`code=opencv_install_failed`，包含失败的阶段、日志路径、代理/镜像建议和离线安装 fallback

### Requirement: Vision runtime is read-only and sandboxed

视觉 worker 必须（SHALL）只读取调用方提供的图片路径和 home 下的模型/缓存，只写入 home 下的缓存、预览和日志；不得执行调用方 Python、不得访问网络（模型下载除外）、不得读取与请求无关的用户文件。图片解码必须有尺寸与像素上限，超限返回 `vision_image_too_large`。

#### Scenario: No arbitrary code
- **WHEN** 调用方尝试通过 `vision_analyze` 传入脚本、URL 或模型路径
- **THEN** 参数校验拒绝该字段，worker 不执行任何调用方代码

#### Scenario: Oversized image is rejected
- **WHEN** 图片解码后的像素数超过文档上限
- **THEN** 返回 `vision_image_too_large`，包含尺寸与上限，不进行部分分析
