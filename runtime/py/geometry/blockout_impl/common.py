# -*- coding: utf-8 -*-
"""DSH Blockout 白模—— 一次调用把"说清楚比例"变成可渲染、可导出、可交接的占位几何。

现场问题：弱模型先手写几何再补比例，往往"看起来有件、量起来没数"；而参考图/装配/干涉检查
都需要一个**尺寸可信、命名统一、集合隔离**的白模。本模块只做白模（不做成品细节）：

ops（走 blender_op；模块尾部注册 K.dsh_blockout_api）：
    base(name, template, units, anchor, parts, landmarks, collection, render, export, audit, reset,
         bevel_mm, template_scale, outdir)   # 建一件白模（bmesh 直建 primitive，mm→BU 换算）
    batch(models, outdir, export, collection, cleanup, render)  # 一次进程建 N 件，逐件 collection/清理
    export(objects|name|collection, glb, obj, render, outdir)   # 对已建场景补视图/补 GLB（不重建）
    selftest / help

base 参数（除 name 外都有默认值）：
    name          模型名（必填；对象名 BO-<name>-<id>，文件名也用它）
    template      none（默认）| humanoid_prim | vehicle —— 内置比例骨架（mm，可按 template_scale 缩放）
    units         mm（默认）| cm | m —— parts 里的 *_mm 数值按这个单位解释（字段名沿用 *_mm 不改名）
    anchor        bottom（默认，模型最低点贴 Z=0）| center（包围盒中心在 Z=0）| origin（不平移）
    parts         [{id, prim:"box|cylinder|uv_sphere", size_mm|[sx,sy,sz]（box）, radius_mm / depth_mm（cylinder）,
                    radius_mm（uv_sphere）, at_mm:[x,y,z], rot_deg:[rx,ry,rz], axis:"x|y|z"（cylinder 轴向）,
                    mirror:true}]
                  box 用 size_mm；cylinder 用 radius_mm + depth_mm + axis；uv_sphere 用 radius_mm。
                  mirror=true 在 X 轴上镜像出第二个对象（对象名后缀 -m，几何真镜像、法线已修正）。
    landmarks     {"名字": [x,y,z]} 或 [{name, at_mm}] —— 只记录进回执（随 anchor 一起平移），不建对象
    collection    集合名（默认 BLOCKOUT；不存在则建，未链接到场景则补链接）
    render        false/null 跳过 | true 用默认 | {engine:"workbench", res:[512,512], views:"auto3",
                  sheet:true, outdir, tag, via:"auto|qc|internal", margin:1.12, lens:50}
    export        false/null 跳过 | true（自动 <outdir>/<name>.glb）| {glb: 路径|true, obj: 路径|true}
    audit         true 时对内建网格做体检（非流形边/零面积面/孤立点），不依赖 audit.py
    reset         true 时先清掉目标集合里旧的 BO-* 对象（默认 false：保留现场）
    bevel_mm      倒角宽度（默认 1.5mm；0=不加；只加 Bevel 修改器、**不 apply**，GLB 导出时 export_apply）
    template_scale 内置模板整体缩放（默认 1.0；只影响 template 的件）
    outdir        输出根目录（render/export 的默认落点；默认 K.out_dir）

render 产物：<outdir>/<tag>_<view>.png × N + 可选 <tag>_sheet.png（横向/网格拼图，纯 numpy）
    views 具名：front/back/left/right/top/bottom/iso/iso_l/iso_back/front_high，
    也可 "auto3"（= iso,front,right）、"front,right,top"、{"name","az","el"} 或 "az=35,el=20"。
    via="auto"（默认）：preload 里有 qc_render 就优先调它的 render_views（Workbench + 取景/锁/哈希都现成），
    失败自动回退内置 Workbench 渲染；via="internal" 强制内置；via="qc" 缺 qc_render 直接报错。
    回执 views[i] = {name, path, ms, bytes, md5, coverage_estimate, bbox_px, res}。

单位口径：**Blender 默认 1 单位 = 1 m**（scene.unit_settings.scale_length 跟随；mm_per_unit 由 kit.units() 给出），
    parts 里的 mm 数 → BU = mm / mm_per_unit（默认 ×0.001）。scene 单位设置不改（不污染用户场景）。

诚实边界：
  · 渲染只支持 Workbench（不接 EEVEE/Cycles：白模要快、要稳）；视角全透视（不提供正交）；
  · sheet 是简单横向/网格拼图（无文字标注）；qc_render 路径下也会自己拼（不依赖 montage）；
  · landmarks 只记录不建空物体（避免污染对象计数）；audit 是内建轻量体检，不是 audit.py 全套；
  · batch 默认逐件清理（cleanup=true），要保留全部件请传 cleanup=false 并保证 name 唯一。
"""
import hashlib
import json as json
import math
import os
import re
import struct as struct
import sys
import tempfile
import time as time

