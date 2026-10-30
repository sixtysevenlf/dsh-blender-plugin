# -*- coding: utf-8 -*-
"""DSH Blockout 白模（P0-C）—— 一次调用把"说清楚比例"变成可渲染、可导出、可交接的占位几何。

现场问题：弱模型先手写几何再补比例，往往"看起来有件、量起来没数"；而参考图/装配/干涉检查
都需要一个**尺寸可信、命名统一、集合隔离**的白模。本模块只做白模（不做成品细节）：

ops（走 blender_rt_plan；模块尾部注册 K.dsh_blockout_api）：
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

诚实边界（P0）：
  · 渲染只支持 Workbench（不接 EEVEE/Cycles：白模要快、要稳）；视角全透视（P0 不提供正交）；
  · sheet 是简单横向/网格拼图（无文字标注）；qc_render 路径下也会自己拼（不依赖 montage）；
  · landmarks 只记录不建空物体（避免污染对象计数）；audit 是内建轻量体检，不是 audit.py 全套；
  · batch 默认逐件清理（cleanup=true），要保留全部件请传 cleanup=false 并保证 name 唯一。
"""
import hashlib
import json
import math
import os
import re
import struct
import sys
import tempfile
import time

import bpy
import bmesh
import numpy as np
from mathutils import Euler, Matrix, Vector

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


# ────────────────────────────── 内置比例模板（mm；只放 primitive）──────────────────────────────
def _scale_value(v, s):
    if isinstance(v, (int, float)):
        return float(v) * s
    if isinstance(v, dict):
        return {k: _scale_value(x, s) for k, x in v.items()}
    if isinstance(v, (list, tuple)):
        return [_scale_value(x, s) for x in v]
    return v


def _scaled_parts(parts, s):
    out = []
    for p in parts:
        q = dict(p)
        for k in list(q.keys()):
            if k in ("id", "prim", "axis", "mirror", "name"):
                continue
            q[k] = _scale_value(q[k], s)
        out.append(q)
    return out


def _template_humanoid():
    """7.5 头身素体（总高 ≈1750mm）：头/颈/胸/胯/手臂/腿/脚，四肢用 mirror 出左右。"""
    H = 1750.0
    head_h = H / 7.5
    head_r = head_h * 0.46
    head_z = H - head_r
    chest_top = H - 1.5 * head_h
    chest_h = 1.1 * head_h
    chest_z = chest_top - chest_h / 2.0
    pelvis_h = 0.95 * head_h
    pelvis_z = chest_top - chest_h - pelvis_h / 2.0
    arm_len = 3.1 * head_h
    arm_z = chest_top - arm_len / 2.0
    leg_top = pelvis_z - pelvis_h / 2.0
    leg_len = leg_top
    foot_h = 0.42 * head_h
    foot_y = 0.32 * head_h
    return [
        {"id": "head", "prim": "uv_sphere", "radius_mm": head_r, "at_mm": [0, 0, head_z]},
        {"id": "neck", "prim": "cylinder", "radius_mm": 0.21 * head_h, "depth_mm": 0.42 * head_h,
         "at_mm": [0, 0, head_z - head_r - 0.10 * head_h], "axis": "z"},
        {"id": "chest", "prim": "box", "size_mm": [1.8 * head_h, 1.05 * head_h, chest_h],
         "at_mm": [0, 0, chest_z]},
        {"id": "pelvis", "prim": "box", "size_mm": [1.3 * head_h, 0.95 * head_h, pelvis_h],
         "at_mm": [0, 0, pelvis_z]},
        {"id": "arm", "prim": "cylinder", "radius_mm": 0.26 * head_h, "depth_mm": arm_len,
         "at_mm": [1.15 * head_h, 0, arm_z], "axis": "z", "mirror": True},
        {"id": "hand", "prim": "uv_sphere", "radius_mm": 0.30 * head_h,
         "at_mm": [1.15 * head_h, 0, arm_z - arm_len / 2.0 - 0.15 * head_h], "mirror": True},
        {"id": "leg", "prim": "cylinder", "radius_mm": 0.36 * head_h, "depth_mm": leg_len,
         "at_mm": [0.47 * head_h, 0, leg_len / 2.0], "axis": "z", "mirror": True},
        {"id": "foot", "prim": "box", "size_mm": [0.55 * head_h, 1.1 * head_h, foot_h],
         "at_mm": [0.47 * head_h, foot_y, foot_h / 2.0], "mirror": True},
    ]


def _template_vehicle():
    """轿车占位：X=车宽、Y=车长、Z=高；车身+座舱+四轮（轮轴沿 X，mirror 出左右）。"""
    return [
        {"id": "body", "prim": "box", "size_mm": [1800, 4200, 700], "at_mm": [0, 0, 800]},
        {"id": "cabin", "prim": "box", "size_mm": [1500, 2200, 650], "at_mm": [0, -200, 1375]},
        {"id": "wheel_fr", "prim": "cylinder", "radius_mm": 350, "depth_mm": 260,
         "at_mm": [900, 1400, 350], "axis": "x", "mirror": True},
        {"id": "wheel_rr", "prim": "cylinder", "radius_mm": 350, "depth_mm": 260,
         "at_mm": [900, -1400, 350], "axis": "x", "mirror": True},
    ]


def _template_parts(template, scale):
    if template == "humanoid_prim":
        parts = _template_humanoid()
    elif template == "vehicle":
        parts = _template_vehicle()
    else:
        raise ValueError("template 只支持 %s（收到 %r）" % ("/".join(ALLOWED_TEMPLATES), template))
    return _scaled_parts(parts, float(scale))


# ────────────────────────────── 参数解析 ──────────────────────────────
def _parse_part(spec, index, units):
    if not isinstance(spec, dict):
        raise ValueError("parts[%d] 必须是 dict（收到 %r）" % (index, spec))
    unknown = sorted(set(str(k) for k in spec.keys()) - PART_KEYS)
    if unknown:
        raise ValueError("parts[%d] 不认识的键 %s（可用：%s）"
                         % (index, unknown, ",".join(sorted(PART_KEYS))))
    prim_raw = str(spec.get("prim") or "").strip().lower()
    prim = {"box": "box", "cube": "box", "cylinder": "cylinder", "cyl": "cylinder",
            "uv_sphere": "uv_sphere", "uvsphere": "uv_sphere", "sphere": "uv_sphere"}.get(prim_raw)
    if prim is None:
        raise ValueError("parts[%d].prim 只支持 box/cylinder/uv_sphere（收到 %r）" % (index, spec.get("prim")))
    pid = _safe_token(spec.get("id") or spec.get("name") or ("p%02d" % index), "parts[%d].id" % index)
    factor = _unit_to_mm(units) / _mm_per_bu()     # 输入单位 → Blender 单位
    at = _vec3(spec.get("at_mm", spec.get("at")), "%s.at_mm" % pid, factor, default=(0.0, 0.0, 0.0))
    rot = _vec3(spec.get("rot_deg", spec.get("rot")), "%s.rot_deg" % pid, None, default=(0.0, 0.0, 0.0))
    mirror = _bool(spec.get("mirror"), "%s.mirror" % pid, False)
    axis = str(spec.get("axis") or "z").strip().lower()
    if axis not in ("x", "y", "z"):
        raise ValueError("%s.axis 只支持 x/y/z（收到 %r）" % (pid, spec.get("axis")))
    out = {"id": pid, "prim": prim, "at_bu": at, "rot_deg": rot, "mirror": mirror, "axis": axis,
           "size_bu": None, "radius_bu": None, "depth_bu": None,
           "size_mm": None, "radius_mm": None, "depth_mm": None}
    if prim == "box":
        if spec.get("radius_mm") is not None or spec.get("radius") is not None:
            raise ValueError("%s 是 box：用 size_mm=[x,y,z]，不要 radius_mm" % pid)
        raw = spec.get("size_mm", spec.get("size"))
        if raw is None:
            raise ValueError("%s 是 box：必须给 size_mm=[x,y,z]（或单个数=立方）" % pid)
        size_mm = _box_size(raw, "%s.size_mm" % pid)
        out["size_mm"] = size_mm
        out["size_bu"] = [v * factor for v in size_mm]
    elif prim == "cylinder":
        if spec.get("size_mm") is not None or spec.get("size") is not None:
            raise ValueError("%s 是 cylinder：用 radius_mm + depth_mm（axis=x|y|z），不要 size_mm" % pid)
        r = spec.get("radius_mm", spec.get("radius"))
        d = spec.get("diameter_mm", spec.get("diameter"))
        if r is not None and d is not None:
            raise ValueError("%s 同时给了 radius_mm 和 diameter_mm：只给一个" % pid)
        if r is None and d is not None:
            r = _num(d, "%s.diameter_mm" % pid, gt=0.0) / 2.0
        if r is None:
            raise ValueError("%s 是 cylinder：必须给 radius_mm（或 diameter_mm）" % pid)
        depth = _num(spec.get("depth_mm", spec.get("depth")), "%s.depth_mm" % pid, gt=0.0)
        radius = _num(r, "%s.radius_mm" % pid, gt=0.0)
        out["radius_mm"] = radius
        out["depth_mm"] = depth
        out["radius_bu"] = radius * factor
        out["depth_bu"] = depth * factor
    else:
        for bad in ("size_mm", "size", "depth_mm", "depth"):
            if spec.get(bad) is not None:
                raise ValueError("%s 是 uv_sphere：用 radius_mm，不要 %s" % (pid, bad))
        r = spec.get("radius_mm", spec.get("radius"))
        d = spec.get("diameter_mm", spec.get("diameter"))
        if r is not None and d is not None:
            raise ValueError("%s 同时给了 radius_mm 和 diameter_mm：只给一个" % pid)
        if r is None and d is not None:
            r = _num(d, "%s.diameter_mm" % pid, gt=0.0) / 2.0
        if r is None:
            raise ValueError("%s 是 uv_sphere：必须给 radius_mm（或 diameter_mm）" % pid)
        radius = _num(r, "%s.radius_mm" % pid, gt=0.0)
        out["radius_mm"] = radius
        out["radius_bu"] = radius * factor
    return out


