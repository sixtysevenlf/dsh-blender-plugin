# -*- coding: utf-8 -*-
"""Split from audit.py; behavior preserved by the facade."""
from .common import *  # noqa: F401,F403


def _mesh_totals():
    return {"objects": 0, "tris": 0, "boundary_edges": 0, "nonmanifold_edges": 0, "degenerate_faces": 0,
            "loose_verts": 0, "self_intersections": 0, "normals_inverted": 0, "out_of_bounds": 0,
            "empty_objects": 0, "loose_edges": 0, "normals_unknown": 0, "normals_checked": 0,
            "normals_multi_shell": 0, "analysis_incomplete": 0}


def _mesh_envelope(envelope):
    """解析 envelope；返回 (err_payload_or_None, env)。"""
    if not envelope:
        return None, None
    try:
        e = [float(x) for x in envelope]
        return None, (Vector((min(e[0], e[3]), min(e[1], e[4]), min(e[2], e[5]))),
                      Vector((max(e[0], e[3]), max(e[1], e[4]), max(e[2], e[5]))))
    except Exception as exc:
        return _j({"ok": False, "clean": False, "state": "error", "analyzed": False,
                   "error": "invalid envelope: %s" % exc}), None


def _mesh_loose_edges(me):
    """旧版按 MeshEdge.link_faces 数松边；Blender 5.2 没有该属性则返回 None（后续 bmesh 复算）。"""
    if len(me.edges) == 0:
        return 0
    if hasattr(me.edges[0], "link_faces"):
        return int(sum(1 for e in me.edges if not e.link_faces))
    return None


def _mesh_topology(bm, info, eps_area):
    """bmesh 拓扑计数 + 有符号体积 + 闭合判定。"""
    bm.verts.ensure_lookup_table()
    bm.faces.ensure_lookup_table()
    info["boundary_edges"] = int(sum(1 for e in bm.edges if len(e.link_faces) == 1))
    info["nonmanifold_edges"] = int(sum(1 for e in bm.edges if not e.is_manifold))
    info["degenerate_faces"] = int(sum(1 for f in bm.faces if f.calc_area() <= float(eps_area)))
    info["loose_verts"] = int(sum(1 for v in bm.verts if not v.link_edges))
    # v0.9.6（复审修复）：Blender 5.2 的 MeshEdge **没有** link_faces（上面 hasattr 永远假）⇒
    # loose_edges 恒为 None、汇总恒为 0（"没查"被当成"没有"）。改从 bmesh 实算。
    if info["loose_edges"] is None:
        info["loose_edges"] = int(sum(1 for e in bm.edges if not e.link_faces))
    try:
        vol = float(bm.calc_volume(signed=True))
    except Exception:
        vol = None
    info["signed_volume"] = (round(vol, 6) if vol is not None else None)
    info["closed"] = bool(info["boundary_edges"] == 0 and info["nonmanifold_edges"] == 0)


def _mesh_normals(bm, info):
    """逐连通壳判朝向（聚合有符号体积会被大正向壳+小反向壳抵消）。"""
    try:
        _out, _nstate, _shells, _nreason = _shell_normals(bm)
    except Exception as _ne:
        _out, _nstate, _shells = None, "unknown", []
        _nreason = "壳朝向分析异常（%s: %s）⇒ unknown，不给通过结论" % (type(_ne).__name__, str(_ne)[:90])
    if not info["closed"]:
        _out, _nstate = None, "n/a"        # 开放网格：整体朝向无意义
        _nreason = ""
    info["normals_state"] = _nstate
    # v2.6（本轮反馈 #5）：多壳但各壳朝向可确证 —— 独立第五态，不降级
    info["multi_shell"] = bool(_nstate == "pass_multi_shell")
    info["normals_outward"] = _out
    info["normals_shell_count"] = len(_shells or [])
    info["normals_reason"] = _nreason or ""
    if _nstate != "n/a":
        info["normals_shells"] = _shells_brief(_shells)


