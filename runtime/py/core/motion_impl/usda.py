# -*- coding: utf-8 -*-
"""Split from motion.py; behavior preserved by the facade."""
from .common import *  # noqa: F401,F403
from .urdf import _build_tree, _group_conflicts, _select_joints, _urdf_type



# ============================================================ USDA 导出

def _usda_mat(T):
    return ("( (1, 0, 0, 0), (0, 1, 0, 0), (0, 0, 1, 0), (%s, %s, %s, 1) )"
            % (_fnum(T[0]), _fnum(T[1]), _fnum(T[2])))


def _motion_export_usda_p1(st):
    dir, fixed_base, joints, meters_per_unit, name = (
        st["dir"], st["fixed_base"], st["joints"], st["meters_per_unit"], st["name"])
    _L, _mn, _mx, _v = (st.get("_L"), st.get("_mn"), st.get("_mx"), st.get("_v"))
    try:
        t0 = time.perf_counter()
        if not dir:
            return _j({"ok": False, "error": "dir 必填（USDA 写到哪）"})
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
        mpu = _mpu_arg(meters_per_unit)
        robot = _safe_name(name, "model")
        outdir = _win(dir)
        warnings = []
        try:
            os.makedirs(outdir, exist_ok=True)
        except Exception as e:
            return _j({"ok": False, "error": "建目录失败: %s: %s" % (type(e).__name__, e), "dir": outdir})
        tree = _build_tree(recs, warnings)
        if tree is None:
            return _j({"ok": False, "error": "关节树为空（没有 link）"})
        links, edges, root = tree["links"], tree["edges"], tree["root"]
        dg = bpy.context.evaluated_depsgraph_get()
        _allV = []
        for _k, _L in links.items():
            _v, _f, _p = _group_tris(_objs(_L["names"])[0], dg)
            if len(_v):
                _allV.append(_v)
        model_diag_m = 0.0
        if _allV:
            _mn, _mx, _d = _bbox(np.concatenate(_allV, axis=0))
            if _mn is not None:
                model_diag_m = float(np.linalg.norm(_mx - _mn)) * mpu
        units_suspect = _units_warnings(mpu, model_diag_m, warnings)

        L = ["#usda 1.0",
             "(",
             '    doc = "DSH motion v%d 最小可用导出：结构可读，**不是完整 UsdPhysics schema**；'
             '关节元数据在 dsh:* 自定义属性里"' % MOTION_VERSION,
             '    defaultPrim = "%s"' % _safe_name(robot),
             '    metersPerUnit = %s' % _fnum(mpu),
             '    upAxis = "Z"',
             ")",
             "",
             'def Xform "%s"' % _safe_name(robot),
             "{"]
        if fixed_base:
            L += ['    def Xform "World"', "    {", "    }", ""]
        link_mesh_meta, joint_meta = {}, []
        L += ['    def Scope "Links"', "    {"]
    finally:
        st.update((k, v) for k, v in locals().items() if k != "st")

