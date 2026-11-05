# -*- coding: utf-8 -*-
"""Split from audit.py; behavior preserved by the facade."""
from .common import *  # noqa: F401,F403
from .conn_helpers import _box_fields, _exc_body, _file_names, _load_file_objects, _nm, _r, _units, _world_tris
from .connectivity import _boxvol
from .drift import BVH_MAX_TRIS, _sample

# ============================================================================
# v0.9.1：跨件 / 跨文件 面-对重叠 + 交集体体积（rifle-build《93 反馈》，定级）
# ----------------------------------------------------------------------------
# 反馈原文："check_interference 只认当前会话注册过的组件，传 file= 直接参数不匹配；跨
# parts/*.blend 的互穿只能自写 BVH 面-对 + 布尔交集体积"；证据是成员自建的
# lead_bvh_overlap.py / lead_interference.py（**正是这套自建审计抓出"弹匣悬空：井口盖板
# 10719.7 mm³"与"护木/前握把 2536/30319 mm³"**）。这里收敛成两个算子：
#     audit_overlap       面-对重叠（结构化：pairs / pair_count / intersection_bbox / per_object）
#     audit_interference  交集体积估计（mm³；Monte-Carlo 射线奇偶，**不用 bpy boolean**）
# 实测语义（Blender 5.2，逐条写进返回体，别当没这回事）：
#   * BVHTree.overlap = **真三角-三角相交**（AABB 重叠但不穿透的面对不报）→ method 敢写"三角面精确"；
#   * 但**共面且互相覆盖**的两张面不报（isect_tri_tri 的共面语义）→ 共面贴合由 contact_probe 补报；
#   * ray_cast 命中后要沿方向前进 eps 再打（否则原地自命中），迭代上限写死并回报 unknown。
# ============================================================================

INTERFERENCE_SAMPLES = 200000     # 默认采样数（相对误差 ∝ 1/√n；口径见返回体 error_caliber）
MATERIAL_VOLUME_MM3 = 1.0         # 可执行下限：< 1 mm³ 的互穿低于网格弦差量级 → 不作为"真干涉"
INTERFERENCE_MAX_STEPS = 64       # 单点单方向射线迭代上限；超限的点记 unknown（不猜）
CONTACT_PROBE_PTS = 256           # contact_probe 每侧曲面采样点数
PAIRS_BBOX_CAP = 0                # **0 = 不设上限（默认全跑）**；显式设非 0 才截断（病态场景防爆内存）


def _box_pair(lo, hi, mm_per_unit, digits=5):
    """(盒, 盒_mm)：场景单位与 mm 两个视角都给（铁律：数值带单位）。"""
    f = _box_fields(lo, hi, mm_per_unit, digits)
    return ({"min": f["min"], "max": f["max"], "size": f["size"]},
            {"min": f["min_mm"], "max": f["max_mm"], "size": f["size_mm"]})


def _box_intersect(lo1, hi1, lo2, hi2):
    """两个世界 AABB 的交盒；任一轴分离 → None（交集体必在交盒里，所以它是有用的先验）。"""
    import numpy as np
    lo = np.maximum(np.asarray(lo1, dtype=np.float64), np.asarray(lo2, dtype=np.float64))
    hi = np.minimum(np.asarray(hi1, dtype=np.float64), np.asarray(hi2, dtype=np.float64))
    if bool(np.any(hi < lo)):
        return None
    return (lo, hi)


def _bvh_from_tris(tris):
    """世界三角面 (N,3,3) → BVHTree（FromPolygons + all_triangles → 索引就是三角面下标）。"""
    from mathutils.bvhtree import BVHTree
    V = tris.reshape(-1, 3).tolist()
    polys = [(3 * i, 3 * i + 1, 3 * i + 2) for i in range(len(tris))]
    return BVHTree.FromPolygons(V, polys, all_triangles=True)


def _session_side(sel, include_hidden=False):
    """会话侧来源：对象名 / 名字列表 / 集合名 → (objs, missing, err)。"""
    if isinstance(sel, dict):
        w = sel
        sel = w.get("objects") or w.get("names") or w.get("list")
        if sel is None:
            sel = w.get("scope")
        include_hidden = bool(w.get("include_hidden", include_hidden))
    if sel is None or (isinstance(sel, str) and not sel.strip()):
        return [], [], "这一端没给来源：给会话对象/集合名（a / objects_a），或用 file_a / file_b 指到 .blend"
    got, missing = [], []
    if isinstance(sel, (list, tuple, set)):
        for n in sel:
            ob = bpy.data.objects.get(str(n))
            if ob is None or ob.type != "MESH":
                missing.append(str(n))
            else:
                got.append(ob)
    else:
        s = str(sel)
        ob = bpy.data.objects.get(s)
        if ob is not None:
            if ob.type != "MESH":
                return [], [], "不是 mesh 对象（type=%s）：%s" % (ob.type, s)
            got = [ob]
        else:
            coll = bpy.data.collections.get(s)
            if coll is None:
                return [], [], "会话里既没有对象也没有集合：%s" % s
            got = [o for o in coll.all_objects if o.type == "MESH"]
    if not include_hidden:
        got = [o for o in got if not (o.hide_render or o.hide_viewport)]
    return got, missing, None


