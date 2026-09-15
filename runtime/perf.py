"""DSH Blender 渲染性能助手 —— 本会话所有优化的固化版（perf + 对象精简）。

通过 blender_rt_perf / blender_rt_opt 调用；API 挂在 K.dsh_perf_api。

包含：
  dsh_perf_analyze()   实测 sync / 每采样 GPU 成本（1 spp 与 4 spp 差分）
  dsh_perf_apply()     应用优化预设：persistent_data / 降噪器自动判定（OptiX↔OIDN）/ denoising_use_gpu / auto_tile off / 采样上限
  dsh_perf_revert()    还原到 apply 之前的值
  dsh_perf_status()    当前渲染设置 + 预设状态
  dsh_opt_analyze()    对象合并候选分析（预计节省多少 ms/轮）
  dsh_opt_join()       按安全规则执行合并（dry_run 默认 True）
"""
import bpy, json, time
from collections import defaultdict

PERF_VERSION = 1


def _j(o):
    return json.dumps(o, ensure_ascii=False, default=str)


def _kernel():
    import sys
    return sys.modules.get("dsh_rt_kernel")


def _sc():
    return bpy.context.scene


def _snap(sc):
    c = sc.cycles
    r = sc.render
    return {
        "persistent": r.use_persistent_data,
        "denoiser": c.denoiser,
        "denoising_use_gpu": getattr(c, "denoising_use_gpu", None),
        "auto_tile": c.use_auto_tile,
        "tile_size": c.tile_size,
        "samples": c.samples,
        "adaptive": c.use_adaptive_sampling,
        "adaptive_threshold": c.adaptive_threshold,
        "use_denoising": c.use_denoising,
        "resolution_percentage": r.resolution_percentage,
        "filepath": r.filepath,
        "engine": r.engine,
        "device": c.device,
    }


def _dsh_perf_status_cycles():
    sc = _sc()
    c = sc.cycles
    r = sc.render
    K = _kernel()
    saved = getattr(K, "dsh_perf_saved", None) if K else None
    return _j({
        "version": PERF_VERSION,
        "engine": r.engine,
        "device": c.device,
        "samples": c.samples,
        "adaptive": [c.use_adaptive_sampling, round(c.adaptive_threshold, 4)],
        "persistent_data": r.use_persistent_data,
        "denoiser": [c.denoiser, getattr(c, "denoising_use_gpu", None)],
        "auto_tile": [c.use_auto_tile, c.tile_size],
        "resolution": [r.resolution_x, r.resolution_y, r.resolution_percentage],
        "objects": len(bpy.context.view_layer.objects),
        "preset_applied": bool(getattr(K, "dsh_perf_applied", False)) if K else False,
        "saved_snapshot": saved,
    })


def _pick_denoiser():
    """分享版：默认按本机 GPU 情况自动选降噪器（N 卡 OptiX → 否则 OpenImageDenoise）。

    返回 (denoiser, denoise_gpu)；取不到偏好时给 (None, None) 表示"别动它的降噪设置"。
    """
    try:
        addon = bpy.context.preferences.addons.get("cycles")
        prefs = getattr(addon, "preferences", None) if addon else None
        dtype = str(getattr(prefs, "compute_device_type", "") or "").upper()
        if dtype == "OPTIX":
            return "OPTIX", True
        if dtype in ("CUDA", "HIP", "ONEAPI", "METAL"):
            return "OPENIMAGEDENOISE", True
        return "OPENIMAGEDENOISE", False
    except Exception:
        return None, None


