# 视觉检测自定义后端（DNN / ONNX Runtime） · Vision custom backend

适用版本：v3.1.0-alpha.1（OpenSpec change `blender-vision-opencv`）。本文件是 DNN 自定义推理环境的完整事实源；模型目录里的 `GPU-DIY.md` 是同一内容的快捷版。

## 中文

### 0. 一句话结论

DNN（`vision_detect`）默认使用 **OpenCV DNN + CPU**，不安装、不打包、也不默认 import `onnxruntime`；插件只对自定义环境做 **import / provider 校验**，安装驱动、CUDA/cuDNN、ONNX Runtime 全部由使用者自己完成。插件不保证所有平台与驱动组合可用，也不会代替你安装或维护这些包。

### 1. 边界（先读）

- **不做**：自动 `pip install`、自动装驱动、自动配 CUDA/cuDNN、联网下载模型、运行期联网校验、静默回退到未声明的 backend。
- **做**：读取你显式填写的 `vision.python` / `vision.venv` / `vision.dnn.backend` / `vision.dnn.device`；启动一次性 probe 进程校验 import 与 provider；把不满足条件的组合报成结构化错误（`vision_custom_env_invalid`、`dnn_backend_unavailable`、`dnn_device_unavailable`）。
- `backend=onnxruntime` 或 `device=auto|gpu` 时，插件只确认“包能 import、provider 在列表里”，**不保证**模型在你的驱动/显卡上一定跑得起来。
- 预设模型来自 ultralytics 官方 release，许可 **GPL-3.0**；使用与再分发前请自行确认许可义务。
- 本文不涉及任何凭据；插件不需要 token、账号或云端密钥。

### 2. 模型目录与 `models.yml`

默认模型目录（`vision_status.dnn.docs.localPath` 会给出真实路径）：

```text
$DSH_HOME/dsh-blender/vision/models/
  GPU-DIY.md              # 本文件的快捷版，插件激活时缺失才复制，不覆盖用户文件
  coco80.txt              # 预设下载时补齐的 80 类标签
  models.yml              # 模型清单（OpenCV FileStorage 可读）
  <你的子目录>/*.onnx      # 任意深度，宿主递归扫描
```

`models.yml` 用 OpenCV 5 `samples/dnn/models.yml` 的键名与语义；宿主不解析 YAML，worker 用 `cv.FileStorage` 读取。顶层 key 就是模型 id（对应 `vision.model`）：

```yaml
%YAML:1.0
---
yolov5n:
  model: yolov5n.onnx
  labels: coco80.txt
  width: 640
  height: 640
  postprocessing: yolov5
  rgb: true
```

校验规则（probe 与 worker 一致）：

- 必填：`model`、`width`、`height`、`postprocessing`；`labels` 可省略。
- `postprocessing` 本期只支持 `yolov5`。
- `width` / `height` 必须为正数。
- `rgb` 可省略，接受 `true/false`、`0/1`、`yes/no`、`on/off`（大小写不敏感）；宿主下载弹窗写的就是 `rgb: true`。
- `model` / `labels` 必须是模型目录内的相对路径；绝对路径、`..` 逃逸与符号链接越界会被拒绝。
- `labels` 文件必须存在且非空（当前不强制 80 行，自定义类别集可以不是 COCO）。
- 缺少 `vision.model` 对应条目 → `vision_model_unconfigured`；条目存在但文件缺失 → `vision_model_missing`；字段/后处理/路径非法 → `vision_model_unsupported`；启用了 DNN 但模型目录完全没有 `.onnx` → `dnn_no_model`。

下载弹窗只会写 `models.yml` 里**不存在**的同名 key；已有条目不会被覆盖。手动放模型时把 `.onnx` 放进模型目录任意子目录，并在 `models.yml` 增加顶层 key。

#### 2.1 render pass manifest 字段

`vision_pass` 读 `blender_run(kind="render", passes=[...])` 写出的 manifest：

