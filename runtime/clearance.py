# -*- coding: utf-8 -*-
"""DSH 成对间隙门：**规则表驱动**的最近距离 + 互穿判定。

规则表：pairs=[{"id","a":[对象],"b":[对象],"min_mm","allow_overlap"}]
  ① 轮/悬挂/灯具这类**不许碰**的：min_mm 给工艺间隙（含行程）；
  ② 板件/蒙皮这类**允许互插**的：allow_overlap=true（互穿是设计要求）。

样板（JSON / API / dispatch / BVH / 采样 / 单位）**全部来自共享内核 kit**（K.dsh_kit）；
本模块只留领域逻辑 —— 这是 S1 转换的第一个样板（252 行 → 约 130 行，回执键与判定逐字节不变）。
"""
import json
import sys

CLEAR_VERSION = 2

_K = sys.modules.get("dsh_rt_kernel")
_KIT = getattr(_K, "dsh_kit", None)
if _KIT is None:
    raise RuntimeError("clearance.py 需要共享内核 K.dsh_kit（由 KERNEL_BOOTSTRAP 注入）")

_j = _KIT.j


def clear_check(pairs=None, samples=1200, seed=0, include_hidden=True):
    """规则表驱动的间隙/互穿判定。返回逐对 gap_mm / interpenetrating / verdict。"""
    mmu = _KIT.units()
    if not pairs:
        raise ValueError("pairs 必填：[{id,a:[对象],b:[对象],min_mm,allow_overlap}]")
    rows, failed = [], []
    for i, spec in enumerate(list(pairs)):
        sp = dict(spec or {})
        pid = str(sp.get("id") or ("pair%d" % i))
        a_names = sp.get("a") or []
        b_names = sp.get("b") or []
        a_names = [a_names] if isinstance(a_names, str) else list(a_names)
        b_names = [b_names] if isinstance(b_names, str) else list(b_names)
        if not a_names or not b_names:
            rows.append({"id": pid, "verdict": "error", "error": "a / b 都要给对象列表"})
            failed.append(pid)
            continue
        objs_a, miss_a = _KIT.objects(a_names)
        objs_b, miss_b = _KIT.objects(b_names)
        miss = miss_a + miss_b
        if miss:
            rows.append({"id": pid, "verdict": "error", "error": "对象不存在：%s" % miss[:4]})
            failed.append(pid)
            continue
        r = _KIT.pairwise_clearance(objs_a, objs_b, samples=samples, seed=seed)
        if r.get("error"):
            rows.append({"id": pid, "verdict": "error", "error": "有一侧没有三角面（空对象？）"})
            failed.append(pid)
            continue
        gap_mm = (r["gap_m"] * mmu) if r.get("gap_m") is not None else None
        inter = bool(r["interpenetrating"])
        n_ov = int(r["overlap_tri_pairs"])
        min_mm = float(sp.get("min_mm") or 0.0)
        allow = bool(sp.get("allow_overlap") or False)
        if inter and not allow:
            verdict, why = "fail", "互穿：%d 对三角面相交" % n_ov
        elif (not inter) and gap_mm is not None and gap_mm < min_mm:
            verdict, why = "fail", "间隙 %.3f mm < 要求 %.3f mm" % (gap_mm, min_mm)
        else:
            verdict = "pass"
            why = ("允许互插（%d 对三角面相交）" % n_ov) if inter else ("间隙 %.3f mm ≥ %.3f mm" % (gap_mm or 0.0, min_mm))
        rows.append({"id": pid, "a": a_names[:6], "b": b_names[:6],
                     "gap_mm": (round(gap_mm, 4) if gap_mm is not None else None),
                     "interpenetrating": bool(inter), "overlap_tri_pairs": n_ov,
                     "min_mm": min_mm, "allow_overlap": allow, "verdict": verdict, "why": why})
        if verdict != "pass":
            failed.append(pid)
    gaps = [r["gap_mm"] for r in rows if r.get("gap_mm") is not None and not r.get("interpenetrating")]
    return _j({"ok": not failed, "verdict": ("pass" if not failed else "fail"), "pairs": rows,
               "pair_count": len(rows), "pairs_failed": failed,
               "min_clearance_mm": (round(min(gaps), 4) if gaps else None),
               "units": {"mm_per_unit": round(mmu, 6)},
               "note": "min_mm 管间隙、allow_overlap 管允许互插（板件）—— 两种规则别用一条阈值盖",
               "honest_limits": ["间隙用采样点量：两个大曲面平行贴合时可能高估（互穿判定精确）",
                                 "只看给定 pairs：没列进来的关系不会被检查"]})


