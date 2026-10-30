# -*- coding: utf-8 -*-
"""契约层自检（S1+S2）—— 不需要 GUI，纯无头可跑，约 1–2 s。

跑法：
    blender -b --factory-startup --python tests/contract_selftest.py -- <outdir>
    # 或插件：blender_rt_headless(preload="view,contract",
    #     script="exec(open('<本文件>').read())", outdir="D:...")

覆盖：组件/连接注册 · 包络越界 · 干涉（AABB+BVH）· 接口间隙 · 破坏性门控 ·
      证据记账（md5）· 三态判定（unresolved → supported）· 翻转 · 报告生成。
"""
import bpy, json, math, os, sys

FAILS = []
OKS = []


def check(name, cond, detail=""):
    (OKS if cond else FAILS).append({"name": name, "detail": detail})
    print(("  ok   " if cond else "  FAIL ") + name + ("" if cond else "  <- " + str(detail)))


def box(name, size, loc):
    bpy.ops.mesh.primitive_cube_add(size=1.0, location=loc)
    ob = bpy.context.object
    ob.name = name
    ob.scale = size
    ob.select_set(False)
    return ob


def clean():
    bpy.ops.object.select_all(action="SELECT")
    bpy.ops.object.delete()
    for m in list(bpy.data.meshes):
        bpy.data.meshes.remove(m)
    bpy.context.view_layer.update()


