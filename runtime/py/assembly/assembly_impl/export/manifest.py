# -*- coding: utf-8 -*-
"""asm_export 阶段 4：构造并写 <name>_manifest.json。"""
from ..common import *  # noqa: F401,F403
from .helpers import *  # noqa: F401,F403
from ..manifest import _write_json


def _asm_manifest_payload(ctx):
    """构造 manifest 字典（含自指纹）。"""
    nm, od = ctx["nm"], ctx["od"]
    exports, files = ctx["exports"], ctx["files"]
    summary, gate_slim, merge_slim = ctx["summary"], ctx["gate_slim"], ctx["merge_slim"]
    manifest = {
        "schema": "dsh-assembly-export-manifest/1", "tool": "assembly.asm_export",
        "version": ASSEMBLY_VERSION, "created": time.strftime("%Y-%m-%d %H:%M:%S"),
        "name": nm, "dir": od, "units": {"mm_per_unit": _r(ctx["mmu"], 6)},
        "formats": ctx["fmts"], "formats_ok": ctx["ok_formats"], "formats_failed": ctx["failed"],
        "objects": summary["objects"], "missing": summary.get("missing") or [],
        "counts": dict(summary["counts"], files=len(ctx["ok_formats"])),
        "bbox_units": summary.get("bbox_units"), "bbox_mm": summary.get("bbox_mm"),
        "gate": gate_slim, "merge": merge_slim,
        "files": {k: {"path": v, "name": os.path.basename(str(v)),
                      "bytes": (exports.get(k) or {}).get("bytes"),
                      "md5": (exports.get(k) or {}).get("md5"),
                      "verify": _asm_export_slim(exports.get(k)) or {}}
                  for k, v in files.items() if k in ("glb", "fbx", "obj", "obj_mtl") and v},
        "exports": {k: _asm_export_slim(v) for k, v in exports.items()},
        "warnings": ctx["warnings"],
        "honest_limits": list(_ASM_EXPORT_LIMITS),
    }
    # files 里 verify 字段瘦身（避免 manifest 嵌套过深）
    for k, v in (manifest.get("files") or {}).items():
        if isinstance(v.get("verify"), dict):
            v["verify"] = {kk: v["verify"].get(kk) for kk in ("ok", "magic_ok", "bytes", "md5")}
    manifest["manifest_md5"] = _md5_text(_canon({k: v for k, v in manifest.items()
                                                 if k != "manifest_md5"}))
    return manifest


def _asm_manifest_stage(ctx):
    """写 manifest（失败只追加 warning）；返回 ctx。"""
    ctx["mid"] = None
    if not ctx["flags"]["include_receipt"]:
        return ctx
    manifest = _asm_manifest_payload(ctx)
    mid = manifest["manifest_md5"]
    try:
        _write_json(ctx["manifest_path"], manifest)
        ctx["md5map"]["manifest"] = _asm_md5_file(ctx["manifest_path"])
    except Exception as e:
        ctx["warnings"].append("manifest 写失败：%s: %s" % (type(e).__name__, str(e)[:200]))
        ctx["files"]["manifest"] = None
    ctx["mid"] = mid
    return ctx
