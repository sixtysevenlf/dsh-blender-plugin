# -*- coding: utf-8 -*-
"""T8 evidence: import every new package module and every old flat path inside Blender 4.2.10.

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
import runtime.py.core.kit  # registers K.dsh_kit, as KERNEL_BOOTSTRAP does
print("KIT_OK", hasattr(sys.modules["dsh_rt_kernel"], "dsh_kit"))

new_modules = []
for domain in ("core", "geometry", "assembly", "render", "export"):
    folder = os.path.join(RT, "py", domain)
    for filename in sorted(os.listdir(folder)):
        if filename.endswith(".py") and filename != "__init__.py":
            new_modules.append((domain, filename[:-3]))
for domain, name in new_modules:
    check("runtime.py.%s.%s" % (domain, name), "new:%s/%s.py" % (domain, name))

flat_modules = [f[:-3] for f in sorted(os.listdir(RT)) if f.endswith(".py")]
for name in flat_modules:
    check("runtime." + name, "old:" + name + ".py")

new_fail = [f for f in failures if f[0].startswith("new:")]
flat_fail = [f for f in failures if f[0].startswith("old:")]
print("NEW_MODULES", len(new_modules), "FAILED", len(new_fail))
print("OLD_FLAT_PATHS", len(flat_modules), "FAILED", len(flat_fail))
print("T8_IMPORT_SMOKE", "PASS" if not failures else "FAIL")
sys.exit(0 if not failures else 1)