def main():
    outdir = None
    if "--" in sys.argv:
        rest = sys.argv[sys.argv.index("--") + 1:]
        outdir = rest[-1] if rest else None
    outdir = outdir or os.path.join(os.path.expanduser("~"), "contract_selftest_out")
    os.makedirs(outdir, exist_ok=True)

    # --- 载入契约层（两种方式：preload 已挂 K，或就地 exec 源码）
    K = sys.modules.get("dsh_rt_kernel")
    if K is None or not hasattr(K, "dsh_contract_api"):
        here = os.path.dirname(os.path.abspath(__file__))
        src = open(os.path.join(os.path.dirname(here), "runtime", "contract.py"), encoding="utf-8").read()
        exec(compile(src, "contract.py", "exec"), globals())
    api = K.dsh_contract_api
    print("CONTRACT_SELFTEST version=%s" % api["version"])

    # --- 场景：座椅 + 靠背 + 内部榫 + 一个越界杂物 + 一对互相穿插的方块
    clean()
    box("Seat", (0.45, 0.45, 0.05), (0, 0, 0.475))
    box("Backrest", (0.45, 0.03, 0.50), (0, 0.25, 0.70))
    box("Tenon", (0.10, 0.06, 0.06), (0, 0.195, 0.465))
    box("Stray", (0.10, 0.10, 0.10), (0, 0, 3.0))          # 越界
    box("Inter1", (0.20, 0.20, 0.20), (1.0, 0, 0.5))
    box("Inter2", (0.20, 0.20, 0.20), (1.05, 0, 0.5))      # 与 Inter1 穿插
    bpy.context.view_layer.update()
    bpy.context.evaluated_depsgraph_get().update()

    r = json.loads(api["reset"]())
    check("reset 清空契约", r.get("ok") and r.get("reset"))

    # --- 注册
    r = json.loads(api["register_component"]("seat", ["Seat"], ["seat"], "座椅"))
    check("注册组件 seat", r.get("ok") and r.get("missing_objects") == [])
    r = json.loads(api["register_component"]("backrest", ["Backrest", "Tenon"], ["backrest"], "靠背板 + 内榫"))
    check("注册组件 backrest", r.get("ok"))
    r = json.loads(api["register_component"]("ghost", ["NoSuchObject"]))
    check("组件缺失对象会被标出", r.get("missing_objects") == ["NoSuchObject"])

    r = json.loads(api["register_connection"](
        "joint", "seat", "backrest", ["attach", "insert"],
        {"dy": {"range": [0.0, 0.03]}, "tenon_len": {"range": [0.03, 0.10]}},
        ["boolean_union", "weld"], "medium", ["probe_dismount"], "proposed"))
    check("注册连接 joint", r.get("ok") and r.get("status") == "proposed")
    r = json.loads(api["register_connection"]("bad", "seat", "not_registered"))
    check("连接端点未注册时报错", r.get("ok") is False)

    # --- 包络
    r = json.loads(api["register_envelope"]("chair_env", [-0.5, -0.5, 0.0], [0.5, 0.5, 1.5], ["Seat", "Backrest", "Tenon", "Stray"]))
    check("注册包络", r.get("ok"))
    r = json.loads(api["check_envelope"]("chair_env"))
    viol = r["envelopes"][0]["violations"]
    names = [o["name"] for o in r["envelopes"][0]["objects"]]
    check("包络能抓出越界物件（Stray）", viol >= 1 and "Stray" in names, names)

    # --- 干涉（AABB + BVH）
    r = json.loads(api["check_interference"](["Inter"], use_bvh=True, limit=50))
    check("干涉检查：AABB 候选 >= 1", r["pairs_aabb"] >= 1, r)
    check("干涉检查：BVH 精查命中 >= 1（互相穿插的方块）", (r["pairs_bvh"] or 0) >= 1, r)
    check("干涉检查耗时被记录", "bbox_ms" in r and "bvh_ms" in r)

    # --- 接口间隙
    r = json.loads(api["check_interface"]("joint"))
    check("接口间隙可算", r.get("ok") and "gap_m" in r, r)

    # --- 破坏性门控
    r = json.loads(api["destructive_guard"]("boolean_union", ["Backrest"]))
    check("未判别连接的 Boolean 被拦", r.get("allowed") is False and r.get("code") == "Unsupported Destructive Merge", r)
    r2 = json.loads(api["destructive_guard"]("move", ["Backrest"]))
    check("非破坏性操作不拦", r2.get("allowed") is True)

    # --- 证据记账
    K = sys.modules.get("dsh_rt_kernel")
    if hasattr(K, "dsh_view_api"):
        r = json.loads(api["evidence"]("side_view", {"from": [-2.6, 0.0, 0.78], "look_at": [0.0, 0.16, 0.66],
                                                       "width": 240, "height": 150, "background": False,
                                                       "overlays": False, "path": os.path.join(outdir, "evidence_side.png")}))
        check("出图 + md5 记账", r.get("ok") and len(r["evidence"]["md5"]) == 32, r)
        led = json.loads(api["ledger"]())
        check("账本里有这条证据", len(led["evidence"]) >= 1)
    else:
        print("  skip 证据记账（没有 K.dsh_view_api；用 preload=view 可测）")

    # --- 三态判定（S2）
    r = json.loads(api["verify"]("joint", err=184, tolerance=220, identifiable=["dy"],
                                 evidence_labels=["side_view"], note="外部证据"))
    check("外部证据：tenon_len 不可辨识 → unresolved", r.get("verdict") == "unresolved", r)
    r = json.loads(api["verify"]("joint", probe_err=0.005, probe_tolerance=0.01, note="探针"))
    check("探针读数达标 → supported", r.get("verdict") == "supported", r)
    r = json.loads(api["advance"]("joint", "proposed", "重开判别"))
    check("可手动回退状态", r.get("status") == "proposed")
    r = json.loads(api["flip"]("joint", "attach", "换假设重测"))
    check("翻转候选并记历史", r.get("ok") and r.get("status") == "testing", r)
    r = json.loads(api["flip"]("joint", "nonsense"))
    check("非法候选被拒", r.get("ok") is False)

    # --- V0.9：#7 几何指纹 + 证据随改动失效；#8 判定漂移自动降级
    fp0 = json.loads(api["fingerprint"](["Seat", "Backrest"]))
    check("指纹可取（对象/面数/bbox + 12 位 digest）",
          fp0.get("ok") and len(fp0["fingerprint"]["digest"]) == 12 and fp0["fingerprint"]["objects"] == 2, fp0)
    r = json.loads(api["verify"]("joint", probe_err=0.005, probe_tolerance=0.01, note="建立 bbox 快照"))
    check("verify 记录 bbox 快照", (r.get("bbox_snapshot") or {}).get("diagonal") is not None
          and r.get("verdict") == "supported", r)
    st0 = json.loads(api["status"]())
    check("无漂移时不降级", st0.get("demoted_now") == [], st0.get("demoted_now"))
    ob_b = bpy.data.objects.get("Backrest")
    loc_before = tuple(ob_b.location)
    ob_b.location = (loc_before[0], loc_before[1] + 1.2, loc_before[2])   # 远超 10% 对角线
    bpy.context.view_layer.update()
    st1 = json.loads(api["status"]())
    check("几何漂移 → supported 自动降级 unresolved", "joint" in (st1.get("demoted_now") or []), st1.get("demoted_now"))
    d1 = (st1.get("detail") or {}).get("joint") or {}
    check("降级理由留在 verdict.demoted", bool(((d1.get("verdict") or {}).get("demoted") or {}).get("reason")),
          d1.get("verdict"))
    check("降级后 status 变 unresolved", d1.get("status") == "unresolved", d1.get("status"))
    ob_b.location = loc_before
    bpy.context.view_layer.update()

    if hasattr(K, "dsh_view_api"):
        r = json.loads(api["evidence"]("stale_probe", {"from": [-2.6, 0.0, 0.78], "look_at": [0.0, 0.16, 0.66],
                                                        "width": 200, "height": 120, "background": False,
                                                        "overlays": False,
                                                        "path": os.path.join(outdir, "stale_probe.png")},
                                       "指纹绑定探针", ["Backrest"]))
        check("证据绑指纹（fp_objects 记名）", r.get("ok") and r["evidence"].get("fp_objects") == ["Backrest"],
              r.get("error") or r)
        led0 = json.loads(api["ledger"]())
        hit0 = [e for e in led0["evidence"] if e["label"] == "stale_probe"]
        check("未改动时证据不 stale", bool(hit0) and hit0[0].get("stale") is False, hit0[:1])
        ob_b.scale = (ob_b.scale[0], ob_b.scale[1], ob_b.scale[2] * 1.5)   # 改源
        bpy.context.view_layer.update()
        led1 = json.loads(api["ledger"]())
        hit1 = [e for e in led1["evidence"] if e["label"] == "stale_probe"]
        check("源一变 → 证据自动 stale", bool(hit1) and hit1[0].get("stale") is True
              and led1.get("stale_evidence", 0) >= 1 and "stale_probe" in (led1.get("stale_labels") or []),
              {"stale": hit1[0].get("stale") if hit1 else None, "count": led1.get("stale_evidence")})
        ob_b.scale = (ob_b.scale[0], ob_b.scale[1], ob_b.scale[2] / 1.5)
        bpy.context.view_layer.update()
    else:
        print("  skip 证据 staleness（没有 K.dsh_view_api；用 preload=view,contract 可测）")

    # --- 报告
    p = os.path.join(outdir, "contract_report.md")
    r = json.loads(api["report"](p))
    ok_file = os.path.isfile(p)
    txt = open(p, encoding="utf-8").read() if ok_file else ""
    check("报告落盘", r.get("ok") and ok_file, r)
    check("报告含连接与判定", "joint" in txt and "unresolved" in txt.lower() or "supported" in txt.lower(), txt[:200])
    st = json.loads(api["status"]())
    check("status 概览正常", st.get("components") == 3 and st.get("connections") == 1, st)

    print("HEADLESS " + json.dumps({"ok": len(FAILS) == 0, "passed": len(OKS), "failed": len(FAILS),
                                    "failures": FAILS, "outdir": outdir}, ensure_ascii=False))


if __name__ == "__main__":
    main()
