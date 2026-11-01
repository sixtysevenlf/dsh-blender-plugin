# -*- coding: utf-8 -*-
"""GUI native E2E: contract evidence ledger and staleness (T11b.7).

The headless module native/contract.py covers the contract core.  The archived
case also captured OpenGL evidence and checked ledger staleness; this module is
the G-layer half and runs in a real (non-background) Blender window.
"""

from __future__ import annotations



def _box(name, size, loc):
    import bmesh
    import bpy
    me = bpy.data.meshes.new(name)
    bm = bmesh.new()
    bmesh.ops.create_cube(bm, size=1.0)
    bm.to_mesh(me)
    bm.free()
    ob = bpy.data.objects.new(name, me)
    bpy.context.scene.collection.objects.link(ob)
    ob.location = loc
    ob.scale = size
    ob.select_set(False)
    bpy.context.view_layer.update()
    return ob


def _scene():
    import bpy
    _box("Seat", (0.45, 0.45, 0.05), (0, 0, 0.475))
    _box("Backrest", (0.45, 0.03, 0.50), (0, 0.25, 0.70))
    _box("Tenon", (0.10, 0.06, 0.06), (0, 0.195, 0.465))
    bpy.context.view_layer.update()
    bpy.context.evaluated_depsgraph_get().update()


def _register(api, ctx):
    ctx.call(api, "reset")
    ctx.call(api, "register_component", "seat", ["Seat"], ["seat"], "座椅")
    ctx.call(api, "register_component", "backrest", ["Backrest", "Tenon"], ["backrest"], "靠背板 + 内榫")
    ctx.call(api, "register_connection", "joint", "seat", "backrest",
             ["attach", "insert"],
             {"dy": {"range": [0.0, 0.03]}, "tenon_len": {"range": [0.03, 0.10]}},
             ["boolean_union", "weld"], "medium", ["probe_dismount"], "proposed")


def test_contract_evidence(ctx):
    import bpy
    api = ctx.api("contract")
    _scene()
    _register(api, ctx)

    # --- evidence capture + ledger
    side = {"from": [-2.6, 0.0, 0.78], "look_at": [0.0, 0.16, 0.66],
            "width": 240, "height": 150, "background": False, "overlays": False,
            "path": str(ctx.test_dir / "evidence_side.png")}
    r = ctx.call(api, "evidence", "side_view", side)
    ctx.check("出图 + md5 记账", bool(r.get("ok")) and len(str((r.get("evidence") or {}).get("md5"))) == 32, r)
    ctx.check("PNG 文件真实落盘", (ctx.test_dir / "evidence_side.png").is_file(), r.get("error"))
    led = ctx.call(api, "ledger")
    ctx.check("账本里有这条证据", len(led.get("evidence") or []) >= 1, len(led.get("evidence") or []))

    # --- tri-state prerequisites for the stale probe
    r = ctx.call(api, "verify", "joint", err=184, tolerance=220, identifiable=["dy"],
                 evidence_labels=["side_view"], note="外部证据")
    ctx.check("外部证据：tenon_len 不可辨识 → unresolved", r.get("verdict") == "unresolved", r.get("verdict"))
    r = ctx.call(api, "verify", "joint", probe_err=0.005, probe_tolerance=0.01, note="建立 bbox 快照")
    ctx.check("探针读数达标 → supported", r.get("verdict") == "supported", r.get("verdict"))

    # --- fingerprint-bound evidence, then mutate the source
    stale_spec = {"from": [-2.6, 0.0, 0.78], "look_at": [0.0, 0.16, 0.66],
                  "width": 200, "height": 120, "background": False, "overlays": False,
                  "path": str(ctx.test_dir / "stale_probe.png")}
    r = ctx.call(api, "evidence", "stale_probe", stale_spec, "指纹绑定探针", ["Backrest"])
    ctx.check("证据绑指纹（fp_objects 记名）",
              bool(r.get("ok")) and (r.get("evidence") or {}).get("fp_objects") == ["Backrest"], r.get("error") or r)
    led0 = ctx.call(api, "ledger")
    hit0 = [e for e in (led0.get("evidence") or []) if e.get("label") == "stale_probe"]
    ctx.check("未改动时证据不 stale", bool(hit0) and hit0[0].get("stale") is False, hit0[:1])

    ob_b = bpy.data.objects.get("Backrest")
    scale_before = tuple(ob_b.scale)
    ob_b.scale = (scale_before[0], scale_before[1], scale_before[2] * 1.5)
    bpy.context.view_layer.update()
    led1 = ctx.call(api, "ledger")
    hit1 = [e for e in (led1.get("evidence") or []) if e.get("label") == "stale_probe"]
    ctx.check("源一变 → 证据自动 stale",
              bool(hit1) and hit1[0].get("stale") is True
              and led1.get("stale_evidence", 0) >= 1
              and "stale_probe" in (led1.get("stale_labels") or []),
              {"stale": hit1[0].get("stale") if hit1 else None, "count": led1.get("stale_evidence")})
    ob_b.scale = scale_before
    bpy.context.view_layer.update()