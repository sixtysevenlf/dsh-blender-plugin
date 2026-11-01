# -*- coding: utf-8 -*-
"""Split from vehicle.py; behavior preserved by the facade."""
from .common import *  # noqa: F401,F403
from .base import vehicle_sections
from .panels import _params_from_stations, _silhouette_iou

def _fit_polyline(xs, top_r, bot_r, ks=(6, 8, 12), iterations=120, seed=0):
    """**折线族**拟合 + 控制点数 K 扫描：K 越少 spec 越简单、IoU 可能略降（给"够用就好"的选择）。"""
    import random
    Lm = max(xs)
    Hm = max(top_r)
    ride = max(1.0, min(bot_r))

    both = len(bot_r) == len(top_r) and (max(bot_r) - min(bot_r)) > 0.02 * Hm

    def make_spec(p):
        return vehicle_spec(profile_pts=p, length_mm=Lm, height_mm=Hm, ride_mm=ride,
                            wheel_r_mm=min(0.30 * Hm, 340.0), wheelbase_mm=0.6 * Lm,
                            front_axle_from_front_mm=0.2 * Lm, track_mm=min(0.5 * Lm, 1500.0),
                            wheel_w_mm=120.0, width_mm=max(1600.0, min(0.5 * Lm, 1500.0) + 160.0))

    def sc(p):
        try:
            sp = make_spec(p)
            sp["_profile"] = _profile_points(sp)
        except Exception:
            return 0.0
        t, b = [], []
        for x in xs:
            lo, hi = _bottom_top_at(sp, x / 1000.0, sp["_profile"])
            lo = _arch_bottom(sp, x / 1000.0, lo)
            t.append(hi * 1000.0)
            b.append(lo * 1000.0)
        return _silhouette_iou(xs, top_r, bot_r, t, b)[0]

    rows = []
    best = None
    for K in ks:
        K = max(4, int(K))
        step = (len(xs) - 1) / float(K - 1)
        pts = [[float(xs[int(round(i * step))]), float(top_r[int(round(i * step))]),
                float(bot_r[int(round(i * step))])] for i in range(K)] if both else \
              [[float(xs[int(round(i * step))]), float(top_r[int(round(i * step))])] for i in range(K)]
        rng = random.Random(int(seed) + K)
        iou0 = sc(pts)
        iou = iou0
        for _ in range(int(iterations)):
            i = rng.randrange(len(pts))
            trial = [list(q) for q in pts]
            trial[i][1] = trial[i][1] + rng.uniform(-0.06, 0.06) * Hm
            if both and rng.random() < 0.5:
                trial[i][2] = trial[i][2] + rng.uniform(-0.06, 0.06) * Hm
            if 0 < i < len(pts) - 1:
                trial[i][0] = min(max(trial[i][0] + rng.uniform(-0.04, 0.04) * Lm, xs[0]), xs[-1])
            s = sc(trial)
            if s > iou + 1e-6:
                iou, pts = s, trial
        rows.append({"control_points": K, "iou_init": round(iou0, 4), "iou": round(iou, 4)})
        if best is None or iou > best[1]:
            best = (pts, iou, K)
    return {"ok": True, "mode": ("polyline_closed" if both else "polyline_top"), "curve": rows,
            "best": {"control_points": best[2], "iou": round(best[1], 4),
                     "profile_pts": [[round(float(q[0]), 1), round(float(q[1]), 1)] for q in best[0]]},
            "note": "折线族能表达 cargen 族外的形状（方箱/皮卡/装甲）；K 越小 spec 越简单，按 curve 挑够用的 K"}


def _vf_stations(side, stations, mm_per_px, known_len_mm, invert_side, wheel_r_px):
    """站表来源：显式 stations 或从侧视图提取；返回 (sts, pkg, err)。"""
    sts = stations
    pkg = None
    if not sts:
        if not side:
            return None, None, _j({"ok": False, "error": "给 stations 或 side"})
        sec = json.loads(vehicle_sections(side=side, mm_per_px=mm_per_px, known_len_mm=known_len_mm,
                                          invert_side=invert_side, wheel_r_px=wheel_r_px))
        if not sec.get("ok"):
            return None, None, _j({"ok": False, "error": "站表提取失败", "sections": sec})
        sts = sec.get("stations") or []
        pkg = sec.get("package") or {}
    sts = [s for s in sts if s.get("z_top_mm") is not None and s.get("z_bottom_mm") is not None]
    if len(sts) < 5:
        return None, None, _j({"ok": False, "error": "有效站位不足 5 个"})
    return sts, pkg, None


