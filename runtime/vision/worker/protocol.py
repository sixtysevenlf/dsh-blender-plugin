# -*- coding: utf-8 -*-
"""Wire protocol for the private vision worker.

The host talks to this worker over stdin/stdout with newline-delimited JSON
frames. This module owns the frame model, the frame size limit and the single
error shape (VisionError). It never imports cv2; a worker can use the frame
helpers before any vision dependency is loaded.

A request frame is {"id": <int|str>, "op": <str>, "args": {...},
"configRevision": <int>}; a response frame is either
{"id": ..., "ok": true, "result": {...}, "configRevision": <int>} or
{"id": ..., "ok": false, "error": {...}, "configRevision": <int>}.
"""
from __future__ import annotations

import json
from typing import Any, BinaryIO, Iterator

PROTOCOL_VERSION = 1

# One frame (including the trailing newline) must fit in 8 MiB.
MAX_FRAME_BYTES = 8 * 1024 * 1024

# Stages used by every vision failure payload.
STAGES = frozenset({"config", "vision", "input"})

# Ops served by main.py itself; the remaining ops are delegated to a flat
# module of the same name in the worker directory.
BUILTIN_OPS = frozenset({"hello", "status", "shutdown"})
VISION_OPS = frozenset({"analyze", "compare", "detect", "track", "selftest"})
ALL_OPS = BUILTIN_OPS | VISION_OPS


class VisionError(Exception):
    """Structured worker failure.

    stage is one of config / vision / input; code is a stable machine-readable
    identifier shared with the host dispatcher.
    """

    def __init__(
        self,
        code: str,
        stage: str,
        message: str,
        fix: str | None = None,
        retryable: bool = False,
        details: dict[str, Any] | None = None,
    ) -> None:
        if not isinstance(code, str) or not code:
            raise ValueError("VisionError requires a non-empty code")
        if stage not in STAGES:
            raise ValueError("VisionError stage must be one of " + ", ".join(sorted(STAGES)))
        super().__init__(message)
        self.code = code
        self.stage = stage
        self.message = message
        self.fix = fix
        self.retryable = bool(retryable)
        self.details = dict(details) if details else {}

    def to_payload(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "code": self.code,
            "stage": self.stage,
            "message": self.message,
            "retryable": self.retryable,
        }
        if self.fix is not None:
            payload["fix"] = self.fix
        if self.details:
            payload["details"] = self.details
        return payload


def as_vision_error(exc: BaseException) -> VisionError:
    """Return exc when it already is a VisionError, wrap everything else."""
    if isinstance(exc, VisionError):
        return exc
    message = str(exc) or exc.__class__.__name__
    return VisionError(
        "vision_worker_failed",
        "vision",
        message,
        retryable=False,
        details={"exceptionType": exc.__class__.__name__},
    )


def encode_frame(payload: dict[str, Any]) -> bytes:
    """Serialize one response/request frame, enforcing the 8 MiB frame limit."""
    try:
        body = json.dumps(payload, ensure_ascii=False, allow_nan=False, separators=(",", ":")).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise VisionError(
            "vision_protocol_error",
            "vision",
            "response is not JSON-serializable: " + str(exc),
            retryable=False,
        ) from exc
    frame = body + b"\n"
    if len(frame) > MAX_FRAME_BYTES:
        raise VisionError(
            "vision_protocol_error",
            "vision",
            "response frame exceeds the 8 MiB limit (" + str(len(frame)) + " bytes)",
            retryable=False,
            details={"bytes": len(frame), "maxFrameBytes": MAX_FRAME_BYTES},
        )
    return frame


def decode_frame(line: bytes) -> dict[str, Any]:
    """Decode one newline-free frame; every malformed input is a protocol error."""
    if not line or not line.strip():
        raise VisionError("vision_protocol_error", "vision", "empty request frame", retryable=False)
    try:
        text = line.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise VisionError("vision_protocol_error", "vision", "request frame is not UTF-8: " + str(exc), retryable=False) from exc
    try:
        frame = json.loads(text)
    except json.JSONDecodeError as exc:
        raise VisionError(
            "vision_protocol_error",
            "vision",
            "request frame is not valid JSON: " + str(exc),
            retryable=False,
        ) from exc
    if not isinstance(frame, dict):
        raise VisionError("vision_protocol_error", "vision", "request frame must be a JSON object", retryable=False)
    frame_id = frame.get("id")
    if isinstance(frame_id, bool) or not isinstance(frame_id, (int, str)):
        raise VisionError("vision_protocol_error", "vision", "request frame needs an int/string id", retryable=False)
    op = frame.get("op")
    if not isinstance(op, str) or not op:
        raise VisionError("vision_protocol_error", "vision", "request frame needs an op", retryable=False)
    args = frame.get("args", {})
    if args is None:
        args = {}
    if not isinstance(args, dict):
        raise VisionError("vision_protocol_error", "vision", "request frame args must be an object", retryable=False)
    frame["args"] = args
    return frame


def write_frame(stream: BinaryIO, payload: dict[str, Any]) -> None:
    """Write one frame and flush immediately so the host never blocks on a buffer."""
    stream.write(encode_frame(payload))
    stream.flush()


class FrameReader:
    """Iterate request frames from a binary stream.

    readline(limit) is used instead of an unbounded read so an oversized frame
    is reported as a protocol error instead of consuming host memory.
    """

    def __init__(self, stream: BinaryIO, max_frame_bytes: int = MAX_FRAME_BYTES) -> None:
        self._stream = stream
        self._max_frame_bytes = max_frame_bytes

    def __iter__(self) -> Iterator[dict[str, Any]]:
        while True:
            raw = self._stream.readline(self._max_frame_bytes + 2)
            if raw == b"":
                return
            if raw.endswith(b"\n"):
                line = raw[:-1]
            else:
                line = raw
            # Check the payload length uniformly: encode_frame allows a frame of at most
            # max_frame_bytes including its trailing newline, so the payload must be
            # strictly shorter. A MAX+1-byte line ending in \n is therefore rejected.
            if len(line) + 1 > self._max_frame_bytes:
                raise VisionError(
                    "vision_protocol_error",
                    "vision",
                    "request frame exceeds the 8 MiB limit",
                    retryable=False,
                    details={"maxFrameBytes": self._max_frame_bytes, "bytes": len(line) + 1},
                )
            if line.endswith(b"\r"):
                line = line[:-1]
            if not line.strip():
                continue
            yield decode_frame(line)
