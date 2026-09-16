# -*- coding: utf-8 -*-
"""deliver（#13 交付导出）自检 —— **纯无头可跑，自造合成场景，绝不碰 GUI 场景**。

跑法：
    blender_rt_headless(preload="deliver", engine="none", outdir="D:\\\\DSH\\\\blender\\\\tmp\\\\txn_v090",
        script="_p=K.win_path('/home/sixtyseven67/DSH/dsh-blender-plugin/tests/deliver_selftest.py');"
               "exec(compile(open(_p,encoding='utf-8').read(),'x','exec'),globals())")
    # 或 blender -b --factory-startup --python tests/deliver_selftest.py -- <outdir>

覆盖（每条都带数字）：
  ⑦ OBJ+MTL+manifest 三件齐；md5/字节/面数/g/usemtl 逐项 PASS；单位块带单位；归一化可反推
  ⑧ 故意改字节 / 追加面 → verify 必须 FAIL（证明校验真的咬人，而不是只会 ok:true）
  ⑨ 空 scope / 空集合 / objects 全不存在 / 非法 fmt / 零尺寸 bbox → 一律 ok:false
  ＋ STL 二进制：文件大小 = 84 + 50×面数、头声明面数一致、归一化最长边 = 2×target_half_extent
  ＋ 求值后网格：带 Array 修改器的对象导出面数 = 求值后（不是基础网格）
  ＋ include_hidden：hide_render 的对象默认不导出，include_hidden=True 才进
  ＋ 确定性：同一场景连导两次，OBJ 的 md5 一致（归档可比对）
  ＋ 配色来源：有材质走 Kd 真值；无材质 → 确定性灰阶且被点名（guessed_colors）
"""
import bpy
import hashlib
import json
import os
import struct
import sys

FAILS, OKS, SKIPS = [], [], []


def check(name, cond, detail=""):
    (OKS if cond else FAILS).append({"name": name, "detail": detail})
    print(("  ok   " if cond else "  FAIL ") + name + ("" if cond else "  <- " + str(detail)))


def skip(name, why=""):
    SKIPS.append(name)
    print("  skip " + name + ("  <- " + why if why else ""))


def J(s):
    return json.loads(s)


def _api(attr, modname):
    """优先 K.<attr>（preload 注入），其次从 K.runtime_dir 现场加载同目录 <modname>.py（与 qc.py 同套写法）。"""
    K = sys.modules.get("dsh_rt_kernel")
    api = getattr(K, attr, None) if K is not None else None
    if api:
        return api, K
    d = getattr(K, "runtime_dir", None) if K is not None else None
    if d and os.path.isfile(os.path.join(str(d), modname + ".py")):
        p = os.path.join(str(d), modname + ".py")
        with open(p, encoding="utf-8") as fh:
            src = fh.read()
        exec(compile(src, p, "exec"), {"__name__": "dsh_" + modname, "__file__": p})
        return getattr(K, attr, None), K
    return None, K


COLL = "DSH_DV_COLL"
A, B, C, H = "DSH_DV_BOX_A", "DSH_DV_BOX_B", "DSH_DV_ARR", "DSH_DV_HIDDEN"
MAT = "DSH_DV_MAT_RED"
MTL_TXT = "-" * 60


def _mk_box(name, size=(1.0, 1.0, 1.0), loc=(0.0, 0.0, 0.0), coll=None):
    hx, hy, hz = size[0] / 2.0, size[1] / 2.0, size[2] / 2.0
    vs = [(-hx, -hy, -hz), (hx, -hy, -hz), (hx, hy, -hz), (-hx, hy, -hz),
          (-hx, -hy, hz), (hx, -hy, hz), (hx, hy, hz), (-hx, hy, hz)]
    fs = [(0, 3, 2, 1), (4, 5, 6, 7), (0, 1, 5, 4), (1, 2, 6, 5), (2, 3, 7, 6), (3, 0, 4, 7)]
    me = bpy.data.meshes.new(name + "_mesh")
    me.from_pydata(vs, [], fs)
    me.update()
    ob = bpy.data.objects.new(name, me)
    ob.location = loc
    (coll or bpy.context.scene.collection).objects.link(ob)
    return ob


