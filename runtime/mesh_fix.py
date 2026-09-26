# -*- coding: utf-8 -*-
"""DSH 网格修复闭环（v0.9.6 · 上游整合 A2）—— audit_mesh 只诊断，这里负责治。

现状：audit_mesh 报 boundary_edges / nonmanifold_edges / degenerate_faces / loose_verts /
self_intersections / normals_inverted，但**纯只读**。上游 blend-ai 有 repair_mesh / decimate_mesh，
本模块按同一接口形状与判据自实现（不复制其 AGPL 代码，只借"修完要能自证"的思路）。

ops：
    blender_rt_plan(op="fix_repair",   args={objects:["P01"], actions:["merge_doubles","dissolve_degenerate","delete_loose","recalc_normals","fill_holes"]})
    blender_rt_plan(op="fix_decimate", args={objects:["P01"], target_tris:2000})
    blender_rt_plan(op="fix_selftest")

**硬规则**：0 个 mesh ⇒ ok=false；actions 为空 ⇒ ok=false；修复是破坏性操作 ⇒ 回执必须带
"前后缺陷计数"，且只有 nonmanifold_edges + degenerate_faces 明显下降才算 ok=true（否则 ok=false +
说明为什么没修动）。改前要回退点请用 blender_rt_txn(op="snapshot")（对象级 mark/revert 不含拓扑）。
"""
import json
import math

import bpy
import bmesh

FIX_VERSION = 1
ACTIONS = ("merge_doubles", "dissolve_degenerate", "delete_loose", "recalc_normals", "fill_holes")
EPS_AREA = 1e-9


def _j(o):
    return json.dumps(o, ensure_ascii=False, default=str)


def _num(v, name, lo=None, hi=None, default=None):
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


def _objs(objects=None, scope="ACTIVE"):
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


def _defects_bm(bm, eps_area=EPS_AREA):
    """与 audit_mesh 同口径的缺陷计数（bmesh 版，修复前后各测一次）。"""
    return {
        "verts": len(bm.verts), "edges": len(bm.edges), "faces": len(bm.faces),
        "boundary_edges": sum(1 for e in bm.edges if len(e.link_faces) == 1),
        "nonmanifold_edges": sum(1 for e in bm.edges if not e.is_manifold),
        "degenerate_faces": sum(1 for f in bm.faces if f.calc_area() <= float(eps_area)),
        "loose_verts": sum(1 for v in bm.verts if not v.link_edges),
        "loose_edges": sum(1 for e in bm.edges if not e.link_faces),
    }


def _defects_me(me, eps_area=EPS_AREA):
    bm = bmesh.new()
    bm.from_mesh(me)
    d = _defects_bm(bm, eps_area)
    bm.free()
    return d


