# -*- coding: utf-8 -*-
"""Unit tests for kit.Api call forms (T5d.5).

These tests use a fake dispatch and do not import bpy. They pin the contract
that made qc_render_selftest F8 fail: api({...}) must use the module's default
op, while api() stays help and api["dispatch"] stays a raw str call.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

from runtime.py.core import kit  # noqa: E402


def make_api(default_op="help"):
    calls = []

    def dispatch(op, payload):
        calls.append((op, payload))
        return json.dumps({"op": op, "args": json.loads(payload)})

    return kit.Api({"version": 1, "dispatch": dispatch}, default_op=default_op), calls


def test_dict_only_uses_default_op():
    api, calls = make_api(default_op="render_views")
    out = api({"views": ["front"]})
    assert out == {"op": "render_views", "args": {"views": ["front"]}}
    assert calls == [("render_views", '{"views": ["front"]}')]


def test_dict_only_without_default_keeps_help():
    api, calls = make_api()
    out = api({"views": ["front"]})
    assert out["op"] == "help"
    assert calls == [("help", '{"views": ["front"]}')]


def test_no_argument_is_still_help():
    api, calls = make_api(default_op="render_views")
    assert api()["op"] == "help"
    assert calls[-1] == ("help", "{}")


def test_positional_op_and_args():
    api, calls = make_api(default_op="render_views")
    out = api("render_views", {"views": ["iso"]})
    assert out["op"] == "render_views"
    assert out["args"] == {"views": ["iso"]}
    assert calls[-1] == ("render_views", '{"views": ["iso"]}')


def test_op_without_args_is_empty_payload():
    api, calls = make_api()
    assert api("help")["op"] == "help"
    assert calls[-1] == ("help", "{}")


def test_dispatch_key_remains_raw_str():
    api, _calls = make_api(default_op="render_views")
    raw = api["dispatch"]("render_catalog", "{}")
    assert isinstance(raw, str)
    assert json.loads(raw)["op"] == "render_catalog"


def test_kwargs_are_json_payload():
    api, calls = make_api()
    api("op", b=2)
    assert calls[-1] == ("op", '{"b": 2}')


def test_default_op_is_visible_and_coerced():
    api, _calls = make_api(default_op="render_views")
    assert api.default_op == "render_views"
    assert make_api(default_op="")[0].default_op == "help"


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__]))