def _side_tris(tag, sel=None, file=None, names=None, include_hidden=False):
    """解析一端（A/B）：会话对象/集合，或**另一个 .blend**（names/objects_x 在文件内筛名）。

    返回 {ok, error, tris, owner, objs, names, aabb, source, cleanup, file_info, hint}
    —— tris/owner/aabb 里有 numpy 对象，**不要**直接塞进 JSON（上报走 _side_summary）。
    """
    import os
    filt = names if names is not None else sel
    if not file and isinstance(sel, str) and sel.strip() and os.path.exists(sel):
        ext = os.path.splitext(sel)[1].lower()
        if ext == ".blend":
            file, filt = sel, (names if names is not None else None)   # a 直接给 .blend 路径也认
        else:
            return {"ok": False, "side": tag, "source": "path:%s" % sel,
                    "error": "只认 .blend 文件（给的是 %s）" % (ext or "无扩展名"),
                    "hint": ".obj/.stl 的两两比较请用 audit_drift（同一条 _src_tris 通路）"}
    if file:
        objs, cleanup, info = _load_file_objects(file, _file_names(filt), include_hidden)
        name_of = info.pop("_name_of", None)
        src = info.get("source")
        if not info.get("ok"):
            return {"ok": False, "side": tag, "source": src, "error": info.get("error"),
                    "cleanup": cleanup, "file_info": info,
                    "hint": "file= 侧：文件要存在、里面有可见 mesh；空产出必须 ok:false"}
        note = "file= 临时加载（跑完即删；名字用文件里的原名）"
    else:
        objs, missing, err = _session_side(filt, include_hidden)
        cleanup, info, name_of, src, note = None, None, None, "session", None
        if err:
            return {"ok": False, "side": tag, "source": src, "error": err}
        if not objs:
            return {"ok": False, "side": tag, "source": src, "error": "该端 0 个可见 mesh", "missing": missing,
                    "hint": "名字/集合名对不上，或都被 hide_render/hide_viewport 挡了（include_hidden=true 放行）"}
    tris, owner, _boxes = _world_tris(objs)
    nm_list = [_nm(o, name_of) for o in objs]
    if tris is None or len(tris) == 0:
        return {"ok": False, "side": tag, "source": src, "error": "该端 0 个三角面（对象在但没几何）",
                "object_names": nm_list, "cleanup": cleanup, "file_info": info}
    p = tris.reshape(-1, 3)
    return {"ok": True, "side": tag, "source": src, "tris": tris, "owner": owner, "objs": objs,
            "names": nm_list, "name_of": name_of, "aabb": (p.min(axis=0), p.max(axis=0)),
            "cleanup": cleanup, "file_info": info, "note": note}


def _side_summary(s, mm_per_unit):
    """一端的上报摘要（绝不带 numpy 对象）。"""
    out = {"source": s.get("source"), "object_names": s.get("names") or [],
           "objects": len(s.get("objs") or []),
           "tris": (int(len(s["tris"])) if s.get("tris") is not None else 0)}
    if s.get("aabb") is not None:
        out["aabb"] = _box_fields(s["aabb"][0], s["aabb"][1], mm_per_unit)
    if not s.get("ok") and s.get("error"):
        out["error"] = s.get("error")
    if s.get("hint"):
        out["hint"] = s["hint"]
    if s.get("missing"):
        out["missing"] = s["missing"]
    if s.get("file_info"):
        fi = s["file_info"]
        out["file_load"] = {k: fi.get(k) for k in ("path", "requested", "loaded", "mesh", "non_mesh",
                                                   "missing", "hidden_skipped", "renamed", "tmp_collection",
                                                   "name_map_note", "mesh_names") if fi.get(k) is not None}
    return out


def _resolve_two_sides(a, b, file_a, file_b, objects_a, objects_b, include_hidden):
    """两端一起解析（文件侧会临时加载）→ (sides, cleanups)。cleanup 由调用方在 finally 里跑。"""
    sides, cleanups = {}, {}
    for tag, (sel, f, names) in (("a", (a, file_a, objects_a)), ("b", (b, file_b, objects_b))):
        sd = _side_tris(tag, sel, f, names, include_hidden)
        sides[tag] = sd
        if sd.get("cleanup"):
            cleanups[tag] = sd["cleanup"]
    return sides, cleanups


def _sides_error(sides, mm_per_unit):
    """任一端失败 → 统一的 ok:false 体（两端摘要都带上，方便定位是哪一端）。"""
    bad = {t: sides[t].get("error") for t in ("a", "b") if not sides[t].get("ok")}
    if not bad:
        return None
    return {"ok": False, "analyzed": False,
            "error": "来源失败：" + "；".join("%s: %s" % (t, bad[t]) for t in sorted(bad)),
            "sides": {t: _side_summary(sides[t], mm_per_unit) for t in ("a", "b")},
            "hint": "两端各给一个来源：会话对象名/集合名（a/objects_a、b/objects_b），"
                    "或 .blend（file_a/file_b，用 objects_a/objects_b 在文件内筛名）"}


def _tri_tri_segment(ta, tb):
    """两个三角面的真实交线段 → (point[3]|None, length|None, kind)。

    做法：两个三角面所在平面求交得到公共直线 (P, D)，再用三角形三条边的面内半空间把这条直线
    裁出 t 区间，两区间之交就是真交线段（P 同时在两个平面上，所以参数 t 对两侧一致）。
    共面/平行（overlap() 本来就不报共面）或裁剪失败 → 退回"两重心连线的中点"，kind 里写明 ——
    不给假精度。
    """
    import numpy as np
    a = np.asarray(ta, dtype=np.float64)
    b = np.asarray(tb, dtype=np.float64)
    ca, cb = a.mean(axis=0), b.mean(axis=0)
    mid = (ca + cb) / 2.0
    n1 = np.cross(a[1] - a[0], a[2] - a[0])
    n2 = np.cross(b[1] - b[0], b[2] - b[0])
    l1, l2 = float(np.linalg.norm(n1)), float(np.linalg.norm(n2))
    if l1 <= 1e-12 or l2 <= 1e-12:
        return mid, None, "退化三角面（零面积）→ 用重心中点"
    n1, n2 = n1 / l1, n2 / l2
    d = np.cross(n1, n2)
    dn = float(np.linalg.norm(d))
    if dn <= 1e-9:
        return mid, None, "两面共面/平行 → 用重心中点（共面覆盖 overlap() 本来就不报）"
    d = d / dn
    c1, c2 = float(np.dot(n1, a[0])), float(np.dot(n2, b[0]))
    P = (c1 * np.cross(n2, d) + c2 * np.cross(d, n1)) / (dn * dn)
    t1 = _line_clip(P, d, a, n1)
    t2 = _line_clip(P, d, b, n2)
    if t1 is None or t2 is None:
        return mid, None, "直线裁剪失败（边界/退化情形）→ 用重心中点"
    lo, hi = max(t1[0], t2[0]), min(t1[1], t2[1])
    if hi < lo - 1e-12:
        return mid, None, "裁剪后区间为空 → 用重心中点"
    return (P + ((lo + hi) / 2.0) * d), max(0.0, hi - lo), "平面求交 + 三角形裁剪（真交线段）"


def _line_clip(P, d, tri, n):
    """直线 P+t·d（已知落在三角形平面上）被三角形裁出的 t 区间 [lo,hi]；无交 → None。"""
    import numpy as np
    lo, hi = -1e30, 1e30
    for i in range(3):
        u, v, w = tri[i], tri[(i + 1) % 3], tri[(i + 2) % 3]
        m = np.cross(v - u, n)                     # 面内法向
        if float(np.dot(w - u, m)) < 0:            # ★ 用第三个顶点定符号：内侧必须 ≥0（不依赖绕序）
            m = -m
        fu, fd = float(np.dot(P - u, m)), float(np.dot(d, m))
        if abs(fd) < 1e-15:
            if fu < -1e-12:
                return None                        # 整条直线都在这条边外侧
            continue
        t = -fu / fd
        if fd > 0:
            lo = max(lo, t)
        else:
            hi = min(hi, t)
    if lo > hi + 1e-12:
        return None
    return (lo, hi)


