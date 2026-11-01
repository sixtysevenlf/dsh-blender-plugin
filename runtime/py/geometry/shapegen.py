# -*- coding: utf-8 -*-
"""DSH 参考图通用还原（P4）：**与对象类别无关**的"包络 → 站表 → 截面 → 特征线 → 验收"方法层。

这一层不重复实现几何：
  * 量（站表/截面轮廓/包络）→ 复用 vehicle_sections（它本来就是逐列/逐行量轮廓，与"车"无关）
  * 放样（站表 + 截面 + 特征线 + 圆角）→ 复用 vehicle_loft
  * 参数化（折线族 + K 扫描）→ 复用 vehicle_fit(family="polyline")
  * 多体量 / 缝 → 复用 vehicle_regions / vehicle_panels
本层负责的是**方法**：按对象类别给出"要哪些视图、盯哪些比例、配哪些算子、怎么验收"。

ops：
    shape_plan(object_class)   —— 该类别的还原协议（视图/比例/算子/验收/坑）
    shape_sections(...)        —— 三视图 → 站表 + 截面轮廓 + 包络（通用量具）
    shape_loft(...)            —— 站表 → 放样（截面轮廓 + 特征线 + 圆角）
    shape_fit(...)             —— 站表 → 折线族参数化（默认 family=polyline，K 扫描）
    shape_regions / shape_panels —— 多体量 / 按缝分件
    shape_selftest / shape_help

⚠ 适用边界（写进 shape_help）：凡"有明确外轮廓、能用正交视图描述"的物体都适用；
不适用：没有外轮廓的（布料/毛发/流体/烟）、靠内部结构定义的（多孔晶格/拓扑优化件）。
"""
import json

SHAPE_VERSION = 2  # v2：新增 S0 草稿评估（shape_draft / shape_draft_check）

# 类别 → 还原协议。views 里 s/f/t = 侧/前/顶 视；ratios = 必须盯住的比例；ops = 该配的算子
CLASSES = {
    "vehicle": {
        "views": "s f t", "symmetric": "双侧", "volumes": "单/多体量", "ground": "轮或履带接地",
        "ratios": ["轮径/车高 WBR", "轴距/车长", "高/长", "轮距/车宽", "前后悬", "离地间隙"],
        "ops": ["img_rectify", "vehicle_sections", "vehicle_package", "vehicle_loft", "vehicle_regions",
                "vehicle_panels", "vehicle_fit", "clear_check"],
        "gates": ["vehicle_package 全项 pass", "clear_check 零互穿 + 缝宽一致", "qc_render_views 三视图 IoU"],
        "traps": ["轮径不可从填充轮廓反推（要 wheel_r_px）", "没有俯视图 ⇒ 车身偏窄", "照片必须先把透视校正"],
    },
    "humanoid": {
        "views": "s f", "symmetric": "双侧", "volumes": "单（含分件）", "ground": "双脚接地",
        "ratios": ["头身比 heads（7–8）", "肩宽/头高（≈1.5）", "肘/膝位置（站位的 0.5/0.75）"],
        "ops": ["human_spec", "human_base", "human_head", "human_measure", "face_ratios", "motion_joints"],
        "gates": ["human_measure 的 heads/shoulder_over_head 与 spec 差", "face_ratios 容差", "motion_joints 关节可动"],
        "traps": ["写实脸零素材做不到 ⇒ 用面罩/头盔遮住", "关节标记是唯一可靠挂载基准"],
    },
    "creature": {
        "views": "s f t", "symmetric": "双侧", "volumes": "单", "ground": "四足/多足接地",
        "ratios": ["身长/肩高", "头长/身长", "四肢节段比例"],
        "ops": ["shape_sections", "shape_loft", "shape_fit", "audit_connectivity"],
        "gates": ["站表 IoU", "轮廓对参考图 IoU ≥0.8"],
        "traps": ["四足与双足不能用同一套比例", "毛发不适用本方法（无外轮廓）"],
    },
    "headwear": {
        "views": "s f t", "symmetric": "双侧", "volumes": "单（壳）", "ground": "无",
        "ratios": ["头高/头宽/头深（233:175:206 参考）", "眼线 0.50 / 鼻底 0.725 / 嘴线 0.825"],
        "ops": ["human_head", "face_ratios", "shape_loft", "print_report"],
        "gates": ["三维比例与 landmark 一致", "print_report 壁厚（要打印时）"],
        "traps": ["内腔要留头围余量；耳/颈开口别忘"],
    },
    "furniture": {
        "views": "s f", "symmetric": "双侧", "volumes": "多体量（座/背/腿）", "ground": "四脚接地",
        "ratios": ["座高/总高", "座深/总深", "靠背倾角（°）", "腿部节距"],
        "ops": ["shape_sections", "shape_loft", "shape_regions", "sweep_build", "print_report", "clear_check"],
        "gates": ["关键尺寸（座高/座深）±2%", "腿与座面无互穿", "要打印则过 print_report"],
        "traps": ["人体工学尺寸优先于外形相似", "多体量必须分件做再装配"],
    },
    "hull": {
        "views": "s t", "symmetric": "双侧", "volumes": "单", "ground": "水线",
        "ratios": ["船长/船宽", "型深/船长", "水线以上/以下截面不同"],
        "ops": ["shape_sections", "shape_loft", "shape_fit", "print_report"],
        "gates": ["水线处宽度吻合", "侧视轮廓 IoU"],
        "traps": ["水线上下要**分别**给站表（截面突变）", "俯视图必须有（决定船宽分布）"],
    },
    "aircraft": {
        "views": "s f t", "symmetric": "双侧", "volumes": "多体量（机身/机翼/尾翼）", "ground": "起落架",
        "ratios": ["翼展/机身长", "机翼弦长分布", "机身高/宽", "尾翼面积比"],
        "ops": ["shape_sections", "shape_loft", "shape_regions", "sweep_build", "clear_check"],
        "gates": ["三视图 IoU（顶视最重要）", "翼与机身接口无互穿"],
        "traps": ["机翼要用**翼型截面**（shape 给剖面点）不是超椭圆", "上反角/后掠角是独立参数"],
    },
    "rotational": {
        "views": "s（单轮廓）", "symmetric": "轴对称", "volumes": "单", "ground": "无",
        "ratios": ["高/直径", "最大直径位置", "颈/口比例"],
        "ops": ["img_scan", "shape_revolve", "print_report"],
        "gates": ["轮廓 IoU", "旋转体闭合（audit_mesh 无非流形）"],
        "traps": ["**别用放样**：旋转体（瓶/罐/轮毂/花瓶）要沿轴**旋转**单条轮廓"],
    },
    "weapon": {
        "views": "s t", "symmetric": "单侧/双侧", "volumes": "多体量", "ground": "无",
        "ratios": ["总长", "握把位置/长度", "枪管/机匣比例"],
        "ops": ["shape_sections", "shape_loft", "shape_regions", "clear_check"],
        "gates": ["总长与握持点尺寸", "运动件间隙（clear_check min_mm）"],
        "traps": ["握持尺寸是硬约束（手宽/指位）", "运动件要留行程间隙"],
    },
    "generic": {
        "views": "s f（+t 强烈建议）", "symmetric": "按实际", "volumes": "按实际", "ground": "按实际",
        "ratios": ["先量长/宽/高三个包络比，再定该类别特有的比例"],
        "ops": ["img_rectify", "shape_sections", "shape_loft", "shape_fit", "img_diff", "audit_mesh"],
        "gates": ["三视图 IoU", "包络尺寸误差 ≤2%"],
        "traps": ["照片先校正透视", "没有俯视图就没有平面收放"],
    },
}

