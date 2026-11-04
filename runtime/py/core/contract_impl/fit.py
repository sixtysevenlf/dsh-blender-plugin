# -*- coding: utf-8 -*-
"""Split from contract.py; behavior preserved by the facade."""
from .common import *  # noqa: F401,F403
from .fingerprint import _r4
from .mate import _aabb_pairs, _bvh, _fit_value, _inside_mask, _mesh_data, _nearest, _overlap_estimate, _pt_tri_dist, _r6, _sample_surface, _units, _units_block

def _features_path():
    """定位 assembly_features.json（位于 runtime/py/assembly/，旧扁平布局仍兼容）。

    Path lookup prefers K.runtime_dir / find_runtime_file, then falls back to
    the module directory, the sibling assembly package, and ../runtime (for
    direct tests). All supported loaders provide the real module path; a
    missing __file__ is treated as an injection contract violation, not masked.
    """
    try:
        p = _KIT.find_runtime_file(FEATURES_JSON_NAME)
        if p:
            return p
    except Exception:
        pass
    cands = []
    if _HERE:
        cands.append(_HERE)
        cands.append(os.path.join(os.path.dirname(_HERE), "assembly"))
        cands.append(os.path.join(os.path.dirname(_HERE), "runtime"))
        cands.append(os.path.join(os.path.dirname(os.path.dirname(_HERE)), "runtime"))
    K = _kernel()
    rd = getattr(K, "runtime_dir", None) if K is not None else None
    if rd:
        cands.insert(0, str(rd))
    for c in cands:
        try:
            if c and os.path.isfile(os.path.join(c, FEATURES_JSON_NAME)):
                return os.path.join(c, FEATURES_JSON_NAME)
        except Exception:
            continue
    return FEATURES_JSON if cands else FEATURES_JSON_NAME


