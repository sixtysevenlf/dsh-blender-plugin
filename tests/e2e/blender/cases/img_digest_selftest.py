# -*- coding: utf-8 -*-
"""tests/img_digest_selftest.py —— imgtools v2（img_digest / img_probe / img_measure / img_calib）自检。

跑法（真机 headless）：
    blender_rt_headless(preload="imgtools", engine="none", timeout_ms=300000,
                        script_file="/home/<user>/DSH/dsh-blender-plugin/tests/img_digest_selftest.py")

自带合成图（bpy 写 RGBA PNG，alpha 就是真值）：矩形 / 圆孔+微小孔 / 45° 斜杆 / 镜像体。
断言：bbox ±1px、aspect ±0.5%、px_per_mm ±0.5%、圆直径 ±2%、无标定 verdict=needs_scale、
<8px 特征进 unreadable；probe 亚像素宽度、measure delta、calib 多视一致性、坏参数 ok:false。
结果契约：print("HEADLESS " + json)；失败 exit 1。
"""
import json
import math
import os
import sys
import tempfile
import time

import numpy as np
import bpy

K = sys.modules.get("dsh_rt_kernel")
API = getattr(K, "dsh_img_api", None) if K is not None else None
if API is None:
    print("HEADLESS " + json.dumps({"ok": False, "error": "K.dsh_img_api 不存在：跑 headless 时 preload=\"imgtools\""}))
    sys.exit(1)

EV = {}
FAIL = []


def check(name, cond, got=None):
    ok = bool(cond)
    EV[name] = {"ok": ok, "got": got}
    if not ok:
        FAIL.append(name)
    return ok


def call(_op, **kw):
    return json.loads(API["dispatch"](_op, json.dumps(kw)))


TMP = tempfile.mkdtemp(prefix="dsh_img_digest_")


def save_png(arr, name):
    H, W = int(arr.shape[0]), int(arr.shape[1])
    p = os.path.join(TMP, name)
    img = bpy.data.images.new(name="selftest_" + str(abs(hash(name)) % 100000) + name, width=W, height=H, alpha=True)
    try:
        img.pixels.foreach_set(np.ascontiguousarray(arr[::-1].reshape(-1)).astype(np.float32))
        img.filepath_raw = p
        img.file_format = "PNG"
        img.save()
    finally:
        try:
            bpy.data.images.remove(img)
        except Exception:
            pass
    return p


def blank(W=640, H=480):
    return np.zeros((H, W, 4), np.float32)


def paint(a, m, color=0.6, alpha=1.0):
    a[m, 0:3] = color
    a[m, 3] = alpha


# ── 合成图 ──────────────────────────────────────────────────────────────
W, H = 640, 480
yy, xx = np.mgrid[0:H, 0:W]

rect = blank()
rect_m = (xx >= 100) & (xx < 420) & (yy >= 80) & (yy < 320)      # bbox = [100,80,320,240]
paint(rect, rect_m)

part = rect.copy()
big_hole = ((xx - 260.0) ** 2 + (yy - 200.0) ** 2) < (40.0 ** 2)  # 直径 ≈ 79~80 px
small_hole = ((xx - 140.0) ** 2 + (yy - 120.0) ** 2) < (2.5 ** 2)  # 直径 ≈ 4~5 px
for hm in (big_hole, small_hole):
    part[hm, 0:3] = 0.0
    part[hm, 3] = 0.0

diag = blank()
x1, y1, x2, y2 = 100.0, 100.0, 400.0, 400.0
L = math.hypot(x2 - x1, y2 - y1)
ux, uy = (x2 - x1) / L, (y2 - y1) / L
dx = xx - x1
dy = yy - y1
t = dx * ux + dy * uy
perp = np.abs(-dx * uy + dy * ux)
paint(diag, (t >= 0) & (t <= L) & (perp <= 5.0))

MW, MH = 800, 480
myy, mxx = np.mgrid[0:MH, 0:MW]
mirror = blank(MW, MH)
mrect = (mxx >= 250) & (mxx < 550) & (myy >= 200) & (myy < 421)
mtri = (myy >= 110) & (myy < 200) & (np.abs(mxx - 399.5) <= (200 - myy) * (150.0 / 90.0))
paint(mirror, mrect | mtri)