# ── 截面通路选择（**唯一的选路依据**：shape_plan 会把它随协议一起给出来）
SECTION_PATH = {
    "vehicle": "section_shape（前视逐行宽度→镜像对称）；宽体套件/非对称件用 section_outline；轮/毂用 shape_revolve",
    "humanoid": "human_base 自带体量；躯干/四肢断面要精确时用 section_shape",
    "creature": "section_shape（侧视给轮廓、俯视给半宽）；有角/喙这类非对称件用 section_outline",
    "headwear": "section_shape（对称帽体）；帽檐/护目镜这类非对称件用 section_outline；头盔内腔用 shape_revolve 起步",
    "furniture": "section_outline（腿/靠背多为矩形或异形截面）；管件走 sweep_build；座面/靠背分块用 shape_regions",
    "hull": "section_shape（俯视给半宽）**水线上下分别给站表**；球鼻艏等回转体用 shape_revolve",
    "aircraft": "**section_outline（翼型，必选）**——镜像对称那条路表达不了弯度；机身用 section_shape；发动机舱用 shape_revolve",
    "rotational": "**shape_revolve（不是放样）**：瓶/罐/轮毂/花瓶/喷口；多件用 shape_revolve(parts=[...])",
    "weapon": "section_outline（机匣/护木多为矩形或异形）；枪管/弹匣井用 shape_revolve；握把用 section_outline",
    "generic": "默认 section_shape；**有弯度/左右不对称/要精确控制截面 ⇒ section_outline**；轴对称 ⇒ shape_revolve",
}

DECISION = [
    "① 轴对称（瓶/罐/轮毂/花瓶/喷口）⇒ shape_revolve（母线绕轴旋转），**不要**用放样",
    "② 左右对称、且前视能看到轮廓 ⇒ section_shape（归一化逐行宽度，按站点半宽缩放）",
    "③ 有弯度 / 左右不对称 / 要精确控制截面（翼型、机匣、异形件）⇒ section_outline（显式闭合轮廓）",
    "④ 只有一条侧视轮廓、截面随长度基本不变（板件、型材）⇒ 超椭圆截面 section_pts + 特征线即可",
    "⑤ 多体量（驾驶室+货箱、机身+机翼、轮毂+轮胎）⇒ shape_regions / shape_revolve(parts=[...])，装配后 clear_check 验间隙",
]

STEPS = [
    "⓪ 草稿评估：shape_draft —— 只判「是什么 / 大概多大（量级档）/ 拆成几块 / 主体配件 / 什么形 / 哪凸哪凹」，"
    "**不量 mm、不数 mesh**；填好表用 shape_draft_check 校验（图来了再逐行完善）",
    "① 校正：照片 → img_rectify（正交三视图可跳过）",
    "② 量包络：长/宽/高 + 该类别关键比例（先过比例门，不过别往下做）",
    "③ 量站表：shape_sections（侧视逐列上下边界 + 俯视逐列半宽 + 前视截面轮廓）",
    "④ 放样：shape_loft（站表定轮廓、截面轮廓定性格、特征线定面感）",
    "⑤ 特征线：crease_lines（硬折线 / radius_mm 圆角）+ inset_lines（折面凹槽）",
    "⑥ 参数化（可选）：shape_fit 折线族 + K 扫描，得到可改数字的 spec",
    "⑦ 多体量/缝：shape_regions（分块各成一个体量）+ shape_panels（真缝）",
    "⑧ 验收：三视图 IoU（qc_render_views/img_diff）+ 数值门（尺寸/比例/间隙）",
]


