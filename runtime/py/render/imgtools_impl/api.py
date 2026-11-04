# -*- coding: utf-8 -*-
"""Split from imgtools.py; behavior preserved by the facade."""
from .common import *  # noqa: F401,F403
from .ops_basic import img_annotate, img_crop, img_diff, img_rectify, img_scan
from .hough import _fit_scale, _parse_known_lengths, _spec_px
from .digest import img_digest
from .measure import img_measure, img_probe



# ── img_calib：多视逐视标定 + 跨视共享尺寸一致性 ────────────────────────
def _img_calib_p1(st):
    shared, views = st["shared"], st.get("views")
    cx, cy, fit, i, items, mids, n_points, name, ppm, resid_pct, resid_px, same, source, v, verdict, why = (st.get("cx"), st.get("cy"), st.get("fit"), st.get("i"), st.get("items"), st.get("mids"), st.get("n_points"), st.get("name"), st.get("ppm"), st.get("resid_pct"), st.get("resid_px"), st.get("same"), st.get("source"), st.get("v"), st.get("verdict"), st.get("why"))
    try:
        if views is None:
            return _j({"ok": False, "error": "views 必填（每视至少一个已知长度或 px_per_mm）",
                       "hint": "views=[{id:'front', known_len:[{mm:160, px:320}]}, {id:'side', known_len:[{mm:100,px:200}]}]"})
        if isinstance(views, dict):
            views = [views]
        if not isinstance(views, (list, tuple)) or len(views) == 0:
            return _j({"ok": False, "error": "views 必须是非空列表", "views": views})
        per = []
        ppm_by = {}
        cx_by = {}
        for i, v in enumerate(views):
            if not isinstance(v, dict):
                return _j({"ok": False, "error": "第 %d 个 view 不是 dict" % (i + 1), "view": v})
            name = str(v.get("id") or v.get("name") or ("view%d" % (i + 1)))
            items = _parse_known_lengths(v.get("known_len") or v.get("known") or v.get("lengths"))
            ppm = None
            n_points = 0
            resid_px = None
            resid_pct = None
            source = None
            why = None
            verdict = "needs_data"
            if v.get("px_per_mm") is not None:
                try:
                    ppm = float(v["px_per_mm"])
                except Exception:
                    ppm = None
                if ppm and ppm > 0:
                    source = "given"
                    n_points = len(items)
                    verdict = "ok"
                    why = "调用方直接给了 px_per_mm"
                else:
                    ppm = None
            if ppm is None and items:
                fit = _fit_scale(items)
                if fit:
                    ppm = float(fit["px_per_mm"])
                    n_points = int(fit["n_points"])
                    resid_px = fit.get("resid_px")
                    resid_pct = fit.get("resid_pct")
                    source = "ls_known_len"
                    if resid_pct is not None and float(resid_pct) > 2.0:
                        verdict = "fail"
                        why = "多组已知长度不一致：残差 %.2f%% > 2%%（取点错/透视/不是同一平面）" % float(resid_pct)
                    else:
                        verdict = "ok"
                        why = "最小二乘 px_per_mm=%.4f（%d 组已知长度）" % (ppm, n_points)
            if ppm is None:
                verdict = "needs_data"
                why = "没给 known_len / px_per_mm：这一视没法标定"
            mids = [it.get("mid") for it in items if it.get("mid")]
            cx = sum(float(m[0]) for m in mids) / float(len(mids)) if mids else None
            cy = sum(float(m[1]) for m in mids) / float(len(mids)) if mids else None
            if cx is None:
                try:
                    if v.get("size_px") and len(v["size_px"]) >= 2:
                        cx, cy = float(v["size_px"][0]) / 2.0, float(v["size_px"][1]) / 2.0
                except Exception:
                    pass
            per.append({"id": name, "px_per_mm": (round(ppm, 6) if ppm else None),
                        "cx_px": (round(cx, 2) if cx is not None else None),
                        "cy_px": (round(cy, 2) if cy is not None else None),
                        "n_points": int(n_points), "resid_px": resid_px, "resid_pct": resid_pct,
                        "verdict": verdict, "source": source, "why": why})
            if ppm:
                ppm_by[name] = float(ppm)
                cx_by[name] = cx
        # 跨视一致性
        shared_list = []
        if isinstance(shared, dict):
            shared_list = [shared]
        elif isinstance(shared, (list, tuple)):
            shared_list = [s for s in shared if isinstance(s, dict)]
        cons = {"pairwise": [], "mm_max_delta": None, "same_scale": None,
                "keys": [], "tol_pct": None}
        ppms = [p for p in ppm_by.values() if p]
        if len(ppms) >= 2:
            same = (max(ppms) / min(ppms)) <= 1.01
            cons["same_scale"] = bool(same)
            cons["ppm_min"] = round(min(ppms), 6)
            cons["ppm_max"] = round(max(ppms), 6)
            cons["ppm_spread_pct"] = round(100.0 * (max(ppms) / min(ppms) - 1.0), 3)
        inconsistent = False
        deltas = []
    finally:
        st.update((k, v) for k, v in locals().items() if k != "st")