def _box(nm, x, size=20.0):
    import bpy
    import bmesh as _bm
    import mathutils
    me = bpy.data.meshes.new(nm)
    ob = bpy.data.objects.new(nm, me)
    bpy.context.scene.collection.objects.link(ob)
    bm = _bm.new()
    _bm.ops.create_cube(bm, size=1.0, matrix=mathutils.Matrix.Diagonal((size, size, size, 1.0)))
    bm.to_mesh(me)
    bm.free()
    ob.location = (x, 0.0, 0.0)
    return nm


def clear_selftest():
    """自检：10mm 间隙该过 · 2mm 该挂 · 互穿该挂 · 允许互插该过。"""
    import bpy
    prev = float(bpy.context.scene.unit_settings.scale_length)
    made = []
    ev = {}
    try:
        bpy.context.scene.unit_settings.scale_length = 0.001
        a1, b1 = _box("__dsh_cl_a", 0.0), _box("__dsh_cl_b", 30.0)
        a2, b2 = _box("__dsh_cl_c", 0.0), _box("__dsh_cl_d", 22.0)
        a3, b3 = _box("__dsh_cl_e", 0.0), _box("__dsh_cl_f", 10.0)
        made = [a1, b1, a2, b2, a3, b3]
        try:
            bpy.context.view_layer.update()
        except Exception:
            pass
        r = json.loads(clear_check(pairs=[
            {"id": "gap10", "a": [a1], "b": [b1], "min_mm": 5.0},
            {"id": "gap2", "a": [a2], "b": [b2], "min_mm": 5.0},
            {"id": "overlap", "a": [a3], "b": [b3], "min_mm": 1.0},
            {"id": "overlap_ok", "a": [a3], "b": [b3], "allow_overlap": True},
        ]))
        by = {x["id"]: x for x in r.get("pairs", [])}
        ev["gap10"] = {"gap_mm": by["gap10"].get("gap_mm"), "verdict": by["gap10"].get("verdict")}
        ev["gap2"] = {"gap_mm": by["gap2"].get("gap_mm"), "verdict": by["gap2"].get("verdict")}
        ev["overlap"] = {"inter": by["overlap"].get("interpenetrating"), "verdict": by["overlap"].get("verdict")}
        ev["allowed"] = {"verdict": by["overlap_ok"].get("verdict")}
        ev["top"] = {"ok": r.get("ok"), "failed": r.get("pairs_failed")}
        ok = (by["gap10"].get("verdict") == "pass" and abs((by["gap10"].get("gap_mm") or 0) - 10.0) < 0.6
              and by["gap2"].get("verdict") == "fail"
              and by["overlap"].get("verdict") == "fail" and by["overlap"].get("interpenetrating") is True
              and by["overlap_ok"].get("verdict") == "pass"
              and r.get("ok") is False and set(r.get("pairs_failed") or []) == {"gap2", "overlap"})
        return _j({"ok": bool(ok), "evidence": ev})
    except Exception as e:
        return _j({"ok": False, "evidence": ev, "error": "%s: %s" % (type(e).__name__, str(e)[:220])})
    finally:
        bpy.context.scene.unit_settings.scale_length = prev
        for nm in made:
            o = bpy.data.objects.get(nm)
            if o is not None:
                me = o.data
                bpy.data.objects.remove(o, do_unlink=True)
                if me is not None and me.users == 0:
                    try:
                        bpy.data.meshes.remove(me, do_unlink=True)
                    except Exception:
                        pass


def clear_help():
    return _j({
        "module": "clearance.py", "version": CLEAR_VERSION,
        "what": "成对间隙门：规则表驱动（min_mm 管间隙 / allow_overlap 管允许互插），给最近距离与互穿判定",
        "ops": ["clear_check(pairs)", "clear_selftest", "clear_help"],
        "pair_schema": {"id": "规则名", "a": ["对象…"], "b": ["对象…"], "min_mm": 5.0, "allow_overlap": False},
        "protocol": ["轮/悬挂/灯具这类**不许碰**的：min_mm 给工艺间隙（含悬挂行程）",
                     "板件/蒙皮这类**允许互插**的：allow_overlap=true（互穿是设计要求）",
                     "门里用它：先把规则写进 spec.py，再让 gate_run 一次判完"],
        "limits": ["间隙是采样点距离（大曲面平行贴合可能高估）；互穿判定精确",
                   "只看列进 pairs 的关系"],
        "kernel": "共享内核 K.dsh_kit v%s" % getattr(_KIT, "version", "?"),
    })


# 注册：一行搞定（原先手写 _j + _DshApi + dispatch + 注册约 50 行）
_KIT.register("clearance", CLEAR_VERSION, {"check": clear_check, "selftest": clear_selftest, "help": clear_help},
              extra={"pairwise_clearance": _KIT.pairwise_clearance})