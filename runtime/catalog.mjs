import { createHash } from 'node:crypto';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

/** 内容指纹（sha256 前 12 位）—— catalogFingerprint 用 */
function sha12(buf) { return createHash('sha256').update(buf).digest('hex').slice(0, 12); }
const HERE = path.dirname(fileURLToPath(import.meta.url));

export const PLAN_CATALOG = [
  { f: 'sculpt', prefix: 'sculpt_',
    when: '要"雕出形体"：有机/角色/道具的体积感、表面起伏、浮雕式细节',
    not: '规整硬表面板件（板+倒角那条路）；也不需要时别为了细节狂细分',
    sk: 'blender_rt_plan(op="sculpt_scan", args={objects:["Head"]}) → "sculpt_setup"(mode="VOXEL", voxel_size="auto") → "sculpt_apply"(strokes=[{brush:"draw", points:[[0,0,1]], radius:0.45, strength:0.7}])',
    ops: ['scan', 'setup', 'apply', 'filter', 'mask', 'remesh', 'selftest', 'help'],
    key: 'brush=draw|inflate|pinch|flatten|smooth|crease；无头可跑；笔触改拓扑后 mark/revert 回不去，改前先 rt_txn(op="snapshot")' },
  { f: 'fix', prefix: 'fix_',
    when: 'audit_mesh 报了缺陷要治（重复点/零面积面/孤立点/法线），或要减面',
    not: '设计上就该开放的薄壳：fill_holes 会把它封死',
    sk: 'blender_rt_plan(op="fix_repair", args={objects:["P01"], actions:["merge_doubles","dissolve_degenerate","delete_loose","recalc_normals"]})',
    ops: ['repair', 'decimate', 'selftest', 'help'],
    key: '回执带 before/after 缺陷计数；没修动就 ok=false（不许把"跑过"当"修好"）' },
  { f: 'uv', prefix: 'uv_',
    when: '要贴图、要交付带 UV 的 OBJ/MTL，或减面之后 UV 乱了',
    not: '纯几何验证阶段不必展开 UV',
    sk: 'blender_rt_plan(op="uv_smart_project", args={objects:["Body"], angle_limit:66}) → "uv_stats" → "uv_pack"',
    ops: ['stats', 'smart_project', 'unwrap', 'project', 'pack', 'selftest', 'help'],
    key: '判据：有 UV 层 且 零面积 UV 面=0；method 传错会回"允许列表"' },
  { f: 'print', prefix: 'print_',
    when: '判断"能不能造出来"：3D 打印/加工口径的壁厚与悬垂',
    not: '装配干涉/连通/包络（那走 audit_*）',
    sk: 'blender_rt_plan(op="print_report", args={objects:["Body"], min_mm:1.2, max_angle_deg:45})',
    ops: ['walls', 'overhang', 'report', 'selftest', 'help'],
    key: '回执带 resolution_mm（本地面尺寸）；min_mm 低于它会给"结论不可信"警告 —— 别拿粗网格下细结论' },
  { f: 'sweep', prefix: 'sweep_',
    when: '管路 / 线缆 / 轨道 / 护栏这类"沿路径成形"',
    not: '等距重复阵列（履带/链节走参数化 Array 配方）',
    sk: 'blender_rt_plan(op="sweep_analyze", args={path:[[0,0,0],[0.6,0,0],[0.6,0,0.6]], profile:{type:"circle", radius:0.05}}) → "sweep_build"',
    ops: ['analyze', 'build', 'selftest', 'help'],
    key: '先算弯折半径 vs 型材半宽；过紧默认拒绝（force=true 才硬做）' },
  { f: 'audit', prefix: 'audit_',
    when: '装配体检与出厂门：连通 / 干涉 / 包络 / 漂移 / 量测 / 重复件 / 浮块',
    not: '主观"像不像"（那走 qc_*）',
    sk: 'blender_rt_plan(op="audit_scene", args={}) → "audit_mesh"(objects=[...]) → "audit_gate"(scope="COL_Geo", envelope=[[min],[max]])',
    ops: ['mesh', 'scene', 'duplicates', 'connectivity', 'gate', 'drift', 'measure', 'snap_floaters', 'overlap', 'interference', 'purge_orphans', 'selftest', 'gate_selftest', 'help'],
    key: '相接口径 micro_gap_mm 默认 0.3（3D 打印口径）；带设计间隙的装配件按工艺给 1–2；audit_interference/overlap 支持 file= 跨 .blend；回执太大时加 summary_only=true / top_k=N（实测 40 对象场景 9,176→1,148 字符，判定不变）' },
  { f: 'qc', prefix: 'qc_',
    when: '与参考图比对（IoU / 剖面差 / 鲁棒性）或做合成自检',
    not: '能数值判定的装配问题（audit_* 更硬）',
    sk: 'blender_rt_plan(op="qc_compare", args={ref:"ref.png", render:"out.png"})',
    ops: ['compare', 'compare_basic', 'compare_auto', 'align_iou', 'align_rotate', 'mask_auto', 'mask_sweep', 'metrics', 'profile', 'profile_diff', 'robustness_check', 'self_check', 'load', 'crop', 'mask', 'help'],
    key: '没有参考图时先 qc_render_views 出图再看；**自定义灯组**用 lights={key,fill,rim}（或 lights=false 关掉），lights_mode="add"(默认，叠在场景灯上)/"only"(临时屏蔽其它灯，出图后还原) —— 背面全黑时先想到它' },
  { f: 'qc_render', prefix: 'qc_render_',
    when: '要对照图 / 多视角 / 逐部件配色图（自动取景 + 临时三点光 + 逐张 md5）',
    not: '只是"改一步看一眼"→ 用 blender_rt_see（约 100 ms，别开渲染）',
    sk: 'blender_rt_plan(op="qc_render_views", args={views:[{name:"front", from:[7,-7,5], look_at:[0,0,0], lens:50}], res:[1280,720], samples:64, outdir:"D:/DSH/blender/out/qc"})',
    ops: ['views', 'catalog', 'help'],
    key: '长渲染加 asJob=true 转作业层；ref_path 给了就逐张算 IoU；主体 <5% 画面会给 subject_too_small' },
  { f: 'pipe', prefix: 'pipe_',
    when: '多步链要一次跑完、产出要传给下一步：别再人肉搬运 JSON，用 @工件 引用',
    not: '不是新的几何能力：它是编排层，实际执行仍在各家族；单发 op 不需要它',
    sk: 'op="pipe_run", args={steps:[{api:"vehicle",op:"sections",args:{side:...},out:"@sec"},{api:"vehicle",op:"loft",args:{stations:"@sec.stations"}}]}',
    ops: ['run', 'put', 'get', 'list', 'clear', 'selftest', 'help'],
    key: '@工件 或 @工件.a.b 引用前一步产出；失败默认即停；回执逐步给 ms/ok/keys/stored' },
  { f: 'shape', prefix: 'shape_',
    when: '任何**参考图还原**：先问 shape_plan 拿该类别协议（要哪些视图/盯哪些比例/配哪些算子/怎么验收），再走量→放样→特征线→IoU',
    not: '不是新几何引擎：几何复用 img_*/vehicle_* 的量具与放样；也不适用于没外轮廓的（布料/毛发/流体）',
    sk: 'op="shape_plan", args={object_class:"furniture"} → op="shape_sections", args={side:"D:/ref/s.png", front:"D:/ref/f.png", mm_per_px:2.0} → op="shape_loft", args={stations:…, section_shape:…, crease_lines:[{frac:0.5,radius_mm:6}]}；轴对称走 op="shape_revolve"',
    ops: ['plan', 'sections', 'loft', 'fit', 'regions', 'panels', 'revolve', 'selftest', 'help'],
    key: '类别：vehicle/humanoid/creature/headwear/furniture/hull/aircraft/rotational/weapon/generic —— 每类给视图、关键比例、算子清单、验收门与已知坑；方法与类别无关的部分：量轮廓 → 放样 → 特征线 → IoU' },
  { f: 'vehicle', prefix: 'vehicle_',
    when: '车辆外壳要贴参考图 / 要根治穿模：先用比例门定包络，再放样成壳（轮眉解析扣出 ⇒ 零互穿）',
    not: '不做细节件（灯/格栅/后视镜/玻璃分件）—— 那些用硬表面单独做再挂；也不是车漆材质（material_*）',
    sk: 'op="vehicle_sections", args={side:"D:/ref/side.png", top:"D:/ref/top.png", mm_per_px:3.2} → op="vehicle_loft", args={stations:<上一步>, n_top:5, tumblehome_mm:120, flare_mm:30, crease_shoulder:0.7}；参数化 spec 用 op="vehicle_fit"（family:"polyline" + control_points 给 K 扫描，closed 折线族能贴到 IoU 0.82+）→ op="vehicle_panels", args={object_name:…, cuts_mm:[1500,3000], gap_mm:4}；简版也可 op="vehicle_base", args={...15 参数}',
    ops: ['spec', 'package', 'sections', 'loft', 'panels', 'regions', 'fit', 'base', 'selftest', 'help'],
    key: '参数表照 cargen（长宽高/轴距/前轴到车头/轮径轮宽/格栅高/引擎盖角/风挡角/车顶长/后窗角/后备箱角，米+度）；比例按 vehicle-design 的 WBR（运动车 35-40% / 超跑 40-45% / 肌肉车 30-35% / SUV 25-30%）—— 不过比例门就别建壳' },
  { f: 'clearance', prefix: 'clear_',
    when: '成对间隙/互穿判定：轮↔轮眉必须留间隙、板件允许互插——两种规则要分开判',
    not: '不是整体交集体积（那是 audit_interference）；它按规则表逐对判',
    sk: 'op="clear_check", args={pairs:[{id:"wheel_fl", a:["WHEEL_FL"], b:["GEO-body"], min_mm:8}]}',
    ops: ['check', 'selftest', 'help'],
    key: 'min_mm 管间隙、allow_overlap 管允许互插；回执给 gap_mm / interpenetrating / overlap_tri_pairs。间隙用采样点量（大曲面平行贴合可能高估），互穿判定精确' },
  { f: 'human', prefix: 'human_',
    when: '要人形：比例正确的素体（装配基准/摆姿势/挂装甲），或从参考图反推人体比例',
    not: '不做写实人体：脸/手这类高细节部位用面罩手套回避；也不是骨骼绑定（那是 motion_*）',
    sk: 'op="human_base", args={height_mm:1750, heads:7.5, pose:"A"} → op="human_measure", args={name:"HumanBase"}；参考图先 op="human_spec", args={image:"D:/ref/front.png"}',
    ops: ['base', 'measure', 'spec', 'head', 'selftest', 'help'],
    key: '回执给 heads_measured（几何量出来）与 shoulder_over_head —— 与 spec 比差就是验收；markers=true 出 19 个关节空物体（供 motion_joints 与装甲挂载）' },
  { f: 'ext', prefix: '',
    when: '交付物/几何要外部独立通路复核：glTF 语义合规（规则码）、水密/流形/自交的第二意见',
    not: '不是插件内体检（那是 audit_*/print_*）：它跑外部进程 glTF-Validator / open3d',
    sk: 'op="gltf_validate", args={path:"D:/out/model.glb"} → op="ext_mesh_check", args={path:"/mnt/d/out/model.ply"}',
    ops: ['gltf_validate', 'ext_mesh_check'],
    key: 'gltf_validate 给 messages[].code（规则码可进 pass_if），numErrors==0 才算过；ext_mesh_check 用 open3d 给 watertight/edge_manifold/self_intersecting —— 导出用 PLY/STL 别用 OBJ（Blender 5.2 的 OBJ 会被 open3d 读成 0 面，实测）' },
  { f: 'face', prefix: 'face_',
    when: '人形/人脸要判「比例对不对」「这版比参考差多少」—— 把脸从主观判断变成可优化数字',
    not: '不认脸、不做检测：landmarks 要你给（自己标或 MediaPipe/EMOCA 出）；轮廓那一半用 img_diff',
    sk: 'op="face_ratios", args={landmarks:{top:[…],chin:[…],eye_l:[…],eye_r:[…],face_l:[…],face_r:[…],nose_base:[…]}} → op="face_compare", args={attempt:{…}, ref:{…}}',
    ops: ['ratios', 'compare', 'selftest', 'help'],
    key: '经典比例只是参考值（风格/年龄/种族不同）—— 容差必须按项目钉进 spec.py；轮廓用 img_diff 的 IoU，两者合起来才是完整判据' },
  { f: 'img', prefix: 'img_',
    when: '参考图看不清细节 / 要量它：轮廓尺寸、长宽比、主要直线角度、指定点取色；或把量到的东西画回图上、把两张图做差分',
    not: '不是"看图"本身（看图仍要 read_image/rt_see）；它是量具，负责把像素变成数字',
    sk: 'op="img_scan", args={path:"D:/ref/front.png"} → op="img_crop", args={path:…, x:20, y:30, w:200, h:160, scale:2} → op="img_diff", args={a:"render.png", b:"ref.png"}',
    ops: ['scan', 'rectify', 'crop', 'annotate', 'diff', 'selftest', 'help'],
    key: '协议：**看图必须产出数字**（写进 spec.py），否则算白看；每次 ≤2 张图且带明确问题；先 crop 到 ROI 再放大；判"像不像"用 img_diff 的 IoU 不靠肉眼；像素↔毫米要标定。照片背景复杂先抠图（rembg），否则给 threshold' },
  { f: 'calib', prefix: 'calib_',
    when: '想知道"我的门到底抓得住什么"：注入 8 类已知缺陷量捕获率；或改了门之后验证有没有变强',
    not: '不是日常体检（那是 audit_*/print_*）；它跑的是自建装配，不动你的件',
    sk: 'op="calib_run", args={} → 看 capture_rate / missed / baseline_pass；op="calib_selftest"',
    ops: ['run', 'selftest', 'help'],
    key: '**baseline_pass 必须为 true**（健康基线 4 门全过＝无假阳性），否则整次标定作废；missed 列表就是门的盲区 —— 实测它一次抓出 4 个真 bug（薄板测不到/体素假薄壁/各向异性缩放漏判/pass_if 优先级）' },
  { f: 'gate', prefix: 'gate_',
    when: '把**一个项目的验收判据固化下来、一次跑完**（替掉"每个项目重写一遍门脚本"）',
    not: '不是单点体检（只查一项用 audit_mesh / print_report）；也不做渲染对照（那是 qc_*）',
    sk: 'op="gate_plan", args={spec_path:"D:/proj/gate.json"} → op="gate_run", args={spec_path:"D:/proj/gate.json", out_json:"D:/proj/gate_out.json"}；或 op="gate_run", args={preset:"assembly"}',
    ops: ['plan', 'run', 'selftest', 'help'],
    key: 'verdict **三态**（pass/fail/degraded），**ok 只在 pass 时为 true**（degraded 不等于通过）；spec 支持 .json / .py（SPEC 或 GATES）/ 内联 dict / preset；pass_if 是受限表达式（比较/布尔/算术 + min/max/len/abs/round/all/any/sum），引用不存在的字段会报错并列出可用字段' },
  { f: 'material', prefix: 'material_',
    when: '要材质：程序化材质（金属拉丝 / 漆面 / 锈 / 玻璃 / 布料 / 木纹 / 混凝土 / 自发光 / 全息），或把程序化材质**烘成贴图**交付',
    not: '只是换个纯色（改 BSDF 默认值就行）；也不是贴图库（那走 PolyHaven 集成）',
    sk: 'op="material_build", args={name:"M-hull", preset:"metal_brushed", params:{base_color:[0.35,0.38,0.42], scale:24}} → "material_apply"(objects=["P01"]) → "material_bake"(bake_type="AO", resolution:1024)',
    ops: ['scan', 'build', 'apply', 'bake', 'selftest', 'help'],
    key: 'preset 写错回允许列表；bake 前必须先有 UV（会指路 uv_smart_project）；bake 走 Cycles、结束自动还原引擎；OBJ/MTL 交付要带材质就得先 bake' },
  { f: 'render_guard', prefix: '',
    when: '渲染中撞到"卡住/超时"：想知道 Blender 是不是正在渲染、等它空下来，或清掉崩溃后留下的陈标记',
    not: '它不是渲染入口（出图用 qc_render_views / rt_headless）；多会话排队用 render_lock',
    sk: 'op="render_state", args={} → op="render_wait"(timeout_ms=300000)',
    ops: ['render_state', 'render_wait', 'render_reset', 'render_guard_install', 'render_guard_selftest'],
    key: '渲染开始/结束由 bpy handler 写/删磁盘标记；**渲染中主线程写命令秒回 BUSY_RENDER**（不再排队到超时）；陈标记 15 min 自动放行' },
  { f: 'render', prefix: 'render_',
    when: '渲染队列/锁与渲染状态（多会话排队）',
    not: '单次出图不需要锁',
    sk: 'blender_rt_plan(op="render_status", args={})',
    ops: ['lock', 'status', 'help'],
    key: 'cross-process 文件锁：acquire/release/status，TTL + 持有者' },
  { f: 'deliver', prefix: 'deliver_',
    when: '交付导出：单位盒归一化 + 多组 OBJ/MTL + manifest(md5)',
    not: '中间产物（直接 rt_headless 里 export 就行）',
    sk: 'blender_rt_plan(op="deliver_export", args={objects:["COL_Geo"]}) → "deliver_verify"',
    ops: ['export', 'verify', 'help'],
    key: '要 GLB/FBX 用 rt_cmd export_scene（addon v1.7 起）' },
  { f: 'motion', prefix: 'motion_',
    when: '机构/铰接：关节轴与锚点实测、扫掠验证、URDF/USDA 导出',
    not: '静态几何（audit_measure 就够）',
    sk: 'blender_rt_plan(op="motion_joints", args={}) → "motion_measure" → "motion_export_urdf"',
    ops: ['joints', 'joint', 'infer_axis', 'measure', 'export_urdf', 'export_usda', 'status', 'reset', 'selftest', 'help'],
    key: 'motion_measure 会临时驱动对象 → 属写操作（要过租约）' },
  { f: 'generator', prefix: 'generator_',
    when: '参数化重复件（履带/链节/齿圈/阵列）；"改参不改码"，配方即形状',
    not: '一次性造型',
    sk: 'blender_rt_plan(op="generator_save", args={name:"sprocket", code:"n=PARAMS.get(\'n\',8)\\n…", params:{n:8}}) → op="generator_run", args={name:"sprocket", args:{n:12}, expect:{objects:12}}',
    ops: ['save', 'run', 'list', 'get', 'diff', 'selftest', 'help'],
    key: '源码参数名是 **code**（不是 script）；generator_run 的 PARAMS 走 **args**（这一层不会被摊平）。编译门 = 全新无头进程复现；源码一改回执自动过期' },
  { f: 'plan', prefix: 'plan_',
    when: '假设驱动建模：组件/连接/包络注册、三态判定、破坏性门控、证据账本',
    not: '单纯几何体检（audit_* 更直接）',
    sk: 'blender_rt_plan(op="plan_status", args={})',
    ops: ['load', 'validate', 'order', 'build', 'graph', 'status', 'diag', 'help'],
    key: '判据见插件包 docs/假设驱动建模-cookbook.md；外部证据不足必须报 unresolved' },
  { f: 'contract', prefix: '',
    when: '需要"先判定再动手"：包络/干涉/接口校验、未判别连接上的 destructive_guard、证据与报告',
    not: '已经明确要改就直说（别为了流程而流程）',
    sk: 'blender_rt_plan(op="check_interference", args={a:"P01", b:"P02"}) / op="destructive_guard"',
    ops: ['status', 'help', 'reset', 'register_component', 'register_connection', 'register_envelope', 'check_envelope', 'check_interference', 'check_interface', 'destructive_guard', 'evidence', 'ledger', 'report', 'verify', 'flip', 'advance', 'mate_check', 'fit_help', 'interference_report'],
    key: '未判别的连接上做 boolean/weld/merge 会被拦下（返回 Unsupported Destructive Merge）' },
  { f: 'gui', prefix: 'gui_',
    when: '只有真 UI 上下文才能做的事：框选对象/全场景、切视口着色、打开 .blend',
    not: '无头 -b 进程里做不了（会明确报"没有 VIEW_3D 区域"）',
    sk: 'blender_rt_plan(op="gui_frame", args={object:"P01"})',
    ops: ['frame', 'shading', 'open', 'help'],
    key: 'gui_frame/gui_shading 属只读；gui_open 会换文件' },
  { f: 'montage', prefix: '',
    when: '把多张对照图拼成一张给眼睛看（证据分级：只给少数格）',
    not: '只有一张图时不需要',
    sk: 'blender_rt_plan(op="montage", args={images:[...], out:"D:/.../montage.png"})',
    ops: ['montage', 'help'],
    key: '' },
];