- depth：`dtype:"uint16"`、`range:[0,1]`、`surfaces:camera`、`near`/`far`、`noGeometryValue:0`（**唯一**无效值）、`clampedAt:"far"`、`decodeFormula`。渲染侧把 `[near, far]` 重映射到 `[1/65535, 1]`，所以 near 平面对应原始值 1/65535；原始值 0 专门表示“无几何”，不是 near。远端裁到 far，表现为 `clampedAt:"far"`。
- normal：`encoding:"n*0.5+0.5"`、`coordinateSpace:"camera"`。Normal pass 在 Cycles 与 EEVEE 下都是世界空间，渲染脚本对**每个引擎**都做世界→相机的 numpy 转换（Cycles 也走同一条路径），所以 manifest 声明相机空间时消费者无需再转换。
- object_index：`mapping`（`pass_index` → 对象名）与 `background:0`；`object_index` **只支持 Cycles**，EEVEE / Workbench 下请求它会得到 `stage=input` 的 `pass_unsupported`，错误里点名当前引擎并提示改用 `CYCLES` 或去掉该 pass。
- 分析结果在 `roi.stride > 1` 时带 `sampled:true`（像素计数是采样计数，不是全图计数）；object_index 行另带 `unmapped_pixels`，统计不在 `mapping` 里的非背景索引。

### 3. 两种推理后端

| backend | 默认 | 运行位置 | 依赖 | 说明 |
|---|---|---|---|---|
| `opencv` | ✅ | 本机 CPU | 托管 venv 内的 `opencv-python-headless` + `numpy` | 不开 GPU，不 import `onnxruntime`；`device` 必须是 `cpu` |
| `onnxruntime` |  | 你提供的 venv | 你自行安装的 `onnxruntime` 发行包 | 插件只校验 import 与 providers；`device=cpu/auto/gpu` |

托管 venv 由插件创建与维护（默认在 `$DSH_HOME/dsh-blender/vision/venv`）。一旦设置非空 `vision.venv`，插件**完全使用你的 venv**：不创建、不 pip、不回退。因此你的 venv 里必须至少有：

```text
numpy
opencv-python-headless          # 用于读图、FileStorage、OpenCV 后端
onnxruntime 或平台对应发行包      # 仅 backend=onnxruntime 时需要
```

### 4. 配置字段

| 字段 | 默认 | 含义 |
|---|---|---|
| `features.dnn` | `false` | DNN 总开关。关闭时 `vision_detect` 在启动 worker 前返回 `dnn_disabled` |
| `vision.python` | `""` | 指定解释器绝对路径；留空自动探测 3.13→3.12→3.11 |
| `vision.venv` | `""` | 自定义 venv 根目录绝对路径；留空使用托管 venv |
| `vision.model` | `yolov5n` | `models.yml` 顶层 key；必须匹配 `^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$`，路径/URL 不是合法 id（传路径/URL 的 `vision_detect` 返回 `vision_model_unsupported`） |
| `vision.dnn.backend` | `opencv` | `opencv` 或 `onnxruntime` |
| `vision.dnn.device` | `cpu` | `cpu` / `auto` / `gpu`；`opencv` 只允许 `cpu` |

环境变量 `DSH_BLENDER_DNN` 只接受 `on/true/1` 与 `off/false/0`（大小写不敏感），优先级最高，并**锁定** `features.dnn`：配置卡只冻结这一个开关；尝试改开关的 `dnn.apply` 返回 `dnn_env_locked` 且不写运行时快照。其它非空值（如 `yes`、`maybe`）不算锁：忽略该变量，沿用配置里的 `features.dnn`，doctor 报 `dnn_env_invalid` 并带原值。其他 DNN 字段仍可保存并热应用；移除变量并重启 Gateway 后才能改开关。

`vision.*` 的层叠语义：键缺省表示沿用下层；空字符串同样按“继承”处理（不是清空）。`runtime.json` 里的 `vision.*: null` 是显式清空，覆盖低层值并让字段回到未配置（空）。在 DSH 设置里把字段清回默认值则不再序列化到 `DSH_BLENDER_PLUGIN_CONFIG`，于是回落 home/config 层。

### 5. 平台组合（示例，不构成承诺）

以下只是常见安装方式；包名/版本随 ONNX Runtime 发布变化，请以官方文档为准。

#### 5.1 Windows · CPU（最省事）

```powershell
py -3.11 -m venv D:\venvs\blender-onnx
D:\venvs\blender-onnx\Scripts\python.exe -m pip install --upgrade pip
D:\venvs\blender-onnx\Scripts\python.exe -m pip install numpy opencv-python-headless onnxruntime
```

配置卡：`vision.venv = D:\venvs\blender-onnx`、`backend = onnxruntime`、`device = cpu`。

#### 5.2 Windows · NVIDIA / AMD / Intel GPU（DirectML）

