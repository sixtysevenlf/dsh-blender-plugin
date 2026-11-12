# GPU / 自定义推理环境快捷说明 · GPU / Custom inference quick start

> 这是模型目录里的快捷版。完整文档（中英双语、各平台细节、排障）：
> https://github.com/sixtysevenlf/dsh-blender-plugin/blob/main/docs/vision-custom-backend.md
>
> This is the quick copy inside the model directory. Full bilingual guide:
> https://github.com/sixtysevenlf/dsh-blender-plugin/blob/main/docs/vision-custom-backend.md

## 中文

### 默认事实（先读）

- 插件默认只走 **CPU + OpenCV**：`vision.dnn.backend = opencv`、`vision.dnn.device = cpu`。
- 默认安装**不包含** `onnxruntime`，也不包含 CUDA / DirectML / OpenVINO / MIGraphX 运行时。
- 插件**不安装、不打包、不维护**任何 GPU runtime，也不会自动探测 GPU 后切换后端。GPU 是用户自备环境的 DIY 扩展点。

### DIY 步骤

1. 自建 venv（不要改系统 Python，也不要装进插件托管 venv）：
   - Windows: `py -3.12 -m venv D:\venvs\blender-ort`
   - Linux: `python3.12 -m venv ~/venvs/blender-ort`
2. 在 venv 中安装 OpenCV 与 NumPy（插件仍需 `cv2` 做模型/标签校验），以及对应平台的 ONNX Runtime：
   - Windows NVIDIA/AMD/Intel（DirectML）: `pip install onnxruntime-directml`
   - Windows/Linux NVIDIA CUDA: `pip install onnxruntime-gpu`（需匹配 CUDA/cuDNN）
   - Linux Intel: `pip install onnxruntime-openvino`
   - AMD MIGraphX 属高级 DIY，不承诺可用。
3. 在 DSH 插件设置里填：
   - `vision.venv` = 该 venv 目录（或其中的 python.exe）
   - `vision.dnn.backend` = `onnxruntime`
   - `vision.dnn.device` = `auto`（有 GPU provider 用 GPU，否则回退 CPU 并写 reason）或 `gpu`（没有 GPU provider 直接报 `dnn_device_unavailable`）
4. 点“检测可用性”（probe），再“保存并应用”。保存后无需重启 Gateway。

### 验证

- 配置卡 probe 逐项显示解释器、`cv2`/`numpy`/`onnxruntime`、providers 与 device 是否匹配。
- `vision_status` 的 `dnn.backend`、`dnn.device.used`、`dnn.provider` 与 `dnn.availableProviders` 反映实际运行环境。
- 设备显式 `gpu` 但无 provider：`dnn_device_unavailable`；`auto` 回退 CPU 时带 `reason`。

### 边界与排障

- 插件不 pip install、不改驱动、不改系统 Python、不写用户 venv。
- `vision_custom_env_invalid`：venv 不存在/不可执行、Python 不在 3.11–3.13、`import cv2/numpy` 失败。
- `dnn_backend_unavailable`：`backend=onnxruntime` 但 venv 未设置或 `import onnxruntime` 失败。
- DLL 找不到 / provider 缺失：通常是 CUDA/cuDNN 版本不匹配或驱动过旧；先在同一 venv 用 `python -c "import onnxruntime; print(onnxruntime.get_available_providers())"` 自查。
- 模型文件请放在本目录并写 `models.yml`；插件不替用户下载 ONNX（预设下载仅限 YOLOv5 v7.0 五个固定 URL）。

## English

### Default facts (read first)

- The plugin defaults to **CPU + OpenCV**: `vision.dnn.backend = opencv`, `vision.dnn.device = cpu`.
- The default install ships **no** `onnxruntime` and no CUDA / DirectML / OpenVINO / MIGraphX runtime.
- The plugin does **not** install, bundle or maintain any GPU runtime, and never auto-detects a GPU to switch the backend. GPU is a DIY extension point you own.

### DIY steps

1. Create your own venv (never modify the system Python or the plugin-managed venv):
   - Windows: `py -3.12 -m venv D:\venvs\blender-ort`
   - Linux: `python3.12 -m venv ~/venvs/blender-ort`
2. Install OpenCV and NumPy (the worker still needs `cv2` for model/label validation) plus the ONNX Runtime build for your platform:
   - Windows NVIDIA/AMD/Intel (DirectML): `pip install onnxruntime-directml`
   - Windows/Linux NVIDIA CUDA: `pip install onnxruntime-gpu` (matching CUDA/cuDNN)
   - Linux Intel: `pip install onnxruntime-openvino`
   - AMD MIGraphX is advanced DIY and not promised.
3. In the DSH plugin settings set:
   - `vision.venv` = that venv directory (or its python.exe)
   - `vision.dnn.backend` = `onnxruntime`
   - `vision.dnn.device` = `auto` (use a GPU provider when present, otherwise fall back to CPU with a reason) or `gpu` (fail with `dnn_device_unavailable` when no GPU provider exists)
4. Run “Check availability” (probe), then “Save & apply”. No Gateway restart is required.

### Verify

- The config-card probe lists interpreter, `cv2`/`numpy`/`onnxruntime`, and whether providers match the requested device.
- `vision_status` reports the actual `dnn.backend`, `dnn.device.used`, `dnn.provider` and `dnn.availableProviders`.
- Explicit `gpu` without a provider: `dnn_device_unavailable`; `auto` falling back to CPU carries a `reason`.

### Boundaries and troubleshooting

- The plugin never runs pip, never touches drivers or the system Python, and never writes into your venv.
- `vision_custom_env_invalid`: venv missing/not executable, Python outside 3.11–3.13, or `import cv2/numpy` failing.
- `dnn_backend_unavailable`: `backend=onnxruntime` without a venv or with `import onnxruntime` failing.
- Missing DLLs / providers: usually a CUDA/cuDNN version mismatch or an old driver; check in the same venv with `python -c "import onnxruntime; print(onnxruntime.get_available_providers())"`.
- Put model files in this directory and declare them in `models.yml`; the plugin does not download arbitrary ONNX for you (the preset download covers only the five fixed YOLOv5 v7.0 URLs).
