---
name: blender-modeling
description: Blender 建模与交付教程（v2.6，GUI 优先，无头备用）——先按决策树选形（分面硬表面 / 光滑硬表面 / 有机 / 重复阵列 / 布尔），再走流水线（冻结规格 → 场景准备 → 白模 → 粗形精修 → 修改器栈 → 清理 → 装配/验收 → 交付）。覆盖参考图量测（img_digest/probe/measure/calib）、无头多视轮廓拟合（shapesolve）、装配扫描/清单/合并/修复/门/导出（asm_*）、白模与 Seedance 参考包（blockout_*，含 4 视图与转台 MP4）、同机位 QC（visualreport_report）、缺陷注入评估（eval_run）、交互式无头会话（worker see/watch，rt_do/rt_see session）与强制门（gate_/deliver_export require_gate）。用于做 3D 物体、按参考图还原、量比例、装配体检、修穿模/浮空、批量白模、交付导出与验收。
license: MIT（融合自 RobLe3/cc-blender-skill 与 arjun988/blender-skills，见 NOTICE.md）
---

# Blender 建模流程

> **v2.6 · 2026-10-28**：配套插件 @dsh-external/dsh-blender-plugin v2.6.2，15 个模型工具，plan 通道 34 个 family / 228 个 op。**默认 GUI 优先**：先开 GUI Blender + Connect；无头是备用路径（GUI 不可用/批处理/长活），但无头也能 rt_see，同样能“改一步、同步看一步”。
> 旧版按 v2/v3/v7 记录的“哪段是哪次实测加的”不再写在正文；需要追溯看 CHANGELOG。下面只写**当前有效口径**。

先定形状，再走流程，改一步看一眼。**不要一口气写完整个模型再回来看**——每一步都要有画面或数字证据。

## 0. 本机工具与通道语义（先读，这决定你怎么写代码）

### 0.1 当前工具面（15 个模型工具）

| 工具 | 什么时候用 | 关键点 |
|---|---|---|
| blender_rt_see | 看一眼画面 | GUI 可用时走视口；**GUI 不在/传 session 时自动走持久 worker 的 Workbench 离屏**，50–150 ms；给 from/look_at 走自定义视角，不动用户视口 |
| blender_rt_do | 改一步看一眼 | 持久内核 K；传 session 时在同一个无头 worker 里 exec+see；GUI 不可达自动降级；不是异步 job |
| blender_rt_watch | 判断“动没动/对不对” | GUI 时间窗采样；传 session 走无头帧序列（逐帧 hash + 最多 6 帧图），支持 frames/帧范围/动画默认范围 |
| blender_rt_loop | 参数搜索内环 | GUI timers 或 target=session 的同步阻塞内环；收敛后必须换通路（渲染/量测/装配门）复核 |
| blender_rt_worker | 热无头会话 | start/preload/warmup/exec/see/watch/reset/status/stop/restart；preload 后可直接调 K.dsh_*_api；空闲 TTL 默认 15 min |
| blender_rt_headless | 批处理/重活 | 独立 blender -b；preload/outdir/args/attach_images/shots；长活走 as_job；不占 GUI |
| blender_rt_job | 超长作业 | start/status/wait/collect/kill；超时不等于失败，按 id 回收 |
| blender_rt_txn | 回滚 | 对象级 mark/revert 不含拓扑；拓扑改动前用文件级 snapshot |
| blender_rt_plan | 契约层 + 判定/验收/导出/造型 | op=catalog 先查 34 个 family / 228 个 op；传 session="auto" 时按 family 自动起无头 worker 同步执行，不依赖 GUI |
| blender_rt_perf | 渲染性能 | analyze/apply/revert；persistent_data 等 |
| blender_rt_opt | 对象精简 | analyze/join 安全合并；几何零损失 |
| blender_rt_preset | 配方库 | save/list/get/apply/delete/export/import |
| blender_rt_cmd / blender_rt_commands | addon 命令透传/查能力 | 需要 GUI addon 的命令才走这里；不确定先 commands |
| blender_viewport | 通道运维 | status/doctor/start/stop/restart/launch/lease/release；写 op 过租约 |

**硬规则**：
1. **先查目录，不要猜 op**：blender_rt_plan(op="catalog")；单族 args={family:"audit"}；算子细节 <family>_help。写错 op 会回 did_you_mean，参数名错会回“不认识的参数”。
2. **写操作要过写租约**（blender_viewport op=lease/who/release）；只读 op 豁免；多会话并行时必须先声明写作用域。
3. **回执先读 verdict，不读 ok**：gate/audit/asm/visualreport/eval 都是 pass|fail|degraded 三态；degraded ≠ 通过；ok 只表示“跑完了”。
4. **数字必须可复算**：尺寸、px_per_mm、bbox、md5、IoU 要带来源；没有标定就不许把像素当毫米。
5. **headless 里优先 bpy.data/bmesh，慎用 bpy.ops**：依赖 screen/context 的 GUI 型 ops 在无头会话可能失败；必须用时先小步验证。

### 0.2 GUI 优先（默认）：两种交互式回路

- **GUI 回路**（人在 Blender 前）：rt_do / rt_see / rt_watch 走 addon，~50–100 ms/步。
- **无头回路**（没有人看画面）：rt_worker start + preload，然后 rt_do(session="s1", see=true) / rt_see(session="s1")。每次 exec 后同步 see，图片直接回模型；场景与 K 跨调用保留。**这是同步交互，不是“提交作业—等回合”。**
- **plan 无头**：blender_rt_plan(op="asm_scan", args={...}, session="auto")；首次冷启动约 2 s，之后复用 worker。
- **默认先开 GUI**；只有没有人看画面/GUI 不可用时才用无头回路。无头也必须能“改一步、立刻拿到图/数字”，不要退化成长时间无反馈的批处理。

### 0.3 单位与尺度（先定，后建模）

- 场景单位默认 1 BU = 1 m = 1000 mm（unit_settings.scale_length=1）；写代码前先读 scene.unit_settings。
- 所有尺寸用毫米写进 spec，再统一换算；不要一会儿米一会儿毫米。
- 装配 manifest / blockout / asm_scan 都用 mm 口径；导出前确认 units 字段。
- 参考图没有标尺时只能给像素比例；px_per_mm 必须由已知边长、标尺两端点或 img_calib 标定得到。

### 0.4 证据链与回执

每次关键步骤至少留一种证据：
- 画面：rt_see / rt_watch / blockout 3 视图 / refsheet 4 视图；
- 数字：audit_mesh / asm_scan / gate_run / visualreport_report / eval_run；
- 文件：manifest/md5/GLB/FBX/OBJ/MP4。

回执里优先看：verdict、top_defects、failures、counts、gate、coverage、warnings、honest_limits。不要把 warnings 当通过。

## 0.5 多 Agent 并行建模的分工范式（★ 做大件必读）

并行建模的收益来自“同时做几件独立的事”，不是“让几个模型自由发挥”。必须先把合同冻结，再分头写。

### 0.5.1 第一步永远是冻结规格（spec.py）

所有人只读同一份 spec.py / manifest.json，不许各自定义常量。spec 至少冻结：
- 对象清单、每个件的尺寸/位置/朝向/材质槽；
- 接口表：谁和谁相接、接口平面、公称间隙、允许交叠；
- 单位、坐标系、前向轴（默认 -Y）、上轴（+Z）；
- 验收门：audit_mesh 阈值、asm_gate 要跑哪些门、允许的 hidden_ok 交叠量。

### 0.5.2 写作用域协议（谁写哪个文件）

每个 builder 只写自己的 .py 和对象前缀；并行前用 blender_viewport lease 声明 collection/prefix 作用域；装配前用 K.route_collections 归位。不要让两个 agent 同时写同一个 .blend 或同一批对象。

### 0.5.3 并行期无法互测 = 固有代价

并行期间各件只能做“单件自证”（audit_mesh + 尺寸/bbox + 单件渲染）；接口对不上是正常的，靠装配后复检消化：
1) asm_manifest 校验清单（id/文件/md5/接口帧/allow 列表）；
2) asm_scan 全对全分类（clash / hidden_ok / contact / gap / float / contained / unresolved）；
3) asm_repair(policy="safe") 只做低风险平移/吸附；布尔/缩放等高风险动作交人工确认；
4) asm_gate 用 manifest 连接图判浮空/接口偏差；export_allowed 只在 pass 时为 true；
5) asm_export 导出 GLB/FBX/OBJ + manifest + merge_ledger。

### 0.5.4 并行反模式（实测踩过）

- 各 agent 自造常量、自写验收判据 → 合并时件对不上，且没人能复现；
- 用“看起来接上了”代替数字 → 穿模/悬空被眼睛放过；
- 在未判连接关系时直接 Boolean/Weld → destructive_guard 会拦；先注册连接/接口；
- 把 hidden_ok（设计内隐藏交叠）当穿模修 → 先读 manifest 的 allow 列表。

## 0.6 参考图驱动的形体还原（“像不像”的关键）

### 0.6.1 先量，再建：img_digest → img_probe/measure → img_calib

1) **img_digest**：给参考图路径，拿原生分辨率 bbox、轮廓、直线、圆孔、对称轴、角点、可读性、原生裁剪与标注图。无标定时 verdict=needs_scale，所有 mm 字段为 null，绝不猜。
2) **img_probe**：要某高度宽度用 scan_y/scan_x；要圆孔用 circle；要局部放大用 crop。
3) **img_measure**：已知边长/两点距离 → 求 px_per_mm；再量宽高/孔径/角度。
4) **img_calib**：多视图标定，求每视 px_per_mm 与跨视图尺度；单应只校正取点那个平面，跨深度误差按 Δz/(D+Δz) 估算。
5) 标定后把所有尺寸写进 spec；没有标定的图只能做比例参考，不能写“宽 2000 mm”。

### 0.6.2 轮廓反推：shapesolve

参数化形体（盒体/支架/车壳基础体）可按参考图轮廓反推尺寸：
- 输入：setup 代码（定义 apply(p)）、参数区间、views（ref/from/look_at/ortho_scale）；
- 原理：正交解析投影 + numpy 三角形光栅化，不渲染、不依赖 GUI/timers；
- 输出：best/逐视 IoU/敏感性（strong/weak/unidentifiable）/可复现脚本；
- 边界：只比轮廓并集；ortho_scale 是绝对对齐，写错会整体判错；参考图必须同相机参数生成。
- 拟合结果必须用**另一条通路**复核（blockout 渲染 + visualreport 或直接量尺寸），不能拿训练目标当验收。

### 0.6.3 白模与参考包：blockout

- blockout_base：声明式 parts（prim/size_mm/at_mm/rot）→ 低模 + 3 视图 + GLB；默认 Workbench、单白材质、mm/锚点。
- blockout_batch：批量 N 件，适合给视频模型做基础白模。
- blockout_refsheet：前/侧/顶正交 + iso 透视 4 视图 + 2x2 拼图 + refsheet.json（units/bbox/forward_axis/camera/fps）+ 可选内嵌转台；给 Seedance 这类视频模型做参考包。
- blockout_turntable：绕 Z 轴 orbit，Blender 内置 FFMPEG 出 MP4/H264；MP4 无 alpha；没有 FFMPEG 构建时回退 PNG 序列并标 degraded。
- 白模不是高模：细节仍走 shape/vehicle/human/sculpt 等现有通路；白模只负责形体比例与镜头参考。

### 0.6.4 同机位验收：visualreport_report

- 输入：ref + render（或 views）、可选 px_per_mm、targets、ref_box_px/render_box_px；
- 输出：verdict（pass/fail/degraded）、IoU/Dice/missing/extra/boundary、top_defects（bbox/面积/位置 mm）、缺陷 crop、fix_hint；
- 前提：同机位、同分辨率、同裁切；不同机位/引擎的分数不可直接比；
- 照片参考需先抠背景（掩膜是前景启发式）；px_per_mm 缺失时 mm 字段为 null。
- **IoU 只是单视轮廓的必要条件，不是“整体像不像”**；必须配合尺寸/接口/装配门。