def _dedupe_ids(parsed):
    seen = {}
    warnings = []
    for p in parsed:
        base = p["id"]
        k = seen.get(base, 0)
        seen[base] = k + 1
        if k:
            p["id"] = "%s-%d" % (base, k + 1)
            warnings.append("重复的 part id %r 已重命名为 %r" % (base, p["id"]))
    return warnings


def _parse_landmarks(raw, units):
    if raw is None:
        return [], []
    factor = _unit_to_mm(units) / _mm_per_bu()
    items = []
    if isinstance(raw, dict):
        for k, v in raw.items():
            items.append((str(k), v))
    elif isinstance(raw, (list, tuple)):
        for i, e in enumerate(raw):
            if not isinstance(e, dict):
                raise ValueError("landmarks[%d] 必须是 dict（{name, at_mm}）" % i)
            nm = e.get("name", e.get("id", "lm%02d" % i))
            at = e.get("at_mm", e.get("at", e.get("pos")))
            items.append((str(nm), at))
    else:
        raise ValueError("landmarks 必须是 {名字:[x,y,z]} 或 [{name,at_mm}]")
    out = []
    for nm, at in items:
        try:
            vals = _vec3(at, "landmarks[%r]" % nm, factor)
        except ValueError as e:
            raise ValueError(str(e))
        out.append((str(nm)[:64], vals))
    return out, []


def _parse_res(v):
    if v is None:
        return list(DEFAULT_RES)
    if isinstance(v, (int, float)):
        n = int(_num(v, "render.res", gt=0.0, maxv=4096))
        res = [n, n]
    elif isinstance(v, (list, tuple)) and len(v) == 2:
        res = [int(_num(x, "render.res[%d]" % i, gt=0)) for i, x in enumerate(v)]
    elif isinstance(v, str) and "x" in v.lower():
        try:
            a, b = v.lower().split("x", 1)
            res = [int(a), int(b)]
        except Exception:
            raise ValueError("render.res 写法看不懂：%r（用 [512,512] 或 512）" % (v,))
    else:
        raise ValueError("render.res 必须是 [w,h] 或单个数（收到 %r）" % (v,))
    if not (64 <= res[0] <= 4096 and 64 <= res[1] <= 4096):
        raise ValueError("render.res 每边必须在 64..4096（收到 %s）" % res)
    return res


def _view_spec(v, index):
    if isinstance(v, dict):
        try:
            az = _num(v.get("az"), "views[%d].az" % index)
            el = _num(v.get("el"), "views[%d].el" % index, default=0.0)
        except ValueError:
            raise
        nm = _safe_token(v.get("name") or ("az%d_el%d" % (int(round(az)), int(round(el)))), "views[%d].name" % index)
        return {"name": nm, "az": az, "el": el}
    if not isinstance(v, str) or not v.strip():
        raise ValueError("views[%d] 必须是视角名或 {name,az,el}（收到 %r）" % (index, v))
    s = v.strip().lower()
    if "az=" in s:
        kv = {}
        for part in s.replace(" ", "").split(","):
            if "=" in part:
                k, val = part.split("=", 1)
                try:
                    kv[k] = float(val)
                except ValueError:
                    raise ValueError("views[%d] 角度的 az=/el= 不是数字：%r" % (index, v))
        az = kv.get("az")
        if az is None:
            raise ValueError("views[%d] 的 az= 写法必须带 az（收到 %r）" % (index, v))
        el = kv.get("el", 0.0)
        nm = _safe_token("az%d_el%d" % (int(round(az)), int(round(el))), "views[%d].name" % index)
        return {"name": nm, "az": az, "el": el}
    s = _VIEW_ALIASES.get(s, s)
    if s not in _NAMED_VIEWS:
        raise ValueError("未知视角 %r（可用：%s；或写 az=..,el=.. / {name,az,el}）"
                         % (v, ", ".join(sorted(_NAMED_VIEWS))))
    az, el = _NAMED_VIEWS[s]
    return {"name": s, "az": az, "el": el}


def _resolve_views(raw):
    if raw is None:
        raw = list(VIEW_AUTO3)
    if isinstance(raw, str):
        s = raw.strip()
        if s.lower() in ("auto3", "auto", "default"):
            raw = list(VIEW_AUTO3)
        else:
            raw = [x.strip() for x in s.replace(";", ",").split(",") if x.strip()]
    if not isinstance(raw, (list, tuple)):
        raise ValueError("render.views 必须是 list / 'auto3' / 'front,right,top'（收到 %r）" % (raw,))
    if not raw:
        raise ValueError("render.views 不能是空 list")
    if len(raw) > 12:
        raise ValueError("render.views 最多 12 个（收到 %d）" % len(raw))
    out, seen = [], set()
    for i, v in enumerate(raw):
        spec = _view_spec(v, i)
        if spec["name"] in seen:
            continue
        seen.add(spec["name"])
        out.append(spec)
    if not out:
        raise ValueError("render.views 解析后为空")
    return out


def _parse_render_opt(render, root, tag):
    if render is None or render is False or render == 0:
        return None
    if render is True:
        opt = {}
    elif isinstance(render, str):
        opt = {"views": render}
    elif isinstance(render, dict):
        opt = dict(render)
    else:
        raise ValueError("render 必须是 dict / true / false（收到 %r）" % (render,))
    unknown = sorted(set(str(k) for k in opt.keys()) - _RENDER_KEYS)
    if unknown:
        raise ValueError("render 里不认识的键 %s（可用：%s）"
                         % (unknown, ",".join(sorted(_RENDER_KEYS))))
    eng = str(opt.get("engine") or "workbench").strip().lower()
    if eng not in ("workbench", "wb"):
        raise ValueError("render.engine 只支持 workbench（P0；收到 %r）—— 白模要快、要稳，不接 EEVEE/Cycles" % (opt.get("engine"),))
    res = _parse_res(opt.get("res"))
    views = _resolve_views(opt.get("views"))
    outdir = _norm_path(opt.get("outdir"), root) if opt.get("outdir") else _norm_path(root)
    try:
        os.makedirs(outdir, exist_ok=True)
    except Exception as e:
        raise ValueError("render.outdir 建不出来：%s（%s）" % (outdir, str(e)[:120]))
    rtag = _safe_token(opt.get("tag") or tag, "render.tag")
    sheet = opt.get("sheet", True)
    if isinstance(sheet, bool):
        sheet_cols = len(views) if sheet else 0
    elif isinstance(sheet, (int, float)):
        sheet_cols = int(sheet)
        if sheet_cols < 0:
            raise ValueError("render.sheet 为数字时必须 >=0")
        sheet_cols = min(sheet_cols, len(views))
    else:
        raise ValueError("render.sheet 必须是 bool 或列数（收到 %r）" % (sheet,))
    via = str(opt.get("via") or "auto").strip().lower()
    if via not in ("auto", "qc", "internal"):
        raise ValueError("render.via 只支持 auto/qc/internal（收到 %r）" % (opt.get("via"),))
    margin = _num(opt.get("margin"), "render.margin", default=1.12, minv=1.01, maxv=4.0)
    lens = _num(opt.get("lens"), "render.lens", default=50.0, gt=1.0, maxv=300.0)
    film = _bool(opt.get("film_transparent"), "render.film_transparent", True)
    return {"engine": "workbench", "res": res, "views": views, "outdir": outdir, "tag": rtag,
            "sheet_cols": sheet_cols, "via": via, "margin": margin, "lens": lens,
            "film_transparent": film}


def _parse_export_opt(export, root, stem):
    if export is None or export is False or export == 0:
        return None
    if export is True:
        exp = {"glb": True}
    elif isinstance(export, str):
        exp = {"glb": export}
    elif isinstance(export, dict):
        exp = dict(export)
    else:
        raise ValueError("export 必须是 dict / true / false / 路径字符串（收到 %r）" % (export,))
    unknown = sorted(set(str(k) for k in exp.keys()) - set(["glb", "obj", "outdir"]))
    if unknown:
        raise ValueError("export 里不认识的键 %s（可用：glb,obj,outdir）" % unknown)
    sub_out = _norm_path(exp.get("outdir") or root)
    paths = {}
    for k in ("glb", "obj"):
        v = exp.get(k)
        if v is None or v is False:
            continue
        if v is True:
            p = os.path.join(sub_out, str(stem) + "." + k)
        elif isinstance(v, str) and v.strip():
            p = _norm_path(v.strip(), sub_out)
        else:
            raise ValueError("export.%s 必须是路径或 true（收到 %r）" % (k, v))
        paths[k] = p
    if not paths:
        raise ValueError("export 里没给 glb/obj（可写 true 自动命名，或给路径）")
    return paths


# ────────────────────────────── 度量 / 体检 ──────────────────────────────
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
                         "hint": "sheet 是附加产物（P0 允许只给单张 views）：先看 views 里的 PNG"}
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


