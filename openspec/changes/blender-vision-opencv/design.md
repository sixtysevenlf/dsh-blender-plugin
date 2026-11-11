# Design

## Context

本 change 构建在 3.1 Gateway 拓扑之上，见 `proposal.md`。当前约束：

- 3.1 已把主机侧收敛为六个模型工具（`session/op/py/see/run/inspect`），Gateway 单端点，addon 反向连接；本 change 不改变拓扑，不新增工具或 HTTP 路由。
- 现有 `img_*` / `qc_*` 在 Blender Python 内运行（`runtime/py/render/imgtools_impl/api.py`、`runtime/py/render/qc_impl/api.py`），依赖 bpy/numpy；本 change 不重写它们，只新增 Gateway 侧视觉契约。
- `blender_run(kind=render)` 目前由 `runtime/core/queue/spawn-executor.ts` 的 `renderPrelude` 生成固定 `--python-expr`，只输出 beauty；queue 没有 pass、manifest 或缓存。
- 持久数据当前默认落在 `%LOCALAPPDATA%\dsh-blender-rt`（`runtime/config.ts`），配置由 `runtime/config.ts` 的静态 `CFG` 和环境变量/JSON 文件读取；host 侧 `src/plugin.ts:38-52` 的 `Config` 只有 `port/autoStart/tui`。
- 所有持久数据必须迁到 `$DSH_HOME/dsh-blender/`；旧的 `dsh-blender-rt` 只作为迁移来源读取，不自动删除。

## Goals / Non-Goals

**Goals:**

- 提供一个 Gateway 直属、无监听端口、可自动安装/自检的 Python/OpenCV worker，让视觉分析不占 Blender、不要求 GUI 会话。
- 把 home、配置优先级、DSH 设置字段和 `doctor` 可见性固化成一套，后续缓存/视觉状态不再散落。
- 让 render pass 成为可复核的数据集：固定 pass 集合、每帧 manifest、不把像素塞进工具回执。
- 用有界、可失效、原子化的缓存避免重复渲染，且缓存不改变 render 的旧行为。
- 所有失败都能定位到 `config/vision/input`，并给出可执行修复或明确 fallback。

**Non-Goals:**

- 不迁移/重写既有 `img_*` / `qc_*` 算法；共享原语替换留给后续 golden-parity change。
- 不做 CUDA/GPU OpenCV、不做通用 DNN 平台/模型市场、不做 CLI、不做第七个模型工具、不改 3.1 协议与路由。
- 不把 OpenCV 装进 Blender Python，不对系统 Python 执行全局 pip。
- 不把 YOLOv5 模型打进 npm 包，不承诺 GPU 检测性能。

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
- `features.dnn`：默认 false，布尔；DNN 检测总开关，保存后由宿主重启 Gateway 生效，env `DSH_BLENDER_DNN=off/on` 可覆盖，`doctor` 报告生效值与来源。
- `vision.python`：默认空字符串，表示自动发现；非空时必须是可执行路径。
- `vision.model`：默认 `yolov5n`，格式必须是模型 id（与 models.yml 顶层 key 同一规则）；检测时按 id 解析，不存在时返回对应的 `dnn_no_model`/`vision_model_missing`/`vision_model_unconfigured`；不允许枚举固定列表，因为用户可扩展模型；不接受路径/URL。

使用现有 schemastery `z.object` / `z.natural` / `z.boolean` 约束；DSH 插件设置按 3.1 已有的 Config schema 渲染，不新增 `dsh.client.inject` 页面。`apply()` 只把合法值序列化进 `DSH_BLENDER_PLUGIN_CONFIG`，非法值由 schema 拒绝或回退默认并在 `doctor` 报告字段/来源；不把任何密钥写入该环境变量。

`features.dnn` 只有这一层开关，不设 `allowDnn` 第二层许可：DNN 不拉起隐藏 GUI、不写桌面，开启与否本身就是人的选择。关闭时 `vision_detect` 在 host 侧直接返回 `dnn_disabled`，不启动 worker。

**替代方案**：自定义设置页 —— 需要新客户端 bundle 和 DSH 设置插槽，收益低；本 change 沿用现有 `src/client/index.js` 配置卡的开关/弹窗，只加 schema 字段和 DNN 下载交互。

### D3. Vision worker：Gateway 私有子进程，JSON-lines 协议

