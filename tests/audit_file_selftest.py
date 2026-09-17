# -*- coding: utf-8 -*-
"""audit.py 文件级算子自检（C1/C2）：audit_overlap / audit_interference / file= 三条 op + 清理证明。

跑法（无头；engine=none 只为省引擎前导）：
    blender_rt_headless(preload="audit", engine="none", factory_startup=True, timeout_ms=300000,
        script="_p=K.win_path('/home/sixtyseven67/DSH/dsh-blender-plugin/tests/audit_file_selftest.py');"
               "exec(compile(open(_p,encoding='utf-8').read(),'x','exec'),globals())")
    # 也可以直接： blender -b --factory-startup --python tests/audit_file_selftest.py

合成场景（**绝不碰用户 GUI 场景**：--factory-startup + 开头 st_clean_scene()，全在临时目录里造）：
    A.blend   DSH_SELFTEST_CUBE，1×1×1 立方体 @ 世界原点        （单位盒 [-0.5,0.5]³）
    B.blend   同一立方体沿 +X 平移 0.5                          （[0,1]×[-0.5,0.5]²）
    C.blend   同 B 但删掉一个面（开放网格：boundary_edges=4）    → 必须 unresolved，缺证据不许猜
    D.blend   同一立方体沿 +X 平移 2.0                          （AABB 不相交）
解析解：A∩B = 0.5×1×1 = 0.5 单位³。本机 scene.unit_settings.scale_length = 1.0 → _units() = (1.0, 1000.0)
        → 1 单位³ = 10⁹ mm³ → **5×10⁸ mm³**（脚本用返回值里的 units.mm_per_unit 现场复核，不硬套）。
判据对着实测数字写：不达标改代码，不改断言。
"""
import json as _json
import os as _os
import shutil as _shutil
import sys as _sys
import tempfile as _tempfile

import bmesh as _bmesh
import bpy as _bpy

ST_CTX = "DSH_SELFTEST_CUBE"
ST_PASS, ST_FAILS, ST_SKIP, ST_MEAS, ST_BLENDS = [], [], [], {}, {}


def st_check(name, cond, detail=""):
    if cond:
        ST_PASS.append(name)
        print("  [OK]   " + name)
    else:
        ST_FAILS.append({"clause": name, "detail": str(detail)[:500]})
        print("  [FAIL] " + name + "  <- " + str(detail)[:300])


def st_note(name, detail):
    ST_SKIP.append({"clause": name, "detail": str(detail)[:300]})
    print("  [SKIP] " + name + "  " + str(detail)[:200])


def st_get_api():
    """拿 K.dsh_audit_api（preload / 引擎注入）；没有就从 runtime/audit.py 兜底 exec 一份。"""
    K = globals().get("K")
    if K is None:
        K = _sys.modules.get("dsh_rt_kernel")
    api = getattr(K, "dsh_audit_api", None) if K is not None else None
    if api is not None:
        return api, "K.dsh_audit_api"
    cands = []
    if "__file__" in globals():
        cands.append(_os.path.join(_os.path.dirname(_os.path.abspath(globals()["__file__"])),
                                   _os.pardir, "runtime", "audit.py"))
    cands += ["/home/sixtyseven67/DSH/dsh-blender-plugin/runtime/audit.py",
              "dsh-blender-plugin/runtime/audit.py"]
    for p in cands:
        for q in (p, _os.path.abspath(p)):
            if _os.path.exists(q):
                ns = {"__name__": "dsh_audit_selftest_inline"}
                exec(compile(open(q, encoding="utf-8").read(), q, "exec"), ns)
                return ns, q
    return None, None


ST_API, ST_API_SRC = st_get_api()


def st_call(op, args=None):
    """统一调用：优先 API 对象可调用（v0.9.1 形态），否则走 dispatch（返回 str → json.loads）。"""
    if callable(ST_API):
        return ST_API(op, args or {})
    if isinstance(ST_API, dict) and "dispatch" in ST_API:
        return _json.loads(ST_API["dispatch"](str(op), _json.dumps(args or {})))
    return _json.loads(ST_API["dispatch"](str(op), _json.dumps(args or {})))


def st_clean_scene():
    for ob in list(_bpy.data.objects):
        try:
            _bpy.data.objects.remove(ob, do_unlink=True)
        except Exception:
            pass
    for me in list(_bpy.data.meshes):
        try:
            if me.users == 0:
                _bpy.data.meshes.remove(me)
        except Exception:
            pass


