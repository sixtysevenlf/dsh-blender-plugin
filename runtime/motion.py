# -*- coding: utf-8 -*-
"""DSH 运动/铰接层（motion v1，Procedura 融合批次 2）—— 把装配关系变成「可验证的关节 + URDF/USDA 导出
+ 用运动学扫掠证明它真的能动」。

为什么有它
    上游 SpatiaOS/Procedura（MIT）用 NVIDIA Isaac 做铰接验证：JointSpec → URDF/USDA → 物理仿真。
    本机没有 Isaac（也不打算引），于是把最后一步换成 Blender 侧的两件真事：
      ① **实测轴**（geometry.ts 的 analyzeRotationalSymmetry / analyzeContactRegion 思路）——
         采样上限 3000、评分阈值 0.55、退化（球/近立方）判据 0.08 全部照抄；
      ② **驱动扫掠 + BVH 干涉自检**——把 child 组绕关节轴（或沿轴）驱动 N 个相位，
         每相位对「非本关节链上」的对象求干涉对与最小间距；全程零干涉且确实位移 ⇒ supported。
    关节树的 URDF/USDA 导出（单树合成、球/6DOF/距离关节降级、SI 单位、bbox 近似惯量）也在这里落地。

边界（诚实说明，别当能力用）
    * 扫掠是**运动学**验证，不是物理验证：没有质量/摩擦/驱动动力学，只回答"按这个轴动起来会不会撞"。
    * 最小间距是**顶点+面心双向采样到对方表面**的近似（对上界偏大），干涉判定则用 BVH 三角面相交（精确）。
      穿透深度用"最近面法线朝向"估计，是近似值。
    * 轴推断只对"可辨识"几何有效：圆柱/轮/销（旋转对称）或细长接触带（父↔子接触区）。
      门板式的薄平板绕铰链轴**没有**旋转对称 → 会如实报 unresolved，绝不硬凑一个轴。
    * USDA 是**最小可用 ASCII**：结构可读（Xform / Mesh / 关节自定义属性），不是完整 UsdPhysics schema。

状态放持久内核 K.dsh_motion（跨调用保留）；API 挂 K.dsh_motion_api。
入口：
    blender_rt_headless(engine="none", preload="motion", script="print(K.dsh_motion_api['selftest']())")
    K.dsh_motion_api["dispatch"]("joint", {"jid": "hinge", "parent": "Base", "child": "Door"})
    K.dsh_motion_api["dispatch"]("measure", {"jid": "hinge", "phases": 8, "sweep_deg": 60})

硬规则：空产出必须 ok=false；判定三态（supported / refuted / unresolved，证据不足就 unresolved）；
        数值带单位；扫掠结束**必须**把场景 transform 完整还原（逐对象对拍并报 mismatch）。
"""
import json
import math
import os
import shutil
import time
import traceback
import xml.etree.ElementTree as ET

import bpy
import numpy as np
from mathutils import Matrix, Vector
from mathutils.bvhtree import BVHTree

MOTION_VERSION = 1

# ---- 轴推断（照抄 src/motion/geometry.ts 的口径）----
SYMMETRY_SAMPLE_CAP = 3000       # 每对象参与评分的采样点上限
SYMMETRY_MIN_SCORE = 0.55        # 低于它 → 该轴判定为 unresolved
DEGENERATE_GAP = 0.08            # 次优轴与最优轴之差 ≤ 它 → 退化（球/近立方）
SYMMETRY_CELL_FRAC = 0.02        # 空间哈希格子边长 = bbox 对角线 × 它
SYMMETRY_CAP_FRAC = 0.05         # 距离归一化上限 = bbox 对角线 × 它
CONTACT_SAMPLE_CAP = 4000        # 接触区采样上限
CONTACT_MIN_POINTS = 8           # 接触区最少点
ELONGATION_THRESHOLD = 2.0       # 接触带长宽比阈值（小于它不算铰链证据）
CONSENSUS_ANGLE_DEG = 10.0       # 多对象候选同向判据

# ---- 导出 ----
BBOX_FILL_FACTOR = 0.4           # bbox 体积 → 实体体积的填充系数（照抄上游）
MIN_INERTIA = 1e-6               # 惯量下限（kg·m²）
DEFAULT_DENSITY = 1000.0         # kg/m³
DEFAULT_EFFORT = 1000.0          # N·m / N
DEFAULT_VELOCITY = 100.0         # rad/s（角）/ m/s（线）
MIN_LINK_MESH_DIAG = 1e-12

# ---- 关节类型 ----
AXIS_KINDS = ("revolute", "continuous", "prismatic")            # 必须有轴
FIXED_KINDS = ("fixed",)
PASSIVE_KINDS = ("spherical", "d6", "distance", "planar", "floating",
                 "gear", "rack_and_pinion")                     # URDF 表达不了 → 降级 fixed
ALL_KINDS = AXIS_KINDS + FIXED_KINDS + PASSIVE_KINDS
VERDICTS = ("supported", "refuted", "unresolved")
MAX_DIST_SAMPLES = 400           # 最小间距探针的单侧采样上限
MAX_OVERLAP_REPORT = 8           # 每对干涉最多回报多少条三角对


def _j(o):
    return json.dumps(o, ensure_ascii=False, default=str)


def _kernel():
    import sys
    return sys.modules.get("dsh_rt_kernel")


def _store():
    K = _kernel()
    if K is None:
        raise RuntimeError("需要持久内核 K（走 blender_rt_* 通道；无头里用 preload='motion'）")
    if not hasattr(K, "dsh_motion"):
        K.dsh_motion = {"joints": {}, "exports": [], "log": [], "seq": 0}
    return K.dsh_motion


def _now():
    return time.strftime("%Y-%m-%d %H:%M:%S")


def _log(action, detail):
    try:
        st = _store()
    except Exception:
        return
    st["log"].append({"t": _now(), "action": action, "detail": detail})
    del st["log"][:-200]


def _out_dir():
    K = _kernel()
    d = getattr(K, "out_dir", None) if K is not None else None
    return d or os.path.join(os.path.expanduser("~"), "dsh_motion")


def _win(path):
    """WSL → Windows 侧路径（Blender 是 Windows 进程）。K.win_path 可用时用它。"""
    K = _kernel()
    if K is not None and hasattr(K, "win_path"):
        try:
            return K.win_path(path)
        except Exception:
            pass
    s = str(path)
    if s.startswith("/mnt/") and len(s) > 6:
        return s[5].upper() + ":" + chr(92) + s[7:].replace("/", chr(92))
    return s


def _scene_mpu():
    """场景单位 → 米（scale_length；缺省 1.0 表示 1 单位 = 1 m）。数值一律经它换算，不硬套。"""
    try:
        v = float(bpy.context.scene.unit_settings.scale_length)
        return v if v > 0 else 1.0
    except Exception:
        return 1.0


def _mpu_arg(meters_per_unit):
    """meters_per_unit 参数：数字直接用；"auto"/"scene" 读场景 scale_length；None → 0.001（上游 stage 约定）。"""
    if isinstance(meters_per_unit, str):
        s = meters_per_unit.strip().lower()
        if s in ("auto", "scene"):
            return _scene_mpu()
        try:
            v = float(s)
        except Exception:
            return 0.001
        return v if v > 0 else 0.001
    try:
        v = float(meters_per_unit) if meters_per_unit is not None else 0.001
    except Exception:
        return 0.001
    return v if v > 0 else 0.001


def _units_warnings(mpu, model_diag_m, warnings):
    """单位体检：换算后整个模型不到 1 cm ⇒ 场景大概率是米制（0.001 这个默认是给 mm 场景的）。"""
    suspect = bool(model_diag_m and model_diag_m > 0 and model_diag_m < 0.01)
    if suspect:
        warnings.append("单位可疑：按 meters_per_unit=%g 换算后整个模型对角线只有 %.4g m（不到 1 cm）—— "
                        "若场景是米制（Blender 默认 scale_length=1.0），请传 meters_per_unit=\"scene\" 或 1.0；"
                        "当前产物里的质量/惯量按该换算得出，可能差若干个数量级" % (mpu, model_diag_m))
    return suspect


def _r(x, n=6):
    try:
        v = float(x)
        if v != v or v in (float("inf"), float("-inf")):
            return None
        return round(v, n)
    except Exception:
        return x


def _rl(v, n=6):
    try:
        return [_r(float(x), n) for x in v]
    except Exception:
        return v


def _names(x):
    """对象名参数：str / list / {'objects': [...]} 都收。"""
    if x is None:
        return []
    if isinstance(x, str):
        return [x]
    if isinstance(x, dict):
        return _names(x.get("objects") or x.get("names") or x.get("list") or [])
    try:
        return [str(i) for i in x]
    except Exception:
        return [str(x)]


def _objs(names, mesh_only=True):
    out, missing, non_mesh = [], [], []
    for n in _names(names):
        ob = bpy.data.objects.get(n)
        if ob is None:
            missing.append(n)
            continue
        if mesh_only and ob.type != "MESH":
            non_mesh.append(n)
            continue
        out.append(ob)
    return out, missing, non_mesh


def _link_key(names):
    return tuple(sorted(str(n) for n in _names(names)))


def _safe_name(s, fallback="link"):
    t = "".join((c if (c.isalnum() or c in "_-.") else "_") for c in str(s))
    t = t.strip("_")
    return t or fallback


def _link_name(names):
    ns = _names(names)
    if not ns:
        return "link"
    if len(ns) == 1:
        return _safe_name(ns[0])
    return _safe_name("_".join(ns))


def _esc(s):
    return (str(s).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
            .replace('"', "&quot;"))


def _fnum(x):
    """URDF/USDA 里的数：%.9g（保留小量级，必要时走指数写法；URDF/USD 都吃得下 1e-11）。

    注意：早先用 "%.9f" 会把 mm 级模型的 mass/inertia（如 8.6e-11）直接写成 "0" —— 静默错误。
    """
    try:
        v = float(x)
    except Exception:
        return "0"
    if v != v or v in (float("inf"), float("-inf")):
        return "0"
    s = "%.9g" % v
    if "e" in s or "E" in s:
        m, e = s.lower().split("e")
        if m.endswith("."):
            m = m[:-1]
        return "%se%d" % (m, int(e))
    if "." in s:
        s = s.rstrip("0").rstrip(".")
    return s if s not in ("", "-0") else "0"


# ============================================================ 几何：世界三角面 / bbox / 采样

def _mesh_tris_world(ob, dg=None):
    """对象（求值后）的三角面 → 世界坐标。返回 (V(N,3) float64, F(M,3) int32)。"""
    obe = ob.evaluated_get(dg) if dg is not None else ob
    me = obe.to_mesh()
    try:
        me.calc_loop_triangles()
        nv = len(me.vertices)
        V = np.empty((nv, 3), dtype=np.float64)
        if nv:
            me.vertices.foreach_get("co", V.ravel())
        nt = len(me.loop_triangles)
        F = np.empty((nt, 3), dtype=np.int32)
        if nt:
            me.loop_triangles.foreach_get("vertices", F.ravel())
    finally:
        obe.to_mesh_clear()
    M = np.array(ob.matrix_world, dtype=np.float64)
    if len(V):
        V = V @ M[:3, :3].T + M[:3, 3]
    return V, F


def _group_tris(objs, dg=None):
    """一组对象 → 合并的世界三角面 (V, F, parts)，parts = [{name, v0, v1}] 便于按对象归因。"""
    Vs, Fs, parts, off = [], [], [], 0
    for ob in objs:
        V, F = _mesh_tris_world(ob, dg)
        if len(V) == 0:
            continue
        Vs.append(V)
        Fs.append(F + off if len(F) else F)
        parts.append({"name": ob.name, "v0": off, "v1": off + len(V),
                      "verts": int(len(V)), "tris": int(len(F))})
        off += len(V)
    if not Vs:
        return (np.zeros((0, 3)), np.zeros((0, 3), dtype=np.int32), [])
    return (np.concatenate(Vs, axis=0), np.concatenate(Fs, axis=0) if Fs else np.zeros((0, 3), np.int32), parts)


def _bbox(V):
    if V is None or len(V) == 0:
        return None, None, 0.0
    mn = V.min(axis=0)
    mx = V.max(axis=0)
    return mn, mx, float(np.linalg.norm(mx - mn))


def _sample_points(V, F, cap):
    """确定性采样：顶点（均匀步长）+ 三角面心（补形状信息，避免只取顶点时的空洞）。"""
    if len(V) == 0:
        return np.zeros((0, 3))
    step = max(1, int(math.ceil(len(V) / float(max(1, cap)))))
    P = V[::step]
    if len(F):
        cents = V[F].mean(axis=1)
        step2 = max(1, int(math.ceil(len(cents) / float(max(1, cap)))))
        P = np.concatenate([P, cents[::step2]], axis=0)
    return P


def _dense_samples(V, F, cap):
    """面积加权密采样（确定性重心网格）：大面按面积分到更多点 —— 接触带分析靠它才不被稀疏顶点骗过。"""
    if len(V) == 0 or len(F) == 0:
        return _sample_points(V, F, cap)
    tri = V[F]
    areas = 0.5 * np.linalg.norm(np.cross(tri[:, 1] - tri[:, 0], tri[:, 2] - tri[:, 0]), axis=1)
    tot = float(areas.sum())
    if tot <= 1e-18:
        return _sample_points(V, F, cap)
    cap = max(8, int(cap))
    out, left = [], cap
    for idx in np.argsort(-areas, kind="stable"):     # 确定性：面积降序
        if left <= 0:
            break
        n = int(max(1, min(left, round(cap * float(areas[idx]) / tot))))
        m = int(max(1, math.ceil(math.sqrt(2.0 * n))))
        a = V[F[idx]]
        cnt = 0
        for i in range(m):
            for j in range(m - i):
                u, v = i / float(m), j / float(m)
                out.append(a[0] + u * (a[1] - a[0]) + v * (a[2] - a[0]))
                cnt += 1
                if cnt >= n:
                    break
            if cnt >= n:
                break
        left -= cnt
    if not out:
        return _sample_points(V, F, cap)
    return np.asarray(out, dtype=np.float64)


def _rot_pts(P, origin, axis, cos_t, sin_t):
    """Rodrigues：把点集绕 (origin, axis) 旋转。"""
    if len(P) == 0:
        return P
    k = np.asarray(axis, dtype=np.float64)
    n = float(np.linalg.norm(k))
    if n < 1e-30:
        return P.copy()
    k = k / n
    v = P - origin
    dot = v.dot(k)
    cross = np.cross(np.broadcast_to(k, v.shape), v)
    return origin + v * cos_t + cross * sin_t + np.outer(dot, k) * (1.0 - cos_t)


def _nearest_dists(points, queries, cell, cap):
    """queries 里每点到 points 的最近距离（27 邻格哈希）；超出邻域 → clamp 到 cap（保守）。"""
    if len(queries) == 0:
        return np.zeros((0,))
    if len(points) == 0 or cell <= 1e-30:
        return np.full((len(queries),), cap)
    grid = {}
    for i, p in enumerate(points):
        key = (int(math.floor(p[0] / cell)), int(math.floor(p[1] / cell)), int(math.floor(p[2] / cell)))
        grid.setdefault(key, []).append(i)
    Pp = points
    out = np.full((len(queries),), cap, dtype=np.float64)
    for qi in range(len(queries)):
        q = queries[qi]
        cx = int(math.floor(q[0] / cell)); cy = int(math.floor(q[1] / cell)); cz = int(math.floor(q[2] / cell))
        best = cap
        for dx in (-1, 0, 1):
            for dy in (-1, 0, 1):
                for dz in (-1, 0, 1):
                    lst = grid.get((cx + dx, cy + dy, cz + dz))
                    if not lst:
                        continue
                    idx = np.asarray(lst, dtype=np.int64)
                    d = np.sqrt(((Pp[idx] - q) ** 2).sum(axis=1)).min()
                    if d < best:
                        best = float(d)
        out[qi] = best
    return out


def _canon_dir(d):
    """确定性符号：最大分量取正。"""
    a = np.asarray(d, dtype=np.float64)
    n = float(np.linalg.norm(a))
    if n < 1e-30:
        return np.array([0.0, 0.0, 1.0])
    a = a / n
    ix = int(np.argmax(np.abs(a)))
    return -a if a[ix] < 0 else a


def _snap_axis(d):
    if abs(d[0]) >= 0.95:
        return "X"
    if abs(d[1]) >= 0.95:
        return "Y"
    if abs(d[2]) >= 0.95:
        return "Z"
    return None


def _angle_deg(a, b):
    a = np.asarray(a, dtype=np.float64); b = np.asarray(b, dtype=np.float64)
    na, nb = float(np.linalg.norm(a)), float(np.linalg.norm(b))
    if na < 1e-30 or nb < 1e-30:
        return None
    c = float(np.clip(abs(np.dot(a, b)) / (na * nb), -1.0, 1.0))
    return math.degrees(math.acos(c))


def _dist_point_line(p, origin, axis):
    v = np.asarray(p, dtype=np.float64) - np.asarray(origin, dtype=np.float64)
    k = np.asarray(axis, dtype=np.float64)
    n = float(np.linalg.norm(k))
    if n < 1e-30:
        return None
    k = k / n
    return float(np.linalg.norm(v - k * float(np.dot(v, k))))


