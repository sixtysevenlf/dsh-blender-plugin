"""Host-side launcher for GUI native E2E modules.

The modules under tests/e2e/gui/native/ run inside a real (non-background)
Blender window because OpenGL capture and evidence rendering need a window.
This launcher starts that process and asserts the JSON report; it never imports
bpy itself.  Marked gui: excluded from the default pytest run.

Run: npm run test:e2e:gui
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

import gui_harness as harness  # noqa: E402

_BLENDER_E2E = HERE.parent / "blender"
if str(_BLENDER_E2E) not in sys.path:
    sys.path.insert(0, str(_BLENDER_E2E))
import native_manifest  # noqa: E402

pytestmark = pytest.mark.gui


def _ids(modules):
    return [m["id"] for m in modules]


@pytest.fixture(scope="session", params=native_manifest.GUI_MODULES, ids=_ids(native_manifest.GUI_MODULES))
def native_gui_run(request, tmp_path_factory):
    executable = harness.require_blender()
    harness.require_desktop()
    module = request.param
    base = tmp_path_factory.mktemp("t11b-gui-" + module["id"])
    run = harness.run_native_module(base, executable, module)
    run["module"] = module
    return run


def _dump(run):
    return json.dumps({
        "returncode": run["returncode"],
        "command": run["command"],
        "report": run["report"],
        "stderr_tail": (run["stderr"] or "")[-4000:],
        "stdout_tail": (run["stdout"] or "")[-4000:],
    }, ensure_ascii=False, indent=2)


def test_native_gui_module_passes(native_gui_run):
    assert native_gui_run["returncode"] == 0, _dump(native_gui_run)
    report = native_gui_run["report"]
    assert report is not None, _dump(native_gui_run)
    assert report["ok"] is True, _dump(native_gui_run)
    assert report["failed"] == 0, _dump(native_gui_run)
    assert report["passed"] >= 1, _dump(native_gui_run)
    assert report["stage"] == "done", _dump(native_gui_run)
    failures = [t for t in report["tests"] if not t["ok"]]
    assert failures == [], _dump(native_gui_run)


def test_native_gui_report_is_explicit(native_gui_run):
    report = native_gui_run["report"]
    module = native_gui_run["module"]
    assert report is not None, _dump(native_gui_run)
    assert report["module"] == module["id"], _dump(native_gui_run)
    assert report["module_path"].replace("\\", "/").endswith(module["path"]), _dump(native_gui_run)
    assert report["preloads"] == list(module.get("preloads") or ()), _dump(native_gui_run)
    assert report["blender_version"].startswith("4.2."), _dump(native_gui_run)
    out_dir = Path(report["out_dir"]).resolve()
    assert out_dir.is_dir(), _dump(native_gui_run)
    assert out_dir == Path(native_gui_run["work_dir"]).resolve() / "out", _dump(native_gui_run)