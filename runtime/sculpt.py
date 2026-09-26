# -*- coding: utf-8 -*-
"""DSH 程序化雕刻（v0.9.6 · 上游整合 A1）—— 把"雕刻"从手势输入变成可复算的几何操作。

为什么有它：本插件 runtime 全库检索 sculpt/remesh/multires/dyntopo/voxel/brush = 0 命中，
而上游两家都做不到程序化雕刻（blend-ai README 自述 "Sculpt strokes cannot be simulated"，
mcp-for-blender 根本没有雕刻）。本机实测（Blender 5.2.2 LTS）结论：

  可用（GUI 与无头都行）  object.mode_set(SCULPT)、tool_settings.sculpt.brush、
                          brush.size/.strength、use_symmetry_*、dynamic_topology_toggle、
                          data.remesh_voxel_size + object.voxel_remesh（1986→12148 顶点 / 0.084 s）、
                          multires_subdivide、object.subdivision_set、.sculpt_mask 属性
  只在 GUI 可用           sculpt.mesh_filter / mask_from_cavity / face_sets_init / face_sets_create
                          （要真 VIEW_3D 上下文，用 view.py 的 _find_view3d + temp_override）
  不可用（5.2 API 层）    sculpt.brush_stroke —— 上下文已解决（poll() 实测 True），但 stroke 集合
                          在 RNA 侧拒绝 dict（6 种格式全失败）、OperatorStrokeElement 无法实例化

所以这里的主力是 **L2 自研 numpy 位移笔刷**：读 foreach_get("co") → falloff → 沿法线位移 →
foreach_set。实测 1986 顶点单笔 257 顶点受影响 / 6 ms；天然支持对称、遮罩乘子、笔触路径插值。
不依赖 sculpt 模式、不改全局模式、无头可跑、结果可 diff —— 与 generator_*（程序即形状）同源。

API 挂 K.dsh_sculpt_api；入口：
    blender_rt_plan(op="sculpt_scan",   args={objects:["Head"]})
    blender_rt_plan(op="sculpt_setup",  args={object:"Head", mode:"VOXEL", voxel_size:"auto"})
    blender_rt_plan(op="sculpt_apply",  args={object:"Head", strokes:[
        {brush:"draw",   points:[[0,0,1.2],[0.1,0,1.3]], radius:0.35, strength:0.6},
        {brush:"crease", points:[[0.6,0,0.8]], radius:0.12, strength:0.8, symmetry:["x"]}]})
    blender_rt_plan(op="sculpt_filter", args={objects:["Head"], type:"SMOOTH", strength:0.4})
    blender_rt_plan(op="sculpt_mask",   args={objects:["Head"], mode:"sphere", center:[0,0,1], radius:0.5})
    blender_rt_plan(op="sculpt_remesh", args={objects:["Head"], mode:"VOXEL", voxel_size:"auto"})

**硬规则**：0 个 mesh ⇒ ok=false；NaN/Inf 参数一律拒绝（会写坏 .blend）；体素重构先算预算
（cells = Π(dim/v)，超 max_cells 就报错并给出可用尺寸，而不是让 Blender 卡到超时）。
雕刻改拓扑后对象级 mark/revert 回不去 —— 要回退请用 blender_rt_txn(op="snapshot")。
"""
import json
import math
import time

import bpy
import bmesh
from mathutils import Vector

SCULPT_VERSION = 1

# 与 blend-ai 同口径的体素预算上限（思路来自其 addon/handlers/sculpting.py:137；实现为本插件自有代码）
MAX_VOXEL_CELLS = 40_000_000
BRUSHES = ("draw", "inflate", "pinch", "flatten", "smooth", "crease")
FALLOFFS = ("smooth", "linear", "sphere", "constant", "sharp")
FILTERS = ("SMOOTH", "INFLATE", "RELAX", "RELAX_FACE_SETS", "SHARPEN", "ENHANCE_DETAILS", "ERASE_DISPLACEMENT")


import sys as _sys_kit
_KIT = getattr(_sys_kit.modules.get("dsh_rt_kernel"), "dsh_kit", None)
if _KIT is None:
    raise RuntimeError("sculpt 需要共享内核 K.dsh_kit（由 KERNEL_BOOTSTRAP 注入）")


_j = _KIT.j  # 共享内核（原自带实现已删，见 S1）
def _num(v, name, lo=None, hi=None, default=None):
    """参数纪律：非有限数一律拒绝（NaN/Infinity 写进 Blender 属性会持久化进 .blend）。"""
    if v is None:
        if default is None:
            raise ValueError("%s 必填" % name)
        return float(default)
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        raise ValueError("%s 需要数字，收到 %r" % (name, v))
    f = float(v)
    if not math.isfinite(f):
        raise ValueError("%s 必须是有限数（NaN/Inf 会写坏 .blend）" % name)
    if lo is not None and f < lo:
        raise ValueError("%s=%g 小于下限 %g" % (name, f, lo))
    if hi is not None and f > hi:
        raise ValueError("%s=%g 大于上限 %g" % (name, f, hi))
    return f


