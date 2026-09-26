# -*- coding: utf-8 -*-
"""DSH 车辆外壳（P3）：参考图 → 纵向剖面数字 → 放样成壳 → 比例门 + 间隙（构造性防穿模）。

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
import json
import math

import bpy
import bmesh

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


_j = _KIT.j  # 共享内核（原自带实现已删，见 S1）
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


def vehicle_base(spec=None, name="VehicleShell", stations=28, section_pts=16, wheels=True, markers=True, **kw):
    """放样车壳：纵向剖面 → 逐站超椭圆截面 → 桥接成壳（含解析轮眉）+ 四个轮 + 标记。"""
    try:
        if isinstance(spec, dict) and spec:
            kw = dict({k: x for k, x in spec.items() if k in DEFAULTS}, **kw)
        sp = vehicle_spec(**kw)
        sp["_profile"] = _profile_points(sp)
        L = sp["length_mm"] / 1000.0
        nm = str(name or "VehicleShell")
        for nmx in [nm] + ([nm + "_wheel_%s" % s for s in ("fl", "fr", "rl", "rr")] if wheels else []):
            o = bpy.data.objects.get(nmx)
            if o is not None:
                me0 = o.data if o.type == "MESH" else None
                bpy.data.objects.remove(o, do_unlink=True)
                if me0 is not None and me0.users == 0:
                    bpy.data.meshes.remove(me0, do_unlink=True)
        me = bpy.data.meshes.new(nm)
        bm = bmesh.new()
        # 站位：均匀 + **在轮缘处加密** —— 轮眉在 dx=r+clear 处是断崖式的，
        # 均匀站位（0.19m 一段）的弦会直接穿过轮子后缘（实测 58 对面相交）。
        r_w = sp["wheel_r_mm"] / 1000.0
        clear_w = sp["arch_clear_mm"] / 1000.0
        fx_w = sp["front_axle_from_front_mm"] / 1000.0
        rx_w = fx_w + sp["wheelbase_mm"] / 1000.0
        xs = [L * i / float(max(1, int(stations) - 1)) for i in range(int(stations))]
        for wx in (fx_w, rx_w):
            for off in (0.0, -r_w, r_w, -(r_w + clear_w), (r_w + clear_w),
                        -0.5 * r_w, 0.5 * r_w, -0.9 * r_w, 0.9 * r_w):
                xw = wx + off
                if 0.0 <= xw <= L:
                    xs.append(xw)
        xs = sorted(set(round(x, 6) for x in xs))
        rings = []
        for x in xs:
            pts = _section(sp, x, n=int(section_pts))
            rings.append([bm.verts.new(p) for p in pts])
        for a, b in zip(rings, rings[1:]):
            for i in range(len(a)):
                j = (i + 1) % len(a)
                try:
                    bm.faces.new((a[i], a[j], b[j], b[i]))
                except Exception:
                    pass
        for ring in (rings[0], rings[-1]):
            try:
                bm.faces.new(ring)
            except Exception:
                pass
        bmesh.ops.recalc_face_normals(bm, faces=bm.faces)
        bm.to_mesh(me)
        bm.free()
        ob = bpy.data.objects.new(nm, me)
        bpy.context.scene.collection.objects.link(ob)
        # 轮眉精修：**布尔减去略大的轮圆柱**（半径 r + arch_clear）——
        # 解析轮眉已把剖面抬起来，但轮缘处是断崖，粗站位下弦会切进轮子（实测 20 对面相交）。
        # 布尔之后「轮与壳零互穿」是**构造保证**，不依赖站位密度。
        r_b = sp["wheel_r_mm"] / 1000.0
        ww_b = sp["wheel_w_mm"] / 1000.0
        sink_b = sp["wheel_sink_frac"]
        clear_b = sp["arch_clear_mm"] / 1000.0
        fx_b = sp["front_axle_from_front_mm"] / 1000.0
        rx_b = fx_b + sp["wheelbase_mm"] / 1000.0
        wc_z_b = r_b * (1.0 - sink_b)
        cutters = []
        for ax_b in (fx_b, rx_b):
            for sy_b in (1.0, -1.0):
                cme = bpy.data.meshes.new(nm + "_cut")
                cbm = bmesh.new()
                bmesh.ops.create_cone(cbm, cap_ends=True, cap_tris=False, segments=32,
                                      radius1=r_b + clear_b, radius2=r_b + clear_b, depth=ww_b * 1.8,
                                      matrix=__import__("mathutils").Matrix.Rotation(math.radians(90), 4, "X"))
                cbm.to_mesh(cme)
                cbm.free()
                cob = bpy.data.objects.new(nm + "_cut", cme)
                cob.location = (ax_b, sy_b * sp["track_mm"] / 1000.0 / 2.0, wc_z_b)
                bpy.context.scene.collection.objects.link(cob)
                cutters.append(cob)
        for cob in cutters:
            md = ob.modifiers.new("arch_cut", "BOOLEAN")
            md.operation = "DIFFERENCE"
            md.object = cob
        for o in bpy.context.selected_objects:
            o.select_set(False)
        ob.select_set(True)
        bpy.context.view_layer.objects.active = ob
        for md in list(ob.modifiers):
            try:
                bpy.ops.object.modifier_apply(modifier=md.name)
            except Exception:
                pass
        for cob in cutters:
            cme = cob.data
            bpy.data.objects.remove(cob, do_unlink=True)
            if cme is not None and cme.users == 0:
                try:
                    bpy.data.meshes.remove(cme, do_unlink=True)
                except Exception:
                    pass
        made = [nm]
        mmu = _KIT.units()
        wheel_info = []
        if wheels:
            r = sp["wheel_r_mm"] / 1000.0
            ww = sp["wheel_w_mm"] / 1000.0
            sink = sp["wheel_sink_frac"]
            fx = sp["front_axle_from_front_mm"] / 1000.0
            rx = fx + sp["wheelbase_mm"] / 1000.0
            zc = r * (1.0 - sink)
            for sid, sy in (("fl", 1.0), ("fr", -1.0)):
                wme = bpy.data.meshes.new(nm + "_wheel_" + sid)
                wbm = bmesh.new()
                bmesh.ops.create_cone(wbm, cap_ends=True, cap_tris=False, segments=24,
                                      radius1=r, radius2=r, depth=ww,
                                      matrix=__import__("mathutils").Matrix.Rotation(math.radians(90), 4, "X"))
                wbm.to_mesh(wme)
                wbm.free()
                wob = bpy.data.objects.new(nm + "_wheel_" + sid, wme)
                wob.location = (fx, sy * sp["track_mm"] / 1000.0 / 2.0, zc)
                bpy.context.scene.collection.objects.link(wob)
                made.append(wob.name)
                wheel_info.append({"side": sid, "axle": "front", "x_mm": round(fx * mmu, 1),
                                   "z_mm": round(zc * mmu, 1), "r_mm": round(r * mmu, 1)})
            for sid, sy in (("rl", 1.0), ("rr", -1.0)):
                wme = bpy.data.meshes.new(nm + "_wheel_" + sid)
                wbm = bmesh.new()
                bmesh.ops.create_cone(wbm, cap_ends=True, cap_tris=False, segments=24,
                                      radius1=r, radius2=r, depth=ww,
                                      matrix=__import__("mathutils").Matrix.Rotation(math.radians(90), 4, "X"))
                wbm.to_mesh(wme)
                wbm.free()
                wob = bpy.data.objects.new(nm + "_wheel_" + sid, wme)
                wob.location = (rx, sy * sp["track_mm"] / 1000.0 / 2.0, zc)
                bpy.context.scene.collection.objects.link(wob)
                made.append(wob.name)
                wheel_info.append({"side": sid, "axle": "rear", "x_mm": round(rx * mmu, 1),
                                   "z_mm": round(zc * mmu, 1), "r_mm": round(r * mmu, 1)})
        mk = []
        if markers:
            coll = bpy.data.collections.get(nm + "_marks")
            if coll is None:
                coll = bpy.data.collections.new(nm + "_marks")
                bpy.context.scene.collection.children.link(coll)
            for o in list(coll.objects):
                bpy.data.objects.remove(o, do_unlink=True)
            fx = sp["front_axle_from_front_mm"] / 1000.0
            rx = fx + sp["wheelbase_mm"] / 1000.0
            for an, co in (("front_axle", (fx, 0.0, 0.0)), ("rear_axle", (rx, 0.0, 0.0)),
                           ("ground_front", (fx, 0.0, 0.0)), ("nose", (0.0, 0.0, 0.5 * sp["height_mm"] / 1000.0)),
                           ("tail", (L, 0.0, 0.5 * sp["height_mm"] / 1000.0))):
                e = bpy.data.objects.new(nm + "_M_" + an, None)
                e.empty_display_type = "PLAIN_AXES"
                e.empty_display_size = 0.1
                e.location = co
                coll.objects.link(e)
                mk.append(e.name)
            try:
                bpy.context.view_layer.update()
            except Exception:
                pass
        d = [round(float(x) * mmu, 1) for x in ob.dimensions]
        return _j({"ok": True, "object": ob.name, "size_mm": {"len": d[0], "w": d[1], "h": d[2]},
                   "stations": len(xs), "stations_uniform": int(stations), "section_pts": int(section_pts),
                   "vertices": len(ob.data.vertices), "polygons": len(ob.data.polygons),
                   "wheels": wheel_info, "wheel_objects": made[1:], "markers": mk,
                   "arch_clear_mm": sp["arch_clear_mm"],
                   "spec": {k: sp[k] for k in ("type", "length_mm", "width_mm", "height_mm", "wheelbase_mm",
                                               "wheel_r_mm", "track_mm", "ride_mm")},
                   "note": "轮眉是**解析扣出来**的（底面抬到轮圆 + arch_clear）⇒ 轮穿车身在构造上不可能；"
                           "外壳侧轮廓由剖面参数决定 ⇒ 与参考图的差就是剖面参数的差，可逐项改"})
    except Exception as e:
        return _j({"ok": False, "error": "%s: %s" % (type(e).__name__, str(e)[:220])})




def vehicle_sections(side, front=None, top=None, mm_per_px=None, known_len_mm=None,
                     stations=41, invert_side=False, invert_front=False, invert_top=False,
                     threshold=None, wheel_centers_px=None, wheel_r_px=None):
    """三视图 → **站表 + 截面参数 + 包络数字**（外表面还原第 2 步；跑车/复杂外壳通用）。

    side（必填）：侧视图。逐列给 z_top(x)/z_bottom(x) ⇒ 轮廓与站表。
    top（可选）：俯视图。逐列给 half_width(x) ⇒ 平面收放（跑车的腰身）。
    front（可选）：前视图。逐行给宽度 ⇒ 腰线高度 / 侧倾(tumblehome) / 下裙内收。
    标定：给 mm_per_px，或给 known_len_mm（用侧视图 bbox 宽度换算）。
    wheel_centers_px：轮心像素（相对整图）；不给则用**接地段中心**自动定位轮轴 x（可靠），
                     轮径无法从填充轮廓反推（轮子与车身连成一片）⇒ 需 wheel_r_px 或已知规格。
    """
    img = _api("img")
    if img is None:
        return _j({"ok": False, "error": "需要 imgtools（preload 里加 imgtools）"})
    def _scan(p, inv):
        a = {"path": p, "invert": bool(inv)}
        if threshold is not None:
            a["threshold"] = threshold
        return img("scan", a)
    sc = _scan(side, invert_side)
    if not sc.get("ok") or not sc.get("bbox"):
        return _j({"ok": False, "error": "侧视图轮廓没分出来（给 threshold 或先抠背景）", "scan": sc})
    bx, by, bw_, bh_ = sc["bbox"]
    mpp = mm_per_px
    if mpp is None:
        if known_len_mm:
            mpp = float(known_len_mm) / float(bw_)
        else:
            return _j({"ok": False, "error": "需要 mm_per_px 或 known_len_mm（一个已知长度就能标定）"})
    mpp = float(mpp)
    ctop = sc.get("col_top") or []
    cbot = sc.get("col_bottom") or []
    step_c = int(sc.get("col_px_per_step") or 1)
    if not ctop:
        return _j({"ok": False, "error": "侧视图没有 col_top/col_bottom（imgtools 版本旧？）"})
    length_mm = bw_ * mpp
    height_mm = bh_ * mpp
    # 侧视站表：x 从 0（车头）到 length
    rows = []
    n = max(3, int(stations))
    for i in range(n):
        u = i / float(n - 1)
        ci = min(len(ctop) - 1, int(round(u * (len(ctop) - 1))))
        t, b = ctop[ci], cbot[ci]
        if t is None or b is None:
            rows.append({"u": round(u, 4), "x_mm": round(u * length_mm, 1), "empty": True})
            continue
        rows.append({"u": round(u, 4), "x_mm": round(u * length_mm, 1),
                     "z_top_mm": round((bh_ - float(t)) * mpp, 1),
                     "z_bottom_mm": round((bh_ - float(b)) * mpp, 1),
                     "z_px": [int(t), int(b)]})
    # 平面半宽：优先俯视图；否则标记为推算
    hw_src = "none"
    if top:
        tc = _scan(top, invert_top)
        ttop, tbot = tc.get("col_top") or [], tc.get("col_bottom") or []
        if tc.get("ok") and ttop and tbot:
            hw_src = "top_view"
            for r in rows:
                if r.get("empty"):
                    continue
                ci = min(len(ttop) - 1, int(round(r["u"] * (len(ttop) - 1))))
                if ttop[ci] is None or tbot[ci] is None:
                    r["half_w_mm"] = None
                else:
                    r["half_w_mm"] = round((float(tbot[ci]) - float(ttop[ci])) * mpp / 2.0, 1)
            hw_src = "top_view"
    front_w = None
    section = {"n_top": 4.0, "n_top_source": "assumed", "beltline_z_mm": None,
               "tumblehome_mm": None, "sill_tuck_mm": None, "source": "none"}
    if front:
        fc = _scan(front, invert_front)
        prof = fc.get("row_profile") or []
        if fc.get("ok") and prof and fc.get("bbox"):
            fbx, fby, fbw, fbh = fc["bbox"]
            front_w = fbw * mpp
            # 逐行宽度（bbox 内采样，行步长由 row_profile 长度推）
            step_r = max(1, fbh // max(1, len(prof)))
            widest_i = max(range(len(prof)), key=lambda k: prof[k])
            belt_z = (fbh - widest_i * step_r) * mpp
            top_w = max(prof[:max(1, len(prof) // 10)] or [0])
            bot_w = max(prof[-max(1, len(prof) // 10):] or [0])
            section = {"n_top": 4.0, "n_top_source": "assumed",
                       "beltline_z_mm": round(belt_z, 1),
                       "tumblehome_mm": round((prof[widest_i] - top_w) * mpp / 2.0, 1),
                       "sill_tuck_mm": round((prof[widest_i] - bot_w) * mpp / 2.0, 1),
                       "front_width_mm": round(front_w, 1), "source": "front_view"}
    # 轮轴：接地段中心（轮廓里可靠）；轮径需外部给
    ground_runs = []
    run = None
    # 自适应阈值：z_min + 0.35×(中位底边 − z_min)。固定阈值会失效 ——
    # 整条底边（前唇/门槛/后杠）都在"低"的位置时，全车都会被当成接地段（实测只识别出 1 段）。
    _zs = [r["z_bottom_mm"] for r in rows if not r.get("empty") and r.get("z_bottom_mm") is not None]
    _zmin = min(_zs) if _zs else 0.0
    _zmed = sorted(_zs)[len(_zs) // 2] if _zs else 0.0
    _thr = _zmin + 0.35 * max(1.0, (_zmed - _zmin))
    for i, r in enumerate(rows):
        if r.get("empty"):
            continue
        # 轮子与车身在填充轮廓里连成一片 ⇒ 不能只看"贴地几像素"：
        # 取底部**低洼段**（z_bottom 明显低于车高的 15%）并聚类，其中心即轮轴 x
        on_ground = r["z_bottom_mm"] is not None and r["z_bottom_mm"] <= _thr
        gap_ok = run is not None and (r["x_mm"] - run[1]) <= 0.06 * length_mm
        if on_ground and (run is None or not gap_ok):
            if run is not None:
                ground_runs.append(run)
            run = [r["x_mm"], r["x_mm"]]
        elif on_ground:
            run[1] = r["x_mm"]
        elif run is not None:
            ground_runs.append(run); run = None
    if run is not None:
        ground_runs.append(run)
    axles = [round((a + b) / 2.0, 1) for (a, b) in ground_runs if (b - a) >= 0.05 * length_mm]
    wheel_info = {"source": "ground_runs", "axles_x_mm": axles, "threshold_mm": round(_thr, 2),
                  "runs": [[round(a, 1), round(b, 1)] for (a, b) in ground_runs]}
    # ③ 截面轮廓：前视图逐行宽度 → 归一化形状 [{z_frac, hw_frac}]（0=底,1=顶；宽/最宽）
    section_shape = None
    if front:
        fc2 = _scan(front, invert_front)
        prof2 = fc2.get("row_profile") or []
        if fc2.get("ok") and prof2 and max(prof2) > 0:
            npts = max(6, min(20, len(prof2)))
            mx = float(max(prof2))
            pts = []
            for k in range(npts):
                i = int(round(k * (len(prof2) - 1) / float(max(1, npts - 1))))
                pts.append({"z_frac": round(1.0 - i / float(max(1, len(prof2) - 1)), 4),
                            "hw_frac": round(float(prof2[i]) / mx, 4)})
            section_shape = sorted(pts, key=lambda q: q["z_frac"])
    if wheel_centers_px:
        pts = []
        for p in wheel_centers_px:
            px = (float(p[0]) - bx) * mpp
            py = (bh_ - (float(p[1]) - by)) * mpp
            pts.append({"x_mm": round(px, 1), "z_mm": round(py, 1)})
        wheel_info = {"source": "given_px", "centers": pts,
                      "axles_x_mm": [q["x_mm"] for q in pts]}
    if hw_src == "none" and front_w:
        # 没有俯视图但有前视图：用最大宽 × 平面收放假设填 half_w（**声明为假设**）
        for r in rows:
            if r.get("empty"):
                continue
            u = float(r.get("u") or 0.0)
            taper = 1.0 - 0.35 * max(0.0, (abs(u - 0.5) - 0.30) / 0.20) ** 1.4
            r["half_w_mm"] = round(front_w / 2.0 * max(0.62, taper), 1)
        hw_src = "front_view_taper_assumed"
    wheel_r_mm = round(float(wheel_r_px) * mpp, 1) if wheel_r_px else None
    pkg = {"length_mm": round(length_mm, 1), "height_mm": round(height_mm, 1)}
    if len(axles) >= 2:
        pkg["front_axle_from_front_mm"] = axles[0]
        pkg["wheelbase_mm"] = round(axles[-1] - axles[0], 1)
    if front_w:
        pkg["width_mm"] = round(front_w, 1)
    if wheel_r_mm:
        pkg["wheel_r_mm"] = wheel_r_mm
    warn = []
    if hw_src == "none":
        warn.append("没有俯视图：half_w_mm 未给（放样时会按默认收放）")
    if wheel_r_mm is None:
        warn.append("轮径无法从填充轮廓反推：给 wheel_r_px 或用已知规格")
    if len(axles) < 2:
        warn.append("接地段不足两段：轮轴 x 可能没识别出来（给 wheel_centers_px 更稳）")
    return _j({"ok": True, "side": sc.get("path"), "scale_mm_per_px": round(mpp, 5),
               "stations": rows, "station_count": len(rows),
               "half_width_source": hw_src, "section_params": section, "section_shape": section_shape, "wheels": wheel_info,
               "package": pkg, "warnings": warn,
               "note": "站表可直接喂 vehicle_loft；包络数字可喂 vehicle_package 先过比例门",
               "protocol": ["① 先用 package 过比例门 → ② 站表放样 → ③ 正交渲染 vs 原图算 IoU → ④ 改站表参数再跑"]})



def _apply_insets(pts, insets):
    """特征线（几何层）：把落在指定高度带内、指定 x 区间内的环点**向内收** inset_mm ⇒ 折面/凹槽。

    inset 规格：{"z_mm": 900, "band_mm": 60, "inset_mm": 12, "x_mm": [400, 3900], "weight": 1.0}
                也可以用 {"frac": 0.55, ...}（按该站高度比例定位）
    """
    if not insets:
        return pts, 0
    out = []
    hit = 0
    for (x, y, z) in pts:
        ny = y
        for sp in insets:
            band = float(sp.get("band_mm") or 60.0) / 1000.0
            ins = float(sp.get("inset_mm") or 0.0) / 1000.0
            if ins <= 0:
                continue
            w = float(sp.get("weight") or 1.0)
            zt = sp.get("z_mm")
            if zt is None and sp.get("frac") is not None:
                zt = None      # frac 需要该站上下界，交由调用方换算；这里只认 z_mm
            if zt is None:
                continue
            zt = float(zt) / 1000.0
            xr = sp.get("x_mm")
            if xr and not (float(xr[0]) / 1000.0 <= x <= float(xr[1]) / 1000.0):
                continue
            if abs(z - zt) <= band:
                k = 1.0 - (abs(z - zt) / band) ** 2      # 带中心最强，边缘平滑过渡
                ny = ny - (1.0 if y >= 0 else -1.0) * ins * k * w
                hit += 1
        out.append((x, ny, z))
    return out, hit


def _mark_creases_idx(me, ring_co, lines, edge_of=None, attr_name="crease_edge", default_w=0.7):
    """按**环索引**标折痕（可靠）：纵向线 = 各环「离目标最近的那个点」连成的边；横向线 = 某环的整圈边。

    为什么不用 z 带匹配：要求一条边的两个端点都落在带内，而环上顶点分布是离散的，
    目标高度上常常**根本没有边**（实测 frac=0.55 折痕 0 条边）。
    规格：{"frac":0.55}（按各环自身高度比例）· {"z_mm":560}（绝对高度）· {"x_mm":1500}（横向分缝）· {"at":0.5}（环参数）
    """
    if not lines or not ring_co or not ring_co[0]:
        return []
    n = len(ring_co[0])
    K = len(ring_co)
    if edge_of is None:
        edge_of = {}
        for e in me.edges:
            a, b = e.vertices[0], e.vertices[1]
            edge_of[(min(a, b), max(a, b))] = e.index
    attr = me.attributes.get(attr_name)
    if attr is None:
        attr = me.attributes.new(attr_name, "FLOAT", "EDGE")
    out = []
    for sp in lines:
        w = float(sp.get("weight") or default_w)
        marked = 0
        xv = sp.get("x_mm")
        if isinstance(xv, (int, float)) and not isinstance(xv, bool):
            xt = float(xv) / 1000.0
            k = min(range(K), key=lambda q: abs(ring_co[q][0][0] - xt))
            for i in range(n):
                a = k * n + i
                b = k * n + ((i + 1) % n)
                key = (min(a, b), max(a, b))
                if key in edge_of:
                    attr.data[edge_of[key]].value = w
                    marked += 1
        else:
            xr = sp.get("x_mm") if isinstance(sp.get("x_mm"), (list, tuple)) else None
            idxs = []
            for k in range(K):
                if xr and not (float(xr[0]) / 1000.0 - 1e-6 <= ring_co[k][0][0] <= float(xr[1]) / 1000.0 + 1e-6):
                    idxs.append(None)
                    continue
                zs = [q[2] for q in ring_co[k]]
                zb, zt = min(zs), max(zs)
                if sp.get("at") is not None:
                    idxs.append(min(n - 1, max(0, int(round(float(sp["at"]) * (n - 1))))))
                    continue
                if sp.get("frac") is not None:
                    tgt = zb + float(sp["frac"]) * (zt - zb)
                else:
                    tgt = float(sp.get("z_mm") or 0.0) / 1000.0
                idxs.append(min(range(n), key=lambda i: abs(ring_co[k][i][2] - tgt)))
            for k in range(K - 1):
                if idxs[k] is None or idxs[k + 1] is None:
                    continue
                a = k * n + idxs[k]
                b = (k + 1) * n + idxs[k + 1]
                key = (min(a, b), max(a, b))
                if key in edge_of:
                    attr.data[edge_of[key]].value = w
                    marked += 1
        row = {"spec": {q: sp[q] for q in sp if q in ("z_mm", "frac", "at", "x_mm", "weight", "radius_mm", "segments")}, "edges": marked}
        if marked == 0:
            row["hint"] = "没标到边：横向线（x_mm 给数值）会自动插站；纵向线检查 frac/z_mm 是否在几何范围内"
        out.append(row)
    return out


def _mark_creases(me, lines, zmin, zmax, xmin, xmax):
    """特征线（折痕层）：给落在指定高度/位置上的环边打 SubD 折痕权重（面感的来源）。

    规格：{"z_mm": 900, "tol_mm": 40, "x_mm": [400, 3900], "weight": 0.8}   ← 纵向腰线/门线
          {"frac": 0.55, "tol_frac": 0.06, "weight": 0.7}                    ← 按高度比例（旧 crease_shoulder）
          {"x_mm": 1500, "tol_mm": 60, "weight": 1.0}                        ← 横向（A 柱/分缝）
    返回 [{spec 摘要, edges}]。Blender 4+ 折痕走 crease_edge attribute（e.crease_weight 已不存在）。
    """
    if not lines:
        return []
    attr = me.attributes.get("crease_edge")
    if attr is None:
        attr = me.attributes.new("crease_edge", "FLOAT", "EDGE")
    zr = max(1e-6, zmax - zmin)
    xr = max(1e-6, xmax - xmin)
    res = []
    for sp in lines:
        w = float(sp.get("weight") or 0.7)
        zt = sp.get("z_mm")
        if zt is None and sp.get("frac") is not None:
            zt = zmin + float(sp["frac"]) * zr
        xt = sp.get("x_mm") if not isinstance(sp.get("x_mm"), (list, tuple)) else None
        ztol = float(sp.get("tol_mm") or 40.0) / 1000.0 if sp.get("z_mm") is not None else zr * float(sp.get("tol_frac") or 0.06)
        xtol = float(sp.get("tol_mm") or 60.0) / 1000.0
        zrange = sp.get("z_range_mm") if isinstance(sp.get("z_range_mm"), (list, tuple)) else None
        xrange = sp.get("x_mm") if isinstance(sp.get("x_mm"), (list, tuple)) else None
        n = 0
        for e in me.edges:
            a = me.vertices[e.vertices[0]].co
            b = me.vertices[e.vertices[1]].co
            hit = True
            if zt is not None:
                zt_m = float(zt) / 1000.0
                if not (abs(a.z - zt_m) <= ztol and abs(b.z - zt_m) <= ztol):
                    hit = False
            if hit and xt is not None:
                xt_m = float(xt) / 1000.0
                if not (abs(a.x - xt_m) <= xtol and abs(b.x - xt_m) <= xtol):
                    hit = False
            if hit and xrange and not (float(xrange[0]) / 1000.0 - 1e-6 <= a.x <= float(xrange[1]) / 1000.0 + 1e-6):
                hit = False
            if hit and zrange and not (float(zrange[0]) / 1000.0 - 1e-6 <= a.z <= float(zrange[1]) / 1000.0 + 1e-6):
                hit = False
            if hit:
                attr.data[e.index].value = w
                n += 1
        row = {"spec": {k: sp[k] for k in sp if k in ("z_mm", "frac", "x_mm", "tol_mm", "tol_frac", "weight")}, "edges": n}
        if n == 0:
            row["hint"] = ("没命中任何环边：纵向线检查 z_mm/tol_mm 与 z_range；横向线要落在两个站位之间时，"
                           "vehicle_loft 会自动插站（x_mm 给数值即可），或把 tol_mm 放大")
        res.append(row)
    return res


def _ring_from_outline(st, outline, flare=0.0):
    """**自定义截面轮廓**（可非对称：翼型/带弯度/异形）：outline = [[y_mm, z_mm], …] 闭合轮廓。

    与 _ring_from_shape 的区别：shape 是"前视逐行宽度"再镜像（必然左右对称），
    outline 直接用给定轮廓 —— 左右可以不同（翼型有弯度、侧挂件不对称）。
    按站点缩放：y 按该站半宽归一，z 按该站高度归一。
    """
    x = st["x_mm"] / 1000.0
    zt, zb = st.get("z_top_mm"), st.get("z_bottom_mm")
    if zt is None or zb is None:
        return None
    zt, zb = zt / 1000.0, zb / 1000.0
    pts = [(float(q[0]) / 1000.0, float(q[1]) / 1000.0) for q in (outline or [])]
    if len(pts) < 3:
        return None
    ymax = max(abs(q[0]) for q in pts) or 1e-6
    zmin = min(q[1] for q in pts)
    zmax = max(q[1] for q in pts)
    hw = (st.get("half_w_mm") or 0.0) / 1000.0 + flare / 1000.0
    if hw <= 0:
        hw = 0.35 * max(1e-4, zt - zb)
    sy = hw / ymax
    sz = (zt - zb) / max(1e-6, zmax - zmin)
    return [(x, q[0] * sy, zb + (q[1] - zmin) * sz) for q in pts]


def _ring_from_shape(st, shape, flare=0.0):
    """用**前视图量到的截面轮廓**造环（米）：z_frac → 半宽比例 × 本站半宽。"""
    x = st["x_mm"] / 1000.0
    zt, zb = st.get("z_top_mm"), st.get("z_bottom_mm")
    if zt is None or zb is None:
        return None
    zt, zb = zt / 1000.0, zb / 1000.0
    hw = (st.get("half_w_mm") or 0.0) / 1000.0 + flare / 1000.0
    if hw <= 0:
        hw = 0.35 * max(1e-4, zt - zb)
    right = []
    for q in shape:
        z = zb + float(q["z_frac"]) * (zt - zb)
        y = hw * float(q["hw_frac"])
        right.append((x, y, z))
    left = [(x, -y, z) for (x, y, z) in reversed(right)]
    return right + left


def _ring_from_station(st, n=24, n_top=4.0, n_bot=4.0, tumblehome=0.0, beltline_frac=0.55,
                       shoulder_inset=0.0, sill_tuck=0.0, flare=0.0):
    """按「截面参数」生成一个站位环（米）：上/下半用不同超椭圆指数，含侧倾/腰线台阶/下裙内收。"""
    x = st["x_mm"] / 1000.0
    zt = st.get("z_top_mm")
    zb = st.get("z_bottom_mm")
    hw = (st.get("half_w_mm") or 0.0) / 1000.0
    if zt is None or zb is None:
        return None
    zt, zb = zt / 1000.0, zb / 1000.0
    if hw <= 0:
        hw = 0.5 * max(0.35, 1.0 - 0.5 * abs(0.5 - (x / max(1e-9, x + 1.0)))) * (zt - zb)
    hw = hw + flare / 1000.0
    zc = 0.5 * (zt + zb)
    hh = max(1e-4, 0.5 * (zt - zb))
    belt_z = zb + (zt - zb) * float(beltline_frac)
    pts = []
    for i in range(int(n)):
        th = 2.0 * math.pi * i / float(n)
        ct, stn = math.cos(th), math.sin(th)
        up = stn >= 0.0
        exp = 2.0 / (float(n_top) if up else float(n_bot))
        yy = hw * math.copysign(abs(ct) ** exp, ct)
        zz = zc + hh * math.copysign(abs(stn) ** exp, stn)
        # 侧倾：腰线以上按高度线性收（tumblehome 单位 mm，指"从腰线到车顶收多少"）
        if zz > belt_z and tumblehome > 0:
            k = (zz - belt_z) / max(1e-9, (zt - belt_z))
            yy *= max(0.2, 1.0 - (tumblehome / 1000.0) / max(1e-6, hw) * k)
        # 腰线台阶：腰线附近内收一点，形成折面
        if shoulder_inset > 0 and abs(zz - belt_z) < 0.12 * (zt - zb):
            yy *= max(0.2, 1.0 - (shoulder_inset / 1000.0) / max(1e-6, hw))
        # 下裙内收
        if sill_tuck > 0 and zz < zb + 0.18 * (zt - zb):
            yy *= max(0.2, 1.0 - (sill_tuck / 1000.0) / max(1e-6, hw))
        pts.append((x, yy, zz))
    return pts


def vehicle_loft(stations=None, name="Shell", section_pts=24, n_top=4.0, n_bot=4.0,
                 tumblehome_mm=0.0, beltline_frac=0.55, shoulder_inset_mm=0.0, sill_tuck_mm=0.0,
                 flare_mm=0.0, flare_x_mm=None, subsurf=1, crease_shoulder=0.0, close_ends=True,
                 section_shape=None, section_outline=None, crease_lines=None, inset_lines=None,
                 spec=None, side=None, **kw):
    """站表 → 放样成壳（外表面还原第 3 步）。

    stations：vehicle_sections 给的站表（每站 x_mm / z_top_mm / z_bottom_mm / half_w_mm）。
    截面参数：n_top（车顶平度）/ n_bot（底边方度）/ tumblehome_mm（侧倾）/ beltline_frac（腰线位置）/
              shoulder_inset_mm（腰线台阶）/ sill_tuck_mm（下裙内收）。
    flare_mm + flare_x_mm：轮眉**外扩**（跑车后轮拱最典型）；flare_x_mm 默认取站表里 z_bottom 最低的两处。
    crease_shoulder>0：腰线环边加 SubD 折痕（面感的来源）。
    也可以只给 spec：用 vehicle_spec 的剖面先造一张站表（简版跑车）再放样。
    """
    try:
        import bmesh as _bm
        if not stations:
            if not (isinstance(spec, dict) and spec.get("length_mm")) and side is None:
                return _j({"ok": False, "error": "要么给 stations（vehicle_sections 的输出），要么给 spec/side"})
            sp = vehicle_spec(**({k: v for k, v in (spec or {}).items() if k in DEFAULTS}), **kw) if spec else vehicle_spec(**kw)
            sp["_profile"] = _profile_points(sp)
            L = sp["length_mm"] / 1000.0
            n_st = 24
            stations = []
            for i in range(n_st):
                x = L * i / float(n_st - 1)
                lo, hi = _bottom_top_at(sp, x, sp["_profile"])
                lo = _arch_bottom(sp, x, lo)
                stations.append({"x_mm": x * 1000.0, "z_top_mm": hi * 1000.0, "z_bottom_mm": lo * 1000.0,
                                 "half_w_mm": 0.5 * sp["width_mm"] * (1.0 - 0.5 * max(0.0, (abs(x / max(1e-9, L) - 0.5) - 0.28) / 0.22) ** 1.4)})
        sts = [s for s in stations if s.get("z_top_mm") is not None and s.get("z_bottom_mm") is not None]
        if len(sts) < 3:
            return _j({"ok": False, "error": "有效站位不足 3 个（站表可能没量到轮廓）"})
        if flare_mm and not flare_x_mm:
            # 轮拱 = 底边被**抬起**的站位（z_bottom 最大），不是最低处（那是最低的车唇/门槛）
            highs = sorted(sts, key=lambda s: -s["z_bottom_mm"])[:2]
            flare_x_mm = [s["x_mm"] for s in highs]
        fl_set = set()
        if flare_mm and flare_x_mm:
            for s in sts:
                if any(abs(s["x_mm"] - fx) <= 0.12 * (sts[-1]["x_mm"] - sts[0]["x_mm"] + 1.0) for fx in flare_x_mm):
                    fl_set.add(round(s["x_mm"], 3))
        nm = str(name or "Shell")
        o0 = bpy.data.objects.get(nm)
        if o0 is not None:
            me0 = o0.data
            bpy.data.objects.remove(o0, do_unlink=True)
            if me0 is not None and me0.users == 0:
                bpy.data.meshes.remove(me0, do_unlink=True)
        me = bpy.data.meshes.new(nm)
        bm = _bm.new()
        rings = []
        ring_co = []
        # 横向特征线（{"x_mm": 1500}）必须**有站位**才能落到几何上：没有就插一个（否则 0 条边）
        inserted = 0
        for cl in (crease_lines or []):
            xv = cl.get("x_mm")
            if isinstance(xv, bool) or not isinstance(xv, (int, float)):
                continue
            xv = float(xv) / 1000.0
            xs_ = [s["x_mm"] / 1000.0 for s in sts]
            if any(abs(q - xv) <= 1e-4 for q in xs_):
                continue
            left = max([i for i in range(len(xs_)) if xs_[i] <= xv], default=None)
            right = min([i for i in range(len(xs_)) if xs_[i] >= xv], default=None)
            if left is None or right is None or left == right:
                continue
            t = (xv - xs_[left]) / max(1e-9, xs_[right] - xs_[left])
            ns = {"x_mm": xv * 1000.0, "_inserted": True}
            for k in ("z_top_mm", "z_bottom_mm", "half_w_mm"):
                a, b = sts[left].get(k), sts[right].get(k)
                ns[k] = None if (a is None or b is None) else a + t * (b - a)
            sts = sorted(sts + [ns], key=lambda s: s["x_mm"])
            inserted += 1
        inset_hits = 0
        insets_eff = list(inset_lines or [])
        if not insets_eff and shoulder_inset_mm:
            insets_eff = []      # 旧参数走 _ring_from_station 内部逻辑，不重复处理
        for s in sts:
            _fl = flare_mm if round(s["x_mm"], 3) in fl_set else 0.0
            if section_outline:
                ring = _ring_from_outline(s, section_outline, flare=_fl)
            elif section_shape:
                ring = _ring_from_shape(s, section_shape, flare=_fl)
            else:
                ring = _ring_from_station(s, n=int(section_pts), n_top=n_top, n_bot=n_bot,
                                          tumblehome=tumblehome_mm, beltline_frac=beltline_frac,
                                          shoulder_inset=shoulder_inset_mm, sill_tuck=sill_tuck_mm,
                                          flare=_fl)
            if ring is None:
                continue
            # 特征线（几何层）：按高度带/区间向内收 ⇒ 折面、凹槽
            if insets_eff:
                _z0 = min(q[2] for q in ring)
                _z1 = max(q[2] for q in ring)
                eff = []
                for sp in insets_eff:
                    q = dict(sp)
                    if q.get("z_mm") is None and q.get("frac") is not None:
                        q["z_mm"] = (_z0 + float(q["frac"]) * (_z1 - _z0)) * 1000.0
                    eff.append(q)
                ring, h = _apply_insets(ring, eff)
                inset_hits += h
            ring_co.append(list(ring))
            rings.append([bm.verts.new(p) for p in ring])
        for a, b in zip(rings, rings[1:]):
            for i in range(len(a)):
                j = (i + 1) % len(a)
                try:
                    bm.faces.new((a[i], a[j], b[j], b[i]))
                except Exception:
                    pass
        if close_ends:
            for ring in (rings[0], rings[-1]):
                try:
                    bm.faces.new(ring)
                except Exception:
                    pass
        bmesh.ops.recalc_face_normals(bm, faces=bm.faces)
        bm.to_mesh(me)
        bm.free()
        ob = bpy.data.objects.new(nm, me)
        bpy.context.scene.collection.objects.link(ob)
        # 特征线（折痕层）：无论有没有 subsurf 都先标（没 subsurf 时折痕不生效但记录在案）
        _zmin = min(x.co.z for x in me.vertices)
        _zmax = max(x.co.z for x in me.vertices)
        _xmin = min(x.co.x for x in me.vertices)
        _xmax = max(x.co.x for x in me.vertices)
        lines_eff = list(crease_lines or [])
        if crease_shoulder and 0.0 < beltline_frac < 1.0:
            lines_eff = lines_eff + [{"frac": float(beltline_frac), "weight": float(crease_shoulder)}]
        # 特征线分两路：crease（SubD 折痕=硬折线）/ bevel（**圆角**：真半径、真圆弧过渡）
        radius_lines = [q for q in lines_eff if float(q.get("radius_mm") or 0.0) > 0]
        hard_lines = [q for q in lines_eff if float(q.get("radius_mm") or 0.0) <= 0]
        crease_report = _mark_creases_idx(me, ring_co, hard_lines) if hard_lines else []
        bevel_report = (_mark_creases_idx(me, ring_co, radius_lines, attr_name="bevel_weight_edge",
                                         default_w=1.0) if radius_lines else [])
        creases = sum(int(r.get("edges") or 0) for r in crease_report)   # 旧字段：折痕环边总数
        bevel_info = None
        if bevel_report:
            bmax = max(float(q.get("radius_mm") or 0.0) for q in radius_lines)
            segs = int(max([int(q.get("segments") or 3) for q in radius_lines] or [3]))
            bv = ob.modifiers.new("feature_bevel", "BEVEL")
            try:
                bv.affect = "EDGES"
                bv.limit_method = "WEIGHT"
                bv.offset_type = "OFFSET"
            except Exception:
                pass
            bv.width = bmax / 1000.0            # 米（模型就是米）
            bv.segments = max(1, min(8, segs))
            bv.profile = min(0.95, max(0.05, float(radius_lines[0].get("profile") or 0.5)))
            # 0.5 = 正圆；>0.5 偏方（更接近 G2 的平顺过渡）、<0.5 偏凹
            bevel_info = {"radius_mm": bmax, "segments": bv.segments, "profile": round(float(bv.profile), 3), "edges": sum(int(q.get("edges") or 0) for q in bevel_report)}
        if int(subsurf or 0) > 0:
            ss = ob.modifiers.new("subsurf", "SUBSURF")
            ss.levels = int(subsurf)
            ss.render_levels = int(subsurf)
            for o in bpy.context.selected_objects:
                o.select_set(False)
            ob.select_set(True)
            bpy.context.view_layer.objects.active = ob
            for m in list(ob.modifiers):
                try:
                    bpy.ops.object.modifier_apply(modifier=m.name)
                except Exception:
                    pass
        elif bevel_report:
            # 没开 subsurf 也要把圆角落地（否则 bevel 只挂在栈上、几何没变）
            for o in bpy.context.selected_objects:
                o.select_set(False)
            ob.select_set(True)
            bpy.context.view_layer.objects.active = ob
            for m in list(ob.modifiers):
                try:
                    bpy.ops.object.modifier_apply(modifier=m.name)
                except Exception:
                    pass
        d = [round(float(x) * 1000.0, 1) for x in ob.dimensions]
        return _j({"ok": True, "object": ob.name, "stations_used": len(rings),
                   "size_mm": {"len": d[0], "w": d[1], "h": d[2]},
                   "vertices": len(ob.data.vertices), "polygons": len(ob.data.polygons),
                   "section_source": ("custom_outline" if section_outline else ("front_view_shape" if section_shape else "superellipse")),
                   "section_params": {"n_top": n_top, "n_bot": n_bot, "tumblehome_mm": tumblehome_mm,
                                      "beltline_frac": beltline_frac, "shoulder_inset_mm": shoulder_inset_mm,
                                      "sill_tuck_mm": sill_tuck_mm},
                   "flare": {"mm": flare_mm, "at_x_mm": sorted(fl_set) if fl_set else []},
                   "crease_lines": crease_report, "crease_radius": bevel_report, "bevel": bevel_info,
                   "inset_hits": inset_hits, "stations_inserted": inserted,
                   "crease_edges": creases,
                   "note": "站表决定轮廓、截面参数决定性格（平度/侧倾/腰线/下裙）、flare 决定轮拱外扩；"
                           "下一步：正交渲染 + img_diff 对原图算 IoU，改参数再跑"})
    except Exception as e:
        return _j({"ok": False, "error": "%s: %s" % (type(e).__name__, str(e)[:220])})


def vehicle_panels(object_name=None, cuts_mm=None, gap_mm=3.0, separate=True, names=None):
    """按缝切分件（外表面还原第 4 步）：在给定 x 处**减去薄板**（缝宽 gap_mm）⇒ 真实缝；可选分离成多件。"""
    try:
        import bmesh as _bm
        ob = bpy.data.objects.get(str(object_name)) if object_name else bpy.context.view_layer.objects.active
        if ob is None or ob.type != "MESH":
            return _j({"ok": False, "error": "给 object_name（或选中一个 mesh）"})
        if not cuts_mm:
            return _j({"ok": False, "error": "cuts_mm 必填：[x 位置…]（毫米，沿车长）"})
        mmu = _KIT.units()
        cutters = []
        for i, x in enumerate(list(cuts_mm)):
            cme = bpy.data.meshes.new(ob.name + "_cut%d" % i)
            cbm = _bm.new()
            import mathutils as _mu
            size = max(float(ob.dimensions.x), float(ob.dimensions.y), float(ob.dimensions.z)) * 2.0
            _bm.ops.create_cube(cbm, size=1.0, matrix=_mu.Matrix.Diagonal(
                (float(gap_mm) / mmu, size, size, 1.0)))
            cbm.to_mesh(cme)
            cbm.free()
            cob = bpy.data.objects.new(ob.name + "_cut%d" % i, cme)
            cob.location = (float(x) / mmu, float(ob.dimensions.y) * 0.0, float(ob.dimensions.z) * 0.0)
            bpy.context.scene.collection.objects.link(cob)
            cutters.append(cob)
        for cob in cutters:
            md = ob.modifiers.new("panel_cut", "BOOLEAN")
            md.operation = "DIFFERENCE"
            md.object = cob
        for o in bpy.context.selected_objects:
            o.select_set(False)
        ob.select_set(True)
        bpy.context.view_layer.objects.active = ob
        for md in list(ob.modifiers):
            try:
                bpy.ops.object.modifier_apply(modifier=md.name)
            except Exception:
                pass
        for cob in cutters:
            cme = cob.data
            bpy.data.objects.remove(cob, do_unlink=True)
            if cme is not None and cme.users == 0:
                try:
                    bpy.data.meshes.remove(cme, do_unlink=True)
                except Exception:
                    pass
        made = []
        if separate:
            try:
                bpy.ops.mesh.separate(type="LOOSE")
                for o in bpy.context.selected_objects:
                    made.append(o.name)
            except Exception:
                made = [ob.name]
        else:
            made = [ob.name]
        return _j({"ok": True, "source": ob.name, "cuts_mm": list(cuts_mm), "gap_mm": gap_mm,
                   "parts": made, "part_count": len(made),
                   "note": "缝是真几何：用 clear_check 逐对量缝宽（min_mm=gap 的一半左右），左右对称也能量"})
    except Exception as e:
        return _j({"ok": False, "error": "%s: %s" % (type(e).__name__, str(e)[:220])})



def vehicle_regions(stations=None, regions=None, side=None, mm_per_px=None, known_len_mm=None,
                    name="Region", loft_kw=None, invert_side=False, wheel_r_px=None):
    """多区域装配（第 2 件）：把参考图按 x 区间切成几块，**每块单独放样成一个体量**。

    用途：卡车（驾驶室 + 货箱）、装甲（车体 + 炮塔）、科幻（主体 + 吊舱）这类**多体量**车；
    每块都有自己的名字，可单独做细节/打材质/装配验收。
    regions=[{name:"cab", x0_mm:0, x1_mm:2200}, {name:"bed", x0_mm:2250, x1_mm:6000}]
    """
    try:
        if not regions:
            return _j({"ok": False, "error": "regions 必填：[{name, x0_mm, x1_mm}]"})
        sts = stations
        if not sts:
            if not side:
                return _j({"ok": False, "error": "给 stations 或 side（side 时内部先跑 vehicle_sections）"})
            sec = json.loads(vehicle_sections(side=side, mm_per_px=mm_per_px, known_len_mm=known_len_mm,
                                              invert_side=invert_side, wheel_r_px=wheel_r_px))
            if not sec.get("ok"):
                return _j({"ok": False, "error": "站表提取失败", "sections": sec})
            sts = sec.get("stations") or []
        lk = dict(loft_kw or {})
        made = []
        for rg in regions:
            r = dict(rg or {})
            rn = str(r.get("name") or "part")
            x0 = float(r.get("x0_mm") or 0.0)
            x1 = float(r.get("x1_mm") or 0.0)
            sub = [s for s in sts if s.get("z_top_mm") is not None
                   and x0 - 1e-6 <= float(s.get("x_mm") or -1e9) <= x1 + 1e-6]
            if len(sub) < 3:
                made.append({"name": rn, "ok": False, "error": "该区间内有效站位不足 3 个（%d）" % len(sub)})
                continue
            out = json.loads(vehicle_loft(stations=sub, name="%s_%s" % (name, rn), **lk))
            made.append({"name": rn, "ok": bool(out.get("ok")), "object": out.get("object"),
                         "size_mm": out.get("size_mm"), "stations_used": out.get("stations_used"),
                         "error": out.get("error")})
        ok = all(m.get("ok") for m in made)
        return _j({"ok": ok, "regions": made, "count": len(made),
                   "note": "每个区域是独立体量：装配/细节/材质各自处理；区间之间留缝用 vehicle_panels 或直接给相邻区间留 gap"})
    except Exception as e:
        return _j({"ok": False, "error": "%s: %s" % (type(e).__name__, str(e)[:220])})


def _silhouette_iou(xs, top_a, bot_a, top_b, bot_b):
    """两条上下轮廓围出的**区域 IoU**（纯 2D，梯形积分）。返回 (iou, area_a, area_b, inter)。"""
    inter = 0.0
    area_a = 0.0
    area_b = 0.0
    for i in range(len(xs) - 1):
        dx = xs[i + 1] - xs[i]
        if dx <= 0:
            continue
        a_lo, a_hi = bot_a[i], top_a[i]
        b_lo, b_hi = bot_b[i], top_b[i]
        area_a += 0.5 * dx * ((a_hi - a_lo) + (top_a[i + 1] - bot_a[i + 1]))
        area_b += 0.5 * dx * ((b_hi - b_lo) + (top_b[i + 1] - bot_b[i + 1]))
        lo = max(a_lo, b_lo)
        hi = min(a_hi, b_hi)
        lo2 = max(bot_a[i + 1], bot_b[i + 1])
        hi2 = min(top_a[i + 1], top_b[i + 1])
        inter += 0.5 * dx * (max(0.0, hi - lo) + max(0.0, hi2 - lo2))
    union = area_a + area_b - inter
    return ((inter / union) if union > 1e-9 else 0.0), area_a, area_b, inter


def _params_from_stations(sts):
    """从站表**解析反推**剖面参数（拟合的初值）：车顶平台 + 斜率突变定风挡/后窗/后备箱。"""
    xs = [float(s["x_mm"]) for s in sts]
    zs = [float(s["z_top_mm"]) for s in sts]
    n = len(xs)
    L = max(1e-6, xs[-1])
    H = max(zs)
    z0 = zs[0]
    out = {"length_mm": L, "height_mm": max(1.0, H)}
    top_idx = [i for i, z in enumerate(zs) if z >= H - 0.03 * max(1.0, H)]
    if not top_idx:
        return out
    i0, i1 = top_idx[0], top_idx[-1]
    j = max(0, i0 - 1)
    out["grille_h_mm"] = max(1.0, zs[0])
    out["hood_len_mm"] = max(50.0, xs[j])
    out["hood_angle_deg"] = max(0.0, min(60.0, math.degrees(math.atan2(zs[j] - z0, max(1e-6, xs[j])))))
    dz = H - zs[j]
    dx = max(1e-6, xs[i0] - xs[j])
    out["windshield_angle_deg"] = max(5.0, min(80.0, math.degrees(math.atan2(dz, dx))))
    out["roof_len_mm"] = max(50.0, xs[i1] - xs[i0])
    k = min(n - 1, i1 + 1)
    while k < n - 1 and zs[k] > zs[-1] + 0.05 * max(1.0, H - zs[-1]):
        k += 1
    out["rear_window_h_mm"] = max(1.0, zs[k])
    d2 = H - zs[k]
    x2 = max(1e-6, xs[k] - xs[i1])
    out["rear_window_angle_deg"] = max(5.0, min(80.0, math.degrees(math.atan2(d2, x2))))
    out["trunk_angle_deg"] = max(0.0, min(45.0, math.degrees(math.atan2(max(0.0, zs[k] - zs[-1]),
                                                                        max(1e-6, L - xs[k])))))
    zb = [float(s.get("z_bottom_mm") or 0.0) for s in sts]
    out["ride_mm"] = max(20.0, min(zb))
    try:
        lows = sorted(rgs for rgs in [(abs(b - (min(zb) + 0.25 * (max(zb) - min(zb) + 1.0))), xs[i])
                                      for i, b in enumerate(zb)])
    except Exception:
        pass
    return out


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


def vehicle_fit(side=None, stations=None, mm_per_px=None, known_len_mm=None, params=None,
                free=None, iterations=240, seed=0, target_iou=0.92, invert_side=False, wheel_r_px=None,
                family="cargen", control_points=None):
    """**站表 → IoU 拟合闭环**（第 1 件）：把 15 参数剖面拟合到量出来的轮廓，纯 2D 数学、不需要渲染。

    做法：目标轮廓来自 vehicle_sections 的站表；候选轮廓由 vehicle_spec 的剖面公式现算；
    目标函数 = 两条上下轮廓围出的**区域 IoU**；搜索 = 坐标下降 + 随机重启（迭代次数可配）。
    返回拟合后的 spec（可直接喂 vehicle_base / vehicle_loft）+ 起始/最终 IoU + 轨迹。
    """
    try:
        import random
        sts = stations
        pkg = None
        if not sts:
            if not side:
                return _j({"ok": False, "error": "给 stations 或 side"})
            sec = json.loads(vehicle_sections(side=side, mm_per_px=mm_per_px, known_len_mm=known_len_mm,
                                              invert_side=invert_side, wheel_r_px=wheel_r_px))
            if not sec.get("ok"):
                return _j({"ok": False, "error": "站表提取失败", "sections": sec})
            sts = sec.get("stations") or []
            pkg = sec.get("package") or {}
        sts = [s for s in sts if s.get("z_top_mm") is not None and s.get("z_bottom_mm") is not None]
        if len(sts) < 5:
            return _j({"ok": False, "error": "有效站位不足 5 个"})
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
        base = {k: x for k, x in base.items() if x is not None}
        free = list(free or ["grille_h_mm", "hood_len_mm", "hood_angle_deg", "windshield_angle_deg",
                             "roof_len_mm", "roof_angle_deg", "rear_window_angle_deg",
                             "rear_window_h_mm", "trunk_angle_deg"])

        _Lm = max(xs) if xs else 4300.0
        _Hm = max(top_r) if top_r else 1250.0

        def _sanitize(prm):
            """参数自洽化：拟合里 height/length 会被改，轮径/轴距的硬约束必须跟着调 ——
            否则 vehicle_spec 直接抛错、所有候选都变 0 分（实测 IoU 归零）。"""
            p = dict(prm)
            p["length_mm"] = max(800.0, max(float(_Lm), float(p.get("length_mm") or 0.0)))
            p["height_mm"] = max(300.0, max(float(_Hm), float(p.get("height_mm") or 0.0)))
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

        def cand_profile(prm):
            sp = vehicle_spec(**_sanitize(prm))
            sp["_profile"] = _profile_points(sp)
            t, b = [], []
            for x in xs:
                xm = x / 1000.0
                lo, hi = _bottom_top_at(sp, xm, sp["_profile"])
                lo = _arch_bottom(sp, xm, lo)
                t.append(hi * 1000.0)
                b.append(lo * 1000.0)
            return t, b

        def score(prm):
            try:
                t, b = cand_profile(prm)
            except Exception:
                return 0.0
            iou, _, _, _ = _silhouette_iou(xs, top_r, bot_r, t, b)
            return iou

        rng = random.Random(int(seed))
        cur = dict(base)
        best_iou = score(cur)
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
            s = score(seed_try)
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
                s = score(trial)
                if s > best_iou + 1e-6:
                    best_iou, best, improved = s, trial, True
                    trace.append({"i": it, "iou": round(s, 4), "changed": p})
                    break
            if not improved and it % max(1, len(free)) == 0:
                # 停滞：随机重启一个参数（跳出局部）
                p2 = str(free[rng.randrange(len(free))])
                trial = dict(best)
                trial[p2] = float(trial.get(p2) or 0.0) * (1.0 + rng.uniform(-0.25, 0.25))
                s = score(trial)
                if s > best_iou + 1e-6:
                    best_iou, best = s, trial
                    trace.append({"i": it, "iou": round(s, 4), "changed": p2 + " (restart)"})
            if best_iou >= float(target_iou):
                break
        deltas = {k: round(float(best.get(k) or 0.0) - float(base.get(k) or 0.0), 2) for k in free if k in best}
        return _j({"ok": True, "iou": round(best_iou, 4), "iou_start": round(start_iou, 4),
                   "target_iou": target_iou, "hit_target": best_iou >= float(target_iou),
                   "spec": {k: best[k] for k in best if not str(k).startswith("_")}, "deltas": deltas,
                   "free": free, "evaluations": int(iterations), "restarts": restarts,
                   "trace": trace[:3] + trace[-5:] if len(trace) > 8 else trace, "init": "stations_analytic",
                   "note": "这是「站表 → IoU」闭环：拟合完把 spec 喂 vehicle_base / vehicle_loft 建壳；"
                           "要更好的结果就加 iterations 或缩小 free 参数集"})
    except Exception as e:
        return _j({"ok": False, "error": "%s: %s" % (type(e).__name__, str(e)[:220])})

def vehicle_selftest():
    """自检：比例门该过/该挂 + 建壳 + 轮与壳**零互穿**（解析轮眉的硬承诺）+ 尺寸吻合。"""
    ev = {}
    try:
        sp = vehicle_spec(type="sports", length_mm=4300.0, width_mm=1850.0, height_mm=1250.0,
                          wheelbase_mm=2600.0, front_axle_from_front_mm=900.0, wheel_r_mm=340.0,
                          wheel_w_mm=245.0, track_mm=1580.0, hood_len_mm=1150.0, roof_len_mm=950.0)
        pk = json.loads(vehicle_package(spec=sp))
        ev["package"] = {"ok": pk.get("ok"), "failed": pk.get("failed")}
        bad = vehicle_spec(type="sports", wheel_r_mm=150.0)          # 轮太小 ⇒ WBR 挂
        ev["bad_wbr"] = json.loads(vehicle_package(spec=bad)).get("failed")
        r = json.loads(vehicle_base(spec=sp, name="__dsh_veh_selftest", stations=24, section_pts=14))
        ev["base"] = {k: r.get(k) for k in ("ok", "size_mm", "vertices", "polygons")}
        ev["wheels"] = len(r.get("wheels") or [])
        # 零互穿：车壳 vs 四个轮，用 audit_clearance（同一进程里若已注入该模块）
        aud = __import__("sys").modules.get("dsh_rt_kernel")
        api = getattr(aud, "dsh_clearance_api", None) if aud is not None else None
        if api is not None and r.get("wheel_objects"):
            cl = api("check", {"pairs": [{"id": "wheel_vs_shell", "a": [r["wheel_objects"][0]],
                                          "b": [r["object"]], "min_mm": 0.0}]})
            p0 = (cl.get("pairs") or [{}])[0]
            ev["clearance"] = {"gap_mm": p0.get("gap_mm"), "interpenetrating": p0.get("interpenetrating"),
                               "why": p0.get("why")}
        else:
            ev["clearance"] = "clearance 模块未注入（preload 里加 clearance 可一并验零互穿）"
        size = r.get("size_mm") or {}
        ok = (pk.get("ok") is True and ev["bad_wbr"] and "wheel_to_body_ratio" in ev["bad_wbr"]
              and r.get("ok") is True and abs((size.get("len") or 0) - 4300.0) < 60.0
              and len(r.get("wheels") or []) == 4)
        if isinstance(ev.get("clearance"), dict) and ev["clearance"].get("interpenetrating") is not None:
            ok = ok and ev["clearance"].get("interpenetrating") is False
        return _j({"ok": bool(ok), "evidence": ev})
    except Exception as e:
        return _j({"ok": False, "evidence": ev, "error": "%s: %s" % (type(e).__name__, str(e)[:220])})
    finally:
        for o in [x for x in bpy.data.objects if x.name.startswith("__dsh_veh_selftest")]:
            me = o.data if o.type == "MESH" else None
            bpy.data.objects.remove(o, do_unlink=True)
            if me is not None and me.users == 0:
                try:
                    bpy.data.meshes.remove(me, do_unlink=True)
                except Exception:
                    pass
        c = bpy.data.collections.get("__dsh_veh_selftest_marks")
        if c is not None:
            bpy.data.collections.remove(c)


def vehicle_help():
    return _j({
        "module": "vehicle.py", "version": VEHICLE_VERSION,
        "what": "车辆外壳：参考图/参数 → 纵向剖面 → 放样成壳（解析轮眉）→ 比例门 + 零互穿保证",
        "ops": ["vehicle_spec", "vehicle_package", "vehicle_sections", "vehicle_loft", "vehicle_panels", "vehicle_regions", "vehicle_fit", "vehicle_base", "vehicle_selftest", "vehicle_help"],
        "types": TYPES, "defaults": DEFAULTS,
        "protocol": ["① 先从参考图量：车长/车高/轴距/轮径（img_scan 的 bbox + row_profile，或直接给像素点）",
                     "② vehicle_package 过比例门（WBR / 轴距比 / 高长比 / 轮距比）—— 不过就别建壳",
                     "③ vehicle_base 建壳 + 轮（轮眉解析扣出 ⇒ 零互穿）",
                     "④ qc_render_views 正交侧视图 + img_diff 对参考图算 IoU，改剖面参数再跑"],
        "limits": ["超级椭圆截面是**体量级**外壳：贴参考图靠剖面参数与 IoU 迭代，不靠雕曲面",
                   "轮眉扣的是轮心圆柱（不含悬挂行程）；要留行程就把 arch_clear_mm 加大",
                   "没做 A 柱/玻璃分件与细节件（灯/格栅/后视镜）—— 那些用硬表面单独做再挂"],
    })


def vehicle_dispatch(op, args_json):
    args = {}
    if isinstance(args_json, str) and args_json.strip():
        try:
            args = json.loads(args_json)
        except Exception as e:
            return _j({"ok": False, "error": "args 不是合法 JSON: %s" % str(e)[:120]})
    kw = {}
    for k, v in (args or {}).items():
        if k == "args" and isinstance(v, dict):
            kw.update(v)
        else:
            kw[k] = v
    try:
        kw = _KIT.resolve_refs(kw)   # S5-b：单步调用也支持 "@工件"
    except KeyError as e:
        return _j({"ok": False, "error": "引用解析失败: %s" % str(e)[:160]})
    ops = {"spec": lambda **k: _j(vehicle_spec(**k)), "package": vehicle_package, "base": vehicle_base,
           "sections": vehicle_sections, "loft": vehicle_loft, "panels": vehicle_panels,
           "regions": vehicle_regions, "fit": vehicle_fit,
           "selftest": vehicle_selftest, "help": vehicle_help}
    fn = ops.get(str(op))
    if fn is None:
        return _j({"ok": False, "error": "unknown vehicle op", "op": op, "ops": sorted(ops)})
    try:
        return fn(**kw)
    except Exception as e:
        return _j({"ok": False, "error": "%s: %s" % (type(e).__name__, str(e)[:220]), "op": op})


_DshApi = _KIT.Api  # 共享内核（尾部注册行无需改）
import sys as _sys
_K = _sys.modules.get("dsh_rt_kernel")
if _K is not None:
    _K.dsh_vehicle_api = _DshApi({"version": VEHICLE_VERSION, "dispatch": vehicle_dispatch, "spec": vehicle_spec,
                                  "package": vehicle_package, "base": vehicle_base, "selftest": vehicle_selftest,
                                  "help": vehicle_help})