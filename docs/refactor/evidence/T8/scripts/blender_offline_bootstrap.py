# -*- coding: utf-8 -*-
"""T8 evidence: offline bootstrap across the five domain packages.

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
from runtime.py.core import offline_bootstrap as ob
print("DISCOVERED_RUNTIME", ob.RT)
ns = ob.boot(["vehicle", "audit", "qc"])
kapi = ns["kapi"]
for name in ("vehicle", "audit", "qc"):
    api = kapi(name)
    assert isinstance(api, dict) and api, "missing API for " + name
    print("API", name, "OK", len(api))
print("T8_OFFLINE_BOOTSTRAP PASS")
