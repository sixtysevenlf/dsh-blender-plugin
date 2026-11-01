# -*- coding: utf-8 -*-
"""Split from audit.py; behavior preserved by the facade."""
from .common import *  # noqa: F401,F403
from .conn_helpers import _r, _units, _world_tris

def _read_obj(path):
    import numpy as np
    V, F = [], []
    with open(path, "r", encoding="utf-8", errors="ignore") as fh:
        for line in fh:
            if line.startswith("v "):
                p = line.split()
                V.append((float(p[1]), float(p[2]), float(p[3])))
            elif line.startswith("f "):
                idx = []
                for tok in line.split()[1:]:
                    t = tok.split("/")[0]
                    if t:
                        i = int(t)
                        idx.append(i - 1 if i > 0 else len(V) + i)
                for k in range(1, len(idx) - 1):
                    F.append((idx[0], idx[k], idx[k + 1]))
    return np.asarray(V, dtype=np.float64), np.asarray(F, dtype=np.int64)


def _read_stl(path):
    import numpy as np
    with open(path, "rb") as fh:
        head = fh.read(84)
        if len(head) < 84:
            raise ValueError("STL 太短：%s" % path)
        n = int(np.frombuffer(head[80:84], dtype="<u4")[0])
        raw = fh.read(50 * n)
        if n > 0 and len(raw) == 50 * n:
            rec = np.frombuffer(raw, dtype=np.uint8).reshape(n, 50)
            tri = rec[:, 12:48].copy().view("<f4").reshape(n, 3, 3).astype(np.float64)
            return tri.reshape(-1, 3), np.arange(n * 3, dtype=np.int64).reshape(n, 3)
    V, F = [], []
    with open(path, "r", encoding="utf-8", errors="ignore") as fh:
        cur = []
        for line in fh:
            t = line.strip().split()
            if len(t) == 4 and t[0].lower() == "vertex":
                cur.append((float(t[1]), float(t[2]), float(t[3])))
                if len(cur) == 3:
                    base = len(V)
                    V.extend(cur)
                    F.append((base, base + 1, base + 2))
                    cur = []
    import numpy as np
    return np.asarray(V, dtype=np.float64), np.asarray(F, dtype=np.int64)


def _src_tris(src):
    """对象名 / 集合名 / .obj / .stl 路径 → (tris, label)。"""
    import os
    s = str(src)
    if os.path.exists(s):
        ext = os.path.splitext(s)[1].lower()
        if ext == ".obj":
            V, F = _read_obj(s)
        elif ext == ".stl":
            V, F = _read_stl(s)
        else:
            raise ValueError("只支持 .obj/.stl 文件，或对象名/集合名：%s" % s)
        if len(F) == 0:
            raise ValueError("文件里没有面：%s" % s)
        return V[F], os.path.basename(s)
    ob = bpy.data.objects.get(s)
    if ob is not None and ob.type == "MESH":
        t, _o, _b = _world_tris([ob])
        return t, s
    coll = bpy.data.collections.get(s)
    if coll is not None:
        objs = [o for o in coll.all_objects if o.type == "MESH" and not (o.hide_render or o.hide_viewport)]
        t, _o, _b = _world_tris(objs)
        return t, s
    raise ValueError("找不到对象/集合/文件：%s" % s)


def _normalize_tris(tris):
    p = tris.reshape(-1, 3)
    lo, hi = p.min(axis=0), p.max(axis=0)
    c = (lo + hi) / 2.0
    longest = float((hi - lo).max())
    if longest <= 0:
        raise ValueError("退化网格（零尺寸 bbox）")
    return (tris - c) * (2.0 / longest)


def _sample(tris, n, rng):
    import numpy as np
    v0, v1, v2 = tris[:, 0], tris[:, 1], tris[:, 2]
    a = 0.5 * np.linalg.norm(np.cross(v1 - v0, v2 - v0), axis=1)
    tot = float(a.sum())
    if tot <= 0:
        p = tris.reshape(-1, 3)
        return p[rng.integers(0, len(p), size=int(n))]
    c = np.cumsum(a)
    idx = np.searchsorted(c, rng.random(int(n)) * tot)
    u = rng.random((int(n), 2))
    m = np.sqrt(u[:, 0])[:, None]
    s = u[:, 1][:, None]
    return v0[idx] * (1 - m) + v1[idx] * (m * (1 - s)) + v2[idx] * (m * s)


BVH_MAX_TRIS = 0             # **0 = 不设上限（默认全跑）**；显式设非 0 才在上限外退回分格点对点


