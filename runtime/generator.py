# -*- coding: utf-8 -*-
"""生成器注册表（v0.9.0 · Procedura 融合批次 3 #18「代码化建模通道」的 Blender 原生轻量版）。

为什么有它（以及它与上游的关系）
------------------------------------------------------------------------------
Procedura 的核心收益不是"用 OpenSCAD"，而是**程序即形状**：
  ① 模型由一段可读、可 diff、可重放的程序生成；
  ② 产物过"编译门"（程序必须能从头跑通）才能算数；
  ③ 模块级缓存：源码没变就不重跑；
  ④ 源码一变，之前的验证结果立刻过期。
上游用 OpenSCAD + Manifold 当几何内核（**必须 Manifold**：同一个 hull() 实测 CGAL 1774 s
vs Manifold 1.77 s）。本插件是 bpy 原生路线，用户机器上两侧都没有 OpenSCAD —— 所以这里
把①–④用 Blender 原生方式拿到：**生成器 = 一段 python 程序**（bpy/bmesh/mathutils 任意用），
在**全新无头进程**里从零跑一遍来证明可复现，产物写成回执（receipt），源码 hash 当缓存键。

边界（诚实写清）
  * 几何内核仍是 Blender（CSG 用 bpy 的 boolean/hull 之类），**不是** Manifold；大布尔运算
    比 Manifold 慢得多，这是没装 OpenSCAD 的代价，不是能做而没做的事。
  * 生成器跑在**独立进程**里（--factory-startup），所以它看不见你当前 GUI 会话里的临时状态 ——
    这正是"可复现"的定义，但也意味着生成器里的代码不能依赖会话里的 K。

入口（工具侧 op，前缀 generator_）
    blender_rt_plan(op="generator_save", args={name, code, params, note, overwrite})
    blender_rt_plan(op="generator_run",  args={name, args, expect, engine, use_cache, timeout_ms})
    blender_rt_plan(op="generator_list") / generator_get / generator_diff / generator_help
    blender_rt_plan(op="generator_selftest")

程序契约（生成器脚本怎么写）
    * 变量 PARAMS：dict（来自 --args，run 时传什么就是什么）。
    * 建议最后 print("DSH_RECEIPT " + json.dumps({...}))；不打印也行 —— 运行器会自己扫场景
      产出一份回执（对象数/名字/面数/包围盒），所以"脚本沉默"不会变成"没有证据"。
    * 退出码非 0 或抛异常 → 直接 ok:false 并把 stderr 尾巴带回来（不许静默成功）。
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import hashlib
import datetime
import re

GENERATOR_VERSION = 1

PRELUDE = """# ---- dsh generator prelude（运行器注入：不要删）----
import sys, json, math, os
try:
    import bpy, bmesh
    from mathutils import Vector, Matrix, Euler
except Exception:
    bpy = None
_bad = None
try:
    _i = sys.argv.index("--")
    _raw = sys.argv[_i + 1] if len(sys.argv) > _i + 1 else "{}"
    PARAMS = json.loads(_raw)
except Exception as _e:
    PARAMS = {}
    _bad = "无法解析 PARAMS：%s" % _e
# clean_scene（默认 1）：生成器从**空场景**开始 —— 与 OpenSCAD 的"世界由程序定义"语义一致。
# 不这么做，--factory-startup 自带的 Cube 会被算进回执（自检抓过这个 off-by-one）。
_flags = sys.argv[sys.argv.index("--") + 2:] if "--" in sys.argv else []
CLEAN_SCENE = (_flags[0] != "0") if _flags else True
if CLEAN_SCENE and bpy is not None:
    try:
        for _o in list(bpy.data.objects):
            bpy.data.objects.remove(_o, do_unlink=True)
        for _m in list(bpy.data.meshes):
            if _m.users == 0:
                bpy.data.meshes.remove(_m)
    except Exception as _ce:
        _bad = (_bad or "") + " clean_scene 失败：%s" % _ce
