# -*- coding: utf-8 -*-
"""DSH 网格体检（v0.9.1 / C1·C2）+ 同名资源审计（D7）—— 把"脚本能判的对错"变成一等公民。

为什么有它（外部反馈 00-G-1 / 93 §4）：airframe-smith 自己写了 af_geom.validate()，**抓到翼盒 13 处自交、
前缘襟翼 39 处零面积面、P03 非流形 56 边、雷达罩环点数 60 vs 78 不一致**；没有它会带进权威场景、P4 才返工。
sys-smith / qc-smith 各自也造了一遍。这里收敛成通用能力。

v0.9.1 补 C1/C2（rifle-build《93 反馈》，定级 P2）：**文件级算子** —— 不再"什么都得先在会话里注册"。
    audit_overlap       跨件/跨文件 面-对重叠（pairs / pair_count / intersection_bbox / per_object）
    audit_interference  跨件/跨文件 交集体体积 mm³（Monte-Carlo 射线奇偶，不用 bpy boolean）
    audit_connectivity / audit_gate / audit_measure 全部新增 file=<.blend>（临时加载 → 跑 → finally 删净）
API 挂 K.dsh_audit_api；入口：
    blender_rt_plan(op="audit_mesh",  args={objects:["P01_Fuselage"], envelope:[min,max]})
    blender_rt_plan(op="audit_scene", args={envelope:[min,max]})
    blender_rt_plan(op="audit_overlap", args={file_a:".../05_barrel.blend", file_b:".../07_handguard.blend"})
    blender_rt_plan(op="audit_interference", args={file_a:".../08_magazine.blend", a:"08_magazine",
                                                   file_b:".../01_body_shell.blend", b:"01_body_shell",
                                                   samples:200000})
    blender_rt_plan(op="audit_connectivity", args={file:".../07_handguard.blend"})
    blender_rt_plan(op="audit_duplicates")
    blender_rt_plan(op="audit_purge_orphans")      # users==0 的灯/相机/网格/材质/图像
无头里也可 preload="audit" 后直接 K.dsh_audit_api["mesh"]({...})，或 K.dsh_audit_api("mesh", {...})（v0.9.1 起
dict 子类可调用，返回已解析的 dict；api["dispatch"](op, json_str) 仍返回 str —— 引擎契约不变）。

**硬规则**：对象集合里 0 个 mesh ⇒ ok=false（外部反馈的"静默空产出"事故就在这一条上）；
file= 打不开/没 mesh/全隐藏 ⇒ ok=false；超上限 ⇒ analyzed=false（"没跑"不许读成"没问题"）。
"""
import json

import bpy
import bmesh
from mathutils import Vector

AUDIT_VERSION = 3


import sys as _sys_kit
_KIT = getattr(_sys_kit.modules.get("dsh_rt_kernel"), "dsh_kit", None)
if _KIT is None:
    raise RuntimeError("audit 需要共享内核 K.dsh_kit（由 KERNEL_BOOTSTRAP 注入）")


_j = _KIT.j  # 共享内核（原自带实现已删，见 S1）
def _kernel():
    import sys
    return sys.modules.get("dsh_rt_kernel")


def _tris(me):
    n = 0
    for p in me.polygons:
        n += max(0, len(p.vertices) - 2)
    return n


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


SHELL_MAX_FACES = 300000         # 逐壳朝向分析的面上限（env DSH_SHELL_MAX_FACES 可覆盖）
# 实测（Blender 5.2，本机）：155k 面 ≈ 0.67 s，618k 面 ≈ 11 s（连通分组 3.5 s + 逐面度量 7.7 s）。
# 超上限 ⇒ 一律 unknown/degraded（"未分析 ≠ 通过"），绝不静默拖住大场景；要强行分析就设 env。


def _shell_max_faces():
    import os
    try:
        v = int(os.environ.get("DSH_SHELL_MAX_FACES", "") or 0)
        return v if v > 0 else SHELL_MAX_FACES
    except Exception:
        return SHELL_MAX_FACES


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
    if nf > cap:
        # 大网格直接降级（与 connectivity 的 max_tris 同一纪律：未分析 ≠ 通过，也绝不阻塞大场景）
        return None, 'unknown', [], ("面数 %d 超过逐壳朝向分析上限 %d（env DSH_SHELL_MAX_FACES 可调）"
                                     "—— 未做逐壳分析，不给通过结论" % (nf, cap))
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
        # 热路径（v0.9.6 复审）：**不要**为每个壳建 verts/edges 的 set 再三次遍历 —— 实测 155k 面
        # 时那种写法比 audit_mesh 原有的全部 O(F) 计数还慢一个数量级（832ms vs 84ms）。这里一趟走完：
        #   体积用散度定理的面形式  Σ (A/3)·n̂·(c−o) （与 bm.calc_volume(signed=True) 同号同值，逐面 C 调用）
        #   闭合/绕向一致性直接在面上查边；AABB 直接吃面上的顶点
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
    if not shells:
        return None, 'unknown', shells, 'no faces'
    # Bound quadratic work: unresolved is preferable to blocking a large scene.
    if len(shells) > 256:
        return None, 'unknown', shells, 'shell_count exceeds 256; containment not analyzed'
    dims = [max(s['bounds'][1][k] - s['bounds'][0][k] for k in range(3)) for s in shells]
    tol = max(dims) * 1e-9 if dims else 0.0
    ambiguous = set()
    cache, budget = {}, 64                      # 包含性确认的射线预算（超了 → unknown，不猜）
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
            # rel='a_in_b' ⇒ a（shells[i]）在 b（shells[j]）里面 ⇒ inner=i, outer=j
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
    for i in ambiguous:
        shells[i]['state'] = 'unknown'
    fails = [i for i, s in enumerate(shells)
             if i not in ambiguous and not s['nested'] and s['closed'] and s['winding_consistent']
             and s['signed_volume'] < -s['eps']]
    if fails:
        for i in fails:
            shells[i]['state'] = 'fail'
        worst = sorted((i for i in fails), key=lambda i: shells[i]['signed_volume'])[:6]
        return False, 'fail', shells, ("非嵌套壳整体朝内（signed_volume<0）：%s —— 聚合有符号体积会被"
                                       "「大正向壳 + 小反向壳」抵消成正数，所以这里逐壳判"
                                       % ", ".join("shell#%d(vol=%.6g, faces=%d)"
                                                   % (i, shells[i]['signed_volume'], shells[i]['faces'])
                                                   for i in worst))
    if ambiguous:
        return None, 'unknown', shells, ("%d 组壳的 AABB 相交但无法确证嵌套/空腔（互穿、部分重叠或重合副本）"
                                         "—— 朝向语义 unresolved，不给通过结论" % len(ambiguous))
    # 没有确定的反向壳、也没有歧义：只剩"闭合性/绕向/零体积"判不出来的壳
    unknown = [i for i, s in enumerate(shells)
               if not s['closed'] or not s['winding_consistent'] or abs(s['signed_volume']) <= s['eps']]
    if unknown:
        for i in unknown:
            shells[i]['state'] = 'unknown'
        return None, 'unknown', shells, ("壳闭合性/绕向/体积无法判定：%s"
                                         % ", ".join("shell#%d" % i for i in unknown[:6]))
    for s in shells:
        # 嵌套壳（合法空腔 / 壳内独立件）不是缺陷：标注 'nested'，不参与 fail
        s['state'] = 'nested' if s['nested'] else 'pass'
    return True, 'pass', shells, ''


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


def audit_mesh(objects=None, scope=None, envelope=None, eps_area=1e-9, self_intersect=True,
               max_issues=20, min_dist=1e-6, summary_only=False,
               island_split=False, min_island_verts=0):
    """v0.9.6（现场反馈 #1）：island_split=true 时把自交按**连通岛**分栏（同岛/跨岛），
    min_island_verts=N 时忽略"任一侧面属于 <N 顶点小岛"的相交 —— 铆钉/小五金按工艺压入宿主
    必然相交，那是良性的；这一栏就是现场手工做过的归因实验（肩甲 1640 对里大部分是这种）。"""
    """单对象/一组对象的网格体检。envelope=[x0,y0,z0,x1,y1,z1] 时给 out_of_bounds。"""
    objs, missing, err = _pick(objects, scope)
    if err:
        return _j({"ok": False, "error": err})
    objs = [o for o in objs if o.type == "MESH"]
    if not objs:
        # ★ 硬规则：0 个 mesh 必须失败（外部反馈的"静默空产出"）
        return _j({"ok": False, "error": "体检对象里 0 个 mesh", "objects": objects, "scope": scope,
                   "hint": "多半是 build 空跑了：确认对象名/集合名，或先跑 audit_scene 看场景里有什么",
                   "missing": missing})
    env = None
    if envelope:
        try:
            e = [float(x) for x in envelope]
            env = (Vector((min(e[0], e[3]), min(e[1], e[4]), min(e[2], e[5]))),
                   Vector((max(e[0], e[3]), max(e[1], e[4]), max(e[2], e[5]))))
        except Exception as exc:
            return _j({"ok": False, "clean": False, "state": "error", "analyzed": False,
                       "error": "invalid envelope: %s" % exc})

    parts = []
    total = {"objects": 0, "tris": 0, "boundary_edges": 0, "nonmanifold_edges": 0, "degenerate_faces": 0,
             "loose_verts": 0, "self_intersections": 0, "normals_inverted": 0, "out_of_bounds": 0,
             "empty_objects": 0, "loose_edges": 0, "normals_unknown": 0, "normals_checked": 0,
             "analysis_incomplete": 0}
    for ob in objs:
        me = ob.data
        # 空网格（0 边）会让 me.edges[0] 直接 IndexError —— 建了对象还没填面的常见状态，
        # 不该让体检崩掉，而该报"empty"（v0.9.6 修）。
        _n_edges = len(me.edges)
        if _n_edges == 0:
            _loose_edges = 0
        elif hasattr(me.edges[0], "link_faces"):
            _loose_edges = int(sum(1 for e in me.edges if not e.link_faces))
        else:
            _loose_edges = None
        info = {"name": ob.name, "verts": len(me.vertices), "polys": len(me.polygons), "tris": _tris(me),
                "loose_edges": _loose_edges, "empty": bool(len(me.vertices) == 0)}
        bm = bmesh.new()
        try:
            bm.from_mesh(me)
            bm.verts.ensure_lookup_table()
            bm.faces.ensure_lookup_table()
            info["boundary_edges"] = int(sum(1 for e in bm.edges if len(e.link_faces) == 1))
            info["nonmanifold_edges"] = int(sum(1 for e in bm.edges if not e.is_manifold))
            info["degenerate_faces"] = int(sum(1 for f in bm.faces if f.calc_area() <= float(eps_area)))
            info["loose_verts"] = int(sum(1 for v in bm.verts if not v.link_edges))
            # v0.9.6（复审修复）：Blender 5.2 的 MeshEdge **没有** link_faces（上面 hasattr 永远假）⇒
            # loose_edges 恒为 None、汇总恒为 0（"没查"被当成"没有"）。改从 bmesh 实算。
            if info["loose_edges"] is None:
                info["loose_edges"] = int(sum(1 for e in bm.edges if not e.link_faces))
            try:
                vol = float(bm.calc_volume(signed=True))
            except Exception:
                vol = None
            info["signed_volume"] = (round(vol, 6) if vol is not None else None)
            info["closed"] = bool(info["boundary_edges"] == 0 and info["nonmanifold_edges"] == 0)
            # v0.9.6（复审修复）：逐连通壳判朝向。原来 `normals_outward = vol > 0` 用的是**全体壳体积之和**，
            # 「同对象里大正向壳 + 分离的小反向壳」总和仍为正 ⇒ 反向壳被吞掉、误报 normals_outward=true。
            try:
                _out, _nstate, _shells, _nreason = _shell_normals(bm)
            except Exception as _ne:
                _out, _nstate, _shells = None, "unknown", []
                _nreason = "壳朝向分析异常（%s: %s）⇒ unknown，不给通过结论" % (type(_ne).__name__, str(_ne)[:90])
            if not info["closed"]:
                _out, _nstate = None, "n/a"        # 开放网格：整体朝向无意义
                _nreason = ""
            info["normals_state"] = _nstate
            info["normals_outward"] = _out
            info["normals_shell_count"] = len(_shells or [])
            info["normals_reason"] = _nreason or ""
            if _nstate != "n/a":
                info["normals_shells"] = _shells_brief(_shells)
            info["self_intersections"] = 0
            info["self_intersection_pairs"] = []
            info["self_intersections_analyzed"] = False
            info["self_intersections_state"] = "skipped"      # 未请求 / 异常 / pass / fail
            info["self_intersection_error"] = None
            if self_intersect and len(bm.faces) > 0:
                try:
                    from mathutils.bvhtree import BVHTree
                    bvh = BVHTree.FromBMesh(bm)
                    seen = set()
                    hit = []
                    for a, b in (bvh.overlap(bvh) or []):
                        if a >= b:
                            continue
                        key = (a, b)
                        if key in seen:
                            continue
                        seen.add(key)
                        fa, fb = bm.faces[a], bm.faces[b]
                        va = set(v.index for v in fa.verts)
                        vb = set(v.index for v in fb.verts)
                        if va & vb:
                            continue                     # 相邻面共享顶点 → 不算自交
                        if (fa.calc_center_median() - fb.calc_center_median()).length < float(min_dist):
                            continue                     # 重合面/薄壳抖动 → 由 degenerate 报
                        hit.append([a, b, [round(x, 4) for x in fa.calc_center_median()]])
                    info["self_intersections"] = len(hit)
                    info["self_intersection_pairs"] = hit[:int(max_issues)]
                    info["self_intersections_analyzed"] = True
                    info["self_intersections_state"] = "fail" if hit else "pass"
                    if island_split or int(min_island_verts or 0) > 0:
                        # 连通岛：边连接的顶点并查集（一个"岛"= 一块连通的壳；铆钉/小五金各自成岛）
                        nv = len(bm.verts)
                        par = list(range(nv))
                        def _find(x):
                            while par[x] != x:
                                par[x] = par[par[x]]
                                x = par[x]
                            return x
                        for e in bm.edges:
                            a0, b0 = e.verts[0].index, e.verts[1].index
                            ra, rb = _find(a0), _find(b0)
                            if ra != rb:
                                par[rb] = ra
                        size = {}
                        for i in range(nv):
                            r = _find(i)
                            size[r] = size.get(r, 0) + 1
                        isl_of_face = {}
                        for f in bm.faces:
                            isl_of_face[f.index] = _find(f.verts[0].index)
                        thr = int(min_island_verts or 0)
                        same = cross = filt = small_isl = 0
                        for rr in size.values():
                            if thr and rr < thr:
                                small_isl += 1
                        kept = []
                        for rec in hit:
                            ia = isl_of_face.get(rec[0])
                            ib = isl_of_face.get(rec[1])
                            sza = size.get(ia, 0)
                            szb = size.get(ib, 0)
                            is_same = (ia == ib)
                            if is_same:
                                same += 1
                            else:
                                cross += 1
                            drop = bool(thr and (sza < thr or szb < thr))
                            if drop:
                                filt += 1
                            else:
                                kept.append(rec + [bool(is_same), int(sza), int(szb)])
                        info["islands"] = {"count": len(size), "small_islands": small_isl,
                                           "min_island_verts": thr}
                        info["self_intersections_same_island"] = same
                        info["self_intersections_cross_island"] = cross
                        info["self_intersections_filtered_small_island"] = filt
                        info["self_intersections_kept"] = len(kept)
                        info["self_intersection_pairs"] = kept[:int(max_issues)] if max_issues else []
                        info["island_note"] = ("同岛自交=同一块壳自己扎自己（往往是建模事故）；跨岛=两块壳互穿"
                                               "（铆钉压入宿主属这一类，按工艺可能是良性的）")
                except Exception as e:
                    # 自交检查**异常**不得读成"没有自交"（原来是静默 0 ⇒ clean=true 的假通过）
                    info["self_intersect_error"] = "%s: %s" % (type(e).__name__, str(e)[:100])
                    info["self_intersections_analyzed"] = False
                    info["self_intersections_state"] = "error"
        finally:
            bm.free()
        # 世界 AABB / 越界
        mn, mx = None, None
        for c in ob.bound_box:
            p = ob.matrix_world @ Vector((c[0], c[1], c[2]))
            mn = p.copy() if mn is None else Vector((min(mn[i], p[i]) for i in range(3)))
            mx = p.copy() if mx is None else Vector((max(mx[i], p[i]) for i in range(3)))
        info["aabb"] = {"min": [round(float(x), 4) for x in mn], "max": [round(float(x), 4) for x in mx]}
        info["dimensions"] = [round(float(x), 4) for x in ob.dimensions]
        if env:
            out = [i for i in range(3) if mn[i] < env[0][i] - 1e-6 or mx[i] > env[1][i] + 1e-6]
            info["out_of_bounds"] = bool(out)
            info["out_of_bounds_axes"] = out
            if out:
                total["out_of_bounds"] += 1
        # 逐对象三态：fail（确有缺陷）/ degraded（没查完或判不出来）/ pass
        _incomplete = (not info.get("self_intersections_analyzed")) or info.get("normals_state") == "unknown"
        info["analysis_incomplete"] = bool(_incomplete)
        if any(info.get(k) for k in ("boundary_edges", "nonmanifold_edges", "degenerate_faces", "loose_verts",
                                     "loose_edges", "self_intersections", "empty")):
            info["state"] = "fail"
        elif info.get("normals_outward") is False:
            info["state"] = "fail"
        elif _incomplete:
            info["state"] = "degraded"
        else:
            info["state"] = "pass"
        # 汇总
        total["objects"] += 1
        total["tris"] += info["tris"]
        for k in ("boundary_edges", "nonmanifold_edges", "degenerate_faces", "loose_verts", "loose_edges",
                  "self_intersections"):
            total[k] += int(info.get(k) or 0)
        if info.get("normals_outward") is False:
            total["normals_inverted"] += 1
        if info.get("normals_state") == "unknown":
            total["normals_unknown"] += 1
        if info.get("normals_state") in ("pass", "fail"):
            total["normals_checked"] += 1
        if info.get("analysis_incomplete"):
            total["analysis_incomplete"] += 1
        if info.get("empty"):
            total["empty_objects"] += 1
        parts.append(info)

    # v0.9.6（复审修复）：
    #   ① loose_verts / loose_edges 也是缺陷 —— 原来 loose_verts 不计入 bad ⇒ 「孤立点 > 0 但 clean=true」。
    #   ② clean 的含义收紧为"**查完了而且没缺陷**"：自交检查被跳过/抛异常、或壳朝向判不出来（unknown）时，
    #      clean=false + state=degraded —— "没查"和"查了没有"必须是两个结论（不再假通过）。
    bad = (total["boundary_edges"] or total["nonmanifold_edges"] or total["degenerate_faces"]
           or total["loose_verts"] or total["loose_edges"] or total["self_intersections"]
           or total["normals_inverted"] or total["out_of_bounds"] or total["empty_objects"])
    _incomplete = int(total.get("analysis_incomplete") or 0)
    if bad:
        state, reason = "fail", "确有缺陷（见 totals 里非 0 的计数字段）"
    elif _incomplete:
        state, reason = "degraded", ("有 %d 个对象没查完/判不出来（自交检查被跳过或抛异常、壳朝向 unknown）"
                                     "—— 未分析不得读作通过" % _incomplete)
    else:
        state, reason = "pass", ""
    mn, mx = _bounds(objs)
    # v0.9.6（D3 · 瘦身）：summary_only=true 只回判据字段（去掉明细列表），供验证者/门用
    if summary_only:
        _keep = ("name", "verts", "polys", "tris", "boundary_edges", "nonmanifold_edges",
                 "degenerate_faces", "loose_verts", "loose_edges", "closed", "normals_outward", "normals_state",
                 "normals_shell_count", "self_intersections", "self_intersections_analyzed",
                 "self_intersections_state", "self_intersections_same_island", "self_intersections_cross_island",
                 "self_intersections_filtered_small_island", "self_intersections_kept", "islands",
                 "signed_volume", "empty", "out_of_bounds", "state")
        parts = [{k: v for k, v in p.items() if k in _keep} for p in parts]
    payload = {"ok": True, "clean": bool(not bad and state == "pass"), "state": state, "verdict": state,
               "reason": reason, "count": len(objs), "missing": missing,
               "totals": total, "objects": parts,
               "analysis": {"self_intersections": ("analyzed" if self_intersect else "skipped"),
                            "normals": ("unknown" if total.get("normals_unknown") else
                                        ("checked" if total.get("normals_checked") else "n/a")),
                            "incomplete_objects": _incomplete},
               "aabb": ({"min": [round(float(x), 4) for x in mn], "max": [round(float(x), 4) for x in mx]} if mn else None),
               "envelope": ([[round(float(x), 4) for x in env[0]], [round(float(x), 4) for x in env[1]]] if env else None),
               "note": "判据：boundary/nonmanifold/degenerate/loose_verts/loose_edges/self_intersections/"
                       "normals_inverted/out_of_bounds/empty 任一非 0 → clean=false 且 state=fail；"
                       "normals_outward 逐**连通壳**判（聚合有符号体积会被大正向壳+小反向壳抵消成正数），"
                       "只对闭合网格有意义（closed=true 时才给），判不出来时给 normals_state=unknown；"
                       "自交检查被跳过（self_intersect=false）或抛异常 ⇒ state=degraded，clean=false —— 不假通过"}
    if state == "degraded":
        payload["warn"] = "DEGRADED ≠ 通过：" + reason
        if not self_intersect:
            payload["hint"] = "要完整结论请用 self_intersect=true 重跑（本门的自交检查被显式跳过）"
    if summary_only:
        payload["slim"] = {"summary_only": True}
    elif len(_j(payload)) > 6000:
        payload["hint"] = ("回执较大（%d 字符）：要精简用 summary_only=true（只回判据字段），"
                           "或 audit_scene(top_k=N) 只看最脏的几个" % len(_j(payload)))
    return _j(payload)


