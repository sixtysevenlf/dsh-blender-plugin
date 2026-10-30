# -*- coding: utf-8 -*-
"""tests/geo_selftest.py —— 几何底座（runtime/geo.py）自检。

跑法（需要 Blender，因为 geo.py 用 bpy/bmesh）：
    blender -b --factory-startup --python tests/geo_selftest.py
或经插件无头通道（script_file 传 UNC）：
    blender_rt_headless(script_file="\\\\wsl.localhost\\Ubuntu\\home\\sixtyseven67\\DSH\\dsh-blender-plugin\\tests\\geo_selftest.py")

为什么自带加载：本文件**不依赖 KERNEL_BOOTSTRAP**，自己按序 exec kit.py → geo.py，
所以它测的是模块本体；bootstrap 的"任何路径都在"由 tests 之外的引擎侧断言覆盖。
"""
import io
import json
import os
import sys
import types

HERE = os.path.dirname(os.path.abspath(__file__))
RT = os.path.normpath(os.path.join(HERE, "..", "runtime"))

K = sys.modules.get("dsh_rt_kernel")
if K is None:
    K = types.ModuleType("dsh_rt_kernel")
    sys.modules["dsh_rt_kernel"] = K

for nm in ("kit.py", "geo.py"):
    p = os.path.join(RT, nm)
    with io.open(p, encoding="utf-8") as fh:
        src = fh.read()
    exec(compile(src, nm, "exec"), {"__name__": nm[:-3]})

out = json.loads(K.dsh_geo_api["selftest"]())
failed = sorted(k for k, v in (out.get("evidence") or {}).items() if v is not True)
print("GEO_SELFTEST " + json.dumps({"ok": out.get("ok"), "version": out.get("version"),
                                   "failed": failed, "evidence": out.get("evidence"),
                                   "canary": out.get("canary")}, ensure_ascii=False))
if out.get("error"):
    print("GEO_SELFTEST_ERROR " + str(out["error"]))
if failed or not out.get("ok"):
    sys.exit(1)
print("GEO_SELFTEST ALL PASS")
