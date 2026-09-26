# -*- coding: utf-8 -*-
"""DSH 材质节点图（v0.9.6 · 上游整合 A6）—— 把"每次手写节点连线"变成一次 op 调用。

实测动机（工作区 7,182 个脚本）：ShaderNode/nodes.new 命中 **2,732 次 / 204 个文件** ——
每个 build 脚本都各自写一遍 TexCoord → Mapping → Noise → ColorRamp → Principled 的连线，
既重复又容易猜错 socket 名。另外 OBJ/MTL 交付会丢掉程序化节点（只有贴图能带走），
所以这里同时提供 **material_bake**：把程序化材质烘成 PNG 贴图（Cycles bake：DIFFUSE/
ROUGHNESS/NORMAL/AO/EMIT/COMBINED），再接 deliver_export 就能把材质一起交付。

ops：
    blender_rt_plan(op="material_scan",  args={objects:["P01"]})                      # 只读
    blender_rt_plan(op="material_build", args={name:"M-hull", preset:"metal_brushed",
                                              params:{base_color:[0.35,0.38,0.42], scale:24}})
    blender_rt_plan(op="material_apply", args={material:"M-hull", objects:["P01","P02"]})
    blender_rt_plan(op="material_bake",  args={objects:["P01"], bake_type:"DIFFUSE",
                                              resolution:1024, outdir:"D:/DSH/blender/out/bake"})
    blender_rt_plan(op="material_selftest")

**硬规则**：preset 名写错 ⇒ 回允许列表；参数名写错 ⇒ 回"不认识的参数"；bake 前没有 UV ⇒ ok=false
并指路 uv_smart_project；bake 走 Cycles（会临时切引擎，结束还原）。
"""
import json
import math
import os
import time

import bpy

MAT_VERSION = 1
PRESETS = ("metal_brushed", "metal_paint", "rust", "plastic", "glass", "fabric", "wood",
           "concrete", "emission", "hologram", "unlit")
BAKE_TYPES = ("DIFFUSE", "ROUGHNESS", "NORMAL", "AO", "EMIT", "COMBINED")


def _j(o):
    return json.dumps(o, ensure_ascii=False, default=str)


def _num(v, name, lo=None, hi=None, default=None):
    if v is None:
        if default is None:
            raise ValueError("%s 必填" % name)
        return float(default)
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        raise ValueError("%s 需要数字，收到 %r" % (name, v))
    f = float(v)
    if not math.isfinite(f):
        raise ValueError("%s 必须是有限数（NaN/Inf 会写坏 .blend）" % name)
    if lo is not None and f < lo:
        raise ValueError("%s=%g 小于下限 %g" % (name, f, lo))
    if hi is not None and f > hi:
        raise ValueError("%s=%g 大于上限 %g" % (name, f, hi))
    return f


def _objs(objects=None, scope="ACTIVE"):
    if objects:
        names = [objects] if isinstance(objects, str) else list(objects)
        out = []
        for n in names:
            ob = bpy.data.objects.get(str(n))
            if ob is None:
                raise ValueError("对象不存在：%s" % n)
            out.append(ob)
        if not out:
            raise ValueError("objects 为空")
        return out
    sc = str(scope or "ACTIVE").upper()
    if sc == "ACTIVE":
        ob = bpy.context.view_layer.objects.active
        if ob is None:
            raise ValueError("没有活动对象（给 objects=[...] 或 scope=SELECTED/VISIBLE/ALL）")
        return [ob]
    if sc == "SELECTED":
        sel = list(bpy.context.selected_objects)
        if not sel:
            raise ValueError("没有选中对象")
        return sel
    if sc == "VISIBLE":
        vis = [o for o in bpy.context.view_layer.objects if o.visible_get()]
        if not vis:
            raise ValueError("没有可见对象")
        return vis
    if sc == "ALL":
        allm = list(bpy.data.objects)
        if not allm:
            raise ValueError("场景里没有对象")
        return allm
    raise ValueError("未知 scope：%s" % scope)


def _mat(name):
    m = bpy.data.materials.get(str(name))
    if m is None:
        raise ValueError("材质不存在：%s" % name)
    return m


# ---------------------------------------------------------------- 节点图构建

def _new_tree(m):
    m.use_nodes = True
    nt = m.node_tree
    nt.nodes.clear()
    return nt