import bpy
import bmesh
import numpy as np  # noqa: F401  # re-exported to the split blockout modules
from mathutils import Euler as Euler, Matrix, Vector as Vector

BLOCKOUT_VERSION = 1

_K = sys.modules.get("dsh_rt_kernel")
_KIT = getattr(_K, "dsh_kit", None)
if _KIT is None:
    raise RuntimeError("blockout.py 需要共享内核 K.dsh_kit（由 KERNEL_BOOTSTRAP 注入）")
_j = _KIT.j

DEFAULT_COLLECTION = "BLOCKOUT"
DEFAULT_MATERIAL = "BO_White"
DEFAULT_BEVEL_MM = 1.5
DEFAULT_RES = (512, 512)
VIEW_AUTO3 = ["iso", "front", "right"]
ALLOWED_TEMPLATES = ("none", "humanoid_prim", "vehicle")
ALLOWED_UNITS = ("mm", "cm", "m")
ALLOWED_ANCHORS = ("bottom", "center", "origin")
ALLOWED_PRIMS = ("box", "cylinder", "uv_sphere")
PART_KEYS = set(["id", "prim", "size_mm", "size", "radius_mm", "radius", "diameter_mm", "diameter",
                 "depth_mm", "depth", "at_mm", "at", "rot_deg", "rot", "axis", "mirror", "name"])
_RENDERABLE_TYPES = ("MESH", "CURVE", "SURFACE", "META", "FONT", "CURVES", "POINTCLOUD", "VOLUME",
                     "GPENCIL", "GREASEPENCIL")
_NAMED_VIEWS = {
    "front": (-90.0, 0.0), "back": (90.0, 0.0), "left": (180.0, 0.0), "right": (0.0, 0.0),
    "top": (0.0, 90.0), "bottom": (0.0, -90.0),
    "iso": (-45.0, 25.0), "iso_l": (45.0, 25.0), "iso_back": (135.0, 25.0), "front_high": (-90.0, 30.0),
}
_VIEW_ALIASES = {"isometric": "iso", "persp": "iso", "hero": "iso", "3q": "iso"}
_SHADING_ATTRS = ("light", "color_type", "show_cavity", "cavity_type", "show_object_outline",
                  "show_specular_highlight")
_RENDER_KEYS = set(["engine", "res", "views", "sheet", "outdir", "tag", "via", "margin", "lens",
                    "film_transparent", "warmup"])


# ────────────────────────────── 小工具 ──────────────────────────────
def _err(msg, hint=None, op=None, **kw):
    d = {"ok": False, "error": str(msg)[:320]}
    if hint:
        d["hint"] = str(hint)[:420]
    if op:
        d["op"] = op
    d.update(kw)
    return d


def _safe_token(s, what="name", maxlen=64):
    t = re.sub(r"[^\w.-]+", "_", str(s if s is not None else "").strip(), flags=re.UNICODE).strip("_.-")
    if not t:
        raise ValueError("%s 不能为空（或全是非法字符）" % what)
    return t[:maxlen]


def _mm_per_bu():
    try:
        v = float(_KIT.units())
        return v if v > 0 else 1000.0
    except Exception:
        return 1000.0


def _unit_to_mm(units):
    u = str(units or "mm").strip().lower()
    if u not in ALLOWED_UNITS:
        raise ValueError("units 只支持 %s（收到 %r）" % ("/".join(ALLOWED_UNITS), units))
    return {"mm": 1.0, "cm": 10.0, "m": 1000.0}[u]


def _num(v, what, default=None, gt=None, minv=None, maxv=None):
    if v is None:
        if default is None:
            raise ValueError("%s 必填" % what)
        return float(default)
    try:
        x = float(v)
    except (TypeError, ValueError):
        raise ValueError("%s 不是数字：%r" % (what, v))
    if not math.isfinite(x):
        raise ValueError("%s 不是有限数：%r" % (what, v))
    if gt is not None and not x > gt:
        raise ValueError("%s 必须 > %g（收到 %g）" % (what, gt, x))
    if minv is not None and x < minv:
        raise ValueError("%s 必须 >= %g（收到 %g）" % (what, minv, x))
    if maxv is not None and x > maxv:
        raise ValueError("%s 必须 <= %g（收到 %g）" % (what, maxv, x))
    return x


