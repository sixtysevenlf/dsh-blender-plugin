# -*- coding: utf-8 -*-
"""Split from assembly.py; behavior preserved by the facade."""
from .common import *  # noqa: F401,F403
from .manifest import _load_manifest, _manifest_validate
from .scan import _pair_key, asm_scan
from .gate import _trim

_REPAIR_METHODS = ("translate", "snap", "subtract")
_REPAIR_HONEST_LIMITS = [
    "平移按 AABB 分离规划：AABB 分离 ⇒ 三角面必不相交；但可能引入与第三件的新冲突（复扫会报，最多 max_rounds 轮）",
    "布尔差只从较大件减去较小件（cutter 用副本、原件不动）；拓扑改动不能用对象级 mark/revert 回退，必须靠文件级 snapshot",
    "浮块计划来自 audit_snap_floaters（单件 1% 可见口径）；装配接口没搭上请用 manifest 连接图，不是同一判据",
    "开网格 / 法线朝内 / 求值网格缺失一律不自动修（证据不足不猜）；先 fix_repair / 重算法线",
    "dry_run=true 只读：不移动、不布尔、不写盘；深度/体积是采样式估计",
]


def _repair_flag(v, name):
    """JSON / 字符串 / 0-1 → bool；坏值回 (None, 错误)。"""
    if isinstance(v, bool):
        return v, None
    if isinstance(v, (int, float)) and float(v) in (0.0, 1.0):
        return bool(v), None
    if isinstance(v, str):
        s = v.strip().lower()
        if s in ("true", "1", "yes", "on"):
            return True, None
        if s in ("false", "0", "no", "off"):
            return False, None
    return None, "%s 必须是布尔（true/false），收到 %r" % (name, v)


def _repair_policy(policy):
    """policy 归一化 → (policy, explicit, err)。

    缺省（None）等价 safe，但保留旧调用语义：allow_boolean=true 是显式人工授权，仍可做布尔；
    **显式**传 policy="safe" 才硬禁布尔（拓扑改动），manual 只规划，aggressive 保留旧 allow_boolean 路径。
    """
    if policy is None:
        return "safe", False, None
    p = str(policy).strip().lower()
    if p in ("safe", "manual", "aggressive"):
        return p, True, None
    return None, False, "policy 只支持 safe|manual|aggressive，收到 %r" % (policy,)


def _repair_brief(scan):
    """scan 回执 → 修复循环用的瘦身状态（只留判据字段）。"""
    if not isinstance(scan, dict):
        return {"ok": False, "verdict": "error", "counts": {}, "offenders": [], "floaters": [],
                "floating_unresolved": [], "coverage": {}, "error": "scan 回执不是 dict"}
    return {"ok": bool(scan.get("ok")), "verdict": scan.get("verdict"),
            "counts": dict(scan.get("counts") or {}),
            "offenders": list(scan.get("offenders") or []),
            "floaters": list(scan.get("floaters") or []),
            "floating_unresolved": list(scan.get("floating_unresolved") or []),
            "coverage": dict(scan.get("coverage") or {}),
            "verdict_line": scan.get("verdict_line"), "error": scan.get("error")}


def _repair_bad_total(brief):
    """clash + contained + float（float 按 scan 的 counts 口径：浮空对象/接口数）。"""
    c = (brief or {}).get("counts") or {}
    return (int(c.get("clash") or 0) + int(c.get("contained") or 0) + int(c.get("float") or 0))


def _repair_residual(brief):
    c = (brief or {}).get("counts") or {}
    return {"clash": int(c.get("clash") or 0), "contained": int(c.get("contained") or 0),
            "float": int(c.get("float") or 0), "unresolved": int(c.get("unresolved") or 0)}


def _repair_pair_row(p):
    """offender → 瘦身行（判据字段，不展开大对象）。"""
    if not isinstance(p, dict):
        return {"raw": str(p)[:160]}
    return {"a": p.get("a"), "b": p.get("b"), "class": p.get("class"),
            "severity": p.get("severity"), "depth_mm": p.get("depth_mm"),
            "volume_mm3": p.get("volume_mm3"), "min_gap_mm": p.get("min_gap_mm"),
            "tri_overlap_pairs": p.get("tri_overlap_pairs"),
            "method": p.get("method"), "reason": str(p.get("reason") or "")[:220]}


def _repair_float_row(f):
    if not isinstance(f, dict):
        return {"raw": str(f)[:160]}
    return {"object": f.get("object"), "objects": [str(x) for x in (f.get("objects") or [])][:12],
            "reason": f.get("reason"), "interface": f.get("interface"),
            "mesh_gap_mm": f.get("mesh_gap_mm"), "why": str(f.get("why") or "")[:200]}


def _repair_brief_out(brief):
    b = brief if isinstance(brief, dict) else {}
    return {"ok": bool(b.get("ok", False)), "verdict": b.get("verdict"),
            "counts": b.get("counts") or {},
            "coverage": b.get("coverage"),
            "offenders": [_repair_pair_row(p) for p in (b.get("offenders") or [])[:20]],
            "floaters": [_repair_float_row(f) for f in (b.get("floaters") or [])[:20]],
            "floating_unresolved": [{"reason": u.get("reason"), "why": str(u.get("why") or "")[:200]}
                                    for u in (b.get("floating_unresolved") or [])[:10] if isinstance(u, dict)],
            "verdict_line": b.get("verdict_line"), "error": b.get("error")}


def _repair_touched(rec):
    """一个 plan/blocked 记录牵涉到的对象名集合。"""
    out = set()
    if not isinstance(rec, dict):
        return out
    pr = rec.get("pair")
    if isinstance(pr, (list, tuple)):
        out.update(str(x) for x in pr if x is not None)
    elif pr:
        out.add(str(pr))
    for k in ("target", "cutter", "object"):
        if rec.get(k):
            out.add(str(rec[k]))
    for o in rec.get("objects") or []:
        out.add(str(o))
    return out


def _dedupe_blocked(items):
    out, seen = [], set()
    for it in items or []:
        try:
            key = _canon(it if isinstance(it, dict) else {"raw": str(it)})
        except Exception:
            key = str(it)
        if key in seen:
            continue
        seen.add(key)
        out.append(it)
    return out


def _obj_volume_mm3(md, mmu):
    """求值网格体积（mm³）；有符号体积不可用时退 AABB 体积。"""
    if not isinstance(md, dict):
        return 0.0
    try:
        v = abs(float(md.get("volume_signed_m3") or 0.0)) * (float(mmu) ** 3)
    except Exception:
        v = 0.0
    if v > 0.0:
        return v
    try:
        lo, hi = md["aabb"][0], md["aabb"][1]
        return abs((float(hi[0]) - float(lo[0])) * (float(hi[1]) - float(lo[1])) *
                   (float(hi[2]) - float(lo[2]))) * (float(mmu) ** 3)
    except Exception:
        return 0.0


