# -*- coding: utf-8 -*-
"""上游整合 A5 管线与单发 @引用（原 upstream_integration_selftest.py 拆分）.

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


def t_pipeline():
    # S5 流水线：工件注册表 + @引用 + 逐步回执 + 失败即停（让 op 真正配合）
    p = getattr(K, "dsh_pipe_api", None)  # noqa: F821
    if p is None:
        raise RuntimeError("模块没注入：K.dsh_pipe_api（preload 里加 pipeline）")
    st = p("selftest", {})
    chk("流水线自检（工件存取 / @引用含点路径 / 嵌套 / 失败即停）", bool(st.get("ok")), st.get("evidence"))
    p("clear", {})
    p("put", {"name": "nums", "value": {"a": [1, 2, 3], "s": "hi"}})
    got = p("get", {"name": "nums", "path": "a.2"})
    chk("点路径取值（a.2 -> 3）", got.get("value") == 3, got.get("value"))
    # 真链：第 3 步**消费**第 2 步的工件（value 写成 "@n"），第 4 步再取回来核对
    r = p("run", {"steps": [
        {"api": "clearance", "op": "help", "args": {}, "out": "@h"},
        {"api": "pipe", "op": "put", "args": {"name": "n", "value": 7}, "out": "@p1"},
        {"api": "pipe", "op": "put", "args": {"name": "m", "value": "@n"}, "out": "@p2"},
        {"api": "pipe", "op": "get", "args": {"name": "m"}, "out": "@g2"},
    ]})
    chk("四步链跑通且逐步都有回执", bool(r.get("ok")) and r.get("step_count") == 4,
        {"err": str(r.get("error"))[:120], "steps": [(s.get("op"), s.get("ok")) for s in (r.get("steps") or [])]})
    chk("产出按 out= 存成工件", set(["h", "p1", "p2", "g2"]).issubset(set(r.get("artifacts") or [])),
        r.get("artifacts"))
    chk("@引用传到真实调用里（value\"@n\" -> 7）", p("get", {"name": "m"}).get("value") == 7,
        p("get", {"name": "m"}).get("value"))
    bad = p("run", {"steps": [{"api": "clearance", "op": "help", "args": {"x": "@nope"}}]})
    chk("引用不存在的工件 = 失败即停（不静默）", bad.get("ok") is False and bad.get("failed_steps") == [0],
        bad.get("failed_steps"))
    p("clear", {})


def t_refs_single_op():
    # S5-b：**单发 op** 也能用 @引用（不只是 pipe_run）—— 工件由 pipe_put 存，单步调用时解析
    p = getattr(K, "dsh_pipe_api", None)  # noqa: F821
    v = getattr(K, "dsh_vehicle_api", None)  # noqa: F821
    if p is None or v is None:
        raise RuntimeError("需要 pipe + vehicle 模块")
    p("clear", {})
    sts = [{"x_mm": float(x), "z_top_mm": float(z), "z_bottom_mm": 110.0, "half_w_mm": 700.0}
           for (x, z) in ((0, 400), (1000, 480), (2000, 700), (3000, 460), (4300, 400))]
    p("put", {"name": "zzsts", "value": sts})
    r = v("loft", {"stations": "@zzsts", "name": PREFIX + "refshell", "subsurf": 1})
    chk("单发 op 的 @引用被解析（stations=\"@zzsts\" 建出壳）",
        bool(r.get("ok")) and int(r.get("stations_used") or 0) == 5, r.get("stations_used"))
    bad = v("loft", {"stations": "@no_such_artifact"})
    chk("单发 op 引用不存在 ⇒ 可读报错（列出已有工件）",
        bad.get("ok") is False and "工件" in str(bad.get("error") or ""), str(bad.get("error"))[:80])
    p("clear", {})


if __name__ == "__main__":
    U.run([t_pipeline, t_refs_single_op], "上游整合 A5 管线与单发 @引用（原 upstream_integration_selftest.py 拆分）")