def st_cube(name, loc, drop_face=False):
    """1×1×1 立方体（[-0.5,0.5]³ 再平移到 loc）；drop_face=True 删一个面 → 开放网格。"""
    me = _bpy.data.meshes.new(name + "_mesh")
    bm = _bmesh.new()
    _bmesh.ops.create_cube(bm, size=1.0)
    if drop_face:
        bm.faces.ensure_lookup_table()
        _bmesh.ops.delete(bm, geom=[bm.faces[0]], context="FACES")
    bm.to_mesh(me)
    bm.free()
    ob = _bpy.data.objects.new(name, me)
    ob.location = tuple(loc)
    _bpy.context.scene.collection.objects.link(ob)
    return ob


def st_save(path):
    _bpy.ops.wm.save_as_mainfile(filepath=path, copy=True, check_existing=False)


def st_counts():
    return {"objects": len(_bpy.data.objects), "collections": len(_bpy.data.collections),
            "libraries": len(_bpy.data.libraries), "meshes": len(_bpy.data.meshes),
            "materials": len(_bpy.data.materials), "images": len(_bpy.data.images)}


def st_boxes_equal(x, y, tol=1e-9):
    """逐项比较 bbox 字典（min/max/size，3 轴都要相等）。"""
    if not x or not y:
        return False
    for k in ("min", "max", "size"):
        a, b = x.get(k), y.get(k)
        if a is None or b is None or len(a) != len(b):
            return False
        for i in range(3):
            if abs(float(a[i]) - float(b[i])) > tol:
                return False
    return True


