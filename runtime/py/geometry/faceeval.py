# -*- coding: utf-8 -*-
"""DSH 人脸比例门（v0.9.6 · P1 精简版）：把「脸像不像」变成可优化的数字。

landmarks（像素坐标，上原点）由调用方给 —— 模型自己标，或 MediaPipe/EMOCA 出：
  top chin face_l face_r eye_l eye_r nose_base mouth_l mouth_r

ops: face_ratios / face_compare / face_selftest / face_help
⚠ 经典比例是参考值：风格/年龄/种族不同要按项目在 spec 里覆盖 canon/tol。
"""
import json

FACE_VERSION = 1

CANON = {"eye_line_frac": 0.50, "nose_base_frac": 0.72, "mouth_line_frac": 0.82,
         "ipd_over_width": 0.46, "mouth_width_over_width": 0.33, "face_w_over_h": 0.72}
TOL = {k: 0.08 for k in CANON}


import sys as _sys_kit
_KIT = getattr(_sys_kit.modules.get("dsh_rt_kernel"), "dsh_kit", None)
if _KIT is None:
    raise RuntimeError("faceeval 需要共享内核 K.dsh_kit（由 KERNEL_BOOTSTRAP 注入）")


_j = _KIT.j  # 共享内核（原自带实现已删，见 S1）
def _pt(lm, k):
    v = lm.get(k)
    if not isinstance(v, (list, tuple)) or len(v) < 2:
        return None
    return float(v[0]), float(v[1])


def ratios_from_landmarks(lm):
    """landmarks -> 6 个无量纲比例；缺点的项直接不给（不编数）。"""
    if not isinstance(lm, dict):
        raise ValueError("landmarks 必须是对象 {名称:[x,y]}")
    top, chin = _pt(lm, "top"), _pt(lm, "chin")
    if top is None or chin is None:
        raise ValueError("至少要有 top 与 chin")
    head_h = abs(chin[1] - top[1])
    if head_h <= 1e-9:
        raise ValueError("top/chin 的 y 相同，头高为 0")
    r = {}
    fl, fr = _pt(lm, "face_l"), _pt(lm, "face_r")
    width = abs(fr[0] - fl[0]) if (fl and fr) else None
    el, er = _pt(lm, "eye_l"), _pt(lm, "eye_r")
    if el and er:
        r["eye_line_frac"] = round((((el[1] + er[1]) / 2.0) - top[1]) / head_h, 4)
        if width:
            r["ipd_over_width"] = round(abs(er[0] - el[0]) / width, 4)
    if width:
        r["face_w_over_h"] = round(width / head_h, 4)
    nb = _pt(lm, "nose_base")
    if nb:
        r["nose_base_frac"] = round((nb[1] - top[1]) / head_h, 4)
    ml, mr = _pt(lm, "mouth_l"), _pt(lm, "mouth_r")
    if ml and mr:
        r["mouth_line_frac"] = round((((ml[1] + mr[1]) / 2.0) - top[1]) / head_h, 4)
        if width:
            r["mouth_width_over_width"] = round(abs(mr[0] - ml[0]) / width, 4)
    return r


def face_ratios(landmarks, canon=None, tol=None):
    """比例表 + 与经典比例的相对误差 + 逐项判定（给 gate spec 当 pass_if 字段用）。"""
    got = ratios_from_landmarks(landmarks)
    c = dict(CANON, **(canon or {}))
    t = dict(TOL, **(tol or {}))
    rows, fails = [], []
    for k, v in got.items():
        base = c.get(k)
        if base is None:
            rows.append({"ratio": k, "value": v, "verdict": "no_baseline"})
            continue
        rel = abs(v - base) / (abs(base) if abs(base) > 1e-9 else 1.0)
        lim = float(t.get(k, 0.08))
        verdict = "pass" if rel <= lim else "fail"
        rows.append({"ratio": k, "value": v, "canon": base, "rel_err": round(rel, 4), "tol": lim, "verdict": verdict})
        if verdict == "fail":
            fails.append(k)
    return _j({"ok": not fails, "ratios": {r["ratio"]: r["value"] for r in rows}, "rows": rows,
               "failed": fails, "worst_rel_err": (max([r.get("rel_err") or 0 for r in rows]) if rows else None),
               "missing": [k for k in CANON if k not in got],
               "note": "经典比例只作参考：按项目覆盖 canon/tol；missing = landmarks 没给够点"})