def _mesh_island_split(bm, hit, info, island_split, min_island_verts, max_issues):
    """连通岛分栏（同岛/跨岛）+ 小岛过滤；只在检测成功后调用。"""
    if not (island_split or int(min_island_verts or 0) > 0):
        return
    # 连通岛：边连接的顶点并查集（一个"岛"= 一块连通的壳；铆钉/小五金各自成岛）
    nv = len(bm.verts)
    par = list(range(nv))

    def _find(x):
        while par[x] != x:
            par[x] = par[par[x]]
            x = par[x]
        return x

    for e in bm.edges:
        a0, b0 = e.verts[0].index, e.verts[1].index
        ra, rb = _find(a0), _find(b0)
        if ra != rb:
            par[rb] = ra
    size = {}
    for i in range(nv):
        r = _find(i)
        size[r] = size.get(r, 0) + 1
    isl_of_face = {}
    for f in bm.faces:
        isl_of_face[f.index] = _find(f.verts[0].index)
    thr = int(min_island_verts or 0)
    same = cross = filt = small_isl = 0
    for rr in size.values():
        if thr and rr < thr:
            small_isl += 1
    kept = []
    for rec in hit:
        ia = isl_of_face.get(rec[0])
        ib = isl_of_face.get(rec[1])
        sza = size.get(ia, 0)
        szb = size.get(ib, 0)
        is_same = (ia == ib)
        if is_same:
            same += 1
        else:
            cross += 1
        drop = bool(thr and (sza < thr or szb < thr))
        if drop:
            filt += 1
        else:
            kept.append(rec + [bool(is_same), int(sza), int(szb)])
    info["islands"] = {"count": len(size), "small_islands": small_isl,
                       "min_island_verts": thr}
    info["self_intersections_same_island"] = same
    info["self_intersections_cross_island"] = cross
    info["self_intersections_filtered_small_island"] = filt
    info["self_intersections_kept"] = len(kept)
    info["self_intersection_pairs"] = kept[:int(max_issues)] if max_issues else []
    info["island_note"] = ("同岛自交=同一块壳自己扎自己（往往是建模事故）；跨岛=两块壳互穿"
                           "（铆钉压入宿主属这一类，按工艺可能是良性的）")


def _mesh_intersections(bm, info, self_intersect, max_issues, min_dist, island_split, min_island_verts):
    """BVH 自交检测（含岛分栏）；异常写 self_intersections_state=error，不读成"没有自交"。"""
    info["self_intersections"] = 0
    info["self_intersection_pairs"] = []
    info["self_intersections_analyzed"] = False
    info["self_intersections_state"] = "skipped"      # 未请求 / 异常 / pass / fail
    info["self_intersection_error"] = None
    if not (self_intersect and len(bm.faces) > 0):
        return
    try:
        from mathutils.bvhtree import BVHTree
        bvh = BVHTree.FromBMesh(bm)
        seen = set()
        hit = []
        for a, b in (bvh.overlap(bvh) or []):
            if a >= b:
                continue
            key = (a, b)
            if key in seen:
                continue
            seen.add(key)
            fa, fb = bm.faces[a], bm.faces[b]
            va = set(v.index for v in fa.verts)
            vb = set(v.index for v in fb.verts)
            if va & vb:
                continue                     # 相邻面共享顶点 → 不算自交
            if (fa.calc_center_median() - fb.calc_center_median()).length < float(min_dist):
                continue                     # 重合面/薄壳抖动 → 由 degenerate 报
            hit.append([a, b, [round(x, 4) for x in fa.calc_center_median()]])
        info["self_intersections"] = len(hit)
        info["self_intersection_pairs"] = hit[:int(max_issues)]
        info["self_intersections_analyzed"] = True
        info["self_intersections_state"] = "fail" if hit else "pass"
        _mesh_island_split(bm, hit, info, island_split, min_island_verts, max_issues)
    except Exception as e:
        # 自交检查**异常**不得读成"没有自交"（原来是静默 0 ⇒ clean=true 的假通过）
        info["self_intersect_error"] = "%s: %s" % (type(e).__name__, str(e)[:100])
        info["self_intersections_analyzed"] = False
        info["self_intersections_state"] = "error"