p_rect = save_png(rect, "rect.png")
p_part = save_png(part, "part.png")
p_diag = save_png(diag, "diag.png")
p_mirror = save_png(mirror, "mirror.png")

DIG = os.path.join(TMP, "digests")
os.makedirs(DIG, exist_ok=True)

# ── 1) 无标定 digest：bbox / aspect / verdict=needs_scale ───────────────
d0 = call("digest", path=p_rect, out_dir=os.path.join(DIG, "rect_nocal"))
check("digest0_ok", d0.get("ok") is True, d0.get("error"))
check("digest0_native_px", (d0.get("image") or {}).get("native_px") == [640, 480],
      (d0.get("image") or {}).get("native_px"))
check("digest0_md5_32", len(str((d0.get("image") or {}).get("md5") or "")) == 32,
      len(str((d0.get("image") or {}).get("md5") or "")))
sil0 = d0.get("silhouette") or {}
bb0 = sil0.get("bbox_px") or []
check("digest0_bbox_pm1", len(bb0) == 4 and all(abs(bb0[i] - [100, 80, 320, 240][i]) <= 1 for i in range(4)), bb0)
asp0 = float(sil0.get("aspect_wh") or 0)
check("digest0_aspect_pm05pct", abs(asp0 - 320.0 / 240.0) / (320.0 / 240.0) < 0.005, asp0)
check("digest0_needs_scale", d0.get("verdict") == "needs_scale", d0.get("verdict"))
mm_bad = [n for n in (d0.get("numbers") or []) if n.get("unit") == "mm" and n.get("value") is not None]
check("digest0_no_mm_without_calib", len(mm_bad) == 0, mm_bad[:2])
check("digest0_crops_written", any((c or {}).get("path") for c in (d0.get("crops") or [])),
      [(c or {}).get("path") for c in (d0.get("crops") or [])][:2])
check("digest0_overlay_written", bool(((d0.get("overlay") or {}).get("path"))),
      (d0.get("overlay") or {}).get("path"))

# ── 2) 有标定 digest：px_per_mm ±0.5%、verdict ready_for_spec ───────────
d1 = call("digest", path=p_rect,
          known_len={"mm": 160, "p1_px": [100, 80], "p2_px": [420, 80]},
          views=["front", "side"], out_dir=os.path.join(DIG, "rect_scaled"))
sc1 = d1.get("scale") or {}
ppm1 = sc1.get("px_per_mm")
check("digest1_ppm", ppm1 is not None and abs(float(ppm1) - 2.0) / 2.0 < 0.005, ppm1)
check("digest1_verdict", d1.get("verdict") == "ready_for_spec", d1.get("verdict"))
wmm = None
for n in (d1.get("numbers") or []):
    if n.get("key") == "width_mm":
        wmm = n.get("value")
check("digest1_width_mm", wmm is not None and abs(float(wmm) - 160.0) / 160.0 < 0.005, wmm)
check("digest1_scale_provenance", (sc1.get("source") == "known_len" and sc1.get("err_mm_at_300px") is not None),
      {"source": sc1.get("source"), "err": sc1.get("err_mm_at_300px")})

# ── 3) 圆孔直径 ±2% + 微小特征进 unreadable ──────────────────────────────
d2 = call("digest", path=p_part, out_dir=os.path.join(DIG, "part"))
feats2 = d2.get("features") or []
circles = [f for f in feats2 if f.get("kind") == "circle"]
big = max(circles, key=lambda f: float((f.get("geom") or {}).get("diameter_px") or 0.0)) if circles else {}
dpx = float((big.get("geom") or {}).get("diameter_px") or 0.0)
check("digest2_circle_found", len(circles) >= 1, [float((f.get("geom") or {}).get("diameter_px") or 0) for f in circles])
check("digest2_circle_diam_pm2pct", abs(dpx - 80.0) / 80.0 < 0.02, dpx)
check("digest2_circle_resid", float((big.get("geom") or {}).get("residual_px") or 99.0) < 2.0,
      (big.get("geom") or {}).get("residual_px"))
check("digest2_holes_2", int(((d2.get("mask") or {}).get("self_check") or {}).get("holes") or 0) == 2,
      ((d2.get("mask") or {}).get("self_check") or {}).get("holes"))
