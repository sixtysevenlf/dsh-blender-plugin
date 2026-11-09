# Proposal

## Why

3.1 已经完成 Gateway 单端点、反向连接 addon、六工具面与作业队列，但"视觉"仍停在 Blender 进程内的截图与 NumPy：`img_*` / `qc_*` 每次分析都占 bpy，缺少 OpenCV 的轮廓、配准、光流与 DNN 能力；`blender_run` 的 render 作业也不产出 depth / normal / object_index 等 pass，无法做可复核的像素级判断。3.1 design 已经把逐像素分析明确留给后续的 `blender_op` vision change（`openspec/changes/gateway-topology-3-1/design.md:84`），现在 3.1.0-alpha.1 已落地，是补上这层能力的时候。

不做这件事，3.1 的"see 看一眼"仍只能把 PNG 交给模型目测：没有稳定的 2D 量测、没有渲染与参考图的自动配准、没有序列运动分析，也没有可复用的 GPU 渲染 pass。用户已经明确：视觉 worker 由 Gateway 管理，所有持久数据放在 `$DSH_HOME/dsh-blender/`，系统 Python 3.11+ 可接受，默认检测模型用 YOLOv5 v7.0，渲染 pass 缓存默认 8 条 / 4 GiB 且可在 DSH 插件设置里改。本 change 把这些决定固化成可验收的契约。

## What Changes

- **统一 home 与配置**：新增 `$DSH_HOME/dsh-blender/` 数据根（config、state、logs、jobs、sessions、queue、cache、vision），保留 `DSH_BLENDER_HOME` / 用户级 / 项目级覆盖顺序；插件激活时幂等创建目录，不上传、不打包用户数据。
- **Gateway 托管的视觉 worker**：Gateway 拉起一个私有 Python 子进程，stdin/stdout 私有管道通信，不监听端口，不占用 Blender Python；状态机覆盖 Python 发现、venv 创建、OpenCV 安装、校验与失败。用户不需要手动 pip，也不需要管理员权限。
- **OpenCV 自动安装**：默认 `opencv-python-headless==5.0.0.93` + `numpy>=2`，只装进 `$DSH_HOME/dsh-blender/vision/venv`；系统 Python 支持 3.11/3.12/3.13，优先 3.13；没有可用 Python 时给出结构化 `vision_unavailable`（stage/root_cause/fix/fallback），不在系统 Python 里 pip，不把 cv2 装进 Blender。
- **`blender_op` 新增 `vision` 家族（7 个 op）**：`vision_status`、`vision_analyze`、`vision_compare`、`vision_detect`、`vision_track`、`vision_selftest`、`vision_help`。全部 host-local、read-only、不需要 GUI 会话；仍通过现有 `blender_op` 暴露，不新增第七个模型工具。
- **默认检测器**：YOLOv5 v7.0 ONNX，默认 `yolov5n`（约 3.8 MB），可在配置里切 v7.0 的 s/m/l/x；模型从官方 release 固定 URL + sha256 下载到 `vision/models/`，不随 npm 包分发（GPL-3.0），首次检测时下载并明确展示来源与许可。不内置 YOLOX/NanoDet/YOLOv3/YOLO26，不做通用 ONNX 导入，也不做模型市场。
- **渲染 pass 与缓存**：`blender_run(kind=render, passes=[...])` 新增 `beauty`、`depth`、`normal`、`object_index`、`alpha`（默认只出 beauty，保持旧行为）；每帧写 manifest（分辨率、dtype、值域、单位、坐标系、object index→对象名），不把 raw 数组塞进工具回执。新增 8 条 / 4 GiB / 7 天 LRU 缓存，按 scene + frame + camera + resolution + samples + engine + pass set 命中；单条超过 2 GiB 不缓存并给出 warning；缓存命中直接返回 completed job，不占 GPU slot。
- **DSH 插件设置**：`Config` schema 增加 `cache.render.*`（maxEntries / maxBytes / ttlDays）、`vision.*`（python、detector、model、autoInstall）等字段，由 DSH 插件设置 UI 编辑；未配置时使用上述默认值。缓存与视觉状态在 `doctor` / `vision_status` 中可读。
- **契约与计数**：catalog 从 34 family / 228 op 变为 35 family / 235 op；6 个模型工具、29 条 HTTP 路由、25 个注册 addon 命令和 protocol 12 不变。更新 `config/contracts.json`（vision 为 host family）、`config/plan-ids.json` / lock、behavior matrix 与文档。
- **非目标**：不新增模型工具；不改 3.1 Gateway/addon 拓扑；不做 CLI；不做 CUDA/GPU OpenCV；不做通用 DNN 平台或模型市场；不在本 change 重写既有 `img_*` / `qc_*` 的算法与 verdict（只新增 vision 契约；共享原语的替换在 golden parity 之后另开 change）。

## Capabilities

### New Capabilities

- `blender/plugin-home`: `$DSH_HOME/dsh-blender/` 数据根、配置解析优先级、目录生命周期，以及 DSH 设置中暴露的缓存/视觉字段。
- `blender/vision`: Gateway 托管的 Python/OpenCV worker 生命周期、自动安装、`vision_*` op 契约、YOLOv5 检测器、错误与许可边界。
- `blender/render-passes`: `blender_run(kind=render)` 的 pass 集合、manifest、object index 映射与有界缓存。

### Modified Capabilities

- `plan-op-dispatch`: catalog 新增 host-local `vision` 家族；它必须在没有 GUI 会话时可解析、在 `contracts.json` 中登记为 host family，并且不进入 Python dispatch 表。

## Impact

- **代码**：`src/plugin.ts`（Config schema）、`src/tools/op/read-only.ts`（vision 只读 op）、`src/tools/op/execute.ts`（会话门）、`runtime/catalog/data.ts`、`runtime/core/plan-dispatch.ts`（vision host-local 入口）、`runtime/execution` / `runtime/core/queue`（render passes + cache）、`runtime/home` / `runtime/config.ts`（home 与设置）、新增 `runtime/vision/*`、`runtime/py/vision_worker/*`、`scripts/vision_install.py` 或等价安装器。
- **依赖**：不新增 Node 运行时依赖（如需下载 standalone Python，仅用现有 Node 能力或明确登记的 helper）；Python 依赖由 worker 的 venv 在运行时安装，不进入 npm pack。
- **API/契约**：`blender_op` 增加 7 个 op（35/235），`blender_run` 增加可选 `passes` 与 render 产物 manifest；无新工具、无新 HTTP 路由、无协议变更。
- **配置/数据**：新增 `$DSH_HOME/dsh-blender/` 持久目录；新增 `cache.render.*` 与 `vision.*` 配置；不写系统 Python、不写 Blender 安装目录。
- **文档/测试**：architecture、配置参考、操作教程、CHANGELOG、behavior matrix、catalog lock、contract parity、settings UI 冒烟测试、OpenCV/YOLO 许可说明。
- **风险**：首次安装需要网络；Python 版本差异；OpenCV wheel 体积（Windows 约 44 MB）；YOLOv5 GPL-3.0 许可；缓存命中/失效的边界；DSH 设置 UI 与 Config schema 的兼容性。