# ────────────────────────────── op: base ──────────────────────────────
def blockout_base(name=None, template="none", units="mm", anchor="bottom", parts=None,
                  landmarks=None, collection=None, render=None, export=None, audit=False,
                  reset=False, bevel_mm=DEFAULT_BEVEL_MM, template_scale=1.0, outdir=None):
    t_start = time.perf_counter()
    try:
        nm = _safe_token(name, "name")
        tmpl = str(template or "none").strip().lower()
        if tmpl not in ALLOWED_TEMPLATES:
            raise ValueError("template 只支持 %s（收到 %r）" % ("/".join(ALLOWED_TEMPLATES), template))
        un = str(units or "mm").strip().lower()
        un_mm = _unit_to_mm(un)
        anc = str(anchor or "bottom").strip().lower()
        if anc not in ALLOWED_ANCHORS:
            raise ValueError("anchor 只支持 %s（收到 %r）" % ("/".join(ALLOWED_ANCHORS), anchor))
        bevel_val = _num(bevel_mm, "bevel_mm", default=DEFAULT_BEVEL_MM, minv=0.0, maxv=20.0)
        tscale = _num(template_scale, "template_scale", default=1.0, gt=0.0, maxv=20.0)
        root = _root_outdir(outdir)
        ropt = _parse_render_opt(render, root, nm)
        eopt = _parse_export_opt(export, root, nm)
        do_audit = _bool(audit, "audit", False)
        do_reset = _bool(reset, "reset", False)
        lm_items, lm_warn = _parse_landmarks(landmarks, un)
        tmpl_parts = _template_parts(tmpl, tscale) if tmpl != "none" else []
        user_parts = []
        if parts is not None:
            if not isinstance(parts, (list, tuple)):
                raise ValueError("parts 必须是 list（收到 %r）" % (type(parts).__name__,))
            user_parts = list(parts)
        if not tmpl_parts and not user_parts:
            raise ValueError("没有几何件：template=none 时 parts 必填；或给 template=humanoid_prim/vehicle")
        parsed = []
        for i, sp in enumerate(tmpl_parts):
            parsed.append(_parse_part(sp, i, "mm"))
        for j, sp in enumerate(user_parts):
            parsed.append(_parse_part(sp, len(parsed) + j, un))
        warnings = list(lm_warn)
        warnings.extend(_dedupe_ids(parsed))
    except ValueError as e:
        return _err(e, hint="base 参数说明见 K.dsh_blockout_api['help']()；parts 每件要 prim+尺寸+at_mm",
                    op="base")
    except Exception as e:
        return _err("%s: %s" % (type(e).__name__, str(e)), hint="参数解析失败，场景未改动", op="base")
    try:
        col = _ensure_collection(collection or DEFAULT_COLLECTION)
        removed = _clear_bo_objects(col) if do_reset else []
        mat = _white_material()
        bevel_bu = bevel_val / _mm_per_bu()
        objs = []
        recs = []
        for p in parsed:
            stem = "BO-%s-%s" % (nm, p["id"])
            if p["prim"] == "box":
                me = _prim_box(p["size_bu"][0], p["size_bu"][1], p["size_bu"][2], stem)
            elif p["prim"] == "cylinder":
                me = _prim_cylinder(p["radius_bu"], p["depth_bu"], p["axis"], stem)
            else:
                me = _prim_sphere(p["radius_bu"], stem)
            try:
                me.materials.append(mat)
            except Exception:
                pass
            ob = bpy.data.objects.new(stem, me)
            col.objects.link(ob)
            ob.location = tuple(p["at_bu"])
            ob.rotation_mode = "XYZ"
            ob.rotation_euler = Euler([math.radians(a) for a in p["rot_deg"]], "XYZ")
            _add_bevel(ob, bevel_bu)
            objs.append(ob)
            recs.append({"id": p["id"], "object": ob.name, "prim": p["prim"],
                         "at_mm": [round(v * _mm_per_bu(), 4) for v in p["at_bu"]],
                         "size_mm": p["size_mm"], "radius_mm": p["radius_mm"],
                         "depth_mm": p["depth_mm"], "axis": p["axis"] if p["prim"] == "cylinder" else None,
                         "rot_deg": p["rot_deg"], "mirror": bool(p["mirror"]),
                         "mirror_object": None, "modifiers": ["Bevel"] if bevel_bu > 0 else []})
        try:
            bpy.context.view_layer.update()
        except Exception:
            pass
        # 镜像件（在矩阵更新后做：真镜像 + 法线修正）
        for ob, rec in list(zip(list(objs), recs)):
            if not rec["mirror"]:
                continue
            mob = _mirror_object(ob, ob.name + "-m", col)
            _add_bevel(mob, bevel_bu)
            rec["mirror_object"] = mob.name
            objs.append(mob)
        try:
            bpy.context.view_layer.update()
        except Exception:
            pass
        # anchor：整体平移（只动 Z；X/Y 保持输入位置，便于 bbox 对拍）
        mn0, mx0 = _bbox_bu(objs)
        if anc == "bottom":
            dz = -float(mn0.z)
        elif anc == "center":
            dz = -float(mn0.z + mx0.z) * 0.5
        else:
            dz = 0.0
        if abs(dz) > 1e-12:
            for ob in objs:
                ob.location.z = float(ob.location.z) + dz
            try:
                bpy.context.view_layer.update()
            except Exception:
                pass
        mn, mx = _bbox_bu(objs)
        geom_ms = int(round((time.perf_counter() - t_start) * 1000.0))
        landmarks_out = {}
        for lm_name, vals in lm_items:
            landmarks_out[lm_name] = [round((vals[i] + (dz if i == 2 else 0.0)) * _mm_per_bu(), 4)
                                      for i in range(3)]
        total_tris, per_tris = _tris_base(objs)
        for rec, t in zip(recs, per_tris):
            rec["tris"] = int(t)
        result = {
            "ok": True, "version": BLOCKOUT_VERSION, "op": "base", "name": nm,
            "template": tmpl, "units": un, "anchor": anc,
            "collection": col.name, "material": mat.name,
            "object_count": len(objs), "objects": [ob.name for ob in objs],
            "parts": recs, "total_tris": int(total_tris), "total_tris_eval": _tris_eval(objs),
            "bbox_mm": _bbox_mm(mn, mx),
            "landmarks": landmarks_out,
            "bevel_mm": bevel_val, "reset_removed": removed,
            "units_meta": {"mm_per_unit": round(_mm_per_bu(), 6), "input": un,
                           "input_mm_factor": un_mm},
            "warnings": warnings,
            "ms": {"geom": geom_ms},
        }
        if do_audit:
            result["audit"] = _audit_objects(objs)
            if not result["audit"].get("ok"):
                result["ok"] = False
                result.setdefault("error", "内建体检没通过：%s" % (result["audit"].get("note") or ""))
                result.setdefault("hint", "看 audit.per_object；白模一般是封闭 primitive，出现非流形多半是参数/镜像异常")
        if ropt is not None:
            try:
                result["render"] = _render_views(objs, ropt, nm)
                if not result["render"].get("ok"):
                    result["ok"] = False
                    result.setdefault("error", "渲染失败")
            except Exception as e:
                result["render"] = {"ok": False, "error": "%s: %s" % (type(e).__name__, str(e)[:260]),
                                    "hint": "渲染是附加产物；场景里的几何已建好（objects 在回执里）"}
                result["ok"] = False
                result.setdefault("error", "渲染失败：%s" % str(e)[:200])
        if eopt is not None:
            try:
                result["export"] = _export_objs(objs, eopt)
                if not result["export"].get("ok"):
                    result["ok"] = False
                    result.setdefault("error", result["export"].get("error") or "导出失败")
            except Exception as e:
                result["export"] = {"ok": False, "error": "%s: %s" % (type(e).__name__, str(e)[:260])}
                result["ok"] = False
                result.setdefault("error", "导出失败：%s" % str(e)[:200])
        result["ms"]["total"] = int(round((time.perf_counter() - t_start) * 1000.0))
        return result
    except Exception as e:
        return _err("%s: %s" % (type(e).__name__, str(e)[:280]),
                    hint="建几何阶段失败；场景可能留下部分对象，可用 K.dsh_qc_api 或 blender_rt_txn 回退",
                    op="base", name=name)


