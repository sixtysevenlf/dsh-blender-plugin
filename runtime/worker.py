# -*- coding: utf-8 -*-
"""DSH 热无头 worker —— 常驻的 blender -b 进程，接受代码片段并复用同一个 Blender 会话。

为什么需要它：addon 的 socket 服务在 background 模式下不启动（别人维护的 addon 里的行为），
所以"用 socket 通道"= 必须 GUI、"用 headless"= 每次都冷启动。本 worker 两者都要：
    blender.exe -b --factory-startup --python worker.py -- --port 9879 --gpu auto
它把 **阻塞 accept 循环跑在主线程**（无头下 bpy.app.timers 不触发，实测 0 次；主线程执行 bpy 调用才安全），
复用持久内核 K（与 blender_rt_do 同一套语义），并提供 GPU 前导与结构化回执。

协议（换行分帧 JSON）：
    请求  {"id":1,"op":"ping"} | {"id":2,"op":"exec","code":"..."} | {"id":3,"op":"status"} | {"id":4,"op":"shutdown"}
    应答  {"id":2,"ok":true,"ms":12,"stdout":"...","stderr":"","result":{...}}
约定：脚本里 print("HEADLESS {json}") （单行）会被解析成 result —— 与 blender_rt_headless 一致。
串行语义：一次只处理一个请求；长代码会占住 worker（这是"热"的代价，也是安全的来源）。
"""

import io
import json
import os
import socket
import sys
import time
import traceback

WORKER_VERSION = 1
_started = time.time()
_calls = 0
_errors = 0


def _j(o):
    return json.dumps(o, ensure_ascii=False, default=str)


def _arg(name, default=None):
    argv = sys.argv
    if name in argv:
        i = argv.index(name)
        if i + 1 < len(argv):
            return argv[i + 1]
    return default


def _kernel_bootstrap():
    import types
    K = sys.modules.get("dsh_rt_kernel")
    if K is None:
        K = types.ModuleType("dsh_rt_kernel")
        sys.modules["dsh_rt_kernel"] = K
    import bpy, math, mathutils
    K.bpy = bpy
    K.math = math
    K.mathutils = mathutils
    return K


def _engine_setup(gpu_mode, engine="eevee"):
    """v0.8.0：默认 EEVEE + 光追；engine='cycles' 走 OptiX 设备前导；'keep' 不动。"""
    import bpy
    info = {"engine_mode": engine, "gpu_mode": gpu_mode}
    try:
        import gpu
        info["backend"] = gpu.platform.backend_type_get()
        info["renderer"] = gpu.platform.renderer_get()
    except Exception as e:
        info["backend"] = "n/a: %s" % str(e)[:60]
    sc = bpy.context.scene
    info["before"] = {"engine": sc.render.engine}
    if engine == "keep":
        info["ok"] = True
        info["skipped"] = True
        return info
    if engine == "cycles":
        info.update(_gpu_setup(gpu_mode))
        try:
            sc.render.engine = "CYCLES"
            if info.get("configured"):
                sc.cycles.device = "GPU"
        except Exception as e:
            info["engine_set_err"] = str(e)[:80]
        info["after"] = {"engine": sc.render.engine}
        return info
    ee = getattr(sc, "eevee", None)
    try:
        sc.render.engine = "BLENDER_EEVEE"
        if ee is not None:
            if hasattr(ee, "use_raytracing"):
                ee.use_raytracing = True
            if hasattr(ee, "ray_tracing_method"):
                try:
                    ee.ray_tracing_method = "SCREEN"
                except Exception:
                    pass
            if hasattr(ee, "use_shadows"):
                ee.use_shadows = True
            for attr, val in (("shadow_ray_count", 2), ("shadow_step_count", 8)):
                if hasattr(ee, attr):
                    try:
                        setattr(ee, attr, val)
                    except Exception:
                        pass
            cur = getattr(ee, "taa_render_samples", 64) or 64
            if hasattr(ee, "taa_render_samples") and cur < 64:
                ee.taa_render_samples = 64
        info["configured"] = "eevee-rt" if (ee is not None and getattr(ee, "use_raytracing", False)) else "eevee"
        info["ok"] = True
    except Exception as e:
        info["ok"] = False
        info["error"] = "%s: %s" % (type(e).__name__, str(e)[:120])
    info["after"] = {"engine": sc.render.engine, "rt": getattr(ee, "use_raytracing", None),
                     "samples": getattr(ee, "taa_render_samples", None)}
    info["fell_back_to_cpu"] = False
    return info


