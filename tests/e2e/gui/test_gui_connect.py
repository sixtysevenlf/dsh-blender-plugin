"""GUI Blender E2E: Connect button + real socket round-trip (T10, release machine).

Run: DSH_BLENDER_EXE=/path/to/blender pytest tests/e2e/gui -m gui -v
"""

from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

import gui_harness as harness  # noqa: E402

pytestmark = pytest.mark.gui


@pytest.fixture(scope="session")
def gui_run():
    executable = harness.require_blender()
    harness.require_desktop()
    with tempfile.TemporaryDirectory(prefix="t10-blender-gui-") as tmp:
        run = harness.run_probe(Path(tmp), executable)
        yield run


def _dump(run):
    return json.dumps({
        "returncode": run["returncode"],
        "command": run["command"],
        "report": run["report"],
        "stderr_tail": (run["stderr"] or "")[-4000:],
        "stdout_tail": (run["stdout"] or "")[-4000:],
    }, ensure_ascii=False, indent=2)


def test_gui_probe_exits_zero_and_opened_a_window(gui_run):
    assert gui_run["returncode"] == 0, _dump(gui_run)
    assert "T10_GUI_RESULT " in (gui_run["stdout"] or ""), _dump(gui_run)
    report = gui_run["report"]
    assert report is not None
    assert report["background"] is False
    assert report["blender"].startswith("4.2.")
    assert report["raw"]["has_window"] is True
    failed = sorted(name for name, value in report["checks"].items() if not value["ok"])
    assert failed == [], _dump(gui_run)


def test_connect_starts_the_real_socket_server(gui_run):
    detail = harness.check(gui_run, "connect_operator")["detail"]
    assert detail["operator_result"] == ["FINISHED"]
    assert detail["server_running"] is True
    assert detail["scene_flag"] is True
    assert detail["port"] > 0


def test_socket_roundtrip_served_by_the_blender_main_thread(gui_run):
    detail = harness.check(gui_run, "socket_roundtrip")["detail"]
    assert detail["responses"] is not None
    assert list(detail["responses"]) == ["get_addon_info", "ping", "execute_code", "get_selection"]
    info = harness.check(gui_run, "get_addon_info")["detail"]
    assert info["status"] == "success"
    assert info["result"]["protocol_version"] == 11
    assert info["result"]["addon_version"] == [1, 11]
    assert harness.check(gui_run, "ping")["detail"] == {
        "status": "success", "result": {"pong": True},
    }
    executed = harness.check(gui_run, "execute_bpy_command")["detail"]
    assert executed["status"] == "success"
    assert executed["result"]["executed"] is True
    selected = harness.check(gui_run, "get_selection")["detail"]
    assert selected["status"] == "success"
    assert selected["result"]["count"] >= 1


def test_disconnect_stop_disable_remove(gui_run):
    assert harness.check(gui_run, "disconnect")["ok"] is True
    stopped = harness.check(gui_run, "stop_server")["detail"]
    assert stopped["scene_flag"] is False
    residue = harness.check(gui_run, "disable_no_residue")["detail"]
    assert residue == {
        "port_prop": False, "ai_paused_prop": False, "selection_prop": False,
    }
    removed = harness.check(gui_run, "remove_addon")["detail"]
    assert removed["package_dir_removed"] is True
    assert removed["still_enabled"] == [False, False]
    assert removed["remove_error"] is None
