
# 上游整合评估：mcp-for-blender(ahujasid) 与 blend-ai(HoldMyBeer-gg) 里有哪些能进本插件

> 评估时间：**2026-09-26** ｜ 评估对象：`dsh-blender-plugin` v0.9.5（本机 GUI 通道，Blender **5.2.2 LTS**）
> 方法：读两份上游源码 + **在本机真 Blender 上实测**（GUI 通道 rt_do / 独立无头进程 headless）。
> 本文所有"实测"字样都带原始回执，可复现；未经实测的都标注为"读码推断"。

---

## 0. 落地状态（v0.9.6，2026-09-26 —— 本文的 P0/P1 已实现）

| 本文条目 | 落地 | 实测证据 |
|---|---|---|
| §2 雕刻栈（L1 拓扑 + L2 位移笔刷 + L3 遮罩/面组） | `runtime/sculpt.py`（8 op） | 体素重构 1986→12148 顶点 0.084 s；单笔 1986 顶点 / 257 affected / 6 ms；`sculpt_selftest` 绿 |
| §3 addon 升级 v1.6 → 2.x | **已做**（addon v1.6/protocol 5 → v1.7/protocol 11） | `get_addon_info` 回 [1,7]/protocol 11；`rt_commands` 可用命令 15→19；`describe_node_type`/`bpy_api_lookup`/`export_scene` 三条现场跑通；v1.6 备份 md5 对拍并存两份 |
| §4 网格修复闭环 | `runtime/mesh_fix.py`（repair / decimate） | `fix_selftest` 绿；E2E：10 顶点 2 loose → 7 顶点 0 loose、closed |
| §5 UV 四件套 | `runtime/uv_tools.py`（6 op） | `uv_selftest` 绿；smart project 后零面积 UV 面 = 0 |
| §5 制造检查 | `runtime/printcheck.py`（4 op） | 薄板 5mm ±0.5 检出；粗网格给 `resolution_mm` + "结论不可信"警告 |
| §5 沿路径扫掠 | `runtime/sweep.py`（4 op） | 直管 38 顶点 / 48 面；过紧路径被拒并指认第几点 |
| §5 参数纪律（未知参数 / NaN 门） | 五个模块 dispatch 均做未知参数检查；NaN/Inf 由 `_num` 统一拒绝 | 测试断言"参数名拼错要报错" |
| 顺带修掉 | `audit.py` 空网格 IndexError（回归测试已固定） | `empty_objects` 计入 `clean=false` |

其余（渲染状态跟踪 / 物理烘焙 / 材质图生成 / Ollama / GPencil）**仍在候选清单**，未动。
CHANGELOG 见 `CHANGELOG.md` 的 v0.9.6 条目；skill 见 `blender-modeling` 的 Recipe 11–14；
回归测试见 `tests/upstream_integration_selftest.py`（23 断言，无头可跑）。

---

## 0. 结论速览

