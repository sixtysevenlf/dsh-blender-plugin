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

import base64
import io
import json
import os
import socket
import sys
import tempfile
import time
import traceback

WORKER_VERSION = 2
_started = time.time()
_calls = 0
_errors = 0
_boot_info = None
_seq = 0


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
    if engine in ("keep", "workbench", "none"):
        if engine == "workbench":
            try:
                sc.render.engine = "BLENDER_WORKBENCH"
                try:
                    sc.display.shading.light = "STUDIO"
                    sc.display.shading.show_cavity = True
                except Exception:
                    pass
            except Exception as e:
                info["engine_set_err"] = str(e)[:120]
        info["ok"] = True
        info["skipped"] = engine != "workbench"
        info["configured"] = engine
        info["after"] = {"engine": sc.render.engine}
        info["fell_back_to_cpu"] = False
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


def _run_boot(path):
    """执行 engine 拼好的启动前导（KERNEL_BOOTSTRAP + preload 模块源码）。

    为什么放这里：worker 原来只注入 K/bpy，preload 模块（audit/qc/runner…）完全不存在，
    于是热会话里 K.dsh_audit_api 等一律 AttributeError。engine 在 spawn 前把同一份前导写到
    --boot 指定的 .py；这里在 ready 之前 exec 一次。
    """
    global _boot_info
    if not path or not os.path.exists(str(path)):
        _boot_info = {"ok": True, "skipped": True}
        return _boot_info
    K = _kernel_bootstrap()
    out, err = io.StringIO(), io.StringIO()
    ok, errmsg, tb = True, None, None
    ns = {"K": K, "bpy": K.bpy, "math": K.math, "mathutils": K.mathutils,
          "Vector": K.mathutils.Vector, "__name__": "__main__", "__file__": str(path)}
    try:
        import contextlib
        src = open(str(path), "r", encoding="utf-8").read()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            exec(compile(src, str(path), "exec"), ns)
    except Exception as e:
        ok = False
        errmsg = "%s: %s" % (type(e).__name__, e)
        tb = traceback.format_exc()
    _boot_info = {"ok": ok, "path": str(path), "stdout": out.getvalue()[-2000:],
                  "stderr": err.getvalue()[-2000:], "error": errmsg,
                  "traceback": (tb or "")[-2000:],
                  "kernel_modules": sorted([a for a in dir(K) if a.startswith("dsh_") and a.endswith("_api")])}
    return _boot_info


def _frame_dir():
    return str(_arg("--frame-dir", None) or os.environ.get("DSH_WORKER_FRAME_DIR") or tempfile.gettempdir())