def _add(nt, kind, loc, label=None, **props):
    n = nt.nodes.new(kind)
    n.location = loc
    if label:
        n.label = label
    for k, v in props.items():
        try:
            setattr(n, k, v)
        except Exception:
            try:
                n.inputs[k].default_value = v
            except Exception:
                pass
    return n


def _link(nt, a, a_sock, b, b_sock):
    try:
        return nt.links.new(a.outputs[a_sock], b.inputs[b_sock])
    except Exception as e:
        raise ValueError("连线失败 %s.%s → %s.%s：%s" % (a.name, a_sock, b.name, b_sock, str(e)[:80]))


def _rgba(v, name, default):
    if v is None:
        return default
    if not isinstance(v, (list, tuple)) or len(v) not in (3, 4):
        raise ValueError("%s 需要 [r,g,b] 或 [r,g,b,a]，收到 %r" % (name, v))
    out = [_num(x, name + "[%d]" % i, 0.0, 1000.0) for i, x in enumerate(v)]
    if len(out) == 3:
        out.append(1.0)
    return out


def _p(params, key, default=None):
    return (params or {}).get(key, default)


def _build_preset(nt, preset, params):
    """每个 preset 显式连线（数据驱动容易错，显式更稳）。返回 {socket 名: 节点} 供回执描述。"""
    P = params or {}
    base = _rgba(_p(P, "base_color"), "base_color", [0.8, 0.8, 0.8, 1.0])
    out = _add(nt, "ShaderNodeOutputMaterial", (520, 0))
    if preset == "unlit":
        bsdf = _add(nt, "ShaderNodeEmission", (300, 0), color=base, strength=_num(_p(P, "strength"), "strength", 0, 100, 1.0))
        _link(nt, bsdf, "Emission", out, "Surface")
        return {"bsdf": bsdf, "out": out}
    bsdf = _add(nt, "ShaderNodeBsdfPrincipled", (300, 0))
    bsdf.inputs["Base Color"].default_value = base
    metal = _num(_p(P, "metallic"), "metallic", 0.0, 1.0, 0.0)
    rough = _num(_p(P, "roughness"), "roughness", 0.0, 1.0, 0.5)
    bsdf.inputs["Metallic"].default_value = metal
    bsdf.inputs["Roughness"].default_value = rough
    if "IOR" in bsdf.inputs:
        bsdf.inputs["IOR"].default_value = _num(_p(P, "ior"), "ior", 1.0, 4.0, 1.45)
    _link(nt, bsdf, "BSDF", out, "Surface")

    def texture_chain(scale_default, detail=6.0, rough_noise=0.5):
        tc = _add(nt, "ShaderNodeTexCoord", (-820, 0))
        mp = _add(nt, "ShaderNodeMapping", (-620, 0))
        nz = _add(nt, "ShaderNodeTexNoise", (-420, 0), scale=_num(_p(P, "scale"), "scale", 0.01, 1000, scale_default),
                  detail=detail, roughness=rough_noise)
        _link(nt, tc, "Object", mp, "Vector")
        _link(nt, mp, "Vector", nz, "Vector")
        return tc, mp, nz

    def ramp(nz, stops, loc=(-220, 0)):
        r = _add(nt, "ShaderNodeValToRGB", loc)
        el = r.color_ramp.elements
        while len(el) > 1:
            el.remove(el[-1])
        el[0].position = stops[0][0]
        el[0].color = stops[0][1]
        for pos, col in stops[1:]:
            e = el.new(pos)
            e.color = col
        _link(nt, nz, "Fac", r, "Fac")
        return r

    if preset == "metal_brushed":
        tc, mp, nz = texture_chain(800.0, detail=2.0, rough_noise=0.35)
        mp.inputs["Scale"].default_value = (1.0, 1.0, 0.02)   # 拉长 → 拉丝
        rr = ramp(nz, [(0.35, (0.0, 0.0, 0.0, 1.0)), (0.65, (1.0, 1.0, 1.0, 1.0))], (-220, -260))
        mix = _add(nt, "ShaderNodeMix", (60, -120), data_type="RGBA", blend_type="MIX")
        mix.inputs[0].default_value = _num(_p(P, "variation"), "variation", 0.0, 1.0, 0.25)
        mix.inputs[6].default_value = base
        mix.inputs[7].default_value = [base[0] * 0.75, base[1] * 0.75, base[2] * 0.75, 1.0]
        _link(nt, rr, "Color", mix, "Factor")
        _link(nt, mix, "Result", bsdf, "Base Color")
        _link(nt, rr, "Color", bsdf, "Roughness")
        bsdf.inputs["Metallic"].default_value = 1.0
        return {"bsdf": bsdf, "out": out, "noise": nz, "ramp": rr, "chain": [tc, mp, nz]}
    if preset in ("metal_paint", "plastic"):
        tc, mp, nz = texture_chain(180.0, detail=4.0, rough_noise=0.4)
        nz.inputs["Detail"].default_value = 3.0
        rr = ramp(nz, [(0.45, (0.0, 0.0, 0.0, 1.0)), (0.55, (0.06, 0.06, 0.06, 1.0))], (-220, -260))
        bump = _add(nt, "ShaderNodeBump", (60, -300), Strength=_num(_p(P, "bump"), "bump", 0.0, 1.0, 0.08))
        _link(nt, rr, "Color", bump, "Height")
        _link(nt, bump, "Normal", bsdf, "Normal")
        bsdf.inputs["Roughness"].default_value = rough
        bsdf.inputs["Metallic"].default_value = metal
        return {"bsdf": bsdf, "out": out, "noise": nz, "bump": bump, "chain": [tc, mp, nz]}
    if preset == "rust":
        tc, mp, nz = texture_chain(24.0, detail=10.0, rough_noise=0.7)
        rr = ramp(nz, [(0.30, [0.16, 0.10, 0.07, 1.0]), (0.55, [0.42, 0.20, 0.08, 1.0]), (0.80, [0.72, 0.36, 0.14, 1.0])], (-220, -60))
        bump = _add(nt, "ShaderNodeBump", (60, -320), Strength=_num(_p(P, "bump"), "bump", 0.0, 1.0, 0.55))
        _link(nt, rr, "Color", bsdf, "Base Color")
        _link(nt, rr, "Color", bump, "Height")
        _link(nt, bump, "Normal", bsdf, "Normal")
        bsdf.inputs["Metallic"].default_value = _num(_p(P, "metallic"), "metallic", 0.0, 1.0, 0.35)
        bsdf.inputs["Roughness"].default_value = _num(_p(P, "roughness"), "roughness", 0.0, 1.0, 0.85)
        return {"bsdf": bsdf, "out": out, "noise": nz, "ramp": rr, "bump": bump, "chain": [tc, mp, nz]}
    if preset == "glass":
        bsdf.inputs["Base Color"].default_value = base
        bsdf.inputs["Roughness"].default_value = rough
        bsdf.inputs["Metallic"].default_value = 0.0
        try:
            bsdf.inputs["Transmission Weight"].default_value = _num(_p(P, "transmission"), "transmission", 0.0, 1.0, 1.0)
        except Exception:
            pass
        m = _mat_tree_get(nt)
        return {"bsdf": bsdf, "out": out}
    if preset == "fabric":
        tc, mp, nz = texture_chain(400.0, detail=6.0, rough_noise=0.6)
        mp.inputs["Scale"].default_value = (1.0, 1.0, 1.0)
        rr = ramp(nz, [(0.42, (0.0, 0.0, 0.0, 1.0)), (0.58, (0.35, 0.35, 0.35, 1.0))], (-220, -260))
        bump = _add(nt, "ShaderNodeBump", (60, -320), Strength=_num(_p(P, "bump"), "bump", 0.0, 1.0, 0.6))
        _link(nt, rr, "Color", bump, "Height")
        _link(nt, bump, "Normal", bsdf, "Normal")
        bsdf.inputs["Roughness"].default_value = _num(_p(P, "roughness"), "roughness", 0.0, 1.0, 0.9)
        bsdf.inputs["Sheen Weight"].default_value = 0.4 if "Sheen Weight" in bsdf.inputs else 0.0
        return {"bsdf": bsdf, "out": out, "noise": nz, "bump": bump, "chain": [tc, mp, nz]}
    if preset == "wood":
        tc, mp, nz = texture_chain(6.0, detail=8.0, rough_noise=0.55)
        mp.inputs["Scale"].default_value = (1.0, 8.0, 1.0)
        rr = ramp(nz, [(0.25, [0.22, 0.13, 0.06, 1.0]), (0.55, [0.42, 0.26, 0.12, 1.0]), (0.85, [0.58, 0.38, 0.18, 1.0])], (-220, -60))
        bump = _add(nt, "ShaderNodeBump", (60, -320), Strength=_num(_p(P, "bump"), "bump", 0.0, 1.0, 0.25))
        _link(nt, rr, "Color", bsdf, "Base Color")
        _link(nt, rr, "Color", bump, "Height")
        _link(nt, bump, "Normal", bsdf, "Normal")
        bsdf.inputs["Roughness"].default_value = _num(_p(P, "roughness"), "roughness", 0.0, 1.0, 0.55)
        return {"bsdf": bsdf, "out": out, "noise": nz, "ramp": rr, "chain": [tc, mp, nz]}
    if preset == "concrete":
        tc, mp, nz = texture_chain(60.0, detail=8.0, rough_noise=0.8)
        nz2 = _add(nt, "ShaderNodeTexVoronoi", (-420, -300), scale=_num(_p(P, "scale"), "scale", 0.01, 1000, 12.0))
        _link(nt, mp, "Vector", nz2, "Vector")
        mix = _add(nt, "ShaderNodeMix", (-140, -160), data_type="RGBA", blend_type="MIX")
        mix.inputs[0].default_value = 0.45
        _link(nt, nz, "Color", mix, "A")
        _link(nt, nz2, "Color", mix, "B")
        mix2 = _add(nt, "ShaderNodeMix", (60, -160), data_type="RGBA", blend_type="MULTIPLY")
        mix2.inputs[0].default_value = 0.5
        mix2.inputs[7].default_value = [0.62, 0.62, 0.60, 1.0]
        _link(nt, mix, "Result", mix2, "A")
        _link(nt, mix2, "Result", bsdf, "Base Color")
        bsdf.inputs["Roughness"].default_value = _num(_p(P, "roughness"), "roughness", 0.0, 1.0, 0.9)
        return {"bsdf": bsdf, "out": out, "noise": nz, "chain": [tc, mp, nz, nz2]}
    if preset == "emission":
        em = _add(nt, "ShaderNodeEmission", (60, -60), color=_rgba(_p(P, "color"), "color", base),
                  strength=_num(_p(P, "strength"), "strength", 0.0, 100000.0, 12.0))
        mix = _add(nt, "ShaderNodeMixShader", (300, -60))
        mix.inputs[0].default_value = _num(_p(P, "mix"), "mix", 0.0, 1.0, 0.85)
        _link(nt, bsdf, "BSDF", mix, "Shader")
        _link(nt, em, "Emission", mix, "Shader")
        _link(nt, mix, "Shader", out, "Surface")
        return {"bsdf": bsdf, "emission": em, "mix": mix, "out": out}
    if preset == "hologram":
        em = _add(nt, "ShaderNodeEmission", (60, -60), color=_rgba(_p(P, "color"), "color", [0.3, 0.8, 1.0, 1.0]),
                  strength=_num(_p(P, "strength"), "strength", 0.0, 100000.0, 6.0))
        try:
            tr = _add(nt, "ShaderNodeBsdfTransparent", (60, 140))
            mix = _add(nt, "ShaderNodeMixShader", (300, -60))
            mix.inputs[0].default_value = _num(_p(P, "alpha"), "alpha", 0.0, 1.0, 0.6)
            _link(nt, tr, "BSDF", mix, "Shader")
            _link(nt, em, "Emission", mix, "Shader")
            _link(nt, mix, "Shader", out, "Surface")
            return {"emission": em, "transparent": tr, "mix": mix, "out": out}
        except Exception:
            _link(nt, em, "Emission", out, "Surface")
            return {"emission": em, "out": out}
    raise ValueError("未知 preset：%s（允许 %s）" % (preset, list(PRESETS)))


