"""Real-Blender 4.2 add-on E2E (T10, release machine only).

One Blender process installs the committed zip, enables the add-on, exercises
get_addon_info / execute_code / selection POST / pause gate / unknown command,
then disables and removes it. The raw log is kept in the fixture so the evidence
pack can save it unedited.

Run: DSH_BLENDER_EXE=/path/to/blender pytest tests/e2e/blender -m blender -v
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

import harness  # noqa: E402

pytestmark = pytest.mark.blender


@pytest.fixture(scope="session")
def blender_run():
    executable = harness.require_blender()
    with tempfile.TemporaryDirectory(prefix="t10-blender-e2e-") as tmp:
        run = harness.run_runner(Path(tmp), executable)
        yield run


def _dump(run):
    return json.dumps({
        "returncode": run["returncode"],
        "command": run["command"],
        "report": run["report"],
        "stderr_tail": (run["stderr"] or "")[-4000:],
        "stdout_tail": (run["stdout"] or "")[-4000:],
    }, ensure_ascii=False, indent=2)


def test_blender_process_exits_zero_and_reports_env(blender_run):
    assert blender_run["returncode"] == 0, _dump(blender_run)
    assert "T10_E2E_RESULT " in (blender_run["stdout"] or ""), _dump(blender_run)
    report = blender_run["report"]
    assert report is not None and report["blender"].startswith("4.2."), _dump(blender_run)
    failed = sorted(name for name, value in report["checks"].items() if not value["ok"])
    assert failed == [], _dump(blender_run)
    assert set(report["checks"]) == {
        "install_zip", "enable_addon", "get_addon_info", "execute_bpy_command",
        "selection_send", "pause_gate", "unknown_command", "uninstall",
    }


def test_zip_installs_and_enables_the_package(blender_run):
    detail = harness.check(blender_run, "install_zip")["detail"]
    assert detail["installed"] is True
    assert detail["operator_result"] == ["FINISHED"]

    enabled = harness.check(blender_run, "enable_addon")["detail"]
    assert enabled["bl_info_version"] == [1, 11]
    assert enabled["addon_version"] == [1, 11]
    assert enabled["protocol"] == 11
    assert enabled["commands"] == 39
    assert enabled["scene_prop"] is True
    assert enabled["enabled_pair"] == [True, True], enabled
    assert enabled["module_file"].replace("\\", "/").endswith("dsh_blender_bridge/__init__.py")


def test_get_addon_info_contract(blender_run):
    response = harness.check(blender_run, "get_addon_info")["detail"]
    assert set(response) == {"status", "result"}
    assert response["status"] == "success"
    info = response["result"]
    assert info["name"] == "DSH Blender Bridge"
    assert info["addon_version"] == [1, 11]
    assert info["protocol_version"] == 11
    assert len(info["capabilities"]) == 16
    assert info["blender_version"].startswith("4.2.")


def test_execute_code_runs_a_real_bpy_command(blender_run):
    detail = harness.check(blender_run, "execute_bpy_command")["detail"]
    assert detail["response"]["status"] == "success"
    assert detail["response"]["result"]["executed"] is True
    assert "T10_EXECUTED" in detail["response"]["result"]["result"]
    assert "Cube" in detail["objects"]
    assert "MESH" in detail["cube_types"]


def test_selection_is_posted_to_the_backend(blender_run):
    detail = harness.check(blender_run, "selection_send")["detail"]
    response = detail["response"]
    assert response["status"] == "success"
    posted = response["result"]
    assert posted["ok"] is True
    assert posted["seq"] == 1
    assert posted["backend_url"].startswith("http://127.0.0.1:")
    assert len(detail["posted"]) == 1
    record = detail["posted"][0]
    assert record["path"] == "/host/selection"
    payload = record["json"]
    assert payload["v"] == 1
    assert payload["source"] == "blender-addon"
    assert payload["selection"]["kind"] == "objects"
    assert payload["selection"]["count"] >= 1
    assert "Cube" in payload["selection"]["names"]
    assert payload["scene"]["name"] == "Scene"
    assert payload["activity"]["source"] == "addon"

    query = detail["selection_query"]
    assert query["status"] == "success"
    assert query["result"]["count"] >= 1
    assert "Cube" in query["result"]["names"]


def test_pause_gate_and_unknown_command(blender_run):
    paused = harness.check(blender_run, "pause_gate")["detail"]
    assert paused["blocked"]["status"] == "error"
    assert paused["blocked"]["error"] == "AI_PAUSED"
    assert "继续 AI 操作" in paused["blocked"]["message"]
    assert paused["allowed"]["status"] == "success"
    assert paused["allowed"]["result"]["name"] == "Scene"

    unknown = harness.check(blender_run, "unknown_command")["detail"]
    assert unknown == {
        "status": "error",
        "message": "Unknown command type: definitely_not_a_command",
    }


def test_uninstall_leaves_no_scene_props_timers_or_files(blender_run):
    detail = harness.check(blender_run, "uninstall")["detail"]
    assert detail["residue"] == {
        "port_prop": False, "pause_prop": False, "selection_prop": False,
    }
    assert detail["timer_registered"] is False
    assert detail["package_dir_removed"] is True
    assert detail["still_enabled"] == [False, False]
    remove_error = detail["addon_remove_error"]
    if remove_error is not None:
        # Documented background-mode limitation of Blender's UI operator, not an
        # add-on defect; the GUI probe exercises the real Remove button.
        assert "tag_redraw" in remove_error and "'NoneType' object has no attribute" in remove_error