def _pairs_bbox(ta, tb, pairs):
    """交叠三角面顶点的世界 bbox（= 交集体的**超集**盒）→ (lo, hi, truncated)。

    为什么这个盒有效：交集体的边界由"∂A 在 B 内的部分 + ∂B 在 A 内的部分 + ∂A∩∂B"组成，前两类
    必然落在**与对方相交的三角面**上，第三类更是交叠面对本身 → 交集体一定在这个盒里。
    """
    import numpy as np
    _pc = int(PAIRS_BBOX_CAP)
    sel = pairs if _pc <= 0 else pairs[:_pc]
    ia = np.fromiter((int(p[0]) for p in sel), dtype=np.int64, count=len(sel))
    ib = np.fromiter((int(p[1]) for p in sel), dtype=np.int64, count=len(sel))
    p = np.concatenate([ta[ia].reshape(-1, 3), tb[ib].reshape(-1, 3)], axis=0)
    return p.min(axis=0), p.max(axis=0), bool(len(pairs) > len(sel))


def _pair_rows(sa, sb, ta, tb, pairs, limit, mm_per_unit):
    """前 limit 对交叠面的结构化行（对象名 + 三角面下标 + 交点/交线段长）。"""
    rows = []
    for pr in pairs[:max(0, int(limit))]:
        ia, ib = int(pr[0]), int(pr[1])
        pt, seglen, kind = _tri_tri_segment(ta[ia], tb[ib])
        ca = ta[ia].mean(axis=0)
        cb = tb[ib].mean(axis=0)
        rows.append({"a": sa["names"][int(sa["owner"][ia])], "b": sb["names"][int(sb["owner"][ib])],
                     "tri_a": ia, "tri_b": ib,
                     "point": ([_r(float(x), 6) for x in pt] if pt is not None else None),
                     "point_mm": ([_r(float(x) * mm_per_unit, 4) for x in pt] if pt is not None else None),
                     "point_kind": kind,
                     "segment_len_mm": (None if seglen is None else _r(seglen * mm_per_unit, 4)),
                     "centroid_dist_mm": _r(float(((ca - cb) ** 2).sum() ** 0.5) * mm_per_unit, 4)})
    return rows


def _per_object_counts(sa, sb, pairs):
    """每端每个对象的交叠对数（只列 >0 的）。"""
    import numpy as np
    out = {"a": {}, "b": {}}
    if not pairs:
        return out
    arr = np.asarray(pairs, dtype=np.int64)
    ca = np.bincount(sa["owner"][arr[:, 0]], minlength=len(sa["objs"]))
    cb = np.bincount(sb["owner"][arr[:, 1]], minlength=len(sb["objs"]))
    out["a"] = {sa["names"][i]: int(ca[i]) for i in range(len(sa["objs"])) if int(ca[i]) > 0}
    out["b"] = {sb["names"][i]: int(cb[i]) for i in range(len(sb["objs"])) if int(cb[i]) > 0}
    return out


def _ray_limit(lo, hi, p, dr, pad):
    """射线**参数**上限：到本侧自身 AABB 边界外一点点（出了自己的包围盒就再也打不到面）。

    ★ 必须除以方向分量：dr 不一定是轴对齐单位向量（diag 实测：漏除会让斜方向的射线提前 1.73 倍被截断
      → 全部点判成外侧）。
    """
    lim = None
    for k in range(3):
        dk = float(dr[k])
        if abs(dk) < 1e-15:
            continue
        d = ((float(hi[k]) - p[k]) if dk > 0 else (p[k] - float(lo[k]))) / abs(dk)
        lim = d if lim is None else min(lim, d)
    return pad if lim is None else (lim + pad)


def _inside_mask(tree, pts, lo, hi, dirs=((1.0, 0.0, 0.0),), max_steps=INTERFERENCE_MAX_STEPS):
    """射线奇偶校验：点沿 +X（默认）打射线，命中次数为奇数 ⇒ 在闭合网格内；多方向取多数票。

    * 迭代到区间外：每命中一次，从命中点沿方向前进 eps 再打；射线长度上限锁在本侧自身 AABB 之外
      一点点 → 迭代天然有界；仍超过 max_steps 的点记 unknown（**不猜**，数目写进返回体）；
    * ★ 推进步长 eps 必须**大于 float32 几何的坐标分辨率**（本模块第一轮自检抓到的真 bug，实测数据：
      命中点由 float32 算出会落在真平面**之后** —— 0.499999911 vs 0.5；若 eps 只有 1e-7，下一发射线会
      以 dist=0 再命中同一张面 → 命中数变偶数 → 内侧点被判成外侧，实测 459/50000 = 0.92%，
      且集中在射线行程 t≈1 的那一侧，是**系统性偏差**）。所以
      eps = max(span×1e-6, 8×float32_eps×坐标量级)（实测 1e-6 时 0 漏判）。
    * 命中距离 ≤0（射线起点正贴在面上）→ 该点记 unknown（奇偶不可判，不猜）；
    * 只在闭合流形网格上成立 —— 调用方负责先做流形检查（_mesh_health）。
    返回 (mask, unknown, stats)
    """
    import numpy as np
    n = int(len(pts))
    mask = np.zeros(n, dtype=bool)
    if n == 0:
        return mask, 0, {"points_total": 0}
    span = max(float(hi[0]) - float(lo[0]), float(hi[1]) - float(lo[1]), float(hi[2]) - float(lo[2]))
    mag = max(span, float(np.abs(np.asarray(lo, dtype=np.float64)).max()),
              float(np.abs(np.asarray(hi, dtype=np.float64)).max()))
    eps = max(span * 1e-6, 8.0 * 1.1920929e-07 * mag, 1e-12)
    pad = eps * 4.0
    inside_box = np.ones(n, dtype=bool)
    for k in range(3):
        inside_box &= (pts[:, k] >= float(lo[k]) - 1e-12) & (pts[:, k] <= float(hi[k]) + 1e-12)
    idx = np.nonzero(inside_box)[0]
    unknown, maxhits = 0, 0
    nd = max(1, len(dirs))
    for i in idx:
        p = pts[i]
        votes, bad, on_surface = 0, False, False
        for (dx, dy, dz) in dirs:
            ox, oy, oz = float(p[0]) + dx * eps, float(p[1]) + dy * eps, float(p[2]) + dz * eps
            lim = _ray_limit(lo, hi, (ox, oy, oz), (dx, dy, dz), pad)
            cnt, steps = 0, 0
            while steps < int(max_steps):
                loc, nor, fidx, dist = tree.ray_cast((ox, oy, oz), (dx, dy, dz), lim)
                if loc is None:
                    break
                if dist is not None and float(dist) <= 0.0:
                    on_surface = True               # 起点贴在面上 → 奇偶不可判，别硬判
                    break
                cnt += 1
                steps += 1
                ox, oy, oz = loc[0] + dx * eps, loc[1] + dy * eps, loc[2] + dz * eps
                lim = _ray_limit(lo, hi, (ox, oy, oz), (dx, dy, dz), pad)
                if lim <= 0:
                    break
            else:
                bad = True                       # while 走完 max_steps 没 break → 到上限了
            if cnt > maxhits:
                maxhits = cnt
            if cnt % 2 == 1:
                votes += 1
        if bad or on_surface:
            unknown += 1
            continue
        mask[i] = bool(votes * 2 > nd)
    return mask, int(unknown), {"points_total": n, "points_in_own_bbox": int(len(idx)),
                               "rays": int(nd), "max_hits": int(maxhits), "eps_units": eps,
                               "eps_note": "推进步长 = max(span×1e-6, 8×float32_eps×坐标量级)"}


