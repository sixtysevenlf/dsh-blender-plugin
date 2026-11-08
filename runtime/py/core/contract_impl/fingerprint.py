# -*- coding: utf-8 -*-
"""Split from contract.py; behavior preserved by the facade."""
from .common import *  # noqa: F401,F403


# ---------------------------------------------------------------- 几何指纹 / 判定漂移（v0.9.0）

def _r4(x):
    try:
        return round(float(x), 4)
    except Exception:
        return x


def _fingerprint(objects=None, scope=None, include_hidden=False):
    """几何指纹（轻量）：对象数 / 三角面数 / 顶点数 / 世界 bbox + 12 位摘要。

    为什么不用全网格 md5：大场景算一次要几秒；而"改过 / 挪过 / 换过件"这三件事用
    面数+点数+bbox 已经够发现（顶点级的细微漂移交给 audit_drift 的 Chamfer 距离）。
    证据与判定都绑这个摘要 —— 源一变，旧结论就该失效（Procedura state.ts 的 staleness 纪律）。
    """
    import hashlib
    if objects:
        want = [str(x) for x in (objects if isinstance(objects, (list, tuple)) else [objects])]
        objs = [bpy.data.objects.get(n) for n in want]
        objs = [o for o in objs if o is not None and o.type == "MESH"]
    else:
        objs = _scope_objects(scope, include_hidden)
    lines, mn, mx, tris, verts = [], None, None, 0, 0
    for ob in sorted(objs, key=lambda o: o.name):
        t, v = len(ob.data.polygons), len(ob.data.vertices)
        tris += t
        verts += v
        (lo, hi) = _bbox(ob)
        lo, hi = [_r4(x) for x in lo], [_r4(x) for x in hi]
        if mn is None:
            mn, mx = list(lo), list(hi)
        else:
            for k in range(3):
                mn[k] = min(mn[k], lo[k])
                mx[k] = max(mx[k], hi[k])
        lines.append("%s:%d:%d:%.4f,%.4f,%.4f:%.4f,%.4f,%.4f" % (ob.name, t, v, lo[0], lo[1], lo[2], hi[0], hi[1], hi[2]))
    return {"objects": len(objs), "tris": int(tris), "verts": int(verts),
            "bbox": {"min": mn, "max": mx},
            "digest": hashlib.md5("|".join(sorted(lines)).encode("utf-8")).hexdigest()[:12] if lines else "empty",
            "scope": scope, "object_names": [o.name for o in objs][:200],
            "rule": "面数/点数/世界 bbox 变了 → 摘要变 → 绑它的证据与判定视为过期"}


def c_fingerprint(objects=None, scope=None, include_hidden=False):
    """取当前指纹（改动前先记一份，改完再取一份对比即可判定"有没有动过"）。"""
    fp = _fingerprint(objects, scope, include_hidden)
    return _j({"ok": True, "fingerprint": fp,
               "usage": "改前先 fingerprint → 改后再 fingerprint：digest 不同即「源已变」；"
                        "也可以让 ledger 自动帮你比（evidence/verify 都绑了指纹）"})


def _conn_objects(conn):
    return _component_objects(conn["a"]) + _component_objects(conn["b"])


def _union_bbox(objs):
    mn = mx = None
    for ob in objs:
        (lo, hi) = _bbox(ob)
        if mn is None:
            mn, mx = list(lo), list(hi)
        else:
            for k in range(3):
                mn[k] = min(mn[k], lo[k])
                mx[k] = max(mx[k], hi[k])
    return mn, mx


def _snapshot_of(objs):
    mn, mx = _union_bbox(objs)
    if mn is None:
        return None
    size = [float(mx[k] - mn[k]) for k in range(3)]
    import math as _m
    return {"min": [_r4(x) for x in mn], "max": [_r4(x) for x in mx],
            "size": [_r4(x) for x in size], "diagonal": _r4(_m.sqrt(sum(s * s for s in size))),
            "t": _now()}


