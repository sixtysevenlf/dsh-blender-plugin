# -*- coding: utf-8 -*-
"""DSH 车辆外壳：参考图 → 纵向剖面数字 → 放样成壳 → 比例门 + 间隙（构造性防穿模）。

为什么这么设计（对着 GitHub 上的做法定的）：
  * fpagerie/cargen（Blender 插件）证明车壳可以**从参数生成**：长/宽/高、轴距、前轴到车头、轮+胎半径、
    轮宽、轮陷入地板比例、格栅高、引擎盖角度/长度、风挡角、车顶长/角、后窗角/高、后备箱角（米 + 度，角度从水平量）。
  * vehicle-design 技能的「Proportion-First」：轮径/车高（WBR）运动车 35-40% / 超跑 40-45% / 肌肉车 30-35% /
    SUV 25-30%；并明说「再多的表面细节也救不了错的比例」。

关键设计：**轮眉是从剖面里解析扣出来的**（每个站位把底面抬到轮圆之上 + 间隙），不是事后 boolean —— 
所以「轮穿车身」在构造上就不可能发生（穿模的根治，而不是事后检测）。

ops：
    vehicle_spec(...)      —— 参数 → 校验后的 spec（也可给参考图让 human/img 量出的比例填进来）
    vehicle_package(spec)  —— 比例门：WBR / 轴距比 / 前后悬 / 轮距比 / 离地间隙，按车型给容差
    vehicle_base(spec)     —— 放样车壳（含解析轮眉）+ 四个轮 + 车轴/地面标记
    vehicle_selftest / vehicle_help
"""
import json as json
import math

import bmesh as bmesh
import bpy as bpy


VEHICLE_VERSION = 1

# 车型 → 比例区间（来源：vehicle-design 技能的 Proportion-First 协议）
# WBR = 轮径 / 总车高。区间按**真车标定**（911(992) 1300/680 ⇒ 52%；Corolla 1435/632 ⇒ 44%；
# Range Rover 1870/781 ⇒ 42%）—— vehicle-design 技能里的 35-45% 是另一套口径（相对车身而非总高），
# 这里用总高口径并写明，避免拿错基准。
TYPES = {
    "sports":    {"wbr": (0.46, 0.56), "wheelbase_over_len": (0.55, 0.65), "h_over_len": (0.24, 0.30)},
    "supercar":  {"wbr": (0.50, 0.58), "wheelbase_over_len": (0.55, 0.65), "h_over_len": (0.22, 0.28)},
    "muscle":    {"wbr": (0.44, 0.53), "wheelbase_over_len": (0.52, 0.62), "h_over_len": (0.26, 0.32)},
    "suv":       {"wbr": (0.38, 0.46), "wheelbase_over_len": (0.55, 0.68), "h_over_len": (0.32, 0.40)},
    "truck":     {"wbr": (0.34, 0.46), "wheelbase_over_len": (0.55, 0.70), "h_over_len": (0.33, 0.45)},
    "sedan":     {"wbr": (0.40, 0.48), "wheelbase_over_len": (0.55, 0.65), "h_over_len": (0.26, 0.32)},
}
DEFAULTS = {
    "profile_pts": None,   # 显式折线剖面（外表面还原：控制点直接来自参考图）
    "type": "sports", "length_mm": 4300.0, "width_mm": 1850.0, "height_mm": 1250.0,
    "wheelbase_mm": 2600.0, "front_axle_from_front_mm": 900.0, "wheel_r_mm": 340.0, "wheel_w_mm": 245.0,
    "wheel_sink_frac": 0.15, "track_mm": 1580.0, "ride_mm": 110.0, "arch_clear_mm": 12.0,
    "grille_h_mm": 300.0, "hood_angle_deg": 3.0, "hood_len_mm": 1150.0, "windshield_angle_deg": 32.0,
    "roof_len_mm": 950.0, "roof_angle_deg": 0.0, "rear_window_angle_deg": 34.0, "rear_window_h_mm": 330.0,
    "trunk_angle_deg": 6.0,
}


import sys as _sys_kit
_KIT = getattr(_sys_kit.modules.get("dsh_rt_kernel"), "dsh_kit", None)
if _KIT is None:
    raise RuntimeError("vehicle 需要共享内核 K.dsh_kit（由 KERNEL_BOOTSTRAP 注入）")


