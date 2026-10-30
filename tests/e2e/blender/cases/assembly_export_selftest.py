# -*- coding: utf-8 -*-
"""tests/assembly_export_selftest.py —— asm_export（交付导出 manifest+ledger）与 asm_repair policy 真机自检。

跑法（插件无头通道）：
    blender_rt_headless(preload="assembly,audit,contract,clearance,gate,mesh_fix,geo,txn,deliver",
                        engine="none", factory_startup=True, timeout_ms=300000,
                        script_file="<包路径>/tests/assembly_export_selftest.py")

独立跑（自己按序 exec kit/audit/contract/clearance/gate/mesh_fix/geo/txn/deliver/assembly）：
    blender -b --factory-startup --python tests/assembly_export_selftest.py

覆盖：
  1) policy：safe 不自动做布尔（blocked 有 blocked_reason）/ aggressive 才规划 subtract /
     manual 只规划不执行且不规划布尔 / plan[].risk 存在 / 坏 policy ok:false
  2) asm_export merge=True（两 part .blend，manifest 允许 2mm 设计内交叠）：GLB/FBX/OBJ/manifest/ledger
     都存在、GLB 魔数 glTF、FBX Kaydara 头、manifest_md5 可复算、逐文件 md5 与真实文件一致、
     ledger ≥1 行且 export 事件带 manifest_md5/gate_verdict、merge gate=pass
  3) 非合并导出（formats 逗号字符串）+ dry_run=true 只回计划不建目录
  4) 坏参数：非法 formats / 空 formats / 空 name / merge 无 manifest / 非布尔 dry_run /
     空 objects / 不存在对象 / 空场景 → ok:false
"""
import hashlib
import json
import os
import sys
import tempfile
import traceback

HERE = os.path.dirname(os.path.abspath(__file__)) if "__file__" in globals() else os.getcwd()
RT = os.path.normpath(os.path.join(HERE, "..", "runtime"))
MODULES = ("kit.py", "audit.py", "contract.py", "clearance.py", "gate.py",
           "mesh_fix.py", "geo.py", "txn.py", "deliver.py", "assembly.py")


def _ensure_kernel():
    import types
    K = sys.modules.get("dsh_rt_kernel")
    if K is None:
        K = types.ModuleType("dsh_rt_kernel")
        sys.modules["dsh_rt_kernel"] = K
    if getattr(K, "dsh_assembly_api", None) is not None:
        return K
    for nm in MODULES:
        p = os.path.join(RT, nm)
        with open(p, "r", encoding="utf-8") as fh:
            src = fh.read()
        ns = {"__name__": nm[:-3], "__file__": p}
        exec(compile(src, p, "exec"), ns)
    return sys.modules["dsh_rt_kernel"]


K = _ensure_kernel()
API = K.dsh_assembly_api


def call(op, **kw):
    out = API(op, kw)
    return json.loads(out) if isinstance(out, str) else out


def _safe(op, *args, **kw):
    try:
        if args and isinstance(args[0], dict):
            kw = dict(args[0], **kw)
        return call(op, **kw)
    except Exception as e:
        return {"ok": False, "error": "%s: %s" % (type(e).__name__, str(e)[:300])}


import bpy
import bmesh


def reset_scene():
    for ob in list(bpy.data.objects):
        try:
            bpy.data.objects.remove(ob, do_unlink=True)
        except Exception:
            pass
    for me in list(bpy.data.meshes):
        if me.users == 0:
            try:
                bpy.data.meshes.remove(me)
            except Exception:
                pass


def box(name, size, loc):
    me = bpy.data.meshes.new(name)
    bm = bmesh.new()
    bmesh.ops.create_cube(bm, size=1.0)
    if isinstance(size, (int, float)):
        sx = sy = sz = float(size)
    else:
        sx, sy, sz = [float(x) for x in size]
    for v in bm.verts:
        v.co.x *= sx
        v.co.y *= sy
        v.co.z *= sz
    bm.to_mesh(me)
    bm.free()
    ob = bpy.data.objects.new(name, me)
    bpy.context.scene.collection.objects.link(ob)
    ob.location = tuple(float(x) for x in loc)
    return ob