def dsh_perf_apply(samples=1024, persistent=True, denoiser=None, denoise_gpu=None, auto_tile=False):
    _mode0 = _dsh_perf_engine_mode()
    if _mode0 == "eevee":
        _r0 = _dsh_perf_apply_eevee(locals().get("args"))
        if _r0 is not None:
            return _r0
    """应用优化预设。denoiser / denoise_gpu 缺省 = 按本机自动判定（见 _pick_denoiser）。"""
    if denoiser is None or str(denoiser).lower() in ("", "auto"):
        denoiser, auto_gpu = _pick_denoiser()
        if denoise_gpu is None:
            denoise_gpu = auto_gpu
    if denoise_gpu is None:
        denoise_gpu = True
    sc = _sc()
    c = sc.cycles
    r = sc.render
    K = _kernel()
    if K is None:
        return _j({"ok": False, "err": "需要持久内核 K（走 blender_rt_* 通道）"})
    if getattr(K, "dsh_perf_saved", None) is None:
        K.dsh_perf_saved = _snap(sc)
    notes = []
    try:
        r.use_persistent_data = bool(persistent)
    except Exception as e:
        notes.append("persistent: %s" % e)
    if denoiser:
        try:
            c.denoiser = denoiser
        except Exception as e:
            notes.append("denoiser: %s" % e)
    try:
        c.denoising_use_gpu = bool(denoise_gpu)
    except Exception as e:
        notes.append("denoise_gpu: %s" % e)
    try:
        c.use_auto_tile = bool(auto_tile)
    except Exception as e:
        notes.append("auto_tile: %s" % e)
    if samples:
        c.samples = int(samples)
    K.dsh_perf_applied = True
    out = json.loads(dsh_perf_status())
    out["ok"] = True
    out["notes"] = notes
    return _j(out)


def dsh_perf_revert():
    sc = _sc()
    c = sc.cycles
    r = sc.render
    K = _kernel()
    saved = getattr(K, "dsh_perf_saved", None) if K else None
    if not saved:
        return _j({"ok": False, "err": "没有保存过的快照（未 apply 过）"})
    r.use_persistent_data = saved["persistent"]
    c.denoiser = saved["denoiser"]
    try:
        c.denoising_use_gpu = saved["denoising_use_gpu"]
    except Exception:
        pass
    c.use_auto_tile = saved["auto_tile"]
    c.tile_size = saved["tile_size"]
    c.samples = saved["samples"]
    c.use_adaptive_sampling = saved["adaptive"]
    c.adaptive_threshold = saved["adaptive_threshold"]
    c.use_denoising = saved["use_denoising"]
    r.resolution_percentage = saved["resolution_percentage"]
    r.filepath = saved["filepath"]
    K.dsh_perf_applied = False
    out = json.loads(dsh_perf_status())
    out["ok"] = True
    out["reverted"] = True
    return _j(out)


def dsh_perf_analyze(pct=25, low=1, high=4, persistent_test=True):
    """差分测速：t(1) 与 t(4) → per_sample ≈ (t4-t1)/3、sync ≈ t1 - per_sample。"""
    sc = _sc()
    c = sc.cycles
    r = sc.render
    snap = _snap(sc)
    steps = []
    try:
        r.resolution_percentage = int(pct)
        c.use_denoising = False
        c.use_adaptive_sampling = False
        def shot(n, persist, tag):
            c.samples = n
            r.use_persistent_data = persist
            t0 = time.perf_counter()
            bpy.ops.render.render(write_still=False)
            steps.append({"tag": tag, "samples": n, "persistent": persist, "seconds": round(time.perf_counter() - t0, 3)})
            return steps[-1]["seconds"]
        t1 = shot(low, False, "cold_%dspp" % low)
        th = shot(high, False, "warm_%dspp" % high)
        per_sample = (th - t1) / max(1, high - low)
        sync_est = t1 - per_sample
        warm_persist = None
        if persistent_test:
            shot(low, True, "persist_first")
            warm_persist = shot(high, True, "persist_warm_%dspp" % high)
        rec = []
        if sync_est > 1.0:
            rec.append("CPU 侧每轮同步约 %.1f s → 打开 persistent_data 可免除（apply 会做）" % sync_est)
        if per_sample > 0.05:
            rec.append("GPU 单采样约 %.3f s；1080p 全帧 ≈ 采样数 × 该值" % per_sample)
        if warm_persist is not None and th > 0:
            rec.append("persistent 后同采样数用时 %.3f s（对比 %.3f s）" % (warm_persist, th))
        obj = len(bpy.context.view_layer.objects)
        rec.append("当前对象数 %d；每 1000 对象约贡献 %.2f s 同步（按 1.4 ms/对象估）" % (obj, 1.4 * obj / 1000.0))
        return _j({"ok": True, "steps": steps, "per_sample_s": round(per_sample, 4),
                   "sync_est_s": round(sync_est, 3), "objects": obj, "recommendations": rec})
    except BaseException as e:
        import traceback
        return _j({"ok": False, "err": str(e), "trace": traceback.format_exc(limit=3), "steps": steps})
    finally:
        r.resolution_percentage = snap["resolution_percentage"]
        c.samples = snap["samples"]
        c.use_denoising = snap["use_denoising"]
        c.use_adaptive_sampling = snap["adaptive"]
        r.use_persistent_data = snap["persistent"]
        r.filepath = snap["filepath"]