def _gpu_setup(mode):
    """mode: auto / true / false —— 与插件侧 GPU_PRELUDE 同语义"""
    import bpy
    info = {"mode": mode}
    if str(mode).lower() in ("false", "off", "none", "0"):
        info.update({"skipped": True, "ok": True})
        return info
    try:
        prefs = bpy.context.preferences.addons["cycles"].preferences
    except Exception as e:
        info.update({"ok": False, "error": "no cycles prefs: %s" % e})
        return info
    sc = bpy.context.scene

    def snap():
        try:
            devs = [d.name for d in prefs.devices if d.use and d.type != "CPU"]
        except Exception:
            devs = []
        return {"device_type": prefs.compute_device_type, "scene_device": getattr(sc.cycles, "device", None), "gpu_enabled": devs}

    info["before"] = snap()
    if info["before"]["device_type"] == "NONE" or not info["before"]["gpu_enabled"]:
        for t in ("OPTIX", "CUDA", "HIP", "ONEAPI", "METAL"):
            try:
                prefs.compute_device_type = t
                prefs.get_devices()
                if [d for d in prefs.devices if d.type != "CPU"]:
                    for d in prefs.devices:
                        d.use = (d.type != "CPU")
                    try:
                        sc.cycles.device = "GPU"
                    except Exception:
                        pass
                    info["configured"] = t
                    break
            except Exception:
                continue
        else:
            info["configured"] = None
            info["error"] = "no GPU backend available"
    info["after"] = snap()
    a = info["after"]
    info["fell_back_to_cpu"] = bool(a["device_type"] == "NONE" or not a["gpu_enabled"])
    info["ok"] = (not info["fell_back_to_cpu"]) or (str(mode).lower() not in ("true", "required"))
    return info


def _exec(code, timeout_ms=None):
    global _calls, _errors
    _calls += 1
    K = _kernel_bootstrap()
    t0 = time.time()
    out, err = io.StringIO(), io.StringIO()
    ok, tb, errmsg = True, None, None
    ns = {"K": K, "bpy": K.bpy, "math": K.math, "mathutils": K.mathutils, "Vector": K.mathutils.Vector}
    try:
        import contextlib
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            exec(compile(code, "<dsh_worker>", "exec"), ns)
    except Exception as e:
        ok = False; errmsg = "%s: %s" % (type(e).__name__, e); tb = traceback.format_exc()
        _errors += 1
    stdout = out.getvalue()
    result = None
    lines = [l for l in stdout.splitlines() if l.startswith("HEADLESS ")]
    if lines:
        try:
            result = json.loads(lines[-1][len("HEADLESS "):].strip())
        except Exception as e:
            result = {"_parse_error": str(e), "raw": lines[-1][:300]}
    return {"ok": ok, "ms": int((time.time() - t0) * 1000), "stdout": stdout[-8000:], "stderr": err.getvalue()[-4000:],
            "result": result, "error": errmsg, "traceback": tb}


def _status(gpu_info):
    import bpy
    K = sys.modules.get("dsh_rt_kernel")
    return {"ok": True, "version": WORKER_VERSION, "pid": os.getpid(),
            "uptime_s": int(time.time() - _started), "calls": _calls, "errors": _errors,
            "objects": len(bpy.data.objects), "file": bpy.data.filepath or None,
            "kernel_modules": sorted([a for a in ("dsh_view_api", "dsh_perf_api", "dsh_contract_api", "dsh_plan_api") if K and hasattr(K, a)]),
            "gpu": gpu_info}


def main():
    port = int(_arg("--port", "9879"))
    gpu_mode = _arg("--gpu", "auto")
    engine = str(_arg("--engine", "eevee")).lower()
    host = _arg("--host", "127.0.0.1")
    gpu_info = _engine_setup(gpu_mode, engine)
    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    srv.bind((host, port))
    srv.listen(4)
    print("DSH_WORKER " + _j({"ready": True, "port": port, "pid": os.getpid(), "version": WORKER_VERSION,
                              "gpu": gpu_info, "blender": __import__("bpy").app.version_string}), flush=True)
    stop = False
    while not stop:
        try:
            conn, addr = srv.accept()          # 主线程阻塞等待 —— bpy 调用随后也在主线程执行
        except Exception as e:
            print("DSH_WORKER_ERR " + _j({"accept": str(e)}), flush=True)
            time.sleep(0.2)
            continue
        conn.settimeout(None)
        f = conn.makefile("rwb")
        try:
            for raw in f:
                try:
                    req = json.loads(raw.decode("utf-8", "replace").strip() or "{}")
                except Exception as e:
                    f.write((_j({"id": None, "ok": False, "error": "bad json: %s" % e}) + "\n").encode()); f.flush(); continue
                rid = req.get("id")
                op = str(req.get("op") or "ping")
                if op == "ping":
                    resp = {"id": rid, "ok": True, "pong": True, "uptime_s": int(time.time() - _started)}
                elif op == "status":
                    resp = {"id": rid, "status": _status(gpu_info)}
                elif op == "exec":
                    r = _exec(str(req.get("code") or ""), req.get("timeoutMs"))
                    resp = {"id": rid}; resp.update(r)
                elif op == "shutdown":
                    resp = {"id": rid, "ok": True, "bye": True}
                    f.write((_j(resp) + "\n").encode()); f.flush()
                    stop = True
                    break
                else:
                    resp = {"id": rid, "ok": False, "error": "unknown op: %s" % op}
                f.write((_j(resp) + "\n").encode()); f.flush()
        except Exception as e:
            try:
                f.write((_j({"ok": False, "error": "conn error: %s" % e}) + "\n").encode()); f.flush()
            except Exception:
                pass
        finally:
            try:
                f.close(); conn.close()
            except Exception:
                pass
    try:
        srv.close()
    except Exception:
        pass
    print("DSH_WORKER_STOP " + _j({"calls": _calls, "errors": _errors, "uptime_s": int(time.time() - _started)}), flush=True)


if __name__ == "__main__":
    main()
