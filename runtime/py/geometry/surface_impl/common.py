# -*- coding: utf-8 -*-
"""DSH 曲面算法包（surface）v1 —— M0–M2/M4，纯 bpy + numpy（不依赖 OCCT）。

为什么要它：插件已有 geo.loft/sweep 这类建模基元，但缺一条「曲面规格 → 网格 → 质量控制」的闭环：
  M0 SurfaceSpec（kind/units/sections/degree/continuity/closed/constraints/tessellation）校验与 spec_hash；
  M1 曲面生成：loft（Bézier/B-spline 截面放样）/ sweep（沿折线扫掠，含 twist/scale/朝向）/ patch（四边面片）/
     tessellate（规格或对象 → 网格对象）；不依赖 bpy.ops，全部 bmesh + numpy；
  M2 validate：G0/G1/G2 连续性、曲率 max/mean/热点、自交（BVH tri-tri，限三角数，超限 degraded）、
     厚度（射线近似）、网格质量（长宽比/法线一致性/水密）→ ok/verdict/metrics/defects/next；
  M4 help + selftest（瓶身/翼子板/头盔简化三基准 + 自交/薄壁/坏参数负例）；
  M5 blend/offset 只留接口：纯 Python 不做，明确返回 ok:false + OCCT 需求（不假装实现）。

口径（写死，改算法先改这里）：
  * 所有长度入参/回执单位 mm；内部按 scene.unit_settings.scale_length（K.dsh_kit.units()）折算成 Blender 单位；
  * 同 spec（含 tessellation）⇒ spec_hash 相同、顶点逐字节可复现（无随机数、无 bpy.ops）；
  * 放样沿 v 方向用插值型 B-spline（degree 1..7，默认 3）：曲面穿过每个 section；u 方向用 Catmull-Rom
    （closed=环向闭合）重采样；sweep 用平行传输标架 + 线性/逐点 scale + 累积 twist；
  * patch 是张量积 B-spline（control_grid 大于 degree+1 时按 clamped 节点求值）；
  * validate 的 fail 永不静默成 pass：确认缺陷（自交/薄壁/超曲率/翻转法线/退化面）⇒ fail；
    资源不足（三角数超限/射线厚度量不到）⇒ degraded；开放曲面的「无厚度」是 n/a 不是 fail。

入口（preload 后）：
    K.dsh_surface_api("loft", {"sections": [...], "degree": 3, "closed": True, "name": "Bottle"})
    K.dsh_surface_api("sweep", {"profile_mm": [...], "rail_mm": [...], "twist_deg": 30})
    K.dsh_surface_api("patch", {"control_grid": [[...]], "degree_u": 3, "degree_v": 3})
    K.dsh_surface_api("tessellate", {"spec_or_object": {...}|"ObjName", "quality": "normal"})
    K.dsh_surface_api("validate", {"spec_or_object": {...}|"ObjName"})
真机自检：tests/e2e/blender/native/surface.py（由 tests/e2e/blender/native_runner.py 执行，preload="surface,audit"）。
"""
import hashlib
import json
import math
import os as os
import sys
import time

import bpy as bpy

import numpy as np

SURFACE_VERSION = 1
KINDS = ("loft", "sweep", "patch")
CONTINUITY = ("G0", "G1", "G2")
ORIENTATIONS = ("auto", "up_z", "up_y", "fixed")
QUALITY_TESS = {"draft": (16, 16), "normal": (48, 48), "fine": (96, 96)}
MAX_TESS = 512
MAX_VERTS = 262144
DEFAULT_COLL = "COL_surface"
PFX = "SURF_"

_K = sys.modules.get("dsh_rt_kernel")
_KIT = getattr(_K, "dsh_kit", None)
if _KIT is None:
    raise RuntimeError("surface.py 需要共享内核 K.dsh_kit（由 KERNEL_BOOTSTRAP 注入）")

_j = _KIT.j


# ────────────────────────────── 基础 helper ──────────────────────────────
def _kernel():
    return sys.modules.get("dsh_rt_kernel")


def _num(v):
    if v is None or isinstance(v, bool):
        return None
    try:
        f = float(v)
    except Exception:
        return None
    return f if math.isfinite(f) else None


def _r(x, n=6):
    try:
        return round(float(x), int(n))
    except Exception:
        return None


def _canon(obj):
    return json.dumps(obj, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)