def _drift_of(conn):
    """与 verify 时记的 bbox 快照比 → (drifted, detail)。跳过的情形都写清理由，不静默。"""
    vd = conn.get("verdict") or {}
    snap = vd.get("bbox_snapshot")
    if not snap:
        return False, {"why": "旧判定没有 bbox 快照 → 不降级（建议重跑 verify 建立快照）"}
    objs = _conn_objects(conn)
    if not objs:
        return False, {"why": "连接涉及的对象已不存在", "objects": 0}
    if any(getattr(ob, "animation_data", None) is not None for ob in objs):
        return False, {"why": "有动画数据：位置由动画驱动，跳过漂移降级"}
    cur = _snapshot_of(objs)
    if cur is None:
        return False, {"why": "对象没有几何（0 面）"}
    diag = float(snap.get("diagonal") or 0) or 1.0
    c0 = [(snap["min"][k] + snap["max"][k]) / 2.0 for k in range(3)]
    c1 = [(cur["min"][k] + cur["max"][k]) / 2.0 for k in range(3)]
    moved = sum((c1[k] - c0[k]) ** 2 for k in range(3)) ** 0.5
    dsz = [abs(float(cur["size"][k]) - float(snap["size"][k])) for k in range(3)]
    size_bad = [k for k in range(3) if dsz[k] > DRIFT_SIZE_FRACTION * max(float(cur["size"][k]), 0.05 * diag)]
    drifted = (moved > DRIFT_CENTER_FRACTION * diag) or bool(size_bad)
    return drifted, {"why": "中心漂移 %.4f（门限 %.4f=对角线 %.0f%%）%s" % (
        moved, DRIFT_CENTER_FRACTION * diag, DRIFT_CENTER_FRACTION * 100,
        ("；尺寸漂移 %s（门限 %.0f%%）" % (["%s %.4f" % ("xyz"[k], dsz[k]) for k in size_bad],
                                        DRIFT_SIZE_FRACTION * 100)) if size_bad else ""),
        "center_moved": _r4(moved), "center_limit": _r4(DRIFT_CENTER_FRACTION * diag),
        "size_delta": [_r4(x) for x in dsz], "snapshot_t": snap.get("t"), "now_t": cur.get("t")}


def _demote_if_drifted(conn, cid):
    """几何漂移 → 把 supported 判定降级为 unresolved（保留旧判定，记历史）。"""
    if conn.get("status") not in ("supported",):
        return False
    drifted, detail = _drift_of(conn)
    if not drifted:
        return False
    conn["history"].append({"t": _now(), "status": "unresolved",
                            "why": "几何漂移 → 判定自动降级：%s" % detail.get("why")})
    vd = conn.get("verdict") or {}
    vd["demoted"] = {"t": _now(), "reason": detail}
    conn["verdict"] = vd
    conn["status"] = "unresolved"
    _log("demote", {"id": cid, "detail": detail})
    return True


# ---------------------------------------------------------------- 证据

def c_evidence(label, view=None, note="", objects=None, scope=None):
    K = _kernel()
    api = getattr(K, "dsh_view_api", None)
    if api is None:
        return _j({"ok": False, "error": "需要 view.py：先 preload/注入 K.dsh_view_api（引擎里 ensureView）"})
    import hashlib, os
    spec = dict(view or {})
    path = spec.get("path")
    if not path:
        return _j({"ok": False, "error": "view.path 必填（引擎会带默认工作目录）"})
    info = json.loads(api["capture"](json.dumps(spec)))
    if not info.get("ok"):
        return _j({"ok": False, "error": info})
    h = hashlib.md5(open(path, "rb").read()).hexdigest()
    # v0.9.0：证据绑几何指纹 —— 源一改，这条证据自动被 ledger 判 stale
    fp = _fingerprint(objects, scope)
    fp_objs = ([str(x) for x in (objects if isinstance(objects, (list, tuple)) else [objects])]
               if objects else None)
    rec = {"label": str(label), "path": path, "md5": h, "bytes": os.path.getsize(path),
           "width": info.get("width"), "height": info.get("height"),
           "view": {k: spec.get(k) for k in ("from", "look_at", "lens", "ortho", "ortho_scale", "shading")},
           "fp": fp, "fp_objects": fp_objs, "note": str(note), "t": _now()}
    _store()["evidence"].append(rec)
    _log("evidence", {"label": label, "md5": h, "fp": fp.get("digest")})
    return _j({"ok": True, "evidence": rec, "total": len(_store()["evidence"])})


