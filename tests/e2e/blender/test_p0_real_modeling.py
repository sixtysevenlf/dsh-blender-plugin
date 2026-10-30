"""P0 真实建模端到端（参考图 -> 建模 -> 渲染 -> digest 闭环）。

E2E layer (T11 migration): archived case cases/p0_real_modeling_selftest.py executed in a real Blender
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

PRELOADS = ("imgtools",)


def test_p0_real_modeling(tmp_path):
    run = legacy.run_case(tmp_path, "p0_real_modeling_selftest.py", preloads=PRELOADS, timeout=600)
    assert run["report"] is not None, legacy.describe_failure(run)
    assert run["report"]["ok"] is True, legacy.describe_failure(run)
