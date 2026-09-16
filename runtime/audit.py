# -*- coding: utf-8 -*-
"""DSH 网格体检（v0.8.10 / C1）+ 同名资源审计（D7）—— 把"脚本能判的对错"变成一等公民。

为什么有它（外部反馈 00-G-1 / 93 §4）：airframe-smith 自己写了 af_geom.validate()，**抓到翼盒 13 处自交、
前缘襟翼 39 处零面积面、P03 非流形 56 边、雷达罩环点数 60 vs 78 不一致**；没有它会带进权威场景、P4 才返工。
sys-smith / qc-smith 各自也造了一遍。这里收敛成通用能力。

API 挂 K.dsh_audit_api；入口：
    blender_rt_plan(op="audit_mesh",  args={objects:["P01_Fuselage"], envelope:[min,max]})
    blender_rt_plan(op="audit_scene", args={envelope:[min,max]})
    blender_rt_plan(op="audit_duplicates")
    blender_rt_plan(op="audit_purge_orphans")      # users==0 的灯/相机/网格/材质/图像
无头里也可 preload="audit" 后直接 K.dsh_audit_api["mesh"]({...})。

**硬规则**：对象集合里 0 个 mesh ⇒ ok=false（外部反馈的"静默空产出"事故就在这一条上）。
"""
import json

import bpy
import bmesh
from mathutils import Vector

AUDIT_VERSION = 1


def _j(o):
    return json.dumps(o, ensure_ascii=False, default=str)


def _kernel():
    import sys
    return sys.modules.get("dsh_rt_kernel")


def _tris(me):
    n = 0
    for p in me.polygons:
        n += max(0, len(p.vertices) - 2)
    return n


def _bounds(objs):
    mn = Vector((1e30, 1e30, 1e30))
    mx = Vector((-1e30, -1e30, -1e30))
    n = 0
    for ob in objs:
        mw = ob.matrix_world
        for c in ob.bound_box:
            p = mw @ Vector((c[0], c[1], c[2]))
            for i in range(3):
                mn[i] = min(mn[i], p[i])
                mx[i] = max(mx[i], p[i])
        n += 1
    if n == 0:
        return None, None
    return mn, mx


def _pick(objects=None, scope=None):
    """挑对象：objects=[名字] 优先；否则 scope（集合名）里的；再否则全场景可见 mesh。

    容错（v0.8.10）：把 {"objects": [...]} 这样的包装字典直接传进来也能用 —— 别让参数作用域坑再咬人。
    """
    if isinstance(objects, dict):
        _w = objects
        objects = _w.get("objects") or _w.get("names") or _w.get("list")
        if scope is None:
            scope = _w.get("scope")
    if objects:
        want = [str(x) for x in (objects if isinstance(objects, (list, tuple)) else [objects])]
        got, missing = [], []
        for n in want:
            ob = bpy.data.objects.get(n)
            if ob is None:
                missing.append(n)
            else:
                got.append(ob)
        return got, missing, None
    coll = None
    if scope:
        coll = bpy.data.collections.get(str(scope))
        if coll is None:
            return [], [], "集合不存在：%s" % scope
        objs = [o for o in coll.all_objects if o.type == "MESH"]
    else:
        objs = [o for o in bpy.context.scene.objects if o.type == "MESH"]
    return objs, [], None