def _repair_manifold_ok(md):
    return bool(isinstance(md, dict) and md.get("closed") and md.get("normals_ok"))


def _repair_move_precheck(name):
    """整件平移的阻塞预检（与 audit_snap_floaters 同口径）。"""
    out = []
    ob = bpy.data.objects.get(str(name))
    if ob is None:
        return ["对象不存在: %s" % name]
    if ob.parent is not None:
        out.append("%s 有父级（整件平移会被父级覆盖，先清父级）" % name)
    if ob.animation_data is not None:
        out.append("%s 有动画数据（关键帧会覆盖平移）" % name)
    return out


def _repair_translate_plan(md, a, b, clearance_mm, mmu):
    """两件 AABB → 把**体积较小件**沿最省平移量的轴移开（AABB 分离 ⇒ 三角面必不相交）。

    返回 (action, None) 或 (None, blocked_slim)。平移量 = AABB 分离所需 + clearance_mm；
    是否超出 max_move_mm 由调用方判定（这里只给 required_mm）。
    """
    ma, mb = md.get(a), md.get(b)
    if not isinstance(ma, dict) or not isinstance(mb, dict):
        return None, {"pair": sorted([a, b]), "method": "translate",
                      "reason": "求值网格缺失（md），无法规划平移"}
    va, vb = _obj_volume_mm3(ma, mmu), _obj_volume_mm3(mb, mmu)
    small, big = (a, b) if va <= vb else (b, a)
    ms, mbg = md.get(small), md.get(big)
    try:
        mn_s = [float(x) for x in ms["aabb"][0]]
        mx_s = [float(x) for x in ms["aabb"][1]]
        mn_b = [float(x) for x in mbg["aabb"][0]]
        mx_b = [float(x) for x in mbg["aabb"][1]]
    except Exception as e:
        return None, {"pair": sorted([a, b]), "method": "translate",
                      "reason": "AABB 读取失败: %s: %s" % (type(e).__name__, str(e)[:120])}
    best = None
    for k in range(3):
        if mx_s[k] <= mn_b[k] + 1e-12 or mx_b[k] <= mn_s[k] + 1e-12:
            continue  # 该轴已分离，不是可行分离轴
        d_pos = mx_b[k] - mn_s[k]           # 小件往 + 方向移出大件 AABB
        d_neg = mn_b[k] - mx_s[k]           # 小件往 - 方向移出大件 AABB
        delta = d_pos if abs(d_pos) <= abs(d_neg) else d_neg
        req_mm = abs(delta) * float(mmu)
        overlap_mm = (min(mx_s[k], mx_b[k]) - max(mn_s[k], mn_b[k])) * float(mmu)
        if best is None or req_mm < best["required_mm"] - 1e-12:
            best = {"axis": k, "delta": delta, "required_mm": req_mm, "overlap_mm": overlap_mm}
    if best is None:
        return None, {"pair": sorted([a, b]), "method": "translate",
                      "reason": "AABB 三轴都已分离（与 clash/contained 分类不一致，数据异常）"}
    axes = "xyz"
    delta_vec = [0.0, 0.0, 0.0]
    delta_vec[best["axis"]] = best["delta"]
    post_mn = [mn_s[k] + delta_vec[k] for k in range(3)]
    post_mx = [mx_s[k] + delta_vec[k] for k in range(3)]
    new_overlaps = []
    for nm, mdo in (md or {}).items():
        if nm in (a, b) or not isinstance(mdo, dict):
            continue
        try:
            on = [float(x) for x in mdo["aabb"][0]]
            ox = [float(x) for x in mdo["aabb"][1]]
        except Exception:
            continue
        if all(min(post_mx[k], ox[k]) - max(post_mn[k], on[k]) > 1e-9 for k in range(3)):
            new_overlaps.append(str(nm))
    need = float(best["required_mm"]) + float(clearance_mm)
    act = {"method": "translate", "target": small, "keep": big, "pair": sorted([a, b]),
           "axis": axes[best["axis"]],
           "delta_units": [_r(x, 8) for x in delta_vec],
           "delta_mm": [_r(x * float(mmu), 4) for x in delta_vec],
           "overlap_mm": _r(best["overlap_mm"], 4),
           "required_mm": _r(need, 4),
           "clearance_mm": _r(clearance_mm, 4),
           "aabb_separated_after": True,
           "new_aabb_overlaps": new_overlaps[:10],
           "risk": ("可能新增与 %d 件 AABB 重叠：%s（复扫会报）"
                    % (len(new_overlaps), new_overlaps[:5]) if new_overlaps else "none"),
           "why": ("沿 %s 轴移开小件 %s：平移 %.3f mm（AABB 交叠 %.3f + 间隙 %.3f）→ AABB 分离"
                   "（AABB 分离 ⇒ 三角面不可能相交）"
                   % (axes[best["axis"]], small, need, best["overlap_mm"], float(clearance_mm)))}
    return act, None


def _repair_boolean_plan(md, a, b, mmu, cls):
    """两件都闭合流形 → 从较大件布尔减较小件（cutter 会在执行时复制，原件不动）。"""
    ma, mb = md.get(a), md.get(b)
    if not isinstance(ma, dict) or not isinstance(mb, dict):
        return None, "求值网格缺失"
    if not (_repair_manifold_ok(ma) and _repair_manifold_ok(mb)):
        return None, "两件不都是闭合且法线朝外的流形（布尔差不可信）"
    va, vb = _obj_volume_mm3(ma, mmu), _obj_volume_mm3(mb, mmu)
    target, cutter = (a, b) if va >= vb else (b, a)
    return {"method": "subtract", "target": target, "cutter": cutter, "pair": sorted([a, b]),
            "class": cls,
            "params": {"op": "DIFFERENCE", "solver": "EXACT", "cutter_copy": True},
            "risk": "布尔改 target 拓扑（材质槽/UV 可能受影响）；cutter 用副本、原件不动；回退靠文件级 snapshot",
            "why": "两件均闭合流形；allow_boolean=true → 从 %s 减去 %s 的侵入体（不改 cutter 原件）"
                   % (target, cutter)}, None