### 0.6.5 读图纪律（决定 token 与效果）

- 先低分辨率看整体（420–560），有疑点再局部放大；不要每步都 1600 px。
- 同一画面 hash 相同 = 没变，不要重复看。
- 看图前先想“我要验证哪个数字/哪个缺陷”，否则看了等于没看。
- 发现缺陷先定位（bbox/center/方向），再改；改完同机位复看 + 数字门复跑。

## 0.7 建模类型 → 工具指路（★ 动手前先查这张表）

| 我要做什么 | 首选 | 备注 |
|---|---|---|
| 不知道该用什么 | blender_rt_plan(op="catalog") | 34 个 family / 228 个 op；单族 family=... |
| 基本体/分面硬表面 | bmesh / geo primitives + bevel | 分面件不要 SubSurf |
| 光滑硬表面 | Bevel + Subdivision + shade smooth | Bevel 必须在 SubSurf 前 |
| 布尔开孔/切割 | geo.boolean / K.dsh_geo_api | 先判连接/闭合；保留 cutter 副本 |
| 重复阵列 | Array / 参数化 generator_* | 节距/弦长写进 spec |
| 曲线/管路/线缆 | sweep_build / shape_* | 先算弯折半径 |
| 有机/雕刻 | sculpt_*（numpy 位移笔刷） | 不用 bpy.ops.sculpt.brush_stroke |
| 人形/头型/车壳 | human_* / vehicle_* / shape_* | 有参考图先 shape_plan 拿截面 |
| 参考图量比例 | img_digest → probe/measure → calib | 无标定不写 mm |
| 轮廓反推尺寸 | shapesolve_solve | 拟合后换通路复核 |
| 白模/视频参考包 | blockout_base/batch/refsheet/turntable | 4 视图 + MP4 |
| 多件装配 | asm_manifest → scan → repair → gate → export | 先冻结 manifest |
| 穿模/悬空 | asm_scan / asm_repair / asm_gate | hidden_ok 不是穿模 |
| 同机位像不像 | visualreport_report / qc_render_views | 同机位才可比 |
| 网格毛病 | audit_mesh → fix_repair → audit_mesh | 没修动会回 ok=false |
| 打印检查 | print_report | 低于分辨率会警告 |
| 交付 | asm_export / deliver_export + gate_deliver | 门不过不交付 |
| 参数搜索 | blender_rt_loop（target=session 可无头） | 收敛后换通路复核 |
| 判断动画动没动 | rt_watch(session=...) | 逐帧 hash + 图 |
| 项目级验收 | gate_run / eval_run | verdict 三态 |
| 参数化曲面（放样/扫掠/补片） | surface_loft/sweep/patch → tessellate → validate → export | 纯 bpy+numpy；细节见 references/曲面算法.md；M3 fit、M5 blend/offset 未实现 |

### 0.7.1 曲面算法（surface_*）—— 有曲率、要可复现的形体

适合：截面放样（瓶身/罩壳/机身段）、沿路径扫掠（管/梁/裙边）、四边补片（过渡面）。
不适合：基本体/分面硬表面（用 bmesh/geo+Bevel）、有机雕刻（sculpt_*）、需要 G2/CAD 偏置（需 OCCT）。

链路四步，全程 mm、可复现：

    surface_loft(sections=[{z_mm, points_mm:[[x,y]...]}], degree, continuity, closed, tessellation)
      → surface_tessellate(spec_or_object=<spec>)        # 拿网格对象名
      → surface_validate(spec_or_object=<object>)        # 读 verdict：pass / fail / degraded
      → surface_export(spec_or_object=<object>, out_dir=..., name=...)   # 出 GLB

硬规则：
- fail 不静默成 pass；degraded 是「没测成」，不等于通过；
- 开放曲面（patch）厚度 n/a，不是 fail；
- 先量参考图（img_*）再写 sections，不要凭感觉写 mm；
- 曲率/厚度是离散近似；交付前仍要过 gate_*（三态）；
- M3 fit、M5 blend/offset 未实现（明确 ok:false），别假装能用。

细节与限制：references/曲面算法.md。

## 0.8 交付与验收（先读这一节，再写最后一步）

交付前按顺序：
1) audit_mesh（单件网格）→ 2) asm_scan（多件穿模/悬空）→ 3) asm_gate / gate_run（项目门，三态）→ 4) visualreport（同机位像不像）→ 5) asm_export（GLB/FBX/OBJ + manifest + merge_ledger）→ 6) 需要视频参考包再加 blockout_refsheet/turntable。
- 门不过（verdict≠pass）不交付；degraded 要补证据，不许当通过。
- 交付物必须有 manifest（units/bbox/objects/md5）+ 回执 + 限制说明；没有 md5 的“交付”不可复现。
- 非合并的 asm_export 若 gate 非 pass：文件会写出但 verdict=fail + warnings，不可直接当出厂件；合并导出门不过不写盘。
- 拓扑改动前先 txn.snapshot / gate_checkpoint；对象级 mark/revert 不含拓扑。

## 1. 架构优先：这个插件就是建模流程本身（强制）

> 这一节不是建议，是**返工判据**。AI 建模必须跟着插件架构走：先查能力目录，再用 plan 通道的 op 做，每一步拿回执，最后用同一条链验收。不要绕开插件自写一套几何/验收。

### 1.1 唯一正确的调用顺序

任何建模任务，从第一分钟到交付，默认按这个顺序：

1. **看现状**：blender_rt_cmd get_scene_info / blender_rt_plan(op="audit_scene")；确认单位、对象、已有资产。
2. **查目录**：blender_rt_plan(op="catalog")；不确定就单族展开 args={family:"..."}；算子细节 <family>_help。
3. **定流程**：按 §2 状态机选阶段；把阶段、输入、验收门写进任务卡。
4. **选通道**：GUI 在就 rt_do/rt_see；GUI 不在就 rt_worker start + preload，然后 rt_do/rt_see(session=...)；plan op 传 session="auto"。
5. **执行一个可验证的小步**：优先 plan op；没有对应 op 才写 bpy/bmesh；每步尽量带回图片或数字。
6. **读回执**：先读 verdict，再读 top_defects / failures / warnings / next；不要只读 ok。
7. **过门**：audit_mesh → asm_scan → asm_gate/gate_run → visualreport；不过门不进入下一步。
8. **交付**：asm_export/deliver_export + manifest/md5 + gate_deliver；产物与回执一起留档。

**允许的例外只有一种**：catalog 里确实没有对应能力，且你已经用 catalog/help 证明过。此时才写 bpy/bmesh，并且仍必须回到插件验收门（audit/asm/gate/visualreport）证明结果。

### 1.2 三条铁律

**铁律 A：op-first。**
- 有 plan op 就不许手写等价几何/检查。比如装配干涉先 asm_scan，不要自己写 BVH 采样；参考图量尺寸先 img_digest/probe/measure，不要自己读像素猜。
- 有 blockout/shape/vehicle/human/sculpt 等生成器就不许从零 bpy 拼形状；生成器负责基线，细节再补。
- 写 op 前先查 <family>_help 的参数表；参数名写错会回“不认识的参数”，照提示改，不要猜第二遍。

**铁律 B：evidence-first。**
- 每一步至少一种证据：图（rt_see/rt_watch/blockout views/refsheet）、数字（audit/asm/gate/visualreport/eval）、文件（manifest/md5/GLB/FBX/OBJ/MP4）。
- “看起来对”不是证据；没有数字的“完成了”一律视为未完成。
- 数字必须带来源：px_per_mm 来自哪次标定、bbox 来自哪个 op、md5 来自哪个文件。

**铁律 C：verdict-first。**
- audit/gate/asm/visualreport/eval 都是 pass | fail | degraded 三态；degraded 是“没查完/证据不足”，不是通过。
- 只看 ok 会误判：ok=true 只表示程序跑完；结论在 verdict/state 里。
- gate 里 skipped 也会把顶层压成 degraded；跳过不是通过，必须补参数或显式记录为未检查。

### 1.3 回执怎么读（弱模型最容易错的地方）

优先级固定：
1. **verdict / state**：pass/fail/degraded；
2. **top_defects / offenders / failures**：最该改的 1–5 项；
3. **next / hint / fix_hint**：下一步命令或修法；
4. **counts / coverage / warnings**：查没查全、有没有降级；
5. **detail / 全量 JSON**：需要复算时再下钻，不要一上来读几万字。

plan 通道返回 receipt.v2 信封（contract/op/verdict/summary/top_defects/next/images/provenance）。大结果会被截断，完整结果用 out_json 落盘或看 results/；不要因为文本截断就认为 op 没结果。

### 1.4 GUI 优先；无头是备用交互回路

- **默认先开 GUI**；只有没有人看画面/GUI 不可用时，才用持久 worker 做“改一步、同步看一步”。
- 固定 session：worker start 一次，后面所有 rt_do/rt_see/rt_watch 都带同一个 session；不要每步换名字导致反复冷启动。
- preload 一次把要用的模块注入 K：imgtools/assembly/blockout/visualreport/shapesolve/gate/txn/deliver 等。preload 后可直接 K.dsh_*_api。
- 每步 see 50–100 ms 是正常；几秒说明走错通道或冷启动。
- 长活（>1 min/长渲染/批量）走 rt_job/rt_headless as_job；交互回路只做小步验证。

### 1.5 spec / manifest 是架构的合同

- 建模前先有 spec（尺寸、单位、接口、验收门）；并行前先冻结，所有人只读。
- 多件装配必须有 manifest（id/文件/md5/接口帧/allow 列表）；asm_scan/asm_gate/asm_export 都以它为准。
- 装配前不知道接口关系就写 manifest；不要用“看起来接上了”代替接口表。
- manifest 里的 hidden_ok/allow 是设计内隐藏交叠；不是穿模的免罪符，扩大 allow 必须有理由。

### 1.6 什么时候可以写 bpy/bmesh

- catalog 无对应 op（先用 catalog + help 证明）；
- 需要 op 没暴露的局部编辑（例如某个面的微调），且对象/选择状态明确；
- 生成器/plan op 已经给出基线，只做局部精修。

写 bpy 的约束：
- headless 里优先 bpy.data/bmesh，慎用依赖 screen/context 的 bpy.ops；
- 每段代码要可重跑、少依赖选择状态；对象按名字取，不要靠“当前选中”；
- 写完立刻回 audit_mesh 或对应门，不许跳过；
- 拓扑改动前 snapshot/checkpoint；不要在没有回退点的情况下 Boolean/合并。

## 2. 标准建模状态机（每一步都有输入/输出/门）

### 2.1 阶段总览

| 阶段 | 目标 | 主要工具 | 出门条件 |
|---|---|---|---|
| S0 规格/草稿 | 定范围、单位、接口、验收门 | 任务卡 + spec.py / manifest.json | 字段完整、无 mm 级臆造 |
| S1 参考量测 | 把图变成 px_per_mm + 尺寸 | img_digest → probe/measure/calib | 标定可复算；无标定只做比例 |
| S2 白模/基线 | 快速出形体比例 | blockout_base/batch、shape_*、vehicle_*、human_* | 3 视图可看、bbox 合理 |
| S3 形体精修 | 放样/截面/特征线/雕刻 | shape_loft/sections/crease、sculpt_* | 尺寸/比例达标；关键特征可指认 |
| S4 硬表面细节 | 倒角/布尔/阵列/扫掠 | geo、fix、uv、sweep、material | 无退化面；法线正确；材质槽稳定 |
| S5 单件体检 | 网格/法线/UV/打印 | audit_mesh、uv_stats、print_report | state=pass（degraded 要补查） |
| S6 装配 | 清单/扫描/修复/门 | asm_manifest → asm_scan → asm_repair → asm_gate | gate verdict=pass；clash/contained/float 已解释 |
| S7 视觉验收 | 同机位像不像 | visualreport_report / qc_render_views | verdict=pass 或明确 unresolved |
| S8 交付 | 导出/参考包/账本 | asm_export / deliver_export / blockout_refsheet/turntable | manifest+md5+gate 回执齐全 |