"""

POSTLUDE = """# ---- dsh generator postlude（运行器注入：产出回执）----
def _dsh_receipt():
    out = {"PARAMS": PARAMS}
    try:
        import bpy as _b
        objs = [o for o in _b.context.scene.objects if o.type == "MESH"]
        tris = 0
        mn = [1e30, 1e30, 1e30]
        mx = [-1e30, -1e30, -1e30]
        for o in objs:
            tris += sum(max(0, len(p.vertices) - 2) for p in o.data.polygons)
            for c in o.bound_box:
                w = o.matrix_world @ __import__("mathutils").Vector((c[0], c[1], c[2]))
                for i in range(3):
                    mn[i] = min(mn[i], w[i]); mx[i] = max(mx[i], w[i])
        out["objects"] = len(objs)
        out["names"] = [o.name for o in objs][:80]
        out["tris"] = int(tris)
        out["bbox"] = None if not objs else {"min": [round(float(x), 4) for x in mn],
                                             "max": [round(float(x), 4) for x in mx]}
        out["objects_all"] = len(_b.data.objects)
    except Exception as _e:
        out["receipt_error"] = str(_e)[:200]
    print("DSH_RECEIPT " + json.dumps(out, ensure_ascii=False))
_dsh_receipt()
"""


def _j(o):
    return json.dumps(o, ensure_ascii=False, default=str)


def _kernel():
    return sys.modules.get("dsh_rt_kernel")


def _store():
    K = _kernel()
    if K is None:
        raise RuntimeError("需要持久内核 K（走 blender_rt_* 通道）")
    if not hasattr(K, "dsh_generator"):
        K.dsh_generator = {"runs": [], "log": []}
    return K.dsh_generator


def _now():
    return datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _root(d=None):
    """生成器根目录：显式 d > env DSH_GENERATOR_DIR > 用户主目录下 dsh_generators。"""
    d = d or os.environ.get("DSH_GENERATOR_DIR")
    if not d:
        d = os.path.join(os.path.expanduser("~"), "dsh_generators")
    return os.path.abspath(d)


def _paths(name, d=None):
    root = _root(d)
    safe = "".join(c if (c.isalnum() or c in "._-") else "_" for c in str(name))
    if not safe:
        raise ValueError("生成器名不能为空")
    return root, os.path.join(root, safe + ".py"), os.path.join(root, safe + ".json")


def _hash(s):
    return hashlib.md5(s.encode("utf-8")).hexdigest()[:12]


def _load_sidecar(side):
    try:
        with open(side, "r", encoding="utf-8") as fh:
            return json.load(fh)
    except Exception:
        return {}


def _save_sidecar(side, obj):
    try:
        os.makedirs(os.path.dirname(side), exist_ok=True)
        with open(side, "w", encoding="utf-8") as fh:
            json.dump(obj, fh, ensure_ascii=False, indent=1)
        return True
    except Exception:
        return False


def _blender_bin():
    """当前 Blender 的可执行文件（生成器在"另一个全新进程"里跑，用来证明可复现）。"""
    try:
        import bpy
        p = bpy.app.binary_path
        if p and os.path.exists(p):
            return p
    except Exception:
        pass
    return shutil.which("blender") or shutil.which("blender.exe")


# ---------------------------------------------------------------- 注册表

def generator_save(name, code, params=None, note="", dir=None, overwrite=True):
    """保存一个生成器（源码 + sidecar 元数据）。源码一变，之前的运行回执自动算过期。"""
    if not code or not str(code).strip():
        return _j({"ok": False, "error": "code 为空：空生成器必须失败（静默空产出是这个项目的硬红线）"})
    try:
        root, path, side = _paths(name, dir)
    except Exception as e:
        return _j({"ok": False, "error": str(e)})
    old = None
    if os.path.exists(path):
        try:
            with open(path, "r", encoding="utf-8") as fh:
                old = fh.read()
        except Exception:
            old = None
        if old is not None and old != code and not overwrite:
            return _j({"ok": False, "error": "同名生成器已存在且内容不同；要覆盖就 overwrite=true",
                       "path": path, "hash_old": _hash(old), "hash_new": _hash(code)})
    os.makedirs(root, exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(code)
    side_data = _load_sidecar(side)
    meta = {"name": str(name), "params": params or {}, "note": str(note),
            "hash": _hash(code), "bytes": len(code), "saved_at": _now(), "path": path,
            "last_run": side_data.get("last_run")}
    _save_sidecar(side, meta)
    st = _store()
    st["log"].append({"t": _now(), "action": "save", "name": str(name), "hash": meta["hash"]})
    del st["log"][:-200]
    stale = bool(meta.get("last_run") and meta["last_run"].get("hash") != meta["hash"])
    return _j({"ok": True, "name": str(name), "path": path, "sidecar": side, "hash": meta["hash"],
               "bytes": len(code), "last_run_becomes_stale": stale,
               "note": "改了源码 → 上次运行回执过期（这就是「未验证」的定义）；要绿灯就 generator_run 复现一次" if stale else ""})


def generator_list(dir=None):
    root = _root(dir)
    out = []
    try:
        names = sorted(os.listdir(root))
    except Exception:
        names = []
    for n in names:
        if not n.endswith(".py"):
            continue
        base = n[:-3]
        side = os.path.join(root, base + ".json")
        meta = _load_sidecar(side)
        cur = None
        try:
            with open(os.path.join(root, n), "r", encoding="utf-8") as fh:
                cur = _hash(fh.read())
        except Exception:
            pass
        lr = meta.get("last_run") or {}
        out.append({"name": base, "hash_now": cur, "hash_last_run": lr.get("hash"),
                    "stale": bool(lr and cur and lr.get("hash") != cur),
                    "verdict": lr.get("verdict"), "last_run_at": lr.get("t"),
                    "objects": (lr.get("receipt") or {}).get("objects"),
                    "note": meta.get("note")})
    return _j({"ok": True, "root": root, "count": len(out), "generators": out,
               "rule": "stale=true 表示源码在最后一次运行之后被改过 → 那次回执不能再拿来当证据"})


def generator_get(name, dir=None):
    try:
        root, path, side = _paths(name, dir)
    except Exception as e:
        return _j({"ok": False, "error": str(e)})
    if not os.path.exists(path):
        return _j({"ok": False, "error": "生成器不存在: %s" % name, "looked_at": path})
    with open(path, "r", encoding="utf-8") as fh:
        code = fh.read()
    meta = _load_sidecar(side)
    return _j({"ok": True, "name": str(name), "path": path, "hash": _hash(code), "bytes": len(code),
               "code": code, "meta": meta,
               "stale": bool((meta.get("last_run") or {}).get("hash") and (meta["last_run"].get("hash") != _hash(code)))})


def generator_diff(name, dir=None):
    """源码 vs 上次运行时的源码：变没变（变了 → 上次回执过期）。"""
    try:
        root, path, side = _paths(name, dir)
    except Exception as e:
        return _j({"ok": False, "error": str(e)})
    if not os.path.exists(path):
        return _j({"ok": False, "error": "生成器不存在: %s" % name})
    with open(path, "r", encoding="utf-8") as fh:
        code = fh.read()
    meta = _load_sidecar(side)
    lr = meta.get("last_run") or {}
    if not lr:
        return _j({"ok": True, "name": str(name), "hash_now": _hash(code), "last_run": None,
                   "changed": None, "verdict": "unresolved",
                   "note": "从没跑过 → 没有可比对象；先 generator_run 建立基线"})
    changed = lr.get("hash") != _hash(code)
    return _j({"ok": True, "name": str(name), "hash_now": _hash(code), "hash_last_run": lr.get("hash"),
               "changed": changed, "last_run_at": lr.get("t"), "last_verdict": lr.get("verdict"),
               "verdict": "refuted" if changed else "supported",
               "note": "源码变了 → 上次回执过期，必须重跑" if changed else "与上次运行时一致"})


# ---------------------------------------------------------------- 编译门：全新进程复现

def _check_expect(receipt, expect):
    """把回执与预期对拍 → (verdict, why[])。expect 支持 objects/names/min_objects/max_objects/max_tris。"""
    if not expect:
        return "unresolved", ["没有给 expect：只证明了「能跑通」，没证明「跑出来的就是你要的」"]
    why, bad = [], []
    if "objects" in expect and int(receipt.get("objects") or -1) != int(expect["objects"]):
        bad.append("objects 实测 %s ≠ 预期 %s" % (receipt.get("objects"), expect["objects"]))
    if "min_objects" in expect and int(receipt.get("objects") or -1) < int(expect["min_objects"]):
        bad.append("objects 实测 %s < min %s" % (receipt.get("objects"), expect["min_objects"]))
    if "max_objects" in expect and int(receipt.get("objects") or 10 ** 9) > int(expect["max_objects"]):
        bad.append("objects 实测 %s > max %s" % (receipt.get("objects"), expect["max_objects"]))
    if "max_tris" in expect and int(receipt.get("tris") or 0) > int(expect["max_tris"]):
        bad.append("tris 实测 %s > max %s" % (receipt.get("tris"), expect["max_tris"]))
    if "names" in expect:
        want = [str(x) for x in expect["names"]]
        got = [str(x) for x in (receipt.get("names") or [])]
        miss = [n for n in want if n not in got]
        if miss:
            bad.append("缺少预期对象: %s（实测 %d 个：%s）" % (", ".join(miss[:6]), len(got), ", ".join(got[:8])))
    if receipt.get("receipt_error"):
        bad.append("回执本身报错：%s" % receipt["receipt_error"])
    if bad:
        return "refuted", bad
    why.append("预期全部满足：%s" % json.dumps({k: receipt.get(k) for k in ("objects", "tris") if k in receipt},
                                              ensure_ascii=False))
    return "supported", why


def generator_run(name, args=None, dir=None, expect=None, engine="none", factory_startup=True,
                  use_cache=True, timeout_ms=180000, keep_dir=None, clean_scene=True):
    """编译门：在**全新无头 Blender 进程**里从零跑这个生成器，产出回执并与 expect 对拍。

    缓存：源码 hash + args hash 都没变且上次是 supported → 直接返回上次回执（cached=true）。
    """
    try:
        root, path, side = _paths(name, dir)
    except Exception as e:
        return _j({"ok": False, "error": str(e)})
    if not os.path.exists(path):
        return _j({"ok": False, "error": "生成器不存在: %s" % name, "looked_at": path})
    with open(path, "r", encoding="utf-8") as fh:
        code = fh.read()
    h = _hash(code)
    ah = _hash(json.dumps(args or {}, sort_keys=True, ensure_ascii=False))
    meta = _load_sidecar(side)
    lr = meta.get("last_run") or {}
    if use_cache and lr and lr.get("hash") == h and lr.get("args_hash") == ah and lr.get("verdict") == "supported":
        return _j({"ok": True, "cached": True, "name": str(name), "hash": h, "args_hash": ah,
                   "verdict": lr.get("verdict"), "receipt": lr.get("receipt"), "ms": 0,
                   "note": "源码与参数都没变且上次已过门 → 命中缓存（不改就不重跑，这是「模块级缓存」）"})
    exe = _blender_bin()
    if not exe:
        return _j({"ok": False, "error": "找不到 blender 可执行文件（bpy.app.binary_path 为空）"})
    tmpdir = keep_dir or tempfile.mkdtemp(prefix="dsh_gen_")
    os.makedirs(tmpdir, exist_ok=True)
    run_py = os.path.join(tmpdir, "_dsh_generator_run.py")
    with open(run_py, "w", encoding="utf-8") as fh:
        fh.write("# 由 generator_run 拼装：prelude + 你的源码 + postlude\n")
        fh.write(PRELUDE)
        fh.write("\n# ---- 生成器源码（%s / %s）----\n" % (name, h))
        fh.write(code)
        fh.write("\n")
        fh.write(POSTLUDE)
    cmd = [exe, "-b"]
    if factory_startup:
        cmd.append("--factory-startup")
    if engine and engine != "none":
        cmd += ["--python-expr", "import bpy;bpy.context.scene.render.engine=%s" % json.dumps(engine)]
    cmd += ["--python", run_py, "--", json.dumps(args or {}, ensure_ascii=False),
            "1" if clean_scene else "0"]
    t0 = time.time()
    try:
        p = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                           timeout=max(5, int(timeout_ms) / 1000.0))
        out = p.stdout.decode("utf-8", "ignore")
        err = p.stderr.decode("utf-8", "ignore")
        rc = p.returncode
    except subprocess.TimeoutExpired:
        out, err, rc = "", "timeout after %sms" % timeout_ms, -9
    except Exception as e:
        out, err, rc = "", "%s: %s" % (type(e).__name__, str(e)[:200]), -1
    ms = int((time.time() - t0) * 1000)
    receipt = None
    for line in out.splitlines():
        if line.startswith("DSH_RECEIPT "):
            try:
                receipt = json.loads(line[len("DSH_RECEIPT "):])
            except Exception:
                receipt = None
    script_error = bool(re.search(r"Traceback \(most recent call last\)|(^|\n)\s*\w*Error:", err or ""))
    if rc != 0 or receipt is None or script_error:
        verdict = "refuted"
        why = ["进程 exit=%s" % rc]
        if receipt and receipt.get("receipt_error"):
            why.append("回执报错：%s" % receipt["receipt_error"])
        if err.strip():
            why.append("stderr 尾巴：%s" % err.strip()[-500:])
        elif receipt is None:
            why.append("没有回执（脚本可能没跑到 postlude）")
        if script_error and rc == 0:
            why.append("实测坑：Blender 的 --python 脚本抛异常时**退出码仍是 0** → 判定改用「回执 + stderr 异常标记」，"
                       "只看 exit 会漏判")
        result = {"ok": True, "cached": False, "name": str(name), "hash": h, "args_hash": ah, "ms": ms,
                  "exit": rc, "verdict": verdict, "why": why, "receipt": receipt,
                  "stdout_tail": out.strip()[-800:], "stderr_tail": err.strip()[-800:],
                  "script": run_py, "timeout_ms": timeout_ms,
                  "note": "编译门没过（没跑通就不算数）。脚本与日志留在上面路径，便于查错。"}
        lr0 = meta.get("last_run")
        if not isinstance(lr0, dict):
            lr0 = {}
        lr0.update({"hash": h, "args_hash": ah, "verdict": verdict,
                    "t": _now(), "ms": ms, "receipt": receipt})
        meta["last_run"] = lr0
        _save_sidecar(side, meta)
        return _j(result)
    verdict, why = _check_expect(receipt, expect)
    meta["last_run"] = {"hash": h, "args_hash": ah, "verdict": verdict, "t": _now(), "ms": ms,
                        "receipt": receipt, "expect": expect}
    _save_sidecar(side, meta)
    st = _store()
    st["runs"].append({"t": _now(), "name": str(name), "hash": h, "verdict": verdict, "ms": ms})
    del st["runs"][:-200]
    return _j({"ok": True, "cached": False, "name": str(name), "hash": h, "args_hash": ah, "ms": ms,
               "exit": rc, "verdict": verdict, "why": why, "receipt": receipt, "script": run_py,
               "expect": expect, "timeout_ms": timeout_ms,
               "note": ("三个态：" "supported=预期满足；refuted=预期不满足（已把差异列在 why 里）；"
                        "unresolved=没给 expect，只证明了能跑通、没证明跑对")})


def generator_help():
    return _j({"version": GENERATOR_VERSION,
               "ops": {"save": "name, code, params, note, dir, overwrite → 写 <root>/<name>.py + sidecar",
                       "run": "name, args, expect{objects|names|min_objects|max_objects|max_tris}, "
                              "engine, use_cache, timeout_ms, factory_startup → 全新无头进程复现 + 对拍",
                       "list": "dir → 全部生成器 + stale（源码在最后一次运行后被改过）",
                       "get": "name → 源码 + sidecar", "diff": "name → 源码 vs 上次运行时（变了 → 回执过期）",
                       "selftest": "generator_selftest()"},
               "root": "默认 ~/dsh_generators（env DSH_GENERATOR_DIR 或 dir 参数可覆盖）",
               "program_contract": "脚本里用 PARAMS（dict）；建议 print(\"DSH_RECEIPT \" + json.dumps({...}))；"
                                   "不打印也行 —— 运行器会扫场景自己出回执（所以「没证据」不会发生）",
               "why": "程序即形状的四件事：可重放 / 编译门 / 模块级缓存 / 源码一变回执过期。"
                      "几何内核仍是 Blender（不是 Manifold）——大布尔运算会比 Manifold 慢得多",
               "vs_upstream": "上游 Procedura 用 OpenSCAD+Manifold 当几何内核；本插件是 bpy 原生路线，"
                              "本模块把那四件事用 Blender 原生方式实现（本机两侧都没装 OpenSCAD）"})


def generator_selftest():
    """合成自检：注册 → 跑通（supported）→ 缓存命中 → 预期不满足（refuted）→ 改源码（stale）→ 坏脚本（refuted）。"""
    tmp = tempfile.mkdtemp(prefix="dsh_gen_selftest_")
    ok_all, steps = True, []
    try:
        code = "\n".join([
            "# 自检生成器：按 PARAMS 摆几个方块",
            "n = int(PARAMS.get('n', 3))",
            "size = float(PARAMS.get('size', 1.0))",
            "for i in range(n):",
            "    bpy.ops.mesh.primitive_cube_add(size=size, location=(i * 1.5, 0, 0))",
            "    bpy.context.object.name = 'GEN_CUBE_%02d' % i",
            "print('DSH_RECEIPT ' + json.dumps({'made': n, 'size': size}))",
        ])
        r1 = json.loads(generator_save("selftest_cubes", code, {"n": 3, "size": 1.0}, "自检", dir=tmp))
        steps.append({"step": "save", "ok": r1.get("ok"), "hash": r1.get("hash")})
        ok_all &= bool(r1.get("ok"))

        a1 = json.loads(generator_run("selftest_cubes", {"n": 3, "size": 1.0},
                                      dir=tmp, expect={"objects": 3, "names": ["GEN_CUBE_00", "GEN_CUBE_02"]},
                                      timeout_ms=120000))
        steps.append({"step": "run(expect ok)", "verdict": a1.get("verdict"), "objects": (a1.get("receipt") or {}).get("objects"),
                      "ms": a1.get("ms"), "cached": a1.get("cached"), "why": a1.get("why")})
        ok_all &= (a1.get("verdict") == "supported" and (a1.get("receipt") or {}).get("objects") == 3)

        a1b = json.loads(generator_run("selftest_cubes", {"n": 3, "size": 1.0}, dir=tmp,
                                       expect={"objects": 3}, timeout_ms=120000))
        steps.append({"step": "run again (cache)", "cached": a1b.get("cached"), "ms": a1b.get("ms"),
                      "verdict": a1b.get("verdict")})
        ok_all &= (a1b.get("cached") is True and a1b.get("ms") == 0)

        a2 = json.loads(generator_run("selftest_cubes", {"n": 5, "size": 1.0}, dir=tmp,
                                      expect={"objects": 3}, timeout_ms=120000))
        steps.append({"step": "run(expect mismatch)", "verdict": a2.get("verdict"), "objects": (a2.get("receipt") or {}).get("objects"),
                      "why": a2.get("why")})
        ok_all &= (a2.get("verdict") == "refuted")

        a3 = json.loads(generator_run("selftest_cubes", {"n": 5, "size": 1.0}, dir=tmp,
                                      engine="none", timeout_ms=120000))
        steps.append({"step": "run(no expect)", "verdict": a3.get("verdict"),
                      "why": (a3.get("why") or [""])[0][:80]})
        ok_all &= (a3.get("verdict") == "unresolved")

        r2 = json.loads(generator_save("selftest_cubes", code.replace("1.5", "2.0"), {"n": 3}, "改过", dir=tmp))
        d = json.loads(generator_diff("selftest_cubes", dir=tmp))
        steps.append({"step": "save(overwrite)+diff", "stale_after_edit": r2.get("last_run_becomes_stale"),
                      "diff_changed": d.get("changed"), "diff_verdict": d.get("verdict")})
        ok_all &= (d.get("changed") is True and d.get("verdict") == "refuted")

        bad = json.loads(generator_save("selftest_bad", "def broken(:\n    pass\n", dir=tmp))
        b1 = json.loads(generator_run("selftest_bad", dir=tmp, timeout_ms=120000))
        steps.append({"step": "bad script", "save_ok": bad.get("ok"), "verdict": b1.get("verdict"),
                      "exit": b1.get("exit"), "stderr": (b1.get("stderr_tail") or "")[-120:],
                      "exit_code_reliable": False,
                      "why_note": "实测：Blender --python 抛异常时 exit 仍是 0 → 判据用回执缺失 + stderr 异常标记"})
        ok_all &= (b1.get("verdict") == "refuted" and "SyntaxError" in (b1.get("stderr_tail") or "")
                   and (b1.get("why") and any("退出码仍是 0" in w for w in b1["why"])))

        lst = json.loads(generator_list(dir=tmp))
        steps.append({"step": "list", "count": lst.get("count")})
        ok_all &= (lst.get("count") == 2)
        return _j({"ok": bool(ok_all), "steps": steps, "tmp": tmp,
                   "expect": "save ok → run 3 个方块 supported → 再跑命中缓存(ms=0) → expect 不符 refuted → "
                             "没给 expect unresolved → 改源码后 diff.changed=true/verdict=refuted → "
                             "坏脚本 refuted（stderr 带 SyntaxError，且判据不依赖 exit —— Blender 异常退出码为 0）→ "
                             "list 数到 2 个"})
    except Exception as e:
        import traceback
        return _j({"ok": False, "error": "%s: %s" % (type(e).__name__, str(e)[:300]),
                   "traceback": traceback.format_exc()[-900:], "steps": steps})


def generator_dispatch(op, args=None):
    if isinstance(args, str):
        try:
            args = json.loads(args) if args.strip() else {}
        except Exception:
            args = {}
    kw = {}
    for k, v in (args or {}).items():
        if k == "args" and isinstance(v, dict):
            kw.update(v)
        else:
            kw[k] = v
    ops = {"save": generator_save, "run": generator_run, "list": generator_list, "get": generator_get,
           "diff": generator_diff, "help": generator_help, "selftest": generator_selftest}
    fn = ops.get(str(op))
    if fn is None:
        return _j({"ok": False, "error": "unknown generator op", "op": op, "ops": sorted(ops)})
    try:
        return fn(**kw)
    except TypeError as e:
        return _j({"ok": False, "error": "参数不匹配: %s" % str(e)[:200], "op": op, "help": generator_help()})


import sys as _sys
_K = _sys.modules.get("dsh_rt_kernel")
if _K is not None:
    _K.dsh_generator_api = {"version": GENERATOR_VERSION, "dispatch": generator_dispatch,
                            "save": generator_save, "run": generator_run, "list": generator_list,
                            "get": generator_get, "diff": generator_diff,
                            "selftest": generator_selftest, "help": generator_help}

# ---- v0.9.1（93-B1/B2）：API 可调用化（换成 dict 子类实例，返回已解析对象）----
# 背景：K.dsh_x_api 原来是普通 dict → 进程内 api(args) 报 TypeError: 'dict' object is not callable；
# 且 dispatch 返回 JSON 字符串，调用方还得自己 json.loads。
# 现在：api("op", {…}) 或 api({…}) → dict；api["dispatch"](op, json_str) 仍返回 str（引擎契约不变）。
# 注意：dict 是静态类型，不能对已有实例做 __class__ 赋值（实测 TypeError），所以换成一个新实例。
class _DshApi(dict):
    _DEFAULT_OP = "list"

    def __init__(self, base=None):
        dict.__init__(self, base or {})

    def __call__(self, op=None, args=None, **kw):
        if op is None or isinstance(op, dict):
            args, op = (op if isinstance(op, dict) else args), self._DEFAULT_OP
        payload = args if isinstance(args, str) else json.dumps(dict(args or {}, **kw),
                                                               ensure_ascii=False, default=str)
        return json.loads(self["dispatch"](str(op), payload))

    def call(self, op, args=None, **kw):
        return self(op, args, **kw)


import sys as _sys_api
_K_api = _sys_api.modules.get("dsh_rt_kernel")
if _K_api is not None and isinstance(getattr(_K_api, "dsh_generator_api", None), dict) \
        and not isinstance(getattr(_K_api, "dsh_generator_api", None), _DshApi):
    _K_api.dsh_generator_api = _DshApi(_K_api.dsh_generator_api)
