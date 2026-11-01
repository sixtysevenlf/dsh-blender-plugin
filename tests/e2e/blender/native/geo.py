# -*- coding: utf-8 -*-
"""Native E2E: geometry base module (geo.py).

Ported from the archived geo_selftest.py. F2.3 removed the flat runtime/geo.py
and runtime/kit.py, so the native runner preloads the registry modules instead
of load_flat. Assertions are the same evidence keys plus ok/no-error.
"""

from __future__ import annotations


def test_geo_selftest(ctx):
    # The manifest preloads geo (and kit through the registry); exercising the
    # selftest is the same module body the archived case loaded by hand.
    api = ctx.api("geo")
    out = ctx.call(api, "selftest")
    evidence = out.get("evidence") or {}
    ctx.check("geo version is 1", out.get("version") == 1, out.get("version"))
    for key in sorted(evidence):
        ctx.check("geo evidence " + key, evidence[key] is True, evidence.get(key))
    ctx.check("geo ok", out.get("ok") is True, out.get("ok"))
    ctx.check("geo has no error", not out.get("error"), out.get("error"))