def _spec_hash(norm):
    if not isinstance(norm, dict):
        return None
    d = {k: v for k, v in norm.items() if k != "spec_hash" and not str(k).startswith("_")}
    return hashlib.sha256(_canon(d).encode("utf-8")).hexdigest()


def _receipt(body, t0=None):
    d = dict(body or {})
    d.setdefault("units", {"mm_per_unit": _r(_KIT.units(), 6)})
    if t0 is not None:
        d["ms"] = int((time.perf_counter() - t0) * 1000)
    return d


def _err(msg, **kw):
    d = {"ok": False, "error": str(msg)[:400], "hint": kw.pop("hint", None)}
    d.update(kw)
    return _j(_receipt(d))


def _mmu():
    return float(_KIT.units())


def _mm2u(v):
    return float(v) / max(_mmu(), 1e-12)


def _u2mm(v):
    return float(v) * _mmu()


def _vec3(v):
    if not isinstance(v, (list, tuple)) or len(v) != 3:
        return None
    out = []
    for x in v:
        f = _num(x)
        if f is None:
            return None
        out.append(f)
    return out


def _vec2(v):
    if not isinstance(v, (list, tuple)) or len(v) != 2:
        return None
    out = []
    for x in v:
        f = _num(x)
        if f is None:
            return None
        out.append(f)
    return out


# ────────────────────────────── M0：SurfaceSpec 校验 ──────────────────────────────
def _norm_tessellation(raw, kind):
    base = {"loft": (48, 48), "sweep": (32, 48), "patch": (32, 32)}.get(kind, (32, 32))
    if raw is None:
        u, v = base
        quality = None
    elif isinstance(raw, str):
        q = raw.strip().lower()
        if q not in QUALITY_TESS:
            return None, [{"code": "BAD_QUALITY", "msg": "tessellation 质量只支持 %s" % list(QUALITY_TESS)}]
        u, v = QUALITY_TESS[q]
        quality = q
    elif isinstance(raw, dict):
        quality = str(raw.get("quality") or "").strip().lower() or None
        if quality is not None:
            if quality not in QUALITY_TESS:
                return None, [{"code": "BAD_QUALITY",
                               "msg": "tessellation.quality 只支持 %s" % list(QUALITY_TESS)}]
            du, dv = QUALITY_TESS[quality]
        else:
            du, dv = base
        u = _num(raw.get("u"))
        v = _num(raw.get("v"))
        u = int(round(du if u is None else u))
        v = int(round(dv if v is None else v))
    else:
        return None, [{"code": "BAD_TESSELLATION", "msg": "tessellation 必须是 {u,v} / quality / 字符串"}]
    errs = []
    if u < 4 or u > MAX_TESS:
        errs.append({"code": "BAD_TESS_U", "msg": "tessellation.u 必须在 4..%d（收到 %d）" % (MAX_TESS, u)})
    if v < 4 or v > MAX_TESS:
        errs.append({"code": "BAD_TESS_V", "msg": "tessellation.v 必须在 4..%d（收到 %d）" % (MAX_TESS, v)})
    if u * v > MAX_VERTS:
        errs.append({"code": "TESS_TOO_BIG", "msg": "u*v=%d > %d，先降 tessellation" % (u * v, MAX_VERTS)})
    if errs:
        return None, errs
    return {"u": int(u), "v": int(v),
            "quality": quality,
            "source": ("quality" if quality is not None else ("dict" if isinstance(raw, dict) else "default"))}, []


def _norm_constraints(raw):
    out = {}
    errs = []
    if raw is None:
        return out, errs
    if not isinstance(raw, dict):
        return out, [{"code": "BAD_CONSTRAINTS", "msg": "constraints 必须是对象"}]
    for k, v in raw.items():
        key = str(k)
        if key in ("min_thickness_mm", "max_curvature_1_per_mm", "g1_tol_deg", "g2_tol_ratio"):
            f = _num(v)
            if f is None or f <= 0:
                errs.append({"code": "BAD_CONSTRAINT", "msg": "constraints.%s 必须是正数" % key})
            else:
                out[key] = float(f)
        elif key == "up":
            vec = _vec3(v)
            n = float(np.linalg.norm(np.array(vec))) if vec else 0.0
            if vec is None or n < 1e-9:
                errs.append({"code": "BAD_CONSTRAINT", "msg": "constraints.up 必须是长度>0 的 3 向量"})
            else:
                out["up"] = [x / n for x in vec]
        elif key in ("closed_rail", "symmetry", "note"):
            out[key] = v
        else:
            out[key] = v
    return out, errs


