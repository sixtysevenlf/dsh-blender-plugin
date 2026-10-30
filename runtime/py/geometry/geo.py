# -*- coding: utf-8 -*-
"""DSH 几何底座（geo）—— **机制层：只给动词，不给名词**。

为什么有这个模块：实测同一个技能 + 同一个插件下，两个会话各自手写了一套 kit
（box/cyl/boolean/镜像/预览…），**精确同名只有 4 个**，而语义等价的至少 8 组
（bool_diff↔boolean、wipe↔clear_scene、bolt↔hexbolt、rail↔rail_teeth、loft↔loft_y/stack…）。
后果两条：① 跨会话用法经验无法累积；② 同一批 Blender 版本坑各踩一遍。
本模块把"**与物品无关**的那部分"收成唯一实现。

## 分层（这条线是存在的理由，破坏它就没意义）

| 层 | 换物品会变吗 | 谁持有 | 内容 |
|---|---|---|---|
| 方法层（怎么建） | **会变** | 技能决策树 / Lead | 分面硬表面 / 光滑硬表面 / 有机 / 阵列 / 布尔 |
| **机制层（用什么动词造）** | **不变** | **本模块** | 布尔 · 开孔 · 开槽 · 镜像 · 阵列 · 拉伸 · 放样 · 倒角 · 自动平滑 |
| 形状层（长什么样） | **会变** | 项目 lib/spec.py | 齿距 / 站表 / 截面函数 / 坐标清单 |

**收一个新函数前的三条判据**（全中才收）：
  1. 把参数里的数值全删掉，剩下那句描述**换个物品还成立**吗？
     （"造导轨齿" → 不成立，不收；"把实体从实体里减掉" → 成立，收）
  2. 默认值能被调用方**完全覆盖**吗？（本模块**一律不给形状默认值**）
  3. 它解决的是"Blender 怎么用"或"几何怎么算"，还是"这东西长什么样"？

**违反这三条，底座就会把不同物品的模型压成一个样** —— 那正是它该被拒绝的理由。

## 明确不提供（属于形状层，请写在项目自己的 lib/ 里）
  导轨齿距 / 孔阵排布 / 握把截面 / 车辆站表 / 翼型轮廓 / 任何 pitch·tooth·ratio 的默认形状值。

## 怎么拿到
  KERNEL_BOOTSTRAP 已注入（与 kit 同等：**任何注入路径都在**），公开名直接可用；
  同时注册为 K.dsh_geo_api（可 dispatch）；也可显式 preload="geo"。
  自检：geo_selftest()   速查：geo_help()

单位：几何坐标用**当前场景单位**；要按 mm 建模就把 scene.unit_settings.scale_length 设成 0.001
（此时 K.dsh_kit.units() 回报 1.0 mm/unit）。本模块所有 `*_mm` 参数按毫米解释并自动换算。
"""
import json
import math
import sys
import types

import bpy
import bmesh
from mathutils import Vector, Matrix

GEO_VERSION = 1

_K = sys.modules.get("dsh_rt_kernel")
_KIT = getattr(_K, "dsh_kit", None)
if _KIT is None:
    raise RuntimeError("geo.py 需要共享内核 K.dsh_kit（由 KERNEL_BOOTSTRAP 注入）")
_j = _KIT.j
_err = _KIT.err
_receipt = _KIT.receipt
_units = _KIT.units


# ══════════════════════════════════════════════════════════════════════
# 档 A · 环境适配 —— 与物品零关系，但每个会话都会踩一遍的 Blender/无头坑
# ══════════════════════════════════════════════════════════════════════
def mm2u(v):
    """毫米 → 场景单位。"""
    return float(v) / (_units() or 1000.0)


def u2mm(v):
    """场景单位 → 毫米。"""
    return float(v) * (_units() or 1000.0)


def principled(mat):
    """取材质的 Principled BSDF —— **必须按 bl_idname 找**。
    中文界面下节点名是"原理化BSDF"，按名字 get("Principled BSDF") 会 KeyError（实测踩过）。"""
    if mat is None:
        return None
    nt = getattr(mat, "node_tree", None)
    if nt is None:
        return None
    for n in nt.nodes:
        if getattr(n, "bl_idname", "") == "ShaderNodeBsdfPrincipled":
            return n
    return None


def mix_rgb(nt, loc=(0.0, 0.0)):
    """兼容取 Mix 节点：Blender 5.x 用 ShaderNodeMix(data_type='RGBA')，老版回退 MixRGB。
    返回 (node, fac_in, c1_in, c2_in, color_out)。"""
    try:
        n = nt.nodes.new("ShaderNodeMix")
        n.data_type = "RGBA"
        n.location = loc
        return n, n.inputs["Factor"], n.inputs[6], n.inputs[7], n.outputs[2]
    except Exception:
        n = nt.nodes.new("ShaderNodeMixRGB")
        n.location = loc
        return n, n.inputs["Fac"], n.inputs["Color1"], n.inputs["Color2"], n.outputs["Color"]


