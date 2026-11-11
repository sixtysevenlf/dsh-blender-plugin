# Design

## Context

本 change 构建在 3.1 Gateway 拓扑之上，见 `proposal.md`。当前约束：

- 3.1 已把主机侧收敛为六个模型工具（`session/op/py/see/run/inspect`），Gateway 单端点，addon 反向连接；本 change 不改变拓扑，不新增工具或 HTTP 路由。
- 现有 `img_*` / `qc_*` 在 Blender Python 内运行（`runtime/py/render/imgtools_impl/api.py`、`runtime/py/render/qc_impl/api.py`），依赖 bpy/numpy；本 change 不重写它们，只新增 Gateway 侧视觉契约。
- `blender_run(kind=render)` 目前由 `runtime/core/queue/spawn-executor.ts` 的 `renderPrelude` 生成固定 `--python-expr`，只输出 beauty；queue 没有 pass、manifest 或缓存。
- 持久数据当前默认落在 `%LOCALAPPDATA%\dsh-blender-rt`（`runtime/config.ts`），配置由 `runtime/config.ts` 的静态 `CFG` 和环境变量/JSON 文件读取；host 侧 `src/plugin.ts:38-52` 的 `Config` 只有 `port/autoStart/tui`。
- 所有持久数据必须迁到 `$DSH_HOME/dsh-blender/`；旧的 `dsh-blender-rt` 只作为迁移来源读取，不自动删除。
- `openspec/future/vision-motion-analysis` 已停用，其未被覆盖的 pass 语义统计、2D 序列稳定性与 3D 动作分析并入本 change；本 change 是这些能力的唯一事实源。

## Goals / Non-Goals

**Goals:**

- 提供一个 Gateway 直属、无监听端口、可自动安装/自检的 Python/OpenCV worker，让视觉分析不占 Blender、不要求 GUI 会话。
- 把 home、配置优先级、DSH 设置字段和 `doctor` 可见性固化成一套，后续缓存/视觉状态不再散落。
- 让 render pass 成为可复核的数据集：固定 pass 集合、每帧 manifest、不把像素塞进工具回执；pass 语义统计通过 `vision_pass` 消费 manifest。
- 用有界、可失效、原子化的缓存避免重复渲染，且缓存不改变 render 的旧行为。
- 让 2D 序列与 3D 动作证据都可复核：2D 给稳定性/参考序列偏差，3D 给 root motion、关节、接触/脚滑、采样穿插与参考轨迹；3D 从 evaluated 场景帧取最终姿态，关节链显式、骨骼选择基于原生事实，不推断 rig 角色；两者可组合，但不互相替代。
- DNN 默认 CPU-only；GPU 作为用户自备 venv + ONNX Runtime 的可选 DIY 扩展点，插件不打包、不自动安装、不承诺平台/驱动覆盖。
- DNN 配置保存后无需重启 Gateway 即可热应用；切换在请求边界两阶段提交，忙时只登记 pending、不中断在途 worker。
- 配置卡提供无副作用的可用性 probe，保存前就能看到解释器、import、provider、models.yml 与模型是否就绪。
- 所有失败都能定位到 `config/vision/input`，并给出可执行修复或明确 fallback。

**Non-Goals:**

- 不迁移/重写既有 `img_*` / `qc_*` 算法；共享原语替换留给后续 golden-parity change。
- 不打包、不自动安装、不维护 GPU runtime（CUDA/DirectML/OpenVINO/MIGraphX）；GPU 只通过用户自备 venv + ONNX Runtime 的实验性 DIY 路径，不做通用 DNN 平台/模型市场、不做 CLI、不做第七个模型工具、不改 3.1 协议与路由。
- 不把 OpenCV 装进 Blender Python，不对系统 Python 执行全局 pip。
- 不把 YOLOv5 模型、onnxruntime 或 GPU runtime 打进 npm 包；不承诺 GPU 检测性能、跨平台 provider 一致性或所有显卡/驱动可用。
- 不做动画编辑、重定向、IK/物理仿真；`animation_*` 只做不持久修改场景的报告，不改关键帧或 pose。不分析修改器/Geometry Nodes/粒子动画，不推断 control/mechanism/deform 角色；Rigify 专用 profile 作为 future extension，v1 不实现。
- 不做图像反推 3D、不做连续时间碰撞保证；3D 穿插是离散采样网格重叠，接触是骨头采样点相对地平面的近似。
- 不把 2D 分析当作 3D 真值的替代，也不把 3D 分析塞进 vision worker 或 motion 家族。
- 热应用只覆盖 DNN 相关配置；`cache.render.*`、托管 Python/OpenCV 安装、模型下载进度仍按既有生命周期，不做缓存或安装过程的热切换。

## Decisions

### D1. Home 与配置：一个解析器，两层文件，插件设置最高

新增 `runtime/home.ts` 负责解析和创建唯一 home：

- 解析顺序：`DSH_BLENDER_HOME` → `$DSH_HOME/dsh-blender` → `~/.dsh/dsh-blender`；同一进程只解析一次，`doctor` / `vision_status` 报告 `home` 与 `home.source`。
- `ensureHomeLayout()` 幂等创建 `logs/`、`state/`、`jobs/`、`sessions/`、`queue/`、`cache/`、`vision/`；创建失败返回 `home_unwritable`，绝不回退到包目录或系统临时区。
- 旧 `DSH_BLENDER_WORKDIR` 仅作为弃用别名（测试/开发用），映射到 home 并在 `doctor` 打 warning；默认不再写 `%LOCALAPPDATA%\dsh-blender-rt`。旧目录存在且新 home 的 `queue/queue.json` 缺失时，只复制队列/状态 JSON 作为迁移来源，不移动、不删除旧产物。

配置合并顺序（后者优先）：内置默认 → 包内 `dsh-blender.config.json` → home 内 `dsh-blender.config.json` → `DSH_BLENDER_CONFIG` 指定文件 → DSH 插件设置。插件设置在 host 侧由 `Config` 解析后，以 `DSH_BLENDER_PLUGIN_CONFIG`（JSON，不含密钥）环境变量传给 Gateway；Gateway 启动时合并并记录每项来源，`doctor` 可读。这样不需要 host 写文件，也避免设置与文件互相覆盖。

`runtime/config.ts` 从“模块加载即读死配置”改为“先 `resolveHome()` / `loadConfig()`，再导出 `CFG`”；保留 `CFG.workDirWin/Wsl` 作为 home 下的工作目录兼容名，内部消费者逐步改用 `home` 路径，避免一次性大改所有调用点。`src/host/backend/index.ts` 的端口解析增加 home 配置文件候选，仍保持 `port → 环境变量 → 配置文件 → 39877` 的旧优先级。

**替代方案**：把设置写进 home 配置文件再启动 Gateway —— 会引入 host/Gateway 双写和重启竞态，不采用。让 Gateway 读取 DSH 设置存储 —— 跨进程/跨包耦合，不采用。

### D2. DSH 设置：只加 Config schema，不新增客户端 bundle

`src/plugin.ts` 的 `Config` 增加嵌套对象：

- `cache.render.maxEntries`：默认 8，范围 1–32。
- `cache.render.maxBytes`：默认 4 GiB，范围 512 MiB–16 GiB（字节整数）。
- `cache.render.ttlDays`：默认 7，范围 1–30。
- `features.dnn`：默认 false，布尔；DNN 检测总开关，保存后经宿主 `dnn.apply` 热应用，不要求重启 Gateway；env `DSH_BLENDER_DNN=off/on` 是部署级覆盖，设置后配置卡显示 env 锁定，快照不得覆盖；`doctor` / `vision_status` 报告生效值、来源与 active/pending revision。
- `vision.python`：默认空字符串，表示自动发现；非空时必须是可执行路径；`vision.venv` 非空时本字段被忽略。
- `vision.venv`：默认空字符串；非空时必须是绝对路径，指向用户自备的 venv（Windows 用 `<venv>/Scripts/python.exe`，POSIX 用 `<venv>/bin/python`）。设置后 Gateway 直接使用该解释器，跳过托管 venv 的创建与 pip 安装；启动时校验解释器存在、可执行，并能 `import cv2`、`numpy`（`vision.dnn.backend=onnxruntime` 时还需 `import onnxruntime`），失败返回 `vision_custom_env_invalid`。插件不向该 venv 安装、升级或删除任何包，也不接管其生命周期；用户负责 OpenCV/NumPy/ONNX Runtime 的版本与驱动匹配。
- `vision.model`：默认 `yolov5n`，格式必须是模型 id（与 models.yml 顶层 key 同一规则）；检测时按 id 解析，不存在时返回对应的 `dnn_no_model`/`vision_model_missing`/`vision_model_unconfigured`；不允许枚举固定列表，因为用户可扩展模型；不接受路径/URL。
- `vision.dnn.backend`：枚举 `opencv`（默认）/`onnxruntime`；`opencv` 使用 `cv2.dnn` 的 CPU 路径，不 import `onnxruntime`；`onnxruntime` 必须配合非空 `vision.venv`（由用户安装 `onnxruntime` 及对应 GPU 包），否则返回 `dnn_backend_unavailable`。
- `vision.dnn.device`：枚举 `cpu`（默认）/`auto`/`gpu`，仅在 `backend=onnxruntime` 时生效；`backend=opencv` 且本字段不是 `cpu` 时在设置校验阶段拒绝并说明“GPU 需要自备 ONNX Runtime venv”。`cpu` 强制 CPU provider；`auto` 在存在可用 GPU provider 时用 GPU，否则回退 CPU 并在 `vision_status` 报告 `reason`；`gpu` 要求 GPU provider 可用，不可用返回 `dnn_device_unavailable`，不回退。

