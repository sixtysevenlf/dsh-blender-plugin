---
name: blender-modeling
description: Blender 建模流程与装配教程——先按决策树选形（分面硬表面 / 光滑硬表面 / 有机 / 重复阵列 / 布尔），再走流水线（冻结规格 → 场景准备 → Blockout → 粗形精修 → 修改器栈 → 清理 → 交接），配 10 个可直接运行的配方（基本体 / 分面装甲板 / Bevel+SubSurf 光滑栈 / bmesh 编辑 / 布尔开孔 / 镜像 / Array 沿曲线 / 参数化精确节距阵列 / 装配审计门 / 固定机位对照验收），覆盖六条必踩的坑（长轴·宽轴·薄轴语义、拼件必须互相插入 5–15mm、收尖压中线再合并、headless 慎用 bpy.ops、法线方向是一切穿模判据的前提、预览装置默认光照要看得清板缝），并含多 Agent 并行建模的分工范式（冻结 spec.py / 写作用域协议 / 接口表 / 深嵌+复检 / 数值门装配审计）与参考图驱动的形体还原（比例标定 + 双证据验收），以及本机 blender_rt_* 直连通道语义。用于「做一个 / 建一个 / 捏一个 3D 物体」「加个立方体 / 球 / 圆柱」「挤出 / 内插 / 倒角这个面」「加个修改器」「挖个洞」「做个剑 / 椅子 / 门」等几何创建与网格编辑请求，也用于「多个 agent 分工造一台机器」「按参考图还原外形 / 量比例」「装配体检：查穿模 / 浮空 / 嵌入深度」等大件建模与装配任务。
license: MIT（融合自 RobLe3/cc-blender-skill 与 arjun988/blender-skills，见 NOTICE.md）
---

# Blender 建模流程

> **v2 · 2026-09-25**：按《M1A1 分件建模》实测反馈扩写（500 对象 / 7 个 agent / 全程 headless）。
> 新增 **§0.5 多 Agent 并行建模的分工范式**、**§0.6 参考图驱动的形体还原**、坑四/坑五/坑六，
> 修正配方 2 / 3 / 6 的「载体不匹配」（分面硬表面不要上 SubSurf、headless 慎用 bpy.ops、Array 做不到精确节距），
> 并把 §6 清理清单升级成**数值门清单**。逐条对照见 §9。

先定形状，再走流程，改一步看一眼。**不要一口气写完整个模型再回来看**——每一步都要有画面或数字证据。

## 0. 本机工具与通道语义（先读，这决定你怎么写代码）

| 要做的事 | 用哪个工具 | 关键语义 |
|---|---|---|
| **拉起 Blender（GUI 会话）** | `blender_viewport(op="launch")` | **v0.9.4 起 agent 能自己点亮 GUI**：写 boot 脚本（GUI 里 enable addon → 起 socket server）→ detached spawn blender.exe → **轮询 9876 端口**（唯一可信判据）→ 顺手 doctor；幂等（已在监听只回 already，不会堆出第二个 Blender）。参数：wait_ms / file / exe / addon_module / addon_file / dry_run |
| 迭代建模、改一步看一眼 | `blender_rt_do` | **常驻 GUI 会话 + 持久内核 K**：整步约 105ms，变量**跨调用保留**；可选立刻回一帧 |
| 批处理 / 重活（**第一路径**） | `blender_rt_headless` | **每次都是全新 Blender 进程**（blender -b）：变量**不保留**，每次重新 import；shots 参数可一次出多视角；引擎默认 EEVEE + 光追 |
| 跑很久的活 | `blender_rt_job` | 作业层：op=start / **wait** / status / collect；日志落 `jobs/<id>/`；超时不丢结果（回 promoted + jobId） |
| 反复跑同一个脚本 | `blender_rt_worker` | 热无头会话：冷启动 + EEVEE 着色器编译只付一次。**单例串行** → 多 agent 并行时**不要共用**（会互相串场景） |
| 看画面 / 看动没动 | `blender_rt_see` / `blender_rt_watch` | see 回一帧（给 from/look_at 走自定义视角，带 coverage_estimate）；watch 在时间窗内采样 ≤6 帧 + 逐帧 hash |
| 回退到改动前 | `blender_rt_txn` | mark/revert 是对象级、毫秒；**拓扑改动**（布尔 / 删面 / 新建对象）必须 snapshot/restore |
| **装配体检（数值门）** | `blender_rt_plan(op="audit_*")` | audit_mesh · audit_scene · audit_connectivity · audit_gate · **audit_interference** · audit_overlap · audit_measure · audit_drift · audit_duplicates · audit_snap_floaters（见 §6） |
| **对照图 / 逐部件配色** | `blender_rt_plan(op="qc_render_views")` | 按 AABB 自动取景 + 临时三点光 + 逐张 md5 + coverage_estimate；`mode="parts_color"` 出逐部件配色图（图例写 parts_color_meta.txt） |
| **交付导出** | `blender_rt_plan(op="deliver_*")` | 单位盒归一化 + 多组 OBJ/MTL + manifest md5 |
| 查场景 / 对象 / 引擎 | `blender_rt_cmd` `rt_commands` `rt_perf` | cmd 传 get_scene_info / get_object_info；commands 列全部 addon 命令；perf status 看引擎与采样 |

