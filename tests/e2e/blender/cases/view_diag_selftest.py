# -*- coding: utf-8 -*-
"""v0.9.1（93-D1/D2）view.py 诊断与 GUI 原语的**无 GUI 单测**。

为什么单独写：D2 的判据（"画面里有没有几何"）容易写成"数非底色像素"——background=true 时
世界底色也算像素，纯覆盖率判不出"瞄空"。这里把三条判据分别钉住：投影入框、覆盖率（众数底色）、
以及空帧告警的内容（必须带 scene_bbox + suggest）。

跑法：
    blender_rt_headless(preload="view", engine="none", factory_startup=True, timeout_ms=120000,
      script="_p=K.win_path('<本文件>');exec(compile(open(_p,encoding='utf-8').read(),'x','exec'),globals())")
"""
import bpy, bmesh, json, math, os, sys

FAILS, OKS = [], []


def check(name, cond, detail=""):
    (OKS if cond else FAILS).append({"name": name, "detail": detail})
    print(("  ok   " if cond else "  FAIL ") + name + ("" if cond else "  <- " + str(detail)))


def main():
    K = sys.modules.get("dsh_rt_kernel")
    if K is None or not hasattr(K, "dsh_view_api"):
        # 没 preload 就就地加载源码（与 contract_selftest 同款兜底）
        here = os.path.dirname(os.path.abspath(__file__))
        src = open(os.path.join(os.path.dirname(here), "runtime", "view.py"), encoding="utf-8").read()
        exec(compile(src, "view.py", "exec"), globals())
        K = sys.modules.get("dsh_rt_kernel")
    api = K.dsh_view_api
    pre = set(bpy.data.objects.keys())

    # 造场景：原点一个 2×2×2 立方体
    me = bpy.data.meshes.new("DSH_VIEW_SELFTEST")
    bm = bmesh.new()
    bmesh.ops.create_cube(bm, size=2.0)
    bm.to_mesh(me)
    bm.free()
    ob = bpy.data.objects.new("DSH_VIEW_SELFTEST", me)
    bpy.context.scene.collection.objects.link(ob)
    bpy.context.view_layer.update()

    try:
        check("VIEW_VERSION = 3（v0.9.4：诊断惰性化）", api.get("version") == 3, api.get("version"))
        for k in ("gui_frame", "gui_shading", "gui_open", "gui_help"):
            check("GUI 原语已注册：%s" % k, callable(api.get(k)))

        diag = api["diagnostics"]           # v0.9.1：诊断原语正式挂在 API 上（私有名不外泄）
        _scene_bbox = diag["scene_bbox"]
        _coverage = diag["coverage"]
        _objects_in_frame = diag["objects_in_frame"]
        _empty_frame_note = diag["empty_frame_note"]
        bb = _scene_bbox()
        check("_scene_bbox 能算（center/span；场景可能还有默认方块，只断言 span ≥ 2）",
              bb and abs(bb["span"] - 2.0) < 1e-6 and bb["objects"] >= 1 and bb["min"] == [-1.0, -1.0, -1.0], bb)

        spec_ok = {"from": [2.5, 2.5, 2.5], "look_at": [0, 0, 0], "width": 160, "height": 90,
                   "lens": 50.0, "sensor": 36.0, "clip_start": 0.1, "clip_end": 1000.0,
                   "sensor_fit": "AUTO", "ortho": False, "ortho_scale": 10.0, "shift_x": 0.0, "shift_y": 0.0}
        names_ok, margin_ok = _objects_in_frame(spec_ok)
        # 近到 50mm 时立方体会略微出框（margin 可以为负 = 被裁掉一点），只要求"数到了"
        check("投影入框：正对着时能数到被测对象", "DSH_VIEW_SELFTEST" in names_ok and margin_ok is not None,
              {"names": names_ok, "margin_px": margin_ok})
        names_far, margin_far = _objects_in_frame(dict(spec_ok, **{"from": [8, 8, 8]}))
        check("投影入框：退远后完全在画内（margin_px > 0）", margin_far is not None and margin_far > 0,
              {"names": names_far, "margin_px": margin_far})

        spec_bad = dict(spec_ok, **{"from": [10, 10, 10], "look_at": [20, 20, 20]})
        names_bad, _ = _objects_in_frame(spec_bad)
        check("投影入框：背离场景时数不到被测对象（这就是「瞄空」的判据）",
              "DSH_VIEW_SELFTEST" not in names_bad, names_bad)
        warn = _empty_frame_note(0.0, bb, spec_bad, names_bad)
        check("空帧告警：code=frame_looks_empty + 带 scene_bbox + 带 suggest",
              warn and warn.get("code") == "frame_looks_empty" and warn.get("scene_bbox")
              and (warn.get("suggest") or {}).get("look_at") == [0.0, 0.0, 0.0], warn)
        check("空帧告警：非空画面时不误报", _empty_frame_note(0.5, bb, spec_ok, names_ok) is None)

        # 覆盖率：整幅同色 → ~0；右下角画一块白 → >0（底色取众数，故与 background=true 无关）
        W, H = 64, 36
        flat = bytearray([40, 40, 40, 255] * (W * H))
        check("_coverage：整幅同色 ≈ 0", _coverage(bytes(flat), W, H, None) < 1e-6)
        patched = bytearray(flat)
        for y in range(0, 8):
            for x in range(0, 8):
                i = (y * W + x) * 4
                patched[i:i + 3] = b"\xff\xff\xff"
        cov = _coverage(bytes(patched), W, H, None)
        check("_coverage：局部白块被算进去（≈64/2304）", 0.01 < cov < 0.06, cov)

        # 建议值可用性：照 suggest 出的视角必须能看到对象
        sug = (warn or {}).get("suggest") or {}
        if sug:
            spec_sug = dict(spec_ok, **{"from": sug["from"], "look_at": sug["look_at"]})
            names_sug, _ = _objects_in_frame(spec_sug)
            check("suggest 的 from/look_at 真能看到对象（照它再出一次即可）",
                  "DSH_VIEW_SELFTEST" in names_sug, names_sug)

        # ── v0.9.4（P0-1）诊断惰性化：不变量 = "跑了诊断 ⟺ 显式要求 or 帧字节数 < 阈值" ──
        # 不去赌某一帧的具体字节数（那会随场景/引擎变），而是断言**判据本身**：
        #   · diagnostics=false ⇒ coverage/scene_bbox/objects_in_frame 一律 None（省掉 45–490 ms）
        #   · diagnostics=true  ⇒ 三项必须真算出来（且与直接调 _coverage 的结果一致）
        thr = diag.get("empty_png_bytes")
        check("阈值已暴露给自检（empty_png_bytes）", isinstance(thr, int) and thr > 0, thr)
        p_lean = os.path.join(os.path.dirname(api["capture"] and __file__ or __file__), "dsh_view_diag_lean.png")
        import tempfile as _tf
        p_lean = os.path.join(_tf.gettempdir(), "dsh_view_diag_lean.png")
        r_lean = json.loads(api["capture"](json.dumps({"from": [2.5, 2.5, 2.5], "look_at": [0, 0, 0],
                                                       "width": 160, "height": 90, "path": p_lean})))
        want_diag = bool(r_lean.get("bytes", 0) < thr)
        check("默认（未显式要求）：diagnostics 标志 == (字节数 < 阈值)",
              bool(r_lean.get("diagnostics")) == want_diag, {"bytes": r_lean.get("bytes"), "thr": thr, "flag": r_lean.get("diagnostics")})
        if not want_diag:
            check("默认路径：三项诊断全部为 None（这就是省下来的成本）",
                  r_lean.get("coverage_estimate") is None and r_lean.get("scene_bbox") is None
                  and r_lean.get("objects_in_frame") is None and r_lean.get("warning") is None, r_lean.get("coverage_estimate"))
        r_forced = json.loads(api["capture"](json.dumps({"from": [2.5, 2.5, 2.5], "look_at": [0, 0, 0],
                                                         "width": 160, "height": 90, "path": p_lean,
                                                         "diagnostics": True})))
        check("显式 diagnostics=true：三项必须真算出来（语义与 v0.9.1 一致）",
              r_forced.get("diagnostics") is True and isinstance(r_forced.get("coverage_estimate"), float)
              and isinstance(r_forced.get("scene_bbox"), dict) and r_forced.get("objects_in_frame") is not None,
              {"diag": r_forced.get("diagnostics"), "cov": r_forced.get("coverage_estimate")})
        r_far = json.loads(api["capture"](json.dumps({"from": [500, 500, 500], "look_at": [900, 900, 900],
                                                      "width": 160, "height": 90, "path": p_lean,
                                                      "diagnostics": True})))
        check("强制诊断 + 瞄空 ⇒ frame_looks_empty 告警照旧（自动补跑那条路也一样）",
              (r_far.get("warning") or {}).get("code") == "frame_looks_empty"
              and bool((r_far.get("warning") or {}).get("suggest")), r_far.get("warning"))
        h = json.loads(api["help"]())
        check("help 里写明诊断的触发条件（默认不跑/近空补跑/可强制）",
              "diagnostics_v3" in h and "默认不跑" in json.dumps(h.get("diagnostics_v3") or {}, ensure_ascii=False),
              list(h.keys())[:8])

        # 视口着色：headless 没有真 UI → 必须**明确报错**，不许假装成功
        # 实测：headless(factory-startup) 里 Blender 仍有一个最小 window/screen，gui_shading 居然能生效；
        # 所以判据不是"必须失败"，而是"要么真改了、要么明确报错"——不许既 ok 又什么都没发生。
        r = json.loads(api["gui_shading"]("WIREFRAME"))
        well_formed = (r.get("ok") is True and r.get("shading") == "WIREFRAME") or (r.get("ok") is False and bool(r.get("error")))
        check("gui_shading 行为明确（要么生效要么报错，不许静默）", well_formed, r)
        if r.get("ok") is True:
            api["gui_shading"]("SOLID")      # 还原（本自检进程内）

    finally:
        try:
            bpy.data.objects.remove(ob, do_unlink=True)
            bpy.data.meshes.remove(me, do_unlink=True)
        except Exception:
            pass

    left = set(bpy.data.objects.keys()) - pre
    check("自检不留下对象", left == set(), sorted(left))
    print("HEADLESS " + json.dumps({"ok": len(FAILS) == 0, "passed": len(OKS), "failed": len(FAILS),
                                    "failures": FAILS}, ensure_ascii=False))


if __name__ == "__main__":
    main()
