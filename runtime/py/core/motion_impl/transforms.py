# -*- coding: utf-8 -*-
"""Split from motion.py; behavior preserved by the facade."""
from .common import *  # noqa: F401,F403



# ============================================================ 扫掠验证

_TF_FIELDS = ("location", "scale", "rotation_euler", "rotation_quaternion",
              "delta_location", "delta_scale", "delta_rotation_euler", "delta_rotation_quaternion")
TOL_MAT = 1e-6          # matrix_world 对拍容差（Blender 内部按 float32 存 transform）

# v2.6.1：批量 update 工具优先用 K.dsh_kit.BatchUpdates；旧内核/内联测试里没有时用本地兜底，
# 避免"kit 与模块版本不一致"直接 AttributeError（模块自身不硬依赖新 kit）。
_BatchUpdates = getattr(_KIT, "BatchUpdates", None)
if _BatchUpdates is None:
    class _BatchUpdates:  # noqa: F811 - 本地兜底，行为与 kit 版一致
        def __init__(self, view_layer=None):
            import bpy as _bpy
            self.vl = view_layer or _bpy.context.view_layer
            self.stages = 0
            self.updates = 0
        def flush(self):
            self.vl.update()
            self.updates += 1
            return self


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

    v2.6.1 性能：字段写入不再逐对象 update —— 先全部写完、一次 update 再统一核对；
    只有兜底写矩阵的对象才需要额外 update。语义与旧实现一致（native motion E2E 的逐对象 matrix_world 还原检查守）。

    返回 (mismatches, pre_delta, wrote)：pre_delta = 还原前发现的最大偏差，
    还原后的残余偏差写在各对象的对拍里（mismatches 为空即逐项相等）。
    """
    touched = set(touched or [])
    batch = _BatchUpdates()
    batch.flush()                            # 起始基线（与旧实现一致）
    mism, pre_max, post_max, wrote = [], 0.0, 0.0, []
    pending = []

    # 第一遍：只写字段，不逐对象 update
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
        pending.append((ob, rec))

    if not pending:
        return mism, pre_max, post_max, wrote

    batch.flush()                            # ★ 一次 update 替代"每对象一次"
    residual = {}
    fallback = []
    for ob, rec in pending:
        now2 = _mat_now(ob)
        d2 = max(abs(a - b) for a, b in zip(rec["matrix"], now2))
        residual[ob.name] = d2
        if d2 > TOL_MAT:
            fallback.append((ob, rec))

    if fallback:
        for ob, rec in fallback:
            try:                            # 兜底：直接写矩阵
                ob.matrix_world = _mat_of(rec)
            except Exception as e:
                mism.append({"name": ob.name, "reason": "矩阵还原失败: %s" % str(e)[:120]})
                residual.pop(ob.name, None)
        batch.flush()
        # 兜底写矩阵可能影响子级 / 与其它待核对象互为父子 ⇒ 全部复验；仍不齐的再单对象修一次
        for ob, rec in pending:
            if ob.name not in residual:
                continue
            now3 = _mat_now(ob)
            d3 = max(abs(a - b) for a, b in zip(rec["matrix"], now3))
            residual[ob.name] = d3
            if d3 > TOL_MAT:
                try:
                    ob.matrix_world = _mat_of(rec)
                    batch.flush()
                    now4 = _mat_now(ob)
                    d4 = max(abs(a - b) for a, b in zip(rec["matrix"], now4))
                    residual[ob.name] = d4
                    if d4 > TOL_MAT:
                        mism.append({"name": ob.name, "reason": "transform 未还原", "residual": d4,
                                     "want": _rl(rec["matrix"], 9), "got": _rl(now4, 9)})
                except Exception as e:
                    mism.append({"name": ob.name, "reason": "矩阵还原失败: %s" % str(e)[:120]})
                    residual.pop(ob.name, None)

    for ob, rec in pending:
        if ob.name in residual:
            post_max = max(post_max, residual[ob.name])
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