使用现有 schemastery `z.object` / `z.natural` / `z.boolean` / `z.union` 约束；DSH 插件设置按 3.1 已有的 Config schema 渲染，不新增 `dsh.client.inject` 页面。`apply()` 只把合法值序列化进 `DSH_BLENDER_PLUGIN_CONFIG`，非法值由 schema 拒绝或回退默认并在 `doctor` 报告字段/来源；不把任何密钥写入该环境变量。插件首次激活时 `apply()` 只把持久化 Config 作为启动默认，不覆盖已存在的 `vision/runtime.json`，避免插件重载把用户刚应用的 DNN 配置回退；之后用户在配置卡保存或点“保存并应用”时，由 `src/host/dnn-config.ts` 写运行时快照并触发 Gateway 在请求边界重读（D6a）。

DNN 开关必须显示“实验性”badge，并在开关下方给出影响说明（YOLOv5/GPL-3.0、默认 CPU、结果可能不稳定、GPU 需自备环境），同时提供指向 `https://github.com/sixtysevenlf/dsh-blender-plugin/blob/main/docs/vision-custom-backend.md` 的“GPU / 自备 ONNX Runtime 说明”链接。`vision.venv`、`vision.dnn.backend`、`vision.dnn.device` 放在同一配置卡的“自定义推理环境（DIY）”分组；`backend=onnxruntime` 或 `device=auto|gpu` 时显示当前只完成 import/provider 校验、不安装驱动/运行时的提示。模型目录 `GPU-DIY.md` 的路径与文档 URL 由 `vision_status.dnn.docs` 返回，配置卡不硬编码文件系统路径。DNN 分组另提供“保存并应用”“检测可用性”“取消等待”按钮与 `idle/pending/switching/failed` 状态、在途 op、active/pending revision；保存不再提示“重启 Gateway”，只在 `gateway_unreachable` 或 `dnn_config_switch_failed` 时显示真实失败。

`features.dnn` 只有这一层开关，不设 `allowDnn` 第二层许可：DNN 不拉起隐藏 GUI、不写桌面，开启与否本身就是人的选择。关闭时 `vision_detect` 在 host 侧直接返回 `dnn_disabled`，不启动 worker。

**替代方案**：自定义设置页 —— 需要新客户端 bundle 和 DSH 设置插槽，收益低；本 change 沿用现有 `src/client/index.js` 配置卡的开关/弹窗，只加 schema 字段和 DNN 下载交互。

### D3. Vision worker：Gateway 私有子进程，JSON-lines 协议

新增 `runtime/vision/`：

- `manager.ts`：至多一个 worker 子进程；首次视觉请求时启动，空闲 10 分钟回收；崩溃/超时/协议错乱时杀掉进程树并按“最多 3 次/5 分钟”重启，重启后重新握手。manager 同时维护 `activeRevision/pendingRevision`、worker-backed 请求的 in-flight 集合和切换状态机（D6a）；`vision_status` 与 `vision_pass` 不启动 worker、不增加 in-flight。
- `protocol.ts`：stdin/stdout 上的换行分隔 JSON；请求 `{id, op, args, configRevision}`，响应 `{id, ok, result|error, configRevision}`；单帧上限 8 MiB，超过即协议错误；每次请求 30 秒超时；worker 不下载模型，因此没有下载放宽。
- `worker/`：Python 包（`main.py`、`analyze.py`、`compare.py`、`detect.py`、`track.py`、`selftest.py`、`models.py`、`probe.py`、`protocol.py`，以及 `inference/opencv.py` / `inference/onnxruntime.py` / `inference/select.py`）。它不在 `runtime/py/modules.json` 中，不注册为 Blender 模块，不 import bpy；它与 `runtime/py` 的域模块完全隔离；`models.py` 用 `cv.FileStorage` 读取 `vision/models/models.yml`（OpenCV 5 语法），不在 Node 侧引入 YAML 解析依赖；worker 不发起任何网络请求，也不安装包。`probe.py` 只被 D6b 的一次性 probe 调用，不常驻、不加载 ONNX。新增 `runtime-config.ts`：读取/校验运行时快照、维护 revision 与切换状态，并原子写 `home/vision/runtime-state.json`。
- worker 环境只继承最小变量（`PATH`、代理、home），cwd 为 home，Windows 用 `windowsHide` + 独立进程组；stderr 追加到 `home/logs/vision-worker.log`，保留最后 256 KiB。
- worker 不监听端口；manager 不通过 HTTP、WebSocket 或命名管道暴露它。Gateway 的其他请求/作业不受其崩溃影响。

**替代方案**：常驻独立 sidecar 进程/本地端口 —— 需要端口和额外生命周期，违反“Gateway owns worker、无监听”的约束；不采用。

### D4. Python 发现与安装：系统优先，托管解释器兜底

新增 `runtime/vision/installer.ts` 与 `runtime/vision/python-detect.ts`，状态写入 `home/vision/state.json`，状态机：

`not_installed → resolving_python → (downloading_python) → creating_venv → installing_opencv → verifying → ready | failed`

- 发现顺序：`vision.venv` 非空时直接使用其解释器（优先于下列所有来源）；否则 `vision.python` 显式路径 → `DSH_BLENDER_PYTHON` → Windows `py -3.13/-3.12/-3.11` → `python3.13/3.12/3.11` → `python3`/`python`；每个候选验证版本在 3.11–3.13 且能 `import venv`。优先 3.13，其次 3.12、3.11。
- 自定义 venv：`vision.venv` 非空时，解析 `<venv>/Scripts/python.exe`（Windows）或 `<venv>/bin/python`（POSIX），校验存在、可执行、版本 3.11–3.13，并实际执行 `import cv2, numpy`（`backend=onnxruntime` 时再 `import onnxruntime`）；失败返回 `stage=config`、`code=vision_custom_env_invalid`，列出缺失模块与文档链接。成功时 `vision_status.env.source=custom`、安装状态为 `ready`，跳过托管 venv 创建、pip 安装、`state.json` 安装状态机与 notices 写入；插件不向该 venv 安装/升级/删除任何包，也不覆盖用户文件。自定义 venv 的 OpenCV/NumPy/ONNX Runtime 版本与驱动匹配由用户负责，`vision_status` 只报告实际 import 到的版本与 provider。
- 没有系统解释器时，下载固定版本的 python-build-standalone CPython 3.13（Windows/Linux 各固定 URL + sha256）到 `home/vision/python/`，用系统 `tar` 解压，校验 `python -c "import venv"` 后再用；该来源在 `vision_status` 标为 `managed`。没有网络或校验失败返回 `python_missing`，不回退到系统 pip。该安装只涉及 Python/OpenCV，与 `features.dnn` 和 YOLOv5 模型下载无关。
- venv 建在 `home/vision/venv`，先建 `venv.tmp-<pid>`，成功校验后原子 rename；失败清理临时目录，不留下半可用 venv。
- 依赖固定为 `opencv-python-headless==5.0.0.93` + `numpy>=2`，由 `runtime/vision/requirements.lock` 按平台锁定并提供 sha256；pip 以 `--require-hashes --only-binary=:all: --no-input` 安装，pip cache 指向 `home/vision/cache/pip`，不升级系统 pip 除非验证失败后显式重试。
- 安装器用原子 `mkdir` 锁（`home/vision/install.lock`，含 pid/时间，10 分钟过期）防并发；失败最多重试 2 次；每次失败写 `home/logs/vision-install.log` 并把阶段、退出码、代理建议、离线安装 fallback 放进状态。
- 托管路径（`vision.venv` 为空）安装成功后写 `home/vision/THIRD_PARTY_NOTICES.md`（OpenCV Apache-2.0、numpy BSD、Python PSF）并在 `vision_status.licenses` 给出路径；不复制 OpenCV 源码/头文件。自定义 venv 的依赖与许可由用户自行负责，插件不写 notices、不执行 pip。

**替代方案**：只在系统 Python 上 `pip --user` —— 污染系统环境、权限不可控；不采用。只支持系统 Python 不下载托管解释器 —— 干净机器会永久不可用，不采用。

### D5. Vision op 契约：8 个 host-local op，结果是数字不是像素

`blender_op` 新增 `vision` 家族，8 个 op 全部 `requires_session=false`、只读、无需 lease/虚拟显示。catalog 标记 host family，`runtime/core/plan-dispatch.ts` 在 addon 之前短路到 `runtime/vision/dispatch.ts`；`src/tools/op/read-only.ts` 加入这 8 个 op；`config/contracts.json` 的 host family 声明供 parity 使用。任何 `vision_*` 都不进入 Python dispatch 表、不触发 Blender 启动。

