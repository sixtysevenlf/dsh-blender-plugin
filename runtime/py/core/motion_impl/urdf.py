# -*- coding: utf-8 -*-
"""Split from motion.py; behavior preserved by the facade."""
from .common import *  # noqa: F401,F403



# ============================================================ URDF 导出

def _select_joints(joints):
    st = _store()
    if joints is None:
        ids = list(st["joints"])
    else:
        ids = [str(x) for x in _names(joints)]
        bad = [i for i in ids if i not in st["joints"]]
        if bad:
            return None, _j({"ok": False, "error": "有未登记的关节 id", "unknown": bad,
                             "have": sorted(st["joints"])})
    recs = [st["joints"][i] for i in ids]
    return recs, None


def _group_conflicts(recs):
    """同一对象出现在多个 link 里 = 刚体归属矛盾：URDF/USDA 会把它的几何与质量重复计入 → 必须拦住。"""
    owner = {}
    for r in recs:
        for side in ("parent", "child"):
            lname = _link_name(r[side])
            for n in _names(r[side]):
                owner.setdefault(n, set()).add(lname)
    return {n: sorted(v) for n, v in owner.items() if len(v) > 1}


def _build_tree(recs, warnings):
    """关节记录 → URDF 单树（link=对象组；degrade / 丢边 / orphan 合成固定关节）。"""
    links, edges, order = {}, {}, []
    for r in recs:
        pk, ck = _link_key(r["parent"]), _link_key(r["child"])
        for key in (pk, ck):
            if key not in links:
                links[key] = {"key": key, "names": list(key), "name": _link_name(key),
                              "frame": None, "joint": None}
        if ck in edges:
            warnings.append("link %s 已有父关节（%s），跳过关节 %s（URDF 单树约束）"
                            % (links[ck]["name"], edges[ck]["jid"], r["id"]))
            continue
        edges[ck] = {"jid": r["id"], "parent": pk, "child": ck, "rec": r}
        order.append(ck)
    if not links:
        return None
    # 根：没有父关节的 link
    roots = [k for k in links if k not in edges]
    if not roots:
        warnings.append("关节图成环（没有无父 link）→ 丢掉一条边打破环")
        drop = order[0]
        edges.pop(drop, None)
        roots = [k for k in links if k not in edges]
    root = sorted(roots, key=lambda k: links[k]["name"])[0]
    if len(roots) > 1:
        warnings.append("有 %d 个候选根（%s）→ 取 %s 为根，其余在下面按不可达处理"
                        % (len(roots), ", ".join(links[k]["name"] for k in sorted(roots)), links[root]["name"]))
    # 可达性
    def _reach():
        seen = {root}
        q = [root]
        kids = {}
        for ck, e in edges.items():
            kids.setdefault(e["parent"], []).append(ck)
        while q:
            cur = q.pop(0)
            for c in kids.get(cur, []):
                if c not in seen:
                    seen.add(c)
                    q.append(c)
        return seen
    reach = _reach()
    guard = 0
    while len(reach) < len(links) and guard < len(links) + 2:
        guard += 1
        cand = sorted([k for k in links if k not in reach and k not in edges],
                      key=lambda k: links[k]["name"])
        if not cand:
            cand = sorted([k for k in links if k not in reach], key=lambda k: links[k]["name"])
            if cand:
                warnings.append("link %s 不可达且带着成环的边 → 丢掉该边以接入根"
                                % links[cand[0]]["name"])
                edges.pop(cand[0], None)
                cand = [cand[0]]
        if not cand:
            break
        k = cand[0]
        warnings.append("link %s 从根 %s 不可达 → 合成 fixed 关节挂到根（URDF 必须单树）"
                        % (links[k]["name"], links[root]["name"]))
        edges[k] = {"jid": "orphan_%s_fixed" % links[k]["name"], "parent": root, "child": k,
                    "rec": None, "synthesized": True}
        reach = _reach()
    # 帧：根=世界原点，其余=关节锚点（无锚点则退到该组 bbox 中心）
    links[root]["frame"] = np.zeros(3).tolist()
    dgf = bpy.context.evaluated_depsgraph_get()
    for ck, e in edges.items():
        r = e["rec"]
        if r is not None and r.get("anchor"):
            links[ck]["frame"] = [float(x) for x in r["anchor"]]
            continue
        v, _f, _p = _group_tris(_objs(links[ck]["names"])[0], dgf)
        mn, mx, _d = _bbox(v)
        if mn is not None:
            links[ck]["frame"] = (((mn + mx) / 2.0).tolist())
            if r is not None:
                warnings.append("关节 %s（kind=%s）没有锚点 → link %s 的帧取该组 bbox 中心 "
                                "(%.4g, %.4g, %.4g)（网格随之写成相对该帧）"
                                % (r["id"], r["kind"], links[ck]["name"], links[ck]["frame"][0],
                                   links[ck]["frame"][1], links[ck]["frame"][2]))
        else:
            links[ck]["frame"] = [0.0, 0.0, 0.0]
            warnings.append("link %s 既没有关节锚点也没有几何 → 帧取世界原点" % links[ck]["name"])
    return {"root": root, "links": links, "edges": edges, "reach": reach}