def st_main():
    A = None
    tmp = _tempfile.mkdtemp(prefix="dsh_audit_fileselftest_")
    A = _os.path.join(tmp, "A.blend")
    B = _os.path.join(tmp, "B.blend")
    C = _os.path.join(tmp, "C_open.blend")
    D = _os.path.join(tmp, "D_far.blend")
    ST_BLENDS.update({"dir": tmp, "A": A, "B": B, "C": C, "D": D})
    print("== audit 文件级算子自检（C1/C2）；API 来自 %s ==" % ST_API_SRC)
    st_check("api_present", ST_API is not None, "拿不到 K.dsh_audit_api，也无法从 runtime/audit.py 加载")

    # ---- 合成四个 .blend（会话里留一份与 B 相同位置的参照件，供 ⑤ 会话侧对照）----
    st_clean_scene()
    cube = st_cube(ST_CTX, (0.0, 0.0, 0.0))
    st_save(A)
    cube.location = (0.5, 0.0, 0.0)
    _bpy.context.view_layer.update()
    st_save(B)
    _bpy.context.scene.collection.objects.unlink(cube)      # 别把参照件也存进 C/D
    open_cube = st_cube(ST_CTX, (0.5, 0.0, 0.0), drop_face=True)
    st_save(C)
    _bpy.data.objects.remove(open_cube, do_unlink=True)
    far_cube = st_cube(ST_CTX, (2.0, 0.0, 0.0))
    st_save(D)
    _bpy.data.objects.remove(far_cube, do_unlink=True)
    _bpy.context.scene.collection.objects.link(cube)        # 参照件回场景（@0.5，与 B.blend 同）
    _bpy.context.view_layer.update()
    st_check("blends_created", all(_os.path.getsize(p) > 0 for p in (A, B, C, D)),
             {k: _os.path.getsize(v) for k, v in (("A", A), ("B", B), ("C", C), ("D", D))})

    # ---- 基线（第六项清理证明用）----
    base = st_counts()
    ST_MEAS["counts_baseline"] = base

    ENV_OK = [0.0, -0.5, -0.5, 1.0, 0.5, 0.5]
    ENV_BAD = [-0.1, -0.5, -0.5, 0.9, 0.5, 0.5]
    ref_conn = st_call("connectivity", {"objects": [ST_CTX]})
    ref_meas = st_call("measure", {"objects": [ST_CTX]})
    ref_gate_ok = st_call("gate", {"objects": [ST_CTX], "envelope": ENV_OK})
    ref_gate_bad = st_call("gate", {"objects": [ST_CTX], "envelope": ENV_BAD})

    # ================= ① 面-对重叠（A@0 × B@+0.5） =================
    ov = st_call("overlap", {"file_a": A, "file_b": B, "limit": 50})
    ib = ov.get("intersection_bbox") or {}
    lo, hi = ib.get("min"), ib.get("max")
    ST_MEAS["overlap_ab"] = {"ok": ov.get("ok"), "analyzed": ov.get("analyzed"), "ms": ov.get("ms"),
                             "method": ov.get("method"), "pair_count": ov.get("pair_count"),
                             "intersection_bbox": ib, "intersection_bbox_mm": ov.get("intersection_bbox_mm"),
                             "overlap_faces_bbox": ov.get("overlap_faces_bbox"),
                             "max_segment_len_mm": ov.get("max_segment_len_mm"),
                             "per_object": ov.get("per_object"), "tris_a": ov.get("tris_a"),
                             "tris_b": ov.get("tris_b"),
                             "pairs_head": (ov.get("pairs") or [])[:2],
                             "sides_names": [ov.get("sides", {}).get("a", {}).get("object_names"),
                                             ov.get("sides", {}).get("b", {}).get("object_names")],
                             "renamed": [ov.get("sides", {}).get("a", {}).get("file_load", {}).get("renamed"),
                                         ov.get("sides", {}).get("b", {}).get("file_load", {}).get("renamed")],
                             "cleanup": ov.get("cleanup")}
    st_check("①overlap_ok", ov.get("ok") is True and ov.get("analyzed") is True, ov.get("error"))
    st_check("①overlap_method_string", ov.get("method") == "bvh-overlap(三角面精确)", ov.get("method"))
    st_check("①pair_count_gt0", int(ov.get("pair_count") or 0) > 0, ov.get("pair_count"))
    # 任务书给的口径是"落在 [0,0.5]×[0,1]² 内"——那是按"立方体占 [0,1]³"想的；本自检按
    # create_cube(size=1.0) 的**居中**立方体构建（[-0.5,0.5]³，与 audit_gate_selftest 同口径），
    # 所以交集体是 [0,0.5]×[-0.5,0.5]²：x 与任务书一致，y/z 放宽到 [-0.5,0.5]（下面那条精确断言才是主判据）。
    st_check("①intersection_bbox_in_[0,0.5]x[-0.5,0.5]^2",
             bool(lo and hi and len(lo) == 3 and len(hi) == 3
                  and float(lo[0]) >= -1e-9 and float(hi[0]) <= 0.5 + 1e-9
                  and all(float(lo[k]) >= -0.5 - 1e-9 and float(hi[k]) <= 0.5 + 1e-9 for k in (1, 2))), ib)
    st_check("①intersection_bbox_matches_analytic",
             bool(lo and hi and abs(float(lo[0])) <= 1e-9 and abs(float(hi[0]) - 0.5) <= 1e-9
                  and all(abs(float(lo[k]) + 0.5) <= 1e-9 and abs(float(hi[k]) - 0.5) <= 1e-9 for k in (1, 2))),
             "解析解 x∈[0,0.5]、y,z∈[-0.5,0.5]；实测 min=%s max=%s" % (lo, hi))
    st_check("①overlap_faces_bbox_covers_true_intersection",
             bool((ov.get("overlap_faces_bbox") or {}).get("min")
                  and float(ov["overlap_faces_bbox"]["min"][0]) <= 1e-9
                  and float(ov["overlap_faces_bbox"]["max"][0]) >= 0.5 - 1e-9),
             ov.get("overlap_faces_bbox"))
    st_check("①segment_len_positive", float(ov.get("max_segment_len_mm") or 0) > 0,
             "max_segment_len_mm=%s（交线段真长度，0 说明平面求交/裁剪没走通）" % ov.get("max_segment_len_mm"))
    st_check("①per_object_nonempty",
             bool((ov.get("per_object") or {}).get("a")) and bool((ov.get("per_object") or {}).get("b")),
             ov.get("per_object"))
    st_check("①names_are_file_names",
             ov.get("sides", {}).get("a", {}).get("object_names") == [ST_CTX]
             and ov.get("sides", {}).get("b", {}).get("object_names") == [ST_CTX],
             ST_MEAS["overlap_ab"]["sides_names"])
    st_check("①renamed_mapping_exercised",
             any((ov.get("sides", {}).get(t, {}).get("file_load") or {}).get("renamed") for t in ("a", "b")),
             "会话里已有同名对象 → 加载时被 Blender 改名，必须靠 name_of 映射回原名：%s"
             % ST_MEAS["overlap_ab"]["renamed"])
    st_check("①side_source_is_file",
             ov.get("sides", {}).get("a", {}).get("source") == "file:" + A
             and ov.get("sides", {}).get("b", {}).get("source") == "file:" + B,
             [ov.get("sides", {}).get(t, {}).get("source") for t in ("a", "b")])
    cl_a = (ov.get("cleanup") or {}).get("a") or {}
    cl_b = (ov.get("cleanup") or {}).get("b") or {}
    ST_MEAS["per_side_cleanup"] = {"a": {k: cl_a.get(k) for k in ("ok", "removed", "failed")},
                                   "b": {k: cl_b.get(k) for k in ("ok", "removed", "failed")}}
    # 同一次 op 里加载了 A、B 两个文件：每个 cleanup 只能删自己的东西（第一轮自检里这条正是翻车点：
    # A 的 cleanup 把 B 的对象一起删了）。最后跑的那个（b）跑完，会话必须回到全局基线。
    ra = (cl_a.get("removed") or {}).get("objects") or []
    rb = (cl_b.get("removed") or {}).get("objects") or []
    ca = (cl_a.get("removed") or {}).get("collections") or []
    cb = (cl_b.get("removed") or {}).get("collections") or []
    la = (cl_a.get("removed") or {}).get("libraries") or []
    lb = (cl_b.get("removed") or {}).get("libraries") or []
    st_check("①per_side_cleanup_isolated",
             bool(cl_a.get("ok")) and bool(cl_b.get("ok")) and not cl_a.get("failed") and not cl_b.get("failed")
             and len(ra) == 1 and len(rb) == 1 and ra != rb
             and len(ca) == 1 and len(cb) == 1 and ca != cb
             and len(la) == 1 and len(lb) == 1 and la != lb
             and all(cl_b.get("counts_after", {}).get(k) == base[k]
                     for k in ("objects", "collections", "libraries", "meshes")),
             {"a_removed": cl_a.get("removed"), "b_removed": cl_b.get("removed"),
              "a_failed": cl_a.get("failed"), "b_failed": cl_b.get("failed"),
              "b_after": cl_b.get("counts_after"), "baseline": base})

    # ================= ② 交集体积（解析解 0.5 单位³） =================
    it = st_call("interference", {"file_a": A, "file_b": B, "samples": 200000, "seed": 0})
    mm_per_unit = float((it.get("units") or {}).get("mm_per_unit") or 0.0)
    analytic_mm3 = 0.5 * (mm_per_unit ** 3)
    vol = it.get("volume_mm3")
    ci95 = float(it.get("volume_ci95_mm3") or 0.0)
    vol_f = float(vol) if vol is not None else None
    rel = (abs(vol_f - analytic_mm3) / analytic_mm3) if (vol_f is not None and analytic_mm3) else None
    rel_ci = it.get("relative_ci95")
    ST_MEAS["interference_ab"] = {"ok": it.get("ok"), "method": it.get("method"), "ms": it.get("ms"),
                                  "verdict": it.get("verdict"), "why": it.get("why"),
                                  "volume_mm3": vol, "analytic_mm3": analytic_mm3,
                                  "rel_err": rel, "volume_ci95_mm3": it.get("volume_ci95_mm3"),
                                  "relative_ci95": rel_ci,
                                  "ci95_lower_mm3": it.get("ci95_lower_mm3"),
                                  "ci95_upper_mm3": it.get("ci95_upper_mm3"),
                                  "upper_bound_mm3": it.get("upper_bound_mm3"),
                                  "frac": it.get("frac"), "box_volume_mm3": it.get("box_volume_mm3"),
                                  "bbox_mm": it.get("bbox_mm"), "bbox_source": it.get("bbox_source"),
                                  "samples_used": it.get("samples_used"),
                                  "in_overlap_bbox": it.get("in_overlap_bbox"),
                                  "inside_a": it.get("inside_a"), "inside_b": it.get("inside_b"),
                                  "inside_both": it.get("inside_both"), "pair_count": it.get("pair_count"),
                                  "error": it.get("error"), "traceback": it.get("traceback"),
                                  "error_caliber": it.get("error_caliber")}
    st_check("②interference_ok", it.get("ok") is True and it.get("analyzed") is True, it.get("error"))
    st_check("②method_string", it.get("method") == "monte-carlo(ray-parity, 只在交叠 bbox 内采样)",
             it.get("method"))
    st_check("②verdict_supported", it.get("verdict") == "supported", it.get("why"))
    st_check("②samples_used_200k", int(it.get("samples_used") or 0) == 200000, it.get("samples_used"))
    st_check("②volume_rel_err_lt_5pct", rel is not None and rel < 0.05,
             "实测 %.1f mm³ vs 解析 %.1f mm³ → rel=%.5f" % (vol_f or -1, analytic_mm3, rel if rel is not None else -1))
    st_check("②measured_within_3ci95",
             bool(vol_f is not None and ci95 > 0 and abs(vol_f - analytic_mm3) <= 3.0 * ci95),
             "|Δ|=%.1f mm³，3×ci95=%.1f mm³（估计量不能有系统偏差）"
             % (abs((vol_f or 0) - analytic_mm3), 3.0 * ci95))
    bmm = it.get("bbox_mm") or {}
    st_check("②bbox_mm_present_and_mm_sized",
             bool(bmm.get("min") and bmm.get("max") and bmm.get("size"))
             and all(abs(float(bmm["size"][k]) - v) <= 1e-3 for k, v in enumerate((1500.0, 1000.0, 1000.0)))
             and all(abs(float(bmm["min"][k]) - v) <= 1e-3 for k, v in enumerate((-500.0, -500.0, -500.0))),
             {"bbox_mm": bmm, "bbox_source": it.get("bbox_source"),
              "解析采样盒": "交叠面对 bbox = [-0.5,1]×[-0.5,0.5]² → mm [-500,-500,-500]..[1000,500,500]"})
    st_check("②frac_box_consistent",
             bool(vol_f is not None and it.get("frac") is not None and it.get("box_volume_mm3")
                  and abs(vol_f - float(it["frac"]) * float(it["box_volume_mm3"]))
                  <= 1e-3 * max(1.0, vol_f)),
             "volume=%.1f, frac=%.6f, box=%.1f mm³" % (vol_f or -1, float(it.get("frac") or -1),
                                                       float(it.get("box_volume_mm3") or -1)))

    # ================= ③ 不相交（D@+2.0）→ 真结论 refuted =================
    ov_far = st_call("overlap", {"file_a": A, "file_b": D})
    far = st_call("interference", {"file_a": A, "file_b": D, "samples": 200000, "seed": 0})
    ST_MEAS["far"] = {"overlap_ok": ov_far.get("ok"), "pair_count": ov_far.get("pair_count"),
                      "intersection_bbox": ov_far.get("intersection_bbox"),
                      "note": ov_far.get("note"),
                      "verdict": far.get("verdict"), "why": far.get("why"),
                      "volume_mm3": far.get("volume_mm3"),
                      "upper_bound_mm3": far.get("upper_bound_mm3"),
                      "upper_bound_kind": far.get("upper_bound_kind"),
                      "bbox_mm": far.get("bbox_mm"), "samples_used": far.get("samples_used")}
    st_check("③overlap_ok_pair_count_0",
             ov_far.get("ok") is True and ov_far.get("pair_count") is not None
             and int(ov_far["pair_count"]) == 0,
             {"ok": ov_far.get("ok"), "pair_count": ov_far.get("pair_count"), "error": ov_far.get("error")})
    st_check("③intersection_bbox_null", ov_far.get("intersection_bbox") is None, ov_far.get("intersection_bbox"))
    st_check("③verdict_refuted", far.get("verdict") == "refuted", far.get("why"))
    st_check("③volume_below_upper_bound",
             float(far.get("volume_mm3") if far.get("volume_mm3") is not None else -1.0)
             < float(far.get("upper_bound_mm3") or -1.0),
             "volume=%s < upper=%s（kind=%s）" % (far.get("volume_mm3"), far.get("upper_bound_mm3"),
                                                  far.get("upper_bound_kind")))
    st_check("③volume_is_zero",
             far.get("volume_mm3") is not None and float(far["volume_mm3"]) == 0.0, far.get("volume_mm3"))

    # ================= ④ 开放网格（C：删一个面）→ unresolved，不给数 =================
    op_it = st_call("interference", {"file_a": A, "file_b": C, "samples": 200000, "seed": 0})
    mh = op_it.get("mesh_health") or {}
    hb = (mh.get("b") or [{}])[0]
    ST_MEAS["open"] = {"verdict": op_it.get("verdict"), "why": op_it.get("why"),
                       "volume_mm3": op_it.get("volume_mm3"), "samples_used": op_it.get("samples_used"),
                       "pair_count": op_it.get("pair_count"), "mesh_health": mh,
                       "next_step": op_it.get("next_step")}
    st_check("④open_mesh_detected",
             mh.get("closed_b") is False and int(hb.get("boundary_edges") if hb.get("boundary_edges") is not None else -1) == 4
             and int(hb.get("nonmanifold_edges") if hb.get("nonmanifold_edges") is not None else -1) == 4,
             {"closed_b": mh.get("closed_b"), "b0": hb, "closed_a": mh.get("closed_a"),
              "口径": "boundary_edges=只有 1 个相邻面的边；nonmanifold_edges=not is_manifold（含边界边）→ "
                      "删一个面后 4 条边同时进这两个计数（与 audit_mesh 同口径）"})
    st_check("④verdict_unresolved", op_it.get("verdict") == "unresolved", op_it.get("why"))
    st_check("④no_fake_volume",
             op_it.get("volume_mm3") is None and not int(op_it.get("samples_used") or 0),
             {"volume_mm3": op_it.get("volume_mm3"), "samples_used": op_it.get("samples_used")})
    st_check("④still_reports_pairs",
             op_it.get("pair_count") is not None and int(op_it["pair_count"]) > 0, op_it.get("pair_count"))

    # ================= ⑤ file= 三条 op 与会话侧逐项一致 =================
    f_conn = st_call("connectivity", {"file": B})
    f_meas = st_call("measure", {"file": B})
    f_gate_ok = st_call("gate", {"file": B, "envelope": ENV_OK})
    f_gate_bad = st_call("gate", {"file": B, "envelope": ENV_BAD})
    ST_MEAS["file_vs_session"] = {
        "conn": {"file": {"objects": f_conn.get("objects"), "tris": f_conn.get("tris"),
                          "aabb": f_conn.get("aabb"), "names": f_conn.get("object_names"),
                          "source": f_conn.get("source")},
                 "session": {"objects": ref_conn.get("objects"), "tris": ref_conn.get("tris"),
                             "aabb": ref_conn.get("aabb"), "names": ref_conn.get("object_names")}},
        "measure": {"file": {"count": f_meas.get("count"), "items": (f_meas.get("items") or [{}])[0],
                             "names": f_meas.get("object_names")},
                    "session": {"count": ref_meas.get("count"),
                                "items": (ref_meas.get("items") or [{}])[0],
                                "names": ref_meas.get("object_names")}},
        "gate": {"file_ok_env": (f_gate_ok.get("verdicts", {}).get("envelope")),
                 "session_ok_env": (ref_gate_ok.get("verdicts", {}).get("envelope")),
                 "file_bad_env": (f_gate_bad.get("verdicts", {}).get("envelope")),
                 "session_bad_env": (ref_gate_bad.get("verdicts", {}).get("envelope"))},
        "cleanup": f_conn.get("cleanup")}
    st_check("⑤connectivity_objects_tris_aabb_equal",
             ref_conn.get("objects") == f_conn.get("objects") and ref_conn.get("tris") == f_conn.get("tris")
             and st_boxes_equal(ref_conn.get("aabb"), f_conn.get("aabb"))
             and f_conn.get("object_names") == [ST_CTX],
             {"ref": [ref_conn.get("objects"), ref_conn.get("tris"), ref_conn.get("aabb")],
              "file": [f_conn.get("objects"), f_conn.get("tris"), f_conn.get("aabb"),
                       f_conn.get("object_names")], "error": f_conn.get("error")})
    st_check("⑤measure_count_tris_bbox_equal",
             ref_meas.get("count") == f_meas.get("count")
             and (ref_meas.get("items") or [{}])[0].get("tris") == (f_meas.get("items") or [{}])[0].get("tris")
             and st_boxes_equal((ref_meas.get("items") or [{}])[0], (f_meas.get("items") or [{}])[0])
             and f_meas.get("object_names") == [ST_CTX],
             {"ref": (ref_meas.get("items") or [{}])[0], "file": (f_meas.get("items") or [{}])[0],
              "names": f_meas.get("object_names"), "error": f_meas.get("error")})
    st_check("⑤gate_objects_tris_aabb_equal",
             ref_gate_ok.get("objects") == f_gate_ok.get("objects")
             and ref_gate_ok.get("tris") == f_gate_ok.get("tris")
             and st_boxes_equal(ref_gate_ok.get("aabb"), f_gate_ok.get("aabb"))
             and ref_gate_ok.get("verdicts", {}).get("envelope", {}).get("ok") is True
             and f_gate_ok.get("verdicts", {}).get("envelope", {}).get("ok") is True,
             {"ref": [ref_gate_ok.get("objects"), ref_gate_ok.get("tris"), ref_gate_ok.get("aabb"),
                      ref_gate_ok.get("verdicts", {}).get("envelope")],
              "file": [f_gate_ok.get("objects"), f_gate_ok.get("tris"), f_gate_ok.get("aabb"),
                       f_gate_ok.get("verdicts", {}).get("envelope")], "error": f_gate_ok.get("error")})
    st_check("⑤gate_envelope_violation_equal",
             ref_gate_bad.get("verdicts", {}).get("envelope", {}).get("violations") == 1
             and f_gate_bad.get("verdicts", {}).get("envelope", {}).get("violations") == 1
             and ref_gate_bad.get("verdicts", {}).get("envelope", {}).get("objects")
             == f_gate_bad.get("verdicts", {}).get("envelope", {}).get("objects"),
             {"ref": ref_gate_bad.get("verdicts", {}).get("envelope"),
              "file": f_gate_bad.get("verdicts", {}).get("envelope")})
    st_check("⑤source_file_and_names",
             f_conn.get("source") == "file:" + B and f_meas.get("source") == "file:" + B
             and f_gate_ok.get("source") == "file:" + B
             and f_meas.get("object_names") == [ST_CTX] and f_conn.get("object_names") == [ST_CTX],
             {"conn": f_conn.get("source"), "meas": f_meas.get("source"), "gate": f_gate_ok.get("source")})
    st_check("⑤cleanup_reported_and_restores_counts",
             isinstance(f_conn.get("cleanup"), dict) and bool(f_conn["cleanup"].get("removed", {}).get("objects"))
             and all(f_conn["cleanup"]["counts_before"].get(k) == f_conn["cleanup"]["counts_after"].get(k)
                     for k in ("objects", "collections", "libraries", "meshes"))
             and isinstance(f_meas.get("cleanup"), dict) and isinstance(f_gate_ok.get("cleanup"), dict),
             {"removed": f_conn.get("cleanup", {}).get("removed"), "failed": f_conn.get("cleanup", {}).get("failed")})
    st_check("⑤file_names_filter_missing_reports",
             (st_call("connectivity", {"file": B, "objects": ["NoSuchObj"]}) or {}).get("ok") is False,
             "file= + 不存在的名字 → 必须 ok:false（不许静默空产出）")

    # ================= ⑥ 清理证明（跑完前后三个计数逐项相等） =================
    after = st_counts()
    ST_MEAS["counts_after"] = after
    ST_MEAS["counts_equal"] = {k: (after[k] == base[k]) for k in base}
    print("  清理证明：objects %d→%d, collections %d→%d, libraries %d→%d（meshes %d→%d, materials %d→%d, "
          "images %d→%d）" % (base["objects"], after["objects"], base["collections"], after["collections"],
                             base["libraries"], after["libraries"], base["meshes"], after["meshes"],
                             base["materials"], after["materials"], base["images"], after["images"]))
    st_check("⑥cleanup_objects_collections_libraries_equal",
             all(after[k] == base[k] for k in ("objects", "collections", "libraries")),
             {"baseline": base, "after": after})
    st_check("⑥cleanup_strict_all_id_kinds_equal", all(after[k] == base[k] for k in base),
             {"baseline": base, "after": after})

    # ================= 附：B1/B2 API 形态（可调用 dict；dispatch 仍返回 str） =================
    st_check("api_is_dict", isinstance(ST_API, dict),
             {"src": ST_API_SRC, "type": type(ST_API).__name__})
    if ST_API_SRC == "K.dsh_audit_api":
        st_check("api_is_callable", callable(ST_API), "v0.9.1：dict 子类可调用 api(op, args) → dict")
        h = ST_API("help")
        st_check("api_call_returns_dict_and_version",
                 isinstance(h, dict) and h.get("version") == 3,
                 {"version": h.get("version"), "AUDIT_VERSION": globals().get("AUDIT_VERSION")})
        st_check("api_default_help", isinstance(ST_API(), dict) and "ops" in ST_API(), "api() 无参 → help")
        st_check("api_call_method", isinstance(ST_API.call("help"), dict), "api.call(op, args)")
        d = ST_API["dispatch"]("help", "{}")
        st_check("dispatch_still_returns_str", isinstance(d, str) and isinstance(_json.loads(d), dict),
                 type(d).__name__)
    else:
        st_note("api_callable_clauses", "API 来自兜底 exec（非 K.dsh_audit_api，preload 没生效）→ 跳过形态断言")
    st_check("new_ops_in_help",
             all(k in (ST_API("help").get("ops") or {}) for k in ("overlap", "interference")),
             sorted((ST_API("help").get("ops") or {}).keys()))

    # ================= 附：失败路径（文件不存在 / 空产出） =================
    nope = _os.path.join(tmp, "no_such_file.blend")
    ov_nope = st_call("overlap", {"file_a": nope, "file_b": B})
    cn_nope = st_call("connectivity", {"file": nope})
    ST_MEAS["failure_paths"] = {"overlap_missing_file": {"ok": ov_nope.get("ok"),
                                                         "error": ov_nope.get("error")},
                                "connectivity_missing_file": {"ok": cn_nope.get("ok"),
                                                              "error": cn_nope.get("error"),
                                                              "cleanup": cn_nope.get("cleanup")}}
    st_check("neg_overlap_missing_file_ok_false", ov_nope.get("ok") is False and ov_nope.get("error"),
             ov_nope)
    st_check("neg_connectivity_missing_file_ok_false",
             cn_nope.get("ok") is False and "不存在" in str(cn_nope.get("error")), cn_nope)
    st_check("neg_missing_file_cleanup_reported", isinstance(cn_nope.get("cleanup"), dict),
             cn_nope.get("cleanup"))


