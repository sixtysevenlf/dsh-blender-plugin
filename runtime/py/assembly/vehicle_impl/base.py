# -*- coding: utf-8 -*-
"""Split from vehicle.py; behavior preserved by the facade."""
from .common import *  # noqa: F401,F403

def _vb_prepare(spec, name, kw, wheels):
    """spec 解析 + 清同名旧对象；返回 (sp, nm, L)。"""
    if isinstance(spec, dict) and spec:
        kw = dict({k: x for k, x in spec.items() if k in DEFAULTS}, **kw)
    sp = vehicle_spec(**kw)
    sp["_profile"] = _profile_points(sp)
    L = sp["length_mm"] / 1000.0
    nm = str(name or "VehicleShell")
    for nmx in [nm] + ([nm + "_wheel_%s" % s for s in ("fl", "fr", "rl", "rr")] if wheels else []):
        o = bpy.data.objects.get(nmx)
        if o is not None:
            me0 = o.data if o.type == "MESH" else None
            bpy.data.objects.remove(o, do_unlink=True)
            if me0 is not None and me0.users == 0:
                bpy.data.meshes.remove(me0, do_unlink=True)
    return sp, nm, L


def _vb_stations(sp, stations, L):
    """站位：均匀 + 轮缘处加密；返回排序去重的 xs。

    轮眉在 dx=r+clear 处是断崖式的，均匀站位（0.19m 一段）的弦会直接穿过轮子后缘（实测 58 对面相交）。
    """
    r_w = sp["wheel_r_mm"] / 1000.0
    clear_w = sp["arch_clear_mm"] / 1000.0
    fx_w = sp["front_axle_from_front_mm"] / 1000.0
    rx_w = fx_w + sp["wheelbase_mm"] / 1000.0
    xs = [L * i / float(max(1, int(stations) - 1)) for i in range(int(stations))]
    for wx in (fx_w, rx_w):
        for off in (0.0, -r_w, r_w, -(r_w + clear_w), (r_w + clear_w),
                    -0.5 * r_w, 0.5 * r_w, -0.9 * r_w, 0.9 * r_w):
            xw = wx + off
            if 0.0 <= xw <= L:
                xs.append(xw)
    return sorted(set(round(x, 6) for x in xs))


def _vb_shell(sp, xs, nm, section_pts):
    """逐站超椭圆截面 → 桥接成壳（首尾封口 + 重算法线）；返回 ob。"""
    me = bpy.data.meshes.new(nm)
    bm = bmesh.new()
    rings = []
    for x in xs:
        pts = _section(sp, x, n=int(section_pts))
        rings.append([bm.verts.new(p) for p in pts])
    for a, b in zip(rings, rings[1:]):
        for i in range(len(a)):
            j = (i + 1) % len(a)
            try:
                bm.faces.new((a[i], a[j], b[j], b[i]))
            except Exception:
                pass
    for ring in (rings[0], rings[-1]):
        try:
            bm.faces.new(ring)
        except Exception:
            pass
    bmesh.ops.recalc_face_normals(bm, faces=bm.faces)
    bm.to_mesh(me)
    bm.free()
    ob = bpy.data.objects.new(nm, me)
    bpy.context.scene.collection.objects.link(ob)
    return ob