不要跳阶段。S2 没白模就做 S4 细节，返工率最高；S5 没体检就进 S6，装配门会把问题放大。

### 2.2 任务卡（开工先填，不超过 10 行）

    目标：一句话（做什么、给谁用）
    单位/坐标：mm 或 m；前向轴 -Y；上轴 +Z
    交付：GLB / FBX / OBJ / blend / MP4 / 图
    参考：图路径 + 是否已标定 px_per_mm
    主要件：对象/集合清单
    接口：谁和谁相接、公称间隙、允许交叠
    验收门：audit_mesh / asm_gate / gate_run / visualreport / eval
    禁用：不要做的事（如不要布尔、不要改材质）
    预算：时间/面数/分辨率

没有任务卡就开始建模，等于没有合同；后面所有“像不像/合不合格”都无法判定。

### 2.3 每阶段的证据产物

- S1：img_digest 回执 + 标定表（px_per_mm、两点/已知边长、误差）；
- S2：白模 3 视图 + bbox + GLB；
- S3：截面/站表/特征线参数 + 尺寸表；
- S4：修改器栈说明 + 布尔/阵列参数 + audit_mesh；
- S5：audit_mesh 回执（state/各计数/法线逐壳）；
- S6：manifest + asm_scan + asm_repair 计划 + asm_gate；
- S7：visualreport_report 回执 + overlay/sheet；
- S8：manifest.json + merge_ledger.jsonl + 逐文件 md5 + gate 回执。

证据不齐的“完成”只是半成品；不要交给下游。

## 3. 工具选择矩阵（先查这张表，再写代码）

| 任务 | 第一选择（插件架构） | 第二选择（仅当无 op） | 验收 |
|---|---|---|---|
| 基本体/白模 | blockout_base/batch | bmesh.ops.create_* | bbox + 3 视图 + audit_mesh |
| 参数化截面/放样 | shape_plan/shape_sections/shape_loft | bmesh 放样 | shape_fit IoU + 尺寸 |
| 车辆外壳 | vehicle_package → vehicle_base → clear_check | shape_* | clear_check + visualreport |
| 人形/头型 | human_* / face_* | sculpt + 参考图 | 比例/特征线/visualreport |
| 有机/雕刻 | sculpt_scan/setup/apply | bmesh 位移 | audit_mesh + 图 |
| 硬表面分面 | 基本体 + bevel/crease_lines | bmesh inset/extrude | 面感图 + audit_mesh |
| 光滑硬表面 | Bevel + Subdivision（修改器） | bmesh 细分 | 面感图 + 法线 |
| 布尔开孔/切割 | geo.boolean / K.dsh_geo_api | bpy.ops boolean | audit_mesh + 干涉 |
| 阵列/重复件 | generator_* / Array | bmesh 复制 | 计数/节距/bbox |
| 管路/线缆/轨道 | sweep_analyze → sweep_build | 曲线+bevel | 弯折半径 + audit_mesh |
| 参考图量尺寸 | img_digest → probe/measure/calib | numpy 读像素 | 标定误差 + 复算 |
| 轮廓反推 | shapesolve_solve | 手工量点 | 换通路复核 |
| 网格修复 | audit_mesh → fix_repair | bmesh 清理 | 修复前后计数 |
| UV/材质 | uv_* / material_* | bpy.ops uv | uv_stats + 材质检查 |
| 装配 | asm_manifest → scan → repair → gate | 手工对齐 | asm_gate verdict |
| 视觉 QC | visualreport_report / qc_compare | 人眼看图 | 同机位 IoU + 缺陷 |
| 交付 | asm_export / deliver_export + gate_deliver | 手工导出 | manifest + md5 |
| 参数搜索 | rt_loop（target=session 可无头） | 手写网格搜索 | 换通路复核 |
| 动画/修改器验证 | rt_watch(session) | 逐帧 see | hash 变化 + 图 |
| 缺陷评估 | eval_run | 自写用例 | capture_rate/baseline |

**判据**：能用第一选择就不用第二选择；第二选择必须在回执里写明“为什么不用插件 op”。

## 9. 当前 plan 通道速查（v2.6 · 34 个 family / 228 个 op）

先 blender_rt_plan(op="catalog") 看“什么时候用 / 别用 / 最小骨架”，再单族展开。常用家族：

| family | 用途 | 代表 op |
|---|---|---|
| audit | 网格/场景体检 | audit_scene / audit_mesh / audit_interference / audit_connectivity / audit_gate |
| asm | 装配清单/扫描/合并/修复/导出/门 | asm_manifest / asm_scan / asm_merge / asm_repair / asm_export / asm_gate |
| blockout | 声明式白模/批量/参考包 | blockout_base / blockout_batch / blockout_export / blockout_refsheet / blockout_turntable |
| img | 参考图量具 | img_digest / img_probe / img_measure / img_calib / img_rectify / img_crop / img_diff |
| visualreport | 同机位 QC 报告 | visualreport_report |
| shapesolve | 无头多视轮廓拟合 | shapesolve_solve |
| eval | 缺陷注入/基线评估 | eval_run |
| qc / qc_render | 渲染与图比较 | qc_render_views / qc_compare |
| gate | 项目门/流程 | gate_plan / gate_run / gate_preflight / gate_checkpoint / gate_deliver |
| deliver | 交付导出/核对 | deliver_export / deliver_verify |
| shape | 截面/放样/折线族 | shape_plan / shape_draft / shape_loft / shape_sections |
| vehicle / human / face | 车辆/人形/头型 | vehicle_package / vehicle_base / human_* / face_* |
| sculpt / fix / uv / print / sweep | 雕刻/修复/UV/打印/扫掠 | *_apply / fix_repair / uv_smart_project / print_report / sweep_build |
| material / motion / generator | 材质/机构/参数化 | material_build / motion_* / generator_save|run |
| surface | 参数化曲面候选/质检/导出 | surface_loft / surface_sweep / surface_patch / surface_tessellate / surface_validate / surface_export |
| clear / calib | 间隙/门标定 | clear_check / calib_run |
| pipe / plannner | 多步链/规划器 | pipe_run / planner_* |

规则：
- op 名拼错会回 did_you_mean；参数名写错会回“不认识的参数”，照提示改。
- 只读 op 不走写租约；写 op 先 lease。
- 大结果会落 results/ 目录；回执文本截断时用 out_json/结果文件。
- plan 传 session="auto" 时按 family 自动起无头 worker；首次冷启动约 2 s，之后复用。

## 10. P0–P2 新链路配方（可直接照抄）

### Recipe 10.1 交互式无头建模（没有人看画面）

    # 1) 起一个持久无头会话（preload 决定能直接调哪些 K API）
    blender_rt_worker(op="start", name="s1",
      preload="imgtools,assembly,blockout,visualreport,shapesolve,gate,txn,deliver",
      engine="workbench", warmup=true)
    # 2) 改一步，同步看一步（图片直接回模型；不是异步 job）
    blender_rt_do(code="...bpy/bmesh 代码...", session="s1", see=true, max_size=560)
    # 3) 换角度复核
    blender_rt_see(session="s1", from="7,-7,5", look_at="0,0,1")
    # 4) 动画/修改器有没有动
    blender_rt_watch(session="s1", frames=[1,10,20], max_size=420)
    # 5) 不想要 GUI 也能跑 plan op
    blender_rt_plan(op="img_digest", args={path:"..."}, session="auto")

判据：start 一次约 2–3 s；之后每步 exec 0–2 ms、see 50–100 ms（320–560 px）。如果 see 要几秒，说明走错通道/每步在冷启动。

### Recipe 10.2 参考图 → 尺寸 → 白模 → 验收

    # 1) 先量：无标定会给 needs_scale，不猜 mm
    blender_rt_plan(op="img_digest", args={path:"ref.png"}, session="auto")
    # 2) 已知边长/两点 → px_per_mm
    blender_rt_plan(op="img_measure", args={path:"ref.png", known_len:{...}}, session="auto")
    # 3) 白模（mm 口径；先冻结 spec）
    blender_rt_plan(op="blockout_base", args={
      name:"robot",
      parts:[{id:"torso", prim:"box", size_mm:[300,180,420], at_mm:[0,0,900]}],
      render:{views:"auto3", res:[512,512]},
      export:{glb:"D:/out/robot.glb"}
    }, session="auto")
    # 4) 同机位像不像（同相机/同分辨率）
    blender_rt_plan(op="visualreport_report", args={
      ref:"ref.png", render:"render.png", px_per_mm:0.16
    }, session="auto")

注意：IoU 只是单视轮廓的必要条件；还要用尺寸/接口/装配门交叉验证。

### Recipe 10.3 装配：清单 → 扫描 → 安全修复 → 门 → 导出

    # 1) 清单（id/文件/md5/接口/allow）
    blender_rt_plan(op="asm_manifest", args={action:"build", parts:[...]}, session="auto")
    # 2) 全对全分类（clash/hidden_ok/contact/gap/float/contained/unresolved）
    blender_rt_plan(op="asm_scan", args={manifest:"assembly.json"}, session="auto")
    # 3) 低风险修复：safe 只做 translate/snap，硬禁布尔/缩放；先 dry_run
    blender_rt_plan(op="asm_repair", args={manifest:"assembly.json", policy:"safe", dry_run:true}, session="auto")
    # 4) 门（verdict 三态；export_allowed 只在 pass）
    blender_rt_plan(op="asm_gate", args={manifest:"assembly.json"}, session="auto")
    # 5) 交付导出（GLB/FBX/OBJ + manifest + merge_ledger）
    blender_rt_plan(op="asm_export", args={
      manifest:"assembly.json", merge:true, dry_run:false,
      out_dir:"D:/out", name:"assembly",
      formats:["glb","fbx","obj"], ledger:true
    }, session="auto")

边界：hidden_ok 是 manifest 里声明允许的隐藏交叠，不是穿模；不要为了让 clash=0 随意扩大 allow。非合并导出 gate 非 pass 时文件会写出但 verdict=fail + warnings，不可当出厂件。

### Recipe 10.4 Seedance 参考包（4 视图 + 转台 MP4）

    blender_rt_plan(op="blockout_refsheet", args={
      objects:["robot_torso","robot_arm"],
      out_dir:"D:/Blender/robot/ref",
      name:"robot", views:["front","side","top","iso"], res:[768,768],
      turntable:{frames:120, fps:24, elev_deg:15}
    }, session="auto")
    # 只要转台 MP4
    blender_rt_plan(op="blockout_turntable", args={
      objects:["robot_torso"], out_dir:"D:/Blender/robot/ref",
      name:"robot", frames:120, fps:24, res:[512,512]
    }, session="auto")

产物：4 PNG + 2x2 拼图 + refsheet.json（units/bbox/forward_axis/fps）+ 可选 MP4。MP4 无 alpha；无 FFMPEG 构建回退 PNG 序列并标 degraded。白模是参考包，不是高模。

### Recipe 10.5 轮廓反推（shapesolve）与复核

    blender_rt_plan(op="shapesolve_solve", args={
      setup:"def apply(p): ...",
      params:[{name:"width",min:0.5,max:2.0,init:1.0}],
      views:[{ref:"front.png", from:[0,-5,1], look_at:[0,0,1], ortho_scale:3.0}],
      iterations:200, budget_ms:10000
    }, session="auto")

复核：best 参数写回几何 → blockout/渲染 → visualreport 或 img_measure 量尺寸；不要拿拟合目标函数当验收。

### Recipe 10.6 强制门交付

    # 先体检/留回退点
    blender_rt_plan(op="gate_preflight", session="auto")
    blender_rt_plan(op="gate_checkpoint", args={label:"before_delivery"}, session="auto")
    # 先过门再导出（不过门 blocked，不写盘）
    blender_rt_plan(op="gate_deliver", args={
      dir:"D:/out", spec_path:"gate.json", name:"model", fmt:"obj", verify:true
    }, session="auto")

