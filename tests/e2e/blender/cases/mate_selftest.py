# -*- coding: utf-8 -*-
"""S3 自检：配合门（mate_check）/ 装配特征库（fit_help）/ 干涉报告（interference_report）。

跑法：
    blender_rt_headless(preload="view,contract", engine="none", outdir="D:\\DSH\\blender\\tmp\\mate_new",
        script="_p=K.win_path('/home/sixtyseven67/DSH/dsh-blender-plugin/tests/mate_selftest.py');"
               "exec(compile(open(_p,encoding='utf-8').read(),'x','exec'),globals())")
    # 或 blender -b --factory-startup --python tests/mate_selftest.py

合成场景（绝不碰用户 GUI 场景：--factory-startup + 开头 clean()）：
    MPlate/MPlate 真孔（cylinder + Boolean DIFFERENCE 并 Apply）+ MPin  = 正常 location 配合
    同一对挪远 5 mm                                                    = 没搭上
    DPlate/DPlate 盲孔 + DPin 顶到孔底再深 3 mm                          = 深穿模
    IX/IY 深度穿插（4 mm）· JX/JY 浅穿插（0.1 mm，同父件 JParent）        = 干涉报告 + 白名单 + 分组

判据不改松：断言全部对着"实测数字"写（接触占比、中位间隙、穿透深度），不达标就改代码不改断言。
"""
import bpy, json, math, os, sys

FAILS = []
OKS = []
MEAS = {}


def check(name, cond, detail=""):
    (OKS if cond else FAILS).append({"name": name, "detail": detail})
    print(("  ok   " if cond else "  FAIL ") + name + ("" if cond else "  <- " + str(detail)))


def box(name, size, loc):
    bpy.ops.mesh.primitive_cube_add(size=1.0, location=loc)
    ob = bpy.context.object
    ob.name = name
    ob.scale = size
    ob.select_set(False)
    return ob


def cyl(name, r, depth, loc, verts=64):
    bpy.ops.mesh.primitive_cylinder_add(vertices=verts, radius=r, depth=depth, location=loc)
    ob = bpy.context.object
    ob.name = name
    ob.select_set(False)
    return ob


def cut(target, cutter, label=""):
    """target 减去 cutter（一次 Boolean DIFFERENCE 并 Apply）—— 造"真孔"，不是拿 bbox 假装有孔。"""
    bpy.context.view_layer.objects.active = target
    target.select_set(True)
    m = target.modifiers.new("cut_%s" % (label or cutter.name), "BOOLEAN")
    m.operation = "DIFFERENCE"
    m.object = cutter
    try:
        m.solver = "EXACT"
    except Exception:
        pass
    bpy.ops.object.modifier_apply(modifier=m.name)
    target.select_set(False)
    me = cutter.data
    bpy.data.objects.remove(cutter, do_unlink=True)
    try:
        bpy.data.meshes.remove(me)
    except Exception:
        pass


def clean():
    bpy.ops.object.select_all(action="SELECT")
    bpy.ops.object.delete()
    for m in list(bpy.data.meshes):
        bpy.data.meshes.remove(m)
    bpy.context.view_layer.update()


def upd():
    bpy.context.view_layer.update()
    bpy.context.evaluated_depsgraph_get().update()


def find_pair(rows, a, b):
    for r in rows:
        if {r.get("a"), r.get("b")} == {a, b}:
            return r
    return None


