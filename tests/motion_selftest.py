# -*- coding: utf-8 -*-
"""运动/铰接层（runtime/motion.py）的**独立**自检 —— 不依赖模块内置自检的场景。

跑法（无头隔离进程，不碰 GUI 场景）：
    blender -b --factory-startup --python tests/motion_selftest.py -- <outdir>
    # 或插件通道：
    blender_rt_headless(preload="motion", engine="none", factory_startup=True,
        script="exec(open('<本文件>').read())", outdir="D:...")

与 motion_selftest() 的分工：模块内置自检用**销（旋转对称）**定轴 + 沿 Z 的铰链；
这里换一条**独立通路**：铰链沿 **Y**、无销、靠**父↔子接触带**定轴，外加一个 **prismatic 滑块**，
避免"用同一套几何自证同一套判据"。

覆盖：
    ① 接触带定轴（无销薄板）→ supported 且与真轴夹角 < 5°
    ② 沿 Y 扫掠零干涉 → supported；放挡块 → refuted 且点名
    ③ prismatic 直线驱动扫掠 → supported
    ④ 扫掠后逐对象 matrix_world 逐项还原（含无关对象）
    ⑤ URDF / USDA 结构自检 + 网格文件落盘
    ⑥ 空产出 / 无轴 / 未知对象的硬规则（必须 ok=false）
    ⑦ dispatch 的 {"args": {...}} 解包 + status/help 形状
"""
import bpy
import json
import math
import os
import shutil
import sys
import tempfile

FAILS = []
OKS = []


def check(name, cond, detail=""):
    (OKS if cond else FAILS).append({"name": name, "detail": detail if not cond else ""})
    print(("  ok   " if cond else "  FAIL ") + name + ("" if cond else "  <- " + json.dumps(detail, ensure_ascii=False)[:400]))


def _mk_box(name, size, loc):
    sx, sy, sz = [float(x) for x in size]
    me = bpy.data.meshes.new("MSH_" + name)
    v = [(-sx / 2, -sy / 2, -sz / 2), (sx / 2, -sy / 2, -sz / 2), (sx / 2, sy / 2, -sz / 2), (-sx / 2, sy / 2, -sz / 2),
         (-sx / 2, -sy / 2, sz / 2), (sx / 2, -sy / 2, sz / 2), (sx / 2, sy / 2, sz / 2), (-sx / 2, sy / 2, sz / 2)]
    f = [(0, 3, 2, 1), (4, 5, 6, 7), (0, 1, 5, 4), (1, 2, 6, 5), (2, 3, 7, 6), (3, 0, 4, 7)]
    me.from_pydata(v, [], f)
    me.update()
    ob = bpy.data.objects.new(name, me)
    ob.location = [float(x) for x in loc]
    bpy.context.scene.collection.objects.link(ob)
    return ob


def _snap():
    return {o.name: [float(x) for row in o.matrix_world for x in row] for o in bpy.data.objects}


def _diff(a, b, tol=1e-6):
    mx, bad = 0.0, []
    for k in sorted(set(a) | set(b)):
        va, vb = a.get(k), b.get(k)
        if va is None or vb is None:
            bad.append(k)
            continue
        d = max(abs(x - y) for x, y in zip(va, vb))
        mx = max(mx, d)
        if d > tol:
            bad.append(k)
    return {"max_delta": mx, "bad": bad, "exact": mx == 0.0}


def _angle_deg(a, b):
    import numpy as np
    a, b = np.asarray(a, float), np.asarray(b, float)
    na, nb = float((a ** 2).sum() ** 0.5), float((b ** 2).sum() ** 0.5)
    if na < 1e-30 or nb < 1e-30:
        return None
    return math.degrees(math.acos(max(-1.0, min(1.0, abs(float(a.dot(b))) / (na * nb)))))


