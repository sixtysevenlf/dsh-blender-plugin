# Spec Delta

## Purpose

定义从 evaluated 场景帧、物体与骨骼世界空间结果采样 3D 动作证据的 session-read 分析契约：把 root motion、局部关节角/角速度、接触/脚滑、采样网格穿插与参考轨迹偏差变成有界、可复核的数字，同时明确它不做动画编辑、物理仿真、修改器/Geometry Nodes/粒子动画或连续碰撞保证。该能力需要真实 Blender 会话，与 host-local 的 `vision_*` 分析分开。

## ADDED Requirements

### Requirement: Animation analysis is a separate session-bound family

catalog 必须（SHALL）新增 `animation` 家族，prefix 为 `animation_`，包含 `animation_report`、`animation_selftest` 和 `animation_help`，并标记 `requires_session=true`。`animation_report`/`animation_help` 是 session-read，不是 read-only；`animation_selftest` 允许在临时 collection 创建测试数据并在结束时清理。该家族不得被 host-local vision 短路截获，也不得登记为 `config/contracts.json` 的 host family。

#### Scenario: Animation needs a Blender session
- **WHEN** 没有已连接的 Blender，调用 `blender_op(op="animation_report", args={objects:["Cube"]})`
- **THEN** 回执为 `stage=session` 的结构化失败，不返回空指标、不启动 vision worker

#### Scenario: Catalog exposes three ops
- **WHEN** 调用 `blender_op(op="catalog")`
- **THEN** catalog 包含 `animation` 家族及 `animation_report`、`animation_selftest`、`animation_help`，并标明需要会话

#### Scenario: Animation is not a host family
- **WHEN** 检查 `config/contracts.json` 与 catalog parity
- **THEN** animation 不被列为 host family，三个 op 由 Blender Python dispatch 覆盖

### Requirement: Session-read requires a session but no write grant

`animation_report` 与 `animation_help` 必须（SHALL）要求已有会话并通过 session-read 门；不得要求 write grant、不得获取写租约。`animation_selftest` 必须（SHALL）走写门，没有 write grant 时拒绝。session-read 不得把 op 登记进 read-only 列表，也不得因为“只读”而跳过会话检查。

#### Scenario: Report works without a write grant
- **WHEN** 会话在线但调用方没有 write grant，调用 `animation_report`
- **THEN** 报告正常返回，不申请写租约、不改场景

#### Scenario: Report without a session fails
- **WHEN** 没有已连接的 Blender，调用 `animation_report`
- **THEN** 回执为 `stage=session`，不执行采样

#### Scenario: Selftest requires a write grant
- **WHEN** 会话在线但没有 write grant，调用 `animation_selftest`
- **THEN** 回执为权限/写门失败，不创建临时对象

### Requirement: Sampling uses effective fps and a bounded frame budget

`animation_report` 必须（SHALL）使用 `effective_fps = scene.render.fps / scene.render.fps_base`；`frame_start`/`frame_end` 是闭区间。未给 `frame_step` 时，必须选择最小整数步长使采样数不超过 240；显式步长导致采样数超过 240 时返回 `animation_sampling_invalid`，不得静默改步长。回执必须返回 `effective_fps`、请求与实际帧范围、实际最后采样帧、步长和采样数。所有由帧差得到的位移、速度、加速度与角速度必须用 `dt = frame_step / effective_fps` 换算，`frame_step>1` 不得把速度按步长倍数放大。

#### Scenario: Fractional fps is honored
- **WHEN** `scene.render.fps=24`、`fps_base=1.001`，调用 `animation_report`
- **THEN** 回执的 `effective_fps` 约为 23.976，时间、速度和角速度按该值换算

#### Scenario: Default sampling fits the budget
- **WHEN** 场景帧范围为 1–480，调用方没有给 `frame_step`
- **THEN** 回执使用 `frame_step=2`，采样 240 帧，实际最后采样帧为 479，并返回请求范围与实际范围

#### Scenario: Step does not scale speeds
- **WHEN** 同一段动画分别以 `frame_step=1` 与 `frame_step=2` 采样，位移相同
- **THEN** `max_speed_m_s` 与 `max_accel_m_s2` 在两种步长下一致（误差在浮点范围内），不出现 step=2 时速度翻倍

#### Scenario: Explicit step over budget is rejected
- **WHEN** `frame_start=1`、`frame_end=480`、`frame_step=1`
- **THEN** 回执为 `stage=input`、`code=animation_sampling_invalid`，不擅自改成 step=2

### Requirement: Bone facts are explicit and selection is caller-defined