def _mesh_world_aabb(ob, info, env):
    """世界 AABB / dimensions / 越界判定。"""
    mn, mx = None, None
    for c in ob.bound_box:
        p = ob.matrix_world @ Vector((c[0], c[1], c[2]))
        mn = p.copy() if mn is None else Vector((min(mn[i], p[i]) for i in range(3)))
        mx = p.copy() if mx is None else Vector((max(mx[i], p[i]) for i in range(3)))
    info["aabb"] = {"min": [round(float(x), 4) for x in mn], "max": [round(float(x), 4) for x in mx]}
    info["dimensions"] = [round(float(x), 4) for x in ob.dimensions]
    if env:
        out = [i for i in range(3) if mn[i] < env[0][i] - 1e-6 or mx[i] > env[1][i] + 1e-6]
        info["out_of_bounds"] = bool(out)
        info["out_of_bounds_axes"] = out


def _mesh_object_state(info):
    """逐对象三态：fail（确有缺陷）/ degraded（没查完或判不出来）/ pass。"""
    _incomplete = (not info.get("self_intersections_analyzed")) or info.get("normals_state") == "unknown"
    info["analysis_incomplete"] = bool(_incomplete)
    if any(info.get(k) for k in ("boundary_edges", "nonmanifold_edges", "degenerate_faces", "loose_verts",
                                 "loose_edges", "self_intersections", "empty")):
        info["state"] = "fail"
    elif info.get("normals_outward") is False:
        info["state"] = "fail"
    elif _incomplete:
        info["state"] = "degraded"
    else:
        info["state"] = "pass"
        if info.get("normals_state") == "pass_multi_shell":
            info["state_detail"] = "pass_multi_shell"   # v2.6：pass 的子态，门照常通过


def _mesh_object_info(ob, eps_area, self_intersect, max_issues, min_dist,
                      island_split, min_island_verts, env):
    """单对象体检：拓扑 / 朝向 / 自交 / AABB / 三态。"""
    me = ob.data
    info = {"name": ob.name, "verts": len(me.vertices), "polys": len(me.polygons), "tris": _tris(me),
            "loose_edges": _mesh_loose_edges(me), "empty": bool(len(me.vertices) == 0)}
    bm = bmesh.new()
    try:
        bm.from_mesh(me)
        _mesh_topology(bm, info, eps_area)
        _mesh_normals(bm, info)
        _mesh_intersections(bm, info, self_intersect, max_issues, min_dist, island_split, min_island_verts)
    finally:
        bm.free()
    _mesh_world_aabb(ob, info, env)
    _mesh_object_state(info)
    return info


def _mesh_totals_add(total, info):
    """把一个对象的计数并入汇总。"""
    total["objects"] += 1
    total["tris"] += info["tris"]
    for k in ("boundary_edges", "nonmanifold_edges", "degenerate_faces", "loose_verts", "loose_edges",
              "self_intersections"):
        total[k] += int(info.get(k) or 0)
    if info.get("normals_outward") is False:
        total["normals_inverted"] += 1
    if info.get("normals_state") == "unknown":
        total["normals_unknown"] += 1
    if info.get("normals_state") in ("pass", "pass_multi_shell", "fail"):
        total["normals_checked"] += 1
    if info.get("normals_state") == "pass_multi_shell":
        total["normals_multi_shell"] = int(total.get("normals_multi_shell") or 0) + 1
    if info.get("analysis_incomplete"):
        total["analysis_incomplete"] += 1
    if info.get("empty"):
        total["empty_objects"] += 1
    if info.get("out_of_bounds"):
        total["out_of_bounds"] += 1