| # | 候选件 | 来源 | 本插件现状 | 整合通道 | 优先级 | 工作量 |
|---|---|---|---|---|---|---|
| 1 | **雕刻栈**（拓扑 + 位移笔刷 + 遮罩/面组） | blend-ai sculpting（8 工具，无笔触） | **0 覆盖**（sculpt/remesh/multires/dyntopo 全库 0 命中） | 新增 `runtime/sculpt.py` + `/plan` op | **P0** | 1 天 |
| 2 | **addon 升级 v1.6 → 2.x**（多出 `describe_node_type` / `bpy_api_lookup` / `export_scene`） | mcp-for-blender 上游 addon.py | 本机 addon 实测 **v1.6 / protocol 5**，无这三条 | 换 addon 文件（不改插件） | **P0** | 1 小时 |
| 3 | **网格修复闭环** `repair_mesh` / `decimate` | blend-ai mesh_quality | 只有 `audit_mesh` **诊断**，无修复 | `runtime/audit.py` 增 op | **P0** | 半天 |
| 4 | **UV 四件套**（smart project / unwrap SLIM / projection / pack） | blend-ai uv | **0 覆盖** | `runtime/uv.py` 新模块 | P1 | 半天 |
| 5 | **制造检查**（薄壁 / 悬垂 / 自交 / 壳体） | blend-ai print3d | 有干涉/连通，无壁厚/悬垂 | `runtime/audit.py` 增 op | P1 | 半天 |
| 6 | **沿路径扫掠 + 路径弯折检查** | blend-ai sweep | **0 覆盖**（管路/线缆/轨道需要） | `runtime/sweep.py` | P1 | 半天 |
| 7 | 参数纪律三件（未知参数拒绝 / 枚举进 schema / NaN-Inf 门） | blend-ai strict+enum_hints+addon server | 无 | engine + addon-protocol | P2 | 2 小时 |
| 8 | 渲染状态跟踪 + 崩溃恢复 | blend-ai render_guard(50 行) | 有 `render_lock`（跨进程锁），无"渲染中排队/崩溃恢复" | `runtime/` | P2 | 2 小时 |
| 9 | 物理/粒子/烘焙 | blend-ai physics(9) | **0 覆盖** | `runtime/physics.py` | P2 | 1 天 |
| 10 | 材质节点图一次成型 | blend-ai materials(25) | 只有 `presets`（套参数） | `runtime/presets.py` 扩展 | P3 | 1 天 |
| 11 | 专家 prompt 内容（12 条） | blend-ai prompts | skill 未覆盖 | 写进 `dsh-skill-blender-modeling` | P3 | 半天 |
| 12 | 本地模型直驱（Ollama） | blend-ai ollama_chat.py(24.6KB) | 无 | 独立脚本 | P3 | 视需要 |

**一句话**：真正值得动手的是 **1、2、3**；4–6 是明确的空白面；7–9 是便宜的健壮性；10–12 是锦上添花。
**不要**做的：把 186 个工具照搬（schema 税 ≈17.5k tokens，且与本插件"少量强工具 + `/plan` 通道"的哲学冲突）。

---

## 1. 本插件现状盘点（作为缺口判据的基线）

`runtime/` 里的 Python 能力（实测 def 清单）：

| 模块 | 行数 | 能力 | 覆盖缺口 |
|---|---|---|---|
| audit.py | 2667 | mesh/scene/duplicates/connectivity/gate/drift/measure/snap_floaters/overlap/interference/purge_orphans | **只诊断不修复**；无壁厚/悬垂 |
| qc.py | 1126 | IoU/轮廓/剖面差、多视角渲染、鲁棒性扫 | — |
| contract.py | 1768 | 假设/区间/证据/三态判定/destructive_guard | — |
| motion.py | 2683 | 关节轴/锚点实测、URDF/USDA | 无动力学 |
| deliver.py | 953 | OBJ/MTL 归一化 + md5 清单 | 无 GLB/FBX |
| generator.py | 558 | 代码化建模 + 编译门 | — |
| perf.py / presets.py / montage.py / planner.py | — | 渲染性能/配方/拼图/规划 | 无着色器图生成 |
| txn.py | 555 | 快照/回退/编辑纪律 | — |
| view.py | 680 | 视口捕获、`gui_frame/``gui_shading/``gui_open`（**真 UI 上下文 temp_override 模式**） | — |

关键词检索（`runtime/` 全库）：`sculpt` / `remesh` / `multires` / `dyntopo` / `voxel` / `brush` / `face_set` / `unwrap` / `smart_project` = **0 命中**；`thin wall` / `overhang` = 0；`mask` 的 131 处全是八叉树/干涉用途的布尔掩码，与雕刻遮罩无关。

> 顺带：`dsh-skill-blender-modeling/SKILL.md:324` 目前写着"需要'笔触式雕刻'时说明：那是手势输入，不适合文本驱动"——本评估的第 3 节把这句话升级成了可执行的结论。

---

## 2. P0：真 Blender 上实测出来的雕刻能力边界（重点）

### 2.1 blend-ai 的雕刻是什么

