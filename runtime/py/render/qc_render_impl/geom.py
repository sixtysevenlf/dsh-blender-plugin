# -*- coding: utf-8 -*-
"""Split from qc_render.py; behavior preserved by the facade."""
from .common import *  # noqa: F401,F403

def _safe(name):
    s = "".join((c if (c.isalnum() or c in "-_.") else "_") for c in str(name))
    return s.strip("_") or "view"


def _md5(path):
    h = hashlib.md5()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 16), b""):
            h.update(chunk)
    return h.hexdigest()


# ---------------------------------------------------------------- 目标 AABB + 取景

def _collect_objs(names=None):
    scn = bpy.context.scene
    skipped = {"hidden": 0, "empty": 0, "missing": []}
    objs = []
    if names:
        for n in names:
            ob = bpy.data.objects.get(str(n))
            if ob is None:
                skipped["missing"].append(str(n))
                continue
            objs.append(ob)
        return objs, skipped
    for ob in scn.objects:
        if ob.type != "MESH":
            continue
        if ob.hide_render:
            skipped["hidden"] += 1
            continue
        try:
            if not ob.visible_get():
                skipped["hidden"] += 1
                continue
        except Exception:
            pass
        if ob.data is None or len(ob.data.vertices) == 0:
            skipped["empty"] += 1
            continue
        objs.append(ob)
    return objs, skipped


def _aabb(objs):
    mn = Vector((1e30, 1e30, 1e30))
    mx = Vector((-1e30, -1e30, -1e30))
    n = 0
    for ob in objs:
        mw = ob.matrix_world
        for c in ob.bound_box:
            p = mw @ Vector((c[0], c[1], c[2]))
            for i in range(3):
                if p[i] < mn[i]:
                    mn[i] = p[i]
                if p[i] > mx[i]:
                    mx[i] = p[i]
        n += 1
    if n == 0:
        return None, None, 0
    return mn, mx, n


def _corners(mn, mx):
    return [Vector((x, y, z)) for x in (mn[0], mx[0]) for y in (mn[1], mx[1]) for z in (mn[2], mx[2])]


def _fov_tans(w, h, lens, sensor, sensor_fit="AUTO"):
    """与 bpy Camera.calc_matrix_camera 同一口径（AUTO：sensor 贴长边），返回 (tan_x, tan_y)。"""
    fit = ("H" if w >= h else "V") if str(sensor_fit).upper().startswith("AUTO") else str(sensor_fit)[0].upper()
    if fit == "H":
        hx = sensor / 2.0
        hy = hx * (h / float(w))
    else:
        hy = sensor / 2.0
        hx = hy * (w / float(h))
    return hx / max(1e-9, float(lens)), hy / max(1e-9, float(lens))


def _basis(az, el):
    """(相机方位角, 仰角) → (center→camera 单位向量, 视线方向, right, up)。"""
    a = math.radians(float(az))
    e = math.radians(float(el))
    away = Vector((math.cos(e) * math.cos(a), math.cos(e) * math.sin(a), math.sin(e)))
    d = -away
    up_hint = Vector((0.0, 0.0, 1.0))
    if abs(d.dot(up_hint)) > 0.999:
        up_hint = Vector((0.0, 1.0, 0.0))
    right = d.cross(up_hint)
    right = right.normalized() if right.length > 1e-9 else Vector((1.0, 0.0, 0.0))
    up = right.cross(d).normalized()
    return away, d, right, up


def _solve_view(center, corners, az, el, w, h, lens, sensor, margin, ortho=False, ortho_scale=None):
    """解相机位置：让 AABB 八角全部落在视锥内，再乘 margin 留白。返回 (loc, d, ortho_scale_used, 统计)。"""
    away, d, right, up = _basis(az, el)
    tx, ty = _fov_tans(w, h, lens, sensor)
    half_x = half_y = 0.0
    dist = 0.0
    for p in corners:
        r = p - center
        x = r.dot(right)
        y = r.dot(up)
        z = r.dot(-d)          # 朝相机方向为正
        half_x = max(half_x, abs(x))
        half_y = max(half_y, abs(y))
        if not ortho:
            dist = max(dist, abs(x) / tx - z, abs(y) / ty - z)
    if ortho:
        if w >= h:
            need = max(2.0 * half_x, 2.0 * half_y * (w / float(h)))
        else:
            need = max(2.0 * half_y, 2.0 * half_x * (h / float(w)))
        scale = float(ortho_scale) if ortho_scale else need * float(margin)
        dd = max(2.0 * max(half_x, half_y), 1e-3) * 4.0
        return center - d * dd, d, scale, {"half_x": half_x, "half_y": half_y, "dist": dd}
    dist = max(dist * float(margin), 1e-3)
    return center - d * dist, d, None, {"half_x": half_x, "half_y": half_y, "dist": dist}


