# -*- coding: utf-8 -*-
"""txn（事务/回滚 + 待定编辑协议 #12）自检 —— **纯无头可跑，自造合成场景，绝不碰 GUI 场景**。

跑法（推荐，父任务给的命令）：
    blender_rt_headless(preload="txn", engine="none", outdir="D:\\\\DSH\\\\blender\\\\tmp\\\\txn_v090",
        script="_p=K.win_path('/home/sixtyseven67/DSH/dsh-blender-plugin/tests/txn_selftest.py');"
               "exec(compile(open(_p,encoding='utf-8').read(),'x','exec'),globals())")

为什么不再走老版本的 HTTP(/act, 127.0.0.1:9877) 路径：那条路打的是**用户的 GUI 场景**（会在里面建/删
DSH_TXN_SELFTEST 立方体），而且它的 prune(keep=1) 会把用户 D:\\DSH\\blender\\tmp\\snapshots 里已有的
快照（pre_exo_build / pre_p2_baseline / pre_p3_parts）删掉。本文件改为**进程内直调 K.dsh_txn_api**
+ 自造合成对象（对象名/集合/材质全带 DSH_TS_ 前缀，结束自清），断言与老版本逐条对应（§老断言）。

覆盖：
  §老断言  mark→移动→revert 回原位；snapshot→list→prune→drop；拓扑改动过 revert 会被跳过并报告
  §新断言①  有 pending 时再 edit_begin 被拒
  §新断言②  没有 check 就 edit_accept 被拒
  §新断言③  edit_accept("") 被拒
  §新断言④  begin→check→accept 成功且对象状态保持
  §新断言⑤  revert 真正回滚（transform/材质/可见性逐项对拍）
  §新断言⑥  连续两次同 label 的 revert 被拒（防抖）
  §交叉⑦⑧⑨ deliver_export 出 OBJ+MTL+manifest → verify 逐项 PASS / 改一个字节 → FAIL / 空 scope → ok:false
"""
import bpy
import hashlib
import json
import os
import sys
import time

FAILS, OKS, SKIPS = [], [], []


def check(name, cond, detail=""):
    (OKS if cond else FAILS).append({"name": name, "detail": detail})
    print(("  ok   " if cond else "  FAIL ") + name + ("" if cond else "  <- " + str(detail)))


def skip(name, why=""):
    SKIPS.append(name)
    print("  skip " + name + ("  <- " + why if why else ""))


def J(s):
    return json.loads(s)


def _api(attr, modname):
    """拿模块 API：优先 K.<attr>（preload 已注入），其次从 K.runtime_dir 现场加载同目录 <modname>.py。

    与 qc.py 的 _load_qc_render / contract_selftest 的兜底同一套写法（headless 只 preload 了一个模块时也够用）。
    """
    K = sys.modules.get("dsh_rt_kernel")
    api = getattr(K, attr, None) if K is not None else None
    if api:
        return api, K
    d = getattr(K, "runtime_dir", None) if K is not None else None
    if d and os.path.isfile(os.path.join(str(d), modname + ".py")):
        p = os.path.join(str(d), modname + ".py")
        with open(p, encoding="utf-8") as fh:
            src = fh.read()
        exec(compile(src, p, "exec"), {"__name__": "dsh_" + modname, "__file__": p})
        return getattr(K, attr, None), K
    return None, K


# ---------------------------------------------------------------- 合成场景

COLL = "DSH_TS_COLL"
OBJ = "DSH_TS_BOX"
OBJ2 = "DSH_TS_BOX_B"
MAT = "DSH_TS_MAT"
MARK, SNAP = "dsh_selftest_mark", "dsh_selftest_snap"


def _mk_box(name, size=(1.0, 1.0, 1.0), loc=(0.0, 0.0, 0.0), coll=None):
    """自造一个长方体（from_pydata，不依赖任何 bpy.ops 的上下文）。"""
    hx, hy, hz = size[0] / 2.0, size[1] / 2.0, size[2] / 2.0
    vs = [(-hx, -hy, -hz), (hx, -hy, -hz), (hx, hy, -hz), (-hx, hy, -hz),
          (-hx, -hy, hz), (hx, -hy, hz), (hx, hy, hz), (-hx, hy, hz)]
    fs = [(0, 3, 2, 1), (4, 5, 6, 7), (0, 1, 5, 4), (1, 2, 6, 5), (2, 3, 7, 6), (3, 0, 4, 7)]
    me = bpy.data.meshes.new(name + "_mesh")
    me.from_pydata(vs, [], fs)
    me.update()
    ob = bpy.data.objects.new(name, me)
    ob.location = loc
    (coll or bpy.context.scene.collection).objects.link(ob)
    return ob


