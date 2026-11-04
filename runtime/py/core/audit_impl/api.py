# -*- coding: utf-8 -*-
"""Split from audit.py; behavior preserved by the facade."""
from .common import *  # noqa: F401,F403
from .mesh import audit_duplicates, audit_mesh, audit_scene, audit_selftest, purge_orphans
from .connectivity import audit_connectivity, audit_gate
from .drift import audit_drift
from .measure import audit_measure, audit_snap_floaters
from .overlap import audit_interference, audit_overlap

def audit_gate_selftest():
    """装配级判据的合成自检：可见浮块 / 微隙容忍 / 归因 / 漂移 四项一起验。"""
    made = []
    try:
        def cube(name, loc, size):
            me = bpy.data.meshes.new(name)
            bm = bmesh.new()
            bmesh.ops.create_cube(bm, size=float(size))
            bm.to_mesh(me)
            bm.free()
            ob = bpy.data.objects.new(name, me)
            ob.location = tuple(loc)
            bpy.context.scene.collection.objects.link(ob)
            made.append(ob)
            return ob
        A = cube("DSH_GATE_A", (0, 0, 0), 1.0)
        B = cube("DSH_GATE_B", (3.0, 0, 0), 0.4)        # 远端浮块（span 远大于 1%）
        C = cube("DSH_GATE_C", (-1.0002, 0, 0), 1.0)    # A 的另一侧：与 A 的 x=-0.5 面相差 0.0002 m = 0.2 mm（真微隙）
        D = cube("DSH_GATE_D", (6.0, 0, 0), 1.0)        # 形状改动版：非均匀缩放（各自归一化消除不掉）
        D.scale = (1.0, 1.0, 1.45)
        import math as _math
        F = cube("DSH_GATE_F", (0.6, 0.6, 0.0), 0.3)    # 绕 Z 转 45°：bbox 与 A 重叠 → bbox 判 micro，
        F.rotation_euler = (0.0, 0.0, _math.radians(45.0))   # 但面到面约 150 mm —— "bbox 骗人"用例
        try:
            bpy.context.view_layer.update()
        except Exception:
            pass
        r0 = json.loads(audit_connectivity(objects=["DSH_GATE_A", "DSH_GATE_B", "DSH_GATE_C", "DSH_GATE_F"]))
        r1 = json.loads(audit_drift("DSH_GATE_A", "DSH_GATE_A"))
        r2 = json.loads(audit_drift("DSH_GATE_A", "DSH_GATE_C"))
        r3 = json.loads(audit_drift("DSH_GATE_A", "DSH_GATE_D"))
        r4f = json.loads(audit_drift("DSH_GATE_A", "DSH_GATE_D", samples=3000, fast=True))
        r4g = json.loads(audit_drift("DSH_GATE_A", "DSH_GATE_D", samples=3000, fast=False))
        agree = float(r4g.get("chamfer", 0)) >= float(r4f.get("chamfer", 0)) - 1e-9

        def floater_at(pt):
            for f in r0.get("floaters", []):
                if all(f["bbox_min"][k] - 1e-6 <= pt[k] <= f["bbox_max"][k] + 1e-6 for k in range(3)):
                    return f
            return {}
        f_b, f_f = floater_at((3.0, 0, 0)), floater_at((0.6, 0.6, 0.0))
        tol_names = {tuple(sorted(t.get("objects") or [])) for t in r0.get("tolerated", [])}
        clauses = {
            "r0_ok": r0.get("ok") is True,
            "r0_analyzed": r0.get("analyzed") is True,
            "floater_count_2": r0.get("floater_count") == 2,
            "visible_2": r0.get("visible_floater_count") == 2,
            "B_floating_far": bool(f_b) and f_b.get("gap_mm", 0) > 1000,
            "F_bbox_micro": bool(f_f) and f_f.get("bbox_micro") is True,
            "F_not_confirmed": bool(f_f) and f_f.get("mesh_confirmed") is False,
            "A_tolerated": ("DSH_GATE_A",) in tol_names,
            "C_tolerated": ("DSH_GATE_C",) in tol_names,
            "micro_tolerated_2": r0.get("micro_floater_count") == 2,
            "gate_fail": r0.get("gate", {}).get("ok") is False,
            "drift_self_small": r1.get("ok") is True and r1.get("chamfer", 1) <= 0.005,
            "movement_reported": r2.get("ok") is True and r2.get("bbox_delta", {}).get("moved_mm", 0) > 0.1,
            "drift_reshaped_big": r3.get("ok") is True and r3.get("chamfer", 0) > 0.03,
            "nn_grid_ge_bvh": bool(agree),
        }
        bad = [k for k, v in clauses.items() if not v]
        ok = not bad
        return _j({"ok": bool(ok), "failed_clauses": bad,
                   "floaters_slim": [{"rank": f["rank"], "bbox_min": f["bbox_min"], "bbox_max": f["bbox_max"],
                                      "bbox_micro": f["bbox_micro"], "mesh_confirmed": f["mesh_confirmed"],
                                      "gap_mm": f["gap_mm"], "true_gap_units": f["true_gap_units"],
                                      "attribution": (f.get("attribution") or {}).get("object")}
                                     for f in r0.get("floaters", [])],
                   "tolerated_slim": [{"rank": t["rank"], "objects": t["objects"], "gap_mm": t["gap_mm"],
                                       "mesh_confirmed": t["mesh_confirmed"],
                                       "true_gap_units": t.get("true_gap_units")}
                                      for t in r0.get("tolerated", [])],
                   "floater_count": r0.get("floater_count"),
                   "visible": r0.get("visible_floater_count"), "micro": r0.get("micro_floater_count"),
                   "real": r0.get("real_floater_count"), "gate": r0.get("gate", {}).get("state"),
                   "micro_confirm": r0.get("micro_confirm"),
                   "F_bbox_micro": f_f.get("bbox_micro"), "F_mesh_confirmed": f_f.get("mesh_confirmed"),
                   "F_confirm_note": f_f.get("confirm_note"),
                   "connectivity": r0 if not ok else "ok",
                   "drift_self": r1.get("chamfer"), "drift_shifted_chamfer": r2.get("chamfer"),
                   "drift_shifted_moved_mm": r2.get("bbox_delta", {}).get("moved_mm"),
                   "drift_reshaped": r3.get("chamfer"), "drift_reshaped_band": r3.get("band"),
                   "nn_bvh": r4f.get("chamfer"), "nn_grid": r4g.get("chamfer"), "nn_grid_ge_bvh": bool(agree),
                   "expect": "floater_count=2（B 远端 + F）；A/C 各与对方相接（0.2mm 缝 & 互穿）→ 进 tolerated 不算浮块；"
                             "F bbox_micro=true 但 mesh_confirmed=false → 判浮块（bbox 骗人必须被揭穿，实测最近 1.32mm）；"
                             "micro_floater_count=2、gate=fail；同网格 chamfer≈0（≤0.005）；平移不进 chamfer 但 "
                             "bbox_delta.moved_mm 报中心距；非均匀缩放 chamfer>0.03（band=strong）；grid 口径 ≥ BVH 口径"})
    except Exception as e:
        import traceback
        return _j({"ok": False, "error": "%s: %s" % (type(e).__name__, str(e)[:200]),
                   "traceback": traceback.format_exc()[-800:]})
    finally:
        for ob in made:
            try:
                bpy.data.objects.remove(ob, do_unlink=True)
            except Exception:
                pass


