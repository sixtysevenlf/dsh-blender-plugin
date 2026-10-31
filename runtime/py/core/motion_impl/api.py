# -*- coding: utf-8 -*-
"""Split from motion.py; behavior preserved by the facade."""
from .common import *  # noqa: F401,F403
from .infer import motion_infer_axis, motion_joint, motion_joints
from .transforms import TOL_MAT, _snap_diff
from .measure import motion_measure
from .urdf import motion_export_urdf
from .usda import motion_export_usda



# ============================================================ status / help

def motion_status():
    st = _store()
    js = {}
    counts = {"supported": 0, "refuted": 0, "unresolved": 0}
    for k, v in st["joints"].items():
        mv = (v.get("measured") or {}).get("verdict")
        av = v.get("axis_verdict") or "unresolved"
        vv = mv or av
        counts[vv if vv in counts else "unresolved"] = counts.get(vv if vv in counts else "unresolved", 0) + 1
        js[k] = {"kind": v["kind"], "parent": v["parent"], "child": v["child"],
                 "axis": v.get("axis"), "anchor": v.get("anchor"),
                 "axis_source": v.get("axis_source"), "axis_verdict": av,
                 "limits": v.get("limits"), "note": v.get("note"),
                 "measured": ({"verdict": mv, "moved": (v.get("measured") or {}).get("moved"),
                               "phases": len((v.get("measured") or {}).get("phases") or []),
                               "reason": (v.get("measured") or {}).get("reason"),
                               "restored_ok": (v.get("measured") or {}).get("restored_ok"),
                               "t": (v.get("measured") or {}).get("t")} if v.get("measured") else None),
                 "warnings": v.get("warnings") or []}
    return _j({"ok": True, "op": "status", "version": MOTION_VERSION,
               "joints": js, "joint_count": len(js),
               "verdict_counts": counts,
               "exports": st["exports"], "export_count": len(st["exports"]),
               "log_tail": st["log"][-10:],
               "units": {"length": "scene_unit（1 单位 = %g m）" % _scene_mpu(), "angle": "deg"},
               "boundary": "关节轴可实测（旋转对称/接触带）；扫掠是运动学验证不是物理验证；"
                           "USDA 不是完整 UsdPhysics schema"})


def motion_help():
    return _j({
        "version": MOTION_VERSION,
        "ops": {
            "joint": "motion_joint(jid, parent, child, kind='revolute', axis=None, anchor=None, limits=None, "
                     "note='') —— parent/child 是对象名或对象名列表（刚性组）；axis/anchor 不给则实测推断",
            "infer_axis": "motion_infer_axis(jid=None, objects=None, parent=None, sample_cap=3000, "
                          "min_score=0.55, max_distance=None) → 证据 + 三态（薄平板绕铰链轴无旋转对称 ⇒ unresolved）",
            "joints": "motion_joints() → 已登记关节 + 轴判定 + 实测摘要",
            "measure": "motion_measure(jid=None, phases=8, sweep_deg=60, margin_frac=0.05, exclude=None, "
                       "chain_depth='full', max_candidates=64, max_samples=400) → 扫掠（干涉对+最小间距）",
            "export_urdf": "motion_export_urdf(dir, name='model', joints=None, meters_per_unit=0.001, "
                           "density=1000, masses=None, mesh_format='obj', effort=1000, velocity=100) "
                           "→ <dir>/<name>.urdf + <dir>/meshes/*.obj + 结构自检",
            "export_usda": "motion_export_usda(dir, name='model', joints=None, meters_per_unit=0.001, "
                           "density=1000, masses=None) → 最小可用 ASCII USDA + 结构自检",
            "status": "motion_status() → 关节 + 判定 + 导出历史（K.dsh_motion）",
            "selftest": "motion_selftest() → 合成铰链自检（无头可跑，自带场景/清理）",
            "help": "motion_help()",
        },
        "typical": "K.dsh_motion_api['dispatch']('joint', {'jid':'hinge','parent':['Base'],"
                   "'child':['Door','Pin'],'kind':'revolute','limits':[-90,90]}) → "
                   "K.dsh_motion_api['dispatch']('measure', {'jid':'hinge','phases':8,'sweep_deg':60}) → "
                   "K.dsh_motion_api['dispatch']('export_urdf', {'dir': K.out_dir, 'name':'door'})",
        "rules": {
            "verdict": "supported / refuted / unresolved 三态；证据不足一律 unresolved（不许二选一凑）",
            "ok": "ok=false ⇔ unresolved 或空产出（0 link / 0 joint / 没轴 / 没 child 几何）",
            "units": "revolute 的 limits/相位=deg；prismatic=场景单位；间距同时给 scene_unit 与 m；URDF=SI",
            "restore": "扫掠结束逐对象对拍 matrix_world（全部对象），mismatch 写在 restored 里",
        },
        "boundary": [
            "扫掠 = 运动学 + BVH 干涉，不是 Isaac 那种物理验证（无质量/摩擦/驱动动力学）",
            "最小间距是采样近似（偏保守/偏大）；干涉判定用 BVH 三角面相交（精确）",
            "零干涉需要至少一个'旁观者'对象，否则是空洞证据 → 该情形判 unresolved",
            "轴推断只对可辨识几何有效；接触带证据需要父组 + max_distance 内有足够接触点",
            "两条证据（旋转对称 / 接触带）矛盾时一律 unresolved，不替用户选",
            "导出要求各关节分组互不重叠（一个对象只能属于一个 link），否则 ok=false",
            "USDA 是最小可用 ASCII，不是完整 UsdPhysics schema",
        ],
    })