def _vec3(v, name):
    if not isinstance(v, (list, tuple)) or len(v) != 3:
        raise ValueError("%s 需要 [x,y,z]，收到 %r" % (name, v))
    return [_num(v[0], name + "[0]"), _num(v[1], name + "[1]"), _num(v[2], name + "[2]")]


def _kernel():
    import sys
    return sys.modules.get("dsh_rt_kernel")


def _objs(objects=None, scope="ACTIVE"):
    """取 mesh 对象。0 个 ⇒ 抛错（外部反馈的'静默空产出'事故就在这条上）。"""
    if objects:
        names = [objects] if isinstance(objects, str) else list(objects)
        out = []
        for n in names:
            ob = bpy.data.objects.get(str(n))
            if ob is None:
                raise ValueError("对象不存在：%s" % n)
            if ob.type != "MESH":
                raise ValueError("%s 不是 mesh（type=%s）" % (n, ob.type))
            out.append(ob)
        if not out:
            raise ValueError("objects 为空")
        return out
    sc = str(scope or "ACTIVE").upper()
    if sc == "ACTIVE":
        ob = bpy.context.view_layer.objects.active
        if ob is None or ob.type != "MESH":
            raise ValueError("没有活动的 mesh 对象（给 objects=[...] 或 scope=SELECTED/VISIBLE/ALL）")
        return [ob]
    if sc == "SELECTED":
        sel = [o for o in bpy.context.selected_objects if o.type == "MESH"]
        if not sel:
            raise ValueError("没有选中的 mesh")
        return sel
    if sc == "VISIBLE":
        vis = [o for o in bpy.context.view_layer.objects if o.type == "MESH" and o.visible_get()]
        if not vis:
            raise ValueError("没有可见 mesh")
        return vis
    if sc == "ALL":
        allm = [o for o in bpy.data.objects if o.type == "MESH"]
        if not allm:
            raise ValueError("场景里没有 mesh")
        return allm
    raise ValueError("未知 scope：%s（ACTIVE/SELECTED/VISIBLE/ALL）" % scope)


def _tris(me):
    return sum(max(0, len(p.vertices) - 2) for p in me.polygons)


def _dims(ob):
    return [max(float(d), 1e-6) for d in ob.dimensions]


def _voxel_plan(ob, voxel_size, max_cells=MAX_VOXEL_CELLS):
    """预算守卫：Blender 接受任意 voxel_size 然后开始磨 —— 超预算先在插件侧报错。"""
    d = _dims(ob)
    if voxel_size in (None, "auto"):
        # 目标：cells ≈ max_cells/4（留 4x 余量），反解一个体素尺寸
        v = ((d[0] * d[1] * d[2]) / (max_cells / 4.0)) ** (1.0 / 3.0)
        return round(float(v), 6), True
    v = _num(voxel_size, "voxel_size", 1e-5, 100.0)
    cells = 1.0
    for x in d:
        cells *= x / v
    if cells > max_cells:
        workable = ((d[0] * d[1] * d[2]) / max_cells) ** (1.0 / 3.0)
        raise ValueError(
            "voxel_size %g 在 %.2f x %.2f x %.2f 的对象上约需 %s 个体素，会卡住 Blender 而不是失败；"
            "请用 %.4f 或更大（或 voxel_size=\"auto\"）" % (v, d[0], d[1], d[2], format(int(cells), ","), workable))
    return v, False


def _vertex_normals(me):
    """顶点法线：优先用 mesh.vertex_normals，取不到就 bmesh 兜底。"""
    import numpy as np
    n = len(me.vertices)
    arr = np.zeros((n, 3), dtype=np.float64)
    try:
        me.vertex_normals  # noqa: B018  4.1+ 只读集合
        for i in range(n):
            v = me.vertex_normals[i].vector
            arr[i] = (v[0], v[1], v[2])
        return arr
    except Exception:
        bm = bmesh.new()
        bm.from_mesh(me)
        bm.verts.ensure_lookup_table()
        for i, v in enumerate(bm.verts):
            arr[i] = (v.normal[0], v.normal[1], v.normal[2])
        bm.free()
        return arr


def _falloff(kind, t):
    import numpy as np
    t = np.clip(t, 0.0, 1.0)
    if kind == "linear":
        return 1.0 - t
    if kind == "sphere":
        return np.sqrt(np.clip(1.0 - t * t, 0.0, 1.0))
    if kind == "constant":
        return np.ones_like(t)
    if kind == "sharp":
        return np.clip(1.0 - t, 0.0, 1.0) ** 4
    s = 1.0 - t  # smooth（默认）：smoothstep
    return s * s * (3.0 - 2.0 * s)


def _mask_array(me):
    """读 .sculpt_mask（有则返回 0..1 掩码，无则 None）。"""
    import numpy as np
    att = me.attributes.get(".sculpt_mask") if hasattr(me, "attributes") else None
    if att is None:
        return None
    n = len(me.vertices)
    a = np.zeros(n, dtype=np.float64)
    try:
        buf = np.empty(n, dtype=np.float32)
        att.data.foreach_get("value", buf)
        a = buf.astype(np.float64)
    except Exception:
        return None
    return np.clip(a, 0.0, 1.0)


