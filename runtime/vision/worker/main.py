# -*- coding: utf-8 -*-
"""Entry point for the private vision worker.

The host starts this file with the vision venv interpreter (cwd = DSH home,
stdin/stdout are pipes). It speaks the newline-delimited JSON protocol from
protocol.py, serves hello/status/shutdown itself and delegates the vision ops
(analyze/compare/detect/track/selftest) to flat modules of the same name:

    def run(args: dict, ctx: dict) -> dict

ctx keys: home, models_dir, config, inference, versions, log. The worker never
listens on a port, never imports bpy and never makes network requests.
"""
from __future__ import annotations

import importlib
import importlib.util
import os
import platform
import sys
from pathlib import Path
from typing import Any, BinaryIO, Mapping

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

import protocol as wire  # noqa: E402  (path setup above)
from inference.select import build_inference  # noqa: E402


def log(message: str) -> None:
    """Bounded stderr logging; the host appends stderr to vision-worker.log."""
    try:
        print("[vision-worker] " + str(message), file=sys.stderr, flush=True)
    except Exception:
        pass


def _module_version(name: str) -> str | None:
    try:
        module = importlib.import_module(name)
    except Exception:
        return None
    version = getattr(module, "__version__", None)
    return str(version) if version else None


def _module_present(name: str) -> bool:
    try:
        return importlib.util.find_spec(name) is not None
    except (ImportError, ValueError):
        return False


def _as_int(value: object, default: int = 0) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        return default
    return int(value)


def _as_str(value: object, default: str = "") -> str:
    return value if isinstance(value, str) else default


def collect_versions(config: Mapping[str, Any]) -> dict[str, Any]:
    """Report versions without importing onnxruntime on the default opencv path."""
    versions: dict[str, Any] = {
        "protocol": wire.PROTOCOL_VERSION,
        "python": platform.python_version(),
        "cv2": _module_version("cv2"),
        "numpy": _module_version("numpy"),
        "onnxruntime": None,
        "onnxruntimeInstalled": _module_present("onnxruntime"),
    }
    if config.get("backend") == "onnxruntime":
        versions["onnxruntime"] = _module_version("onnxruntime")
    return versions


def build_context(hello_args: Mapping[str, Any], env: Mapping[str, str]) -> dict[str, Any]:
    home = _as_str(hello_args.get("home")) or env.get("DSH_BLENDER_VISION_HOME") or os.getcwd()
    models_dir = _as_str(hello_args.get("modelsDir")) or env.get("DSH_BLENDER_VISION_MODELS") or os.path.join(
        home, "vision", "models"
    )
    raw_config = hello_args.get("config")
    raw_values = raw_config if isinstance(raw_config, dict) else {}
    config = {
        "revision": _as_int(raw_values.get("revision"), 0),
        "model": _as_str(raw_values.get("model")),
        "backend": _as_str(raw_values.get("backend"), "opencv") or "opencv",
        "device": _as_str(raw_values.get("device"), "cpu") or "cpu",
        "python": _as_str(raw_values.get("python")),
        "venv": _as_str(raw_values.get("venv")),
    }
    return {
        "home": home,
        "models_dir": models_dir,
        "config": config,
        "inference": build_inference(config),
        "versions": collect_versions(config),
        "log": log,
    }


def _hello_args(frame: Mapping[str, Any]) -> dict[str, Any]:
    if frame.get("op") != "hello":
        raise wire.VisionError(
            "vision_protocol_error",
            "vision",
            "the first frame must be hello",
            retryable=False,
        )
    args = frame.get("args") or {}
    if not isinstance(args, dict):
        raise wire.VisionError("vision_protocol_error", "vision", "hello args must be an object", retryable=False)
    version = args.get("protocolVersion")
    if version != wire.PROTOCOL_VERSION:
        raise wire.VisionError(
            "vision_protocol_error",
            "vision",
            "worker protocol version mismatch: expected " + str(wire.PROTOCOL_VERSION) + ", got " + repr(version),
            fix="Reinstall the package so runtime/vision/worker matches the host.",
            retryable=False,
            details={"expected": wire.PROTOCOL_VERSION, "actual": version},
        )
    return args


def _hello_result(ctx: Mapping[str, Any]) -> dict[str, Any]:
    inferred = ctx["inference"].describe()
    versions = ctx["versions"]
    return {
        "protocolVersion": wire.PROTOCOL_VERSION,
        "python": versions.get("python"),
        "cv2": versions.get("cv2"),
        "numpy": versions.get("numpy"),
        "backend": inferred.get("backend"),
        "device": inferred.get("device"),
        "available": inferred.get("available"),
        "reason": inferred.get("reason"),
        "provider": inferred.get("provider"),
        "providers": inferred.get("providers"),
        "configRevision": ctx["config"].get("revision"),
    }


def _status_result(ctx: Mapping[str, Any]) -> dict[str, Any]:
    inferred = ctx["inference"].describe()
    models_dir = Path(str(ctx["models_dir"]))
    onnx_count = 0
    if models_dir.is_dir():
        onnx_count = sum(1 for path in models_dir.rglob("*.onnx") if path.is_file())
    return {
        "protocolVersion": wire.PROTOCOL_VERSION,
        "home": ctx["home"],
        "modelsDir": str(models_dir),
        "modelsYml": (models_dir / "models.yml").is_file(),
        "onnxCount": onnx_count,
        "config": dict(ctx["config"]),
        "versions": dict(ctx["versions"]),
        "inference": inferred,
    }


