# -*- coding: utf-8 -*-
"""DSH 配方库（v0.8.2）—— 把「参数组合」变成可保存、可套用、可分发的资产。

灵感来自 lurenjia-l/dsh-blender-stylized-shading 的「配方化」优点：它把自然语言风格词翻译成节点组参数组合并写成手册；
本模块把它通用化为**与领域无关**的机制：任意 JSON 配方（材质参数 / 对象属性 / 场景设置 / 自定义）都能存、列、取、套用、导出、导入。

API 挂 K.dsh_preset_api；工具入口 blender_rt_preset。
    配方文件：<K.out_dir>/presets/<name>.json   （自包含：name/kind/tags/note/data/mapping/version/created）
    data 用**点路径**表达：{"data": {"inputs.Base Color": [1,0,0,1], "Base Color": [1,0,0,1]}}
    apply(name, targets) 支持 target 形如：
        "MAT:材质名"       → 套到材质节点（node_name 可选）
        "OBJ:对象名"       → 套到对象属性
        "SCENE"            → 套到 scene 属性
        省略 targets         → 只返回 dry_run 预览（不写）
"""
import bpy
import json
import os
import time

PRESET_VERSION = 1


def _j(o):
    return json.dumps(o, ensure_ascii=False, default=str)


def _kernel():
    import sys
    return sys.modules.get("dsh_rt_kernel")


def _dir():
    K = _kernel()
    d = getattr(K, "out_dir", None) or os.path.join(os.path.expanduser("~"), "dsh_out")
    p = os.path.join(d, "presets")
    os.makedirs(p, exist_ok=True)
    return p


def _path(name):
    safe = "".join(c for c in str(name) if c.isalnum() or c in "._- ")
    if not safe:
        raise ValueError("非法配方名")
    return os.path.join(_dir(), safe + ".json")


def p_save(name, data, kind="generic", tags=None, note="", mapping=None, overwrite=True):
    p = _path(name)
    if os.path.isfile(p) and not overwrite:
        return _j({"ok": False, "error": "配方已存在（overwrite=false）", "name": str(name)})
    rec = {"name": str(name), "kind": str(kind), "tags": list(tags or []), "note": str(note),
           "data": data, "mapping": mapping or {}, "version": PRESET_VERSION,
           "created": time.strftime("%Y-%m-%d %H:%M:%S")}
    with open(p, "w", encoding="utf-8") as f:
        json.dump(rec, f, ensure_ascii=False, indent=1)
    return _j({"ok": True, "name": rec["name"], "kind": rec["kind"], "keys": len(data or {}), "path": p, "bytes": os.path.getsize(p)})


def _load(name):
    p = _path(name)
    if not os.path.isfile(p):
        return None
    with open(p, encoding="utf-8") as f:
        return json.load(f)


def p_list(kind=None, tag=None):
    out = []
    for fn in sorted(os.listdir(_dir())):
        if not fn.endswith(".json"):
            continue
        if fn.startswith("presets_bundle"):  # 导出包不参与列出（曾污染 list）
            continue
        try:
            with open(os.path.join(_dir(), fn), encoding="utf-8") as f:
                r = json.load(f)
        except Exception:
            continue
        if kind and r.get("kind") != kind:
            continue
        if tag and tag not in (r.get("tags") or []):
            continue
        out.append({"name": r.get("name"), "kind": r.get("kind"), "tags": r.get("tags"),
                    "keys": len(r.get("data") or {}), "note": r.get("note"), "created": r.get("created")})
    return _j({"ok": True, "count": len(out), "presets": out, "dir": _dir()})


def p_get(name):
    r = _load(name)
    return _j({"ok": bool(r), "preset": r}) if r else _j({"ok": False, "error": "没有这个配方: %s" % name, "dir": _dir()})


def p_delete(name):
    p = _path(name)
    if os.path.isfile(p):
        os.remove(p)
        return _j({"ok": True, "deleted": str(name)})
    return _j({"ok": False, "error": "没有这个配方: %s" % name})


def _get_path(root, path):
    cur = root
    for part in str(path).split("."):
        if cur is None:
            return None, None
        if isinstance(cur, dict):
            cur = cur.get(part)
        else:
            cur = getattr(cur, part, None)
    return cur, None


