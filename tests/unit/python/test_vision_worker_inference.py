# -*- coding: utf-8 -*-
"""Unit tests for runtime/vision/worker/inference backends.

cv2 and onnxruntime are replaced with small fakes: the plugin never installs
either package in CI and the default path must not import onnxruntime at all.
"""
from __future__ import annotations

import sys
import types
from pathlib import Path

import numpy as np
import pytest

WORKER = Path(__file__).resolve().parents[3] / "runtime" / "vision" / "worker"
if str(WORKER) not in sys.path:
    sys.path.insert(0, str(WORKER))

from inference.select import InferenceProxy  # noqa: E402
from protocol import VisionError  # noqa: E402


class FakeNet:
    def __init__(self):
        self.calls = []
        self.forward_result = np.zeros((1, 4, 2), dtype=np.float32)

    def setPreferableBackend(self, value):
        self.calls.append(("backend", value))

    def setPreferableTarget(self, value):
        self.calls.append(("target", value))

    def setInput(self, blob):
        self.calls.append(("input", blob.shape))

    def forward(self):
        self.calls.append(("forward", None))
        return self.forward_result


def install_fake_cv2(monkeypatch):
    module = types.ModuleType("cv2")
    module.__version__ = "5.0.0-fake"
    module.dnn = types.SimpleNamespace(
        readNetFromONNX=lambda _path: FakeNet(),
        DNN_BACKEND_OPENCV=0,
        DNN_TARGET_CPU=1,
    )
    monkeypatch.setitem(sys.modules, "cv2", module)
    return module


class FakeSession:
    def __init__(self, path, providers=None):
        self.path = path
        self.providers = providers
        self.feed = None

    def get_inputs(self):
        return [types.SimpleNamespace(name="images")]

    def run(self, _outputs, feed):
        self.feed = feed
        return [np.zeros((1, 6), dtype=np.float32)]


def install_fake_ort(monkeypatch, providers):
    module = types.ModuleType("onnxruntime")
    module.__version__ = "9.9.9-fake"
    module.get_available_providers = lambda: list(providers)
    sessions = []

    def make_session(path, providers=None):
        session = FakeSession(path, providers=providers)
        sessions.append(session)
        return session

    module.InferenceSession = make_session
    monkeypatch.setitem(sys.modules, "onnxruntime", module)
    return module, sessions


def test_opencv_cpu_is_available_without_onnxruntime(monkeypatch):
    install_fake_cv2(monkeypatch)
    proxy = InferenceProxy({"backend": "opencv", "device": "cpu"})
    assert proxy.available() is True
    assert proxy.providers() == []
    described = proxy.describe()
    assert described["backend"] == "opencv"
    assert described["deviceUsed"] == "cpu"
    assert described["provider"] is None
    assert described["available"] is True
    handle = proxy.load("model.onnx", (640, 640))
    outputs = proxy.infer(handle, np.zeros((1, 3, 640, 640), dtype=np.float32))
    assert len(outputs) == 1
    assert outputs[0].shape == (1, 4, 2)
    assert ("input", (1, 3, 640, 640)) in handle["net"].calls


def test_opencv_rejects_non_cpu_device(monkeypatch):
    install_fake_cv2(monkeypatch)
    proxy = InferenceProxy({"backend": "opencv", "device": "auto"})
    assert proxy.available() is False
    described = proxy.describe()
    assert described["reason"] == "dnn_device_unavailable"
    assert "cpu" in described["fix"].lower()
    with pytest.raises(VisionError) as raised:
        proxy.load("model.onnx")
    assert raised.value.code == "dnn_device_unavailable"
    assert raised.value.stage == "config"


def test_opencv_reports_missing_cv2(monkeypatch):
    monkeypatch.setitem(sys.modules, "cv2", None)
    proxy = InferenceProxy({"backend": "opencv", "device": "cpu"})
    assert proxy.available() is False
    assert proxy.describe()["reason"] == "dnn_backend_unavailable"
    with pytest.raises(VisionError) as raised:
        proxy.load("model.onnx")
    assert raised.value.code == "dnn_backend_unavailable"


def test_onnxruntime_cpu_provider(monkeypatch):
    module, sessions = install_fake_ort(monkeypatch, ["CPUExecutionProvider"])
    proxy = InferenceProxy({"backend": "onnxruntime", "device": "cpu"})
    described = proxy.describe()
    assert described["available"] is True
    assert described["deviceUsed"] == "cpu"
    assert described["provider"] == "CPUExecutionProvider"
    assert described["availableProviders"] == ["CPUExecutionProvider"]
    handle = proxy.load("model.onnx", (320, 320))
    assert sessions and sessions[0].providers == ["CPUExecutionProvider"]
    outputs = proxy.infer(handle, np.zeros((1, 3, 320, 320), dtype=np.float32))
    assert outputs[0].shape == (1, 6)
    assert handle["session"].feed["images"].dtype == np.float32


def test_onnxruntime_explicit_gpu_without_provider_fails(monkeypatch):
    install_fake_ort(monkeypatch, ["CPUExecutionProvider"])
    proxy = InferenceProxy({"backend": "onnxruntime", "device": "gpu"})
    assert proxy.available() is False
    described = proxy.describe()
    assert described["reason"] == "dnn_device_unavailable"
    # The stored details survive describe() instead of being discarded.
    assert described["availableProviders"] == ["CPUExecutionProvider"]
    with pytest.raises(VisionError) as raised:
        proxy.load("model.onnx")
    assert raised.value.code == "dnn_device_unavailable"
    with pytest.raises(VisionError) as raised:
        proxy.ensure()
    assert raised.value.code == "dnn_device_unavailable"