def _vb_arch_cut(ob, sp, nm):
    """轮眉精修：布尔减去略大的轮圆柱（半径 r + arch_clear）。

    解析轮眉已把剖面抬起来，但轮缘处是断崖，粗站位下弦会切进轮子（实测 20 对面相交）。
    布尔之后「轮与壳零互穿」是**构造保证**，不依赖站位密度。
    """
    r_b = sp["wheel_r_mm"] / 1000.0
    ww_b = sp["wheel_w_mm"] / 1000.0
    sink_b = sp["wheel_sink_frac"]
    clear_b = sp["arch_clear_mm"] / 1000.0
    fx_b = sp["front_axle_from_front_mm"] / 1000.0
    rx_b = fx_b + sp["wheelbase_mm"] / 1000.0
    wc_z_b = r_b * (1.0 - sink_b)
    cutters = []
    for ax_b in (fx_b, rx_b):
        for sy_b in (1.0, -1.0):
            cme = bpy.data.meshes.new(nm + "_cut")
            cbm = bmesh.new()
            bmesh.ops.create_cone(cbm, cap_ends=True, cap_tris=False, segments=32,
                                  radius1=r_b + clear_b, radius2=r_b + clear_b, depth=ww_b * 1.8,
                                  matrix=__import__("mathutils").Matrix.Rotation(math.radians(90), 4, "X"))
            cbm.to_mesh(cme)
            cbm.free()
            cob = bpy.data.objects.new(nm + "_cut", cme)
            cob.location = (ax_b, sy_b * sp["track_mm"] / 1000.0 / 2.0, wc_z_b)
            bpy.context.scene.collection.objects.link(cob)
            cutters.append(cob)
    for cob in cutters:
        md = ob.modifiers.new("arch_cut", "BOOLEAN")
        md.operation = "DIFFERENCE"
        md.object = cob
    for o in bpy.context.selected_objects:
        o.select_set(False)
    ob.select_set(True)
    bpy.context.view_layer.objects.active = ob
    for md in list(ob.modifiers):
        try:
            bpy.ops.object.modifier_apply(modifier=md.name)
        except Exception:
            pass
    for cob in cutters:
        cme = cob.data
        bpy.data.objects.remove(cob, do_unlink=True)
        if cme is not None and cme.users == 0:
            try:
                bpy.data.meshes.remove(cme, do_unlink=True)
            except Exception:
                pass


def _vb_make_wheel(nm, sid, axle, x, sy, r, ww, zc, sp, mmu):
    """单轮：24 段圆锥（轴向 X）；返回 (对象名, wheel_info 行)。"""
    wme = bpy.data.meshes.new(nm + "_wheel_" + sid)
    wbm = bmesh.new()
    bmesh.ops.create_cone(wbm, cap_ends=True, cap_tris=False, segments=24,
                          radius1=r, radius2=r, depth=ww,
                          matrix=__import__("mathutils").Matrix.Rotation(math.radians(90), 4, "X"))
    wbm.to_mesh(wme)
    wbm.free()
    wob = bpy.data.objects.new(nm + "_wheel_" + sid, wme)
    wob.location = (x, sy * sp["track_mm"] / 1000.0 / 2.0, zc)
    bpy.context.scene.collection.objects.link(wob)
    return wob.name, {"side": sid, "axle": axle, "x_mm": round(x * mmu, 1),
                      "z_mm": round(zc * mmu, 1), "r_mm": round(r * mmu, 1)}


def _vb_wheels(sp, nm, mmu):
    """四个轮（前后轴 × 左右）；返回 (wheel_objects, wheel_info)。"""
    made = []
    wheel_info = []
    r = sp["wheel_r_mm"] / 1000.0
    ww = sp["wheel_w_mm"] / 1000.0
    sink = sp["wheel_sink_frac"]
    fx = sp["front_axle_from_front_mm"] / 1000.0
    rx = fx + sp["wheelbase_mm"] / 1000.0
    zc = r * (1.0 - sink)
    for sid, sy in (("fl", 1.0), ("fr", -1.0)):
        name, info = _vb_make_wheel(nm, sid, "front", fx, sy, r, ww, zc, sp, mmu)
        made.append(name)
        wheel_info.append(info)
    for sid, sy in (("rl", 1.0), ("rr", -1.0)):
        name, info = _vb_make_wheel(nm, sid, "rear", rx, sy, r, ww, zc, sp, mmu)
        made.append(name)
        wheel_info.append(info)
    return made, wheel_info