**改一步看一眼的正确节奏**：调 `blender_rt_do` 并在同一次调用里 see=true → 看图 → 不对就改 → 再看。局部修正只看问题视角加受影响的邻视角，不做每次全场景重建。

> 通道差异是本节最容易出错的地方：老式 MCP 教程会让你"每次调用重新 import 一切"——那对 `blender_rt_headless` 成立，对 `blender_rt_do` **不必要**（内核 K 和已导入的名字都还在）。但**依赖上一次调用留下的变量本身是脆的**：跨会话、后端重启、走 job 之后都会丢。稳妥写法是在**当次调用内**重新取一遍对象引用（`bpy.data.objects['GEO-x']`），不要假设 `obj` 这个变量还在。

**写通道租约（多 agent 必读）**：写操作自动带 holder 租约；别的会话持有时返回 **409 leased**，只读 op 豁免。
`blender_viewport(op="who")` 先看谁在写 → `op="lease"` 拿 → 真要抢再加 `force=true`。
**多 agent 并行的正确姿势是 headless**（每条调用独立进程，天然并行），不是抢 GUI 租约。

## 0.5 多 Agent 并行建模的分工范式（★ 做大件必读）

> 单人单线捏一个物体，配方够用；**造一台机器**（几百个对象、多个 builder）时，决定成败的不是捏形状，而是**分工协议**。
> M1A1 实测：500 对象 / 7 个 agent，**装配质量几乎全靠这一章**。

### 0.5.1 第一步永远是冻结规格（`lib/spec.py`）

在任何人动手之前，Lead 先写 `lib/spec.py` 并**冻结**（尺寸 + 接口表 + 命名 + 材质映射 = 唯一真源）：

```python
# lib/spec.py —— 唯一真源。builder 只读，不许各自定义常量。
TOTAL = {"length": 9.83, "width": 3.66, "height": 2.88}      # m（口径写清：含炮管 / 含裙板）
WHEEL_R, WHEEL_W, WHEELBASE = 0.33, 0.20, 4.60
DECK_Z, HULL_BOTTOM_Z = 1.62, 0.42
PARTS = {                                                     # 命名 → 归属 → 写作用域
    "GEO-hull":    {"owner": "hull-dev",    "file": "lib/hull.py"},
    "GEO-turret":  {"owner": "turret-dev",  "file": "lib/turret.py"},
    "GEO-rungear": {"owner": "rungear-dev", "file": "lib/rungear.py"},
}
INTERFACES = [                                                # 接口表：拼件之间的"合同"
    {"a": "GEO-turret_ring", "b": "GEO-hull_deck_ring", "normal": (0, 0, 1),
     "plane_z": DECK_Z, "embed_mm": 12, "tol_mm": 2},
]
```

**没有冻结规格就分件 = 装配必崩**：炮塔和车体各按各的理解建，接口对不上是必然，不是运气问题。

### 0.5.2 写作用域协议（谁写哪个文件）

- 每个 builder **只写**自己那条线：`lib/<part>.py` + `work/<part>_test.py` + `qc/<part>_*`。
- `lib/spec.py` **只读**：要改规格 → 找 Lead 改，改完**通知全员重新量**（旧数字全部作废）。
- 装配层 `lib/assemble.py` 归属**单独一个人**（Lead 或专职 assembler）；builder 不许碰。
- 共享文件系统**没有锁**：两个人同时写一个文件，后写的覆盖前面的。守界就是纪律。

### 0.5.3 接口表怎么定（拼件的"合同"）

每条接口写清四件事：**名义面**（plane + normal）、**嵌入深度 5–15mm**（坑二）、**公差**、**朝向语义**（谁的局部轴朝哪）。
这一条把"贴合面只能靠猜"变成"按名义面深嵌、装配后复检"。

### 0.5.4 并行期无法互测 = 固有代价，用"深嵌 + 装配后复检"消化

并行时邻件还没建出来，你**物理上不可能**知道真实贴合面。正确做法：

1. 按接口表的**名义面深嵌** 5–15mm（藏在较大件内部，外观上看不见）；
2. 交付时写清"我按名义面 X 嵌了 N mm，**实际面未验证**"；
3. 装配层跑 `audit_interference` / `audit_measure` 一次性收敛所有偏差。

**不要为了"看起来贴合"去猜邻件的实际形状** —— 那只会让偏差集中到装配时爆发。

### 0.5.5 单件自证 → 装配门（用数字代替肉眼）

