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
        "preloads": ("geo",),
        "timeout": 600,
    },
    {
        "id": "plan",
        "path": "tests/e2e/blender/native/plan.py",
        "preloads": ("view", "planner", "contract"),
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
        "id": "deliver_shared_material",
        "path": "tests/e2e/blender/native/deliver_shared_material.py",
        "preloads": ("deliver",),
        "timeout": 300,
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
    {
        "id": "qc_render_api",
        "path": "tests/e2e/blender/native/qc_render_api.py",
        "preloads": ("qc", "qc_render"),
        "timeout": 300,
    },
    {
        "id": "qc_dispatch",
        "path": "tests/e2e/blender/native/qc_dispatch.py",
        "preloads": ("qc", "imgtools"),
        "timeout": 300,
    },
    {
        "id": "upstream_sculpt",
        "path": "tests/e2e/blender/native/upstream/upstream_sculpt.py",
        "preloads": ("audit", "sculpt", "mesh_fix", "uv_tools", "printcheck", "sweep", "material", "render_guard", "gate", "human", "vehicle", "clearance", "shapegen", "faceeval", "imgtools", "pipeline"),
        "timeout": 600,
    },
    {
        "id": "upstream_mesh_fix",
        "path": "tests/e2e/blender/native/upstream/upstream_mesh_fix.py",
        "preloads": ("audit", "sculpt", "mesh_fix", "uv_tools", "printcheck", "sweep", "material", "render_guard", "gate", "human", "vehicle", "clearance", "shapegen", "faceeval", "imgtools", "pipeline"),
        "timeout": 600,
    },
    {
        "id": "upstream_uv",
        "path": "tests/e2e/blender/native/upstream/upstream_uv.py",
        "preloads": ("audit", "sculpt", "mesh_fix", "uv_tools", "printcheck", "sweep", "material", "render_guard", "gate", "human", "vehicle", "clearance", "shapegen", "faceeval", "imgtools", "pipeline"),
        "timeout": 600,
    },
    {
        "id": "upstream_print",
        "path": "tests/e2e/blender/native/upstream/upstream_print.py",
        "preloads": ("audit", "sculpt", "mesh_fix", "uv_tools", "printcheck", "sweep", "material", "render_guard", "gate", "human", "vehicle", "clearance", "shapegen", "faceeval", "imgtools", "pipeline"),
        "timeout": 600,
    },
    {
        "id": "upstream_sweep",
        "path": "tests/e2e/blender/native/upstream/upstream_sweep.py",
        "preloads": ("audit", "sculpt", "mesh_fix", "uv_tools", "printcheck", "sweep", "material", "render_guard", "gate", "human", "vehicle", "clearance", "shapegen", "faceeval", "imgtools", "pipeline"),
        "timeout": 600,
    },
    {
        "id": "upstream_audit_smoke",
        "path": "tests/e2e/blender/native/upstream/upstream_audit_smoke.py",
        "preloads": ("audit", "sculpt", "mesh_fix", "uv_tools", "printcheck", "sweep", "material", "render_guard", "gate", "human", "vehicle", "clearance", "shapegen", "faceeval", "imgtools", "pipeline"),
        "timeout": 600,
    },
    {
        "id": "upstream_material_render",
        "path": "tests/e2e/blender/native/upstream/upstream_material_render.py",
        "preloads": ("audit", "sculpt", "mesh_fix", "uv_tools", "printcheck", "sweep", "material", "render_guard", "gate", "human", "vehicle", "clearance", "shapegen", "faceeval", "imgtools", "pipeline"),
        "timeout": 600,
    },
    {
        "id": "upstream_gate",
        "path": "tests/e2e/blender/native/upstream/upstream_gate.py",
        "preloads": ("audit", "sculpt", "mesh_fix", "uv_tools", "printcheck", "sweep", "material", "render_guard", "gate", "human", "vehicle", "clearance", "shapegen", "faceeval", "imgtools", "pipeline"),
        "timeout": 600,
    },
    {
        "id": "upstream_assembly_vehicle",
        "path": "tests/e2e/blender/native/upstream/upstream_assembly_vehicle.py",
        "preloads": ("audit", "sculpt", "mesh_fix", "uv_tools", "printcheck", "sweep", "material", "render_guard", "gate", "human", "vehicle", "clearance", "shapegen", "faceeval", "imgtools", "pipeline"),
        "timeout": 600,
    },
    {
        "id": "upstream_shape",
        "path": "tests/e2e/blender/native/upstream/upstream_shape.py",
        "preloads": ("audit", "sculpt", "mesh_fix", "uv_tools", "printcheck", "sweep", "material", "render_guard", "gate", "human", "vehicle", "clearance", "shapegen", "faceeval", "imgtools", "pipeline"),
        "timeout": 600,
    },
    {
        "id": "upstream_pipeline",
        "path": "tests/e2e/blender/native/upstream/upstream_pipeline.py",
        "preloads": ("audit", "sculpt", "mesh_fix", "uv_tools", "printcheck", "sweep", "material", "render_guard", "gate", "human", "vehicle", "clearance", "shapegen", "faceeval", "imgtools", "pipeline"),
        "timeout": 600,
    },
)

GUI_MODULES = (
    {
        "id": "contract_evidence",
        "path": "tests/e2e/gui/native/contract_evidence.py",
        "preloads": ("view", "contract"),
        "timeout": 600,
    },
    {
        "id": "view_capture",
        "path": "tests/e2e/gui/native/view_capture.py",
        "preloads": ("view",),
        "timeout": 600,
    },
)