def _mesh_health(objs, name_of=None):
    """闭合性体检（与 audit_mesh 同口径，以对象原始网格为准）：→ (rows, closed_all)。

    closed = boundary_edges==0 且 nonmanifold_edges==0；0 个面的对象一律 closed=False（无从校验）。
    """
    rows, closed_all = [], True
    for ob in objs:
        me = ob.data
        b, nmk = None, None
        if len(me.polygons) > 0:
            bmw = bmesh.new()
            try:
                bmw.from_mesh(me)
                b = int(sum(1 for e in bmw.edges if len(e.link_faces) == 1))
                nmk = int(sum(1 for e in bmw.edges if not e.is_manifold))
            except Exception:
                b, nmk = None, None
            finally:
                bmw.free()
        closed = bool(b == 0 and nmk == 0)
        rows.append({"name": _nm(ob, name_of), "polys": int(len(me.polygons)),
                     "boundary_edges": b, "nonmanifold_edges": nmk, "closed": closed})
        closed_all = closed_all and closed
    return rows, bool(closed_all)


def _containment_probe(ta, tb, tree_a, tree_b, box_a, box_b):
    """"一件完全包在另一件里"的探针：overlap() 对这种情形一条都不报 → 别读成无干涉。

    快筛：一侧 AABB 被另一侧 AABB 包含（带容差）才继续；命中就采样内层曲面点，用射线奇偶看
    有没有落在外层内部。返回 None（没有嵌套迹象，不花这个钱）或 dict。
    """
    import numpy as np
    lo_a, hi_a = box_a
    lo_b, hi_b = box_b
    tol = max(1e-9, 1e-9 * float(max(hi_a[i] - lo_a[i] for i in range(3))))
    inner = None
    if all(lo_a[k] <= lo_b[k] + tol and hi_b[k] <= hi_a[k] + tol for k in range(3)):
        inner, outer = "b", "a"
    elif all(lo_b[k] <= lo_a[k] + tol and hi_a[k] <= hi_b[k] + tol for k in range(3)):
        inner, outer = "a", "b"
    if inner is None:
        return None
    if inner == "b":
        pts = _sample(tb, 64, np.random.default_rng(0))
        tree_o, lo_o, hi_o = tree_a, lo_a, hi_a
    else:
        pts = _sample(ta, 64, np.random.default_rng(0))
        tree_o, lo_o, hi_o = tree_b, lo_b, hi_b
    mask, unk, _st = _inside_mask(tree_o, pts, lo_o, hi_o)
    inside_n = int(mask.sum())
    return {"suspected": bool(inside_n > 0), "inner": inner, "outer": outer,
            "probe_points": int(len(pts)), "inside_points": inside_n, "parity_unknown": int(unk),
            "note": ("面无相交，但 %s 的 AABB 完全落在 %s 内、且内层曲面有 %d/%d 个采样点落在外层内部 → "
                     "可能「一件完全包在另一件里」（overlap() 天生不报这种情形）→ 别读成无干涉"
                     % (inner, outer, inside_n, len(pts))) if inside_n > 0 else
                    ("%s 的 AABB 落在 %s 内，但内层曲面采样点没有一个落在外层内部 → 无包含迹象"
                     % (inner, outer))}


def _contact_probe(ta, tb, tree_a, tree_b, eps_units, mm_per_unit, max_pts=CONTACT_PROBE_PTS):
    """贴没贴上：两侧曲面各采 max_pts 个点，量到**对面曲面**的最近距离（BVHTree.find_nearest）。

    pair_count=0 时补这一步的理由：overlap() 实测不报"共面且互相覆盖"的面，而共面贴合/贴而未重合
    在装配里极常见 —— 直接读 pair_count:0 会漏。判据口径与 _conn_analyze 的 micro_gap_mm 一致
    （默认 0.3 mm 算相接）。
    """
    import numpy as np
    rng = np.random.default_rng(0)
    pa = _sample(ta, int(max_pts), rng)
    pb = _sample(tb, int(max_pts), rng)
    best, within = None, 0
    for pts, tree in ((pa, tree_b), (pb, tree_a)):
        for p in pts:
            hit = tree.find_nearest((float(p[0]), float(p[1]), float(p[2])))
            if hit is None or hit[0] is None or hit[3] is None:
                continue
            d = float(hit[3])
            if best is None or d < best:
                best = d
            if d <= float(eps_units):
                within += 1
    return {"contact": bool(within > 0), "min_dist_units": (None if best is None else _r(best, 8)),
            "min_dist_mm": (None if best is None else _r(best * mm_per_unit, 5)),
            "points_within_eps": int(within), "probe_points": int(len(pa) + len(pb)),
            "eps_mm": _r(float(eps_units) * mm_per_unit, 5),
            "note": "面对面最近距离（曲面采样近似，不是精确最小距离）；eps 口径与连接的 micro_gap_mm 一致"}