def _bool(v, what="参数", default=False):
    if v is None:
        return bool(default)
    if isinstance(v, bool):
        return v
    if isinstance(v, (int, float)):
        return bool(v)
    if isinstance(v, str):
        return v.strip().lower() in ("1", "true", "yes", "on", "y")
    raise ValueError("%s 必须是 bool（收到 %r）" % (what, v))


def _vec3(v, what, factor=None, default=None):
    """[x,y,z] / {"x","y","z"} → list[float]；factor 给定时乘以它（mm→BU 换算）。"""
    if v is None:
        if default is None:
            raise ValueError("%s 必填（[x,y,z]）" % what)
        vals = list(default)
    elif isinstance(v, dict):
        try:
            vals = [v["x"], v["y"], v["z"]]
        except KeyError:
            raise ValueError("%s 用 dict 时必须给 x/y/z 三个键" % what)
    elif isinstance(v, (list, tuple)) and len(v) == 3:
        vals = list(v)
    else:
        raise ValueError("%s 必须是 [x,y,z]（收到 %r）" % (what, v))
    out = []
    for i, vv in enumerate(vals):
        x = _num(vv, "%s[%d]" % (what, i))
        out.append(x * float(factor) if factor else x)
    return out


def _box_size(v, what):
    if isinstance(v, (int, float)):
        s = _num(v, what, gt=0.0)
        return [s, s, s]
    if isinstance(v, dict):
        try:
            vals = [v["x"], v["y"], v["z"]]
        except KeyError:
            raise ValueError("%s 用 dict 时必须给 x/y/z" % what)
    elif isinstance(v, (list, tuple)) and len(v) == 3:
        vals = list(v)
    else:
        raise ValueError("%s 必须是 [sx,sy,sz] 或单个数（立方）" % what)
    return [_num(x, "%s[%d]" % (what, i), gt=0.0) for i, x in enumerate(vals)]


def _win(p):
    """WSL 路径 → Windows（K.win_path 可用时）；其它路径原样。"""
    s = str(p)
    try:
        if s.startswith("/") and _K is not None and hasattr(_K, "win_path"):
            return _K.win_path(s)
    except Exception:
        pass
    return s


def _norm_path(p, base=None):
    s = str(p)
    if s.startswith("/"):
        s = _win(s)
    elif base and not os.path.isabs(s) and not re.match(r"^[A-Za-z]:[\\/]", s):
        s = os.path.join(str(base), s)
    return s


def _root_outdir(raw):
    if raw:
        p = _norm_path(raw)
        try:
            os.makedirs(p, exist_ok=True)
        except Exception as e:
            raise ValueError("outdir 建不出来：%s（%s）" % (p, str(e)[:120]))
        return p
    base = getattr(_K, "out_dir", None) or getattr(_K, "workdir", None) or tempfile.gettempdir()
    base = str(base)
    try:
        os.makedirs(base, exist_ok=True)
        return base
    except Exception:
        return tempfile.gettempdir()


def _md5(path):
    h = hashlib.md5()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _file_receipt(path, expect_nonempty=True):
    if not path or not os.path.isfile(path):
        raise RuntimeError("文件不存在：%r" % (path,))
    n = int(os.path.getsize(path))
    if expect_nonempty and n <= 0:
        raise RuntimeError("文件是空的：%s" % path)
    return {"path": path, "bytes": n, "md5": _md5(path)}


# ────────────────────────────── 场景 / 集合 / 材质 ──────────────────────────────
def _ensure_collection(name):
    nm = str(name or DEFAULT_COLLECTION).strip() or DEFAULT_COLLECTION
    col = bpy.data.collections.get(nm)
    if col is None:
        col = bpy.data.collections.new(nm)
        bpy.context.scene.collection.children.link(col)
        return col
    linked = False
    try:
        for c in bpy.context.scene.collection.children_recursive:
            if c is col:
                linked = True
                break
    except Exception:
        linked = True
    if not linked:
        try:
            bpy.context.scene.collection.children.link(col)
        except Exception:
            pass
    return col


def _white_material():
    mat = bpy.data.materials.get(DEFAULT_MATERIAL)
    if mat is not None:
        return mat
    mat = bpy.data.materials.new(DEFAULT_MATERIAL)
    mat.use_nodes = True
    bsdf = None
    try:
        for node in mat.node_tree.nodes:
            if node.type == "BSDF_PRINCIPLED":
                bsdf = node
                break
    except Exception:
        bsdf = None
    if bsdf is not None:
        try:
            bsdf.inputs["Base Color"].default_value = (0.82, 0.82, 0.82, 1.0)
            bsdf.inputs["Roughness"].default_value = 0.6
            bsdf.inputs["Metallic"].default_value = 0.0
        except Exception:
            pass
    mat.diffuse_color = (0.82, 0.82, 0.82, 1.0)
    return mat


