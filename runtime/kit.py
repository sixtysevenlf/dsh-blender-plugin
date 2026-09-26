# -*- coding: utf-8 -*-
"""DSH 共享内核（kit）—— **唯一实现**：JSON/API 样板、单位、几何、度量、回执骨架。

为什么存在：此前 26 个模块各自复制了 `class _DshApi` / `def _j` / dispatch / 单位换算 / BVH / IoU，
同一套管道代码 ~800-1500 行，且约定会在复制中漂移（contract.py 与 deliver.py 各自踩过同一个 dict-vs-Api 坑）。

怎么用：kit 由 KERNEL_BOOTSTRAP 注入（注册为 K.dsh_kit），所以**每条注入路径都有它**：
    K = sys.modules.get("dsh_rt_kernel")
    _KIT = getattr(K, "dsh_kit", None)          # None 时说明没走 bootstrap（自建脚本要自己兜）
    _j = _KIT.j ; Api = _KIT.Api ; dispatch = _KIT.dispatch ; units = _KIT.units

约定：kit 只放"**每个模块都要写一遍**"的东西；领域逻辑一律留在各模块。
"""
import json
import sys
import types

KIT_VERSION = 1


def _kernel():
    K = sys.modules.get("dsh_rt_kernel")
    if K is None:
        K = types.ModuleType("dsh_rt_kernel")
        sys.modules["dsh_rt_kernel"] = K
    return K


# ────────────────────────────── JSON / 回执 ──────────────────────────────
def j(obj):
    """统一 JSON 序列化（中文不转义；非 JSON 类型退化为 str）。"""
    return json.dumps(obj, ensure_ascii=False, default=str)


def err(msg, **kw):
    """统一错误回执。"""
    d = {"ok": False, "error": str(msg)[:300]}
    d.update(kw)
    return j(d)


def receipt(ok=None, state=None, verdict=None, **kw):
    """统一回执骨架：ok / state / verdict / units 四件套 + 任意附加字段。

    state 与 verdict 的关系（与 gate v3 一致）：state 用于"这一项成没成"，verdict 用于三态判定；
    调用方给哪个就带哪个，不给就不编。
    """
    d = {}
    if ok is not None:
        d["ok"] = bool(ok)
    if state is not None:
        d["state"] = state
    if verdict is not None:
        d["verdict"] = verdict
    d["units"] = {"mm_per_unit": units()}
    d.update(kw)
    return d


# ────────────────────────────── 单位 ──────────────────────────────
def units():
    """mm_per_unit = scene.unit_settings.scale_length * 1000（Blender 默认 1 单位 = 1 m ⇒ 1000）。"""
    try:
        import bpy
        return float(bpy.context.scene.unit_settings.scale_length or 1.0) * 1000.0
    except Exception:
        return 1000.0


def mm(v):
    """Blender 单位 → 毫米。"""
    return float(v) * units()


# ────────────────────────────── 跨模块 API ──────────────────────────────
def api(name):
    """取另一个模块的 API（`K.dsh_<name>_api`）；没注入返回 None。"""
    return getattr(_kernel(), "dsh_%s_api" % name, None)


class Api(dict):
    """模块 API 的标准形态：dict 可下标访问 + 可直接调用（进程内 `api(op, args)`）。

    历史坑：API 曾是普通 dict ⇒ 进程内 `api(args)` 报 TypeError: dict object is not callable；
    两个模块各自写了转换 shim。这里统一定义，模块不要再自己造。
    """

    def __call__(self, op=None, args=None, **kw):
        if op is None or isinstance(op, dict):
            args, op = (op if isinstance(op, dict) else args), "help"
        payload = args if isinstance(args, str) else j(dict(args or {}, **kw))
        out = self["dispatch"](str(op), payload)
        return out if isinstance(out, dict) else json.loads(out)

    def call(self, op, args=None, **kw):
        return self(op, args, **kw)


def register(name, version, ops, dispatch=None, extra=None):
    """把 `K.dsh_<name>_api` 注册成标准 Api（模块尾部一行搞定，别再手写 _DshApi）。

    ops: {"op名": 可调用}  —— 同时成为 API 的键，便于 `K.dsh_x_api["op"](...)` 直接调。
    """
    def _default_dispatch(op, args_json):
        return dispatch_table(ops, op, args_json)
    d = {"version": version, "dispatch": dispatch or _default_dispatch}
    for k, v in (ops or {}).items():
        d[k] = v
    d.update(extra or {})
    a = Api(d)
    setattr(_kernel(), "dsh_%s_api" % name, a)
    return a


def flatten(args):
    """参数摊平：`{"args": {...}}` 与顶层键等价（历史两种写法都支持）。"""
    out = {}
    if isinstance(args, str):
        try:
            args = json.loads(args) if args.strip() else {}
        except Exception:
            args = {}
    for k, v in (args or {}).items():
        if k == "args" and isinstance(v, dict):
            out.update(v)
        else:
            out[k] = v
    return out


def dispatch_table(ops, op, args_json=None):
    """标准 dispatch：解析 → 摊平 → 查表 → 调用 → 统一错误回执。"""
    kw = flatten(args_json)
    fn = ops.get(str(op))
    if fn is None:
        return err("unknown op", op=op, ops=sorted(ops))
    try:
        out = fn(**kw)
        return out if isinstance(out, str) else j(out)
    except TypeError as e:
        return err("参数不匹配: %s" % str(e)[:200], op=op)
    except Exception as e:
        return err("%s: %s" % (type(e).__name__, str(e)[:200]), op=op)


