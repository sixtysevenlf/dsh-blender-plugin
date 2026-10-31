"""Host-side launcher for native in-Blender E2E modules (T11b).

The modules under tests/e2e/blender/native/ are plain Python files, not pytest
tests: Blender's bundled Python has no pytest.  This launcher starts a real
Blender 4.2 headless process per module and asserts the JSON report the runner
writes.  It never imports bpy itself, so it is collectable on any machine (it
skips when no Blender executable is configured).

Run: npm run test:e2e       (or DSH_BLENDER_EXE=... pytest -m blender tests/e2e)
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

import harness  # noqa: E402
import native_manifest  # noqa: E402

pytestmark = pytest.mark.blender


def _ids(modules):
    return [m["id"] for m in modules]


@pytest.fixture(scope="session", params=native_manifest.HEADLESS_MODULES, ids=_ids(native_manifest.HEADLESS_MODULES))
def native_run(request, tmp_path_factory):
    executable = harness.require_blender()
    module = request.param
    base = tmp_path_factory.mktemp("t11b-native-" + module["id"])
    return harness.run_native(base, executable, module)


def _dump(run):
    return json.dumps({
        "returncode": run["returncode"],
        "command": run["command"],
        "report": run["report"],
        "stderr_tail": (run["stderr"] or "")[-4000:],
        "stdout_tail": (run["stdout"] or "")[-4000:],
    }, ensure_ascii=False, indent=2)


def test_native_module_passes(native_run):
    assert native_run["returncode"] == 0, _dump(native_run)
    report = native_run["report"]
    assert report is not None, _dump(native_run)
    assert report["ok"] is True, _dump(native_run)
    assert report["failed"] == 0, _dump(native_run)
    assert report["passed"] >= 1, _dump(native_run)
    assert report["stage"] == "done", _dump(native_run)
    failures = [t for t in report["tests"] if not t["ok"]]
    assert failures == [], _dump(native_run)


def test_native_report_is_explicit_and_repo_clean(native_run):
    report = native_run["report"]
    module = native_run["module"]
    assert report is not None, _dump(native_run)
    assert report["module"] == module["id"], _dump(native_run)
    assert report["module_path"].replace("\\", "/").endswith(module["path"]), _dump(native_run)
    assert report["preloads"] == list(module.get("preloads") or ()), _dump(native_run)
    assert report["blender_version"].startswith("4.2."), _dump(native_run)
    out_dir = Path(report["out_dir"]).resolve()
    assert out_dir.is_dir(), _dump(native_run)
    assert Path(native_run["out_dir"]).resolve() == out_dir, _dump(native_run)
    # The explicit out_dir must live under the harness work dir, never the repo.
    work_dir = Path(native_run["work_dir"]).resolve()
    assert out_dir == work_dir / "out", _dump(native_run)


def test_manifest_modules_are_unique_and_not_pytest_named():
    ids = [m["id"] for m in native_manifest.HEADLESS_MODULES + native_manifest.GUI_MODULES]
    assert len(ids) == len(set(ids))
    for module in native_manifest.HEADLESS_MODULES + native_manifest.GUI_MODULES:
        path = Path(module["path"])
        assert path.suffix == ".py"
        assert not path.name.startswith("test_"), path
        assert module.get("timeout", 0) >= 60