def _motion_export_usda_p2(st):
    L, density, dg, edges, joint_meta = (
        st.get("L"), st["density"], st.get("dg"), st.get("edges"), st.get("joint_meta"))
    link_mesh_meta, links, masses, mpu, warnings = (
        st.get("link_mesh_meta"), st.get("links"), st["masses"], st.get("mpu"), st.get("warnings"))
    F, Lk, V, Vl, axis, cf, child, ck, degraded, dims, e, frame, jname, k, key, lim, m_explicit, mass, mn, mx, objs, org, p, parent, pf, pts, r, utype, vol = (st.get("F"), st.get("Lk"), st.get("V"), st.get("Vl"), st.get("axis"), st.get("cf"), st.get("child"), st.get("ck"), st.get("degraded"), st.get("dims"), st.get("e"), st.get("frame"), st.get("jname"), st.get("k"), st.get("key"), st.get("lim"), st.get("m_explicit"), st.get("mass"), st.get("mn"), st.get("mx"), st.get("objs"), st.get("org"), st.get("p"), st.get("parent"), st.get("pf"), st.get("pts"), st.get("r"), st.get("utype"), st.get("vol"))
    try:
        for k in sorted(links, key=lambda x: links[x]["name"]):
            Lk = links[k]
            objs, _m, _n = _objs(Lk["names"])
            V, F, _p = _group_tris(objs, dg)
            frame = np.asarray(Lk["frame"] or [0.0, 0.0, 0.0], dtype=np.float64)
            L.append('        def Xform "%s"' % _safe_name(Lk["name"]))
            L.append("        {")
            L.append("            matrix4d xformOp:transform = %s" % _usda_mat(frame))
            L.append('            uniform token[] xformOpOrder = ["xformOp:transform"]')
            L.append('            custom string dsh:objects = "%s"' % _esc(",".join(Lk["names"])))
            if len(V) and len(F):
                Vl = (V - frame)          # 场景单位（metersPerUnit 已声明换算）
                mn, mx = Vl.min(axis=0), Vl.max(axis=0)
                dims = (mx - mn).tolist()
                vol = max(0.0, dims[0] * dims[1] * dims[2]) * (mpu ** 3)
                m_explicit = None
                for key in (Lk["name"],) + tuple(Lk["names"]):
                    if masses and key in masses:
                        m_explicit = float(masses[key])
                        break
                mass = m_explicit if m_explicit is not None else float(density) * vol * BBOX_FILL_FACTOR
                L.append('            custom double dsh:massKg = %s' % _fnum(mass))
                L.append('            custom double3 dsh:bboxSizeM = (%s, %s, %s)'
                         % tuple(_fnum(d * mpu) for d in dims))
                L.append('            def Mesh "mesh"')
                L.append("            {")
                L.append("                int[] faceVertexCounts = [%s]" % ", ".join(["3"] * len(F)))
                L.append("                int[] faceVertexIndices = [%s]" % ", ".join(str(int(i)) for i in F.ravel()))
                pts = []
                for p in Vl:
                    pts.append("(%s, %s, %s)" % (_fnum(p[0]), _fnum(p[1]), _fnum(p[2])))
                L.append("                point3f[] points = [%s]" % ", ".join(pts))
                L.append('                uniform token subdivisionScheme = "none"')
                L.append("            }")
                link_mesh_meta[Lk["name"]] = {"points": int(len(Vl)), "faces": int(len(F))}
            else:
                warnings.append("link %s 没有几何 → 只有 Xform，没有 Mesh" % Lk["name"])
                link_mesh_meta[Lk["name"]] = {"points": 0, "faces": 0}
            L.append("        }")
        L += ["    }", ""]
        L += ['    def Scope "Joints"', "    {"]
        for ck in sorted(edges, key=lambda x: links[x]["name"]):
            e = edges[ck]
            r = e["rec"]
            child = links[ck]
            parent = links[e["parent"]]
            pf = np.asarray(parent["frame"] or [0.0, 0.0, 0.0], dtype=np.float64)
            cf = np.asarray(child["frame"] or [0.0, 0.0, 0.0], dtype=np.float64)
            org = cf - pf
            jname = _safe_name(e["jid"], "joint")
            utype, degraded = (_urdf_type(r, []) if r is not None else ("fixed", None))
            axis = (r.get("axis") if r is not None else None) or [0.0, 0.0, 1.0]
            L.append('        def Xform "%s"' % jname)
            L.append("        {")
            L.append('            custom string dsh:kind = "%s"' % (r["kind"] if r is not None else "fixed"))
            L.append('            custom string dsh:urdfType = "%s"' % utype)
            if degraded:
                L.append('            custom string dsh:degradedFrom = "%s"' % _esc(degraded))
                L.append('            custom string dsh:boundary = "URDF/USDA 均无法表达 %s；已按 fixed 记录"'
                         % _esc(degraded))
            L.append('            custom string dsh:parentLink = "%s"' % _esc(parent["name"]))
            L.append('            custom string dsh:childLink = "%s"' % _esc(child["name"]))
            L.append("            custom double3 dsh:axis = (%s, %s, %s)"
                     % tuple(_fnum(x) for x in axis))
            L.append("            custom double3 dsh:anchor = (%s, %s, %s)"
                     % tuple(_fnum(x) for x in cf))
            if r is not None and r.get("limits"):
                if utype == "revolute":
                    lim = (math.radians(float(r["limits"][0])), math.radians(float(r["limits"][1])))
                    L.append("            custom double2 dsh:limitsRad = (%s, %s)"
                             % (_fnum(lim[0]), _fnum(lim[1])))
                else:
                    L.append("            custom double2 dsh:limits = (%s, %s)"
                             % (_fnum(r["limits"][0]), _fnum(r["limits"][1])))
            L.append('            custom bool dsh:synthesized = %s'
                     % ("true" if e.get("synthesized") else "false"))
            L.append('            custom string dsh:body0 = "%s"' % _esc(parent["name"]))
            L.append('            custom string dsh:body1 = "%s"' % _esc(child["name"]))
            L.append("            matrix4d xformOp:transform = %s" % _usda_mat(org))
            L.append('            uniform token[] xformOpOrder = ["xformOp:transform"]')
            L.append("        }")
            joint_meta.append({"joint": e["jid"], "kind": (r["kind"] if r is not None else "fixed"),
                               "type": utype, "degraded_from": degraded,
                               "parent": parent["name"], "child": child["name"]})
        L += ["    }", "}"]
    finally:
        st.update((k, v) for k, v in locals().items() if k != "st")