def audit_mesh(objects=None, scope=None, envelope=None, eps_area=1e-9, self_intersect=True,
               max_issues=20, min_dist=1e-6):
    """单对象/一组对象的网格体检。envelope=[x0,y0,z0,x1,y1,z1] 时给 out_of_bounds。"""
    objs, missing, err = _pick(objects, scope)
    if err:
        return _j({"ok": False, "error": err})
    objs = [o for o in objs if o.type == "MESH"]
    if not objs:
        # ★ 硬规则：0 个 mesh 必须失败（外部反馈的"静默空产出"）
        return _j({"ok": False, "error": "体检对象里 0 个 mesh", "objects": objects, "scope": scope,
                   "hint": "多半是 build 空跑了：确认对象名/集合名，或先跑 audit_scene 看场景里有什么",
                   "missing": missing})
    env = None
    if envelope:
        try:
            e = [float(x) for x in envelope]
            env = (Vector((min(e[0], e[3]), min(e[1], e[4]), min(e[2], e[5]))),
                   Vector((max(e[0], e[3]), max(e[1], e[4]), max(e[2], e[5]))))
        except Exception:
            env = None

    parts = []
    total = {"objects": 0, "tris": 0, "boundary_edges": 0, "nonmanifold_edges": 0, "degenerate_faces": 0,
             "loose_verts": 0, "self_intersections": 0, "normals_inverted": 0, "out_of_bounds": 0}
    for ob in objs:
        me = ob.data
        info = {"name": ob.name, "verts": len(me.vertices), "polys": len(me.polygons), "tris": _tris(me),
                "loose_edges": int(sum(1 for e in me.edges if not e.link_faces)) if hasattr(me.edges[0], "link_faces") else None}
        bm = bmesh.new()
        try:
            bm.from_mesh(me)
            bm.verts.ensure_lookup_table()
            bm.faces.ensure_lookup_table()
            info["boundary_edges"] = int(sum(1 for e in bm.edges if len(e.link_faces) == 1))
            info["nonmanifold_edges"] = int(sum(1 for e in bm.edges if not e.is_manifold))
            info["degenerate_faces"] = int(sum(1 for f in bm.faces if f.calc_area() <= float(eps_area)))
            info["loose_verts"] = int(sum(1 for v in bm.verts if not v.link_edges))
            try:
                vol = float(bm.calc_volume(signed=True))
            except Exception:
                vol = None
            info["signed_volume"] = (round(vol, 6) if vol is not None else None)
            info["closed"] = bool(info["boundary_edges"] == 0 and info["nonmanifold_edges"] == 0)
            info["normals_outward"] = (None if vol is None or not info["closed"] else bool(vol > 0))
            info["self_intersections"] = 0
            info["self_intersection_pairs"] = []
            if self_intersect and len(bm.faces) > 0:
                try:
                    from mathutils.bvhtree import BVHTree
                    bvh = BVHTree.FromBMesh(bm)
                    seen = set()
                    hit = []
                    for a, b in (bvh.overlap(bvh) or []):
                        if a >= b:
                            continue
                        key = (a, b)
                        if key in seen:
                            continue
                        seen.add(key)
                        fa, fb = bm.faces[a], bm.faces[b]
                        va = set(v.index for v in fa.verts)
                        vb = set(v.index for v in fb.verts)
                        if va & vb:
                            continue                     # 相邻面共享顶点 → 不算自交
                        if (fa.calc_center_median() - fb.calc_center_median()).length < float(min_dist):
                            continue                     # 重合面/薄壳抖动 → 由 degenerate 报
                        hit.append([a, b, [round(x, 4) for x in fa.calc_center_median()]])
                    info["self_intersections"] = len(hit)
                    info["self_intersection_pairs"] = hit[:int(max_issues)]
                except Exception as e:
                    info["self_intersect_error"] = "%s: %s" % (type(e).__name__, str(e)[:100])
        finally:
            bm.free()
        # 世界 AABB / 越界
        mn, mx = None, None
        for c in ob.bound_box:
            p = ob.matrix_world @ Vector((c[0], c[1], c[2]))
            mn = p.copy() if mn is None else Vector((min(mn[i], p[i]) for i in range(3)))
            mx = p.copy() if mx is None else Vector((max(mx[i], p[i]) for i in range(3)))
        info["aabb"] = {"min": [round(float(x), 4) for x in mn], "max": [round(float(x), 4) for x in mx]}
        info["dimensions"] = [round(float(x), 4) for x in ob.dimensions]
        if env:
            out = [i for i in range(3) if mn[i] < env[0][i] - 1e-6 or mx[i] > env[1][i] + 1e-6]
            info["out_of_bounds"] = bool(out)
            info["out_of_bounds_axes"] = out
            if out:
                total["out_of_bounds"] += 1
        # 汇总
        total["objects"] += 1
        total["tris"] += info["tris"]
        for k in ("boundary_edges", "nonmanifold_edges", "degenerate_faces", "loose_verts", "self_intersections"):
            total[k] += int(info.get(k) or 0)
        if info.get("normals_outward") is False:
            total["normals_inverted"] += 1
        parts.append(info)

    bad = (total["boundary_edges"] or total["nonmanifold_edges"] or total["degenerate_faces"]
           or total["self_intersections"] or total["normals_inverted"] or total["out_of_bounds"])
    mn, mx = _bounds(objs)
    return _j({"ok": True, "clean": (not bad), "count": len(objs), "missing": missing,
               "totals": total, "objects": parts,
               "aabb": ({"min": [round(float(x), 4) for x in mn], "max": [round(float(x), 4) for x in mx]} if mn else None),
               "envelope": ([[round(float(x), 4) for x in env[0]], [round(float(x), 4) for x in env[1]]] if env else None),
               "note": "判据：boundary/nonmanifold/degenerate/self_intersections/normals_inverted/out_of_bounds "
                       "任一非 0 → clean=false；normals_outward 只对闭合网格有意义（closed=true 时才给）"})


