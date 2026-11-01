# -*- coding: utf-8 -*-
"""Blender-side runner for native E2E test modules (T11b).

This is intentionally a thin runner, not pytest.  Blender's bundled Python does
not ship pytest, and the previous archived-case runner executed whole scripts
with module-level sys.exit calls.  A native module instead defines
test_<scenario>(ctx) functions; this runner boots the kernel, loads the module,
runs each test against a factory-reset scene, and writes a JSON report.

argv after --:
    module_path report_path out_dir comma_separated_preloads repo_root

The out_dir is explicit: native modules must never derive it from sys.argv's
last element (that bug made legacy contract/deliver/txn write into the repo).
"""

from __future__ import annotations

import importlib.util
import json
import os
import sys
import time
import traceback
from pathlib import Path

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))


def _split_argv(argv):
    if "--" in argv:
        argv = argv[argv.index("--") + 1:]
    else:
        argv = argv[1:]
    if len(argv) < 5:
        raise SystemExit("native_runner: expected module report outdir preloads repo_root")
    module_path = argv[0]
    report_path = argv[1]
    out_dir = argv[2]
    preloads = [p for p in argv[3].split(",") if p]
    repo_root = argv[4]
    return module_path, report_path, out_dir, preloads, repo_root


def _boot(repo_root, preloads):
    repo = Path(repo_root)
    sys.path.insert(0, str(repo))
    bootstrap_path = repo / "runtime" / "py" / "core" / "offline_bootstrap.py"
    spec = importlib.util.spec_from_file_location("offline_bootstrap", str(bootstrap_path))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.boot(list(preloads))


def _load_module(module_path, module_id):
    path = Path(module_path)
    if not path.is_file():
        raise AssertionError("native module missing: %s" % path)
    # Keep sibling helpers (native/upstream/support.py) importable.
    for candidate in (path.parent, path.parent.parent):
        if str(candidate) not in sys.path:
            sys.path.insert(0, str(candidate))
    spec = importlib.util.spec_from_file_location("dsh_native_%s" % module_id, str(path))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _collect_tests(module):
    tests = []
    for name in dir(module):
        if not name.startswith("test_"):
            continue
        value = getattr(module, name)
        if not callable(value):
            continue
        tests.append((getattr(value, "__code__", None).co_firstlineno if hasattr(value, "__code__") else 0, name))
    tests.sort()
    return [name for _, name in tests]


def main():
    module_path, report_path, out_dir, preloads, repo_root = _split_argv(sys.argv)
    module_id = Path(module_path).stem
    report = {
        "module": module_id,
        "module_path": str(module_path),
        "preloads": preloads,
        "out_dir": str(out_dir),
        "blender_version": None,
        "ok": False,
        "stage": "boot",
        "tests": [],
        "passed": 0,
        "failed": 0,
        "error": None,
    }
    try:
        _boot(repo_root, preloads)
        import bpy  # noqa: E402  (inside Blender only)
        report["blender_version"] = ".".join(str(x) for x in bpy.app.version[:3])
        import native_support  # noqa: E402  (Blender-side helper next to this runner)

        module = _load_module(module_path, module_id)
        names = _collect_tests(module)
        if not names:
            raise AssertionError("native module has no test_* functions: %s" % module_path)
        report["stage"] = "run"
        report["discovered"] = names
        for name in names:
            fn = getattr(module, name)
            ctx = native_support.Context(module_id, out_dir, repo_root)
            native_support.reset_scene()
            started = time.time()
            result = {"name": name, "ok": False, "ms": 0, "checks": 0, "failures": [], "error": None}
            try:
                ctx.begin(name)
                setup = getattr(module, "setup", None)
                if callable(setup):
                    setup(ctx)
                fn(ctx)
            except BaseException:
                result["error"] = traceback.format_exc()
                print("NATIVE_TEST_ERROR " + module_id + "::" + name)
                print(result["error"])
            finally:
                try:
                    teardown = getattr(module, "teardown", None)
                    if callable(teardown):
                        teardown(ctx)
                except BaseException:
                    if result["error"] is None:
                        result["error"] = traceback.format_exc()
                try:
                    ctx.cleanup()
                except BaseException:
                    pass
            result["ms"] = int((time.time() - started) * 1000)
            result["checks"] = len(ctx.checks)
            result["failures"] = list(ctx.failures)
            result["ok"] = not result["error"] and not result["failures"]
            report["tests"].append(result)
            print("NATIVE_TEST_DONE " + json.dumps({
                "module": module_id,
                "test": name,
                "ok": result["ok"],
                "ms": result["ms"],
                "checks": result["checks"],
                "failures": [f["name"] for f in result["failures"]],
            }, ensure_ascii=False))
            native_support.reset_scene()
        report["passed"] = sum(1 for t in report["tests"] if t["ok"])
        report["failed"] = len(report["tests"]) - report["passed"]
        report["ok"] = report["failed"] == 0
        report["stage"] = "done"
    except BaseException:
        report["error"] = traceback.format_exc()
        report["ok"] = False
        print("NATIVE_RUNNER_ERROR " + report["error"])
    finally:
        report_path_obj = Path(report_path)
        report_path_obj.parent.mkdir(parents=True, exist_ok=True)
        report_path_obj.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        print("NATIVE_REPORT " + json.dumps({
            "module": report["module"],
            "ok": report["ok"],
            "passed": report["passed"],
            "failed": report["failed"],
            "stage": report["stage"],
            "tests": [t["name"] for t in report["tests"]],
        }, ensure_ascii=False))
    code = 0 if report["ok"] else 1
    sys.stdout.flush()
    sys.stderr.flush()
    if os.environ.get("NATIVE_GUI") == "1":
        # A non-background Blender keeps its window alive after the script ends.
        os._exit(code)
    raise SystemExit(code)


if __name__ == "__main__":
    main()
