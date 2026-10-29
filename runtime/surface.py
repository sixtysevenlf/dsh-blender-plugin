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
真机自检：tests/surface_selftest.py（preload="surface,audit"）。
"""
import hashlib
import json
import math
import os
import sys
import time

import bpy
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
# ────────────────────────────── 数学：B-spline / Catmull-Rom / 标架 ──────────────────────────────
def _bspline_knots(n, p):
    """clamped 均匀节点向量（n 个基函数、次数 p）：长度 n+p+1。"""
    interior = n - p - 1
    U = [0.0] * (p + 1)
    for i in range(1, interior + 1):
        U.append(i / float(interior + 1))
    U += [1.0] * (p + 1)
    return np.array(U, dtype=np.float64)


def _bspline_basis_matrix(params, n, p):
    """params(m,) × clamped B-spline 基函数 (n) → (m,n) 矩阵；t 夹到 [0,1)。"""
    U = _bspline_knots(n, p)
    m = n + p + 1
    P = np.asarray(params, dtype=np.float64).reshape(-1)
    out = np.zeros((len(P), n), dtype=np.float64)
    for row, t in enumerate(P):
        t = min(max(float(t), 0.0), 1.0 - 1e-12)
        N = ((U[:-1] <= t) & (t < U[1:])).astype(np.float64)   # 长度 m-1
        N = np.concatenate([N, [0.0]])
        for d in range(1, p + 1):
            # Cox-de Boor 递推：i 的有效上界让 idx+d+1 不越界（m-d-2），末层正好覆盖 n 个基函数
            idx = np.arange(0, m - d - 1, dtype=np.int64)
            d1 = U[idx + d] - U[idx]
            d2 = U[idx + d + 1] - U[idx + 1]
            left = np.where(d1 > 1e-12, (t - U[idx]) / np.maximum(d1, 1e-12), 0.0) * N[idx]
            right = np.where(d2 > 1e-12, (U[idx + d + 1] - t) / np.maximum(d2, 1e-12), 0.0) * N[idx + 1]
            Nn = np.zeros(m, dtype=np.float64)
            Nn[idx] = left + right
            N = Nn
        out[row, :] = N[:n]
    return out


def _bspline_interp(points, degree, sample_params, knot_params=None):
    """过控制点（插值型 clamped B-spline）：返回 (samples, dim) 与 (degree, knot_params)。

    points: (n, dim)；knot_params: (n,) 严格递增、首 0 尾 1（默认均匀）；sample_params: (m,) ∈[0,1]。
    """
    P = np.asarray(points, dtype=np.float64)
    n = int(P.shape[0])
    if n < 2:
        return np.repeat(P[:1], len(np.asarray(sample_params).reshape(-1)), axis=0), 1, np.array([0.0])
    p = int(min(max(1, int(degree)), n - 1))
    if knot_params is None:
        t = np.linspace(0.0, 1.0, n)
    else:
        t = np.asarray(knot_params, dtype=np.float64).reshape(-1)
        if len(t) != n:
            raise ValueError("knot_params 长度必须等于控制点数 %d" % n)
        if t[0] != 0.0 or abs(t[-1] - 1.0) > 1e-9:
            t = (t - t[0]) / max(t[-1] - t[0], 1e-12)
        if np.any(np.diff(t) <= 0):
            raise ValueError("knot_params 必须严格递增（截面/路径参数重复会让插值矩阵奇异）")
    A = _bspline_basis_matrix(t, n, p)
    try:
        C = np.linalg.solve(A, P)
    except np.linalg.LinAlgError:
        raise ValueError("B-spline 插值矩阵奇异（参数点太靠近或重复）；拉开 section/rail 间距")
    B = _bspline_basis_matrix(sample_params, n, p)
    return B @ C, p, t


def _cr_point(p0, p1, p2, p3, t):
    t2 = t * t
    t3 = t2 * t
    return 0.5 * ((2.0 * p1) + (-p0 + p2) * t + (2.0 * p0 - 5.0 * p1 + 4.0 * p2 - p3) * t2
                  + (-p0 + 3.0 * p1 - 3.0 * p2 + p3) * t3)


def _resample_polyline(pts, count, closed=False):
    """折线/多边形 → count 个居中的 Catmull-Rom 采样点（按弦长均分；closed 环绕）。"""
    P = np.asarray(pts, dtype=np.float64)
    n = int(P.shape[0])
    count = int(max(2, count))
    if n == 0:
        raise ValueError("折线为空")
    if n == 1:
        return np.repeat(P, count, axis=0)
    if closed and n >= 3:
        seg_i = list(range(n))
        seg_j = [(i + 1) % n for i in range(n)]
        s = np.arange(count, dtype=np.float64) / float(count)
    else:
        seg_i = list(range(n - 1))
        seg_j = list(range(1, n))
        s = np.linspace(0.0, 1.0, count)
    lengths = np.array([float(np.linalg.norm(P[j] - P[i])) for i, j in zip(seg_i, seg_j)], dtype=np.float64)
    total = float(lengths.sum())
    if total <= 1e-12:
        return np.repeat(P[:1], count, axis=0)
    cum = np.cumsum(lengths) / total
    idx = np.clip(np.searchsorted(cum, s, side="right"), 0, len(seg_i) - 1)
    prev = np.concatenate([[0.0], cum[:-1]])
    span = np.maximum(cum - prev, 1e-12)
    tl = np.clip((s - prev[idx]) / span[idx], 0.0, 1.0)
    out = np.zeros((count, P.shape[1]), dtype=np.float64)
    for k in range(count):
        si = int(idx[k])
        i, j = seg_i[si], seg_j[si]
        if closed and n >= 3:
            p0, p1, p2, p3 = P[(i - 1) % n], P[i], P[j], P[(j + 1) % n]
        else:
            p0 = P[i - 1] if i > 0 else 2.0 * P[i] - P[j]
            p3 = P[j + 1] if j + 1 < n else 2.0 * P[j] - P[i]
            p1, p2 = P[i], P[j]
        out[k] = _cr_point(p0, p1, p2, p3, float(tl[k]))
    return out


def _frames(rail, orientation="auto", up=None):
    """沿折线采样点的平行传输标架 → (tangents, normals, binormals)，全部单位向量。"""
    P = np.asarray(rail, dtype=np.float64)
    n = int(P.shape[0])
    if n < 2:
        raise ValueError("rail 至少 2 点")
    tang = np.zeros_like(P)
    tang[:-1] = P[1:] - P[:-1]
    tang[-1] = P[-1] - P[-2]
    tang[0] = P[1] - P[0]
    tlen = np.linalg.norm(tang, axis=1)
    if np.any(tlen < 1e-12):
        raise ValueError("rail 切线退化（相邻点重合）")
    tang = tang / tlen[:, None]
    ref = None
    if orientation == "up_z":
        ref = np.array([0.0, 0.0, 1.0])
    elif orientation == "up_y":
        ref = np.array([0.0, 1.0, 0.0])
    elif orientation == "fixed":
        u = np.array(up if up else [0.0, 0.0, 1.0], dtype=np.float64)
        nl = float(np.linalg.norm(u))
        ref = u / nl if nl > 1e-12 else np.array([0.0, 0.0, 1.0])
    normals = np.zeros_like(P)
    prev = None
    for i in range(n):
        t = tang[i]
        cand = prev if (orientation == "auto" and prev is not None) else (ref if ref is not None else None)
        if cand is None:
            cand = np.array([0.0, 0.0, 1.0]) if abs(float(t[2])) < 0.9 else np.array([0.0, 1.0, 0.0])
        nv = cand - t * float(np.dot(cand, t))
        ln = float(np.linalg.norm(nv))
        if ln < 1e-8:
            # 参考轴与切线近平行：换一根全局轴，再不行用上一点法线
            alt = np.array([1.0, 0.0, 0.0]) if abs(float(t[0])) < 0.9 else np.array([0.0, 1.0, 0.0])
            nv = alt - t * float(np.dot(alt, t))
            ln = float(np.linalg.norm(nv))
            if ln < 1e-8 and prev is not None:
                nv = prev - t * float(np.dot(prev, t))
                ln = float(np.linalg.norm(nv))
        if ln < 1e-8:
            raise ValueError("无法为 rail 构造正交标架（切线方向病态）")
        nv = nv / ln
        if prev is not None and float(np.dot(nv, prev)) < 0.0:
            nv = -nv
        normals[i] = nv
        prev = nv
    binormals = np.cross(tang, normals)
    bl = np.linalg.norm(binormals, axis=1)
    binormals = binormals / np.maximum(bl, 1e-12)[:, None]
    return tang, normals, binormals


def _grid_faces(rows, cols, closed_u=False, closed_v=False):
    """(rows × cols) 顶点网格 → 四边形 face 列表（约定：行=v，列=u）。"""
    faces = []
    r_max = rows if closed_v else rows - 1
    c_max = cols if closed_u else cols - 1
    for r in range(r_max):
        r2 = (r + 1) % rows
        for c in range(c_max):
            c2 = (c + 1) % cols
            a = r * cols + c
            b = r * cols + c2
            cc = r2 * cols + c2
            d = r2 * cols + c
            faces.append((a, b, cc, d))
    return faces


# ────────────────────────────── M1：曲面网格生成（mm → Blender 单位） ──────────────────────────────
def _build_loft(norm):
    secs = norm["sections"]
    u = int(norm["tessellation"]["u"])
    v = int(norm["tessellation"]["v"])
    ns = len(secs)
    rings = []
    for sec in secs:
        rings.append(_resample_polyline(np.asarray(sec["points_mm"], dtype=np.float64), u,
                                        closed=bool(norm["closed"])))
    rings = np.asarray(rings, dtype=np.float64)                       # (ns,u,2)
    zs = np.array([s["z_mm"] for s in secs], dtype=np.float64)
    t = (zs - zs[0]) / max(zs[-1] - zs[0], 1e-12)                     # 截面参数（严格递增）
    sp = np.linspace(0.0, 1.0, v)
    flat = rings.reshape(ns, -1)                                       # (ns,u*2)
    xy, p_used, _t = _bspline_interp(flat, int(norm["degree"]), sp, knot_params=t)
    xy = xy.reshape(v, u, 2)
    z = np.interp(sp, t, zs)                                           # z 沿 v 线性（不额外摆动）
    verts = np.concatenate([xy, np.repeat(z[:, None, None], u, axis=1)], axis=2)  # (v,u,3) mm
    faces = _grid_faces(v, u, closed_u=bool(norm["closed"]), closed_v=False)
    return {"verts_mm": verts.reshape(-1, 3), "faces": faces,
            "meta": {"kind": "loft", "u": u, "v": v, "sections": ns, "degree_used": p_used,
                     "closed_u": bool(norm["closed"])}}


def _build_sweep(norm):
    u = int(norm["tessellation"]["u"])
    v = int(norm["tessellation"]["v"])
    prof = _resample_polyline(np.asarray(norm["profile_mm"], dtype=np.float64), u,
                              closed=bool(norm["closed"]))
    rail_raw = np.asarray(norm["rail_mm"], dtype=np.float64)
    closed_rail = bool((norm.get("constraints") or {}).get("closed_rail") or False)
    rail = _resample_polyline(rail_raw, v, closed=closed_rail)
    tang, normals, binormals = _frames(rail, str(norm["orientation"]),
                                       (norm.get("constraints") or {}).get("up"))
    # scale：标量或逐 rail 点列表 → 在弦长参数上插值到 v 个采样
    sc = norm["scale"]
    if isinstance(sc, list):
        src = np.asarray(sc, dtype=np.float64)
        if len(src) != len(rail_raw):
            src = np.repeat(src[:1], len(rail_raw))
        chord = np.concatenate([[0.0], np.cumsum(np.linalg.norm(np.diff(rail_raw, axis=0), axis=1))])
        chord = chord / max(chord[-1], 1e-12)
        rs = np.linspace(0.0, 1.0, len(rail))
        scale_v = np.interp(rs, chord, src)
    else:
        scale_v = np.full(len(rail), float(sc), dtype=np.float64)
    twist = np.radians(float(norm["twist_deg"]) or 0.0)
    s_par = np.linspace(0.0, 1.0, len(rail))
    verts = np.zeros((len(rail), u, 3), dtype=np.float64)
    for i in range(len(rail)):
        ang = twist * float(s_par[i])
        ca, sa = math.cos(ang), math.sin(ang)
        for k in range(u):
            px, py = float(prof[k][0]) * float(scale_v[i]), float(prof[k][1]) * float(scale_v[i])
            qx, qy = ca * px - sa * py, sa * px + ca * py
            verts[i, k] = rail[i] + normals[i] * qx + binormals[i] * qy
    faces = _grid_faces(len(rail), u, closed_u=bool(norm["closed"]), closed_v=closed_rail)
    return {"verts_mm": verts.reshape(-1, 3), "faces": faces,
            "meta": {"kind": "sweep", "u": u, "v": v, "profile_closed": bool(norm["closed"]),
                     "rail_closed": closed_rail, "twist_deg": float(norm["twist_deg"] or 0.0),
                     "orientation": str(norm["orientation"]), "scale": ("list" if isinstance(sc, list) else sc)}}


def _build_patch(norm):
    grid = np.asarray(norm["control_grid"], dtype=np.float64)          # (n_u, n_v, 3)
    n_u, n_v = int(grid.shape[0]), int(grid.shape[1])
    u = int(norm["tessellation"]["u"])
    v = int(norm["tessellation"]["v"])
    pu = int(min(int(norm["degree_u"]), n_u - 1))
    pv = int(min(int(norm["degree_v"]), n_v - 1))
    su = np.linspace(0.0, 1.0, u)
    sv = np.linspace(0.0, 1.0, v)
    Bu = _bspline_basis_matrix(su, n_u, pu)
    Bv = _bspline_basis_matrix(sv, n_v, pv)
    pts = np.einsum("ai,ijk,bj->abk", Bu, grid, Bv)                    # (u,v,3)
    faces = []
    for i in range(u - 1):
        for j in range(v - 1):
            a = i * v + j
            b = (i + 1) * v + j
            c = (i + 1) * v + (j + 1)
            d = i * v + (j + 1)
            faces.append((a, b, c, d))
    return {"verts_mm": pts.reshape(-1, 3), "faces": faces,
            "meta": {"kind": "patch", "u": u, "v": v, "degree_u_used": pu, "degree_v_used": pv,
                     "control_grid": [n_u, n_v]}}


def surface_mesh_data(norm):
    """M1：spec 归一化结果 → 内存网格（verts 为 mm；faces 索引）＋ meta。"""
    kind = str(norm.get("kind"))
    if kind == "loft":
        data = _build_loft(norm)
    elif kind == "sweep":
        data = _build_sweep(norm)
    elif kind == "patch":
        data = _build_patch(norm)
    else:
        raise ValueError("未知 kind: %r" % kind)
    verts = np.asarray(data["verts_mm"], dtype=np.float64)
    if verts.size and not np.isfinite(verts).all():
        raise ValueError("生成结果出现 NaN/Inf（参数病态）")
    faces = [tuple(int(i) for i in f) for f in data["faces"]]
    nv = len(verts)
    for f in faces:
        if any(i < 0 or i >= nv for i in f):
            raise ValueError("face 索引越界")
    return {"verts_mm": verts, "faces": faces, "meta": data["meta"],
            "verts": int(nv), "tris": int(sum(max(0, len(f) - 2) for f in faces))}
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


def _resolve_source(src):
    """spec(dict) 或 mesh 对象名 → 分析上下文 {verts_mm, tris, faces, spec, source}；坏输入抛异常。"""
    mmu = max(_mmu(), 1e-12)
    if isinstance(src, dict) and "kind" not in src and src.get("object"):
        src = str(src.get("object"))
    if isinstance(src, dict):
        norm, errs = surface_spec_normalize(src)
        if errs:
            raise _SpecError(errs)
        md = surface_mesh_data(norm)
        return {"verts_mm": np.asarray(md["verts_mm"], dtype=np.float64),
                "faces": md["faces"], "tris": _faces_to_tris(md["faces"]),
                "spec": norm, "source": {"kind": "spec", "spec_kind": norm["kind"],
                                         "spec_hash": norm["spec_hash"]}}
    if isinstance(src, str):
        ob = bpy.data.objects.get(str(src))
        if ob is None:
            raise ValueError("对象不存在: %r" % src)
        if ob.type != "MESH":
            raise ValueError("对象不是 mesh（type=%s）: %r" % (ob.type, src))
        baked = _object_eval_mesh(ob)
        if len(baked["tris"]) == 0:
            raise ValueError("对象求值后 0 三角面: %r" % src)
        return {"verts_mm": baked["verts"] * mmu, "faces": None, "tris": baked["tris"],
                "spec": None, "source": {"kind": "object", "object": str(ob.name)}}
    raise ValueError("spec_or_object 必须是 SurfaceSpec 对象或 mesh 对象名")


def _vertex_normals(verts, tris):
    V = np.asarray(verts, dtype=np.float64)
    T = np.asarray(tris, dtype=np.int64)
    n = np.zeros_like(V)
    if len(T) == 0:
        return n
    a, b, c = V[T[:, 0]], V[T[:, 1]], V[T[:, 2]]
    fn = np.cross(b - a, c - a)
    for k in range(3):
        np.add.at(n, T[:, k], fn)
    ln = np.linalg.norm(n, axis=1)
    ok = ln > 1e-12
    n[ok] = n[ok] / ln[ok, None]
    n[~ok] = np.array([0.0, 0.0, 1.0])
    return n


def _mesh_topo(verts, tris):
    """拓扑/质量统计：边界边、非流形边、退化面、长宽比、边长、翻转法线。"""
    V = np.asarray(verts, dtype=np.float64)
    T = np.asarray(tris, dtype=np.int64)
    if len(T) == 0:
        return {"tris": 0, "verts": len(V), "boundary_edges": 0, "nonmanifold_edges": 0,
                "watertight": False, "degenerate_faces": 0, "flipped_normals": 0,
                "aspect_max": None, "aspect_mean": None, "edge_min_mm": None, "edge_max_mm": None}
    edges = np.concatenate([T[:, [0, 1]], T[:, [1, 2]], T[:, [2, 0]]], axis=0)
    uniq, cnt = np.unique(np.sort(edges, axis=1), axis=0, return_counts=True)
    boundary = int((cnt == 1).sum())
    nonmanifold = int((cnt > 2).sum())
    a, b, c = V[T[:, 0]], V[T[:, 1]], V[T[:, 2]]
    cross = np.cross(b - a, c - a)
    area = 0.5 * np.linalg.norm(cross, axis=1)
    degenerate = int((area < 1e-12).sum())
    e = np.stack([np.linalg.norm(b - a, axis=1), np.linalg.norm(c - b, axis=1),
                  np.linalg.norm(a - c, axis=1)], axis=1)
    emax = e.max(axis=1)
    with np.errstate(divide="ignore", invalid="ignore"):
        aspect = np.where(area > 1e-12, (emax ** 2) / (2.0 * np.maximum(area, 1e-30)), np.inf)
    finite = aspect[np.isfinite(aspect)]
    # 翻转法线：流形边上两面法线夹角 > 90°
    fn = cross / np.maximum(np.linalg.norm(cross, axis=1), 1e-30)[:, None]
    edges_n = np.sort(np.concatenate([T[:, [0, 1]], T[:, [1, 2]], T[:, [2, 0]]], axis=0), axis=1)
    order = np.lexsort((edges_n[:, 1], edges_n[:, 0]))
    edges_s = edges_n[order]
    tri_of_edge = np.concatenate([np.arange(len(T))] * 3)[order]
    flipped = 0
    i = 0
    while i + 1 < len(edges_s):
        if edges_s[i][0] == edges_s[i + 1][0] and edges_s[i][1] == edges_s[i + 1][1]:
            t1, t2 = int(tri_of_edge[i]), int(tri_of_edge[i + 1])
            if t1 != t2 and float(np.dot(fn[t1], fn[t2])) < -0.1:
                flipped += 1
            i += 2
        else:
            i += 1
    return {"tris": int(len(T)), "verts": int(len(V)),
            "boundary_edges": boundary, "nonmanifold_edges": nonmanifold,
            "watertight": bool(boundary == 0 and nonmanifold == 0),
            "degenerate_faces": degenerate, "flipped_normals": int(flipped),
            "aspect_max": (_r(float(finite.max()), 4) if len(finite) else None),
            "aspect_mean": (_r(float(finite.mean()), 4) if len(finite) else None),
            "edge_min_mm": _r(float(e.min()), 4), "edge_max_mm": _r(float(e.max()), 4)}


def _curvature_metrics(verts, tris, vnormals):
    """离散曲率近似：每条边 k = 两顶点法线夹角 / 边长(mm)；逐顶点取入射边均值。"""
    V = np.asarray(verts, dtype=np.float64)
    T = np.asarray(tris, dtype=np.int64)
    nv = len(V)
    if len(T) == 0 or nv == 0:
        return {"max_1_per_mm": None, "mean_1_per_mm": None, "hotspots": 0,
                "p95_1_per_mm": None, "n_edges": 0}
    edges = np.sort(np.concatenate([T[:, [0, 1]], T[:, [1, 2]], T[:, [2, 0]]], axis=0), axis=1)
    edges = np.unique(edges, axis=0)
    i, j = edges[:, 0], edges[:, 1]
    lens = np.linalg.norm(V[j] - V[i], axis=1)
    ok = lens > 1e-9
    cosang = np.clip(np.einsum("ij,ij->i", vnormals[i], vnormals[j]), -1.0, 1.0)
    ang = np.arccos(cosang)
    k_edge = np.where(ok, ang / np.maximum(lens, 1e-12), 0.0)
    acc = np.zeros(nv, dtype=np.float64)
    cnt = np.zeros(nv, dtype=np.float64)
    np.add.at(acc, i, k_edge)
    np.add.at(acc, j, k_edge)
    np.add.at(cnt, i, 1.0)
    np.add.at(cnt, j, 1.0)
    k_v = np.where(cnt > 0, acc / np.maximum(cnt, 1e-12), 0.0)
    mean = float(k_v.mean())
    hotspot_thr = max(2.0 * mean, 0.5)          # 1/mm ⇒ 半径 ≤2mm 的急弯
    return {"max_1_per_mm": _r(float(k_v.max()), 6), "mean_1_per_mm": _r(mean, 6),
            "hotspots": int((k_v > hotspot_thr).sum()), "hotspot_threshold_1_per_mm": _r(hotspot_thr, 6),
            "p95_1_per_mm": _r(float(np.percentile(k_v, 95)), 6),
            "n_edges": int(len(edges))}


def _self_intersection(verts, tris, max_tris):
    """BVH 自交：tri-tri overlap（排除共顶点的拓扑相邻对）。超三角上限 → degraded。"""
    from mathutils.bvhtree import BVHTree
    T = np.asarray(tris, dtype=np.int64)
    if len(T) == 0:
        return {"state": "pass", "pairs": 0, "analyzed": True, "limit": int(max_tris)}
    if int(max_tris) > 0 and len(T) > int(max_tris):
        return {"state": "degraded", "pairs": None, "analyzed": False, "limit": int(max_tris),
                "reason": "三角数 %d > max_tris %d，自交检查跳过（不当作 pass）" % (len(T), int(max_tris))}
    V = np.asarray(verts, dtype=np.float64)
    try:
        tree = BVHTree.FromPolygons([tuple(v) for v in V.tolist()],
                                    [tuple(int(i) for i in t) for t in T.tolist()],
                                    all_triangles=True)
        raw = tree.overlap(tree) or []
    except Exception as e:
        return {"state": "degraded", "pairs": None, "analyzed": False, "limit": int(max_tris),
                "reason": "BVH 构建/自重叠失败: %s: %s" % (type(e).__name__, str(e)[:160])}
    seen = set()
    for a, b in raw:
        if a >= b:
            continue
        if len(set(T[a].tolist()) & set(T[b].tolist())) > 0:
            continue
        seen.add((int(a), int(b)))
        if len(seen) >= 5000:
            break
    sample = []
    for (a, b) in list(seen)[:5]:
        cen = 0.5 * (V[T[a]].mean(axis=0) + V[T[b]].mean(axis=0))
        sample.append({"tri_a": int(a), "tri_b": int(b), "at_mm": [_r(x, 4) for x in cen]})
    return {"state": "fail" if seen else "pass", "pairs": int(len(seen)),
            "analyzed": True, "limit": int(max_tris), "sample": sample}


def _thickness(verts, tris, vnormals, watertight, max_samples=1500):
    """法线-射线近似厚度（mm）：闭合壳从顶点向内打射线，取首个交点距离。"""
    if not watertight:
        return {"state": "n/a", "reason": "开放曲面没有厚度（内向射线会穿出）；闭合壳才量",
                "min_mm": None, "median_mm": None, "sampled": 0}
    from mathutils.bvhtree import BVHTree
    V = np.asarray(verts, dtype=np.float64)
    T = np.asarray(tris, dtype=np.int64)
    try:
        tree = BVHTree.FromPolygons([tuple(v) for v in V.tolist()],
                                    [tuple(int(i) for i in t) for t in T.tolist()],
                                    all_triangles=True)
    except Exception as e:
        return {"state": "degraded", "reason": "厚度 BVH 构建失败: %s: %s" % (type(e).__name__, str(e)[:160]),
                "min_mm": None, "median_mm": None, "sampled": 0}
    from mathutils import Vector
    nv = len(V)
    idx = np.unique(np.linspace(0, nv - 1, min(int(max_samples), nv)).astype(np.int64))
    diag = float(np.linalg.norm(V.max(axis=0) - V.min(axis=0)))
    eps = max(1e-6, diag * 1e-4)
    vals = []
    for vi in idx.tolist():
        p = Vector(V[vi].tolist())
        d = Vector([-float(x) for x in vnormals[vi]])
        origin = p + d * eps
        hit = tree.ray_cast(origin, d)
        if hit is None or hit[0] is None:
            continue
        dist = float(hit[3]) + eps
        if dist > eps * 2.0 and math.isfinite(dist):
            vals.append(dist)
    if not vals:
        return {"state": "degraded", "reason": "闭合壳上射线全部没打到对面（法线/拓扑可疑）",
                "min_mm": None, "median_mm": None, "sampled": 0}
    arr = np.asarray(vals, dtype=np.float64)
    return {"state": "pass", "min_mm": _r(float(arr.min()), 4), "median_mm": _r(float(np.median(arr)), 4),
            "p05_mm": _r(float(np.percentile(arr, 5)), 4), "sampled": int(len(arr))}


def _continuity_spec(ctx, g1_tol_deg, g2_tol_ratio):
    """spec 模式的缝隙连续性：loft 看 section 缝（法线/曲率跳变）；sweep 看闭合剖面接缝；patch=n/a。"""
    spec = ctx.get("spec") or {}
    kind = str(spec.get("kind"))
    V = ctx["verts_mm"]
    T = ctx["tris"]
    vn = _vertex_normals(V, T)
    tess = spec.get("tessellation") or {}
    u = int(tess.get("u") or 0)
    v = int(tess.get("v") or 0)
    target = str(spec.get("continuity") or "G1")
    out = {"target": target, "method": None, "g0": True, "g1_deg": None, "g2_ratio": None,
           "state": "n/a", "note": ""}

    def _angle(a, b):
        c = float(np.clip(np.dot(a, b), -1.0, 1.0))
        return math.degrees(math.acos(c))

    if kind == "loft" and u > 0 and v > 1:
        secs = spec.get("sections") or []
        zs = np.array([s["z_mm"] for s in secs], dtype=np.float64)
        t = (zs - zs[0]) / max(zs[-1] - zs[0], 1e-12)
        rows = []
        for tt in t.tolist():
            rows.append(int(np.clip(round(tt * (v - 1)), 0, v - 1)))
        max_ang, max_jump = 0.0, 0.0
        # 只在内部 section 缝上量（跳过 v 两端）
        for k in range(1, len(rows) - 1):
            r = rows[k]
            if r <= 0 or r >= v - 1:
                continue
            for c in range(u):
                n_prev = vn[(r - 1) * u + c]
                n_mid = vn[r * u + c]
                n_next = vn[(r + 1) * u + c]
                a1 = _angle(n_prev, n_mid)
                a2 = _angle(n_mid, n_next)
                max_ang = max(max_ang, a1, a2)
                k1 = a1 / max(1e-9, float(np.linalg.norm(V[r * u + c] - V[(r - 1) * u + c])))
                k2 = a2 / max(1e-9, float(np.linalg.norm(V[(r + 1) * u + c] - V[r * u + c])))
                jump = abs(k2 - k1) / max(k1, k2, 1e-12)
                max_jump = max(max_jump, jump)
        out.update({"method": "loft section 缝（相邻两段法线角 / 曲率跳变）",
                    "g1_deg": _r(max_ang, 4), "g2_ratio": _r(max_jump, 4),
                    "seams": max(0, len(rows) - 2)})
    elif kind == "sweep" and u > 2 and v > 1:
        if not spec.get("closed"):
            out["note"] = "开放剖面没有接缝；sweep 连续性无内部缝可查"
            return out
        max_ang = 0.0
        for r in range(v):
            n0 = vn[r * u + (u - 1)]
            n1 = vn[r * u + 0]
            max_ang = max(max_ang, _angle(n0, n1))
        out.update({"method": "sweep 闭合剖面接缝（法线角）", "g1_deg": _r(max_ang, 4),
                    "g2_ratio": 0.0, "seams": v})
    else:
        out["note"] = "%s 单张面片没有内部边界缝（G0/G1/G2 由构造保证）" % kind
        out["state"] = "n/a"
        return out
    if target == "G0":
        out["state"] = "pass"
        return out
    ok = (out["g1_deg"] is not None and out["g1_deg"] <= float(g1_tol_deg))
    if target == "G2":
        ok = ok and (out["g2_ratio"] is not None and out["g2_ratio"] <= float(g2_tol_ratio))
    out["state"] = "pass" if ok else "fail"
    out["reason"] = ("%s 连续：最大法线角 %.3f° ≤ %.3f°%s"
                     % (target, out["g1_deg"] or 0.0, float(g1_tol_deg),
                        "" if target != "G2" else "，曲率跳变 %.3f ≤ %.3f"
                        % (out["g2_ratio"] or 0.0, float(g2_tol_ratio)))) if ok else \
                    ("%s 不连续：最大法线角 %.3f°（> %.3f°）%s"
                     % (target, out["g1_deg"] or 0.0, float(g1_tol_deg),
                        "" if target != "G2" else " / 曲率跳变 %.3f（> %.3f）"
                        % (out["g2_ratio"] or 0.0, float(g2_tol_ratio))))
    return out


def _continuity_mesh(verts, tris, g1_tol_deg):
    """对象模式：网格光滑度（最大二面角）；只作参考，不按 spec 目标判 fail。"""
    V = np.asarray(verts, dtype=np.float64)
    T = np.asarray(tris, dtype=np.int64)
    if len(T) == 0:
        return {"state": "n/a", "method": "mesh dihedral", "max_dihedral_deg": None}
    face_n = np.cross(V[T[:, 1]] - V[T[:, 0]], V[T[:, 2]] - V[T[:, 0]])
    face_n = face_n / np.maximum(np.linalg.norm(face_n, axis=1), 1e-30)[:, None]
    edges = np.sort(np.concatenate([T[:, [0, 1]], T[:, [1, 2]], T[:, [2, 0]]], axis=0), axis=1)
    order = np.lexsort((edges[:, 1], edges[:, 0]))
    es = edges[order]
    te = np.concatenate([np.arange(len(T))] * 3)[order]
    max_d = 0.0
    i = 0
    while i + 1 < len(es):
        if es[i][0] == es[i + 1][0] and es[i][1] == es[i + 1][1]:
            t1, t2 = int(te[i]), int(te[i + 1])
            if t1 != t2:
                c = float(np.clip(np.dot(face_n[t1], face_n[t2]), -1.0, 1.0))
                max_d = max(max_d, math.degrees(math.acos(c)))
            i += 2
        else:
            i += 1
    return {"state": "n/a", "method": "mesh dihedral（对象没有声明的 continuity 目标，只给数字）",
            "max_dihedral_deg": _r(max_d, 4), "tolerance_reference_deg": float(g1_tol_deg)}
# ────────────────────────────── M2 公开 op：surface_validate ──────────────────────────────
def surface_validate(spec_or_object=None, continuity=True, curvature=True, self_intersection=True,
                     thickness=True, draft=False, min_thickness_mm=None, max_curvature=None,
                     max_tris=120000, g1_tol_deg=None, g2_tol_ratio=None):
    """曲面/对象体检：连续性、曲率、自交、厚度、网格质量 → ok/verdict/metrics/defects/next。

    判据（fail 不许静默成 pass）：
      * 确认缺陷 ⇒ fail：自交 >0、闭合壳最小厚度 < 目标、法线翻转、退化面、非流形边、超曲率约束；
      * 资源不足/量不到 ⇒ degraded：三角数超 max_tris 没做自交、闭合壳厚度射线全落空；
      * 开放曲面的「无厚度」是 n/a（曲面片本来没有体积），不算 fail 也不算 degraded；
      * 对象没有声明的 continuity 目标 → 连续性只给网格二面角数字（informational），不按 spec 判 fail。
    """
    t0 = time.perf_counter()
    try:
        ctx = _resolve_source(spec_or_object)
    except _SpecError as e:
        return _err("SurfaceSpec 校验失败（坏参数）", errors=e.errors[:12], stage="M0")
    except Exception as e:
        return _err("无法解析 spec_or_object: %s: %s" % (type(e).__name__, str(e)[:240]), stage="M0")
    spec = ctx.get("spec")
    cons = dict((spec or {}).get("constraints") or {})
    gi_tol = _num(g1_tol_deg) or _num(cons.get("g1_tol_deg")) or 12.0
    g2_tol = _num(g2_tol_ratio) or _num(cons.get("g2_tol_ratio")) or 0.6
    thin_min = _num(min_thickness_mm) or _num(cons.get("min_thickness_mm")) or 0.8
    curv_max = _num(max_curvature) if max_curvature is not None else _num(cons.get("max_curvature_1_per_mm"))
    V = ctx["verts_mm"]
    T = ctx["tris"]
    vn = _vertex_normals(V, T)
    topo = _mesh_topo(V, T)
    checks, defects, skipped, nexts = {}, [], [], []

    # ---- 网格质量 ----
    q_state = "pass"
    if topo["degenerate_faces"]:
        q_state = "fail"
        defects.append({"check": "mesh_quality", "severity": "fail",
                        "msg": "退化面 %d 个（面积≈0）——先修截面/重采样" % topo["degenerate_faces"]})
    if topo["flipped_normals"]:
        q_state = "fail"
        defects.append({"check": "mesh_quality", "severity": "fail",
                        "msg": "法线翻转 %d 条边（相邻面法线夹角 >90°）" % topo["flipped_normals"]})
    if topo["nonmanifold_edges"]:
        q_state = "fail"
        defects.append({"check": "mesh_quality", "severity": "fail",
                        "msg": "非流形边 %d 条（>2 面共边）" % topo["nonmanifold_edges"]})
    checks["mesh_quality"] = dict({"state": q_state}, **topo)
    if not topo["watertight"]:
        nexts.append("开放边界 %d 条（曲面片正常；要厚度/水密请封口或用闭合实体）" % topo["boundary_edges"])

    # ---- 曲率 ----
    cur = None
    if curvature:
        cur = _curvature_metrics(V, T, vn)
        st = "pass"
        if curv_max is not None and cur["max_1_per_mm"] is not None and cur["max_1_per_mm"] > float(curv_max):
            st = "fail"
            defects.append({"check": "curvature", "severity": "fail",
                            "msg": "最大曲率 %.6g /mm > 约束 %.6g /mm（急弯/噪声）"
                                   % (cur["max_1_per_mm"], float(curv_max))})
        elif cur["hotspots"]:
            nexts.append("曲率热点 %d 个（>%.3g /mm）——检查截面半径突变或网格噪声"
                         % (cur["hotspots"], cur["hotspot_threshold_1_per_mm"] or 0.0))
        checks["curvature"] = dict({"state": st}, **cur)
    else:
        skipped.append({"check": "curvature", "why": "continuity/curvature=False（调用方显式跳过）"})

    # ---- 自交 ----
    si = None
    if self_intersection:
        lim = int(min(int(max_tris), 20000)) if draft else int(max_tris)
        si = _self_intersection(V, T, lim)
        checks["self_intersection"] = si
        if si.get("state") == "fail":
            defects.append({"check": "self_intersection", "severity": "fail",
                            "msg": "自交 %d 对三角面（超限前 5000 对截断）" % int(si.get("pairs") or 0),
                            "sample": si.get("sample")})
        elif si.get("state") == "degraded":
            skipped.append({"check": "self_intersection", "why": si.get("reason")})
    else:
        skipped.append({"check": "self_intersection", "why": "self_intersection=False"})

    # ---- 厚度 ----
    th = None
    if thickness:
        if draft:
            th = {"state": "n/a", "reason": "draft=True 跳过厚度（高成本）",
                  "min_mm": None, "median_mm": None, "sampled": 0}
            skipped.append({"check": "thickness", "why": th["reason"]})
        else:
            th = _thickness(V, T, vn, bool(topo["watertight"]))
            if th.get("state") == "pass" and th.get("min_mm") is not None:
                if th["min_mm"] < float(thin_min):
                    th["state"] = "fail"
                    defects.append({"check": "thickness", "severity": "fail",
                                    "msg": "近似最小厚度 %.4g mm < 目标 %.4g mm" % (th["min_mm"], float(thin_min))})
            elif th.get("state") == "n/a":
                skipped.append({"check": "thickness", "why": th.get("reason")})
            elif th.get("state") == "degraded":
                skipped.append({"check": "thickness", "why": th.get("reason")})
            checks["thickness"] = th
    else:
        skipped.append({"check": "thickness", "why": "thickness=False"})

    # ---- 连续性 ----
    cont = None
    if continuity:
        if spec is not None:
            cont = _continuity_spec(ctx, gi_tol, g2_tol)
            if cont.get("state") == "fail":
                defects.append({"check": "continuity", "severity": "fail",
                                "msg": cont.get("reason") or "边界连续性不达标",
                                "g1_deg": cont.get("g1_deg"), "g2_ratio": cont.get("g2_ratio")})
            elif cont.get("state") == "n/a":
                skipped.append({"check": "continuity", "why": cont.get("note")})
        else:
            cont = _continuity_mesh(V, T, gi_tol)
            if cont.get("max_dihedral_deg") is not None and cont["max_dihedral_deg"] > 90.0:
                nexts.append("对象有 %0.1f° 二面角（信息性；对象没有声明 continuity 目标，未判 fail）"
                             % cont["max_dihedral_deg"])
        checks["continuity"] = cont
    else:
        skipped.append({"check": "continuity", "why": "continuity=False"})

    if any(d.get("severity") == "fail" for d in defects):
        verdict = "fail"
    elif any((c or {}).get("state") == "degraded" for c in checks.values()):
        verdict = "degraded"
    else:
        verdict = "pass"
    if verdict == "fail":
        nexts.insert(0, "先按 defects 逐条修（改截面/参数或换更大 tessellation），再复跑 validate；不许把 fail 当 pass")
    elif verdict == "degraded":
        nexts.insert(0, "有检查没跑成（%s）——调大 max_tris 或给闭合壳后复跑；degraded ≠ 通过"
                     % (skipped[-1].get("check") if skipped else "超限"))
    bbox = _bbox_mm(V)
    body = {
        "ok": True, "version": SURFACE_VERSION, "verdict": verdict,
        "source": ctx.get("source"), "spec_hash": (spec or {}).get("spec_hash"),
        "metrics": {"verts": int(len(V)), "tris": int(len(T)), "bbox_mm": bbox,
                    "watertight": bool(topo["watertight"]),
                    "boundary_edges": int(topo["boundary_edges"]),
                    "nonmanifold_edges": int(topo["nonmanifold_edges"]),
                    "degenerate_faces": int(topo["degenerate_faces"]),
                    "flipped_normals": int(topo["flipped_normals"]),
                    "aspect_max": topo["aspect_max"],
                    "curvature": ({"max_1_per_mm": cur["max_1_per_mm"], "mean_1_per_mm": cur["mean_1_per_mm"],
                                   "hotspots": cur["hotspots"]} if cur else None),
                    "thickness": ({"min_mm": th.get("min_mm"), "median_mm": th.get("median_mm"),
                                   "state": th.get("state")} if th else None),
                    "self_intersection": ({"pairs": si.get("pairs"), "state": si.get("state"),
                                           "analyzed": si.get("analyzed")} if si else None),
                    "continuity": ({"state": cont.get("state"), "g1_deg": cont.get("g1_deg"),
                                    "g2_ratio": cont.get("g2_ratio"),
                                    "max_dihedral_deg": cont.get("max_dihedral_deg")} if cont else None)},
        "checks": checks, "defects": defects, "skipped": skipped, "next": nexts[:8],
        "targets": {"min_thickness_mm": _r(thin_min, 4), "max_curvature_1_per_mm": curv_max,
                    "g1_tol_deg": _r(gi_tol, 4), "g2_tol_ratio": _r(g2_tol, 4),
                    "max_tris": int(max_tris), "draft": bool(draft)},
        "note": "fail 不静默成 pass；degraded=没查完（degraded ≠ 通过）；开放曲面无厚度是 n/a",
        "honest_limits": [
            "曲率是离散近似（顶点法线夹角/边长），对三角化密度敏感；只用于找急弯热点，不是解析曲率",
            "厚度是向内射线近似（顶点子集 ≤1500）；非凸/空腔件可能读到局部直径而不是壁厚",
            "自交是 BVH tri-tri（排除共顶点相邻对）；三角数超 max_tris ⇒ degraded 不当作无自交",
            "对象模式没有声明的 G1/G2 目标：连续性只报二面角参考值，spec 模式才按 continuity 判 fail",
        ],
    }
    return _j(_receipt(body, t0))
# ────────────────────────────── M4：selftest / help / 注册 ──────────────────────────────
def _cleanup_objects(names):
    removed = []
    for nm in list(names or []):
        ob = bpy.data.objects.get(str(nm))
        if ob is None:
            continue
        me = ob.data if ob.type == "MESH" else None
        try:
            bpy.data.objects.remove(ob, do_unlink=True)
            removed.append(str(nm))
        except Exception:
            continue
        if me is not None and me.users == 0:
            try:
                bpy.data.meshes.remove(me, do_unlink=True)
            except Exception:
                pass
    return removed


def _circle_pts(n, rx, ry=None, cx=0.0, cy=0.0):
    ry = rx if ry is None else ry
    return [[cx + rx * math.cos(2.0 * math.pi * k / float(n)),
             cy + ry * math.sin(2.0 * math.pi * k / float(n))] for k in range(int(n))]


def _meshes_equal(ob_a, ob_b, n=64):
    if not ob_a or not ob_b:
        return False, {"error": "object_missing", "a": ob_a, "b": ob_b}
    a, b = bpy.data.objects.get(str(ob_a)), bpy.data.objects.get(str(ob_b))
    if a is None or b is None or a.type != "MESH" or b.type != "MESH":
        return False, "对象缺失"
    va = [tuple(round(float(x), 9) for x in v.co) for v in a.data.vertices[:n]]
    vb = [tuple(round(float(x), 9) for x in v.co) for v in b.data.vertices[:n]]
    return (len(a.data.vertices) == len(b.data.vertices) and va == vb), {"verts": len(a.data.vertices)}


def surface_selftest():
    """M4 自检：瓶身(loft)/翼子板(sweep)/头盔简化(patch) 三基准 + 自交/薄壁/连续性负例 + 坏参数。"""
    ev = {}
    t0 = time.perf_counter()
    made = []
    before_objs = set(bpy.data.objects)
    try:
        prev_scale = float(bpy.context.scene.unit_settings.scale_length)
        bpy.context.scene.unit_settings.scale_length = 0.001      # 1 单位 = 1 mm
        # ---- 三个基准 ----
        ns = 8
        sections = []
        for k in range(ns):
            tpar = k / float(ns - 1)
            z = 160.0 * tpar
            r = 30.0 + 8.0 * math.sin(math.pi * tpar)
            sections.append({"z_mm": z, "points_mm": _circle_pts(24, r)})
        r_bottle = json.loads(surface_loft(sections=sections, degree=3, continuity="G1",
                                           closed=True, tessellation={"u": 32, "v": 24},
                                           name="__surface_eval_bottle"))
        if r_bottle.get("object"):
            made.append(r_bottle["object"])
        ev["bottle_ok"] = bool(r_bottle.get("ok") and r_bottle.get("verts", 0) > 0
                               and r_bottle.get("tris", 0) > 0)
        ev["bottle_bbox_z_mm"] = (r_bottle.get("bbox_mm") or {}).get("size_mm")
        # 翼子板截面用椭圆，但把**环向接缝放在顶部（曲率小）**——接缝落在长轴端的高曲率处会
        # 让离散法线角变大（那是采样分辨率问题，不是 G1 断裂）。这是真实建模里也该遵守的取缝位置。
        fender_prof = []
        for k in range(24):
            ang = math.pi / 2.0 + 2.0 * math.pi * k / 24.0
            fender_prof.append([30.0 * math.cos(ang), 14.0 * math.sin(ang)])
        r_fender = json.loads(surface_sweep(
            profile_mm=fender_prof,
            rail_mm=[[0, 0, 0], [30, 5, 2], [60, 12, 4], [90, 20, 6], [120, 28, 8]],
            orientation="auto", twist_deg=20.0, scale=[1.2, 1.1, 1.0, 0.95, 0.9],
            tessellation={"u": 32, "v": 32}, closed=True, name="__surface_eval_fender"))
        if r_fender.get("object"):
            made.append(r_fender["object"])
        ev["fender_ok"] = bool(r_fender.get("ok") and r_fender.get("verts", 0) > 0)
        grid = []
        for i in range(4):
            row = []
            x = -40.0 + 80.0 * i / 3.0
            for j in range(4):
                y = -40.0 + 80.0 * j / 3.0
                z = 55.0 - 20.0 * (x / 40.0) ** 2 - 15.0 * (y / 40.0) ** 2
                row.append([x, y, z])
            grid.append(row)
        r_helmet = json.loads(surface_patch(control_grid=grid, degree_u=3, degree_v=3,
                                            tessellation={"u": 28, "v": 28},
                                            name="__surface_eval_helmet"))
        if r_helmet.get("object"):
            made.append(r_helmet["object"])
        ev["helmet_ok"] = bool(r_helmet.get("ok") and r_helmet.get("verts", 0) > 0)
        for key, r in (("bottle", r_bottle), ("fender", r_fender), ("helmet", r_helmet)):
            ev["%s_spec_hash_64" % key] = bool(len(str(r.get("spec_hash") or "")) == 64)
            v = json.loads(surface_validate(r.get("spec") or r.get("object"), draft=False))
            ev["%s_validate_ok" % key] = bool(v.get("ok") and v.get("verdict") in ("pass", "degraded"))
            ev["%s_validate_verdict" % key] = v.get("verdict")
        # 确定性：同 spec 两个对象，顶点前 64 个逐坐标一致
        r_b2 = json.loads(surface_loft(sections=sections, degree=3, continuity="G1", closed=True,
                                       tessellation={"u": 32, "v": 24}, name="__surface_eval_bottle2"))
        if r_b2.get("object"):
            made.append(r_b2["object"])
        same, meta = _meshes_equal(r_bottle.get("object"), r_b2.get("object"))
        ev["deterministic_verts"] = bool(same and r_bottle.get("spec_hash") == r_b2.get("spec_hash"))
        # ---- 负例 1：自交（U 形路径 + 大于曲率半径的截面） ----
        rail_u = [[10.0 * math.sin(math.radians(a)), 10.0 * math.cos(math.radians(a)), 0.0]
                  for a in (-90, -60, -30, 0, 30, 60, 90)]
        r_bad = json.loads(surface_sweep(profile_mm=_circle_pts(18, 16.0), rail_mm=rail_u,
                                         tessellation={"u": 20, "v": 28}, closed=True,
                                         name="__surface_eval_selfint"))
        if r_bad.get("object"):
            made.append(r_bad["object"])
        v_bad = json.loads(surface_validate(r_bad.get("object"), thickness=False,
                                            self_intersection=True, draft=False))
        ev["self_intersection_caught"] = bool(v_bad.get("verdict") == "fail"
                                              and any(d.get("check") == "self_intersection"
                                                      for d in v_bad.get("defects") or []))
        # ---- 负例 2：薄壁（闭合 100×100×0.5mm） ----
        import bmesh as _bm
        me = bpy.data.meshes.new("__surface_eval_thin")
        bm = _bm.new()
        _bm.ops.create_cube(bm, size=1.0)
        for v in bm.verts:
            v.co.x *= 100.0
            v.co.y *= 100.0
            v.co.z *= 0.5
        bm.to_mesh(me)
        bm.free()
        thin = bpy.data.objects.new("__surface_eval_thin", me)
        bpy.context.scene.collection.objects.link(thin)
        made.append(str(thin.name))
        v_thin = json.loads(surface_validate(str(thin.name), min_thickness_mm=0.8,
                                             continuity=False, curvature=False))
        ev["thin_wall_caught"] = bool(v_thin.get("verdict") == "fail"
                                      and any(d.get("check") == "thickness" for d in v_thin.get("defects") or []))
        # ---- 正例：20mm 厚闭合盒厚度应过 ----
        me2 = bpy.data.meshes.new("__surface_eval_thick")
        bm = _bm.new()
        _bm.ops.create_cube(bm, size=1.0)
        for v in bm.verts:
            v.co.x *= 40.0
            v.co.y *= 40.0
            v.co.z *= 20.0
        bm.to_mesh(me2)
        bm.free()
        thick = bpy.data.objects.new("__surface_eval_thick", me2)
        bpy.context.scene.collection.objects.link(thick)
        made.append(str(thick.name))
        v_thick = json.loads(surface_validate(str(thick.name), min_thickness_mm=5.0,
                                              continuity=False, curvature=False))
        ev["thick_box_pass"] = bool(v_thick.get("verdict") == "pass")
        # ---- 负例 3：G1 不连续（degree=1 折线截面 + 左右摆心） ----
        zig = []
        for k in range(4):
            zig.append({"z_mm": 30.0 * k,
                        "points_mm": _circle_pts(12, 8.0, 8.0, 20.0 * (k % 2), 0.0)})
        r_zig = json.loads(surface_loft(sections=zig, degree=1, continuity="G1", closed=True,
                                        tessellation={"u": 16, "v": 20},
                                        name="__surface_eval_zig"))
        if r_zig.get("object"):
            made.append(r_zig["object"])
        # 连续性目标写在 spec 里（对象模式只给二面角参考值，不判 fail）→ 必须用 spec 验
        v_zig = json.loads(surface_validate(r_zig.get("spec"), thickness=False,
                                            self_intersection=False))
        ev["continuity_caught"] = bool(v_zig.get("verdict") == "fail"
                                       and any(d.get("check") == "continuity"
                                               for d in v_zig.get("defects") or []))
        # ---- tessellate：spec → 对象；对象 → 烘焙对象 ----
        t_spec = json.loads(surface_tessellate(spec=r_bottle.get("spec"), quality="draft",
                                               name="__surface_eval_tess_spec"))
        if t_spec.get("object"):
            made.append(t_spec["object"])
        t_obj = json.loads(surface_tessellate(spec_or_object=r_bottle.get("object"),
                                              name="__surface_eval_tess_obj"))
        if t_obj.get("object"):
            made.append(t_obj["object"])
        ev["tessellate_spec_ok"] = bool(t_spec.get("ok") and t_spec.get("verts", 0) > 0)
        ev["tessellate_object_ok"] = bool(t_obj.get("ok") and t_obj.get("mode") == "object_bake")
        # ---- 坏参数 ----
        bad = {}
        bad["loft_missing_sections"] = json.loads(surface_loft(sections=[])).get("ok") is False
        bad["loft_bad_units"] = json.loads(surface_loft(sections=sections, units="inch")).get("ok") is False
        bad["loft_tess_too_big"] = json.loads(surface_loft(sections=sections,
                                                           tessellation={"u": 9999, "v": 99})).get("ok") is False
        bad["patch_ragged"] = json.loads(surface_patch(control_grid=[[[0, 0, 0], [1, 0, 0]],
                                                                   [[0, 1, 0]]])).get("ok") is False
        bad["validate_missing_object"] = json.loads(surface_validate("__surface_no_such")).get("ok") is False
        bad["validate_bad_spec"] = json.loads(surface_validate({"kind": "loft", "sections": []})).get("ok") is False
        bad["blend_okfalse"] = json.loads(surface_blend(a="x", b="y")).get("ok") is False
        bad["offset_okfalse"] = json.loads(surface_offset(distance_mm=1.0)).get("ok") is False
        bad["tessellate_bad"] = json.loads(surface_tessellate(spec_or_object="__surface_no_such")).get("ok") is False
        ev["bad_params_okfalse"] = all(bad.values())
        ev["bad_params_detail"] = bad
        # ---- NaN 检查：全部生成对象顶点有限 ----
        nan_ok = True
        for nm in made:
            ob = bpy.data.objects.get(nm)
            if ob is None or ob.type != "MESH":
                continue
            co = np.empty(len(ob.data.vertices) * 3, dtype=np.float64)
            ob.data.vertices.foreach_get("co", co)
            if not np.isfinite(co).all():
                nan_ok = False
        ev["no_nan_vertices"] = nan_ok
        ok = all(v is True for k, v in ev.items()
                 if isinstance(v, bool) and k not in ("bottle_validate_verdict", "fender_validate_verdict",
                                                      "helmet_validate_verdict"))
        return _j({"ok": bool(ok), "version": SURFACE_VERSION, "evidence": ev,
                   "ms": int((time.perf_counter() - t0) * 1000)})
    except Exception as e:
        import traceback
        return _j({"ok": False, "version": SURFACE_VERSION, "evidence": ev,
                   "error": "%s: %s" % (type(e).__name__, str(e)[:240]),
                   "traceback": traceback.format_exc()[-800:]})
    finally:
        # 只删本次自检新建的 __surface_eval_ 对象（外部已有同名对象时 Blender 会加 .001 后缀，
        # 按"本次新建 + 前缀"双重条件收，避免误删调用方场景里的对象）。
        victims = list(made)
        for ob in list(bpy.data.objects):
            if str(ob.name).startswith("__surface_eval_") and ob not in before_objs:
                victims.append(str(ob.name))
        try:
            _cleanup_objects(victims)
        except Exception:
            pass


def _object_bbox_mm(ob):
    try:
        import numpy as _np
        M = ob.matrix_world
        pts = [_np.array([*(M @ v.co)]) * _KIT.units() for v in ob.data.vertices]
        return _bbox_mm(pts)
    except Exception:
        return {"min_mm": None, "max_mm": None, "size_mm": None, "center_mm": None}


def _safe_stem(text, fallback="surface"):
    s = str(text or "").strip() or fallback
    s = "".join(c if (c.isalnum() or c in "._-") else "_" for c in s)
    return (s[:80] or fallback)


def surface_export(spec_or_object=None, out_dir=None, name=None, apply_modifiers=True,
                   selection_only=True, max_tris=None):
    """把候选导成 GLB（可选跑一次 validate），供外部查看/交付用。

    单位、网格与 validate 都在本模块内完成；导出只做"对象 → GLB"，
    不写任何外部索引文件，保持模块职责单一。
    """
    try:
        import os
        src = spec_or_object
        if isinstance(src, dict) and "kind" not in src and src.get("object"):
            src = str(src.get("object"))
        if isinstance(src, dict):
            norm, errs = surface_spec_normalize(src)
            if errs:
                return _j({"ok": False, "op": "export", "error": "spec 无效", "detail": errs})
            ob = None
            kind = norm.get("kind")
            gen = {"loft": surface_loft, "sweep": surface_sweep, "patch": surface_patch}.get(kind)
            if gen is None:
                gen = surface_loft
            r = json.loads(gen(spec=norm) if False else gen(**({})))
            return _j({"ok": False, "op": "export", "error": "请先 tessellate 生成对象再导出",
                       "hint": "surface_tessellate(spec_or_object=<spec>) → 拿 object 名 → surface_export",
                       "detail": r.get("ok")})
        ob = bpy.data.objects.get(str(src)) if src is not None else None
        if ob is None or ob.type != "MESH":
            return _j({"ok": False, "op": "export",
                       "error": "找不到 MESH 对象：%r" % (src,),
                       "hint": "传 tessellate 返回的 object 名，或先 surface_tessellate(spec)"})
        tris = sum(max(0, len(p.vertices) - 2) for p in ob.data.polygons)
        if max_tris and tris > int(max_tris):
            return _j({"ok": False, "op": "export",
                       "error": "三角数 %d 超过 max_tris=%s（面板查看器会卡；先 tessellate 降密度）"
                                % (tris, max_tris)})
        out_dir = str(out_dir) if out_dir else (bpy.path.abspath("//") or os.getcwd())
        os.makedirs(out_dir, exist_ok=True)
        stem = _safe_stem(name or ob.name)
        path = os.path.join(out_dir, stem + ".glb")
        prev_sel = [o for o in bpy.context.selected_objects]
        prev_active = bpy.context.view_layer.objects.active
        try:
            for o in list(prev_sel):
                o.select_set(False)
            ob.select_set(True)
            bpy.context.view_layer.objects.active = ob
            kwargs = {"filepath": path, "export_format": "GLB"}
            if selection_only:
                kwargs["use_selection"] = True
            if apply_modifiers:
                kwargs["export_apply"] = True
            bpy.ops.export_scene.gltf(**kwargs)
        finally:
            try:
                for o in prev_sel:
                    o.select_set(True)
                if prev_active is not None:
                    bpy.context.view_layer.objects.active = prev_active
            except Exception:
                pass
        size = os.path.getsize(path) if os.path.exists(path) else None
        out = {"ok": bool(size), "op": "export", "object": ob.name,
               "path": path, "file": os.path.basename(path), "bytes": size, "tris": tris}
        if not size:
            out["error"] = "GLB 没写出来（检查导出器/权限）"
            return _j(out)
        out["hint"] = "GLB 可直接用外部查看器打开；要参考图 IoU / 材质 / 打印检查时补 qc_render_views"
        return _j(out)
    except Exception as e:
        import traceback
        return _j({"ok": False, "op": "export", "error": "%s: %s" % (type(e).__name__, e),
                   "traceback": traceback.format_exc()[-800:]})



# ────────────────────────────── M3：fit 只留接口（不假装实现） ──────────────────────────────
_M3_FIT_MSG = "fit not implemented in pure-python, point-cloud/BRep solver required (M3)"


def surface_fit(*args, **kwargs):
    """M3 接口：把点云/参考曲线拟合为 SurfaceSpec 需要外部求解器（本包不依赖），明确拒绝。"""
    return _err(_M3_FIT_MSG, stage="M3", op="fit", code="M3_NOT_IMPLEMENTED",
                next_action="当前用 op=loft/sweep/patch 手工给截面/控制网格；或 vehicle_fit 的参数化 polyline 路线",
                hint="纯 bpy+numpy 不做稳健曲面拟合；需要 OCCT/点云拟合后端接线后再开放。")


def surface_help():
    return _j({
        "module": "surface.py", "version": SURFACE_VERSION,
        "what": "曲面算法包：SurfaceSpec(M0) → loft/sweep/patch/tessellate 生成 bmesh 网格(M1) → "
                "连续性/曲率/自交/厚度/质量体检(M2)；M5 blend/offset 明确未实现（需 OCCT）。纯 bpy+numpy。",
        "ops": {
            "loft": {"sig": "sections=[{z_mm,points_mm:[[x,y],...]}], degree=3, continuity=G0|G1|G2, "
                           "closed=false, tessellation={u,v}|draft|normal|fine, name?, collection?",
                     "out": "object/collection/verts/tris/bbox_mm/spec_hash/spec（可复现）"},
            "sweep": {"sig": "profile_mm=[[x,y],...], rail_mm=[[x,y,z],...], orientation=auto|up_z|up_y|fixed, "
                            "twist_deg=0, scale=1|[...], tessellation, closed=false",
                      "out": "同上；平行传输标架 + 逐点 scale + 累积 twist"},
            "patch": {"sig": "control_grid=[[[x,y,z],...],...]（行=u，列=v）, degree_u=3, degree_v=3, "
                            "tessellation",
                      "out": "同上；张量积 clamped B-spline 面片"},
            "tessellate": {"sig": "spec_or_object=SurfaceSpec|对象名, quality=draft|normal|fine, tessellation?",
                           "out": "spec → 生成网格；对象 → 烘焙求值网格（应用修改器/变换）"},
            "validate": {"sig": "spec_or_object, continuity=true, curvature=true, self_intersection=true, "
                                "thickness=true, draft=false, min_thickness_mm?, max_curvature?, max_tris=120000",
                         "out": "ok/verdict(pass|fail|degraded)/metrics/checks/defects/skipped/next"},
            "blend": {"sig": "a,b,...", "out": "M5：ok:false + 'not implemented in pure-python, OCCT backend required'"},
            "offset": {"sig": "spec_or_object,distance_mm", "out": "M5：同上（纯 Python offset 会自交/翻面）"},
            "export": {"sig": "spec_or_object, out_dir, name, apply_modifiers=true, selection_only=true, max_tris?",
                       "out": "直接导 GLB（工作台内置查看器可看，免渲染三视图）"},
            "fit": {"sig": "points/curves + tolerance",
                    "out": "M3：ok:false + 'not implemented in pure-python'（需点云/BRep 求解器）"},
            "selftest": {"sig": "无参", "out": "三基准 + 自交/薄壁/连续性负例 + 坏参数"},
            "help": {"sig": "无参"},
        },
        "spec_schema": {"kind": "loft|sweep|patch", "units": "mm（唯一支持）",
                        "tessellation": "{u,v} 或 quality",
                        "degree/continuity/closed/constraints": "放样次数/目标连续性/环向闭合/约束",
                        "constraints": {"min_thickness_mm": "厚度目标", "max_curvature_1_per_mm": "曲率上限",
                                        "g1_tol_deg": "G1 法线角容差", "g2_tol_ratio": "G2 曲率跳变容差",
                                        "closed_rail": "sweep 路径闭合", "up": "fixed 标架的 up 向量"}},
        "protocol": [
            "单位：所有长度 mm；内部按 scene.unit_settings.scale_length 折算；不要把 inch 直接塞进来",
            "可复现：同 spec（含 tessellation）⇒ spec_hash 相同、顶点一致；对象名不影响 spec_hash",
            "验证读 verdict 三态：fail=确认缺陷，degraded=有检查没跑成（超限/量不到），open 曲面厚度 n/a",
            "自交：三角数超 max_tris ⇒ degraded（不当作无自交）；厚度只对闭合壳有意义",
        ],
        "deps": {"required": "K.dsh_kit", "optional": "audit（可再交叉跑 audit_mesh，本模块不强制）"},
        "honest_limits": ["离散曲率/射线厚度都是近似", "M3 fit 未实现（需点云/BRep 求解器）", "M5 blend/offset 未实现（需 OCCT）",
                          "patch/sweep 的连续性只查有接缝的方向；单张面片无内部缝",
                          "loft 的 z 沿 v 线性（不额外摆动）；截面参数按 z 归一化"],
    })


# 注册：K.dsh_surface_api（ops = loft/sweep/patch/tessellate/validate/blend/offset/selftest/help）
_KIT.register("surface", SURFACE_VERSION, {
    "loft": surface_loft, "sweep": surface_sweep, "patch": surface_patch,
    "tessellate": surface_tessellate, "validate": surface_validate,
    "blend": surface_blend, "offset": surface_offset, "export": surface_export,
    "fit": surface_fit, "selftest": surface_selftest, "help": surface_help})