def audit_help():
    return _j({"version": AUDIT_VERSION,
               "ops": {"mesh": "audit_mesh(objects|scope, envelope, eps_area, self_intersect)",
                       "scene": "audit_scene(envelope, limit)",
                       "duplicates": "audit_duplicates()",
                       "purge_orphans": "purge_orphans()",
                       "connectivity": "audit_connectivity(objects|scope|file, visible_frac, micro_gap_mm, limit, max_tris)"
                                       " → 连通分量 / 浮块 / 微隙 / 归因",
                       "gate": "audit_gate(objects|scope|file, envelope, micro_gap_mm, max_floater_span_fraction)"
                               " → 出厂门（连通 + 包络 + 未分析三态）",
                       "drift": "audit_drift(a, b, samples, seed) → 对称 Chamfer 距离（两条网格改了多远）",
                       "measure": "audit_measure(objects|scope|file, neighbors, k) → 世界 bbox + 逐轴间隙/重叠 mm/%",
                       "snap_floaters": "audit_snap_floaters(objects|scope, dry_run=true) → 把可见浮块贴到最近邻",
                       "overlap": "audit_overlap(a|objects_a, b|objects_b, file_a, file_b, limit, max_tris)"
                                  " → 面-对重叠（BVH 三角面精确）：pair_count / pairs / intersection_bbox / per_object",
                       "interference": "audit_interference(a|objects_a, b|objects_b, file_a, file_b, samples, seed, "
                                       "material_mm3, max_tris, rays) → A∩B 体积估计 mm³（Monte-Carlo 射线奇偶）"
                                       " + 三态 verdict",
                       "selftest": "audit_selftest()",
                       "gate_selftest": "audit_gate_selftest() → 装配级判据合成自检"},
               "file_mode": "v0.9.1：connectivity / gate / measure 都接受 file=<.blend 路径> —— "
                            "把文件里的对象**临时**追加进会话跑同一条链路，finally 删净（对象/临时集合/"
                            "新造孤儿数据/库条目），返回体给 source / file_load / cleanup；此时 objects/scope "
                            "当**文件内的对象名**筛选（append 不带集合成员关系）。"
                            "overlap / interference 两端都可以走 file_a/file_b（a/b 直接给 .blend 路径也认）。",
               "fields": "boundary_edges / nonmanifold_edges / degenerate_faces / loose_verts / loose_edges / "
                         "self_intersections / normals_outward / closed / tris / aabb / out_of_bounds；"
                         "mesh 侧三态：state(=verdict) / reason / analysis{self_intersections,normals,incomplete_objects} / "
                         "normals_state{pass|fail|unknown|n/a} / normals_shells[{shell,signed_volume,closed,"
                         "winding_consistent,nested,relation,state}] / self_intersections_analyzed / "
                         "self_intersections_state{pass|fail|skipped|error}；"
                         "装配侧：components / floaters[{span_fraction, gap_mm, bbox_micro, mesh_confirmed, true_gap_units, attribution}] / "
                         "tolerated[]（确认相接）/ unconfirmed[]（未确认→降级）/ gap_histogram / gate{state,offenders}；"
                         "跨件侧：pair_count / pairs[{tri_a,tri_b,point,segment_len_mm}] / intersection_bbox / "
                         "overlap_faces_bbox / per_object / volume_mm3 / volume_ci95_mm3 / verdict{supported|refuted|unresolved} / why",
               "units": "mm 口径阈值一律经场景 scale_length 换算（返回 units 块）；体积另给 volume_units3 与 "
                        "volume_mm3（units.volume_units3_to_mm3）",
               "honest_limits": "overlap：overlap() 是**真三角-三角相交**，但**共面且互相覆盖的面不报**（实测）→ "
                                "共面贴合看 contact_probe；interference：Monte-Carlo **不是精确布尔**，"
                                "非流形/开放网格一律 unresolved（不给数）；误差 ∝1/√samples（口径见 error_caliber）",
               "hard_rule": "0 个 mesh ⇒ ok=false（静默空产出必须失败）；大网格 ⇒ analyzed=false，"
                            "未分析不得读作已连通/无重叠；file= 打不开/没 mesh/全隐藏 ⇒ ok=false"})


