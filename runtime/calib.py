# -*- coding: utf-8 -*-
"""DSH 门标定套件（v0.9.6 · 整合方案 P0）—— 回答一个问题：**你的门到底抓得住什么？**

"插件的价值上限由你自己的门决定"——那门的上限又由什么决定？由**它抓不住的缺陷**决定。
所以：故意注入若干类已知缺陷，看每类有没有被某条门抓住；同时跑一次**健康基线**（必须全过），
否则说明门在误报，标定结果无效。

    健康基线（4 条门全过）  ← 假阳性对照，不过就让整次标定作废
    8 类缺陷注入            → 每类判 caught / missed
    ⇒ 捕获率 + 漏网清单 + 是谁抓住的（哪条门）

**用法**：请在**无头干净场景**里跑（会临时建/删对象，dup 门需要干净场景）：
    blender_rt_headless(preload="calib", script='print("HEADLESS " + K.dsh_calib_api("run", {}))')
或 plan：blender_rt_plan(op="calib_run")   （会自己起无头进程）
"""
import json
import time

import bpy
import bmesh

CALIB_VERSION = 2
PFX = "__dsh_calib_"
# 本套几何按 **mm** 写（底板 3mm、互插 1.5mm），门的口径也是 mm（micro 0.3mm / min_mm 1.0）
# —— 所以基线**和每一类缺陷**都必须在 1 单位 = 1 mm 下跑。原来只给基线设 CALIB_SCALE、
# 缺陷段却复位回场景单位（prev_scale），两段口径不同 ⇒ 捕获率不可信（v0.9.6 复审修复）。
CALIB_SCALE = 0.001


def _j(o):
    return json.dumps(o, ensure_ascii=False, default=str)


def _api(name):
    import sys
    K = sys.modules.get("dsh_rt_kernel")
    return getattr(K, "dsh_%s_api" % name, None) if K is not None else None


def _purge():
    for o in [x for x in bpy.data.objects if x.name.startswith(PFX)]:
        me = o.data
        bpy.data.objects.remove(o, do_unlink=True)
        try:
            if me is not None and me.users == 0:
                bpy.data.meshes.remove(me, do_unlink=True)
        except Exception:
            pass
    for m in [x for x in bpy.data.meshes if x.name.startswith(PFX)]:
        try:
            bpy.data.meshes.remove(m, do_unlink=True)
        except Exception:
            pass
    for m in [x for x in bpy.data.materials if x.name.startswith(PFX)]:
        try:
            bpy.data.materials.remove(m, do_unlink=True)
        except Exception:
            pass


def _mk(name, builder, loc=(0, 0, 0)):
    """builder(bm) 往 bmesh 里加几何；返回对象名。"""
    me = bpy.data.meshes.new(PFX + name)
    ob = bpy.data.objects.new(PFX + name, me)
    ob.location = loc
    bpy.context.scene.collection.objects.link(ob)
    bm = bmesh.new()
    builder(bm)
    bm.to_mesh(me)
    bm.free()
    return ob.name


def _build_healthy(with_rivet=True, with_selfint=False):
    """健康装配：底板（3mm 厚）+ 两根柱子（**互插**底板 1mm，工艺要求的做法）± 铆钉。"""
    import mathutils

    def plate(bm):
        bmesh.ops.create_cube(bm, size=1.0, matrix=mathutils.Matrix.Diagonal((40.0, 30.0, 3.0, 1.0)))

    def post(bm):
        bmesh.ops.create_cube(bm, size=1.0, matrix=mathutils.Matrix.Diagonal((8.0, 8.0, 20.0, 1.0)))

    names = [_mk("plate", plate)]
    names.append(_mk("post_a", post, loc=(-10.0, 0.0, 10.0)))    # 底面 z=0 与底板顶面 z=1.5 互插
    names.append(_mk("post_b", post, loc=(10.0, 0.0, 10.0)))
    if with_rivet:
        names.append(_mk("rivet", post, loc=(0.0, 0.0, 4.0)))
    if with_selfint:
        def two(bm):
            bmesh.ops.create_cube(bm, size=1.0, matrix=mathutils.Matrix.Diagonal((6.0, 6.0, 6.0, 1.0)))
            bmesh.ops.create_cube(bm, size=1.0,
                                  matrix=mathutils.Matrix.Translation((3.0, 0.0, 0.0)) @ mathutils.Matrix.Diagonal((6.0, 6.0, 6.0, 1.0)))
        names.append(_mk("selfint", two, loc=(0.0, 25.0, 3.0)))
    bpy.context.view_layer.objects.active = bpy.data.objects[names[0]]
    return names


