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

v1.0.5（issue #14）：悬垂默认**排除贴床面** —— 平底零件不再恒判 overhang_ok=false。
    贴床面单列 bed_area_mm2；老口径（含贴床面）保留在 overhang_area_incl_bed_mm2，
    exclude_bed=False 可整体回退。判据见 print_overhang 的 docstring 与 print_help 的 bed_caliber。
"""
import json
import math

import bpy
import mathutils
from mathutils import Vector
from mathutils.bvhtree import BVHTree

PRINT_VERSION = 2   # v2：悬垂默认排除贴床面（issue #14；字段口径见 print_overhang）


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


def _bed_level_flat(level_areas, tol_mm):
    """把「水平朝下」的面按标高聚簇（tol_mm 内算同一层），返回面积最大的那一簇的标高。

    issue #14：贴床面不能用「全局最低点」定义 —— 底板下缘的倒角/碎面会低于平底面
    （报告人实测最低点 -0.6905 mm，而平底面在 z = 0），于是平底面反而被判成悬垂。
    改用「面积最大的水平朝下层的标高」当平台面：平底面的面积通常远大于碎面。
    """
    clusters = []
    for lvl, area in sorted(level_areas, key=lambda t: t[0]):
        if clusters and abs(lvl - clusters[-1]["ref"]) <= tol_mm:
            c = clusters[-1]
            c["area"] += area
            c["wsum"] += lvl * area
            if c["area"] > 0:
                c["ref"] = c["wsum"] / c["area"]
        else:
            clusters.append({"ref": lvl, "area": area, "wsum": lvl * area})
    if not clusters:
        return None, []
    best = max(clusters, key=lambda c: c["area"])
    return best["ref"], clusters


def print_overhang(objects=None, scope="ACTIVE", max_angle_deg=45.0, up="Z", min_area_mm2=0.0,
                   mm_per_unit=None, max_list=20, exclude_bed=True, bed_tol_mm=0.2,
                   bed_mode="flat", bed_horiz_deg=1.0, bed_band_mm=1.0):
    """悬垂：面法线与"正下方"的夹角 <= max_angle_deg 视为需要支撑（0° = 正朝下）。

    v1.0.5（issue #14）：**贴床面默认不再计入悬垂面积** —— 平底零件不再恒判 overhang_ok=false。
      贴床面须同时满足两条：① 水平朝下（法线与正下方夹角 <= bed_horiz_deg，默认 1°）；
      ② 标高落在平台面 ±bed_tol_mm 内。平台面标高由 bed_mode 决定：
        flat（默认）= 面积最大的「水平朝下」层的标高（抗体素碎面/倒角把标高拉低）；
        min         = 所选集合沿 up 轴的最低点（物理打印平台，对碎面敏感）。
      flat 有一道防线 bed_band_mm（默认 1.0）：面积最大的那个水平朝下面若比集合最低点高出
      超过这个距离，就判定"它不像平台面"（半空中的帽檐/屋面、或整件悬空），**退回最低点口径**
      并在 bed_warning 里说明 —— 否则蘑菇件会把整块真实悬垂排掉、让 ok 假绿（实测 200×200 帽
      + 陡锥柱：40000mm² 被吞、ok=true）。
      min_area_mm2 是**净悬垂面积预算**（超过即判超阈），不再兼作"排除贴床面"的手段。
      回退：exclude_bed=False 恢复 v1.0.4 及以前的老口径（贴床面计入悬垂）；
      overhang_area_incl_bed_mm2 始终给含贴床面的老口径数字，便于对照。
    """
    ang = _num(max_angle_deg, "max_angle_deg", 0.0, 90.0)
    # tol 必须 > 0：tol=0 会让「同一平底面被浮点噪声切开」的碎面互相判成非贴床（实测同一几何
    # 90000mm²@0 + 10000mm²@+1e-9mm，tol=0 ⇒ 净悬垂 10000、tol=0.2 ⇒ 0，答案翻转且无警告）。
    tol = _num(bed_tol_mm, "bed_tol_mm", 1e-9, 1e9)
    horiz = _num(bed_horiz_deg, "bed_horiz_deg", 0.0, 90.0)
    mode = str(bed_mode or "flat").strip().lower()
    if mode not in ("flat", "min"):
        return _j({"ok": False, "error": "bed_mode 只支持 flat|min，收到 %r" % bed_mode})
    upv = str(up or "Z").upper()
    axis = {"X": Vector((1, 0, 0)), "Y": Vector((0, 1, 0)), "Z": Vector((0, 0, 1))}.get(upv)
    if axis is None:
        return _j({"ok": False, "error": "up 只支持 X/Y/Z，收到 %r" % up})
    down = -axis
    mmu = _mm_per_unit(mm_per_unit)
    min_area = _num(min_area_mm2, "min_area_mm2", 0.0, 1e12, 0.0)
    obs = _objs(objects, scope)
    cos_lim = math.cos(math.radians(ang))
    cos_horiz = math.cos(math.radians(horiz))

    # ── 第一遍：每个面的（标高 / 面积 / 法线朝下程度）+ 集合最低点 ──
    # 标高用面心（水平面的面心标高即该层标高）；最低点用真实顶点（min 模式要的是物理平台）。
    # v1.0.5 修两个各向异性缩放的旧账（复核实测，v1.0.4 就有）：
    #   ① 法线必须用**逆转置**（`(M⁻¹)ᵀ @ n`）—— 原来 `M.to_3x3() @ n` 在 scale(2,1,1) 下把
    #      真角度 16° 的面算成 49° ⇒ 整块面被丢出悬垂集（漏判）；旋转/等比缩放下两者等价。
    #   ② 面积按**世界坐标三角扇**算 —— 原来 `p.area × (_scale_avg × mmu)²` 在 scale(2,1,1) 下
    #      给 -11.1% 的偏差（1.777e6 vs 真值 2e6 mm²）。
    faces = []      # (ob_index, face_index, level_mm, area_mm2, cos_down)
    lowest = None
    for oi, ob in enumerate(obs):
        me = ob.data
        if len(me.polygons) == 0:
            continue
        mw = ob.matrix_world
        rot = mw.inverted_safe().transposed().to_3x3()
        vco = [v.co.copy() for v in me.vertices]
        for v in me.vertices:
            lv = (mw @ v.co).dot(axis) * mmu
            if lowest is None or lv < lowest:
                lowest = lv
        for i, p in enumerate(me.polygons):
            nrm = (rot @ p.normal)
            if nrm.length < 1e-12:
                continue
            nrm.normalize()
            vs = [mw @ vco[vi] for vi in p.vertices]
            a_world = 0.0
            for k in range(1, len(vs) - 1):
                a_world += (vs[k] - vs[0]).cross(vs[k + 1] - vs[0]).length * 0.5
            faces.append((oi, i, (mw @ p.center).dot(axis) * mmu,
                          a_world * mmu * mmu, nrm.dot(down)))

    # ── 平台面标高 ──
    # bed_band_mm 是「平台面候选」的防线：面积最大的水平朝下面若离集合最低点超过这个距离，
    # 它多半**不是**平台面，而是半空中的帽檐/屋面（蘑菇件）—— 拿它当平台会把整块真实悬垂
    # 排除掉、让 ok 假绿（实测：200×200 帽 + 陡锥柱 ⇒ 40000mm² 被吞、ok=true）。
    # 这种情况一律退回「最低点当平台面」，并把原因写进 bed_warning。
    band = _num(bed_band_mm, "bed_band_mm", 0.0, 1e9)
    cand = None
    if mode == "flat":
        cand, _clusters = _bed_level_flat([(f[2], f[3]) for f in faces if f[4] >= cos_horiz], tol)
    fallback_why = None
    if mode != "flat":
        bed, used = lowest, "min"
    elif cand is None:                      # 没有水平朝下的面（球/回转体）→ 退回最低点
        bed, used = lowest, "min-fallback"
        fallback_why = "没有水平朝下的面（球/回转体？）"
    elif lowest is not None and (cand - lowest) > band:
        bed, used = lowest, "min-fallback"
        fallback_why = ("面积最大的水平朝下面在 %.4f mm，比集合最低点高 %.4f mm（> bed_band_mm=%.4f）"
                        "⇒ 它不像平台面（半空中的帽檐/屋面，或整件悬空）" % (cand, cand - lowest, band))
    else:
        bed, used = cand, "flat"
    if bed is None:
        bed = 0.0

    # ── 第二遍：按平台面把「贴床面」与「真悬垂」分开记账 ──
    out = [None] * len(obs)
    acc = {}
    for oi, ob in enumerate(obs):
        if len(ob.data.polygons) == 0:
            out[oi] = {"name": ob.name, "ok": False, "error": "0 面"}
        else:
            acc[oi] = {"tot": 0.0, "bad_area": 0.0, "bed_area": 0.0, "incl_area": 0.0,
                       "bad": [], "bed": []}
    for (oi, i, lvl, a_mm2, c) in faces:
        a = acc[oi]
        a["tot"] += a_mm2
        if c < cos_lim:
            continue
        a["incl_area"] += a_mm2          # 老口径：凡朝下面都算悬垂
        is_bed = bool(exclude_bed) and (c >= cos_horiz) and (abs(lvl - bed) <= tol)
        row = {"face": int(i), "level_mm": round(lvl, 4),
               "angle_deg": round(math.degrees(math.acos(max(-1.0, min(1.0, c)))), 2),
               "area_mm2": round(a_mm2, 4)}
        if is_bed:
            a["bed_area"] += a_mm2
            if len(a["bed"]) < int(max_list):
                a["bed"].append(row)
        else:
            a["bad_area"] += a_mm2
            if len(a["bad"]) < int(max_list):
                a["bad"].append(row)

    bed_warning = None
    if fallback_why:
        bed_warning = ("%s；已改按最低点 %.4f mm 当平台面。⚠ 别为了「让它平」而随意调大 bed_band_mm —— "
                       "那会把半空中的大平面当平台面、把整块真实悬垂吞掉（宁可保守多报）。"
                       "想回退 v1.0.4 老口径（贴床面计入悬垂）：exclude_bed=false"
                       % (fallback_why, (lowest if lowest is not None else 0.0)))
    elif used == "flat" and lowest is not None and (bed - lowest) > tol:
        bed_warning = ("贴床面标高 %.4f 比集合最低点 %.4f 高 %.4f mm（倒角/碎面低于平底面？）"
                       "—— 若最低处才是真实接触面，调小 bed_band_mm 或改用 bed_mode=min" % (bed, lowest, bed - lowest))

    for oi, ob in enumerate(obs):
        a = acc.get(oi)
        if a is None:
            continue
        bad_area, bed_area, incl_area, tot = a["bad_area"], a["bed_area"], a["incl_area"], a["tot"]
        over_min = bad_area > min_area
        hint = ("净悬垂面积 %.2f mm² 超预算 %.2f mm²：需要支撑或改朝向" % (bad_area, min_area)) if over_min \
            else "无超预算悬垂"
        if bed_area > 0:
            hint += "；已排除贴床面 %.2f mm²（占全表面 %.1f%%）" % (
                bed_area, (100.0 * bed_area / tot) if tot > 0 else 0.0)
        rec = {"name": ob.name, "ok": not over_min, "up": upv, "max_angle_deg": ang,
               "faces": len(ob.data.polygons), "total_area_mm2": round(tot, 4),
               # 主口径（v1.0.5 起）：不含贴床面的净悬垂
               "overhang_area_mm2": round(bad_area, 4),
               "overhang_ratio": round(bad_area / tot, 4) if tot > 0 else None,
               # 贴床面单列（issue #14 建议 3）
               "bed_area_mm2": round(bed_area, 4),
               "bed_ratio": round(bed_area / tot, 4) if tot > 0 else None,
               # 老口径（<= v1.0.4：贴床面计入悬垂），保留以便对照与回退
               "overhang_area_incl_bed_mm2": round(incl_area, 4),
               "exclude_bed": bool(exclude_bed), "bed_mode": used, "bed_tol_mm": tol,
               "bed_horiz_deg": horiz, "bed_band_mm": band, "bed_level_mm": round(bed, 4),
               "lowest_mm": (round(lowest, 4) if lowest is not None else None),
               "overhang_faces_listed": len(a["bad"]), "samples": a["bad"],
               "bed_faces_listed": len(a["bed"]), "bed_samples": a["bed"],
               "hint": hint}
        if bed_warning:
            rec["bed_warning"] = bed_warning
        out[oi] = rec
    return _j({"ok": all(r.get("ok") for r in out), "objects": out,
               "bed_level_mm": round(bed, 4), "bed_mode": used,
               "note": ("口径：法线与正下方夹角 <= max_angle_deg（默认 45°）算需支撑；"
                        "其中「水平朝下且落在平台面 ±bed_tol_mm 内」的面判为贴床面、不计入 overhang_area_mm2"
                        "（平台面按 bed_mode=%s；exclude_bed=False 可回退老口径）；"
                        "平台面是**全选择集共享**的单一标高（bed_level_mm），不是逐对象各自推断 —— "
                        "所以同一件单独测 vs 与别件一起测，结论可能不同（想逐件判：一件一次调用）；"
                        "min_area_mm2 是净悬垂面积预算" % used)})


def print_report(objects=None, scope="ACTIVE", min_mm=1.0, max_angle_deg=45.0, up="Z",
                 min_area_mm2=0.0, mm_per_unit=None, max_samples=4000,
                 exclude_bed=True, bed_tol_mm=0.2, bed_mode="flat", bed_horiz_deg=1.0, bed_band_mm=1.0):
    """汇总回执：壁厚 + 悬垂 + 网格可造性，给一个总判定（供交付门用）。

    v1.0.5（issue #14）：悬垂一道默认排除贴床面（口径见 print_overhang 的 docstring）；
    贴床面积单列在 objects[].bed_area_mm2，老口径在 objects[].overhang_area_incl_bed_mm2。
    """
    w = json.loads(print_walls(objects=objects, scope=scope, min_mm=min_mm, max_samples=max_samples,
                               mm_per_unit=mm_per_unit))
    o = json.loads(print_overhang(objects=objects, scope=scope, max_angle_deg=max_angle_deg, up=up,
                                  min_area_mm2=min_area_mm2, mm_per_unit=mm_per_unit,
                                  exclude_bed=exclude_bed, bed_tol_mm=bed_tol_mm,
                                  bed_mode=bed_mode, bed_horiz_deg=bed_horiz_deg,
                                  bed_band_mm=bed_band_mm))
    if not w.get("ok") and any(r.get("error") for r in w.get("objects", [])):
        return _j({"ok": False, "stage": "walls", "walls": w, "overhang": o})
    rows = []
    for a, b in zip(w.get("objects", []), o.get("objects", [])):
        rows.append({"name": a.get("name"), "ok": bool(a.get("ok") and b.get("ok")),
                     "min_mm": a.get("min_mm"), "thin_samples": a.get("thin_samples"),
                     "overhang_area_mm2": b.get("overhang_area_mm2"), "overhang_ratio": b.get("overhang_ratio"),
                     # v1.0.5（issue #14）：贴床面积单列 + 老口径对照，门判据可以只用净悬垂
                     "bed_area_mm2": b.get("bed_area_mm2"), "bed_level_mm": b.get("bed_level_mm"),
                     "overhang_area_incl_bed_mm2": b.get("overhang_area_incl_bed_mm2"),
                     # 平台面判据的自证警告必须能到达聚合层 —— 只留 detail 里等于对门隐形
                     "bed_warning": b.get("bed_warning")})
    _thin_total = sum(int(a.get("thin_samples") or 0) for a in w.get("objects", []))
    _mins = [a.get("min_mm") for a in w.get("objects", []) if a.get("min_mm") is not None]
    # v1.0.5：overhang 那一侧的自证警告（平台面判据可能判错）必须在顶层可见 —— 门/交付脚本读顶层与 rows
    _owarn = sorted(set(str(b["bed_warning"]) for b in o.get("objects", []) if b.get("bed_warning")))
    return _j({"ok": all(r["ok"] for r in rows), "objects": rows,
               # v0.9.6（整合方案 P0）：门友好汇总 —— 判据用顶层字段，别让 gate spec 去翻 objects[]
               "walls_ok": all(bool(a.get("ok")) for a in w.get("objects", [])),
               "overhang_ok": all(bool(b.get("ok")) for b in o.get("objects", [])),
               "thin_samples_total": _thin_total,
               "worst_min_mm": (min(_mins) if _mins else None),
               # 门判据建议用 p05：实测体素球有 0.05% 的近零离群样本，判 min 会假报薄壁
               "worst_p05_mm": (min([a.get("p05_mm") for a in w.get("objects", []) if a.get("p05_mm") is not None] or [None])),
               "warnings": _owarn,
               "params": {"min_mm": min_mm, "max_angle_deg": max_angle_deg, "up": str(up).upper(),
                           "min_area_mm2": min_area_mm2, "exclude_bed": bool(exclude_bed),
                           "bed_tol_mm": bed_tol_mm, "bed_mode": o.get("bed_mode"),
                           "bed_horiz_deg": bed_horiz_deg, "bed_band_mm": bed_band_mm,
                           "bed_level_mm": o.get("bed_level_mm")},
               "detail": {"walls": w, "overhang": o},
               "note": ("ok=true 只代表过壁厚/悬垂两道门；连通性与干涉另有 audit_connectivity / audit_interference；"
                        "warnings 非空时说明悬垂的平台面判据可能判错（见 print_overhang 的 bed_caliber）")})


def print_selftest():
    """自检：四种形状验证"贴床面被排除、真悬垂仍被计入"（issue #14 的回归锁）。

    1) 1 单位立方体：壁厚 ≈1000mm；贴床底面 1e6 mm² 必须被排除 → 净悬垂 = 0、ok = true。
       老口径（exclude_bed=False）必须复现 v1.0.4 的数字（1e6 / 0.1667 / ok=false）—— 这就是 issue #14。
    2) 底板 + 悬臂：贴床面 4e6、悬臂底面（离台 500mm）必须仍计入悬垂 1e6。
    3) 平底面下 0.5mm 有碎块（报告人的形状，在 bed_band_mm 内）：flat 认平底面为平台（碎块计入并给
       bed_warning）；min 把平台压到碎块那层 → 平底面反被判悬垂（复现旧口径的失效形状）。
    4) 蘑菇件（细柱 + 200mm 宽帽）：帽底 40000mm² 是**大悬垂不是平台面** —— flat 若把它当平台面就会
       整块吞掉真实悬垂、让 ok 假绿；必须由 bed_band_mm 退回 min 口径（bed_mode=min-fallback）。
    """
    import bmesh
    P = "__dsh_print_selftest_"
    made = []
    ev = {}

    def box(name, size, loc=(0.0, 0.0, 0.0)):
        """size = 全尺寸；loc 直接烘进顶点（不靠对象变换，免 matrix_world 未刷新的坑）。"""
        m = bpy.data.meshes.new(name)
        o = bpy.data.objects.new(name, m)
        bpy.context.scene.collection.objects.link(o)
        bm = bmesh.new()
        bmesh.ops.create_cube(bm, size=1.0)
        bmesh.ops.scale(bm, vec=Vector(size), verts=list(bm.verts))
        bmesh.ops.translate(bm, verts=list(bm.verts), vec=Vector(loc))
        bm.to_mesh(m)
        bm.free()
        made.append((o, m))
        return o

    def two_box(name, size_a, loc_a, size_b, loc_b):
        """两个盒子塞进**同一个 mesh**（一件里同时有贴床面与真悬垂 ⇒ 用来锁 overhang_ratio 的分母）。"""
        m = bpy.data.meshes.new(name)
        o = bpy.data.objects.new(name, m)
        bpy.context.scene.collection.objects.link(o)
        bm = bmesh.new()
        bmesh.ops.create_cube(bm, size=1.0)
        bmesh.ops.scale(bm, vec=Vector(size_a), verts=list(bm.verts))
        bmesh.ops.translate(bm, verts=list(bm.verts), vec=Vector(loc_a))
        r = bmesh.ops.create_cube(bm, size=1.0)
        vs = list(r["verts"])
        bmesh.ops.scale(bm, vec=Vector(size_b), verts=vs)
        bmesh.ops.translate(bm, verts=vs, vec=Vector(loc_b))
        bm.to_mesh(m)
        bm.free()
        made.append((o, m))
        return o

    def tilt_face(name, size, loc, tilt_deg):
        """薄片绕**其底面面心**倾斜 tilt_deg ⇒ 面心仍落在原标高，但法线不再朝正下方。

        用途：锁 bed_horiz_deg —— 若实现忘了「必须水平才算贴床面」，这个面会被误当贴床面排除。
        """
        m = bpy.data.meshes.new(name)
        o = bpy.data.objects.new(name, m)
        bpy.context.scene.collection.objects.link(o)
        bm = bmesh.new()
        bmesh.ops.create_cube(bm, size=1.0)
        bmesh.ops.scale(bm, vec=Vector(size), verts=list(bm.verts))
        cent = Vector((0.0, 0.0, -size[2] / 2.0))          # 底面面心（缩放后）
        bmesh.ops.rotate(bm, cent=cent, verts=list(bm.verts),
                         matrix=mathutils.Matrix.Rotation(math.radians(tilt_deg), 3, "Y"))
        bmesh.ops.translate(bm, verts=list(bm.verts),
                            vec=Vector((loc[0], loc[1], loc[2] + size[2] / 2.0)))
        bm.to_mesh(m)
        bm.free()
        made.append((o, m))
        return o

    def near(a, b, eps=4.0):
        return a is not None and abs(float(a) - float(b)) <= eps

    def ov(names, **kw):
        return json.loads(print_overhang(objects=names, max_angle_deg=45.0, **kw))

    try:
        fails = []
        # ── 1) 平底立方体：贴床面不得计入悬垂（issue #14 的核心） ──
        cube = box(P + "cube", (1.0, 1.0, 1.0))
        w = json.loads(print_walls(objects=[cube.name], min_mm=1.0))
        r1 = ov([cube.name])["objects"][0]
        ev["walls"] = {k: w["objects"][0][k] for k in ("min_mm", "thin_samples")}
        ev["cube"] = {k: r1[k] for k in ("faces", "bed_area_mm2", "overhang_area_mm2",
                                         "overhang_area_incl_bed_mm2", "overhang_ratio", "ok")}
        if not near(ev["walls"]["min_mm"], 1000.0, 5.0):
            fails.append("壁厚 min_mm=%.3f 应 ≈1000mm" % float(ev["walls"]["min_mm"]))
        if not (near(r1["bed_area_mm2"], 1e6) and near(r1["overhang_area_mm2"], 0.0)
                and near(r1["overhang_area_incl_bed_mm2"], 1e6) and near(r1["overhang_ratio"], 0.0, 1e-6)):
            fails.append("平底立方体：净悬垂应=0、贴床面应=1e6 mm²、老口径应=1e6 mm²（实得 %s）"
                         % json.dumps(ev["cube"]))
        if r1["ok"] is not True:
            fails.append("平底立方体应判 ok=true（issue #14：平底零件不再恒判不合格）")
        r1b = ov([cube.name], exclude_bed=False)["objects"][0]
        ev["cube_legacy"] = {k: r1b[k] for k in ("overhang_area_mm2", "overhang_ratio", "ok")}
        if not (near(r1b["overhang_area_mm2"], 1e6) and near(r1b["overhang_ratio"], 0.1667, 1e-3)
                and r1b["ok"] is False):
            fails.append("exclude_bed=False 应复现 v1.0.4 老口径 1e6 / 0.1667 / ok=false（实得 %s）"
                         % json.dumps(ev["cube_legacy"]))

        # ── 2) 底板 + 悬臂：真悬垂不许被平台面吞掉 ──
        base = box(P + "base", (2.0, 2.0, 0.5), (0.0, 0.0, 0.0))     # 底面 4 m² @ z=-0.25
        arm = box(P + "arm", (1.0, 1.0, 0.5), (1.0, 0.0, 0.5))       # 底面 1 m² @ z=+0.25（离台 500mm）
        o2 = ov([base.name, arm.name])
        rb, ra = o2["objects"][0], o2["objects"][1]
        ev["cantilever"] = {"bed_level_mm": o2["bed_level_mm"],
                            "base": {k: rb[k] for k in ("bed_area_mm2", "overhang_area_mm2")},
                            "arm": {k: ra[k] for k in ("bed_area_mm2", "overhang_area_mm2")}}
        if not (near(rb["bed_area_mm2"], 4e6) and near(rb["overhang_area_mm2"], 0.0)):
            fails.append("底板：贴床面应=4e6、净悬垂应=0（实得 %s）" % json.dumps(ev["cantilever"]["base"]))
        if not (near(ra["overhang_area_mm2"], 1e6) and near(ra["bed_area_mm2"], 0.0, 1e-6)):
            fails.append("悬臂底面（离台 500mm）必须计入悬垂=1e6、贴床面=0（实得 %s）"
                         % json.dumps(ev["cantilever"]["arm"]))
        if o2["ok"] is not False:
            fails.append("有真悬垂时 ok 必须为 false")

        # ── 3) 平底面下 0.5mm 有碎块（在 bed_band_mm 内）：flat 仍认平底面为平台 ──
        base2 = box(P + "base2", (2.0, 2.0, 0.5), (0.0, 0.0, 0.25))    # 底面 4 m² @ z=0
        chip = box(P + "chip", (0.2, 0.2, 0.001), (0.5, 0.5, 0.0))     # 底面 0.04 m² @ z=-0.5mm
        fl = ov([base2.name, chip.name])
        fb, fc = fl["objects"][0], fl["objects"][1]
        mn = ov([base2.name, chip.name], bed_mode="min")
        mb = mn["objects"][0]
        ev["sliver"] = {
            "flat": {"bed_level_mm": fl["bed_level_mm"], "bed_mode": fl["bed_mode"],
                     "base_bed_mm2": fb["bed_area_mm2"],
                     "base_over_mm2": fb["overhang_area_mm2"], "chip_over_mm2": fc["overhang_area_mm2"],
                     "bed_warning": bool(fb.get("bed_warning"))},
            "min": {"bed_level_mm": mn["bed_level_mm"], "base_over_mm2": mb["overhang_area_mm2"]}}
        if not (near(fl["bed_level_mm"], 0.0, 1e-6) and fl["bed_mode"] == "flat"
                and near(fb["bed_area_mm2"], 4e6)
                and near(fb["overhang_area_mm2"], 0.0) and near(fc["overhang_area_mm2"], 4e4)
                and bool(fb.get("bed_warning"))):
            fails.append("flat 模式应认平底面为平台（4e6 排除）、碎块计入 + 给 bed_warning（实得 %s）"
                         % json.dumps(ev["sliver"]["flat"]))
        if not (near(mn["bed_level_mm"], -0.5, 1e-3) and near(mb["overhang_area_mm2"], 4e6)):
            fails.append("min 模式应复现 issue #14 的失效形状（平台被压到碎块层 ⇒ 平底面被当悬垂 4e6，实得 %s）"
                         % json.dumps(ev["sliver"]["min"]))

        # ── 4) 蘑菇件（细柱 + 宽帽）：帽底 40000mm² 是大悬垂、**不是**平台面 ──
        # 没有 bed_band_mm 这道防线时，flat 会把帽底当平台面 ⇒ 整块真实悬垂被吞、ok 假绿（实测过）。
        stalk = box(P + "stalk", (0.02, 0.02, 0.02), (0.0, 0.0, 0.01))   # 20mm 柱，z ∈ [0, 20]mm
        cap = box(P + "cap", (0.2, 0.2, 0.02), (0.0, 0.0, 0.03))         # 200mm 帽，z ∈ [20, 40]mm
        mush = ov([stalk.name, cap.name])
        mcap = mush["objects"][1]
        ev["mushroom"] = {"ok": mush["ok"], "bed_mode": mush["bed_mode"], "bed_level_mm": mush["bed_level_mm"],
                          "cap_over_mm2": mcap["overhang_area_mm2"], "cap_bed_mm2": mcap["bed_area_mm2"],
                          "warning": bool(mcap.get("bed_warning"))}
        if not (mush["bed_mode"] == "min-fallback" and near(mush["bed_level_mm"], 0.0, 1e-6)
                and near(mcap["overhang_area_mm2"], 4e4) and near(mcap["bed_area_mm2"], 0.0, 1e-6)
                and mush["ok"] is False and bool(mcap.get("bed_warning"))):
            fails.append("蘑菇件：帽底 40000mm² 必须仍算悬垂（flat 拿它当平台面就是假绿）——"
                         "期望 bed_mode=min-fallback / ok=false / bed_warning（实得 %s）"
                         % json.dumps(ev["mushroom"]))

        # ── 5) 平台标高上的**斜面**：不许被当成贴床面（锁 bed_horiz_deg） ──
        # 薄片绕自身底面面心倾斜 30° ⇒ 面心仍落在平台面上方 0.1mm（在 bed_tol_mm 内），
        # 但法线离正下方 30°。若实现忘了「必须水平才算贴床」这条，它会被误排除 ⇒ 净悬垂变 0。
        plate5 = box(P + "plate5", (2.0, 2.0, 0.5), (0.0, 0.0, 0.25))      # 底面 4 m² @ z=0（平台面）
        facet = tilt_face(P + "facet", (0.0004, 0.0004, 0.0001), (1.5, 1.5, 0.0001), 30.0)
        #          0.4×0.4mm 薄片，底面面心在 z=+0.1mm（tol=0.2 内），最低角恰好回到 z=0
        tl = ov([plate5.name, facet.name], bed_mode="min")
        tp, tf = tl["objects"][0], tl["objects"][1]
        ev["tilted"] = {"bed_level_mm": tl["bed_level_mm"], "bed_mode": tl["bed_mode"],
                        "plate_bed_mm2": tp["bed_area_mm2"], "plate_over_mm2": tp["overhang_area_mm2"],
                        "facet_over_mm2": tf["overhang_area_mm2"], "facet_bed_mm2": tf["bed_area_mm2"]}
        if not (near(tp["bed_area_mm2"], 4e6) and near(tf["overhang_area_mm2"], 0.16, 0.05)
                and near(tf["bed_area_mm2"], 0.0, 1e-6)):
            fails.append("斜面（30°、面心在平台面 +0.1mm）必须仍算悬垂 0.16mm²，不许被当贴床面（实得 %s）"
                         % json.dumps(ev["tilted"]))

        # ── 6) 一件里同时有「贴床面」与「真悬垂」：锁 overhang_ratio 的分母是全表面 ──
        tee = two_box(P + "tee", (2.0, 2.0, 0.5), (0.0, 0.0, 0.0), (1.0, 1.0, 0.5), (1.0, 0.0, 0.5))
        tt = ov([tee.name])["objects"][0]
        _tot, _bad = float(tt["total_area_mm2"]), float(tt["overhang_area_mm2"])
        ev["tee"] = {"total_area_mm2": _tot, "bed_area_mm2": tt["bed_area_mm2"],
                     "overhang_area_mm2": _bad, "overhang_ratio": tt["overhang_ratio"],
                     "bad_over_total": round(_bad / _tot, 4) if _tot > 0 else None}
        if not (near(tt["bed_area_mm2"], 4e6) and near(_bad, 1e6) and near(tt["overhang_ratio"], 0.0625, 1e-3)
                and abs(float(tt["overhang_ratio"]) - _bad / _tot) <= 1e-3):
            fails.append("单件 T 形：贴床 4e6 / 悬垂 1e6 / 比值 = 1e6÷16e6 = 0.0625（分母是全表面，实得 %s）"
                         % json.dumps(ev["tee"]))

        # ── 7) 非均匀缩放：面积必须按世界坐标算（旧口径 `_scale_avg` 偏 -11%） ──
        cube7 = box(P + "cube7", (1.0, 1.0, 1.0))
        cube7.scale = (2.0, 1.0, 1.0)
        bpy.context.view_layer.update()          # 让 matrix_world 反映 scale
        r7 = ov([cube7.name])["objects"][0]
        ev["aniso_area"] = {"bed_area_mm2": r7["bed_area_mm2"], "total_area_mm2": r7["total_area_mm2"]}
        # 底面真值 = 2m × 1m = 2 m² = 2e6 mm²；总表面 = 2×(2×1)+2×(2×1)+2×(1×1) = 10 m² = 1e7 mm²
        # 旧口径（(_scale_avg=1.333)²）会给 1.777e6 / 8.888e6
        if not (near(r7["bed_area_mm2"], 2e6, 10.0) and near(r7["total_area_mm2"], 1e7, 50.0)):
            fails.append("非均匀缩放 (2,1,1) 的 1m 立方体：底面应=2e6、总表面应=1e7 mm²（实得 %s）"
                         % json.dumps(ev["aniso_area"]))

        # ── 8) 非均匀缩放下的**法线**：必须用逆转置（旧口径把 16° 的面算成 49° ⇒ 整块漏判） ──
        plate8 = box(P + "plate8", (2.0, 2.0, 0.5), (0.0, 0.0, 0.25))          # 底面 4 m² @ z=0
        facet8 = tilt_face(P + "facet8", (0.0004, 0.0004, 0.0001), (1.5, 1.5, 0.0001), 30.0)
        facet8.scale = (2.0, 1.0, 1.0)
        bpy.context.view_layer.update()
        t8 = ov([plate8.name, facet8.name], bed_mode="min")
        f8 = t8["objects"][1]
        ev["aniso_normal"] = {"facet_over_mm2": f8["overhang_area_mm2"], "facet_bed_mm2": f8["bed_area_mm2"]}
        if not (f8["overhang_area_mm2"] > 0.1 and near(f8["bed_area_mm2"], 0.0, 1e-6)):
            fails.append("非均匀缩放下斜面漏判：真法线离正下方 ≈16°（必须计入悬垂），"
                         "`M @ n` 会把它算成 49° 整块丢掉（实得 %s）" % json.dumps(ev["aniso_normal"]))

        return _j({"ok": not fails, "fails": fails, "evidence": ev,
                   "expect": {"walls_min_mm": 1000.0, "cube_net_overhang_mm2": 0.0,
                              "cube_bed_mm2": 1e6, "cube_legacy_overhang_mm2": 1e6,
                              "arm_overhang_mm2": 1e6, "base_bed_mm2": 4e6,
                              "cap_overhang_mm2": 4e4, "facet_overhang_mm2": 0.16,
                              "tee_overhang_ratio": 0.0625, "aniso_bed_mm2": 2e6,
                              "tolerance_mm2": 4.0}})
    except Exception as e:
        return _j({"ok": False, "evidence": ev, "error": "%s: %s" % (type(e).__name__, str(e)[:200])})
    finally:
        for o, m in made:
            try:
                bpy.data.objects.remove(o, do_unlink=True)
            except Exception:
                pass
            try:
                bpy.data.meshes.remove(m, do_unlink=True)
            except Exception:
                pass


def print_help():
    return _j({
        "module": "printcheck.py", "version": PRINT_VERSION,
        "ops": {
            "print_walls": "只读：min_mm 阈值 + max_samples（默认 4000）；回执给 min/p05/median/max、thin_samples、worst 10",
            "print_overhang": ("只读：max_angle_deg（默认 45，法线与正下方夹角）、up=X|Y|Z、"
                               "min_area_mm2（净悬垂面积预算）、exclude_bed（默认 true）、bed_tol_mm（0.2）、"
                               "bed_mode=flat|min、bed_horiz_deg（1.0）、bed_band_mm（1.0，flat 的防线）；"
                               "贴床面单列 bed_area_mm2，老口径 overhang_area_incl_bed_mm2"),
            "print_report": "只读：两道门汇总（交付门用）；悬垂一道默认排除贴床面；顶层 warnings[] 带平台面判据的自证警告",
            "print_selftest": "自检：8 形状 —— 平底立方体净悬垂=0 / 悬臂仍计入 / 平底碎块 / 蘑菇帽不许假绿 / 斜面不许当贴床 / 单件 T 比值 / 非均匀缩放的面积真值与法线不漏判",
            "print_help": "本表",
        },
        "units": "1 单位 = scale_length 米；mm = 单位 * scale_length * 1000（可用 mm_per_unit 覆盖）",
        "bed_caliber": ("贴床面 = 水平朝下（<= bed_horiz_deg）且标高在平台面 ±bed_tol_mm 内；"
                        "平台面 flat（默认）取面积最大的水平朝下层标高、min 取集合最低点；"
                        "flat 还要求该层离最低点不超过 bed_band_mm（默认 1.0，即「底缘碎面/桥接容差」），"
                        "否则退回 min 口径并给 bed_warning。**bed_band_mm 内的差值判不出来**："
                        "距台 ≤1mm 的宽面与「底缘碎面」是同一形状，flat 一律算贴床（FDM 能桥接）；"
                        "这类不确定必须靠 bed_warning / print_report 顶层 warnings 暴露，"
                        "gate 的 print preset 已把 warnings 非空算成 degraded（不假绿）。"
                        "flat/min 都几何自推、位置无关：整件悬空时最低点就是它自己 —— 要按绝对平台判请先落台，"
                        "或用 exclude_bed=False 的保守口径（= v1.0.4 老口径，贴床面计入悬垂）"),
        "why_not_3d_print_toolbox": "上游 blend-ai 包装 3D Print Toolbox 插件（默认没启用）；本模块用 BVH 自算，少一个依赖、结果可复算",
        "limits": ("开放薄壳测不出厚度（射线打到外面）；凹角处偏乐观 —— 判据看 p05 与 thin_ratio，不看单点最小值；"
                   "贴床判据是**位置无关**的（平台面由几何自推）：整件悬空/未落台时 flat 依旧会把它面积最大的"
                   "水平朝下面当平台面（想让悬空件的最底面也算悬垂，用 exclude_bed=False 的保守口径，"
                   "或先把件落到 z=0；bed_band_mm 只在「该面离最低点实在太远」时救场）；"
                   "bed_mode=min 对倒角/碎面敏感（会把平台压低 ⇒ 平底面反被判悬垂）"),
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
