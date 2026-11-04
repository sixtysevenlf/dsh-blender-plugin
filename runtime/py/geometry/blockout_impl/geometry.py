# -*- coding: utf-8 -*-
"""Split from blockout.py; behavior preserved by the facade."""
from .common import *  # noqa: F401,F403

def _bbox_bu(objs):
    mn = Vector((1e30, 1e30, 1e30))
    mx = Vector((-1e30, -1e30, -1e30))
    n = 0
    for ob in objs:
        if ob is None or ob.type != "MESH":
            continue
        mw = ob.matrix_world
        for c in ob.bound_box:
            p = mw @ Vector((c[0], c[1], c[2]))
            for i in range(3):
                if p[i] < mn[i]:
                    mn[i] = p[i]
                if p[i] > mx[i]:
                    mx[i] = p[i]
        n += 1
    if n == 0:
        raise ValueError("没有可量的 mesh 对象")
    return mn, mx


def _bbox_mm(mn, mx):
    k = _mm_per_bu()
    lo = [round(float(mn[i]) * k, 4) for i in range(3)]
    hi = [round(float(mx[i]) * k, 4) for i in range(3)]
    size = [round(hi[i] - lo[i], 4) for i in range(3)]
    center = [round((hi[i] + lo[i]) / 2.0, 4) for i in range(3)]
    return {"min": lo, "max": hi, "size": size, "center": center}


def _tris_base(objs):
    n = 0
    per = []
    for ob in objs:
        if ob is None or ob.type != "MESH":
            continue
        me = ob.data
        try:
            me.calc_loop_triangles()
            t = len(me.loop_triangles)
        except Exception:
            t = sum(max(1, len(p.vertices) - 2) for p in me.polygons)
        n += int(t)
        per.append(int(t))
    return n, per


def _tris_eval(objs):
    """含修改器（Bevel 不 apply 但会参与求值）的三角面数。"""
    dg = bpy.context.evaluated_depsgraph_get()
    n = 0
    for ob in objs:
        if ob is None or ob.type != "MESH":
            continue
        try:
            ev = ob.evaluated_get(dg)
            me = ev.to_mesh()
            try:
                me.calc_loop_triangles()
                n += len(me.loop_triangles)
            finally:
                ev.to_mesh_clear()
        except Exception:
            pass
    return int(n)


def _audit_objects(objs):
    """内建轻量体检：非流形边 / 零面积面 / 孤立点（只看 base mesh，不含修改器）。"""
    rows = []
    bad = 0
    total_nm = total_zf = total_loose = 0
    for ob in objs:
        if ob is None or ob.type != "MESH":
            continue
        me = ob.data
        bm = bmesh.new()
        try:
            bm.from_mesh(me)
            nm = sum(1 for e in bm.edges if not e.is_manifold)
            zf = sum(1 for f in bm.faces if f.calc_area() < 1e-14)
            loose = sum(1 for v in bm.verts if not v.link_edges)
            verts = len(bm.verts)
            faces = len(bm.faces)
        finally:
            bm.free()
        total_nm += nm
        total_zf += zf
        total_loose += loose
        issue = bool(nm or zf or loose)
        if issue:
            bad += 1
        rows.append({"object": str(ob.name), "verts": verts, "faces": faces,
                     "non_manifold_edges": nm, "zero_area_faces": zf, "loose_verts": loose,
                     "ok": not issue})
    return {"ok": bad == 0, "objects": len(rows), "objects_with_issues": bad,
            "non_manifold_edges": total_nm, "zero_area_faces": total_zf, "loose_verts": total_loose,
            "per_object": rows, "note": "base mesh 体检（Bevel 未 apply，不在此列）"}


# ────────────────────────────── 渲染 ──────────────────────────────
def _srgb_to_lin(a):
    a = np.asarray(a, dtype=np.float32)
    lo = a / 12.92
    hi = np.power((np.clip(a, 0.0, None) + 0.055) / 1.055, 2.4)
    return np.where(a <= 0.04045, lo, hi).astype(np.float32)


