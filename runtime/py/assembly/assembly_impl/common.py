# -*- coding: utf-8 -*-
"""DSH 装配层 v1 —— 全对全穿模扫描 / 零件清单 / N 路合并 / 装配门。

为什么有它（多会话并行建模的合并事故）：
  分件方各建各的 .blend，合并者用自写 append/join（无清单、无接口帧、无 hash），
  合并后没有"一次调用、结论先行"的门；已有 audit_interference / audit_overlap 是**两两**算子，
  必须人工指出哪两件比，而 contract.interference_report 虽是 scope 全对全，却
  (a) 默认 limit=60 静默截断、(b) 完全包含件因"三角面不相交"被提前 continue、(c) 没有逐对 verdict/分类。
  本模块把"合并 + 体检 + 出厂门"收敛成四件事，数字全部由插件算，模型只读 verdict 并按 repairs 执行：

  1) scan（别名 audit_clash_all）—— 全对全分类扫描（只读）
     便宜→贵的分层：求值网格预检 → AABB 全对全粗筛 → 精确 tri-tri（BVH.overlap）
     → 深度/体积（优先复用 K.dsh_contract_api["interference_report"]，拿不到时自己用
       BVH 最近点 + 射线奇偶内外 + 低差异采样体积）→ **补完全包含分支**（tri 不相交但 AABB 包含
       → 用小件表面点射线奇偶判 inside，必须报 contained/clash）。
     分类：clash / hidden_ok / contact / gap / float / contained / unresolved。
     spec.allow 表达"本 spec 允许的隐藏交叠"（max_depth_mm / max_volume_mm3），允许对不误报。
     覆盖：coverage{complete,truncated,budget_hit}；任何未测/未判 ⇒ degraded（绝不静默当通过）。
     浮空：有 manifest 用"连接图可达 + 逐接口接触验证"；没有 manifest 退 audit_connectivity。

  2) manifest —— 零件清单（init|add_part|add_interface|add_allow|validate|seal|show）
     JSON 读写 + 校验：id 唯一 / file 存在 / file_md5 / 接口两端存在 / basis 正交 / 单位一致。
     它是 allow 唯一白名单来源（跨进程可复现，不依赖 K.dsh_contract 的会话内状态）。

  3) merge —— N 路合并（默认 dry_run=true）
     按 part id 字典序 libraries.load(link=False) append → 对象名 <partid>__<原名>
     → 写 custom props → manifest 变换 → recalc normals → 可选 txn snapshot → 自动 gate。
     dry_run 只回计划不写盘；gate 不过不保存（失败不写盘）；成功写 merge_receipt.json。

  4) gate —— 装配出厂门（verdict-first）
     跑 mesh(audit_mesh) + normals + scan + 浮空(manifest 连接图/connectivity) + interfaces + measure，
     聚合三态；**export_allowed 仅在 verdict=pass 时为 true**；任何 skipped/degraded 都不算通过。

  5) repair（别名 fix_clash；纯规划器 fix_interference）—— 装配修复
     scan → 规划（clash/contained 平移；float 用 audit_snap_floaters；布尔仅在显式同意时）→ snapshot →
     apply → 复扫，最多 max_rounds 轮；policy=safe|manual|aggressive 控制低风险自动修复边界。

  6) export（别名 asm_export）—— 交付闭环
     可选 asm_merge(dry_run=false) → 导出 GLB/FBX/OBJ → <out_dir>/<name>_manifest.json +
     append-only merge_ledger.jsonl（ts/action/manifest_md5/files/md5/gate_verdict）。

硬规则（沿用 audit / gate 的 v3 口径）：
  * 空产出 / 坏参数 / 分析炸了 ⇒ ok:false（不静默成功）；
  * unresolved（证据不足）绝不当 pass；truncated / budget_hit ⇒ degraded；
  * 只读 op（scan / manifest show|validate / gate）不改场景；merge 是唯一的写 op（默认 dry_run）。
  * 诚实边界写在各回执的 honest_limits 里（采样式深度/体积是估计值；开网格/朝内法线的内外结论不作数）。

入口（Python / preload 后）：
    K.dsh_assembly_api("scan", {"objects": ["GEO-a","GEO-b"], "spec": {"allow": [...]}})
    K.dsh_assembly_api("manifest", {"action": "init", "path": "...assembly.json", "assembly": "Aurora"})
    K.dsh_assembly_api("merge", {"manifest": "...assembly.json", "dry_run": true})
    K.dsh_assembly_api("gate", {"scope": ["COL_Geo"], "manifest": "...assembly.json"})
    K.dsh_assembly_api("repair", {"objects": ["GEO-a", "GEO-b"], "dry_run": true, "policy": "safe"})
    K.dsh_assembly_api("export", {"manifest": "...assembly.json", "out_dir": "D:/Blender/out",
                                  "name": "assembly", "merge": true, "formats": ["glb", "fbx", "obj"]})
"""
import hashlib
import json
import math
import os
import sys
import time