| op | 参数 | 返回（有界） |
|---|---|---|
| `vision_status` | 无 | home、安装状态、`env.source=managed|custom`/`env.venv`/实际 python/cv2/numpy 版本、worker pid/启动时间/`lastStartError`、`features.dnn` 生效值/来源、`dnn.experimental=true`、`dnn.backend`、`dnn.device.requested/used`、`dnn.provider`、`dnn.availableProviders`、`dnn.reason`、`dnn.docs`（GitHub URL + 模型目录 `GPU-DIY.md` 路径）、`dnn.config.activeRevision/pendingRevision/phase/waitingFor/lastSwitchError/previousRevision/active/pending`、模型目录、可用/未配置模型、下载状态、许可路径；调用本身不启动 worker |
| `vision_analyze` | `path`、可选 `roi` / `maxContours` | 宽高/通道、颜色均值/标准差、清晰度（Laplacian 方差）、曝光（高/低光裁剪百分比）、前景 bbox、最多 32 条轮廓摘要、最多 32 条直线摘要 |
| `vision_compare` | `ref`、`candidate`、可选 `align`（`ecc`/`orb`）、`roi` | 对齐方法、2x3 变换、inliers、IoU、平均绝对差、变化像素百分比、变化 bbox、预览路径 |
| `vision_detect` | `path`、可选 `model`（模型 id）、`conf`、`iou`、`maxDetections` | 检测数组（class、可选 label、confidence、xyxy），最多 100 条；模型 id/来源/许可；`backend` 与 `device.used`/`provider`；`configRevision`；未启用、切换中、无模型或 backend/device 不可用时返回 `dnn_disabled`/`dnn_config_switching`/`dnn_no_model`/`dnn_backend_unavailable`/`dnn_device_unavailable` |
| `vision_track` | `frames`（路径数组或 manifest 路径）、`method`（`farneback`/`lk`）、可选 `reference` | 每帧运动摘要/轨迹，最多 120 帧、20 条轨迹；序列稳定性（闪烁/抖动/跳变）与可选参考序列偏差；不返回完整光流场 |
| `vision_pass` | `manifests`（1–240 个 manifest 路径）、可选 `passes` / `roi` / `objects` | depth 分位与 ROI、normal 角分布、object index 每对象可见覆盖率/bbox/前后顺序；逐帧与跨帧聚合，最多 64 对象；不返回原始数组 |
| `vision_selftest` | 无 | 合成图上的 import/颜色/轮廓/ECC/检测前向检查结果；DNN 未启用或无模型时该检查标记为 skipped；切换 pending 时返回 `dnn_config_switching` 而不是启动旧 worker；启用时校验实际 backend/device/provider，显式失败不回退 |
| `vision_help` | 无 | 8 个 op 的参数、单位、限制与示例；`models.yml` 字段与模型 id 用法；`vision.venv`/`backend`/`device` 的 DIY 边界、保存并应用/pending/probe 语义与 `docs/vision-custom-backend.md` 链接 |

约定：像素坐标原点左上、x 向右、y 向下；bbox 用 `[x0,y0,x1,y1)`、0-based；长度单位像素；深度单位米。所有回执不含完整像素/mask/光流数组，只含标量、小数组、路径与 manifest 引用。图片仅接受已存在文件路径（绝对或相对 cwd），不接受 URL/通配/脚本；解码后像素上限 64 MP，超过返回 `vision_image_too_large`。worker-backed 的成功回执带 `configRevision`；`dnn_config_switching`/`dnn_config_switch_failed` 回执带 `activeRevision`、`pendingRevision`、`waitingFor` 与修复说明。

`vision_analyze` 的前景检测规则写进注释与 `vision_help`：有 alpha 用 alpha>0；无 alpha 用 Otsu 阈值反相后最大连通域；都不可靠时返回 `foreground:null` 并说明。`vision_compare` 先 ECC（`MOTION_EUCLIDEAN`），失败再用 ORB+单应；对齐失败返回 `aligned:false`，不伪造 IoU。`vision_track` 的 Farneback 只返回位移/散度/旋度摘要，LK 只返回有限轨迹点；序列稳定性用相邻帧位移/亮度差的鲁棒统计，参考序列偏差用同长度轨迹的 RMSE/max，不把缺失帧当 0。`vision_pass` 只读 manifest 与 pass 文件，不猜 dtype/值域/公式；具体边界见 D5a。

**替代方案**：把这些塞进 `see` 或 `inspect` —— `see` 是 GUI 采集，`inspect` 是只读文本，都会混淆边界；不采用。

### D5a. Pass 语义分析：只读 manifest，不猜格式

`vision_pass` 是 host-local、read-only、无会话的 op，直接读 render manifest 与 pass 文件，不启动 Blender，也不经过 worker 的图片分析路径。参数：

- `manifests`：1–240 个 `manifest.json` 路径，每个 manifest 对应一帧；路径必须是已存在文件。
- `passes`：可选子集 `depth` / `normal` / `object_index` / `alpha`；缺省使用 manifest 中实际存在的 pass。
- `roi`：可选像素 ROI，格式与 `vision_analyze` 相同；只影响统计窗口，不改 manifest。
- `objects`：可选对象名过滤，按 manifest 的 index→对象名映射匹配，最多 64 个。

返回分三层：逐帧、聚合、诊断。逐帧只给标量/小数组：depth 用 manifest 的 near/far 与解码公式还原为米，给 min/max/mean/p01/p05/p50/p95/p99 与 invalid_ratio；normal 给相机空间单位向量的均值、角度离散度与有效像素占比；object_index 给每对象可见像素、占画面比例、bbox，以及按中位深度得到的前后顺序；alpha 给前景覆盖率。聚合给跨帧范围、对象覆盖稳定性（标准差）和缺帧/越界警告。预览最多 2 张，路径写 home。

`object_index` 的顺序只表示可见覆盖与中位深度，不声称隐藏面重建或精确遮挡；bbox 重叠另给 `bbox_overlap_order`，并在回执中注明这是 bbox 级近似。manifest 缺 pass、dtype/值域/公式不完整、尺寸不一致时返回 `stage=input`、`code=vision_pass_manifest_invalid`，不猜格式；请求的 pass 不在 manifest 中返回 `vision_pass_unsupported`。所有数组有上限，超限截断并 warning，不返回原始像素、深度、法线或索引数组。

### D5b. 3D 动作分析：独立 animation 家族，session-bound session-read 报告

3D 分析不复用 vision worker：它需要 bpy 场景与骨骼，而 worker 明确不 import bpy；也不塞进 motion 家族：motion 是机构/关节/URDF，语义和契约不同。新增：

- catalog 家族 `animation`，prefix `animation_`，`requires_session=true`，`write_scope=exclusive`（家族最大可申请值；report/help 不申请写 grant，只有 selftest 需要临时写场景）。`animation_report`/`animation_help` 是 **session-read**，不是 read-only。
- 3 个 op：`animation_report`、`animation_selftest`、`animation_help`。
- 新 Python 领域 `runtime/py/animation/`，登记 `runtime/py/modules.json` 的 `domains` 与 `modules`；`config/routing.json` 增加 GUI 路由（`dsh_animation_api`、`ensureAnimation`、`preload animation`），由 `scripts/gen-routes.mjs` 生成 dispatch 表。
- `pyproject.toml` 的 import-linter layers 加入 `animation`。该领域只 import bpy/mathutils/numpy 与 Python 标准库，不 import 其他 runtime 域，保持 boundary-governance 的 exhaustive siblings 契约。
- 门禁：`src/tools/op/execute.ts` 不再只用 `READ_ONLY.has(op)` 判断是否要求会话；新增 `src/tools/op/session-read.ts` 导出 `SESSION_READ_OPS`（`animation_report`/`animation_help`），read-only 跳过会话，session-read 要求会话但不取写租约，其余走写门。`runtime/catalog/data.ts` 新增 `PLAN_SESSION_READ_OPS`；`runtime/http/routes/plan.ts` 对 session-read 调用 `leaseGate(payload,{readOnly:true})`，由 `runtime/http/session-gate.ts` 的 `sessionReadGate` 只检查会话存在与 owner/attached agent，不要求 write grant、不取写租约。`PLAN_READ_ONLY_OPS` 保持 98，不加入任何 animation op；`READ_ONLY_OPS` 只保留 8 个 vision op。`animation_report`/`animation_help` 都走 session-read；`animation_selftest` 走正常写门。

`animation_report` 参数（至少给 `objects` 或 `armature` 之一；只给 `bones`/`bone_chains`/`contact_bones`/`bone_collections`/`root_bone` 而未给 `armature` 时返回 `animation_input_invalid`；完全没有目标时返回 `animation_target_missing`）：

- `objects`：最多 16 个物体，采 root motion 与穿插；`armature`：可选骨架对象；`bones`：最多 64 根骨头；`bone_chains`：显式关节链；`bone_collections`：按 collection 选骨；`bone_filter`：`deform`（默认）/`all`；`include_bone_inventory`：额外返回 collection 统计。
- `contact_bones`：最多 16 根骨头，做接触/脚滑；`contact_point`：`head`/`tail`/`center`，默认 `tail`；`ground_z` 默认 0.0，`contact_epsilon` 默认 0.01 m。
- `frame_start` / `frame_end` / `frame_step`：缺省用场景帧范围；`frame_start`/`frame_end` 是闭区间；未给 step 时取最小整数 step 使采样数不超过 240，显式 step 导致超过 240 返回 `animation_sampling_invalid`，不静默改步长；回执区分 requested/actual 范围、实际最后采样帧、`frame_step`、`sample_count` 与 `effective_fps`。
- `root_bone`：可选；armature 默认按 armature object 的世界位移，给了根骨时按 `armature.matrix_world @ pose_bones[root_bone].matrix.translation`。
- `reference`：可选 JSON 路径，格式为 `{version:1, fps:24, frames:[{frame:1, objects:{Cube:[x,y,z]}}]}`，最多 2 MiB / 240 帧；只比位置，缺帧显式报告，不接受 URL。
- `intersection_objects`：可选，最多 16 个，缺省用 `objects`。

参数校验：`bones`/`bone_chains`/`contact_bones`/`bone_collections`/`root_bone` 必须同时给 `armature`；`intersection_objects` 至少 2 个；`frame_start <= frame_end`；`frame_step` 与 `contact_epsilon` 必须为正；`ground_z` 必须是有限数；`bone_filter` 只接受 `deform`/`all`，`contact_point` 只接受 `head`/`tail`/`center`；`objects`/`intersection_objects` 中未知对象名，以及 `bone_chains`/`contact_bones`/`bone_collections` 中未知名称，返回 `animation_target_missing`；非法组合返回 `animation_input_invalid`。

骨架选择优先级：显式 `bones`/`bone_chains` > `bone_collections` > `bone_filter`。`bone_filter=deform` 只选 `use_deform=true` 的骨，`all` 选全部；`bone_collections` 选指定 collection 的成员。`bone_chains` 只接受显式链，不从 `Bone.parent` 自动推导；没有 `bone_chains` 时 joints 返回空并 warning，不猜解剖链。

