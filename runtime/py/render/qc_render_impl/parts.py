# -*- coding: utf-8 -*-
"""Split from qc_render.py; behavior preserved by the facade."""
from .common import *  # noqa: F401,F403
from .common import _j

PARTS_PALETTE = [
    (230, 60, 60), (60, 130, 230), (70, 200, 90), (240, 190, 50),
    (170, 90, 220), (40, 200, 200), (240, 130, 40), (130, 130, 240),
    (200, 220, 60), (240, 110, 180), (90, 200, 150), (150, 150, 150),
]


def _srgb_to_linear(v):
    """sRGB(0-1) → 线性。Base Color / Emission 吃的是线性值；把 sRGB 直接塞进去会整体偏亮，
    图例与图上颜色就对不上了（颜色编码类渲染，图例必须能对上图）。"""
    v = max(0.0, min(1.0, float(v)))
    return v / 12.92 if v <= 0.04045 else ((v + 0.055) / 1.055) ** 2.4


def _palette_linear(rgb255):
    return tuple(round(_srgb_to_linear(float(c) / 255.0), 6) for c in rgb255[:3])


def _stable_hash(s):
    """跨进程稳定的字符串哈希。坑：Python 内置 hash() 带随机盐（PYTHONHASHSEED），
    同一场景两次渲染会换色 —— 确定性配色必须用 md5。"""
    return int(hashlib.md5(str(s).encode("utf-8")).hexdigest()[:8], 16)


def _parts_groups(objs, group_by):
    """部件分组 → [(组名, [obj, ...])]，按组名排序（确定性）。

    group_by="object"（默认，一对象一部件）| "collection"（一集合一部件：对象属于多个集合时取
    **名字最小**的那个集合 —— 别用 users_collection 的自然顺序，那是随场景结构变的）。
    """
    g = {}
    for ob in objs:
        if str(group_by) == "collection":
            cols = sorted([str(c.name) for c in ob.users_collection]) or ["(无集合)"]
            key = cols[0]
        else:
            key = str(ob.name)
        g.setdefault(key, []).append(ob)
    return sorted(g.items(), key=lambda kv: str(kv[0]))


def _parts_assign(groups, palette):
    """按组名排序分配颜色 → {组名: {"index","rgb","source"}}（同场景多次渲染配色一致）。

    · 组数 ≤ 色数：第 i 组吃 palette[i]（source="order"）；
    · 组数 > 色数：多出来的组按名字哈希排定次序，逐个塞进"当前用得最少"的颜色（source="hash"）——
      确定性 + 复用摊得最匀；**绝不因为色数不够报错**。
    """
    n = len(palette)
    asg = {}
    used = [0] * n
    for i, (name, _objs) in enumerate(groups):
        if i < n:
            asg[str(name)] = {"index": i, "rgb": [int(palette[i][0]), int(palette[i][1]), int(palette[i][2])],
                              "source": "order"}
            used[i] += 1
    rest = [str(nm) for i, (nm, _o) in enumerate(groups) if i >= n]
    for nm in sorted(rest, key=lambda s: (_stable_hash(s), s)):
        k = min(range(n), key=lambda j: (used[j], _stable_hash("%s#%d" % (nm, j))))   # 用得最少的色，哈希定序
        used[k] += 1
        asg[nm] = {"index": k, "rgb": [int(palette[k][0]), int(palette[k][1]), int(palette[k][2])],
                   "source": "hash"}
    return asg