上游 8 个工具（`addon/handlers/sculpting.py` 336 行 + `tools/sculpting.py` 234 行）：
进入/退出雕刻模式、切笔刷（`wm.tool_set_by_id("builtin_brush.X")`）、笔刷 size/strength/stroke_method、`remesh`（VOXEL 走 `voxel_remesh`，SHARP/SMOOTH/BLOCKS 走 REMESH 修改器）、`add_multires_modifier`（CATMULL_CLARK 逐级）、对称轴、dyntopo。

**它唯一值得抄的"工程件"是体素预算守卫**（`handlers/sculpting.py:137` `MAX_VOXEL_GRID_CELLS = 40_000_000`）：先用 `obj.dimensions` 估算 `Π(dim_i/voxel)`，超限就**报错并给出可用尺寸**，而不是让 Blender 卡死到超时。
**它明确做不到的**（README 自述）："Sculpt strokes cannot be simulated"。

### 2.2 实测：Blender 5.2.2 上什么能脚本化、什么不能

GUI 通道（`rt_do`，`temp_override(window, screen, area=VIEW_3D, region=WINDOW)`）与独立无头进程各测一轮，回执如下：

| 能力 | API | GUI 通道 | 无头 -b | 判据 |
|---|---|---|---|---|
| 进入雕刻模式 | `object.mode_set(mode='SCULPT')` | ✅ | ✅ | 两次实测均返回 SCULPT |
| 活动笔刷 | `tool_settings.sculpt.brush` | ✅（默认 "Draw"） | ✅ | 无头也拿得到笔刷对象 |
| 笔刷参数/对称 | `brush.size/.strength`、`sculpt.use_symmetry_x` | ✅ | ✅ | 实测 [80, 0.7] / True |
| dyntopo 开关 | `sculpt.dynamic_topology_toggle()` | ✅ FINISHED | ✅ FINISHED | 两种模式都行 |
| 体素重构 | `data.remesh_voxel_size` + `object.voxel_remesh()` | ✅ 1986→12148 顶点，**0.084 s** | ✅ 1986→7832 | 可用且快 |
| REMESH 修改器（SHARP/SMOOTH/BLOCKS） | `modifiers.new(type='REMESH')` + apply | 读码推断 ✅（上游同法） | — | 未单独实测 |
| 多分辨率 | `multires_subdivide(mode='CATMULL_CLARK')` | 未测 | ✅ | 无头实测返回 [Multires, sculpt_levels=1]，需按 levels 循环 |
| 细分（非 multires） | `object.subdivision_set(level=n)` | ✅ 加了 SUBSURF | — | 进雕刻前垫底模可用 |
| **网格滤镜笔刷** | `sculpt.mesh_filter(type='SMOOTH', strength=0.3)` | ✅ **FINISHED** | 未测 | 这是"无需手势的形变"主力 |
| 遮罩：腔体 | `sculpt.mask_from_cavity()` | ✅ FINISHED | — | — |
| 遮罩：初始化 | `sculpt.mask_init(mode='RANDOM_PER_VERTEX'\|'RANDOM_PER_FACE_SET'\|…)` | 枚举已实测（我传 RANDOM 被拒） | — | 参数名是 mode |
| 遮罩：洪水填充 | `**paint**.mask_flood_fill`（不在 sculpt 命名空间） | 未测（命名空间已纠正） | — | 上游/文档常写错 |
| **遮罩直接写** | 顶点属性 `".sculpt_mask"` | 属性存在已实测（`attr_names` 里可见） | 未测 | 写入走常规 foreach_set，未单独验证；比算子可控 |
| 面组 | `face_sets_init(mode='LOOSE_PARTS')` / `face_sets_create(mode='VISIBLE')` | ✅ FINISHED | — | 分件隔离雕刻 |
| **真笔触** | `sculpt.brush_stroke(stroke=[…])` | ❌ **6 种参数格式全部失败** | ❌ poll() 直接 False | 见下 |