deliver_export 也支持 require_gate=true；gate_deliver 是弱模型/交付流程的一键强制路径。degraded 要显式 allow_degraded 并说明，不能静默变绿。

### Recipe 10.7 缺陷注入评估（改门之后先跑它）

    blender_rt_plan(op="eval_run", args={
      suite:"assembly", out_json:"D:/out/eval.json"
    }, session="auto")

读数：capture_rate / missed / false_positive / baseline_pass；assembly/reference 绿、core 缺依赖时 degraded 是正常。capture_rate 只代表合成缺陷集，不等于真实项目通过率。

### 10.8 多 Agent 并行：按插件架构分工

多 Agent 不是“几个模型同时自由建模”，而是“共享同一份合同，各自只写自己的作用域”。默认分工：

1. **Lead / 架构 owner**：冻结 spec.py / manifest.json；跑 catalog 选路线；分配写作用域；最后跑 asm_gate/gate_run。
2. **Reference builder**：img_digest/probe/measure/calib → 尺寸表 → 更新 spec；不写几何。
3. **Blockout builder**：blockout_base/batch → 白模 + 3 视图；只写自己前缀的对象。
4. **Shape/vehicle/human builder**：按 spec 生成主形体；输出 bbox/截面对照。
5. **Detail builder**：硬表面细节/布尔/阵列/扫掠；拓扑改动前 checkpoint。
6. **Assembly owner**：asm_manifest → asm_scan → asm_repair(policy=safe) → asm_gate；只做装配层。
7. **Verifier**：独立跑 audit/asm/visualreport/eval，不采信 builder 的结论。

写作用域：
- 每个 builder 只写自己的对象前缀与文件；用 blender_viewport lease 声明 collection/prefix；
- 装配前用 K.route_collections 按前缀归位（COL_Lights/COL_Camera/COL_VFX/COL_Geo 等）；
- 任何人不得改别人的对象；接口改动必须回 spec/manifest，不允许“口头约定”。

装配协议：
- 接口表先写：a/b、interface、nominal_mm、tol_mm、allow_overlap_mm；
- hidden_ok/allow 必须有理由（设计内隐藏交叠），不是为了让 scan 变绿；
- 冲突处理顺序：asm_repair(policy=safe) 平移/吸附 → 人工确认布尔/缩放 → 重跑 scan/gate；
- 门不过不合并、不导出；合并导出必须 gate=pass。

并行期验收：
- 单件：audit_mesh + bbox/尺寸 + 单件渲染；
- 装配：asm_scan + asm_gate；
- 视觉：visualreport（同机位）；
- 交付：asm_export + manifest/ledger。

### 10.9 反模式清单（出现就返工）

| 反模式 | 为什么错 | 正确做法 |
|---|---|---|
| 不查 catalog 就写 bpy | 重复实现插件已有能力，参数/验收口径全错 | 先 catalog；有 op 用 op |
| 自写“像不像”判据 | 和插件 IoU/门口径不一致，无法复现 | visualreport/qc_compare + 尺寸门 |
| 只看 ok 不看 verdict | ok 只表示跑完；degraded 被当通过 | 先读 verdict/state |
| 把 skipped 当通过 | 门没跑过，等于没检查 | 补 args/overrides 或记未检查 |
| 为让 scan=0 乱扩 allow | 真穿模被隐藏，交付后炸 | allow 必须有设计理由 |
| GUI 不在就停工 | headless 本来就能交互 | worker session + see |
| 每步换 session / 每步冷启动 | 慢且状态丢 | 固定 session + preload |
| 长活同步硬等 | 客户端超时误判失败 | as_job/rt_job |
| 改拓扑前不 snapshot | 无法回滚 | txn.snapshot / gate_checkpoint |
| 用“看起来接上了”代替 manifest | 装配无法复检 | asm_manifest + 接口表 |
| 交付不带 manifest/md5 | 不可复现 | asm_export/deliver_export |
| 把白模当高模交付 | 参考包不是成品 | blockout 只做基线/参考 |
| 照片参考直接算 IoU | 背景掩膜不可信 | 先抠背景/给裁切框 |
| 无标定写 mm | 数字是编的 | img_measure/img_calib 或标 needs_scale |
| 让 VLM 看全图代替数字门 | 看不到 mm 级缺陷 | 数字门 + 局部 crop |

### 10.10 新会话开场模板（直接复制）

    你在 DSH × Blender 插件环境里建模。必须按插件架构走：
    1) 先 blender_rt_plan(op="catalog") 查能力，不要自造几何/验收；
    2) 有 plan op 就用 op（传 session="auto" 可无头）；没有才写 bpy/bmesh，并说明理由；
    3) 每步给我图或数字证据；回执先读 verdict，degraded ≠ 通过；
    4) 参考图先 img_digest/probe/measure/calib；无标定不许写 mm；
    5) 多件先 asm_manifest，再 asm_scan/asm_repair/asm_gate；门不过不交付；
    6) 交付用 asm_export/deliver_export，带 manifest/md5/ledger；
    7) 长活走 rt_job/headless as_job；交互小步用 rt_worker + rt_do/rt_see(session)。
    任务：<目标/单位/交付/参考图/预算/禁用项>

### 10.11 从零到交付的四条最短路径

**A. 单个硬表面道具**
catalog → blockout_base 或基本体 + Bevel/crease_lines → audit_mesh → 材质/UV（按需）→ qc_render_views → asm_export（单件也可，manifest 仍建议）。

**B. 参考图还原**
img_digest → img_measure/calib → spec 尺寸表 → blockout_base/shape_loft/vehicle_base → visualreport（同机位）→ audit_mesh → asm_export。
如果参数化特征强：shapesolve_solve 反推参数 → 写回几何 → 换通路复核。

**C. 多件装配**
冻结 manifest → 各件 builder → 单件 audit_mesh → asm_scan → asm_repair(policy=safe, dry_run 先看) → asm_gate → asm_export(merge=true) → visualreport/refsheet。

**D. Seedance 基础白模包**
blockout_batch 出 N 件 → blockout_refsheet（4 视图 + 拼图 + refsheet.json）→ blockout_turntable（MP4）→ 检查 units/bbox/forward_axis → 交付到 D:/Blender/<项目>/。

### 10.12 门/评估的当前口径（别记旧版）

- gate_run / asm_gate / visualreport / eval 都是三态：pass / fail / degraded；ok 只在 pass 时为 true。
- gate 里 skipped 会把顶层压成 degraded；degraded 要补证据，不能静默变绿。
- eval_run 的 capture_rate 只代表合成缺陷集；改门后跑一次，记录 baseline/missed。
- 同机位 visualreport 的 IoU 是轮廓必要条件，不是整体像不像；跨机位/引擎分数不可比。
- asm_export 非合并导出 gate 非 pass 时文件会写出但 verdict=fail + warnings，不可当出厂件。

## 11. 常见故障与边界（当前版本）

| 症状 | 原因 | 处理 |
|---|---|---|
| GUI 不在，rt_see/rt_do 报连接失败 | 没走无头降级 | 传 session="s1" 或 auto；先 worker start |
| worker see 很慢/每次都冷启动 | 每步换了 session 名，或 worker 被 TTL 回收 | 固定 session；preload 一次；status 看 idleMs |
| plan op 执行失败提示无头映射 | 该 family 未接无头路由/模块未 preload | 用 blender_rt_headless(preload=...) 直接调 K API，或走 GUI 通道 |
| 无标定却想要 mm | img_digest verdict=needs_scale | 给 known_len/标尺/两点，或 img_calib；不猜 |
| asm_scan 把设计内交叠报 clash | manifest 没声明 allow/hidden_ok | 先 asm_manifest 声明；不要直接改几何 |
| asm_repair 不动/blocked | 有父级/动画/超 max_move，或 policy=safe | 清父级/关键帧、调 max_move；布尔交人工/aggressive |
| asm_gate degraded | 有 skipped/未检查门 | 补 manifest/参数，不要把 degraded 当 pass |
| visualreport 分数异常低 | 机位/分辨率/裁切不同，或照片背景没抠 | 同机位重渲；给 ref_box/render_box；先抠背景 |
| refsheet MP4 失败 | Blender 构建无 FFMPEG | 看 warnings/degraded，用 PNG 序列或换构建 |
| 交付后无法复现 | 没 manifest/md5 | 用 asm_export/deliver_export 的 manifest + ledger |
| 渲染卡住/超时 | 主线程被渲染占住 | render_state → render_wait；长活走 job |

明确不覆盖/未装依赖：landmark/人脸（无 cv2/MediaPipe）、OCR、DXF、Open3D/manifold3d/glTF-Validator 未安装；不要写成已支持。生物/有机高模、电影级布光/合成、角色绑定动画也不在本 skill 的覆盖范围。

## 12. 交付前最终清单（逐条打勾）

1. 单位/前向轴/上轴已确认；所有尺寸来自标定或 spec，不来自猜。
2. 单件 audit_mesh 通过；拓扑改动前有 txn.snapshot 或 gate_checkpoint。
3. 多件 asm_manifest 校验通过；asm_scan 分类已看（clash/contained/float/unresolved 为 0 或已解释）。
4. asm_gate/gate_run verdict=pass；degraded/failed/skipped 已补证据，不是当通过。
5. 同机位 visualreport/QC 已看；IoU 与尺寸/接口/装配门交叉一致。
6. 交付文件有 manifest（units/bbox/objects/md5）+ ledger；回执可复算。
7. 限制/风险已写进回执：无标定、无外部依赖、MP4 无 alpha、非合并 gate 非 pass 等。
8. 变更过的门/工具，已跑 eval_run 或对应 selftest，并记录 capture_rate/基线。

> 记住：**画面是证据，数字是判据，verdict 是结论**。不要用“看起来对”代替任何一条。


## 附录 A. 通用 Blender 深水参考（不是首选路径）

> 以下内容保留通用建模知识/数值门/渲染经验，供“catalog 里确实没有对应 op”时参考。
> **默认路径始终是 §1–§3 的架构规则 + §9–§10 的插件配方**；任何参考做法做完仍必须回到插件门（audit/asm/gate/visualreport）验收，不许自写判据。

### A.4. 六条必踩的坑

### 4.0 六条坑怎么分流（P2-3：能机械判定的交给门，不能的写进 checklist）

| 坑 | 能不能机械判定 | 落在哪 |
|---|---|---|
| 坑五 **法线方向** | ✅ | **验收门**：`audit_mesh` 的 `normals_state`（`pass`/`fail`/`unknown`/`n/a`）+ `normals_shells[]`（逐壳）；⚠ 别只看总 `signed_volume`（大正向壳 + 小反向壳会抵消成正数），`closed=false` 时 `normals_outward=null`（**不是"没问题"**），`unknown` ⇒ 至少 `degraded`（别的缺陷同时在就是 `fail`），**任何情况都不给 pass** |
| 坑二 **浮块 / 悬空** | ✅ | **验收门**：`audit_connectivity` 的 `floaters[].{span_fraction, gap_mm, mesh_confirmed}` + **`gate.state`**（顶层没有 `state`）、`audit_snap_floaters`（默认只报告）、`audit_gate` 的 `state` |
| 坑二 **穿模 / 隐藏交叠** | ✅ | **验收门**：`audit_interference`（三态 + `volume_mm3`；`unresolved` = 判不了）、`audit_overlap`（`pair_count`；**贴合会给 ≥1**）、`audit_measure` 的 `pairs[].axis_overlap_mm`（**逐轴 AABB：仅当接口法线与世界轴一致且轴对齐盒对该几何适用时才作粗筛**；旋转接口沿接口局部坐标量真实表面，见坑二 ③） |
| 坑一 长轴 / 宽轴 / 薄轴语义 | ❌ 语义，机器判不了 | **写代码前 checklist** |
| 坑三 收尖压中线再合并 | ❌ | **写代码前 checklist** |
| 坑四 headless 慎用 bpy.ops | ❌ | **写代码前 checklist** |
| 坑六 预览光照 / 材质 / 机位 | ⚠ 半（参数进 `lib/preview.py`；出图后看 `coverage_estimate` 与 `warning.frame_looks_empty`） | 装置默认值 + 门⑨ |