# ============================================================ 自检

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


def _mk_cyl(name, radius, depth, loc, segs=32):
    """沿 +Z 的圆柱；segs=32 ⇒ 90° 旋转精确自映射（对称轴可辨识的合成证据）。"""
    r, d = float(radius), float(depth)
    v, f = [], []
    for i in range(segs):
        a = 2.0 * math.pi * i / segs
        x, y = r * math.cos(a), r * math.sin(a)
        v.append((x, y, -d / 2.0))
        v.append((x, y, d / 2.0))
    for i in range(segs):
        j = (i + 1) % segs
        f.append((2 * i, 2 * j, 2 * j + 1, 2 * i + 1))
    f.append(tuple(reversed(range(0, 2 * segs, 2))))
    f.append(tuple(range(1, 2 * segs, 2)))
    me = bpy.data.meshes.new("MSH_" + name)
    me.from_pydata(v, [], f)
    me.update()
    ob = bpy.data.objects.new(name, me)
    ob.location = [float(x) for x in loc]
    bpy.context.scene.collection.objects.link(ob)
    return ob


def motion_selftest(keep=False):
    """合成自检：造铰链（底板 + 门板 + 沿 Z 的轴）→ 实测轴 → 无阻挡扫掠 → 加挡块扫掠 →
    URDF/USDA 结构自检 → 还原证明。不依赖用户场景（预先存在的对象一律 exclude），最后清理。
    """
    t0 = time.perf_counter()
    checks = []

    def _chk(name, cond, detail):
        checks.append({"name": name, "ok": bool(cond), "detail": detail})
        return bool(cond)

    K = _kernel()
    made_objs, made_meshes, made_dirs = [], [], []
    pre_names = sorted(o.name for o in bpy.data.objects)
    outdir = os.path.join(_out_dir(), "motion_selftest")
    res = {}
    try:
        # ---- 场景：铰链轴 = 世界 Z 轴（过 x=0,y=0）
        base = _mk_box("MS_Base", (1.0, 0.4, 0.06), (-0.05, 0.0, -0.06))
        door = _mk_box("MS_Door", (0.5, 0.04, 0.5), (0.29, 0.0, 0.25))     # x∈[0.04,0.54]
        pin = _mk_cyl("MS_Pin", 0.02, 0.6, (0.0, 0.0, 0.25))               # 轴 = Z，过原点
        block = _mk_box("MS_Blocker", (0.08, 0.08, 0.6), (0.40, 0.21, 0.25))  # 挡在 +y 扫掠路径上
        stand = _mk_box("MS_Stand", (0.2, 0.2, 0.2), (-1.5, 0.0, 0.1))     # 孤儿 link 用
        bolt = _mk_box("MS_Bolt", (0.05, 0.05, 0.05), (-1.5, 0.2, 0.1))
        for ob in (base, door, pin, block, stand, bolt):
            made_objs.append(ob)
            made_meshes.append(ob.data)
        bpy.context.view_layer.update()
        _chk("scene_built", len(made_objs) == 6, {"objects": [o.name for o in made_objs]})

        # ---- ① 登记关节（不给 axis/anchor → 实测推断）
        r_joint = json.loads(motion_joint("selftest_hinge", ["MS_Base"], ["MS_Door", "MS_Pin"],
                                          kind="revolute", limits=[-30, 30], note="自检铰链"))
        inf = json.loads(motion_infer_axis(jid="selftest_hinge"))
        true_axis = np.array([0.0, 0.0, 1.0])
        ang = _angle_deg(inf.get("axis") or [0, 0, 0], true_axis) if inf.get("axis") else None
        anchor = inf.get("anchor") or [0, 0, 0]
        anchor_off = _dist_point_line(anchor, [0.0, 0.0, 0.25], true_axis)
        res["infer"] = {"verdict": inf.get("verdict"), "axis": inf.get("axis"), "anchor": inf.get("anchor"),
                        "angle_to_true_axis_deg": _r(ang, 4) if ang is not None else None,
                        "anchor_offset_from_hinge_line": _r(anchor_off),
                        "reason": inf.get("reason"),
                        "per_object": [{"label": c.get("label"), "score": c.get("score"),
                                        "passed": c.get("passed"), "degenerate": c.get("degenerate"),
                                        "snapped": c.get("snapped")} for c in
                                       (inf.get("evidence") or {}).get("per_object", [])]}
        _chk("infer_verdict_not_refuted", inf.get("verdict") in ("supported", "unresolved"),
              {"verdict": inf.get("verdict"), "reason": inf.get("reason")})
        _chk("infer_axis_clearance_lt_5deg", (ang is not None and ang < 5.0),
              {"angle_deg": (None if ang is None else _r(ang, 4)), "axis": inf.get("axis"),
               "note": "销（圆柱）绕 Z 旋转对称 ⇒ 轴可辨识"})
        _chk("infer_anchor_on_hinge_line", (anchor_off is not None and anchor_off * _scene_mpu() <= 0.005),
              {"offset_scene_unit": _r(anchor_off), "offset_m": _r((anchor_off or 0) * _scene_mpu()),
               "tol_m": 0.005})
        _chk("joint_registered_with_axis", bool(r_joint.get("ok") and r_joint.get("axis")),
              {"ok": r_joint.get("ok"), "axis": r_joint.get("axis"), "axis_verdict": r_joint.get("axis_verdict"),
               "warnings": r_joint.get("warnings")})

        # ---- 孤儿 link（导出单树合成）用
        motion_joint("selftest_spare", ["MS_Stand"], ["MS_Bolt"], kind="spherical", note="自检孤儿+降级")

        # ---- ② 无阻挡扫掠：必须 supported
        before = {o.name: [float(x) for row in o.matrix_world for x in row] for o in bpy.data.objects}
        m1 = json.loads(motion_measure(jid="selftest_hinge", phases=8, sweep_deg=60,
                                       exclude=pre_names + ["MS_Blocker"]))
        after1 = {o.name: [float(x) for row in o.matrix_world for x in row] for o in bpy.data.objects}
        res["sweep_clean"] = {"verdict": m1.get("verdict"), "moved": m1.get("moved"),
                              "max_displacement_m": m1.get("max_displacement_m"),
                              "phases": len(m1.get("phases") or []),
                              "candidates": m1.get("candidates"),
                              "restored_ok": (m1.get("restored") or {}).get("ok"),
                              "reason": m1.get("reason"),
                              "phase_values": [(p.get("value"), p.get("unit"),
                                                len(p.get("collisions") or [])) for p in (m1.get("phases") or [])]}
        _chk("sweep_clean_supported", m1.get("verdict") == "supported" and m1.get("moved") is True,
              {"verdict": m1.get("verdict"), "moved": m1.get("moved"), "reason": m1.get("reason")})
        d1, bad1, exact1 = _snap_diff(before, after1)
        res["sweep_clean"]["restore_diff"] = {"max_delta": d1, "bad": bad1, "exact": exact1,
                                             "tol": TOL_MAT}
        _chk("sweep_clean_restored", (m1.get("restored") or {}).get("ok") is True and not bad1,
              {"restored": m1.get("restored"), "diff": res["sweep_clean"]["restore_diff"]})

        # ---- ③ 挡块进扫掠路径：必须 refuted 且点名
        m2 = json.loads(motion_measure(jid="selftest_hinge", phases=8, sweep_deg=60, exclude=pre_names))
        after2 = {o.name: [float(x) for row in o.matrix_world for x in row] for o in bpy.data.objects}
        off = m2.get("offenders") or []
        res["sweep_blocked"] = {"verdict": m2.get("verdict"), "offenders": off,
                                "collisions": [{"phase": c.get("phase"), "other": c.get("other"),
                                                "min_gap_m": c.get("min_gap_m"),
                                                "penetration_m": c.get("penetration_m")}
                                               for c in (m2.get("collisions") or [])],
                                "reason": m2.get("reason"),
                                "restored_ok": (m2.get("restored") or {}).get("ok")}
        _chk("sweep_block_refuted", m2.get("verdict") == "refuted" and "MS_Blocker" in off,
              {"verdict": m2.get("verdict"), "offenders": off, "reason": m2.get("reason")})
        _chk("sweep_block_penetration_negative",
              any((c.get("min_gap_m") is not None and c["min_gap_m"] < 0) for c in (m2.get("collisions") or [])),
              {"collisions": res["sweep_blocked"]["collisions"]})
        d2, bad2, exact2 = _snap_diff(before, after2)
        res["sweep_blocked"]["restore_diff"] = {"max_delta": d2, "bad": bad2, "exact": exact2,
                                                "tol": TOL_MAT}
        _chk("sweep_block_restored", (m2.get("restored") or {}).get("ok") is True and not bad2,
              {"restored": m2.get("restored"), "diff": res["sweep_blocked"]["restore_diff"]})

        # ---- ④ 导出结构自检（URDF + USDA）
        os.makedirs(outdir, exist_ok=True)
        made_dirs.append(outdir)
        u = json.loads(motion_export_urdf(dir=outdir, name="selftest_model",
                                          joints=["selftest_hinge", "selftest_spare"],
                                          meters_per_unit=0.001, density=1000.0))
        s = json.loads(motion_export_usda(dir=outdir, name="selftest_model",
                                          joints=["selftest_hinge", "selftest_spare"],
                                          meters_per_unit=0.001, density=1000.0))
        res["urdf"] = {"ok": u.get("ok"), "path": u.get("path"),
                       "self_check": u.get("self_check"), "warnings": u.get("warnings"),
                       "links": [l.get("link") for l in (u.get("links") or [])],
                       "joints": [(j.get("joint"), j.get("type"), j.get("synthesized"),
                                   j.get("degraded_from")) for j in (u.get("joints") or [])]}
        res["usda"] = {"ok": s.get("ok"), "path": s.get("path"),
                       "self_check": s.get("self_check"), "warnings": s.get("warnings"),
                       "links": s.get("links"), "joints": s.get("joints")}
        _chk("urdf_self_check_ok", bool(u.get("ok") and (u.get("self_check") or {}).get("ok")),
              {"ok": u.get("ok"), "self_check": u.get("self_check"), "warnings": u.get("warnings")})
        _chk("urdf_degraded_reported",
              any("降级" in str(w) for w in (u.get("warnings") or [])),
              {"warnings": u.get("warnings")})
        _chk("urdf_orphan_synthesized",
              any("不可达" in str(w) for w in (u.get("warnings") or []))
              and any(j.get("synthesized") for j in (u.get("joints") or [])),
              {"warnings": u.get("warnings"), "joints": res["urdf"]["joints"]})
        _chk("usda_self_check_ok", bool(s.get("ok") and (s.get("self_check") or {}).get("ok")),
              {"ok": s.get("ok"), "self_check": s.get("self_check"), "warnings": s.get("warnings")})
        # 单位体检：本场景是米制（0.5 单位 ≈ 0.5 m），却按 0.001 导出 ⇒ 必须自己报「单位可疑」
        _chk("units_guard_fires_on_implausible_scale",
              (u.get("units") or {}).get("scene_units_suspect") is True
              and any("单位可疑" in str(w) for w in (u.get("warnings") or [])),
              {"units": u.get("units"), "warnings": u.get("warnings")})
        # 小量级不能被写成 0：质量/惯量必须保住数量级（早先 "%.9f" 会把 8.6e-11 写成 0）
        import re as _re
        _txt = open(u["path"], encoding="utf-8").read()
        _zeros = _re.findall(r'(?:mass value|inertia ixx|iyy|izz)="0"', _txt)
        _chk("SI 小量级不塌成 0（质量/惯量保数量级）", not _zeros,
              {"zeros": _zeros[:6], "sample": _re.findall(r'<mass value="[^"]+"', _txt)[:4]})

        # ---- ⑤ 空产出 / 无轴 必须失败（硬规则）
        empty_joint = json.loads(motion_joint("selftest_bad", ["MS_Nope"], ["MS_AlsoNope"]))
        empty_export = json.loads(motion_export_urdf(dir=outdir, name="empty", joints=[]))
        no_axis_measure = json.loads(motion_measure(jid="selftest_spare", phases=4))
        res["hard_rules"] = {"empty_joint_ok": empty_joint.get("ok"),
                             "empty_export_ok": empty_export.get("ok"),
                             "empty_export_error": empty_export.get("error"),
                             "no_axis_measure_ok": no_axis_measure.get("ok"),
                             "no_axis_measure_verdict": no_axis_measure.get("verdict"),
                             "no_axis_measure_reason": no_axis_measure.get("reason")}
        _chk("empty_joint_must_fail", empty_joint.get("ok") is False, {"resp": empty_joint})
        _chk("empty_export_must_fail", empty_export.get("ok") is False, {"resp": empty_export.get("error")})
        _chk("no_axis_measure_unresolved",
              no_axis_measure.get("ok") is False and no_axis_measure.get("verdict") == "unresolved",
              {"resp": res["hard_rules"]})

        # ---- ⑥ 轴推断的三态（判据真的会咬人）：接触带可定轴 / 薄板必须 unresolved / 假设轴可被反驳
        bar2 = _mk_box("MS_Bar", (1.0, 0.08, 0.08), (-3.0, 0.0, 0.0))          # 顶面 z=0.04
        pl_a = _mk_box("MS_PlateNS", (0.5, 0.04, 0.3), (-3.4, 0.0, 0.17))      # 非方形：无旋转对称
        pl_b = _mk_box("MS_PlateSQ", (0.5, 0.04, 0.5), (-2.6, 0.0, 0.27))      # 方形：绕法线也对称
        pad2 = _mk_box("MS_Pad", (0.6, 0.3, 0.04), (-5.0, 0.0, 0.0))
        flap = _mk_box("MS_Flap", (0.5, 0.03, 0.4), (-5.0, 0.0, 0.25))         # 与 Pad 有 0.03 间隙
        for ob in (bar2, pl_a, pl_b, pad2, flap):
            made_objs.append(ob)
            made_meshes.append(ob.data)
        bpy.context.view_layer.update()
        true_hinge = np.array([1.0, 0.0, 0.0])

        j_contact = json.loads(motion_joint("selftest_contact", ["MS_Bar"], ["MS_PlateNS"],
                                           kind="revolute", note="接触带定轴"))
        ax_c = j_contact.get("axis") or [0, 0, 0]
        ang_c = _angle_deg(ax_c, true_hinge)
        anc_off_c = _dist_point_line(j_contact.get("anchor") or [0, 0, 0], [0.0, 0.0, 0.04], true_hinge)
        res["infer_contact"] = {"verdict": j_contact.get("axis_verdict"), "source": j_contact.get("axis_source"),
                                "axis": j_contact.get("axis"), "anchor": j_contact.get("anchor"),
                                "angle_to_true_deg": _r(ang_c, 4),
                                "anchor_offset": _r(anc_off_c), "warnings": j_contact.get("warnings")}
        _chk("contact_strip_axis_supported",
              j_contact.get("axis_verdict") == "supported" and j_contact.get("axis_source") == "contact_region"
              and ang_c is not None and ang_c < 5.0,
              res["infer_contact"])
        _chk("contact_strip_anchor_near_hinge_line", anc_off_c is not None and anc_off_c * _scene_mpu() <= 0.005,
              {"offset_scene_unit": _r(anc_off_c), "offset_m": _r((anc_off_c or 0) * _scene_mpu()), "tol_m": 0.005})

        j_slab = json.loads(motion_joint("selftest_slab", ["MS_Pad"], ["MS_Flap"], kind="revolute",
                                         note="薄板无销：必须 unresolved"))
        inf_slab = json.loads(motion_infer_axis(jid="selftest_slab"))
        res["infer_slab_unresolved"] = {"joint_axis_verdict": j_slab.get("axis_verdict"),
                                        "axis": j_slab.get("axis"),
                                        "verdict": inf_slab.get("verdict"),
                                        "best_score": [{"label": c.get("label"), "score": c.get("score"),
                                                        "degenerate": c.get("degenerate")}
                                                       for c in (inf_slab.get("evidence") or {}).get("per_object", [])],
                                        "contact": {k: (inf_slab.get("evidence") or {}).get("contact", {}).get(k)
                                                    for k in ("available", "reason", "close_points", "elongation")},
                                        "missing_evidence": inf_slab.get("missing_evidence"),
                                        "reason": inf_slab.get("reason")}
        _chk("slab_without_pin_is_unresolved",
              j_slab.get("axis") is None and j_slab.get("axis_verdict") == "unresolved"
              and inf_slab.get("verdict") == "unresolved" and inf_slab.get("missing_evidence"),
              res["infer_slab_unresolved"])

        j_sq = json.loads(motion_joint("selftest_square", ["MS_Bar"], ["MS_PlateSQ"], kind="revolute",
                                       note="方形板：对称轴与接触带矛盾"))
        inf_sq = json.loads(motion_infer_axis(jid="selftest_square"))
        res["infer_conflict"] = {"joint_axis_verdict": j_sq.get("axis_verdict"),
                                 "verdict": inf_sq.get("verdict"),
                                 "axis_conflict": inf_sq.get("axis_conflict"),
                                 "reason": inf_sq.get("reason")}
        _chk("square_plate_conflict_unresolved",
              inf_sq.get("verdict") == "unresolved" and bool(inf_sq.get("axis_conflict"))
              and inf_sq.get("axis") is None,
              res["infer_conflict"])

        motion_joint("selftest_wrongaxis", ["MS_Base"], ["MS_Pin"], kind="revolute",
                     axis=[1.0, 0.0, 0.0], anchor=[0.0, 0.0, 0.25], note="故意给错轴（child 只有销）")
        inf_wrong = json.loads(motion_infer_axis(jid="selftest_wrongaxis"))
        res["infer_wrong_axis"] = {"verdict": inf_wrong.get("verdict"), "reason": inf_wrong.get("reason"),
                                   "nearest": ((inf_wrong.get("hypothesis") or {}).get("evidence") or {}).get("candidates")}
        _chk("wrong_axis_hypothesis_refuted", inf_wrong.get("verdict") == "refuted",
              res["infer_wrong_axis"])

        motion_joint("selftest_ambiguous", ["MS_Base"], ["MS_Door", "MS_Pin"], kind="revolute",
                     axis=[1.0, 0.0, 0.0], anchor=[0.0, 0.0, 0.25], note="假设方向只是近失候选")
        inf_amb = json.loads(motion_infer_axis(jid="selftest_ambiguous"))
        res["infer_ambiguous"] = {"verdict": inf_amb.get("verdict"), "reason": inf_amb.get("reason"),
                                  "nearest": ((inf_amb.get("hypothesis") or {}).get("evidence") or {}).get("candidates")}
        _chk("ambiguous_hypothesis_not_refuted", inf_amb.get("verdict") == "unresolved",
              res["infer_ambiguous"])
    except Exception as e:
        checks.append({"name": "selftest_exception", "ok": False,
                       "detail": "%s: %s" % (type(e).__name__, str(e)[:300]),
                       "traceback": traceback.format_exc()[-1200:]})
    finally:
        removed, failed = [], []
        for ob in made_objs:
            try:
                nm = str(ob.name)          # 先取名：remove 之后 StructRNA 就失效了
            except Exception:
                nm = "?"
            try:
                bpy.data.objects.remove(ob, do_unlink=True)
                removed.append(nm)
            except Exception as e:
                failed.append({"name": nm, "error": str(e)[:120]})
        for me in made_meshes:
            try:
                bpy.data.meshes.remove(me, do_unlink=True)
            except Exception:
                pass
        if not keep:
            for d in made_dirs:
                try:
                    shutil.rmtree(d, ignore_errors=True)
                except Exception:
                    pass
        try:
            bpy.context.view_layer.update()
        except Exception:
            pass
        res["cleaned"] = {"removed_objects": removed, "failed": failed,
                          "objects_left": sorted(o.name for o in bpy.data.objects),
                          "pre_existing": pre_names,
                          "dir_removed": (not keep),
                          "dir": outdir}

    fails = [c for c in checks if not c["ok"]]
    ok = not fails
    if not (res.get("cleaned") or {}).get("removed_objects"):
        ok = False
        fails.append({"name": "cleanup_ran", "ok": False, "detail": "清理没有执行"})
    _log("selftest", {"ok": ok, "fails": [c["name"] for c in fails]})
    return _j({
        "ok": ok, "op": "selftest", "version": MOTION_VERSION,
        "checks": checks, "fails": fails, "fail_count": len(fails),
        "measured": res,
        "expect": ("销（圆柱，沿 Z）可辨识 ⇒ 轴实测 supported 且与真轴夹角 <5°、锚点在铰链线上；"
                   "无阻挡 8 相位扫掠 = supported（零干涉且确实位移）；把挡块放进扫掠路径 ⇒ 同一关节变 "
                   "refuted 且点名 MS_Blocker（最小间距 <0）；URDF/USDA 结构自检全过（含 spherical 降级与"
                   "孤儿 link 合成单树）；扫掠前后所有对象 matrix_world 逐项相等；空产出/无轴必须 ok=false"),
        "notes": ["自检不碰用户场景：预先存在的对象一律 exclude 且不删",
                  "临时对象/文件在 finally 里清理（keep=true 可保留产物用于排查）"],
        "ms": int((time.perf_counter() - t0) * 1000),
    })