/** plan 之外的顶层工具：先决定"用哪个工具"，再决定 op */
/**
 * v0.9.6（D2 · 描述预算）：重工具的参数长尾（从工具描述里搬出来的那部分）。
 * 取用：blender_rt_plan(op="catalog", args={tool:"rt_headless"})（后端本地直出）
 * 动机：15 个工具的 schema 是每轮固定开销；模型在"调用那一刻"只需要参数名 + 一句说明。
 * 维护：工具描述里删掉的解释性文字往这里放，别让它悄悄涨回去（tests/discoverability_selftest.mjs 有预算门）。
 */
export const TOOL_DETAIL = {
 "rt_headless": "blender_rt_headless —— 无头 Blender 第一路径（独立进程，不动 GUI 场景）\n\n【路径语义（现场踩过，先读这条）】headless 的 cwd 是 \\\\wsl.localhost\\<distro>\\home\\<user>\\DSH；**绝对 WSL 路径必须带开头 / **（漏了会被当相对路径接到 cwd 后面，症状是 ...\\DSH\\home\\<user>\\DSH\\测试\\...；v0.9.6 起会自动纠正并写后端 stderr）。script_file 是**内联**执行（拷进 D:\\DSH\\blender\\tmp\\dsh_headless_*.py），v0.9.6 起自动注入 DSH_SCRIPT_FILE + __file__（指回原脚本）+ 脚本目录进 sys.path —— 多文件工程不再需要手铺 sys.path。\n\n【结果契约】脚本里 print(\"HEADLESS \" + json.dumps(obj, separators=(\",\",\":\")))；必须是**单行 JSON**。\n  解析失败不顶掉输出：记在 resultParseError，stdoutTail/logs 保留原文；>4KB 的结果自动落盘并给 resultPath。\n【超时语义】（v0.9.3）timeout_ms 只决定服务端子进程跑多久；客户端等待窗口默认 100 s（DSH_HEADLESS_WAIT_MS 可调），\n  到点回 {kind:\"promoted\", jobId:\"run-…\"} —— 子进程照跑，用 blender_rt_job(op=\"wait\"|\"collect\", id=…) 收结果。\n  ⇒ 预期 >100 s 的活**直接 as_job=true**（可配 wait_s 先等几秒），别让外层 deadline 决定结果去向。\n【引擎】默认注入 EEVEE + 光追前导（回执回 engine/gpu）；engine=\"cycles\" 走 OptiX、engine=\"keep\" 保持现状、\n  engine=\"none\" 完全跳过前导与 GPU 探测（纯 numpy/图像类任务省 1–1.5 s）。gpu 只对 cycles 路径有意义。\n【路径】outdir/out_json/file/script_file 都收 WSL 路径（/home/… 内部映射成 \\wsl.localhost\\<distro>\\…），\n  回执给 Windows + WSL 两种真实路径；脚本把 POSIX 路径交给 Windows API 会被体检出来（pathWarnings）。\n【可观测】三处 spawn 注入 PYTHONUNBUFFERED=1（日志运行期就有增量）；脚本里 dsh_stage(\"building\") 打心跳，\n  blender_rt_job(op=\"status\") 回 stage/idleMs/lines/logBytes/pidAlive。\n【多视角一体化】shots=[{name, from, look_at, lens, res, samples}] 一次出 N 张（内置 harness：自动三点光 + 渲染锁 +\n  逐张 md5 + 实测设备）；回执 res.shots 每行带 coverage_estimate，主体占画面 <5% 会给 subject_too_small。\n【回执字段】result / resultJson / status / gpu / logs / lastException / traceback / artifacts / shots / inputFile。\n【脚本内可用】K（持久内核，与 rt_do 同一套）/ K.args / K.win_path / K.wsl_path / K.blend_path / K.out_dir / K.env；\n  预载模块用 preload=\"audit,qc,qc_render\" → K.dsh_audit_api / K.dsh_qc_api …\n【别做】长任务别连发 op=status 轮询（用 job op=wait）；GUI 会话里别连发 render.render()。\n【常见坑】没传 outdir 时产物落在默认工作目录；file= 传了 .py 会被自动当脚本（并给提示）；\n  factory_startup=false 会加载用户 startup（其中本插件会尝试占 9876 端口，通常无害但有报错噪音）。",
 "rt_job": "blender_rt_job —— 作业层（长活后台化 + 按 runId/jobId 回收）\n\nop=start: 与 headless 同形参（script / script_file / file / outdir / args / env / engine / preload /\n  factory_startup / bootstrap / workdir / out_json / timeout_ms 默认 1 h、上限 24 h）。\nop=wait: 阻塞到完成或超时（默认 120 s / 上限 600 s）—— 一次拿结构化结果，**别连发 status**。\nop=status/collect: stage/stageAt/stageAgeMs/lastOutputAt/idleMs/lines/logBytes/pidAlive；collect 可 tail=N。\nop=kill: 对未知 id 不抛错（回\"已结束/不存在\"）。op=list: 列作业。\nrun 与 job 同一 id 空间：headless 的 runId（run-…）也能用 status/collect/wait/kill 收。\n日志落 <outdir>/jobs/<id>/（stdout.log 边跑边写）；后端重启后台账仍在磁盘。",
 "rt_worker": "blender_rt_worker —— 热无头会话（反复迭代免冷启动：省 0.9–1.2 s 启动 + 最多 ~16 s EEVEE 着色器编译）\n\nstart: name（v0.9.4 多实例，默认 default）/ engine / gpu；exec: name / code / timeout_ms / purge_prefix；\nstatus / stop / restart / list。\n串行：一次只处理一个请求，长代码会占住 worker；无窗口（依赖 GUI 上下文的 bpy.ops 可能失败）。\n语义：同一实例共享场景与 K（复用 = 放弃进程隔离），脏了用 restart 换新会话。\n改了用户模块必须 purge_prefix（否则 import 命中旧代码）。print(\"HEADLESS {json}\") 仍是结果契约。",
 "rt_see": "blender_rt_see —— 取一帧视口（约 55–160 ms；比 CLI/MCP 快 20 倍）\n\nmax_size（默认 560，420 更快 / 900 更清晰）；full=true + area=N 走整窗口截图（看 Blender UI 用）；\nfrom/look_at（+ lens / ortho / ortho_scale / view_size / shading / overlays / view_mode）走自定义视角：\n  自建矩阵离屏绘制，不建相机、不改 scene.camera、不动用户视口（约 100 ms）。\ndiagnostics=true 强制跑三项诊断（coverage / scene_bbox / objects_in_frame）；默认只在近空帧自动补跑。\n同画面重复出图默认不重复附图（省视觉 token），要重发传 force=true；回执带 hash 可对拍。",
 "rt_loop": "blender_rt_loop —— Blender 侧内环迭代（bpy.app.timers，主线程安全，迭代/时间双上限 + 急停）\n\n何时用（量化）：要在参数空间搜 >=20 次、且每次都得出图或量测 → 用它；只搜 <=5 次或判据不需每次渲染 → 用 rt_do 自己循环。\nspec={setup, step, measure, iterations, budget_ms, interval, measure_every, minimize, top_k, group_key, redraw_every, patience}；\n  setup 只跑一次；step/measure 共享命名空间 ns（预置 bpy/K/math/random/np/i/frac/penalize/anneal/record）；\n  measure 必须给 ns[\"score\"]，参数写 ns[\"params\"]，可选 ns[\"metrics\"]/ns[\"violations\"]。\npatience（默认 0=关）：连续 N 次 measure 没刷新 best 就早停，stop_reason=\"plateau\"。\nop=start/status/stop/board/export/help/bench；export 把 best 导出成可复用脚本。\n⚠ 内环只优化你写的目标函数：收敛后必须换另一条计算通路复核 + rt_see 视觉确认（防 Goodhart）。",
 "viewport": "blender_viewport —— 通道运维 + 写租约 + 工具参数细节\n\nstatus: 后端健康/视口区域/计数/版本；doctor: 真跑一次 bpy 往返的三级体检（未连 / 主线程忙 / addon 线程卡死 + 修法）；\nwho / lease / release: 写租约（holder / ttl_ms / force；被别人持有时写路由 409，只读 op 豁免）；\nstart / stop / restart: 后端进程与 15 s 看护；\nlaunch: 一键拉起 GUI Blender 并自动 Connect addon（wait_ms 默认 90000 / file / exe / addon_module / addon_file / dry_run）；\n  唯一可信判据是\"addon 端口开了\"，不靠进程活着；幂等（已在监听就回 already=true）。\nhelp: args={tool:\"rt_headless\"} 取该工具的完整参数细节；args 省略 → 返回有细节的工具索引。",
 "rt_plan": 'blender_rt_plan —— 判定 / 验收 / 导出 / 造型的唯一入口（' + PLAN_CATALOG.length + ' family / '
   + planOpNames().length + ' op；数字由 PLAN_CATALOG 现算，不再手写以免过期）\n\n'
   + '不确定用哪个 op → op="catalog"（本地直出：什么时候用 / 别用 / 最小骨架）。\n'
   + '单族展开：op="catalog", args={family:"audit"}；算子细节：op="audit_help" / "sculpt_help" / "qc_help" …\n'
   + '工具参数细节（重工具长尾）：op="catalog", args={tool:"rt_plan"}。\n'
   + '常用：audit_scene|audit_mesh|audit_gate|audit_interference · qc_render_views|qc_compare ·\n'
   + '  sculpt_scan→setup→apply · fix_repair|fix_decimate · uv_smart_project|uv_unwrap|uv_pack ·\n'
   + '  print_report · sweep_analyze→build · deliver_export|verify · motion_joints|measure|export_urdf ·\n'
   + '  generator_save|run（save 用 **code=**；run 的生成器 PARAMS 走嵌套 **args=**，不会被摊平）·\n'
   + '  gui_frame|shading|open · render_lock|render_status · montage ·\n'
   + '  契约层 status|register_*|check_*|destructive_guard|evidence|ledger|report|verify|flip|advance。\n'
   + '【目录自证（v0.9.6 D3）】目录回执带 provenance：loadedHash=本进程加载 engine.mjs 时的**内容指纹**（sha256:12）、\n'
   + '  currentHash=现场再读磁盘的指纹、verdict=current|stale|unknown。**不看 mtime**（touch 会假 stale、同秒重写会假 fresh）。\n'
   + '  stale=true → 磁盘已改、本进程还是旧一代：重启后端再问一次；强对拍用 op="catalog", args={verify:true}\n'
   + '  （另起干净 node 进程 import 磁盘 engine.mjs，用它的 family/op 数与 catalogHash 当裁判 —— 实测 ~50 ms）。\n'
   + '写 op 过写租约；只读 op（catalog / audit_* / print_* / qc_* / sculpt_scan / uv_stats / sweep_analyze …）豁免；\n'
   + '每次顶层调用另写一行轨迹 JSONL（DSH_TRAJ=0 关，DSH_TRAJ_FULL=1 记更多参数）。'
};