| 门 | 判据 | 工具 |
|---|---|---|
| 单件网格 | 0 非流形 / 0 开边 / 0 孤立点 | `audit_mesh` / `audit_scene` |
| 尺寸 | 关键 bbox 与 spec 偏差 ≤ 公差（军模级建议 ±3mm） | `audit_measure` |
| 浮空 | 0 悬空件（可见浮块 = 最长 bbox 边 ≥ 全模型 1%） | `audit_connectivity` / `audit_snap_floaters` |
| 穿模 | 无异常互穿；**允许的深嵌必须白名单化**（否则审计天天报） | `audit_interference` / `audit_overlap` |
| 嵌入 | 拼件互相插入 **5–15mm** | `audit_measure` 的逐轴重叠 |
| 形体 | 固定机位对照图 **+** 数值 bbox（两个都要） | `qc_render_views` + `audit_measure` |

**装配层最后统一 recalc 法线**（`bpy.ops.mesh.normals_make_consistent(inside=False)` 或 bmesh 等价）——
见坑五：法线是**所有**穿模 / 内含判据的前提，装配层必须兜底一次。

### 0.5.6 分工反模式（实测踩过的）

- ❌ 两个 builder 写同一个文件 → 静默覆盖。
- ❌ 7 个 builder 共用 `blender_rt_worker` → 单例串行 + 场景互相污染。
- ❌ 每个 builder 各自定义尺寸常量 → 装配对不上；常量必须在 `spec.py`。
- ❌ 用"看起来对"验收 → 必须给数字（bbox / 间隙 / 面数 / 嵌入深度）。
- ❌ builder 自己跑装配 / 导出 → 那是装配层的事。
- ❌ 拿单张成品图当形体证据 → 必须固定机位对照图（§0.6）。

## 0.6 参考图驱动的形体还原（"像不像"的关键）

> 实测：炮塔外形不是"捏"出来的，是**从参考图量出来的**。"裙板下沿 / 甲板高 = 48.2% vs 规格 48.4%"这种对拍才是形体验收。

### 0.6.1 三步标定

1. **找标尺**：在参考图里找已知长度的特征（轴距 / 轮径 / 总长 / 炮管长）。
2. **建比例**：`px_per_mm = 特征像素长 / 特征真实长度`；多特征交叉验证，偏差 >2% 就说明这张图不是正交的（或透视太强）。
3. **量特征**：把要建的关键尺寸逐个量成毫米，写进 `spec.py`，**附来源**（图名 + 像素坐标 + 标尺）。

### 0.6.2 双证据验收（缺一不可）

- **数值**：`audit_measure` 出 bbox / 逐轴间隙，与 `spec.py` 对拍。
- **画面**：`qc_render_views` 用**与参考图相同的机位**出图，叠着看。

单张"好看的成品图"**不能**验收形体 —— 前裙甲上翘就是这么漏掉的：只有固定机位对照图 + bbox 数字才抓得出来。

### 0.6.3 读图纪律

读图结论必须三分：【**实测看到的**】【**推断的**】【**我无法判断的**】。比例、遮挡关系、法线朝向这三类最容易"看着像就下结论"。

## 1. 六步总纲

```
0 冻结规格   → spec.py（尺寸 + 接口表 + 命名）★ 多 agent 时这一步是硬前置（§0.5）
1 场景准备   → 集合分层、比例参照、命名规范
2 Blockout   → 基本体摆大形（可随时推翻）
3 粗形精修   → 编辑模式 / 修改器，把比例做对
4 修改器栈   → 非破坏性地加细节（倒角 / 细分 / 布尔…）
5 清理       → 合并重复点、法线、流形、松散几何（数值门见 §6）
6 交接       → 交给 UV / 材质 / 渲染，或导出（装配层统一 recalc + 审计）
```

要点：
- **比例在低模阶段解决**。加细分只让表面更光滑，**不会**改善造型是否像。
- **非破坏优先**：布尔保持 live 到导出前；拓扑定型再 apply 镜像。
- **Blockout 与成品分开**：轮廓没被认可前不要删 blockout。
- 每一步都留下可对照的固定相机画面，别只存最后一张好看的图。

集合结构建议（大场景时）：

```
COL_Project
├── COL_Reference      # 比例参照、正交参考图
├── COL_Blockout       # 临时大形
├── COL_Geo            # 最终几何
├── COL_Collision      # 代理网格
└── COL_Instances      # 实例化用的空物体 / 集合
```

命名：立即给名字，**永远不要留 Cube.001**；本技能统一用 `GEO-` 前缀（如 GEO-blade、GEO-guard）。
**多 agent 时命名就是接口**：名字一旦进 spec.py 就不能改（改名字 = 改所有人的引用）。

## 2. 决策树：什么形状走什么路子

