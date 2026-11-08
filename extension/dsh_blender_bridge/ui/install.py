"""Guided virtual-gui prerequisite install.

The plan is produced by the Gateway (/doctor -> virtualGui.installPlans) and is
the same payload the host's /blender doctor --fix uses. This module never
composes package-manager commands: a supported one-click plan is exactly the
argv the Gateway displayed, executed with system authentication (pkexec).
Unsupported plans are returned for display and are never executed.
"""

from __future__ import annotations

import json
import subprocess
import urllib.request

from ..config import get_gateway_host, get_gateway_port

DOCTOR_TIMEOUT_S = 5.0
INSTALL_TIMEOUT_S = 600
STDERR_LIMIT = 4096


def doctor_url(host=None, port=None):
    return "http://%s:%d/doctor" % (
        host or get_gateway_host(),
        int(port or get_gateway_port()),
    )


def fetch_doctor(url=None, timeout=DOCTOR_TIMEOUT_S):
    with urllib.request.urlopen(url or doctor_url(), timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


def missing_plans(doctor):
    virtual_gui = (doctor or {}).get("virtualGui") or {}
    plans = virtual_gui.get("installPlans") or []
    return [plan for plan in plans if isinstance(plan, dict) and plan.get("ok") is not True]


def plan_command(plan):
    return str((plan or {}).get("command") or "").strip()


def one_click_argv(plan):
    argv = (plan or {}).get("oneClick")
    if (plan or {}).get("supported") is not True or not isinstance(argv, list) or not argv:
        return None
    return [str(part) for part in argv]


def plan_reason(plan):
    return str((plan or {}).get("reason") or "").strip()


def format_plan(plan):
    label = str((plan or {}).get("label") or (plan or {}).get("id") or "prerequisite")
    lines = [label]
    command = plan_command(plan)
    if command:
        lines.append("  command: " + command)
    if one_click_argv(plan):
        lines.append("  one-click: available (system authentication required)")
    else:
        reason = plan_reason(plan)
        lines.append("  manual" + (": " + reason if reason else ""))
    return "\n".join(lines)


def run_plan(plan, runner=None):
    argv = one_click_argv(plan)
    if not argv:
        return {"ok": False, "detail": "no one-click command", "stderr": ""}
    try:
        completed = (runner or subprocess.run)(
            argv,
            capture_output=True,
            text=True,
            timeout=INSTALL_TIMEOUT_S,
        )
    except Exception as error:  # noqa: BLE001 - surfaced to the operator report
        return {"ok": False, "detail": str(error), "stderr": ""}
    code = getattr(completed, "returncode", None)
    stderr = (getattr(completed, "stderr", "") or "")[-STDERR_LIMIT:]
    if code == 0:
        return {"ok": True, "code": 0, "detail": "installed", "stderr": stderr}
    tail = " ".join(stderr.strip().splitlines()[-3:])
    return {"ok": False, "code": code, "detail": tail or "install failed", "stderr": stderr}


def install_missing(doctor=None, runner=None, fetch=None):
    payload = doctor if doctor is not None else (fetch or fetch_doctor)()
    plans = missing_plans(payload)
    if not plans:
        return {"ok": True, "installed": [], "manual": [], "missing": [], "message": "all prerequisites ready"}
    installed = []
    manual = []
    for plan in plans:
        if not one_click_argv(plan):
            manual.append(plan)
            continue
        result = run_plan(plan, runner)
        if not result.get("ok"):
            return {
                "ok": False,
                "installed": installed,
                "manual": manual,
                "missing": [],
                "message": "install failed: " + str(result.get("detail")),
                "stderr": result.get("stderr") or "",
                "plan": plan.get("id"),
            }
        installed.append(plan.get("id"))
    after = (fetch or fetch_doctor)()
    virtual_gui = (after or {}).get("virtualGui") or {}
    ready = virtual_gui.get("ready") is True
    return {
        "ok": ready,
        "installed": installed,
        "manual": manual,
        "missing": virtual_gui.get("missing") or [],
        "message": "ready" if ready else "still missing prerequisites",
        "stderr": "",
    }