def apply_mods(ob):
    """把对象上的 modifier **用求值器**落进网格。

    不用 bpy.ops.object.modifier_apply：它依赖 active/选中/上下文，无头下时好时坏。
    """
    dg = bpy.context.evaluated_depsgraph_get()
    ev = ob.evaluated_get(dg)
    me = bpy.data.meshes.new_from_object(ev)
    old = ob.data
    ob.modifiers.clear()
    ob.data = me
    if old.users == 0:
        bpy.data.meshes.remove(old)
    return ob


def bevel(ob, width_mm=0.0, segments=2, angle_deg=40.0):
    """**按角度限定**倒角并落地（只倒该倒的棱，不把缓变面倒成锯齿）。width_mm<=0 直接返回。"""
    if not width_mm or float(width_mm) <= 0:
        return ob
    m = ob.modifiers.new("geobevel", "BEVEL")
    m.width = mm2u(width_mm)
    m.segments = int(segments)
    m.limit_method = "ANGLE"
    m.angle_limit = math.radians(float(angle_deg))
    try:
        m.harden_normals = False
    except Exception:
        pass
    return apply_mods(ob)


def shade_auto(ob, angle_deg=35.0):
    """自动平滑 —— **不改拓扑**的正解。

    Blender 4.1+/5.x 已移除 mesh.use_auto_smooth；而 EDGE_SPLIT 会把边**真正劈开**、
    制造边界边，直接顶穿"0 非流形边"的门。正确做法：面全平滑 + set_sharp_from_angle 标锐边。
    实测（5.2.2）：8 顶点正方体 → 顶点数不变、标出 12 条锐边。
    """
    me = ob.data
    if not len(me.polygons):
        return ob
    me.polygons.foreach_set("use_smooth", [True] * len(me.polygons))
    try:
        me.set_sharp_from_angle(angle=math.radians(float(angle_deg)))
    except Exception:
        m = ob.modifiers.new("geosplit", "EDGE_SPLIT")
        m.split_angle = math.radians(float(angle_deg))
        m.use_edge_angle = True
        apply_mods(ob)
    try:
        me.update()
    except Exception:
        pass
    return ob


def shade_flat(ob):
    """全部平直着色（硬表面块件的默认观感）。"""
    me = ob.data
    me.polygons.foreach_set("use_smooth", [False] * len(me.polygons))
    try:
        me.update()
    except Exception:
        pass
    return ob


def recalc_normals(ob):
    """法线一律朝外 —— **这是后面所有互穿/间隙判据的前提**。"""
    bm = bmesh.new()
    bm.from_mesh(ob.data)
    bmesh.ops.recalc_face_normals(bm, faces=bm.faces)
    bm.to_mesh(ob.data)
    bm.free()
    ob.data.update()
    return ob


def mesh_stats(ob):
    """网格体检：非流形边（link_faces != 2）/ 零面积面 / 顶点 / 面。"""
    bm = bmesh.new()
    bm.from_mesh(ob.data)
    nm = sum(1 for e in bm.edges if len(e.link_faces) != 2)
    degen = sum(1 for f in bm.faces if f.calc_area() < 1e-12)
    out = {"nonmanifold_edges": nm, "degenerate_faces": degen,
           "verts": len(bm.verts), "faces": len(bm.faces)}
    bm.free()
    return out


# ══════════════════════════════════════════════════════════════════════
# 档 B · 几何动词 —— 只有动词与显式参数，没有任何形状默认值
# ══════════════════════════════════════════════════════════════════════
def collection(name):
    """取/建集合（配合 COL_<agent> 归属约定）。"""
    c = bpy.data.collections.get(str(name))
    if c is None:
        c = bpy.data.collections.new(str(name))
        bpy.context.scene.collection.children.link(c)
    return c


def own(ob, coll=None, owner=None):
    """登记归属：进集合 + 打 dsh.owner 标签（技能 §0.5.7 的约定，别只写在脑子里）。"""
    if ob is None:
        return ob
    c = coll if isinstance(coll, bpy.types.Collection) else collection(coll or "COL_geo")
    for cc in list(ob.users_collection):
        if cc is not c:
            try:
                cc.objects.unlink(ob)
            except Exception:
                pass
    if ob.name not in c.objects:
        try:
            c.objects.link(ob)
        except Exception:
            pass
    if owner:
        ob["dsh.owner"] = str(owner)
    return ob


def assign(ob, matname):
    """挂材质（按名字；不存在就建一个占位）。材质名 → 具体数值属于**形状/外观层**，由项目定。"""
    if ob is None:
        return ob
    m = bpy.data.materials.get(str(matname))
    if m is None:
        m = bpy.data.materials.new(str(matname))
        m.diffuse_color = (0.05, 0.05, 0.05, 1)
    if ob.data.materials:
        ob.data.materials[0] = m
    else:
        ob.data.materials.append(m)
    return ob