def _mk_mat(name, color=(0.9, 0.2, 0.1, 1.0)):
    mat = bpy.data.materials.new(name)
    try:
        if getattr(mat, "node_tree", None) is None:
            mat.use_nodes = True
    except Exception:
        pass
    pn = None
    try:
        for n in mat.node_tree.nodes:
            if n.type == "BSDF_PRINCIPLED":
                pn = n
                break
    except Exception:
        pn = None
    if pn is not None:
        try:
            pn.inputs["Base Color"].default_value = color
        except Exception:
            pass
    else:
        try:
            mat.diffuse_color = color
        except Exception:
            pass
    return mat


def build_scene():
    coll = bpy.data.collections.new(COLL)
    bpy.context.scene.collection.children.link(coll)
    a = _mk_box(OBJ, (1.0, 1.0, 1.0), (0.0, 0.0, 0.0), coll)
    b = _mk_box(OBJ2, (0.4, 0.4, 2.0), (2.0, 0.0, 0.5), coll)
    a.data.materials.append(_mk_mat(MAT))
    bpy.context.view_layer.update()
    return coll, a, b


def cleanup(coll, objs):
    for ob in objs:
        try:
            bpy.data.objects.remove(ob, do_unlink=True)
        except Exception:
            pass
    try:
        bpy.data.collections.remove(coll)
    except Exception:
        pass
    for m in list(bpy.data.meshes):
        if m.name.startswith("DSH_TS_"):
            try:
                bpy.data.meshes.remove(m)
            except Exception:
                pass
    for m in list(bpy.data.materials):
        if m.name.startswith("DSH_TS_"):
            try:
                bpy.data.materials.remove(m)
            except Exception:
                pass
    bpy.context.view_layer.update()


def snap_of(ob):
    """对象状态指纹（对拍用）：transform + 可见性 + 材质槽 + 顶点数。"""
    return {"loc": [round(v, 6) for v in ob.location], "rot": [round(v, 6) for v in ob.rotation_euler],
            "scale": [round(v, 6) for v in ob.scale], "hide_viewport": bool(ob.hide_viewport),
            "hide_render": bool(ob.hide_render), "verts": len(ob.data.vertices) if ob.type == "MESH" else None,
            "mats": [m.name if m else None for m in ob.data.materials]}