def _dab_points(points, radius, spacing_frac=0.25):
    """把控制点插值成 dab 序列（相邻点间距 > radius*spacing_frac 就补点）。"""
    import numpy as np
    pts = [Vector(p) for p in points]
    if len(pts) == 1:
        return [[float(c) for c in pts[0]]]
    step = max(float(radius) * float(spacing_frac), 1e-6)
    out = []
    for i in range(len(pts) - 1):
        a, b = pts[i], pts[i + 1]
        d = (b - a).length
        k = max(1, int(math.ceil(d / step)))
        for s in range(k):
            p = a + (b - a) * (float(s) / k)
            out.append([p.x, p.y, p.z])
    out.append([float(pts[-1].x), float(pts[-1].y), float(pts[-1].z)])
    return out


def _apply_one(ob, stroke, use_mask=True):
    """单个笔触：numpy 位移。返回统计 dict。"""
    import numpy as np
    me = ob.data
    n = len(me.vertices)
    if n == 0:
        return {"name": ob.name, "skipped": "0 顶点"}

    brush = str(stroke.get("brush") or "draw").lower()
    if brush not in BRUSHES:
        raise ValueError("未知 brush：%s（允许 %s）" % (brush, list(BRUSHES)))
    radius = _num(stroke.get("radius"), "radius", 1e-6, 1e6)
    strength = _num(stroke.get("strength"), "strength", default=0.5)
    fall = str(stroke.get("falloff") or "smooth").lower()
    if fall not in FALLOFFS:
        raise ValueError("未知 falloff：%s（允许 %s）" % (fall, list(FALLOFFS)))
    raw = stroke.get("points")
    if not isinstance(raw, (list, tuple)) or not raw:
        raise ValueError("stroke.points 必填（[[x,y,z], ...]）")
    points = [_vec3(p, "points[%d]" % i) for i, p in enumerate(raw)]
    sym = stroke.get("symmetry") or []
    sym = [str(s).lower() for s in (sym if isinstance(sym, (list, tuple)) else [sym])]
    invert = bool(stroke.get("invert"))
    dirn = stroke.get("direction") or "normal"

    co = np.empty(n * 3, dtype=np.float64)
    me.vertices.foreach_get("co", co)
    co = co.reshape(n, 3)
    nrm = _vertex_normals(me)
    mask = _mask_array(me) if use_mask else None

    # 对称：镜像轴生成"独立的多条笔触"（对象局部原点，与 Blender 雕刻对称同口径）。
    # 曾经把镜像点追加进同一条折线 —— 那会让 _dab_points 在 +x 与 -x 之间插值出
    # 一条横穿物体的假路径（实测 1 个点变成 39 个 dab，把表面拖花）。
    axis_idx = {"x": 0, "y": 1, "z": 2}
    paths = [[list(p) for p in points]]
    for s in sym:
        if s not in axis_idx:
            raise ValueError("未知 symmetry 轴：%s（x/y/z）" % s)
        i = axis_idx[s]
        mirrored = []
        for path in paths:
            mirrored.append([[( -p[0] if i == 0 else p[0]), (-p[1] if i == 1 else p[1]), (-p[2] if i == 2 else p[2])] for p in path])
        paths = paths + mirrored

    dabs = []
    for path in paths:
        dabs.extend(_dab_points(path, radius))
    used_paths = len(paths)
    disp = np.zeros((n, 3), dtype=np.float64)
    affect = np.zeros(n, dtype=bool)
    t0 = time.time()
    for p in dabs:
        c = np.asarray(p, dtype=np.float64)
        d = np.linalg.norm(co - c, axis=1)
        t = d / radius
        inside = t <= 1.0
        if not bool(inside.any()):
            continue
        f = _falloff(fall, t)
        if mask is not None:
            f = f * (1.0 - mask)
        amp = strength * float(radius) * 0.25  # 与半径成比例的单 dab 位移上限
        if brush == "draw":
            disp += (nrm * (f * amp * (-1.0 if invert else 1.0))[:, None])
        elif brush == "inflate":
            dirv = co - np.array([ob.matrix_world.translation[i] for i in range(3)])
            ln = np.linalg.norm(dirv, axis=1, keepdims=True)
            dirv = dirv / np.maximum(ln, 1e-9)
            disp += dirv * (f * amp * (-1.0 if invert else 1.0))[:, None]
        elif brush == "pinch":
            dirv = c[None, :] - co
            ln = np.linalg.norm(dirv, axis=1, keepdims=True)
            dirv = dirv / np.maximum(ln, 1e-9)
            disp += dirv * (f * amp * 0.6) [:, None]
        elif brush == "flatten":
            # 朝笔触点法线方向的平面压平：把超出平面的部分拉回
            pl = c
            nvec = nrm[inside].mean(axis=0) if bool(inside.any()) else np.array([0.0, 0.0, 1.0])
            ln = float(np.linalg.norm(nvec)) or 1.0
            nvec = nvec / ln
            signed = (co - pl[None, :]) @ nvec
            delta = -signed * f * strength
            disp += nvec[None, :] * delta[:, None] * (-1.0 if invert else 1.0)
        elif brush == "smooth":
            # 邻接平均（用边表建一次平均，后面按 falloff 混）
            if not hasattr(_apply_one, "_adj"):
                _apply_one._adj = {}
            key = (ob.name, n)
            adj = _apply_one._adj.get(key)
            if adj is None:
                bm = bmesh.new()
                bm.from_mesh(me)
                bm.verts.ensure_lookup_table()
                idx = [np.array([e.other_vert(v).index for e in v.link_edges], dtype=np.int64) for v in bm.verts]
                bm.free()
                adj = idx
                _apply_one._adj[key] = adj
            avg = co.copy()
            for i in np.nonzero(inside)[0]:
                nb = adj[int(i)]
                if len(nb):
                    avg[i] = co[nb].mean(axis=0)
            disp += (avg - co) * (f * strength)[:, None]
        elif brush == "crease":
            tang = co - c[None, :]
            ln = np.linalg.norm(tang, axis=1, keepdims=True)
            tang = tang / np.maximum(ln, 1e-9)
            proj = nrm - tang * (np.sum(nrm * tang, axis=1, keepdims=True))
            ln2 = np.linalg.norm(proj, axis=1, keepdims=True)
            proj = proj / np.maximum(ln2, 1e-9)
            disp -= proj * (f * amp * (-1.0 if invert else 1.0))[:, None]
        affect |= inside

    if dirn not in ("normal", None, "normal_z"):
        dv = np.asarray(_vec3(dirn, "direction"), dtype=np.float64)
        ln = float(np.linalg.norm(dv)) or 1.0
        dv = dv / ln
        # 指定方向：把已算的位移投影到该方向（保持笔刷形状，方向统一）
        mag = np.linalg.norm(disp, axis=1, keepdims=True)
        disp = dv[None, :] * mag * np.sign((disp * nrm).sum(axis=1, keepdims=True) + 1e-12)

    new = co + disp
    me.vertices.foreach_set("co", new.reshape(-1))
    me.update()
    if hasattr(me, "calc_normals_split"):
        pass
    maxd = float(np.abs(new - co).max()) if n else 0.0
    return {"name": ob.name, "brush": brush, "dabs": len(dabs), "stroke_paths": used_paths, "verts": n,
            "affected_verts": int(affect.sum()), "max_delta": round(maxd, 8),
            "ms": int((time.time() - t0) * 1000)}


