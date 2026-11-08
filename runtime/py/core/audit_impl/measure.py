# -*- coding: utf-8 -*-
"""Split from audit.py; behavior preserved by the facade."""
from .common import *  # noqa: F401,F403
from .conn_helpers import _box_fields, _exc_body, _file_extras, _named_boxes, _nm, _objs_for, _objs_with_optional_file, _r, _units, _world_tris
from .connectivity import _conn_analyze, _gate_from, _snap_pair_mesh

def audit_measure(objects=None, scope=None, include_hidden=False, neighbors=True, k=4, max_pairs=200,
                  file=None):
    """测量包：世界 bbox + 逐轴间隙/重叠（mm 与 %）+ 邻居（契约图 + 空间最近 k）。

    v0.9.1：新增 file=<.blend 路径>（临时加载 → 跑同一条链路 → finally 删净；source/file_load/
    cleanup 上报来源与收尾）。items[].name / pairs[].a|b / neighbors 的键一律是**文件里的原名**。
    老签名与老返回键一个没动，只做加法（新增 object_names / aabb / source / file_load / cleanup）。
    """
    import numpy as np
    S = _objs_with_optional_file(objects, scope, include_hidden, file)
    objs, missing, err, name_of = S["objs"], S["missing"], S["err"], S["name_of"]
    try:
        if err:
            res = {"ok": False, "error": err, "missing": missing, "file": (S["load"] or {}).get("path"),
                   "hint": "file= 模式：文件要能打开、里面有可见 mesh；空产出一律 ok:false"}
        elif not objs:
            res = {"ok": False, "error": "范围内 0 个 mesh", "missing": missing}
        else:
            m_per_unit, mm_per_unit = _units()
            _t, _o, boxes = _world_tris(objs)
            boxes = _named_boxes(objs, boxes, name_of)
            items, good = [], []
            for ob in objs:
                nm = _nm(ob, name_of)
                b = boxes.get(nm)
                if b is None:
                    items.append({"name": nm, "error": "无几何（0 面）"})
                    continue
                lo, hi = [float(x) for x in b[0]], [float(x) for x in b[1]]
                size = [hi[i] - lo[i] for i in range(3)]
                ctr = [(hi[i] + lo[i]) / 2.0 for i in range(3)]
                items.append({"name": nm, "min": [_r(x) for x in lo], "max": [_r(x) for x in hi],
                              "size": [_r(x) for x in size], "size_mm": [_r(x * mm_per_unit, 3) for x in size],
                              "center": [_r(x) for x in ctr], "tris": int(_tris(ob.data))})
                good.append((nm, lo, hi))
            pairs = []
            for i in range(len(good)):
                for j in range(i + 1, len(good)):
                    na, la, ha = good[i]
                    nb, lb, hb = good[j]
                    gaps, overlaps = {}, {}
                    for ax in range(3):
                        g = max(0.0, max(la[ax] - hb[ax], lb[ax] - ha[ax]))
                        ov = min(ha[ax], hb[ax]) - max(la[ax], lb[ax])
                        gaps["xyz"[ax]] = _r(g * mm_per_unit, 4)
                        overlaps["xyz"[ax]] = _r(ov * mm_per_unit, 4)
                    sep = (sum(v * v for v in gaps.values())) ** 0.5
                    pairs.append({"a": na, "b": nb, "gap_mm": _r(sep, 4), "axis_gap_mm": gaps,
                                  "axis_overlap_mm": overlaps,
                                  "contact": bool(sep <= 1e-9),
                                  "overlap_bbox": bool(all(overlaps[a] > 0 for a in "xyz")),
                                  "center_dist_mm": _r(float(np.linalg.norm(np.array(
                                      [(ha[i] + la[i] - hb[i] - lb[i]) / 2.0 for i in range(3)]))) * mm_per_unit, 4)})
                    if len(pairs) >= int(max_pairs):
                        break
                if len(pairs) >= int(max_pairs):
                    break
            pairs.sort(key=lambda p: p["gap_mm"])
            res = {"ok": True, "units": {"m_per_unit": _r(m_per_unit, 6), "mm_per_unit": _r(mm_per_unit, 6)},
                   "count": len(items), "items": items, "missing": missing,
                   "object_names": [_nm(o, name_of) for o in objs],
                   "aabb": (_box_fields(np.array([g[1] for g in good]).min(axis=0),
                                        np.array([g[2] for g in good]).max(axis=0), mm_per_unit) if good else None),
                   "pairs": pairs if pairs else [],
                   "note": "所有 mm 口径都按 units.mm_per_unit 换算；bbox 级（不是三角级）"}
            if neighbors and good:
                # ① 契约图的邻居（若注册过组件/连接）
                decl = {}
                try:
                    K = _kernel()
                    store = getattr(K, "dsh_contract", None) if K is not None else None
                    if store:
                        name2comp = {}
                        for cid, comp in (store.get("components") or {}).items():
                            for n in (comp.get("objects") or []):
                                name2comp.setdefault(n, []).append(cid)
                        for conn in (store.get("connections") or {}).values():
                            for n in (store.get("components", {}).get(conn.get("a"), {}) or {}).get("objects", []) or []:
                                for m in (store.get("components", {}).get(conn.get("b"), {}) or {}).get("objects", []) or []:
                                    if n != m:
                                        decl.setdefault(n, set()).add(m)
                except Exception:
                    decl = {}
                nb = {}
                for name, lo, hi in good:
                    c = np.array([(hi[i] + lo[i]) / 2.0 for i in range(3)])
                    arr = []
                    for nm, l, h in good:
                        if nm == name:
                            continue
                        c2 = np.array([(h[i] + l[i]) / 2.0 for i in range(3)])
                        arr.append((float(np.linalg.norm(c - c2)), nm))
                    arr.sort(key=lambda x: x[0])
                    nb[name] = {"declared": sorted(decl.get(name, [])),
                                "nearest": [{"name": nm, "center_dist_mm": _r(dd * mm_per_unit, 3)}
                                            for dd, nm in arr[:int(k)]]}
                res["neighbors"] = nb
    except Exception as e:
        res = _exc_body(e, "measure")
    finally:
        crep = S["cleanup"]() if S.get("cleanup") else None
    return _j(_file_extras(res, S, crep))