def audit_scene(envelope=None, limit=40, eps_area=1e-9, summary_only=False, top_k=None,
                self_intersect=False):
    """全场景体检：先给你"哪几个对象最脏"，再按需 audit_mesh 深挖。

    v0.9.6（D3 · 瘦身）：summary_only=true 只回判据字段；top_k=N 只回最脏的 N 个（默认沿用 limit）。
    v0.9.6（复审修复）：自交检查**默认不跑**（保持"一次便宜的初筛"这个性能契约），但这时结论只能是
        **degraded**（clean=false）——"跳过检查"不许读成"没问题"。要一条能当门用的完整结论，
        传 self_intersect=true（全场景逐对象跑一遍 BVH 自交，代价按面数走）。
    """
    objs = [o for o in bpy.context.scene.objects if o.type == "MESH"]
    if not objs:
        return _j({"ok": False, "error": "场景里 0 个 mesh（build 空跑？）"})
    rows = []
    r = json.loads(audit_mesh(objects=[o.name for o in objs], envelope=envelope, eps_area=eps_area,
                              self_intersect=bool(self_intersect), max_issues=0))
    tot = r.get("totals", {})
    for info in r.get("objects", []):
        score = (info.get("boundary_edges") or 0) + 3 * (info.get("nonmanifold_edges") or 0) + \
                2 * (info.get("degenerate_faces") or 0) + 2 * (info.get("self_intersections") or 0) + \
                (info.get("loose_verts") or 0) + (info.get("loose_edges") or 0)
        rows.append({"name": info["name"], "tris": info["tris"], "boundary_edges": info.get("boundary_edges"),
                     "nonmanifold_edges": info.get("nonmanifold_edges"),
                     "degenerate_faces": info.get("degenerate_faces"), "closed": info.get("closed"),
                     "loose_verts": info.get("loose_verts"), "loose_edges": info.get("loose_edges"),
                     "self_intersections": info.get("self_intersections"),
                     "normals_outward": info.get("normals_outward"), "normals_state": info.get("normals_state"),
                     "state": info.get("state"), "score": score, "aabb": info.get("aabb")})
    rows.sort(key=lambda x: -x["score"])
    k = int(top_k) if top_k is not None else int(limit)
    rows_out = rows[:max(0, k)]
    # 场景级三态：任何对象 fail ⇒ fail；否则任何"降级"（含主动跳过自交检查）⇒ degraded；否则 pass
    states = [x.get("state") for x in rows]
    if r.get("state") == "fail" or "fail" in states:
        state = "fail"
    elif (not self_intersect) or r.get("state") == "degraded" or "degraded" in states:
        state = "degraded"
    else:
        state = "pass"
    if summary_only:
        _keep = ("name", "score", "boundary_edges", "nonmanifold_edges", "degenerate_faces",
                 "loose_verts", "loose_edges", "self_intersections", "normals_outward", "normals_state",
                 "state", "tris", "closed")
        rows_out = [{kk: vv for kk, vv in row.items() if kk in _keep} for row in rows_out]
    reason = ""
    if state == "degraded":
        reason = ("初筛跳过了自交检查" if not self_intersect else "") + \
                 ("；" if not self_intersect else "") + \
                 ("%d 个对象判不出来（见 worst[].normals_state/state）" % len([x for x in states if x == "degraded"])
                  if any(x == "degraded" for x in states) else "")
        reason = reason or "未查完"
    payload = {"ok": True, "count": len(objs), "totals": tot, "worst": rows_out,
               "clean": bool(r.get("clean") and state == "pass" and self_intersect),
               "state": state, "verdict": state, "reason": reason,
               "self_intersections_analyzed": bool(self_intersect), "aabb": r.get("aabb"),
               "slim": {"summary_only": bool(summary_only), "top_k": k, "rows_returned": len(rows_out)},
               "note": "worst 按 score=boundary+3*nonmanifold+2*degenerate+2*selfint+loose 排序；"
                       "深挖用 audit_mesh(objects=[...])；"
                       "本 op 默认**不跑自交检查** ⇒ verdict=degraded（clean=false）——"
                       "要能当门用的完整结论请传 self_intersect=true；回执太大时加 summary_only=true / top_k=N"}
    if state == "degraded":
        payload["warn"] = "DEGRADED ≠ 通过：" + reason
    return _j(payload)


def _mat_sig(ob):
    """材质签名：节点类型 + 关键输入默认值（粗指纹，用于发现"同名分叉"）。"""
    try:
        nt = ob.node_tree
        if nt is None:
            return "none"
        items = []
        for nd in sorted(nt.nodes, key=lambda n: n.name):
            key = str(nd.type) + ":" + str(getattr(nd, "blend_type", "")) + ":" + str(getattr(nd, "operation", ""))
            vals = []
            for inp in getattr(nd, "inputs", []) or []:
                dv = getattr(inp, "default_value", None)
                try:
                    if hasattr(dv, "__len__"):
                        vals.append(",".join("%.4f" % float(x) for x in dv))
                    else:
                        vals.append("%.4f" % float(dv))
                except Exception:
                    vals.append("-")
            items.append(key + "[" + "|".join(vals[:8]) + "]")
        return "|".join(items)[:600]
    except Exception:
        return "err"


def audit_duplicates(limit=30, top_k=None):
    """同名资源审计（D7）：把 Foo / Foo.001 这类分叉分组，并比较内容是否不同。"""
    k = int(top_k) if top_k is not None else int(limit)
    import hashlib
    out = {"ok": True, "materials": [], "meshes": [],
           "note": "同名分叉多为「先到先得」覆盖造成；若内容不同，说明有两份不一样的资源在混用"}
    # 材质
    groups = {}
    for m in bpy.data.materials:
        base = str(m.name).split(".")[0]
        groups.setdefault(("m", base), []).append(m)
    for (kind, base), items in sorted(groups.items()):
        if len(items) < 2:
            continue
        sigs = {}
        for m in items:
            sigs.setdefault(_mat_sig(m), []).append(m.name)
        out["materials"].append({"base": base, "variants": [m.name for m in items], "distinct_signatures": len(sigs),
                                 "users": [int(m.users) for m in items]})
    # 网格
    g2 = {}
    for me in bpy.data.meshes:
        base = str(me.name).split(".")[0]
        g2.setdefault(base, []).append(me)
    for base, items in sorted(g2.items()):
        if len(items) < 2:
            continue
        sigs = {}
        for me in items:
            h = hashlib.md5()
            h.update(("%d/%d" % (len(me.vertices), len(me.polygons))).encode())
            for v in me.vertices[:2000]:
                h.update(("%.5f,%.5f,%.5f;" % (v.co[0], v.co[1], v.co[2])).encode())
            sigs.setdefault(h.hexdigest()[:12], []).append(me.name)
        out["meshes"].append({"base": base, "variants": [m.name for m in items], "distinct_signatures": len(sigs),
                              "users": [int(m.users) for m in items]})
    out["count"] = {"materials": len(out["materials"]), "meshes": len(out["meshes"])}
    # v0.9.6（D3）：limit/top_k 原来收了参数却没用（静默失效）—— 现在真正生效并回报被截断的总数
    for _key in ("materials", "meshes"):
        if len(out[_key]) > k:
            out[_key + "_total"] = len(out[_key])
            out[_key] = out[_key][:k]
    out["slim"] = {"top_k": k, "materials": len(out["materials"]), "meshes": len(out["meshes"])}
    out["ok"] = True
    return _j(out)


def purge_orphans():
    """清理 users==0 的 datablock（D1 配套：孤儿灯/相机/网格/材质/图像）。"""
    removed = {}
    targets = [("lights", bpy.data.lights), ("cameras", bpy.data.cameras), ("meshes", bpy.data.meshes),
               ("materials", bpy.data.materials), ("images", bpy.data.images), ("node_groups", bpy.data.node_groups),
               ("collections", bpy.data.collections)]
    for name, coll in targets:
        n = 0
        for db in list(coll):
            try:
                if int(db.users) == 0:
                    coll.remove(db, do_unlink=True)
                    n += 1
            except Exception:
                pass
        if n:
            removed[name] = n
    return _j({"ok": True, "removed": removed, "total": sum(removed.values()),
               "left": {k: len(getattr(bpy.data, k)) for k, _ in targets}})


def audit_selftest():
    """合成自检：造一个"已知脏"的网格，看各项计数是否如实。"""
    me = bpy.data.meshes.new("DSH_AUDIT_SELFTEST")
    bm = bmesh.new()
    bmesh.ops.create_grid(bm, x_segments=2, y_segments=2, size=1.0)     # 开放网格 → 有边界边
    bmesh.ops.create_cube(bm, size=0.5)                                  # 闭合立方体
    bm.to_mesh(me)
    bm.free()
    ob = bpy.data.objects.new("DSH_AUDIT_SELFTEST", me)
    bpy.context.scene.collection.objects.link(ob)
    try:
        r = json.loads(audit_mesh(objects=[ob.name], self_intersect=False))
        empty = json.loads(audit_mesh(objects=["NoSuchObject"]))
        return _j({"ok": True, "boundary_edges": r["totals"]["boundary_edges"],
                   "tris": r["totals"]["tris"], "clean": r["clean"],
                   "empty_must_fail": (empty.get("ok") is False),
                   "expect": "boundary_edges>0（开放网格）且 clean=false；audit_mesh(不存在的名字) 必须 ok=false"})
    finally:
        try:
            bpy.data.objects.remove(ob, do_unlink=True)
            bpy.data.meshes.remove(me, do_unlink=True)
        except Exception:
            pass


# ============================================================================
# v0.9.0（Procedura 融合 · 批次 1）：装配级判据 —— 连通分量 / 微隙 / 归因 / 漂移 / 测量包
# ----------------------------------------------------------------------------
# 来源：SpatiaOS/Procedura（MIT）：src/mesh/connectivity.ts、src/mesh/floater-attribution.ts、
#       src/mesh/chamfer.ts、src/tools/module_context.ts。这里把「跨 OpenSCAD 模块的 STL 判据」
#       改写成「跨 bpy 对象的装配判据」，阈值与三态纪律原样保留：
#         * 可见浮块：最长 bbox 边 ≥ 全模型最长边 1%（VISIBLE_SPAN_FRACTION）—— 严重度只看它，不看体积
#         * 微隙：贴而未重合（默认 0.3 mm）—— 报告但永不阻塞门（否则几十条微缝会让门永远红）
#         * 大网格：超上限一律 analyzed=false；**未分析绝不能读成已连通**
#         * 单位：所有 mm 口径阈值按场景 scale_length 换算，返回里带 units 块
# ============================================================================

VISIBLE_SPAN_FRACTION = 0.01
MICRO_GAP_MM = 0.3
CONN_MAX_TRIS = 4000000          # numpy 连通路径的实际上限；env DSH_CONN_MAX_TRIS 可覆盖


def _conn_max_tris():
    import os
    try:
        v = int(os.environ.get("DSH_CONN_MAX_TRIS", "") or 0)
        return v if v > 0 else CONN_MAX_TRIS
    except Exception:
        return CONN_MAX_TRIS


def _units():
    """场景单位换算 → (米/单位, 毫米/单位)。mm 口径的阈值一律经它换算，绝不硬套。"""
    m = 1.0
    try:
        m = float(bpy.context.scene.unit_settings.scale_length) or 1.0
    except Exception:
        m = 1.0
    return m, m * 1000.0


def _r(x, n=5):
    try:
        return round(float(x), n)
    except Exception:
        return x


def _objs_for(objects, scope, include_hidden=False):
    """_pick 的包装：再按 hide_render/hide_viewport 过滤（与契约层 _scope_objects 同口径）。"""
    if isinstance(objects, dict):
        w = objects
        include_hidden = bool(w.get("include_hidden", include_hidden))
        objects = w.get("objects") or w.get("names") or w.get("list")
        if scope is None:
            scope = w.get("scope")
    got, missing, err = _pick(objects, scope)
    out = got if include_hidden else [o for o in got if not (o.hide_render or o.hide_viewport)]
    return out, missing, err


# ============================================================================
# v0.9.1（C2）：file= 临时加载 —— 把"会话绑定"解开（上报一律用文件里的原名）
# ----------------------------------------------------------------------------
# 来源：rifle-build《93 反馈》C1/C2（P2）："check_* / audit_* 都要先在会话里注册组件，批处理 12 个
# part 时不便"。口径全部取本机实测（Blender 5.2，2026-09）：
#   * bpy.data.libraries.load(link=False) 是**数据 API**：无头/GUI 都能用（bpy.ops.wm.append 要 GUI 上下文）；
#   * 加载进来的对象**不属于任何集合** —— 不链到临时集合，evaluated_get 拿不到几何（tris=0）；
#   * dst.objects 会被 Blender **就地填充**成对象 → 必须传副本进去，才留得住文件里的名字；
#   * 会话里已有同名对象时 Blender 会加 .001/.002 后缀 → 上报一律经 name_of 映射回文件原名；
#   * 打不开的文件抛 OSError（先 os.path.isfile 判一次，再 try 兜底，原始错误照抄进 error）；
#   * 零残留要删四样：对象 / 临时集合 / 本次新造且 users==0 的数据块 / 本次新出现的库条目。
# ============================================================================

_PURGE_KINDS = ("meshes", "materials", "images", "node_groups", "lights", "cameras", "textures",
                "curves", "armatures", "actions")
_COUNT_KINDS = ("objects", "collections", "libraries") + _PURGE_KINDS
_REPORT_KINDS = ("objects", "collections", "libraries", "meshes", "materials", "images")   # 返回体里只报这 6 个


def _nm(ob, name_of=None):
    """对象显示名：file= 模式下用文件里的原名（重名追加时 Blender 改的是会话内名字，不能直接上报）。"""
    if name_of:
        n = name_of.get(str(ob.name))
        if n:
            return str(n)
    return str(ob.name)


def _named_boxes(objs, boxes, name_of=None):
    """_world_tris 的 boxes（键=会话内名字）→ 按上报名重排（file= 模式给文件原名）。"""
    out = {}
    for ob in objs:
        b = boxes.get(ob.name)
        if b is not None:
            out[_nm(ob, name_of)] = b
    return out


def _id_snapshot():
    out = {}
    for k in _COUNT_KINDS:
        coll = getattr(bpy.data, k, None)
        out[k] = set(coll) if coll is not None else set()
    return out


def _id_counts():
    return {k: len(getattr(bpy.data, k)) for k in _COUNT_KINDS if getattr(bpy.data, k, None) is not None}


def _exc_body(e, tag=""):
    """统一的"分析炸了"失败体（ok:false，绝不当成功）；带一小段 traceback 便于定位。"""
    import traceback
    return {"ok": False, "error": "分析失败%s：%s: %s" % (("（%s）" % tag) if tag else "",
                                                         type(e).__name__, str(e)[:200]),
            "traceback": traceback.format_exc()[-600:]}


def _noop_cleanup():
    return {"ok": True, "removed": {}, "failed": [], "note": "没有临时加载任何东西"}


def _load_file_objects(path, names=None, include_hidden=False):
    """把另一个 .blend 里的对象**临时**追加进当前会话 → (objs, cleanup, info)。

    * 只取对象（不取场景/集合/世界）；names 非空就只取这些名字，文件里没有的名字进 info.missing。
    * 可见性：include_hidden=False 时按 hide_render/hide_viewport 过滤（与 _objs_for 同口径）。
    * cleanup() **幂等**，返回本次实际删掉的清单（返回体的 cleanup 字段就是它）；
      删：本次加载的对象（含非 mesh）/临时集合/本次新造且 users==0 的数据块/本次新出现的库条目。
    * info["_name_of"] = {会话内名字 → 文件原名}，调用方 pop 出来做上报映射（别塞进 JSON）。
    * 空文件 / 文件里没有（可见）mesh / 打不开 → info.ok=False + error（原始错误照抄）。
    """
    import os
    src = str(path or "")
    before = _id_snapshot()
    before_counts = _id_counts()
    state = {"done": False, "report": None}
    loaded, coll, objs, name_of = [], None, [], {}
    # 「本次加载造出来的东西」——必须在 load 一结束就抓下来：同一次 op 里可能加载**两个**文件
    # （audit_overlap/interference 的 A、B 两侧），两个 cleanup 都在 finally 里跑，
    # 靠快照 diff 当场锁定归属，才不会把对方的东西删掉（第一轮自检就在这里翻过车）。
    mine = {"objects": [], "libraries": []}
    mine_ids = {k: [] for k in _PURGE_KINDS}

    def _capture_mine():
        for k in ("objects", "libraries"):
            cur = getattr(bpy.data, k, None)
            if cur is None:
                continue
            mine[k] = [x for x in cur if x not in before.get(k, set())]
        for k in _PURGE_KINDS:
            cur = getattr(bpy.data, k, None)
            if cur is None:
                continue
            mine_ids[k] = [x for x in cur if x not in before.get(k, set())]

    info = {"ok": False, "path": src, "source": "file:%s" % src, "requested": None, "loaded": 0,
            "mesh": 0, "non_mesh": 0, "missing": [], "hidden_skipped": 0, "renamed": [],
            "tmp_collection": None, "error": None, "_name_of": None}

    def cleanup():
        """finally 里必跑（幂等）：把这次临时加载的一切删干净，并回报删了什么。"""
        if state["done"]:
            return state["report"]
        state["done"] = True
        rep = {"ok": True, "removed": {}, "failed": [], "counts_before": before_counts,
               "counts_after": None,
               "note": "临时加载的收尾（无论成败都跑）：对象/临时集合/本次新造孤儿数据/库条目"}
        rm = {}
        # ① 本次加载进来的对象（loaded + 现场 diff）——去重，且**只删自己的**
        victims, seen = [], set()
        for ob in list(loaded) + list(mine["objects"]):
            try:
                key = int(ob.as_pointer())
            except Exception:
                key = id(ob)
            if key in seen:
                continue
            seen.add(key)
            victims.append(ob)
        names_rm = []
        for ob in victims:
            try:
                nm_ob = str(ob.name)
            except Exception:
                nm_ob = "?"
            try:
                bpy.data.objects.remove(ob, do_unlink=True)
                names_rm.append(nm_ob)
            except Exception as e:
                rep["failed"].append("object %s: %s" % (nm_ob, str(e)[:80]))
        if names_rm:
            rm["objects"] = names_rm
        # ② 临时集合
        if coll is not None:
            cname = str(coll.name)
            try:
                bpy.data.collections.remove(coll, do_unlink=True)
                rm["collections"] = [cname]
            except Exception as e:
                rep["failed"].append("collection %s: %s" % (cname, str(e)[:80]))
        # ③ 本次新造的数据块（users==0 才删；还有用户在用的记进 kept，不硬删）
        kept = {}
        for kind in _PURGE_KINDS:
            c2 = getattr(bpy.data, kind, None)
            if c2 is None:
                continue
            for db in list(mine_ids.get(kind) or []):
                if db in before.get(kind, set()):
                    continue
                try:
                    users = int(db.users)
                except Exception:
                    continue
                if users == 0:
                    dname = str(db.name)
                    try:
                        c2.remove(db, do_unlink=True)
                        rm.setdefault(kind, []).append(dname)
                    except Exception as e:
                        rep["failed"].append("%s %s: %s" % (kind, dname, str(e)[:80]))
                else:
                    kept.setdefault(kind, []).append(str(db.name))
        # ④ 本次出现的库条目（实测：link=False 追加也会留下一条 users==1 的 Library，可以直接 remove）
        for lib in list(mine["libraries"]):
            if lib in before.get("libraries", set()):
                continue
            lname = str(lib.name)
            try:
                bpy.data.libraries.remove(lib, do_unlink=True)
                rm.setdefault("libraries", []).append(lname)
            except Exception as e:
                rep["failed"].append("library %s: %s" % (lname, str(e)[:80]))
        rep["removed"] = rm
        if kept:
            rep["kept"] = kept
        rep["counts_before"] = {k: before_counts.get(k) for k in _REPORT_KINDS}
        rep["counts_after"] = {k: _id_counts().get(k) for k in _REPORT_KINDS}
        rep["ok"] = not rep["failed"]
        state["report"] = rep
        return rep

    if not src or not os.path.isfile(src):
        info["error"] = "文件不存在或不是文件：%s" % src
        return [], cleanup, info
    try:
        with bpy.data.libraries.load(src, link=False) as (src_ctx, dst):
            all_names = [str(n) for n in src_ctx.objects]
            if names:
                keep = set(str(x) for x in names)
                want = [n for n in all_names if n in keep]
                info["missing"] = sorted(keep - set(all_names))
            else:
                want = list(all_names)
            info["requested"] = want
            dst.objects = list(want)          # ★ 传副本：Blender 就地把这个列表填成对象
    except Exception as e:
        _capture_mine()                       # 半途失败也可能留下半边数据 → 照抓照删
        info["error"] = "打不开/加载失败：%s: %s" % (type(e).__name__, str(e)[:200])
        return [], cleanup, info
    _capture_mine()                           # ★ 就在这一刻锁定"本次加载造出来的东西"
    got = list(dst.objects)
    loaded = [o for o in got if o is not None]
    if len(got) == len(info["requested"]):
        name_of = {str(o.name): str(info["requested"][i]) for i, o in enumerate(got) if o is not None}
        info["renamed"] = [{"session": str(o.name), "file": str(info["requested"][i])}
                           for i, o in enumerate(got)
                           if o is not None and str(o.name) != str(info["requested"][i])]
    else:
        info["name_map_note"] = ("加载返回 %d 条 ≠ 请求 %d 条 → 不假设顺序，名字按会话内名字上报"
                                 % (len(got), len(info["requested"])))
    info["loaded"] = len(loaded)
    meshes = [o for o in loaded if o.type == "MESH"]
    info["mesh"] = len(meshes)
    info["non_mesh"] = len(loaded) - len(meshes)
    objs = [o for o in meshes if include_hidden or not (o.hide_render or o.hide_viewport)]
    info["hidden_skipped"] = len(meshes) - len(objs)
    info["_name_of"] = name_of
    if not loaded:
        info["error"] = "文件里没有对象：%s" % src
        return [], cleanup, info
    if not meshes:
        info["error"] = "文件里没有 mesh 对象：%d 个对象全是 %s" % (
            len(loaded), "/".join(sorted(set(str(o.type) for o in loaded))))
        return [], cleanup, info
    if not objs:
        info["error"] = "文件里 %d 个 mesh 全被隐藏（include_hidden=false 时不参与分析）" % len(meshes)
        return [], cleanup, info
    try:
        coll = bpy.data.collections.new("DSH_AUDIT_FILE_TMP")
        bpy.context.scene.collection.children.link(coll)
        for o in objs:
            coll.objects.link(o)
        bpy.context.view_layer.update()      # 让 depsgraph 看见新对象（否则 evaluated_get 给不出几何）
        info["tmp_collection"] = str(coll.name)
    except Exception as e:
        info["error"] = "临时集合/链接失败：%s: %s" % (type(e).__name__, str(e)[:200])
        return [], cleanup, info
    info["ok"] = True
    info["mesh_names"] = [_nm(o, name_of) for o in objs]
    return objs, cleanup, info


