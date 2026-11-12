# -*- coding: utf-8 -*-
"""Unit tests for runtime/vision/worker/protocol.py (no cv2 needed)."""
from __future__ import annotations

import io
import json
import sys
from pathlib import Path

import pytest

WORKER = Path(__file__).resolve().parents[3] / "runtime" / "vision" / "worker"
if str(WORKER) not in sys.path:
    sys.path.insert(0, str(WORKER))

import protocol as wire  # noqa: E402


def test_constants_are_stable():
    assert wire.PROTOCOL_VERSION == 1
    assert wire.MAX_FRAME_BYTES == 8 * 1024 * 1024
    assert wire.STAGES == frozenset({"config", "vision", "input"})
    assert wire.ALL_OPS == wire.BUILTIN_OPS | wire.VISION_OPS


def test_encode_frame_is_single_line_json():
    frame = wire.encode_frame({"id": 1, "ok": True, "result": {"text": "a\nb"}})
    assert frame.endswith(b"\n")
    assert frame.count(b"\n") == 1
    decoded = json.loads(frame.decode("utf-8"))
    assert decoded == {"id": 1, "ok": True, "result": {"text": "a\nb"}}


def test_encode_frame_rejects_oversized_response():
    with pytest.raises(wire.VisionError) as raised:
        wire.encode_frame({"id": 1, "blob": "x" * (wire.MAX_FRAME_BYTES + 1)})
    error = raised.value
    assert error.code == "vision_protocol_error"
    assert error.stage == "vision"
    assert error.retryable is False
    assert error.details["maxFrameBytes"] == wire.MAX_FRAME_BYTES


def test_encode_frame_rejects_non_serializable_payload():
    with pytest.raises(wire.VisionError) as raised:
        wire.encode_frame({"id": 1, "value": object()})
    assert raised.value.code == "vision_protocol_error"


def test_vision_error_payload_contains_structured_fields():
    error = wire.VisionError(
        "vision_model_missing",
        "config",
        "no such model",
        fix="add it",
        retryable=True,
        details={"model": "x"},
    )
    payload = error.to_payload()
    assert payload == {
        "code": "vision_model_missing",
        "stage": "config",
        "message": "no such model",
        "retryable": True,
        "fix": "add it",
        "details": {"model": "x"},
    }


def test_vision_error_rejects_unknown_stage():
    with pytest.raises(ValueError):
        wire.VisionError("code", "scene", "message")


def test_vision_error_rejects_empty_code():
    with pytest.raises(ValueError):
        wire.VisionError("", "vision", "message")


def test_as_vision_error_passes_through_and_wraps():
    original = wire.VisionError("dnn_disabled", "config", "disabled")
    assert wire.as_vision_error(original) is original
    wrapped = wire.as_vision_error(ValueError("boom"))
    assert wrapped.code == "vision_worker_failed"
    assert wrapped.stage == "vision"
    assert wrapped.retryable is False
    assert wrapped.details == {"exceptionType": "ValueError"}


def test_decode_frame_accepts_valid_frame():
    frame = wire.decode_frame(b'{"id": 7, "op": "status", "args": {"a": 1}, "configRevision": 3}')
    assert frame == {"id": 7, "op": "status", "args": {"a": 1}, "configRevision": 3}


def test_decode_frame_defaults_missing_args_to_empty_object():
    frame = wire.decode_frame(b'{"id": 7, "op": "status"}')
    assert frame["args"] == {}


def test_decode_frame_rejects_boolean_frame_ids():
    with pytest.raises(wire.VisionError) as raised:
        wire.decode_frame(b'{"id": true, "op": "status"}')
    assert raised.value.code == "vision_protocol_error"


@pytest.mark.parametrize(
    "line",
    [
        b"",
        b"   ",
        b"not json",
        b'["list"]',
        b'{"op": "status"}',
        b'{"id": 1}',
        b'{"id": 1, "op": "status", "args": [1]}',
    ],
)
def test_decode_frame_rejects_malformed_frames(line):
    with pytest.raises(wire.VisionError) as raised:
        wire.decode_frame(line)
    assert raised.value.code == "vision_protocol_error"


def test_frame_reader_reads_multiple_frames_and_crlf():
    stream = io.BytesIO(b'{"id": 1, "op": "status"}\r\n\n{"id": 2, "op": "shutdown"}\n')
    frames = list(wire.FrameReader(stream))
    assert [frame["id"] for frame in frames] == [1, 2]
    assert [frame["op"] for frame in frames] == ["status", "shutdown"]


def test_frame_reader_handles_final_frame_without_newline():
    stream = io.BytesIO(b'{"id": 1, "op": "shutdown"}')
    assert [frame["id"] for frame in wire.FrameReader(stream)] == [1]


def test_frame_reader_rejects_oversized_frame():
    stream = io.BytesIO(b'{"id": 1, "op": "' + b"x" * 50 + b'"}')
    with pytest.raises(wire.VisionError) as raised:
        list(wire.FrameReader(stream, max_frame_bytes=16))
    assert raised.value.code == "vision_protocol_error"
    assert raised.value.details["maxFrameBytes"] == 16


def test_frame_reader_counts_the_newline_in_the_limit():
    at_limit = b'{"id":1,"op":"x"}'
    assert len(at_limit) + 1 == 18
    assert [frame["id"] for frame in wire.FrameReader(io.BytesIO(at_limit + b"\n"), max_frame_bytes=18)] == [1]
    with pytest.raises(wire.VisionError) as raised:
        list(wire.FrameReader(io.BytesIO(at_limit + b"y\n"), max_frame_bytes=18))
    assert raised.value.code == "vision_protocol_error"
    assert raised.value.details["bytes"] == 19


def test_write_frame_flushes_into_stream():
    stream = io.BytesIO()
    wire.write_frame(stream, {"id": 1, "ok": True, "result": {}})
    assert stream.getvalue() == b'{"id":1,"ok":true,"result":{}}\n'