import bpy
import numpy as np
from mathutils import Euler as Euler, Matrix as Matrix, Vector
from mathutils.bvhtree import BVHTree

ASSEMBLY_VERSION = 1
MANIFEST_SCHEMA = "dsh-assembly-manifest/1"
CLASSES = ("clash", "hidden_ok", "contact", "gap", "float", "contained", "unresolved")
FIT_CLASSES = ("clearance", "location", "press", "snap")
UNIT_MM_PER_UNIT = {"mm": 1000.0, "cm": 10.0, "m": 1.0}

_K = sys.modules.get("dsh_rt_kernel")
_KIT = getattr(_K, "dsh_kit", None)
if _KIT is None:
    raise RuntimeError("assembly.py 需要共享内核 K.dsh_kit（由 KERNEL_BOOTSTRAP 注入）")

_j = _KIT.j


# ────────────────────────────── 基础 helper ──────────────────────────────
def _kernel():
    return sys.modules.get("dsh_rt_kernel")


def _api(name):
    K = _kernel()
    return getattr(K, "dsh_%s_api" % str(name), None) if K is not None else None


def _call_api(name, op, args=None):
    """调另一个模块的 API（Api 可调用 → 返回已解析 dict）；失败回 (None, 原因)，不抛。"""
    api = _api(name)
    if api is None:
        return None, ("K.dsh_%s_api 不存在 —— 在线路径把 %s 加进 preload（例如 preload=\"assembly,"
                      "audit,contract,clearance,gate\"）；离线见 runtime/py/core/offline_bootstrap.py" % (name, name))
    try:
        out = api(str(op), dict(args or {}))
    except Exception as e:
        return None, "K.dsh_%s_api.%s 调用失败: %s: %s" % (name, op, type(e).__name__, str(e)[:180])
    if isinstance(out, str):
        try:
            out = json.loads(out)
        except Exception:
            return None, "K.dsh_%s_api.%s 返回不是 JSON" % (name, op)
    if not isinstance(out, dict):
        return None, "K.dsh_%s_api.%s 返回类型异常: %s" % (name, op, type(out).__name__)
    return out, None


def _path(p):
    """WSL 路径 → Blender（Windows 进程）可用形态；K.win_path 可用时用它。"""
    s = str(p or "")
    if not s or not s.startswith("/"):
        return s
    K = _kernel()
    if K is not None and hasattr(K, "win_path"):
        try:
            return str(K.win_path(s))
        except Exception:
            pass
    return s


def _r(x, n=4):
    try:
        if x is None:
            return None
        return round(float(x), int(n))
    except Exception:
        return None


def _num(v):
    if v is None or isinstance(v, bool):
        return None
    try:
        f = float(v)
    except Exception:
        return None
    if not math.isfinite(f):
        return None
    return f


def _as_list(v):
    if v is None:
        return []
    if isinstance(v, str):
        return [v]
    if isinstance(v, (list, tuple, set)):
        return [str(x) for x in v]
    return [str(v)]


def _sha256_bytes(b):
    return hashlib.sha256(b).hexdigest()


def _sha256_file(path):
    p = _path(path)
    try:
        h = hashlib.sha256()
        with open(p, "rb") as fh:
            while True:
                chunk = fh.read(1024 * 1024)
                if not chunk:
                    break
                h.update(chunk)
        return h.hexdigest()
    except Exception:
        return None