export const PLAN_TOOL_MAP = [
  { t: 'blender_viewport(op="doctor")', use: '开工前体检/排错；launch 可一键拉起 GUI Blender' },
  { t: 'blender_rt_see', use: '看一眼画面（约 100 ms；给 from/look_at 走自定义视角，不建相机）' },
  { t: 'blender_rt_do', use: '改一步看一眼（GUI 常驻 + 持久内核 K，变量跨调用保留）' },
  { t: 'blender_rt_headless', use: '独立进程跑脚本（变量不保留）；批处理第一路径' },
  { t: 'blender_rt_job', use: '重活/长渲染：op=start 异步 + op=wait/collect 按 runId 回收（超时不丢结果）' },
  { t: 'blender_rt_worker', use: '同一脚本反复跑（冷启动与着色器编译只付一次）；单例串行，多 agent 别共用' },
  { t: 'blender_rt_txn', use: '回退点：mark/revert 对象级毫秒级；拓扑改动必须 snapshot/restore' },
  { t: 'blender_rt_preset', use: '参数配方：save/apply/export/import（点路径 data；MAT:/OBJ:/SCENE）' },
  { t: 'blender_rt_plan', use: '所有"判定/验收/导出/雕刻/修复/UV/制造/扫掠"的 op 都在这里（不确定就 op="catalog"）' },
  { t: 'blender_rt_cmd / rt_commands', use: '透传 addon 命令（get_scene_info / export_scene / describe_node_type / bpy_api_lookup …）' },
];