def sculpt_scan(objects=None, scope="ACTIVE"):
    """只读体检：能不能雕、怎么雕、体素预算多大、遮罩/面组有几何。"""
    obs = _objs(objects, scope)
    rows = []
    for ob in obs:
        me = ob.data
        ts = bpy.context.tool_settings.sculpt
        row = {"name": ob.name, "verts": len(me.vertices), "tris": _tris(me),
               "dimensions": [round(x, 6) for x in _dims(ob)],
               "mode": ob.mode, "modifiers": [m.type for m in ob.modifiers]}
        row["has_multires"] = any(m.type == "MULTIRES" for m in ob.modifiers)
        row["mask_attr"] = bool(hasattr(me, "attributes") and me.attributes.get(".sculpt_mask"))
        att = me.attributes.get(".sculpt_mask") if hasattr(me, "attributes") else None
        if att is not None:
            try:
                import numpy as np
                buf = np.empty(len(me.vertices), dtype=np.float32)
                att.data.foreach_get("value", buf)
                row["mask_coverage"] = round(float((buf > 0.05).mean()), 4)
                row["mask_mean"] = round(float(buf.mean()), 4)
            except Exception:
                pass
        row["symmetry"] = [bool(ts.use_symmetry_x), bool(ts.use_symmetry_y), bool(ts.use_symmetry_z)]
        row["brush"] = ts.brush.name if ts.brush else None
        row["dyntopo"] = bool(getattr(bpy.context.sculpt_object, "use_dynamic_topology_sculpting", False)) if bpy.context.sculpt_object else False
        try:
            vy, auto = _voxel_plan(ob, "auto")
            vx, _ = _voxel_plan(ob, None)
            row["voxel_auto"] = vx
            row["voxel_budget_note"] = "cells = Π(dim/v)，上限 %s" % format(MAX_VOXEL_CELLS, ",")
        except Exception as e:
            row["voxel_error"] = str(e)[:160]
        row["strokes_available"] = "numpy 位移笔刷（本模块）"
        rows.append(row)
    return _j({"ok": True, "scope": str(scope or "ACTIVE").upper(), "count": len(rows), "objects": rows,
               "capability": {"scriptable_free": ["draw", "inflate", "pinch", "flatten", "smooth", "crease"],
                              "scriptable_gui_only": ["mesh_filter", "mask_from_cavity", "face_sets_init"],
                              "not_scriptable": ["sculpt.brush_stroke（5.2 RNA 拒绝 dict / 无法实例化 stroke 元素）"]},
               "note": "雕刻改拓扑后对象级 mark/revert 回不去；改前用 blender_rt_txn(op=\"snapshot\")"})


