# -*- coding: utf-8 -*-
"""Split from blockout.py; behavior preserved by the facade."""
from .common import *  # noqa: F401,F403



# ────────────────────────────── 内置比例模板（mm；只放 primitive）──────────────────────────────
def _scale_value(v, s):
    if isinstance(v, (int, float)):
        return float(v) * s
    if isinstance(v, dict):
        return {k: _scale_value(x, s) for k, x in v.items()}
    if isinstance(v, (list, tuple)):
        return [_scale_value(x, s) for x in v]
    return v


def _scaled_parts(parts, s):
    out = []
    for p in parts:
        q = dict(p)
        for k in list(q.keys()):
            if k in ("id", "prim", "axis", "mirror", "name"):
                continue
            q[k] = _scale_value(q[k], s)
        out.append(q)
    return out


def _template_humanoid():
    """7.5 头身素体（总高 ≈1750mm）：头/颈/胸/胯/手臂/腿/脚，四肢用 mirror 出左右。"""
    H = 1750.0
    head_h = H / 7.5
    head_r = head_h * 0.46
    head_z = H - head_r
    chest_top = H - 1.5 * head_h
    chest_h = 1.1 * head_h
    chest_z = chest_top - chest_h / 2.0
    pelvis_h = 0.95 * head_h
    pelvis_z = chest_top - chest_h - pelvis_h / 2.0
    arm_len = 3.1 * head_h
    arm_z = chest_top - arm_len / 2.0
    leg_top = pelvis_z - pelvis_h / 2.0
    leg_len = leg_top
    foot_h = 0.42 * head_h
    foot_y = 0.32 * head_h
    return [
        {"id": "head", "prim": "uv_sphere", "radius_mm": head_r, "at_mm": [0, 0, head_z]},
        {"id": "neck", "prim": "cylinder", "radius_mm": 0.21 * head_h, "depth_mm": 0.42 * head_h,
         "at_mm": [0, 0, head_z - head_r - 0.10 * head_h], "axis": "z"},
        {"id": "chest", "prim": "box", "size_mm": [1.8 * head_h, 1.05 * head_h, chest_h],
         "at_mm": [0, 0, chest_z]},
        {"id": "pelvis", "prim": "box", "size_mm": [1.3 * head_h, 0.95 * head_h, pelvis_h],
         "at_mm": [0, 0, pelvis_z]},
        {"id": "arm", "prim": "cylinder", "radius_mm": 0.26 * head_h, "depth_mm": arm_len,
         "at_mm": [1.15 * head_h, 0, arm_z], "axis": "z", "mirror": True},
        {"id": "hand", "prim": "uv_sphere", "radius_mm": 0.30 * head_h,
         "at_mm": [1.15 * head_h, 0, arm_z - arm_len / 2.0 - 0.15 * head_h], "mirror": True},
        {"id": "leg", "prim": "cylinder", "radius_mm": 0.36 * head_h, "depth_mm": leg_len,
         "at_mm": [0.47 * head_h, 0, leg_len / 2.0], "axis": "z", "mirror": True},
        {"id": "foot", "prim": "box", "size_mm": [0.55 * head_h, 1.1 * head_h, foot_h],
         "at_mm": [0.47 * head_h, foot_y, foot_h / 2.0], "mirror": True},
    ]


def _template_vehicle():
    """轿车占位：X=车宽、Y=车长、Z=高；车身+座舱+四轮（轮轴沿 X，mirror 出左右）。"""
    return [
        {"id": "body", "prim": "box", "size_mm": [1800, 4200, 700], "at_mm": [0, 0, 800]},
        {"id": "cabin", "prim": "box", "size_mm": [1500, 2200, 650], "at_mm": [0, -200, 1375]},
        {"id": "wheel_fr", "prim": "cylinder", "radius_mm": 350, "depth_mm": 260,
         "at_mm": [900, 1400, 350], "axis": "x", "mirror": True},
        {"id": "wheel_rr", "prim": "cylinder", "radius_mm": 350, "depth_mm": 260,
         "at_mm": [900, -1400, 350], "axis": "x", "mirror": True},
    ]


def _template_parts(template, scale):
    if template == "humanoid_prim":
        parts = _template_humanoid()
    elif template == "vehicle":
        parts = _template_vehicle()
    else:
        raise ValueError("template 只支持 %s（收到 %r）" % ("/".join(ALLOWED_TEMPLATES), template))
    return _scaled_parts(parts, float(scale))