def test_onnxruntime_session_failure_on_gpu_is_a_device_error(monkeypatch, tmp_path):
    module, _sessions = install_fake_ort(monkeypatch, ["CUDAExecutionProvider", "CPUExecutionProvider"])

    def explode(_path, providers=None):
        raise RuntimeError("CUDA kernel launch failed")

    module.InferenceSession = explode
    model = tmp_path / "model.onnx"
    model.write_bytes(b"onnx")
    proxy = InferenceProxy({"backend": "onnxruntime", "device": "gpu"})
    with pytest.raises(VisionError) as raised:
        proxy.load(str(model))
    error = raised.value
    assert error.code == "dnn_device_unavailable"
    assert error.stage == "config"
    assert error.details["provider"] == "CUDAExecutionProvider"
    assert error.details["availableProviders"] == ["CUDAExecutionProvider", "CPUExecutionProvider"]
    assert "CUDA kernel launch failed" in error.details["message"]
    assert error.details["docs"]


def test_onnxruntime_session_failure_in_auto_is_a_device_error(monkeypatch, tmp_path):
    module, _sessions = install_fake_ort(monkeypatch, ["DmlExecutionProvider", "CPUExecutionProvider"])

    def explode(_path, providers=None):
        raise RuntimeError("provider init failed")

    module.InferenceSession = explode
    model = tmp_path / "model.onnx"
    model.write_bytes(b"onnx")
    proxy = InferenceProxy({"backend": "onnxruntime", "device": "auto"})
    with pytest.raises(VisionError) as raised:
        proxy.load(str(model))
    assert raised.value.code == "dnn_device_unavailable"
    assert raised.value.details["device"] == "auto"


def test_onnxruntime_session_failure_on_cpu_is_a_model_error(monkeypatch, tmp_path):
    module, _sessions = install_fake_ort(monkeypatch, ["CPUExecutionProvider"])

    def explode(_path, providers=None):
        raise RuntimeError("INVALID_GRAPH: node has an unsupported attribute")

    module.InferenceSession = explode
    model = tmp_path / "model.onnx"
    model.write_bytes(b"onnx")
    proxy = InferenceProxy({"backend": "onnxruntime", "device": "cpu"})
    with pytest.raises(VisionError) as raised:
        proxy.load(str(model))
    assert raised.value.code == "vision_model_unsupported"
    assert raised.value.stage == "config"
    assert raised.value.details["modelPath"] == str(model)


def test_onnxruntime_missing_model_file_is_a_missing_model(monkeypatch, tmp_path):
    module, _sessions = install_fake_ort(monkeypatch, ["CPUExecutionProvider"])

    def explode(_path, providers=None):
        raise FileNotFoundError("no such file")

    module.InferenceSession = explode
    proxy = InferenceProxy({"backend": "onnxruntime", "device": "cpu"})
    with pytest.raises(VisionError) as raised:
        proxy.load(str(tmp_path / "absent.onnx"))
    assert raised.value.code == "vision_model_missing"
    assert raised.value.details["modelPath"] == str(tmp_path / "absent.onnx")


def test_onnxruntime_gpu_with_provider_uses_it(monkeypatch):
    _module, sessions = install_fake_ort(monkeypatch, ["CUDAExecutionProvider", "CPUExecutionProvider"])
    proxy = InferenceProxy({"backend": "onnxruntime", "device": "gpu"})
    described = proxy.describe()
    assert described["available"] is True
    assert described["deviceUsed"] == "gpu"
    assert described["provider"] == "CUDAExecutionProvider"
    proxy.load("model.onnx")
    assert sessions[0].providers == ["CUDAExecutionProvider"]


def test_onnxruntime_auto_falls_back_to_cpu_with_reason(monkeypatch):
    install_fake_ort(monkeypatch, ["CPUExecutionProvider"])
    proxy = InferenceProxy({"backend": "onnxruntime", "device": "auto"})
    described = proxy.describe()
    assert described["available"] is True
    assert described["deviceUsed"] == "cpu"
    assert described["reason"]


def test_onnxruntime_auto_prefers_gpu_provider(monkeypatch):
    install_fake_ort(monkeypatch, ["CPUExecutionProvider", "DmlExecutionProvider"])
    proxy = InferenceProxy({"backend": "onnxruntime", "device": "auto"})
    described = proxy.describe()
    assert described["deviceUsed"] == "gpu"
    assert described["provider"] == "DmlExecutionProvider"


def test_onnxruntime_missing_package(monkeypatch):
    monkeypatch.setitem(sys.modules, "onnxruntime", None)
    proxy = InferenceProxy({"backend": "onnxruntime", "device": "cpu"})
    assert proxy.available() is False
    described = proxy.describe()
    assert described["reason"] == "dnn_backend_unavailable"
    with pytest.raises(VisionError) as raised:
        proxy.load("model.onnx")
    assert raised.value.code == "dnn_backend_unavailable"


def test_unknown_backend_is_config_invalid():
    proxy = InferenceProxy({"backend": "tensorrt", "device": "cpu"})
    assert proxy.available() is False
    assert proxy.describe()["reason"] == "dnn_config_invalid"