def _norm_sections(raw, closed):
    errs = []
    if not isinstance(raw, (list, tuple)) or len(raw) < 2:
        return None, [{"code": "BAD_SECTIONS", "msg": "loft.sections 至少 2 个截面"}]
    out, zs = [], []
    for i, sec in enumerate(raw):
        if not isinstance(sec, dict):
            errs.append({"code": "BAD_SECTION", "msg": "sections[%d] 必须是 {z_mm, points_mm}" % i, "path": i})
            continue
        z = _num(sec.get("z_mm"))
        if z is None:
            errs.append({"code": "BAD_SECTION_Z", "msg": "sections[%d].z_mm 必须是数字" % i, "path": i})
            continue
        pts_raw = sec.get("points_mm")
        if not isinstance(pts_raw, (list, tuple)) or len(pts_raw) < (3 if closed else 2):
            errs.append({"code": "BAD_SECTION_POINTS",
                         "msg": "sections[%d].points_mm 至少 %d 个 [x,y]" % (i, 3 if closed else 2), "path": i})
            continue
        pts = []
        bad = False
        for p in pts_raw:
            v = _vec2(p)
            if v is None:
                bad = True
                break
            pts.append(v)
        if bad:
            errs.append({"code": "BAD_SECTION_POINTS", "msg": "sections[%d].points_mm 里有非法 [x,y]" % i, "path": i})
            continue
        out.append({"z_mm": float(z), "points_mm": pts})
        zs.append(float(z))
    if len(out) != len(raw):
        return None, errs
    for a, b in zip(zs, zs[1:]):
        if b <= a:
            errs.append({"code": "SECTIONS_NOT_SORTED",
                         "msg": "sections 的 z_mm 必须严格递增（%.4g → %.4g）；先排序再放样" % (a, b)})
            break
    return out, errs


def _norm_profile(raw, closed, field="profile_mm", min_n=3):
    if not isinstance(raw, (list, tuple)) or len(raw) < (min_n if closed else 2):
        return None, [{"code": "BAD_PROFILE",
                       "msg": "%s 至少 %d 个 [x,y]" % (field, min_n if closed else 2)}]
    pts = []
    for p in raw:
        v = _vec2(p)
        if v is None:
            return None, [{"code": "BAD_PROFILE", "msg": "%s 里有非法 [x,y]" % field}]
        pts.append(v)
    return pts, []


def _norm_rail(raw):
    if not isinstance(raw, (list, tuple)) or len(raw) < 2:
        return None, [{"code": "BAD_RAIL", "msg": "rail_mm 至少 2 个 [x,y,z]"}]
    pts = []
    for p in raw:
        v = _vec3(p)
        if v is None:
            return None, [{"code": "BAD_RAIL", "msg": "rail_mm 里有非法 [x,y,z]"}]
        if pts and float(np.linalg.norm(np.array(v) - np.array(pts[-1]))) < 1e-12:
            return None, [{"code": "BAD_RAIL", "msg": "rail_mm 有相邻重复点（切线无定义）；先去重"}]
        pts.append(v)
    return pts, []


def _norm_control_grid(raw):
    if not isinstance(raw, (list, tuple)) or len(raw) < 2:
        return None, [{"code": "BAD_GRID", "msg": "control_grid 至少 2 行"}]
    rows = []
    for i, row in enumerate(raw):
        if not isinstance(row, (list, tuple)) or len(row) < 2:
            return None, [{"code": "BAD_GRID", "msg": "control_grid[%d] 至少 2 列" % i}]
        pts = []
        for p in row:
            v = _vec3(p)
            if v is None:
                return None, [{"code": "BAD_GRID", "msg": "control_grid 里有非法 [x,y,z]"}]
            pts.append(v)
        rows.append(pts)
    ncol = len(rows[0])
    if any(len(r) != ncol for r in rows):
        return None, [{"code": "BAD_GRID", "msg": "control_grid 必须是矩形（每行等长）"}]
    return rows, []