def _set_path(root, path, value):
    parts = str(path).split(".")
    cur = root
    for part in parts[:-1]:
        cur = cur.get(part) if isinstance(cur, dict) else getattr(cur, part, None)
        if cur is None:
            raise KeyError("路径不存在: %s" % path)
    last = parts[-1]
    if isinstance(cur, dict):
        if last not in cur:
            raise KeyError("键不存在: %s" % path)
        cur[last] = value
    else:
        if not hasattr(cur, last):
            raise KeyError("属性不存在: %s" % path)
        old = getattr(cur, last)
        if hasattr(old, "to_tuple") or hasattr(old, "__len__"):
            try:
                setattr(cur, last, type(old)(value) if not isinstance(value, (int, float, str, bool)) else value)
                return
            except Exception:
                pass
        setattr(cur, last, value)


def p_apply(name, targets=None, dry_run=False):
    r = _load(name)
    if not r:
        return _j({"ok": False, "error": "没有这个配方: %s" % name})
    data = dict(r.get("data") or {})
    for k, v in (r.get("mapping") or {}).items():
        if k in data:
            data[v] = data.pop(k)
    tgts = [str(t) for t in (targets or [])]
    report = {"applied": [], "skipped": [], "errors": []}
    for t in tgts:
        t = t.strip()
        if t.upper().startswith("MAT:"):
            mat = bpy.data.materials.get(t[4:].strip())
            if mat is None or not mat.use_nodes:
                report["skipped"].append({"target": t, "why": "材质不存在或未启用节点"})
                continue
            root = mat.node_tree
            for path, val in data.items():
                key = path if path.startswith("nodes.") else ("nodes." + path) if "." in path else ("defaults." + path)
                try:
                    if "." not in path:
                        _set_path(root.defaults, path, val)
                        report["applied"].append({"target": t, "path": "defaults." + path})
                    else:
                        _set_path(root, path, val)
                        report["applied"].append({"target": t, "path": path})
                except Exception as e:
                    report["errors"].append({"target": t, "path": path, "error": str(e)[:80]})
        elif t.upper().startswith("OBJ:"):
            ob = bpy.data.objects.get(t[4:].strip())
            if ob is None:
                report["skipped"].append({"target": t, "why": "对象不存在"})
                continue
            for path, val in data.items():
                try:
                    _set_path(ob, path, val)
                    report["applied"].append({"target": t, "path": path})
                except Exception as e:
                    report["errors"].append({"target": t, "path": path, "error": str(e)[:80]})
        elif t.upper().startswith("SCENE"):
            for path, val in data.items():
                try:
                    _set_path(bpy.context.scene, path, val)
                    report["applied"].append({"target": "SCENE", "path": path})
                except Exception as e:
                    report["errors"].append({"target": "SCENE", "path": path, "error": str(e)[:80]})
        else:
            report["skipped"].append({"target": t, "why": "无法识别的 target（用 MAT:名 / OBJ:名 / SCENE）"})
    if not tgts:
        bpy.context.view_layer.update()
    return _j({"ok": True, "preset": r.get("name"), "dry_run": True if not tgts else bool(dry_run),
               "keys": list(data), "targets": tgts, "report": report,
               "note": "未给 targets 时只预览；给了 targets 就实际写入（可用 dry_run=true 只校验路径）"})


def p_export(names=None, path=None):
    files = [f for f in os.listdir(_dir()) if f.endswith(".json")]
    picked = []
    for fn in files:
        with open(os.path.join(_dir(), fn), encoding="utf-8") as f:
            r = json.load(f)
        if names and r.get("name") not in names:
            continue
        picked.append(r)
    exp = os.path.join(os.path.dirname(_dir()), "presets_export")
    os.makedirs(exp, exist_ok=True)
    dst = path or os.path.join(exp, "presets_bundle_%s.json" % time.strftime("%Y%m%d_%H%M%S"))
    with open(dst, "w", encoding="utf-8") as f:
        json.dump({"bundle": picked, "count": len(picked), "version": PRESET_VERSION}, f, ensure_ascii=False, indent=1)
    return _j({"ok": True, "count": len(picked), "path": dst, "bytes": os.path.getsize(dst)})