# ────────────────────────────── op: batch ──────────────────────────────
def blockout_batch(models=None, outdir=None, export=None, collection=None, cleanup=True, render=None):
    t_start = time.perf_counter()
    try:
        if not isinstance(models, (list, tuple)) or not models:
            raise ValueError("models 必须是非空 list（每项是 base 的参数 dict，至少含 name+parts）")
        if len(models) > 100:
            raise ValueError("models 最多 100 件（收到 %d）" % len(models))
        cleanup_b = _bool(cleanup, "cleanup", True)
        root = _root_outdir(outdir)
        base_col = str(collection or "BLOCKOUT-BATCH").strip() or "BLOCKOUT-BATCH"
        names = []
        for i, m in enumerate(models):
            if not isinstance(m, dict):
                raise ValueError("models[%d] 必须是 dict（收到 %r）" % (i, type(m).__name__))
            mn = _safe_token(m.get("name") or ("m%02d" % i), "models[%d].name" % i)
            if mn in names:
                raise ValueError("models 里 name 重复：%r（导出文件名会互相覆盖，先改名）" % mn)
            names.append(mn)
    except ValueError as e:
        return _err(e, hint="batch 的每项与 base 同构；建议每件至少 {name, parts}", op="batch")
    except Exception as e:
        return _err("%s: %s" % (type(e).__name__, str(e)), op="batch")
    items = []
    ok_all = True
    for i, spec in enumerate(models):
        item = dict(spec)
        nm = names[i]
        item["name"] = nm
        if "collection" not in item or not item.get("collection"):
            item["collection"] = "%s-%s" % (base_col, nm)
        if item.get("render") is None and render is not None:
            item["render"] = render
        if item.get("export") is None and export is not None:
            item["export"] = export
        if item.get("outdir") is None:
            item["outdir"] = root
        try:
            r = blockout_base(**item)
        except TypeError as e:
            r = _err("参数不匹配：%s" % str(e)[:220],
                     hint="batch 项与 base 参数同名（见 help）；不认识的键已列出", op="batch-item",
                     name=nm)
        except Exception as e:
            r = _err("%s: %s" % (type(e).__name__, str(e)[:240]), op="batch-item", name=nm)
        items.append(r)
        if not r.get("ok"):
            ok_all = False
        if cleanup_b:
            try:
                _remove_objects(r.get("objects") or [])
                _remove_collection_if_empty(item.get("collection"))
            except Exception:
                pass
    built = sum(1 for r in items if r.get("ok"))
    return {"ok": bool(ok_all), "op": "batch", "count": len(items), "built": built,
            "failed": [it.get("name") for it in items if not it.get("ok")],
            "items": items, "collection": base_col, "cleanup": cleanup_b, "outdir": root,
            "ms": int(round((time.perf_counter() - t_start) * 1000.0)),
            "total_ms": int(round((time.perf_counter() - t_start) * 1000.0))}


# ────────────────────────────── op: export（对已建场景补）─────────────────────────────
def _find_model_objects(objects=None, name=None, collection=None):
    found = []
    missing = []
    if objects:
        names = [objects] if isinstance(objects, str) else list(objects)
        for n in names:
            ob = bpy.data.objects.get(str(n))
            if ob is None:
                missing.append(str(n))
            elif ob.type == "MESH":
                found.append(ob)
    if name:
        prefix = "BO-%s-" % _safe_token(name, "name")
        for ob in bpy.context.scene.objects:
            if ob.type == "MESH" and str(ob.name).startswith(prefix):
                found.append(ob)
    if collection:
        col = bpy.data.collections.get(str(collection))
        if col is None:
            missing.append("collection:%s" % collection)
        else:
            for ob in col.objects:
                if ob.type == "MESH" and str(ob.name).startswith("BO-"):
                    found.append(ob)
    uniq = []
    seen = set()
    for ob in found:
        if ob.name not in seen:
            seen.add(ob.name)
            uniq.append(ob)
    if missing:
        raise ValueError("找不到这些对象：%s" % missing[:6])
    if not uniq:
        raise ValueError("找不到已建白模：给 objects=[名字] / name=<模型名> / collection=<集合名>")
    return uniq


def blockout_export(objects=None, name=None, collection=None, glb=None, obj=None, export=None,
                    render=None, outdir=None):
    t_start = time.perf_counter()
    try:
        objs = _find_model_objects(objects=objects, name=name, collection=collection)
        root = _root_outdir(outdir)
        # export 参数既可嵌套 dict，也可平级给 glb/obj
        exp = {}
        if isinstance(export, dict):
            exp.update(export)
        elif export is True:
            exp["glb"] = True
        elif isinstance(export, str) and export.strip():
            exp["glb"] = export
        elif export not in (None, False, 0):
            raise ValueError("export 必须是 dict / true / false / 路径字符串（收到 %r）" % (export,))
        if glb is not None:
            exp["glb"] = glb
        if obj is not None:
            exp["obj"] = obj
        stem = _safe_token(name or (objs[0].name if objs else "export"), "name")
        eopt = _parse_export_opt(exp if exp else None, root, stem)
        ropt = _parse_render_opt(render, root, stem)
        if eopt is None and ropt is None:
            raise ValueError("什么都没要求：至少给 render=<视图参数> / glb= / obj= 之一")
        mn, mx = _bbox_bu(objs)
        result = {"ok": True, "op": "export", "objects": [ob.name for ob in objs],
                  "object_count": len(objs), "bbox_mm": _bbox_mm(mn, mx)}
        if ropt is not None:
            try:
                result["render"] = _render_views(objs, ropt, stem)
                if not result["render"].get("ok"):
                    result["ok"] = False
            except Exception as e:
                result["render"] = {"ok": False, "error": "%s: %s" % (type(e).__name__, str(e)[:260])}
                result["ok"] = False
                result.setdefault("error", "渲染失败：%s" % str(e)[:200])
        if eopt is not None:
            try:
                result["export"] = _export_objs(objs, eopt)
                if not result["export"].get("ok"):
                    result["ok"] = False
                    result.setdefault("error", result["export"].get("error") or "导出失败")
            except Exception as e:
                result["export"] = {"ok": False, "error": "%s: %s" % (type(e).__name__, str(e)[:260])}
                result["ok"] = False
                result.setdefault("error", "导出失败：%s" % str(e)[:200])
        result["ms"] = int(round((time.perf_counter() - t_start) * 1000.0))
        return result
    except ValueError as e:
        return _err(e, hint="export 不重建几何：对象要先用 base 建好；名字形如 BO-<name>-<id>", op="export")
    except Exception as e:
        return _err("%s: %s" % (type(e).__name__, str(e)[:280]), op="export")


# ────────────────────────────── 场景呈现（turntable / refsheet 共用）──────────────────────────────
_PRESENT_RENDER_ATTRS = ("resolution_x", "resolution_y", "resolution_percentage", "film_transparent",
                         "fps", "fps_base", "filepath")
_PRESENT_IMG_ATTRS = ("media_type", "file_format", "color_mode", "color_depth")
_PRESENT_FF_ATTRS = ("format", "codec", "constant_rate_factor", "audio_codec", "ffmpeg_preset")
_PRESENT_SHADING_ATTRS = ("light", "color_type", "single_color", "show_cavity", "cavity_type",
                          "show_object_outline", "object_outline_color", "show_specular_highlight",
                          "background_type", "background_color")
_BG_COLORS = {"white": 1.0, "gray": 0.5, "grey": 0.5, "black": 0.0, "darkgray": 0.18}
_REF_VIEW_ALIASES = {"front": "front", "back": "back", "left": "left", "side": "side", "right": "side",
                     "top": "top", "bottom": "bottom", "iso": "iso", "isometric": "iso", "hero": "iso"}
_REF_PERSP_ANGLES = {"front": (-90.0, 0.0), "back": (90.0, 0.0), "side": (0.0, 0.0),
                     "left": (180.0, 0.0), "top": (0.0, 90.0), "bottom": (0.0, -90.0),
                     "iso": (-45.0, 25.0)}


def _present_state_save(sc):
    st = {"render": {}, "img": {}, "ff": {}, "sh": {}, "scene": {}, "misc": {}}
    for a in _PRESENT_RENDER_ATTRS:
        try:
            st["render"][a] = getattr(sc.render, a)
        except Exception:
            pass
    try:
        st["render"]["engine"] = sc.render.engine
    except Exception:
        pass
    for a in _PRESENT_IMG_ATTRS:
        try:
            st["img"][a] = getattr(sc.render.image_settings, a)
        except Exception:
            pass
    for a in _PRESENT_FF_ATTRS:
        try:
            st["ff"][a] = getattr(sc.render.ffmpeg, a)
        except Exception:
            pass
    for a in _PRESENT_SHADING_ATTRS:
        try:
            st["sh"][a] = getattr(sc.display.shading, a)
        except Exception:
            pass
    for a in ("frame_start", "frame_end", "frame_step", "frame_current"):
        try:
            st["scene"][a] = getattr(sc, a)
        except Exception:
            pass
    try:
        st["misc"]["aa"] = sc.display.render_aa
    except Exception:
        pass
    try:
        st["misc"]["view_transform"] = sc.view_settings.view_transform
    except Exception:
        pass
    try:
        st["misc"]["camera"] = sc.camera
    except Exception:
        pass
    return st


def _present_state_restore(sc, st):
    def _set(obj, attr, val):
        try:
            setattr(obj, attr, val)
        except Exception:
            pass
    for a, v in (st.get("render") or {}).items():
        _set(sc.render, a, v)
    for a, v in (st.get("img") or {}).items():
        _set(sc.render.image_settings, a, v)
    for a, v in (st.get("ff") or {}).items():
        _set(sc.render.ffmpeg, a, v)
    try:
        sh = sc.display.shading
        for a, v in (st.get("sh") or {}).items():
            _set(sh, a, v)
    except Exception:
        pass
    for a, v in (st.get("scene") or {}).items():
        _set(sc, a, v)
    misc = st.get("misc") or {}
    if "aa" in misc:
        _set(sc.display, "render_aa", misc["aa"])
    if "view_transform" in misc:
        _set(sc.view_settings, "view_transform", misc["view_transform"])
    if "camera" in misc:
        _set(sc, "camera", misc["camera"])


def _bg_rgb(background, what="background"):
    b = str(background if background is not None else "white").strip().lower()
    if b in _BG_COLORS:
        return _BG_COLORS[b], b, False
    if b in ("transparent", "alpha"):
        return 0.0, "transparent", True
    raise ValueError("%s 只支持 white/gray/black/transparent（收到 %r）" % (what, background))


def _hide_other_objects(sc, objs):
    objset = set(objs)
    hidden = []
    for ob in list(sc.objects):
        if ob in objset:
            continue
        if ob.type in _RENDERABLE_TYPES:
            hidden.append((ob, bool(ob.hide_render)))
            ob.hide_render = True
    return hidden


