# -*- coding: utf-8 -*-
"""Unit checks for the generator_* dispatch parameter corridor (no Blender).

Migrated from tests/generator_dispatch_selftest.py. Field bugs pinned here:

  * the catalog skeleton said script:"..." while generator_save takes code
    -> following the catalog raised TypeError;
  * generator_dispatch flattened the args key unconditionally, but for
    generator_run args is the generator PARAMS dict -> params vanished and the
    call either errored or silently ran with empty parameters.

The module needs the shared kernel (dsh_rt_kernel.dsh_kit); this file boots the
real runtime/py/core/kit.py into a stub kernel exactly like the legacy test did,
then imports runtime/generator.py.
"""
import importlib.util
import json
import os
import sys
import types

import pytest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
GENERATOR = os.path.join(ROOT, "runtime", "generator.py")
KIT = os.path.join(ROOT, "runtime", "py", "core", "kit.py")

CODE = "\n".join([
    "n = int(PARAMS.get('n', 0))",
    "print('DSH_RECEIPT ' + json.dumps({'made': n, 'params_seen': sorted(PARAMS.keys())}))",
])


@pytest.fixture(scope="module")
def generator():
    saved = sys.modules.get("dsh_rt_kernel")
    kernel = types.ModuleType("dsh_rt_kernel")
    kernel.dsh_generator = {"runs": [], "log": []}
    sys.modules["dsh_rt_kernel"] = kernel
    with open(KIT, encoding="utf-8") as fh:
        exec(compile(fh.read(), KIT, "exec"), {})
    try:
        spec = importlib.util.spec_from_file_location("dsh_generator", GENERATOR)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        yield module
    finally:
        if saved is None:
            sys.modules.pop("dsh_rt_kernel", None)
        else:
            sys.modules["dsh_rt_kernel"] = saved


def dispatch(generator, op, payload):
    return json.loads(generator.generator_dispatch(op, json.dumps(payload)))


def test_save_takes_code_not_script(generator, tmp_path):
    tmp = str(tmp_path)
    saved = dispatch(generator, "save", {"name": "disp", "code": CODE, "params": {"n": 3}, "dir": tmp})
    assert saved.get("ok") is True, saved
    assert saved.get("hash"), saved
    assert sorted(os.listdir(tmp)) == ["disp.json", "disp.py"]

    wrong = dispatch(generator, "save", {"name": "disp2", "script": CODE, "dir": tmp})
    assert wrong.get("ok") is False, wrong
    assert "script" in str(wrong.get("error")), wrong
    assert "code" in str(wrong.get("expected", "")), wrong


def test_run_keeps_nested_args_not_flattened(generator, tmp_path):
    tmp = str(tmp_path)
    missing = dispatch(generator, "run", {"name": "nope_xyz", "args": {"n": 3}, "dir": tmp, "timeout_ms": 1000})
    assert "生成器不存在" in str(missing.get("error")), missing
    assert "nope_xyz" in str(missing.get("error")), missing

    only_args = dispatch(generator, "run", {"args": {"n": 3}})
    assert only_args.get("ok") is False, only_args
    assert "name" in str(only_args.get("error")), only_args
    assert only_args.get("expected"), only_args
    assert "args" in str(only_args.get("note")), only_args


def test_single_key_args_envelope_is_still_accepted(generator, tmp_path):
    tmp = str(tmp_path)
    envelope = dispatch(generator, "list", {"args": {"dir": tmp}})
    assert envelope.get("ok") is True, envelope
    assert os.path.abspath(envelope.get("root", "")) == os.path.abspath(tmp)


def test_unknown_op_lists_available_ops(generator):
    bad = dispatch(generator, "nope", {})
    assert bad.get("ok") is False, bad
    assert "help" in (bad.get("ops") or []), bad


def test_help_documents_code_and_nested_args(generator):
    help_payload = json.loads(generator.generator_help())
    assert "code" in help_payload["ops"]["save"], help_payload["ops"]["save"]
    run_help = help_payload["ops"]["run"]
    assert "args" in run_help and "PARAMS" in run_help, run_help
