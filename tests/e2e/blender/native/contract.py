# -*- coding: utf-8 -*-
"""Native E2E: contract core (S1/S2) in headless Blender (T11b.2).

Ported from cases/contract_selftest.py.  The archived wrapper was marked GUI
only because it also exercised view evidence (OpenGL capture).  This module
preloads only contract and covers the headless core: registration, envelope,
interference, interface, destructive guard, tri-state verify, fingerprint,
drift demotion, report.  The evidence/stale checks move to the GUI module
(gui/native/contract_evidence.py) in T11b.7.
"""

from __future__ import annotations

import json

import bpy


def box(name, size, loc):
    bpy.ops.mesh.primitive_cube_add(size=1.0, location=loc)
    ob = bpy.context.object
    ob.name = name
    ob.scale = size
    ob.select_set(False)
    return ob


def build_scene():
    box("Seat", (0.45, 0.45, 0.05), (0, 0, 0.475))
    box("Backrest", (0.45, 0.03, 0.50), (0, 0.25, 0.70))
    box("Tenon", (0.10, 0.06, 0.06), (0, 0.195, 0.465))
    box("Stray", (0.10, 0.10, 0.10), (0, 0, 3.0))          # 越界
    box("Inter1", (0.20, 0.20, 0.20), (1.0, 0, 0.5))
    box("Inter2", (0.20, 0.20, 0.20), (1.05, 0, 0.5))      # 与 Inter1 穿插
    bpy.context.view_layer.update()
    bpy.context.evaluated_depsgraph_get().update()