def main():
    outdir = None
    if "--" in sys.argv:
        rest = sys.argv[sys.argv.index("--") + 1:]
        outdir = rest[-1] if rest else None
    outdir = outdir or os.path.join(os.path.expanduser("~"), "dsh_txn_selftest")
    os.makedirs(outdir, exist_ok=True)

    api, K = _api("dsh_txn_api", "txn")
    if api is None:
        print("HEADLESS " + json.dumps({"ok": False, "passed": 0, "failed": 1, "skipped": 0,
                                        "failures": [{"name": "load_txn_api", "detail": "K.dsh_txn_api 缺失且 runtime_dir 下没有 txn.py"}],
                                        "hint": "用 preload='txn' 跑"}, ensure_ascii=False))
        return
    print("TXN_SELFTEST version=%s outdir=%s blender=%s" % (api.get("version"), outdir, bpy.app.version_string))
    check("TXN_VERSION 已 +1（=3）", int(api.get("version") or 0) == 3, api.get("version"))
    old_ops = ["snapshot", "restore", "list", "prune", "mark", "revert", "marks", "drop", "help"]
    new_ops = ["edit_begin", "edit_check", "edit_accept", "edit_revert", "edit_status"]
    check("老 API 一个都没丢", all(o in api for o in old_ops), [o for o in old_ops if o not in api])
    check("新 API 都挂上了", all(o in api for o in new_ops), [o for o in new_ops if o not in api])
    h = J(api["help"]())
    check("t_help 里补了新 op", h.get("version") == 3 and "pending_edit" in h
          and all(o in h["pending_edit"] for o in new_ops), sorted(h))
    check("t_help 写明拓扑边界", "t_snapshot" in str(h.get("boundary")), h.get("boundary"))

    coll, a, b = build_scene()
    # ★ 隔离：headless 里 K.out_dir 恒为插件默认工作目录（D:\DSH\blender\tmp），**不是** outdir 参数 ——
    # 不改的话 t_snapshot 会写进共享的 tmp\snapshots。这里把它指到本测试自己的目录，跑完还原。
    prev_out = getattr(K, "out_dir", None) if K is not None else None
    if K is not None:
        K.out_dir = outdir
    try:
        # ============================================================ §老断言（逐条对应旧 txn_selftest.py）
        print("== §老断言：对象级 mark / revert（含拓扑边界） ==")
        m = J(api["mark"](MARK, [OBJ]))
        check("老①：mark 记到 1 个对象", int(m.get("objects") or 0) == 1, m.get("objects"))
        a.location = (5.0, 0.0, 0.0)
        bpy.context.view_layer.update()
        check("老②：移动生效（应在 x=5）", abs(a.location.x - 5.0) < 1e-9, tuple(a.location))
        rv = J(api["revert"](MARK))
        check("老③：revert 报告有对象被回滚", int(rv.get("changed_count") or 0) >= 1, rv.get("changed_count"))
        check("老④：revert 未跳过对象（拓扑没变）", not (rv.get("blocked") or []), rv.get("blocked"))
        check("老⑤：位置已回到原点（x=0）", abs(a.location.x) < 1e-9, tuple(a.location))

        # 老文件的 docstring 声称覆盖"拓扑改动被跳过"，实际没测 —— 这里补上（边界必须真咬人）
        m2 = J(api["mark"]("dsh_selftest_topo", [OBJ2]))
        me = bpy.data.objects[OBJ2].data
        try:
            me.clear_geometry()          # from_pydata 不能直接替换已有几何（会 Array length mismatch）
        except Exception:
            me = bpy.data.meshes.new("DSH_TS_TOPO")
            bpy.data.objects[OBJ2].data = me
        me.from_pydata([(0, 0, 0), (1, 0, 0), (0, 1, 0)], [], [(0, 1, 2)])
        me.update()
        bpy.context.view_layer.update()
        check("老⑥pre：拓扑确实变了（8 顶点 → 3 顶点）", len(bpy.data.objects[OBJ2].data.vertices) == 3,
              len(bpy.data.objects[OBJ2].data.vertices))
        rv2 = J(api["revert"]("dsh_selftest_topo"))
        check("老⑥：拓扑变过的对象被跳过并报告",
              bool(rv2.get("blocked")) and rv2["blocked"][0]["name"] == OBJ2,
              rv2.get("blocked"))
        check("老⑥b：被跳过的对象确实没被回写", abs(bpy.data.objects[OBJ2].location.x - 2.0) < 1e-9,
              tuple(bpy.data.objects[OBJ2].location))
        J(api["drop"]("dsh_selftest_topo"))

        print("== §老断言：文件级 snapshot / list / prune / drop ==")
        sn = J(api["snapshot"](SNAP, "selftest"))
        info = sn.get("snapshot") or {}
        check("老⑦：快照已落盘（bytes > 0）",
              int(info.get("bytes") or 0) > 0, str(info.get("bytes")) + "B / " + str(info.get("ms")) + "ms")
        check("老⑦b：快照落在本测试的隔离 out_dir/snapshots 里（不污染共享目录）",
              str(info.get("path") or "").startswith(outdir), info.get("path"))
        ls = J(api["list"]())
        labels = [s.get("label") for s in (ls.get("snapshots") or [])]
        check("老⑧：list 能看到快照", SNAP in labels, labels)
        pr = J(api["prune"](1))
        check("老⑨：prune 正常返回", pr.get("ok") is not False, pr.get("removed"))
        J(api["drop"](MARK))
        marks_after = [m.get("label") for m in (J(api["marks"]()).get("marks") or [])]
        check("老⑩：drop 之后 mark 清单里没有它了", MARK not in marks_after, marks_after)

        # ============================================================ §新断言 ①–⑥（#12 待定编辑协议）
        print("== §新断言：#12 待定编辑协议 ==")
        # ① 有 pending 时再 edit_begin 被拒
        e1 = J(api["edit_begin"]("DSH_TS_E1", "edit", [OBJ], "第一次待定"))
        check("新①a：edit_begin 挂上 pending", e1.get("ok") is True and e1.get("pending") is True, e1.get("error"))
        e2 = J(api["edit_begin"]("DSH_TS_E2", "edit", [OBJ], "第二条"))
        check("新①b：已有 pending 时再 edit_begin 被拒（ok:false）", e2.get("ok") is False and "pending" in str(e2.get("error")), e2.get("error"))
        check("新①c：拒绝信息里带当前 pending 的 label", (e2.get("pending") or {}).get("label") == "DSH_TS_E1", e2.get("pending"))

        # ② 没有 check 就 edit_accept 被拒
        a.location = (1.5, 0.0, 0.0)
        bpy.context.view_layer.update()
        acc0 = J(api["edit_accept"]("我看过了"))
        check("新②：没有 check 就 accept 被拒", acc0.get("ok") is False and "复核" in str(acc0.get("error")), acc0.get("error"))

        # ③ edit_accept("") 被拒
        c1 = J(api["edit_check"]("measure", {"dx": 1.5}, "量了位移"))
        check("新③a：edit_check 记账成功", c1.get("ok") is True and c1.get("checks") == 1, c1)
        acc_empty = J(api["edit_accept"](""))
        check("新③b：edit_accept('') 被拒", acc_empty.get("ok") is False and "verified 必填" in str(acc_empty.get("error")), acc_empty.get("error"))
        acc_ws = J(api["edit_accept"]("   "))
        check("新③c：edit_accept('   ') 也被拒（不是只挡空串）", acc_ws.get("ok") is False, acc_ws.get("error"))

        # ④ 正常流程 begin→check→accept 成功且对象状态保持
        before4 = snap_of(a)
        acc = J(api["edit_accept"]("measure dx=1.5 与我算的一致，方向对，无副作用"))
        check("新④a：accept 成功并返回 accepted 计数", acc.get("ok") is True and int(acc.get("accepted") or 0) >= 1, acc.get("error"))
        check("新④b：accept 后 pending 清空", J(api["edit_status"]()).get("has_pending") is False, J(api["edit_status"]()).get("pending"))
        check("新④c：对象状态保持（accept 不动场景）", snap_of(a) == before4, {"before": before4, "after": snap_of(a)})
        check("新④d：编辑入历史", any(e.get("label") == "DSH_TS_E1" and e.get("outcome") == "accepted"
                                     for e in (J(api["edit_status"]()).get("history") or [])),
              J(api["edit_status"]()).get("history"))

        # ⑤ revert 真正回滚（transform/material/可见性逐项对拍）
        mat_before = [m.name if m else None for m in a.data.materials]
        e5 = J(api["edit_begin"]("DSH_TS_E5", "edit", [OBJ], "准备撤销"))
        check("新⑤a：新 pending 挂上", e5.get("ok") is True, e5.get("error"))
        a.location = (-3.0, 4.0, 1.25)
        a.rotation_euler = (0.3, 0.0, 0.7)
        a.scale = (2.0, 2.0, 0.5)
        a.hide_viewport = True
        a.hide_render = True
        a.data.materials.clear()          # 改材质槽
        bpy.context.view_layer.update()
        rv5 = J(api["edit_revert"]("量错了：位移方向反了，先撤回来重算"))
        check("新⑤b：edit_revert 成功", rv5.get("ok") is True, rv5.get("error"))
        check("新⑤c：transform 四项全部回到 begin 时",
              [round(v, 6) for v in a.location] == [1.5, 0.0, 0.0] and [round(v, 6) for v in a.rotation_euler] == [0.0, 0.0, 0.0]
              and [round(v, 6) for v in a.scale] == [1.0, 1.0, 1.0],
              {"loc": tuple(a.location), "rot": tuple(a.rotation_euler), "scale": tuple(a.scale)})
        check("新⑤d：可见性回到 begin 时（hide_viewport/hide_render 都还原）",
              a.hide_viewport is False and a.hide_render is False,
              {"hv": a.hide_viewport, "hr": a.hide_render})
        check("新⑤e：材质槽回到 begin 时", [m.name if m else None for m in a.data.materials] == mat_before,
              {"before": mat_before, "after": [m.name if m else None for m in a.data.materials]})
        check("新⑤f：revert 报告 changed 且无 blocked", int(rv5.get("changed_count") or 0) >= 1 and not (rv5.get("blocked") or []),
              {"changed": rv5.get("changed"), "blocked": rv5.get("blocked")})
        check("新⑤g：撤销入历史且 why 被记下", any(e.get("label") == "DSH_TS_E5" and e.get("outcome") == "reverted"
                                                for e in (J(api["edit_status"]()).get("history") or [])),
              J(api["edit_status"]()).get("history"))

        # ⑥ 连续两次同 label 的 revert 被拒（防抖）
        J(api["edit_begin"]("DSH_TS_E6", "edit", [OBJ], "同类编辑再来一次"))
        a.location = (9.0, 0.0, 0.0)
        bpy.context.view_layer.update()
        rv6a = J(api["edit_revert"]("第一次撤"))
        check("新⑥a：第一次 revert 成功", rv6a.get("ok") is True, rv6a.get("error"))
        J(api["edit_begin"]("DSH_TS_E6", "edit", [OBJ], "同 label 再改一次"))
        rv6b = J(api["edit_revert"]("同 label 再撤一次"))
        check("新⑥b：同 label 连续 revert 被拒（防抖）",
              rv6b.get("ok") is False and rv6b.get("code") == "dither_guard", rv6b.get("error"))
        check("新⑥c：防抖拒绝里给了'先算量'的指引", "来回抖" in str(rv6b.get("error")) or "量算对" in str(rv6b.get("error")),
              rv6b.get("hint"))
        check("新⑥d：被拒后 pending 仍在（可以继续 accept 或换 label）", J(api["edit_status"]()).get("has_pending") is True,
              J(api["edit_status"]()).get("pending"))
        # 换个处置方式 → 允许继续（证明防抖不是把整条路堵死）
        J(api["edit_check"]("measure", {"x": 9.0}, "防抖后先量再定"))
        J(api["edit_accept"]("防抖场景下接受这条：x=9 是我要的位置"))
        st6 = J(api["edit_status"]())
        check("新⑥e：edit_status 三项计数齐（accepted/reverted/reverts_left）",
              int(st6.get("accepted") or 0) >= 2 and int(st6.get("reverted") or 0) >= 2 and st6.get("reverts_left") is not None,
              {"accepted": st6.get("accepted"), "reverted": st6.get("reverted"), "left": st6.get("reverts_left"),
               "cap": st6.get("revert_cap")})
        check("新⑥f：edit_status 写明额度口径与拓扑提醒",
              "max(" in str(st6.get("budget_rule")) and "t_snapshot" in str(st6.get("topology_warning")),
              {"budget_rule": st6.get("budget_rule"), "topology_warning": st6.get("topology_warning")})
        check("新⑥g：edit_status 带 pending 摘要字段（无 pending 时为 null）",
              st6.get("pending") is None and st6.get("has_pending") is False, st6.get("pending"))
        a.location = (0.0, 0.0, 0.0)
        bpy.context.view_layer.update()

        # ============================================================ §交叉 ⑦⑧⑨（交付导出 + 读回校验）
        print("== §交叉断言：deliver_export / deliver_verify（#13） ==")
        dapi, _ = _api("dsh_deliver_api", "deliver")
        if dapi is None:
            skip("交叉⑦⑧⑨ 交付导出校验", "K.dsh_deliver_api 缺失且 runtime_dir 下没有 deliver.py（用 preload='txn,deliver'）")
        else:
            dd = os.path.join(outdir, "deliver_txn_selftest")
            ex = J(dapi["export"](dd, [OBJ, OBJ2], None, "xmodel", "obj", True, 1.0, True, True, True, False))
            files = ex.get("files") or {}
            check("交叉⑦a：export ok 且 OBJ+MTL+manifest 三件齐",
                  ex.get("ok") is True and "obj" in files and "mtl" in files and ex.get("manifest"),
                  ex.get("error") or sorted(files) + [str(ex.get("manifest"))])
            check("交叉⑦b：文件都真落盘且 bytes>0",
                  all(int((files.get(k) or {}).get("bytes") or 0) > 0 for k in ("obj", "mtl"))
                  and int((ex.get("manifest") or {}).get("bytes") or 0) > 0,
                  {k: (files.get(k) or {}).get("bytes") for k in ("obj", "mtl")})
            check("交叉⑦c：归一化记录可反推（scale>0 + center + 原 bbox）",
                  (ex.get("normalize") or {}).get("applied") is True
                  and float((ex.get("normalize") or {}).get("scale") or 0) > 0
                  and (ex.get("normalize") or {}).get("bbox_before_min") is not None,
                  ex.get("normalize"))
            vf = J(dapi["verify"](dd, "auto"))
            check("交叉⑦d：verify 逐项 PASS（ok:true 且 failed:0）",
                  vf.get("ok") is True and int(vf.get("failed") or 0) == 0,
                  {"passed": vf.get("passed"), "failed": vf.get("failed"), "failed_checks": vf.get("failed_checks")})
            check("交叉⑦e：verify 真的逐项给了数字（checks 数 >= 8）", len(vf.get("checks") or []) >= 8,
                  len(vf.get("checks") or []))
            # ⑧ 故意改一个字节 → 必须 FAIL
            op = (files.get("obj") or {}).get("path")
            raw = open(op, "rb").read()
            i = raw.find(b"\nv ")
            j = i + 3
            while j < len(raw) and not (48 <= raw[j] <= 57):
                j += 1                                   # 定位到 v 行里的第一个数字
            mut = bytearray(raw)
            mut[j] = 48 if mut[j] == 57 else 57          # 0→9 / 9→0
            open(op, "wb").write(bytes(mut))
            check("交叉⑧a：确实改了字节（md5 变了）",
                  hashlib.md5(bytes(mut)).hexdigest() != (files.get("obj") or {}).get("md5"),
                  {"before": (files.get("obj") or {}).get("md5"), "after": hashlib.md5(bytes(mut)).hexdigest()})
            vf2 = J(dapi["verify"](dd, "auto"))
            check("交叉⑧b：改一个字节后 verify 必须 FAIL（ok:false）", vf2.get("ok") is False,
                  {"passed": vf2.get("passed"), "failed": vf2.get("failed")})
            check("交叉⑧c：FAIL 项点名 md5 对不上", "md5_match" in str(vf2.get("failed_checks")),
                  vf2.get("failed_checks"))
            # ⑨ 空 scope → ok:false
            bad = J(dapi["export"](dd, None, "DSH_TS_NO_SUCH_COLL", "empty", "obj"))
            check("交叉⑨a：空 scope（集合不存在）→ ok:false", bad.get("ok") is False and "集合不存在" in str(bad.get("error")), bad.get("error"))
            bpy.context.scene.collection.children.link(bpy.data.collections.new("DSH_TS_EMPTY_COLL"))
            bad2 = J(dapi["export"](dd, None, "DSH_TS_EMPTY_COLL", "empty2", "obj"))
            check("交叉⑨b：空集合（0 个 mesh）→ ok:false", bad2.get("ok") is False and "0 个" in str(bad2.get("error")), bad2.get("error"))
            bad3 = J(dapi["export"](dd, ["DSH_TS_NO_SUCH_OBJECT"], None, "empty3", "obj"))
            check("交叉⑨c：objects 全不存在 → ok:false", bad3.get("ok") is False, bad3.get("error"))
            bpy.data.collections.remove(bpy.data.collections["DSH_TS_EMPTY_COLL"])
    finally:
        if K is not None:
            try:
                K.out_dir = prev_out
            except Exception:
                pass
        cleanup(coll, [a, b])
        check("清理：合成对象/集合已删干净",
              bpy.data.objects.get(OBJ) is None and bpy.data.objects.get(OBJ2) is None
              and bpy.data.collections.get(COLL) is None, "leftover")

    print("")
    if FAILS:
        print("FAILED: " + ", ".join(f["name"] for f in FAILS))
    print("HEADLESS " + json.dumps({"ok": len(FAILS) == 0, "passed": len(OKS), "failed": len(FAILS),
                                    "skipped": len(SKIPS), "failures": FAILS, "skips": SKIPS, "outdir": outdir},
                                   ensure_ascii=False))


if __name__ == "__main__":
    main()