def _img_calib_p2(st):
    assert_same_scale, auto_ortho, cons, deltas, per = (
        st["assert_same_scale"], st["auto_ortho"], st.get("cons"), st.get("deltas"), st.get("per"))
    ppm_by, shared_list = st.get("ppm_by"), st.get("shared_list")
    a, ai, b, bi_, combo, combos, delta, expected, inconsistent, key, ma, mb, mean, mm, names, nm, ppm, px, sh, si, spec, tol, tol_mm_arg, tol_pct, vals, vmap, vn, vv = (st.get("a"), st.get("ai"), st.get("b"), st.get("bi_"), st.get("combo"), st.get("combos"), st.get("delta"), st.get("expected"), st.get("inconsistent"), st.get("key"), st.get("ma"), st.get("mb"), st.get("mean"), st.get("mm"), st.get("names"), st.get("nm"), st.get("ppm"), st.get("px"), st.get("sh"), st.get("si"), st.get("spec"), st.get("tol"), st.get("tol_mm_arg"), st.get("tol_pct"), st.get("vals"), st.get("vmap"), st.get("vn"), st.get("vv"))
    try:
        for si, sh in enumerate(shared_list):
            key = str(sh.get("key") or sh.get("id") or ("shared%d" % (si + 1)))
            tol_pct = float(sh.get("tol_pct") or 1.0)
            tol_mm_arg = sh.get("tol_mm")
            expected = sh.get("mm", sh.get("expect_mm", sh.get("value_mm")))
            try:
                expected = float(expected) if expected is not None else None
            except Exception:
                expected = None
            vmap = sh.get("views") or sh.get("per_view") or {}
            if not isinstance(vmap, dict) or not vmap:
                vmap = {k: v for k, v in sh.items()
                        if k not in ("key", "id", "mm", "expect_mm", "value_mm", "tol_pct", "tol_mm")
                        and isinstance(v, (dict, int, float))}
            vals = {}
            for vn, spec in vmap.items():
                px = _spec_px(spec)
                ppm = ppm_by.get(str(vn))
                mm = (px / ppm) if (px is not None and ppm) else None
                vals[str(vn)] = {"px": (round(float(px), 3) if px is not None else None),
                                 "mm": (round(float(mm), 4) if mm is not None else None)}
            names = list(vals.keys())
            combos = []
            for ai in range(len(names)):
                for bi_ in range(ai + 1, len(names)):
                    a, b = names[ai], names[bi_]
                    ma, mb = vals[a]["mm"], vals[b]["mm"]
                    if ma is None or mb is None:
                        continue
                    mean = 0.5 * (ma + mb)
                    tol = abs(mean) * tol_pct / 100.0 if tol_mm_arg is None \
                        else max(abs(float(tol_mm_arg)), abs(mean) * tol_pct / 100.0)
                    delta = ma - mb
                    combo = {"key": key, "a": a, "b": b, "mm_a": ma, "mm_b": mb,
                             "delta_mm": round(delta, 4),
                             "delta_pct": (round(100.0 * delta / mean, 3) if mean else None),
                             "tol_mm": round(tol, 4),
                             "consistent": bool(abs(delta) <= tol + 1e-9)}
                    combos.append(combo)
                    deltas.append(abs(delta))
                    if not combo["consistent"]:
                        inconsistent = True
            if expected is not None:
                for nm, vv in vals.items():
                    if vv["mm"] is None:
                        continue
                    delta = vv["mm"] - expected
                    tol = abs(expected) * tol_pct / 100.0 if tol_mm_arg is None \
                        else max(abs(float(tol_mm_arg)), abs(expected) * tol_pct / 100.0)
                    combo = {"key": key, "a": nm, "b": "expected", "mm_a": vv["mm"],
                             "mm_b": round(expected, 4), "delta_mm": round(delta, 4),
                             "delta_pct": (round(100.0 * delta / expected, 3) if expected else None),
                             "tol_mm": round(tol, 4),
                             "consistent": bool(abs(delta) <= tol + 1e-9)}
                    combos.append(combo)
                    deltas.append(abs(delta))
                    if not combo["consistent"]:
                        inconsistent = True
            cons["pairwise"].extend(combos)
            cons["keys"].append(key)
        if deltas:
            cons["mm_max_delta"] = round(max(deltas), 4)
        verdict = "ok"
        any_fail = any(p["verdict"] == "fail" for p in per)
        any_data = any(p["verdict"] == "needs_data" for p in per)
        if any_fail:
            verdict = "fail"
        elif inconsistent:
            verdict = "inconsistent"
        elif assert_same_scale and cons.get("same_scale") is False:
            verdict = "inconsistent"
        elif any_data:
            verdict = "needs_data"
        ortho = {"enabled": bool(auto_ortho), "perspective_score": None, "status": "unresolved",
                 "note": "不做自动消失点：单张/未知标定图无法可靠推 perspective；perspective_score=null。"
                         "要正交假设，先用 img_rectify 给四角点做单应校正，再 img_calib"}
        return _j({"ok": bool(verdict == "ok"), "verdict": verdict, "per_view": per,
                   "consistency": cons, "auto_ortho": ortho, "units": "mm",
                   "provenance": {"method": "per-view least squares over known lengths; "
                                            "shared dims converted with each view's px_per_mm then compared",
                                  "views": [p["id"] for p in per],
                                  "shared_keys": cons["keys"]},
                   "honest_limits": ["cx_px 是标定参考点的像素中心，不是相机主点（本模块不做内参标定）",
                                     "同视多组已知长度残差 >2% 判 fail（取点错/透视/不共面）",
                                     "same_scale 只在多视且都标定出 px_per_mm 时给（假设正交同尺度）"]})
    finally:
        st.update((k, v) for k, v in locals().items() if k != "st")