```powershell
D:\venvs\blender-onnx\Scripts\python.exe -m pip install onnxruntime-directml
```

provider 期望包含 `DmlExecutionProvider`。`device=gpu` 在没有 DML provider 时会失败；`device=auto` 会回退 CPU 并给出 warn。

#### 5.3 Windows / Linux · NVIDIA CUDA

```powershell
# Windows
D:\venvs\blender-onnx\Scripts\python.exe -m pip install onnxruntime-gpu
```

```bash
# Linux
python3.11 -m venv ~/venvs/blender-onnx
~/venvs/blender-onnx/bin/python -m pip install onnxruntime-gpu
```

provider 期望包含 `CUDAExecutionProvider`。CUDA / cuDNN 版本必须与安装的 `onnxruntime-gpu` 版本匹配；驱动太旧时 import 可能成功但 provider 初始化失败。插件只报 `dnn_device_unavailable` / `dnn_backend_unavailable`，不替你修驱动。

#### 5.4 Linux · Intel GPU（OpenVINO）

```bash
python3.11 -m venv ~/venvs/blender-onnx
~/venvs/blender-onnx/bin/python -m pip install onnxruntime-openvino
```

provider 期望包含 `OpenVINOExecutionProvider`。是否需要系统 OpenVINO runtime 取决于发行包版本。

#### 5.5 Linux · AMD（MIGraphX，高级 DIY）

MIGraphX EP 通常不在标准 PyPI 包里，需要自行构建 ONNX Runtime 或使用带该 EP 的发行版。插件不提供构建支持、不承诺可用；`device=gpu` 找不到 provider 时会明确失败，不会静默伪装成功。

#### 5.6 macOS（可选）

onnxruntime CPU 包可用；CoreML EP 需对应构建。本仓库当前只承诺文件协议与校验逻辑，不承诺具体加速效果。

### 6. 热应用、probe 与状态机

保存 DNN 配置不需要重启 Gateway：

1. 配置卡把字段写成 `$DSH_HOME/dsh-blender/vision/runtime.json`（原子写：临时文件 + rename；revision 单调递增；不含密钥）。
2. Gateway 在下一次 `/doctor` 与每个 `vision_*` 请求边界读取快照并 reconcile。
3. 需要换 worker 的字段（`vision.python`、`vision.venv`、`vision.dnn.backend`、`vision.dnn.device`）走两阶段切换：空闲时立即切换；有在途请求时只登记 pending，新 worker-backed 请求返回 `dnn_config_switching`（`retryable=true`，带 active/pending revision 与 `waitingFor`），在途请求用旧 revision 完成。
4. 只改 `features.dnn` 或 `vision.model` 不重启 worker，也不重启 Gateway：`vision.model` 随每个 worker-backed 请求下发（`frame.config = {revision, model}`），下一次请求即用新模型；`vision_status` 与成功回执都回显生效的 `configRevision`/`activeRevision`。切换失败时 phase=`failed`、保留 pending，新请求返回 `dnn_config_switch_failed`；可以“取消等待”用当前生效配置覆盖，或“回滚上一版”。

worker 协议：stdin/stdout 各一行一个 JSON（`{id, op, args, configRevision?, config?}`），每帧（去掉行尾换行后）上限 **8 MiB**；超限的完整行同样被拒绝（`vision_protocol_error`），不是只拒未闭合的残行。worker 不回传像素，只回标量/路径与 `configRevision`。

配置卡与 `vision_status.dnn.config` 会显示：`activeRevision`、`pendingRevision`、`phase`（idle/pending/switching/failed）、`waitingFor`、`lastSwitchError`、`previousRevision`，以及 `gateway.reachable`。Gateway 不可达时保存返回 `gateway_unreachable` + `savedForNextStart=true`，不会显示“已生效”。

“检测可用性”运行一次性 probe 进程（10 秒超时，不经 shell）：

- 不写 `runtime.json`、不改 Gateway 配置、不启动常驻 worker、不加载 ONNX 模型、不联网、不安装任何东西。
- 逐项返回 `pass/warn/fail`：解释器版本（3.11–3.13）、`import cv2`、`import numpy`、`import onnxruntime`、providers × device、worker 协议模块、模型目录、`.onnx` 数量、`models.yml`、`vision.model` 条目/labels/路径逃逸。
- `ok` = 没有 fail；warn 不阻止保存。
- 超时 → `dnn_probe_timeout`；进程/解析异常 → `dnn_probe_failed`。