SPEC_TMPL = {"name": "calib", "units": "mm", "tolerance_mm": 1.0, "gates": [
    {"id": "mesh", "op": "audit_mesh", "args": {}, "pass_if": "clean == True and totals[\"self_intersections\"] == 0"},
    {"id": "connectivity", "op": "audit_gate", "args": {}, "pass_if": "verdict == 'pass'"},
    {"id": "print", "op": "print_report", "args": {"min_mm": 1.0},
     "pass_if": "worst_p05_mm >= 1.0 and walls_ok == True"},
    {"id": "duplicates", "op": "audit_duplicates", "args": {"top_k": 5}, "pass_if": "len(materials) + len(meshes) == 0"},
]}


def _spec_for(names, break_gates=None):
    sp = json.loads(_j(SPEC_TMPL))
    sp["gates"][0]["args"] = {"objects": names, "self_intersect": True, "summary_only": True}
    sp["gates"][1]["args"] = {"objects": names}
    sp["gates"][2]["args"] = {"objects": names, "min_mm": 1.0}
    broken = set(str(x) for x in (break_gates or []))
    for g in sp["gates"]:
        if g["id"] in broken:
            # 故意把这条门弄成 error（pass_if 引用回执里不存在的字段）—— 用来证明
            # "门跑不起来"绝不会被记成"抓到了缺陷"（回归测试用）。
            g["pass_if"] = "definitely_not_a_field == 1"
    return sp


def _classify_defect(gates, expect):
    """把一次门的回执判成"真捕获"（v0.9.6 复审修复）。

    **只有 state == 'fail' 才算捕获**。原来 `caught = verdict != 'pass'` —— 于是
      * error（门跑不起来：模块没注入 / pass_if 写错 / op 抛异常）
      * degraded（门没给出结论）
    都被记成"抓到了"，捕获率虚高（实测：把 print 模块抽掉后仍然报 8/8，其中 thin_wall 名义上
    只被 print"抓住"，而那条门其实 error）。判据是"对应门真 fail"，不是"整包不是 pass"。

    返回 (caught, fails, errors, degraded)。
    """
    gs = [g for g in (gates or []) if isinstance(g, dict)]
    fails = [g.get("id") for g in gs if g.get("state") == "fail"]
    errors = [g.get("id") for g in gs if g.get("state") == "error"]
    degraded = [g.get("id") for g in gs if g.get("state") == "degraded"]
    exp = [g for g in gs if g.get("id") == expect]
    caught = bool(exp) and exp[0].get("state") == "fail"
    return bool(caught), fails, errors, degraded


def _scale_now():
    try:
        return float(bpy.context.scene.unit_settings.scale_length)
    except Exception:
        return None


def _near(a, b, tol=1e-6):
    """scale_length 是 **float32** 属性（0.001 存成 0.001000000047...）⇒ 比较要给相对容差。"""
    try:
        return abs(float(a) - float(b)) <= tol * max(1.0, abs(float(a)), abs(float(b)))
    except Exception:
        return False


# ---------------------------------------------------------------- 缺陷注入

def _d_delete_face(names, ctx):
    ob = bpy.data.objects[names[1]]
    bm = bmesh.new(); bm.from_mesh(ob.data)
    bm.faces.ensure_lookup_table()
    bmesh.ops.delete(bm, geom=[bm.faces[0]], context="FACES_ONLY")
    bm.to_mesh(ob.data); bm.free()
    return "删掉柱子上一个面（开口 → boundary_edges）"


def _d_flip_normals(names, ctx):
    ob = bpy.data.objects[names[1]]
    bm = bmesh.new(); bm.from_mesh(ob.data)
    bmesh.ops.reverse_faces(bm, faces=bm.faces[:])
    bm.to_mesh(ob.data); bm.free()
    return "把柱子的法线全反过来"


def _d_loose_verts(names, ctx):
    ob = bpy.data.objects[names[1]]
    bm = bmesh.new(); bm.from_mesh(ob.data)
    for p in ((0, 0, 0), (5, 5, 5)):
        bm.verts.new(p)
    bm.to_mesh(ob.data); bm.free()
    return "加了两个孤立顶点（原点云残留）"


