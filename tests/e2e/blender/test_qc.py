"""qc 视觉比对（需要插件后端 127.0.0.1:9877 + GUI Blender + addon）。

E2E layer (T11 migration): archived case cases/qc_selftest.py executed in a real Blender
4.2 --background --factory-startup process through legacy_runner.py, which
recreates the legacy blender_rt_headless(preload=...) environment via
runtime/py/core/offline_bootstrap.py. Release-gated: excluded from the default
pytest run by the "blender" marker; require_blender() skips when Blender is
absent (it never fakes a pass).
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

import legacy  # noqa: E402

pytestmark = [pytest.mark.blender, pytest.mark.gui]

def _require_gui_backend():
    if os.environ.get("DSH_BLENDER_GUI_E2E") != "1":
        pytest.skip("GUI E2E needs a running GUI Blender + add-on + backend on 127.0.0.1:9877; set DSH_BLENDER_GUI_E2E=1")

PRELOADS = ()


def test_qc(tmp_path):
    _require_gui_backend()
    run = legacy.run_case(tmp_path, "qc_selftest.py", preloads=PRELOADS, timeout=600)
    assert run["report"] is not None, legacy.describe_failure(run)
    assert run["report"]["ok"] is True, legacy.describe_failure(run)