def _estimate_volume(ta, tb, tree_a, tree_b, box, samples, seed, box_a, box_b, rays=1):
    """在 box 内均匀撒点 → 射线奇偶判"同时在 A、B 内" → (frac, n, stats)。

    frac × 盒体积就是 A∩B 体积的无偏估计（盒是交集的超集，盒外的点本来就不可能同时在两者内）。
    """
    import numpy as np
    n = max(1, int(samples))
    lo, hi = box
    rng = np.random.default_rng(int(seed))
    pts = np.asarray(lo, dtype=np.float64) + rng.random((n, 3)) * (
        np.asarray(hi, dtype=np.float64) - np.asarray(lo, dtype=np.float64))
    dirs = ((1.0, 0.0, 0.0), (0.0, 1.0, 0.0), (0.0, 0.0, 1.0))[:max(1, min(3, int(rays)))]
    ma, unk_a, sa = _inside_mask(tree_a, pts, box_a[0], box_a[1], dirs)
    mb, unk_b, sb = _inside_mask(tree_b, pts, box_b[0], box_b[1], dirs)
    both = ma & mb
    nb = int(both.sum())
    return (float(nb) / float(n), n, {"inside_a": int(ma.sum()), "inside_b": int(mb.sum()),
                                      "inside_both": nb, "unknown_a": int(unk_a), "unknown_b": int(unk_b),
                                      "parity_a": sa, "parity_b": sb, "points": int(len(pts))})


def audit_overlap(a=None, b=None, file_a=None, file_b=None, objects_a=None, objects_b=None,
                  limit=200, include_hidden=False, max_tris=None):
    """面-对重叠（结构化）：这堆面到底穿没穿？穿在哪几对三角面上？

    两端各自可以是：**当前会话**的对象名/名字列表/集合名（a / objects_a），或**另一个 .blend**
    （file_a + objects_a 在文件里筛名；不筛就取文件里全部可见 mesh）。a/b 直接给 .blend 路径也认。

    返回（关键键）：
      ok / analyzed / method = bvh-overlap(三角面精确)
      pair_count          交叠面对**总数**（不受 limit 截断；0 是结论，不是"没跑"）
      pairs               前 limit 对：[a,b,tri_a,tri_b,point,point_mm,point_kind,segment_len_mm,
                          centroid_dist_mm]（point=真交线段上的点；共面/退化时退重心中点并写明）
      intersection_bbox   两侧世界 AABB 的**交盒**（交集体必在此盒内；不相交时 null）
      overlap_faces_bbox  交叠三角面顶点的 bbox（= audit_interference 的采样盒）
      per_object          每端每个对象的交叠对数；tris_a/tris_b；aabb_a/aabb_b；sides；units；cleanup
    三态：无交叠 → ok=true + pair_count=0（真结论）；某端为空/文件不存在 → ok=false；
          三角面超上限（BVH_MAX_TRIS，可 max_tris 覆盖）→ ok=false + analyzed=false（不许读成"没问题"）。

    ⚠ 覆盖边界（实测 Blender 5.2）：overlap() 是*真*三角-三角相交（AABB 重叠但不穿透的面对不报），
    但**共面且互相覆盖**的两张面不报 → pair_count=0 不排除"共面贴合"；两侧 bbox 相交而面无相交时
    会自动跑一次 contact_probe（最近距离）与 containment_probe（完全包含），免得把漏报读成没问题。
    """
    import time as _t
    t0 = _t.perf_counter()
    m_per_unit, mm_per_unit = _units()
    ub = {"m_per_unit": _r(m_per_unit, 6), "mm_per_unit": _r(mm_per_unit, 6)}
    sides, cleanups = {}, {}

    def _body():
        base = {"method": "bvh-overlap(三角面精确)", "units": ub, "limit": int(limit),
                "include_hidden": bool(include_hidden),
                "sides": {t: _side_summary(sides[t], mm_per_unit) for t in ("a", "b")}}
        err = _sides_error(sides, mm_per_unit)
        if err:
            return dict(base, **err)
        sa, sb = sides["a"], sides["b"]
        ta, tb = sa["tris"], sb["tris"]
        na, nb = int(len(ta)), int(len(tb))
        cap = int(max_tris or BVH_MAX_TRIS)
        over = [(t, v) for t, v in (("a", na), ("b", nb)) if cap > 0 and v > cap]   # cap=0 → 不设上限（默认全跑）
        if over:
            return dict(base, ok=False, analyzed=False, tris_a=na, tris_b=nb, max_tris=cap,
                        error="三角面超上限：%s（上限 %d，可用 max_tris 覆盖）→ 不分析"
                              % ("/".join("%s=%d" % kv for kv in over), cap),
                        note="未分析 ≠ 无重叠：这是「没跑」，不能读成「没问题」")
        tree_a, tree_b = _bvh_from_tris(ta), _bvh_from_tris(tb)
        pairs = list(tree_a.overlap(tree_b) or [])
        pair_count = len(pairs)
        out = dict(base, ok=True, analyzed=True, tris_a=na, tris_b=nb, pair_count=pair_count,
                   aabb_a=_box_fields(sa["aabb"][0], sa["aabb"][1], mm_per_unit),
                   aabb_b=_box_fields(sb["aabb"][0], sb["aabb"][1], mm_per_unit),
                   point_kind="point=真交线段中点（平面求交 + 三角形裁剪）；退化/共面时退两重心中点（kind 说明）",
                   coverage="overlap() 实测语义：报真三角-三角相交（AABB 重叠但不穿透的不报）；"
                            "共面且互相覆盖的面不报 → 共面贴合看 contact_probe")
        inter = _box_intersect(sa["aabb"][0], sa["aabb"][1], sb["aabb"][0], sb["aabb"][1])
        provable_empty = bool(inter is None or _boxvol(inter) <= 0.0)
        if provable_empty:
            out["intersection_bbox"] = None
            out["intersection_bbox_mm"] = None
            out["intersection_bbox_note"] = ("两侧世界 AABB 不相交/只贴不叠 → 交集体为空"
                                             "（交集体必在两 AABB 的交盒里，所以这是可证结论）")
        else:
            bi, bm = _box_pair(inter[0], inter[1], mm_per_unit)
            out["intersection_bbox"] = bi
            out["intersection_bbox_mm"] = bm
        out["containment_probe"] = None
        out["contact_probe"] = None
        if pair_count == 0:
            out["pairs"] = []
            out["overlap_faces_bbox"] = None
            out["overlap_faces_bbox_mm"] = None
            out["per_object"] = {"a": {}, "b": {}}
            if provable_empty:
                out["note"] = "两侧世界 AABB 不相交/只贴不叠 → 面-对重叠必为 0（这是结论，不是「没跑」）"
            else:
                cont = _containment_probe(ta, tb, tree_a, tree_b, sa["aabb"], sb["aabb"])
                out["containment_probe"] = cont
                if cont and cont.get("suspected"):
                    out["note"] = cont["note"] + "；要体积请用 audit_interference"
                else:
                    cp = _contact_probe(ta, tb, tree_a, tree_b, MICRO_GAP_MM / mm_per_unit, mm_per_unit)
                    out["contact_probe"] = cp
                    out["note"] = ("面无相交（非共面穿透为 0）" +
                                   ("，但两曲面已贴上：最近 %.5f mm（≤ %.2f mm 口径）→ 共面贴合/贴而未重合"
                                    % (cp["min_dist_mm"], MICRO_GAP_MM) if cp["contact"] else
                                    "；两曲面最近 %.5f mm（未接触）" % (cp["min_dist_mm"] if cp["min_dist_mm"] is not None else -1.0)) +
                                   "。⚠ overlap() 不报共面重叠，共面覆盖情形本算子看不出来")
        else:
            out["pairs"] = _pair_rows(sa, sb, ta, tb, pairs, limit, mm_per_unit)
            fb_lo, fb_hi, trunc = _pairs_bbox(ta, tb, pairs)
            bf, bfm = _box_pair(fb_lo, fb_hi, mm_per_unit)
            out["overlap_faces_bbox"] = bf
            out["overlap_faces_bbox_mm"] = bfm
            if trunc:
                out["overlap_faces_bbox_note"] = ("面对数超过 %d，bbox 只统计前 %d 对（pair_count 仍是全量）"
                                                  % (PAIRS_BBOX_CAP, PAIRS_BBOX_CAP))
            out["per_object"] = _per_object_counts(sa, sb, pairs)
            seg = [r["segment_len_mm"] for r in out["pairs"] if r["segment_len_mm"] is not None]
            out["max_segment_len_mm"] = (max(seg) if seg else None)
            out["note"] = ("面-对重叠 %d 对%s（面对数=精确相交的三角面对；AABB 重叠但不穿透的不计）"
                           % (pair_count, "" if pair_count <= int(limit) else "，pairs 只列前 %d 对" % int(limit)))
        return out

    try:
        sides, cleanups = _resolve_two_sides(a, b, file_a, file_b, objects_a, objects_b, include_hidden)
        res = _body()
    except Exception as e:
        res = _exc_body(e, "overlap")
    finally:
        for fn in cleanups.values():
            fn()
    if cleanups:
        res["cleanup"] = {t: cleanups[t]() for t in sorted(cleanups)}
    res["ms"] = int((_t.perf_counter() - t0) * 1000)
    return _j(res)


