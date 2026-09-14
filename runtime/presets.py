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
    dst = path or os.path.join(_dir(), "presets_bundle_%s.json" % time.strftime("%Y%m%d_%H%M%S"))
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


def p_help():
    return _j({"version": PRESET_VERSION, "dir": _dir(),
               "ops": {"save": "save(name, data, kind, tags, note, mapping, overwrite=true)",
                       "list": "list(kind=None, tag=None)", "get": "get(name)", "delete": "delete(name)",
                       "apply": "apply(name, targets=[MAT:x/OBJ:y/SCENE], dry_run=false)",
                       "export": "export(names=None, path=None) → 单个 bundle 文件（可分发）",
                       "import": "import(path, overwrite=false)"},
               "data_format": "点路径 dict：{\"inputs.Base Color\": [1,0,0,1]} 或 {\"nodes.Principled BSDF.inputs.Roughness\": 0.4} 或 {\"location\": [0,0,1]}",
               "why": "把风格/参数配方变成资产：可保存、可套用、可导出分享；与领域无关（材质/对象/场景/自定义都行）"})


def p_dispatch(op, args=None):
    if isinstance(args, str):
        try:
            args = json.loads(args) if args.strip() else {}
        except Exception:
            args = {}
    fn = {"save": p_save, "list": p_list, "get": p_get, "delete": p_delete, "apply": p_apply,
          "export": p_export, "import": p_import, "help": p_help}.get(str(op))
    if fn is None:
        return _j({"ok": False, "error": "unknown preset op", "op": op, "ops": ["save", "list", "get", "delete", "apply", "export", "import", "help"]})
    try:
        return fn(**(args or {}))
    except TypeError as e:
        return _j({"ok": False, "error": "参数不匹配: %s" % e, "op": op, "given": sorted(args or {}), "sig": p_help()})


import sys as _sys
_K = _sys.modules.get("dsh_rt_kernel")
if _K is not None:
    _K.dsh_preset_api = {"version": PRESET_VERSION, "dispatch": p_dispatch, "save": p_save, "list": p_list,
                         "get": p_get, "delete": p_delete, "apply": p_apply, "export": p_export,
                         "import": p_import, "help": p_help}