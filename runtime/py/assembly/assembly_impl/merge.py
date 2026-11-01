# -*- coding: utf-8 -*-
"""Split from assembly.py; behavior preserved by the facade."""
from .common import *  # noqa: F401,F403
from .manifest import _load_manifest, _manifest_md5, _manifest_validate, _write_json
from .gate import asm_gate

def _transform_matrix(tr, policy, mmu):
    """manifest transform（loc_mm / rot_euler_deg / scale）→ 4×4（mm→Blender 单位）。"""
    if str(policy) == "as_is" or not isinstance(tr, dict):
        return Matrix.Identity(4)
    loc = [_num(x) or 0.0 for x in (_as_list(tr.get("loc_mm")) or [0, 0, 0])][:3]
    while len(loc) < 3:
        loc.append(0.0)
    rot = [_num(x) or 0.0 for x in (_as_list(tr.get("rot_euler_deg")) or [0, 0, 0])][:3]
    while len(rot) < 3:
        rot.append(0.0)
    sc = [_num(x) or 1.0 for x in (_as_list(tr.get("scale")) or [1, 1, 1])][:3]
    while len(sc) < 3:
        sc.append(1.0)
    T = Matrix.Translation(Vector([float(x) / mmu for x in loc]))
    R = Euler([math.radians(float(x)) for x in rot], "XYZ").to_matrix().to_4x4()
    S = Matrix.Diagonal((float(sc[0]), float(sc[1]), float(sc[2]), 1.0))
    return T @ R @ S


def _frame_origin(part, fid):
    for fr in (part.get("interface_frames") or []) if isinstance(part, dict) else []:
        if isinstance(fr, dict) and str(fr.get("id")) == str(fid):
            vals = [_num(x) or 0.0 for x in (_as_list(fr.get("origin_mm")) or [0, 0, 0])][:3]
            while len(vals) < 3:
                vals.append(0.0)
            return vals
    return None


def _plan_part_names(pid, requested, prefix_fmt):
    pre = str(prefix_fmt).replace("<partid>", pid).replace("{partid}", pid)
    return {orig: (pre + orig) for orig in requested}


def _merge_cleanup(objects, coll_name="ASM_MERGED"):
    removed = []
    for ob in objects:
        try:
            nm = str(ob.name)
            bpy.data.objects.remove(ob, do_unlink=True)
            removed.append(nm)
        except Exception:
            pass
    coll = bpy.data.collections.get(coll_name)
    if coll is not None and len(coll.objects) == 0:
        try:
            bpy.data.collections.remove(coll, do_unlink=True)
        except Exception:
            pass
    return removed


def _recalc_normals(objs):
    import bmesh
    rows = []
    for ob in objs:
        if ob.type != "MESH" or ob.data is None:
            continue
        row = {"object": str(ob.name), "recalced": False, "open_shell": None}
        try:
            bm = bmesh.new()
            bm.from_mesh(ob.data)
            boundary = int(sum(1 for e in bm.edges if len(e.link_faces) == 1))
            row["open_shell"] = bool(boundary > 0)
            try:
                row["volume_before"] = _r(float(bm.calc_volume(signed=True)), 6)
            except Exception:
                row["volume_before"] = None
            if boundary > 0:
                row["skipped"] = "开放壳（边界边 %d）不 recalc，避免把开口方向硬拧" % boundary
            else:
                bmesh.ops.recalc_face_normals(bm, faces=list(bm.faces))
                bm.to_mesh(ob.data)
                ob.data.update()
                row["recalced"] = True
                try:
                    row["volume_after"] = _r(float(bm.calc_volume(signed=True)), 6)
                except Exception:
                    row["volume_after"] = None
            bm.free()
        except Exception as e:
            row["error"] = "%s: %s" % (type(e).__name__, str(e)[:160])
        rows.append(row)
    return rows


