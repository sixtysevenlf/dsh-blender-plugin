# -*- coding: utf-8 -*-
"""DSH 网格体检（v0.9.1）+ 同名资源审计—— 把"脚本能判的对错"变成一等公民。

为什么有它（外部反馈 00-G-1 / 93 §4）：airframe-smith 自己写了 af_geom.validate()，**抓到翼盒 13 处自交、
前缘襟翼 39 处零面积面、P03 非流形 56 边、雷达罩环点数 60 vs 78 不一致**；没有它会把这些带进权威场景，返工更晚。
sys-smith / qc-smith 各自也造了一遍。这里收敛成通用能力。

v0.9.1（rifle-build《93 反馈》）：**文件级算子** —— 不再"什么都得先在会话里注册"。
    audit_overlap       跨件/跨文件 面-对重叠（pairs / pair_count / intersection_bbox / per_object）
    audit_interference  跨件/跨文件 交集体体积 mm³（Monte-Carlo 射线奇偶，不用 bpy boolean）
    audit_connectivity / audit_gate / audit_measure 全部新增 file=<.blend>（临时加载 → 跑 → finally 删净）
API 挂 K.dsh_audit_api；入口：
    blender_op(op="audit_mesh",  args={objects:["P01_Fuselage"], envelope:[min,max]})
    blender_op(op="audit_scene", args={envelope:[min,max]})
    blender_op(op="audit_overlap", args={file_a:".../05_barrel.blend", file_b:".../07_handguard.blend"})
    blender_op(op="audit_interference", args={file_a:".../08_magazine.blend", a:"08_magazine",
                                                   file_b:".../01_body_shell.blend", b:"01_body_shell",
                                                   samples:200000})
    blender_op(op="audit_connectivity", args={file:".../07_handguard.blend"})
    blender_op(op="audit_duplicates")
    blender_op(op="audit_purge_orphans")      # users==0 的灯/相机/网格/材质/图像
无头里也可 preload="audit" 后直接 K.dsh_audit_api["mesh"]({...})，或 K.dsh_audit_api("mesh", {...})（v0.9.1 起
dict 子类可调用，返回已解析的 dict；api["dispatch"](op, json_str) 仍返回 str —— 引擎契约不变）。

**硬规则**：对象集合里 0 个 mesh ⇒ ok=false（外部反馈的"静默空产出"事故就在这一条上）；
file= 打不开/没 mesh/全隐藏 ⇒ ok=false；超上限 ⇒ analyzed=false（"没跑"不许读成"没问题"）。
"""

import json as json

import bmesh as bmesh
import bpy
from mathutils import Vector

AUDIT_VERSION = 3


import sys as _sys_kit
_KIT = getattr(_sys_kit.modules.get("dsh_rt_kernel"), "dsh_kit", None)
if _KIT is None:
    raise RuntimeError("audit 需要共享内核 K.dsh_kit（由 KERNEL_BOOTSTRAP 注入）")


_j = _KIT.j  # 共享内核（原自带实现已删）
def _kernel():
    import sys
    return sys.modules.get("dsh_rt_kernel")


def _tris(me):
    # v2.6.1（性能实测，太空电梯 137k 面）：逐面 len(p.vertices) 走 RNA 要 54–59 ms；
    # 先把 loop_total 批量读进 list 再求和只要 14–15 ms（约 3.8×）。小网格保留原路径，避免固定开销。
    n = len(me.polygons)
    if n == 0:
        return 0
    if n >= 256:
        buf = [0] * n
        me.polygons.foreach_get("loop_total", buf)
        return sum(max(0, x - 2) for x in buf)
    total = 0
    for p in me.polygons:
        total += max(0, len(p.vertices) - 2)
    return total


def _bounds(objs):
    mn = Vector((1e30, 1e30, 1e30))
    mx = Vector((-1e30, -1e30, -1e30))
    n = 0
    for ob in objs:
        mw = ob.matrix_world
        for c in ob.bound_box:
            p = mw @ Vector((c[0], c[1], c[2]))
            for i in range(3):
                mn[i] = min(mn[i], p[i])
                mx[i] = max(mx[i], p[i])
        n += 1
    if n == 0:
        return None, None
    return mn, mx


