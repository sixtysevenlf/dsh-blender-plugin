# -*- coding: utf-8 -*-
"""motion synthetic selftest harness (moved out of api.py; behavior preserved)."""
from ..common import *  # noqa: F401,F403
from ..infer import motion_infer_axis, motion_joint
from ..transforms import TOL_MAT, _snap_diff
from ..measure import motion_measure
from ..urdf import motion_export_urdf
from ..usda import motion_export_usda


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


class _Selftest:
    """One synthetic hinge scene walked through every motion stage in order."""

    def __init__(self):
        self.checks = []
        self.res = {}
        self.made_objs = []
        self.made_meshes = []
        self.made_dirs = []
        self.pre_names = sorted(o.name for o in bpy.data.objects)
        self.outdir = os.path.join(_out_dir(), "motion_selftest")

    def chk(self, name, cond, detail):
        self.checks.append({"name": name, "ok": bool(cond), "detail": detail})
        return bool(cond)

    def build_scene(self):
        # ---- 场景：铰链轴 = 世界 Z 轴（过 x=0,y=0）
        base = _mk_box("MS_Base", (1.0, 0.4, 0.06), (-0.05, 0.0, -0.06))
        door = _mk_box("MS_Door", (0.5, 0.04, 0.5), (0.29, 0.0, 0.25))     # x∈[0.04,0.54]
        pin = _mk_cyl("MS_Pin", 0.02, 0.6, (0.0, 0.0, 0.25))               # 轴 = Z，过原点
        block = _mk_box("MS_Blocker", (0.08, 0.08, 0.6), (0.40, 0.21, 0.25))  # 挡在 +y 扫掠路径上
        stand = _mk_box("MS_Stand", (0.2, 0.2, 0.2), (-1.5, 0.0, 0.1))     # 孤儿 link 用
        bolt = _mk_box("MS_Bolt", (0.05, 0.05, 0.05), (-1.5, 0.2, 0.1))
        for ob in (base, door, pin, block, stand, bolt):
            self.made_objs.append(ob)
            self.made_meshes.append(ob.data)
        bpy.context.view_layer.update()
        self.chk("scene_built", len(self.made_objs) == 6, {"objects": [o.name for o in self.made_objs]})

    def register_and_infer(self):
        # ---- ① 登记关节（不给 axis/anchor → 实测推断）
        r_joint = json.loads(motion_joint("selftest_hinge", ["MS_Base"], ["MS_Door", "MS_Pin"],
                                          kind="revolute", limits=[-30, 30], note="自检铰链"))
        inf = json.loads(motion_infer_axis(jid="selftest_hinge"))
        true_axis = np.array([0.0, 0.0, 1.0])
        ang = _angle_deg(inf.get("axis") or [0, 0, 0], true_axis) if inf.get("axis") else None
        anchor = inf.get("anchor") or [0, 0, 0]
        anchor_off = _dist_point_line(anchor, [0.0, 0.0, 0.25], true_axis)
        self.res["infer"] = {"verdict": inf.get("verdict"), "axis": inf.get("axis"), "anchor": inf.get("anchor"),
                             "angle_to_true_axis_deg": _r(ang, 4) if ang is not None else None,
                             "anchor_offset_from_hinge_line": _r(anchor_off),
                             "reason": inf.get("reason"),
                             "per_object": [{"label": c.get("label"), "score": c.get("score"),
                                             "passed": c.get("passed"), "degenerate": c.get("degenerate"),
                                             "snapped": c.get("snapped")} for c in
                                            (inf.get("evidence") or {}).get("per_object", [])]}
        self.chk("infer_verdict_not_refuted", inf.get("verdict") in ("supported", "unresolved"),
                 {"verdict": inf.get("verdict"), "reason": inf.get("reason")})
        self.chk("infer_axis_clearance_lt_5deg", (ang is not None and ang < 5.0),
                 {"angle_deg": (None if ang is None else _r(ang, 4)), "axis": inf.get("axis"),
                  "note": "销（圆柱）绕 Z 旋转对称 ⇒ 轴可辨识"})
        self.chk("infer_anchor_on_hinge_line", (anchor_off is not None and anchor_off * _scene_mpu() <= 0.005),
                 {"offset_scene_unit": _r(anchor_off), "offset_m": _r((anchor_off or 0) * _scene_mpu()),
                  "tol_m": 0.005})
        self.chk("joint_registered_with_axis", bool(r_joint.get("ok") and r_joint.get("axis")),
                 {"ok": r_joint.get("ok"), "axis": r_joint.get("axis"), "axis_verdict": r_joint.get("axis_verdict"),
                  "warnings": r_joint.get("warnings")})
        # ---- 孤儿 link（导出单树合成）用
        motion_joint("selftest_spare", ["MS_Stand"], ["MS_Bolt"], kind="spherical", note="自检孤儿+降级")

    def sweeps(self):
        # ---- ② 无阻挡扫掠：必须 supported
        before = {o.name: [float(x) for row in o.matrix_world for x in row] for o in bpy.data.objects}
        m1 = json.loads(motion_measure(jid="selftest_hinge", phases=8, sweep_deg=60,
                                       exclude=self.pre_names + ["MS_Blocker"]))
        after1 = {o.name: [float(x) for row in o.matrix_world for x in row] for o in bpy.data.objects}
        self.res["sweep_clean"] = {"verdict": m1.get("verdict"), "moved": m1.get("moved"),
                                   "max_displacement_m": m1.get("max_displacement_m"),
                                   "phases": len(m1.get("phases") or []),
                                   "candidates": m1.get("candidates"),
                                   "restored_ok": (m1.get("restored") or {}).get("ok"),
                                   "reason": m1.get("reason"),
                                   "phase_values": [(p.get("value"), p.get("unit"),
                                                     len(p.get("collisions") or [])) for p in (m1.get("phases") or [])]}
        self.chk("sweep_clean_supported", m1.get("verdict") == "supported" and m1.get("moved") is True,
                 {"verdict": m1.get("verdict"), "moved": m1.get("moved"), "reason": m1.get("reason")})
        d1, bad1, exact1 = _snap_diff(before, after1)
        self.res["sweep_clean"]["restore_diff"] = {"max_delta": d1, "bad": bad1, "exact": exact1,
                                                   "tol": TOL_MAT}
        self.chk("sweep_clean_restored", (m1.get("restored") or {}).get("ok") is True and not bad1,
                 {"restored": m1.get("restored"), "diff": self.res["sweep_clean"]["restore_diff"]})
        # ---- ③ 挡块进扫掠路径：必须 refuted 且点名
        m2 = json.loads(motion_measure(jid="selftest_hinge", phases=8, sweep_deg=60, exclude=self.pre_names))
        after2 = {o.name: [float(x) for row in o.matrix_world for x in row] for o in bpy.data.objects}
        off = m2.get("offenders") or []
        self.res["sweep_blocked"] = {"verdict": m2.get("verdict"), "offenders": off,
                                     "collisions": [{"phase": c.get("phase"), "other": c.get("other"),
                                                     "min_gap_m": c.get("min_gap_m"),
                                                     "penetration_m": c.get("penetration_m")}
                                                    for c in (m2.get("collisions") or [])],
                                     "reason": m2.get("reason"),
                                     "restored_ok": (m2.get("restored") or {}).get("ok")}
        self.chk("sweep_block_refuted", m2.get("verdict") == "refuted" and "MS_Blocker" in off,
                 {"verdict": m2.get("verdict"), "offenders": off, "reason": m2.get("reason")})
        self.chk("sweep_block_penetration_negative",
                 any((c.get("min_gap_m") is not None and c["min_gap_m"] < 0) for c in (m2.get("collisions") or [])),
                 {"collisions": self.res["sweep_blocked"]["collisions"]})
        d2, bad2, exact2 = _snap_diff(before, after2)
        self.res["sweep_blocked"]["restore_diff"] = {"max_delta": d2, "bad": bad2, "exact": exact2,
                                                     "tol": TOL_MAT}
        self.chk("sweep_block_restored", (m2.get("restored") or {}).get("ok") is True and not bad2,
                 {"restored": m2.get("restored"), "diff": self.res["sweep_blocked"]["restore_diff"]})

    def exports(self):
        # ---- ④ 导出结构自检（URDF + USDA）
        os.makedirs(self.outdir, exist_ok=True)
        self.made_dirs.append(self.outdir)
        u = json.loads(motion_export_urdf(dir=self.outdir, name="selftest_model",
                                          joints=["selftest_hinge", "selftest_spare"],
                                          meters_per_unit=0.001, density=1000.0))
        s = json.loads(motion_export_usda(dir=self.outdir, name="selftest_model",
                                          joints=["selftest_hinge", "selftest_spare"],
                                          meters_per_unit=0.001, density=1000.0))
        self.res["urdf"] = {"ok": u.get("ok"), "path": u.get("path"),
                            "self_check": u.get("self_check"), "warnings": u.get("warnings"),
                            "links": [l.get("link") for l in (u.get("links") or [])],
                            "joints": [(j.get("joint"), j.get("type"), j.get("synthesized"),
                                        j.get("degraded_from")) for j in (u.get("joints") or [])]}
        self.res["usda"] = {"ok": s.get("ok"), "path": s.get("path"),
                            "self_check": s.get("self_check"), "warnings": s.get("warnings"),
                            "links": s.get("links"), "joints": s.get("joints")}
        self.chk("urdf_self_check_ok", bool(u.get("ok") and (u.get("self_check") or {}).get("ok")),
                 {"ok": u.get("ok"), "self_check": u.get("self_check"), "warnings": u.get("warnings")})
        self.chk("urdf_degraded_reported",
                 any("降级" in str(w) for w in (u.get("warnings") or [])),
                 {"warnings": u.get("warnings")})
        self.chk("urdf_orphan_synthesized",
                 any("不可达" in str(w) for w in (u.get("warnings") or []))
                 and any(j.get("synthesized") for j in (u.get("joints") or [])),
                 {"warnings": u.get("warnings"), "joints": self.res["urdf"]["joints"]})
        self.chk("usda_self_check_ok", bool(s.get("ok") and (s.get("self_check") or {}).get("ok")),
                 {"ok": s.get("ok"), "self_check": s.get("self_check"), "warnings": s.get("warnings")})
        # 单位体检：本场景是米制（0.5 单位 ≈ 0.5 m），却按 0.001 导出 ⇒ 必须自己报「单位可疑」
        self.chk("units_guard_fires_on_implausible_scale",
                 (u.get("units") or {}).get("scene_units_suspect") is True
                 and any("单位可疑" in str(w) for w in (u.get("warnings") or [])),
                 {"units": u.get("units"), "warnings": u.get("warnings")})
        # 小量级不能被写成 0：质量/惯量必须保住数量级（早先 "%.9f" 会把 8.6e-11 写成 0）
        import re as _re
        _txt = open(u["path"], encoding="utf-8").read()
        _zeros = _re.findall(r'(?:mass value|inertia ixx|iyy|izz)="0"', _txt)
        self.chk("SI 小量级不塌成 0（质量/惯量保数量级）", not _zeros,
                 {"zeros": _zeros[:6], "sample": _re.findall(r'<mass value="[^"]+"', _txt)[:4]})

    def hard_rules(self):
        # ---- ⑤ 空产出 / 无轴 必须失败（硬规则）
        empty_joint = json.loads(motion_joint("selftest_bad", ["MS_Nope"], ["MS_AlsoNope"]))
        empty_export = json.loads(motion_export_urdf(dir=self.outdir, name="empty", joints=[]))
        no_axis_measure = json.loads(motion_measure(jid="selftest_spare", phases=4))
        self.res["hard_rules"] = {"empty_joint_ok": empty_joint.get("ok"),
                                  "empty_export_ok": empty_export.get("ok"),
                                  "empty_export_error": empty_export.get("error"),
                                  "no_axis_measure_ok": no_axis_measure.get("ok"),
                                  "no_axis_measure_verdict": no_axis_measure.get("verdict"),
                                  "no_axis_measure_reason": no_axis_measure.get("reason")}
        self.chk("empty_joint_must_fail", empty_joint.get("ok") is False, {"resp": empty_joint})
        self.chk("empty_export_must_fail", empty_export.get("ok") is False, {"resp": empty_export.get("error")})
        self.chk("no_axis_measure_unresolved",
                 no_axis_measure.get("ok") is False and no_axis_measure.get("verdict") == "unresolved",
                 {"resp": self.res["hard_rules"]})

    def build_axis_scene(self):
        # ---- ⑥ 轴推断的三态（判据真的会咬人）：接触带可定轴 / 薄板必须 unresolved / 假设轴可被反驳
        bar2 = _mk_box("MS_Bar", (1.0, 0.08, 0.08), (-3.0, 0.0, 0.0))          # 顶面 z=0.04
        pl_a = _mk_box("MS_PlateNS", (0.5, 0.04, 0.3), (-3.4, 0.0, 0.17))      # 非方形：无旋转对称
        pl_b = _mk_box("MS_PlateSQ", (0.5, 0.04, 0.5), (-2.6, 0.0, 0.27))      # 方形：绕法线也对称
        pad2 = _mk_box("MS_Pad", (0.6, 0.3, 0.04), (-5.0, 0.0, 0.0))
        flap = _mk_box("MS_Flap", (0.5, 0.03, 0.4), (-5.0, 0.0, 0.25))         # 与 Pad 有 0.03 间隙
        for ob in (bar2, pl_a, pl_b, pad2, flap):
            self.made_objs.append(ob)
            self.made_meshes.append(ob.data)
        bpy.context.view_layer.update()

    def contact_axis(self):
        true_hinge = np.array([1.0, 0.0, 0.0])
        j_contact = json.loads(motion_joint("selftest_contact", ["MS_Bar"], ["MS_PlateNS"],
                                            kind="revolute", note="接触带定轴"))
        ax_c = j_contact.get("axis") or [0, 0, 0]
        ang_c = _angle_deg(ax_c, true_hinge)
        anc_off_c = _dist_point_line(j_contact.get("anchor") or [0, 0, 0], [0.0, 0.0, 0.04], true_hinge)
        self.res["infer_contact"] = {"verdict": j_contact.get("axis_verdict"),
                                     "source": j_contact.get("axis_source"),
                                     "axis": j_contact.get("axis"), "anchor": j_contact.get("anchor"),
                                     "angle_to_true_deg": _r(ang_c, 4),
                                     "anchor_offset": _r(anc_off_c), "warnings": j_contact.get("warnings")}
        self.chk("contact_strip_axis_supported",
                 j_contact.get("axis_verdict") == "supported" and j_contact.get("axis_source") == "contact_region"
                 and ang_c is not None and ang_c < 5.0,
                 self.res["infer_contact"])
        self.chk("contact_strip_anchor_near_hinge_line", anc_off_c is not None and anc_off_c * _scene_mpu() <= 0.005,
                 {"offset_scene_unit": _r(anc_off_c), "offset_m": _r((anc_off_c or 0) * _scene_mpu()),
                  "tol_m": 0.005})

    def slab_and_square(self):
        j_slab = json.loads(motion_joint("selftest_slab", ["MS_Pad"], ["MS_Flap"], kind="revolute",
                                         note="薄板无销：必须 unresolved"))
        inf_slab = json.loads(motion_infer_axis(jid="selftest_slab"))
        self.res["infer_slab_unresolved"] = {"joint_axis_verdict": j_slab.get("axis_verdict"),
                                             "axis": j_slab.get("axis"),
                                             "verdict": inf_slab.get("verdict"),
                                             "best_score": [{"label": c.get("label"), "score": c.get("score"),
                                                             "degenerate": c.get("degenerate")}
                                                            for c in (inf_slab.get("evidence") or {}).get("per_object", [])],
                                             "contact": {k: (inf_slab.get("evidence") or {}).get("contact", {}).get(k)
                                                         for k in ("available", "reason", "close_points", "elongation")},
                                             "missing_evidence": inf_slab.get("missing_evidence"),
                                             "reason": inf_slab.get("reason")}
        self.chk("slab_without_pin_is_unresolved",
                 j_slab.get("axis") is None and j_slab.get("axis_verdict") == "unresolved"
                 and inf_slab.get("verdict") == "unresolved" and inf_slab.get("missing_evidence"),
                 self.res["infer_slab_unresolved"])
        j_sq = json.loads(motion_joint("selftest_square", ["MS_Bar"], ["MS_PlateSQ"], kind="revolute",
                                       note="方形板：对称轴与接触带矛盾"))
        inf_sq = json.loads(motion_infer_axis(jid="selftest_square"))
        self.res["infer_conflict"] = {"joint_axis_verdict": j_sq.get("axis_verdict"),
                                      "verdict": inf_sq.get("verdict"),
                                      "axis_conflict": inf_sq.get("axis_conflict"),
                                      "reason": inf_sq.get("reason")}
        self.chk("square_plate_conflict_unresolved",
                 inf_sq.get("verdict") == "unresolved" and bool(inf_sq.get("axis_conflict"))
                 and inf_sq.get("axis") is None,
                 self.res["infer_conflict"])

    def hypothesis_cases(self):
        motion_joint("selftest_wrongaxis", ["MS_Base"], ["MS_Pin"], kind="revolute",
                     axis=[1.0, 0.0, 0.0], anchor=[0.0, 0.0, 0.25], note="故意给错轴（child 只有销）")
        inf_wrong = json.loads(motion_infer_axis(jid="selftest_wrongaxis"))
        self.res["infer_wrong_axis"] = {"verdict": inf_wrong.get("verdict"), "reason": inf_wrong.get("reason"),
                                        "nearest": ((inf_wrong.get("hypothesis") or {}).get("evidence") or {}).get("candidates")}
        self.chk("wrong_axis_hypothesis_refuted", inf_wrong.get("verdict") == "refuted",
                 self.res["infer_wrong_axis"])
        motion_joint("selftest_ambiguous", ["MS_Base"], ["MS_Door", "MS_Pin"], kind="revolute",
                     axis=[1.0, 0.0, 0.0], anchor=[0.0, 0.0, 0.25], note="假设方向只是近失候选")
        inf_amb = json.loads(motion_infer_axis(jid="selftest_ambiguous"))
        self.res["infer_ambiguous"] = {"verdict": inf_amb.get("verdict"), "reason": inf_amb.get("reason"),
                                       "nearest": ((inf_amb.get("hypothesis") or {}).get("evidence") or {}).get("candidates")}
        self.chk("ambiguous_hypothesis_not_refuted", inf_amb.get("verdict") == "unresolved",
                 self.res["infer_ambiguous"])

    def cleanup(self, keep=False):
        removed, failed = [], []
        for ob in self.made_objs:
            try:
                nm = str(ob.name)          # 先取名：remove 之后 StructRNA 就失效了
            except Exception:
                nm = "?"
            try:
                bpy.data.objects.remove(ob, do_unlink=True)
                removed.append(nm)
            except Exception as e:
                failed.append({"name": nm, "error": str(e)[:120]})
        for me in self.made_meshes:
            try:
                bpy.data.meshes.remove(me, do_unlink=True)
            except Exception:
                pass
        if not keep:
            for d in self.made_dirs:
                try:
                    shutil.rmtree(d, ignore_errors=True)
                except Exception:
                    pass
        try:
            bpy.context.view_layer.update()
        except Exception:
            pass
        self.res["cleaned"] = {"removed_objects": removed, "failed": failed,
                               "objects_left": sorted(o.name for o in bpy.data.objects),
                               "pre_existing": self.pre_names,
                               "dir_removed": (not keep),
                               "dir": self.outdir}

    def run(self):
        self.build_scene()
        self.register_and_infer()
        self.sweeps()
        self.exports()
        self.hard_rules()
        self.build_axis_scene()
        self.contact_axis()
        self.slab_and_square()
        self.hypothesis_cases()