def _asm_merge_p1(st):
    manifest, name_prefix, snap_tol_mm, transform_policy, verify_files = (
        st["manifest"], st["name_prefix"], st["snap_tol_mm"], st["transform_policy"], st["verify_files"])
    verify_md5 = st["verify_md5"]
    M, a, all_names, b, data_from, delta, delta_mm, f, it, missing, oa, ob, p, pa, pb, pid, va, vb, wa, want, wb = (st.get("M"), st.get("a"), st.get("all_names"), st.get("b"), st.get("data_from"), st.get("delta"), st.get("delta_mm"), st.get("f"), st.get("it"), st.get("missing"), st.get("oa"), st.get("ob"), st.get("p"), st.get("pa"), st.get("pb"), st.get("pid"), st.get("va"), st.get("vb"), st.get("wa"), st.get("want"), st.get("wb"))
    try:
        t0 = time.perf_counter()
        policy = str(transform_policy or "manifest")
        if policy not in ("manifest", "as_is", "snap_interface"):
            return _err("transform_policy 只支持 manifest|as_is|snap_interface，收到 %r" % policy)
        m, merr = _load_manifest(manifest)
        if merr:
            return _err(merr)
        val = _manifest_validate(m, check_files=bool(verify_files), check_md5=bool(verify_md5))
        if not val["ok"]:
            return _err("manifest 校验失败，拒绝合并（先修清单/文件）", errors=val["errors"][:20],
                        warnings=val["warnings"][:20], manifest_md5=val["manifest_md5"])
        mmu = float(_KIT.units())
        mdig = _manifest_md5(m)
        parts = [p for p in (m.get("parts") or []) if isinstance(p, dict)]
        if not parts:
            return _err("manifest 没有 parts —— 没东西可合并")
        order = sorted(parts, key=lambda p: str(p.get("id") or ""))
        prefix_fmt = str(name_prefix or "<partid>__")
        plan, warn_objects = [], []
        for p in order:
            pid = str(p.get("id") or "")
            f = _path(p.get("file"))
            if not f or not os.path.isfile(f):
                return _err("part %s 的文件不存在: %s" % (pid, f))
            try:
                with bpy.data.libraries.load(f, link=False) as (data_from, _data_to):
                    all_names = [str(x) for x in data_from.objects]
            except Exception as e:
                return _err("part %s 的 .blend 打不开: %s: %s" % (pid, type(e).__name__, str(e)[:160]))
            want = _as_list(p.get("objects")) or list(all_names)
            missing = [x for x in want if x not in all_names]
            if missing:
                warn_objects.append({"part": pid, "missing_objects": missing[:10],
                                     "why": "manifest 列的 objects 在文件里没有（按实际存在的继续）"})
            want = [x for x in want if x in all_names]
            M = _transform_matrix(p.get("transform"), policy, mmu)
            plan.append({"part": pid, "file": f, "requested": want, "missing": missing,
                         "matrix": M, "owner": p.get("owner"),
                         "target_names": _plan_part_names(pid, want, prefix_fmt),
                         "manifest_transform": p.get("transform")})
        snap_notes, snapped = [], set()
        if policy == "snap_interface":
            for it in sorted([x for x in (m.get("interfaces") or []) if isinstance(x, dict)],
                             key=lambda x: str(x.get("id") or "")):
                a, b = str(it.get("a") or ""), str(it.get("b") or "")
                pa = next((p for p in order if str(p.get("id")) == a), {})
                pb = next((p for p in order if str(p.get("id")) == b), {})
                oa = _frame_origin(pa, it.get("frame_a")) if it.get("frame_a") else None
                ob = _frame_origin(pb, it.get("frame_b")) if it.get("frame_b") else None
                if oa is None or ob is None:
                    snap_notes.append({"interface": it.get("id"), "state": "unresolved",
                                       "why": "缺 frame_a/frame_b 或 origin_mm，无法求解接口帧"})
                    continue
                va = next((p for p in plan if p["part"] == a), None)
                vb = next((p for p in plan if p["part"] == b), None)
                if va is None or vb is None:
                    snap_notes.append({"interface": it.get("id"), "state": "unresolved",
                                       "why": "接口端不在合并计划里"})
                    continue
                if b in snapped:
                    snap_notes.append({"interface": it.get("id"), "state": "conflict",
                                       "why": "part %s 已被别的接口对齐过（多约束冲突，未硬拧）" % b})
                    continue
                wa = va["matrix"] @ Vector([x / mmu for x in oa])
                wb = vb["matrix"] @ Vector([x / mmu for x in ob])
                delta = wa - wb
                delta_mm = float(delta.length) * mmu
                if delta_mm <= float(snap_tol_mm) + 1e-9:
                    vb["matrix"] = Matrix.Translation(delta) @ vb["matrix"]
                    snapped.add(b)
                    snap_notes.append({"interface": it.get("id"), "state": "snapped", "delta_mm": _r(delta_mm, 4)})
                else:
                    snap_notes.append({"interface": it.get("id"), "state": "unresolved", "delta_mm": _r(delta_mm, 4),
                                       "why": "接口帧偏差 %.3f mm > snap_tol_mm %.3f → 不硬拧（unresolved）"
                                              % (delta_mm, float(snap_tol_mm))})
        plan_out = [{"part": p["part"], "file": p["file"], "objects": p["requested"],
                     "objects_missing": p["missing"], "target_names": p["target_names"],
                     "transform": {"policy": policy,
                                   "loc_mm": (p["manifest_transform"] or {}).get("loc_mm")
                                   if isinstance(p["manifest_transform"], dict) else [0, 0, 0]}}
                    for p in plan]
        objs_total = sum(len(p["requested"]) for p in plan)
    finally:
        st.update((k, v) for k, v in locals().items() if k != "st")