```
要做什么几何？
├── 硬表面 · 分面装甲 / 军模 / 机械板件（有明确棱边、板缝）
│   → 平面板 + 小倒角（Bevel 1–3mm, 1–2 段, ANGLE 30°）＋**平坦着色** → 配方 2a
│   ✗ 不要上 SubSurf：它会把装甲棱边全部圆掉，一眼就从"军模"变"塑料玩具"
├── 硬表面 · 光滑曲面（汽车钣金 / 消费电子 / 圆润道具）
│   → Cube + Bevel + SubSurf 栈 → 配方 2b
├── 有机（角色 / 生物 / 植物——只做粗形）
│   → Ico Sphere（统一三角面，适合雕刻与体素重构）
│   → 需要"笔触式雕刻"时说明：那是手势输入，不适合文本驱动
├── 建筑 / 重复（栅栏、柱子、瓷砖）
│   → Plane/Cube + Array（+ Curve 沿路径）→ 配方 6
├── **等距重复 / 必须精确闭合**（履带、链节、拉链、齿圈）
│   → **参数化路径求解**，不要用 Array → 配方 6b
├── 管状（管道、柱子、瓶子）
│   → Cylinder，或 Curve + bevel_object
├── 在已有几何上开孔 / 切
│   → Boolean DIFFERENCE → 配方 4
└── 只要快速布景（大形阶段）
    → 多个基本体直接摆 → 配方 7
```

基本体怎么选：**Cube** 硬表面主力；**Ico Sphere** 有机／雕刻底模（三角面最均匀）；**UV Sphere** 要贴图的圆物（自带好 UV）；**Cylinder** 机械件；**Cone / Torus** 尖角与环。

## 3. 配方（可直接跑）

> 下面代码块是**在 Blender 里执行的 Python**。交互式用 `blender_rt_do(code=…)` 跑；批处理用 `blender_rt_headless(script=…)`（那时记得在开头 import bpy，且**变量不跨调用保留**）。
> ⚠ **headless 下 bpy.ops 的 context 很脆**（详见坑四）：批处理脚本优先用 **bmesh / data API**，配方里同时给了两套写法。

### Recipe 1 — 加基本体并立即命名

```python
import bpy
bpy.ops.mesh.primitive_cube_add(size=2.0, location=(0, 0, 1))
obj = bpy.context.active_object
obj.name = 'GEO-base_box'
print(f"created:{obj.name} verts:{len(obj.data.vertices)}")
```

把 primitive_cube_add 换成 _plane_ / _uv_sphere_ / _ico_sphere_ / _cylinder_ / _cone_ / _torus_ / _monkey_，参数各按形状给（radius、depth、vertices、segments、subdivisions）。
（这几种 primitive_*_add 在 headless 下是**安全**的：它们不依赖编辑模式 context。脆的是 `mesh.*` / `object.mode_set` 那一类。）

### Recipe 2a — 分面硬表面（装甲板 / 军模 / 机械板件）★ 先看这条

```python
import bpy
obj = bpy.data.objects['GEO-armor_plate']

bevel = obj.modifiers.new('Bevel', type='BEVEL')
bevel.width = 0.002          # 2mm：只是把"刀口"磨掉，棱边仍然清晰
bevel.segments = 1           # 分面板件通常 1 段就够（要更圆用 2）
bevel.limit_method = 'ANGLE'
bevel.angle_limit = 0.523599  # 30°：只倒比这更尖的边

# ✗ 不要加 SubSurf —— 分面装甲板加细分会把棱边圆成"肥皂"
for p in obj.data.polygons:
    p.use_smooth = False      # 平坦着色：板面之间要能看出转折
print(f"faceted:{obj.name} polys:{len(obj.data.polygons)}")
```

判据：**边缘高光是一条细线**（倒角）**而不是一道渐变**（细分）；相邻板面之间的转折在渲染里应当**看得见**。
参考图里能数出板缝的模型，一律走这条。

### Recipe 2b — 光滑硬表面（汽车钣金 / 圆润道具）

```python
import bpy
obj = bpy.data.objects['GEO-base_box']

# 1. Bevel：先把硬边倒圆
bevel = obj.modifiers.new('Bevel', type='BEVEL')
bevel.width = 0.02            # 2cm 圆角
bevel.segments = 3
bevel.limit_method = 'ANGLE'  # 只倒比阈值更尖的边
bevel.angle_limit = 0.523599  # 30 度（弧度）

# 2. Subdivision 必须在 Bevel 之后
subsurf = obj.modifiers.new('SubSurf', type='SUBSURF')
subsurf.levels = 2
subsurf.render_levels = 3

# 3. 平滑着色
bpy.context.view_layer.objects.active = obj
bpy.ops.object.shade_smooth()
print(f"hardsurface:{obj.name} polys:{len(obj.data.polygons)}")
```

**顺序反了就会出收窄伪影**（最常见的业余错误）。
**载体判据**：只有"表面本来就连续"的件才配这一栈；分面件（配方 2a）上 SubSurf = 毁形。

### Recipe 3 — 编辑模式：挤出 / 内插 / 环切（GUI 可用；headless 见 3b）

