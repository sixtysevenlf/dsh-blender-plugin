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
    blender_run(kind="headless", engine="none", preload="motion", script="print(K.dsh_motion_api['selftest']())")
    K.dsh_motion_api["dispatch"]("joint", {"jid": "hinge", "parent": "Base", "child": "Door"})
    K.dsh_motion_api["dispatch"]("measure", {"jid": "hinge", "phases": 8, "sweep_deg": 60})

硬规则：空产出必须 ok=false；判定三态（supported / refuted / unresolved，证据不足就 unresolved）；
        数值带单位；扫掠结束**必须**把场景 transform 完整还原（逐对象对拍并报 mismatch）。
"""
import math
import os
import time

import bpy
import numpy as np

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


import sys as _sys_kit
_KIT = getattr(_sys_kit.modules.get("dsh_rt_kernel"), "dsh_kit", None)
if _KIT is None:
    raise RuntimeError("motion 需要共享内核 K.dsh_kit（由 KERNEL_BOOTSTRAP 注入）")


_j = _KIT.j  # 共享内核（原自带实现已删）
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


__all__ = [name for name in list(globals()) if not name.startswith("__")]
