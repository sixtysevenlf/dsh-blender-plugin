# -*- coding: utf-8 -*-
"""Split from assembly.py; behavior preserved by the facade."""
from .common import *  # noqa: F401,F403
from .manifest import _load_manifest, _manifest_counts, _manifest_md5, _manifest_validate, _obj_part_map
from .scan import _connectivity_float, _manifest_float, _pair_key, _scan_core

def _agg_states(states):
    """整包三态（与 gate v3 同口径）：任一 fail ⇒ fail；否则任一 degraded/skipped ⇒ degraded；否则 pass。"""
    if any(s in ("fail", "error") for s in states):
        return "fail"
    if any(s in ("degraded", "skipped") for s in states):
        return "degraded"
    if not any(s == "pass" for s in states):
        return "degraded"
    return "pass"


def _trim(rec, depth=0):
    """回执瘦身：只留判据字段，明细截短（summary_only 用）。"""
    if not isinstance(rec, dict) or depth > 2:
        return rec
    keep = ("ok", "state", "verdict", "reason", "warn", "error", "counts", "coverage", "totals",
            "clean", "real_floater_count", "visible_floater_count", "unconfirmed_floater_count",
            "micro_floater_count", "floater_count", "manifest_md5", "sealed", "version",
            "trend", "max_depth_mm", "min_gap_mm", "export_allowed", "analyzed")
    out = {}
    for k in keep:
        if k in rec:
            out[k] = _trim(rec[k], depth + 1) if isinstance(rec[k], (dict, list)) else rec[k]
    for k in ("offenders", "floaters", "pairs", "deviations", "objects", "errors", "warnings"):
        v = rec.get(k)
        if isinstance(v, list):
            out[k] = [_trim(x, depth + 1) for x in v[:5]]
            out.setdefault(k + "_total", len(v))
        elif isinstance(v, dict):
            out[k] = _trim(v, depth + 1)
    return out


def _mesh_gate(names, strict):
    rec, err = _call_api("audit", "mesh", {"objects": list(names), "summary_only": True,
                                           "self_intersect": bool(strict)})
    if err:
        return {"state": "degraded", "mode": "audit_mesh_unavailable", "error": err,
                "reason": "mesh 门没跑成（audit 未 preload？）—— 未分析不得读作通过"}
    if not isinstance(rec, dict):
        return {"state": "degraded", "mode": "audit_mesh_bad_receipt", "error": "回执不是 dict"}
    raw = rec.get("state") or ("pass" if rec.get("clean") else "fail")
    state = raw if raw in ("pass", "fail", "degraded") else "degraded"
    return {"state": state, "mode": "audit_mesh", "receipt": _trim(rec),
            "totals": rec.get("totals") or {},
            "objects": [{k: o.get(k) for k in ("name", "state", "normals_state", "normals_outward",
                                              "boundary_edges", "nonmanifold_edges",
                                              "degenerate_faces", "self_intersections", "closed")}
                        for o in (rec.get("objects") or []) if isinstance(o, dict)][:40],
            "reason": rec.get("reason") or ""}


def _normals_gate(mesh_gate):
    """法线门：任一闭合件朝内 ⇒ fail；判不出来（unknown / 没有闭合件）⇒ degraded（绝不假通过）。"""
    if mesh_gate.get("mode") != "audit_mesh":
        return {"state": "degraded", "reason": "mesh 门没拿到 audit_mesh 回执，法线判不了",
                "inverted": [], "unknown": [], "checked": 0}
    totals = mesh_gate.get("totals") or {}
    inv = int(totals.get("normals_inverted") or 0)
    unk = int(totals.get("normals_unknown") or 0)
    chk = int(totals.get("normals_checked") or 0)
    rows = mesh_gate.get("objects") or []
    inverted = [o.get("name") for o in rows if o.get("normals_outward") is False]
    unknown = [o.get("name") for o in rows if o.get("normals_state") == "unknown"]
    if inv:
        return {"state": "fail", "inverted": inverted, "unknown": unknown, "checked": chk,
                "reason": "%d 个对象法线朝内（闭合件 signed volume 为负）—— 穿模/内外判据的前提被破坏" % inv}
    if unk or chk == 0:
        return {"state": "degraded", "inverted": inverted, "unknown": unknown, "checked": chk,
                "reason": "法线判不出来（unknown=%d / checked=%d）—— 未分析不得读作通过" % (unk, chk)}
    return {"state": "pass", "inverted": [], "unknown": [], "checked": chk,
            "reason": "闭合件法线全部朝外（%d 个已判）" % chk}