def _pick(objects=None, scope=None):
    """挑对象：objects=[名字] 优先；否则 scope（集合名）里的；再否则全场景可见 mesh。

    容错（v0.8.10）：把 {"objects": [...]} 这样的包装字典直接传进来也能用 —— 别让参数作用域坑再咬人。
    """
    if isinstance(objects, dict):
        _w = objects
        objects = _w.get("objects") or _w.get("names") or _w.get("list")
        if scope is None:
            scope = _w.get("scope")
    if objects:
        want = [str(x) for x in (objects if isinstance(objects, (list, tuple)) else [objects])]
        got, missing = [], []
        for n in want:
            ob = bpy.data.objects.get(n)
            if ob is None:
                missing.append(n)
            else:
                got.append(ob)
        return got, missing, None
    coll = None
    if scope:
        coll = bpy.data.collections.get(str(scope))
        if coll is None:
            return [], [], "集合不存在：%s" % scope
        objs = [o for o in coll.all_objects if o.type == "MESH"]
    else:
        objs = [o for o in bpy.context.scene.objects if o.type == "MESH"]
    return objs, [], None


def _box_relation(a, b, tol):
    """两个 AABB 的关系：apart / a_in_b / b_in_a / same / cross。"""
    if any(a[1][k] < b[0][k] - tol or b[1][k] < a[0][k] - tol for k in range(3)):
        return 'apart'
    a_in_b = all(a[0][k] >= b[0][k] - tol and a[1][k] <= b[1][k] + tol for k in range(3))
    b_in_a = all(b[0][k] >= a[0][k] - tol and b[1][k] <= a[1][k] + tol for k in range(3))
    if a_in_b and b_in_a:
        return 'same'
    if a_in_b:
        return 'a_in_b'
    if b_in_a:
        return 'b_in_a'
    return 'cross'


def _shell_bvh(shell, cache, key):
    """壳内面的 BVH（包含性确认用）。建不出来返回 None —— 调用方必须降级，不许当通过。"""
    if key in cache:
        return cache[key]
    from mathutils.bvhtree import BVHTree
    verts, idx, polys = [], {}, []
    for f in shell['_faces']:
        loop = []
        for v in f.verts:
            i = idx.get(v)
            if i is None:
                i = len(verts)
                idx[v] = i
                verts.append(v.co.copy())
            loop.append(i)
        if len(loop) >= 3:
            polys.append(loop)
    bvh = BVHTree.FromPolygons(verts, polys, all_triangles=False, epsilon=0.0) if polys else None
    cache[key] = bvh
    return bvh


def _ray_parity_inside(bvh, point):
    """射线奇偶判点是否在壳内：三个轴向各投一条，多数票。返回 True/False（票数平分算 False）。"""
    votes = 0
    for d in (Vector((1.0, 0.0, 0.0)), Vector((0.0, 1.0, 0.0)), Vector((0.0, 0.0, 1.0))):
        o = point.copy()
        hits = 0
        for _ in range(512):
            loc = bvh.ray_cast(o, d)[0]
            if loc is None:
                break
            hits += 1
            o = loc + d * 1e-6
        if hits % 2 == 1:
            votes += 1
    return votes >= 2