def _lin_to_srgb(a):
    a = np.clip(np.asarray(a, dtype=np.float32), 0.0, None)
    lo = a * 12.92
    hi = 1.055 * np.power(a, 1.0 / 2.4) - 0.055
    return np.where(a <= 0.0031308, lo, hi).astype(np.float32)


def _load_rgba(path):
    img = bpy.data.images.load(path, check_existing=False)
    try:
        w, h = int(img.size[0]), int(img.size[1])
        if w <= 0 or h <= 0:
            raise RuntimeError("图像尺寸非法：%s" % path)
        buf = np.empty(w * h * 4, dtype=np.float32)
        img.pixels.foreach_get(buf)
    finally:
        bpy.data.images.remove(img)
    return buf.reshape(h, w, 4), w, h


def _alpha_stats(path):
    arr, w, h = _load_rgba(path)
    a = arr[:, :, 3]
    m = a > 0.02
    cov = float(m.mean())
    bbox = None
    if bool(m.any()):
        ys, xs = np.nonzero(m)
        bbox = [int(xs.min()), int(ys.min()), int(xs.max()), int(ys.max())]
    return {"coverage": round(cov, 4), "alpha_max": round(float(a.max()), 4), "bbox": bbox,
            "size": [w, h]}


def _make_sheet(paths, names, out_path, cols=None, gap=6, bg=0.82):
    if not paths:
        raise ValueError("没有可拼的视图")
    arrs = []
    size = None
    for p in paths:
        arr, w, h = _load_rgba(p)
        if size is None:
            size = (w, h)
        elif (w, h) != size:
            raise ValueError("视图分辨率不一致：%s=%s vs %s" % (p, (w, h), size))
        arrs.append(arr)
    w, h = size
    n = len(arrs)
    cols = max(1, min(int(cols or n), n))
    rows = int(math.ceil(n / float(cols)))
    W = cols * w + (cols - 1) * gap
    H = rows * h + (rows - 1) * gap
    out = np.empty((H, W, 4), dtype=np.float32)
    lin_bg = _srgb_to_lin(np.array([bg, bg, bg], dtype=np.float32))
    out[:, :, 0:3] = float(bg)
    out[:, :, 3] = 1.0
    for i, arr in enumerate(arrs):
        r = i // cols
        c = i % cols
        y0 = H - (r + 1) * h - r * gap
        x0 = c * (w + gap)
        lin = _srgb_to_lin(arr[:, :, 0:3])
        alpha = arr[:, :, 3:4]
        comp = lin * alpha + lin_bg.reshape(1, 1, 3) * (1.0 - alpha)
        out[y0:y0 + h, x0:x0 + w, 0:3] = _lin_to_srgb(comp)
    img = bpy.data.images.new("BO_SHEET_TMP", width=W, height=H, alpha=True)
    try:
        img.pixels.foreach_set(out.ravel())
        img.filepath_raw = _norm_path(out_path)
        img.file_format = "PNG"
        img.save()
    finally:
        try:
            bpy.data.images.remove(img)
        except Exception:
            pass
    rec = _file_receipt(_norm_path(out_path))
    rec.update({"ok": True, "res": [W, H], "cols": cols, "rows": rows,
                "cells": [str(x) for x in names]})
    return rec


def _configure_workbench(sc, res, film_transparent=True):
    sc.render.engine = "BLENDER_WORKBENCH"
    sc.render.resolution_x = int(res[0])
    sc.render.resolution_y = int(res[1])
    sc.render.resolution_percentage = 100
    sc.render.film_transparent = bool(film_transparent)
    try:
        sc.render.image_settings.file_format = "PNG"
        sc.render.image_settings.color_mode = "RGBA"
        sc.render.image_settings.color_depth = "8"
    except Exception:
        pass
    try:
        sc.display.render_aa = "8"
    except Exception:
        try:
            sc.display.render_aa = "5"
        except Exception:
            pass
    try:
        sh = sc.display.shading
        sh.light = "STUDIO"
        sh.color_type = "MATERIAL"
        sh.show_cavity = True
        try:
            sh.cavity_type = "BOTH"
        except Exception:
            pass
        sh.show_object_outline = True
        try:
            sh.object_outline_color = (0.08, 0.08, 0.08)
        except Exception:
            pass
        sh.show_specular_highlight = True
    except Exception:
        pass