def _d_self_intersect(names, ctx):
    extra = _build_healthy(with_rivet=False, with_selfint=True)
    ctx["extra"] = [n for n in extra if n.startswith(PFX + "selfint")]
    return "加了一个自交件（两块壳互穿在同一对象里）"


def _d_shift_detach(names, ctx):
    ob = bpy.data.objects[names[1]]
    ob.location = (ob.location[0], ob.location[1] + 60.0, ob.location[2])
    return "把柱子平移 60mm 脱离底板（真浮块）"


def _d_floater(names, ctx):
    def cube(bm):
        import mathutils
        bmesh.ops.create_cube(bm, size=6.0, matrix=mathutils.Matrix.Diagonal((6.0, 6.0, 6.0, 1.0)))
    ctx["extra"] = [_mk("floater", cube, loc=(0.0, 80.0, 3.0))]
    return "远处留一个孤立件"


def _d_thin_wall(names, ctx):
    bpy.context.scene.unit_settings.scale_length = CALIB_SCALE   # 1 单位 = 1 mm（与全段一致）
    ob = bpy.data.objects[names[0]]
    ob.scale = (1.0, 1.0, 0.1)                                # 底板 3mm → 0.3mm
    return "底板减薄到 0.3mm（低于 min_mm=1.0）"


def _d_dup_resource(names, ctx):
    me = bpy.data.meshes.new(PFX + "DupMesh")
    bpy.data.objects.new(PFX + "dup_holder_a", me)
    me2 = bpy.data.meshes.new(PFX + "DupMesh.001")
    bpy.data.objects.new(PFX + "dup_holder_b", me2)
    for n in (PFX + "DupMat", PFX + "DupMat.001"):
        bpy.data.materials.new(n).use_nodes = True
    return "造同名分叉资源（Foo / Foo.001）"


DEFECTS = [
    # (id, 注入函数, **应该抓住它的那条门**, 为什么是它)
    ("delete_face", _d_delete_face, "mesh", "开口 ⇒ boundary/nonmanifold ⇒ clean=false"),
    ("flip_normals", _d_flip_normals, "mesh", "单壳整体朝内 ⇒ normals_inverted ⇒ clean=false"),
    ("loose_verts", _d_loose_verts, "mesh", "孤立点 ⇒ loose_verts>0 ⇒ clean=false（v0.9.6 修）"),
    ("self_intersect", _d_self_intersect, "mesh", "同对象两块壳互穿 ⇒ self_intersections>0"),
    ("shift_detach", _d_shift_detach, "connectivity", "柱子脱离底板 ⇒ 可见浮块"),
    ("floater", _d_floater, "connectivity", "远处孤立件 ⇒ 可见浮块"),
    ("thin_wall", _d_thin_wall, "print", "底板 0.3mm < min_mm=1.0"),
    ("dup_resource", _d_dup_resource, "duplicates", "Foo / Foo.001 同名分叉"),
]
DEFECT_TABLE = {d[0]: {"fn": d[1], "expect": d[2], "why": d[3]} for d in DEFECTS}