def c_ledger(with_stale=True):
    """证据账本。with_stale=True（默认）时逐条与当前几何指纹对拍：源变过的证据标 stale=true。"""
    st = _store()
    out = {"ok": True, "evidence": [], "stale_evidence": 0, "stale_labels": [],
           "demoted_connections": [], "checked": bool(with_stale)}
    cache = {}

    def _cur_global():
        if "scene" not in cache:
            cache["scene"] = _fingerprint(None, None)
        return cache["scene"]
    for e in st["evidence"]:
        rec = dict(e)
        if with_stale and e.get("fp"):
            names = e.get("fp_objects")
            key = "scene" if not names else ",".join(names)
            if key not in cache:
                cache[key] = _cur_global() if not names else _fingerprint(names, None)
            rec["stale"] = (cache[key].get("digest") != e["fp"].get("digest"))
            rec["fp_now"] = cache[key].get("digest")
            if rec["stale"]:
                out["stale_evidence"] += 1
                out["stale_labels"].append(e.get("label"))
        out["evidence"].append(rec)
    for cid, conn in st["connections"].items():
        if _demote_if_drifted(conn, cid):
            out["demoted_connections"].append(cid)
    return _j(out)


def c_status():
    st = _store()
    demoted = []
    for k, v in st["connections"].items():
        if _demote_if_drifted(v, k):
            demoted.append(k)
    conns = {k: {"a": v["a"], "b": v["b"], "status": v["status"], "candidates": v["candidates"],
                 "forbidden": v["forbidden"], "verdict": v["verdict"],
                 "history_len": len(v["history"])} for k, v in st["connections"].items()}
    unresolved = [k for k, v in st["connections"].items() if v["status"] != "supported"]
    return _j({"ok": True, "components": len(st["components"]), "connections": len(st["connections"]),
               "envelopes": len(st["envelopes"]), "evidence": len(st["evidence"]),
               "unresolved_connections": unresolved, "detail": conns,
               "demoted_now": demoted,
               "note": "demoted_now = 本次读取时因几何漂移（中心>10%对角线 或 尺寸>20%）被自动降级的连接"})


# ----------------------------------------------------------------：三态判定与翻转

