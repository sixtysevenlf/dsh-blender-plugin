# -*- coding: utf-8 -*-
"""DSH 渲染状态跟踪（v0.9.6 · 上游整合 A7）—— 把"渲染中撞满主线程"从超时变成秒回的 busy。

实测动机：本机 8,116 次调用里 **76/111 条失败是超时**，而渲染占满 Blender 主线程是主因之一；
工作区脚本里 bpy.ops.render.render 出现 **362 次 / 334 个文件**。现在的情况是：渲染期间任何
主线程命令都排队 → 客户端等到超时 → 看起来像"通道坏了"。

做法（关键设计）：状态不靠"问 Blender"（那时主线程正忙，问了也排队），而是
**渲染开始时由 bpy.app.handlers 往磁盘写一个标记文件，结束时删掉**；后端（Node）读文件即可
**瞬间**判定 busy —— 不需要任何 Blender 往返。

  render_init     → 写 {state:"rendering", since, scene, frame, pid}
  render_complete → 删（cleared_by="complete"）
  render_cancel   → 删（cleared_by="cancel"）
  load_post       → 删（换文件/崩溃恢复，避免陈标记把通道永久挡住）

后端据此：① 渲染中把主线程写命令**秒回 busy**（并建议渲染完再发 / 或改用 blender_rt_job）；
② render_state/render_wait/render_reset 三个 op；③ 陈标记（默认 >15 min）自动判 stale 并放行。

API 挂 K.dsh_render_guard；install() 幂等（重复调用不会重复注册 handler）。
"""
import json
import os
import sys
import time

import bpy

RG_VERSION = 1
STALE_MS = 15 * 60 * 1000          # 陈标记阈值（Node 侧也用同一口径）
_STATE = {"state": "idle", "since": None, "scene": None, "frame": None, "pid": None, "cleared_by": None}
_INSTALLED = False


import sys as _sys_kit
_KIT = getattr(_sys_kit.modules.get("dsh_rt_kernel"), "dsh_kit", None)
if _KIT is None:
    raise RuntimeError("render_guard 需要共享内核 K.dsh_kit（由 KERNEL_BOOTSTRAP 注入）")


_j = _KIT.j  # 共享内核（原自带实现已删，见 S1）
def _flag_path():
    """标记文件路径由插件注入（DSH_RENDER_FLAG_WIN，Windows 形式；Blender 就在 Windows 上跑）。
    没注入时（无头/自检）退回 DSH_RENDER_FLAG 环境变量 → 临时目录，保证模块自洽可测。"""
    p = globals().get("DSH_RENDER_FLAG_WIN")
    if p:
        return p
    env = os.environ.get("DSH_RENDER_FLAG")
    if env:
        return env
    import tempfile
    return os.path.join(tempfile.gettempdir(), "dsh_render_state.json")


def _write_flag(payload):
    p = _flag_path()
    if not p:
        return False
    try:
        d = os.path.dirname(p)
        if d and not os.path.isdir(d):
            os.makedirs(d, exist_ok=True)
        tmp = p + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False)
        os.replace(tmp, p)
        return True
    except Exception:
        return False


def _clear_flag(why):
    global _STATE
    _STATE = {"state": "idle", "since": None, "scene": None, "frame": None, "pid": None, "cleared_by": why}
    p = _flag_path()
    try:
        if p and os.path.exists(p):
            os.remove(p)
            return True
    except Exception:
        pass
    return False


# ---------------------------------------------------------------- handlers

def _on_render_init(scene=None, *a):
    global _STATE
    now = time.time()
    _STATE = {"state": "rendering", "since": now,
              "scene": getattr(scene, "name", None),
              "frame": getattr(scene, "frame_current", None),
              "pid": os.getpid(), "cleared_by": None}
    _write_flag({"state": "rendering", "since": now, "since_iso": time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(now)),
                 "scene": _STATE["scene"], "frame": _STATE["frame"], "pid": _STATE["pid"],
                 "engine": getattr(getattr(scene, "render", None), "engine", None)})


def _on_render_complete(scene=None, *a):
    _clear_flag("complete")


def _on_render_cancel(scene=None, *a):
    _clear_flag("cancel")


def _on_load_post(*a):
    # 换文件 / 崩溃后恢复：陈标记会永久挡住写通道，这里清掉
    _clear_flag("load_post")


def install(force=False):
    """注册四个 handler（幂等）。返回注册结果。"""
    global _INSTALLED
    H = bpy.app.handlers
    want = [("render_init", _on_render_init), ("render_complete", _on_render_complete),
            ("render_cancel", _on_render_cancel), ("load_post", _on_load_post)]
    added = []
    for name, fn in want:
        lst = getattr(H, name, None)
        if lst is None:
            continue
        if any(getattr(h, "__name__", "") == fn.__name__ for h in lst):
            continue
        lst.append(fn)
        added.append(name)
    _INSTALLED = True
    return {"ok": True, "installed": True, "added": added, "flag_path": _flag_path(),
            "handlers": {n: sum(1 for h in getattr(H, n, []) if getattr(h, "__name__", "").startswith("_on_")) for n, _ in want}}


