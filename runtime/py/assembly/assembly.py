# -*- coding: utf-8 -*-
"""DSH 装配层 v1 —— 全对全穿模扫描 / 零件清单 / N 路合并 / 装配门（P0-B）。

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
from mathutils import Euler, Matrix, Vector
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
# ────────────────────────────── manifest（零件清单） ──────────────────────────────
def _manifest_init(assembly=None, units="mm", coord=None):
    return {"schema": MANIFEST_SCHEMA, "units": str(units or "mm"),
            "coord": dict(coord or {"up": "Z", "forward": "-Y"}),
            "assembly": str(assembly or "unnamed-assembly"),
            "version": 1, "sealed": False, "sealed_md5": None,
            "parts": [], "interfaces": [], "allow_pairs": []}


def _load_manifest(src):
    """manifest = dict | JSON 字符串 | 文件路径 → (dict, 错误)。"""
    if src is None or src == "":
        return None, "manifest 没给（需要 dict / JSON 字符串 / .json 路径）"
    if isinstance(src, dict):
        try:
            return json.loads(json.dumps(src, default=str)), None
        except Exception as e:
            return None, "manifest dict 无法深拷贝: %s: %s" % (type(e).__name__, str(e)[:160])
    s = str(src)
    if s.strip().startswith("{"):
        try:
            m = json.loads(s)
        except Exception as e:
            return None, "manifest JSON 解析失败: %s: %s" % (type(e).__name__, str(e)[:160])
        return (m, None) if isinstance(m, dict) else (None, "manifest 必须是 JSON 对象")
    p = _path(s)
    if not os.path.isfile(p):
        return None, "manifest 文件不存在: %s" % p
    try:
        with open(p, "r", encoding="utf-8") as fh:
            m = json.load(fh)
    except Exception as e:
        return None, "manifest 读取失败: %s: %s" % (type(e).__name__, str(e)[:160])
    return (m, None) if isinstance(m, dict) else (None, "manifest 文件内容必须是 JSON 对象")


def _manifest_md5(m):
    """清单内容指纹（canonical JSON，排除 manifest_md5 / sealed_md5 两个自指字段）。"""
    if not isinstance(m, dict):
        return None
    d = {k: v for k, v in m.items() if k not in ("manifest_md5", "sealed_md5")}
    return _md5_text(_canon(d))


def _write_json(path, obj):
    p = _path(path)
    d = os.path.dirname(p)
    if d and not os.path.isdir(d):
        os.makedirs(d, exist_ok=True)
    tmp = p + ".tmp%d" % os.getpid()
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(obj, fh, ensure_ascii=False, indent=2)
        fh.write("\n")
    os.replace(tmp, p)
    return p


def _manifest_counts(m):
    return {"parts": len(m.get("parts") or []), "interfaces": len(m.get("interfaces") or []),
            "allow_pairs": len(m.get("allow_pairs") or [])}


def _validate_frame(fr, pid, fid, E, W):
    origin = fr.get("origin_mm")
    if origin is not None:
        if not isinstance(origin, (list, tuple)) or len(origin) != 3 or any(_num(x) is None for x in origin):
            E("FRAME_BAD_ORIGIN", "part %s frame %s: origin_mm 必须是 3 个数字" % (pid, fid), pid)
    normal = fr.get("normal")
    if normal is None:
        W("FRAME_NO_NORMAL", "part %s frame %s: 没有 normal，接口朝向未定义" % (pid, fid), pid)
    else:
        if not isinstance(normal, (list, tuple)) or len(normal) != 3 or any(_num(x) is None for x in normal):
            E("FRAME_BAD_NORMAL", "part %s frame %s: normal 必须是 3 个数字" % (pid, fid), pid)
        elif float(np.linalg.norm(np.array([float(x) for x in normal]))) < 1e-9:
            E("FRAME_BAD_NORMAL", "part %s frame %s: normal 长度 0" % (pid, fid), pid)
    basis = fr.get("basis")
    if basis is None:
        W("FRAME_NO_BASIS", "part %s frame %s: 没有 basis（x/y），绕法线的旋转未定" % (pid, fid), pid)
        return
    try:
        if isinstance(basis, dict):
            bx, by = basis.get("x"), basis.get("y")
        elif isinstance(basis, (list, tuple)) and len(basis) >= 2:
            bx, by = basis[0], basis[1]
        else:
            raise ValueError("basis 形态不认识")
        bx = np.array([float(x) for x in bx], dtype=np.float64)
        by = np.array([float(x) for x in by], dtype=np.float64)
        if bx.shape != (3,) or by.shape != (3,):
            raise ValueError("x/y 必须是 3 个数字")
    except Exception as e:
        E("FRAME_BAD_BASIS", "part %s frame %s: basis 非法（%s）" % (pid, fid, str(e)[:100]), pid)
        return
    nx, ny = float(np.linalg.norm(bx)), float(np.linalg.norm(by))
    if abs(nx - 1.0) > 1e-3 or abs(ny - 1.0) > 1e-3:
        E("FRAME_BASIS_NOT_UNIT", "part %s frame %s: basis 不是单位向量（|x|=%.6f |y|=%.6f）"
          % (pid, fid, nx, ny), pid)
    dot = float(np.dot(bx, by))
    if abs(dot) > 1e-3:
        E("FRAME_BASIS_NOT_ORTHOGONAL", "part %s frame %s: basis x·y=%.6f（要求 ≈0）" % (pid, fid, dot), pid)
    if normal is not None:
        try:
            nrm = np.array([float(x) for x in normal], dtype=np.float64)
            z = np.cross(bx, by)
            zn = float(np.linalg.norm(z))
            if zn > 1e-9:
                cosang = float(np.dot(z / zn, nrm / max(1e-12, float(np.linalg.norm(nrm)))))
                if cosang < 0.9:
                    W("FRAME_NORMAL_MISMATCH",
                      "part %s frame %s: normal 与 basis 叉乘方向不一致（cos=%.3f），合并对齐可能差 180°"
                      % (pid, fid, cosang), pid)
        except Exception:
            pass
def _manifest_validate(m, check_files=False, check_md5=False, mmu=None):
    """清单校验 → {ok, verdict, errors[], warnings[], manifest_md5, counts, units}。

    校验：id 唯一 / file 存在 / file_md5 / 接口两端存在（含 frame）/ basis 正交 / 单位一致 / seal 完整性。
    check_files / check_md5 默认关（gate 只看结构；validate 动作默认开）。
    """
    errors, warnings = [], []

    def E(code, msg, path=None):
        errors.append({"code": str(code), "msg": str(msg)[:400], "path": (str(path) if path is not None else None)})

    def W(code, msg, path=None):
        warnings.append({"code": str(code), "msg": str(msg)[:400], "path": (str(path) if path is not None else None)})

    if not isinstance(m, dict):
        E("BAD_MANIFEST", "manifest 必须是 JSON 对象")
        return {"ok": False, "verdict": "fail", "errors": errors, "warnings": warnings,
                "manifest_md5": None, "counts": {"parts": 0, "interfaces": 0, "allow_pairs": 0}}
    mmu = float(mmu if mmu is not None else _KIT.units())
    schema = m.get("schema")
    if schema is None:
        W("NO_SCHEMA", "manifest 没有 schema（建议 %s）" % MANIFEST_SCHEMA)
    elif str(schema) != MANIFEST_SCHEMA:
        W("SCHEMA_MISMATCH", "schema=%r ≠ %s（按 v1 尽力校验）" % (schema, MANIFEST_SCHEMA))
    units = str(m.get("units") or "mm")
    conv = UNIT_MM_PER_UNIT.get(units)
    if conv is None:
        E("BAD_UNITS", "units 只支持 mm/cm/m，收到 %r" % units)
    elif abs(mmu - conv) / conv > 0.01:
        W("UNIT_SCALE_SUSPECT",
          "manifest units=%s ⇒ 期望 1 单位=%.3g mm，实测 %.6g mm（把 scene.unit_settings.scale_length "
          "设成 %.6g；否则 loc_mm/深度全部按错单位读）" % (units, conv, mmu, conv / 1000.0))

    parts = m.get("parts")
    if parts is None:
        parts = []
    if not isinstance(parts, list):
        E("PARTS_NOT_LIST", "parts 必须是数组")
        parts = []
    ids, by_id, frames_by_part = set(), {}, {}
    for i, p in enumerate(parts):
        if not isinstance(p, dict):
            E("BAD_PART", "parts[%d] 不是对象" % i)
            continue
        pid = str(p.get("id") or "")
        if not pid:
            E("MISSING_PART_ID", "parts[%d] 没有 id" % i)
            continue
        if pid in ids:
            E("DUP_ID", "part id 重复: %s" % pid, pid)
            continue
        ids.add(pid)
        by_id[pid] = p
        fp = p.get("file")
        if not fp:
            W("MISSING_FILE", "part %s 没有 file（只能当纯数据清单用，merge 会跳过）" % pid, pid)
        else:
            f = _path(fp)
            if check_files and not os.path.isfile(f):
                E("MISSING_FILE", "part %s 的 file 不存在: %s" % (pid, f), pid)
            elif check_md5 and os.path.isfile(f):
                want = str(p.get("file_md5") or "").lower()
                if not want:
                    W("MISSING_FILE_MD5", "part %s 没有 file_md5，文件无法复核" % pid, pid)
                else:
                    got = _sha256_file(f)
                    if got is None:
                        W("FILE_UNREADABLE", "part %s 的文件读不了: %s" % (pid, f), pid)
                    elif got != want:
                        E("MD5_MISMATCH", "part %s 文件 md5 不匹配（manifest=%s… 实测=%s…）"
                          % (pid, want[:12], got[:12]), pid)
        tr = p.get("transform")
        if tr is not None:
            if not isinstance(tr, dict):
                E("BAD_TRANSFORM", "part %s transform 必须是对象" % pid, pid)
            else:
                for k in ("loc_mm", "rot_euler_deg", "scale"):
                    if k not in tr:
                        continue
                    v = tr.get(k)
                    if not isinstance(v, (list, tuple)) or len(v) != 3 or any(_num(x) is None for x in v):
                        E("BAD_TRANSFORM", "part %s transform.%s 必须是 3 个数字" % (pid, k), pid)
                        continue
                    if k == "scale" and any(float(x) <= 0.0 for x in v):
                        E("BAD_TRANSFORM", "part %s transform.scale 必须 > 0" % pid, pid)
        frames = p.get("interface_frames")
        fids = set()
        if frames is not None:
            if not isinstance(frames, list):
                E("BAD_FRAMES", "part %s interface_frames 必须是数组" % pid, pid)
            else:
                for fr in frames:
                    if not isinstance(fr, dict):
                        E("BAD_FRAME", "part %s 有 interface_frame 不是对象" % pid, pid)
                        continue
                    fid = str(fr.get("id") or "")
                    if not fid:
                        E("MISSING_FRAME_ID", "part %s 有 interface_frame 缺 id" % pid, pid)
                        continue
                    if fid in fids:
                        W("FRAME_ID_DUP", "part %s frame id 重复: %s" % (pid, fid), pid)
                        continue
                    fids.add(fid)
                    _validate_frame(fr, pid, fid, E, W)
        frames_by_part[pid] = fids

    interfaces = m.get("interfaces") or []
    if not isinstance(interfaces, list):
        E("INTERFACES_NOT_LIST", "interfaces 必须是数组")
        interfaces = []
    iids, iface_ids = set(), set()
    for i, it in enumerate(interfaces):
        if not isinstance(it, dict):
            E("BAD_INTERFACE", "interfaces[%d] 不是对象" % i)
            continue
        iid = str(it.get("id") or "")
        if not iid:
            W("MISSING_INTERFACE_ID", "interfaces[%d] 没有 id" % i)
            iid = "IF#%d" % i
        if iid in iids:
            E("DUP_ID", "interface id 重复: %s" % iid, iid)
            continue
        iids.add(iid)
        iface_ids.add(iid)
        a, b = str(it.get("a") or ""), str(it.get("b") or "")
        if a not in by_id:
            E("CONN_UNKNOWN_PART", "interface %s: 端 a=%r 不在 parts 里" % (iid, a), iid)
        if b not in by_id:
            E("CONN_UNKNOWN_PART", "interface %s: 端 b=%r 不在 parts 里" % (iid, b), iid)
        if a and a == b:
            E("SELF_INTERFACE", "interface %s 的两端是同一个 part: %s" % (iid, a), iid)
        fa, fb = it.get("frame_a"), it.get("frame_b")
        if fa is not None and a in frames_by_part and str(fa) not in frames_by_part[a]:
            E("FRAME_NOT_FOUND", "interface %s: 端 a=%s 没有 frame %r" % (iid, a, fa), iid)
        if fb is not None and b in frames_by_part and str(fb) not in frames_by_part[b]:
            E("FRAME_NOT_FOUND", "interface %s: 端 b=%s 没有 frame %r" % (iid, b, fb), iid)
        fit = it.get("fit")
        if fit is not None and str(fit) not in FIT_CLASSES:
            W("UNKNOWN_FIT", "interface %s: fit=%r 不在 %s 里（按 location 口径处理）"
              % (iid, fit, list(FIT_CLASSES)), iid)
        for k in ("nominal_mm", "tol_mm", "allow_overlap_mm", "clearance_min_mm"):
            v = it.get(k)
            if v is None:
                continue
            nv = _num(v)
            if nv is None or nv < 0:
                E("BAD_INTERFACE_NUM", "interface %s: %s 必须是非负数字" % (iid, k), iid)

    allow = m.get("allow_pairs") or []
    if not isinstance(allow, list):
        E("ALLOW_NOT_LIST", "allow_pairs 必须是数组")
        allow = []
    aids = set()
    for i, ap in enumerate(allow):
        if not isinstance(ap, dict):
            E("BAD_ALLOW", "allow_pairs[%d] 不是对象" % i)
            continue
        aid = str(ap.get("id") or "")
        if not aid:
            W("MISSING_ALLOW_ID", "allow_pairs[%d] 没有 id" % i)
            aid = "ALLOW#%d" % i
        if aid in aids:
            E("DUP_ID", "allow id 重复: %s" % aid, aid)
            continue
        aids.add(aid)
        a, b = str(ap.get("a") or ""), str(ap.get("b") or "")
        if a not in by_id:
            E("ALLOW_UNKNOWN_PART", "allow %s: 端 a=%r 不在 parts 里" % (aid, a), aid)
        if b not in by_id:
            E("ALLOW_UNKNOWN_PART", "allow %s: 端 b=%r 不在 parts 里" % (aid, b), aid)
        ref = ap.get("interface")
        if ref is not None and str(ref) not in iface_ids:
            W("ALLOW_UNKNOWN_CONN", "allow %s: 引用了不存在的 interface %r" % (aid, ref), aid)
        md_ = _num(ap.get("max_depth_mm"))
        mv = _num(ap.get("max_volume_mm3"))
        if md_ is None and mv is None:
            E("ALLOW_NO_LIMIT", "allow %s: 必须给 max_depth_mm 或 max_volume_mm3（无上限的允许交叠是危险默认）" % aid, aid)
        for k, nv in (("max_depth_mm", md_), ("max_volume_mm3", mv)):
            if nv is not None and nv < 0:
                E("BAD_ALLOW_NUM", "allow %s: %s 必须 ≥ 0" % (aid, k), aid)

    sealed = bool(m.get("sealed"))
    if sealed:
        want = str(m.get("sealed_md5") or "")
        got = _manifest_md5(m)
        if want and got and want != got:
            E("MANIFEST_SEAL_MISMATCH", "清单在 seal 之后被改过（sealed_md5=%s… 实测=%s…）"
              % (want[:12], got[:12]))
    return {"ok": not errors, "verdict": "pass" if not errors else "fail",
            "errors": errors, "warnings": warnings, "manifest_md5": _manifest_md5(m),
            "sealed": sealed, "counts": _manifest_counts(m),
            "units": {"manifest_units": units, "mm_per_unit_scene": _r(mmu, 6),
                      "expected_mm_per_unit": conv}}
def _obj_part_map(manifest, names):
    """场景对象名 → part id：显式 objects 列表 / 同名 / 合并命名 <partid>__ 前缀。"""
    out = {}
    if not isinstance(manifest, dict):
        return out
    for p in manifest.get("parts") or []:
        if not isinstance(p, dict):
            continue
        pid = str(p.get("id") or "")
        if not pid:
            continue
        for o in _as_list(p.get("objects")):
            out[str(o)] = pid
        for n in names:
            if n == pid or n.startswith(pid + "__"):
                out[str(n)] = pid
    return out


def _effective_spec(manifest, spec):
    """把 manifest + spec 合并成白名单：allow / clearance / interfaces。"""
    sp = dict(spec) if isinstance(spec, dict) else {}
    allow = [dict(x) for x in (sp.get("allow") or []) if isinstance(x, dict)]
    clearance = [dict(x) for x in (sp.get("clearance") or []) if isinstance(x, dict)]
    interfaces = [dict(x) for x in (sp.get("interfaces") or []) if isinstance(x, dict)]
    if isinstance(manifest, dict):
        for x in manifest.get("interfaces") or []:
            if isinstance(x, dict):
                interfaces.append(dict(x))
        for ap in manifest.get("allow_pairs") or []:
            if isinstance(ap, dict):
                allow.append(dict(ap))
        for it in manifest.get("interfaces") or []:
            if not isinstance(it, dict):
                continue
            ov = _num(it.get("allow_overlap_mm"))
            if ov is not None and ov > 0:
                allow.append({"id": "IF-ALLOW-%s" % it.get("id"), "a": it.get("a"), "b": it.get("b"),
                              "max_depth_mm": ov, "max_volume_mm3": None,
                              "reason": "interface %s 的 allow_overlap_mm=%.4g" % (it.get("id"), ov),
                              "interface": it.get("id")})
    return {"allow": allow, "clearance": clearance, "interfaces": interfaces,
            "micro_gap_mm": _num(sp.get("micro_gap_mm"))}


def _allow_match(allow_list, obj_part, na, nb):
    """allow 条目 a/b 可以是对象名，也可以是 part id（含合并前缀 <partid>__）。"""
    for ent in allow_list or []:
        ea, eb = str(ent.get("a") or ""), str(ent.get("b") or "")
        if not ea or not eb:
            continue

        def _hit(name, side):
            return name == side or obj_part.get(name) == side

        if (_hit(na, ea) and _hit(nb, eb)) or (_hit(na, eb) and _hit(nb, ea)):
            return ent
    return None


# ────────────────────────────── 浮空判据 ──────────────────────────────
def _manifest_float(manifest, md, names, mmu, contact_eps_mm):
    """manifest 连接图可达性 + 逐接口接触验证（装配口径，替代 1%/0.3mm 单件口径）。"""
    floaters, unresolved, rows = [], [], []
    if not isinstance(manifest, dict):
        return {"floaters": floaters, "unresolved": unresolved, "interfaces": rows, "graph": None}
    obj_part = _obj_part_map(manifest, names)
    parts = [str(p.get("id")) for p in (manifest.get("parts") or [])
             if isinstance(p, dict) and p.get("id")]
    part_names = {pid: sorted([n for n in names if obj_part.get(n) == pid]) for pid in parts}
    present = [pid for pid in parts if part_names.get(pid)]
    for pid in parts:
        if not part_names.get(pid):
            unresolved.append({"reason": "manifest_part_not_in_scene", "part": pid,
                               "why": "manifest 的 part %s 在扫描范围里没有对应对象（合并漏件？）" % pid})
    interfaces = [it for it in (manifest.get("interfaces") or []) if isinstance(it, dict)]
    deg = {pid: 0 for pid in parts}
    adj = {pid: set() for pid in parts}
    for it in interfaces:
        a, b = str(it.get("a") or ""), str(it.get("b") or "")
        if a in adj and b in adj:
            adj[a].add(b)
            adj[b].add(a)
            deg[a] = deg.get(a, 0) + 1
            deg[b] = deg.get(b, 0) + 1
    root = str(manifest.get("root_part") or "")
    unreachable = []
    if len(parts) > 1:
        if root and root in adj:
            seen, stack = {root}, [root]
            while stack:
                cur = stack.pop()
                for nxt in adj[cur]:
                    if nxt not in seen:
                        seen.add(nxt)
                        stack.append(nxt)
            unreachable = [pid for pid in present if pid not in seen]
        else:
            unreachable = [pid for pid in present if deg.get(pid, 0) == 0]
    for pid in unreachable:
        floaters.append({"object": (part_names.get(pid) or [pid])[0], "part": pid,
                         "objects": part_names.get(pid) or [pid],
                         "reason": "unconnected_in_manifest", "mesh_gap_mm": None,
                         "why": "part %s 在 manifest 连接图里没有任何声明接口（也没有 root_part 可达路径）" % pid})
    for it in interfaces:
        iid = str(it.get("id") or "")
        a, b = str(it.get("a") or ""), str(it.get("b") or "")
        oa = [n for n in names if obj_part.get(n) == a]
        ob = [n for n in names if obj_part.get(n) == b]
        if not oa or not ob:
            unresolved.append({"reason": "interface_part_missing", "interface": iid,
                               "why": "interface %s 的端在扫描范围里缺对象（a=%s:%d b=%s:%d）"
                                      % (iid, a, len(oa), b, len(ob))})
            rows.append({"id": iid, "a": a, "b": b, "state": "unresolved", "gap_mm": None})
            continue
        best = None
        for na in oa:
            for nb in ob:
                if na in md and nb in md:
                    g = _surface_gap(md[na], md[nb], samples=400)
                    if g is not None:
                        best = g if best is None else min(best, g)
        if best is None:
            unresolved.append({"reason": "interface_measure_failed", "interface": iid,
                               "why": "interface %s 两端量不到表面距离" % iid})
            rows.append({"id": iid, "a": a, "b": b, "state": "unresolved", "gap_mm": None})
            continue
        gap_mm = best * mmu
        tol = _num(it.get("tol_mm"))
        nom = _num(it.get("nominal_mm"))
        allow_ov = _num(it.get("allow_overlap_mm"))
        limit = max(float(tol if tol is not None else (contact_eps_mm or 0.3)),
                    float(contact_eps_mm or 0.3))
        if allow_ov is not None and allow_ov > 0:
            limit = max(limit, float(allow_ov))
        if nom is not None:
            limit = max(limit, abs(nom) + float(tol if tol is not None else 0.0))
        state = "contact" if gap_mm <= limit + 1e-9 else "float"
        rows.append({"id": iid, "a": a, "b": b, "state": state, "gap_mm": _r(gap_mm, 4),
                     "limit_mm": _r(limit, 4)})
        if state == "float":
            floaters.append({"object": (oa or [a])[0], "part": a, "other_part": b,
                             "objects": sorted(oa + ob), "reason": "interface_not_contact",
                             "interface": iid, "mesh_gap_mm": _r(gap_mm, 4),
                             "why": "接口 %s 两端网格最近距离 %.3f mm > 容许 %.3f mm —— 没搭上"
                                    % (iid, gap_mm, limit)})
    graph = {"parts": len(parts), "present_parts": len(present), "edges": len(interfaces),
             "isolated": [pid for pid in parts if deg.get(pid, 0) == 0],
             "root_part": root or None, "unreachable": unreachable}
    return {"floaters": floaters, "unresolved": unresolved, "interfaces": rows, "graph": graph}


def _connectivity_float(names, contact_eps_mm, include_hidden=False):
    """没有 manifest 时退 audit_connectivity（单件/装配口径混用风险写进 note）。"""
    rec, err = _call_api("audit", "connectivity",
                         {"objects": list(names), "include_hidden": bool(include_hidden),
                          "micro_gap_mm": float(contact_eps_mm or 0.3)})
    out = {"floaters": [], "unresolved": [], "state": "degraded", "receipt": None,
           "note": "audit_connectivity 的 1% 可见浮块 + micro_gap 是**单件**口径；装配请给 manifest 用连接图"}
    if err:
        out["unresolved"].append({"reason": "connectivity_unavailable", "why": err})
        return out
    out["receipt"] = {k: rec.get(k) for k in ("analyzed", "objects", "tris", "real_floater_count",
                                             "visible_floater_count", "unconfirmed_floater_count",
                                             "micro_floater_count", "floater_count", "micro_confirm")}
    if not rec.get("analyzed"):
        out["unresolved"].append({"reason": "connectivity_not_analyzed",
                                  "why": str(rec.get("reason") or "未分析")[:240]})
        return out
    gate = rec.get("gate") or {}
    for f in rec.get("floaters") or []:
        if not isinstance(f, dict):
            continue
        attr = f.get("attribution") or {}
        raw = attr.get("contains")
        if raw is None:
            raw = attr.get("object")
        if isinstance(raw, dict):
            objs = list(raw.keys())
        elif isinstance(raw, (list, tuple, set)):
            objs = list(raw)
        elif raw:
            objs = [raw]
        else:
            objs = []
        out["floaters"].append({"object": (objs or [None])[0], "objects": sorted(str(x) for x in objs if x),
                                "reason": "mesh_float", "visible": bool(f.get("visible")),
                                "span_fraction": f.get("span_fraction"), "gap_mm": f.get("gap_mm"),
                                "bbox_micro": f.get("bbox_micro"), "mesh_confirmed": f.get("mesh_confirmed"),
                                "why": "连通分量与任何其它分量都不相接（audit_connectivity）"})
    unconf = int(gate.get("unconfirmed_floater_count") or rec.get("unconfirmed_floater_count") or 0)
    if unconf:
        out["unresolved"].append({"reason": "connectivity_unconfirmed",
                                  "why": "%d 个可见分量无法确认是否相接（%s）"
                                         % (unconf, rec.get("micro_confirm") or "未说明")})
    real = int(gate.get("real_floater_count") or rec.get("real_floater_count") or 0)
    if real and not out["floaters"]:
        out["unresolved"].append({"reason": "floater_unattributed",
                                  "why": "connectivity 报 %d 个浮块但回执里没有逐块归因" % real})
    out["state"] = "fail" if (real or out["floaters"]) else ("degraded" if out["unresolved"] else "pass")
    return out
# ────────────────────────────── 分类核心 ──────────────────────────────
def _pair_key(a, b):
    x, y = str(a), str(b)
    return (x, y) if x <= y else (y, x)


def _severity(depth_mm, volume_mm3, contained=False):
    if contained:
        return "high" if (volume_mm3 or 0.0) > 1000.0 else "medium"
    d = _num(depth_mm)
    v = _num(volume_mm3)
    if d is None and v is None:
        return "unknown"
    if (d is not None and d > 2.0) or (v is not None and v > 500.0):
        return "high"
    if (d is not None and d > 0.5) or (v is not None and v > 10.0):
        return "medium"
    return "low"


def _classify_pair(i, j, names, md, contain_ij, contain_ji, aabb_gap_m, is_candidate,
                   deep_map, allow_list, obj_part, depth_eps_mm, contact_eps_mm,
                   report_gap_mm, floor_mm3, mmu):
    """单对分类（返回 rec，不抛）。所有数字 mm；深度/体积不可测时绝不臆造。"""
    a, b = names[i], names[j]
    ma, mb = md[a], md[b]
    rec = {"a": a, "b": b, "class": None, "declared": False, "spec_ref": None,
           "tri_overlap_pairs": 0, "depth_mm": None, "depth_measured": False,
           "volume_mm3": None, "volume_reliable": False, "min_gap_mm": None,
           "closed": {"a": bool(ma["closed"]), "b": bool(mb["closed"])},
           "normals_ok": {"a": bool(ma["normals_ok"]), "b": bool(mb["normals_ok"])},
           "severity": "unknown", "method": None, "reason": ""}
    invalid = not (ma["closed"] and ma["normals_ok"] and mb["closed"] and mb["normals_ok"])
    allow_rec = _allow_match(allow_list, obj_part, a, b)
    max_depth = _num((allow_rec or {}).get("max_depth_mm"))
    max_vol = _num((allow_rec or {}).get("max_volume_mm3"))
    if allow_rec:
        rec["declared"] = True
        rec["spec_ref"] = str(allow_rec.get("id") or allow_rec.get("reason") or "spec.allow")

    def _allow_ok(depth, vol, contained_gap=None):
        ok_d = (max_depth is None) or (depth is not None and depth <= max_depth * 1.001 + 1e-9)
        ok_v = (max_vol is None) or (vol is not None and vol <= max_vol * 1.001 + 1e-9)
        if contained_gap is not None and max_depth is not None and max_vol is None:
            return contained_gap <= max_depth * 1.001 + 1e-9
        return bool(ok_d and ok_v)

    if is_candidate:
        tri = 0
        try:
            tri = len(ma["bvh"].overlap(mb["bvh"]) or [])
        except Exception as e:
            rec["reason"] = "BVH.overlap 失败: %s" % str(e)[:120]
            rec["class"] = "unresolved"
            return rec
        rec["tri_overlap_pairs"] = int(tri)
        if tri > 0:
            rec["method"] = "BVH tri-tri"
            if invalid:
                rec["class"] = "unresolved"
                rec["reason"] = ("两件表面确实相交（%d 对三角面），但网格开放/法线朝内 ⇒ 内外与深度不可信，不猜" % tri)
                return rec
            drec = (deep_map or {}).get(_pair_key(a, b))
            depth = _num((drec or {}).get("depth_mm"))
            vol = _num((drec or {}).get("volume_mm3"))
            if drec and drec.get("depth_measured") is False:
                depth = None
            # contract 的 INSIDE_MAX_TRIS=0 实际会跳过侵入判定（depth 恒 None）⇒ 深度必须兜底量
            if depth is None:
                own = _depth_measure(ma, mb)
                own_depth = _num(own.get("depth_mm"))
                if own_depth is not None:
                    depth = own_depth
                if vol is None:
                    vol = _num(own.get("volume_mm3"))
                rec["method"] = ("contract 体积 + assembly 深度兜底" if drec
                                 else own.get("method"))
            else:
                rec["method"] = "contract.interference_report（复用）"
            rec["depth_mm"] = _r(depth, 4)
            rec["depth_measured"] = depth is not None
            rec["volume_mm3"] = _r(vol, 4)
            rec["volume_reliable"] = bool((drec or {}).get("volume_reliable", vol is not None))
            if depth is None and vol is None:
                rec["class"] = "unresolved"
                rec["reason"] = "三角面相交但深度/体积都量不出来（采样失败）"
                return rec
            if allow_rec:
                if _allow_ok(depth, vol):
                    rec["class"] = "hidden_ok"
                    rec["reason"] = ("spec.allow 覆盖：深度 %.3f mm / 体积 %.1f mm³ 在允许范围内"
                                     % (depth or 0.0, vol or 0.0))
                else:
                    rec["class"] = "clash"
                    rec["reason"] = ("spec.allow 存在但超限：深度 %s mm（上限 %s）/ 体积 %s mm³（上限 %s）"
                                     % (_r(depth, 3), max_depth, _r(vol, 1), max_vol))
            elif depth is not None and depth > float(depth_eps_mm) + 1e-9:
                rec["class"] = "clash"
                rec["reason"] = "穿透深度 %.3f mm > depth_eps %.3f mm" % (depth, float(depth_eps_mm))
            elif vol is not None and vol > float(floor_mm3) + 1e-9:
                rec["class"] = "clash"
                rec["reason"] = "相交体积 %.3f mm³ > floor %.3f mm³" % (vol, float(floor_mm3))
            else:
                rec["class"] = "contact"
                rec["reason"] = ("面接触（深度 %.3f mm ≤ %.3f / 体积 %.3f mm³ ≤ 下限）"
                                 % (depth or 0.0, float(depth_eps_mm), vol or 0.0))
            rec["severity"] = _severity(rec["depth_mm"], rec["volume_mm3"])
            return rec
        # ---- 三角面不相交：先补"完全包含"分支（旧实现会在这里 continue，漏判） ----
        if (contain_ij or contain_ji) and not invalid:
            if contain_ij:
                small, big, sm, bg = ma, mb, a, b
            else:
                small, big, sm, bg = mb, ma, b, a
            pts = _sample_surface(small["tris"], 400)
            n_in, tested = _count_inside(pts, big["bvh"])
            frac = (n_in / float(tested)) if tested else 0.0
            if frac >= 0.5:
                vol_est = abs(float(small["volume_signed_m3"])) * (mmu ** 3)
                gap_min = None
                for p in pts:
                    r = big["bvh"].find_nearest(Vector(p))
                    if r is None or r[0] is None:
                        continue
                    d = float(r[3])
                    gap_min = d if gap_min is None else min(gap_min, d)
                gap_min_mm = (gap_min * mmu) if gap_min is not None else None
                rec["method"] = "AABB 包含 + 射线奇偶内外（包含探针）"
                rec["depth_mm"] = _r(gap_min_mm, 4)
                rec["depth_measured"] = gap_min_mm is not None
                rec["volume_mm3"] = _r(vol_est, 4)
                rec["volume_reliable"] = False
                rec["min_gap_mm"] = _r(gap_min_mm, 4)
                rec["contained_in"] = bg
                rec["contained_part"] = sm
                if allow_rec and _allow_ok(None, vol_est, contained_gap=gap_min_mm):
                    rec["class"] = "hidden_ok"
                    rec["reason"] = ("完全包含但 spec.allow 覆盖（小件体积 %.1f mm³ / 包络间隙 %s mm）"
                                     % (vol_est, _r(gap_min_mm, 3)))
                    rec["severity"] = "low"
                else:
                    rec["class"] = "contained"
                    rec["reason"] = ("完全包含：%s 的整体被 %s 包住（表面点 %.0f%% 在内部，小件体积 %.1f mm³，"
                                     "包络最小间隙 %s mm）—— 面无相交所以旧全对全会漏掉"
                                     % (sm, bg, frac * 100.0, vol_est, _r(gap_min_mm, 3)))
                    rec["severity"] = _severity(None, vol_est, contained=True)
                return rec
        # ---- 其余：量间隙（接触面积不在全对全里算，成本控制） ----
        gap_m = _surface_gap(ma, mb, samples=500)
        gap_mm = (gap_m * mmu) if gap_m is not None else None
        rec["min_gap_mm"] = _r(gap_mm, 4)
        rec["method"] = "表面采样最近距离"
        if gap_mm is None:
            rec["class"] = "unresolved"
            rec["reason"] = "AABB 重叠但表面距离量不出来（网格退化？）"
        elif gap_mm <= float(contact_eps_mm) + 1e-9:
            rec["class"] = "contact"
            rec["reason"] = "表面间隙 %.3f mm ≤ contact_eps %.3f mm" % (gap_mm, float(contact_eps_mm))
        else:
            rec["class"] = "gap"
            rec["reason"] = "AABB 重叠但表面间隙 %.3f mm（分离/互锁）" % gap_mm
        return rec
    # ---- AABB 不相交的近对（AABB 距离是真实距离下界，可证） ----
    rec["method"] = "AABB 距离下界"
    if aabb_gap_m is not None and (aabb_gap_m * mmu) > float(contact_eps_mm) + 1e-9:
        rec["class"] = "gap"
        rec["min_gap_mm"] = _r(aabb_gap_m * mmu, 4)
        rec["reason"] = "AABB 距离下界 %.3f mm > contact_eps（真实间隙只会更大）" % (aabb_gap_m * mmu)
        return rec
    gap_m = _surface_gap(ma, mb, samples=300)
    gap_mm = (gap_m * mmu) if gap_m is not None else None
    rec["min_gap_mm"] = _r(gap_mm, 4)
    rec["method"] = "AABB 近对 + 表面采样最近距离"
    if gap_mm is None:
        rec["class"] = "unresolved"
        rec["reason"] = "近对但表面距离量不出来"
    elif gap_mm <= float(contact_eps_mm) + 1e-9:
        rec["class"] = "contact"
        rec["reason"] = "表面间隙 %.3f mm ≤ contact_eps %.3f mm" % (gap_mm, float(contact_eps_mm))
    elif gap_mm <= float(report_gap_mm) + 1e-9:
        rec["class"] = "gap"
        rec["reason"] = "表面间隙 %.3f mm（在 report_gap %.3f mm 内）" % (gap_mm, float(report_gap_mm))
    else:
        rec["class"] = "gap"
        rec["reason"] = "表面间隙 %.3f mm（超出 report_gap，按 gap 统计不展开）" % gap_mm
    return rec
# ────────────────────────────── contract 深测复用 ──────────────────────────────
def _contract_deep(names, include_hidden, depth_eps_mm, max_pairs):
    """复用 K.dsh_contract_api["interference_report"] 的逐对深度/体积（拿不到返回 None + 原因）。"""
    api = _api("contract")
    if api is None:
        return None, "K.dsh_contract_api 不存在（preload 里带 contract）"
    scope = sorted(set(str(n) for n in names))
    if not scope:
        return None, "scope 为空"
    res, err = _call_api("contract", "interference_report",
                         {"scope": scope, "use_bvh": True, "limit": int(max(1, int(max_pairs))),
                          "declared_ok": True, "depth_eps": float(depth_eps_mm),
                          "include_hidden": bool(include_hidden)})
    if err:
        return None, err
    if not res.get("ok"):
        return None, "interference_report ok=false: %s" % str(res.get("error") or res.get("note"))[:200]
    deep = {}
    for grp in ("offenders", "exempted"):
        for rec in res.get(grp) or []:
            if not isinstance(rec, dict):
                continue
            key = _pair_key(rec.get("a"), rec.get("b"))
            deep[key] = {"depth_mm": rec.get("depth_mm"), "depth_mean_mm": rec.get("depth_mean_mm"),
                         "depth_measured": bool(rec.get("depth_measured", rec.get("depth_mm") is not None)),
                         "volume_mm3": rec.get("overlap_volume_mm3"),
                         "volume_reliable": bool(rec.get("volume_reliable", False)),
                         "surface_inside_frac": rec.get("surface_inside_frac"),
                         "closed": rec.get("closed"), "declared": rec.get("declared"),
                         "severity": rec.get("severity"), "method": rec.get("method")}
    meta = {k: res.get(k) for k in ("objects", "pairs_aabb", "pairs_tested", "pairs_checked",
                                   "pairs_overlapping", "exempted_count") if k in res}
    meta["used"] = True
    return {"pairs": deep, "meta": meta}, None


# ────────────────────────────── scan 核心 ──────────────────────────────
def _scan_core(objs, names, manifest=None, spec=None, limit=0, max_pairs=20000,
               depth_eps_mm=0.2, contact_eps_mm=0.3, report_gap_mm=5.0, floor_mm3=1.0,
               summary_only=False, use_contract=True, include_hidden=False, md=None,
               budget_ms=0):
    t0 = time.perf_counter()
    mmu = float(_KIT.units())
    scale = max(mmu, 1e-9)
    names = [str(n) for n in names]
    md_errors = {}
    if md is None:
        md, md_errors = _mesh_map(objs)
    names = [n for n in names if n in md]
    n = len(names)
    if n == 0:
        return {"ok": False, "error": "没有可扫描的求值网格（0 个对象有三角面）—— 空产出不静默成功",
                "md_errors": md_errors}
    esp = _effective_spec(manifest, spec)
    allow_list = esp["allow"]
    obj_part = _obj_part_map(manifest, names)
    mn = np.array([md[nm]["aabb"][0] for nm in names], dtype=np.float64)
    mx = np.array([md[nm]["aabb"][1] for nm in names], dtype=np.float64)
    eps = 1e-9 * max(1.0, float(np.abs(np.concatenate([mn, mx])).max()))
    lo = np.maximum(mn[:, None, :], mn[None, :, :])
    hi = np.minimum(mx[:, None, :], mx[None, :, :])
    inter = hi - lo
    # 相贴（inter==0，如零厚度面片切件）也必须进候选：用 -eps 而不是 >0，否则 plane 切立方体会被当成"近对"
    aabb_overlap = (inter >= -eps).all(axis=2)
    np.fill_diagonal(aabb_overlap, False)
    gap_vec = np.maximum(0.0, np.maximum(mn[:, None, :] - mx[None, :, :], mn[None, :, :] - mx[:, None, :]))
    gap = np.sqrt((gap_vec ** 2).sum(axis=2))
    np.fill_diagonal(gap, np.inf)
    contain = ((mn[:, None, :] >= mn[None, :, :] - eps) & (mx[:, None, :] <= mx[None, :, :] + eps)).all(axis=2)
    np.fill_diagonal(contain, False)
    same_box = (((np.abs(mn[:, None, :] - mn[None, :, :]) <= eps).all(axis=2)) &
                ((np.abs(mx[:, None, :] - mx[None, :, :]) <= eps).all(axis=2)))
    contain = contain & ~same_box
    inter_vol = np.prod(np.maximum(inter, 0.0), axis=2)

    cands, near = [], []
    for i in range(n):
        for j in range(i + 1, n):
            if aabb_overlap[i, j]:
                cands.append((i, j))
            elif gap[i, j] <= float(report_gap_mm) / scale:
                near.append((i, j))

    def _ckey(ij):
        i, j = ij
        cont = 1 if bool(contain[i, j] or contain[j, i]) else 0
        return (-cont, -float(inter_vol[i, j]), float(gap[i, j]), names[i], names[j])

    cands.sort(key=_ckey)
    near.sort(key=lambda ij: (float(gap[ij]), names[ij[0]], names[ij[1]]))
    total_avail = len(cands) + len(near)
    cap = int(max_pairs) if int(max_pairs) > 0 else total_avail
    if int(limit) > 0:
        cap = min(cap, int(limit))
    tested_c = cands[:cap]
    tested_n = near[:max(0, cap - len(tested_c))]
    truncated = (len(tested_c) + len(tested_n)) < total_avail

    deep_map, contract_meta = None, {"used": False, "reason": None}
    if use_contract and cands:
        deep, derr = _contract_deep(names, include_hidden, depth_eps_mm, max_pairs)
        if deep is not None:
            deep_map = deep["pairs"]
            contract_meta = deep["meta"]
        else:
            contract_meta = {"used": False, "reason": str(derr)[:240]}

    pairs, matrix_cells = [], []
    budget_hit = False
    checked = 0
    t_loop = time.perf_counter()
    for is_cand, seq in ((True, tested_c), (False, tested_n)):
        for (i, j) in seq:
            if int(budget_ms) > 0 and (time.perf_counter() - t_loop) * 1000.0 > float(budget_ms):
                budget_hit = True
                break
            rec = _classify_pair(i, j, names, md, bool(contain[i, j]), bool(contain[j, i]),
                                 float(gap[i, j]), is_cand, deep_map, allow_list, obj_part,
                                 depth_eps_mm, contact_eps_mm, report_gap_mm, floor_mm3, mmu)
            if rec.get("class") not in CLASSES:
                rec["class"] = "unresolved"
                rec["reason"] = "分类器返回未知 class（按证据不足处理）"
            pairs.append(rec)
            matrix_cells.append([int(i), int(j), rec["class"], rec.get("depth_mm"), rec.get("volume_mm3")])
            checked += 1
        if budget_hit:
            break
    if budget_hit:
        truncated = True

    # ---- 浮空：manifest 连接图优先，否则 audit_connectivity ----
    floaters, float_unresolved, float_source = [], [], "none"
    manifest_float_rows, graph = [], None
    if isinstance(manifest, dict):
        mf = _manifest_float(manifest, md, names, mmu, contact_eps_mm)
        floaters = mf["floaters"]
        float_unresolved = mf["unresolved"]
        manifest_float_rows = mf["interfaces"]
        graph = mf["graph"]
        float_source = "manifest"
    else:
        cf = _connectivity_float(names, contact_eps_mm, include_hidden)
        floaters = cf["floaters"]
        float_unresolved = cf["unresolved"]
        float_source = "audit_connectivity"

    existing = {_pair_key(p["a"], p["b"]): p for p in pairs}
    for row in manifest_float_rows:
        if row.get("state") != "float":
            continue
        key = _pair_key(row.get("a"), row.get("b"))
        if key in existing:
            existing[key]["interface_state"] = "float"
            existing[key].setdefault("reason", "manifest 接口未接触")
            continue
        rec = {"a": str(row.get("a")), "b": str(row.get("b")), "class": "float", "declared": True,
               "spec_ref": str(row.get("id") or ""), "tri_overlap_pairs": 0,
               "depth_mm": None, "depth_measured": False, "volume_mm3": None,
               "volume_reliable": False, "min_gap_mm": row.get("gap_mm"),
               "closed": {}, "normals_ok": {}, "severity": "unknown",
               "method": "manifest 接口接触验证",
               "reason": "接口 %s 两端网格间隙 %s mm > 容许 %s mm —— 没搭上"
                         % (row.get("id"), row.get("gap_mm"), row.get("limit_mm"))}
        pairs.append(rec)
        existing[key] = rec

    counts = {c: 0 for c in CLASSES}
    for rec in pairs:
        if rec.get("class") in counts and rec["class"] != "float":
            counts[rec["class"]] += 1
    counts["float"] = len(floaters)
    far = max(0, n * (n - 1) // 2 - total_avail)
    counts["gap_far"] = far
    counts["candidates"] = len(cands)
    counts["near_pairs"] = len(near)
    counts["tested"] = int(checked)
    counts["pairs_total"] = n * (n - 1) // 2

    sev_rank = {"high": 3, "medium": 2, "low": 1, "unknown": 0}
    offenders = [p for p in pairs if p.get("class") in ("clash", "contained")]
    offenders.sort(key=lambda p: (-sev_rank.get(p.get("severity") or "unknown", 0),
                                  -(p.get("depth_mm") or 0.0), -(p.get("volume_mm3") or 0.0),
                                  str(p.get("a")), str(p.get("b"))))
    unresolved_pairs = [p for p in pairs if p.get("class") == "unresolved"]
    hidden_ok = [p for p in pairs if p.get("class") == "hidden_ok"]
    contacts = [p for p in pairs if p.get("class") == "contact"]
    invalid_objs = sorted([nm for nm in names if not (md[nm]["closed"] and md[nm]["normals_ok"])])
    unresolved_objects = sorted(set(list(md_errors.keys()) + invalid_objs))
    unres_total = counts["unresolved"] + len(float_unresolved)
    bad = counts["clash"] + counts["contained"] + counts["float"]
    if bad:
        verdict = "fail"
    elif unres_total or truncated or budget_hit or unresolved_objects:
        verdict = "degraded"
    else:
        verdict = "pass"
    coverage = {"objects": n, "aabb_pairs": len(cands), "near_pairs": len(near),
                "tested": int(checked), "complete": bool(not truncated and not budget_hit and not unresolved_objects),
                "truncated": bool(truncated), "budget_hit": bool(budget_hit),
                "unresolved_objects": unresolved_objects[:60], "md_errors": md_errors}
    depths = [p.get("depth_mm") for p in pairs if p.get("depth_mm") is not None]
    gaps = [p.get("min_gap_mm") for p in pairs if p.get("min_gap_mm") is not None]
    repairs = []
    for p in offenders[:5]:
        why = ("完全包含（面无相交，旧全对全会漏）" if p.get("class") == "contained"
               else "穿透深度 %s mm / 体积 %s mm³ 超门限" % (p.get("depth_mm"), p.get("volume_mm3")))
        repairs.append({"op": "fix_interference",
                        "args": {"a": p.get("a"), "b": p.get("b"), "plan_only": True}, "why": why})
    for f in floaters[:5]:
        repairs.append({"op": "asm_manifest" if float_source == "manifest" else "audit_snap_floaters",
                        "args": ({"action": "add_interface", "note": "把浮空件接进连接图"} if float_source == "manifest"
                                 else {"objects": f.get("objects") or [f.get("object")], "dry_run": True}),
                        "why": f.get("why") or "装配浮空"})
    if float_source == "manifest" and any(u.get("reason") == "manifest_part_not_in_scene" for u in float_unresolved):
        repairs.append({"op": "asm_merge", "args": {"manifest": "<path>", "dry_run": True},
                        "why": "manifest 里有 part 在当前场景找不到 —— 合并漏件，先看 merge 计划"})
    spec_summary = {"allow_pairs": len(allow_list),
                    "allow": [{"a": a.get("a"), "b": a.get("b"),
                               "max_depth_mm": _num(a.get("max_depth_mm")),
                               "max_volume_mm3": _num(a.get("max_volume_mm3")),
                               "spec_ref": a.get("id") or a.get("reason")}
                              for a in allow_list[:20]],
                    "interfaces": len(esp["interfaces"]), "clearance": len(esp["clearance"])}
    manifest_summary = None
    if isinstance(manifest, dict):
        manifest_summary = {"assembly": manifest.get("assembly"), "sealed": bool(manifest.get("sealed")),
                            "manifest_md5": _manifest_md5(manifest),
                            "counts": _manifest_counts(manifest)}
    max_depth = max(depths) if depths else None
    min_gap = min(gaps) if gaps else None
    verdict_line = ("扫了 %d 件 / %d 对候选：clash=%d contained=%d hidden_ok=%d contact=%d gap=%d float=%d "
                    "unresolved=%d → %s" % (n, total_avail, counts["clash"], counts["contained"],
                                            counts["hidden_ok"], counts["contact"], counts["gap"],
                                            counts["float"], unres_total, verdict))
    out = {"ok": True, "verdict": verdict, "analyzed": True, "readonly": True,
           "objects": n, "object_names": names, "counts": counts,
           "coverage": coverage, "verdict_line": verdict_line,
           "thresholds": {"depth_eps_mm": _r(depth_eps_mm, 4), "contact_eps_mm": _r(contact_eps_mm, 4),
                          "report_gap_mm": _r(report_gap_mm, 4), "floor_mm3": _r(floor_mm3, 4),
                          "limit": int(limit), "max_pairs": int(max_pairs), "budget_ms": int(budget_ms)},
           "max_depth_mm": _r(max_depth, 4), "min_gap_mm": _r(min_gap, 4),
           "offenders": (offenders[:10] if summary_only else offenders[:60]),
           "floaters": (floaters[:10] if summary_only else floaters[:60]),
           "floating_unresolved": float_unresolved[:20], "float_source": float_source,
           "manifest_float_graph": graph,
           "repairs": repairs[:10], "spec": spec_summary, "manifest": manifest_summary,
           "contract": contract_meta,
           "honest_limits": [
               "深度/体积是采样式估计（低差异序列，非随机；contract 复用时为对方口径），同输入同输出",
               "完全包含：内/外靠射线奇偶；大件若是**空心壳**，小件被包住不等于材质干涉（本 op 按实心口径报 contained，需人核）",
               "开网格 / 法线朝内：三角面相交仍是精确证据，但深度与内外结论不可信 → 该对一律 unresolved",
               "接触面积不在全对全里算（成本控制）；gap 类是采样最近距离，平行贴合可能高估",
               "AABB 不相交的对子间隙是**下界**（真实只会更大）；> report_gap 的远对只计数不展开",
               "多壳且各壳朝向相反的件，按总体有符号体积可能漏判朝内（gate 另跑 audit_mesh 逐壳判）",
           ]}
    if not summary_only:
        out["pairs"] = pairs
        out["matrix"] = {"format": "sparse", "n": n, "index": list(names), "cells": matrix_cells,
                         "note": "只列被评估的关系（AABB 重叠 / report_gap 内近对）；远对按 counts.gap_far 计数"}
    else:
        out["pairs"] = []
        out["matrix"] = None
        out["slim"] = {"summary_only": True}
        out["counts_note"] = "counts.float = 浮空对象/接口数（不是 pair 数）；分类明细用 summary_only=false 看 pairs"
    if verdict == "degraded":
        out["warn"] = "DEGRADED ≠ 通过：有 unresolved / 截断 / 没判完的对象"
        out["hint"] = "先修网格/法线，或把预算调大（max_pairs / budget_ms）后重跑；未判完不许读成没问题"
    return out
# ────────────────────────────── ops：scan ──────────────────────────────
def asm_scan(scope=None, objects=None, file=None, manifest=None, spec=None, limit=0,
             max_pairs=20000, depth_eps_mm=0.2, contact_eps_mm=0.3, report_gap_mm=5.0,
             summary_only=False, use_contract=True, floor_mm3=1.0,
             include_hidden=False, budget_ms=0):
    """全对全穿模/包含/接触/间隙/浮空分类扫描（只读）。别名 audit_clash_all。"""
    t0 = time.perf_counter()
    try:
        depth_eps_mm = float(depth_eps_mm)
        contact_eps_mm = float(contact_eps_mm)
        report_gap_mm = float(report_gap_mm)
        floor_mm3 = float(floor_mm3)
    except Exception:
        return _err("阈值参数必须是数字（depth_eps_mm / contact_eps_mm / report_gap_mm / floor_mm3）")
    if depth_eps_mm < 0 or contact_eps_mm < 0 or report_gap_mm < 0 or floor_mm3 < 0:
        return _err("阈值参数不能为负", depth_eps_mm=depth_eps_mm, contact_eps_mm=contact_eps_mm,
                    report_gap_mm=report_gap_mm, floor_mm3=floor_mm3)
    m = None
    if manifest is not None:
        m, merr = _load_manifest(manifest)
        if merr:
            return _err(merr)
        val = _manifest_validate(m, check_files=False, check_md5=False)
        if not val["ok"]:
            return _err("manifest 结构校验失败（坏清单上不给结论）", errors=val["errors"][:10],
                        warnings=val["warnings"][:10], manifest_md5=val["manifest_md5"])
    res, err_res, cleanup, file_info = None, None, None, None
    try:
        if file:
            objs, file_info, cleanup = _load_file_objects(file, include_hidden=include_hidden)
            if file_info.get("error"):
                err_res = _err(file_info["error"], file=file_info)
            elif not objs:
                err_res = _err("file 里没有可用 mesh：%s" % file_info.get("path"))
            else:
                names = [str(o.name) for o in objs]
                res = _scan_core(objs, names, m, spec, limit, max_pairs, depth_eps_mm,
                                 contact_eps_mm, report_gap_mm, floor_mm3, summary_only,
                                 use_contract, include_hidden, None, budget_ms)
                if res.get("ok"):
                    res["source"] = {"kind": "file", "path": file_info.get("path")}
                    res["name_of"] = file_info.get("name_of") or {}
                    res["file_load"] = {k: file_info.get(k) for k in
                                        ("path", "loaded", "mesh", "non_mesh", "hidden_skipped", "missing")}
        else:
            info = _pick_objects(scope, objects, include_hidden)
            if info.get("error"):
                err_res = _err(info["error"], objects=info.get("names"), missing=info.get("missing"),
                               scopes_missing=info.get("scopes_missing"),
                               hint="确认对象名/集合名；空产出必须失败（不静默成功）")
            else:
                res = _scan_core(info["objects"], info["names"], m, spec, limit, max_pairs,
                                 depth_eps_mm, contact_eps_mm, report_gap_mm, floor_mm3,
                                 summary_only, use_contract, include_hidden, None, budget_ms)
                res["source"] = {"kind": "session",
                                 "scope": scope if not isinstance(scope, dict) else scope.get("scope")}
                res["hidden_skipped"] = info.get("hidden_skipped", 0)
    except Exception as e:
        import traceback
        err_res = _err("scan 异常: %s: %s" % (type(e).__name__, str(e)[:240]),
                       traceback=traceback.format_exc()[-600:])
    finally:
        cr = cleanup() if cleanup is not None else None
    if err_res is not None:
        return err_res
    if cr is not None:
        res["cleanup"] = cr
    return _j(_receipt(res, t0))
# ────────────────────────────── ops：manifest ──────────────────────────────
def asm_manifest(action="show", path=None, assembly=None, units="mm", coord=None, part=None,
                 interfaces=None, interface=None, allow=None, allow_pair=None,
                 manifest=None, force=False, compute_md5=True, check_files=True, check_md5=True,
                 note=None):
    """零件清单：init | add_part | add_interface | add_allow | validate | seal | show。"""
    t0 = time.perf_counter()
    act = str(action or "show").strip().lower()
    if act == "init":
        m = _manifest_init(assembly, units, coord)
        if isinstance(manifest, dict):
            for k in ("schema", "assembly", "units", "coord", "parts", "interfaces", "allow_pairs"):
                if k in manifest:
                    m[k] = manifest[k]
        if note:
            m["note"] = str(note)
        if not path:
            return _j(_receipt({"ok": True, "action": "init", "manifest": m,
                                "manifest_md5": _manifest_md5(m), "path": None,
                                "note": "没给 path ⇒ 只返回骨架，没有落盘"}, t0))
        if os.path.isfile(_path(path)) and not force:
            return _err("manifest 已存在，拒绝覆盖（force=true 才覆盖）: %s" % _path(path))
        val = _manifest_validate(m, check_files=False, check_md5=False)
        if not val["ok"]:
            return _err("init 清单自校验失败", errors=val["errors"], warnings=val["warnings"])
        try:
            p = _write_json(path, m)
        except Exception as e:
            return _err("manifest 写盘失败: %s: %s" % (type(e).__name__, str(e)[:160]))
        return _j(_receipt({"ok": True, "action": "init", "path": p, "manifest_md5": _manifest_md5(m),
                            "counts": _manifest_counts(m), "warnings": val["warnings"],
                            "next": "add_part → add_interface → add_allow(可选) → validate → seal"}, t0))
    src = manifest if manifest is not None else path
    m, merr = _load_manifest(src)
    if merr:
        return _err(merr)
    if act == "validate":
        val = _manifest_validate(m, check_files=bool(check_files), check_md5=bool(check_md5))
        d = {"ok": bool(val["ok"]), "action": "validate", "verdict": val["verdict"],
             "path": _path(path) if path else None, "manifest_md5": val["manifest_md5"],
             "errors": val["errors"], "warnings": val["warnings"],
             "counts": val["counts"], "sealed": val["sealed"], "units": val["units"]}
        if not val["ok"]:
            d["hint"] = "先修 errors；manifest 自身不合法时 merge/gate 会拒绝出结论"
        return _j(_receipt(d, t0))
    if act == "show":
        val = _manifest_validate(m, check_files=False, check_md5=False)
        return _j(_receipt({"ok": bool(val["ok"]), "action": "show", "path": _path(path) if path else None,
                            "manifest": m, "manifest_md5": _manifest_md5(m),
                            "counts": _manifest_counts(m), "sealed": bool(m.get("sealed")),
                            "validation": {"verdict": val["verdict"],
                                           "errors": val["errors"][:20], "warnings": val["warnings"][:20]}},
                           t0))
    if act not in ("add_part", "add_interface", "add_allow", "seal"):
        return _err("未知 action: %s（支持 init|add_part|add_interface|add_allow|validate|seal|show）" % act)
    if not path:
        return _err("action=%s 必须给 path（要写回清单文件）" % act)
    if act == "seal":
        val = _manifest_validate(m, check_files=bool(check_files), check_md5=bool(check_md5))
        if not val["ok"]:
            return _err("seal 拒绝：清单还有 %d 个 error，先修" % len(val["errors"]),
                        errors=val["errors"][:20], warnings=val["warnings"][:20])
        if m.get("sealed") and not force:
            return _err("清单已经 sealed（force=true 才重封）", sealed_md5=m.get("sealed_md5"))
        try:
            m["version"] = int(m.get("version") or 0) + 1
        except Exception:
            m["version"] = 2
        m["sealed"] = True
        m["sealed_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
        m["sealed_md5"] = _manifest_md5(m)
        if note:
            m["note"] = str(note)
        try:
            p = _write_json(path, m)
        except Exception as e:
            return _err("seal 写盘失败: %s: %s" % (type(e).__name__, str(e)[:160]))
        return _j(_receipt({"ok": True, "action": "seal", "path": p, "sealed": True,
                            "version": m["version"], "sealed_md5": m["sealed_md5"],
                            "manifest_md5": _manifest_md5(m), "counts": _manifest_counts(m)}, t0))
    if act == "add_part":
        p = dict(part) if isinstance(part, dict) else None
        if p is None:
            return _err("add_part 需要 part={id,file,objects,transform,interface_frames}")
        pid = str(p.get("id") or "")
        if not pid:
            return _err("part.id 必填")
        if any(isinstance(x, dict) and str(x.get("id")) == pid for x in (m.get("parts") or [])):
            return _err("part id 重复: %s" % pid)
        fp = p.get("file")
        if not fp:
            return _err("part.file 必填（merge 要知道从哪 append）")
        f = _path(fp)
        if not os.path.isfile(f):
            return _err("part.file 不存在: %s" % f)
        if compute_md5 or not p.get("file_md5"):
            got = _sha256_file(f)
            if got is None:
                return _err("part.file 读不了，算不出 md5: %s" % f)
            p["file_md5"] = got
        p.setdefault("transform", {"loc_mm": [0, 0, 0], "rot_euler_deg": [0, 0, 0], "scale": [1, 1, 1]})
        m.setdefault("parts", []).append(p)
        val = _manifest_validate(m, check_files=True, check_md5=False)
        if not val["ok"]:
            m["parts"].pop()
            return _err("add_part 校验失败（未写入）", errors=val["errors"][:20], warnings=val["warnings"][:20])
        try:
            wp = _write_json(path, m)
        except Exception as e:
            return _err("manifest 写盘失败: %s: %s" % (type(e).__name__, str(e)[:160]))
        return _j(_receipt({"ok": True, "action": "add_part", "part": pid, "path": wp,
                            "file_md5": p.get("file_md5"), "manifest_md5": _manifest_md5(m),
                            "counts": _manifest_counts(m), "warnings": val["warnings"][:20]}, t0))
    if act == "add_interface":
        items = []
        if isinstance(interfaces, dict):
            items.append(dict(interfaces))
        elif isinstance(interfaces, (list, tuple)):
            items.extend([dict(x) for x in interfaces if isinstance(x, dict)])
        if isinstance(interface, dict):
            items.append(dict(interface))
        if not items:
            return _err("add_interface 需要 interface={id,a,b,frame_a,frame_b,fit,nominal_mm,tol_mm,allow_overlap_mm}")
        cur = m.setdefault("interfaces", [])
        added = []
        for it in items:
            it.setdefault("id", "IF-%d" % (len(cur) + 1))
            if any(isinstance(x, dict) and str(x.get("id")) == str(it.get("id")) for x in cur):
                return _err("interface id 重复: %s" % it.get("id"))
            cur.append(it)
            added.append(str(it.get("id")))
        val = _manifest_validate(m, check_files=True, check_md5=False)
        if not val["ok"]:
            for _ in added:
                cur.pop()
            return _err("add_interface 校验失败（未写入）", errors=val["errors"][:20], warnings=val["warnings"][:20])
        try:
            wp = _write_json(path, m)
        except Exception as e:
            return _err("manifest 写盘失败: %s: %s" % (type(e).__name__, str(e)[:160]))
        return _j(_receipt({"ok": True, "action": "add_interface", "added": added, "path": wp,
                            "manifest_md5": _manifest_md5(m), "counts": _manifest_counts(m),
                            "warnings": val["warnings"][:20]}, t0))
    # add_allow
    items = []
    for src_item in (allow, allow_pair):
        if isinstance(src_item, dict):
            items.append(dict(src_item))
        elif isinstance(src_item, (list, tuple)):
            items.extend([dict(x) for x in src_item if isinstance(x, dict)])
    if not items:
        return _err("add_allow 需要 allow={a,b,max_depth_mm,max_volume_mm3,reason}（或列表）")
    cur = m.setdefault("allow_pairs", [])
    added = []
    for it in items:
        it.setdefault("id", "ALLOW-%d" % (len(cur) + 1))
        if any(isinstance(x, dict) and str(x.get("id")) == str(it.get("id")) for x in cur):
            return _err("allow id 重复: %s" % it.get("id"))
        cur.append(it)
        added.append(str(it.get("id")))
    val = _manifest_validate(m, check_files=True, check_md5=False)
    if not val["ok"]:
        for _ in added:
            cur.pop()
        return _err("add_allow 校验失败（未写入）", errors=val["errors"][:20], warnings=val["warnings"][:20])
    try:
        wp = _write_json(path, m)
    except Exception as e:
        return _err("manifest 写盘失败: %s: %s" % (type(e).__name__, str(e)[:160]))
    return _j(_receipt({"ok": True, "action": "add_allow", "added": added, "path": wp,
                        "manifest_md5": _manifest_md5(m), "counts": _manifest_counts(m),
                        "warnings": val["warnings"][:20]}, t0))
# ────────────────────────────── merge ──────────────────────────────
def _transform_matrix(tr, policy, mmu):
    """manifest transform（loc_mm / rot_euler_deg / scale）→ 4×4（mm→Blender 单位）。"""
    if str(policy) == "as_is" or not isinstance(tr, dict):
        return Matrix.Identity(4)
    loc = [_num(x) or 0.0 for x in (_as_list(tr.get("loc_mm")) or [0, 0, 0])][:3]
    while len(loc) < 3:
        loc.append(0.0)
    rot = [_num(x) or 0.0 for x in (_as_list(tr.get("rot_euler_deg")) or [0, 0, 0])][:3]
    while len(rot) < 3:
        rot.append(0.0)
    sc = [_num(x) or 1.0 for x in (_as_list(tr.get("scale")) or [1, 1, 1])][:3]
    while len(sc) < 3:
        sc.append(1.0)
    T = Matrix.Translation(Vector([float(x) / mmu for x in loc]))
    R = Euler([math.radians(float(x)) for x in rot], "XYZ").to_matrix().to_4x4()
    S = Matrix.Diagonal((float(sc[0]), float(sc[1]), float(sc[2]), 1.0))
    return T @ R @ S


def _frame_origin(part, fid):
    for fr in (part.get("interface_frames") or []) if isinstance(part, dict) else []:
        if isinstance(fr, dict) and str(fr.get("id")) == str(fid):
            vals = [_num(x) or 0.0 for x in (_as_list(fr.get("origin_mm")) or [0, 0, 0])][:3]
            while len(vals) < 3:
                vals.append(0.0)
            return vals
    return None


def _plan_part_names(pid, requested, prefix_fmt):
    pre = str(prefix_fmt).replace("<partid>", pid).replace("{partid}", pid)
    return {orig: (pre + orig) for orig in requested}


def _merge_cleanup(objects, coll_name="ASM_MERGED"):
    removed = []
    for ob in objects:
        try:
            nm = str(ob.name)
            bpy.data.objects.remove(ob, do_unlink=True)
            removed.append(nm)
        except Exception:
            pass
    coll = bpy.data.collections.get(coll_name)
    if coll is not None and len(coll.objects) == 0:
        try:
            bpy.data.collections.remove(coll, do_unlink=True)
        except Exception:
            pass
    return removed


def _recalc_normals(objs):
    import bmesh
    rows = []
    for ob in objs:
        if ob.type != "MESH" or ob.data is None:
            continue
        row = {"object": str(ob.name), "recalced": False, "open_shell": None}
        try:
            bm = bmesh.new()
            bm.from_mesh(ob.data)
            boundary = int(sum(1 for e in bm.edges if len(e.link_faces) == 1))
            row["open_shell"] = bool(boundary > 0)
            try:
                row["volume_before"] = _r(float(bm.calc_volume(signed=True)), 6)
            except Exception:
                row["volume_before"] = None
            if boundary > 0:
                row["skipped"] = "开放壳（边界边 %d）不 recalc，避免把开口方向硬拧" % boundary
            else:
                bmesh.ops.recalc_face_normals(bm, faces=list(bm.faces))
                bm.to_mesh(ob.data)
                ob.data.update()
                row["recalced"] = True
                try:
                    row["volume_after"] = _r(float(bm.calc_volume(signed=True)), 6)
                except Exception:
                    row["volume_after"] = None
            bm.free()
        except Exception as e:
            row["error"] = "%s: %s" % (type(e).__name__, str(e)[:160])
        rows.append(row)
    return rows


def asm_merge(manifest=None, out_blend=None, dry_run=True, transform_policy="manifest",
              snap_tol_mm=2.0, recalc_normals=True, snapshot=True, gate=True,
              gate_spec=None, strict=True, verify_files=True, verify_md5=True,
              name_prefix=None, include_hidden=False):
    """N 路合并：按 part id 字典序 append → 改名 → 变换 → recalc normals → 自动 gate → 写盘+回执。

    dry_run=true（默认）只回计划，不 append / 不写盘 / 不跑 gate；gate 不过不保存。
    """
    t0 = time.perf_counter()
    policy = str(transform_policy or "manifest")
    if policy not in ("manifest", "as_is", "snap_interface"):
        return _err("transform_policy 只支持 manifest|as_is|snap_interface，收到 %r" % policy)
    m, merr = _load_manifest(manifest)
    if merr:
        return _err(merr)
    val = _manifest_validate(m, check_files=bool(verify_files), check_md5=bool(verify_md5))
    if not val["ok"]:
        return _err("manifest 校验失败，拒绝合并（先修清单/文件）", errors=val["errors"][:20],
                    warnings=val["warnings"][:20], manifest_md5=val["manifest_md5"])
    mmu = float(_KIT.units())
    mdig = _manifest_md5(m)
    parts = [p for p in (m.get("parts") or []) if isinstance(p, dict)]
    if not parts:
        return _err("manifest 没有 parts —— 没东西可合并")
    order = sorted(parts, key=lambda p: str(p.get("id") or ""))
    prefix_fmt = str(name_prefix or "<partid>__")
    plan, warn_objects = [], []
    for p in order:
        pid = str(p.get("id") or "")
        f = _path(p.get("file"))
        if not f or not os.path.isfile(f):
            return _err("part %s 的文件不存在: %s" % (pid, f))
        try:
            with bpy.data.libraries.load(f, link=False) as (data_from, _data_to):
                all_names = [str(x) for x in data_from.objects]
        except Exception as e:
            return _err("part %s 的 .blend 打不开: %s: %s" % (pid, type(e).__name__, str(e)[:160]))
        want = _as_list(p.get("objects")) or list(all_names)
        missing = [x for x in want if x not in all_names]
        if missing:
            warn_objects.append({"part": pid, "missing_objects": missing[:10],
                                 "why": "manifest 列的 objects 在文件里没有（按实际存在的继续）"})
        want = [x for x in want if x in all_names]
        M = _transform_matrix(p.get("transform"), policy, mmu)
        plan.append({"part": pid, "file": f, "requested": want, "missing": missing,
                     "matrix": M, "owner": p.get("owner"),
                     "target_names": _plan_part_names(pid, want, prefix_fmt),
                     "manifest_transform": p.get("transform")})
    snap_notes, snapped = [], set()
    if policy == "snap_interface":
        for it in sorted([x for x in (m.get("interfaces") or []) if isinstance(x, dict)],
                         key=lambda x: str(x.get("id") or "")):
            a, b = str(it.get("a") or ""), str(it.get("b") or "")
            pa = next((p for p in order if str(p.get("id")) == a), {})
            pb = next((p for p in order if str(p.get("id")) == b), {})
            oa = _frame_origin(pa, it.get("frame_a")) if it.get("frame_a") else None
            ob = _frame_origin(pb, it.get("frame_b")) if it.get("frame_b") else None
            if oa is None or ob is None:
                snap_notes.append({"interface": it.get("id"), "state": "unresolved",
                                   "why": "缺 frame_a/frame_b 或 origin_mm，无法求解接口帧"})
                continue
            va = next((p for p in plan if p["part"] == a), None)
            vb = next((p for p in plan if p["part"] == b), None)
            if va is None or vb is None:
                snap_notes.append({"interface": it.get("id"), "state": "unresolved",
                                   "why": "接口端不在合并计划里"})
                continue
            if b in snapped:
                snap_notes.append({"interface": it.get("id"), "state": "conflict",
                                   "why": "part %s 已被别的接口对齐过（多约束冲突，未硬拧）" % b})
                continue
            wa = va["matrix"] @ Vector([x / mmu for x in oa])
            wb = vb["matrix"] @ Vector([x / mmu for x in ob])
            delta = wa - wb
            delta_mm = float(delta.length) * mmu
            if delta_mm <= float(snap_tol_mm) + 1e-9:
                vb["matrix"] = Matrix.Translation(delta) @ vb["matrix"]
                snapped.add(b)
                snap_notes.append({"interface": it.get("id"), "state": "snapped", "delta_mm": _r(delta_mm, 4)})
            else:
                snap_notes.append({"interface": it.get("id"), "state": "unresolved", "delta_mm": _r(delta_mm, 4),
                                   "why": "接口帧偏差 %.3f mm > snap_tol_mm %.3f → 不硬拧（unresolved）"
                                          % (delta_mm, float(snap_tol_mm))})
    plan_out = [{"part": p["part"], "file": p["file"], "objects": p["requested"],
                 "objects_missing": p["missing"], "target_names": p["target_names"],
                 "transform": {"policy": policy,
                               "loc_mm": (p["manifest_transform"] or {}).get("loc_mm")
                               if isinstance(p["manifest_transform"], dict) else [0, 0, 0]}}
                for p in plan]
    objs_total = sum(len(p["requested"]) for p in plan)
    if dry_run:
        d = {"ok": True, "verdict": "planned", "dry_run": True, "readonly": True,
             "manifest_md5": mdig, "out_blend": _path(out_blend) if out_blend else None,
             "order": [p["part"] for p in plan], "parts": plan_out,
             "objects_total": objs_total, "object_warnings": warn_objects,
             "interfaces": {"count": len(m.get("interfaces") or []), "snap": snap_notes},
             "outputs": {"out_blend": _path(out_blend) if out_blend else None, "written": False},
             "gate": "skipped_dry_run",
             "note": "dry_run：只回计划（append 顺序/目标对象名/变换），不 append、不写盘、不跑 gate"}
        return _j(_receipt(d, t0))
    if not out_blend:
        return _err("dry_run=false 必须给 out_blend（合并结果要落盘）")
    snap = None
    if snapshot:
        txn = _api("txn")
        if txn is None:
            snap = {"ok": False, "skipped": True,
                    "note": "K.dsh_txn_api 未加载 ⇒ 没做文件级快照（合并失败只能靠不保存回退）"}
        else:
            rec, serr = _call_api("txn", "snapshot",
                                  {"label": "asm_merge-%s" % str(mdig)[:12], "note": "asm_merge 前快照"})
            if serr:
                return _err("快照失败，拒绝在无回退点的场景上合并：%s" % serr)
            snap = rec.get("snapshot") if isinstance(rec, dict) else rec
    coll = bpy.data.collections.get("ASM_MERGED")
    if coll is None:
        coll = bpy.data.collections.new("ASM_MERGED")
        bpy.context.scene.collection.children.link(coll)
    merged, added_objs = [], []
    try:
        for item in plan:
            with bpy.data.libraries.load(item["file"], link=False) as (data_from, data_to):
                data_to.objects = list(item["requested"])
            got = [o for o in (getattr(data_to, "objects", None) or []) if o is not None]
            if not got:
                raise RuntimeError("part %s append 回来 0 个对象" % item["part"])
            for idx, ob in enumerate(got):
                orig = item["requested"][idx] if idx < len(item["requested"]) else str(ob.name)
                target = item["target_names"].get(orig) or "%s%s" % (item["part"] + "__", orig)
                try:
                    ob.name = target
                except Exception:
                    pass
                try:
                    ob["asm.part"] = item["part"]
                    ob["asm.manifest_md5"] = str(mdig)
                    if item.get("owner"):
                        ob["dsh.owner"] = str(item["owner"])
                except Exception:
                    pass
                ob.matrix_world = item["matrix"] @ ob.matrix_world
                try:
                    coll.objects.link(ob)
                except Exception:
                    pass
                added_objs.append(ob)
                merged.append({"part": item["part"], "object": str(ob.name),
                               "source_object": str(orig), "file": item["file"]})
        bpy.context.view_layer.update()
        normals = _recalc_normals(added_objs) if recalc_normals else {"skipped": True}
        gate_rec = None
        if gate:
            gspec = dict(gate_spec) if isinstance(gate_spec, dict) else {}
            names = [x["object"] for x in merged]
            raw = asm_gate(scope=None, objects=names, manifest=m, spec=gspec, summary_only=True,
                           strict=bool(strict), include_hidden=include_hidden)
            gate_rec = json.loads(raw) if isinstance(raw, str) else raw
            if not isinstance(gate_rec, dict) or gate_rec.get("verdict") != "pass":
                return _j(_receipt({
                    "ok": False, "verdict": (gate_rec or {}).get("verdict") if isinstance(gate_rec, dict) else "unresolved",
                    "stage": "gate", "saved": False, "dry_run": False,
                    "error": "合并结果没通过 gate ⇒ 不写盘（失败不保存）",
                    "gate": gate_rec, "merged": merged, "snapshot": snap, "normals": normals,
                    "hint": "场景已被本次 append 改动；用 txn.restore(<snapshot label>) 回退，或修 manifest/几何后重跑",
                }, t0))
        p_out = _path(out_blend)
        d_out = os.path.dirname(p_out)
        if d_out and not os.path.isdir(d_out):
            os.makedirs(d_out, exist_ok=True)
        bpy.ops.wm.save_as_mainfile(filepath=p_out, copy=True)
        blend_md5 = _sha256_file(p_out)
        rec_path = (p_out[:-6] + ".merge_receipt.json") if p_out.lower().endswith(".blend") \
            else (p_out + ".merge_receipt.json")
        gate_digest = _md5_text(_canon({k: v for k, v in (gate_rec or {}).items()
                                        if k not in ("ms", "receipt_md5")})) if gate_rec else None
        receipt = {"schema": "dsh-assembly-merge-receipt/1", "assembly": m.get("assembly"),
                   "version": ASSEMBLY_VERSION, "manifest_md5": mdig,
                   "sealed": bool(m.get("sealed")), "out_blend": p_out, "blend_md5": blend_md5,
                   "parts": [{"part": p["part"], "file": p["file"],
                              "file_md5": next((x.get("file_md5") for x in parts
                                                if str(x.get("id")) == p["part"]), None)} for p in plan],
                   "order": [p["part"] for p in plan], "objects": merged,
                   "transforms": plan_out, "interfaces": {"snap": snap_notes},
                   "normals": normals, "gate": gate_rec, "gate_md5": gate_digest,
                   "snapshot": snap, "created": time.strftime("%Y-%m-%d %H:%M:%S"),
                   "units": {"mm_per_unit": _r(mmu, 6)}}
        try:
            _write_json(rec_path, receipt)
        except Exception as e:
            return _j(_receipt({"ok": False, "verdict": "pass", "stage": "receipt",
                                "error": "blend 已保存但 merge_receipt.json 写失败: %s: %s"
                                         % (type(e).__name__, str(e)[:160]),
                                "outputs": {"blend": p_out, "md5": blend_md5, "receipt": rec_path},
                                "gate": gate_rec}, t0))
        return _j(_receipt({"ok": True, "verdict": "pass", "dry_run": False, "stage": "done",
                            "manifest_md5": mdig, "merged": merged,
                            "objects_total": len(merged), "order": [p["part"] for p in plan],
                            "transform_policy": policy, "interfaces": {"snap": snap_notes},
                            "normals": normals, "gate": gate_rec, "snapshot": snap,
                            "object_warnings": warn_objects,
                            "outputs": {"blend": p_out, "md5": blend_md5, "receipt": rec_path,
                                        "receipt_md5": _sha256_file(rec_path)},
                            "note": "成功路径：blend + merge_receipt.json 都写了；gate 不过的路径不会写盘"},
                           t0))
    except Exception as e:
        removed = _merge_cleanup(added_objs)
        import traceback
        return _j(_receipt({"ok": False, "stage": "append_or_merge",
                            "error": "%s: %s" % (type(e).__name__, str(e)[:240]),
                            "cleaned_objects": removed, "snapshot": snap,
                            "traceback": traceback.format_exc()[-600:],
                            "hint": "失败不保存；已尽力删掉本次 append 的对象（快照 label 见 snapshot）"}, t0))
# ────────────────────────────── gate 辅助 ──────────────────────────────
def _agg_states(states):
    """整包三态（与 gate v3 同口径）：任一 fail ⇒ fail；否则任一 degraded/skipped ⇒ degraded；否则 pass。"""
    if any(s in ("fail", "error") for s in states):
        return "fail"
    if any(s in ("degraded", "skipped") for s in states):
        return "degraded"
    if not any(s == "pass" for s in states):
        return "degraded"
    return "pass"


def _trim(rec, depth=0):
    """回执瘦身：只留判据字段，明细截短（summary_only 用）。"""
    if not isinstance(rec, dict) or depth > 2:
        return rec
    keep = ("ok", "state", "verdict", "reason", "warn", "error", "counts", "coverage", "totals",
            "clean", "real_floater_count", "visible_floater_count", "unconfirmed_floater_count",
            "micro_floater_count", "floater_count", "manifest_md5", "sealed", "version",
            "trend", "max_depth_mm", "min_gap_mm", "export_allowed", "analyzed")
    out = {}
    for k in keep:
        if k in rec:
            out[k] = _trim(rec[k], depth + 1) if isinstance(rec[k], (dict, list)) else rec[k]
    for k in ("offenders", "floaters", "pairs", "deviations", "objects", "errors", "warnings"):
        v = rec.get(k)
        if isinstance(v, list):
            out[k] = [_trim(x, depth + 1) for x in v[:5]]
            out.setdefault(k + "_total", len(v))
        elif isinstance(v, dict):
            out[k] = _trim(v, depth + 1)
    return out


def _mesh_gate(names, strict):
    rec, err = _call_api("audit", "mesh", {"objects": list(names), "summary_only": True,
                                           "self_intersect": bool(strict)})
    if err:
        return {"state": "degraded", "mode": "audit_mesh_unavailable", "error": err,
                "reason": "mesh 门没跑成（audit 未 preload？）—— 未分析不得读作通过"}
    if not isinstance(rec, dict):
        return {"state": "degraded", "mode": "audit_mesh_bad_receipt", "error": "回执不是 dict"}
    raw = rec.get("state") or ("pass" if rec.get("clean") else "fail")
    state = raw if raw in ("pass", "fail", "degraded") else "degraded"
    return {"state": state, "mode": "audit_mesh", "receipt": _trim(rec),
            "totals": rec.get("totals") or {},
            "objects": [{k: o.get(k) for k in ("name", "state", "normals_state", "normals_outward",
                                              "boundary_edges", "nonmanifold_edges",
                                              "degenerate_faces", "self_intersections", "closed")}
                        for o in (rec.get("objects") or []) if isinstance(o, dict)][:40],
            "reason": rec.get("reason") or ""}


def _normals_gate(mesh_gate):
    """法线门：任一闭合件朝内 ⇒ fail；判不出来（unknown / 没有闭合件）⇒ degraded（绝不假通过）。"""
    if mesh_gate.get("mode") != "audit_mesh":
        return {"state": "degraded", "reason": "mesh 门没拿到 audit_mesh 回执，法线判不了",
                "inverted": [], "unknown": [], "checked": 0}
    totals = mesh_gate.get("totals") or {}
    inv = int(totals.get("normals_inverted") or 0)
    unk = int(totals.get("normals_unknown") or 0)
    chk = int(totals.get("normals_checked") or 0)
    rows = mesh_gate.get("objects") or []
    inverted = [o.get("name") for o in rows if o.get("normals_outward") is False]
    unknown = [o.get("name") for o in rows if o.get("normals_state") == "unknown"]
    if inv:
        return {"state": "fail", "inverted": inverted, "unknown": unknown, "checked": chk,
                "reason": "%d 个对象法线朝内（闭合件 signed volume 为负）—— 穿模/内外判据的前提被破坏" % inv}
    if unk or chk == 0:
        return {"state": "degraded", "inverted": inverted, "unknown": unknown, "checked": chk,
                "reason": "法线判不出来（unknown=%d / checked=%d）—— 未分析不得读作通过" % (unk, chk)}
    return {"state": "pass", "inverted": [], "unknown": [], "checked": chk,
            "reason": "闭合件法线全部朝外（%d 个已判）" % chk}


def _scan_summary(scan):
    s = _trim(scan)
    s["counts"] = scan.get("counts")
    s["coverage"] = scan.get("coverage")
    s["verdict"] = scan.get("verdict")
    s["state"] = scan.get("verdict")
    s["max_depth_mm"] = scan.get("max_depth_mm")
    s["offenders"] = scan.get("offenders") or []
    s["floaters"] = scan.get("floaters") or []
    s["contract"] = scan.get("contract")
    return s


def _measure_gate(m, spec, md, names, mmu, tolerance_mm, contact_eps_mm):
    """measure 门：manifest 接口 + spec.clearance 的期望值 vs 求值网格实测间隙（bbox 值作旁证）。"""
    items = []
    if isinstance(m, dict):
        for it in m.get("interfaces") or []:
            if isinstance(it, dict):
                items.append(("interface", it))
    sp = dict(spec) if isinstance(spec, dict) else {}
    for c in sp.get("clearance") or []:
        if isinstance(c, dict):
            items.append(("clearance", c))
    if not items:
        return {"state": "skipped", "deviations": [],
                "reason": "没有接口/间隙期望（manifest 没 interfaces，spec 没 clearance）—— 未分析不得读作通过"}
    obj_part = _obj_part_map(m, names)
    am, aerr = _call_api("audit", "measure",
                         {"objects": list(names),
                          "max_pairs": int(max(1, min(2000, len(names) * max(1, len(names) - 1) // 2)))})
    am_pairs = {}
    if isinstance(am, dict) and am.get("ok"):
        for p in am.get("pairs") or []:
            if isinstance(p, dict):
                am_pairs[_pair_key(p.get("a"), p.get("b"))] = p
    deviations, n_unres = [], 0

    def _objects_of(side):
        side = str(side or "")
        out = [n for n in names if obj_part.get(n) == side]
        if not out and side in names:
            out = [side]
        return out

    for kind, it in items:
        a, b = str(it.get("a") or ""), str(it.get("b") or "")
        oa, ob = _objects_of(a), _objects_of(b)
        dev = {"kind": kind, "id": it.get("id"), "a": a, "b": b}
        if not oa or not ob:
            dev.update({"state": "unresolved", "why": "端对象不在当前场景（a=%d b=%d 个对象）" % (len(oa), len(ob))})
            deviations.append(dev)
            n_unres += 1
            continue
        best = None
        for na in oa:
            for nb in ob:
                if na in md and nb in md:
                    g = _surface_gap(md[na], md[nb], samples=400)
                    if g is not None:
                        best = g if best is None else min(best, g)
        if best is None:
            dev.update({"state": "unresolved", "why": "两端量不到表面距离"})
            deviations.append(dev)
            n_unres += 1
            continue
        gap_mm = best * mmu
        dp = am_pairs.get(_pair_key(a, b)) or am_pairs.get(_pair_key(oa[0], ob[0])) or {}
        dev["measured_gap_mm"] = _r(gap_mm, 4)
        dev["bbox_gap_mm"] = dp.get("gap_mm")
        if kind == "interface":
            nom = _num(it.get("nominal_mm"))
            nom = 0.0 if nom is None else nom
            tol = _num(it.get("tol_mm"))
            tol = float(tolerance_mm) if tol is None else tol
            ov = _num(it.get("allow_overlap_mm"))
            if ov is not None and ov > 0:
                tol = max(tol, float(ov), float(contact_eps_mm))
            dev.update({"expected_mm": _r(nom, 4), "tol_mm": _r(tol, 4),
                        "deviation_mm": _r(gap_mm - nom, 4)})
            ok = abs(gap_mm - nom) <= tol + 1e-9
            dev["state"] = "pass" if ok else "fail"
            dev["why"] = ("接口 %s 实测单边间隙 %.3f mm，期望 %.3f ± %.3f mm"
                          % (it.get("id"), gap_mm, nom, tol))
        else:
            min_mm = _num(it.get("min_mm")) or 0.0
            dev.update({"min_mm": _r(min_mm, 4), "clearance_mm": _r(gap_mm, 4)})
            ok = gap_mm >= min_mm - 1e-9
            dev["state"] = "pass" if ok else "fail"
            dev["why"] = ("间隙规则 %s 实测 %.3f mm，要求 ≥ %.3f mm"
                          % (it.get("id") or it.get("a"), gap_mm, min_mm))
        deviations.append(dev)
    states = [d.get("state") for d in deviations]
    if "fail" in states:
        state = "fail"
    elif n_unres:
        state = "degraded"
    else:
        state = "pass"
    return {"state": state, "deviations": deviations,
            "audit_measure": {"used": bool(am_pairs), "error": aerr, "pairs_bbox": len(am_pairs)},
            "reason": ("%d 个期望全部在公差内" % len(deviations)) if state == "pass"
                      else ("有 %d 个期望超公差" % states.count("fail") if state == "fail"
                            else "%d 个期望没量成（未判完）" % n_unres)}


def _severity_of_pair(p):
    d = p.get("depth_mm")
    v = p.get("volume_mm3")
    if p.get("class") == "contained":
        return "high" if (v or 0) > 1000 else "medium"
    if d is not None and d > 2.0:
        return "high"
    if (d is not None and d > 0.5) or (v is not None and v > 500):
        return "medium"
    return "low"
# ────────────────────────────── ops：gate ──────────────────────────────
def asm_gate(scope=None, objects=None, manifest=None, spec=None, require=None,
             summary_only=True, shots=False, strict=True, depth_eps_mm=0.2,
             contact_eps_mm=0.3, report_gap_mm=5.0, max_pairs=20000,
             tolerance_mm=0.5, include_hidden=False, use_contract=True, budget_ms=0):
    """装配出厂门：mesh + normals + scan(clash/contained) + floaters + interfaces + measure → 三态。

    export_allowed 仅当 verdict=pass；任何 skipped/degraded/unresolved 都不算通过。
    """
    t0 = time.perf_counter()
    default_req = {"mesh_health": True, "normals": True, "clash": True, "floaters": True,
                   "interfaces": True, "measure": True}
    if isinstance(require, (list, tuple, set)):
        req = {k: (k in require) for k in default_req}
    elif isinstance(require, dict):
        req = dict(default_req)
        for k, v in require.items():
            if k in default_req:
                req[k] = bool(v)
            else:
                return _err("require 里不认识的键: %r" % k, allowed=sorted(default_req))
    else:
        req = dict(default_req)
    m = None
    if manifest is not None:
        m, merr = _load_manifest(manifest)
        if merr:
            return _err(merr)
        mval = _manifest_validate(m, check_files=False, check_md5=False)
        if not mval["ok"]:
            return _err("manifest 结构校验失败，gate 拒绝在坏清单上出结论",
                        errors=mval["errors"][:20], warnings=mval["warnings"][:20])
    info = _pick_objects(scope, objects, include_hidden)
    if info.get("error"):
        return _err(info["error"], objects=info.get("names"), missing=info.get("missing"),
                    hint="空产出/坏参数必须失败；确认对象名/集合名")
    names = list(info["names"])
    mmu = float(_KIT.units())
    md, md_errors = _mesh_map(info["objects"])
    gates = []
    mesh_g = None
    if req["mesh_health"]:
        mesh_g = _mesh_gate(names, bool(strict))
        gates.append({"id": "mesh_health", "state": mesh_g["state"],
                      "reason": mesh_g.get("reason") or "",
                      "receipt": {"mode": mesh_g.get("mode"), "totals": mesh_g.get("totals"),
                                  "error": mesh_g.get("error")} if summary_only else mesh_g})
    else:
        gates.append({"id": "mesh_health", "state": "disabled", "reason": "require.mesh_health=false"})
    if req["normals"]:
        if mesh_g is None:
            mesh_g = _mesh_gate(names, bool(strict))
        norm_g = _normals_gate(mesh_g)
        gates.append({"id": "normals", "state": norm_g["state"], "inverted": norm_g.get("inverted"),
                      "unknown": norm_g.get("unknown"), "checked": norm_g.get("checked"),
                      "reason": norm_g.get("reason")})
    else:
        gates.append({"id": "normals", "state": "disabled", "reason": "require.normals=false"})
    scan = None
    if req["clash"]:
        try:
            scan = _scan_core(info["objects"], names, m, spec, 0, max_pairs, float(depth_eps_mm),
                              float(contact_eps_mm), float(report_gap_mm), 1.0, True,
                              bool(use_contract), bool(include_hidden), md, int(budget_ms))
        except Exception as e:
            scan = {"ok": False, "verdict": "degraded", "error": "%s: %s" % (type(e).__name__, str(e)[:200])}
        st = scan.get("verdict") if scan.get("ok") else "degraded"
        if st not in ("pass", "fail", "degraded"):
            st = "degraded"
        gates.append({"id": "clash_all", "state": st,
                      "reason": scan.get("verdict_line") or scan.get("error"),
                      "receipt": _scan_summary(scan) if scan.get("ok") else _trim(scan)})
    else:
        gates.append({"id": "clash_all", "state": "disabled", "reason": "require.clash=false"})
    mfloat = None
    if req["floaters"]:
        if isinstance(m, dict):
            mfloat = _manifest_float(m, md, names, mmu, float(contact_eps_mm))
            st = ("fail" if mfloat["floaters"] else
                  ("degraded" if mfloat["unresolved"] else "pass"))
            gates.append({"id": "floaters", "state": st,
                          "reason": "manifest 连接图：%d 个浮空/接口未接触，%d 个未判"
                                    % (len(mfloat["floaters"]), len(mfloat["unresolved"])),
                          "floaters": mfloat["floaters"][:10],
                          "unresolved": mfloat["unresolved"][:10],
                          "graph": mfloat.get("graph")})
        else:
            cf = _connectivity_float(names, float(contact_eps_mm), bool(include_hidden))
            st = cf.get("state") or ("fail" if cf["floaters"] else
                                     ("degraded" if cf["unresolved"] else "pass"))
            gates.append({"id": "floaters", "state": st,
                          "reason": "audit_connectivity（单件口径）：%d 个浮块，%d 个未判"
                                    % (len(cf["floaters"]), len(cf["unresolved"])),
                          "floaters": cf["floaters"][:10],
                          "unresolved": cf["unresolved"][:10],
                          "receipt": cf.get("receipt"),
                          "note": cf.get("note")})
    else:
        gates.append({"id": "floaters", "state": "disabled", "reason": "require.floaters=false"})
    if req["interfaces"]:
        if isinstance(m, dict) and (m.get("interfaces") or []):
            if mfloat is None:
                mfloat = _manifest_float(m, md, names, mmu, float(contact_eps_mm))
            rows = mfloat.get("interfaces") or []
            states = [r.get("state") for r in rows]
            if "float" in states:
                st = "fail"
            elif any(s == "unresolved" for s in states) or mfloat.get("unresolved"):
                st = "degraded"
            elif states and all(s == "contact" for s in states):
                st = "pass"
            else:
                st = "degraded"
            gates.append({"id": "interfaces", "state": st,
                          "reason": "%d 条接口：contact=%d float=%d unresolved=%d"
                                    % (len(rows), states.count("contact"), states.count("float"),
                                       states.count("unresolved") + len(mfloat.get("unresolved") or [])),
                          "pairs": rows[:20]})
        else:
            gates.append({"id": "interfaces", "state": "skipped",
                          "reason": "manifest 没有 interfaces —— 接口配合未验证（未分析不得读作通过）"})
    else:
        gates.append({"id": "interfaces", "state": "disabled", "reason": "require.interfaces=false"})
    if req["measure"]:
        try:
            mg = _measure_gate(m, spec, md, names, mmu, float(tolerance_mm), float(contact_eps_mm))
        except Exception as e:
            mg = {"state": "degraded", "deviations": [],
                  "reason": "measure 异常: %s: %s" % (type(e).__name__, str(e)[:160])}
        gates.append({"id": "measure", "state": mg["state"], "reason": mg.get("reason"),
                      "deviations": mg.get("deviations")[:20],
                      "receipt": {"audit_measure": mg.get("audit_measure")}})
    else:
        gates.append({"id": "measure", "state": "disabled", "reason": "require.measure=false"})
    enabled = [g for g in gates if g.get("state") != "disabled"]
    verdict = _agg_states([g.get("state") for g in enabled])
    if not enabled:
        verdict = "degraded"
    offenders, repairs = [], []
    trend = {"clash": 0, "contained": 0, "hidden_ok": 0, "contact": 0, "gap": 0,
             "unresolved": 0, "float": 0, "max_depth_mm": None}
    if scan and scan.get("ok"):
        counts = scan.get("counts") or {}
        for k in ("clash", "contained", "hidden_ok", "contact", "gap", "unresolved", "float"):
            trend[k] = int(counts.get(k) or 0)
        trend["max_depth_mm"] = scan.get("max_depth_mm")
        for p in scan.get("offenders") or []:
            offenders.append({"kind": "interference", "a": p.get("a"), "b": p.get("b"),
                              "class": p.get("class"), "severity": _severity_of_pair(p),
                              "depth_mm": p.get("depth_mm"), "volume_mm3": p.get("volume_mm3"),
                              "spec_ref": p.get("spec_ref"), "repair": "fix_interference",
                              "hint": "两件按 spec 复核：完全包含/超深穿模要用布尔差或调接口帧"})
            repairs.append({"op": "fix_interference",
                            "args": {"a": p.get("a"), "b": p.get("b"), "plan_only": True},
                            "why": p.get("reason") or "干涉"})
        for f in scan.get("floaters") or []:
            offenders.append({"kind": "floater", "object": f.get("object"),
                              "objects": f.get("objects"), "reason": f.get("reason"),
                              "interface": f.get("interface"), "mesh_gap_mm": f.get("mesh_gap_mm")})
        for u in scan.get("floating_unresolved") or []:
            offenders.append({"kind": "floater_unresolved", "reason": u.get("reason"),
                              "why": u.get("why")})
    if mfloat is not None:
        for f in mfloat.get("floaters") or []:
            offenders.append({"kind": "floater", "object": f.get("object"), "part": f.get("part"),
                              "reason": f.get("reason"), "interface": f.get("interface"),
                              "mesh_gap_mm": f.get("mesh_gap_mm"), "why": f.get("why")})
            repairs.append({"op": "asm_manifest", "args": {"action": "add_interface"},
                            "why": f.get("why") or "接口/连接图问题"})
    if mesh_g is not None and mesh_g.get("state") == "fail":
        for o in mesh_g.get("objects") or []:
            if o.get("state") == "fail":
                offenders.append({"kind": "mesh", "object": o.get("name"),
                                  "boundary_edges": o.get("boundary_edges"),
                                  "nonmanifold_edges": o.get("nonmanifold_edges"),
                                  "degenerate_faces": o.get("degenerate_faces"),
                                  "self_intersections": o.get("self_intersections")})
                repairs.append({"op": "fix_repair", "args": {"objects": [o.get("name")]},
                                "why": "网格不干净（破洞/非流形/自交），先修再谈装配"})
    norm_gate = next((g for g in gates if g.get("id") == "normals"), None)
    for nm in ((norm_gate or {}).get("inverted") or []):
        offenders.append({"kind": "normals", "object": nm, "reason": "法线朝内"})
        repairs.append({"op": "fix_repair", "args": {"objects": [nm], "recalc_normals": True},
                        "why": "法线朝内会让内外/穿模判据反转"})
    mg_gate = next((g for g in gates if g.get("id") == "measure"), None)
    for d in ((mg_gate or {}).get("deviations") or []):
        if d.get("state") == "fail":
            offenders.append({"kind": "measure", "id": d.get("id"), "a": d.get("a"),
                              "b": d.get("b"), "deviation_mm": d.get("deviation_mm"),
                              "expected_mm": d.get("expected_mm"), "tol_mm": d.get("tol_mm"),
                              "measured_gap_mm": d.get("measured_gap_mm")})
            repairs.append({"op": "asm_merge", "args": {"dry_run": True, "transform_policy": "snap_interface"},
                            "why": "接口间隙超公差：复核 manifest transform / 接口帧，再合并"})
    coverage = {"gates": len(gates), "enabled": len(enabled),
                "ran": sum(1 for g in enabled if g.get("state") in ("pass", "fail", "degraded")),
                "disabled": len(gates) - len(enabled),
                "skipped": sum(1 for g in enabled if g.get("state") == "skipped"),
                "complete": all(g.get("state") not in ("skipped",) for g in enabled)}
    manifest_summary = None
    if isinstance(m, dict):
        manifest_summary = {"assembly": m.get("assembly"), "sealed": bool(m.get("sealed")),
                            "manifest_md5": _manifest_md5(m), "counts": _manifest_counts(m)}
    shots_block = {"state": "skipped", "shots": [],
                   "note": "v1 不出图（shots 先 false）；数字结论以 audit_clash_all/measure 为准"}
    if shots:
        shots_block["warn"] = "shots=true 但本版未实现穿透特写出图（P1 接 qc_render_views）"
    verdict_line = ("装配门 %s（export_allowed=%s）：%s"
                    % (verdict, verdict == "pass",
                       " · ".join("%s=%s" % (g["id"], g["state"]) for g in gates)))
    body = {"ok": True, "verdict": verdict, "analyzed": True, "export_allowed": bool(verdict == "pass"),
            "strict": bool(strict), "gates": gates, "trend": trend, "offenders": offenders[:20],
            "repairs": repairs[:10], "coverage": coverage, "require": req,
            "manifest": manifest_summary, "shots": shots_block,
            "verdict_line": verdict_line,
            "note": "export_allowed 仅 verdict=pass 为 true；degraded=没查完/未确认，不是通过",
            "honest_limits": [
                "mesh 门用 audit_mesh 求值网格；scan 用同一套求值网格，但两者对多壳法线口径不同",
                "没有 manifest 时浮空退 audit_connectivity（单件 1% 可见 + micro_gap 口径），装配件可能误判/漏判",
                "measure 用表面采样最近距离（不是接触面积），bbox 值只作旁证",
                "本版不出穿透特写图（shots 恒 skipped），图只作证据、结论仍以数字为准",
            ],
            "trace": {"objects": len(names), "md_errors": md_errors,
                      "hidden_skipped": info.get("hidden_skipped", 0)}}
    body["receipt_md5"] = _md5_text(_canon({k: v for k, v in body.items()
                                            if k not in ("ms", "receipt_md5", "trace")}))
    return _j(_receipt(body, t0))
# ────────────────────────────── ops：repair（fix_clash / fix_interference） ──────────────────────────────
# P1 装配修复：数字由插件算（asm_scan）→ 规划（AABB 平移 / 浮块 snap / 可选布尔差）→ 执行 → 复扫。
# 硬规则：dry_run 默认 true（只规划、绝不改场景）；dry_run=false 先文件级 snapshot 再动手；
# 布尔（拓扑改动）只在 allow_boolean=true 且两件都闭合流形时规划，且没有可用快照时拒绝执行。
_REPAIR_METHODS = ("translate", "snap", "subtract")
_REPAIR_HONEST_LIMITS = [
    "平移按 AABB 分离规划：AABB 分离 ⇒ 三角面必不相交；但可能引入与第三件的新冲突（复扫会报，最多 max_rounds 轮）",
    "布尔差只从较大件减去较小件（cutter 用副本、原件不动）；拓扑改动不能用对象级 mark/revert 回退，必须靠文件级 snapshot",
    "浮块计划来自 audit_snap_floaters（单件 1% 可见口径）；装配接口没搭上请用 manifest 连接图，不是同一判据",
    "开网格 / 法线朝内 / 求值网格缺失一律不自动修（证据不足不猜）；先 fix_repair / 重算法线",
    "dry_run=true 只读：不移动、不布尔、不写盘；深度/体积是采样式估计",
]


def _repair_flag(v, name):
    """JSON / 字符串 / 0-1 → bool；坏值回 (None, 错误)。"""
    if isinstance(v, bool):
        return v, None
    if isinstance(v, (int, float)) and float(v) in (0.0, 1.0):
        return bool(v), None
    if isinstance(v, str):
        s = v.strip().lower()
        if s in ("true", "1", "yes", "on"):
            return True, None
        if s in ("false", "0", "no", "off"):
            return False, None
    return None, "%s 必须是布尔（true/false），收到 %r" % (name, v)


def _repair_policy(policy):
    """policy 归一化 → (policy, explicit, err)。

    缺省（None）等价 safe，但保留旧调用语义：allow_boolean=true 是显式人工授权，仍可做布尔；
    **显式**传 policy="safe" 才硬禁布尔（拓扑改动），manual 只规划，aggressive 保留旧 allow_boolean 路径。
    """
    if policy is None:
        return "safe", False, None
    p = str(policy).strip().lower()
    if p in ("safe", "manual", "aggressive"):
        return p, True, None
    return None, False, "policy 只支持 safe|manual|aggressive，收到 %r" % (policy,)


def _repair_brief(scan):
    """scan 回执 → 修复循环用的瘦身状态（只留判据字段）。"""
    if not isinstance(scan, dict):
        return {"ok": False, "verdict": "error", "counts": {}, "offenders": [], "floaters": [],
                "floating_unresolved": [], "coverage": {}, "error": "scan 回执不是 dict"}
    return {"ok": bool(scan.get("ok")), "verdict": scan.get("verdict"),
            "counts": dict(scan.get("counts") or {}),
            "offenders": list(scan.get("offenders") or []),
            "floaters": list(scan.get("floaters") or []),
            "floating_unresolved": list(scan.get("floating_unresolved") or []),
            "coverage": dict(scan.get("coverage") or {}),
            "verdict_line": scan.get("verdict_line"), "error": scan.get("error")}


def _repair_bad_total(brief):
    """clash + contained + float（float 按 scan 的 counts 口径：浮空对象/接口数）。"""
    c = (brief or {}).get("counts") or {}
    return (int(c.get("clash") or 0) + int(c.get("contained") or 0) + int(c.get("float") or 0))


def _repair_residual(brief):
    c = (brief or {}).get("counts") or {}
    return {"clash": int(c.get("clash") or 0), "contained": int(c.get("contained") or 0),
            "float": int(c.get("float") or 0), "unresolved": int(c.get("unresolved") or 0)}


def _repair_pair_row(p):
    """offender → 瘦身行（判据字段，不展开大对象）。"""
    if not isinstance(p, dict):
        return {"raw": str(p)[:160]}
    return {"a": p.get("a"), "b": p.get("b"), "class": p.get("class"),
            "severity": p.get("severity"), "depth_mm": p.get("depth_mm"),
            "volume_mm3": p.get("volume_mm3"), "min_gap_mm": p.get("min_gap_mm"),
            "tri_overlap_pairs": p.get("tri_overlap_pairs"),
            "method": p.get("method"), "reason": str(p.get("reason") or "")[:220]}


def _repair_float_row(f):
    if not isinstance(f, dict):
        return {"raw": str(f)[:160]}
    return {"object": f.get("object"), "objects": [str(x) for x in (f.get("objects") or [])][:12],
            "reason": f.get("reason"), "interface": f.get("interface"),
            "mesh_gap_mm": f.get("mesh_gap_mm"), "why": str(f.get("why") or "")[:200]}


def _repair_brief_out(brief):
    b = brief if isinstance(brief, dict) else {}
    return {"ok": bool(b.get("ok", False)), "verdict": b.get("verdict"),
            "counts": b.get("counts") or {},
            "coverage": b.get("coverage"),
            "offenders": [_repair_pair_row(p) for p in (b.get("offenders") or [])[:20]],
            "floaters": [_repair_float_row(f) for f in (b.get("floaters") or [])[:20]],
            "floating_unresolved": [{"reason": u.get("reason"), "why": str(u.get("why") or "")[:200]}
                                    for u in (b.get("floating_unresolved") or [])[:10] if isinstance(u, dict)],
            "verdict_line": b.get("verdict_line"), "error": b.get("error")}


def _repair_touched(rec):
    """一个 plan/blocked 记录牵涉到的对象名集合。"""
    out = set()
    if not isinstance(rec, dict):
        return out
    pr = rec.get("pair")
    if isinstance(pr, (list, tuple)):
        out.update(str(x) for x in pr if x is not None)
    elif pr:
        out.add(str(pr))
    for k in ("target", "cutter", "object"):
        if rec.get(k):
            out.add(str(rec[k]))
    for o in rec.get("objects") or []:
        out.add(str(o))
    return out


def _dedupe_blocked(items):
    out, seen = [], set()
    for it in items or []:
        try:
            key = _canon(it if isinstance(it, dict) else {"raw": str(it)})
        except Exception:
            key = str(it)
        if key in seen:
            continue
        seen.add(key)
        out.append(it)
    return out


def _obj_volume_mm3(md, mmu):
    """求值网格体积（mm³）；有符号体积不可用时退 AABB 体积。"""
    if not isinstance(md, dict):
        return 0.0
    try:
        v = abs(float(md.get("volume_signed_m3") or 0.0)) * (float(mmu) ** 3)
    except Exception:
        v = 0.0
    if v > 0.0:
        return v
    try:
        lo, hi = md["aabb"][0], md["aabb"][1]
        return abs((float(hi[0]) - float(lo[0])) * (float(hi[1]) - float(lo[1])) *
                   (float(hi[2]) - float(lo[2]))) * (float(mmu) ** 3)
    except Exception:
        return 0.0


def _repair_manifold_ok(md):
    return bool(isinstance(md, dict) and md.get("closed") and md.get("normals_ok"))


def _repair_move_precheck(name):
    """整件平移的阻塞预检（与 audit_snap_floaters 同口径）。"""
    out = []
    ob = bpy.data.objects.get(str(name))
    if ob is None:
        return ["对象不存在: %s" % name]
    if ob.parent is not None:
        out.append("%s 有父级（整件平移会被父级覆盖，先清父级）" % name)
    if ob.animation_data is not None:
        out.append("%s 有动画数据（关键帧会覆盖平移）" % name)
    return out


def _repair_translate_plan(md, a, b, clearance_mm, mmu):
    """两件 AABB → 把**体积较小件**沿最省平移量的轴移开（AABB 分离 ⇒ 三角面必不相交）。

    返回 (action, None) 或 (None, blocked_slim)。平移量 = AABB 分离所需 + clearance_mm；
    是否超出 max_move_mm 由调用方判定（这里只给 required_mm）。
    """
    ma, mb = md.get(a), md.get(b)
    if not isinstance(ma, dict) or not isinstance(mb, dict):
        return None, {"pair": sorted([a, b]), "method": "translate",
                      "reason": "求值网格缺失（md），无法规划平移"}
    va, vb = _obj_volume_mm3(ma, mmu), _obj_volume_mm3(mb, mmu)
    small, big = (a, b) if va <= vb else (b, a)
    ms, mbg = md.get(small), md.get(big)
    try:
        mn_s = [float(x) for x in ms["aabb"][0]]
        mx_s = [float(x) for x in ms["aabb"][1]]
        mn_b = [float(x) for x in mbg["aabb"][0]]
        mx_b = [float(x) for x in mbg["aabb"][1]]
    except Exception as e:
        return None, {"pair": sorted([a, b]), "method": "translate",
                      "reason": "AABB 读取失败: %s: %s" % (type(e).__name__, str(e)[:120])}
    best = None
    for k in range(3):
        if mx_s[k] <= mn_b[k] + 1e-12 or mx_b[k] <= mn_s[k] + 1e-12:
            continue  # 该轴已分离，不是可行分离轴
        d_pos = mx_b[k] - mn_s[k]           # 小件往 + 方向移出大件 AABB
        d_neg = mn_b[k] - mx_s[k]           # 小件往 - 方向移出大件 AABB
        delta = d_pos if abs(d_pos) <= abs(d_neg) else d_neg
        req_mm = abs(delta) * float(mmu)
        overlap_mm = (min(mx_s[k], mx_b[k]) - max(mn_s[k], mn_b[k])) * float(mmu)
        if best is None or req_mm < best["required_mm"] - 1e-12:
            best = {"axis": k, "delta": delta, "required_mm": req_mm, "overlap_mm": overlap_mm}
    if best is None:
        return None, {"pair": sorted([a, b]), "method": "translate",
                      "reason": "AABB 三轴都已分离（与 clash/contained 分类不一致，数据异常）"}
    axes = "xyz"
    delta_vec = [0.0, 0.0, 0.0]
    delta_vec[best["axis"]] = best["delta"]
    post_mn = [mn_s[k] + delta_vec[k] for k in range(3)]
    post_mx = [mx_s[k] + delta_vec[k] for k in range(3)]
    new_overlaps = []
    for nm, mdo in (md or {}).items():
        if nm in (a, b) or not isinstance(mdo, dict):
            continue
        try:
            on = [float(x) for x in mdo["aabb"][0]]
            ox = [float(x) for x in mdo["aabb"][1]]
        except Exception:
            continue
        if all(min(post_mx[k], ox[k]) - max(post_mn[k], on[k]) > 1e-9 for k in range(3)):
            new_overlaps.append(str(nm))
    need = float(best["required_mm"]) + float(clearance_mm)
    act = {"method": "translate", "target": small, "keep": big, "pair": sorted([a, b]),
           "axis": axes[best["axis"]],
           "delta_units": [_r(x, 8) for x in delta_vec],
           "delta_mm": [_r(x * float(mmu), 4) for x in delta_vec],
           "overlap_mm": _r(best["overlap_mm"], 4),
           "required_mm": _r(need, 4),
           "clearance_mm": _r(clearance_mm, 4),
           "aabb_separated_after": True,
           "new_aabb_overlaps": new_overlaps[:10],
           "risk": ("可能新增与 %d 件 AABB 重叠：%s（复扫会报）"
                    % (len(new_overlaps), new_overlaps[:5]) if new_overlaps else "none"),
           "why": ("沿 %s 轴移开小件 %s：平移 %.3f mm（AABB 交叠 %.3f + 间隙 %.3f）→ AABB 分离"
                   "（AABB 分离 ⇒ 三角面不可能相交）"
                   % (axes[best["axis"]], small, need, best["overlap_mm"], float(clearance_mm)))}
    return act, None


def _repair_boolean_plan(md, a, b, mmu, cls):
    """两件都闭合流形 → 从较大件布尔减较小件（cutter 会在执行时复制，原件不动）。"""
    ma, mb = md.get(a), md.get(b)
    if not isinstance(ma, dict) or not isinstance(mb, dict):
        return None, "求值网格缺失"
    if not (_repair_manifold_ok(ma) and _repair_manifold_ok(mb)):
        return None, "两件不都是闭合且法线朝外的流形（布尔差不可信）"
    va, vb = _obj_volume_mm3(ma, mmu), _obj_volume_mm3(mb, mmu)
    target, cutter = (a, b) if va >= vb else (b, a)
    return {"method": "subtract", "target": target, "cutter": cutter, "pair": sorted([a, b]),
            "class": cls,
            "params": {"op": "DIFFERENCE", "solver": "EXACT", "cutter_copy": True},
            "risk": "布尔改 target 拓扑（材质槽/UV 可能受影响）；cutter 用副本、原件不动；回退靠文件级 snapshot",
            "why": "两件均闭合流形；allow_boolean=true → 从 %s 减去 %s 的侵入体（不改 cutter 原件）"
                   % (target, cutter)}, None


def _repair_float_plan(scan, names, planned):
    """float → audit_snap_floaters(dry_run=true) 拿计划；拿不到就 blocked（不猜）。"""
    plan, blocked = [], []
    floaters = [f for f in (scan.get("floaters") or []) if isinstance(f, dict)]
    if not floaters:
        return plan, blocked
    names_all = [str(x) for x in (names or [])]
    sf, err = _call_api("audit", "snap_floaters",
                        {"objects": names_all, "dry_run": True})
    if err or sf is None:
        blocked.append({"kind": "float", "method": "snap",
                        "reason": "浮块修复计划拿不到：%s（在线路径 preload 里带 audit）" % str(err)[:200]})
        return plan, blocked
    if not sf.get("ok"):
        blocked.append({"kind": "float", "method": "snap",
                        "reason": "audit_snap_floaters ok=false: %s" % str(sf.get("error"))[:200]})
        return plan, blocked
    moves = [m for m in (sf.get("moves") or []) if isinstance(m, dict)]
    skipped = [s for s in (sf.get("skipped") or []) if isinstance(s, dict)]
    for mv in moves:
        mobjs = [str(x) for x in (mv.get("objects") or []) if x]
        if not mobjs:
            blocked.append({"kind": "float", "method": "snap", "rank": mv.get("rank"),
                            "reason": "snap 计划缺 objects"})
            continue
        if any(o in planned for o in mobjs):
            blocked.append({"kind": "float", "method": "snap", "rank": mv.get("rank"),
                            "objects": mobjs, "reason": "本轮已规划该对象（多浮块/多冲突；下一轮复扫再处理）"})
            continue
        pre = []
        for o in mobjs:
            pre.extend(_repair_move_precheck(o))
        pre.extend(str(x) for x in (mv.get("why_not_applied") or []))
        if pre:
            blocked.append({"kind": "float", "method": "snap", "rank": mv.get("rank"),
                            "objects": mobjs, "reason": "; ".join(sorted(set(pre)))})
            continue
        planned.update(mobjs)
        plan.append({"method": "snap", "objects": mobjs, "pair": mobjs[:2],
                     "delta_units": [_r(x, 8) for x in (mv.get("delta_units") or [])],
                     "delta_mm": [_r(x, 4) for x in (mv.get("delta_mm") or [])],
                     "gap_before_mm": mv.get("gap_before_mm"),
                     "snap_method": mv.get("method"), "rank": mv.get("rank"),
                     "risk": "浮块整件平移会改变与其它件的接触/最近邻关系（复扫会报）；父级/动画已 blocked",
                     "why": "浮块贴到最近邻分量（audit_snap_floaters 计划；delta 单位=场景单位，delta_mm=毫米）"})
    for sk in skipped:
        blocked.append({"kind": "float", "method": "snap", "rank": sk.get("rank"),
                        "objects": [str(x) for x in (sk.get("objects") or [])][:10],
                        "reason": str(sk.get("why") or "snap 计划拒绝该浮块")[:220]})
    if not plan and not blocked:
        blocked.append({"kind": "float", "method": "snap",
                        "objects": [str(f.get("object")) for f in floaters if f.get("object")][:10],
                        "reason": "audit_snap_floaters 没给出移动计划（孤岛无最近邻/浮块只是对象一部分）—— 需人工接上或补 manifest 接口"})
    return plan, blocked


def _repair_plan_all(scan, names, md, mmu, max_move_mm, clearance_mm, allow_boolean,
                     policy="safe", policy_explicit=False):
    """scan → (plan, blocked)。clash/contained 走平移（不够/被父级挡住再考虑布尔），float 走 snap。

    policy（低风险自动修复策略）：
      * safe（默认）：自动动作只允许 translate/snap；布尔是拓扑改动，必须 allow_boolean=true 显式同意
        （人工显式授权，不是自动选择）；scale 不实现/禁止。
      * manual：只规划、不自动执行；**不规划布尔**（干跑=false 也只回只读计划）。
      * aggressive：保留旧行为（布尔仍受 allow_boolean 控制），plan 里标 policy=aggressive。
    """
    plan, blocked = [], []
    planned = set()
    pol = str(policy or "safe").strip().lower()
    if pol not in ("safe", "manual", "aggressive"):
        pol = "safe"

    def _try_boolean(pair, cls):
        if pol == "manual":
            return False, "policy=manual：只出计划、不规划布尔（人工执行用 policy=aggressive + allow_boolean=true）"
        if pol == "safe" and policy_explicit:
            return False, ("policy=safe：禁止布尔（拓扑改动，低风险策略只允许 translate/snap）——"
                           "需显式 policy=aggressive + allow_boolean=true，或人工处理")
        if not allow_boolean:
            if pol == "aggressive":
                return False, "allow_boolean=false（aggressive 也需显式 allow_boolean=true 才做布尔）"
            return False, ("allow_boolean=false（policy=%s 不自动做布尔；布尔是拓扑改动，"
                           "需 allow_boolean=true 显式同意）" % pol)
        act, why = _repair_boolean_plan(md, pair[0], pair[1], mmu, cls)
        if act is None:
            return False, why
        if act["target"] in planned:
            return False, "目标 %s 本轮已有修复动作（多冲突；下一轮复扫再处理）" % act["target"]
        act["policy"] = pol
        planned.add(act["target"])
        plan.append(act)
        return True, None

    seen = set()
    for p in scan.get("offenders") or []:
        if not isinstance(p, dict):
            continue
        a, b = str(p.get("a") or ""), str(p.get("b") or "")
        cls = p.get("class")
        if not a or not b or cls not in ("clash", "contained"):
            continue
        key = _pair_key(a, b)
        if key in seen:
            continue
        seen.add(key)
        missing = [x for x in (a, b) if x not in md]
        if missing:
            blocked.append({"pair": [a, b], "class": cls, "method": "translate",
                            "reason": "求值网格缺失（md 里没有 %s）—— 先修网格" % missing})
            continue
        act, why = _repair_translate_plan(md, a, b, clearance_mm, mmu)
        if act is not None and act["required_mm"] <= float(max_move_mm) + 1e-9:
            target = act["target"]
            if target in planned:
                blocked.append({"pair": [a, b], "class": cls, "method": "translate",
                                "reason": "对象 %s 本轮已规划移动（多冲突；下一轮复扫再处理）" % target})
                continue
            pre = _repair_move_precheck(target)
            if pre:
                okb, whyb = _try_boolean([a, b], cls)
                if okb:
                    continue
                blocked.append({"pair": [a, b], "class": cls, "method": "translate",
                                "reason": "; ".join(pre) + "；布尔不可用：%s" % (whyb or "未知")})
                continue
            act["class"] = cls
            planned.add(target)
            plan.append(act)
            continue
        if act is None:
            why_txt = str((why or {}).get("reason") if isinstance(why, dict) else (why or "无法规划平移"))
        else:
            why_txt = ("需要 %.3f mm 平移 > max_move_mm %.3f mm（可调 max_move_mm / 复核接口帧 / 允许布尔）"
                       % (act["required_mm"], float(max_move_mm)))
        okb, whyb = _try_boolean([a, b], cls)
        if okb:
            continue
        if pol == "manual":
            why_txt += "；policy=manual 不规划布尔（只出计划）"
        elif pol == "safe" and policy_explicit:
            why_txt += "；policy=safe 硬禁布尔（需显式 policy=aggressive + allow_boolean=true，或人工处理）"
        elif not allow_boolean:
            why_txt += "；policy=%s 不自动做布尔（需 allow_boolean=true 显式同意）" % pol
        else:
            why_txt += "；布尔不可用：%s" % (whyb or "未知")
        blocked.append({"pair": [a, b], "class": cls, "method": "translate",
                        "required_mm": (act or {}).get("required_mm"), "reason": why_txt})
    fplan, fblocked = _repair_float_plan(scan, names, planned)
    plan.extend(fplan)
    blocked.extend(fblocked)
    for u in scan.get("floating_unresolved") or []:
        if isinstance(u, dict):
            blocked.append({"kind": "float_unresolved",
                            "reason": str(u.get("why") or u.get("reason") or "浮空未判")[:240]})
    for nm in (scan.get("coverage") or {}).get("unresolved_objects") or []:
        blocked.append({"kind": "unresolved_object", "object": str(nm),
                        "reason": "网格不闭合/法线朝内/求值网格缺失 → 内外与深度不可信，自动修复拒绝（先 fix_repair / 重算法线）"})
    # 统一回执字段：plan[].risk（每动作必须有）+ blocked[].blocked_reason（=reason 别名，弱模型直读）
    for a in plan:
        if isinstance(a, dict):
            a.setdefault("risk", "none")
            a.setdefault("policy", pol)
    for b in blocked:
        if isinstance(b, dict):
            b.setdefault("blocked_reason", b.get("reason"))
    return plan, blocked


def _repair_expected(brief, plan, blocked):
    """dry_run 的预测量（明确写清只是预测，实际以执行后复扫为准）。"""
    counts = dict((brief or {}).get("counts") or {})
    pred = {"clash": int(counts.get("clash") or 0),
            "contained": int(counts.get("contained") or 0),
            "float": int(counts.get("float") or 0)}
    n_translate = n_subtract = n_snap = 0
    for a in plan or []:
        m = a.get("method") if isinstance(a, dict) else None
        cls = (a or {}).get("class") if isinstance(a, dict) else None
        if m == "translate":
            n_translate += 1
            if cls in pred:
                pred[cls] = max(0, pred[cls] - 1)
        elif m == "subtract":
            n_subtract += 1
            if cls in pred:
                pred[cls] = max(0, pred[cls] - 1)
        elif m == "snap":
            n_snap += 1
            pred["float"] = max(0, pred["float"] - 1)
    return {"actions": len(plan or []), "translate_pairs": n_translate,
            "subtract_pairs": n_subtract, "snap_floaters": n_snap,
            "blocked": len(blocked or []), "predicted_bad_counts": pred,
            "note": "预测只按“一个动作消一对”扣减，未计新冲突/采样误差；实际以 dry_run=false 复扫为准"}


def _repair_rescan(names, m, spec):
    try:
        raw = asm_scan(scope=None, objects=list(names), manifest=m, spec=spec, summary_only=False)
        return json.loads(raw) if isinstance(raw, str) else raw
    except Exception as e:
        import traceback
        return {"ok": False, "error": "%s: %s" % (type(e).__name__, str(e)[:240]),
                "traceback": traceback.format_exc()[-500:]}


def _apply_repair_action(act, objs, allow_boolean):
    """执行单个修复动作（translate/snap 平移；subtract 布尔差）。返回逐动作记录，不抛。"""
    if not isinstance(act, dict):
        return {"ok": False, "error": "动作不是 dict", "action": str(act)[:160]}
    method = str(act.get("method") or "")
    if method in ("translate", "snap"):
        targets = [str(act.get("target"))] if method == "translate" \
            else [str(x) for x in (act.get("objects") or []) if x]
        dv = act.get("delta_units") or []
        if len(dv) != 3 or any(_num(x) is None for x in dv):
            return {"ok": False, "method": method, "error": "delta_units 不是 3 个数字",
                    "action": act}
        vec = Vector((float(dv[0]), float(dv[1]), float(dv[2])))
        moved = []
        for n in targets:
            ob = objs.get(n) or bpy.data.objects.get(n)
            if ob is None:
                return {"ok": False, "method": method, "error": "对象不存在: %s" % n, "action": act}
            try:
                ob.matrix_world.translation = ob.matrix_world.translation + vec
            except Exception as e:
                return {"ok": False, "method": method, "target": n,
                        "error": "%s: %s" % (type(e).__name__, str(e)[:160]), "action": act}
            moved.append(n)
        return {"ok": True, "method": method, "objects": moved, "pair": act.get("pair"),
                "target": act.get("target"), "delta_units": [_r(x, 8) for x in dv],
                "delta_mm": act.get("delta_mm")}
    if method == "subtract":
        if not allow_boolean:
            return {"ok": False, "method": "subtract", "pair": act.get("pair"),
                    "error": "allow_boolean=false，拒绝布尔（拓扑改动需显式允许）"}
        geo = _api("geo")
        bfn = None
        try:
            bfn = geo.get("boolean") if geo is not None else None
        except Exception:
            bfn = None
        if not callable(bfn):
            return {"ok": False, "method": "subtract", "pair": act.get("pair"),
                    "error": "K.dsh_geo_api.boolean 不可用（在线路径 preload 里带 geo）"}
        t = bpy.data.objects.get(str(act.get("target") or ""))
        c = bpy.data.objects.get(str(act.get("cutter") or ""))
        if t is None or c is None:
            return {"ok": False, "method": "subtract", "pair": act.get("pair"),
                    "error": "target/cutter 对象不存在（target=%r cutter=%r）"
                             % (act.get("target"), act.get("cutter"))}
        dup, dup_mesh = None, None
        try:
            dup = c.copy()
            if c.data is not None:
                dup.data = c.data.copy()
                dup_mesh = dup.data
            dup.name = "%s__dsh_repair_cut" % str(c.name)[:48]
            coll = (t.users_collection[0] if t.users_collection else bpy.context.scene.collection)
            coll.objects.link(dup)
        except Exception as e:
            if dup is not None:
                try:
                    bpy.data.objects.remove(dup, do_unlink=True)
                except Exception:
                    pass
            if dup_mesh is not None and dup_mesh.users == 0:
                try:
                    bpy.data.meshes.remove(dup_mesh)
                except Exception:
                    pass
            return {"ok": False, "method": "subtract", "pair": act.get("pair"),
                    "error": "复制 cutter 失败: %s: %s" % (type(e).__name__, str(e)[:160])}
        try:
            bfn(t, dup, "DIFFERENCE")
        except Exception as e:
            try:
                if bpy.data.objects.get(dup.name) is not None:
                    bpy.data.objects.remove(dup, do_unlink=True)
            except Exception:
                pass
            if dup_mesh is not None and dup_mesh.users == 0:
                try:
                    bpy.data.meshes.remove(dup_mesh)
                except Exception:
                    pass
            return {"ok": False, "method": "subtract", "target": str(t.name), "cutter": str(c.name),
                    "error": "%s: %s" % (type(e).__name__, str(e)[:240]),
                    "hint": "布尔退化守卫拦下（自交/非流形主体）→ 先 fix_repair 或改用平移"}
        try:
            if bpy.data.objects.get(dup.name) is not None:
                bpy.data.objects.remove(dup, do_unlink=True)
        except Exception:
            pass
        if dup_mesh is not None and dup_mesh.users == 0:
            try:
                bpy.data.meshes.remove(dup_mesh)
            except Exception:
                pass
        return {"ok": True, "method": "subtract", "target": str(t.name),
                "cutter": str(c.name), "pair": act.get("pair")}
    return {"ok": False, "method": method or "?", "error": "未知修复方法（只支持 translate/snap/subtract）"}


def asm_repair(manifest=None, scope=None, objects=None, spec=None, dry_run=True,
               max_move_mm=50.0, allow_boolean=False, snapshot=True,
               max_rounds=3, clearance_mm=0.5, policy=None):
    """装配修复（默认只规划）：asm_scan → 规划 →（dry_run=false）snapshot → apply → 复扫，最多 max_rounds 轮。

    规划口径：
      * clash / contained → 选体积小的件沿 AABB 交叠最小的轴平移（按 AABB 分离所需量 + clearance_mm），
        超过 max_move_mm ⇒ blocked；目标件有父级/动画 ⇒ blocked（整件平移会被覆盖）。
      * float → 有 K.dsh_audit_api 时调 audit_snap_floaters(dry_run=true) 拿计划，否则 blocked。
      * allow_boolean=true 且两件都闭合流形（且平移不可行 / 被父级挡住）→ 从较大件布尔减较小件
        （cutter 用副本，原件不动）；没有可用文件级快照时拒绝执行布尔。
    策略 policy（缺省等价 safe；显式传值才有硬约束）：
      * 缺省 None → safe：自动动作只允许 translate/snap；布尔是拓扑改动，保留旧兼容路径
        （allow_boolean=true = 显式人工授权才做）。
      * policy="safe"（显式）：**硬禁**布尔/scale，只允许 translate/snap。
      * policy="manual"：只规划、绝不自动执行（dry_run=false 也只回只读计划）；不规划布尔。
      * policy="aggressive"：保留旧 allow_boolean 行为，plan 标 policy=aggressive。
    执行口径：dry_run=false 先文件级 snapshot（K.dsh_txn_api），逐动作 apply → view_layer.update →
    重跑 asm_scan；复扫仍有残留就按新扫描再规划，最多 max_rounds 轮（不动就停，不空转）。
    别名：fix_clash（执行器，dry_run 默认 true）；纯规划器用 fix_interference（plan_only=true）。
    """
    t0 = time.perf_counter()
    dry_run, ferr = _repair_flag(dry_run, "dry_run")
    if ferr:
        return _err(ferr)
    allow_boolean, ferr = _repair_flag(allow_boolean, "allow_boolean")
    if ferr:
        return _err(ferr)
    snapshot, ferr = _repair_flag(snapshot, "snapshot")
    if ferr:
        return _err(ferr)
    pol, pol_explicit, perr = _repair_policy(policy)
    if perr:
        return _err(perr)
    mv, cm = _num(max_move_mm), _num(clearance_mm)
    if mv is None or mv < 0.0:
        return _err("max_move_mm 必须是 ≥0 的有限数字，收到 %r" % (max_move_mm,))
    if cm is None or cm < 0.0:
        return _err("clearance_mm 必须是 ≥0 的有限数字，收到 %r" % (clearance_mm,))
    try:
        rounds_f = float(max_rounds)
    except Exception:
        rounds_f = None
    if isinstance(max_rounds, bool) or rounds_f is None or not math.isfinite(rounds_f) \
            or abs(rounds_f - round(rounds_f)) > 1e-9:
        return _err("max_rounds 必须是整数（1..20），收到 %r" % (max_rounds,))
    rounds_cap = int(round(rounds_f))
    if rounds_cap < 1 or rounds_cap > 20:
        return _err("max_rounds 必须在 1..20，收到 %r" % (max_rounds,))
    m = None
    if manifest is not None:
        m, merr = _load_manifest(manifest)
        if merr:
            return _err(merr)
        mval = _manifest_validate(m, check_files=False, check_md5=False)
        if not mval["ok"]:
            return _err("manifest 结构校验失败（坏清单上不规划修复）",
                        errors=mval["errors"][:10], warnings=mval["warnings"][:10])
    info = _pick_objects(scope, objects, include_hidden=False)
    if info.get("error"):
        return _err(info["error"], objects=info.get("names"), missing=info.get("missing"),
                    hint="空产出/坏参数必须失败；确认对象名/集合名（或给 manifest）")
    names = list(info["names"])
    try:
        raw = asm_scan(scope=None, objects=names, manifest=m, spec=spec, summary_only=False)
        scan = json.loads(raw) if isinstance(raw, str) else raw
    except Exception as e:
        return _err("首次 asm_scan 异常: %s: %s" % (type(e).__name__, str(e)[:240]))
    if not isinstance(scan, dict) or not scan.get("ok"):
        return _err("首次 asm_scan 失败，拒绝在没扫描结果时规划修复",
                    scan=_trim(scan) if isinstance(scan, dict) else None)
    mmu = float(_KIT.units())
    md, md_errors = _mesh_map(info["objects"])
    before = _repair_brief(scan)
    before_bad = _repair_bad_total(before)
    params = {"max_move_mm": _r(mv, 4), "clearance_mm": _r(cm, 4),
              "allow_boolean": bool(allow_boolean), "max_rounds": rounds_cap,
              "snapshot": bool(snapshot), "dry_run": bool(dry_run), "policy": pol,
              "policy_explicit": bool(pol_explicit)}
    plan, blocked = _repair_plan_all(scan, names, md, mmu, mv, cm, bool(allow_boolean), pol, pol_explicit)
    expected = _repair_expected(before, plan, blocked)
    brief_counts = before.get("counts") or {}
    counts_line = ("clash=%d contained=%d float=%d unresolved=%d"
                   % (int(brief_counts.get("clash") or 0), int(brief_counts.get("contained") or 0),
                      int(brief_counts.get("float") or 0), int(brief_counts.get("unresolved") or 0)))
    if dry_run:
        if plan:
            verdict = "planned"
        elif before_bad:
            verdict = "blocked"
        else:
            verdict = before.get("verdict") or "pass"
        if verdict == "pass" and before.get("verdict") == "degraded":
            verdict = "degraded"
        if plan and blocked:
            hint = "dry_run 只规划（场景未改）：%d 个动作可执行、%d 项 blocked —— 按 blocked.reason 处理后重跑，确认后传 dry_run=false（会先 snapshot）"
        elif plan:
            hint = "dry_run 只规划（场景未改）：确认后传 dry_run=false 执行（先文件级 snapshot，再 apply，最后复扫）"
        elif before_bad:
            hint = "没有可自动修的动作：看 blocked.reason（调 max_move_mm / allow_boolean / 清父级 / preload audit / 先修网格）"
        elif before.get("verdict") == "degraded":
            hint = ("无 clash/contained/float 可自动修，但扫描是 degraded（unresolved/截断/未判完）—— "
                    "先修网格/法线或调预算重扫，绝不读成没问题")
        else:
            hint = "扫描无 clash/contained/float，无需修复"
        verdict_line = ("装配修复（dry_run，只读）：扫 %d 件 %s → 规划 %d 个动作 / blocked %d → %s"
                        % (len(names), counts_line, len(plan), len(blocked), verdict))
        body = {"ok": True, "verdict": verdict, "dry_run": True, "readonly": True,
                "stage": "planned", "method": "asm_repair", "aliases": ["fix_clash"],
                "params": params, "objects": len(names), "object_names": names,
                "before": _repair_brief_out(before),
                "plan": plan[:60], "blocked": _dedupe_blocked(blocked)[:40],
                "expected": expected,
                "verdict_line": verdict_line, "hint": hint,
                "honest_limits": list(_REPAIR_HONEST_LIMITS),
                "md_errors": md_errors, "contract_repairs": _plan_repair_hints(plan)}
        return _j(_receipt(body, t0))
    if pol == "manual":
        body = {"ok": False, "verdict": "planned", "dry_run": True, "readonly": True,
                "stage": "manual_policy", "method": "asm_repair", "aliases": ["fix_clash"],
                "params": params, "objects": len(names),
                "before": _repair_brief_out(before),
                "plan": plan[:60], "blocked": _dedupe_blocked(blocked)[:40],
                "expected": expected,
                "error": "policy=manual 只出计划、绝不自动执行（场景未改）；确认后改用 policy=safe/aggressive 或人工按 plan 执行",
                "verdict_line": "装配修复（manual，只读）：规划 %d 个动作 / blocked %d → 未改场景"
                                % (len(plan), len(blocked)),
                "hint": "policy=manual 不自动执行：translate/snap 用 policy=safe；布尔用 policy=aggressive + allow_boolean=true",
                "honest_limits": list(_REPAIR_HONEST_LIMITS), "md_errors": md_errors}
        return _j(_receipt(body, t0))
    # ---------------- 执行 ----------------
    exec_blocked = list(blocked)
    applied = []
    bool_exec = bool(allow_boolean) and pol != "manual"
    if not plan:
        if before_bad == 0:
            body = {"ok": True, "verdict": before.get("verdict") or "pass", "dry_run": False,
                    "analyzed": True, "stage": "nothing_to_do", "params": params,
                    "objects": len(names), "before": _repair_brief_out(before),
                    "after": _repair_brief_out(before), "residual": _repair_residual(before),
                    "repaired": False, "rounds": 0, "plan": [], "applied": [],
                    "blocked": _dedupe_blocked(exec_blocked)[:40], "snapshot": None,
                    "verdict_line": "装配修复：扫描已无 clash/contained/float，无需修复",
                    "hint": "无需修复（如仍有 unresolved/degraded，先修网格/法线再看）",
                    "honest_limits": list(_REPAIR_HONEST_LIMITS), "md_errors": md_errors}
            return _j(_receipt(body, t0))
        body = {"ok": False, "verdict": "blocked", "dry_run": False, "analyzed": True,
                "stage": "no_executable_action", "params": params,
                "objects": len(names), "before": _repair_brief_out(before),
                "after": _repair_brief_out(before), "residual": _repair_residual(before),
                "repaired": False, "rounds": 0, "plan": [], "applied": [],
                "blocked": _dedupe_blocked(exec_blocked)[:40], "snapshot": None,
                "error": "扫描到 %d 处 clash/contained/float，但没有可自动执行的动作" % before_bad,
                "verdict_line": "装配修复：0 个可执行动作 → blocked（场景未改）",
                "hint": "看 blocked.reason：调 max_move_mm / allow_boolean / 清父级动画 / preload audit / 先修网格",
                "honest_limits": list(_REPAIR_HONEST_LIMITS), "md_errors": md_errors}
        return _j(_receipt(body, t0))
    snap = None
    if snapshot:
        if _api("txn") is None:
            snap = {"ok": False, "skipped": True,
                    "note": "K.dsh_txn_api 不可用 ⇒ 没做文件级快照（布尔动作会被拒绝；平移失败无法自动回退）"}
        else:
            rec, serr = _call_api("txn", "snapshot",
                                  {"label": "asm_repair-%s" % time.strftime("%Y%m%d_%H%M%S"),
                                   "note": "asm_repair(dry_run=false) 前快照"})
            if serr:
                return _err("快照失败，拒绝在无回退点的场景上执行修复：%s" % serr)
            snap = rec.get("snapshot") if isinstance(rec, dict) else rec
    else:
        snap = {"ok": True, "skipped": True,
                "note": "snapshot=false：调用方自担回退（布尔拓扑改动对象级回退不回来）"}
    snap_ok = bool(isinstance(snap, dict) and not snap.get("skipped")
                   and snap.get("ok") is not False
                   and bool(snap.get("label") or snap.get("path") or snap.get("ok") is True))
    if any(a.get("method") == "subtract" for a in plan) and not snap_ok:
        keep = []
        for a in plan:
            if a.get("method") == "subtract":
                exec_blocked.append({"pair": a.get("pair"), "class": a.get("class"),
                                     "method": "subtract",
                                     "reason": "布尔改拓扑但没有可用的文件级快照（txn）→ 拒绝执行；先让 K.dsh_txn_api 可用且 snapshot=true"})
            else:
                keep.append(a)
        plan = keep
    if not plan:
        body = {"ok": False, "verdict": "blocked", "dry_run": False, "analyzed": True,
                "stage": "no_executable_action", "params": params,
                "objects": len(names), "before": _repair_brief_out(before),
                "after": _repair_brief_out(before), "residual": _repair_residual(before),
                "repaired": False, "rounds": 0, "plan": [], "applied": [],
                "blocked": _dedupe_blocked(exec_blocked)[:40], "snapshot": snap,
                "error": "可执行动作都被安全门拦下（布尔需要文件级快照）",
                "verdict_line": "装配修复：0 个可执行动作 → blocked（场景未改）",
                "hint": "preload txn 或 snapshot=true 后重跑；或先处理 blocked.reason",
                "honest_limits": list(_REPAIR_HONEST_LIMITS), "md_errors": md_errors}
        return _j(_receipt(body, t0))
    objs = {str(o.name): o for o in info["objects"]}
    cur_scan, cur_plan, rounds = scan, plan, 0
    for rnd in range(1, rounds_cap + 1):
        acts = [a for a in cur_plan if isinstance(a, dict) and a.get("method") in _REPAIR_METHODS]
        if not acts:
            break
        rounds = rnd
        n_ok = 0
        for act in acts:
            rec = _apply_repair_action(act, objs, bool_exec)
            rec["round"] = rnd
            if rec.get("ok"):
                applied.append(rec)
                n_ok += 1
            else:
                exec_blocked.append(rec)
        if n_ok == 0:
            break  # 什么都没动 ⇒ 复扫不会变，不空转
        try:
            bpy.context.view_layer.update()
        except Exception:
            pass
        rescan = _repair_rescan(names, m, spec)
        cur_scan = rescan
        brief = _repair_brief(rescan)
        if not brief.get("ok"):
            exec_blocked.append({"kind": "rescan", "round": rnd,
                                 "reason": "复扫失败：%s" % str(brief.get("error"))[:240]})
            break
        if _repair_bad_total(brief) == 0:
            break
        if rnd >= rounds_cap:
            break
        md, md_errors = _mesh_map([objs[nm] for nm in names if nm in objs])
        cur_plan, more_blocked = _repair_plan_all(rescan, names, md, mmu, mv, cm, bool(allow_boolean),
                                                 pol, pol_explicit)
        exec_blocked.extend(more_blocked)
    after = _repair_brief(cur_scan if isinstance(cur_scan, dict) else scan)
    final_bad = _repair_bad_total(after)
    residual = _repair_residual(after)
    rescan_broke = any(isinstance(x, dict) and x.get("kind") == "rescan" for x in exec_blocked)
    ok_final = bool(final_bad == 0 and after.get("ok") and not rescan_broke)
    if final_bad > 0:
        verdict = "fail"
    elif not after.get("ok"):
        verdict = "degraded"
    else:
        verdict = after.get("verdict") or "degraded"
    if ok_final:
        hint = "修复完成：clash/contained/float 已清零（复扫 verdict=%s）；建议再跑 asm_gate/asm_scan 复核" % verdict
    else:
        hint = ("修复未完成：残留 clash=%d contained=%d float=%d；看 blocked 与 after.offenders，"
                "调 max_move_mm / allow_boolean / 清父级动画 / 先修网格"
                % (residual["clash"], residual["contained"], residual["float"]))
    verdict_line = ("装配修复（已执行 %d 轮）：扫 %d 件 %s → 应用 %d 个动作，blocked %d → 复扫残留 "
                    "clash=%d contained=%d float=%d unresolved=%d → %s"
                    % (rounds, len(names), counts_line, len(applied),
                       len(_dedupe_blocked(exec_blocked)), residual["clash"], residual["contained"],
                       residual["float"], residual["unresolved"], verdict))
    body = {"ok": bool(ok_final), "verdict": verdict, "dry_run": False, "analyzed": True,
            "stage": "rechecked", "method": "asm_repair", "aliases": ["fix_clash"],
            "params": params, "objects": len(names), "object_names": names,
            "before": _repair_brief_out(before), "after": _repair_brief_out(after),
            "residual": residual,
            "repaired": bool(before_bad > 0 and final_bad < before_bad),
            "rounds": rounds, "plan": plan[:60],
            "applied": applied[:60], "blocked": _dedupe_blocked(exec_blocked)[:40],
            "snapshot": snap, "verdict_line": verdict_line, "hint": hint,
            "honest_limits": list(_REPAIR_HONEST_LIMITS), "md_errors": md_errors}
    return _j(_receipt(body, t0))


def _plan_repair_hints(plan):
    """dry_run 回执附带的下一步（弱模型照着调即可）。"""
    hints = []
    for a in plan or []:
        if not isinstance(a, dict):
            continue
        m = a.get("method")
        if m == "subtract":
            hints.append({"op": "fix_clash",
                          "args": {"a": (a.get("pair") or [None, None])[0],
                                   "b": (a.get("pair") or [None, None])[1],
                                   "allow_boolean": True, "policy": "aggressive", "dry_run": False},
                          "why": a.get("why")})
        elif m == "translate":
            hints.append({"op": "fix_clash",
                          "args": {"a": (a.get("pair") or [None, None])[0],
                                   "b": (a.get("pair") or [None, None])[1],
                                   "policy": "safe", "dry_run": False},
                          "why": a.get("why")})
        elif m == "snap":
            hints.append({"op": "fix_clash",
                          "args": {"objects": a.get("objects"), "policy": "safe", "dry_run": False},
                          "why": a.get("why")})
    return hints[:10]


def fix_interference(a=None, b=None, plan_only=True, manifest=None, scope=None, objects=None,
                     spec=None, max_move_mm=50.0, allow_boolean=False, clearance_mm=0.5,
                     max_rounds=3, policy=None):
    """装配干涉**规划器**（只读，永不改场景）：asm_repair(dry_run=true) 的规划视图。

    a/b 给了就只看牵涉这两件（或其中一件）的计划；不给就规划整个 scope。
    policy：safe（默认，自动只用 translate/snap）/ manual（只规划）/ aggressive（布尔受 allow_boolean 控制）。
    执行请用 fix_clash（= asm_repair，dry_run=false）；本 op 拒绝 plan_only=false。
    """
    t0 = time.perf_counter()
    po, ferr = _repair_flag(plan_only, "plan_only")
    if ferr:
        return _err(ferr)
    if not po:
        return _err("fix_interference 是纯规划器（plan_only 只能为 true，永不改场景）；"
                    "要执行用 fix_clash / asm_repair（dry_run=false）")
    scan_objects = objects
    want = set()
    for x in (a, b):
        if x is not None and str(x).strip():
            want.add(str(x))
    if len(want) >= 2 and objects is None and scope is None:
        scan_objects = sorted(want)   # 只看这一对：扫描成本最小
    res = json.loads(asm_repair(manifest=manifest, scope=scope, objects=scan_objects, spec=spec,
                                dry_run=True, max_move_mm=max_move_mm,
                                allow_boolean=allow_boolean, snapshot=False,
                                max_rounds=max_rounds, clearance_mm=clearance_mm, policy=policy))
    if not isinstance(res, dict):
        return _err("asm_repair 回执异常（不是 dict）")
    if not res.get("ok"):
        return _j(res)
    if want:
        res["plan"] = [r for r in (res.get("plan") or []) if want & _repair_touched(r)]
        res["blocked"] = [r for r in (res.get("blocked") or []) if want & _repair_touched(r)]
        res["filtered_to"] = sorted(want)
        res["expected"] = _repair_expected(res.get("before") or {}, res.get("plan") or [],
                                           res.get("blocked") or [])
        if not res["plan"] and res.get("verdict") == "planned":
            res["verdict"] = "blocked"
    res["planner"] = "fix_interference"
    res["readonly"] = True
    res["executor"] = "fix_clash（asm_repair，dry_run=false）"
    return _j(_receipt(res, t0))
# ────────────────────────────── ops：export（asm_export / 交付 + 账本） ──────────────────────────────
# P2 交付闭环：可选 N 路合并（asm_merge 内部已 gate+snapshot）→ 导出 GLB/FBX/OBJ → manifest + append-only ledger。
# 只读保证：dry_run=true 只回计划（不建目录、不导出、不写 manifest/ledger）。
_ASM_EXPORT_FORMATS = ("glb", "fbx", "obj")
_ASM_EXPORT_LIMITS = [
    "GLB/FBX/OBJ 由 Blender 官方导出器写出（export_apply=True 会落地修改器）；材质/贴图随导出器规则，不保证与 .blend 逐字节一致",
    "manifest 的 bbox/counts 来自求值网格快照（导出瞬间），与导出器内部再求值可能有微小差异",
    "ledger 是 append-only 的本地审计流水（不含跨进程锁）；并发追加可能交错，只保证单次调用内事件顺序",
    "gate verdict 只反映导出前 asm_gate 的三态结论；degraded ≠ 通过",
    "OBJ 附带 .mtl（export_materials=true），是否齐全取决于材质节点能否被 OBJ 导出器理解",
]


def _asm_export_name(name):
    """交付名安全化：只留字母数字/._-（中文等替换成 _）；空 → 错误。"""
    raw = str(name or "").strip()
    if not raw:
        return None, "name 不能为空（用于 <name>.glb / <name>_manifest.json）"
    keep = []
    for ch in raw:
        keep.append(ch if (ch.isalnum() or ch in "._-") else "_")
    out = "".join(keep).strip("._-")
    if not out:
        return None, "name 安全化后为空：%r" % (name,)
    if out != raw:
        return out, "name 已安全化：%r → %r" % (raw, out)
    return out, None


def _asm_export_formats(formats):
    """formats 归一化（列表或逗号/空格分隔字符串；gltf→glb；去重保序）。"""
    if formats is None:
        return list(_ASM_EXPORT_FORMATS), None
    raw = formats
    if isinstance(raw, str):
        s = raw.replace(",", " ").replace(";", " ").replace("/", " ")
        raw = [x for x in s.split() if x]
    if not isinstance(raw, (list, tuple, set)):
        return None, "formats 必须是列表或逗号分隔字符串，收到 %r" % (formats,)
    out, bad = [], []
    for x in raw:
        t = str(x).strip().lower()
        if t in ("gltf", "gltf2", "gltf-binary"):
            t = "glb"
        if t not in _ASM_EXPORT_FORMATS:
            bad.append(str(x))
            continue
        if t not in out:
            out.append(t)
    if bad:
        return None, "formats 里有不支持的格式 %s（允许 glb/fbx/obj）" % bad
    if not out:
        return None, "formats 为空（至少给 glb/fbx/obj 之一）"
    return out, None


def _asm_md5_file(path):
    """真 md5（manifest/files/ledger 用）；读不到回 None（不抛）。"""
    try:
        h = hashlib.md5()
        with open(path, "rb") as fh:
            while True:
                chunk = fh.read(1024 * 1024)
                if not chunk:
                    break
                h.update(chunk)
        return h.hexdigest()
    except Exception:
        return None


def _asm_file_receipt(path):
    rec = {"path": str(path), "exists": False, "bytes": None, "md5": None}
    try:
        rec["exists"] = bool(os.path.isfile(path))
        if rec["exists"]:
            rec["bytes"] = int(os.path.getsize(path))
            rec["md5"] = _asm_md5_file(path)
    except Exception as e:
        rec["error"] = "%s: %s" % (type(e).__name__, str(e)[:160])
    return rec


def _asm_verify_file(fmt, path):
    """按格式验文件真实性：GLB 魔数 / FBX Kaydara 头 / OBJ v & f 行；返回 ok + 证据。"""
    rec = {"fmt": str(fmt), "path": str(path), "ok": False, "exists": False,
           "bytes": None, "md5": None, "magic_ok": False}
    try:
        rec["exists"] = bool(os.path.isfile(path))
        if not rec["exists"]:
            rec["error"] = "文件不存在"
            return rec
        rec["bytes"] = int(os.path.getsize(path))
        if rec["bytes"] <= 0:
            rec["error"] = "文件为空（0 字节）"
            return rec
        rec["md5"] = _asm_md5_file(path)
        with open(path, "rb") as fh:
            head = fh.read(4096)
        if fmt == "glb":
            rec["magic_ok"] = head[:4] == b"glTF"
            if not rec["magic_ok"]:
                rec["error"] = "GLB 魔数不是 b'glTF'（拿到 %r）" % head[:4]
                return rec
        elif fmt == "fbx":
            rec["magic_ok"] = bool(head.startswith(b"Kaydara FBX Binary")
                                   or head.lstrip()[:3] in (b"; FB", b"FBX"))
            if not rec["magic_ok"]:
                rec["error"] = "FBX 头不像 Kaydara FBX Binary（拿到 %r）" % head[:16]
                return rec
        elif fmt == "obj":
            with open(path, "rb") as fh:
                chunk = fh.read(1 << 17)
            rec["magic_ok"] = bool((b"\nv " in chunk) or chunk.startswith(b"v ")
                                   or (b"\nvt " in chunk) or (b"\nf " in chunk))
            if not rec["magic_ok"]:
                rec["error"] = "OBJ 里没找到 v/vt/f 行"
                return rec
        else:
            rec["error"] = "未知格式: %s" % fmt
            return rec
        rec["ok"] = True
        return rec
    except Exception as e:
        rec["error"] = "%s: %s" % (type(e).__name__, str(e)[:200])
        return rec


def _asm_select_only(objs):
    """只选中要导出的对象（导出 use_selection=True）→ 返回可恢复的选择快照。"""
    snap = {"prev_selected": [], "prev_active": None}
    try:
        vl = bpy.context.view_layer
        act = vl.objects.active
        snap["prev_active"] = str(act.name) if act is not None else None
        snap["prev_selected"] = [str(ob.name) for ob in vl.objects if ob.select_get()]
        for ob in vl.objects:
            try:
                ob.select_set(False)
            except Exception:
                pass
        for ob in objs:
            try:
                ob.select_set(True)
            except Exception:
                pass
        if objs:
            vl.objects.active = objs[0]
    except Exception as e:
        snap["error"] = "%s: %s" % (type(e).__name__, str(e)[:160])
    return snap


def _asm_restore_selection(snap):
    try:
        vl = bpy.context.view_layer
        prev = set(snap.get("prev_selected") or [])
        for ob in vl.objects:
            try:
                ob.select_set(str(ob.name) in prev)
            except Exception:
                pass
        act = bpy.data.objects.get(str(snap.get("prev_active") or ""))
        if act is not None:
            vl.objects.active = act
    except Exception:
        pass


def _asm_ensure_addon(module):
    try:
        if module not in bpy.context.preferences.addons.keys():
            bpy.ops.preferences.addon_enable(module=module)
    except Exception:
        pass


def _asm_export_one(fmt, objs, path):
    """跑一次官方导出器（不改场景，只改选中状态，结束后恢复）。返回文件回执。"""
    t0 = time.perf_counter()
    d = os.path.dirname(path)
    if d:
        os.makedirs(d, exist_ok=True)
    sel = _asm_select_only(objs)
    try:
        if fmt == "glb":
            _asm_ensure_addon("io_scene_gltf2")
            if not hasattr(bpy.ops.export_scene, "gltf"):
                raise RuntimeError("glTF 导出器不可用（io_scene_gltf2 未启用且 bpy.ops.export_scene.gltf 不存在）")
            try:
                bpy.ops.export_scene.gltf(filepath=path, export_format="GLB", use_selection=True,
                                          export_apply=True, export_yup=True)
            except TypeError:
                bpy.ops.export_scene.gltf(filepath=path, export_format="GLB", use_selection=True)
        elif fmt == "fbx":
            _asm_ensure_addon("io_scene_fbx")
            if not hasattr(bpy.ops.export_scene, "fbx"):
                raise RuntimeError("FBX 导出器不可用（io_scene_fbx 未启用且 bpy.ops.export_scene.fbx 不存在）")
            try:
                bpy.ops.export_scene.fbx(filepath=path, use_selection=True, add_leaf_bones=False,
                                         bake_anim=False, apply_scale_options="FBX_SCALE_ALL")
            except TypeError:
                bpy.ops.export_scene.fbx(filepath=path, use_selection=True)
        elif fmt == "obj":
            if hasattr(bpy.ops.wm, "obj_export"):
                try:
                    bpy.ops.wm.obj_export(filepath=path, export_selected_objects=True,
                                          apply_modifiers=True, export_materials=True)
                except TypeError:
                    bpy.ops.wm.obj_export(filepath=path, export_selected_objects=True)
            elif hasattr(bpy.ops.export_scene, "obj"):
                bpy.ops.export_scene.obj(filepath=path, use_selection=True, use_mesh_modifiers=True)
            else:
                raise RuntimeError("OBJ 导出器不可用（bpy.ops.wm.obj_export / export_scene.obj 都不存在）")
        else:
            raise RuntimeError("不支持的格式: %s" % fmt)
    finally:
        _asm_restore_selection(sel)
    rec = _asm_file_receipt(path)
    rec["ms"] = int((time.perf_counter() - t0) * 1000)
    return rec


def _asm_export_paths(out_dir, name, formats, include_receipt, ledger):
    p = {}
    for fmt in formats:
        p[fmt] = os.path.join(out_dir, name + "." + fmt)
    p["manifest"] = os.path.join(out_dir, name + "_manifest.json") if include_receipt else None
    p["ledger"] = os.path.join(out_dir, "merge_ledger.jsonl") if ledger else None
    return p


def _asm_export_slim(rec):
    if not isinstance(rec, dict):
        return None
    out = {k: rec.get(k) for k in ("ok", "path", "bytes", "md5", "magic_ok", "error", "ms")}
    v = rec.get("verify")
    if isinstance(v, dict):
        out["verify"] = {k: v.get(k) for k in ("ok", "fmt", "magic_ok", "error", "skipped", "reason")}
    return out


def _asm_gate_slim(gate):
    if not isinstance(gate, dict):
        return None
    return {"ok": gate.get("ok"), "verdict": gate.get("verdict"),
            "export_allowed": gate.get("export_allowed"),
            "trend": gate.get("trend"), "coverage": gate.get("coverage"),
            "gates": [{"id": g.get("id"), "state": g.get("state"),
                       "reason": str(g.get("reason") or "")[:160]}
                      for g in (gate.get("gates") or [])[:12] if isinstance(g, dict)],
            "offenders": (gate.get("offenders") or [])[:10],
            "error": gate.get("error")}


def _asm_merge_slim(rec):
    if not isinstance(rec, dict):
        return None
    out = {k: rec.get(k) for k in ("ok", "verdict", "dry_run", "manifest_md5", "sealed",
                                   "objects_total", "object_warnings", "error")}
    out["order"] = (rec.get("order") or [])[:80]
    out["outputs"] = rec.get("outputs")
    out["snapshot"] = rec.get("snapshot")
    out["gate"] = _asm_gate_slim(rec.get("gate"))
    return out


def _asm_ledger_append(path, events):
    """append-only JSONL：只以 "a" 打开，绝不重写历史。"""
    rec = {"ok": False, "path": str(path), "lines": 0, "bytes": None, "error": None}
    try:
        d = os.path.dirname(path)
        if d:
            os.makedirs(d, exist_ok=True)
        with open(path, "a", encoding="utf-8") as fh:
            for ev in events:
                fh.write(json.dumps(ev, ensure_ascii=False, default=str) + "\n")
                rec["lines"] += 1
        rec["bytes"] = int(os.path.getsize(path))
        rec["ok"] = True
    except Exception as e:
        rec["error"] = "%s: %s" % (type(e).__name__, str(e)[:200])
    return rec


def _asm_mesh_summary(names, md, mmu):
    """导出对象 → manifest 用的 objects/counts/bbox（求值网格快照，单位与 mm 双份）。"""
    rows, missing = [], []
    lo = [None, None, None]
    hi = [None, None, None]
    tris = verts = 0
    for nm in names:
        m = md.get(nm)
        if not isinstance(m, dict):
            rows.append({"name": str(nm), "missing": True})
            missing.append(str(nm))
            continue
        try:
            a0 = [float(x) for x in m["aabb"][0]]
            a1 = [float(x) for x in m["aabb"][1]]
        except Exception:
            rows.append({"name": str(nm), "missing": "no_aabb"})
            missing.append(str(nm))
            continue
        ob = bpy.data.objects.get(str(nm))
        v = int(len(ob.data.vertices)) if (ob is not None and ob.data is not None) else None
        t = int(m.get("tris_n") or 0)
        tris += t
        if v:
            verts += v
        for k in range(3):
            lo[k] = a0[k] if lo[k] is None else min(lo[k], a0[k])
            hi[k] = a1[k] if hi[k] is None else max(hi[k], a1[k])
        rows.append({"name": str(nm), "verts": v, "tris": t,
                     "bbox_mm": {"min": [_r(a0[k] * mmu, 4) for k in range(3)],
                                 "max": [_r(a1[k] * mmu, 4) for k in range(3)],
                                 "size": [_r((a1[k] - a0[k]) * mmu, 4) for k in range(3)]}})
    bbox_units = bbox_mm = None
    if lo[0] is not None:
        bbox_units = {"min": [_r(x, 6) for x in lo], "max": [_r(x, 6) for x in hi],
                      "size": [_r(hi[k] - lo[k], 6) for k in range(3)]}
        bbox_mm = {"min": [_r(lo[k] * mmu, 4) for k in range(3)],
                   "max": [_r(hi[k] * mmu, 4) for k in range(3)],
                   "size": [_r((hi[k] - lo[k]) * mmu, 4) for k in range(3)]}
    return {"objects": rows[:200], "missing": missing[:60],
            "counts": {"objects": len(names), "verts": verts, "tris": tris},
            "bbox_units": bbox_units, "bbox_mm": bbox_mm}


def asm_export(manifest=None, scope=None, objects=None, out_dir=None, name="assembly",
               formats=["glb", "fbx", "obj"], merge=False, dry_run=False,
               include_receipt=True, ledger=True, verify=True):
    """装配交付导出：可选 N 路合并 → GLB/FBX/OBJ → <out_dir>/<name>_manifest.json + append-only merge_ledger.jsonl。

    流程：
      1) merge=true 且 dry_run=false：先 asm_merge(dry_run=false)（内部 gate + snapshot；不过门不导出）；
         merge=true 且 dry_run=true：只跑 asm_merge(dry_run=true) 拿计划。
      2) 导出 GLB（export_scene.gltf, GLB/use_selection/export_apply/export_yup）、
         FBX（export_scene.fbx）、OBJ（wm.obj_export，回退 export_scene.obj）；自动 enable io_scene_gltf2/io_scene_fbx。
      3) manifest：objects/bbox(单位+mm)/units/md5/files/gate/merge receipt；manifest_md5 自指纹。
      4) ledger：append-only JSONL，一行一个 merge/export 事件（ts/action/manifest_md5/files/md5/gate_verdict）。
    失败口径：坏参数 / 空场景 / 全部格式失败 ⇒ ok:false；部分格式失败 ⇒ ok:true + warnings。
    dry_run=true：只回计划（不建目录、不导出、不写 manifest/ledger）；场景只读。
    """
    t0 = time.perf_counter()
    merge, ferr = _repair_flag(merge, "merge")
    if ferr:
        return _err(ferr)
    dry_run, ferr = _repair_flag(dry_run, "dry_run")
    if ferr:
        return _err(ferr)
    include_receipt, ferr = _repair_flag(include_receipt, "include_receipt")
    if ferr:
        return _err(ferr)
    ledger, ferr = _repair_flag(ledger, "ledger")
    if ferr:
        return _err(ferr)
    verify, ferr = _repair_flag(verify, "verify")
    if ferr:
        return _err(ferr)
    fmts, ferr = _asm_export_formats(formats)
    if ferr:
        return _err(ferr)
    nm, nwarn = _asm_export_name(name)
    if nm is None:
        return _err(nwarn)
    warnings = [nwarn] if nwarn else []
    if out_dir is not None and not isinstance(out_dir, (str, os.PathLike)):
        return _err("out_dir 必须是路径字符串，收到 %r" % (out_dir,))
    if out_dir:
        od = _path(str(out_dir))
    else:
        K = _kernel()
        base = getattr(K, "out_dir", None) if K is not None else None
        od = _path(os.path.join(base or os.path.expanduser("~"), "asm_export"))
        warnings.append("out_dir 未给 → 默认 %s" % od)
    m = None
    if manifest is not None:
        m, merr = _load_manifest(manifest)
        if merr:
            return _err(merr)
        mval = _manifest_validate(m, check_files=bool(merge), check_md5=bool(merge))
        if not mval["ok"]:
            return _err("manifest 校验失败（坏清单上不导出）", errors=mval["errors"][:10],
                        warnings=mval["warnings"][:10])
    if merge and m is None:
        return _err("merge=true 必须给 manifest（N 路合并需要零件清单）")
    merge_out_blend = os.path.join(od, nm + "_merged.blend")
    merge_rec = None
    if merge:
        merge_call = dict(manifest=m, out_blend=merge_out_blend, dry_run=bool(dry_run))
        raw_merge = asm_merge(**merge_call)
        merge_rec = json.loads(raw_merge) if isinstance(raw_merge, str) else raw_merge
        if not isinstance(merge_rec, dict) or not merge_rec.get("ok"):
            gv = ((merge_rec or {}).get("gate") or {}).get("verdict") if isinstance(merge_rec, dict) else None
            body = {"ok": False, "verdict": gv or "fail", "dry_run": bool(dry_run),
                    "stage": "merge", "dir": od, "name": nm, "formats": fmts,
                    "files": {}, "counts": {"objects": 0, "files": 0},
                    "gate": _asm_gate_slim((merge_rec or {}).get("gate")),
                    "merge": _asm_merge_slim(merge_rec),
                    "md5": {}, "warnings": warnings,
                    "error": "合并失败/装配门未通过 ⇒ 不导出",
                    "verdict_line": "asm_export：合并阶段失败（%s）→ 未导出任何文件"
                                    % str((merge_rec or {}).get("error"))[:160],
                    "hint": "先看 merge.gate / merge.error；修 manifest/几何后重跑；merge 失败不保存 .blend",
                    "honest_limits": list(_ASM_EXPORT_LIMITS)}
            return _j(_receipt(body, t0))
    # ---- 对象解析 ----
    names = []
    objs = []
    info = None
    if merge and dry_run:
        for p in merge_rec.get("parts") or []:
            tn = p.get("target_names") if isinstance(p, dict) else None
            if isinstance(tn, dict):
                names.extend(str(v) for v in tn.values())
        if not names:
            names = [str(x.get("object")) for x in (merge_rec.get("merged") or [])
                     if isinstance(x, dict) and x.get("object")]
        if not names:
            return _err("合并计划里没有可导出的对象（空合并）", merge=_asm_merge_slim(merge_rec))
    else:
        if merge:
            names = [str(x.get("object")) for x in (merge_rec.get("merged") or [])
                     if isinstance(x, dict) and x.get("object")]
            if not names:
                return _err("合并完成但回执里没有 merged 对象（异常）", merge=_asm_merge_slim(merge_rec))
        info = _pick_objects(scope, objects, include_hidden=False)
        if info.get("error"):
            return _err(info["error"], objects=info.get("names"), missing=info.get("missing"),
                        hint="空场景/坏参数必须失败；确认对象名/集合名")
        names = list(info["names"])
        objs = list(info["objects"])
    gate = None
    if merge and isinstance(merge_rec.get("gate"), dict):
        gate = merge_rec.get("gate")
    elif not (merge and dry_run):
        try:
            raw_g = asm_gate(objects=list(names), manifest=m, summary_only=True, strict=True)
            gate = json.loads(raw_g) if isinstance(raw_g, str) else raw_g
        except Exception as e:
            gate = {"ok": False, "verdict": "degraded",
                    "error": "asm_gate 异常: %s: %s" % (type(e).__name__, str(e)[:200])}
    gate_slim = _asm_gate_slim(gate)
    merge_slim = _asm_merge_slim(merge_rec)
    planned = _asm_export_paths(od, nm, fmts, include_receipt, ledger)
    if merge:
        planned["blend"] = merge_out_blend
    # ---- dry_run：只回计划 ----
    if dry_run:
        body = {"ok": True, "verdict": "planned", "dry_run": True, "readonly": True,
                "stage": "planned", "dir": od, "name": nm, "formats": fmts,
                "files": planned, "objects": list(names)[:200],
                "counts": {"objects": len(names), "formats": len(fmts)},
                "gate": gate_slim, "merge": merge_slim, "md5": {}, "manifest_md5": None,
                "warnings": warnings + (["merge dry_run：gate 跳过（gate=skipped_dry_run）"] if merge else []),
                "verify": bool(verify),
                "verdict_line": "asm_export（dry_run，只读）：计划 %d 件 → %s；不建目录/不导出/不写账本"
                                % (len(names), "/".join(fmts)),
                "hint": "dry_run 只回计划；确认后传 dry_run=false（merge=true 会先 asm_merge(dry_run=false)）",
                "honest_limits": list(_ASM_EXPORT_LIMITS)}
        return _j(_receipt(body, t0))
    # ---- 真实导出 ----
    try:
        os.makedirs(od, exist_ok=True)
    except Exception as e:
        return _err("产物目录建不出来: %s: %s" % (type(e).__name__, str(e)[:200]), dir=od)
    exports, files, md5map = {}, {}, {}
    ok_formats, failed = [], []
    for fmt in fmts:
        path = planned[fmt]
        try:
            rec = _asm_export_one(fmt, objs, path)
            if verify:
                v = _asm_verify_file(fmt, path)
                rec["verify"] = v
                rec["magic_ok"] = v.get("magic_ok")
                rec["ok"] = bool(v.get("ok"))
                if rec.get("md5") is None:
                    rec["md5"] = v.get("md5")
                if not rec["ok"]:
                    rec["error"] = v.get("error") or "verify 失败"
            else:
                rec["verify"] = {"skipped": True, "reason": "verify=false（只查存在与非空）"}
                rec["ok"] = bool(rec.get("exists") and (rec.get("bytes") or 0) > 0)
                if not rec["ok"]:
                    rec["error"] = "文件不存在或为空"
        except Exception as e:
            rec = {"ok": False, "path": path,
                   "error": "%s: %s" % (type(e).__name__, str(e)[:240]),
                   "verify": {"skipped": True, "reason": "导出异常"}}
        exports[fmt] = rec
        files[fmt] = path
        if rec.get("ok"):
            ok_formats.append(fmt)
            md5map[fmt] = rec.get("md5")
            if fmt == "obj":
                mtl = os.path.splitext(path)[0] + ".mtl"
                if os.path.isfile(mtl):
                    files["obj_mtl"] = mtl
                    md5map["obj_mtl"] = _asm_md5_file(mtl)
        else:
            failed.append(fmt)
            warnings.append("%s 导出/校验失败：%s" % (fmt, str(rec.get("error"))[:180]))
    mmu = float(_KIT.units())
    md, md_errors = _mesh_map(objs)
    summary = _asm_mesh_summary(names, md, mmu)
    if summary.get("missing"):
        warnings.append("以下对象没有求值网格（manifest 里标 missing）：%s" % summary["missing"][:10])
    manifest_path = planned.get("manifest")
    ledger_path = planned.get("ledger")
    if include_receipt:
        files["manifest"] = manifest_path
    if ledger:
        files["ledger"] = ledger_path
    if merge:
        files["blend"] = ((merge_slim or {}).get("outputs") or {}).get("blend") or merge_out_blend
    mid = None
    if include_receipt:
        manifest = {
            "schema": "dsh-assembly-export-manifest/1", "tool": "assembly.asm_export",
            "version": ASSEMBLY_VERSION, "created": time.strftime("%Y-%m-%d %H:%M:%S"),
            "name": nm, "dir": od, "units": {"mm_per_unit": _r(mmu, 6)},
            "formats": fmts, "formats_ok": ok_formats, "formats_failed": failed,
            "objects": summary["objects"], "missing": summary.get("missing") or [],
            "counts": dict(summary["counts"], files=len(ok_formats)),
            "bbox_units": summary.get("bbox_units"), "bbox_mm": summary.get("bbox_mm"),
            "gate": gate_slim, "merge": merge_slim,
            "files": {k: {"path": v, "name": os.path.basename(str(v)),
                          "bytes": (exports.get(k) or {}).get("bytes"),
                          "md5": (exports.get(k) or {}).get("md5"),
                          "verify": _asm_export_slim(exports.get(k)) or {}}
                      for k, v in files.items() if k in ("glb", "fbx", "obj", "obj_mtl") and v},
            "exports": {k: _asm_export_slim(v) for k, v in exports.items()},
            "warnings": warnings,
            "honest_limits": list(_ASM_EXPORT_LIMITS),
        }
        # files 里 verify 字段瘦身（避免 manifest 嵌套过深）
        for k, v in (manifest.get("files") or {}).items():
            if isinstance(v.get("verify"), dict):
                v["verify"] = {kk: v["verify"].get(kk) for kk in ("ok", "magic_ok", "bytes", "md5")}
        manifest["manifest_md5"] = _md5_text(_canon({k: v for k, v in manifest.items()
                                                     if k != "manifest_md5"}))
        mid = manifest["manifest_md5"]
        try:
            _write_json(manifest_path, manifest)
            md5map["manifest"] = _asm_md5_file(manifest_path)
        except Exception as e:
            warnings.append("manifest 写失败：%s: %s" % (type(e).__name__, str(e)[:200]))
            files["manifest"] = None
    ledger_rec = None
    if ledger:
        ts = time.strftime("%Y-%m-%d %H:%M:%S")
        events = []
        if merge and merge_slim is not None:
            events.append({
                "ts": ts, "action": "merge", "name": nm,
                "manifest_md5": merge_slim.get("manifest_md5"),
                "files": [os.path.basename(str(merge_slim.get("outputs", {}).get("blend") or merge_out_blend))]
                if isinstance(merge_slim.get("outputs"), dict) else [os.path.basename(merge_out_blend)],
                "md5": {"blend": (merge_slim.get("outputs") or {}).get("md5")
                        if isinstance(merge_slim.get("outputs"), dict) else None},
                "gate_verdict": (merge_slim.get("gate") or {}).get("verdict"),
                "objects": merge_slim.get("objects_total"),
                "tool": "asm_merge", "version": ASSEMBLY_VERSION})
        events.append({
            "ts": ts, "action": "export", "name": nm, "dir": od,
            "manifest_md5": mid,
            "files": [os.path.basename(str(files[k])) for k in ok_formats if files.get(k)],
            "md5": {k: md5map.get(k) for k in ok_formats},
            "gate_verdict": (gate_slim or {}).get("verdict"),
            "objects": summary["counts"].get("objects"),
            "formats": ok_formats, "failed": failed,
            "tool": "asm_export", "version": ASSEMBLY_VERSION})
        ledger_rec = _asm_ledger_append(ledger_path, events)
        if not ledger_rec.get("ok"):
            warnings.append("ledger 追加失败：%s" % str(ledger_rec.get("error"))[:200])
            files["ledger"] = None
        else:
            md5map["ledger"] = _asm_md5_file(ledger_path)
    if not ok_formats:
        verdict = "fail"
        warnings.append("全部格式都失败 ⇒ ok:false（看 exports[].error）")
    else:
        verdict = (gate_slim or {}).get("verdict") or "unverified"
        if verdict != "pass":
            warnings.append("gate verdict=%s（≠ pass）：文件已导出但不可直接当作出厂件交付" % verdict)
    ok = bool(ok_formats)
    body = {"ok": ok, "verdict": verdict, "dry_run": False, "stage": "exported",
            "dir": od, "name": nm, "formats": fmts,
            "formats_ok": ok_formats, "formats_failed": failed,
            "files": files, "counts": dict(summary["counts"], files=len(ok_formats)),
            "objects": list(names)[:200], "gate": gate_slim, "merge": merge_slim,
            "md5": md5map, "manifest_md5": mid,
            "exports": {k: _asm_export_slim(v) for k, v in exports.items()},
            "warnings": warnings, "verify": bool(verify), "ledger": ledger_rec,
            "verdict_line": ("asm_export：%d 件 → %s（成功 %s / 失败 %s）· gate=%s · manifest=%s · ledger=%s"
                             % (len(names), "/".join(fmts), "/".join(ok_formats) or "无",
                                "/".join(failed) or "无", verdict,
                                "written" if mid else "skipped",
                                "appended" if (ledger_rec or {}).get("ok") else "skipped")),
            "hint": ("导出完成：先读 verdict（degraded≠通过）与 warnings；manifest 里有逐文件 md5/bbox")
                    if ok else "全部格式失败：看 exports[].error（GLB 需 io_scene_gltf2 / FBX 需 io_scene_fbx）",
            "honest_limits": list(_ASM_EXPORT_LIMITS), "md_errors": md_errors}
    return _j(_receipt(body, t0))


# ────────────────────────────── selftest / help ──────────────────────────────
def _selftest_box(name, size, loc):
    import bmesh
    me = bpy.data.meshes.new(name)
    bm = bmesh.new()
    bmesh.ops.create_cube(bm, size=1.0)
    for v in bm.verts:
        v.co *= size
    bm.to_mesh(me)
    bm.free()
    ob = bpy.data.objects.new(name, me)
    bpy.context.scene.collection.objects.link(ob)
    ob.location = loc
    return ob


def asm_selftest():
    """模块内快速自检：clash 分类 / manifest 往返 / merge dry_run / 坏参数 ok:false。"""
    import tempfile
    ev = {}
    t0 = time.perf_counter()
    made, tmpdir = [], None
    prev_scale = float(bpy.context.scene.unit_settings.scale_length)
    try:
        bpy.context.scene.unit_settings.scale_length = 0.001
        a = _selftest_box("__dsh_asm_a", 20.0, (0, 0, 0))
        b = _selftest_box("__dsh_asm_b", 20.0, (19.0, 0, 0))
        made += [a, b]
        try:
            bpy.context.view_layer.update()
        except Exception:
            pass
        r = json.loads(asm_scan(objects=["__dsh_asm_a", "__dsh_asm_b"], summary_only=False,
                                max_pairs=10, report_gap_mm=2.0))
        by = {}
        for p in r.get("pairs") or []:
            by[tuple(sorted([p.get("a"), p.get("b")]))] = p
        rec = by.get(("__dsh_asm_a", "__dsh_asm_b")) or {}
        ev["scan_clash"] = rec.get("class") == "clash"
        ev["scan_depth_measured"] = rec.get("depth_mm") is not None
        ev["scan_verdict_fail"] = r.get("verdict") == "fail"
        r_bad = json.loads(asm_scan(objects=["__dsh_asm_nope"]))
        ev["scan_bad_input_okfalse"] = r_bad.get("ok") is False
        tmpdir = tempfile.mkdtemp(prefix="dsh_asm_selftest_")
        partfile = os.path.join(tmpdir, "part_a.blend")
        bpy.ops.wm.save_as_mainfile(filepath=partfile, copy=True)
        man = os.path.join(tmpdir, "assembly.json")
        m1 = json.loads(asm_manifest(action="init", path=man, assembly="selftest", force=True))
        m2 = json.loads(asm_manifest(action="add_part", path=man,
                                     part={"id": "P1", "file": partfile, "objects": ["__dsh_asm_a"]}))
        m3 = json.loads(asm_manifest(action="seal", path=man))
        ev["manifest_roundtrip"] = bool(m1.get("ok") and m2.get("ok") and m3.get("ok"))
        ev["manifest_sealed"] = bool(m3.get("sealed"))
        dry = json.loads(asm_merge(manifest=man, out_blend=os.path.join(tmpdir, "merged.blend"),
                                   dry_run=True))
        ev["merge_dry_run"] = bool(dry.get("ok") and dry.get("dry_run") is True)
        ev["merge_dry_no_write"] = not os.path.isfile(os.path.join(tmpdir, "merged.blend"))
        ev["merge_order"] = dry.get("order")
        ev["merge_target_names"] = (dry.get("parts") or [{}])[0].get("target_names")
        g_bad = json.loads(asm_gate(objects=[]))
        ev["gate_bad_input_okfalse"] = g_bad.get("ok") is False
        ok = all(v is True for k, v in ev.items() if k not in ("merge_order", "merge_target_names"))
        return _j({"ok": bool(ok), "version": ASSEMBLY_VERSION, "evidence": ev,
                   "ms": int((time.perf_counter() - t0) * 1000)})
    except Exception as e:
        import traceback
        return _j({"ok": False, "version": ASSEMBLY_VERSION, "evidence": ev,
                   "error": "%s: %s" % (type(e).__name__, str(e)[:240]),
                   "traceback": traceback.format_exc()[-600:]})
    finally:
        bpy.context.scene.unit_settings.scale_length = prev_scale
        for nm in [str(o.name) for o in made]:
            ob = bpy.data.objects.get(nm)
            if ob is not None:
                me = ob.data
                bpy.data.objects.remove(ob, do_unlink=True)
                if me is not None and me.users == 0:
                    try:
                        bpy.data.meshes.remove(me, do_unlink=True)
                    except Exception:
                        pass
        if tmpdir:
            try:
                import shutil
                shutil.rmtree(tmpdir, ignore_errors=True)
            except Exception:
                pass


def asm_help():
    return _j({
        "module": "assembly.py", "version": ASSEMBLY_VERSION,
        "what": "装配层：全对全穿模/包含分类扫描 + 零件清单 + N 路合并 + 装配出厂门（verdict-first）",
        "ops": {
            "scan": {"alias": "audit_clash_all",
                     "sig": "scope|objects|file, manifest?, spec?, limit=0, max_pairs=20000, "
                            "depth_eps_mm=0.2, contact_eps_mm=0.3, report_gap_mm=5.0, summary_only=false",
                     "out": "verdict + counts{clash,hidden_ok,contact,gap,float,contained,unresolved} + "
                            "pairs + matrix(sparse) + coverage{complete,truncated,budget_hit} + offenders + repairs",
                     "why": "旧全对全在 tri 不相交时 continue ⇒ 完全包含漏判；本 op 补 containment 分支 + 逐对分类 + allow 白名单"},
            "manifest": {"action": "init|add_part|add_interface|add_allow|validate|seal|show",
                         "sig": "path, assembly, part={id,file,objects,owner,transform,interface_frames}, "
                                "interfaces=[{id,a,b,frame_a,frame_b,fit,nominal_mm,tol_mm,allow_overlap_mm}], "
                                "allow=[{a,b,max_depth_mm,max_volume_mm3,reason}], check_files=true, check_md5=true",
                         "out": "errors[]/warnings[]/manifest_md5/verdict；校验 id 唯一/file/file_md5/接口两端/basis 正交/单位"},
            "merge": {"sig": "manifest, out_blend, dry_run=true, transform_policy=manifest|as_is|snap_interface, "
                             "snap_tol_mm=2.0, recalc_normals=true, snapshot=true, gate=true, strict=true",
                      "flow": "校验 → (snapshot) → 按 part id 字典序 append → 改名 <partid>__<原名> → 变换 → "
                              "recalc normals → asm_gate → 通过才 save + merge_receipt.json；失败不保存"},
            "gate": {"alias": "merge_gate",
                     "sig": "scope|objects, manifest?, spec?, require{}, summary_only=true, strict=true, "
                            "tolerance_mm=0.5",
                     "gates": "mesh_health + normals + clash_all + floaters + interfaces + measure → 三态；"
                              "export_allowed 仅 verdict=pass"},
            "repair": {"alias": "fix_clash", "planner": "fix_interference",
                       "policy": "缺省（不打 policy）= safe 行为：自动只做 translate/snap，"
                                 "布尔保留 allow_boolean=true 显式授权（兼容旧调用）"
                                 "| 显式 policy=safe：硬禁布尔/scale"
                                 "| manual：只规划、绝不自动执行、不规划布尔"
                                 "| aggressive：保留旧行为，布尔受 allow_boolean 控制",
                       "sig": "scope|objects|manifest, spec?, dry_run=true, max_move_mm=50.0, "
                              "allow_boolean=false, snapshot=true, max_rounds=3, clearance_mm=0.5, policy",
                       "flow": "asm_scan → 规划（clash/contained：体积小件沿 AABB 交叠最小轴平移 "
                               "AABB 分离所需量+clearance；float：audit_snap_floaters 计划；allow_boolean 且两件闭合"
                               "流形：从较大件布尔减较小件）→（dry_run=false）文件级 snapshot → 逐动作 apply "
                               "→ view_layer.update → 重跑 asm_scan，最多 max_rounds 轮",
                       "out": "dry_run=true：plan[]/blocked[]/expected（只读，场景不变）；"
                              "dry_run=false：before/after counts + residual + applied + blocked + snapshot",
                       "verdict": "dry_run：planned（有动作）/ blocked（无动作）/ pass（本来没坏）；"
                                  "执行后：残留 clash/contained/float=0 ⇒ ok:true 且 verdict=复扫三态，"
                                  "有残留 ⇒ ok:false + residual",
                       "safety": "dry_run 默认 true；布尔只在 allow_boolean=true 且两件闭合流形时规划；"
                                 "布尔执行必须有可用文件级快照（K.dsh_txn_api），否则拒绝；"
                                 "目标件有父级/动画 ⇒ blocked（整件平移会被覆盖）；"
                                 "plan[].risk / blocked[].blocked_reason 逐条可读",
                       "risk_policy": "safe 不自动做布尔/scale；manual 只出计划；aggressive 才保留 allow_boolean 布尔路径"},
            "fix_interference": {"planner_only": True,
                                 "sig": "a?, b?, plan_only=true, manifest?, scope?, objects?, spec?, "
                                        "max_move_mm=50.0, allow_boolean=false, clearance_mm=0.5, max_rounds=3, "
                                        "policy=safe",
                                 "out": "plan[]/blocked[]/expected（只读；a/b 可只保留该对）",
                                 "why": "asm_scan / asm_gate 的 repairs 里给的就是这个规划器调用；执行用 fix_clash"},
            "export": {"alias": "asm_export",
                       "sig": "manifest?, scope|objects, out_dir?, name=assembly, formats=[glb,fbx,obj], "
                              "merge=false, dry_run=false, include_receipt=true, ledger=true, verify=true",
                       "flow": "merge=true 且 dry_run=false → 先 asm_merge(dry_run=false)（内部 gate+snapshot，不过门不导出）"
                               "→ 官方导出器写 GLB/FBX/OBJ（自动 enable io_scene_gltf2/io_scene_fbx）"
                               "→ <out_dir>/<name>_manifest.json + append-only <out_dir>/merge_ledger.jsonl",
                       "out": "{ok, verdict, dir, files{glb,fbx,obj,manifest,ledger,blend,obj_mtl}, counts, "
                              "gate, merge, md5, manifest_md5, warnings}；dry_run=true 只回计划不写盘",
                       "verdict": "gate 三态；全部格式失败 ⇒ ok:false；部分失败 ⇒ ok:true + warnings",
                       "safety": "dry_run=true 绝不写盘/建目录；manifest 记录逐文件 md5/bytes/gate/merge receipt；"
                                 "ledger 只 append 不重写"},
            "selftest": {"sig": "无参", "out": "模块内快速自检（clash/manifest/merge dry_run/坏参数）"},
        },
        "verdict_rules": {
            "scan": "clash>0 或 contained>0 或 float>0 ⇒ fail；unresolved/截断/未判完对象 ⇒ degraded；否则 pass",
            "gate": "任一 fail ⇒ fail；任一 degraded/skipped ⇒ degraded；全部 pass ⇒ pass；"
                    "degraded ≠ 通过，export_allowed 仅 pass",
            "unresolved": "证据不足（开网格 / 法线朝内 / 未测）一律 unresolved，不得当 pass",
        },
        "manifest_schema": {"schema": MANIFEST_SCHEMA, "units": "mm", "coord": {"up": "Z", "forward": "-Y"},
                            "parts": "id/file/objects/owner/file_md5/transform{loc_mm,rot_euler_deg,scale}/interface_frames",
                            "interfaces": "id/a/b/frame_a/frame_b/fit/nominal_mm/tol_mm/allow_overlap_mm/clearance_min_mm",
                            "allow_pairs": "id/a/b/max_depth_mm/max_volume_mm3/reason"},
        "protocol": [
            "多会话并行：分件方各自 save parts/*.blend → Lead 用 asm_manifest init/add_part/add_interface/seal → asm_merge(dry_run 看计划) → asm_merge(dry_run=false) 自动过 asm_gate",
            "allow 唯一来源是 manifest/spec（跨进程可复现），不要指望 K.dsh_contract 的会话内 declared 状态",
            "设计内隐藏交叠写 spec.allow（max_depth_mm/max_volume_mm3）；不许用全局阈值放过",
            "gate 不过 ⇒ 不保存；修法看 offenders/repairs（fix_interference / fix_repair / 调接口帧）",
        ],
        "deps": {"required": "K.dsh_kit",
                 "optional": "contract（深测复用）/audit（mesh+connectivity+measure）/clearance/gate/txn（merge 快照）"},
        "honest_limits": ["采样式深度/体积是估计值", "完全包含按实心口径报 contained（空心壳需人核）",
                          "开网格/法线朝内 ⇒ unresolved", "全对全不算接触面积", "shots 本版未实现"],
    })


# ────────────────────────────── dispatch / 注册 ──────────────────────────────
_ALIASES = {"audit_clash_all": "scan", "merge_gate": "gate", "fix_clash": "repair",
            "asm_export": "export"}
_OPS = {"scan": asm_scan, "audit_clash_all": asm_scan, "manifest": asm_manifest,
        "merge": asm_merge, "gate": asm_gate, "merge_gate": asm_gate,
        "repair": asm_repair, "fix_clash": asm_repair, "fix_interference": fix_interference,
        "export": asm_export, "asm_export": asm_export,
        "selftest": asm_selftest, "help": asm_help}


def _asm_dispatch(op, args_json=None):
    kw = _KIT.flatten(args_json)
    try:
        kw = _KIT.resolve_refs(kw)
    except KeyError as e:
        return _j({"ok": False, "error": "引用解析失败: %s" % str(e)[:160], "op": op})
    name = str(op or "").strip()
    key = _ALIASES.get(name, name)
    fn = _OPS.get(key)
    if fn is None:
        try:
            import difflib
            cand = difflib.get_close_matches(key, sorted(set(_OPS)), n=3, cutoff=0.4)
        except Exception:
            cand = []
        return _j({"ok": False, "error": "unknown assembly op", "op": name,
                   "ops": sorted(set(_OPS)), "aliases": _ALIASES, "did_you_mean": cand})
    import inspect
    try:
        allowed = set(inspect.signature(fn).parameters)
    except (TypeError, ValueError):
        allowed = None
    if allowed is not None:
        unknown = sorted(set(kw) - allowed)
        if unknown:
            return _j({"ok": False, "error": "不认识的参数 %s" % unknown, "op": name,
                       "allowed": sorted(allowed)})
    try:
        out = fn(**kw)
    except TypeError as e:
        return _j({"ok": False, "error": "参数不匹配: %s" % str(e)[:220], "op": name,
                   "allowed": sorted(allowed or [])})
    except Exception as e:
        import traceback
        return _j({"ok": False, "error": "%s: %s" % (type(e).__name__, str(e)[:240]),
                   "op": name, "traceback": traceback.format_exc()[-600:]})
    return out if isinstance(out, str) else _j(out)


_KIT.register("assembly", ASSEMBLY_VERSION, _OPS, dispatch=_asm_dispatch)