def c_verify(conn_id, err=None, tolerance=None, identifiable=None, probe_err=None,
             probe_tolerance=None, evidence_labels=None, note=""):
    """三态判定（cookbook §5）：
         err <= tol 且关键参数可辨识          → supported
         err >  tol                            → refuted
         err <= tol 但关键参数不可辨识/多假设   → unresolved（并列出需要什么探针）
       probe_err 给定时：probe_err <= probe_tolerance → 支持；否则推翻外部结论。
    """
    st = _store()
    conn = st["connections"].get(str(conn_id))
    if not conn:
        return _j({"ok": False, "error": "连接未注册: %s" % conn_id})
    ident = list(identifiable or [])
    missing = [p for p in (conn.get("params") or {}) if p not in ident]
    verdict = None; why = []
    if err is not None and tolerance is not None:
        if float(err) > float(tolerance):
            verdict = "refuted"; why.append("外部误差 %s > 容差 %s" % (err, tolerance))
        elif missing:
            verdict = "unresolved"
            why.append("外部误差 %s <= %s 但参数不可辨识: %s（需要探针）" % (err, tolerance, ", ".join(missing)))
        else:
            verdict = "supported"; why.append("外部误差 %s <= %s 且参数可辨识" % (err, tolerance))
    if probe_err is not None and probe_tolerance is not None:
        if float(probe_err) <= float(probe_tolerance):
            verdict = "supported"; why.append("探针误差 %s <= %s → 支持" % (probe_err, probe_tolerance))
        else:
            verdict = "refuted"; why.append("探针误差 %s > %s → 推翻" % (probe_err, probe_tolerance))
    if verdict is None:
        return _j({"ok": False, "error": "至少要给 err+tolerance 或 probe_err+probe_tolerance"})
    ev = [{"label": e["label"], "md5": e["md5"], "path": e["path"]} for e in st["evidence"]
          if not evidence_labels or e["label"] in evidence_labels]
    # v0.9.0：判定绑 bbox 快照 → 之后几何漂移（中心>10%对角线 / 尺寸>20%）会自动降级为 unresolved
    drifted, drift_detail = _drift_of(conn)
    if drifted:
        why.append("注意：上一版判定的几何已漂移（%s）→ 本次为重判" % drift_detail.get("why"))
    objs = _conn_objects(conn)
    snap = _snapshot_of(objs)
    conn["status"] = verdict
    conn["verdict"] = {"verdict": verdict, "why": why, "err": err, "tolerance": tolerance,
                       "identifiable": ident, "unidentifiable": missing,
                       "probe_err": probe_err, "probe_tolerance": probe_tolerance,
                       "evidence": ev, "bbox_snapshot": snap,
                       "objects": [o.name for o in objs], "drift_of_previous": drift_detail if drifted else None,
                       "note": str(note), "t": _now()}
    conn["history"].append({"t": _now(), "status": verdict, "why": why})
    _log("verify", {"id": conn_id, "verdict": verdict})
    return _j({"ok": True, "connection": conn_id, "verdict": verdict, "why": why,
               "unidentifiable_params": missing, "evidence_used": len(ev),
               "bbox_snapshot": snap,
               "drift_guard": {"center_fraction": DRIFT_CENTER_FRACTION, "size_fraction": DRIFT_SIZE_FRACTION,
                               "note": "之后若几何漂移超门限，status 会在 status/ledger 读取时自动降级为 unresolved，"
                                       "并在 verdict.demoted 里留理由（有动画数据的对象跳过降级）"}})


def c_flip(conn_id, to, why=""):
    """假设翻转：把连接切到另一个候选类型，保留历史（不删旧判定）。"""
    st = _store()
    conn = st["connections"].get(str(conn_id))
    if not conn:
        return _j({"ok": False, "error": "连接未注册: %s" % conn_id})
    if conn["candidates"] and to not in conn["candidates"]:
        return _j({"ok": False, "error": "候选里没有: %s" % to, "candidates": conn["candidates"]})
    conn["history"].append({"t": _now(), "flip_from": conn.get("flipped_to"), "status": conn.get("status"),
                            "why": "flip -> %s：%s" % (to, why)})
    conn["flipped_to"] = str(to)
    conn["status"] = "testing"
    conn["verdict"] = None
    _log("flip", {"id": conn_id, "to": to, "why": why})
    return _j({"ok": True, "connection": conn_id, "now": to, "status": "testing", "history_len": len(conn["history"])})


def c_advance(conn_id, status, why=""):
    st = _store()
    conn = st["connections"].get(str(conn_id))
    if not conn:
        return _j({"ok": False, "error": "连接未注册: %s" % conn_id})
    if status not in ("proposed", "testing", "supported", "refuted"):
        return _j({"ok": False, "error": "非法状态", "allowed": ["proposed", "testing", "supported", "refuted"]})
    conn["status"] = status
    conn["history"].append({"t": _now(), "status": status, "why": why})
    _log("advance", {"id": conn_id, "status": status})
    return _j({"ok": True, "connection": conn_id, "status": status})


