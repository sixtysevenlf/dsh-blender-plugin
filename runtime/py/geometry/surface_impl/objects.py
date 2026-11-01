# -*- coding: utf-8 -*-
"""Split from surface_orig.py; behavior preserved by the facade."""
from .common import *  # noqa: F401,F403
from .build import surface_mesh_data

# ────────────────────────────── 对象创建（bmesh，不碰 bpy.ops） ──────────────────────────────
def _coll(name=None):
    nm = str(name or DEFAULT_COLL).strip() or DEFAULT_COLL
    c = bpy.data.collections.get(nm)
    if c is None:
        c = bpy.data.collections.new(nm)
        bpy.context.scene.collection.children.link(c)
    return c


def _bbox_mm(verts_mm):
    V = np.asarray(verts_mm, dtype=np.float64)
    if V.size == 0:
        return {"min_mm": None, "max_mm": None, "size_mm": None, "center_mm": None}
    lo = V.min(axis=0)
    hi = V.max(axis=0)
    return {"min_mm": [_r(x, 4) for x in lo], "max_mm": [_r(x, 4) for x in hi],
            "size_mm": [_r(x, 4) for x in (hi - lo)],
            "center_mm": [_r(x, 4) for x in ((hi + lo) / 2.0)]}


def _create_object(mesh_data, kind, norm, name=None, collection=None):
    """内存网格（mm）→ bmesh 网格对象（Blender 单位）；返回 (object, info)。"""
    import bmesh
    mmu = max(_mmu(), 1e-12)
    verts_u = np.asarray(mesh_data["verts_mm"], dtype=np.float64) / mmu
    faces = mesh_data["faces"]
    nm = str(name or "").strip() or (PFX + "%s_%s" % (kind, str(norm.get("spec_hash") or "nohash")[:8]))
    nm = nm.replace("\n", " ").strip()[:120]
    me = bpy.data.meshes.new(nm)
    bm = bmesh.new()
    made_faces = 0
    try:
        bverts = [bm.verts.new((float(v[0]), float(v[1]), float(v[2]))) for v in verts_u.tolist()]
        bm.verts.ensure_lookup_table()
        for f in faces:
            try:
                bm.faces.new([bverts[int(i)] for i in f])
                made_faces += 1
            except ValueError:
                continue
        bm.normal_update()
        bm.to_mesh(me)
    finally:
        bm.free()
    if made_faces == 0:
        bpy.data.meshes.remove(me, do_unlink=True)
        raise ValueError("没有任何有效 face（截面/剖面退化？）")
    ob = bpy.data.objects.new(nm, me)
    coll = _coll(collection)
    coll.objects.link(ob)
    try:
        ob["surface.kind"] = kind
        ob["surface.version"] = int(SURFACE_VERSION)
        ob["surface.spec_hash"] = str(norm.get("spec_hash") or "")
        ob["surface.tessellation"] = json.dumps(norm.get("tessellation") or {}, ensure_ascii=False)
        ob["dsh.surface"] = "%s:%s" % (kind, str(norm.get("spec_hash") or "")[:12])
    except Exception:
        pass
    info = {"object": str(ob.name), "collection": str(coll.name), "mesh": str(me.name),
            "verts": int(len(me.vertices)), "tris": int(sum(max(0, len(p.vertices) - 2) for p in me.polygons)),
            "faces": int(len(me.polygons)), "bbox_mm": _bbox_mm(mesh_data["verts_mm"])}
    return ob, info


def _spec_summary(norm):
    d = {k: v for k, v in (norm or {}).items() if not str(k).startswith("_")}
    return d


def _build_from_norm(norm, name=None, collection=None):
    md = surface_mesh_data(norm)
    ob, info = _create_object(md, str(norm["kind"]), norm, name=name, collection=collection)
    return ob, info, md


def _op_ok(kind, norm, md, info, t0, extra=None):
    body = {"ok": True, "kind": kind, "object": info["object"], "collection": info["collection"],
            "mesh": info["mesh"], "verts": info["verts"], "tris": info["tris"],
            "bbox_mm": info["bbox_mm"], "spec_hash": norm["spec_hash"],
            "tessellation": norm["tessellation"], "meta": md["meta"],
            "spec": _spec_summary(norm),
            "note": "尺寸单位 mm（内部已按 scene.unit_settings 折算）；同 spec+tessellation 可复现"}
    if extra:
        body.update(extra)
    return _j(_receipt(body, t0))


