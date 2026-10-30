""" Activity tracking state machine and handlers. """

import bpy
import time
from contextlib import contextmanager, redirect_stdout, suppress
from bpy.app.handlers import persistent

from ..compat import SELECTION_PAYLOAD_NAMES
from .user_edit import _edit_recorder

HUMAN_ACTIVE_WINDOW_S = 1.5
AGENT_ACTIVE_WINDOW_S = 1.5
ACTIVITY_TICK_MIN_INTERVAL_S = 0.2


SELECTION_ACTIVITY_NAMES = 8


MAX_MODAL_OPERATORS = 8


class ActivityTracker:
    """Unified human/agent activity + native selection snapshot (main thread only)."""

    def __init__(self):
        self._recorder = None
        self._last_human_at = 0.0
        self._last_human_kind = None
        self._last_agent_at = 0.0
        self._last_tick = 0.0
        self._selection_fp = None
        self._selection = {"count": 0, "names": [], "active": None,
                           "mode": "OBJECT", "truncated": False}
        self._modal = []
        self._scene = None

    def attach(self, recorder):
        self._recorder = recorder
        self._resync()

    def _suppressed(self):
        return bool(self._recorder is not None and self._recorder._suppressed)

    @property
    def agent_depth(self):
        return 0 if self._recorder is None else int(self._recorder._agent_depth)

    def note_human(self, kind, now=None):
        """Record a human-originated action; agent-issued commands are ignored."""
        if self._suppressed():
            return
        self._last_human_at = time.time() if now is None else float(now)
        self._last_human_kind = str(kind or "activity")

    def note_agent_end(self, now=None):
        """Mark that an agent command just finished (so its side effects are not blamed on the human)."""
        self._last_agent_at = time.time() if now is None else float(now)

    def _resync(self):
        """Drop the previous selection fingerprint after agent commands (avoids a false 'human' event)."""
        self._selection_fp = None

    @staticmethod
    def _modal_operators():
        try:
            win = bpy.context.window
            ops = list(getattr(win, "modal_operators", []) or []) if win is not None else []
        except Exception:
            return []
        out = []
        for op in ops[:MAX_MODAL_OPERATORS]:
            try:
                out.append(str(getattr(op, "bl_idname", None) or getattr(op, "name", None) or "modal"))
            except Exception:
                out.append("modal")
        return out

    @staticmethod
    def _read_selection():
        try:
            selected = list(bpy.context.selected_objects or [])
        except Exception:
            selected = []
        count = len(selected)
        names = [obj.name for obj in selected[:SELECTION_PAYLOAD_NAMES]]
        try:
            active = getattr(bpy.context.active_object, "name", None)
        except Exception:
            active = None
        try:
            mode = str(bpy.context.mode)
        except Exception:
            mode = "UNKNOWN"
        return {"count": count, "names": names, "active": active, "mode": mode,
                "truncated": count > len(names)}

    def tick(self, now=None, force=False):
        """Poll modal state + native selection; throttled to ACTIVITY_TICK_MIN_INTERVAL_S."""
        now = time.time() if now is None else float(now)
        if not force and (now - self._last_tick) < ACTIVITY_TICK_MIN_INTERVAL_S:
            return self._selection
        self._last_tick = now
        self._modal = self._modal_operators()
        if self._modal and not self._suppressed():
            self.note_human("modal:" + self._modal[0], now)
        sel = self._read_selection()
        fingerprint = (sel["count"], sel["active"], sel["mode"],
                       None if sel["truncated"] else tuple(sel["names"]))
        if self._selection_fp is not None and fingerprint != self._selection_fp:
            # 刚被 agent 命令改过的选中不算人的操作（否则每次工具调用后都误报 human_active）
            if not self._suppressed() and not self.agent_active(now):
                self.note_human("selection", now)
        self._selection_fp = fingerprint
        self._selection = sel
        try:
            self._scene = bpy.context.scene.name if bpy.context.scene else None
        except Exception:
            self._scene = None
        return sel

    def human_active(self, now=None):
        now = time.time() if now is None else float(now)
        if self._modal:
            return True
        return (now - self._last_human_at) <= HUMAN_ACTIVE_WINDOW_S

    def agent_active(self, now=None):
        now = time.time() if now is None else float(now)
        return self.agent_depth > 0 or (now - self._last_agent_at) <= AGENT_ACTIVE_WINDOW_S

    def snapshot(self, now=None, include_current_command=True):
        """Unified activity snapshot.

        `include_current_command=False` is for **query commands** (get_activity_state /
        get_selection): a query must not report *itself* as "agent is operating", otherwise
        every model-facing query would always say `state=agent`.
        """
        now = time.time() if now is None else float(now)
        self.tick(now)
        depth = self.agent_depth
        if not include_current_command:
            depth = max(0, depth - 1)
        human = self.human_active(now)
        agent = depth > 0 or (now - self._last_agent_at) <= AGENT_ACTIVE_WINDOW_S
        state = "both" if (human and agent) else ("human" if human else ("agent" if agent else "idle"))
        human_idle = None if self._last_human_at <= 0 else max(0, int(round((now - self._last_human_at) * 1000)))
        agent_idle = None if self._last_agent_at <= 0 else max(0, int(round((now - self._last_agent_at) * 1000)))
        sel = dict(self._selection)
        sel["names"] = list(sel.get("names") or [])[:SELECTION_ACTIVITY_NAMES]
        sel["truncated"] = bool(sel.get("truncated") or int(sel.get("count") or 0) > len(sel["names"]))
        return {
            "state": state,
            "human_active": human,
            "agent_active": agent,
            "human_idle_ms": human_idle,
            "agent_idle_ms": agent_idle,
            "last_human_kind": self._last_human_kind,
            "agent_depth": depth,
            "modal_operators": list(self._modal),
            "mode": sel.get("mode", "UNKNOWN"),
            "selection": sel,
            "windows_ms": {"human": int(HUMAN_ACTIVE_WINDOW_S * 1000),
                           "agent": int(AGENT_ACTIVE_WINDOW_S * 1000)},
            "scene": self._scene,
            "ts": now,
            "source": "addon",
        }