export function planEditDistance(a, b) {
  const m = a.length, n = b.length;
  let prev = new Array(n + 1);
  for (let j = 0; j <= n; j++) prev[j] = j;
  for (let i = 1; i <= m; i++) {
    const cur = [i];
    for (let j = 1; j <= n; j++) {
      cur[j] = Math.min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (a[i - 1] === b[j - 1] ? 0 : 1));
    }
    prev = cur;
  }
  return prev[n];
}

/** 全部已知 plan op 名（带 family 前缀，便于最近邻） */
export function planOpNames() {
  const out = [];
  for (const e of PLAN_CATALOG) {
    for (const o of e.ops) out.push(e.prefix ? e.prefix + o : o);
  }
  return out;
}

/** 未知 op 的最近邻建议（含最小骨架）—— 把"写错了"变成"一步改对" */
export function planOpSuggestion(opName) {
  const target = String(opName || '');
  if (!target) return null;
  let best = null;
  for (const name of planOpNames()) {
    const d = planEditDistance(target.toLowerCase(), name.toLowerCase());
    if (!best || d < best.d) best = { name: name, d: d };
  }
  if (!best || best.d > Math.max(3, Math.ceil(target.length * 0.34))) return null;
  const fam = PLAN_CATALOG.find((e) => (e.prefix ? name0StartsWith(best.name, e.prefix) : e.ops.indexOf(best.name) >= 0));
  return { op: best.name, distance: best.d, family: fam ? fam.f : null, skeleton: fam ? fam.sk : null,
           when: fam ? fam.when : null };
}
export function name0StartsWith(s, p) { return String(s).indexOf(p) === 0; }