def _asm_merge_p2(st):
    dry_run, m, mdig, objs_total, out_blend = (
        st["dry_run"], st.get("m"), st.get("mdig"), st.get("objs_total"), st["out_blend"])
    plan, plan_out, snap_notes, snapshot, t0 = (
        st.get("plan"), st.get("plan_out"), st.get("snap_notes"), st["snapshot"], st.get("t0"))
    warn_objects = st.get("warn_objects")
    d, rec, serr, txn = (st.get("d"), st.get("rec"), st.get("serr"), st.get("txn"))
    try:
        if dry_run:
            d = {"ok": True, "verdict": "planned", "dry_run": True, "readonly": True,
                 "manifest_md5": mdig, "out_blend": _path(out_blend) if out_blend else None,
                 "order": [p["part"] for p in plan], "parts": plan_out,
                 "objects_total": objs_total, "object_warnings": warn_objects,
                 "interfaces": {"count": len(m.get("interfaces") or []), "snap": snap_notes},
                 "outputs": {"out_blend": _path(out_blend) if out_blend else None, "written": False},
                 "gate": "skipped_dry_run",
                 "note": "dry_run：只回计划（append 顺序/目标对象名/变换），不 append、不写盘、不跑 gate"}
            return _j(_receipt(d, t0))
        if not out_blend:
            return _err("dry_run=false 必须给 out_blend（合并结果要落盘）")
        snap = None
        if snapshot:
            txn = _api("txn")
            if txn is None:
                snap = {"ok": False, "skipped": True,
                        "note": "K.dsh_txn_api 未加载 ⇒ 没做文件级快照（合并失败只能靠不保存回退）"}
            else:
                rec, serr = _call_api("txn", "snapshot",
                                      {"label": "asm_merge-%s" % str(mdig)[:12], "note": "asm_merge 前快照"})
                if serr:
                    return _err("快照失败，拒绝在无回退点的场景上合并：%s" % serr)
                snap = rec.get("snapshot") if isinstance(rec, dict) else rec
        coll = bpy.data.collections.get("ASM_MERGED")
        if coll is None:
            coll = bpy.data.collections.new("ASM_MERGED")
            bpy.context.scene.collection.children.link(coll)
        merged, added_objs = [], []
    finally:
        st.update((k, v) for k, v in locals().items() if k != "st")