def _scan_summary(scan):
    s = _trim(scan)
    s["counts"] = scan.get("counts")
    s["coverage"] = scan.get("coverage")
    s["verdict"] = scan.get("verdict")
    s["state"] = scan.get("verdict")
    s["max_depth_mm"] = scan.get("max_depth_mm")
    s["offenders"] = scan.get("offenders") or []
    s["floaters"] = scan.get("floaters") or []
    s["contract"] = scan.get("contract")
    return s


def _measure_gate(m, spec, md, names, mmu, tolerance_mm, contact_eps_mm):
    """measure 门：manifest 接口 + spec.clearance 的期望值 vs 求值网格实测间隙（bbox 值作旁证）。"""
    items = []
    if isinstance(m, dict):
        for it in m.get("interfaces") or []:
            if isinstance(it, dict):
                items.append(("interface", it))
    sp = dict(spec) if isinstance(spec, dict) else {}
    for c in sp.get("clearance") or []:
        if isinstance(c, dict):
            items.append(("clearance", c))
    if not items:
        return {"state": "skipped", "deviations": [],
                "reason": "没有接口/间隙期望（manifest 没 interfaces，spec 没 clearance）—— 未分析不得读作通过"}
    obj_part = _obj_part_map(m, names)
    am, aerr = _call_api("audit", "measure",
                         {"objects": list(names),
                          "max_pairs": int(max(1, min(2000, len(names) * max(1, len(names) - 1) // 2)))})
    am_pairs = {}
    if isinstance(am, dict) and am.get("ok"):
        for p in am.get("pairs") or []:
            if isinstance(p, dict):
                am_pairs[_pair_key(p.get("a"), p.get("b"))] = p
    deviations, n_unres = [], 0

    def _objects_of(side):
        side = str(side or "")
        out = [n for n in names if obj_part.get(n) == side]
        if not out and side in names:
            out = [side]
        return out

    for kind, it in items:
        a, b = str(it.get("a") or ""), str(it.get("b") or "")
        oa, ob = _objects_of(a), _objects_of(b)
        dev = {"kind": kind, "id": it.get("id"), "a": a, "b": b}
        if not oa or not ob:
            dev.update({"state": "unresolved", "why": "端对象不在当前场景（a=%d b=%d 个对象）" % (len(oa), len(ob))})
            deviations.append(dev)
            n_unres += 1
            continue
        best = None
        for na in oa:
            for nb in ob:
                if na in md and nb in md:
                    g = _surface_gap(md[na], md[nb], samples=400)
                    if g is not None:
                        best = g if best is None else min(best, g)
        if best is None:
            dev.update({"state": "unresolved", "why": "两端量不到表面距离"})
            deviations.append(dev)
            n_unres += 1
            continue
        gap_mm = best * mmu
        dp = am_pairs.get(_pair_key(a, b)) or am_pairs.get(_pair_key(oa[0], ob[0])) or {}
        dev["measured_gap_mm"] = _r(gap_mm, 4)
        dev["bbox_gap_mm"] = dp.get("gap_mm")
        if kind == "interface":
            nom = _num(it.get("nominal_mm"))
            nom = 0.0 if nom is None else nom
            tol = _num(it.get("tol_mm"))
            tol = float(tolerance_mm) if tol is None else tol
            ov = _num(it.get("allow_overlap_mm"))
            if ov is not None and ov > 0:
                tol = max(tol, float(ov), float(contact_eps_mm))
            dev.update({"expected_mm": _r(nom, 4), "tol_mm": _r(tol, 4),
                        "deviation_mm": _r(gap_mm - nom, 4)})
            ok = abs(gap_mm - nom) <= tol + 1e-9
            dev["state"] = "pass" if ok else "fail"
            dev["why"] = ("接口 %s 实测单边间隙 %.3f mm，期望 %.3f ± %.3f mm"
                          % (it.get("id"), gap_mm, nom, tol))
        else:
            min_mm = _num(it.get("min_mm")) or 0.0
            dev.update({"min_mm": _r(min_mm, 4), "clearance_mm": _r(gap_mm, 4)})
            ok = gap_mm >= min_mm - 1e-9
            dev["state"] = "pass" if ok else "fail"
            dev["why"] = ("间隙规则 %s 实测 %.3f mm，要求 ≥ %.3f mm"
                          % (it.get("id") or it.get("a"), gap_mm, min_mm))
        deviations.append(dev)
    states = [d.get("state") for d in deviations]
    if "fail" in states:
        state = "fail"
    elif n_unres:
        state = "degraded"
    else:
        state = "pass"
    return {"state": state, "deviations": deviations,
            "audit_measure": {"used": bool(am_pairs), "error": aerr, "pairs_bbox": len(am_pairs)},
            "reason": ("%d 个期望全部在公差内" % len(deviations)) if state == "pass"
                      else ("有 %d 个期望超公差" % states.count("fail") if state == "fail"
                            else "%d 个期望没量成（未判完）" % n_unres)}


def _severity_of_pair(p):
    d = p.get("depth_mm")
    v = p.get("volume_mm3")
    if p.get("class") == "contained":
        return "high" if (v or 0) > 1000 else "medium"
    if d is not None and d > 2.0:
        return "high"
    if (d is not None and d > 0.5) or (v is not None and v > 500):
        return "medium"
    return "low"
# ────────────────────────────── ops：gate ──────────────────────────────
def _asm_gate_p1(_state):
    budget_ms, contact_eps_mm, depth_eps_mm, include_hidden, manifest = (
        _state["budget_ms"], _state["contact_eps_mm"], _state["depth_eps_mm"], _state["include_hidden"], _state["manifest"])
    max_pairs, objects, report_gap_mm, require, scope = (
        _state["max_pairs"], _state["objects"], _state["report_gap_mm"], _state["require"], _state["scope"])
    spec, strict, summary_only, use_contract = (
        _state["spec"], _state["strict"], _state["summary_only"], _state["use_contract"])
    k, merr, mval, norm_g, req, v = (_state.get("k"), _state.get("merr"), _state.get("mval"), _state.get("norm_g"), _state.get("req"), _state.get("v"))
    try:
        t0 = time.perf_counter()
        default_req = {"mesh_health": True, "normals": True, "clash": True, "floaters": True,
                       "interfaces": True, "measure": True}
        if isinstance(require, (list, tuple, set)):
            req = {k: (k in require) for k in default_req}
        elif isinstance(require, dict):
            req = dict(default_req)
            for k, v in require.items():
                if k in default_req:
                    req[k] = bool(v)
                else:
                    return _err("require 里不认识的键: %r" % k, allowed=sorted(default_req))
        else:
            req = dict(default_req)
        m = None
        if manifest is not None:
            m, merr = _load_manifest(manifest)
            if merr:
                return _err(merr)
            mval = _manifest_validate(m, check_files=False, check_md5=False)
            if not mval["ok"]:
                return _err("manifest 结构校验失败，gate 拒绝在坏清单上出结论",
                            errors=mval["errors"][:20], warnings=mval["warnings"][:20])
        info = _pick_objects(scope, objects, include_hidden)
        if info.get("error"):
            return _err(info["error"], objects=info.get("names"), missing=info.get("missing"),
                        hint="空产出/坏参数必须失败；确认对象名/集合名")
        names = list(info["names"])
        mmu = float(_KIT.units())
        md, md_errors = _mesh_map(info["objects"])
        gates = []
        mesh_g = None
        if req["mesh_health"]:
            mesh_g = _mesh_gate(names, bool(strict))
            gates.append({"id": "mesh_health", "state": mesh_g["state"],
                          "reason": mesh_g.get("reason") or "",
                          "receipt": {"mode": mesh_g.get("mode"), "totals": mesh_g.get("totals"),
                                      "error": mesh_g.get("error")} if summary_only else mesh_g})
        else:
            gates.append({"id": "mesh_health", "state": "disabled", "reason": "require.mesh_health=false"})
        if req["normals"]:
            if mesh_g is None:
                mesh_g = _mesh_gate(names, bool(strict))
            norm_g = _normals_gate(mesh_g)
            gates.append({"id": "normals", "state": norm_g["state"], "inverted": norm_g.get("inverted"),
                          "unknown": norm_g.get("unknown"), "checked": norm_g.get("checked"),
                          "reason": norm_g.get("reason")})
        else:
            gates.append({"id": "normals", "state": "disabled", "reason": "require.normals=false"})
        scan = None
        if req["clash"]:
            try:
                scan = _scan_core(info["objects"], names, m, spec, 0, max_pairs, float(depth_eps_mm),
                                  float(contact_eps_mm), float(report_gap_mm), 1.0, True,
                                  bool(use_contract), bool(include_hidden), md, int(budget_ms))
            except Exception as e:
                scan = {"ok": False, "verdict": "degraded", "error": "%s: %s" % (type(e).__name__, str(e)[:200])}
            st = scan.get("verdict") if scan.get("ok") else "degraded"
            if st not in ("pass", "fail", "degraded"):
                st = "degraded"
            gates.append({"id": "clash_all", "state": st,
                          "reason": scan.get("verdict_line") or scan.get("error"),
                          "receipt": _scan_summary(scan) if scan.get("ok") else _trim(scan)})
        else:
            gates.append({"id": "clash_all", "state": "disabled", "reason": "require.clash=false"})
        mfloat = None
    finally:
        _state.update((k, v) for k, v in locals().items() if k != "_state")

def _asm_gate_p2(_state):
    contact_eps_mm, gates, include_hidden, m, md = (
        _state["contact_eps_mm"], _state.get("gates"), _state["include_hidden"], _state.get("m"), _state.get("md"))
    mmu, names, req, spec, tolerance_mm = (
        _state.get("mmu"), _state.get("names"), _state.get("req"), _state["spec"], _state["tolerance_mm"])
    cf, mfloat, mg, rows, states = (_state.get("cf"), _state.get("mfloat"), _state.get("mg"), _state.get("rows"), _state.get("states"))
    try:
        if req["floaters"]:
            if isinstance(m, dict):
                mfloat = _manifest_float(m, md, names, mmu, float(contact_eps_mm))
                st = ("fail" if mfloat["floaters"] else
                      ("degraded" if mfloat["unresolved"] else "pass"))
                gates.append({"id": "floaters", "state": st,
                              "reason": "manifest 连接图：%d 个浮空/接口未接触，%d 个未判"
                                        % (len(mfloat["floaters"]), len(mfloat["unresolved"])),
                              "floaters": mfloat["floaters"][:10],
                              "unresolved": mfloat["unresolved"][:10],
                              "graph": mfloat.get("graph")})
            else:
                cf = _connectivity_float(names, float(contact_eps_mm), bool(include_hidden))
                st = cf.get("state") or ("fail" if cf["floaters"] else
                                         ("degraded" if cf["unresolved"] else "pass"))
                gates.append({"id": "floaters", "state": st,
                              "reason": "audit_connectivity（单件口径）：%d 个浮块，%d 个未判"
                                        % (len(cf["floaters"]), len(cf["unresolved"])),
                              "floaters": cf["floaters"][:10],
                              "unresolved": cf["unresolved"][:10],
                              "receipt": cf.get("receipt"),
                              "note": cf.get("note")})
        else:
            gates.append({"id": "floaters", "state": "disabled", "reason": "require.floaters=false"})
        if req["interfaces"]:
            if isinstance(m, dict) and (m.get("interfaces") or []):
                if mfloat is None:
                    mfloat = _manifest_float(m, md, names, mmu, float(contact_eps_mm))
                rows = mfloat.get("interfaces") or []
                states = [r.get("state") for r in rows]
                if "float" in states:
                    st = "fail"
                elif any(s == "unresolved" for s in states) or mfloat.get("unresolved"):
                    st = "degraded"
                elif states and all(s == "contact" for s in states):
                    st = "pass"
                else:
                    st = "degraded"
                gates.append({"id": "interfaces", "state": st,
                              "reason": "%d 条接口：contact=%d float=%d unresolved=%d"
                                        % (len(rows), states.count("contact"), states.count("float"),
                                           states.count("unresolved") + len(mfloat.get("unresolved") or [])),
                              "pairs": rows[:20]})
            else:
                gates.append({"id": "interfaces", "state": "skipped",
                              "reason": "manifest 没有 interfaces —— 接口配合未验证（未分析不得读作通过）"})
        else:
            gates.append({"id": "interfaces", "state": "disabled", "reason": "require.interfaces=false"})
        if req["measure"]:
            try:
                mg = _measure_gate(m, spec, md, names, mmu, float(tolerance_mm), float(contact_eps_mm))
            except Exception as e:
                mg = {"state": "degraded", "deviations": [],
                      "reason": "measure 异常: %s: %s" % (type(e).__name__, str(e)[:160])}
            gates.append({"id": "measure", "state": mg["state"], "reason": mg.get("reason"),
                          "deviations": mg.get("deviations")[:20],
                          "receipt": {"audit_measure": mg.get("audit_measure")}})
        else:
            gates.append({"id": "measure", "state": "disabled", "reason": "require.measure=false"})
        enabled = [g for g in gates if g.get("state") != "disabled"]
        verdict = _agg_states([g.get("state") for g in enabled])
        if not enabled:
            verdict = "degraded"
        offenders, repairs = [], []
        trend = {"clash": 0, "contained": 0, "hidden_ok": 0, "contact": 0, "gap": 0,
                 "unresolved": 0, "float": 0, "max_depth_mm": None}
    finally:
        _state.update((k, v) for k, v in locals().items() if k != "_state")

def _asm_gate_p3(_state):
    enabled, gates, info, m, md_errors = (
        _state.get("enabled"), _state.get("gates"), _state.get("info"), _state.get("m"), _state.get("md_errors"))
    mesh_g, mfloat, names, offenders, repairs = (
        _state.get("mesh_g"), _state.get("mfloat"), _state.get("names"), _state.get("offenders"), _state.get("repairs"))
    req, scan, shots, strict, t0 = (
        _state.get("req"), _state.get("scan"), _state["shots"], _state["strict"], _state.get("t0"))
    trend, verdict = _state.get("trend"), _state.get("verdict")
    counts, d, f, k, nm, o, p, u = (_state.get("counts"), _state.get("d"), _state.get("f"), _state.get("k"), _state.get("nm"), _state.get("o"), _state.get("p"), _state.get("u"))
    try:
        if scan and scan.get("ok"):
            counts = scan.get("counts") or {}
            for k in ("clash", "contained", "hidden_ok", "contact", "gap", "unresolved", "float"):
                trend[k] = int(counts.get(k) or 0)
            trend["max_depth_mm"] = scan.get("max_depth_mm")
            for p in scan.get("offenders") or []:
                offenders.append({"kind": "interference", "a": p.get("a"), "b": p.get("b"),
                                  "class": p.get("class"), "severity": _severity_of_pair(p),
                                  "depth_mm": p.get("depth_mm"), "volume_mm3": p.get("volume_mm3"),
                                  "spec_ref": p.get("spec_ref"), "repair": "fix_interference",
                                  "hint": "两件按 spec 复核：完全包含/超深穿模要用布尔差或调接口帧"})
                repairs.append({"op": "fix_interference",
                                "args": {"a": p.get("a"), "b": p.get("b"), "plan_only": True},
                                "why": p.get("reason") or "干涉"})
            for f in scan.get("floaters") or []:
                offenders.append({"kind": "floater", "object": f.get("object"),
                                  "objects": f.get("objects"), "reason": f.get("reason"),
                                  "interface": f.get("interface"), "mesh_gap_mm": f.get("mesh_gap_mm")})
            for u in scan.get("floating_unresolved") or []:
                offenders.append({"kind": "floater_unresolved", "reason": u.get("reason"),
                                  "why": u.get("why")})
        if mfloat is not None:
            for f in mfloat.get("floaters") or []:
                offenders.append({"kind": "floater", "object": f.get("object"), "part": f.get("part"),
                                  "reason": f.get("reason"), "interface": f.get("interface"),
                                  "mesh_gap_mm": f.get("mesh_gap_mm"), "why": f.get("why")})
                repairs.append({"op": "asm_manifest", "args": {"action": "add_interface"},
                                "why": f.get("why") or "接口/连接图问题"})
        if mesh_g is not None and mesh_g.get("state") == "fail":
            for o in mesh_g.get("objects") or []:
                if o.get("state") == "fail":
                    offenders.append({"kind": "mesh", "object": o.get("name"),
                                      "boundary_edges": o.get("boundary_edges"),
                                      "nonmanifold_edges": o.get("nonmanifold_edges"),
                                      "degenerate_faces": o.get("degenerate_faces"),
                                      "self_intersections": o.get("self_intersections")})
                    repairs.append({"op": "fix_repair", "args": {"objects": [o.get("name")]},
                                    "why": "网格不干净（破洞/非流形/自交），先修再谈装配"})
        norm_gate = next((g for g in gates if g.get("id") == "normals"), None)
        for nm in ((norm_gate or {}).get("inverted") or []):
            offenders.append({"kind": "normals", "object": nm, "reason": "法线朝内"})
            repairs.append({"op": "fix_repair", "args": {"objects": [nm], "recalc_normals": True},
                            "why": "法线朝内会让内外/穿模判据反转"})
        mg_gate = next((g for g in gates if g.get("id") == "measure"), None)
        for d in ((mg_gate or {}).get("deviations") or []):
            if d.get("state") == "fail":
                offenders.append({"kind": "measure", "id": d.get("id"), "a": d.get("a"),
                                  "b": d.get("b"), "deviation_mm": d.get("deviation_mm"),
                                  "expected_mm": d.get("expected_mm"), "tol_mm": d.get("tol_mm"),
                                  "measured_gap_mm": d.get("measured_gap_mm")})
                repairs.append({"op": "asm_merge", "args": {"dry_run": True, "transform_policy": "snap_interface"},
                                "why": "接口间隙超公差：复核 manifest transform / 接口帧，再合并"})
        coverage = {"gates": len(gates), "enabled": len(enabled),
                    "ran": sum(1 for g in enabled if g.get("state") in ("pass", "fail", "degraded")),
                    "disabled": len(gates) - len(enabled),
                    "skipped": sum(1 for g in enabled if g.get("state") == "skipped"),
                    "complete": all(g.get("state") not in ("skipped",) for g in enabled)}
        manifest_summary = None
        if isinstance(m, dict):
            manifest_summary = {"assembly": m.get("assembly"), "sealed": bool(m.get("sealed")),
                                "manifest_md5": _manifest_md5(m), "counts": _manifest_counts(m)}
        shots_block = {"state": "skipped", "shots": [],
                       "note": "v1 不出图（shots 先 false）；数字结论以 audit_clash_all/measure 为准"}
        if shots:
            shots_block["warn"] = "shots=true 但本版未实现穿透特写出图（接 qc_render_views）"
        verdict_line = ("装配门 %s（export_allowed=%s）：%s"
                        % (verdict, verdict == "pass",
                           " · ".join("%s=%s" % (g["id"], g["state"]) for g in gates)))
        body = {"ok": True, "verdict": verdict, "analyzed": True, "export_allowed": bool(verdict == "pass"),
                "strict": bool(strict), "gates": gates, "trend": trend, "offenders": offenders[:20],
                "repairs": repairs[:10], "coverage": coverage, "require": req,
                "manifest": manifest_summary, "shots": shots_block,
                "verdict_line": verdict_line,
                "note": "export_allowed 仅 verdict=pass 为 true；degraded=没查完/未确认，不是通过",
                "honest_limits": [
                    "mesh 门用 audit_mesh 求值网格；scan 用同一套求值网格，但两者对多壳法线口径不同",
                    "没有 manifest 时浮空退 audit_connectivity（单件 1% 可见 + micro_gap 口径），装配件可能误判/漏判",
                    "measure 用表面采样最近距离（不是接触面积），bbox 值只作旁证",
                    "本版不出穿透特写图（shots 恒 skipped），图只作证据、结论仍以数字为准",
                ],
                "trace": {"objects": len(names), "md_errors": md_errors,
                          "hidden_skipped": info.get("hidden_skipped", 0)}}
        body["receipt_md5"] = _md5_text(_canon({k: v for k, v in body.items()
                                                if k not in ("ms", "receipt_md5", "trace")}))
        return _j(_receipt(body, t0))
    finally:
        _state.update((k, v) for k, v in locals().items() if k != "_state")

def asm_gate(scope=None, objects=None, manifest=None, spec=None, require=None,
             summary_only=True, shots=False, strict=True, depth_eps_mm=0.2,
             contact_eps_mm=0.3, report_gap_mm=5.0, max_pairs=20000,
             tolerance_mm=0.5, include_hidden=False, use_contract=True, budget_ms=0):
    """装配出厂门：mesh + normals + scan(clash/contained) + floaters + interfaces + measure → 三态。

    export_allowed 仅当 verdict=pass；任何 skipped/degraded/unresolved 都不算通过。
    """
    _state = dict(locals())
    for _fn_ in (_asm_gate_p1, _asm_gate_p2, _asm_gate_p3):
        _r = _fn_(_state)
        if _r is not None:
            return _r