# ============================================================ 轴推断（旋转对称 / 接触带）

def _symmetry_evidence(label, V, F, sample_cap=SYMMETRY_SAMPLE_CAP, min_score=SYMMETRY_MIN_SCORE):
    """旋转对称证据：面积加权质心 + 惯性主轴；每轴按 90°/180° 旋转后的最近邻距离评分。"""
    if len(V) < 4 or len(F) == 0:
        return None
    mn, mx, diag = _bbox(V)
    if not (diag > 1e-12):
        return None
    P = _sample_points(V, F, sample_cap)
    if len(P) < 4:
        return None
    tri = V[F]
    e1 = tri[:, 1] - tri[:, 0]
    e2 = tri[:, 2] - tri[:, 0]
    areas = 0.5 * np.linalg.norm(np.cross(e1, e2), axis=1)
    tot = float(areas.sum())
    cents = tri.mean(axis=1)
    if tot <= 1e-18:
        w = np.full(len(cents), 1.0 / max(1, len(cents)))
    else:
        w = areas / tot
    centroid = (cents * w[:, None]).sum(axis=0)
    d = cents - centroid
    cov = (w[:, None, None] * (d[:, :, None] * d[:, None, :])).sum(axis=0)
    try:
        vals, vecs = np.linalg.eigh(cov)
    except Exception:
        return None
    axes = [vecs[:, 2], vecs[:, 1], vecs[:, 0]]     # 特征值降序
    cell = SYMMETRY_CELL_FRAC * diag
    cap = SYMMETRY_CAP_FRAC * diag
    scored = []
    for ax in axes:
        s = 0.0
        cnt = 0
        for (c, sn) in ((0.0, 1.0), (-1.0, 0.0)):    # 90° / 180°
            Q = _rot_pts(P, centroid, ax, c, sn)
            dd = np.minimum(_nearest_dists(P, Q, cell, cap), cap)
            s += float(dd.sum())
            cnt += len(Q)
        score = 0.0 if cnt == 0 else max(0.0, min(1.0, 1.0 - (s / cnt) / cap))
        scored.append((score, ax))
    scored.sort(key=lambda t: -t[0])
    best_score, best_axis = scored[0]
    second_score = scored[1][0] if len(scored) > 1 else 0.0
    axis_dir = _canon_dir(best_axis)
    radii = np.array([_dist_point_line(p, centroid, axis_dir) for p in P], dtype=np.float64)
    mean_radius = float(radii.mean()) if len(radii) else 0.0
    radius_std = float(radii.std()) if len(radii) else 0.0
    degenerate = (best_score - second_score) <= DEGENERATE_GAP
    ratio = (radius_std / mean_radius) if mean_radius > 1e-12 else float("inf")
    if degenerate:
        confidence = "low"
    elif best_score >= 0.85 and ratio < 0.35:
        confidence = "high"
    elif best_score >= 0.7:
        confidence = "medium"
    else:
        confidence = "low"
    return {
        "label": str(label),
        "samples": int(len(P)),
        "tris": int(len(F)),
        "score": _r(best_score, 6),
        "second_score": _r(second_score, 6),
        "degenerate": bool(degenerate),
        "passed": bool(best_score >= min_score and not degenerate),
        "axis": _rl(axis_dir, 9),
        "anchor": _rl(centroid, 9),
        "snapped": _snap_axis(axis_dir),
        "mean_radius": _r(mean_radius),
        "radius_std": _r(radius_std),
        "radius_ratio": (None if ratio == float("inf") else _r(ratio)),
        "confidence": confidence,
        "diagonal": _r(diag),
        "cell": _r(cell), "cap_dist": _r(cap),
    }


def _contact_evidence(parent_objs, child_objs, dg, max_distance=None, sample_cap=CONTACT_SAMPLE_CAP):
    """父↔子接触带证据：细长（elongation ≥ 2）且方向明确 → 铰链轴候选。"""
    pv, pf, _ = _group_tris(parent_objs, dg)
    cv, cf, _ = _group_tris(child_objs, dg)
    if len(pv) == 0 or len(cv) == 0:
        return {"available": False, "reason": "父/子组没有几何"}
    mn, mx, diag = _bbox(np.concatenate([pv, cv], axis=0))
    if not (diag > 1e-12):
        return {"available": False, "reason": "装配对角线为 0"}
    md = float(max_distance) if max_distance else 0.01 * diag
    A = _dense_samples(pv, pf, sample_cap)
    B = _dense_samples(cv, cf, sample_cap)
    cell = max(md, 1e-9)

    def _close_to(pts, other):
        grid = {}
        for i, p in enumerate(other):
            grid.setdefault((int(math.floor(p[0] / cell)), int(math.floor(p[1] / cell)),
                             int(math.floor(p[2] / cell))), []).append(i)
        hit_pts = []
        for p in pts:
            cx = int(math.floor(p[0] / cell)); cy = int(math.floor(p[1] / cell)); cz = int(math.floor(p[2] / cell))
            hit = False
            for dx in (-1, 0, 1):
                for dy in (-1, 0, 1):
                    for dz in (-1, 0, 1):
                        lst = grid.get((cx + dx, cy + dy, cz + dz))
                        if not lst:
                            continue
                        idx = np.asarray(lst, dtype=np.int64)
                        if float(np.sqrt(((other[idx] - p) ** 2).sum(axis=1)).min()) <= md:
                            hit = True
                            break
                    if hit:
                        break
                if hit:
                    break
            if hit:
                hit_pts.append(p)
        return hit_pts

    close = _close_to(A, B) + _close_to(B, A)      # 双向收：接触带两侧的点都算
    if len(close) < CONTACT_MIN_POINTS:
        return {"available": False, "reason": "接触点不足（%d < %d），父↔子在 max_distance=%.6g 内几乎不接触"
                % (len(close), CONTACT_MIN_POINTS, md),
                "samples_a": int(len(A)), "samples_b": int(len(B)), "max_distance": _r(md),
                "close_points": int(len(close))}
    C = np.asarray(close, dtype=np.float64)
    cen = C.mean(axis=0)
    dc = C - cen
    cov = (dc[:, :, None] * dc[:, None, :]).sum(axis=0) / max(1, len(C))
    try:
        vals, vecs = np.linalg.eigh(cov)
    except Exception:
        return {"available": False, "reason": "接触带协方差分解失败"}
    order = [2, 1, 0]
    exts = np.sqrt(np.maximum(vals, 0.0)) * 2.0
    e_sorted = sorted([float(exts[i]) for i in order], reverse=True)
    denom = max(e_sorted[-1], 1e-12)
    elong = e_sorted[0] / denom
    axis_dir = _canon_dir(vecs[:, order[0]])
    passed = bool(elong >= ELONGATION_THRESHOLD)
    return {
        "available": True,
        "samples_a": int(len(A)), "samples_b": int(len(B)),
        "close_points": int(len(C)),
        "max_distance": _r(md),
        "anchor": _rl(cen, 9),
        "axis": _rl(axis_dir, 9),
        "snapped": _snap_axis(axis_dir),
        "elongation": _r(elong, 4),
        "min_elongation": ELONGATION_THRESHOLD,
        "extent": _rl(e_sorted, 6),
        "passed": passed,
        "reason": ("接触带细长（%.3g ≥ %.2g）→ 可作为铰链轴证据" % (elong, ELONGATION_THRESHOLD)) if passed
                  else ("接触带不细长（%.3g < %.2g）→ 不足以定轴" % (elong, ELONGATION_THRESHOLD)),
    }


def _consensus(cands):
    """多候选共识：取分最高者，收集同向（≤ CONSENSUS_ANGLE_DEG）的通过项。"""
    ok = [c for c in cands if c and c.get("passed")]
    if not ok:
        return None
    ok.sort(key=lambda c: (-float(c.get("score") or 0.0), str(c.get("label"))))
    best = ok[0]
    sup, spread = [], 0.0
    for c in ok:
        a = _angle_deg(c["axis"], best["axis"])
        if a is not None and a <= CONSENSUS_ANGLE_DEG:
            sup.append(c["label"])
            spread = max(spread, a)
    return {
        "axis": best["axis"], "anchor": best["anchor"], "score": best["score"],
        "snapped": best.get("snapped"), "supporters": sup, "support_count": len(sup),
        "angle_tol_deg": CONSENSUS_ANGLE_DEG, "max_spread_deg": _r(spread, 4),
        "source": best.get("source") or best.get("label"),
    }


def motion_infer_axis(jid=None, objects=None, parent=None, sample_cap=SYMMETRY_SAMPLE_CAP,
                      min_score=SYMMETRY_MIN_SCORE, max_distance=None, note=""):
    """从几何实测轴与锚点，并给出**证据**（采样点数 / 评分 / 候选 / 退化 / 接触带）。

    证据 = ① 逐对象的旋转对称轴（圆柱/销/轮） ② 合并组的对称轴 ③ 父↔子接触带方向。
    verdict：有通过证据 → supported；有假设轴且与实测明确不符 → refuted；证据不足 → unresolved。
    """
    t0 = time.perf_counter()
    st = _store()
    jrec = None
    if jid is not None:
        jrec = st["joints"].get(str(jid))
        if jrec is None:
            return _j({"ok": False, "error": "关节未登记: %s" % jid, "have": sorted(st["joints"])})
        child_names = jrec["child"]
        parent_names = jrec["parent"] if parent is None else parent
    else:
        child_names = _names(objects)
        parent_names = _names(parent)
    if not child_names:
        return _j({"ok": False, "error": "没有输入：给 jid（用已登记关节）或 objects（要定轴的对象名）"})

    child, miss_c, nonmesh_c = _objs(child_names)
    par, miss_p, nonmesh_p = _objs(parent_names)
    if not child:
        return _j({"ok": False, "error": "child 组没有可用 mesh 对象", "child": _names(child_names),
                   "missing": miss_c, "non_mesh": nonmesh_c})

    dg = bpy.context.evaluated_depsgraph_get()
    per_obj, total_samples = [], 0
    VV, FF, _ = _group_tris(child, dg)
    for ob in child:
        v, f = _mesh_tris_world(ob, dg)
        if len(v) == 0:
            continue
        ev = _symmetry_evidence(ob.name, v, f, sample_cap=sample_cap, min_score=min_score)
        if ev is None:
            per_obj.append({"label": ob.name, "passed": False, "reason": "几何不足（顶点<4 或无三角面）",
                            "verts": int(len(v)), "tris": int(len(f))})
            continue
        ev["source"] = "rotational_symmetry"
        per_obj.append(ev)
        total_samples += ev["samples"]

    group_ev = None
    if len(VV) and len(child) > 1:
        group_ev = _symmetry_evidence("group:%s" % _link_name(child_names), VV, FF,
                                      sample_cap=sample_cap, min_score=min_score)
        if group_ev is not None:
            group_ev["source"] = "group_symmetry"

    contact_ev = None
    if par:
        try:
            contact_ev = _contact_evidence(par, child, dg, max_distance=max_distance)
        except Exception as e:
            contact_ev = {"available": False, "reason": "%s: %s" % (type(e).__name__, str(e)[:160])}

    cands = [c for c in per_obj if c.get("passed")]
    if group_ev is not None:
        cands.append(group_ev)
    cons = _consensus(cands)

    contact_ok = bool(contact_ev and contact_ev.get("available") and contact_ev.get("passed"))
    contact_axis = None
    if contact_ok:
        contact_axis = {"label": "contact:%s" % _link_name(child_names), "score": None,
                        "axis": contact_ev["axis"], "anchor": contact_ev["anchor"],
                        "snapped": contact_ev.get("snapped"), "source": "contact_region"}

    # 两条独立证据（旋转对称 / 接触带）都通过时：一致 → 更强；不一致 → 如实报矛盾，不硬选
    conflict = None
    if cons is not None and contact_axis is not None:
        a = _angle_deg(cons["axis"], contact_axis["axis"])
        if a is not None and a > CONSENSUS_ANGLE_DEG:
            conflict = {"symmetry_axis": cons["axis"], "symmetry_score": cons["score"],
                        "symmetry_supporters": cons["supporters"],
                        "contact_axis": contact_axis["axis"],
                        "contact_elongation": contact_ev.get("elongation"),
                        "angle_deg": _r(a, 4), "tol_deg": CONSENSUS_ANGLE_DEG,
                        "note": "两条证据互相矛盾（方形板绕自身法线也旋转对称，而铰链线在接触带上）"}

    hypothesis = None
    hyp_axis = None
    if jrec is not None and jrec.get("axis"):
        hyp_axis = jrec["axis"]
    verdict, reason, chosen = "unresolved", "", None
    if conflict:
        chosen = None
    elif cons is not None:
        # 两证据一致时锚点取接触带（关节界面更贴近关节帧语义）；只有对称证据时取对称轴锚点
        anchor = (contact_axis["anchor"] if contact_axis is not None else cons["anchor"])
        chosen = {"axis": cons["axis"], "anchor": anchor, "source": cons["source"],
                  "score": cons["score"], "passers": cons["supporters"],
                  "anchor_source": ("contact_region" if contact_axis is not None else cons["source"])}
    elif contact_axis is not None:
        chosen = {"axis": contact_axis["axis"], "anchor": contact_axis["anchor"],
                  "source": "contact_region", "score": None,
                  "passers": ["contact:%s" % _link_name(child_names)],
                  "anchor_source": "contact_region"}

    if hyp_axis is not None:
        # 有假设轴 → 判"支持 / 反驳 / 证据不足"
        cand_all = [c for c in per_obj if c.get("axis")] + ([group_ev] if group_ev else [])
        near = [(c["label"], _angle_deg(c["axis"], hyp_axis), c.get("score"), c.get("passed"))
                for c in cand_all if c.get("axis")]
        near = [n for n in near if n[1] is not None]
        near.sort(key=lambda n: n[1])
        best_near = near[0] if near else None
        hyp_evidence = {"candidates": [{"label": a, "angle_deg": _r(b, 4), "score": c, "passed": d}
                                       for (a, b, c, d) in near[:6]]}
        contact_ok = bool(contact_ev and contact_ev.get("available") and contact_ev.get("passed"))
        contact_angle = _angle_deg(contact_ev["axis"], hyp_axis) if contact_ok else None
        if contact_angle is not None:
            hyp_evidence["contact_angle_deg"] = _r(contact_angle, 4)
        sym_agree = (best_near is not None and best_near[3] and best_near[1] <= CONSENSUS_ANGLE_DEG)
        con_agree = (contact_angle is not None and contact_angle <= CONSENSUS_ANGLE_DEG)
        passers = [n for n in near if n[3]]
        near_miss = [n for n in near if (not n[3]) and n[1] is not None and n[1] <= CONSENSUS_ANGLE_DEG]
        disagreeing = [n for n in passers if n[1] > CONSENSUS_ANGLE_DEG]
        if sym_agree or con_agree:
            verdict = "supported"
            reason = "假设轴与实测证据一致（%s）" % (
                "旋转对称候选 %s 夹角 %.2f°" % (best_near[0], best_near[1]) if sym_agree
                else "接触带夹角 %.2f°" % contact_angle)
            if conflict:
                reason += "；另有矛盾证据需注意（%s）" % conflict["note"]
            chosen = {"axis": _rl(np.asarray(hyp_axis, dtype=np.float64), 9),
                      "anchor": jrec.get("anchor") or (best_near and None) or None,
                      "source": "hypothesis_confirmed", "score": (best_near[2] if best_near else None),
                      "passers": [best_near[0]] if best_near else []}
            hp = _canon_dir(np.asarray(hyp_axis, dtype=np.float64))
            anchor_line = None
            if con_agree and contact_ev.get("anchor"):
                anchor_line = contact_ev["anchor"]
            elif best_near is not None:
                for c in cand_all:
                    if c.get("label") == best_near[0]:
                        anchor_line = c.get("anchor")
            chosen["anchor"] = anchor_line or jrec.get("anchor") or _rl(hp, 9)
            chosen["anchor_source"] = ("contact_region" if con_agree and contact_ev.get("anchor")
                                       else "rotational_symmetry")
        elif disagreeing and not near_miss:
            # 有可辨识候选，且假设方向附近什么都没有 → 明确反驳
            d = min(disagreeing, key=lambda n: n[1])
            verdict = "refuted"
            reason = ("假设轴与实测轴不符：可辨识的候选是 %s（夹角 %.1f° > %.1f°），"
                      "且假设方向附近没有任何候选（夹角 ≤ %.1f° 内为空）"
                      % (d[0], d[1], CONSENSUS_ANGLE_DEG, CONSENSUS_ANGLE_DEG))
            chosen = None
        elif disagreeing and near_miss:
            d = min(disagreeing, key=lambda n: n[1])
            nm = near_miss[0]
            verdict = "unresolved"
            reason = ("证据冲突：假设方向附近有候选 %s（夹角 %.1f°）但评分 %.3f < %.2f（不可辨识），"
                      "同时另有可辨识候选 %s 与假设夹角 %.1f° → 不能判定，也不构成反驳"
                      % (nm[0], nm[1], float(nm[2] or 0.0), min_score, d[0], d[1]))
            chosen = None
        elif contact_ok and contact_angle is not None and contact_angle > CONSENSUS_ANGLE_DEG:
            verdict = "unresolved"
            reason = ("接触带方向与假设不符（夹角 %.1f°），但没有可用的旋转对称证据 → "
                      "单靠接触带（判据较弱）不足以反驳假设" % contact_angle)
            chosen = None
        elif near_miss:
            nm = near_miss[0]
            verdict = "unresolved"
            reason = ("候选与假设同向（%.1f°）但评分 %.3f < %.2f（或退化）→ 证据不足，不能判定"
                      % (nm[1], float(nm[2] or 0.0), min_score))
        else:
            verdict = "unresolved"
            reason = "没有任何通过阈值的候选轴，也没有细长接触带 → 证据不足（缺：可辨识的旋转对称几何或父↔子接触带）"
        hypothesis = {"axis": _rl(hyp_axis, 9), "anchor": _rl(jrec.get("anchor"), 9) if jrec.get("anchor") else None,
                      "evidence": hyp_evidence}
    else:
        if conflict:
            verdict = "unresolved"
            reason = ("两条独立证据给出不同轴：旋转对称 %s（score %.3f）vs 接触带 %s（elongation %.3g），"
                      "夹角 %.1f° > %.1f° → 缺判别依据，请显式给 axis"
                      % (_rl(conflict["symmetry_axis"], 4), float(conflict["symmetry_score"] or 0.0),
                         _rl(conflict["contact_axis"], 4), float(conflict["contact_elongation"] or 0.0),
                         conflict["angle_deg"], CONSENSUS_ANGLE_DEG))
        elif chosen is not None:
            verdict = "supported"
            if chosen["source"] == "contact_region":
                reason = "无旋转对称证据，但父↔子接触带细长（elongation %.3g）且方向明确 → 取接触带方向为轴" % \
                         float(contact_ev.get("elongation") or 0.0)
            elif contact_axis is not None:
                reason = ("两条证据一致：旋转对称 %s（score %.3f）+ 接触带同向（夹角 ≤ %.1f°）"
                          % (chosen["source"], float(chosen["score"] or 0.0), CONSENSUS_ANGLE_DEG))
            else:
                reason = ("旋转对称证据通过：%s（score %.3f，收获 %d 个同向候选）"
                          % (chosen["source"], float(chosen["score"] or 0.0), len(chosen.get("passers") or [])))
        else:
            top = max([float(c.get("score") or 0.0) for c in per_obj if c.get("score") is not None] or [0.0])
            verdict = "unresolved"
            reason = ("没有对象达到评分阈值 %.2f（最高 %.3f）%s → 证据不足"
                      % (min_score, top,
                         "；且候选退化（球/近立方无唯一轴）" if any(c.get("degenerate") for c in per_obj) else ""))

    missing = []
    if verdict == "unresolved":
        if conflict:
            missing.append("缺：判别依据（旋转对称轴与接触带方向矛盾，需显式给 axis 或补第三种证据）")
        if not any(c.get("score") is not None for c in per_obj):
            missing.append("child 组没有任何可评分的三角面")
        missing.append("缺：自转对称件（圆柱/销/轮）——薄平板绕铰链轴没有旋转对称")
        if not par:
            missing.append("缺：父组（没有父组就算不了父↔子接触带）")
        elif not (contact_ev and contact_ev.get("available")):
            missing.append("缺：父↔子近邻接触点（max_distance 内点数 < %d）" % CONTACT_MIN_POINTS)
        elif not contact_ev.get("passed"):
            missing.append("缺：细长接触带（elongation < %.2f）" % ELONGATION_THRESHOLD)

    out = {
        "ok": verdict != "unresolved",
        "op": "infer_axis",
        "jid": (str(jid) if jid is not None else None),
        "child": [o.name for o in child],
        "parent": [o.name for o in par],
        "missing_objects": miss_c + miss_p,
        "non_mesh": nonmesh_c + nonmesh_p,
        "verdict": verdict,
        "reason": reason,
        "missing_evidence": missing,
        "axis": (chosen["axis"] if chosen else None),
        "anchor": (chosen["anchor"] if chosen else None),
        "axis_source": (chosen["source"] if chosen else None),
        "anchor_source": (chosen.get("anchor_source") if chosen else None),
        "axis_conflict": conflict,
        "hypothesis": hypothesis,
        "evidence": {
            "sample_cap": int(sample_cap), "min_score": float(min_score),
            "angle_tol_deg": CONSENSUS_ANGLE_DEG,
            "total_samples": int(total_samples),
            "child_verts": int(len(VV)), "child_tris": int(len(FF)),
            "per_object": per_obj,
            "group": group_ev,
            "contact": contact_ev,
            "consensus": cons,
            "units": {"length": "场景单位（1 单位 = %g m）" % _scene_mpu(), "angle": "deg"},
            "method": "面积加权质心+惯性主轴；90°/180° 旋转后 27 邻格最近邻距离评分（cap=5%% 对角线，cell=2%%）",
        },
        "note": str(note),
        "ms": int((time.perf_counter() - t0) * 1000),
    }
    return _j(out)


