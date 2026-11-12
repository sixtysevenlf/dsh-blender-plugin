# 动作分析（animation 家族）

[English](../animation-analysis.md)

`animation` 家族测量真实场景里的运动：对象/骨架的 root motion、显式骨链上的局部关节角、地面
接触与脚滑、网格表面穿插，以及可选的参考轨迹。它逐帧从 evaluated depsgraph 采样，**不直接读
`Action.fcurves`**；action、NLA、driver、约束与 IK 都通过 Blender 自己的求值参与计算。它不分析
修改器/几何节点/粒子参数，不推断骨骼角色（control/mechanism/DEF），也不提供具体绑定规范的示例。

## op 与会话语义

| op | 会话 | 写授权 | 作用 |
|---|---|---|---|
| `animation_report` | 需要（session-read） | 不需要 | 采样指定帧窗口并返回指标 |
| `animation_help` | 需要（session-read） | 不需要 | 列出参数、单位、限制、方法与错误码 |
| `animation_selftest` | 需要（写门） | 需要 | 临时建关键帧物体+骨架、验证后清理 |

`animation_report` / `animation_help` 是 **session-read** op：要求 ready 的 GUI 会话（读的是活场景），
但不取写租约、不要求写 scope 授权。`animation_selftest` 会临时改场景，走普通写门。三者都不在
`READ_ONLY_OPS`；前两者在 `PLAN_SESSION_READ_OPS`。

## 参数

| 参数 | 默认 | 含义 |
|---|---|---|
| `objects` | — | 最多 16 个对象名；对象级 root motion 与穿插 |
| `armature` | — | 骨架对象；`bones` / `bone_chains` / `bone_collections` / `contact_bones` / `root_bone` 都必须同时提供它 |
| `bones` | — | 显式骨名，最多 64 |
| `bone_chains` | — | 显式关节链，例如 `[["upper_arm", "forearm"]]`；**不从 `Bone.parent` 自动推导** |
| `bone_collections` | — | 按 Blender 骨骼 collection 选骨 |
| `bone_filter` | `deform` | `deform`（仅 `use_deform`）或 `all`；显式给骨/链/collection 时不生效 |
| `include_bone_inventory` | `false` | 每个 collection 的骨数、变形/非变形骨数、`mixed`、`sample_bones` |
| `contact_bones` | — | 最多 16 根做地面接触判定的骨 |
| `contact_point` | `tail` | pose bone 的 `head` / `tail` / `center` |
| `ground_z` | `0.0` | 地平面高度（米） |
| `contact_epsilon` | `0.01` | 接触容差带（米）；有符号判定 `z <= ground_z + epsilon` |
| `frame_start` / `frame_end` | 场景范围 | 闭区间；只给一个时必须同时给另一个 |
| `frame_step` | 自动 | 正整数；省略时取最小整数 step，使采样数 ≤ 240 |
| `root_bone` | — | 用该骨的最终世界变换作为 root motion 来源（默认用骨架对象） |
| `intersection_objects` | `objects` | 2..16 个 mesh 对象；两两表面重叠 |
| `reference` | — | `{version: 1, fps, frames: [{frame, objects: {name: [x, y, z]}}]}`，最多 240 帧 / 2 MiB |

选骨优先级：显式 `bones`/`bone_chains` → `bone_collections` → `bone_filter`。`objects` 与
`armature` 至少要给一个。

## 单位与采样

- 单位：长度 **m**、角度 **deg**、速度 **m/s**、角速度 **deg/s**、`effective_fps` 为 1/s。
- `effective_fps = scene.render.fps / scene.render.fps_base`（24 配 `fps_base=1.001` 即 23.976…）。
- 逐帧 `scene.frame_set(frame)`，再从 evaluated depsgraph 读 `Object.matrix_world` 与
  `PoseBone.matrix`。采样间隔是 `dt = frame_step / effective_fps`；速度、角速度、滑移速度一律
  除以这个 `dt`，所以 `frame_step > 1` 不会把速度放大（也不会把加速度平方放大）。
- `frame_start..frame_end` 是闭区间；不给 step 时选满足 `sample_count <= 240` 的最小整数步长。
  显式 step 超 240 返回 `animation_sampling_invalid`，**不会静默改小**。
- 回执带请求范围、实际范围、`frame_step`、`sample_count`、`last_sample_frame` 与
  `effective_fps`。
- `frame_current` / `frame_subframe` 在 `finally` 中恢复；不改关键帧/pose/材质，不保存 .blend。

## 指标

### Root motion

每个采样对象（以及给 `root_bone` 时的该骨）：`path_length_m`、`displacement_m`、
`displacement_vector_m`、`max_speed_m_s`、`max_accel_m_s2`、`loop_closure_m`、
`rotation_delta_deg`、`rotation_path_deg`。速度/加速度用帧差与 `effective_fps`；只有一个样本时
返回 `null`，绝不填 0。`rotation_delta_deg` 取最短弧（超过 180° 会压缩并给
`rotation_shortest_arc` warning）；`rotation_path_deg` 是逐步角度累计。窗口内变换不变的对象给零
指标 + `static` warning；所有目标都没有 `animation_data` 且无变换变化时返回 `animation_no_data`。

