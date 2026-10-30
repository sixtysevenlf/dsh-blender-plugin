# -*- coding: utf-8 -*-
"""T8 兼容 shim —— 实现已迁移到 runtime/py/geometry/geo.py。

本文件只做 re-export，不含业务逻辑；保留一版，旧 import 路径继续可用：
    import runtime.geo                 # 仓库根在 sys.path
    import geo                         # runtime/ 在 sys.path（下方 fallback）
"""
try:
    from runtime.py.geometry.geo import *  # noqa: F401,F403
except ModuleNotFoundError as _e:
    # 旧式 flat import：sys.path 里只有 runtime/ 时 runtime 包不可见，改走 py.*。
    # 只兜 "找不到 runtime/py 顶层包" 这一种情况；模块内部缺依赖（bpy 等）照常抛出。
    if not (_e.name or "").startswith(("runtime", "py")):
        raise
    from py.geometry.geo import *  # noqa: F401,F403