def _canon(obj):
    return json.dumps(obj, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)


def _md5_text(t):
    return _sha256_bytes(str(t).encode("utf-8"))


def _receipt(body, t0=None):
    """统一回执骨架：ok/verdict + units + 耗时。"""
    d = dict(body or {})
    d.setdefault("units", {"mm_per_unit": _r(_KIT.units(), 6)})
    if t0 is not None:
        d["ms"] = int((time.perf_counter() - t0) * 1000)
    return d


def _err(msg, **kw):
    d = {"ok": False, "error": str(msg)[:400], "hint": kw.pop("hint", None)}
    d.update(kw)
    return _j(_receipt(d))


# ────────────────────────────── 对象解析 / 求值网格 ──────────────────────────────
def _pick_objects(scope=None, objects=None, include_hidden=False):
    """scope|objects → 当前会话里的 mesh 对象。空/坏参数走 info.error（调用方 ok:false）。

    兼容 {"objects": [...], "scope": ...} 信封；scope 可以是对象名/集合名/名字前缀（与 audit/contract 同口径）。
    """
    info = {"objects": [], "names": [], "missing": [], "non_mesh": [], "hidden_skipped": 0,
            "scopes_missing": [], "error": None, "source": "session"}
    if isinstance(objects, dict):
        w = objects
        objects = w.get("objects") or w.get("names")
        if scope is None:
            scope = w.get("scope")
    names = _as_list(objects)
    if objects is not None and not names:
        info["error"] = "objects 是空列表（空产出必须失败，不能静默扫全场景）"
        return info
    picked = []
    if names:
        for n in names:
            ob = bpy.data.objects.get(str(n))
            if ob is None:
                info["missing"].append(str(n))
            elif ob.type != "MESH":
                info["non_mesh"].append(str(n))
            else:
                picked.append(ob)
        if info["missing"] or info["non_mesh"]:
            info["error"] = ("objects 里有不存在/非 mesh 的对象：missing=%s non_mesh=%s"
                             % (info["missing"][:6], info["non_mesh"][:6]))
            return info
    elif scope is not None:
        scopes = _as_list(scope)
        seen = set()
        for s in scopes:
            hit = []
            ob = bpy.data.objects.get(str(s))
            if ob is not None and ob.type == "MESH":
                hit.append(ob)
            coll = bpy.data.collections.get(str(s))
            if coll is not None:
                for o in coll.all_objects:
                    if o.type == "MESH":
                        hit.append(o)
            if not hit:
                hit = [o for o in bpy.data.objects if o.type == "MESH" and str(o.name).startswith(str(s))]
            if not hit:
                info["scopes_missing"].append(str(s))
            for o in hit:
                if str(o.name) not in seen:
                    seen.add(str(o.name))
                    picked.append(o)
        if info["scopes_missing"]:
            info["error"] = "scope 里没有任何对象/集合/名字前缀命中：%s" % info["scopes_missing"][:6]
            return info
    else:
        picked = [o for o in bpy.context.scene.objects if o.type == "MESH"]
    out = []
    for ob in picked:
        if not include_hidden:
            try:
                if ob.hide_render or ob.hide_viewport:
                    info["hidden_skipped"] += 1
                    continue
            except Exception:
                pass
        out.append(ob)
    info["objects"] = out
    info["names"] = [str(o.name) for o in out]
    if not out:
        info["error"] = ("范围内 0 个可见 mesh（hidden_skipped=%d）—— 空产出必须失败，不能读成'没问题'"
                         % info["hidden_skipped"])
    return info