def sculpt_setup(object=None, objects=None, mode="KEEP", voxel_size=None, levels=2, detail_size=12.0,
                  detail_mode="RELATIVE", symmetry=None, apply_modifiers=False):
    """拓扑准备：进入雕刻前把底模弄对（VOXEL 重构 / MULTIRES / SUBDIV / DYNTOPO）。"""
    obs = _objs(objects or object)
    m = str(mode or "KEEP").upper()
    if m not in ("KEEP", "VOXEL", "MULTIRES", "SUBDIV", "DYNTOPO"):
        raise ValueError("未知 mode：%s（KEEP/VOXEL/MULTIRES/SUBDIV/DYNTOPO）" % mode)
    out = []
    for ob in obs:
        rec = {"name": ob.name, "mode": m, "verts_before": len(ob.data.vertices)}
        t0 = time.time()
        frm = ob.mode
        if m == "VOXEL":
            v, auto = _voxel_plan(ob, voxel_size)
            ob.data.remesh_voxel_size = v
            if bpy.context.view_layer.objects.active is not ob:
                bpy.context.view_layer.objects.active = ob
            r = bpy.ops.object.voxel_remesh()
            rec["voxel_size"] = v
            rec["auto"] = bool(auto)
            rec["op"] = str(r)
        elif m == "MULTIRES":
            md = ob.modifiers.new(name="Multires", type="MULTIRES")
            lv = int(_num(levels, "levels", 1, 6))
            for _ in range(lv):
                bpy.context.view_layer.objects.active = ob
                bpy.ops.object.multires_subdivide(modifier=md.name, mode="CATMULL_CLARK")
            rec["modifier"] = md.name
            rec["levels"] = lv
            rec["sculpt_levels"] = int(md.sculpt_levels)
        elif m == "SUBDIV":
            lv = int(_num(levels, "levels", 1, 6))
            bpy.context.view_layer.objects.active = ob
            bpy.ops.object.subdivision_set(level=lv, relative=False)
            rec["levels"] = lv
        elif m == "DYNTOPO":
            bpy.context.view_layer.objects.active = ob
            if ob.mode != "SCULPT":
                bpy.ops.object.mode_set(mode="SCULPT")
            if not bpy.context.sculpt_object.use_dynamic_topology_sculpting:
                bpy.ops.sculpt.dynamic_topology_toggle()
            ts = bpy.context.tool_settings.sculpt
            ts.detail_size = _num(detail_size, "detail_size", 0.1, 500.0)
            ts.detail_type_method = str(detail_mode).upper()
            rec["detail_size"] = ts.detail_size
            rec["detail_mode"] = ts.detail_type_method
            bpy.ops.object.mode_set(mode="OBJECT")
        if symmetry:
            s = symmetry if isinstance(symmetry, (list, tuple)) else [symmetry]
            ts = bpy.context.tool_settings.sculpt
            ts.use_symmetry_x = "x" in [str(k).lower() for k in s]
            ts.use_symmetry_y = "y" in [str(k).lower() for k in s]
            ts.use_symmetry_z = "z" in [str(k).lower() for k in s]
            rec["symmetry"] = [bool(ts.use_symmetry_x), bool(ts.use_symmetry_y), bool(ts.use_symmetry_z)]
        try:
            if frm != ob.mode:
                bpy.ops.object.mode_set(mode=frm)
        except Exception:
            pass
        rec["verts_after"] = len(ob.data.vertices)
        rec["tris_after"] = _tris(ob.data)
        rec["ms"] = int((time.time() - t0) * 1000)
        out.append(rec)
    return _j({"ok": True, "objects": out})


def sculpt_apply(object=None, objects=None, strokes=None, use_mask=True):
    """L2 位移笔刷：一笔 = 一条控制点路径 + 一个笔刷 + 半径/强度/衰减（+ 对称/遮罩/方向）。"""
    obs = _objs(objects or object)
    if not isinstance(strokes, (list, tuple)) or not strokes:
        raise ValueError("strokes 必填（[{brush, points, radius, strength, falloff, symmetry, invert, direction}]）")
    allowed = ("brush", "points", "radius", "strength", "falloff", "symmetry", "invert", "direction")
    for i, s in enumerate(strokes):
        if not isinstance(s, dict):
            raise ValueError("strokes[%d] 需要对象" % i)
    out = []
    for ob in obs:
        recs = []
        for s in strokes:
            recs.append(_apply_one(ob, s, use_mask=bool(use_mask)))
        out.append({"object": ob.name, "strokes": recs,
                    "verts_after": len(ob.data.vertices), "tris_after": _tris(ob.data)})
    return _j({"ok": True, "results": out,
               "note": "位移写在基础网格上（不是 multires 层）；随后可 sculpt_filter 平滑、sculpt_remesh 重拓扑"})


