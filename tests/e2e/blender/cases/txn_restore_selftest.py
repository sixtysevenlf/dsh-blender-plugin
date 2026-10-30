# -*- coding: utf-8 -*-
"""txn `restore` 的 filepath 语义自检 —— 回归外部反馈（文件级 restore 会把会话搬进快照目录）。

问题（实测现象）：
    restore 用 open_mainfile(<out_dir>/snapshots/<label>.blend) 把**整个会话**搬到快照文件上，
    bpy.data.filepath 随之变成快照路径。原文件其实一直躺在原地没动 ——
    但之后用户/助手**一保存就写进快照目录**，语义意外（"我明明在改自己的工程"）。

判据（本文件逐条断言）：
  ① 默认 restore 之后，会话必须回到**原来的用户文件**上（后续保存落回原文件）
  ② 会话已经不在用户文件上（已在快照目录里）时，按快照记录里的 filepath 兜底 —— 仍要回到原文件
  ③ 兜不到目标（未保存场景）时**不许乱写**：保持快照路径 + 明确告警，不许静默
  ④ restore 之前自动留一份 pre_restore 备份（restore 会丢当前未保存状态，这条得能撤销）
  ⑤ 非破坏模式 into="snapshot"：一个字节都不许写原文件
  ⑥ 快照记录带 filepath（跨开文件后仍知道"原文件在哪"）

跑法（headless）：
    blender_rt_headless(preload="txn", engine="none", factory_startup=True,
        outdir="<tmp>",
        script="_p=K.win_path('<包路径>/tests/txn_restore_selftest.py');"
               "exec(compile(open(_p,encoding='utf-8').read(),'x','exec'),globals())")
"""
import bpy
import hashlib
import json
import os
import sys

FAILS, OKS, SKIPS = [], [], []
MARK = "DSH_TRS_MARK"


def check(name, cond, detail=""):
    (OKS if cond else FAILS).append({"name": name, "detail": detail})
    print(("  ok   " if cond else "  FAIL ") + name + ("" if cond else "  <- " + str(detail)))


def J(s):
    if isinstance(s, dict):
        return s
    try:
        return json.loads(s)
    except Exception:
        return {"_raw": str(s)}


def _api(attr, modname):
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


def call(fn, *a, **kw):
    """调用并吞掉异常（老版本没有 into=/backup= 这类参数时要给出可读的失败而不是崩）。"""
    try:
        return J(fn(*a, **kw)), None
    except Exception as e:
        return None, "%s: %s" % (type(e).__name__, e)


def md5_of(path):
    with open(path, "rb") as fh:
        return hashlib.md5(fh.read()).hexdigest()


def mk_mark(loc=(0.0, 0.0, 0.0)):
    ob = bpy.data.objects.get(MARK)
    if ob is not None:
        return ob
    me = bpy.data.meshes.new(MARK + "_mesh")
    me.from_pydata([(0, 0, 0), (1, 0, 0), (0, 1, 0)], [], [(0, 1, 2)])
    me.update()
    ob = bpy.data.objects.new(MARK, me)
    ob.location = loc
    bpy.context.scene.collection.objects.link(ob)
    bpy.context.view_layer.update()
    return ob


