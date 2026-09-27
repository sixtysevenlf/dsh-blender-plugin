/**
 * S6 · 建模类型指引表（"哪类建模 → 第一步 + 该用哪些算子 + 禁止自造什么 + 验收门"）
 *
 * 为什么单独一份：catalog 是"能力族"视角（audit/qc/vehicle/…），模型想的是"我要建什么"。
 * 实测教训：另一个建模 AI 有 vehicle_* 配方、知道 Recipe 17/18/19，仍然自己写参数化放样 ⇒
 * 一张连续光滑面、没棱没缝、6 处结构硬伤。根因不是缺文档，是缺"入口 + 禁止自造 + 可数值判的验收门"。
 * 本表把这三样按**建模类型**写死，并让目录能按类查询：
 *   blender_rt_plan(op="catalog", args={classes:true})         → 全部类型
 *   blender_rt_plan(op="catalog", args={class:"recon"})        → 单类（入口/算子链/禁止自造/验收门）
 * 完整性由 tests/guidance_selftest.mjs 机械检查（缺字段/写错 op 名会红）。
 */

export const MODELING_CLASS_GUIDE = [
  {
    c: "recon",
    name: "参考图还原（任何有外轮廓的物体）",
    entry: "blender_rt_plan(op=\"shape_plan\", args={object_class:\"aircraft|vehicle|rotational|furniture|hull|…\"})",
    chain: "img_rectify → shape_sections → shape_loft（要可改数字就用 shape_fit 出参数 spec）→ crease_lines / inset_lines → shape_regions / shape_panels → qc_render_views(ref_path=…)",
    forbid: "别自造放样、别手算站表 —— 自造只能出一张连续光滑面（没棱没缝的肥皂）；截面通路必须用 shape_plan 给的 section_path",
    gate: "比例门（shape_package / vehicle_package）+ 三视图 IoU + 板缝与特征线计数 > 0",
  },
  {
    c: "faceted",
    name: "分面硬表面（装甲板 / 军模 / 机械板件）",
    entry: "技能 Recipe 2a（先选形）",
    chain: "基本体/板件 + 布尔 → bevel（分段 2–3、保硬边）→ audit_mesh → clear_check（拼接件）",
    forbid: "别用 SubSurf 抹平棱；别靠缩放板件假装板缝",
    gate: "audit_mesh 锐边/法线 + 板缝可见 + 无浮块（audit_connectivity）",
  },
  {
    c: "smooth",
    name: "光滑硬表面（钣金 / 圆润道具）",
    entry: "技能 Recipe 2b",
    chain: "基本体 → Bevel（2 段）→ SubSurf(2) 或 crease_lines(radius_mm=…) → audit_mesh",
    forbid: "别手推顶点做曲率；别用 shade smooth 假装圆角（真半径要 Bevel/crease）",
    gate: "无褶皱/自交 + 特征线条数 > 0 + 剖面偏差（profile_diff）",
  },
  {
    c: "organic",
    name: "有机 / 角色 / 道具体积",
    entry: "blender_rt_plan(op=\"sculpt_scan\", args={objects:[\"…\"]})",
    chain: "sculpt_setup(mode=\"VOXEL\", voxel_size=\"auto\") → sculpt_apply(brush=draw|inflate|pinch|flatten|smooth|crease) → sculpt_filter / sculpt_remesh → audit_connectivity",
    forbid: "别用布尔拼体积；无头里别试 bpy.ops.sculpt.brush_stroke（Blender 5.2 Python 走不通）",
    gate: "连通分量 = 1 + 包络 + 法线朝外 +（要打印则）print_report",
  },
  {
    c: "repeats",
    name: "重复阵列（履带 / 链节 / 散热片）",
    entry: "blender_rt_plan(op=\"generator_save\", args={code:…}) 或技能 Recipe 6b（参数化节距阵列）",
    chain: "generator_save → generator_run(args=…)（改参不改码）或 Array+Curve → audit_interference",
    forbid: "别复制粘贴硬编码件（改参要改码就错了）",
    gate: "件数/节距/间隙数值门 + 零互穿",
  },
  {
    c: "boolean",
    name: "布尔开孔（轮眉 / 散热孔 / 减重孔）",
    entry: "blender_rt_plan(op=\"destructive_guard\", args={op:\"boolean\", targets:[\"…\"]})",
    chain: "destructive_guard → 布尔 → fix_repair（清理）→ audit_interference",
    forbid: "别在未判别的连接上布尔（会毁掉装配关系）",
    gate: "零互穿 + 零面积面 = 0 + 连接未被破坏",
  },
  {
    c: "human",
    name: "人形素体 / 头型",
    entry: "human_* 素体与头型（技能 Recipe 15/16）",
    chain: "human 素体/头型 → sculpt_* 细修 → audit_mesh →（装配）clear_check",
    forbid: "别自造人体比例表；脸/手这类高细节部位用面罩手套回避",
    gate: "比例表 + 连通 = 1 + 无自交",
  },
  {
    c: "sweep",
    name: "管路 / 线缆 / 轨道 / 护栏",
    entry: "blender_rt_plan(op=\"sweep_analyze\", args={path:[[…]], profile:{type:\"circle\", radius_mm:…}})",
    chain: "sweep_analyze（先算弯折半径）→ sweep_build → audit_connectivity",
    forbid: "别手接路径段（弯折过紧默认会被拒）",
    gate: "弯折半径 > 型材半宽 + 连通分量 = 1",
  },
  {
    c: "rotational",
    name: "旋转体（瓶 / 罐 / 轮毂 / 花瓶 / 喷口）",
    entry: "blender_rt_plan(op=\"shape_plan\", args={object_class:\"rotational\"})",
    chain: "shape_revolve(parts=[…]) →（壁厚）print_report → material_*",
    forbid: "别放样 —— 轴对称走放样是错的路径（shape_plan 明说 shape_revolve）",
    gate: "回转轴向偏差 + 壁厚（print_report 的 resolution_mm 警告要当回事）",
  },
  {
    c: "plate",
    name: "板件 / 型材 / 翼型（有弯度或左右不对称）",
    entry: "blender_rt_plan(op=\"shape_plan\", args={object_class:\"aircraft|hull\"})",
    chain: "shape_sections → section_outline（显式闭合轮廓）或超椭圆 section_pts + crease_lines → shape_loft",
    forbid: "别用左右对称镜像表达弯度（翼型必选 section_outline）",
    gate: "剖面偏差（profile_diff）+ IoU + 特征线条数",
  },
  {
    c: "assembly",
    name: "装配 / 机构 / 铰接",
    entry: "blender_rt_plan(op=\"gate_plan\", args={preset:\"assembly\"})",
    chain: "mate_check / fit_help → motion_joints → motion_measure → motion_export_urdf；总验收 gate_run(spec_path=…)",
    forbid: "别自写验收判据（顶层读 verdict 三态）",
    gate: "gate_run 的 verdict ∈ pass/degraded/refuted（degraded ≠ 通过）",
  },
  {
    c: "deliver",
    name: "打印可行性 / 交付",
    entry: "blender_rt_plan(op=\"print_report\", args={objects:[\"…\"], min_mm:1.2, max_angle_deg:45})",
    chain: "uv_* → material_* → material_bake → deliver_export → deliver_verify",
    forbid: "别手写导出、别只交 .blend（交付要 OBJ/MTL + manifest md5）",
    gate: "manifest md5 对拍 + resolution_mm 警告 + UV 零面积面 = 0",
  },
];

