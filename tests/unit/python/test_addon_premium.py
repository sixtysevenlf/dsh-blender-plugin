"""Unit checks for premium status, request helpers and parameter parsing (premium/)."""

from __future__ import annotations

import importlib
from types import SimpleNamespace

import bpy_stub

bpy = bpy_stub.install()

client = importlib.import_module("addon.dsh_blender_bridge.premium.client")
jobs = importlib.import_module("addon.dsh_blender_bridge.premium.jobs")
generate_premium = importlib.import_module("addon.dsh_blender_bridge.commands.generate_premium")


def _run_scene():
    scene = bpy.context.scene
    for name in ("dshbb_use_tripo", "dshbb_use_hyper3d", "dshbb_use_hunyuan3d"):
        setattr(scene, name, False)
    return scene


def test_premium_error_and_formatting_helpers():
    assert client.premium_error("INVALID_KEY") == {
        "error": client.PREMIUM_ERROR_MESSAGES["INVALID_KEY"],
        "code": "INVALID_KEY",
        "message": client.PREMIUM_ERROR_MESSAGES["INVALID_KEY"],
    }
    assert client.premium_error("NOPE")["message"] == "NOPE"
    assert client.premium_error("PROVIDER_ERROR", "server said so")["message"] == "server said so"
    assert client._premium_format_date("2026-01-02T03:04:05Z") == "Jan 2"
    assert client._premium_format_date("not-a-date") is None
    assert client._premium_format_date(None) is None
    assert client._premium_remaining({"standard": {"remaining": 3}}, "standard") == 3
    assert client._premium_remaining({"standard": {"total": 5, "used": 2}}, "standard") == 3
    assert client._premium_remaining({}, "standard") == 0
    assert len(client.PREMIUM_ERROR_MESSAGES) == 12
    assert client.PREMIUM_TIMEOUT == 30
    assert client.PREMIUM_MAX_IMAGE_BYTES == 8 * 1024 * 1024


def test_premium_active_follows_preferences_and_env(monkeypatch):
    monkeypatch.delenv("DSHBB_PREMIUM_LICENSE_KEY", raising=False)
    monkeypatch.delenv("BLENDERMCP_PREMIUM_LICENSE_KEY", raising=False)
    assert client.premium_active() is False

    monkeypatch.setenv("DSHBB_PREMIUM_LICENSE_KEY", "env-key")
    assert client.premium_active() is True
    monkeypatch.delenv("DSHBB_PREMIUM_LICENSE_KEY")

    prefs = SimpleNamespace(generation_source="PREMIUM")
    monkeypatch.setattr(client, "_premium_prefs", lambda: prefs)
    assert client.premium_active() is True
    prefs.generation_source = "BYOK"
    assert client.premium_active() is False


def test_premium_license_helpers_prefer_preferences_then_env(monkeypatch):
    monkeypatch.delenv("DSHBB_PREMIUM_LICENSE_KEY", raising=False)
    monkeypatch.delenv("DSHBB_PREMIUM_INSTANCE_ID", raising=False)
    prefs = SimpleNamespace(premium_license_key="pref-key", premium_instance_id="pref-instance")
    monkeypatch.setattr(client, "_premium_prefs", lambda: prefs)
    assert client.premium_license_key() == "pref-key"
    assert client.premium_instance_id() == "pref-instance"

    monkeypatch.setenv("DSHBB_PREMIUM_LICENSE_KEY", "env-key")
    monkeypatch.setenv("DSHBB_PREMIUM_INSTANCE_ID", "env-instance")
    prefs.premium_license_key = ""
    prefs.premium_instance_id = ""
    assert client.premium_license_key() == "env-key"
    assert client.premium_instance_id() == "env-instance"
    monkeypatch.delenv("DSHBB_PREMIUM_API_URL", raising=False)
    assert client.premium_api_url() == client.PREMIUM_API_URL_DEFAULT
    monkeypatch.setenv("DSHBB_PREMIUM_API_URL", "https://premium.example/api/")
    assert client.premium_api_url() == "https://premium.example/api"