预设下载（仅在人类确认后）：URL / 字节数 / SHA-256 固定在 `src/host/dnn-pins.ts`；单并发；先写 `.part`，校验字节数与 SHA-256 后原子 rename；写 `coco80.txt`；向 `models.yml` 追加不覆盖已有 key；失败清理 `.part`。下载进程不联网（worker 也不联网）。

### 7. 验证与排障

先看 `vision_status` 的 `dnn` 块与配置卡状态；再点“检测可用性”读逐项结果。

```bash
# 解释器与包（用你填进 vision.venv 的那个 python）
D:\venvs\blender-onnx\Scripts\python.exe -c "import sys, cv2, numpy; print(sys.version); print(cv2.__version__); print(numpy.__version__)"
D:\venvs\blender-onnx\Scripts\python.exe -c "import onnxruntime as ort; print(ort.__version__); print(ort.get_available_providers())"
```

| 现象 | 原因 | 处理 |
|---|---|---|
| `vision_custom_env_invalid` | 自定义 venv 缺 cv2/numpy，或解释器不可执行 | 用该 venv 的 python 安装依赖；`vision.venv` 填绝对路径 |
| `dnn_backend_unavailable` | `backend=onnxruntime` 但 import 失败 | 在该 venv 安装对应平台的 onnxruntime 发行包 |
| `dnn_device_unavailable` | `device=gpu` 但没有 GPU provider | 安装 `onnxruntime-gpu`/`onnxruntime-directml`，检查 CUDA/cuDNN/驱动；或改 `auto`/`cpu` |
| `ImportError: DLL load failed` | CUDA/cuDNN/VC++ 运行库缺失或版本不匹配 | 对齐 onnxruntime-gpu 与 CUDA/cuDNN 版本；装 VC++ Redistributable |
| provider 列表没有期望项 | 包类型不对（CPU 包没有 CUDA EP） | 换对应发行包，不要混装多种 onnxruntime |
| `dnn_no_model` | 模型目录无 `.onnx` | 下载预设或手动放模型 |
| `vision_model_unconfigured` | `vision.model` 在 `models.yml` 没有条目 | 增加顶层 key 或改 `vision.model` |
| `vision_model_missing` | 条目存在但文件不在 | 检查相对路径与文件名大小写 |
| `vision_model_unsupported` | 字段/后处理/路径非法 | 按第 2 节校验规则修正 |
| `dnn_env_locked` | 尝试修改被 `DSH_BLENDER_DNN` 锁定的 `features.dnn` | 移除环境变量并重启 Gateway；其他 DNN 字段不受影响 |

### 8. 相关文档

- 操作教程：[操作教程.md](操作教程.md)
- 配置参考：[配置参考.md](配置参考.md)
- 架构说明：[architecture.md](architecture.md)
- 模型目录快捷版：`vision/models/GPU-DIY.md`（插件激活时缺失才创建，不覆盖用户文件）

## English

### 0. One-paragraph summary

DNN (`vision_detect`) defaults to **OpenCV DNN on CPU**. The plugin does not install, bundle, or import `onnxruntime` by default; it only validates imports and execution providers in an environment you provide. Drivers, CUDA/cuDNN, and ONNX Runtime packages are your responsibility. No platform/driver combination is guaranteed, and the plugin never installs or maintains these packages for you.

### 1. Boundaries

- **Not done by the plugin**: automatic `pip install`, driver/CUDA/cuDNN setup, network model downloads, runtime network calls, or silent fallback to an undeclared backend.
- **Done by the plugin**: reading the explicit `vision.python` / `vision.venv` / `vision.dnn.backend` / `vision.dnn.device` values, running a one-shot probe for imports and providers, and returning structured errors (`vision_custom_env_invalid`, `dnn_backend_unavailable`, `dnn_device_unavailable`).
- With `backend=onnxruntime` or `device=auto|gpu`, the plugin only verifies that the package imports and that a provider is listed. It does **not** guarantee that a model runs on your GPU/driver.
- Preset models come from the official ultralytics releases and are licensed **GPL-3.0**; check the license before use or redistribution.
- This document contains no credentials. The plugin needs no token, account, or cloud key.

### 2. Model directory and `models.yml`

Default model directory (the real path is reported as `vision_status.dnn.docs.localPath`):

