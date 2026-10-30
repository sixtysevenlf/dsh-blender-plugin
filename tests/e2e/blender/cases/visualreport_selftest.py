# -*- coding: utf-8 -*-
"""tests/visualreport_selftest.py —— runtime/visualreport.py 的真机自检（同机位 QC 视觉报告）。

跑法（headless；qc/imgtools 用来验三级降级里的高档位，缺了也能过）：
    blender_rt_headless(preload="visualreport,qc,imgtools", engine="none", factory_startup=True,
        timeout_ms=300000,
        script_file="<包路径>/tests/visualreport_selftest.py")

断言（数字写死；不达标改代码，不改断言）：
  A. op 面：help（version=1 / ops=report,selftest,help / verdict_rules / limits）、selftest op ok。
  B. 同图：verdict=pass、IoU≈1、top_defects 空、overlay/sheet 落盘 bytes>0、metrics 兼容 JSON。
  C. 平移 16px：verdict=fail、缺/多 px 都 >0、top_defects≥1、reasons 非空、crop（≤3）文件存在、
     缺陷含 bbox_px/area_px/area_frac/center_px。
  D. 缺角 26px：verdict=fail、missing>extra、缺陷 bbox 落右上角、area_px≈676（±15%）。
  E. targets：px_per_mm=2 时 height 45±1 判 pass、期望 60 判 fail（verdict=fail）；
     不给 px_per_mm → target status=unresolved 且 verdict=degraded。
  F. bbox 归一：ref_box_px=[0,0,160,160]  vs 320×160 合成参考图的左半 → pass；
     render_box_px=[160,0,320,160] vs 右半合成渲染图 → pass；不给框 → 有"未给 ref_box_px" warning 且 verdict≠pass。
  G. 多视图：views=[ok,bad] → verdict=fail、两视图都进 views、top_defects 带 view 名、crops≤3。
  H. 坏参数全部 ok:false（ref 缺失 / 没 render / render+views 同给 / max_defects=-1 / px_per_mm=0 /
     target key 非法 / 空 box / 空 views / views 路径缺失 / 未知 op / iou_fail>iou_pass）。
  I. 输出契约：verdict 是第一字段；metrics{iou_min,iou_mean,dice_min,missing_px_total,extra_px_total,
     boundary_mean_px_max,views[]} 齐全；每视图 metrics 有 iou/dice/missing_px/extra_px/boundary；
     images{overlay,sheet,crops,per_view}、fix_hint、warnings、method、run_dir、ms 都在。
打印：HEADLESS {ok,pass,fail,verdicts,metrics,crops,ms,...}
"""
import json
import os
import time

import bpy
import numpy as np

OKS, FAILS = [], []
SEQ = [0]


def check(name, cond, detail=""):
    (OKS if cond else FAILS).append({"name": name, "detail": detail})
    print(("  [OK]   " if cond else "  [FAIL] ") + name + (" — " + str(detail)[:320] if detail else ""))


def save_png(arr_rgb, path):
    h, w = int(arr_rgb.shape[0]), int(arr_rgb.shape[1])
    SEQ[0] += 1
    img = bpy.data.images.new("VRT_%d" % SEQ[0], width=w, height=h, alpha=True)
    try:
        buf = np.ones((h, w, 4), dtype=np.float32)
        buf[:, :, :3] = np.clip(arr_rgb[::-1], 0.0, 1.0)
        img.pixels.foreach_set(buf.ravel())
        img.filepath_raw = path
        img.file_format = "PNG"
        img.save()
    finally:
        try:
            bpy.data.images.remove(img)
        except Exception:
            pass
    return path


def square_img(n=160, square=90, at=35, notch=0, dx=0, ox=None):
    a = np.ones((n, n, 3), dtype=np.float32)
    x0 = (at if ox is None else ox) + dx
    a[at:at + square, x0:x0 + square, :] = 0.18
    if notch:
        a[at:at + notch, x0 + square - notch:x0 + square, :] = 1.0
    return a


def comp_img(square=90, at=35, half="left", notch=0):
    a = np.ones((160, 320, 3), dtype=np.float32)
    x0 = at if half == "left" else 160 + at
    a[at:at + square, x0:x0 + square, :] = 0.18
    if notch:
        a[at:at + notch, x0 + square - notch:x0 + square, :] = 1.0
    return a


