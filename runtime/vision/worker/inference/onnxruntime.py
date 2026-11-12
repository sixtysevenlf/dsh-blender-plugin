# -*- coding: utf-8 -*-
"""ONNX Runtime inference backend (user-provided venv only).

The plugin never installs onnxruntime and never imports it on the default
opencv path. An explicit gpu request without a GPU provider fails instead of
falling back; auto is the only mode allowed to fall back (to CPU) and it
records the reason for the caller.
"""
from __future__ import annotations

import os
from typing import Any

import numpy as np

from protocol import VisionError

#: GPU-capable execution providers, in preference order.
GPU_PROVIDERS = (
    "CUDAExecutionProvider",
    "DmlExecutionProvider",
    "ROCMExecutionProvider",
    "MIGraphXExecutionProvider",
    "OpenVINOExecutionProvider",
)
CPU_PROVIDER = "CPUExecutionProvider"

PROVIDER_DOCS = "https://github.com/sixtysevenlf/dsh-blender-plugin/blob/main/docs/vision-custom-backend.md"


def _require_ort() -> Any:
    try:
        import onnxruntime as ort  # pyright: ignore[reportMissingImports]
    except ImportError as exc:
        raise VisionError(
            "dnn_backend_unavailable",
            "config",
            "backend=onnxruntime but the worker interpreter cannot import onnxruntime",
            fix="Point vision.venv at a venv that has onnxruntime installed, or switch back to backend=opencv.",
            retryable=False,
            details={"backend": "onnxruntime", "exceptionType": "ImportError", "docs": PROVIDER_DOCS},
        ) from exc
    return ort


class OnnxRuntimeBackend:
    name = "onnxruntime"

    def __init__(self, device: str) -> None:
        self.device = device or "cpu"
        if self.device not in ("cpu", "auto", "gpu"):
            raise VisionError(
                "dnn_config_invalid",
                "config",
                "unknown vision.dnn.device: " + self.device,
                fix="Use cpu, auto or gpu.",
                retryable=False,
                details={"device": self.device},
            )
        self._ort = _require_ort()
        self.available_providers = tuple(str(item) for item in self._ort.get_available_providers())
        self.device_used = "cpu"
        self.reason: str | None = None
        self._primary: str | None = None
        if self.device == "cpu":
            self._primary = CPU_PROVIDER
        elif self.device == "gpu":
            primary = _first_gpu_provider(self.available_providers)
            if primary is None:
                raise VisionError(
                    "dnn_device_unavailable",
                    "config",
                    "device=gpu but onnxruntime exposes no GPU provider",
                    fix="Install a matching onnxruntime GPU build and driver, or set vision.dnn.device=cpu.",
                    retryable=False,
                    details={
                        "backend": "onnxruntime",
                        "device": "gpu",
                        "availableProviders": list(self.available_providers),
                        "docs": PROVIDER_DOCS,
                    },
                )
            self._primary = primary
            self.device_used = "gpu"
        else:
            primary = _first_gpu_provider(self.available_providers)
            if primary is None:
                self._primary = CPU_PROVIDER
                self.device_used = "cpu"
                self.reason = "no GPU provider reported by onnxruntime; device=auto fell back to CPU"
            else:
                self._primary = primary
                self.device_used = "gpu"

    def available(self) -> bool:
        return self._ort is not None

    def providers(self) -> list[str]:
        return list(self.available_providers)

    def provider(self) -> str | None:
        return self._primary

    def load(self, model_path: str, input_size: tuple[int, int] | None = None) -> Any:
        providers = [self._primary] if self._primary else [CPU_PROVIDER]
        try:
            session = self._ort.InferenceSession(model_path, providers=providers)
        except BaseException as exc:  # ORT raises its own exception hierarchy
            raise _session_error(exc, model_path, providers, self.device, self.available_providers) from exc
        inputs = session.get_inputs()
        input_name = inputs[0].name if inputs else None
        return {"session": session, "inputName": input_name, "inputSize": input_size}

    def infer(self, handle: Any, blob: Any) -> list[Any]:
        input_name = handle["inputName"]
        if input_name is None:
            raise VisionError(
                "vision_worker_failed",
                "vision",
                "onnxruntime session exposes no input tensor",
                retryable=False,
            )
        data = np.ascontiguousarray(blob, dtype=np.float32)
        return list(handle["session"].run(None, {input_name: data}))


def _session_error(
    exc: BaseException,
    model_path: str,
    providers: list[str],
    device: str,
    available: tuple[str, ...],
) -> VisionError:
    """Attribute an InferenceSession failure to the model file or to the device."""
    message = str(exc).splitlines()[0][:300] if str(exc) else exc.__class__.__name__
    missing = not isinstance(model_path, str) or not model_path or not os.path.isfile(model_path)
    if missing:
        return VisionError(
            "vision_model_missing",
            "config",
            "onnxruntime cannot load the model file: " + str(model_path),
            fix="Check models.yml and place the .onnx under vision/models/.",
            retryable=False,
            details={"modelPath": str(model_path), "exceptionType": exc.__class__.__name__},
        )
    if device in ("gpu", "auto"):
        return VisionError(
            "dnn_device_unavailable",
            "config",
            "onnxruntime InferenceSession failed with device=" + device + ": " + message,
            fix="Install a matching onnxruntime GPU build/driver, or set vision.dnn.device=cpu.",
            retryable=False,
            details={
                "backend": "onnxruntime",
                "device": device,
                "provider": providers[0] if providers else None,
                "availableProviders": list(available),
                "message": message,
                "exceptionType": exc.__class__.__name__,
                "docs": PROVIDER_DOCS,
            },
        )
    return VisionError(
        "vision_model_unsupported",
        "config",
        "onnxruntime cannot load this model: " + message,
        fix="Re-export the ONNX (opset/IR compatible with your onnxruntime) or pick another model.",
        retryable=False,
        details={
            "backend": "onnxruntime",
            "device": device,
            "modelPath": model_path,
            "message": message,
            "exceptionType": exc.__class__.__name__,
            "docs": PROVIDER_DOCS,
        },
    )


def _first_gpu_provider(available: tuple[str, ...]) -> str | None:
    for provider in GPU_PROVIDERS:
        if provider in available:
            return provider
    return None