def _proj_matrix_fallback(cam, w, h):
    """与 view.py 同源的投影矩阵（calc_matrix_camera 不可用时的兜底），相机在原点朝 -Z。"""
    from mathutils import Matrix
    cd = cam.data
    lens = float(cd.lens)
    sensor = float(cd.sensor_width)
    fit = str(cd.sensor_fit)
    tx, ty = _fov_tans(w, h, lens, sensor, fit)
    if cd.type == "ORTHO":
        sx = float(cd.ortho_scale)
        if w >= h:
            p00, p11 = 2.0 / sx, 2.0 / (sx * (h / float(w)))
        else:
            p00, p11 = 2.0 / (sx * (w / float(h))), 2.0 / sx
        return Matrix(((p00, 0.0, 2.0 * cd.shift_x, 0.0), (0.0, p11, 2.0 * cd.shift_y, 0.0),
                       (0.0, 0.0, -2.0 / (cd.clip_end - cd.clip_start),
                        -(cd.clip_end + cd.clip_start) / (cd.clip_end - cd.clip_start)), (0.0, 0.0, 0.0, 1.0)))
    p00 = 1.0 / tx
    p11 = 1.0 / ty
    cs, ce = float(cd.clip_start), float(cd.clip_end)
    return Matrix(((p00, 0.0, 2.0 * cd.shift_x, 0.0), (0.0, p11, 2.0 * cd.shift_y, 0.0),
                   (0.0, 0.0, -(ce + cs) / (ce - cs), -(2.0 * ce * cs) / (ce - cs)), (0.0, 0.0, -1.0, 0.0)))


def _fallback_px(cam, points, w, h):
    """自算投影矩阵 → 像素（与 view.py 同源；Blender 5.2 起 Camera.calc_matrix_camera 已被移除）。"""
    pm = _proj_matrix_fallback(cam, w, h)
    vm = cam.matrix_world.inverted()
    xs, ys = [], []
    for p in points:
        # 注意：mathutils 里 4x4 @ 3D 向量不做透视除法 → 显式拼成 4D 点
        v = vm @ Vector((float(p[0]), float(p[1]), float(p[2])))
        c = pm @ Vector((float(v[0]), float(v[1]), float(v[2]), 1.0))
        ww = float(c[3])
        if abs(ww) < 1e-9:
            ww = 1e-9
        xs.append((float(c[0]) / ww * 0.5 + 0.5) * w)
        ys.append((0.5 - float(c[1]) / ww * 0.5) * h)
    return xs, ys


def _project_px(cam, points, w, h):
    """世界点 → 像素 bbox。主口径 = Blender 自己的 world_to_camera_view（真相），
    自算矩阵作对拍；两者逐点像素差进 proj_err_px（正常应在 1e-3 像素以内）。

    返回 (bbox, source, err_px)。
    """
    xs, ys = [], []
    src = "fallback"
    try:
        from bpy_extras.object_utils import world_to_camera_view
        scn = bpy.context.scene
        for p in points:
            c = world_to_camera_view(scn, cam, p)
            xs.append(float(c[0]) * w)
            ys.append((1.0 - float(c[1])) * h)
        src = "world_to_camera_view"
    except Exception:
        xs, ys, src = [], [], "fallback"
    fbx, fby = _fallback_px(cam, points, w, h)
    if src == "world_to_camera_view" and len(fbx) == len(xs):
        err = max([abs(a - b) for a, b in zip(xs, fbx)] + [abs(a - b) for a, b in zip(ys, fby)] or [0.0])
        err = round(float(err), 6)
    else:
        xs, ys, src, err = fbx, fby, "fallback", None
    return [round(min(xs), 1), round(min(ys), 1), round(max(xs), 1), round(max(ys), 1)], src, err