import sys as _sys_kit
_KIT = getattr(_sys_kit.modules.get("dsh_rt_kernel"), "dsh_kit", None)
if _KIT is None:
    raise RuntimeError("shapegen 需要共享内核 K.dsh_kit（由 KERNEL_BOOTSTRAP 注入）")


_j = _KIT.j  # 共享内核（原自带实现已删，见 S1）
_api = _KIT.api  # 共享内核
def shape_plan(object_class="generic", family=None):
    """该类别的**还原协议**：要哪些视图、盯哪些比例、配哪些算子、怎么验收、有哪些坑。"""
    c = str(object_class or "generic").lower()
    if c not in CLASSES:
        return _j({"ok": False, "error": "未知类别 object_class=%s" % object_class,
                   "classes": sorted(CLASSES)})
    row = dict(CLASSES[c])
    row["section_path"] = SECTION_PATH.get(c)
    return _j({"ok": True, "object_class": c, "plan": row,
               "steps": STEPS, "section_decision": DECISION, "families": sorted(CLASSES),
               "note": "这是**方法层**：几何全部复用 img_*/vehicle_* 的量具、放样、拟合、分件算子；"
                       "换类别只换协议（视图/比例/验收），不换机制"})


def shape_sections(**kw):
    """通用量具：三视图 → 站表 + 截面轮廓 + 包络（委托 vehicle_sections；轮轴检测只在有轮时命中）。"""
    api = _api("vehicle")
    if api is None:
        return _j({"ok": False, "error": "需要 vehicle 模块（preload 里加 vehicle）"})
    out = api("sections", kw)
    out["layer"] = "shape/measure"
    return _j(out)


def shape_loft(**kw):
    """通用放样：站表 → 壳（截面轮廓 + 特征线 + 圆角 + 轮眉外扩）。委托 vehicle_loft。"""
    api = _api("vehicle")
    if api is None:
        return _j({"ok": False, "error": "需要 vehicle 模块"})
    out = api("loft", kw)
    out["layer"] = "shape/loft"
    return _j(out)


def shape_fit(**kw):
    """通用参数化：站表 → 折线族 + K 扫描（默认 family=polyline；它不依赖任何"车"的假设）。"""
    api = _api("vehicle")
    if api is None:
        return _j({"ok": False, "error": "需要 vehicle 模块"})
    kw.setdefault("family", "polyline")
    out = api("fit", kw)
    out["layer"] = "shape/fit"
    return _j(out)


def shape_regions(**kw):
    """通用多体量：按区间分块，每块一个独立体量。委托 vehicle_regions。"""
    api = _api("vehicle")
    if api is None:
        return _j({"ok": False, "error": "需要 vehicle 模块"})
    out = api("regions", kw)
    out["layer"] = "shape/regions"
    return _j(out)


def shape_panels(**kw):
    """通用分件：在给定位置减薄板 ⇒ 真实缝，可选分离成多件。委托 vehicle_panels。"""
    api = _api("vehicle")
    if api is None:
        return _j({"ok": False, "error": "需要 vehicle 模块"})
    out = api("panels", kw)
    out["layer"] = "shape/panels"
    return _j(out)


def shape_revolve(profile=None, name="Revolved", axis="Z", segments=48, angle_deg=360.0, parts=None):
    """**旋转体**通路（瓶/罐/轮毂/花瓶）：单条轮廓绕轴旋转 —— 这类物体不要用放样。

    profile = [[r_mm, z_mm], …]（母线：半径 → 高度），按 z 从下到上给。
    """
    import bpy
    import bmesh as _bm
    import math
    if parts:
        # **多体量旋转件**：一次给多个 {profile, at:[x,y,z]_mm, name, segments}
        # 用途：轮毂+轮胎、瓶身+瓶盖、喷口阵列、法兰组 —— 旋转体与多体量组合
        out = []
        for i, q in enumerate(list(parts)):
            q = dict(q or {})
            at = q.get("at") or [0.0, 0.0, 0.0]
            r = json.loads(shape_revolve(profile=q.get("profile"), name=q.get("name") or ("%s_%d" % (name, i)),
                                         axis=q.get("axis") or axis,
                                         segments=int(q.get("segments") or segments),
                                         angle_deg=float(q.get("angle_deg") or angle_deg)))
            if not r.get("ok"):
                out.append({"name": q.get("name") or i, "ok": False, "error": r.get("error")})
                continue
            ob = bpy.data.objects.get(r.get("object"))
            if ob is not None:
                ob.location = (float(at[0]) / 1000.0, float(at[1]) / 1000.0, float(at[2]) / 1000.0)
            r["at_mm"] = [round(float(x), 2) for x in at]
            out.append(r)
        return _j({"ok": all(r.get("ok") for r in out), "parts": out, "count": len(out),
                   "note": "多体量旋转件：每件独立对象 + 独立落点；装配后再用 clear_check 验间隙"})
    try:
        pts = [[float(q[0]) / 1000.0, float(q[1]) / 1000.0] for q in (profile or [])]
        if len(pts) < 2:
            return _j({"ok": False, "error": "profile 至少两个点 [[r_mm, z_mm], …]"})
        nm = str(name or "Revolved")
        o0 = bpy.data.objects.get(nm)
        if o0 is not None:
            me0 = o0.data
            bpy.data.objects.remove(o0, do_unlink=True)
            if me0 is not None and me0.users == 0:
                bpy.data.meshes.remove(me0, do_unlink=True)
        bm = _bm.new()
        n = max(6, int(segments))
        sweep = max(1e-6, math.radians(float(angle_deg)))
        rings = []
        closed = abs(float(angle_deg) - 360.0) < 1e-6
        for i in range(n if closed else n + 1):
            th = sweep * i / float(n)
            ring = []
            for (r, z) in pts:
                if r <= 1e-9:
                    ring.append(bm.verts.new((0.0, 0.0, z)))
                else:
                    ring.append(bm.verts.new((r * math.cos(th), r * math.sin(th), z)))
            rings.append(ring)
        for k in range(len(rings) - 1 if not closed else len(rings)):
            a = rings[k]
            b = rings[(k + 1) % len(rings)]
            for i in range(len(a) - 1):
                try:
                    bm.faces.new((a[i], a[i + 1], b[i + 1], b[i]))
                except Exception:
                    pass
        _bm.ops.remove_doubles(bm, verts=bm.verts, dist=1e-6)
        _bm.ops.recalc_face_normals(bm, faces=bm.faces)
        me = bpy.data.meshes.new(nm)
        bm.to_mesh(me)
        bm.free()
        ob = bpy.data.objects.new(nm, me)
        bpy.context.scene.collection.objects.link(ob)
        d = [round(float(x) * 1000.0, 1) for x in ob.dimensions]
        return _j({"ok": True, "object": ob.name, "segments": n, "profile_pts": len(pts),
                   "size_mm": {"x": d[0], "y": d[1], "h": d[2]}, "vertices": len(ob.data.vertices),
                   "note": "旋转体通路：母线来自 reference 轮廓（逐行宽度的一半 = 半径）"})
    except Exception as e:
        return _j({"ok": False, "error": "%s: %s" % (type(e).__name__, str(e)[:220])})