def calib_run(ids=None, keep=False, prefix_note=None, break_gates=None):
    """跑标定：健康基线（必须全过）+ 每类缺陷（必须被**对应门**真抓到）。

    v0.9.6（复审修复）：
      ① 捕获判据从"整包 verdict != pass"改成"**对应门 state == fail**" —— error（门跑不起来）
         和 degraded（门没给结论）不算捕获，避免捕获率虚高；
      ② 单位口径统一：基线与每一类缺陷都在 CALIB_SCALE（1 单位 = 1 mm）下跑，finally 复位场景单位；
         每行回执带 unit_scale，跑完回报 unit_ok。
    break_gates=["mesh", ...] 故意把某些门弄成 error，用于验证 ① （回归测试用）。
    """
    gate = _api("gate")
    if gate is None:
        return _j({"ok": False, "error": "需要 gate 模块（preload=\"calib,gate\" 或走 calib_run op）"})
    want = [d[0] for d in DEFECTS]
    if ids:
        want = [str(x) for x in (ids if isinstance(ids, (list, tuple)) else [ids])]
        bad = [x for x in want if x not in DEFECT_TABLE]
        if bad:
            return _j({"ok": False, "error": "未知缺陷 id %s" % bad, "allowed": [d[0] for d in DEFECTS]})
    broken = [str(x) for x in (break_gates if isinstance(break_gates, (list, tuple, set)) else ([break_gates] if break_gates else []))]
    t0 = time.time()
    prev_scale = _scale_now()
    scale_after = None
    rows = []
    baseline = None
    try:
        # 整条链路（基线 + 每一类缺陷）统一到 mm 口径；只在 finally 复位场景单位一次
        bpy.context.scene.unit_settings.scale_length = CALIB_SCALE
        _purge()
        names = _build_healthy()
        sp = _spec_for(names, break_gates=broken)
        base = gate("run", {"spec": sp})          # _DshApi 已解析，别再 json.loads
        b_gates = [{"id": g.get("id"), "state": g.get("state")} for g in base.get("gates", [])]
        b_fail = [g["id"] for g in b_gates if g.get("state") == "fail"]
        b_err = [g["id"] for g in b_gates if g.get("state") == "error"]
        b_deg = [g["id"] for g in b_gates if g.get("state") == "degraded"]
        baseline = {"verdict": base.get("verdict"), "failed": base.get("failed"), "gates": b_gates,
                    "errors": b_err, "degraded": b_deg, "unit_scale": _scale_now(),
                    "ok": bool(base.get("verdict") == "pass" and not b_err and not b_deg
                               and not b_fail)}
        _purge()
        for did in want:
            fn, expect = DEFECT_TABLE[did]["fn"], DEFECT_TABLE[did]["expect"]
            ctx = {}
            bpy.context.scene.unit_settings.scale_length = CALIB_SCALE
            _purge()
            row = {"id": did, "expect": expect, "unit_scale": _scale_now()}
            try:
                names = _build_healthy()
                note = fn(names, ctx)
                for n in ctx.get("extra") or []:
                    if n not in names:
                        names.append(n)
                row["injected"] = note
                # 缺陷函数可能自己动过单位（如 thin_wall 会写 scale_length）⇒ 跑门前拉回校准口径，
                # 保证"注入的缺陷"和"门的口径"在同一单位下比较（否则 0.3mm 门槛可能变成 0.3 单位）
                bpy.context.scene.unit_settings.scale_length = CALIB_SCALE
                sp = _spec_for(names, break_gates=broken)
                r = gate("run", {"spec": sp})
                caught, fails, errors, degraded = _classify_defect(r.get("gates"), expect)
                row.update({"caught": bool(caught), "verdict": r.get("verdict"),
                            "caught_by": fails, "caught_by_expected_gate": bool(expect in fails),
                            "gate_errors": errors, "gate_degraded": degraded,
                            "detail": {g.get("id"): g.get("key") for g in r.get("gates", [])
                                       if g.get("state") != "pass"}})
                if not caught:
                    row["naive_caught"] = bool(r.get("verdict") != "pass")   # 旧口径会把它误记成 caught
                    if expect in errors:
                        row["why_missed"] = "对应门 %s 自己 error —— 门跑不起来 ≠ 抓到缺陷" % expect
                    elif expect in degraded:
                        row["why_missed"] = "对应门 %s 只给 degraded —— 没给出结论 ≠ 抓到缺陷" % expect
                    else:
                        row["why_missed"] = "对应门 %s 没有 fail（真漏网）" % expect
            except Exception as e:
                row.update({"caught": None, "why_missed": "注入/执行异常",
                            "error": "%s: %s" % (type(e).__name__, str(e)[:180])})
            finally:
                # ← 复位到 CALIB_SCALE（不是 prev_scale）：余下的校准段仍在同一口径里
                try:
                    bpy.context.scene.unit_settings.scale_length = CALIB_SCALE
                except Exception:
                    pass
                _purge()
            rows.append(row)
    finally:
        try:
            bpy.context.scene.unit_settings.scale_length = prev_scale
            scale_after = _scale_now()
        except Exception:
            scale_after = None
        if not keep:
            _purge()
    caught = [r for r in rows if r.get("caught") is True]
    missed = [r["id"] for r in rows if r.get("caught") is not True]
    gate_errors = sorted({g for r in rows for g in (r.get("gate_errors") or [])} |
                         set((baseline or {}).get("errors") or []))
    # 只在"那条门恰好是某类缺陷的对应门"时才算问题（已在 missed 里体现）；其余 gate error 是噪音/告警
    blocking_errors = sorted({g for r in rows for g in (r.get("gate_errors") or []) if g == r.get("expect")})
    false_captures = [{"id": r["id"], "naive_verdict": r.get("verdict"), "why": r.get("why_missed")}
                      for r in rows if r.get("caught") is not True and r.get("naive_caught")]
    baseline_ok = bool(baseline and baseline.get("ok"))
    unit_ok = bool(
        prev_scale is not None and scale_after is not None and _near(scale_after, prev_scale)
        and all(_near(r.get("unit_scale"), CALIB_SCALE) for r in rows))
    ok = bool(baseline_ok and not missed and unit_ok)
    warnings = []
    if gate_errors:
        warnings.append("有门在实际执行里报 error：%s —— error 不算捕获（门跑不起来 ≠ 抓到缺陷），"
                        "capture_rate 已按新口径剔除" % gate_errors)
    if false_captures:
        warnings.append("false_captures 里的用例若用旧口径（verdict != pass）会被误记成 captured")
    return _j({"ok": ok, "baseline_pass": baseline_ok, "baseline": baseline,
               "defects": rows, "caught": len(caught), "total": len(rows),
               "capture_rate": (round(len(caught) / float(len(rows)), 3) if rows else None),
               "missed": missed, "gate_errors": gate_errors, "blocking_gate_errors": blocking_errors,
               "false_captures": false_captures, "warnings": warnings,
               "units": {"calib_scale": CALIB_SCALE, "scene_before": prev_scale, "scene_after": scale_after,
                         "defect_scales": sorted({r.get("unit_scale") for r in rows},
                                                 key=lambda x: (x is None, x)),
                         "unit_ok": unit_ok},
               "valid_run": baseline_ok, "unit_ok": unit_ok, "break_gates": broken,
               "rule": "captured ⇔ 该缺陷的**对应门**（defects[].expect）state=='fail'；"
                       "error/degraded 一律不算捕获",
               "note": ("健康基线必须全过（否则门在误报，标定无效）；每类缺陷必须被**对应门**（defects[].expect）"
                        "以 state='fail' 抓住 —— error（门跑不起来）/degraded（没给结论）都不算捕获；"
                        "漏网的 id 就是你要补的门" if baseline_ok else
                        "⚠ 健康基线没过 ⇒ 本次标定无效：先看 baseline.gates 里哪条门是 error/degraded/fail"
                        "（门配置错/模块没注入/场景不干净），请在无头 factory 场景跑"),
               "ms": int((time.time() - t0) * 1000)})