新增 `runtime/vision/`：

- `manager.ts`：至多一个 worker 子进程；首次视觉请求时启动，空闲 10 分钟回收；崩溃/超时/协议错乱时杀掉进程树并按“最多 3 次/5 分钟”重启，重启后重新握手。
- `protocol.ts`：stdin/stdout 上的换行分隔 JSON；请求 `{id, op, args}`，响应 `{id, ok, result|error}`；单帧上限 8 MiB，超过即协议错误；每次请求 30 秒超时；worker 不下载模型，因此没有下载放宽。
- `worker/`：Python 包（`main.py`、`analyze.py`、`compare.py`、`detect.py`、`track.py`、`selftest.py`、`models.py`、`protocol.py`）。它不在 `runtime/py/modules.json` 中，不注册为 Blender 模块，不 import bpy；它与 `runtime/py` 的域模块完全隔离；`models.py` 用 `cv.FileStorage` 读取 `vision/models/models.yml`（OpenCV 5 语法），不在 Node 侧引入 YAML 解析依赖；worker 不发起任何网络请求。
- worker 环境只继承最小变量（`PATH`、代理、home），cwd 为 home，Windows 用 `windowsHide` + 独立进程组；stderr 追加到 `home/logs/vision-worker.log`，保留最后 256 KiB。
- worker 不监听端口；manager 不通过 HTTP、WebSocket 或命名管道暴露它。Gateway 的其他请求/作业不受其崩溃影响。

**替代方案**：常驻独立 sidecar 进程/本地端口 —— 需要端口和额外生命周期，违反“Gateway owns worker、无监听”的约束；不采用。

### D4. Python 发现与安装：系统优先，托管解释器兜底

新增 `runtime/vision/installer.ts` 与 `runtime/vision/python-detect.ts`，状态写入 `home/vision/state.json`，状态机：

`not_installed → resolving_python → (downloading_python) → creating_venv → installing_opencv → verifying → ready | failed`

- 发现顺序：`vision.python` 显式路径 → `DSH_BLENDER_PYTHON` → Windows `py -3.13/-3.12/-3.11` → `python3.13/3.12/3.11` → `python3`/`python`；每个候选验证版本在 3.11–3.13 且能 `import venv`。优先 3.13，其次 3.12、3.11。
- 没有系统解释器时，下载固定版本的 python-build-standalone CPython 3.13（Windows/Linux 各固定 URL + sha256）到 `home/vision/python/`，用系统 `tar` 解压，校验 `python -c "import venv"` 后再用；该来源在 `vision_status` 标为 `managed`。没有网络或校验失败返回 `python_missing`，不回退到系统 pip。该安装只涉及 Python/OpenCV，与 `features.dnn` 和 YOLOv5 模型下载无关。
- venv 建在 `home/vision/venv`，先建 `venv.tmp-<pid>`，成功校验后原子 rename；失败清理临时目录，不留下半可用 venv。
- 依赖固定为 `opencv-python-headless==5.0.0.93` + `numpy>=2`，由 `runtime/vision/requirements.lock` 按平台锁定并提供 sha256；pip 以 `--require-hashes --only-binary=:all: --no-input` 安装，pip cache 指向 `home/vision/cache/pip`，不升级系统 pip 除非验证失败后显式重试。
- 安装器用原子 `mkdir` 锁（`home/vision/install.lock`，含 pid/时间，10 分钟过期）防并发；失败最多重试 2 次；每次失败写 `home/logs/vision-install.log` 并把阶段、退出码、代理建议、离线安装 fallback 放进状态。
- 安装成功后写 `home/vision/THIRD_PARTY_NOTICES.md`（OpenCV Apache-2.0、numpy BSD、Python PSF）并在 `vision_status.licenses` 给出路径；不复制 OpenCV 源码/头文件。

**替代方案**：只在系统 Python 上 `pip --user` —— 污染系统环境、权限不可控；不采用。只支持系统 Python 不下载托管解释器 —— 干净机器会永久不可用，不采用。

### D5. Vision op 契约：7 个 host-local op，结果是数字不是像素

`blender_op` 新增 `vision` 家族，全部 `requires_session=false`、只读、无需 lease/虚拟显示。catalog 标记 host family，`runtime/core/plan-dispatch.ts` 在 addon 之前短路到 `runtime/vision/dispatch.ts`；`src/tools/op/read-only.ts` 加入这 7 个 op；`config/contracts.json` 的 host family 声明供 parity 使用。任何 `vision_*` 都不进入 Python dispatch 表、不触发 Blender 启动。

