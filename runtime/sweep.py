# -*- coding: utf-8 -*-
"""DSH 沿路径扫掠（v0.9.6 · 上游整合 A5）—— 管路/线缆/轨道/护栏这类"沿路径"件。

旧 runtime 里 sweep 只在 qc/motion 里作为"扫描"出现，**没有沿路径成形的能力**。
上游 blend-ai 有 sweep_profile_along_path + analyze_sweep_path（先算路径弯折是否够宽）。
本模块按同一接口形状自实现：平行移动标架（rotation-minimizing frame）保证不扭转、不翻转。

ops：
    blender_rt_plan(op="sweep_analyze", args={path:[[0,0,0],[1,0,0],[1,1,0]], profile:{type:"circle", radius:0.05}})
    blender_rt_plan(op="sweep_build",   args={name:"Hose", path:[[0,0,0],[1,0,0],[1,1,0]],
                                             profile:{type:"rect", width:0.08, height:0.04}, cap:true})
    blender_rt_plan(op="sweep_selftest")

**先算后建**：sweep_build 默认先跑 sweep_analyze；弯折半径 < 型材半宽 时直接拒（force=true 才硬做），
因为"扫出来自交"在几何上不可修 —— 这和契约层的 destructive_guard 是同一套纪律。
"""
import json
import math

import bpy
from mathutils import Vector

SWEEP_VERSION = 1


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


def _vec3(v, name):
    if not isinstance(v, (list, tuple)) or len(v) != 3:
        raise ValueError("%s 需要 [x,y,z]，收到 %r" % (name, v))
    return [ _num(v[0], name + "[0]"), _num(v[1], name + "[1]"), _num(v[2], name + "[2]") ]


def _profile(profile):
    """归一化型材：返回 (点表 [(u,v)], 最大外廓尺寸)。"""
    p = profile if isinstance(profile, dict) else {"type": "circle", "radius": 0.05}
    t = str(p.get("type") or ("points" if p.get("points") else "circle")).lower()
    if t == "circle":
        r = _num(p.get("radius"), "profile.radius", 1e-6, 1e6)
        seg = int(_num(p.get("segments"), "profile.segments", 3, 256, 16))
        pts = [(r * math.cos(2 * math.pi * i / seg), r * math.sin(2 * math.pi * i / seg)) for i in range(seg)]
        return pts, 2.0 * r
    if t == "rect":
        w = _num(p.get("width"), "profile.width", 1e-6, 1e6)
        h = _num(p.get("height"), "profile.height", 1e-6, 1e6)
        hw, hh = w / 2.0, h / 2.0
        return [(-hw, -hh), (hw, -hh), (hw, hh), (-hw, hh)], max(w, h)
    if t == "points":
        raw = p.get("points")
        if not isinstance(raw, (list, tuple)) or len(raw) < 3:
            raise ValueError("profile.points 至少 3 个点")
        pts = [(_num(q[0], "profile.points[][0]"), _num(q[1], "profile.points[][1]")) for q in raw]
        sz = 2.0 * max(max(abs(a), abs(b)) for a, b in pts)
        return pts, sz
    raise ValueError("未知 profile.type：%s（circle/rect/points）" % t)


def _path_points(path=None, path_object=None):
    """路径来源：显式 path=[[x,y,z]...] 或 path_object=<曲线/网格对象>（曲线走 evaluated mesh 取折线）。"""
    if path:
        if not isinstance(path, (list, tuple)) or len(path) < 2:
            raise ValueError("path 至少 2 个点")
        return [Vector(_vec3(p, "path[%d]" % i)) for i, p in enumerate(path)]
    if not path_object:
        raise ValueError("path 或 path_object 必填")
    ob = bpy.data.objects.get(str(path_object))
    if ob is None:
        raise ValueError("对象不存在：%s" % path_object)
    dg = bpy.context.evaluated_depsgraph_get()
    ev = ob.evaluated_get(dg)
    me = ev.to_mesh()
    try:
        pts = [ob.matrix_world @ v.co.copy() for v in me.vertices]
    finally:
        ev.to_mesh_clear()
    if len(pts) < 2:
        raise ValueError("path_object 采样不到 2 个点（曲线要有几何）")
    return pts


def _frames(pts, up=None):
    """平行移动标架：相邻切线用 rotation_difference 传递法线，避免扭转/翻转。"""
    n = len(pts)
    tan = []
    for i in range(n):
        if i == 0:
            t = pts[1] - pts[0]
        elif i == n - 1:
            t = pts[-1] - pts[-2]
        else:
            t = pts[i + 1] - pts[i - 1]
        if t.length < 1e-12:
            raise ValueError("路径点 %d 与相邻点重合（间距为 0）" % i)
        tan.append(t.normalized())
    upv = Vector(_vec3(up, "up")) if up else Vector((0.0, 0.0, 1.0))
    n0 = upv - tan[0] * upv.dot(tan[0])
    if n0.length < 1e-9:
        n0 = Vector((1.0, 0.0, 0.0)) - tan[0] * tan[0].x
        if n0.length < 1e-9:
            n0 = Vector((0.0, 1.0, 0.0)) - tan[0] * tan[0].y
    n0.normalize()
    normals = [n0]
    for i in range(1, n):
        q = tan[i - 1].rotation_difference(tan[i])
        nv = (q @ normals[-1])
        nv = nv - tan[i] * nv.dot(tan[i])
        if nv.length < 1e-9:
            nv = normals[-1]
        normals.append(nv.normalized())
    binorm = [tan[i].cross(normals[i]).normalized() for i in range(n)]
    return tan, normals, binorm


