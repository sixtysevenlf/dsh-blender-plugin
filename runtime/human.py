# -*- coding: utf-8 -*-
"""DSH 人形素体（P2 · 零素材路线）：模型只填数字，几何由 Skin + Subsurf 生成。

为什么走这条：flash 手写人体顶点/拓扑做不到，而硬表面是它的强项 —— 所以人形只需要
**比例正确的素体**（可装配、可摆姿、可挂装甲），不需要写实解剖。

ops：
    human_base(spec|height_mm,heads,shoulder_w_mm,pose) —— 骨架线 → Skin → Subsurf → 素体 + 关节标记
    human_measure(objects|name)  —— 量：总高 / 头高 / 肩宽 / 头身比（门友好顶层字段）
    human_spec(image)            —— 从参考图轮廓反推像素比（零下载）
    human_selftest / human_help

几何按米建（Blender 默认 1 单位 = 1 m），回执一律给 mm。
经典关节高度（头顶=0，脚底=7.5 头身，单位=头高）：
    下巴 1.0 · 肩 1.5 · 胸 2.0 · 腰 3.0 · 胯 3.75 · 膝 5.5 · 踝 7.3 · 脚底 7.5
"""
import json

import bpy
import bmesh

HUMAN_VERSION = 2

LM = {
    "top": 0.0, "chin": 1.0, "shoulder": 1.5, "chest": 2.0, "waist": 3.0,
    "pelvis": 3.75, "knee": 5.5, "ankle": 7.3, "sole": 7.5,
}
DEFAULT_HEADS = 7.5


import sys as _sys_kit
_KIT = getattr(_sys_kit.modules.get("dsh_rt_kernel"), "dsh_kit", None)
if _KIT is None:
    raise RuntimeError("human 需要共享内核 K.dsh_kit（由 KERNEL_BOOTSTRAP 注入）")


_j = _KIT.j  # 共享内核（原自带实现已删，见 S1）
_api = _KIT.api  # 共享内核
def _num(v, name, lo, hi, default=None):
    if v is None:
        if default is None:
            raise ValueError("%s 必填" % name)
        return float(default)
    f = float(v)
    if not (lo <= f <= hi):
        raise ValueError("%s=%g 超出 [%g, %g]" % (name, f, lo, hi))
    return f


def _opt(v, name, lo, hi):
    return None if v is None else _num(v, name, lo, hi)


def build_spec(height_mm=None, heads=None, shoulder_w_mm=None, depth_mm=None, arm_span_mm=None,
               pose="A", limb_r_mm=None):
    """身高 + 头身比 + 可选肩宽 → 关节坐标（米）。缺省按经典比例推。"""
    h_mm = _num(height_mm, "height_mm", 300.0, 3000.0, 1750.0)
    heads = _num(heads, "heads", 5.0, 9.0, DEFAULT_HEADS)
    limb_r = _num(limb_r_mm, "limb_r_mm", 5.0, 200.0, 55.0)
    H = h_mm / 1000.0
    hd = H / heads
    z = lambda k: H - LM[k] * hd
    _sw = _opt(shoulder_w_mm, "shoulder_w_mm", 100.0, 1500.0)
    sw = (_sw / 1000.0) if _sw else (1.5 * hd)          # 肩宽≈1.5 头高（7.5 头身 1.75m ⇒ 350mm）
    _dep = _opt(depth_mm, "depth_mm", 50.0, 800.0)
    dep = (_dep / 1000.0) if _dep else (0.55 * sw)
    _span = _opt(arm_span_mm, "arm_span_mm", 300.0, 3000.0)
    span = (_span / 1000.0) if _span else (h_mm * 0.98 / 1000.0)
    hip_w = 0.75 * sw
    pts = {}
    pts["pelvis"] = (0.0, 0.0, z("pelvis"))
    pts["waist"] = (0.0, 0.0, z("waist"))
    pts["chest"] = (0.0, 0.0, z("chest"))
    pts["neck"] = (0.0, 0.0, z("shoulder") + 0.35 * hd)
    pts["head"] = (0.0, 0.0, z("top") - 0.5 * hd)
    arm = {"A": (0.42, -0.42), "T": (0.0, 0.0)}.get(str(pose).upper(), (0.42, -0.42))
    for side, sx in (("l", 1.0), ("r", -1.0)):
        upper = 0.45 * span / 2.0
        fore = 0.42 * span / 2.0
        ex = sx * (sw / 2.0 + upper * arm[0])
        ex2 = sx * (sw / 2.0 + upper * (arm[0] + 0.15) + fore * arm[0])
        pts["shoulder_" + side] = (sx * sw / 2.0, 0.0, z("shoulder"))
        pts["elbow_" + side] = (ex, 0.0, z("shoulder") + upper * arm[1])
        pts["wrist_" + side] = (ex2, 0.0, z("shoulder") + (upper + fore) * arm[1])
        pts["hip_" + side] = (sx * hip_w / 2.0, 0.0, z("pelvis"))
        pts["knee_" + side] = (sx * hip_w / 2.0 * 0.85, 0.0, z("knee"))
        pts["ankle_" + side] = (sx * hip_w / 2.0 * 0.8, 0.0, z("ankle"))
        pts["foot_" + side] = (sx * hip_w / 2.0 * 0.8, -0.12 * dep, z("sole") + 0.02 * hd)
    chain = [("pelvis", "waist"), ("waist", "chest"), ("chest", "neck"), ("neck", "head")]
    for side in ("l", "r"):
        chain += [("chest", "shoulder_" + side), ("shoulder_" + side, "elbow_" + side),
                  ("elbow_" + side, "wrist_" + side), ("pelvis", "hip_" + side),
                  ("hip_" + side, "knee_" + side), ("knee_" + side, "ankle_" + side),
                  ("ankle_" + side, "foot_" + side)]
    return {"height_mm": h_mm, "heads": heads, "head_mm": hd * 1000.0, "shoulder_w_mm": sw * 1000.0,
            "depth_mm": dep * 1000.0, "arm_span_mm": span * 1000.0, "pose": str(pose).upper(),
            "limb_r_mm": limb_r, "points": pts, "chain": chain}