def sculpt_filter(objects=None, scope="ACTIVE", type="SMOOTH", strength=0.3, iterations=1, mask=False):
    """GUI 滤镜笔刷（sculpt.mesh_filter）：整块平滑/膨胀/松弛；需要真 3D 视口上下文。"""
    t = str(type or "SMOOTH").upper()
    if t not in FILTERS:
        raise ValueError("未知 type：%s（允许 %s）" % (type, list(FILTERS)))
    st = _num(strength, "strength", 0.0, 1.0)
    it = int(_num(iterations, "iterations", 1, 50))
    obs = _objs(objects, scope)
    # 真 UI 上下文：与 view.py 同法找 VIEW_3D
    win = ar = reg = None
    for w in bpy.context.window_manager.windows:
        if w.screen is None:
            continue
        for a in w.screen.areas:
            if a.type == "VIEW_3D":
                win, ar = w, a
                reg = next((r for r in a.regions if r.type == "WINDOW"), None)
                break
        if win:
            break
    if not (win and ar and reg):
        return _j({"ok": False, "error": "没有 VIEW_3D 区域：mesh_filter 需要 GUI 视口上下文（无头 -b 不支持）",
                   "hint": "改用 sculpt_apply（numpy 位移，无头可用）"})
    out = []
    for ob in obs:
        frm = ob.mode
        t0 = time.time()
        rs = []
        try:
            for o in bpy.context.selected_objects:
                o.select_set(False)
            ob.select_set(True)
            bpy.context.view_layer.objects.active = ob
            if ob.mode != "SCULPT":
                bpy.ops.object.mode_set(mode="SCULPT")
            if mask:
                try:
                    bpy.context.tool_settings.sculpt.use_mask = True
                except Exception:
                    pass
            with bpy.context.temp_override(window=win, screen=win.screen, area=ar, region=reg):
                rs = []
                for _ in range(it):
                    rs.append(str(bpy.ops.sculpt.mesh_filter(type=t, strength=st)))
        finally:
            try:
                if ob.mode != frm:
                    bpy.ops.object.mode_set(mode=frm if frm else "OBJECT")
            except Exception:
                pass
        out.append({"name": ob.name, "type": t, "iterations": it, "strength": st,
                    "op": rs[-1] if rs else None, "ms": int((time.time() - t0) * 1000),
                    "verts": len(ob.data.vertices)})
    return _j({"ok": True, "objects": out})


def sculpt_mask(objects=None, scope="ACTIVE", mode="sphere", center=None, radius=0.3, invert=False,
                value=1.0, smooth_iterations=0):
    """遮罩：sphere/box 区域直接写 .sculpt_mask；from_cavity/init 走 GUI 算子；clear 清空。"""
    import numpy as np
    m = str(mode or "sphere").lower()
    if m not in ("sphere", "box", "clear", "from_cavity", "init"):
        raise ValueError("未知 mode：%s（sphere/box/clear/from_cavity/init）" % mode)
    obs = _objs(objects, scope)
    out = []
    for ob in obs:
        me = ob.data
        n = len(me.vertices)
        rec = {"name": ob.name, "mode": m, "verts": n}
        if m == "clear":
            att = me.attributes.get(".sculpt_mask") if hasattr(me, "attributes") else None
            if att is not None:
                me.attributes.remove(att)
            rec["cleared"] = True
        elif m in ("sphere", "box"):
            co = np.empty(n * 3, dtype=np.float64)
            me.vertices.foreach_get("co", co)
            co = co.reshape(n, 3)
            c = np.asarray(_vec3(center, "center"), dtype=np.float64)
            if m == "sphere":
                r = _num(radius, "radius", 1e-6, 1e6)
                d = np.linalg.norm(co - c, axis=1)
                val = np.clip(1.0 - d / r, 0.0, 1.0)
            else:
                r = _num(radius, "radius", 1e-6, 1e6)
                val = np.clip(1.0 - np.abs(co - c).max(axis=1) / r, 0.0, 1.0)
            val = val * _num(value, "value", 0.0, 1.0)
            if invert:
                val = 1.0 - val
            att = me.attributes.get(".sculpt_mask") if hasattr(me, "attributes") else None
            if att is None:
                att = me.attributes.new(name=".sculpt_mask", type="FLOAT", domain="POINT")
            att.data.foreach_set("value", val.astype(np.float32))
            me.update()
            rec["coverage"] = round(float((val > 0.05).mean()), 4)
        else:
            # GUI 算子路径
            win = ar = reg = None
            for w in bpy.context.window_manager.windows:
                if w.screen is None:
                    continue
                for a in w.screen.areas:
                    if a.type == "VIEW_3D":
                        win, ar = w, a
                        reg = next((r for r in a.regions if r.type == "WINDOW"), None)
                        break
                if win:
                    break
            if not (win and ar and reg):
                return _j({"ok": False, "error": "%s 需要 GUI 3D 视口（无头 -b 不支持）" % m,
                           "hint": "无头里可用 mode=sphere/box 直接写 .sculpt_mask"})
            frm = ob.mode
            try:
                for o in bpy.context.selected_objects:
                    o.select_set(False)
                ob.select_set(True)
                bpy.context.view_layer.objects.active = ob
                if ob.mode != "SCULPT":
                    bpy.ops.object.mode_set(mode="SCULPT")
                with bpy.context.temp_override(window=win, screen=win.screen, area=ar, region=reg):
                    if m == "from_cavity":
                        rec["op"] = str(bpy.ops.sculpt.mask_from_cavity())
                    else:
                        rec["op"] = str(bpy.ops.sculpt.mask_init(mode="RANDOM_PER_VERTEX"))
            finally:
                try:
                    if ob.mode != frm:
                        bpy.ops.object.mode_set(mode=frm if frm else "OBJECT")
                except Exception:
                    pass
            mk = _mask_array(me)
            if mk is not None:
                rec["coverage"] = round(float((mk > 0.05).mean()), 4)
        out.append(rec)
    return _j({"ok": True, "objects": out,
               "note": "遮罩 = 后续 sculpt_apply 里 use_mask=true 时的乘子（masked 处不动）"})