`animation_report` 必须（SHALL）对每根参与骨返回 `name`、`parent`、`use_deform` 与 `collections`。骨骼选择优先级必须是：显式 `bones`/`bone_chains` > `bone_collections` > `bone_filter`；`bone_filter` 接受 `deform`（默认，仅 `use_deform=true`）与 `all`。工具不得断言某根骨或某个 collection 是 control/mechanism/DEF 角色。

#### Scenario: Deform filter selects only deform bones
- **WHEN** 骨架同时有 `use_deform=true` 和 `false` 的骨，调用方给 `bone_filter=deform`
- **THEN** 单骨指标只包含 `use_deform=true` 的骨，并返回每根骨的 parent/collections 事实

#### Scenario: Mixed collection is reported as mixed
- **WHEN** 一个 collection 同时包含形变骨与非形变骨
- **THEN** 该 collection 的 `mixed=true`，不得被当作纯形变层

#### Scenario: Deform flag is uninformative
- **WHEN** 所有骨的 `use_deform` 都为 true 或都为 false
- **THEN** 回执带 `deform_flag_uninformative` warning，提示调用方显式给 `bone_chains` 或 `bone_collections`

### Requirement: Bone inventory reports collection composition

当 `include_bone_inventory=true` 时，`animation_report` 必须（SHALL）返回每个 collection 的 `name`、`bone_count`、`deform_bone_count`、`non_deform_bone_count`、`mixed` 与 `sample_bones`。该统计只描述构成，不附带角色结论；agent 可以用它判断“形变骨为主的 collection”，但不能把它当作 rig 语义保证。

#### Scenario: Inventory shows deform composition
- **WHEN** 一个 collection 有 40 根骨且全部 `use_deform=true`
- **THEN** 该 collection 的 `deform_bone_count=40`、`non_deform_bone_count=0`、`mixed=false`

### Requirement: Joint chains are explicit and angles are local

关节指标必须（SHALL）只对调用方显式给出的 `bone_chains` 计算；不得从 `Bone.parent` 自动推导解剖链。关节角必须是局部关节角：用当前骨最终姿态相对父骨最终姿态，与对应 rest 局部变换比较；角速度/角加速度用相邻姿态的四元数差/Δt 计算，不用 Euler 差分。没有 `bone_chains` 时 joints 返回空并 warning；最多 64 根骨，不返回原始矩阵或四元数数组。

#### Scenario: Parent rotation does not become child joint angle
- **WHEN** 父骨旋转、子骨相对父骨不旋转，调用方给显式两骨链
- **THEN** 子骨的局部关节角接近 0，不被父骨旋转污染

#### Scenario: Constraint-driven final pose is measured
- **WHEN** IK 约束改变了骨的最终姿态
- **THEN** 关节指标反映最终姿态，而不是未加约束的通道值

#### Scenario: Missing chain does not guess joints
- **WHEN** 调用方给了 `armature` 和 `bones` 但没有 `bone_chains`
- **THEN** joints 为空并带 warning，不从 parent 自动构造链

### Requirement: Root motion is world-space and direction-preserving

`animation_report` 必须（SHALL）对每个物体/骨架返回 `path_length_m`、标量 `displacement_m`、`displacement_vector_m`、`max_speed_m_s`、`max_accel_m_s2`、`loop_closure_m`、`rotation_delta_deg` 与 `rotation_path_deg`。armature 默认测 object 世界位移；给了 `root_bone` 时测该骨最终世界位置。`rotation_delta_deg` 是最短弧：超过 180° 时会压缩角度并带 warning；`rotation_path_deg` 是逐步累计，不是圈数保证。有限差分样本不足时必须返回 null，不得返回 0 冒充指标。

#### Scenario: Root motion is measured
- **WHEN** 一个物体从第 1 帧到第 24 帧沿 X 轴移动 2 米
- **THEN** 回执的位移向量约为 (2,0,0)、displacement 约为 2 m、path length 约为 2 m

#### Scenario: Root bone motion is measured
- **WHEN** armature object 不动，但 `root_bone` 在世界空间移动
- **THEN** 根运动按该骨的世界位置变化报告，不只报告 object 的零位移

#### Scenario: Insufficient samples return null
- **WHEN** 只有一个采样帧，无法计算速度或加速度
- **THEN** 对应速度/加速度字段为 null，并带说明，不返回 0

### Requirement: Contact and foot slide use a signed sampled approximation

