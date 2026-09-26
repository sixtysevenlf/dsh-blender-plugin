# -*- coding: utf-8 -*-
"""DSH 成对间隙门（P3 配套）：**规则表驱动**的最近距离 + 互穿判定。

为什么单独一个模块：audit_interference 只给「交集体积」，判不出车辆/机构装配里**两种不同的规则**：
  ① 轮 ↔ 轮眉/车身：必须留间隙（不许碰）；
  ② 板件 ↔ 主体：按工艺**允许互插**（allow_overlap=true），但要有上限。

ops：
    clear_check(pairs=[...])  —— 规则表：{id, a:[对象], b:[对象], min_mm, allow_overlap}
    clear_selftest / clear_help

做法：两侧各建 BVH；`overlap()` 判互穿（精确：三角面相交）；不互穿时用顶点+面心采样量到对面曲面的最近距离。
⚠ 诚实边界：间隙是**采样点**到对面曲面的距离 —— 两个大曲面平行贴合时可能高估；互穿判定是精确的。
"""
import json
import random

import bpy

CLEAR_VERSION = 1


def _j(o):
    return json.dumps(o, ensure_ascii=False, default=str)


def _world_tris_of(objs, include_hidden=True):
    dg = bpy.context.evaluated_depsgraph_get()
    tris = []
    for ob in objs:
        if ob is None or ob.type != "MESH":
            continue
        if not include_hidden:
            try:
                if not ob.visible_get():
                    continue
            except Exception:
                pass
        ev = ob.evaluated_get(dg)
        me = ev.to_mesh()
        try:
            me.calc_loop_triangles()
            mw = ob.matrix_world
            for t in me.loop_triangles:
                tris.append([tuple(mw @ me.vertices[i].co) for i in t.vertices])
        finally:
            ev.to_mesh_clear()
    return tris


def _bvh_of(tris):
    from mathutils.bvhtree import BVHTree
    if not tris:
        return None
    V = [v for t in tris for v in t]
    polys = [(3 * i, 3 * i + 1, 3 * i + 2) for i in range(len(tris))]
    return BVHTree.FromPolygons(V, polys, all_triangles=True)


def clear_check(pairs=None, samples=1200, seed=0, include_hidden=True):
    """规则表驱动的间隙/互穿判定。返回逐对 gap_mm / interpenetrating / verdict。"""
    import mathutils
    if not pairs:
        raise ValueError("pairs 必填：[{id,a:[对象],b:[对象],min_mm,allow_overlap}]")
    mmu = float(bpy.context.scene.unit_settings.scale_length or 1.0) * 1000.0
    rows, failed = [], []
    for i, spec in enumerate(list(pairs)):
        sp = dict(spec or {})
        pid = str(sp.get("id") or ("pair%d" % i))
        a_names = sp.get("a") or []
        b_names = sp.get("b") or []
        a_names = [a_names] if isinstance(a_names, str) else list(a_names)
        b_names = [b_names] if isinstance(b_names, str) else list(b_names)
        if not a_names or not b_names:
            rows.append({"id": pid, "verdict": "error", "error": "a / b 都要给对象列表"})
            failed.append(pid)
            continue
        objs_a = [bpy.data.objects.get(str(n)) for n in a_names]
        objs_b = [bpy.data.objects.get(str(n)) for n in b_names]
        miss = [n for n, o in zip(a_names + b_names, objs_a + objs_b) if o is None]
        if miss:
            rows.append({"id": pid, "verdict": "error", "error": "对象不存在：%s" % miss[:4]})
            failed.append(pid)
            continue
        ta, tb = _world_tris_of(objs_a, include_hidden), _world_tris_of(objs_b, include_hidden)
        if not ta or not tb:
            rows.append({"id": pid, "verdict": "error", "error": "有一侧没有三角面（空对象？）"})
            failed.append(pid)
            continue
        ba, bb = _bvh_of(ta), _bvh_of(tb)
        ov = ba.overlap(bb) or []
        inter = len(ov) > 0
        rng = random.Random(int(seed))
        best = None
        for tris, other in ((ta, bb), (tb, ba)):
            pts = []
            for t in tris:
                pts.append(t[0]); pts.append(t[1]); pts.append(t[2])
                pts.append(((t[0][0] + t[1][0] + t[2][0]) / 3.0,
                            (t[0][1] + t[1][1] + t[2][1]) / 3.0,
                            (t[0][2] + t[1][2] + t[2][2]) / 3.0))
            if len(pts) > int(samples):
                pts = rng.sample(pts, int(samples))
            for p in pts:
                r = other.find_nearest(p)
                if r is None or r[0] is None:
                    continue
                d = (r[0] - mathutils.Vector(p)).length
                if best is None or d < best:
                    best = d
        gap_mm = (best * mmu) if best is not None else None
        min_mm = float(sp.get("min_mm") or 0.0)
        allow = bool(sp.get("allow_overlap") or False)
        if inter and not allow:
            verdict, why = "fail", "互穿：%d 对三角面相交" % len(ov)
        elif (not inter) and gap_mm is not None and gap_mm < min_mm:
            verdict, why = "fail", "间隙 %.3f mm < 要求 %.3f mm" % (gap_mm, min_mm)
        else:
            verdict = "pass"
            why = ("允许互插（%d 对三角面相交）" % len(ov)) if inter else ("间隙 %.3f mm ≥ %.3f mm" % (gap_mm or 0.0, min_mm))
        rows.append({"id": pid, "a": a_names[:6], "b": b_names[:6],
                     "gap_mm": (round(gap_mm, 4) if gap_mm is not None else None),
                     "interpenetrating": bool(inter), "overlap_tri_pairs": len(ov),
                     "min_mm": min_mm, "allow_overlap": allow, "verdict": verdict, "why": why})
        if verdict != "pass":
            failed.append(pid)
    gaps = [r["gap_mm"] for r in rows if r.get("gap_mm") is not None and not r.get("interpenetrating")]
    return _j({"ok": not failed, "verdict": ("pass" if not failed else "fail"), "pairs": rows,
               "pair_count": len(rows), "pairs_failed": failed,
               "min_clearance_mm": (round(min(gaps), 4) if gaps else None),
               "units": {"mm_per_unit": round(mmu, 6)},
               "note": "min_mm 管间隙、allow_overlap 管允许互插（板件）—— 两种规则别用一条阈值盖",
               "honest_limits": ["间隙用采样点量：两个大曲面平行贴合时可能高估（互穿判定精确）",
                                 "只看给定 pairs：没列进来的关系不会被检查"]})


