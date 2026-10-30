# -*- coding: utf-8 -*-
"""T8 evidence: domain-crossing loaders: contract features json, qc->qc_render, qc_render->montage.

Runs with: blender.exe -b --factory-startup --python this_file

"""
import importlib
import os
import sys
import traceback

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "..", "..", ".."))
RT = os.path.join(ROOT, "runtime")
failures = []


def check(module_name, label):
    try:
        importlib.import_module(module_name)
        return True
    except Exception as exc:  # noqa: BLE001 - evidence wants the real error
        failures.append((label, type(exc).__name__, str(exc)))
        traceback.print_exc(limit=3)
        return False

sys.path.insert(0, ROOT)
import runtime.py.core.kit  # noqa: F401 - registers kernel
import runtime.py.core.contract as contract
features = contract._features_path()
assert os.path.isfile(features), features
print("FEATURES_PATH", features)

import runtime.py.render.qc as qc
qc_render = qc._load_qc_render()
assert qc_render, "qc_render API missing"
print("QC_TO_QC_RENDER OK")

import runtime.py.render.qc_render as qc_render_mod
montage = qc_render_mod._montage_api()
assert montage, "montage API missing"
print("QC_RENDER_TO_MONTAGE OK")
print("T8_DOMAIN_LOADERS PASS")