数据源与采样：按帧 `frame_set` 后从 evaluated depsgraph 读 `Object.matrix_world` 与 `PoseBone.matrix`；不读 `Action.fcurves`，也不依赖 layered Action/channelbag；actions/NLA/drivers/constraints/slots 都由 Blender 求值。`effective_fps = scene.render.fps / scene.render.fps_base`，所有时间/速度/角速度都按它换算。

骨骼事实：`animation_report` 对每根参与骨返回 `name`/`parent`/`use_deform`/`collections`；`include_bone_inventory=true` 时另返回每个 collection 的 `name`/`bone_count`/`deform_bone_count`/`non_deform_bone_count`/`mixed`/`sample_bones`。不输出 control/mechanism/DEF 角色断言；若所有骨的 `use_deform` 都相同，给 `deform_flag_uninformative` warning，提示改用 `bone_chains`/`bone_collections`。`animation_help` 只写通用字段与选择方法，不列 Rigify 的 DEF/MCH/ORG/CTRL 示例。

指标（位置米、角度度、速度 m/s、角速度 deg/s；无原始曲线/网格/顶点/四元数数组）：

- root motion：每个物体/骨架的 `path_length_m`、`displacement_m`、`displacement_vector_m`、`max_speed_m_s`、`max_accel_m_s2`、`loop_closure_m`（首末采样点距离，不是循环保证）、`rotation_delta_deg`（首末最短弧；超过 180° 会被压缩并带 warning）、`rotation_path_deg`（逐步累计，不是圈数保证）。有限差分样本不足时返回 null，不返回 0。
- joints：每根骨头的 min/max/mean 局部关节角、max_angular_speed_deg_s、max_angular_accel_deg_s2；最多 64 根。局部角公式：`pose_local = parent_pose.matrix.inverted() @ pose.matrix`；`rest_local = parent_bone.matrix_local.inverted() @ bone.matrix_local`（root 的 pose/rest 父变换都用 identity）；`delta = pose_local.to_3x3() @ rest_local.to_3x3().inverted()`；角度取 `delta.to_quaternion().angle`。角速度/角加速度由相邻姿态四元数差/Δt 计算，不用 Euler 差分。
- contacts：接触区间 start/end、`contact_samples`、`horizontal_slide_m`、`max_slide_speed_m_s`、`max_penetration_m`。采样点由 `PoseBone.matrix` 与骨骼长度推导 head/tail/center，再乘 `armature.matrix_world`；判定为有符号条件 `z <= ground_z + contact_epsilon`，穿透也算接触；step>1 时是采样近似，不是连续接触。
- intersections：逐帧把参与对象的 evaluated mesh 顶点变换到同一世界空间，用 `BVHTree.FromPolygons(..., all_triangles=True)` 做表面三角形重叠；只检测表面相交，完全包含但表面不相交可能报无重叠，不承诺穿透深度或实体布尔。预算是每采样帧所有参与对象的总三角形数 20 万，不是整份报告；超预算跳过该帧并记 `skipped_frames`，部分跳 `degraded`、全跳 `skipped`；最多 64 个对象对，按输入顺序确定性截断；`to_mesh_clear` 放 finally。
- trajectory：有 `reference` 时给每对象 RMSE、max deviation、matched/missing/unused frames；没有 reference 时该字段为 null。reference 严格校验 `version:1`、fps 与 `effective_fps` 容差 1e-6、帧号唯一整数、坐标有限；只按精确帧号匹配，不最近邻；缺失帧不按 0 计算；重复帧/NaN/Infinity/fps 不匹配返回 `animation_reference_invalid`。

非持久语义：`animation_report` 在 finally 中保存/恢复 `frame_current` 与 `frame_subframe`；`frame_set` 会触发 depsgraph、driver 与 frame_change handler，因此它是“不持久修改、但运行时可观察”的分析，不是无副作用的并发只读。report 不改关键帧/pose/材质/对象，不保存 .blend；selftest 在临时 collection 建带关键帧的立方体/骨架并在 finally 清理。

结果有硬上限：240 采样、16 物体、64 骨头、64 接触区间、64 相交对；超限截断并 warning。失败至少区分 `animation_input_invalid`、`animation_sampling_invalid`、`animation_target_missing`、`animation_reference_invalid`、`animation_no_data`、`animation_budget_exceeded`、`animation_unsupported`，带 stage（input/scene/animation）、root_cause、fix、fallback。`animation_no_data` 只在所有目标都没有 animation_data 且采样变换无变化时返回；部分目标静态时给零指标与 `static` warning，不整份报错。

v1 不分析修改器/Geometry Nodes/粒子动画；evaluated mesh 只用于穿插和最终几何，不解析修改器/GN/粒子参数。v1 不做 Rigify 专用 profile 或 control/mechanism/deform 角色推断；Rigify 适配作为 future rig profile 另行评估。

### D6. DNN 是可选 feature flag，模型由 OpenCV 5 models.yml 声明

DNN 只保留一个门：`features.dnn`（DSH 插件设置开关；host `Config.features.dnn` 默认 false；Gateway 从 `DSH_BLENDER_DNN=off/on` 或 `DSH_BLENDER_PLUGIN_CONFIG` 读取，暴露 `CFG.features.dnn` 与 source）。不设 `allowDnn` 第二层许可：DNN 不拉起隐藏 GUI、不写桌面，开启与否本身就是人的选择。关闭时 `vision_detect` 在 host 侧返回 `stage=config`、`code=dnn_disabled`、`retryable=false`，不启动 worker；agent 没有打开该 flag、下载模型或传任意路径的接口。

**实验性与默认 CPU**：`features.dnn` 在配置卡中必须带“实验性”badge 和影响说明（YOLOv5/GPL-3.0、默认 CPU、结果可能不稳定、GPU 需自备环境）。默认 `vision.dnn.backend=opencv`、`vision.dnn.device=cpu`，worker 只走 `cv2.dnn` CPU 路径，不 import `onnxruntime`；插件不安装、不打包 ORT/CUDA/DirectML/OpenVINO。GPU 仅作为用户自备环境的 DIY 扩展点：`vision.venv` 指向用户 venv，用户自行安装 OpenCV/NumPy 与对应平台 ONNX Runtime，再设置 `backend=onnxruntime`、`device=auto|cpu|gpu`。插件只校验 import 与 provider，不可用显式报错，不自动安装、不静默回退、不承诺所有显卡/驱动。

模型目录为 `$DSH_HOME/dsh-blender/vision/models/`，根下 `models.yml` 采用 OpenCV 5 `samples/dnn/models.yml` 的键名与语义。宿主不解析 YAML：worker 的 `models.py` 用 `cv.FileStorage` 读取并校验，避免新增 Node YAML 依赖；宿主只递归扫描 `models/**/*.onnx` 判断是否完全没有 ONNX（用于 `dnn_no_model` 早失败和下载弹窗）。模型以顶层 key 作为 id。模型目录同时放中英双语 `GPU-DIY.md`：`ensureHomeLayout()` 在文件缺失时创建，已存在时不覆盖；内容是 CPU 默认、自备 venv、各平台 ONNX Runtime 组合、配置字段、验证步骤与完整文档链接的快捷说明。示例：

```yaml
yolov5n:
  model: yolov5n.onnx
  mean: 0.0
  scale: 0.00392
  width: 640
  height: 640
  rgb: true
  labels: coco80.txt
  postprocessing: yolov5
my-detector:
  model: my-detector.onnx
  mean: 0.0
  scale: 0.00392
  width: 640
  height: 640
  rgb: true
  labels: my-detector.txt
  postprocessing: yolov5
```

规则：

- 必填 `model`、`width`、`height`、`postprocessing`；`postprocessing` 本期只接受 `yolov5`，其他值返回 `vision_model_unsupported`。
- `mean` 接受 OpenCV 样例中的标量或 BGR 三元组；`mean`、`scale`、`rgb` 省略时用 OpenCV DNN 默认值（mean 0、scale 1、rgb false）；YOLOv5 预设条目按 OpenCV 5 样例显式写 `mean: 0.0`、`scale: 0.00392`、`rgb: true`（约 1/255），不按模型名猜预处理。
- `labels` 可选：省略时只返回数字 class id；存在时必须是 models 目录内的相对路径，一行一个类名、UTF-8、无表头、行号即 class id，忽略文件末尾的单个换行。文件缺失或行数不匹配返回 `vision_model_unconfigured`，不伪造类名。
- `std`、`load_info`、`sample` 作为 OpenCV 元数据接受但不参与推理；`std` 非默认值时返回 `vision_model_unsupported`，避免静默错算。`background_label_id` 可选，用于过滤该 class。
- models.yml 中的 `model` 字段与可选的 `labels` 字段必须是 models 目录内的相对路径；拒绝绝对路径、URL、`..` 和解析后逃出目录的 symlink。模型 id 只允许 `^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$`，不接受路径/URL；重复 key 或 YAML 解析失败返回 `vision_model_unconfigured` 并带文件/行号。
- `vision.model` 默认 `yolov5n`；请求 id 有同名 `.onnx` 但 models.yml 无条目、条目不完整或 `model` 文件缺失时返回 `vision_model_unconfigured`，列出缺失字段与可复制示例；没有任何 `.onnx` 时返回 `dnn_no_model`；请求 id 无同名 `.onnx` 且不在 models.yml 时返回 `vision_model_missing`（附可用 id 列表）；模型 id 含路径/URL 时参数校验返回 `vision_model_unsupported`。不自动猜测预处理、不静默降级到别的模型。
- `vision.venv` 指向的目录/解释器不存在、不可执行、版本不在 3.11–3.13，或 `import cv2/numpy` 失败时返回 `stage=config`、`code=vision_custom_env_invalid`，附 venv 路径、缺失模块、要求的安装命令与文档链接；不回退到托管 venv。`backend=onnxruntime` 且 `vision.venv` 为空或 `import onnxruntime` 失败时返回 `dnn_backend_unavailable`，提示设置 `vision.venv` 并在其中安装 ORT；不自动安装。
- `backend=opencv` 与 `device=auto|gpu` 的组合在设置校验阶段拒绝；若通过配置文件/环境变量绕过，`vision_detect` 返回 `stage=config`、`code=dnn_device_unavailable`，说明 OpenCV DNN 后端只有 CPU。`backend=onnxruntime`、`device=gpu` 但没有任何 GPU provider 或 session 创建失败时返回 `dnn_device_unavailable`；`device=auto` 才允许回退 CPU，并在回执/状态写 `device.used=cpu` 与 `reason`。
- 请求门优先于模型门：`dnn_disabled` → `dnn_config_switching` → `dnn_config_switch_failed` → `vision_custom_env_invalid`/`dnn_backend_unavailable`/`dnn_device_unavailable` → `dnn_no_model` → `vision_model_unconfigured` → `vision_model_missing` → `vision_model_unsupported`。`dnn_config_invalid`/`dnn_config_stale` 是 apply/status 错误，不进入 detect 门。任何一级失败都不得自动下载、安装包、切换 backend/device 或改用其他模型。