当提供 `contact_bones` 时，`animation_report` 必须（SHALL）用 `contact_point`（`head`/`tail`/`center`，默认 `tail`）的世界坐标与地平面判断接触。判定必须是有符号条件 `z <= ground_z + contact_epsilon`，穿透也算接触；返回接触区间、`contact_samples`、水平位移、最大滑动速度与 `max_penetration_m`。该结果是采样点近似，不是物理接触求解；最多 16 根 contact 骨、64 个区间，超过时按时间顺序截断并返回 `contact_intervals_total`/`contact_intervals_truncated`，不得静默丢弃。

#### Scenario: Penetration still counts as contact
- **WHEN** 脚骨采样点低于 `ground_z` 超过 `contact_epsilon`
- **THEN** 该段仍算接触，并报告 `max_penetration_m`，不因穿透而漏判

#### Scenario: No contact is not an error
- **WHEN** 所有 contact 骨都离地超过阈值
- **THEN** 回执返回空接触区间与原因，不伪造接触、不把空结果当失败

#### Scenario: Armature transform is applied
- **WHEN** armature 整体沿 Z 轴平移后脚骨仍在局部坐标贴地
- **THEN** 接触判断使用 `armature.matrix_world` 后的世界坐标，不使用局部坐标

### Requirement: Intersections are world-space surface overlap with per-frame budgets

当提供两个或更多 `intersection_objects` 时，`animation_report` 必须（SHALL）把参与对象的 evaluated mesh 顶点变换到同一世界空间，做 BVH 表面三角形重叠；只报告表面相交，不承诺实体包含、穿透深度或布尔结果。三角形预算为每采样帧所有参与对象总计 20 万，超预算跳过该帧并记 `skipped_frames`；最多 64 个对象对，按输入顺序确定性截断，截断时回执带 `pairs_total`/`pairs_truncated`。无法解析为 evaluated mesh 的对象必须跳过并在聚合里标为 `skipped`，报告 verdict 至少 degraded，不得当成“无重叠”而给出 pass。

#### Scenario: Overlapping meshes are reported
- **WHEN** 两个物体在若干采样帧发生表面三角形重叠
- **THEN** 回执列出对象对与重叠帧计数，不返回三角形或顶点数据

#### Scenario: Budget exceeded degrades explicitly
- **WHEN** 某采样帧参与对象的总三角形数超过 20 万
- **THEN** 该帧标记 skipped，累计 `skipped_frames`；部分跳过为 degraded、全跳过为 skipped，不把空结果当无穿插

#### Scenario: Non-mesh object is skipped with warning
- **WHEN** `intersection_objects` 中包含非 mesh 对象
- **THEN** 该对象被跳过、聚合状态为 `skipped` 且 verdict 为 degraded，不静默当作空 mesh 或“无穿插”

### Requirement: Reference trajectory is strictly validated

`reference` 是可选参数；一旦提供，必须（SHALL）校验 `{version:1, fps:number, frames:[{frame:number, objects:{name:[x,y,z]}}]}`，最多 2 MiB / 240 帧。fps 必须与 `effective_fps` 一致（容差 1e-6）；帧号必须是唯一整数；坐标必须有限。只按精确帧号匹配，不最近邻；返回每对象 RMSE、最大偏差、matched/missing/unused 帧；缺失帧不得按 0 计算。

#### Scenario: Reference deviation is measured
- **WHEN** reference 给出每帧物体位置，场景轨迹与它相差 0.1 m
- **THEN** 回执的 RMSE 约为 0.1 m、max deviation 约为 0.1 m，并列出匹配帧数

#### Scenario: FPS mismatch is invalid
- **WHEN** reference 的 fps 与 `effective_fps` 不一致
- **THEN** 回执为 `stage=input`、`code=animation_reference_invalid`，不做近似匹配

#### Scenario: Duplicate or non-finite reference is invalid
- **WHEN** reference 的 frames 出现重复帧号、NaN 或 Infinity
- **THEN** 回执为 `animation_reference_invalid`，不把异常值当 0

### Requirement: Sampling is non-persistent but runtime-observable

`animation_report` 必须（SHALL）在成功或异常时恢复原来的 `frame_current` 与 `frame_subframe`，并且不得修改关键帧、F-Curve、pose、材质或场景对象，不得保存 .blend。由于 `frame_set` 会触发 depsgraph、driver 与 frame_change handler，报告不得声称自己是无运行时副作用的并发只读；它只是不持久修改。`animation_selftest` 必须在 finally 清理临时 collection、对象与动作。

#### Scenario: Current frame and subframe are restored
- **WHEN** 调用前当前帧为 7、subframe 为 0.5，`animation_report` 采样 1–24 后抛错
- **THEN** 返回时 `frame_current=7`、`frame_subframe=0.5`，关键帧与对象数量不变