def _mat_tree_get(nt):
    return nt


# ---------------------------------------------------------------- ops

def material_scan(materials=None, objects=None, scope="ACTIVE"):
    """只读：材质节点图摘要（节点类型分布 / 贴图依赖 / 是否程序化 / 谁在用）。"""
    mset = []
    if materials:
        names = [materials] if isinstance(materials, str) else list(materials)
        mset = [_mat(n) for n in names]
    else:
        obs = _objs(objects, scope)
        seen = []
        for ob in obs:
            if ob.type != "MESH":
                continue
            for slot in ob.material_slots:
                if slot.material and slot.material not in seen:
                    seen.append(slot.material)
        mset = seen
        if not mset:
            raise ValueError("对象上没有材质（先 material_build/apply）")
    rows = []
    for m in mset:
        row = {"name": m.name, "users": m.users, "use_nodes": bool(m.use_nodes),
               "blend_method": getattr(m, "blend_method", None)}
        if not m.use_nodes or m.node_tree is None:
            rows.append(row)
            continue
        nt = m.node_tree
        hist = {}
        images = []
        has_principled = False
        procedural = False
        for n in nt.nodes:
            hist[n.bl_idname] = hist.get(n.bl_idname, 0) + 1
            if n.bl_idname == "ShaderNodeBsdfPrincipled":
                has_principled = True
            if n.bl_idname.startswith("ShaderNodeTex") and n.bl_idname != "ShaderNodeTexImage":
                procedural = True
            if n.bl_idname == "ShaderNodeTexImage" and getattr(n, "image", None) is not None:
                im = n.image
                images.append({"node": n.name, "image": im.name, "size": list(im.size),
                               "packed": bool(im.packed_file), "filepath": (im.filepath or "")[:160]})
        row.update({"nodes": len(nt.nodes), "links": len(nt.links), "node_types": hist,
                    "principled": has_principled, "procedural": procedural, "images": images,
                    "needs_uv": any(k in hist for k in ("ShaderNodeTexCoord", "ShaderNodeTexImage"))})
        rows.append(row)
    return _j({"ok": True, "count": len(rows), "materials": rows,
               "note": "procedural=true 的材质在 OBJ/MTL 交付里会丢节点 —— 用 material_bake 烘成贴图再交付"})


