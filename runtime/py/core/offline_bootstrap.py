# -*- coding: utf-8 -*-
"""离线逃生口（S9）—— 后端挂了也能用插件能力（官方化，替代手搓 15 行）。

现场：后端一挂，headless/job/do 全废；用户只能在 blender.exe -b 里手搓 dsh_rt_kernel 模块、
再 import runtime/py/assembly/vehicle.py + runtime/py/core/audit.py 才恢复能力。这个文件就是那 15 行的官方版。

两种用法（都不需要后端、不需要 KERNEL_BOOTSTRAP）：

    # ① 直接在 Blender 里跑（GUI 或 -b 都行）
    blender -b --factory-startup --python runtime/py/core/offline_bootstrap.py -- vehicle audit qc

    # ② 在自己的脚本里
    import offline_bootstrap as ob
    ns = ob.boot(["vehicle", "audit", "qc"])      # 建内核 + 注入 kit + 按名加载模块
    veh = ns["kapi"]("vehicle")                    # → 模块 API（dict，可直接调用）
    print(veh("spec", {"type": "sports"}))

约定：模块名不变（preload 契约不变）；实现文件按 runtime/py/<领域>/<name>.py 查找，
      旧扁平 runtime/<name>.py（含 generator / uv_tools 两个未分包的模块）兜底。
      加载后 API 挂在 K.dsh_<name>_api 上，与在线路径**同一个契约**。
"""
import os
import sys
import types

# T8：领域包布局（与 runtime/py/<domain>/ 一致）
_DOMAINS = ("core", "geometry", "assembly", "render", "export")


def _find_rt():
    """定位 runtime：env 优先 → 脚本所在目录 → 其 runtime/ 子目录 → 上两级（py/core 布局）。

    返回值是**含 kit.py 的目录**：常规是 <插件>/runtime/py/core，旧布局是 <插件>/runtime。
    """
    here = os.path.dirname(os.path.abspath(__file__))
    cands = [os.environ.get("DSH_BLENDER_RUNTIME"), here,
             os.path.join(here, "runtime"),
             os.path.dirname(here),                    # runtime/py
             os.path.dirname(os.path.dirname(here)),   # runtime
             os.path.join(os.path.dirname(os.path.dirname(here)), "runtime")]
    for c in cands:
        if c and _kit_file(c):
            return c
    raise RuntimeError("定位不到 runtime（kit.py 不在：%s）—— 设 DSH_BLENDER_RUNTIME=<插件>/runtime 或传 runtime_dir=" % cands)


def _kit_file(rt):
    """rt 下的 kit.py：领域布局是 py/core/kit.py，旧布局是 kit.py。"""
    for p in (os.path.join(rt, "kit.py"), os.path.join(rt, "py", "core", "kit.py")):
        if os.path.isfile(p):
            return p
    return None


def _runtime_root(rt):
    """kit.py 所在的 runtime 根（用于 K.runtime_dir，让 kit.find_runtime_file 找到领域包）。"""
    if os.path.basename(rt) == "core" and os.path.basename(os.path.dirname(rt)) == "py":
        return os.path.dirname(os.path.dirname(rt))
    return rt


def _module_dirs(rt):
    """按优先级列候选目录：领域包 → py 根 → 旧扁平 rt。"""
    root = _runtime_root(rt)
    dirs = []
    for base in (root, rt):
        for d in _DOMAINS:
            dirs.append(os.path.join(base, "py", d))
        dirs.append(os.path.join(base, "py"))
    # 旧扁平布局的真实模块（§4.7 未收录：generator / uv_tools）就在 runtime 根下。
    dirs.append(root)
    dirs.append(rt)
    seen, out = set(), []
    for d in dirs:
        if d not in seen:
            seen.add(d)
            out.append(d)
    return out


def _find_module(name, rt):
    """按模块名找 .py：runtime/py/<领域>/name.py 优先，旧扁平 runtime/name.py 兜底。"""
    n = str(name or "").strip()
    if not n or any(ch not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_" for ch in n):
        return None
    for d in _module_dirs(rt):
        p = os.path.join(d, n + ".py")
        if os.path.isfile(p):
            return p
    return None


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
    """建内核 → 注入共享内核 kit → 按名加载模块；返回命名空间（含 K 与 kapi）。

    modules 里的名字保持与 preload 契约一致（例如 "vehicle" / "qc"），不需要带路径；
    runtime_dir 可传 runtime 根、py/core，或旧布局的 runtime 目录。
    """
    rt = runtime_dir or RT
    K = sys.modules.get("dsh_rt_kernel")
    if K is None:
        K = types.ModuleType("dsh_rt_kernel")
        sys.modules["dsh_rt_kernel"] = K
    _install_kernel_getattr(K)
    # T8：kit.find_runtime_file 依赖 K.runtime_dir；old bootstrap 没设过。
    K.runtime_dir = _runtime_root(rt)
    if getattr(K, "dsh_kit", None) is None:
        _exec(_kit_file(rt), {"K": K, "__name__": "kit", "__file__": _kit_file(rt)}, "kit")
    ns = {"K": K}
    # v2.6.3（T8 回归修复）：每个模块必须 exec 到自己的命名空间。
    # 旧实现把所有模块塞进同一个 globals，而各模块都定义 _num/_r/_j/_path 等私有 helper，
    # 后加载的模块会把前面的顶掉（实测 assembly 的 _num(v) 被 mesh_fix 的 _num(v,name,...) 覆盖，
    # asm_export 直接报 TypeError: _num() missing 1 required positional argument: 'name'）。
    # 与在线 kernel.preloadChunk 的约定一致：私有名不外泄，只把公开名合并回共享 ns（兼容旧脚本）。
    for name in modules:
        p = _find_module(name, rt)
        if p is None:
            raise RuntimeError("找不到模块 %r（查找目录：%s）—— 检查模块名或 runtime_dir"
                               % (name, os.pathsep.join(_module_dirs(rt))))
        mod_ns = {"K": K, "__name__": name, "__file__": p}
        _exec(p, mod_ns, name)
        ns.update({k: v for k, v in mod_ns.items() if not k.startswith("_")})
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
