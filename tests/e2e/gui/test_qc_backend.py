"""Host-side backend integration (B layer): qc self-check.

No Blender is started here.  The test talks HTTP to an already running plugin
backend (the same one the GUI uses) and verifies that the host-side qc math
reports a perfect self-match and recovers synthetic shift/scale edits through
alignment search.  When no backend is listening it skips with an explicit
reason instead of faking a pass.
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request

import pytest

API = os.environ.get("DSH_BLENDER_API", "http://127.0.0.1:9877").rstrip("/")

pytestmark = pytest.mark.gui


def _get(path, timeout=5):
    with urllib.request.urlopen(API + path, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


def _plan(op, args=None, timeout=180):
    body = json.dumps({"op": op, "args": args or {}, "force": True}).encode("utf-8")
    request = urllib.request.Request(
        API + "/plan", data=body, headers={"content-type": "application/json"})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        payload = json.loads(response.read().decode("utf-8"))
    result = payload.get("result") if isinstance(payload, dict) else None
    if isinstance(result, str):
        result = json.loads(result)
    if result is None and isinstance(payload, dict):
        return payload
    assert isinstance(result, dict) and result, payload
    return result


def _require_backend():
    try:
        health = _get("/health", timeout=3)
    except (urllib.error.URLError, OSError, ValueError) as exc:
        pytest.skip(
            "backend not running at %s (%s); start the plugin backend with a real Blender "
            "add-on connected, e.g. npm run build && node lib/runtime/gateway.js" % (API, exc))
    assert health.get("ok") is True, health
    return health


def test_backend_health_is_honest():
    health = _require_backend()
    assert health["ok"] is True
    provenance = health.get("provenance") or {}
    assert provenance.get("pluginVersion") == "3.0.6", provenance
    assert isinstance(health.get("backend"), dict) and health["backend"].get("pid"), health


def test_qc_self_check_metrics():
    _require_backend()
    result = _plan("qc_self_check")
    if not result.get("ok"):
        diagnosis = result.get("diagnosis") or {}
        if diagnosis.get("kind") == "blender-unreachable" or "ECONNREFUSED" in str(result.get("error")):
            pytest.skip(
                "backend at %s is up but no GUI Blender add-on is connected; the /plan route "
                "is proxied through the add-on, so the qc case needs the normal GUI session" % API)
        raise AssertionError(result)
    assert result.get("ok") is True, result
    assert float(result.get("iou_self") or 0.0) == 1.0, result
    assert float(result.get("iou_shift12px") or 0.0) == 1.0, result
    assert float(result.get("iou_scale115") or 0.0) > 0.97, result
    metrics = result.get("self_metrics") or {}
    assert float(metrics.get("dice") or 0.0) == 1.0, result
    assert int(metrics.get("missing_px") if metrics.get("missing_px") is not None else -1) == 0, result