"""Executed INSIDE a real Blender 4.2 process by harness.py (background mode).

Steps: install the committed extension zip into a throw-away user extension repo
-> enable via the bl_ext namespace -> get_addon_info -> bpy execute -> selection
POST to an in-process fake DSH backend -> pause gate -> uninstall.
Writes a JSON check report; raises when a check fails so Blender exits non-zero.
"""

import importlib
import json
import os
import sys
import threading
import traceback
from http.server import BaseHTTPRequestHandler, HTTPServer

import addon_utils
import bpy

PACKAGE_ROOT = "dsh_blender_bridge"
EXTENSION_REPO = "user_default"
PACKAGE_NAME = "bl_ext.user_default.dsh_blender_bridge"


def extension_repo():
    for index, repo in enumerate(bpy.context.preferences.extensions.repos):
        if repo.module == EXTENSION_REPO:
            return index, repo
    raise RuntimeError("Blender user extension repository user_default is unavailable")


def main():
    argv = sys.argv[sys.argv.index("--") + 1:]
    zip_path, results_path, work_dir = argv[0], argv[1], argv[2]
    report = {"blender": bpy.app.version_string, "checks": {}}

    def check(name):
        def decorate(function):
            try:
                detail = function()
                report["checks"][name] = {"ok": True, "detail": detail}
            except Exception:
                report["checks"][name] = {"ok": False, "detail": traceback.format_exc()}
            return function

        return decorate

    # 1. Install the zip into the throw-away user extension repository.
    @check("install_extension")
    def _install():
        _index, repo = extension_repo()
        result = bpy.ops.extensions.package_install_files(
            filepath=zip_path,
            repo=EXTENSION_REPO,
            enable_on_install=True,
            overwrite=True,
        )
        package_dir = os.path.join(repo.directory, PACKAGE_ROOT)
        return {
            "operator_result": sorted(result),
            "extension_dir": repo.directory,
            "package_dir": package_dir,
            "installed": result == {"FINISHED"} and os.path.isdir(package_dir),
        }

    # 2. Enable it and read the registration/version surface.
    @check("enable_addon")
    def _enable():
        module = addon_utils.enable(PACKAGE_NAME, default_set=True)
        enabled = addon_utils.check(PACKAGE_NAME)
        addon = importlib.import_module(PACKAGE_NAME)
        command_registry = importlib.import_module(PACKAGE_NAME + ".command_registry")
        return {
            "module": module.__name__ if module is not None else None,
            "module_file": addon.__file__,
            "enabled_pair": list(enabled),
            "addon_version": list(addon.ADDON_VERSION),
            "protocol": addon.ADDON_PROTOCOL_VERSION,
            "commands": len(command_registry.COMMAND_NAMES),
            "scene_prop": hasattr(bpy.types.Scene, "dshbb_port"),
        }

    server_module = importlib.import_module(PACKAGE_NAME + ".server")
    server = server_module.DSHBlenderBridgeServer()

    # 3. get_addon_info through the real dispatch path.
    @check("get_addon_info")
    def _info():
        response = server.execute_command({"type": "get_addon_info", "params": {}})
        return response

    # 4. Execute a bpy command through execute_code, then verify the scene.
    @check("execute_bpy_command")
    def _execute():
        response = server.execute_command({
            "type": "execute_code",
            "params": {"code": "bpy.ops.mesh.primitive_cube_add()\nprint('T10_EXECUTED')"},
        })
        objects = [obj.name for obj in bpy.data.objects]
        return {"response": response, "objects": objects,
                "cube_types": [obj.type for obj in bpy.data.objects]}

    # 5. Send the native selection to an in-process fake DSH backend.
    @check("selection_send")
    def _selection():
        received = []

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                payload = self.rfile.read(int(self.headers.get("Content-Length", "0")))
                received.append({"path": self.path, "json": json.loads(payload.decode("utf-8"))})
                body = json.dumps({"ok": True, "seq": 1, "received_at": "t10"}).encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *args):
                pass

        httpd = HTTPServer(("127.0.0.1", 0), Handler)
        thread = threading.Thread(target=httpd.serve_forever, daemon=True)
        thread.start()
        try:
            bpy.context.scene.dshbb_backend_url = "http://127.0.0.1:%d" % httpd.server_port
            bpy.ops.object.select_all(action="SELECT")
            if bpy.context.object is not None:
                bpy.context.view_layer.objects.active = bpy.context.object
            response = server.execute_command({"type": "send_selection_to_dsh", "params": {}})
            query = server.execute_command({"type": "get_selection", "params": {}})
        finally:
            httpd.shutdown()
            httpd.server_close()
            bpy.context.scene.dshbb_backend_url = ""
        return {
            "response": response,
            "selection_query": query,
            "posted": received,
        }

    # 6. Pause gate: writes rejected, reads still served.
    @check("pause_gate")
    def _pause():
        bpy.context.scene.dshbb_ai_paused = True
        try:
            blocked = server.execute_command({"type": "execute_code", "params": {"code": "1+1"}})
            allowed = server.execute_command({"type": "get_scene_info", "params": {}})
        finally:
            bpy.context.scene.dshbb_ai_paused = False
        return {"blocked": blocked, "allowed": allowed}

    # 7. Unknown command envelope.
    @check("unknown_command")
    def _unknown():
        return server.execute_command({"type": "definitely_not_a_command", "params": {}})

    # 8. Uninstall: disable, check no scene props / timers, remove the extension.
    @check("uninstall")
    def _uninstall():
        server.stop()
        index, repo = extension_repo()
        package_dir = os.path.join(repo.directory, PACKAGE_ROOT)
        config_module = sys.modules.get(PACKAGE_NAME + ".config")
        addon_module = sys.modules.get(PACKAGE_NAME)
        owned_timers = [timer for timer in (
            getattr(config_module, "_dshbb_ensure_server_running", None),
            getattr(addon_module, "dshbb_ui_list_timer", None),
        ) if timer is not None]
        addon_utils.disable(PACKAGE_NAME, default_set=True)
        timer_registered = any(bpy.app.timers.is_registered(timer) for timer in owned_timers)
        residue = {
            "port_prop": hasattr(bpy.types.Scene, "dshbb_port"),
            "pause_prop": hasattr(bpy.types.Scene, "dshbb_ai_paused"),
            "selection_prop": hasattr(bpy.types.Scene, "dshbb_selection_items"),
        }
        uninstall_error = None
        try:
            result = bpy.ops.extensions.package_uninstall(
                repo_directory=repo.directory,
                repo_index=index,
                pkg_id=PACKAGE_ROOT,
            )
        except (RuntimeError, AttributeError) as exc:
            # Blender's UI operator can fail on a redraw in background mode; the
            # report keeps the error instead of hiding it.
            uninstall_error = str(exc)
            result = set()
        if os.path.isdir(package_dir):
            import shutil
            shutil.rmtree(package_dir, ignore_errors=True)
        return {
            "operator_result": sorted(result),
            "residue": residue,
            "owned_timers": [getattr(timer, "__name__", repr(timer)) for timer in owned_timers],
            "timer_registered": timer_registered,
            "package_dir_before_remove": package_dir,
            "package_dir_removed": not os.path.isdir(package_dir),
            "still_enabled": addon_utils.check(PACKAGE_NAME),
            "uninstall_error": uninstall_error,
        }

    with open(results_path, "w", encoding="utf-8") as handle:
        json.dump(report, handle, ensure_ascii=False, indent=2, default=str)
    print("T10_E2E_RESULT " + json.dumps(report, ensure_ascii=False, default=str))

    failed = sorted(name for name, value in report["checks"].items() if not value["ok"])
    if failed:
        raise RuntimeError("T10 E2E checks failed: " + ", ".join(failed))


if __name__ == "__main__":
    main()
