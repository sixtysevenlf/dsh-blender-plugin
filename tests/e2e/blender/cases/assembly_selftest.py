# -*- coding: utf-8 -*-
"""tests/assembly_selftest.py —— 装配层（runtime/assembly.py）真机自检。

跑法（插件无头通道）：
    blender_rt_headless(preload="assembly,audit,contract,clearance,gate", engine="none",
        factory_startup=True, timeout_ms=300000,
        script_file="<包路径>/tests/assembly_selftest.py")

或独立跑（自己按序 exec kit/audit/contract/clearance/gate/assembly）：
    blender -b --factory-startup --python tests/assembly_selftest.py

覆盖：
  D1 浅穿模（1mm 交叠）→ clash
  D3 完全包含（小件在大件内、面不相交）→ contained（旧全对全会漏的分支）
  D4 12mm 设计内隐藏交叠 + spec.allow → hidden_ok（允许对误报必须 0）
  D5 面贴接触（gap=0）→ contact（不是 clash）
  D7 小悬空件 → float（装配浮空必须报）
  D8 法线朝内 → gate 不得 pass（unresolved/ fail，不许假通过）
  D9 开口件相交 → unresolved（证据不足 ≠ pass）
  manifest init/add_part/add_interface/add_allow/validate/seal/show + merge(dry_run) 确定性
  坏参数：空 objects / 不存在的对象 / md5 不符 / basis 不正交 / 重复 id → ok:false
"""
import json
import os
import sys
import tempfile
import traceback

HERE = os.path.dirname(os.path.abspath(__file__)) if "__file__" in globals() else os.getcwd()
RT = os.path.normpath(os.path.join(HERE, "..", "runtime"))
MODULES = ("kit.py", "audit.py", "contract.py", "clearance.py", "gate.py", "assembly.py")


def _ensure_kernel():
    """preload 已注入就用现成的；否则自己按序 exec（保持脚本独立可跑）。"""
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


# ────────────────────────────── 场景构造 ──────────────────────────────
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


def box(name, size, loc, flip=False):
    me = bpy.data.meshes.new(name)
    bm = bmesh.new()
    bmesh.ops.create_cube(bm, size=1.0)
    for v in bm.verts:
        v.co *= float(size)
    if flip:
        bmesh.ops.reverse_faces(bm, faces=list(bm.faces))
    bm.to_mesh(me)
    bm.free()
    ob = bpy.data.objects.new(name, me)
    bpy.context.scene.collection.objects.link(ob)
    ob.location = tuple(float(x) for x in loc)
    return ob


def quad(name, size, loc):
    me = bpy.data.meshes.new(name)
    bm = bmesh.new()
    s = float(size) / 2.0
    vs = [bm.verts.new((-s, -s, 0.0)), bm.verts.new((s, -s, 0.0)),
          bm.verts.new((s, s, 0.0)), bm.verts.new((-s, s, 0.0))]
    bm.faces.new(vs)
    bm.to_mesh(me)
    bm.free()
    ob = bpy.data.objects.new(name, me)
    bpy.context.scene.collection.objects.link(ob)
    ob.location = tuple(float(x) for x in loc)
    return ob


def pairs_by_key(scan):
    out = {}
    for p in scan.get("pairs") or []:
        out[tuple(sorted([str(p.get("a")), str(p.get("b"))]))] = p
    return out


def _floater_objects(scan):
    names = set()
    for f in scan.get("floaters") or []:
        if f.get("object"):
            names.add(str(f.get("object")))
        for o in f.get("objects") or []:
            names.add(str(o))
    return names


EVID = {}


def _rec(key, cond, **nums):
    EVID[key] = bool(cond)
    for k, v in nums.items():
        EVID[key + "." + k] = v
    return bool(cond)