def _repair_float_plan(scan, names, planned):
    """float → audit_snap_floaters(dry_run=true) 拿计划；拿不到就 blocked（不猜）。"""
    plan, blocked = [], []
    floaters = [f for f in (scan.get("floaters") or []) if isinstance(f, dict)]
    if not floaters:
        return plan, blocked
    names_all = [str(x) for x in (names or [])]
    sf, err = _call_api("audit", "snap_floaters",
                        {"objects": names_all, "dry_run": True})
    if err or sf is None:
        blocked.append({"kind": "float", "method": "snap",
                        "reason": "浮块修复计划拿不到：%s（在线路径 preload 里带 audit）" % str(err)[:200]})
        return plan, blocked
    if not sf.get("ok"):
        blocked.append({"kind": "float", "method": "snap",
                        "reason": "audit_snap_floaters ok=false: %s" % str(sf.get("error"))[:200]})
        return plan, blocked
    moves = [m for m in (sf.get("moves") or []) if isinstance(m, dict)]
    skipped = [s for s in (sf.get("skipped") or []) if isinstance(s, dict)]
    for mv in moves:
        mobjs = [str(x) for x in (mv.get("objects") or []) if x]
        if not mobjs:
            blocked.append({"kind": "float", "method": "snap", "rank": mv.get("rank"),
                            "reason": "snap 计划缺 objects"})
            continue
        if any(o in planned for o in mobjs):
            blocked.append({"kind": "float", "method": "snap", "rank": mv.get("rank"),
                            "objects": mobjs, "reason": "本轮已规划该对象（多浮块/多冲突；下一轮复扫再处理）"})
            continue
        pre = []
        for o in mobjs:
            pre.extend(_repair_move_precheck(o))
        pre.extend(str(x) for x in (mv.get("why_not_applied") or []))
        if pre:
            blocked.append({"kind": "float", "method": "snap", "rank": mv.get("rank"),
                            "objects": mobjs, "reason": "; ".join(sorted(set(pre)))})
            continue
        planned.update(mobjs)
        plan.append({"method": "snap", "objects": mobjs, "pair": mobjs[:2],
                     "delta_units": [_r(x, 8) for x in (mv.get("delta_units") or [])],
                     "delta_mm": [_r(x, 4) for x in (mv.get("delta_mm") or [])],
                     "gap_before_mm": mv.get("gap_before_mm"),
                     "snap_method": mv.get("method"), "rank": mv.get("rank"),
                     "risk": "浮块整件平移会改变与其它件的接触/最近邻关系（复扫会报）；父级/动画已 blocked",
                     "why": "浮块贴到最近邻分量（audit_snap_floaters 计划；delta 单位=场景单位，delta_mm=毫米）"})
    for sk in skipped:
        blocked.append({"kind": "float", "method": "snap", "rank": sk.get("rank"),
                        "objects": [str(x) for x in (sk.get("objects") or [])][:10],
                        "reason": str(sk.get("why") or "snap 计划拒绝该浮块")[:220]})
    if not plan and not blocked:
        blocked.append({"kind": "float", "method": "snap",
                        "objects": [str(f.get("object")) for f in floaters if f.get("object")][:10],
                        "reason": "audit_snap_floaters 没给出移动计划（孤岛无最近邻/浮块只是对象一部分）—— 需人工接上或补 manifest 接口"})
    return plan, blocked


def _repair_plan_all(scan, names, md, mmu, max_move_mm, clearance_mm, allow_boolean,
                     policy="safe", policy_explicit=False):
    """scan → (plan, blocked)。clash/contained 走平移（不够/被父级挡住再考虑布尔），float 走 snap。

    policy（低风险自动修复策略）：
      * safe（默认）：自动动作只允许 translate/snap；布尔是拓扑改动，必须 allow_boolean=true 显式同意
        （人工显式授权，不是自动选择）；scale 不实现/禁止。
      * manual：只规划、不自动执行；**不规划布尔**（干跑=false 也只回只读计划）。
      * aggressive：保留旧行为（布尔仍受 allow_boolean 控制），plan 里标 policy=aggressive。
    """
    plan, blocked = [], []
    planned = set()
    pol = str(policy or "safe").strip().lower()
    if pol not in ("safe", "manual", "aggressive"):
        pol = "safe"

    def _try_boolean(pair, cls):
        if pol == "manual":
            return False, "policy=manual：只出计划、不规划布尔（人工执行用 policy=aggressive + allow_boolean=true）"
        if pol == "safe" and policy_explicit:
            return False, ("policy=safe：禁止布尔（拓扑改动，低风险策略只允许 translate/snap）——"
                           "需显式 policy=aggressive + allow_boolean=true，或人工处理")
        if not allow_boolean:
            if pol == "aggressive":
                return False, "allow_boolean=false（aggressive 也需显式 allow_boolean=true 才做布尔）"
            return False, ("allow_boolean=false（policy=%s 不自动做布尔；布尔是拓扑改动，"
                           "需 allow_boolean=true 显式同意）" % pol)
        act, why = _repair_boolean_plan(md, pair[0], pair[1], mmu, cls)
        if act is None:
            return False, why
        if act["target"] in planned:
            return False, "目标 %s 本轮已有修复动作（多冲突；下一轮复扫再处理）" % act["target"]
        act["policy"] = pol
        planned.add(act["target"])
        plan.append(act)
        return True, None

    seen = set()
    for p in scan.get("offenders") or []:
        if not isinstance(p, dict):
            continue
        a, b = str(p.get("a") or ""), str(p.get("b") or "")
        cls = p.get("class")
        if not a or not b or cls not in ("clash", "contained"):
            continue
        key = _pair_key(a, b)
        if key in seen:
            continue
        seen.add(key)
        missing = [x for x in (a, b) if x not in md]
        if missing:
            blocked.append({"pair": [a, b], "class": cls, "method": "translate",
                            "reason": "求值网格缺失（md 里没有 %s）—— 先修网格" % missing})
            continue
        act, why = _repair_translate_plan(md, a, b, clearance_mm, mmu)
        if act is not None and act["required_mm"] <= float(max_move_mm) + 1e-9:
            target = act["target"]
            if target in planned:
                blocked.append({"pair": [a, b], "class": cls, "method": "translate",
                                "reason": "对象 %s 本轮已规划移动（多冲突；下一轮复扫再处理）" % target})
                continue
            pre = _repair_move_precheck(target)
            if pre:
                okb, whyb = _try_boolean([a, b], cls)
                if okb:
                    continue
                blocked.append({"pair": [a, b], "class": cls, "method": "translate",
                                "reason": "; ".join(pre) + "；布尔不可用：%s" % (whyb or "未知")})
                continue
            act["class"] = cls
            planned.add(target)
            plan.append(act)
            continue
        if act is None:
            why_txt = str((why or {}).get("reason") if isinstance(why, dict) else (why or "无法规划平移"))
        else:
            why_txt = ("需要 %.3f mm 平移 > max_move_mm %.3f mm（可调 max_move_mm / 复核接口帧 / 允许布尔）"
                       % (act["required_mm"], float(max_move_mm)))
        okb, whyb = _try_boolean([a, b], cls)
        if okb:
            continue
        if pol == "manual":
            why_txt += "；policy=manual 不规划布尔（只出计划）"
        elif pol == "safe" and policy_explicit:
            why_txt += "；policy=safe 硬禁布尔（需显式 policy=aggressive + allow_boolean=true，或人工处理）"
        elif not allow_boolean:
            why_txt += "；policy=%s 不自动做布尔（需 allow_boolean=true 显式同意）" % pol
        else:
            why_txt += "；布尔不可用：%s" % (whyb or "未知")
        blocked.append({"pair": [a, b], "class": cls, "method": "translate",
                        "required_mm": (act or {}).get("required_mm"), "reason": why_txt})
    fplan, fblocked = _repair_float_plan(scan, names, planned)
    plan.extend(fplan)
    blocked.extend(fblocked)
    for u in scan.get("floating_unresolved") or []:
        if isinstance(u, dict):
            blocked.append({"kind": "float_unresolved",
                            "reason": str(u.get("why") or u.get("reason") or "浮空未判")[:240]})
    for nm in (scan.get("coverage") or {}).get("unresolved_objects") or []:
        blocked.append({"kind": "unresolved_object", "object": str(nm),
                        "reason": "网格不闭合/法线朝内/求值网格缺失 → 内外与深度不可信，自动修复拒绝（先 fix_repair / 重算法线）"})
    # 统一回执字段：plan[].risk（每动作必须有）+ blocked[].blocked_reason（=reason 别名，弱模型直读）
    for a in plan:
        if isinstance(a, dict):
            a.setdefault("risk", "none")
            a.setdefault("policy", pol)
    for b in blocked:
        if isinstance(b, dict):
            b.setdefault("blocked_reason", b.get("reason"))
    return plan, blocked