def _mk_red_mat():
    mat = bpy.data.materials.new(MAT)
    try:
        if getattr(mat, "node_tree", None) is None:
            mat.use_nodes = True
    except Exception:
        pass
    pn = None
    try:
        for n in mat.node_tree.nodes:
            if n.type == "BSDF_PRINCIPLED":
                pn = n
                break
    except Exception:
        pn = None
    if pn is not None:
        pn.inputs["Base Color"].default_value = (0.9, 0.2, 0.1, 1.0)
    else:
        mat.diffuse_color = (0.9, 0.2, 0.1, 1.0)
    return mat, (pn is not None)


def build_scene():
    """合成场景（全部落在 A 的 [-4,4]³ 包围盒内 → 归一化期望值可手算：最长边 8、缩放 = 2×target/8）。

    A 8³ @原点（红材质，12 面）· B 0.5³ @(3.5,3.5,3.5)（无材质 → 灰阶，12 面）
    C 0.2³ @(0,3.5,0) + Array×3（求值后 36 面）· H 1³ @(0,0,3.5) 且 hide_render（默认排除，12 面）
    """
    coll = bpy.data.collections.new(COLL)
    bpy.context.scene.collection.children.link(coll)
    a = _mk_box(A, (8.0, 8.0, 8.0), (0.0, 0.0, 0.0), coll)
    b = _mk_box(B, (0.5, 0.5, 0.5), (3.5, 3.5, 3.5), coll)
    c = _mk_box(C, (0.2, 0.2, 0.2), (0.0, 3.5, 0.0), coll)
    c.modifiers.new("DSH_DV_ARR", "ARRAY").count = 3
    h = _mk_box(H, (1.0, 1.0, 1.0), (0.0, 0.0, 3.5), coll)
    h.hide_render = True
    mat, node_ok = _mk_red_mat()
    a.data.materials.append(mat)
    bpy.context.view_layer.update()
    return coll, a, b, c, h, node_ok


def cleanup(coll, objs):
    for ob in objs:
        try:
            bpy.data.objects.remove(ob, do_unlink=True)
        except Exception:
            pass
    try:
        bpy.data.collections.remove(coll)
    except Exception:
        pass
    for m in list(bpy.data.meshes):
        if m.name.startswith("DSH_DV_"):
            try:
                bpy.data.meshes.remove(m)
            except Exception:
                pass
    for m in list(bpy.data.materials):
        if m.name.startswith("DSH_DV_"):
            try:
                bpy.data.materials.remove(m)
            except Exception:
                pass
    bpy.context.view_layer.update()


def scan_obj(path):
    n_f = n_g = n_u = n_v = n_vn = 0
    with open(path, "r", encoding="utf-8") as fh:
        for line in fh:
            if line.startswith("f "):
                n_f += 1
            elif line.startswith("g "):
                n_g += 1
            elif line.startswith("usemtl "):
                n_u += 1
            elif line.startswith("v "):
                n_v += 1
            elif line.startswith("vn "):
                n_vn += 1
    return {"f": n_f, "g": n_g, "usemtl": n_u, "v": n_v, "vn": n_vn}