# ── S0 粗评估（draft recon）★ 参考图还原的**第一步** ────────────────────────────
# 动机（实测教训）：一上来就 img_rectify/shape_sections 量 mm、跑 audit_* 数对象与面数，
#   在**还没有参考图**的阶段等于把编出来的数字当成规格；后续图来了又要推翻重来。
# 正确姿势：先"在脑子里打草稿"——只判**是什么 / 大概多大 / 拆成几块 / 各是什么形 / 空间上哪凸哪凹**，
#   全部带 confidence 与 source，**允许 unknown，禁止写死**。
# 本契约把该步固化成机械可检的东西：
#   R1 尺寸只给对数档 A–F（禁止 mm/cm 级绝对值）；
#   R2 禁止在本阶段调 audit_*/print_report（数对象/面数）与量站表算子；
#   R3 每字段带 confidence + source，每件带 to_confirm（= 图来了要补什么）。
import re as _re

_DRAFT_MM_RE = _re.compile(r"\d+(\.\d+)?\s*(mm|毫米|cm|厘米)", _re.I)
_DRAFT_AUTO_RE = _re.compile(r"^[A-Za-z_]+\.\d{3}$")

DRAFT_STAGE = "S0"

SIZE_CLASSES = [
    {"id": "A", "range": "<10cm",   "anchor": "掌上小物（首饰/零件/打火机）"},
    {"id": "B", "range": "10–30cm", "anchor": "单手可持（手枪/手柄/小道具）"},
    {"id": "C", "range": "0.3–1m",  "anchor": "双手或怀抱（长枪/椅子/小家电）"},
    {"id": "D", "range": "1–3m",    "anchor": "一人高到两人长（人/摩托/沙发/小艇）"},
    {"id": "E", "range": "3–10m",   "anchor": "车辆级（轿车/装甲车/小船/房间）"},
    {"id": "F", "range": ">10m",    "anchor": "建筑/舰船/飞行器级"},
]

DRAFT_FORMS = [
    {"id": "faceted",    "judge": "有明显棱线与平面拼装（装甲/军模/机械板件）",
     "impl": "基本体 + 布尔 → bevel(2–3 段保硬边) → audit_mesh"},
    {"id": "smooth",     "judge": "连续大曲率但仍是工程体（钣金/消费电子/圆润道具）",
     "impl": "Bevel(2) → SubSurf(2) 或 crease_lines(radius_mm=…)（radius 到 S3 才给）"},
    {"id": "organic",    "judge": "无棱线、体积感强、生物形态",
     "impl": "sculpt_setup(VOXEL) → sculpt_apply(位移笔刷)；别用布尔拼体积"},
    {"id": "rotational", "judge": "绕一条轴旋转得到（瓶/罐/轮毂/喷口/消焰器）",
     "impl": "shape_revolve（母线绕轴）—— 别用放样"},
    {"id": "plate",      "judge": "一维长、截面基本不变（板/管/翼型/机匣）",
     "impl": "section_outline/超椭圆 + 特征线 → shape_loft"},
    {"id": "repeats",    "judge": "同形件规律排布（履带/散热片/齿/铆钉）",
     "impl": "generator_save→run（改参不改码）或 Array+Curve"},
    {"id": "sweep",      "judge": "沿路径走的细长件（管路/线缆/轨道/护栏）",
     "impl": "sweep_analyze（先算弯折半径）→ sweep_build"},
]

DRAFT_FEATURES = [
    {"id": "void_through", "judge": "能透过去看到背景", "attrs": ["通/盲", "位置(部件+上中下)", "数量级 1|数个|阵列"],
     "impl": "布尔（先 destructive_guard 判别）或直接放样成形"},
    {"id": "boss",         "judge": "局部外凸",         "attrs": ["形状 块|柱|肋", "位置", "长径比档"],
     "impl": "基本体 + 并集 + 倒角"},
    {"id": "recess",       "judge": "局部内凹",         "attrs": ["开口形状", "位置", "是否槽"],
     "impl": "布尔差 / inset_lines（inset_mm 到 S3 才给）"},
    {"id": "seam",         "judge": "两件之间的缝/线",   "attrs": ["走向 纵|横|环", "是否贯穿"],
     "impl": "shape_panels 出真缝 → clear_check（到 S5 才量缝宽）"},
    {"id": "thin_wall",    "judge": "一片板状结构",      "attrs": ["位置", "是否要 3D 打印"],
     "impl": "放样/挤出成板；壁厚走 print_report（**到 S5 才量**）"},
    {"id": "grille",       "judge": "周期性孔阵/格栅",   "attrs": ["行列数量级", "朝向"],
     "impl": "generator_* 或 Array + 批量布尔"},
]

