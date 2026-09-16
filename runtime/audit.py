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

AUDIT_VERSION = 2


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


# ============================================================================
# v0.9.0（Procedura 融合 · 批次 1）：装配级判据 —— 连通分量 / 微隙 / 归因 / 漂移 / 测量包
# ----------------------------------------------------------------------------
# 来源：SpatiaOS/Procedura（MIT）：src/mesh/connectivity.ts、src/mesh/floater-attribution.ts、
#       src/mesh/chamfer.ts、src/tools/module_context.ts。这里把「跨 OpenSCAD 模块的 STL 判据」
#       改写成「跨 bpy 对象的装配判据」，阈值与三态纪律原样保留：
#         * 可见浮块：最长 bbox 边 ≥ 全模型最长边 1%（VISIBLE_SPAN_FRACTION）—— 严重度只看它，不看体积
#         * 微隙：贴而未重合（默认 0.3 mm）—— 报告但永不阻塞门（否则几十条微缝会让门永远红）
#         * 大网格：超上限一律 analyzed=false；**未分析绝不能读成已连通**
#         * 单位：所有 mm 口径阈值按场景 scale_length 换算，返回里带 units 块
# ============================================================================

VISIBLE_SPAN_FRACTION = 0.01
MICRO_GAP_MM = 0.3
CONN_MAX_TRIS = 4000000          # numpy 连通路径的实际上限；env DSH_CONN_MAX_TRIS 可覆盖


def _conn_max_tris():
    import os
    try:
        v = int(os.environ.get("DSH_CONN_MAX_TRIS", "") or 0)
        return v if v > 0 else CONN_MAX_TRIS
    except Exception:
        return CONN_MAX_TRIS


def _units():
    """场景单位换算 → (米/单位, 毫米/单位)。mm 口径的阈值一律经它换算，绝不硬套。"""
    m = 1.0
    try:
        m = float(bpy.context.scene.unit_settings.scale_length) or 1.0
    except Exception:
        m = 1.0
    return m, m * 1000.0


def _r(x, n=5):
    try:
        return round(float(x), n)
    except Exception:
        return x


def _objs_for(objects, scope, include_hidden=False):
    """_pick 的包装：再按 hide_render/hide_viewport 过滤（与契约层 _scope_objects 同口径）。"""
    if isinstance(objects, dict):
        w = objects
        include_hidden = bool(w.get("include_hidden", include_hidden))
        objects = w.get("objects") or w.get("names") or w.get("list")
        if scope is None:
            scope = w.get("scope")
    got, missing, err = _pick(objects, scope)
    out = got if include_hidden else [o for o in got if not (o.hide_render or o.hide_viewport)]
    return out, missing, err


def _world_tris(objs):
    """求值后的三角面 → 世界坐标。返回 (tris(N,3,3) float64, owner(N,) int32, boxes{名:(lo,hi)|None})。"""
    import numpy as np
    dg = bpy.context.evaluated_depsgraph_get()
    tris_all, owner_all, boxes = [], [], {}
    for k, ob in enumerate(objs):
        boxes.setdefault(ob.name, None)
        obe = None
        try:
            obe = ob.evaluated_get(dg)
            me = obe.to_mesh()
            if me is None:
                continue
            me.calc_loop_triangles()
            n = len(me.loop_triangles)
            if n == 0:
                obe.to_mesh_clear()
                continue
            v = np.empty(len(me.vertices) * 3, dtype=np.float64)
            me.vertices.foreach_get("co", v)
            v = v.reshape(-1, 3)
            idx = np.empty(n * 3, dtype=np.int32)
            me.loop_triangles.foreach_get("vertices", idx)
            mw = np.array(obe.matrix_world, dtype=np.float64)
            t = v[idx.reshape(-1, 3)] @ mw[:3, :3].T + mw[:3, 3]
            tris_all.append(t)
            owner_all.append(np.full(n, k, dtype=np.int32))
            p = t.reshape(-1, 3)
            boxes[ob.name] = (p.min(axis=0), p.max(axis=0))
            obe.to_mesh_clear()
        except Exception as e:
            boxes[ob.name] = None
            try:
                if obe is not None:
                    obe.to_mesh_clear()
            except Exception:
                pass
    if not tris_all:
        return None, None, boxes
    return np.concatenate(tris_all), np.concatenate(owner_all), boxes


def _components(tris, precision=5):
    """按共享顶点（四舍五入去重）做连通分量 → (comp_of_tri, 顶点数)。"""
    import numpy as np
    flat = tris.reshape(-1, 3)
    uniq, inv = np.unique(np.round(flat, int(precision)), axis=0, return_inverse=True)
    f = inv.reshape(-1, 3).astype(np.int64)
    nv = int(uniq.shape[0])
    e = np.concatenate([f[:, [0, 1]], f[:, [1, 2]], f[:, [2, 0]]], axis=0)
    e.sort(axis=1)
    e = np.unique(e, axis=0)
    parent = np.arange(nv, dtype=np.int64)
    a, b = e[:, 0], e[:, 1]
    for _ in range(64):
        before = parent.copy()
        np.minimum.at(parent, b, parent[a])
        np.minimum.at(parent, a, parent[b])
        parent = parent[parent]
        if np.array_equal(parent, before):
            break
    _u, comp = np.unique(parent[f[:, 0]], return_inverse=True)
    return comp.reshape(-1).astype(np.int64), nv