def _unhide_objects(hidden):
    for ob, hv in hidden or []:
        try:
            if bpy.data.objects.get(ob.name) is not None:
                ob.hide_render = hv
        except Exception:
            pass


def _present_setup(sc, res, background, neutral=0.62):
    """Workbench + 白底/透明 + 中性 SINGLE 色（不动物体材质）+ Standard 色彩变换。"""
    bg_val, bg_name, transparent = _bg_rgb(background)
    _configure_workbench(sc, res, film_transparent=transparent)
    try:
        sc.view_settings.view_transform = "Standard"
    except Exception:
        pass
    try:
        sh = sc.display.shading
        sh.color_type = "SINGLE"
        sh.single_color = (float(neutral), float(neutral), float(neutral))
        sh.background_type = "VIEWPORT"
        sh.background_color = (float(bg_val), float(bg_val), float(bg_val))
        sh.light = "STUDIO"
        try:
            sh.show_cavity = True
            sh.cavity_type = "BOTH"
        except Exception:
            pass
        try:
            sh.show_object_outline = True
            sh.object_outline_color = (0.05, 0.05, 0.05)
        except Exception:
            pass
    except Exception:
        pass
    try:
        sc.render.image_settings.media_type = "IMAGE"
    except Exception:
        pass
    try:
        ims = sc.render.image_settings
        ims.file_format = "PNG"
        ims.color_mode = "RGBA"
        ims.color_depth = "8"
    except Exception:
        pass
    return {"background": bg_name, "transparent": transparent,
            "bg_rgb": [float(bg_val)] * 3, "neutral_color": float(neutral)}


def _look_at(cam, loc, target):
    cam.location = tuple(loc)
    cam.rotation_mode = "XYZ"
    cam.rotation_euler = (Vector(target) - Vector(loc)).to_track_quat("-Z", "Y").to_euler()
    try:
        bpy.context.view_layer.update()
    except Exception:
        pass


def _orbit_loc(center, dist, az_deg, el_deg):
    a = math.radians(float(az_deg))
    e = math.radians(float(el_deg))
    d = Vector((math.cos(e) * math.cos(a), math.cos(e) * math.sin(a), math.sin(e)))
    return Vector(center) + d * float(dist)


def _find_video_file(root, stem):
    base = os.path.basename(stem)
    cands = []
    try:
        for fn in os.listdir(root):
            if not fn.startswith(base):
                continue
            if fn.lower().endswith((".mp4", ".mov", ".mkv", ".avi", ".webm")):
                cands.append(os.path.join(root, fn))
    except Exception:
        return None
    cands.sort(key=lambda p: os.path.getmtime(p), reverse=True)
    return cands[0] if cands else None


def _clean_prefix_files(root, stem, exts):
    for fn in list(os.listdir(root)):
        low = fn.lower()
        if fn.startswith(os.path.basename(stem)) and any(low.endswith(e) for e in exts):
            try:
                os.remove(os.path.join(root, fn))
            except Exception:
                pass


def _render_turntable_video(sc, cam, center, cam_dist, elev, az_start, orbit, frames, root, stem, fps_v, res):
    """Blender 内置 FFMPEG：media_type=VIDEO + file_format=FFMPEG（Blender 5.x 的口径）。"""
    _clean_prefix_files(root, stem + "_turntable", (".mp4", ".mov", ".mkv", ".avi", ".webm"))
    prefix = _norm_path(os.path.join(root, stem + "_turntable"))
    ims = sc.render.image_settings
    ims.media_type = "VIDEO"
    ims.file_format = "FFMPEG"
    ff = sc.render.ffmpeg
    ff.format = "MPEG4"
    ff.codec = "H264"
    for attr, val in (("constant_rate_factor", "HIGH"), ("audio_codec", "NONE"), ("ffmpeg_preset", "GOOD")):
        try:
            setattr(ff, attr, val)
        except Exception:
            pass
    sc.render.fps = int(round(float(fps_v)))
    sc.frame_start = 1
    sc.frame_end = int(frames)
    sc.frame_step = 1
    sc.render.filepath = prefix
    for i in range(int(frames)):
        az = float(az_start) + float(orbit) * i / float(frames)
        _look_at(cam, _orbit_loc(center, cam_dist, az, elev), center)
        f = i + 1
        cam.keyframe_insert(data_path="location", frame=f)
        cam.keyframe_insert(data_path="rotation_euler", frame=f)
    try:
        ad = cam.animation_data
        act = ad.action if ad is not None else None
        if act is not None:
            for fc in act.fcurves:
                for kp in fc.keyframe_points:
                    kp.interpolation = "LINEAR"
    except Exception:
        pass
    t0 = time.perf_counter()
    bpy.ops.render.render(animation=True)
    render_ms = int(round((time.perf_counter() - t0) * 1000.0))
    path = _find_video_file(root, stem + "_turntable")
    if path is None:
        raise RuntimeError("FFMPEG 渲染后没找到视频文件（目录：%s）" % sorted(os.listdir(root))[:8])
    rec = _file_receipt(path)
    with open(path, "rb") as f:
        head = f.read(12)
    rec.update({"ok": True, "magic_ok": head[4:8] == b"ftyp", "container": "MPEG4", "codec": "H264",
                "frames": int(frames), "fps": int(round(float(fps_v))),
                "duration_s": round(float(frames) / float(fps_v), 3), "res": list(res),
                "render_ms": render_ms, "prefix": prefix})
    if not rec["magic_ok"]:
        raise RuntimeError("视频魔数不对（第 4-8 字节不是 ftyp）：%s" % path)
    return rec


def _render_turntable_sequence(sc, cam, center, cam_dist, elev, az_start, orbit, frames, outdir, res):
    outdir = _norm_path(outdir)
    os.makedirs(outdir, exist_ok=True)
    try:
        for fn in list(os.listdir(outdir)):
            if fn.startswith("frame_") and fn.lower().endswith(".png"):
                os.remove(os.path.join(outdir, fn))
    except Exception:
        pass
    try:
        if cam.animation_data is not None:
            cam.animation_data_clear()
    except Exception:
        pass
    try:
        sc.render.image_settings.media_type = "IMAGE"
    except Exception:
        pass
    ims = sc.render.image_settings
    ims.file_format = "PNG"
    ims.color_mode = "RGBA"
    ims.color_depth = "8"
    t0 = time.perf_counter()
    files = []
    for i in range(int(frames)):
        az = float(az_start) + float(orbit) * i / float(frames)
        _look_at(cam, _orbit_loc(center, cam_dist, az, elev), center)
        try:
            sc.frame_set(i + 1)
        except Exception:
            pass
        path = _norm_path(os.path.join(outdir, "frame_%04d.png" % (i + 1)))
        sc.render.filepath = path
        bpy.ops.render.render(write_still=True)
        files.append(_file_receipt(path))
    ms = int(round((time.perf_counter() - t0) * 1000.0))
    manifest = "|".join(f["md5"] for f in files)
    return {"ok": True, "dir": outdir, "pattern": "frame_%04d.png", "count": len(files),
            "files": [f["path"] for f in files], "res": list(res),
            "bytes_total": int(sum(f["bytes"] for f in files)),
            "md5_manifest": hashlib.md5(manifest.encode("utf-8")).hexdigest(),
            "first": files[0] if files else None, "last": files[-1] if files else None,
            "ms": ms}


