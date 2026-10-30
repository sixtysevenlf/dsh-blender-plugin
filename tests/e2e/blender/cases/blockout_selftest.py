# -*- coding: utf-8 -*-
"""tests/blockout_selftest.py —— runtime/blockout.py 的真机自检（20 件白模 + 3 视图 512 + GLB）。

跑法（headless，preload 必须带上 blockout；qc/qc_render 用来验"优先调 qc_render"这条路）：
    blender_rt_headless(preload="blockout,qc,qc_render", engine="none", factory_startup=True,
        timeout_ms=300000,
        script_file="<包路径>/tests/blockout_selftest.py")

断言（实测数字写死；不达标改代码，不改断言）：
  A. 主模型：20 个 box（5×4 网格，90×90×60mm，底部贴地）
     · ok:true、对象数 = 20、命名 BO-selftest20-*、total_tris = 240（20×12）
     · bbox_mm 与输入一致（min=(-245,-195,0) max=(245,195,60)，±0.01mm）
     · 每件都有 Bevel 修改器、宽度 1-2mm、**未 apply**（base mesh 仍是 8 顶点）
  B. 渲染：3 视图（iso/front/right）@512×512
     · 3 张 PNG 存在、bytes>0、md5 与文件一致、图片实际尺寸 512×512
     · coverage_estimate > 0.02；preload 有 qc_render 时必须走 via=qc_render（不是回退）
     · sheet 存在、bytes>0、尺寸 = 1548×512（3×512 + 2×6 间隙）
  C. 导出：GLB 文件存在、bytes>0、前 4 字节 == b"glTF"、md5 与回执一致、nodes >= 20
  D. audit：内建体检 ok（非流形边/零面积面/孤立点都为 0）
  E. 附加：humanoid_prim 12 件且总高 1750mm；units=cm 换算（5cm 立方 → 50mm）；
     export op 能对已建场景补视图/补 GLB（不重建）；batch 3 件逐件 GLB + cleanup 后零残留；
     selftest/help 两个 op 可用；坏参数（空 parts / 坏 prim / 缺尺寸 / 坏视角 / 非 workbench 引擎 /
     export 空调用 / 未知 op）全部 ok:false 且不污染场景。
打印：HEADLESS {pass, fail, tris, bbox, views[], sheet, glb, ms{geom/views/export/total/wall}, ...}
"""
import bpy
import hashlib
import json
import os
import time

OKS, FAILS = [], []


def check(name, cond, detail=""):
    (OKS if cond else FAILS).append({"name": name, "detail": detail})
    print(("  [OK]   " if cond else "  [FAIL] ") + name + (" — " + str(detail)[:320] if detail else ""))


def md5(path):
    h = hashlib.md5()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def img_size(path):
    im = bpy.data.images.load(path, check_existing=False)
    try:
        return [int(im.size[0]), int(im.size[1])]
    finally:
        bpy.data.images.remove(im)


def scene_names():
    return set(o.name for o in bpy.context.scene.objects)


def mm_per_unit():
    try:
        return float(K.dsh_kit.units())
    except Exception:
        return 1000.0


