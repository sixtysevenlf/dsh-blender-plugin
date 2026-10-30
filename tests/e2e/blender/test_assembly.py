"""装配层 D1/D3/D4/D5/D7/D8 回归（浅穿模/包含/隐藏交叠/接触/悬空/法线）。

E2E layer (T11 migration): archived case cases/assembly_selftest.py executed in a real Blender
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

PRELOADS = ("assembly", "audit", "contract", "clearance", "gate",)


def test_assembly(tmp_path):
    run = legacy.run_case(tmp_path, "assembly_selftest.py", preloads=PRELOADS, timeout=600)
    assert run["report"] is not None, legacy.describe_failure(run)
    assert run["report"]["ok"] is True, legacy.describe_failure(run)
