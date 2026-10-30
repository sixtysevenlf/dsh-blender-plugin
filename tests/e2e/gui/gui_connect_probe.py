"""Executed INSIDE a GUI Blender process by tests/e2e/gui/harness.py.

Presses the real Connect operator, lets Blender's main thread service the
server timer, drives get_addon_info / ping / execute_code / get_selection from a
worker-thread client, then disconnects and removes the add-on.
"""

import json
import os
import shutil
import socket
import sys
import threading
import time
import traceback

import addon_utils
import bpy

PACKAGE_NAME = "dsh_blender_bridge"


def free_port():
    probe = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]
    finally:
        probe.close()


def client_worker(port, state):
    def request(connection, payload):
        connection.sendall((json.dumps(payload, ensure_ascii=False) + "\n").encode("utf-8"))
        buffer = b""
        while True:
            chunk = connection.recv(65536)
            if not chunk:
                raise ConnectionError("server closed the connection")
            buffer += chunk
            try:
                return json.loads(buffer.decode("utf-8"))
            except (json.JSONDecodeError, UnicodeDecodeError):
                continue

    deadline = time.time() + 15
    connection = None
    try:
        while time.time() < deadline:
            try:
                connection = socket.create_connection(("127.0.0.1", port), timeout=2)
                break
            except OSError:
                time.sleep(0.1)
        if connection is None:
            raise TimeoutError("could not connect to 127.0.0.1:%d" % port)
        connection.settimeout(10)
        state["responses"] = {
            "get_addon_info": request(connection, {"type": "get_addon_info", "params": {}}),
            "ping": request(connection, {"type": "ping", "params": {}}),
            "execute_code": request(connection, {
                "type": "execute_code",
                "params": {"code": "bpy.ops.mesh.primitive_cube_add()\nprint('T10_GUI_EXECUTED')"},
            }),
            "get_selection": request(connection, {"type": "get_selection", "params": {}}),
        }
        state["done"] = True
    except Exception:
        state["error"] = traceback.format_exc()
        state["done"] = True
    finally:
        if connection is not None:
            try:
                connection.close()
            except OSError:
                pass