```python
import bpy
obj = bpy.data.objects['GEO-base_box']
bpy.context.view_layer.objects.active = obj
bpy.ops.object.mode_set(mode='EDIT')

bpy.ops.mesh.select_all(action='SELECT')
bpy.ops.mesh.extrude_region_move(TRANSFORM_OT_translate={'value': (0, 0, 1.0)})
bpy.ops.mesh.inset(thickness=0.1, depth=0)
bpy.ops.mesh.loopcut_slide(
    MESH_OT_loopcut={'number_cuts': 1, 'edge_index': 0},
    TRANSFORM_OT_edge_slide={'value': 0.0},
)
bpy.ops.object.mode_set(mode='OBJECT')
print(f"edited:{obj.name} verts:{len(obj.data.vertices)}")
```

### Recipe 3b — 同一件事的 bmesh 写法（★ headless / 批处理用这条）

```python
import bmesh, bpy
obj = bpy.data.objects['GEO-base_box']
bm = bmesh.new()
bm.from_mesh(obj.data)

# 1) 找到朝上的那个面（比"选中顶面"更稳：不依赖选择状态与 context）
up = [f for f in bm.faces if f.normal.z > 0.9]
face = up[0]

# 2) 挤出 + 平移（等价 extrude_region_move）
res = bmesh.ops.extrude_face_region(bm, geom=[face])
new_verts = [g for g in res['geom'] if isinstance(g, bmesh.types.BMVert)]
bmesh.ops.translate(bm, verts=new_verts, vec=(0.0, 0.0, 1.0))

# 3) 内插（等价 mesh.inset）
top = [f for f in bm.faces if f.normal.z > 0.9 and f.calc_center_median().z > 1.0]
bmesh.ops.inset_individual(bm, faces=top, thickness=0.1, depth=0.0)

bm.to_mesh(obj.data)
bm.free()
obj.data.update()
print(f"bmesh-edited:{obj.name} verts:{len(obj.data.vertices)}")
```

**为什么值得改写法**：`bpy.ops.mesh.*` 依赖"当前编辑模式 + 活动对象 + 选择集 + 区域 context"，
在 `blender -b` 下这些经常是空的，报错信息还很难懂（"context is incorrect"）。
bmesh 直接操作数据层，**没有 context 依赖**，批处理里稳得多。必须用 ops 时用 `temp_override` 补 context。

### Recipe 4 — 布尔开孔

```python
import bpy
target = bpy.data.objects['GEO-base_box']
cutter = bpy.data.objects.get('GEO-cutter')
if cutter is None:
    bpy.ops.mesh.primitive_cylinder_add(radius=0.3, depth=3.0, location=(0, 0, 1))
    cutter = bpy.context.active_object
    cutter.name = 'GEO-cutter'

mod = target.modifiers.new('Boolean', type='BOOLEAN')
mod.operation = 'DIFFERENCE'
mod.object = cutter
mod.solver = 'EXACT'

bpy.context.view_layer.objects.active = target
bpy.ops.object.modifier_apply(modifier=mod.name)   # 想保持非破坏就先别 apply
cutter.hide_viewport = True
cutter.hide_render = True
print(f"booleaned:{target.name}")
```

> 实测：军模里布尔**用得不多但很关键**（炮塔前下方 undercut、炮口开孔）。用法是"**外科手术**"，
> 不是主力建模手段 —— 大面积造型靠板件拼装（§0.5），布尔只负责那几个真开孔的地方。

### Recipe 5 — 镜像（只做一半）

```python
import bpy
obj = bpy.data.objects['GEO-character_half']
mod = obj.modifiers.new('Mirror', type='MIRROR')
mod.use_axis[0] = True     # 沿 X 镜像
mod.use_clip = True        # 轴上顶点吸附
mod.use_mirror_merge = True
mod.merge_threshold = 0.001
print(f"mirrored:{obj.name}")
```

**Mirror 放在栈的最前面**（在 Bevel / SubSurf 之前）。

### Recipe 6 — Array 沿曲线（链子、栅栏、珠子）

```python
import bpy
bpy.ops.mesh.primitive_cube_add(size=0.2, location=(0, 0, 0))
unit = bpy.context.active_object
unit.name = 'GEO-bead'

path = bpy.data.objects.get('GEO-path')
if path is None:
    bpy.ops.curve.primitive_bezier_curve_add()
    path = bpy.context.active_object
    path.name = 'GEO-path'

arr = unit.modifiers.new('Array', type='ARRAY')
arr.fit_type = 'FIT_CURVE'
arr.curve = path
arr.relative_offset_displace = (1.0, 0, 0)

crv = unit.modifiers.new('Curve', type='CURVE')
crv.object = path
crv.deform_axis = 'POS_X'
print(f"arrayed:{unit.name}")
```

