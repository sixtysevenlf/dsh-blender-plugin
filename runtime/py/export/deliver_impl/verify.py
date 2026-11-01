# -*- coding: utf-8 -*-
"""Split from deliver.py; behavior preserved by the facade."""
from .common import *  # noqa: F401,F403
from .export import deliver_export

# ---------------------------------------------------------------- 读回自检

def _dv_scan_obj(path):
    """读回 OBJ 自己数：f（三角面）/ g / usemtl 行数与唯一材质名 / v / vn + **从 v 行重算 bbox**。"""
    n_f = n_g = n_u = n_v = n_vn = 0
    u_names = set()
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
                u_names.add(line[7:].strip())
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
    out = {"triangles": n_f, "groups": n_g, "usemtl": n_u, "usemtl_unique": len(u_names),
           "vertices": n_v, "normals": n_vn}
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


def _deliver_verify_p1(_state):
    manifest, path_or_dir = _state.get("manifest"), _state["path_or_dir"]
    cands, d, last, lm, prefer, stem = (_state.get("cands"), _state.get("d"), _state.get("last"), _state.get("lm"), _state.get("prefer"), _state.get("stem"))
    try:
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
    finally:
        _state.update((k, v) for k, v in locals().items() if k != "_state")

def _deliver_verify_p2(_state):
    chk, flist, man, obs, target_file = (
        _state.get("chk"), _state.get("flist"), _state.get("man"), _state.get("obs"), _state.get("target_file"))
    base, dev, exists, exp_g, exp_hi, exp_lo, exp_u, ext, f, fp, got_bytes, got_md5, mt, nb, sc, want = (_state.get("base"), _state.get("dev"), _state.get("exists"), _state.get("exp_g"), _state.get("exp_hi"), _state.get("exp_lo"), _state.get("exp_u"), _state.get("ext"), _state.get("f"), _state.get("fp"), _state.get("got_bytes"), _state.get("got_md5"), _state.get("mt"), _state.get("nb"), _state.get("sc"), _state.get("want"))
    try:
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
                counts = man.get("counts") or {}
                exp_g = int(counts.get("groups", f.get("groups", counts.get("objects", f.get("objects", -1)))))
                exp_u = int(counts.get("usemtl", f.get("usemtl", -1)))
                exp_uu = counts.get("usemtl_unique", f.get("usemtl_unique"))
                exp_lines = exp_g if exp_g >= 0 else exp_u
                chk("obj_group_count_match:" + base, sc["groups"] == exp_g,
                    "g 行 %d 个 / 对象(组)数 %d" % (sc["groups"], exp_g), groups=sc["groups"], manifest_groups=exp_g)
                chk("obj_usemtl_count_match:" + base, sc["usemtl"] == exp_lines,
                    "usemtl 行 %d 个 / 对象(组)数 %d" % (sc["usemtl"], exp_lines),
                    usemtl=sc["usemtl"], manifest_lines=exp_lines)
                if exp_uu is None:
                    # 3.0.0 及更早的清单把 counts.usemtl 存成唯一材质数，没有
                    # usemtl_unique；按旧口径兼容，行数仍由 groups/objects 校验。
                    meta_ok = exp_u in (sc["usemtl"], sc["usemtl_unique"])
                    chk("obj_usemtl_metadata_match:" + base, meta_ok,
                        "清单 usemtl=%d（legacy：唯一材质数或行数）；文件行=%d / 唯一=%d"
                        % (exp_u, sc["usemtl"], sc["usemtl_unique"]),
                        manifest_usemtl=exp_u, usemtl=sc["usemtl"], usemtl_unique=sc["usemtl_unique"])
                else:
                    exp_uu = int(exp_uu)
                    meta_ok = exp_u == sc["usemtl"] and exp_uu == sc["usemtl_unique"]
                    chk("obj_usemtl_metadata_match:" + base, meta_ok,
                        "清单 usemtl=%d / usemtl_unique=%d；文件行=%d / 唯一=%d"
                        % (exp_u, exp_uu, sc["usemtl"], sc["usemtl_unique"]),
                        manifest_usemtl=exp_u, manifest_usemtl_unique=exp_uu,
                        usemtl=sc["usemtl"], usemtl_unique=sc["usemtl_unique"])
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
    finally:
        _state.update((k, v) for k, v in locals().items() if k != "_state")

def _deliver_verify_p3(_state):
    checks, chk, man, man_path, man_src = (
        _state.get("checks"), _state.get("chk"), _state.get("man"), _state.get("man_path"), _state.get("man_src"))
    nz, obs, p = _state.get("nz"), _state.get("obs"), _state.get("p")
    longest, ok, want = (_state.get("longest"), _state.get("ok"), _state.get("want"))
    try:
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
    finally:
        _state.update((k, v) for k, v in locals().items() if k != "_state")

def deliver_verify(path_or_dir=None, manifest="auto"):
    """读回产物逐项自检。返回 {ok, checks:[{name, ok, detail, ...数字}], passed, failed}。

    检查项：清单可用 / 文件存在且有字节 / md5 与清单一致 / 三角面数与清单一致 /
    OBJ 的 g 与 usemtl 行数（= 对象数）与清单一致；usemtl_unique 只作唯一材质数诊断，
    旧清单（3.0.0 及更早，usemtl 为唯一材质数）按 legacy 口径兼容。
    单位块带单位 / 归一化可反推（scale+center+原 bbox）。
    `manifest="auto"` → 目录里找 `<name>_manifest.json`（多个则用最近一次导出的那个）。
    """
    _state = dict(locals())
    for _fn_ in (_deliver_verify_p1, _deliver_verify_p2, _deliver_verify_p3):
        _r = _fn_(_state)
        if _r is not None:
            return _r


def deliver_help():
    return _dv_j({
        "version": DELIVER_VERSION,
        "api": "K.dsh_deliver_api",
        "ops": {
            "export": "export(dir, objects=None, scope=None, name='model', fmt='obj'|'stl', normalize=True, "
                      "target_half_extent=1.0, per_part_materials=True, manifest=True, units_note=True, include_hidden=False)",
            "verify": "verify(path_or_dir=None, manifest='auto') → 逐项 PASS/FAIL（md5 / 面数 / g 与 usemtl 行数 / 单位 / 归一化可反推）；"
                      "manifest='auto' 只认磁盘上的 *_manifest.json，manifest='memory' 才是拿最近一次导出的内存记录（较弱）",
            "help": "help() → 本速查",
        },
        "typical": "K.dsh_deliver_api['export']({'dir': 'D:/Blender/house', 'scope': 'House', 'name': 'house'}) "
                   "→ 然后 K.dsh_deliver_api['verify']('D:/Blender/house')",
        "rules": ["空产出（挑不到对象 / 全是空网格）→ ok:false，不静默成功",
                  "归一化记录 scale/center/原 bbox → p_orig = p_out / scale + center（归档铁律）",
                  "OBJ：每对象一个 g + usemtl（共享材质也每对象一行；usemtl_unique 只记唯一材质数）；"
                  "MTL 的 Kd 来自材质，取不到给确定性灰阶并在 guessed_colors 里点名",
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