def _radii(spec, name, head_scale=1.0):
    """按部位给蒙皮半径（米）：躯干粗、四肢细、颈最细，全由身高/头高推。"""
    hd = spec["head_mm"] / 1000.0
    r = spec["limb_r_mm"] / 1000.0
    sw = spec["shoulder_w_mm"] / 1000.0
    if name in ("chest", "waist"):
        return (sw * 0.30, spec["depth_mm"] / 1000.0 * 0.45)
    if name == "pelvis":
        return (sw * 0.28, spec["depth_mm"] / 1000.0 * 0.42)
    if name == "neck":
        return (r * 0.62, r * 0.62)
    if name == "head":
        return (hd * 0.42 * head_scale, hd * 0.48 * head_scale)
    if name.startswith("shoulder"):
        return (r * 1.2, r * 1.2)
    if name.startswith("elbow"):
        return (r * 0.85, r * 0.85)
    if name.startswith("wrist"):
        return (r * 0.62, r * 0.62)
    if name.startswith("hip"):
        return (r * 1.35, r * 1.35)
    if name.startswith("knee"):
        return (r * 0.95, r * 0.95)
    if name.startswith("ankle"):
        return (r * 0.62, r * 0.62)
    return (r, r)


def _drop(nm):
    o = bpy.data.objects.get(nm)
    if o is None:
        return
    me = o.data if o.type == "MESH" else None
    bpy.data.objects.remove(o, do_unlink=True)
    if me is not None and me.users == 0:
        try:
            bpy.data.meshes.remove(me, do_unlink=True)
        except Exception:
            pass


