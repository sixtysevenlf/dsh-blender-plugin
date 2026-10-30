# -*- coding: utf-8 -*-
"""tests/assembly_repair_selftest.py —— runtime/assembly.py 的 asm_repair / fix_clash / fix_interference 真机自检。

跑法（插件无头通道）：
    blender_rt_headless(preload="assembly,audit,contract,clearance,gate,mesh_fix,geo,txn",
                        engine="none", factory_startup=True, timeout_ms=300000,
                        script_file="<包路径>/tests/assembly_repair_selftest.py")

独立跑（自己按序 exec kit/audit/contract/clearance/gate/mesh_fix/geo/txn/assembly）：
    blender -b --factory-startup --python tests/assembly_repair_selftest.py

覆盖：
  1) 两立方体 2mm 互穿（+ 底板，保证复扫后仍连通不误报浮块）
     → dry_run 有计划（translate）且场景指纹不变；dry_run=false 后 clash=0
  2) 布尔：max_move_mm 不够 + allow_boolean=false ⇒ blocked；allow_boolean=true ⇒ subtract 计划；
     执行后 clash=0 且 cutter 原件仍在（布尔用副本）
  3) 浮块场景 → snap 计划（或 blocked 且写明原因）；dry_run 不动场景
  4) fix_interference 规划器只读 + a/b 过滤；plan_only=false 拒绝
  5) 别名 fix_clash 可用；help/ops 已注册；干净场景 dry_run ⇒ verdict=pass
  6) 坏参数：负 max_move / 负 clearance / max_rounds=0 / dry_run 非布尔 / 空 objects / 未知参数 → ok:false
"""
import json
import os
import sys
import traceback

HERE = os.path.dirname(os.path.abspath(__file__)) if "__file__" in globals() else os.getcwd()
RT = os.path.normpath(os.path.join(HERE, "..", "runtime"))
MODULES = ("kit.py", "audit.py", "contract.py", "clearance.py", "gate.py",
           "mesh_fix.py", "geo.py", "txn.py", "assembly.py")


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


def box(name, size, loc):
    """size: 数字（立方体）或 (sx, sy, sz)；单位=场景单位。"""
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
    """只看会被修复动作改变的东西：世界位置/旋转 + 网格规模（dry_run 必须零变化）。"""
    out = {}
    for ob in bpy.context.scene.objects:
        if ob.type != "MESH":
            continue
        mw = ob.matrix_world.copy()
        out[str(ob.name)] = {
            "loc": [round(float(x), 6) for x in mw.translation],
            "rot": [round(float(x), 6) for x in mw.to_euler()],
            "verts": int(len(ob.data.vertices)),
            "polys": int(len(ob.data.polygons)),
        }
    return out


EVID = {}


def _rec(key, cond, **nums):
    # 顶层断言键用 __ 分组（不能带 "."）；"group.name.detail" 只作人类可读旁证，不参与判定
    EVID[str(key).replace(".", "__")] = bool(cond)
    for k, v in nums.items():
        EVID[str(key) + "." + k] = v
    return bool(cond)


def _counts(rep):
    return ((rep.get("after") or {}).get("counts") or {}) if isinstance(rep, dict) else {}