def main():
    argv = sys.argv[sys.argv.index("--") + 1:]
    zip_path, results_path, work_dir = argv[0], argv[1], argv[2]
    report = {
        "blender": bpy.app.version_string,
        "background": bpy.app.background,
        "checks": {},
        "raw": {},
    }

    def record(name, ok, detail):
        report["checks"][name] = {"ok": ok, "detail": detail}

    port = free_port()
    report["port"] = port
    state = {"done": False}
    timers = {}
    started_at = time.time()

    try:
        bpy.ops.preferences.addon_install(filepath=zip_path, overwrite=True)
        addon_utils.enable(PACKAGE_NAME, default_set=True)
        import dsh_blender_bridge as addon

        report["raw"]["module_file"] = addon.__file__
        report["raw"]["version"] = list(addon.bl_info["version"])
        scene = bpy.context.scene
        scene.dshbb_port = port
        scene.dshbb_auto_start_server = False
        report["raw"]["has_window"] = bpy.context.window is not None
        record("install_enable", True, {
            "module_file": addon.__file__,
            "version": list(addon.bl_info["version"]),
            "protocol": addon.ADDON_PROTOCOL_VERSION,
            "commands": len(addon.protocol.COMMAND_NAMES),
        })

        connect = bpy.ops.dshbb.start_server()
        server = getattr(bpy.types, "dshbb_server", None)
        record("connect_operator", connect == {"FINISHED"} and server is not None and server.running, {
            "operator_result": sorted(connect),
            "server_running": bool(server and server.running),
            "scene_flag": bool(scene.dshbb_server_running),
            "port": port,
        })
        report["raw"]["listening"] = bool(server and server.running)

        worker = threading.Thread(target=client_worker, args=(port, state), daemon=True)
        worker.start()
        report["raw"]["client_started"] = True

        def poll():
            if state["done"]:
                worker.join(timeout=5)
                timers["value"] = time.time() - started_at
                record("socket_roundtrip", "error" not in state, {
                    "responses": state.get("responses"),
                    "elapsed_s": round(timers["value"], 3),
                })
                if "responses" in state:
                    info = state["responses"]["get_addon_info"]
                    record("get_addon_info", info.get("status") == "success"
                           and info["result"]["protocol_version"] == 11, info)
                    record("ping", state["responses"]["ping"] == {
                        "status": "success", "result": {"pong": True},
                    }, state["responses"]["ping"])
                    executed = state["responses"]["execute_code"]
                    record("execute_bpy_command", executed.get("status") == "success"
                           and "T10_GUI_EXECUTED" in str(executed.get("result", {}).get("result")),
                           executed)
                    selected = state["responses"]["get_selection"]
                    record("get_selection", selected.get("status") == "success"
                           and selected["result"]["count"] >= 1, selected)
                # Disconnect first so Blender's main thread stops servicing it.
                record("disconnect", True, {})
                stop = None
                try:
                    stop = bpy.ops.dshbb.stop_server()
                except Exception:
                    pass
                report["raw"]["stop_result"] = sorted(stop) if stop else None
                report["raw"]["server_after_stop"] = bool(
                    getattr(bpy.types, "dshbb_server", None) and bpy.types.dshbb_server.running
                )
                record("stop_server", report["raw"]["server_after_stop"] is False, {
                    "stop_result": report["raw"]["stop_result"],
                    "scene_flag": bool(scene.dshbb_server_running),
                })
                addon_utils.disable(PACKAGE_NAME, default_set=True)
                residue = {
                    "port_prop": hasattr(bpy.types.Scene, "dshbb_port"),
                    "ai_paused_prop": hasattr(bpy.types.Scene, "dshbb_ai_paused"),
                    "selection_prop": hasattr(bpy.types.Scene, "dshbb_selection_items"),
                }
                record("disable_no_residue", not any(residue.values()), residue)
                addons_dir = bpy.utils.user_resource("SCRIPTS", path="addons", create=True)
                package_dir = os.path.join(addons_dir, PACKAGE_NAME)
                remove_error = None
                windows = list(bpy.context.window_manager.windows)
                window = windows[0] if windows else None
                area = next(iter(window.screen.areas), None) if window is not None else None
                try:
                    # The Remove operator ends with context.area.tag_redraw(); a
                    # timer callback has no area, so override with the real GUI
                    # window/area before pressing it.
                    if area is not None:
                        with bpy.context.temp_override(window=window, area=area,
                                                       screen=window.screen):
                            bpy.ops.preferences.addon_remove(module=PACKAGE_NAME)
                    else:
                        bpy.ops.preferences.addon_remove(module=PACKAGE_NAME)
                except Exception as exc:
                    remove_error = str(exc)
                if os.path.isdir(package_dir):
                    shutil.rmtree(package_dir, ignore_errors=True)
                record("remove_addon", not os.path.isdir(package_dir), {
                    "remove_error": remove_error,
                    "package_dir_removed": not os.path.isdir(package_dir),
                    "still_enabled": list(addon_utils.check(PACKAGE_NAME)),
                })
                write_report()
                bpy.ops.wm.quit_blender()
                return None
            if time.time() - started_at > 45:
                record("socket_roundtrip", False, {"error": "timeout waiting for the client"})
                write_report()
                bpy.ops.wm.quit_blender()
                return None
            return 0.1

        def write_report():
            report["elapsed_s"] = round(time.time() - started_at, 3)
            with open(results_path, "w", encoding="utf-8") as handle:
                json.dump(report, handle, ensure_ascii=False, indent=2, default=str)
            print("T10_GUI_RESULT " + json.dumps(report, ensure_ascii=False, default=str))

        bpy.app.timers.register(poll, first_interval=0.1, persistent=True)
    except Exception:
        record("probe_error", False, traceback.format_exc())
        report["elapsed_s"] = round(time.time() - started_at, 3)
        with open(results_path, "w", encoding="utf-8") as handle:
            json.dump(report, handle, ensure_ascii=False, indent=2, default=str)
        print("T10_GUI_RESULT " + json.dumps(report, ensure_ascii=False, default=str))
        raise


if __name__ == "__main__":
    main()