def test_premium_short_status_and_summary():
    monkeypatch_instance = SimpleNamespace(premium_instance_id=lambda: "")
    original = client.premium_instance_id
    try:
        client.premium_instance_id = lambda: ""
        assert client.premium_short_status() == "Premium: not activated"
        client.premium_instance_id = lambda: "instance"
        previous = client._premium_status
        client._premium_status = {}
        assert client.premium_short_status() == "Premium: activated"
        client._premium_status = {"tier": None}
        assert client.premium_short_status() == "Premium: subscription not active"
        client._premium_status = {
            "tier": "pro",
            "standard": {"total": 10, "used": 1},
            "premium": {"total": 5, "used": 2},
        }
        assert client.premium_short_status() == "Premium: Pro, 9 + 3 left"
        assert client.premium_summary() == (
            "Premium (Pro): 9 standard and 3 high-quality generations left"
        )
        client._premium_status = {
            "tier": "pro",
            "standard": {"total": 10, "used": 1},
            "premium": {"total": 5, "used": 2},
            "resets_at": "2026-01-02T00:00:00Z",
        }
        assert client.premium_summary().endswith(", resets Jan 2")
        client._premium_status = {}
        assert client.premium_summary() == "Premium: no active subscription."
    finally:
        client.premium_instance_id = original


def test_premium_quality_selection_and_high_lock():
    assert client._premium_quality("rodin", None, False) == "high"
    assert client._premium_quality("tripo", None, True) == "high"
    assert client._premium_quality("tripo", None, False) == "standard"
    assert client._premium_quality("hyper3d", "standard", False) == "standard"
    assert client._premium_quality("hunyuan", "high", False) == "high"

    previous = client._premium_status
    try:
        client._premium_status = {"tier": "pro", "premium": {"total": 0}}
        assert client._premium_high_locked() is True
        assert client.premium_default_quality() == "standard"
        client._premium_status = {"tier": "pro", "premium": {"total": 3}}
        assert client._premium_high_locked() is False
        client._premium_status = {"premium": {"total": 0}}
        assert client._premium_high_locked() is False
        client._premium_status = {}
    finally:
        client._premium_status = previous

    prefs = SimpleNamespace(premium_default_quality="high")
    original = client._premium_prefs
    try:
        client._premium_prefs = lambda: prefs
        client._premium_status = {"tier": "pro", "premium": {"total": 3}}
        assert client.premium_default_quality() == "high"
        client._premium_status = {"tier": "pro", "premium": {"total": 0}}
        assert client.premium_default_quality() == "standard"
        prefs.premium_default_quality = "bogus"
        client._premium_status = {"tier": "pro", "premium": {"total": 3}}
        assert client.premium_default_quality() == "standard"
    finally:
        client._premium_prefs = original
        client._premium_status = previous


def test_premium_integration_status_disabled_enabled_and_error(monkeypatch):
    disabled = client.premium_integration_status("tripo", False)
    assert disabled["enabled"] is False
    assert disabled["mode"] == "PREMIUM"
    assert "Tripo is turned off" in disabled["message"]

    monkeypatch.setattr(client, "premium_request", lambda method, path: {"tier": "pro"})
    enabled = client.premium_integration_status("tripo", True)
    assert enabled["enabled"] is True
    assert enabled["mode"] == "PREMIUM"
    assert "Mode: PREMIUM" in enabled["message"]

    monkeypatch.setattr(client, "premium_request", lambda method, path: {"code": "INVALID_KEY", "message": "bad key"})
    failed = client.premium_integration_status("hyper3d", True)
    assert failed == {"enabled": False, "mode": "PREMIUM", "message": "bad key"}

    cached = {"tier": "pro", "standard": {"total": 2}, "premium": {"total": 1}}
    monkeypatch.setattr(client, "_premium_status", cached)
    monkeypatch.setattr(client, "premium_request", lambda method, path: {"code": "NETWORK_ERROR", "message": "down"})
    fallback = client.premium_integration_status("hunyuan3d", True)
    assert fallback["enabled"] is True
    assert "cached; the Premium server is unreachable right now" in fallback["message"]
    assert "Mode: PREMIUM" in fallback["message"]


class _Response:
    def __init__(self, status_code=200, body=None, json_error=False):
        self.status_code = status_code
        self._body = body
        self._json_error = json_error

    def json(self):
        if self._json_error:
            raise ValueError("not json")
        return self._body


