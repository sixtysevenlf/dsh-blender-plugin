# -*- coding: utf-8 -*-
"""DSH 交付导出（v0.9.0 / #13）—— 把"场景里看着对"变成"文件能被别人打开、且能反推原始尺度"。

API 挂 K.dsh_deliver_api（DELIVER_VERSION = 1），入口：
    blender_rt_cmd(name="...") / K.dsh_deliver_api["export"](...)
    blender_rt_plan 之外也可无头：preload="deliver" 后直接 K.dsh_deliver_api["export"]({...})

能力：
  export(dir, objects, scope, name, fmt, normalize, target_half_extent, per_part_materials,
         manifest, units_note, include_hidden)
      从**求值后的 mesh**（evaluated_depsgraph_get().evaluated_get(ob).to_mesh()，修改器/形态键都算进去）
      取世界坐标三角面 → 写 OBJ（v/vn/f；每个对象一个 `g <对象名>`，材质 `usemtl <材质名>`）
      或二进制 STL（单文件）；per_part_materials=True 时同目录写 `<name>.mtl`（newmtl + Kd）。
      normalize=True → 整模型缩放到"最长边 = 2 × target_half_extent"，并把**缩放系数 / 原 bbox / 原中心**
      写进返回体与 manifest（**归档铁律：必须能反推原始尺度**）。
      manifest=True → 写 `<name>_manifest.json`（逐文件 md5 + 字节数 + 对象数 + 三角面数 + 单位换算块 +
      normalize 记录 + 生成时间 + api 版本）。
      units_note → 按 scene.unit_settings.scale_length 给 mm/m 说明（数值一律带单位）。
  verify(path_or_dir, manifest="auto")
      读回 resulting OBJ/STL 自检：文件存在 / 三角面数与 manifest 一致 / md5 与 manifest 一致 /
      OBJ 的 g 与 usemtl 数量与对象数一致。**逐项 PASS/FAIL + 数字**，绝不只回一句 ok:true。
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
import os
import struct
import time

DELIVER_VERSION = 1
_DV_PRECISION = 5
_DV_FORMATS = ("obj", "stl")
_DV_BOUNDARY = ("导出的是**求值后**的三角面（修改器/形态键已烘焙进几何），法线用几何面法线（每个三角面一条）；"
            "不做 UV / 自定义法线 / 平滑组；坐标轴为 Blender 世界系 Z-up **原样写出**（未做 Y-up 转换）")


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


# ---------------------------------------------------------------- 求值后的三角面

def _dv_eval_part(ob, dg):
    """求值后的 mesh → 世界坐标三角面。返回 (part | None, err)。

    part = {"name","verts":[[x,y,z]...],"faces":[[i,j,k]...],"normals":[[x,y,z]...],
            "triangles","degenerate","src_verts","src_polys"}
    顶点按 5 位小数去重（与上游 obj-mtl 的 _DV_PRECISION 一致），顺序由 np.unique 字典序定死 → 可复现。
    """
    import numpy as np
    obe = None
    try:
        obe = ob.evaluated_get(dg)
        me = obe.to_mesh()
    except Exception as e:
        return None, "%s: %s" % (type(e).__name__, e)
    try:
        if me is None:
            return None, "to_mesh() 返回 None"
        me.calc_loop_triangles()
        n = len(me.loop_triangles)
        if n == 0:
            return {"name": ob.name, "verts": [], "faces": [], "normals": [], "triangles": 0,
                    "degenerate": 0, "src_verts": len(me.vertices), "src_polys": len(me.polygons)}, None
        mw = np.array(obe.matrix_world, dtype=np.float64)
        v = np.empty(len(me.vertices) * 3, dtype=np.float64)
        me.vertices.foreach_get("co", v)
        v = v.reshape(-1, 3)
        idx = np.empty(n * 3, dtype=np.int32)
        me.loop_triangles.foreach_get("vertices", idx)
        tri = v[idx.reshape(-1, 3)] @ mw[:3, :3].T + mw[:3, 3]        # (n,3,3) 世界坐标
        q = np.round(tri.reshape(-1, 3), _DV_PRECISION)
        uniq, inv = np.unique(q, axis=0, return_inverse=True)
        inv = np.asarray(inv).reshape(-1)
        e1 = tri[:, 1, :] - tri[:, 0, :]
        e2 = tri[:, 2, :] - tri[:, 0, :]
        nrm = np.cross(e1, e2)
        ln = np.linalg.norm(nrm, axis=1)
        degen = int((ln < 1e-12).sum())
        safe = np.where(ln < 1e-12, 1.0, ln)
        nrm = nrm / safe[:, None]
        nrm[ln < 1e-12] = (0.0, 0.0, 1.0)                             # 退化面给个确定性的占位法线
        return {"name": ob.name, "verts": uniq.tolist(), "faces": inv.reshape(-1, 3).tolist(),
                "normals": np.round(nrm, _DV_PRECISION).tolist(), "triangles": int(n), "degenerate": degen,
                "src_verts": len(me.vertices), "src_polys": len(me.polygons)}, None
    except Exception as e:
        return None, "%s: %s" % (type(e).__name__, e)
    finally:
        try:
            if obe is not None:
                obe.to_mesh_clear()
        except Exception:
            pass


def _dv_bbox(all_verts):
    lo = [1e30, 1e30, 1e30]
    hi = [-1e30, -1e30, -1e30]
    for p in all_verts:
        for i in range(3):
            if p[i] < lo[i]:
                lo[i] = p[i]
            if p[i] > hi[i]:
                hi[i] = p[i]
    if lo[0] > 1e29:
        return None, None
    return lo, hi


# ---------------------------------------------------------------- 材质 / 基础色

def _dv_principled(mat):
    try:
        nt = mat.node_tree
        if nt is None:
            return None
        for n in nt.nodes:
            if n.type == "BSDF_PRINCIPLED":
                return n
    except Exception:
        pass
    return None


def _dv_grey_seed(text):
    """确定性灰阶：由名字 md5 派生（同一对象每次导出同色，便于归档比对）。"""
    h = hashlib.md5(str(text).encode("utf-8")).hexdigest()
    v = int(h[:2], 16) / 255.0
    g = round(0.35 + v * 0.45, 4)      # 0.35 ~ 0.80，避开纯黑纯白
    return [g, g, g]


def _dv_material_of(ob):
    """对象 → (材质名, Kd, 来源, 粗糙度, 金属度, 是否"颜色是猜的")。

    来源优先级（如实回传，不假装）：
      ① principled_base_color —— Principled BSDF 的 Base Color 且**未被连线**（连了线就不是常量真值）
      ② viewport_diffuse      —— 材质的 viewport 显示色 diffuse_color（是材质自己的值，但不是 BSDF 真值）
      ③ deterministic_grey    —— 没有材质槽 / 颜色读不出来 → 由名字 md5 派生的灰阶（= 猜的）
    """
    mat = None
    try:
        if ob.data is not None and len(ob.data.materials) > 0:
            mat = ob.data.materials[0]
    except Exception:
        mat = None
    if mat is None:
        return None, _dv_grey_seed(ob.name), "deterministic_grey", None, None, True
    pn = _dv_principled(mat)
    if pn is not None:
        try:
            inp = pn.inputs.get("Base Color")
            if inp is not None and not inp.is_linked:
                c = list(inp.default_value)
                rough = None
                metal = None
                try:
                    ri = pn.inputs.get("Roughness")
                    if ri is not None and not ri.is_linked:
                        rough = float(ri.default_value)
                    mi = pn.inputs.get("Metallic")
                    if mi is not None and not mi.is_linked:
                        metal = float(mi.default_value)
                except Exception:
                    pass
                return mat.name, [min(1.0, max(0.0, float(c[0]))), min(1.0, max(0.0, float(c[1]))),
                                  min(1.0, max(0.0, float(c[2])))], "principled_base_color", rough, metal, False
        except Exception:
            pass
    try:
        c = list(mat.diffuse_color)
        return mat.name, [min(1.0, max(0.0, float(c[0]))), min(1.0, max(0.0, float(c[1]))),
                          min(1.0, max(0.0, float(c[2])))], "viewport_diffuse", None, None, False
    except Exception:
        return mat.name, _dv_grey_seed(mat.name), "deterministic_grey", None, None, True


def _dv_mat_ref(name, used):
    """材质引用名（MTL / usemtl 用）：只把空白换成下划线，重名加后缀，并记回映射。"""
    base = "".join((c if not c.isspace() else "_") for c in str(name or "mat"))
    ref = base
    i = 2
    while ref in used:
        ref = "%s_%d" % (base, i)
        i += 1
    used.add(ref)
    return ref


# ---------------------------------------------------------------- 写文件

def _dv_write_obj(path, parts, header_lines, mtl_basename):
    """写 OBJ：每个对象一个 `g <对象名>` + `usemtl <材质引用>`，v/vn/f（三角面，面法线）。"""
    out = list(header_lines)
    if mtl_basename:
        out.append("mtllib " + mtl_basename)
    v_base, vn_base = 0, 0
    for p in parts:
        out.append("g " + p["name"])
        out.append("usemtl " + p["material_ref"])
        for c in p["verts"]:
            out.append("v %.5f %.5f %.5f" % (c[0], c[1], c[2]))
        nidx, nseen, nlist = [], {}, []
        for n in p["normals"]:
            k = "%.5f|%.5f|%.5f" % (n[0], n[1], n[2])
            i = nseen.get(k)
            if i is None:
                nlist.append(n)
                i = len(nlist) - 1
                nseen[k] = i
            nidx.append(i)
        for n in nlist:
            out.append("vn %.5f %.5f %.5f" % (n[0], n[1], n[2]))
        for f, ni in zip(p["faces"], nidx):
            out.append("f %d//%d %d//%d %d//%d" % (v_base + f[0] + 1, vn_base + ni + 1,
                                                   v_base + f[1] + 1, vn_base + ni + 1,
                                                   v_base + f[2] + 1, vn_base + ni + 1))
        v_base += len(p["verts"])
        vn_base += len(nlist)
        p["vn_written"] = len(nlist)
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        f.write("\n".join(out) + "\n")
    return {"vertices": v_base, "normals": vn_base, "groups": len(parts),
            "usemtl": len(set(p["material_ref"] for p in parts)), "lines": len(out) + 1}


def _dv_write_mtl(path, mats, header_lines):
    """写 MTL：newmtl + Kd（基础色）+ 可选 Pr/Pm；取不到就落确定性灰阶。"""
    out = list(header_lines)
    for m in mats:
        out.append("")
        out.append("newmtl " + m["ref"])
        out.append("Ka 0.000000 0.000000 0.000000")
        out.append("Kd %.6f %.6f %.6f" % (m["kd"][0], m["kd"][1], m["kd"][2]))
        if m.get("rough") is not None:
            out.append("Pr %.6f" % float(m["rough"]))
        if m.get("metal") is not None:
            out.append("Pm %.6f" % float(m["metal"]))
        ks = 0.9 if (m.get("metal") or 0.0) > 0.5 else 0.2
        out.append("Ks %.6f %.6f %.6f" % (ks, ks, ks))
        out.append("Ns %d" % max(1, int((1.0 - (m.get("rough") if m.get("rough") is not None else 0.5)) ** 2 * 900 + 1)))
        out.append("d 1.0")
        out.append("illum 2")
        out.append("# src_material=%s source=%s objects=%s" % (m.get("src"), m.get("source"), ",".join(m.get("objects") or [])))
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        f.write("\n".join(out) + "\n")
    return {"materials": len(mats), "lines": len(out)}


def _dv_write_stl(path, parts, name):
    """二进制 STL：80 字节头 + uint32 三角数 + 每面 (法线3f + 顶点9f + uint16 attr) = 50 字节。"""
    tris = 0
    for p in parts:
        tris += len(p["faces"])
    head = ("DSH deliver %s (%d tris)" % (name, tris)).encode("utf-8")[:80]
    with open(path, "wb") as f:
        f.write(head + b" " * (80 - len(head)))
        f.write(struct.pack("<I", tris))
        for p in parts:
            verts = p["verts"]
            for f_i, face in enumerate(p["faces"]):
                a, b, c = verts[face[0]], verts[face[1]], verts[face[2]]
                n = p["normals"][f_i] if f_i < len(p["normals"]) else (0.0, 0.0, 1.0)
                f.write(struct.pack("<12fH", n[0], n[1], n[2], a[0], a[1], a[2],
                                    b[0], b[1], b[2], c[0], c[1], c[2], 0))
    return {"triangles": tris, "bytes": os.path.getsize(path)}


def _dv_write_text(path, text):
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        f.write(text)


# ---------------------------------------------------------------- 单位

def _dv_units_info():
    """场景单位换算块：**数值一律带单位**（1 场景单位 = ? m / ? mm）。"""
    us = getattr(bpy.context.scene, "unit_settings", None)
    m = 1.0
    try:
        m = float(us.scale_length) or 1.0
    except Exception:
        m = 1.0
    mm = m * 1000.0
    sysname = str(getattr(us, "system", "") or "")
    lunit = str(getattr(us, "length_unit", "") or "")
    return {"scale_length": _dv_r(m), "meters_per_unit": _dv_r(m), "millimeters_per_unit": _dv_r(mm),
            "unit_system": sysname, "length_unit": lunit,
            "note": "1 场景单位 = %.6g m = %.6g mm（unit_settings.scale_length = %.6g，system=%s/%s）；"
                    "本文件坐标以「场景单位」写出" % (m, mm, m, sysname, lunit)}


# ---------------------------------------------------------------- 导出

def deliver_export(dir=None, objects=None, scope=None, name="model", fmt="obj", normalize=True,
                   target_half_extent=1.0, per_part_materials=True, manifest=True, units_note=True,
                   include_hidden=False):
    """把选中的 mesh 导出成可交付的 OBJ(+MTL) / STL，并写 manifest（含 md5 / 面数 / 单位 / 归一化记录）。

    典型一行：K.dsh_deliver_api["export"]({"dir": "D:/Blender/house", "scope": "House", "name": "house"})
    空产出（挑不到对象 / 全都没有三角面）→ ok:false，绝不静默成功。
    """
    t0 = time.perf_counter()
    f = str(fmt or "obj").strip().lower()
    if f not in _DV_FORMATS:
        return _dv_j({"ok": False, "error": "非法 fmt=%s" % fmt, "allowed": list(_DV_FORMATS),
                   "hint": "fmt='obj'（默认，可带 .mtl）或 fmt='stl'（二进制单文件）"})
    try:
        the = float(target_half_extent)
    except Exception:
        return _dv_j({"ok": False, "error": "target_half_extent 必须是数字", "given": target_half_extent})
    if the <= 0:
        return _dv_j({"ok": False, "error": "target_half_extent 必须 > 0（最长边 = 2 × target_half_extent）", "given": the})
    nm, changed_name = _dv_safe(name), None
    if nm != str(name or ""):
        changed_name = "name 已安全化：%r → %r" % (name, nm)
    out_dir = dir
    if not out_dir:
        K = _dv_kernel()
        base = getattr(K, "out_dir", None) if K is not None else None
        out_dir = os.path.join(base or os.path.expanduser("~"), "deliver")
    out_dir = _dv_win(out_dir)
    try:
        os.makedirs(out_dir, exist_ok=True)
    except Exception as e:
        return _dv_j({"ok": False, "error": "产物目录建不出来（%s）：%s" % (type(e).__name__, e), "dir": out_dir})

    warn = []
    if changed_name:
        warn.append(changed_name)
    objs, missing, err, excluded = _dv_pick(objects, scope, include_hidden)
    if err:
        return _dv_j({"ok": False, "error": err, "dir": out_dir, "objects": objects, "scope": scope})
    if missing:
        warn.append("对象不存在（已跳过）：%s" % ", ".join(missing))
    if not objs:
        return _dv_j({"ok": False, "error": "挑到 0 个 mesh：objects/scope 都没给出有效对象，或全部被隐藏过滤（include_hidden=False）",
                   "objects": objects, "scope": scope, "excluded_hidden": excluded, "dir": out_dir})

    dg = bpy.context.evaluated_depsgraph_get()
    parts, failed, empty = [], [], []
    for ob in objs:
        part, perr = _dv_eval_part(ob, dg)
        if part is None:
            failed.append({"name": ob.name, "why": perr})
            continue
        if part["triangles"] == 0:
            empty.append(ob.name)
            continue
        if part["degenerate"]:
            warn.append("%s：%d 个退化三角面（法线给了确定性占位 (0,0,1)）" % (ob.name, part["degenerate"]))
        parts.append(part)
    if failed:
        warn.append("求值失败（已跳过）：%s" % _dv_j(failed))
    if empty:
        warn.append("无三角面（已跳过）：%s" % ", ".join(empty))
    if not parts:
        return _dv_j({"ok": False, "error": "挑到的对象里 0 个有三角面 —— 空产出不接受（求值失败/空网格）",
                   "objects": [o.name for o in objs], "failed": failed, "empty": empty, "dir": out_dir})

    # ---- 材质（每个对象一条 usemtl；同材质对象共用同一条 newmtl）+ 基础色
    mats, by_src, guessed, ref_by_key = [], {}, [], {}
    for p in parts:
        ob = bpy.data.objects.get(p["name"])
        if ob is not None:
            mname, kd, src, rough, metal, is_guess = _dv_material_of(ob)
        else:
            mname, kd, src, rough, metal, is_guess = None, _dv_grey_seed(p["name"]), "deterministic_grey", None, None, True
        key = mname if mname else ("__nomat__:" + p["name"])
        ref = ref_by_key.get(key)
        if ref is None:
            ref = _dv_mat_ref(mname if mname else ("mat_" + _dv_safe(p["name"], "part")),
                           set(m["ref"] for m in mats))
            ref_by_key[key] = ref
            mats.append({"ref": ref, "src": mname, "kd": [round(float(kd[0]), 6), round(float(kd[1]), 6), round(float(kd[2]), 6)],
                         "source": src, "rough": None if rough is None else round(float(rough), 6),
                         "metal": None if metal is None else round(float(metal), 6), "objects": [p["name"]]})
        else:
            for m in mats:
                if m["ref"] == ref:
                    m["objects"].append(p["name"])
                    break
        p["material"] = mname
        p["material_ref"] = ref
        p["material_source"] = src
        p["kd"] = [round(float(kd[0]), 6), round(float(kd[1]), 6), round(float(kd[2]), 6)]
        p["guessed"] = bool(is_guess)
        by_src[src] = by_src.get(src, 0) + 1
        if is_guess:
            guessed.append({"object": p["name"], "material": mname, "kd": p["kd"],
                            "why": "对象没有材质槽或颜色读不出来 → 由名字 md5 派生的确定性灰阶（不是真值）"})

    # ---- bbox → normalize（归档铁律：scale/center/原 bbox 三件套必须留）
    all_verts = []
    for p in parts:
        all_verts.extend(p["verts"])
    lo, hi = _dv_bbox(all_verts)
    size = [hi[i] - lo[i] for i in range(3)]
    center = [(lo[i] + hi[i]) * 0.5 for i in range(3)]
    longest = max(size) if size else 0.0
    norm = {"applied": False, "target_half_extent": the,
            "bbox_before_min": [_dv_r(v) for v in lo], "bbox_before_max": [_dv_r(v) for v in hi],
            "bbox_before_size": [_dv_r(v) for v in size], "center": [_dv_r(v) for v in center],
            "scale": 1.0, "center_offset": [0.0, 0.0, 0.0],
            "recover": "原始坐标 p_orig = p_out / scale + center（scale=1、center=0 即未归一化）"}
    if normalize:
        if not (longest > 0):
            return _dv_j({"ok": False, "error": "零尺寸包围盒（几何塌成一点/共面到零）：归一化无从谈起",
                       "bbox_size": [_dv_r(v) for v in size], "dir": out_dir})
        scale = (2.0 * the) / longest
        center = [lo[i] + size[i] * 0.5 for i in range(3)]
        for p in parts:
            p["verts"] = [[(c[0] - center[0]) * scale, (c[1] - center[1]) * scale, (c[2] - center[2]) * scale]
                          for c in p["verts"]]
        lo2, hi2 = _dv_bbox([c for p in parts for c in p["verts"]])
        norm.update({"applied": True, "scale": _dv_r(scale, 9), "center": [_dv_r(v) for v in center],
                     "center_offset": [_dv_r(-v) for v in center],
                     "bbox_after_min": [_dv_r(v) for v in lo2], "bbox_after_max": [_dv_r(v) for v in hi2],
                     "bbox_after_size": [_dv_r(hi2[i] - lo2[i]) for i in range(3)],
                     "formula": "p_out = (p_orig - center) * scale",
                     "recover": "原始坐标 p_orig = p_out / scale + center（scale=%.9g）" % scale})
    else:
        norm.update({"bbox_after_min": norm["bbox_before_min"], "bbox_after_max": norm["bbox_before_max"],
                     "bbox_after_size": norm["bbox_before_size"],
                     "note": "normalize=False：文件里的尺度即原始场景尺度（scale=1、center=(0,0,0)）"})

    units = _dv_units_info() if units_note else {"suppressed": True, "reason": "units_note=False（归档铁律要求数值带单位，verify 会判 FAIL）"}

    n_tri = sum(len(p["faces"]) for p in parts)
    n_v = sum(len(p["verts"]) for p in parts)
    head = ["# DSH deliver v%d  name=%s  fmt=%s" % (DELIVER_VERSION, nm, f),
            "# objects=%d triangles=%d vertices=%d" % (len(parts), n_tri, n_v)]
    if units_note:
        head.append("# units: " + units["note"])
    if norm.get("applied"):
        head.append("# normalize: scale=%.9g center=(%.6g, %.6g, %.6g) target_half_extent=%.6g  →  p_orig = p_out/scale + center"
                    % (norm["scale"], center[0], center[1], center[2], the))
    else:
        head.append("# normalize: 未做（文件尺度 = 原始场景尺度）")
    head.append("# axes: Blender 世界系 Z-up 原样写出（未做 Y-up 转换）；法线为几何面法线")

    files = {}
    try:
        if f == "obj":
            obj_path = os.path.join(out_dir, nm + ".obj")
            mtl_name = (nm + ".mtl") if per_part_materials else None
            info = _dv_write_obj(obj_path, parts, head, mtl_name)
            files["obj"] = {"role": "obj", "path": _dv_win(obj_path), "bytes": os.path.getsize(obj_path),
                            "md5": _dv_md5(obj_path), "triangles": n_tri, "vertices": info["vertices"],
                            "normals": info["normals"], "groups": info["groups"], "usemtl": info["usemtl"]}
            if mtl_name:
                mtl_path = os.path.join(out_dir, mtl_name)
                minfo = _dv_write_mtl(mtl_path, mats, ["# DSH deliver v%d  name=%s" % (DELIVER_VERSION, nm),
                                                    "# 对象 → 材质映射见 %s_manifest.json" % nm])
                files["mtl"] = {"role": "mtl", "path": _dv_win(mtl_path), "bytes": os.path.getsize(mtl_path),
                                "md5": _dv_md5(mtl_path), "materials": minfo["materials"]}
        else:
            stl_path = os.path.join(out_dir, nm + ".stl")
            sinfo = _dv_write_stl(stl_path, parts, nm)
            files["stl"] = {"role": "stl", "path": _dv_win(stl_path), "bytes": sinfo["bytes"], "md5": _dv_md5(stl_path),
                            "triangles": sinfo["triangles"], "groups": len(parts)}
            if per_part_materials:
                warn.append("fmt='stl'：STL 不携带材质（per_part_materials 对 STL 无效，已忽略）")
    except Exception as e:
        return _dv_j({"ok": False, "error": "写文件失败（%s）：%s" % (type(e).__name__, e), "dir": out_dir})

    objects_rec = [{"name": p["name"], "triangles": len(p["faces"]), "vertices": len(p["verts"]),
                    "material": p.get("material"), "material_ref": p.get("material_ref"),
                    "material_source": p.get("material_source"), "kd": p.get("kd"), "guessed": p.get("guessed")}
                   for p in parts]
    counts = {"objects": len(parts), "triangles": n_tri, "vertices": n_v, "materials": len(mats),
              "groups": len(parts), "usemtl": len(set(p["material_ref"] for p in parts))}
    result = {"ok": True, "dir": out_dir, "name": nm, "fmt": f, "files": files, "counts": counts,
              "objects": objects_rec, "materials": mats, "normalize": norm, "units": units,
              "guessed_colors": guessed, "material_color_sources": by_src, "excluded_hidden": excluded,
              "skipped_empty": empty, "missing_objects": missing, "warnings": warn,
              "boundary": _DV_BOUNDARY, "ms": int((time.perf_counter() - t0) * 1000)}

    if manifest:
        man = {"tool": "dsh-deliver", "api_version": DELIVER_VERSION, "generated_at": _dv_now(),
               "name": nm, "fmt": f, "dir": out_dir,
               "blender": bpy.app.version_string, "scene": bpy.context.scene.name if bpy.context.scene else None,
               "blend_filepath": bpy.data.filepath or None,
               "files": [dict(v, name=os.path.basename(str(v["path"]))) for v in files.values()],
               "objects": objects_rec, "counts": counts,
               "materials": [{"ref": m["ref"], "src": m["src"], "kd": m["kd"], "source": m["source"],
                              "rough": m["rough"], "metal": m["metal"], "objects": m["objects"]} for m in mats],
               "units": units, "normalize": norm, "guessed_colors": guessed,
               "material_color_sources": by_src, "excluded_hidden": excluded,
               "warnings": warn, "boundary": _DV_BOUNDARY,
               "recovery_rule": "归档必须能反推原始尺度：p_orig = p_out / normalize.scale + normalize.center"}
        man_path = os.path.join(out_dir, nm + "_manifest.json")
        try:
            _dv_write_text(man_path, json.dumps(man, ensure_ascii=False, indent=2, default=str) + "\n")
        except Exception as e:
            result["ok"] = False
            result["error"] = "几何已写出，但 manifest 写失败（%s）：%s" % (type(e).__name__, e)
            return _dv_j(result)
        result["manifest"] = {"path": _dv_win(man_path), "bytes": os.path.getsize(man_path), "md5": _dv_md5(man_path)}
    else:
        warn.append("manifest=False：没有清单文件（verify 只能用内存里最近一次导出的记录做参考）")

    st = _dv_store()
    st["last"] = {"dir": out_dir, "name": nm, "fmt": f, "manifest": result.get("manifest", {}).get("path"),
                  "files": [dict(v, name=os.path.basename(str(v["path"]))) for v in files.values()],
                  "counts": counts, "normalize": norm, "units": units, "t": _dv_now(),
                  "authority": "内存记录（K.dsh_deliver.last）—— 只作参考；权威是磁盘上的 <name>_manifest.json"}
    st["exports"].append({"t": _dv_now(), "dir": out_dir, "name": nm, "fmt": f, "objects": counts["objects"],
                          "triangles": n_tri, "md5": {k: v.get("md5") for k, v in files.items()}})
    del st["exports"][:-50]
    result["hint"] = "交付前先 K.dsh_deliver_api['verify'](%r) 逐项核对（md5 / 面数 / g 与 usemtl 数量）" % out_dir
    return _dv_j(result)


# ---------------------------------------------------------------- 读回自检

def _dv_scan_obj(path):
    """读回 OBJ 自己数：f（三角面）/ g / usemtl / v / vn + **从 v 行重算 bbox**（校验要咬文件本身，不是咬清单）。"""
    n_f = n_g = n_u = n_v = n_vn = 0
    lo = [1e30, 1e30, 1e30]
    hi = [-1e30, -1e30, -1e30]
    with open(path, "r", encoding="utf-8", errors="replace") as fh:
        for line in fh:
            if line.startswith("f "):
                n_f += 1
            elif line.startswith("g "):
                n_g += 1
            elif line.startswith("usemtl "):
                n_u += 1
            elif line.startswith("v "):
                n_v += 1
                try:
                    a = line.split()
                    for i in range(3):
                        x = float(a[i + 1])
                        if x < lo[i]:
                            lo[i] = x
                        if x > hi[i]:
                            hi[i] = x
                except Exception:
                    pass
            elif line.startswith("vn "):
                n_vn += 1
    out = {"triangles": n_f, "groups": n_g, "usemtl": n_u, "vertices": n_v, "normals": n_vn}
    if n_v:
        out["bbox_min"] = [_dv_r(x) for x in lo]
        out["bbox_max"] = [_dv_r(x) for x in hi]
        out["longest_edge"] = _dv_r(max(hi[i] - lo[i] for i in range(3)))
    return out


def _dv_scan_stl(path):
    """读回二进制 STL：80 字节头 + uint32 三角数；顺带从顶点浮点重算 bbox（校验咬文件本身）。"""
    b = os.path.getsize(path)
    lo = [1e30, 1e30, 1e30]
    hi = [-1e30, -1e30, -1e30]
    with open(path, "rb") as fh:
        fh.seek(80)
        raw = fh.read(4)
        if len(raw) == 4:
            n = struct.unpack("<I", raw)[0]
            body = fh.read(50 * n)
            if len(body) == 50 * n:
                for rec in struct.iter_unpack("<12fH", body):
                    for k in range(3):                      # 3 个顶点
                        for i in range(3):                  # xyz
                            x = rec[3 + k * 3 + i]
                            if x < lo[i]:
                                lo[i] = x
                            if x > hi[i]:
                                hi[i] = x
    if len(raw) != 4:
        return {"triangles": None, "bytes": b, "error": "文件短于 84 字节，不是合法二进制 STL"}
    out = {"triangles": int(n), "bytes": b, "expected_bytes": 84 + 50 * int(n)}
    if hi[0] > -1e29:
        out["bbox_min"] = [_dv_r(x) for x in lo]
        out["bbox_max"] = [_dv_r(x) for x in hi]
        out["longest_edge"] = _dv_r(max(hi[i] - lo[i] for i in range(3)))
    return out


def deliver_verify(path_or_dir=None, manifest="auto"):
    """读回产物逐项自检。返回 {ok, checks:[{name, ok, detail, ...数字}], passed, failed}。

    检查项：清单可用 / 文件存在且有字节 / md5 与清单一致 / 三角面数与清单一致 /
    OBJ 的 g 与 usemtl 数量与清单一致 / 单位块带单位 / 归一化可反推（scale+center+原 bbox）。
    `manifest="auto"` → 目录里找 `<name>_manifest.json`（多个则用最近一次导出的那个）。
    """
    st = None
    try:
        st = _dv_store()
    except Exception:
        st = None
    p = path_or_dir
    if not p:
        last = (st or {}).get("last")
        if not last:
            return _dv_j({"ok": False, "error": "没给 path_or_dir，也没有可参考的最近导出记录", "checks": []})
        p = last["dir"]
    p = _dv_win(p)
    if not os.path.exists(p):
        return _dv_j({"ok": False, "error": "路径不存在：%s" % p, "checks": [
            {"name": "path_exists", "ok": False, "detail": "路径不存在: %s" % p}]})
    target_file = None
    if os.path.isfile(p):
        target_file = p
        d = os.path.dirname(p)
        if os.path.basename(p).endswith("_manifest.json"):
            if manifest == "auto":
                manifest = p
    else:
        d = p
    checks = []

    def chk(name, ok, detail="", **extra):
        rec = {"name": name, "ok": bool(ok), "detail": detail}
        rec.update(extra)
        checks.append(rec)
        return bool(ok)

    # ---- 1. 清单
    man, man_path, man_src = None, None, None
    if manifest and manifest != "auto" and str(manifest) != "memory":
        man_path = _dv_win(manifest)
        if os.path.isfile(man_path):
            try:
                man = json.loads(open(man_path, encoding="utf-8").read())
                man_src = "explicit"
            except Exception as e:
                chk("manifest_loadable", False, "清单解析失败（%s）：%s" % (type(e).__name__, e), path=man_path)
        else:
            chk("manifest_exists", False, "指定的清单不存在：%s" % man_path, path=man_path)
    else:
        cands = []
        try:
            cands = sorted([os.path.join(d, n) for n in os.listdir(d) if n.endswith("_manifest.json")])
        except Exception:
            cands = []
        if target_file and not target_file.endswith("_manifest.json"):
            stem = os.path.basename(target_file).rsplit(".", 1)[0]
            prefer = os.path.join(d, stem + "_manifest.json")
            if os.path.isfile(prefer):
                cands = [prefer] + [c for c in cands if c != prefer]
        if len(cands) > 1 and st and st.get("last") and st["last"].get("manifest"):
            lm = _dv_win(st["last"]["manifest"])
            if lm in cands:
                cands = [lm] + [c for c in cands if c != lm]
        if cands:
            man_path = cands[0]
            try:
                man = json.loads(open(man_path, encoding="utf-8").read())
                man_src = "auto"
            except Exception as e:
                chk("manifest_loadable", False, "清单解析失败（%s）：%s" % (type(e).__name__, e), path=man_path)
        elif str(manifest) == "memory" and st and st.get("last"):
            man = st["last"]
            man_src = "memory(显式 memory 模式，比磁盘清单弱)"
    chk("manifest_available", man is not None,
        ("清单来源=%s path=%s" % (man_src, man_path)) if man is not None
        else "目录 %s 里没有 *_manifest.json（manifest=False 导出或路径给错）—— 显式传 manifest=<路径>；"
             "确实没清单时可用 manifest='memory' 拿最近一次导出的记录（较弱，会在返回里标出来）" % d,
        manifest_path=man_path, manifest_source=man_src, candidates=len(cands) if str(manifest) == "auto" else None)
    if man is None:
        return _dv_j({"ok": False, "target": p, "manifest": man_path, "manifest_source": man_src,
                   "passed": len([c for c in checks if c["ok"]]), "failed": len([c for c in checks if not c["ok"]]),
                   "failed_checks": [c["name"] for c in checks if not c["ok"]], "checks": checks,
                   "hint": "先 deliver_export(..., manifest=True) 再 verify；或 deliver_verify(<带清单的目录>)"})

    # ---- 2. 逐文件：存在 / 字节 / md5 / 面数
    flist = man.get("files") or []
    if not flist and target_file:
        flist = [{"role": os.path.basename(target_file).rsplit(".", 1)[-1], "path": target_file}]
    obs = {"files": {}}
    for f in flist:
        fp = _dv_win(f.get("path") or "")
        if not fp or not os.path.isfile(fp):
            if target_file and os.path.basename(fp) == os.path.basename(target_file):
                fp = target_file
        base = os.path.basename(str(fp))
        exists = bool(fp) and os.path.isfile(fp)
        chk("file_exists:" + base, exists, ("存在 %s" % fp) if exists else "清单里的文件不存在: %s" % fp, path=fp)
        if not exists:
            continue
        got_bytes = os.path.getsize(fp)
        got_md5 = _dv_md5(fp)
        obs["files"][base] = {"bytes": got_bytes, "md5": got_md5}
        chk("file_nonempty:" + base, got_bytes > 0, "%d 字节" % got_bytes, bytes=got_bytes)
        chk("md5_match:" + base, (not f.get("md5")) or got_md5 == f.get("md5"),
            "文件 md5=%s / 清单 md5=%s" % (got_md5, f.get("md5")), md5=got_md5, manifest_md5=f.get("md5"))
        if f.get("bytes") is not None:
            chk("size_match:" + base, int(f["bytes"]) == got_bytes,
                "文件 %d 字节 / 清单 %d 字节" % (got_bytes, int(f["bytes"])), bytes=got_bytes, manifest_bytes=int(f["bytes"]))
        ext = base.rsplit(".", 1)[-1].lower()
        if ext == "obj":
            sc = _dv_scan_obj(fp)
            obs["obj"] = sc
            chk("triangle_count_match:" + base, sc["triangles"] == int(man.get("counts", {}).get("triangles", f.get("triangles", -1))),
                "OBJ 里 %d 个三角面 / 清单 %s" % (sc["triangles"], man.get("counts", {}).get("triangles", f.get("triangles"))),
                triangles=sc["triangles"], manifest_triangles=man.get("counts", {}).get("triangles"))
            exp_g = int(man.get("counts", {}).get("groups", f.get("groups", -1)))
            exp_u = int(man.get("counts", {}).get("usemtl", f.get("usemtl", -1)))
            chk("obj_group_count_match:" + base, sc["groups"] == exp_g,
                "g 行 %d 个 / 对象(组)数 %d" % (sc["groups"], exp_g), groups=sc["groups"], manifest_groups=exp_g)
            chk("obj_usemtl_count_match:" + base, sc["usemtl"] == exp_u,
                "usemtl 行 %d 个 / 材质引用数 %d" % (sc["usemtl"], exp_u), usemtl=sc["usemtl"], manifest_usemtl=exp_u)
            chk("obj_has_normals:" + base, sc["normals"] >= 1, "vn 行 %d 个" % sc["normals"], normals=sc["normals"])
            # 从文件自己的 v 行重算 bbox，与清单里的 normalize 记录对拍（不信清单的自述）
            nb = man.get("normalize") or {}
            exp_lo, exp_hi = nb.get("bbox_after_min"), nb.get("bbox_after_max")
            if sc.get("bbox_min") and exp_lo and exp_hi:
                dev = max(abs(sc["bbox_min"][i] - float(exp_lo[i])) for i in range(3))
                dev = max(dev, max(abs(sc["bbox_max"][i] - float(exp_hi[i])) for i in range(3)))
                chk("obj_bbox_matches_manifest:" + base, dev <= 2e-4,
                    "文件 bbox=%s..%s / 清单记录=%s..%s（最大偏差 %.2e，容差 2e-4=两次 5 位小数舍入）"
                    % (sc["bbox_min"], sc["bbox_max"], exp_lo, exp_hi, dev), max_dev=dev)
            if nb.get("applied") and sc.get("longest_edge") is not None:
                want = 2.0 * float(nb.get("target_half_extent") or 0)
                chk("obj_normalized_longest_edge:" + base, abs(sc["longest_edge"] - want) <= 1e-4 * max(1.0, want),
                    "文件里最长边 %.5f / 期望 %.5f（2 × target_half_extent）" % (sc["longest_edge"], want),
                    longest=sc["longest_edge"], expected=want)
        elif ext == "stl":
            sc = _dv_scan_stl(fp)
            obs["stl"] = sc
            mt = int(man.get("counts", {}).get("triangles", f.get("triangles", -1)))
            chk("triangle_count_match:" + base, sc.get("triangles") == mt,
                "STL 头声明 %s 个三角面 / 清单 %d" % (sc.get("triangles"), mt), triangles=sc.get("triangles"), manifest_triangles=mt)
            chk("stl_size_consistent:" + base, sc.get("expected_bytes") == got_bytes,
                "84+50×%s = %s 字节 / 实际 %d 字节" % (sc.get("triangles"), sc.get("expected_bytes"), got_bytes),
                expected_bytes=sc.get("expected_bytes"), bytes=got_bytes)
            nb = man.get("normalize") or {}
            if nb.get("applied") and sc.get("longest_edge") is not None:
                want = 2.0 * float(nb.get("target_half_extent") or 0)
                chk("stl_normalized_longest_edge:" + base, abs(sc["longest_edge"] - want) <= 1e-4 * max(1.0, want),
                    "文件里最长边 %.5f / 期望 %.5f（2 × target_half_extent）" % (sc["longest_edge"], want),
                    longest=sc["longest_edge"], expected=want)

    # ---- 3. 单位块 / 归一化可反推（归档铁律）
    u = man.get("units") or {}
    note = str(u.get("note") or "")
    has_num = any(c.isdigit() for c in note)
    has_unit = ("mm" in note) or (" m" in note) or ("m =" in note)
    chk("units_note_has_units", bool(note) and has_num and has_unit,
        ("note=%s | meters_per_unit=%s m | millimeters_per_unit=%s mm"
         % (note, u.get("meters_per_unit"), u.get("millimeters_per_unit"))) if note
        else "清单没有单位换算块（units_note=False 或旧清单）",
        meters_per_unit=u.get("meters_per_unit"), millimeters_per_unit=u.get("millimeters_per_unit"))
    nz = man.get("normalize") or {}
    if nz.get("applied"):
        ok = (float(nz.get("scale") or 0) > 0) and (nz.get("center") is not None) and (nz.get("bbox_before_min") is not None)
        chk("normalize_recoverable", ok,
            "scale=%s center=%s target_half_extent=%s 原 bbox=%s..%s（p_orig = p_out/scale + center）"
            % (nz.get("scale"), nz.get("center"), nz.get("target_half_extent"), nz.get("bbox_before_min"), nz.get("bbox_before_max")),
            scale=nz.get("scale"), center=nz.get("center"), target_half_extent=nz.get("target_half_extent"))
        if nz.get("bbox_after_size"):
            longest = max(nz["bbox_after_size"])
            want = 2.0 * float(nz.get("target_half_extent") or 0)
            chk("normalize_longest_edge", abs(longest - want) <= 1e-4 * max(1.0, want),
                "归一化后最长边 %.6f / 期望 %.6f（2 × target_half_extent）" % (longest, want),
                longest=longest, expected=want)
    else:
        chk("normalize_recoverable", nz.get("bbox_before_min") is not None,
            "未归一化（normalize=False）：scale=1、center=(0,0,0)，文件尺度即原始尺度；原 bbox=%s..%s"
            % (nz.get("bbox_before_min"), nz.get("bbox_before_max")), scale=1.0)

    passed = len([c for c in checks if c["ok"]])
    fail = [c["name"] for c in checks if not c["ok"]]
    return _dv_j({"ok": len(fail) == 0, "target": p, "manifest": man_path, "manifest_source": man_src,
               "passed": passed, "failed": len(fail), "failed_checks": fail, "checks": checks,
               "observed": obs, "counts": man.get("counts"),
               "note": "逐项核对；任何一项 FAIL 都意味着这个交付物不能按清单归档"})


def deliver_help():
    return _dv_j({
        "version": DELIVER_VERSION,
        "api": "K.dsh_deliver_api",
        "ops": {
            "export": "export(dir, objects=None, scope=None, name='model', fmt='obj'|'stl', normalize=True, "
                      "target_half_extent=1.0, per_part_materials=True, manifest=True, units_note=True, include_hidden=False)",
            "verify": "verify(path_or_dir=None, manifest='auto') → 逐项 PASS/FAIL（md5 / 面数 / g 与 usemtl / 单位 / 归一化可反推）；"
                      "manifest='auto' 只认磁盘上的 *_manifest.json，manifest='memory' 才是拿最近一次导出的内存记录（较弱）",
            "help": "help() → 本速查",
        },
        "typical": "K.dsh_deliver_api['export']({'dir': 'D:/Blender/house', 'scope': 'House', 'name': 'house'}) "
                   "→ 然后 K.dsh_deliver_api['verify']('D:/Blender/house')",
        "rules": ["空产出（挑不到对象 / 全是空网格）→ ok:false，不静默成功",
                  "归一化记录 scale/center/原 bbox → p_orig = p_out / scale + center（归档铁律）",
                  "OBJ：每对象一个 g + usemtl；MTL 的 Kd 来自材质，取不到给确定性灰阶并在 guessed_colors 里点名",
                  "STL 不携带材质（per_part_materials 对 stl 无效）",
                  "坐标轴：Blender 世界系 Z-up 原样写出，不做 Y-up 转换"],
    })


def d_dispatch(op, args=None):
    """统一入口：工具侧发 {op, args}（与 txn/contract 同族的解包方式）。"""
    if isinstance(args, str):
        try:
            args = json.loads(args) if args.strip() else {}
        except Exception:
            args = {}
    kw = {}
    for k, v in (args or {}).items():
        if k == "args" and isinstance(v, dict):
            kw.update(v)
        else:
            kw[k] = v
    fn = d_ops().get(str(op))
    if fn is None:
        return _dv_j({"ok": False, "error": "unknown deliver op", "op": op, "ops": sorted(d_ops())})
    try:
        return fn(**kw)
    except TypeError as e:
        return _dv_j({"ok": False, "error": "参数不匹配: %s" % e, "op": op, "given": sorted(kw), "sig_hint": deliver_help()})


def d_ops():
    return {"export": deliver_export, "verify": deliver_verify, "help": deliver_help}


import sys as _sys
_dv_K = _sys.modules.get("dsh_rt_kernel")
if _dv_K is not None:
    _dv_K.dsh_deliver_api = {"version": DELIVER_VERSION, "dispatch": d_dispatch,
                          "export": deliver_export, "verify": deliver_verify, "help": deliver_help}

# ---- v0.9.1（93-B1/B2）：API 可调用化（换成 dict 子类实例，返回已解析对象）----
# 背景：K.dsh_x_api 原来是普通 dict → 进程内 api(args) 报 TypeError: 'dict' object is not callable；
# 且 dispatch 返回 JSON 字符串，调用方还得自己 json.loads。
# 现在：api("op", {…}) 或 api({…}) → dict；api["dispatch"](op, json_str) 仍返回 str（引擎契约不变）。
# 注意：dict 是静态类型，不能对已有实例做 __class__ 赋值（实测 TypeError），所以换成一个新实例。
class _DshApi(dict):
    _DEFAULT_OP = "help"

    def __init__(self, base=None):
        dict.__init__(self, base or {})

    def __call__(self, op=None, args=None, **kw):
        if op is None or isinstance(op, dict):
            args, op = (op if isinstance(op, dict) else args), self._DEFAULT_OP
        payload = args if isinstance(args, str) else json.dumps(dict(args or {}, **kw),
                                                               ensure_ascii=False, default=str)
        return json.loads(self["dispatch"](str(op), payload))

    def call(self, op, args=None, **kw):
        return self(op, args, **kw)


import sys as _sys_api
_K_api = _sys_api.modules.get("dsh_rt_kernel")
if _K_api is not None and isinstance(getattr(_K_api, "dsh_deliver_api", None), dict) \
        and not isinstance(getattr(_K_api, "dsh_deliver_api", None), _DshApi):
    _K_api.dsh_deliver_api = _DshApi(_K_api.dsh_deliver_api)