def clear_selftest():
    """自检：10mm 间隙该过 · 2mm 该挂 · 互穿该挂 · 允许互插该过。"""
    import mathutils
    import bmesh as _bm
    prev = float(bpy.context.scene.unit_settings.scale_length)
    made = []
    ev = {}
    try:
        bpy.context.scene.unit_settings.scale_length = 0.001

        def box(nm, x, size=20.0):
            me = bpy.data.meshes.new(nm)
            ob = bpy.data.objects.new(nm, me)
            bpy.context.scene.collection.objects.link(ob)
            bm = _bm.new()
            _bm.ops.create_cube(bm, size=1.0, matrix=mathutils.Matrix.Diagonal((size, size, size, 1.0)))
            bm.to_mesh(me)
            bm.free()
            ob.location = (x, 0.0, 0.0)
            made.append(nm)
            return nm

        a1, b1 = box("__dsh_cl_a", 0.0), box("__dsh_cl_b", 30.0)
        a2, b2 = box("__dsh_cl_c", 0.0), box("__dsh_cl_d", 22.0)
        a3, b3 = box("__dsh_cl_e", 0.0), box("__dsh_cl_f", 10.0)
        try:
            bpy.context.view_layer.update()
        except Exception:
            pass
        r = json.loads(clear_check(pairs=[
            {"id": "gap10", "a": [a1], "b": [b1], "min_mm": 5.0},
            {"id": "gap2", "a": [a2], "b": [b2], "min_mm": 5.0},
            {"id": "overlap", "a": [a3], "b": [b3], "min_mm": 1.0},
            {"id": "overlap_ok", "a": [a3], "b": [b3], "allow_overlap": True},
        ]))
        by = {x["id"]: x for x in r.get("pairs", [])}
        ev["gap10"] = {"gap_mm": by["gap10"].get("gap_mm"), "verdict": by["gap10"].get("verdict")}
        ev["gap2"] = {"gap_mm": by["gap2"].get("gap_mm"), "verdict": by["gap2"].get("verdict")}
        ev["overlap"] = {"inter": by["overlap"].get("interpenetrating"), "verdict": by["overlap"].get("verdict")}
        ev["allowed"] = {"verdict": by["overlap_ok"].get("verdict")}
        ev["top"] = {"ok": r.get("ok"), "failed": r.get("pairs_failed")}
        ok = (by["gap10"].get("verdict") == "pass" and abs((by["gap10"].get("gap_mm") or 0) - 10.0) < 0.6
              and by["gap2"].get("verdict") == "fail"
              and by["overlap"].get("verdict") == "fail" and by["overlap"].get("interpenetrating") is True
              and by["overlap_ok"].get("verdict") == "pass"
              and r.get("ok") is False and set(r.get("pairs_failed") or []) == {"gap2", "overlap"})
        return _j({"ok": bool(ok), "evidence": ev})
    except Exception as e:
        return _j({"ok": False, "evidence": ev, "error": "%s: %s" % (type(e).__name__, str(e)[:220])})
    finally:
        bpy.context.scene.unit_settings.scale_length = prev
        for nm in made:
            o = bpy.data.objects.get(nm)
            if o is not None:
                me = o.data
                bpy.data.objects.remove(o, do_unlink=True)
                if me is not None and me.users == 0:
                    try:
                        bpy.data.meshes.remove(me, do_unlink=True)
                    except Exception:
                        pass


def clear_help():
    return _j({
        "module": "clearance.py", "version": CLEAR_VERSION,
        "what": "成对间隙门：规则表驱动（min_mm 管间隙 / allow_overlap 管允许互插），给最近距离与互穿判定",
        "ops": ["clear_check(pairs)", "clear_selftest", "clear_help"],
        "pair_schema": {"id": "规则名", "a": ["对象…"], "b": ["对象…"], "min_mm": 5.0, "allow_overlap": False},
        "protocol": ["轮/悬挂/灯具这类**不许碰**的：min_mm 给工艺间隙（含悬挂行程）",
                     "板件/蒙皮这类**允许互插**的：allow_overlap=true（互穿是设计要求）",
                     "门里用它：先把规则写进 spec.py，再让 gate_run 一次判完"],
        "limits": ["间隙是采样点距离（大曲面平行贴合可能高估）；互穿判定精确",
                   "只看列进 pairs 的关系"],
    })


def clear_dispatch(op, args_json):
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
    ops = {"check": clear_check, "selftest": clear_selftest, "help": clear_help}
    fn = ops.get(str(op))
    if fn is None:
        return _j({"ok": False, "error": "unknown clearance op", "op": op, "ops": sorted(ops)})
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
    _K.dsh_clearance_api = _DshApi({"version": CLEAR_VERSION, "dispatch": clear_dispatch,
                                    "check": clear_check, "selftest": clear_selftest, "help": clear_help})