def _repair_expected(brief, plan, blocked):
    """dry_run 的预测量（明确写清只是预测，实际以执行后复扫为准）。"""
    counts = dict((brief or {}).get("counts") or {})
    pred = {"clash": int(counts.get("clash") or 0),
            "contained": int(counts.get("contained") or 0),
            "float": int(counts.get("float") or 0)}
    n_translate = n_subtract = n_snap = 0
    for a in plan or []:
        m = a.get("method") if isinstance(a, dict) else None
        cls = (a or {}).get("class") if isinstance(a, dict) else None
        if m == "translate":
            n_translate += 1
            if cls in pred:
                pred[cls] = max(0, pred[cls] - 1)
        elif m == "subtract":
            n_subtract += 1
            if cls in pred:
                pred[cls] = max(0, pred[cls] - 1)
        elif m == "snap":
            n_snap += 1
            pred["float"] = max(0, pred["float"] - 1)
    return {"actions": len(plan or []), "translate_pairs": n_translate,
            "subtract_pairs": n_subtract, "snap_floaters": n_snap,
            "blocked": len(blocked or []), "predicted_bad_counts": pred,
            "note": "预测只按“一个动作消一对”扣减，未计新冲突/采样误差；实际以 dry_run=false 复扫为准"}


def _repair_rescan(names, m, spec):
    try:
        raw = asm_scan(scope=None, objects=list(names), manifest=m, spec=spec, summary_only=False)
        return json.loads(raw) if isinstance(raw, str) else raw
    except Exception as e:
        import traceback
        return {"ok": False, "error": "%s: %s" % (type(e).__name__, str(e)[:240]),
                "traceback": traceback.format_exc()[-500:]}


def _apply_repair_action(act, objs, allow_boolean):
    """执行单个修复动作（translate/snap 平移；subtract 布尔差）。返回逐动作记录，不抛。"""
    if not isinstance(act, dict):
        return {"ok": False, "error": "动作不是 dict", "action": str(act)[:160]}
    method = str(act.get("method") or "")
    if method in ("translate", "snap"):
        targets = [str(act.get("target"))] if method == "translate" \
            else [str(x) for x in (act.get("objects") or []) if x]
        dv = act.get("delta_units") or []
        if len(dv) != 3 or any(_num(x) is None for x in dv):
            return {"ok": False, "method": method, "error": "delta_units 不是 3 个数字",
                    "action": act}
        vec = Vector((float(dv[0]), float(dv[1]), float(dv[2])))
        moved = []
        for n in targets:
            ob = objs.get(n) or bpy.data.objects.get(n)
            if ob is None:
                return {"ok": False, "method": method, "error": "对象不存在: %s" % n, "action": act}
            try:
                ob.matrix_world.translation = ob.matrix_world.translation + vec
            except Exception as e:
                return {"ok": False, "method": method, "target": n,
                        "error": "%s: %s" % (type(e).__name__, str(e)[:160]), "action": act}
            moved.append(n)
        return {"ok": True, "method": method, "objects": moved, "pair": act.get("pair"),
                "target": act.get("target"), "delta_units": [_r(x, 8) for x in dv],
                "delta_mm": act.get("delta_mm")}
    if method == "subtract":
        if not allow_boolean:
            return {"ok": False, "method": "subtract", "pair": act.get("pair"),
                    "error": "allow_boolean=false，拒绝布尔（拓扑改动需显式允许）"}
        geo = _api("geo")
        bfn = None
        try:
            bfn = geo.get("boolean") if geo is not None else None
        except Exception:
            bfn = None
        if not callable(bfn):
            return {"ok": False, "method": "subtract", "pair": act.get("pair"),
                    "error": "K.dsh_geo_api.boolean 不可用（在线路径 preload 里带 geo）"}
        t = bpy.data.objects.get(str(act.get("target") or ""))
        c = bpy.data.objects.get(str(act.get("cutter") or ""))
        if t is None or c is None:
            return {"ok": False, "method": "subtract", "pair": act.get("pair"),
                    "error": "target/cutter 对象不存在（target=%r cutter=%r）"
                             % (act.get("target"), act.get("cutter"))}
        dup, dup_mesh = None, None
        try:
            dup = c.copy()
            if c.data is not None:
                dup.data = c.data.copy()
                dup_mesh = dup.data
            dup.name = "%s__dsh_repair_cut" % str(c.name)[:48]
            coll = (t.users_collection[0] if t.users_collection else bpy.context.scene.collection)
            coll.objects.link(dup)
        except Exception as e:
            if dup is not None:
                try:
                    bpy.data.objects.remove(dup, do_unlink=True)
                except Exception:
                    pass
            if dup_mesh is not None and dup_mesh.users == 0:
                try:
                    bpy.data.meshes.remove(dup_mesh)
                except Exception:
                    pass
            return {"ok": False, "method": "subtract", "pair": act.get("pair"),
                    "error": "复制 cutter 失败: %s: %s" % (type(e).__name__, str(e)[:160])}
        try:
            bfn(t, dup, "DIFFERENCE")
        except Exception as e:
            try:
                if bpy.data.objects.get(dup.name) is not None:
                    bpy.data.objects.remove(dup, do_unlink=True)
            except Exception:
                pass
            if dup_mesh is not None and dup_mesh.users == 0:
                try:
                    bpy.data.meshes.remove(dup_mesh)
                except Exception:
                    pass
            return {"ok": False, "method": "subtract", "target": str(t.name), "cutter": str(c.name),
                    "error": "%s: %s" % (type(e).__name__, str(e)[:240]),
                    "hint": "布尔退化守卫拦下（自交/非流形主体）→ 先 fix_repair 或改用平移"}
        try:
            if bpy.data.objects.get(dup.name) is not None:
                bpy.data.objects.remove(dup, do_unlink=True)
        except Exception:
            pass
        if dup_mesh is not None and dup_mesh.users == 0:
            try:
                bpy.data.meshes.remove(dup_mesh)
            except Exception:
                pass
        return {"ok": True, "method": "subtract", "target": str(t.name),
                "cutter": str(c.name), "pair": act.get("pair")}
    return {"ok": False, "method": method or "?", "error": "未知修复方法（只支持 translate/snap/subtract）"}


