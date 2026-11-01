# -*- coding: utf-8 -*-
"""asm_export 阶段 1：参数校验 / 格式与名字 / 输出目录 / manifest 校验 / merge 前置。"""
from ..common import *  # noqa: F401,F403
from .helpers import *  # noqa: F401,F403
from ..manifest import _load_manifest, _manifest_validate
from ..merge import asm_merge
from ..repair import _repair_flag


def _asm_flags(merge, dry_run, include_receipt, ledger, verify):
    """5 个布尔参数统一校验；返回 (err, flags)。"""
    out = {}
    for key, raw in (("merge", merge), ("dry_run", dry_run), ("include_receipt", include_receipt),
                     ("ledger", ledger), ("verify", verify)):
        fixed, ferr = _repair_flag(raw, key)
        if ferr:
            return _err(ferr), None
        out[key] = fixed
    return None, out


def _asm_name_and_formats(formats, name):
    """校验 formats/name；返回 (err, fmts, nm, warnings)。"""
    fmts, ferr = _asm_export_formats(formats)
    if ferr:
        return _err(ferr), None, None, None
    nm, nwarn = _asm_export_name(name)
    if nm is None:
        return _err(nwarn), None, None, None
    return None, fmts, nm, ([nwarn] if nwarn else [])


def _asm_target_dir(out_dir, warnings):
    """解析输出目录；返回 (err, od)。"""
    if out_dir is not None and not isinstance(out_dir, (str, os.PathLike)):
        return _err("out_dir 必须是路径字符串，收到 %r" % (out_dir,)), None
    if out_dir:
        return None, _path(str(out_dir))
    K = _kernel()
    base = getattr(K, "out_dir", None) if K is not None else None
    od = _path(os.path.join(base or os.path.expanduser("~"), "asm_export"))
    warnings.append("out_dir 未给 → 默认 %s" % od)
    return None, od


def _asm_manifest_stage(manifest, merge):
    """载入并按需校验 manifest；返回 (err, m)。"""
    m = None
    if manifest is not None:
        m, merr = _load_manifest(manifest)
        if merr:
            return _err(merr), None
        mval = _manifest_validate(m, check_files=bool(merge), check_md5=bool(merge))
        if not mval["ok"]:
            return _err("manifest 校验失败（坏清单上不导出）", errors=mval["errors"][:10],
                        warnings=mval["warnings"][:10]), None
    if merge and m is None:
        return _err("merge=true 必须给 manifest（N 路合并需要零件清单）"), None
    return None, m


def _asm_merge_stage(ctx):
    """merge=true 时先跑 asm_merge（含 dry_run 计划）；返回 (fail_body_or_None, merge_rec)。"""
    flags = ctx["flags"]
    od, nm, fmts = ctx["od"], ctx["nm"], ctx["fmts"]
    dry_run = flags["dry_run"]
    merge_out_blend = os.path.join(od, nm + "_merged.blend")
    ctx["merge_out_blend"] = merge_out_blend
    if not flags["merge"]:
        return None, None
    merge_call = dict(manifest=ctx["m"], out_blend=merge_out_blend, dry_run=bool(dry_run))
    raw_merge = asm_merge(**merge_call)
    merge_rec = json.loads(raw_merge) if isinstance(raw_merge, str) else raw_merge
    if not isinstance(merge_rec, dict) or not merge_rec.get("ok"):
        gv = ((merge_rec or {}).get("gate") or {}).get("verdict") if isinstance(merge_rec, dict) else None
        body = {"ok": False, "verdict": gv or "fail", "dry_run": bool(dry_run),
                "stage": "merge", "dir": od, "name": nm, "formats": fmts,
                "files": {}, "counts": {"objects": 0, "files": 0},
                "gate": _asm_gate_slim((merge_rec or {}).get("gate")),
                "merge": _asm_merge_slim(merge_rec),
                "md5": {}, "warnings": ctx["warnings"],
                "error": "合并失败/装配门未通过 ⇒ 不导出",
                "verdict_line": "asm_export：合并阶段失败（%s）→ 未导出任何文件"
                                % str((merge_rec or {}).get("error"))[:160],
                "hint": "先看 merge.gate / merge.error；修 manifest/几何后重跑；merge 失败不保存 .blend",
                "honest_limits": list(_ASM_EXPORT_LIMITS)}
        return body, None
    return None, merge_rec