def c_fit_help(fit=None, feature=None, m=None, d=None):
    """装配特征库速查（读装配包 assembly_features.json，runtime/py/assembly/）。

    为什么做成数据文件而不是把数字抄进代码：配合件对不上的头号原因不是公差选错，而是公母两处各写了
    一个字面量（改一处忘一处）。mate_check / fit_help / 生成器读**同一份**数值，才不会各自漂移。
    返回里每个特征都带「Blender 侧建议怎么造」（cylinder/cube + boolean 的对应关系）。
    """
    path = _features_path()
    if not os.path.isfile(path):
        return _j({"ok": False, "error": "特征库文件不存在: %s" % path,
                   "tried": [FEATURES_JSON, "K.runtime_dir"], "file": FEATURES_JSON_NAME,
                   "hint": "assembly_features.json 应位于 runtime/py/assembly/（或与 contract.py 同目录的旧布局）"})
    try:
        with open(path, encoding="utf-8") as f:
            lib = json.load(f)
    except Exception as e:
        return _j({"ok": False, "error": "特征库读取失败: %s" % e, "path": path})
    out = {"ok": True, "path": path, "schema": lib.get("schema"), "units": lib.get("units"), "schema": lib.get("schema"), "units": lib.get("units"),
           "shared_nominal_rule": lib.get("shared_nominal_rule"),
           "fits": lib.get("fits"), "lead_chamfer": lib.get("lead_chamfer"),
           "iso273_clearance_diameter": lib.get("iso273_clearance_diameter"),
           "fit_default_cls": lib.get("fit_default_cls"),
           "features_index": [{"name": x.get("name"), "kind": x.get("kind"), "cn": x.get("cn"),
                               "pairs_with": x.get("pairs_with"), "blender": x.get("blender")}
                              for x in (lib.get("features") or [])],
           "blender_general": lib.get("blender_general"),
           "source": lib.get("source")}
    if m is not None:
        try:
            mm_ = float(m)
        except Exception:
            return _j({"ok": False, "error": "m 必须是数字（公制螺纹标称直径 mm）", "m": m})
        br = lib.get("iso273_clearance_diameter", {}).get("branches") or []
        off = None
        for b in br:
            if b.get("m_le") is None or mm_ <= float(b["m_le"]):
                off = float(b["offset_mm"]); break
        out["iso273_query"] = {"m": mm_, "offset_mm": off, "clearance_diameter_mm": (mm_ + off) if off is not None else None,
                               "formula": lib.get("iso273_clearance_diameter", {}).get("formula"),
                               "blender": "cutter = primitive_cylinder_add(radius=clearance_d/2, depth=depth) + cone 入口漏斗（lead=0.6）→ BOOLEAN/DIFFERENCE"}
    if d is not None:
        try:
            dd = float(d)
        except Exception:
            return _j({"ok": False, "error": "d 必须是数字（被倒角处的直径 mm）", "d": d})
        out["lead_query"] = {"d": dd, "lead_mm": round(max(0.6, 0.15 * dd), 4),
                             "formula": "asm_lead(d) = max(0.6, 0.15*d)",
                             "blender": "公件尖端 cone(radius1=d/2, radius2=max(0.2,d-2*lead)/2, depth=lead)；母件口部 cone(radius1=bore/2, radius2=(bore+2*lead)/2)"}
    if fit is not None:
        v, note = _fit_value(fit)
        out["fit_query"] = {"class": str(fit), "per_side_mm": v, "note": note,
                            "bore_rule": "母件孔径 = nominal + 2×%.2f mm（单边 %.2f）" % (v, v),
                            "mate_check_usage": "mate_check(conn_id, fit=\"%s\") → 期望单边间隙 %.2f mm"
                                                % (str(fit), v),
                            "tol_default_mm": FIT_DEFAULT_TOL_MM}
    if feature is not None:
        hit = [x for x in (lib.get("features") or []) if str(x.get("name")) == str(feature)]
        if not hit:
            return _j({"ok": False, "error": "特征不存在: %s" % feature,
                       "available": [x.get("name") for x in (lib.get("features") or [])]})
        out["feature"] = hit[0]
    out["usage"] = "fit_help() 全量速查 · fit_help(fit=\"location\") 单档 · fit_help(feature=\"socket\") 单特征 · fit_help(m=3) ISO 间隙孔 · fit_help(d=8) 导向倒角"
    return _j(out)


def _declared_index():
    """设计意图索引：对象名 → [(连接 id, 端)]，以及 (a名,b名) 对 → [连接 id]。只读 K.dsh_contract。"""
    st = _store()
    side, pairs, comp_of = {}, {}, {}
    for cid, comp in st["components"].items():
        for nm in comp.get("objects", []):
            comp_of.setdefault(nm, []).append(cid)
    for cid, conn in st["connections"].items():
        na = [o.name for o in _component_objects(conn["a"])]
        nb = [o.name for o in _component_objects(conn["b"])]
        for nm in na:
            side.setdefault(nm, []).append({"connection": cid, "side": "a", "component": conn["a"]})
        for nm in nb:
            side.setdefault(nm, []).append({"connection": cid, "side": "b", "component": conn["b"]})
        for x in na:
            for y in nb:
                pairs.setdefault((x, y), []).append(cid)
                pairs.setdefault((y, x), []).append(cid)
    return side, pairs, comp_of


def _severity(depth_mm, frac):
    """严重度三档。阈值理由：2 mm ≈ 常见打印壁厚（穿透 2 mm 就是打穿一层壁）；
    0.5 mm ≈ 肉眼可见的缝隙量级；体积占比 25% ≈ 小件被埋掉四分之一；2% ≈ 一小条。"""
    hi = (depth_mm is not None and depth_mm > SEV_DEPTH_HIGH_MM) or (frac is not None and frac > SEV_FRAC_HIGH)
    med = (depth_mm is not None and depth_mm > SEV_DEPTH_MED_MM) or (frac is not None and frac > SEV_FRAC_MED)
    return "high" if hi else ("medium" if med else "low")