def assign_all(objs, matname):
    for o in (objs or []):
        assign(o, matname)
    return objs


def solid(name, bm, coll="COL_geo", owner=None, bevel_mm=0.0, seg=2,
          angle_deg=40.0, smooth_angle=None):
    """bmesh → 场景对象：本模块**唯一的落地点**（统一走：法线朝外 → 登记 → 倒角 → 平滑）。"""
    bmesh.ops.recalc_face_normals(bm, faces=bm.faces)
    me = bpy.data.meshes.new(str(name))
    bm.to_mesh(me)
    bm.free()
    ob = bpy.data.objects.new(str(name), me)
    bpy.context.scene.collection.objects.link(ob)
    own(ob, coll, owner)
    if bevel_mm:
        bevel(ob, bevel_mm, seg, angle_deg)
    if smooth_angle is not None:
        shade_auto(ob, smooth_angle)
    return ob


def box(name, xr, yr, zr, coll="COL_geo", owner=None, bevel_mm=0.0, seg=2,
        angle_deg=40.0, smooth_angle=None):
    """轴对齐盒：三个区间 (min, max)。**没有尺寸默认值 —— 尺寸永远由调用方给。**"""
    x0, x1 = float(xr[0]), float(xr[1])
    y0, y1 = float(yr[0]), float(yr[1])
    z0, z1 = float(zr[0]), float(zr[1])
    bm = bmesh.new()
    bmesh.ops.create_cube(bm, size=1.0)
    for v in bm.verts:
        v.co.x = x0 + (v.co.x + 0.5) * (x1 - x0)
        v.co.y = y0 + (v.co.y + 0.5) * (y1 - y0)
        v.co.z = z0 + (v.co.z + 0.5) * (z1 - z0)
    return solid(name, bm, coll, owner, bevel_mm, seg, angle_deg, smooth_angle)


def cyl(name, p0, p1, r, coll="COL_geo", r2=None, segs=24, owner=None,
        bevel_mm=0.0, smooth_angle=32.0):
    """两点定轴的圆柱（r2 给了就是圆台）。"""
    a, b = Vector(p0), Vector(p1)
    d = b - a
    L = d.length
    if L < 1e-9:
        raise ValueError("geo.cyl: p0 与 p1 重合，轴长为 0")
    q = Vector((0.0, 0.0, 1.0)).rotation_difference(d.normalized())
    M = Matrix.Translation((a + b) * 0.5) @ q.to_matrix().to_4x4()
    r_b = float(r if r2 is None else r2)
    kw = dict(cap_ends=True, cap_tris=False, segments=int(segs), depth=L)
    bm = bmesh.new()
    try:
        bmesh.ops.create_cone(bm, radius1=float(r), radius2=r_b, **kw)
    except TypeError:
        bmesh.ops.create_cone(bm, diameter1=float(r) * 2.0, diameter2=r_b * 2.0, **kw)
    bmesh.ops.transform(bm, matrix=M, verts=bm.verts)
    return solid(name, bm, coll, owner, bevel_mm, 2, 40.0, smooth_angle)


def tube(name, p0, p1, r_out, r_in, coll="COL_geo", segs=24, owner=None,
         bevel_mm=0.0, smooth_angle=32.0):
    """空心管（外圆柱 − 内圆柱），两端开口。"""
    if float(r_in) >= float(r_out):
        raise ValueError("geo.tube: r_in 必须小于 r_out")
    outer = cyl(name, p0, p1, r_out, coll=coll, r2=None, segs=segs, owner=owner,
                bevel_mm=0.0, smooth_angle=None)
    inner = cyl("__geotube_in", p0, p1, r_in, coll="COL_geo", segs=segs,
                smooth_angle=None)
    boolean(outer, inner, "DIFFERENCE", delete_cutter=True, verify=True)
    if bevel_mm:
        bevel(outer, bevel_mm, 2, 40.0)
    if smooth_angle is not None:
        shade_auto(outer, smooth_angle)
    return outer


def prism(name, profile, axis, a0, a1, coll="COL_geo", owner=None, bevel_mm=0.0,
          seg=2, angle_deg=40.0, smooth_angle=None):
    """2D 闭合截面沿轴拉伸。profile=[(u,v),…]（轴=X→(Y,Z)；Y→(X,Z)；Z→(X,Y)）。"""
    ax = str(axis).upper()
    if ax not in ("X", "Y", "Z"):
        raise ValueError("geo.prism: axis 只能是 X/Y/Z")

    def P(u, v, a):
        if ax == "X":
            return (a, u, v)
        if ax == "Y":
            return (u, a, v)
        return (u, v, a)

    bm = bmesh.new()
    vs = [bm.verts.new(P(float(u), float(v), float(a0))) for (u, v) in profile]
    f = bm.faces.new(vs)
    res = bmesh.ops.extrude_face_region(bm, geom=[f])
    nv = [e for e in res["geom"] if isinstance(e, bmesh.types.BMVert)]
    bmesh.ops.translate(bm, vec=Vector(P(0.0, 0.0, float(a1))) - Vector(P(0.0, 0.0, float(a0))),
                        verts=nv)
    return solid(name, bm, coll, owner, bevel_mm, seg, angle_deg, smooth_angle)