def motion_selftest(keep=False):
    """合成自检：造铰链（底板 + 门板 + 沿 Z 的轴）→ 实测轴 → 无阻挡扫掠 → 加挡块扫掠 →
    URDF/USDA 结构自检 → 还原证明。不依赖用户场景（预先存在的对象一律 exclude），最后清理。
    """
    t0 = time.perf_counter()
    st = _Selftest()
    try:
        st.run()
    except Exception as e:
        st.checks.append({"name": "selftest_exception", "ok": False,
                          "detail": "%s: %s" % (type(e).__name__, str(e)[:300]),
                          "traceback": traceback.format_exc()[-1200:]})
    finally:
        st.cleanup(keep)

    fails = [c for c in st.checks if not c["ok"]]
    ok = not fails
    if not (st.res.get("cleaned") or {}).get("removed_objects"):
        ok = False
        fails.append({"name": "cleanup_ran", "ok": False, "detail": "清理没有执行"})
    _log("selftest", {"ok": ok, "fails": [c["name"] for c in fails]})
    return _j({
        "ok": ok, "op": "selftest", "version": MOTION_VERSION,
        "checks": st.checks, "fails": fails, "fail_count": len(fails),
        "measured": st.res,
        "expect": ("销（圆柱，沿 Z）可辨识 ⇒ 轴实测 supported 且与真轴夹角 <5°、锚点在铰链线上；"
                   "无阻挡 8 相位扫掠 = supported（零干涉且确实位移）；把挡块放进扫掠路径 ⇒ 同一关节变 "
                   "refuted 且点名 MS_Blocker（最小间距 <0）；URDF/USDA 结构自检全过（含 spherical 降级与"
                   "孤儿 link 合成单树）；扫掠前后所有对象 matrix_world 逐项相等；空产出/无轴必须 ok=false"),
        "notes": ["自检不碰用户场景：预先存在的对象一律 exclude 且不删",
                  "临时对象/文件在 finally 里清理（keep=true 可保留产物用于排查）"],
        "ms": int((time.perf_counter() - t0) * 1000),
    })