# ============================================================ 关节登记

def motion_joint(jid, parent, child, kind="revolute", axis=None, anchor=None, limits=None,
                 note="", verdict=None, snap_axis=False):
    """登记一个关节。axis/anchor 不给 → 模块实测推断（证据不足则该关节轴为 unresolved）。

    snap_axis=True 时把最终轴吸附到最近的世界轴（|分量| ≥ 0.95 才算），原始值记进
    axis_snapped_from —— 默认**不吸附**，保持实测值（URDF 里就是实测的那点偏角）。
    """
    st = _store()
    jid = str(jid)
    k = str(kind or "revolute").lower()
    if not k:
        return _j({"ok": False, "error": "kind 不能为空", "kinds": list(ALL_KINDS)})
    pn, cn = _names(parent), _names(child)
    if not pn or not cn:
        return _j({"ok": False, "error": "parent / child 都要给（对象名或对象名列表=刚性组）",
                   "parent": pn, "child": cn})
    po, miss_p, nonmesh_p = _objs(pn)
    co, miss_c, nonmesh_c = _objs(cn)
    if not co or not po:
        return _j({"ok": False, "error": "关节两端必须都有可用 mesh 对象（空产出不允许）",
                   "parent_ok": [o.name for o in po], "child_ok": [o.name for o in co],
                   "missing": miss_p + miss_c, "non_mesh": nonmesh_p + nonmesh_c})

    warnings = []
    if k not in ALL_KINDS:
        warnings.append("未知 kind=%s：按原样登记，URDF 导出会降级为 fixed" % k)
    elif k in PASSIVE_KINDS:
        warnings.append("kind=%s 在 URDF 里表达不了 → 导出时降级为 fixed（扫掠/轴推断对它无意义）" % k)

    ax = None
    anc = None
    src = "none"
    a_verdict = "unresolved"
    ev = None
    if axis is not None:
        a = np.asarray([float(x) for x in axis], dtype=np.float64)
        n = float(np.linalg.norm(a))
        if n < 1e-12:
            return _j({"ok": False, "error": "axis 是零向量，无法定义关节轴", "axis": axis})
        ax = (a / n).tolist()
        src = "given"
        a_verdict = "supported"
        if anchor is not None:
            anc = [float(x) for x in anchor]
        else:
            v, _f, _p = _group_tris(co, bpy.context.evaluated_depsgraph_get())
            mn, mx, _diag = _bbox(v)
            if mn is None:
                return _j({"ok": False, "error": "给 axis 但 child 组没有几何，无法推 anchor（请显式给 anchor）"})
            cen = (mn + mx) / 2.0
            # 锚点 = 组中心在轴上的投影（轴过组中心）—— 只是确定性兜底，明确记 warning
            anc = cen.tolist()
            warnings.append("只给了 axis 没给 anchor：锚点取 child 组 bbox 中心 (%.4g, %.4g, %.4g) 在轴上的投影，"
                            "可能不在真实铰链线上 —— 建议显式给 anchor 或用实测推断" % tuple(anc))
    elif k in AXIS_KINDS:
        inf = json.loads(motion_infer_axis(objects=cn, parent=pn))
        ev = inf.get("evidence")
        a_verdict = inf.get("verdict")
        if inf.get("ok") and inf.get("axis"):
            ax = inf["axis"]
            anc = inf["anchor"]
            src = inf.get("axis_source") or "measured"
        else:
            warnings.append("轴实测判定为 %s：%s" % (a_verdict, inf.get("reason")))
            warnings.append("该关节没有可用的轴 → 扫掠会 unresolved，URDF 里会退化为 continuous/fixed 级的占位信息")
    else:
        a_verdict = "supported"          # fixed/被动关节不需要轴
        src = "not_required"

    lim = None
    if limits is not None:
        try:
            lim = [float(limits[0]), float(limits[1])]
            if lim[1] < lim[0]:
                lim = [lim[1], lim[0]]
                warnings.append("limits 上下界反了，已交换")
        except Exception:
            return _j({"ok": False, "error": "limits 要 [lower, upper]（revolute=deg，prismatic=场景单位）",
                       "limits": limits})

    snapped_from = None
    if ax is not None and snap_axis:
        raw = [float(x) for x in ax]
        s = _snap_axis(np.asarray(raw, dtype=np.float64))
        if s is not None:
            tgt = {"X": [1.0, 0.0, 0.0], "Y": [0.0, 1.0, 0.0], "Z": [0.0, 0.0, 1.0]}[s]
            ax = tgt
            snapped_from = _rl(raw, 9)
            src = src + "+snapped"
            warnings.append("snap_axis：轴吸附到 %s（原始 %s，夹角 %.3f°）—— 原始值留在 axis_snapped_from"
                            % (s, _rl(raw, 6), _angle_deg(raw, tgt) or 0.0))
        else:
            warnings.append("snap_axis 打开但实测轴与任何世界轴的 |分量| 都 < 0.95 → 不吸附（保留实测值）")

    rec = {
        "id": jid, "parent": pn, "child": cn, "kind": k,
        "axis": (_rl(ax, 9) if ax else None), "anchor": (_rl(anc, 9) if anc else None),
        "axis_source": src, "axis_verdict": a_verdict, "limits": lim, "note": str(note),
        "axis_snapped_from": snapped_from,
        "evidence": ev, "warnings": warnings, "measured": None, "registered": _now(),
        "objects": {"parent": [o.name for o in po], "child": [o.name for o in co]},
    }
    if verdict:
        rec["axis_verdict"] = str(verdict)
    st["joints"][jid] = rec
    st["seq"] = int(st.get("seq", 0)) + 1
    _log("joint", {"id": jid, "kind": k, "parent": pn, "child": cn, "axis": rec["axis"],
                   "axis_verdict": rec["axis_verdict"]})
    return _j({"ok": True, "op": "joint", "id": jid, "kind": k, "parent": pn, "child": cn,
               "axis": rec["axis"], "anchor": rec["anchor"], "axis_source": src,
               "axis_snapped_from": snapped_from,
               "axis_verdict": rec["axis_verdict"], "limits": lim,
               "evidence": ({"verdict": (ev or {}).get("verdict") if isinstance(ev, dict) else None,
                             "consensus": (ev or {}).get("consensus") if isinstance(ev, dict) else None}
                            if ev else None),
               "warnings": warnings,
               "note": "revolute/continuous 的 limits 单位=deg；prismatic 的单位=场景单位"})


def motion_joints():
    st = _store()
    out = []
    for k, v in st["joints"].items():
        out.append({"id": k, "kind": v["kind"], "parent": v["parent"], "child": v["child"],
                    "axis": v.get("axis"), "anchor": v.get("anchor"),
                    "axis_source": v.get("axis_source"), "axis_verdict": v.get("axis_verdict"),
                    "limits": v.get("limits"),
                    "measured": (None if not v.get("measured") else
                                 {"verdict": v["measured"].get("verdict"),
                                  "phases": len(v["measured"].get("phases") or []),
                                  "moved": v["measured"].get("moved")}),
                    "warnings": v.get("warnings") or []})
    return _j({"ok": True, "op": "joints", "joints": out, "count": len(out)})


def _chain_names(jrec, all_joints, chain_depth="full"):
    """本关节链：immediate = 只 parent+child 两组；full = 沿已登记关节连通传递（祖先+后代）。"""
    chain = set(jrec["parent"]) | set(jrec["child"])
    if str(chain_depth) == "immediate":
        return chain
    changed = True
    while changed:
        changed = False
        for jid, j in all_joints.items():
            if j is jrec:
                continue
            pset, cset = set(j["parent"]), set(j["child"])
            if cset & chain and not pset <= chain:
                chain |= pset
                changed = True
            if pset & chain and not cset <= chain:
                chain |= cset
                changed = True
    return chain


# ============================================================ 扫掠验证

_TF_FIELDS = ("location", "scale", "rotation_euler", "rotation_quaternion",
              "delta_location", "delta_scale", "delta_rotation_euler", "delta_rotation_quaternion")
TOL_MAT = 1e-6          # matrix_world 对拍容差（Blender 内部按 float32 存 transform）


def _snap_transforms(objs):
    """快照 transform：矩阵（对拍用）+ 原始字段（还原用，float 位级一致）。"""
    snap = {}
    for ob in objs:
        fields = {}
        for f in _TF_FIELDS:
            v = getattr(ob, f, None)
            if v is None:
                continue
            try:
                fields[f] = [float(x) for x in v]
            except Exception:
                pass
        snap[ob.name] = {"mode": ob.rotation_mode,
                         "matrix": [float(x) for row in ob.matrix_world for x in row],
                         "parent_inverse": [float(x) for row in ob.matrix_parent_inverse for x in row],
                         "fields": fields}
    return snap


def _mat_of(rec):
    m = rec["matrix"]
    return Matrix([m[0:4], m[4:8], m[8:12], m[12:16]])


def _mat_now(ob):
    return [float(x) for row in ob.matrix_world for x in row]


def _snap_diff(a, b, tol=TOL_MAT):
    """两份 {名字: 16 元素矩阵} 快照逐对象逐项对拍 → (最大差, 超差对象, 是否严格逐项相等)。"""
    mx, bad = 0.0, []
    for k in sorted(set(a) | set(b)):
        va, vb = a.get(k), b.get(k)
        if va is None or vb is None or len(va) != len(vb):
            bad.append(k)
            continue
        d = max(abs(x - y) for x, y in zip(va, vb))
        mx = max(mx, d)
        if d > tol:
            bad.append(k)
    return mx, bad, bool(mx == 0.0)


def _restore_transforms(objs, snap, touched=None):
    """把 transform 还原到快照；只写"动过"的对象（没动的对象逐项对拍即可，绝不重写）。

    返回 (mismatches, pre_delta, wrote)：pre_delta = 还原前发现的最大偏差，
    还原后的残余偏差写在各对象的对拍里（mismatches 为空即逐项相等）。
    """
    touched = set(touched or [])
    bpy.context.view_layer.update()
    mism, pre_max, post_max, wrote = [], 0.0, 0.0, []
    for ob in objs:
        rec = snap.get(ob.name)
        if rec is None:
            mism.append({"name": ob.name, "reason": "快照里没有这个对象"})
            continue
        now = _mat_now(ob)
        d = max(abs(a - b) for a, b in zip(rec["matrix"], now))
        pre_max = max(pre_max, d)
        if d <= TOL_MAT and ob.name not in touched:
            post_max = max(post_max, d)
            continue                        # 没动过 → 逐项相等，无需写回
        try:
            if ob.rotation_mode != rec["mode"]:
                ob.rotation_mode = rec["mode"]
            for f, v in rec["fields"].items():
                setattr(ob, f, v)
            ob.matrix_parent_inverse = _mat_of({"matrix": rec["parent_inverse"]})
        except Exception as e:
            mism.append({"name": ob.name, "reason": "字段还原失败: %s" % str(e)[:120]})
            continue
        wrote.append(ob.name)
        bpy.context.view_layer.update()
        now2 = _mat_now(ob)
        d2 = max(abs(a - b) for a, b in zip(rec["matrix"], now2))
        if d2 > TOL_MAT:
            try:                            # 兜底：直接写矩阵
                ob.matrix_world = _mat_of(rec)
                bpy.context.view_layer.update()
                now3 = _mat_now(ob)
                d3 = max(abs(a - b) for a, b in zip(rec["matrix"], now3))
                d2 = d3
                if d3 > TOL_MAT:
                    mism.append({"name": ob.name, "reason": "transform 未还原", "residual": d3,
                                 "want": _rl(rec["matrix"], 9), "got": _rl(now3, 9)})
            except Exception as e:
                mism.append({"name": ob.name, "reason": "矩阵还原失败: %s" % str(e)[:120]})
                continue
        post_max = max(post_max, d2)
    return mism, pre_max, post_max, wrote