**用户扩展**：用户把 `my.onnx` 放到 `vision/models/` 并写一条 `models.yml` 条目即可被 `vision_detect` 使用；`vision_status` 同时列出已配置模型和未配置的 `.onnx`（含原因）。模型由用户自带时，许可与预处理正确性由用户负责，工具只做结构校验和形状检查。

**下载预设**：仅 YOLOv5 v7.0 的 `yolov5n/s/m/l/x`，来源与 sha256 固定在宿主 `src/host/dnn-pins.ts`，不读 models.yml 的 URL。启用 `features.dnn` 且 `models/` 下没有任何 `.onnx` 时，DSH 配置卡通过现有 owner-only `/doctor` 的 `dnn` 块读取 `onnxCount/downloadable/download`，弹出复选框列表（`yolov5n` 默认勾选，n/s/m/l/x 可多选，显示大小、来源、GPL-3.0）；开关打开/保存后立即按待生效值检查并弹窗，不等 Gateway 重启；下载完成后失效模型缓存，下一次检测即可使用，无需重启 Gateway。确认后由宿主 `dnn` RPC 的 `download` 操作下载（不新增 HTTP 路由）：先写 `.part`，校验 sha256/字节数后原子 rename，写 `coco80.txt`，再向 `models.yml` 追加对应条目（已存在同名 key 不覆盖、不重复写）。单并发、失败清理 `.part`；worker 始终不联网。

点“暂不下载”只关闭弹窗，不关闭 `features.dnn`；配置卡保留警告和“下载模型”入口，开关生效后 `vision_detect` 返回 `dnn_no_model`（无 ONNX）或 `vision_model_unconfigured`（有 ONNX 但没配置），并附“请人在 DSH 插件设置中启用/下载”的修复说明。agent 不得自动下载、不得改用路径/URL、不得回退到其他模型。

**文档与发现**：完整中英双语说明在仓库 `docs/vision-custom-backend.md`，覆盖：默认 CPU 的事实、Windows DirectML（NVIDIA/AMD/Intel 卡）、Windows/Linux NVIDIA CUDA（onnxruntime-gpu + 匹配的 CUDA/cuDNN）、Linux Intel OpenVINO、Linux AMD MIGraphX（高级 DIY，不承诺）、每平台创建 venv/安装包/设置字段/验证命令、常见错误（provider 缺失、DLL 找不到、版本不匹配、驱动过旧）与“插件不安装/不维护这些包”的边界。模型目录 `GPU-DIY.md` 是同一内容的双语快捷版，由 home layout 缺失时创建、不覆盖；`vision_status.dnn.docs` 返回 `{url, localPath}`，配置卡用 `url` 打开 GitHub。该文档不包含任何凭据，也不要求用户向插件提供凭据。

**推理**：默认 `backend=opencv` 时用 `cv2.dnn.readNetFromONNX` + `cv2.dnn.NMSBoxes`，CPU only；letterbox 到条目声明的 `width/height`，YOLOv5 输出 `(1,N,85)`（N 由模型输入尺寸决定，不硬编码 25200），按 `xywh→xyxy` 解码，obj×class 过滤，NMS 默认 `conf=0.25/iou=0.45`（可覆盖），最多 100 条；坐标映射回原图并裁剪，class 按 labels 映射（无 labels 时只给 id）。`backend=onnxruntime` 时用 `onnxruntime.InferenceSession` 的 providers：`device=cpu` 只用 `CPUExecutionProvider`；`auto` 按平台可用的 GPU provider（CUDA/DirectML/OpenVINO/MIGraphX 等）优先、CPU 兜底，并在回执写 `device.used` 与 `reason`；`device=gpu` 只接受 GPU provider，不可用返回 `dnn_device_unavailable`，不回退。两种 backend 共用上述 letterbox/解码/NMS 与输出形状校验；输出形状或 postprocessing 不匹配返回 `vision_model_unsupported`，不返回空结果。`vision_selftest` 在 feature 关闭或无模型时将 DNN 检查标为 skipped，不算失败；启用时校验实际 backend/device/provider，失败显式返回。

**替代方案**：旧的固定模型表 + `ensure_model` 自动下载 —— 不能用户扩展，且 agent 会在用户不知情时联网下载；不采用。任意 ONNX 路径/URL —— 绕过许可与配置校验；不采用。在 Node 里引入 YAML 解析器 —— 增加运行时依赖，且与 OpenCV 语义可能漂移；改用 worker 的 `cv.FileStorage`。把 onnxruntime-gpu/CUDA/DirectML/OpenVINO 打包或自动安装 —— 体积、驱动矩阵与平台差异不可维护，且会把“可选实验性”变成受支持承诺；不采用。用 OpenCV 的 CUDA build 作为 GPU 路径 —— 需要自编译且跨厂商不可行；不采用。默认探测 GPU 并自动切换 backend —— 会让默认安装引入未声明的依赖与不确定行为；不采用，必须由用户显式设 `vision.venv`/`backend`/`device`。

### D6a. DNN 配置热应用：宿主写快照，Gateway 在请求边界两阶段切换

`$DSH_HOME/dsh-blender/vision/runtime.json` 是宿主单写的 DNN 运行时快照，只含 `version`、`revision`、`updatedAt`、`features.dnn`、`vision.python`、`vision.venv`、`vision.model`、`vision.dnn.backend`、`vision.dnn.device`；不含密钥、cookie、token。宿主用临时文件 + 原子 rename 写入，权限尽量 0600；Gateway 只读。快照覆盖 `DSH_BLENDER_PLUGIN_CONFIG` 的对应字段，`DSH_BLENDER_DNN=off/on` 优先级最高：env 存在时 `features.dnn` 由 env 锁定，`dnn.apply` 返回 `dnn_env_locked`，配置卡禁用开关并说明。Gateway 启动时按“内置默认 → 包内/home/DSH_BLENDER_CONFIG → DSH_BLENDER_PLUGIN_CONFIG → runtime.json”合并；启动时快照无效则回退插件配置，写 `phase=failed`/`dnn_config_invalid`，不崩溃。

Gateway 维护 `activeRevision/pendingRevision/phase/active/pending/waitingFor/lastSwitchError/previous/worker`，并原子写 `home/vision/runtime-state.json`（临时文件 + rename）。`active/pending` 只含上述 DNN 字段摘要，不含密钥。`phase` 取 `idle`/`pending`/`switching`/`failed`；`waitingFor` 是 `{op, startedAt, requestId}`；`previous` 是上一次成功提交的配置和 revision，用于显式回滚；`worker.lastStartError` 记录新配置启动失败。`vision_status.dnn.config` 与 `/doctor.dnn.config` 暴露同一状态，状态文件只作跨进程读取，内存中的 manager 是事实源。

Gateway 在 `/doctor` 和每次 `vision_*` 请求开始时调用 `runtime-config.reconcile()`；不启动后台轮询。宿主 `dnn.apply` 写完快照后调用现有 `/doctor` 触发立即 reconcile，不新增 HTTP 路由。若 `revision`/`updatedAt` 早于 active/pending，忽略并返回 `dnn_config_stale`，不改状态。Gateway 不可达时宿主已写快照但返回 `gateway_unreachable`、`applied:false`、`savedForNextStart:true`；配置卡不得显示已生效。

切换规则：

| 变化 | 处理 |
|---|---|
| 仅 `features.dnn` | 立即提交；新 `vision_detect` 按新值门禁；不重启 worker（analyze/compare/track 仍需要它） |
| 仅 `vision.model` 或 models.yml 内容 | 立即提交；失效模型缓存；不重启 worker |
| `vision.python` / `vision.venv` | 需要换 worker |
| `vision.dnn.backend` / `device` | 仅在 active 或 pending 的 `features.dnn=true` 时需要换 worker；否则立即提交 |
| 字段非法/类型错误 | 不提交；`phase=failed`、`dnn_config_invalid`，active 不变 |

需要换 worker 且没有 in-flight 时，立即走两阶段切换：先优雅关闭旧 worker（关闭 stdin，等 2 秒，仍不退则杀进程树），成功后才提交 pending 为 active、清空 pending、`phase=idle`；下一次 worker-backed 请求按新配置启动。关闭失败时保持旧 active，`phase=failed`、`lastSwitchError.code=dnn_config_switch_failed`，pending 保留供重试/取消；新 worker-backed 请求返回同一错误，不再落到可能半关闭的旧 worker。