def _tan_half(res, lens, sensor=36.0):
    w, h = float(res[0]), float(res[1])
    if w >= h:
        hx = sensor / 2.0
        hy = hx * (h / w)
    else:
        hy = sensor / 2.0
        hx = hy * (w / h)
    return min(hx / float(lens), hy / float(lens))


def _fit_camera(cam, sc, mn, mx, az, el, tan_half, margin):
    """按 bbox 8 角点的**投影**做对焦（相机始终在包围球外 ⇒ 角点都在相机前方）。

    为什么不用"半径/视场角"一步算：细长模型（沿视线方向很长）按包围球取景会退得极远，
    而按比例逼近又会在相机进入包围盒后发散 —— 这里用**几何二分**求"投影恰好贴到 margin 边"的距离。
    """
    center = (mn + mx) * 0.5
    radius = max(float((mx - mn).length) * 0.5, 1e-5)
    a = math.radians(float(az))
    e = math.radians(float(el))
    d = Vector((math.cos(e) * math.cos(a), math.cos(e) * math.sin(a), math.sin(e)))
    if d.length < 1e-9:
        d = Vector((0.0, -1.0, 0.0))
    corners = [Vector((x, y, z)) for x in (mn.x, mx.x) for y in (mn.y, mx.y) for z in (mn.z, mx.z)]
    target = 0.5 / float(margin)
    init = radius / max(math.sin(math.atan(tan_half)), 1e-6) * float(margin)

    def place(dist):
        cam.location = center + d * dist
        cam.rotation_mode = "XYZ"
        cam.rotation_euler = (center - cam.location).to_track_quat("-Z", "Y").to_euler()
        try:
            bpy.context.view_layer.update()
        except Exception:
            pass

    try:
        from bpy_extras.object_utils import world_to_camera_view
    except Exception:
        place(init)
        return cam.location, cam.rotation_euler

    def worst_at(dist):
        place(dist)
        w = 0.0
        for c in corners:
            p = world_to_camera_view(sc, cam, c)
            w = max(w, abs(float(p.x) - 0.5), abs(float(p.y) - 0.5))
        return w

    lo = radius * 1.000001          # 相机刚出包围球：近角点必顶满/溢出画面
    hi = max(init * 2.0, radius * 2.0)
    w_hi = worst_at(hi)
    guard = 0
    while w_hi > target and guard < 12:
        hi *= 2.0
        w_hi = worst_at(hi)
        guard += 1
    if w_hi > target:               # 理论上不会（无穷远一定收敛），防御性回退
        place(hi)
        return cam.location, cam.rotation_euler
    if worst_at(lo) <= target:      # 极薄模型：贴到包围球仍填不满，就用 lo（不再往里钻）
        place(lo)
        return cam.location, cam.rotation_euler
    for _ in range(24):
        mid = math.sqrt(lo * hi)    # 几何二分（距离是对数尺度）
        if worst_at(mid) > target:
            lo = mid
        else:
            hi = mid
        if hi / max(lo, 1e-9) < 1.01:
            break
    place(hi)
    return cam.location, cam.rotation_euler