DRAFT_ROLES = ["主体", "配件", "重复件"]

DRAFT_RULES = [
    {"id": "R1", "rule": "不量精确尺寸", "check": "尺寸字段只能是量级档 A–F 或相对比例（如 0.3×）；出现 mm/cm 级绝对值即拒收"},
    {"id": "R2", "rule": "不数 mesh",   "check": "本阶段禁止调 audit_scene / audit_mesh / print_report（对象数·面数·顶点数）；只允许 part_count_hint 这种量级估计"},
    {"id": "R3", "rule": "不定死数据",   "check": "每字段带 confidence(high|mid|low) 与 source；判不出写 unknown；每件必须带 to_confirm（驱动图来了再完善）"},
]

DRAFT_FORBIDDEN_OPS = ["audit_scene", "audit_mesh", "print_report", "print_walls", "print_overhang",
                       "shape_sections", "vehicle_sections", "img_rectify", "vehicle_package"]

DRAFT_ITERATION = [
    "S0 草稿（无图，本步）：T1 全量 + T2 骨架 + T3/T4 定性；fidelity=draft，全部 provisional",
    "S1 读图：img_scan 只拿 bbox/长宽比/主轴角 —— 像素 ≠ 毫米，仍不给 mm",
    "S2 标定（第一次有数字）：img_rectify → mm_per_px → 包络，provisional 逐步转 confirmed",
    "S3 站表/截面：shape_sections + shape_plan 给的 section_path（此时才允许 mm）",
    "S4 出图对照：shape_loft → qc_render_views(ref_path=) 逐视图 IoU + 特征线对照",
    "S5 验收：audit_* / gate_run（这一步才数面数、量间隙）",
]

_DRAFT_T1 = [
    {"k": "name", "ask": "这是什么东西", "how": "读需求文字 / 看图直接命名", "precision": "定性", "refine": "基本不变"},
    {"k": "class", "ask": "归到哪一类", "how": "映射 shape_plan 的 10 类；可给 1 主 + 1 备选", "precision": "粗归类", "refine": "图来了复核 section_path"},
    {"k": "size_class", "ask": "现实大概多大", "how": "对数档 A–F + 人体尺度锚（掌上/单手/怀抱/一人高/车长/建筑）", "precision": "只给档位，禁止 mm", "refine": "S2 标定后才有 mm"},
    {"k": "size_ref", "ask": "拿什么做参照", "how": "人类尺度锚，不写数字", "precision": "定性锚点", "refine": "换成实测包络"},
    {"k": "symmetry", "ask": "对称性", "how": "双侧 / 轴对称 / 单侧手性 / 无", "precision": "定性", "refine": "复核"},
    {"k": "orientation", "ask": "轴向语义", "how": "长轴·宽轴·薄轴 + 哪根朝上/朝前（写清项目坐标，如 +X=枪口）", "precision": "定性", "refine": "冻结进 spec"},
    {"k": "confidence", "ask": "把握多大", "how": "high/mid/low", "precision": "—", "refine": "逐轮提升"},
]

_DRAFT_T2 = [
    {"k": "part", "ask": "这块叫什么", "how": "语义命名（禁止 Cube.001 这类）", "precision": "定性", "refine": "沿用为对象命名"},
    {"k": "role", "ask": "主体还是配件", "how": "判据（定性、不算体积）：① 占包络主要体量 ② 被 2 个以上件挂靠 ③ 少了它整体不成立 —— 满足①即主体，其余为配件/重复件", "precision": "主体唯一（多体量则并列），可改", "refine": "装配后复核"},
    {"k": "parent", "ask": "挂在哪", "how": "装配树（主体为 root）", "precision": "粗，可改", "refine": "接口表（spec）"},
    {"k": "size_class", "ask": "该件量级", "how": "同 T1 的 A–F 档", "precision": "档位", "refine": "S2 标定"},
    {"k": "rel", "ask": "相对主体的比例", "how": "目测比例，如 1.0× / 0.3×（标 approx）", "precision": "粗", "refine": "实测修正"},
    {"k": "form_class", "ask": "什么形", "how": "见 T3（一个物体常是混合体：按件分别标）", "precision": "定性", "refine": "S3 选截面通路"},
    {"k": "features", "ask": "有什么空间特征", "how": "见 T4；只记 通/盲、位置档、数量级", "precision": "定性", "refine": "S3 给尺寸"},
    {"k": "build_path", "ask": "怎么做", "how": "填算子链：shape_plan(object_class=…) 给的 section_path + 技能配方（见 T3 的 impl）", "precision": "粗选路子，可改", "refine": "冻结进 spec"},
    {"k": "evidence", "ask": "凭什么这么判", "how": "写清来源：需求文字 / 参考图第 N 张 / 常识先验", "precision": "必填，不许空", "refine": "图来了补图号"},
    {"k": "to_confirm", "ask": "图来了要定什么", "how": "例：截面形状 / 该特征出现几次 / 是否有第二处同类", "precision": "必填（这列驱动迭代）", "refine": "逐条销项"},
    {"k": "confidence", "ask": "把握多大", "how": "high/mid/low", "precision": "—", "refine": "逐轮提升"},
]