def _asm_merge_p3(st):
    added_objs, coll, gate, gate_spec, include_hidden = (
        st.get("added_objs"), st.get("coll"), st["gate"], st["gate_spec"], st["include_hidden"])
    m, mdig, merged, mmu, out_blend = (
        st.get("m"), st.get("mdig"), st.get("merged"), st.get("mmu"), st["out_blend"])
    parts, plan, plan_out, policy, recalc_normals = (
        st.get("parts"), st.get("plan"), st.get("plan_out"), st.get("policy"), st["recalc_normals"])
    snap, snap_notes, strict, t0, warn_objects = (
        st.get("snap"), st.get("snap_notes"), st["strict"], st.get("t0"), st.get("warn_objects"))
    blend_md5, d_out, data_to, gate_digest, gate_rec, got, gspec, idx, item, names, normals, ob, orig, p_out, raw, rec_path, receipt, removed, target = (st.get("blend_md5"), st.get("d_out"), st.get("data_to"), st.get("gate_digest"), st.get("gate_rec"), st.get("got"), st.get("gspec"), st.get("idx"), st.get("item"), st.get("names"), st.get("normals"), st.get("ob"), st.get("orig"), st.get("p_out"), st.get("raw"), st.get("rec_path"), st.get("receipt"), st.get("removed"), st.get("target"))
    try:
        try:
            for item in plan:
                with bpy.data.libraries.load(item["file"], link=False) as (data_from, data_to):
                    data_to.objects = list(item["requested"])
                got = [o for o in (getattr(data_to, "objects", None) or []) if o is not None]
                if not got:
                    raise RuntimeError("part %s append 回来 0 个对象" % item["part"])
                for idx, ob in enumerate(got):
                    orig = item["requested"][idx] if idx < len(item["requested"]) else str(ob.name)
                    target = item["target_names"].get(orig) or "%s%s" % (item["part"] + "__", orig)
                    try:
                        ob.name = target
                    except Exception:
                        pass
                    try:
                        ob["asm.part"] = item["part"]
                        ob["asm.manifest_md5"] = str(mdig)
                        if item.get("owner"):
                            ob["dsh.owner"] = str(item["owner"])
                    except Exception:
                        pass
                    ob.matrix_world = item["matrix"] @ ob.matrix_world
                    try:
                        coll.objects.link(ob)
                    except Exception:
                        pass
                    added_objs.append(ob)
                    merged.append({"part": item["part"], "object": str(ob.name),
                                   "source_object": str(orig), "file": item["file"]})
            bpy.context.view_layer.update()
            normals = _recalc_normals(added_objs) if recalc_normals else {"skipped": True}
            gate_rec = None
            if gate:
                gspec = dict(gate_spec) if isinstance(gate_spec, dict) else {}
                names = [x["object"] for x in merged]
                raw = asm_gate(scope=None, objects=names, manifest=m, spec=gspec, summary_only=True,
                               strict=bool(strict), include_hidden=include_hidden)
                gate_rec = json.loads(raw) if isinstance(raw, str) else raw
                if not isinstance(gate_rec, dict) or gate_rec.get("verdict") != "pass":
                    return _j(_receipt({
                        "ok": False, "verdict": (gate_rec or {}).get("verdict") if isinstance(gate_rec, dict) else "unresolved",
                        "stage": "gate", "saved": False, "dry_run": False,
                        "error": "合并结果没通过 gate ⇒ 不写盘（失败不保存）",
                        "gate": gate_rec, "merged": merged, "snapshot": snap, "normals": normals,
                        "hint": "场景已被本次 append 改动；用 txn.restore(<snapshot label>) 回退，或修 manifest/几何后重跑",
                    }, t0))
            p_out = _path(out_blend)
            d_out = os.path.dirname(p_out)
            if d_out and not os.path.isdir(d_out):
                os.makedirs(d_out, exist_ok=True)
            bpy.ops.wm.save_as_mainfile(filepath=p_out, copy=True)
            blend_md5 = _sha256_file(p_out)
            rec_path = (p_out[:-6] + ".merge_receipt.json") if p_out.lower().endswith(".blend") \
                else (p_out + ".merge_receipt.json")
            gate_digest = _md5_text(_canon({k: v for k, v in (gate_rec or {}).items()
                                            if k not in ("ms", "receipt_md5")})) if gate_rec else None
            receipt = {"schema": "dsh-assembly-merge-receipt/1", "assembly": m.get("assembly"),
                       "version": ASSEMBLY_VERSION, "manifest_md5": mdig,
                       "sealed": bool(m.get("sealed")), "out_blend": p_out, "blend_md5": blend_md5,
                       "parts": [{"part": p["part"], "file": p["file"],
                                  "file_md5": next((x.get("file_md5") for x in parts
                                                    if str(x.get("id")) == p["part"]), None)} for p in plan],
                       "order": [p["part"] for p in plan], "objects": merged,
                       "transforms": plan_out, "interfaces": {"snap": snap_notes},
                       "normals": normals, "gate": gate_rec, "gate_md5": gate_digest,
                       "snapshot": snap, "created": time.strftime("%Y-%m-%d %H:%M:%S"),
                       "units": {"mm_per_unit": _r(mmu, 6)}}
            try:
                _write_json(rec_path, receipt)
            except Exception as e:
                return _j(_receipt({"ok": False, "verdict": "pass", "stage": "receipt",
                                    "error": "blend 已保存但 merge_receipt.json 写失败: %s: %s"
                                             % (type(e).__name__, str(e)[:160]),
                                    "outputs": {"blend": p_out, "md5": blend_md5, "receipt": rec_path},
                                    "gate": gate_rec}, t0))
            return _j(_receipt({"ok": True, "verdict": "pass", "dry_run": False, "stage": "done",
                                "manifest_md5": mdig, "merged": merged,
                                "objects_total": len(merged), "order": [p["part"] for p in plan],
                                "transform_policy": policy, "interfaces": {"snap": snap_notes},
                                "normals": normals, "gate": gate_rec, "snapshot": snap,
                                "object_warnings": warn_objects,
                                "outputs": {"blend": p_out, "md5": blend_md5, "receipt": rec_path,
                                            "receipt_md5": _sha256_file(rec_path)},
                                "note": "成功路径：blend + merge_receipt.json 都写了；gate 不过的路径不会写盘"},
                               t0))
        except Exception as e:
            removed = _merge_cleanup(added_objs)
            import traceback
            return _j(_receipt({"ok": False, "stage": "append_or_merge",
                                "error": "%s: %s" % (type(e).__name__, str(e)[:240]),
                                "cleaned_objects": removed, "snapshot": snap,
                                "traceback": traceback.format_exc()[-600:],
                                "hint": "失败不保存；已尽力删掉本次 append 的对象（快照 label 见 snapshot）"}, t0))
    finally:
        st.update((k, v) for k, v in locals().items() if k != "st")

def asm_merge(manifest=None, out_blend=None, dry_run=True, transform_policy="manifest",
              snap_tol_mm=2.0, recalc_normals=True, snapshot=True, gate=True,
              gate_spec=None, strict=True, verify_files=True, verify_md5=True,
              name_prefix=None, include_hidden=False):
    """N 路合并：按 part id 字典序 append → 改名 → 变换 → recalc normals → 自动 gate → 写盘+回执。

    dry_run=true（默认）只回计划，不 append / 不写盘 / 不跑 gate；gate 不过不保存。
    """
    st = dict(locals())
    for _fn_ in (_asm_merge_p1, _asm_merge_p2, _asm_merge_p3):
        _r = _fn_(st)
        if _r is not None:
            return _r