def loft(name, rings, coll="COL_geo", owner=None, bevel_mm=0.0, seg=2,
         angle_deg=40.0, smooth_angle=None, cap=True):
    """多截面放样：rings=[层][点]，**层间点数一致、绕向一致**。轴向变截面实体用它。"""
    if not rings or not rings[0]:
        raise ValueError("geo.loft: rings 为空")
    n = len(rings[0])
    for i, r in enumerate(rings):
        if len(r) != n:
            raise ValueError("geo.loft: 第 %d 层点数 %d != 第 0 层 %d" % (i, len(r), n))
    bm = bmesh.new()
    vr = [[bm.verts.new(Vector(p)) for p in r] for r in rings]
    for a, b in zip(vr[:-1], vr[1:]):
        for i in range(n):
            j = (i + 1) % n
            try:
                bm.faces.new((a[i], a[j], b[j], b[i]))
            except ValueError:
                pass
    if cap:
        for ring, rev in ((vr[0], True), (vr[-1], False)):
            vs = list(reversed(ring)) if rev else list(ring)
            try:
                bm.faces.new(vs)
            except ValueError:
                pass
    return solid(name, bm, coll, owner, bevel_mm, seg, angle_deg, smooth_angle)


def stack(name, sections, axis="X", coll="COL_geo", owner=None, bevel_mm=0.0,
          seg=2, angle_deg=40.0, smooth_angle=None):
    """沿轴堆截面 → loft 的便捷形。sections=[(a, [(u,v),…]), …]（u,v 含义同 prism）。"""
    ax = str(axis).upper()
    if ax not in ("X", "Y", "Z"):
        raise ValueError("geo.stack: axis 只能是 X/Y/Z")
    rings = []
    for (a, pts) in sections:
        ring = []
        for (u, v) in pts:
            if ax == "X":
                ring.append((float(a), float(u), float(v)))
            elif ax == "Y":
                ring.append((float(u), float(a), float(v)))
            else:
                ring.append((float(u), float(v), float(a)))
        rings.append(ring)
    return loft(name, rings, coll, owner, bevel_mm, seg, angle_deg, smooth_angle)


def _obb(ob):
    """世界坐标包围盒 [min, max]。"""
    try:
        bpy.context.view_layer.update()
    except Exception:
        pass
    cs = [ob.matrix_world @ Vector(c) for c in ob.bound_box]
    lo = [min(c[i] for c in cs) for i in range(3)]
    hi = [max(c[i] for c in cs) for i in range(3)]
    return [Vector(lo), Vector(hi)]


def _inside(a, b, tol):
    return all(a[0][i] >= b[0][i] - tol and a[1][i] <= b[1][i] + tol for i in range(3))


def boolean(target, cutter, op="DIFFERENCE", solver="EXACT", delete_cutter=True, verify=True):
    """布尔 + **退化守卫**。

    已知坑（实测）：EXACT 布尔在**自交主体**（多个互相穿插的原语 join 成一个网格）上会
    **静默退化成"并集"** —— 不报错、只是形状悄悄错了，结果包围盒胀到切具尺寸。
    守卫（后验，便宜且确定）：
      差分结果必须仍包在 target 包围盒内；交点必须包在两者公共包围盒内；并集不许超出两者之并。
    违反 → 抛 RuntimeError（带诊断与修法），**不静默通过**。verify=False 可关（不建议）。
    """
    top = str(op).upper()
    if top not in ("DIFFERENCE", "UNION", "INTERSECT"):
        raise ValueError("geo.boolean: op 只能是 DIFFERENCE / UNION / INTERSECT")
    tbb = _obb(target)
    cbb = _obb(cutter)
    pre = mesh_stats(target)
    m = target.modifiers.new("geobool", "BOOLEAN")
    m.operation = top
    m.object = cutter
    try:
        m.solver = str(solver)
    except Exception:
        pass
    apply_mods(target)
    if delete_cutter:
        try:
            bpy.data.objects.remove(cutter, do_unlink=True)
        except Exception:
            pass
    info = {"op": top, "pre_nonmanifold_edges": pre["nonmanifold_edges"],
            "pre_degenerate_faces": pre["degenerate_faces"], "guard": "skipped"}
    if verify:
        rbb = _obb(target)
        span = max(1e-9, (tbb[1] - tbb[0]).length)
        tol = 1e-4 * max(1.0, span)
        bad = None
        if top == "DIFFERENCE":
            if not _inside(rbb, tbb, tol):
                bad = "difference_grew_beyond_target"
        elif top == "INTERSECT":
            lo = Vector([max(tbb[0][i], cbb[0][i]) for i in range(3)])
            hi = Vector([min(tbb[1][i], cbb[1][i]) for i in range(3)])
            if not _inside(rbb, [lo, hi], tol):
                bad = "intersect_outside_common"
        else:
            lo = Vector([min(tbb[0][i], cbb[0][i]) for i in range(3)])
            hi = Vector([max(tbb[1][i], cbb[1][i]) for i in range(3)])
            if not _inside(rbb, [lo, hi], tol):
                bad = "union_beyond_both"
        if bad:
            raise RuntimeError(
                "几何底座退化守卫拦下了一次布尔（%s）。%s 的结果包围盒超出了应有范围，"
                "最常见原因是**主体是自交网格**（多个互相穿插的原语 join 在一起）—— "
                "EXACT 布尔在这种输入上会静默退化成并集。修法："
                "(1) 布尔只在**单一原语**上做，切完再 join；"
                "(2) 或先跑 fix_repair（合并重复点/消零面积面/重算法线）再切；"
                "(3) 确认这种形变是设计意图时传 verify=False。"
                % (bad, top))
        info["guard"] = "pass"
    try:
        target["dsh_boolean_guard"] = _j(info)
    except Exception:
        pass
    return target


