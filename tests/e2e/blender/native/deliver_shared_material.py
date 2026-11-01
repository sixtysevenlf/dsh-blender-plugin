# -*- coding: utf-8 -*-
"""Native E2E: deliver usemtl counts with a shared material (issue #55).

The bug was metadata-only: OBJ wrote one usemtl line per object, but the
manifest stored the number of unique materials.  This module exercises the
new line-count semantics, the legacy-manifest compatibility path, and the
negative case (a missing usemtl line must still fail verify).
"""

from __future__ import annotations

import json
import os
import shutil

import bpy


def _box(name, x, mat):
    bpy.ops.mesh.primitive_cube_add(size=0.4, location=(x, 0.0, 0.0))
    ob = bpy.context.active_object
    ob.name = name
    ob.data.materials.append(mat)
    return ob


def _scan(path):
    n_g = n_u = 0
    names = set()
    with open(path, "r", encoding="utf-8", errors="replace") as fh:
        for line in fh:
            if line.startswith("g "):
                n_g += 1
            elif line.startswith("usemtl "):
                n_u += 1
                names.add(line[7:].strip())
    return {"g": n_g, "usemtl": n_u, "usemtl_unique": len(names)}


def _manifest_path(d):
    for name in sorted(os.listdir(d)):
        if name.endswith("_manifest.json"):
            return os.path.join(d, name)
    return None


def _write_manifest(path, man):
    with open(path, "w", encoding="utf-8", newline="\n") as fh:
        json.dump(man, fh, ensure_ascii=False, indent=2)
        fh.write("\n")


def _copy_export(src, dst):
    """Copy an export directory and retarget the manifest paths to the copy."""
    shutil.copytree(src, dst)
    man_path = _manifest_path(dst)
    with open(man_path, "r", encoding="utf-8") as fh:
        man = json.load(fh)
    man["dir"] = dst
    for f in man.get("files") or []:
        f["path"] = os.path.join(dst, f.get("name") or os.path.basename(str(f.get("path") or "")))
    _write_manifest(man_path, man)
    return man_path


def _make_legacy(src, dst):
    """Copy an export and rewrite its manifest to the pre-3.0.1 unique-count semantics."""
    man_path = _copy_export(src, dst)
    with open(man_path, "r", encoding="utf-8") as fh:
        man = json.load(fh)
    counts = man.setdefault("counts", {})
    if "usemtl_unique" in counts:
        counts["usemtl"] = counts["usemtl_unique"]
        counts.pop("usemtl_unique", None)
    for f in man.get("files") or []:
        if "usemtl_unique" in f:
            f["usemtl"] = f["usemtl_unique"]
            f.pop("usemtl_unique", None)
    _write_manifest(man_path, man)
    return man_path


def test_deliver_shared_material(ctx):
    api = ctx.api("deliver")
    out = str(ctx.test_dir / "new")
    mat = bpy.data.materials.new("MAT_SHARED")
    mat.use_nodes = True
    try:
        objs = [_box("SHARED_A", 0.0, mat), _box("SHARED_B", 0.6, mat)]
        bpy.context.view_layer.update()
        exp = ctx.call(api, "export", out, [o.name for o in objs], None, "shared", "obj")
        if not ctx.check("export ok", exp.get("ok") is True, exp.get("error")):
            return
        counts = exp.get("counts") or {}
        ctx.check("counts.usemtl = 2 (one line per object)", counts.get("usemtl") == 2, counts)
        ctx.check("counts.usemtl_unique = 1", counts.get("usemtl_unique") == 1, counts)
        ctx.check("counts.materials = 1", counts.get("materials") == 1, counts)
        obj_meta = (exp.get("files") or {}).get("obj") or {}
        ctx.check("files.obj.usemtl = 2 and usemtl_unique = 1",
                  obj_meta.get("usemtl") == 2 and obj_meta.get("usemtl_unique") == 1, obj_meta)
        obj = obj_meta.get("path")
        sc = _scan(obj)
        ctx.check("OBJ has 2 g / 2 usemtl lines / 1 unique material",
                  sc["g"] == 2 and sc["usemtl"] == 2 and sc["usemtl_unique"] == 1, sc)
        ver = ctx.call(api, "verify", out, "auto")
        ctx.check("new manifest verify PASS",
                  ver.get("ok") is True and int(ver.get("failed") or 0) == 0, ver.get("failed_checks"))

        bad_meta = str(ctx.test_dir / "bad_meta")
        man_path = _copy_export(out, bad_meta)
        with open(man_path, "r", encoding="utf-8") as fh:
            man = json.load(fh)
        man["counts"]["usemtl"] = man["counts"].get("usemtl_unique")
        _write_manifest(man_path, man)
        vbad_meta = ctx.call(api, "verify", bad_meta, "auto")
        ctx.check("new manifest with wrong counts.usemtl FAILs metadata check",
                  vbad_meta.get("ok") is False and "obj_usemtl_metadata_match" in str(vbad_meta.get("failed_checks")),
                  vbad_meta.get("failed_checks"))

        legacy = str(ctx.test_dir / "legacy")
        _make_legacy(out, legacy)
        vlegacy = ctx.call(api, "verify", legacy, "auto")
        ctx.check("legacy manifest (usemtl = unique count) verify PASS",
                  vlegacy.get("ok") is True, vlegacy.get("failed_checks"))

        tamper = str(ctx.test_dir / "tamper")
        man_path = _make_legacy(out, tamper)
        with open(man_path, "r", encoding="utf-8") as fh:
            man = json.load(fh)
        obj_path = next(f["path"] for f in man.get("files") or [] if str(f.get("name", "")).endswith(".obj"))
        with open(obj_path, "r", encoding="utf-8") as fh:
            lines = fh.read().splitlines(True)
        for i, line in enumerate(lines):
            if line.startswith("usemtl "):
                del lines[i]
                break
        with open(obj_path, "w", encoding="utf-8", newline="\n") as fh:
            fh.writelines(lines)
        vbad = ctx.call(api, "verify", tamper, "auto")
        ctx.check("missing usemtl line still FAILs obj_usemtl_count_match",
                  vbad.get("ok") is False and "obj_usemtl_count_match" in str(vbad.get("failed_checks")),
                  vbad.get("failed_checks"))
    finally:
        for name in ("SHARED_A", "SHARED_B"):
            ob = bpy.data.objects.get(name)
            if ob is not None:
                bpy.data.objects.remove(ob, do_unlink=True)
        m = bpy.data.materials.get(mat.name)
        if m is not None:
            bpy.data.materials.remove(m)