/** rt_cmd 跨通道纠错：把 plan op 当 addon 命令发时，直接告诉它该走哪个工具（实测真实发生过） */
export function planOpCrossChannelHint(addonName) {
  const n = String(addonName || '');
  if (!n || COMMAND_CATALOG[n]) return null;
  // ① 全名精确命中（qc_render_catalog / audit_mesh / sculpt_apply …）
  for (const e of PLAN_CATALOG) {
    for (const op of e.ops) {
      const full = e.prefix ? e.prefix + op : op;
      if (n === full) {
        return '「' + n + '」是 blender_rt_plan 的 op（' + e.f + ' 家族），不是 addon 命令 → '
          + '改用 blender_rt_plan(op="' + full + '", args={...})。' + (e.sk ? '最小骨架：' + e.sk : '');
      }
    }
  }
  // ② 只写了下半段（catalog / mesh / apply）→ 补上前缀
  for (const e of PLAN_CATALOG) {
    if (e.prefix && e.ops.indexOf(n) >= 0) {
      const full = e.prefix + n;
      return '「' + n + '」是 plan 通道的 ' + e.f + ' 家族 op → 完整写法是 blender_rt_plan(op="' + full + '")。'
        + (e.sk ? '最小骨架：' + e.sk : '');
    }
  }
  // ③ 都不是 → 最近邻
  const near = planOpSuggestion(n);
  if (near) {
    return '「' + n + '」既不是 addon 命令、也不是已知 plan op；最接近的是「' + near.op + '」→ '
      + 'blender_rt_plan(op="' + near.op + '")。' + (near.skeleton ? '最小骨架：' + near.skeleton : '');
  }
  return null;
}