def fix_repair(objects=None, scope="ACTIVE", actions=None, merge_threshold=1e-4, eps_area=EPS_AREA,
               max_hole_edges=64, preserve_volume=False):
    """按 audit_mesh 的缺陷清单施治，并给出前后对比（没修动就 ok=false）。"""
    acts = list(actions) if actions else ["merge_doubles", "dissolve_degenerate", "delete_loose", "recalc_normals"]
    bad = sorted(set(acts) - set(ACTIONS))
    if bad:
        raise ValueError("未知 actions %s（允许 %s）" % (bad, list(ACTIONS)))
    if not acts:
        raise ValueError("actions 不能为空（要修什么就写什么）")
    thr = _num(merge_threshold, "merge_threshold", 1e-9, 1.0)
    eps = _num(eps_area, "eps_area", 0.0, 1.0, EPS_AREA)
    mhe = int(_num(max_hole_edges, "max_hole_edges", 3, 100000))
    obs = _objs(objects, scope)
    out = []
    for ob in obs:
        me = ob.data
        if len(me.vertices) == 0:
            out.append({"name": ob.name, "ok": False, "error": "0 顶点"})
            continue
        bm = bmesh.new()
        bm.from_mesh(me)
        before = _defects_bm(bm, eps)
        steps = []
        try:
            for a in acts:
                if a == "merge_doubles":
                    n0 = len(bm.verts)
                    bmesh.ops.remove_doubles(bm, verts=list(bm.verts), dist=thr)
                    steps.append({"action": a, "verts_before": n0, "verts_after": len(bm.verts)})
                elif a == "dissolve_degenerate":
                    n0 = len(bm.faces)
                    bmesh.ops.dissolve_degenerate(bm, dist=thr, edges=list(bm.edges))
                    steps.append({"action": a, "faces_before": n0, "faces_after": len(bm.faces)})
                elif a == "delete_loose":
                    el = [e for e in bm.edges if not e.link_faces]
                    if el:
                        bmesh.ops.delete(bm, geom=el, context="EDGES")
                    vl = [v for v in bm.verts if not v.link_edges]
                    if vl:
                        bmesh.ops.delete(bm, geom=vl, context="VERTS")
                    steps.append({"action": a, "removed_edges": len(el), "removed_verts": len(vl)})
                elif a == "recalc_normals":
                    bmesh.ops.recalc_face_normals(bm, faces=list(bm.faces))
                    steps.append({"action": a})
                elif a == "fill_holes":
                    bd = [e for e in bm.edges if len(e.link_faces) == 1]
                    if bd:
                        bmesh.ops.holes_fill(bm, edges=bd, sides=mhe)
                    steps.append({"action": a, "boundary_edges": len(bd)})
            after = _defects_bm(bm, eps)
            bm.to_mesh(me)
            me.update()
        finally:
            bm.free()
        d_nm = before["nonmanifold_edges"] - after["nonmanifold_edges"]
        d_dg = before["degenerate_faces"] - after["degenerate_faces"]
        d_lv = before["loose_verts"] - after["loose_verts"]
        improved = (d_nm + d_dg + d_lv) > 0
        # 边界边（只有 1 个面）在 Blender 的 is_manifold 口径里也算 non-manifold；
        # 但开放壳是设计选择，不该判成"没修好" → 残留按"真正的非流形"（>2 面/线边）算。
        real_nonmanifold = max(0, after["nonmanifold_edges"] - after["boundary_edges"])
        residual_bad = real_nonmanifold > 0 or after["degenerate_faces"] > 0
        out.append({"name": ob.name, "ok": bool(improved and not residual_bad),
                    "improved": bool(improved), "residual": residual_bad,
                    "before": before, "after": after, "steps": steps,
                    "tris_after": _tris(me),
                    "real_nonmanifold": real_nonmanifold,
                    "open_shell": bool(after["boundary_edges"] > 0),
                    "note": ("还有真残留缺陷（>2 面的边或零面积面）：先用 audit_mesh 深挖" if residual_bad
                             else ("已清（开放壳 boundary=%d 属设计选择，未强封）" % after["boundary_edges"]
                                   if after["boundary_edges"] else "缺陷已清，且是闭合网格"))})
    ok = all(r.get("ok") for r in out)
    return _j({"ok": bool(ok), "actions": acts, "objects": out,
               "note": "修复是破坏性的：改前没 snapshot 的话回不去；残留缺陷别当没修过"})


def fix_decimate(objects=None, scope="ACTIVE", ratio=None, target_tris=None, mode="COLLAPSE"):
    """减面：ratio（0<r<=1）或 target_tris（自动换算 ratio）。"""
    m = str(mode or "COLLAPSE").upper()
    if m not in ("COLLAPSE", "UNSUBDIV", "DISSOLVE"):
        raise ValueError("未知 mode：%s（COLLAPSE/UNSUBDIV/DISSOLVE）" % mode)
    obs = _objs(objects, scope)
    out = []
    for ob in obs:
        me = ob.data
        t0 = _tris(me)
        if t0 <= 0:
            out.append({"name": ob.name, "ok": False, "error": "0 三角面"})
            continue
        if target_tris is not None:
            r = _num(target_tris, "target_tris", 1, 1e9) / float(t0)
            r = max(0.01, min(1.0, r))
        else:
            r = _num(ratio, "ratio", 0.01, 1.0)
        bpy.context.view_layer.objects.active = ob
        md = ob.modifiers.new(name="Decimate", type="DECIMATE")
        md.decimate_type = m
        md.ratio = r
        op = str(bpy.ops.object.modifier_apply(modifier=md.name))
        out.append({"name": ob.name, "ok": True, "mode": m, "ratio": round(r, 6),
                    "tris_before": t0, "tris_after": _tris(me), "verts_after": len(me.vertices), "op": op})
    return _j({"ok": all(r.get("ok") for r in out), "objects": out,
               "note": "减面会改拓扑与 UV 密度；要保 UV 就先用 uv_stats 看，减完再 uv_pack"})


