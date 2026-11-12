# -*- coding: utf-8 -*-
"""Unit tests for runtime/vision/worker/main.py framing and dispatch."""
from __future__ import annotations

import io
import json
import sys
import types
from pathlib import Path

WORKER = Path(__file__).resolve().parents[3] / "runtime" / "vision" / "worker"
if str(WORKER) not in sys.path:
    sys.path.insert(0, str(WORKER))

import main as worker_main  # noqa: E402
import protocol as wire  # noqa: E402


def install_fake_cv2(monkeypatch):
    module = types.ModuleType("cv2")
    module.__version__ = "5.0.0-fake"
    module.dnn = types.SimpleNamespace()
    monkeypatch.setitem(sys.modules, "cv2", module)
    return module


def hello(revision=5, **args):
    payload = {"protocolVersion": wire.PROTOCOL_VERSION, "home": "C:/home", "modelsDir": "C:/home/vision/models"}
    payload.update(args)
    payload["config"] = {"revision": revision, "model": "yolov5n", "backend": "opencv", "device": "cpu"}
    return {"id": "h", "op": "hello", "args": payload}


def run(frames, env=None):
    stdin = io.BytesIO(b"".join(wire.encode_frame(frame) for frame in frames))
    stdout = io.BytesIO()
    code = worker_main.run_worker(stdin, stdout, {} if env is None else env)
    output = stdout.getvalue().decode("utf-8")
    responses = [json.loads(line) for line in output.splitlines() if line.strip()]
    return code, responses


def test_hello_status_shutdown_round_trip(monkeypatch, tmp_path):
    install_fake_cv2(monkeypatch)
    (tmp_path / "models").mkdir()
    (tmp_path / "models" / "a.onnx").write_bytes(b"x")
    code, responses = run(
        [
            hello(modelsDir=str(tmp_path / "models")),
            {"id": 1, "op": "status", "args": {}},
            {"id": 2, "op": "shutdown", "args": {}},
        ]
    )
    assert code == 0
    assert len(responses) == 3
    hello_result = responses[0]
    assert hello_result["id"] == "h"
    assert hello_result["ok"] is True
    assert hello_result["result"]["protocolVersion"] == 1
    assert hello_result["result"]["cv2"] == "5.0.0-fake"
    assert hello_result["result"]["configRevision"] == 5
    status = responses[1]["result"]
    assert status["home"] == "C:/home"
    assert status["onnxCount"] == 1
    assert status["config"]["model"] == "yolov5n"
    assert responses[2]["result"] == {"shuttingDown": True}


def test_hello_requires_matching_protocol_version(monkeypatch):
    install_fake_cv2(monkeypatch)
    frame = hello()
    frame["args"]["protocolVersion"] = 99
    code, responses = run([frame])
    assert code == 2
    assert responses[0]["ok"] is False
    assert responses[0]["error"]["code"] == "vision_protocol_error"
    assert responses[0]["error"]["details"]["expected"] == 1


def test_first_frame_must_be_hello(monkeypatch):
    install_fake_cv2(monkeypatch)
    code, responses = run([{"id": 1, "op": "status", "args": {}}])
    assert code == 2
    assert responses[0]["error"]["code"] == "vision_protocol_error"


def test_malformed_first_frame_exits_with_protocol_error(monkeypatch):
    install_fake_cv2(monkeypatch)
    stdout = io.BytesIO()
    code = worker_main.run_worker(io.BytesIO(b"not json\n"), stdout, {})
    assert code == 2
    payload = json.loads(stdout.getvalue().decode("utf-8").strip())
    assert payload["error"]["code"] == "vision_protocol_error"


def test_empty_stdin_exits_cleanly():
    code, responses = run([])
    assert code == 0
    assert responses == []


def test_unknown_op_is_reported_and_loop_continues(monkeypatch):
    install_fake_cv2(monkeypatch)
    code, responses = run(
        [
            hello(),
            {"id": 1, "op": "nope", "args": {}},
            {"id": 2, "op": "shutdown", "args": {}},
        ]
    )
    assert code == 0
    assert responses[1]["error"]["code"] == "vision_protocol_error"
    assert "configRevision" not in responses[1]
    assert responses[2]["ok"] is True


def test_second_hello_is_a_protocol_error(monkeypatch):
    install_fake_cv2(monkeypatch)
    code, responses = run([hello(), hello()])
    assert code == 0
    assert responses[1]["ok"] is False
    assert responses[1]["error"]["code"] == "vision_protocol_error"


def test_vision_op_dispatches_to_flat_module(monkeypatch):
    install_fake_cv2(monkeypatch)

    def run_detect(args, ctx):
        return {"model": ctx["config"]["model"], "arg": args.get("image")}

    fake = types.ModuleType("detect")
    fake.run = run_detect
    monkeypatch.setitem(sys.modules, "detect", fake)
    code, responses = run(
        [
            hello(revision=9),
            {"id": 7, "op": "detect", "args": {"image": "a.png"}, "configRevision": 9},
        ]
    )
    assert code == 0
    assert responses[1]["ok"] is True
    assert responses[1]["result"] == {"model": "yolov5n", "arg": "a.png"}
    assert responses[1]["configRevision"] == 9