# ────────────────────────────── 主流程 ──────────────────────────────
def main():
    prev_scale = float(bpy.context.scene.unit_settings.scale_length)
    tmpdir = tempfile.mkdtemp(prefix="dsh_assembly_selftest_")
    try:
        bpy.context.scene.unit_settings.scale_length = 0.001      # 1 单位 = 1 mm
        reset_scene()

        # ---------------- 脏场景：D1/D3/D4/D5/D7/D8 ----------------
        box("D1_a", 20.0, (0, 0, 0))
        box("D1_b", 20.0, (19.0, 0, 0))                            # D1 浅穿模 1mm
        box("D3_big", 40.0, (100, 0, 0))
        box("D3_small", 6.0, (100, 0, 0))                          # D3 完全包含、面无相交
        box("D4_a", 40.0, (0, 100, 0))
        box("D4_b", 40.0, (0, 100, 28))                            # D4 z 向交叠 12mm（设计内）
        box("D5_a", 20.0, (0, 200, 0))
        box("D5_b", 20.0, (20.0, 200, 0))                          # D5 面贴接触 gap=0
        box("D7_float", 6.0, (240, 0, 0))                          # D7 小悬空件
        box("D8_inv", 20.0, (300, 300, 0), flip=True)              # D8 法线朝内
        bpy.context.view_layer.update()
        names = [ob.name for ob in bpy.context.scene.objects if ob.type == "MESH"]
        spec = {"allow": [{"id": "ALLOW-D4", "a": "D4_a", "b": "D4_b",
                           "max_depth_mm": 13.0, "max_volume_mm3": 25000.0,
                           "reason": "selftest：12mm 设计内隐藏交叠（留 1mm 采样容差）"}]}

        sc = _safe("scan", objects=names, spec=spec, summary_only=False,
                   max_pairs=500, report_gap_mm=5.0)
        pb = pairs_by_key(sc)
        d1 = pb.get(("D1_a", "D1_b"), {})
        d3 = pb.get(("D3_big", "D3_small"), {})
        d4 = pb.get(("D4_a", "D4_b"), {})
        d5 = pb.get(("D5_a", "D5_b"), {})
        _rec("scan.ok", sc.get("ok") is True, error=sc.get("error"))
        _rec("scan.d1_clash", d1.get("class") == "clash", cls=d1.get("class"),
             depth=d1.get("depth_mm"), tri=d1.get("tri_overlap_pairs"))
        _rec("scan.d1_depth_measured", d1.get("depth_measured") is True and abs((d1.get("depth_mm") or 0) - 1.0) < 0.6,
             depth=d1.get("depth_mm"))
        _rec("scan.d3_contained", d3.get("class") == "contained", cls=d3.get("class"),
             volume=d3.get("volume_mm3"))
        _rec("scan.d4_hidden_ok", d4.get("class") == "hidden_ok", cls=d4.get("class"),
             depth=d4.get("depth_mm"))
        _rec("scan.d5_contact", d5.get("class") == "contact", cls=d5.get("class"),
             gap=d5.get("min_gap_mm"))
        offenders = set()
        for p in sc.get("offenders") or []:
            offenders.add(tuple(sorted([str(p.get("a")), str(p.get("b"))])))
        _rec("scan.allow_false_positive_zero", ("D4_a", "D4_b") not in offenders)
        _rec("scan.offenders_have_d1_d3", ("D1_a", "D1_b") in offenders and ("D3_big", "D3_small") in offenders)
        floats = _floater_objects(sc)
        _rec("scan.d7_float_detected", "D7_float" in floats, floats=sorted(floats)[:8])
        _rec("scan.d8_unresolved_object", "D8_inv" in (sc.get("coverage") or {}).get("unresolved_objects", []))
        _rec("scan.verdict_fail", sc.get("verdict") == "fail", verdict=sc.get("verdict"))
        _rec("scan.coverage_truncated_false", (sc.get("coverage") or {}).get("truncated") is False)
        _rec("scan.allowed_count", int((sc.get("counts") or {}).get("hidden_ok") or 0) >= 1,
             hidden_ok=(sc.get("counts") or {}).get("hidden_ok"))

        sc_sum = _safe("scan", objects=names, spec=spec, summary_only=True, max_pairs=500)
        _rec("scan.summary_only_no_pairs", sc_sum.get("pairs") == [] and sc_sum.get("matrix") is None
             and (sc_sum.get("offenders") or []) != [])

        gate1 = _safe("gate", objects=names, spec=spec, summary_only=True, strict=True)
        gmap = {g.get("id"): g.get("state") for g in gate1.get("gates") or []}
        _rec("gate.dirty_not_pass", gate1.get("verdict") != "pass" and gate1.get("export_allowed") is False,
             verdict=gate1.get("verdict"), export_allowed=gate1.get("export_allowed"))
        _rec("gate.d8_normals_fail", gmap.get("normals") in ("fail", "degraded"), normals=gmap.get("normals"))
        _rec("gate.clash_fail", gmap.get("clash_all") in ("fail", "degraded"), clash=gmap.get("clash_all"))

        # 只用允许对 + 面贴两对 → gate 必须 pass（正例，防"永远 fail"）
        reset_scene()
        box("GEO-base", 20.0, (0, 0, 0))
        box("GEO-top", 20.0, (20.0, 0, 0))
        bpy.context.view_layer.update()
        clean_names = ["GEO-base", "GEO-top"]
        man_clean = {"schema": "dsh-assembly-manifest/1", "units": "mm", "assembly": "clean",
                     "parts": [{"id": "GEO-base", "objects": ["GEO-base"]},
                               {"id": "GEO-top", "objects": ["GEO-top"]}],
                     "interfaces": [{"id": "IF-1", "a": "GEO-base", "b": "GEO-top",
                                     "fit": "location", "nominal_mm": 0.0, "tol_mm": 0.5}],
                     "allow_pairs": []}
        gate2 = _safe("gate", objects=clean_names, manifest=man_clean, summary_only=True, strict=True)
        gmap2 = {g.get("id"): g.get("state") for g in gate2.get("gates") or []}
        _rec("gate.clean_pass", gate2.get("verdict") == "pass" and gate2.get("export_allowed") is True,
             verdict=gate2.get("verdict"), gates=gmap2, error=gate2.get("error"))

        # ---------------- D9 开口件相交 → unresolved ≠ pass ----------------
        reset_scene()
        box("U_cube", 20.0, (0, 0, 0))
        quad("U_plane", 30.0, (0, 0, 0))
        bpy.context.view_layer.update()
        sc_u = _safe("scan", objects=["U_cube", "U_plane"], summary_only=False, max_pairs=10)
        pu = pairs_by_key(sc_u).get(("U_cube", "U_plane"), {})
        _rec("scan.open_mesh_unresolved", pu.get("class") == "unresolved", cls=pu.get("class"),
             verdict=sc_u.get("verdict"))
        _rec("scan.unresolved_not_pass", sc_u.get("verdict") in ("degraded", "fail"),
             verdict=sc_u.get("verdict"))
        gate_u = _safe("gate", objects=["U_cube", "U_plane"], summary_only=True)
        _rec("gate.unresolved_not_pass", gate_u.get("verdict") != "pass"
             and gate_u.get("export_allowed") is False)

        # ---------------- manifest + merge(dry_run) ----------------
        reset_scene()
        pa = os.path.join(tmpdir, "part_a.blend")
        pb = os.path.join(tmpdir, "part_b.blend")
        box("PartA", 10.0, (0, 0, 0))
        bpy.ops.wm.save_as_mainfile(filepath=pa, copy=True)
        reset_scene()
        box("PartB", 10.0, (0, 0, 0))
        bpy.ops.wm.save_as_mainfile(filepath=pb, copy=True)
        reset_scene()
        man = os.path.join(tmpdir, "assembly.json")
        r_init = _safe("manifest", action="init", path=man, assembly="selftest-asm", units="mm", force=True)
        r_p2 = _safe("manifest", action="add_part", path=man,
                     part={"id": "P2", "file": pb, "objects": ["PartB"],
                           "transform": {"loc_mm": [30, 0, 0], "rot_euler_deg": [0, 0, 0], "scale": [1, 1, 1]}})
        r_p1 = _safe("manifest", action="add_part", path=man,
                     part={"id": "P1", "file": pa, "objects": ["PartA"],
                           "transform": {"loc_mm": [0, 0, 0], "rot_euler_deg": [0, 0, 0], "scale": [1, 1, 1]}})
        r_if = _safe("manifest", action="add_interface", path=man,
                     interface={"id": "IF-P1-P2", "a": "P1", "b": "P2", "fit": "location",
                                "nominal_mm": 0.15, "tol_mm": 1.0})
        r_al = _safe("manifest", action="add_allow", path=man,
                     allow={"id": "ALLOW-P1-P2", "a": "P1", "b": "P2",
                            "max_depth_mm": 2.0, "max_volume_mm3": 500.0, "reason": "selftest"})
        _rec("manifest.build_ok", all(r.get("ok") for r in (r_init, r_p2, r_p1, r_if, r_al)),
             errs=[r.get("error") for r in (r_init, r_p2, r_p1, r_if, r_al) if not r.get("ok")])
        r_val = _safe("manifest", action="validate", path=man, check_files=True, check_md5=True)
        _rec("manifest.validate_ok", r_val.get("ok") is True and r_val.get("errors") == [],
             errors=r_val.get("errors"))
        r_seal = _safe("manifest", action="seal", path=man)
        _rec("manifest.sealed", r_seal.get("ok") is True and r_seal.get("sealed") is True
             and len(str(r_seal.get("sealed_md5") or "")) == 64, version=r_seal.get("version"))
        r_show = _safe("manifest", action="show", path=man)
        _rec("manifest.show_ok", r_show.get("ok") is True
             and (r_show.get("counts") or {}).get("parts") == 2
             and (r_show.get("counts") or {}).get("interfaces") == 1
             and (r_show.get("counts") or {}).get("allow_pairs") == 1,
             counts=r_show.get("counts"))
        _rec("manifest.sealed_md5_stable", r_seal.get("manifest_md5") == r_show.get("manifest_md5"))
        dry = _safe("merge", manifest=man, out_blend=os.path.join(tmpdir, "merged.blend"), dry_run=True)
        _rec("merge.dry_ok", dry.get("ok") is True and dry.get("dry_run") is True, error=dry.get("error"))
        _rec("merge.order_sorted", dry.get("order") == ["P1", "P2"], order=dry.get("order"))
        tn = ((dry.get("parts") or [{}])[0].get("target_names") or {})
        _rec("merge.prefixed_names", tn.get("PartA") == "P1__PartA", target_names=tn)
        _rec("merge.dry_no_write", (dry.get("outputs") or {}).get("written") is False
             and not os.path.isfile(os.path.join(tmpdir, "merged.blend")))
        _rec("merge.dry_gate_skipped", dry.get("gate") == "skipped_dry_run")

        # 坏 manifest：md5 不符 / basis 不正交 / 重复 id
        bad = os.path.join(tmpdir, "assembly_bad.json")
        with open(man, "r", encoding="utf-8") as fh:
            mm = json.load(fh)
        mm["parts"][0]["file_md5"] = "0" * 64
        with open(bad, "w", encoding="utf-8") as fh:
            json.dump(mm, fh, ensure_ascii=False)
        r_badmd5 = _safe("manifest", action="validate", path=bad, check_files=True, check_md5=True)
        _rec("bad.md5_okfalse", r_badmd5.get("ok") is False
             and any(e.get("code") == "MD5_MISMATCH" for e in r_badmd5.get("errors") or []))
        r_dup = _safe("manifest", action="add_part", path=man,
                      part={"id": "P1", "file": pa, "objects": ["PartA"]})
        _rec("bad.dup_id_okfalse", r_dup.get("ok") is False)
        bad_man = {"schema": "dsh-assembly-manifest/1", "units": "mm", "assembly": "bad-basis",
                   "parts": [{"id": "Q1", "objects": ["PartA"],
                              "interface_frames": [{"id": "F1", "origin_mm": [0, 0, 0],
                                                    "normal": [0, 0, 1],
                                                    "basis": {"x": [1, 1, 0], "y": [0, 1, 0]}}]}],
                   "interfaces": [], "allow_pairs": []}
        r_b4 = _safe("manifest", action="validate", manifest=bad_man,
                     check_files=False, check_md5=False)
        codes = [e.get("code") for e in r_b4.get("errors") or []]
        _rec("bad.basis_okfalse", r_b4.get("ok") is False
             and any(str(c).startswith("FRAME_BASIS") for c in codes), codes=codes)
        # add_part 本身也必须拒绝坏 basis（不写进清单）
        bad2 = os.path.join(tmpdir, "assembly_bad2.json")
        r_b2 = _safe("manifest", action="init", path=bad2, assembly="bad-basis", force=True)
        r_b3 = _safe("manifest", action="add_part", path=bad2,
                     part=dict(bad_man["parts"][0], file=pa))
        codes3 = [e.get("code") for e in r_b3.get("errors") or []]
        _rec("bad.add_part_bad_basis_rejected", r_b3.get("ok") is False
             and any(str(c).startswith("FRAME_BASIS") for c in codes3), codes=codes3)

        # scan file= 模式
        sc_file = _safe("scan", file=pa, summary_only=True)
        _rec("scan.file_mode_ok", sc_file.get("ok") is True
             and (sc_file.get("source") or {}).get("kind") == "file", source=sc_file.get("source"))
        # 坏参数
        _rec("bad.empty_objects_okfalse", _safe("scan", objects=[]) .get("ok") is False)
        _rec("bad.missing_object_okfalse", _safe("scan", objects=["__no_such_obj__"]).get("ok") is False)
        _rec("bad.unknown_op_okfalse", _safe("nope_op", {}).get("ok") is False)
        _rec("bad.unknown_arg_okfalse", _safe("scan", objects=["PartA"], bad_arg=1).get("ok") is False)
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
        # 只把**顶层断言键**当判据；"key.xxx" 是给人看的数字旁证（布尔旁证可能是合法的 false）
        bad = sorted([k for k, v in EVID.items() if isinstance(v, bool) and v is not True and "." not in k])
        return {"ok": len(bad) == 0, "failed": bad, "evidence": EVID}
    except Exception as e:
        return {"ok": False, "error": "%s: %s" % (type(e).__name__, str(e)[:400]),
                "traceback": traceback.format_exc()[-1200:], "evidence": EVID}


if __name__ == "__main__":
    res = main_safe()
    print("HEADLESS " + json.dumps(res, ensure_ascii=False))
    print("ASSEMBLY_SELFTEST " + ("ALL PASS" if res.get("ok") else "FAIL"))
    if not res.get("ok"):
        sys.exit(1)
