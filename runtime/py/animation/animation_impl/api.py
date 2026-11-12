# -*- coding: utf-8 -*-
"""animation API registration: structured failures + one-line kit registration.

The register-ops call is literal on purpose: scripts/catalog_dispatch_keys.py reads
this file with ast and needs the literal op keys {"report", "selftest", "help"}.
"""
import sys

from . import common, help as help_impl, report as report_impl, selftest as selftest_impl

ANIMATION_VERSION = common.ANIMATION_VERSION

_K = sys.modules.get("dsh_rt_kernel")
_KIT = getattr(_K, "dsh_kit", None)
if _KIT is None:
    raise RuntimeError("animation 需要共享内核 K.dsh_kit（由 KERNEL_BOOTSTRAP 注入）")


def _structured(fn):
    def run(**kwargs):
        try:
            return fn(**kwargs)
        except common.AnimationError as exc:
            return exc.payload()
        except Exception as exc:
            message = "%s: %s" % (type(exc).__name__, str(exc)[:300])
            return {
                "ok": False,
                "module": "animation",
                "version": ANIMATION_VERSION,
                "code": "animation_unsupported",
                "stage": "animation",
                "error": message,
                "root_cause": message,
                "fix": "确认 Blender 版本支持 evaluated depsgraph / BVHTree API",
                "fallback": "先用 blender_py 读单帧状态，或缩小请求（只对象 / 只接触）",
                "units": dict(common.UNITS),
            }

    run.__name__ = getattr(fn, "__name__", "run")
    run.__doc__ = getattr(fn, "__doc__", "")
    return run


animation_report = _structured(report_impl.animation_report)
animation_selftest = _structured(selftest_impl.animation_selftest)
animation_help = _structured(help_impl.animation_help)

_KIT.register("animation", ANIMATION_VERSION,
              {"report": animation_report, "selftest": animation_selftest, "help": animation_help},
              extra={"units": dict(common.UNITS),
                     "limits": {"objects": common.MAX_OBJECTS, "bones": common.MAX_BONES,
                                "contact_bones": common.MAX_CONTACT_BONES,
                                "intersection_objects": common.MAX_INTERSECTION_OBJECTS,
                                "samples": common.MAX_SAMPLES,
                                "contact_intervals": common.MAX_CONTACT_INTERVALS,
                                "intersection_pairs": common.MAX_INTERSECTION_PAIRS,
                                "reference_frames": common.MAX_REFERENCE_FRAMES,
                                "triangle_budget_per_frame": common.TRIANGLE_BUDGET}},
              default_op="report")