def _bend_radii(pts):
    """每个内部点的弯折半径（三角形外接圆半径）；近共线时给 None（视为直线）。"""
    out = []
    for i in range(1, len(pts) - 1):
        a, b, c = pts[i - 1], pts[i], pts[i + 1]
        ab = (b - a).length
        bc = (c - b).length
        ca = (a - c).length
        s = (ab + bc + ca) / 2.0
        area2 = s * (s - ab) * (s - bc) * (s - ca)
        area = math.sqrt(area2) if area2 > 0 else 0.0
        if area <= 1e-12 or ab <= 0 or bc <= 0 or ca <= 0:
            out.append(None)
        else:
            out.append((ab * bc * ca) / (4.0 * area))
    return out


def sweep_analyze(path=None, path_object=None, profile=None, min_radius_factor=1.0, up=None):
    """只读：路径弯折半径 vs 型材半宽 —— 太紧就先改路径，别扫出自交。"""
    pts = _path_points(path, path_object)
    prof, size = _profile(profile)
    factor = _num(min_radius_factor, "min_radius_factor", 0.1, 100.0, 1.0)
    radii = _bend_radii(pts)
    req = (size / 2.0) * factor
    worst = None
    for i, r in enumerate(radii):
        if r is None:
            continue
        if worst is None or r < worst[1]:
            worst = (i + 1, r)
    finite = [r for r in radii if r is not None]
    ok = (worst is None) or (worst[1] >= req)
    rec = {"ok": bool(ok), "points": len(pts), "profile_max_size": round(size, 6),
           "required_min_radius": round(req, 6),
           "min_radius": round(min(finite), 6) if finite else None,
           "min_radius_at": worst[0] if worst else None,
           "straight_segments": sum(1 for r in radii if r is None),
           "segment_lengths": [round((pts[i + 1] - pts[i]).length, 6) for i in range(len(pts) - 1)][:64],
           "total_length": round(sum((pts[i + 1] - pts[i]).length for i in range(len(pts) - 1)), 6)}
    if not ok:
        rec["hint"] = ("路径第 %d 点弯折半径 %.4f < 需要 %.4f：把拐角拆成圆弧、加大间距，或换更小型材"
                       % (worst[0], worst[1], req))
    return _j(rec)


def sweep_build(path=None, path_object=None, profile=None, name="Sweep", cap=True, close_path=False,
                up=None, smooth=True, collection=None, min_radius_factor=1.0, force=False,
                keep_original=False):
    """沿路径扫掠成型（平行移动标架，不扭转）；默认先跑 analyze，太紧就拒（force=true 硬做）。"""
    pts = _path_points(path, path_object)
    prof, size = _profile(profile)
    anal = json.loads(sweep_analyze(path=[list(p) for p in pts], profile=profile,
                                    min_radius_factor=min_radius_factor, up=up))
    if not anal.get("ok") and not force:
        return _j({"ok": False, "stage": "analyze", "analysis": anal,
                   "error": "路径弯折过紧，扫掠会自交；先改路径，或 force=true 硬做"})
    if close_path:
        pts = pts + [pts[0]]
    tan, nor, bino = _frames(pts, up)
    m = len(prof)
    verts = []
    for i, p in enumerate(pts):
        for (u, v) in prof:
            q = p + nor[i] * u + bino[i] * v
            verts.append((q.x, q.y, q.z))
    faces = []
    for i in range(len(pts) - 1):
        for j in range(m):
            j2 = (j + 1) % m
            faces.append((i * m + j, i * m + j2, (i + 1) * m + j2, (i + 1) * m + j))
    if close_path:
        for j in range(m):
            j2 = (j + 1) % m
            faces.append(((len(pts) - 1) * m + j, (len(pts) - 1) * m + j2, j2, j))
    elif cap:
        c0 = len(verts)
        verts.append((pts[0].x, pts[0].y, pts[0].z))
        c1 = len(verts)
        verts.append((pts[-1].x, pts[-1].y, pts[-1].z))
        for j in range(m):
            j2 = (j + 1) % m
            faces.append((c0, j2, j))
            faces.append((c1, (len(pts) - 1) * m + j, (len(pts) - 1) * m + j2))
    me = bpy.data.meshes.new(name)
    me.from_pydata(verts, [], faces)
    me.validate()
    me.update()
    if smooth:
        for p in me.polygons:
            p.use_smooth = True
    ob = bpy.data.objects.new(name, me)
    coll = bpy.data.collections.get(str(collection)) if collection else None
    (coll.objects if coll else bpy.context.scene.collection.objects).link(ob)
    total = sum((pts[i + 1] - pts[i]).length for i in range(len(pts) - 1))
    return _j({"ok": True, "name": ob.name, "verts": len(me.vertices), "faces": len(me.polygons),
               "profile_points": m, "path_points": len(pts), "cap": bool(cap and not close_path),
               "closed": bool(close_path), "length": round(total, 6),
               "smooth": bool(smooth), "analysis": {"ok": anal.get("ok"),
                                                    "min_radius": anal.get("min_radius"),
                                                    "required_min_radius": anal.get("required_min_radius")},
               "note": "平行移动标架：拐弯处不扭转；UV/材质留给 uv_smart_project + presets"})