def audit_snap_floaters(objects=None, scope=None, dry_run=True, visible_frac=VISIBLE_SPAN_FRACTION,
                        micro_gap_mm=MICRO_GAP_MM, overlap_mm=0.05, apply_parented=False, max_tris=None):
    """把可见浮块贴到最近邻（默认只报告）。整件平移只对"完全属于该浮块"的对象做，焊接体一块不撕。"""
    import numpy as np
    objs, missing, err = _objs_for(objects, scope, False)
    if err:
        return _j({"ok": False, "error": err})
    if not objs:
        return _j({"ok": False, "error": "范围内 0 个 mesh", "missing": missing})
    a = _conn_analyze(objs, visible_frac=visible_frac, micro_gap_mm=micro_gap_mm, max_tris=max_tris)
    if not a.get("analyzed"):
        return _j({"ok": False, "error": a.get("reason"), "note": a.get("note"),
                   "hint": "先缩小 scope 或提高 env DSH_CONN_MAX_TRIS"})
    _m, mm_per_unit = _units()
    eps = float(overlap_mm) / mm_per_unit
    moves, skipped = [], []
    tree_cache = {}
    for s in a["floaters"]:
        if not s.get("visible"):
            continue
        owners = s["owners"]
        # 该对象是否"整件都在这个浮块里"（否则平移会撕开焊接体 —— Procedura v3 snap 的翻车点）
        obj_tris = a.get("obj_tris") or {}
        whole = [n for n in owners if obj_tris.get(n) and obj_tris.get(n) == owners[n]]
        if not whole:
            skipped.append({"rank": s["rank"], "span_fraction": s["span_fraction"], "gap_mm": s["gap_mm"],
                            "why": "浮块只是某个对象的一部分（焊接体），整件平移会撕开焊点 → 交给编辑那一步处理"})
            continue
        # 朝最近邻分量方向平移：逐轴 gap + 一点重叠量
        nb = None
        for t2 in a["components"]:
            if t2["rank"] == s["rank"]:
                continue
            d2 = 0.0
            for kk in range(3):
                g = max(0.0, max(s["min"][kk] - t2["max"][kk], t2["min"][kk] - s["max"][kk]))
                d2 += g * g
            if nb is None or d2 < nb[0]:
                nb = (d2, t2)
        if nb is None:
            continue
        t2 = nb[1]
        delta = []
        for kk in range(3):
            if s["max"][kk] < t2["min"][kk]:
                delta.append(_r(t2["min"][kk] - s["max"][kk] + eps, 8))
            elif t2["max"][kk] < s["min"][kk]:
                delta.append(_r(-(s["min"][kk] - t2["max"][kk] + eps), 8))
            else:
                delta.append(0.0)
        method = "bbox"
        if all(abs(x) < 1e-12 for x in delta):
            # bbox 读成 0：floaters 里只会出现 attached=False（真浮块）→ 必须走网格级最近点对
            raw = a.get("_raw") or {}
            if not raw:
                skipped.append({"rank": s["rank"], "why": "没有原始三角数据，无法做网格级平移"})
                continue
            dvec, dist_u, note = _snap_pair_mesh(raw["tris"], raw["comp"], s["comp"], tree_cache)
            if not dvec:
                skipped.append({"rank": s["rank"], "span_fraction": s["span_fraction"], "gap_mm": s["gap_mm"],
                                "why": note})
                continue
            scale = 1.0 + (eps / max(float(dist_u), 1e-9))
            delta = [_r(dvec[k] * scale, 8) for k in range(3)]
            method = "mesh:" + note
        entry = {"rank": s["rank"], "objects": whole, "delta_units": delta,
                 "delta_mm": [_r(d * mm_per_unit, 4) for d in delta], "method": method,
                 "gap_before_mm": s["gap_mm"], "target_component_rank": t2["rank"],
                 "span_fraction": s["span_fraction"]}
        blocked = []
        for n in whole:
            ob = bpy.data.objects.get(n)
            if ob is None:
                continue
            if ob.parent is not None and not apply_parented:
                blocked.append("%s 有父级（平移会被父级覆盖）" % n)
            if ob.animation_data is not None:
                blocked.append("%s 有动画数据（关键帧会覆盖平移）" % n)
        if blocked:
            entry["why_not_applied"] = blocked
            skipped.append({"rank": s["rank"], "span_fraction": s["span_fraction"], "gap_mm": s["gap_mm"],
                            "why": "; ".join(blocked)})
        moves.append(entry)
    applied = []
    if not dry_run:
        for mv in moves:
            if mv.get("why_not_applied"):
                continue
            dv = np.array(mv["delta_units"], dtype=np.float64)
            for n in mv["objects"]:
                ob = bpy.data.objects.get(n)
                if ob is None:
                    continue
                ob.matrix_world.translation = ob.matrix_world.translation + Vector(dv)
                applied.append(n)
        try:
            bpy.context.view_layer.update()
        except Exception:
            pass
    return _j({"ok": True, "dry_run": bool(dry_run), "units": a["units"],
               "moves": moves, "skipped": skipped, "applied": applied,
               "before": {"visible_floater_count": a["visible_floater_count"],
                          "real_floater_count": a["real_floater_count"], "gate": _gate_from(a)},
               "hint": ("只报告。" if dry_run else "已平移（只动了整件属于浮块的对象）") +
                       " 应用前建议先 blender_session(action='txn', op='snapshot')；改完用 audit_gate 复核",
               "note": "平移只收口「贴而未重合」；若浮块是对象的一部分，需在编辑那一步改几何"})