# ────────────────────────────── M1 公开 op ──────────────────────────────
def surface_loft(sections=None, degree=3, continuity="G1", closed=False, tessellation=None,
                 name=None, collection=None, constraints=None, units="mm", spec=None):
    """Bézier/B-spline 截面放样：sections=[{z_mm, points_mm:[[x,y],...]}] → bmesh 网格对象。"""
    t0 = time.perf_counter()
    raw = spec if isinstance(spec, dict) else {
        "kind": "loft", "units": units, "sections": sections, "degree": degree,
        "continuity": continuity, "closed": closed, "tessellation": tessellation,
        "constraints": constraints}
    norm, errs = surface_spec_normalize(raw)
    if errs:
        return _err("loft spec 校验失败", errors=errs[:12], stage="M0",
                    hint="sections 至少 2 个、z_mm 严格递增、points_mm 为 [[x,y],...]（closed=True 时 ≥3 点）")
    try:
        ob, info, md = _build_from_norm(norm, name=name, collection=collection)
    except Exception as e:
        import traceback
        return _err("loft 生成失败: %s: %s" % (type(e).__name__, str(e)[:240]), stage="M1",
                    traceback=traceback.format_exc()[-600:])
    return _op_ok("loft", norm, md, info, t0,
                  {"degree_used": md["meta"].get("degree_used")})


def surface_sweep(profile_mm=None, rail_mm=None, orientation="auto", twist_deg=0.0,
                  scale=1.0, tessellation=None, name=None, collection=None, closed=False,
                  constraints=None, units="mm", spec=None):
    """沿折线扫掠：profile_mm 二维截面 + rail_mm 三维路径 → bmesh 网格对象。"""
    t0 = time.perf_counter()
    raw = spec if isinstance(spec, dict) else {
        "kind": "sweep", "units": units, "profile_mm": profile_mm, "rail_mm": rail_mm,
        "orientation": orientation, "twist_deg": twist_deg, "scale": scale,
        "tessellation": tessellation, "closed": closed, "constraints": constraints}
    norm, errs = surface_spec_normalize(raw)
    if errs:
        return _err("sweep spec 校验失败", errors=errs[:12], stage="M0",
                    hint="profile_mm 至少 3 点（closed 环）、rail_mm 至少 2 个非重复 [x,y,z]")
    try:
        ob, info, md = _build_from_norm(norm, name=name, collection=collection)
    except Exception as e:
        import traceback
        return _err("sweep 生成失败: %s: %s" % (type(e).__name__, str(e)[:240]), stage="M1",
                    traceback=traceback.format_exc()[-600:])
    return _op_ok("sweep", norm, md, info, t0, {"meta_extra": md["meta"]})


def surface_patch(control_grid=None, degree_u=3, degree_v=3, tessellation=None, name=None,
                  collection=None, constraints=None, units="mm", spec=None):
    """四边 B-spline 面片：control_grid=[[[x,y,z],...],...]（行=u，列=v）→ bmesh 网格对象。"""
    t0 = time.perf_counter()
    raw = spec if isinstance(spec, dict) else {
        "kind": "patch", "units": units, "control_grid": control_grid,
        "degree_u": degree_u, "degree_v": degree_v, "tessellation": tessellation,
        "constraints": constraints}
    norm, errs = surface_spec_normalize(raw)
    if errs:
        return _err("patch spec 校验失败", errors=errs[:12], stage="M0",
                    hint="control_grid 必须是矩形（≥2×2），degree_u/v 不得超过对应方向控制点数-1")
    try:
        ob, info, md = _build_from_norm(norm, name=name, collection=collection)
    except Exception as e:
        import traceback
        return _err("patch 生成失败: %s: %s" % (type(e).__name__, str(e)[:240]), stage="M1",
                    traceback=traceback.format_exc()[-600:])
    return _op_ok("patch", norm, md, info, t0)


def _object_eval_mesh(ob):
    """对象 → 求值后网格（顶点为 Blender 单位世界坐标，含对象变换与修改器）。"""
    dg = bpy.context.evaluated_depsgraph_get()
    obe = ob.evaluated_get(dg)
    me = obe.to_mesh()
    try:
        if me is None:
            raise ValueError("对象没有可求值网格")
        me.calc_loop_triangles()
        nv = len(me.vertices)
        v = np.empty(nv * 3, dtype=np.float64)
        me.vertices.foreach_get("co", v)
        v = v.reshape(-1, 3)
        mw = np.array(obe.matrix_world, dtype=np.float64)
        v = v @ mw[:3, :3].T + mw[:3, 3]
        idx = np.empty(len(me.loop_triangles) * 3, dtype=np.int64)
        me.loop_triangles.foreach_get("vertices", idx)
        idx = idx.reshape(-1, 3)
        return {"verts": v, "tris": idx}
    finally:
        try:
            obe.to_mesh_clear()
        except Exception:
            pass


