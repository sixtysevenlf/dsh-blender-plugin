# -*- coding: utf-8 -*-
"""Split from assembly.py; behavior preserved by the facade."""
from .common import *  # noqa: F401,F403
from .manifest import asm_manifest
from .merge import asm_merge
from .scan import asm_scan
from .gate import asm_gate
from .repair import asm_repair, fix_interference
from .export import asm_export
from .selftest import asm_help, asm_selftest

_ALIASES = {"audit_clash_all": "scan", "merge_gate": "gate", "fix_clash": "repair",
            "asm_export": "export"}
_OPS = {"scan": asm_scan, "audit_clash_all": asm_scan, "manifest": asm_manifest,
        "merge": asm_merge, "gate": asm_gate, "merge_gate": asm_gate,
        "repair": asm_repair, "fix_clash": asm_repair, "fix_interference": fix_interference,
        "export": asm_export, "asm_export": asm_export,
        "selftest": asm_selftest, "help": asm_help}


def _asm_dispatch(op, args_json=None):
    kw = _KIT.flatten(args_json)
    try:
        kw = _KIT.resolve_refs(kw)
    except KeyError as e:
        return _j({"ok": False, "error": "引用解析失败: %s" % str(e)[:160], "op": op})
    name = str(op or "").strip()
    key = _ALIASES.get(name, name)
    fn = _OPS.get(key)
    if fn is None:
        try:
            import difflib
            cand = difflib.get_close_matches(key, sorted(set(_OPS)), n=3, cutoff=0.4)
        except Exception:
            cand = []
        return _j({"ok": False, "error": "unknown assembly op", "op": name,
                   "ops": sorted(set(_OPS)), "aliases": _ALIASES, "did_you_mean": cand})
    import inspect
    try:
        allowed = set(inspect.signature(fn).parameters)
    except (TypeError, ValueError):
        allowed = None
    if allowed is not None:
        unknown = sorted(set(kw) - allowed)
        if unknown:
            return _j({"ok": False, "error": "不认识的参数 %s" % unknown, "op": name,
                       "allowed": sorted(allowed)})
    try:
        out = fn(**kw)
    except TypeError as e:
        return _j({"ok": False, "error": "参数不匹配: %s" % str(e)[:220], "op": name,
                   "allowed": sorted(allowed or [])})
    except Exception as e:
        import traceback
        return _j({"ok": False, "error": "%s: %s" % (type(e).__name__, str(e)[:240]),
                   "op": name, "traceback": traceback.format_exc()[-600:]})
    return out if isinstance(out, str) else _j(out)


_KIT.register("assembly", ASSEMBLY_VERSION, _OPS, dispatch=_asm_dispatch)