def material_build(name, preset="metal_paint", params=None, use_backface_culling=False, blend_method=None):
    """一次调用建程序化材质（节点 + 连线 + 布局），可重复执行（已存在则重建节点树）。"""
    n = str(name or "").strip()
    if not n:
        raise ValueError("name 必填")
    ps = str(preset or "").lower()
    if ps not in PRESETS:
        raise ValueError("未知 preset：%s（允许 %s）" % (preset, list(PRESETS)))
    m = bpy.data.materials.get(n) or bpy.data.materials.new(n)
    nt = _new_tree(m)
    t0 = time.time()
    info = _build_preset(nt, ps, params)
    m.use_backface_culling = bool(use_backface_culling)
    if blend_method:
        try:
            m.blend_method = str(blend_method).upper()
        except Exception:
            pass
    xmin = min((nd.location[0] for nd in nt.nodes), default=0.0)
    xmax = max((nd.location[0] for nd in nt.nodes), default=0.0)
    return _j({"ok": True, "material": m.name, "preset": ps, "nodes": len(nt.nodes), "links": len(nt.links),
               "params_used": params or {}, "x_span": [round(xmin, 1), round(xmax, 1)],
               "ms": int((time.time() - t0) * 1000),
               "note": "要交付 OBJ/MTL 就先 material_bake（程序化节点带不走）"})