def audit_dispatch(op, args=None):
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
    ops = {"mesh": audit_mesh, "scene": audit_scene, "duplicates": audit_duplicates,
           "purge_orphans": purge_orphans, "selftest": audit_selftest, "help": audit_help,
           "connectivity": audit_connectivity, "gate": audit_gate, "drift": audit_drift,
           "measure": audit_measure, "snap_floaters": audit_snap_floaters,
           "overlap": audit_overlap, "interference": audit_interference,
           "gate_selftest": audit_gate_selftest}
    fn = ops.get(str(op))
    if fn is None:
        return _j({"ok": False, "error": "unknown audit op", "op": op, "ops": sorted(ops)})
    try:
        return fn(**kw)
    except TypeError as e:
        msg = str(e)[:200]
        out = {"ok": False, "error": "参数不匹配: %s" % msg, "op": op, "help": audit_help(),
               "given": sorted(kw)}
        # 定向提示（2026-09-30 实测反馈）：file= 只被一部分 op 接受，泛化的"参数不匹配"
        # 会让 agent 反复试错（QBZ-191 项目里 4 个 agent 独立踩同一个坑）。
        bad = [k for k in ("file", "file_a", "file_b")
               if k in kw and ("unexpected keyword argument '%s'" % k) in msg]
        if bad:
            takes_file = ["connectivity", "gate", "measure", "overlap", "interference"]
            out["error_hint"] = (
                "`audit_%s` 不吃 %s —— 整个 audit 家族里只有 %s 接受 file="
                "（overlap / interference 用 file_a / file_b）。"
                "要对**另一个 .blend** 里的件跑单件自证，只有这一条路："
                "先 bpy.ops.wm.open_mainfile(filepath=PATH) 真的打开它，"
                "再 audit_mesh(objects=[...])（headless 里 preload=\"audit\" 后 "
                "K.dsh_audit_api(\"mesh\", {...}) 同口径）。"
                % (op, "/".join(bad), " / ".join("audit_" + t for t in takes_file)))
            out["file_arg_ops"] = takes_file
        return _j(out)


_DshApi = _KIT.Api  # 共享内核（尾部注册行无需改）
import sys as _sys
_K = _sys.modules.get("dsh_rt_kernel")
if _K is not None:
    _K.dsh_audit_api = _DshApi({"version": AUDIT_VERSION, "dispatch": audit_dispatch, "mesh": audit_mesh,
                                "scene": audit_scene, "duplicates": audit_duplicates,
                                "purge_orphans": purge_orphans,
                                "connectivity": audit_connectivity, "gate": audit_gate, "drift": audit_drift,
                                "measure": audit_measure, "snap_floaters": audit_snap_floaters,
                                "overlap": audit_overlap, "interference": audit_interference,
                                "selftest": audit_selftest, "gate_selftest": audit_gate_selftest,
                                "help": audit_help})
