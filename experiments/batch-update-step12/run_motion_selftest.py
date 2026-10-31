# -*- coding: utf-8 -*-
"""DEPRECATED historical experiment.

验证 _restore_transforms 批量改造后的还原语义。它引用的 tests/motion_selftest.py
已在 T11b.6 迁移到 tests/e2e/blender/native/motion.py；本脚本仅作实验记录，
不再适用于当前树。
"""
import importlib.util, os, sys

RT = r"\\wsl.localhost\Ubuntu\home\sixtyseven67\DSH\.scratch\step12\runtime"
TEST = r"\\wsl.localhost\Ubuntu\home\sixtyseven67\DSH\.scratch\step12\tests\motion_selftest.py"

spec = importlib.util.spec_from_file_location("offline_bootstrap", os.path.join(RT, "offline_bootstrap.py"))
ob = importlib.util.module_from_spec(spec); spec.loader.exec_module(ob)
# headless 工具可能已注入旧版内核 kit（来自安装的插件 runtime）⇒ 强制用本工作树的 kit
_K = sys.modules.get("dsh_rt_kernel")
if _K is not None and hasattr(_K, "dsh_kit"):
    try:
        delattr(_K, "dsh_kit")
    except Exception:
        pass

sys.path.insert(0, RT)
ob.boot(["motion"])
sys.argv = [TEST, "--", r"D:/DSH/blender/tmp/motion_selftest_out"]
g = {"__name__": "__main__", "__file__": TEST}
exec(compile(open(TEST, encoding="utf-8").read(), TEST, "exec"), g)