readability = d2.get("readability") or []
tiny = [r for r in readability if r.get("readable") is False and float(r.get("size_px") or 99) < 8.0]
check("digest2_tiny_unreadable", len(tiny) >= 1, tiny[:2])
tiny_feat = [f for f in feats2 if f.get("readable") is False and float(f.get("size_px") or 99) < 8.0]
check("digest2_tiny_feature_unreadable", len(tiny_feat) >= 1,
      [(f.get("key"), f.get("size_px")) for f in tiny_feat])
check("digest2_unreadable_list", len(d2.get("unreadable") or []) >= 1,
      (d2.get("unreadable") or [])[:2])

# ── 4) 对称性（镜像体）───────────────────────────────────────────────────
d3 = call("digest", path=p_mirror, out_dir=os.path.join(DIG, "mirror"))
sym = d3.get("symmetry") or {}
check("digest3_axis_vertical", sym.get("axis") == "vertical", sym.get("axis"))
check("digest3_score_high", float(sym.get("score") or 0.0) > 0.9, sym.get("score"))
me = sym.get("mirror_err_px")
check("digest3_mirror_err", me is not None and float(me) <= 2.0, me)

# ── 5) 45° 直线 + 主轴角 ────────────────────────────────────────────────
d4 = call("digest", path=p_diag, out_dir=os.path.join(DIG, "diag"))
lines = d4.get("lines") or []
dirs = [float(ln.get("direction_deg") or 0.0) for ln in lines]
long_dirs = [float(ln.get("direction_deg") or 0.0) for ln in lines if float(ln.get("span_px") or 0.0) > 200.0]
near45 = [v for v in long_dirs if abs(((v - 45.0 + 90.0) % 180.0) - 90.0) <= 2.0]
check("digest4_lines_found", len(lines) >= 1, len(lines))
check("digest4_45deg_line", len(near45) >= 1, {"directions": dirs, "long": long_dirs})
pdeg = (d4.get("silhouette") or {}).get("principal_axis_deg")
check("digest4_principal_45", pdeg is not None and abs(float(pdeg) - 45.0) <= 3.0, pdeg)

# ── 6) img_probe：亚像素宽度 + 热路径耗时 ───────────────────────────────
pr = call("probe", path=p_rect, op="scan_y", y_px=200)
check("probe_scan_y_ok", pr.get("ok") is True and pr.get("op") == "scan_y", pr.get("error"))
check("probe_width_pm1", abs(float(pr.get("width_px") or 0.0) - 320.0) <= 1.0,
      {"left": pr.get("left_px"), "right": pr.get("right_px"), "width": pr.get("width_px")})
check("probe_one_segment", int(pr.get("n_segments") or 0) == 1, pr.get("segments"))
pr_part = call("probe", path=p_part, op="scan_y", y_px=200)
check("probe_segments_with_hole", int(pr_part.get("n_segments") or 0) == 2,
      [(s.get("x0_px"), s.get("x1_px")) for s in (pr_part.get("segments") or [])])
pc = call("probe", path=p_part, op="circle", x_px=260, y_px=200)
check("probe_circle_found", pc.get("found") is True and (pc.get("circle") or {}).get("is_circle") is True,
      (pc.get("circle") or {}).get("diameter_px"))
t0 = time.perf_counter()
for _ in range(30):
    call("probe", path=p_rect, op="scan_y", y_px=200)
avg_ms = (time.perf_counter() - t0) / 30.0 * 1000.0
check("probe_hot_avg_under_50ms", avg_ms < 50.0, round(avg_ms, 3))

# ── 7) img_measure：mm + delta + pass/fail ──────────────────────────────
image_id = (d1.get("image") or {}).get("image_id")
m1 = call("measure", image_id=image_id,
          targets=[{"key": "width", "p1_px": [100, 200], "p2_px": [420, 200], "expect_mm": 160.0, "tol_pct": 1.0},
                   {"key": "too_tall", "p1_px": [100, 80], "p2_px": [100, 320], "expect_mm": 100.0, "tol_pct": 1.0}])