def _load_file_objects(path, include_hidden=False):
    """把另一个 .blend 的对象**临时**追加进当前会话 → (objs, info, cleanup)。

    只取对象（不取场景/集合/世界）；cleanup() 幂等，把本次加载进来的对象/临时集合/孤儿数据/库条目删干净。
    """
    src = _path(path)
    info = {"path": src, "source": "file:%s" % src, "loaded": 0, "mesh": 0, "non_mesh": 0,
            "missing": [], "hidden_skipped": 0, "error": None, "name_of": {}}
    before = set(bpy.data.objects)
    before_libs = set(bpy.data.libraries)
    state = {"done": False, "obj": [], "coll": None}
    requested = []

    if not src or not os.path.isfile(src):
        info["error"] = "文件不存在或不是文件：%s" % src
        return [], info, (lambda: {"ok": True, "removed": {}, "note": "没有临时加载任何东西"})

    try:
        with bpy.data.libraries.load(src, link=False) as (data_from, data_to):
            requested = [str(n) for n in data_from.objects]
            data_to.objects = list(requested)
    except Exception as e:
        got = [o for o in bpy.data.objects if o not in before]
        for o in got:
            try:
                bpy.data.objects.remove(o, do_unlink=True)
            except Exception:
                pass
        info["error"] = "打不开/加载失败：%s: %s" % (type(e).__name__, str(e)[:200])
        return [], info, (lambda: {"ok": True, "removed": {"objects": [str(o.name) for o in got]},
                                  "note": "半途失败：已尽力删掉本次加载的对象"})

    got = [o for o in getattr(data_to, "objects", []) or [] if o is not None]
    if len(got) == len(requested):
        info["name_of"] = {str(o.name): str(requested[i]) for i, o in enumerate(got) if o is not None}
    state["obj"] = list(got)
    info["loaded"] = len(got)
    meshes = [o for o in got if o.type == "MESH"]
    info["mesh"] = len(meshes)
    info["non_mesh"] = len(got) - len(meshes)
    objs = [o for o in meshes if include_hidden or not (o.hide_render or o.hide_viewport)]
    info["hidden_skipped"] = len(meshes) - len(objs)
    if not got:
        info["error"] = "文件里没有对象：%s" % src
    elif not meshes:
        info["error"] = "文件里没有 mesh 对象：%d 个对象全是 %s" % (
            len(got), "/".join(sorted(set(str(o.type) for o in got))))
    elif not objs:
        info["error"] = "文件里 %d 个 mesh 全被隐藏（include_hidden=false）" % len(meshes)
    else:
        try:
            coll = bpy.data.collections.new("DSH_ASM_FILE_TMP")
            bpy.context.scene.collection.children.link(coll)
            for o in objs:
                coll.objects.link(o)
            state["coll"] = coll
            bpy.context.view_layer.update()
            info["tmp_collection"] = str(coll.name)
        except Exception as e:
            info["error"] = "临时集合/链接失败：%s: %s" % (type(e).__name__, str(e)[:200])

    def cleanup():
        if state["done"]:
            return {"ok": True, "removed": {}, "note": "already cleaned"}
        state["done"] = True
        removed = {"objects": [], "libraries": [], "data": []}
        for o in state["obj"]:
            try:
                nm = str(o.name)
                me = o.data if o.type == "MESH" else None
                bpy.data.objects.remove(o, do_unlink=True)
                removed["objects"].append(nm)
                if me is not None and me.users == 0:
                    try:
                        bpy.data.meshes.remove(me, do_unlink=True)
                    except Exception:
                        pass
            except Exception:
                pass
        if state["coll"] is not None:
            try:
                bpy.data.collections.remove(state["coll"], do_unlink=True)
            except Exception:
                pass
        for lib in list(bpy.data.libraries):
            if lib not in before_libs:
                try:
                    nm = str(lib.name)
                    bpy.data.libraries.remove(lib, do_unlink=True)
                    removed["libraries"].append(nm)
                except Exception:
                    pass
        return {"ok": True, "removed": removed,
                "note": "临时加载的收尾（无论成败都跑）"}

    if info["error"]:
        return [], info, cleanup
    return objs, info, cleanup


