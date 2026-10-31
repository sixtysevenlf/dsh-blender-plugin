# -*- coding: utf-8 -*-
"""Blender-side support for native E2E test modules (T11b).

A native module is a plain Python file under tests/e2e/blender/native/.  It is
loaded inside a real Blender process by native_runner.py and exposes
test_<scenario>(ctx) functions.  This is deliberately not pytest: Blender's
bundled Python has no pytest.  The host-side pytest launcher only starts
Blender and asserts the JSON report.

Design rules (see plan section 6.5.1):
- explicit out_dir; never read sys.argv's last argument;
- no sys.exit inside a module;
- use ctx.check for soft assertions (aggregated per test) or plain assert;
- tests start from factory settings; the runner resets the scene per test.
"""

from __future__ import annotations

import json
import os
import re
import sys
import traceback
from pathlib import Path

import bpy

_REPO = Path(__file__).resolve().parents[3]


def reset_scene():
    """Factory-reset the scene and restore metric units.

    The runner calls this before every test, so a test may leave objects behind
    without affecting the next one.  It is intentionally stronger than the old
    legacy runner, which reused one process for all checks in a script.
    """
    bpy.ops.wm.read_factory_settings(use_empty=True)
    scene = bpy.context.scene
    unit = scene.unit_settings
    unit.system = "METRIC"
    unit.scale_length = 1.0
    unit.length_unit = "METERS"
    bpy.context.view_layer.update()


def _safe_name(name):
    cleaned = re.sub(r"[^0-9A-Za-z_.-]+", "_", str(name)).strip("._")
    return cleaned or "test"


class Context:
    """Per-test context handed to native test functions."""

    def __init__(self, module_id, out_dir, repo_root=None):
        self.module_id = module_id
        self.out_dir = Path(out_dir)
        self.repo_root = Path(repo_root) if repo_root else _REPO
        self.K = sys.modules.get("dsh_rt_kernel")
        if self.K is None:
            raise RuntimeError("dsh_rt_kernel is not loaded; native_runner must boot first")
        self.out_dir.mkdir(parents=True, exist_ok=True)
        self.test_name = None
        self.test_dir = None
        self.checks = []
        self.failures = []

    def begin(self, name):
        self.test_name = name
        self.test_dir = self.out_dir / _safe_name(name)
        self.test_dir.mkdir(parents=True, exist_ok=True)
        # The online headless kernel sets K.out_dir per run; keep that contract.
        self.K.out_dir = str(self.test_dir)
        self.checks = []
        self.failures = []
        print("NATIVE_TEST_BEGIN " + self.module_id + "::" + name)

    def check(self, name, cond, detail=""):
        ok = bool(cond)
        self.checks.append({"name": name, "ok": ok, "detail": str(detail)[:1200]})
        if not ok:
            self.failures.append({"name": name, "detail": str(detail)[:1200]})
        print(("  ok   " if ok else "  FAIL ") + name +
              ("" if ok else "  <- " + str(detail)[:800]))
        return ok

    def require(self, name, cond, detail=""):
        if not self.check(name, cond, detail):
            raise AssertionError(name + ": " + str(detail))
        return True

    def api(self, name):
        attr = "dsh_%s_api" % name
        api = getattr(self.K, attr, None)
        if api is None:
            raise AssertionError("kernel has no %s; preload %r" % (attr, name))
        return api

    @staticmethod
    def parse(raw):
        if isinstance(raw, str):
            text = raw.strip()
            if text and text[0] in "[{":
                return json.loads(text)
            return raw
        return raw

    def call(self, api, key, *args, **kwargs):
        if key not in api:
            raise AssertionError("API %r has no method %r" % (api, key))
        return self.parse(api[key](*args, **kwargs))

    def load_flat(self, filename):
        """Execute a runtime module file the way the old cases did.

        This preserves the legacy cases that deliberately load module bodies
        directly (geo_selftest, plan_selftest) instead of relying on preloads.
        """
        path = self.repo_root / "runtime" / filename
        if not path.is_file():
            raise AssertionError("runtime module missing: %s" % path)
        namespace = {"K": self.K, "__name__": path.stem, "__file__": str(path)}
        source = path.read_text(encoding="utf-8")
        exec(compile(source, str(path), "exec"), namespace)
        return namespace

    def scene_snapshot(self):
        return {
            "objects": sorted(o.name for o in bpy.data.objects),
            "collections": sorted(c.name for c in bpy.data.collections),
            "meshes": sorted(m.name for m in bpy.data.meshes),
        }

    def object_names(self):
        return sorted(o.name for o in bpy.data.objects)

    def cleanup(self):
        """Best-effort cleanup after a test; the runner factory-resets anyway."""
        try:
            if bpy.context.object is not None and bpy.context.object.mode != "OBJECT":
                bpy.ops.object.mode_set(mode="OBJECT")
        except Exception:
            pass
        try:
            bpy.ops.object.select_all(action="SELECT")
            bpy.ops.object.delete()
        except Exception:
            pass


def format_exception():
    return traceback.format_exc()