def _vf_base_params(params, sts, pkg):
    """起始参数：显式 params 优先，否则用站表解析参数；再补 package 缺省。"""
    base = dict(params or {})
    if not params:
        try:
            base.update({k: x for k, x in _params_from_stations(sts).items() if x is not None})
        except Exception:
            pass
    if pkg:
        base.setdefault("length_mm", pkg.get("length_mm"))
        base.setdefault("height_mm", pkg.get("height_mm"))
        base.setdefault("wheel_r_mm", pkg.get("wheel_r_mm"))
        base.setdefault("wheelbase_mm", pkg.get("wheelbase_mm"))
        base.setdefault("front_axle_from_front_mm", pkg.get("front_axle_from_front_mm"))
    return {k: x for k, x in base.items() if x is not None}


def _vf_sanitize(prm, Lm, Hm):
    """参数自洽化：拟合里 height/length 会被改，轮径/轴距的硬约束必须跟着调 ——
    否则 vehicle_spec 直接抛错、所有候选都变 0 分（实测 IoU 归零）。"""
    p = dict(prm)
    p["length_mm"] = max(800.0, max(float(Lm), float(p.get("length_mm") or 0.0)))
    p["height_mm"] = max(300.0, max(float(Hm), float(p.get("height_mm") or 0.0)))
    hh = float(p["height_mm"])
    ll = float(p["length_mm"])
    wr = float(p.get("wheel_r_mm") or (0.30 * hh))
    p["wheel_r_mm"] = max(80.0, min(wr, 0.30 * hh))
    fa = float(p.get("front_axle_from_front_mm") or (0.22 * ll))
    wb = float(p.get("wheelbase_mm") or (0.60 * ll))
    if fa + wb > 0.96 * ll:
        wb = max(200.0, 0.96 * ll - fa)
    if fa > 0.5 * ll:
        fa = 0.2 * ll
    p["front_axle_from_front_mm"], p["wheelbase_mm"] = fa, wb
    ww = float(p.get("wheel_w_mm") or 245.0)
    tr = float(p.get("track_mm") or 1580.0)
    wd = float(p.get("width_mm") or max(1600.0, tr + ww + 20.0))
    if tr + ww > wd:
        wd = tr + ww + 20.0
    p["width_mm"], p["track_mm"], p["wheel_w_mm"] = wd, tr, ww
    return p


def _vf_candidate(prm, xs, Lm, Hm):
    """参数 → 目标站表处的 (top_mm, bottom_mm) 候选轮廓。"""
    sp = vehicle_spec(**_vf_sanitize(prm, Lm, Hm))
    sp["_profile"] = _profile_points(sp)
    t, b = [], []
    for x in xs:
        xm = x / 1000.0
        lo, hi = _bottom_top_at(sp, xm, sp["_profile"])
        lo = _arch_bottom(sp, xm, lo)
        t.append(hi * 1000.0)
        b.append(lo * 1000.0)
    return t, b


def _vf_score(prm, xs, top_r, bot_r, Lm, Hm):
    """候选轮廓与目标轮廓的区域 IoU（异常 ⇒ 0 分）。"""
    try:
        t, b = _vf_candidate(prm, xs, Lm, Hm)
    except Exception:
        return 0.0
    iou, _, _, _ = _silhouette_iou(xs, top_r, bot_r, t, b)
    return iou


def _vf_search(base, free, xs, top_r, bot_r, Lm, Hm, iterations, seed, target_iou):
    """坐标下降 + 3 次随机多起点（跳过局部极小）；返回 (best_iou, best, start_iou, trace, restarts)。"""
    import random
    rng = random.Random(int(seed))
    cur = dict(base)
    best_iou = _vf_score(cur, xs, top_r, bot_r, Lm, Hm)
    start_iou = best_iou
    best = dict(cur)
    steps = {"grille_h_mm": 60.0, "hood_len_mm": 120.0, "hood_angle_deg": 3.0,
             "windshield_angle_deg": 4.0, "roof_len_mm": 120.0, "roof_angle_deg": 1.5,
             "rear_window_angle_deg": 4.0, "rear_window_h_mm": 60.0, "trunk_angle_deg": 2.0,
             "height_mm": 40.0}
    trace = [{"i": 0, "iou": round(best_iou, 4), "changed": None}]
    restarts = 0
    # 多起点：单次坐标下降容易卡在局部（实测 0.456→0.569 就停）
    for rs in range(3):
        seed_try = dict(best)
        for p in free:
            base_v = float(seed_try.get(p) or 0.0)
            if abs(base_v) > 1e-9:
                seed_try[p] = base_v * (1.0 + rng.uniform(-0.18, 0.18))
            else:
                seed_try[p] = rng.uniform(-4.0, 4.0)
        s = _vf_score(seed_try, xs, top_r, bot_r, Lm, Hm)
        if s > best_iou + 1e-6:
            best_iou, best, restarts = s, seed_try, restarts + 1
            trace.append({"i": -1 - rs, "iou": round(s, 4), "changed": "multistart"})
    for it in range(1, int(iterations) + 1):
        p = str(free[(it - 1) % len(free)])
        stp = float(steps.get(p, 0.05 * abs(float(best.get(p) or 1.0)) or 1.0))
        improved = False
        for direction in (1.0, -1.0):
            trial = dict(best)
            trial[p] = float(trial.get(p) or 0.0) + direction * stp * (2.0 ** rng.randint(-1, 1))
            s = _vf_score(trial, xs, top_r, bot_r, Lm, Hm)
            if s > best_iou + 1e-6:
                best_iou, best, improved = s, trial, True
                trace.append({"i": it, "iou": round(s, 4), "changed": p})
                break
        if not improved and it % max(1, len(free)) == 0:
            # 停滞：随机重启一个参数（跳出局部）
            p2 = str(free[rng.randrange(len(free))])
            trial = dict(best)
            trial[p2] = float(trial.get(p2) or 0.0) * (1.0 + rng.uniform(-0.25, 0.25))
            s = _vf_score(trial, xs, top_r, bot_r, Lm, Hm)
            if s > best_iou + 1e-6:
                best_iou, best = s, trial
                trace.append({"i": it, "iou": round(s, 4), "changed": p2 + " (restart)"})
        if best_iou >= float(target_iou):
            break
    return best_iou, best, start_iou, trace, restarts