def img_bytes(path):
    try:
        return int(os.path.getsize(path))
    except Exception:
        return 0


def first_bad(report):
    for v in reversed(report.get("views") or []):
        if v.get("verdict") == "fail":
            return v
    return (report.get("views") or [{}])[0]


def main():
    t_start = time.perf_counter()
    api = getattr(K, "dsh_visualreport_api", None)   # noqa: F821  （headless 注入的持久内核）
    check("K.dsh_visualreport_api 已注册", api is not None, "preload 要带 visualreport")
    if api is None:
        return finish(t_start)
    qc_on = hasattr(K, "dsh_qc_api")
    img_on = hasattr(K, "dsh_img_api")

    # ── A. op 面
    h = api("help", {})
    check("A1 help version=1 + ops 齐全",
          h.get("version") == 1 and set(["report", "selftest", "help"]) <= set((h.get("ops") or {}).keys()),
          {k: h.get(k) for k in ("version",)})
    check("A2 help 有三态判定规则与边界说明",
          bool(h.get("verdict_rules")) and bool(h.get("limits")) and bool(h.get("outputs")))
    st = api("selftest", {})
    sev = st.get("evidence") or {}
    check("A3 selftest op ok（同图 pass / 缺角 fail / 坏参数拒绝）",
          st.get("ok") is True and sev.get("same_pass") is True and sev.get("notch_fail") is True
          and sev.get("bad_params_ok") is True, st.get("error") or sev)

    d = os.path.join(K.out_dir, "visualreport_selftest")
    os.makedirs(d, exist_ok=True)
    same = save_png(square_img(), os.path.join(d, "same.png"))
    shift = save_png(square_img(dx=16), os.path.join(d, "shift.png"))
    notch_png = save_png(square_img(notch=26), os.path.join(d, "notch.png"))
    comp_left = save_png(comp_img(half="left"), os.path.join(d, "comp_left.png"))
    comp_right = save_png(comp_img(half="right"), os.path.join(d, "comp_right.png"))

    # ── B. 同图 → pass
    r_same = api("report", {"ref": same, "render": same, "out_dir": d, "label": "same"})
    check("B1 同图 ok:true + verdict=pass + verdict 是第一字段",
          r_same.get("ok") is True and r_same.get("verdict") == "pass"
          and list(r_same.keys())[0] == "verdict", {"ok": r_same.get("ok"),
                                                    "verdict": r_same.get("verdict"),
                                                    "first": list(r_same.keys())[:2]})
    m_same = r_same.get("metrics") or {}
    check("B2 同图 IoU≈1、无缺陷",
          abs(float(m_same.get("iou_min") or 0) - 1.0) <= 0.01 and not (r_same.get("top_defects") or []),
          {"iou_min": m_same.get("iou_min"), "defects": len(r_same.get("top_defects") or [])})
    im_same = r_same.get("images") or {}
    check("B3 同图 overlay/sheet 落盘 bytes>0",
          img_bytes((im_same.get("overlay") or {}).get("path")) > 0
          and img_bytes((im_same.get("sheet") or {}).get("path")) > 0,
          {"overlay": (im_same.get("overlay") or {}).get("bytes"),
           "sheet": (im_same.get("sheet") or {}).get("bytes")})
    try:
        json.dumps(r_same)
        json_ok = True
    except Exception as e:
        json_ok = "%s: %s" % (type(e).__name__, str(e)[:120])
    check("B4 回执可 JSON 序列化（无 numpy 类型泄漏）", json_ok is True, json_ok)
    v_same = (r_same.get("views") or [{}])[0]
    check("B5 metrics 契约齐全（顶层 + 每视图）",
          all(k in m_same for k in ("iou_min", "iou_mean", "dice_min", "missing_px_total",
                                    "extra_px_total", "boundary_mean_px_max", "views"))
          and all(k in (v_same.get("metrics") or {}) for k in
                  ("iou", "dice", "missing_px", "extra_px", "missing_frac", "extra_frac", "boundary")),
          sorted((v_same.get("metrics") or {}).keys()))
    check("B6 元信息齐全（fix_hint/method/run_dir/ms/warnings）",
          isinstance(r_same.get("fix_hint"), str) and isinstance(r_same.get("method"), dict)
          and bool(r_same.get("run_dir")) and isinstance(r_same.get("ms"), int)
          and isinstance(r_same.get("warnings"), list))
    check("B7 交叉核验在预载时命中（qc 优先，缺 qc 落 imgtools）",
          ((v_same.get("crosscheck") or {}).get("source") == "qc.compare") if qc_on
          else ((v_same.get("crosscheck") or {}).get("source") in ("imgtools.diff", None)),
          {"qc": qc_on, "img": img_on, "cross": (v_same.get("crosscheck") or {}).get("source")})

    # ── C. 平移 → fail + 缺陷 + crop
    r_shift = api("report", {"ref": same, "render": shift, "out_dir": d, "label": "shift", "max_defects": 3})
    ms = r_shift.get("metrics") or {}
    defects = r_shift.get("top_defects") or []
    crops = (r_shift.get("images") or {}).get("crops") or []
    check("C1 平移 ok:true + verdict=fail + reasons 非空",
          r_shift.get("ok") is True and r_shift.get("verdict") == "fail" and bool(r_shift.get("reasons")),
          {"verdict": r_shift.get("verdict"), "reasons": (r_shift.get("reasons") or [])[:2]})
    check("C2 平移 缺/多 px 都 >0、IoU 下降",
          int(ms.get("missing_px_total") or 0) > 0 and int(ms.get("extra_px_total") or 0) > 0
          and float(ms.get("iou_min") or 0) < 0.9, ms)
    check("C3 平移 top_defects 非空且字段齐全",
          len(defects) >= 1 and all(all(k in x for k in ("kind", "view", "bbox_px", "area_px",
                                                          "area_frac", "center_px"))
                                    for x in defects),
          [sorted(x.keys()) for x in defects[:1]])
    check("C4 平移 crop ≤3 张且文件 bytes>0",
          1 <= len(crops) <= 3 and all(img_bytes(c.get("path")) > 0 for c in crops),
          [{"path": c.get("path"), "bytes": c.get("bytes")} for c in crops])
    check("C5 平移 fix_hint 指出缺失/多出方向",
          ("缺失" in (r_shift.get("fix_hint") or "")) or ("多出" in (r_shift.get("fix_hint") or "")),
          r_shift.get("fix_hint"))
    try:
        json.dumps(r_shift)
        check("C6 平移回执可 JSON 序列化", True)
    except Exception as e:
        check("C6 平移回执可 JSON 序列化", False, "%s: %s" % (type(e).__name__, str(e)[:120]))

    # ── D. 缺角 → missing 为主 + bbox 位置/面积
    r_notch = api("report", {"ref": same, "render": notch_png, "out_dir": d, "label": "notch",
                             "px_per_mm": 2.0, "max_defects": 3})
    mn = r_notch.get("metrics") or {}
    nd = (r_notch.get("top_defects") or [])
    check("D1 缺角 verdict=fail、missing > extra",
          r_notch.get("verdict") == "fail" and int(mn.get("missing_px_total") or 0) > int(mn.get("extra_px_total") or 0),
          {"missing": mn.get("missing_px_total"), "extra": mn.get("extra_px_total")})
    if nd:
        d0 = nd[0]
        area_ok = abs(int(d0.get("area_px") or 0) - 676) <= int(676 * 0.15)
        bbox = d0.get("bbox_px") or [0, 0, 0, 0]
        pos_ok = d0.get("kind") == "missing" and bbox[0] >= 95 and bbox[1] <= 40
        check("D2 缺角缺陷：kind=missing、bbox 在右上角、面积≈676", area_ok and pos_ok,
              {"kind": d0.get("kind"), "bbox": bbox, "area": d0.get("area_px")})
        check("D3 px_per_mm 给了 → 面积/位置有 mm 口径",
              d0.get("area_mm2") is not None and abs(float(d0.get("area_mm2")) - 169.0) <= 30.0
              and isinstance(d0.get("center_mm"), list) and len(d0.get("center_mm")) == 2,
              {"area_mm2": d0.get("area_mm2"), "center_mm": d0.get("center_mm")})
    else:
        check("D2 缺角缺陷：kind=missing、bbox 在右上角、面积≈676", False, "top_defects 为空")
        check("D3 px_per_mm 给了 → 面积/位置有 mm 口径", False, "top_defects 为空")

    # ── E. targets
    r_tp = api("report", {"ref": same, "render": same, "px_per_mm": 2.0,
                          "targets": [{"key": "height", "expect_mm": 45, "tol_mm": 1}],
                          "out_dir": d, "label": "target_pass"})
    r_tf = api("report", {"ref": same, "render": same, "px_per_mm": 2.0,
                          "targets": [{"key": "height", "expect_mm": 60, "tol_mm": 1}],
                          "out_dir": d, "label": "target_fail"})
    r_tu = api("report", {"ref": same, "render": same,
                          "targets": [{"key": "height", "expect_mm": 45, "tol_mm": 1}],
                          "out_dir": d, "label": "target_unres"})
    tp = (r_tp.get("targets") or [{}])
    tf = (r_tf.get("targets") or [{}])
    tu = (r_tu.get("targets") or [{}])
    check("E1 target 命中 → status=pass / verdict=pass",
          r_tp.get("verdict") == "pass" and (tp[0] or {}).get("status") == "pass"
          and abs(float((tp[0] or {}).get("measured_mm") or 0) - 45.0) <= 1.0,
          {"verdict": r_tp.get("verdict"), "target": tp[0]})
    check("E2 target 超标 → status=fail / verdict=fail",
          r_tf.get("verdict") == "fail" and (tf[0] or {}).get("status") == "fail",
          {"verdict": r_tf.get("verdict"), "target": tf[0]})
    check("E3 无 px_per_mm → targets=unresolved / verdict=degraded",
          r_tu.get("verdict") == "degraded" and (tu[0] or {}).get("status") == "unresolved"
          and bool(r_tu.get("degraded_reasons")),
          {"verdict": r_tu.get("verdict"), "target": tu[0],
           "degraded": r_tu.get("degraded_reasons")})

    # ── F. bbox 归一（ref_box_px / render_box_px）
    r_box = api("report", {"ref": comp_left, "render": same, "ref_box_px": [0, 0, 160, 160],
                           "out_dir": d, "label": "ref_box"})
    r_rbox = api("report", {"ref": same, "render": comp_right, "render_box_px": [160, 0, 320, 160],
                            "out_dir": d, "label": "render_box"})
    r_nobox = api("report", {"ref": comp_left, "render": same, "out_dir": d, "label": "no_box"})
    check("F1 ref_box_px 裁切后同图 → pass",
          r_box.get("ok") is True and r_box.get("verdict") == "pass",
          {"verdict": r_box.get("verdict"), "iou": (r_box.get("metrics") or {}).get("iou_min")})
    check("F2 render_box_px 裁切后同图 → pass",
          r_rbox.get("ok") is True and r_rbox.get("verdict") == "pass",
          {"verdict": r_rbox.get("verdict"), "iou": (r_rbox.get("metrics") or {}).get("iou_min")})
    check("F3 不给框 → warning 里写明整图对齐，且 verdict≠pass",
          any("未给 ref_box_px" in str(w) for w in (r_nobox.get("warnings") or []))
          and r_nobox.get("verdict") != "pass",
          {"verdict": r_nobox.get("verdict"), "warnings": (r_nobox.get("warnings") or [])[:2]})

    # ── G. 多视图
    r_multi = api("report", {"ref": same,
                             "views": [{"name": "ok", "path": same}, {"name": "bad", "path": shift}],
                             "max_defects": 2, "out_dir": d, "label": "multi"})
    mvs = r_multi.get("views") or []
    mdef = r_multi.get("top_defects") or []
    mcrops = (r_multi.get("images") or {}).get("crops") or []
    check("G1 多视图 verdict=fail + 两视图都回执",
          r_multi.get("verdict") == "fail" and len(mvs) == 2
          and set(v.get("name") for v in mvs) == set(["ok", "bad"]),
          {"verdict": r_multi.get("verdict"), "views": [v.get("name") for v in mvs]})
    check("G2 多视图 top_defects 带 view 名、crops≤3、per_view 两路",
          all(x.get("view") in ("ok", "bad") for x in mdef) and len(mcrops) <= 3
          and len((r_multi.get("images") or {}).get("per_view") or []) == 2,
          {"defects": [x.get("view") for x in mdef], "crops": len(mcrops)})
    check("G3 聚合 metrics：iou_min 是最低视图",
          abs(float((r_multi.get("metrics") or {}).get("iou_min") or 1.0)
              - min(float((v.get("metrics") or {}).get("iou") or 0.0) for v in mvs)) <= 0.01,
          (r_multi.get("metrics") or {}).get("iou_min"))

    # ── H. max_defects 限流
    r_md = api("report", {"ref": same, "render": shift, "max_defects": 1, "out_dir": d, "label": "md1"})
    check("H1 max_defects=1 → top_defects≤1、crop≤1",
          len(r_md.get("top_defects") or []) <= 1
          and len((r_md.get("images") or {}).get("crops") or []) <= 1,
          {"defects": len(r_md.get("top_defects") or [])})

    # ── I. 坏参数（全部 ok:false，不能静默通过）
    bad_cases = [
        ("ref 缺失", {"ref": os.path.join(d, "nope.png"), "render": same}),
        ("没 render/views", {"ref": same}),
        ("render+views", {"ref": same, "render": same, "views": [{"name": "a", "path": same}]}),
        ("max_defects=-1", {"ref": same, "render": same, "max_defects": -1}),
        ("px_per_mm=0", {"ref": same, "render": same, "px_per_mm": 0}),
        ("target key 非法", {"ref": same, "render": same,
                             "targets": [{"key": "weight", "expect_mm": 1, "tol_mm": 1}]}),
        ("空 box", {"ref": same, "render": same, "ref_box_px": [10, 10, 5, 5]}),
        ("views=[]", {"ref": same, "views": []}),
        ("views 路径缺失", {"ref": same, "views": [{"name": "a", "path": os.path.join(d, "nope2.png")}]}),
        ("iou_fail>iou_pass", {"ref": same, "render": same, "iou_pass": 0.5, "iou_fail": 0.9}),
    ]
    bad_rows = []
    for label, args in bad_cases:
        rr = api("report", args)
        bad_rows.append({"case": label, "ok": rr.get("ok"), "error": (rr.get("error") or "")[:110]})
    r_unk = api("nope", {})
    bad_ok = all(x["ok"] is False for x in bad_rows) and r_unk.get("ok") is False
    check("I1 坏参数 10 例 + 未知 op 全部 ok:false", bad_ok,
          {"cases": bad_rows, "unknown": {k: r_unk.get(k) for k in ("ok", "error") if k in r_unk}})

    # ── J. 产物目录与文件真实存在
    run_dirs = [r_same.get("run_dir"), r_shift.get("run_dir"), r_notch.get("run_dir")]
    files_ok = all(bool(p) and os.path.isdir(p) for p in run_dirs[:2])
    check("J1 每次报告独立 run_dir（目录存在）", files_ok, run_dirs)

    wall_ms = int(round((time.perf_counter() - t_start) * 1000.0))
    return finish(t_start, api, extra={
        "qc_available": qc_on, "imgtools_available": img_on,
        "verdicts": {"same": r_same.get("verdict"), "shift": r_shift.get("verdict"),
                     "notch": r_notch.get("verdict"), "multi": r_multi.get("verdict"),
                     "target_pass": r_tp.get("verdict"), "target_fail": r_tf.get("verdict"),
                     "target_unres": r_tu.get("verdict")},
        "metrics": {"same": m_same, "shift": ms, "notch": mn},
        "defects": {"shift": len(defects), "notch": len(nd)},
        "crops": {"shift": len(crops)},
        "ms": {"report_same": r_same.get("ms"), "report_shift": r_shift.get("ms"),
               "report_notch": r_notch.get("ms"), "selftest_op": st.get("ms"), "wall": wall_ms},
        "out_dir": d,
    })


def finish(t_start, api=None, extra=None):
    wall = int(round((time.perf_counter() - t_start) * 1000.0))
    out = {"ok": len(FAILS) == 0, "probe": "visualreport_selftest", "version": 1,
           "pass": len(OKS), "fail": len(FAILS), "fails": FAILS,
           "wall_ms": wall}
    if extra:
        out.update(extra)
    print("")
    print("visualreport selftest：%d 通过 / %d 失败（%.1fs）" % (len(OKS), len(FAILS), wall / 1000.0))
    for f in FAILS:
        print("  - " + f["name"])
    print("HEADLESS " + json.dumps(out, ensure_ascii=False, default=str))
    return out


main()