def _repair_params(dry_run, max_move_mm, allow_boolean, snapshot, max_rounds, clearance_mm, policy):
    """校验布尔/数字参数与 policy；返回 (cfg, err)。"""
    dry_run, ferr = _repair_flag(dry_run, "dry_run")
    if ferr:
        return None, ferr
    allow_boolean, ferr = _repair_flag(allow_boolean, "allow_boolean")
    if ferr:
        return None, ferr
    snapshot, ferr = _repair_flag(snapshot, "snapshot")
    if ferr:
        return None, ferr
    pol, pol_explicit, perr = _repair_policy(policy)
    if perr:
        return None, perr
    mv, cm = _num(max_move_mm), _num(clearance_mm)
    if mv is None or mv < 0.0:
        return None, "max_move_mm 必须是 ≥0 的有限数字，收到 %r" % (max_move_mm,)
    if cm is None or cm < 0.0:
        return None, "clearance_mm 必须是 ≥0 的有限数字，收到 %r" % (clearance_mm,)
    try:
        rounds_f = float(max_rounds)
    except Exception:
        rounds_f = None
    if isinstance(max_rounds, bool) or rounds_f is None or not math.isfinite(rounds_f) \
            or abs(rounds_f - round(rounds_f)) > 1e-9:
        return None, "max_rounds 必须是整数（1..20），收到 %r" % (max_rounds,)
    rounds_cap = int(round(rounds_f))
    if rounds_cap < 1 or rounds_cap > 20:
        return None, "max_rounds 必须在 1..20，收到 %r" % (max_rounds,)
    return {"dry_run": bool(dry_run), "allow_boolean": bool(allow_boolean), "snapshot": bool(snapshot),
            "pol": pol, "pol_explicit": bool(pol_explicit), "mv": mv, "cm": cm,
            "rounds_cap": rounds_cap}, None


def _repair_context(manifest, scope, objects, spec):
    """manifest 校验 + 选对象 + 首次 asm_scan + 网格映射/基线摘要；返回 (ctx, err)。"""
    m = None
    if manifest is not None:
        m, merr = _load_manifest(manifest)
        if merr:
            return None, merr
        mval = _manifest_validate(m, check_files=False, check_md5=False)
        if not mval["ok"]:
            return None, ("manifest 结构校验失败（坏清单上不规划修复）",
                          {"errors": mval["errors"][:10], "warnings": mval["warnings"][:10]})
    info = _pick_objects(scope, objects, include_hidden=False)
    if info.get("error"):
        return None, (info["error"], {"objects": info.get("names"), "missing": info.get("missing"),
                                      "hint": "空产出/坏参数必须失败；确认对象名/集合名（或给 manifest）"})
    names = list(info["names"])
    try:
        raw = asm_scan(scope=None, objects=names, manifest=m, spec=spec, summary_only=False)
        scan = json.loads(raw) if isinstance(raw, str) else raw
    except Exception as e:
        return None, "首次 asm_scan 异常: %s: %s" % (type(e).__name__, str(e)[:240])
    if not isinstance(scan, dict) or not scan.get("ok"):
        return None, ("首次 asm_scan 失败，拒绝在没扫描结果时规划修复",
                      {"scan": _trim(scan) if isinstance(scan, dict) else None})
    mmu = float(_KIT.units())
    md, md_errors = _mesh_map(info["objects"])
    before = _repair_brief(scan)
    before_bad = _repair_bad_total(before)
    return {"manifest": m, "objects": info["objects"], "names": names, "scan": scan,
            "mmu": mmu, "md": md, "md_errors": md_errors,
            "before": before, "before_bad": before_bad}, None


def _repair_counts_line(before):
    """扫前计数一行（verdict_line 用）。"""
    brief_counts = before.get("counts") or {}
    return ("clash=%d contained=%d float=%d unresolved=%d"
            % (int(brief_counts.get("clash") or 0), int(brief_counts.get("contained") or 0),
               int(brief_counts.get("float") or 0), int(brief_counts.get("unresolved") or 0)))


def _repair_params_out(cfg):
    """对外回执里的 params 字段（保持既有键序/取值）。"""
    return {"max_move_mm": _r(cfg["mv"], 4), "clearance_mm": _r(cfg["cm"], 4),
            "allow_boolean": cfg["allow_boolean"], "max_rounds": cfg["rounds_cap"],
            "snapshot": cfg["snapshot"], "dry_run": cfg["dry_run"], "policy": cfg["pol"],
            "policy_explicit": cfg["pol_explicit"]}


