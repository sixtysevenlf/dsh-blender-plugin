# -*- coding: utf-8 -*-
"""Host-side manifest for native E2E modules (T11b).

This file is imported by the host pytest launcher only; it must never import
bpy.  Each entry provides the module path, the kernel preloads, and a timeout.

The module filenames deliberately do not start with test_/ and live outside the
default pytest testpaths: they are loaded inside Blender, not by pytest.
"""

from __future__ import annotations

HEADLESS_MODULES = (
    {
        "id": "geo",
        "path": "tests/e2e/blender/native/geo.py",
        "preloads": (),
        "timeout": 600,
    },
    {
        "id": "plan",
        "path": "tests/e2e/blender/native/plan.py",
        "preloads": ("view",),
        "timeout": 600,
    },
    {
        "id": "contract",
        "path": "tests/e2e/blender/native/contract.py",
        "preloads": ("contract",),
        "timeout": 600,
    },
    {
        "id": "view_diag",
        "path": "tests/e2e/blender/native/view_diag.py",
        "preloads": ("view",),
        "timeout": 600,
    },
    {
        "id": "audit_review",
        "path": "tests/e2e/blender/native/audit_review.py",
        "preloads": ("audit", "calib", "gate", "printcheck"),
        "timeout": 600,
    },
    {
        "id": "gate_review",
        "path": "tests/e2e/blender/native/gate_review.py",
        "preloads": ("audit", "gate", "printcheck", "uv_tools", "material"),
        "timeout": 600,
    },
)

GUI_MODULES = ()