def _phase_values(jrec, phases, sweep_deg, margin_frac, V):
    """相位值：有 limits 用 limits（首尾各留 margin），否则 revolute 用 ±sweep_deg/2。"""
    k = jrec["kind"]
    if jrec.get("limits"):
        lo, hi = float(jrec["limits"][0]), float(jrec["limits"][1])
        unit = "deg" if k in ("revolute", "continuous") else "scene_unit"
    elif k in ("revolute", "continuous"):
        lo, hi = -abs(float(sweep_deg)) / 2.0, abs(float(sweep_deg)) / 2.0
        unit = "deg"
    else:  # prismatic：无 limits 时按 child 对角线的 10% 作为行程
        _, _, diag = _bbox(V)
        span = max(0.1 * (diag or 1.0), 1e-6)
        lo, hi = -span, span
        unit = "scene_unit"
    span = hi - lo
    if not (span > 0):
        return None, None, None, "limits/行程跨度为 0，无法扫掠"
    m = span * float(margin_frac)
    lo2, hi2 = lo + m, hi - m
    n = max(2, int(phases or 8))
    vals = [lo2 + (hi2 - lo2) * (i / float(n - 1)) for i in range(n)]
    return vals, unit, span, None


def _inside_probe(tree, p):
    """点是否在闭合网格内（最近面法线朝向判定）+ 到表面的距离。近似但足够做穿透深度。"""
    try:
        hit = tree.find_nearest(Vector((float(p[0]), float(p[1]), float(p[2]))))
    except Exception:
        return False, None
    if not hit or hit[0] is None or hit[3] is None:
        return False, None
    loc, nor, _idx, dist = hit
    inside = False
    if nor is not None:
        inside = (Vector((float(p[0]), float(p[1]), float(p[2]))) - loc).dot(nor) < 0.0
    return inside, float(dist)


def _min_gap_pair(tree_a, pts_a, tree_b, pts_b, max_samples=MAX_DIST_SAMPLES,
                  V_a=None, F_a=None, V_b=None, F_b=None):
    """双向采样求最小间距 + 干涉（BVH 三角面相交=精确）+ 穿透深度（重叠三角对 + 采样点，法线朝向估计）。"""
    def _stride(arr):
        if len(arr) <= max_samples:
            return arr
        st = int(math.ceil(len(arr) / float(max_samples)))
        return arr[::st]

    A = _stride(pts_a)
    B = _stride(pts_b)
    gap = None
    pen = 0.0
    witness = None
    for pts, tree in ((A, tree_b), (B, tree_a)):
        for p in pts:
            hit = tree.find_nearest(Vector((float(p[0]), float(p[1]), float(p[2]))))
            if not hit or hit[0] is None:
                continue
            loc, nor, _idx, dist = hit
            if dist is None:
                continue
            inside = False
            if nor is not None:
                d = (Vector((float(p[0]), float(p[1]), float(p[2]))) - loc).dot(nor)
                inside = d < 0.0
            if gap is None or dist < gap:
                gap = float(dist)
                witness = [float(p[0]), float(p[1]), float(p[2])]
            if inside and float(dist) > pen:
                pen = float(dist)
                witness = [float(p[0]), float(p[1]), float(p[2])]
    over = []
    try:
        over = tree_a.overlap(tree_b) or []
    except Exception:
        over = []
    # 相交三角对的面心当穿透探针（采样点可能一个都没落在对方内部）
    if over and V_a is not None and F_a is not None and V_b is not None and F_b is not None:
        probes = []
        for (ia, ib) in over[:64]:
            try:
                probes.append(V_a[F_a[int(ia)]].mean(axis=0))
            except Exception:
                pass
            try:
                probes.append(V_b[F_b[int(ib)]].mean(axis=0))
            except Exception:
                pass
        for p in probes:
            ins_b, d_b = _inside_probe(tree_b, p)      # 探针落在 B 内部 → 穿透进 B
            if ins_b and d_b is not None and d_b > pen:
                pen = float(d_b)
                witness = [float(p[0]), float(p[1]), float(p[2])]
            ins_a, d_a = _inside_probe(tree_a, p)      # 探针落在 A 内部 → B 穿进 A
            if ins_a and d_a is not None and d_a > pen:
                pen = float(d_a)
                witness = [float(p[0]), float(p[1]), float(p[2])]
    return {"gap": gap, "penetration": pen, "overlap_tris": int(len(over)),
            "overlap_sample": [[int(a), int(b)] for (a, b) in over[:MAX_OVERLAP_REPORT]],
            "witness": (_rl(witness, 6) if witness else None)}


def _measure_one(jrec, phases, sweep_deg, margin_frac, exclude, chain_depth,
                 max_candidates, max_samples, mpu):
    st = _store()
    jid = jrec["id"]
    if not jrec.get("axis"):
        why = ("关节类型 %s 不需要轴（axis_source=%s）→ 扫掠对它没有意义"
               % (jrec.get("kind"), jrec.get("axis_source"))
               if jrec.get("axis_source") == "not_required" else
               "关节没有轴（axis_verdict=%s）→ 先 motion_infer_axis 或登记时给 axis"
               % jrec.get("axis_verdict"))
        return {"ok": False, "op": "measure", "jid": jid, "verdict": "unresolved",
                "reason": why, "axis_verdict": jrec.get("axis_verdict"),
                "moved": None, "phases": [], "axis": None, "anchor": None}
    co, miss_c, nonmesh_c = _objs(jrec["child"])
    if not co:
        return {"ok": False, "op": "measure", "jid": jid, "verdict": "unresolved",
                "reason": "child 组没有可用 mesh 对象", "missing": miss_c, "non_mesh": nonmesh_c,
                "moved": None, "phases": [], "axis": jrec["axis"], "anchor": jrec.get("anchor")}

    axis = np.asarray(jrec["axis"], dtype=np.float64)
    anchor = np.asarray(jrec.get("anchor") or [0.0, 0.0, 0.0], dtype=np.float64)
    dg = bpy.context.evaluated_depsgraph_get()

    all_joints = dict(st["joints"])
    chain = _chain_names(jrec, all_joints, chain_depth=chain_depth)
    excl = set(_names(exclude)) | chain
    cands = []
    skipped_hidden = []
    for ob in bpy.data.objects:
        if ob.type != "MESH" or ob.name in excl:
            continue
        if ob.hide_viewport or ob.hide_render:
            skipped_hidden.append(ob.name)
            continue
        cands.append(ob)
    cands = sorted(cands, key=lambda o: o.name)[:max(1, int(max_candidates or 64))]

    V0, F0, parts0 = _group_tris(co, dg)
    if len(V0) == 0:
        return {"ok": False, "op": "measure", "jid": jid, "verdict": "unresolved",
                "reason": "child 组求值后没有三角面（无可测几何）", "moved": None, "phases": [],
                "axis": _rl(axis, 9), "anchor": _rl(anchor, 9)}

    vals, unit, span, err = _phase_values(jrec, phases, sweep_deg, margin_frac, V0)
    if err:
        return {"ok": False, "op": "measure", "jid": jid, "verdict": "unresolved", "reason": err,
                "moved": None, "phases": [], "axis": _rl(axis, 9), "anchor": _rl(anchor, 9)}

    # 候选 BVH / 采样点（静止不动的对象，只建一次）
    ctrees, cpts, cnames, cverts = {}, {}, [], {}
    for ob in cands:
        v, f = _mesh_tris_world(ob, dg)
        if len(v) == 0 or len(f) == 0:
            continue
        try:
            ctrees[ob.name] = BVHTree.FromPolygons([tuple(map(float, p)) for p in v],
                                                   [tuple(int(i) for i in t) for t in f],
                                                   all_triangles=True, epsilon=0.0)
        except Exception:
            continue
        cpts[ob.name] = _sample_points(v, f, max_samples)
        cverts[ob.name] = (v, f)
        cnames.append(ob.name)

    # 场景快照（全部对象：连无关对象都要证明没被动过）
    all_objs = [o for o in bpy.data.objects]
    snap = _snap_transforms(all_objs)
    rest_mat = {ob.name: _mat_of(snap[ob.name]) for ob in co if ob.name in snap}
    touched = set(rest_mat)
    t0 = time.perf_counter()
    phase_recs, moved_max, rest = [], 0.0, None
    sweep_err = None
    mism, pre_delta, post_delta, wrote = [], 0.0, 0.0, []
    touched = set(rest_mat)
    try:
        # 静止位（相位 0）：只做一次基线测量
        rest = _sweep_at(co, ctrees, cpts, cnames, V0, F0, dg, max_samples, mpu, cverts)
        for i, val in enumerate(vals):
            tp = time.perf_counter()
            if jrec["kind"] in ("revolute", "continuous"):
                th = math.radians(float(val))
                R = Matrix.Rotation(th, 4, Vector((float(axis[0]), float(axis[1]), float(axis[2]))))
                T = Matrix.Translation(Vector((float(anchor[0]), float(anchor[1]), float(anchor[2]))))
                M = T @ R @ T.inverted()
            else:
                M = Matrix.Translation(Vector((float(axis[0]), float(axis[1]), float(axis[2]))) * float(val))
            # 每个相位都从"静止位"算起（不是从上一相位）→ 不累积误差
            for ob in co:
                rm = rest_mat.get(ob.name)
                if rm is not None:
                    ob.matrix_world = M @ rm
            bpy.context.view_layer.update()
            Vp, Fp, _ = _group_tris(co, dg)
            if len(Vp):
                moved_max = max(moved_max, float(np.abs(Vp - V0).max()))
            rec = _sweep_at(co, ctrees, cpts, cnames, Vp, Fp, dg, max_samples, mpu, cverts)
            rec["phase"] = i
            rec["value"] = _r(val, 6)
            rec["unit"] = unit
            rec["ms"] = int((time.perf_counter() - tp) * 1000)
            phase_recs.append(rec)
    except Exception as e:
        sweep_err = "%s: %s" % (type(e).__name__, str(e)[:200])
    finally:
        mism, pre_delta, post_delta, wrote = _restore_transforms(all_objs, snap, touched=touched)

    colls = []
    for r in phase_recs:
        for c in r.get("collisions") or []:
            colls.append({"phase": r["phase"], "value": r["value"], "unit": unit,
                          "other": c["other"], "penetration_m": c["penetration_m"],
                          "penetration_units": c.get("penetration_units"),
                          "min_gap_m": c.get("min_gap_m"), "min_gap_units": c.get("min_gap_units"),
                          "collides": True, "overlap_tris": c["overlap_tris"],
                          "witness": c.get("witness")})
    tol = max(1e-9, 1e-6 * (float(np.linalg.norm(V0.max(axis=0) - V0.min(axis=0))) or 1.0))
    moved = bool(moved_max > tol)
    if not cands:
        verdict = "unresolved"
        reason = ("没有非链上对象可供干涉检查 → 零干涉是空洞证据（干涉检查需要一个「旁观者」对象）；"
                  "moved=%s" % moved)
    elif not moved:
        verdict = "refuted"
        reason = "驱动后 child 组无几何位移（最大位移 %.3e ≤ %.3e）→ 它并没有'能动'" % (moved_max, tol)
    elif colls:
        names = []
        for c in sorted(colls, key=lambda x: (x["penetration_m"] or 0.0), reverse=True):
            if c["other"] not in names:
                names.append(c["other"])
        verdict = "refuted"
        reason = ("扫掠中撞上 %s（%d 个相位有干涉，最深 %.4g m，相位 %s）；全程零干涉这一条不成立"
                  % ("、".join(names), len({c["phase"] for c in colls}),
                     max([c["penetration_m"] or 0.0 for c in colls] or [0.0]),
                     sorted({c["phase"] for c in colls})))
    else:
        verdict = "supported"
        reason = ("%d 个相位全程零干涉且确实位移（最大 %.4g m）→ 这就是'它能动'的运动学证据"
                  % (len(phase_recs), moved_max))
    if sweep_err:
        verdict = "unresolved"
        reason = "扫掠中途异常（场景已还原）：%s" % sweep_err
    return {
        "ok": verdict != "unresolved",
        "op": "measure", "jid": jid, "kind": jrec["kind"],
        "child": [o.name for o in co], "parent": list(jrec["parent"]),
        "axis": _rl(axis, 9), "anchor": _rl(anchor, 9),
        "axis_source": jrec.get("axis_source"), "axis_verdict": jrec.get("axis_verdict"),
        "sweep": {"phases": len(phase_recs), "unit": unit,
                  "span": _r(span, 6),
                  "span_si": _r(float(span) * (mpu if unit == "scene_unit" else (math.pi / 180.0)), 6),
                  "span_si_unit": ("m" if unit == "scene_unit" else "rad"),
                  "margin_frac": float(margin_frac), "limits": jrec.get("limits")},
        "rest": rest, "phases": phase_recs,
        "moved": moved, "max_displacement": _r(moved_max, 6),
        "max_displacement_m": _r(moved_max * mpu, 6),
        "verdict": verdict, "reason": reason, "error": sweep_err,
        "collisions": colls, "offenders": sorted({c["other"] for c in colls}),
        "chain_excluded": sorted(chain), "chain_depth": str(chain_depth),
        "candidates": cnames, "candidates_total": len(cnames),
        "skipped_hidden": skipped_hidden,
        "restored": {"ok": len(mism) == 0, "checked": len(all_objs), "mismatches": mism,
                     "pre_delta": pre_delta, "max_delta": post_delta, "tol": TOL_MAT,
                     "rewritten": wrote,
                     "note": "扫掠结束逐对象对拍 matrix_world（全部对象，不只 child 组）；"
                             "max_delta 是还原后的残余偏差（未动对象逐项相等，不重写）"},
        "units": {"length": "scene_unit（1 单位 = %g m）" % mpu, "angle": "deg"},
        "ms": int((time.perf_counter() - t0) * 1000),
    }


def _sweep_at(co, ctrees, cpts, cnames, V, F, dg, max_samples, mpu, cverts=None):
    """某一姿态下：child 组 vs 全部候选的干涉对 + 最小间距。V/F 用调用方算好的当前姿态世界三角面。"""
    if V is None or len(V) == 0:
        return {"collisions": [], "nearest": None, "checked": 0,
                "note": "child 组求值后没有几何"}
    if F is None or len(F) == 0:
        return {"collisions": [], "nearest": None, "checked": 0, "note": "child 组没有三角面"}
    tree = BVHTree.FromPolygons([tuple(map(float, p)) for p in V],
                                [tuple(int(i) for i in t) for t in F],
                                all_triangles=True, epsilon=0.0)
    pts = _sample_points(V, F, max_samples)
    mn, mx, diag = _bbox(V)
    margin = 0.05 * (diag or 0.0)
    lo = mn - margin
    hi = mx + margin
    collisions, nearest, checked = [], None, 0
    for name in cnames:
        t2 = ctrees.get(name)
        p2 = cpts.get(name)
        if t2 is None or p2 is None or len(p2) == 0:
            continue
        bmn = p2.min(axis=0); bmx = p2.max(axis=0)
        if np.any(bmx < lo) or np.any(bmn > hi):
            continue          # AABB 预筛：离得远，连间距都不必算
        checked += 1
        vb = cverts.get(name) if cverts else None
        r = _min_gap_pair(tree, pts, t2, p2, max_samples=max_samples,
                          V_a=V, F_a=F, V_b=(vb[0] if vb else None), F_b=(vb[1] if vb else None))
        gap = r["gap"]
        pen = float(r["penetration"] or 0.0)
        collides = (r["overlap_tris"] > 0) or (pen > 0.0)
        entry = {"other": name, "collides": bool(collides),
                 "gap_m": (_r(gap * mpu, 6) if gap is not None else None),
                 "gap_units": (_r(gap, 6) if gap is not None else None),
                 "penetration_m": _r(pen * mpu, 6), "penetration_units": _r(pen, 6),
                 "depth_measured": bool(pen > 0.0),
                 "overlap_tris": int(r["overlap_tris"]), "witness": r["witness"],
                 "samples_self": int(min(len(pts), max_samples)),
                 "samples_other": int(min(len(p2), max_samples))}
        if collides:
            # 间距符号约定：<0 表示穿模（穿透深度取负）。
            # 三角面相交已证实但探针没测到深度（贴面擦过）时 min_gap=0 且 depth_measured=false。
            entry["min_gap_m"] = _r(-pen * mpu, 6) if pen > 0 else 0.0
            entry["min_gap_units"] = _r(-pen, 6) if pen > 0 else 0.0
            collisions.append(entry)
        else:
            entry["min_gap_m"] = _r((gap or 0.0) * mpu, 6)
            entry["min_gap_units"] = _r(gap or 0.0, 6)
            if gap is not None and (nearest is None or gap < nearest["gap_units"]):
                nearest = entry
    collisions.sort(key=lambda e: -(e["penetration_units"] or 0.0))
    return {"collisions": collisions, "nearest": nearest, "checked": checked}