def _shell_contains(outer, inner, cache, key, max_points=6):
    """包含性确认：inner 壳的若干个采样点是否**都**在 outer 壳内部。

    返回 True（确证嵌套）/ False（确证不是嵌套 —— 两壳表面互穿或只是 AABB 相交）。
    异常向上抛，由 _shell_normals 统一降级为 unknown。
    """
    bvh = _shell_bvh(outer, cache, key)
    if bvh is None:
        return False
    lo, hi = inner['bounds']
    pts = [Vector([(lo[k] + hi[k]) * 0.5 for k in range(3)])]     # AABB 中心
    # 只在这里才去收集顶点（嵌套候选是少数；热路径上不建顶点表）
    vs = {}
    for f in inner['_faces']:
        for v in f.verts:
            vs[v.index] = v.co
    idx = sorted(vs)
    step = max(1, len(idx) // max(1, int(max_points)))
    for i in idx[::step][:int(max_points)]:
        pts.append(vs[i])
    return all(_ray_parity_inside(bvh, p) for p in pts)


SHELL_MAX_FACES = 0              # **0 = 不设上限（默认全跑）**；验收不随规模退化。显式设 env DSH_SHELL_MAX_FACES 才启用上限
# 实测（Blender 5.2，本机）：155k 面 ≈ 0.67 s，618k 面 ≈ 11 s（连通分组 3.5 s + 逐面度量 7.7 s）。
# 超上限 ⇒ 一律 unknown/degraded（"未分析 ≠ 通过"），绝不静默拖住大场景；要强行分析就设 env。


def _shell_max_faces():
    import os
    try:
        v = int(os.environ.get("DSH_SHELL_MAX_FACES", "") or 0)
        return v if v > 0 else SHELL_MAX_FACES
    except Exception:
        return SHELL_MAX_FACES


def _shell_components(bm):
    """按边连通拆壳，逐壳算有符号体积/闭合性/绕向一致性/AABB（热路径：一趟走完，不建中间 set）。"""
    pending = set(bm.faces)
    shells = []
    while pending:
        seed = pending.pop()
        faces, stack = [seed], [seed]
        while stack:
            for edge in stack.pop().edges:
                for face in edge.link_faces:
                    if face in pending:
                        pending.remove(face)
                        faces.append(face)
                        stack.append(face)
        origin = faces[0].verts[0].co
        vol = 0.0
        closed = True
        consistent = True
        lo = [float("inf")] * 3
        hi = [float("-inf")] * 3
        for f in faces:
            vol += (f.calc_area() / 3.0) * f.normal.dot(f.calc_center_median() - origin)
            for e in f.edges:
                if len(e.link_faces) != 2:
                    closed = False
                elif not e.is_contiguous:
                    consistent = False
            for v in f.verts:
                co = v.co
                for k in range(3):
                    if co[k] < lo[k]:
                        lo[k] = co[k]
                    if co[k] > hi[k]:
                        hi[k] = co[k]
        eps = max(max(hi[i] - lo[i] for i in range(3)) ** 3 * 1e-12, 1e-30)
        shells.append(dict(faces=len(faces), signed_volume=vol, closed=closed,
                           winding_consistent=bool(closed and consistent), bounds=[lo, hi], eps=eps,
                           nested=False, relation='', state=('unknown' if not closed or abs(vol) <= eps else
                                                             'pass' if consistent and vol > 0 else 'fail'),
                           _faces=faces))
    return shells


def _shell_relations(shells):
    """壳两两 AABB 关系 + 包含性射线确认（预算/去重缓存）；返回 ambiguous 集合。"""
    dims = [max(s['bounds'][1][k] - s['bounds'][0][k] for k in range(3)) for s in shells]
    tol = max(dims) * 1e-9 if dims else 0.0
    ambiguous = set()
    cache, budget = {}, 64
    for i, a in enumerate(shells):
        for j in range(i):
            b = shells[j]
            rel = _box_relation(a['bounds'], b['bounds'], tol)
            if rel == 'apart':
                continue
            if rel == 'same' or rel == 'cross':
                shells[i]['relation'] = shells[j]['relation'] = rel
                ambiguous.update((i, j))
                continue
            inner_i, outer_j = (i, j) if rel == 'a_in_b' else (j, i)
            if budget <= 0:
                ambiguous.update((i, j))
                continue
            budget -= 1
            if _shell_contains(shells[outer_j], shells[inner_i], cache, outer_j):
                shells[inner_i]['nested'] = True
                shells[inner_i]['relation'] = 'nested'
            else:
                shells[i]['relation'] = shells[j]['relation'] = 'overlap-unconfirmed'
                ambiguous.update((i, j))
    return ambiguous


def _shell_verdict(shells, ambiguous):
    """由壳状态给出 (outward, state, reason)；不确定一律 unknown（→ degraded），绝不猜成 pass。"""
    def _self_confirmed(s):
        if not s['closed'] or not s['winding_consistent'] or abs(s['signed_volume']) <= s['eps']:
            return False
        if s['signed_volume'] > 0:
            return True
        return bool(s['nested'])

    ambiguous_unresolved = [i for i in ambiguous if not _self_confirmed(shells[i])]
    for i in ambiguous_unresolved:
        shells[i]['state'] = 'unknown'
    fails = [i for i, s in enumerate(shells)
             if i not in ambiguous_unresolved and not s['nested'] and s['closed'] and s['winding_consistent']
             and s['signed_volume'] < -s['eps']]
    if fails:
        for i in fails:
            shells[i]['state'] = 'fail'
        worst = sorted((i for i in fails), key=lambda i: shells[i]['signed_volume'])[:6]
        return False, 'fail', ("非嵌套壳整体朝内（signed_volume<0）：%s —— 聚合有符号体积会被"
                               "「大正向壳 + 小反向壳」抵消成正数，所以这里逐壳判"
                               % ", ".join("shell#%d(vol=%.6g, faces=%d)"
                                           % (i, shells[i]['signed_volume'], shells[i]['faces'])
                                           for i in worst))
    if ambiguous_unresolved:
        return None, 'unknown', ("%d 个壳 AABB 相交且自身朝向判不出来（未闭合/绕向不一致/零体积/未确证嵌套）"
                                 "—— 朝向语义 unresolved，不给通过结论" % len(ambiguous_unresolved))
    unknown = [i for i, s in enumerate(shells)
               if not s['closed'] or not s['winding_consistent'] or abs(s['signed_volume']) <= s['eps']]
    if unknown:
        for i in unknown:
            shells[i]['state'] = 'unknown'
        return None, 'unknown', ("壳闭合性/绕向/体积无法判定：%s"
                                 % ", ".join("shell#%d" % i for i in unknown[:6]))
    for s in shells:
        s['state'] = 'nested' if s['nested'] else 'pass'
    if len(shells) > 1:
        return True, 'pass_multi_shell', ("多壳（%d 个连通壳），每个壳的朝向都可确证；"
                                          "多壳本身不是缺陷（合法空腔已标 nested）" % len(shells))
    return True, 'pass', ''


def _shell_normals(bm):
    """逐连通壳判朝向（v0.9.6 复审修复）。

    为什么不能只看整体有符号体积：`bm.calc_volume(signed=True)` 把所有壳**相加** ——
    一个大的正向壳（+8）配一个分离的小反向壳（−1），总和仍是 +7 > 0，于是"法线朝外"被误报为真。
    这里改成**逐壳**判，只有"可确证是独立实体"的壳才下结论：

      * 壳 = 边连通的面集合；各自算有符号体积 / 闭合性 / 绕向一致性 / AABB。
      * AABB 互相**分离**的壳 ⇒ 独立实体：闭合 + 绕向一致 + 体积 ≤ 0 ⇒ **确定朝内（fail）**。
      * AABB **互相包含**的壳 ⇒ 疑似嵌套（合法空腔 / 壳内独立件）：射线奇偶**确认**真的在里面；
        确证 ⇒ 嵌套壳豁免（合法空腔不许误报成反向）；确认不成立 ⇒ unknown。
      * AABB 相交但互不包含（互穿 / 部分重叠 / 重合副本）⇒ 光靠朝向分不出 ⇒ unknown。

    不确定一律 unknown（→ audit_mesh 判 verdict=degraded），**绝不猜成 pass**。
    返回 (normals_outward, state, shells, reason)：outward ∈ {True, False, None}。
    """
    nf = len(bm.faces)
    cap = _shell_max_faces()
    if cap > 0 and nf > cap:
        return None, 'unknown', [], ("面数 %d 超过逐壳朝向分析上限 %d（env DSH_SHELL_MAX_FACES 可调）"
                                     "—— 未做逐壳分析，不给通过结论" % (nf, cap))
    shells = _shell_components(bm)
    if not shells:
        return None, 'unknown', shells, 'no faces'
    if len(shells) > 256:
        return None, 'unknown', shells, 'shell_count exceeds 256; containment not analyzed'
    ambiguous = _shell_relations(shells)
    outward, state, reason = _shell_verdict(shells, ambiguous)
    return outward, state, shells, reason



def _shells_brief(shells, limit=12):
    """把壳明细压成回执里的小块（去掉内部字段 _faces/_verts）。"""
    rows = []
    for i, s in enumerate(shells or []):
        if i >= int(limit):
            break
        rows.append({"shell": i, "faces": s.get("faces"), "signed_volume": round(float(s.get("signed_volume") or 0.0), 6),
                     "closed": bool(s.get("closed")), "winding_consistent": bool(s.get("winding_consistent")),
                     "nested": bool(s.get("nested")), "relation": s.get("relation") or "",
                     "state": s.get("state")})
    return rows
#         * 大网格：超上限一律 analyzed=false；**未分析绝不能读成已连通**
#         * 单位：所有 mm 口径阈值按场景 scale_length 换算，返回里带 units 块
# ============================================================================

VISIBLE_SPAN_FRACTION = 0.01
MICRO_GAP_MM = 0.3
CONN_MAX_TRIS = 0                # **0 = 不设上限（默认全跑）**；显式 env DSH_CONN_MAX_TRIS / 参数 max_tris 才启用




__all__ = [name for name in list(globals()) if not name.startswith("__")]