def _make_part_material(idx, rgb255, flat=False):
    """纯色临时材质：默认 Principled（有明暗 → 能看出穿插/悬空/朝向；Procedura 也是这个口径），
    flat=True 用 Emission（不受灯光影响，图上颜色≈图例色，适合照着色标读图）。
    材质名带序号与色值（DSH_PC_07_F0BE32），事后在 .blend 里能一眼认出是谁留下的。"""
    rgb255 = [int(rgb255[0]), int(rgb255[1]), int(rgb255[2])]
    name = "DSH_PC_%02d_%02X%02X%02X" % (int(idx), rgb255[0], rgb255[1], rgb255[2])
    mat = bpy.data.materials.new(name)
    try:
        mat.use_nodes = True           # 5.2 唯一的开节点方式（6.0 起会移除这个开关 → 不写死）
    except Exception:
        pass
    lin = _palette_linear(rgb255)
    nt = getattr(mat, "node_tree", None)
    if nt is None:
        raise RuntimeError("材质 %s 拿不到 node_tree（Blender 版本差异）" % name)
    out = None
    for nd in nt.nodes:
        if nd.bl_idname == "ShaderNodeOutputMaterial":
            out = nd
    if out is None:
        out = nt.nodes.new("ShaderNodeOutputMaterial")
    if flat:
        nd = nt.nodes.new("ShaderNodeEmission")
        nd.inputs["Color"].default_value = (lin[0], lin[1], lin[2], 1.0)
        try:
            nd.inputs["Strength"].default_value = 1.0
        except Exception:
            pass
        nt.links.new(nd.outputs[0], out.inputs["Surface"])
    else:
        b = None
        for nd in nt.nodes:
            if nd.bl_idname == "ShaderNodeBsdfPrincipled":
                b = nd
        if b is None:
            b = nt.nodes.new("ShaderNodeBsdfPrincipled")
            nt.links.new(b.outputs[0], out.inputs["Surface"])
        b.inputs["Base Color"].default_value = (lin[0], lin[1], lin[2], 1.0)
        for k, v in (("Roughness", 0.55), ("Metallic", 0.0), ("Specular IOR Level", 0.3)):
            if k in b.inputs:
                try:
                    b.inputs[k].default_value = v
                except Exception:
                    pass
    return mat


def _parts_override(objs, assign_by_name, group_of, flat):
    """临时覆盖材质槽 → (state, mats_by_group, err)。state 逐对象记下还原所需的一切。

    两个坑（都实测过，写在注释里防后人踩）：
      · **链接副本**（多个对象共享同一 mesh）改 DATA 槽会串色到别的对象（实测 shared_bleeds=true）→
        先 `ob.data = ob.data.copy()` 隔离，还原时换回原 mesh 并删掉副本（mesh 计数必须回基线）；
      · **0 槽对象**要 append 一个槽才渲得出色，还原时 `mesh.materials.clear()` 清回 0 槽
        （直接赋 None 会留下一个空槽，槽数就对不上了）。
    """
    state, mats, err = [], {}, None
    mat_cache = {}
    for ob in objs:
        key = str(group_of(ob))
        asg = (assign_by_name or {}).get(key) or {}
        rgb = asg.get("rgb") or [int(PARTS_PALETTE[0][0]), int(PARTS_PALETTE[0][1]), int(PARTS_PALETTE[0][2])]
        idx = int(asg.get("index") or 0)
        mk = (idx, tuple(int(x) for x in rgb), bool(flat))
        mat = mat_cache.get(mk)
        if mat is None:
            mat = _make_part_material(idx, rgb, flat)
            mat_cache[mk] = mat
        mats[key] = mat
        st = {"object": str(ob.name), "group": key, "material": str(mat.name),
              "slots": [[str(s.link), (str(s.material.name) if s.material else None)] for s in ob.material_slots],
              "n_slots": len(ob.material_slots), "appended": False,
              "mesh_before": None, "mesh_copy": None}
        try:
            if int(getattr(ob.data, "users", 0) or 0) > 1:
                st["mesh_before"] = ob.data
                ob.data = ob.data.copy()
                st["mesh_copy"] = ob.data
            if len(ob.material_slots) == 0:
                ob.data.materials.append(mat)
                st["appended"] = True
            else:
                for s in ob.material_slots:
                    s.material = mat
        except Exception as e:
            err = "覆盖材质失败（%s）：%s: %s" % (ob.name, type(e).__name__, str(e)[:160])
            state.append(st)
            break
        state.append(st)
    return state, mats, err