def _draft_record_skeleton(object_class=None):
    return {
        "object": {"name": None, "class": (object_class or None), "size_class": None, "size_ref": None,
                   "symmetry": None, "orientation": None, "confidence": None, "source": None},
        "parts": [{"part": None, "role": None, "parent": None, "size_class": None, "rel": None,
                   "form_class": None, "features": [], "build_path": None,
                   "evidence": None, "to_confirm": [], "confidence": None}],
        "part_count_hint": None,
    }


def shape_draft(object_class=None, hint=None):
    """S0 草稿评估：给「制作表格」的字段契约 + 可填骨架。**本 op 不返回任何测量值。**

    object_class 可选（vehicle/humanoid/… 之一）—— 给了就附带该类的"关注项"（要盯哪些比例、
    建议哪条截面通路），但**只是关注项的名字，不含数字**；数值一律等 S2 标定之后再取。
    """
    c = str(object_class).lower() if object_class else None
    focus = None
    unknown_class = None
    if c:
        if c in CLASSES:
            row = CLASSES[c]
            focus = {"object_class": c, "views": row.get("views"), "symmetric": row.get("symmetric"),
                     "volumes": row.get("volumes"), "focus_ratios": row.get("ratios"),
                     "section_path": SECTION_PATH.get(c), "traps": row.get("traps"),
                     "note": "以上是**关注项的名字**（该盯什么），不是测量值；数值等 S2 标定后再取"}
        else:
            unknown_class = {"object_class": object_class, "known": sorted(CLASSES)}
    out = {
        "ok": True, "op": "shape_draft", "stage": DRAFT_STAGE, "fidelity": "draft", "revisable": True,
        "what": "参考图还原的**第一步**：先出一张制作表格（是什么/大概多大/拆成几块/各是什么形/哪凸哪凹），"
                "像在脑子里打草稿 —— **不要把数据定死**，图来了再逐行补数字",
        "why_draft": "此阶段通常还没有参考图；先量 mm / 数 mesh 等于把编出来的数字当规格，图来了必推翻重来",
        "rules": DRAFT_RULES,
        "forbidden_ops_this_stage": DRAFT_FORBIDDEN_OPS,
        "forbidden_note": "以上算子在 S0 阶段**不要调**（它们会产出精确尺寸或对象/面数统计）；到 S3/S5 才用",
        "enums": {"size_class": SIZE_CLASSES, "form_class": DRAFT_FORMS, "feature_kind": DRAFT_FEATURES,
                  "role": DRAFT_ROLES, "confidence": ["high", "mid", "low"]},
        "tables": {
            "T1_object": {"one_row": True, "fields": _DRAFT_T1,
                          "done_when": "name + class + size_class 三项齐 = T1 完成（不要求任何 mm 数字）"},
            "T2_parts": {"one_row_per_part": True, "fields": _DRAFT_T2,
                         "done_when": "每件都有 role/form_class/evidence/to_confirm = T2 完成；"
                                      "这张表同时是后续分工与接口表的雏形"},
            "T3_form": {"one_row_per_part": True, "enums": DRAFT_FORMS,
                        "rule": "一个物体常是混合体（如角色=有机身体+硬表面装备）⇒ 按件分别标，不要求整体一个答案"},
            "T4_features": {"one_row_per_feature": True, "enums": DRAFT_FEATURES,
                            "rule": "只定位到「部件 + 上中下/前后」与数量级；孔径/深度/壁厚留到 S3/S5"},
        },
        "record": _draft_record_skeleton(c if (c and c in CLASSES) else None),
        "iteration": DRAFT_ITERATION,
        "next": "① 把 record 填成表格 → shape_draft_check(record=…) 机械校验；"
                "② 拿到参考图 → S1 img_scan（只拿轮廓/比例，不给 mm）→ S2 img_rectify 标定",
    }
    if focus:
        out["focus"] = focus
    if unknown_class:
        out["unknown_class"] = unknown_class
        out["warning"] = "未知 object_class=%s —— 仍可用 generic 关注项（先量长/宽/高三个包络比）" % object_class
    if hint:
        out["hint_echo"] = str(hint)[:200]

    return _j(out)


def _draft_scan_absolute(obj, path="", hits=None):
    """递归找 mm/cm 级绝对值（key 以 _mm/_cm 结尾，或字符串里带数字+单位）。"""
    if hits is None:
        hits = []
    if isinstance(obj, dict):
        for k, v in obj.items():
            kp = path + "." + str(k)
            lk = str(k).lower()
            if lk.endswith("_mm") or lk.endswith("_cm") or lk in ("mm", "cm"):
                hits.append(kp)
            _draft_scan_absolute(v, kp, hits)
    elif isinstance(obj, (list, tuple)):
        for i, v in enumerate(obj):
            _draft_scan_absolute(v, "%s[%d]" % (path, i), hits)
    elif isinstance(obj, str):
        if _DRAFT_MM_RE.search(obj):
            hits.append("%s = %s" % (path, obj[:48]))
    return hits