def material_apply(material, objects=None, scope="ACTIVE", slot=0, replace=True):
    """把材质套到对象的槽位上（replace=true 全替换，false 追加新槽）。"""
    m = _mat(material)
    obs = _objs(objects, scope)
    out = []
    for ob in obs:
        if ob.type != "MESH":
            out.append({"name": ob.name, "ok": False, "error": "不是 mesh（%s）" % ob.type})
            continue
        try:
            prev = len(ob.material_slots)
            if replace or prev == 0:
                ob.data.materials.clear()
                ob.data.materials.append(m)
                mode = "replace"
            else:
                ob.data.materials.append(m)
                mode = "append"
            out.append({"name": ob.name, "ok": True, "mode": mode, "slots_before": prev,
                        "slots_after": len(ob.material_slots)})
        except Exception as e:
            out.append({"name": ob.name, "ok": False, "error": "%s: %s" % (type(e).__name__, str(e)[:120])})
    return _j({"ok": all(r.get("ok") for r in out), "material": m.name, "objects": out})


def material_bake(objects=None, scope="ACTIVE", bake_type="DIFFUSE", resolution=1024, samples=16,
                  margin=8, outdir=None, prefix=None, save=True, use_clear=True, set_active=True):
    """把材质烘成贴图（Cycles）。**这是"程序化材质 → 可交付纹理"的桥**：烘完接 deliver_export。

    硬规则：没有 UV ⇒ ok=false 并指路 uv_smart_project；bake_type 不在允许列表 ⇒ 回列表；
    结束把渲染引擎还原成原来的（不偷改用户设置）。
    """
    bt = str(bake_type or "DIFFUSE").upper()
    if bt not in BAKE_TYPES:
        raise ValueError("未知 bake_type：%s（允许 %s）" % (bake_type, list(BAKE_TYPES)))
    res = int(_num(resolution, "resolution", 16, 8192))
    smp = int(_num(samples, "samples", 1, 4096))
    mg = int(_num(margin, "margin", 0, 64))
    obs = _objs(objects, scope)
    obs = [o for o in obs if o.type == "MESH"]
    if not obs:
        raise ValueError("没有 mesh 对象可烘")
    sc = bpy.context.scene
    prev_engine = sc.render.engine
    prev_bake = getattr(sc.cycles, "bake_type", None) if hasattr(sc, "cycles") else None
    try:
        sc.render.engine = "CYCLES"
        if hasattr(sc, "cycles"):
            sc.cycles.bake_type = bt
            sc.cycles.samples = smp
            if hasattr(sc.cycles, "use_denoising"):
                sc.cycles.use_denoising = True
        if hasattr(sc.render, "bake"):
            sc.render.bake.margin = mg
            sc.render.bake.use_clear = bool(use_clear)
            for attr, val in (("use_pass_direct", bt == "COMBINED"), ("use_pass_indirect", False),
                              ("use_pass_color", True)):
                if hasattr(sc.render.bake, attr):
                    setattr(sc.render.bake, attr, val)
    except Exception as e:
        return _j({"ok": False, "error": "切 Cycles 失败：%s" % str(e)[:160]})
    out = []
    try:
        for ob in obs:
            rec = {"name": ob.name, "bake_type": bt, "resolution": [res, res]}
            if not ob.data.uv_layers:
                rec.update({"ok": False, "error": "没有 UV",
                            "hint": "先 blender_rt_plan(op=\"uv_smart_project\", args={objects:[\"%s\"]})" % ob.name})
                out.append(rec)
                continue
            if not ob.data.materials:
                rec.update({"ok": False, "error": "没有材质",
                            "hint": "先 material_build + material_apply"})
                out.append(rec)
                continue
            for o in bpy.context.selected_objects:
                o.select_set(False)
            ob.select_set(True)
            bpy.context.view_layer.objects.active = ob
            t0 = time.time()
            imgs = []
            for slot in ob.material_slots:
                m = slot.material
                if m is None or not m.use_nodes:
                    continue
                nt = m.node_tree
                iname = "%s_%s_%s" % (prefix or ob.name, m.name, bt)
                img = bpy.data.images.get(iname)
                if img is None or tuple(img.size) != (res, res):
                    if img is not None:
                        bpy.data.images.remove(img)
                    img = bpy.data.images.new(iname, res, res, alpha=False, float_buffer=False)
                node = None
                for nd in nt.nodes:
                    if nd.bl_idname == "ShaderNodeTexImage" and getattr(nd, "image", None) is img:
                        node = nd
                        break
                if node is None:
                    node = nt.nodes.new("ShaderNodeTexImage")
                    node.image = img
                    node.location = (bsdf_x(nt), -520)
                for nd in nt.nodes:
                    nd.select = False
                node.select = True
                nt.nodes.active = node
                try:
                    bpy.ops.object.bake(type=bt, use_clear=bool(use_clear), margin=mg)
                except Exception as e:
                    imgs.append({"material": m.name, "ok": False, "error": "%s: %s" % (type(e).__name__, str(e)[:140])})
                    continue
                path = None
                if save:
                    d = str(outdir).rstrip("/\\") if outdir else os.path.join(os.path.expanduser("~"), "dsh_bake")
                    try:
                        os.makedirs(d, exist_ok=True)
                    except Exception:
                        pass
                    path = os.path.join(d, iname + ".png")
                    try:
                        img.filepath_raw = path
                        img.file_format = "PNG"
                        img.save()
                    except Exception as e:
                        path = None
                        imgs.append({"material": m.name, "ok": False, "error": "存盘失败：%s" % str(e)[:120]})
                        continue
                imgs.append({"material": m.name, "image": img.name, "ok": True, "path": path,
                             "bytes": (os.path.getsize(path) if path and os.path.exists(path) else None)})
            rec.update({"ok": all(i.get("ok") for i in imgs) and bool(imgs), "images": imgs,
                        "ms": int((time.time() - t0) * 1000)})
            out.append(rec)
    finally:
        try:
            sc.render.engine = prev_engine
            if prev_bake is not None and hasattr(sc, "cycles"):
                sc.cycles.bake_type = prev_bake
        except Exception:
            pass
    return _j({"ok": all(r.get("ok") for r in out), "objects": out,
               "note": "烘出的 PNG 是 UV 展开后的贴图：交付时用 deliver_export（OBJ+MTL+贴图一起走）；"
                       "程序化节点仍留在 .blend 里，两者并存"})