def _c_interference_report_p1(st):
    depth_eps, include_hidden, limit, scope = st["depth_eps"], st["include_hidden"], st["limit"], st["scope"]
    try:
        import numpy as np  # noqa: F401  # handed to the next split stage through st
        t0 = time.perf_counter()
        objs = _scope_objects(scope, include_hidden)
        if not objs:
            return _j({"ok": False, "error": "scope 里没有可见网格对象（空产出 → 不静默成功）",
                       "scope": scope, "include_hidden": bool(include_hidden)})
        m_per_unit, mm_per_unit = _units()
        ub = _units_block(m_per_unit, mm_per_unit)
        eps_mm = float(depth_eps) if depth_eps is not None else MATE_DEPTH_EPS_MM
        side, dpairs, comp_of = _declared_index()
        cand = _aabb_pairs(objs)
        tested = cand[:max(0, int(limit))]
        cache = {}

        def MD(ob):
            if ob.name not in cache:
                cache[ob.name] = _mesh_data([ob])
            return cache[ob.name]

        def gkey(name):
            if name in comp_of:
                return "comp:" + comp_of[name][0]
            ob = bpy.data.objects.get(name)
            if ob is not None and ob.parent is not None:
                return "parent:" + ob.parent.name
            return "self:" + name

        offenders, exempted, skipped = [], [], []
        checked = overlapping = 0
        t1 = time.perf_counter()
    finally:
        st.update((k, v) for k, v in locals().items() if k != "st")