def _dist_point_line(p, origin, axis):
    import numpy as np
    v = np.asarray(p, float) - np.asarray(origin, float)
    k = np.asarray(axis, float)
    k = k / float((k ** 2).sum() ** 0.5)
    return float(((v - k * float(v.dot(k))) ** 2).sum() ** 0.5)


def _load_api():
    """preload 已挂 K 就直接用；否则造一个假内核 + 就地 exec 源码（独立进程也能跑）。"""
    K = sys.modules.get("dsh_rt_kernel")
    if K is not None and hasattr(K, "dsh_motion_api"):
        return K, K.dsh_motion_api
    import types
    if K is None:
        K = types.ModuleType("dsh_rt_kernel")
        sys.modules["dsh_rt_kernel"] = K
    if not hasattr(K, "out_dir"):
        K.out_dir = os.environ.get("DSH_OUT") or tempfile.gettempdir()
    here = os.path.dirname(os.path.abspath(__file__))
    src = open(os.path.join(os.path.dirname(here), "runtime", "motion.py"), encoding="utf-8").read()
    g = {"__name__": "dsh_motion_inline", "__file__": os.path.join(os.path.dirname(here), "runtime", "motion.py")}
    exec(compile(src, "motion.py", "exec"), g)
    return K, K.dsh_motion_api