| op | 参数 | 返回（有界） |
|---|---|---|
| `vision_status` | 无 | home、安装状态、python/cv2/numpy 版本、worker pid/启动时间、`features.dnn` 生效值/来源、模型目录、可用/未配置模型、下载状态、许可路径 |
| `vision_analyze` | `path`、可选 `roi` / `maxContours` | 宽高/通道、颜色均值/标准差、清晰度（Laplacian 方差）、曝光（高/低光裁剪百分比）、前景 bbox、最多 32 条轮廓摘要、最多 32 条直线摘要 |
| `vision_compare` | `ref`、`candidate`、可选 `align`（`ecc`/`orb`）、`roi` | 对齐方法、2x3 变换、inliers、IoU、平均绝对差、变化像素百分比、变化 bbox、预览路径 |
| `vision_detect` | `path`、可选 `model`（模型 id）、`conf`、`iou`、`maxDetections` | 检测数组（class、可选 label、confidence、xyxy），最多 100 条；模型 id/来源/许可；未启用或无模型时返回 `dnn_disabled`/`dnn_no_model` |
| `vision_track` | `frames`（路径数组或 manifest 路径）、`method`（`farneback`/`lk`） | 每帧运动摘要/轨迹，最多 120 帧、20 条轨迹；不返回完整光流场 |
| `vision_selftest` | 无 | 合成图上的 import/颜色/轮廓/ECC/检测前向检查结果；DNN 未启用或无模型时该检查标记为 skipped |
| `vision_help` | 无 | 7 个 op 的参数、单位、限制与示例；`models.yml` 字段与模型 id 用法 |

约定：像素坐标原点左上、x 向右、y 向下；bbox 用 `[x0,y0,x1,y1)`、0-based；长度单位像素；深度单位米。所有回执不含完整像素/mask/光流数组，只含标量、小数组、路径与 manifest 引用。图片仅接受已存在文件路径（绝对或相对 cwd），不接受 URL/通配/脚本；解码后像素上限 64 MP，超过返回 `vision_image_too_large`。

`vision_analyze` 的前景检测规则写进注释与 `vision_help`：有 alpha 用 alpha>0；无 alpha 用 Otsu 阈值反相后最大连通域；都不可靠时返回 `foreground:null` 并说明。`vision_compare` 先 ECC（`MOTION_EUCLIDEAN`），失败再用 ORB+单应；对齐失败返回 `aligned:false`，不伪造 IoU。`vision_track` 的 Farneback 只返回位移/散度/旋度摘要，LK 只返回有限轨迹点。

**替代方案**：把这些塞进 `see` 或 `inspect` —— `see` 是 GUI 采集，`inspect` 是只读文本，都会混淆边界；不采用。

### D6. DNN 是可选 feature flag，模型由 OpenCV 5 models.yml 声明

DNN 只保留一个门：`features.dnn`（DSH 插件设置开关；host `Config.features.dnn` 默认 false；Gateway 从 `DSH_BLENDER_DNN=off/on` 或 `DSH_BLENDER_PLUGIN_CONFIG` 读取，暴露 `CFG.features.dnn` 与 source）。不设 `allowDnn` 第二层许可：DNN 不拉起隐藏 GUI、不写桌面，开启与否本身就是人的选择。关闭时 `vision_detect` 在 host 侧返回 `stage=config`、`code=dnn_disabled`、`retryable=false`，不启动 worker；agent 没有打开该 flag、下载模型或传任意路径的接口。

模型目录为 `$DSH_HOME/dsh-blender/vision/models/`，根下 `models.yml` 采用 OpenCV 5 `samples/dnn/models.yml` 的键名与语义。宿主不解析 YAML：worker 的 `models.py` 用 `cv.FileStorage` 读取并校验，避免新增 Node YAML 依赖；宿主只递归扫描 `models/**/*.onnx` 判断是否完全没有 ONNX（用于 `dnn_no_model` 早失败和下载弹窗）。模型以顶层 key 作为 id，示例：

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