def _mesh_summary_state(total):
    """全局三态：clean 的含义是"查完了而且没缺陷"。"""
    bad = (total["boundary_edges"] or total["nonmanifold_edges"] or total["degenerate_faces"]
           or total["loose_verts"] or total["loose_edges"] or total["self_intersections"]
           or total["normals_inverted"] or total["out_of_bounds"] or total["empty_objects"])
    _incomplete = int(total.get("analysis_incomplete") or 0)
    if bad:
        return True, "fail", "确有缺陷（见 totals 里非 0 的计数字段）", _incomplete
    if _incomplete:
        return False, "degraded", ("有 %d 个对象没查完/判不出来（自交检查被跳过或抛异常、壳朝向 unknown）"
                                   "—— 未分析不得读作通过" % _incomplete), _incomplete
    return False, "pass", "", _incomplete


def _mesh_slim_parts(parts):
    """summary_only=true 只回判据字段（去掉明细列表），供验证者/门用。"""
    _keep = ("name", "verts", "polys", "tris", "boundary_edges", "nonmanifold_edges",
             "degenerate_faces", "loose_verts", "loose_edges", "closed", "normals_outward", "normals_state",
             "normals_shell_count", "self_intersections", "self_intersections_analyzed",
             "self_intersections_state", "self_intersections_same_island", "self_intersections_cross_island",
             "self_intersections_filtered_small_island", "self_intersections_kept", "islands",
             "signed_volume", "empty", "out_of_bounds", "state", "multi_shell", "state_detail")
    return [{k: v for k, v in p.items() if k in _keep} for p in parts]


def _mesh_payload(objs, parts, total, missing, env, summary_only, self_intersect):
    """最终回执：verdict/reason/totals/aabb/note + 瘦身与提示。"""
    bad, state, reason, _incomplete = _mesh_summary_state(total)
    mn, mx = _bounds(objs)
    slim = _mesh_slim_parts(parts) if summary_only else parts
    payload = {"ok": True, "clean": bool(not bad and state == "pass"), "state": state, "verdict": state,
               "reason": reason, "count": len(objs), "missing": missing,
               "totals": total, "objects": slim,
               "analysis": {"self_intersections": ("analyzed" if self_intersect else "skipped"),
                            "normals": ("unknown" if total.get("normals_unknown") else
                                        ("checked" if total.get("normals_checked") else "n/a")),
                            "incomplete_objects": _incomplete},
               "aabb": ({"min": [round(float(x), 4) for x in mn],
                         "max": [round(float(x), 4) for x in mx]} if mn else None),
               "envelope": ([[round(float(x), 4) for x in env[0]], [round(float(x), 4) for x in env[1]]]
                            if env else None),
               "note": "判据：boundary/nonmanifold/degenerate/loose_verts/loose_edges/self_intersections/"
                       "normals_inverted/out_of_bounds/empty 任一非 0 → clean=false 且 state=fail；"
                       "normals_outward 逐**连通壳**判（聚合有符号体积会被大正向壳+小反向壳抵消成正数），"
                       "只对闭合网格有意义（closed=true 时才给），判不出来时给 normals_state=unknown；"
                       "多壳但各壳朝向可确证时给 normals_state=pass_multi_shell（不算 degraded：碎块簇/布景多壳件是合法的）；"
                       "自交检查被跳过（self_intersect=false）或抛异常 ⇒ state=degraded，clean=false —— 不假通过"}
    if state == "degraded":
        payload["warn"] = "DEGRADED ≠ 通过：" + reason
        if not self_intersect:
            payload["hint"] = "要完整结论请用 self_intersect=true 重跑（本门的自交检查被显式跳过）"
    if summary_only:
        payload["slim"] = {"summary_only": True}
    elif len(_j(payload)) > 6000:
        payload["hint"] = ("回执较大（%d 字符）：要精简用 summary_only=true（只回判据字段），"
                           "或 audit_scene(top_k=N) 只看最脏的几个" % len(_j(payload)))
    return payload