def _urdf_type(rec, warnings):
    k = str(rec["kind"]).lower()
    if k in ("revolute", "continuous", "prismatic", "fixed"):
        if k == "revolute" and not rec.get("limits"):
            warnings.append("关节 %s 是 revolute 但没有 limits → 按 continuous 导出"
                            "（URDF 的 revolute 必须给 lower/upper）" % rec["id"])
            return "continuous", None
        return k, None
    warnings.append("关节 %s 的 kind=%s 降级为 fixed（URDF 表达不了 %s）" % (rec["id"], k, k))
    return "fixed", k


def _urdf_limits_attrs(utype, rec, fallback_travel_m, warnings):
    k = rec["kind"]
    if utype == "revolute":
        lo = float((rec.get("limits") or [0.0, 0.0])[0])
        hi = float((rec.get("limits") or [0.0, 0.0])[1])
        return ' lower="%s" upper="%s"' % (_fnum(math.radians(lo)), _fnum(math.radians(hi)))
    if utype == "prismatic":
        if rec.get("limits"):
            lo, hi = float(rec["limits"][0]), float(rec["limits"][1])
            return ' lower="%s" upper="%s"' % (_fnum(lo), _fnum(hi))
        warnings.append("关节 %s 是 prismatic 但没 limits → 用模型对角线的 ±%s m 兜底"
                        "（URDF 缺 lower/upper 会被读成 0，等于锁死自由度）"
                        % (rec["id"], _fnum(fallback_travel_m)))
        return ' lower="%s" upper="%s"' % (_fnum(-fallback_travel_m), _fnum(fallback_travel_m))
    return ""