有 in-flight 时只登记 pending，不中断在途请求：in-flight 集合只包含 worker-backed 的 `analyze/compare/detect/track/selftest`，`vision_status`、`vision_help`、`vision_pass` 不计入。新 worker-backed 请求返回 `stage=config`、`code=dnn_config_switching`、`retryable=true`，带 `activeRevision`、`pendingRevision`、`waitingFor`；在途请求继续用旧 revision 跑完，回执带旧 `configRevision`。in-flight 归零（正常完成、30 秒超时、崩溃）后执行上述两阶段切换。pending 期间再次 apply 时用最新 revision 覆盖 pending；若最新配置不再需要换 worker，立即清空 pending 并按规则提交。`dnn.cancel-pending` 用当前 active 配置写一个新 revision，reconcile 后清空 pending，不影响在途请求。

`features.dnn=false` 不触发 worker 重启：新 `vision_detect` 立即 `dnn_disabled`，在途 detect 跑完；共享 worker 继续服务 analyze/compare/track，空闲 10 分钟后由现有回收逻辑停止。只有 `vision.python/venv/backend/device` 变化才换 worker。`DSH_BLENDER_DNN` 锁定时只允许改其他 DNN 字段；apply 返回 `dnn_env_locked` 时不写快照。

新配置提交后 worker 是懒启动；若启动失败，active 仍是新配置，不回退旧配置，`vision_status` 报告 `worker.lastStartError`、`previous` 与修复说明；请求返回 `vision_custom_env_invalid`/`dnn_backend_unavailable`/`dnn_device_unavailable`。用户可用 `previous` 显式回滚（写回快照），不自动回滚。配置卡在 `activeRevision` 等于请求 revision 前显示“等待当前检测结束”或切换失败，不显示已生效；失败时提供“重试”“取消等待”“回滚上一版”。

### D6b. 可用性 probe：宿主侧一次性检查，不触碰运行中的 Gateway

配置卡“检测可用性”调用现有 owner-only `/dsh-blender` RPC 的 `dnn.probe`，传入表单候选值；probe 不写 `runtime.json`、不改变运行中的 Gateway，Gateway 不可达时也可用。宿主校验候选 schema 后，按 `vision.venv` → `vision.python` → 托管 venv → 自动发现系统 Python 的顺序解析解释器，然后用固定脚本 `runtime/vision/worker/probe.py` 启动一次性子进程（JSON stdin/stdout，10 秒超时，不经 shell）；不启动常驻 worker、不加载 ONNX、不创建推理 session、不分配 GPU、不联网、不下载/安装包、不写模型目录或 runtime 文件。

检查项逐条返回 `pass`/`warn`/`fail` 与 `fix`：feature 生效值与 env 锁定；解释器存在/可执行/版本 3.11–3.13；`import cv2`、`numpy`；`backend=onnxruntime` 时 `import onnxruntime`；ORT providers 与 `device=cpu|auto|gpu` 是否匹配（显式 `gpu` 无 provider 为 fail，`auto` 回退 CPU 为 warn）；worker 模块与协议版本可导入；models 目录存在；`.onnx` 数量；models.yml 可被 `cv.FileStorage` 解析；`vision.model` 指向的 id 是否存在、必填字段/labels 是否完整、路径是否逃逸。缺模型或未启用 DNN 是 warn，不阻止保存；解释器/import/显式 GPU 不可用是 fail。

回执 `{ok, checks:[{id,label,status,detail,fix}], summary:{pass,warn,fail}, elapsedMs, candidate}`；`ok` 表示没有 fail。probe 超时返回 `dnn_probe_timeout`，脚本异常返回 `dnn_probe_failed`，都不改变配置。保存并应用后自动再跑一次 probe；字段格式错误阻止保存；环境/模型未就绪仍允许保存，但配置卡显示 warn，`vision_detect` 仍按 D6 的错误码拒绝。probe 结果不缓存为“已就绪”，每次点击/保存重新检查。

### D7. Render pass：可选、固定 Python、manifest 先行

`src/tools/run/execute.ts` 的 `RENDER_KEYS` 增加 `passes`；`passes` 必须是允许集合的非空子集，未知/重复/空数组在 host 侧拒绝。未传 `passes` 时完全走旧 `renderPrelude`/输出路径，不写 manifest、不写缓存。

传 `passes` 时，Gateway 在 queue 的 `buildJobArgs` 中写两个固定文件到 job 临时目录：`render_passes.py` 与 `render-config.json`（含 passes、frames、outdir、camera、resolution、samples、engine、format）。Blender 仍以 `-b --factory-startup <file>` 启动，再 `--python render_passes.py`；脚本不接收调用方代码。脚本：

1. 设置相机、分辨率、samples、engine、帧范围；开启 view layer 的 Combined/Z/Alpha/Normal/ObjectIndex pass。
2. 对 `object_index`：按 `(name, type)` 排序给对象分配稳定的 `pass_index`（1..N），写映射；只作用本次进程，不保存 .blend。
3. 构建 compositor：Render Layers → 每 pass 一个 File Output 节点；beauty 用请求的 `format`；depth 用 Z pass 经 Map Range 到 `[0,1]` 后 16-bit PNG；normal 用 `n*0.5+0.5` 的 RGB 8-bit PNG；object_index 用 IndexOB 16-bit PNG；alpha 8-bit PNG。非 beauty 节点设 `save_as_render=False` 以避免颜色管理污染数据。
4. 每帧输出到 `<outdir>/passes/frame_<frame:04d>/`，渲染完成后写该帧的 `manifest.json`（最后写）；manifest 至少含 `manifestVersion`、frame、camera、resolution、samples、engine、passes、每 pass 的 path/dtype/range/colorspace、depth 的 near/far/解码公式、normal 编码、object_index 映射。
5. 渲染结束打印一行 `PASSES_DONE {json}` 供 executor 读取；失败/缺帧/缺 manifest 时退出非零。

depth 的 near/far 取相机 `clip_start/clip_end`（不是 view layer 的 mist 范围），manifest 公式为 `depth_m = near + (pixel/65535)*(far-near)`；normal 的坐标系为世界空间还是相机空间必须在 manifest 固定为相机空间并写编码；object_index 的 0 表示背景，映射只含实际对象。具体 Blender 4.2/4.5/5.2 的 pass 可用性用 E2E 验证，不可用时该 pass 返回 `pass_unsupported` 而非静默缺文件。

manifest 同时是 `vision_pass` 的唯一输入契约：必须带足够字段让分析器解码 depth、normal 与 object index，不允许分析器按文件名/颜色/像素分布猜 dtype、值域或编码。字段缺失或不一致时 `vision_pass` 返回 `vision_pass_manifest_invalid`，不静默跳过该帧。

**替代方案**：用 `bpy.data.images['Render Result']` 读 pass 再写文件 —— 依赖上下文/颜色管理且多帧不稳定；不采用。用 OpenEXR 存全部 pass —— 调用方与 OpenCV 读取成本更高，且规范要求 depth 16-bit PNG；不采用（manifest 仍允许后续扩展 EXR）。

### D8. Render pass 缓存：按内容寻址、原子提升、失败不污染

新增 `runtime/core/queue/render-cache.ts`，只在 `passes` 非空时启用：

- key = sha256(canonical JSON of {scene: {path,size,mtime}, frame, camera, resolution, samples, engine, passSet, manifestVersion})。scene 文件必须存在且可 stat；未保存/无文件路径时直接不缓存。帧范围逐帧做 key，一个 job 可对应多条 entry。
- 提交时先查缓存；命中则直接创建 `state=completed`、`cache_hit=true` 的 job 记录，artifacts 指向缓存文件，不占 GPU slot、不 spawn Blender。大小/条目/TTL 上限来自启动时读到的 `cache.render.*`。
- 未命中时正常排队；pass 输出先写 `home/queue/staging/<jobId>/`，成功且 manifest 完整后，按 entry 计算总字节：超过 2 GiB → 不提升，产物移到 `home/jobs/<jobId>/`，回执带 `cache_skipped=oversize`；否则原子 rename 到 `home/cache/render/<key>/` 并更新索引。用户指定 `outdir` 时，从 staging/缓存复制一份到用户目录，缓存仍保留自己的副本。
- 索引 `home/cache/render/index.json` 用临时文件+rename 原子写；entry 含 key、createdAt、lastAccess、bytes、scene、frame、manifest、artifacts。启动时清理 TTL/超限；每次写入后按 LRU 淘汰到上限。单条 entry 超过上限或文件缺失时删除并回退 miss。
- 失败/取消/超时：删除 staging，不写索引；已有旧 entry 不动。命中后发现 manifest 缺失/损坏：删除 entry 并按 miss 重渲染，回执说明 `cache_corrupt`。
- 设置变更只在 Gateway 下一次启动生效；启动时立即按新上限淘汰。缓存索引/目录损坏时整体重建为空，不阻塞 render。

**替代方案**：缓存用户 outdir 的路径引用而不复制 —— 用户移动/删除文件后命中损坏；不采用。按 jobId 缓存 —— 无法跨提交复用；不采用。

### D9. 计数、契约与文档