def audit_mesh(objects=None, scope=None, envelope=None, eps_area=1e-9, self_intersect=True,
               max_issues=20, min_dist=1e-6, summary_only=False,
               island_split=False, min_island_verts=0):
    """v0.9.6（现场反馈 #1）：island_split=true 时把自交按**连通岛**分栏（同岛/跨岛），
    min_island_verts=N 时忽略"任一侧面属于 <N 顶点小岛"的相交 —— 铆钉/小五金按工艺压入宿主
    必然相交，那是良性的；这一栏就是现场手工做过的归因实验（肩甲 1640 对里大部分是这种）。"""
    """单对象/一组对象的网格体检。envelope=[x0,y0,z0,x1,y1,z1] 时给 out_of_bounds。"""
    objs, missing, err = _pick(objects, scope)
    if err:
        return _j({"ok": False, "error": err})
    objs = [o for o in objs if o.type == "MESH"]
    if not objs:
        # ★ 硬规则：0 个 mesh 必须失败（外部反馈的"静默空产出"）
        return _j({"ok": False, "error": "体检对象里 0 个 mesh", "objects": objects, "scope": scope,
                   "hint": "多半是 build 空跑了：确认对象名/集合名，或先跑 audit_scene 看场景里有什么",
                   "missing": missing})
    err, env = _mesh_envelope(envelope)
    if err:
        return err
    total = _mesh_totals()
    parts = []
    for ob in objs:
        info = _mesh_object_info(ob, eps_area, self_intersect, max_issues, min_dist,
                                 island_split, min_island_verts, env)
        _mesh_totals_add(total, info)
        parts.append(info)
    return _j(_mesh_payload(objs, parts, total, missing, env, summary_only, self_intersect))