row_w = next((r for r in (m1.get("rows") or []) if r.get("key") == "width"), {})
row_bad = next((r for r in (m1.get("rows") or []) if r.get("key") == "too_tall"), {})
check("measure_rows", len(m1.get("rows") or []) == 2, m1.get("rows"))
check("measure_width_mm", abs(float(row_w.get("value_mm") or 0.0) - 160.0) < 0.8, row_w.get("value_mm"))
check("measure_pass_row", row_w.get("verdict") == "pass", row_w.get("verdict"))
check("measure_fail_row", row_bad.get("verdict") == "fail",
      {"verdict": row_bad.get("verdict"), "delta_mm": row_bad.get("delta_mm")})
check("measure_overall_fail", m1.get("ok") is False and m1.get("verdict") == "fail",
      {"ok": m1.get("ok"), "verdict": m1.get("verdict")})
m2 = call("measure", px_per_mm=2.0, targets=[{"key": "given", "px": 100.0, "expect_mm": 50.0}])
check("measure_given_px_ok", m2.get("ok") is True and m2.get("verdict") == "pass", m2.get("verdict"))

# ── 8) img_calib：多视一致性 / assert_same_scale ────────────────────────
cal1 = call("calib",
            views=[{"id": "front", "known_len": [{"mm": 160, "px": 320}]},
                   {"id": "side", "known_len": [{"mm": 100, "px": 200}]}],
            shared=[{"key": "height", "mm": 240,
                     "views": {"front": {"px": 480}, "side": {"px": 480}}}],
            assert_same_scale=True)
pv = {str(v.get("id")): v for v in (cal1.get("per_view") or [])}
cons = cal1.get("consistency") or {}
check("calib_ok", cal1.get("ok") is True and cal1.get("verdict") == "ok",
      {"verdict": cal1.get("verdict"), "error": cal1.get("error")})
check("calib_ppm_ls", abs(float((pv.get("front") or {}).get("px_per_mm") or 0.0) - 2.0) < 1e-6
      and abs(float((pv.get("side") or {}).get("px_per_mm") or 0.0) - 2.0) < 1e-6,
      [pv.get("front", {}).get("px_per_mm"), pv.get("side", {}).get("px_per_mm")])
check("calib_same_scale", cons.get("same_scale") is True, cons.get("same_scale"))
check("calib_shared_zero_delta", float(cons.get("mm_max_delta") or 0.0) < 1e-6, cons.get("mm_max_delta"))
cal2 = call("calib",
            views=[{"id": "front", "known_len": [{"mm": 160, "px": 320}]},
                   {"id": "side", "known_len": [{"mm": 100, "px": 250}]}],
            shared=[{"key": "height", "views": {"front": {"px": 480}, "side": {"px": 480}}}],
            assert_same_scale=True)
check("calib_inconsistent", cal2.get("verdict") in ("inconsistent", "fail") and cal2.get("ok") is False,
      {"verdict": cal2.get("verdict"), "delta": (cal2.get("consistency") or {}).get("mm_max_delta")})
cal3 = call("calib", views=[{"id": "only"}])
check("calib_needs_data", cal3.get("verdict") == "needs_data", cal3.get("verdict"))

# ── 9) 防御：空输入/坏参数必须 ok:false ─────────────────────────────────
bad = {}
bad["digest_empty_path"] = call("digest", path="")
bad["digest_missing_file"] = call("digest", path="Z:/dsh_no_such_file_xyz.png")
bad["digest_bad_known"] = call("digest", path=p_rect, known_len={"foo": 1})
bad["probe_no_args"] = call("probe")
bad["probe_unknown_op"] = call("probe", path=p_rect, op="nope")
bad["probe_bad_roi"] = call("probe", path=p_rect, roi=[0, 0, 0, 0])
bad["measure_no_targets"] = call("measure", image_id=image_id)
# 用未标定过的图（p_part 的 digest 没有 known_len），避免命中 p_rect 的标定缓存
bad["measure_no_scale"] = call("measure", path=p_part, targets=[{"key": "x", "px": 10}])
bad["calib_empty"] = call("calib", views=[])
for k, v in bad.items():
    check("bad_" + k, v.get("ok") is False, {"ok": v.get("ok"), "error": v.get("error"), "verdict": v.get("verdict")})

# ── 10) 其余 v2 面：scan_x / distance / max_px 降采样 / 旧 op 回归 / help ──
prx = call("probe", path=p_rect, op="scan_x", x_px=200)
check("probe_scan_x", prx.get("ok") is True and abs(float(prx.get("height_px") or 0.0) - 240.0) <= 1.0,
      {"top": prx.get("top_px"), "bottom": prx.get("bottom_px"), "height": prx.get("height_px")})
