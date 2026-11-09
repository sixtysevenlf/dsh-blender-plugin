# OpenCV 替换矩阵与 ONNX 检测器候选

> 状态：explore 工作笔记（非规范 artifact），2026-11-09。
> 目的：把“哪些算法原语迁到 Gateway / OpenCV、哪些留在 Blender”和“YOLO/ONNX 可选型号”固定成可回看的表。
> 口径：Blender 仍拥有 bpy IO、渲染、场景真值、色彩管理；Gateway 拥有 2D/序列/文件分析。OpenCV 是插件必备能力，不是可选功能。

## 0. 本轮已确认（2026-11-09）

| 项 | 决定 |
|---|---|
| render pass cache | 默认 **8 个 pass-set / 4 GiB**（原 16/8 缩减一半）；LRU、7 天过期；可在 DSH 插件配置界面改 |
| vision 进程 | Gateway 管的 Python 子进程；不是 Blender Python sidecar，也不是用户自管服务 |
| 数据目录 | 全部在 `$DSH_HOME/dsh-blender/` 下；首次激活静默创建 |
| OpenCV 安装 | 静默安装，插件功能的一部分；失败明确报 `vision_unavailable`，不是可选降级 |
| Python | 优先系统 3.11+；默认 3.13，向下兼容 3.11；覆盖 Blender 4.2/4.5（3.11）与 5.2（3.13） |
| 检测器 | 默认安装 **YOLOv5 v7.0 ONNX**（默认 `yolov5n`，GPL-3.0 提示）；其余模型不做内置，用户自行解决 |

## A. 迁移边界（先定原则）

| 留在 Blender Python | 迁到 Gateway / OpenCV |
|---|---|
| bpy.data.images 读写、alpha、色彩管理、EXR/PNG 编码 | 掩膜、阈值、形态学、连通域、轮廓、边缘、直线/圆、单应、光流、模板匹配 |
| 渲染、视口取帧、render pass、object index 真值 | 参考图与渲染图的 2D 配准、IoU/边界/剖面、颜色/曝光统计、序列帧差 |
| 场景对象/骨骼/接触/相机求解 | 不依赖场景的纯像素与文件分析、缓存、批量任务 |
| 需要 bpy 上下文才能拿到的数据 | 只吃图片/数组/文件的分析 |

关键纪律：
- 迁移的是**算法原语**，不是报告外壳。`img_digest` / `qc_*` 的回执 schema、verdict、provenance、阈值语义先保持不变。
- 先做新旧并跑（golden tests + side-by-side），再切换；不得用 OpenCV 结果直接替换旧阈值而不重新基线化。
- 不在 Blender 的 Python 里装 cv2；也不把 OpenCV 做成 Blender 的依赖。

## B. OpenCV 替换矩阵