def _c_interference_report_p2(st):
    MD, checked, declared_ok, dpairs, eps_mm = (
        st.get("MD"), st.get("checked"), st["declared_ok"], st.get("dpairs"), st.get("eps_mm"))
    exempted, mm_per_unit, np, objs, offenders = (
        st.get("exempted"), st.get("mm_per_unit"), st.get("np"), st.get("objs"), st.get("offenders"))
    overlapping, samples, side, skipped, tested = (
        st.get("overlapping"), st["samples"], st.get("side"), st.get("skipped"), st.get("tested"))
    use_bvh = st["use_bvh"]
    DA, DB, PA, PB, closed, dby, declared, dep, depth_max, depth_mean, depth_mm, depth_ok, dir_used, frac, frac_proxy, how, i, inA, inB, inside_frac, j, ma, mb, n, oa, ob_, ovr, rec, score, sev, ta, tb, tri_hit = (st.get("DA"), st.get("DB"), st.get("PA"), st.get("PB"), st.get("closed"), st.get("dby"), st.get("declared"), st.get("dep"), st.get("depth_max"), st.get("depth_mean"), st.get("depth_mm"), st.get("depth_ok"), st.get("dir_used"), st.get("frac"), st.get("frac_proxy"), st.get("how"), st.get("i"), st.get("inA"), st.get("inB"), st.get("inside_frac"), st.get("j"), st.get("ma"), st.get("mb"), st.get("n"), st.get("oa"), st.get("ob_"), st.get("ovr"), st.get("rec"), st.get("score"), st.get("sev"), st.get("ta"), st.get("tb"), st.get("tri_hit"))
    try:
        for (i, j) in tested:
            oa, ob_ = objs[i], objs[j]
            ma, mb = MD(oa), MD(ob_)
            if not ma or not mb or ma["tris_n"] == 0 or mb["tris_n"] == 0:
                skipped.append({"a": oa.name, "b": ob_.name, "why": "一端没有三角面"})
                continue
            if MATE_MAX_TRIS > 0 and (ma["tris_n"] > MATE_MAX_TRIS or mb["tris_n"] > MATE_MAX_TRIS):
                skipped.append({"a": oa.name, "b": ob_.name, "why": "网格过大（> %d 面）不判" % MATE_MAX_TRIS})
                continue
            checked += 1
            tri_hit = None
            if use_bvh:
                ta, tb = _bvh(ma["tris"]), _bvh(mb["tris"])
                try:
                    tri_hit = bool(ta.overlap(tb)) if (ta is not None and tb is not None) else None
                except Exception as e:
                    tri_hit = None
                    skipped.append({"a": oa.name, "b": ob_.name, "why": "BVH overlap 失败: %s（按 AABB 候选继续）" % e})
                if tri_hit is False:
                    continue
            overlapping += 1
            # --- 穿透深度 ---
            n = int(max(16, min(int(samples), CONTACT_SAMPLE_CAP)))
            PA = _sample_surface(ma["tris"], n)
            PB = _sample_surface(mb["tris"], n)
            if PA is None or PB is None:
                skipped.append({"a": oa.name, "b": ob_.name, "why": "零面积网格，采样失败"})
                continue
            closed = bool(ma["closed"] and mb["closed"])
            if INSIDE_MAX_TRIS <= 0 or (
                ma["tris_n"] <= INSIDE_MAX_TRIS and mb["tris_n"] <= INSIDE_MAX_TRIS
            ):
                inB = _inside_mask(PA, mb["tris"])
                inA = _inside_mask(PB, ma["tris"])
                if use_bvh:
                    _q, _nn, DA = _nearest(_bvh(mb["tris"]), PA)
                    _q2, _nn2, DB = _nearest(_bvh(ma["tris"]), PB)
                    how = "BVH 最近点距离 + 射线奇偶内外"
                else:
                    DA = _pt_tri_dist(PA, mb["tris"])
                    DB = _pt_tri_dist(PB, ma["tris"])
                    how = "纯 numpy 点-三角面最近距离 + 射线奇偶内外（use_bvh=false）"
                dep = np.concatenate([DA[inB & np.isfinite(DA)], DB[inA & np.isfinite(DB)]])
                depth_max = float(dep.max()) if len(dep) else 0.0
                depth_mean = float(dep.mean()) if len(dep) else 0.0
                dir_used = "A→B" if (inB & np.isfinite(DA)).any() else ("B→A" if (inA & np.isfinite(DB)).any() else "无埋入点")
                inside_frac = float(len(dep)) / float(len(PA) + len(PB))
                depth_ok = True
            else:
                depth_max = depth_mean = None
                inside_frac = None
                dir_used = "skipped"
                how = "网格超内测试上限 %d → 未判穿透" % INSIDE_MAX_TRIS
                depth_ok = False
            ovr = _overlap_estimate(ma["tris"], mb["tris"], ma["volume_m3"], mb["volume_m3"],
                                    ma["closed"], mb["closed"], samples=int(max(32, min(int(samples), 4000))))
            frac = ovr.get("frac_of_smaller") if ovr.get("reliable") else None
            frac_proxy = frac if frac is not None else inside_frac
            depth_mm = (depth_max * mm_per_unit) if depth_max is not None else None
            sev = _severity(depth_mm, frac if frac is not None else inside_frac)
            score = _r4((frac_proxy or 0.0) * 1000.0 + (depth_mm or 0.0) * 10.0)
            dby = dpairs.get((oa.name, ob_.name), []) + dpairs.get((ob_.name, oa.name), [])
            dby = sorted(set(dby))
            declared = bool(dby)
            rec = {"a": oa.name, "b": ob_.name, "declared": declared, "declared_by": dby,
                   "declared_sides": [side.get(oa.name, []), side.get(ob_.name, [])] if declared else None,
                   "depth_mm": _r4(depth_mm) if depth_mm is not None else None,
                   "depth_mean_mm": _r4(depth_mean * mm_per_unit) if depth_mean is not None else None,
                   "depth_measured": bool(depth_ok), "depth_direction": dir_used,
                   "overlap_volume_mm3": ovr.get("overlap_volume_mm3"),
                   "overlap_frac_of_smaller": frac,
                   "overlap_frac_of_box": ovr.get("frac_of_box"),
                   "overlap_stderr": ovr.get("stderr_frac"),
                   "surface_inside_frac": _r6(inside_frac) if inside_frac is not None else None,
                   "volume_reliable": bool(ovr.get("reliable")),
                   "severity": sev, "severity_score": score,
                   "aabb_tri_overlap": tri_hit, "method": how,
                   "a_tris": ma["tris_n"], "b_tris": mb["tris_n"], "closed": closed,
                   "volume_mm3": {"a": _r4(ma["volume_m3"] * (mm_per_unit ** 3)),
                                  "b": _r4(mb["volume_m3"] * (mm_per_unit ** 3))}}
            if declared and declared_ok and depth_mm is not None and depth_mm <= eps_mm:
                rec["exempt_reason"] = ("设计内的接触：%s 是连接 %s 的两端（declared），"
                                        "实测穿透 %.3f mm ≤ 门限 %.3f mm" % (oa.name, "/".join(dby), depth_mm, eps_mm))
                exempted.append(rec)
            else:
                if declared and declared_ok and depth_mm is not None:
                    rec["exempt_reason"] = None
                    rec["note"] = ("declared 但穿透 %.3f mm > 门限 %.3f mm → 仍要报（计划内接触不等于可以随便穿）"
                                   % (depth_mm, eps_mm))
                offenders.append(rec)
    finally:
        st.update((k, v) for k, v in locals().items() if k != "st")