# ============================================================ 派发

def motion_reset():
    K = _kernel()
    if K is None:
        return _j({"ok": False, "error": "需要持久内核 K"})
    K.dsh_motion = {"joints": {}, "exports": [], "log": [], "seq": 0}
    return _j({"ok": True, "op": "reset", "note": "只清 K.dsh_motion（不动场景、不删文件）"})


def motion_dispatch(op, args=None):
    """统一入口：工具侧发 {op, args}；args 里再包一层 {"args": {...}} 也解包（与契约层同口径）。"""
    if isinstance(args, str):
        try:
            args = json.loads(args) if args.strip() else {}
        except Exception:
            args = {}
    kw = {}
    for k, v in (args or {}).items():
        if k == "args" and isinstance(v, dict):
            kw.update(v)
        else:
            kw[k] = v
    fn = motion_ops().get(str(op))
    if fn is None:
        return _j({"ok": False, "error": "unknown motion op", "op": op, "ops": sorted(motion_ops())})
    try:
        return fn(**kw)
    except TypeError as e:
        return _j({"ok": False, "error": "参数不匹配: %s" % str(e)[:200], "op": op,
                   "given": sorted(kw), "sig_hint": motion_help()})
    except Exception as e:
        return _j({"ok": False, "error": "%s: %s" % (type(e).__name__, str(e)[:200]), "op": op,
                   "traceback": traceback.format_exc()[-800:]})


