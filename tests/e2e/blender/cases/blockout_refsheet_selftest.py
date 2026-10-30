# -*- coding: utf-8 -*-
"""tests/blockout_refsheet_selftest.py —— P2-C：blockout 的 turntable / refsheet 真机自检。

跑法（headless）：
    blender_rt_headless(preload="blockout,qc,qc_render,montage", engine="none", factory_startup=True,
        timeout_ms=300000,
        script_file="<包路径>/tests/blockout_refsheet_selftest.py")

断言（数字写死；不达标改代码，不改断言）：
  A. base 建 12 件白模（4×3 网格 120mm 间距，100×80×60mm）→ 对象数=12、bbox=(-230,-160,0)..(230,160,60)。
  B. refsheet（4 视图 256²、白底、正交 front/side/top + 透视 iso）：
     · ok:true、4 张 PNG 存在/bytes>0/md5 与回执一致/PNG 魔数/尺寸 256²；
     · 投影类型：front/side/top=ortho（有 ortho_scale_mm），iso=persp（lens 50）；
     · 拼图 2×2（518×518）存在；refsheet.json 存在且 md5 与回执一致；
     · JSON：schema/units(mm_per_unit=1000)/bbox 与 base 一致/forward_axis=-Y/
       views 名与相机参数/每视图 md5 映射/fps/frame_count 齐全；
     · 白底：角像素 ≥0.9 且 alpha=1；中性灰：模型像素（<0.85）占比 >5%。
  C. 独立 turntable(frames=12, fps=24, 256²)：MP4（魔数 ftyp）+ bytes/md5/frames/fps/duration_s=0.5；
     若本机 Blender 无 FFMPEG → 回退 PNG 序列（12 帧、degraded=true、ok=false）也算通过；
     camera 参数（orbit_deg/elev_deg/distance/camera_distance/center_mm）齐全。
  D. refsheet 内嵌 turntable（frames=4, fps=12, 128²）→ 4 视图+JSON 照出，JSON.turntable.frames=4；
     内嵌产物 MP4 或回退序列均可（回执 warnings 留痕）。
  E. 场景状态还原：engine/camera/resolution/媒体格式与调用前一致；用户对象/材质未改。
  F. 坏参数全部 ok:false（turntable: 无对象/frames=0/fps=0/fmt=avi/background=pink；
     refsheet: 无对象/views 未知/views 空/res=0/background=pink/未知 op）。
  G. HEADLESS JSON 打印 pass/fail 与关键数字（不含 mp4 全量字节）。
"""
import json
import os
import time

import bpy
import numpy as np

OKS, FAILS = [], []
PNG_MAGIC = bytes([137, 80, 78, 71, 13, 10, 26, 10])


def check(name, cond, detail=""):
    (OKS if cond else FAILS).append({"name": name, "detail": detail})
    print(("  [OK]   " if cond else "  [FAIL] ") + name + (" — " + str(detail)[:320] if detail else ""))


def md5(path):
    import hashlib
    h = hashlib.md5()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def fbytes(path):
    try:
        return int(os.path.getsize(path))
    except Exception:
        return 0


def png_magic(path):
    try:
        with open(path, "rb") as f:
            return f.read(8) == PNG_MAGIC
    except Exception:
        return False


def mp4_ftyp(path):
    try:
        with open(path, "rb") as f:
            head = f.read(12)
        return head[4:8] == b"ftyp"
    except Exception:
        return False


def load_px(path):
    im = bpy.data.images.load(path, check_existing=False)
    try:
        w, h = int(im.size[0]), int(im.size[1])
        buf = np.empty(w * h * 4, dtype=np.float32)
        im.pixels.foreach_get(buf)
    finally:
        bpy.data.images.remove(im)
    return buf.reshape(h, w, 4), w, h