def fix_selftest():
    """自检：造一个带缺陷的网格（边界洞 + 零面积面 + 孤立点）→ 修 → 断言缺陷下降 → 删净。"""
    me = bpy.data.meshes.new("__dsh_fix_selftest")
    ob = bpy.data.objects.new("__dsh_fix_selftest", me)
    bpy.context.scene.collection.objects.link(ob)
    ev = {}
    try:
        bm = bmesh.new()
        v = [bm.verts.new(c) for c in [(0, 0, 0), (1, 0, 0), (1, 1, 0), (0, 1, 0)]]
        bm.faces.new(v)                                  # 开放四边形 → 4 条边界边
        v2 = [bm.verts.new(c) for c in [(0, 0, 0), (1, 0, 0), (2, 0, 0)]]
        bm.faces.new(v2)                                 # 零面积（共线）
        bm.verts.new((5, 5, 5))                          # 孤立点
        bm.to_mesh(me)
        bm.free()
        ev["before"] = _defects_me(me)
        r = json.loads(fix_repair(objects=[ob.name],
                                  actions=["dissolve_degenerate", "delete_loose", "merge_doubles"]))
        ev["repair"] = {"ok": r.get("ok"), "before": r["objects"][0]["before"], "after": r["objects"][0]["after"]}
        ok = (ev["repair"]["after"]["degenerate_faces"] < ev["repair"]["before"]["degenerate_faces"]
              and ev["repair"]["after"]["loose_verts"] < ev["repair"]["before"]["loose_verts"])
        return _j({"ok": bool(ok), "evidence": ev})
    except Exception as e:
        return _j({"ok": False, "evidence": ev, "error": "%s: %s" % (type(e).__name__, str(e)[:200])})
    finally:
        try:
            bpy.data.objects.remove(ob, do_unlink=True)
            bpy.data.meshes.remove(me, do_unlink=True)
        except Exception:
            pass


def fix_help():
    return _j({
        "module": "mesh_fix.py", "version": FIX_VERSION,
        "ops": {
            "fix_repair": "actions=[merge_doubles|dissolve_degenerate|delete_loose|recalc_normals|fill_holes]，"
                          "merge_threshold（默认 1e-4）、max_hole_edges（默认 64）；回执带前后缺陷计数",
            "fix_decimate": "ratio=0.01..1 或 target_tris=N；mode=COLLAPSE|UNSUBDIV|DISSOLVE",
            "fix_selftest": "自检：合成带缺陷网格 → 修 → 断言缺陷下降",
            "fix_help": "本表",
        },
        "workflow": ["audit_mesh 拿缺陷清单", "blender_rt_txn(op=\"snapshot\") 留回退点",
                     "fix_repair（先 merge+dissolve+delete_loose，recalc_normals 保底）",
                     "audit_mesh 复检到 nonmanifold/degenerate 归零", "audit_gate 放行"],
        "hard_rules": ["0 个 mesh ⇒ ok=false", "actions 为空 ⇒ ok=false",
                       "没修动（缺陷数没降）⇒ ok=false，不许把'跑过'当'修好'"],
        "limits": {"open_shell": "设计上就该开放的壳体（薄壳/贴片）不要 fill_holes，会封死",
                   "uv": "修复/减面都会动 UV 密度，交付前用 uv_stats 复检"},
    })


def fix_dispatch(op, args_json):
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
    ops = {"repair": fix_repair, "decimate": fix_decimate, "selftest": fix_selftest, "help": fix_help}
    fn = ops.get(str(op))
    if fn is None:
        return _j({"ok": False, "error": "unknown fix op", "op": op, "ops": sorted(ops)})
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
    except TypeError as e:
        return _j({"ok": False, "error": "参数不匹配: %s" % str(e)[:200], "op": op, "help": fix_help()})
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
    _K.dsh_fix_api = _DshApi({"version": FIX_VERSION, "dispatch": fix_dispatch, "repair": fix_repair,
                              "decimate": fix_decimate, "selftest": fix_selftest, "help": fix_help})