def blockout_turntable(objects=None, collection=None, name=None, out_dir=None, frames=96, fps=24,
                       res=None, elev_deg=15, distance=1.6, background="white", fmt="mp4", orbit_deg=360):
    """绕 Z 轴转台：内置 FFMPEG 出 MP4（失败回退 PNG 序列，ok:false/degraded + warnings）。"""
    t_start = time.perf_counter()
    try:
        objs = _find_model_objects(objects=objects, name=name, collection=collection)
        nm = _safe_token(name or collection or "turntable", "name")
        nframes = int(_num(frames, "frames", default=96, gt=0.0, maxv=1200.0))
        fps_v = _num(fps, "fps", default=24.0, gt=0.0, maxv=120.0)
        res_v = _parse_res([512, 512] if res is None else res)
        elev = _num(elev_deg, "elev_deg", default=15.0, minv=-89.0, maxv=89.0)
        dmult = _num(distance, "distance", default=1.6, gt=0.0, maxv=20.0)
        orbit = _num(orbit_deg, "orbit_deg", default=360.0, gt=0.0, maxv=3600.0)
        fmt_s = str(fmt if fmt is not None else "mp4").strip().lower()
        if fmt_s not in ("mp4", "png", "sequence"):
            raise ValueError("fmt 只支持 mp4 / png(sequence)（收到 %r）" % (fmt,))
        bg_val, bg_name, transparent = _bg_rgb(background)
        root = _root_outdir(out_dir)
        mn, mx = _bbox_bu(objs)
        center = (mn + mx) * 0.5
        radius = max(float((mx - mn).length) * 0.5, 1e-5)
        tan_half = _tan_half(res_v, 50.0)
        fit_dist = radius / max(math.sin(math.atan(tan_half)), 1e-6) * 1.12
        cam_dist = fit_dist * dmult
        cam_meta = {"az_start_deg": -90.0, "orbit_deg": round(orbit, 3), "elev_deg": round(elev, 3),
                    "distance_mult": round(dmult, 4), "fit_distance": round(fit_dist, 6),
                    "camera_distance": round(cam_dist, 6), "radius": round(radius, 6),
                    "center_mm": [round(float(v) * _mm_per_bu(), 3) for v in center],
                    "lens_mm": 50.0, "sensor_width_mm": 36.0}
    except ValueError as e:
        return _err(e, hint="turntable 用 objects=/name=/collection= 指定已建白模；参数见 help().ops.turntable",
                    op="turntable")
    except Exception as e:
        return _err("%s: %s" % (type(e).__name__, str(e)), op="turntable")
    sc = bpy.context.scene
    st = _present_state_save(sc)
    hidden = []
    cam = None
    cam_data = None
    warnings = []
    try:
        hidden = _hide_other_objects(sc, objs)
        cam_data = bpy.data.cameras.new("BO_TT_CAM")
        cam = bpy.data.objects.new("BO_TT_CAM", cam_data)
        sc.collection.objects.link(cam)
        cam_data.lens = 50.0
        cam_data.sensor_width = 36.0
        cam_data.sensor_fit = "AUTO"
        cam_data.clip_start = 1e-4
        cam_data.clip_end = 1e4
        sc.camera = cam
        video = None
        sequence = None
        degraded = False
        error = None
        if fmt_s == "mp4":
            if transparent:
                warnings.append("background=transparent 不支持视频（MP4 无 alpha）→ 已按 white 渲染；"
                                "要带 alpha 的帧请用 fmt=png")
            pres = _present_setup(sc, res_v, "white" if transparent else background)
            try:
                video = _render_turntable_video(sc, cam, center, cam_dist, elev, -90.0, orbit,
                                                nframes, root, nm, fps_v, res_v)
            except Exception as e:
                degraded = True
                error = "MP4 输出失败：%s: %s" % (type(e).__name__, str(e)[:220])
                warnings.append(error + " → 回退 PNG 序列")
                try:
                    _clean_prefix_files(root, nm + "_turntable",
                                        (".mp4", ".mov", ".mkv", ".avi", ".webm"))
                except Exception:
                    pass
                try:
                    sequence = _render_turntable_sequence(
                        sc, cam, center, cam_dist, elev, -90.0, orbit, nframes,
                        os.path.join(root, nm + "_turntable_frames"), res_v)
                except Exception as e2:
                    return _err("MP4 与 PNG 序列都失败：%s / %s" % (str(e)[:140], str(e2)[:140]),
                                op="turntable")
        else:
            pres = _present_setup(sc, res_v, background)
            sequence = _render_turntable_sequence(
                sc, cam, center, cam_dist, elev, -90.0, orbit, nframes,
                os.path.join(root, nm + "_turntable_frames"), res_v)
        fps_out = int(round(fps_v)) if abs(fps_v - round(fps_v)) < 1e-6 else round(fps_v, 3)
        out = {"op": "turntable", "name": nm, "engine": "BLENDER_WORKBENCH", "fmt": fmt_s,
               "frames": nframes, "fps": fps_out,
               "duration_s": round(float(nframes) / float(fps_v), 3), "res": res_v,
               "background": pres["background"], "camera": cam_meta,
               "objects": [ob.name for ob in objs], "object_count": len(objs),
               "video": video, "sequence": sequence, "warnings": warnings,
               "degraded": bool(degraded),
               "ms": {"render": ((video or {}).get("render_ms") or (video or {}).get("ms")
                                 or (sequence or {}).get("ms"))}}
        if video is not None:
            out.update({"ok": True, "path": video["path"], "bytes": video["bytes"], "md5": video["md5"]})
        elif sequence is not None:
            out.update({"ok": not degraded, "path": sequence["dir"],
                        "bytes": sequence["bytes_total"], "md5": sequence["md5_manifest"]})
        else:
            out.update({"ok": False, "error": "没有产物"})
        if degraded:
            out["ok"] = False
            out["error"] = error
        out["ms"]["total"] = int(round((time.perf_counter() - t_start) * 1000.0))
        return out
    except Exception as e:
        return _err("%s: %s" % (type(e).__name__, str(e)[:280]),
                    hint="转台需要已建白模（BO-* 对象）；检查场景/对象名", op="turntable")
    finally:
        act = None
        try:
            ad = cam.animation_data if cam is not None else None
            act = ad.action if ad is not None else None
        except Exception:
            act = None
        try:
            if cam is not None:
                bpy.data.objects.remove(cam, do_unlink=True)
        except Exception:
            pass
        try:
            if act is not None and getattr(act, "users", 0) == 0:
                bpy.data.actions.remove(act)
        except Exception:
            pass
        if cam_data is not None and getattr(cam_data, "users", 0) == 0:
            try:
                bpy.data.cameras.remove(cam_data)
            except Exception:
                pass
        _unhide_objects(hidden)
        _present_state_restore(sc, st)


def _parse_refsheet_views(raw):
    if raw is None:
        raw = ["front", "side", "top", "iso"]
    if isinstance(raw, str):
        raw = [x.strip() for x in raw.replace(";", ",").split(",") if x.strip()]
    if not isinstance(raw, (list, tuple)) or not raw:
        raise ValueError("views 必须是非空 list（默认 front/side/top/iso）")
    if len(raw) > 8:
        raise ValueError("views 最多 8 个（收到 %d）" % len(raw))
    out, seen = [], set()
    for i, v in enumerate(raw):
        if not isinstance(v, str) or not v.strip():
            raise ValueError("views[%d] 必须是视角名字符串（收到 %r）" % (i, v))
        key = _REF_VIEW_ALIASES.get(v.strip().lower())
        if key is None:
            raise ValueError("views[%d] 不支持 %r（可用：front/side/right/left/back/top/bottom/iso）"
                             % (i, v))
        if key in seen:
            raise ValueError("views 里的视角重复：%s" % key)
        seen.add(key)
        out.append(key)
    return out


def _setup_ref_camera(sc, cam, cam_data, mn, mx, view, res, ortho, lens=50.0, margin=1.12):
    """固定机位：front/side/top 正交（ortho=true 时），iso 透视；正交感光按 bbox 投影占满。"""
    center = (mn + mx) * 0.5
    half = (mx - mn) * 0.5
    radius = max(float(half.length), 1e-5)
    k = _mm_per_bu()
    cam_data.lens = float(lens)
    cam_data.sensor_width = 36.0
    cam_data.sensor_fit = "AUTO"
    cam_data.clip_start = 1e-4
    cam_data.clip_end = 1e4
    ortho_views = ("front", "back", "left", "side", "top", "bottom")
    if bool(ortho) and view in ortho_views:
        cam_data.type = "ORTHO"
        dirs = {"front": Vector((0.0, -1.0, 0.0)), "back": Vector((0.0, 1.0, 0.0)),
                "side": Vector((1.0, 0.0, 0.0)), "left": Vector((-1.0, 0.0, 0.0)),
                "top": Vector((0.0, 0.0, 1.0)), "bottom": Vector((0.0, 0.0, -1.0))}
        _look_at(cam, center + dirs[view] * max(radius * 4.0, 1.0), center)
        if view in ("front", "back"):
            ex, ey = float(half.x), float(half.z)
        elif view in ("side", "left"):
            ex, ey = float(half.y), float(half.z)
        else:
            ex, ey = float(half.x), float(half.y)
        cam_data.ortho_scale = max(max(ex, ey) * 2.0 * float(margin), 1e-4)
        meta = {"projection": "ortho", "az_deg": None, "el_deg": None,
                "ortho_scale": round(float(cam_data.ortho_scale), 6),
                "ortho_scale_mm": round(float(cam_data.ortho_scale) * k, 3), "lens_mm": None}
    else:
        cam_data.type = "PERSP"
        az, el = _REF_PERSP_ANGLES[view]
        tan_half = _tan_half(res, lens)
        _fit_camera(cam, sc, mn, mx, az, el, tan_half, margin)
        meta = {"projection": "persp", "az_deg": az, "el_deg": el, "lens_mm": float(lens),
                "ortho_scale": None, "ortho_scale_mm": None}
    meta.update({"loc_mm": [round(float(v) * k, 3) for v in cam.location],
                 "rot_euler_deg": [round(math.degrees(float(v)), 3) for v in cam.rotation_euler],
                 "res": list(res)})
    return meta


def _json_receipt(path, payload):
    p = _norm_path(path)
    d = os.path.dirname(p)
    if d:
        os.makedirs(d, exist_ok=True)
    with open(p, "w", encoding="utf-8") as f:
        f.write(json.dumps(payload, ensure_ascii=False, indent=2, default=str))
    rec = _file_receipt(p)
    rec["ok"] = True
    return rec