def main():
    t_start = time.perf_counter()
    api = getattr(K, "dsh_blockout_api", None)   # noqa: F821  （headless 注入的持久内核）
    check("K.dsh_blockout_api 已注册", api is not None, "preload 里要带 blockout")
    if api is None:
        return finish(t_start, api=None)

    h = api("help", {})
    check("help 可读（module/version/ops）",
          h.get("module") == "blockout.py" and h.get("version") == 1
          and set(["base", "batch", "export", "selftest", "help"]) <= set((h.get("ops") or {}).keys()),
          {k: h.get(k) for k in ("module", "version")})
    check("help 写明 P0 边界（limits 非空）", bool(h.get("limits")))

    outdir = str(getattr(K, "out_dir", None) or os.path.expanduser("~"))
    outdir = os.path.join(outdir, "blockout_selftest")
    try:
        os.makedirs(outdir, exist_ok=True)
    except Exception:
        import tempfile
        outdir = tempfile.mkdtemp(prefix="dsh_blockout_selftest_")

    # ── A. 主模型：20 件 box（5×4 网格）
    boxes = []
    for i in range(20):
        x = (i % 5) * 100.0 - 200.0
        y = (i // 5) * 100.0 - 150.0
        boxes.append({"id": "b%02d" % i, "prim": "box", "size_mm": [90, 90, 60], "at_mm": [x, y, 30.0]})
    expect_min = [-245.0, -195.0, 0.0]
    expect_max = [245.0, 195.0, 60.0]

    t0 = time.perf_counter()
    r = api("base", {
        "name": "selftest20", "parts": boxes,
        "render": {"engine": "workbench", "res": [512, 512], "views": "auto3", "sheet": True},
        "export": {"glb": True}, "audit": True, "outdir": outdir,
    })
    base_wall_ms = int(round((time.perf_counter() - t0) * 1000.0))
    check("A1 base ok:true", r.get("ok") is True, r.get("error") or r.get("hint"))
    check("A2 对象数 = 20", r.get("object_count") == 20, r.get("object_count"))
    names = list(r.get("objects") or [])
    check("A3 命名 BO-selftest20-*", len(names) == 20 and all(n.startswith("BO-selftest20-") for n in names),
          names[:3])
    check("A4 total_tris = 240（20×12）", r.get("total_tris") == 240, r.get("total_tris"))
    ev = int(r.get("total_tris_eval") or 0)
    check("A5 eval tris 含 Bevel（240 < 值 <= 4000）", 240 < ev <= 4000, ev)
    bbox = r.get("bbox_mm") or {}
    lo, hi = bbox.get("min") or [], bbox.get("max") or []
    ok_lo = len(lo) == 3 and all(abs(lo[i] - expect_min[i]) <= 0.01 for i in range(3))
    ok_hi = len(hi) == 3 and all(abs(hi[i] - expect_max[i]) <= 0.01 for i in range(3))
    check("A6 bbox_mm 与输入一致", ok_lo and ok_hi, {"min": lo, "max": hi,
                                                    "expect_min": expect_min, "expect_max": expect_max})
    objs = [bpy.data.objects.get(n) for n in names]
    mesh_objs = [o for o in objs if o is not None and o.type == "MESH"]
    widths = []
    for ob in mesh_objs:
        mods = [m for m in ob.modifiers if m.type == "BEVEL"]
        if mods:
            widths.append(float(mods[0].width) * mm_per_unit())
    check("A7 每件都有 Bevel 修改器（20 个）", len(widths) == 20, len(widths))
    check("A8 Bevel 宽度 1-2mm", bool(widths) and all(1.0 - 1e-6 <= w <= 2.0 + 1e-6 for w in widths),
          sorted(set(round(w, 3) for w in widths)))
    check("A9 Bevel 未 apply（base mesh 仍 8 顶点）",
          all(len(ob.data.vertices) == 8 for ob in mesh_objs), [len(o.data.vertices) for o in mesh_objs[:4]])
    check("A10 audit ok（无非流形/零面积/孤立点）", (r.get("audit") or {}).get("ok") is True,
          {k: (r.get("audit") or {}).get(k) for k in ("non_manifold_edges", "zero_area_faces", "loose_verts")})

    # ── B. 渲染：3 视图 512
    rv = r.get("render") or {}
    check("B1 render ok:true", rv.get("ok") is True, rv.get("error"))
    via = rv.get("via")
    qc_available = hasattr(K, "dsh_qc_render_api")
    check("B2 via 合法（preload 有 qc_render 时必须走 qc_render）",
          via == "qc_render" if qc_available else via in ("qc_render", "internal"), via)
    views = rv.get("views") or []
    check("B3 恰好 3 个视图", len(views) == 3, [v.get("name") for v in views])
    check("B4 视图名 = iso/front/right", set(v.get("name") for v in views) == set(["iso", "front", "right"]),
          [v.get("name") for v in views])
    view_rows = []
    all_png_ok = True
    all_512 = True
    for v in views:
        path = str(v.get("path") or "")
        exists = bool(path) and os.path.isfile(path)
        nbytes = os.path.getsize(path) if exists else 0
        h_md5 = (md5(path) == v.get("md5")) if exists else False
        size = img_size(path) if exists else [0, 0]
        cov = v.get("coverage_estimate")
        row_ok = exists and nbytes > 0 and h_md5 and size == [512, 512] and cov is not None and float(cov) > 0.02
        all_png_ok = all_png_ok and row_ok
        all_512 = all_512 and size == [512, 512]
        view_rows.append({"name": v.get("name"), "bytes": nbytes, "ms": v.get("ms"), "md5": (v.get("md5") or "")[:12],
                          "coverage": cov, "res": size, "ok": row_ok})
        check("B5 视图 %r 文件/bytes/md5/coverage" % v.get("name"), row_ok,
              {"bytes": nbytes, "md5_match": h_md5, "res": size, "coverage": cov})
    check("B6 3 张 PNG 都是 512×512", all_512)
    sheet = rv.get("sheet") or {}
    sheet_ok = (sheet.get("ok") is True and os.path.isfile(str(sheet.get("path") or ""))
                and int(sheet.get("bytes") or 0) > 0 and sheet.get("res") == [1548, 512])
    check("B7 sheet 存在（bytes>0，1548×512）", sheet_ok,
          {"bytes": sheet.get("bytes"), "res": sheet.get("res"), "error": sheet.get("error")})

    # ── C. 导出 GLB
    ex = r.get("export") or {}
    glb = ex.get("glb") or {}
    gpath = str(glb.get("path") or "")
    glb_exists = bool(gpath) and os.path.isfile(gpath)
    magic_ok = False
    nodes = 0
    h_md5_glb = False
    if glb_exists:
        with open(gpath, "rb") as f:
            magic_ok = f.read(4) == b"glTF"
        nodes = int(glb.get("nodes") or 0)
        h_md5_glb = md5(gpath) == glb.get("md5")
    check("C1 GLB 文件存在且 bytes>0", glb_exists and int(glb.get("bytes") or 0) > 0,
          {"path": gpath, "bytes": glb.get("bytes")})
    check("C2 GLB 魔数 glTF", magic_ok)
    check("C3 GLB md5 与回执一致", h_md5_glb, glb.get("md5"))
    check("C4 GLB nodes >= 20", nodes >= 20, nodes)

    # ── 计时（geom / views / export / total）
    ms = r.get("ms") or {}
    geom_ms = ms.get("geom")
    views_ms = rv.get("ms")
    export_ms = glb.get("ms")
    total_ms = ms.get("total")
    check("D1 ms 字段齐全（geom/views/export/total）",
          all(isinstance(x, int) for x in (geom_ms, views_ms, export_ms, total_ms)), ms)

    # ── E1. template humanoid_prim
    r2 = api("base", {"name": "tmpl", "template": "humanoid_prim", "render": False,
                      "export": False, "audit": True, "outdir": outdir})
    bbox2 = (r2.get("bbox_mm") or {})
    human_ok = (r2.get("ok") is True and r2.get("object_count") == 12
                and abs(float((bbox2.get("size") or [0, 0, 0])[2]) - 1750.0) <= 1.0)
    check("E1 humanoid_prim = 12 件、总高 1750mm", human_ok,
          {"objects": r2.get("object_count"), "bbox": bbox2, "error": r2.get("error")})
    tmpl_names = list(r2.get("objects") or [])

    # ── E2. units=cm（输入按 cm 解释，5cm 立方 = 50mm）
    r3 = api("base", {"name": "cmtest", "units": "cm",
                      "parts": [{"id": "b", "prim": "box", "size_mm": [5, 5, 5], "at_mm": [0, 0, 2.5]}],
                      "render": False, "export": False, "outdir": outdir})
    bbox3 = (r3.get("bbox_mm") or {})
    cm_ok = (r3.get("ok") is True and r3.get("object_count") == 1
             and all(abs(float(x) - 50.0) <= 0.01 for x in (bbox3.get("size") or []))
             and abs(float((bbox3.get("min") or [0, 0, 1])[2])) <= 0.01)
    check("E2 units=cm 换算（5cm 立方 → 50mm，底部贴地）", cm_ok,
          {"bbox": bbox3, "error": r3.get("error")})
    cm_names = list(r3.get("objects") or [])

    # ── E3. export op：对已建场景补视图/补 GLB（不重建）
    before_names = scene_names()
    r4 = api("export", {"name": "selftest20",
                        "render": {"engine": "workbench", "res": [256, 256], "views": ["top"],
                                   "via": "internal", "sheet": False},
                        "glb": os.path.join(outdir, "selftest20_supp.glb")})
    supp_views = (r4.get("render") or {}).get("views") or []
    supp_glb = (r4.get("export") or {}).get("glb") or {}
    supp_glb_path = str(supp_glb.get("path") or "")
    supp_magic = False
    if supp_glb_path and os.path.isfile(supp_glb_path):
        with open(supp_glb_path, "rb") as f:
            supp_magic = f.read(4) == b"glTF"
    check("E3 export op 补视图 + 补 GLB（对象没重建）",
          r4.get("ok") is True and len(supp_views) == 1 and int((supp_views[0] or {}).get("bytes") or 0) > 0
          and supp_magic and scene_names() == before_names,
          {"ok": r4.get("ok"), "views": len(supp_views), "glb_magic": supp_magic,
           "new_objects": sorted(scene_names() - before_names)})

    # ── E4. batch 3 件（逐件 collection/清理；export 走 batch 级继承）
    before_batch = scene_names()
    r5 = api("batch", {"models": [
        {"name": "bt1", "parts": [{"id": "x", "prim": "box", "size_mm": [20, 20, 20], "at_mm": [0, 0, 10]}]},
        {"name": "bt2", "parts": [{"id": "x", "prim": "cylinder", "radius_mm": 8, "depth_mm": 30,
                                   "at_mm": [0, 0, 15], "axis": "z"}]},
        {"name": "bt3", "parts": [{"id": "x", "prim": "uv_sphere", "radius_mm": 10, "at_mm": [0, 0, 10]}]},
    ], "outdir": outdir, "export": {"glb": True}, "cleanup": True})
    batch_items = r5.get("items") or []
    batch_glbs = []
    for it in batch_items:
        g = ((it.get("export") or {}).get("glb") or {})
        p = str(g.get("path") or "")
        magic = False
        if p and os.path.isfile(p):
            with open(p, "rb") as f:
                magic = f.read(4) == b"glTF"
        batch_glbs.append({"name": it.get("name"), "ok": it.get("ok"), "bytes": g.get("bytes"),
                           "magic": magic, "objects": it.get("object_count")})
    batch_ok = (r5.get("ok") is True and r5.get("built") == 3 and len(batch_items) == 3
                and all(x["ok"] and x["magic"] and int(x["bytes"] or 0) > 0 for x in batch_glbs)
                and scene_names() == before_batch)
    check("E4 batch 3/3 + 逐件 GLB + cleanup 零残留", batch_ok,
          {"built": r5.get("built"), "items": batch_glbs,
           "new_objects": sorted(scene_names() - before_batch)})

    # ── E5. selftest op（模块内建自检）
    r6 = api("selftest", {})
    leftover = [o.name for o in bpy.context.scene.objects if str(o.name).startswith("BO-unittest")]
    check("E5 selftest op ok 且跑完清理", r6.get("ok") is True and not leftover,
          {"evidence_checks": (r6.get("evidence") or {}).get("checks"), "leftover": leftover,
           "error": r6.get("error")})

    # ── E6. 坏参数必须 ok:false（且不建对象）
    cases = [
        ("空 parts", {"name": "bad0", "parts": []}),
        ("坏 prim", {"name": "bad1", "parts": [{"id": "x", "prim": "cone", "radius_mm": 1, "depth_mm": 1}]}),
        ("box 缺 size", {"name": "bad2", "parts": [{"id": "x", "prim": "box"}]}),
        ("box 负尺寸", {"name": "bad3", "parts": [{"id": "x", "prim": "box", "size_mm": [-1, 2, 3]}]}),
        ("坏视角", {"name": "bad4", "parts": [{"id": "x", "prim": "box", "size_mm": [1, 1, 1]}],
                    "render": {"views": ["sideways"]}}),
        ("非 workbench 引擎", {"name": "bad5", "parts": [{"id": "x", "prim": "box", "size_mm": [1, 1, 1]}],
                              "render": {"engine": "eevee"}}),
    ]
    before_bad = scene_names()
    bad_results = []
    for label, args in cases:
        rr = api("base", args)
        bad_results.append({"case": label, "ok": rr.get("ok"), "error": (rr.get("error") or "")[:120]})
    r7 = api("export", {})
    r8 = api("nope", {})
    bad_ok = (all(x["ok"] is False for x in bad_results)
              and r7.get("ok") is False and r8.get("ok") is False
              and "base" in (r8.get("ops") or [])
              and scene_names() == before_bad)
    check("E6 坏参数/空调用/未知 op 全部 ok:false 且不污染场景", bad_ok,
          {"cases": bad_results, "export": {k: r7.get(k) for k in ("ok", "error") if k in r7},
           "unknown": {k: r8.get(k) for k in ("ok", "error") if k in r8},
           "new_objects": sorted(scene_names() - before_bad)})

    # ── 清理（只删本测试建的对象/空集合；产物 PNG/GLB 保留作证据）
    for nm in list(names) + list(tmpl_names) + list(cm_names):
        ob = bpy.data.objects.get(str(nm))
        if ob is not None:
            data = ob.data if ob.type == "MESH" else None
            try:
                bpy.data.objects.remove(ob, do_unlink=True)
            except Exception:
                pass
            if data is not None and data.users == 0:
                try:
                    bpy.data.meshes.remove(data, do_unlink=True)
                except Exception:
                    pass
    for colname in ("BLOCKOUT", "BLOCKOUT-BATCH-bt1", "BLOCKOUT-BATCH-bt2", "BLOCKOUT-BATCH-bt3"):
        col = bpy.data.collections.get(colname)
        if col is not None and not len(col.objects) and not len(col.children):
            try:
                bpy.data.collections.remove(col)
            except Exception:
                pass
    wall_ms = int(round((time.perf_counter() - t_start) * 1000.0))
    return finish(t_start, api=api, extra={
        "tris": r.get("total_tris"), "tris_eval": r.get("total_tris_eval"),
        "bbox_mm": bbox, "object_count": r.get("object_count"),
        "render_via": via, "views": view_rows,
        "sheet": {"bytes": sheet.get("bytes"), "res": sheet.get("res"), "md5": (sheet.get("md5") or "")[:12]},
        "glb": {"path": gpath, "bytes": glb.get("bytes"), "magic": magic_ok, "nodes": nodes},
        "ms": {"geom": geom_ms, "views": views_ms, "export": export_ms, "base_total": total_ms,
               "base_wall": base_wall_ms, "wall": wall_ms},
        "outdir": outdir,
        "batch": {"built": r5.get("built"), "items": batch_glbs},
        "selftest_op": (r6.get("evidence") or {}).get("checks"),
        "humanoid": {"objects": r2.get("object_count"), "height_mm": (bbox2.get("size") or [0, 0, 0])[2]},
        "units_cm_box_mm": (bbox3.get("size") or [None])[0],
    })


def finish(t_start, api=None, extra=None):
    wall = int(round((time.perf_counter() - t_start) * 1000.0))
    out = {"ok": len(FAILS) == 0, "probe": "blockout_selftest", "version": 1,
           "pass": len(OKS), "fail": len(FAILS), "fails": FAILS,
           "wall_ms": wall}
    if extra:
        out.update(extra)
    print("")
    print("blockout selftest：%d 通过 / %d 失败（%.1fs）"
          % (len(OKS), len(FAILS), wall / 1000.0))
    if FAILS:
        for f in FAILS:
            print("  - " + f["name"])
    print("HEADLESS " + json.dumps(out, ensure_ascii=False, default=str))
    return out


main()
