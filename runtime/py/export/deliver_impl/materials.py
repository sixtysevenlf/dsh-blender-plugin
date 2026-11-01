# -*- coding: utf-8 -*-
"""Split from deliver.py; behavior preserved by the facade."""
from .common import *  # noqa: F401,F403


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
            "usemtl": len(parts), "usemtl_unique": len(set(p["material_ref"] for p in parts)),
            "lines": len(out) + 1}


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