> ⚠ **载体判据**：Array 适合"装饰性重复"（珠子、栅栏、铆钉），**做不到精确节距闭合** ——
> `FIT_CURVE` 只保证"装满"，首尾接缝与节距都不受你控制。凡是"节距是规格、必须闭合"的（履带、链节、齿圈），
> 一律走 6b。

### Recipe 6b — 参数化路径阵列（★ 履带 / 链节 / 齿圈）

```python
import bpy, math
from mathutils import Vector

N, PITCH = 83, 0.176          # 节数、节距（m）—— 总周长必须 ≈ N * PITCH，先自己核对
unit = bpy.data.objects['GEO-track_link']

def path(t):                   # t ∈ [0,1) → (位置, 切向单位向量)
    """履带包络：直线段 + 圆弧段拼出来的封闭路径（按弧长参数化！）"""
    s = t * (N * PITCH)        # 弧长
    # ... 你的几何：直线段直接线性插值；圆弧段 pos = center + R*(cos,sin)，切向 = 圆周切向
    return Vector((0, 0, 0)), Vector((1, 0, 0))

for i in range(N):
    pos, tan = path(i / N)
    obj = unit.copy()                       # 或者用 linked duplicate 省内存（几百节时明显）
    bpy.context.collection.objects.link(obj)
    obj.location = pos
    obj.rotation_euler = tan.to_track_quat('Y', 'Z').to_euler()
    obj.name = 'GEO-track_link_%03d' % i
print('links:%d pitch:%.4f' % (N, PITCH))
```

**验收（脚本量，不许靠看）**：① `总弧长 / N` 与 PITCH 的偏差 ≤ 0.5mm；
② 第 0 节与第 N-1 节的相邻间隙 == 中段相邻间隙（±0.5mm）；
③ `audit_connectivity` 里履带是一条连通分量（不散架）。

### Recipe 7 — Blockout 布景

```python
import bpy
bpy.ops.mesh.primitive_plane_add(size=10)
bpy.context.active_object.name = 'GEO-floor'
bpy.ops.mesh.primitive_cube_add(size=1.5, location=(0, 0, 0.75))
bpy.context.active_object.name = 'GEO-subject'
bpy.ops.mesh.primitive_cylinder_add(radius=0.5, depth=2, location=(2, 1.5, 1))
bpy.context.active_object.name = 'GEO-prop_pillar'
print('blockout:done')
```

### Recipe 8 — 曲线转网格 / 布尔之后的清理（装配层也跑这一条）

```python
import bpy
obj = bpy.data.objects['GEO-target']
bpy.context.view_layer.objects.active = obj
bpy.ops.object.mode_set(mode='EDIT')
bpy.ops.mesh.select_all(action='SELECT')
bpy.ops.mesh.remove_doubles(threshold=0.0001)
bpy.ops.mesh.normals_make_consistent(inside=False)   # ★ 法线一致化：穿模判据的前提（见坑五）
bpy.ops.object.mode_set(mode='OBJECT')
bpy.ops.object.shade_smooth()
print(f"cleanup:{obj.name} verts:{len(obj.data.vertices)}")
```

> headless 里 `mode_set` / `mesh.*` 可能因 context 失败 —— 等价 bmesh 写法：
> `bmesh.ops.remove_doubles(bm, verts=bm.verts, dist=1e-4)` + `bmesh.ops.recalc_face_normals(bm, faces=bm.faces)`。

### Recipe 9 — 装配审计门（把 §6 的数值门一把跑完）

```python
# 方式 A（推荐，模型侧）：工具层直接调，回执是结构化 JSON
#   blender_rt_plan(op="audit_gate",      args={"scope": "COL_Geo"})          # 出厂门：连通 + 包络
#   blender_rt_plan(op="audit_interference", args={"a": "GEO-rungear", "b": "GEO-hull"})
#   blender_rt_plan(op="audit_measure",   args={"neighbors": True})           # bbox + 逐轴间隙/重叠

# 方式 B（headless 批处理里）：preload="audit" 之后直接调 python API
#   K.dsh_audit_api("mesh", {"objects": ["GEO-hull"]})
#   K.dsh_audit_api("connectivity", {"scope": "COL_Geo"})
#   K.dsh_audit_api("gate", {"scope": "COL_Geo", "envelope": ENVELOPE})
```

判据全在 §6。**装配完成后、导出之前，必跑一次**；每个 builder 交件前也要跑单件那几条。

### Recipe 10 — 形体验收：固定机位对照图 + 数值 bbox

```python
# ① 数值：与 spec.py 对拍（这是"像不像"的硬证据）
#   blender_rt_plan(op="audit_measure", args={"objects": ["GEO-turret"], "neighbors": False})
#   → 回 sizeof/bbox；逐个与 spec.py 的期望值比，偏差写进报告（军模级 ±3mm）

# ② 画面：用**与参考图相同的机位**出图（多视角 harness 自带取景与三点光）
#   blender_rt_plan(op="qc_render_views", args={
#       "views": [{"name": "front", "from": [0, -12, 1.6], "look_at": [0, 0, 1.6], "lens": 50}],
#       "res": [1280, 720], "samples": 64, "outdir": "D:/DSH/blender/out/qc"})
#   → 逐张 md5 + coverage_estimate；主体占画面 <5% 会给 subject_too_small 警告
```