def main():
    outdir = None
    if "--" in sys.argv:
        rest = sys.argv[sys.argv.index("--") + 1:]
        outdir = rest[-1] if rest else None
    outdir = outdir or os.path.join(os.path.expanduser("~"), "dsh_deliver_selftest")
    outdir = outdir.replace("\\", "/")
    os.makedirs(outdir, exist_ok=True)

    api, K = _api("dsh_deliver_api", "deliver")
    if api is None:
        print("HEADLESS " + json.dumps({"ok": False, "passed": 0, "failed": 1, "skipped": 0,
                                        "failures": [{"name": "load_deliver_api",
                                                      "detail": "K.dsh_deliver_api 缺失且 runtime_dir 下没有 deliver.py"}],
                                        "hint": "用 preload='deliver' 跑"}, ensure_ascii=False))
        return
    print("DELIVER_SELFTEST version=%s outdir=%s blender=%s" % (api.get("version"), outdir, bpy.app.version_string))
    check("DELIVER_VERSION == 1", int(api.get("version") or 0) == 1, api.get("version"))
    check("API 三件套齐（export/verify/help）", all(k in api for k in ("export", "verify", "help")), sorted(api))
    h = J(api["help"]())
    check("help 给了典型调用且带版本", h.get("version") == 1 and "export" in str(h.get("typical")), h.get("typical"))
    check("非法 fmt 报错并列出允许值",
          J(api["export"](outdir, None, COLL, "bad", "ply")).get("allowed") == ["obj", "stl"],
          J(api["export"](outdir, None, COLL, "bad", "ply")))
    check("负 target_half_extent 被拒", J(api["export"](outdir, None, COLL, "bad2", "obj", True, 0.0)).get("ok") is False,
          J(api["export"](outdir, None, COLL, "bad2", "obj", True, 0.0)).get("error"))

    coll, a, b, c, hb, node_ok = build_scene()
    try:
        d1 = os.path.join(outdir, "export_obj")
        # ============================================================ ⑦ OBJ + MTL + manifest
        ex = J(api["export"](d1, None, COLL, "part", "obj", True, 1.0, True, True, True, False))
        files = ex.get("files") or {}
        check("⑦a：export ok（OBJ+MTL+manifest 三件齐）",
              ex.get("ok") is True and set(files) >= {"obj", "mtl"} and bool(ex.get("manifest")),
              ex.get("error") or sorted(files))
        check("⑦b：目录被自动创建（导出前不存在）", os.path.isdir(d1), d1)
        check("⑦c：三件都 bytes>0 且有 md5（32 位）",
              all(int((files.get(k) or {}).get("bytes") or 0) > 0 and len(str((files.get(k) or {}).get("md5"))) == 32
                  for k in ("obj", "mtl")) and len(str((ex.get("manifest") or {}).get("md5"))) == 32,
              {k: ((files.get(k) or {}).get("bytes"), (files.get(k) or {}).get("md5")) for k in ("obj", "mtl")})
        cnt = ex.get("counts") or {}
        check("⑦d：对象数=3、三角面=12+12+36=60（含 Array 修改器的求值结果）",
              cnt.get("objects") == 3 and cnt.get("triangles") == 60,
              {"objects": cnt.get("objects"), "triangles": cnt.get("triangles"), "per": [(o["name"], o["triangles"]) for o in ex.get("objects") or []]})
        check("⑦d2：Array 修改器的对象导出的是**求值后**面数 36（基础网格只有 12）",
              [o["triangles"] for o in (ex.get("objects") or []) if o["name"] == C] == [36],
              [(o["name"], o["triangles"]) for o in ex.get("objects") or []])
        check("⑦e：hide_render 的对象被默认排除（excluded_hidden=1）", int(ex.get("excluded_hidden") or 0) == 1,
              ex.get("excluded_hidden"))
        sc = scan_obj((files["obj"])["path"])
        check("⑦f：OBJ 里 g 行数=对象数=3、usemtl 行数=3、v/vn/f 都非 0",
              sc["g"] == 3 and sc["usemtl"] == 3 and sc["v"] > 0 and sc["vn"] > 0 and sc["f"] == 60, sc)
        check("⑦g：MTL 有 3 条 newmtl（含灰阶兜底那条）", str(files["mtl"]["materials"]) == "3", files["mtl"])
        mtl_text = open(files["mtl"]["path"], encoding="utf-8").read()
        check("⑦h：MTL 里能看到 Kd 0.9/0.2/0.1（材质真值）", "0.900000 0.200000 0.100000" in mtl_text,
              [ln for ln in mtl_text.splitlines() if ln.startswith("Kd")])
        objs = {o["name"]: o for o in (ex.get("objects") or [])}
        if node_ok:
            check("⑦i：有材质的对象 Kd = Principled Base Color（0.9/0.2/0.1）",
                  objs[A]["kd"] == [0.9, 0.2, 0.1] and objs[A]["material_source"] == "principled_base_color",
                  objs[A])
        else:
            check("⑦i：有材质的对象 Kd 取自材质（非猜）",
                  objs[A]["kd"] == [0.9, 0.2, 0.1] and objs[A]["guessed"] is False, objs[A])
        check("⑦j：无材质的对象被点名为'颜色是猜的'（B 与 C 都没材质）",
              objs[B]["guessed"] is True and objs[C]["guessed"] is True
              and sorted(g["object"] for g in (ex.get("guessed_colors") or [])) == sorted([B, C]),
              ex.get("guessed_colors"))
        check("⑦j2：配色来源分档被回传（principled 1 条 + 灰阶 2 条）",
              (ex.get("material_color_sources") or {}).get("deterministic_grey") == 2
              and len(ex.get("material_color_sources") or {}) >= 1,
              ex.get("material_color_sources"))
        check("⑦k：灰阶是确定性的（同一名字两次导出同色）",
              (ex.get("guessed_colors") or [{}])[0].get("kd") == J(api["export"](os.path.join(outdir, "again"), None, COLL, "part", "obj"))["guessed_colors"][0]["kd"],
              (ex.get("guessed_colors") or [{}])[0].get("kd"))

        # ============================================================ 单位 + 归一化（归档铁律）
        u = ex.get("units") or {}
        check("⑦l：单位块带 m 与 mm 两个数值", isinstance(u.get("meters_per_unit"), (int, float))
              and isinstance(u.get("millimeters_per_unit"), (int, float)) and "mm" in str(u.get("note")),
              u.get("note"))
        nz = ex.get("normalize") or {}
        want_scale = 2.0 / 8.0        # 最长边 8 → 缩放到 2×target_half_extent
        check("⑦m：scale = 2×target/最长边 = 0.25（原 bbox 最长边 8）",
              abs(float(nz.get("scale")) - want_scale) < 1e-9
              and abs(max(nz.get("bbox_before_size") or [0]) - 8.0) < 1e-6,
              {"scale": nz.get("scale"), "bbox_before_size": nz.get("bbox_before_size")})
        check("⑦n：原中心记进 normalize.center（可反推原始尺度）",
              nz.get("center") is not None and all(abs(float(nz["center"][i]) - 0.0) < 1e-6 for i in range(3)),
              nz.get("center"))
        aft = nz.get("bbox_after_size") or []
        check("⑦o：归一化后最长边 = 2×target_half_extent = 2.0", abs(max(aft) - 2.0) < 1e-6,
              {"after_size": aft})
        check("⑦p：recover 公式写明 p_orig = p_out/scale + center", "p_out / scale + center" in str(nz.get("recover")),
              nz.get("recover"))
        man = json.loads(open(ex["manifest"]["path"], encoding="utf-8").read())
        check("⑦q：manifest 记了 md5/面数/单位/normalize/api 版本",
              man.get("api_version") == 1 and man.get("counts", {}).get("triangles") == 60
              and bool(man.get("units", {}).get("note")) and man.get("normalize", {}).get("applied") is True
              and all(len(f.get("md5") or "") == 32 for f in man.get("files") or []),
              {"api_version": man.get("api_version"), "counts": man.get("counts"), "files": len(man.get("files") or [])})

        # ============================================================ verify 逐项 PASS
        vf = J(api["verify"](d1, "auto"))
        check("⑦r：verify 逐项 PASS（ok:true / failed:0 / checks>=10）",
              vf.get("ok") is True and int(vf.get("failed") or 0) == 0 and len(vf.get("checks") or []) >= 10,
              {"passed": vf.get("passed"), "failed": vf.get("failed"), "n": len(vf.get("checks") or []),
               "failed_checks": vf.get("failed_checks")})
        check("⑦s：verify 回传了数字（triangles / md5 / bbox 最长边）",
              (vf.get("observed", {}).get("obj") or {}).get("triangles") == 60
              and len(str((vf.get("observed", {}).get("files", {}).get("part.obj", {}) or {}).get("md5"))) == 32
              and abs(float((vf.get("observed", {}).get("obj") or {}).get("longest_edge")) - 2.0) < 2e-4,
              vf.get("observed"))
        vf_dir = J(api["verify"](d1))
        check("⑦t：verify(目录) 不传 manifest 也能自动找到清单", vf_dir.get("ok") is True and vf_dir.get("manifest_source") == "auto",
              {"ok": vf_dir.get("ok"), "src": vf_dir.get("manifest_source")})
        vf_file = J(api["verify"]((files["obj"])["path"]))
        check("⑦u：verify(单个文件路径) 也能核（挑同目录同名清单）", vf_file.get("ok") is True, vf_file.get("failed_checks"))

        # ============================================================ ⑧ 篡改必须 FAIL
        d2 = os.path.join(outdir, "tamper")
        ex2 = J(api["export"](d2, None, COLL, "t", "obj"))
        op = ex2["files"]["obj"]["path"]
        raw = open(op, "rb").read()
        i = raw.find(b"\nv ")
        j = i + 3
        while j < len(raw) and not (48 <= raw[j] <= 57):
            j += 1
        mut = bytearray(raw)
        mut[j] = 48 if mut[j] == 57 else 57
        open(op, "wb").write(bytes(mut))
        vf2 = J(api["verify"](d2, "auto"))
        check("⑧a：改 1 个字节 → verify FAIL（ok:false）", vf2.get("ok") is False,
              {"passed": vf2.get("passed"), "failed": vf2.get("failed")})
        check("⑧b：FAIL 项点名 md5_match", "md5_match" in str(vf2.get("failed_checks")), vf2.get("failed_checks"))
        with open(op, "a", encoding="utf-8") as fh:
            fh.write("f 1//1 2//2 3//3\n")           # 追加一个面 → 面数对不上
        vf3 = J(api["verify"](d2, "auto"))
        check("⑧c：追加一个面 → triangle_count_match 也 FAIL",
              vf3.get("ok") is False and "triangle_count_match" in str(vf3.get("failed_checks")),
              vf3.get("failed_checks"))
        check("⑧d：篡改后 ok:false 是从 checks 推出来的（failed>=2）", int(vf3.get("failed") or 0) >= 2, vf3.get("failed"))
        # 缺文件也要 FAIL 而不是抛异常
        os.remove(op)
        vf4 = J(api["verify"](d2, "auto"))
        check("⑧e：文件被删 → file_exists FAIL（不抛异常）",
              vf4.get("ok") is False and "file_exists:t.obj" in str(vf4.get("failed_checks")), vf4.get("failed_checks"))

        # ============================================================ ⑨ 空产出必须 ok:false
        check("⑨a：不存在的集合 → ok:false", J(api["export"](outdir, None, "DSH_DV_NO_SUCH", "e1", "obj")).get("ok") is False,
              J(api["export"](outdir, None, "DSH_DV_NO_SUCH", "e1", "obj")).get("error"))
        empty = bpy.data.collections.new("DSH_DV_EMPTY")
        bpy.context.scene.collection.children.link(empty)
        r9b = J(api["export"](outdir, None, "DSH_DV_EMPTY", "e2", "obj"))
        check("⑨b：空集合（0 个 mesh）→ ok:false", r9b.get("ok") is False and "0 个" in str(r9b.get("error")), r9b.get("error"))
        bpy.data.collections.remove(empty)
        r9c = J(api["export"](outdir, ["DSH_DV_NOPE"], None, "e3", "obj"))
        check("⑨c：objects 全不存在 → ok:false", r9c.get("ok") is False, r9c.get("error"))
        me = bpy.data.meshes.new("DSH_DV_EMPTYMESH")
        ob0 = bpy.data.objects.new("DSH_DV_EMPTYOBJ", me)
        bpy.context.scene.collection.objects.link(ob0)
        r9d = J(api["export"](outdir, ["DSH_DV_EMPTYOBJ"], None, "e4", "obj"))
        check("⑨d：对象在但 0 个三角面 → ok:false（空产出不许静默成功）",
              r9d.get("ok") is False and "0 个有三角面" in str(r9d.get("error")), r9d.get("error"))
        bpy.data.objects.remove(ob0, do_unlink=True)
        bpy.data.meshes.remove(me)
        flat = _mk_box("DSH_DV_FLAT", (1.0, 1.0, 0.0), (0.0, 0.0, 0.0))
        r9e = J(api["export"](outdir, ["DSH_DV_FLAT"], None, "flat", "obj", True, 1.0))
        check("⑨e：薄片（z 尺寸 0）是合法交付 → ok:true 且 scale = 2/最长边 = 2.0",
              r9e.get("ok") is True and abs(float(r9e["normalize"]["scale"]) - 2.0) < 1e-9,
              r9e.get("error") or r9e.get("normalize"))
        bpy.data.objects.remove(flat, do_unlink=True)
        degen = _mk_box("DSH_DV_DEGEN", (0.0, 0.0, 0.0), (0.0, 0.0, 0.0))     # 8 个顶点全重合 → 零尺寸 bbox
        r9f = J(api["export"](outdir, ["DSH_DV_DEGEN"], None, "degen", "obj", True, 1.0))
        check("⑨f：零尺寸包围盒（顶点全重合）→ ok:false 而不是除零",
              r9f.get("ok") is False and "零尺寸包围盒" in str(r9f.get("error")), r9f.get("error"))
        bpy.data.objects.remove(degen, do_unlink=True)
        for m in list(bpy.data.meshes):
            if m.name.startswith("DSH_DV_FLAT") or m.name.startswith("DSH_DV_DEGEN"):
                bpy.data.meshes.remove(m)

        # ============================================================ STL
        d3 = os.path.join(outdir, "export_stl")
        ex3 = J(api["export"](d3, None, COLL, "part", "stl", True, 1.0, True, True, True, False))
        sf = (ex3.get("files") or {}).get("stl") or {}
        sz = int(sf.get("bytes") or 0)
        check("STL①：二进制 STL 落盘，大小 = 84 + 50×面数",
              ex3.get("ok") is True and sz == 84 + 50 * 60, {"bytes": sz, "expect": 84 + 50 * 60})
        with open(sf["path"], "rb") as fh:
            fh.seek(80)
            head_tris = struct.unpack("<I", fh.read(4))[0]
        check("STL②：头里声明 60 个三角面", head_tris == 60, head_tris)
        check("STL③：STL 不写 MTL 且提示'不携带材质'",
              "mtl" not in (ex3.get("files") or {}) and any("不携带材质" in w for w in ex3.get("warnings") or []),
              ex3.get("warnings"))
        vf5 = J(api["verify"](d3, "auto"))
        check("STL④：verify 逐项 PASS（含 stl_normalized_longest_edge）",
              vf5.get("ok") is True and "stl_normalized_longest_edge:part.stl" in str([c["name"] for c in vf5.get("checks") or []]),
              {"failed": vf5.get("failed_checks"), "names": [c["name"] for c in vf5.get("checks") or []]})

        # ============================================================ include_hidden / 确定性 / normalize=False
        ex4 = J(api["export"](os.path.join(outdir, "with_hidden"), None, COLL, "all", "obj", True, 1.0, True, True, True, True))
        check("A①：include_hidden=True → 被隐藏的对象也进（4 对象 / 72 面）",
              (ex4.get("counts") or {}).get("objects") == 4 and (ex4.get("counts") or {}).get("triangles") == 72,
              ex4.get("counts"))
        d5a, d5b = os.path.join(outdir, "det_a"), os.path.join(outdir, "det_b")
        r5a = J(api["export"](d5a, None, COLL, "det", "obj"))
        r5b = J(api["export"](d5b, None, COLL, "det", "obj"))
        check("A②：确定性 —— 同场景连导两次 OBJ 的 md5 一致（OBJ 里不写时间戳）",
              r5a["files"]["obj"]["md5"] == r5b["files"]["obj"]["md5"],
              {"a": r5a["files"]["obj"]["md5"], "b": r5b["files"]["obj"]["md5"]})
        r6 = J(api["export"](os.path.join(outdir, "no_norm"), None, COLL, "raw", "obj", False, 1.0))
        check("A③：normalize=False → scale=1、bbox_after == bbox_before（原始尺度即文件尺度）",
              abs(float(r6["normalize"]["scale"]) - 1.0) < 1e-12
              and r6["normalize"]["bbox_after_size"] == r6["normalize"]["bbox_before_size"],
              r6.get("normalize"))
        vf6 = J(api["verify"](os.path.join(outdir, "no_norm"), "auto"))
        check("A④：normalize=False 的产物 verify 仍 PASS（走未归一化分支）", vf6.get("ok") is True, vf6.get("failed_checks"))
        vf7 = J(api["verify"](os.path.join(outdir, "does_not_exist")))
        check("A⑤：verify 不存在的路径 → ok:false（不抛异常）",
              vf7.get("ok") is False and (vf7.get("checks") or [{}])[0].get("name") == "path_exists", vf7.get("error"))
        d8 = os.path.join(outdir, "no_manifest")
        r8 = J(api["export"](d8, None, COLL, "nm", "obj", True, 1.0, True, False))
        check("A⑥：manifest=False → 不写清单（files 里没有 manifest 键）",
              r8.get("ok") is True and "manifest" not in r8 and any("manifest=False" in w for w in r8.get("warnings") or []),
              r8.get("warnings"))
        vf8 = J(api["verify"](d8, "auto"))
        check("A⑦：无磁盘清单时 verify 不装 PASS（ok:false 且指名清单不可用）",
              vf8.get("ok") is False and "manifest_available" in str(vf8.get("failed_checks")), vf8.get("failed_checks"))
        vf8b = J(api["verify"](d8, "memory"))
        check("A⑦b：显式 manifest='memory' 才用内存记录（并在返回里标出较弱来源）",
              vf8b.get("ok") is True and "memory" in str(vf8b.get("manifest_source")),
              {"ok": vf8b.get("ok"), "src": vf8b.get("manifest_source"), "failed": vf8b.get("failed_checks")})
        r9 = J(api["export"](os.path.join(outdir, "no_units"), None, COLL, "nu", "obj", True, 1.0, True, True, False))
        vf9 = J(api["verify"](os.path.join(outdir, "no_units"), "auto"))
        check("A⑧：units_note=False → verify 的 units_note_has_units 判 FAIL（归档铁律）",
              vf9.get("ok") is False and "units_note_has_units" in str(vf9.get("failed_checks")), vf9.get("failed_checks"))
    finally:
        cleanup(coll, [a, b, c, hb])
        check("清理：合成对象/集合已删干净",
              all(bpy.data.objects.get(n) is None for n in (A, B, C, H)) and bpy.data.collections.get(COLL) is None,
              "leftover")

    print("")
    if FAILS:
        print("FAILED: " + ", ".join(f["name"] for f in FAILS))
    print("HEADLESS " + json.dumps({"ok": len(FAILS) == 0, "passed": len(OKS), "failed": len(FAILS),
                                    "skipped": len(SKIPS), "failures": FAILS, "skips": SKIPS, "outdir": outdir},
                                   ensure_ascii=False))


if __name__ == "__main__":
    main()
