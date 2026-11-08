""" Gateway connection, command queue and command dispatch.

3.1 reversed the control channel: the add-on dials the DSH Gateway over
WebSocket (endpoint, protocol and handshake fields come from the generated
gateway schema) and owns no inbound listener. Requests arrive as JSON text
frames from the Gateway; they run through
the same main-thread command queue and execute_command path as the old socket
server, so pause gating, agent-record marking and the response envelopes are
unchanged.
"""

import bpy
import json
import os
import queue
import threading
import time
import traceback
import uuid

from . import transport
from .command_registry import (
    COMMAND_NAMES,
    WIRE_SPECS,
    begin_agent_command,
    command_requires_network,
    command_mixins,
    drain_human_edits,
    end_agent_command,
    gate_property,
    start_runtime_handlers,
    stop_runtime_handlers,
    tick_activity,
)
from .config import (
    get_credential,
    get_gateway_host,
    get_gateway_port,
    gateway_url,
    online_access_enabled,
    online_access_error,
)
from .gateway_schema import ADDON_WS_PATH, ADDON_VERSION as ADDON_VERSION_TEXT
from .protocol import ADDON_PROTOCOL_VERSION, ai_paused, is_pause_safe_command

GATEWAY_PATH = ADDON_WS_PATH
HANDSHAKE_TIMEOUT_SECONDS = 5.0
RECV_POLL_SECONDS = 1.0
RETRY_MIN_SECONDS = 1.0
RETRY_MAX_SECONDS = 10.0
HANDSHAKE_MESSAGE_TYPES = frozenset({"handshake_ok", "handshake_error"})

def _instance_id():
    """Stable for the whole Blender process.

    The Gateway routes a reconnected add-on back to the same instance, and an
    add-on reload must not mint a new identity. A virtual-gui launch sets
    DSHBB_INSTANCE_ID so the Gateway can wait for the exact handshake it spawned;
    in every other launch the identity stays derived from the Blender pid.
    """
    override = (os.getenv("DSHBB_INSTANCE_ID") or "").strip()
    if override and len(override) <= 128 and all(ch.isalnum() or ch in "-_." for ch in override):
        return override
    return uuid.uuid5(uuid.NAMESPACE_URL, "dshbb-addon-%d" % os.getpid()).hex


_INSTANCE_ID = _instance_id()


def _session_kind():
    """How this Blender process was started, as the Gateway handshake reports it."""
    kind = (os.getenv("DSHBB_SESSION_KIND") or "").strip().lower()
    return kind if kind in ("user-gui", "virtual-gui") else "user-gui"