**只用其中一条都不够**：数值抓得出"前裙甲上翘 6°"，但看不出"焊缝难看"；画面看得出"像不像"，但看不出 3mm 的偏差。

## 4. 六条必踩的坑

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

### 坑二：拼件接缝——必须互相插入 5–15mm（★ 全场最高价值的一条）

多部件拼装（剑 = 刃 + 护手 + 柄 + 柄头；坦克 = 车体 + 炮塔 + 悬挂 + 裙板）时，**面贴面**即使数值上接触，渲染也会有可见的缝；不同形状之间（圆柱柄插进方块护手）更明显。

两条一起用。

**① 在接头处深度交叠 5–15mm**（藏在较大件内部，外部看不到接缝）：

```python
# 柄的顶端伸进护手 1.5cm，底端伸进柄头 1cm
GRIP_OVERLAP_INTO_GUARD = 0.015
GRIP_OVERLAP_INTO_POMMEL = 0.010
grip_total_len = GRIP_VISIBLE_LEN + GRIP_OVERLAP_INTO_GUARD + GRIP_OVERLAP_INTO_POMMEL
```

错误（必然见缝）与正确（把缝藏进几何里）：

```python
pommel_z = -GRIP_LEN/2 - POMMEL_R + 0.010   # ✅ 上推 1cm 进柄
guard_z  =  GRIP_LEN/2 + GUARD_H/2 - 0.015  # ✅ 下压 1.5cm 包住柄顶
```

**② 圆润件开平滑着色**：`bpy.ops.object.shade_smooth()`。平面着色的圆柱能看出每一段棱面；方块类硬表面保持平面着色（Blender 5.x 已移除 Mesh.use_auto_smooth，用修改器式平滑或逐面标记）。

> 实测（500 对象 / 7 agent）：**装配质量几乎全靠这一条**。悬挂臂穿模、驱动轮盖悬空、挡泥板浮空 193mm —— 全都是围绕"嵌入 5–15mm"这条硬规则抓出来并收敛的。
> 并行建模时把它写进**团队协议 + 接口表**（§0.5.3），并让审计用 `audit_measure` 的逐轴重叠当判据。

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
2. 装配层**统一兜底**一次 recalc（Recipe 8 的那一行），别指望每个 builder 都记得。
3. 判据写在报告里时要带**法线前提**："在法线朝外的前提下，X 深入 Y 12mm"。
4. 双向确认：同一对件用 `audit_interference` 与 `audit_measure` 各跑一遍，结论相反就先查法线。

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

## 5. 修改器栈序与症状表

**背下来**：

```
Mirror → Array → Solidify → Bevel → Subdivision Surface →（需要时 Boolean）
```

顺序错 = 伪影。最常见的就是 SubSurf 放到 Bevel 前面。
补充规则：布尔保持 live 到导出前；拓扑定型再 apply 镜像；**不要叠多层 subsurf 而没有支撑边**；应用缩放（Apply Scale）之后再布尔 / 导出。

| 症状 | 修法 |
|---|---|
| 一看就是默认方块 | 加 Bevel（0.02m / 3 段）+ SubSurf（**仅限光滑件**） |
| **装甲棱边被圆掉、像塑料玩具** | 这是分面件误用了 SubSurf → 删掉 SubSurf，改走配方 2a（小倒角 + 平坦着色） |
| 圆形上出现夹点 | Bevel 要在 SubSurf **之前** |
| 渲染出黑面 | 重算法线（坑五）；先查有符号体积判断是不是整体朝内 |
| 布尔产生 n-gon | apply 布尔后进编辑模式改回四边面，再 SubSurf |
| 对称破掉 | 用 Mirror 修改器，不要复制再翻转 |
| 网格里有看不见的内部面 | Clean Up → Delete Loose |
| 细分后发皱 / 塌陷 | 缺支撑边：加环切固定结构，别继续加细分层级 |
| 阵列首尾对不上（履带 / 链节） | Array 做不到精确闭合 → 配方 6b 参数化路径 |
| headless 里 ops 报 context/poll 错 | 坑四：改 bmesh / data API，或用 temp_override |

## 6. 数值门清单（交付 / 交接前逐条过）

> v1 这一节只有"清理清单"，实测证明**肉眼验收不可靠** —— 这里给每条判据配上可跑的判据与工具。
> 单件交付跑 ①–④，装配完成后跑 ①–⑧。

