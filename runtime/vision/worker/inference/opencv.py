# -*- coding: utf-8 -*-
"""OpenCV DNN inference backend (CPU only).

The default vision path is opencv + cpu: no onnxruntime import, no GPU
provider probing. OpenCV DNN builds shipped on PyPI are CPU-only, so a non-cpu
request is reported as dnn_device_unavailable instead of silently falling back.
"""
from __future__ import annotations

from typing import Any

import numpy as np

from protocol import VisionError


def _require_cv2() -> Any:
    try:
        import cv2  # pyright: ignore[reportMissingImports]
    except ImportError as exc:
        raise VisionError(
            "dnn_backend_unavailable",
            "config",
            "OpenCV (cv2) is not installed in the worker interpreter",
            fix="Reinstall the vision environment so opencv-python-headless is available.",
            retryable=False,
            details={"backend": "opencv", "exceptionType": "ImportError"},
        ) from exc
    return cv2


class OpenCVBackend:
    """cv2.dnn backend. input_size is accepted for interface parity only."""

    name = "opencv"

    def __init__(self, device: str) -> None:
        self.device = device or "cpu"
        if self.device != "cpu":
            raise VisionError(
                "dnn_device_unavailable",
                "config",
                "the OpenCV DNN backend is CPU-only; device=" + self.device + " is not available",
                fix="Set vision.dnn.device=cpu, or install a custom venv and use vision.dnn.backend=onnxruntime for GPU.",
                retryable=False,
                details={"backend": "opencv", "device": self.device, "availableDevices": ["cpu"]},
            )
        self._cv2 = _require_cv2()

    def available(self) -> bool:
        return self._cv2 is not None

    def providers(self) -> list[str]:
        return []

    def provider(self) -> str | None:
        return None

    def load(self, model_path: str, input_size: tuple[int, int] | None = None) -> Any:
        net = self._cv2.dnn.readNetFromONNX(model_path)
        net.setPreferableBackend(self._cv2.dnn.DNN_BACKEND_OPENCV)
        net.setPreferableTarget(self._cv2.dnn.DNN_TARGET_CPU)
        return {"net": net, "inputSize": input_size}

    def infer(self, handle: Any, blob: Any) -> list[Any]:
        net = handle["net"]
        data = np.ascontiguousarray(blob, dtype=np.float32)
        net.setInput(data)
        return [net.forward()]
