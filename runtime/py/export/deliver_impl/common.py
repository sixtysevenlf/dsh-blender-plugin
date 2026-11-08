# -*- coding: utf-8 -*-
"""DSH 交付导出（v0.9.0 / #13）—— 把"场景里看着对"变成"文件能被别人打开、且能反推原始尺度"。

API 挂 K.dsh_deliver_api（DELIVER_VERSION = 1），入口：
    blender_inspect(command=""...") / K.dsh_deliver_api["export"](...)
    blender_op 之外也可无头：preload="deliver" 后直接 K.dsh_deliver_api["export"]({...})

能力：
  export(dir, objects, scope, name, fmt, normalize, target_half_extent, per_part_materials,
         manifest, units_note, include_hidden)
      从**求值后的 mesh**（evaluated_depsgraph_get().evaluated_get(ob).to_mesh()，修改器/形态键都算进去）
      取世界坐标三角面 → 写 OBJ（v/vn/f；每个对象一个 `g <对象名>` 和一行 `usemtl <材质名>`，
      共享材质也每对象写一行）
      或二进制 STL（单文件）；per_part_materials=True 时同目录写 `<name>.mtl`（newmtl + Kd）。
      normalize=True → 整模型缩放到"最长边 = 2 × target_half_extent"，并把**缩放系数 / 原 bbox / 原中心**
      写进返回体与 manifest（**归档铁律：必须能反推原始尺度**）。
      manifest=True → 写 `<name>_manifest.json`（逐文件 md5 + 字节数 + 对象数 + 三角面数 + 单位换算块 +
      normalize 记录 + 生成时间 + api 版本）。
      units_note → 按 scene.unit_settings.scale_length 给 mm/m 说明（数值一律带单位）。
  verify(path_or_dir, manifest="auto")
      读回 resulting OBJ/STL 自检：文件存在 / 三角面数与 manifest 一致 / md5 与 manifest 一致 /
      OBJ 的 g 行数与 usemtl 行数均与对象数一致；`usemtl_unique` 只作唯一材质数诊断。
      **逐项 PASS/FAIL + 数字**，绝不只回一句 ok:true。
  help()   速查（含一行典型调用）。

硬规则（外部反馈口径）：
  ① 空产出必须 ok:false —— objects/scope 都给不出、或挑到的对象里 0 个有三角面，一律失败，不许静默成功；
  ② 数值带单位 —— 坐标是「场景单位」，换算块给 m 与 mm；
  ③ 归一化必须可逆 —— scale / center / 原 bbox 三件套齐全，p_orig = p_out / scale + center；
  ④ 不引新依赖 —— 只用 bpy / mathutils / numpy / json / os / struct / hashlib。
"""
import bpy
import hashlib
import json
import os as os
import struct as struct
import time

DELIVER_VERSION = 1
_DV_PRECISION = 5
_DV_FORMATS = ("obj", "stl")
_DV_BOUNDARY = ("导出的是**求值后**的三角面（修改器/形态键已烘焙进几何），法线用几何面法线（每个三角面一条）；"
            "不做 UV / 自定义法线 / 平滑组；坐标轴为 Blender 世界系 Z-up **原样写出**（未做 Y-up 转换）")


import sys as _sys_kit
_KIT = getattr(_sys_kit.modules.get("dsh_rt_kernel"), "dsh_kit", None)
if _KIT is None:
    raise RuntimeError("deliver 需要共享内核 K.dsh_kit（由 KERNEL_BOOTSTRAP 注入）")


def _dv_j(o):
    return json.dumps(o, ensure_ascii=False, default=str)


def _dv_kernel():
    import sys
    return sys.modules.get("dsh_rt_kernel")


def _dv_store():
    """交付侧的轻状态：记住最近一次成功导出（verify 的 manifest='auto' 兜底 + 人读得懂的回执）。"""
    K = _dv_kernel()
    if K is None:
        raise RuntimeError("需要持久内核 K（走 blender_rt_* 通道）")
    if not hasattr(K, "dsh_deliver"):
        K.dsh_deliver = {"exports": [], "last": None}
    st = K.dsh_deliver
    st.setdefault("exports", [])
    st.setdefault("last", None)
    return st


def _dv_now():
    return time.strftime("%Y-%m-%d %H:%M:%S")


def _dv_win(path):
    K = _dv_kernel()
    if K is not None and hasattr(K, "win_path"):
        try:
            return K.win_path(path)
        except Exception:
            pass
    s = str(path)
    if s.startswith("/mnt/") and len(s) > 6:
        return s[5].upper() + ":" + chr(92) + s[7:].replace("/", chr(92))
    return s


def _dv_md5(path):
    h = hashlib.md5()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 16), b""):
            h.update(chunk)
    return h.hexdigest()


def _dv_r(x, n=6):
    try:
        return round(float(x), n)
    except Exception:
        return x


def _dv_safe(name, default="model"):
    """文件名安全化（顺手挡掉 ../ 与绝对路径注入）。"""
    s = "".join((c if (c.isalnum() or c in "-_.") else "_") for c in str(name or ""))
    s = s.strip("._")
    return s or default


def _dv_pick(objects=None, scope=None, include_hidden=False):
    """挑对象：(objs, missing, err, excluded_hidden)。

    objects=[名字] 优先；否则 scope（集合名，含子集合）里的 MESH；再否则全场景 MESH。
    include_hidden=False 时按 hide_render/hide_viewport 过滤（与 audit.py / contract.py 同口径）。
    容错：整个 {"objects":[...]} 包装字典传进来也能用。
    """
    if isinstance(objects, dict):
        w = objects
        objects = w.get("objects") or w.get("names") or w.get("list")
        if scope is None:
            scope = w.get("scope")
    missing = []
    if objects:
        want = [str(x) for x in (objects if isinstance(objects, (list, tuple)) else [objects])]
        got = []
        for n in want:
            o = bpy.data.objects.get(n)
            if o is None:
                missing.append(n)
            else:
                got.append(o)
    elif scope:
        coll = bpy.data.collections.get(str(scope))
        if coll is None:
            return [], [], "集合不存在：%s" % scope, 0
        got = list(coll.all_objects)
    else:
        got = list(bpy.context.scene.objects)
    got = [o for o in got if o.type == "MESH"]
    if include_hidden:
        return got, missing, None, 0
    keep = [o for o in got if not (o.hide_render or o.hide_viewport)]
    return keep, missing, None, len(got) - len(keep)



__all__ = [name for name in list(globals()) if not name.startswith("__")]