**真笔触的结论（这是本次最有价值的一条）**：
- **上下文问题已经解决**：用插件现成的 `view.py::_find_view3d()` + `temp_override` 后，`brush_stroke.poll()` 实测为 **True**，`context.space_data.type == "VIEW_3D"`。
- **真正的拦路虎是 RNA 参数转换**：Blender 5.2 上 `stroke` 是 `COLLECTION/OperatorStrokeElement`，传 dict（列表/元组 location、全字段/最小字段、带 name 不带 name，共 6 种）一律报
  `TypeError: … stroke error converting a member of a collection from a dicts into an RNA collection`；
  而 `bpy.types.OperatorStrokeElement()` 与 `__new__` 实例化也失败（`bpy_struct.__new__(struct): expected a single argument`）。
- 也就是说：**不是"手势输入不适合文本驱动"，而是当前 Blender 版本在 API 层把这条路堵了**。建议在 skill 里照实写，并挂一个"待 Blender 修复/降版本再评估"的观察项。
- 反过来说：**这条路走不通，恰恰意味着 L2（自研位移笔刷）是差异化**——上游两家都做不到程序化雕刻（blend-ai 自述不能，mcp-for-blender 根本没有雕刻）。

### 2.3 建议的 DSH 雕刻栈（四层，全部本机实测过）

**L1 拓扑层**（无头可用 → 可进 `rt_job` 批处理）
`sculpt_remesh(object, mode, voxel_size="auto")` —— 抄"预算守卫"的**思路**自己实现（3 行数学，非版权内容）：
`cells = Π(max(dim_i,1e-6)/voxel)；cells > 4e7 → 报错并给可用尺寸`。VOXEL 走 `data.remesh_voxel_size` + `voxel_remesh()`，其余走 REMESH 修改器再 apply。
`sculpt_multires(object, levels)` / `sculpt_subdiv(object, level)` / `sculpt_dyntopo(object, detail_size, mode)`。

**L2 形变层（自研，差异化核心）**
- **numpy 位移笔刷**：读 `foreach_get("co")` → 计算 falloff（球/管/方框）→ 沿法线/指定方向位移 → `foreach_set` + `mesh.update()`。
  实测成本：1986 顶点、单笔 257 顶点受影响、**6 ms**；笔触路径 = 若干控制点线性/样条插值成 N 个 dab，天然支持对称（对 x/z 镜像再算一遍）与遮罩乘子。
  建议先做 6 支：`draw / inflate / pinch / flatten / smooth(邻接平均) / crease`；这 6 支足以覆盖"大体块 → 局部特征"的文本驱动需求。
- **`sculpt.mesh_filter`**：拿来做整块平滑/膨胀/松弛（实测 FINISHED），配合 L2 的 dab 就是"局部 + 全局"两级。
- 笔刷命中判据：以顶点法线为方向，位移量 = `strength * falloff(d) * brush_radius`；一切参数化 → 可复现、可 diff、可写进 `generator_*`（程序即形状）。

**L3 遮罩/面组层**
- 遮罩：直接写 `".sculpt_mask"` 属性（实测存在）；或 `mask_from_cavity` / `mask_init`；洪水填充记得用 `paint.mask_flood_fill`。
- 面组：`face_sets_init(mode='LOOSE_PARTS')` 把拼件自动分组（对"每个分件单独细化"极有用）。
- 语义：遮罩 = "哪些顶点允许被笔刷改" → 正好和插件的 `destructive_guard`（未判别连接不许布尔/焊接）形成同一套纪律：**先限定作用域，再动几何**。

**L4 观测/验收（插件已有，直接复用）**
- 雕刻前：`audit_mesh` 快照（顶点/面、nonmanifold、degenerate、loose）
- 雕刻后：`audit_drift`（对称 Chamfer 形状漂移）看"改了多少"；`qc_render_views` + `qc_compare` 看"像不像参考图"
- 高风险前：`rt_txn snapshot` 留文件级回退点（对象级 `mark/revert` **不含拓扑改动**，雕刻后回不去，必须用文件级）

### 2.4 建议新增的 op（挂在 `blender_rt_plan` 下，与 `audit_*` 同级）