**写代码前 checklist（不可机械判定的三条，动手前逐条念一遍）**：

1. **长轴 / 宽轴 / 薄轴**：这个件哪个轴是长？哪个轴是"看得出形状"的宽面？相机在哪一侧？—— 先在注释里写下 `(长, 宽, 薄) = (局部?, 局部?, 局部?)` 再动手（坑一）。
2. **收尖**：要收尖的那一圈顶点，是不是**先压到中线（x=y=0）再 `remove_doubles`**？只按比例缩放一定得到平头（坑三）。
3. **headless**：这段脚本里有没有 `object.mode_set` / `mesh.*` / `loopcut` / `transform.*` / `uv.*`？有 ⇒ 改 bmesh 或 data API（坑四表 + 配方 3b/8）。

**怎么验**：checklist 三条各自能在代码里指出对应的一行（轴注释 / 压中线那两行 / 没有 ops 调用）；
机械判定的三条则必须出现在交付回执里（§0.5.5）。

### 坑一：细长物体的"长轴 / 宽轴 / 薄轴"语义

任何细长或不对称的东西（剑刃、刀、瓶、木板、骨头、螺丝刀头），三个轴的**含义不同**：

- **长轴** = 长度（剑刃 78cm）
- **宽轴** = 宽面（剑刃 4.5cm，是"能看出是把剑"的那个面）
- **薄轴** = 截面厚度（剑刃 0.8cm，也就是刃口方向）

**永远让宽轴朝向英雄镜头的相机。** 从薄轴看过去的剑，渲染出来就是一根细棍。

| 物体 | 局部 X | 局部 Y | 局部 Z |
|---|---|---|---|
| 剑刃 | 薄 0.8cm | 宽 4.5cm | 长 78cm（竖直） |
| 刀刃 | 薄 | 宽 | 长（水平） |
| 木板 | 薄 | 宽 | 长 |
| 瓶子 | 对称（半径） | 对称（半径） | 长（高度） |

建完记得**旋转物体**让宽面朝向相机（相机在 -Y 方向时，把刀刃绕 Z 转 90 度，局部 Y 宽轴就朝向世界 X，宽面可见）。

### 坑二：拼件接缝——可视拼接与真实连接是两回事（★ 全场最高价值的一条）

多部件拼装（剑 = 刃 + 护手 + 柄 + 柄头；坦克 = 车体 + 炮塔 + 悬挂 + 裙板）时，**面贴面**即使数值上接触，渲染也会有可见的缝；不同形状之间（圆柱柄插进方块护手）更明显。
展示模型的做法是**隐藏交叠**：让两件在不可见处互相压进去一点，缝就没了。

> ★ **纠正一条被写死的通则**：老版本把"拼件互相插入 **5–15mm**"当硬规则；v4/v5 把它换成"`1–2×` 接口特征尺寸"，
> 但**那仍是规范性的固定倍数/下限**。**v6 撤回全部这类通则**（见 §13）：本技能不再给任何通用倍数、百分比或下限。

**① 展示层（本技能负责的那部分）：隐藏交叠 + 按 spec 复核**

- 拼接可以在**不改变可见轮廓、不捅穿薄壳**的前提下选择**隐藏交叠**（把交叠埋在较大件内部或不可见面之间）；
- **交叠量由本项目的 spec 明文规定**（`spec.py` 接口表逐条写自己的值）；**没有通则**，spec 没写就回去问规格，别自己发明一个数；
- 装配后按 spec 复核三件事：交叠量（`audit_measure`）、浮空（`audit_connectivity`）、真互穿（`audit_interference`）。

**② 真实连接不由可视脚本裁定**

一件东西**能不能承载、抗扭、抗弯、装不装得上**，由**设计配合（配合与公差）、适用标准、承载工况、制造工艺（壁厚 / 拔模 / 打印方向）与运动间隙**共同规定 —— 这些都是要人给的设计输入。
**本技能的几何脚本只回答"叠没叠、浮没浮、穿没穿"，不能给结构强度结论**：`volume_mm3` / `pair_count` / `axis_overlap_mm` 都不是强度或连接合格证。

**③ 怎么用 `audit_measure` 判交叠（★ 它有口径坑）**

`pairs[].axis_overlap_mm` 是**逐轴 AABB 重叠**，回执自带 `note: "bbox 级（不是三角级）"`。

- **只有"接口法线与某根世界轴一致、且轴对齐盒这个近似对这种几何适用"时，逐轴 AABB 才能当粗筛**；此时读**接口法线那一根轴**的值 —— 不是取最大、也不是求和；
- **旋转 / 斜置接口不能这么读**：接口法线不与世界轴对齐时，"挑一根世界轴"量到的是**盒子的重叠**，不是接口处的深度。必须**沿真实接口的局部坐标**去**量测真实表面**（在接口局部坐标系里取截面 / 量真实三角面），或改用真三角级证据；
- 读法示例（示例只演示"怎么读 AABB"，**不构成任何深度标准**）：Ø6 轴入毂若接口轴与 z 一致、回执给 `{x:6, y:6, z:6}`，z 的 6 是盒级重叠；3mm 薄板搭接给 `{x:180, y:200, z:1.5}` —— x/y 那 180/200 是**板本身有多大**，跟交叠无关；只看单个数或取最大值必然得出错误结论；
- **AABB 相交 ≠ 真的有交叠**：全内含的 10mm 浮块同样给 `overlap_bbox=true`、三轴各 10mm。所以交叠复核必须**与浮空门并跑**（`audit_connectivity` / `audit_gate`）；
- 要**真三角级**穿透证据用 `audit_interference` 的 `volume_mm3` / `audit_overlap` 的 `pair_count`；
  注意 `audit_overlap` 是"真三角-三角相交"，但**共面且互相覆盖的面不报**（插件回执原文），共面贴合要看 `contact_probe`。

**④ 圆润件开平滑着色**：`bpy.data.polygons[].use_smooth = True`（headless 稳）或 GUI 里 `bpy.ops.object.shade_smooth()`。平面着色的圆柱能看出每一段棱面；方块类硬表面保持平面着色（Blender 5.x 已移除 `Mesh.use_auto_smooth`，用修改器式平滑或逐面标记）。

> 实测（500 对象 / 7 agent）：**装配质量几乎全靠这一条**。悬挂臂穿模、驱动轮盖悬空、挡泥板浮空 193mm —— 都是靠"隐藏交叠 + 装配后按 spec 复检"抓出来并收敛的。
> 并行建模时把它写进**团队协议 + 接口表**（§0.5.3，每条接口写自己的 `overlap_mm` 与它在 spec 里的出处）。

想要**真正无缝**（高质量渲染）时，把同材质的件 Boolean Union 成一个网格——但仅限材质相同。

### 坑三：收尖不能只靠缩放

把顶部顶点缩到接近 0 会得到"凿子一样的平头"。正确做法是**把顶点压到中线再合并**：

```python
import bpy, bmesh
obj = bpy.data.objects['GEO-blade']
bpy.context.view_layer.objects.active = obj
bpy.ops.object.mode_set(mode='EDIT')

bm = bmesh.from_edit_mesh(obj.data)
bm.verts.ensure_lookup_table()
max_z = max(v.co.z for v in bm.verts)
top_verts = [v for v in bm.verts if abs(v.co.z - max_z) < 0.001]
for v in top_verts:
    v.co.x = 0.0
    v.co.y = 0.0
bmesh.update_edit_mesh(obj.data)

bpy.ops.mesh.select_all(action='DESELECT')
for v in top_verts:
    v.select = True
bmesh.update_edit_mesh(obj.data)
bpy.ops.mesh.remove_doubles(threshold=0.001)   # 少了这步就是退化拓扑的"假尖"
bpy.ops.object.mode_set(mode='OBJECT')
print(f"tapered:{obj.name}")
```

### 坑四：headless 下慎用 bpy.ops（★ 能省掉很多"不明原因失败"）

`bpy.ops` 的很多算子是**context 依赖**的：要求"当前是编辑模式 + 活动对象是它 + 有选择集 + 有合适的区域（VIEW_3D）"。
在 `blender -b`（无窗口、无区域）里，这些前置条件经常不成立，于是你拿到的是
`RuntimeError: Operator bpy.ops.mesh.xxx.poll() failed, context is incorrect` 这种**看不出原因**的失败。

| 类别 | headless 下的表现 | 建议 |
|---|---|---|
| `primitive_*_add`（加基本体） | ✅ 稳 | 直接用 |
| `object.modifier_apply` / `modifier_add` | ✅ 基本稳（给 `view_layer.objects.active` 就好） | 直接用，注意 apply 前把对象设为 active |
| `object.mode_set` + `mesh.*`（挤出 / 内插 / 环切 / remove_doubles…） | ⚠ 常见 poll 失败 | **改 bmesh**（配方 3b / 8） |
| `mesh.loopcut_slide`、`transform.*`、`uv.*` | ⚠ 依赖区域 / 模态输入 | 不要用；用 bmesh 或直接改 data |
| `object.shade_smooth` / `object.select_all` | ⚠ 依赖 view_layer | 用 data API（见下） |

替代写法（**推荐**）：

```python
# 平坦/平滑着色：直接改面属性，不碰 ops
for p in obj.data.polygons:
    p.use_smooth = False        # 或 True

# 法线一致化：bmesh 版（等价 normals_make_consistent）
import bmesh
bm = bmesh.new(); bm.from_mesh(obj.data)
bmesh.ops.recalc_face_normals(bm, faces=bm.faces)
bm.to_mesh(obj.data); bm.free(); obj.data.update()
```

必须用 ops 时：用 `bpy.context.temp_override(...)` 补齐 context，或者**改走 GUI 通道**（`blender_rt_do`，那里有真的窗口与区域）。
判据：**同一段脚本在 headless 里报 poll/context 错、在 GUI 里正常 → 就是这个坑**，不要去怀疑几何数据。

### 坑五：法线方向是一切"穿模 / 内含"判据的前提（★ 会给出反向结论）

任何形如 `(p − loc) · n < 0 → "在内部"` 的判据（点在内侧、件插进主体多深、面是否朝外），
**都假设法线朝外**。法线朝内时结论会**整体反号**。

实测：车体侧板外表面法线朝内 → 审计把"贴在外壁上的支架"误报成"深入车体 150mm"。
这类错误最坑的地方是**它看起来像几何错了**，于是你去改几何，越改越乱。

**纪律**：

1. 做任何穿模 / 内含判定**之前**，先验法线：
```python
import bmesh, mathutils
def normal_health(obj, probe_point=None):
    """返回 (是否一致朝外, 有符号体积)。有符号体积 < 0 = 整体法线朝内。"""
    bm = bmesh.new(); bm.from_mesh(obj.data)
    bmesh.ops.triangulate(bm, faces=bm.faces)
    vol = 0.0
    for f in bm.faces:
        a, b, c = [v.co for v in f.verts]
        vol += a.dot(b.cross(c)) / 6.0
    bm.free()
    return (vol > 0), vol
```
   （闭合网格的有符号体积 < 0 ⇒ 整体朝内 ⇒ 先 recalc 再判。）
   ⚠ **这只是"单体单壳"的快检**：一个对象里可以有多块互不相连的壳，`bm.calc_volume(signed=True)` 把它们**相加**——
   大正向壳（+8）+ 分离的小反向壳（−1）总和仍是 +7 > 0 ⇒ 反向的那块被吞掉。多壳件交给 `audit_mesh`
   （当前版本 起**逐连通壳**判：`normals_state` ∈ `pass`/`fail`/`unknown`/`n/a`，明细在 `normals_shells[]`，含逐壳 `state`/`signed_volume`/`nested`）。