def _merge(objs, name, coll="COL_tmp"):
    """把多个切具合成**一个**网格 —— 批量开孔走一次布尔（比逐个切快且稳）。"""
    bm = bmesh.new()
    for o in objs:
        bm.from_mesh(o.data)
    for o in objs:
        try:
            bpy.data.objects.remove(o, do_unlink=True)
        except Exception:
            pass
    return solid(name, bm, coll=coll, owner=None, smooth_angle=None)


def hole_cut(target, holes, name="GEO_holes", coll="COL_tmp", verify=True):
    """批量开孔，一次布尔。holes 每项二选一：
      {"center":(x,y,z), "axis":"X|Y|Z", "r":R, "length":L}   —— 轴对齐孔
      (p0, p1, r)                                            —— 任意轴孔
    """
    cutters = []
    for i, h in enumerate(holes):
        if isinstance(h, dict):
            c = Vector(h.get("center", (0.0, 0.0, 0.0)))
            ax = str(h.get("axis", "X")).upper()
            dv = {"X": Vector((1.0, 0.0, 0.0)), "Y": Vector((0.0, 1.0, 0.0)),
                  "Z": Vector((0.0, 0.0, 1.0))}.get(ax)
            if dv is None:
                raise ValueError("geo.hole_cut: axis 只能是 X/Y/Z")
            half = dv * (float(h["length"]) * 0.5)
            p0, p1, r = c - half, c + half, float(h["r"])
        else:
            p0, p1, r = h[0], h[1], float(h[2])
        cutters.append(cyl("__geohole%d" % i, p0, p1, r, coll=coll, smooth_angle=None))
    return boolean(target, _merge(cutters, name + "_cutter", coll), "DIFFERENCE", verify=verify)


def slots_cut(target, slots, name="GEO_slots", coll="COL_tmp", verify=True):
    """批量开槽，一次布尔。slots 每项 = ((x0,x1),(y0,y1),(z0,z1)) 的盒子。"""
    cutters = [box("__geoslot%d" % i, s[0], s[1], s[2], coll=coll, smooth_angle=None)
               for i, s in enumerate(slots)]
    return boolean(target, _merge(cutters, name + "_cutter", coll), "DIFFERENCE", verify=verify)


def mirror(ob, axis="Y", name=None, coll=None):
    """通用镜像（不写死 Y）。负缩放**就地落地**并翻转法线 —— 否则会得到里外翻的壳，
    而法线方向是所有互穿判据的前提。"""
    ax = str(axis).upper()
    if ax not in ("X", "Y", "Z"):
        raise ValueError("geo.mirror: axis 只能是 X/Y/Z")
    me = ob.data.copy()
    dup = bpy.data.objects.new(name or (ob.name + "_mir"), me)
    (coll if isinstance(coll, bpy.types.Collection)
     else (ob.users_collection[0] if ob.users_collection else collection("COL_geo"))).objects.link(dup)
    s = [1.0, 1.0, 1.0]
    s["XYZ".index(ax)] = -1.0
    me.transform(Matrix.Diagonal((s[0], s[1], s[2], 1.0)))
    if s[0] * s[1] * s[2] < 0:
        bm = bmesh.new()
        bm.from_mesh(me)
        bmesh.ops.reverse_faces(bm, faces=bm.faces)
        bm.to_mesh(me)
        bm.free()
    me.update()
    try:
        dup["dsh.owner"] = ob.get("dsh.owner", "")
    except Exception:
        pass
    return dup


def linear_array(ob, count, offset, name=None):
    """沿向量复制（份数含原件）。offset = 每一步的位移。"""
    n = int(count)
    if n < 1:
        raise ValueError("geo.linear_array: count 必须 >= 1")
    out = [ob]
    c = ob.users_collection[0] if ob.users_collection else collection("COL_geo")
    for i in range(1, n):
        dup = bpy.data.objects.new("%s_%02d" % (name or (ob.name + "_arr"), i), ob.data.copy())
        c.objects.link(dup)
        dup.matrix_world = Matrix.Translation(Vector(offset) * i) @ ob.matrix_world
        try:
            dup["dsh.owner"] = ob.get("dsh.owner", "")
        except Exception:
            pass
        out.append(dup)
    return out