```text
$DSH_HOME/dsh-blender/vision/models/
  GPU-DIY.md              # quick-start copy of this document; created when missing, never overwrites
  coco80.txt              # 80 COCO labels, written together with preset downloads
  models.yml              # model manifest (readable by OpenCV FileStorage)
  <your subdirs>/*.onnx    # any depth; the host scans recursively
```

`models.yml` follows the key names and semantics of OpenCV 5 `samples/dnn/models.yml`. The host does not parse YAML; the worker reads it with `cv.FileStorage`. The top-level key is the model id used by `vision.model`:

```yaml
%YAML:1.0
---
yolov5n:
  model: yolov5n.onnx
  labels: coco80.txt
  width: 640
  height: 640
  postprocessing: yolov5
  rgb: true
```

Validation rules (probe and worker agree):

- Required: `model`, `width`, `height`, `postprocessing`; `labels` is optional.
- Only `postprocessing: yolov5` is supported in this release.
- `width` / `height` must be positive.
- `rgb` is optional and accepts `true/false`, `0/1`, `yes/no`, `on/off` (case-insensitive); the host download dialog writes `rgb: true`.
- `model` / `labels` must be relative paths inside the model directory; absolute paths, `..` escapes, and symlinks that leave the directory are rejected.
- A `labels` file must exist and be non-empty (80 lines are not enforced; custom class sets are allowed).
- Missing `vision.model` entry → `vision_model_unconfigured`; entry exists but file missing → `vision_model_missing`; invalid fields/postprocessing/path → `vision_model_unsupported`; DNN enabled but no `.onnx` anywhere → `dnn_no_model`.

The download dialog only appends `models.yml` keys that do not already exist; existing entries are never overwritten. For manual models, drop the `.onnx` into any subdirectory and add a top-level key.

#### 2.1 Render pass manifest fields

`vision_pass` reads the manifest written by `blender_run(kind="render", passes=[...])`:

- depth: `dtype:"uint16"`, `range:[0,1]`, `surfaces:camera`, `near`/`far`, `noGeometryValue:0` (the **only** invalid value), `clampedAt:"far"`, `decodeFormula`. The renderer remaps `[near, far]` onto `[1/65535, 1]`, so the near plane is raw 1/65535; raw 0 means “no geometry” exclusively, never near. The far end is clipped to far and reports `clampedAt:"far"`.
- normal: `encoding:"n*0.5+0.5"`, `coordinateSpace:"camera"`. Blender's Normal pass is world space under Cycles and EEVEE, and the render script applies the world→camera numpy conversion for **every engine** (Cycles included), so consumers must not convert again.
- object_index: `mapping` (`pass_index` → object name) and `background:0`; `object_index` is **Cycles-only**. Requesting it under EEVEE or Workbench returns `pass_unsupported` with `stage=input`; the error names the current engine and suggests switching to `CYCLES` or dropping the pass.
- When `roi.stride > 1`, analyses carry `sampled:true` (pixel counts are sample counts, not full-image counts); object_index rows additionally carry `unmapped_pixels` for non-background indexes absent from `mapping`.

### 3. The two backends

| backend | Default | Runs on | Dependencies | Notes |
|---|---|---|---|---|
| `opencv` | ✅ | Local CPU | `opencv-python-headless` + `numpy` in the managed venv | No GPU, never imports `onnxruntime`; `device` must be `cpu` |
| `onnxruntime` |  | A venv you provide | An ONNX Runtime distribution you install | The plugin only checks imports/providers; `device=cpu/auto/gpu` |

The managed venv is created and maintained by the plugin (default `$DSH_HOME/dsh-blender/vision/venv`). Once `vision.venv` is non-empty, the plugin **uses that venv exclusively**: it does not create it, pip-install into it, or fall back. Your venv must contain at least:

```text
numpy
opencv-python-headless          # image IO, FileStorage, OpenCV backend
onnxruntime or a platform package  # only for backend=onnxruntime
```

### 4. Configuration fields

| Field | Default | Meaning |
|---|---|---|
| `features.dnn` | `false` | Master DNN switch. When off, `vision_detect` returns `dnn_disabled` before starting a worker |
| `vision.python` | `""` | Absolute interpreter path; empty means auto-detect 3.13→3.12→3.11 |
| `vision.venv` | `""` | Absolute custom venv root; empty means the managed venv |
| `vision.model` | `yolov5n` | Top-level key in `models.yml`; must match `^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$`, so paths/URLs are not valid ids (`vision_detect` with a path/URL returns `vision_model_unsupported`) |
| `vision.dnn.backend` | `opencv` | `opencv` or `onnxruntime` |
| `vision.dnn.device` | `cpu` | `cpu` / `auto` / `gpu`; `opencv` allows only `cpu` |