**用户扩展**：用户把 `my.onnx` 放到 `vision/models/` 并写一条 `models.yml` 条目即可被 `vision_detect` 使用；`vision_status` 同时列出已配置模型和未配置的 `.onnx`（含原因）。模型由用户自带时，许可与预处理正确性由用户负责，工具只做结构校验和形状检查。

**下载预设**：仅 YOLOv5 v7.0 的 `yolov5n/s/m/l/x`，来源与 sha256 固定在宿主 `src/host/dnn-pins.ts`，不读 models.yml 的 URL。启用 `features.dnn` 且 `models/` 下没有任何 `.onnx` 时，DSH 配置卡通过现有 owner-only `/doctor` 的 `dnn` 块读取 `onnxCount/downloadable/download`，弹出复选框列表（`yolov5n` 默认勾选，n/s/m/l/x 可多选，显示大小、来源、GPL-3.0）；开关打开/保存后立即按待生效值检查并弹窗，不等 Gateway 重启，下载完成后再提示重启使检测生效。确认后由宿主 RPC `downloadDnnModels` 下载（不新增 HTTP 路由）：先写 `.part`，校验 sha256/字节数后原子 rename，写 `coco80.txt`，再向 `models.yml` 追加对应条目（已存在同名 key 不覆盖、不重复写）。单并发、失败清理 `.part`；worker 始终不联网。

点“暂不下载”只关闭弹窗，不关闭 `features.dnn`；配置卡保留警告和“下载模型”入口，开关生效后 `vision_detect` 返回 `dnn_no_model`（无 ONNX）或 `vision_model_unconfigured`（有 ONNX 但没配置），并附“请人在 DSH 插件设置中启用/下载”的修复说明。agent 不得自动下载、不得改用路径/URL、不得回退到其他模型。

**推理**：`cv2.dnn.readNetFromONNX` + `cv2.dnn.NMSBoxes`；letterbox 到条目声明的 `width/height`，YOLOv5 输出 `(1,N,85)`（N 由模型输入尺寸决定，不硬编码 25200），按 `xywh→xyxy` 解码，obj×class 过滤，NMS 默认 `conf=0.25/iou=0.45`（可覆盖），最多 100 条；坐标映射回原图并裁剪，class 按 labels 映射（无 labels 时只给 id）。输出形状或 postprocessing 不匹配返回 `vision_model_unsupported`，不返回空结果。DNN 后端只用 CPU，不尝试 CUDA；`vision_selftest` 在 feature 关闭或无模型时将 DNN 检查标为 skipped，不算失败。

**替代方案**：旧的固定模型表 + `ensure_model` 自动下载 —— 不能用户扩展，且 agent 会在用户不知情时联网下载；不采用。任意 ONNX 路径/URL —— 绕过许可与配置校验；不采用。在 Node 里引入 YAML 解析器 —— 增加运行时依赖，且与 OpenCV 语义可能漂移；改用 worker 的 `cv.FileStorage`。

### D7. Render pass：可选、固定 Python、manifest 先行

`src/tools/run/execute.ts` 的 `RENDER_KEYS` 增加 `passes`；`passes` 必须是允许集合的非空子集，未知/重复/空数组在 host 侧拒绝。未传 `passes` 时完全走旧 `renderPrelude`/输出路径，不写 manifest、不写缓存。

传 `passes` 时，Gateway 在 queue 的 `buildJobArgs` 中写两个固定文件到 job 临时目录：`render_passes.py` 与 `render-config.json`（含 passes、frames、outdir、camera、resolution、samples、engine、format）。Blender 仍以 `-b --factory-startup <file>` 启动，再 `--python render_passes.py`；脚本不接收调用方代码。脚本：

1. 设置相机、分辨率、samples、engine、帧范围；开启 view layer 的 Combined/Z/Alpha/Normal/ObjectIndex pass。
2. 对 `object_index`：按 `(name, type)` 排序给对象分配稳定的 `pass_index`（1..N），写映射；只作用本次进程，不保存 .blend。
3. 构建 compositor：Render Layers → 每 pass 一个 File Output 节点；beauty 用请求的 `format`；depth 用 Z pass 经 Map Range 到 `[0,1]` 后 16-bit PNG；normal 用 `n*0.5+0.5` 的 RGB 8-bit PNG；object_index 用 IndexOB 16-bit PNG；alpha 8-bit PNG。非 beauty 节点设 `save_as_render=False` 以避免颜色管理污染数据。
4. 每帧输出到 `<outdir>/passes/frame_<frame:04d>/`，渲染完成后写该帧的 `manifest.json`（最后写）；manifest 至少含 `manifestVersion`、frame、camera、resolution、samples、engine、passes、每 pass 的 path/dtype/range/colorspace、depth 的 near/far/解码公式、normal 编码、object_index 映射。
5. 渲染结束打印一行 `PASSES_DONE {json}` 供 executor 读取；失败/缺帧/缺 manifest 时退出非零。