def test_vision_op_merges_the_per_request_config_model(monkeypatch):
    install_fake_cv2(monkeypatch)

    def run_detect(_args, ctx):
        return {"model": ctx["config"]["model"], "revision": ctx["config"].get("revision"), "backend": ctx["config"]["backend"]}

    fake = types.ModuleType("detect")
    fake.run = run_detect
    monkeypatch.setitem(sys.modules, "detect", fake)
    code, responses = run(
        [
            hello(revision=9),
            {
                "id": 5,
                "op": "detect",
                "args": {},
                "configRevision": 4,
                "config": {"model": "custom", "revision": 11},
            },
        ]
    )
    assert code == 0
    assert responses[1]["result"] == {"model": "custom", "revision": 11, "backend": "opencv"}
    # The frame's own configRevision is still echoed, not the merged one.
    assert responses[1]["configRevision"] == 4


def test_hello_context_failure_still_writes_a_structured_frame(monkeypatch):
    install_fake_cv2(monkeypatch)

    def boom(_args, _env):
        raise RuntimeError("kaboom")

    monkeypatch.setattr(worker_main, "build_context", boom)
    code, responses = run([hello()])
    assert code == 2
    assert responses[0]["ok"] is False
    assert responses[0]["error"]["code"] == "vision_worker_failed"
    assert responses[0]["error"]["details"]["exceptionType"] == "RuntimeError"


def test_vision_op_exceptions_become_worker_failed(monkeypatch):
    install_fake_cv2(monkeypatch)

    def boom(_args, _ctx):
        raise RuntimeError("kaboom")

    fake = types.ModuleType("detect")
    fake.run = boom
    monkeypatch.setitem(sys.modules, "detect", fake)
    _code, responses = run([hello(), {"id": 1, "op": "detect", "args": {}}])
    error = responses[1]["error"]
    assert error["code"] == "vision_worker_failed"
    assert error["stage"] == "vision"
    assert error["retryable"] is False
    assert error["details"]["exceptionType"] == "RuntimeError"


def test_vision_error_passes_through(monkeypatch):
    install_fake_cv2(monkeypatch)

    def fail(_args, _ctx):
        raise wire.VisionError("vision_model_missing", "config", "no model", retryable=False)

    fake = types.ModuleType("detect")
    fake.run = fail
    monkeypatch.setitem(sys.modules, "detect", fake)
    _code, responses = run([hello(), {"id": 1, "op": "detect", "args": {}}])
    assert responses[1]["error"]["code"] == "vision_model_missing"


def test_vision_op_must_return_a_dict(monkeypatch):
    install_fake_cv2(monkeypatch)

    fake = types.ModuleType("detect")
    fake.run = lambda _args, _ctx: ["not", "a", "dict"]
    monkeypatch.setitem(sys.modules, "detect", fake)
    _code, responses = run([hello(), {"id": 1, "op": "detect", "args": {}}])
    assert responses[1]["error"]["code"] == "vision_worker_failed"
    assert "list" in responses[1]["error"]["message"]


def test_env_fallback_for_home_and_models(monkeypatch, tmp_path):
    install_fake_cv2(monkeypatch)
    frame = {"id": 1, "op": "hello", "args": {"protocolVersion": 1}}
    code, responses = run(
        [frame, {"id": 2, "op": "status", "args": {}}],
        env={
            "DSH_BLENDER_VISION_HOME": str(tmp_path),
            "DSH_BLENDER_VISION_MODELS": str(tmp_path / "models"),
        },
    )
    assert code == 0
    status = responses[1]["result"]
    assert status["home"] == str(tmp_path)
    assert status["modelsDir"] == str(tmp_path / "models")
    assert status["config"]["backend"] == "opencv"
    assert status["config"]["device"] == "cpu"


def test_status_does_not_import_onnxruntime_on_default_path(monkeypatch):
    install_fake_cv2(monkeypatch)
    monkeypatch.setitem(sys.modules, "onnxruntime", None)
    _code, responses = run([hello(), {"id": 2, "op": "status", "args": {}}])
    versions = responses[1]["result"]["versions"]
    assert versions["onnxruntime"] is None
    assert versions["onnxruntimeInstalled"] is False
    assert responses[1]["result"]["inference"]["available"] is True


def test_onnxruntime_backend_without_package_is_reported(monkeypatch):
    install_fake_cv2(monkeypatch)
    monkeypatch.setitem(sys.modules, "onnxruntime", None)
    frame = hello()
    frame["args"]["config"] = {"revision": 1, "model": "m", "backend": "onnxruntime", "device": "cpu"}
    _code, responses = run([frame])
    result = responses[0]["result"]
    assert result["available"] is False
    assert result["reason"] == "dnn_backend_unavailable"


def test_malformed_second_frame_exits_with_protocol_error(monkeypatch):
    install_fake_cv2(monkeypatch)
    stdin = io.BytesIO(wire.encode_frame(hello()) + b"oops\n")
    stdout = io.BytesIO()
    code = worker_main.run_worker(stdin, stdout, {})
    assert code == 2
    lines = [json.loads(line) for line in stdout.getvalue().decode("utf-8").splitlines() if line.strip()]
    assert lines[0]["ok"] is True
    assert lines[-1]["error"]["code"] == "vision_protocol_error"
