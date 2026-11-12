# -*- coding: utf-8 -*-
"""Select the inference backend for the worker configuration.

The proxy is lazy: creating it never imports onnxruntime on the default opencv
path, and an unavailable backend is reported through describe()/available()
(bounded status data) while load()/infer() raise the structured error.
"""
from __future__ import annotations

from typing import Any, Mapping

from protocol import VisionError

PROVIDER_DOCS = "https://github.com/sixtysevenlf/dsh-blender-plugin/blob/main/docs/vision-custom-backend.md"

OPENCV_BACKEND = "opencv"
ONNXRUNTIME_BACKEND = "onnxruntime"
DEVICES = ("cpu", "auto", "gpu")


class InferenceProxy:
    """Lazy adapter for the configured backend/device."""

    def __init__(self, config: Mapping[str, Any] | None = None) -> None:
        values = dict(config or {})
        self.backend = str(values.get("backend") or OPENCV_BACKEND)
        self.device = str(values.get("device") or "cpu")
        self._impl: Any = None
        self._error: VisionError | None = None
        self._resolved = False

    @property
    def name(self) -> str:
        return self.backend

    def _resolve(self) -> Any:
        if self._resolved:
            if self._error is not None:
                raise self._error
            return self._impl
        self._resolved = True
        try:
            if self.backend == OPENCV_BACKEND:
                from inference.opencv import OpenCVBackend

                self._impl = OpenCVBackend(self.device)
            elif self.backend == ONNXRUNTIME_BACKEND:
                from inference.onnxruntime import OnnxRuntimeBackend

                self._impl = OnnxRuntimeBackend(self.device)
            else:
                raise VisionError(
                    "dnn_config_invalid",
                    "config",
                    "unknown vision.dnn.backend: " + self.backend,
                    fix="Use opencv or onnxruntime.",
                    retryable=False,
                    details={"backend": self.backend, "docs": PROVIDER_DOCS},
                )
        except VisionError as exc:
            self._error = exc
        if self._impl is None:
            if self._error is not None:
                raise self._error
            raise VisionError(
                "dnn_backend_unavailable",
                "config",
                "inference backend did not initialize",
                retryable=False,
                details={"backend": self.backend},
            )
        return self._impl

    def available(self) -> bool:
        try:
            return self._resolve() is not None
        except VisionError:
            return False

    def ensure(self) -> Any:
        """Resolve the backend or re-raise the stored VisionError.

        available() is a boolean probe that hides *which* config error occurred;
        detect/selftest use ensure() so dnn_device_unavailable and
        dnn_config_invalid do not degrade into a generic input error.
        """
        return self._resolve()

    def providers(self) -> list[str]:
        try:
            impl = self._resolve()
        except VisionError:
            return []
        return list(impl.providers())

    def describe(self) -> dict[str, Any]:
        try:
            impl = self._resolve()
            return {
                "backend": self.backend,
                "device": self.device,
                "deviceUsed": getattr(impl, "device_used", self.device),
                "provider": getattr(impl, "provider", lambda: None)(),
                "providers": list(impl.providers()),
                "availableProviders": list(impl.providers()),
                "available": True,
                "reason": getattr(impl, "reason", None),
                "fix": None,
                "docs": PROVIDER_DOCS,
            }
        except VisionError as exc:
            payload: dict[str, Any] = {
                "backend": self.backend,
                "device": self.device,
                "deviceUsed": None,
                "provider": None,
                "providers": [],
                "availableProviders": [],
                "available": False,
                "reason": exc.code,
                "fix": exc.fix,
                "docs": PROVIDER_DOCS,
            }
            # Keep the backend-specific details (providers, devices, docs, exception type)
            # instead of discarding them; the boolean fields above stay authoritative.
            for key, value in dict(exc.details or {}).items():
                payload[key] = value
            payload["available"] = False
            payload["reason"] = exc.code
            if exc.fix is not None:
                payload["fix"] = exc.fix
            return payload

    def load(self, model_path: str, input_size: tuple[int, int] | None = None) -> Any:
        impl = self._resolve()
        return impl.load(model_path, input_size)

    def infer(self, handle: Any, blob: Any) -> list[Any]:
        impl = self._resolve()
        return impl.infer(handle, blob)


def build_inference(config: Mapping[str, Any] | None = None) -> InferenceProxy:
    return InferenceProxy(config)