_activity_tracker = ActivityTracker()
_activity_tracker.attach(_edit_recorder)


def get_activity_tracker():
    return _activity_tracker


@persistent
def _dshbb_activity_undo_post(scene, depsgraph=None):
    tracker = get_activity_tracker()
    tracker.note_human("undo")
    tracker.tick(force=True)


@persistent
def _dshbb_activity_redo_post(scene, depsgraph=None):
    tracker = get_activity_tracker()
    tracker.note_human("redo")
    tracker.tick(force=True)


@persistent
def _dshbb_activity_depsgraph_post(scene, depsgraph=None):
    try:
        get_activity_tracker().tick()
    except Exception:
        pass


def _register_activity_handlers():
    """Always-on (independent of telemetry consent): local-only activity detection."""
    handlers = [
        (bpy.app.handlers.undo_post, _dshbb_activity_undo_post),
        (bpy.app.handlers.redo_post, _dshbb_activity_redo_post),
        (bpy.app.handlers.depsgraph_update_post, _dshbb_activity_depsgraph_post),
    ]
    for handler_list, handler in handlers:
        if handler not in handler_list:
            handler_list.append(handler)


def _unregister_activity_handlers():
    handlers = [
        (bpy.app.handlers.undo_post, _dshbb_activity_undo_post),
        (bpy.app.handlers.redo_post, _dshbb_activity_redo_post),
        (bpy.app.handlers.depsgraph_update_post, _dshbb_activity_depsgraph_post),
    ]
    for handler_list, handler in handlers:
        with suppress(ValueError):
            handler_list.remove(handler)