def _join_key(o):
    mats = tuple(m.name for m in o.data.materials)
    coll = o.users_collection[0].name if o.users_collection else ""
    return (coll, mats, o.parent.name if o.parent else None, len(o.data.uv_layers))


def _join_candidates():
    vl = bpy.context.view_layer
    groups = defaultdict(list)
    for o in vl.objects:
        if o.type != "MESH":
            continue
        if (o.modifiers or o.animation_data or o.data.shape_keys or len(o.keys())
                or o.instance_type != "NONE" or o.instance_collection or o.library):
            continue
        groups[_join_key(o)].append(o)
    return [v for v in groups.values() if len(v) >= 2]


def dsh_opt_analyze():
    vl = bpy.context.view_layer
    cand = _join_candidates()
    total = len(vl.objects)
    joinable = sum(len(v) for v in cand)
    top = sorted([[len(v), v[0].users_collection[0].name if v[0].users_collection else "",
                   (v[0].data.materials[0].name if v[0].data.materials else ""), v[0].name[:28]] for v in cand],
                 reverse=True)[:10]
    return _j({"ok": True, "objects": total, "groups": len(cand), "joinable": joinable,
               "objects_after": total - joinable + len(cand),
               "est_sync_save_s": round(max(0, joinable - len(cand)) * 1.4 / 1000.0, 3),
               "top_groups": top})


def dsh_opt_join(dry_run=True, save_before=None):
    K = _kernel()
    vl = bpy.context.view_layer
    cand = _join_candidates()
    faces_before = sum(len(o.data.polygons) for o in vl.objects if o.type == "MESH")
    verts_before = sum(len(o.data.vertices) for o in vl.objects if o.type == "MESH")
    mats_before = len(bpy.data.materials)
    before_objects = len(vl.objects)
    if dry_run:
        return _j({"ok": True, "dry_run": True, "objects_before": before_objects,
                   "groups": len(cand), "joinable": sum(len(v) for v in cand)})
    saved_path = None
    if save_before:
        try:
            bpy.ops.wm.save_as_mainfile(filepath=save_before, copy=True)
            saved_path = save_before
        except BaseException as e:
            return _j({"ok": False, "err": "保存回退点失败: %s" % e})
    if bpy.context.mode != "OBJECT":
        try:
            bpy.ops.object.mode_set(mode="OBJECT")
        except Exception:
            pass
    joins = []
    fails = []
    for objs in cand:
        target = objs[0]
        try:
            for o in vl.objects:
                o.select_set(False)
            for o in objs:
                o.select_set(True)
            vl.objects.active = target
            with bpy.context.temp_override(active_object=target, selected_editable_objects=list(objs),
                                           selected_objects=list(objs)):
                bpy.ops.object.join()
            prefix = target.name.split("_")[0]
            mat = target.data.materials[0].name.split("_", 2)[-1][:18] if target.data.materials else "nom"
            target.name = (prefix + "_MERGED_" + mat)[:60]
            target.data.name = target.name
            joins.append([target.name, len(objs)])
        except BaseException as e:
            fails.append([target.name, str(e)[:80]])
    faces_after = sum(len(o.data.polygons) for o in vl.objects if o.type == "MESH")
    verts_after = sum(len(o.data.vertices) for o in vl.objects if o.type == "MESH")
    return _j({"ok": True, "dry_run": False, "saved_before": saved_path,
               "objects": [before_objects, len(vl.objects)],
               "faces": [faces_before, faces_after], "verts": [verts_before, verts_after],
               "materials": [mats_before, len(bpy.data.materials)],
               "groups_joined": len(joins), "failures": fails[:5], "sample": joins[:8]})


def dsh_perf_help():
    return _j({
        "version": PERF_VERSION,
        "ops": {
            "status": "当前渲染设置 + 预设状态 + 保存的快照",
            "analyze": "差分测速（sync / per-sample），参数 pct=25 low=1 high=4",
            "apply": "应用优化预设（persistent_data / 降噪器自动判定（OptiX↔OIDN）/ denoising_use_gpu / auto_tile off / 采样上限 samples=1024）",
            "revert": "还原到 apply 之前",
        },
        "opt_ops": {
            "opt_analyze": "对象合并候选分析（同集合/同材质/同父级/无修改器动画形态键自定义属性实例）",
            "opt_join": "执行合并（dry_run=True 只分析；save_before=<路径> 先存回退点）",
        },
        "notes": "analyze/opt_join 会占用 Blender 主线程；join 会改变场景结构，务必先 save_before",
    })