/**
 * v0.9.6（D3）：目录自己的内容指纹 —— 只哈希"真正会回答的东西"（定序，顺序敏感）。
 * 用途：① 跨进程对拍（干净进程 import 磁盘 engine.mjs 后的 catalogHash 必须相等）
 *       ② 把"目录变没变"变成可比对的 12 位十六进制，而不是"感觉好像多了几族"。
 */
export function catalogFingerprint() {
  const parts = [];
  for (const e of PLAN_CATALOG) parts.push([e.f, e.prefix, e.ops.join(','), e.when, e.not, e.sk || '', e.key || ''].join('|'));
  return { catalogHash: sha12(Buffer.from(parts.join(String.fromCharCode(10)), 'utf8')),
           families: PLAN_CATALOG.length, opsCount: planOpNames().length,
           toolDetailKeys: Object.keys(TOOL_DETAIL).length,
           commandCatalogKeys: Object.keys(COMMAND_CATALOG).length };
}

/** 另起一个干净 node 进程 import **磁盘上的** engine.mjs —— 只有它才是"下一个请求会拿到什么"的裁判 */
export function probeDiskCatalog(timeoutMs) {
  if (!ENGINE_SOURCE_PATH) return { ok: false, error: 'engine 源码路径未知（import.meta.url 解不出）' };
  const js = [
    'import(' + JSON.stringify(pathToFileURL(ENGINE_SOURCE_PATH).href) + ')',
    '.then(function (m) { process.stdout.write(JSON.stringify(Object.assign({ ok: true, pluginVersion: m.PLUGIN_VERSION }, m.catalogFingerprint()))); })',
    '.catch(function (e) { process.stdout.write(JSON.stringify({ ok: false, error: String((e && e.message) || e) })); })',
  ].join('');
  try {
    // ⚠ 故意不给 --input-type/-e 之外的任何"装依赖"动作：只用当前 node，不联网、不装包。
    const out = execFileSync(process.execPath, ['-e', js], {
      encoding: 'utf8', timeout: Math.max(2000, Math.min(30000, Number(timeoutMs) || 8000)),
      stdio: ['ignore', 'pipe', 'pipe'], env: Object.assign({}, process.env, { DSH_PROVENANCE_PROBE: '1' }),
    });
    const line = String(out).trim().split(String.fromCharCode(10)).filter(Boolean).pop() || '';
    return JSON.parse(line);
  } catch (e) { return { ok: false, error: String((e && e.message) || e).slice(0, 200) }; }
}