def _build_once(sp, nm, head_scale=1.0, subsurf=1, smooth=True):
    """建一次（Skin + Subsurf 全部应用）。返回 (ob, applied)。"""
    _drop(nm)
    me = bpy.data.meshes.new(nm)
    bm = bmesh.new()
    layer = bm.verts.layers.skin.verify()
    vmap = {}
    # ⚠ 实测（Blender 5.2）：Skin 的**末端大球**不成立（头中心会算到网格顶之上）⇒
    # 躯干/四肢/颈走 Skin，**头单独用一个精确椭球**（同 bmesh 内生成，随对象一起缩放）。
    import mathutils
    hd = sp["head_mm"] / 1000.0 * float(head_scale)
    head_w, head_d = hd * 0.75, hd * 0.90
    mtx = (mathutils.Matrix.Translation(sp["points"]["head"]) @
           mathutils.Matrix.Diagonal((head_w / 2.0, head_d / 2.0, hd / 2.0, 1.0)))
    res = bmesh.ops.create_uvsphere(bm, u_segments=24, v_segments=16, radius=1.0, matrix=mtx)
    head_faces = [f for f in bm.faces]
    skin_points = {k2: v for k2, v in sp["points"].items() if k2 != "head"}
    chain = [e for e in sp["chain"] if "head" not in e]
    for pname, co in skin_points.items():
        v = bm.verts.new(co)
        rx, ry = _radii(sp, pname, head_scale=head_scale)
        v[layer].radius = (rx, ry)
        if pname == "pelvis":
            # ⚠ 实测（Blender 5.2）：不标 use_root 的骨架，Skin 修改器会生成**零宽**几何（尺寸全 0）
            try:
                v[layer].use_root = True
            except Exception:
                pass
        vmap[pname] = v
    bm.verts.index_update()
    for a, b in chain:
        try:
            bm.edges.new((vmap[a], vmap[b]))
        except Exception:
            pass
    bm.to_mesh(me)
    bm.free()
    ob = bpy.data.objects.new(nm, me)
    bpy.context.scene.collection.objects.link(ob)
    sk = ob.modifiers.new("skin", "SKIN")
    try:
        sk.use_smooth_shade = bool(smooth)
    except Exception:
        pass
    if int(subsurf or 0) > 0:
        ss = ob.modifiers.new("subsurf", "SUBSURF")
        ss.levels = int(subsurf)
        ss.render_levels = int(subsurf)
    for o in bpy.context.selected_objects:
        o.select_set(False)
    ob.select_set(True)
    bpy.context.view_layer.objects.active = ob
    applied = []
    for m in list(ob.modifiers):
        _mn = str(m.name)
        bpy.ops.object.modifier_apply(modifier=_mn)
        applied.append(_mn)
    return ob, applied


def _fit_height(ob, target_m):
    """按目标身高校正（Subsurf 会缩）：返回缩放系数 k 与校正后高度。"""
    h_raw = float(ob.dimensions.z) or 1e-9
    k = target_m / h_raw
    ob.scale = (k, k, k)
    for o in bpy.context.selected_objects:
        o.select_set(False)
    ob.select_set(True)
    bpy.context.view_layer.objects.active = ob
    bpy.ops.object.transform_apply(location=False, rotation=False, scale=True)
    return k, float(ob.dimensions.z)


def _head_geo(ob, sp, k):
    """几何量头：冠顶 Z 与头中心 Z 之差 = 头半径 ⇒ 头高、头身比。"""
    zs = [float(v.co.z) for v in ob.data.vertices]
    if not zs:
        return None, None
    r = abs(max(zs) - sp["points"]["head"][2] * k)
    if r <= 1e-9:
        return None, None
    return (abs(float(ob.dimensions.z)) / (2.0 * r), 2.0 * r * 1000.0)