_j = _KIT.j  # 共享内核（原自带实现已删）
_api = _KIT.api  # 共享内核
def _num(v, name, lo, hi, dflt=None):
    if v is None:
        if dflt is None:
            raise ValueError("%s 必填" % name)
        return float(dflt)
    f = float(v)
    if not (lo <= f <= hi):
        raise ValueError("%s=%g 超出 [%g, %g]" % (name, f, lo, hi))
    return f


def vehicle_spec(**kw):
    """参数 → 校验后的 spec（毫米/度）。未给的取车型默认值。"""
    sp = dict(DEFAULTS)
    for k, v in (kw or {}).items():
        if v is not None and k in sp:
            sp[k] = v
    t = str(sp.get("type") or "sports").lower()
    if t not in TYPES:
        raise ValueError("未知车型 type=%s（允许 %s）" % (sp.get("type"), sorted(TYPES)))
    sp["type"] = t
    sp["length_mm"] = _num(sp.get("length_mm"), "length_mm", 1500.0, 15000.0)
    sp["width_mm"] = _num(sp.get("width_mm"), "width_mm", 800.0, 4000.0)
    sp["height_mm"] = _num(sp.get("height_mm"), "height_mm", 500.0, 5000.0)
    sp["wheelbase_mm"] = _num(sp.get("wheelbase_mm"), "wheelbase_mm", 800.0, 10000.0)
    sp["front_axle_from_front_mm"] = _num(sp.get("front_axle_from_front_mm"), "front_axle_from_front_mm", 100.0, 5000.0)
    sp["wheel_r_mm"] = _num(sp.get("wheel_r_mm"), "wheel_r_mm", 100.0, 1200.0)
    sp["wheel_w_mm"] = _num(sp.get("wheel_w_mm"), "wheel_w_mm", 60.0, 900.0)
    sp["wheel_sink_frac"] = _num(sp.get("wheel_sink_frac"), "wheel_sink_frac", 0.0, 0.6)
    sp["track_mm"] = _num(sp.get("track_mm"), "track_mm", 500.0, 3500.0)
    sp["ride_mm"] = _num(sp.get("ride_mm"), "ride_mm", 20.0, 800.0)
    sp["arch_clear_mm"] = _num(sp.get("arch_clear_mm"), "arch_clear_mm", 0.0, 120.0)
    for k in ("grille_h_mm", "hood_len_mm", "roof_len_mm", "rear_window_h_mm"):
        sp[k] = _num(sp.get(k), k, 0.0, 8000.0)
    for k in ("hood_angle_deg", "windshield_angle_deg", "roof_angle_deg", "rear_window_angle_deg", "trunk_angle_deg"):
        sp[k] = _num(sp.get(k), k, -20.0, 80.0)
    # 一致性：轴距 + 前轴到车头 不能超过车长；轮距不能超过车宽
    if sp["front_axle_from_front_mm"] + sp["wheelbase_mm"] > sp["length_mm"]:
        raise ValueError("前轴到车头(%.0f) + 轴距(%.0f) = %.0f 超过车长 %.0f"
                         % (sp["front_axle_from_front_mm"], sp["wheelbase_mm"],
                            sp["front_axle_from_front_mm"] + sp["wheelbase_mm"], sp["length_mm"]))
    if sp["track_mm"] + sp["wheel_w_mm"] > sp["width_mm"]:
        raise ValueError("轮距(%.0f) + 轮宽(%.0f) 超过车宽(%.0f)" % (sp["track_mm"], sp["wheel_w_mm"], sp["width_mm"]))
    if 2.0 * sp["wheel_r_mm"] > 0.65 * sp["height_mm"]:
        raise ValueError("轮径(%.0f) 比车高(%.0f) 还大" % (2.0 * sp["wheel_r_mm"], sp["height_mm"]))
    return sp