def _file_names(objects=None, scope=None):
    """file= 模式下的筛选名：objects（列表/字符串/包装 dict）优先，其次把 scope 当**对象名**用。

    为什么集合名在这里只当对象名：append 只把对象搬过来，**集合成员关系不跟着来** ——
    要按文件内集合筛，请先把集合也 append 进会话，再走会话侧（不给 file=）。
    """
    if isinstance(objects, dict):
        w = objects
        objects = w.get("objects") or w.get("names") or w.get("list")
        if scope is None:
            scope = w.get("scope")
    if objects:
        return [str(x) for x in (objects if isinstance(objects, (list, tuple, set)) else [objects])]
    if scope:
        return [str(scope)]
    return None


def _objs_with_optional_file(objects, scope, include_hidden, file):
    """C2 三个 op 的统一入口：file= 时临时加载该文件（names 用来在文件内筛），否则走 _objs_for（会话）。

    返回 dict：{objs, missing, err, name_of, source, load, cleanup}
    —— cleanup 由调用方在 finally 里执行（无论成功失败都要跑），它的返回值就是返回体的 cleanup 字段。
    """
    if not file:
        objs, missing, err = _objs_for(objects, scope, include_hidden)
        return {"objs": objs, "missing": missing, "err": err, "name_of": None,
                "source": None, "load": None, "cleanup": None}
    objs, cleanup, info = _load_file_objects(file, _file_names(objects, scope), include_hidden)
    name_of = info.pop("_name_of", None)
    return {"objs": objs, "missing": info.get("missing") or [],
            "err": None if info.get("ok") else info.get("error"),
            "name_of": name_of, "source": info.get("source"), "load": info, "cleanup": cleanup}


def _file_extras(res, S, crep):
    """把 file= 侧的来源/加载摘要/清理报告补进返回体（老键一个不动，全是加法）。"""
    if S.get("source"):
        res["source"] = S["source"]
    if S.get("load") is not None:
        res["file_load"] = S["load"]
    if crep is not None:
        res["cleanup"] = crep
    return res


def _box_fields(lo, hi, mm_per_unit, digits=5):
    """世界盒 → {min,max,size} + mm 版（返回体里"数值带单位"的统一格式）。"""
    lo = [float(x) for x in lo]
    hi = [float(x) for x in hi]
    size = [hi[k] - lo[k] for k in range(3)]
    return {"min": [_r(x, digits) for x in lo], "max": [_r(x, digits) for x in hi],
            "size": [_r(x, digits) for x in size],
            "min_mm": [_r(x * mm_per_unit, 4) for x in lo], "max_mm": [_r(x * mm_per_unit, 4) for x in hi],
            "size_mm": [_r(x * mm_per_unit, 4) for x in size]}


def _world_tris(objs):
    """求值后的三角面 → 世界坐标。返回 (tris(N,3,3) float64, owner(N,) int32, boxes{名:(lo,hi)|None})。"""
    import numpy as np
    dg = bpy.context.evaluated_depsgraph_get()
    tris_all, owner_all, boxes = [], [], {}
    for k, ob in enumerate(objs):
        boxes.setdefault(ob.name, None)
        obe = None
        try:
            obe = ob.evaluated_get(dg)
            me = obe.to_mesh()
            if me is None:
                continue
            me.calc_loop_triangles()
            n = len(me.loop_triangles)
            if n == 0:
                obe.to_mesh_clear()
                continue
            v = np.empty(len(me.vertices) * 3, dtype=np.float64)
            me.vertices.foreach_get("co", v)
            v = v.reshape(-1, 3)
            idx = np.empty(n * 3, dtype=np.int32)
            me.loop_triangles.foreach_get("vertices", idx)
            mw = np.array(obe.matrix_world, dtype=np.float64)
            t = v[idx.reshape(-1, 3)] @ mw[:3, :3].T + mw[:3, 3]
            tris_all.append(t)
            owner_all.append(np.full(n, k, dtype=np.int32))
            p = t.reshape(-1, 3)
            boxes[ob.name] = (p.min(axis=0), p.max(axis=0))
            obe.to_mesh_clear()
        except Exception as e:
            boxes[ob.name] = None
            try:
                if obe is not None:
                    obe.to_mesh_clear()
            except Exception:
                pass
    if not tris_all:
        return None, None, boxes
    return np.concatenate(tris_all), np.concatenate(owner_all), boxes


def _components(tris, precision=5):
    """按共享顶点（四舍五入去重）做连通分量 → (comp_of_tri, 顶点数)。"""
    import numpy as np
    flat = tris.reshape(-1, 3)
    uniq, inv = np.unique(np.round(flat, int(precision)), axis=0, return_inverse=True)
    f = inv.reshape(-1, 3).astype(np.int64)
    nv = int(uniq.shape[0])
    e = np.concatenate([f[:, [0, 1]], f[:, [1, 2]], f[:, [2, 0]]], axis=0)
    e.sort(axis=1)
    e = np.unique(e, axis=0)
    parent = np.arange(nv, dtype=np.int64)
    a, b = e[:, 0], e[:, 1]
    for _ in range(64):
        before = parent.copy()
        np.minimum.at(parent, b, parent[a])
        np.minimum.at(parent, a, parent[b])
        parent = parent[parent]
        if np.array_equal(parent, before):
            break
    _u, comp = np.unique(parent[f[:, 0]], return_inverse=True)
    return comp.reshape(-1).astype(np.int64), nv


