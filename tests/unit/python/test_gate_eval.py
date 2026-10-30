# -*- coding: utf-8 -*-
"""Unit checks for the restricted gate expression evaluator (no Blender needed).

Migrated from tests/gate_eval_selftest.py. The evaluator used to call Python's
built-in dynamic evaluation entry point on pass_if / degrade_if; supply chain
scanners flag that shape even though the AST whitelist kept it narrow. The fix is
a per-node evaluator (_eval_node). This suite pins:

  A  source surface: no dynamic-eval call form, whitelist remains the only entry
  B  semantic equivalence with the old evaluator (arithmetic, comparisons,
     chained comparisons, boolean short circuit, subscripts, tuples, whitelisted
     functions, missing units/tolerance_mm defaulting to None)
  C  rejection surface (strict, never loosened)

runtime/py/core/gate.py imports bpy and requires dsh_rt_kernel.dsh_kit at import
time; this module stubs both (the stub only needs what the gate touches at import).
"""
import ast
import importlib.util
import os
import sys
import types

import pytest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
GATE = os.path.join(ROOT, "runtime", "py", "core", "gate.py")


class _Kit:
    j = None

    def resolve_refs(self, value):
        return value

    class Api(dict):
        def __init__(self, payload=None, *args, **kwargs):
            super().__init__(payload or {})

    def __getattr__(self, name):
        return lambda *args, **kwargs: None


@pytest.fixture(scope="module")
def gate_module():
    saved = {name: sys.modules.get(name) for name in ("bpy", "dsh_rt_kernel")}
    bpy = types.ModuleType("bpy")
    sys.modules["bpy"] = bpy
    kernel = types.ModuleType("dsh_rt_kernel")
    kernel.dsh_kit = _Kit()
    sys.modules["dsh_rt_kernel"] = kernel
    try:
        spec = importlib.util.spec_from_file_location("gate_under_test", GATE)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        yield module
    finally:
        for name, value in saved.items():
            if value is None:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = value


RECEIPT = {
    "thin_samples_total": 0, "a": 1, "b": 2, "c": 3, "verdict": "refuted",
    "gate": {"verdict": "pass"}, "tags": ["x", "y"], "mod_ok": True,
}


def test_source_no_dynamic_eval_and_whitelist_is_the_entry(gate_module):
    with open(GATE, encoding="utf-8") as fh:
        src = fh.read()
    # Build the needle by concatenation on purpose: otherwise this test file
    # itself matches the scanner rule ("name" followed by an opening paren).
    eval_call = "ev" + "al("
    assert eval_call not in src
    assert "def _eval_node(" in src
    assert "_check_ast(tree, label)" in src
    assert "ALLOWED_FUNCS" in src


@pytest.mark.parametrize("expression,want", [
    ("1 + 2 * 3 == 7", True),
    ("thin_samples_total == 0", True),
    ("a < b < c", True),
    ("not (a == b)", True),
    ("verdict == 'refuted' or verdict == 'pass'", True),
    ("gate['verdict'] == 'pass'", True),
    ("'x' in tags and len(tags) == 2", True),
    ("min(1, 2, 3) + max([4, 5]) == 6", True),
    ("abs(-3) == 3 and round(1.6) == 2", True),
    ("sum([1, 2, 3]) == 6 and all([True, 1]) and any([0, False, 2])", True),
    ('int("4") + float("0.5") == 4.5 and str(9) == "9"', True),
    ("tolerance_mm == None", True),
    ("units == None", True),
    ("mod_ok", True),
    ("(a, b) == (1, 2)", True),
])
def test_semantics_equivalent_to_the_old_evaluator(gate_module, expression, want):
    assert gate_module._eval_pass_if(expression, RECEIPT) is want


def _node(expression):
    return ast.parse(expression, mode="eval").body


def test_short_circuit_returns_operands_not_booleans(gate_module):
    env = {"a": 1, "b": 2, "z": 0}
    assert gate_module._eval_node(_node("a and b"), env, "t") == 2
    assert gate_module._eval_node(_node("z or 5"), env, "t") == 5
    assert gate_module._eval_node(_node("a < b < c"), {"a": 1, "b": 2, "c": 3}, "t") is True
    assert gate_module._eval_node(_node("gate['verdict']"), {"gate": {"verdict": "pass"}}, "t") == "pass"


@pytest.mark.parametrize("expression", [
    "__import__('os')",
    "open('/etc/passwd')",
    "a.__class__",
    "os.system('id')",
    "(1).__class__.__bases__",
    "min(*[1, 2])",
    "min(a, key=len)",
    "unknown_field == 1",
    "a if a else b",
    "[x for x in tags]",
    "lambda: 1",
])
def test_rejects_anything_outside_the_whitelist(gate_module, expression):
    with pytest.raises(Exception):
        gate_module._eval_pass_if(expression, RECEIPT)


def test_missing_receipt_field_names_the_field(gate_module):
    with pytest.raises(ValueError) as excinfo:
        gate_module._eval_pass_if("nope_missing == 1", RECEIPT)
    assert "nope_missing" in str(excinfo.value)