# ────────────────────────────── 参数解析 ──────────────────────────────
def _parse_part(spec, index, units):
    if not isinstance(spec, dict):
        raise ValueError("parts[%d] 必须是 dict（收到 %r）" % (index, spec))
    unknown = sorted(set(str(k) for k in spec.keys()) - PART_KEYS)
    if unknown:
        raise ValueError("parts[%d] 不认识的键 %s（可用：%s）"
                         % (index, unknown, ",".join(sorted(PART_KEYS))))
    prim_raw = str(spec.get("prim") or "").strip().lower()
    prim = {"box": "box", "cube": "box", "cylinder": "cylinder", "cyl": "cylinder",
            "uv_sphere": "uv_sphere", "uvsphere": "uv_sphere", "sphere": "uv_sphere"}.get(prim_raw)
    if prim is None:
        raise ValueError("parts[%d].prim 只支持 box/cylinder/uv_sphere（收到 %r）" % (index, spec.get("prim")))
    pid = _safe_token(spec.get("id") or spec.get("name") or ("p%02d" % index), "parts[%d].id" % index)
    factor = _unit_to_mm(units) / _mm_per_bu()     # 输入单位 → Blender 单位
    at = _vec3(spec.get("at_mm", spec.get("at")), "%s.at_mm" % pid, factor, default=(0.0, 0.0, 0.0))
    rot = _vec3(spec.get("rot_deg", spec.get("rot")), "%s.rot_deg" % pid, None, default=(0.0, 0.0, 0.0))
    mirror = _bool(spec.get("mirror"), "%s.mirror" % pid, False)
    axis = str(spec.get("axis") or "z").strip().lower()
    if axis not in ("x", "y", "z"):
        raise ValueError("%s.axis 只支持 x/y/z（收到 %r）" % (pid, spec.get("axis")))
    out = {"id": pid, "prim": prim, "at_bu": at, "rot_deg": rot, "mirror": mirror, "axis": axis,
           "size_bu": None, "radius_bu": None, "depth_bu": None,
           "size_mm": None, "radius_mm": None, "depth_mm": None}
    if prim == "box":
        if spec.get("radius_mm") is not None or spec.get("radius") is not None:
            raise ValueError("%s 是 box：用 size_mm=[x,y,z]，不要 radius_mm" % pid)
        raw = spec.get("size_mm", spec.get("size"))
        if raw is None:
            raise ValueError("%s 是 box：必须给 size_mm=[x,y,z]（或单个数=立方）" % pid)
        size_mm = _box_size(raw, "%s.size_mm" % pid)
        out["size_mm"] = size_mm
        out["size_bu"] = [v * factor for v in size_mm]
    elif prim == "cylinder":
        if spec.get("size_mm") is not None or spec.get("size") is not None:
            raise ValueError("%s 是 cylinder：用 radius_mm + depth_mm（axis=x|y|z），不要 size_mm" % pid)
        r = spec.get("radius_mm", spec.get("radius"))
        d = spec.get("diameter_mm", spec.get("diameter"))
        if r is not None and d is not None:
            raise ValueError("%s 同时给了 radius_mm 和 diameter_mm：只给一个" % pid)
        if r is None and d is not None:
            r = _num(d, "%s.diameter_mm" % pid, gt=0.0) / 2.0
        if r is None:
            raise ValueError("%s 是 cylinder：必须给 radius_mm（或 diameter_mm）" % pid)
        depth = _num(spec.get("depth_mm", spec.get("depth")), "%s.depth_mm" % pid, gt=0.0)
        radius = _num(r, "%s.radius_mm" % pid, gt=0.0)
        out["radius_mm"] = radius
        out["depth_mm"] = depth
        out["radius_bu"] = radius * factor
        out["depth_bu"] = depth * factor
    else:
        for bad in ("size_mm", "size", "depth_mm", "depth"):
            if spec.get(bad) is not None:
                raise ValueError("%s 是 uv_sphere：用 radius_mm，不要 %s" % (pid, bad))
        r = spec.get("radius_mm", spec.get("radius"))
        d = spec.get("diameter_mm", spec.get("diameter"))
        if r is not None and d is not None:
            raise ValueError("%s 同时给了 radius_mm 和 diameter_mm：只给一个" % pid)
        if r is None and d is not None:
            r = _num(d, "%s.diameter_mm" % pid, gt=0.0) / 2.0
        if r is None:
            raise ValueError("%s 是 uv_sphere：必须给 radius_mm（或 diameter_mm）" % pid)
        radius = _num(r, "%s.radius_mm" % pid, gt=0.0)
        out["radius_mm"] = radius
        out["radius_bu"] = radius * factor
    return out