# ---------------------------------------------------------------- 灯光

# ---- v0.8.10（C5）：世界坐标框选（aabb=[x0,y0,z0,x1,y1,z1] / focus="x,y,z,r"）
def _focus_box(spec):
    """focus="x,y,z,r"（球心+半径，按外接立方取景）或 [x0,y0,z0,x1,y1,z1] → (min,max)；非法返回 None。"""
    if not spec:
        return None
    try:
        if isinstance(spec, (list, tuple)):
            v = [float(x) for x in spec]
            if len(v) == 6:
                mn = Vector((min(v[0], v[3]), min(v[1], v[4]), min(v[2], v[5])))
                mx = Vector((max(v[0], v[3]), max(v[1], v[4]), max(v[2], v[5])))
                return mn, mx
            if len(v) == 4:                       # x,y,z,r
                c = Vector((v[0], v[1], v[2]))
                r = abs(v[3])
                return c - Vector((r, r, r)), c + Vector((r, r, r))
            return None
        if isinstance(spec, dict):
            if "mn" in spec and "mx" in spec:
                return Vector([float(x) for x in spec["mn"]]), Vector([float(x) for x in spec["mx"]])
            if "center" in spec and "radius" in spec:
                c = Vector([float(x) for x in spec["center"]])
                r = abs(float(spec["radius"]))
                return c - Vector((r, r, r)), c + Vector((r, r, r))
            return None
        s = str(spec).strip()
        if not s:
            return None
        v = [float(x) for x in s.replace(" ", "").split(",")]
        return _focus_box(v)
    except Exception:
        return None


def _apply_step(rs, ladder, step_i, w, h):
    """按降质档设置分辨率/采样（v0.8.10 C2 的降质阶梯）。返回 (w_eff, h_eff, step_used)。"""
    if not ladder:
        rs.resolution_x, rs.resolution_y = w, h
        return w, h, None
    step_i = max(0, min(int(step_i), len(ladder) - 1))
    step = ladder[step_i] or {}
    sc_ = float(step.get("scale", 1.0) or 1.0)
    w_eff = max(16, int(round(w * sc_)))
    h_eff = max(16, int(round(h * sc_)))
    rs.resolution_x, rs.resolution_y = w_eff, h_eff
    n = step.get("samples")
    if n:
        scn = bpy.context.scene
        ee = getattr(scn, "eevee", None)
        if ee is not None and hasattr(ee, "taa_render_samples"):
            try:
                ee.taa_render_samples = int(n)
            except Exception:
                pass
        if hasattr(scn, "cycles"):
            try:
                scn.cycles.samples = int(n)
            except Exception:
                pass
    return w_eff, h_eff, step


def _db_counts():
    """datablock 计数（v0.8.10 D1：临时资源泄漏自检）。"""
    out = {}
    for k in ("objects", "meshes", "materials", "lights", "cameras", "images", "collections"):
        try:
            out[k] = len(getattr(bpy.data, k))
        except Exception:
            out[k] = None
    try:
        out["orphans"] = len([d for d in bpy.data.lights if d.users == 0]) + len([d for d in bpy.data.cameras if d.users == 0])
    except Exception:
        out["orphans"] = None
    return out


def _montage_api():
    """拿 montage 模块（拼图）：K.dsh_montage_api → 否则现场加载 montage.py。

    T8 起 montage.py 在 runtime/py/render/；用共享内核 find_runtime_file 兼容旧扁平布局。
    """
    K = _kernel()
    api = getattr(K, "dsh_montage_api", None) if K is not None else None
    if api:
        return api
    p = None
    try:
        p = _KIT.find_runtime_file("montage.py")
    except Exception:
        p = None
    if not p:
        d = getattr(K, "runtime_dir", None) if K is not None else None
        if d:
            p = os.path.join(str(d), "montage.py")
    if p and os.path.isfile(p):
        with open(p, encoding="utf-8") as fh:
            src = fh.read()
        exec(compile(src, p, "exec"), {"__name__": "dsh_montage", "__file__": p})
        api = getattr(K, "dsh_montage_api", None)
    return api