def bsdf_x(nt):
    for nd in nt.nodes:
        if nd.bl_idname == "ShaderNodeBsdfPrincipled":
            return nd.location[0]
    return 0.0


def material_selftest():
    """自检：建材质 → 套到临时立方体（带 UV）→ scan 断言 → 烘 128px AO → 断言 PNG 落盘 → 删净。"""
    import bmesh
    ev = {}
    made = []
    try:
        me = bpy.data.meshes.new("__dsh_mat_selftest")
        ob = bpy.data.objects.new("__dsh_mat_selftest", me)
        bpy.context.scene.collection.objects.link(ob)
        made.append(ob.name)
        bm = bmesh.new()
        bmesh.ops.create_cube(bm, size=1.0)
        bm.to_mesh(me)
        bm.free()
        bpy.context.view_layer.objects.active = ob
        ob.select_set(True)
        bpy.ops.object.mode_set(mode="EDIT")
        bpy.ops.mesh.select_all(action="SELECT")
        bpy.ops.uv.smart_project(angle_limit=1.15, island_margin=0.02)
        bpy.ops.object.mode_set(mode="OBJECT")
        ev["uv_layers"] = [l.name for l in me.uv_layers]
        r = json.loads(material_build(name="__dsh_mat_selftest_M", preset="metal_brushed",
                                      params={"base_color": [0.4, 0.42, 0.45], "scale": 200}))
        ev["build"] = {"ok": r.get("ok"), "nodes": r.get("nodes"), "links": r.get("links")}
        a = json.loads(material_apply(material="__dsh_mat_selftest_M", objects=[ob.name]))
        ev["apply"] = a.get("ok")
        s = json.loads(material_scan(materials=["__dsh_mat_selftest_M"]))
        row = s["materials"][0]
        ev["scan"] = {"nodes": row.get("nodes"), "procedural": row.get("procedural"),
                      "principled": row.get("principled"), "needs_uv": row.get("needs_uv")}
        b = json.loads(material_bake(objects=[ob.name], bake_type="AO", resolution=128, samples=4,
                                     outdir=os.path.join(os.path.expanduser("~"), "dsh_bake_selftest")))
        img = (b.get("objects") or [{}])[0].get("images") or [{}]
        ev["bake"] = {"ok": b.get("ok"), "path": img[0].get("path"), "bytes": img[0].get("bytes")}
        ok = (ev["build"]["ok"] and ev["build"]["nodes"] >= 5 and ev["apply"]
              and ev["scan"]["procedural"] and ev["scan"]["principled"]
              and bool(ev["bake"]["ok"]) and bool(ev["bake"]["bytes"]))
        return _j({"ok": bool(ok), "evidence": ev})
    except Exception as e:
        return _j({"ok": False, "evidence": ev, "error": "%s: %s" % (type(e).__name__, str(e)[:220])})
    finally:
        for nm in made:
            o = bpy.data.objects.get(nm)
            if o is not None:
                m = o.data
                bpy.data.objects.remove(o, do_unlink=True)
                try:
                    bpy.data.meshes.remove(m, do_unlink=True)
                except Exception:
                    pass
        mm = bpy.data.materials.get("__dsh_mat_selftest_M")
        if mm is not None:
            bpy.data.materials.remove(mm, do_unlink=True)
        # 自检烘出来的图像 datablock 也要清（否则 bpy.data.images 里留孤儿）
        for im in [i for i in bpy.data.images if i.name.startswith("__dsh_mat_selftest")]:
            try:
                bpy.data.images.remove(im, do_unlink=True)
            except Exception:
                pass