def audit_scene(envelope=None, limit=40, eps_area=1e-9, summary_only=False, top_k=None,
                self_intersect=False):
    """全场景体检：先给你"哪几个对象最脏"，再按需 audit_mesh 深挖。

    v0.9.6（瘦身）：summary_only=true 只回判据字段；top_k=N 只回最脏的 N 个（默认沿用 limit）。
    v0.9.6（复审修复）：自交检查**默认不跑**（保持"一次便宜的初筛"这个性能契约），但这时结论只能是
        **degraded**（clean=false）——"跳过检查"不许读成"没问题"。要一条能当门用的完整结论，
        传 self_intersect=true（全场景逐对象跑一遍 BVH 自交，代价按面数走）。
    """
    objs = [o for o in bpy.context.scene.objects if o.type == "MESH"]
    if not objs:
        return _j({"ok": False, "error": "场景里 0 个 mesh（build 空跑？）"})
    rows = []
    r = json.loads(audit_mesh(objects=[o.name for o in objs], envelope=envelope, eps_area=eps_area,
                              self_intersect=bool(self_intersect), max_issues=0))
    tot = r.get("totals", {})
    for info in r.get("objects", []):
        score = (info.get("boundary_edges") or 0) + 3 * (info.get("nonmanifold_edges") or 0) + \
                2 * (info.get("degenerate_faces") or 0) + 2 * (info.get("self_intersections") or 0) + \
                (info.get("loose_verts") or 0) + (info.get("loose_edges") or 0)
        rows.append({"name": info["name"], "tris": info["tris"], "boundary_edges": info.get("boundary_edges"),
                     "nonmanifold_edges": info.get("nonmanifold_edges"),
                     "degenerate_faces": info.get("degenerate_faces"), "closed": info.get("closed"),
                     "loose_verts": info.get("loose_verts"), "loose_edges": info.get("loose_edges"),
                     "self_intersections": info.get("self_intersections"),
                     "normals_outward": info.get("normals_outward"), "normals_state": info.get("normals_state"),
                     "state": info.get("state"), "score": score, "aabb": info.get("aabb")})
    rows.sort(key=lambda x: -x["score"])
    k = int(top_k) if top_k is not None else int(limit)
    rows_out = rows[:max(0, k)]
    # 场景级三态：任何对象 fail ⇒ fail；否则任何"降级"（含主动跳过自交检查）⇒ degraded；否则 pass
    states = [x.get("state") for x in rows]
    if r.get("state") == "fail" or "fail" in states:
        state = "fail"
    elif (not self_intersect) or r.get("state") == "degraded" or "degraded" in states:
        state = "degraded"
    else:
        state = "pass"
    if summary_only:
        _keep = ("name", "score", "boundary_edges", "nonmanifold_edges", "degenerate_faces",
                 "loose_verts", "loose_edges", "self_intersections", "normals_outward", "normals_state",
                 "state", "tris", "closed")
        rows_out = [{kk: vv for kk, vv in row.items() if kk in _keep} for row in rows_out]
    reason = ""
    if state == "degraded":
        reason = ("初筛跳过了自交检查" if not self_intersect else "") + \
                 ("；" if not self_intersect else "") + \
                 ("%d 个对象判不出来（见 worst[].normals_state/state）" % len([x for x in states if x == "degraded"])
                  if any(x == "degraded" for x in states) else "")
        reason = reason or "未查完"
    payload = {"ok": True, "count": len(objs), "totals": tot, "worst": rows_out,
               "clean": bool(r.get("clean") and state == "pass" and self_intersect),
               "state": state, "verdict": state, "reason": reason,
               "self_intersections_analyzed": bool(self_intersect), "aabb": r.get("aabb"),
               "slim": {"summary_only": bool(summary_only), "top_k": k, "rows_returned": len(rows_out)},
               "note": "worst 按 score=boundary+3*nonmanifold+2*degenerate+2*selfint+loose 排序；"
                       "深挖用 audit_mesh(objects=[...])；"
                       "本 op 默认**不跑自交检查** ⇒ verdict=degraded（clean=false）——"
                       "要能当门用的完整结论请传 self_intersect=true；回执太大时加 summary_only=true / top_k=N"}
    if state == "degraded":
        payload["warn"] = "DEGRADED ≠ 通过：" + reason
    return _j(payload)


def _mat_sig(ob):
    """材质签名：节点类型 + 关键输入默认值（粗指纹，用于发现"同名分叉"）。"""
    try:
        nt = ob.node_tree
        if nt is None:
            return "none"
        items = []
        for nd in sorted(nt.nodes, key=lambda n: n.name):
            key = str(nd.type) + ":" + str(getattr(nd, "blend_type", "")) + ":" + str(getattr(nd, "operation", ""))
            vals = []
            for inp in getattr(nd, "inputs", []) or []:
                dv = getattr(inp, "default_value", None)
                try:
                    if hasattr(dv, "__len__"):
                        vals.append(",".join("%.4f" % float(x) for x in dv))
                    else:
                        vals.append("%.4f" % float(dv))
                except Exception:
                    vals.append("-")
            items.append(key + "[" + "|".join(vals[:8]) + "]")
        return "|".join(items)[:600]
    except Exception:
        return "err"