def _motion_export_urdf_p1(st):
    dir, fixed_base, joints, masses, mesh_format = (
        st["dir"], st["fixed_base"], st["joints"], st.get("masses"), st["mesh_format"])
    meters_per_unit, name = st["meters_per_unit"], st["name"]
    F, V, Vm, fn, fp, frame, k, mn, mx, objs, v = (st.get("F"), st.get("V"), st.get("Vm"), st.get("fn"), st.get("fp"), st.get("frame"), st.get("k"), st.get("mn"), st.get("mx"), st.get("objs"), st.get("v"))
    try:
        t0 = time.perf_counter()
        if not dir:
            return _j({"ok": False, "error": "dir 必填（URDF 与网格写到哪）"})
        recs, err = _select_joints(joints)
        if err is not None:
            return _j(json.loads(err))
        if not recs:
            return _j({"ok": False, "error": "没有关节可导出（空产出必须失败）",
                       "registered": sorted(_store()["joints"])})
        conf = _group_conflicts(recs)
        if conf:
            return _j({"ok": False, "error": "关节分组互相重叠：同一个对象属于多个 link（刚体归属矛盾 → "
                                            "几何与质量会被重复计入）→ 请把各关节的 parent/child 组改成互不重叠",
                       "conflicts": conf, "units": {"length": "scene_unit"}})
        fmt = str(mesh_format or "obj").lower()
        if fmt not in ("obj", "stl"):
            return _j({"ok": False, "error": "mesh_format 只支持 obj / stl", "given": fmt})
        mpu = _mpu_arg(meters_per_unit)
        robot = _safe_name(name, "model")
        outdir = _win(dir)
        meshdir = os.path.join(outdir, "meshes")
        warnings = []
        try:
            os.makedirs(meshdir, exist_ok=True)
        except Exception as e:
            return _j({"ok": False, "error": "建目录失败: %s: %s" % (type(e).__name__, e), "dir": outdir})

        tree = _build_tree(recs, warnings)
        if tree is None:
            return _j({"ok": False, "error": "关节树为空（没有 link）"})
        links, edges, root = tree["links"], tree["edges"], tree["root"]
        dg = bpy.context.evaluated_depsgraph_get()
        masses = masses or {}

        # 兜底行程（m）：模型 bbox 对角线，下限 0.1 m
        allV = []
        for k, L in links.items():
            v, _f, _p = _group_tris(_objs(L["names"])[0], dg)
            if len(v):
                allV.append(v)
        fallback_travel_m = 0.1
        model_diag_m = 0.0
        if allV:
            mn, mx, _d = _bbox(np.concatenate(allV, axis=0))
            if mn is not None:
                model_diag_m = float(np.linalg.norm(mx - mn)) * mpu
                fallback_travel_m = max(0.1, model_diag_m)
        units_suspect = _units_warnings(mpu, model_diag_m, warnings)

        # 每 link 一个网格（相对 link 帧，单位 m）
        mesh_files, link_geo = {}, {}
        for k, L in links.items():
            objs, _m, _n = _objs(L["names"])
            V, F, _parts = _group_tris(objs, dg)
            if len(V) == 0 or len(F) == 0:
                warnings.append("link %s 没有可导出的三角面 → URDF 里该 link 无 visual/collision" % L["name"])
                link_geo[k] = None
                continue
            frame = np.asarray(L["frame"] or [0.0, 0.0, 0.0], dtype=np.float64)
            Vm = (V - frame) * mpu
            fn = "%s.%s" % (L["name"], fmt)
            fp = os.path.join(meshdir, fn)
            try:
                if fmt == "obj":
                    _write_obj(fp, Vm, F, "DSH motion 导出：link %s / %d 顶点 / %d 三角面 / 单位 m"
                                           % (L["name"], len(Vm), len(F)))
                else:
                    _write_stl(fp, Vm, F, "DSH motion link %s" % L["name"])
            except Exception as e:
                return _j({"ok": False, "error": "写网格失败 %s: %s" % (fn, str(e)[:160])})
            mesh_files[k] = fn
            link_geo[k] = {"verts": int(len(Vm)), "tris": int(len(F)),
                           "min": _rl(Vm.min(axis=0), 6), "max": _rl(Vm.max(axis=0), 6),
                           "bytes": os.path.getsize(fp)}

        L = ['<?xml version="1.0" encoding="utf-8"?>',
             '<!-- DSH motion v%d 导出（纯字符串生成，SI 单位；bbox 近似惯量） -->' % MOTION_VERSION,
             '<robot name="%s">' % _esc(robot)]
        if fixed_base:
            L.append('  <link name="world"/>')
            L.append('  <joint name="world_to_%s" type="fixed">' % _esc(links[root]["name"]))
            L.append('    <origin xyz="0 0 0" rpy="0 0 0"/>')
            L.append('    <parent link="world"/>')
            L.append('    <child link="%s"/>' % _esc(links[root]["name"]))
            L.append('  </joint>')
    finally:
        st.update((k, v) for k, v in locals().items() if k != "st")