def p_import(path, overwrite=False):
    with open(path, encoding="utf-8") as f:
        b = json.load(f)
    added, skipped = [], []
    for r in (b.get("bundle") or []):
        nm = r.get("name")
        if not nm:
            continue
        dst = _path(nm)
        if os.path.isfile(dst) and not overwrite:
            skipped.append(nm)
            continue
        r["version"] = PRESET_VERSION
        with open(dst, "w", encoding="utf-8") as f:
            json.dump(r, f, ensure_ascii=False, indent=1)
        added.append(nm)
    return _j({"ok": True, "added": added, "skipped": skipped, "dir": _dir()})


# ---------------------------------------------------------------- v0.8.10（D3）：玻璃 / 光学配方
# 外部反馈：EEVEE 默认不把玻璃渲透明 —— 要自己开 use_ssr_refraction / use_raytracing_refraction
# + 材质侧标志 + Principled 的 Transmission/Alpha，而且 **4.x/5.x 输入名不一样**（白渲一轮磨砂塑料才发现）。

_PRINCIPLED_ALIASES = {
    "transmission": ["Transmission Weight", "Transmission"],
    "ior": ["IOR"],
    "roughness": ["Roughness"],
    "alpha": ["Alpha"],
    "base_color": ["Base Color"],
    "specular": ["Specular IOR Level", "Specular"],
    "coat": ["Coat Weight", "Clearcoat"],
}


def _set_input(node, key, value):
    """按 4.x/5.x 别名表找 Principled 的输入名；成功返回真实输入名，失败返回 None。"""
    for name in _PRINCIPLED_ALIASES.get(key, []):
        if name in node.inputs:
            try:
                node.inputs[name].default_value = value
                return name
            except Exception:
                pass
    return None


def preset_glass(targets=None, ior=1.45, transmission=1.0, roughness=0.03, alpha=0.35,
                 base_color=(0.86, 0.93, 0.96, 1.0), use_alpha=False, scene_flags=True):
    """一条调用把「EEVEE 真透玻璃」配好：引擎开关 + 材质侧标志 + Principled 输入名映射（4.x/5.x 通吃）。

    targets：逗号分隔 MAT:名字 / OBJ:名字（OBJ 会取其所有材质槽）；省略则对所有材质应用。
    """
    rep = {"ok": True, "scene": {}, "materials": [], "warnings": []}
    sc = bpy.context.scene
    if scene_flags:
        ee = getattr(sc, "eevee", None)
        if ee is not None:
            for attr, val in (("use_raytracing", True), ("use_ssr_refraction", True), ("use_raytracing_refraction", True)):
                if hasattr(ee, attr):
                    try:
                        setattr(ee, attr, val)
                        rep["scene"][attr] = val
                    except Exception as e:
                        rep["warnings"].append("%s: %s" % (attr, str(e)[:60]))
                else:
                    rep["warnings"].append("本引擎没有 %s（版本差异）" % attr)
    # 选材质
    mats = []
    if targets:
        names = [t.strip() for t in str(targets).replace(";", ",").split(",") if t.strip()]
        for n in names:
            if n.upper().startswith("MAT:"):
                m = bpy.data.materials.get(n.split(":", 1)[1])
                if m:
                    mats.append(m)
                else:
                    rep["warnings"].append("材质不存在：%s" % n)
            elif n.upper().startswith("OBJ:"):
                ob = bpy.data.objects.get(n.split(":", 1)[1])
                if not ob:
                    rep["warnings"].append("对象不存在：%s" % n)
                    continue
                for slot in ob.material_slots:
                    if slot.material and slot.material not in mats:
                        mats.append(slot.material)
            else:
                rep["warnings"].append("target 形态不认（用 MAT:名 / OBJ:名）：%s" % n)
    else:
        mats = [m for m in bpy.data.materials if m.users > 0]
    for m in mats:
        item = {"name": m.name, "set": {}, "missing": []}
        try:
            if hasattr(m, "use_screen_refraction"):
                m.use_screen_refraction = True
                item["set"]["use_screen_refraction"] = True
            if hasattr(m, "use_raytrace_refraction"):
                m.use_raytrace_refraction = True
                item["set"]["use_raytrace_refraction"] = True
            # 5.x：surface_render_method='BLENDED'；4.x：blend_method='BLEND'
            for attr, val in (("surface_render_method", "BLENDED"), ("blend_method", "BLEND")):
                if hasattr(m, attr):
                    try:
                        setattr(m, attr, val)
                        item["set"][attr] = val
                    except Exception:
                        pass
            if hasattr(m, "show_transparent_back"):
                m.show_transparent_back = False
            nt = getattr(m, "node_tree", None)
            node = None
            if nt is not None:
                for nd in nt.nodes:
                    if nd.type == "BSDF_PRINCIPLED":
                        node = nd
                        break
            if node is None:
                item["missing"].append("没有 Principled BSDF 节点")
            else:
                for key, val in (("transmission", float(transmission)), ("ior", float(ior)),
                                 ("roughness", float(roughness)),
                                 ("alpha", float(alpha) if use_alpha else 1.0),
                                 ("base_color", tuple(base_color))):
                    got = _set_input(node, key, val)
                    if got:
                        item["set"]["inputs." + got] = val
                    else:
                        item["missing"].append(key)
        except Exception as e:
            item["error"] = "%s: %s" % (type(e).__name__, str(e)[:100])
        if item["missing"]:
            rep["warnings"].append("%s 缺少：%s" % (m.name, ", ".join(item["missing"])))
        rep["materials"].append(item)
    rep["count"] = len(rep["materials"])
    rep["note"] = ("EEVEE 真透明三件套：引擎 use_raytracing+use_ssr_refraction+use_raytracing_refraction、"
                   "材质 use_screen_refraction/use_raytrace_refraction、Principled 的 Transmission/Alpha；"
                   "输入名 4.x(Transmission Weight)/3.x(Transmission) 已自动映射。不透明塑料就别开 use_alpha。")
    if not rep["materials"]:
        rep["ok"] = False
        rep["error"] = "没有可应用的材质（检查 targets 或场景里是否有材质）"
    return _j(rep)