def blockout_refsheet(objects=None, collection=None, out_dir=None, name=None, views=None, res=None,
                      ortho=True, background="white", turntable=None):
    """同机位参考图包：4 视图 PNG（正交前/侧/顶 + 透视 iso）+ 拼图 + refsheet.json（+ 可选转台 MP4）。"""
    t_start = time.perf_counter()
    try:
        objs = _find_model_objects(objects=objects, name=name, collection=collection)
        nm = _safe_token(name or collection or "refsheet", "name")
        view_list = _parse_refsheet_views(views)
        res_v = _parse_res([768, 768] if res is None else res)
        ortho_b = _bool(ortho, "ortho", True)
        bg_val, bg_name, transparent = _bg_rgb(background)
        root = _root_outdir(out_dir)
        tt = None
        if turntable not in (None, False, 0):
            if turntable is True:
                tt = {}
            elif isinstance(turntable, dict):
                tt = dict(turntable)
            else:
                raise ValueError("turntable 必须是 false / true / dict（收到 %r）" % (turntable,))
            unknown = sorted(set(str(k) for k in tt.keys()) -
                             set(["frames", "fps", "elev_deg", "distance", "fmt", "res",
                                  "background", "orbit_deg"]))
            if unknown:
                raise ValueError("refsheet.turntable 不认识的键 %s（可用：frames,fps,elev_deg,distance,"
                                 "fmt,res,background,orbit_deg）" % unknown)
        mn, mx = _bbox_bu(objs)
        mmu = _mm_per_bu()
        bbox_mm = _bbox_mm(mn, mx)
        center_mm = [round(float(v) * mmu, 3) for v in ((mn + mx) * 0.5)]
    except ValueError as e:
        return _err(e, hint="refsheet 用 objects=/name=/collection= 指定已建白模；参数见 help().ops.refsheet",
                    op="refsheet")
    except Exception as e:
        return _err("%s: %s" % (type(e).__name__, str(e)), op="refsheet")
    sc = bpy.context.scene
    st = _present_state_save(sc)
    hidden = []
    cam = None
    cam_data = None
    warnings = []
    try:
        hidden = _hide_other_objects(sc, objs)
        pres = _present_setup(sc, res_v, background)
        cam_data = bpy.data.cameras.new("BO_REF_CAM")
        cam = bpy.data.objects.new("BO_REF_CAM", cam_data)
        sc.collection.objects.link(cam)
        sc.camera = cam
        view_rows = []
        view_paths = []
        for view in view_list:
            meta = _setup_ref_camera(sc, cam, cam_data, mn, mx, view, res_v, ortho_b)
            path = _norm_path(os.path.join(root, "%s_refsheet_%s.png" % (nm, view)))
            sc.render.filepath = path
            try:
                sc.render.image_settings.media_type = "IMAGE"
                sc.render.image_settings.file_format = "PNG"
                sc.render.image_settings.color_mode = "RGBA"
            except Exception:
                pass
            t1 = time.perf_counter()
            bpy.ops.render.render(write_still=True)
            ms = int(round((time.perf_counter() - t1) * 1000.0))
            rec = _file_receipt(path)
            rec.update({"view": view, "ms": ms, "res": list(res_v), "camera": meta,
                        "coverage_estimate": _alpha_stats(path).get("coverage") if transparent else None})
            view_rows.append(rec)
            view_paths.append(path)
        cols = 2 if len(view_rows) >= 4 else len(view_rows)
        sheet = _make_sheet(view_paths, [v["view"] for v in view_rows],
                            os.path.join(root, "%s_refsheet_sheet.png" % nm), cols=cols)
        render_fps = None
        try:
            render_fps = int(round(float(sc.render.fps)))
        except Exception:
            pass
        tt_rec = None
        tt_ok = True
        if tt is not None:
            tt_args = {"objects": [ob.name for ob in objs], "name": nm + "_refsheet",
                       "out_dir": root, "frames": 120, "fps": 24, "elev_deg": 15, "fmt": "mp4",
                       "background": "white" if transparent else background}
            tt_args.update(tt)
            try:
                tt_rec = blockout_turntable(**tt_args)
                tt_ok = tt_rec.get("ok") is True
                if not tt_ok:
                    warnings.append("refsheet 内嵌 turntable 未成功：%s"
                                    % str(tt_rec.get("error") or "?")[:200])
                    for w in (tt_rec.get("warnings") or []):
                        warnings.append("turntable: %s" % str(w)[:160])
            except Exception as e:
                tt_ok = False
                tt_rec = {"ok": False, "error": "%s: %s" % (type(e).__name__, str(e)[:220])}
                warnings.append("refsheet 内嵌 turntable 异常：%s" % str(e)[:180])
        payload = {
            "schema": "dsh.blockout.refsheet/1", "version": BLOCKOUT_VERSION, "name": nm,
            "created_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
            "engine": "BLENDER_WORKBENCH", "view_transform": "Standard",
            "units": {"mm_per_unit": round(mmu, 6), "unit": "mm"},
            "bbox_mm": bbox_mm, "center_mm": center_mm,
            "forward_axis": "-Y", "up_axis": "Z", "screen_right_axis": "+X",
            "note": "front=相机在 -Y 看 +Y（看到的正面朝 -Y）；side=+X 看 -X；top=+Z 看 -Z；iso=透视",
            "res": list(res_v), "ortho": bool(ortho_b), "background": bg_name,
            "neutral_color": pres.get("neutral_color"), "render_fps": render_fps,
            "objects": [ob.name for ob in objs], "object_count": len(objs),
            "views": view_rows, "sheet": sheet,
            "md5": {"views": {v["view"]: v["md5"] for v in view_rows}, "sheet": sheet.get("md5")},
            "fps": render_fps, "frame_count": len(view_rows),
            "turntable": ({"ok": tt_rec.get("ok"), "path": tt_rec.get("path"),
                           "bytes": tt_rec.get("bytes"), "md5": tt_rec.get("md5"),
                           "frames": tt_rec.get("frames"), "fps": tt_rec.get("fps"),
                           "duration_s": tt_rec.get("duration_s"), "res": tt_rec.get("res"),
                           "degraded": tt_rec.get("degraded"),
                           "warnings": (tt_rec.get("warnings") or [])[:6]}
                          if tt_rec is not None else None),
        }
        json_path = os.path.join(root, "%s_refsheet.json" % nm)
        json_rec = _json_receipt(json_path, payload)
        files = {"views": view_rows, "sheet": sheet, "json": json_rec,
                 "all": [v["path"] for v in view_rows] + [sheet["path"], json_rec["path"]]}
        if tt_rec is not None:
            files["turntable"] = tt_rec
            files["all"].extend([tt_rec.get("path")] if tt_rec.get("path") else [])
        ok = bool(view_rows and len(view_rows) == len(view_list) and sheet.get("md5")
                  and json_rec.get("md5") and tt_ok)
        out = {"op": "refsheet", "name": nm, "engine": "BLENDER_WORKBENCH",
               "units": {"mm_per_unit": round(mmu, 6), "unit": "mm"},
               "bbox_mm": bbox_mm, "forward_axis": "-Y", "up_axis": "Z",
               "views": view_rows, "sheet": sheet, "files": files,
               "refsheet_json": json_rec["path"], "md5": json_rec["md5"],
               "turntable": tt_rec, "warnings": warnings,
               "ms": {"views": int(sum(v.get("ms") or 0 for v in view_rows)),
                      "turntable": ((tt_rec or {}).get("ms") or {}).get("total")
                      if isinstance((tt_rec or {}).get("ms"), dict) else (tt_rec or {}).get("ms"),
                      "total": int(round((time.perf_counter() - t_start) * 1000.0))}}
        out["ok"] = ok
        if not ok:
            if tt_rec is not None and not tt_ok:
                out["error"] = "refsheet 视图/JSON 已产出，但内嵌 turntable 失败（见 warnings）"
            else:
                out["error"] = "refsheet 产物不完整（views/sheet/json 缺失）"
        return out
    except Exception as e:
        return _err("%s: %s" % (type(e).__name__, str(e)[:280]),
                    hint="refsheet 需要已建白模（BO-* 对象）；检查场景/对象名/输出目录权限", op="refsheet")
    finally:
        try:
            if cam is not None:
                bpy.data.objects.remove(cam, do_unlink=True)
        except Exception:
            pass
        if cam_data is not None and getattr(cam_data, "users", 0) == 0:
            try:
                bpy.data.cameras.remove(cam_data)
            except Exception:
                pass
        _unhide_objects(hidden)
        _present_state_restore(sc, st)