### 关节

只有显式 `bone_chains` 会生成关节行。`pose_local = parent_pose^-1 @ pose`、
`rest_local = parent_rest^-1 @ rest`（链首骨的父变换取 identity），
`delta = pose_local.to_3x3() @ rest_local.to_3x3().inverted()`，角度取
`delta.to_quaternion().angle`；角速度/角加速度由相邻姿态四元数差除以 Δt。父骨旋转带着子骨一起
转时，子骨的局部角变化 ≈0。

### 接触

接触点 = pose bone 的 head/tail/center 乘骨架对象矩阵。`z <= ground_z + contact_epsilon` 即算
接触，**穿透也算接触**。每根骨返回 `intervals`（`start_frame`、`end_frame`、`contact_samples`、
`horizontal_slide_m`、`max_slide_speed_m_s`、`max_penetration_m`）。`frame_step > 1` 时是采样
近似。空接触是空列表，不算失败。

### 穿插

每个采样帧构建 evaluated 世界空间网格，用 `BVHTree.FromPolygons(..., all_triangles=True)` 判表面
重叠；每对返回 `overlap_frames` / `sample_frames`。每帧所有参与对象的三角形预算为 200,000：
超预算跳过该帧并记入 `skipped_frames`（部分跳过 `degraded`，全部跳过 `skipped`）。无法解析的穿插
（可用 mesh 少于 2 个、非 mesh 目标、所有帧都被跳过）不会冒充“已测量”：结果记 `status:
"skipped"` 并给 warning，回执的 verdict 是 `degraded`，绝不会是 `pass`。最多 64 对象对，按输入
顺序截断。非 mesh 对象跳过并给 warning。回执不返回三角形/顶点数组。

### 参考轨迹

`reference` 严格校验：`version: 1`、`fps` 与 `effective_fps` 容差 `1e-6`、帧号唯一且为整数、
坐标有限。只按**精确帧号**匹配（不找最近邻），缺失帧记为 `missing`，绝不补 0。每个对象返回
`rmse_m`、`max_deviation_m`、`matched`、`missing`、`unused`。

## 限制与错误

硬上限：16 对象、64 骨、16 接触骨、64 接触区间、64 相交对、240 采样、240 参考帧 / 2 MiB。
超限截断并给 warning 与 `truncated: true`。

stage 划分：参数形态错误一律是 `input`（不是 `scene`）——非法组合、非法数值、把非骨架对象
传到 `armature`（`animation_input_invalid`）、采样算术错误。只有必须读活场景才能回答的“不存在”
错误用 `scene`（`animation_target_missing`：对象/骨架/骨/骨骼 collection 不存在）。

| 错误码 | stage | 含义 |
|---|---|---|
| `animation_input_invalid` | `input` | 参数组合非法（骨参数缺 armature、`armature` 传了非骨架对象、相交对象 <2、filter/point/epsilon/ground_z 非法） |
| `animation_sampling_invalid` | `input` | frame_start > frame_end、frame_step <= 0，或显式 step 超 240 采样 |
| `animation_target_missing` | `scene` | 对象、骨架、骨或骨骼 collection 不存在 |
| `animation_reference_invalid` | `input` | version 错、fps 不匹配、帧重复、坐标非有限/帧号非整数 |
| `animation_no_data` | `animation` | 所有目标无 animation_data 且采样变换无变化 |
| `animation_budget_exceeded` | `animation` | 预算无法满足（回执带被跳过的证据） |
| `animation_unsupported` | `animation` | 运行时/API 异常 |

每个失败都带 `stage`、`root_cause`、`fix`、`fallback`，调用方不用猜。

## 示例

```python
# 显式窗口上的对象级 root motion
blender_op(op="animation_report", args={"objects": ["CHAR_body"], "frame_start": 1, "frame_end": 48})
# 显式骨链 + 地面接触
blender_op(op="animation_report", args={
  "armature": "CHAR_rig", "bone_chains": [["thigh_L", "shin_L", "foot_L"]],
  "contact_bones": ["foot_L", "foot_R"], "contact_point": "tail", "ground_z": 0.0,
  "frame_start": 1, "frame_end": 120, "frame_step": 1})
# 同一窗口内两个对象的网格穿插
blender_op(op="animation_report", args={"objects": ["door"], "intersection_objects": ["door", "frame"],
  "frame_start": 1, "frame_end": 24})
# 通用发现与可验证清理的 selftest
blender_op(op="animation_help")
blender_op(op="animation_selftest")
```

上面的骨名只是占位符，请替换成场景里的真实名字。`animation_help` 只列通用字段与限制，不提供
具体绑定规范的示例。
