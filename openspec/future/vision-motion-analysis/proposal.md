# Proposal

## Why

3.1 的 `see` 只返回 8-bit RGB 预览，`watch` 吸收进 `see` 后也只保留“帧 + hash”的能力；`op` 里的 imgtools 是纯 bpy + numpy，能测轮廓、尺寸、颜色和简单差分，但拿不到最终渲染的精确深度、法线、对象可见性，也无法做像素级运动/时序分析。结果是 agent 面对“这个画面里前后关系是什么、哪个物体被遮挡、这段动画有没有抖动/穿帮”时只能靠看图猜，缺少可复核的数字证据。

3.1 已经把 `run`（唯一作业入口）和 `op`（catalog 分析层）的边界定下来，适合在其上补一层“视觉与运动证据”，而不是把 OpenCV 或 render pass 塞进 `see`，破坏交互式预览的延迟和语义。因此本 change 独立于 3.1，等 3.1 落地后再实施。

## What Changes

- `run(kind="render")` 增加声明式 `passes`（至少 depth / normal / object_index，可扩展）。每个 pass 作为作业产物落盘，并返回 manifest：路径、帧、分辨率、dtype、值域、坐标系/单位，以及 object index → 对象名的映射。原始数组不进入 tool response。
- 新增只读的 `op` 分析能力（具体 op 名由 design 定）：
  - pass 统计：depth 范围/分位数/ROI、normal 分布、object index 每对象覆盖率/可见像素/遮挡关系；
  - 2D 运动：帧差/变化区域、光流（方向/幅度/一致性）、特征跟踪、时序稳定性（闪烁/抖动/跳变）、与参考序列对比；
  - 结果以数字表、掩膜路径、verdict 和可选彩色预览返回；预览最多 1–2 张，原始数组不返回。
- 新增 3D 动作分析 `op`：从 bpy 的物体/骨骼/F-Curve 采样，输出 root motion、关节角/角速度、接触/脚滑、穿插、轨迹偏差等；不依赖 OpenCV，可与 2D 证据组合成最终 QA。
- `see` 不新增 pass 或分析职责：保持“只采集 GUI viewport 的 RGB 预览/序列帧”。需要看 depth/normal/object index 的彩色预览时，由 `op` 生成并附图。
- OpenCV 作为**可选分析器**：只在 host/Gateway 侧的独立 sidecar venv 使用（建议 `opencv-python-headless`），不装进 Blender/addon 的 Python；缺少 OpenCV 时分析 op 返回 `degraded`/`missing_dependency` 和安装提示，不静默假通过，也不影响 3.1 的 see/run 基础能力。
- 计数与契约：catalog family/op 计数显式更新，行为矩阵新增对应行；六工具数量不变（仍是 6），不新增第七个工具。
- 文档：操作教程、架构、配置参考、skill 增加 pass/分析/依赖安装/降级说明；中英文同步。

## Capabilities

### New Capabilities

- `blender/render-passes`: render pass 的声明式生成、产物流转、manifest 契约、object index 映射、坐标/单位/格式与失败处理。
- `blender/vision-analysis`: 对渲染图、帧序列和 pass 的 2D 数字分析（depth/normal/object coverage、帧差、光流、特征跟踪、稳定性、参考对比），以及 OpenCV 可选 sidecar 的依赖/降级契约。
- `blender/animation-analysis`: 从 Blender 场景、骨骼和 F-Curve 采样 3D 动作指标（root motion、关节、接触/脚滑、穿插、轨迹）的只读分析契约。

### Modified Capabilities

无。项目当前 `openspec/specs/` 为空；本 change 依赖 3.1 的 `run`/`op`/`see` 边界，但不修改 3.1 的 spec。

## Impact

- **代码**：`runtime/execution/*`（render job 的 passes 与产物）、`runtime/job-queue`（artifact manifest）、`runtime/catalog`（新增分析 op 与计数）、`runtime/py`（pass 分析、2D 运动、3D 动作模块）、host 侧 `op` 路由与 schema；`src` 只改参数面和路由，不新增模型工具。
- **API/协议**：`run` 的 render 参数增加 `passes`，job 产物增加 pass manifest；`op` 增加只读分析 op；不改变 3.1 的六工具数量。无破坏性变更；OpenCV 是可选依赖。
- **依赖/平台**：可选 Python sidecar（OpenCV + NumPy），不随默认安装捆绑，不装进 Blender/addon 的 Python；缺依赖时明确降级并给出安装路径。默认安装和 3.1 的 see/run 不依赖 OpenCV。
- **测试与门禁**：pass 产物/manifest 与 object index 映射；depth 单位/坐标系；每对象覆盖率/遮挡；光流/帧差/稳定性基准序列；OpenCV 缺失时的 degraded 负向测试；响应中不出现原始数组；catalog 计数与 behavior matrix 同步。
- **文档/用户可见**：`docs/architecture.md`、`docs/操作教程.md`、`docs/配置参考.md`、`skills/blender-modeling/*`、CHANGELOG；中英文同步；说明 pass 产物位置、分析结果含义、可选 OpenCV 安装与降级。
- **非目标**：不做通用 CV 平台、ML 检测/分割/OCR/人脸；不从图像反推 3D 几何；不让 `see` 返回 pass 或原始数组；不把 cv2 装进 Blender Python；不在 3.1 同一 release 交付；不新增模型工具；不用 2D 分析替代 Blender 的 3D 真值。
