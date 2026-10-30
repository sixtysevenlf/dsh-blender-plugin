"""evalharness 评测台（回归探针/校准）回归。

E2E layer (T11 migration): archived case cases/evalharness_selftest.py executed in a real Blender
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

pytestmark = pytest.mark.blender

PRELOADS = ("evalharness", "assembly", "audit", "contract", "clearance", "gate", "imgtools", "calib", "mesh_fix",)


def test_evalharness(tmp_path):
    run = legacy.run_case(tmp_path, "evalharness_selftest.py", preloads=PRELOADS, timeout=600)
    assert run["report"] is not None, legacy.describe_failure(run)
    assert run["report"]["ok"] is True, legacy.describe_failure(run)
