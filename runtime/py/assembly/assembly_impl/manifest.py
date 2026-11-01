# -*- coding: utf-8 -*-
"""Split from assembly.py; behavior preserved by the facade."""
from .common import *  # noqa: F401,F403

def _manifest_init(assembly=None, units="mm", coord=None):
    return {"schema": MANIFEST_SCHEMA, "units": str(units or "mm"),
            "coord": dict(coord or {"up": "Z", "forward": "-Y"}),
            "assembly": str(assembly or "unnamed-assembly"),
            "version": 1, "sealed": False, "sealed_md5": None,
            "parts": [], "interfaces": [], "allow_pairs": []}


def _load_manifest(src):
    """manifest = dict | JSON 字符串 | 文件路径 → (dict, 错误)。"""
    if src is None or src == "":
        return None, "manifest 没给（需要 dict / JSON 字符串 / .json 路径）"
    if isinstance(src, dict):
        try:
            return json.loads(json.dumps(src, default=str)), None
        except Exception as e:
            return None, "manifest dict 无法深拷贝: %s: %s" % (type(e).__name__, str(e)[:160])
    s = str(src)
    if s.strip().startswith("{"):
        try:
            m = json.loads(s)
        except Exception as e:
            return None, "manifest JSON 解析失败: %s: %s" % (type(e).__name__, str(e)[:160])
        return (m, None) if isinstance(m, dict) else (None, "manifest 必须是 JSON 对象")
    p = _path(s)
    if not os.path.isfile(p):
        return None, "manifest 文件不存在: %s" % p
    try:
        with open(p, "r", encoding="utf-8") as fh:
            m = json.load(fh)
    except Exception as e:
        return None, "manifest 读取失败: %s: %s" % (type(e).__name__, str(e)[:160])
    return (m, None) if isinstance(m, dict) else (None, "manifest 文件内容必须是 JSON 对象")


def _manifest_md5(m):
    """清单内容指纹（canonical JSON，排除 manifest_md5 / sealed_md5 两个自指字段）。"""
    if not isinstance(m, dict):
        return None
    d = {k: v for k, v in m.items() if k not in ("manifest_md5", "sealed_md5")}
    return _md5_text(_canon(d))


def _write_json(path, obj):
    p = _path(path)
    d = os.path.dirname(p)
    if d and not os.path.isdir(d):
        os.makedirs(d, exist_ok=True)
    tmp = p + ".tmp%d" % os.getpid()
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(obj, fh, ensure_ascii=False, indent=2)
        fh.write("\n")
    os.replace(tmp, p)
    return p


def _manifest_counts(m):
    return {"parts": len(m.get("parts") or []), "interfaces": len(m.get("interfaces") or []),
            "allow_pairs": len(m.get("allow_pairs") or [])}