def scene_fingerprint():
    out = {}
    for ob in bpy.context.scene.objects:
        if ob.type != "MESH":
            continue
        mw = ob.matrix_world.copy()
        out[str(ob.name)] = {"loc": [round(float(x), 6) for x in mw.translation],
                            "verts": int(len(ob.data.vertices)),
                            "polys": int(len(ob.data.polygons))}
    return out


def md5_file(path):
    h = hashlib.md5()
    with open(path, "rb") as fh:
        while True:
            chunk = fh.read(1 << 20)
            if not chunk:
                break
            h.update(chunk)
    return h.hexdigest()


def canon(obj):
    return json.dumps(obj, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)


EVID = {}


def _rec(key, cond, **nums):
    # 顶层断言键用 __ 分组（不能带 "."）；"group.name.detail" 只作旁证，不参与判定
    EVID[str(key).replace(".", "__")] = bool(cond)
    for k, v in nums.items():
        EVID[str(key) + "." + k] = v
    return bool(cond)


def _ledger_lines(path):
    try:
        with open(path, "r", encoding="utf-8") as fh:
            return [json.loads(x) for x in fh if x.strip()]
    except Exception:
        return []


def main():
    prev_scale = float(bpy.context.scene.unit_settings.scale_length)
    tmpdir = tempfile.mkdtemp(prefix="dsh_asm_export_selftest_")
    try:
        bpy.context.scene.unit_settings.scale_length = 0.001      # 1 场景单位 = 1 mm
        reset_scene()
        # ================= 1) repair policy =================
        box("PA", 20.0, (0.0, 0.0, 10.0))
        box("PB", 20.0, (18.0, 0.0, 10.0))                       # 2mm 互穿
        box("PBASE", (60.0, 60.0, 10.0), (9.0, 0.0, -5.0))
        bpy.context.view_layer.update()
        names = ["PA", "PB", "PBASE"]
        fp0 = scene_fingerprint()
        tr = _safe("repair", objects=names)
        plan = tr.get("plan") or []
        _rec("policy.safe_translate_plan", any(a.get("method") == "translate" for a in plan),
             methods=[a.get("method") for a in plan])
        _rec("policy.plan_has_risk", bool(plan) and all(isinstance(a.get("risk"), str) and a.get("risk")
                                                        for a in plan),
             risks=[a.get("risk") for a in plan][:4])
        legacy_bool = _safe("repair", objects=names, max_move_mm=0.1, allow_boolean=True)
        _rec("policy.legacy_allow_boolean",
             any(a.get("method") == "subtract" for a in (legacy_bool.get("plan") or [])),
             methods=[a.get("method") for a in legacy_bool.get("plan") or []])
        safe_b = _safe("repair", objects=names, max_move_mm=0.1,
                       allow_boolean=True, policy="safe")   # 显式 safe：硬禁布尔
        safe_b_blocked = [x for x in (safe_b.get("blocked") or []) if isinstance(x, dict)]
        _rec("policy.safe_boolean_blocked",
             all(a.get("method") != "subtract" for a in (safe_b.get("plan") or []))
             and any("policy=safe" in str(x.get("blocked_reason") or x.get("reason") or "")
                     for x in safe_b_blocked),
             blocked=[str(x.get("blocked_reason"))[:90] for x in safe_b_blocked][:3])
        _rec("policy.blocked_has_reason",
             bool(safe_b_blocked) and all(bool(x.get("blocked_reason")) for x in safe_b_blocked))
        aggr = _safe("repair", objects=names, max_move_mm=0.1, allow_boolean=True, policy="aggressive")
        _rec("policy.aggressive_subtract", any(a.get("method") == "subtract" for a in (aggr.get("plan") or [])),
             methods=[a.get("method") for a in aggr.get("plan") or []])
        manual_p = _safe("repair", objects=names, max_move_mm=0.1, allow_boolean=True, policy="manual")
        mp_block = [x for x in (manual_p.get("blocked") or []) if isinstance(x, dict)]
        _rec("policy.manual_no_boolean",
             all(a.get("method") != "subtract" for a in (manual_p.get("plan") or []))
             and any("policy=manual" in str(x.get("blocked_reason") or "") for x in mp_block),
             blocked=[str(x.get("blocked_reason"))[:90] for x in mp_block][:3])
        fp_before_manual = scene_fingerprint()
        manual_ex = _safe("repair", objects=names, dry_run=False, max_move_mm=0.1,
                          allow_boolean=True, policy="manual")
        _rec("policy.manual_no_exec", manual_ex.get("ok") is False
             and manual_ex.get("stage") == "manual_policy" and manual_ex.get("readonly") is True,
             stage=manual_ex.get("stage"), ok=manual_ex.get("ok"))
        _rec("policy.manual_scene_unchanged", scene_fingerprint() == fp_before_manual)
        _rec("policy.bad_okfalse", _safe("repair", objects=names, policy="yolo").get("ok") is False)

        # ================= 2) merge + 三格式导出 + manifest + ledger =================
        reset_scene()
        box("GEO_A", 20.0, (0.0, 0.0, 0.0))
        bpy.context.view_layer.update()
        part_a = os.path.join(tmpdir, "part_a.blend")
        bpy.ops.wm.save_as_mainfile(filepath=part_a, copy=True)
        reset_scene()
        box("GEO_B", 20.0, (0.0, 0.0, 0.0))
        bpy.context.view_layer.update()
        part_b = os.path.join(tmpdir, "part_b.blend")
        bpy.ops.wm.save_as_mainfile(filepath=part_b, copy=True)
        reset_scene()
        man = {
            "schema": "dsh-assembly-manifest/1", "units": "mm",
            "coord": {"up": "Z", "forward": "-Y"}, "assembly": "export-selftest", "version": 1,
            "sealed": False, "sealed_md5": None,
            "parts": [
                {"id": "P1", "file": part_a, "objects": ["GEO_A"]},
                {"id": "P2", "file": part_b, "objects": ["GEO_B"],
                 "transform": {"loc_mm": [18.0, 0.0, 0.0]}},
            ],
            "interfaces": [{"id": "IF-1", "a": "P1", "b": "P2", "fit": "location",
                            "nominal_mm": 0.0, "tol_mm": 0.5}],
            "allow_pairs": [{"id": "ALLOW-1", "a": "P1", "b": "P2",
                             "max_depth_mm": 3.0, "max_volume_mm3": 50000.0,
                             "reason": "selftest：P2 与 P1 有 2mm 设计内交叠（隐藏于内部）"}],
        }
        dry_merge_dir = os.path.join(tmpdir, "out_merge_dry")
        drym = _safe("asm_export", manifest=man, out_dir=dry_merge_dir, name="asmdry",
                     formats=["glb", "obj"], merge=True, dry_run=True)
        _rec("merge_dry.plan", drym.get("ok") is True and drym.get("dry_run") is True
             and len(drym.get("objects") or []) >= 2
             and all(isinstance((drym.get("files") or {}).get(k), str)
                     for k in ("glb", "obj", "manifest", "ledger", "blend"))
             and not os.path.exists(dry_merge_dir),
             objects=drym.get("objects"), error=drym.get("error"))
        out1 = os.path.join(tmpdir, "out_merge")
        exp = _safe("asm_export", manifest=man, out_dir=out1, name="asm",
                    formats=["glb", "fbx", "obj"], merge=True, dry_run=False)
        files = exp.get("files") or {}
        man_path = files.get("manifest") or ""
        led_path = files.get("ledger") or ""
        _rec("export.ok", exp.get("ok") is True, error=exp.get("error"), verdict=exp.get("verdict"))
        _rec("export.dry_false", exp.get("dry_run") is False)
        _rec("export.files_exist",
             all(isinstance(files.get(k), str) and os.path.isfile(files.get(k))
                 for k in ("glb", "fbx", "obj", "manifest", "ledger", "blend")),
             files={k: (os.path.isfile(v) if isinstance(v, str) else None) for k, v in files.items()})
        glb_ok = False
        try:
            with open(files.get("glb") or "", "rb") as fh:
                glb_ok = fh.read(4) == b"glTF"
        except Exception:
            glb_ok = False
        _rec("export.glb_magic", glb_ok)
        fbx_ok = False
        try:
            with open(files.get("fbx") or "", "rb") as fh:
                fbx_ok = fh.read(21).startswith(b"Kaydara FBX Binary")
        except Exception:
            fbx_ok = False
        _rec("export.fbx_magic", fbx_ok)
        obj_ok = False
        try:
            with open(files.get("obj") or "", "rb") as fh:
                chunk = fh.read(1 << 17)
            obj_ok = (b"\nv " in chunk) or chunk.startswith(b"v ")
        except Exception:
            obj_ok = False
        _rec("export.obj_verts", obj_ok)
        _rec("export.merge_gate", ((exp.get("merge") or {}).get("gate") or {}).get("verdict") == "pass",
             merge_gate=((exp.get("merge") or {}).get("gate") or {}).get("verdict"))
        _rec("export.gate_pass", (exp.get("gate") or {}).get("verdict") == "pass",
             gate=(exp.get("gate") or {}).get("verdict"))
        _rec("export.counts", int((exp.get("counts") or {}).get("objects") or 0) >= 2
             and int((exp.get("counts") or {}).get("files") or 0) == 3,
             counts=exp.get("counts"))
        # manifest：存在 / 自指纹可复算 / 逐文件 md5 与真实文件一致 / 记录 gate+merge
        manj = {}
        try:
            with open(man_path, "r", encoding="utf-8") as fh:
                manj = json.load(fh)
        except Exception:
            manj = {}
        _rec("manifest.written", bool(manj) and bool(manj.get("manifest_md5")))
        recomputed = None
        if manj:
            want = hashlib.sha256(canon({k: v for k, v in manj.items()
                                         if k != "manifest_md5"}).encode("utf-8")).hexdigest()
            recomputed = want == manj.get("manifest_md5")
        _rec("manifest.md5_recomputes", bool(recomputed), manifest_md5=manj.get("manifest_md5"))
        real_glb_md5 = md5_file(files["glb"])
        man_glb_md5 = (((manj.get("files") or {}).get("glb") or {}).get("md5"))
        _rec("manifest.file_md5_match", man_glb_md5 == real_glb_md5,
             manifest=man_glb_md5, real=real_glb_md5)
        _rec("manifest.merge_gate_pass",
             (((manj.get("merge") or {}).get("gate") or {}).get("verdict") == "pass"))
        # ledger：>=1 行，merge + export 事件，export 带 manifest_md5/gate_verdict
        lines = _ledger_lines(led_path)
        ev_actions = [str(x.get("action")) for x in lines]
        ev_export = [x for x in lines if x.get("action") == "export"]
        _rec("ledger.lines", len(lines) >= 1 and "merge" in ev_actions and "export" in ev_actions,
             actions=ev_actions, lines=len(lines))
        _rec("ledger.export_fields",
             bool(ev_export) and ev_export[-1].get("manifest_md5") == manj.get("manifest_md5")
             and ev_export[-1].get("gate_verdict") == "pass"
             and bool((ev_export[-1].get("md5") or {}).get("glb")),
             export_event=ev_export[-1] if ev_export else None)
        # 追加语义：同目录再导一次 OBJ，ledger 行数必须只增不减
        n_before = len(lines)
        exp_again = _safe("export", objects=[o.name for o in bpy.context.scene.objects if o.type == "MESH"],
                          out_dir=out1, name="asm_again", formats=["obj"], merge=False, dry_run=False)
        lines2 = _ledger_lines(led_path)
        _rec("ledger.append_only", len(lines2) == n_before + 1,
             before=n_before, after=len(lines2), ok=exp_again.get("ok"))

        # ================= 3) 非合并导出（formats 字符串）+ dry_run 不写盘 =================
        out2 = os.path.join(tmpdir, "out_nonmerge")
        objs2 = [o.name for o in bpy.context.scene.objects if o.type == "MESH"]
        exp2 = _safe("export", objects=objs2, out_dir=out2, name="asm2",
                     formats="glb,obj", merge=False, dry_run=False)
        f2 = exp2.get("files") or {}
        _rec("nonmerge.ok", exp2.get("ok") is True and exp2.get("formats_ok") == ["glb", "obj"],
             formats_ok=exp2.get("formats_ok"), error=exp2.get("error"))
        _rec("nonmerge.files", all(isinstance(f2.get(k), str) and os.path.isfile(f2.get(k))
                                   for k in ("glb", "obj", "manifest", "ledger")))
        dry_dir = os.path.join(tmpdir, "out_dry")
        dry = _safe("export", objects=objs2, out_dir=dry_dir, name="dry",
                    formats=["glb", "fbx", "obj"], dry_run=True)
        dry_files = dry.get("files") or {}
        _rec("dryrun.plan_only", dry.get("ok") is True and dry.get("dry_run") is True
             and dry.get("readonly") is True and all(isinstance(dry_files.get(k), str)
                                                     for k in ("glb", "fbx", "obj")),
             error=dry.get("error"))
        _rec("dryrun.no_write", not os.path.exists(dry_dir))

        # ================= 4) 坏参数 =================
        _rec("bad.formats", _safe("export", objects=objs2, out_dir=out1, formats=["stl"]).get("ok") is False)
        _rec("bad.formats_empty", _safe("export", objects=objs2, out_dir=out1, formats=[]).get("ok") is False)
        _rec("bad.name", _safe("export", objects=objs2, out_dir=out1, name="").get("ok") is False)
        _rec("bad.merge_no_manifest", _safe("export", objects=objs2, out_dir=out1, merge=True).get("ok") is False)
        _rec("bad.dry_run_flag", _safe("export", objects=objs2, out_dir=out1, dry_run="maybe").get("ok") is False)
        _rec("bad.empty_objects", _safe("export", objects=[], out_dir=out1).get("ok") is False)
        _rec("bad.missing_object", _safe("export", objects=["__no_such_obj__"], out_dir=out1).get("ok") is False)
        reset_scene()
        _rec("bad.empty_scene", _safe("export", out_dir=os.path.join(tmpdir, "empty")).get("ok") is False)
    finally:
        bpy.context.scene.unit_settings.scale_length = prev_scale
        reset_scene()
        try:
            import shutil
            shutil.rmtree(tmpdir, ignore_errors=True)
        except Exception:
            pass


def main_safe():
    try:
        main()
        bad = sorted([k for k, v in EVID.items() if isinstance(v, bool) and v is not True and "." not in k])
        return {"ok": len(bad) == 0, "failed": bad, "evidence": EVID}
    except Exception as e:
        return {"ok": False, "error": "%s: %s" % (type(e).__name__, str(e)[:400]),
                "traceback": traceback.format_exc()[-1600:], "evidence": EVID}


if __name__ == "__main__":
    res = main_safe()
    print("HEADLESS " + json.dumps(res, ensure_ascii=False, separators=(",", ":")))
    print("ASSEMBLY_EXPORT_SELFTEST " + ("ALL PASS" if res.get("ok") else "FAIL"))
    if not res.get("ok"):
        sys.exit(1)