def img_calib(views=None, shared=None, auto_ortho=False, assert_same_scale=False):
    """多视标定：每视用已知长度做最小二乘 px_per_mm；shared 尺寸跨视换算成 mm 后两两比。

    views=[{id, known_len:[{mm,px}|{mm,p1_px,p2_px}], px_per_mm?}]；
    shared=[{key, mm?, views:{视名: 像素值|{px}|{p1_px,p2_px}}, tol_pct?, tol_mm?}]。
    不做自动消失点：perspective_score 恒 null / unresolved（要正交先 img_rectify 四角点校正）。
    """
    st = dict(locals())
    for _fn_ in (_img_calib_p1, _img_calib_p2):
        _r = _fn_(st)
        if _r is not None:
            return _r


def img_selftest():
    """自检：合成两张已知几何的图（实心块 + 平移块），断言 scan/crop/annotate/diff 的数字都对得上。"""
    ev = {}
    try:
        import tempfile
        d = tempfile.mkdtemp(prefix="dsh-img-selftest-")
        W, H = 200, 120
        base = np.zeros((H, W, 4), np.float32); base[:, :, 3] = 1.0
        base[30:90, 20:100, 0:3] = 0.1          # 深色块 80x60
        p1 = _save(base, os.path.join(d, "a.png"))
        shift = np.zeros((H, W, 4), np.float32); shift[:, :, 3] = 1.0
        shift[30:90, 40:120, 0:3] = 0.1          # 同尺寸、右移 20px
        p2 = _save(shift, os.path.join(d, "b.png"))
        s = json.loads(img_scan(p1))
        ev["scan"] = {"bbox": s.get("bbox"), "aspect": s.get("aspect_wh"), "fill": s.get("fill_ratio"),
                      "axis": s.get("principal_axis_deg")}
        ok_scan = s.get("bbox") == [20, 30, 80, 60] and abs(s.get("aspect_wh", 0) - 80 / 60.0) < 0.01
        c = json.loads(img_crop(p1, 20, 30, 80, 60, scale=2.0, out=os.path.join(d, "crop.png")))
        ev["crop"] = {"out_size": c.get("out_size")}
        ok_crop = c.get("out_size") == [160, 120] and os.path.exists(c.get("out"))
        an = json.loads(img_annotate(p1, lines=[{"x1": 0, "y1": 0, "x2": 199, "y2": 0, "label": "top"}],
                                     boxes=[{"x": 20, "y": 30, "w": 80, "h": 60, "label": "silhouette"}],
                                     grid={"step": 20}, out=os.path.join(d, "ann.png")))
        ev["annotate"] = {"legend": [x.get("kind") for x in an.get("legend", [])]}
        ok_an = os.path.exists(an.get("out")) and len(an.get("legend", [])) == 3
        df = json.loads(img_diff(p1, p2, out=os.path.join(d, "diff.png")))
        ev["diff"] = {"iou": df.get("iou"), "a_only": df.get("a_only_px"), "b_only": df.get("b_only_px")}
        # 对齐后 bbox 都归一到 80x60：完全重合 ⇒ IoU≈1、a_only=b_only=0
        ok_diff = abs((df.get("iou") or 0) - 1.0) < 0.02 and df.get("a_only_px") == 0 and df.get("b_only_px") == 0
        df2 = json.loads(img_diff(p1, p2, align=False))
        ev["diff_unaligned"] = {"iou": df2.get("iou")}
        ok_diff2 = (df2.get("iou") or 1) < 0.85      # 不对齐时明显更低 —— 证明它真的在量差异
        return _j({"ok": bool(ok_scan and ok_crop and ok_an and ok_diff and ok_diff2), "evidence": ev,
                   "tmpdir": d})
    except Exception as e:
        return _j({"ok": False, "evidence": ev, "error": "%s: %s" % (type(e).__name__, str(e)[:220])})