def _mesh_map(objs):
    """一组对象的**求值后**世界网格 → {name: md}；md 含 tris/bvh/aabb/closed/normals_ok。

    求值网格（evaluated_get + to_mesh）：Boolean 未 Apply 的母件也按最终形状算。
    闭合性用三角边计数（边界边=1 / 非流形边>2）；法线是否朝外看 signed volume（闭合才有意义）。
    诚实边界：多壳且各壳朝向不一致时，按总体有符号体积会漏判（gate 另跑 audit_mesh 逐壳判）。
    """
    dg = bpy.context.evaluated_depsgraph_get()
    out, errors = {}, {}
    for ob in objs:
        nm = str(ob.name)
        obe = None
        try:
            obe = ob.evaluated_get(dg)
            me = obe.to_mesh()
            if me is None:
                errors[nm] = "无求值网格"
                continue
            me.calc_loop_triangles()
            n = len(me.loop_triangles)
            if n == 0:
                errors[nm] = "0 三角面（求值后为空）"
                continue
            v = np.empty(len(me.vertices) * 3, dtype=np.float64)
            me.vertices.foreach_get("co", v)
            v = v.reshape(-1, 3)
            idx = np.empty(n * 3, dtype=np.int64)
            me.loop_triangles.foreach_get("vertices", idx)
            idx = idx.reshape(-1, 3)
            mw = np.array(obe.matrix_world, dtype=np.float64)
            tris = (v[idx.reshape(-1)] @ mw[:3, :3].T + mw[:3, 3]).reshape(-1, 3, 3)
            edges = np.concatenate([idx[:, [0, 1]], idx[:, [1, 2]], idx[:, [2, 0]]], axis=0)
            _u, cnt = np.unique(np.sort(edges, axis=1), axis=0, return_counts=True)
            boundary = int((cnt == 1).sum())
            nonmanifold = int((cnt > 2).sum())
            a, b, c = tris[:, 0], tris[:, 1], tris[:, 2]
            vol_signed = float(np.einsum("ij,ij->i", a, np.cross(b, c)).sum() / 6.0)
            areas = 0.5 * np.linalg.norm(np.cross(b - a, c - a), axis=1)
            closed = bool(boundary == 0 and nonmanifold == 0)
            normals_ok = bool(closed and vol_signed > 0.0)
            verts = tris.reshape(-1, 3)
            polys = [(3 * i, 3 * i + 1, 3 * i + 2) for i in range(n)]
            bvh = BVHTree.FromPolygons([tuple(p) for p in verts.tolist()], polys, all_triangles=True)
            out[nm] = {
                "name": nm, "tris": tris, "tris_n": int(n), "bvh": bvh,
                "aabb": (tris.min(axis=(0, 1)).tolist(), tris.max(axis=(0, 1)).tolist()),
                "boundary_edges": boundary, "nonmanifold_edges": nonmanifold,
                "degenerate_faces": int((areas < 1e-12).sum()),
                "closed": closed, "volume_signed_m3": vol_signed, "normals_ok": normals_ok,
                "matrix_world": mw.tolist(), "skipped": None,
            }
        except Exception as e:
            errors[nm] = "%s: %s" % (type(e).__name__, str(e)[:160])
        finally:
            try:
                if obe is not None:
                    obe.to_mesh_clear()
            except Exception:
                pass
    return out, errors


# ────────────────────────────── 几何量（纯 numpy + BVH） ──────────────────────────────
def _sample_surface_n(tris, n):
    """按面积确定性采样三角面表面点 + 源三角面朝外法线（同输入同输出）。

    法线用来把测试点沿外法线推离表面 —— 仅靠"点是否在内部"会因共面边界/浮点误差误判。
    """
    tris = np.asarray(tris, dtype=np.float64)
    n = int(max(1, n))
    if len(tris) == 0:
        return np.zeros((0, 3)), np.zeros((0, 3))
    ab = tris[:, 1] - tris[:, 0]
    ac = tris[:, 2] - tris[:, 0]
    cross = np.cross(ab, ac)
    areas = 0.5 * np.linalg.norm(cross, axis=1)
    total = float(areas.sum())
    if total <= 0.0:
        return np.zeros((0, 3)), np.zeros((0, 3))
    cum = np.cumsum(areas) / total
    k = np.arange(n, dtype=np.float64)
    f = (k + 0.5) / float(n)
    ti = np.clip(np.searchsorted(cum, f, side="right"), 0, len(tris) - 1)
    phi, psi = 0.6180339887498949, 0.7548776662466927
    u = np.mod((k + 1.0) * phi, 1.0)
    v = np.mod((k + 1.0) * psi, 1.0)
    flip = (u + v) > 1.0
    u[flip] = 1.0 - u[flip]
    v[flip] = 1.0 - v[flip]
    pts = tris[ti, 0] + u[:, None] * ab[ti] + v[:, None] * ac[ti]
    normals = cross[ti]
    ln = np.linalg.norm(normals, axis=1)
    normals = normals / np.maximum(ln, 1e-30)[:, None]
    return pts, normals