def motion_measure(jid=None, phases=8, sweep_deg=60, margin_frac=0.05, exclude=None,
                   chain_depth="full", max_candidates=64, max_samples=MAX_DIST_SAMPLES, note=""):
    """扫掠验证（替代 Isaac）：驱动 N 个相位，逐相位做 BVH 干涉 + 最小间距；结束时完整还原场景。

    verdict：全程零干涉且确实位移 → supported；一动就撞（干涉且间距<0）→ refuted（点名撞在谁身上）；
             没有轴 / 没有 child 几何 / 没有可比对对象 → unresolved。
    jid=None → 对全部已登记关节逐个测量（聚合判定取最差）。
    """
    st = _store()
    mpu = _scene_mpu()
    if jid is not None:
        jrec = st["joints"].get(str(jid))
        if jrec is None:
            return _j({"ok": False, "op": "measure", "error": "关节未登记: %s" % jid,
                       "have": sorted(st["joints"])})
        rec = _measure_one(jrec, phases, sweep_deg, margin_frac, exclude, chain_depth,
                           max_candidates, max_samples, mpu)
        rec["note"] = str(note)
        if rec.get("ok") or rec.get("verdict"):
            jrec["measured"] = {"verdict": rec.get("verdict"), "ms": rec.get("ms"),
                                "moved": rec.get("moved"), "phases": rec.get("phases"),
                                "reason": rec.get("reason"), "t": _now(),
                                "restored_ok": (rec.get("restored") or {}).get("ok")}
        _log("measure", {"jid": rec.get("jid"), "verdict": rec.get("verdict"),
                         "reason": str(rec.get("reason"))[:200]})
        return _j(rec)

    results, order = {}, {"refuted": 0, "unresolved": 1, "supported": 2}
    worst, worst_jid = "supported", None
    for k, jrec in st["joints"].items():
        r = _measure_one(jrec, phases, sweep_deg, margin_frac, exclude, chain_depth,
                         max_candidates, max_samples, mpu)
        r["note"] = str(note)
        jrec["measured"] = {"verdict": r.get("verdict"), "ms": r.get("ms"), "moved": r.get("moved"),
                            "phases": r.get("phases"), "reason": r.get("reason"), "t": _now(),
                            "restored_ok": (r.get("restored") or {}).get("ok")}
        results[k] = r
        v = r.get("verdict") or "unresolved"
        if worst_jid is None or order.get(v, 1) < order.get(worst, 2):
            worst, worst_jid = v, k
    if not results:
        return _j({"ok": False, "op": "measure", "error": "没有任何已登记关节", "verdict": "unresolved"})
    return _j({"ok": worst != "unresolved", "op": "measure", "verdict": worst, "worst_joint": worst_jid,
               "verdicts": {k: (v.get("verdict")) for k, v in results.items()},
               "results": results,
               "units": {"length": "scene_unit（1 单位 = %g m）" % mpu, "angle": "deg"}})


# ============================================================ URDF 导出

def _select_joints(joints):
    st = _store()
    if joints is None:
        ids = list(st["joints"])
    else:
        ids = [str(x) for x in _names(joints)]
        bad = [i for i in ids if i not in st["joints"]]
        if bad:
            return None, _j({"ok": False, "error": "有未登记的关节 id", "unknown": bad,
                             "have": sorted(st["joints"])})
    recs = [st["joints"][i] for i in ids]
    return recs, None


def _group_conflicts(recs):
    """同一对象出现在多个 link 里 = 刚体归属矛盾：URDF/USDA 会把它的几何与质量重复计入 → 必须拦住。"""
    owner = {}
    for r in recs:
        for side in ("parent", "child"):
            lname = _link_name(r[side])
            for n in _names(r[side]):
                owner.setdefault(n, set()).add(lname)
    return {n: sorted(v) for n, v in owner.items() if len(v) > 1}


def _build_tree(recs, warnings):
    """关节记录 → URDF 单树（link=对象组；degrade / 丢边 / orphan 合成固定关节）。"""
    links, edges, order = {}, {}, []
    for r in recs:
        pk, ck = _link_key(r["parent"]), _link_key(r["child"])
        for key in (pk, ck):
            if key not in links:
                links[key] = {"key": key, "names": list(key), "name": _link_name(key),
                              "frame": None, "joint": None}
        if ck in edges:
            warnings.append("link %s 已有父关节（%s），跳过关节 %s（URDF 单树约束）"
                            % (links[ck]["name"], edges[ck]["jid"], r["id"]))
            continue
        edges[ck] = {"jid": r["id"], "parent": pk, "child": ck, "rec": r}
        order.append(ck)
    if not links:
        return None
    # 根：没有父关节的 link
    roots = [k for k in links if k not in edges]
    if not roots:
        warnings.append("关节图成环（没有无父 link）→ 丢掉一条边打破环")
        drop = order[0]
        edges.pop(drop, None)
        roots = [k for k in links if k not in edges]
    root = sorted(roots, key=lambda k: links[k]["name"])[0]
    if len(roots) > 1:
        warnings.append("有 %d 个候选根（%s）→ 取 %s 为根，其余在下面按不可达处理"
                        % (len(roots), ", ".join(links[k]["name"] for k in sorted(roots)), links[root]["name"]))
    # 可达性
    def _reach():
        seen = {root}
        q = [root]
        kids = {}
        for ck, e in edges.items():
            kids.setdefault(e["parent"], []).append(ck)
        while q:
            cur = q.pop(0)
            for c in kids.get(cur, []):
                if c not in seen:
                    seen.add(c)
                    q.append(c)
        return seen
    reach = _reach()
    guard = 0
    while len(reach) < len(links) and guard < len(links) + 2:
        guard += 1
        cand = sorted([k for k in links if k not in reach and k not in edges],
                      key=lambda k: links[k]["name"])
        if not cand:
            cand = sorted([k for k in links if k not in reach], key=lambda k: links[k]["name"])
            if cand:
                warnings.append("link %s 不可达且带着成环的边 → 丢掉该边以接入根"
                                % links[cand[0]]["name"])
                edges.pop(cand[0], None)
                cand = [cand[0]]
        if not cand:
            break
        k = cand[0]
        warnings.append("link %s 从根 %s 不可达 → 合成 fixed 关节挂到根（URDF 必须单树）"
                        % (links[k]["name"], links[root]["name"]))
        edges[k] = {"jid": "orphan_%s_fixed" % links[k]["name"], "parent": root, "child": k,
                    "rec": None, "synthesized": True}
        reach = _reach()
    # 帧：根=世界原点，其余=关节锚点（无锚点则退到该组 bbox 中心）
    links[root]["frame"] = np.zeros(3).tolist()
    dgf = bpy.context.evaluated_depsgraph_get()
    for ck, e in edges.items():
        r = e["rec"]
        if r is not None and r.get("anchor"):
            links[ck]["frame"] = [float(x) for x in r["anchor"]]
            continue
        v, _f, _p = _group_tris(_objs(links[ck]["names"])[0], dgf)
        mn, mx, _d = _bbox(v)
        if mn is not None:
            links[ck]["frame"] = (((mn + mx) / 2.0).tolist())
            if r is not None:
                warnings.append("关节 %s（kind=%s）没有锚点 → link %s 的帧取该组 bbox 中心 "
                                "(%.4g, %.4g, %.4g)（网格随之写成相对该帧）"
                                % (r["id"], r["kind"], links[ck]["name"], links[ck]["frame"][0],
                                   links[ck]["frame"][1], links[ck]["frame"][2]))
        else:
            links[ck]["frame"] = [0.0, 0.0, 0.0]
            warnings.append("link %s 既没有关节锚点也没有几何 → 帧取世界原点" % links[ck]["name"])
    return {"root": root, "links": links, "edges": edges, "reach": reach}


def _urdf_type(rec, warnings):
    k = str(rec["kind"]).lower()
    if k in ("revolute", "continuous", "prismatic", "fixed"):
        if k == "revolute" and not rec.get("limits"):
            warnings.append("关节 %s 是 revolute 但没有 limits → 按 continuous 导出"
                            "（URDF 的 revolute 必须给 lower/upper）" % rec["id"])
            return "continuous", None
        return k, None
    warnings.append("关节 %s 的 kind=%s 降级为 fixed（URDF 表达不了 %s）" % (rec["id"], k, k))
    return "fixed", k


def _urdf_limits_attrs(utype, rec, fallback_travel_m, warnings):
    k = rec["kind"]
    if utype == "revolute":
        lo = float((rec.get("limits") or [0.0, 0.0])[0])
        hi = float((rec.get("limits") or [0.0, 0.0])[1])
        return ' lower="%s" upper="%s"' % (_fnum(math.radians(lo)), _fnum(math.radians(hi)))
    if utype == "prismatic":
        if rec.get("limits"):
            lo, hi = float(rec["limits"][0]), float(rec["limits"][1])
            return ' lower="%s" upper="%s"' % (_fnum(lo), _fnum(hi))
        warnings.append("关节 %s 是 prismatic 但没 limits → 用模型对角线的 ±%s m 兜底"
                        "（URDF 缺 lower/upper 会被读成 0，等于锁死自由度）"
                        % (rec["id"], _fnum(fallback_travel_m)))
        return ' lower="%s" upper="%s"' % (_fnum(-fallback_travel_m), _fnum(fallback_travel_m))
    return ""


def motion_export_urdf(dir=None, name="model", joints=None, meters_per_unit=0.001,
                       density=DEFAULT_DENSITY, masses=None, mesh_format="obj",
                       effort=DEFAULT_EFFORT, velocity=DEFAULT_VELOCITY, fixed_base=False, note=""):
    """写 <dir>/<name>.urdf + 每 link 一个网格（OBJ）+ warnings；写完做结构自检。

    单位：URDF 输出 SI（m / rad / kg / kg·m²）；场景单位按 meters_per_unit 换算
          （默认 0.001 = 场景 1 单位 = 1 mm，与上游 stage 约定一致；米制场景请传 1.0）。
    惯量：bbox 近似（质心=bbox 中心，盒体公式，填充系数 BBOX_FILL_FACTOR=0.4，质量=体积×密度）。
    """
    t0 = time.perf_counter()
    if not dir:
        return _j({"ok": False, "error": "dir 必填（URDF 与网格写到哪）"})
    recs, err = _select_joints(joints)
    if err is not None:
        return _j(json.loads(err))
    if not recs:
        return _j({"ok": False, "error": "没有关节可导出（空产出必须失败）",
                   "registered": sorted(_store()["joints"])})
    conf = _group_conflicts(recs)
    if conf:
        return _j({"ok": False, "error": "关节分组互相重叠：同一个对象属于多个 link（刚体归属矛盾 → "
                                        "几何与质量会被重复计入）→ 请把各关节的 parent/child 组改成互不重叠",
                   "conflicts": conf, "units": {"length": "scene_unit"}})
    fmt = str(mesh_format or "obj").lower()
    if fmt not in ("obj", "stl"):
        return _j({"ok": False, "error": "mesh_format 只支持 obj / stl", "given": fmt})
    mpu = _mpu_arg(meters_per_unit)
    robot = _safe_name(name, "model")
    outdir = _win(dir)
    meshdir = os.path.join(outdir, "meshes")
    warnings = []
    try:
        os.makedirs(meshdir, exist_ok=True)
    except Exception as e:
        return _j({"ok": False, "error": "建目录失败: %s: %s" % (type(e).__name__, e), "dir": outdir})

    tree = _build_tree(recs, warnings)
    if tree is None:
        return _j({"ok": False, "error": "关节树为空（没有 link）"})
    links, edges, root = tree["links"], tree["edges"], tree["root"]
    dg = bpy.context.evaluated_depsgraph_get()
    masses = masses or {}

    # 兜底行程（m）：模型 bbox 对角线，下限 0.1 m
    allV = []
    for k, L in links.items():
        v, _f, _p = _group_tris(_objs(L["names"])[0], dg)
        if len(v):
            allV.append(v)
    fallback_travel_m = 0.1
    model_diag_m = 0.0
    if allV:
        mn, mx, _d = _bbox(np.concatenate(allV, axis=0))
        if mn is not None:
            model_diag_m = float(np.linalg.norm(mx - mn)) * mpu
            fallback_travel_m = max(0.1, model_diag_m)
    units_suspect = _units_warnings(mpu, model_diag_m, warnings)

    # 每 link 一个网格（相对 link 帧，单位 m）
    mesh_files, link_geo = {}, {}
    for k, L in links.items():
        objs, _m, _n = _objs(L["names"])
        V, F, _parts = _group_tris(objs, dg)
        if len(V) == 0 or len(F) == 0:
            warnings.append("link %s 没有可导出的三角面 → URDF 里该 link 无 visual/collision" % L["name"])
            link_geo[k] = None
            continue
        frame = np.asarray(L["frame"] or [0.0, 0.0, 0.0], dtype=np.float64)
        Vm = (V - frame) * mpu
        fn = "%s.%s" % (L["name"], fmt)
        fp = os.path.join(meshdir, fn)
        try:
            if fmt == "obj":
                _write_obj(fp, Vm, F, "DSH motion 导出：link %s / %d 顶点 / %d 三角面 / 单位 m"
                                       % (L["name"], len(Vm), len(F)))
            else:
                _write_stl(fp, Vm, F, "DSH motion link %s" % L["name"])
        except Exception as e:
            return _j({"ok": False, "error": "写网格失败 %s: %s" % (fn, str(e)[:160])})
        mesh_files[k] = fn
        link_geo[k] = {"verts": int(len(Vm)), "tris": int(len(F)),
                       "min": _rl(Vm.min(axis=0), 6), "max": _rl(Vm.max(axis=0), 6),
                       "bytes": os.path.getsize(fp)}

    L = ['<?xml version="1.0" encoding="utf-8"?>',
         '<!-- DSH motion v%d 导出（纯字符串生成，SI 单位；bbox 近似惯量） -->' % MOTION_VERSION,
         '<robot name="%s">' % _esc(robot)]
    if fixed_base:
        L.append('  <link name="world"/>')
        L.append('  <joint name="world_to_%s" type="fixed">' % _esc(links[root]["name"]))
        L.append('    <origin xyz="0 0 0" rpy="0 0 0"/>')
        L.append('    <parent link="world"/>')
        L.append('    <child link="%s"/>' % _esc(links[root]["name"]))
        L.append('  </joint>')
    for k in sorted(links, key=lambda x: links[x]["name"]):
        Lk = links[k]
        V, F, _p = _group_tris(_objs(Lk["names"])[0], dg)
        frame = np.asarray(Lk["frame"] or [0.0, 0.0, 0.0], dtype=np.float64)
        dims, mass = [0.0, 0.0, 0.0], 0.0
        com = [0.0, 0.0, 0.0]
        if len(V):
            vloc = (V - frame) * mpu
            mn, mx = vloc.min(axis=0), vloc.max(axis=0)
            dims = (mx - mn).tolist()
            com = ((mn + mx) / 2.0).tolist()
            vol = max(0.0, dims[0] * dims[1] * dims[2])
            m_explicit = None
            for key in (Lk["name"],) + tuple(Lk["names"]):
                if key in masses:
                    m_explicit = float(masses[key])
                    break
            mass = m_explicit if m_explicit is not None else float(density) * vol * BBOX_FILL_FACTOR
        ixx = max(MIN_INERTIA, mass / 12.0 * (dims[1] ** 2 + dims[2] ** 2))
        iyy = max(MIN_INERTIA, mass / 12.0 * (dims[0] ** 2 + dims[2] ** 2))
        izz = max(MIN_INERTIA, mass / 12.0 * (dims[0] ** 2 + dims[1] ** 2))
        if mass <= 0:
            warnings.append("link %s 质量为 0（密度 %g × bbox 体积 × %.2f）→ 惯量夹到 %.0e"
                            % (Lk["name"], density, BBOX_FILL_FACTOR, MIN_INERTIA))
        L.append('  <link name="%s">' % _esc(Lk["name"]))
        L.append('    <inertial>')
        L.append('      <origin xyz="%s %s %s" rpy="0 0 0"/>' % tuple(_fnum(x) for x in com))
        L.append('      <mass value="%s"/>' % _fnum(mass))
        L.append('      <inertia ixx="%s" ixy="0" ixz="0" iyy="%s" iyz="0" izz="%s"/>'
                 % (_fnum(ixx), _fnum(iyy), _fnum(izz)))
        L.append('    </inertial>')
        fn = mesh_files.get(k)
        if fn:
            for tag in ("visual", "collision"):
                L.append('    <%s>' % tag)
                L.append('      <origin xyz="0 0 0" rpy="0 0 0"/>')
                L.append('      <geometry>')
                L.append('        <mesh filename="meshes/%s"/>' % _esc(fn))
                L.append('      </geometry>')
                L.append('    </%s>' % tag)
        else:
            warnings.append("link %s 无几何：URDF 里没有 visual/collision" % Lk["name"])
        L.append('  </link>')
    for ck in sorted(edges, key=lambda x: links[x]["name"]):
        e = edges[ck]
        r = e["rec"]
        child = links[ck]
        parent = links[e["parent"]]
        pf = np.asarray(parent["frame"] or [0.0, 0.0, 0.0], dtype=np.float64)
        cf = np.asarray(child["frame"] or [0.0, 0.0, 0.0], dtype=np.float64)
        org = (cf - pf) * mpu
        if r is None:
            utype, degraded, jname = "fixed", None, e["jid"]
            axis = [0.0, 0.0, 1.0]
        else:
            utype, degraded = _urdf_type(r, warnings)
            jname = _safe_name(r["id"], "joint")
            axis = r.get("axis") or [0.0, 0.0, 1.0]
        L.append('  <joint name="%s" type="%s">' % (_esc(jname), utype))
        L.append('    <origin xyz="%s %s %s" rpy="0 0 0"/>' % tuple(_fnum(x) for x in org))
        L.append('    <parent link="%s"/>' % _esc(parent["name"]))
        L.append('    <child link="%s"/>' % _esc(child["name"]))
        if utype != "fixed":
            L.append('    <axis xyz="%s %s %s"/>' % tuple(_fnum(x) for x in axis))
            attrs = ""
            if r is not None:
                attrs = _urdf_limits_attrs(utype, r, fallback_travel_m, warnings)
            L.append('    <limit%s effort="%s" velocity="%s"/>'
                     % (attrs, _fnum(effort), _fnum(velocity if utype != "prismatic" else velocity * mpu)))
        L.append('  </joint>')
    L.append('</robot>')
    text = "\n".join(L) + "\n"
    path = os.path.join(outdir, robot + ".urdf")
    try:
        with open(path, "w", encoding="utf-8") as f:
            f.write(text)
    except Exception as e:
        return _j({"ok": False, "error": "写 URDF 失败: %s: %s" % (type(e).__name__, e), "path": path})

    chk = _urdf_self_check(text, path, meshdir, mesh_files, root)
    rec_export = {"kind": "urdf", "path": path, "dir": outdir, "meshes": len(mesh_files),
                  "bytes": os.path.getsize(path), "links": len(links), "joints": len(edges),
                  "self_check_ok": chk["ok"], "warnings": len(warnings), "t": _now(),
                  "note": str(note)}
    _store()["exports"].append(rec_export)
    del _store()["exports"][:-50]
    _log("export_urdf", {"path": path, "links": len(links), "joints": len(edges),
                         "self_check_ok": chk["ok"]})
    if not chk["ok"]:
        return _j({"ok": False, "error": "URDF 结构自检未通过（产物已写出，但不合格）",
                   "path": path, "self_check": chk, "warnings": warnings})
    return _j({"ok": True, "op": "export_urdf", "path": path, "dir": outdir, "mesh_dir": meshdir,
               "robot": robot, "root_link": links[root]["name"],
               "links": [{"link": links[k]["name"], "objects": links[k]["names"],
                          "frame_m": _rl(np.asarray(links[k]["frame"] or [0, 0, 0]) * mpu, 6),
                          "mesh": mesh_files.get(k), "geo": link_geo.get(k)}
                         for k in sorted(links, key=lambda x: links[x]["name"])],
               "joints": [{"joint": e["jid"], "type": (_urdf_type(e["rec"], [])[0] if e["rec"] else "fixed"),
                           "parent": links[e["parent"]]["name"], "child": links[e["child"]]["name"],
                           "synthesized": bool(e.get("synthesized")),
                           "degraded_from": (_urdf_type(e["rec"], [])[1] if e["rec"] else None)}
                          for e in [edges[c] for c in sorted(edges, key=lambda x: links[x]["name"])]],
               "units": {"urdf": "SI（m / rad / kg / kg·m²）",
                         "meters_per_unit": mpu,
                         "model_diagonal_m": _r(model_diag_m, 6),
                         "scene_units_suspect": units_suspect,
                         "note": "场景单位 × meters_per_unit = 米；OBJ 顶点已是米；"
                                 "meters_per_unit 可传 \"scene\" 读 scene.unit_settings.scale_length"},
               "warnings": warnings, "self_check": chk,
               "ms": int((time.perf_counter() - t0) * 1000), "note": str(note)})