def calib_selftest():
    """自检：基线必须过；8 类缺陷必须**各自被对应门**真抓到；单位用完必须复位到原值。

    另固定两条回归（v0.9.6 复审修复）：
      ① 分类器：error / degraded **不算**捕获（"门跑不起来"≠"抓到缺陷"）；
      ② 抽掉一条门（break_gates）时 ok 必须 false、gate_errors 非空 —— 不许假通过。
    """
    # ① 纯分类器单测（不依赖场景）
    g_err = [{"id": "mesh", "state": "error"}, {"id": "connectivity", "state": "pass"}]
    g_deg = [{"id": "print", "state": "degraded"}]
    g_fail = [{"id": "mesh", "state": "fail"}]
    c1 = _classify_defect(g_err, "mesh")
    c2 = _classify_defect(g_deg, "print")
    c3 = _classify_defect(g_fail, "mesh")
    c4 = _classify_defect([{"id": "mesh", "state": "pass"}], "mesh")
    classifier_ok = (c1[0] is False and c2[0] is False and c3[0] is True and c4[0] is False
                     and c1[1] == [] and c1[2] == ["mesh"] and c2[3] == ["print"])
    r = json.loads(calib_run())
    # ② 故意让 mesh 门 error：不许因此把 delete_face/flip_normals/loose_verts/self_intersect 记成"抓到"
    rb = json.loads(calib_run(ids=["delete_face", "loose_verts"], break_gates=["mesh"]))
    broken_rows = rb.get("defects") or []
    broken_ok = (rb.get("ok") is False and bool(rb.get("gate_errors"))
                 and all(x.get("caught") is not True for x in broken_rows)
                 and all(x.get("caught_by_expected_gate") is False for x in broken_rows))
    return _j({"ok": bool(classifier_ok and r.get("valid_run") and r.get("unit_ok")
                          and (r.get("capture_rate") or 0) >= 1.0 and not r.get("missed") and broken_ok),
               "classifier_rejects_error_and_degraded": classifier_ok,
               "broken_gate_not_counted_as_caught": broken_ok,
               "capture_rate": r.get("capture_rate"), "missed": r.get("missed"),
               "baseline_pass": r.get("baseline_pass"), "unit_ok": r.get("unit_ok"),
               "units": r.get("units"),
               "evidence": {"caught": r.get("caught"), "total": r.get("total"),
                            "gate_errors": r.get("gate_errors"),
                            "baseline_failed": (r.get("baseline") or {}).get("failed"),
                            "expect_map": {d["id"]: d["expect"] for d in (r.get("defects") or [])}}})


