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

SHAPE_VERSION = 1

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
        ok = (not missing and not missing_path and len(DECISION) >= 4
              and p.get("ok") is True and rv.get("ok") is True
              and abs((rv.get("size_mm") or {}).get("h", 0) - 100.0) < 1.0)
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
        "ops": ["shape_plan", "shape_sections", "shape_loft", "shape_fit", "shape_regions",
                "shape_panels", "shape_revolve", "shape_selftest", "shape_help"],
        "steps": STEPS, "classes": sorted(CLASSES),
        "section_decision": DECISION, "section_path": SECTION_PATH,
        "why": "几何机制与类别无关：量轮廓 → 放样 → 特征线 → 验收；换类别只换协议（视图/比例/验收）",
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
                                "plan": shape_plan, "sections": shape_sections, "loft": shape_loft,
                                "fit": shape_fit, "revolve": shape_revolve, "selftest": shape_selftest,
                                "help": shape_help})