def c_report(path=None):
    """生成 Markdown 报告：假设/区间/判定/证据/复现命令（provenance 表）。"""
    st = _store()
    L = ["# 契约与假设报告（自动生成）", "", "> 生成时间：%s" % _now(), "",
         "## 组件（%d）" % len(st["components"]), "", "| 组件 | 对象 | 标签 | 缺失对象 |", "|---|---|---|---|"]
    for k, v in st["components"].items():
        L.append("| %s | %s | %s | %s |" % (k, ", ".join(v["objects"]) or "-", ", ".join(v["tags"]) or "-",
                                            ", ".join(v["missing_objects"]) or "-"))
    L += ["", "## 连接与判定（%d）" % len(st["connections"]), "",
          "| 连接 | a → b | 候选 | 禁止项 | 状态 | 判定依据 |", "|---|---|---|---|---|---|"]
    for k, v in st["connections"].items():
        vd = v.get("verdict") or {}
        why = "；".join(vd.get("why") or []) or "-"
        extra = ""
        if vd.get("unidentifiable"):
            extra = "（不可辨识：%s）" % ", ".join(vd["unidentifiable"])
        L.append("| %s | %s → %s | %s | %s | **%s** | %s%s |" % (
            k, v["a"], v["b"], ", ".join(v["candidates"]) or "-", ", ".join(v["forbidden"]) or "-",
            v["status"], why, extra))
    if st["envelopes"]:
        L += ["", "## 包络", "", "| 包络 | min | max | 作用域 |", "|---|---|---|---|"]
        for k, v in st["envelopes"].items():
            L.append("| %s | %s | %s | %s |" % (k, v["min"], v["max"], ", ".join(v["scope"]) or "(全部)"))
    L += ["", "## 证据账本（%d）" % len(st["evidence"]), "",
          "| 标签 | 文件 | md5 | 尺寸 |", "|---|---|---|---|"]
    for e in st["evidence"]:
        L.append("| %s | %s | %s | %sx%s |" % (e["label"], e["path"], e["md5"], e.get("width"), e.get("height")))
    L += ["", "## 历史", ""]
    for r in st["log"][-30:]:
        L.append("- %s %s %s" % (r["t"], r["action"], json.dumps(r["detail"], ensure_ascii=False)[:200]))
    L += ["", "## 复现", "", "    该报告由契约层生成：blender_op(op=\"report\")",
          "    假设/区间/探针流程见 docs/假设驱动建模-cookbook.md", ""]
    text = "\n".join(L) + "\n"
    if path:
        try:
            with open(path, "w", encoding="utf-8") as f:
                f.write(text)
            return _j({"ok": True, "path": path, "bytes": len(text.encode("utf-8")), "preview": text[:600]})
        except Exception as e:
            return _j({"ok": False, "error": str(e), "preview": text[:600]})
    return _j({"ok": True, "path": None, "text": text})


# ============================================================================
# v0.9.0：装配级判据（配合门 / 装配特征库 / 干涉报告）
# ----------------------------------------------------------------------------
# 来源：SpatiaOS/Procedura（MIT）
#   * src/motion/geometry.ts —— analyzeContactRegion / analyzeMateRegistration / computeMeshVolume
#   * OpenSCAD lib/assembly.scad —— 9 个配合特征 + asm_fit 四档 + asm_lead + ISO 273 间隙孔
#
# 为什么把 check_interface 从 bbox 级升级到网格级（本次改动的全部动机）：
#   bbox 只看包围盒。"销插进孔"这种配合两端 bbox 必然重叠 → bbox 口径永远读成"重叠、0 缝"，
#   既分不出 0.15 mm 的 location 配合，也看不出销已经压进母材 3 mm。所以改成：
#     A 侧按**面积加权**采样 → 对 B 的**曲面**求最近点 → 距离场量间隙、射线奇偶判内外。
#
# 三条与上游同口径的不变量（改之前先读这里的理由）：
#   * 接触距离 contact_max = 1.5% × 两件**合并** bbox 对角线（不是各自的对角线之和）
#   * 采样硬上限 4000 点；接触点 < 8 点 → 证据不足 → unresolved
#   * 接触面积占比双向各算一次取**较大**者（小件贴在大件上时，只有小件那侧的读数有意义：
#     大件表面积大，它的"接触占比"天然很小，取最大值相当于按小件归一化 —— 上游注释原文）