def calib_help():
    return _j({
        "module": "calib.py", "version": CALIB_VERSION,
        "what": "门标定：注入 8 类已知缺陷，量你的门抓得住几类；同时用健康基线做假阳性对照",
        "defects": [{"id": d[0], "expect_gate": d[2], "why": d[3]} for d in DEFECTS],
        "gates_used": [g["id"] + " → " + g["op"] for g in SPEC_TMPL["gates"]],
        "ops": ["calib_run（全量或 ids=[...]；break_gates=[...] 故意让门 error 做回归）",
                "calib_selftest", "calib_help"],
        "capture_rule": "只有**对应门 state=='fail'** 才算捕获；error（门跑不起来）/degraded（没给结论）不算 —— "
                        "旧口径 'verdict != pass 就算抓到' 会让坏掉的门刷高捕获率",
        "units": "基线与每一类缺陷都在 scale_length=%s（1 单位 = 1 mm）下跑，跑完复位场景单位；"
                 "回执 units.unit_ok=false 说明单位没管住，本次结论不可信" % CALIB_SCALE,
        "how": ["优先走 blender_rt_plan(op='calib_run')（插件自己起无头干净场景）",
                "或 blender_rt_headless(preload='calib,gate,printcheck', "
                "script='print(\"HEADLESS \" + K.dsh_calib_api(\"run\", {}))')"],
        "reading": ["baseline_pass=false ⇒ 本次标定无效（门在误报/门配置错/场景不干净）",
                    "missed 列表 = 你的门的盲区 = 下一步要补的门",
                    "gate_errors 非空 ⇒ 有门根本没跑起来，先修门再谈捕获率",
                    "false_captures ⇒ 旧口径会误判成捕获的那些用例",
                    "capture_rate 是这个项目门强度的**一个数字**；换门/加门后再跑一次即可对比"],
    })


def calib_dispatch(op, args_json):
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
    ops = {"run": calib_run, "selftest": calib_selftest, "help": calib_help}
    fn = ops.get(str(op))
    if fn is None:
        return _j({"ok": False, "error": "unknown calib op", "op": op, "ops": sorted(ops)})
    import inspect
    try:
        allowed = set(inspect.signature(fn).parameters)
        unknown = sorted(set(kw) - allowed)
        if unknown:
            return _j({"ok": False, "error": "不认识的参数 %s" % unknown, "op": op, "allowed": sorted(allowed)})
    except (TypeError, ValueError):
        pass
    try:
        return fn(**kw)
    except Exception as e:
        return _j({"ok": False, "error": "%s: %s" % (type(e).__name__, str(e)[:220]), "op": op})


class _DshApi(dict):
    def __call__(self, op=None, args=None, **kw):
        if op is None or isinstance(op, dict):
            args, op = (op if isinstance(op, dict) else args), "help"
        payload = args if isinstance(args, str) else _j(dict(args or {}, **kw))
        return json.loads(self["dispatch"](str(op), payload))

    def call(self, op, args=None, **kw):
        return self(op, args, **kw)


import sys as _sys
_K = _sys.modules.get("dsh_rt_kernel")
if _K is not None:
    _K.dsh_calib_api = _DshApi({"version": CALIB_VERSION, "dispatch": calib_dispatch,
                                "run": calib_run, "selftest": calib_selftest, "help": calib_help})
