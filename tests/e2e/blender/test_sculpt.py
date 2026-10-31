"""上游整合 A1 雕刻自检（原 upstream_integration_selftest.py 拆分）.

E2E layer: real Blender 4.2 --background --factory-startup via legacy_runner.py.
Release-gated with the "blender" marker; skips (never fakes a pass) without Blender.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

import upstream_support  # noqa: E402

pytestmark = pytest.mark.blender


def test_sculpt(tmp_path):
    upstream_support.run_split_case(tmp_path, "upstream_sculpt_selftest.py")