- catalog：`runtime/catalog/data.ts` 新增 `f:"vision", prefix:"vision_", requires_session:false, write_scope:"none"` 家族 8 op，与 `f:"animation", prefix:"animation_", requires_session:true, write_scope:"exclusive"` 家族 3 op；总数 34/228 → 36/239。6 工具、29 路由、25 注册命令（+ping=26 wire）、protocol 12 不变。
- `config/contracts.json` 增加 vision host family（host 入口、8 op、hostReason）；catalog↔Python parity 把 vision 计为 host 覆盖，把 animation 计为 Python dispatch 覆盖。任何未声明的 host family 或缺 op 让门禁失败。
- `config/routing.json` 增加 animation GUI 路由（`dsh_animation_api`、`ensureAnimation`、`preload animation`），由 `scripts/gen-routes.mjs` 生成 `runtime/core/dispatcher/routes.generated.ts`；`runtime/py/modules.json` 的 domains/modules 增加 animation。
- `pyproject.toml` 的 import-linter layers 加入 animation；否则 boundary-governance 的 exhaustive 契约会把新领域报为未登记层。
- `src/tools/op/read-only.ts` 只增加 8 个 vision op；新增 `src/tools/op/session-read.ts` 导出 `SESSION_READ_OPS`（`animation_report`/`animation_help`）。`src/tools/op/execute.ts` 的会话门分成三类：`READ_ONLY` 跳过会话、`SESSION_READ` 要求会话但不取写租约、其余走写门；catalog `requires_session` 继续用于 catalog 展示与契约，但不作为 host 门唯一依据。`runtime/catalog/data.ts` 新增 `PLAN_SESSION_READ_OPS`（`animation_report`/`animation_help`），`runtime/http/routes/plan.ts` 对 session-read 调用 `leaseGate(payload,{readOnly:true})`，由 `runtime/http/session-gate.ts` 的 `sessionReadGate` 只检查会话存在与 owner/attached agent，不要求 write grant、不取写租约。`PLAN_READ_ONLY_OPS` 保持 98，不加入任何 animation op；`animation_selftest` 走普通 `sessionWriteGate`。契约测试断言 `READ_ONLY_OPS` 与 `PLAN_READ_ONLY_OPS` 不含 animation，`SESSION_READ_OPS` 与 `PLAN_SESSION_READ_OPS` 一致。
- 文档同一 change 更新：`docs/architecture.md`（home、vision worker、DNN 运行时快照/切换状态机、render passes、animation 领域）、`docs/配置参考.md`（新字段/目录/上限，含 `vision.venv`/`vision.dnn.backend`/`vision.dnn.device`、`runtime.json`/`runtime-state.json`、热应用与 env 锁定）、`docs/操作教程.md`（首次安装、离线、许可、doctor、动画指标单位、DNN 实验性开关/下载/保存并应用/忙碌等待/检测可用性）、新增中英双语 `docs/vision-custom-backend.md`（默认 CPU、自备 venv、各平台 ONNX Runtime/provider、热应用/probe、验证/排障/边界）、新增 `runtime/vision/assets/GPU-DIY.md`（模型目录快捷版，home layout 缺失时复制、不覆盖）、CHANGELOG、behavior matrix、catalog lock、`docs/zh` 对应页；配置卡链接固定为 `https://github.com/sixtysevenlf/dsh-blender-plugin/blob/main/docs/vision-custom-backend.md`。
- 视觉 worker 的 Python 文件加入 `pyrightconfig.python.json` / ruff 的扫描范围，但不加入 `runtime/py/modules.json`（它不是 Blender 模块）；animation 的 Python 文件加入 ruff 与 `runtime/py/modules.json`，属于 Blender 侧领域。

### D10. 测试与验收

- 单元：home 解析/创建失败、配置优先级与非法值、worker 协议分帧/超时、Python 发现顺序、venv 状态机（fake python）、自定义 venv 解析/import 校验/不回退、DNN feature 门与错误优先级、models.yml 解析/路径约束/labels、YOLO 解码/NMS（合成张量）、backend/device 枚举与 `opencv + auto/gpu` 拒绝、ORT provider 选择（fake provider）、`vision_status.dnn` 字段与 `dnn.docs`、下载清单/sha256/原子 rename、运行时快照 revision/atomic rename/陈旧 revision、切换规则与 in-flight 计数、pending 覆盖/取消/关闭失败、probe 逐项 pass/warn/fail 与超时、render 参数校验、cache key/LRU/TTL/oversize/corrupt、manifest schema、`vision_pass` 的 manifest 解析/统计/上限/错误、animation 的 session-read（无 write grant 可读、无会话失败）、effective fps、requested/actual 采样范围、显式 step 超 240 拒绝、bones/collections 事实与 `bone_filter=deform|all`、collection 统计/mixed、局部关节公式、root_bone、方向位移/rotation_path/null 指标、有符号接触/穿透、世界空间 BVH/每帧预算、reference 严格校验（fps/重复帧/NaN/精确帧匹配）、`frame_current`+`frame_subframe` 恢复、静态目标 warning 与合成场景。
- 集成：fake worker 跑 vision dispatch；用临时 venv + 真 OpenCV 的 `test:vision`（CI 可选/夜间）跑 analyze/compare/detect/track/pass/selftest，覆盖 models.yml 配置、无 labels、未配置模型与形状不匹配；默认路径断言不 import onnxruntime；用 fake ORT provider 覆盖 custom venv/backend/device/auto 回退/显式 GPU 失败；真实 ORT 只在开发者机器或夜间可选任务跑，不进默认 CI；下载 RPC 用本地 stub 源与临时 models 目录；假 addon 的 animation route/dispatch 测试；假 Blender 的 queue 测试覆盖 pass 参数与 cache 提升；用假 Gateway/临时 home 覆盖 `dnn.apply`/`status`/`cancel-pending`/`probe`、Gateway 不可达、busy 时 pending 与完成后提交、关闭失败、陈旧快照、模型下载后无需重启。
- E2E：真实 Blender 4.2/4.5/5.2 渲染 1–2 帧 depth/normal/object_index/alpha，校验 manifest 与像素；真实 OpenCV worker 安装/自检，以及用户放置 ONNX + models.yml 的扩展路径；真实动画场景跑 `animation_report`，校验 effective fps、root motion/局部关节/有符号接触/世界空间穿插、`frame_current`+`frame_subframe` 恢复、无 write grant 可读；真实 Gateway 下在 `vision_detect` 进行中保存后端/venv 变更，确认新请求得到 `dnn_config_switching`、在途请求完成、空闲后自动切换，并验证 Gateway 不可达与自定义 venv 启动失败。
- 契约：catalog 36/239、8 个 vision op 在 read-only 与 host family、3 个 animation op 走 Python dispatch、`animation_report`/`animation_help` 在 `SESSION_READ_OPS` 与 `PLAN_SESSION_READ_OPS`、`animation_selftest` 在写门、`READ_ONLY_OPS`/`PLAN_READ_ONLY_OPS` 不含 animation 且保持 98、无新工具/路由/命令、`contracts.json` parity、routing 生成表、import-linter layers 负向测试（移除 animation 层应失败）。
- 验收标准：无 Blender 会话时 `vision_analyze` 可返回数字或 `stage=vision/config`；`features.dnn=false` 时 `vision_detect` 在 host 侧返回 `dnn_disabled` 且不启动 worker；默认启用后 `vision_status` 报告 `backend=opencv`/`device.used=cpu`/`experimental=true` 且默认包不含 onnxruntime；空闲时保存 DNN 配置无需重启即可生效，busy 时新请求返回 `dnn_config_switching` 且旧请求完成后再提交；`vision_status` 在切换前后报告 active/pending revision 与在途 op；Gateway 不可达时配置卡显示 `gateway_unreachable` 而非“已生效”；`dnn_config_switch_failed` 时旧 active 保持可用或明确重试；`dnn.probe` 不改配置、不启动常驻 worker、不加载模型，逐项返回 pass/warn/fail；启用但无模型时点“暂不下载”后仍是 `dnn_no_model`，不关闭 feature；自定义 venv 缺失模块返回 `vision_custom_env_invalid`，`device=gpu` 无 provider 返回 `dnn_device_unavailable`，`device=auto` 回退 CPU 且带 reason；`blender_run(passes=...)` 产出每帧 manifest 且回执无 raw 数组；`vision_pass` 能从 manifest 还原 depth 米值并给出对象覆盖；同参数二次提交 `cache_hit=true` 且不占 slot；失败/取消后无缓存 entry；`animation_report` 走 session-read、不取写租约、恢复 `frame_current`+`frame_subframe`、超预算显式降级；`animation_help` 不列 Rigify 示例；npm pack 不含 venv/模型/用户数据；模型目录 `GPU-DIY.md` 与配置卡 GitHub 链接可发现。
- 负面用例：模型 id 传路径/URL、models.yml 路径逃逸、未知 postprocessing、输出形状不匹配均返回对应 `vision_*` 错误，不加载、不联网、不静默降级；自定义 venv 解释器缺失或 import 失败返回 `vision_custom_env_invalid`，`backend=onnxruntime` 无 venv/无 ORT 返回 `dnn_backend_unavailable`，`device=gpu` 无 provider 返回 `dnn_device_unavailable`，均不回退托管环境或 CPU（`auto` 除外）；busy 时切换返回 `dnn_config_switching`、关闭失败返回 `dnn_config_switch_failed`、陈旧快照返回 `dnn_config_stale`、env 锁定返回 `dnn_env_locked`，均不写半个配置；manifest 缺字段/尺寸不一致返回 `vision_pass_manifest_invalid`，请求不在 manifest 的 pass 返回 `vision_pass_unsupported`；动画目标不存在/参考 JSON 非法返回 `animation_target_missing`/`animation_reference_invalid`；显式 step 超 240 返回 `animation_sampling_invalid`，不静默改步长；`animation_report` 无 write grant 仍可读，`animation_selftest` 无 write grant 拒绝；`bone_chains` 缺失时 joints 为空并 warning，不自动推父链。

## Risks / Trade-offs