def _write_obj(path, V, F, header):
    lines = ["# " + header]
    for p in V:
        lines.append("v %.9g %.9g %.9g" % (float(p[0]), float(p[1]), float(p[2])))
    for t in F:
        lines.append("f %d %d %d" % (int(t[0]) + 1, int(t[1]) + 1, int(t[2]) + 1))
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")


def _write_stl(path, V, F, name):
    with open(path, "w", encoding="utf-8") as f:
        f.write("solid %s\n" % _safe_name(name))
        for t in F:
            a, b, c = V[int(t[0])], V[int(t[1])], V[int(t[2])]
            n = np.cross(b - a, c - a)
            ln = float(np.linalg.norm(n))
            n = (n / ln) if ln > 1e-30 else np.zeros(3)
            f.write("  facet normal %.6g %.6g %.6g\n    outer loop\n" % (n[0], n[1], n[2]))
            for p in (a, b, c):
                f.write("      vertex %.9g %.9g %.9g\n" % (float(p[0]), float(p[1]), float(p[2])))
            f.write("    endloop\n  endfacet\n")
        f.write("endsolid %s\n" % _safe_name(name))


def _urdf_self_check(text, path, meshdir, mesh_files, root_key):
    problems = []
    chk = {"xml_parse": False, "link_names_unique": False, "joint_names_unique": False,
           "joint_endpoints_exist": False, "single_tree": False, "acyclic": False,
           "reachable_all": False, "mesh_files_exist": False,
           "counts": {"links": 0, "joints": 0, "meshes": len(mesh_files)},
           "problems": problems}
    try:
        root = ET.fromstring(text)
        chk["xml_parse"] = True
    except Exception as e:
        problems.append("XML 解析失败: %s: %s" % (type(e).__name__, str(e)[:160]))
        chk["ok"] = False
        return chk
    if root.tag != "robot":
        problems.append("根元素不是 <robot> 而是 <%s>" % root.tag)
    lnames = [e.get("name") for e in root.findall("link")]
    jnames = [e.get("name") for e in root.findall("joint")]
    chk["counts"]["links"] = len(lnames)
    chk["counts"]["joints"] = len(jnames)
    chk["link_names_unique"] = (len(set(lnames)) == len(lnames))
    if not chk["link_names_unique"]:
        problems.append("link 名字有重复")
    chk["joint_names_unique"] = (len(set(jnames)) == len(jnames))
    if not chk["joint_names_unique"]:
        problems.append("joint 名字有重复")
    lset = set(lnames)
    ok_end = True
    kids, indeg = {}, {}
    for e in root.findall("joint"):
        pe = e.find("parent"); ce = e.find("child")
        p = pe.get("link") if pe is not None else None
        c = ce.get("link") if ce is not None else None
        if p not in lset or c not in lset:
            ok_end = False
            problems.append("joint %s 的 parent/child 不在 link 列表里（%s → %s）" % (e.get("name"), p, c))
        else:
            kids.setdefault(p, []).append(c)
            indeg[c] = indeg.get(c, 0) + 1
    chk["joint_endpoints_exist"] = ok_end
    multi = [c for c, n in indeg.items() if n > 1]
    chk["single_tree"] = not multi
    if multi:
        problems.append("有 link 被多个关节当 child（不是单树）: %s" % ", ".join(multi))
    start = None
    for n in lnames:
        if indeg.get(n, 0) == 0:
            start = n
            break
    seen, stack, cyclic = set(), list(kids.get(start, [])), False
    if start is not None:
        seen.add(start)
        while stack:
            cur = stack.pop()
            if cur in seen:
                cyclic = True
                break
            seen.add(cur)
            stack.extend(kids.get(cur, []))
    chk["acyclic"] = (start is not None) and (not cyclic)
    if start is None:
        problems.append("没有无父 link（根不唯一或成环）")
    elif cyclic:
        problems.append("关节树里有环")
    chk["reachable_all"] = bool(start is not None) and (len(seen) == len(lnames))
    if not chk["reachable_all"]:
        problems.append("从根 %s 不可达的 link: %s"
                        % (start, ", ".join(sorted(lset - seen))))
    missing = []
    for fn in mesh_files.values():
        if not os.path.isfile(os.path.join(meshdir, fn)):
            missing.append(fn)
    chk["mesh_files_exist"] = not missing
    if missing:
        problems.append("mesh 文件不存在: %s" % ", ".join(missing))
    chk["root_link"] = start
    chk["ok"] = bool(chk["xml_parse"] and chk["link_names_unique"] and chk["joint_names_unique"]
                     and chk["joint_endpoints_exist"] and chk["single_tree"] and chk["acyclic"]
                     and chk["reachable_all"] and chk["mesh_files_exist"])
    return chk


# ============================================================ USDA 导出

def _usda_mat(T):
    return ("( (1, 0, 0, 0), (0, 1, 0, 0), (0, 0, 1, 0), (%s, %s, %s, 1) )"
            % (_fnum(T[0]), _fnum(T[1]), _fnum(T[2])))


def motion_export_usda(dir=None, name="model", joints=None, meters_per_unit=0.001,
                       density=DEFAULT_DENSITY, masses=None, fixed_base=False, note=""):
    """写最小可用 ASCII USDA：Xform（含 matrix4d xformOp:transform）+ UsdGeom.Mesh + 关节自定义属性。

    如实说明：**不是完整 USD Physics schema**（没有 UsdPhysics.Joint/Drive/MassAPI 的 schema 语义），
    只保证结构可读：mesh 的 points/faceVertexCounts/faceVertexIndices 齐全、xform 正确、关节元数据齐全。
    """
    t0 = time.perf_counter()
    if not dir:
        return _j({"ok": False, "error": "dir 必填（USDA 写到哪）"})
    recs, err = _select_joints(joints)
    if err is not None:
        return _j(json.loads(err))
    if not recs:
        return _j({"ok": False, "error": "没有关节可导出（空产出必须失败）",
                   "registered": sorted(_store()["joints"])})
    conf = _group_conflicts(recs)
    if conf:
        return _j({"ok": False, "error": "关节分组互相重叠：同一个对象属于多个 link（刚体归属矛盾 → "
                                        "几何与质量会被重复计入）→ 请把各关节的 parent/child 组改成互不重叠",
                   "conflicts": conf, "units": {"length": "scene_unit"}})
    mpu = _mpu_arg(meters_per_unit)
    robot = _safe_name(name, "model")
    outdir = _win(dir)
    warnings = []
    try:
        os.makedirs(outdir, exist_ok=True)
    except Exception as e:
        return _j({"ok": False, "error": "建目录失败: %s: %s" % (type(e).__name__, e), "dir": outdir})
    tree = _build_tree(recs, warnings)
    if tree is None:
        return _j({"ok": False, "error": "关节树为空（没有 link）"})
    links, edges, root = tree["links"], tree["edges"], tree["root"]
    dg = bpy.context.evaluated_depsgraph_get()
    _allV = []
    for _k, _L in links.items():
        _v, _f, _p = _group_tris(_objs(_L["names"])[0], dg)
        if len(_v):
            _allV.append(_v)
    model_diag_m = 0.0
    if _allV:
        _mn, _mx, _d = _bbox(np.concatenate(_allV, axis=0))
        if _mn is not None:
            model_diag_m = float(np.linalg.norm(_mx - _mn)) * mpu
    units_suspect = _units_warnings(mpu, model_diag_m, warnings)

    L = ["#usda 1.0",
         "(",
         '    doc = "DSH motion v%d 最小可用导出：结构可读，**不是完整 UsdPhysics schema**；'
         '关节元数据在 dsh:* 自定义属性里"' % MOTION_VERSION,
         '    defaultPrim = "%s"' % _safe_name(robot),
         '    metersPerUnit = %s' % _fnum(mpu),
         '    upAxis = "Z"',
         ")",
         "",
         'def Xform "%s"' % _safe_name(robot),
         "{"]
    if fixed_base:
        L += ['    def Xform "World"', "    {", "    }", ""]
    link_mesh_meta, joint_meta = {}, []
    L += ['    def Scope "Links"', "    {"]
    for k in sorted(links, key=lambda x: links[x]["name"]):
        Lk = links[k]
        objs, _m, _n = _objs(Lk["names"])
        V, F, _p = _group_tris(objs, dg)
        frame = np.asarray(Lk["frame"] or [0.0, 0.0, 0.0], dtype=np.float64)
        L.append('        def Xform "%s"' % _safe_name(Lk["name"]))
        L.append("        {")
        L.append("            matrix4d xformOp:transform = %s" % _usda_mat(frame))
        L.append('            uniform token[] xformOpOrder = ["xformOp:transform"]')
        L.append('            custom string dsh:objects = "%s"' % _esc(",".join(Lk["names"])))
        if len(V) and len(F):
            Vl = (V - frame)          # 场景单位（metersPerUnit 已声明换算）
            mn, mx = Vl.min(axis=0), Vl.max(axis=0)
            dims = (mx - mn).tolist()
            vol = max(0.0, dims[0] * dims[1] * dims[2]) * (mpu ** 3)
            m_explicit = None
            for key in (Lk["name"],) + tuple(Lk["names"]):
                if masses and key in masses:
                    m_explicit = float(masses[key])
                    break
            mass = m_explicit if m_explicit is not None else float(density) * vol * BBOX_FILL_FACTOR
            L.append('            custom double dsh:massKg = %s' % _fnum(mass))
            L.append('            custom double3 dsh:bboxSizeM = (%s, %s, %s)'
                     % tuple(_fnum(d * mpu) for d in dims))
            L.append('            def Mesh "mesh"')
            L.append("            {")
            L.append("                int[] faceVertexCounts = [%s]" % ", ".join(["3"] * len(F)))
            L.append("                int[] faceVertexIndices = [%s]" % ", ".join(str(int(i)) for i in F.ravel()))
            pts = []
            for p in Vl:
                pts.append("(%s, %s, %s)" % (_fnum(p[0]), _fnum(p[1]), _fnum(p[2])))
            L.append("                point3f[] points = [%s]" % ", ".join(pts))
            L.append('                uniform token subdivisionScheme = "none"')
            L.append("            }")
            link_mesh_meta[Lk["name"]] = {"points": int(len(Vl)), "faces": int(len(F))}
        else:
            warnings.append("link %s 没有几何 → 只有 Xform，没有 Mesh" % Lk["name"])
            link_mesh_meta[Lk["name"]] = {"points": 0, "faces": 0}
        L.append("        }")
    L += ["    }", ""]
    L += ['    def Scope "Joints"', "    {"]
    for ck in sorted(edges, key=lambda x: links[x]["name"]):
        e = edges[ck]
        r = e["rec"]
        child = links[ck]
        parent = links[e["parent"]]
        pf = np.asarray(parent["frame"] or [0.0, 0.0, 0.0], dtype=np.float64)
        cf = np.asarray(child["frame"] or [0.0, 0.0, 0.0], dtype=np.float64)
        org = cf - pf
        jname = _safe_name(e["jid"], "joint")
        utype, degraded = (_urdf_type(r, []) if r is not None else ("fixed", None))
        axis = (r.get("axis") if r is not None else None) or [0.0, 0.0, 1.0]
        L.append('        def Xform "%s"' % jname)
        L.append("        {")
        L.append('            custom string dsh:kind = "%s"' % (r["kind"] if r is not None else "fixed"))
        L.append('            custom string dsh:urdfType = "%s"' % utype)
        if degraded:
            L.append('            custom string dsh:degradedFrom = "%s"' % _esc(degraded))
            L.append('            custom string dsh:boundary = "URDF/USDA 均无法表达 %s；已按 fixed 记录"'
                     % _esc(degraded))
        L.append('            custom string dsh:parentLink = "%s"' % _esc(parent["name"]))
        L.append('            custom string dsh:childLink = "%s"' % _esc(child["name"]))
        L.append("            custom double3 dsh:axis = (%s, %s, %s)"
                 % tuple(_fnum(x) for x in axis))
        L.append("            custom double3 dsh:anchor = (%s, %s, %s)"
                 % tuple(_fnum(x) for x in cf))
        if r is not None and r.get("limits"):
            if utype == "revolute":
                lim = (math.radians(float(r["limits"][0])), math.radians(float(r["limits"][1])))
                L.append("            custom double2 dsh:limitsRad = (%s, %s)"
                         % (_fnum(lim[0]), _fnum(lim[1])))
            else:
                L.append("            custom double2 dsh:limits = (%s, %s)"
                         % (_fnum(r["limits"][0]), _fnum(r["limits"][1])))
        L.append('            custom bool dsh:synthesized = %s'
                 % ("true" if e.get("synthesized") else "false"))
        L.append('            custom string dsh:body0 = "%s"' % _esc(parent["name"]))
        L.append('            custom string dsh:body1 = "%s"' % _esc(child["name"]))
        L.append("            matrix4d xformOp:transform = %s" % _usda_mat(org))
        L.append('            uniform token[] xformOpOrder = ["xformOp:transform"]')
        L.append("        }")
        joint_meta.append({"joint": e["jid"], "kind": (r["kind"] if r is not None else "fixed"),
                           "type": utype, "degraded_from": degraded,
                           "parent": parent["name"], "child": child["name"]})
    L += ["    }", "}"]
    text = "\n".join(L) + "\n"
    path = os.path.join(outdir, robot + ".usda")
    try:
        with open(path, "w", encoding="utf-8") as f:
            f.write(text)
    except Exception as e:
        return _j({"ok": False, "error": "写 USDA 失败: %s: %s" % (type(e).__name__, e), "path": path})
    chk = _usda_self_check(text, path, link_mesh_meta, joint_meta)
    rec_export = {"kind": "usda", "path": path, "dir": outdir, "meshes": len(link_mesh_meta),
                  "bytes": os.path.getsize(path), "links": len(links), "joints": len(edges),
                  "self_check_ok": chk["ok"], "warnings": len(warnings), "t": _now(),
                  "note": str(note)}
    _store()["exports"].append(rec_export)
    del _store()["exports"][:-50]
    _log("export_usda", {"path": path, "links": len(links), "joints": len(edges),
                         "self_check_ok": chk["ok"]})
    if not chk["ok"]:
        return _j({"ok": False, "error": "USDA 结构自检未通过（产物已写出，但不合格）",
                   "path": path, "self_check": chk, "warnings": warnings})
    return _j({"ok": True, "op": "export_usda", "path": path, "dir": outdir, "robot": robot,
               "root_link": links[root]["name"], "links": link_mesh_meta, "joints": joint_meta,
               "units": {"usda": "metersPerUnit=%g（points/xform 用场景单位）" % mpu,
                         "model_diagonal_m": _r(model_diag_m, 6),
                         "scene_units_suspect": units_suspect,
                         "upAxis": "Z"},
               "warnings": warnings, "self_check": chk,
               "ms": int((time.perf_counter() - t0) * 1000), "note": str(note)})