def _motion_export_urdf_p2(st):
    L, density, dg, edges, effort = st.get("L"), st["density"], st.get("dg"), st.get("edges"), st["effort"]
    fallback_travel_m, links, masses, mesh_files, meshdir = (
        st.get("fallback_travel_m"), st.get("links"), st.get("masses"), st.get("mesh_files"), st.get("meshdir"))
    mpu, note, outdir, robot, root = (
        st.get("mpu"), st["note"], st.get("outdir"), st.get("robot"), st.get("root"))
    velocity, warnings = st["velocity"], st.get("warnings")
    Lk, V, attrs, axis, cf, child, ck, com, dims, e, f, fn, frame, ixx, iyy, izz, jname, k, key, m_explicit, mass, mn, mx, org, parent, pf, r, tag, utype, vloc, vol = (st.get("Lk"), st.get("V"), st.get("attrs"), st.get("axis"), st.get("cf"), st.get("child"), st.get("ck"), st.get("com"), st.get("dims"), st.get("e"), st.get("f"), st.get("fn"), st.get("frame"), st.get("ixx"), st.get("iyy"), st.get("izz"), st.get("jname"), st.get("k"), st.get("key"), st.get("m_explicit"), st.get("mass"), st.get("mn"), st.get("mx"), st.get("org"), st.get("parent"), st.get("pf"), st.get("r"), st.get("tag"), st.get("utype"), st.get("vloc"), st.get("vol"))
    try:
        for k in sorted(links, key=lambda x: links[x]["name"]):
            Lk = links[k]
            V, F, _p = _group_tris(_objs(Lk["names"])[0], dg)
            frame = np.asarray(Lk["frame"] or [0.0, 0.0, 0.0], dtype=np.float64)
            dims, mass = [0.0, 0.0, 0.0], 0.0
            com = [0.0, 0.0, 0.0]
            if len(V):
                vloc = (V - frame) * mpu
                mn, mx = vloc.min(axis=0), vloc.max(axis=0)
                dims = (mx - mn).tolist()
                com = ((mn + mx) / 2.0).tolist()
                vol = max(0.0, dims[0] * dims[1] * dims[2])
                m_explicit = None
                for key in (Lk["name"],) + tuple(Lk["names"]):
                    if key in masses:
                        m_explicit = float(masses[key])
                        break
                mass = m_explicit if m_explicit is not None else float(density) * vol * BBOX_FILL_FACTOR
            ixx = max(MIN_INERTIA, mass / 12.0 * (dims[1] ** 2 + dims[2] ** 2))
            iyy = max(MIN_INERTIA, mass / 12.0 * (dims[0] ** 2 + dims[2] ** 2))
            izz = max(MIN_INERTIA, mass / 12.0 * (dims[0] ** 2 + dims[1] ** 2))
            if mass <= 0:
                warnings.append("link %s 质量为 0（密度 %g × bbox 体积 × %.2f）→ 惯量夹到 %.0e"
                                % (Lk["name"], density, BBOX_FILL_FACTOR, MIN_INERTIA))
            L.append('  <link name="%s">' % _esc(Lk["name"]))
            L.append('    <inertial>')
            L.append('      <origin xyz="%s %s %s" rpy="0 0 0"/>' % tuple(_fnum(x) for x in com))
            L.append('      <mass value="%s"/>' % _fnum(mass))
            L.append('      <inertia ixx="%s" ixy="0" ixz="0" iyy="%s" iyz="0" izz="%s"/>'
                     % (_fnum(ixx), _fnum(iyy), _fnum(izz)))
            L.append('    </inertial>')
            fn = mesh_files.get(k)
            if fn:
                for tag in ("visual", "collision"):
                    L.append('    <%s>' % tag)
                    L.append('      <origin xyz="0 0 0" rpy="0 0 0"/>')
                    L.append('      <geometry>')
                    L.append('        <mesh filename="meshes/%s"/>' % _esc(fn))
                    L.append('      </geometry>')
                    L.append('    </%s>' % tag)
            else:
                warnings.append("link %s 无几何：URDF 里没有 visual/collision" % Lk["name"])
            L.append('  </link>')
        for ck in sorted(edges, key=lambda x: links[x]["name"]):
            e = edges[ck]
            r = e["rec"]
            child = links[ck]
            parent = links[e["parent"]]
            pf = np.asarray(parent["frame"] or [0.0, 0.0, 0.0], dtype=np.float64)
            cf = np.asarray(child["frame"] or [0.0, 0.0, 0.0], dtype=np.float64)
            org = (cf - pf) * mpu
            if r is None:
                utype, degraded, jname = "fixed", None, e["jid"]
                axis = [0.0, 0.0, 1.0]
            else:
                utype, degraded = _urdf_type(r, warnings)
                jname = _safe_name(r["id"], "joint")
                axis = r.get("axis") or [0.0, 0.0, 1.0]
            L.append('  <joint name="%s" type="%s">' % (_esc(jname), utype))
            L.append('    <origin xyz="%s %s %s" rpy="0 0 0"/>' % tuple(_fnum(x) for x in org))
            L.append('    <parent link="%s"/>' % _esc(parent["name"]))
            L.append('    <child link="%s"/>' % _esc(child["name"]))
            if utype != "fixed":
                L.append('    <axis xyz="%s %s %s"/>' % tuple(_fnum(x) for x in axis))
                attrs = ""
                if r is not None:
                    attrs = _urdf_limits_attrs(utype, r, fallback_travel_m, warnings)
                L.append('    <limit%s effort="%s" velocity="%s"/>'
                         % (attrs, _fnum(effort), _fnum(velocity if utype != "prismatic" else velocity * mpu)))
            L.append('  </joint>')
        L.append('</robot>')
        text = "\n".join(L) + "\n"
        path = os.path.join(outdir, robot + ".urdf")
        try:
            with open(path, "w", encoding="utf-8") as f:
                f.write(text)
        except Exception as e:
            return _j({"ok": False, "error": "写 URDF 失败: %s: %s" % (type(e).__name__, e), "path": path})

        chk = _urdf_self_check(text, path, meshdir, mesh_files, root)
        rec_export = {"kind": "urdf", "path": path, "dir": outdir, "meshes": len(mesh_files),
                      "bytes": os.path.getsize(path), "links": len(links), "joints": len(edges),
                      "self_check_ok": chk["ok"], "warnings": len(warnings), "t": _now(),
                      "note": str(note)}
        _store()["exports"].append(rec_export)
    finally:
        st.update((k, v) for k, v in locals().items() if k != "st")