# ────────────────────────────── op: selftest / help ──────────────────────────────
def blockout_selftest():
    t0 = time.perf_counter()
    ev = {}
    names = []
    colname = None
    try:
        root = _root_outdir(None)
        d = _norm_path(os.path.join(root, "blockout_selftest"))
        try:
            os.makedirs(d, exist_ok=True)
        except Exception:
            d = tempfile.mkdtemp(prefix="dsh_blockout_")
        r = blockout_base(
            name="unittest", parts=[
                {"id": "a", "prim": "box", "size_mm": [20, 20, 20], "at_mm": [0, 0, 10]},
                {"id": "b", "prim": "cylinder", "radius_mm": 6, "depth_mm": 20, "at_mm": [30, 0, 10], "axis": "x"},
                {"id": "c", "prim": "uv_sphere", "radius_mm": 5, "at_mm": [-30, 0, 10]},
                {"id": "d", "prim": "box", "size_mm": [8, 8, 8], "at_mm": [0, 30, 10], "mirror": True},
            ],
            render={"res": [96, 96], "views": ["front"], "sheet": False, "via": "internal"},
            export={"glb": True}, audit=True, outdir=d)
        names = list(r.get("objects") or [])
        colname = r.get("collection")
        ev["base_ok"] = r.get("ok") is True
        ev["object_count"] = r.get("object_count")
        ev["total_tris"] = r.get("total_tris")
        ev["total_tris_eval"] = r.get("total_tris_eval")
        ev["bbox_mm"] = r.get("bbox_mm")
        ev["audit_ok"] = (r.get("audit") or {}).get("ok")
        ev["views"] = [{"name": v.get("name"), "bytes": v.get("bytes"),
                        "md5": bool(v.get("md5")), "coverage": v.get("coverage_estimate")}
                       for v in ((r.get("render") or {}).get("views") or [])]
        ev["glb"] = {k: (r.get("export") or {}).get("glb", {}).get(k)
                     for k in ("path", "bytes", "magic_ok", "nodes", "md5")}
        bbox = r.get("bbox_mm") or {}
        # P2-C：turntable / refsheet 冒烟（小帧数/小分辨率，只看产物与魔数）
        tt_ok = False
        try:
            tt = blockout_turntable(objects=names, name="unittest", out_dir=d, frames=4, fps=12,
                                    res=[64, 64], fmt="mp4")
            vid = tt.get("video") or {}
            seq = tt.get("sequence") or {}
            ev["turntable"] = {"ok": tt.get("ok"), "degraded": tt.get("degraded"),
                               "video": bool(vid.get("path")), "magic_ok": vid.get("magic_ok"),
                               "bytes": vid.get("bytes"), "sequence": seq.get("count"),
                               "frames": tt.get("frames"), "fps": tt.get("fps"),
                               "ms": (tt.get("ms") or {}).get("total"),
                               "error": tt.get("error"), "warnings": (tt.get("warnings") or [])[:2]}
            tt_ok = (tt.get("ok") is True and vid.get("magic_ok") is True
                     and int(vid.get("bytes") or 0) > 0) or \
                    (tt.get("degraded") is True and int(seq.get("count") or 0) == 4)
        except Exception as e:
            ev["turntable"] = {"error": "%s: %s" % (type(e).__name__, str(e)[:180])}
        rs_ok = False
        try:
            rs = blockout_refsheet(objects=names, name="unittest", out_dir=d,
                                   views=["front", "side", "top", "iso"], res=[128, 128],
                                   ortho=True, background="white", turntable=None)
            files = rs.get("files") or {}
            ev["refsheet"] = {"ok": rs.get("ok"),
                              "views": len(files.get("views") or []),
                              "sheet": bool((files.get("sheet") or {}).get("bytes")),
                              "json": rs.get("refsheet_json"), "md5": rs.get("md5"),
                              "bbox_mm": rs.get("bbox_mm"),
                              "ms": (rs.get("ms") or {}).get("total"),
                              "error": rs.get("error"), "warnings": (rs.get("warnings") or [])[:2]}
            rs_ok = (rs.get("ok") is True and len(files.get("views") or []) == 4
                     and bool(rs.get("refsheet_json")) and bool(rs.get("md5"))
                     and os.path.isfile(rs.get("refsheet_json") or ""))
        except Exception as e:
            ev["refsheet"] = {"error": "%s: %s" % (type(e).__name__, str(e)[:180])}
        ev["checks"] = {
            "object_count_5": ev["object_count"] == 5,
            "tris_box_12_each": bool(ev["total_tris"] and ev["total_tris"] >= 4 * 12),
            "bbox_z_bottom_0": bool(bbox.get("min") and abs(bbox["min"][2]) < 0.02),
            "bbox_size_x_75": bool(bbox.get("size") and abs(bbox["size"][0] - 75.0) < 0.50),
            "render_view_ok": bool(ev["views"] and ev["views"][0].get("bytes", 0) > 0),
            "glb_ok": bool(ev["glb"].get("bytes") and ev["glb"].get("magic_ok")),
            "audit_ok": ev["audit_ok"] is True,
            "turntable_ok": bool(tt_ok),
            "refsheet_ok": bool(rs_ok),
        }
        ok = all(bool(v) for v in ev["checks"].values())
        return {"ok": bool(ok), "evidence": ev, "ms": int(round((time.perf_counter() - t0) * 1000.0))}
    except Exception as e:
        return {"ok": False, "evidence": ev, "error": "%s: %s" % (type(e).__name__, str(e)[:260])}
    finally:
        try:
            _remove_objects(names)
            if colname:
                _remove_collection_if_empty(colname)
        except Exception:
            pass


def blockout_help():
    return {
        "module": "blockout.py", "version": BLOCKOUT_VERSION,
        "what": "Blockout 白模：bmesh 直建 primitive（mm 输入、底部贴地、统一命名/集合/白材质/Bevel），"
                "可一次渲染 3 视图 + 拼图 + 导出 GLB/OBJ；batch 一次进程建 N 件",
        "ops": {
            "base": {"doc": "建一件白模（几何 + 可选 render/export/audit）",
                     "args": ["name", "template(none|humanoid_prim|vehicle)", "units(mm|cm|m)",
                              "anchor(bottom|center|origin)", "parts", "landmarks", "collection",
                              "render", "export", "audit", "reset", "bevel_mm", "template_scale", "outdir"]},
            "batch": {"doc": "一次进程建 N 件（逐件 collection；cleanup=true 时逐件清理）",
                      "args": ["models", "outdir", "export", "collection", "cleanup", "render"]},
            "export": {"doc": "对已建场景补视图/补 GLB/OBJ（不重建）",
                       "args": ["objects", "name", "collection", "glb", "obj", "export", "render", "outdir"]},
            "turntable": {"doc": "绕 Z 轴转台视频：Blender 内置 FFMPEG 出 MP4（distance=自动取景距离倍数；"
                                 "背景透明会按 white 渲并 warning；失败回退 PNG 序列 + ok:false/degraded）",
                          "args": ["objects", "collection", "name", "out_dir", "frames", "fps", "res",
                                   "elev_deg", "distance", "background(white|gray|black|transparent)",
                                   "fmt(mp4|png)", "orbit_deg"]},
            "refsheet": {"doc": "同机位参考图包：正交前/侧/顶 + 透视 iso PNG + 拼图 + refsheet.json"
                                "（views 默认 front/side/top/iso；turntable={frames,fps,elev_deg,...} 可选）",
                         "args": ["objects", "collection", "out_dir", "name", "views", "res", "ortho",
                                  "background", "turntable"]},
            "selftest": {"doc": "模块自检（建 4 件 + 1 视图 + GLB + audit + 转台/参考图冒烟，跑完清理）"},
            "help": {"doc": "本说明"},
        },
        "part_schema": {
            "box": {"id": "p1", "prim": "box", "size_mm": [100, 60, 40], "at_mm": [0, 0, 20],
                    "rot_deg": [0, 0, 0], "mirror": False},
            "cylinder": {"id": "w1", "prim": "cylinder", "radius_mm": 30, "depth_mm": 80,
                         "axis": "x|y|z", "at_mm": [0, 0, 30], "mirror": True},
            "uv_sphere": {"id": "h1", "prim": "uv_sphere", "radius_mm": 25, "at_mm": [0, 0, 50]},
        },
        "render": {"engine": "workbench（P0 只此一种）", "res": "[512,512]",
                   "views": "auto3 | front,right,top | [{name,az,el}] | az=35,el=20",
                   "sheet": "true=单行拼图 / 数字=列数；qc_render 路径也自己拼",
                   "via": "auto（优先 qc_render）/ internal / qc"},
        "protocol": [
            "先 base 拿 bbox_mm 与三段 ms（geom/render/export/total），再决定加细节；",
            "装配前用 collection 隔离；要保留多件就 batch(cleanup=false) 且 name 唯一；",
            "GLB 导出走 export_apply（Bevel 只作用于导出副本，场景里修改器不 apply）；",
            "对已建场景补图/补导出用 export op（不要重跑 base）；",
            "要交接/给参考图：refsheet（固定机位白底四视图 + refsheet.json + 可选转台）一次出齐；",
            "要展示片/转台：turntable（内置 FFMPEG；先渲染后编码，成片路径/bytes/md5 都在回执里）。",
        ],
        "limits": [
            "渲染只支持 Workbench（EEVEE/Cycles 未接）；render op 的视角是透视；refsheet 的 front/side/top 是正交；",
            "turntable MP4 走 Blender 内置 FFMPEG（media_type=VIDEO；不依赖系统 ffmpeg）；本机 Blender 缺 FFmpeg 时回退 PNG 序列并 ok:false/degraded；",
            "turntable 视频无 alpha（background=transparent 会按 white 渲并 warning；要 alpha 用 fmt=png）；",
            "template 只是比例占位（humanoid_prim/vehicle 的 primitive 骨架），不是解剖/车辆细节模型；",
            "landmarks 只记录不建空物体；audit 是内建轻量体检，不是 audit.py 全套；",
            "sheet 无文字标注；refsheet 用中性灰 SINGLE 色（0.62）避免白模白底看不清；",
            "转台/参考图只临时改相机/渲染设置/背景并在 finally 还原（不改用户材质、不 apply 修改器）。",
        ],
        "presentation": {"engine": "BLENDER_WORKBENCH", "view_transform": "Standard",
                          "neutral_color": 0.62, "background": "white|gray|black|transparent",
                          "forward_axis": "-Y（front 相机在 -Y 看 +Y）", "up_axis": "Z"},
        "kernel": "共享内核 K.dsh_kit v%s" % getattr(_KIT, "version", "?"),
        "templates": list(ALLOWED_TEMPLATES), "prims": list(ALLOWED_PRIMS),
        "fake_units_note": "units 只是输入解释口径；scene.unit_settings.scale_length 原样保留",
    }


# 注册（一行）：K.dsh_blockout_api["base"] / K.dsh_blockout_api("base", {...})
_KIT.register("blockout", BLOCKOUT_VERSION,
              {"base": blockout_base, "batch": blockout_batch, "export": blockout_export,
               "turntable": blockout_turntable, "refsheet": blockout_refsheet,
               "selftest": blockout_selftest, "help": blockout_help},
              extra={"templates": list(ALLOWED_TEMPLATES), "prims": list(ALLOWED_PRIMS),
                     "view_auto3": list(VIEW_AUTO3), "ref_views": ["front", "side", "top", "iso"],
                     "turntable_fmt": ["mp4", "png"]})