/** 单类的短渲染（模型一屏能读完：入口 → 链 → 禁止 → 门） */
export function renderClassText(r) {
  const L = [];
  L.push("建模类型: " + r.name + "（class=" + r.c + "）");
  L.push("第一步: " + r.entry);
  L.push("算子链: " + r.chain);
  L.push("禁止自造: " + r.forbid);
  L.push("验收门: " + r.gate);
  return L.join(String.fromCharCode(10));
}

/** 供机械检查用：缺字段的类别会被 guidance_selftest 报红 */
export function classGuideStats() {
  const need = ["c", "name", "entry", "chain", "forbid", "gate"];
  const missing = [];
  for (const r of MODELING_CLASS_GUIDE) {
    for (const k of need) if (!r[k] || !String(r[k]).trim()) missing.push(r.c + "." + k);
  }
  return { classes: MODELING_CLASS_GUIDE.length, keys: MODELING_CLASS_GUIDE.map((r) => r.c), missing };
}

/** 模型侧工具清单（15 个）—— 与 capability.lock.json 的 tools 逐名对拍（guidance_selftest 守） */
export const MODEL_FACING_TOOLS = [
  "blender_rt_see", "blender_rt_do", "blender_rt_watch", "blender_rt_loop",
  "blender_rt_cmd", "blender_rt_commands", "blender_rt_perf", "blender_rt_opt",
  "blender_rt_headless", "blender_rt_plan", "blender_rt_worker", "blender_rt_txn",
  "blender_rt_preset", "blender_rt_job", "blender_viewport",
];

/**
 * 子代理交接块（S6-b）—— 主模型把它**原样粘进子代理提示词**。
 *
 * 为什么必需：子代理是自包含上下文（看不到主模型读过的 catalog），而它们倾向直接写 Python 绕过 plan 通道；
 * 实测后果就是"没用 vehicle_* 而自造放样 → 一张没棱没缝的光滑面"。
 * 目标：**短**（能塞进子代理提示词）、**只给判断所需的**（类型→第一步→禁止自造 + 工具名 + 硬规则 + 通道纪律）。
 */
export function renderHandoff() {
  const L = [];
  L.push("# Blender 工具指路（子代理必读 · 30 秒）");
  L.push("");
  L.push("**别猜工具**：先 `blender_rt_plan(op=\"catalog\")` 看 28 个能力族；按建模类型查 `args={classes:true}`（或单类 `args={class:\"recon\"}`）。");
  L.push("模型侧 15 个工具：" + MODEL_FACING_TOOLS.map((t) => "`" + t + "`").join(" "));
  L.push("");
  L.push("## 12 类建模：第一步 | 禁止自造");
  L.push("");
  for (const r of MODELING_CLASS_GUIDE) {
    L.push("- **" + r.c + "**（" + r.name + "）：" + r.entry + " ｜ 禁止：" + r.forbid);
  }
  L.push("");
  L.push("## 三条硬规则");
  L.push("1. 有参考图 → 先 `shape_plan(object_class=…)` 拿 `section_path`，别自造截面、别手算站表；");
  L.push("2. 车壳/外壳类**禁止自造放样**（自造只能出一张连续光滑面，没棱没缝）；板缝与棱线用 `vehicle_panels` + `crease_lines`/`inset_lines`；");
  L.push("3. 验收一律**机械门**（`gate_run` 顶层读 verdict 三态；`degraded` ≠ 通过），别用\"看起来像\"代替。");
  L.push("");
  L.push("## 通道纪律");
  L.push("- 长活（>1 min / 长渲染 / 批量几何）走 `blender_rt_job` / `blender_rt_headless`，别同步硬等；");
  L.push("- 写场景**同一时刻只能一个写者**（写租约；别的会话持有时回 409，只读 op 豁免）；");
  L.push("- 改一步看一眼用 `blender_rt_see`（~100 ms）；量尺寸用 `img_*`/`audit_*`，**别用眼睛估**。");
  return L.join(String.fromCharCode(10));
}