prd = call("probe", path=p_rect, op="distance", p1_px=[100, 200], p2_px=[420, 200])
check("probe_distance", abs(float(prd.get("distance_px") or 0.0) - 320.0) < 1e-6
      and abs(float(prd.get("distance_mm") or 0.0) - 160.0) < 1e-6,
      {"px": prd.get("distance_px"), "mm": prd.get("distance_mm")})
dd = call("digest", path=p_rect, max_px=64000, out_dir=os.path.join(DIG, "rect_small"))
sc = math.sqrt(64000.0 / (640.0 * 480.0))
exp_w = 320.0 * sc
bbd = (dd.get("silhouette") or {}).get("bbox_px") or []
check("digest_maxpx_downscale",
      dd.get("ok") is True and (dd.get("image") or {}).get("downscaled") is True
      and (dd.get("image") or {}).get("loaded_px") != [640, 480]
      and len(bbd) == 4 and abs(float(bbd[2]) - exp_w) <= 2.0,
      {"loaded": (dd.get("image") or {}).get("loaded_px"), "bbox": bbd, "expected_w": round(exp_w, 2)})
legacy = call("selftest")
check("legacy_img_selftest", legacy.get("ok") is True, legacy.get("error"))
hp = call("help")
check("help_version_2", hp.get("version") == 2 and all(k in (hp.get("ops") or {})
      for k in ("img_digest", "img_probe", "img_measure", "img_calib")),
      {"version": hp.get("version"), "ops": len(hp.get("ops") or {})})

# ── 11) 无 alpha 照片式输入走 border_otsu 掩膜；未知参数被拦截 ─────────
phot = np.ones((360, 480, 4), np.float32)
pyy, pxx = np.mgrid[0:360, 0:480]
pobj = (pxx >= 80) & (pxx < 320) & (pyy >= 60) & (pyy < 300)
phot[pobj, 0:3] = 0.12
p_photo = save_png(phot, "photo.png")
dphoto = call("digest", path=p_photo, out_dir=os.path.join(DIG, "photo"))
check("mask_otsu_source", (dphoto.get("mask") or {}).get("source") == "border_otsu",
      (dphoto.get("mask") or {}).get("source"))
bbo = (dphoto.get("silhouette") or {}).get("bbox_px") or []
check("mask_otsu_bbox_pm1", len(bbo) == 4 and all(abs(bbo[i] - [80, 60, 240, 240][i]) <= 1 for i in range(4)), bbo)
badp = call("digest", path=p_rect, no_such_param=1)
check("bad_unknown_param", badp.get("ok") is False and "不认识的参数" in str(badp.get("error")),
      badp.get("error"))

# ── 汇总 ────────────────────────────────────────────────────────────────
passed = sum(1 for v in EV.values() if v["ok"])
summary = {
    "ok": len(FAIL) == 0,
    "version": API.get("version"),
    "passed": passed,
    "failed": FAIL,
    "checks": len(EV),
    "key_numbers": {
        "bbox_px": bb0,
        "aspect_wh": asp0,
        "px_per_mm": ppm1,
        "width_mm": wmm,
        "scale_err_mm_at_300px": sc1.get("err_mm_at_300px"),
        "circle_diameter_px": dpx,
        "circle_residual_px": (big.get("geom") or {}).get("residual_px"),
        "symmetry": {"axis": sym.get("axis"), "score": sym.get("score"), "err_px": sym.get("mirror_err_px")},
        "lines": len(lines),
        "line_direction_deg": long_dirs[:4],
        "principal_axis_deg": pdeg,
        "probe_width_px": pr.get("width_px"),
        "probe_hot_avg_ms": round(avg_ms, 3),
        "measure_width_mm": row_w.get("value_mm"),
        "calib_ppm": [pv.get("front", {}).get("px_per_mm"), pv.get("side", {}).get("px_per_mm")],
        "calib_mm_max_delta": cons.get("mm_max_delta"),
    },
    "evidence": EV,
    "tmpdir": TMP,
}
print("HEADLESS " + json.dumps(summary, ensure_ascii=False, default=str))
if FAIL:
    sys.exit(1)
