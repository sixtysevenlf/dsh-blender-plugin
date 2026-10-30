""" Undo/redo/depsgraph edit capture and telemetry handlers. """

import bpy
from contextlib import contextmanager, redirect_stdout, suppress
from bpy.app.handlers import persistent

from ..compat import ADDON_MODULE
from .user_edit import _edit_recorder

def _dshbb_undo_post(scene, depsgraph=None):
    _edit_recorder.record_undo("undo")


@persistent
def _dshbb_redo_post(scene, depsgraph=None):
    _edit_recorder.record_undo("redo")


@persistent
def _dshbb_depsgraph_post(scene, depsgraph=None):
    _edit_recorder.poll_operators()


def _telemetry_consent_enabled():
    """Read the consent preference directly. Fails closed."""
    try:
        addon_prefs = bpy.context.preferences.addons.get(ADDON_MODULE)
        if not addon_prefs:
            return False
        return bool(addon_prefs.preferences.telemetry_consent)
    except Exception:
        return False


def _register_edit_capture_handlers():
    """Attach manual-edit handlers, but only with telemetry consent."""
    if not _telemetry_consent_enabled():
        _unregister_edit_capture_handlers()
        return False

    handlers = [
        (bpy.app.handlers.undo_post, _dshbb_undo_post),
        (bpy.app.handlers.redo_post, _dshbb_redo_post),
        (bpy.app.handlers.depsgraph_update_post, _dshbb_depsgraph_post),
    ]
    for handler_list, fn in handlers:
        if fn not in handler_list:
            handler_list.append(fn)
    return True


def sync_edit_capture_handlers():
    """Re-apply the consent gate. Safe to call when consent or server state changes."""
    try:
        server_running = bool(
            getattr(bpy.types, "dshbb_server", None)
            and bpy.types.dshbb_server.running
        )
    except Exception:
        server_running = False

    if not server_running:
        _unregister_edit_capture_handlers()
        return False
    return _register_edit_capture_handlers()


def _unregister_edit_capture_handlers():
    handlers = [
        (bpy.app.handlers.undo_post, _dshbb_undo_post),
        (bpy.app.handlers.redo_post, _dshbb_redo_post),
        (bpy.app.handlers.depsgraph_update_post, _dshbb_depsgraph_post),
    ]
    for handler_list, fn in handlers:
        with suppress(ValueError):
            handler_list.remove(fn)