def _motion_export_usda_p3(st):
    L, edges, joint_meta, link_mesh_meta, links = (
        st.get("L"), st.get("edges"), st.get("joint_meta"), st.get("link_mesh_meta"), st.get("links"))
    model_diag_m, mpu, note, outdir, robot = (
        st.get("model_diag_m"), st.get("mpu"), st["note"], st.get("outdir"), st.get("robot"))
    root, t0, units_suspect, warnings = (
        st.get("root"), st.get("t0"), st.get("units_suspect"), st.get("warnings"))
    f = (st.get("f"))
    try:
        text = "\n".join(L) + "\n"
        path = os.path.join(outdir, robot + ".usda")
        try:
            with open(path, "w", encoding="utf-8") as f:
                f.write(text)
        except Exception as e:
            return _j({"ok": False, "error": "写 USDA 失败: %s: %s" % (type(e).__name__, e), "path": path})
        chk = _usda_self_check(text, path, link_mesh_meta, joint_meta)
        rec_export = {"kind": "usda", "path": path, "dir": outdir, "meshes": len(link_mesh_meta),
                      "bytes": os.path.getsize(path), "links": len(links), "joints": len(edges),
                      "self_check_ok": chk["ok"], "warnings": len(warnings), "t": _now(),
                      "note": str(note)}
        _store()["exports"].append(rec_export)
        del _store()["exports"][:-50]
        _log("export_usda", {"path": path, "links": len(links), "joints": len(edges),
                             "self_check_ok": chk["ok"]})
        if not chk["ok"]:
            return _j({"ok": False, "error": "USDA 结构自检未通过（产物已写出，但不合格）",
                       "path": path, "self_check": chk, "warnings": warnings})
        return _j({"ok": True, "op": "export_usda", "path": path, "dir": outdir, "robot": robot,
                   "root_link": links[root]["name"], "links": link_mesh_meta, "joints": joint_meta,
                   "units": {"usda": "metersPerUnit=%g（points/xform 用场景单位）" % mpu,
                             "model_diagonal_m": _r(model_diag_m, 6),
                             "scene_units_suspect": units_suspect,
                             "upAxis": "Z"},
                   "warnings": warnings, "self_check": chk,
                   "ms": int((time.perf_counter() - t0) * 1000), "note": str(note)})
    finally:
        st.update((k, v) for k, v in locals().items() if k != "st")

def motion_export_usda(dir=None, name="model", joints=None, meters_per_unit=0.001,
                       density=DEFAULT_DENSITY, masses=None, fixed_base=False, note=""):
    """写最小可用 ASCII USDA：Xform（含 matrix4d xformOp:transform）+ UsdGeom.Mesh + 关节自定义属性。

    如实说明：**不是完整 USD Physics schema**（没有 UsdPhysics.Joint/Drive/MassAPI 的 schema 语义），
    只保证结构可读：mesh 的 points/faceVertexCounts/faceVertexIndices 齐全、xform 正确、关节元数据齐全。
    """
    st = dict(locals())
    for _fn_ in (_motion_export_usda_p1, _motion_export_usda_p2, _motion_export_usda_p3):
        _r = _fn_(st)
        if _r is not None:
            return _r


