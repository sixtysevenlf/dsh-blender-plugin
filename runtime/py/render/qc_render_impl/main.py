# -*- coding: utf-8 -*-
"""qc_render_views 入口（941 行拆为 views / views_render 两模块的阶段 helper，主函数只做编排）。"""
import time

from .common import *  # noqa: F401,F403
from .common import _unpack_args
from .views import (_qv_aim, _qv_lock, _qv_open_file, _qv_parts, _qv_prepare, _qv_setup,
                    _qv_vlist, _qv_views)
from .views_render import (_qv_finish_checks, _qv_finish_out, _qv_loop, _qv_post,
                           _qv_restore, _qv_scene)


def qc_render_views(args=None):
    t0 = time.perf_counter()
    a = _unpack_args(args)
    st = {"a": a, "t0": t0}
    for _fn_ in (_qv_prepare, _qv_open_file, _qv_aim, _qv_views, _qv_vlist,
                 _qv_parts, _qv_setup, _qv_lock):
        _r = _fn_(st)
        if isinstance(_r, str):
            return _r
    try:
        _qv_scene(st)
        _qv_loop(st)
        _qv_post(st)
    except BaseException as e:
        import traceback
        st["err"] = {"error": "%s: %s" % (type(e).__name__, str(e)[:300]),
                     "traceback": traceback.format_exc()[-2000:]}
    finally:
        _qv_restore(st)
    _qv_finish_checks(st)
    return _qv_finish_out(st)