def audit_scene(envelope=None, limit=40, eps_area=1e-9):
    """全场景体检：先给你"哪几个对象最脏"，再按需 audit_mesh 深挖。"""
    objs = [o for o in bpy.context.scene.objects if o.type == "MESH"]
    if not objs:
        return _j({"ok": False, "error": "场景里 0 个 mesh（build 空跑？）"})
    rows = []
    r = json.loads(audit_mesh(objects=[o.name for o in objs], envelope=envelope, eps_area=eps_area,
                              self_intersect=False, max_issues=0))
    tot = r.get("totals", {})
    for info in r.get("objects", []):
        score = (info.get("boundary_edges") or 0) + 3 * (info.get("nonmanifold_edges") or 0) + \
                2 * (info.get("degenerate_faces") or 0)
        rows.append({"name": info["name"], "tris": info["tris"], "boundary_edges": info.get("boundary_edges"),
                     "nonmanifold_edges": info.get("nonmanifold_edges"),
                     "degenerate_faces": info.get("degenerate_faces"), "closed": info.get("closed"),
                     "normals_outward": info.get("normals_outward"), "score": score,
                     "aabb": info.get("aabb")})
    rows.sort(key=lambda x: -x["score"])
    return _j({"ok": True, "count": len(objs), "totals": tot, "worst": rows[:int(limit)],
               "clean": bool(r.get("clean")), "aabb": r.get("aabb"),
               "note": "worst 按 score=boundary+3*nonmanifold+2*degenerate 排序；深挖用 audit_mesh(objects=[...])"})


def _mat_sig(ob):
    """材质签名：节点类型 + 关键输入默认值（粗指纹，用于发现"同名分叉"）。"""
    try:
        nt = ob.node_tree
        if nt is None:
            return "none"
        items = []
        for nd in sorted(nt.nodes, key=lambda n: n.name):
            key = str(nd.type) + ":" + str(getattr(nd, "blend_type", "")) + ":" + str(getattr(nd, "operation", ""))
            vals = []
            for inp in getattr(nd, "inputs", []) or []:
                dv = getattr(inp, "default_value", None)
                try:
                    if hasattr(dv, "__len__"):
                        vals.append(",".join("%.4f" % float(x) for x in dv))
                    else:
                        vals.append("%.4f" % float(dv))
                except Exception:
                    vals.append("-")
            items.append(key + "[" + "|".join(vals[:8]) + "]")
        return "|".join(items)[:600]
    except Exception:
        return "err"


def audit_duplicates(limit=30):
    """同名资源审计（D7）：把 Foo / Foo.001 这类分叉分组，并比较内容是否不同。"""
    import hashlib
    out = {"ok": True, "materials": [], "meshes": [],
           "note": "同名分叉多为「先到先得」覆盖造成；若内容不同，说明有两份不一样的资源在混用"}
    # 材质
    groups = {}
    for m in bpy.data.materials:
        base = str(m.name).split(".")[0]
        groups.setdefault(("m", base), []).append(m)
    for (kind, base), items in sorted(groups.items()):
        if len(items) < 2:
            continue
        sigs = {}
        for m in items:
            sigs.setdefault(_mat_sig(m), []).append(m.name)
        out["materials"].append({"base": base, "variants": [m.name for m in items], "distinct_signatures": len(sigs),
                                 "users": [int(m.users) for m in items]})
    # 网格
    g2 = {}
    for me in bpy.data.meshes:
        base = str(me.name).split(".")[0]
        g2.setdefault(base, []).append(me)
    for base, items in sorted(g2.items()):
        if len(items) < 2:
            continue
        sigs = {}
        for me in items:
            h = hashlib.md5()
            h.update(("%d/%d" % (len(me.vertices), len(me.polygons))).encode())
            for v in me.vertices[:2000]:
                h.update(("%.5f,%.5f,%.5f;" % (v.co[0], v.co[1], v.co[2])).encode())
            sigs.setdefault(h.hexdigest()[:12], []).append(me.name)
        out["meshes"].append({"base": base, "variants": [m.name for m in items], "distinct_signatures": len(sigs),
                              "users": [int(m.users) for m in items]})
    out["count"] = {"materials": len(out["materials"]), "meshes": len(out["meshes"])}
    out["ok"] = True
    return _j(out)