def _parts_restore(state, mats):
    """还原 parts_color 现场：材质槽（含链接副本换回原 mesh、删副本）→ 删临时材质 → **逐项核对**。

    核对口径：还原后的 `[(slot.link, slot.material.name)]` 必须与覆盖前逐槽相等；
    核对不过 → 调用方把 ok 置 false（改了场景没还原干净，比少出一张图严重得多）。
    """
    rep = {"objects": len(state), "slots_restored": 0, "mesh_copies_removed": 0,
           "temp_materials_removed": 0, "temp_materials_left": [], "mismatches": [], "ok": False}
    for st in reversed(state):
        ob = bpy.data.objects.get(str(st.get("object")))
        if ob is None:
            rep["mismatches"].append("%s：对象已不在（无法核对材质槽）" % st.get("object"))
            continue
        try:
            if st.get("appended"):
                ob.data.materials.clear()
            else:
                for i, pair in enumerate(st.get("slots") or []):
                    if i >= len(ob.material_slots):
                        rep["mismatches"].append("%s：槽数 %d < 记录 %d"
                                                 % (ob.name, len(ob.material_slots), len(st.get("slots") or [])))
                        break
                    s = ob.material_slots[i]
                    if str(s.link) != str(pair[0]):
                        s.link = str(pair[0])
                    s.material = (bpy.data.materials.get(str(pair[1])) if pair[1] else None)
        except Exception as e:
            rep["mismatches"].append("%s：还原材质槽抛错 %s: %s"
                                     % (st.get("object"), type(e).__name__, str(e)[:140]))
        if st.get("mesh_copy") is not None:
            try:
                ob.data = st["mesh_before"]
                bpy.data.meshes.remove(st["mesh_copy"])
                rep["mesh_copies_removed"] += 1
            except Exception as e:
                rep["mismatches"].append("%s：链接副本 mesh 还原失败 %s: %s"
                                         % (st.get("object"), type(e).__name__, str(e)[:140]))
        try:
            now = [[str(s.link), (str(s.material.name) if s.material else None)] for s in getattr(ob, "material_slots", ())]
            if now != [list(x) for x in (st.get("slots") or [])]:
                rep["mismatches"].append("%s：材质槽与渲染前不等 %s != %s"
                                         % (ob.name, now, st.get("slots")))
            else:
                rep["slots_restored"] += 1
        except Exception as e:
            rep["mismatches"].append("%s：核对材质槽抛错 %s: %s" % (ob.name, type(e).__name__, str(e)[:120]))
    for mat in set(mats.values()):
        nm = str(mat.name)
        try:
            bpy.data.materials.remove(mat, do_unlink=True)
        except Exception as e:
            rep["mismatches"].append("临时材质 %s 删除失败 %s: %s" % (nm, type(e).__name__, str(e)[:120]))
        if bpy.data.materials.get(nm) is not None:
            rep["temp_materials_left"].append(nm)
        else:
            rep["temp_materials_removed"] += 1
    rep["ok"] = (not rep["mismatches"]) and (not rep["temp_materials_left"])
    return rep


def _parts_legend_rows(groups, assign):
    """图例行：`名字<TAB>R,G,B<TAB>面数`（面数 = 该部件所有对象的 polygon 之和）。"""
    rows = []
    for name, objs in groups:
        asg = assign.get(str(name)) or {}
        rgb = asg.get("rgb") or [0, 0, 0]
        faces = 0
        for ob in objs:
            try:
                faces += len(ob.data.polygons)
            except Exception:
                pass
        rows.append({"name": str(name).replace("\t", " ").replace("\n", " "),
                     "rgb": [int(rgb[0]), int(rgb[1]), int(rgb[2])], "faces": int(faces),
                     "objects": [str(o.name) for o in objs], "color_index": asg.get("index"),
                     "color_source": asg.get("source")})
    return rows


def _write_parts_legend(txt_path, rows, meta):
    """写图例：.txt = 每行 `名字<TAB>R,G,B<TAB>面数`（**纯数据行、无表头** → `wc -l` 即部件数）；
    .json = 同名的全量细节（调色板、sRGB→线性换算、分组口径、还原自检…），人照样读得到。"""
    with open(txt_path, "w", encoding="utf-8") as f:
        for r in rows:
            f.write("%s\t%d,%d,%d\t%d" % (r["name"], r["rgb"][0], r["rgb"][1], r["rgb"][2], r["faces"]) + chr(10))
    jpath = os.path.splitext(txt_path)[0] + ".json"
    with open(jpath, "w", encoding="utf-8") as f:
        f.write(_j(dict(meta, legend_txt=txt_path, legend_json=jpath, rows=rows)))
    return jpath


def _legend_line_count(path):
    """图例数据行数（空行不计）—— 自检用它断言"行数 == 部件数"。"""
    n = 0
    with open(path, encoding="utf-8") as f:
        for ln in f:
            if ln.strip():
                n += 1
    return n