def _render_qc(objs, views, res, outdir, tag, opt):
    api = _KIT.api("qc_render")
    vargs = [{"name": v["name"], "az": v["az"], "el": v["el"]} for v in views]
    args = {
        "views": vargs, "res": list(res), "outdir": outdir, "tag": tag,
        "targets": [ob.name for ob in objs],
        "engine": "keep", "view_transform": "Standard", "lights": False,
        "lock": False, "sheet": 0, "warmup": False, "film_transparent": True,
        "margin": opt["margin"], "view_order": "request",
        "jsonl": _norm_path(os.path.join(outdir, tag + "_views.jsonl")),
    }
    r = api("render_views", args)
    if isinstance(r, str):
        r = json.loads(r)
    if not isinstance(r, dict):
        raise RuntimeError("qc_render 没返回 dict（%r）" % (type(r),))
    if not r.get("ok"):
        raise RuntimeError("qc_render 回 ok=false：%s" % str(r.get("error") or "?")[:200])
    by = {}
    for e in (r.get("views") or []):
        if isinstance(e, dict):
            by[str(e.get("view"))] = e
    rows = []
    for v in views:
        e = by.get(v["name"])
        if e is None:
            raise RuntimeError("qc_render 少出了视角 %r（实际：%s）" % (v["name"], sorted(by.keys())))
        path = str(e.get("path") or "")
        if not path or not os.path.isfile(path):
            raise RuntimeError("qc_render 报的 PNG 不存在：%r" % (path,))
        frame = e.get("frame") or {}
        cov = frame.get("coverage")
        if cov is None:
            try:
                cov = _alpha_stats(path)["coverage"]
            except Exception:
                cov = None
        rows.append({"name": v["name"], "path": path, "ms": e.get("ms"),
                     "bytes": int(e.get("bytes") or os.path.getsize(path)),
                     "md5": e.get("hash") or _md5(path),
                     "coverage_estimate": cov, "bbox_px": frame.get("bbox"),
                     "res": e.get("res") or list(res)})
    return rows


def _render_internal(objs, views, res, outdir, tag, opt):
    sc = bpy.context.scene
    cam_data = bpy.data.cameras.new("BO_TMP_CAM")
    cam = bpy.data.objects.new("BO_TMP_CAM", cam_data)
    sc.collection.objects.link(cam)
    try:
        cam_data.lens = float(opt["lens"])
        cam_data.sensor_width = 36.0
        cam_data.sensor_fit = "AUTO"
        cam_data.clip_start = 0.0001
        cam_data.clip_end = 10000.0
        sc.camera = cam
        _configure_workbench(sc, res, opt.get("film_transparent", True))
        mn, mx = _bbox_bu(objs)
        tan_half = _tan_half(res, opt["lens"])
        rows = []
        for v in views:
            _fit_camera(cam, sc, mn, mx, v["az"], v["el"], tan_half, opt["margin"])
            path = _norm_path(os.path.join(outdir, "%s_%s.png" % (tag, _safe_token(v["name"], "view"))))
            sc.render.filepath = path
            t1 = time.perf_counter()
            bpy.ops.render.render(write_still=True)
            ms = int(round((time.perf_counter() - t1) * 1000.0))
            rec = _file_receipt(path)
            cov = _alpha_stats(path)
            rec.update({"name": v["name"], "ms": ms, "coverage_estimate": cov.get("coverage"),
                        "bbox_px": cov.get("bbox"), "res": list(res)})
            rows.append(rec)
        return rows
    finally:
        try:
            if sc.camera is cam:
                sc.camera = None
        except Exception:
            pass
        try:
            bpy.data.objects.remove(cam, do_unlink=True)
        except Exception:
            pass
        if getattr(cam_data, "users", 0) == 0:
            try:
                bpy.data.cameras.remove(cam_data)
            except Exception:
                pass