def vehicle_package(spec=None, **kw):
    """比例门（只读数字）：WBR / 轴距比 / 高长比 / 前后悬 / 轮距比 / 离地间隙。逐项 pass/fail。"""
    # 部分参数也要能过：把给的键 merge 到 DEFAULTS 上再校验（否则缺 ride_mm 之类就 KeyError）
    if isinstance(spec, dict) and spec:
        kw = dict({k: x for k, x in spec.items() if k in DEFAULTS}, **kw)
    sp = vehicle_spec(**kw)
    t = str(sp.get("type") or "sports").lower()
    lim = TYPES.get(t, TYPES["sports"])
    wbr = (2.0 * sp["wheel_r_mm"]) / sp["height_mm"]
    wb_l = sp["wheelbase_mm"] / sp["length_mm"]
    h_l = sp["height_mm"] / sp["length_mm"]
    track_w = sp["track_mm"] / sp["width_mm"]
    front_oh = sp["front_axle_from_front_mm"] - sp["wheel_r_mm"]
    rear_oh = sp["length_mm"] - (sp["front_axle_from_front_mm"] + sp["wheelbase_mm"]) - sp["wheel_r_mm"]
    rows = [
        {"metric": "wheel_to_body_ratio", "value": round(wbr, 4), "range": list(lim["wbr"]),
         "verdict": "pass" if lim["wbr"][0] <= wbr <= lim["wbr"][1] else "fail",
         "note": "轮径/车高：人眼对这条最敏感"},
        {"metric": "wheelbase_over_len", "value": round(wb_l, 4), "range": list(lim["wheelbase_over_len"]),
         "verdict": "pass" if lim["wheelbase_over_len"][0] <= wb_l <= lim["wheelbase_over_len"][1] else "fail"},
        {"metric": "height_over_len", "value": round(h_l, 4), "range": list(lim["h_over_len"]),
         "verdict": "pass" if lim["h_over_len"][0] <= h_l <= lim["h_over_len"][1] else "fail"},
        {"metric": "track_over_width", "value": round(track_w, 4), "range": [0.80, 0.92],
         "verdict": "pass" if 0.80 <= track_w <= 0.92 else "fail",
         "note": "轮距/车宽：太窄像玩具，太宽轮子出界"},
        {"metric": "front_overhang_mm", "value": round(front_oh, 1), "range": [0.0, 0.55 * sp["wheel_r_mm"] * 2],
         "verdict": "pass" if front_oh >= 0 else "fail"},
        {"metric": "rear_overhang_mm", "value": round(rear_oh, 1), "range": [0.0, 0.70 * sp["wheel_r_mm"] * 2],
         "verdict": "pass" if rear_oh >= 0 else "fail"},
        {"metric": "ride_mm", "value": round(sp["ride_mm"], 1), "range": [0.15 * sp["wheel_r_mm"], 0.60 * sp["wheel_r_mm"]],
         "verdict": "pass" if 0.15 * sp["wheel_r_mm"] <= sp["ride_mm"] <= 0.60 * sp["wheel_r_mm"] else "fail"},
    ]
    failed = [r["metric"] for r in rows if r["verdict"] == "fail"]
    return _j({"ok": not failed, "verdict": ("pass" if not failed else "fail"), "type": t, "rows": rows,
               "failed": failed, "spec_echo": {k: sp[k] for k in ("length_mm", "width_mm", "height_mm", "wheelbase_mm", "wheel_r_mm")},
               "note": "比例门只读数字：先过它再去建壳；再多的表面细节也救不了错的比例"})


def _profile_points(sp):
    """纵向剖面关键点（x 从车头往车尾，z 向上，单位米）。照 cargen 的参数化。"""
    L = sp["length_mm"] / 1000.0
    H = sp["height_mm"] / 1000.0
    ride = sp["ride_mm"] / 1000.0
    # **折线剖面**：给了控制点就直接用它当上轮廓（可表达方箱/皮卡这类 cargen 族外的形状）
    if sp.get("profile_pts"):
        raw = [list(q) for q in sp["profile_pts"]]
        closed = len(raw[0]) >= 3      # [x, z_top, z_bottom] = **闭合剖面**（上下都来自参考图 ⇒ 可贴到 IoU≈1）
        pts = sorted([(float(q[0]) / 1000.0, float(q[1]) / 1000.0,
                       (float(q[2]) / 1000.0 if closed else ride)) for q in raw], key=lambda t: t[0])
        if len(pts) >= 3:
            top = [(x, zt) for (x, zt, zb) in pts]
            bot = [(x, zb) for (x, zt, zb) in pts]
            if abs(top[0][0]) > 1e-9:
                top = [(0.0, top[0][1])] + top
                bot = [(0.0, bot[0][1])] + bot
            if abs(top[-1][0] - L) > 1e-9:
                top = top + [(L, top[-1][1])]
                bot = bot + [(L, bot[-1][1])]
            return top + list(reversed(bot))
    return _profile_cargen(sp)