def test_premium_request_headers_auth_and_error_mapping(monkeypatch):
    captured = {}

    def fake_request(method, url, headers, json, timeout):
        captured.update({"method": method, "url": url, "headers": headers,
                         "json": json, "timeout": timeout})
        return _Response(200, {"tier": "pro"})

    monkeypatch.setattr(client.requests, "request", fake_request)
    monkeypatch.setenv("DSHBB_PREMIUM_LICENSE_KEY", "license-1")
    monkeypatch.setenv("DSHBB_PREMIUM_INSTANCE_ID", "instance-1")
    monkeypatch.setenv("DSHBB_PREMIUM_API_URL", "https://premium.example/api/")
    result = client.premium_request("POST", "/status", payload={"x": 1})
    assert result == {"tier": "pro"}
    assert captured["method"] == "POST"
    assert captured["url"] == "https://premium.example/api/status"
    assert captured["headers"]["X-Addon-Version"] == "1.11"
    assert captured["headers"]["X-Addon-Protocol"] == "11"
    assert captured["headers"]["Authorization"] == "Bearer license-1"
    assert captured["headers"]["X-Instance-Id"] == "instance-1"
    assert captured["timeout"] == 30

    monkeypatch.delenv("DSHBB_PREMIUM_INSTANCE_ID")
    monkeypatch.setattr(client, "_premium_prefs", lambda: None)
    monkeypatch.setattr(client.requests, "request",
                        lambda *a, **k: (_ for _ in ()).throw(AssertionError("must not request")))
    assert client.premium_request("GET", "/status")["code"] == "NOT_ACTIVATED"

    monkeypatch.setenv("DSHBB_PREMIUM_INSTANCE_ID", "instance-1")
    monkeypatch.delenv("DSHBB_PREMIUM_LICENSE_KEY")
    assert client.premium_request("GET", "/status")["code"] == "INVALID_KEY"


def test_premium_request_transport_and_bad_response_mapping(monkeypatch):
    monkeypatch.setenv("DSHBB_PREMIUM_LICENSE_KEY", "license-1")
    monkeypatch.setenv("DSHBB_PREMIUM_INSTANCE_ID", "instance-1")

    def raise_network(*args, **kwargs):
        raise ConnectionError("no route")

    monkeypatch.setattr(client.requests, "request", raise_network)
    network = client.premium_request("GET", "/status")
    assert network["code"] == "NETWORK_ERROR"
    assert "no route" in network["message"]

    monkeypatch.setattr(client.requests, "request", lambda *a, **k: _Response(200, None, json_error=True))
    bad = client.premium_request("GET", "/status")
    assert bad["code"] == "BAD_RESPONSE"
    assert "HTTP 200" in bad["message"]

    monkeypatch.setattr(client.requests, "request", lambda *a, **k: _Response(500, {"code": "QUOTA_EXHAUSTED", "message": "quota"}))
    server_error = client.premium_request("POST", "/jobs")
    assert server_error["code"] == "QUOTA_EXHAUSTED"
    assert server_error["message"] == "quota"


def test_premium_request_remembers_usage(monkeypatch):
    monkeypatch.setenv("DSHBB_PREMIUM_LICENSE_KEY", "license-1")
    monkeypatch.setenv("DSHBB_PREMIUM_INSTANCE_ID", "instance-1")
    usage = {"tier": "pro", "standard": {"total": 3, "used": 1}, "premium": {"total": 1, "used": 0}}
    monkeypatch.setattr(client.requests, "request",
                        lambda *a, **k: _Response(200, {"ok": True, "usage": usage}))
    original = client._premium_status
    try:
        client.premium_request("GET", "/status")
        assert client._premium_status == usage
    finally:
        client._premium_status = original


# ---------------------------------------------------------------- jobs / params