def material_help():
    return _j({
        "module": "material.py", "version": MAT_VERSION,
        "ops": {
            "material_scan": "只读：节点数/类型分布/贴图依赖/是否程序化/是否需要 UV/被谁用",
            "material_build": "name + preset + params{base_color, metallic, roughness, scale, bump, strength, color, mix, alpha, variation, ior, transmission}（已存在则重建节点树）",
            "material_apply": "material + objects/scope；slot/replace（true=清空槽后套，false=追加）",
            "material_bake": "objects + bake_type=DIFFUSE|ROUGHNESS|NORMAL|AO|EMIT|COMBINED + resolution + samples + outdir + prefix（Cycles；结束还原引擎）",
            "material_selftest": "自检：建→套→scan→烘 128px AO→断言 PNG 落盘→删净",
            "material_help": "本表",
        },
        "presets": list(PRESETS),
        "why": "工作区实测 2,732 次节点连线 / 204 个脚本在重复造轮子；OBJ/MTL 交付会丢程序化节点 → 配 material_bake",
        "workflow": ["material_build(preset=...) 建材质", "material_apply 套到分件",
                     "material_scan 复核（procedural / needs_uv）",
                     "uv_smart_project 展 UV（bake 前置）", "material_bake 烘 AO/DIFFUSE 等贴图",
                     "deliver_export 连贴图一起交付"],
        "limits": ["bake 需要 UV（没有会明确报错并指路）", "bake 走 Cycles：会临时切引擎，结束还原",
                   "玻璃/透射类 preset 在 EEVEE 里表现与 Cycles 不同，验收以渲染 harness 的出图为准"],
    })