def img_help():
    return _j({
        "module": "imgtools.py", "version": IMG_VERSION,
        "what": "参考图量具：把'看不清'变成'不用看'（量成数字 / 裁出来放大 / 画回去 / 差分）",
        "ops": {
            "img_scan": "只读：轮廓 bbox / 长宽比 / 填充率 / 主轴角 / 主要直线角度 / ROI 前景率 / 指定点取色",
            "img_crop": "ROI 裁剪 + 上采样落盘（一次只看一块，尽量占满视觉输入）",
            "img_annotate": "把线/框/十字/网格画回图（颜色看图、数字看 legend）",
            "img_diff": "两张图轮廓差分：IoU + a_only/b_only + 剖面差 + 红蓝叠加图",
            "img_digest": "v2 主力：一次给轮廓/对称/直线/Hough/孔圆/比例尺/可读性 + 原生裁剪 + 标注总图 + verdict（ready_for_spec|needs_scale|needs_views|unreadable|degraded）；无 known_len 时 mm 全 null",
            "img_probe": "v2 按需量测（读缓存快）：scan_y/scan_x 亚像素边缘与多段、circle/holes 圆孔、distance 两点距离、profile 剖面",
            "img_measure": "v2：像素→mm 并和期望值比，回 rows[{key,value_mm,expect_mm,delta_mm,delta_pct,verdict}]",
            "img_calib": "v2：多视逐视最小二乘 px_per_mm + shared 尺寸跨视换算 mm 两两比（不做自动消失点）",
            "img_selftest": "自检（合成已知几何，断言 bbox/尺寸/IoU 都对）",
        },
        "protocol": ["看图必须产出数字（写进 spec.py），否则算白看",
                     "每次 ≤2 张图，且每张带明确问题（yes/no 或数字）",
                     "每张图先 img_crop 到 ROI 再放大；不要给整张",
                     "判断'像不像'用 img_diff / qc_compare 的数字，不靠肉眼",
                     "像素↔毫米必须标定（给标尺或已知长度）"],
        "limits": ["照片背景复杂时先抠图（rembg）再 img_scan",
                   "img_crop 用最近邻上采样：不制造假细节",
                   "这套是「量具」不是「眼睛」：数值可比，观感仍需人/强模型判"],
    })


def img_dispatch(op, args_json):
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
        kw = _KIT.resolve_refs(kw)   # 单步调用也支持 "@工件"
    except KeyError as e:
        return _j({"ok": False, "error": "引用解析失败: %s" % str(e)[:160]})
    ops = {"scan": img_scan, "rectify": img_rectify, "crop": img_crop, "annotate": img_annotate, "diff": img_diff,
           "digest": img_digest, "probe": img_probe, "measure": img_measure, "calib": img_calib,
           "selftest": img_selftest, "help": img_help}
    fn = ops.get(str(op))
    if fn is None:
        return _j({"ok": False, "error": "unknown img op", "op": op, "ops": sorted(ops)})
    import inspect
    try:
        allowed = set(inspect.signature(fn).parameters)
        unknown = sorted(set(kw) - allowed)
        if unknown:
            return _j({"ok": False, "error": "不认识的参数 %s" % unknown, "op": op, "allowed": sorted(allowed)})
    except (TypeError, ValueError):
        pass
    try:
        return fn(**kw)
    except Exception as e:
        return _j({"ok": False, "error": "%s: %s" % (type(e).__name__, str(e)[:220]), "op": op})


_DshApi = _KIT.Api  # 共享内核（尾部注册行无需改）
import sys as _sys
_K = _sys.modules.get("dsh_rt_kernel")
if _K is not None:
    _K.dsh_img_api = _DshApi({"version": IMG_VERSION, "dispatch": img_dispatch, "scan": img_scan,
                              "crop": img_crop, "annotate": img_annotate, "diff": img_diff,
                              "digest": img_digest, "probe": img_probe, "measure": img_measure,
                              "calib": img_calib, "selftest": img_selftest, "help": img_help})