#### Scenario: Selftest cleans up
- **WHEN** `animation_selftest` 成功或失败
- **THEN** 临时 collection、对象与动作被删除，场景对象数量与调用前一致

### Requirement: Animation inputs are validated before sampling

`bones`、`bone_chains`、`contact_bones`、`bone_collections` 或 `root_bone` 必须（SHALL）同时提供 `armature`；`intersection_objects` 至少 2 个；`frame_start` 不得大于 `frame_end`；`frame_step` 与 `contact_epsilon` 必须为正；`ground_z` 必须是有限数；`bone_filter` 只接受 `deform`/`all`，`contact_point` 只接受 `head`/`tail`/`center`。未知骨、collection 或对象名返回 `animation_target_missing`；非法参数组合返回 `animation_input_invalid`；`armature` 指向非 armature 对象时同样返回 `stage=input`、`animation_input_invalid`，不得当作空骨架继续采样。校验必须在采样和方法分发之前完成。

#### Scenario: Bones without an armature are rejected
- **WHEN** 调用方给了 `bones` 或 `bone_chains`，但没有给 `armature`
- **THEN** 回执为 `stage=input`、`code=animation_input_invalid`，不启动采样

#### Scenario: Invalid frame or contact values are rejected
- **WHEN** `frame_start > frame_end`、`frame_step <= 0`、`contact_epsilon <= 0` 或 `ground_z` 不是有限数
- **THEN** 回执为 `animation_input_invalid`，不返回部分指标

#### Scenario: Root bone or collection without an armature is rejected
- **WHEN** 调用方给了 `root_bone` 或 `bone_collections`，但没有给 `armature`
- **THEN** 回执为 `stage=input`、`code=animation_input_invalid`

#### Scenario: Non-armature object is rejected
- **WHEN** 调用方把 mesh 对象名传给 `armature`
- **THEN** 回执为 `stage=input`、`code=animation_input_invalid`，不启动采样、不返回 `animation_unsupported`

#### Scenario: Intersection needs at least two objects
- **WHEN** `intersection_objects` 只包含一个对象
- **THEN** 回执为 `animation_input_invalid`，不把单对象当作无穿插

#### Scenario: Unknown bone is a target failure
- **WHEN** `bone_chains` 或 `contact_bones` 中包含骨架里不存在的骨名
- **THEN** 回执为 `animation_target_missing`，并列出缺失骨名

### Requirement: Animation failures are explicit and bounded

`animation_report` 必须（SHALL）在目标不存在、输入非法、采样预算超限、参考非法、预算不足或没有动画数据时返回结构化失败或降级，不得返回空指标冒充成功。`animation_no_data` 只在所有目标都没有 animation_data 且采样变换无变化时返回；部分目标静态时给零指标与 `static` warning。错误必须带 `stage`（`input`/`animation`）、`code`、`root_cause`、`fix` 与 `fallback`；输入校验与类型错误统一用 `stage=input`，不得使用 `stage=scene`。

#### Scenario: Missing target is input failure
- **WHEN** `objects` 中包含不存在的对象名
- **THEN** 回执为 `stage=input`、`code=animation_target_missing`，并列出缺失名称

#### Scenario: Static target warns without failing the report
- **WHEN** 一个目标没有动画、另一个目标有动画
- **THEN** 报告正常返回，静态目标给零指标与 `static` warning，不整份报 `animation_no_data`

#### Scenario: Oversized result is truncated
- **WHEN** 对象/骨头/帧数超过文档上限
- **THEN** 回执返回有界摘要、truncated=true 与 warning，不返回无限数组

### Requirement: Animation v1 excludes modifier, geometry-node, particle and rig-role analysis

v1 的 `animation_report` 必须（SHALL）只覆盖物体与骨骼运动；evaluated mesh 只用于网格穿插，不解析修改器、Geometry Nodes 或粒子参数；不得把修改器/GN/粒子驱动的几何变化报告成 root motion 或关节指标。v1 不得推断 control/mechanism/deform 角色，也不得提供 Rigify 专用 profile 或 Rigify 示例。

#### Scenario: Modifier-only motion is not root motion
- **WHEN** 物体没有 transform 动画，只有修改器改变几何
- **THEN** root motion 指标为零或静态，不把修改器结果当作物体位移

#### Scenario: No rig-role assertion
- **WHEN** 调用方请求 bone inventory
- **THEN** 只返回 `use_deform`/collections/parent 事实，不返回“这是 DEF/control/mechanism 层”的结论
