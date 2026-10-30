"""Real-Blender harness for the add-on E2E layer (T10).

Spawns Blender 4.2 LTS with --background --factory-startup, points
BLENDER_USER_SCRIPTS at a throw-away directory, installs the committed add-on
zip, calls the runner inside Blender, and returns the JSON check report plus the
raw stdout/stderr so the evidence pack can keep the real log.

Blender is a release-machine dependency: tests skip (never fake a pass) when no
executable is configured or discoverable.
"""

from __future__ import annotations

import json
import os
import subprocess
import tempfile
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
ZIP_PATH = REPO_ROOT / "addon" / "dsh_blender_bridge.zip"
RUNNER = Path(__file__).resolve().parent / "runner.py"

DEFAULT_BLENDER_CANDIDATES = (
    os.environ.get("DSH_BLENDER_EXE", ""),
    "D:/DSH/tools/blender-win/blender-4.2.10-windows-x64/blender.exe",
    "C:/Program Files/Blender Foundation/Blender 4.2/blender.exe",
    "/usr/bin/blender",
    "/Applications/Blender.app/Contents/MacOS/Blender",
)


def blender_executable():
    if os.environ.get("DSH_BLENDER_EXE"):
        candidate = Path(os.environ["DSH_BLENDER_EXE"])
        return str(candidate) if candidate.is_file() else None
    for raw in DEFAULT_BLENDER_CANDIDATES[1:]:
        candidate = Path(raw)
        if candidate.is_file():
            return str(candidate)
    return None


def require_blender():
    executable = blender_executable()
    if not executable:
        pytest.skip("no Blender executable: set DSH_BLENDER_EXE to run the E2E layer")
    return executable


def _env(work_dir: Path):
    temp_dir = work_dir / "tmp"
    user_scripts = work_dir / "user_scripts"
    temp_dir.mkdir(parents=True, exist_ok=True)
    user_scripts.mkdir(parents=True, exist_ok=True)
    env = dict(os.environ)
    env.update({
        "TEMP": str(temp_dir),
        "TMP": str(temp_dir),
        "BLENDER_USER_SCRIPTS": str(user_scripts),
        "PYTHONIOENCODING": "utf-8",
        "PYTHONUTF8": "1",
    })
    return env


def run_runner(tmp_path: Path, executable: str, timeout=300):
    work_dir = Path(tmp_path) / "blender-e2e"
    work_dir.mkdir(parents=True, exist_ok=True)
    results_path = work_dir / "results.json"
    command = [
        executable,
        "--background",
        "--factory-startup",
        "--python-exit-code", "1",
        "--python", str(RUNNER),
        "--", str(ZIP_PATH), str(results_path), str(work_dir),
    ]
    completed = subprocess.run(
        command,
        cwd=str(REPO_ROOT),
        env=_env(work_dir),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout,
    )
    report = None
    if results_path.is_file():
        report = json.loads(results_path.read_text(encoding="utf-8"))
    log_dir = os.environ.get("DSH_T10_E2E_LOG_DIR")
    if log_dir:
        destination = Path(log_dir)
        destination.mkdir(parents=True, exist_ok=True)
        (destination / "blender-headless.stdout.log").write_text(completed.stdout, encoding="utf-8")
        (destination / "blender-headless.stderr.log").write_text(completed.stderr, encoding="utf-8")
        (destination / "blender-headless.results.json").write_text(
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
    assert name in checks, "runner did not report check " + name + ": " + json.dumps(report)
    return checks[name]