def human_base(spec=None, height_mm=None, heads=None, shoulder_w_mm=None, pose="A", limb_r_mm=None,
               name="HumanBase", markers=True, subsurf=1, smooth=True, converge=True):
    """建素体：骨架 → Skin → Subsurf → 按目标身高校正 →（可选）按几何头高收敛一次。"""
    try:
        if isinstance(spec, dict) and spec.get("points"):
            sp = dict(spec)
        else:
            sp = build_spec(height_mm=height_mm, heads=heads, shoulder_w_mm=shoulder_w_mm, pose=pose,
                            limb_r_mm=limb_r_mm)
        nm = str(name or "HumanBase")
        target = sp["height_mm"] / 1000.0
        head_scale = 1.0
        iters = 0
        heads_geo, head_mm_geo, k, h_after, ob, applied = None, None, 1.0, 0.0, None, []
        while True:
            iters += 1
            ob, applied = _build_once(sp, nm, head_scale=head_scale, subsurf=subsurf, smooth=smooth)
            k, h_after = _fit_height(ob, target)
            heads_geo, head_mm_geo = _head_geo(ob, sp, k)
            want = float(sp["heads"])
            if not converge or heads_geo is None or iters >= 3:
                break
            if abs(heads_geo - want) / want <= 0.03:
                break
            # heads_geo = 身高/(2r)：实测偏大 ⇒ 头偏小 ⇒ 要放大 ⇒ 乘 (实测/目标)
            head_scale *= (heads_geo / want)
        mmu = float(bpy.context.scene.unit_settings.scale_length or 1.0) * 1000.0
        mk = []
        if markers:
            coll = bpy.data.collections.get(nm + "_joints")
            if coll is None:
                coll = bpy.data.collections.new(nm + "_joints")
                bpy.context.scene.collection.children.link(coll)
            for o in list(coll.objects):
                bpy.data.objects.remove(o, do_unlink=True)
            for pname, co in sp["points"].items():
                e = bpy.data.objects.new(nm + "_J_" + pname, None)
                e.empty_display_type = "SPHERE"
                e.empty_display_size = 0.03
                e.location = (co[0] * k, co[1] * k, co[2] * k)
                coll.objects.link(e)
                mk.append(e.name)
            try:
                bpy.context.view_layer.update()
            except Exception:
                pass
        try:
            ob["dsh_height_mm"] = round(h_after * mmu, 3)
            ob["dsh_head_mm"] = round(head_mm_geo or (sp["head_mm"] * k), 3)
            ob["dsh_shoulder_mm"] = round(sp["shoulder_w_mm"] * k, 3)
            ob["dsh_heads"] = round((head_mm_geo and (h_after * mmu / head_mm_geo)) or sp["heads"], 4)
            ob["dsh_kind"] = "human_base"
        except Exception:
            pass
        err = abs(h_after - target) / target
        return _j({"ok": err < 0.005, "object": ob.name, "height_mm": round(h_after * mmu, 2),
                   "target_height_mm": sp["height_mm"], "height_rel_err": round(err, 6),
                   "heads_target": sp["heads"], "heads_measured": (round(heads_geo, 3) if heads_geo else None),
                   "head_mm": (round(head_mm_geo, 2) if head_mm_geo else None),
                   "shoulder_w_mm": round(sp["shoulder_w_mm"] * k, 2),
                   "shoulder_over_head": (round((sp["shoulder_w_mm"] * k) / head_mm_geo, 3) if head_mm_geo else None),
                   "scale_correction": round(k, 5), "convergence": {"iters": iters, "head_scale": round(head_scale, 4)},
                   "vertices": len(ob.data.vertices), "polygons": len(ob.data.polygons), "applied": applied,
                   "marker_count": len(mk), "markers": mk[:6],
                   "spec": {k2: sp[k2] for k2 in ("height_mm", "heads", "head_mm", "shoulder_w_mm", "arm_span_mm", "pose")},
                   "note": "素体=比例正确的假人：当装配基准/挂装甲/摆姿势够用；写实解剖不在这条路上"})
    except Exception as e:
        return _j({"ok": False, "error": "%s: %s" % (type(e).__name__, str(e)[:220])})


def human_measure(objects=None, scope="ACTIVE", name=None):
    """量已有素体：总高 / 头高 / 肩宽 / 头身比（优先读建模时写进对象的属性）。"""
    objs = []
    if name:
        o = bpy.data.objects.get(str(name))
        if o is None:
            return _j({"ok": False, "error": "对象不存在：%s" % name})
        objs = [o]
    elif objects:
        names = [objects] if isinstance(objects, str) else list(objects)
        objs = [bpy.data.objects.get(str(n)) for n in names]
        objs = [o for o in objs if o is not None]
    else:
        o = bpy.context.view_layer.objects.active
        objs = [o] if o is not None else []
    if not objs:
        return _j({"ok": False, "error": "没有对象（给 objects/name）"})
    mmu = float(bpy.context.scene.unit_settings.scale_length or 1.0) * 1000.0
    try:
        bpy.context.view_layer.update()
    except Exception:
        pass
    ob = objs[0]
    h_mm = float(ob.dimensions.z) * mmu
    head_mm = float(ob["dsh_head_mm"]) if "dsh_head_mm" in ob.keys() else None
    joints = [o for o in bpy.data.objects if o.type == "EMPTY" and o.name.startswith(ob.name + "_J_")]
    sw = None
    sh = [o for o in joints if "_J_shoulder_" in o.name]
    if len(sh) == 2:
        sw = abs(float(sh[0].location.x) - float(sh[1].location.x)) * mmu
    if head_mm is None and joints:
        zs = sorted(float(o.location.z) for o in joints)
        if len(zs) >= 2:
            head_mm = abs(max(zs) - zs[0]) * mmu
    if sw is None:
        sw = float(ob.dimensions.x) * mmu
    heads = (h_mm / head_mm) if head_mm else None
    return _j({"ok": True, "object": ob.name, "height_mm": round(h_mm, 2),
               "head_mm": (round(head_mm, 2) if head_mm else None), "shoulder_w_mm": round(sw, 2),
               "heads": (round(heads, 3) if heads else None),
               "shoulder_over_head": (round(sw / head_mm, 3) if head_mm else None),
               "joints_found": len(joints), "source": ("props" if "dsh_head_mm" in ob.keys() else "geometry"),
               "note": "head_mm 优先读建模时写入的属性；没有就退回关节标记几何"})