def _validate_frame(fr, pid, fid, E, W):
    origin = fr.get("origin_mm")
    if origin is not None:
        if not isinstance(origin, (list, tuple)) or len(origin) != 3 or any(_num(x) is None for x in origin):
            E("FRAME_BAD_ORIGIN", "part %s frame %s: origin_mm 必须是 3 个数字" % (pid, fid), pid)
    normal = fr.get("normal")
    if normal is None:
        W("FRAME_NO_NORMAL", "part %s frame %s: 没有 normal，接口朝向未定义" % (pid, fid), pid)
    else:
        if not isinstance(normal, (list, tuple)) or len(normal) != 3 or any(_num(x) is None for x in normal):
            E("FRAME_BAD_NORMAL", "part %s frame %s: normal 必须是 3 个数字" % (pid, fid), pid)
        elif float(np.linalg.norm(np.array([float(x) for x in normal]))) < 1e-9:
            E("FRAME_BAD_NORMAL", "part %s frame %s: normal 长度 0" % (pid, fid), pid)
    basis = fr.get("basis")
    if basis is None:
        W("FRAME_NO_BASIS", "part %s frame %s: 没有 basis（x/y），绕法线的旋转未定" % (pid, fid), pid)
        return
    try:
        if isinstance(basis, dict):
            bx, by = basis.get("x"), basis.get("y")
        elif isinstance(basis, (list, tuple)) and len(basis) >= 2:
            bx, by = basis[0], basis[1]
        else:
            raise ValueError("basis 形态不认识")
        bx = np.array([float(x) for x in bx], dtype=np.float64)
        by = np.array([float(x) for x in by], dtype=np.float64)
        if bx.shape != (3,) or by.shape != (3,):
            raise ValueError("x/y 必须是 3 个数字")
    except Exception as e:
        E("FRAME_BAD_BASIS", "part %s frame %s: basis 非法（%s）" % (pid, fid, str(e)[:100]), pid)
        return
    nx, ny = float(np.linalg.norm(bx)), float(np.linalg.norm(by))
    if abs(nx - 1.0) > 1e-3 or abs(ny - 1.0) > 1e-3:
        E("FRAME_BASIS_NOT_UNIT", "part %s frame %s: basis 不是单位向量（|x|=%.6f |y|=%.6f）"
          % (pid, fid, nx, ny), pid)
    dot = float(np.dot(bx, by))
    if abs(dot) > 1e-3:
        E("FRAME_BASIS_NOT_ORTHOGONAL", "part %s frame %s: basis x·y=%.6f（要求 ≈0）" % (pid, fid, dot), pid)
    if normal is not None:
        try:
            nrm = np.array([float(x) for x in normal], dtype=np.float64)
            z = np.cross(bx, by)
            zn = float(np.linalg.norm(z))
            if zn > 1e-9:
                cosang = float(np.dot(z / zn, nrm / max(1e-12, float(np.linalg.norm(nrm)))))
                if cosang < 0.9:
                    W("FRAME_NORMAL_MISMATCH",
                      "part %s frame %s: normal 与 basis 叉乘方向不一致（cos=%.3f），合并对齐可能差 180°"
                      % (pid, fid, cosang), pid)
        except Exception:
            pass
def _manifest_validate_p1(st):
    m, mmu = st["m"], st.get("mmu")
    try:
        errors, warnings = [], []

        def E(code, msg, path=None):
            errors.append({"code": str(code), "msg": str(msg)[:400], "path": (str(path) if path is not None else None)})

        def W(code, msg, path=None):
            warnings.append({"code": str(code), "msg": str(msg)[:400], "path": (str(path) if path is not None else None)})

        if not isinstance(m, dict):
            E("BAD_MANIFEST", "manifest 必须是 JSON 对象")
            return {"ok": False, "verdict": "fail", "errors": errors, "warnings": warnings,
                    "manifest_md5": None, "counts": {"parts": 0, "interfaces": 0, "allow_pairs": 0}}
        mmu = float(mmu if mmu is not None else _KIT.units())
        schema = m.get("schema")
        if schema is None:
            W("NO_SCHEMA", "manifest 没有 schema（建议 %s）" % MANIFEST_SCHEMA)
        elif str(schema) != MANIFEST_SCHEMA:
            W("SCHEMA_MISMATCH", "schema=%r ≠ %s（按 v1 尽力校验）" % (schema, MANIFEST_SCHEMA))
        units = str(m.get("units") or "mm")
        conv = UNIT_MM_PER_UNIT.get(units)
        if conv is None:
            E("BAD_UNITS", "units 只支持 mm/cm/m，收到 %r" % units)
        elif abs(mmu - conv) / conv > 0.01:
            W("UNIT_SCALE_SUSPECT",
              "manifest units=%s ⇒ 期望 1 单位=%.3g mm，实测 %.6g mm（把 scene.unit_settings.scale_length "
              "设成 %.6g；否则 loc_mm/深度全部按错单位读）" % (units, conv, mmu, conv / 1000.0))

        parts = m.get("parts")
        if parts is None:
            parts = []
        if not isinstance(parts, list):
            E("PARTS_NOT_LIST", "parts 必须是数组")
            parts = []
        ids, by_id, frames_by_part = set(), {}, {}
    finally:
        st.update((k, v) for k, v in locals().items() if k != "st")

