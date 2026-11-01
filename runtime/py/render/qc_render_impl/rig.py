# -*- coding: utf-8 -*-
"""Split from qc_render.py; behavior preserved by the facade."""
from .common import *  # noqa: F401,F403

def _add_rig(az, energies):
    made = []
    for key in ("key", "fill", "rim"):
        en = float((energies or {}).get(key, 0.0) or 0.0)
        if en <= 0:
            continue
        daz, el = LIGHT_ANGLES[key]
        a = math.radians(float(az) + daz)
        e = math.radians(el)
        from_dir = Vector((math.cos(e) * math.cos(a), math.cos(e) * math.sin(a), math.sin(e)))
        ld = bpy.data.lights.new("DSH_QC_LIGHT_" + key.upper(), type="SUN")
        ld.energy = en
        try:
            ld.angle = math.radians(LIGHT_SOFT[key])
        except Exception:
            pass
        ob = bpy.data.objects.new("DSH_QC_LIGHT_" + key.upper(), ld)
        bpy.context.scene.collection.objects.link(ob)
        ob.location = Vector((0.0, 0.0, 0.0))
        ob.rotation_mode = "QUATERNION"
        ob.rotation_quaternion = (-from_dir).to_track_quat("-Z", "Y")
        made.append(ob)
    return made


def _aim_rig(objs, az):
    for ob in objs:
        key = ob.name.rsplit("_", 1)[-1].lower()
        daz, el = LIGHT_ANGLES.get(key, (0.0, 45.0))
        a = math.radians(float(az) + daz)
        e = math.radians(el)
        from_dir = Vector((math.cos(e) * math.cos(a), math.cos(e) * math.sin(a), math.sin(e)))
        ob.rotation_quaternion = (-from_dir).to_track_quat("-Z", "Y")


# ---------------------------------------------------------------- 读回：alpha 包围盒

def _alpha_stats(path, thr=0.02):
    img = bpy.data.images.load(path, check_existing=False)
    try:
        w, h = int(img.size[0]), int(img.size[1])
        buf = np.empty(w * h * 4, dtype=np.float32)
        img.pixels.foreach_get(buf)
    finally:
        bpy.data.images.remove(img)
    a = buf.reshape(h, w, 4)[::-1, :, 3]
    m = a > float(thr)
    ys, xs = np.nonzero(m)
    if len(xs) == 0:
        return {"bbox": None, "coverage": 0.0, "alpha_max": round(float(a.max()), 4), "size": [w, h]}
    x0, x1, y0, y1 = int(xs.min()), int(xs.max()), int(ys.min()), int(ys.max())
    return {"bbox": [x0, y0, x1, y1], "margin_px": [x0, y0, w - 1 - x1, h - 1 - y1],
            "coverage": round(float(m.mean()), 4),
            "fill": round(float((x1 - x0 + 1) * (y1 - y0 + 1)) / float(w * h), 4),
            "alpha_max": round(float(a.max()), 4), "size": [w, h]}


# ---------------------------------------------------------------- 引擎 / 渲染设置

def _set_engine(mode):
    """mode: eevee | cycles | keep。返回 (before, after) 供还原。"""
    sc = bpy.context.scene
    before = sc.render.engine
    m = str(mode or "keep").lower()
    if m in ("eevee", "eevee_rt", "e"):
        try:
            sc.render.engine = "BLENDER_EEVEE"
        except Exception:
            sc.render.engine = "BLENDER_EEVEE_NEXT"
        ee = getattr(sc, "eevee", None)
        if ee is not None:
            for attr, val in (("use_raytracing", True), ("use_shadows", True)):
                if hasattr(ee, attr):
                    try:
                        setattr(ee, attr, val)
                    except Exception:
                        pass
            if hasattr(ee, "ray_tracing_method"):
                try:
                    ee.ray_tracing_method = "SCREEN"
                except Exception:
                    pass
    elif m in ("cycles", "c"):
        sc.render.engine = "CYCLES"
    return before, sc.render.engine