def _surface_mean_dist(P, tris_target):
    """P 每点到目标三角面集合的最近距离均值 —— BVHTree（C 实现）= 点到**曲面**，不是点到点。"""
    from mathutils.bvhtree import BVHTree
    V = tris_target.reshape(-1, 3).tolist()
    polys = [(3 * i, 3 * i + 1, 3 * i + 2) for i in range(len(tris_target))]
    tree = BVHTree.FromPolygons(V, polys, all_triangles=True)
    fn = tree.find_nearest
    tot = 0.0
    n = len(P)
    for i in range(n):
        p = P[i]
        hit = fn((float(p[0]), float(p[1]), float(p[2])))
        if hit is not None and hit[3] is not None:
            tot += float(hit[3])
    return tot / max(1, n), int(n)


def _nn_mean(P, Q, cell=None, fast=True):
    """P 每点到 Q 的最近距离均值。

    fast=True 走"分格 + 补齐数组"的向量化路径（每格最多 32 点；只在 27 邻域里找最近，
    与 Procedura 的 cell≈2/cbrt(N) 同口径）—— 比逐点慢路径快约一个量级；任何异常/退化
    （单格点数 >32、孤立点）都回退到慢路径，结果口径不变。
    """
    if len(P) == 0 or len(Q) == 0:
        return float("nan")
    if cell is None:
        cell = 2.0 / max(1.0, round(len(P) ** (1.0 / 3.0)))
    if fast:
        try:
            return _nn_mean_grid(P, Q, cell)
        except Exception:
            pass
    return _nn_mean_slow(P, Q, cell)


def _nn_mean_grid(P, Q, cell):
    import numpy as np
    lo = np.array([-1.5, -1.5, -1.5])          # 规范化帧 [-1,1]，留余量
    K = int(np.ceil(3.0 / float(cell))) + 4

    def cid(X):
        return np.clip(np.floor((X - lo) / float(cell)).astype(np.int64), 0, K - 1)

    qc = cid(Q)
    lin = qc[:, 0] * K * K + qc[:, 1] * K + qc[:, 2]
    order = np.argsort(lin, kind="stable")
    lin_s = lin[order]
    starts = np.searchsorted(lin_s, np.arange(K * K * K))
    counts = np.bincount(lin, minlength=K * K * K)
    maxp = int(counts.max()) if len(counts) else 0
    if maxp <= 0 or maxp > 32:
        raise RuntimeError("dense-or-empty")
    pad = np.full((K * K * K, maxp, 3), 1e9, dtype=np.float64)
    within = np.arange(len(Q)) - starts[lin[order]]
    pad[lin[order], within] = Q[order]
    pad = pad.reshape(K, K, K, maxp, 3)
    pc = cid(P)
    offs = np.array([(dx, dy, dz) for dx in (-1, 0, 1) for dy in (-1, 0, 1) for dz in (-1, 0, 1)],
                    dtype=np.int64)
    out = np.empty(len(P), dtype=np.float64)
    BATCH = 2048                     # 批量取格：把 numpy 调用摊到几百个查询上（否则每点一次调用 ≈0.15 ms）
    for s in range(0, len(P), BATCH):
        pcx = pc[s:s + BATCH]
        nb = np.clip(pcx[:, None, :] + offs[None, :, :], 0, K - 1)       # (b,27,3)
        blk = pad[nb[:, :, 0], nb[:, :, 1], nb[:, :, 2]]                 # (b,27,maxp,3)
        blk = blk.reshape(blk.shape[0], -1, 3)
        d = np.linalg.norm(blk - P[s:s + BATCH][:, None, :], axis=2)
        out[s:s + BATCH] = d.min(axis=1)
    bad = out > 1e6                      # 27 邻域空 → 整云兜底（规范化后极少发生）
    if bad.any():
        for i in np.nonzero(bad)[0]:
            out[i] = float(np.linalg.norm(Q - P[i], axis=1).min())
    return float(out.mean())


def _nn_mean_slow(P, Q, cell):
    import numpy as np
    grid = {}
    cells = np.floor(Q / cell).astype(np.int64)
    for i in range(len(Q)):
        grid.setdefault((int(cells[i, 0]), int(cells[i, 1]), int(cells[i, 2])), []).append(i)
    offs = [(dx, dy, dz) for dx in (-1, 0, 1) for dy in (-1, 0, 1) for dz in (-1, 0, 1)]
    pc = np.floor(P / cell).astype(np.int64)
    out = np.empty(len(P), dtype=np.float64)
    for i in range(len(P)):
        cx, cy, cz = int(pc[i, 0]), int(pc[i, 1]), int(pc[i, 2])
        best = float("inf")
        for (dx, dy, dz) in offs:
            lst = grid.get((cx + dx, cy + dy, cz + dz))
            if not lst:
                continue
            d = float(np.linalg.norm(Q[lst] - P[i], axis=1).min())
            if d < best:
                best = d
                if best <= 0:
                    break
        if best == float("inf"):
            best = float(np.linalg.norm(Q - P[i], axis=1).min())
        out[i] = best
    return float(out.mean())


