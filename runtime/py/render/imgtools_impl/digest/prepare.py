# -*- coding: utf-8 -*-
"""img_digest 阶段 1：参数校验与图像载入（行为与拆分前一致）。"""
from ..common import *  # noqa: F401,F403
from ..common import _j, _load_ex
from ..analysis import _md5_cached
from ..hough import _parse_known_lengths


def _digest_prepare(path, max_px, crop_max_px, known_len):
    """校验参数并解析 known_len；成功返回 (None, ctx)，失败返回 (err, None)。"""
    if path is None or str(path).strip() == "":
        return _j({"ok": False, "error": "path 必填（参考图路径）",
                   "hint": "img_digest(path='D:/ref/front.png', known_len={'mm':100,'p1_px':[100,80],'p2_px':[420,80]})"}), None
    path = str(path)
    try:
        max_px = int(max_px) if max_px else 0
    except Exception:
        return _j({"ok": False, "error": "max_px 必须是整数", "max_px": max_px}), None
    try:
        crop_max_px = int(crop_max_px or 900)
    except Exception:
        return _j({"ok": False, "error": "crop_max_px 必须是整数", "crop_max_px": crop_max_px}), None
    if known_len is not None:
        known_items = _parse_known_lengths(known_len)
        if not known_items:
            return _j({"ok": False, "error": "known_len 解析不出 (px, mm) 对",
                       "hint": "给 {'mm':100,'px':250} 或 {'mm':100,'p1_px':[x,y],'p2_px':[x,y]}（可列表）",
                       "known_len": known_len}), None
    else:
        known_items = []
    return None, {"path": path, "max_px": max_px, "crop_max_px": crop_max_px,
                  "known_items": known_items}


def _digest_load(ctx):
    """载图并构造 image_block；成功返回 (None, ctx)。"""
    path = ctx["path"]
    max_px = ctx["max_px"]
    try:
        arr, p, native_px, loaded_px = _load_ex(path, max_px=max_px)
    except Exception as e:
        return _j({"ok": False, "error": "%s: %s" % (type(e).__name__, str(e)[:200]),
                   "path": path}), None
    H, W = int(arr.shape[0]), int(arr.shape[1])
    md5 = _md5_cached(p)
    image_id = md5[:12]
    image_block = {"path": p, "md5": md5, "image_id": image_id,
                   "native_px": [int(native_px[0]), int(native_px[1])],
                   "loaded_px": [W, H],
                   "downscaled": bool(int(native_px[0]) * int(native_px[1]) > W * H),
                   "max_px": int(max_px)}
    ctx.update({"arr": arr, "p": p, "native_px": native_px, "loaded_px": loaded_px,
                "H": H, "W": W, "md5": md5, "image_id": image_id, "image_block": image_block})
    return None, ctx
