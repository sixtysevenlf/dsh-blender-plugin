# -*- coding: utf-8 -*-
"""离线逃生口（S9）—— 后端挂了也能用插件能力（官方化，替代手搓 15 行）。

现场：后端一挂，headless/job/do 全废；用户只能在 blender.exe -b 里手搓 dsh_rt_kernel 模块、
再 import runtime/vehicle.py + runtime/audit.py 才恢复能力。这个文件就是那 15 行的官方版。

两种用法（都不需要后端、不需要 KERNEL_BOOTSTRAP）：

    # ① 直接在 Blender 里跑（GUI 或 -b 都行）
    blender -b --factory-startup --python runtime/offline_bootstrap.py -- vehicle audit qc

    # ② 在自己的脚本里
    import offline_bootstrap as ob
    ns = ob.boot(["vehicle", "audit", "qc"])      # 建内核 + 注入 kit + 按名加载模块
    veh = ns["kapi"]("vehicle")                    # → 模块 API（dict，可直接调用）
    print(veh("spec", {"type": "sports"}))

约定：文件名即模块名（runtime/<name>.py）；加载后 API 挂在 K.dsh_<name>_api 上，与在线路径**同一个契约**。
"""
import os
import sys
import types

def _find_rt():
    """定位 runtime 目录：env 优先 → 脚本所在目录 → 脚本目录的 runtime/ 子目录（被拷到 tmp 跑时用）。"""
    cands = [os.environ.get("DSH_BLENDER_RUNTIME"), os.path.dirname(os.path.abspath(__file__)),
             os.path.join(os.path.dirname(os.path.abspath(__file__)), "runtime")]
    for c in cands:
        if c and os.path.exists(os.path.join(c, "kit.py")):
            return c
    raise RuntimeError("定位不到 runtime 目录（kit.py 不在：%s）—— 设 DSH_BLENDER_RUNTIME=<插件>/runtime 或传 runtime_dir=" % cands)


RT = _find_rt()


def _exec(path, ns, label):
    if not os.path.exists(path):
        raise RuntimeError("找不到模块文件：%s（%s）" % (path, label))
    src = open(path, encoding="utf-8").read()
    exec(compile(src, path, "exec"), ns)


def _install_kernel_getattr(K):
    """给内核模块挂 PEP 562 的 `__getattr__`：把"该模块没 preload"的裸 AttributeError
    变成可执行的指引（2026-09-30 实测反馈：4 个 agent 独立卡在 `K.dsh_audit_api` 不存在）。

    只接管 `dsh_*_api` 这一族名字，其余属性保持 Python 原生行为。
    """
    if hasattr(K, "__getattr__"):
        return K

    def _kernel_getattr(name, _K=K):
        if name.startswith("dsh_") and name.endswith("_api"):
            have = sorted(k[4:-4] for k in dir(_K)
                          if k.startswith("dsh_") and k.endswith("_api"))
            base = name[4:-4]
            raise AttributeError(
                "内核里没有 %s —— 模块 %r 没被加载。已加载：%s。"
                "headless 用 preload=%r 带上它；或 K.dsh_kit.kapi(%r) 查/取。"
                % (name, base, have or "（只有 kit）", base, base))
        raise AttributeError(name)

    K.__getattr__ = _kernel_getattr
    return K


def boot(modules=(), runtime_dir=None):
    """建内核 → 注入共享内核 kit → 按名加载模块；返回命名空间（含 K 与 kapi）。"""
    rt = runtime_dir or RT
    K = sys.modules.get("dsh_rt_kernel")
    if K is None:
        K = types.ModuleType("dsh_rt_kernel")
        sys.modules["dsh_rt_kernel"] = K
    _install_kernel_getattr(K)
    if getattr(K, "dsh_kit", None) is None:
        _exec(os.path.join(rt, "kit.py"), {"K": K}, "kit")
    ns = {}
    for name in modules:
        _exec(os.path.join(rt, name + ".py"), ns, name)
    ns["K"] = K

    def kapi(name):
        api = getattr(K, "dsh_%s_api" % name, None)
        if api is None:
            have = sorted(k[len("dsh_"):-len("_api")] for k in dir(K) if k.startswith("dsh_") and k.endswith("_api"))
            raise RuntimeError("模块 %s 没加载；已加载：%s（用 boot([...]) 带上它）" % (name, have))
        return api

    ns["kapi"] = kapi
    return ns


def _main(argv):
    # 只把"纯模块名"当模块（runner 可能把脚本自身路径塞进 argv）
    names = [a for a in argv if not a.startswith("-") and "/" not in a and "\\" not in a and not a.endswith(".py")]
    ns = boot(names)
    loaded = sorted(k[len("dsh_"):-len("_api")] for k in dir(ns["K"]) if k.startswith("dsh_") and k.endswith("_api"))
    print("OFFLINE_BOOTSTRAP ok · 已加载：" + (", ".join(loaded) or "（只有 kit）"))


if __name__ == "__main__":
    _main(sys.argv[1:])
