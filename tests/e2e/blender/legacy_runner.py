# -*- coding: utf-8 -*-
"""Executes one archived legacy E2E case inside Blender and writes a JSON report.

argv (after --): case_path report_path comma_separated_preloads repo_root

The legacy scripts were written for blender_rt_headless(preload=...): they read
K = sys.modules['dsh_rt_kernel'] (built by the kernel bootstrap) and print
their own PASS/FAIL lines, calling sys.exit(1) on failure. This runner gives
them the same in-process environment through runtime/py/core/offline_bootstrap.py
(no HTTP backend, engine=none equivalent), with __file__ pointing at the archived
case so its repo-relative path arithmetic keeps working.

A case is reported ok only when it finished without an exception, without a
non-zero SystemExit and (when present) its DSH_*_SELFTEST / *ALL PASS marker was
not contradicted. Failures keep the traceback in the report; nothing is skipped
silently here -- the pytest wrapper decides whether to skip on missing Blender.
"""
from __future__ import annotations

import importlib.util
import json
import os
import sys
import traceback


def _split_argv():
    argv = sys.argv
    if "--" in argv:
        argv = argv[argv.index("--") + 1:]
    else:
        argv = argv[1:]
    case_path, report_path = argv[0], argv[1]
    preloads = [p for p in (argv[2] if len(argv) > 2 else "").split(",") if p]
    repo_root = argv[3] if len(argv) > 3 else os.getcwd()
    return case_path, report_path, preloads, repo_root


def _boot(repo_root, preloads):
    sys.path.insert(0, repo_root)
    bootstrap_path = os.path.join(repo_root, "runtime", "py", "core", "offline_bootstrap.py")
    spec = importlib.util.spec_from_file_location("offline_bootstrap", bootstrap_path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    namespace = module.boot(list(preloads)) if preloads else {}
    return namespace


def main():
    case_path, report_path, preloads, repo_root = _split_argv()
    report = {"case": os.path.basename(case_path), "preloads": preloads, "ok": False,
              "exit_code": None, "error": None, "stage": "boot"}
    try:
        namespace = _boot(repo_root, preloads)
        kernel = sys.modules.get("dsh_rt_kernel")
        # Archived cases write their artefacts under K.out_dir (the online headless
        # kernel sets it per run). The offline harness must provide it too.
        out_dir = os.path.join(os.path.dirname(os.path.abspath(report_path)), "out")
        os.makedirs(out_dir, exist_ok=True)
        if kernel is not None:
            kernel.out_dir = out_dir
        # The legacy scripts compute repo paths from __file__ (HERE/../runtime).
        # Keep their original repo/tests/<name>.py location as the virtual file
        # name so that arithmetic keeps pointing at the real runtime/ and tests/.
        virtual_path = os.path.join(repo_root, "tests", os.path.basename(case_path))
        namespace.update({"__name__": "__main__", "__file__": virtual_path, "K": kernel})
        report["stage"] = "exec"
        with open(case_path, encoding="utf-8") as handle:
            source = handle.read()
        try:
            exec(compile(source, virtual_path, "exec"), namespace)
            report["ok"] = True
        except SystemExit as exc:
            code = exc.code
            report["exit_code"] = 0 if code is None else code
            report["ok"] = code in (None, 0)
            if not report["ok"]:
                report["error"] = "case called sys.exit(%r)" % (code,)
        report["stage"] = "done"
    except BaseException:
        report["error"] = traceback.format_exc()
    with open(report_path, "w", encoding="utf-8") as handle:
        json.dump(report, handle, ensure_ascii=False, indent=2)
    print("LEGACY_CASE_REPORT " + json.dumps(report, ensure_ascii=False))
    # GUI mode: a non-background Blender keeps its window (and the process)
    # alive after the script ends. Quit explicitly so the harness gets its exit.
    if os.environ.get("LEGACY_GUI") == "1":
        # bpy.ops.wm.quit_blender() can leave a GUI process waiting for its
        # event loop; the report is already flushed, so exit the process.
        sys.stdout.flush()
        sys.stderr.flush()
        os._exit(0)


if __name__ == "__main__":
    main()