def _motion_export_urdf_p3(st):
    chk, edges, link_geo, links, mesh_files = (
        st.get("chk"), st.get("edges"), st.get("link_geo"), st.get("links"), st.get("mesh_files"))
    meshdir, model_diag_m, mpu, note, outdir = (
        st.get("meshdir"), st.get("model_diag_m"), st.get("mpu"), st["note"], st.get("outdir"))
    path, robot, root, t0, units_suspect = (
        st.get("path"), st.get("robot"), st.get("root"), st.get("t0"), st.get("units_suspect"))
    warnings = st.get("warnings")
    try:
        del _store()["exports"][:-50]
        _log("export_urdf", {"path": path, "links": len(links), "joints": len(edges),
                             "self_check_ok": chk["ok"]})
        if not chk["ok"]:
            return _j({"ok": False, "error": "URDF 结构自检未通过（产物已写出，但不合格）",
                       "path": path, "self_check": chk, "warnings": warnings})
        return _j({"ok": True, "op": "export_urdf", "path": path, "dir": outdir, "mesh_dir": meshdir,
                   "robot": robot, "root_link": links[root]["name"],
                   "links": [{"link": links[k]["name"], "objects": links[k]["names"],
                              "frame_m": _rl(np.asarray(links[k]["frame"] or [0, 0, 0]) * mpu, 6),
                              "mesh": mesh_files.get(k), "geo": link_geo.get(k)}
                             for k in sorted(links, key=lambda x: links[x]["name"])],
                   "joints": [{"joint": e["jid"], "type": (_urdf_type(e["rec"], [])[0] if e["rec"] else "fixed"),
                               "parent": links[e["parent"]]["name"], "child": links[e["child"]]["name"],
                               "synthesized": bool(e.get("synthesized")),
                               "degraded_from": (_urdf_type(e["rec"], [])[1] if e["rec"] else None)}
                              for e in [edges[c] for c in sorted(edges, key=lambda x: links[x]["name"])]],
                   "units": {"urdf": "SI（m / rad / kg / kg·m²）",
                             "meters_per_unit": mpu,
                             "model_diagonal_m": _r(model_diag_m, 6),
                             "scene_units_suspect": units_suspect,
                             "note": "场景单位 × meters_per_unit = 米；OBJ 顶点已是米；"
                                     "meters_per_unit 可传 \"scene\" 读 scene.unit_settings.scale_length"},
                   "warnings": warnings, "self_check": chk,
                   "ms": int((time.perf_counter() - t0) * 1000), "note": str(note)})
    finally:
        st.update((k, v) for k, v in locals().items() if k != "st")

def motion_export_urdf(dir=None, name="model", joints=None, meters_per_unit=0.001,
                       density=DEFAULT_DENSITY, masses=None, mesh_format="obj",
                       effort=DEFAULT_EFFORT, velocity=DEFAULT_VELOCITY, fixed_base=False, note=""):
    """写 <dir>/<name>.urdf + 每 link 一个网格（OBJ）+ warnings；写完做结构自检。

    单位：URDF 输出 SI（m / rad / kg / kg·m²）；场景单位按 meters_per_unit 换算
          （默认 0.001 = 场景 1 单位 = 1 mm，与上游 stage 约定一致；米制场景请传 1.0）。
    惯量：bbox 近似（质心=bbox 中心，盒体公式，填充系数 BBOX_FILL_FACTOR=0.4，质量=体积×密度）。
    """
    st = dict(locals())
    for _fn_ in (_motion_export_urdf_p1, _motion_export_urdf_p2, _motion_export_urdf_p3):
        _r = _fn_(st)
        if _r is not None:
            return _r


def _write_obj(path, V, F, header):
    lines = ["# " + header]
    for p in V:
        lines.append("v %.9g %.9g %.9g" % (float(p[0]), float(p[1]), float(p[2])))
    for t in F:
        lines.append("f %d %d %d" % (int(t[0]) + 1, int(t[1]) + 1, int(t[2]) + 1))
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")