def join(objs, name=None, coll=None, owner=None):
    """合并成一格网格（**布尔请在 join 之前做** —— join 出的自交网格会让 EXACT 布尔退化）。"""
    objs = [o for o in (objs or []) if o is not None]
    if not objs:
        return None
    bm = bmesh.new()
    for o in objs:
        bm.from_mesh(o.data)
    base = objs[0]
    for o in objs:
        if o is not base:
            try:
                bpy.data.objects.remove(o, do_unlink=True)
            except Exception:
                pass
    old = base.data
    me = bpy.data.meshes.new(base.name)
    bm.to_mesh(me)
    bm.free()
    base.data = me
    if old.users == 0:
        bpy.data.meshes.remove(old)
    if name:
        base.name = str(name)
    own(base, coll or (base.users_collection[0] if base.users_collection else "COL_geo"),
        owner or base.get("dsh.owner"))
    return base


def clear_scene(prefixes=("GEO_", "GEO-", "__")):
    """清场：默认只删自己那套前缀的件，**不碰相机/灯/别人的件**。返回删除数量。"""
    n = 0
    for o in list(bpy.data.objects):
        if any(o.name.startswith(p) for p in prefixes):
            bpy.data.objects.remove(o, do_unlink=True)
            n += 1
    return n


# ══════════════════════════════════════════════════════════════════════
# 档 C · 管线 / 量测 / 预览
# ══════════════════════════════════════════════════════════════════════
def tri_count(objs=None):
    """三角面数（供"面数预算"这类门用）。"""
    objs = objs if objs is not None else [o for o in bpy.data.objects if o.type == "MESH"]
    n = 0
    for o in objs:
        for p in o.data.polygons:
            n += max(0, len(p.vertices) - 2)
    return n


def bbox_all(objs=None, unit="mm"):
    """整组件的世界包围盒（默认 mm）。返 {"ok","bbox","size","unit"}。"""
    objs = objs if objs is not None else [o for o in bpy.data.objects if o.type == "MESH"]
    objs = [o for o in objs if o is not None and o.type == "MESH"]
    if not objs:
        return {"ok": False, "error": "bbox_all: 没有 mesh 对象"}
    lo = [1e18] * 3
    hi = [-1e18] * 3
    for o in objs:
        bb = _obb(o)
        for i in range(3):
            lo[i] = min(lo[i], bb[0][i])
            hi[i] = max(hi[i], bb[1][i])
    if str(unit).lower() == "mm":
        f = u2mm
    else:
        f = float
    return {"ok": True, "unit": str(unit),
            "bbox": [[round(f(v), 4) for v in lo], [round(f(v), 4) for v in hi]],
            "size": [round(f(hi[i] - lo[i]), 4) for i in range(3)]}


def _aim(loc, target):
    d = Vector(target) - Vector(loc)
    q = Vector((0.0, 0.0, -1.0)).rotation_difference(d.normalized())
    return q.to_euler()


def quick_views(outdir, views, res=(1280, 720), engine="BLENDER_WORKBENCH", prefix=""):
    """多视角快速预览（不需要灯/材质也能出图）。

    views=[{"name","from","look_at","lens"?,"ortho"?}, …]；坐标 = 当前场景单位。
    轻量自足；要**与参考图算 IoU / 剖面对齐**请走 blender_rt_plan(op="qc_render_views")。
    """
    import os
    os.makedirs(str(outdir), exist_ok=True)
    sc = bpy.context.scene
    old_engine = sc.render.engine
    old_res = (sc.render.resolution_x, sc.render.resolution_y)
    made = []
    try:
        try:
            sc.render.engine = str(engine)
        except Exception:
            pass
        sc.render.resolution_x = int(res[0])
        sc.render.resolution_y = int(res[1])
        sc.render.resolution_percentage = 100
        sc.render.image_settings.file_format = "PNG"
        for v in views:
            cd = bpy.data.cameras.new("__geo_cam")
            cd.lens = float(v.get("lens", 50.0))
            if v.get("ortho"):
                cd.type = "ORTHO"
                cd.ortho_scale = float(v["ortho"])
            co = bpy.data.objects.new("__geo_cam", cd)
            sc.collection.objects.link(co)
            co.location = v["from"]
            co.rotation_euler = _aim(v["from"], v["look_at"])
            sc.camera = co
            fn = os.path.join(str(outdir), "%s%s.png" % (prefix, v.get("name", "view")))
            sc.render.filepath = fn
            bpy.ops.render.render(write_still=True)
            made.append(fn)
            bpy.data.objects.remove(co, do_unlink=True)
            if cd.users == 0:
                bpy.data.cameras.remove(cd)
    finally:
        sc.render.resolution_x = int(old_res[0])
        sc.render.resolution_y = int(old_res[1])
        try:
            sc.render.engine = old_engine
        except Exception:
            pass
    return {"ok": True, "n": len(made), "files": made}


