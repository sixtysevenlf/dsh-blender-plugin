"""GUI Blender harness: real window, Connect button, 9876+ socket round-trip.

The probe runs inside a normal (non-background) Blender because the add-on
refuses to start its socket server headless. It installs the committed zip,
presses the real "Connect" operator, talks to the listening socket from a
worker thread while Blender's main thread keeps servicing timers, then
disconnects and removes the add-on.

Unlike the headless layer this needs an interactive desktop session; when the
run cannot open a window the test skips or fails honestly (never a fake pass).
"""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
ZIP_PATH = REPO_ROOT / "addon" / "dsh_blender_bridge.zip"
PROBE = Path(__file__).resolve().parent / "gui_connect_probe.py"

DEFAULT_BLENDER_CANDIDATES = (
    "D:/DSH/tools/blender-win/blender-4.2.10-windows-x64/blender.exe",
    "C:/Program Files/Blender Foundation/Blender 4.2/blender.exe",
    "/usr/bin/blender",
    "/Applications/Blender.app/Contents/MacOS/Blender",
)


def blender_executable():
    configured = os.environ.get("DSH_BLENDER_EXE")
    if configured:
        return configured if Path(configured).is_file() else None
    for raw in DEFAULT_BLENDER_CANDIDATES:
        candidate = Path(raw)
        if candidate.is_file():
            return str(candidate)
    return None


def require_blender():
    executable = blender_executable()
    if not executable:
        pytest.skip("no Blender executable: set DSH_BLENDER_EXE to run the GUI E2E")
    return executable


def require_desktop():
    if os.environ.get("T10_SKIP_GUI") == "1":
        pytest.skip("T10_SKIP_GUI=1")
    if os.environ.get("CI") and os.name != "nt":
        pytest.skip("no interactive display in this CI job")


def run_probe(tmp_path: Path, executable: str, timeout=240):
    work_dir = Path(tmp_path) / "blender-gui"
    temp_dir = work_dir / "tmp"
    user_scripts = work_dir / "user_scripts"
    results_path = work_dir / "gui-results.json"
    for path in (work_dir, temp_dir, user_scripts):
        path.mkdir(parents=True, exist_ok=True)
    env = dict(os.environ)
    env.update({
        "TEMP": str(temp_dir),
        "TMP": str(temp_dir),
        "BLENDER_USER_SCRIPTS": str(user_scripts),
        "PYTHONIOENCODING": "utf-8",
        "PYTHONUTF8": "1",
    })
    command = [
        executable,
        "--factory-startup",
        "--python-exit-code", "1",
        "--python", str(PROBE),
        "--", str(ZIP_PATH), str(results_path), str(work_dir),
    ]
    completed = subprocess.run(
        command, cwd=str(REPO_ROOT), env=env, capture_output=True, text=True,
        encoding="utf-8", errors="replace", timeout=timeout,
    )
    report = None
    if results_path.is_file():
        report = json.loads(results_path.read_text(encoding="utf-8"))
    log_dir = os.environ.get("DSH_T10_E2E_LOG_DIR")
    if log_dir:
        destination = Path(log_dir)
        destination.mkdir(parents=True, exist_ok=True)
        (destination / "blender-gui.stdout.log").write_text(completed.stdout, encoding="utf-8")
        (destination / "blender-gui.stderr.log").write_text(completed.stderr, encoding="utf-8")
        (destination / "blender-gui.results.json").write_text(
            json.dumps(report, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    return {
        "command": command,
        "returncode": completed.returncode,
        "stdout": completed.stdout,
        "stderr": completed.stderr,
        "report": report,
        "work_dir": str(work_dir),
    }


def check(run, name):
    report = run.get("report") or {}
    checks = report.get("checks") or {}
    assert name in checks, "GUI probe did not report check " + name + ": " + json.dumps(report)
    return checks[name]