def main():
    outdir = None
    if "--" in sys.argv:
        rest = sys.argv[sys.argv.index("--") + 1:]
        outdir = rest[-1] if rest else None
    outdir = outdir or os.path.join(os.path.expanduser("~"), "dsh_txn_restore_selftest")
    os.makedirs(outdir, exist_ok=True)

    api, K = _api("dsh_txn_api", "txn")
    if api is None:
        print("HEADLESS " + json.dumps({"ok": False, "passed": 0, "failed": 1, "skipped": 0,
              "failures": [{"name": "load_txn_api", "detail": "K.dsh_txn_api 缺失"}],
              "hint": "用 preload='txn' 跑"}, ensure_ascii=False))
        return

    print("TXN_RESTORE_SELFTEST version=%s outdir=%s blender=%s"
          % (api.get("version"), outdir, bpy.app.version_string))
    prev_out = getattr(K, "out_dir", None) if K is not None else None
    if K is not None:
        K.out_dir = outdir
    orig = os.path.join(outdir, "user_scene.blend")
    snap_dir = os.path.join(outdir, "snapshots")
    try:
        # ---------------------------------------------------------------- 前置：造一份"用户的工程"
        mk_mark((0.0, 0.0, 0.0))
        try:
            bpy.ops.wm.save_as_mainfile(filepath=orig)
        except Exception as e:
            check("前置：能把场景存成用户工程文件", False, "%s: %s" % (type(e).__name__, e))
            raise
        check("前置①：会话已绑定到用户工程文件", os.path.normpath(bpy.data.filepath) == os.path.normpath(orig),
              bpy.data.filepath)

        s1, err = call(api["snapshot"], "trs_s1", "restore 语义自检")
        check("前置②：snapshot 成功", s1 is not None and s1.get("ok") is True, err or s1)
        rec = (s1 or {}).get("snapshot") or {}
        check("⑥ 快照记录带 filepath（取快照时的原文件）",
              os.path.normpath(str(rec.get("filepath") or "")) == os.path.normpath(orig), rec.get("filepath"))

        # 改一下（删掉标记对象）—— restore 之后必须回来
        ob = bpy.data.objects.get(MARK)
        bpy.data.objects.remove(ob, do_unlink=True)
        bpy.context.view_layer.update()
        check("前置③：改动已生效（标记对象已删）", bpy.data.objects.get(MARK) is None, "still there")

        # ---------------------------------------------------------------- ① 默认 restore 回到原文件
        print("== ① 默认 restore：会话必须回到用户原文件 ==")
        r1, err = call(api["restore"], "trs_s1")
        check("①a：restore 成功", r1 is not None and r1.get("ok") is True, err or r1)
        r1 = r1 or {}
        check("①b：会话回到用户原文件（后续保存不再写进快照目录）",
              os.path.normpath(bpy.data.filepath) == os.path.normpath(orig),
              {"filepath": bpy.data.filepath, "want": orig, "receipt": r1.get("filepath")})
        check("①c：快照前的对象已回来", bpy.data.objects.get(MARK) is not None, "marker missing")
        check("①d：回执写明 filepath_before / 是否写回原文件",
              ("filepath_before" in r1) and ("wrote_to_original" in r1), sorted(r1))

        # ---------------------------------------------------------------- ④ restore 前的自动备份
        print("== ④ restore 前的 pre_restore 自动备份 ==")
        bk = r1.get("backup") or {}
        check("④a：回执给出备份记录", bool(bk.get("path")), r1.get("backup"))
        check("④b：备份文件真落盘", bool(bk.get("path")) and os.path.isfile(str(bk.get("path"))), bk.get("path"))
        labels = [s.get("label") for s in (J(api["list"]()).get("snapshots") or [])]
        check("④c：备份进了 snapshot 清单（能被 list/restore 找到）",
              bool(bk.get("label")) and bk.get("label") in labels, {"backup": bk.get("label"), "labels": labels})

        # ---------------------------------------------------------------- ⑤ into="snapshot" 不写原文件
        print("== ⑤ into='snapshot'：非破坏模式，一个字节都不许写原文件 ==")
        ob = bpy.data.objects.get(MARK)
        bpy.data.objects.remove(ob, do_unlink=True)
        bpy.context.view_layer.update()
        before_md5 = md5_of(orig)
        r2, err = call(api["restore"], "trs_s1", into="snapshot")
        check("⑤a：into='snapshot' 调用成功（参数存在）", r2 is not None and r2.get("ok") is True, err or r2)
        r2 = r2 or {}
        check("⑤b：原文件 md5 没变（没被写）", md5_of(orig) == before_md5, before_md5)
        check("⑤c：会话停在快照路径上（明确、不假装）",
              str(bpy.data.filepath).startswith(snap_dir), bpy.data.filepath)
        check("⑤d：回执给出 next_save_goes_to / 告警", bool(r2.get("next_save_goes_to")) and bool(r2.get("warning")),
              sorted(r2))

        # ---------------------------------------------------------------- ② 会话已在快照目录 → 兜底回原文件
        print("== ② 会话已在快照目录时，靠快照记录里的 filepath 兜回原文件 ==")
        r3, err = call(api["restore"], "trs_s1")
        check("②a：restore 成功", r3 is not None and r3.get("ok") is True, err or r3)
        check("②b：仍然回到用户原文件（不是回到快照目录）",
              os.path.normpath(bpy.data.filepath) == os.path.normpath(orig),
              {"filepath": bpy.data.filepath, "want": orig})

        # ---------------------------------------------------------------- ③ 未保存场景：不许乱写
        print("== ③ 未保存场景：兜不到目标时不许乱写，只告警 ==")
        try:
            bpy.ops.wm.read_homefile(use_empty=True)
        except Exception as e:
            skip_note = "%s: %s" % (type(e).__name__, e)
            SKIPS.append("③ 未保存场景")
            print("  skip ③ 未保存场景  <- " + skip_note)
        else:
            check("③pre：会话确实处于未保存状态", not bpy.data.filepath, bpy.data.filepath)
            mk_mark((0.0, 0.0, 0.0))
            s2, err = call(api["snapshot"], "trs_s2", "未保存场景")
            check("③pre②：未保存场景也能快照", s2 is not None and s2.get("ok") is True, err or s2)
            ob = bpy.data.objects.get(MARK)
            if ob is not None:
                bpy.data.objects.remove(ob, do_unlink=True)
            r4, err = call(api["restore"], "trs_s2")
            check("③a：restore 成功（不崩）", r4 is not None and r4.get("ok") is True, err or r4)
            r4 = r4 or {}
            check("③b：没有原文件可回 → 保持在快照路径（不许瞎写）",
                  str(bpy.data.filepath).startswith(snap_dir), bpy.data.filepath)
            check("③c：回执明确说明没有写回目标", bool(r4.get("warning")), sorted(r4))
            check("③d：对象回来了", bpy.data.objects.get(MARK) is not None, "marker missing")
    finally:
        for ob in list(bpy.data.objects):
            if ob.name.startswith("DSH_TRS_"):
                try:
                    bpy.data.objects.remove(ob, do_unlink=True)
                except Exception:
                    pass
        if K is not None:
            try:
                K.out_dir = prev_out
            except Exception:
                pass

    print("")
    if FAILS:
        print("FAILED: " + ", ".join(f["name"] for f in FAILS))
    print("HEADLESS " + json.dumps({"ok": len(FAILS) == 0, "passed": len(OKS), "failed": len(FAILS),
                                    "skipped": len(SKIPS), "failures": FAILS, "skips": SKIPS, "outdir": outdir},
                                   ensure_ascii=False))


if __name__ == "__main__":
    main()