- **首次安装依赖网络/pip** → 状态机 + 可恢复 venv + 离线手动放置 + 明确 `opencv_install_failed`；CI 夜间任务覆盖真实安装。
- **Python 3.11–3.13/平台 wheel 差异** → 版本验证 + 平台锁文件 + 校验 import/selftest；不支持 3.14，错误里写清楚。
- **托管 Python 下载体积/许可** → 固定版本+sha256、系统 Python 优先、只在缺失时下载；PSF 许可进 notices。
- **YOLOv5 GPL-3.0 与预设下载可用性** → 不打包、固定 release URL+sha256、展示来源/许可、人工确认下载；下载只写 models/，失败不破坏已有模型。
- **用户自带 ONNX 的许可/预处理错误** → 只做结构/形状校验，错误指向 models.yml 条目；许可与预处理正确性由用户负责，文档给示例；不静默猜 mean/scale/layout。
- **用户自备 GPU 环境的版本/驱动漂移** → 插件只校验解释器/import/provider，不安装、不升级、不锁定 ORT/CUDA/DirectML/OpenVINO；错误码 `vision_custom_env_invalid`/`dnn_backend_unavailable`/`dnn_device_unavailable` 指向具体缺失项与文档；文档明确 best-effort、不承诺所有显卡/驱动/平台。
- **GPU 文档与实现漂移/双语不同步** → `docs/vision-custom-backend.md` 是唯一完整事实源，`GPU-DIY.md` 只做快捷版并链接回仓库；配置卡只使用固定 GitHub URL，不内嵌易过期的长文；发布前 `check:doc-paths` 与文档 reviewer 同时核对中英两节。
- **models.yml 误改或路径逃逸** → 只接受相对路径，解析后 realpath 校验，拒绝 URL/symlink 逃逸；解析失败报文件与 key（能拿到行号时带行号），不加载模型。
- **render pass 颜色管理/坐标系错误** → `save_as_render=False`、manifest 固定编码、Blender 版本 E2E、golden 图对比；无法保证时返回 `pass_unsupported`。
- **缓存假命中/磁盘占用** → key 含 mtime/size + manifest 版本，命中前校验文件/哈希，损坏即删；LRU/TTL/单条上限；失败不提升。
- **DSH 设置未渲染/配置不一致** → 只用 Config schema 的简单字段，host→Gateway 通过环境变量传递，`doctor` 报告来源；无法读取时用默认值并可见；DNN 额外用运行时快照承载热应用，快照缺失时回退插件配置，状态文件缺失时用内存状态。
- **DNN 运行时快照并发/陈旧 revision** → 宿主单写、临时文件+rename、`revision`+`updatedAt` 比较；Gateway 只读并在 `/doctor`/每次 vision 请求边界 reconcile；陈旧快照忽略并报 `dnn_config_stale`；状态文件原子写。
- **切换时 worker 正忙/关闭失败** → in-flight 计数与 pending；新请求 `dnn_config_switching`；关闭失败保持旧 active、`dnn_config_switch_failed`，不半切换；UI 显示等待/重试/回滚；in-flight 超时/崩溃也走同一归零路径。
- **probe 误触发副作用** → 固定脚本、一次性进程、10 秒超时、不加载模型/不创建 session/不联网/不写 runtime 或模型目录；自定义 venv 的 import 副作用由用户环境负责并在文档说明。
- **Worker 安全边界** → 不接受脚本/URL/任意模型路径，图片像素上限，路径解析后只读，worker 只写 home；worker 完全不访问网络，模型下载由 host 在人类确认后执行。
- **队列/旧 workDir 迁移** → 旧目录只读、复制 JSON、不删除；`DSH_BLENDER_WORKDIR` 别名标记弃用，测试改用 `DSH_BLENDER_HOME`。
- **pass 数据格式/颜色管理错误** → manifest 固定 dtype/值域/公式，`vision_pass` 只按 manifest 解码，缺字段即失败；用真实 16-bit PNG 与 Blender 版本 E2E 校验 depth 米值、normal 相机空间与 object index 映射。
- **3D 穿插成本/语义** → 只对显式对象做离散采样、世界空间 BVH 表面重叠，预算按每采样帧所有参与对象 20 万三角形计，超限显式 skipped/degraded；文档说明完全包含但表面不相交可能漏报、不是连续碰撞、不返回穿透深度。
- **骨骼/坐标系/接触定义差异** → 统一世界坐标、米、度、`effective_fps = fps/fps_base`；关节角是局部关节角，接触用有符号 `z <= ground_z + epsilon` 并单独报告穿透；用合成骨架（父骨转/子骨不转、IK、平移 armature）与真实 rig E2E 固定 golden。
- **animation 新领域破坏边界门禁** → 同步 `runtime/py/modules.json`、`config/routing.json`、生成表和 `pyproject.toml` import-linter layers；session-read 列表与 `PLAN_READ_ONLY_OPS` 保持独立，契约测试断言 animation 不进 read-only；不通过 waiver 绕过 exhaustive 契约。
- **动画分析污染场景** → `animation_report` 在 finally 保存/恢复 `frame_current`+`frame_subframe`，不改关键帧/pose、不保存 .blend；明确 `frame_set` 会触发 depsgraph/driver/handler，属于运行时可观察而非无副作用；`animation_selftest` 只在临时 collection 创建并清理，测试断言清理后对象数不变。
- **Rigify/角色语义误判** → v1 不推断 control/mechanism/DEF，只给 `use_deform`/collections 事实与 `bone_filter`；关节链必须显式；Rigify profile 作为 future extension，不默认启用、不 import Rigify。

## Migration Plan

1. **Home/config 先行**：引入 `runtime/home.ts`，把 queue/jobs/sessions/logs/cache 的默认根切到 home；旧 `dsh-blender-rt` 只读迁移。保持 `DSH_BLENDER_WORKDIR` 别名与现有端口行为，跑全量门禁。
2. **安装与状态**：加入 `runtime/vision/installer.ts`、worker 骨架与 `vision_status`；默认后台安装，失败不影响其他工具；文档先写离线路径。
3. **视觉 op**：catalog 加 vision 家族、read-only、host dispatch、contracts，先实现 analyze/compare/help/selftest，再 detect/track 的 host 分派与参数校验（DNN 关闭时早失败）；此时无 Blender 也可用。
4. **可选 DNN**：加入 `features.dnn`、models.yml 解析、用户模型目录、YOLOv5 预设下载与默认 CPU-only 推理；默认关闭，无模型时 detect 返回 `dnn_no_model`；自定义 `vision.venv`/`backend=onnxruntime`/`device=auto|gpu` 只作为显式 DIY 路径，不进入默认安装；用样本图 + 用户放置的小 ONNX 做 golden 检测测试，再用 fake provider 覆盖 GPU 错误/回退。随后加入 `runtime.json`/`runtime-state.json`、`dnn.apply/status/cancel-pending/probe` RPC 与两阶段切换；先以空闲路径通过，再补 busy/pending、关闭失败、Gateway 不可达与自定义 venv 启动失败。
5. **Render pass（无缓存）**：只加 `passes`、固定脚本、manifest；默认路径不变，用真实 Blender E2E 验证；失败 pass 显式降级。
6. **Pass 语义分析**：在 manifest 稳定后加入 `vision_pass`；先用真实 pass 文件校验 depth 米值、normal 编码与 object index 映射，再接序列聚合；不依赖缓存。

7. **3D 动作分析**：新增 animation 领域、catalog、routing、session-read 门与 selftest；先用 evaluated depsgraph 采样、effective fps、显式 `bone_chains`，再做 root/局部关节/有符号接触，最后做世界空间 BVH 穿插与严格 reference；用真实动画 E2E 验证并更新 import-linter layers。
8. **缓存**：在 pass 稳定后启用；先观测不淘汰，再打开 LRU/TTL/上限；默认 8/4GiB/7d，设置下次启动生效。
9. **收尾**：计数/文档/CHANGELOG/behavior matrix/配置参考中的热应用与 probe 说明；`npm pack` 与 install smoke 确认不含 venv/模型/缓存；发布 next minor。

**回滚**：各步骤独立可 revert；DNN 可通过 `features.dnn=false` 停用，或把 `vision.dnn.backend` 改回 `opencv`、`vision.venv` 置空即可回到默认 CPU 路径；运行中回滚用配置卡“回滚上一版”（写回 `previous` 快照）或重新 apply 旧配置，避免手工改 `runtime.json`；视觉其他 op 不依赖模型；render pass 只在显式 `passes` 时生效，旧调用不受影响；缓存目录可删除，索引会重建。回滚不要求迁移旧 workDir 回来，也不删除用户自备 venv。

## Open Questions

- python-build-standalone 的固定 release 与各平台 sha256，以及 YOLOv5 v7.0 五个 ONNX 的 URL/sha256/字节数，需在实现时从官方 release 采集并写入 `src/host/dnn-pins.ts`；这不改变上述方案，只决定锁定值。
- OpenCV 5.x 的 `cv.FileStorage` 在解析失败时能否给出准确行号需实测；拿不到行号时错误至少带文件路径与 key。
- Blender 4.2/4.5/5.2 的 BVHTree 重叠 API 与 evaluated mesh 行为需 E2E 确认；若某版本 API 不可用，intersections 段显式 `animation_budget_exceeded` 或 degraded，不静默返回空。
- 接触检测的 `ground_z` 与默认 `contact_epsilon` 是否足以覆盖常见脚步 rig 需用真实场景校准；本期只承诺可配阈值与透明近似，不承诺物理接触精度。
- `animation_report` 是否需要在后续 change 暴露 headless 入口未定；本期只走 GUI/虚拟 GUI 会话，避免新增 job 语义。
- 各平台 ONNX Runtime 的包名/版本/provider 组合（Windows DirectML、Linux CUDA、Linux OpenVINO、AMD MIGraphX）在文档里给出经过人工验证的示例，但不作为插件锁定值；具体版本随上游变化，需在发布前复核文档。
- `dnn.probe` 的 10 秒预算在冷启动的自定义 venv（尤其首次 import CUDA/DirectML provider）上是否足够需实测；不足时只提高 probe 超时，不延长正常 worker 的 30 秒请求超时。
- Rigify 适配是否值得做成可选的 rig profile（检测 DEF/MCH/ORG collection 与前缀，输出带 `source` 的 hints/suggested chains）留作 future extension；v1 只给通用事实与显式 `bone_chains`，不 import Rigify、不默认启用、不把它做成 feature flag。
- 修改器/Geometry Nodes/粒子动画是否作为后续 `geometry_motion`/`animation_instances`/`particle_report` 能力加入 catalog，留待单独 change；v1 的 `animation_report` 只覆盖物体与骨骼运动，evaluated mesh 仅用于穿插。