def _see(spec=None):
    """无头离屏 see：临时相机 + bpy.ops.render.render + Workbench。

    不走 view.py 的 GPUOffScreen.draw_view3d（需要 VIEW_3D），也不走 render.opengl（需要窗口）——
    这条路径与 geo.quick_views 同源，blender -b 下可用（本机实测 512² 稳态 31–38 ms/帧）。
    """
    global _seq
    import bpy
    try:
        from mathutils import Vector
    except Exception:
        Vector = None
    spec = dict(spec or {})
    if not spec.get("from"):
        spec["from"] = [7, -7, 5]
    if not spec.get("look_at"):
        spec["look_at"] = [0, 0, 1]
    width = int(spec.get("width") or spec.get("max_size") or 560)
    height = int(spec.get("height") or max(120, round(width * 9.0 / 16.0)))
    width = max(64, min(1600, width)); height = max(64, min(1600, height))
    sc = bpy.context.scene
    saved = {"engine": sc.render.engine, "rx": sc.render.resolution_x, "ry": sc.render.resolution_y,
             "pct": sc.render.resolution_percentage, "fp": sc.render.filepath,
             "fmt": sc.render.image_settings.file_format, "cm": sc.render.image_settings.color_mode,
             "transparent": bool(getattr(sc.render, "film_transparent", False)),
             "camera": sc.camera}
    cam = None
    t0 = time.time()
    try:
        cd = bpy.data.cameras.new("__dsh_see_cam")
        cam = bpy.data.objects.new("__dsh_see_cam", cd)
        sc.collection.objects.link(cam)
        cam.location = tuple(float(x) for x in spec["from"][:3])
        look = tuple(float(x) for x in spec["look_at"][:3])
        if Vector is not None:
            cam.rotation_euler = (Vector(look) - Vector(cam.location)).to_track_quat("-Z", "Y").to_euler()
        cam.data.lens = float(spec.get("lens") or 50.0)
        if bool(spec.get("ortho")):
            cam.data.type = "ORTHO"
            cam.data.ortho_scale = float(spec.get("ortho_scale") or 10.0)
        sc.camera = cam
        sc.render.engine = "BLENDER_WORKBENCH"
        sc.render.resolution_x = width
        sc.render.resolution_y = height
        sc.render.resolution_percentage = 100
        sc.render.image_settings.file_format = "PNG"
        sc.render.image_settings.color_mode = "RGBA"
        try:
            sc.render.film_transparent = True
        except Exception:
            pass
        try:
            sh = sc.display.shading
            sh.light = "STUDIO"
            sh.color_type = "MATERIAL" if spec.get("shading") == "MATERIAL" else "SINGLE"
            if sh.color_type == "SINGLE":
                sh.single_color = (0.78, 0.78, 0.78)
            sh.show_cavity = bool(spec.get("cavity", True))
            sh.show_shadows = bool(spec.get("shadows", False))
            sc.display.render_aa = str(spec.get("aa") or "8")
        except Exception:
            pass
        _seq += 1
        path = os.path.join(_frame_dir(), "dsh_see_%d_%d.png" % (os.getpid(), _seq))
        sc.render.filepath = path
        _t_render = time.time()
        bpy.ops.render.render(write_still=True)
        _t_render_ms = int((time.time() - _t_render) * 1000)
        _t_read = time.time()
        data = open(path, "rb").read()
        coverage = None
        try:
            import numpy as np
            img = bpy.data.images.load(path, check_existing=False)
            n = int(img.size[0]) * int(img.size[1])
            buf = np.empty(n * 4, dtype=np.float32)
            img.pixels.foreach_get(buf)
            coverage = round(float((buf[3::4] > 0.5).mean()), 4)
            bpy.data.images.remove(img)
        except Exception:
            coverage = None
        return {"ok": True, "mode": "workbench-offscreen", "path": path,
                "png_b64": base64.b64encode(data).decode("ascii"),
                "width": width, "height": height, "bytes": len(data),
                "coverage_estimate": coverage, "ms": int((time.time() - t0) * 1000),
                "t": {"render_ms": _t_render_ms, "read_ms": int((time.time() - _t_read) * 1000),
                      "total_ms": int((time.time() - t0) * 1000)},
                "engine": "BLENDER_WORKBENCH"}
    except Exception as e:
        return {"ok": False, "mode": "workbench-offscreen", "error": "%s: %s" % (type(e).__name__, e),
                "traceback": traceback.format_exc()[-2000:], "ms": int((time.time() - t0) * 1000)}
    finally:
        try:
            if cam is not None:
                bpy.data.objects.remove(cam, do_unlink=True)
                if cam.data is not None and cam.data.users == 0:
                    bpy.data.cameras.remove(cam.data)
        except Exception:
            pass
        try:
            sc.render.engine = saved["engine"]
            sc.render.resolution_x = saved["rx"]; sc.render.resolution_y = saved["ry"]
            sc.render.resolution_percentage = saved["pct"]
            sc.render.filepath = saved["fp"]
            sc.render.image_settings.file_format = saved["fmt"]
            sc.render.image_settings.color_mode = saved["cm"]
            sc.render.film_transparent = saved["transparent"]
            sc.camera = saved["camera"]
            bpy.context.view_layer.update()
        except Exception:
            pass