def _usda_self_check(text, path, link_mesh_meta, joint_meta):
    problems = []
    lines = text.split("\n")
    n_open = text.count("{")
    n_close = text.count("}")
    n_paren_o = text.count("(")
    n_paren_c = text.count(")")
    chk = {"header": text.startswith("#usda 1.0"), "brace_balance": n_open == n_close,
           "paren_balance": n_paren_o == n_paren_c, "lines": len(lines),
           "links": len(link_mesh_meta), "joints": len(joint_meta),
           "meshes": sum(1 for v in link_mesh_meta.values() if v["faces"] > 0),
           "mesh_arrays_consistent": True, "indices_in_range": True,
           "problems": problems,
           "boundary": "结构级自检（本机没有 USD 运行库）：括号/圆括号配平、数组长度与索引范围一致性；"
                       "不是 schema 校验"}
    if not chk["header"]:
        problems.append("首行不是 #usda 1.0")
    if not chk["brace_balance"]:
        problems.append("花括号不配平: { =%d, } =%d" % (n_open, n_close))
    if not chk["paren_balance"]:
        problems.append("圆括号不配平: ( =%d, ) =%d" % (n_paren_o, n_paren_c))
    # 逐块检查 Mesh 数组一致性
    def _arr(s):
        """取行内最后一个 [...] 的内容（注意 int[] 这种声明里也有方括号）。"""
        i, j = s.rfind("["), s.rfind("]")
        if i < 0 or j < i:
            raise ValueError("行内没有数组: %s" % s[:80])
        return s[i + 1:j]

    i = 0
    n_mesh = 0
    while i < len(lines):
        ln = lines[i]
        if "faceVertexCounts" in ln:
            n_mesh += 1
            try:
                counts = [int(x) for x in _arr(ln).split(",") if x.strip()]
                idx = [int(x) for x in _arr(lines[i + 1]).split(",") if x.strip()]
                pts = lines[i + 2].count("(")
                if sum(counts) != len(idx):
                    chk["mesh_arrays_consistent"] = False
                    problems.append("第 %d 行 Mesh：sum(faceVertexCounts)=%d ≠ len(faceVertexIndices)=%d"
                                    % (i + 1, sum(counts), len(idx)))
                if idx and (min(idx) < 0 or max(idx) >= pts):
                    chk["indices_in_range"] = False
                    problems.append("第 %d 行 Mesh：索引范围 [%d, %d] 越界（points=%d）"
                                    % (i + 1, min(idx), max(idx), pts))
                if not counts or any(c != 3 for c in counts):
                    problems.append("第 %d 行 Mesh：存在非三角面（本项目只导出三角面）" % (i + 1))
            except Exception as e:
                chk["mesh_arrays_consistent"] = False
                problems.append("第 %d 行 Mesh 数组解析失败: %s" % (i + 1, str(e)[:120]))
        i += 1
    if n_mesh != chk["meshes"]:
        problems.append("Mesh 块数 %d ≠ 有几何的 link 数 %d" % (n_mesh, chk["meshes"]))
        chk["mesh_arrays_consistent"] = False
    if len(joint_meta) != sum(1 for ln in lines if "dsh:kind" in ln):
        problems.append("关节块数 %d ≠ dsh:kind 出现次数 %d"
                        % (len(joint_meta), sum(1 for ln in lines if "dsh:kind" in ln)))
    if os.path.getsize(path) <= 0:
        problems.append("文件是空的")
    chk["ok"] = bool(chk["header"] and chk["brace_balance"] and chk["paren_balance"]
                     and chk["mesh_arrays_consistent"] and chk["indices_in_range"]
                     and chk["links"] > 0 and not any("≠" in p or "不配平" in p for p in problems))
    return chk


# ============================================================ status / help

def motion_status():
    st = _store()
    js = {}
    counts = {"supported": 0, "refuted": 0, "unresolved": 0}
    for k, v in st["joints"].items():
        mv = (v.get("measured") or {}).get("verdict")
        av = v.get("axis_verdict") or "unresolved"
        vv = mv or av
        counts[vv if vv in counts else "unresolved"] = counts.get(vv if vv in counts else "unresolved", 0) + 1
        js[k] = {"kind": v["kind"], "parent": v["parent"], "child": v["child"],
                 "axis": v.get("axis"), "anchor": v.get("anchor"),
                 "axis_source": v.get("axis_source"), "axis_verdict": av,
                 "limits": v.get("limits"), "note": v.get("note"),
                 "measured": ({"verdict": mv, "moved": (v.get("measured") or {}).get("moved"),
                               "phases": len((v.get("measured") or {}).get("phases") or []),
                               "reason": (v.get("measured") or {}).get("reason"),
                               "restored_ok": (v.get("measured") or {}).get("restored_ok"),
                               "t": (v.get("measured") or {}).get("t")} if v.get("measured") else None),
                 "warnings": v.get("warnings") or []}
    return _j({"ok": True, "op": "status", "version": MOTION_VERSION,
               "joints": js, "joint_count": len(js),
               "verdict_counts": counts,
               "exports": st["exports"], "export_count": len(st["exports"]),
               "log_tail": st["log"][-10:],
               "units": {"length": "scene_unit（1 单位 = %g m）" % _scene_mpu(), "angle": "deg"},
               "boundary": "关节轴可实测（旋转对称/接触带）；扫掠是运动学验证不是物理验证；"
                           "USDA 不是完整 UsdPhysics schema"})


def motion_help():
    return _j({
        "version": MOTION_VERSION,
        "ops": {
            "joint": "motion_joint(jid, parent, child, kind='revolute', axis=None, anchor=None, limits=None, "
                     "note='') —— parent/child 是对象名或对象名列表（刚性组）；axis/anchor 不给则实测推断",
            "infer_axis": "motion_infer_axis(jid=None, objects=None, parent=None, sample_cap=3000, "
                          "min_score=0.55, max_distance=None) → 证据 + 三态（薄平板绕铰链轴无旋转对称 ⇒ unresolved）",
            "joints": "motion_joints() → 已登记关节 + 轴判定 + 实测摘要",
            "measure": "motion_measure(jid=None, phases=8, sweep_deg=60, margin_frac=0.05, exclude=None, "
                       "chain_depth='full', max_candidates=64, max_samples=400) → 扫掠（干涉对+最小间距）",
            "export_urdf": "motion_export_urdf(dir, name='model', joints=None, meters_per_unit=0.001, "
                           "density=1000, masses=None, mesh_format='obj', effort=1000, velocity=100) "
                           "→ <dir>/<name>.urdf + <dir>/meshes/*.obj + 结构自检",
            "export_usda": "motion_export_usda(dir, name='model', joints=None, meters_per_unit=0.001, "
                           "density=1000, masses=None) → 最小可用 ASCII USDA + 结构自检",
            "status": "motion_status() → 关节 + 判定 + 导出历史（K.dsh_motion）",
            "selftest": "motion_selftest() → 合成铰链自检（无头可跑，自带场景/清理）",
            "help": "motion_help()",
        },
        "typical": "K.dsh_motion_api['dispatch']('joint', {'jid':'hinge','parent':['Base'],"
                   "'child':['Door','Pin'],'kind':'revolute','limits':[-90,90]}) → "
                   "K.dsh_motion_api['dispatch']('measure', {'jid':'hinge','phases':8,'sweep_deg':60}) → "
                   "K.dsh_motion_api['dispatch']('export_urdf', {'dir': K.out_dir, 'name':'door'})",
        "rules": {
            "verdict": "supported / refuted / unresolved 三态；证据不足一律 unresolved（不许二选一凑）",
            "ok": "ok=false ⇔ unresolved 或空产出（0 link / 0 joint / 没轴 / 没 child 几何）",
            "units": "revolute 的 limits/相位=deg；prismatic=场景单位；间距同时给 scene_unit 与 m；URDF=SI",
            "restore": "扫掠结束逐对象对拍 matrix_world（全部对象），mismatch 写在 restored 里",
        },
        "boundary": [
            "扫掠 = 运动学 + BVH 干涉，不是 Isaac 那种物理验证（无质量/摩擦/驱动动力学）",
            "最小间距是采样近似（偏保守/偏大）；干涉判定用 BVH 三角面相交（精确）",
            "零干涉需要至少一个'旁观者'对象，否则是空洞证据 → 该情形判 unresolved",
            "轴推断只对可辨识几何有效；接触带证据需要父组 + max_distance 内有足够接触点",
            "两条证据（旋转对称 / 接触带）矛盾时一律 unresolved，不替用户选",
            "导出要求各关节分组互不重叠（一个对象只能属于一个 link），否则 ok=false",
            "USDA 是最小可用 ASCII，不是完整 UsdPhysics schema",
        ],
    })


# ============================================================ 自检

def _mk_box(name, size, loc):
    sx, sy, sz = [float(x) for x in size]
    me = bpy.data.meshes.new("MSH_" + name)
    v = [(-sx / 2, -sy / 2, -sz / 2), (sx / 2, -sy / 2, -sz / 2), (sx / 2, sy / 2, -sz / 2), (-sx / 2, sy / 2, -sz / 2),
         (-sx / 2, -sy / 2, sz / 2), (sx / 2, -sy / 2, sz / 2), (sx / 2, sy / 2, sz / 2), (-sx / 2, sy / 2, sz / 2)]
    f = [(0, 3, 2, 1), (4, 5, 6, 7), (0, 1, 5, 4), (1, 2, 6, 5), (2, 3, 7, 6), (3, 0, 4, 7)]
    me.from_pydata(v, [], f)
    me.update()
    ob = bpy.data.objects.new(name, me)
    ob.location = [float(x) for x in loc]
    bpy.context.scene.collection.objects.link(ob)
    return ob


def _mk_cyl(name, radius, depth, loc, segs=32):
    """沿 +Z 的圆柱；segs=32 ⇒ 90° 旋转精确自映射（对称轴可辨识的合成证据）。"""
    r, d = float(radius), float(depth)
    v, f = [], []
    for i in range(segs):
        a = 2.0 * math.pi * i / segs
        x, y = r * math.cos(a), r * math.sin(a)
        v.append((x, y, -d / 2.0))
        v.append((x, y, d / 2.0))
    for i in range(segs):
        j = (i + 1) % segs
        f.append((2 * i, 2 * j, 2 * j + 1, 2 * i + 1))
    f.append(tuple(reversed(range(0, 2 * segs, 2))))
    f.append(tuple(range(1, 2 * segs, 2)))
    me = bpy.data.meshes.new("MSH_" + name)
    me.from_pydata(v, [], f)
    me.update()
    ob = bpy.data.objects.new(name, me)
    ob.location = [float(x) for x in loc]
    bpy.context.scene.collection.objects.link(ob)
    return ob