def face_compare(attempt, ref, canon=None, tol=None, tol_ratio=0.08):
    """两套 landmarks 的逐项对比：相对误差 + 判定（用于「这版比参考差多少」）。"""
    ra, rr = ratios_from_landmarks(attempt), ratios_from_landmarks(ref)
    rows, fails = [], []
    for k, v in ra.items():
        rv = rr.get(k)
        if rv is None:
            continue
        rel = abs(v - rv) / (abs(rv) if abs(rv) > 1e-9 else 1.0)
        verdict = "pass" if rel <= float(tol_ratio) else "fail"
        rows.append({"ratio": k, "attempt": v, "ref": rv, "rel_err": round(rel, 4), "verdict": verdict})
        if verdict == "fail":
            fails.append(k)
    return _j({"ok": not fails, "rows": rows, "failed": fails, "worst_rel_err": (max([r["rel_err"] for r in rows]) if rows else None),
               "note": "这是「与参考的差距」；face_ratios 是「与经典比例的差距」，两者都要看"})


def face_selftest():
    """自检：严格符合 canon 的 landmarks 必须全过；偏 20%% 的必须挂 ≥3 项。"""
    ev = {}
    try:
        def mk(eye_frac, ipd, nose, mouth, w_h):
            h = 400.0
            top = (200.0, 100.0)
            w = w_h * h
            fl, fr = (200.0 - w / 2.0, 300.0), (200.0 + w / 2.0, 300.0)
            ey = top[1] + eye_frac * h
            el, er = (200.0 - ipd * w / 2.0, ey), (200.0 + ipd * w / 2.0, ey)
            return {"top": top, "chin": (200.0, 100.0 + h), "face_l": fl, "face_r": fr,
                    "eye_l": el, "eye_r": er, "nose_base": (200.0, top[1] + nose * h),
                    "mouth_l": (200.0 - mouth * w / 2.0, top[1] + 0.82 * h),
                    "mouth_r": (200.0 + mouth * w / 2.0, top[1] + 0.82 * h)}
        good = mk(CANON["eye_line_frac"], CANON["ipd_over_width"], CANON["nose_base_frac"],
                  CANON["mouth_width_over_width"], CANON["face_w_over_h"])
        rg = json.loads(face_ratios(good))
        ev["good"] = {"ok": rg.get("ok"), "ratios": rg.get("ratios"), "worst": rg.get("worst_rel_err")}
        bad = mk(0.62, 0.30, 0.55, 0.20, 0.60)
        rb = json.loads(face_ratios(bad))
        ev["bad"] = {"ok": rb.get("ok"), "failed": rb.get("failed"), "worst": rb.get("worst_rel_err")}
        cp = json.loads(face_compare(bad, good))
        ev["compare"] = {"ok": cp.get("ok"), "failed": cp.get("failed")}
        ok = (rg.get("ok") is True and rb.get("ok") is False and len(rb.get("failed") or []) >= 3
              and cp.get("ok") is False and len(cp.get("failed") or []) >= 3
              and (rg.get("worst_rel_err") or 1) < 0.01)
        return _j({"ok": bool(ok), "evidence": ev})
    except Exception as e:
        return _j({"ok": False, "evidence": ev, "error": "%s: %s" % (type(e).__name__, str(e)[:200])})


def face_help():
    return _j({
        "module": "faceeval.py", "version": FACE_VERSION,
        "what": "人脸比例门：landmarks -> 6 个无量纲比例 -> 与经典比例/参考的误差与判定",
        "ops": ["face_ratios(landmarks, canon, tol)", "face_compare(attempt, ref, tol_ratio)",
                "face_selftest", "face_help"],
        "landmarks": ["top", "chin", "face_l", "face_r", "eye_l", "eye_r", "nose_base", "mouth_l", "mouth_r"],
        "canon": CANON, "tol": TOL,
        "protocol": ["先跑 5-10 版拿基线（face_ratios 的 failed 项 + worst_rel_err）",
                     "换参数化/参考拟合路径后再跑，比同一组数字",
                     "比例容差按项目钉进 spec.py，别用默认值交付",
                     "轮廓那一半用 img_diff 的 IoU，两者合起来才是完整判据"],
        "limits": ["不内置人脸检测器：landmarks 由调用方给",
                   "经典比例是参考值，不是真理：风格化角色要重钉"],
    })


def face_dispatch(op, args_json):
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
    ops = {"ratios": face_ratios, "compare": face_compare, "selftest": face_selftest, "help": face_help}
    fn = ops.get(str(op))
    if fn is None:
        return _j({"ok": False, "error": "unknown face op", "op": op, "ops": sorted(ops)})
    try:
        return fn(**kw)
    except Exception as e:
        return _j({"ok": False, "error": "%s: %s" % (type(e).__name__, str(e)[:220]), "op": op})


_DshApi = _KIT.Api  # 共享内核（尾部注册行无需改）
import sys as _sys
_K = _sys.modules.get("dsh_rt_kernel")
if _K is not None:
    _K.dsh_face_api = _DshApi({"version": FACE_VERSION, "dispatch": face_dispatch, "ratios": face_ratios,
                               "compare": face_compare, "selftest": face_selftest, "help": face_help})