The environment variable `DSH_BLENDER_DNN` accepts only `on/true/1` and `off/false/0` (case-insensitive). It has the highest priority and **locks** `features.dnn`: the config card freezes only that switch; a `dnn.apply` that changes the switch returns `dnn_env_locked` and does not write the runtime snapshot. Any other non-empty value (for example `yes` or `maybe`) does not lock anything: the variable is ignored, the configured `features.dnn` stays in effect, and doctor reports `dnn_env_invalid` with the raw value. Other DNN fields can still be saved and hot-applied; remove the variable and restart the Gateway to edit the switch again.

Layering of `vision.*`: an absent key inherits from the layer below; an empty string is also “inherit”, not “clear”. In `runtime.json`, `vision.*: null` is an explicit clear: it overrides lower layers and leaves the field unconfigured (empty). Clearing the field back to its default in DSH settings removes the key from `DSH_BLENDER_PLUGIN_CONFIG`, so the home/config layer applies again.

### 5. Platform recipes (examples, not promises)

Package names and versions change with ONNX Runtime releases; consult the official documentation.

#### 5.1 Windows · CPU (simplest)

```powershell
py -3.11 -m venv D:\venvs\blender-onnx
D:\venvs\blender-onnx\Scripts\python.exe -m pip install --upgrade pip
D:\venvs\blender-onnx\Scripts\python.exe -m pip install numpy opencv-python-headless onnxruntime
```

Config card: `vision.venv = D:\venvs\blender-onnx`, `backend = onnxruntime`, `device = cpu`.

#### 5.2 Windows · NVIDIA / AMD / Intel GPU (DirectML)

```powershell
D:\venvs\blender-onnx\Scripts\python.exe -m pip install onnxruntime-directml
```

Expect `DmlExecutionProvider`. `device=gpu` fails when no DML provider exists; `device=auto` falls back to CPU with a warning.

#### 5.3 Windows / Linux · NVIDIA CUDA

```powershell
D:\venvs\blender-onnx\Scripts\python.exe -m pip install onnxruntime-gpu
```

```bash
python3.11 -m venv ~/venvs/blender-onnx
~/venvs/blender-onnx/bin/python -m pip install onnxruntime-gpu
```

Expect `CUDAExecutionProvider`. The CUDA/cuDNN versions must match the installed `onnxruntime-gpu`; an old driver may import fine but fail to initialize the provider. The plugin reports `dnn_device_unavailable` / `dnn_backend_unavailable`; it does not repair drivers.

#### 5.4 Linux · Intel GPU (OpenVINO)

```bash
python3.11 -m venv ~/venvs/blender-onnx
~/venvs/blender-onnx/bin/python -m pip install onnxruntime-openvino
```

Expect `OpenVINOExecutionProvider`. Whether a system OpenVINO runtime is also required depends on the distribution package.

#### 5.5 Linux · AMD (MIGraphX, advanced DIY)

The MIGraphX EP is typically not in the standard PyPI wheels; you must build ONNX Runtime or use a distribution that carries it. The plugin offers no build support and no guarantee. `device=gpu` fails explicitly when no provider is present; it never pretends success.

#### 5.6 macOS (optional)

ONNX Runtime CPU wheels work; the CoreML EP requires a matching build. This repository commits to the file protocol and validation logic, not to a specific acceleration result.

### 6. Hot apply, probe, and the state machine

Saving DNN configuration does not require a Gateway restart:

1. The config card writes `$DSH_HOME/dsh-blender/vision/runtime.json` atomically (temp file + rename, monotonic revision, no secrets).
2. The Gateway reads the snapshot at the next `/doctor` and at every `vision_*` request boundary.
3. Fields that require a new worker (`vision.python`, `vision.venv`, `vision.dnn.backend`, `vision.dnn.device`) use a two-phase switch. When idle the switch happens immediately. When requests are in flight only a pending revision is registered; new worker-backed requests return `dnn_config_switching` (`retryable=true` with active/pending revisions and `waitingFor`), and in-flight requests finish on the old revision.
4. Changing only `features.dnn` or `vision.model` restarts neither the worker nor the Gateway: `vision.model` travels with every worker-backed request (`frame.config = {revision, model}`), so the next request uses the new model, and `vision_status` plus successful receipts echo the applied `configRevision`/`activeRevision`. A failed switch leaves phase=`failed` and keeps the pending revision; new requests return `dnn_config_switch_failed`. Use “cancel waiting” to overwrite pending with the active configuration, or “roll back” to the previous revision.