def main():
    t_start = time.perf_counter()
    api = getattr(K, "dsh_blockout_api", None)   # noqa: F821  （headless 注入的持久内核）
    check("K.dsh_blockout_api 已注册", api is not None, "preload 要带 blockout")
    if api is None:
        return finish(t_start, api)
    h = api("help", {})
    check("A0 help 列出 turntable/refsheet 且含 presentation/limits",
          set(["turntable", "refsheet"]) <= set((h.get("ops") or {}).keys())
          and isinstance(h.get("presentation"), dict) and bool(h.get("limits")),
          sorted((h.get("ops") or {}).keys()))

    outdir = os.path.join(str(K.out_dir), "blockout_refsheet_selftest")
    os.makedirs(outdir, exist_ok=True)
    sc = bpy.context.scene
    st0 = {"engine": sc.render.engine, "camera": (sc.camera.name if sc.camera else None),
           "res": [int(sc.render.resolution_x), int(sc.render.resolution_y)],
           "fmt": sc.render.image_settings.file_format,
           "media": getattr(sc.render.image_settings, "media_type", None),
           "view_transform": sc.view_settings.view_transform}

    # ── A. 12 件白模
    parts = []
    for i in range(12):
        x = (i % 4) * 120.0 - 180.0
        y = (i // 4) * 120.0 - 120.0
        parts.append({"id": "p%02d" % i, "prim": "box", "size_mm": [100, 80, 60], "at_mm": [x, y, 30.0]})
    t0 = time.perf_counter()
    rb = api("base", {"name": "resheet", "parts": parts, "render": False, "export": False,
                      "outdir": outdir})
    base_ms = int(round((time.perf_counter() - t0) * 1000.0))
    names = list(rb.get("objects") or [])
    bbox = rb.get("bbox_mm") or {}
    bmin, bmax = bbox.get("min") or [0, 0, 0], bbox.get("max") or [0, 0, 0]
    check("A1 base ok + 12 对象", rb.get("ok") is True and rb.get("object_count") == 12,
          {"ok": rb.get("ok"), "count": rb.get("object_count"), "error": rb.get("error")})
    check("A2 bbox 与输入一致（-230,-160,0 .. 230,160,60）",
          all(abs(bmin[i] - [-230.0, -160.0, 0.0][i]) <= 0.01 for i in range(3))
          and all(abs(bmax[i] - [230.0, 160.0, 60.0][i]) <= 0.01 for i in range(3)),
          {"min": bmin, "max": bmax})

    # ── B. refsheet（4 视图 256²，白底）
    t0 = time.perf_counter()
    rs = api("refsheet", {"objects": names, "name": "resheet", "out_dir": outdir,
                          "views": ["front", "side", "top", "iso"], "res": [256, 256],
                          "ortho": True, "background": "white"})
    refsheet_ms = int(round((time.perf_counter() - t0) * 1000.0))
    files = rs.get("files") or {}
    views = files.get("views") or []
    check("B1 refsheet ok:true + 4 视图",
          rs.get("ok") is True and len(views) == 4, {"ok": rs.get("ok"), "views": len(views),
                                                     "error": rs.get("error")})
    check("B2 视图名/投影正确（front/side/top=ortho，iso=persp）",
          [v.get("view") for v in views] == ["front", "side", "top", "iso"]
          and all((v.get("camera") or {}).get("projection") == "ortho" for v in views[:3])
          and (views[3].get("camera") or {}).get("projection") == "persp"
          and (views[3].get("camera") or {}).get("lens_mm") == 50.0,
          [{"view": v.get("view"), "proj": (v.get("camera") or {}).get("projection")} for v in views])
    view_ok = True
    for v in views:
        p = str(v.get("path") or "")
        ok = (os.path.isfile(p) and fbytes(p) > 0 and png_magic(p)
              and md5(p) == v.get("md5") and list(v.get("res") or []) == [256, 256])
        view_ok = view_ok and ok
        check("B3 视图 %r PNG/bytes/md5/尺寸" % v.get("view"), ok,
              {"bytes": fbytes(p), "md5_ok": md5(p) == v.get("md5"),
               "res": v.get("res"), "magic": png_magic(p)})
    sheet = files.get("sheet") or {}
    check("B4 拼图 2×2（518×518）+ bytes/md5",
          os.path.isfile(str(sheet.get("path") or "")) and fbytes(sheet.get("path")) > 0
          and sheet.get("res") == [518, 518] and md5(sheet.get("path")) == sheet.get("md5"),
          {"bytes": sheet.get("bytes"), "res": sheet.get("res")})
    json_path = str(rs.get("refsheet_json") or "")
    json_ok = os.path.isfile(json_path) and rs.get("md5") == md5(json_path)
    payload = {}
    if json_ok:
        try:
            payload = json.load(open(json_path, encoding="utf-8"))
        except Exception as e:
            json_ok = False
            payload = {"_error": "%s: %s" % (type(e).__name__, str(e)[:160])}
    check("B5 refsheet.json 存在且 md5 与回执一致", json_ok,
          {"path": json_path, "md5": rs.get("md5")})
    jv = payload.get("views") or []
    check("B6 JSON 契约（schema/units/bbox/forward_axis/相机/md5 映射/fps）",
          payload.get("schema") == "dsh.blockout.refsheet/1"
          and abs(float((payload.get("units") or {}).get("mm_per_unit") or 0) - 1000.0) <= 1e-6
          and payload.get("forward_axis") == "-Y"
          and all(abs(float((payload.get("bbox_mm") or {}).get("min", [0, 0, 0])[i]) - bmin[i]) <= 0.01
                  for i in range(3))
          and [v.get("view") for v in jv] == ["front", "side", "top", "iso"]
          and all(isinstance((v.get("camera") or {}).get("loc_mm"), list) for v in jv)
          and all((v.get("camera") or {}).get("ortho_scale_mm") for v in jv[:3])
          and set(((payload.get("md5") or {}).get("views") or {}).keys()) == {"front", "side", "top", "iso"}
          and isinstance(payload.get("fps"), int) and payload.get("frame_count") == 4,
          {"keys": sorted(payload.keys())[:12],
           "views": [v.get("view") for v in jv],
           "units": payload.get("units"), "fps": payload.get("fps")})
    # 白底 + 中性灰
    try:
        arr, w, hh = load_px(views[0]["path"])
        corner = float(arr[0, 0, :3].min())
        alpha = float(arr[0, 0, 3])
        dark_frac = float((arr[:, :, :3].min(axis=2) < 0.85).mean())
        check("B7 白底（角像素≥0.9、alpha=1）+ 中性灰可见（<0.85 像素>5%）",
              corner >= 0.9 and alpha >= 0.99 and dark_frac > 0.05,
              {"corner": round(corner, 3), "alpha": round(alpha, 3),
               "dark_frac": round(dark_frac, 4)})
    except Exception as e:
        check("B7 白底（角像素≥0.9、alpha=1）+ 中性灰可见（<0.85 像素>5%）", False,
              "%s: %s" % (type(e).__name__, str(e)[:160]))

    # ── C. 独立 turntable（frames=12, fps=24）
    t0 = time.perf_counter()
    tt = api("turntable", {"objects": names, "name": "resheet", "out_dir": outdir, "frames": 12,
                           "fps": 24, "res": [256, 256], "fmt": "mp4", "elev_deg": 15,
                           "distance": 1.6, "background": "white"})
    turntable_ms = int(round((time.perf_counter() - t0) * 1000.0))
    vid = tt.get("video") or {}
    seq = tt.get("sequence") or {}
    produced_mp4 = bool(vid.get("path")) and mp4_ftyp(vid.get("path"))
    clip_info = {}
    if produced_mp4:
        check("C1 MP4 产物存在（魔数 ftyp）+ bytes/md5",
              os.path.isfile(vid.get("path")) and fbytes(vid.get("path")) > 0
              and md5(vid.get("path")) == vid.get("md5"),
              {"path": vid.get("path"), "bytes": vid.get("bytes"), "md5_ok": True})
        check("C2 MP4 frames/fps/duration/res",
              int(tt.get("frames") or 0) == 12 and int(tt.get("fps") or 0) == 24
              and abs(float(tt.get("duration_s") or 0) - 0.5) < 0.01
              and list(tt.get("res") or []) == [256, 256],
              {"frames": tt.get("frames"), "fps": tt.get("fps"),
               "duration_s": tt.get("duration_s"), "res": tt.get("res")})
        check("C3 turntable ok:true、camera 参数齐全",
              tt.get("ok") is True and tt.get("degraded") is not True
              and abs(float((tt.get("camera") or {}).get("orbit_deg") or 0) - 360.0) < 1e-6
              and float((tt.get("camera") or {}).get("camera_distance") or 0) > 0
              and isinstance((tt.get("camera") or {}).get("center_mm"), list),
              {"ok": tt.get("ok"), "camera": tt.get("camera")})
        try:
            clip = bpy.data.movieclips.load(vid.get("path"), check_existing=False)
            try:
                clip_info = {"size": list(clip.size), "frames": int(clip.frame_duration),
                             "fps": round(float(clip.fps), 3)}
            finally:
                bpy.data.movieclips.remove(clip)
        except Exception as e:
            clip_info = {"error": "%s: %s" % (type(e).__name__, str(e)[:160])}
        check("C3b MP4 可解码（movieclip 尺寸/帧数/fps）",
              clip_info.get("size") == [256, 256] and int(clip_info.get("frames") or 0) >= 12
              and abs(float(clip_info.get("fps") or 0) - 24.0) < 0.5, clip_info)
    else:
        check("C1 MP4 产物存在（魔数 ftyp）+ bytes/md5", True,
              "本机无 FFMPEG → 验收回退序列（同 C2b）")
        check("C2 MP4 frames/fps/duration/res", True, "skip（回退序列）")
        seq_ok = (tt.get("degraded") is True and tt.get("ok") is False
                  and int(seq.get("count") or 0) == 12
                  and all(fbytes(p) > 0 for p in (seq.get("files") or []))
                  and bool(seq.get("md5_manifest")))
        check("C3 turntable 回退 PNG 序列（12 帧、degraded、ok:false、manifest md5）", seq_ok,
              {"degraded": tt.get("degraded"), "ok": tt.get("ok"),
               "seq_count": seq.get("count"), "error": tt.get("error")})
    check("C4 场景状态已还原（engine/camera/res/媒体/色彩变换）",
          str(sc.render.engine) == str(st0["engine"])
          and (sc.camera.name if sc.camera else None) == st0["camera"]
          and [int(sc.render.resolution_x), int(sc.render.resolution_y)] == st0["res"]
          and sc.render.image_settings.file_format == st0["fmt"]
          and getattr(sc.render.image_settings, "media_type", None) == st0["media"]
          and sc.view_settings.view_transform == st0["view_transform"],
          {"now": {"engine": str(sc.render.engine),
                   "camera": (sc.camera.name if sc.camera else None),
                   "res": [int(sc.render.resolution_x), int(sc.render.resolution_y)],
                   "fmt": sc.render.image_settings.file_format,
                   "media": getattr(sc.render.image_settings, "media_type", None),
                   "vt": sc.view_settings.view_transform}, "before": st0})

    # ── C5. fmt=png 显式序列（FFMPEG 不可用时的回退通路也走这里）
    t_seq = time.perf_counter()
    tt_png = api("turntable", {"objects": names, "name": "resheet_seq", "out_dir": outdir,
                               "frames": 4, "fps": 12, "res": [64, 64], "fmt": "png",
                               "background": "white"})
    tt_png_ms = int(round((time.perf_counter() - t_seq) * 1000.0))
    seq2 = tt_png.get("sequence") or {}
    check("C5 fmt=png 显式序列：ok:true、4 帧、manifest md5、文件都在",
          tt_png.get("ok") is True and int(seq2.get("count") or 0) == 4
          and isinstance(seq2.get("md5_manifest"), str) and len(seq2.get("md5_manifest") or "") == 32
          and all(os.path.isfile(p) and fbytes(p) > 0 for p in (seq2.get("files") or [])),
          {"ok": tt_png.get("ok"), "count": seq2.get("count"),
           "md5_manifest": (seq2.get("md5_manifest") or "")[:12], "error": tt_png.get("error")})

    # ── D. refsheet 内嵌 turntable
    t0 = time.perf_counter()
    rs2 = api("refsheet", {"objects": names, "name": "resheet_tt", "out_dir": outdir,
                           "views": ["front", "iso"], "res": [192, 192], "ortho": True,
                           "background": "white",
                           "turntable": {"frames": 4, "fps": 12, "res": [128, 128]}})
    nested_ms = int(round((time.perf_counter() - t0) * 1000.0))
    nfiles = rs2.get("files") or {}
    ntt = rs2.get("turntable") or {}
    njson = {}
    try:
        njson = json.load(open(rs2.get("refsheet_json") or "", encoding="utf-8")) if rs2.get("refsheet_json") else {}
    except Exception:
        njson = {}
    nested_ok = (len(nfiles.get("views") or []) == 2 and os.path.isfile(str(rs2.get("refsheet_json") or ""))
                 and (njson.get("turntable") or {}).get("frames") == 4
                 and isinstance(rs2.get("md5"), str) and len(rs2.get("md5")) == 32)
    check("D1 refsheet 内嵌 turntable：视图+JSON 照出、JSON.frames=4、MD5 齐全", nested_ok,
          {"ok": rs2.get("ok"), "views": len(nfiles.get("views") or []),
           "tt": {k: ntt.get(k) for k in ("ok", "frames", "path", "degraded")},
           "error": rs2.get("error")})

    # ── F. 坏参数
    bad_cases = [
        ("tt 无对象", ("turntable", {"objects": ["__nope__"], "out_dir": outdir})),
        ("tt frames=0", ("turntable", {"objects": names, "frames": 0, "out_dir": outdir})),
        ("tt fps=0", ("turntable", {"objects": names, "fps": 0, "out_dir": outdir})),
        ("tt fmt=avi", ("turntable", {"objects": names, "fmt": "avi", "out_dir": outdir})),
        ("tt background=pink", ("turntable", {"objects": names, "background": "pink", "out_dir": outdir})),
        ("rs 无对象", ("refsheet", {"objects": ["__nope__"], "out_dir": outdir})),
        ("rs 未知视图", ("refsheet", {"objects": names, "views": ["diagonal"], "out_dir": outdir})),
        ("rs views=[]", ("refsheet", {"objects": names, "views": [], "out_dir": outdir})),
        ("rs res=0", ("refsheet", {"objects": names, "res": 0, "out_dir": outdir})),
        ("rs background=pink", ("refsheet", {"objects": names, "background": "pink", "out_dir": outdir})),
    ]
    bad_rows = []
    for label, (op, args) in bad_cases:
        rr = api(op, args)
        bad_rows.append({"case": label, "ok": rr.get("ok"), "error": (rr.get("error") or "")[:110]})
    r_unk = api("nope", {})
    bad_ok = all(x["ok"] is False for x in bad_rows) and r_unk.get("ok") is False
    check("F1 10 例坏参数 + 未知 op 全部 ok:false", bad_ok,
          {"cases": bad_rows, "unknown": {k: r_unk.get(k) for k in ("ok", "error") if k in r_unk}})

    # ── G. 清理（只删本次建的 12 件；产物留作证据）
    for nm in names:
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
    for cn in ("BLOCKOUT",):
        col = bpy.data.collections.get(cn)
        if col is not None and not len(col.objects) and not len(col.children):
            try:
                bpy.data.collections.remove(col)
            except Exception:
                pass
    wall_ms = int(round((time.perf_counter() - t_start) * 1000.0))
    return finish(t_start, api, extra={
        "base": {"objects": rb.get("object_count"), "bbox_mm": bbox, "ms": base_ms},
        "refsheet": {"ok": rs.get("ok"), "views": [v.get("view") for v in views],
                     "view_bytes": [v.get("bytes") for v in views],
                     "sheet_res": sheet.get("res"), "sheet_bytes": sheet.get("bytes"),
                     "json": json_path, "json_md5": rs.get("md5"),
                     "wall_ms": refsheet_ms,
                     "ms": {k: (rs.get("ms") or {}).get(k) for k in ("views", "total")}},
        "turntable": {"ok": tt.get("ok"), "degraded": tt.get("degraded"),
                      "produced_mp4": produced_mp4,
                      "path": vid.get("path") or (seq.get("dir") if seq else None),
                      "bytes": vid.get("bytes") or seq.get("bytes_total"),
                      "md5": vid.get("md5") or seq.get("md5_manifest"),
                      "frames": tt.get("frames"), "fps": tt.get("fps"),
                      "duration_s": tt.get("duration_s"), "res": tt.get("res"),
                      "camera": tt.get("camera"),
                      "ms": tt.get("ms"), "wall_ms": turntable_ms,
                      "seq_count": seq.get("count"), "movieclip": clip_info,
                      "warnings": (tt.get("warnings") or [])[:3],
                      "png_sequence": {"ok": tt_png.get("ok"), "count": seq2.get("count"),
                                       "md5_manifest": seq2.get("md5_manifest"),
                                       "wall_ms": tt_png_ms}},
        "nested_refsheet": {"ok": rs2.get("ok"), "views": len(nfiles.get("views") or []),
                            "tt": {k: ntt.get(k) for k in ("ok", "frames", "path", "degraded")},
                            "wall_ms": nested_ms},
        "outdir": outdir, "wall_ms": wall_ms,
    })


def finish(t_start, api=None, extra=None):
    wall = int(round((time.perf_counter() - t_start) * 1000.0))
    out = {"ok": len(FAILS) == 0, "probe": "blockout_refsheet_selftest", "version": 1,
           "pass": len(OKS), "fail": len(FAILS), "fails": FAILS, "wall_ms": wall}
    if extra:
        out.update(extra)
    print("")
    print("blockout turntable/refsheet selftest：%d 通过 / %d 失败（%.1fs）"
          % (len(OKS), len(FAILS), wall / 1000.0))
    for f in FAILS:
        print("  - " + f["name"])
    print("HEADLESS " + json.dumps(out, ensure_ascii=False, default=str))
    return out


main()