2. 装配层**统一兜底**一次 recalc（Recipe 8 的那一行），别指望每个 builder 都记得。
3. 判据写在报告里时要带**法线前提**："在法线朝外的前提下，X 深入 Y 12mm"。
4. 双向确认：同一对件用 `audit_interference` 与 `audit_measure` 各跑一遍，结论相反就先查法线。
5. **`unknown` 不许当 pass**（当前版本 的逐壳判据）：

   | `normals_state` | 含义 | 处置 |
   |---|---|---|
   | `pass` | 每个壳都"可确证是独立实体"且朝外；合法嵌套空腔标 `nested` 豁免（负体积不误报） | 记入回执 |
   | `fail` | 至少一个**非嵌套**壳闭合、绕向一致、`signed_volume < 0` | 先 recalc 再复检 |
   | `unknown` | 判不出来：壳的 AABB 相交但无法确证嵌套/空腔（互穿、部分重叠、重合副本）、闭合/绕向/体积不可判、或（显式设了上限时）面数超过逐壳分析上限 | **不给 pass**：无其它缺陷时 `state=degraded`，同时有缺陷（如互穿被抓成自交）时 `state=fail` ⇒ **补查**（`normals_reason` 给原因；大网格可用 env `DSH_SHELL_MAX_FACES` 放宽），**不要**下"没问题"结论 |
   | `n/a` | 网格未闭合（`closed=false`），"整体朝向"无意义 | 先闭合，或对有意的开口件只按口部法线单独核对 |

### 坑六：给 agent 用的预览装置，默认必须看得清几何突变

实测（三个组员都抱怨过）：白材质 + 白背景的预览图**"白对白"**，板缝、焊缝、分块全看不见，
panel 级根本没法 QC —— 最后是组员自己改成"**灰材质 + 单侧强太阳**"才看清。

**默认就该这么配**（写进你的 `lib/preview.py`，而不是每次临时调）：

- 材质：中性灰（`Base Color ≈ 0.18`、`Roughness ≈ 0.6`、`Metallic = 0`），**不要纯白**；
- 光照：**单侧强太阳**（斜 45°，`angle ≈ 1–3°` 的硬阴影）+ 很弱的环境光 —— 硬阴影能把 1–2mm 的台阶照出来；
- 背景：中灰或渐变，别用纯白 / 纯黑（前者吃掉高光，后者吃掉暗部）；
- 零件级 QC：用 `mode="parts_color"` 逐部件配色 + 图例，比"整体一个灰"更容易发现"少了一块 / 装反了"；
- **每次至少两个机位**：一个"英雄机位"（像不像）+ 一个"斜 45° 检查位"（缝、浮空、穿模）。

判据：**如果一张图看不出板缝在哪，这张图就不能作为 QC 证据**。

### A.5. 修改器栈序与症状表

> ★ **纠正一条被写死的"固定链"**：老版本让人"背下来"这一串 ——
> `Mirror → Array → Solidify → Bevel → Subdivision Surface →（需要时 Boolean）`。
> **不存在这样一条通用顺序。** 修改器栈是一个**按目标函数排序的求值链**：同一个基础网格，
> 只换两三条的顺序，**拓扑和尺寸都会变**。实测（Blender 5.2.2，同一个 1m 立方体）：

| 顺序 | verts / faces | bbox 最长边 |
|---|---|---|
| Bevel(0.2/2 段) → SubSurf(2) | **866 / 864** | **0.9904 m** |
| SubSurf(2) → Bevel(0.2/2 段) | **98 / 96** | **0.8395 m** |
| Solidify(0.03) → Bevel(0.05/2 段)（平面） | **56 / 54** | — |
| Bevel(0.05/2 段) → Solidify(0.03)（平面） | **8 / 6** | — |

两边的结果**都对**，取决于你要的是"圆角被细分保留"还是"细分后的低模再被倒圆"；
`bbox` 差 **15%** 说明这不只是面数问题。⇒ 顺序靠**目标**决定，不靠背。

**怎么定顺序**：从目标反推，逐条问"这一步要吃上一步的什么"。

| 目标 | 顺序（相对） | 为什么（设计意图） |
|---|---|---|
| 镜像件要浑然一体 | **Mirror 在前**，Bevel / SubSurf 在后 | 让镜像缝先焊成连续面，再统一倒角/细分；反过来缝处会被当成自由边处理 |
| 重复阵列的每个单元各自带倒角 | **Bevel 在前**，Array 在后 | 后级只复制前级的结果；反了则倒角作用在"整排"的接缝上 |
| 薄壳（钣金/壳体）要边缘圆润 | **Solidify 在前**，Bevel 在后 | 先有厚度才有"实心边"可倒；实测反着放倒角基本被吃掉（54 面 → 6 面） |
| 光滑曲面但要保棱 | **Bevel 在前**，SubSurf 在后 | 倒角提供支撑边，细分才不会把棱圆掉（配方 2b） |
| 分面硬表面（装甲板/军模） | **不要 SubSurf** | 细分会把棱边全部圆成"肥皂"→ 走配方 2a（小倒角 + 平坦着色） |
| 布尔开孔 | Boolean **尽量靠后**、导出前再 apply | 保持非破坏；布尔后的 n-gon 还会影响后续细分 |
| 拓扑定型后再镜像 | **apply Mirror 在改拓扑之前** | 拓扑一变，镜像缝的对齐就不保证 |

**顺序对不对，用回执判**（不是靠背）：`evaluated_get(depsgraph).to_mesh()` 取求值后的
`verts / faces / bbox`，与你要的目标对拍；再加 §6 的数值门。**改了顺序就重跑一遍门。**

补充规则：布尔保持 live 到导出前；拓扑定型再 apply 镜像；**不要叠多层 subsurf 而没有支撑边**；应用缩放（Apply Scale）之后再布尔 / 导出。

| 症状 | 修法 |
|---|---|
| 一看就是默认方块 | 加 Bevel（0.02m / 3 段）+ SubSurf（**仅限光滑件**） |
| **装甲棱边被圆掉、像塑料玩具** | 这是分面件误用了 SubSurf → 删掉 SubSurf，改走配方 2a（小倒角 + 平坦着色） |
| 圆形上出现夹点 | Bevel 要在 SubSurf **之前** |
| 渲染出黑面 | 重算法线（坑五）；先查有符号体积判断是不是整体朝内 |
| 布尔产生 n-gon | apply 布尔后进编辑模式改回四边面，再 SubSurf |
| 对称破掉 | 用 Mirror 修改器，不要复制再翻转 |
| **网格里有看不见的内部面 / 埋在实体里的面** | ★ **不是 `Delete Loose`**（它只删"不属于任何面的点/边"，一个面都不删）→ 见下方专条 |
| 细分后发皱 / 塌陷 | 缺支撑边：加环切固定结构，别继续加细分层级 |
| 阵列首尾对不上（履带 / 链节） | 本配方口径的 `FIT_CURVE` 阵列给不了精确闭环节距 → 配方 6b 参数化**销轴弦长**路径 |
| headless 里 ops 报 context/poll 错 | 坑四：改 bmesh / data API，或用 temp_override |

### 5.1 「删除内部面」的正确做法（★ 老版本这里教错了）

老版本的症状表写"网格里有看不见的内部面 → Clean Up → **Delete Loose**"。**这是错的**：
`Delete Loose` 只删除**不属于任何面的顶点与边**，**一个面都不会删**。

**实测（Blender 5.2.2）**，两种内部面场景，Blender 自己打印的是一行 `Info`：

| 场景 | 内部面 | 跑 `delete_loose` 后 |
|---|---|---|
| 立方体里塞一张独立浮动四边形 | 1 张 | `Info: Removed: 482 vertices, 992 edges, **0 faces**` → 那张面还在 |
| 两个重叠立方体 join（每个壳各有一面埋进对方） | 2 张 | `Info: Removed: 0 vertices, 0 edges, **0 faces**` → 面数仍是 12 |

顺带否掉第二个常见误解：`bpy.ops.mesh.select_interior_faces()` **也不解决**这个问题 ——
上面那个 join 出来的重叠对，它选中 **0** 张面（它对"每条边都有 >2 个面用户"那类才有效）。

**正确做法（按成因分）**：

```python
import bpy, bmesh
from mathutils.bvhtree import BVHTree

def buried_faces(obj, eps=1e-5):
    """埋在实体内部的面：从面中心沿**外法线**打射线，命中 = 该面被别的几何挡住了。
    （clean cube -> 0；两个重叠立方体 join -> 恰好 2）"""
    bm = bmesh.new(); bm.from_mesh(obj.data)
    bvh = BVHTree.FromBMesh(bm)
    out = []
    for f in bm.faces:
        c = f.calc_center_median() + f.normal * eps
        loc, nor, idx, dist = bvh.ray_cast(c, f.normal, 1e6)
        if idx is not None:
            out.append(f)
    bm.free()
    return out
```

| 成因 | 正确处置 |
|---|---|
| **join / 复制出来的重叠壳体**（最常见） | **Boolean UNION**（或 `mesh.intersect` 的 Boolean）把两壳并成一个实体；**别用"选中再删"** —— 实测删掉埋面后留下 **8 条开边**（壳被捅开了），还得再补面 |
| Boolean 求解残留的碎片 | 换 `solver='EXACT'` 重跑，或 `fix_repair`（`merge_doubles` + `dissolve_degenerate` + `delete_loose` + `recalc_normals`）—— 注意这里的 `delete_loose` 干的是它**真正**该干的活（清孤立点/边） |
| 建模时手滑多做的内壁 | 用上面的 `buried_faces()` 找出来删，**删完必须补面或改走布尔**，并复检 `audit_mesh` 的 `boundary_edges` |
| 起因不明 | 先 `audit_mesh` 看 `self_intersections` / `nonmanifold_edges` / `boundary_edges` —— **`self_intersections` 才是"互相穿进对方"的机器判据** |

**判据**：删内部面之后 `audit_mesh` 必须仍是 `clean=true`（`boundary_edges=0` / `nonmanifold_edges=0`）。
只盯着"面数变少了"而不管开边，等于把穿模换成了破面。

### A.6. 数值门清单（交付 / 交接前逐条过）★**本节是唯一的权威门表**，其余章节一律引用它

> v1 这一节只有"清理清单"，实测证明**肉眼验收不可靠** —— 这里给每条判据配上可命令与字段。
> 单件交付跑 ①–④，装配完成后跑 ①–⑧。**缺字段 = 没跑**。
>
> ★ **三态口径（当前版本，比"红/绿"多一态）**：`audit_mesh` / `audit_scene` 顶层给 `state`（等价字段 `verdict`）= `pass` / `fail` / `degraded`。
> `clean` 收紧为"**查完了而且没缺陷**"⇒ `degraded` 时 `clean=false`。**`clean=false` 有两种成因**（确有缺陷 / 没查完），
> 必须看 `state` + `reason`；`degraded` 的处置是**补查**（不是放过，也不是当失败）。