def human_spec(image, samples=9, invert=False, threshold=None):
    """从参考图轮廓反推像素比（零下载）：总高/头宽/肩宽/腰宽 + 各高度切片宽度。"""
    img = _api("img")
    if img is None:
        return _j({"ok": False, "error": "需要 imgtools（preload 里加 imgtools）"})
    args = {"path": image, "invert": bool(invert)}
    if threshold is not None:
        args["threshold"] = threshold
    s = img("scan", args)
    if not s.get("ok") or not s.get("bbox"):
        return _j({"ok": False, "error": "轮廓没分出来（照片请先抠图，或给 threshold）", "scan": s})
    x, y, w, h = s["bbox"]
    rows = s.get("row_profile") or []
    prof = []
    n = max(1, int(samples))
    for i in range(n):
        frac = (i / float(n - 1)) if n > 1 else 0.0
        if rows:
            idx = min(len(rows) - 1, int(frac * (len(rows) - 1)))
            prof.append({"frac": round(frac, 3), "width_px": rows[idx]})
    head_w = next((p["width_px"] for p in prof if p["frac"] >= 0.08), None)
    shoulder = max([p["width_px"] for p in prof if 0.12 <= p["frac"] <= 0.30] or [0]) or None
    waist = min([p["width_px"] for p in prof if 0.30 <= p["frac"] <= 0.55] or [0]) or None
    return _j({"ok": True, "image": s.get("path"), "size": s.get("size"), "bbox": s.get("bbox"),
               "height_px": h, "head_w_px": head_w, "shoulder_px": shoulder, "waist_px": waist,
               "profile": prof, "row_profile_available": bool(rows),
               "ratios": {"shoulder_over_head_w": (round(shoulder / head_w, 3) if (shoulder and head_w) else None),
                          "waist_over_shoulder": (round(waist / shoulder, 3) if (waist and shoulder) else None),
                          "height_over_shoulder": (round(h / shoulder, 3) if shoulder else None)},
               "note": "这些是像素比：对到 spec 的 heads/shoulder_w_mm；毫米要自己钉（或给已知长度标定）"})