/**
 * 加载版本 / 磁盘版本的内容指纹对拍（v0.9.6 D3）。
 * 默认只读一次磁盘（~0.2 ms）；`verify:true` 才起干净进程对拍（~120 ms），按需付费。
 */
/** 单条 family 的短渲染（渐进披露：先目录，再 family，再 *_help） */
export function renderFamilyText(e) {
  const L = [];
  L.push('family: ' + e.f + (e.prefix ? '（前缀 ' + e.prefix + '）' : ''));
  L.push('什么时候用: ' + e.when);
  L.push('什么时候别用: ' + e.not);
  L.push('ops: ' + e.ops.join(' / '));
  if (e.sk) L.push('最小骨架: ' + e.sk);
  if (e.key) L.push('要点: ' + e.key);
  L.push('细节速查: blender_rt_plan(op="' + (e.ops.indexOf('help') >= 0 ? (e.prefix ? e.prefix + 'help' : e.f + '_help') : 'help') + '")');
  return L.join(String.fromCharCode(10));
}

/**
 * op="catalog" 的载荷。默认**只回短文本**（≈2–3k 字符）：flash 级模型读"短枚举 + 判据 + 骨架"远比读
 * 4.7k 字散文有效；结构化明细要 args={full:true} 才给（那是给机器/复盘用的）。
 *   op="catalog"                         → 一页目录（family 一行 + ops + 骨架要点）
 *   op="catalog", args={family:"sculpt"}  → 只展开这一族
 *   op="catalog", args={full:true}        → 附带结构化 families/tools
 */