depth 的 near/far 取相机 `clip_start/clip_end`（不是 view layer 的 mist 范围），manifest 公式为 `depth_m = near + (pixel/65535)*(far-near)`；normal 的坐标系为世界空间还是相机空间必须在 manifest 固定为相机空间并写编码；object_index 的 0 表示背景，映射只含实际对象。具体 Blender 4.2/4.5/5.2 的 pass 可用性用 E2E 验证，不可用时该 pass 返回 `pass_unsupported` 而非静默缺文件。

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

- catalog：`runtime/catalog/data.ts` 新增 `f:"vision", prefix:"vision_", requires_session:false, write_scope:"none"` 家族 7 op；总数 34/228 → 35/235。6 工具、29 路由、25 注册命令（+ping=26 wire）、protocol 12 不变。
- `config/contracts.json` 增加 vision host family（host 入口、7 op、hostReason）；catalog↔Python parity 把 host family 计为覆盖。任何未声明的 host family 或缺 op 让门禁失败。
- `src/tools/op/read-only.ts` 增加 7 op；`src/tools/op/execute.ts` 的会话门由 catalog `requires_session=false` 放行，不额外维护第二份白名单（如必须保留白名单，则加契约测试断言两份一致）。
- 文档同一 change 更新：`docs/architecture.md`（home、vision worker、render passes）、`docs/配置参考.md`（新字段/目录/上限）、`docs/操作教程.md`（首次安装、离线、许可、doctor）、CHANGELOG、behavior matrix、catalog lock、`docs/zh` 对应页。
- 视觉 worker 的 Python 文件加入 `pyrightconfig.python.json` / ruff 的扫描范围；不加入 `runtime/py/modules.json`，避免被 Blender 模块注册表误认。

### D10. 测试与验收

- 单元：home 解析/创建失败、配置优先级与非法值、worker 协议分帧/超时、Python 发现顺序、venv 状态机（fake python）、DNN feature 门与错误优先级、models.yml 解析/路径约束/labels、YOLO 解码/NMS（合成张量）、下载清单/sha256/原子 rename、render 参数校验、cache key/LRU/TTL/oversize/corrupt、manifest schema。
- 集成：fake worker 跑 vision dispatch；用临时 venv + 真 OpenCV 的 `test:vision`（CI 可选/夜间）跑 analyze/compare/detect/selftest，覆盖 models.yml 配置、无 labels、未配置模型与形状不匹配；下载 RPC 用本地 stub 源与临时 models 目录；假 Blender 的 queue 测试覆盖 pass 参数与 cache 提升。
- E2E：真实 Blender 4.2/4.5/5.2 渲染 1–2 帧 depth/normal/object_index/alpha，校验 manifest 与像素；真实 OpenCV worker 安装/自检，以及用户放置 ONNX + models.yml 的扩展路径。
- 契约：catalog 35/235、7 个 vision op 在 read-only 与 host family、无新路由/工具/命令、`contracts.json` parity 负向测试（移除 host family 应失败）。
- 验收标准：无 Blender 会话时 `vision_analyze` 可返回数字或 `stage=vision/config`；`features.dnn=false` 时 `vision_detect` 在 host 侧返回 `dnn_disabled` 且不启动 worker；启用但无模型时点“暂不下载”后仍是 `dnn_no_model`，不关闭 feature；`blender_run(passes=...)` 产出每帧 manifest 且回执无 raw 数组；同参数二次提交 `cache_hit=true` 且不占 slot；失败/取消后无缓存 entry；npm pack 不含 venv/模型/用户数据。
- 负面用例：模型 id 传路径/URL、models.yml 路径逃逸、未知 postprocessing、输出形状不匹配均返回对应 `vision_*` 错误，不加载、不联网、不静默降级。

## Risks / Trade-offs