def head_base(landmarks=None, head_mm=None, width_ratio=0.75, depth_ratio=0.90,
              name="HeadBase", markers=True, subsurf=1):
    """参数化头型：颅椭球 + 下颌楔 + 五官定位标记（眼/鼻底/嘴/下巴/颅顶）。

    landmarks（像素坐标，可只给一部分）用来**推导比例**，不直接拟合 —— 零素材、零检测器：
      top/chin 定头高；face_l/face_r 定头宽；eye_l/eye_r 定眼线；nose_base 定鼻底；mouth_l/mouth_r 定嘴线。
    头高若给了 head_mm 就用它（毫米），否则按 landmarks 的像素高当毫米用（1px=1mm）。
    """
    import mathutils
    lm = dict(landmarks or {})
    hd_mm = head_mm
    frac = {}
    try:
        if "top" in lm and "chin" in lm:
            px_h = abs(float(lm["chin"][1]) - float(lm["top"][1]))
            if not hd_mm:
                hd_mm = px_h
            if px_h > 0:
                for k2, key in (("eye", ("eye_l", "eye_r")), ("nose", ("nose_base",)),
                                ("mouth", ("mouth_l", "mouth_r"))):
                    if key[0] in lm:
                        y = (sum(float(lm[q][1]) for q in key) / len(key)) - float(lm["top"][1])
                        frac[k2 + "_line_frac"] = round(y / px_h, 4)
        if "face_l" in lm and "face_r" in lm:
            width_ratio = abs(float(lm["face_r"][0]) - float(lm["face_l"][0])) / max(1e-9, abs(float(lm["chin"][1]) - float(lm["top"][1]))) if ("chin" in lm and "top" in lm) else width_ratio
    except Exception:
        pass
    hd_mm = float(hd_mm or 233.0)
    hd = hd_mm / 1000.0
    hw, hde = hd * float(width_ratio), hd * float(depth_ratio)
    nm = str(name or "HeadBase")
    _drop(nm)
    me = bpy.data.meshes.new(nm)
    bm = bmesh.new()
    # 颅：椭球（中心在头中偏上）
    mtx = mathutils.Matrix.Translation((0.0, 0.0, 0.58 * hd)) @ mathutils.Matrix.Diagonal((hw / 2.0, hde / 2.0, hd / 2.0, 1.0))
    bmesh.ops.create_uvsphere(bm, u_segments=24, v_segments=16, radius=1.0, matrix=mtx)
    # 下颌楔：一个收窄的盒（下缘到下巴），下窄上宽
    jaw_h = 0.46 * hd
    jaw_w_top, jaw_w_bot = 0.42 * hw, 0.26 * hw
    verts = {}
    for sx, xr in ((-1, jaw_w_top / 2.0), (1, jaw_w_top / 2.0), (-1, jaw_w_bot / 2.0), (1, jaw_w_bot / 2.0)):
        pass
    top_z, bot_z = 0.46 * hd, 0.02 * hd
    pts = {}
    for sx in (-1, 1):
        for sy in (-1, 1):
            pts[("t", sx, sy)] = (sx * jaw_w_top / 2.0, sy * hde * 0.42, top_z)
            pts[("b", sx, sy)] = (sx * jaw_w_bot / 2.0, sy * hde * 0.34, bot_z)
    vs = {k2: bm.verts.new(co) for k2, co in pts.items()}
    faces = [(("t", -1, -1), ("t", 1, -1), ("t", 1, 1), ("t", -1, 1)),
             (("b", -1, -1), ("b", -1, 1), ("b", 1, 1), ("b", 1, -1)),
             (("t", -1, -1), ("b", -1, -1), ("b", 1, -1), ("t", 1, -1)),
             (("t", -1, 1), ("t", 1, 1), ("b", 1, 1), ("b", -1, 1)),
             (("t", -1, -1), ("t", -1, 1), ("b", -1, 1), ("b", -1, -1)),
             (("t", 1, -1), ("b", 1, -1), ("b", 1, 1), ("t", 1, 1))]
    for f in faces:
        try:
            bm.faces.new([vs[q] for q in f])
        except Exception:
            pass
    bm.to_mesh(me)
    bm.free()
    ob = bpy.data.objects.new(nm, me)
    bpy.context.scene.collection.objects.link(ob)
    if int(subsurf or 0) > 0:
        ss = ob.modifiers.new("subsurf", "SUBSURF")
        ss.levels = int(subsurf)
        ss.render_levels = int(subsurf)
        for o in bpy.context.selected_objects:
            o.select_set(False)
        ob.select_set(True)
        bpy.context.view_layer.objects.active = ob
        try:
            bpy.ops.object.modifier_apply(modifier="subsurf")
        except Exception:
            pass
    mk = []
    if markers:
        coll = bpy.data.collections.get(nm + "_marks")
        if coll is None:
            coll = bpy.data.collections.new(nm + "_marks")
            bpy.context.scene.collection.children.link(coll)
        for o in list(coll.objects):
            bpy.data.objects.remove(o, do_unlink=True)
        anchors = {"crown": (0.0, 0.0, hd), "chin": (0.0, 0.0, 0.0),
                   "eye_l": (-hw * 0.23, -hde * 0.34, (frac.get("eye_line_frac") or 0.5) * hd),
                   "eye_r": (hw * 0.23, -hde * 0.34, (frac.get("eye_line_frac") or 0.5) * hd),
                   "nose_base": (0.0, -hde * 0.48, (frac.get("nose_line_frac") or 0.72) * hd),
                   "mouth": (0.0, -hde * 0.42, (frac.get("mouth_line_frac") or 0.82) * hd)}
        for an, co in anchors.items():
            e = bpy.data.objects.new(nm + "_M_" + an, None)
            e.empty_display_type = "PLAIN_AXES"
            e.empty_display_size = hd * 0.08
            e.location = co
            coll.objects.link(e)
            mk.append(e.name)
        try:
            bpy.context.view_layer.update()
        except Exception:
            pass
    d = [round(float(x) * 1000.0, 2) for x in ob.dimensions]
    return _j({"ok": True, "object": ob.name, "head_mm": round(hd_mm, 2),
               "size_mm": {"w": d[0], "d": d[1], "h": d[2]}, "landmark_fracs": frac,
               "width_ratio": round(hw / hd, 4), "depth_ratio": round(hde / hd, 4),
               "markers": mk, "vertices": len(ob.data.vertices),
               "note": "五官定位靠标记点（眼/鼻底/嘴/下巴/颅顶）：把眼睛鼻子嘴的零件挂到这些点上；写实谈不上，配面罩/头盔正好"})