def _restore_engine_samples(sc, eng_engine, samp_before):
    """把引擎/采样写回并**逐项核对**（v0.9.1：拿不到渲染锁的早退路径也必须有这一步）。

    为什么单独拎出来：qc_render_views 里 `_set_engine`/`_apply_samples` 在 try 之前执行，
    早退（例如等锁超时）不会走 finally —— 实测踩到：一次锁超时的自检把 eevee.taa_render_samples
    从 64 留成了 16（自检 E2 快照对拍抓到）。改了渲染设置就必须还，早退路径也不例外。
    """
    out = {"ok": True, "engine": None, "samples": {}, "errors": []}
    try:
        if eng_engine:
            sc.render.engine = str(eng_engine)
        out["engine"] = str(sc.render.engine)
        if eng_engine and str(sc.render.engine) != str(eng_engine):
            out["ok"] = False
            out["errors"].append("engine 回读是 %r（期望 %r）" % (str(sc.render.engine), str(eng_engine)))
    except Exception as e:
        out["ok"] = False
        out["errors"].append("engine: %s: %s" % (type(e).__name__, str(e)[:100]))
    ee = getattr(sc, "eevee", None)
    if ee is not None and "eevee.taa_render_samples" in (samp_before or {}):
        try:
            ee.taa_render_samples = int(samp_before["eevee.taa_render_samples"])
            out["samples"]["eevee.taa_render_samples"] = int(ee.taa_render_samples)
        except Exception as e:
            out["ok"] = False
            out["errors"].append("eevee: %s: %s" % (type(e).__name__, str(e)[:100]))
    if "cycles.samples" in (samp_before or {}):
        try:
            sc.cycles.samples = int(samp_before["cycles.samples"])
            out["samples"]["cycles.samples"] = int(sc.cycles.samples)
        except Exception as e:
            out["ok"] = False
            out["errors"].append("cycles: %s: %s" % (type(e).__name__, str(e)[:100]))
    return out


def _engine_label():
    sc = bpy.context.scene
    e = str(sc.render.engine)
    if "EEVEE" in e:
        ee = getattr(sc, "eevee", None)
        return e + ("+RT" if getattr(ee, "use_raytracing", False) else "")
    return e


def _apply_samples(n):
    sc = bpy.context.scene
    n = int(max(1, int(n)))
    done = {}
    ee = getattr(sc, "eevee", None)
    if ee is not None and hasattr(ee, "taa_render_samples"):
        done["eevee.taa_render_samples"] = int(ee.taa_render_samples)
        ee.taa_render_samples = n
    if hasattr(sc, "cycles"):
        try:
            done["cycles.samples"] = int(sc.cycles.samples)
            sc.cycles.samples = n
        except Exception:
            pass
    return done


# ---------------------------------------------------------------- v0.8.11（B）：实测渲染设备
# 为什么有它（Procedura src/render/device_line.ts 的原文教训）：Cycles 会**静默回落 CPU**（同一张约 7× 慢），
# 而 jsonl 里原来只有 {view, ms, bytes, hash} —— 慢的原因在产物里一个字都查不到；
# 他们那边 `_blender_gpu.py` 专门打印了 "[render] device: …"，却被上层"成功就把 stdout 扔掉"给吞了。
# 纪律：**只读 bpy 的真实状态，拿不到就 null + device_source**，绝不按引擎名猜设备型号。
# （perf.py 的 _gpu_setup / _pick_denoiser 是"设置"语义：它改设备；这里只读，不调用它、更不改 perf.py。）

def _cycles_prefs():
    """拿 cycles 偏好对象 → (prefs, None) 或 (None, 原因)。"""
    try:
        addon = bpy.context.preferences.addons.get("cycles")
        prefs = getattr(addon, "preferences", None) if addon else None
        if prefs is None:
            return None, "拿不到 preferences.addons['cycles'].preferences（addon 未启用或该构建无 Cycles）"
        return prefs, None
    except Exception as e:
        return None, "读 cycles 偏好抛错：%s: %s" % (type(e).__name__, str(e)[:120])


def _cycles_device_list(prefs):
    """实测设备列表 [{name,type,use}] —— 读之前快照 use、读之后原样回写，绝不动用户的设备勾选。

    坑（实测）：prefs.devices 在**没调过 get_devices()** 时是**空列表**（新起的 -b 进程里必然如此），
    直接读会得出"一个设备都没有"的假结论 —— 静默 CPU 那类误判就是这么来的。实测 5.2.2 上
    get_devices() 会保留 use 标记，但这里仍按"先快照后回写"处理：读操作不留副作用。
    """
    snap = {}
    try:
        for d in prefs.devices:
            snap[str(d.name)] = bool(d.use)
    except Exception:
        pass
    try:
        prefs.get_devices()
    except Exception as e:
        return None, "prefs.get_devices() 失败：%s: %s" % (type(e).__name__, str(e)[:120])
    out = []
    try:
        for d in prefs.devices:
            nm = str(d.name)
            if nm in snap:
                try:
                    d.use = snap[nm]       # 回写：读取不该改变用户的启用状态
                except Exception:
                    pass
            out.append({"name": nm, "type": str(d.type), "use": bool(d.use)})
    except Exception as e:
        return None, "遍历 prefs.devices 失败：%s: %s" % (type(e).__name__, str(e)[:120])
    return out, None