| 现有位置/函数 | 现在做什么 | 迁移后归属 | OpenCV 替代 | 备注 |
|---|---|---|---|---|
| `imgtools_impl/common._load/_save`、`qc_impl/load.qc_load` | bpy 图像读写、alpha、色彩管理 | Blender 保留 | 不替换（只做边界转换） | OpenCV 是 BGR/uint8；保留 Blender 的线性/色彩管理语义 |
| `_gray`、`_to_u8`、`_scale_rgb`、`_hstack` | numpy 灰度/缩放/拼接 | Gateway | `cvtColor`、`resize`、`hconcat` | 插值方式必须显式固定（NEAREST/LINEAR/AREA） |
| `_mask_of`、`qc_mask`、`qc_mask_auto`、`_otsu` | 边框背景估计 + Otsu / alpha 掩膜 | Gateway | `cvtColor`、`threshold(THRESH_OTSU)`、`inRange`、`morphologyEx` | 保留“alpha 优先、Otsu 兜底、异常前景回落”的语义 |
| `_bbox`、`_largest_cc_mask`、`_label_cc` | bbox、最大连通域、行-run 并查集 | Gateway | `boundingRect`、`connectedComponentsWithStats` | 当前纯 numpy 并查集可整体替换；注意 4/8 邻接口径 |
| `_dilate`、`_erode1`、`_fill_rc` | 方形膨胀/腐蚀、行列填充 | Gateway | `dilate`、`erode`、`floodFill` / `morphologyEx` | 行列填充是旧实现特性，需 golden 对照 |
| `img_scan`（mask/矩/梯度方向直方图/列行剖面） | 轮廓 bbox、长宽比、填充率、主轴角、边方向、站表原料 | Gateway | `findContours`、`moments`、`Sobel`、`calcHist`、`boundingRect` | 输出字段与坐标约定不能变 |
| `imgtools_impl/hough.py` | Hough 直线/圆 | Gateway | `HoughLinesP`、`HoughCircles` | 旧阈值/最小长度/合并规则要逐项对照 |
| `imgtools_impl/contour.py` | 轮廓、多边形近似、孔洞、圆拟合 | Gateway | `findContours`、`approxPolyDP`、`minEnclosingCircle`、`contourArea` | 孔洞 hierarchy 与旧输出要一一对应 |
| `img_crop` | ROI 裁剪 + 最近邻上采样 | Gateway | `resize(..., INTER_NEAREST)` / `warpAffine` | 明确“不引入插值假细节”的旧意图 |
| `img_annotate` | 画线/框/十字/网格，写回 PNG | Gateway | `line`、`rectangle`、`drawMarker`、`putText` | 字体可用 OpenCV Hershey；旧 legend 语义保留 |
| `img_diff` | 掩膜 IoU、bbox 对齐、列/行剖面、红蓝叠加 | Gateway | `bitwise_and/or`、`absdiff`、`warpAffine`、`resize` | IoU 仍可用 numpy；OpenCV 只替换像素操作 |
| `img_rectify` | 4 点 DLT + 双线性采样 | Gateway | `getPerspectiveTransform` / `findHomography` + `warpPerspective` | 4 点精确解用 getPerspectiveTransform；多点/RANSAC 才用 findHomography |
| `img_probe`（亚像素边缘/扫描线） | 梯度峰值抛物线亚像素边界 | Gateway | `Sobel`/`Scharr` + 自定义抛物线；必要时 `cornerSubPix` | 亚像素定义是旧契约，不建议直接换 Canny |
| `img_measure` | px→mm、ROI 统计、剖面 | Gateway | 主要 numpy；`contourArea`、`arcLength` | 没有 known_len 绝不产 mm 的纪律不变 |
| `img_calib` | 多视标定/单应/尺度 | Gateway | `calibrateCamera`、`solvePnP`、`findHomography` | 先确认当前标定模型（针孔/平面）再替换 |
| `qc_align_iou`、`_align_search`、`qc_align_rotate` | bbox 归一、尺度/平移/角度搜索 | Gateway | `warpAffine`、`matchTemplate`、`findTransformECC`、`phaseCorrelate` | 搜索空间、初始对齐、IoU 口径必须保持 |
| `qc_compare_auto`、`qc_robustness_check`、`qc_mask_sweep` | 掩膜 + 对齐 + 指标 + overlay/sheet | Gateway（渲染源仍在 Blender） | 上述组合 | 报告外壳、阈值、engine_at_qc 仍在 Blender 侧采集 |
| `qc_profile`、`_profile_diff` | 逐层宽度/剖面差 | Gateway | `resize`/reduction + numpy | 纯统计可继续 numpy |
| `pnggif._gif_first_frame`、`gif_to_png` | GIF LZW 首帧解码 | Gateway | OpenCV GIF 支持不稳定；用 Pillow 或保留纯 Python | 非主路径，别为它引入重依赖 |
| `img_digest` | 编排 load/mask/scan/crop atlas/report | Gateway 编排 + OpenCV 原语 | 上述组合 | 保持回执 schema 与 provenance |
| `visualreport`、`blockout`、`assembly` 的图像比对 | 报告合成；调用 qc/imgtools | Gateway vision；Blender 只负责渲染/收集 | 上述组合 | 先迁移只读分析，再删重复 numpy |
| `qc_render_views` | Blender 多视角渲染 + 比对 | 渲染留 Blender；分析去 Gateway | `imread`/上述 | 不在 vision 里做 viewport/render |
| `shapesolve` 轮廓拟合 | 渲染轮廓 + IoU/剖面 | 渲染 Blender；比较 Gateway | `findContours`、`matchShapes`、`HuMoments` | 3D 求解仍归 Blender |
| `cache.py` | 图片缓存（320 MiB / 8 项） | Gateway | 无 OpenCV | 与新的 render-pass cache / vision cache 分开计 |