| op | 读/写 | 作用 | 验收判据 |
|---|---|---|---|
| `sculpt_scan` | 只读 | 报告可雕刻性：模式、brush、dimensions、体素预算、遮罩/面组统计、multires 层级 | 无副作用；可加进 `READ_ONLY_OPS` |
| `sculpt_setup` | 写 | 进雕刻模式 + 拓扑准备（remesh/multires/dyntopo）+ 对称/遮罩设定 | 返回前后顶点数、模式、参数 |
| `sculpt_apply` | 写 | 位移笔刷批处理（`strokes=[{brush, points, radius, strength, falloff, axis}]`） | 返回受影响顶点数、最大位移、耗时；与 `audit_mesh` 前后对比 |
| `sculpt_filter` | 写 | `mesh_filter`（smooth/inflate/relax） | 同上 |
| `sculpt_remesh` | 写 | 体素/修改器重构（带预算守卫） | 顶点数变化 + 耗时 + closed 判据 |
| `sculpt_help` | 只读 | 速查 + 能力边界（含"笔触不可用"的实测结论） | — |

实现落点：`runtime/sculpt.py`（沿用 `preloadChunk` 独立命名空间套路，暴露 `K.dsh_sculpt_api`）、`server.mjs` 路由与 `READ_ONLY_OPS`、`engine.mjs` 工具描述。
**注意**：`sculpt_apply/remesh/setup` 会改几何 → **不算只读**，要过写租约；`sculpt_scan/help` 建议列入只读白名单。

---

## 3. P0：addon 升级（一次升级，白拿三条命令）

实测本机 addon 回执：
`{"name":"MCP for Blender","addon_version":[1,6],"protocol_version":5,"capabilities":[…9 条…],"blender_version":"5.2.2 LTS"}`
`blender_rt_commands` 可用 15 条、被集成开关挡住 15 条（polyhaven/hyper3d/sketchfab/polypizza/hunyuan3d 全 off）。

上游 2.1.0 的 `addon.py` 基线 handler dict 里比 1.6 多出：

| 新增命令 | 价值 | 插件侧立刻可用方式 |
|---|---|---|
| `describe_node_type` | 查节点类型的所有 socket（名字/顺序/类型/默认值） | `rt_cmd` 直接用；正好补插件"契约层"缺的"查 API 不猜" |
| `bpy_api_lookup` | 查 bpy API 参考 | 同上，减少模型臆造属性名 |
| `export_scene` | 导出 GLB/FBX（全场景/选择/名单） | 补 `deliver_export`（只有 OBJ/MTL） |

**升级纪律**（呼应 AGENTS.md"addon 是共享底座"）：
1. 先 `blender_rt_cmd get_addon_info` 记录当前版本；2. `blender_viewport op=doctor` 跑通；3. 升级后再 doctor + 抽查 `get_world_state_snapshot`；4. 备好 1.6 的 zip 以便回退。
风险：协议 `protocol_version` 若变化，`addon-protocol.mjs` 的适配层（`addonProtocol=auto`）需核对；命令面是**增量**（1.6 的 9 条在 2.x 仍在），预期平滑。

---

## 4. P0：网格修复闭环（audit 已诊断，缺"治"）

现状：`audit_mesh` 输出 `boundary_edges / nonmanifold_edges / degenerate_faces / loose_verts / self_intersections / normals_inverted`（实测字段名），**纯只读**。
blend-ai 的 `analyze_mesh_quality / repair_mesh / decimate_mesh` 提供了后半段。建议新增:

- `repair_mesh(objects, actions=[...])`：`remove_doubles(merge_threshold)` / `dissolve_degenerate` / `delete_loose` / `recalc_normals` / 可选 `fill_holes`（按边界环数上限）。
- `decimate_mesh(object, ratio|target_tris)`：加 DECIMATE 修改器并 apply，返回前后三角数。
- **门控**：修复属破坏性操作 → 走 `contract.destructive_guard` 的同一套纪律（未判别连接先拒），并在回执里带 `audit_mesh` 前后对比（nonmanifold 归零才算过）。

---

## 5. P1/P2 其它候选（要点式）