def _add_bevel(ob, width_bu):
    if width_bu <= 0:
        return None
    mod = ob.modifiers.new("Bevel", "BEVEL")
    try:
        mod.width = float(width_bu)
        mod.segments = 2
        mod.limit_method = "ANGLE"
        mod.angle_limit = math.radians(30.0)
        mod.clamp_overlap = True
    except Exception:
        pass
    return mod


def _remove_objects(names):
    removed = []
    for nm in list(names or []):
        ob = bpy.data.objects.get(str(nm))
        if ob is None:
            continue
        data = ob.data if ob.type == "MESH" else None
        try:
            bpy.data.objects.remove(ob, do_unlink=True)
            removed.append(str(nm))
        except Exception:
            continue
        if data is not None and data.users == 0:
            try:
                bpy.data.meshes.remove(data, do_unlink=True)
            except Exception:
                pass
    return removed


def _remove_collection_if_empty(name):
    if not name:
        return False
    col = bpy.data.collections.get(str(name))
    if col is None:
        return False
    try:
        if len(col.objects) or len(col.children):
            return False
        bpy.data.collections.remove(col)
        return True
    except Exception:
        return False


def _clear_bo_objects(col):
    """reset：清目标集合里旧的 BO-* 对象（只认 BO- 前缀，不动用户自己的件）。"""
    removed = []
    for ob in list(col.objects):
        if str(ob.name).startswith("BO-"):
            data = ob.data if ob.type == "MESH" else None
            nm = str(ob.name)
            try:
                bpy.data.objects.remove(ob, do_unlink=True)
            except Exception:
                continue
            removed.append(nm)
            if data is not None and data.users == 0:
                try:
                    bpy.data.meshes.remove(data, do_unlink=True)
                except Exception:
                    pass
    return removed


# ────────────────────────────── primitive（bmesh 直建，不用 bpy.ops）──────────────────────────────
def _mesh_from_bm(bm, name):
    me = bpy.data.meshes.new(str(name))
    try:
        bm.to_mesh(me)
    finally:
        bm.free()
    try:
        me.validate(verbose=False)
        me.update()
    except Exception:
        pass
    return me


def _prim_box(sx, sy, sz, name):
    bm = bmesh.new()
    try:
        bmesh.ops.create_cube(bm, size=1.0)
        bmesh.ops.scale(bm, vec=(float(sx), float(sy), float(sz)), verts=list(bm.verts))
    except Exception:
        bm.free()
        raise
    return _mesh_from_bm(bm, name)


def _prim_cylinder(radius, depth, axis, name, segments=24):
    bm = bmesh.new()
    try:
        bmesh.ops.create_cone(bm, cap_ends=True, cap_tris=False, segments=int(segments),
                              radius1=float(radius), radius2=float(radius), depth=float(depth))
        ax = str(axis or "z").lower()
        if ax == "x":
            bmesh.ops.rotate(bm, cent=(0.0, 0.0, 0.0),
                             matrix=Matrix.Rotation(math.radians(90.0), 3, "Y"), verts=list(bm.verts))
        elif ax == "y":
            bmesh.ops.rotate(bm, cent=(0.0, 0.0, 0.0),
                             matrix=Matrix.Rotation(math.radians(90.0), 3, "X"), verts=list(bm.verts))
    except Exception:
        bm.free()
        raise
    return _mesh_from_bm(bm, name)


def _prim_sphere(radius, name, u_segments=24, v_segments=12):
    bm = bmesh.new()
    try:
        bmesh.ops.create_uvsphere(bm, u_segments=int(u_segments), v_segments=int(v_segments),
                                  radius=float(radius))
    except Exception:
        bm.free()
        raise
    return _mesh_from_bm(bm, name)


def _mirror_object(ob, new_name, col):
    """真镜像（局部 x→-x + 面反向 + 世界矩阵 S·M·S），法线不反、导出不需要负缩放。"""
    me = ob.data.copy()
    bm = bmesh.new()
    try:
        bm.from_mesh(me)
        for v in bm.verts:
            v.co.x = -v.co.x
        bmesh.ops.reverse_faces(bm, faces=list(bm.faces))
        bm.to_mesh(me)
    finally:
        bm.free()
    try:
        me.validate(verbose=False)
        me.update()
    except Exception:
        pass
    nob = bpy.data.objects.new(str(new_name), me)
    col.objects.link(nob)
    S = Matrix.Diagonal((-1.0, 1.0, 1.0, 1.0))
    nob.matrix_world = S @ ob.matrix_world @ S
    return nob


__all__ = [name for name in list(globals()) if not name.startswith("__")]