# ────────────────────────────── 主流程 ──────────────────────────────
def main():
    prev_scale = float(bpy.context.scene.unit_settings.scale_length)
    try:
        bpy.context.scene.unit_settings.scale_length = 0.001      # 1 单位 = 1 mm
        # ================= 1) 两立方体 2mm 互穿：dry_run 零改动；执行后 clash=0 =================
        reset_scene()
        box("RP_a", 20.0, (0.0, 0.0, 10.0))
        box("RP_b", 20.0, (18.0, 0.0, 10.0))                     # 与 RP_a 沿 X 互穿 2mm
        box("RP_base", (60.0, 60.0, 10.0), (9.0, 0.0, -5.0))     # 顶面 z=0，与 a/b 面贴（contact）
        bpy.context.view_layer.update()
        names = ["RP_a", "RP_b", "RP_base"]
        sc0 = _safe("scan", objects=names, summary_only=False)
        _rec("setup.scan_ok", sc0.get("ok") is True, error=sc0.get("error"))
        _rec("setup.clash_present", int((sc0.get("counts") or {}).get("clash") or 0) >= 1,
             counts=sc0.get("counts"))
        fp0 = scene_fingerprint()
        dry = _safe("repair", objects=names)
        plan = dry.get("plan") or []
        _rec("dry.ok", dry.get("ok") is True, error=dry.get("error"))
        _rec("dry.verdict_planned", dry.get("verdict") == "planned", verdict=dry.get("verdict"))
        _rec("dry.plan_translate", any(a.get("method") == "translate" for a in plan),
             methods=[a.get("method") for a in plan][:8])
        _rec("dry.plan_pair", any(sorted(a.get("pair") or []) == ["RP_a", "RP_b"] for a in plan),
             pairs=[a.get("pair") for a in plan][:8])
        _rec("dry.no_scene_change", scene_fingerprint() == fp0)
        _rec("dry.readonly", dry.get("readonly") is True and dry.get("dry_run") is True)
        _rec("dry.expected", int((dry.get("expected") or {}).get("actions") or 0) == len(plan),
             expected=(dry.get("expected") or {}).get("actions"), plan=len(plan))
        ex = _safe("repair", objects=names, dry_run=False, max_rounds=3)
        exc = _counts(ex)
        res = ex.get("residual") or {}
        _rec("exec.ok", ex.get("ok") is True, error=ex.get("error"), verdict=ex.get("verdict"))
        _rec("exec.clash_zero", int(exc.get("clash") or 0) == 0 and int(res.get("clash") or 0) == 0,
             counts=exc, residual=res)
        _rec("exec.contained_zero", int(exc.get("contained") or 0) == 0)
        _rec("exec.float_zero", int(exc.get("float") or 0) == 0)
        _rec("exec.applied", len(ex.get("applied") or []) >= 1,
             applied=[a.get("method") for a in ex.get("applied") or []])
        _rec("exec.scene_moved", scene_fingerprint() != fp0)
        snapinfo = ex.get("snapshot") or {}
        _rec("exec.snapshot", isinstance(snapinfo, dict) and bool(snapinfo.get("label")),
             snapshot=snapinfo.get("label"))
        # 干净场景再 dry_run：verdict 必须是 pass（防"永远 fail/blocked"）
        cln = _safe("repair", objects=names)
        _rec("clean.verdict_pass", cln.get("ok") is True and cln.get("verdict") == "pass",
             verdict=cln.get("verdict"))
        cln_ex = _safe("repair", objects=names, dry_run=False)
        _rec("clean.no_action_execute", cln_ex.get("ok") is True
             and cln_ex.get("stage") == "nothing_to_do" and not (cln_ex.get("applied") or []),
             stage=cln_ex.get("stage"), ok=cln_ex.get("ok"))

        # 坏参数（空产出/坏数值一律 ok:false，不静默成功）
        _rec("bad.max_move", _safe("repair", objects=names, max_move_mm=-1).get("ok") is False)
        _rec("bad.clearance", _safe("repair", objects=names, clearance_mm=-0.5).get("ok") is False)
        _rec("bad.max_rounds", _safe("repair", objects=names, max_rounds=0).get("ok") is False)
        _rec("bad.dry_run", _safe("repair", objects=names, dry_run="maybe").get("ok") is False)
        _rec("bad.max_move_str", _safe("repair", objects=names, max_move_mm="abc").get("ok") is False)
        _rec("bad.empty_objects", _safe("repair", objects=[]).get("ok") is False)
        _rec("bad.unknown_arg", _safe("repair", objects=names, nope=1).get("ok") is False)
        _rec("bad.unknown_op", _safe("nope_op", {}).get("ok") is False)

        # 别名 / help 注册
        h = _safe("help")
        hops = h.get("ops") or {}
        _rec("api.help_repair", "repair" in hops and "fix_interference" in hops,
             ops=sorted(hops.keys()))
        _rec("api.help_alias", ((hops.get("repair") or {}).get("alias")) == "fix_clash",
             alias=(hops.get("repair") or {}).get("alias"))
        fc = _safe("fix_clash", objects=names)
        _rec("alias.fix_clash", fc.get("ok") is True and fc.get("dry_run") is True
             and fc.get("verdict") in ("pass", "planned", "blocked"), verdict=fc.get("verdict"))
        fi_clean = _safe("fix_interference", objects=names)
        _rec("alias.fix_interference", fi_clean.get("ok") is True
             and fi_clean.get("planner") == "fix_interference" and fi_clean.get("readonly") is True)
        fi_bad = _safe("fix_interference", objects=names, plan_only=False)
        _rec("alias.fix_interference_plan_only", fi_bad.get("ok") is False)

        # ================= 2) 布尔：allow_boolean 才规划/执行；cutter 原件保留 =================
        reset_scene()
        box("BL_a", 20.0, (0.0, 0.0, 10.0))
        box("BL_b", 20.0, (18.0, 0.0, 10.0))
        box("BL_base", (60.0, 60.0, 10.0), (9.0, 0.0, -5.0))
        bpy.context.view_layer.update()
        names2 = ["BL_a", "BL_b", "BL_base"]
        fp2 = scene_fingerprint()
        blocked_only = _safe("repair", objects=names2, max_move_mm=0.1)
        _rec("bool.blocked_without_allow",
             not any(a.get("method") == "subtract" for a in (blocked_only.get("plan") or []))
             and len(blocked_only.get("blocked") or []) >= 1,
             blocked=[str(b.get("reason"))[:80] for b in blocked_only.get("blocked") or []][:4])
        allowed = _safe("repair", objects=names2, max_move_mm=0.1, allow_boolean=True)
        plan2 = allowed.get("plan") or []
        _rec("bool.subtract_planned", any(a.get("method") == "subtract" for a in plan2),
             methods=[a.get("method") for a in plan2])
        _rec("bool.dry_no_change", scene_fingerprint() == fp2)
        fi2 = _safe("fix_interference", a="BL_a", b="BL_b", max_move_mm=0.1, allow_boolean=True)
        _rec("planner.interference_subtract", fi2.get("ok") is True
             and any(a.get("method") == "subtract" for a in (fi2.get("plan") or [])),
             plan=[a.get("method") for a in fi2.get("plan") or []])
        _rec("planner.readonly", scene_fingerprint() == fp2)
        no_snap = _safe("repair", objects=names2, dry_run=False, max_move_mm=0.1,
                        allow_boolean=True, snapshot=False)
        _rec("bool.no_snapshot_refused", no_snap.get("ok") is False
             and no_snap.get("stage") == "no_executable_action" and not (no_snap.get("applied") or []),
             stage=no_snap.get("stage"), ok=no_snap.get("ok"))
        _rec("bool.no_snapshot_no_change", scene_fingerprint() == fp2)
        bex = _safe("repair", objects=names2, dry_run=False, max_move_mm=0.1,
                    allow_boolean=True, max_rounds=2)
        _rec("bool.executed", any(a.get("method") == "subtract" for a in (bex.get("applied") or [])),
             applied=[a.get("method") for a in bex.get("applied") or []])
        _rec("bool.cutter_preserved", bpy.data.objects.get("BL_b") is not None)
        _rec("bool.clash_zero", int(_counts(bex).get("clash") or 0) == 0,
             counts=_counts(bex), residual=bex.get("residual"))
        _rec("bool.ok", bex.get("ok") is True, verdict=bex.get("verdict"), error=bex.get("error"))

        # ================= 2b) 完全包含（面无相交）→ contained 必须有平移计划，dry_run 不动 =================
        reset_scene()
        box("CT_big", 40.0, (0.0, 0.0, 0.0))
        box("CT_small", 6.0, (0.0, 0.0, 0.0))                    # 整体在大件内、面无相交
        bpy.context.view_layer.update()
        fp_ct = scene_fingerprint()
        ct = _safe("repair", objects=["CT_big", "CT_small"])
        ctplan = ct.get("plan") or []
        _rec("contained.plan_translate",
             any(a.get("method") == "translate" and a.get("class") == "contained" for a in ctplan),
             plan=[{"method": a.get("method"), "class": a.get("class"), "target": a.get("target"),
                    "required_mm": a.get("required_mm")} for a in ctplan][:6])
        _rec("contained.target_is_small", any(a.get("target") == "CT_small" for a in ctplan))
        _rec("contained.requires_long_move", any(float(a.get("required_mm") or 0) > 20.0 for a in ctplan))
        _rec("contained.dry_no_change", scene_fingerprint() == fp_ct)

        # ================= 3) 浮块场景 → snap 计划（或 blocked 写明原因），dry_run 不动 =================
        reset_scene()
        box("FL_base", 20.0, (0.0, 0.0, 0.0))
        box("FL_partner", 20.0, (20.0, 0.0, 0.0))                # 面贴 contact
        box("FL_float", 10.0, (0.0, 0.0, 60.0))                  # 与主件相距 45mm → 浮块
        bpy.context.view_layer.update()
        names3 = ["FL_base", "FL_partner", "FL_float"]
        sc3 = _safe("scan", objects=names3, summary_only=False)
        _rec("float.detected", int((sc3.get("counts") or {}).get("float") or 0) >= 1,
             counts=sc3.get("counts"), floaters=sc3.get("floaters"))
        fp3 = scene_fingerprint()
        fl = _safe("repair", objects=names3)
        plan3 = fl.get("plan") or []
        blocked3 = [x for x in (fl.get("blocked") or []) if isinstance(x, dict)]
        _rec("float.plan_or_blocked",
             any(a.get("method") == "snap" for a in plan3)
             or any(x.get("kind") == "float" for x in blocked3),
             plan_methods=[a.get("method") for a in plan3],
             blocked_kinds=[x.get("kind") for x in blocked3])
        _rec("float.dry_ok", fl.get("ok") is True, error=fl.get("error"))
        _rec("float.dry_no_change", scene_fingerprint() == fp3)
        _rec("float.dry_verdict", fl.get("verdict") in ("planned", "blocked"), verdict=fl.get("verdict"))
    finally:
        bpy.context.scene.unit_settings.scale_length = prev_scale
        reset_scene()


def main_safe():
    try:
        main()
        # 只把**顶层断言键**当判据；"key.xxx" 是给人看的数字旁证
        bad = sorted([k for k, v in EVID.items() if isinstance(v, bool) and v is not True and "." not in k])
        return {"ok": len(bad) == 0, "failed": bad, "evidence": EVID}
    except Exception as e:
        return {"ok": False, "error": "%s: %s" % (type(e).__name__, str(e)[:400]),
                "traceback": traceback.format_exc()[-1600:], "evidence": EVID}


if __name__ == "__main__":
    res = main_safe()
    print("HEADLESS " + json.dumps(res, ensure_ascii=False, separators=(",", ":")))
    print("ASSEMBLY_REPAIR_SELFTEST " + ("ALL PASS" if res.get("ok") else "FAIL"))
    if not res.get("ok"):
        sys.exit(1)