def _repair_dry_payload(ctx, params, plan, blocked, expected, counts_line):
    """dry_run=true 只读回执。"""
    before = ctx["before"]
    before_bad = ctx["before_bad"]
    names = ctx["names"]
    md_errors = ctx["md_errors"]
    if plan:
        verdict = "planned"
    elif before_bad:
        verdict = "blocked"
    else:
        verdict = before.get("verdict") or "pass"
    if verdict == "pass" and before.get("verdict") == "degraded":
        verdict = "degraded"
    if plan and blocked:
        hint = "dry_run 只规划（场景未改）：%d 个动作可执行、%d 项 blocked —— 按 blocked.reason 处理后重跑，确认后传 dry_run=false（会先 snapshot）"
    elif plan:
        hint = "dry_run 只规划（场景未改）：确认后传 dry_run=false 执行（先文件级 snapshot，再 apply，最后复扫）"
    elif before_bad:
        hint = "没有可自动修的动作：看 blocked.reason（调 max_move_mm / allow_boolean / 清父级 / preload audit / 先修网格）"
    elif before.get("verdict") == "degraded":
        hint = ("无 clash/contained/float 可自动修，但扫描是 degraded（unresolved/截断/未判完）—— "
                "先修网格/法线或调预算重扫，绝不读成没问题")
    else:
        hint = "扫描无 clash/contained/float，无需修复"
    verdict_line = ("装配修复（dry_run，只读）：扫 %d 件 %s → 规划 %d 个动作 / blocked %d → %s"
                    % (len(names), counts_line, len(plan), len(blocked), verdict))
    return {"ok": True, "verdict": verdict, "dry_run": True, "readonly": True,
            "stage": "planned", "method": "asm_repair", "aliases": ["fix_clash"],
            "params": params, "objects": len(names), "object_names": names,
            "before": _repair_brief_out(before),
            "plan": plan[:60], "blocked": _dedupe_blocked(blocked)[:40],
            "expected": expected,
            "verdict_line": verdict_line, "hint": hint,
            "honest_limits": list(_REPAIR_HONEST_LIMITS),
            "md_errors": md_errors, "contract_repairs": _plan_repair_hints(plan)}


def _repair_manual_payload(ctx, params, plan, blocked, expected):
    """policy=manual：只出计划，绝不自动执行。"""
    before = ctx["before"]
    md_errors = ctx["md_errors"]
    return {"ok": False, "verdict": "planned", "dry_run": True, "readonly": True,
            "stage": "manual_policy", "method": "asm_repair", "aliases": ["fix_clash"],
            "params": params, "objects": len(ctx["names"]),
            "before": _repair_brief_out(before),
            "plan": plan[:60], "blocked": _dedupe_blocked(blocked)[:40],
            "expected": expected,
            "error": "policy=manual 只出计划、绝不自动执行（场景未改）；确认后改用 policy=safe/aggressive 或人工按 plan 执行",
            "verdict_line": "装配修复（manual，只读）：规划 %d 个动作 / blocked %d → 未改场景"
                            % (len(plan), len(blocked)),
            "hint": "policy=manual 不自动执行：translate/snap 用 policy=safe；布尔用 policy=aggressive + allow_boolean=true",
            "honest_limits": list(_REPAIR_HONEST_LIMITS), "md_errors": md_errors}


def _repair_no_plan_payload(ctx, params, blocked):
    """执行分支但规划为空：要么本来就没事，要么没有可自动执行的动作。"""
    before = ctx["before"]
    before_bad = ctx["before_bad"]
    names = ctx["names"]
    md_errors = ctx["md_errors"]
    if before_bad == 0:
        return {"ok": True, "verdict": before.get("verdict") or "pass", "dry_run": False,
                "analyzed": True, "stage": "nothing_to_do", "params": params,
                "objects": len(names), "before": _repair_brief_out(before),
                "after": _repair_brief_out(before), "residual": _repair_residual(before),
                "repaired": False, "rounds": 0, "plan": [], "applied": [],
                "blocked": _dedupe_blocked(blocked)[:40], "snapshot": None,
                "verdict_line": "装配修复：扫描已无 clash/contained/float，无需修复",
                "hint": "无需修复（如仍有 unresolved/degraded，先修网格/法线再看）",
                "honest_limits": list(_REPAIR_HONEST_LIMITS), "md_errors": md_errors}
    return {"ok": False, "verdict": "blocked", "dry_run": False, "analyzed": True,
            "stage": "no_executable_action", "params": params,
            "objects": len(names), "before": _repair_brief_out(before),
            "after": _repair_brief_out(before), "residual": _repair_residual(before),
            "repaired": False, "rounds": 0, "plan": [], "applied": [],
            "blocked": _dedupe_blocked(blocked)[:40], "snapshot": None,
            "error": "扫描到 %d 处 clash/contained/float，但没有可自动执行的动作" % before_bad,
            "verdict_line": "装配修复：0 个可执行动作 → blocked（场景未改）",
            "hint": "看 blocked.reason：调 max_move_mm / allow_boolean / 清父级动画 / preload audit / 先修网格",
            "honest_limits": list(_REPAIR_HONEST_LIMITS), "md_errors": md_errors}


def _repair_snapshot(snapshot):
    """执行前文件级快照；返回 (snap, snap_ok, err)。快照失败必须拒绝执行。"""
    if not snapshot:
        return ({"ok": True, "skipped": True,
                 "note": "snapshot=false：调用方自担回退（布尔拓扑改动对象级回退不回来）"}, False, None)
    if _api("txn") is None:
        snap = {"ok": False, "skipped": True,
                "note": "K.dsh_txn_api 不可用 ⇒ 没做文件级快照（布尔动作会被拒绝；平移失败无法自动回退）"}
    else:
        rec, serr = _call_api("txn", "snapshot",
                              {"label": "asm_repair-%s" % time.strftime("%Y%m%d_%H%M%S"),
                               "note": "asm_repair(dry_run=false) 前快照"})
        if serr:
            return None, False, "快照失败，拒绝在无回退点的场景上执行修复：%s" % serr
        snap = rec.get("snapshot") if isinstance(rec, dict) else rec
    snap_ok = bool(isinstance(snap, dict) and not snap.get("skipped")
                   and snap.get("ok") is not False
                   and bool(snap.get("label") or snap.get("path") or snap.get("ok") is True))
    return snap, snap_ok, None


def _repair_guard_subtract(plan, blocked, snap_ok):
    """布尔改拓扑但无可用快照 ⇒ 把 subtract 动作降级为 blocked（不完全放弃其余动作）。"""
    if snap_ok or not any(a.get("method") == "subtract" for a in plan):
        return plan, blocked
    keep = []
    exec_blocked = list(blocked)
    for a in plan:
        if a.get("method") == "subtract":
            exec_blocked.append({"pair": a.get("pair"), "class": a.get("class"),
                                 "method": "subtract",
                                 "reason": "布尔改拓扑但没有可用的文件级快照（txn）→ 拒绝执行；先让 K.dsh_txn_api 可用且 snapshot=true"})
        else:
            keep.append(a)
    return keep, exec_blocked