def _c_interference_report_p3(st):
    cand, checked, declared_ok, eps_mm, exempted = (
        st.get("cand"), st.get("checked"), st["declared_ok"], st.get("eps_mm"), st.get("exempted"))
    gkey, objs, offenders, overlapping, samples = (
        st.get("gkey"), st.get("objs"), st.get("offenders"), st.get("overlapping"), st["samples"])
    skipped, t0, t1, tested, ub = st.get("skipped"), st.get("t0"), st.get("t1"), st.get("tested"), st.get("ub")
    use_bvh = st["use_bvh"]
    g, k, order, r, sig = (st.get("g"), st.get("k"), st.get("order"), st.get("r"), st.get("sig"))
    try:
        offenders.sort(key=lambda r: (-(r.get("severity_score") or 0.0), str(r["a"]), str(r["b"])))
        for k, r in enumerate(offenders):
            r["rank"] = k + 1
        groups = {}
        for r in offenders:
            sig = tuple(sorted([gkey(r["a"]), gkey(r["b"])]))
            g = groups.setdefault(sig, {"group_key": list(sig), "members": [], "worst_severity": "low",
                                        "worst_score": 0.0, "max_depth_mm": 0.0, "objects": set(),
                                        "declared_all": True})
            g["members"].append({"a": r["a"], "b": r["b"], "rank": r["rank"], "severity": r["severity"],
                                 "depth_mm": r["depth_mm"], "declared": r["declared"]})
            g["objects"].update([r["a"], r["b"]])
            g["declared_all"] = g["declared_all"] and r["declared"]
            order = {"low": 0, "medium": 1, "high": 2}
            if order[r["severity"]] > order[g["worst_severity"]]:
                g["worst_severity"] = r["severity"]
            g["worst_score"] = max(g["worst_score"], r.get("severity_score") or 0.0)
            g["max_depth_mm"] = max(g["max_depth_mm"], r.get("depth_mm") or 0.0)
        gout = []
        for sig, g in groups.items():
            gout.append({"group_key": g["group_key"], "objects": sorted(g["objects"]),
                         "pairs": len(g["members"]), "worst_severity": g["worst_severity"],
                         "worst_score": _r4(g["worst_score"]), "max_depth_mm": _r4(g["max_depth_mm"]),
                         "declared_all": g["declared_all"],
                         "why": "同组：%s（按 同一组件 > 同一父件 > 自身 归一）" % " / ".join(g["group_key"]),
                         "members": g["members"]})
        gout.sort(key=lambda g: -g["worst_score"])
        ms_total = (time.perf_counter() - t0) * 1000
        res = {"ok": True, "objects": len(objs), "pairs_aabb": len(cand), "pairs_tested": len(tested),
               "pairs_checked": checked, "pairs_overlapping": overlapping,
               "units": ub, "depth_eps_mm": _r4(eps_mm), "declared_ok": bool(declared_ok),
               "use_bvh": bool(use_bvh), "samples": int(samples),
               "count": len(offenders), "offenders": offenders, "groups": gout,
               "exempted_count": len(exempted), "exempted": exempted, "skipped": skipped,
               "severity_rules": {"depth_high_mm": SEV_DEPTH_HIGH_MM, "depth_medium_mm": SEV_DEPTH_MED_MM,
                                  "frac_high": SEV_FRAC_HIGH, "frac_medium": SEV_FRAC_MED,
                                  "score": "体积占比×1000 + 深度mm×10（体积为主序）"},
               "ms": {"total": round(ms_total, 1), "measure": round((time.perf_counter() - t1) * 1000, 1)},
               "note": "老 op check_interference 未改动（返回结构不变）；本 op 只多给信息，不替代它",
               "readonly": True}
        if checked == 0 and cand:
            res["warning"] = "所有 AABB 候选都被跳过（网格过大/无三角面）→ 本次没有任何可用结论，不要读成'无干涉'"
        elif not cand:
            res["note_clean"] = "AABB 粗筛就没有候选对 → 无干涉（这是结论，不是空产出）"
        _log("interference_report", {"objects": len(objs), "offenders": len(offenders),
                                     "exempted": len(exempted), "eps_mm": _r4(eps_mm)})
        return _j(res)
    finally:
        st.update((k, v) for k, v in locals().items() if k != "st")