def audit_interference(a=None, b=None, file_a=None, file_b=None, objects_a=None, objects_b=None,
                       samples=INTERFERENCE_SAMPLES, seed=0, limit=200, include_hidden=False,
                       material_mm3=MATERIAL_VOLUME_MM3, max_tris=None, rays=1):
    """A∩B 交集体积估计（mm³）—— "弹匣悬空：井口盖板 10719.7 mm³" 那一类数的算子化。

    两端来源与 audit_overlap 完全相同（会话对象/集合，或 file_a/file_b 指到 .blend；a/b 直接给
    .blend 路径也认）。**不用 bpy boolean**：无头里也能跑，不建临时对象、不改场景。

    做法（照反馈自建脚本的口径收敛）：
      ① tree_a.overlap(tree_b) 拿交叠三角面对 → 只在这些面的世界 bbox 里采样（否则高效不了）；
      ② 该盒内均匀撒 samples 个点（numpy.random.default_rng(seed)）；
      ③ 每点用**射线奇偶**（默认沿 +X 打；rays=3 时 X/Y/Z 取多数票）分别判是否在 A 内、在 B 内；
      ④ volume = frac × 盒体积；ci95 = 1.96·sqrt(frac(1-frac)/n) × 盒体积。

    ⚠ 诚实边界（同时写进返回体 honest_limits / error_caliber）：
      * **不是精确布尔**：Monte-Carlo 估计，相对误差(95%) ≈ 1.96·sqrt((1-p)/(p·n))（p=总体积占盒比）。
        实测口径：n=200k 时 p=0.5 → ±0.44%、p=0.1 → ±1.31%、p=0.01 → ±4.4%（samples×4 → 误差减半）。
      * 只在**交叠三角面的 bbox**内采样（该盒是交集体的超集，不是交集体本身）。
      * 射线奇偶只在**闭合流形**网格上成立：检测到边界边/非流形边 → verdict=unresolved、volume=null
        （不给一个看着很准的错数），why 里写清缺什么证据。
      * 一面被完全包含（面无相交）时 overlap() 给 0 对 → 由 containment_probe 兜住，不误判为 refuted。

    三态 verdict：supported（volume>0 且 95% 下界>0 且 ≥ 可执行下限）/ refuted（无干涉：给上界 mm³）/
      unresolved（不流形、样本不足、上界还不够紧 → 说清差多少）。
    必给键：volume_mm3 / volume_ci95_mm3 / verdict / why / bbox_mm（另有 units 与 mm 全套）。
    """
    import time as _t
    t0 = _t.perf_counter()
    m_per_unit, mm_per_unit = _units()
    mm3 = float(mm_per_unit) ** 3
    floor = float(material_mm3)
    ub = {"m_per_unit": _r(m_per_unit, 6), "mm_per_unit": _r(mm_per_unit, 6),
          "volume_units3_to_mm3": _r(mm3, 6)}
    sides, cleanups = {}, {}


    def _fill_bbox(out, cand, src, mm_per_unit, mm3):
        if cand is None:
            out["bbox"] = None
            out["bbox_mm"] = None
            out["bbox_source"] = "无候选盒（交集可证为空）"
            return
        b, bm = _box_pair(cand[0], cand[1], mm_per_unit)
        out["bbox"] = b
        out["bbox_mm"] = bm
        out["bbox_source"] = src
        out["bbox_volume_mm3"] = _r(float(_boxvol(cand)) * mm3, 4)

    try:
        sides, cleanups = _resolve_two_sides(a, b, file_a, file_b, objects_a, objects_b, include_hidden)
        res = _audit_interference_body(_fill_bbox, floor, include_hidden, limit, max_tris, mm3, mm_per_unit, rays, samples, seed, sides, ub)
    except Exception as e:
        res = _exc_body(e, "interference")
    finally:
        for fn in cleanups.values():
            fn()
    if cleanups:
        res["cleanup"] = {t: cleanups[t]() for t in sorted(cleanups)}
    res["ms"] = int((_t.perf_counter() - t0) * 1000)
    return _j(res)