- **UV 四件套**（blend-ai `uv`：smart_uv_project / uv_unwrap(ANGLE_BASED|CONFORMAL|**SLIM**) / set_uv_projection / pack_uv_islands）：插件 0 覆盖，而 `deliver_export` 要出带贴图的 OBJ/MTL 就必须有 UV。
- **制造检查**（blend-ai `print3d`：薄壁、悬垂、自交、壳体，包装 3D Print Toolbox）：与现有 `audit_interference/connectivity` 互补——尤其是 3D 打印口径的"薄壁/悬垂"，目前完全空白。
- **沿路径扫掠**（blend-ai `sweep`：`sweep_profile_along_path` + `analyze_sweep_path`）：管路/线缆/轨道/护栏这类"沿路径"件现在只能靠 rt_do 手搓；它的"先算路径最小弯折半径是否够"是很好的先算后建。
- **参数纪律三件**：`strict.forbid_unknown_parameters`（35 行，把拼错的参数名变成错误而不是静默忽略）、`enum_hints`（把枚举值写进 JSON Schema，模型少猜）、**NaN/Inf 门**（上游 5.2 的 addon server 有 `_reject_non_finite`，本机 addon 1.6 无 → 可在插件侧对 `rt_do`/`/plan` 参数做一次性校验，防写坏 .blend）。
- **render_guard**（50 行）：渲染中"排队 + 返回 busy"而不是硬超时；`load_post` 从崩溃渲染恢复。插件已有 `render_lock`（跨进程文件锁），缺的是"渲染状态跟踪 + 崩溃恢复"，二者叠加才完整。
- **物理/粒子/烘焙**（blend-ai 9 工具）：刚体/布料/流体/粒子 + bake，插件 0 覆盖；对"机构运动"可与 `motion_*`（几何法测关节）形成"几何 + 动力学"两条证据。
- **材质节点图一次成型**（blend-ai materials 25 工具的 `create_procedural_material`）：插件 `presets` 只套已有参数的材质；若要"一句话生成程序化材质"，需要一个节点图构造器（坐标/映射/噪声/色带/BSDF 连好）。
- **12 条专家 prompt**（拓扑、真实尺度、布光、棚拍、角色 basemesh、PBR、**auto-critique 自检回路**）：zero-code，直接写进 `dsh-skill-blender-modeling` 与 `references/`，是性价比最高的一项。
- **Ollama 本地直驱**（`ollama_chat.py` 24.6KB + `launch_ollama.sh`）：离线场景可用本地模型驱动 Blender；与本插件"本地优先"的取向一致，但优先级看需求。

---

## 6. 不建议整合

| 项 | 理由 |
|---|---|
| 186 个 MCP 工具整体照搬 | schema 固定税 ≈17.5k tokens（与 mcp-for-blender 实测 6.9k/36 工具同一算法外推）；且大部分原语（transforms/objects/collections 的基础操作）在 `rt_do`/headless 里已经能表达 |
| 遥测 / premium / 云端 3D 生成 | 与"本地、可审计、写租约"的取向冲突；本机 5 个集成开关本就全 off |
| 直接复制 blend-ai 的代码 | **AGPL-3.0-or-later**（且其 addon manifest 又写 GPL-3.0-or-later，自身不一致）→ 只借"思想 + 参数表 + 判据"，代码自己写；mcp-for-blender 是 **MIT**，参考/复用需注明来源 |
| 把雕刻做成 186 工具那样的细粒度 API | 与插件"少量强工具 + `/plan` 命名空间"的架构冲突；雕刻应做成 6 个 op，不是 60 个 |

---

## 7. 建议实施顺序（含验收，全部可回退）