def mark_rendering(scene=None, note=None):
    """手动标记（自检/异常兜底用）。"""
    _on_render_init(scene or bpy.context.scene)
    return state()


def mark_idle(why="manual"):
    _clear_flag(str(why or "manual"))
    return state()


def state():
    """本地状态 + 磁盘标记（两者不一致时以磁盘为准，并给出 in_sync）。"""
    p = _flag_path()
    disk = None
    try:
        if p and os.path.exists(p):
            with open(p, encoding="utf-8") as f:
                disk = json.load(f)
    except Exception:
        disk = None
    age_ms = None
    if disk and disk.get("since"):
        age_ms = int((time.time() - float(disk["since"])) * 1000)
    return _j({"ok": True, "installed": _INSTALLED, "local": _STATE, "flag_path": p,
               "flag": disk, "age_ms": age_ms, "stale": bool(age_ms is not None and age_ms > STALE_MS),
               "in_sync": (disk is None) == (_STATE.get("state") != "rendering"),
               "handlers": {n: sum(1 for h in getattr(bpy.app.handlers, n, []) if getattr(h, "__name__", "").startswith("_on_"))
                            for n in ("render_init", "render_complete", "render_cancel", "load_post")}})


def selftest():
    """自检：注册 handler → 手动 mark → 断言标记文件出现 → clear → 断言消失 →（可选）真渲染。"""
    ev = {}
    try:
        r = install()
        ev["install"] = r
        p = _flag_path()
        ev["flag_path"] = p
        if not p:
            return _j({"ok": False, "evidence": ev, "error": "没有注入 DSH_RENDER_FLAG_WIN（引擎侧要给路径）"})
        if os.path.exists(p):
            os.remove(p)
        mark_rendering(note="selftest")
        ev["after_mark_exists"] = os.path.exists(p)
        if ev["after_mark_exists"]:
            with open(p, encoding="utf-8") as f:
                ev["flag_content"] = json.load(f)
        st = json.loads(state())
        ev["state_while_marked"] = {"state": (st.get("flag") or {}).get("state"), "age_ms": st.get("age_ms"),
                                    "stale": st.get("stale"), "handlers": st.get("handlers")}
        mark_idle("selftest-clear")
        ev["after_clear_exists"] = os.path.exists(p)
        ok = (bool(ev["after_mark_exists"]) and ev["state_while_marked"]["state"] == "rendering"
              and not ev["after_clear_exists"]
              and all(v >= 1 for v in (ev["install"].get("handlers") or {}).values()))
        return _j({"ok": bool(ok), "evidence": ev})
    except Exception as e:
        return _j({"ok": False, "evidence": ev, "error": "%s: %s" % (type(e).__name__, str(e)[:200])})


def help_():
    return _j({
        "module": "render_guard.py", "version": RG_VERSION,
        "what": "渲染状态跟踪：渲染开始写磁盘标记、结束/取消/换文件时删掉；后端读文件秒判 busy（不占 Blender 往返）",
        "api": {"install": "注册 4 个 handler（幂等）", "state": "本地状态 + 磁盘标记 + stale 判定",
                "mark_rendering/mark_idle": "手动打标（自检与异常兜底）", "selftest": "自检（含 handler 注册断言）"},
        "why": "实测 111 条失败里 76 条是超时，渲染占主线程是主因之一；工作区 render.render 362 次 / 334 个文件",
        "limits": ["stale 阈值 15 min：超时未清的标记会被判 stale 并放行（避免永久挡通道）",
                   "换文件（load_post）会清标记；真正渲染中请用 render_wait 等它，别硬发命令"],
    })


def dispatch(op, args_json):
    args = {}
    if isinstance(args_json, str) and args_json.strip():
        try:
            args = json.loads(args_json)
        except Exception as e:
            return _j({"ok": False, "error": "args 不是合法 JSON: %s" % str(e)[:120]})
    if not isinstance(args, dict):
        return _j({"ok": False, "error": "args 需要对象"})
    kw = {k: v for k, v in args.items() if k != "args"}
    ops = {"install": lambda **k: _j(install(bool(k.get("force")))),
           "state": lambda **k: state(),
           "mark": lambda **k: mark_rendering(note=k.get("note")),
           "clear": lambda **k: mark_idle(k.get("why") or "manual"),
           "selftest": lambda **k: selftest(),
           "help": lambda **k: help_()}
    fn = ops.get(str(op))
    if fn is None:
        return _j({"ok": False, "error": "unknown render op", "op": op, "ops": sorted(ops)})
    try:
        return fn(**kw)
    except Exception as e:
        return _j({"ok": False, "error": "%s: %s" % (type(e).__name__, str(e)[:200]), "op": op})


_DshApi = _KIT.Api  # 共享内核（尾部注册行无需改）
_K = sys.modules.get("dsh_rt_kernel")
if _K is not None:
    _K.dsh_render_guard = _DshApi({"version": RG_VERSION, "dispatch": dispatch, "install": install,
                                   "state": state, "mark": mark_rendering, "clear": mark_idle,
                                   "selftest": selftest, "help": help_})
    # 注入即注册：模块一到就挂 handler，免得"先渲染后安装"漏记
    try:
        install()
    except Exception:
        pass