def _device_info():
    """这一次渲染会走哪个设备 —— {"engine","device","gpu","source","detail"}，device 可为 null（不许猜）。

    device=null 的情形都是**真的拿不到判据**，source 里逐条写明为什么；能拿到时给出判据出处：
      ① CYCLES：scene.cycles.device + preferences['cycles'].preferences.devices 里 use=True 的非 CPU 设备。
         · device=GPU 且确有启用的 GPU → device=<设备 type：OPTIX/CUDA/HIP/ONEAPI/METAL>、gpu=设备名；
         · device=GPU 但**没有任何启用的 GPU** → null，并在 source 里点名"Cycles 会静默回落 CPU（≈7×）"；
         · device=CPU → "CPU"（bpy 不区分"用户就要 CPU"与"GPU 不可用后的回落"，所以 CPU 本身不代表正常）；
      ② EEVEE / WORKBENCH：GPU 光栅化引擎、没有 CPU 路径 → device="GPU"（引擎语义，不是猜型号）；
         具体设备型号 bpy 不暴露（后台进程里 gpu.platform 需要 gpu.init，本模块**不调用**以免副作用）。
    """
    sc = bpy.context.scene
    eng = str(getattr(sc.render, "engine", "") or "")
    info = {"engine": eng or None, "device": None, "gpu": None, "source": None, "detail": {}}
    if not eng:
        info["source"] = "拿不到：scene.render.engine 为空"
        return info
    if "CYCLES" in eng:
        c = getattr(sc, "cycles", None)
        if c is None:
            info["source"] = "拿不到：引擎是 %s 但 scene.cycles 不存在 → 不猜" % eng
            return info
        cdev = str(getattr(c, "device", "") or "")
        prefs, perr = _cycles_prefs()
        devs, derr = (None, perr) if prefs is None else _cycles_device_list(prefs)
        ctype = None
        try:
            ctype = str(prefs.compute_device_type)
        except Exception:
            ctype = None
        info["detail"] = {"cycles_device": cdev or None, "compute_device_type": ctype,
                          "devices": devs, "devices_error": derr}
        enabled = [d for d in (devs or []) if d.get("use") and str(d.get("type")) != "CPU"]
        if cdev == "GPU" and enabled:
            info["device"] = str(enabled[0]["type"])
            info["gpu"] = ", ".join(sorted(set(str(d["name"]) for d in enabled)))
            info["source"] = ("bpy 实测：scene.cycles.device=GPU，且偏好里 use=True 的非 CPU 设备 = %s"
                              % info["gpu"])
        elif cdev == "GPU":
            info["source"] = ("拿不到：scene.cycles.device=GPU，但偏好里没有任何 use=True 的非 CPU 设备"
                              "（设备列表读取：%s）→ Cycles 会**静默回落 CPU**（约 7× 慢，就是 Procedura "
                              "device_line 要抓的那个坑）；bpy 不回报实际后端，故 device=null 不猜"
                              % (derr or "读到 0 个启用设备"))
        elif cdev == "CPU":
            info["device"] = "CPU"
            info["source"] = ("bpy 实测：scene.cycles.device=CPU —— 注意 bpy 不区分「用户就要 CPU」与"
                              "「GPU 不可用后的回落」，所以 device=CPU 本身**不代表正常**")
        else:
            info["source"] = "拿不到：scene.cycles.device=%r 既不是 CPU 也不是 GPU → 不猜" % cdev
        return info
    if "EEVEE" in eng or "WORKBENCH" in eng:
        info["device"] = "GPU"
        r = None
        try:
            import gpu
            r = "%s %s" % (str(gpu.platform.vendor_get()), str(gpu.platform.renderer_get()))
        except Exception as e:
            info["detail"] = {"gpu_platform_error": "%s: %s" % (type(e).__name__, str(e)[:100])}
        info["gpu"] = r
        info["source"] = (("引擎语义：%s 是 GPU 光栅化（无 CPU 路径）→ device=GPU；型号串来自 gpu.platform 实测"
                           % eng) if r else
                          ("引擎语义：%s 是 GPU 光栅化（无 CPU 路径）→ device=GPU；型号串这次拿不到"
                           "（-b 进程首次渲染前 gpu.platform 不可用，_device_live 会在渲完后补上）" % eng))
        return info
    info["source"] = "拿不到：未知引擎 %s 不知道走什么设备 → null 不猜（要判据就扩 _device_info）" % eng
    return info