| 步 | 内容 | 验收证据 | 估时 |
|---|---|---|---|
| 1 | 雕刻 L1+L2（`runtime/sculpt.py` + 6 op + skill 章节改写） | 配方跑通：球体 → 体素重构 → 3 笔位移 → mesh_filter → `qc_render_views` 与参考图 IoU；`audit_mesh` 前后报告 | 1 天 |
| 2 | `repair_mesh` / `decimate_mesh` | 构造非流形样例 → 修复后 nonmanifold=0、degenerate=0，且 `audit_gate` 通过 | 半天 |
| 3 | addon 1.6 → 2.x | `rt_cmd bpy_api_lookup` 有返回、`export_scene` 出 GLB、`doctor` ok；1.6 zip 备好 | 1 小时 |
| 4 | UV 四件套 + sweep | 交付包带 UV 且贴图对得上；管路件输出最小弯折半径报告 | 1 天 |
| 5 | 纪律件（strict / enum hints / NaN 门 / render_guard 语义） | 拼错参数名报错而非静默；NaN 入参被拒；渲染中调用返回 busy 而非超时 | 半天 |

---

## 8. 附录：本次实测回执（可复现）

**A. 本机 addon 版本**
`blender_rt_cmd(name="get_addon_info")` →
`{"name":"MCP for Blender","addon_version":[1,6],"protocol_version":5,"capabilities":["drain_human_activity","execute_code","get_addon_info","get_object_info","get_scene_info","get_telemetry_consent","get_viewport_screenshot","get_world_state_snapshot","set_telemetry_consent"],"blender_version":"5.2.2 LTS"}`

**B. 无头进程（`blender_rt_headless`，factory-startup，engine=none，2.7 s）**
`{"blender":"5.2.2 LTS","background":true,"ctx_window":true,"ctx_area":false,"ctx_region":false,
 "enter_sculpt":"SCULPT","sculpt_object_ok":true,"active_brush":"Draw",
 "brush_stroke_params":["stroke","mode","brush_toggle","pen_flip","override_location","ignore_background_click"],
 "brush_stroke_poll":false,"stroke_result":"ERR RuntimeError: …poll() failed, context is incorrect",
 "dyntopo_toggle":"{'FINISHED'}","voxel_remesh":["{'FINISHED'}",7832],"multires_subdivide":["Multires",1,1]}`
→ 结论：拓扑/参数类无头可用；**笔触在无头没有 UI 上下文**。

**C. GUI 通道（`rt_do`，临时对象 + 全状态保存/恢复，实测 `scene_intact:true`）**
`{"view3d_found":true,"poll_with_ctx":true,"space_type":"VIEW_3D","ctx":{"area":"VIEW_3D","region":"WINDOW","sculpt_object":"__dsh_probe3"},
 "stroke":"ERR TypeError: …stroke error converting a member of a collection from a dicts into an RNA collection",
 "instantiate":"ERR TypeError: bpy_struct.__new__(struct): expected a single argument",
 "mesh_filter":"{'FINISHED'}","mask_from_cavity":"{'FINISHED'}","face_sets_init":"{'FINISHED'}","face_sets_create":"{'FINISHED'}",
 "subdivision_set":"{'FINISHED'}","attr_names":["sharp_face","position",".edge_verts",".corner_vert",".corner_edge",".sculpt_mask"],
 "numpy_draw":[0.006,0.12,257],"voxel_remesh_gui":["{'FINISHED'}",1986,12148,0.084]}`
→ 结论：上下文不是问题（poll=True）；**笔触被 RNA 参数转换挡住**；而 numpy 位移（6 ms）+ mesh_filter + 体素重构（84 ms）完全可用。

**D. 源码出处**
- blend-ai：`addon/handlers/sculpting.py:137`（体素预算）、`src/blend_ai/tools/sculpting.py`（参数校验与枚举）、`README.md` Limitations（"Sculpt strokes cannot be simulated"）、`addon/render_guard.py`、`src/blend_ai/strict.py`、`enum_hints.py`、`validators.py`
- mcp-for-blender：`addon.py`（handler dict：`describe_node_type` / `bpy_api_lookup` / `export_scene`）、`src/blender_mcp/safe_mode.py`（AST allowlist，opt-in）

**E. 复现命令**
`bash
# 无头能力探针（零风险，独立进程）
blender_rt_headless(script=<B 段脚本>, engine="none")
# GUI 能力探针（临时对象 + finally 清理 + 前后对象数校验）
blender_rt_do(code=<C 段脚本>, see=false)
`