class DSHBlenderBridgeServer(*command_mixins()):
    def __init__(self, gateway_port=None, gateway_host=None):
        # Explicit values are for tests; production reads config (env -> add-on
        # preference -> generated default) every time the connection is (re)started.
        self._configured_gateway_port = gateway_port
        self._configured_gateway_host = gateway_host
        self.gateway_host = gateway_host or get_gateway_host()
        self.gateway_port = int(gateway_port) if gateway_port else get_gateway_port()
        self.running = False
        self.connected = False
        self.handshake_error = ""
        # Commands are pushed here by the Gateway connection thread and drained
        # by a single timer running on Blender's main thread. bpy.app.timers is
        # not thread-safe, so registering a timer per command could silently
        # drop the callback - on Windows especially.
        self.command_queue = queue.Queue()
        self._gateway_thread = None
        self._connection = None
        self._connection_lock = threading.Lock()
        self._project = ""
        self._reported_pause = False

    # Credentials live in exactly one place: add-on preferences, with the
    # DSHBB_*/BLENDERMCP_* environment variables as fallback
    # (config.get_credential). Session overrides serve the Hyper3D free-trial
    # button. The old Scene-property mirror was removed in 4.5, so a saved
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
            print("DSH Blender Bridge: cannot start in background mode (blender -b) - commands would never execute\n"
                  "DSH Blender Bridge: run Blender with a GUI, or use a virtual display: xvfb-run -a blender")
            return

        if self.running:
            print("Server is already running")
            return

        if not self._configured_gateway_port:
            self.gateway_host = self._configured_gateway_host or get_gateway_host()
            self.gateway_port = get_gateway_port()
        self.running = True
        self.handshake_error = ""
        self._project = self._current_project()
        self._reported_pause = False

        try:
            start_runtime_handlers()

            # start() is called from the operator, i.e. the main thread, so
            # this is the only safe place to touch bpy.app.timers.
            if not bpy.app.timers.is_registered(self._drain_command_queue):
                bpy.app.timers.register(self._drain_command_queue, persistent=True)

            self._gateway_thread = threading.Thread(
                target=self._gateway_loop, name="DSHBB-Gateway", daemon=True)
            self._gateway_thread.start()

            print("DSH Blender Bridge connecting to the DSH Gateway at " + gateway_url(self.gateway_port)
                  + " (start the DSH plugin to finish the handshake)")
        except Exception as exc:
            print("Failed to start the DSH Gateway connection: %s" % str(exc))
            self.stop()

    def stop(self):
        self.running = False
        self.connected = False

        # Closing the WebSocket first unblocks the connection thread parked in
        # recv_text(), so the join below cannot wait for a network timeout.
        with self._connection_lock:
            connection, self._connection = self._connection, None
        if connection is not None:
            try:
                connection.close()
            except Exception:
                pass

        thread, self._gateway_thread = self._gateway_thread, None
        if thread is not None:
            try:
                if thread.is_alive():
                    thread.join(timeout=2.0)
            except Exception:
                pass

        try:
            if bpy.app.timers.is_registered(self._drain_command_queue):
                bpy.app.timers.unregister(self._drain_command_queue)
        except Exception:
            pass

        stop_runtime_handlers()

        # Drop any requests that will never be serviced now.
        while True:
            try:
                self.command_queue.get_nowait()
            except queue.Empty:
                break

        print("DSH Blender Bridge Gateway connection stopped")

    @staticmethod
    def _current_project():
        try:
            return bpy.data.filepath or ""
        except Exception:
            return ""

    # -- Gateway connection (background thread) ------------------------------
    def _gateway_loop(self):
        backoff = RETRY_MIN_SECONDS
        while self.running:
            connection = None
            try:
                connection = transport.WebSocketClient(
                    self.gateway_host, self.gateway_port, path=GATEWAY_PATH)
                connection.connect(timeout=HANDSHAKE_TIMEOUT_SECONDS)
                with self._connection_lock:
                    self._connection = connection
                self._send_handshake(connection)
                self._await_handshake(connection)

                self.connected = True
                self.handshake_error = ""
                backoff = RETRY_MIN_SECONDS
                print("DSH Blender Bridge connected to the DSH Gateway (instance %s)" % _INSTANCE_ID)
                self._serve(connection)
            except transport.WebSocketError as exc:
                print("DSH Blender Bridge: Gateway connection problem: %s" % str(exc))
            except OSError as exc:
                print("DSH Blender Bridge: Gateway connection failed: %s" % str(exc))
            except Exception as exc:
                traceback.print_exc()
                print("DSH Blender Bridge: unexpected Gateway connection error: %s" % str(exc))
            finally:
                self.connected = False
                with self._connection_lock:
                    if self._connection is connection:
                        self._connection = None
                if connection is not None:
                    try:
                        connection.close()
                    except Exception:
                        pass

            if not self.running:
                break
            time.sleep(backoff)
            backoff = min(backoff * 2.0, RETRY_MAX_SECONDS)

    def _send_handshake(self, connection):
        handshake = {
            "type": "handshake",
            "instanceId": _INSTANCE_ID,
            "kind": _session_kind(),
            "version": ADDON_VERSION_TEXT,
            "protocol": ADDON_PROTOCOL_VERSION,
            "project": self._project,
            "capabilities": sorted(COMMAND_NAMES),
        }
        connection.send_text(json.dumps(handshake, ensure_ascii=False))

    def _await_handshake(self, connection):
        raw = connection.recv_text(timeout=HANDSHAKE_TIMEOUT_SECONDS)
        if raw is None:
            raise transport.WebSocketError("Gateway closed the connection before the handshake reply")
        try:
            message = json.loads(raw)
        except ValueError as exc:
            raise transport.WebSocketError(
                "Gateway sent an unreadable handshake reply: %s" % raw[:200]) from exc
        if not isinstance(message, dict):
            raise transport.WebSocketError("Gateway handshake reply must be a JSON object")
        message_type = message.get("type")
        if message_type == "handshake_ok":
            return
        if message_type == "handshake_error":
            detail = message.get("message") or message.get("code") or "unknown handshake error"
            fix = message.get("fix") or ""
            self.handshake_error = str(detail)
            print("DSH Blender Bridge: Gateway rejected the handshake: %s" % str(detail))
            if fix:
                print("DSH Blender Bridge: fix: %s" % str(fix))
            raise transport.WebSocketError("Gateway rejected the handshake: %s" % str(detail))
        raise transport.WebSocketError(
            "Gateway sent an unexpected handshake reply type: %r" % message_type)

    def _serve(self, connection):
        while self.running:
            try:
                text = connection.recv_text(timeout=RECV_POLL_SECONDS)
            except TimeoutError:
                continue
            if text is None:
                print("DSH Blender Bridge: Gateway closed the connection")
                return
            self._handle_gateway_message(text, connection.send_text)

    def _report_pause_change(self):
        """Tell the Gateway when the human toggles the N-panel pause.

        Pause is human-owned: the Gateway mirrors this state onto the session so
        the write gate can answer before an agent command reaches Blender.
        """
        try:
            paused = bool(ai_paused())
        except Exception:
            return
        if paused == self._reported_pause:
            return
        self._reported_pause = paused
        with self._connection_lock:
            connection = self._connection
        if connection is None or not self.connected:
            return
        try:
            connection.send_text(json.dumps({
                "type": "event",
                "name": "pause",
                "paused": paused,
                "instanceId": _INSTANCE_ID,
            }, ensure_ascii=False))
        except Exception as exc:
            print("DSH Blender Bridge: failed to report the pause state: %s" % str(exc))

    def _report_human_edits(self):
        """Tell the Gateway which objects/collections the human changed.

        The Gateway turns this into a write-gate conflict so an agent re-reads
        the scene instead of overwriting a human edit. Records are only kept in
        memory and drained on the next main-thread tick.
        """
        try:
            edits = drain_human_edits()
        except Exception:
            return
        if not edits:
            return
        with self._connection_lock:
            connection = self._connection
        if connection is None or not self.connected:
            return
        try:
            connection.send_text(json.dumps({
                "type": "event",
                "name": "human_edit",
                "instanceId": _INSTANCE_ID,
                "objects": [e for e in edits if e.get("kind") == "object"],
                "collections": [e for e in edits if e.get("kind") == "collection"],
            }, ensure_ascii=False))
        except Exception as exc:
            print("DSH Blender Bridge: failed to report human edits: %s" % str(exc))

    def _handle_gateway_message(self, text, send):
        """Parse one Gateway message and queue it for the main-thread timer.

        *send* is a callable that writes one response text frame. Exposed as a
        method so tests can drive framing without a network connection.
        """
        try:
            message = json.loads(text)
        except (ValueError, UnicodeDecodeError) as exc:
            # Without a parsed requestId there is nobody to answer; the Gateway
            # owns the request timeout for a frame it cannot correlate.
            print("DSH Blender Bridge: ignoring a malformed Gateway message: %s" % str(exc))
            return
        if not isinstance(message, dict):
            print("DSH Blender Bridge: ignoring a non-object Gateway message")
            return
        message_type = message.get("type")
        if message_type in HANDSHAKE_MESSAGE_TYPES:
            print("DSH Blender Bridge: ignoring a post-handshake %r message" % message_type)
            return
        request_id = message.get("requestId")
        self.command_queue.put((message, send, request_id))

    def _drain_command_queue(self):
        """Run queued commands on Blender's main thread.

        Registered once by start(); returns the poll interval so Blender keeps
        calling it. All bpy access happens here, on the main thread.
        """
        if not self.running:
            return None

        try:
            self._project = self._current_project()
            tick_activity()
        except Exception:
            pass
        self._report_pause_change()
        self._report_human_edits()

        while True:
            try:
                command, send, request_id = self.command_queue.get_nowait()
            except queue.Empty:
                break

            try:
                response = self.execute_command(command)
            except Exception as exc:
                print("Error executing command: %s" % str(exc))
                traceback.print_exc()
                response = {"status": "error", "message": str(exc)}

            if request_id is not None:
                response["requestId"] = request_id
            try:
                send(json.dumps(response, ensure_ascii=False))
            except Exception as exc:
                print("DSH Blender Bridge: failed to send a Gateway response: %s" % str(exc))

        return 0.05

    # -- dispatch ------------------------------------------------------------
    def execute_command(self, command):
        """Execute a command in the main Blender thread"""
        # 暂停检查放在 try/finally 之前：被拒的请求不应标记成「AI 正在操作」
        if ai_paused() and not is_pause_safe_command(command.get("type")):
            return {"status": "error", "error": "AI_PAUSED",
                    "message": "AI 操作已暂停：请在 Blender N 面板「活动状态」里点「继续 AI 操作」"}
        try:
            with begin_agent_command():
                return self._execute_command_internal(command)

        except Exception as exc:
            print("Error executing command: %s" % str(exc))
            traceback.print_exc()
            return {"status": "error", "message": str(exc)}
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
            return {"status": "error", "message": "Unknown command type: %s" % cmd_type}
        if command_requires_network(cmd_type) and not online_access_enabled():
            return online_access_error()

        try:
            print("Executing handler for %s" % cmd_type)
            result = handler(**params)
            print("Handler execution complete")
            return {"status": "success", "result": result}
        except Exception as exc:
            print("Error in handler: %s" % str(exc))
            traceback.print_exc()
            return {"status": "error", "message": str(exc)}
