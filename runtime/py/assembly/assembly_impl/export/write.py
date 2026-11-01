# -*- coding: utf-8 -*-
"""asm_export 阶段 3：真实导出（逐格式导出 + 校验）与求值网格汇总。"""
from ..common import *  # noqa: F401,F403
from .helpers import *  # noqa: F401,F403


def _asm_export_one_format(fmt, objs, path, verify, warnings):
    """导出单个格式并按需校验；返回回执 dict（失败时追加 warning）。"""
    try:
        rec = _asm_export_one(fmt, objs, path)
        if verify:
            v = _asm_verify_file(fmt, path)
            rec["verify"] = v
            rec["magic_ok"] = v.get("magic_ok")
            rec["ok"] = bool(v.get("ok"))
            if rec.get("md5") is None:
                rec["md5"] = v.get("md5")
            if not rec["ok"]:
                rec["error"] = v.get("error") or "verify 失败"
        else:
            rec["verify"] = {"skipped": True, "reason": "verify=false（只查存在与非空）"}
            rec["ok"] = bool(rec.get("exists") and (rec.get("bytes") or 0) > 0)
            if not rec["ok"]:
                rec["error"] = "文件不存在或为空"
    except Exception as e:
        rec = {"ok": False, "path": path,
               "error": "%s: %s" % (type(e).__name__, str(e)[:240]),
               "verify": {"skipped": True, "reason": "导出异常"}}
    if not rec.get("ok"):
        warnings.append("%s 导出/校验失败：%s" % (fmt, str(rec.get("error"))[:180]))
    return rec


def _asm_export_loop(ctx):
    """逐格式导出并收集 files/md5map/ok/failed。"""
    exports, files, md5map = {}, {}, {}
    ok_formats, failed = [], []
    verify = ctx["flags"]["verify"]
    warnings = ctx["warnings"]
    for fmt in ctx["fmts"]:
        path = ctx["planned"][fmt]
        rec = _asm_export_one_format(fmt, ctx["objs"], path, verify, warnings)
        exports[fmt] = rec
        files[fmt] = path
        if rec.get("ok"):
            ok_formats.append(fmt)
            md5map[fmt] = rec.get("md5")
            if fmt == "obj":
                mtl = os.path.splitext(path)[0] + ".mtl"
                if os.path.isfile(mtl):
                    files["obj_mtl"] = mtl
                    md5map["obj_mtl"] = _asm_md5_file(mtl)
        else:
            failed.append(fmt)
    return exports, files, md5map, ok_formats, failed


def _asm_export_all(ctx):
    """建目录 + 导出 + 求值网格汇总；返回 (err, ctx)。"""
    od = ctx["od"]
    try:
        os.makedirs(od, exist_ok=True)
    except Exception as e:
        return _err("产物目录建不出来: %s: %s" % (type(e).__name__, str(e)[:200]), dir=od), None
    exports, files, md5map, ok_formats, failed = _asm_export_loop(ctx)
    mmu = float(_KIT.units())
    md, md_errors = _mesh_map(ctx["objs"])
    summary = _asm_mesh_summary(ctx["names"], md, mmu)
    if summary.get("missing"):
        ctx["warnings"].append("以下对象没有求值网格（manifest 里标 missing）：%s" % summary["missing"][:10])
    manifest_path = ctx["planned"].get("manifest")
    ledger_path = ctx["planned"].get("ledger")
    if ctx["flags"]["include_receipt"]:
        files["manifest"] = manifest_path
    if ctx["flags"]["ledger"]:
        files["ledger"] = ledger_path
    if ctx["flags"]["merge"]:
        files["blend"] = (((ctx["merge_slim"] or {}).get("outputs") or {}).get("blend")
                          or ctx["merge_out_blend"])
    ctx.update({"exports": exports, "files": files, "md5map": md5map, "ok_formats": ok_formats,
                "failed": failed, "mmu": mmu, "summary": summary, "md_errors": md_errors,
                "manifest_path": manifest_path, "ledger_path": ledger_path})
    return None, ctx
