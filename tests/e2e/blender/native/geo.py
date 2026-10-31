# -*- coding: utf-8 -*-
"""Native E2E: geometry base module (geo.py).

Ported from the archived cases/geo_selftest.py.  The legacy case deliberately
loads kit.py and geo.py itself instead of relying on kernel preloads; load_flat
keeps that behavior.  Assertions are the same evidence keys plus ok/no-error.
"""

from __future__ import annotations


def test_geo_selftest(ctx):
    # Preserve the archived case's own bootstrap: it tests the module body, not
    # the kernel loader.  The native runner has already created K/kit for us.
    ctx.load_flat("kit.py")
    ctx.load_flat("geo.py")
    api = ctx.api("geo")
    out = ctx.call(api, "selftest")
    evidence = out.get("evidence") or {}
    ctx.check("geo version is 1", out.get("version") == 1, out.get("version"))
    for key in sorted(evidence):
        ctx.check("geo evidence " + key, evidence[key] is True, evidence.get(key))
    ctx.check("geo ok", out.get("ok") is True, out.get("ok"))
    ctx.check("geo has no error", not out.get("error"), out.get("error"))