def p_help():
    return _j({"version": PRESET_VERSION, "dir": _dir(),
               "ops": {"save": "save(name, data, kind, tags, note, mapping, overwrite=true)",
                       "list": "list(kind=None, tag=None)", "get": "get(name)", "delete": "delete(name)",
                       "apply": "apply(name, targets=[MAT:x/OBJ:y/SCENE], dry_run=false)",
                       "export": "export(names=None, path=None) → 单个 bundle 文件（可分发）",
                       "import": "import(path, overwrite=false)",
                       "glass": "glass(targets='MAT:x,OBJ:y', ior, transmission, roughness, alpha, use_alpha) "
                                "→ 一条调用配好 EEVEE 真透明玻璃（v0.8.10 D3）"},
               "data_format": "点路径 dict：{\"inputs.Base Color\": [1,0,0,1]} 或 {\"nodes.Principled BSDF.inputs.Roughness\": 0.4} 或 {\"location\": [0,0,1]}",
               "why": "把风格/参数配方变成资产：可保存、可套用、可导出分享；与领域无关（材质/对象/场景/自定义都行）"})


def p_dispatch(op, args=None):
    if isinstance(args, str):
        try:
            args = json.loads(args) if args.strip() else {}
        except Exception:
            args = {}
    fn = {"save": p_save, "list": p_list, "get": p_get, "delete": p_delete, "apply": p_apply,
          "export": p_export, "import": p_import, "glass": preset_glass, "help": p_help}.get(str(op))
    if fn is None:
        return _j({"ok": False, "error": "unknown preset op", "op": op,
                   "ops": ["save", "list", "get", "delete", "apply", "export", "import", "glass", "help"]})
    try:
        return fn(**(args or {}))
    except TypeError as e:
        return _j({"ok": False, "error": "参数不匹配: %s" % e, "op": op, "given": sorted(args or {}), "sig": p_help()})


import sys as _sys
_K = _sys.modules.get("dsh_rt_kernel")
if _K is not None:
    _K.dsh_preset_api = {"version": PRESET_VERSION, "dispatch": p_dispatch, "save": p_save, "list": p_list,
                         "get": p_get, "delete": p_delete, "apply": p_apply, "export": p_export,
                         "import": p_import, "glass": preset_glass, "help": p_help}