def test_premium_image_payload_url_tuple_file_and_mime(tmp_path):
    assert jobs._premium_mime("jpg") == "image/jpeg"
    assert jobs._premium_mime(".JPEG") == "image/jpeg"
    assert jobs._premium_mime("webp") == "image/webp"
    assert jobs._premium_mime("png") == "image/png"
    assert jobs._premium_mime(None) == "image/png"

    assert jobs._premium_image_payload("https://example.com/a.png") == (
        {"image_url": "https://example.com/a.png"}, 0,
    )
    import base64
    payload, size = jobs._premium_image_payload(("png", base64.b64encode(b"raw").decode("ascii")))
    assert payload["image_mime"] == "image/png"
    assert base64.b64decode(payload["image_base64"]) == b"raw"
    assert size == 3

    path = tmp_path / "local.jpg"
    path.write_bytes(b"abc")
    payload, size = jobs._premium_image_payload(str(path))
    assert payload["image_mime"] == "image/jpeg"
    assert base64.b64decode(payload["image_base64"]) == b"abc"
    assert size == 3


def test_premium_create_job_validates_arguments(monkeypatch):
    calls = []
    monkeypatch.setattr(jobs, "premium_request", lambda *a, **k: calls.append((a, k)) or {"job_id": "job-1"})

    assert jobs.premium_create_job("tripo")["error"] == "Give exactly one of a text prompt or an image."
    assert jobs.premium_create_job("tripo", text_prompt="x", image="y")["error"] == (
        "Give exactly one of a text prompt or an image."
    )
    assert jobs.premium_create_job("tripo", text_prompt="x" * 1025)["error"] == (
        "Prompt exceeds 1024 characters limit"
    )
    assert jobs.premium_create_job("tripo", text_prompt="x", quality="ultra")["error"] == (
        "quality must be 'standard' or 'high'"
    )
    assert calls == []


def test_premium_create_job_payload_shape_and_network_retry(monkeypatch):
    responses = [{"code": "NETWORK_ERROR", "message": "down"}, {"job_id": "job-1", "status": "reserved"}]
    captured = []

    def fake_request(method, path, payload=None):
        captured.append({"method": method, "path": path, "payload": dict(payload or {})})
        return responses.pop(0)

    monkeypatch.setattr(jobs, "premium_request", fake_request)
    result = jobs.premium_create_job("tripo", quality="high", text_prompt="a cat",
                                     bbox_condition=[[0, 0, 0], [1, 1, 1]])
    assert result["job_id"] == "job-1"
    assert len(captured) == 2
    first, second = captured
    assert first["method"] == "POST" and first["path"] == "/jobs"
    assert first["payload"]["model"] == "tripo"
    assert first["payload"]["quality"] == "high"
    assert first["payload"]["prompt"] == "a cat"
    assert first["payload"]["operation"] == "generate"
    assert first["payload"]["bbox_condition"] == [[0, 0, 0], [1, 1, 1]]
    assert first["payload"]["client_request_id"] == second["payload"]["client_request_id"]

    monkeypatch.setattr(jobs, "premium_request", lambda *a, **k: {"job_id": "job-2"})
    image_result = jobs.premium_create_job("hunyuan", image=("png", "cmF3"))
    assert image_result["job_id"] == "job-2"


def test_premium_create_job_rejects_oversized_images(monkeypatch):
    monkeypatch.setattr(jobs, "premium_request", lambda *a, **k: (_ for _ in ()).throw(
        AssertionError("must not request")
    ))
    captured = {}

    def fake_payload(image):
        captured["image"] = image
        return {"image_base64": "x", "image_mime": "image/png"}, jobs.PREMIUM_MAX_IMAGE_BYTES + 1

    monkeypatch.setattr(jobs, "_premium_image_payload", fake_payload)
    result = jobs.premium_create_job("tripo", image="a.png")
    assert result["error"] == "Image is larger than 8 MB; use a smaller image or an image URL."