def shape_draft_check(record=None):
    """校验 S0 草稿表：字段齐不齐 + 有没有违规（mm 级尺寸 / 面数统计口径）。

    record 可传 dict 或 JSON 字符串（与 shape_draft 返回的 record 骨架同形）。
    返回 errors（必须改）/ warnings（建议看），**不修改场景**。
    """
    if isinstance(record, str):
        try:
            record = json.loads(record)
        except Exception as e:
            return _j({"ok": False, "stage": DRAFT_STAGE, "error": "record 不是合法 JSON: %s" % str(e)[:160]})
    if record is None:
        return _j({"ok": False, "stage": DRAFT_STAGE,
                   "error": "缺 record —— 先 shape_draft() 拿骨架，填好后传 record={object:…, parts:[…]}"})
    if not isinstance(record, dict):
        return _j({"ok": False, "stage": DRAFT_STAGE, "error": "record 必须是对象(dict)"})

    errors, warnings = [], []
    valid_size = {x["id"] for x in SIZE_CLASSES}
    valid_form = {x["id"] for x in DRAFT_FORMS}
    valid_feat = {x["id"] for x in DRAFT_FEATURES}
    valid_conf = {"high", "mid", "low"}

    obj = record.get("object")
    if not isinstance(obj, dict):
        errors.append("缺 object 段（T1）")
        obj = {}
    for k in ("name", "class", "size_class", "size_ref", "confidence"):
        if not obj.get(k):
            errors.append("object.%s 缺失（T1 未完成）" % k)
    if obj.get("class") and str(obj["class"]).lower() not in CLASSES:
        errors.append("object.class=%s 不在 shape_plan 的 10 类里（%s）" % (obj["class"], ",".join(sorted(CLASSES))))
    if obj.get("size_class") and str(obj["size_class"]).upper() not in valid_size:
        errors.append("object.size_class=%s 不是 A–F 档（size_class 只收档位，不收数字）" % obj["size_class"])
    if obj.get("confidence") and str(obj["confidence"]).lower() not in valid_conf:
        errors.append("object.confidence=%s 不是 high/mid/low" % obj["confidence"])

    parts = record.get("parts")
    if not isinstance(parts, list) or not parts:
        errors.append("parts 为空（T2 至少要有一行，主体也算一行）")
        parts = []
    n_main = 0
    for i, p in enumerate(parts):
        tag = "parts[%d]" % i
        if not isinstance(p, dict):
            errors.append("%s 不是对象" % tag)
            continue
        pname = str(p.get("part") or "").strip()
        if not pname:
            errors.append("%s.part 缺失（必须先命名）" % tag)
        elif _DRAFT_AUTO_RE.match(pname):
            warnings.append("%s.part=%s 像自动名，改成语义命名" % (tag, pname))
        for k in ("role", "form_class", "evidence", "to_confirm", "confidence"):
            v = p.get(k)
            if v is None or (isinstance(v, str) and not v.strip()) or (isinstance(v, list) and not v):
                errors.append("%s.%s 缺失（T2 每件都要有 role/form_class/evidence/to_confirm/confidence）" % (tag, k))
        role = str(p.get("role") or "")
        if role and role not in DRAFT_ROLES:
            errors.append("%s.role=%s 不是 %s 之一" % (tag, role, "/".join(DRAFT_ROLES)))
        if role == "主体":
            n_main += 1
        fc = str(p.get("form_class") or "")
        if fc and fc not in valid_form:
            errors.append("%s.form_class=%s 不在 %s" % (tag, fc, ",".join(sorted(valid_form))))
        if str(p.get("confidence") or "").lower() not in valid_conf and p.get("confidence"):
            errors.append("%s.confidence=%s 不是 high/mid/low" % (tag, p["confidence"]))
        feats = p.get("features")
        if isinstance(feats, list):
            for j, f in enumerate(feats):
                if not isinstance(f, dict):
                    errors.append("%s.features[%d] 不是对象" % (tag, j))
                    continue
                fk = str(f.get("kind") or "")
                if fk and fk not in valid_feat:
                    errors.append("%s.features[%d].kind=%s 不在 %s" % (tag, j, fk, ",".join(sorted(valid_feat))))
                if not f.get("where"):
                    warnings.append("%s.features[%d] 没写 where（位置）—— 至少要写部件+上中下" % (tag, j))
    if n_main == 0:
        warnings.append("没有任何 part 标 role=主体 —— 判定规则：占包络主要体量 的那件是主体")
    if n_main > 1:
        warnings.append("有 %d 件标了主体 —— 多体量可以并列，但要确认它们不是同一件的分块" % n_main)

    mm_hits = _draft_scan_absolute(record)
    if mm_hits:
        errors.append("R1 违规：出现 mm/cm 级绝对值 %s —— S0 只收量级档 A–F 或相对比例（如 0.3×）"
                      % (", ".join(sorted(set(mm_hits))[:6])))

    stats = {"parts": len(parts), "main": n_main,
             "forms": sorted({str(p.get("form_class")) for p in parts if isinstance(p, dict) and p.get("form_class")}),
             "feature_rows": sum(len(p.get("features") or []) for p in parts if isinstance(p, dict))}
    return _j({"ok": not errors, "stage": DRAFT_STAGE, "fidelity": "draft",
               "errors": errors, "warnings": warnings, "stats": stats,
               "note": "ok=true 表示「草稿表填得合格、且没有把数据定死」；**不代表尺寸被验证过** —— "
                       "数字一律等 S2 标定/S5 验收"})


