# -*- coding: utf-8 -*-
"""上游整合 A4 材质与渲染守卫（原 upstream_integration_selftest.py 拆分）.

Split from cases/upstream_integration_selftest.py by T11a. The runner injects K;
the shared helper supplies the original chk/api/clean/new_sphere/new_plate names.
"""
from __future__ import annotations

import json
import math

import bmesh
import bpy

import _upstream_common as U

U.bind(K)
U.install(globals())


def t_material():
    """材质链路：展 UV → material_build → apply → scan → bake（AO 128px）→ 断言贴图落盘。"""
    import shutil as _shutil
    import tempfile as _tempfile
    import bmesh as _bm
    me = bpy.data.meshes.new(PREFIX + "mat")
    ob = bpy.data.objects.new(PREFIX + "mat", me)
    bpy.context.scene.collection.objects.link(ob)
    bm = _bm.new()
    _bm.ops.create_cube(bm, size=1.0)
    bm.to_mesh(me)
    bm.free()
    bpy.context.view_layer.objects.active = ob
    ob.select_set(True)
    od = _tempfile.mkdtemp(prefix="dsh-mat-selftest-")
    try:
        r_uv = api("uv")("smart_project", {"objects": [ob.name], "angle_limit": 66})
        chk("材质链路前置：UV 展好", r_uv.get("ok"), r_uv)
        b = api("material")("build", {"name": PREFIX + "M", "preset": "metal_brushed",
                                      "params": {"base_color": [0.4, 0.42, 0.45], "scale": 200}})
        chk("material_build 建出程序化节点图（节点与连线都 >=5）",
            b.get("ok") and (b.get("nodes") or 0) >= 5 and (b.get("links") or 0) >= 5, b)
        a = api("material")("apply", {"material": PREFIX + "M", "objects": [ob.name]})
        chk("material_apply 套到对象", a.get("ok"), a)
        s = api("material")("scan", {"materials": [PREFIX + "M"]})
        row = (s.get("materials") or [{}])[0]
        chk("material_scan 报 procedural 与 needs_uv", bool(row.get("procedural")) and bool(row.get("needs_uv")), row)
        bk = api("material")("bake", {"objects": [ob.name], "bake_type": "AO", "resolution": 128,
                                      "samples": 4, "outdir": od})
        imgs = ((bk.get("objects") or [{}])[0].get("images") or [{}])
        chk("material_bake 烘出 AO 贴图并落盘（bytes>0）",
            bk.get("ok") and (imgs[0].get("bytes") or 0) > 0, {"ok": bk.get("ok"), "img": imgs[0]})
        chk("未知 preset 要回允许列表", api("material")("build", {"name": PREFIX + "X", "preset": "nope"}).get("ok") is False)
        chk("bake_type 写错要回允许列表",
            "允许" in json.dumps(api("material")("bake", {"objects": [ob.name], "bake_type": "NOPE"}), ensure_ascii=False))
    finally:
        _shutil.rmtree(od, ignore_errors=True)


def t_render_guard():
    """渲染状态跟踪：install → idle → mark → 磁盘标记为 rendering → clear → 标记消失。
    注意：guard 的 API 挂在 K.dsh_render_guard（名字里没有 _api 后缀，见 render_guard.py 尾部）。"""
    rg = getattr(K, "dsh_render_guard", None)  # noqa: F821
    if rg is None:
        raise RuntimeError("模块没注入：K.dsh_render_guard（preload 里漏了 render_guard？）")
    r = rg("install", {})
    chk("render_guard 安装 4 个 handler", r.get("ok") and r.get("installed")
        and all(int(v) >= 1 for v in (r.get("handlers") or {}).values()), r.get("handlers"))
    st = rg("state", {})
    chk("初始状态 idle（无标记）", st.get("ok") and (st.get("local") or {}).get("state") == "idle", st.get("local"))
    rg("mark", {})
    st2 = rg("state", {})
    chk("mark 后标记文件为 rendering", (st2.get("flag") or {}).get("state") == "rendering", st2.get("flag"))
    rg("clear", {"why": "selftest"})
    st3 = rg("state", {})
    chk("clear 后标记消失且 state 归 idle",
        st3.get("flag") is None and (st3.get("local") or {}).get("state") == "idle", st3.get("local"))


if __name__ == "__main__":
    U.run([t_material, t_render_guard], "上游整合 A4 材质与渲染守卫（原 upstream_integration_selftest.py 拆分）")