def _usda_self_check(text, path, link_mesh_meta, joint_meta):
    problems = []
    lines = text.split("\n")
    n_open = text.count("{")
    n_close = text.count("}")
    n_paren_o = text.count("(")
    n_paren_c = text.count(")")
    chk = {"header": text.startswith("#usda 1.0"), "brace_balance": n_open == n_close,
           "paren_balance": n_paren_o == n_paren_c, "lines": len(lines),
           "links": len(link_mesh_meta), "joints": len(joint_meta),
           "meshes": sum(1 for v in link_mesh_meta.values() if v["faces"] > 0),
           "mesh_arrays_consistent": True, "indices_in_range": True,
           "problems": problems,
           "boundary": "结构级自检（本机没有 USD 运行库）：括号/圆括号配平、数组长度与索引范围一致性；"
                       "不是 schema 校验"}
    if not chk["header"]:
        problems.append("首行不是 #usda 1.0")
    if not chk["brace_balance"]:
        problems.append("花括号不配平: { =%d, } =%d" % (n_open, n_close))
    if not chk["paren_balance"]:
        problems.append("圆括号不配平: ( =%d, ) =%d" % (n_paren_o, n_paren_c))
    # 逐块检查 Mesh 数组一致性
    def _arr(s):
        """取行内最后一个 [...] 的内容（注意 int[] 这种声明里也有方括号）。"""
        i, j = s.rfind("["), s.rfind("]")
        if i < 0 or j < i:
            raise ValueError("行内没有数组: %s" % s[:80])
        return s[i + 1:j]

    i = 0
    n_mesh = 0
    while i < len(lines):
        ln = lines[i]
        if "faceVertexCounts" in ln:
            n_mesh += 1
            try:
                counts = [int(x) for x in _arr(ln).split(",") if x.strip()]
                idx = [int(x) for x in _arr(lines[i + 1]).split(",") if x.strip()]
                pts = lines[i + 2].count("(")
                if sum(counts) != len(idx):
                    chk["mesh_arrays_consistent"] = False
                    problems.append("第 %d 行 Mesh：sum(faceVertexCounts)=%d ≠ len(faceVertexIndices)=%d"
                                    % (i + 1, sum(counts), len(idx)))
                if idx and (min(idx) < 0 or max(idx) >= pts):
                    chk["indices_in_range"] = False
                    problems.append("第 %d 行 Mesh：索引范围 [%d, %d] 越界（points=%d）"
                                    % (i + 1, min(idx), max(idx), pts))
                if not counts or any(c != 3 for c in counts):
                    problems.append("第 %d 行 Mesh：存在非三角面（本项目只导出三角面）" % (i + 1))
            except Exception as e:
                chk["mesh_arrays_consistent"] = False
                problems.append("第 %d 行 Mesh 数组解析失败: %s" % (i + 1, str(e)[:120]))
        i += 1
    if n_mesh != chk["meshes"]:
        problems.append("Mesh 块数 %d ≠ 有几何的 link 数 %d" % (n_mesh, chk["meshes"]))
        chk["mesh_arrays_consistent"] = False
    if len(joint_meta) != sum(1 for ln in lines if "dsh:kind" in ln):
        problems.append("关节块数 %d ≠ dsh:kind 出现次数 %d"
                        % (len(joint_meta), sum(1 for ln in lines if "dsh:kind" in ln)))
    if os.path.getsize(path) <= 0:
        problems.append("文件是空的")
    chk["ok"] = bool(chk["header"] and chk["brace_balance"] and chk["paren_balance"]
                     and chk["mesh_arrays_consistent"] and chk["indices_in_range"]
                     and chk["links"] > 0 and not any("≠" in p or "不配平" in p for p in problems))
    return chk
