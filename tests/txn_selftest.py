# -*- coding: utf-8 -*-
"""txn（事务/回滚）自检 —— 需要插件后端在跑（GUI Blender + addon + 127.0.0.1:9877）。

覆盖：对象级 mark → 移动 → revert（必须回到原位）；文件级 snapshot → list → prune；
以及"边界"：拓扑改动过的对象在 revert 时应被跳过并报告。全程自清理。
"""
import json, os, sys, urllib.request

API = os.environ.get("DSH_BLENDER_API", "http://127.0.0.1:9877")
FAILS = []
OBJ = "DSH_TXN_SELFTEST"
MARK = "dsh_selftest_mark"
SNAP = "dsh_selftest_snap"
CODE_CREATE = "import bpy@@N@@bpy.ops.mesh.primitive_cube_add(size=1.0, location=(0,0,0))@@N@@ob = bpy.context.object; ob.name = 'DSH_TXN_SELFTEST'; ob.select_set(False)@@N@@print('created', ob.name)".replace("@@N@@", chr(10))
CODE_MOVE = "import bpy@@N@@ob = bpy.data.objects['DSH_TXN_SELFTEST']; ob.location = (5.0, 0.0, 0.0)@@N@@print('moved', tuple(round(v, 3) for v in ob.location))".replace("@@N@@", chr(10))
CODE_READ = "import bpy@@N@@ob = bpy.data.objects['DSH_TXN_SELFTEST']@@N@@print('LOC', round(ob.location.x, 3), round(ob.location.y, 3), round(ob.location.z, 3))".replace("@@N@@", chr(10))
CODE_DEL = "import bpy@@N@@ob = bpy.data.objects.get('DSH_TXN_SELFTEST')@@N@@bpy.data.objects.remove(ob, do_unlink=True) if ob else None@@N@@print('removed', bpy.data.objects.get('DSH_TXN_SELFTEST') is None)".replace("@@N@@", chr(10))


def post(path, payload):
    body = json.dumps(payload).encode()
    req = urllib.request.Request(API + path, data=body, headers={"content-type": "application/json"})
    with urllib.request.urlopen(req, timeout=300) as r:
        return json.loads(r.read().decode())


def act(code):
    d = post("/act", {"code": code, "force": True})
    if d.get("error"):
        raise RuntimeError("act 失败: " + str(d.get("error"))[:200])
    return (d.get("stdout") or "").strip()


def txn(op, args=None):
    d = post("/txn", {"op": op, "args": args or {}, "force": True})
    if d.get("ok") is not True:
        raise RuntimeError("txn %s 失败: %s" % (op, str(d.get("error"))[:200]))
    return d.get("result") or {}


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
    print("== 对象级 mark / revert ==")
    out = act(CODE_CREATE)
    check("测试对象已创建", "created" in out, out[:60])
    m = txn("mark", {"label": MARK, "objects": [OBJ]})
    check("mark 记到 1 个对象", int(m.get("objects") or 0) == 1, str(m.get("objects")))
    act(CODE_MOVE)
    loc0 = act(CODE_READ)
    check("移动生效（应在 x=5）", "5.0" in loc0, loc0)
    rv = txn("revert", {"label": MARK})
    check("revert 报告有对象被回滚", int(rv.get("changed_count") or 0) >= 1, str(rv.get("changed_count")))
    check("revert 未跳过对象（拓扑没变）", not (rv.get("blocked") or []), str(rv.get("blocked")))
    loc1 = act(CODE_READ)
    check("位置已回到原点（x=0）", "LOC 0.0 0.0 0.0" in loc1, loc1)
    print("== 文件级 snapshot / list / prune ==")
    sn = txn("snapshot", {"label": SNAP, "note": "selftest"})
    info = sn.get("snapshot") or {}
    check("快照已落盘（bytes > 0）", int(info.get("bytes") or 0) > 0, str(info.get("bytes")) + "B / " + str(info.get("ms")) + "ms")
    ls = txn("list")
    labels = [s.get("label") for s in (ls.get("snapshots") or [])]
    check("list 能看到快照", SNAP in labels, str(labels))
    pr = txn("prune", {"keep": 1})
    check("prune 正常返回", pr.get("ok") is not False, str(pr.get("removed")))
    print("== 清理 ==")
    txn("drop", {"label": MARK})
    out2 = act(CODE_DEL)
    check("测试对象已删除", "removed True" in out2, out2[:60])
    print("")
    if FAILS:
        print("FAILED: " + ", ".join(FAILS))
        return 1
    print("TXN selftest: ALL PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())