def _manifest_validate_p2(st):
    E, W, by_id, check_files, check_md5 = (
        st.get("E"), st.get("W"), st.get("by_id"), st["check_files"], st["check_md5"])
    frames_by_part, ids, m, parts = st.get("frames_by_part"), st.get("ids"), st["m"], st.get("parts")
    f, fid, fids, fp, fr, frames, got, i, k, p, pid, tr, v, want = (st.get("f"), st.get("fid"), st.get("fids"), st.get("fp"), st.get("fr"), st.get("frames"), st.get("got"), st.get("i"), st.get("k"), st.get("p"), st.get("pid"), st.get("tr"), st.get("v"), st.get("want"))
    try:
        for i, p in enumerate(parts):
            if not isinstance(p, dict):
                E("BAD_PART", "parts[%d] 不是对象" % i)
                continue
            pid = str(p.get("id") or "")
            if not pid:
                E("MISSING_PART_ID", "parts[%d] 没有 id" % i)
                continue
            if pid in ids:
                E("DUP_ID", "part id 重复: %s" % pid, pid)
                continue
            ids.add(pid)
            by_id[pid] = p
            fp = p.get("file")
            if not fp:
                W("MISSING_FILE", "part %s 没有 file（只能当纯数据清单用，merge 会跳过）" % pid, pid)
            else:
                f = _path(fp)
                if check_files and not os.path.isfile(f):
                    E("MISSING_FILE", "part %s 的 file 不存在: %s" % (pid, f), pid)
                elif check_md5 and os.path.isfile(f):
                    want = str(p.get("file_md5") or "").lower()
                    if not want:
                        W("MISSING_FILE_MD5", "part %s 没有 file_md5，文件无法复核" % pid, pid)
                    else:
                        got = _sha256_file(f)
                        if got is None:
                            W("FILE_UNREADABLE", "part %s 的文件读不了: %s" % (pid, f), pid)
                        elif got != want:
                            E("MD5_MISMATCH", "part %s 文件 md5 不匹配（manifest=%s… 实测=%s…）"
                              % (pid, want[:12], got[:12]), pid)
            tr = p.get("transform")
            if tr is not None:
                if not isinstance(tr, dict):
                    E("BAD_TRANSFORM", "part %s transform 必须是对象" % pid, pid)
                else:
                    for k in ("loc_mm", "rot_euler_deg", "scale"):
                        if k not in tr:
                            continue
                        v = tr.get(k)
                        if not isinstance(v, (list, tuple)) or len(v) != 3 or any(_num(x) is None for x in v):
                            E("BAD_TRANSFORM", "part %s transform.%s 必须是 3 个数字" % (pid, k), pid)
                            continue
                        if k == "scale" and any(float(x) <= 0.0 for x in v):
                            E("BAD_TRANSFORM", "part %s transform.scale 必须 > 0" % pid, pid)
            frames = p.get("interface_frames")
            fids = set()
            if frames is not None:
                if not isinstance(frames, list):
                    E("BAD_FRAMES", "part %s interface_frames 必须是数组" % pid, pid)
                else:
                    for fr in frames:
                        if not isinstance(fr, dict):
                            E("BAD_FRAME", "part %s 有 interface_frame 不是对象" % pid, pid)
                            continue
                        fid = str(fr.get("id") or "")
                        if not fid:
                            E("MISSING_FRAME_ID", "part %s 有 interface_frame 缺 id" % pid, pid)
                            continue
                        if fid in fids:
                            W("FRAME_ID_DUP", "part %s frame id 重复: %s" % (pid, fid), pid)
                            continue
                        fids.add(fid)
                        _validate_frame(fr, pid, fid, E, W)
            frames_by_part[pid] = fids

        interfaces = m.get("interfaces") or []
        if not isinstance(interfaces, list):
            E("INTERFACES_NOT_LIST", "interfaces 必须是数组")
            interfaces = []
        iids, iface_ids = set(), set()
    finally:
        st.update((k, v) for k, v in locals().items() if k != "st")

