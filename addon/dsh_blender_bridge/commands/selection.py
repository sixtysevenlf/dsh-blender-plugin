""" Selection and collection payload commands. """

import bpy
import time
import requests
import os

from ..compat import SELECTION_COLLECTION_ROWS, SELECTION_PAYLOAD_NAMES, SELECTION_PAYLOAD_OBJECTS, _read_sub_selection
from ..config import _dshbb_backend_url
from ..recorders.activity import get_activity_tracker

def _scene_meta():
    """(scene_name, blend_basename) — shared by both payload builders."""
    scene_name = None
    try:
        scene_name = bpy.context.scene.name if bpy.context.scene else None
    except Exception:
        scene_name = None
    file_name = None
    try:
        filepath = bpy.data.filepath
        file_name = os.path.basename(filepath) if filepath else None
    except Exception:
        file_name = None
    return {"name": scene_name, "file": file_name}


def _best_collection_for_selection():
    """当前选中对象里"最代表"的集合（出现在最多选中对象上的那个）；没有就 None。"""
    try:
        selected = list(bpy.context.selected_objects or [])
    except Exception:
        selected = []
    counts = {}
    for obj in selected:
        try:
            collections = list(obj.users_collection or [])
        except Exception:
            collections = []
        for coll in collections:
            try:
                counts[coll.name] = counts.get(coll.name, 0) + 1
            except Exception:
                continue
    if not counts:
        return None
    name = sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))[0][0]
    try:
        return bpy.data.collections.get(name)
    except Exception:
        return None


def _on_selection_mode_update(self, context):
    """切到「集合」模式时：若还没选集合，自动用当前选中对象最常出现的集合。"""
    try:
        if getattr(self, "dshbb_selection_mode", "MULTI") == "COLLECTION" and getattr(self, "dshbb_collection_target", None) is None:
            best = _best_collection_for_selection()
            if best is not None:
                self.dshbb_collection_target = best
    except Exception:
        pass


def _selection_collection_summary(selected, cap=SELECTION_COLLECTION_ROWS):
    """Selected objects grouped by their direct collections: [{name, count}] (count desc)."""
    counts = {}
    for obj in selected:
        seen = set()
        try:
            collections = list(obj.users_collection or [])
        except Exception:
            collections = []
        for coll in collections:
            try:
                coll_name = coll.name
            except Exception:
                continue
            if coll_name in seen:
                continue
            seen.add(coll_name)
            counts[coll_name] = counts.get(coll_name, 0) + 1
    rows = sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))[:cap]
    return [{"name": name, "count": count} for name, count in rows]


def _scene_objects_within(selected, cap=SELECTION_PAYLOAD_OBJECTS):
    """Type + mm dimensions for up to `cap` selected objects."""
    objects = []
    for obj in selected[:cap]:
        entry = {"name": obj.name, "type": getattr(obj, "type", "UNKNOWN")}
        try:
            dims = obj.dimensions
            entry["dimensions_mm"] = [round(float(dims.x) * 1000.0, 1),
                                      round(float(dims.y) * 1000.0, 1),
                                      round(float(dims.z) * 1000.0, 1)]
        except Exception:
            pass
        objects.append(entry)
    return objects


def build_selection_payload(include_current_command=True):
    """Build the v1 selection payload pushed to `POST /host/selection` (no network)."""
    tracker = get_activity_tracker()
    selection = tracker.tick(force=True) or {"count": 0, "names": [], "active": None,
                                             "mode": "UNKNOWN", "truncated": False}
    try:
        selected = list(bpy.context.selected_objects or [])
    except Exception:
        selected = []
    selection_payload = {
        "kind": "objects",
        "count": selection.get("count", 0),
        "names": list(selection.get("names") or []),
        "truncated": bool(selection.get("truncated")),
        "active": selection.get("active"),
        "mode": selection.get("mode"),
        "objects": _scene_objects_within(selected),
        # v2.3：选中对象分布在哪些集合（多件装配体时让 DSH/模型先看到"集合"这一层）
        "collections": _selection_collection_summary(selected),
    }
    # v2.4：编辑/姿态模式的子元素（顶点/边/面、骨骼、曲线/晶格点）
    sub = _read_sub_selection()
    if sub is not None:
        selection_payload["sub"] = sub
    return {
        "v": 1,
        "source": "blender-addon",
        "sent_at": int(round(time.time() * 1000)),
        "selection": selection_payload,
        "scene": _scene_meta(),
        "activity": tracker.snapshot(include_current_command=include_current_command),
    }


def build_collection_payload(collection_name, include_current_command=True):
    """Build a payload that sends one *collection* (instead of individual objects).

    返回 None 表示集合不存在。大装配体场景下，发"集合 + 成员数"比发几百个对象名更有用；
    成员名单仍带前 200 个，客户端文本只展示前若干个。
    """
    name = str(collection_name or "").strip()
    if not name:
        return None
    try:
        collection = bpy.data.collections.get(name)
    except Exception:
        collection = None
    if collection is None:
        return None
    try:
        members = list(collection.objects)
    except Exception:
        members = []
    names = [obj.name for obj in members[:SELECTION_PAYLOAD_NAMES]]
    try:
        mode = str(bpy.context.mode)
    except Exception:
        mode = "UNKNOWN"
    return {
        "v": 1,
        "source": "blender-addon",
        "sent_at": int(round(time.time() * 1000)),
        "selection": {
            "kind": "collection",
            "collection": name,
            "count": len(members),
            "names": names,
            "truncated": len(members) > len(names),
            "active": None,
            "mode": mode,
            "objects": _scene_objects_within(members),
            "collections": [{"name": name, "count": len(members)}],
        },
        "scene": _scene_meta(),
        "activity": get_activity_tracker().snapshot(include_current_command=include_current_command),
    }


