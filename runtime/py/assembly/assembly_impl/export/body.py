# -*- coding: utf-8 -*-
"""asm_export 阶段 6：dry_run 计划回执与最终回执（verdict + body）。"""
from ..common import *  # noqa: F401,F403
from .helpers import *  # noqa: F401,F403


def _asm_planned_body(ctx):
    """dry_run：只回计划（不建目录/不导出/不写账本）。"""
    names = ctx["names"]
    warnings = ctx["warnings"] + (["merge dry_run：gate 跳过（gate=skipped_dry_run）"]
                                 if ctx["flags"]["merge"] else [])
    return {"ok": True, "verdict": "planned", "dry_run": True, "readonly": True,
            "stage": "planned", "dir": ctx["od"], "name": ctx["nm"], "formats": ctx["fmts"],
            "files": ctx["planned"], "objects": list(names)[:200],
            "counts": {"objects": len(names), "formats": len(ctx["fmts"])},
            "gate": ctx["gate_slim"], "merge": ctx["merge_slim"], "md5": {}, "manifest_md5": None,
            "warnings": warnings, "verify": bool(ctx["flags"]["verify"]),
            "verdict_line": "asm_export（dry_run，只读）：计划 %d 件 → %s；不建目录/不导出/不写账本"
                            % (len(names), "/".join(ctx["fmts"])),
            "hint": "dry_run 只回计划；确认后传 dry_run=false（merge=true 会先 asm_merge(dry_run=false)）",
            "honest_limits": list(_ASM_EXPORT_LIMITS)}


def _asm_final_verdict(ctx):
    """最终 verdict 规则（全部失败 ⇒ fail；否则沿用 gate verdict）。"""
    warnings = ctx["warnings"]
    if not ctx["ok_formats"]:
        warnings.append("全部格式都失败 ⇒ ok:false（看 exports[].error）")
        return "fail"
    verdict = (ctx["gate_slim"] or {}).get("verdict") or "unverified"
    if verdict != "pass":
        warnings.append("gate verdict=%s（≠ pass）：文件已导出但不可直接当作出厂件交付" % verdict)
    return verdict


def _asm_final_body(ctx):
    """真实导出完成后的回执 body。"""
    verdict = _asm_final_verdict(ctx)
    ok = bool(ctx["ok_formats"])
    names = ctx["names"]
    led = ctx["ledger_rec"] or {}
    return {"ok": ok, "verdict": verdict, "dry_run": False, "stage": "exported",
            "dir": ctx["od"], "name": ctx["nm"], "formats": ctx["fmts"],
            "formats_ok": ctx["ok_formats"], "formats_failed": ctx["failed"],
            "files": ctx["files"], "counts": dict(ctx["summary"]["counts"], files=len(ctx["ok_formats"])),
            "objects": list(names)[:200], "gate": ctx["gate_slim"], "merge": ctx["merge_slim"],
            "md5": ctx["md5map"], "manifest_md5": ctx["mid"],
            "exports": {k: _asm_export_slim(v) for k, v in ctx["exports"].items()},
            "warnings": ctx["warnings"], "verify": bool(ctx["flags"]["verify"]), "ledger": ctx["ledger_rec"],
            "verdict_line": ("asm_export：%d 件 → %s（成功 %s / 失败 %s）· gate=%s · manifest=%s · ledger=%s"
                             % (len(names), "/".join(ctx["fmts"]), "/".join(ctx["ok_formats"]) or "无",
                                "/".join(ctx["failed"]) or "无", verdict,
                                "written" if ctx["mid"] else "skipped",
                                "appended" if led.get("ok") else "skipped")),
            "hint": ("导出完成：先读 verdict（degraded≠通过）与 warnings；manifest 里有逐文件 md5/bbox")
                    if ok else "全部格式失败：看 exports[].error（GLB 需 io_scene_gltf2 / FBX 需 io_scene_fbx）",
            "honest_limits": list(_ASM_EXPORT_LIMITS), "md_errors": ctx["md_errors"]}