| # | 门 | 判据 | 怎么跑 |
|---|---|---|---|
| ① | 重复点 / 松散几何 | 0 孤立点、0 松散边 | `audit_mesh`（+ remove_doubles） |
| ② | 流形 | 0 非流形边（有意开口除外） | `audit_mesh` / `audit_scene` |
| ③ | 开边 | 0 开边（闭合件口径） | `audit_mesh` |
| ④ | 法线 | 一致朝外（闭合件有符号体积 > 0） | 坑五的 normal_health |
| ⑤ | 尺寸 | 关键 bbox 与 `spec.py` 偏差 ≤ 公差（军模级 ±3mm） | `audit_measure` |
| ⑥ | 浮空 | 0 悬空件（可见浮块 = 最长 bbox 边 ≥ 全模型 1%） | `audit_connectivity` / `audit_snap_floaters` |
| ⑦ | 穿模 | 无异常互穿；允许的深嵌白名单化 | `audit_interference` / `audit_overlap` |
| ⑧ | 嵌入 | 拼件互相插入 **5–15mm**（坑二） | `audit_measure` 逐轴重叠 |
| ⑨ | 形体 | 固定机位对照图 + 数值 bbox（两条都要） | `qc_render_views` + `audit_measure` |
| ⑩ | 面数 | 对三角面预算 | `audit_scene` |

**MUST**：非破坏到导出阶段；对象一建就命名（GEO- 前缀）；blockout 与成品分离；布尔 / 导出前应用变换；
**多 agent 时先冻结 spec.py 与接口表**；每个件交付带数值证据。
**MUST NOT**：轮廓没认可就删 blockout；过早 apply 所有修改器；留默认名；没有比例参照就开始建模；跳过清理；
**用单张成品图验收形体**；**在法线未验证的前提下下穿模结论**。

## 7. 深水参考

需要超出配方的精度时读 `references/modeling-overview.md`（英文原文，含：bpy.ops.mesh 与 bmesh 两套 API 的选择规则、8 种基本体的专业选择、编辑模式工具箱逐条说明、bmesh 精度 API、硬表面 bevel-weight 打法、布尔工具箱、关键修改器、完整 bpy.ops.mesh 速查，以及作者自己标注的未完成项）。

什么时候去读：配方不匹配（需要 bmesh 级精度或自定义操作）；拓扑要求比平时严（动画可用、游戏 LOD）；性能敏感（foreach_set、批量操作）；要用到配方里没列的算子。

## 8. 来源与许可

本技能由以下两个 MIT 许可项目的**建模流程部分**融合改写而成，工具调用一律换成本机 `blender_rt_*` 直连链：

- **RobLe3/cc-blender-skill**（`blender-modeling` 及其 `references/overview.md`）——决策树、配方、坑表、深水参考
- **arjun988/blender-skills**（`blender-modeler`）——六步总纲、集合结构、修改器规则、清理清单、MUST / MUST NOT

完整归属与许可见同目录 `NOTICE.md`。上游原版面向 MCP（mcp__blender__*），本版已改写为本机直连工具链。

## 9. 本次更新对照（v2 · 2026-09-25）

来源：一次 500 对象 / 7 agent 的军模项目复盘（skill 命中率约 1/3，失效的全是"载体不匹配"）。

| 反馈 | 落在哪 |
|---|---|
| 真正救场的只有"拼件互相插入 5–15mm" | 坑二升级为"全场最高价值"，并写进 §0.5.3 接口表 |
| skill 缺"多 Agent 并行建模的分工范式" | **新增 §0.5**（冻结规格 / 写作用域 / 接口表 / 深嵌+复检 / 数值门 / 反模式） |
| skill 缺"参考图驱动的形体还原" | **新增 §0.6**（三步标定 / 双证据验收 / 读图三分法） |
| skill 缺"headless 下慎用 bpy.ops" | **新增坑四** + 配方 3b（bmesh 等价写法）+ 配方 2a/8 的替代写法 |
| 形体不能靠单张成品图验收 | **新增配方 10** + §6 门⑨ |
| 配方 2（Bevel+SubSurf）完全没用（分面装甲被圆掉） | **拆成 2a 分面 / 2b 光滑**，决策树同步修正 |
| 配方 3（编辑模式 ops）没用（headless context 脆） | 配方 3 保留给 GUI，**新增 3b bmesh 版** |
| 配方 6（Array 沿曲线）没用（做不到精确节距闭合） | 保留装饰性用途，**新增 6b 参数化路径**（83 节 / 0.176 节距） |
| 第 6 节清理清单被扩展成自动门 | 升级为 **§6 数值门清单**（10 条判据 + 工具） |
| 预览图"白对白"、panel 级没法 QC（组员自己改灰材质+单侧强太阳） | **新增坑六**（预览装置的默认光照 / 材质 / 机位纪律） |
| 法线朝内导致穿模判据整体反号（误报深入 150mm） | **新增坑五**（判据前先验法线 + 装配层统一 recalc） |
| 插件侧：headless/job 回执"收不回来"、agent 点不亮 GUI | 插件 v0.9.4 修返回通道 + 加 `blender_viewport(op="launch")`（见插件 `CHANGELOG.md`） |