def _write_stl(path, V, F, name):
    with open(path, "w", encoding="utf-8") as f:
        f.write("solid %s\n" % _safe_name(name))
        for t in F:
            a, b, c = V[int(t[0])], V[int(t[1])], V[int(t[2])]
            n = np.cross(b - a, c - a)
            ln = float(np.linalg.norm(n))
            n = (n / ln) if ln > 1e-30 else np.zeros(3)
            f.write("  facet normal %.6g %.6g %.6g\n    outer loop\n" % (n[0], n[1], n[2]))
            for p in (a, b, c):
                f.write("      vertex %.9g %.9g %.9g\n" % (float(p[0]), float(p[1]), float(p[2])))
            f.write("    endloop\n  endfacet\n")
        f.write("endsolid %s\n" % _safe_name(name))


def _urdf_self_check(text, path, meshdir, mesh_files, root_key):
    problems = []
    chk = {"xml_parse": False, "link_names_unique": False, "joint_names_unique": False,
           "joint_endpoints_exist": False, "single_tree": False, "acyclic": False,
           "reachable_all": False, "mesh_files_exist": False,
           "counts": {"links": 0, "joints": 0, "meshes": len(mesh_files)},
           "problems": problems}
    try:
        root = ET.fromstring(text)
        chk["xml_parse"] = True
    except Exception as e:
        problems.append("XML 解析失败: %s: %s" % (type(e).__name__, str(e)[:160]))
        chk["ok"] = False
        return chk
    if root.tag != "robot":
        problems.append("根元素不是 <robot> 而是 <%s>" % root.tag)
    lnames = [e.get("name") for e in root.findall("link")]
    jnames = [e.get("name") for e in root.findall("joint")]
    chk["counts"]["links"] = len(lnames)
    chk["counts"]["joints"] = len(jnames)
    chk["link_names_unique"] = (len(set(lnames)) == len(lnames))
    if not chk["link_names_unique"]:
        problems.append("link 名字有重复")
    chk["joint_names_unique"] = (len(set(jnames)) == len(jnames))
    if not chk["joint_names_unique"]:
        problems.append("joint 名字有重复")
    lset = set(lnames)
    ok_end = True
    kids, indeg = {}, {}
    for e in root.findall("joint"):
        pe = e.find("parent"); ce = e.find("child")
        p = pe.get("link") if pe is not None else None
        c = ce.get("link") if ce is not None else None
        if p not in lset or c not in lset:
            ok_end = False
            problems.append("joint %s 的 parent/child 不在 link 列表里（%s → %s）" % (e.get("name"), p, c))
        else:
            kids.setdefault(p, []).append(c)
            indeg[c] = indeg.get(c, 0) + 1
    chk["joint_endpoints_exist"] = ok_end
    multi = [c for c, n in indeg.items() if n > 1]
    chk["single_tree"] = not multi
    if multi:
        problems.append("有 link 被多个关节当 child（不是单树）: %s" % ", ".join(multi))
    start = None
    for n in lnames:
        if indeg.get(n, 0) == 0:
            start = n
            break
    seen, stack, cyclic = set(), list(kids.get(start, [])), False
    if start is not None:
        seen.add(start)
        while stack:
            cur = stack.pop()
            if cur in seen:
                cyclic = True
                break
            seen.add(cur)
            stack.extend(kids.get(cur, []))
    chk["acyclic"] = (start is not None) and (not cyclic)
    if start is None:
        problems.append("没有无父 link（根不唯一或成环）")
    elif cyclic:
        problems.append("关节树里有环")
    chk["reachable_all"] = bool(start is not None) and (len(seen) == len(lnames))
    if not chk["reachable_all"]:
        problems.append("从根 %s 不可达的 link: %s"
                        % (start, ", ".join(sorted(lset - seen))))
    missing = []
    for fn in mesh_files.values():
        if not os.path.isfile(os.path.join(meshdir, fn)):
            missing.append(fn)
    chk["mesh_files_exist"] = not missing
    if missing:
        problems.append("mesh 文件不存在: %s" % ", ".join(missing))
    chk["root_link"] = start
    chk["ok"] = bool(chk["xml_parse"] and chk["link_names_unique"] and chk["joint_names_unique"]
                     and chk["joint_endpoints_exist"] and chk["single_tree"] and chk["acyclic"]
                     and chk["reachable_all"] and chk["mesh_files_exist"])
    return chk