## B2. OpenCV 5.0 模块归属（2026-11-10 核对）

> 依据：本机 `opencv-python-headless==5.0.0.93-cp37-abi3-win_amd64.whl` 内 `cv2.__version__ == 5.0.0` 与 `cv2.getBuildInformation()`，以及 OpenCV 5.0.0 头文件。**Python API 仍是扁平 `cv2.*`，不存在 `cv2.geometry` 子模块**；下面的模块名只用于源码/许可/构建裁剪讨论，不写进插件代码。

5.0 主库构建模块（wheel 实际输出）：

```text
calib core dnn features flann geometry highgui imgcodecs imgproc
objdetect photo ptcloud python3 stereo stitching video videoio
```

- 没有 `calib3d` / `features2d`：5.0 把原 calib3d 拆成 `geometry`（几何/位姿/多视图）与 `calib`（标定）；原 features2d 改名为 `features`。
- `contrib=False`、`GUI: NONE`；`opencv-python-headless` 是整体主模块 wheel，不能只装/裁某几个模块。
- `ml` 已移出主库；SURF/BRIEF/FREAK 等也移 contrib。主库仍保留 ORB/SIFT/FAST/GoodFeaturesToTrack/MSER。
- OpenCV 5 的 DNN 移除了 Darknet/Caffe parser，ONNX parser 仍保留；YOLOv5 ONNX 可加载，但仍需真实前向 selftest/E2E，不能只凭 `readNetFromONNX` 存在就判定通过。

本 change 会用到的函数按 5.0 模块归档：

| 5.0 模块 | 本 change 相关函数 | 备注 |
|---|---|---|
| `core` | `mean`、`meanStdDev`、`absdiff`、`norm`、`countNonZero`、`minMaxLoc`、`split`、`merge`、`copyMakeBorder`、`bitwise_*`、`inRange`、`normalize`、`convertScaleAbs` | 像素统计/掩膜基础 |
| `imgproc` | `cvtColor`、`resize`、`threshold`（含 Otsu）、`morphologyEx`、`dilate`、`erode`、`findContours`、`connectedComponentsWithStats`、`distanceTransform`、`HoughLinesP`、`HoughCircles`、`warpAffine`、`warpPerspective`、`matchTemplate`、`calcHist`、`phaseCorrelate`、`Laplacian`、`Sobel`、`Canny`、绘图函数 | 原 4.x imgproc 基本不变 |
| `imgcodecs` | `imread`、`imwrite` | 仅 Gateway 侧文件 IO；Blender 图像读写仍走 bpy |
| `geometry` | `findHomography`、`estimateAffinePartial2D`、`getPerspectiveTransform`、`minAreaRect`、`minEnclosingCircle`、`convexHull`、`fitEllipse`、`boundingRect`、`contourArea`、`arcLength`、`approxPolyDP`、`solvePnP`、`triangulatePoints` | 5.0 新模块，原 calib3d 的几何部分 |
| `calib` | `calibrateCamera`、`stereoCalibrate` | 5.0 新模块，原 calib3d 的标定部分 |
| `features` | `ORB_create`、`BFMatcher`、`goodFeaturesToTrack`、`drawMatches` | 原 features2d |
| `video` | `findTransformECC`、`calcOpticalFlowFarneback`、`calcOpticalFlowPyrLK` | 在 `video/tracking.hpp` |
| `dnn` | `readNetFromONNX`、`blobFromImage`、`NMSBoxes` | 检测器推理 |
| `flann` | 仅在用 `FlannBasedMatcher` 时需要 | 本项目默认 BFMatcher，不单独依赖 |
| 不使用 | `highgui`、`videoio`、`objdetect`、`photo`、`stitching`、`stereo`、`ptcloud` | 不引入 GUI/视频 IO/点云路径 |

对应关系提醒：

- 表 B 里的 `calibrateCamera`、`solvePnP`、`findHomography` 在 5.0 分别属于 `calib`、`geometry`、`geometry`；不要在代码注释里写 `calib3d`。
- 表 B 里的 `ORB_create`、`BFMatcher`、`goodFeaturesToTrack` 属于 `features`；不要写 `features2d`。
- 表 B 里的 `findTransformECC`、`calcOpticalFlowFarneback`、`calcOpticalFlowPyrLK` 属于 `video`，不是 `videoio`。
- 迁移指南：https://github.com/opencv/opencv/wiki/OpenCV-4-to-5-migration