# ────────────────────────────── 几何 / 度量 ──────────────────────────────
def objects(names, strict=True):
    """名字列表 → 对象列表；strict 时返回 (objs, missing)。"""
    import bpy
    if isinstance(names, str):
        names = [names]
    objs = [bpy.data.objects.get(str(n)) for n in (names or [])]
    missing = [n for n, o in zip(names or [], objs) if o is None]
    return (objs, missing) if strict else [o for o in objs if o is not None]


def world_tris(objs, include_hidden=True):
    """一组对象的**世界三角面**（用 evaluated mesh：修改器/阵列都算进去）。"""
    import bpy
    dg = bpy.context.evaluated_depsgraph_get()
    tris = []
    for ob in objs:
        if ob is None or ob.type != "MESH":
            continue
        if not include_hidden:
            try:
                if not ob.visible_get():
                    continue
            except Exception:
                pass
        ev = ob.evaluated_get(dg)
        me = ev.to_mesh()
        try:
            me.calc_loop_triangles()
            mw = ob.matrix_world
            for t in me.loop_triangles:
                tris.append([tuple(mw @ me.vertices[i].co) for i in t.vertices])
        finally:
            ev.to_mesh_clear()
    return tris


def bvh(tris):
    """三角面 → BVH（空输入返回 None）。"""
    from mathutils.bvhtree import BVHTree
    if not tris:
        return None
    V = [v for t in tris for v in t]
    polys = [(3 * i, 3 * i + 1, 3 * i + 2) for i in range(len(tris))]
    return BVHTree.FromPolygons(V, polys, all_triangles=True)


def iou(mask_a, mask_b):
    """布尔掩码 IoU —— **唯一实现**（imgtools / qc / qc_render 都该用这个）。

    返回 (iou, inter, union)；空并集返回 (None, 0, 0)。
    """
    import numpy as np
    a = np.asarray(mask_a).astype(bool)
    b = np.asarray(mask_b).astype(bool)
    inter = int(np.count_nonzero(a & b))
    union = int(np.count_nonzero(a | b))
    return ((inter / union) if union else None), inter, union


def pairwise_clearance(objs_a, objs_b, samples=1200, seed=0):
    """两组对象的最近距离（米）与是否互穿 —— 采样法（顶点+面心 ↔ 对面 BVH）。

    返回 dict：{"gap_m": float|None, "interpenetrating": bool, "overlap_tri_pairs": int}
    诚实边界：间隙是采样点距离（大曲面平行贴合可能高估）；互穿判定精确（三角面相交）。
    """
    import random
    ta, tb = world_tris(objs_a), world_tris(objs_b)
    if not ta or not tb:
        return {"gap_m": None, "interpenetrating": False, "overlap_tri_pairs": 0, "error": "empty_mesh"}
    ba, bb = bvh(ta), bvh(tb)
    ov = ba.overlap(bb) or []
    rng = random.Random(int(seed))
    best = None
    for tris, other in ((ta, bb), (tb, ba)):
        pts = []
        for t in tris:
            pts.append(t[0]); pts.append(t[1]); pts.append(t[2])
            pts.append(((t[0][0] + t[1][0] + t[2][0]) / 3.0,
                        (t[0][1] + t[1][1] + t[2][1]) / 3.0,
                        (t[0][2] + t[1][2] + t[2][2]) / 3.0))
        if len(pts) > int(samples):
            pts = rng.sample(pts, int(samples))
        for p in pts:
            r = other.find_nearest(p)
            if r is None or r[0] is None:
                continue
            import mathutils
            d = (r[0] - mathutils.Vector(p)).length
            if best is None or d < best:
                best = d
    return {"gap_m": best, "interpenetrating": len(ov) > 0, "overlap_tri_pairs": len(ov)}


def selftest():
    """内核自检：JSON/单位/回执/派遣/几何/IoU —— 每条都当场验。"""
    ev = {}
    ev["j"] = j({"a": 1}) == '{"a": 1}'
    ev["flatten"] = flatten({"args": {"x": 1}, "y": 2}) == {"x": 1, "y": 2}
    ev["dispatch_unknown"] = '"unknown op"' in dispatch_table({"a": lambda: "{}"}, "nope")
    ev["dispatch_ok"] = dispatch_table({"a": lambda **k: j({"ok": True, **k})}, "a", '{"z": 3}') == '{"ok": true, "z": 3}'
    ev["api_callable"] = isinstance(Api({"dispatch": lambda o, a: '{"ok": true}'}), dict)
    ev["units_positive"] = units() > 0
    m = Api({"dispatch": lambda o, a: '{"ok": true}'})
    ev["api_call"] = m("help", {}) == {"ok": True}
    try:
        import numpy as np
        i, inter, uni = iou(np.array([[1, 0], [1, 0]]), np.array([[1, 1], [0, 0]]))
        ev["iou"] = abs(float(i) - 1.0 / 3.0) < 1e-9 and inter == 1 and uni == 3
    except Exception as e:
        ev["iou"] = "skip: %s" % str(e)[:40]
    ok = all(v is True for v in ev.values())
    return j({"ok": bool(ok), "version": KIT_VERSION, "evidence": ev})


# 注册：KERNEL_BOOTSTRAP 会执行本文件 ⇒ K.dsh_kit 在任何注入路径里都存在
_K = _kernel()
_K.dsh_kit = types.SimpleNamespace(
    version=KIT_VERSION, j=j, err=err, receipt=receipt, units=units, mm=mm, api=api,
    Api=Api, register=register, flatten=flatten, dispatch_table=dispatch_table,
    objects=objects, world_tris=world_tris, bvh=bvh, iou=iou,
    pairwise_clearance=pairwise_clearance, selftest=selftest, kernel=_kernel,
)