def _manifest_validate_p3(st):
    E, W, by_id, conv, errors = st.get("E"), st.get("W"), st.get("by_id"), st.get("conv"), st.get("errors")
    frames_by_part, iface_ids, iids, interfaces, m = (
        st.get("frames_by_part"), st.get("iface_ids"), st.get("iids"), st.get("interfaces"), st["m"])
    mmu, units, warnings = st.get("mmu"), st.get("units"), st.get("warnings")
    a, aid, ap, b, fa, fb, fit, got, i, iid, it, k, md_, mv, nv, ref, v, want = (st.get("a"), st.get("aid"), st.get("ap"), st.get("b"), st.get("fa"), st.get("fb"), st.get("fit"), st.get("got"), st.get("i"), st.get("iid"), st.get("it"), st.get("k"), st.get("md_"), st.get("mv"), st.get("nv"), st.get("ref"), st.get("v"), st.get("want"))
    try:
        for i, it in enumerate(interfaces):
            if not isinstance(it, dict):
                E("BAD_INTERFACE", "interfaces[%d] 不是对象" % i)
                continue
            iid = str(it.get("id") or "")
            if not iid:
                W("MISSING_INTERFACE_ID", "interfaces[%d] 没有 id" % i)
                iid = "IF#%d" % i
            if iid in iids:
                E("DUP_ID", "interface id 重复: %s" % iid, iid)
                continue
            iids.add(iid)
            iface_ids.add(iid)
            a, b = str(it.get("a") or ""), str(it.get("b") or "")
            if a not in by_id:
                E("CONN_UNKNOWN_PART", "interface %s: 端 a=%r 不在 parts 里" % (iid, a), iid)
            if b not in by_id:
                E("CONN_UNKNOWN_PART", "interface %s: 端 b=%r 不在 parts 里" % (iid, b), iid)
            if a and a == b:
                E("SELF_INTERFACE", "interface %s 的两端是同一个 part: %s" % (iid, a), iid)
            fa, fb = it.get("frame_a"), it.get("frame_b")
            if fa is not None and a in frames_by_part and str(fa) not in frames_by_part[a]:
                E("FRAME_NOT_FOUND", "interface %s: 端 a=%s 没有 frame %r" % (iid, a, fa), iid)
            if fb is not None and b in frames_by_part and str(fb) not in frames_by_part[b]:
                E("FRAME_NOT_FOUND", "interface %s: 端 b=%s 没有 frame %r" % (iid, b, fb), iid)
            fit = it.get("fit")
            if fit is not None and str(fit) not in FIT_CLASSES:
                W("UNKNOWN_FIT", "interface %s: fit=%r 不在 %s 里（按 location 口径处理）"
                  % (iid, fit, list(FIT_CLASSES)), iid)
            for k in ("nominal_mm", "tol_mm", "allow_overlap_mm", "clearance_min_mm"):
                v = it.get(k)
                if v is None:
                    continue
                nv = _num(v)
                if nv is None or nv < 0:
                    E("BAD_INTERFACE_NUM", "interface %s: %s 必须是非负数字" % (iid, k), iid)

        allow = m.get("allow_pairs") or []
        if not isinstance(allow, list):
            E("ALLOW_NOT_LIST", "allow_pairs 必须是数组")
            allow = []
        aids = set()
        for i, ap in enumerate(allow):
            if not isinstance(ap, dict):
                E("BAD_ALLOW", "allow_pairs[%d] 不是对象" % i)
                continue
            aid = str(ap.get("id") or "")
            if not aid:
                W("MISSING_ALLOW_ID", "allow_pairs[%d] 没有 id" % i)
                aid = "ALLOW#%d" % i
            if aid in aids:
                E("DUP_ID", "allow id 重复: %s" % aid, aid)
                continue
            aids.add(aid)
            a, b = str(ap.get("a") or ""), str(ap.get("b") or "")
            if a not in by_id:
                E("ALLOW_UNKNOWN_PART", "allow %s: 端 a=%r 不在 parts 里" % (aid, a), aid)
            if b not in by_id:
                E("ALLOW_UNKNOWN_PART", "allow %s: 端 b=%r 不在 parts 里" % (aid, b), aid)
            ref = ap.get("interface")
            if ref is not None and str(ref) not in iface_ids:
                W("ALLOW_UNKNOWN_CONN", "allow %s: 引用了不存在的 interface %r" % (aid, ref), aid)
            md_ = _num(ap.get("max_depth_mm"))
            mv = _num(ap.get("max_volume_mm3"))
            if md_ is None and mv is None:
                E("ALLOW_NO_LIMIT", "allow %s: 必须给 max_depth_mm 或 max_volume_mm3（无上限的允许交叠是危险默认）" % aid, aid)
            for k, nv in (("max_depth_mm", md_), ("max_volume_mm3", mv)):
                if nv is not None and nv < 0:
                    E("BAD_ALLOW_NUM", "allow %s: %s 必须 ≥ 0" % (aid, k), aid)

        sealed = bool(m.get("sealed"))
        if sealed:
            want = str(m.get("sealed_md5") or "")
            got = _manifest_md5(m)
            if want and got and want != got:
                E("MANIFEST_SEAL_MISMATCH", "清单在 seal 之后被改过（sealed_md5=%s… 实测=%s…）"
                  % (want[:12], got[:12]))
        return {"ok": not errors, "verdict": "pass" if not errors else "fail",
                "errors": errors, "warnings": warnings, "manifest_md5": _manifest_md5(m),
                "sealed": sealed, "counts": _manifest_counts(m),
                "units": {"manifest_units": units, "mm_per_unit_scene": _r(mmu, 6),
                          "expected_mm_per_unit": conv}}
    finally:
        st.update((k, v) for k, v in locals().items() if k != "st")