def _dedupe_ids(parsed):
    seen = {}
    warnings = []
    for p in parsed:
        base = p["id"]
        k = seen.get(base, 0)
        seen[base] = k + 1
        if k:
            p["id"] = "%s-%d" % (base, k + 1)
            warnings.append("重复的 part id %r 已重命名为 %r" % (base, p["id"]))
    return warnings


def _parse_landmarks(raw, units):
    if raw is None:
        return [], []
    factor = _unit_to_mm(units) / _mm_per_bu()
    items = []
    if isinstance(raw, dict):
        for k, v in raw.items():
            items.append((str(k), v))
    elif isinstance(raw, (list, tuple)):
        for i, e in enumerate(raw):
            if not isinstance(e, dict):
                raise ValueError("landmarks[%d] 必须是 dict（{name, at_mm}）" % i)
            nm = e.get("name", e.get("id", "lm%02d" % i))
            at = e.get("at_mm", e.get("at", e.get("pos")))
            items.append((str(nm), at))
    else:
        raise ValueError("landmarks 必须是 {名字:[x,y,z]} 或 [{name,at_mm}]")
    out = []
    for nm, at in items:
        try:
            vals = _vec3(at, "landmarks[%r]" % nm, factor)
        except ValueError as e:
            raise ValueError(str(e))
        out.append((str(nm)[:64], vals))
    return out, []


def _parse_res(v):
    if v is None:
        return list(DEFAULT_RES)
    if isinstance(v, (int, float)):
        n = int(_num(v, "render.res", gt=0.0, maxv=4096))
        res = [n, n]
    elif isinstance(v, (list, tuple)) and len(v) == 2:
        res = [int(_num(x, "render.res[%d]" % i, gt=0)) for i, x in enumerate(v)]
    elif isinstance(v, str) and "x" in v.lower():
        try:
            a, b = v.lower().split("x", 1)
            res = [int(a), int(b)]
        except Exception:
            raise ValueError("render.res 写法看不懂：%r（用 [512,512] 或 512）" % (v,))
    else:
        raise ValueError("render.res 必须是 [w,h] 或单个数（收到 %r）" % (v,))
    if not (64 <= res[0] <= 4096 and 64 <= res[1] <= 4096):
        raise ValueError("render.res 每边必须在 64..4096（收到 %s）" % res)
    return res


def _view_spec(v, index):
    if isinstance(v, dict):
        try:
            az = _num(v.get("az"), "views[%d].az" % index)
            el = _num(v.get("el"), "views[%d].el" % index, default=0.0)
        except ValueError:
            raise
        nm = _safe_token(v.get("name") or ("az%d_el%d" % (int(round(az)), int(round(el)))), "views[%d].name" % index)
        return {"name": nm, "az": az, "el": el}
    if not isinstance(v, str) or not v.strip():
        raise ValueError("views[%d] 必须是视角名或 {name,az,el}（收到 %r）" % (index, v))
    s = v.strip().lower()
    if "az=" in s:
        kv = {}
        for part in s.replace(" ", "").split(","):
            if "=" in part:
                k, val = part.split("=", 1)
                try:
                    kv[k] = float(val)
                except ValueError:
                    raise ValueError("views[%d] 角度的 az=/el= 不是数字：%r" % (index, v))
        az = kv.get("az")
        if az is None:
            raise ValueError("views[%d] 的 az= 写法必须带 az（收到 %r）" % (index, v))
        el = kv.get("el", 0.0)
        nm = _safe_token("az%d_el%d" % (int(round(az)), int(round(el))), "views[%d].name" % index)
        return {"name": nm, "az": az, "el": el}
    s = _VIEW_ALIASES.get(s, s)
    if s not in _NAMED_VIEWS:
        raise ValueError("未知视角 %r（可用：%s；或写 az=..,el=.. / {name,az,el}）"
                         % (v, ", ".join(sorted(_NAMED_VIEWS))))
    az, el = _NAMED_VIEWS[s]
    return {"name": s, "az": az, "el": el}