def test_premium_job_files_and_import_job(monkeypatch):
    assert jobs._premium_job_files({"files": [{"url": "u"}]}) == [{"url": "u"}]
    fallback = jobs._premium_job_files({"result_url": "https://example.com/model.glb"})
    assert fallback == [{"role": "model", "format": "glb",
                         "url": "https://example.com/model.glb", "file_name": "model.glb"}]
    assert jobs._premium_job_files({}) is None

    assert jobs.premium_import_job("", "name")["error"] == "request_id is required"

    monkeypatch.setattr(jobs, "_premium_job", lambda job_id: {"code": "INVALID_KEY", "message": "bad"})
    assert jobs.premium_import_job("job-1", "name") == {"succeed": False, "code": "INVALID_KEY", "message": "bad"}

    monkeypatch.setattr(jobs, "_premium_job", lambda job_id: {"status": "reserved"})
    assert jobs.premium_import_job("job-1", "name")["error"] == (
        "Generation isn't finished yet. Poll until COMPLETED, then import."
    )

    imported = {}
    monkeypatch.setattr(jobs, "_premium_job", lambda job_id: {"status": "succeeded",
                                                              "files": [{"url": "https://example.com/m.glb"}]})
    monkeypatch.setattr(jobs, "import_model_files", lambda files, name: imported.update(
        {"files": files, "name": name}) or {"succeed": True, "name": name})
    assert jobs.premium_import_job("job-1", "mesh") == {"succeed": True, "name": "mesh"}
    assert imported["name"] == "mesh"


def test_premium_poll_fal_job_status_mapping(monkeypatch):
    assert jobs.premium_poll_fal_job("")["error"] == "request_id is required"
    cases = {
        "reserved": "IN_QUEUE",
        "succeeded": "COMPLETED",
        "failed": "FAILED",
        "running": "IN_PROGRESS",
    }
    for provider_status, expected in cases.items():
        monkeypatch.setattr(jobs, "_premium_job", lambda job_id, s=provider_status: {
            "status": s, "error": "boom",
        })
        reply = jobs.premium_poll_fal_job("job-1")
        assert reply["status"] == expected, provider_status

    # An unmapped status follows the provider's own queue signal.
    monkeypatch.setattr(jobs, "_premium_job", lambda job_id: {
        "status": "unknown", "provider_status": "IN_QUEUE",
    })
    assert jobs.premium_poll_fal_job("job-1")["status"] == "IN_QUEUE"
    monkeypatch.setattr(jobs, "_premium_job", lambda job_id: {"status": "unknown"})
    assert jobs.premium_poll_fal_job("job-1")["status"] == "IN_PROGRESS"

    monkeypatch.setattr(jobs, "_premium_job", lambda job_id: {"error": "bad job"})
    assert jobs.premium_poll_fal_job("job-1") == {"error": "bad job"}


def test_premium_create_tripo_and_rodin_jobs(monkeypatch):
    calls = []

    def fake_create_job(model, quality="standard", **kwargs):
        calls.append({"model": model, "quality": quality, "kwargs": kwargs})
        return {"job_id": "job-%d" % len(calls)}

    monkeypatch.setattr(jobs, "premium_create_job", fake_create_job)
    monkeypatch.setattr(jobs, "premium_summary", lambda: "summary")

    tripo = jobs.premium_create_tripo_job(text_prompt="a cat")
    assert tripo == {"request_id": "job-1", "status": "IN_QUEUE", "message": "summary"}
    assert calls[-1]["model"] == "tripo"
    assert calls[-1]["quality"] == "standard"

    jobs.premium_create_tripo_job(image="https://example.com/a.png")
    assert calls[-1]["quality"] == "high"

    rodin = jobs.premium_create_rodin_job(text_prompt="root", images=["https://example.com/a.png",
                                                                      "https://example.com/b.png"])
    assert rodin["status"] == "IN_QUEUE"
    assert rodin["note"] == "Premium uses the first image only."
    assert calls[-1]["quality"] == "high"

    monkeypatch.setattr(jobs, "premium_create_job", lambda *a, **k: {"code": "NETWORK_ERROR", "message": "down"})
    assert jobs.premium_create_tripo_job(text_prompt="x") == {"code": "NETWORK_ERROR", "message": "down"}


