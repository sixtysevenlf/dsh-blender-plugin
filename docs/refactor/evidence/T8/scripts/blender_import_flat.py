# -*- coding: utf-8 -*-
"""T8 evidence: flat-import smoke: only runtime/ on sys.path (old-style import <name>).

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

sys.path.insert(0, RT)
import py.core.kit  # registers K.dsh_kit without importing repo-root package
flat_modules = [f[:-3] for f in sorted(os.listdir(RT)) if f.endswith(".py")]
for name in flat_modules:
    check(name, name)
print("FLAT_MODULES", len(flat_modules), "FAILED", len(failures))
print("T8_FLAT_IMPORT_SMOKE", "PASS" if not failures else "FAIL")
sys.exit(0 if not failures else 1)