def _resolve_views(raw):
    if raw is None:
        raw = list(VIEW_AUTO3)
    if isinstance(raw, str):
        s = raw.strip()
        if s.lower() in ("auto3", "auto", "default"):
            raw = list(VIEW_AUTO3)
        else:
            raw = [x.strip() for x in s.replace(";", ",").split(",") if x.strip()]
    if not isinstance(raw, (list, tuple)):
        raise ValueError("render.views 必须是 list / 'auto3' / 'front,right,top'（收到 %r）" % (raw,))
    if not raw:
        raise ValueError("render.views 不能是空 list")
    if len(raw) > 12:
        raise ValueError("render.views 最多 12 个（收到 %d）" % len(raw))
    out, seen = [], set()
    for i, v in enumerate(raw):
        spec = _view_spec(v, i)
        if spec["name"] in seen:
            continue
        seen.add(spec["name"])
        out.append(spec)
    if not out:
        raise ValueError("render.views 解析后为空")
    return out


def _parse_render_opt(render, root, tag):
    if render is None or render is False or render == 0:
        return None
    if render is True:
        opt = {}
    elif isinstance(render, str):
        opt = {"views": render}
    elif isinstance(render, dict):
        opt = dict(render)
    else:
        raise ValueError("render 必须是 dict / true / false（收到 %r）" % (render,))
    unknown = sorted(set(str(k) for k in opt.keys()) - _RENDER_KEYS)
    if unknown:
        raise ValueError("render 里不认识的键 %s（可用：%s）"
                         % (unknown, ",".join(sorted(_RENDER_KEYS))))
    eng = str(opt.get("engine") or "workbench").strip().lower()
    if eng not in ("workbench", "wb"):
        raise ValueError("render.engine 只支持 workbench（收到 %r）—— 白模要快、要稳，不接 EEVEE/Cycles" % (opt.get("engine")))
    res = _parse_res(opt.get("res"))
    views = _resolve_views(opt.get("views"))
    outdir = _norm_path(opt.get("outdir"), root) if opt.get("outdir") else _norm_path(root)
    try:
        os.makedirs(outdir, exist_ok=True)
    except Exception as e:
        raise ValueError("render.outdir 建不出来：%s（%s）" % (outdir, str(e)[:120]))
    rtag = _safe_token(opt.get("tag") or tag, "render.tag")
    sheet = opt.get("sheet", True)
    if isinstance(sheet, bool):
        sheet_cols = len(views) if sheet else 0
    elif isinstance(sheet, (int, float)):
        sheet_cols = int(sheet)
        if sheet_cols < 0:
            raise ValueError("render.sheet 为数字时必须 >=0")
        sheet_cols = min(sheet_cols, len(views))
    else:
        raise ValueError("render.sheet 必须是 bool 或列数（收到 %r）" % (sheet,))
    via = str(opt.get("via") or "auto").strip().lower()
    if via not in ("auto", "qc", "internal"):
        raise ValueError("render.via 只支持 auto/qc/internal（收到 %r）" % (opt.get("via"),))
    margin = _num(opt.get("margin"), "render.margin", default=1.12, minv=1.01, maxv=4.0)
    lens = _num(opt.get("lens"), "render.lens", default=50.0, gt=1.0, maxv=300.0)
    film = _bool(opt.get("film_transparent"), "render.film_transparent", True)
    return {"engine": "workbench", "res": res, "views": views, "outdir": outdir, "tag": rtag,
            "sheet_cols": sheet_cols, "via": via, "margin": margin, "lens": lens,
            "film_transparent": film}


def _parse_export_opt(export, root, stem):
    if export is None or export is False or export == 0:
        return None
    if export is True:
        exp = {"glb": True}
    elif isinstance(export, str):
        exp = {"glb": export}
    elif isinstance(export, dict):
        exp = dict(export)
    else:
        raise ValueError("export 必须是 dict / true / false / 路径字符串（收到 %r）" % (export,))
    unknown = sorted(set(str(k) for k in exp.keys()) - set(["glb", "obj", "outdir"]))
    if unknown:
        raise ValueError("export 里不认识的键 %s（可用：glb,obj,outdir）" % unknown)
    sub_out = _norm_path(exp.get("outdir") or root)
    paths = {}
    for k in ("glb", "obj"):
        v = exp.get(k)
        if v is None or v is False:
            continue
        if v is True:
            p = os.path.join(sub_out, str(stem) + "." + k)
        elif isinstance(v, str) and v.strip():
            p = _norm_path(v.strip(), sub_out)
        else:
            raise ValueError("export.%s 必须是路径或 true（收到 %r）" % (k, v))
        paths[k] = p
    if not paths:
        raise ValueError("export 里没给 glb/obj（可写 true 自动命名，或给路径）")
    return paths


# ────────────────────────────── 度量 / 体检 ──────────────────────────────