def sweep_selftest():
    """自检：直管 + 弯头各扫一次，断言顶点/面数正确、且过紧的路径被 analyze 拦下。"""
    ev = {}
    made = []
    try:
        r1 = json.loads(sweep_build(name="__dsh_sweep_straight", path=[[0, 0, 0], [2, 0, 0], [4, 0, 0]],
                                    profile={"type": "circle", "radius": 0.1, "segments": 12}))
        made.append(r1.get("name"))
        ev["straight"] = {"ok": r1.get("ok"), "verts": r1.get("verts"), "faces": r1.get("faces")}
        r2 = json.loads(sweep_build(name="__dsh_sweep_elbow", path=[[0, 0, 0], [1, 0, 0], [1.05, 0.05, 0]],
                                    profile={"type": "rect", "width": 0.04, "height": 0.04}))
        made.append(r2.get("name"))
        ev["elbow"] = {"ok": r2.get("ok"), "min_radius": r2.get("analysis", {}).get("min_radius")}
        r3 = json.loads(sweep_analyze(path=[[0, 0, 0], [0.02, 0, 0], [0.02, 0.02, 0]],
                                      profile={"type": "rect", "width": 0.4, "height": 0.4}))
        ev["tight_should_fail"] = r3.get("ok")
        # 期望：直管 12 段 x 3 站 + 2 中心点 = 38 顶点；面 = 12*2 + 12*2 = 48
        ok = (ev["straight"]["ok"] and ev["straight"]["verts"] == 38 and ev["straight"]["faces"] == 48
              and ev["elbow"]["ok"] and ev["tight_should_fail"] is False)
        return _j({"ok": bool(ok), "evidence": ev,
                   "expect": {"verts": 38, "faces": 48, "tight_path_ok": False}})
    except Exception as e:
        return _j({"ok": False, "evidence": ev, "error": "%s: %s" % (type(e).__name__, str(e)[:200])})
    finally:
        for nm in made:
            if not nm:
                continue
            ob = bpy.data.objects.get(nm)
            if ob is not None:
                me = ob.data
                bpy.data.objects.remove(ob, do_unlink=True)
                try:
                    bpy.data.meshes.remove(me, do_unlink=True)
                except Exception:
                    pass


def sweep_help():
    return _j({
        "module": "sweep.py", "version": SWEEP_VERSION,
        "ops": {
            "sweep_analyze": "只读：path=[[x,y,z]...] 或 path_object=<曲线>；profile={circle|rect|points}；"
                             "min_radius_factor 默认 1.0；回执给 min_radius / required_min_radius / 该点序号",
            "sweep_build": "name / path|path_object / profile / cap（默认 true）/ close_path / up / smooth / "
                           "collection / force（默认 false：弯折过紧直接拒）",
            "sweep_selftest": "自检：直管 38 顶点 48 面 + 弯头成型 + 过紧路径被拦",
            "sweep_help": "本表",
        },
        "profile_examples": {"circle": {"type": "circle", "radius": 0.05, "segments": 16},
                             "rect": {"type": "rect", "width": 0.08, "height": 0.04},
                             "custom": {"type": "points", "points": [[0, 0], [0.04, 0.01], [0.02, 0.05]]}},
        "why": "平行移动标架（rotation-minimizing frame）—— 拐弯处不扭转、不翻转；先 analyze 再 build",
        "limits": "路径是折线（曲线对象会按 evaluated mesh 采样）；尖角处即使半径够也会有一处折痕，"
                  "要圆滑请先用曲线加圆角或把拐角拆成多段",
    })


def sweep_dispatch(op, args_json):
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
    ops = {"analyze": sweep_analyze, "build": sweep_build, "selftest": sweep_selftest, "help": sweep_help}
    fn = ops.get(str(op))
    if fn is None:
        return _j({"ok": False, "error": "unknown sweep op", "op": op, "ops": sorted(ops)})
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
        return _j({"ok": False, "error": "参数不匹配: %s" % str(e)[:200], "op": op, "help": sweep_help()})
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
    _K.dsh_sweep_api = _DshApi({"version": SWEEP_VERSION, "dispatch": sweep_dispatch,
                                "analyze": sweep_analyze, "build": sweep_build,
                                "selftest": sweep_selftest, "help": sweep_help})