Worker protocol: one JSON frame per line over stdin/stdout (`{id, op, args, configRevision?, config?}`), capped at **8 MiB per frame after stripping the trailing newline**. A complete oversized line is rejected too (`vision_protocol_error`); the cap is not limited to an unterminated remainder. The worker never returns pixels, only scalars/paths and `configRevision`.

The config card and `vision_status.dnn.config` expose `activeRevision`, `pendingRevision`, `phase` (idle/pending/switching/failed), `waitingFor`, `lastSwitchError`, `previousRevision`, and `gateway.reachable`. When the Gateway is unreachable, saving returns `gateway_unreachable` with `savedForNextStart=true` and never claims the change is active.

“Check availability” runs a one-shot probe process (10 s timeout, no shell):

- It does not write `runtime.json`, change Gateway configuration, start a resident worker, load an ONNX model, use the network, or install anything.
- Each check returns `pass/warn/fail`: interpreter version (3.11–3.13), `import cv2`, `import numpy`, `import onnxruntime`, providers × device, worker protocol module, model directory, `.onnx` count, `models.yml`, and the `vision.model` entry/labels/path escape.
- `ok` means no failures; warnings do not block saving.
- Timeout → `dnn_probe_timeout`; process/parse failures → `dnn_probe_failed`.

Preset downloads (only after explicit human confirmation): URL, byte count, and SHA-256 live in `src/host/dnn-pins.ts`; downloads are single-concurrency; bytes go to `.part`, are verified, then atomically renamed; `coco80.txt` is written; `models.yml` entries are appended without overwriting; `.part` files are cleaned up on failure. The download process does not use the network beyond the pinned URLs, and the worker never uses the network.

### 7. Verification and troubleshooting

Start with the `dnn` block of `vision_status` and the config-card status, then click “Check availability” for per-item results.

```bash
# The interpreter you put in vision.venv
D:\venvs\blender-onnx\Scripts\python.exe -c "import sys, cv2, numpy; print(sys.version); print(cv2.__version__); print(numpy.__version__)"
D:\venvs\blender-onnx\Scripts\python.exe -c "import onnxruntime as ort; print(ort.__version__); print(ort.get_available_providers())"
```

| Symptom | Cause | Fix |
|---|---|---|
| `vision_custom_env_invalid` | Custom venv lacks cv2/numpy, or the interpreter cannot run | Install dependencies with that venv's python; use an absolute `vision.venv` |
| `dnn_backend_unavailable` | `backend=onnxruntime` but import failed | Install the matching ONNX Runtime distribution in that venv |
| `dnn_device_unavailable` | `device=gpu` but no GPU provider | Install `onnxruntime-gpu`/`onnxruntime-directml`, check CUDA/cuDNN/driver, or use `auto`/`cpu` |
| `ImportError: DLL load failed` | Missing/mismatched CUDA, cuDNN, or VC++ runtime | Match onnxruntime-gpu to CUDA/cuDNN; install the VC++ Redistributable |
| Expected provider is absent | Wrong package type (CPU wheel has no CUDA EP) | Install the correct distribution; do not mix multiple onnxruntime packages |
| `dnn_no_model` | No `.onnx` in the model directory | Download presets or place a model manually |
| `vision_model_unconfigured` | `vision.model` has no entry in `models.yml` | Add a top-level key or change `vision.model` |
| `vision_model_missing` | Entry exists but the file is gone | Check the relative path and filename case |
| `vision_model_unsupported` | Invalid fields/postprocessing/path | Follow the validation rules in section 2 |
| `dnn_env_locked` | Tried to change `features.dnn` while `DSH_BLENDER_DNN` locks it | Remove the variable and restart the Gateway; other DNN fields are unaffected |

### 8. Related documents

- Operation guide: [操作教程.md](操作教程.md)
- Configuration reference: [配置参考.md](配置参考.md)
- Architecture: [architecture.md](architecture.md)
- Model-directory quick start: `vision/models/GPU-DIY.md` (created at plugin activation only when missing; never overwrites user files)