def _manifest_validate(m, check_files=False, check_md5=False, mmu=None):
    """清单校验 → {ok, verdict, errors[], warnings[], manifest_md5, counts, units}。

    校验：id 唯一 / file 存在 / file_md5 / 接口两端存在（含 frame）/ basis 正交 / 单位一致 / seal 完整性。
    check_files / check_md5 默认关（gate 只看结构；validate 动作默认开）。
    """
    st = dict(locals())
    for _fn_ in (_manifest_validate_p1, _manifest_validate_p2, _manifest_validate_p3):
        _r = _fn_(st)
        if _r is not None:
            return _r
def _obj_part_map(manifest, names):
    """场景对象名 → part id：显式 objects 列表 / 同名 / 合并命名 <partid>__ 前缀。"""
    out = {}
    if not isinstance(manifest, dict):
        return out
    for p in manifest.get("parts") or []:
        if not isinstance(p, dict):
            continue
        pid = str(p.get("id") or "")
        if not pid:
            continue
        for o in _as_list(p.get("objects")):
            out[str(o)] = pid
        for n in names:
            if n == pid or n.startswith(pid + "__"):
                out[str(n)] = pid
    return out


def _effective_spec(manifest, spec):
    """把 manifest + spec 合并成白名单：allow / clearance / interfaces。"""
    sp = dict(spec) if isinstance(spec, dict) else {}
    allow = [dict(x) for x in (sp.get("allow") or []) if isinstance(x, dict)]
    clearance = [dict(x) for x in (sp.get("clearance") or []) if isinstance(x, dict)]
    interfaces = [dict(x) for x in (sp.get("interfaces") or []) if isinstance(x, dict)]
    if isinstance(manifest, dict):
        for x in manifest.get("interfaces") or []:
            if isinstance(x, dict):
                interfaces.append(dict(x))
        for ap in manifest.get("allow_pairs") or []:
            if isinstance(ap, dict):
                allow.append(dict(ap))
        for it in manifest.get("interfaces") or []:
            if not isinstance(it, dict):
                continue
            ov = _num(it.get("allow_overlap_mm"))
            if ov is not None and ov > 0:
                allow.append({"id": "IF-ALLOW-%s" % it.get("id"), "a": it.get("a"), "b": it.get("b"),
                              "max_depth_mm": ov, "max_volume_mm3": None,
                              "reason": "interface %s 的 allow_overlap_mm=%.4g" % (it.get("id"), ov),
                              "interface": it.get("id")})
    return {"allow": allow, "clearance": clearance, "interfaces": interfaces,
            "micro_gap_mm": _num(sp.get("micro_gap_mm"))}