def test_premium_hunyuan_job_shapes(monkeypatch):
    monkeypatch.setattr(jobs, "premium_create_job", lambda *a, **k: {"job_id": "job-9"})
    monkeypatch.setattr(jobs, "premium_summary", lambda: "summary")
    created = jobs.premium_create_hunyuan_job(text_prompt="a chair")
    assert created == {"Response": {"JobId": "job-9"}, "message": "summary"}

    monkeypatch.setattr(jobs, "premium_create_job", lambda *a, **k: {"code": "QUOTA_EXHAUSTED"})
    assert jobs.premium_create_hunyuan_job(text_prompt="x") == {"code": "QUOTA_EXHAUSTED"}

    assert jobs.premium_poll_hunyuan_job_status("")["error"] == "JobId is required"

    monkeypatch.setattr(jobs, "_premium_job", lambda job_id: {"status": "succeeded"})
    assert jobs.premium_poll_hunyuan_job_status("job_9") == {"Response": {"Status": "DONE"}}

    files = [{"role": "model", "format": "obj", "url": "https://example.com/m.obj"}]
    monkeypatch.setattr(jobs, "_premium_job", lambda job_id: {"status": "succeeded", "files": files})
    polled = jobs.premium_poll_hunyuan_job_status("job_9")
    assert polled["Response"]["Status"] == "DONE"
    assert polled["Response"]["ResultFile3Ds"] == [{"Type": "OBJ", "Url": "https://example.com/m.obj"}]
    assert jobs._premium_files_by_url["https://example.com/m.obj"] == files

    monkeypatch.setattr(jobs, "_premium_job", lambda job_id: {"status": "failed", "error": "boom"})
    failed = jobs.premium_poll_hunyuan_job_status("job_9")
    assert failed["Response"]["Status"] == "FAIL"
    assert failed["Response"]["ErrorMessage"] == "boom. This attempt wasn't counted."


def test_premium_hunyuan_import_validates_url_and_uses_remembered_files(monkeypatch):
    assert jobs.premium_import_generated_asset_hunyuan("x", "ftp://bad") == {
        "error": "Invalid URL format. Must start with http:// or https://",
    }
    assert jobs.premium_import_generated_asset_hunyuan("x", "") == {
        "error": "Invalid URL format. Must start with http:// or https://",
    }

    imported = {}
    monkeypatch.setattr(jobs, "import_model_files", lambda files, name: imported.update(
        {"files": files, "name": name}) or {"succeed": True})
    for key in list(jobs._premium_files_by_url):
        del jobs._premium_files_by_url[key]
    jobs._premium_files_by_url["https://example.com/remembered"] = [{"url": "remembered"}]
    assert jobs.premium_import_generated_asset_hunyuan("mesh", "https://example.com/remembered") == {
        "succeed": True,
    }
    assert imported["files"] == [{"url": "remembered"}]

    assert jobs.premium_import_generated_asset_hunyuan("mesh", "https://example.com/m.obj?token=1") == {
        "succeed": True,
    }
    assert imported["files"][0]["format"] == "obj"
    assert imported["files"][0]["file_name"] == "model.obj"


def test_premium_tripo_status_and_handler_gate(monkeypatch):
    scene = _run_scene()
    monkeypatch.delenv("DSHBB_PREMIUM_LICENSE_KEY", raising=False)
    monkeypatch.setattr(client, "_premium_prefs", lambda: None)
    assert client.premium_active() is False
    assert generate_premium.premium_tripo_status() == {
        "enabled": False,
        "message": "Tripo is only available with the upstream Premium service.",
    }
    assert generate_premium.premium_tripo_handlers() == {}

    monkeypatch.setattr(generate_premium, "premium_active", lambda: True)
    scene.dshbb_use_tripo = False
    assert generate_premium.premium_tripo_handlers() == {}
    disabled = generate_premium.premium_tripo_status()
    assert disabled["enabled"] is False

    scene.dshbb_use_tripo = True
    monkeypatch.setattr(generate_premium, "premium_integration_status",
                        lambda integration, enabled: {"enabled": enabled, "integration": integration})
    assert generate_premium.premium_tripo_status() == {"enabled": True, "integration": "tripo"}
    handlers = generate_premium.premium_tripo_handlers()
    assert set(handlers) == {"create_tripo_job", "poll_tripo_job_status",
                             "import_generated_asset_tripo"}
    assert handlers["create_tripo_job"] is jobs.premium_create_tripo_job
    assert handlers["poll_tripo_job_status"] is jobs.premium_poll_fal_job
    assert handlers["import_generated_asset_tripo"] is jobs.premium_import_job
    scene.dshbb_use_tripo = False
