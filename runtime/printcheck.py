# -*- coding: utf-8 -*-
"""DSH 制造检查（v0.9.6 · 上游整合 A4）—— 3D 打印/加工口径的壁厚与悬垂。

旧 runtime 里 thin wall / overhang 关键词 = 0 命中；audit_* 只管干涉/连通/包络，不管"能不能造出来"。
本模块不依赖 Blender 的 3D Print Toolbox 插件（那是上游 blend-ai 的做法），自己用 BVH 射线测壁厚、
用法线锥测悬垂 —— 结果可复算、可写进验收回执。

ops：
    blender_rt_plan(op="print_walls",    args={objects:["Body"], min_mm:1.2, max_samples:4000})   # 只读
    blender_rt_plan(op="print_overhang", args={objects:["Body"], max_angle_deg:45})               # 只读
    blender_rt_plan(op="print_report",   args={objects:["Body"], min_mm:1.2, max_angle_deg:45})   # 只读
    blender_rt_plan(op="print_selftest")

口径：1 Blender 单位 = 1 m（默认场景），mm = 单位 * scale_length * 1000；可用 mm_per_unit 覆盖。
"""
import json
import math

import bpy
import mathutils
from mathutils import Vector
from mathutils.bvhtree import BVHTree

PRINT_VERSION = 1


import sys as _sys_kit
_KIT = getattr(_sys_kit.modules.get("dsh_rt_kernel"), "dsh_kit", None)
if _KIT is None:
    raise RuntimeError("printcheck 需要共享内核 K.dsh_kit（由 KERNEL_BOOTSTRAP 注入）")


_j = _KIT.j  # 共享内核（原自带实现已删，见 S1）
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
            raise ValueError("没有活动的 mesh 对象")
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
    raise ValueError("未知 scope：%s" % scope)


def _mm_per_unit(mm_per_unit=None):
    if mm_per_unit is not None:
        return _num(mm_per_unit, "mm_per_unit", 1e-9, 1e9)
    try:
        return _KIT.units()
    except Exception:
        return 1000.0


def _scale_avg(ob):
    s = ob.matrix_world.to_scale()
    return (abs(s[0]) + abs(s[1]) + abs(s[2])) / 3.0 or 1.0


def _bvh(me):
    verts = [v.co.copy() for v in me.vertices]
    polys = [tuple(p.vertices) for p in me.polygons]
    return BVHTree.FromPolygons(verts, polys, all_triangles=False, epsilon=0.0)