def _profile_cargen(sp):
    # ⚠ 拆分后这三行必须在这里重算（原来它们在被切走的头部，漏了会让剖面计算整个抛错）
    L = sp["length_mm"] / 1000.0
    H = sp["height_mm"] / 1000.0
    ride = sp["ride_mm"] / 1000.0
    grille = sp["grille_h_mm"] / 1000.0
    hood_l = sp["hood_len_mm"] / 1000.0
    roof_l = sp["roof_len_mm"] / 1000.0
    rw_h = sp["rear_window_h_mm"] / 1000.0
    a = math.radians(sp["hood_angle_deg"])
    b = math.radians(sp["windshield_angle_deg"])
    c = math.radians(sp["roof_angle_deg"])
    d = math.radians(sp["rear_window_angle_deg"])
    e = math.radians(sp["trunk_angle_deg"])
    x0, x1 = 0.0, hood_l
    z_hood_end = grille + math.tan(a) * hood_l
    x2 = x1 + (H - z_hood_end) / max(0.15, math.tan(b))          # 风挡：升到车顶
    x3 = x2 + roof_l
    z_roof_end = H + math.tan(c) * roof_l
    x4 = x3 + (z_roof_end - rw_h) / max(0.15, math.tan(d))       # 后窗：降到 rw_h
    x5 = min(L, x4 + max(0.0, (L - x4) * 0.85))
    pts = [(0.0, ride), (0.0, grille), (x1, z_hood_end), (x2, H), (x3, z_roof_end),
           (x4, rw_h), (x5, rw_h - math.tan(e) * max(0.0, x5 - x4)), (L, max(ride, rw_h - math.tan(e) * (L - x4))),
           (L, ride)]
    return [p for p in pts if -0.05 <= p[0] <= L + 0.05]


def _bottom_top_at(sp, x, profile):
    """给定纵坐标 x，返回剖面的下/上边界 z（线性插值）。"""
    xs = [p[0] for p in profile]
    lo = profile[0][1]
    hi = profile[0][1]
    # 下边界：polygon 的下包络；上边界：上包络
    segs = [(profile[i], profile[i + 1]) for i in range(len(profile) - 1)]
    for (p, q) in segs:
        if min(p[0], q[0]) - 1e-9 <= x <= max(p[0], q[0]) + 1e-9:
            if abs(q[0] - p[0]) < 1e-9:
                z = p[1]
            else:
                t = (x - p[0]) / (q[0] - p[0])
                z = p[1] + t * (q[1] - p[1])
            lo = min(lo, z)
            hi = max(hi, z)
    return lo, hi


def _arch_bottom(sp, x, base_lo):
    """解析轮眉：站位落在轮子 x 范围内时，把底面抬到轮圆之上 + 间隙 ⇒ 轮永远不可能穿进壳体。"""
    clear = sp["arch_clear_mm"] / 1000.0
    r = sp["wheel_r_mm"] / 1000.0
    sink = sp["wheel_sink_frac"]
    fx = sp["front_axle_from_front_mm"] / 1000.0
    rx = fx + sp["wheelbase_mm"] / 1000.0
    wc_z = r * (1.0 - sink)                       # 轮心高度（sink 比例陷入地板线下）
    out = base_lo
    for wx in (fx, rx):
        dx = abs(x - wx)
        if dx < r + clear:
            zc = wc_z + math.sqrt(max(0.0, (r + clear) ** 2 - dx ** 2))
            out = max(out, zc)
    return out


def _section(sp, x, n=16):
    """站位 x 处的截面环（超椭圆，带车宽收窄）。返回 [(x, y, z), ...]（逆时针）。"""
    L = sp["length_mm"] / 1000.0
    W = sp["width_mm"] / 1000.0
    prof = sp["_profile"]
    lo, hi = _bottom_top_at(sp, x, prof)
    lo = _arch_bottom(sp, x, lo)
    if hi <= lo:
        hi = lo + 1e-4
    t = x / max(1e-9, L)
    # 车宽收窄：车头/车尾收，中部最宽；车顶比腰线窄（tumblehome）
    taper = 1.0 - 0.55 * max(0.0, (abs(t - 0.5) - 0.28) / 0.22) ** 1.4
    hw = 0.5 * W * max(0.35, taper)
    zc = 0.5 * (lo + hi)
    hh = 0.5 * (hi - lo)
    pts = []
    for i in range(int(n)):
        th = 2.0 * math.pi * i / float(n)
        ct, st = math.cos(th), math.sin(th)
        yy = hw * math.copysign(abs(ct) ** (2.0 / 4.0), ct)
        zz = zc + hh * math.copysign(abs(st) ** (2.0 / 4.0), st)
        pts.append((x, yy, zz))
    return pts




__all__ = [name for name in list(globals()) if not name.startswith("__")]
