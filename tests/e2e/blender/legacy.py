"""Runs one archived legacy E2E case in a real Blender process.

The 25 legacy self-tests live in tests/e2e/blender/cases/. They were written for
blender_rt_headless(preload=...): each one assumes the kernel + named module APIs
are already in sys.modules and reports failures with sys.exit(1). This helper
recreates that environment with runtime/py/core/offline_bootstrap.py inside a
throw-away Blender process (--background --factory-startup), then returns the
runner JSON report plus the raw stdout/stderr for the evidence log.

Blender remains a release-machine dependency: require_blender() skips (never
fakes a pass) when no executable is discoverable.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

import harness  # noqa: E402

CASES = HERE / "cases"
RUNNER = HERE / "legacy_runner.py"


def case_path(case: str) -> Path:
    path = CASES / case
    if not path.is_file():
        raise AssertionError("archived legacy case missing: %s" % path)
    return path


def run_case(tmp_path, case, preloads=(), timeout=600, gui=False):
    """Execute one archived case; returns {ok, returncode, report, command, stdout, stderr}."""
    executable = harness.require_blender()
    work_dir = Path(tmp_path) / "legacy-e2e"
    work_dir.mkdir(parents=True, exist_ok=True)
    report_path = work_dir / "report.json"
    command = [executable]
    if not gui:
        command.append("--background")
    command += [
        "--factory-startup",
        "--python-exit-code", "1",
        "--python", str(RUNNER),
        "--", str(case_path(case)), str(report_path), ",".join(preloads), str(harness.REPO_ROOT),
    ]
    env = harness._env(work_dir)
    if gui:
        env["LEGACY_GUI"] = "1"
    completed = subprocess.run(
        command,
        cwd=str(harness.REPO_ROOT),
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout,
    )
    report = None
    if report_path.is_file():
        report = json.loads(report_path.read_text(encoding="utf-8"))
    log_dir = os.environ.get("DSH_T11_E2E_LOG_DIR")
    if log_dir:
        destination = Path(log_dir)
        destination.mkdir(parents=True, exist_ok=True)
        (destination / (case + ".stdout.log")).write_text(completed.stdout or "", encoding="utf-8")
        (destination / (case + ".stderr.log")).write_text(completed.stderr or "", encoding="utf-8")
        (destination / (case + ".report.json")).write_text(
            json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return {
        "command": command,
        "returncode": completed.returncode,
        "report": report,
        "stdout": completed.stdout,
        "stderr": completed.stderr,
    }


def describe_failure(run):
    report = run.get("report") or {}
    return json.dumps({
        "case": report.get("case"),
        "ok": report.get("ok"),
        "stage": report.get("stage"),
        "exit_code": report.get("exit_code"),
        "error": (report.get("error") or "")[-4000:],
        "returncode": run.get("returncode"),
        "stdout_tail": (run.get("stdout") or "")[-6000:],
        "stderr_tail": (run.get("stderr") or "")[-4000:],
    }, ensure_ascii=False, indent=2)