def st_emit(extra=None):
    res = {"ok": not ST_FAILS, "passed": len(ST_PASS), "failed": len(ST_FAILS), "failures": ST_FAILS,
           "skipped": ST_SKIP, "passed_clauses": ST_PASS, "meas": ST_MEAS, "blends": ST_BLENDS,
           "api_source": ST_API_SRC, "audit_version": globals().get("AUDIT_VERSION"),
           "expect": "①pair_count>0 且 intersection_bbox=解析解 [0,0.5]×[-0.5,0.5]²；②volume≈5×10⁸ mm³（rel<5%"
                     " 且 |Δ|≤3×ci95、method=monte-carlo(ray-parity…)、bbox_mm 齐）；③不相交 → pair_count=0 + "
                     "refuted + volume(0)<upper_bound；④开放网格 → unresolved 且 volume=null（不给假数）；"
                     "⑤file= 三条 op 与会话侧 objects/tris/bbox 逐项相等且名字=文件原名；⑥跑完 objects/"
                     "collections/libraries 与基线逐项相等（严格版连 meshes/materials/images 也相等）"}
    if extra:
        res.update(extra)
    print("HEADLESS " + _json.dumps(res, ensure_ascii=False, default=str))


def st_run():
    """跑自检并回一行 HEADLESS JSON（不依赖 __name__：preload/exec/--python 三种入口都跑）。"""
    if globals().get("_ST_RAN"):
        return
    globals()["_ST_RAN"] = True
    try:
        st_main()
    except Exception as _e:
        import traceback as _tb
        ST_FAILS.append({"clause": "selftest_crashed",
                         "detail": "%s: %s" % (type(_e).__name__, str(_e)[:300])})
        st_emit({"traceback": _tb.format_exc()[-1500:]})
        return
    if not ST_FAILS:
        _shutil.rmtree(ST_BLENDS.get("dir") or "", ignore_errors=True)
        st_emit({"tmp_removed": True})
    else:
        st_emit({"tmp_kept_for_debug": ST_BLENDS.get("dir")})


st_run()