def material_dispatch(op, args_json):
    args = {}
    if isinstance(args_json, str) and args_json.strip():
        try:
            args = json.loads(args_json)
        except Exception as e:
            return _j({"ok": False, "error": "args 不是合法 JSON: %s" % str(e)[:120]})
    if not isinstance(args, dict):
        return _j({"ok": False, "error": "args 需要对象"})
    kw = {}
    for k, v in args.items():
        if k == "args" and isinstance(v, dict):
            kw.update(v)
        else:
            kw[k] = v
    ops = {"scan": material_scan, "build": material_build, "apply": material_apply,
           "bake": material_bake, "selftest": material_selftest, "help": material_help}
    fn = ops.get(str(op))
    if fn is None:
        return _j({"ok": False, "error": "unknown material op", "op": op, "ops": sorted(ops)})
    import inspect
    try:
        allowed = set(inspect.signature(fn).parameters)
        unknown = sorted(set(kw) - allowed)
        if unknown:
            return _j({"ok": False, "error": "不认识的参数 %s" % unknown, "op": op, "allowed": sorted(allowed)})
    except (TypeError, ValueError):
        pass
    try:
        return fn(**kw)
    except TypeError as e:
        return _j({"ok": False, "error": "参数不匹配: %s" % str(e)[:200], "op": op, "help": material_help()})
    except Exception as e:
        return _j({"ok": False, "error": "%s: %s" % (type(e).__name__, str(e)[:220]), "op": op})


class _DshApi(dict):
    def __call__(self, op=None, args=None, **kw):
        if op is None or isinstance(op, dict):
            args, op = (op if isinstance(op, dict) else args), "help"
        payload = args if isinstance(args, str) else _j(dict(args or {}, **kw))
        return json.loads(self["dispatch"](str(op), payload))

    def call(self, op, args=None, **kw):
        return self(op, args, **kw)


import sys as _sys
_K = _sys.modules.get("dsh_rt_kernel")
if _K is not None:
    _K.dsh_material_api = _DshApi({"version": MAT_VERSION, "dispatch": material_dispatch,
                                   "scan": material_scan, "build": material_build, "apply": material_apply,
                                   "bake": material_bake, "selftest": material_selftest, "help": material_help})