def _repair_blocked_all_payload(ctx, params, blocked, snap):
    """安全门把全部动作拦下：blocked（场景未改）。"""
    before = ctx["before"]
    names = ctx["names"]
    md_errors = ctx["md_errors"]
    return {"ok": False, "verdict": "blocked", "dry_run": False, "analyzed": True,
            "stage": "no_executable_action", "params": params,
            "objects": len(names), "before": _repair_brief_out(before),
            "after": _repair_brief_out(before), "residual": _repair_residual(before),
            "repaired": False, "rounds": 0, "plan": [], "applied": [],
            "blocked": _dedupe_blocked(blocked)[:40], "snapshot": snap,
            "error": "可执行动作都被安全门拦下（布尔需要文件级快照）",
            "verdict_line": "装配修复：0 个可执行动作 → blocked（场景未改）",
            "hint": "preload txn 或 snapshot=true 后重跑；或先处理 blocked.reason",
            "honest_limits": list(_REPAIR_HONEST_LIMITS), "md_errors": md_errors}


def _repair_exec_loop(scan, plan, objs, names, m, spec, rounds_cap, bool_exec,
                      mmu, mv, cm, allow_boolean, pol, pol_explicit, blocked, md_errors):
    """逐轮 apply → update → 复扫 → 重规划；返回 (cur_scan, applied, blocked, rounds, md_errors)。"""
    applied = []
    exec_blocked = list(blocked)
    cur_scan, cur_plan, rounds = scan, plan, 0
    for rnd in range(1, rounds_cap + 1):
        acts = [a for a in cur_plan if isinstance(a, dict) and a.get("method") in _REPAIR_METHODS]
        if not acts:
            break
        rounds = rnd
        n_ok = 0
        for act in acts:
            rec = _apply_repair_action(act, objs, bool_exec)
            rec["round"] = rnd
            if rec.get("ok"):
                applied.append(rec)
                n_ok += 1
            else:
                exec_blocked.append(rec)
        if n_ok == 0:
            break  # 什么都没动 ⇒ 复扫不会变，不空转
        try:
            bpy.context.view_layer.update()
        except Exception:
            pass
        rescan = _repair_rescan(names, m, spec)
        cur_scan = rescan
        brief = _repair_brief(rescan)
        if not brief.get("ok"):
            exec_blocked.append({"kind": "rescan", "round": rnd,
                                 "reason": "复扫失败：%s" % str(brief.get("error"))[:240]})
            break
        if _repair_bad_total(brief) == 0:
            break
        if rnd >= rounds_cap:
            break
        md, md_errors = _mesh_map([objs[nm] for nm in names if nm in objs])
        cur_plan, more_blocked = _repair_plan_all(rescan, names, md, mmu, mv, cm, bool(allow_boolean),
                                                 pol, pol_explicit)
        exec_blocked.extend(more_blocked)
    return cur_scan, applied, exec_blocked, rounds, md_errors


def _repair_final_payload(ctx, params, plan, applied, blocked, cur_scan, rounds, snap, counts_line, md_errors):
    """复扫后的最终回执（verdict 三态 + 残留计数 + 修复建议）。"""
    before = ctx["before"]
    before_bad = ctx["before_bad"]
    names = ctx["names"]
    scan = ctx["scan"]
    after = _repair_brief(cur_scan if isinstance(cur_scan, dict) else scan)
    final_bad = _repair_bad_total(after)
    residual = _repair_residual(after)
    rescan_broke = any(isinstance(x, dict) and x.get("kind") == "rescan" for x in blocked)
    ok_final = bool(final_bad == 0 and after.get("ok") and not rescan_broke)
    if final_bad > 0:
        verdict = "fail"
    elif not after.get("ok"):
        verdict = "degraded"
    else:
        verdict = after.get("verdict") or "degraded"
    if ok_final:
        hint = "修复完成：clash/contained/float 已清零（复扫 verdict=%s）；建议再跑 asm_gate/asm_scan 复核" % verdict
    else:
        hint = ("修复未完成：残留 clash=%d contained=%d float=%d；看 blocked 与 after.offenders，"
                "调 max_move_mm / allow_boolean / 清父级动画 / 先修网格"
                % (residual["clash"], residual["contained"], residual["float"]))
    verdict_line = ("装配修复（已执行 %d 轮）：扫 %d 件 %s → 应用 %d 个动作，blocked %d → 复扫残留 "
                    "clash=%d contained=%d float=%d unresolved=%d → %s"
                    % (rounds, len(names), counts_line, len(applied),
                       len(_dedupe_blocked(blocked)), residual["clash"], residual["contained"],
                       residual["float"], residual["unresolved"], verdict))
    return {"ok": bool(ok_final), "verdict": verdict, "dry_run": False, "analyzed": True,
            "stage": "rechecked", "method": "asm_repair", "aliases": ["fix_clash"],
            "params": params, "objects": len(names), "object_names": names,
            "before": _repair_brief_out(before), "after": _repair_brief_out(after),
            "residual": residual,
            "repaired": bool(before_bad > 0 and final_bad < before_bad),
            "rounds": rounds, "plan": plan[:60],
            "applied": applied[:60], "blocked": _dedupe_blocked(blocked)[:40],
            "snapshot": snap, "verdict_line": verdict_line, "hint": hint,
            "honest_limits": list(_REPAIR_HONEST_LIMITS), "md_errors": md_errors}


