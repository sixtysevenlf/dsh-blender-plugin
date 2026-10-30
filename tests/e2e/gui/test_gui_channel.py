"""GUI Connect probe (T11 migration of tests/gui_channel_probe.mjs).

Archived probe cases/gui_channel_probe.mjs is executed with the repository Node
runtime against a real GUI Blender + add-on + backend. Release-gated: marked gui,
excluded from the default pytest run, and skipped unless DSH_BLENDER_GUI_E2E=1
is set (this environment has no GUI Blender session).
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
REPO_ROOT = HERE.parents[2]
PROBE = HERE / "cases" / "gui_channel_probe.mjs"

pytestmark = [pytest.mark.blender, pytest.mark.gui]


def test_gui_channel_probe(tmp_path):
    if os.environ.get("DSH_BLENDER_GUI_E2E") != "1":
        pytest.skip("GUI probe needs a running GUI Blender + add-on + backend; set DSH_BLENDER_GUI_E2E=1")
    node = os.environ.get("NODE", "node")
    completed = subprocess.run(
        [node, str(PROBE)],
        cwd=str(REPO_ROOT),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=120,
    )
    (tmp_path / "gui_channel_probe.log").write_text(
        (completed.stdout or "") + "\n--- stderr ---\n" + (completed.stderr or ""), encoding="utf-8")
    assert completed.returncode == 0, (completed.stdout or "")[-4000:] + (completed.stderr or "")[-2000:]