def c_interference_report(scope=None, use_bvh=True, limit=60, declared_ok=True,
                          depth_eps=None, include_hidden=False, samples=VOL_SAMPLES):
    """逐对干涉报告：穿透深度(mm) + 重叠体积占比 + 严重度排序 + 分组 + 设计意图白名单。

    与老 op check_interference 的分工：老 op 只回答"哪两对相交"（**返回结构一字未动**，向后兼容）；
    本 op 回答"相交多深 / 占多少体积 / 哪对最该管 / 哪些本来就是设计内的"。

    穿透深度怎么量（原理）：
      * 内外：射线奇偶（_inside_mask）—— 对闭合网格全局精确，深埋在凹腔里也不会判反。
      * 量值：BVH 最近点距离（SDF 近似）。方向先 A→B；A 侧表面没埋进去、但 BVH 确实报相交时，
        再量 B→A（小件整个被大件包住时只有 B→A 有读数）。
      * use_bvh=false：退回纯 numpy 的点-三角面最近距离（_pt_tri_dist）—— 独立算法，也用来复核 BVH。
    重叠体积怎么量：没有 CSG 编译器，就在 A/B 世界 AABB 的**交集盒**里低差异采样，
      射线奇偶判"同时在两者内"的比例 × 盒体积 = 无偏估计，同时报二项标准误（是估计值不是精确值）。

    设计意图白名单（declared）：a、b 分属某条已注册连接的两端 → declared=true（计划内的接触），
      默认从 offenders 移到 exempted；但穿透深度 > depth_eps 时**仍然要报**（计划内接触 ≠ 可以随便穿）。
      depth_eps 默认 0.2 mm 的理由：fit 四档里最大的是 clearance 0.25（让料，本就不该穿透），
      真正会过盈的 press 只有 0.05 mm → 0.2 mm 足够放过所有"设计内"的压配；
      它又比 audit.py 的 MICRO_GAP_MM=0.3（贴而未重合的报告线）紧一档 —— 等它涨到可见缺陷量级之前就报；
      同时远高于测量噪声（三角化弦差 ~0.005–0.02 mm），不会因网格噪声误报。

    严重度：score = 体积占比×1000 + 深度mm×10（体积为主序、深度为次序），档位见 _severity()。
    groups：按"同一组件 > 同一父件 > 自身"归一出的组键配对聚类（同组的多对合并成一条，看最坏的那对）。

    只读：不改场景、不改契约状态。空产出（scope 里没有可见网格）→ ok:false。
    """
    st = dict(locals())
    for _fn_ in (_c_interference_report_p1, _c_interference_report_p2, _c_interference_report_p3):
        _r = _fn_(st)
        if _r is not None:
            return _r