def purge_orphans():
    """清理 users==0 的 datablock（D1 配套：孤儿灯/相机/网格/材质/图像）。"""
    removed = {}
    targets = [("lights", bpy.data.lights), ("cameras", bpy.data.cameras), ("meshes", bpy.data.meshes),
               ("materials", bpy.data.materials), ("images", bpy.data.images), ("node_groups", bpy.data.node_groups),
               ("collections", bpy.data.collections)]
    for name, coll in targets:
        n = 0
        for db in list(coll):
            try:
                if int(db.users) == 0:
                    coll.remove(db, do_unlink=True)
                    n += 1
            except Exception:
                pass
        if n:
            removed[name] = n
    return _j({"ok": True, "removed": removed, "total": sum(removed.values()),
               "left": {k: len(getattr(bpy.data, k)) for k, _ in targets}})


def audit_selftest():
    """合成自检：造一个"已知脏"的网格，看各项计数是否如实。"""
    me = bpy.data.meshes.new("DSH_AUDIT_SELFTEST")
    bm = bmesh.new()
    bmesh.ops.create_grid(bm, x_segments=2, y_segments=2, size=1.0)     # 开放网格 → 有边界边
    bmesh.ops.create_cube(bm, size=0.5)                                  # 闭合立方体
    bm.to_mesh(me)
    bm.free()
    ob = bpy.data.objects.new("DSH_AUDIT_SELFTEST", me)
    bpy.context.scene.collection.objects.link(ob)
    try:
        r = json.loads(audit_mesh(objects=[ob.name], self_intersect=False))
        empty = json.loads(audit_mesh(objects=["NoSuchObject"]))
        return _j({"ok": True, "boundary_edges": r["totals"]["boundary_edges"],
                   "tris": r["totals"]["tris"], "clean": r["clean"],
                   "empty_must_fail": (empty.get("ok") is False),
                   "expect": "boundary_edges>0（开放网格）且 clean=false；audit_mesh(不存在的名字) 必须 ok=false"})
    finally:
        try:
            bpy.data.objects.remove(ob, do_unlink=True)
            bpy.data.meshes.remove(me, do_unlink=True)
        except Exception:
            pass


def audit_help():
    return _j({"version": AUDIT_VERSION,
               "ops": {"mesh": "audit_mesh(objects|scope, envelope, eps_area, self_intersect)",
                       "scene": "audit_scene(envelope, limit)",
                       "duplicates": "audit_duplicates()",
                       "purge_orphans": "purge_orphans()",
                       "selftest": "audit_selftest()"},
               "fields": "boundary_edges / nonmanifold_edges / degenerate_faces / loose_verts / "
                         "self_intersections / normals_outward / closed / tris / aabb / out_of_bounds",
               "hard_rule": "0 个 mesh ⇒ ok=false（静默空产出必须失败）"})


def audit_dispatch(op, args=None):
    if isinstance(args, str):
        try:
            args = json.loads(args) if args.strip() else {}
        except Exception:
            args = {}
    ops = {"mesh": audit_mesh, "scene": audit_scene, "duplicates": audit_duplicates,
           "purge_orphans": purge_orphans, "selftest": audit_selftest, "help": audit_help}
    fn = ops.get(str(op))
    if fn is None:
        return _j({"ok": False, "error": "unknown audit op", "op": op, "ops": sorted(ops)})
    try:
        return fn(**dict(args or {}))
    except TypeError as e:
        return _j({"ok": False, "error": "参数不匹配: %s" % str(e)[:200], "op": op, "help": audit_help()})


import sys as _sys
_K = _sys.modules.get("dsh_rt_kernel")
if _K is not None:
    _K.dsh_audit_api = {"version": AUDIT_VERSION, "dispatch": audit_dispatch, "mesh": audit_mesh,
                        "scene": audit_scene, "duplicates": audit_duplicates, "purge_orphans": purge_orphans,
                        "selftest": audit_selftest, "help": audit_help}