def _watch(spec=None):
    """无头帧序列观察：可选先跑 code，再逐帧 Workbench 离屏 see，回逐帧 hash + ≤6 帧图。"""
    import bpy
    import hashlib
    spec = dict(spec or {})
    t0 = time.time()
    sh = {"width": 420, "height": 236}
    for k in ("width", "height", "from", "look_at", "ortho", "ortho_scale", "lens", "shading"):
        if spec.get(k) is not None:
            sh[k] = spec.get(k)
    out = {"ok": True, "mode": "workbench-offscreen-watch", "code": None,
           "frames": [], "picks": [], "distinct": 0, "changed": False, "static_render": False}
    saved_frame = int(getattr(bpy.context.scene, "frame_current", 1) or 1)
    shots = []
    try:
        if spec.get("code"):
            cr = _exec(str(spec.get("code")), int(spec.get("code_timeout_ms") or 60000))
            out["code"] = {"ok": bool(cr.get("ok")), "ms": cr.get("ms"), "error": cr.get("error"),
                           "result": cr.get("result"), "stdout": (cr.get("stdout") or "")[-800:]}
        seconds = max(0.2, min(20.0, float(spec.get("seconds") or 2.0)))
        fps = max(0.5, min(8.0, float(spec.get("fps") or 4.0)))
        total = int(max(1, min(32, int(spec.get("max_frames") or round(seconds * fps)))))
        sc = bpy.context.scene
        raw = spec.get("frames")
        frames = []
        if isinstance(raw, list) and raw:
            for x in raw:
                try:
                    frames.append(int(x))
                except Exception:
                    pass
        elif spec.get("frame_start") is not None or spec.get("frame_end") is not None:
            fs = int(spec.get("frame_start") if spec.get("frame_start") is not None else sc.frame_start)
            fe = int(spec.get("frame_end") if spec.get("frame_end") is not None else sc.frame_end)
            if fe < fs:
                fs, fe = fe, fs
            step = max(1, (fe - fs + 1) // max(1, total))
            frames = list(range(fs, fe + 1, step))[:total]
        if not frames:
            try:
                has_anim = bool(getattr(sc, "animation_data", None)) or any(
                    getattr(o, "animation_data", None) for o in bpy.data.objects)
                fs, fe = int(sc.frame_start), int(sc.frame_end)
                if has_anim and fe > fs:
                    step = max(1, (fe - fs + 1) // max(1, total))
                    frames = list(range(fs, fe + 1, step))[:total]
            except Exception:
                pass
        if not frames:
            frames = [int(getattr(sc, "frame_current", 1) or 1)] * total
            out["static_render"] = True
        if len(frames) > total:
            frames = [frames[int(round(k * (len(frames) - 1) / max(1, total - 1)))] for k in range(total)]
        for i, f in enumerate(frames):
            try:
                bpy.context.scene.frame_set(int(f))
            except Exception:
                pass
            r = _see(sh)
            if not r.get("ok"):
                out["frames"].append({"i": i, "frame": f, "ok": False, "error": r.get("error"), "ms": r.get("ms")})
                continue
            png = str(r.get("png_b64") or "")
            try:
                digest = hashlib.md5(base64.b64decode(png)).hexdigest()[:12]
            except Exception:
                digest = "BAD"
            out["frames"].append({"i": i, "frame": f, "ok": True, "ms": r.get("ms"),
                                  "hash": digest, "width": r.get("width"), "height": r.get("height"),
                                  "path": r.get("path"), "coverage": r.get("coverage_estimate")})
            shots.append({"frame": f, "hash": digest, "png_b64": png, "path": r.get("path")})
        hashes = [s["hash"] for s in shots]
        out["distinct"] = len(set(hashes))
        out["changed"] = out["distinct"] > 1
        if len(shots) <= 6:
            picks = shots
        else:
            picks = [shots[int(round(k * (len(shots) - 1) / 5))] for k in range(6)] if shots else []
        for p in picks:
            out["picks"].append({"frame": p.get("frame"), "hash": p.get("hash"),
                                 "path": p.get("path"), "png_b64": p.get("png_b64")})
        out["sampled"] = len(shots)
        out["requested"] = len(frames)
        out["ms"] = int((time.time() - t0) * 1000)
        return out
    except Exception as e:
        out.update({"ok": False, "error": "%s: %s" % (type(e).__name__, e),
                    "traceback": traceback.format_exc()[-2000:], "ms": int((time.time() - t0) * 1000)})
        return out
    finally:
        try:
            bpy.context.scene.frame_set(saved_frame)
        except Exception:
            pass


def _reset():
    """清场景/材质/相机，保留 K（preload 模块 API 还在）。批量白模之间用。"""
    import bpy
    try:
        bpy.ops.wm.read_factory_settings(use_empty=True)
        return {"ok": True, "objects": len(bpy.data.objects),
                "note": "场景已重置；K 保留（preload 模块 API 仍在），渲染设置按空场景默认"}
    except Exception as e:
        return {"ok": False, "error": "%s: %s" % (type(e).__name__, e),
                "traceback": traceback.format_exc()[-1200:]}


def _purge_modules(prefixes):
    """热重载：按前缀清 sys.modules（改过的用户模块在热会话里会命中旧代码）"""
    import sys as _s, importlib as _i
    _i.invalidate_caches()
    pre = [str(p) for p in (prefixes or []) if str(p)]
    purged = []
    for _n, _m in list(_s.modules.items()):
        if pre and any(_n == p or _n.startswith(p + ".") for p in pre) and not _n.startswith("bpy"):
            _s.modules.pop(_n, None)
            purged.append(_n)
    return purged


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
            "kernel_modules": sorted([a for a in dir(K) if K and a.startswith("dsh_") and a.endswith("_api")]),
            "boot": _boot_info, "frame_dir": _frame_dir(),
            "gpu": gpu_info}


def main():
    port = int(_arg("--port", "9879"))
    gpu_mode = _arg("--gpu", "auto")
    engine = str(_arg("--engine", "eevee")).lower()
    host = _arg("--host", "127.0.0.1")
    gpu_info = _engine_setup(gpu_mode, engine)
    boot_info = _run_boot(_arg("--boot", None))
    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    srv.bind((host, port))
    srv.listen(4)
    print("DSH_WORKER " + _j({"ready": True, "port": port, "pid": os.getpid(), "version": WORKER_VERSION,
                              "gpu": gpu_info, "boot": boot_info,
                              "blender": __import__("bpy").app.version_string}), flush=True)
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
                    _purged = _purge_modules(req.get("purgePrefixes") or [])
                    r = _exec(str(req.get("code") or ""), req.get("timeoutMs"))
                    if _purged:
                        r["purged"] = _purged
                    resp = {"id": rid}; resp.update(r)
                elif op == "see":
                    r = _see(req.get("spec") or req.get("see") or {})
                    resp = {"id": rid}; resp.update(r)
                elif op == "watch":
                    r = _watch(req.get("spec") or req.get("watch") or {})
                    resp = {"id": rid}; resp.update(r)
                elif op == "reset":
                    r = _reset()
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