def _sample_surface(tris, n):
    """按面积确定性采样三角面表面点（低差异序列，不用随机数 → 同输入同输出）。"""
    return _sample_surface_n(tris, n)[0]


_RAY_DIR = Vector((0.5773502691896258, 0.5773502691896258, 0.5773502691896258))


def _is_inside(p, bvh, direction=_RAY_DIR, eps=None, max_hits=4096):
    """射线奇偶判内外（对闭合网格全局精确；方向固定使结果可复现）。

    关键：BVH 的数学是浮点，从表面点出发必须按**坐标量级**给推进步长 —— 固定 1e-6 在
    100+ 单位的坐标上会被浮点精度吃掉（实测大件完全包含被判成 gap、深度误报 17mm）。
    """
    if bvh is None:
        return None, 0
    pp = Vector(p)
    step = float(eps) if eps is not None else max(1e-6, 1e-5 * max(1.0, abs(pp.x), abs(pp.y), abs(pp.z)))
    pp = pp + direction * step
    hits = 0
    while hits < int(max_hits):
        loc, _nor, _idx, _dist = bvh.ray_cast(pp, direction)
        if loc is None:
            break
        hits += 1
        sc = max(1.0, abs(loc.x), abs(loc.y), abs(loc.z))
        pp = loc + direction * max(1e-6, 1e-5 * sc)
    return (hits % 2) == 1, hits


_RAY_DIRS = (_RAY_DIR,
             Vector((-0.4741, 0.6342, -0.6107)).normalized(),
             Vector((0.8123, -0.3999, 0.4252)).normalized())


def _inside_vote(p, bvh):
    """三条不同方向的射线奇偶投票（共面/擦边时单方向会翻车；多数票更稳）。"""
    votes = []
    for d in _RAY_DIRS:
        inside, _h = _is_inside(p, bvh, d)
        if inside is None:
            return None, votes
        votes.append(bool(inside))
    return (sum(1 for v in votes if v) >= 2), votes


def _count_inside(points, bvh):
    n_in = 0
    tested = 0
    for p in points:
        inside, _votes = _inside_vote(p, bvh)
        if inside is None:
            continue
        tested += 1
        if inside:
            n_in += 1
    return n_in, tested


def _surface_gap(md_a, md_b, samples=600):
    """两组求值网格的表面最近距离（米）；采样点 = 表面积加权点 + 顶点。"""
    best = None
    for md, other in ((md_a, md_b), (md_b, md_a)):
        pts = _sample_surface(md["tris"], samples)
        if len(pts) == 0:
            continue
        for p in pts:
            r = other["bvh"].find_nearest(Vector(p))
            if r is None or r[0] is None:
                continue
            d = float(r[3])
            if best is None or d < best:
                best = d
        for t in md["tris"]:
            for p in t:
                r = other["bvh"].find_nearest(Vector(p))
                if r is None or r[0] is None:
                    continue
                d = float(r[3])
                if best is None or d < best:
                    best = d
    return best


def _halton(k, base):
    f, r = 1.0, 0.0
    while k > 0:
        f /= float(base)
        r += f * float(k % base)
        k //= base
    return r