def main():
    outdir = None
    if "--" in sys.argv:
        rest = sys.argv[sys.argv.index("--") + 1:]
        outdir = rest[-1] if rest else None
    outdir = outdir or os.path.join(tempfile.gettempdir(), "motion_selftest_out")
    work = os.path.join(outdir, "motion_selftest_files")
    os.makedirs(work, exist_ok=True)

    K, api = _load_api()
    print("MOTION_SELFTEST version=%s api=%s" % (api["version"], sorted(api)))

    pre = sorted(o.name for o in bpy.data.objects)
    made, meshes = [], []
    try:
        # ---- 场景（铰链沿世界 Y；无销，靠接触带定轴）
        bar = _mk_box("MT_Bar", (0.08, 1.0, 0.08), (0.0, 0.0, 0.0))          # 顶面 z=0.04
        plate = _mk_box("MT_Plate", (0.04, 0.5, 0.3), (0.0, 0.0, 0.17))      # 底面插进 bar 0.02
        blocker = _mk_box("MT_Blocker", (0.10, 0.5, 0.14), (0.15, 0.0, 0.20))  # 摆在 +X 摆动路径上
        rail = _mk_box("MT_Rail", (1.2, 0.1, 0.1), (-3.0, 0.0, 0.0))         # 滑块导轨
        car = _mk_box("MT_Carriage", (0.2, 0.08, 0.08), (-3.0, 0.0, 0.10))   # 车在导轨上方 0.01
        for ob in (bar, plate, blocker, rail, car):
            made.append(ob)
            meshes.append(ob.data)
        bpy.context.view_layer.update()

        # ---- ① 接触带定轴（无销）：铰链真轴 = Y
        # 期望的铰链线 = 关节界面平面（bar 顶面 z=0.04）∩ 板中厚面（x=0）→ 沿 Y 的直线
        HINGE = {"origin": [0.0, 0.0, 0.04], "axis": [0.0, 1.0, 0.0]}
        r = json.loads(api["joint"]("mt_hinge", ["MT_Bar"], ["MT_Plate"], kind="revolute",
                                    limits=[-32, 32], snap_axis=True, note="接触带定轴（独立自检）"))
        ang = _angle_deg(r.get("axis") or [0, 0, 0], HINGE["axis"]) if r.get("axis") else None
        off = _dist_point_line(r.get("anchor") or [0, 0, 0], HINGE["origin"], HINGE["axis"])
        off_mid = _dist_point_line(r.get("anchor") or [0, 0, 0], [0.0, 0.0, 0.03], HINGE["axis"])
        check("接触带定轴 supported（无销薄板）",
              r.get("ok") and r.get("axis_verdict") == "supported" and r.get("axis_source") == "contact_region+snapped",
              {"axis_verdict": r.get("axis_verdict"), "axis_source": r.get("axis_source"),
               "warnings": r.get("warnings"), "axis_snapped_from": r.get("axis_snapped_from")})
        check("实测轴与真轴（Y）夹角 < 5°（吸附后为精确 Y）", ang is not None and ang < 5.0,
              {"angle_deg": ang, "axis": r.get("axis"), "snapped_from": r.get("axis_snapped_from")})
        check("实测锚点落在关节界面线附近（≤5mm）",
              off is not None and off <= 0.005,
              {"offset_to_interface_line": off, "offset_to_mid_seam(z=0.03)": off_mid,
               "anchor": r.get("anchor"),
               "note": "接触带在 z∈[0.02,0.04] 之间，缝中面 z=0.03 与界面面 z=0.04 相距 1cm —— 判据只保证落在界面线上"})

        # ---- ② 沿 Y 扫掠：无挡块 supported；有挡块 refuted
        before = _snap()
        m1 = json.loads(api["measure"]("mt_hinge", phases=8, sweep_deg=64,
                                       exclude=pre + ["MT_Blocker"]))
        d1 = _diff(before, _snap())
        check("无阻挡扫掠 supported 且确实位移",
              m1.get("verdict") == "supported" and m1.get("moved") is True,
              {"verdict": m1.get("verdict"), "moved": m1.get("moved"), "reason": m1.get("reason")})
        check("扫掠①后逐对象 matrix_world 还原", (m1.get("restored") or {}).get("ok") is True and not d1["bad"],
              {"restored": m1.get("restored"), "diff": d1})

        m2 = json.loads(api["measure"]("mt_hinge", phases=8, sweep_deg=64, exclude=pre))
        d2 = _diff(before, _snap())
        check("挡块进扫掠路径 → refuted 且点名 MT_Blocker",
              m2.get("verdict") == "refuted" and "MT_Blocker" in (m2.get("offenders") or []),
              {"verdict": m2.get("verdict"), "offenders": m2.get("offenders"), "reason": m2.get("reason")})
        check("干涉对最小间距为负（穿模）",
              any((c.get("min_gap_m") is not None and c["min_gap_m"] < 0) for c in (m2.get("collisions") or [])),
              {"collisions": m2.get("collisions")})
        check("扫掠②后逐对象 matrix_world 还原", (m2.get("restored") or {}).get("ok") is True and not d2["bad"],
              {"restored": m2.get("restored"), "diff": d2})

        # ---- ③ prismatic 直线驱动扫掠
        rp = json.loads(api["joint"]("mt_slide", ["MT_Rail"], ["MT_Carriage"], kind="prismatic",
                                     axis=[1.0, 0.0, 0.0], anchor=[-3.0, 0.0, 0.10],
                                     limits=[-0.4, 0.4], note="滑块"))
        before_p = _snap()
        m3 = json.loads(api["measure"]("mt_slide", phases=6, exclude=pre + ["MT_Blocker"]))
        d3 = _diff(before_p, _snap())
        check("prismatic 扫掠 supported（直线位移 + 零干涉）",
              rp.get("ok") and m3.get("verdict") == "supported" and m3.get("moved") is True,
              {"verdict": m3.get("verdict"), "moved": m3.get("moved"), "reason": m3.get("reason"),
               "unit": (m3.get("sweep") or {}).get("unit")})
        check("prismatic 扫掠后逐对象还原", (m3.get("restored") or {}).get("ok") is True and not d3["bad"],
              {"restored": m3.get("restored"), "diff": d3})

        # ---- ④ 导出结构自检
        u = json.loads(api["export_urdf"](work, name="mt_model", joints=["mt_hinge", "mt_slide"],
                                          meters_per_unit=0.001, density=1000.0))
        s = json.loads(api["export_usda"](work, name="mt_model", joints=["mt_hinge", "mt_slide"],
                                          meters_per_unit=0.001, density=1000.0))
        check("URDF 结构自检通过（XML/唯一名/端点/单树/无环/可达/网格落盘）",
              u.get("ok") is True and (u.get("self_check") or {}).get("ok") is True,
              {"ok": u.get("ok"), "self_check": u.get("self_check"), "warnings": u.get("warnings")})
        import xml.etree.ElementTree as ET
        lim = None
        try:
            root = ET.parse(u.get("path")).getroot()
            for j in root.findall("joint"):
                if j.get("name") == "mt_hinge":
                    lm = j.find("limit")
                    ax = j.find("axis")
                    mesh = j.find("child") is not None and j.get("type")
                    lim = {"type": j.get("type"), "lower": float(lm.get("lower")), "upper": float(lm.get("upper")),
                           "axis": ax.get("xyz"), "effort": float(lm.get("effort"))}
        except Exception as e:
            lim = {"error": "%s: %s" % (type(e).__name__, e)}
        check("URDF 关节单位正确（limit 弧度制 = ±32°、轴 = Y、SI effort）",
              bool(lim) and lim.get("type") == "revolute"
              and abs(lim.get("lower", 1) + math.radians(32)) < 1e-6
              and abs(lim.get("upper", 0) - math.radians(32)) < 1e-6
              and abs(float(lim.get("axis", "0 0 0").split()[1])) > 0.999
              and lim.get("effort") == 1000.0,
              lim)
        check("URDF 用相对路径引用网格文件",
              "<mesh filename=\"meshes/" in open(u["path"], encoding="utf-8").read(),
              {"path": u.get("path")})
        check("USDA 结构自检通过（括号配平/数组一致/索引范围）",
              s.get("ok") is True and (s.get("self_check") or {}).get("ok") is True,
              {"ok": s.get("ok"), "self_check": s.get("self_check"), "warnings": s.get("warnings")})

        # 单位通路：meters_per_unit="scene" 读 scale_length（factory 场景 = 1.0）→ 不该报「单位可疑」
        u2 = json.loads(api["export_urdf"](work, name="mt_model_scene_units", joints=["mt_hinge", "mt_slide"],
                                           meters_per_unit="scene", density=1000.0))
        check("meters_per_unit='scene' 走场景 scale_length（模型对角线 ≈ 场景实际尺寸，不再报单位可疑）",
              u2.get("ok") is True and (u2.get("units") or {}).get("scene_units_suspect") is False
              and (u2.get("units") or {}).get("model_diagonal_m", 0) > 0.5
              and not any("单位可疑" in str(w) for w in (u2.get("warnings") or [])),
              {"units": u2.get("units"), "warnings": u2.get("warnings")})

        # ---- ⑤ 硬规则：空产出 / 无轴 / 未知对象
        bad_joint = json.loads(api["joint"]("mt_bad", ["NoSuchA"], ["NoSuchB"]))
        bad_export = json.loads(api["export_urdf"](work, name="empty", joints=[]))
        bad_measure = json.loads(api["measure"]("no_such_joint", phases=3))
        check("关节两端是空对象 → ok=false", bad_joint.get("ok") is False, bad_joint)
        check("0 个关节导出 → ok=false（空产出必须失败）", bad_export.get("ok") is False, bad_export.get("error"))
        check("扫掠未知关节 → ok=false", bad_measure.get("ok") is False, bad_measure)

        # ---- ⑥ dispatch 解包 + status/help
        st = json.loads(api["dispatch"]("status", {"args": {}}))
        js = json.loads(api["dispatch"]("joints", {}))
        hp = json.loads(api["dispatch"]("help", {"args": {}}))
        m4 = json.loads(api["dispatch"]("measure", {"args": {"jid": "mt_slide", "phases": 3,
                                                            "exclude": pre + ["MT_Blocker"]}}))
        bad_kw = json.loads(api["dispatch"]("help", {"args": {"bogus": 1}}))
        check("dispatch 支持 {'args': {...}} 再包一层（含带参数的 measure）",
              st.get("ok") and st.get("joint_count") >= 2 and js.get("count") >= 2
              and bool(hp.get("ops")) and m4.get("jid") == "mt_slide" and m4.get("verdict") in ("supported", "refuted"),
              {"status": st.get("joint_count"), "joints": js.get("count"),
               "help_ops": sorted(hp.get("ops") or []), "measure": {"jid": m4.get("jid"),
                                                                    "verdict": m4.get("verdict")}})
        check("status 带三态计数与导出历史",
              (st.get("verdict_counts") or {}).get("supported", 0) >= 1 and st.get("export_count") >= 2,
              {"counts": st.get("verdict_counts"), "exports": st.get("export_count")})
        check("未知 op 明确报错", json.loads(api["dispatch"]("nope", {})).get("ok") is False)
        check("参数不匹配明确报错（带 sig_hint）",
              bad_kw.get("ok") is False and "参数不匹配" in str(bad_kw.get("error")), bad_kw.get("error"))

        # ---- ⑦ 分组重叠：同一对象被两个 link 认领 = 刚体归属矛盾 → 必须拦住（几何/质量会重复计入）
        # 注意：这会把关节图连通起来（本关节链变更），所以放在最后做
        api["joint"]("mt_dupe", ["MT_Bar"], ["MT_Plate", "MT_Rail"], kind="fixed", note="故意分组重叠")
        ov1 = json.loads(api["export_urdf"](work, name="overlap", joints=["mt_hinge", "mt_slide", "mt_dupe"]))
        ov2 = json.loads(api["export_usda"](work, name="overlap", joints=["mt_hinge", "mt_slide", "mt_dupe"]))
        check("分组重叠 → URDF/USDA 都 ok=false 并列出冲突对象",
              ov1.get("ok") is False and bool(ov1.get("conflicts")) and ov2.get("ok") is False
              and sorted(ov1["conflicts"]) == ["MT_Plate", "MT_Rail"],
              {"urdf": ov1.get("conflicts"), "usda": ov2.get("ok"), "error": ov1.get("error")})
        m5 = json.loads(api["measure"]("mt_slide", phases=3, chain_depth="immediate", exclude=pre + ["MT_Blocker"]))
        check("chain_depth='immediate' 时仍能找回旁观者（连通图下不至于空洞 unresolved）",
              m5.get("verdict") == "supported", {"verdict": m5.get("verdict"), "reason": m5.get("reason")})
    except Exception as e:
        import traceback
        FAILS.append({"name": "exception", "detail": "%s: %s" % (type(e).__name__, e),
                      "traceback": traceback.format_exc()[-1200:]})
        print("  FAIL exception %s: %s" % (type(e).__name__, e))
    finally:
        for ob in made:
            try:
                nm = str(ob.name)
            except Exception:
                nm = "?"
            try:
                bpy.data.objects.remove(ob, do_unlink=True)
            except Exception as e:
                print("  warn 清理对象 %s 失败: %s" % (nm, e))
        for me in meshes:
            try:
                bpy.data.meshes.remove(me, do_unlink=True)
            except Exception:
                pass
        shutil.rmtree(work, ignore_errors=True)
        bpy.context.view_layer.update()

    left = sorted(o.name for o in bpy.data.objects)
    check("自检后场景回到预先存在的对象集合", left == pre, {"left": left, "pre": pre})
    ok = not FAILS
    print("HEADLESS " + json.dumps({"ok": ok, "passed": len(OKS), "failed": len(FAILS), "failures": FAILS,
                                    "version": api["version"], "outdir": outdir}, ensure_ascii=False))
    return ok


if __name__ == "__main__":
    main()