def _render_views(objs, opt, tag):
    t0 = time.perf_counter()
    sc = bpy.context.scene
    outdir = opt["outdir"]
    try:
        os.makedirs(outdir, exist_ok=True)
    except Exception as e:
        raise RuntimeError("render outdir 建不出来：%s（%s）" % (outdir, str(e)[:120]))
    warnings = []
    saved = {}
    hidden = []
    try:
        saved["engine"] = sc.render.engine
        saved["camera"] = sc.camera
        saved["filepath"] = sc.render.filepath
        saved["resx"] = sc.render.resolution_x
        saved["resy"] = sc.render.resolution_y
        saved["pct"] = sc.render.resolution_percentage
        saved["film"] = sc.render.film_transparent
        try:
            saved["fmt"] = sc.render.image_settings.file_format
            saved["mode"] = sc.render.image_settings.color_mode
        except Exception:
            pass
        try:
            saved["aa"] = sc.display.render_aa
            sh = sc.display.shading
            for a in _SHADING_ATTRS:
                saved["sh:" + a] = getattr(sh, a)
        except Exception:
            pass
        objset = set(objs)
        for ob in list(sc.objects):
            if ob in objset:
                continue
            if ob.type in _RENDERABLE_TYPES:
                hidden.append((ob, bool(ob.hide_render)))
                ob.hide_render = True
        sc.render.engine = "BLENDER_WORKBENCH"
        try:
            bpy.context.view_layer.update()
        except Exception:
            pass
        rows = None
        via_used = None
        qc = None
        if opt["via"] in ("auto", "qc"):
            qc = _KIT.api("qc_render")
        if opt["via"] == "qc" and qc is None:
            raise RuntimeError("via=qc 但内核里没有 K.dsh_qc_render_api —— preload 里加上 qc_render")
        if qc is not None and opt["via"] in ("auto", "qc"):
            try:
                rows = _render_qc(objs, opt["views"], opt["res"], outdir, opt["tag"], opt)
                via_used = "qc_render"
            except Exception as e:
                if opt["via"] == "qc":
                    raise
                warnings.append("qc_render 路径失败（%s: %s）→ 回退内置 Workbench 渲染"
                                % (type(e).__name__, str(e)[:180]))
        if rows is None:
            rows = _render_internal(objs, opt["views"], opt["res"], outdir, opt["tag"], opt)
            via_used = "internal"
        sheet = None
        if opt["sheet_cols"] and rows:
            try:
                sheet = _make_sheet([r["path"] for r in rows], [r["name"] for r in rows],
                                    os.path.join(outdir, opt["tag"] + "_sheet.png"),
                                    cols=opt["sheet_cols"])
            except Exception as e:
                sheet = {"ok": False, "error": "%s: %s" % (type(e).__name__, str(e)[:200]),
                         "hint": "sheet 是附加产物（允许只给单张 views）：先看 views 里的 PNG"}
                warnings.append("sheet 拼图失败：%s" % str(e)[:160])
        ms = int(round((time.perf_counter() - t0) * 1000.0))
        return {"ok": True, "engine": "BLENDER_WORKBENCH", "via": via_used, "res": list(opt["res"]),
                "views": rows, "sheet": sheet, "ms": ms, "outdir": outdir,
                "tag": opt["tag"], "warnings": warnings}
    finally:
        for ob, hv in hidden:
            try:
                if bpy.data.objects.get(ob.name) is not None:
                    ob.hide_render = hv
            except Exception:
                pass
        def _restore(key, setter):
            if key not in saved:
                return
            try:
                setter(saved[key])
            except Exception:
                pass
        _restore("engine", lambda v: setattr(sc.render, "engine", v))
        _restore("camera", lambda v: setattr(sc, "camera", v))
        _restore("filepath", lambda v: setattr(sc.render, "filepath", v))
        _restore("resx", lambda v: setattr(sc.render, "resolution_x", v))
        _restore("resy", lambda v: setattr(sc.render, "resolution_y", v))
        _restore("pct", lambda v: setattr(sc.render, "resolution_percentage", v))
        _restore("film", lambda v: setattr(sc.render, "film_transparent", v))
        _restore("fmt", lambda v: setattr(sc.render.image_settings, "file_format", v))
        _restore("mode", lambda v: setattr(sc.render.image_settings, "color_mode", v))
        _restore("aa", lambda v: setattr(sc.display, "render_aa", v))
        try:
            sh = sc.display.shading
            for a in _SHADING_ATTRS:
                k = "sh:" + a
                if k in saved:
                    try:
                        setattr(sh, a, saved[k])
                    except Exception:
                        pass
        except Exception:
            pass


# ────────────────────────────── 导出 ──────────────────────────────
def _ensure_gltf():
    try:
        if "io_scene_gltf2" not in bpy.context.preferences.addons.keys():
            bpy.ops.preferences.addon_enable(module="io_scene_gltf2")
    except Exception:
        pass
    if not hasattr(bpy.ops.export_scene, "gltf"):
        raise RuntimeError("glTF 导出器不可用（io_scene_gltf2 没启用，且 bpy.ops.export_scene.gltf 不存在）")