def _overlap_volume(md_a, md_b, n=600):
    """交集体积估计：AABB 交集盒内低差异采样 + 两侧射线奇偶 → p × 盒体积（报二项标准误）。"""
    lo_a, hi_a = md_a["aabb"]
    lo_b, hi_b = md_b["aabb"]
    lo = [max(lo_a[i], lo_b[i]) for i in range(3)]
    hi = [min(hi_a[i], hi_b[i]) for i in range(3)]
    if any(hi[i] <= lo[i] for i in range(3)):
        return {"volume_m3": 0.0, "stderr_m3": 0.0, "p": 0.0, "box_volume_m3": 0.0, "n": 0}
    box = 1.0
    for i in range(3):
        box *= (hi[i] - lo[i])
    n = int(max(32, min(int(n), 4000)))
    hit = 0
    for i in range(n):
        p = [lo[0] + _halton(i + 1, 2) * (hi[0] - lo[0]),
             lo[1] + _halton(i + 1, 3) * (hi[1] - lo[1]),
             lo[2] + _halton(i + 1, 5) * (hi[2] - lo[2])]
        ia, _ = _is_inside(p, md_a["bvh"])
        if not ia:
            continue
        ib, _ = _is_inside(p, md_b["bvh"])
        if ib:
            hit += 1
    p = hit / float(n) if n else 0.0
    stderr = math.sqrt(max(0.0, p * (1.0 - p) / float(n))) if n else 0.0
    return {"volume_m3": p * box, "stderr_m3": stderr * box, "p": p, "box_volume_m3": box, "n": n,
            "hit": hit}


def _depth_measure(md_a, md_b, samples=1200, vol_n=600):
    """穿透深度 + 相交体积（自己的兜底实现，contract 拿不到时用）。

    深度 = 埋在对方内部的表面点到对方表面的最近距离（SDF 近似）。做法有三条关键修正：
      ① 测试点沿**源三角面外法线**推离表面再判内外 —— 直接拿表面点判会因共面边界/浮点噪声误判；
      ② 三条不同方向射线奇偶**多数票**；
      ③ 到对方表面距离 < 2δ 的点（贴边，距离不可信）直接丢弃，不用它凑深度。
    体积 = AABB 交集盒低差异采样估计（确定性）。
    """
    pa, na = _sample_surface_n(md_a["tris"], samples)
    pb, nb = _sample_surface_n(md_b["tris"], samples)
    if len(pa) == 0 or len(pb) == 0:
        return {"depth_mm": None, "depth_mean_mm": None, "volume_mm3": None, "method": "采样失败"}

    def _diag(md):
        lo, hi = md["aabb"]
        return float(np.linalg.norm(np.array(hi, dtype=np.float64) - np.array(lo, dtype=np.float64)))

    delta = max(1e-6, 1e-5 * max(_diag(md_a), _diag(md_b), 1.0))

    def _collect(points, normals, other):
        out = []
        for p, n in zip(points, normals):
            q = np.asarray(p, dtype=np.float64) + np.asarray(n, dtype=np.float64) * delta
            inside, _votes = _inside_vote(q, other["bvh"])
            if not inside:
                continue
            r = other["bvh"].find_nearest(Vector(p))
            if r is None or r[0] is None:
                continue
            d = float(r[3])
            if d < 2.0 * delta:            # 贴边：内外/距离都不可信
                continue
            out.append(d)
        return out

    d_ab = _collect(pa, na, md_b)
    d_ba = _collect(pb, nb, md_a)
    dep = d_ab + d_ba
    vol = _overlap_volume(md_a, md_b, n=vol_n)
    mmu = _KIT.units()
    depth_max_m = max(dep) if dep else 0.0
    return {
        "depth_mm": depth_max_m * mmu,
        "depth_mean_mm": (sum(dep) / len(dep) * mmu) if dep else 0.0,
        "volume_mm3": vol["volume_m3"] * (mmu ** 3),
        "volume_stderr_mm3": vol["stderr_m3"] * (mmu ** 3),
        "volume_p": vol["p"],
        "surface_inside_frac": (len(dep) / float(len(pa) + len(pb))) if (len(pa) + len(pb)) else 0.0,
        "direction": "A→B" if d_ab else ("B→A" if d_ba else "无埋入点"),
        "method": "BVH 最近点距离 + 外法线偏移射线奇偶（三向投票）+ 低差异体积采样（assembly.py 自带兜底）",
    }


__all__ = [name for name in list(globals()) if not name.startswith("__")]