def print_walls(objects=None, scope="ACTIVE", min_mm=1.0, max_samples=4000, mm_per_unit=None,
                samples=None):
    """壁厚：从每个采样面中心沿 -法线 打一条射线，命中距离即局部厚度（局部空间测，再按缩放换算）。"""
    thr_mm = _num(min_mm, "min_mm", 1e-6, 1e6)
    cap = int(_num(max_samples if samples is None else samples, "max_samples", 16, 200000))
    mmu = _mm_per_unit(mm_per_unit)
    obs = _objs(objects, scope)
    out = []
    for ob in obs:
        me = ob.data
        nf = len(me.polygons)
        if nf == 0:
            out.append({"name": ob.name, "ok": False, "error": "0 面"})
            continue
        bvh = _bvh(me)
        sc = _scale_avg(ob)
        # 薄壁测量的分辨率上限 = 本地面尺寸：体素重构出的面是 60mm 级，非平面四边形
        # 的法线与三角化法线不一致，从面心偏移 1e-5 打射线会在同一个面片上立刻命中，
        # 把 2m 的球读成 0.02mm 薄壁（实测 26% 采样被误判）。所以：
        #   1) 起点沿 -法线偏移 0.25 * 本地面尺寸；
        #   2) 命中距离 <= 0.5 * 本地面尺寸的一律当"同一表面片"跳过；
        #   3) 回执给 resolution_mm —— 低于分辨率的 min_mm 结论不可信，必须明说。
        diag = max(float(ob.dimensions.length), 1e-9)
        fsize0 = max((math.sqrt(max(float(pp.area), 0.0)) for pp in me.polygons), default=0.0)
        stride = max(1, nf // cap)
        vals = []
        worst = []
        self_hits = 0
        sizes = []
        for i in range(0, nf, stride):
            p = me.polygons[i]
            n = p.normal.copy()
            if n.length < 1e-12:
                continue
            n.normalize()
            fsize = math.sqrt(max(float(p.area), 0.0))
            sizes.append(fsize)
            # v0.9.6（P0 标定抓到）：原来 off = 0.25*面尺寸（40x30 面的 off≈8.7mm）**比板厚还大**
            # ⇒ 射线起点跑到零件外面，薄板一个样本都采不到（40x30x0.3mm 实测报 30mm、thin=0）。
            # 改为：起点只避开自己这一面片（极小偏移），用**命中面法线**区分「同一表面片」与「对壁」：
            #   同向（n·hn > 0）⇒ 还是同一片，继续往前找；反向（n·hn < -0.5）⇒ 找到对壁，取该距离。
            _eps = max(1e-6, fsize * 1e-3)
            off = _eps
            origin = p.center - n * off
            hit = bvh.ray_cast(origin, -n, 1e9)
            _tries = 0
            while hit is not None and hit[3] is not None and _tries < 8:
                _tries += 1
                hn = hit[1]
                if hn is not None and float(n.dot(hn)) < -0.5:
                    break                              # 对壁 ✓
                self_hits += 1
                origin = hit[0] - n * _eps
                hit = bvh.ray_cast(origin, -n, 1e9)
            if hit is None or hit[3] is None:
                continue
            # v0.9.6（P0 标定抓到 · 第二个真 bug）：各向异性缩放不能用平均 scale ——
            # 底板 z 缩 0.1（3mm→0.3mm）时 sc≈0.7，会把 0.3mm 算成 2.1mm ⇒ 薄壁漏判。
            # 正确做法：把命中点与面心都用**世界矩阵**变换后再量距离（对任意仿射都成立）。
            _mw = ob.matrix_world
            thick_mm = (_mw @ mathutils.Vector(hit[0]) - _mw @ p.center).length * mmu
            vals.append(thick_mm)
            if thick_mm < thr_mm:
                worst.append({"face": int(i), "mm": round(thick_mm, 4)})
        if not vals:
            out.append({"name": ob.name, "ok": False, "skipped_self_hits": self_hits,
                        "error": "射线没有命中任何背面（开放壳体/法线朝内？）",
                        "hint": "开放薄壳测不了厚度：先用 audit_mesh 看 boundary/normals"})
            continue
        vals.sort()
        n = len(vals)

        def q(f):
            return vals[min(n - 1, max(0, int(f * (n - 1))))]
        thin = [v for v in vals if v < thr_mm]
        rec = {"name": ob.name, "ok": len(thin) == 0, "threshold_mm": thr_mm, "samples": n,
               "sampled_faces": n, "stride": stride, "mm_per_unit": mmu,
               "min_mm": round(vals[0], 4), "p05_mm": round(q(0.05), 4), "median_mm": round(q(0.5), 4),
               "max_mm": round(vals[-1], 4), "thin_samples": len(thin),
               "thin_ratio": round(len(thin) / float(n), 4),
               "skipped_self_hits": self_hits,
               "resolution_mm": round((sorted(sizes)[len(sizes)//2] if sizes else fsize0) * sc * mmu, 4),
               # 射线法本身能分辨到多细（v0.9.6 新逻辑：起点只避开本面片）—— 与上面的**几何**分辨率区分开
               "ray_precision_mm": round(max(1e-6, (sorted(sizes)[len(sizes)//2] if sizes else fsize0) * 1e-3) * sc * mmu, 6),
               "worst": sorted(worst, key=lambda r: r["mm"])[:10]}
        res_mm = rec["resolution_mm"]
        hint = ("有 %d 处低于 %.3f mm：薄壁会打不出来/一捏就碎" % (len(thin), thr_mm)) if thin else "无薄壁"
        if thr_mm < res_mm:
            rec["warning"] = ("min_mm=%.3f 低于本网格的测量分辨率 %.3f mm：几何本身分辨不出这么薄的壁，结论不可信（先细化/降 voxel_size）" % (thr_mm, res_mm))
        if self_hits:
            hint += "；%d 处命中在分辨率内已按同一表面跳过" % self_hits
        rec["hint"] = hint
        out.append(rec)
    return _j({"ok": all(r.get("ok") for r in out), "objects": out,
               "note": "厚度=沿面法线向内的最近命中距离；凹角/薄壳可能偏乐观，判据看 p05 与 thin_ratio"})


def print_overhang(objects=None, scope="ACTIVE", max_angle_deg=45.0, up="Z", min_area_mm2=0.0,
                   mm_per_unit=None, max_list=20):
    """悬垂：面法线与"正下方"的夹角 <= max_angle_deg 视为需要支撑（0° = 正朝下）。"""
    ang = _num(max_angle_deg, "max_angle_deg", 0.0, 90.0)
    upv = str(up or "Z").upper()
    axis = {"X": Vector((1, 0, 0)), "Y": Vector((0, 1, 0)), "Z": Vector((0, 0, 1))}.get(upv)
    if axis is None:
        return _j({"ok": False, "error": "up 只支持 X/Y/Z，收到 %r" % up})
    down = -axis
    mmu = _mm_per_unit(mm_per_unit)
    min_area = _num(min_area_mm2, "min_area_mm2", 0.0, 1e12, 0.0)
    obs = _objs(objects, scope)
    out = []
    for ob in obs:
        me = ob.data
        if len(me.polygons) == 0:
            out.append({"name": ob.name, "ok": False, "error": "0 面"})
            continue
        mw = ob.matrix_world.to_3x3()
        sc = _scale_avg(ob)
        area_factor = (sc * mmu) ** 2
        tot = 0.0
        bad_area = 0.0
        bad = []
        cos_lim = math.cos(math.radians(ang))
        for i, p in enumerate(me.polygons):
            nrm = (mw @ p.normal)
            if nrm.length < 1e-12:
                continue
            nrm.normalize()
            a_mm2 = float(p.area) * area_factor
            tot += a_mm2
            c = nrm.dot(down)
            if c >= cos_lim:
                bad_area += a_mm2
                if len(bad) < int(max_list):
                    bad.append({"face": int(i), "angle_deg": round(math.degrees(math.acos(max(-1.0, min(1.0, c)))), 2),
                                "area_mm2": round(a_mm2, 4)})
        over_min = bad_area > min_area
        rec = {"name": ob.name, "ok": not over_min, "up": upv, "max_angle_deg": ang,
               "faces": len(me.polygons), "total_area_mm2": round(tot, 4),
               "overhang_area_mm2": round(bad_area, 4),
               "overhang_ratio": round(bad_area / tot, 4) if tot > 0 else None,
               "overhang_faces_listed": len(bad), "samples": bad,
               "hint": ("悬垂面积 %.2f mm² 超阈值 %.2f mm²：需要支撑或改朝向" % (bad_area, min_area)) if over_min
                       else "无超阈悬垂"}
        out.append(rec)
    return _j({"ok": all(r.get("ok") for r in out), "objects": out,
               "note": "口径：法线与正下方夹角 <= max_angle_deg（默认 45°）算需支撑；底面贴床的平面请用 min_area_mm2 排除"})


def print_report(objects=None, scope="ACTIVE", min_mm=1.0, max_angle_deg=45.0, up="Z",
                 min_area_mm2=0.0, mm_per_unit=None, max_samples=4000):
    """汇总回执：壁厚 + 悬垂 + 网格可造性，给一个总判定（供交付门用）。"""
    w = json.loads(print_walls(objects=objects, scope=scope, min_mm=min_mm, max_samples=max_samples,
                               mm_per_unit=mm_per_unit))
    o = json.loads(print_overhang(objects=objects, scope=scope, max_angle_deg=max_angle_deg, up=up,
                                  min_area_mm2=min_area_mm2, mm_per_unit=mm_per_unit))
    if not w.get("ok") and any(r.get("error") for r in w.get("objects", [])):
        return _j({"ok": False, "stage": "walls", "walls": w, "overhang": o})
    rows = []
    for a, b in zip(w.get("objects", []), o.get("objects", [])):
        rows.append({"name": a.get("name"), "ok": bool(a.get("ok") and b.get("ok")),
                     "min_mm": a.get("min_mm"), "thin_samples": a.get("thin_samples"),
                     "overhang_area_mm2": b.get("overhang_area_mm2"), "overhang_ratio": b.get("overhang_ratio")})
    _thin_total = sum(int(a.get("thin_samples") or 0) for a in w.get("objects", []))
    _mins = [a.get("min_mm") for a in w.get("objects", []) if a.get("min_mm") is not None]
    return _j({"ok": all(r["ok"] for r in rows), "objects": rows,
               # v0.9.6（整合方案 P0）：门友好汇总 —— 判据用顶层字段，别让 gate spec 去翻 objects[]
               "walls_ok": all(bool(a.get("ok")) for a in w.get("objects", [])),
               "overhang_ok": all(bool(b.get("ok")) for b in o.get("objects", [])),
               "thin_samples_total": _thin_total,
               "worst_min_mm": (min(_mins) if _mins else None),
               # 门判据建议用 p05：实测体素球有 0.05% 的近零离群样本，判 min 会假报薄壁
               "worst_p05_mm": (min([a.get("p05_mm") for a in w.get("objects", []) if a.get("p05_mm") is not None] or [None])),
               "params": {"min_mm": min_mm, "max_angle_deg": max_angle_deg, "up": str(up).upper(),
                          "min_area_mm2": min_area_mm2},
               "detail": {"walls": w, "overhang": o},
               "note": "ok=true 只代表过壁厚/悬垂两道门；连通性与干涉另有 audit_connectivity / audit_interference"})


def print_selftest():
    """自检：1 单位立方体 → 壁厚应≈1000mm（1 单位=1m 场景）、正下方悬垂面应=1（底面）→ 删净。"""
    import bmesh
    me = bpy.data.meshes.new("__dsh_print_selftest")
    ob = bpy.data.objects.new("__dsh_print_selftest", me)
    bpy.context.scene.collection.objects.link(ob)
    ev = {}
    try:
        bm = bmesh.new()
        bmesh.ops.create_cube(bm, size=1.0)
        bm.to_mesh(me)
        bm.free()
        w = json.loads(print_walls(objects=[ob.name], min_mm=1.0))
        o = json.loads(print_overhang(objects=[ob.name], max_angle_deg=45.0))
        ev["walls"] = w["objects"][0]
        ev["overhang"] = {k: o["objects"][0][k] for k in ("faces", "overhang_area_mm2", "overhang_ratio")}
        thick_ok = abs(float(ev["walls"]["min_mm"]) - 1000.0) < 5.0
        ov_ok = int(ev["overhang"]["faces"]) == 6 and 0.0 < float(ev["overhang"]["overhang_ratio"]) < 0.2
        return _j({"ok": bool(thick_ok and ov_ok), "evidence": ev,
                   "expect": {"min_mm": 1000.0, "overhang_faces": 1, "tolerance_mm": 5.0}})
    except Exception as e:
        return _j({"ok": False, "evidence": ev, "error": "%s: %s" % (type(e).__name__, str(e)[:200])})
    finally:
        try:
            bpy.data.objects.remove(ob, do_unlink=True)
            bpy.data.meshes.remove(me, do_unlink=True)
        except Exception:
            pass


def print_help():
    return _j({
        "module": "printcheck.py", "version": PRINT_VERSION,
        "ops": {
            "print_walls": "只读：min_mm 阈值 + max_samples（默认 4000）；回执给 min/p05/median/max、thin_samples、worst 10",
            "print_overhang": "只读：max_angle_deg（默认 45，法线与正下方夹角）、up=X|Y|Z、min_area_mm2 门槛",
            "print_report": "只读：两道门汇总（交付门用）",
            "print_selftest": "自检：1 单位立方体应给出 ≈1000mm 壁厚与 1 个正下方悬垂面",
            "print_help": "本表",
        },
        "units": "1 单位 = scale_length 米；mm = 单位 * scale_length * 1000（可用 mm_per_unit 覆盖）",
        "why_not_3d_print_toolbox": "上游 blend-ai 包装 3D Print Toolbox 插件（默认没启用）；本模块用 BVH 自算，少一个依赖、结果可复算",
        "limits": "开放薄壳测不出厚度（射线打到外面）；凹角处偏乐观 —— 判据看 p05 与 thin_ratio，不看单点最小值",
    })


def print_dispatch(op, args_json):
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
    ops = {"walls": print_walls, "overhang": print_overhang, "report": print_report,
           "selftest": print_selftest, "help": print_help}
    fn = ops.get(str(op))
    if fn is None:
        return _j({"ok": False, "error": "unknown print op", "op": op, "ops": sorted(ops)})
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
        return _j({"ok": False, "error": "参数不匹配: %s" % str(e)[:200], "op": op, "help": print_help()})
    except Exception as e:
        return _j({"ok": False, "error": "%s: %s" % (type(e).__name__, str(e)[:220]), "op": op})


_DshApi = _KIT.Api  # 共享内核（尾部注册行无需改）
import sys as _sys
_K = _sys.modules.get("dsh_rt_kernel")
if _K is not None:
    _K.dsh_print_api = _DshApi({"version": PRINT_VERSION, "dispatch": print_dispatch,
                                "walls": print_walls, "overhang": print_overhang,
                                "report": print_report, "selftest": print_selftest, "help": print_help})