| # | 门 | 判据 | 怎么跑（命令 → 看的字段） |
|---|---|---|---|
| ① | 重复点 / 松散几何 | 0 孤立点、0 松散边 | `audit_mesh` → `totals.loose_verts=0` **且** `totals.loose_edges=0`（+ `remove_doubles`） |
| ② | 流形 | 0 非流形边（有意开口除外） | `audit_mesh` / `audit_scene` → `totals.nonmanifold_edges=0`；⚠ `audit_scene` 默认**不跑自交** ⇒ `state=degraded`，要当门用必须传 `self_intersect=true` |
| ③ | 开边 | 0 开边（闭合件口径） | `audit_mesh` → `totals.boundary_edges=0`、`objects[i].closed=true` |
| ④ | 法线 | **逐连通壳**一致朝外（不是"总 `signed_volume > 0`"） | `audit_mesh` → `objects[i].normals_state`（`pass`/`fail`/`unknown`/`n/a`）+ `normals_shells[]`（逐壳 `state`/`signed_volume`/`nested`）；⚠ `closed=false` ⇒ `normals_outward=null`（开放网格没有"整体朝向"这回事）；`normals_state=unknown` ⇒ **不给 pass**（无其它缺陷时 `state=degraded`，有缺陷时 `state=fail`，看 `normals_reason`） |
| ⑤ | 尺寸 | 关键 bbox 与 `spec.py` 偏差 ≤ 公差（军模级 ±3mm） | `audit_measure` → `items[].size_mm` |
| ⑥ | 浮空 | 0 悬空件（可见浮块 = 最长 bbox 边 ≥ 全模型 1%） | `audit_connectivity` → `gate.state=pass` **且** `floaters=[]`（⚠ 顶层**没有** `state`；**别拿"分量数=1"当判据** —— 贴而未焊天然是多分量）/ `audit_snap_floaters`（默认只报告）/ `audit_gate` 的 `state` |
| ⑦ | 穿模 | 无异常互穿；允许的隐藏交叠白名单化 | `audit_interference` → `verdict` + `volume_mm3`（⚠ `unresolved` = **判不了**，不是"无干涉"；贴合接触常给 `unresolved`）+ `audit_overlap` → `pair_count`（⚠ 贴合会 ≥1；`analyzed=false` ≠ 无重叠） |
| ⑧ | 隐藏交叠 | 逐接口交叠量对齐**本 spec 明文**的 `overlap_mm`（坑二：**无通用倍数 / 百分比 / 下限**；不改可见轮廓、不捅穿薄壳） | `audit_measure` → `pairs[].axis_overlap_mm`（**仅接口法线与世界轴一致且轴对齐盒适用时作粗筛**；旋转接口沿接口局部坐标量真实表面）+ `audit_connectivity`（同一对件若同时报浮空，"交叠"不成立） |
| ⑨ | 形体 | 固定机位对照图 + 数值 bbox（两条都要） | `qc_render_views`（固定机位 + `max_size=900`）+ `audit_measure`；**IoU 只判轮廓**，盲区见 §0.6.2 |
| ⑩ | 面数 | 对三角面预算 | `audit_scene` / `audit_gate` 的 `tris`（只看数，不看状态） |
| — | 证据留档 | 源一变即作废 | `fingerprint.digest` + `.blend` 的 md5 |

**MUST**：非破坏到导出阶段；对象一建就命名（GEO- 前缀）；blockout 与成品分离；布尔 / 导出前应用变换；
**多 agent 时先冻结 spec.py 与接口表**；每个件交付带数值证据。
**MUST NOT**：轮廓没认可就删 blockout；过早 apply 所有修改器；留默认名；没有比例参照就开始建模；跳过清理；
**用单张成品图验收形体**；**在法线未验证的前提下下穿模结论**；**拿单视 IoU 当"整体像不像"**。

### 6.1 交接前必跑（P0-4）：三条命令 + 一张回执

照抄命令；**判据与字段一律查 §6 那张表**（本节不重复门表）。

```python
# ① 单件 / 集合的网格健康（boundary / nonmanifold / degenerate / loose / self-intersections / normals）
blender_rt_plan(op="audit_mesh", args={"scope": "COL_Geo"})
# ② 出厂门：连通 + 包络（★ 装配口径；只有单件时请只用 ①）
blender_rt_plan(op="audit_gate", args={"scope": "COL_Geo", "envelope": [x0, y0, z0, x1, y1, z1]})
# ③ 穿模 / 干涉（逐对；设计上允许的隐藏交叠要白名单化）
blender_rt_plan(op="audit_interference", args={"a": "GEO-rungear", "b": "GEO-hull"})
# ④ 全场景初筛（★ 交付验收必须显式开自交检查：默认不跑 ⇒ state=degraded，拿不到 pass）
blender_rt_plan(op="audit_scene", args={"summary_only": True, "top_k": 10, "self_intersect": True})
```

> ★ **`audit_scene` 的自交开关（当前版本 实测）**：它按性能契约**默认 `self_intersect=false`**（便宜初筛），
> 这时无论场景多干净，回执都是 `state=degraded` + `clean=false` + `warn: "DEGRADED ≠ 通过"` +
> `hint: "要完整结论请用 self_intersect=true 重跑"` —— **"跳过检查"不许读成"没问题"**。
> ⇒ **交付/验收口径一律传 `self_intersect=true`**（代价按面数走）；只看"哪几个最脏"的日常初筛可以不开，
> 但那时**不能**把回执当门。

**验收回执模板 + 三条硬纪律见 §0.5.5**（含 `.blend` md5 与 `fingerprint.digest` 的写法）。

**三条口径（实测，别混）**：
1. `audit_mesh` 读**未求值的 `ob.data`** —— 未 apply 的修改器不算数（带 Mirror 的半个件报基础网格的 `verts=4 / boundary_edges=4`）；
   装配级的 `audit_connectivity` / `audit_gate` / `audit_measure` / `audit_interference` 走**求值网格**（Boolean 未 Apply 也算数）。要拿 `audit_mesh` 判"改完没有"，先 apply 掉相关修改器。
2. `ok=false` / `analyzed=false` / `state="degraded"` **都不是通过** —— 那是"没跑"或"没分析完"，不能读成"没问题"。
3. **`clean=false` 有两种成因**：`state=fail`（确有缺陷，看 `totals` 里非 0 的计数字段）或 `state=degraded`（没查完/判不出来：自交被跳过或抛异常、`normals_state=unknown`、逐壳分析超面数上限）。拿到 `clean=false` **先读 `state`**，再决定"改几何"还是"补查"。

### A.6.5 渲染 / 动画 / 长活（★ v7 新增，按《QBZ-191 全程分解视频》实测补）

> §0–§6 管的是**形状对不对**；这一节管**画面对不对、活跑不跑得完**。
> 两者的失败模式完全不同，而且渲染阶段的坑**更隐蔽**：
> 几何错了 `audit_mesh` 会红；**取景错了它只会安静地给你一张切边的图**。

### 6.5.1 取景：不许用包围盒估算，必须逐帧投影自检 ★本节最高价值

**实测事故（QBZ-191 爆炸图，返工 2 轮）**：

| 症状 | 根因 |
|---|---|
| 4 张爆炸图全部切边 | 拿轴对齐 AABB 的 `dx/dy/dz` 估相机距离 —— **3/4 视角的投影范围与这三个数无关** |
| 取景差 **2.2 倍** | 改了 `obj.location` 却没刷依赖图 ⇒ `bounds_all()` 读到**爆炸前**的矩阵（按 949 mm 取景，实际 2129 mm） |
| 迭代发散到 `dist=6.1e15` | 手动解的**居中项符号写反**（该加不是减） |
| 同样发散 | 顶点抽样是**米**、包围盒是**毫米**，两者混算 |

**通则一：相机距离/视点一律按"投影后"的画面范围解，不按 AABB 解。**

```python
def cam_basis(direction):                       # d = 目标->相机；fwd = -d
    d = Vector(direction).normalized(); fwd = -d
    up = Vector((0, 0, 1))
    if abs(fwd.dot(up)) > 0.995: up = Vector((0, 1, 0))
    Z = -fwd
    Y = (up - Z * up.dot(Z)).normalized()
    X = Y.cross(Z).normalized()
    return d, fwd, X, Y, Z

def fit_view(bb, direction, lens=70, res=(1920, 1080), margin=1.18, points=None, iters=40):
    d, fwd, X, Y, Z = cam_basis(direction)
    tan_h = (36.0 / 2) / lens                                     # sensor 36mm
    tan_v = tan_h * (res[1] / float(res[0]))
    pts = points or [Vector((x, y, z)) for x in (bb[0], bb[1])
                     for y in (bb[2], bb[3]) for z in (bb[4], bb[5])]
    look = Vector(((bb[0]+bb[1])/2, (bb[2]+bb[3])/2, (bb[4]+bb[5])/2))
    dist = max(bb[1]-bb[0], bb[3]-bb[2], bb[5]-bb[4]) * 1.8 + 1.0
    target = 1.0 / margin
    for _ in range(iters):
        C = look + d * dist
        k, nx, ny, behind = 0.0, [], [], False
        for P in pts:
            V = P - C; dep = V.dot(fwd)                            # 沿视线正方向的深度
            if dep <= 1e-6: behind = True; break
            ax = V.dot(X) / (dep * tan_h); ay = V.dot(Y) / (dep * tan_v)
            nx.append(ax); ny.append(ay); k = max(k, abs(ax), abs(ay))
        if behind: dist *= 1.8; continue                           # 角点跑到相机后面
        dist *= (k / target) ** 0.85                               # nx,ny ∝ 1/dist
        cxm = (min(nx)+max(nx))/2; cym = (min(ny)+max(ny))/2
        look = look + X*(cxm*dist*tan_h) + Y*(cym*dist*tan_v)      # ★符号：加，不是减
        if k <= target*1.002 and abs(cxm) < 0.004 and abs(cym) < 0.004: break
    return dist, (look.x, look.y, look.z)
```

**通则二：摆好相机之后必须验，验的是"投影"，不是"看着差不多"。**

```python
# 逐帧取景自检：max NDC 必须 <= 1.0（=1.0 就是贴边）
sc.frame_set(f); bpy.context.view_layer.update()        # ★不刷依赖图 = 读到旧矩阵
mw = cam.matrix_world; C = mw.translation
X = mw.col[0].to_3d().normalized(); Y = mw.col[1].to_3d().normalized()
fwd = -mw.col[2].to_3d().normalized()
tan_h = (36.0/2)/cam.data.lens; tan_v = tan_h*(RES[1]/float(RES[0]))
for o in objs:
    for v in [o.data.vertices[i] for i in range(0, len(o.data.vertices), 14)]:   # ★步长=14，与 fit_view 同口径
        V = (o.matrix_world @ v.co) - C; dep = V.dot(fwd)
        assert dep > 1e-4, "角点跑到相机后面"
        worst = max(worst, abs(V.dot(X)/(dep*tan_h)), abs(V.dot(Y)/(dep*tan_v)))
assert worst <= 1.0, "切边：worst NDC=%.3f" % worst
```

> ⚠ **抽样密度必须与 `fit_view` 一致**。我第一次自检只取每件 20 个点，测出 `0.737`，
> 而真实值是 `0.847` —— **自检本身在骗我**。自检比被检对象松，等于没检。
>
> **实测口径**：静帧验收 `worst <= 1.0`；成片留白 `margin 1.12–1.24`（对应 `worst ≈ 0.80–0.89`）。

**通则三：单位先统一。** `kit.bounds_all()` 回 **mm**，`matrix_world @ v.co` 是 **m** —— 混算就得到 `dist=6.1e15`。
任何"顶点 / 包围盒"混用处先 `* 1000`。

### 6.5.2 爆炸图 / 分解动画：位置、幅度、地板、顺序

- **改了 `obj.location` 之后必须 `bpy.context.view_layer.update()`**，否则 `matrix_world` 还是旧的
  （§6.5.1 事故 2 的根因）。对"每帧算包围盒"的动画尤其致命。
- **幅度先定"总跨度"，再按比例分配每件位移**，不要逐件手调。目标 **≈1.4–1.6 倍物体长度**；
  实测 2.2 倍（2129 mm vs 946 mm 枪长）时零件**小如星点**，统一缩放 0.42 → 1445×904 mm 才读得出骨架。
- **地板会吃掉整个画面**：分解件下坠到摄影棚地板以下（实测 Z 到 −1146 mm）。
  分解 / 内构镜头一律 `hide_render = True` 收起地板，用渐变世界当背景。