def _device_slim(info):
    """jsonl 每行用的精简设备块（{engine,device,gpu,source}）—— 不塞设备全表，否则每行几百字节。"""
    d = {"engine": info.get("engine"), "device": info.get("device"), "gpu": info.get("gpu")}
    det = info.get("detail") or {}
    for k in ("cycles_device", "compute_device_type"):
        if det.get(k) is not None:
            d[k] = det[k]
    d["source"] = info.get("source")
    return d


def _device_line(info):
    """一行版（Procedura device_line.ts 的 `[render] device: …`）：抓日志时 grep 一行就够。"""
    if info.get("device") is None:
        return "[render] device: null — %s" % (info.get("source") or "拿不到")
    return "[render] device: %s%s (%s)" % (info.get("device"),
                                           (" — " + str(info["gpu"])) if info.get("gpu") else "",
                                           info.get("engine"))


def _device_live(base):
    """渲染完一张后的**即时回读**（entry 里那行 device 用这个）。

    为什么不能只在批前测一次：踩过的坑 ——
      · 后台进程里 `gpu.platform` 在**首次渲染之后**才可用（GL/Vulkan 上下文这时才起来）：
        批前测 EEVEE 只能给 device=GPU/gpu=null，渲染后能补上真实 renderer 串
        （实测："NVIDIA Corporation NVIDIA GeForce RTX 4060 Laptop GPU/PCIe/SSE2"）；
      · Cycles 的 `scene.cycles.device` 可以在批次中途被人改（脚本/驱动器），所以每张都重读一次；
        设备表沿用批内快照（每张都 get_devices() 没必要，纯读也会刷新设备列表缓存）。
    """
    info = dict(base or {})
    info["detail"] = dict((base or {}).get("detail") or {})
    eng = str(info.get("engine") or "")
    if "EEVEE" in eng or "WORKBENCH" in eng:
        if not info.get("gpu"):
            try:
                import gpu
                info["gpu"] = "%s %s" % (str(gpu.platform.vendor_get()), str(gpu.platform.renderer_get()))
                info["source"] = ("引擎语义：%s 是 GPU 光栅化（无 CPU 路径）→ device=GPU；renderer 串来自 "
                                  "渲染后实测 gpu.platform（后台进程首次渲染前拿不到）" % eng)
            except Exception as e:
                info["detail"]["gpu_platform_error_after_render"] = "%s: %s" % (type(e).__name__, str(e)[:100])
        return info
    if "CYCLES" in eng:
        try:
            now = str(bpy.context.scene.cycles.device or "")
        except Exception:
            now = None
        old = info["detail"].get("cycles_device")
        if now and now != old:
            info["detail"]["cycles_device_changed_from"] = old
            info["detail"]["cycles_device"] = now
            if now == "CPU":
                info["device"], info["gpu"] = "CPU", None
                info["source"] = ("实测：批次中途 scene.cycles.device 变成了 CPU（批前是 %s）→ 这一张是 CPU 渲的；"
                                  "bpy 不区分「用户要 CPU」与「GPU 不可用回落」" % old)
            elif now == "GPU":
                devs = [d for d in (info["detail"].get("devices") or [])
                        if d.get("use") and str(d.get("type")) != "CPU"]
                if devs:
                    info["device"] = str(devs[0]["type"])
                    info["gpu"] = ", ".join(sorted(set(str(d["name"]) for d in devs)))
                    info["source"] = "实测：批次中途切到 GPU 且启用设备 = %s" % info["gpu"]
                else:
                    info["device"], info["gpu"] = None, None
                    info["source"] = "拿不到：批次中途切到 GPU，但设备表里没有启用的非 CPU 设备 → 会静默回落 CPU"
        return info
    return info
