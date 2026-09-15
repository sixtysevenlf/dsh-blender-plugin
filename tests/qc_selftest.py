# -*- coding: utf-8 -*-
"""qc 自检（需要插件后端在跑：GUI Blender + addon + 127.0.0.1:9877）。

用法：python3 tests/qc_selftest.py [--ref 参考图 --box x0,y0,x1,y1 --render 渲染图]
断言：自比 IoU=1.0、平移 12px 仍=1.0、放大 1.15>0.97、扰动下搜索对齐显著优于直接比、
      真实图跑通并给出 iou/iou_fixed/对照图（且无"搜索刷分"告警）。
"""
import json, os, sys, urllib.request

API = os.environ.get("DSH_BLENDER_API", "http://127.0.0.1:9877")
FAILS = []


def call(op, args=None):
    body = json.dumps({"op": op, "args": args or {}, "force": True}).encode()
    req = urllib.request.Request(API + "/plan", data=body, headers={"content-type": "application/json"})
    with urllib.request.urlopen(req, timeout=300) as r:
        d = json.loads(r.read().decode())
    res = d.get("result") or {}
    if isinstance(res, str):
        res = json.loads(res)
    if not res:
        raise RuntimeError("空结果: " + json.dumps(d, ensure_ascii=False)[:300])
    return res


def check(name, cond, detail=""):
    print(("  [OK]   " if cond else "  [FAIL] ") + name + ("  " + detail if detail else ""))
    if not cond:
        FAILS.append(name)


def main():
    try:
        urllib.request.urlopen(API + "/health", timeout=5).read()
    except Exception as e:
        print("后端不可用（%s）—— 先启动 Blender 与插件后端再跑本测试。" % e)
        return 2
    print("== 合成自检 ==")
    sc = call("qc_self_check")
    check("自比 IoU = 1.0", float(sc.get("iou_self") or 0) == 1.0, str(sc.get("iou_self")))
    check("平移 12px 仍 = 1.0", float(sc.get("iou_shift12px") or 0) == 1.0, str(sc.get("iou_shift12px")))
    check("放大 1.15 > 0.97", float(sc.get("iou_scale115") or 0) > 0.97, str(sc.get("iou_scale115")))
    m = sc.get("self_metrics") or {}
    check("自比 dice = 1.0", float(m.get("dice") or 0) == 1.0)
    check("自比 missing_px = 0", int(m.get("missing_px") if m.get("missing_px") is not None else -1) == 0)
    argv = sys.argv[1:]
    ref = argv[argv.index("--ref") + 1] if "--ref" in argv else None
    box = [int(x) for x in argv[argv.index("--box") + 1].split(",")] if "--box" in argv else None
    render = argv[argv.index("--render") + 1] if "--render" in argv else None
    if ref and render:
        print("== 真图：鲁棒性 + 对齐 + 指标 ==")
        rb = call("qc_robustness_check", {"ref_path": ref, "ref_box": box, "render_path": render})
        base = float(rb.get("base_iou") or 0)
        d0 = float(rb.get("perturbed_direct_iou") or 0)
        d1 = float(rb.get("perturbed_searched_iou") or 0)
        check("基线 IoU > 0.6", base > 0.6, "%.4f" % base)
        check("扰动后搜索对齐优于直接比（>= +0.15）", (d1 - d0) >= 0.15, "direct=%.4f searched=%.4f" % (d0, d1))
        r = call("qc_compare", {"ref_path": ref, "ref_box": box, "render_path": render, "label": "selftest"})
        mm = r.get("metrics") or {}
        check("compare 同时给 iou 与 iou_fixed", mm.get("iou") is not None and mm.get("iou_fixed") is not None)
        check("无『搜索对齐刷分』告警", not any("搜索对齐比固定对齐高" in w for w in (mm.get("warnings") or [])), str(mm.get("warnings")))
        check("iou == iou_fixed（默认固定对齐）", mm.get("iou") == mm.get("iou_fixed"), "%s vs %s" % (mm.get("iou"), mm.get("iou_fixed")))
        check("产出叠加图与三联对照图", bool(r.get("overlay")) and bool(r.get("sheet")))
    print("")
    if FAILS:
        print("FAILED: " + ", ".join(FAILS))
        return 1
    print("QC selftest: ALL PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())