def _select_only(objs):
    try:
        for ob in bpy.context.view_layer.objects:
            try:
                ob.select_set(False)
            except Exception:
                pass
        for ob in objs:
            try:
                ob.select_set(True)
            except Exception:
                pass
        if objs:
            bpy.context.view_layer.objects.active = objs[0]
    except Exception:
        pass


def _glb_meta(path):
    """读 GLB 头 + JSON chunk：版本 / 节点数 / mesh 数（导出没带全就用它兜底核对）。"""
    meta = {"magic_ok": False, "version": None, "nodes": None, "meshes": None}
    with open(path, "rb") as f:
        head = f.read(12)
        if len(head) < 12:
            return meta
        meta["magic_ok"] = head[:4] == b"glTF"
        meta["version"] = struct.unpack("<I", head[4:8])[0]
        chunk = f.read(8)
        if len(chunk) < 8:
            return meta
        clen, ctype = struct.unpack("<II", chunk)
        if ctype != 0x4E4F534A:   # JSON
            return meta
        raw = f.read(min(int(clen), 4 << 20))
    try:
        js = json.loads(raw.decode("utf-8", "replace").rstrip("\x00 \t\r\n"))
        meta["nodes"] = len(js.get("nodes") or [])
        meta["meshes"] = len(js.get("meshes") or [])
        meta["generator"] = str((js.get("asset") or {}).get("generator") or "")[:80]
    except Exception as e:
        meta["json_error"] = "%s: %s" % (type(e).__name__, str(e)[:100])
    return meta


def _export_glb(objs, path):
    t0 = time.perf_counter()
    path = _norm_path(path)
    d = os.path.dirname(path)
    if d:
        os.makedirs(d, exist_ok=True)
    _ensure_gltf()
    _select_only(objs)
    try:
        bpy.ops.export_scene.gltf(filepath=path, export_format="GLB", use_selection=True,
                                  export_apply=True, export_yup=True)
    except TypeError:
        bpy.ops.export_scene.gltf(filepath=path, export_format="GLB", use_selection=True)
    rec = _file_receipt(path)
    meta = _glb_meta(path)
    if not meta["magic_ok"]:
        raise RuntimeError("GLB 魔数不对（%r）：%s" % (b"glTF", path))
    rec.update(meta)
    rec.update({"ok": True, "ms": int(round((time.perf_counter() - t0) * 1000.0)),
                "objects": len(objs)})
    return rec


def _export_obj(objs, path):
    t0 = time.perf_counter()
    path = _norm_path(path)
    d = os.path.dirname(path)
    if d:
        os.makedirs(d, exist_ok=True)
    _select_only(objs)
    if hasattr(bpy.ops.wm, "obj_export"):
        bpy.ops.wm.obj_export(filepath=path, export_selected_objects=True,
                              apply_modifiers=True, export_materials=True)
    else:
        bpy.ops.export_scene.obj(filepath=path, use_selection=True, use_mesh_modifiers=True)
    rec = _file_receipt(path)
    rec.update({"ok": True, "ms": int(round((time.perf_counter() - t0) * 1000.0)),
                "objects": len(objs)})
    return rec


def _export_objs(objs, paths):
    out = {"ok": True, "objects": [ob.name for ob in objs]}
    if paths.get("glb"):
        try:
            out["glb"] = _export_glb(objs, paths["glb"])
        except Exception as e:
            out["glb"] = {"ok": False, "error": "%s: %s" % (type(e).__name__, str(e)[:240]),
                          "path": paths.get("glb")}
            out["ok"] = False
    if paths.get("obj"):
        try:
            out["obj"] = _export_obj(objs, paths["obj"])
        except Exception as e:
            out["obj"] = {"ok": False, "error": "%s: %s" % (type(e).__name__, str(e)[:240]),
                          "path": paths.get("obj")}
            out["ok"] = False
    if not out["ok"]:
        errs = [k for k in ("glb", "obj") if isinstance(out.get(k), dict) and out[k].get("ok") is False]
        out["error"] = "导出失败：%s" % ", ".join(errs)
        out["hint"] = "查上面的 glb/obj.error；GLB 需要 io_scene_gltf2（插件会自动 enable）"
    return out
