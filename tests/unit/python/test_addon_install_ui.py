"""Unit checks for the add-on guided virtual-gui prerequisite install.

The plans are the Gateway's /doctor payload; these tests prove the add-on only
executes the displayed one-click argv, never composes its own package-manager
command, and reports manual or failed installs instead of claiming success.
"""

from __future__ import annotations

import importlib
import subprocess

import bpy_stub

bpy = bpy_stub.install()

install = importlib.import_module("addon.dsh_blender_bridge.ui.install")


def doctor(*plans, ready=False, missing=None):
    return {
        "virtualGui": {
            "ready": ready,
            "missing": missing or [],
            "installPlans": list(plans),
        }
    }


def plan(pid="xvfb", *, ok=False, supported=False, one_click=None, command=None, reason=None):
    return {
        "id": pid,
        "label": pid,
        "ok": ok,
        "needsRoot": True,
        "supported": supported,
        "oneClick": one_click,
        "command": command,
        "reason": reason,
    }


def test_doctor_url_is_loopback_http():
    assert install.doctor_url("127.0.0.1", 39877) == "http://127.0.0.1:39877/doctor"


def test_missing_plans_filters_satisfied_rows():
    payload = doctor(
        plan("xvfb"),
        plan("blender", ok=True),
        {"id": "virtualgl", "ok": False, "supported": False, "oneClick": None},
        "not a plan",
    )
    missing = install.missing_plans(payload)
    assert [row["id"] for row in missing] == ["xvfb", "virtualgl"]


def test_one_click_requires_supported_and_argv():
    assert install.one_click_argv(plan("xvfb")) is None
    assert install.one_click_argv(plan("xvfb", supported=True, one_click=[])) is None
    assert install.one_click_argv(
        plan("xvfb", supported=True, one_click=["pkexec", "apt-get", "install", "-y", "xvfb"])
    ) == ["pkexec", "apt-get", "install", "-y", "xvfb"]


def test_format_plan_shows_command_and_manual_reason():
    text = install.format_plan(
        plan(
            "virtualgl",
            command="add the official VirtualGL APT repository: https://github.com/VirtualGL/repo",
            reason="never a one-click install",
        )
    )
    assert "command: add the official VirtualGL APT repository" in text
    assert "manual: never a one-click install" in text
    one_click = install.format_plan(
        plan("xvfb", supported=True, one_click=["pkexec", "apt-get", "install", "-y", "xvfb"])
    )
    assert "one-click: available" in one_click


def test_run_plan_uses_displayed_argv_and_keeps_stderr():
    calls = []

    def runner(argv, **kwargs):
        calls.append((argv, kwargs))
        return subprocess.CompletedProcess(argv, 1, stdout="", stderr="E: Unable to locate package xvfb")

    result = install.run_plan(
        plan("xvfb", supported=True, one_click=["pkexec", "apt-get", "install", "-y", "xvfb"]),
        runner,
    )
    assert calls[0][0] == ["pkexec", "apt-get", "install", "-y", "xvfb"]
    assert calls[0][1]["capture_output"] is True
    assert result["ok"] is False
    assert "Unable to locate package xvfb" in result["stderr"]


def test_run_plan_never_spawns_without_one_click():
    result = install.run_plan(plan("virtualgl"), runner=lambda *a, **k: 0)
    assert result["ok"] is False
    assert "no one-click" in result["detail"]


def test_install_missing_runs_one_click_then_rechecks():
    installed = []

    def runner(argv, **kwargs):
        installed.append(argv)
        return subprocess.CompletedProcess(argv, 0, stdout="", stderr="")

    result = install.install_missing(
        doctor(plan("xvfb", supported=True, one_click=["pkexec", "apt-get", "install", "-y", "xvfb"])),
        runner=runner,
        fetch=lambda: doctor(ready=True),
    )
    assert installed == [["pkexec", "apt-get", "install", "-y", "xvfb"]]
    assert result["ok"] is True
    assert result["installed"] == ["xvfb"]


def test_install_missing_returns_manual_plans_without_spawning():
    result = install.install_missing(
        doctor(plan("virtualgl", command="manual step", reason="no one-click")),
        runner=lambda *a, **k: (_ for _ in ()).throw(AssertionError("must not run")),
        fetch=lambda: doctor(missing=["virtualgl"]),
    )
    assert result["ok"] is False
    assert [row["id"] for row in result["manual"]] == ["virtualgl"]
    assert "virtualgl" in result["missing"]


def test_install_missing_stops_on_failure_and_keeps_stderr():
    def runner(argv, **kwargs):
        return subprocess.CompletedProcess(argv, 1, stdout="", stderr="pkexec: authentication failed")

    result = install.install_missing(
        doctor(plan("xvfb", supported=True, one_click=["pkexec", "apt-get", "install", "-y", "xvfb"])),
        runner=runner,
        fetch=lambda: (_ for _ in ()).throw(AssertionError("must not re-fetch after failure")),
    )
    assert result["ok"] is False
    assert result["plan"] == "xvfb"
    assert "authentication failed" in result["stderr"]
