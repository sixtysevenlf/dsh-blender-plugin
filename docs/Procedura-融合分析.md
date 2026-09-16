# Procedura → dsh-blender-plugin 融合分析

> 对象：[SpatiaOS/Procedura](https://github.com/SpatiaOS/Procedura)（MIT，Agentic 3D Modeling with Procedural Control）
> 分析日期：2026-09-17 · 依据：仓库 raw 源码（已在本地镜像 `.procedura-ref/`）+ 现插件 `runtime/*.py`、`src/index.ts` 实盘核对
> 结论先行：**不要搬它的代码栈（TS + OpenSCAD + Isaac），只搬"判据与算法"。** 现插件强在「单对象网格体检 + 像素级 QC + 契约/证据/事务」，Procedura 强在「多对象装配级判据 + 数值化改动度量 + 判定随几何失效」——融合正好补后半段。

## 落地状态（v0.9.0，2026-09-17）

| # | 能力 | 落点 | 实测 |
|---|---|---|---|
| — | 接通 `audit_*` / `montage` 路由（**发现：文档里的 audit op 从来没接通**） | engine.mjs + server.mjs | `audit_help` 现在真的能回 |
| 1/2 | 连通门 + 浮块归因 + 网格级复核 | audit.py `audit_connectivity` | cockpit：0.3mm 口径 → 178 浮块 / 970 相接；2mm → 63 |
| 3 | 微隙分级 + snap（只整件平移） | audit.py `audit_snap_floaters` | 自检 dry_run 通过；bbox 读 0 时自动走网格级 |
| 4 | 形状漂移度量（BVHTree 点到曲面） | audit.py `audit_drift` | 20k 采样 21,397 ms → **46 ms**；同网格读 0.000000 |
| 5 | 渲染设备行 | qc_render.py | jsonl 每行实测 device（EEVEE→GPU + 型号串） |
| 6 | 逐部件配色渲染 | qc_render.py `mode="parts_color"` | 图例 5 行 == 5 部件；色调命中 [1658,418,420,460,496] |
| 7/8 | 证据指纹失效 + 判定漂移降级 | contract.py（v3） | 契约自检 24 → 33 项；改源 → stale=true |
| 9 | 测量包 | audit.py `audit_measure` | bbox/逐轴间隙/邻居（契约图 + 空间 K） |
| 10 | 配合门 + 特征库 | contract.py `mate_check` / `fit_help` + assembly_features.json | 单边中位 0.1498mm（设计 0.1500）→ supported；mate 自检 57 项 |
| 11 | 干涉升级 | contract.py `interference_report` | 深 4.0mm/体积 400.0002mm³；计划内接触豁免 |
| 12 | 待定编辑事务 | txn.py（v3） | accept 需 verified + 复核；revert 免预算 + 防抖；txn 自检 55 项 |
| 13 | 交付导出 | deliver.py（新） | 单位盒 + 多组 OBJ/MTL + manifest；改一字节 → FAIL；自检 53 项 |
| 14 | 视角目录 + 别名 | qc_render.py `qc_render_catalog` | 22 视角 / 4 组；未知名不再静默当 iso |
| 15 | 轨迹 JSONL | engine.mjs | 每调用一行；超 16MB 轮转；DSH_TRAJ=0 可关 |
| 16 | 大网格保护 + 三态 | audit/contract | 超限 → `analyzed:false` / `attached:null` → 门降级 |
| 17 | 机构：关节实测 + 扫掠 + URDF/USDA | motion.py（新） | 轴夹角 0.00°；扫掠撞挡块 → refuted 点名 MS_Blocker；URDF 结构自检全过 |
| 18 | 代码化建模通道（轻量版） | generator.py（新） | 全新进程复现 = 编译门；缓存命中 ms=0；改源码 → 回执过期 |
| — | 不搬：vendor/harness、stage 提示词、results-server、Isaac、FDM 公差 | — | 按评估跳过 |

**顺带修掉的基础设施问题**：preload 多模块互相覆写 helper（engine 改为各占命名空间）；
headless `ok:true/exit=0` ≠ 脚本成功（Blender 异常时退出码仍 0）；源码注入通道没有 `__file__`。

**未做/待评估**：OpenSCAD/Manifold 真接入（本机两侧都没装；装了才能拿「真 CSG 内核 + 大布尔运算」）；
Isaac 物理验证（按评估不引）；`mate_check` 自动落状态（当前只读）。

## 0. 现状对照（谁有什么）

| 能力面 | dsh-blender-plugin 现状 | Procedura 侧对应物 |
|---|---|---|
| 网格体检 | `audit.py`：边界/非流形/退化/自交/法线/游离/重复/包围盒（单对象） | `mesh/connectivity.ts`（跨体连通）、`mesh/stl.ts` |
| 干涉/间隙 | `contract.py`：`c_check_envelope`、`c_check_interference`（AABB+BVH）、`c_check_interface`（**只到 bbox 级间隙**） | `mesh/collisions.ts`（成对+分组）、`motion/geometry.ts`（三角级接触面积/侵入） |
| 浮块/脱离件 | **无** | `connectivity.ts` + `floater-attribution.ts` + `snap-floaters.ts` |
| 改动度量 | `txn.py` 只能存/回滚，不能量化"改了多少" | `mesh/chamfer.ts`（对称 Chamfer 距离 = 网格漂移） |
| 图像 QC | `qc.py`（IoU/Dice/剖面差/对齐）、`qc_render.py`（多视角+灯光+预算+md5） | `render/{ao,parts_color,pbr}.ts` + 三个 Blender 脚本 |
| 装配语义 | `planner.py` 组件/连接/候选、`contract.py` 组件/连接/包络/证据账本/三态判定 | `lib/assembly.scad`（配合特征库+fit 档）、`pipeline/assembly-seed.ts` |
| 铰接/机构 | **无**（urdf/usda/articulation 全 0 命中） | `motion/{urdf,usda,geometry}.ts` + `isaac_validate.py` |
| 交付导出 | 无统一交付网格约定（产物是 png/json/.blend） | `mesh/normalize.ts` + `mesh/obj-mtl.ts`（单位盒 + 多组 OBJ/MTL） |
| 过程可追溯 | CHANGELOG + evidence ledger | `_trajectory/*.jsonl`、`trajectory/emitter.ts` |

成本口径：XS <1h · S 半天内 · M 1–3 天 · L 1–2 周+

---

## 1. P0 — 低垂果实（纯 Python、bpy 原生、单会话可落地）

| # | 能力 | Procedura 来源（关键常量） | 融合落点 | 成本 | 收益 | 风险/边界 |
|---|---|---|---|---|---|---|
| 1 | **分离分量（floater）门** | `connectivity.ts`：`VISIBLE_SPAN_FRACTION=0.01`、`MICRO_GAP_MM=0.3`、`evaluateConnectivityGate`、`MAX_ANALYZE_TRIS=4e7` + `analyzed` 三态 | `audit.py` 新增 `audit_connectivity(scope)`：BMesh 面邻接 → 连通分量（tri/体积/bbox/spanFraction）；`contract.verify` 接它当出厂门 | S | 把"装配是否真成形"从看图变成判据；区分"摄影级缝隙"与"真掉件" | 必须设网格上限并把"未分析"独立标注——**绝不能把未分析读成已连通**（他们为此吃过 8–12h 挂死） |
| 2 | **浮块 → 组件归因** | `floater-attribution.ts`：bbox 重叠率评分、`confidence`、`moduleFraction`、`alsoOverlaps≥20%` 同漂移团簇 | `contract.py` 的 component（语义对象组）天然是归因目标；输出 `flt01 (span 3.2%) → 组件 seat_rail [82% overlap]` | S | 从"有个浮块"升级为"改哪个对象/组件"；长会话省掉试错 | bbox 重叠是近似（作者自陈），必须同时给 spanFraction 供复核 |
| 3 | **微隙分级 + snap** | `connectivity.floaterGapsMm` + `MICRO_GAP_MM` + `scad/snap-floaters.ts` | 报告分「真空隙 / 微隙（贴未重合）」；新增 `snap_floaters` op（默认 dry-run） | S–M | 门不被几十个微隙卡死；0.1mm 级缝隙一键收口 | 平移会碰修改器/父子关系 → 必须走 txn 快照 + dry_run |
| 4 | **网格漂移度量（对称 Chamfer 距离）** | `mesh/chamfer.ts`：各自归一化 → 面积加权采样 2 万点 → 空间哈希 NN，确定性种子；噪音底 ≈0.01–0.02 | 新增 `qc_mesh_drift(a,b)`（numpy 即可，读 .obj 或求值后的 mesh），接 `txn.mark → 改 → revert` | S | **一条独立于渲染的数值复核通路**（正合你 loop 的防 Goodhart 要求）；量化"这次改动动了多少" | 只测漂移不测质量；采样数与种子必须固定才可跨次比较 |
| 5 | **渲染设备行** | `_blender_gpu.py` 打印 `[render] device:`；`device_line.ts` 把它留进日志（防静默 CPU ≈7× 回退） | `qc_render` 的 jsonl 每行加 `device`；`perf.status` 回填 | XS | 7× 性能静默退化变可见 | 无 |
| 6 | **逐部件配色渲染** | `render/parts_color.ts` + `_render_parts_color_blender.py`：12 色循环、每部件一色、`parts_color_meta.txt` 图例、可按部件覆盖粗糙度/金属度 | `qc_render.py` 加 `mode="parts_color"`（材质覆盖，出图后还原） | S–M | 装配关系/穿插/悬空/镜像错位一眼可读，信息密度远超 AO 图 | 覆盖材质必须出图后还原（你已有现场还原纪律），别污染成品 .blend |
| 7 | **证据随改动失效** | `tools/state.ts`：`stlIsStale / hasFreshDiagnosis / pendingEdit`；accept 强制"先 compile+render 才准接受" | `contract.ledger` 每条证据绑定「对象集合 + 几何指纹」；任何写操作地把相关证据置 stale，重新验证才能再绿 | S–M | 堵住"用旧证据宣称新结论"——把《证据分级与读图纪律》机制化 | 大网格别算全量 md5，用顶点数+bbox+采样哈希 |
| 8 | **漂移降级阈值** | `pipeline/assembly-seed.ts`：`DRIFT_CENTER_FRACTION=0.10`、`DRIFT_SIZE_FRACTION=0.20` → 判定降级为 `unverified` | `c_verify` 记录被测 bbox 快照；超阈自动 `status: unresolved` + 理由 | S | 判定不会在长会话里"悄悄过期" | 对象带动画/形态键/缩放的父级要排除，防误降级 |
| 9 | **测量包 + 邻居上下文** | `tools/module_context.ts`：目标+邻居（装配图双向 + 空间最近 K=4）、真实世界 bbox、逐轴 gap/overlap（mm 与 %）、按 buffer hash 缓存 | 新增 `measure_pack(objects, neighbors=true, k=4)` | S–M | 模型从"看图估"转为"算术推 delta"（他们实测估错量级=白烧两轮） | 单位（m/mm）必须在返回里显式声明 |

## 2. P1 — 值得做（半天～几天）

| # | 能力 | Procedura 来源 | 融合落点 | 成本 | 收益 | 风险/边界 |
|---|---|---|---|---|---|---|
| 10 | **配合门（接触面积/侵入实测）** | `lib/assembly.scad`（peg/socket/bolt_hole/bolt_circle/boss/snap_tab/snap_window/tab/slot、`asm_fit` 四档、`asm_lead` 导向倒角）+ `motion/geometry.ts`（`analyzeContactRegion`：接触距离=1.5% 最大对角线、双向面积占比取最大、contactAnchor/法向；`analyzeMateRegistration`；`computeMeshVolume` 测侵入） | ① "共享标称"写成契约条款（公母同 nominal ± fit，禁两处独立字面量）② bpy 侧装配特征生成器/preset 配方 ③ `c_check_interface` 从 bbox 升级为三角级接触面积占比 + 侵入体积/深度 + fit 公差表 | M | 装配从"看着搭上了"到"真能装"；与你的装配/机构建模直接对口 | BVH 采样在 bpy 侧更重：限制采样点（他们 4000 封顶）、只对注册连接对算 |
| 11 | **干涉分析升级** | `mesh/collisions.ts`（成对报告 + 分组）+ `tools/collision-check.ts`（用 plan 的 parent 边抑制"计划内相邻"） | `c_check_interference` 输出改"每对 + 深度/体积 + 按设计意图分组"，并把 `planner` 连接图当白名单 | S–M | 报告可读可定位、不误报；多对象排查时间大降 | 白名单要可显式关掉（否则会掩盖真干涉） |
| 12 | **待定编辑事务** | `tools/edit-transaction.ts`：pending → `accept_edit(verified=...)` / `revert_edit(why=...)`，revert 免预算但有上限 | 破坏性操作协议：`begin_edit → 操作 → 必须复核 → accept/revert`，与 `destructive_guard` 串联 | S–M | 杜绝"猜错量级、两轮后才发现"；天然产出改动理由的可追溯记录 | 与现有 `txn.mark/revert` 语义要对齐，别搞出两套回滚 |
| 13 | **交付导出约定** | `mesh/normalize.ts`（单位盒）+ `mesh/obj-mtl.ts`（多组 OBJ+MTL 逐部件材质） | `export_deliverable`：可选单位盒归一化、逐部件材质、OBJ+MTL、md5 清单 | S–M | 交付格式统一、归档可核（对接 `D:\Blender\<项目>\归档清单.md`） | 归一化会改尺度，归档前要保留原始变换记录 |
| 14 | **视角目录 + 别名归一化** | `render/views.ts`：16 视角分 4 组、别名 `iso`/`iso-FR-top`→`isometric`、未知名回传警告、稳定 DEFAULT_VIEWS | `qc_render` 加同款 catalog（你已能自动解算视角，这里只补命名/容错/可点名） | XS–S | 视角可复现、跨会话可对照、提示词可直接点名 | 与现有 view 命名不冲突即可 |
| 15 | **轨迹 JSONL** | `_trajectory/procedura-*.jsonl` + `trajectory/emitter.ts` | runtime 写 `<outdir>/trajectory/<run>.jsonl`（工具调用/判定/证据/耗时），与 qc jsonl 合并 | S | 复盘、跨 run 对照、审计底账 | 体积控制/脱敏 |
| 16 | **大网格保护上限** | `MAX_ANALYZE_TRIS` + 免分配快速路径 + `analyzed:false` 三态 | 给连通/干涉/漂移三类分析加"超上限即显式跳过并标注未分析" | XS–S | 防事故（你实测过 2318 对象场景的同步成本） | 无 |

## 3. P2 — 战略级（重，看目标）

| # | 能力 | Procedura 来源 | 融合落点 | 成本 | 收益 | 风险/边界 |
|---|---|---|---|---|---|---|
| 17 | **铰接/机构：URDF + USDA + 运动学图 + 扫掠验证** | `motion/urdf.ts`（世界烘焙 mesh、局部帧=世界锚点、退化关节降级 fixed、孤立 link 合成 fixed、`BBOX_FILL_FACTOR=0.4` 惯性近似）、`usda.ts`、`geometry.ts:analyzeRotationalSymmetry`（面积加权质心+主轴，采样 3000、评分阈 0.55）、`isaac_validate.py` | 两步：① 纯 Blender 侧"关节轴/锚点实测 + 驱动扫掠采样 + 自碰自检"；② 再做 URDF/USDA 导出 | L | 打开机器人/机构品类（行星发动机、太空电梯的自然延伸）；"运动学正确"变可判据 | **别一上来引 Isaac**（多 GB + 账号门控）；先用 Blender 扫掠替代 |
| 18 | **代码化建模通道（OpenSCAD/Manifold）** | 整套 SCAD 流水线（编译门 + 模块拼接 + 模块级缓存 + 三重门） | 轻量版：用集合并层级 + 自定义属性模拟"模块"；重量版：真接 OpenSCAD | L | 可复现、可 diff、可程序化重生成（参数化族价值最大） | **必须 Manifold 后端**（同一 `hull()`：CGAL 1774s vs Manifold 1.77s）；Windows 侧安装/路径转换是额外成本；两套心智 |

## 4. 不建议搬

| 项 | 理由 |
|---|---|
| `vendor/harness/` 整套 agent 运行时、16 个 stage 提示词 | 你已有 DSH 自己的 harness + 提示词体系，重复造轮子零收益 |
| `scripts/results-server.ts` | `montage.py` + `qc_compare` 已覆盖大部分；仅"跨 run 指标表"时可借其对照思路 |
| Isaac Sim 物理验证 | 重依赖（多 GB + 账号），收益边际；先用 Blender 侧运动学扫掠 |
| FDM 公差数值（0.25/0.15/-0.05/0.2） | 是给 FDM 打印标定的，按你的工艺重标定，别照抄 |

## 5. 融合后的整体收益（复利点）

1. **判据层级上移**：从"单个网格健康"→"多对象装配是否成形、是否可装"（#1/#2/#10/#11）。
2. **多了一条与渲染无关的数值通路**：Chamfer 漂移 + 接触面积 + 逐轴间隙，正好满足"内环收敛后必须换另一条通路复核"的纪律（#4/#9/#10）。
3. **判定不再过期**：几何指纹绑定证据 + 漂移自动降级（#7/#8），长会话/反复改模型时结论可信。
4. **报告从"图"变"数 + 图"**：parts-color 图 + 数值表 + 归因点名（#2/#6/#11），模型和人定位问题的成本都降。
5. **交付与归档闭环**：单位盒 + 逐部件材质 + md5 清单（#13），与 `D:\Blender` 归档约定接得上。

## 6. 建议落地顺序

- **批次 1（半天）**：#1 连通门 + #2 归因 + #4 漂移度量 + #5 设备行 + #16 上限保护 → 立刻可用的"装配体检"。
- **批次 2（1–2 天）**：#3 snap + #6 逐部件配色 + #7/#8 证据失效/漂移降级 + #9 测量包 + #14 视角目录。
- **批次 3（按需）**：#10 配合门 + 特征库、#11 干涉升级、#12 编辑事务、#13 交付导出、#15 轨迹。
- **批次 4（战略）**：#17 机构/URDF（先做 Blender 侧扫掠）；#18 代码通道（若确定要做）。

## 附：本地参考镜像

关键源码已镜像到工作区 `.procedura-ref/`（45 个文件，604 KB，MIT），含 `lib_assembly.scad`、`src_mesh_connectivity.ts`、`src_mesh_floater-attribution.ts`、`src_mesh_chamfer.ts`、`src_motion_geometry.ts`、`src_render_*.ts`、`src_tools_*.ts`、三个 `_render_*_blender.py` 等，可直接对照移植。