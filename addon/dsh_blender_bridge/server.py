""" Socket server, command queue and command dispatch. """

import bpy
import json
import threading
import socket
import queue
import time
import traceback

from .command_registry import (
    WIRE_SPECS,
    begin_agent_command,
    command_mixins,
    drain_edit_recorder,
    end_agent_command,
    gate_property,
    start_runtime_handlers,
    stop_runtime_handlers,
    tick_activity,
)
from .config import get_credential
from .protocol import ai_paused, is_pause_safe_command


class DSHBlenderBridgeServer(*command_mixins()):
    def __init__(self, host="localhost", port=9876):
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

    # Credentials live in exactly one place: add-on preferences, with the
    # DSHBB_*/BLENDERMCP_* environment variables as fallback
    # (config.get_credential). Session overrides serve the Hyper3D free-trial
    # button. The old Scene-property mirror was removed in S2 4.5, so a saved
    # .blend can no longer silently override the stored key.
    def _get_hyper3d_api_key(self):
        return get_credential("hyper3d_api_key", "DSHBB_HYPER3D_API_KEY")

    def _get_sketchfab_api_key(self):
        return get_credential("sketchfab_api_key", "DSHBB_SKETCHFAB_API_KEY")

    def _get_polypizza_api_key(self):
        return get_credential("polypizza_api_key", "DSHBB_POLYPIZZA_API_KEY")

    def _get_hunyuan3d_secret_id(self):
        return get_credential("hunyuan3d_secret_id", "DSHBB_HUNYUAN3D_SECRET_ID")

    def _get_hunyuan3d_secret_key(self):
        return get_credential("hunyuan3d_secret_key", "DSHBB_HUNYUAN3D_SECRET_KEY")

    def _get_hunyuan3d_api_url(self):
        return get_credential("hunyuan3d_api_url", "DSHBB_HUNYUAN3D_API_URL") or "http://localhost:8081"

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

            start_runtime_handlers()

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

        stop_runtime_handlers()
        drain_edit_recorder()

        try:
            if bpy.app.timers.is_registered(self._drain_command_queue):
                bpy.app.timers.unregister(self._drain_command_queue)
        except Exception:
            pass

        # Close socket
        if self.socket:
            try:
                self.socket.close()
            except Exception:
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
            except Exception:
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
            tick_activity()
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
                client.sendall(response_json.encode("utf-8"))
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
        buffer = b""

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
                        command = json.loads(buffer.decode("utf-8"))
                        buffer = b""

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
            except Exception:
                pass
            print("Client handler stopped")

    def execute_command(self, command):
        """Execute a command in the main Blender thread"""
        # 暂停检查放在 try/finally 之前：被拒的请求不应标记成「AI 正在操作」
        if ai_paused() and not is_pause_safe_command(command.get("type")):
            return {"status": "error", "error": "AI_PAUSED",
                    "message": "AI 操作已暂停：请在 Blender N 面板「活动状态」里点「继续 AI 操作」"}
        try:
            with begin_agent_command():
                return self._execute_command_internal(command)

        except Exception as e:
            print(f"Error executing command: {str(e)}")
            traceback.print_exc()
            return {"status": "error", "message": str(e)}
        finally:
            # v1.8：标记 agent 刚操作过；并把选区指纹作废，避免把 agent 的改动误算成人的操作
            try:
                end_agent_command()
            except Exception:
                pass

    def ping_response(self, **_ignored):
        """Trivial liveness check; touches no bpy data (params are ignored)."""
        return {"pong": True}

    def _resolve_handler(self, cmd_type):
        """Return the bound handler for a wire command, or None.

        Gated commands exist only while their scene toggle is enabled; an
        unknown or disabled command returns None and is reported as unknown.
        """
        spec = WIRE_SPECS.get(cmd_type)
        if spec is None:
            return None
        gate = gate_property(spec)
        if gate and not getattr(bpy.context.scene, gate, False):
            return None
        return getattr(self, spec.handler, None)

    def _execute_command_internal(self, command):
        """Internal command execution with proper context"""
        cmd_type = command.get("type")
        params = command.get("params", {})

        handler = self._resolve_handler(cmd_type)
        if not handler:
            return {"status": "error", "message": f"Unknown command type: {cmd_type}"}

        try:
            print(f"Executing handler for {cmd_type}")
            result = handler(**params)
            print("Handler execution complete")
            return {"status": "success", "result": result}
        except Exception as e:
            print(f"Error in handler: {str(e)}")
            traceback.print_exc()
            return {"status": "error", "message": str(e)}