- **首次安装依赖网络/pip** → 状态机 + 可恢复 venv + 离线手动放置 + 明确 `opencv_install_failed`；CI 夜间任务覆盖真实安装。
- **Python 3.11–3.13/平台 wheel 差异** → 版本验证 + 平台锁文件 + 校验 import/selftest；不支持 3.14，错误里写清楚。
- **托管 Python 下载体积/许可** → 固定版本+sha256、系统 Python 优先、只在缺失时下载；PSF 许可进 notices。
- **YOLOv5 GPL-3.0 与预设下载可用性** → 不打包、固定 release URL+sha256、展示来源/许可、人工确认下载；下载只写 models/，失败不破坏已有模型。
- **用户自带 ONNX 的许可/预处理错误** → 只做结构/形状校验，错误指向 models.yml 条目；许可与预处理正确性由用户负责，文档给示例；不静默猜 mean/scale/layout。
- **models.yml 误改或路径逃逸** → 只接受相对路径，解析后 realpath 校验，拒绝 URL/symlink 逃逸；解析失败报文件与 key（能拿到行号时带行号），不加载模型。
- **render pass 颜色管理/坐标系错误** → `save_as_render=False`、manifest 固定编码、Blender 版本 E2E、golden 图对比；无法保证时返回 `pass_unsupported`。
- **缓存假命中/磁盘占用** → key 含 mtime/size + manifest 版本，命中前校验文件/哈希，损坏即删；LRU/TTL/单条上限；失败不提升。
- **DSH 设置未渲染/配置不一致** → 只用 Config schema 的简单字段，host→Gateway 通过环境变量传递，`doctor` 报告来源；无法读取时用默认值并可见。
- **Worker 安全边界** → 不接受脚本/URL/任意模型路径，图片像素上限，路径解析后只读，worker 只写 home；worker 完全不访问网络，模型下载由 host 在人类确认后执行。
- **队列/旧 workDir 迁移** → 旧目录只读、复制 JSON、不删除；`DSH_BLENDER_WORKDIR` 别名标记弃用，测试改用 `DSH_BLENDER_HOME`。

## Migration Plan

1. **Home/config 先行**：引入 `runtime/home.ts`，把 queue/jobs/sessions/logs/cache 的默认根切到 home；旧 `dsh-blender-rt` 只读迁移。保持 `DSH_BLENDER_WORKDIR` 别名与现有端口行为，跑全量门禁。
2. **安装与状态**：加入 `runtime/vision/installer.ts`、worker 骨架与 `vision_status`；默认后台安装，失败不影响其他工具；文档先写离线路径。
3. **视觉 op**：catalog 加 vision 家族、read-only、host dispatch、contracts，先实现 analyze/compare/help/selftest，再 detect/track 的 host 分派与参数校验（DNN 关闭时早失败）；此时无 Blender 也可用。
4. **可选 DNN**：加入 `features.dnn`、models.yml 解析、用户模型目录与 YOLOv5 预设下载；默认关闭，无模型时 detect 返回 `dnn_no_model`；用样本图 + 用户放置的小 ONNX 做 golden 检测测试。
5. **Render pass（无缓存）**：只加 `passes`、固定脚本、manifest；默认路径不变，用真实 Blender E2E 验证；失败 pass 显式降级。
6. **缓存**：在 pass 稳定后启用；先观测不淘汰，再打开 LRU/TTL/上限；默认 8/4GiB/7d，设置下次启动生效。
7. **收尾**：计数/文档/CHANGELOG/behavior matrix；`npm pack` 与 install smoke 确认不含 venv/模型/缓存；发布 next minor。

**回滚**：各步骤独立可 revert；DNN 可通过 `features.dnn=false` 停用，视觉其他 op 不依赖模型；render pass 只在显式 `passes` 时生效，旧调用不受影响；缓存目录可删除，索引会重建。回滚不要求迁移旧 workDir 回来。

## Open Questions

- python-build-standalone 的固定 release 与各平台 sha256，以及 YOLOv5 v7.0 五个 ONNX 的 URL/sha256/字节数，需在实现时从官方 release 采集并写入 `src/host/dnn-pins.ts`；这不改变上述方案，只决定锁定值。
- OpenCV 5.x 的 `cv.FileStorage` 在解析失败时能否给出准确行号需实测；拿不到行号时错误至少带文件路径与 key。