def surface_tessellate(spec_or_object=None, quality="normal", name=None, collection=None,
                       tessellation=None, spec=None):
    """曲面规格或对象 → 网格对象（规格走 M1 生成；对象=烘焙求值网格/应用修改器）。"""
    t0 = time.perf_counter()
    src = spec if isinstance(spec, dict) else spec_or_object
    if isinstance(src, dict):
        spec_obj = dict(src)
        if tessellation is not None:
            spec_obj["tessellation"] = tessellation
        elif spec_obj.get("tessellation") is None and quality:
            spec_obj["tessellation"] = str(quality)
        norm, errs = surface_spec_normalize(spec_obj)
        if errs:
            return _err("tessellate spec 校验失败", errors=errs[:12], stage="M0")
        try:
            ob, info, md = _build_from_norm(norm, name=name, collection=collection)
        except Exception as e:
            return _err("tessellate 生成失败: %s: %s" % (type(e).__name__, str(e)[:240]), stage="M1")
        return _j(_receipt({"ok": True, "mode": "spec", "object": info["object"],
                            "collection": info["collection"], "verts": info["verts"],
                            "tris": info["tris"], "bbox_mm": info["bbox_mm"],
                            "spec_hash": norm["spec_hash"], "tessellation": norm["tessellation"],
                            "meta": md["meta"], "spec": _spec_summary(norm)}, t0))
    if isinstance(src, str):
        ob = bpy.data.objects.get(src)
        if ob is None or ob.type != "MESH":
            return _err("对象不存在或不是 mesh: %r" % src, stage="M0")
        try:
            baked = _object_eval_mesh(ob)
        except Exception as e:
            return _err("对象求值失败: %s: %s" % (type(e).__name__, str(e)[:200]), stage="M1")
        import bmesh
        mmu = max(_mmu(), 1e-12)
        nm = str(name or (str(ob.name) + "_tess"))
        me = bpy.data.meshes.new(nm)
        bm = bmesh.new()
        try:
            bv = [bm.verts.new(tuple(v)) for v in baked["verts"].tolist()]
            bm.verts.ensure_lookup_table()
            for tri in baked["tris"]:
                try:
                    bm.faces.new([bv[int(i)] for i in tri])
                except ValueError:
                    continue
            bm.normal_update()
            bm.to_mesh(me)
        finally:
            bm.free()
        nob = bpy.data.objects.new(nm, me)
        coll = _coll(collection)
        coll.objects.link(nob)
        try:
            nob["surface.kind"] = "tessellated"
            nob["surface.source"] = str(ob.name)
            nob["surface.version"] = int(SURFACE_VERSION)
        except Exception:
            pass
        verts_mm = baked["verts"] * mmu
        info = {"object": str(nob.name), "collection": str(coll.name), "mesh": str(me.name),
                "verts": int(len(me.vertices)),
                "tris": int(sum(max(0, len(p.vertices) - 2) for p in me.polygons)),
                "bbox_mm": _bbox_mm(verts_mm)}
        return _j(_receipt({"ok": True, "mode": "object_bake", "source": str(ob.name),
                            "object": info["object"], "collection": info["collection"],
                            "verts": info["verts"], "tris": info["tris"],
                            "bbox_mm": info["bbox_mm"],
                            "note": "对象已按求值网格烘焙（修改器/变换已应用）"}, t0))
    return _err("spec_or_object 必须是 SurfaceSpec 对象或 mesh 对象名", stage="M0")


# ────────────────────────────── M5：只留接口（不假装实现） ──────────────────────────────
_OCCT_MSG = "not implemented in pure-python, OCCT backend required"


def surface_blend(a=None, b=None, method=None, params=None):
    """M5 接口：BRep 混合/圆角需要 OCCT 内核（本包不依赖），明确拒绝而不是给个假曲面。"""
    return _err(_OCCT_MSG, stage="M5", op="blend",
                hint="纯 bpy+numpy 不做 BRep 混合/倒角；需要 OCCT（pythonocc/OCP）后端接线后再开放。"
                     "当前可用替代：用 loft/sweep/patch 手工搭过渡面，或 geo 模块的基元+倒角")


def surface_offset(spec_or_object=None, distance_mm=None, params=None):
    """M5 接口：等距偏置需要 OCCT（稳健 offset 在纯 Python 里会自交/翻面），明确拒绝。"""
    return _err(_OCCT_MSG, stage="M5", op="offset",
                hint="曲面等距偏置在纯 Python 里会自交/翻面，未实现；需要 OCCT 后端。"
                     "当前可用替代：改截面参数重放样（如瓶身壁厚用内外两条 loft）")
# ────────────────────────────── M2：网格分析（全部 mm 口径） ──────────────────────────────
class _SpecError(Exception):
    def __init__(self, errors):
        self.errors = errors or []
        Exception.__init__(self, "spec 校验失败")


def _faces_to_tris(faces):
    out = []
    for f in faces:
        f = [int(i) for i in f]
        for k in range(1, len(f) - 1):
            out.append((f[0], f[k], f[k + 1]))
    return np.asarray(out, dtype=np.int64).reshape(-1, 3)