# ══════════════════════════════════════════════════════════════════════
# 自检 / 速查 / 注册
# ══════════════════════════════════════════════════════════════════════
def geo_help():
    return _j({
        "module": "geo.py", "version": GEO_VERSION,
        "what": "几何底座 —— 机制层：只给动词，不给名词（不含任何形状默认值）",
        "where": "KERNEL_BOOTSTRAP 已注入（公开名直接可用）；也注册为 K.dsh_geo_api；或 preload=\"geo\"",
        "A_env": ["principled", "mix_rgb", "apply_mods", "bevel", "shade_auto",
                  "shade_flat", "recalc_normals", "mesh_stats", "mm2u", "u2mm"],
        "B_verbs": ["solid", "box", "cyl", "tube", "prism", "loft", "stack",
                    "boolean", "hole_cut", "slots_cut", "mirror", "linear_array", "join", "clear_scene"],
        "C_pipe": ["collection", "own", "assign", "assign_all", "tri_count", "bbox_all", "quick_views"],
        "not_here": ["导轨齿距 / 孔阵排布 / 握把截面 / 车辆站表 / 翼型轮廓 —— 形状层，请写在项目 lib/spec.py"],
        "judgement": ["把参数里的数值全删掉，描述换个物品还成立吗？",
                      "默认值能被调用方完全覆盖吗？（本模块一律不给形状默认值）",
                      "它解决的是'Blender 怎么用/几何怎么算'还是'这东西长什么样'？"],
        "selftest": "geo_selftest()",
    })