def audit_drift(a, b, samples=20000, seed=24233, normalize=True, fast=True):
    """对称 Chamfer 距离 = **形状漂移**（不是倒角！）。独立于渲染的数值复核通路。

    ⚠ 口径（Procedura 原样）：normalize=True 时**各自归一化**到单位包围盒 —— 平移与整体缩放被消除，
    所以这个数回答"形状改了多远"，不回答"挪了多远"。要连位移一起看：读 bbox_delta（原始帧的
    center/size 差，mm），或传 normalize=False 做同帧比较。
    """
    import numpy as np
    try:
        ta, la = _src_tris(a)
        tb, lb = _src_tris(b)
    except Exception as e:
        return _j({"ok": False, "error": str(e)[:200], "hint": "a/b 可以是对象名、集合名，或 .obj/.stl 路径"})
    if ta is None or tb is None or len(ta) == 0 or len(tb) == 0:
        return _j({"ok": False, "error": "空网格", "a_tris": 0 if ta is None else int(len(ta)),
                   "b_tris": 0 if tb is None else int(len(tb))})
    ta0, tb0 = ta, tb
    import time as _t
    _t0 = _t.perf_counter()
    try:
        if normalize:
            ta, tb = _normalize_tris(ta), _normalize_tris(tb)
        rng = np.random.default_rng(int(seed))
        Pa, Pb = _sample(ta, samples, rng), _sample(tb, samples, rng)
        metric = "point-to-surface(BVHTree)"
        if fast and (BVH_MAX_TRIS <= 0 or (len(ta) <= BVH_MAX_TRIS and len(tb) <= BVH_MAX_TRIS)):
            try:
                a2b, _n1 = _surface_mean_dist(Pa, tb)
                b2a, _n2 = _surface_mean_dist(Pb, ta)
            except Exception:
                metric = "point-to-point(grid)"
                a2b, b2a = _nn_mean(Pa, Pb, fast=False), _nn_mean(Pb, Pa, fast=False)
        else:
            metric = "point-to-point(grid)"
            a2b, b2a = _nn_mean(Pa, Pb, fast=False), _nn_mean(Pb, Pa, fast=False)
    except Exception as e:
        return _j({"ok": False, "error": "采样/近邻失败：%s" % str(e)[:200]})
    ch = (a2b + b2a) / 2.0
    ms = int((_t.perf_counter() - _t0) * 1000)
    # 原始帧的位置 / 尺寸差（形状漂移看不到这一层，单独给）
    _m, mm_per_unit = _units()
    pa, pb = ta0.reshape(-1, 3), tb0.reshape(-1, 3)
    lo_a, hi_a = pa.min(axis=0), pa.max(axis=0)
    lo_b, hi_b = pb.min(axis=0), pb.max(axis=0)
    c_delta = [float((hi_b[i] + lo_b[i]) / 2.0 - (hi_a[i] + lo_a[i]) / 2.0) for i in range(3)]
    moved = float(np.linalg.norm(c_delta))
    size_a = [float(hi_a[i] - lo_a[i]) for i in range(3)]
    size_b = [float(hi_b[i] - lo_b[i]) for i in range(3)]
    band = "noise" if ch <= 0.01 else ("mild" if ch <= 0.05 else "strong")
    return _j({"ok": True, "chamfer": _r(ch, 6), "a_to_b": _r(a2b, 6), "b_to_a": _r(b2a, 6),
               "samples": int(samples), "seed": int(seed), "normalized": bool(normalize), "fast_nn": bool(fast),
               "ms": ms, "metric": metric,
               "a": la, "b": lb, "a_tris": int(len(ta0)), "b_tris": int(len(tb0)),
               "band": band, "band_read": {"noise": "≤0.01：基本未变（同网格读 ≈0）",
                                           "mild": "0.01–0.05：轻微改动（≤最长边 2.5%）",
                                           "strong": ">0.05：明显改动"}[band],
               "bbox_delta": {"center_delta_mm": [_r(x * mm_per_unit, 4) for x in c_delta],
                              "moved_mm": _r(moved * mm_per_unit, 4),
                              "moved_units": _r(moved, 6),
                              "size_a_mm": [_r(x * mm_per_unit, 4) for x in size_a],
                              "size_b_mm": [_r(x * mm_per_unit, 4) for x in size_b],
                              "note": "维度同帧原始值：形状漂移（chamfer）看不到这部分，别把它读成 0"},
               "noise_floor": "≈0（point-to-surface：源侧采样点正落在目标曲面上；退回 grid 口径时约 0.01–0.02）",
               "read_rule": "规范化帧里最长轴跨 [-1,1]：0.05 ≈ 最长边 2.5%；同 seed 同 samples 才可跨次比较；"
                            "metric=point-to-point(grid) 时是点到最近采样点，数值系统性偏大（≈点距/2），别与 BVH 口径混比"})
