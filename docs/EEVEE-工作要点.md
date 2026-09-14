# EEVEE 工作要点（v0.8.x 默认引擎）与材质管线踩坑

> 为什么有这份文档：v0.8.0 起插件默认渲染引擎 = EEVEE + 光追（纯 GPU、走图形后端，不依赖设备偏好 → 无头不会静默回落 CPU）。
> 本文合并两处实测：① 本插件在 360 对象场景的引擎对比；② 社区插件 blender_Stylized-Shading-Tools（作者 路人甲）使用者
> 在室内楼梯间照片还原任务中的 EEVEE 实测记录（仓库 lurenjia-l/dsh-blender-stylized-shading 的 material_pipeline.md，技术要点引用）。

## 1. 引擎事实与性能预期（本机实测）

| 项 | 值 |
|---|---|
| EEVEE 的 GPU 依赖 | 纯 GPU，走图形后端（本机 OPENGL + NVIDIA RTX 4060 Laptop GPU/PCIe/SSE2，GL 4.6 / 驱动 610.74）；没有 CPU 回退 |
| 与 Cycles 的区别 | Cycles 用 compute_device_type 设备体系（可多卡/可 CPU+GPU，靠偏好）；EEVEE 不用这套 → 无头不受偏好影响，不会静默降级 |
| 预热帧（360 对象 / 512x512 / 64 采样） | EEVEE+光追 1.35 s vs Cycles GPU 3.13 s（2.3 倍） |
| 首帧着色器编译 | 冷缓存约 16 s；缓存热后约 2.4 s → 迭代务必用热会话（blender_rt_worker：编译只付一次） |
| 光追开关 | scene.eevee.use_raytracing（默认 False）；ray_tracing_method 本机枚举含 SCREEN |
| 无头可用性 | blender -b 里 EEVEE 渲染会自行初始化 GPU 上下文（实测出图正常）；但 gpu.platform 读取需先 gpu.init()（离屏绘图才需要） |

## 2. EEVEE 的硬限制与对策（社区实测，直接用）

1. Shader to RGB 只含直接光照 → 环境光/世界光不进材质 → 暗部纯黑。
   - 对策 A（想要 HDR 背景但不想给模型打光）：双 Background + Mix Shader + Is Camera Ray（相机可见 HDR，模型不受其照明）；
   - 对策 B（确实需要环境补光）：提高世界背景强度，或加无阴影 Area 环境灯 —— EEVEE 用瓦特计，要 300~500 W 才明显可见。
2. 光追只影响间接光/阴影质量，不改变「只有直接光」这件事：开光追后暗部仍可能黑，别把「开光追」当成「有全球照明」。
3. 首帧编译成本随材质复杂度上升：复杂 NPR 节点组比 Principled 更贵 → 迭代请复用同一进程。

## 3. NPR 材质管线分工（社区实测，能避坑）

| 节点组 | 对什么反应 | 干什么 | 用法 |
|---|---|---|---|
| 主节点 | 灯光（Shader to RGB 提取亮度） | 二分/平滑明暗、亮/暗部染色、亮暗明度 | 材质的明暗骨架（管线第一级） |
| 光源拓展 BSDF | Lightgroup ID（灯光组） | 把指定灯光组照亮的颜色提取出来按 Blending 混合 | 多光源分层染色 |

```
主节点(明暗+亮暗染色) → [光源拓展组(多光源染色,按需)] → 脏迹/AO/高光/反射(质感) → 渐变(可选) → 输出
```

- 没有配置灯光组时，光源拓展组必须设 Lightgroup ID = -1（否则它只响应 0 号组 = 完全不响应）。
- 警告：不要把「漫射拓展光源」串在主节点之后当提亮器 —— 其内部 Diffuse 用默认 0.8 灰算灯光色，Multiply 后会显著压暗（实测 214 → 44），且提高该组 Color 也救不回来。串联后过暗先查这一条。
- 亮部/暗部染色才是上色主通道（不要依赖漫射组 Color）；EEVEE 下主节点输出常偏亮，亮/暗部明度实测常用 0.4~0.75 区间。

## 4. 渐变组三个坑 + 诊断套路（社区实测）

1. 控制器必须绑定：组内 Texture Coordinate.002.object 若为 None → 渐变值恒 0（完全看不出）。add_gradient_group 会自动建 {物体名}_控制器 并关联；手动建组要写 tc.object = bpy.data.objects[...]。
2. Menu[1] 决定坐标来源：走「物体坐标」还是「空物体坐标」；选错 + 控制器未关联 = 恒 0；薄板物体沿薄轴无跨度也看不出。
3. 组实例的 Rotation/Location/Scale 可能因接口映射错位不生效 → 诊断内部 Mapping.001 的实际值与 Menu Switch.001 的 select；不一致就直接改内部 Mapping 或重建。
4. 诊断套路（很重要）：看不到效果时，先把相机挪到能看清渐变方向的位置渲染验证，确认有效后再回用户机位调参 —— 不要因为「当前机位看不出」就误判渐变失效。
5. 方向/缓急：渐变默认沿 X；要沿世界 Y 就给控制器旋转到局部 X 对齐世界 Y（如绕 Z 转 -90 度），再拉长 ctrl.scale.x 控制过渡缓急。

## 5. 与本插件工具链的配合

| 场景 | 用什么 |
|---|---|
| 迭代看材质变化 | blender_rt_do（持久内核；改参数后 see:true 立刻回帧）或 blender_rt_worker（热会话，EEVEE 编译只付一次） |
| 批量出图/多机位对比 | blender_rt_headless（默认已注入 EEVEE+光追前导；engine="cycles" 可切回） |
| 判「改得像不像」 | blender_rt_plan(op="qc_compare")：IoU/Dice/缺多面积/边界距离 + 对照图；回执带 engine_at_qc（同引擎内才可比） |
| 试参数怕改坏 | blender_rt_txn（mark → 改 → revert；大改前 snapshot） |
| 跑长脚本 | blender_rt_do(file="//scripts/xxx.py")（支持 //rel、/mnt/...、D:/...) |

> 引自 lurenjia-l/dsh-blender-stylized-shading 的 material_pipeline.md（技术要点引用）；本插件侧数字均为本机实测。