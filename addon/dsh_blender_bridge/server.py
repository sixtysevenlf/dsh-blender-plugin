""" Socket server, command queue and command dispatch. """

import bpy
import json
import threading
import socket
import queue
import time
import traceback

from .commands.activity import ActivityCommands
from .commands.assets_polyhaven import PolyHavenCommands
from .commands.assets_polypizza import PolypizzaCommands
from .commands.assets_sketchfab import SketchfabCommands
from .commands.core import CoreCommands
from .commands.generate_hunyuan import HunyuanCommands
from .commands.generate_hyper3d import Hyper3DCommands
from .commands.generate_premium import premium_tripo_handlers, premium_tripo_status
from .commands.material import MaterialCommands
from .commands.scene import SceneCommands
from .commands.selection import SelectionCommands
from .commands.telemetry import TelemetryCommands
from .config import RODIN_FREE_TRIAL_KEY, get_dshbb_addon_preferences, read_env_var
from .protocol import _ai_paused, _is_pause_safe_command
from .recorders.activity import _register_activity_handlers, _unregister_activity_handlers, get_activity_tracker
from .recorders.edit_capture import _register_edit_capture_handlers, _unregister_edit_capture_handlers
from .recorders.user_edit import get_edit_recorder

class DSHBlenderBridgeServer(
    CoreCommands,
    SceneCommands,
    SelectionCommands,
    ActivityCommands,
    MaterialCommands,
    TelemetryCommands,
    PolyHavenCommands,
    SketchfabCommands,
    PolypizzaCommands,
    Hyper3DCommands,
    HunyuanCommands,
):
    def __init__(self, host='localhost', port=9876):
        self.host = host
        self.port = port
        self.running = False
        self.socket = None
        self.server_thread = None
        # Commands are pushed here by client threads and drained by a single
        # timer running on Blender's main thread. bpy.app.timers is not
        # thread-safe, so registering a timer per command (the previous
        # approach) could silently drop the callback - on Windows especially -
        # leaving the client blocked in recv() until its socket timeout.
        self.command_queue = queue.Queue()
        # Live client sockets, so stop() can unblock threads parked in recv().
        self._clients = set()
        self._clients_lock = threading.Lock()

    def _get_config_value(self, scene_attr, pref_attr=None, env_var=None):
        """Read config in order: addon preferences -> scene -> env var."""
        prefs = get_dshbb_addon_preferences()
        if prefs and pref_attr:
            pref_value = getattr(prefs, pref_attr, "")
            if pref_value:
                return pref_value

        scene_value = getattr(bpy.context.scene, scene_attr, "")
        if scene_value:
            return scene_value

        if env_var:
            env_value = read_env_var(env_var) or ""
            if env_value:
                return env_value
        return ""

    def _get_hyper3d_api_key(self):
        # Let the free-trial button temporarily override persistent keys
        # without overwriting user-saved private keys.
        scene_value = getattr(bpy.context.scene, "dshbb_hyper3d_api_key", "")
        if scene_value == RODIN_FREE_TRIAL_KEY:
            return scene_value
        return self._get_config_value(
            "dshbb_hyper3d_api_key",
            "hyper3d_api_key",
            "DSHBB_HYPER3D_API_KEY",
        )

    def _get_sketchfab_api_key(self):
        return self._get_config_value(
            "dshbb_sketchfab_api_key",
            "sketchfab_api_key",
            "DSHBB_SKETCHFAB_API_KEY",
        )

    def _get_polypizza_api_key(self):
        return self._get_config_value(
            "dshbb_polypizza_api_key",
            "polypizza_api_key",
            "DSHBB_POLYPIZZA_API_KEY",
        )

    def _get_hunyuan3d_secret_id(self):
        return self._get_config_value(
            "dshbb_hunyuan3d_secret_id",
            "hunyuan3d_secret_id",
            "DSHBB_HUNYUAN3D_SECRET_ID",
        )

    def _get_hunyuan3d_secret_key(self):
        return self._get_config_value(
            "dshbb_hunyuan3d_secret_key",
            "hunyuan3d_secret_key",
            "DSHBB_HUNYUAN3D_SECRET_KEY",
        )

    def _get_hunyuan3d_api_url(self):
        return self._get_config_value(
            "dshbb_hunyuan3d_api_url",
            "hunyuan3d_api_url",
            "DSHBB_HUNYUAN3D_API_URL",
        ) or "http://localhost:8081"

    def start(self):
        if bpy.app.background:
            print("DSH Blender Bridge: cannot start server in background mode (blender -b) - commands would never execute\n"
                  "DSH Blender Bridge: run Blender with a GUI, or use a virtual display: xvfb-run -a blender")
            return

        if self.running:
            print("Server is already running")
            return

        self.running = True

        try:
            # Create socket
            self.socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            self.socket.bind((self.host, self.port))
            # Backlog of 1 meant a reconnecting client could complete the TCP
            # handshake and then never be accept()ed - a connection that looks
            # established but is never serviced.
            self.socket.listen(5)

            # Start server thread
            self.server_thread = threading.Thread(target=self._server_loop)
            self.server_thread.daemon = True
            self.server_thread.start()

            _register_edit_capture_handlers()
            # v1.8：活动/选区判定与 telemetry 无关，服务器一起来就常驻注册
            _register_activity_handlers()

            # start() is called from the operator, i.e. the main thread, so
            # this is the only safe place to touch bpy.app.timers.
            if not bpy.app.timers.is_registered(self._drain_command_queue):
                bpy.app.timers.register(self._drain_command_queue, persistent=True)

            print(f"DSH Blender Bridge server started on {self.host}:{self.port}")
        except Exception as e:
            print(f"Failed to start server: {str(e)}")
            self.stop()

    def stop(self):
        self.running = False

        _unregister_edit_capture_handlers()
        _unregister_activity_handlers()
        get_edit_recorder().drain()

        try:
            if bpy.app.timers.is_registered(self._drain_command_queue):
                bpy.app.timers.unregister(self._drain_command_queue)
        except Exception:
            pass

        # Close socket
        if self.socket:
            try:
                self.socket.close()
            except:
                pass
            self.socket = None

        # Shut down live client sockets. Without this, handler threads stay
        # parked in a blocking recv() forever; being daemon threads they then
        # outlive the restart and close connections the new server owns
        # (the WinError 10054 seen after toggling the addon).
        with self._clients_lock:
            clients = list(self._clients)
            self._clients.clear()
        for client in clients:
            try:
                client.shutdown(socket.SHUT_RDWR)
            except Exception:
                pass
            try:
                client.close()
            except Exception:
                pass

        # Drop any commands that will never be serviced now.
        while True:
            try:
                self.command_queue.get_nowait()
            except queue.Empty:
                break

        # Wait for thread to finish
        if self.server_thread:
            try:
                if self.server_thread.is_alive():
                    self.server_thread.join(timeout=1.0)
            except:
                pass
            self.server_thread = None

        print("DSH Blender Bridge server stopped")

    def _server_loop(self):
        """Main server loop in a separate thread"""
        print("Server thread started")
        self.socket.settimeout(1.0)  # Timeout to allow for stopping

        while self.running:
            try:
                # Accept new connection
                try:
                    client, address = self.socket.accept()
                    print(f"Connected to client: {address}")

                    # Handle client in a separate thread
                    client_thread = threading.Thread(
                        target=self._handle_client,
                        args=(client,)
                    )
                    client_thread.daemon = True
                    client_thread.start()
                except socket.timeout:
                    # Just check running condition
                    continue
                except Exception as e:
                    print(f"Error accepting connection: {str(e)}")
                    time.sleep(0.5)
            except Exception as e:
                print(f"Error in server loop: {str(e)}")
                if not self.running:
                    break
                time.sleep(0.5)

        print("Server thread stopped")

    def _drain_command_queue(self):
        """Run queued commands on Blender's main thread.

        Registered once by start(); returns the poll interval so Blender keeps
        calling it. All bpy access happens here, on the main thread.
        """
        if not self.running:
            return None

        # v1.8：活动的统一判定跑在主线程 timer 上（modal 操作符 / 原生选区变化）。
        try:
            get_activity_tracker().tick()
        except Exception:
            pass

        while True:
            try:
                command, client = self.command_queue.get_nowait()
            except queue.Empty:
                break

            try:
                response = self.execute_command(command)
                response_json = json.dumps(response)
            except Exception as e:
                print(f"Error executing command: {str(e)}")
                traceback.print_exc()
                response_json = json.dumps({"status": "error", "message": str(e)})

            try:
                client.sendall(response_json.encode('utf-8'))
            except Exception:
                print("Failed to send response - client disconnected")

        return 0.05

    def _handle_client(self, client):
        """Handle connected client"""
        print("Client handler started")
        # A finite timeout keeps this loop responsive to self.running instead
        # of parking in recv() forever.
        client.settimeout(1.0)
        with self._clients_lock:
            self._clients.add(client)
        buffer = b''

        try:
            while self.running:
                # Receive data
                try:
                    data = client.recv(8192)
                    if not data:
                        print("Client disconnected")
                        break

                    buffer += data
                    try:
                        # Try to parse command
                        command = json.loads(buffer.decode('utf-8'))
                        buffer = b''

                        # Hand off to the main thread. Never call
                        # bpy.app.timers.register() from here - it is not
                        # thread-safe and the callback can be silently lost.
                        print(f"Queued command: {command.get('type')}")
                        self.command_queue.put((command, client))
                    except (json.JSONDecodeError, UnicodeDecodeError):
                        # Incomplete data, wait for more. A multi-byte UTF-8
                        # character can land split across a recv() chunk
                        # boundary, which fails decode() before json.loads()
                        # ever runs - that's incomplete data too, not garbage.
                        pass
                except socket.timeout:
                    # Expected; loop round and re-check self.running.
                    continue
                except Exception as e:
                    print(f"Error receiving data: {str(e)}")
                    break
        except Exception as e:
            print(f"Error in client handler: {str(e)}")
        finally:
            with self._clients_lock:
                self._clients.discard(client)
            try:
                client.close()
            except:
                pass
            print("Client handler stopped")

    def execute_command(self, command):
        """Execute a command in the main Blender thread"""
        # 暂停检查放在 try/finally 之前：被拒的请求不应标记成「AI 正在操作」
        if _ai_paused() and not _is_pause_safe_command(command.get("type")):
            return {"status": "error", "error": "AI_PAUSED",
                    "message": "AI 操作已暂停：请在 Blender N 面板「活动状态」里点「继续 AI 操作」"}
        try:
            with get_edit_recorder().agent_command():
                return self._execute_command_internal(command)

        except Exception as e:
            print(f"Error executing command: {str(e)}")
            traceback.print_exc()
            return {"status": "error", "message": str(e)}
        finally:
            # v1.8：标记 agent 刚操作过；并把选区指纹作废，避免把 agent 的改动误算成人的操作
            try:
                tracker = get_activity_tracker()
                tracker.note_agent_end()
                tracker._resync()
            except Exception:
                pass

    def _execute_command_internal(self, command):
        """Internal command execution with proper context"""
        cmd_type = command.get("type")
        params = command.get("params", {})

        # Trivial liveness check. Touches no bpy data, so a successful ping
        # alongside a failing command isolates data access from transport.
        if cmd_type == "ping":
            return {"status": "success", "result": {"pong": True}}

        # Add a handler for checking PolyHaven status
        if cmd_type == "get_polyhaven_status":
            return {"status": "success", "result": self.get_polyhaven_status()}

        # Base handlers that are always available
        handlers = {
            "get_scene_info": self.get_scene_info,
            "get_world_state_snapshot": self.get_world_state_snapshot,
            "get_addon_info": self.get_addon_info,
            "get_object_info": self.get_object_info,
            "get_viewport_screenshot": self.get_viewport_screenshot,
            "execute_code": self.execute_code,
            "describe_node_type": self.describe_node_type,
            "bpy_api_lookup": self.bpy_api_lookup,
            "drain_human_activity": self.drain_human_activity,
            "get_activity_state": self.get_activity_state,
            "get_selection": self.get_selection,
            "send_selection_to_dsh": self.send_selection_to_dsh,
            "get_collections": self.get_collections,
            "send_collection_to_dsh": self.send_collection_to_dsh,
            "get_telemetry_consent": self.get_telemetry_consent,
            "set_telemetry_consent": self.set_telemetry_consent,
            "get_polyhaven_status": self.get_polyhaven_status,
            "get_hyper3d_status": self.get_hyper3d_status,
            "get_sketchfab_status": self.get_sketchfab_status,
            "get_polypizza_status": self.get_polypizza_status,
            "get_hunyuan3d_status": self.get_hunyuan3d_status,
            "get_tripo_status": premium_tripo_status,
            "export_scene": self.export_scene,
        }

        # Add Polyhaven handlers only if enabled
        if bpy.context.scene.dshbb_use_polyhaven:
            polyhaven_handlers = {
                "get_polyhaven_categories": self.get_polyhaven_categories,
                "search_polyhaven_assets": self.search_polyhaven_assets,
                "download_polyhaven_asset": self.download_polyhaven_asset,
                "get_polyhaven_asset_preview": self.get_polyhaven_asset_preview,
                "set_texture": self.set_texture,
            }
            handlers.update(polyhaven_handlers)

        # Add Hyper3d handlers only if enabled
        if bpy.context.scene.dshbb_use_hyper3d:
            polyhaven_handlers = {
                "create_rodin_job": self.create_rodin_job,
                "poll_rodin_job_status": self.poll_rodin_job_status,
                "import_generated_asset": self.import_generated_asset,
            }
            handlers.update(polyhaven_handlers)

        # Add Sketchfab handlers only if enabled
        if bpy.context.scene.dshbb_use_sketchfab:
            sketchfab_handlers = {
                "search_sketchfab_models": self.search_sketchfab_models,
                "get_sketchfab_model_preview": self.get_sketchfab_model_preview,
                "download_sketchfab_model": self.download_sketchfab_model,
            }
            handlers.update(sketchfab_handlers)

        # Add Poly Pizza handlers only if enabled
        if bpy.context.scene.dshbb_use_polypizza:
            polypizza_handlers = {
                "search_polypizza_models": self.search_polypizza_models,
                "download_polypizza_model": self.download_polypizza_model,
            }
            handlers.update(polypizza_handlers)

        # Add Hunyuan3d handlers only if enabled
        if bpy.context.scene.dshbb_use_hunyuan3d:
            hunyuan_handlers = {
                "create_hunyuan_job": self.create_hunyuan_job,
                "poll_hunyuan_job_status": self.poll_hunyuan_job_status,
                "import_generated_asset_hunyuan": self.import_generated_asset_hunyuan
            }
            handlers.update(hunyuan_handlers)

        # Tripo is only offered through Premium
        handlers.update(premium_tripo_handlers())

        handler = handlers.get(cmd_type)
        if handler:
            try:
                print(f"Executing handler for {cmd_type}")
                result = handler(**params)
                print(f"Handler execution complete")
                return {"status": "success", "result": result}
            except Exception as e:
                print(f"Error in handler: {str(e)}")
                traceback.print_exc()
                return {"status": "error", "message": str(e)}
        else:
            return {"status": "error", "message": f"Unknown command type: {cmd_type}"}