def asm_repair(manifest=None, scope=None, objects=None, spec=None, dry_run=True,
               max_move_mm=50.0, allow_boolean=False, snapshot=True,
               max_rounds=3, clearance_mm=0.5, policy=None):
    """装配修复（默认只规划）：asm_scan → 规划 →（dry_run=false）snapshot → apply → 复扫，最多 max_rounds 轮。

    规划口径：
      * clash / contained → 选体积小的件沿 AABB 交叠最小的轴平移（按 AABB 分离所需量 + clearance_mm），
        超过 max_move_mm ⇒ blocked；目标件有父级/动画 ⇒ blocked（整件平移会被覆盖）。
      * float → 有 K.dsh_audit_api 时调 audit_snap_floaters(dry_run=true) 拿计划，否则 blocked。
      * allow_boolean=true 且两件都闭合流形（且平移不可行 / 被父级挡住）→ 从较大件布尔减较小件
        （cutter 用副本，原件不动）；没有可用文件级快照时拒绝执行布尔。
    策略 policy（缺省等价 safe；显式传值才有硬约束）：
      * 缺省 None → safe：自动动作只允许 translate/snap；布尔是拓扑改动，保留旧兼容路径
        （allow_boolean=true = 显式人工授权才做）。
      * policy="safe"（显式）：**硬禁**布尔/scale，只允许 translate/snap。
      * policy="manual"：只规划、绝不自动执行（dry_run=false 也只回只读计划）；不规划布尔。
      * policy="aggressive"：保留旧 allow_boolean 行为，plan 标 policy=aggressive。
    执行口径：dry_run=false 先文件级 snapshot（K.dsh_txn_api），逐动作 apply → view_layer.update →
    重跑 asm_scan；复扫仍有残留就按新扫描再规划，最多 max_rounds 轮（不动就停，不空转）。
    别名：fix_clash（执行器，dry_run 默认 true）；纯规划器用 fix_interference（plan_only=true）。
    """
    t0 = time.perf_counter()
    cfg, perr = _repair_params(dry_run, max_move_mm, allow_boolean, snapshot, max_rounds,
                               clearance_mm, policy)
    if perr:
        return _err(perr)
    dry_run, allow_boolean, snapshot = cfg["dry_run"], cfg["allow_boolean"], cfg["snapshot"]
    ctx, cerr = _repair_context(manifest, scope, objects, spec)
    if cerr:
        if isinstance(cerr, tuple):
            return _err(cerr[0], **cerr[1])
        return _err(cerr)
    names, m = ctx["names"], ctx["manifest"]
    scan, mmu, md = ctx["scan"], ctx["mmu"], ctx["md"]
    before, before_bad, md_errors = ctx["before"], ctx["before_bad"], ctx["md_errors"]
    params = _repair_params_out(cfg)
    plan, blocked = _repair_plan_all(scan, names, md, mmu, cfg["mv"], cfg["cm"], allow_boolean,
                                     cfg["pol"], cfg["pol_explicit"])
    expected = _repair_expected(before, plan, blocked)
    counts_line = _repair_counts_line(before)
    if dry_run:
        return _j(_receipt(_repair_dry_payload(ctx, params, plan, blocked, expected, counts_line), t0))
    if cfg["pol"] == "manual":
        return _j(_receipt(_repair_manual_payload(ctx, params, plan, blocked, expected), t0))
    # ---------------- 执行 ----------------
    bool_exec = bool(allow_boolean) and cfg["pol"] != "manual"
    if not plan:
        return _j(_receipt(_repair_no_plan_payload(ctx, params, blocked), t0))
    snap, snap_ok, serr = _repair_snapshot(snapshot)
    if serr:
        return _err(serr)
    plan, blocked = _repair_guard_subtract(plan, blocked, snap_ok)
    if not plan:
        return _j(_receipt(_repair_blocked_all_payload(ctx, params, blocked, snap), t0))
    objs = {str(o.name): o for o in ctx["objects"]}
    cur_scan, applied, blocked, rounds, md_errors = _repair_exec_loop(
        scan, plan, objs, names, m, spec, cfg["rounds_cap"], bool_exec,
        mmu, cfg["mv"], cfg["cm"], allow_boolean, cfg["pol"], cfg["pol_explicit"], blocked, md_errors)
    return _j(_receipt(_repair_final_payload(ctx, params, plan, applied, blocked, cur_scan, rounds,
                                             snap, counts_line, md_errors), t0))


def _plan_repair_hints(plan):
    """dry_run 回执附带的下一步（弱模型照着调即可）。"""
    hints = []
    for a in plan or []:
        if not isinstance(a, dict):
            continue
        m = a.get("method")
        if m == "subtract":
            hints.append({"op": "fix_clash",
                          "args": {"a": (a.get("pair") or [None, None])[0],
                                   "b": (a.get("pair") or [None, None])[1],
                                   "allow_boolean": True, "policy": "aggressive", "dry_run": False},
                          "why": a.get("why")})
        elif m == "translate":
            hints.append({"op": "fix_clash",
                          "args": {"a": (a.get("pair") or [None, None])[0],
                                   "b": (a.get("pair") or [None, None])[1],
                                   "policy": "safe", "dry_run": False},
                          "why": a.get("why")})
        elif m == "snap":
            hints.append({"op": "fix_clash",
                          "args": {"objects": a.get("objects"), "policy": "safe", "dry_run": False},
                          "why": a.get("why")})
    return hints[:10]


def fix_interference(a=None, b=None, plan_only=True, manifest=None, scope=None, objects=None,
                     spec=None, max_move_mm=50.0, allow_boolean=False, clearance_mm=0.5,
                     max_rounds=3, policy=None):
    """装配干涉**规划器**（只读，永不改场景）：asm_repair(dry_run=true) 的规划视图。

    a/b 给了就只看牵涉这两件（或其中一件）的计划；不给就规划整个 scope。
    policy：safe（默认，自动只用 translate/snap）/ manual（只规划）/ aggressive（布尔受 allow_boolean 控制）。
    执行请用 fix_clash（= asm_repair，dry_run=false）；本 op 拒绝 plan_only=false。
    """
    t0 = time.perf_counter()
    po, ferr = _repair_flag(plan_only, "plan_only")
    if ferr:
        return _err(ferr)
    if not po:
        return _err("fix_interference 是纯规划器（plan_only 只能为 true，永不改场景）；"
                    "要执行用 fix_clash / asm_repair（dry_run=false）")
    scan_objects = objects
    want = set()
    for x in (a, b):
        if x is not None and str(x).strip():
            want.add(str(x))
    if len(want) >= 2 and objects is None and scope is None:
        scan_objects = sorted(want)   # 只看这一对：扫描成本最小
    res = json.loads(asm_repair(manifest=manifest, scope=scope, objects=scan_objects, spec=spec,
                                dry_run=True, max_move_mm=max_move_mm,
                                allow_boolean=allow_boolean, snapshot=False,
                                max_rounds=max_rounds, clearance_mm=clearance_mm, policy=policy))
    if not isinstance(res, dict):
        return _err("asm_repair 回执异常（不是 dict）")
    if not res.get("ok"):
        return _j(res)
    if want:
        res["plan"] = [r for r in (res.get("plan") or []) if want & _repair_touched(r)]
        res["blocked"] = [r for r in (res.get("blocked") or []) if want & _repair_touched(r)]
        res["filtered_to"] = sorted(want)
        res["expected"] = _repair_expected(res.get("before") or {}, res.get("plan") or [],
                                           res.get("blocked") or [])
        if not res["plan"] and res.get("verdict") == "planned":
            res["verdict"] = "blocked"
    res["planner"] = "fix_interference"
    res["readonly"] = True
    res["executor"] = "fix_clash（asm_repair，dry_run=false）"
    return _j(_receipt(res, t0))