def _confirm_micro_with_mesh(tris, comp, cid, radius_units, tree_cache=None, max_pts=400, big_factor=50.0):
    """把「bbox 判 micro」的分量拿到网格上复核：真最近距离到底有没有小于阈值。

    为什么必须复核（本插件与上游的关键差异）：上游是**单网格合并体**，分量之间的 bbox 几乎不重叠，
    bbox 间隙是有信息量的；而我们这里每个部件是独立对象，密集装配里 bbox 普遍互相重叠 → bbox 间隙恒为 0
    → 全被判 micro → 门一律放行（fail-open）。所以多对象场景必须补一次三角级确认：
      对分量采样 ≤max_pts 个点，用全局 BVH 的 find_nearest_range(半径=阈值) 找命中；
      命中三角形属于**别的分量** → 确认贴上了（真 micro）；半径内一无所获 → 其实是浮块（bbox 骗人）。
    返回 (confirmed_micro, true_gap_units|None, note)；true_gap 只在"更大半径里找到了别的分量"时给值。
    """
    import numpy as np
    from mathutils.bvhtree import BVHTree
    sel = np.nonzero(comp == int(cid))[0]
    if len(sel) == 0:
        return False, None, "空分量"
    # 采样必须落在**面上**（面积加权），不能只取顶点：大平面之间的 0.2 mm 缝，顶点可能相距几米
    uniq = _sample(tris[sel], int(max_pts), np.random.default_rng(0))
    if len(uniq) > int(max_pts):
        step = max(1, len(uniq) // int(max_pts))
        uniq = uniq[::step][:int(max_pts)]
    if tree_cache is not None and tree_cache.get("tree") is None:
        V = tris.reshape(-1, 3).tolist()
        polys = [(3 * i, 3 * i + 1, 3 * i + 2) for i in range(len(tris))]
        tree_cache["tree"] = BVHTree.FromPolygons(V, polys, all_triangles=True)
    tree = (tree_cache or {}).get("tree")
    if tree is None:
        return None, None, "BVH 未建立"
    r = float(radius_units)
    r_big = r * float(big_factor)
    hit_other, best = False, None
    for p in uniq:
        pt = (float(p[0]), float(p[1]), float(p[2]))
        for (loc, nor, idx, dist) in tree.find_nearest_range(pt, r):
            if int(comp[int(idx)]) != int(cid):
                hit_other = True
                break
        if hit_other:
            break
        for (loc, nor, idx, dist) in tree.find_nearest_range(pt, r_big):   # 顺手量一下到底漂多远
            if int(comp[int(idx)]) != int(cid):
                d = float(dist)
                if best is None or d < best:
                    best = d
    if hit_other:
        return True, best, "网格确认：阈值半径内有其它分量的面（真贴上）"
    return False, best, "网格复核：阈值半径内没有其它分量的面 → bbox 判的 micro 不成立%s" % (
        "" if best is None else "（更大半径内最近 %.6f 单位）" % best)


def _snap_pair_mesh(tris, comp, cid, tree_cache=None, max_pts=400):
    """网格级最近点对：返回 (delta 向量[指向最近邻], 距离, note)。

    bbox 级间隙在多对象场景会读成 0（所以 snap 必须能走网格级），这也是"贴而未重合"唯一可靠的收口依据。
    """
    import numpy as np
    from mathutils.bvhtree import BVHTree
    sel = np.nonzero(comp == int(cid))[0]
    if len(sel) == 0:
        return None, None, "空分量"
    pts = _sample(tris[sel], int(max_pts), np.random.default_rng(0))   # 面上采样（见 _confirm_micro_with_mesh）
    if len(pts) > int(max_pts):
        pts = pts[::max(1, len(pts) // int(max_pts))][:int(max_pts)]
    if tree_cache is not None and tree_cache.get("tree") is None:
        V = tris.reshape(-1, 3).tolist()
        polys = [(3 * i, 3 * i + 1, 3 * i + 2) for i in range(len(tris))]
        tree_cache["tree"] = BVHTree.FromPolygons(V, polys, all_triangles=True)
    tree = (tree_cache or {}).get("tree")
    if tree is None:
        return None, None, "BVH 未建立"
    best = None
    for p in pts:
        pp = (float(p[0]), float(p[1]), float(p[2]))
        hit = tree.find_nearest(pp)
        if hit is None or hit[0] is None:
            continue
        loc, nor, idx, dist = hit
        if int(comp[int(idx)]) == int(cid):
            continue
        if best is None or float(dist) < best[0]:
            best = (float(dist), (float(loc[0]), float(loc[1]), float(loc[2])), pp)
    if best is None:
        return None, None, "网格近邻里没有别的分量（孤立件）"
    return [best[1][k] - best[2][k] for k in range(3)], best[0], "网格级最近点对（点到面）"


def _ov(a, b):
    """两个 bbox 的重叠体积（a/b 都是 (lo, hi)）。"""
    v = 1.0
    for k in range(3):
        d = min(float(a[1][k]), float(b[1][k])) - max(float(a[0][k]), float(b[0][k]))
        if d <= 0:
            return 0.0
        v *= d
    return v


def _boxvol(b):
    return max(0.0, float(b[1][0]) - float(b[0][0])) * max(0.0, float(b[1][1]) - float(b[0][1])) \
        * max(0.0, float(b[1][2]) - float(b[0][2]))


def _conn_analyze(objs, precision=5, visible_frac=VISIBLE_SPAN_FRACTION, micro_gap_mm=MICRO_GAP_MM,
                  max_tris=None, limit=24):
    """连通分量分析内核：返回 dict（含 numpy 细节，供 snap 复用；公开 op 只暴露格式化结果）。"""
    import numpy as np
    m_per_unit, mm_per_unit = _units()
    cap = int(max_tris or _conn_max_tris())
    tris, owner, boxes = _world_tris(objs)
    out = {"units": {"m_per_unit": _r(m_per_unit, 6), "mm_per_unit": _r(mm_per_unit, 6),
                     "scene_unit": "m" if abs(m_per_unit - 1.0) < 1e-12 else "scaled",
                     "micro_gap_mm": _r(micro_gap_mm, 4),
                     "micro_gap_units": _r(micro_gap_mm / mm_per_unit, 8)},
           "objects": len(objs), "object_names": [o.name for o in objs], "boxes": boxes,
           "gap_note": "gap 是 bbox 级 → 低估真实间隙（贴而未重合会读成 0），偏向判 micro 放行（与上游同口径）"}
    if tris is None or len(tris) == 0:
        return dict(out, analyzed=False, reason="范围内没有三角面（空产出必须失败）", tris=0)
    if len(tris) > cap:
        return dict(out, analyzed=False, tris=int(len(tris)),
                    reason="三角面 %d 超过上限 %d（env DSH_CONN_MAX_TRIS 可调）→ 跳过分析" % (len(tris), cap),
                    note="未分析 ≠ 已连通：门保持 degraded，不给通过结论")
    comp, nv = _components(tris, precision)
    ncomp = int(comp.max()) + 1
    stats = []
    for c in range(ncomp):
        sel = np.nonzero(comp == c)[0]
        t = tris[sel]
        p = t.reshape(-1, 3)
        lo, hi = p.min(axis=0), p.max(axis=0)
        size = hi - lo
        vol = abs(float(np.einsum("ij,ij->i", t[:, 0], np.cross(t[:, 1], t[:, 2])).sum() / 6.0))
        stats.append({"comp": c, "tris": int(len(sel)), "min": [_r(x) for x in lo], "max": [_r(x) for x in hi],
                      "size": [_r(x) for x in size], "max_dim": _r(size.max()), "volume": _r(vol, 8),
                      "owners": {objs[int(i)].name: int((owner[sel] == i).sum())
                                 for i in np.unique(owner[sel])}})
    all_lo = np.array([s["min"] for s in stats]).min(axis=0)
    all_hi = np.array([s["max"] for s in stats]).max(axis=0)
    model_span = float((all_hi - all_lo).max())
    for s in stats:
        s["span_fraction"] = _r(s["max_dim"] / model_span, 6) if model_span > 0 else 0.0
    stats.sort(key=lambda s: s["volume"], reverse=True)
    for rank, s in enumerate(stats):
        s["rank"] = rank
    # 邻域 bbox 间隙（mm）：每个分量到最近其它分量的距离
    gaps = []
    for i, s in enumerate(stats):
        best, bj = None, None
        for j, t2 in enumerate(stats):
            if i == j:
                continue
            d2 = 0.0
            for k in range(3):
                g = max(0.0, max(s["min"][k] - t2["max"][k], t2["min"][k] - s["max"][k]))
                d2 += g * g
            if best is None or d2 < best:
                best, bj = d2, j
        gaps.append({"gap_units": _r((best or 0.0) ** 0.5, 8), "gap_mm": _r(((best or 0.0) ** 0.5) * mm_per_unit, 5),
                     "nearest_rank": (stats[bj]["rank"] if bj is not None else None)})
    # 分类在下面完成：attached=False → 真浮块；True → 阈值内有邻居（容忍）；None → 未确认（降级）
    for s, g in zip(stats, gaps):
        s["gap_mm"] = g["gap_mm"]
        s["gap_units"] = g["gap_units"]
        s["bbox_micro"] = bool(g["gap_mm"] < float(micro_gap_mm))
        s["visible"] = bool(s["span_fraction"] >= float(visible_frac))
        s["attached"] = None
        s["true_gap_units"] = None
        s["mesh_confirmed"] = None
    # 判据（v0.9.0 修正）：不再用「最大体积分量=主体、其余都是浮块」—— 两个等大零件时那条规则会
    # 随便挑一个当浮体（自检抓到过）。物理上要问的是：**这个分量有没有跟别的分量相接**。
    micro_units = float(micro_gap_mm) / mm_per_unit
    mesh_mode = "mesh(BVH)" if len(tris) <= BVH_MAX_TRIS else "bbox-only"
    tree_cache = {}
    for s in stats:
        if not s["bbox_micro"]:
            s["attached"] = False
            s["evidence"] = "bbox"
            s["confirm_note"] = "bbox 间隙 %.4f mm 已超阈值 %.2f mm → 漂着（无需网格复核）" % (s["gap_mm"], micro_gap_mm)
            continue
        if mesh_mode == "bbox-only":
            s["attached"] = None
            s["evidence"] = "bbox-only"
            s["confirm_note"] = "面数 %d 超过 %d，跳过网格复核 → 不能当作已贴上（门按降级处理）" % (len(tris), BVH_MAX_TRIS)
            continue
        try:
            conf, true_gap, note = _confirm_micro_with_mesh(tris, comp, s["comp"], micro_units, tree_cache)
        except Exception as e:
            conf, true_gap, note = None, None, "网格复核失败：%s" % str(e)[:120]
        s["mesh_confirmed"] = conf
        s["true_gap_units"] = true_gap
        s["confirm_note"] = note
        s["evidence"] = "bbox+mesh"
        s["attached"] = conf
    floaters = [s for s in stats if s["attached"] is False]
    tolerated = [s for s in stats if s["attached"] is True]
    unconfirmed = [s for s in stats if s["attached"] is None]
    # 间隙分布：口径（micro_gap_mm）该给多少，取决于你要什么 —— 单一实体/3D 打印用 0.3mm（上游口径）；
    # 带设计间隙的装配件按自己的工艺给（1–2mm 很常见）。这里把分布摊开，让调用者自己选口径。
    def _gap_mm_of(s):
        if s.get("true_gap_units") is not None:
            return float(s["true_gap_units"]) * mm_per_unit
        return float(s.get("gap_mm") or 0.0)
    hist = {}
    for s in stats:
        g = _gap_mm_of(s)
        key = ("<%.2f mm(相接口径)" % micro_gap_mm) if g < micro_gap_mm else (
            "%.2f-1 mm" % micro_gap_mm if g < 1 else ("1-5 mm" if g < 5 else ("5-20 mm" if g < 20 else ">20 mm")))
        hist[key] = hist.get(key, 0) + 1
    fgaps = [_gap_mm_of(s) for s in floaters]
    gap_range = (round(min(fgaps), 4), round(max(fgaps), 4)) if fgaps else None
    for s in stats:      # 归因只对真浮块做（要"改哪个对象"）
        if s["attached"] is not False:
            continue
        # 归因：浮块 bbox 与每个对象 bbox 的重叠率（Procedura: matchFloatersToBoxes）
        fbox = (s["min"], s["max"])
        fvol = _boxvol(fbox) or 1.0
        scored = []
        for name, b in boxes.items():
            if b is None:
                continue
            frac = _ov(fbox, (list(b[0]), list(b[1]))) / fvol
            if frac > 0.01:
                scored.append((frac, name, _boxvol((list(b[0]), list(b[1])))))
        scored.sort(reverse=True)
        best = scored[0] if scored else None
        s["attribution"] = {
            "object": best[1] if best else None,
            "confidence": _r(min(1.0, best[0]) if best else 0.0, 4),
            "object_fraction": _r(min(1.0, fvol / best[2]) if best and best[2] > 0 else 0.0, 4),
            "also_overlaps": [n for f, n, _ in scored[1:] if f >= 0.2],
            "contains": s["owners"],
            "note": "object_fraction≈1 ⇒ 该浮块就是整个对象（可整件平移）；很小 ⇒ 只是焊接体的一块（平移会撕开焊点）",
        }
    visible_f = [s for s in floaters if s["visible"]]
    return dict(out, analyzed=True, tris=int(len(tris)), verts=int(nv), components=stats, floaters=floaters,
                tolerated=tolerated, unconfirmed=unconfirmed,
                _raw={"tris": tris, "comp": comp},     # 给 snap 复用（公开 op 的 JSON 里不会出现）
                obj_tris={objs[k].name: int((owner == k).sum()) for k in range(len(objs))},
                micro_confirm=mesh_mode,
                floater_count=len(floaters), visible_floater_count=len(visible_f),
                real_floater_count=len(visible_f),
                micro_floater_count=len([s for s in tolerated if s["visible"]]),
                unconfirmed_floater_count=len([s for s in unconfirmed if s["visible"]]),
                attached_count=len(tolerated),
                gap_histogram=hist, floater_gap_range_mm=gap_range,
                gate_caliber={"micro_gap_mm": _r(micro_gap_mm, 4),
                              "meaning": "『算作相接』的距离口径：0.3mm=单一实体/3D 打印（上游口径）；"
                                         "带设计间隙的装配件请按工艺给（如 1–2mm）",
                              "how_to_change": "audit_gate(args={micro_gap_mm: 2.0})"},
                max_floater_span_fraction=_r(max([s["span_fraction"] for s in floaters] or [0.0]), 6),
                model_span=_r(model_span), limit=int(limit))


def audit_connectivity(objects=None, scope=None, include_hidden=False, visible_frac=VISIBLE_SPAN_FRACTION,
                       micro_gap_mm=MICRO_GAP_MM, precision=5, limit=24, max_tris=None):
    """连通分量体检：这堆对象到底连成几块？哪几块是"看得见的浮块"？归因到哪个对象？"""
    objs, missing, err = _objs_for(objects, scope, include_hidden)
    if err:
        return _j({"ok": False, "error": err})
    if not objs:
        return _j({"ok": False, "error": "范围内 0 个 mesh", "missing": missing,
                   "hint": "确认对象名/集合名，或 include_hidden=True；静默空产出必须失败"})
    a = _conn_analyze(objs, precision=precision, visible_frac=visible_frac, micro_gap_mm=micro_gap_mm,
                      max_tris=max_tris, limit=limit)
    gate = _gate_from(a)
    out = {"ok": True, "analyzed": a["analyzed"], "units": a["units"], "objects": a["objects"],
           "tris": a.get("tris"), "missing": missing}
    if not a["analyzed"]:
        out.update({"reason": a.get("reason"), "note": a.get("note"), "gate": gate})
        return _j(out)
    out.update({
        "components": a["components"][:int(limit)],
        "floater_count": a["floater_count"],
        "visible_floater_count": a["visible_floater_count"],
        "real_floater_count": a["real_floater_count"],
        "micro_floater_count": a["micro_floater_count"],
        "unconfirmed_floater_count": a.get("unconfirmed_floater_count", 0),
        "micro_confirm": a.get("micro_confirm"),
        "max_floater_span_fraction": a["max_floater_span_fraction"],
        "model_span": a["model_span"],
        "gap_note": a.get("gap_note"),
        "gap_histogram": a.get("gap_histogram"),
        "floater_gap_range_mm": a.get("floater_gap_range_mm"),
        "gate_caliber": a.get("gate_caliber"),
        "attached_count": a.get("attached_count"),
        "floaters": [{"rank": s["rank"], "tris": s["tris"], "span_fraction": s["span_fraction"],
                      "gap_mm": s["gap_mm"], "visible": s["visible"],
                      "bbox_micro": s.get("bbox_micro"), "mesh_confirmed": s.get("mesh_confirmed"),
                      "true_gap_units": s.get("true_gap_units"), "evidence": s.get("evidence"),
                      "confirm_note": s.get("confirm_note"),
                      "bbox_min": s["min"], "bbox_max": s["max"], "size": s["size"],
                      "attribution": s["attribution"]} for s in a["floaters"][:int(limit)]],
        "tolerated": [{"rank": s["rank"], "span_fraction": s["span_fraction"], "gap_mm": s["gap_mm"],
                       "bbox_micro": s.get("bbox_micro"), "mesh_confirmed": s.get("mesh_confirmed"),
                       "true_gap_units": s.get("true_gap_units"),
                       "objects": sorted((s.get("owners") or {}).keys()),
                       "confirm_note": s.get("confirm_note")} for s in a.get("tolerated", [])[:int(limit)]],
        "unconfirmed": [{"rank": s["rank"], "span_fraction": s["span_fraction"],
                         "objects": sorted((s.get("owners") or {}).keys()),
                         "confirm_note": s.get("confirm_note")} for s in a.get("unconfirmed", [])[:int(limit)]],
        "gate": gate,
        "verdict_line": _verdict_line(a, visible_frac, micro_gap_mm),
    })
    return _j(out)


def _gate_from(a):
    """连通门：只看"可见且网格复核确认非贴合"的浮块。未分析/未确认 → ok=null（degraded）。"""
    if not a.get("analyzed"):
        return {"ok": None, "state": "degraded", "reason": a.get("reason") or "未分析",
                "note": "未分析不得读作已连通"}
    real = a.get("real_floater_count", 0)          # 可见且与任何分量都不相接
    micro = a.get("micro_floater_count", 0)        # 可见但已确认相接（贴而未重合/互穿）
    unconf = a.get("unconfirmed_floater_count", 0)
    if real == 0 and unconf > 0:
        return {"ok": None, "state": "degraded", "visible_floater_count": a.get("visible_floater_count"),
                "unconfirmed_floater_count": unconf, "micro_tolerated": micro,
                "reason": "%d 个可见分量无法确认是否相接（%s）→ 降级，不给通过结论" % (unconf, a.get("micro_confirm")),
                "note": "上游在单网格里可以 fail-open；多对象装配不行 —— 未确认就是未确认"}
    if real == 0:
        return {"ok": True, "state": "pass", "visible_floater_count": 0, "micro_tolerated": micro,
                "reason": "" if micro == 0 else "%d 个可见分量经网格确认与邻居相接（贴而未重合/互穿）→ 容忍" % micro}
    worst = [s for s in a.get("floaters", []) if s.get("visible")][:6]
    return {"ok": False, "state": "fail", "visible_floater_count": a.get("visible_floater_count"),
            "real_floater_count": real, "micro_tolerated": micro, "unconfirmed_floater_count": unconf,
            "offenders": [{"rank": s["rank"], "span_fraction": s["span_fraction"], "gap_mm": s["gap_mm"],
                           "true_gap_units": s.get("true_gap_units"), "evidence": s.get("evidence"),
                           "edit_hint": (s["attribution"] or {}).get("object")} for s in worst],
            "reason": "%d 个可见浮块：与任何其它分量都不相接（口径 %.2f mm 内找不到邻居%s）" % (
                real, MICRO_GAP_MM if not a.get("gate_caliber") else a["gate_caliber"].get("micro_gap_mm", MICRO_GAP_MM),
                ("；最近 %.2f–%.2f mm" % (a["floater_gap_range_mm"][0], a["floater_gap_range_mm"][1]))
                if a.get("floater_gap_range_mm") else "")}


def _verdict_line(a, visible_frac, micro_gap_mm):
    if not a.get("analyzed"):
        return "连通：未分析（%s）—— 不得当作已连通" % (a.get("reason") or "")
    real = a.get("real_floater_count", 0)
    micro = a.get("micro_floater_count", 0)
    unconf = a.get("unconfirmed_floater_count", 0)
    rng = a.get("floater_gap_range_mm")
    if real == 0 and unconf == 0:
        return "连通：通过（%d 个分量；%d 个可见分量经网格确认与邻居相接；阈值 可见 %.1f%%／相接 %.2f mm）" % (
            len(a["components"]), micro, visible_frac * 100, micro_gap_mm)
    if real == 0:
        return "连通：降级 —— %d 个分量无法确认相接（%s），不给通过结论" % (unconf, a.get("micro_confirm"))
    names = [((s["attribution"] or {}).get("object") or "UNMATCHED") for s in a["floaters"] if s["visible"]]
    extra = ("；与最近邻的距离 %.2f–%.2f mm —— 若这是设计间隙，把口径调大（micro_gap_mm）" % (rng[0], rng[1])) if rng else ""
    return "连通：不通过 —— %d 个可见浮块与任何分量都不相接%s，优先改 %s" % (real, extra, ", ".join(names[:6]))


def audit_gate(objects=None, scope=None, include_hidden=False, envelope=None, visible_frac=VISIBLE_SPAN_FRACTION,
               micro_gap_mm=MICRO_GAP_MM, max_floater_span_fraction=None, max_tris=None):
    """出厂门：连通 + （可选）包络 + 三态。任何"未分析"都降级为 degraded，绝不给通过。"""
    objs, missing, err = _objs_for(objects, scope, include_hidden)
    if err:
        return _j({"ok": False, "error": err})
    if not objs:
        return _j({"ok": False, "error": "范围内 0 个 mesh", "missing": missing})
    a = _conn_analyze(objs, visible_frac=visible_frac, micro_gap_mm=micro_gap_mm, max_tris=max_tris)
    conn = _gate_from(a)
    verdicts = {"connectivity": conn}
    ok = conn["ok"]
    if conn["ok"] is None:
        ok = None
    if max_floater_span_fraction is not None and a.get("analyzed"):
        lim = float(max_floater_span_fraction)
        got = float(a.get("max_floater_span_fraction") or 0.0)
        good = got <= lim
        verdicts["floater_span"] = {"ok": good, "max_floater_span_fraction": got, "limit": lim}
        if ok is True and not good:
            ok = False
    if envelope is not None and len(list(envelope)) == 6:
        lo = [float(x) for x in list(envelope)[:3]]
        hi = [float(x) for x in list(envelope)[3:]]
        bad = []
        for name, b in a["boxes"].items():
            if b is None:
                continue
            over = []
            for k in range(3):
                if float(b[0][k]) < lo[k] - 1e-9:
                    over.append(["%s_min" % "xyz"[k], _r(float(b[0][k]) - lo[k])])
                if float(b[1][k]) > hi[k] + 1e-9:
                    over.append(["%s_max" % "xyz"[k], _r(float(b[1][k]) - hi[k])])
            if over:
                bad.append({"name": name, "out_of_envelope": over})
        good = len(bad) == 0
        verdicts["envelope"] = {"ok": good, "violations": len(bad), "objects": bad[:20]}
        if ok is True and not good:
            ok = False
    return _j({"ok": True, "ship_ok": ok, "state": ("pass" if ok is True else ("degraded" if ok is None else "fail")),
               "verdicts": verdicts, "units": a["units"], "objects": len(objs),
               "tris": a.get("tris"), "analyzed": a.get("analyzed"),
               "verdict_line": _verdict_line(a, visible_frac, micro_gap_mm),
               "note": "ok=true 表示分析都跑完了（不是门通过）；门结论看 ship_ok/state"})


# ---------------------------------------------------------------- 漂移度量（Chamfer 距离）

def _read_obj(path):
    import numpy as np
    V, F = [], []
    with open(path, "r", encoding="utf-8", errors="ignore") as fh:
        for line in fh:
            if line.startswith("v "):
                p = line.split()
                V.append((float(p[1]), float(p[2]), float(p[3])))
            elif line.startswith("f "):
                idx = []
                for tok in line.split()[1:]:
                    t = tok.split("/")[0]
                    if t:
                        i = int(t)
                        idx.append(i - 1 if i > 0 else len(V) + i)
                for k in range(1, len(idx) - 1):
                    F.append((idx[0], idx[k], idx[k + 1]))
    return np.asarray(V, dtype=np.float64), np.asarray(F, dtype=np.int64)


def _read_stl(path):
    import numpy as np
    with open(path, "rb") as fh:
        head = fh.read(84)
        if len(head) < 84:
            raise ValueError("STL 太短：%s" % path)
        n = int(np.frombuffer(head[80:84], dtype="<u4")[0])
        raw = fh.read(50 * n)
        if n > 0 and len(raw) == 50 * n:
            rec = np.frombuffer(raw, dtype=np.uint8).reshape(n, 50)
            tri = rec[:, 12:48].copy().view("<f4").reshape(n, 3, 3).astype(np.float64)
            return tri.reshape(-1, 3), np.arange(n * 3, dtype=np.int64).reshape(n, 3)
    V, F = [], []
    with open(path, "r", encoding="utf-8", errors="ignore") as fh:
        cur = []
        for line in fh:
            t = line.strip().split()
            if len(t) == 4 and t[0].lower() == "vertex":
                cur.append((float(t[1]), float(t[2]), float(t[3])))
                if len(cur) == 3:
                    base = len(V)
                    V.extend(cur)
                    F.append((base, base + 1, base + 2))
                    cur = []
    import numpy as np
    return np.asarray(V, dtype=np.float64), np.asarray(F, dtype=np.int64)


def _src_tris(src):
    """对象名 / 集合名 / .obj / .stl 路径 → (tris, label)。"""
    import os
    s = str(src)
    if os.path.exists(s):
        ext = os.path.splitext(s)[1].lower()
        if ext == ".obj":
            V, F = _read_obj(s)
        elif ext == ".stl":
            V, F = _read_stl(s)
        else:
            raise ValueError("只支持 .obj/.stl 文件，或对象名/集合名：%s" % s)
        if len(F) == 0:
            raise ValueError("文件里没有面：%s" % s)
        return V[F], os.path.basename(s)
    ob = bpy.data.objects.get(s)
    if ob is not None and ob.type == "MESH":
        t, _o, _b = _world_tris([ob])
        return t, s
    coll = bpy.data.collections.get(s)
    if coll is not None:
        objs = [o for o in coll.all_objects if o.type == "MESH" and not (o.hide_render or o.hide_viewport)]
        t, _o, _b = _world_tris(objs)
        return t, s
    raise ValueError("找不到对象/集合/文件：%s" % s)


def _normalize_tris(tris):
    import numpy as np
    p = tris.reshape(-1, 3)
    lo, hi = p.min(axis=0), p.max(axis=0)
    c = (lo + hi) / 2.0
    longest = float((hi - lo).max())
    if longest <= 0:
        raise ValueError("退化网格（零尺寸 bbox）")
    return (tris - c) * (2.0 / longest)


def _sample(tris, n, rng):
    import numpy as np
    v0, v1, v2 = tris[:, 0], tris[:, 1], tris[:, 2]
    a = 0.5 * np.linalg.norm(np.cross(v1 - v0, v2 - v0), axis=1)
    tot = float(a.sum())
    if tot <= 0:
        p = tris.reshape(-1, 3)
        return p[rng.integers(0, len(p), size=int(n))]
    c = np.cumsum(a)
    idx = np.searchsorted(c, rng.random(int(n)) * tot)
    u = rng.random((int(n), 2))
    m = np.sqrt(u[:, 0])[:, None]
    s = u[:, 1][:, None]
    return v0[idx] * (1 - m) + v1[idx] * (m * (1 - s)) + v2[idx] * (m * s)


BVH_MAX_TRIS = 500000        # 超过就退回分格点对点（BVHTree.FromPolygons 的构建成本随面数线性涨）


def _surface_mean_dist(P, tris_target):
    """P 每点到目标三角面集合的最近距离均值 —— BVHTree（C 实现）= 点到**曲面**，不是点到点。"""
    import numpy as np
    from mathutils.bvhtree import BVHTree
    V = tris_target.reshape(-1, 3).tolist()
    polys = [(3 * i, 3 * i + 1, 3 * i + 2) for i in range(len(tris_target))]
    tree = BVHTree.FromPolygons(V, polys, all_triangles=True)
    fn = tree.find_nearest
    tot = 0.0
    n = len(P)
    for i in range(n):
        p = P[i]
        hit = fn((float(p[0]), float(p[1]), float(p[2])))
        if hit is not None and hit[3] is not None:
            tot += float(hit[3])
    return tot / max(1, n), int(n)


def _nn_mean(P, Q, cell=None, fast=True):
    """P 每点到 Q 的最近距离均值。

    fast=True 走"分格 + 补齐数组"的向量化路径（每格最多 32 点；只在 27 邻域里找最近，
    与 Procedura 的 cell≈2/cbrt(N) 同口径）—— 比逐点慢路径快约一个量级；任何异常/退化
    （单格点数 >32、孤立点）都回退到慢路径，结果口径不变。
    """
    import numpy as np
    if len(P) == 0 or len(Q) == 0:
        return float("nan")
    if cell is None:
        cell = 2.0 / max(1.0, round(len(P) ** (1.0 / 3.0)))
    if fast:
        try:
            return _nn_mean_grid(P, Q, cell)
        except Exception:
            pass
    return _nn_mean_slow(P, Q, cell)


def _nn_mean_grid(P, Q, cell):
    import numpy as np
    lo = np.array([-1.5, -1.5, -1.5])          # 规范化帧 [-1,1]，留余量
    K = int(np.ceil(3.0 / float(cell))) + 4

    def cid(X):
        return np.clip(np.floor((X - lo) / float(cell)).astype(np.int64), 0, K - 1)

    qc = cid(Q)
    lin = qc[:, 0] * K * K + qc[:, 1] * K + qc[:, 2]
    order = np.argsort(lin, kind="stable")
    lin_s = lin[order]
    starts = np.searchsorted(lin_s, np.arange(K * K * K))
    counts = np.bincount(lin, minlength=K * K * K)
    maxp = int(counts.max()) if len(counts) else 0
    if maxp <= 0 or maxp > 32:
        raise RuntimeError("dense-or-empty")
    pad = np.full((K * K * K, maxp, 3), 1e9, dtype=np.float64)
    within = np.arange(len(Q)) - starts[lin[order]]
    pad[lin[order], within] = Q[order]
    pad = pad.reshape(K, K, K, maxp, 3)
    pc = cid(P)
    offs = np.array([(dx, dy, dz) for dx in (-1, 0, 1) for dy in (-1, 0, 1) for dz in (-1, 0, 1)],
                    dtype=np.int64)
    out = np.empty(len(P), dtype=np.float64)
    BATCH = 2048                     # 批量取格：把 numpy 调用摊到几百个查询上（否则每点一次调用 ≈0.15 ms）
    for s in range(0, len(P), BATCH):
        pcx = pc[s:s + BATCH]
        nb = np.clip(pcx[:, None, :] + offs[None, :, :], 0, K - 1)       # (b,27,3)
        blk = pad[nb[:, :, 0], nb[:, :, 1], nb[:, :, 2]]                 # (b,27,maxp,3)
        blk = blk.reshape(blk.shape[0], -1, 3)
        d = np.linalg.norm(blk - P[s:s + BATCH][:, None, :], axis=2)
        out[s:s + BATCH] = d.min(axis=1)
    bad = out > 1e6                      # 27 邻域空 → 整云兜底（规范化后极少发生）
    if bad.any():
        for i in np.nonzero(bad)[0]:
            out[i] = float(np.linalg.norm(Q - P[i], axis=1).min())
    return float(out.mean())


def _nn_mean_slow(P, Q, cell):
    import numpy as np
    grid = {}
    cells = np.floor(Q / cell).astype(np.int64)
    for i in range(len(Q)):
        grid.setdefault((int(cells[i, 0]), int(cells[i, 1]), int(cells[i, 2])), []).append(i)
    offs = [(dx, dy, dz) for dx in (-1, 0, 1) for dy in (-1, 0, 1) for dz in (-1, 0, 1)]
    pc = np.floor(P / cell).astype(np.int64)
    out = np.empty(len(P), dtype=np.float64)
    for i in range(len(P)):
        cx, cy, cz = int(pc[i, 0]), int(pc[i, 1]), int(pc[i, 2])
        best = float("inf")
        for (dx, dy, dz) in offs:
            lst = grid.get((cx + dx, cy + dy, cz + dz))
            if not lst:
                continue
            d = float(np.linalg.norm(Q[lst] - P[i], axis=1).min())
            if d < best:
                best = d
                if best <= 0:
                    break
        if best == float("inf"):
            best = float(np.linalg.norm(Q - P[i], axis=1).min())
        out[i] = best
    return float(out.mean())


def audit_drift(a, b, samples=20000, seed=24233, normalize=True, fast=True):
    """对称 Chamfer 距离 = **形状漂移**（不是倒角！）。独立于渲染的数值复核通路。

    ⚠ 口径（Procedura 原样）：normalize=True 时**各自归一化**到单位包围盒 —— 平移与整体缩放被消除，
    所以这个数回答"形状改了多远"，不回答"挪了多远"。要连位移一起看：读 bbox_delta（原始帧的
    center/size 差，mm），或传 normalize=False 做同帧比较。
    """
    import numpy as np
    try:
        ta, la = _src_tris(a)
        tb, lb = _src_tris(b)
    except Exception as e:
        return _j({"ok": False, "error": str(e)[:200], "hint": "a/b 可以是对象名、集合名，或 .obj/.stl 路径"})
    if ta is None or tb is None or len(ta) == 0 or len(tb) == 0:
        return _j({"ok": False, "error": "空网格", "a_tris": 0 if ta is None else int(len(ta)),
                   "b_tris": 0 if tb is None else int(len(tb))})
    ta0, tb0 = ta, tb
    import time as _t
    _t0 = _t.perf_counter()
    try:
        if normalize:
            ta, tb = _normalize_tris(ta), _normalize_tris(tb)
        rng = np.random.default_rng(int(seed))
        Pa, Pb = _sample(ta, samples, rng), _sample(tb, samples, rng)
        metric = "point-to-surface(BVHTree)"
        if fast and len(ta) <= BVH_MAX_TRIS and len(tb) <= BVH_MAX_TRIS:
            try:
                a2b, _n1 = _surface_mean_dist(Pa, tb)
                b2a, _n2 = _surface_mean_dist(Pb, ta)
            except Exception:
                metric = "point-to-point(grid)"
                a2b, b2a = _nn_mean(Pa, Pb, fast=False), _nn_mean(Pb, Pa, fast=False)
        else:
            metric = "point-to-point(grid)"
            a2b, b2a = _nn_mean(Pa, Pb, fast=False), _nn_mean(Pb, Pa, fast=False)
    except Exception as e:
        return _j({"ok": False, "error": "采样/近邻失败：%s" % str(e)[:200]})
    ch = (a2b + b2a) / 2.0
    ms = int((_t.perf_counter() - _t0) * 1000)
    # 原始帧的位置 / 尺寸差（形状漂移看不到这一层，单独给）
    _m, mm_per_unit = _units()
    pa, pb = ta0.reshape(-1, 3), tb0.reshape(-1, 3)
    lo_a, hi_a = pa.min(axis=0), pa.max(axis=0)
    lo_b, hi_b = pb.min(axis=0), pb.max(axis=0)
    c_delta = [float((hi_b[i] + lo_b[i]) / 2.0 - (hi_a[i] + lo_a[i]) / 2.0) for i in range(3)]
    moved = float(np.linalg.norm(c_delta))
    size_a = [float(hi_a[i] - lo_a[i]) for i in range(3)]
    size_b = [float(hi_b[i] - lo_b[i]) for i in range(3)]
    band = "noise" if ch <= 0.01 else ("mild" if ch <= 0.05 else "strong")
    return _j({"ok": True, "chamfer": _r(ch, 6), "a_to_b": _r(a2b, 6), "b_to_a": _r(b2a, 6),
               "samples": int(samples), "seed": int(seed), "normalized": bool(normalize), "fast_nn": bool(fast),
               "ms": ms, "metric": metric,
               "a": la, "b": lb, "a_tris": int(len(ta0)), "b_tris": int(len(tb0)),
               "band": band, "band_read": {"noise": "≤0.01：基本未变（同网格读 ≈0）",
                                           "mild": "0.01–0.05：轻微改动（≤最长边 2.5%）",
                                           "strong": ">0.05：明显改动"}[band],
               "bbox_delta": {"center_delta_mm": [_r(x * mm_per_unit, 4) for x in c_delta],
                              "moved_mm": _r(moved * mm_per_unit, 4),
                              "moved_units": _r(moved, 6),
                              "size_a_mm": [_r(x * mm_per_unit, 4) for x in size_a],
                              "size_b_mm": [_r(x * mm_per_unit, 4) for x in size_b],
                              "note": "维度同帧原始值：形状漂移（chamfer）看不到这部分，别把它读成 0"},
               "noise_floor": "≈0（point-to-surface：源侧采样点正落在目标曲面上；退回 grid 口径时约 0.01–0.02）",
               "read_rule": "规范化帧里最长轴跨 [-1,1]：0.05 ≈ 最长边 2.5%；同 seed 同 samples 才可跨次比较；"
                            "metric=point-to-point(grid) 时是点到最近采样点，数值系统性偏大（≈点距/2），别与 BVH 口径混比"})


def audit_measure(objects=None, scope=None, include_hidden=False, neighbors=True, k=4, max_pairs=200):
    """测量包：世界 bbox + 逐轴间隙/重叠（mm 与 %）+ 邻居（契约图 + 空间最近 k）。"""
    import numpy as np
    objs, missing, err = _objs_for(objects, scope, include_hidden)
    if err:
        return _j({"ok": False, "error": err})
    if not objs:
        return _j({"ok": False, "error": "范围内 0 个 mesh", "missing": missing})
    m_per_unit, mm_per_unit = _units()
    _t, _o, boxes = _world_tris(objs)
    items, good = [], []
    for ob in objs:
        b = boxes.get(ob.name)
        if b is None:
            items.append({"name": ob.name, "error": "无几何（0 面）"})
            continue
        lo, hi = [float(x) for x in b[0]], [float(x) for x in b[1]]
        size = [hi[i] - lo[i] for i in range(3)]
        ctr = [(hi[i] + lo[i]) / 2.0 for i in range(3)]
        items.append({"name": ob.name, "min": [_r(x) for x in lo], "max": [_r(x) for x in hi],
                      "size": [_r(x) for x in size], "size_mm": [_r(x * mm_per_unit, 3) for x in size],
                      "center": [_r(x) for x in ctr], "tris": int(_tris(ob.data))})
        good.append((ob.name, lo, hi))
    pairs = []
    for i in range(len(good)):
        for j in range(i + 1, len(good)):
            na, la, ha = good[i]
            nb, lb, hb = good[j]
            gaps, overlaps = {}, {}
            for ax in range(3):
                g = max(0.0, max(la[ax] - hb[ax], lb[ax] - ha[ax]))
                ov = min(ha[ax], hb[ax]) - max(la[ax], lb[ax])
                gaps["xyz"[ax]] = _r(g * mm_per_unit, 4)
                overlaps["xyz"[ax]] = _r(ov * mm_per_unit, 4)
            sep = (sum(v * v for v in gaps.values())) ** 0.5
            pairs.append({"a": na, "b": nb, "gap_mm": _r(sep, 4), "axis_gap_mm": gaps, "axis_overlap_mm": overlaps,
                          "contact": bool(sep <= 1e-9),
                          "overlap_bbox": bool(all(overlaps[a] > 0 for a in "xyz")),
                          "center_dist_mm": _r(float(np.linalg.norm(np.array(
                              [(ha[i] + la[i] - hb[i] - lb[i]) / 2.0 for i in range(3)]))) * mm_per_unit, 4)})
            if len(pairs) >= int(max_pairs):
                break
        if len(pairs) >= int(max_pairs):
            break
    pairs.sort(key=lambda p: p["gap_mm"])
    out = {"ok": True, "units": {"m_per_unit": _r(m_per_unit, 6), "mm_per_unit": _r(mm_per_unit, 6)},
           "count": len(items), "items": items, "missing": missing,
           "pairs": pairs if pairs else [],
           "note": "所有 mm 口径都按 units.mm_per_unit 换算；bbox 级（不是三角级）"}
    if neighbors and good:
        # ① 契约图的邻居（若注册过组件/连接）
        decl = {}
        try:
            K = _kernel()
            store = getattr(K, "dsh_contract", None) if K is not None else None
            if store:
                name2comp = {}
                for cid, comp in (store.get("components") or {}).items():
                    for n in (comp.get("objects") or []):
                        name2comp.setdefault(n, []).append(cid)
                for conn in (store.get("connections") or {}).values():
                    for n in (store.get("components", {}).get(conn.get("a"), {}) or {}).get("objects", []) or []:
                        for m in (store.get("components", {}).get(conn.get("b"), {}) or {}).get("objects", []) or []:
                            if n != m:
                                decl.setdefault(n, set()).add(m)
        except Exception:
            decl = {}
        nb = {}
        for name, lo, hi in good:
            c = np.array([(hi[i] + lo[i]) / 2.0 for i in range(3)])
            arr = []
            for nm, l, h in good:
                if nm == name:
                    continue
                c2 = np.array([(h[i] + l[i]) / 2.0 for i in range(3)])
                arr.append((float(np.linalg.norm(c - c2)), nm))
            arr.sort(key=lambda x: x[0])
            nb[name] = {"declared": sorted(decl.get(name, [])),
                        "nearest": [{"name": nm, "center_dist_mm": _r(dd * mm_per_unit, 3)}
                                    for dd, nm in arr[:int(k)]]}
        out["neighbors"] = nb
    return _j(out)


def audit_snap_floaters(objects=None, scope=None, dry_run=True, visible_frac=VISIBLE_SPAN_FRACTION,
                        micro_gap_mm=MICRO_GAP_MM, overlap_mm=0.05, apply_parented=False, max_tris=None):
    """把可见浮块贴到最近邻（默认只报告）。整件平移只对"完全属于该浮块"的对象做，焊接体一块不撕。"""
    import numpy as np
    objs, missing, err = _objs_for(objects, scope, False)
    if err:
        return _j({"ok": False, "error": err})
    if not objs:
        return _j({"ok": False, "error": "范围内 0 个 mesh", "missing": missing})
    a = _conn_analyze(objs, visible_frac=visible_frac, micro_gap_mm=micro_gap_mm, max_tris=max_tris)
    if not a.get("analyzed"):
        return _j({"ok": False, "error": a.get("reason"), "note": a.get("note"),
                   "hint": "先缩小 scope 或提高 env DSH_CONN_MAX_TRIS"})
    _m, mm_per_unit = _units()
    eps = float(overlap_mm) / mm_per_unit
    moves, skipped = [], []
    tree_cache = {}
    for s in a["floaters"]:
        if not s.get("visible"):
            continue
        owners = s["owners"]
        # 该对象是否"整件都在这个浮块里"（否则平移会撕开焊接体 —— Procedura v3 snap 的翻车点）
        obj_tris = a.get("obj_tris") or {}
        whole = [n for n in owners if obj_tris.get(n) and obj_tris.get(n) == owners[n]]
        if not whole:
            skipped.append({"rank": s["rank"], "span_fraction": s["span_fraction"], "gap_mm": s["gap_mm"],
                            "why": "浮块只是某个对象的一部分（焊接体），整件平移会撕开焊点 → 交给编辑那一步处理"})
            continue
        # 朝最近邻分量方向平移：逐轴 gap + 一点重叠量
        nb = None
        for t2 in a["components"]:
            if t2["rank"] == s["rank"]:
                continue
            d2 = 0.0
            for kk in range(3):
                g = max(0.0, max(s["min"][kk] - t2["max"][kk], t2["min"][kk] - s["max"][kk]))
                d2 += g * g
            if nb is None or d2 < nb[0]:
                nb = (d2, t2)
        if nb is None:
            continue
        t2 = nb[1]
        delta = []
        for kk in range(3):
            if s["max"][kk] < t2["min"][kk]:
                delta.append(_r(t2["min"][kk] - s["max"][kk] + eps, 8))
            elif t2["max"][kk] < s["min"][kk]:
                delta.append(_r(-(s["min"][kk] - t2["max"][kk] + eps), 8))
            else:
                delta.append(0.0)
        method = "bbox"
        if all(abs(x) < 1e-12 for x in delta):
            # bbox 读成 0：floaters 里只会出现 attached=False（真浮块）→ 必须走网格级最近点对
            raw = a.get("_raw") or {}
            if not raw:
                skipped.append({"rank": s["rank"], "why": "没有原始三角数据，无法做网格级平移"})
                continue
            dvec, dist_u, note = _snap_pair_mesh(raw["tris"], raw["comp"], s["comp"], tree_cache)
            if not dvec:
                skipped.append({"rank": s["rank"], "span_fraction": s["span_fraction"], "gap_mm": s["gap_mm"],
                                "why": note})
                continue
            scale = 1.0 + (eps / max(float(dist_u), 1e-9))
            delta = [_r(dvec[k] * scale, 8) for k in range(3)]
            method = "mesh:" + note
        entry = {"rank": s["rank"], "objects": whole, "delta_units": delta,
                 "delta_mm": [_r(d * mm_per_unit, 4) for d in delta], "method": method,
                 "gap_before_mm": s["gap_mm"], "target_component_rank": t2["rank"],
                 "span_fraction": s["span_fraction"]}
        blocked = []
        for n in whole:
            ob = bpy.data.objects.get(n)
            if ob is None:
                continue
            if ob.parent is not None and not apply_parented:
                blocked.append("%s 有父级（平移会被父级覆盖）" % n)
            if ob.animation_data is not None:
                blocked.append("%s 有动画数据（关键帧会覆盖平移）" % n)
        if blocked:
            entry["why_not_applied"] = blocked
            skipped.append({"rank": s["rank"], "span_fraction": s["span_fraction"], "gap_mm": s["gap_mm"],
                            "why": "; ".join(blocked)})
        moves.append(entry)
    applied = []
    if not dry_run:
        for mv in moves:
            if mv.get("why_not_applied"):
                continue
            dv = np.array(mv["delta_units"], dtype=np.float64)
            for n in mv["objects"]:
                ob = bpy.data.objects.get(n)
                if ob is None:
                    continue
                ob.matrix_world.translation = ob.matrix_world.translation + Vector(dv)
                applied.append(n)
        try:
            bpy.context.view_layer.update()
        except Exception:
            pass
    return _j({"ok": True, "dry_run": bool(dry_run), "units": a["units"],
               "moves": moves, "skipped": skipped, "applied": applied,
               "before": {"visible_floater_count": a["visible_floater_count"],
                          "real_floater_count": a["real_floater_count"], "gate": _gate_from(a)},
               "hint": ("只报告。" if dry_run else "已平移（只动了整件属于浮块的对象）") +
                       " 应用前建议先 blender_rt_txn(op=snapshot)；改完用 audit_gate 复核",
               "note": "平移只收口「贴而未重合」；若浮块是对象的一部分，需在编辑那一步改几何"})


def audit_gate_selftest():
    """装配级判据的合成自检：可见浮块 / 微隙容忍 / 归因 / 漂移 四项一起验。"""
    import numpy as np
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
                       "connectivity": "audit_connectivity(objects|scope, visible_frac, micro_gap_mm, limit, max_tris)"
                                       " → 连通分量 / 浮块 / 微隙 / 归因",
                       "gate": "audit_gate(objects|scope, envelope, micro_gap_mm, max_floater_span_fraction)"
                               " → 出厂门（连通 + 包络 + 未分析三态）",
                       "drift": "audit_drift(a, b, samples, seed) → 对称 Chamfer 距离（两条网格改了多远）",
                       "measure": "audit_measure(objects|scope, neighbors, k) → 世界 bbox + 逐轴间隙/重叠 mm/%",
                       "snap_floaters": "audit_snap_floaters(objects|scope, dry_run=true) → 把可见浮块贴到最近邻",
                       "selftest": "audit_selftest()",
                       "gate_selftest": "audit_gate_selftest() → 装配级判据合成自检"},
               "fields": "boundary_edges / nonmanifold_edges / degenerate_faces / loose_verts / "
                         "self_intersections / normals_outward / closed / tris / aabb / out_of_bounds；"
                         "装配侧：components / floaters[{span_fraction, gap_mm, bbox_micro, mesh_confirmed, true_gap_units, attribution}] / "
                         "tolerated[]（确认相接）/ unconfirmed[]（未确认→降级）/ gap_histogram / gate{state,offenders}",
               "units": "mm 口径阈值一律经场景 scale_length 换算（返回 units 块），绝不硬套 mm",
               "hard_rule": "0 个 mesh ⇒ ok=false（静默空产出必须失败）；大网格 ⇒ analyzed=false，"
                            "未分析不得读作已连通"})


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
           "gate_selftest": audit_gate_selftest}
    fn = ops.get(str(op))
    if fn is None:
        return _j({"ok": False, "error": "unknown audit op", "op": op, "ops": sorted(ops)})
    try:
        return fn(**kw)
    except TypeError as e:
        return _j({"ok": False, "error": "参数不匹配: %s" % str(e)[:200], "op": op, "help": audit_help()})


import sys as _sys
_K = _sys.modules.get("dsh_rt_kernel")
if _K is not None:
    _K.dsh_audit_api = {"version": AUDIT_VERSION, "dispatch": audit_dispatch, "mesh": audit_mesh,
                        "scene": audit_scene, "duplicates": audit_duplicates, "purge_orphans": purge_orphans,
                        "connectivity": audit_connectivity, "gate": audit_gate, "drift": audit_drift,
                        "measure": audit_measure, "snap_floaters": audit_snap_floaters,
                        "selftest": audit_selftest, "gate_selftest": audit_gate_selftest, "help": audit_help}