def _vb_markers(sp, nm, L):
    """轴/地面/头尾空物体标记；返回 mk 列表。"""
    coll = bpy.data.collections.get(nm + "_marks")
    if coll is None:
        coll = bpy.data.collections.new(nm + "_marks")
        bpy.context.scene.collection.children.link(coll)
    for o in list(coll.objects):
        bpy.data.objects.remove(o, do_unlink=True)
    fx = sp["front_axle_from_front_mm"] / 1000.0
    rx = fx + sp["wheelbase_mm"] / 1000.0
    mk = []
    for an, co in (("front_axle", (fx, 0.0, 0.0)), ("rear_axle", (rx, 0.0, 0.0)),
                   ("ground_front", (fx, 0.0, 0.0)), ("nose", (0.0, 0.0, 0.5 * sp["height_mm"] / 1000.0)),
                   ("tail", (L, 0.0, 0.5 * sp["height_mm"] / 1000.0))):
        e = bpy.data.objects.new(nm + "_M_" + an, None)
        e.empty_display_type = "PLAIN_AXES"
        e.empty_display_size = 0.1
        e.location = co
        coll.objects.link(e)
        mk.append(e.name)
    try:
        bpy.context.view_layer.update()
    except Exception:
        pass
    return mk


def _vb_payload(ob, sp, nm, xs, stations, section_pts, wheel_info, made, mk, mmu):
    """vehicle_base 回执。"""
    d = [round(float(x) * mmu, 1) for x in ob.dimensions]
    return _j({"ok": True, "object": ob.name, "size_mm": {"len": d[0], "w": d[1], "h": d[2]},
               "stations": len(xs), "stations_uniform": int(stations), "section_pts": int(section_pts),
               "vertices": len(ob.data.vertices), "polygons": len(ob.data.polygons),
               "wheels": wheel_info, "wheel_objects": made[1:], "markers": mk,
               "arch_clear_mm": sp["arch_clear_mm"],
               "spec": {k: sp[k] for k in ("type", "length_mm", "width_mm", "height_mm", "wheelbase_mm",
                                           "wheel_r_mm", "track_mm", "ride_mm")},
               "note": "轮眉是**解析扣出来**的（底面抬到轮圆 + arch_clear）⇒ 轮穿车身在构造上不可能；"
                       "外壳侧轮廓由剖面参数决定 ⇒ 与参考图的差就是剖面参数的差，可逐项改"})


def vehicle_base(spec=None, name="VehicleShell", stations=28, section_pts=16, wheels=True, markers=True, **kw):
    """放样车壳：纵向剖面 → 逐站超椭圆截面 → 桥接成壳（含解析轮眉）+ 四个轮 + 标记。"""
    try:
        sp, nm, L = _vb_prepare(spec, name, kw, wheels)
        xs = _vb_stations(sp, stations, L)
        ob = _vb_shell(sp, xs, nm, section_pts)
        _vb_arch_cut(ob, sp, nm)
        made = [nm]
        mmu = _KIT.units()
        wheel_info = []
        if wheels:
            wnames, wheel_info = _vb_wheels(sp, nm, mmu)
            made.extend(wnames)
        mk = _vb_markers(sp, nm, L) if markers else []
        return _vb_payload(ob, sp, nm, xs, stations, section_pts, wheel_info, made, mk, mmu)
    except Exception as e:
        return _j({"ok": False, "error": "%s: %s" % (type(e).__name__, str(e)[:220])})

def _vs_scan(img, p, inv, threshold):
    """imgtools scan 调用（threshold 仅在给定时传入）。"""
    a = {"path": p, "invert": bool(inv)}
    if threshold is not None:
        a["threshold"] = threshold
    return img("scan", a)


def _vs_side(img, side, invert_side, threshold):
    """侧视图轮廓 + 标定；返回 (sc, err)。"""
    sc = _vs_scan(img, side, invert_side, threshold)
    if not sc.get("ok") or not sc.get("bbox"):
        return None, _j({"ok": False, "error": "侧视图轮廓没分出来（给 threshold 或先抠背景）", "scan": sc})
    return sc, None


def _vs_scale(sc, mm_per_px, known_len_mm):
    """像素↔mm 标定（显式 mm_per_px 或已知长度）；返回 (mpp, err)。"""
    bx, by, bw_, bh_ = sc["bbox"]
    mpp = mm_per_px
    if mpp is None:
        if known_len_mm:
            mpp = float(known_len_mm) / float(bw_)
        else:
            return None, _j({"ok": False, "error": "需要 mm_per_px 或 known_len_mm（一个已知长度就能标定）"})
    return float(mpp), None