def main():
    outdir = None
    if "--" in sys.argv:
        rest = sys.argv[sys.argv.index("--") + 1:]
        outdir = rest[-1] if rest else None
    outdir = outdir or os.path.join(os.path.expanduser("~"), "mate_selftest_out")
    os.makedirs(outdir, exist_ok=True)

    K = sys.modules.get("dsh_rt_kernel")
    if K is None or not hasattr(K, "dsh_contract_api"):
        here = os.path.dirname(os.path.abspath(__file__))
        src = open(os.path.join(os.path.dirname(here), "runtime", "contract.py"), encoding="utf-8").read()
        exec(compile(src, "contract.py", "exec"), globals())
    api = K.dsh_contract_api
    print("MATE_SELFTEST contract_version=%s" % api["version"])

    # ---------------------------------------------------------------- 场景
    clean()
    # 正常配合：20×20×4 mm 板 + 真通孔（r = 2 mm nominal + 0.15 mm 单边 fit）+ 同轴销（r = 2 mm）
    mplate = box("MPlate", (0.020, 0.020, 0.004), (0, 0, 0))
    cut(mplate, cyl("MPlateCutter", 0.00215, 0.010, (0, 0, 0)), "hole")
    mpin = cyl("MPin", 0.002, 0.0035, (0, 0, 0))
    # 深穿模：20 mm 立方 + 盲孔（顶面往下 6 mm）+ 销顶到孔底再深 3 mm
    dplate = box("DPlate", (0.020, 0.020, 0.020), (0.060, 0, 0))
    cut(dplate, cyl("DPlateCutter", 0.00215, 0.007, (0.060, 0, 0.0075)), "blind")
    dpin = cyl("DPin", 0.002, 0.012, (0.060, 0, 0.007))
    # 干涉：IX/IY 深穿插 4 mm；JX/JY 浅穿插 0.1 mm（同父件 JParent）
    box("IX", (0.010, 0.010, 0.010), (0.200, 0, 0))
    box("IY", (0.010, 0.010, 0.010), (0.206, 0, 0))
    jx = box("JX", (0.010, 0.010, 0.010), (0.300, 0, 0))
    jy = box("JY", (0.010, 0.010, 0.010), (0.3099, 0, 0))
    bpy.ops.object.empty_add(location=(0, 0, 0))
    jp = bpy.context.object
    jp.name = "JParent"
    jx.parent = jp
    jy.parent = jp
    upd()

    r = json.loads(api["reset"]())
    check("reset 清空契约", r.get("ok") and r.get("reset"))

    api["register_component"]("plate", ["MPlate"], ["plate"])
    api["register_component"]("pin", ["MPin"], ["pin"])
    api["register_component"]("dplate", ["DPlate"], ["plate"])
    api["register_component"]("dpin", ["DPin"], ["pin"])
    r = json.loads(api["register_connection"]("pin_fit", "plate", "pin", ["insert"], {}, [], "medium", [], "proposed"))
    check("注册连接 pin_fit", r.get("ok"))
    r = json.loads(api["register_connection"]("deep_fit", "dplate", "dpin", ["insert"], {}, [], "medium", [], "proposed"))
    check("注册连接 deep_fit", r.get("ok"))

    # ---------------------------------------------------------------- A. 配合门：正常
    m = json.loads(api["mate_check"]("pin_fit", fit="location"))
    ev = m.get("evidence") or {}
    ct = ev.get("contact") or {}
    gp = ev.get("gap_mm") or {}
    pen = ev.get("penetration") or {}
    MEAS["normal"] = {"verdict": m.get("verdict"), "why": m.get("why"),
                      "contact_area_fraction": ct.get("contact_area_fraction"),
                      "frac_a": ct.get("area_fraction_a"), "frac_b": ct.get("area_fraction_b"),
                      "median_gap_mm": gp.get("median_mm"), "mean_gap_mm": gp.get("mean_mm"),
                      "gap_n": gp.get("n"), "depth_max_mm": pen.get("depth_max_mm"),
                      "contact_max_mm": ev.get("contact_max_mm"), "samples": ev.get("samples"),
                      "closed": (ev.get("objects") or {}).get("closed"),
                      "anchor": ct.get("anchor_world"), "normal": ct.get("contact_normal")}
    print("  meas 正常配合: %s" % json.dumps(MEAS["normal"], ensure_ascii=False))
    check("场景：母件真孔布尔成功（求值网格闭合）", ((ev.get("objects") or {}).get("closed") or {}).get("a") is True,
          ev.get("objects"))
    check("正常配合：verdict=supported", m.get("verdict") == "supported", m.get("why"))
    check("正常配合：contact_area_fraction > 0（实测 %.3f）" % (ct.get("contact_area_fraction") or 0),
          (ct.get("contact_area_fraction") or 0) > 0, ct)
    check("正常配合：双向占比取较大者（A %.3f / B %.3f）" % (ct.get("area_fraction_a") or 0, ct.get("area_fraction_b") or 0),
          abs((ct.get("contact_area_fraction") or 0) - max(ct.get("area_fraction_a") or 0,
                                                           ct.get("area_fraction_b") or 0)) < 1e-9, ct)
    check("正常配合：单边中位间隙落在 0.15±0.15 mm（实测 %.4f，n=%s）" % (gp.get("median_mm") or -1, gp.get("n")),
          gp.get("median_mm") is not None and 0.0 <= gp["median_mm"] <= 0.30, gp)
    check("正常配合：接触锚点/接触法向都给出世界坐标", bool(ct.get("anchor_world")) and bool(ct.get("contact_normal")), ct)
    check("正常配合：合并对角线口径 contact_max = 1.5%% 对角线（%.4f mm）" % (ev.get("contact_max_mm") or 0),
          abs((ev.get("contact_max_mm") or 0) - 0.015 * (ev.get("merged_diagonal_mm") or 0)) < 0.02, ev)
    check("采样：samples_used 如实回传且 ≤ 4000", (ev.get("samples") or {}).get("samples_used") == 4000, ev.get("samples"))
    check("正常配合：无过深穿透（max %.4f mm ≤ 0.2）" % (pen.get("depth_max_mm") or 0),
          (pen.get("depth_max_mm") or 0) <= 0.2, pen)

    # ---------------------------------------------------------------- A2. 配合门：挪远 5 mm
    loc = tuple(mpin.location)
    mpin.location = (loc[0], loc[1], loc[2] + 0.005)
    upd()
    m2 = json.loads(api["mate_check"]("pin_fit", fit="location"))
    ev2 = m2.get("evidence") or {}
    MEAS["moved_5mm"] = {"verdict": m2.get("verdict"), "why": m2.get("why"),
                         "contact_area_fraction": (ev2.get("contact") or {}).get("contact_area_fraction"),
                         "gap_n": (ev2.get("gap_mm") or {}).get("n")}
    print("  meas 挪远 5mm: %s" % json.dumps(MEAS["moved_5mm"], ensure_ascii=False))
    check("挪远 5 mm → verdict=refuted", m2.get("verdict") == "refuted", m2.get("why"))
    check("挪远 5 mm → 理由点名'没搭上'",
          any("没搭上" in w for w in (m2.get("why") or [])), m2.get("why"))
    check("挪远 5 mm → 接触点 0 个", ((ev2.get("gap_mm") or {}).get("n") or 0) == 0, ev2.get("gap_mm"))
    mpin.location = loc
    upd()

    # ---------------------------------------------------------------- A3. 配合门：深穿模
    m3 = json.loads(api["mate_check"]("deep_fit", fit="location"))
    ev3 = m3.get("evidence") or {}
    pen3 = ev3.get("penetration") or {}
    MEAS["deep"] = {"verdict": m3.get("verdict"), "why": m3.get("why"),
                    "depth_max_mm": pen3.get("depth_max_mm"), "depth_mean_mm": pen3.get("depth_mean_mm"),
                    "inside_points": pen3.get("points"),
                    "median_gap_mm": (ev3.get("gap_mm") or {}).get("median_mm")}
    print("  meas 深穿模: %s" % json.dumps(MEAS["deep"], ensure_ascii=False))
    check("深穿模：穿透深度 > 0.2 mm 门限（实测 %.3f mm）" % (pen3.get("depth_max_mm") or 0),
          (pen3.get("depth_max_mm") or 0) > 0.2, pen3)
    check("深穿模：verdict 不是 supported（=%s）" % m3.get("verdict"), m3.get("verdict") != "supported", m3.get("why"))
    check("深穿模：内部采样点被判在母材内（points=%s）" % pen3.get("points"), (pen3.get("points") or 0) > 0, pen3)

    # ---------------------------------------------------------------- A4. 配合门：证据不足
    m4 = json.loads(api["mate_check"]("pin_fit", fit="location", samples=2))
    MEAS["samples2"] = {"verdict": m4.get("verdict"), "why": m4.get("why"),
                        "samples_used": ((m4.get("evidence") or {}).get("samples") or {}).get("samples_used")}
    print("  meas samples=2: %s" % json.dumps(MEAS["samples2"], ensure_ascii=False))
    check("samples=2 → unresolved（证据不足不许二选一）", m4.get("verdict") == "unresolved", m4.get("why"))
    check("samples=2 → 理由写明采样不足", any("采样不足" in w for w in (m4.get("why") or [])), m4.get("why"))
    m5 = json.loads(api["mate_check"]("pin_fit"))
    check("不给 fit / nominal → unresolved", m5.get("verdict") == "unresolved", m5.get("why"))
    api["register_component"]("ghostc", ["NoSuchObject"])
    api["register_connection"]("ghost_conn", "ghostc", "pin")
    m6 = json.loads(api["mate_check"]("ghost_conn", fit="location"))
    check("组件为空 → ok:false + verdict=unresolved（空产出不静默成功）",
          m6.get("ok") is False and m6.get("verdict") == "unresolved", m6.get("error"))
    m7 = json.loads(api["mate_check"]("nope", fit="location"))
    check("连接未注册 → ok:false", m7.get("ok") is False, m7.get("error"))
    check("verdict 里带 shared_nominal_rule 提示", "共享标称" in (m.get("shared_nominal_rule") or ""), None)

    # ---------------------------------------------------------------- A5. check_interface 的网格块（只做加法）
    ci = json.loads(api["check_interface"]("pin_fit"))
    cm = ci.get("mesh") or {}
    MEAS["check_interface_normal"] = {"bbox_gap_m": ci.get("gap_m"), "bbox_overlap": ci.get("overlap_bbox"),
                                      "mesh_skipped": cm.get("skipped"), "gap_median_mm": cm.get("gap_median_mm"),
                                      "depth_max_mm": cm.get("depth_max_mm"), "hint": cm.get("verdict_hint")}
    print("  meas check_interface(正常): %s" % json.dumps(MEAS["check_interface_normal"], ensure_ascii=False))
    check("check_interface：老 bbox 字段仍在（向后兼容）",
          "gap_m" in ci and "gap_axis_m" in ci and "overlap_bbox" in ci and ci.get("ok") is True, list(ci.keys()))
    check("check_interface：新增 mesh 块给出网格级中位间隙 ≈ 0.15 mm（实测 %s）" % cm.get("gap_median_mm"),
          (not cm.get("skipped")) and cm.get("gap_median_mm") is not None and 0.0 <= cm["gap_median_mm"] <= 0.30, cm)
    cid = json.loads(api["check_interface"]("deep_fit"))
    cmd = cid.get("mesh") or {}
    MEAS["check_interface_deep"] = {"bbox_gap_m": cid.get("gap_m"), "bbox_overlap": cid.get("overlap_bbox"),
                                    "mesh_depth_max_mm": cmd.get("depth_max_mm"),
                                    "mesh_gap_median_mm": cmd.get("gap_median_mm"), "hint": cmd.get("verdict_hint")}
    print("  meas check_interface(深穿模): %s" % json.dumps(MEAS["check_interface_deep"], ensure_ascii=False))
    check("升级动机成立：bbox 说'0 缝'而网格读出 3 mm 穿模（bbox gap_m=%s / mesh depth=%s）"
          % (cid.get("gap_m"), cmd.get("depth_max_mm")),
          cid.get("gap_m") == 0.0 and (cmd.get("depth_max_mm") or 0) > 2.0, MEAS["check_interface_deep"])
    ci3 = json.loads(api["check_interface"]("pin_fit", mesh=False))
    check("check_interface(mesh=False) → 明确报 skipped，不静默省略",
          (ci3.get("mesh") or {}).get("skipped") is True, ci3.get("mesh"))

    # ---------------------------------------------------------------- A6. 过盈档（press，负 nominal 分支）
    pplate = box("PPlate", (0.020, 0.020, 0.004), (0.120, 0, 0))
    cut(pplate, cyl("PPlateCutter", 0.00195, 0.010, (0.120, 0, 0)), "press_hole")
    cyl("PPin", 0.002, 0.0035, (0.120, 0, 0))     # 销比孔**大** 0.05 mm/边 = asm_fit("press")
    upd()
    api["register_component"]("pplate", ["PPlate"], ["plate"])
    api["register_component"]("ppin", ["PPin"], ["pin"])
    api["register_connection"]("press_fit", "pplate", "ppin", ["insert"], {}, [], "medium", [], "proposed")
    mp = json.loads(api["mate_check"]("press_fit", fit="press"))
    evp = mp.get("evidence") or {}
    penp = evp.get("penetration") or {}
    MEAS["press"] = {"verdict": mp.get("verdict"), "why": mp.get("why"),
                     "expected_gap_mm": (evp.get("fit") or {}).get("expected_gap_mm"),
                     "allowed_penetration_mm": (evp.get("fit") or {}).get("allowed_penetration_mm"),
                     "band_mm": (evp.get("fit") or {}).get("band_mm"),
                     "median_gap_mm": (evp.get("gap_mm") or {}).get("median_mm"),
                     "depth_max_mm": penp.get("depth_max_mm")}
    print("  meas 过盈档: %s" % json.dumps(MEAS["press"], ensure_ascii=False))
    check("过盈档：期望单边间隙 = -0.05 mm（负 nominal 分支）",
          (evp.get("fit") or {}).get("expected_gap_mm") == -0.05, evp.get("fit"))
    check("过盈档：实测确实有过盈（0 < 穿透 %.4f mm ≤ 允许 %.2f mm）"
          % (penp.get("depth_max_mm") or 0, (evp.get("fit") or {}).get("allowed_penetration_mm") or 0),
          0.0 < (penp.get("depth_max_mm") or 0) <= ((evp.get("fit") or {}).get("allowed_penetration_mm") or 0), penp)
    check("过盈档：设计内的过盈 → supported（=%s）" % mp.get("verdict"), mp.get("verdict") == "supported", mp.get("why"))

    # ---------------------------------------------------------------- B. 特征库
    fh = json.loads(api["fit_help"]())
    check("fit_help()：9 个特征 + 4 档 fit + 倒角公式",
          fh.get("ok") and len(fh.get("features_index") or []) == 9
          and len([k for k in (fh.get("fits") or {}) if not k.startswith("_")]) == 4
          and (fh.get("lead_chamfer") or {}).get("formula", "").find("max(0.6, 0.15") >= 0, fh.get("features_index"))
    check("fit_help()：ISO 273 分档表在（5 档）",
          len(((fh.get("iso273_clearance_diameter") or {}).get("branches")) or []) == 5, fh.get("iso273_clearance_diameter"))
    fh2 = json.loads(api["fit_help"](fit="location"))
    check("fit_help(fit='location') → 单边 0.15 mm", (fh2.get("fit_query") or {}).get("per_side_mm") == 0.15, fh2.get("fit_query"))
    fh3 = json.loads(api["fit_help"](feature="socket"))
    check("fit_help(feature='socket') → 带 Blender 侧造法",
          bool((fh3.get("feature") or {}).get("blender")), fh3.get("feature"))
    fh4 = json.loads(api["fit_help"](m=3))
    check("fit_help(m=3) → 间隙孔 3.6 mm", (fh4.get("iso273_query") or {}).get("clearance_diameter_mm") == 3.6, fh4.get("iso273_query"))
    fh5 = json.loads(api["fit_help"](d=8))
    check("fit_help(d=8) → 导向倒角 1.2 mm", (fh5.get("lead_query") or {}).get("lead_mm") == 1.2, fh5.get("lead_query"))
    fh6 = json.loads(api["fit_help"](feature="nope"))
    check("fit_help 未知特征 → ok:false + 候选清单", fh6.get("ok") is False and bool(fh6.get("available")), fh6.get("error"))

    # ---------------------------------------------------------------- C. 干涉报告
    scope = ["IX", "IY", "JX", "JY"]
    r1 = json.loads(api["interference_report"](scope=scope, use_bvh=True, limit=60))
    off = r1.get("offenders") or []
    ex = r1.get("exempted") or []
    p_ix = find_pair(off, "IX", "IY")
    p_jx = find_pair(off, "JX", "JY")
    MEAS["interference_run1"] = {"count": r1.get("count"), "pairs_aabb": r1.get("pairs_aabb"),
                                 "pairs_overlapping": r1.get("pairs_overlapping"),
                                 "IX_IY": p_ix, "JX_JY": p_jx,
                                 "groups": [{"key": g["group_key"], "pairs": g["pairs"], "worst": g["worst_severity"]}
                                            for g in (r1.get("groups") or [])]}
    print("  meas 干涉 run1: %s" % json.dumps(MEAS["interference_run1"], ensure_ascii=False))
    check("干涉：明显穿插的一对被抓出来（count=%s）" % r1.get("count"), bool(p_ix), off)
    check("干涉：未注册时 declared=false", p_ix is not None and p_ix.get("declared") is False, p_ix)
    check("干涉：IX/IY 穿透深度 ≈ 4 mm（实测 %s）" % (p_ix or {}).get("depth_mm"),
          p_ix is not None and abs((p_ix.get("depth_mm") or 0) - 4.0) < 0.05, p_ix)
    check("干涉：IX/IY 严重度 = high", p_ix is not None and p_ix.get("severity") == "high", p_ix)
    check("干涉：重叠体积占比 ≈ 0.4（实测 %s，stderr %s）" % ((p_ix or {}).get("overlap_frac_of_smaller"),
                                                              (p_ix or {}).get("overlap_stderr")),
          p_ix is not None and abs((p_ix.get("overlap_frac_of_smaller") or 0) - 0.4) < 0.05, p_ix)
    # 体积必须带**正确的**单位口径：IX/IY 重合区正好是 4×10×10 = 400 mm³（两个 10 mm 立方差 6 mm 中心距）
    check("干涉：重叠体积 mm³ 口径正确（实测 %s mm³，应为 400）" % (p_ix or {}).get("overlap_volume_mm3"),
          p_ix is not None and abs((p_ix.get("overlap_volume_mm3") or 0) - 400.0) < 5.0, p_ix)
    check("干涉：排名第 1 的是最严重的那对", off and off[0].get("rank") == 1 and {off[0]["a"], off[0]["b"]} == {"IX", "IY"}, off[:1])
    check("干涉：JX/JY 浅穿插也被报出（declared=false）",
          p_jx is not None and p_jx.get("declared") is False, p_jx)
    check("分组：同父件 JParent 的两件合成一组（groups=%s）" % len(r1.get("groups") or []),
          any(g["group_key"] == ["parent:JParent", "parent:JParent"] for g in (r1.get("groups") or [])), r1.get("groups"))
    check("干涉：exempted 为空（还没注册连接）", len(ex) == 0, ex)

    # --- 注册成连接两端后再跑：浅穿插 → 豁免；深穿插 → 仍要报
    api["register_component"]("ixa", ["IX"], ["block"])
    api["register_component"]("iya", ["IY"], ["block"])
    api["register_component"]("jxa", ["JX"], ["block"])
    api["register_component"]("jya", ["JY"], ["block"])
    api["register_connection"]("ipair", "ixa", "iya", ["contact"], {}, [], "medium", [], "proposed")
    api["register_connection"]("jpair", "jxa", "jya", ["contact"], {}, [], "medium", [], "proposed")
    r2 = json.loads(api["interference_report"](scope=scope, use_bvh=True, limit=60))
    off2 = r2.get("offenders") or []
    ex2 = r2.get("exempted") or []
    q_ix = find_pair(off2, "IX", "IY")
    q_jx_off = find_pair(off2, "JX", "JY")
    q_jx_ex = find_pair(ex2, "JX", "JY")
    MEAS["interference_run2"] = {"count": r2.get("count"), "exempted_count": r2.get("exempted_count"),
                                 "exempted": q_jx_ex, "still_offender": q_ix}
    print("  meas 干涉 run2: %s" % json.dumps(MEAS["interference_run2"], ensure_ascii=False))
    check("注册后：JX/JY 变 declared=true 并进 exempted",
          q_jx_ex is not None and q_jx_ex.get("declared") is True, ex2)
    check("注册后：JX/JY 从 offenders 移出", q_jx_off is None, off2)
    check("注册后：豁免理由带连接 id 与实测深度",
          q_jx_ex is not None and "jpair" in (q_jx_ex.get("exempt_reason") or "")
          and "mm" in (q_jx_ex.get("exempt_reason") or ""), (q_jx_ex or {}).get("exempt_reason"))
    check("注册后：深穿插（4 mm > 0.2 mm）仍留在 offenders 且 declared=true",
          q_ix is not None and q_ix.get("declared") is True, off2)

    # --- 独立通路复核（防 Goodhart）：BVH 深度 vs 纯 numpy 点-三角面深度
    r3 = json.loads(api["interference_report"](scope=scope, use_bvh=False, limit=60))
    n_ix = find_pair(r3.get("offenders") or [], "IX", "IY")
    d1 = (p_ix or {}).get("depth_mm")
    d2 = (n_ix or {}).get("depth_mm")
    MEAS["cross_check"] = {"bvh_depth_mm": d1, "numpy_depth_mm": d2}
    print("  meas 双通路复核: %s" % json.dumps(MEAS["cross_check"], ensure_ascii=False))
    check("双通路复核：BVH 与纯 numpy 深度一致（%s vs %s）" % (d1, d2),
          d1 is not None and d2 is not None and abs(d1 - d2) < 0.05, MEAS["cross_check"])
    r4 = json.loads(api["interference_report"](scope=["NoSuchPrefix"], include_hidden=False))
    check("scope 为空 → ok:false（空产出不静默成功）", r4.get("ok") is False, r4.get("error"))

    # --- 老 op 未被动过（向后兼容）
    r5 = json.loads(api["check_interference"](["IX"], use_bvh=True, limit=50))
    check("老 op check_interference 返回结构不变（pairs_aabb/pairs_bvh/pairs）",
          "pairs_aabb" in r5 and "pairs_bvh" in r5 and "pairs" in r5 and "offenders" not in r5, list(r5.keys()))
    check("help 里有三个新 op", all(k in (json.loads(api["help"]()).get("S3_ops") or {})
                                    for k in ("mate_check", "fit_help", "interference_report")), None)
    check("CONTRACT_VERSION = 3", api["version"] == 3, api["version"])

    try:
        with open(os.path.join(outdir, "mate_measurements.json"), "w", encoding="utf-8") as f:
            json.dump(MEAS, f, ensure_ascii=False, indent=2)
    except Exception as e:
        print("  warn 测量包没落盘: %s" % e)

    print("HEADLESS " + json.dumps({"ok": len(FAILS) == 0, "passed": len(OKS), "failed": len(FAILS),
                                    "failures": FAILS, "outdir": outdir}, ensure_ascii=False))


if __name__ == "__main__":
    main()