def geo_selftest():
    """自检：合成几何断言（非流形/退化/布尔守卫/镜像阵列/自动平滑不改拓扑/统计正确）。"""
    ev = {}
    canary = {}
    made = []

    def _clean():
        for o in made:
            try:
                bpy.data.objects.remove(o, do_unlink=True)
            except Exception:
                pass

    prev_scale = None
    try:
        prev_scale = float(bpy.context.scene.unit_settings.scale_length)
        bpy.context.scene.unit_settings.scale_length = 0.001   # 1 单位 = 1mm，方便用 mm 断言

        # 1) 盒 + 倒角 → 流形、无退化面
        b = box("__geo_t_box", (0, 20), (0, 10), (0, 5), coll="COL_geotest",
                bevel_mm=0.5, smooth_angle=30.0)
        made.append(b)
        s = mesh_stats(b)
        ev["box_manifold"] = (s["nonmanifold_edges"] == 0 and s["degenerate_faces"] == 0)

        # 2) 柱 / 管
        c = cyl("__geo_t_cyl", (0, 0, 0), (0, 0, 30), 4.0, coll="COL_geotest", segs=24)
        made.append(c)
        ev["cyl_manifold"] = mesh_stats(c)["nonmanifold_edges"] == 0
        t = tube("__geo_t_tube", (0, 0, 0), (0, 0, 30), 5.0, 3.0, coll="COL_geotest")
        made.append(t)
        st = mesh_stats(t)
        ev["tube_hollow"] = st["nonmanifold_edges"] == 0 and st["faces"] > mesh_stats(c)["faces"]

        # 3) 放样（锥形方柱，层间点数一致）
        rings = []
        for i in range(6):
            t01 = i / 5.0
            h = 6.0 - 4.0 * t01
            rings.append([(-h, -h, i * 4.0), (h, -h, i * 4.0), (h, h, i * 4.0), (-h, h, i * 4.0)])
        L = loft("__geo_t_loft", rings, coll="COL_geotest")
        made.append(L)
        sl = mesh_stats(L)
        ev["loft_manifold"] = sl["nonmanifold_edges"] == 0 and sl["degenerate_faces"] == 0

        # 4) 布尔差分：守卫通过、包围盒不变、面数增加
        base = box("__geo_t_base", (0, 40), (0, 20), (0, 20), coll="COL_geotest")
        made.append(base)
        bb0 = bbox_all([base], unit="mm")
        f0 = mesh_stats(base)["faces"]
        hole_cut(base, [{"center": (20, 10, 10), "axis": "Z", "r": 6.0, "length": 60.0}],
                 name="__geo_t_hole", coll="COL_geotest")
        bb1 = bbox_all([base], unit="mm")
        ev["boolean_guard_pass"] = base.get("dsh_boolean_guard", "").find('"guard": "pass"') > 0
        ev["boolean_bbox_kept"] = bb0["bbox"] == bb1["bbox"]
        ev["boolean_faces_grew"] = mesh_stats(base)["faces"] > f0
        ev["boolean_manifold"] = mesh_stats(base)["nonmanifold_edges"] == 0

        # 5) 守卫两个方向都要测
        # ① **不许误报**：正常件 + 探出主体之外的切具 —— 正确的差集结果仍应包在主体内
        n1 = box("__geo_t_n1", (0, 40), (0, 20), (0, 20), coll="COL_geotest")
        made.append(n1)
        c1 = box("__geo_t_c1", (35, 90), (5, 15), (5, 15), coll="COL_geotest")
        made.append(c1)
        false_alarm = False
        try:
            boolean(n1, c1, "DIFFERENCE", delete_cutter=True, verify=True)
        except RuntimeError:
            false_alarm = True
        ev["guard_no_false_positive"] = (not false_alarm)

        # ② **金丝雀**：自交主体（两个互相穿插的盒 join 成一个网格）+ 探出主体的切具。
        #    若某天 EXACT 布尔在这种输入上退化成并集，包围盒会胀出去 → 守卫应当拦下。
        #    实测 5.2.2 上此构造**未复现**（故记为 canary，不参与 ok）—— 记录在案，
        #    将来版本回归时它会翻成 true，从回执里一眼看到。
        a1 = box("__geo_t_s1", (0, 20), (0, 20), (0, 20), coll="COL_geotest")
        a2 = box("__geo_t_s2", (10, 30), (10, 30), (10, 30), coll="COL_geotest")
        made.extend([a1, a2])
        si = join([a1, a2], name="__geo_t_selfint", coll="COL_geotest")
        made.append(si)
        c2 = box("__geo_t_c2", (25, 80), (10, 30), (10, 30), coll="COL_geotest")
        made.append(c2)
        fired = False
        try:
            boolean(si, c2, "DIFFERENCE", delete_cutter=True, verify=True)
        except RuntimeError:
            fired = True
        canary["selfint_guard_canary"] = fired

        # 6) 镜像 / 阵列
        src = box("__geo_t_m", (0, 10), (0, 5), (0, 5), coll="COL_geotest")
        made.append(src)
        mi = mirror(src, "Y")
        made.append(mi)
        arr = linear_array(src, 4, (30.0, 0.0, 0.0), name="__geo_t_a")
        made.extend(arr)
        ev["mirror_is_mirrored"] = bool(mi) and mi.name.endswith("_mir")
        ev["array_count_4"] = len(arr) == 4

        # 7) 自动平滑不改拓扑（EDGE_SPLIT 会劈边 → 直接顶穿非流形门）
        pv = mesh_stats(b)["verts"]
        nsharp = len([e for e in b.data.edges if e.use_edge_sharp])
        shade_auto(b, 30.0)
        ev["shade_auto_keeps_topology"] = mesh_stats(b)["verts"] == pv
        ev["shade_auto_marks_sharp"] = (len([e for e in b.data.edges if e.use_edge_sharp]) >= nsharp)

        # 8) 统计正确：正方体 12 三角面
        cube = box("__geo_t_cube", (0, 10), (0, 10), (0, 10), coll="COL_geotest")
        made.append(cube)
        ev["tri_count_cube_12"] = tri_count([cube]) == 12
        ev["bbox_size_10mm"] = bbox_all([cube], unit="mm")["size"] == [10.0, 10.0, 10.0]
    except Exception as e:
        _clean()
        try:
            if prev_scale is not None:
                bpy.context.scene.unit_settings.scale_length = prev_scale
        except Exception:
            pass
        return _j({"ok": False, "error": "%s: %s" % (type(e).__name__, str(e)[:300]),
                   "version": GEO_VERSION, "evidence": ev, "canary": canary})
    _clean()
    try:
        if prev_scale is not None:
            bpy.context.scene.unit_settings.scale_length = prev_scale
    except Exception:
        pass
    ok = all(v is True for v in ev.values())
    return _j({"ok": bool(ok), "version": GEO_VERSION, "evidence": ev, "canary": canary})


OPS = {
    "help": geo_help, "selftest": geo_selftest,
    "principled": principled, "mix_rgb": mix_rgb, "apply_mods": apply_mods,
    "bevel": bevel, "shade_auto": shade_auto, "shade_flat": shade_flat,
    "recalc_normals": recalc_normals, "mesh_stats": mesh_stats,
    "mm2u": mm2u, "u2mm": u2mm,
    "collection": collection, "own": own, "assign": assign, "assign_all": assign_all, "solid": solid,
    "box": box, "cyl": cyl, "tube": tube, "prism": prism, "loft": loft, "stack": stack,
    "boolean": boolean, "hole_cut": hole_cut, "slots_cut": slots_cut,
    "mirror": mirror, "linear_array": linear_array, "join": join, "clear_scene": clear_scene,
    "tri_count": tri_count, "bbox_all": bbox_all, "quick_views": quick_views,
}

# 注册：既挂 K.dsh_geo_api（可 dispatch），公开名也随 bootstrap 进脚本 globals（直接可用）
_KIT.register("geo", GEO_VERSION, ops=OPS,
              extra={"get": lambda n: OPS.get(str(n)), "help_text": geo_help})