import sys as _sys
_K = _sys.modules.get("dsh_rt_kernel")
if _K is not None:
    _K.dsh_perf_api = {"version": PERF_VERSION, "status": (lambda *a, **kw: dsh_perf_status(*a, **kw)), "apply": dsh_perf_apply,
                       "revert": dsh_perf_revert, "analyze": dsh_perf_analyze, "help": dsh_perf_help,
                       "opt_analyze": dsh_opt_analyze, "opt_join": dsh_opt_join}


def _dsh_perf_engine_mode():
    sc = bpy.context.scene
    e = str(sc.render.engine)
    if "EEVEE" in e:
        return "eevee"
    if "CYCLES" in e:
        return "cycles"
    return "other"


def dsh_perf_status(*a, **kw):
    """v0.8.0：在 Cycles 状态之上补引擎信息（EEVEE 光追 / 采样 / 阴影）"""
    import json as _json
    raw = _dsh_perf_status_cycles(*a, **kw)
    try:
        d = _json.loads(raw)
    except Exception:
        return raw
    sc = bpy.context.scene
    ee = getattr(sc, "eevee", None)
    d["engine"] = sc.render.engine
    d["engine_mode"] = _dsh_perf_engine_mode()
    if ee is not None:
        d["eevee"] = {"use_raytracing": getattr(ee, "use_raytracing", None),
                      "ray_tracing_method": str(getattr(ee, "ray_tracing_method", "")),
                      "taa_render_samples": getattr(ee, "taa_render_samples", None),
                      "use_shadows": getattr(ee, "use_shadows", None),
                      "shadow_ray_count": getattr(ee, "shadow_ray_count", None),
                      "shadow_step_count": getattr(ee, "shadow_step_count", None)}
    return _json.dumps(d, ensure_ascii=False)


def _dsh_perf_apply_eevee(args=None):
    """EEVEE 预设：开光追 + 采样/阴影质量；Cycles 专属项**显式跳过**（不再静默套用）"""
    import json as _json
    sc = bpy.context.scene
    ee = getattr(sc, "eevee", None)
    if ee is None:
        return None
    before = {"engine": sc.render.engine, "rt": getattr(ee, "use_raytracing", None),
              "samples": getattr(ee, "taa_render_samples", None)}
    applied = []
    try:
        sc.render.engine = "BLENDER_EEVEE"
        if hasattr(ee, "use_raytracing"):
            ee.use_raytracing = True
            applied.append("use_raytracing=True")
        if hasattr(ee, "ray_tracing_method"):
            try:
                ee.ray_tracing_method = "SCREEN"
                applied.append("ray_tracing_method=SCREEN")
            except Exception:
                pass
        if hasattr(ee, "use_shadows"):
            ee.use_shadows = True
            applied.append("use_shadows=True")
        for attr, val in (("shadow_ray_count", 2), ("shadow_step_count", 8)):
            if hasattr(ee, attr):
                try:
                    setattr(ee, attr, val)
                    applied.append("%s=%s" % (attr, val))
                except Exception:
                    pass
        cur = getattr(ee, "taa_render_samples", 64) or 64
        if hasattr(ee, "taa_render_samples") and cur < 64:
            ee.taa_render_samples = 64
            applied.append("taa_render_samples=64")
    except Exception as e:
        return _json.dumps({"ok": False, "error": "%s: %s" % (type(e).__name__, e)}, ensure_ascii=False)
    after = {"engine": sc.render.engine, "rt": getattr(ee, "use_raytracing", None),
             "ray_tracing_method": str(getattr(ee, "ray_tracing_method", "")),
             "samples": getattr(ee, "taa_render_samples", None),
             "use_shadows": getattr(ee, "use_shadows", None)}
    return _json.dumps({"ok": True, "preset": "eevee-rt", "engine": sc.render.engine,
                        "before": before, "after": after, "applied": applied,
                        "skipped_cycles_only": ["persistent_data", "denoising_use_gpu(OptiX)",
                                                "auto_tile off", "samples cap"],
                        "note": "当前引擎是 EEVEE：Cycles 专属项不适用，已显式跳过"}, ensure_ascii=False)