## C. 原生 ONNX 供应情况（检测器）

| 模型 | 官方 ONNX | 来源 | 许可证 | 备注/建议 |
|---|---|---|---|---|
| YOLO26n/s/m/l/x | 否 | Ultralytics 只发 `.pt`；ONNX 需自己 export | AGPL-3.0 | 不作为内置 ONNX 发布；支持“用户自带 ONNX”或开发机导出 |
| YOLO26 社区转换 | 是（社区） | HF `zwh20081/yolo26-onnx`（19 个：det/cls/pose/seg） | AGPL-3.0 | 无官方校验和；仅作可选导入，必须 sha256 + 与 .pt 对齐测试 |
| YOLOv5 v7.0 | 是（官方 release asset） | github.com/ultralytics/yolov5/releases/tag/v7.0 | GPL-3.0 | 有 n/s/m/l/x 及 n6/s6/m6/l6 的 `.onnx`；n 约 3.8 MB |
| YOLOv5 v6.0 | 是（官方 release asset） | .../releases/tag/v6.0 | GPL-3.0 | n/s/m/l/x；n 约 7.2 MB；旧但稳定 |
| YOLOX-s | 是（OpenCV 官方组织） | OpenCV Zoo / HF `opencv/object_detection_yolox` | Apache-2.0 | COCO AP 0.405；仅作参考/可选，不默认（默认已选 YOLOv5 v7.0） |
| NanoDet-Plus-1.5x | 是（OpenCV 官方组织） | OpenCV Zoo / HF `opencv/object_detection_nanodet` | Apache-2.0 | COCO AP 0.304；小目标弱，低分辨率快 |
| YOLOv3 | 是（ONNX 官方模型库） | github.com/onnx/models/.../yolov3 | MIT | 237 MB，mAP 0.553；老、重，但许可证干净 |
| Tiny YOLOv3 | 是（ONNX 官方模型库） | github.com/onnx/models/.../tiny-yolov3 | MIT | 34 MB，mAP 0.331；轻量备选 |
| YOLOv8/9/10/11 | 否 | Ultralytics 只发 `.pt`；需 export | AGPL-3.0 | 同 YOLO26 路径，不自带 ONNX |
| YOLOv6 | 否（官方 release 以 .pt 为主） | 美团 YOLOv6 | 需按仓库确认 | 不作为首选；如用需自行 export + 许可复核 |
| YOLOX（官方仓库） | 否（release assets 为空） | Megvii YOLOX | Apache-2.0 | 官方仓库不直接发 ONNX；用 OpenCV Zoo 的转换版更实际 |

## D. 已选型（2026-11-09 用户确认）

1. **默认安装**：YOLOv5 **v7.0** 官方 ONNX。默认 `yolov5n.onnx`（约 3.8 MB，最快）；配置可切到 s/m/l/x。
2. **来源**：`github.com/ultralytics/yolov5/releases/tag/v7.0`，固定 URL + sha256，不塞进 npm 包。
3. **许可**：GPL-3.0；安装时展示许可与来源，`THIRD_PARTY_NOTICES` 记录。
4. **其余模型**：YOLOX/NanoDet/YOLOv3/YOLO26 不做内置、不做模型市场，也不承诺通用 ONNX 导入。
5. **YOLO26**：不在本次交付范围；用户自行导出/使用不属于插件支持面。
6. **不推荐**：把 Ultralytics `.pt` 运行时塞进插件（AGPL + 额外 Python 依赖），也不推荐 OpenCV.js/WASM 跑检测（慢、内存大、模块少）。

## E. 许可提醒

- 模型权重许可与代码许可可能不同；上表按发布页/README 标注，落地前仍需在 `THIRD_PARTY_NOTICES` 里复核。
- 默认 YOLOv5 v7.0 是 GPL-3.0：只从官方 release 下载，固定 URL + sha256，不随 npm 包分发；安装页展示许可、来源与“可替换/可删除”说明。
- 其他模型不内置；用户自带文件的许可责任归用户，插件只记录来源与 hash。
- OpenCV 本身 Apache-2.0；`opencv-python-headless` 的 wheel 许可随 OpenCV，需在安装页展示。