def motion_ops():
    return {"joint": motion_joint, "joints": motion_joints, "infer_axis": motion_infer_axis,
            "measure": motion_measure, "export_urdf": motion_export_urdf,
            "export_usda": motion_export_usda, "status": motion_status, "help": motion_help,
            "selftest": motion_selftest, "reset": motion_reset}


import sys as _sys
_K = _sys.modules.get("dsh_rt_kernel")
if _K is not None:
    _K.dsh_motion_api = _KIT.Api({"version": MOTION_VERSION, "dispatch": motion_dispatch,
                         "joint": motion_joint, "joints": motion_joints,
                         "infer_axis": motion_infer_axis, "measure": motion_measure,
                         "export_urdf": motion_export_urdf, "export_usda": motion_export_usda,
                         "status": motion_status, "help": motion_help,
                         "selftest": motion_selftest, "reset": motion_reset,
                         "ops": motion_ops})

# ---- v0.9.1（93-B1/B2）：API 可调用化（换成 dict 子类实例，返回已解析对象）----
# 背景：K.dsh_x_api 原来是普通 dict → 进程内 api(args) 报 TypeError: 'dict' object is not callable；
# 且 dispatch 返回 JSON 字符串，调用方还得自己 json.loads。
# 现在：api("op", {…}) 或 api({…}) → dict；api["dispatch"](op, json_str) 仍返回 str（引擎契约不变）。
# 注意：dict 是静态类型，不能对已有实例做 __class__ 赋值（实测 TypeError），所以换成一个新实例。
_DshApi = _KIT.Api  # 共享内核（尾部注册行无需改）
import sys as _sys_api
_K_api = _sys_api.modules.get("dsh_rt_kernel")
if _K_api is not None and isinstance(getattr(_K_api, "dsh_motion_api", None), dict) \
        and not isinstance(getattr(_K_api, "dsh_motion_api", None), _DshApi):
    _K_api.dsh_motion_api = _DshApi(_K_api.dsh_motion_api)