def _vs_rows(sc, mpp, stations):
    """侧视站表（x 从 0 车头到 length）；返回 dict 或 None（缺 col_top/col_bottom）。"""
    bx, by, bw_, bh_ = sc["bbox"]
    ctop = sc.get("col_top") or []
    cbot = sc.get("col_bottom") or []
    step_c = int(sc.get("col_px_per_step") or 1)
    if not ctop:
        return None
    length_mm = bw_ * mpp
    height_mm = bh_ * mpp
    rows = []
    n = max(3, int(stations))
    for i in range(n):
        u = i / float(n - 1)
        ci = min(len(ctop) - 1, int(round(u * (len(ctop) - 1))))
        t, b = ctop[ci], cbot[ci]
        if t is None or b is None:
            rows.append({"u": round(u, 4), "x_mm": round(u * length_mm, 1), "empty": True})
            continue
        rows.append({"u": round(u, 4), "x_mm": round(u * length_mm, 1),
                     "z_top_mm": round((bh_ - float(t)) * mpp, 1),
                     "z_bottom_mm": round((bh_ - float(b)) * mpp, 1),
                     "z_px": [int(t), int(b)]})
    return {"rows": rows, "ctop": ctop, "cbot": cbot, "step_c": step_c,
            "bx": bx, "by": by, "bw": bw_, "bh": bh_,
            "length_mm": length_mm, "height_mm": height_mm}


def _vs_top(rows, img, top, invert_top, threshold, mpp):
    """平面半宽（优先俯视图）；返回 half_width_source。"""
    hw_src = "none"
    if top:
        tc = _vs_scan(img, top, invert_top, threshold)
        ttop, tbot = tc.get("col_top") or [], tc.get("col_bottom") or []
        if tc.get("ok") and ttop and tbot:
            hw_src = "top_view"
            for r in rows:
                if r.get("empty"):
                    continue
                ci = min(len(ttop) - 1, int(round(r["u"] * (len(ttop) - 1))))
                if ttop[ci] is None or tbot[ci] is None:
                    r["half_w_mm"] = None
                else:
                    r["half_w_mm"] = round((float(tbot[ci]) - float(ttop[ci])) * mpp / 2.0, 1)
            hw_src = "top_view"
    return hw_src


