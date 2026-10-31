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
    {
        "id": "assembly",
        "path": "tests/e2e/blender/native/assembly.py",
        "preloads": ("assembly", "audit", "contract", "clearance", "gate"),
        "timeout": 600,
    },
    {
        "id": "assembly_export",
        "path": "tests/e2e/blender/native/assembly_export.py",
        "preloads": ("assembly", "audit", "contract", "clearance", "gate", "mesh_fix", "geo", "txn", "deliver"),
        "timeout": 600,
    },
    {
        "id": "assembly_repair",
        "path": "tests/e2e/blender/native/assembly_repair.py",
        "preloads": ("assembly", "audit", "contract", "clearance", "gate", "mesh_fix", "geo", "txn"),
        "timeout": 600,
    },
    {
        "id": "mate",
        "path": "tests/e2e/blender/native/mate.py",
        "preloads": ("view", "contract"),
        "timeout": 600,
    },
    {
        "id": "audit_file",
        "path": "tests/e2e/blender/native/audit_file.py",
        "preloads": ("audit",),
        "timeout": 600,
    },
    {
        "id": "shapesolve",
        "path": "tests/e2e/blender/native/shapesolve.py",
        "preloads": ("shapesolve",),
        "timeout": 600,
    },
    {
        "id": "surface",
        "path": "tests/e2e/blender/native/surface.py",
        "preloads": ("surface", "audit"),
        "timeout": 600,
    },
    {
        "id": "blockout",
        "path": "tests/e2e/blender/native/blockout.py",
        "preloads": ("blockout", "qc", "qc_render"),
        "timeout": 600,
    },
    {
        "id": "blockout_refsheet",
        "path": "tests/e2e/blender/native/blockout_refsheet.py",
        "preloads": ("blockout", "qc", "qc_render", "montage"),
        "timeout": 600,
    },
    {
        "id": "imgtools",
        "path": "tests/e2e/blender/native/imgtools.py",
        "preloads": ("imgtools",),
        "timeout": 600,
    },
    {
        "id": "visualreport",
        "path": "tests/e2e/blender/native/visualreport.py",
        "preloads": ("visualreport", "qc", "imgtools"),
        "timeout": 600,
    },
    {
        "id": "p0_real_modeling",
        "path": "tests/e2e/blender/native/p0_real_modeling.py",
        "preloads": ("imgtools", "blockout", "assembly"),
        "timeout": 600,
    },
    {
        "id": "deliver",
        "path": "tests/e2e/blender/native/deliver.py",
        "preloads": ("deliver",),
        "timeout": 600,
    },
    {
        "id": "evalharness",
        "path": "tests/e2e/blender/native/evalharness.py",
        "preloads": (
            "evalharness", "assembly", "audit", "contract", "clearance",
            "gate", "imgtools", "calib", "mesh_fix",
        ),
        "timeout": 600,
    },
    {
        "id": "txn",
        "path": "tests/e2e/blender/native/txn.py",
        "preloads": ("txn",),
        "timeout": 600,
    },
    {
        "id": "txn_restore",
        "path": "tests/e2e/blender/native/txn_restore.py",
        "preloads": ("txn",),
        "timeout": 600,
    },
    {
        "id": "motion",
        "path": "tests/e2e/blender/native/motion.py",
        "preloads": ("motion",),
        "timeout": 600,
    },
)

GUI_MODULES = ()