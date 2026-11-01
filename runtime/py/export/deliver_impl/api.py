# -*- coding: utf-8 -*-
"""Split from deliver.py; behavior preserved by the facade."""
from .common import *  # noqa: F401,F403
from .export import deliver_export
from .verify import d_dispatch, deliver_help, deliver_verify

import sys as _sys
_dv_K = _sys.modules.get("dsh_rt_kernel")
def d_selftest():
    # 交付自检：临时立方体 -> 导出 OBJ -> 落盘校验 -> verify 对 md5
    import json

    def _d(x):
        return json.loads(x) if isinstance(x, str) else x

    import bpy
    import bmesh as _bm
    import os
    import tempfile
    ev = {}
    nm = "__dsh_dv_cube"
    d = None
    try:
        me = bpy.data.meshes.new(nm)
        ob = bpy.data.objects.new(nm, me)
        bpy.context.scene.collection.objects.link(ob)
        bm = _bm.new()
        _bm.ops.create_cube(bm, size=1.0)
        bm.to_mesh(me)
        bm.free()
        d = tempfile.mkdtemp(prefix="dsh_deliver_")
        r = _d(deliver_export(dir=d, objects=[nm], name="selftest", fmt="obj"))
        ev["export_ok"] = isinstance(r, dict) and r.get("ok") is not False
        files = sorted(os.listdir(d)) if os.path.isdir(d) else []
        ev["obj_written"] = any(f.endswith(".obj") for f in files)
        v = _d(deliver_verify(path_or_dir=d, manifest="auto"))
        ev["verify_ok"] = isinstance(v, dict) and v.get("ok") is not False
        return _KIT.j({"ok": all(x is True for x in ev.values()), "evidence": ev, "files": files[:8], "dir": d})
    except Exception as e:
        return _KIT.j({"ok": False, "evidence": ev, "error": "%s: %s" % (type(e).__name__, str(e)[:200]), "dir": d})
    finally:
        o = bpy.data.objects.get(nm)
        if o is not None:
            me2 = o.data
            bpy.data.objects.remove(o, do_unlink=True)
            if me2 is not None and me2.users == 0:
                bpy.data.meshes.remove(me2, do_unlink=True)



if _dv_K is not None:
    _dv_K.dsh_deliver_api = _KIT.Api({"version": DELIVER_VERSION, "dispatch": _KIT.wrap_dispatch(d_dispatch, {"selftest": d_selftest}),
                          "export": deliver_export, "verify": deliver_verify, "help": deliver_help,
        "selftest": d_selftest})

# ---- v0.9.1（93-B1/B2）：API 可调用化（换成 dict 子类实例，返回已解析对象）----
# 背景：K.dsh_x_api 原来是普通 dict → 进程内 api(args) 报 TypeError: 'dict' object is not callable；
# 且 dispatch 返回 JSON 字符串，调用方还得自己 json.loads。
# 现在：api("op", {…}) 或 api({…}) → dict；api["dispatch"](op, json_str) 仍返回 str（引擎契约不变）。
# 注意：dict 是静态类型，不能对已有实例做 __class__ 赋值（实测 TypeError），所以换成一个新实例。
_DshApi = _KIT.Api  # 共享内核（尾部注册行无需改）
import sys as _sys_api
_K_api = _sys_api.modules.get("dsh_rt_kernel")
if _K_api is not None and isinstance(getattr(_K_api, "dsh_deliver_api", None), dict) \
        and not isinstance(getattr(_K_api, "dsh_deliver_api", None), _DshApi):
    _K_api.dsh_deliver_api = _DshApi(_K_api.dsh_deliver_api)