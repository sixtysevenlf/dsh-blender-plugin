"""Shared runner for the split upstream integration E2E wrappers."""
from __future__ import annotations

import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

import legacy  # noqa: E402

PRELOADS = (
    "audit", "sculpt", "mesh_fix", "uv_tools", "printcheck", "sweep", "material",
    "render_guard", "gate", "human", "vehicle", "clearance", "shapegen", "faceeval",
    "imgtools", "pipeline",
)


def run_split_case(tmp_path, case, timeout=600):
    run = legacy.run_case(tmp_path, case, preloads=PRELOADS, timeout=timeout)
    assert run["report"] is not None, legacy.describe_failure(run)
    assert run["report"]["ok"] is True, legacy.describe_failure(run)