def sculpt_remesh(objects=None, scope="ACTIVE", object=None, mode="VOXEL", voxel_size=None,
                  octree_depth=6, max_cells=MAX_VOXEL_CELLS):
    """重拓扑：VOXEL（体素，带预算守卫）或 SHARP/SMOOTH/BLOCKS（REMESH 修改器）。"""
    obs = _objs(objects or object, scope)
    m = str(mode or "VOXEL").upper()
    if m not in ("VOXEL", "SHARP", "SMOOTH", "BLOCKS"):
        raise ValueError("未知 mode：%s（VOXEL/SHARP/SMOOTH/BLOCKS）" % mode)
    mc = _num(max_cells, "max_cells", 1e3, 2e9, default=MAX_VOXEL_CELLS)
    out = []
    for ob in obs:
        t0 = time.time()
        rec = {"name": ob.name, "mode": m, "verts_before": len(ob.data.vertices), "tris_before": _tris(ob.data)}
        bpy.context.view_layer.objects.active = ob
        if m == "VOXEL":
            v, auto = _voxel_plan(ob, voxel_size, mc)
            ob.data.remesh_voxel_size = v
            rec["voxel_size"] = v
            rec["auto"] = bool(auto)
            rec["op"] = str(bpy.ops.object.voxel_remesh())
        else:
            md = ob.modifiers.new(name="Remesh", type="REMESH")
            md.mode = m
            md.octree_depth = int(_num(octree_depth, "octree_depth", 1, 12))
            rec["op"] = str(bpy.ops.object.modifier_apply(modifier=md.name))
        rec["verts_after"] = len(ob.data.vertices)
        rec["tris_after"] = _tris(ob.data)
        rec["ms"] = int((time.time() - t0) * 1000)
        out.append(rec)
    return _j({"ok": True, "objects": out,
               "note": "体素重构会丢 UV/材质槽权重（几何零损失但属性可能变）；改前 snapshot"})


def sculpt_selftest():
    """自检：临时球体 → 位移笔刷 → 体素重构 → 断言确实改了几何 → 删净。"""
    import numpy as np
    before = set(o.name for o in bpy.data.objects)
    me = bpy.data.meshes.new("__dsh_sculpt_selftest")
    ob = bpy.data.objects.new("__dsh_sculpt_selftest", me)
    bpy.context.scene.collection.objects.link(ob)
    ev = {}
    try:
        bm = bmesh.new()
        try:
            bmesh.ops.create_uvsphere(bm, u_segments=32, v_segments=16, radius=1.0)
        except TypeError:
            bmesh.ops.create_uvsphere(bm, u_segments=32, v_segments=16, diameter=1.0)
        bm.to_mesh(me)
        bm.free()
        ev["verts0"] = len(me.vertices)
        r = json.loads(sculpt_apply(object=ob.name, strokes=[{"brush": "draw", "points": [[0, 0, 1.0], [0.1, 0, 1.02]],
                                                              "radius": 0.4, "strength": 0.8}]))
        ev["apply_ok"] = r.get("ok")
        ev["affected"] = r["results"][0]["strokes"][0]["affected_verts"]
        ev["max_delta"] = r["results"][0]["strokes"][0]["max_delta"]
        r2 = json.loads(sculpt_remesh(object=ob.name, mode="VOXEL", voxel_size=0.08))
        ev["remesh_verts"] = r2["objects"][0]["verts_after"]
        r3 = json.loads(sculpt_mask(objects=[ob.name], mode="sphere", center=[0, 0, 1], radius=0.5))
        ev["mask_coverage"] = r3["objects"][0].get("coverage")
        ev["scan_ok"] = json.loads(sculpt_scan(objects=[ob.name]))["ok"]
        ok = bool(ev["apply_ok"] and ev["affected"] > 0 and ev["max_delta"] > 0 and ev["remesh_verts"] > 0
                  and ev["mask_coverage"] is not None)
        return _j({"ok": ok, "evidence": ev})
    except Exception as e:
        return _j({"ok": False, "evidence": ev, "error": "%s: %s" % (type(e).__name__, str(e)[:200])})
    finally:
        try:
            bpy.data.objects.remove(ob, do_unlink=True)
            bpy.data.meshes.remove(me, do_unlink=True)
        except Exception:
            pass
        left = [o.name for o in bpy.data.objects if o.name not in before]
        if left:
            ev["leftover"] = left