def c_help():
    return _j({"version": CONTRACT_VERSION,
               "S1_ops": {"register_component": "id, objects, tags, note",
                          "register_connection": "id, a, b, candidates, params, forbidden, confidence, evidence_required, status",
                          "register_envelope": "id, min, max, scope, note",
                          "check_envelope": "id(可选) → 越界清单",
                          "check_interference": "scope, use_bvh, limit → AABB 粗筛 + BVH 精查",
                          "check_interface": "conn_id, mesh(true), samples(1500) → 两组件间隙/侵入：bbox 级老字段（gap_axis_m/gap_m/overlap_bbox）口径不变 + mesh 块（网格级单边间隙 mm/接触占比/穿透 mm）",
                          "destructive_guard": "op, targets, conn_ids → 允许/拦下 + 理由",
                          "evidence": "label, view → 出图 + md5 记账",
                          "ledger/status/report": "证据账本 / 概览 / Markdown 报告"},
               "S2_ops": {"verify": "conn_id, err, tolerance, identifiable, probe_err, probe_tolerance → 三态",
                          "flip": "conn_id, to, why → 翻转候选并记历史",
                          "advance": "conn_id, status(proposed|testing|supported|refuted), why"},
               "V090_ops": {"fingerprint": "objects|scope → 几何指纹（对象/面数/点数/世界 bbox + 12 位 digest）；改前改后各取一次即知源动没动",
                            "ledger": "with_stale=true（默认）→ 逐条证据与当前指纹对拍，源变过的标 stale=true + stale_labels",
                            "drift_guard": "verify 会记 bbox 快照：中心漂移 >10% 对角线 或 尺寸漂移 >20% → supported 在 status/ledger 读取时自动降级 unresolved（verdict.demoted 留理由；带动画数据的对象跳过）"},
               "S3_ops": {"mate_check": "conn_id, fit(clearance|location|press|snap), nominal(期望单边间隙 mm), tol_mm(默认 0.15), samples(≤4000), contact_max_mm, depth_eps_mm"
                                        " → 网格级配合门：接触面积占比/接触锚点/接触法向/单边间隙(mm)/穿透深度(mm) → supported|refuted|unresolved",
                          "fit_help": "fit|feature|m|d → 装配特征库速查（9 特征 + asm_fit 四档 + asm_lead + ISO 273 间隙孔 + Blender 侧造法）",
                          "interference_report": "scope, use_bvh(true), limit(60), declared_ok(true), depth_eps(默认 0.2 mm), include_hidden"
                                                 " → 逐对：穿透深度 mm + 重叠体积占比 + 严重度排序 + groups 聚类 + exempted（设计意图白名单）"},
               "S3_rules": "配合门三态：采样 < 8 点 / 组件空 / 没给 fit·nominal / 没做成侵入判定 → unresolved；接触点 0 个（没搭上）或穿透超门限或间隙出带 → refuted；"
                           "间隙落在 nominal ± tol 且无过深穿透 → supported。接触距离 = 1.5% × 合并 bbox 对角线。共享标称纪律：公母从同一个 nominal ± asm_fit 派生，禁止两处独立字面量。",
               "rules": "外部证据不足（参数不可辨识）必须报 unresolved；探针误差 > 容差则 refuted；破坏性操作在未判别前拦住；"
                        "几何在判定之后漂移 → 判定自动失效（V0.9）；装配级量测一律走求值网格（Boolean 未 Apply 也算数）、数值必带单位、空产出必须 ok:false",
               "docs": "docs/假设驱动建模-cookbook.md · runtime/py/assembly/assembly_features.json（特征库数据）"})