def _audit_body_p1(_state):
    _fill_bbox, floor, include_hidden, limit, max_tris = (
        _state["_fill_bbox"], _state["floor"], _state["include_hidden"], _state["limit"], _state["max_tris"])
    mm3, mm_per_unit, rays, samples, seed = (
        _state["mm3"], _state["mm_per_unit"], _state["rays"], _state["samples"], _state["seed"])
    sides, ub = _state["sides"], _state["ub"]
    bad, det, fb_hi, fb_lo, rows, t, trunc, who = (_state.get("bad"), _state.get("det"), _state.get("fb_hi"), _state.get("fb_lo"), _state.get("rows"), _state.get("t"), _state.get("trunc"), _state.get("who"))
    try:
        base = {"method": "monte-carlo(ray-parity, 只在交叠 bbox 内采样)", "units": ub,
                "samples": int(max(1, int(samples))), "seed": int(seed), "rays": int(max(1, min(3, int(rays)))),
                "include_hidden": bool(include_hidden), "material_mm3": _r(floor, 6), "limit": int(limit),
                "sides": {t: _side_summary(sides[t], mm_per_unit) for t in ("a", "b")},
                "error_caliber": {
                    "formula": "相对误差(95%) ≈ 1.96·sqrt((1-p)/(p·n))（p=总体积/盒体积，n=samples_used）",
                    "n_200k": {"p=0.5": "±0.44%", "p=0.1": "±1.31%", "p=0.01": "±4.4%"},
                    "zero_hit_upper": "frac=0 时用单侧 95% 上界 1-0.05^(1/n) ≈ 3/n（× 盒体积）",
                    "how_to_tighten": "samples×4 → 误差减半（∝1/√n）；盒越贴合交集体，p 越大越准",
                    "material_mm3": "可执行下限 %.3f mm³：低于它属于网格弦差/噪声量级，不作为真干涉" % floor}}
        err = _sides_error(sides, mm_per_unit)
        if err:
            return dict(base, **err)
        sa, sb = sides["a"], sides["b"]
        ta, tb = sa["tris"], sb["tris"]
        na, nb = int(len(ta)), int(len(tb))
        cap = int(max_tris or BVH_MAX_TRIS)
        over = [(t, v) for t, v in (("a", na), ("b", nb)) if cap > 0 and v > cap]   # cap=0 → 不设上限
        if over:
            return dict(base, ok=False, analyzed=False, tris_a=na, tris_b=nb, max_tris=cap,
                        error="三角面超上限：%s（上限 %d，可用 max_tris 覆盖）→ 不分析"
                              % ("/".join("%s=%d" % kv for kv in over), cap),
                        note="未分析 ≠ 无干涉：verdict 不给，别把「没跑」读成「没问题」")
        tree_a, tree_b = _bvh_from_tris(ta), _bvh_from_tris(tb)
        pairs = list(tree_a.overlap(tree_b) or [])
        pair_count = len(pairs)
        out = dict(base, ok=True, analyzed=True, tris_a=na, tris_b=nb, pair_count=pair_count,
                   pairs=_pair_rows(sa, sb, ta, tb, pairs, limit, mm_per_unit),
                   aabb_a=_box_fields(sa["aabb"][0], sa["aabb"][1], mm_per_unit),
                   aabb_b=_box_fields(sb["aabb"][0], sb["aabb"][1], mm_per_unit),
                   honest_limits=[
                       "不是精确布尔：Monte-Carlo 估计，误差口径见 error_caliber",
                       "只采样交叠三角面的 bbox —— 该盒是交集体的超集，不是交集体本身",
                       "射线奇偶只在闭合流形网格上成立：非流形/开放网格一律 unresolved（不给数）",
                       "共面贴合（零厚度接触）体积本来就是 0；overlap() 也不报共面相交 → 别拿 pair_count=0 "
                       "当成「没有贴合」，贴合看 audit_overlap 的 contact_probe"],
                   why=None, verdict=None, volume_mm3=None, volume_units3=None, volume_ci95_mm3=None)
        health_a, closed_a = _mesh_health(sa["objs"], sa["name_of"])
        health_b, closed_b = _mesh_health(sb["objs"], sb["name_of"])
        out["mesh_health"] = {"a": health_a, "b": health_b, "closed_a": closed_a, "closed_b": closed_b}
        inter = _box_intersect(sa["aabb"][0], sa["aabb"][1], sb["aabb"][0], sb["aabb"][1])
        provable_empty = bool(inter is None or _boxvol(inter) <= 0.0)
        cand = None                    # 采样盒
        cand_src = None
        if pair_count > 0:
            fb_lo, fb_hi, trunc = _pairs_bbox(ta, tb, pairs)
            cand, cand_src = (fb_lo, fb_hi), "overlap_faces(交叠三角面的世界 bbox)"
            if trunc:
                out["pairs_bbox_note"] = ("面对数超过 %d，采样盒只按前 %d 对统计（pair_count 仍是全量）"
                                          % (PAIRS_BBOX_CAP, PAIRS_BBOX_CAP))
        elif not provable_empty:
            cand, cand_src = inter, "aabb_intersect(两侧世界 AABB 的交盒；面无相交时的保守盒)"
        # 非流形/开放 → 降级（先判，省掉采样；但 bbox 与对数照报）
        if pair_count > 0 and not (closed_a and closed_b):
            who = [t for t, c in (("a", closed_a), ("b", closed_b)) if not c]
            det = []
            for t in who:
                rows = health_a if t == "a" else health_b
                bad = [r for r in rows if not r["closed"]]
                det.append("%s: %s" % (t, "、".join("%s(boundary=%s, nonmanifold=%s)"
                                                    % (r["name"], r["boundary_edges"], r["nonmanifold_edges"])
                                                    for r in bad[:4])))
            out.update({"verdict": "unresolved", "volume_mm3": None, "volume_units3": None,
                        "volume_ci95_mm3": None, "frac": None, "samples_used": 0,
                        "in_overlap_bbox": 0, "inside_a": 0, "inside_b": 0, "inside_both": 0,
                        "why": "射线奇偶只在闭合流形网格上成立，而 %s 不是闭合流形（%s）→ 不给体积数字。"
                               "缺证据：boundary_edges=0 且 nonmanifold_edges=0 的闭合网格"
                               % ("/".join(who), " | ".join(det)),
                        "next_step": "把该件修补成闭合流形（或改用 audit_overlap 的面对数）再估体积"})
            _fill_bbox(out, cand, cand_src, mm_per_unit, mm3)
            return out
        if cand is None:               # 可证为空（AABB 不相交/只贴不叠）
            out.update({"verdict": "refuted", "volume_mm3": 0.0, "volume_units3": 0.0,
                        "volume_ci95_mm3": 0.0, "frac": 0.0, "samples_used": 0, "in_overlap_bbox": 0,
                        "inside_a": 0, "inside_b": 0, "inside_both": 0,
                        "upper_bound_mm3": _r(floor, 6),
                        "upper_bound_kind": "两侧世界 AABB 不相交/只贴不叠 → 交集体可证为空（上界即可执行下限）",
                        "why": "面-对重叠 %d 对，且两侧世界 AABB 不相交/只贴不叠 → A∩B 为空（可证，未采样）"
                               % pair_count})
            _fill_bbox(out, None, None, mm_per_unit, mm3)
            return out
        box_vol = float(_boxvol(cand))
        frac, n_used, st = _estimate_volume(ta, tb, tree_a, tree_b, cand, samples, seed, sa["aabb"], sb["aabb"],
                                           rays=rays)
    finally:
        _state.update((k, v) for k, v in locals().items() if k != "_state")