/**
 * S4 · 单一事实源：/plan 通道的只读 op 白名单（唯一声明处）（本文件是唯一声明处）。
 * server.mjs 直接消费；tests/single_source_selftest.mjs 与冻结基线逐名对拍，防迁移丢项/多项。
 * 判据：不改场景、不动对象变换、不注册 handler。新增只读 op 只改这一处。
 */
export const PLAN_READ_ONLY_OPS = [
  'status', 'help', 'ledger', 'check_envelope', 'check_interference',
  'check_interface', 'plan_status', 'plan_validate', 'plan_diag', 'plan_order',
  'plan_graph', 'audit_mesh', 'audit_scene', 'audit_duplicates', 'audit_help',
  'audit_connectivity', 'audit_drift', 'audit_measure', 'audit_gate', 'montage',
  'motion_status', 'motion_help', 'motion_joints', 'deliver_help', 'deliver_verify',
  'generator_list', 'generator_get', 'generator_diff', 'generator_help', 'catalog',
  'catalog_help', 'help_all', 'material_scan', 'material_help', 'gate_plan',
  'gate_help', 'img_scan', 'img_help', 'calib_help', 'face_ratios',
  'face_compare', 'face_help', 'gltf_validate', 'clear_check', 'clear_help',
  'vehicle_package', 'vehicle_spec', 'vehicle_help', 'shape_plan', 'shape_help',
  'shape_sections', 'shape_revolve', 'render_state', 'render_wait', 'sculpt_scan',
  'sculpt_help', 'fix_help', 'uv_stats', 'uv_help', 'print_walls',
  'print_overhang', 'print_report', 'print_help', 'sweep_analyze', 'sweep_help',
  'gui_frame', 'gui_shading', 'gui_help', 'render_status', 'render_lock_status',
];
export const PLAN_READ_ONLY = new Set(PLAN_READ_ONLY_OPS);

export const COMMAND_CATALOG = {
  ping: { d: '存活探测（不碰 bpy 数据）', gate: null },
  get_scene_info: { d: '场景概览（无参）', gate: null },
  get_world_state_snapshot: { d: '世界状态快照：对象/选中/帧', gate: null },
  get_addon_info: { d: 'addon 版本与协议号', gate: null },
  get_object_info: { d: '单对象详情（参数 name）', gate: null },
  get_viewport_screenshot: { d: '视口离屏截图（max_size, filepath, format）', gate: null },
  // v0.9.6：addon 升到 v1.7（协议号 11）后新增的三条常驻命令 —— 让模型查 API 而不是猜
  describe_node_type: { d: '查节点类型的全部 socket/属性（bl_idname, property_overrides）—— 建着色器/几何节点前先查，别猜 socket 顺序', gate: null },
  bpy_api_lookup: { d: '查 bpy API 参考（query，如 "bpy.ops.mesh.primitive_cube_add"）', gate: null },
  export_scene: { d: '导出场景/选择/名单到 glb|fbx|obj（filepath, format, object_names, selection_only, apply_modifiers）', gate: null },
  get_tripo_status: { d: 'Tripo 集成状态（premium 通道）', gate: null },
  execute_code: { d: '执行任意 Python（万能通道，预置 K/bpy/math/mathutils）', gate: null },
  drain_human_activity: { d: '取走"人类操作"事件缓冲', gate: null },
  get_telemetry_consent: { d: '读遥测同意开关', gate: null },
  set_telemetry_consent: { d: '写遥测同意开关（参数 consent）', gate: null },
  get_polyhaven_status: { d: 'PolyHaven 集成状态', gate: null },
  get_hyper3d_status: { d: 'Hyper3D Rodin 集成状态', gate: null },
  get_sketchfab_status: { d: 'Sketchfab 集成状态', gate: null },
  get_polypizza_status: { d: 'Poly Pizza 集成状态', gate: null },
  get_hunyuan3d_status: { d: 'Hunyuan3D 集成状态', gate: null },
  get_polyhaven_categories: { d: 'PolyHaven 分类（参数 asset_type: hdris/textures/models）', gate: 'blendermcp_use_polyhaven' },
  search_polyhaven_assets: { d: 'PolyHaven 搜索（asset_type, categories）', gate: 'blendermcp_use_polyhaven' },
  download_polyhaven_asset: { d: '下载并导入 PolyHaven 资产（asset_id, asset_type, resolution）', gate: 'blendermcp_use_polyhaven' },
  set_texture: { d: '把已下载贴图应用到对象（object_name, texture_id）', gate: 'blendermcp_use_polyhaven' },
  create_rodin_job: { d: 'Hyper3D Rodin 生成任务（文本/图片）', gate: 'blendermcp_use_hyper3d' },
  poll_rodin_job_status: { d: 'Rodin 任务轮询', gate: 'blendermcp_use_hyper3d' },
  import_generated_asset: { d: '导入 Rodin 生成结果', gate: 'blendermcp_use_hyper3d' },
  search_sketchfab_models: { d: 'Sketchfab 搜索（query, categories, count）', gate: 'blendermcp_use_sketchfab' },
  get_sketchfab_model_preview: { d: 'Sketchfab 模型预览图', gate: 'blendermcp_use_sketchfab' },
  download_sketchfab_model: { d: '下载并导入 Sketchfab 模型（uid）', gate: 'blendermcp_use_sketchfab' },
  search_polypizza_models: { d: 'Poly Pizza 搜索（query）', gate: 'blendermcp_use_polypizza' },
  download_polypizza_model: { d: '下载并导入 Poly Pizza 模型（model_id）', gate: 'blendermcp_use_polypizza' },
  create_hunyuan_job: { d: 'Hunyuan3D 生成任务', gate: 'blendermcp_use_hunyuan3d' },
  poll_hunyuan_job_status: { d: 'Hunyuan3D 任务轮询', gate: 'blendermcp_use_hunyuan3d' },
  import_generated_asset_hunyuan: { d: '导入 Hunyuan3D 生成结果', gate: 'blendermcp_use_hunyuan3d' },
};