def shape_selftest():
    """自检：协议齐全（每类都有视图/比例/算子/验收/坑）+ 三条通路（量具/放样/旋转体）可用。"""
    import bpy
    ev = {}
    try:
        missing = []
        for k, row in CLASSES.items():
            for key in ("views", "ratios", "ops", "gates", "traps"):
                if not row.get(key):
                    missing.append("%s.%s" % (k, key))
        missing_path = [k for k in CLASSES if not SECTION_PATH.get(k)]
        ev["classes"] = len(CLASSES)
        ev["missing_section_path"] = missing_path
        ev["decision_rules"] = len(DECISION)
        ev["missing_fields"] = missing
        p = json.loads(shape_plan("generic"))
        ev["plan_generic"] = {"ok": p.get("ok"), "steps": len(p.get("steps") or [])}
        rv = json.loads(shape_revolve(profile=[[0, 0], [30, 0], [30, 60], [20, 90], [0, 100]], name="__dsh_rv"))
        ev["revolve"] = {k: rv.get(k) for k in ("ok", "size_mm", "vertices", "error")}
        # S0 草稿契约：模板要齐（四张表 + 规则 + 禁止项），且 check 必须"合法过 / 写 mm 被拒"
        dr = json.loads(shape_draft("furniture"))
        good = {"object": {"name": "样例", "class": "furniture", "size_class": "C",
                           "size_ref": "怀抱", "confidence": "mid"},
                "parts": [{"part": "座面", "role": "主体", "form_class": "plate", "evidence": "需求文字",
                           "to_confirm": ["座面形状"], "confidence": "mid"}]}
        bad = {"object": dict(good["object"]), "parts": [dict(good["parts"][0], rel="450mm")]}
        ck_ok = json.loads(shape_draft_check(good))
        ck_bad = json.loads(shape_draft_check(bad))
        ev["draft"] = {"ok": dr.get("ok"), "tables": sorted((dr.get("tables") or {}).keys()),
                       "rules": len(dr.get("rules") or []),
                       "forbidden": len(dr.get("forbidden_ops_this_stage") or []),
                       "check_good": ck_ok.get("ok"), "check_mm_rejected": ck_bad.get("ok") is False}
        ok = (not missing and not missing_path and len(DECISION) >= 4
              and p.get("ok") is True and rv.get("ok") is True
              and abs((rv.get("size_mm") or {}).get("h", 0) - 100.0) < 1.0
              and dr.get("ok") is True and len(dr.get("tables") or {}) == 4
              and ck_ok.get("ok") is True and ck_bad.get("ok") is False)
        return _j({"ok": bool(ok), "evidence": ev})
    except Exception as e:
        return _j({"ok": False, "evidence": ev, "error": "%s: %s" % (type(e).__name__, str(e)[:220])})
    finally:
        o = bpy.data.objects.get("__dsh_rv")
        if o is not None:
            me = o.data
            bpy.data.objects.remove(o, do_unlink=True)
            if me is not None and me.users == 0:
                try:
                    bpy.data.meshes.remove(me, do_unlink=True)
                except Exception:
                    pass


def shape_help():
    return _j({
        "module": "shapegen.py", "version": SHAPE_VERSION,
        "what": "参考图**通用**还原方法层：类别协议 + 通用量具/放样/拟合/分件/旋转体",
        "ops": ["shape_draft", "shape_draft_check", "shape_plan", "shape_sections", "shape_loft",
                "shape_fit", "shape_regions", "shape_panels", "shape_revolve", "shape_selftest", "shape_help"],
        "steps": STEPS, "classes": sorted(CLASSES),
        "section_decision": DECISION, "section_path": SECTION_PATH,
        "why": "几何机制与类别无关：量轮廓 → 放样 → 特征线 → 验收；换类别只换协议（视图/比例/验收）",
        "draft_stage": "第一步永远是 S0 草稿评估（shape_draft）：先出制作表格，**不量精确尺寸、不数 mesh、不写死数字**；"
                       "尺寸只给 A–F 量级档，每件必带 evidence 与 to_confirm —— 图来了再逐行补数字",
        "applies_to": ["有明确外轮廓、能用正交视图描述的物体：车辆/人形/动物/家具/头盔/船体/飞机/枪械/道具"],
        "not_applies": ["没有外轮廓的（布料/毛发/流体/烟）", "靠内部结构定义的（多孔晶格/拓扑优化件）"],
        "rotational_note": "瓶/罐/轮毂/花瓶这类**轴对称**物体走 shape_revolve（母線旋转），不要用放样",
    })


def shape_dispatch(op, args_json):
    args = {}
    if isinstance(args_json, str) and args_json.strip():
        try:
            args = json.loads(args_json)
        except Exception as e:
            return _j({"ok": False, "error": "args 不是合法 JSON: %s" % str(e)[:120]})
    kw = {}
    for k, v in (args or {}).items():
        if k == "args" and isinstance(v, dict):
            kw.update(v)
        else:
            kw[k] = v
    ops = {"plan": shape_plan, "sections": shape_sections, "loft": shape_loft, "fit": shape_fit,
           "regions": shape_regions, "panels": shape_panels, "revolve": shape_revolve,
           "draft": shape_draft, "draft_check": shape_draft_check,
           "selftest": shape_selftest, "help": shape_help}
    fn = ops.get(str(op))
    if fn is None:
        return _j({"ok": False, "error": "unknown shape op", "op": op, "ops": sorted(ops)})
    try:
        return fn(**kw)
    except Exception as e:
        return _j({"ok": False, "error": "%s: %s" % (type(e).__name__, str(e)[:220]), "op": op})


_DshApi = _KIT.Api  # 共享内核（尾部注册行无需改）
import sys as _sys
_K = _sys.modules.get("dsh_rt_kernel")
if _K is not None:
    _K.dsh_shape_api = _DshApi({"version": SHAPE_VERSION, "dispatch": shape_dispatch,
                                "draft": shape_draft, "draft_check": shape_draft_check,
                                "plan": shape_plan, "sections": shape_sections, "loft": shape_loft,
                                "fit": shape_fit, "revolve": shape_revolve, "selftest": shape_selftest,
                                "help": shape_help})