def _vf_payload(best_iou, best, start_iou, target_iou, free, iterations, restarts, trace, base):
    """vehicle_fit 回执。"""
    deltas = {k: round(float(best.get(k) or 0.0) - float(base.get(k) or 0.0), 2) for k in free if k in best}
    return _j({"ok": True, "iou": round(best_iou, 4), "iou_start": round(start_iou, 4),
               "target_iou": target_iou, "hit_target": best_iou >= float(target_iou),
               "spec": {k: best[k] for k in best if not str(k).startswith("_")}, "deltas": deltas,
               "free": free, "evaluations": int(iterations), "restarts": restarts,
               "trace": trace[:3] + trace[-5:] if len(trace) > 8 else trace, "init": "stations_analytic",
               "note": "这是「站表 → IoU」闭环：拟合完把 spec 喂 vehicle_base / vehicle_loft 建壳；"
                       "要更好的结果就加 iterations 或缩小 free 参数集"})


def vehicle_fit(side=None, stations=None, mm_per_px=None, known_len_mm=None, params=None,
                free=None, iterations=240, seed=0, target_iou=0.92, invert_side=False, wheel_r_px=None,
                family="cargen", control_points=None):
    """**站表 → IoU 拟合闭环**（第 1 件）：把 15 参数剖面拟合到量出来的轮廓，纯 2D 数学、不需要渲染。

    做法：目标轮廓来自 vehicle_sections 的站表；候选轮廓由 vehicle_spec 的剖面公式现算；
    目标函数 = 两条上下轮廓围出的**区域 IoU**；搜索 = 坐标下降 + 随机重启（迭代次数可配）。
    返回拟合后的 spec（可直接喂 vehicle_base / vehicle_loft）+ 起始/最终 IoU + 轨迹。
    """
    try:
        sts, pkg, err = _vf_stations(side, stations, mm_per_px, known_len_mm, invert_side, wheel_r_px)
        if err is not None:
            return err
        xs = [float(s["x_mm"]) for s in sts]
        top_r = [float(s["z_top_mm"]) for s in sts]
        bot_r = [float(s["z_bottom_mm"]) for s in sts]
        if str(family) == "polyline" or control_points:
            ks = control_points if isinstance(control_points, (list, tuple)) else (control_points or (6, 8, 12))
            ks = tuple(int(k) for k in ks)
            res = _fit_polyline(xs, top_r, bot_r, ks=ks, iterations=max(60, int(iterations) // 2), seed=seed)
            res["iou_start"] = res["curve"][0]["iou_init"] if res.get("curve") else None
            res["target_iou"] = target_iou
            res["hit_target"] = bool(res.get("best", {}).get("iou", 0) >= float(target_iou))
            return _j(res)
        base = _vf_base_params(params, sts, pkg)
        free = list(free or ["grille_h_mm", "hood_len_mm", "hood_angle_deg", "windshield_angle_deg",
                             "roof_len_mm", "roof_angle_deg", "rear_window_angle_deg",
                             "rear_window_h_mm", "trunk_angle_deg"])
        _Lm = max(xs) if xs else 4300.0
        _Hm = max(top_r) if top_r else 1250.0
        best_iou, best, start_iou, trace, restarts = _vf_search(
            base, free, xs, top_r, bot_r, _Lm, _Hm, iterations, seed, target_iou)
        return _vf_payload(best_iou, best, start_iou, target_iou, free, iterations, restarts, trace, base)
    except Exception as e:
        return _j({"ok": False, "error": "%s: %s" % (type(e).__name__, str(e)[:220])})