def surface_spec_normalize(spec):
    """M0：把 SurfaceSpec 校验/归一化 → (norm, errors)。norm 里含 spec_hash。"""
    errors = []

    def E(code, msg, path=None):
        errors.append({"code": str(code), "msg": str(msg)[:400], "path": path})

    if not isinstance(spec, dict):
        return None, [{"code": "BAD_SPEC", "msg": "spec 必须是 JSON 对象"}]
    kind = str(spec.get("kind") or "").strip().lower()
    if kind not in KINDS:
        E("BAD_KIND", "kind 只支持 %s（收到 %r）" % (list(KINDS), spec.get("kind")))
    units = str(spec.get("units") or "mm").strip().lower()
    if units != "mm":
        E("BAD_UNITS", "units 只支持 mm（收到 %r）；其它单位请先换算" % spec.get("units"))
    degree = _num(spec.get("degree"))
    degree = 3 if degree is None else int(round(degree))
    if degree < 1 or degree > 7:
        E("BAD_DEGREE", "degree 必须在 1..7（收到 %r）" % spec.get("degree"))
    continuity = str(spec.get("continuity") or "G1").strip().upper()
    if continuity not in CONTINUITY:
        E("BAD_CONTINUITY", "continuity 只支持 %s" % list(CONTINUITY))
    closed = bool(spec.get("closed") or False)
    constraints, cerrs = _norm_constraints(spec.get("constraints"))
    for c in cerrs:
        E(c["code"], c["msg"])
    tess, terr = _norm_tessellation(spec.get("tessellation"), kind)
    for c in terr:
        E(c["code"], c["msg"])
    norm = {"kind": kind, "units": "mm", "degree": degree, "continuity": continuity,
            "closed": closed, "constraints": constraints, "tessellation": tess}
    if kind == "loft":
        sections, serrs = _norm_sections(spec.get("sections"), closed)
        for c in serrs:
            E(c["code"], c["msg"])
        norm["sections"] = sections
    elif kind == "sweep":
        profile, perrs = _norm_profile(spec.get("profile_mm"), closed, "profile_mm", 3)
        for c in perrs:
            E(c["code"], c["msg"])
        rail, rerrs = _norm_rail(spec.get("rail_mm"))
        for c in rerrs:
            E(c["code"], c["msg"])
        orientation = str(spec.get("orientation") or "auto").strip().lower()
        if orientation not in ORIENTATIONS:
            E("BAD_ORIENTATION", "orientation 只支持 %s" % list(ORIENTATIONS))
        twist = _num(spec.get("twist_deg"))
        twist = 0.0 if twist is None else twist
        scale = spec.get("scale")
        if isinstance(scale, (list, tuple)):
            sc = [_num(x) for x in scale]
            if any(x is None or x <= 0 for x in sc):
                E("BAD_SCALE", "scale 列表里必须都是正数")
            elif len(sc) not in (len(rail or []), 1):
                E("BAD_SCALE", "scale 列表长度必须 1 或 == rail 点数（%d）" % len(rail or []))
            scale = [float(x) for x in sc]
        else:
            s = _num(scale)
            s = 1.0 if s is None else s
            if s <= 0:
                E("BAD_SCALE", "scale 必须 > 0")
            scale = float(s)
        norm.update({"profile_mm": profile, "rail_mm": rail, "orientation": orientation,
                     "twist_deg": float(twist), "scale": scale})
    elif kind == "patch":
        grid, gerrs = _norm_control_grid(spec.get("control_grid"))
        for c in gerrs:
            E(c["code"], c["msg"])
        du = _num(spec.get("degree_u"))
        dv = _num(spec.get("degree_v"))
        du = int(round(du if du is not None else degree))
        dv = int(round(dv if dv is not None else degree))
        # control_grid 行=u、列=v；degree_u ≤ 行数-1，degree_v ≤ 列数-1
        for nm, val, n in (("degree_u", du, len(grid) if grid else 0),
                           ("degree_v", dv, len(grid[0]) if grid else 0)):
            if val < 1:
                E("BAD_DEGREE", "%s 必须 ≥1（收到 %r）" % (nm, val))
            elif n and val > n - 1:
                E("BAD_DEGREE", "%s=%d 超过控制点允许上限 %d" % (nm, val, max(1, n - 1)))
        norm.update({"control_grid": grid, "degree_u": du, "degree_v": dv})
    if errors:
        return None, errors
    norm["spec_hash"] = _spec_hash(norm)
    return norm, []


__all__ = [name for name in list(globals()) if not name.startswith("__")]
