# -*- coding: utf-8 -*-
"""Split from audit.py; behavior preserved by the facade."""
from .common import *  # noqa: F401,F403

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
# v0.9.1：file= 临时加载 —— 把"会话绑定"解开（上报一律用文件里的原名）
# ----------------------------------------------------------------------------
# 来源：rifle-build《93 反馈》："check_* / audit_* 都要先在会话里注册组件，批处理 12 个
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


def __load_file_objects_p1(st):
    path = st["path"]
    try:
        import os  # noqa: F401  # handed to the next split stage through st
        src = str(path or "")
        before = _id_snapshot()
        before_counts = _id_counts()
        state = {"done": False, "report": None}
        loaded, coll, objs, name_of = [], None, [], {}
        _shared = {"loaded": loaded, "coll": coll}   # cleanup 闭包与后续 helper 共享这两个会重绑定的引用
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

    finally:
        st.update((k, v) for k, v in locals().items() if k != "st")

def __load_file_objects_p2(st):
    _shared, before, before_counts, mine, mine_ids = (
        st.get("_shared"), st.get("before"), st.get("before_counts"), st.get("mine"), st.get("mine_ids"))
    state = st.get("state")
    try:
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
            for ob in list(_shared["loaded"]) + list(mine["objects"]):
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
            _top_coll = _shared["coll"]
            if _top_coll is not None:
                cname = str(_top_coll.name)
                try:
                    bpy.data.collections.remove(_top_coll, do_unlink=True)
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

    finally:
        st.update((k, v) for k, v in locals().items() if k != "st")

def __load_file_objects_p3(st):
    _capture_mine, _shared, cleanup, include_hidden, info = (
        st.get("_capture_mine"), st.get("_shared"), st.get("cleanup"), st["include_hidden"], st.get("info"))
    names, os, src = st["names"], st.get("os"), st.get("src")
    all_names, coll, dst, keep, name_of, o, src_ctx, want = (st.get("all_names"), st.get("coll"), st.get("dst"), st.get("keep"), st.get("name_of"), st.get("o"), st.get("src_ctx"), st.get("want"))
    try:
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
        _shared["loaded"] = loaded
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
            _shared["coll"] = coll
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
    finally:
        st.update((k, v) for k, v in locals().items() if k != "st")

def _load_file_objects(path, names=None, include_hidden=False):
    """把另一个 .blend 里的对象**临时**追加进当前会话 → (objs, cleanup, info)。

    * 只取对象（不取场景/集合/世界）；names 非空就只取这些名字，文件里没有的名字进 info.missing。
    * 可见性：include_hidden=False 时按 hide_render/hide_viewport 过滤（与 _objs_for 同口径）。
    * cleanup() **幂等**，返回本次实际删掉的清单（返回体的 cleanup 字段就是它）；
      删：本次加载的对象（含非 mesh）/临时集合/本次新造且 users==0 的数据块/本次新出现的库条目。
    * info["_name_of"] = {会话内名字 → 文件原名}，调用方 pop 出来做上报映射（别塞进 JSON）。
    * 空文件 / 文件里没有（可见）mesh / 打不开 → info.ok=False + error（原始错误照抄）。
    """
    st = dict(locals())
    for _fn_ in (__load_file_objects_p1, __load_file_objects_p2, __load_file_objects_p3):
        _r = _fn_(st)
        if _r is not None:
            return _r


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
    """三个 op 的统一入口：file= 时临时加载该文件（names 用来在文件内筛），否则走 _objs_for（会话）。

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