def human_selftest():
    """自检：1.75m/7.5 头身 → 断言高度误差 <0.5%、几何头身比接近目标、关节标记齐全。"""
    ev = {}
    try:
        r = json.loads(human_base(height_mm=1750.0, heads=7.5, name="__dsh_human_selftest"))
        ev["base"] = {k: r.get(k) for k in ("ok", "height_mm", "height_rel_err", "heads_target",
                                            "heads_measured", "head_mm", "shoulder_over_head",
                                            "vertices", "polygons", "marker_count", "convergence")}
        m = json.loads(human_measure(name="__dsh_human_selftest"))
        ev["measure"] = {k: m.get(k) for k in ("height_mm", "head_mm", "shoulder_w_mm", "heads", "source")}
        hre = r.get("height_rel_err")
        hm = r.get("heads_measured")
        ht = float(r.get("heads_target") or 7.5)
        ok = (r.get("ok") is True and hre is not None and hre < 0.005
              and hm is not None and abs(hm - ht) / ht <= 0.05
              and int(r.get("marker_count") or 0) >= 12 and int(r.get("vertices") or 0) > 200
              and bool(m.get("head_mm")))
        return _j({"ok": bool(ok), "evidence": ev})
    except Exception as e:
        return _j({"ok": False, "evidence": ev, "error": "%s: %s" % (type(e).__name__, str(e)[:220])})
    finally:
        for o in [x for x in bpy.data.objects if x.name.startswith("__dsh_human_selftest")]:
            _drop(o.name)
        c = bpy.data.collections.get("__dsh_human_selftest_joints")
        if c is not None:
            bpy.data.collections.remove(c)


def human_help():
    return _j({
        "module": "human.py", "version": HUMAN_VERSION,
        "what": "零素材人形：填数字（身高/头身比/肩宽/姿势）→ Skin+Subsurf 出比例正确的素体",
        "ops": ["human_base", "human_measure", "human_spec", "head_base", "human_selftest", "human_help"],
        "canon": LM, "default_heads": DEFAULT_HEADS,
        "protocol": ["先 human_spec 从参考图拿比例 → 写进 spec.py（数字冻结）",
                     "再 human_base 建素体（markers=true 出关节空物体，供 motion_joints 与装甲挂载）",
                     "验收看 human_measure 的 heads / shoulder_over_head 与 spec 的差",
                     "脸/手这类高细节部位用面罩/手套回避 —— 把硬表面顶上去"],
        "limits": ["素体=假人级（比例对、形体简）",
                   "Subsurf 会缩：已按身高自动校正，并按几何头高收敛一次（回执给 convergence）",
                   "human_spec 只给像素比，毫米要自己钉"],
    })


def human_dispatch(op, args_json):
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
    ops = {"base": human_base, "measure": human_measure, "spec": human_spec, "head": head_base,
           "selftest": human_selftest, "help": human_help}
    fn = ops.get(str(op))
    if fn is None:
        return _j({"ok": False, "error": "unknown human op", "op": op, "ops": sorted(ops)})
    try:
        return fn(**kw)
    except Exception as e:
        return _j({"ok": False, "error": "%s: %s" % (type(e).__name__, str(e)[:220]), "op": op})


_DshApi = _KIT.Api  # 共享内核（尾部注册行无需改）
import sys as _sys
_K = _sys.modules.get("dsh_rt_kernel")
if _K is not None:
    _K.dsh_human_api = _DshApi({"version": HUMAN_VERSION, "dispatch": human_dispatch, "base": human_base, "head": head_base,
                                "measure": human_measure, "spec": human_spec, "selftest": human_selftest,
                                "help": human_help})