def build_collections_payload(limit=200):
    """List scene collections with object counts for the N panel / model query."""
    try:
        selected_names = {obj.name for obj in (bpy.context.selected_objects or [])}
    except Exception:
        selected_names = set()
    rows = []
    try:
        all_collections = list(bpy.data.collections)
    except Exception:
        all_collections = []
    for collection in all_collections:
        try:
            members = list(collection.objects)
        except Exception:
            members = []
        parent = getattr(collection, "parent", None)
        rows.append({
            "name": collection.name,
            "count": len(members),
            "selected_count": sum(1 for obj in members if obj.name in selected_names),
            "parent": getattr(parent, "name", None) if parent else None,
        })
    rows.sort(key=lambda row: (-row["count"], row["name"]))
    try:
        limit = max(1, int(limit))
    except Exception:
        limit = 200
    return {"count": len(rows), "returned": min(len(rows), limit),
            "collections": rows[:limit], "scene": _scene_meta()}


def _post_selection_payload(payload, timeout=3.0):
    """POST one selection payload to the DSH backend (never raises)."""
    url = _dshbb_backend_url() + "/host/selection"
    result = {"ok": False, "backend_url": url, "payload": payload}
    try:
        response = requests.post(url, json=payload, timeout=float(timeout))
        result["status_code"] = int(response.status_code)
        try:
            body = response.json()
        except Exception:
            body = None
        if isinstance(body, dict):
            result["server"] = body
            if body.get("seq") is not None:
                result["seq"] = body.get("seq")
            if body.get("received_at") is not None:
                result["received_at"] = body.get("received_at")
            result["ok"] = bool(response.status_code == 200 and body.get("ok") is True)
            if not result["ok"]:
                result["error"] = str(body.get("error") or ("HTTP %s" % response.status_code))
        else:
            result["error"] = "HTTP %s（响应不是 JSON）" % response.status_code
        return result
    except Exception as exc:
        result["error"] = str(exc)
        result["hint"] = ("后端未运行或端口不同：确认 DSH 插件已加载且 127.0.0.1:9877 在监听；"
                          "可用 DSH_BLENDER_BACKEND_URL 或场景属性 dshbb_backend_url 覆盖")
        return result


def send_selection_to_dsh(timeout=3.0):
    """POST the current native selection to the DSH backend (used by the N-panel button)."""
    return _post_selection_payload(build_selection_payload(), timeout)


def send_collection_to_dsh(collection_name=None, timeout=3.0):
    """POST one collection (the whole set) to the DSH backend."""
    name = collection_name
    if not name:
        try:
            target = getattr(bpy.context.scene, "dshbb_collection_target", None)
            name = getattr(target, "name", None) if target else None
        except Exception:
            name = None
    payload = build_collection_payload(name)
    if payload is None:
        return {"ok": False, "error": "collection_not_found", "collection": name,
                "hint": "先在 N 面板「集合」下拉里选一个集合，或传 collection 名字"}
    return _post_selection_payload(payload, timeout)


_selection_send_state = {}


def _record_selection_send(result):
    global _selection_send_state
    selection = (result.get("payload") or {}).get("selection") or {}
    _selection_send_state = {
        "ok": bool(result.get("ok")),
        "seq": result.get("seq"),
        "error": result.get("error"),
        "backend_url": result.get("backend_url"),
        "at": time.time(),
        "count": int(selection.get("count") or 0),
        "kind": str(selection.get("kind") or "objects"),
        "collection": selection.get("collection"),
    }
    return result


class SelectionCommands:
    def get_selection(self):
        """v1.8：当前 Blender 原生选中（名字/类型/活动对象/模式/场景），供模型或面板查询。"""
        try:
            payload = build_selection_payload(include_current_command=False)
            out = dict(payload.get("selection") or {})
            scene = payload.get("scene") or {}
            out["scene"] = scene.get("name")
            out["file"] = scene.get("file")
            out["activity"] = payload.get("activity")
            return out
        except Exception as e:
            print(f"Error getting selection: {str(e)}")
            return {"error": str(e)}

    def send_selection_to_dsh(self, timeout: float = 3.0):
        """v1.8：把当前原生选中 POST 给 DSH 后端（N 面板按钮 / 模型调用同一实现）。"""
        try:
            return _record_selection_send(send_selection_to_dsh(timeout=timeout))
        except Exception as e:
            print(f"Error sending selection to DSH: {str(e)}")
            return {"ok": False, "error": str(e)}

    def get_collections(self, limit: int = 200):
        """v1.8：列场景集合（名字 / 对象数 / 其中被选中的数量），大装配体先选集合再发送。"""
        try:
            return build_collections_payload(limit=limit)
        except Exception as e:
            print(f"Error listing collections: {str(e)}")
            return {"error": str(e)}

    def send_collection_to_dsh(self, collection=None, timeout: float = 3.0):
        """v1.8：把一个集合整体 POST 给 DSH（集合名缺省取 N 面板下拉的 dshbb_collection_target）。"""
        try:
            return _record_selection_send(send_collection_to_dsh(collection_name=collection, timeout=timeout))
        except Exception as e:
            print(f"Error sending collection to DSH: {str(e)}")
            return {"ok": False, "error": str(e)}