def _run_vision_op(op: str, args: Mapping[str, Any], ctx: Mapping[str, Any]) -> dict[str, Any]:
    try:
        module = importlib.import_module(op)
    except ImportError as exc:
        raise wire.VisionError(
            "vision_worker_failed",
            "vision",
            "worker op module " + op + " is not installed: " + str(exc),
            fix="The package is incomplete; reinstall the plugin.",
            retryable=False,
            details={"op": op, "exceptionType": "ImportError"},
        ) from exc
    runner = getattr(module, "run", None)
    if not callable(runner):
        raise wire.VisionError(
            "vision_worker_failed",
            "vision",
            "worker op module " + op + " has no callable run(args, ctx)",
            retryable=False,
            details={"op": op},
        )
    result = runner(dict(args), ctx)
    if not isinstance(result, dict):
        raise wire.VisionError(
            "vision_worker_failed",
            "vision",
            "worker op " + op + " returned " + type(result).__name__ + ", expected dict",
            retryable=False,
            details={"op": op},
        )
    return result


def _write_response(
    stream: BinaryIO,
    frame_id: Any,
    config_revision: Any,
    error: wire.VisionError | None = None,
    result: Mapping[str, Any] | None = None,
) -> None:
    payload: dict[str, Any] = {"id": frame_id}
    if error is not None:
        payload["ok"] = False
        payload["error"] = error.to_payload()
    else:
        payload["ok"] = True
        payload["result"] = dict(result or {})
    if isinstance(config_revision, int) and not isinstance(config_revision, bool):
        payload["configRevision"] = config_revision
    try:
        wire.write_frame(stream, payload)
    except wire.VisionError as exc:
        log("cannot write response: " + exc.message)
        fallback: dict[str, Any] = {"id": frame_id, "ok": False, "error": exc.to_payload()}
        try:
            wire.write_frame(stream, fallback)
        except wire.VisionError:
            log("cannot write fallback response either")


def _request_context(ctx: Mapping[str, Any], frame: Mapping[str, Any]) -> Mapping[str, Any]:
    """Merge an optional per-request config {revision, model} into a copy of ctx.

    The manager may send `config` on worker-backed ops to pick a model without a
    worker restart. Only the documented fields are merged; the response keeps
    echoing the frame's own configRevision.
    """
    request_config = frame.get("config")
    if not isinstance(request_config, dict):
        return ctx
    merged = dict(ctx.get("config") or {})
    model = request_config.get("model")
    if isinstance(model, str) and model.strip():
        merged["model"] = model.strip()
    revision = request_config.get("revision")
    if isinstance(revision, int) and not isinstance(revision, bool):
        merged["revision"] = revision
    request_ctx = dict(ctx)
    request_ctx["config"] = merged
    return request_ctx


def run_worker(stdin: BinaryIO, stdout: BinaryIO, env: Mapping[str, str] | None = None) -> int:
    environment = os.environ if env is None else env
    frames = iter(wire.FrameReader(stdin))
    try:
        first = next(frames)
    except StopIteration:
        return 0
    except wire.VisionError as exc:
        _write_response(stdout, None, None, exc)
        return 2
    first_id = first.get("id")
    try:
        hello_args = _hello_args(first)
    except wire.VisionError as exc:
        _write_response(stdout, first_id, None, exc)
        return 2
    ctx = None
    try:
        ctx = build_context(hello_args, environment)
        hello_result = _hello_result(ctx)
    except BaseException as exc:  # noqa: BLE001 - hello must still answer with a frame
        error = wire.as_vision_error(exc)
        log("hello context failed: " + error.code + " " + error.message)
        _write_response(stdout, first_id, None, error)
        return 2
    _write_response(stdout, first_id, None, None, hello_result)

    try:
        for frame in frames:
            frame_id = frame.get("id")
            op = str(frame.get("op"))
            config_revision = frame.get("configRevision")
            request_ctx = _request_context(ctx, frame)
            if op == "shutdown":
                _write_response(stdout, frame_id, config_revision, None, {"shuttingDown": True})
                return 0
            if op == "hello":
                _write_response(
                    stdout,
                    frame_id,
                    config_revision,
                    wire.VisionError("vision_protocol_error", "vision", "hello was already completed", retryable=False),
                )
                continue
            try:
                if op == "status":
                    result = _status_result(request_ctx)
                elif op in wire.VISION_OPS:
                    result = _run_vision_op(op, frame.get("args") or {}, request_ctx)
                else:
                    raise wire.VisionError(
                        "vision_protocol_error",
                        "vision",
                        "unknown worker op: " + op,
                        retryable=False,
                        details={"op": op},
                    )
            except BaseException as exc:  # noqa: BLE001 - every op failure becomes a payload
                error = wire.as_vision_error(exc)
                log("op " + op + " failed: " + error.code + " " + error.message)
                _write_response(stdout, frame_id, config_revision, error)
                continue
            _write_response(stdout, frame_id, config_revision, None, result)
    except wire.VisionError as exc:
        _write_response(stdout, None, None, exc)
        return 2
    return 0


def main(argv: list[str] | None = None) -> int:
    del argv
    try:
        return run_worker(sys.stdin.buffer, sys.stdout.buffer)
    except BrokenPipeError:
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