def test_contract_core(ctx):
    api = ctx.api("contract")
    ctx.check("contract version is 3", api["version"] == 3, api.get("version"))
    build_scene()

    r = ctx.call(api, "reset")
    ctx.check("reset 清空契约", r.get("ok") and r.get("reset"))

    # --- 注册
    r = ctx.call(api, "register_component", "seat", ["Seat"], ["seat"], "座椅")
    ctx.check("注册组件 seat", r.get("ok") and r.get("missing_objects") == [])
    r = ctx.call(api, "register_component", "backrest", ["Backrest", "Tenon"], ["backrest"], "靠背板 + 内榫")
    ctx.check("注册组件 backrest", r.get("ok"))
    r = ctx.call(api, "register_component", "ghost", ["NoSuchObject"])
    ctx.check("组件缺失对象会被标出", r.get("missing_objects") == ["NoSuchObject"])

    r = ctx.call(api, "register_connection", "joint", "seat", "backrest",
                 ["attach", "insert"],
                 {"dy": {"range": [0.0, 0.03]}, "tenon_len": {"range": [0.03, 0.10]}},
                 ["boolean_union", "weld"], "medium", ["probe_dismount"], "proposed")
    ctx.check("注册连接 joint", r.get("ok") and r.get("status") == "proposed")
    r = ctx.call(api, "register_connection", "bad", "seat", "not_registered")
    ctx.check("连接端点未注册时报错", r.get("ok") is False)

    # --- 包络
    r = ctx.call(api, "register_envelope", "chair_env",
                 [-0.5, -0.5, 0.0], [0.5, 0.5, 1.5], ["Seat", "Backrest", "Tenon", "Stray"])
    ctx.check("注册包络", r.get("ok"))
    r = ctx.call(api, "check_envelope", "chair_env")
    viol = r["envelopes"][0]["violations"]
    names = [o["name"] for o in r["envelopes"][0]["objects"]]
    ctx.check("包络能抓出越界物件（Stray）", viol >= 1 and "Stray" in names, names)

    # --- 干涉（AABB + BVH）
    r = ctx.call(api, "check_interference", ["Inter"], use_bvh=True, limit=50)
    ctx.check("干涉检查：AABB 候选 >= 1", r["pairs_aabb"] >= 1, r)
    ctx.check("干涉检查：BVH 精查命中 >= 1（互相穿插的方块）", (r["pairs_bvh"] or 0) >= 1, r)
    ctx.check("干涉检查耗时被记录", "bbox_ms" in r and "bvh_ms" in r)

    # --- 接口间隙
    r = ctx.call(api, "check_interface", "joint")
    ctx.check("接口间隙可算", r.get("ok") and "gap_m" in r, r)

    # --- 破坏性门控
    r = ctx.call(api, "destructive_guard", "boolean_union", ["Backrest"])
    ctx.check("未判别连接的 Boolean 被拦",
              r.get("allowed") is False and r.get("code") == "Unsupported Destructive Merge", r)
    r2 = ctx.call(api, "destructive_guard", "move", ["Backrest"])
    ctx.check("非破坏性操作不拦", r2.get("allowed") is True)

    # --- 三态判定（S2）
    r = ctx.call(api, "verify", "joint", err=184, tolerance=220, identifiable=["dy"],
                 evidence_labels=["side_view"], note="外部证据")
    ctx.check("外部证据：tenon_len 不可辨识 → unresolved", r.get("verdict") == "unresolved", r)
    r = ctx.call(api, "verify", "joint", probe_err=0.005, probe_tolerance=0.01, note="探针")
    ctx.check("探针读数达标 → supported", r.get("verdict") == "supported", r)
    r = ctx.call(api, "advance", "joint", "proposed", "重开判别")
    ctx.check("可手动回退状态", r.get("status") == "proposed")
    r = ctx.call(api, "flip", "joint", "attach", "换假设重测")
    ctx.check("翻转候选并记历史", r.get("ok") and r.get("status") == "testing", r)
    r = ctx.call(api, "flip", "joint", "nonsense")
    ctx.check("非法候选被拒", r.get("ok") is False)

    # --- V0.9：#7 几何指纹 + 证据随改动失效；#8 判定漂移自动降级
    fp0 = ctx.call(api, "fingerprint", ["Seat", "Backrest"])
    ctx.check("指纹可取（对象/面数/bbox + 12 位 digest）",
              fp0.get("ok") and len(fp0["fingerprint"]["digest"]) == 12
              and fp0["fingerprint"]["objects"] == 2, fp0)
    r = ctx.call(api, "verify", "joint", probe_err=0.005, probe_tolerance=0.01, note="建立 bbox 快照")
    ctx.check("verify 记录 bbox 快照", (r.get("bbox_snapshot") or {}).get("diagonal") is not None
              and r.get("verdict") == "supported", r)
    st0 = ctx.call(api, "status")
    ctx.check("无漂移时不降级", st0.get("demoted_now") == [], st0.get("demoted_now"))
    ob_b = bpy.data.objects.get("Backrest")
    loc_before = tuple(ob_b.location)
    ob_b.location = (loc_before[0], loc_before[1] + 1.2, loc_before[2])   # 远超 10% 对角线
    bpy.context.view_layer.update()
    st1 = ctx.call(api, "status")
    ctx.check("几何漂移 → supported 自动降级 unresolved", "joint" in (st1.get("demoted_now") or []),
              st1.get("demoted_now"))
    d1 = (st1.get("detail") or {}).get("joint") or {}
    ctx.check("降级理由留在 verdict.demoted",
              bool(((d1.get("verdict") or {}).get("demoted") or {}).get("reason")), d1.get("verdict"))
    ctx.check("降级后 status 变 unresolved", d1.get("status") == "unresolved", d1.get("status"))
    ob_b.location = loc_before
    bpy.context.view_layer.update()

    # --- 报告
    p = ctx.test_dir / "contract_report.md"
    r = ctx.call(api, "report", str(p))
    ok_file = p.is_file()
    txt = p.read_text(encoding="utf-8") if ok_file else ""
    ctx.check("报告落盘", r.get("ok") and ok_file, r)
    ctx.check("报告含连接与判定",
              ("joint" in txt and "unresolved" in txt.lower()) or "supported" in txt.lower(), txt[:200])
    st = ctx.call(api, "status")
    ctx.check("status 概览正常", st.get("components") == 3 and st.get("connections") == 1, st)

    # Headless contract must not silently acquire the GUI evidence path.
    ctx.check("headless core 没有 view API（证据检查留给 GUI 模块）",
              not hasattr(ctx.K, "dsh_view_api"))