def _confirm_micro_with_mesh(tris, comp, cid, radius_units, tree_cache=None, max_pts=400, big_factor=50.0):
    """把「bbox 判 micro」的分量拿到网格上复核：真最近距离到底有没有小于阈值。

    为什么必须复核（本插件与上游的关键差异）：上游是**单网格合并体**，分量之间的 bbox 几乎不重叠，
    bbox 间隙是有信息量的；而我们这里每个部件是独立对象，密集装配里 bbox 普遍互相重叠 → bbox 间隙恒为 0
    → 全被判 micro → 门一律放行（fail-open）。所以多对象场景必须补一次三角级确认：
      对分量采样 ≤max_pts 个点，用全局 BVH 的 find_nearest_range(半径=阈值) 找命中；
      命中三角形属于**别的分量** → 确认贴上了（真 micro）；半径内一无所获 → 其实是浮块（bbox 骗人）。
    返回 (confirmed_micro, true_gap_units|None, note)；true_gap 只在"更大半径里找到了别的分量"时给值。
    """
    import numpy as np
    from mathutils.bvhtree import BVHTree
    sel = np.nonzero(comp == int(cid))[0]
    if len(sel) == 0:
        return False, None, "空分量"
    # 采样必须落在**面上**（面积加权），不能只取顶点：大平面之间的 0.2 mm 缝，顶点可能相距几米
    uniq = _sample(tris[sel], int(max_pts), np.random.default_rng(0))
    if len(uniq) > int(max_pts):
        step = max(1, len(uniq) // int(max_pts))
        uniq = uniq[::step][:int(max_pts)]
    if tree_cache is not None and tree_cache.get("tree") is None:
        V = tris.reshape(-1, 3).tolist()
        polys = [(3 * i, 3 * i + 1, 3 * i + 2) for i in range(len(tris))]
        tree_cache["tree"] = BVHTree.FromPolygons(V, polys, all_triangles=True)
    tree = (tree_cache or {}).get("tree")
    if tree is None:
        return None, None, "BVH 未建立"
    r = float(radius_units)
    r_big = r * float(big_factor)
    hit_other, best = False, None
    for p in uniq:
        pt = (float(p[0]), float(p[1]), float(p[2]))
        for (loc, nor, idx, dist) in tree.find_nearest_range(pt, r):
            if int(comp[int(idx)]) != int(cid):
                hit_other = True
                break
        if hit_other:
            break
        for (loc, nor, idx, dist) in tree.find_nearest_range(pt, r_big):   # 顺手量一下到底漂多远
            if int(comp[int(idx)]) != int(cid):
                d = float(dist)
                if best is None or d < best:
                    best = d
    if hit_other:
        return True, best, "网格确认：阈值半径内有其它分量的面（真贴上）"
    return False, best, "网格复核：阈值半径内没有其它分量的面 → bbox 判的 micro 不成立%s" % (
        "" if best is None else "（更大半径内最近 %.6f 单位）" % best)


def _snap_pair_mesh(tris, comp, cid, tree_cache=None, max_pts=400):
    """网格级最近点对：返回 (delta 向量[指向最近邻], 距离, note)。

    bbox 级间隙在多对象场景会读成 0（所以 snap 必须能走网格级），这也是"贴而未重合"唯一可靠的收口依据。
    """
    import numpy as np
    from mathutils.bvhtree import BVHTree
    sel = np.nonzero(comp == int(cid))[0]
    if len(sel) == 0:
        return None, None, "空分量"
    pts = _sample(tris[sel], int(max_pts), np.random.default_rng(0))   # 面上采样（见 _confirm_micro_with_mesh）
    if len(pts) > int(max_pts):
        pts = pts[::max(1, len(pts) // int(max_pts))][:int(max_pts)]
    if tree_cache is not None and tree_cache.get("tree") is None:
        V = tris.reshape(-1, 3).tolist()
        polys = [(3 * i, 3 * i + 1, 3 * i + 2) for i in range(len(tris))]
        tree_cache["tree"] = BVHTree.FromPolygons(V, polys, all_triangles=True)
    tree = (tree_cache or {}).get("tree")
    if tree is None:
        return None, None, "BVH 未建立"
    best = None
    for p in pts:
        pp = (float(p[0]), float(p[1]), float(p[2]))
        hit = tree.find_nearest(pp)
        if hit is None or hit[0] is None:
            continue
        loc, nor, idx, dist = hit
        if int(comp[int(idx)]) == int(cid):
            continue
        if best is None or float(dist) < best[0]:
            best = (float(dist), (float(loc[0]), float(loc[1]), float(loc[2])), pp)
    if best is None:
        return None, None, "网格近邻里没有别的分量（孤立件）"
    return [best[1][k] - best[2][k] for k in range(3)], best[0], "网格级最近点对（点到面）"


def _ov(a, b):
    """两个 bbox 的重叠体积（a/b 都是 (lo, hi)）。"""
    v = 1.0
    for k in range(3):
        d = min(float(a[1][k]), float(b[1][k])) - max(float(a[0][k]), float(b[0][k]))
        if d <= 0:
            return 0.0
        v *= d
    return v


def _boxvol(b):
    return max(0.0, float(b[1][0]) - float(b[0][0])) * max(0.0, float(b[1][1]) - float(b[0][1])) \
        * max(0.0, float(b[1][2]) - float(b[0][2]))


def _conn_analyze(objs, precision=5, visible_frac=VISIBLE_SPAN_FRACTION, micro_gap_mm=MICRO_GAP_MM,
                  max_tris=None, limit=24, name_of=None):
    """连通分量分析内核：返回 dict（含 numpy 细节，供 snap 复用；公开 op 只暴露格式化结果）。

    name_of（v0.9.1，可选）：{会话内名字 → 上报名}。file= 模式下加载进来的对象会被 Blender 改名
    （重名加 .001），而反馈要的是"对象名与文件内一致" → 所有对外名字都经它映射；会话模式下为 None，
    行为与老版本逐字节一致。
    """
    import numpy as np
    m_per_unit, mm_per_unit = _units()
    cap = int(max_tris or _conn_max_tris())
    tris, owner, boxes = _world_tris(objs)
    boxes = _named_boxes(objs, boxes, name_of)
    out = {"units": {"m_per_unit": _r(m_per_unit, 6), "mm_per_unit": _r(mm_per_unit, 6),
                     "scene_unit": "m" if abs(m_per_unit - 1.0) < 1e-12 else "scaled",
                     "micro_gap_mm": _r(micro_gap_mm, 4),
                     "micro_gap_units": _r(micro_gap_mm / mm_per_unit, 8)},
           "objects": len(objs), "object_names": [_nm(o, name_of) for o in objs], "boxes": boxes,
           "gap_note": "gap 是 bbox 级 → 低估真实间隙（贴而未重合会读成 0），偏向判 micro 放行（与上游同口径）"}
    if tris is None or len(tris) == 0:
        return dict(out, analyzed=False, reason="范围内没有三角面（空产出必须失败）", tris=0)
    if len(tris) > cap:
        return dict(out, analyzed=False, tris=int(len(tris)),
                    reason="三角面 %d 超过上限 %d（env DSH_CONN_MAX_TRIS 可调）→ 跳过分析" % (len(tris), cap),
                    note="未分析 ≠ 已连通：门保持 degraded，不给通过结论")
    p_all = tris.reshape(-1, 3)
    out["aabb"] = _box_fields(p_all.min(axis=0), p_all.max(axis=0), mm_per_unit)
    comp, nv = _components(tris, precision)
    ncomp = int(comp.max()) + 1
    stats = []
    for c in range(ncomp):
        sel = np.nonzero(comp == c)[0]
        t = tris[sel]
        p = t.reshape(-1, 3)
        lo, hi = p.min(axis=0), p.max(axis=0)
        size = hi - lo
        vol = abs(float(np.einsum("ij,ij->i", t[:, 0], np.cross(t[:, 1], t[:, 2])).sum() / 6.0))
        stats.append({"comp": c, "tris": int(len(sel)), "min": [_r(x) for x in lo], "max": [_r(x) for x in hi],
                      "size": [_r(x) for x in size], "max_dim": _r(size.max()), "volume": _r(vol, 8),
                      "owners": {_nm(objs[int(i)], name_of): int((owner[sel] == i).sum())
                                 for i in np.unique(owner[sel])}})
    all_lo = np.array([s["min"] for s in stats]).min(axis=0)
    all_hi = np.array([s["max"] for s in stats]).max(axis=0)
    model_span = float((all_hi - all_lo).max())
    for s in stats:
        s["span_fraction"] = _r(s["max_dim"] / model_span, 6) if model_span > 0 else 0.0
    stats.sort(key=lambda s: s["volume"], reverse=True)
    for rank, s in enumerate(stats):
        s["rank"] = rank
    # 邻域 bbox 间隙（mm）：每个分量到最近其它分量的距离
    gaps = []
    for i, s in enumerate(stats):
        best, bj = None, None
        for j, t2 in enumerate(stats):
            if i == j:
                continue
            d2 = 0.0
            for k in range(3):
                g = max(0.0, max(s["min"][k] - t2["max"][k], t2["min"][k] - s["max"][k]))
                d2 += g * g
            if best is None or d2 < best:
                best, bj = d2, j
        gaps.append({"gap_units": _r((best or 0.0) ** 0.5, 8), "gap_mm": _r(((best or 0.0) ** 0.5) * mm_per_unit, 5),
                     "nearest_rank": (stats[bj]["rank"] if bj is not None else None)})
    # 分类在下面完成：attached=False → 真浮块；True → 阈值内有邻居（容忍）；None → 未确认（降级）
    for s, g in zip(stats, gaps):
        s["gap_mm"] = g["gap_mm"]
        s["gap_units"] = g["gap_units"]
        s["bbox_micro"] = bool(g["gap_mm"] < float(micro_gap_mm))
        s["visible"] = bool(s["span_fraction"] >= float(visible_frac))
        s["attached"] = None
        s["true_gap_units"] = None
        s["mesh_confirmed"] = None
    # 判据（v0.9.0 修正）：不再用「最大体积分量=主体、其余都是浮块」—— 两个等大零件时那条规则会
    # 随便挑一个当浮体（自检抓到过）。物理上要问的是：**这个分量有没有跟别的分量相接**。
    micro_units = float(micro_gap_mm) / mm_per_unit
    mesh_mode = "mesh(BVH)" if len(tris) <= BVH_MAX_TRIS else "bbox-only"
    tree_cache = {}
    for s in stats:
        if ncomp == 1:
            # v0.9.6（现场反馈 #2 收口）：浮块的定义是"这个分量与其它分量都不相接"——
            # 整个范围**只有 1 个连通分量**时这条判据无从成立（内含的壳要么共享顶点、要么**正好相贴**
            # 被 _components 按 precision 焊接在一起）。原先会把"整体焊成一块"误报成 1 个可见浮块：
            # 实测两块正好相贴（gap=0.00 mm）⇒ 旧逻辑 fail；现在是 pass。
            s["attached"] = True
            s["evidence"] = "single-body"
            s["confirm_note"] = ("范围内只有 1 个连通分量（%d 三角面）→ 不存在「与其它分量不相接」，不判浮块；"
                                 "要查内部壳是否贴合，用 audit_mesh(island_split=true)" % len(tris))
            continue
        if not s["bbox_micro"]:
            s["attached"] = False
            s["evidence"] = "bbox"
            s["confirm_note"] = "bbox 间隙 %.4f mm 已超阈值 %.2f mm → 漂着（无需网格复核）" % (s["gap_mm"], micro_gap_mm)
            continue
        if mesh_mode == "bbox-only":
            s["attached"] = None
            s["evidence"] = "bbox-only"
            s["confirm_note"] = "面数 %d 超过 %d，跳过网格复核 → 不能当作已贴上（门按降级处理）" % (len(tris), BVH_MAX_TRIS)
            continue
        try:
            conf, true_gap, note = _confirm_micro_with_mesh(tris, comp, s["comp"], micro_units, tree_cache)
        except Exception as e:
            conf, true_gap, note = None, None, "网格复核失败：%s" % str(e)[:120]
        s["mesh_confirmed"] = conf
        s["true_gap_units"] = true_gap
        s["confirm_note"] = note
        s["evidence"] = "bbox+mesh"
        s["attached"] = conf
    floaters = [s for s in stats if s["attached"] is False]
    tolerated = [s for s in stats if s["attached"] is True]
    unconfirmed = [s for s in stats if s["attached"] is None]
    # 间隙分布：口径（micro_gap_mm）该给多少，取决于你要什么 —— 单一实体/3D 打印用 0.3mm（上游口径）；
    # 带设计间隙的装配件按自己的工艺给（1–2mm 很常见）。这里把分布摊开，让调用者自己选口径。
    def _gap_mm_of(s):
        if s.get("true_gap_units") is not None:
            return float(s["true_gap_units"]) * mm_per_unit
        return float(s.get("gap_mm") or 0.0)
    hist = {}
    for s in stats:
        g = _gap_mm_of(s)
        key = ("<%.2f mm(相接口径)" % micro_gap_mm) if g < micro_gap_mm else (
            "%.2f-1 mm" % micro_gap_mm if g < 1 else ("1-5 mm" if g < 5 else ("5-20 mm" if g < 20 else ">20 mm")))
        hist[key] = hist.get(key, 0) + 1
    fgaps = [_gap_mm_of(s) for s in floaters]
    gap_range = (round(min(fgaps), 4), round(max(fgaps), 4)) if fgaps else None
    for s in stats:      # 归因只对真浮块做（要"改哪个对象"）
        if s["attached"] is not False:
            continue
        # 归因：浮块 bbox 与每个对象 bbox 的重叠率（Procedura: matchFloatersToBoxes）
        fbox = (s["min"], s["max"])
        fvol = _boxvol(fbox) or 1.0
        scored = []
        for name, b in boxes.items():
            if b is None:
                continue
            frac = _ov(fbox, (list(b[0]), list(b[1]))) / fvol
            if frac > 0.01:
                scored.append((frac, name, _boxvol((list(b[0]), list(b[1])))))
        scored.sort(reverse=True)
        best = scored[0] if scored else None
        s["attribution"] = {
            "object": best[1] if best else None,
            "confidence": _r(min(1.0, best[0]) if best else 0.0, 4),
            "object_fraction": _r(min(1.0, fvol / best[2]) if best and best[2] > 0 else 0.0, 4),
            "also_overlaps": [n for f, n, _ in scored[1:] if f >= 0.2],
            "contains": s["owners"],
            "note": "object_fraction≈1 ⇒ 该浮块就是整个对象（可整件平移）；很小 ⇒ 只是焊接体的一块（平移会撕开焊点）",
        }
    visible_f = [s for s in floaters if s["visible"]]
    return dict(out, analyzed=True, tris=int(len(tris)), verts=int(nv), components=stats, floaters=floaters,
                tolerated=tolerated, unconfirmed=unconfirmed,
                _raw={"tris": tris, "comp": comp},     # 给 snap 复用（公开 op 的 JSON 里不会出现）
                obj_tris={_nm(objs[k], name_of): int((owner == k).sum()) for k in range(len(objs))},
                micro_confirm=mesh_mode,
                floater_count=len(floaters), visible_floater_count=len(visible_f),
                real_floater_count=len(visible_f),
                micro_floater_count=len([s for s in tolerated if s["visible"]]),
                unconfirmed_floater_count=len([s for s in unconfirmed if s["visible"]]),
                attached_count=len(tolerated),
                gap_histogram=hist, floater_gap_range_mm=gap_range,
                gate_caliber={"micro_gap_mm": _r(micro_gap_mm, 4),
                              "meaning": "『算作相接』的距离口径：0.3mm=单一实体/3D 打印（上游口径）；"
                                         "带设计间隙的装配件请按工艺给（如 1–2mm）",
                              "how_to_change": "audit_gate(args={micro_gap_mm: 2.0})"},
                max_floater_span_fraction=_r(max([s["span_fraction"] for s in floaters] or [0.0]), 6),
                model_span=_r(model_span), limit=int(limit))


def audit_connectivity(objects=None, scope=None, include_hidden=False, visible_frac=VISIBLE_SPAN_FRACTION,
                       micro_gap_mm=MICRO_GAP_MM, precision=5, limit=24, max_tris=None, file=None,
                       per_object=False):
    """连通分量体检：这堆对象到底连成几块？哪几块是"看得见的浮块"？归因到哪个对象？

    v0.9.1（C2）：新增 file=<.blend 路径> —— 把该文件里的对象**临时**追加进会话，跑同一条分析链路，
    跑完（无论成败）在 finally 里删干净；返回体的 source / file_load / cleanup 说明来源、加载了什么、
    删了什么。此时 objects/scope 当"文件内的**对象名**"筛选（append 不带集合成员关系）。
    报告里的对象名一律是**文件里的原名**（重名追加时 Blender 会加 .001，用 object_names 核对）。
    老签名与老返回键一个没动，只做加法。
    """
    if per_object and not file:
        # v0.9.6（现场反馈 #2）：整体 3M 面 ⇒ 全局只能 bbox-only（degraded）。逐对象跑，每件通常
        # 在 BVH_MAX_TRIS 之内，于是拿得回 mesh(BVH) 级结论，而不是一句"降级"。
        objs0, missing0, err0 = _pick(objects, scope)
        if err0:
            return _j({"ok": False, "error": err0, "missing": missing0})
        rows = []
        for ob in objs0:
            if ob.type != "MESH":
                continue
            sub = json.loads(audit_connectivity(objects=[ob.name], include_hidden=include_hidden,
                                                visible_frac=visible_frac, micro_gap_mm=micro_gap_mm,
                                                precision=precision, limit=limit, max_tris=max_tris))
            g = _gate_from(sub)
            rows.append({"object": ob.name, "state": g.get("state"), "ok": g.get("ok"),
                         "verdict": g.get("verdict"), "tris": sub.get("tris"),
                         "mesh_mode": sub.get("micro_confirm"),
                         "visible_floaters": g.get("visible_floater_count"),
                         "real_floaters": g.get("real_floater_count"),
                         "reason": (g.get("reason") or "")[:200]})
        if not rows:
            return _j({"ok": False, "error": "范围内没有 mesh 对象", "missing": missing0})
        states = [r["state"] for r in rows]
        verdict = ("fail" if "fail" in states else ("degraded" if any(x != "pass" for x in states) else "pass"))
        payload = {"ok": verdict == "pass", "verdict": verdict, "per_object": True, "count": len(rows),
                   "objects": rows,
                   "failed_objects": [r["object"] for r in rows if r["state"] == "fail"],
                   "degraded_objects": [r["object"] for r in rows if r["state"] == "degraded"],
                   "note": "逐对象复核：整体面数超 BVH 上限时用它；verdict 三态，degraded ≠ 通过"}
        if verdict == "degraded":
            payload["warn"] = "DEGRADED ≠ 通过：有对象未给出通过结论（看 objects[].reason）"
        return _j(payload)
    S = _objs_with_optional_file(objects, scope, include_hidden, file)
    objs, missing, err, name_of = S["objs"], S["missing"], S["err"], S["name_of"]
    try:
        if err:
            res = {"ok": False, "error": err, "missing": missing, "file": (S["load"] or {}).get("path"),
                   "hint": "file= 模式：文件要能打开、里面有可见 mesh；空产出一律 ok:false"}
        elif not objs:
            res = {"ok": False, "error": "范围内 0 个 mesh", "missing": missing,
                   "hint": "确认对象名/集合名，或 include_hidden=True；静默空产出必须失败"}
        else:
            a = _conn_analyze(objs, precision=precision, visible_frac=visible_frac, micro_gap_mm=micro_gap_mm,
                              max_tris=max_tris, limit=limit, name_of=name_of)
            gate = _gate_from(a)
            res = {"ok": True, "analyzed": a["analyzed"], "units": a["units"], "objects": a["objects"],
                   "object_names": a["object_names"], "tris": a.get("tris"), "missing": missing,
                   "aabb": a.get("aabb")}
            if not a["analyzed"]:
                res.update({"reason": a.get("reason"), "note": a.get("note"), "gate": gate})
            else:
                res.update({
                    "components": a["components"][:int(limit)],
                    "floater_count": a["floater_count"],
                    "visible_floater_count": a["visible_floater_count"],
                    "real_floater_count": a["real_floater_count"],
                    "micro_floater_count": a["micro_floater_count"],
                    "unconfirmed_floater_count": a.get("unconfirmed_floater_count", 0),
                    "micro_confirm": a.get("micro_confirm"),
                    "max_floater_span_fraction": a["max_floater_span_fraction"],
                    "model_span": a["model_span"],
                    "gap_note": a.get("gap_note"),
                    "gap_histogram": a.get("gap_histogram"),
                    "floater_gap_range_mm": a.get("floater_gap_range_mm"),
                    "gate_caliber": a.get("gate_caliber"),
                    "attached_count": a.get("attached_count"),
                    "floaters": [{"rank": s["rank"], "tris": s["tris"], "span_fraction": s["span_fraction"],
                                  "gap_mm": s["gap_mm"], "visible": s["visible"],
                                  "bbox_micro": s.get("bbox_micro"), "mesh_confirmed": s.get("mesh_confirmed"),
                                  "true_gap_units": s.get("true_gap_units"), "evidence": s.get("evidence"),
                                  "confirm_note": s.get("confirm_note"),
                                  "bbox_min": s["min"], "bbox_max": s["max"], "size": s["size"],
                                  "attribution": s["attribution"]} for s in a["floaters"][:int(limit)]],
                    "tolerated": [{"rank": s["rank"], "span_fraction": s["span_fraction"],
                                   "gap_mm": s["gap_mm"], "bbox_micro": s.get("bbox_micro"),
                                   "mesh_confirmed": s.get("mesh_confirmed"),
                                   "true_gap_units": s.get("true_gap_units"),
                                   "objects": sorted((s.get("owners") or {}).keys()),
                                   "confirm_note": s.get("confirm_note")}
                                  for s in a.get("tolerated", [])[:int(limit)]],
                    "unconfirmed": [{"rank": s["rank"], "span_fraction": s["span_fraction"],
                                     "objects": sorted((s.get("owners") or {}).keys()),
                                     "confirm_note": s.get("confirm_note")}
                                    for s in a.get("unconfirmed", [])[:int(limit)]],
                    "gate": gate,
                    "verdict_line": _verdict_line(a, visible_frac, micro_gap_mm),
                })
    except Exception as e:
        res = _exc_body(e, "connectivity")
    finally:
        crep = S["cleanup"]() if S.get("cleanup") else None
    return _j(_file_extras(res, S, crep))


def _verdict_of(ok, state):
    """v0.9.6（现场反馈 #2）：三态**显式**成一个字段，消费方永远读 verdict，别读 ok/state 再自己推。"""
    if ok is True or state == "pass":
        return {"verdict": "pass"}
    if ok is None or state == "degraded":
        return {"verdict": "degraded",
                "warn": "DEGRADED ≠ 通过：未分析 / 未确认 / 超出复核能力 —— 这一门没有给出通过结论"}
    return {"verdict": "fail"}


def _gate_from(a):
    """连通门：只看"可见且网格复核确认非贴合"的浮块。未分析/未确认 → ok=null（degraded）。"""
    if not a.get("analyzed"):
        return dict({"ok": None, "state": "degraded", "reason": a.get("reason") or "未分析",
                     "note": "未分析不得读作已连通"}, **_verdict_of(None, "degraded"))
    real = a.get("real_floater_count", 0)          # 可见且与任何分量都不相接
    micro = a.get("micro_floater_count", 0)        # 可见但已确认相接（贴而未重合/互穿）
    unconf = a.get("unconfirmed_floater_count", 0)
    if real == 0 and unconf > 0:
        return dict({"ok": None, "state": "degraded", "visible_floater_count": a.get("visible_floater_count"),
                "unconfirmed_floater_count": unconf, "micro_tolerated": micro,
                "reason": "%d 个可见分量无法确认是否相接（%s）→ 降级，不给通过结论" % (unconf, a.get("micro_confirm")),
                "note": "上游在单网格里可以 fail-open；多对象装配不行 —— 未确认就是未确认"}, **_verdict_of(None, "degraded"))
    if real == 0:
        return dict({"ok": True, "state": "pass", "visible_floater_count": 0, "real_floater_count": 0,
                "unconfirmed_floater_count": 0, "micro_tolerated": micro,
                "reason": "" if micro == 0 else "%d 个可见分量经网格确认与邻居相接（贴而未重合/互穿）→ 容忍" % micro},
                    **_verdict_of(True, "pass"))
    worst = [s for s in a.get("floaters", []) if s.get("visible")][:6]
    return dict({"ok": False, "state": "fail", "visible_floater_count": a.get("visible_floater_count"),
            "real_floater_count": real, "micro_tolerated": micro, "unconfirmed_floater_count": unconf,
            "offenders": [{"rank": s["rank"], "span_fraction": s["span_fraction"], "gap_mm": s["gap_mm"],
                           "true_gap_units": s.get("true_gap_units"), "evidence": s.get("evidence"),
                           "edit_hint": (s["attribution"] or {}).get("object")} for s in worst],
            "reason": "%d 个可见浮块：与任何其它分量都不相接（口径 %.2f mm 内找不到邻居%s）" % (
                real, MICRO_GAP_MM if not a.get("gate_caliber") else a["gate_caliber"].get("micro_gap_mm", MICRO_GAP_MM),
                ("；最近 %.2f–%.2f mm" % (a["floater_gap_range_mm"][0], a["floater_gap_range_mm"][1]))
                if a.get("floater_gap_range_mm") else "")}, **_verdict_of(False, "fail"))


def _verdict_line(a, visible_frac, micro_gap_mm):
    if not a.get("analyzed"):
        return "连通：未分析（%s）—— 不得当作已连通" % (a.get("reason") or "")
    real = a.get("real_floater_count", 0)
    micro = a.get("micro_floater_count", 0)
    unconf = a.get("unconfirmed_floater_count", 0)
    rng = a.get("floater_gap_range_mm")
    if real == 0 and unconf == 0:
        return "连通：通过（%d 个分量；%d 个可见分量经网格确认与邻居相接；阈值 可见 %.1f%%／相接 %.2f mm）" % (
            len(a["components"]), micro, visible_frac * 100, micro_gap_mm)
    if real == 0:
        return "连通：降级 —— %d 个分量无法确认相接（%s），不给通过结论" % (unconf, a.get("micro_confirm"))
    names = [((s["attribution"] or {}).get("object") or "UNMATCHED") for s in a["floaters"] if s["visible"]]
    extra = ("；与最近邻的距离 %.2f–%.2f mm —— 若这是设计间隙，把口径调大（micro_gap_mm）" % (rng[0], rng[1])) if rng else ""
    return "连通：不通过 —— %d 个可见浮块与任何分量都不相接%s，优先改 %s" % (real, extra, ", ".join(names[:6]))


def audit_gate(objects=None, scope=None, include_hidden=False, envelope=None, visible_frac=VISIBLE_SPAN_FRACTION,
               micro_gap_mm=MICRO_GAP_MM, max_floater_span_fraction=None, max_tris=None, file=None):
    """出厂门：连通 + （可选）包络 + 三态。任何"未分析"都降级为 degraded，绝不给通过。

    v0.9.1（C2）：新增 file=<.blend 路径>（临时加载 → 跑同一条链路 → finally 删净；source/file_load/
    cleanup 上报来源与收尾）。包络检查用的 per-object bbox 在 file= 模式下以**文件原名**上报。
    老签名与老返回键一个没动，只做加法（新增 object_names / aabb / source / file_load / cleanup）。
    """
    S = _objs_with_optional_file(objects, scope, include_hidden, file)
    objs, missing, err, name_of = S["objs"], S["missing"], S["err"], S["name_of"]
    try:
        if err:
            res = {"ok": False, "error": err, "missing": missing, "file": (S["load"] or {}).get("path"),
                   "hint": "file= 模式：文件要能打开、里面有可见 mesh；空产出一律 ok:false"}
        elif not objs:
            res = {"ok": False, "error": "范围内 0 个 mesh", "missing": missing}
        else:
            a = _conn_analyze(objs, visible_frac=visible_frac, micro_gap_mm=micro_gap_mm, max_tris=max_tris,
                              name_of=name_of)
            conn = _gate_from(a)
            verdicts = {"connectivity": conn}
            ok = conn["ok"]
            if conn["ok"] is None:
                ok = None
            if max_floater_span_fraction is not None and a.get("analyzed"):
                lim = float(max_floater_span_fraction)
                got = float(a.get("max_floater_span_fraction") or 0.0)
                good = got <= lim
                verdicts["floater_span"] = {"ok": good, "max_floater_span_fraction": got, "limit": lim}
                if ok is True and not good:
                    ok = False
            if envelope is not None and len(list(envelope)) == 6:
                lo = [float(x) for x in list(envelope)[:3]]
                hi = [float(x) for x in list(envelope)[3:]]
                bad = []
                for name, b in a["boxes"].items():
                    if b is None:
                        continue
                    over = []
                    for k in range(3):
                        if float(b[0][k]) < lo[k] - 1e-9:
                            over.append(["%s_min" % "xyz"[k], _r(float(b[0][k]) - lo[k])])
                        if float(b[1][k]) > hi[k] + 1e-9:
                            over.append(["%s_max" % "xyz"[k], _r(float(b[1][k]) - hi[k])])
                    if over:
                        bad.append({"name": name, "out_of_envelope": over})
                good = len(bad) == 0
                verdicts["envelope"] = {"ok": good, "violations": len(bad), "objects": bad[:20]}
                if ok is True and not good:
                    ok = False
            res = dict({"ok": True, "ship_ok": ok,
                   "state": ("pass" if ok is True else ("degraded" if ok is None else "fail")),
                   "verdicts": verdicts, "units": a["units"], "objects": len(objs),
                   "object_names": a.get("object_names"), "tris": a.get("tris"), "analyzed": a.get("analyzed"),
                   "aabb": a.get("aabb"),
                   "verdict_line": _verdict_line(a, visible_frac, micro_gap_mm),
                   "note": "ok=true 表示分析都跑完了（不是门通过）；门结论看 verdict（ship_ok 保留兼容）"},
                   **_verdict_of(ok, "pass" if ok is True else ("degraded" if ok is None else "fail")))
    except Exception as e:
        res = _exc_body(e, "gate")
    finally:
        crep = S["cleanup"]() if S.get("cleanup") else None
    return _j(_file_extras(res, S, crep))


# ---------------------------------------------------------------- 漂移度量（Chamfer 距离）

def _read_obj(path):
    import numpy as np
    V, F = [], []
    with open(path, "r", encoding="utf-8", errors="ignore") as fh:
        for line in fh:
            if line.startswith("v "):
                p = line.split()
                V.append((float(p[1]), float(p[2]), float(p[3])))
            elif line.startswith("f "):
                idx = []
                for tok in line.split()[1:]:
                    t = tok.split("/")[0]
                    if t:
                        i = int(t)
                        idx.append(i - 1 if i > 0 else len(V) + i)
                for k in range(1, len(idx) - 1):
                    F.append((idx[0], idx[k], idx[k + 1]))
    return np.asarray(V, dtype=np.float64), np.asarray(F, dtype=np.int64)


def _read_stl(path):
    import numpy as np
    with open(path, "rb") as fh:
        head = fh.read(84)
        if len(head) < 84:
            raise ValueError("STL 太短：%s" % path)
        n = int(np.frombuffer(head[80:84], dtype="<u4")[0])
        raw = fh.read(50 * n)
        if n > 0 and len(raw) == 50 * n:
            rec = np.frombuffer(raw, dtype=np.uint8).reshape(n, 50)
            tri = rec[:, 12:48].copy().view("<f4").reshape(n, 3, 3).astype(np.float64)
            return tri.reshape(-1, 3), np.arange(n * 3, dtype=np.int64).reshape(n, 3)
    V, F = [], []
    with open(path, "r", encoding="utf-8", errors="ignore") as fh:
        cur = []
        for line in fh:
            t = line.strip().split()
            if len(t) == 4 and t[0].lower() == "vertex":
                cur.append((float(t[1]), float(t[2]), float(t[3])))
                if len(cur) == 3:
                    base = len(V)
                    V.extend(cur)
                    F.append((base, base + 1, base + 2))
                    cur = []
    import numpy as np
    return np.asarray(V, dtype=np.float64), np.asarray(F, dtype=np.int64)


def _src_tris(src):
    """对象名 / 集合名 / .obj / .stl 路径 → (tris, label)。"""
    import os
    s = str(src)
    if os.path.exists(s):
        ext = os.path.splitext(s)[1].lower()
        if ext == ".obj":
            V, F = _read_obj(s)
        elif ext == ".stl":
            V, F = _read_stl(s)
        else:
            raise ValueError("只支持 .obj/.stl 文件，或对象名/集合名：%s" % s)
        if len(F) == 0:
            raise ValueError("文件里没有面：%s" % s)
        return V[F], os.path.basename(s)
    ob = bpy.data.objects.get(s)
    if ob is not None and ob.type == "MESH":
        t, _o, _b = _world_tris([ob])
        return t, s
    coll = bpy.data.collections.get(s)
    if coll is not None:
        objs = [o for o in coll.all_objects if o.type == "MESH" and not (o.hide_render or o.hide_viewport)]
        t, _o, _b = _world_tris(objs)
        return t, s
    raise ValueError("找不到对象/集合/文件：%s" % s)


def _normalize_tris(tris):
    import numpy as np
    p = tris.reshape(-1, 3)
    lo, hi = p.min(axis=0), p.max(axis=0)
    c = (lo + hi) / 2.0
    longest = float((hi - lo).max())
    if longest <= 0:
        raise ValueError("退化网格（零尺寸 bbox）")
    return (tris - c) * (2.0 / longest)


def _sample(tris, n, rng):
    import numpy as np
    v0, v1, v2 = tris[:, 0], tris[:, 1], tris[:, 2]
    a = 0.5 * np.linalg.norm(np.cross(v1 - v0, v2 - v0), axis=1)
    tot = float(a.sum())
    if tot <= 0:
        p = tris.reshape(-1, 3)
        return p[rng.integers(0, len(p), size=int(n))]
    c = np.cumsum(a)
    idx = np.searchsorted(c, rng.random(int(n)) * tot)
    u = rng.random((int(n), 2))
    m = np.sqrt(u[:, 0])[:, None]
    s = u[:, 1][:, None]
    return v0[idx] * (1 - m) + v1[idx] * (m * (1 - s)) + v2[idx] * (m * s)


BVH_MAX_TRIS = 500000        # 超过就退回分格点对点（BVHTree.FromPolygons 的构建成本随面数线性涨）


def _surface_mean_dist(P, tris_target):
    """P 每点到目标三角面集合的最近距离均值 —— BVHTree（C 实现）= 点到**曲面**，不是点到点。"""
    import numpy as np
    from mathutils.bvhtree import BVHTree
    V = tris_target.reshape(-1, 3).tolist()
    polys = [(3 * i, 3 * i + 1, 3 * i + 2) for i in range(len(tris_target))]
    tree = BVHTree.FromPolygons(V, polys, all_triangles=True)
    fn = tree.find_nearest
    tot = 0.0
    n = len(P)
    for i in range(n):
        p = P[i]
        hit = fn((float(p[0]), float(p[1]), float(p[2])))
        if hit is not None and hit[3] is not None:
            tot += float(hit[3])
    return tot / max(1, n), int(n)


def _nn_mean(P, Q, cell=None, fast=True):
    """P 每点到 Q 的最近距离均值。

    fast=True 走"分格 + 补齐数组"的向量化路径（每格最多 32 点；只在 27 邻域里找最近，
    与 Procedura 的 cell≈2/cbrt(N) 同口径）—— 比逐点慢路径快约一个量级；任何异常/退化
    （单格点数 >32、孤立点）都回退到慢路径，结果口径不变。
    """
    import numpy as np
    if len(P) == 0 or len(Q) == 0:
        return float("nan")
    if cell is None:
        cell = 2.0 / max(1.0, round(len(P) ** (1.0 / 3.0)))
    if fast:
        try:
            return _nn_mean_grid(P, Q, cell)
        except Exception:
            pass
    return _nn_mean_slow(P, Q, cell)


def _nn_mean_grid(P, Q, cell):
    import numpy as np
    lo = np.array([-1.5, -1.5, -1.5])          # 规范化帧 [-1,1]，留余量
    K = int(np.ceil(3.0 / float(cell))) + 4

    def cid(X):
        return np.clip(np.floor((X - lo) / float(cell)).astype(np.int64), 0, K - 1)

    qc = cid(Q)
    lin = qc[:, 0] * K * K + qc[:, 1] * K + qc[:, 2]
    order = np.argsort(lin, kind="stable")
    lin_s = lin[order]
    starts = np.searchsorted(lin_s, np.arange(K * K * K))
    counts = np.bincount(lin, minlength=K * K * K)
    maxp = int(counts.max()) if len(counts) else 0
    if maxp <= 0 or maxp > 32:
        raise RuntimeError("dense-or-empty")
    pad = np.full((K * K * K, maxp, 3), 1e9, dtype=np.float64)
    within = np.arange(len(Q)) - starts[lin[order]]
    pad[lin[order], within] = Q[order]
    pad = pad.reshape(K, K, K, maxp, 3)
    pc = cid(P)
    offs = np.array([(dx, dy, dz) for dx in (-1, 0, 1) for dy in (-1, 0, 1) for dz in (-1, 0, 1)],
                    dtype=np.int64)
    out = np.empty(len(P), dtype=np.float64)
    BATCH = 2048                     # 批量取格：把 numpy 调用摊到几百个查询上（否则每点一次调用 ≈0.15 ms）
    for s in range(0, len(P), BATCH):
        pcx = pc[s:s + BATCH]
        nb = np.clip(pcx[:, None, :] + offs[None, :, :], 0, K - 1)       # (b,27,3)
        blk = pad[nb[:, :, 0], nb[:, :, 1], nb[:, :, 2]]                 # (b,27,maxp,3)
        blk = blk.reshape(blk.shape[0], -1, 3)
        d = np.linalg.norm(blk - P[s:s + BATCH][:, None, :], axis=2)
        out[s:s + BATCH] = d.min(axis=1)
    bad = out > 1e6                      # 27 邻域空 → 整云兜底（规范化后极少发生）
    if bad.any():
        for i in np.nonzero(bad)[0]:
            out[i] = float(np.linalg.norm(Q - P[i], axis=1).min())
    return float(out.mean())


def _nn_mean_slow(P, Q, cell):
    import numpy as np
    grid = {}
    cells = np.floor(Q / cell).astype(np.int64)
    for i in range(len(Q)):
        grid.setdefault((int(cells[i, 0]), int(cells[i, 1]), int(cells[i, 2])), []).append(i)
    offs = [(dx, dy, dz) for dx in (-1, 0, 1) for dy in (-1, 0, 1) for dz in (-1, 0, 1)]
    pc = np.floor(P / cell).astype(np.int64)
    out = np.empty(len(P), dtype=np.float64)
    for i in range(len(P)):
        cx, cy, cz = int(pc[i, 0]), int(pc[i, 1]), int(pc[i, 2])
        best = float("inf")
        for (dx, dy, dz) in offs:
            lst = grid.get((cx + dx, cy + dy, cz + dz))
            if not lst:
                continue
            d = float(np.linalg.norm(Q[lst] - P[i], axis=1).min())
            if d < best:
                best = d
                if best <= 0:
                    break
        if best == float("inf"):
            best = float(np.linalg.norm(Q - P[i], axis=1).min())
        out[i] = best
    return float(out.mean())


def audit_drift(a, b, samples=20000, seed=24233, normalize=True, fast=True):
    """对称 Chamfer 距离 = **形状漂移**（不是倒角！）。独立于渲染的数值复核通路。

    ⚠ 口径（Procedura 原样）：normalize=True 时**各自归一化**到单位包围盒 —— 平移与整体缩放被消除，
    所以这个数回答"形状改了多远"，不回答"挪了多远"。要连位移一起看：读 bbox_delta（原始帧的
    center/size 差，mm），或传 normalize=False 做同帧比较。
    """
    import numpy as np
    try:
        ta, la = _src_tris(a)
        tb, lb = _src_tris(b)
    except Exception as e:
        return _j({"ok": False, "error": str(e)[:200], "hint": "a/b 可以是对象名、集合名，或 .obj/.stl 路径"})
    if ta is None or tb is None or len(ta) == 0 or len(tb) == 0:
        return _j({"ok": False, "error": "空网格", "a_tris": 0 if ta is None else int(len(ta)),
                   "b_tris": 0 if tb is None else int(len(tb))})
    ta0, tb0 = ta, tb
    import time as _t
    _t0 = _t.perf_counter()
    try:
        if normalize:
            ta, tb = _normalize_tris(ta), _normalize_tris(tb)
        rng = np.random.default_rng(int(seed))
        Pa, Pb = _sample(ta, samples, rng), _sample(tb, samples, rng)
        metric = "point-to-surface(BVHTree)"
        if fast and len(ta) <= BVH_MAX_TRIS and len(tb) <= BVH_MAX_TRIS:
            try:
                a2b, _n1 = _surface_mean_dist(Pa, tb)
                b2a, _n2 = _surface_mean_dist(Pb, ta)
            except Exception:
                metric = "point-to-point(grid)"
                a2b, b2a = _nn_mean(Pa, Pb, fast=False), _nn_mean(Pb, Pa, fast=False)
        else:
            metric = "point-to-point(grid)"
            a2b, b2a = _nn_mean(Pa, Pb, fast=False), _nn_mean(Pb, Pa, fast=False)
    except Exception as e:
        return _j({"ok": False, "error": "采样/近邻失败：%s" % str(e)[:200]})
    ch = (a2b + b2a) / 2.0
    ms = int((_t.perf_counter() - _t0) * 1000)
    # 原始帧的位置 / 尺寸差（形状漂移看不到这一层，单独给）
    _m, mm_per_unit = _units()
    pa, pb = ta0.reshape(-1, 3), tb0.reshape(-1, 3)
    lo_a, hi_a = pa.min(axis=0), pa.max(axis=0)
    lo_b, hi_b = pb.min(axis=0), pb.max(axis=0)
    c_delta = [float((hi_b[i] + lo_b[i]) / 2.0 - (hi_a[i] + lo_a[i]) / 2.0) for i in range(3)]
    moved = float(np.linalg.norm(c_delta))
    size_a = [float(hi_a[i] - lo_a[i]) for i in range(3)]
    size_b = [float(hi_b[i] - lo_b[i]) for i in range(3)]
    band = "noise" if ch <= 0.01 else ("mild" if ch <= 0.05 else "strong")
    return _j({"ok": True, "chamfer": _r(ch, 6), "a_to_b": _r(a2b, 6), "b_to_a": _r(b2a, 6),
               "samples": int(samples), "seed": int(seed), "normalized": bool(normalize), "fast_nn": bool(fast),
               "ms": ms, "metric": metric,
               "a": la, "b": lb, "a_tris": int(len(ta0)), "b_tris": int(len(tb0)),
               "band": band, "band_read": {"noise": "≤0.01：基本未变（同网格读 ≈0）",
                                           "mild": "0.01–0.05：轻微改动（≤最长边 2.5%）",
                                           "strong": ">0.05：明显改动"}[band],
               "bbox_delta": {"center_delta_mm": [_r(x * mm_per_unit, 4) for x in c_delta],
                              "moved_mm": _r(moved * mm_per_unit, 4),
                              "moved_units": _r(moved, 6),
                              "size_a_mm": [_r(x * mm_per_unit, 4) for x in size_a],
                              "size_b_mm": [_r(x * mm_per_unit, 4) for x in size_b],
                              "note": "维度同帧原始值：形状漂移（chamfer）看不到这部分，别把它读成 0"},
               "noise_floor": "≈0（point-to-surface：源侧采样点正落在目标曲面上；退回 grid 口径时约 0.01–0.02）",
               "read_rule": "规范化帧里最长轴跨 [-1,1]：0.05 ≈ 最长边 2.5%；同 seed 同 samples 才可跨次比较；"
                            "metric=point-to-point(grid) 时是点到最近采样点，数值系统性偏大（≈点距/2），别与 BVH 口径混比"})


def audit_measure(objects=None, scope=None, include_hidden=False, neighbors=True, k=4, max_pairs=200,
                  file=None):
    """测量包：世界 bbox + 逐轴间隙/重叠（mm 与 %）+ 邻居（契约图 + 空间最近 k）。

    v0.9.1（C2）：新增 file=<.blend 路径>（临时加载 → 跑同一条链路 → finally 删净；source/file_load/
    cleanup 上报来源与收尾）。items[].name / pairs[].a|b / neighbors 的键一律是**文件里的原名**。
    老签名与老返回键一个没动，只做加法（新增 object_names / aabb / source / file_load / cleanup）。
    """
    import numpy as np
    S = _objs_with_optional_file(objects, scope, include_hidden, file)
    objs, missing, err, name_of = S["objs"], S["missing"], S["err"], S["name_of"]
    try:
        if err:
            res = {"ok": False, "error": err, "missing": missing, "file": (S["load"] or {}).get("path"),
                   "hint": "file= 模式：文件要能打开、里面有可见 mesh；空产出一律 ok:false"}
        elif not objs:
            res = {"ok": False, "error": "范围内 0 个 mesh", "missing": missing}
        else:
            m_per_unit, mm_per_unit = _units()
            _t, _o, boxes = _world_tris(objs)
            boxes = _named_boxes(objs, boxes, name_of)
            items, good = [], []
            for ob in objs:
                nm = _nm(ob, name_of)
                b = boxes.get(nm)
                if b is None:
                    items.append({"name": nm, "error": "无几何（0 面）"})
                    continue
                lo, hi = [float(x) for x in b[0]], [float(x) for x in b[1]]
                size = [hi[i] - lo[i] for i in range(3)]
                ctr = [(hi[i] + lo[i]) / 2.0 for i in range(3)]
                items.append({"name": nm, "min": [_r(x) for x in lo], "max": [_r(x) for x in hi],
                              "size": [_r(x) for x in size], "size_mm": [_r(x * mm_per_unit, 3) for x in size],
                              "center": [_r(x) for x in ctr], "tris": int(_tris(ob.data))})
                good.append((nm, lo, hi))
            pairs = []
            for i in range(len(good)):
                for j in range(i + 1, len(good)):
                    na, la, ha = good[i]
                    nb, lb, hb = good[j]
                    gaps, overlaps = {}, {}
                    for ax in range(3):
                        g = max(0.0, max(la[ax] - hb[ax], lb[ax] - ha[ax]))
                        ov = min(ha[ax], hb[ax]) - max(la[ax], lb[ax])
                        gaps["xyz"[ax]] = _r(g * mm_per_unit, 4)
                        overlaps["xyz"[ax]] = _r(ov * mm_per_unit, 4)
                    sep = (sum(v * v for v in gaps.values())) ** 0.5
                    pairs.append({"a": na, "b": nb, "gap_mm": _r(sep, 4), "axis_gap_mm": gaps,
                                  "axis_overlap_mm": overlaps,
                                  "contact": bool(sep <= 1e-9),
                                  "overlap_bbox": bool(all(overlaps[a] > 0 for a in "xyz")),
                                  "center_dist_mm": _r(float(np.linalg.norm(np.array(
                                      [(ha[i] + la[i] - hb[i] - lb[i]) / 2.0 for i in range(3)]))) * mm_per_unit, 4)})
                    if len(pairs) >= int(max_pairs):
                        break
                if len(pairs) >= int(max_pairs):
                    break
            pairs.sort(key=lambda p: p["gap_mm"])
            res = {"ok": True, "units": {"m_per_unit": _r(m_per_unit, 6), "mm_per_unit": _r(mm_per_unit, 6)},
                   "count": len(items), "items": items, "missing": missing,
                   "object_names": [_nm(o, name_of) for o in objs],
                   "aabb": (_box_fields(np.array([g[1] for g in good]).min(axis=0),
                                        np.array([g[2] for g in good]).max(axis=0), mm_per_unit) if good else None),
                   "pairs": pairs if pairs else [],
                   "note": "所有 mm 口径都按 units.mm_per_unit 换算；bbox 级（不是三角级）"}
            if neighbors and good:
                # ① 契约图的邻居（若注册过组件/连接）
                decl = {}
                try:
                    K = _kernel()
                    store = getattr(K, "dsh_contract", None) if K is not None else None
                    if store:
                        name2comp = {}
                        for cid, comp in (store.get("components") or {}).items():
                            for n in (comp.get("objects") or []):
                                name2comp.setdefault(n, []).append(cid)
                        for conn in (store.get("connections") or {}).values():
                            for n in (store.get("components", {}).get(conn.get("a"), {}) or {}).get("objects", []) or []:
                                for m in (store.get("components", {}).get(conn.get("b"), {}) or {}).get("objects", []) or []:
                                    if n != m:
                                        decl.setdefault(n, set()).add(m)
                except Exception:
                    decl = {}
                nb = {}
                for name, lo, hi in good:
                    c = np.array([(hi[i] + lo[i]) / 2.0 for i in range(3)])
                    arr = []
                    for nm, l, h in good:
                        if nm == name:
                            continue
                        c2 = np.array([(h[i] + l[i]) / 2.0 for i in range(3)])
                        arr.append((float(np.linalg.norm(c - c2)), nm))
                    arr.sort(key=lambda x: x[0])
                    nb[name] = {"declared": sorted(decl.get(name, [])),
                                "nearest": [{"name": nm, "center_dist_mm": _r(dd * mm_per_unit, 3)}
                                            for dd, nm in arr[:int(k)]]}
                res["neighbors"] = nb
    except Exception as e:
        res = _exc_body(e, "measure")
    finally:
        crep = S["cleanup"]() if S.get("cleanup") else None
    return _j(_file_extras(res, S, crep))


def audit_snap_floaters(objects=None, scope=None, dry_run=True, visible_frac=VISIBLE_SPAN_FRACTION,
                        micro_gap_mm=MICRO_GAP_MM, overlap_mm=0.05, apply_parented=False, max_tris=None):
    """把可见浮块贴到最近邻（默认只报告）。整件平移只对"完全属于该浮块"的对象做，焊接体一块不撕。"""
    import numpy as np
    objs, missing, err = _objs_for(objects, scope, False)
    if err:
        return _j({"ok": False, "error": err})
    if not objs:
        return _j({"ok": False, "error": "范围内 0 个 mesh", "missing": missing})
    a = _conn_analyze(objs, visible_frac=visible_frac, micro_gap_mm=micro_gap_mm, max_tris=max_tris)
    if not a.get("analyzed"):
        return _j({"ok": False, "error": a.get("reason"), "note": a.get("note"),
                   "hint": "先缩小 scope 或提高 env DSH_CONN_MAX_TRIS"})
    _m, mm_per_unit = _units()
    eps = float(overlap_mm) / mm_per_unit
    moves, skipped = [], []
    tree_cache = {}
    for s in a["floaters"]:
        if not s.get("visible"):
            continue
        owners = s["owners"]
        # 该对象是否"整件都在这个浮块里"（否则平移会撕开焊接体 —— Procedura v3 snap 的翻车点）
        obj_tris = a.get("obj_tris") or {}
        whole = [n for n in owners if obj_tris.get(n) and obj_tris.get(n) == owners[n]]
        if not whole:
            skipped.append({"rank": s["rank"], "span_fraction": s["span_fraction"], "gap_mm": s["gap_mm"],
                            "why": "浮块只是某个对象的一部分（焊接体），整件平移会撕开焊点 → 交给编辑那一步处理"})
            continue
        # 朝最近邻分量方向平移：逐轴 gap + 一点重叠量
        nb = None
        for t2 in a["components"]:
            if t2["rank"] == s["rank"]:
                continue
            d2 = 0.0
            for kk in range(3):
                g = max(0.0, max(s["min"][kk] - t2["max"][kk], t2["min"][kk] - s["max"][kk]))
                d2 += g * g
            if nb is None or d2 < nb[0]:
                nb = (d2, t2)
        if nb is None:
            continue
        t2 = nb[1]
        delta = []
        for kk in range(3):
            if s["max"][kk] < t2["min"][kk]:
                delta.append(_r(t2["min"][kk] - s["max"][kk] + eps, 8))
            elif t2["max"][kk] < s["min"][kk]:
                delta.append(_r(-(s["min"][kk] - t2["max"][kk] + eps), 8))
            else:
                delta.append(0.0)
        method = "bbox"
        if all(abs(x) < 1e-12 for x in delta):
            # bbox 读成 0：floaters 里只会出现 attached=False（真浮块）→ 必须走网格级最近点对
            raw = a.get("_raw") or {}
            if not raw:
                skipped.append({"rank": s["rank"], "why": "没有原始三角数据，无法做网格级平移"})
                continue
            dvec, dist_u, note = _snap_pair_mesh(raw["tris"], raw["comp"], s["comp"], tree_cache)
            if not dvec:
                skipped.append({"rank": s["rank"], "span_fraction": s["span_fraction"], "gap_mm": s["gap_mm"],
                                "why": note})
                continue
            scale = 1.0 + (eps / max(float(dist_u), 1e-9))
            delta = [_r(dvec[k] * scale, 8) for k in range(3)]
            method = "mesh:" + note
        entry = {"rank": s["rank"], "objects": whole, "delta_units": delta,
                 "delta_mm": [_r(d * mm_per_unit, 4) for d in delta], "method": method,
                 "gap_before_mm": s["gap_mm"], "target_component_rank": t2["rank"],
                 "span_fraction": s["span_fraction"]}
        blocked = []
        for n in whole:
            ob = bpy.data.objects.get(n)
            if ob is None:
                continue
            if ob.parent is not None and not apply_parented:
                blocked.append("%s 有父级（平移会被父级覆盖）" % n)
            if ob.animation_data is not None:
                blocked.append("%s 有动画数据（关键帧会覆盖平移）" % n)
        if blocked:
            entry["why_not_applied"] = blocked
            skipped.append({"rank": s["rank"], "span_fraction": s["span_fraction"], "gap_mm": s["gap_mm"],
                            "why": "; ".join(blocked)})
        moves.append(entry)
    applied = []
    if not dry_run:
        for mv in moves:
            if mv.get("why_not_applied"):
                continue
            dv = np.array(mv["delta_units"], dtype=np.float64)
            for n in mv["objects"]:
                ob = bpy.data.objects.get(n)
                if ob is None:
                    continue
                ob.matrix_world.translation = ob.matrix_world.translation + Vector(dv)
                applied.append(n)
        try:
            bpy.context.view_layer.update()
        except Exception:
            pass
    return _j({"ok": True, "dry_run": bool(dry_run), "units": a["units"],
               "moves": moves, "skipped": skipped, "applied": applied,
               "before": {"visible_floater_count": a["visible_floater_count"],
                          "real_floater_count": a["real_floater_count"], "gate": _gate_from(a)},
               "hint": ("只报告。" if dry_run else "已平移（只动了整件属于浮块的对象）") +
                       " 应用前建议先 blender_rt_txn(op=snapshot)；改完用 audit_gate 复核",
               "note": "平移只收口「贴而未重合」；若浮块是对象的一部分，需在编辑那一步改几何"})


# ============================================================================
# v0.9.1（C1）：跨件 / 跨文件 面-对重叠 + 交集体体积（rifle-build《93 反馈》C1，定级 P2）
# ----------------------------------------------------------------------------
# 反馈原文（§C1）："check_interference 只认当前会话注册过的组件，传 file= 直接参数不匹配；跨
# parts/*.blend 的互穿只能自写 BVH 面-对 + 布尔交集体积"；证据是成员自建的
# lead_bvh_overlap.py / lead_interference.py（**正是这套自建审计抓出"弹匣悬空：井口盖板
# 10719.7 mm³"与"护木/前握把 2536/30319 mm³"**）。这里收敛成两个算子：
#     audit_overlap       面-对重叠（结构化：pairs / pair_count / intersection_bbox / per_object）
#     audit_interference  交集体积估计（mm³；Monte-Carlo 射线奇偶，**不用 bpy boolean**）
# 实测语义（Blender 5.2，逐条写进返回体，别当没这回事）：
#   * BVHTree.overlap = **真三角-三角相交**（AABB 重叠但不穿透的面对不报）→ method 敢写"三角面精确"；
#   * 但**共面且互相覆盖**的两张面不报（isect_tri_tri 的共面语义）→ 共面贴合由 contact_probe 补报；
#   * ray_cast 命中后要沿方向前进 eps 再打（否则原地自命中），迭代上限写死并回报 unknown。
# ============================================================================

INTERFERENCE_SAMPLES = 200000     # 默认采样数（相对误差 ∝ 1/√n；口径见返回体 error_caliber）
MATERIAL_VOLUME_MM3 = 1.0         # 可执行下限：< 1 mm³ 的互穿低于网格弦差量级 → 不作为"真干涉"
INTERFERENCE_MAX_STEPS = 64       # 单点单方向射线迭代上限；超限的点记 unknown（不猜）
CONTACT_PROBE_PTS = 256           # contact_probe 每侧曲面采样点数
PAIRS_BBOX_CAP = 200000           # 交叠面对 bbox 统计上限（病态场景防爆内存）


def _box_pair(lo, hi, mm_per_unit, digits=5):
    """(盒, 盒_mm)：场景单位与 mm 两个视角都给（铁律：数值带单位）。"""
    f = _box_fields(lo, hi, mm_per_unit, digits)
    return ({"min": f["min"], "max": f["max"], "size": f["size"]},
            {"min": f["min_mm"], "max": f["max_mm"], "size": f["size_mm"]})


def _box_intersect(lo1, hi1, lo2, hi2):
    """两个世界 AABB 的交盒；任一轴分离 → None（交集体必在交盒里，所以它是有用的先验）。"""
    import numpy as np
    lo = np.maximum(np.asarray(lo1, dtype=np.float64), np.asarray(lo2, dtype=np.float64))
    hi = np.minimum(np.asarray(hi1, dtype=np.float64), np.asarray(hi2, dtype=np.float64))
    if bool(np.any(hi < lo)):
        return None
    return (lo, hi)


def _bvh_from_tris(tris):
    """世界三角面 (N,3,3) → BVHTree（FromPolygons + all_triangles → 索引就是三角面下标）。"""
    from mathutils.bvhtree import BVHTree
    V = tris.reshape(-1, 3).tolist()
    polys = [(3 * i, 3 * i + 1, 3 * i + 2) for i in range(len(tris))]
    return BVHTree.FromPolygons(V, polys, all_triangles=True)


def _session_side(sel, include_hidden=False):
    """会话侧来源：对象名 / 名字列表 / 集合名 → (objs, missing, err)。"""
    if isinstance(sel, dict):
        w = sel
        sel = w.get("objects") or w.get("names") or w.get("list")
        if sel is None:
            sel = w.get("scope")
        include_hidden = bool(w.get("include_hidden", include_hidden))
    if sel is None or (isinstance(sel, str) and not sel.strip()):
        return [], [], "这一端没给来源：给会话对象/集合名（a / objects_a），或用 file_a / file_b 指到 .blend"
    got, missing = [], []
    if isinstance(sel, (list, tuple, set)):
        for n in sel:
            ob = bpy.data.objects.get(str(n))
            if ob is None or ob.type != "MESH":
                missing.append(str(n))
            else:
                got.append(ob)
    else:
        s = str(sel)
        ob = bpy.data.objects.get(s)
        if ob is not None:
            if ob.type != "MESH":
                return [], [], "不是 mesh 对象（type=%s）：%s" % (ob.type, s)
            got = [ob]
        else:
            coll = bpy.data.collections.get(s)
            if coll is None:
                return [], [], "会话里既没有对象也没有集合：%s" % s
            got = [o for o in coll.all_objects if o.type == "MESH"]
    if not include_hidden:
        got = [o for o in got if not (o.hide_render or o.hide_viewport)]
    return got, missing, None


def _side_tris(tag, sel=None, file=None, names=None, include_hidden=False):
    """解析一端（A/B）：会话对象/集合，或**另一个 .blend**（names/objects_x 在文件内筛名）。

    返回 {ok, error, tris, owner, objs, names, aabb, source, cleanup, file_info, hint}
    —— tris/owner/aabb 里有 numpy 对象，**不要**直接塞进 JSON（上报走 _side_summary）。
    """
    import os
    filt = names if names is not None else sel
    if not file and isinstance(sel, str) and sel.strip() and os.path.exists(sel):
        ext = os.path.splitext(sel)[1].lower()
        if ext == ".blend":
            file, filt = sel, (names if names is not None else None)   # a 直接给 .blend 路径也认
        else:
            return {"ok": False, "side": tag, "source": "path:%s" % sel,
                    "error": "只认 .blend 文件（给的是 %s）" % (ext or "无扩展名"),
                    "hint": ".obj/.stl 的两两比较请用 audit_drift（同一条 _src_tris 通路）"}
    if file:
        objs, cleanup, info = _load_file_objects(file, _file_names(filt), include_hidden)
        name_of = info.pop("_name_of", None)
        src = info.get("source")
        if not info.get("ok"):
            return {"ok": False, "side": tag, "source": src, "error": info.get("error"),
                    "cleanup": cleanup, "file_info": info,
                    "hint": "file= 侧：文件要存在、里面有可见 mesh；空产出必须 ok:false"}
        note = "file= 临时加载（跑完即删；名字用文件里的原名）"
    else:
        objs, missing, err = _session_side(filt, include_hidden)
        cleanup, info, name_of, src, note = None, None, None, "session", None
        if err:
            return {"ok": False, "side": tag, "source": src, "error": err}
        if not objs:
            return {"ok": False, "side": tag, "source": src, "error": "该端 0 个可见 mesh", "missing": missing,
                    "hint": "名字/集合名对不上，或都被 hide_render/hide_viewport 挡了（include_hidden=true 放行）"}
    tris, owner, _boxes = _world_tris(objs)
    nm_list = [_nm(o, name_of) for o in objs]
    if tris is None or len(tris) == 0:
        return {"ok": False, "side": tag, "source": src, "error": "该端 0 个三角面（对象在但没几何）",
                "object_names": nm_list, "cleanup": cleanup, "file_info": info}
    p = tris.reshape(-1, 3)
    return {"ok": True, "side": tag, "source": src, "tris": tris, "owner": owner, "objs": objs,
            "names": nm_list, "name_of": name_of, "aabb": (p.min(axis=0), p.max(axis=0)),
            "cleanup": cleanup, "file_info": info, "note": note}


def _side_summary(s, mm_per_unit):
    """一端的上报摘要（绝不带 numpy 对象）。"""
    out = {"source": s.get("source"), "object_names": s.get("names") or [],
           "objects": len(s.get("objs") or []),
           "tris": (int(len(s["tris"])) if s.get("tris") is not None else 0)}
    if s.get("aabb") is not None:
        out["aabb"] = _box_fields(s["aabb"][0], s["aabb"][1], mm_per_unit)
    if not s.get("ok") and s.get("error"):
        out["error"] = s.get("error")
    if s.get("hint"):
        out["hint"] = s["hint"]
    if s.get("missing"):
        out["missing"] = s["missing"]
    if s.get("file_info"):
        fi = s["file_info"]
        out["file_load"] = {k: fi.get(k) for k in ("path", "requested", "loaded", "mesh", "non_mesh",
                                                   "missing", "hidden_skipped", "renamed", "tmp_collection",
                                                   "name_map_note", "mesh_names") if fi.get(k) is not None}
    return out


def _resolve_two_sides(a, b, file_a, file_b, objects_a, objects_b, include_hidden):
    """两端一起解析（文件侧会临时加载）→ (sides, cleanups)。cleanup 由调用方在 finally 里跑。"""
    sides, cleanups = {}, {}
    for tag, (sel, f, names) in (("a", (a, file_a, objects_a)), ("b", (b, file_b, objects_b))):
        sd = _side_tris(tag, sel, f, names, include_hidden)
        sides[tag] = sd
        if sd.get("cleanup"):
            cleanups[tag] = sd["cleanup"]
    return sides, cleanups


def _sides_error(sides, mm_per_unit):
    """任一端失败 → 统一的 ok:false 体（两端摘要都带上，方便定位是哪一端）。"""
    bad = {t: sides[t].get("error") for t in ("a", "b") if not sides[t].get("ok")}
    if not bad:
        return None
    return {"ok": False, "analyzed": False,
            "error": "来源失败：" + "；".join("%s: %s" % (t, bad[t]) for t in sorted(bad)),
            "sides": {t: _side_summary(sides[t], mm_per_unit) for t in ("a", "b")},
            "hint": "两端各给一个来源：会话对象名/集合名（a/objects_a、b/objects_b），"
                    "或 .blend（file_a/file_b，用 objects_a/objects_b 在文件内筛名）"}


def _tri_tri_segment(ta, tb):
    """两个三角面的真实交线段 → (point[3]|None, length|None, kind)。

    做法：两个三角面所在平面求交得到公共直线 (P, D)，再用三角形三条边的面内半空间把这条直线
    裁出 t 区间，两区间之交就是真交线段（P 同时在两个平面上，所以参数 t 对两侧一致）。
    共面/平行（overlap() 本来就不报共面）或裁剪失败 → 退回"两重心连线的中点"，kind 里写明 ——
    不给假精度。
    """
    import numpy as np
    a = np.asarray(ta, dtype=np.float64)
    b = np.asarray(tb, dtype=np.float64)
    ca, cb = a.mean(axis=0), b.mean(axis=0)
    mid = (ca + cb) / 2.0
    n1 = np.cross(a[1] - a[0], a[2] - a[0])
    n2 = np.cross(b[1] - b[0], b[2] - b[0])
    l1, l2 = float(np.linalg.norm(n1)), float(np.linalg.norm(n2))
    if l1 <= 1e-12 or l2 <= 1e-12:
        return mid, None, "退化三角面（零面积）→ 用重心中点"
    n1, n2 = n1 / l1, n2 / l2
    d = np.cross(n1, n2)
    dn = float(np.linalg.norm(d))
    if dn <= 1e-9:
        return mid, None, "两面共面/平行 → 用重心中点（共面覆盖 overlap() 本来就不报）"
    d = d / dn
    c1, c2 = float(np.dot(n1, a[0])), float(np.dot(n2, b[0]))
    P = (c1 * np.cross(n2, d) + c2 * np.cross(d, n1)) / (dn * dn)
    t1 = _line_clip(P, d, a, n1)
    t2 = _line_clip(P, d, b, n2)
    if t1 is None or t2 is None:
        return mid, None, "直线裁剪失败（边界/退化情形）→ 用重心中点"
    lo, hi = max(t1[0], t2[0]), min(t1[1], t2[1])
    if hi < lo - 1e-12:
        return mid, None, "裁剪后区间为空 → 用重心中点"
    return (P + ((lo + hi) / 2.0) * d), max(0.0, hi - lo), "平面求交 + 三角形裁剪（真交线段）"


def _line_clip(P, d, tri, n):
    """直线 P+t·d（已知落在三角形平面上）被三角形裁出的 t 区间 [lo,hi]；无交 → None。"""
    import numpy as np
    lo, hi = -1e30, 1e30
    for i in range(3):
        u, v, w = tri[i], tri[(i + 1) % 3], tri[(i + 2) % 3]
        m = np.cross(v - u, n)                     # 面内法向
        if float(np.dot(w - u, m)) < 0:            # ★ 用第三个顶点定符号：内侧必须 ≥0（不依赖绕序）
            m = -m
        fu, fd = float(np.dot(P - u, m)), float(np.dot(d, m))
        if abs(fd) < 1e-15:
            if fu < -1e-12:
                return None                        # 整条直线都在这条边外侧
            continue
        t = -fu / fd
        if fd > 0:
            lo = max(lo, t)
        else:
            hi = min(hi, t)
    if lo > hi + 1e-12:
        return None
    return (lo, hi)


def _pairs_bbox(ta, tb, pairs):
    """交叠三角面顶点的世界 bbox（= 交集体的**超集**盒）→ (lo, hi, truncated)。

    为什么这个盒有效：交集体的边界由"∂A 在 B 内的部分 + ∂B 在 A 内的部分 + ∂A∩∂B"组成，前两类
    必然落在**与对方相交的三角面**上，第三类更是交叠面对本身 → 交集体一定在这个盒里。
    """
    import numpy as np
    sel = pairs[:int(PAIRS_BBOX_CAP)]
    ia = np.fromiter((int(p[0]) for p in sel), dtype=np.int64, count=len(sel))
    ib = np.fromiter((int(p[1]) for p in sel), dtype=np.int64, count=len(sel))
    p = np.concatenate([ta[ia].reshape(-1, 3), tb[ib].reshape(-1, 3)], axis=0)
    return p.min(axis=0), p.max(axis=0), bool(len(pairs) > len(sel))


def _pair_rows(sa, sb, ta, tb, pairs, limit, mm_per_unit):
    """前 limit 对交叠面的结构化行（对象名 + 三角面下标 + 交点/交线段长）。"""
    rows = []
    for pr in pairs[:max(0, int(limit))]:
        ia, ib = int(pr[0]), int(pr[1])
        pt, seglen, kind = _tri_tri_segment(ta[ia], tb[ib])
        ca = ta[ia].mean(axis=0)
        cb = tb[ib].mean(axis=0)
        rows.append({"a": sa["names"][int(sa["owner"][ia])], "b": sb["names"][int(sb["owner"][ib])],
                     "tri_a": ia, "tri_b": ib,
                     "point": ([_r(float(x), 6) for x in pt] if pt is not None else None),
                     "point_mm": ([_r(float(x) * mm_per_unit, 4) for x in pt] if pt is not None else None),
                     "point_kind": kind,
                     "segment_len_mm": (None if seglen is None else _r(seglen * mm_per_unit, 4)),
                     "centroid_dist_mm": _r(float(((ca - cb) ** 2).sum() ** 0.5) * mm_per_unit, 4)})
    return rows


def _per_object_counts(sa, sb, pairs):
    """每端每个对象的交叠对数（只列 >0 的）。"""
    import numpy as np
    out = {"a": {}, "b": {}}
    if not pairs:
        return out
    arr = np.asarray(pairs, dtype=np.int64)
    ca = np.bincount(sa["owner"][arr[:, 0]], minlength=len(sa["objs"]))
    cb = np.bincount(sb["owner"][arr[:, 1]], minlength=len(sb["objs"]))
    out["a"] = {sa["names"][i]: int(ca[i]) for i in range(len(sa["objs"])) if int(ca[i]) > 0}
    out["b"] = {sb["names"][i]: int(cb[i]) for i in range(len(sb["objs"])) if int(cb[i]) > 0}
    return out


def _ray_limit(lo, hi, p, dr, pad):
    """射线**参数**上限：到本侧自身 AABB 边界外一点点（出了自己的包围盒就再也打不到面）。

    ★ 必须除以方向分量：dr 不一定是轴对齐单位向量（diag 实测：漏除会让斜方向的射线提前 1.73 倍被截断
      → 全部点判成外侧）。
    """
    lim = None
    for k in range(3):
        dk = float(dr[k])
        if abs(dk) < 1e-15:
            continue
        d = ((float(hi[k]) - p[k]) if dk > 0 else (p[k] - float(lo[k]))) / abs(dk)
        lim = d if lim is None else min(lim, d)
    return pad if lim is None else (lim + pad)


def _inside_mask(tree, pts, lo, hi, dirs=((1.0, 0.0, 0.0),), max_steps=INTERFERENCE_MAX_STEPS):
    """射线奇偶校验：点沿 +X（默认）打射线，命中次数为奇数 ⇒ 在闭合网格内；多方向取多数票。

    * 迭代到区间外：每命中一次，从命中点沿方向前进 eps 再打；射线长度上限锁在本侧自身 AABB 之外
      一点点 → 迭代天然有界；仍超过 max_steps 的点记 unknown（**不猜**，数目写进返回体）；
    * ★ 推进步长 eps 必须**大于 float32 几何的坐标分辨率**（本模块第一轮自检抓到的真 bug，实测数据：
      命中点由 float32 算出会落在真平面**之后** —— 0.499999911 vs 0.5；若 eps 只有 1e-7，下一发射线会
      以 dist=0 再命中同一张面 → 命中数变偶数 → 内侧点被判成外侧，实测 459/50000 = 0.92%，
      且集中在射线行程 t≈1 的那一侧，是**系统性偏差**）。所以
      eps = max(span×1e-6, 8×float32_eps×坐标量级)（实测 1e-6 时 0 漏判）。
    * 命中距离 ≤0（射线起点正贴在面上）→ 该点记 unknown（奇偶不可判，不猜）；
    * 只在闭合流形网格上成立 —— 调用方负责先做流形检查（_mesh_health）。
    返回 (mask, unknown, stats)
    """
    import numpy as np
    n = int(len(pts))
    mask = np.zeros(n, dtype=bool)
    if n == 0:
        return mask, 0, {"points_total": 0}
    span = max(float(hi[0]) - float(lo[0]), float(hi[1]) - float(lo[1]), float(hi[2]) - float(lo[2]))
    mag = max(span, float(np.abs(np.asarray(lo, dtype=np.float64)).max()),
              float(np.abs(np.asarray(hi, dtype=np.float64)).max()))
    eps = max(span * 1e-6, 8.0 * 1.1920929e-07 * mag, 1e-12)
    pad = eps * 4.0
    inside_box = np.ones(n, dtype=bool)
    for k in range(3):
        inside_box &= (pts[:, k] >= float(lo[k]) - 1e-12) & (pts[:, k] <= float(hi[k]) + 1e-12)
    idx = np.nonzero(inside_box)[0]
    unknown, maxhits = 0, 0
    nd = max(1, len(dirs))
    for i in idx:
        p = pts[i]
        votes, bad, on_surface = 0, False, False
        for (dx, dy, dz) in dirs:
            ox, oy, oz = float(p[0]) + dx * eps, float(p[1]) + dy * eps, float(p[2]) + dz * eps
            lim = _ray_limit(lo, hi, (ox, oy, oz), (dx, dy, dz), pad)
            cnt, steps = 0, 0
            while steps < int(max_steps):
                loc, nor, fidx, dist = tree.ray_cast((ox, oy, oz), (dx, dy, dz), lim)
                if loc is None:
                    break
                if dist is not None and float(dist) <= 0.0:
                    on_surface = True               # 起点贴在面上 → 奇偶不可判，别硬判
                    break
                cnt += 1
                steps += 1
                ox, oy, oz = loc[0] + dx * eps, loc[1] + dy * eps, loc[2] + dz * eps
                lim = _ray_limit(lo, hi, (ox, oy, oz), (dx, dy, dz), pad)
                if lim <= 0:
                    break
            else:
                bad = True                       # while 走完 max_steps 没 break → 到上限了
            if cnt > maxhits:
                maxhits = cnt
            if cnt % 2 == 1:
                votes += 1
        if bad or on_surface:
            unknown += 1
            continue
        mask[i] = bool(votes * 2 > nd)
    return mask, int(unknown), {"points_total": n, "points_in_own_bbox": int(len(idx)),
                               "rays": int(nd), "max_hits": int(maxhits), "eps_units": eps,
                               "eps_note": "推进步长 = max(span×1e-6, 8×float32_eps×坐标量级)"}


def _mesh_health(objs, name_of=None):
    """闭合性体检（与 audit_mesh 同口径，以对象原始网格为准）：→ (rows, closed_all)。

    closed = boundary_edges==0 且 nonmanifold_edges==0；0 个面的对象一律 closed=False（无从校验）。
    """
    rows, closed_all = [], True
    for ob in objs:
        me = ob.data
        b, nmk = None, None
        if len(me.polygons) > 0:
            bmw = bmesh.new()
            try:
                bmw.from_mesh(me)
                b = int(sum(1 for e in bmw.edges if len(e.link_faces) == 1))
                nmk = int(sum(1 for e in bmw.edges if not e.is_manifold))
            except Exception:
                b, nmk = None, None
            finally:
                bmw.free()
        closed = bool(b == 0 and nmk == 0)
        rows.append({"name": _nm(ob, name_of), "polys": int(len(me.polygons)),
                     "boundary_edges": b, "nonmanifold_edges": nmk, "closed": closed})
        closed_all = closed_all and closed
    return rows, bool(closed_all)


def _containment_probe(ta, tb, tree_a, tree_b, box_a, box_b):
    """"一件完全包在另一件里"的探针：overlap() 对这种情形一条都不报 → 别读成无干涉。

    快筛：一侧 AABB 被另一侧 AABB 包含（带容差）才继续；命中就采样内层曲面点，用射线奇偶看
    有没有落在外层内部。返回 None（没有嵌套迹象，不花这个钱）或 dict。
    """
    import numpy as np
    lo_a, hi_a = box_a
    lo_b, hi_b = box_b
    tol = max(1e-9, 1e-9 * float(max(hi_a[i] - lo_a[i] for i in range(3))))
    inner = None
    if all(lo_a[k] <= lo_b[k] + tol and hi_b[k] <= hi_a[k] + tol for k in range(3)):
        inner, outer = "b", "a"
    elif all(lo_b[k] <= lo_a[k] + tol and hi_a[k] <= hi_b[k] + tol for k in range(3)):
        inner, outer = "a", "b"
    if inner is None:
        return None
    if inner == "b":
        pts = _sample(tb, 64, np.random.default_rng(0))
        tree_o, lo_o, hi_o = tree_a, lo_a, hi_a
    else:
        pts = _sample(ta, 64, np.random.default_rng(0))
        tree_o, lo_o, hi_o = tree_b, lo_b, hi_b
    mask, unk, _st = _inside_mask(tree_o, pts, lo_o, hi_o)
    inside_n = int(mask.sum())
    return {"suspected": bool(inside_n > 0), "inner": inner, "outer": outer,
            "probe_points": int(len(pts)), "inside_points": inside_n, "parity_unknown": int(unk),
            "note": ("面无相交，但 %s 的 AABB 完全落在 %s 内、且内层曲面有 %d/%d 个采样点落在外层内部 → "
                     "可能「一件完全包在另一件里」（overlap() 天生不报这种情形）→ 别读成无干涉"
                     % (inner, outer, inside_n, len(pts))) if inside_n > 0 else
                    ("%s 的 AABB 落在 %s 内，但内层曲面采样点没有一个落在外层内部 → 无包含迹象"
                     % (inner, outer))}


def _contact_probe(ta, tb, tree_a, tree_b, eps_units, mm_per_unit, max_pts=CONTACT_PROBE_PTS):
    """贴没贴上：两侧曲面各采 max_pts 个点，量到**对面曲面**的最近距离（BVHTree.find_nearest）。

    pair_count=0 时补这一步的理由：overlap() 实测不报"共面且互相覆盖"的面，而共面贴合/贴而未重合
    在装配里极常见 —— 直接读 pair_count:0 会漏。判据口径与 _conn_analyze 的 micro_gap_mm 一致
    （默认 0.3 mm 算相接）。
    """
    import numpy as np
    rng = np.random.default_rng(0)
    pa = _sample(ta, int(max_pts), rng)
    pb = _sample(tb, int(max_pts), rng)
    best, within = None, 0
    for pts, tree in ((pa, tree_b), (pb, tree_a)):
        for p in pts:
            hit = tree.find_nearest((float(p[0]), float(p[1]), float(p[2])))
            if hit is None or hit[0] is None or hit[3] is None:
                continue
            d = float(hit[3])
            if best is None or d < best:
                best = d
            if d <= float(eps_units):
                within += 1
    return {"contact": bool(within > 0), "min_dist_units": (None if best is None else _r(best, 8)),
            "min_dist_mm": (None if best is None else _r(best * mm_per_unit, 5)),
            "points_within_eps": int(within), "probe_points": int(len(pa) + len(pb)),
            "eps_mm": _r(float(eps_units) * mm_per_unit, 5),
            "note": "面对面最近距离（曲面采样近似，不是精确最小距离）；eps 口径与连接的 micro_gap_mm 一致"}


def _estimate_volume(ta, tb, tree_a, tree_b, box, samples, seed, box_a, box_b, rays=1):
    """在 box 内均匀撒点 → 射线奇偶判"同时在 A、B 内" → (frac, n, stats)。

    frac × 盒体积就是 A∩B 体积的无偏估计（盒是交集的超集，盒外的点本来就不可能同时在两者内）。
    """
    import numpy as np
    n = max(1, int(samples))
    lo, hi = box
    rng = np.random.default_rng(int(seed))
    pts = np.asarray(lo, dtype=np.float64) + rng.random((n, 3)) * (
        np.asarray(hi, dtype=np.float64) - np.asarray(lo, dtype=np.float64))
    dirs = ((1.0, 0.0, 0.0), (0.0, 1.0, 0.0), (0.0, 0.0, 1.0))[:max(1, min(3, int(rays)))]
    ma, unk_a, sa = _inside_mask(tree_a, pts, box_a[0], box_a[1], dirs)
    mb, unk_b, sb = _inside_mask(tree_b, pts, box_b[0], box_b[1], dirs)
    both = ma & mb
    nb = int(both.sum())
    return (float(nb) / float(n), n, {"inside_a": int(ma.sum()), "inside_b": int(mb.sum()),
                                      "inside_both": nb, "unknown_a": int(unk_a), "unknown_b": int(unk_b),
                                      "parity_a": sa, "parity_b": sb, "points": int(len(pts))})


def audit_overlap(a=None, b=None, file_a=None, file_b=None, objects_a=None, objects_b=None,
                  limit=200, include_hidden=False, max_tris=None):
    """面-对重叠（结构化）：这堆面到底穿没穿？穿在哪几对三角面上？

    两端各自可以是：**当前会话**的对象名/名字列表/集合名（a / objects_a），或**另一个 .blend**
    （file_a + objects_a 在文件里筛名；不筛就取文件里全部可见 mesh）。a/b 直接给 .blend 路径也认。

    返回（关键键）：
      ok / analyzed / method = bvh-overlap(三角面精确)
      pair_count          交叠面对**总数**（不受 limit 截断；0 是结论，不是"没跑"）
      pairs               前 limit 对：[a,b,tri_a,tri_b,point,point_mm,point_kind,segment_len_mm,
                          centroid_dist_mm]（point=真交线段上的点；共面/退化时退重心中点并写明）
      intersection_bbox   两侧世界 AABB 的**交盒**（交集体必在此盒内；不相交时 null）
      overlap_faces_bbox  交叠三角面顶点的 bbox（= audit_interference 的采样盒）
      per_object          每端每个对象的交叠对数；tris_a/tris_b；aabb_a/aabb_b；sides；units；cleanup
    三态：无交叠 → ok=true + pair_count=0（真结论）；某端为空/文件不存在 → ok=false；
          三角面超上限（BVH_MAX_TRIS，可 max_tris 覆盖）→ ok=false + analyzed=false（不许读成"没问题"）。

    ⚠ 覆盖边界（实测 Blender 5.2）：overlap() 是*真*三角-三角相交（AABB 重叠但不穿透的面对不报），
    但**共面且互相覆盖**的两张面不报 → pair_count=0 不排除"共面贴合"；两侧 bbox 相交而面无相交时
    会自动跑一次 contact_probe（最近距离）与 containment_probe（完全包含），免得把漏报读成没问题。
    """
    import time as _t
    t0 = _t.perf_counter()
    m_per_unit, mm_per_unit = _units()
    ub = {"m_per_unit": _r(m_per_unit, 6), "mm_per_unit": _r(mm_per_unit, 6)}
    sides, cleanups = {}, {}

    def _body():
        base = {"method": "bvh-overlap(三角面精确)", "units": ub, "limit": int(limit),
                "include_hidden": bool(include_hidden),
                "sides": {t: _side_summary(sides[t], mm_per_unit) for t in ("a", "b")}}
        err = _sides_error(sides, mm_per_unit)
        if err:
            return dict(base, **err)
        sa, sb = sides["a"], sides["b"]
        ta, tb = sa["tris"], sb["tris"]
        na, nb = int(len(ta)), int(len(tb))
        cap = int(max_tris or BVH_MAX_TRIS)
        over = [(t, v) for t, v in (("a", na), ("b", nb)) if v > cap]
        if over:
            return dict(base, ok=False, analyzed=False, tris_a=na, tris_b=nb, max_tris=cap,
                        error="三角面超上限：%s（上限 %d，可用 max_tris 覆盖）→ 不分析"
                              % ("/".join("%s=%d" % kv for kv in over), cap),
                        note="未分析 ≠ 无重叠：这是「没跑」，不能读成「没问题」")
        tree_a, tree_b = _bvh_from_tris(ta), _bvh_from_tris(tb)
        pairs = list(tree_a.overlap(tree_b) or [])
        pair_count = len(pairs)
        out = dict(base, ok=True, analyzed=True, tris_a=na, tris_b=nb, pair_count=pair_count,
                   aabb_a=_box_fields(sa["aabb"][0], sa["aabb"][1], mm_per_unit),
                   aabb_b=_box_fields(sb["aabb"][0], sb["aabb"][1], mm_per_unit),
                   point_kind="point=真交线段中点（平面求交 + 三角形裁剪）；退化/共面时退两重心中点（kind 说明）",
                   coverage="overlap() 实测语义：报真三角-三角相交（AABB 重叠但不穿透的不报）；"
                            "共面且互相覆盖的面不报 → 共面贴合看 contact_probe")
        inter = _box_intersect(sa["aabb"][0], sa["aabb"][1], sb["aabb"][0], sb["aabb"][1])
        provable_empty = bool(inter is None or _boxvol(inter) <= 0.0)
        if provable_empty:
            out["intersection_bbox"] = None
            out["intersection_bbox_mm"] = None
            out["intersection_bbox_note"] = ("两侧世界 AABB 不相交/只贴不叠 → 交集体为空"
                                             "（交集体必在两 AABB 的交盒里，所以这是可证结论）")
        else:
            bi, bm = _box_pair(inter[0], inter[1], mm_per_unit)
            out["intersection_bbox"] = bi
            out["intersection_bbox_mm"] = bm
        out["containment_probe"] = None
        out["contact_probe"] = None
        if pair_count == 0:
            out["pairs"] = []
            out["overlap_faces_bbox"] = None
            out["overlap_faces_bbox_mm"] = None
            out["per_object"] = {"a": {}, "b": {}}
            if provable_empty:
                out["note"] = "两侧世界 AABB 不相交/只贴不叠 → 面-对重叠必为 0（这是结论，不是「没跑」）"
            else:
                cont = _containment_probe(ta, tb, tree_a, tree_b, sa["aabb"], sb["aabb"])
                out["containment_probe"] = cont
                if cont and cont.get("suspected"):
                    out["note"] = cont["note"] + "；要体积请用 audit_interference"
                else:
                    cp = _contact_probe(ta, tb, tree_a, tree_b, MICRO_GAP_MM / mm_per_unit, mm_per_unit)
                    out["contact_probe"] = cp
                    out["note"] = ("面无相交（非共面穿透为 0）" +
                                   ("，但两曲面已贴上：最近 %.5f mm（≤ %.2f mm 口径）→ 共面贴合/贴而未重合"
                                    % (cp["min_dist_mm"], MICRO_GAP_MM) if cp["contact"] else
                                    "；两曲面最近 %.5f mm（未接触）" % (cp["min_dist_mm"] if cp["min_dist_mm"] is not None else -1.0)) +
                                   "。⚠ overlap() 不报共面重叠，共面覆盖情形本算子看不出来")
        else:
            out["pairs"] = _pair_rows(sa, sb, ta, tb, pairs, limit, mm_per_unit)
            fb_lo, fb_hi, trunc = _pairs_bbox(ta, tb, pairs)
            bf, bfm = _box_pair(fb_lo, fb_hi, mm_per_unit)
            out["overlap_faces_bbox"] = bf
            out["overlap_faces_bbox_mm"] = bfm
            if trunc:
                out["overlap_faces_bbox_note"] = ("面对数超过 %d，bbox 只统计前 %d 对（pair_count 仍是全量）"
                                                  % (PAIRS_BBOX_CAP, PAIRS_BBOX_CAP))
            out["per_object"] = _per_object_counts(sa, sb, pairs)
            seg = [r["segment_len_mm"] for r in out["pairs"] if r["segment_len_mm"] is not None]
            out["max_segment_len_mm"] = (max(seg) if seg else None)
            out["note"] = ("面-对重叠 %d 对%s（面对数=精确相交的三角面对；AABB 重叠但不穿透的不计）"
                           % (pair_count, "" if pair_count <= int(limit) else "，pairs 只列前 %d 对" % int(limit)))
        return out

    try:
        sides, cleanups = _resolve_two_sides(a, b, file_a, file_b, objects_a, objects_b, include_hidden)
        res = _body()
    except Exception as e:
        res = _exc_body(e, "overlap")
    finally:
        for fn in cleanups.values():
            fn()
    if cleanups:
        res["cleanup"] = {t: cleanups[t]() for t in sorted(cleanups)}
    res["ms"] = int((_t.perf_counter() - t0) * 1000)
    return _j(res)


def audit_interference(a=None, b=None, file_a=None, file_b=None, objects_a=None, objects_b=None,
                       samples=INTERFERENCE_SAMPLES, seed=0, limit=200, include_hidden=False,
                       material_mm3=MATERIAL_VOLUME_MM3, max_tris=None, rays=1):
    """A∩B 交集体积估计（mm³）—— "弹匣悬空：井口盖板 10719.7 mm³" 那一类数的算子化。

    两端来源与 audit_overlap 完全相同（会话对象/集合，或 file_a/file_b 指到 .blend；a/b 直接给
    .blend 路径也认）。**不用 bpy boolean**：无头里也能跑，不建临时对象、不改场景。

    做法（照反馈自建脚本的口径收敛）：
      ① tree_a.overlap(tree_b) 拿交叠三角面对 → 只在这些面的世界 bbox 里采样（否则高效不了）；
      ② 该盒内均匀撒 samples 个点（numpy.random.default_rng(seed)）；
      ③ 每点用**射线奇偶**（默认沿 +X 打；rays=3 时 X/Y/Z 取多数票）分别判是否在 A 内、在 B 内；
      ④ volume = frac × 盒体积；ci95 = 1.96·sqrt(frac(1-frac)/n) × 盒体积。

    ⚠ 诚实边界（同时写进返回体 honest_limits / error_caliber）：
      * **不是精确布尔**：Monte-Carlo 估计，相对误差(95%) ≈ 1.96·sqrt((1-p)/(p·n))（p=总体积占盒比）。
        实测口径：n=200k 时 p=0.5 → ±0.44%、p=0.1 → ±1.31%、p=0.01 → ±4.4%（samples×4 → 误差减半）。
      * 只在**交叠三角面的 bbox**内采样（该盒是交集体的超集，不是交集体本身）。
      * 射线奇偶只在**闭合流形**网格上成立：检测到边界边/非流形边 → verdict=unresolved、volume=null
        （不给一个看着很准的错数），why 里写清缺什么证据。
      * 一面被完全包含（面无相交）时 overlap() 给 0 对 → 由 containment_probe 兜住，不误判为 refuted。

    三态 verdict：supported（volume>0 且 95% 下界>0 且 ≥ 可执行下限）/ refuted（无干涉：给上界 mm³）/
      unresolved（不流形、样本不足、上界还不够紧 → 说清差多少）。
    必给键：volume_mm3 / volume_ci95_mm3 / verdict / why / bbox_mm（另有 units 与 mm 全套）。
    """
    import time as _t
    t0 = _t.perf_counter()
    m_per_unit, mm_per_unit = _units()
    mm3 = float(mm_per_unit) ** 3
    floor = float(material_mm3)
    ub = {"m_per_unit": _r(m_per_unit, 6), "mm_per_unit": _r(mm_per_unit, 6),
          "volume_units3_to_mm3": _r(mm3, 6)}
    sides, cleanups = {}, {}

    def _body():
        base = {"method": "monte-carlo(ray-parity, 只在交叠 bbox 内采样)", "units": ub,
                "samples": int(max(1, int(samples))), "seed": int(seed), "rays": int(max(1, min(3, int(rays)))),
                "include_hidden": bool(include_hidden), "material_mm3": _r(floor, 6), "limit": int(limit),
                "sides": {t: _side_summary(sides[t], mm_per_unit) for t in ("a", "b")},
                "error_caliber": {
                    "formula": "相对误差(95%) ≈ 1.96·sqrt((1-p)/(p·n))（p=总体积/盒体积，n=samples_used）",
                    "n_200k": {"p=0.5": "±0.44%", "p=0.1": "±1.31%", "p=0.01": "±4.4%"},
                    "zero_hit_upper": "frac=0 时用单侧 95% 上界 1-0.05^(1/n) ≈ 3/n（× 盒体积）",
                    "how_to_tighten": "samples×4 → 误差减半（∝1/√n）；盒越贴合交集体，p 越大越准",
                    "material_mm3": "可执行下限 %.3f mm³：低于它属于网格弦差/噪声量级，不作为真干涉" % floor}}
        err = _sides_error(sides, mm_per_unit)
        if err:
            return dict(base, **err)
        sa, sb = sides["a"], sides["b"]
        ta, tb = sa["tris"], sb["tris"]
        na, nb = int(len(ta)), int(len(tb))
        cap = int(max_tris or BVH_MAX_TRIS)
        over = [(t, v) for t, v in (("a", na), ("b", nb)) if v > cap]
        if over:
            return dict(base, ok=False, analyzed=False, tris_a=na, tris_b=nb, max_tris=cap,
                        error="三角面超上限：%s（上限 %d，可用 max_tris 覆盖）→ 不分析"
                              % ("/".join("%s=%d" % kv for kv in over), cap),
                        note="未分析 ≠ 无干涉：verdict 不给，别把「没跑」读成「没问题」")
        tree_a, tree_b = _bvh_from_tris(ta), _bvh_from_tris(tb)
        pairs = list(tree_a.overlap(tree_b) or [])
        pair_count = len(pairs)
        out = dict(base, ok=True, analyzed=True, tris_a=na, tris_b=nb, pair_count=pair_count,
                   pairs=_pair_rows(sa, sb, ta, tb, pairs, limit, mm_per_unit),
                   aabb_a=_box_fields(sa["aabb"][0], sa["aabb"][1], mm_per_unit),
                   aabb_b=_box_fields(sb["aabb"][0], sb["aabb"][1], mm_per_unit),
                   honest_limits=[
                       "不是精确布尔：Monte-Carlo 估计，误差口径见 error_caliber",
                       "只采样交叠三角面的 bbox —— 该盒是交集体的超集，不是交集体本身",
                       "射线奇偶只在闭合流形网格上成立：非流形/开放网格一律 unresolved（不给数）",
                       "共面贴合（零厚度接触）体积本来就是 0；overlap() 也不报共面相交 → 别拿 pair_count=0 "
                       "当成「没有贴合」，贴合看 audit_overlap 的 contact_probe"],
                   why=None, verdict=None, volume_mm3=None, volume_units3=None, volume_ci95_mm3=None)
        health_a, closed_a = _mesh_health(sa["objs"], sa["name_of"])
        health_b, closed_b = _mesh_health(sb["objs"], sb["name_of"])
        out["mesh_health"] = {"a": health_a, "b": health_b, "closed_a": closed_a, "closed_b": closed_b}
        inter = _box_intersect(sa["aabb"][0], sa["aabb"][1], sb["aabb"][0], sb["aabb"][1])
        provable_empty = bool(inter is None or _boxvol(inter) <= 0.0)
        cand = None                    # 采样盒
        cand_src = None
        if pair_count > 0:
            fb_lo, fb_hi, trunc = _pairs_bbox(ta, tb, pairs)
            cand, cand_src = (fb_lo, fb_hi), "overlap_faces(交叠三角面的世界 bbox)"
            if trunc:
                out["pairs_bbox_note"] = ("面对数超过 %d，采样盒只按前 %d 对统计（pair_count 仍是全量）"
                                          % (PAIRS_BBOX_CAP, PAIRS_BBOX_CAP))
        elif not provable_empty:
            cand, cand_src = inter, "aabb_intersect(两侧世界 AABB 的交盒；面无相交时的保守盒)"
        # 非流形/开放 → 降级（先判，省掉采样；但 bbox 与对数照报）
        if pair_count > 0 and not (closed_a and closed_b):
            who = [t for t, c in (("a", closed_a), ("b", closed_b)) if not c]
            det = []
            for t in who:
                rows = health_a if t == "a" else health_b
                bad = [r for r in rows if not r["closed"]]
                det.append("%s: %s" % (t, "、".join("%s(boundary=%s, nonmanifold=%s)"
                                                    % (r["name"], r["boundary_edges"], r["nonmanifold_edges"])
                                                    for r in bad[:4])))
            out.update({"verdict": "unresolved", "volume_mm3": None, "volume_units3": None,
                        "volume_ci95_mm3": None, "frac": None, "samples_used": 0,
                        "in_overlap_bbox": 0, "inside_a": 0, "inside_b": 0, "inside_both": 0,
                        "why": "射线奇偶只在闭合流形网格上成立，而 %s 不是闭合流形（%s）→ 不给体积数字。"
                               "缺证据：boundary_edges=0 且 nonmanifold_edges=0 的闭合网格"
                               % ("/".join(who), " | ".join(det)),
                        "next_step": "把该件修补成闭合流形（或改用 audit_overlap 的面对数）再估体积"})
            _fill_bbox(out, cand, cand_src, mm_per_unit, mm3)
            return out
        if cand is None:               # 可证为空（AABB 不相交/只贴不叠）
            out.update({"verdict": "refuted", "volume_mm3": 0.0, "volume_units3": 0.0,
                        "volume_ci95_mm3": 0.0, "frac": 0.0, "samples_used": 0, "in_overlap_bbox": 0,
                        "inside_a": 0, "inside_b": 0, "inside_both": 0,
                        "upper_bound_mm3": _r(floor, 6),
                        "upper_bound_kind": "两侧世界 AABB 不相交/只贴不叠 → 交集体可证为空（上界即可执行下限）",
                        "why": "面-对重叠 %d 对，且两侧世界 AABB 不相交/只贴不叠 → A∩B 为空（可证，未采样）"
                               % pair_count})
            _fill_bbox(out, None, None, mm_per_unit, mm3)
            return out
        box_vol = float(_boxvol(cand))
        frac, n_used, st = _estimate_volume(ta, tb, tree_a, tree_b, cand, samples, seed, sa["aabb"], sb["aabb"],
                                           rays=rays)
        out.update({"samples_used": int(n_used), "in_overlap_bbox": int(st["points"]),
                    "inside_a": st["inside_a"], "inside_b": st["inside_b"], "inside_both": st["inside_both"],
                    "parity_unknown": {"a": st["unknown_a"], "b": st["unknown_b"]},
                    "frac": _r(frac, 8), "box_volume_units3": _r(box_vol, 8),
                    "box_volume_mm3": _r(box_vol * mm3, 4), "parity_stats": {"a": st["parity_a"], "b": st["parity_b"]}})
        _fill_bbox(out, cand, cand_src, mm_per_unit, mm3)
        vol_u3 = frac * box_vol
        vol_mm3 = vol_u3 * mm3
        sd_frac = (frac * (1.0 - frac) / float(n_used)) ** 0.5
        ci95_frac = 1.96 * sd_frac
        up95_frac = min(1.0, frac + 1.645 * sd_frac) if frac > 0 else min(1.0, 1.0 - 0.05 ** (1.0 / float(n_used)))
        out.update({"volume_units3": _r(vol_u3, 8), "volume_mm3": _r(vol_mm3, 4),
                    "volume_ci95_mm3": _r(ci95_frac * box_vol * mm3, 4),
                    "ci95_lower_mm3": _r(max(0.0, (vol_u3 - ci95_frac * box_vol)) * mm3, 4),
                    "ci95_upper_mm3": _r(min(1.0, frac + ci95_frac) * box_vol * mm3, 4),
                    "relative_ci95": (_r(ci95_frac / frac, 6) if frac > 0 else None),
                    "frac_ci95": _r(ci95_frac, 8),
                    "upper_bound_mm3": _r(max(up95_frac * box_vol * mm3, floor), 4),
                    "upper_bound_kind": ("单侧 95%% 统计上界（frac=0：1-0.05^(1/n) ≈ 3/n × 盒体积；"
                                         "frac>0：frac+1.645·SE）+ 不低于可执行下限 %.3f mm³" % floor)})
        unk = st["unknown_a"] + st["unknown_b"]
        if unk > 0.05 * 2 * float(n_used):
            out.update({"verdict": "unresolved",
                        "why": "射线奇偶在 %d/%d 个点上迭代到上限 %d（>5%%）→ 内外判断不可靠，不给体积结论"
                               % (unk, 2 * n_used, INTERFERENCE_MAX_STEPS)})
            return out
        lower95 = max(0.0, (vol_u3 - ci95_frac * box_vol)) * mm3
        if vol_mm3 > 0 and lower95 > 0:
            if vol_mm3 >= floor:
                out.update({"verdict": "supported",
                            "why": "估计交集体积 %.1f mm³（95%% CI ±%.1f mm³，下界 %.1f mm³ > 0）→ 真干涉；"
                                   "面对数 %d" % (vol_mm3, out["volume_ci95_mm3"], lower95, pair_count)})
            else:
                out.update({"verdict": "refuted", "sub_material": True,
                            "why": "估计交集体积 %.4f mm³ < 可执行下限 %.3f mm³（网格弦差/噪声量级）→ "
                                   "不作为真干涉；上界 %.4f mm³" % (vol_mm3, floor, out["upper_bound_mm3"])})
        elif out["upper_bound_mm3"] <= floor:
            out.update({"verdict": "refuted",
                        "why": "采样 %d 点里没有同时在 A、B 内的点，且单侧 95%% 上界 %.4f mm³ ≤ 可执行下限 "
                               "%.3f mm³ → 无干涉（对着面-对重叠 %d 对读：AABB 相交但实体不相交）"
                               % (n_used, out["upper_bound_mm3"], floor, pair_count)})
        else:
            need = int(box_vol * mm3 * 3.0 / max(floor, 1e-12)) + 1
            out.update({"verdict": "unresolved",
                        "why": "拿不到显著大于 0 的体积：估计 %.4f mm³、上界 %.4f mm³ 高于可执行下限 %.3f mm³ "
                               "→ 判不了（不是「无干涉」也不是「有干涉」）"
                               % (vol_mm3, out["upper_bound_mm3"], floor),
                        "next_step": "samples ≥ %d 才能把上界压到下限（现 n=%d；误差 ∝1/√n）" % (need, n_used),
                        "samples_needed_for_floor": need})
        return out

    def _fill_bbox(out, cand, src, mm_per_unit, mm3):
        if cand is None:
            out["bbox"] = None
            out["bbox_mm"] = None
            out["bbox_source"] = "无候选盒（交集可证为空）"
            return
        b, bm = _box_pair(cand[0], cand[1], mm_per_unit)
        out["bbox"] = b
        out["bbox_mm"] = bm
        out["bbox_source"] = src
        out["bbox_volume_mm3"] = _r(float(_boxvol(cand)) * mm3, 4)

    try:
        sides, cleanups = _resolve_two_sides(a, b, file_a, file_b, objects_a, objects_b, include_hidden)
        res = _body()
    except Exception as e:
        res = _exc_body(e, "interference")
    finally:
        for fn in cleanups.values():
            fn()
    if cleanups:
        res["cleanup"] = {t: cleanups[t]() for t in sorted(cleanups)}
    res["ms"] = int((_t.perf_counter() - t0) * 1000)
    return _j(res)


def audit_gate_selftest():
    """装配级判据的合成自检：可见浮块 / 微隙容忍 / 归因 / 漂移 四项一起验。"""
    import numpy as np
    made = []
    try:
        def cube(name, loc, size):
            me = bpy.data.meshes.new(name)
            bm = bmesh.new()
            bmesh.ops.create_cube(bm, size=float(size))
            bm.to_mesh(me)
            bm.free()
            ob = bpy.data.objects.new(name, me)
            ob.location = tuple(loc)
            bpy.context.scene.collection.objects.link(ob)
            made.append(ob)
            return ob
        A = cube("DSH_GATE_A", (0, 0, 0), 1.0)
        B = cube("DSH_GATE_B", (3.0, 0, 0), 0.4)        # 远端浮块（span 远大于 1%）
        C = cube("DSH_GATE_C", (-1.0002, 0, 0), 1.0)    # A 的另一侧：与 A 的 x=-0.5 面相差 0.0002 m = 0.2 mm（真微隙）
        D = cube("DSH_GATE_D", (6.0, 0, 0), 1.0)        # 形状改动版：非均匀缩放（各自归一化消除不掉）
        D.scale = (1.0, 1.0, 1.45)
        import math as _math
        F = cube("DSH_GATE_F", (0.6, 0.6, 0.0), 0.3)    # 绕 Z 转 45°：bbox 与 A 重叠 → bbox 判 micro，
        F.rotation_euler = (0.0, 0.0, _math.radians(45.0))   # 但面到面约 150 mm —— "bbox 骗人"用例
        try:
            bpy.context.view_layer.update()
        except Exception:
            pass
        r0 = json.loads(audit_connectivity(objects=["DSH_GATE_A", "DSH_GATE_B", "DSH_GATE_C", "DSH_GATE_F"]))
        r1 = json.loads(audit_drift("DSH_GATE_A", "DSH_GATE_A"))
        r2 = json.loads(audit_drift("DSH_GATE_A", "DSH_GATE_C"))
        r3 = json.loads(audit_drift("DSH_GATE_A", "DSH_GATE_D"))
        r4f = json.loads(audit_drift("DSH_GATE_A", "DSH_GATE_D", samples=3000, fast=True))
        r4g = json.loads(audit_drift("DSH_GATE_A", "DSH_GATE_D", samples=3000, fast=False))
        agree = float(r4g.get("chamfer", 0)) >= float(r4f.get("chamfer", 0)) - 1e-9

        def floater_at(pt):
            for f in r0.get("floaters", []):
                if all(f["bbox_min"][k] - 1e-6 <= pt[k] <= f["bbox_max"][k] + 1e-6 for k in range(3)):
                    return f
            return {}
        f_b, f_f = floater_at((3.0, 0, 0)), floater_at((0.6, 0.6, 0.0))
        tol_names = {tuple(sorted(t.get("objects") or [])) for t in r0.get("tolerated", [])}
        clauses = {
            "r0_ok": r0.get("ok") is True,
            "r0_analyzed": r0.get("analyzed") is True,
            "floater_count_2": r0.get("floater_count") == 2,
            "visible_2": r0.get("visible_floater_count") == 2,
            "B_floating_far": bool(f_b) and f_b.get("gap_mm", 0) > 1000,
            "F_bbox_micro": bool(f_f) and f_f.get("bbox_micro") is True,
            "F_not_confirmed": bool(f_f) and f_f.get("mesh_confirmed") is False,
            "A_tolerated": ("DSH_GATE_A",) in tol_names,
            "C_tolerated": ("DSH_GATE_C",) in tol_names,
            "micro_tolerated_2": r0.get("micro_floater_count") == 2,
            "gate_fail": r0.get("gate", {}).get("ok") is False,
            "drift_self_small": r1.get("ok") is True and r1.get("chamfer", 1) <= 0.005,
            "movement_reported": r2.get("ok") is True and r2.get("bbox_delta", {}).get("moved_mm", 0) > 0.1,
            "drift_reshaped_big": r3.get("ok") is True and r3.get("chamfer", 0) > 0.03,
            "nn_grid_ge_bvh": bool(agree),
        }
        bad = [k for k, v in clauses.items() if not v]
        ok = not bad
        return _j({"ok": bool(ok), "failed_clauses": bad,
                   "floaters_slim": [{"rank": f["rank"], "bbox_min": f["bbox_min"], "bbox_max": f["bbox_max"],
                                      "bbox_micro": f["bbox_micro"], "mesh_confirmed": f["mesh_confirmed"],
                                      "gap_mm": f["gap_mm"], "true_gap_units": f["true_gap_units"],
                                      "attribution": (f.get("attribution") or {}).get("object")}
                                     for f in r0.get("floaters", [])],
                   "tolerated_slim": [{"rank": t["rank"], "objects": t["objects"], "gap_mm": t["gap_mm"],
                                       "mesh_confirmed": t["mesh_confirmed"],
                                       "true_gap_units": t.get("true_gap_units")}
                                      for t in r0.get("tolerated", [])],
                   "floater_count": r0.get("floater_count"),
                   "visible": r0.get("visible_floater_count"), "micro": r0.get("micro_floater_count"),
                   "real": r0.get("real_floater_count"), "gate": r0.get("gate", {}).get("state"),
                   "micro_confirm": r0.get("micro_confirm"),
                   "F_bbox_micro": f_f.get("bbox_micro"), "F_mesh_confirmed": f_f.get("mesh_confirmed"),
                   "F_confirm_note": f_f.get("confirm_note"),
                   "connectivity": r0 if not ok else "ok",
                   "drift_self": r1.get("chamfer"), "drift_shifted_chamfer": r2.get("chamfer"),
                   "drift_shifted_moved_mm": r2.get("bbox_delta", {}).get("moved_mm"),
                   "drift_reshaped": r3.get("chamfer"), "drift_reshaped_band": r3.get("band"),
                   "nn_bvh": r4f.get("chamfer"), "nn_grid": r4g.get("chamfer"), "nn_grid_ge_bvh": bool(agree),
                   "expect": "floater_count=2（B 远端 + F）；A/C 各与对方相接（0.2mm 缝 & 互穿）→ 进 tolerated 不算浮块；"
                             "F bbox_micro=true 但 mesh_confirmed=false → 判浮块（bbox 骗人必须被揭穿，实测最近 1.32mm）；"
                             "micro_floater_count=2、gate=fail；同网格 chamfer≈0（≤0.005）；平移不进 chamfer 但 "
                             "bbox_delta.moved_mm 报中心距；非均匀缩放 chamfer>0.03（band=strong）；grid 口径 ≥ BVH 口径"})
    except Exception as e:
        import traceback
        return _j({"ok": False, "error": "%s: %s" % (type(e).__name__, str(e)[:200]),
                   "traceback": traceback.format_exc()[-800:]})
    finally:
        for ob in made:
            try:
                bpy.data.objects.remove(ob, do_unlink=True)
            except Exception:
                pass


def audit_help():
    return _j({"version": AUDIT_VERSION,
               "ops": {"mesh": "audit_mesh(objects|scope, envelope, eps_area, self_intersect)",
                       "scene": "audit_scene(envelope, limit)",
                       "duplicates": "audit_duplicates()",
                       "purge_orphans": "purge_orphans()",
                       "connectivity": "audit_connectivity(objects|scope|file, visible_frac, micro_gap_mm, limit, max_tris)"
                                       " → 连通分量 / 浮块 / 微隙 / 归因",
                       "gate": "audit_gate(objects|scope|file, envelope, micro_gap_mm, max_floater_span_fraction)"
                               " → 出厂门（连通 + 包络 + 未分析三态）",
                       "drift": "audit_drift(a, b, samples, seed) → 对称 Chamfer 距离（两条网格改了多远）",
                       "measure": "audit_measure(objects|scope|file, neighbors, k) → 世界 bbox + 逐轴间隙/重叠 mm/%",
                       "snap_floaters": "audit_snap_floaters(objects|scope, dry_run=true) → 把可见浮块贴到最近邻",
                       "overlap": "audit_overlap(a|objects_a, b|objects_b, file_a, file_b, limit, max_tris)"
                                  " → 面-对重叠（BVH 三角面精确）：pair_count / pairs / intersection_bbox / per_object",
                       "interference": "audit_interference(a|objects_a, b|objects_b, file_a, file_b, samples, seed, "
                                       "material_mm3, max_tris, rays) → A∩B 体积估计 mm³（Monte-Carlo 射线奇偶）"
                                       " + 三态 verdict",
                       "selftest": "audit_selftest()",
                       "gate_selftest": "audit_gate_selftest() → 装配级判据合成自检"},
               "file_mode": "v0.9.1（C2）：connectivity / gate / measure 都接受 file=<.blend 路径> —— "
                            "把文件里的对象**临时**追加进会话跑同一条链路，finally 删净（对象/临时集合/"
                            "新造孤儿数据/库条目），返回体给 source / file_load / cleanup；此时 objects/scope "
                            "当**文件内的对象名**筛选（append 不带集合成员关系）。"
                            "overlap / interference 两端都可以走 file_a/file_b（a/b 直接给 .blend 路径也认）。",
               "fields": "boundary_edges / nonmanifold_edges / degenerate_faces / loose_verts / loose_edges / "
                         "self_intersections / normals_outward / closed / tris / aabb / out_of_bounds；"
                         "mesh 侧三态：state(=verdict) / reason / analysis{self_intersections,normals,incomplete_objects} / "
                         "normals_state{pass|fail|unknown|n/a} / normals_shells[{shell,signed_volume,closed,"
                         "winding_consistent,nested,relation,state}] / self_intersections_analyzed / "
                         "self_intersections_state{pass|fail|skipped|error}；"
                         "装配侧：components / floaters[{span_fraction, gap_mm, bbox_micro, mesh_confirmed, true_gap_units, attribution}] / "
                         "tolerated[]（确认相接）/ unconfirmed[]（未确认→降级）/ gap_histogram / gate{state,offenders}；"
                         "跨件侧：pair_count / pairs[{tri_a,tri_b,point,segment_len_mm}] / intersection_bbox / "
                         "overlap_faces_bbox / per_object / volume_mm3 / volume_ci95_mm3 / verdict{supported|refuted|unresolved} / why",
               "units": "mm 口径阈值一律经场景 scale_length 换算（返回 units 块）；体积另给 volume_units3 与 "
                        "volume_mm3（units.volume_units3_to_mm3）",
               "honest_limits": "overlap：overlap() 是**真三角-三角相交**，但**共面且互相覆盖的面不报**（实测）→ "
                                "共面贴合看 contact_probe；interference：Monte-Carlo **不是精确布尔**，"
                                "非流形/开放网格一律 unresolved（不给数）；误差 ∝1/√samples（口径见 error_caliber）",
               "hard_rule": "0 个 mesh ⇒ ok=false（静默空产出必须失败）；大网格 ⇒ analyzed=false，"
                            "未分析不得读作已连通/无重叠；file= 打不开/没 mesh/全隐藏 ⇒ ok=false"})


def audit_dispatch(op, args=None):
    if isinstance(args, str):
        try:
            args = json.loads(args) if args.strip() else {}
        except Exception:
            args = {}
    kw = {}
    for k, v in (args or {}).items():
        if k == "args" and isinstance(v, dict):
            kw.update(v)
        else:
            kw[k] = v
    ops = {"mesh": audit_mesh, "scene": audit_scene, "duplicates": audit_duplicates,
           "purge_orphans": purge_orphans, "selftest": audit_selftest, "help": audit_help,
           "connectivity": audit_connectivity, "gate": audit_gate, "drift": audit_drift,
           "measure": audit_measure, "snap_floaters": audit_snap_floaters,
           "overlap": audit_overlap, "interference": audit_interference,
           "gate_selftest": audit_gate_selftest}
    fn = ops.get(str(op))
    if fn is None:
        return _j({"ok": False, "error": "unknown audit op", "op": op, "ops": sorted(ops)})
    try:
        return fn(**kw)
    except TypeError as e:
        return _j({"ok": False, "error": "参数不匹配: %s" % str(e)[:200], "op": op, "help": audit_help()})


_DshApi = _KIT.Api  # 共享内核（尾部注册行无需改）
import sys as _sys
_K = _sys.modules.get("dsh_rt_kernel")
if _K is not None:
    _K.dsh_audit_api = _DshApi({"version": AUDIT_VERSION, "dispatch": audit_dispatch, "mesh": audit_mesh,
                                "scene": audit_scene, "duplicates": audit_duplicates,
                                "purge_orphans": purge_orphans,
                                "connectivity": audit_connectivity, "gate": audit_gate, "drift": audit_drift,
                                "measure": audit_measure, "snap_floaters": audit_snap_floaters,
                                "overlap": audit_overlap, "interference": audit_interference,
                                "selftest": audit_selftest, "gate_selftest": audit_gate_selftest,
                                "help": audit_help})