def _vs_front(img, front, invert_front, threshold, mpp):
    """前视图逐行宽度 → (front_width_mm, section_params)。"""
    fc = _vs_scan(img, front, invert_front, threshold)
    prof = fc.get("row_profile") or []
    if not (fc.get("ok") and prof and fc.get("bbox")):
        return None, {"n_top": 4.0, "n_top_source": "assumed", "beltline_z_mm": None,
                      "tumblehome_mm": None, "sill_tuck_mm": None, "source": "none"}
    fbx, fby, fbw, fbh = fc["bbox"]
    front_w = fbw * mpp
    # 逐行宽度（bbox 内采样，行步长由 row_profile 长度推）
    step_r = max(1, fbh // max(1, len(prof)))
    widest_i = max(range(len(prof)), key=lambda k: prof[k])
    belt_z = (fbh - widest_i * step_r) * mpp
    top_w = max(prof[:max(1, len(prof) // 10)] or [0])
    bot_w = max(prof[-max(1, len(prof) // 10):] or [0])
    section = {"n_top": 4.0, "n_top_source": "assumed",
               "beltline_z_mm": round(belt_z, 1),
               "tumblehome_mm": round((prof[widest_i] - top_w) * mpp / 2.0, 1),
               "sill_tuck_mm": round((prof[widest_i] - bot_w) * mpp / 2.0, 1),
               "front_width_mm": round(front_w, 1), "source": "front_view"}
    return front_w, section


def _vs_section_shape(img, front, invert_front, threshold):
    """截面轮廓：前视图逐行宽度 → 归一化形状 [{z_frac, hw_frac}]（0=底,1=顶；宽/最宽）。"""
    if not front:
        return None
    fc2 = _vs_scan(img, front, invert_front, threshold)
    prof2 = fc2.get("row_profile") or []
    if not (fc2.get("ok") and prof2 and max(prof2) > 0):
        return None
    npts = max(6, min(20, len(prof2)))
    mx = float(max(prof2))
    pts = []
    for k in range(npts):
        i = int(round(k * (len(prof2) - 1) / float(max(1, npts - 1))))
        pts.append({"z_frac": round(1.0 - i / float(max(1, len(prof2) - 1)), 4),
                    "hw_frac": round(float(prof2[i]) / mx, 4)})
    return sorted(pts, key=lambda q: q["z_frac"])


def _vs_axles(rows, length_mm):
    """轮轴：接地段（低洼段）聚类中心；返回 (axles, wheel_info)。"""
    # 自适应阈值：z_min + 0.35×(中位底边 − z_min)。固定阈值会失效 ——
    # 整条底边（前唇/门槛/后杠）都在"低"的位置时，全车都会被当成接地段（实测只识别出 1 段）。
    ground_runs = []
    run = None
    _zs = [r["z_bottom_mm"] for r in rows if not r.get("empty") and r.get("z_bottom_mm") is not None]
    _zmin = min(_zs) if _zs else 0.0
    _zmed = sorted(_zs)[len(_zs) // 2] if _zs else 0.0
    _thr = _zmin + 0.35 * max(1.0, (_zmed - _zmin))
    for i, r in enumerate(rows):
        if r.get("empty"):
            continue
        # 轮子与车身在填充轮廓里连成一片 ⇒ 不能只看"贴地几像素"：
        # 取底部**低洼段**（z_bottom 明显低于车高的 15%）并聚类，其中心即轮轴 x
        on_ground = r["z_bottom_mm"] is not None and r["z_bottom_mm"] <= _thr
        gap_ok = run is not None and (r["x_mm"] - run[1]) <= 0.06 * length_mm
        if on_ground and (run is None or not gap_ok):
            if run is not None:
                ground_runs.append(run)
            run = [r["x_mm"], r["x_mm"]]
        elif on_ground:
            run[1] = r["x_mm"]
        elif run is not None:
            ground_runs.append(run); run = None
    if run is not None:
        ground_runs.append(run)
    axles = [round((a + b) / 2.0, 1) for (a, b) in ground_runs if (b - a) >= 0.05 * length_mm]
    wheel_info = {"source": "ground_runs", "axles_x_mm": axles, "threshold_mm": round(_thr, 2),
                  "runs": [[round(a, 1), round(b, 1)] for (a, b) in ground_runs]}
    return axles, wheel_info


def _vs_wheel_given(wheel_centers_px, bx, by, bh, mpp):
    """给定轮心像素 → wheel_info（source=given_px）。"""
    pts = []
    for p in wheel_centers_px:
        px = (float(p[0]) - bx) * mpp
        py = (bh_ - (float(p[1]) - by)) * mpp
        pts.append({"x_mm": round(px, 1), "z_mm": round(py, 1)})
    return {"source": "given_px", "centers": pts,
            "axles_x_mm": [q["x_mm"] for q in pts]}


def _vs_taper(rows, front_w):
    """没有俯视图但有前视图：用最大宽 × 平面收放假设填 half_w（**声明为假设**）。"""
    for r in rows:
        if r.get("empty"):
            continue
        u = float(r.get("u") or 0.0)
        taper = 1.0 - 0.35 * max(0.0, (abs(u - 0.5) - 0.30) / 0.20) ** 1.4
        r["half_w_mm"] = round(front_w / 2.0 * max(0.62, taper), 1)


def _vs_payload(sc, mpp, rows, hw_src, section, section_shape, wheel_info, axles,
                front_w, wheel_r_mm, length_mm, height_mm):
    """vehicle_sections 回执（含 package 与 warnings）。"""
    pkg = {"length_mm": round(length_mm, 1), "height_mm": round(height_mm, 1)}
    if len(axles) >= 2:
        pkg["front_axle_from_front_mm"] = axles[0]
        pkg["wheelbase_mm"] = round(axles[-1] - axles[0], 1)
    if front_w:
        pkg["width_mm"] = round(front_w, 1)
    if wheel_r_mm:
        pkg["wheel_r_mm"] = wheel_r_mm
    warn = []
    if hw_src == "none":
        warn.append("没有俯视图：half_w_mm 未给（放样时会按默认收放）")
    if wheel_r_mm is None:
        warn.append("轮径无法从填充轮廓反推：给 wheel_r_px 或用已知规格")
    if len(axles) < 2:
        warn.append("接地段不足两段：轮轴 x 可能没识别出来（给 wheel_centers_px 更稳）")
    return _j({"ok": True, "side": sc.get("path"), "scale_mm_per_px": round(mpp, 5),
               "stations": rows, "station_count": len(rows),
               "half_width_source": hw_src, "section_params": section, "section_shape": section_shape, "wheels": wheel_info,
               "package": pkg, "warnings": warn,
               "note": "站表可直接喂 vehicle_loft；包络数字可喂 vehicle_package 先过比例门",
               "protocol": ["① 先用 package 过比例门 → ② 站表放样 → ③ 正交渲染 vs 原图算 IoU → ④ 改站表参数再跑"]})


def vehicle_sections(side, front=None, top=None, mm_per_px=None, known_len_mm=None,
                     stations=41, invert_side=False, invert_front=False, invert_top=False,
                     threshold=None, wheel_centers_px=None, wheel_r_px=None):
    """三视图 → **站表 + 截面参数 + 包络数字**（外表面还原第 2 步；跑车/复杂外壳通用）。

    side（必填）：侧视图。逐列给 z_top(x)/z_bottom(x) ⇒ 轮廓与站表。
    top（可选）：俯视图。逐列给 half_width(x) ⇒ 平面收放（跑车的腰身）。
    front（可选）：前视图。逐行给宽度 ⇒ 腰线高度 / 侧倾(tumblehome) / 下裙内收。
    标定：给 mm_per_px，或给 known_len_mm（用侧视图 bbox 宽度换算）。
    wheel_centers_px：轮心像素（相对整图）；不给则用**接地段中心**自动定位轮轴 x（可靠），
                     轮径无法从填充轮廓反推（轮子与车身连成一片）⇒ 需 wheel_r_px 或已知规格。
    """
    img = _api("img")
    if img is None:
        return _j({"ok": False, "error": "需要 imgtools（preload 里加 imgtools）"})
    sc, err = _vs_side(img, side, invert_side, threshold)
    if err is not None:
        return err
    mpp, err = _vs_scale(sc, mm_per_px, known_len_mm)
    if err is not None:
        return err
    info = _vs_rows(sc, mpp, stations)
    if info is None:
        return _j({"ok": False, "error": "侧视图没有 col_top/col_bottom（imgtools 版本旧？）"})
    rows, length_mm, height_mm = info["rows"], info["length_mm"], info["height_mm"]
    hw_src = _vs_top(rows, img, top, invert_top, threshold, mpp)
    front_w = None
    section = {"n_top": 4.0, "n_top_source": "assumed", "beltline_z_mm": None,
               "tumblehome_mm": None, "sill_tuck_mm": None, "source": "none"}
    if front:
        front_w, section = _vs_front(img, front, invert_front, threshold, mpp)
    axles, wheel_info = _vs_axles(rows, length_mm)
    section_shape = _vs_section_shape(img, front, invert_front, threshold)
    if wheel_centers_px:
        wheel_info = _vs_wheel_given(wheel_centers_px, info["bx"], info["by"], info["bh"], mpp)
    if hw_src == "none" and front_w:
        _vs_taper(rows, front_w)
        hw_src = "front_view_taper_assumed"
    wheel_r_mm = round(float(wheel_r_px) * mpp, 1) if wheel_r_px else None
    return _vs_payload(sc, mpp, rows, hw_src, section, section_shape, wheel_info, axles,
                       front_w, wheel_r_mm, length_mm, height_mm)