def _allow_match(allow_list, obj_part, na, nb):
    """allow 条目 a/b 可以是对象名，也可以是 part id（含合并前缀 <partid>__）。"""
    for ent in allow_list or []:
        ea, eb = str(ent.get("a") or ""), str(ent.get("b") or "")
        if not ea or not eb:
            continue

        def _hit(name, side):
            return name == side or obj_part.get(name) == side

        if (_hit(na, ea) and _hit(nb, eb)) or (_hit(na, eb) and _hit(nb, ea)):
            return ent
    return None
def _asm_manifest_p1(st):
    action, assembly, check_files, check_md5, coord = (
        st["action"], st["assembly"], st["check_files"], st["check_md5"], st["coord"])
    force, manifest, note, path, units = st["force"], st["manifest"], st["note"], st["path"], st["units"]
    d, k, p, val = (st.get("d"), st.get("k"), st.get("p"), st.get("val"))
    try:
        t0 = time.perf_counter()
        act = str(action or "show").strip().lower()
        if act == "init":
            m = _manifest_init(assembly, units, coord)
            if isinstance(manifest, dict):
                for k in ("schema", "assembly", "units", "coord", "parts", "interfaces", "allow_pairs"):
                    if k in manifest:
                        m[k] = manifest[k]
            if note:
                m["note"] = str(note)
            if not path:
                return _j(_receipt({"ok": True, "action": "init", "manifest": m,
                                    "manifest_md5": _manifest_md5(m), "path": None,
                                    "note": "没给 path ⇒ 只返回骨架，没有落盘"}, t0))
            if os.path.isfile(_path(path)) and not force:
                return _err("manifest 已存在，拒绝覆盖（force=true 才覆盖）: %s" % _path(path))
            val = _manifest_validate(m, check_files=False, check_md5=False)
            if not val["ok"]:
                return _err("init 清单自校验失败", errors=val["errors"], warnings=val["warnings"])
            try:
                p = _write_json(path, m)
            except Exception as e:
                return _err("manifest 写盘失败: %s: %s" % (type(e).__name__, str(e)[:160]))
            return _j(_receipt({"ok": True, "action": "init", "path": p, "manifest_md5": _manifest_md5(m),
                                "counts": _manifest_counts(m), "warnings": val["warnings"],
                                "next": "add_part → add_interface → add_allow(可选) → validate → seal"}, t0))
        src = manifest if manifest is not None else path
        m, merr = _load_manifest(src)
        if merr:
            return _err(merr)
        if act == "validate":
            val = _manifest_validate(m, check_files=bool(check_files), check_md5=bool(check_md5))
            d = {"ok": bool(val["ok"]), "action": "validate", "verdict": val["verdict"],
                 "path": _path(path) if path else None, "manifest_md5": val["manifest_md5"],
                 "errors": val["errors"], "warnings": val["warnings"],
                 "counts": val["counts"], "sealed": val["sealed"], "units": val["units"]}
            if not val["ok"]:
                d["hint"] = "先修 errors；manifest 自身不合法时 merge/gate 会拒绝出结论"
            return _j(_receipt(d, t0))
        if act == "show":
            val = _manifest_validate(m, check_files=False, check_md5=False)
            return _j(_receipt({"ok": bool(val["ok"]), "action": "show", "path": _path(path) if path else None,
                                "manifest": m, "manifest_md5": _manifest_md5(m),
                                "counts": _manifest_counts(m), "sealed": bool(m.get("sealed")),
                                "validation": {"verdict": val["verdict"],
                                               "errors": val["errors"][:20], "warnings": val["warnings"][:20]}},
                               t0))
        if act not in ("add_part", "add_interface", "add_allow", "seal"):
            return _err("未知 action: %s（支持 init|add_part|add_interface|add_allow|validate|seal|show）" % act)
        if not path:
            return _err("action=%s 必须给 path（要写回清单文件）" % act)
        if act == "seal":
            val = _manifest_validate(m, check_files=bool(check_files), check_md5=bool(check_md5))
            if not val["ok"]:
                return _err("seal 拒绝：清单还有 %d 个 error，先修" % len(val["errors"]),
                            errors=val["errors"][:20], warnings=val["warnings"][:20])
            if m.get("sealed") and not force:
                return _err("清单已经 sealed（force=true 才重封）", sealed_md5=m.get("sealed_md5"))
            try:
                m["version"] = int(m.get("version") or 0) + 1
            except Exception:
                m["version"] = 2
            m["sealed"] = True
            m["sealed_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
            m["sealed_md5"] = _manifest_md5(m)
            if note:
                m["note"] = str(note)
            try:
                p = _write_json(path, m)
            except Exception as e:
                return _err("seal 写盘失败: %s: %s" % (type(e).__name__, str(e)[:160]))
            return _j(_receipt({"ok": True, "action": "seal", "path": p, "sealed": True,
                                "version": m["version"], "sealed_md5": m["sealed_md5"],
                                "manifest_md5": _manifest_md5(m), "counts": _manifest_counts(m)}, t0))
    finally:
        st.update((k, v) for k, v in locals().items() if k != "st")

def _asm_manifest_p2(st):
    act, allow, allow_pair, compute_md5, interface = (
        st.get("act"), st["allow"], st["allow_pair"], st["compute_md5"], st["interface"])
    interfaces, m, part, path, t0 = st["interfaces"], st.get("m"), st["part"], st["path"], st.get("t0")
    f, fp, got, it, p, pid, src_item, wp = (st.get("f"), st.get("fp"), st.get("got"), st.get("it"), st.get("p"), st.get("pid"), st.get("src_item"), st.get("wp"))
    try:
        if act == "add_part":
            p = dict(part) if isinstance(part, dict) else None
            if p is None:
                return _err("add_part 需要 part={id,file,objects,transform,interface_frames}")
            pid = str(p.get("id") or "")
            if not pid:
                return _err("part.id 必填")
            if any(isinstance(x, dict) and str(x.get("id")) == pid for x in (m.get("parts") or [])):
                return _err("part id 重复: %s" % pid)
            fp = p.get("file")
            if not fp:
                return _err("part.file 必填（merge 要知道从哪 append）")
            f = _path(fp)
            if not os.path.isfile(f):
                return _err("part.file 不存在: %s" % f)
            if compute_md5 or not p.get("file_md5"):
                got = _sha256_file(f)
                if got is None:
                    return _err("part.file 读不了，算不出 md5: %s" % f)
                p["file_md5"] = got
            p.setdefault("transform", {"loc_mm": [0, 0, 0], "rot_euler_deg": [0, 0, 0], "scale": [1, 1, 1]})
            m.setdefault("parts", []).append(p)
            val = _manifest_validate(m, check_files=True, check_md5=False)
            if not val["ok"]:
                m["parts"].pop()
                return _err("add_part 校验失败（未写入）", errors=val["errors"][:20], warnings=val["warnings"][:20])
            try:
                wp = _write_json(path, m)
            except Exception as e:
                return _err("manifest 写盘失败: %s: %s" % (type(e).__name__, str(e)[:160]))
            return _j(_receipt({"ok": True, "action": "add_part", "part": pid, "path": wp,
                                "file_md5": p.get("file_md5"), "manifest_md5": _manifest_md5(m),
                                "counts": _manifest_counts(m), "warnings": val["warnings"][:20]}, t0))
        if act == "add_interface":
            items = []
            if isinstance(interfaces, dict):
                items.append(dict(interfaces))
            elif isinstance(interfaces, (list, tuple)):
                items.extend([dict(x) for x in interfaces if isinstance(x, dict)])
            if isinstance(interface, dict):
                items.append(dict(interface))
            if not items:
                return _err("add_interface 需要 interface={id,a,b,frame_a,frame_b,fit,nominal_mm,tol_mm,allow_overlap_mm}")
            cur = m.setdefault("interfaces", [])
            added = []
            for it in items:
                it.setdefault("id", "IF-%d" % (len(cur) + 1))
                if any(isinstance(x, dict) and str(x.get("id")) == str(it.get("id")) for x in cur):
                    return _err("interface id 重复: %s" % it.get("id"))
                cur.append(it)
                added.append(str(it.get("id")))
            val = _manifest_validate(m, check_files=True, check_md5=False)
            if not val["ok"]:
                for _ in added:
                    cur.pop()
                return _err("add_interface 校验失败（未写入）", errors=val["errors"][:20], warnings=val["warnings"][:20])
            try:
                wp = _write_json(path, m)
            except Exception as e:
                return _err("manifest 写盘失败: %s: %s" % (type(e).__name__, str(e)[:160]))
            return _j(_receipt({"ok": True, "action": "add_interface", "added": added, "path": wp,
                                "manifest_md5": _manifest_md5(m), "counts": _manifest_counts(m),
                                "warnings": val["warnings"][:20]}, t0))
        # add_allow
        items = []
        for src_item in (allow, allow_pair):
            if isinstance(src_item, dict):
                items.append(dict(src_item))
            elif isinstance(src_item, (list, tuple)):
                items.extend([dict(x) for x in src_item if isinstance(x, dict)])
        if not items:
            return _err("add_allow 需要 allow={a,b,max_depth_mm,max_volume_mm3,reason}（或列表）")
        cur = m.setdefault("allow_pairs", [])
        added = []
        for it in items:
            it.setdefault("id", "ALLOW-%d" % (len(cur) + 1))
            if any(isinstance(x, dict) and str(x.get("id")) == str(it.get("id")) for x in cur):
                return _err("allow id 重复: %s" % it.get("id"))
            cur.append(it)
            added.append(str(it.get("id")))
        val = _manifest_validate(m, check_files=True, check_md5=False)
        if not val["ok"]:
            for _ in added:
                cur.pop()
            return _err("add_allow 校验失败（未写入）", errors=val["errors"][:20], warnings=val["warnings"][:20])
    finally:
        st.update((k, v) for k, v in locals().items() if k != "st")

def _asm_manifest_p3(st):
    added, m, path, t0, val = st.get("added"), st.get("m"), st["path"], st.get("t0"), st.get("val")
    wp = (st.get("wp"))
    try:
        try:
            wp = _write_json(path, m)
        except Exception as e:
            return _err("manifest 写盘失败: %s: %s" % (type(e).__name__, str(e)[:160]))
        return _j(_receipt({"ok": True, "action": "add_allow", "added": added, "path": wp,
                            "manifest_md5": _manifest_md5(m), "counts": _manifest_counts(m),
                            "warnings": val["warnings"][:20]}, t0))
    finally:
        st.update((k, v) for k, v in locals().items() if k != "st")

def asm_manifest(action="show", path=None, assembly=None, units="mm", coord=None, part=None,
                 interfaces=None, interface=None, allow=None, allow_pair=None,
                 manifest=None, force=False, compute_md5=True, check_files=True, check_md5=True,
                 note=None):
    """零件清单：init | add_part | add_interface | add_allow | validate | seal | show。"""
    st = dict(locals())
    for _fn_ in (_asm_manifest_p1, _asm_manifest_p2, _asm_manifest_p3):
        _r = _fn_(st)
        if _r is not None:
            return _r