def _audit_body_p2(_state):
    _fill_bbox, box_vol, cand, cand_src, floor = (
        _state["_fill_bbox"], _state.get("box_vol"), _state.get("cand"), _state.get("cand_src"), _state["floor"])
    frac, mm3, mm_per_unit, n_used, out = (
        _state.get("frac"), _state["mm3"], _state["mm_per_unit"], _state.get("n_used"), _state.get("out"))
    pair_count, st = _state.get("pair_count"), _state.get("st")
    need = (_state.get("need"))
    try:
        out.update({"samples_used": int(n_used), "in_overlap_bbox": int(st["points"]),
                    "inside_a": st["inside_a"], "inside_b": st["inside_b"], "inside_both": st["inside_both"],
                    "parity_unknown": {"a": st["unknown_a"], "b": st["unknown_b"]},
                    "frac": _r(frac, 8), "box_volume_units3": _r(box_vol, 8),
                    "box_volume_mm3": _r(box_vol * mm3, 4), "parity_stats": {"a": st["parity_a"], "b": st["parity_b"]}})
        _fill_bbox(out, cand, cand_src, mm_per_unit, mm3)
        vol_u3 = frac * box_vol
        vol_mm3 = vol_u3 * mm3
        sd_frac = (frac * (1.0 - frac) / float(n_used)) ** 0.5
        ci95_frac = 1.96 * sd_frac
        up95_frac = min(1.0, frac + 1.645 * sd_frac) if frac > 0 else min(1.0, 1.0 - 0.05 ** (1.0 / float(n_used)))
        out.update({"volume_units3": _r(vol_u3, 8), "volume_mm3": _r(vol_mm3, 4),
                    "volume_ci95_mm3": _r(ci95_frac * box_vol * mm3, 4),
                    "ci95_lower_mm3": _r(max(0.0, (vol_u3 - ci95_frac * box_vol)) * mm3, 4),
                    "ci95_upper_mm3": _r(min(1.0, frac + ci95_frac) * box_vol * mm3, 4),
                    "relative_ci95": (_r(ci95_frac / frac, 6) if frac > 0 else None),
                    "frac_ci95": _r(ci95_frac, 8),
                    "upper_bound_mm3": _r(max(up95_frac * box_vol * mm3, floor), 4),
                    "upper_bound_kind": ("单侧 95%% 统计上界（frac=0：1-0.05^(1/n) ≈ 3/n × 盒体积；"
                                         "frac>0：frac+1.645·SE）+ 不低于可执行下限 %.3f mm³" % floor)})
        unk = st["unknown_a"] + st["unknown_b"]
        if unk > 0.05 * 2 * float(n_used):
            out.update({"verdict": "unresolved",
                        "why": "射线奇偶在 %d/%d 个点上迭代到上限 %d（>5%%）→ 内外判断不可靠，不给体积结论"
                               % (unk, 2 * n_used, INTERFERENCE_MAX_STEPS)})
            return out
        lower95 = max(0.0, (vol_u3 - ci95_frac * box_vol)) * mm3
        if vol_mm3 > 0 and lower95 > 0:
            if vol_mm3 >= floor:
                out.update({"verdict": "supported",
                            "why": "估计交集体积 %.1f mm³（95%% CI ±%.1f mm³，下界 %.1f mm³ > 0）→ 真干涉；"
                                   "面对数 %d" % (vol_mm3, out["volume_ci95_mm3"], lower95, pair_count)})
            else:
                out.update({"verdict": "refuted", "sub_material": True,
                            "why": "估计交集体积 %.4f mm³ < 可执行下限 %.3f mm³（网格弦差/噪声量级）→ "
                                   "不作为真干涉；上界 %.4f mm³" % (vol_mm3, floor, out["upper_bound_mm3"])})
        elif out["upper_bound_mm3"] <= floor:
            out.update({"verdict": "refuted",
                        "why": "采样 %d 点里没有同时在 A、B 内的点，且单侧 95%% 上界 %.4f mm³ ≤ 可执行下限 "
                               "%.3f mm³ → 无干涉（对着面-对重叠 %d 对读：AABB 相交但实体不相交）"
                               % (n_used, out["upper_bound_mm3"], floor, pair_count)})
        else:
            need = int(box_vol * mm3 * 3.0 / max(floor, 1e-12)) + 1
            out.update({"verdict": "unresolved",
                        "why": "拿不到显著大于 0 的体积：估计 %.4f mm³、上界 %.4f mm³ 高于可执行下限 %.3f mm³ "
                               "→ 判不了（不是「无干涉」也不是「有干涉」）"
                               % (vol_mm3, out["upper_bound_mm3"], floor),
                        "next_step": "samples ≥ %d 才能把上界压到下限（现 n=%d；误差 ∝1/√n）" % (need, n_used),
                        "samples_needed_for_floor": need})
        return out
    finally:
        _state.update((k, v) for k, v in locals().items() if k != "_state")

def _audit_interference_body(_fill_bbox, floor, include_hidden, limit, max_tris, mm3, mm_per_unit, rays, samples, seed, sides, ub):
    _state = dict(locals())
    for _fn_ in (_audit_body_p1, _audit_body_p2):
        _r = _fn_(_state)
        if _r is not None:
            return _r