def motion_selftest(keep=False):
    """合成自检：造铰链（底板 + 门板 + 沿 Z 的轴）→ 实测轴 → 无阻挡扫掠 → 加挡块扫掠 →
    URDF/USDA 结构自检 → 还原证明。不依赖用户场景（预先存在的对象一律 exclude），最后清理。
    """
    t0 = time.perf_counter()
    checks = []

    def _chk(name, cond, detail):
        checks.append({"name": name, "ok": bool(cond), "detail": detail})
        return bool(cond)

    K = _kernel()
    made_objs, made_meshes, made_dirs = [], [], []
    pre_names = sorted(o.name for o in bpy.data.objects)
    outdir = os.path.join(_out_dir(), "motion_selftest")
    res = {}
    try:
        # ---- 场景：铰链轴 = 世界 Z 轴（过 x=0,y=0）
        base = _mk_box("MS_Base", (1.0, 0.4, 0.06), (-0.05, 0.0, -0.06))
        door = _mk_box("MS_Door", (0.5, 0.04, 0.5), (0.29, 0.0, 0.25))     # x∈[0.04,0.54]
        pin = _mk_cyl("MS_Pin", 0.02, 0.6, (0.0, 0.0, 0.25))               # 轴 = Z，过原点
        block = _mk_box("MS_Blocker", (0.08, 0.08, 0.6), (0.40, 0.21, 0.25))  # 挡在 +y 扫掠路径上
        stand = _mk_box("MS_Stand", (0.2, 0.2, 0.2), (-1.5, 0.0, 0.1))     # 孤儿 link 用
        bolt = _mk_box("MS_Bolt", (0.05, 0.05, 0.05), (-1.5, 0.2, 0.1))
        for ob in (base, door, pin, block, stand, bolt):
            made_objs.append(ob)
            made_meshes.append(ob.data)
        bpy.context.view_layer.update()
        _chk("scene_built", len(made_objs) == 6, {"objects": [o.name for o in made_objs]})

        # ---- ① 登记关节（不给 axis/anchor → 实测推断）
        r_joint = json.loads(motion_joint("selftest_hinge", ["MS_Base"], ["MS_Door", "MS_Pin"],
                                          kind="revolute", limits=[-30, 30], note="自检铰链"))
        inf = json.loads(motion_infer_axis(jid="selftest_hinge"))
        true_axis = np.array([0.0, 0.0, 1.0])
        ang = _angle_deg(inf.get("axis") or [0, 0, 0], true_axis) if inf.get("axis") else None
        anchor = inf.get("anchor") or [0, 0, 0]
        anchor_off = _dist_point_line(anchor, [0.0, 0.0, 0.25], true_axis)
        res["infer"] = {"verdict": inf.get("verdict"), "axis": inf.get("axis"), "anchor": inf.get("anchor"),
                        "angle_to_true_axis_deg": _r(ang, 4) if ang is not None else None,
                        "anchor_offset_from_hinge_line": _r(anchor_off),
                        "reason": inf.get("reason"),
                        "per_object": [{"label": c.get("label"), "score": c.get("score"),
                                        "passed": c.get("passed"), "degenerate": c.get("degenerate"),
                                        "snapped": c.get("snapped")} for c in
                                       (inf.get("evidence") or {}).get("per_object", [])]}
        _chk("infer_verdict_not_refuted", inf.get("verdict") in ("supported", "unresolved"),
              {"verdict": inf.get("verdict"), "reason": inf.get("reason")})
        _chk("infer_axis_clearance_lt_5deg", (ang is not None and ang < 5.0),
              {"angle_deg": (None if ang is None else _r(ang, 4)), "axis": inf.get("axis"),
               "note": "销（圆柱）绕 Z 旋转对称 ⇒ 轴可辨识"})
        _chk("infer_anchor_on_hinge_line", (anchor_off is not None and anchor_off * _scene_mpu() <= 0.005),
              {"offset_scene_unit": _r(anchor_off), "offset_m": _r((anchor_off or 0) * _scene_mpu()),
               "tol_m": 0.005})
        _chk("joint_registered_with_axis", bool(r_joint.get("ok") and r_joint.get("axis")),
              {"ok": r_joint.get("ok"), "axis": r_joint.get("axis"), "axis_verdict": r_joint.get("axis_verdict"),
               "warnings": r_joint.get("warnings")})

        # ---- 孤儿 link（导出单树合成）用
        motion_joint("selftest_spare", ["MS_Stand"], ["MS_Bolt"], kind="spherical", note="自检孤儿+降级")

        # ---- ② 无阻挡扫掠：必须 supported
        before = {o.name: [float(x) for row in o.matrix_world for x in row] for o in bpy.data.objects}
        m1 = json.loads(motion_measure(jid="selftest_hinge", phases=8, sweep_deg=60,
                                       exclude=pre_names + ["MS_Blocker"]))
        after1 = {o.name: [float(x) for row in o.matrix_world for x in row] for o in bpy.data.objects}
        res["sweep_clean"] = {"verdict": m1.get("verdict"), "moved": m1.get("moved"),
                              "max_displacement_m": m1.get("max_displacement_m"),
                              "phases": len(m1.get("phases") or []),
                              "candidates": m1.get("candidates"),
                              "restored_ok": (m1.get("restored") or {}).get("ok"),
                              "reason": m1.get("reason"),
                              "phase_values": [(p.get("value"), p.get("unit"),
                                                len(p.get("collisions") or [])) for p in (m1.get("phases") or [])]}
        _chk("sweep_clean_supported", m1.get("verdict") == "supported" and m1.get("moved") is True,
              {"verdict": m1.get("verdict"), "moved": m1.get("moved"), "reason": m1.get("reason")})
        d1, bad1, exact1 = _snap_diff(before, after1)
        res["sweep_clean"]["restore_diff"] = {"max_delta": d1, "bad": bad1, "exact": exact1,
                                             "tol": TOL_MAT}
        _chk("sweep_clean_restored", (m1.get("restored") or {}).get("ok") is True and not bad1,
              {"restored": m1.get("restored"), "diff": res["sweep_clean"]["restore_diff"]})

        # ---- ③ 挡块进扫掠路径：必须 refuted 且点名
        m2 = json.loads(motion_measure(jid="selftest_hinge", phases=8, sweep_deg=60, exclude=pre_names))
        after2 = {o.name: [float(x) for row in o.matrix_world for x in row] for o in bpy.data.objects}
        off = m2.get("offenders") or []
        res["sweep_blocked"] = {"verdict": m2.get("verdict"), "offenders": off,
                                "collisions": [{"phase": c.get("phase"), "other": c.get("other"),
                                                "min_gap_m": c.get("min_gap_m"),
                                                "penetration_m": c.get("penetration_m")}
                                               for c in (m2.get("collisions") or [])],
                                "reason": m2.get("reason"),
                                "restored_ok": (m2.get("restored") or {}).get("ok")}
        _chk("sweep_block_refuted", m2.get("verdict") == "refuted" and "MS_Blocker" in off,
              {"verdict": m2.get("verdict"), "offenders": off, "reason": m2.get("reason")})
        _chk("sweep_block_penetration_negative",
              any((c.get("min_gap_m") is not None and c["min_gap_m"] < 0) for c in (m2.get("collisions") or [])),
              {"collisions": res["sweep_blocked"]["collisions"]})
        d2, bad2, exact2 = _snap_diff(before, after2)
        res["sweep_blocked"]["restore_diff"] = {"max_delta": d2, "bad": bad2, "exact": exact2,
                                                "tol": TOL_MAT}
        _chk("sweep_block_restored", (m2.get("restored") or {}).get("ok") is True and not bad2,
              {"restored": m2.get("restored"), "diff": res["sweep_blocked"]["restore_diff"]})

        # ---- ④ 导出结构自检（URDF + USDA）
        os.makedirs(outdir, exist_ok=True)
        made_dirs.append(outdir)
        u = json.loads(motion_export_urdf(dir=outdir, name="selftest_model",
                                          joints=["selftest_hinge", "selftest_spare"],
                                          meters_per_unit=0.001, density=1000.0))
        s = json.loads(motion_export_usda(dir=outdir, name="selftest_model",
                                          joints=["selftest_hinge", "selftest_spare"],
                                          meters_per_unit=0.001, density=1000.0))
        res["urdf"] = {"ok": u.get("ok"), "path": u.get("path"),
                       "self_check": u.get("self_check"), "warnings": u.get("warnings"),
                       "links": [l.get("link") for l in (u.get("links") or [])],
                       "joints": [(j.get("joint"), j.get("type"), j.get("synthesized"),
                                   j.get("degraded_from")) for j in (u.get("joints") or [])]}
        res["usda"] = {"ok": s.get("ok"), "path": s.get("path"),
                       "self_check": s.get("self_check"), "warnings": s.get("warnings"),
                       "links": s.get("links"), "joints": s.get("joints")}
        _chk("urdf_self_check_ok", bool(u.get("ok") and (u.get("self_check") or {}).get("ok")),
              {"ok": u.get("ok"), "self_check": u.get("self_check"), "warnings": u.get("warnings")})
        _chk("urdf_degraded_reported",
              any("降级" in str(w) for w in (u.get("warnings") or [])),
              {"warnings": u.get("warnings")})
        _chk("urdf_orphan_synthesized",
              any("不可达" in str(w) for w in (u.get("warnings") or []))
              and any(j.get("synthesized") for j in (u.get("joints") or [])),
              {"warnings": u.get("warnings"), "joints": res["urdf"]["joints"]})
        _chk("usda_self_check_ok", bool(s.get("ok") and (s.get("self_check") or {}).get("ok")),
              {"ok": s.get("ok"), "self_check": s.get("self_check"), "warnings": s.get("warnings")})
        # 单位体检：本场景是米制（0.5 单位 ≈ 0.5 m），却按 0.001 导出 ⇒ 必须自己报「单位可疑」
        _chk("units_guard_fires_on_implausible_scale",
              (u.get("units") or {}).get("scene_units_suspect") is True
              and any("单位可疑" in str(w) for w in (u.get("warnings") or [])),
              {"units": u.get("units"), "warnings": u.get("warnings")})
        # 小量级不能被写成 0：质量/惯量必须保住数量级（早先 "%.9f" 会把 8.6e-11 写成 0）
        import re as _re
        _txt = open(u["path"], encoding="utf-8").read()
        _zeros = _re.findall(r'(?:mass value|inertia ixx|iyy|izz)="0"', _txt)
        _chk("SI 小量级不塌成 0（质量/惯量保数量级）", not _zeros,
              {"zeros": _zeros[:6], "sample": _re.findall(r'<mass value="[^"]+"', _txt)[:4]})

        # ---- ⑤ 空产出 / 无轴 必须失败（硬规则）
        empty_joint = json.loads(motion_joint("selftest_bad", ["MS_Nope"], ["MS_AlsoNope"]))
        empty_export = json.loads(motion_export_urdf(dir=outdir, name="empty", joints=[]))
        no_axis_measure = json.loads(motion_measure(jid="selftest_spare", phases=4))
        res["hard_rules"] = {"empty_joint_ok": empty_joint.get("ok"),
                             "empty_export_ok": empty_export.get("ok"),
                             "empty_export_error": empty_export.get("error"),
                             "no_axis_measure_ok": no_axis_measure.get("ok"),
                             "no_axis_measure_verdict": no_axis_measure.get("verdict"),
                             "no_axis_measure_reason": no_axis_measure.get("reason")}
        _chk("empty_joint_must_fail", empty_joint.get("ok") is False, {"resp": empty_joint})
        _chk("empty_export_must_fail", empty_export.get("ok") is False, {"resp": empty_export.get("error")})
        _chk("no_axis_measure_unresolved",
              no_axis_measure.get("ok") is False and no_axis_measure.get("verdict") == "unresolved",
              {"resp": res["hard_rules"]})

        # ---- ⑥ 轴推断的三态（判据真的会咬人）：接触带可定轴 / 薄板必须 unresolved / 假设轴可被反驳
        bar2 = _mk_box("MS_Bar", (1.0, 0.08, 0.08), (-3.0, 0.0, 0.0))          # 顶面 z=0.04
        pl_a = _mk_box("MS_PlateNS", (0.5, 0.04, 0.3), (-3.4, 0.0, 0.17))      # 非方形：无旋转对称
        pl_b = _mk_box("MS_PlateSQ", (0.5, 0.04, 0.5), (-2.6, 0.0, 0.27))      # 方形：绕法线也对称
        pad2 = _mk_box("MS_Pad", (0.6, 0.3, 0.04), (-5.0, 0.0, 0.0))
        flap = _mk_box("MS_Flap", (0.5, 0.03, 0.4), (-5.0, 0.0, 0.25))         # 与 Pad 有 0.03 间隙
        for ob in (bar2, pl_a, pl_b, pad2, flap):
            made_objs.append(ob)
            made_meshes.append(ob.data)
        bpy.context.view_layer.update()
        true_hinge = np.array([1.0, 0.0, 0.0])

        j_contact = json.loads(motion_joint("selftest_contact", ["MS_Bar"], ["MS_PlateNS"],
                                           kind="revolute", note="接触带定轴"))
        ax_c = j_contact.get("axis") or [0, 0, 0]
        ang_c = _angle_deg(ax_c, true_hinge)
        anc_off_c = _dist_point_line(j_contact.get("anchor") or [0, 0, 0], [0.0, 0.0, 0.04], true_hinge)
        res["infer_contact"] = {"verdict": j_contact.get("axis_verdict"), "source": j_contact.get("axis_source"),
                                "axis": j_contact.get("axis"), "anchor": j_contact.get("anchor"),
                                "angle_to_true_deg": _r(ang_c, 4),
                                "anchor_offset": _r(anc_off_c), "warnings": j_contact.get("warnings")}
        _chk("contact_strip_axis_supported",
              j_contact.get("axis_verdict") == "supported" and j_contact.get("axis_source") == "contact_region"
              and ang_c is not None and ang_c < 5.0,
              res["infer_contact"])
        _chk("contact_strip_anchor_near_hinge_line", anc_off_c is not None and anc_off_c * _scene_mpu() <= 0.005,
              {"offset_scene_unit": _r(anc_off_c), "offset_m": _r((anc_off_c or 0) * _scene_mpu()), "tol_m": 0.005})

        j_slab = json.loads(motion_joint("selftest_slab", ["MS_Pad"], ["MS_Flap"], kind="revolute",
                                         note="薄板无销：必须 unresolved"))
        inf_slab = json.loads(motion_infer_axis(jid="selftest_slab"))
        res["infer_slab_unresolved"] = {"joint_axis_verdict": j_slab.get("axis_verdict"),
                                        "axis": j_slab.get("axis"),
                                        "verdict": inf_slab.get("verdict"),
                                        "best_score": [{"label": c.get("label"), "score": c.get("score"),
                                                        "degenerate": c.get("degenerate")}
                                                       for c in (inf_slab.get("evidence") or {}).get("per_object", [])],
                                        "contact": {k: (inf_slab.get("evidence") or {}).get("contact", {}).get(k)
                                                    for k in ("available", "reason", "close_points", "elongation")},
                                        "missing_evidence": inf_slab.get("missing_evidence"),
                                        "reason": inf_slab.get("reason")}
        _chk("slab_without_pin_is_unresolved",
              j_slab.get("axis") is None and j_slab.get("axis_verdict") == "unresolved"
              and inf_slab.get("verdict") == "unresolved" and inf_slab.get("missing_evidence"),
              res["infer_slab_unresolved"])

        j_sq = json.loads(motion_joint("selftest_square", ["MS_Bar"], ["MS_PlateSQ"], kind="revolute",
                                       note="方形板：对称轴与接触带矛盾"))
        inf_sq = json.loads(motion_infer_axis(jid="selftest_square"))
        res["infer_conflict"] = {"joint_axis_verdict": j_sq.get("axis_verdict"),
                                 "verdict": inf_sq.get("verdict"),
                                 "axis_conflict": inf_sq.get("axis_conflict"),
                                 "reason": inf_sq.get("reason")}
        _chk("square_plate_conflict_unresolved",
              inf_sq.get("verdict") == "unresolved" and bool(inf_sq.get("axis_conflict"))
              and inf_sq.get("axis") is None,
              res["infer_conflict"])

        motion_joint("selftest_wrongaxis", ["MS_Base"], ["MS_Pin"], kind="revolute",
                     axis=[1.0, 0.0, 0.0], anchor=[0.0, 0.0, 0.25], note="故意给错轴（child 只有销）")
        inf_wrong = json.loads(motion_infer_axis(jid="selftest_wrongaxis"))
        res["infer_wrong_axis"] = {"verdict": inf_wrong.get("verdict"), "reason": inf_wrong.get("reason"),
                                   "nearest": ((inf_wrong.get("hypothesis") or {}).get("evidence") or {}).get("candidates")}
        _chk("wrong_axis_hypothesis_refuted", inf_wrong.get("verdict") == "refuted",
              res["infer_wrong_axis"])

        motion_joint("selftest_ambiguous", ["MS_Base"], ["MS_Door", "MS_Pin"], kind="revolute",
                     axis=[1.0, 0.0, 0.0], anchor=[0.0, 0.0, 0.25], note="假设方向只是近失候选")
        inf_amb = json.loads(motion_infer_axis(jid="selftest_ambiguous"))
        res["infer_ambiguous"] = {"verdict": inf_amb.get("verdict"), "reason": inf_amb.get("reason"),
                                  "nearest": ((inf_amb.get("hypothesis") or {}).get("evidence") or {}).get("candidates")}
        _chk("ambiguous_hypothesis_not_refuted", inf_amb.get("verdict") == "unresolved",
              res["infer_ambiguous"])
    except Exception as e:
        checks.append({"name": "selftest_exception", "ok": False,
                       "detail": "%s: %s" % (type(e).__name__, str(e)[:300]),
                       "traceback": traceback.format_exc()[-1200:]})
    finally:
        removed, failed = [], []
        for ob in made_objs:
            try:
                nm = str(ob.name)          # 先取名：remove 之后 StructRNA 就失效了
            except Exception:
                nm = "?"
            try:
                bpy.data.objects.remove(ob, do_unlink=True)
                removed.append(nm)
            except Exception as e:
                failed.append({"name": nm, "error": str(e)[:120]})
        for me in made_meshes:
            try:
                bpy.data.meshes.remove(me, do_unlink=True)
            except Exception:
                pass
        if not keep:
            for d in made_dirs:
                try:
                    shutil.rmtree(d, ignore_errors=True)
                except Exception:
                    pass
        try:
            bpy.context.view_layer.update()
        except Exception:
            pass
        res["cleaned"] = {"removed_objects": removed, "failed": failed,
                          "objects_left": sorted(o.name for o in bpy.data.objects),
                          "pre_existing": pre_names,
                          "dir_removed": (not keep),
                          "dir": outdir}

    fails = [c for c in checks if not c["ok"]]
    ok = not fails
    if not (res.get("cleaned") or {}).get("removed_objects"):
        ok = False
        fails.append({"name": "cleanup_ran", "ok": False, "detail": "清理没有执行"})
    _log("selftest", {"ok": ok, "fails": [c["name"] for c in fails]})
    return _j({
        "ok": ok, "op": "selftest", "version": MOTION_VERSION,
        "checks": checks, "fails": fails, "fail_count": len(fails),
        "measured": res,
        "expect": ("销（圆柱，沿 Z）可辨识 ⇒ 轴实测 supported 且与真轴夹角 <5°、锚点在铰链线上；"
                   "无阻挡 8 相位扫掠 = supported（零干涉且确实位移）；把挡块放进扫掠路径 ⇒ 同一关节变 "
                   "refuted 且点名 MS_Blocker（最小间距 <0）；URDF/USDA 结构自检全过（含 spherical 降级与"
                   "孤儿 link 合成单树）；扫掠前后所有对象 matrix_world 逐项相等；空产出/无轴必须 ok=false"),
        "notes": ["自检不碰用户场景：预先存在的对象一律 exclude 且不删",
                  "临时对象/文件在 finally 里清理（keep=true 可保留产物用于排查）"],
        "ms": int((time.perf_counter() - t0) * 1000),
    })


# ============================================================ 派发

def motion_reset():
    K = _kernel()
    if K is None:
        return _j({"ok": False, "error": "需要持久内核 K"})
    K.dsh_motion = {"joints": {}, "exports": [], "log": [], "seq": 0}
    return _j({"ok": True, "op": "reset", "note": "只清 K.dsh_motion（不动场景、不删文件）"})


def motion_dispatch(op, args=None):
    """统一入口：工具侧发 {op, args}；args 里再包一层 {"args": {...}} 也解包（与契约层同口径）。"""
    if isinstance(args, str):
        try:
            args = json.loads(args) if args.strip() else {}
        except Exception:
            args = {}
    kw = {}
    for k, v in (args or {}).items():
        if k == "args" and isinstance(v, dict):
            kw.update(v)
        else:
            kw[k] = v
    fn = motion_ops().get(str(op))
    if fn is None:
        return _j({"ok": False, "error": "unknown motion op", "op": op, "ops": sorted(motion_ops())})
    try:
        return fn(**kw)
    except TypeError as e:
        return _j({"ok": False, "error": "参数不匹配: %s" % str(e)[:200], "op": op,
                   "given": sorted(kw), "sig_hint": motion_help()})
    except Exception as e:
        return _j({"ok": False, "error": "%s: %s" % (type(e).__name__, str(e)[:200]), "op": op,
                   "traceback": traceback.format_exc()[-800:]})


def motion_ops():
    return {"joint": motion_joint, "joints": motion_joints, "infer_axis": motion_infer_axis,
            "measure": motion_measure, "export_urdf": motion_export_urdf,
            "export_usda": motion_export_usda, "status": motion_status, "help": motion_help,
            "selftest": motion_selftest, "reset": motion_reset}


import sys as _sys
_K = _sys.modules.get("dsh_rt_kernel")
if _K is not None:
    _K.dsh_motion_api = {"version": MOTION_VERSION, "dispatch": motion_dispatch,
                         "joint": motion_joint, "joints": motion_joints,
                         "infer_axis": motion_infer_axis, "measure": motion_measure,
                         "export_urdf": motion_export_urdf, "export_usda": motion_export_usda,
                         "status": motion_status, "help": motion_help,
                         "selftest": motion_selftest, "reset": motion_reset,
                         "ops": motion_ops}