def sculpt_help():
    return _j({
        "module": "sculpt.py", "version": SCULPT_VERSION,
        "ops": {
            "sculpt_scan": "只读：顶点/三角/尺寸/模式/修改器/遮罩覆盖/对称/体素预算（列入只读白名单）",
            "sculpt_setup": "拓扑准备：mode=KEEP|VOXEL|MULTIRES|SUBDIV|DYNTOPO，可带 symmetry=['x']",
            "sculpt_apply": "位移笔刷：strokes=[{brush:draw|inflate|pinch|flatten|smooth|crease, points:[[x,y,z]...], "
                            "radius, strength, falloff:smooth|linear|sphere|constant|sharp, symmetry:['x'], invert, "
                            "direction:[x,y,z]}]，use_mask 默认 true",
            "sculpt_filter": "GUI 滤镜：type=SMOOTH|INFLATE|RELAX|...，strength 0..1，iterations（需 3D 视口）",
            "sculpt_mask": "遮罩：mode=sphere|box（无头可用，直接写 .sculpt_mask）/clear/from_cavity/init（GUI）",
            "sculpt_remesh": "重拓扑：mode=VOXEL（voxel_size=auto|数值，带 40M cell 预算守卫）/SHARP/SMOOTH/BLOCKS",
            "sculpt_selftest": "自检：临时球体上跑 apply+remesh+mask+scan 并断言几何确实变化",
            "sculpt_help": "本表",
        },
        "workflow": ["sculpt_scan 体检", "sculpt_setup(mode=VOXEL, voxel_size=auto) 定底模",
                     "sculpt_apply 若干笔（大体块 draw → 局部 crease/pinch）",
                     "sculpt_filter 平滑", "sculpt_mask 限定区域后再雕",
                     "audit_mesh/audit_drift 前后对比 + qc_render_views 出图验收"],
        "hard_rules": ["0 个 mesh ⇒ ok=false", "NaN/Inf 参数一律拒绝",
                       "体素重构先算预算，超限报错并给可用尺寸",
                       "雕刻改拓扑 ⇒ 对象级 mark/revert 回不去，要回退用 blender_rt_txn(op=\"snapshot\")"],
        "limits": {"brush_stroke": "Blender 5.2 上 Python 无法构造 stroke 集合（RNA 拒绝 dict；元素无法实例化）；"
                                   "上下文本身没问题（temp_override 后 poll()=True）",
                   "multires_sculpt": "位移写在基础网格上；multires 层上的雕刻需 GUI 手势，脚本暂不支持",
                   "headless": "sculpt_apply/remesh/mask(sphere|box) 无头可用；mesh_filter 与 from_cavity 需要 GUI"},
    })


def sculpt_dispatch(op, args_json):
    args = {}
    if isinstance(args_json, str) and args_json.strip():
        try:
            args = json.loads(args_json)
        except Exception as e:
            return _j({"ok": False, "error": "args 不是合法 JSON: %s" % str(e)[:120]})
    if not isinstance(args, dict):
        return _j({"ok": False, "error": "args 需要对象"})
    kw = {}
    for k, v in args.items():
        if k == "args" and isinstance(v, dict):
            kw.update(v)
        else:
            kw[k] = v
    ops = {"scan": sculpt_scan, "setup": sculpt_setup, "apply": sculpt_apply, "filter": sculpt_filter,
           "mask": sculpt_mask, "remesh": sculpt_remesh, "selftest": sculpt_selftest, "help": sculpt_help}
    fn = ops.get(str(op))
    if fn is None:
        return _j({"ok": False, "error": "unknown sculpt op", "op": op, "ops": sorted(ops)})
    import inspect
    try:
        allowed = set(inspect.signature(fn).parameters)
        unknown = sorted(set(kw) - allowed)
        if unknown:
            return _j({"ok": False, "error": "不认识的参数 %s" % unknown, "op": op,
                       "allowed": sorted(allowed), "hint": "参数名拼错会静默失效，所以这里直接报错"})
    except (TypeError, ValueError):
        pass
    try:
        return fn(**kw)
    except TypeError as e:
        return _j({"ok": False, "error": "参数不匹配: %s" % str(e)[:200], "op": op, "help": sculpt_help()})
    except Exception as e:
        return _j({"ok": False, "error": "%s: %s" % (type(e).__name__, str(e)[:220]), "op": op})


_DshApi = _KIT.Api  # 共享内核（尾部注册行无需改）
import sys as _sys
_K = _sys.modules.get("dsh_rt_kernel")
if _K is not None:
    _K.dsh_sculpt_api = _DshApi({"version": SCULPT_VERSION, "dispatch": sculpt_dispatch,
                                 "scan": sculpt_scan, "setup": sculpt_setup, "apply": sculpt_apply,
                                 "filter": sculpt_filter, "mask": sculpt_mask, "remesh": sculpt_remesh,
                                 "selftest": sculpt_selftest, "help": sculpt_help})