def audit_duplicates(limit=30, top_k=None):
    """同名资源审计：把 Foo / Foo.001 这类分叉分组，并比较内容是否不同。"""
    k = int(top_k) if top_k is not None else int(limit)
    import hashlib
    out = {"ok": True, "materials": [], "meshes": [],
           "note": "同名分叉多为「先到先得」覆盖造成；若内容不同，说明有两份不一样的资源在混用"}
    # 材质
    groups = {}
    for m in bpy.data.materials:
        base = str(m.name).split(".")[0]
        groups.setdefault(("m", base), []).append(m)
    for (kind, base), items in sorted(groups.items()):
        if len(items) < 2:
            continue
        sigs = {}
        for m in items:
            sigs.setdefault(_mat_sig(m), []).append(m.name)
        out["materials"].append({"base": base, "variants": [m.name for m in items], "distinct_signatures": len(sigs),
                                 "users": [int(m.users) for m in items]})
    # 网格
    g2 = {}
    for me in bpy.data.meshes:
        base = str(me.name).split(".")[0]
        g2.setdefault(base, []).append(me)
    for base, items in sorted(g2.items()):
        if len(items) < 2:
            continue
        sigs = {}
        for me in items:
            h = hashlib.md5()
            h.update(("%d/%d" % (len(me.vertices), len(me.polygons))).encode())
            for v in me.vertices[:2000]:
                h.update(("%.5f,%.5f,%.5f;" % (v.co[0], v.co[1], v.co[2])).encode())
            sigs.setdefault(h.hexdigest()[:12], []).append(me.name)
        out["meshes"].append({"base": base, "variants": [m.name for m in items], "distinct_signatures": len(sigs),
                              "users": [int(m.users) for m in items]})
    out["count"] = {"materials": len(out["materials"]), "meshes": len(out["meshes"])}
    # v0.9.6：limit/top_k 原来收了参数却没用（静默失效）—— 现在真正生效并回报被截断的总数
    for _key in ("materials", "meshes"):
        if len(out[_key]) > k:
            out[_key + "_total"] = len(out[_key])
            out[_key] = out[_key][:k]
    out["slim"] = {"top_k": k, "materials": len(out["materials"]), "meshes": len(out["meshes"])}
    out["ok"] = True
    return _j(out)


def purge_orphans():
    """清理 users==0 的 datablock（配套：孤儿灯/相机/网格/材质/图像）。"""
    removed = {}
    targets = [("lights", bpy.data.lights), ("cameras", bpy.data.cameras), ("meshes", bpy.data.meshes),
               ("materials", bpy.data.materials), ("images", bpy.data.images), ("node_groups", bpy.data.node_groups),
               ("collections", bpy.data.collections)]
    for name, coll in targets:
        n = 0
        for db in list(coll):
            try:
                if int(db.users) == 0:
                    coll.remove(db, do_unlink=True)
                    n += 1
            except Exception:
                pass
        if n:
            removed[name] = n
    return _j({"ok": True, "removed": removed, "total": sum(removed.values()),
               "left": {k: len(getattr(bpy.data, k)) for k, _ in targets}})


def audit_selftest():
    """合成自检：造一个"已知脏"的网格，看各项计数是否如实。"""
    me = bpy.data.meshes.new("DSH_AUDIT_SELFTEST")
    bm = bmesh.new()
    bmesh.ops.create_grid(bm, x_segments=2, y_segments=2, size=1.0)     # 开放网格 → 有边界边
    bmesh.ops.create_cube(bm, size=0.5)                                  # 闭合立方体
    bm.to_mesh(me)
    bm.free()
    ob = bpy.data.objects.new("DSH_AUDIT_SELFTEST", me)
    bpy.context.scene.collection.objects.link(ob)
    try:
        r = json.loads(audit_mesh(objects=[ob.name], self_intersect=False))
        empty = json.loads(audit_mesh(objects=["NoSuchObject"]))
        return _j({"ok": True, "boundary_edges": r["totals"]["boundary_edges"],
                   "tris": r["totals"]["tris"], "clean": r["clean"],
                   "empty_must_fail": (empty.get("ok") is False),
                   "expect": "boundary_edges>0（开放网格）且 clean=false；audit_mesh(不存在的名字) 必须 ok=false"})
    finally:
        try:
            bpy.data.objects.remove(ob, do_unlink=True)
            bpy.data.meshes.remove(me, do_unlink=True)
        except Exception:
            pass