- **必须有一个"基准件"不动**，给观众参照系（我是上机匣）。
- **波次表里"具体名必须排在泛化名前"**：`bolt_carrier` / `bolt_cam_pin` / `bolt_catch` 都要排在 `bolt` 之前，
  否则 `bolt` 会把它们全吃掉。同理 `buffer_tube` 必须在 `buffer` 前、`trigger_guard` 在 `trigger` 前。
- **收尾必须打印未匹配清单**，`UNMATCHED []` 才算全件编排到了（否则会有件默默不动）。
- **缓动**：机械感用 **ease-out**（`1-(1-u)**2.4`），分 8 段采样打关键帧，比默认 Bezier 更像"脱开"。

### 6.5.3 长活（>2 min）：默认必须能被打断 ★

**实测**：10 分钟渲染期间后端重启 ⇒ `blender_rt_job(op="wait")` 回 `Error: terminated`，
作业变 `stale：pid 已不存在`。**这是常态，不是异常。**

**规范（每条都省过时间）**：
1. 输出按**帧号命名**落到固定 `outdir`（`seq_0001.png`）；
2. 出图前 `if os.path.exists(p) and not FORCE: continue` —— **存在即跳过**；
3. 提供 `--force` 重来。**"可续跑"是长活的第一属性，不是优化项** ——
   实测 301 帧的视频补渲 1 帧用 **6 秒**，而不是重跑 10 分钟；
4. 每 N 帧 print 进度 + **实测 s/帧 + ETA**；ETA 用**已渲帧数**算，不要用循环序号算（续跑时后者会骗你）；
5. **草稿与终稿必须分目录**：否则"存在即跳过"会把 480p 草稿当成 1080p 成品帧；
6. **作业句柄归属要认清**：`as_job=true` 回的是 **Blender 侧台账 id**，用 `blender_rt_job(op=wait/collect)` 收；
   拿它去 DSH 的 `job_output` 会回 `unknown job`（两套注册表）。

### 6.5.4 渲染耗时：先测 3 帧，再排期

**实测模型**（Cycles/OptiX + 降噪，138 件 / 191k 面，全件每帧都在动，RTX 4060 Laptop）：

| 分辨率 | 采样 | 秒/帧 |
|---|---|---|
| 1920×1080 | 64 | 1.78 |
| 1920×1080 | 128 | 2.00 |
| 1920×1080 | 256 | 2.50 |
| 1920×1080 | 320 | 2.45 |
| 3840×2160 | 320 | 9.71 |

**关键结论：一帧里约 60% 是固定开销**（场景求值 + BVH 重建 + 降噪 + PNG 写盘）。
所以 **"降采样省钱"几乎无效**（320 → 64 只省 30%），**真正的杠杆是帧数 = 时长 × 帧率**；
分辨率接近像素数线性（4K ≈ 3.8 × 1080p）。**排期前先花 1 分钟渲 3 帧实测，不要套用别人的数字。**

⚠ **`use_persistent_data` 在 Blender 5.2 挂在 `scene.render`，不在 `scene.cycles`。**
包在 `try/except` 里会**静默失败**（我连续失败 **26 次**才发现）——
**任何 setter 都不许静默失败，必须回读校验**（这是 §6 "静默空产出必须失败" 的同一条纪律）：

```python
sc.render.use_persistent_data = True
assert sc.render.use_persistent_data is True, "没生效：属性可能在别的对象上"
```

### 6.5.5 视频：低帧率渲染 + 光流插值（省 3 倍，但必须先量帧间位移）

**判据不是"我觉得能动"，是"每帧位移多少像素"**：

```
px_per_frame = 单件总行程_mm / (时长_s × 渲染帧率) ÷ (画面宽_mm / 画面宽_px)
< 30 px/帧  ⇒ 光流插值基本无损
> 80 px/帧  ⇒ 开始撕裂 / 重影
```

实测（1080p，画面宽 ≈1450 mm 映射 1920 px）：最猛的件 **17 px/帧** ⇒ 10 fps 渲、30 fps 出，安全。

**必须做真值对照才算验收**：让渲染器把**"插值帧对应的真实时间轴帧"单独渲出来**当 ground truth，逐像素比。

| 对比 | PSNR |
|---|---|
| 源帧直通（验证链路无损） | **51.6 dB** |
| **插值帧 vs 真值** | **40.1 / 40.4 dB** |
| 编码后解码帧 vs 源 PNG | 43.0 dB |

残余误差集中在**高频边缘的亚像素混叠**（导轨齿、防滑纹）；**调大搜索窗口无改善**（40.13 → 40.20 dB）
⇒ **这是低帧率采样的固有代价，不是参数问题，别再去调参**。3× 放大目视不可辨。

```bash
# 单镜头连续运动用 scd=none，别让场景切换检测误判成剪辑点
ffmpeg -start_number 1 -framerate 10 -i seq_%04d.png \
  -vf "minterpolate=fps=30:mi_mode=mci:mc_mode=aobmc:me_mode=bidir:vsbmc=1:scd=none" \
  -fps_mode cfr -frames:v 900 -c:v libx264 -preset slow -crf 15 -pix_fmt yuv420p out.mp4
```
> ⚠ ffmpeg 7：`-vsync vfr` 与 `-r` 冲突（报 `One of -r/-fpsmax was specified together a non-CFR`），改 `-fps_mode cfr`。
> ⚠ 时间轴要多留 3 帧，否则 30 fps 输出差 2 帧凑不满 30.000 s。

### 6.5.6 Blender 5.2 API 变更（实测，会直接报错）

| 症状 | 原因 | 改法 |
|---|---|---|
| `'Action' object has no attribute 'fcurves'` | 5.2 改 layered / slotted action | 不要遍历 `action.fcurves`；要控制缓动就**自己按缓动采样打关键帧** |
| `bpy_prop_collection[slice]: slice steps not supported` | `vertices[::n]` 不支持步长切片 | `[o.data.vertices[i] for i in range(0, n, step)]` |
| `CyclesRenderSettings has no attribute 'use_persistent_data'` | 属性移到 `scene.render` | 见 §6.5.4 |
| `Scene.frame_set()` expected int, not float | 帧号必须整数 | `int(1 + round(t * fps))` |
| GUI 里 `open_mainfile` 后见空网格 | 用的是"用户当前视口" | 传 `from=` / `look_at=` 走自定义视角 |
| `module 'dsh_rt_kernel' has no attribute 'dsh_audit_api'` | 该模块**没被 preload 进内核** | `blender_rt_headless(..., preload="audit")`；或查已加载：`K.dsh_kit.kapi("audit")`（找不到会列出可用模块名） |
| `'Scene' object has no attribute 'node_tree'` | **5.0 起合成器改成节点组**（`scene.node_tree` 被移除） | `ng = bpy.data.node_groups.new("Comp","CompositorNodeTree"); scene.compositing_node_group = ng`；先 `ng.interface.new_socket(name="Image", in_out='OUTPUT', socket_type='NodeSocketColor')`，输出用 **`NodeGroupOutput`**（`CompositorNodeComposite` 已不存在） |
| `AttributeError: type ... is read-only` / `glare_type` 无效 | 5.x 把 Glare 的 **Type/Quality 变成 MENU 输入插座**，参数也进了 inputs | `gl.inputs['Type'].default_value = 'Fog Glow'`、`gl.inputs['Quality'].default_value = 'High'`、`gl.inputs['Threshold'] / ['Size'] / ['Strength']`；不要写 `gl.glare_type` / `gl.threshold` |
| `Error: Node type CompositorNodeMixRGB undefined`（同理 `CompositorNodeMath` / `CompositorNodeComposite`） | 5.x 合成器删掉了旧 Mix/Math/Composite | 合成器树里**改用着色器节点**：`ShaderNodeMix` / `ShaderNodeMath` / `ShaderNodeValToRGB` 仍可 `nodes.new`；叠加用 `CompositorNodeAlphaOver`，调色用 `CompositorNodeColorBalance` / `CompositorNodeCurveRGB` |
| `'CyclesMaterialSettings' object has no attribute 'displacement_method'` | 5.2 把它挪到 Material 本体 | `mat.displacement_method = 'BUMP'`（**不是** `mat.cycles.displacement_method`）；透明混合用 `mat.surface_render_method = 'BLENDED'`（枚举只有 `DITHERED`/`BLENDED`） |
| 控制台每帧刷 `Shadow buffer full (3040 / 2048)`，且 `scene.eevee.shadow_pool_size='4096'` **静默无效** | 阴影池是**枚举**且上限就是 `'2048'`（2 GB） | ① `scene.eevee.shadow_pool_size='2048'`；② 主力是**减投影灯**：`light.use_shadow=False`（点光/面光都算），只留 2–4 盏；③ 大场景再降 `scene.eevee.shadow_resolution_scale`；④ 一行跑完用 `K.light_hygiene(apply=True)`（v2.6 起常驻） |


**5.2 合成器最小可跑片段（Fog Glow 泛光；照抄即可）**：

```python
import bpy
sc = bpy.context.scene
ng = bpy.data.node_groups.get("Comp") or bpy.data.node_groups.new("Comp", "CompositorNodeTree")
sc.compositing_node_group = ng          # 5.0+：不是 sc.node_tree
ng.nodes.clear()
ng.interface.new_socket(name="Image", in_out='OUTPUT', socket_type='NodeSocketColor')
rl = ng.nodes.new('CompositorNodeRLayers')
gl = ng.nodes.new('CompositorNodeGlare')
gl.inputs['Type'].default_value = 'Fog Glow'      # MENU 插座，不是 gl.glare_type
gl.inputs['Quality'].default_value = 'High'
gl.inputs['Threshold'].default_value = 1.0
gl.inputs['Size'].default_value = 8.0
gl.inputs['Strength'].default_value = 0.45
out = ng.nodes.new('NodeGroupOutput')             # 不是 CompositorNodeComposite
ng.links.new(rl.outputs['Image'], gl.inputs['Image'])
ng.links.new(gl.outputs['Image'], out.inputs['Image'])
sc.render.use_compositing = True
```
### 6.5.7 渲染 / 动画的验收回执

```text
【渲染回执】<镜头名 / agent>   <ISO 时间>
  取景    worst NDC = 0.804（<=1.0）   抽样=每 14 顶点   相机关键点 = 21
  分辨率  1920x1080   采样 320   实测 2.02 s/帧   帧数 301   总耗时 605 s
  连续性  全件已编排 UNMATCHED=[]   基准件 = upper_receiver（不动）
  续跑    存在即跳过：本轮补渲 1 帧 / 6 s
  ⚠ 不确定项：<如实列>
```

### A.7. 深水参考

需要超出配方的精度时读 `references/modeling-overview.md`（英文原文，含：bpy.ops.mesh 与 bmesh 两套 API 的选择规则、8 种基本体的专业选择、编辑模式工具箱逐条说明、bmesh 精度 API、硬表面 bevel-weight 打法、布尔工具箱、关键修改器、完整 bpy.ops.mesh 速查，以及作者自己标注的未完成项）。

什么时候去读：配方不匹配（需要 bmesh 级精度或自定义操作）；拓扑要求比平时严（动画可用、游戏 LOD）；性能敏感（foreach_set、批量操作）；要用到配方里没列的算子。

**`references/multiview-calibration.md`**（本技能自带）：三视图联立标定的可运行脚本（纯标准库，`--selftest` 自检）+ 判据表 + 取点规则 + "怎么知道标定是对的"的自证口径 —— 走 §0.6.1 时读它。

### A.8. 来源与许可

本技能由以下两个 MIT 许可项目的**建模流程部分**融合改写而成，工具调用一律换成本机 `blender_rt_*` 直连链：

- **RobLe3/cc-blender-skill**（`blender-modeling` 及其 `references/overview.md`）——决策树、配方、坑表、深水参考
- **arjun988/blender-skills**（`blender-modeler`）——六步总纲、集合结构、修改器规则、清理清单、MUST / MUST NOT

完整归属与许可见同目录 `NOTICE.md`。上游原版面向 MCP（mcp__blender__*），本版已改写为本机直连工具链。
