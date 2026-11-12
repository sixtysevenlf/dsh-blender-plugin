# -*- coding: utf-8 -*-
"""Shared helpers for the vision worker op modules.

The worker runs with its own directory on sys.path (flat imports), so op modules
do "import common" and "from protocol import VisionError". Everything here is
read-only: files are opened for reading, previews are the only writes and they go
under the DSH home passed in by the manager.

Conventions (vision contract):
  * image origin top-left, x right, y down; bbox = [x0, y0, x1, y1) 0-based;
  * lengths in pixels, depth in meters;
  * receipts carry scalars, small bounded arrays and paths only — never raw
    pixels, masks, flow fields or per-frame arrays.
"""
from __future__ import annotations

import json
import math
import os
from datetime import datetime, timezone
from typing import Any, Sequence

import numpy as np

from protocol import VisionError

#: 64 MP decode ceiling (vision contract); over this the worker refuses instead of OOM.
MAX_IMAGE_PIXELS = 64_000_000
#: 256 MiB encoded-file ceiling; a tiny compressed bomb must be rejected before imdecode.
MAX_IMAGE_FILE_BYTES = 256 * 1024 * 1024
#: Preview files written under <home>/vision/previews, at most this many per call.
MAX_PREVIEWS = 2
GLOB_CHARS = set("*?")

#: JPEG SOF markers (baseline/progressive/lossless/arithmetic variants).
_JPEG_SOF_MARKERS = frozenset({0xC0, 0xC1, 0xC2, 0xC3, 0xC5, 0xC6, 0xC7, 0xC9, 0xCA, 0xCB, 0xCD, 0xCE, 0xCF})


def _cv2():
    """Import cv2 lazily so numpy-only helpers stay testable without OpenCV."""
    try:
        import cv2  # noqa: PLC0415 - optional/heavy dependency
    except Exception as exc:  # pragma: no cover - depends on the venv
        raise VisionError(
            "vision_unavailable",
            "vision",
            "OpenCV(cv2) 不可用：" + str(exc),
            fix="安装插件管理的 vision venv（或检查 vision.venv 是否装了 opencv-python）；插件不会替你 pip。",
            retryable=False,
        ) from exc
    return cv2


def invalid(message: str, details: dict[str, Any] | None = None) -> VisionError:
    return VisionError("vision_input_invalid", "input", message, fix=None, retryable=False, details=details)


def require_file(path: Any, label: str = "path") -> str:
    """Accept only an existing local file; never a URL or glob.

    The returned value is the normalized realpath, so callers report and compare
    one canonical path. There is no sandbox claim: any readable local file is
    decoded (the host passes absolute paths). Only `*`, `?` and URLs are rejected;
    brackets are valid filename characters (for example `render_[0001].png`).
    """
    if not isinstance(path, str) or not path.strip():
        raise invalid(label + " 必须是已存在的图片文件路径（字符串）", {label: path})
    value = path.strip()
    if "://" in value:
        raise invalid(label + " 不接受 URL，只接受本地文件路径", {label: value})
    if any(ch in value for ch in GLOB_CHARS):
        raise invalid(label + " 不接受通配符（* / ?），只接受一个具体文件", {label: value})
    if not os.path.isfile(value):
        raise invalid(label + " 文件不存在：" + value, {label: value})
    return os.path.realpath(value)


def image_size(image: np.ndarray) -> tuple[int, int]:
    height, width = image.shape[:2]
    return int(width), int(height)


def check_pixels(width: int, height: int, path: str = "") -> None:
    if int(width) * int(height) > MAX_IMAGE_PIXELS:
        raise VisionError(
            "vision_image_too_large",
            "input",
            "图片 " + str(width) + "x" + str(height) + " 超过 64 MP 解码上限：" + path,
            fix="先缩小图片或传 roi 只分析局部，再重试。",
            retryable=False,
            details={"width": int(width), "height": int(height), "limit": MAX_IMAGE_PIXELS, "path": path},
        )


def _png_size(data: bytes) -> tuple[int, int] | None:
    """Read width/height from a PNG IHDR chunk without decoding pixels."""
    if len(data) >= 24 and data[:8] == b"\x89PNG\r\n\x1a\n" and data[12:16] == b"IHDR":
        return int.from_bytes(data[16:20], "big"), int.from_bytes(data[20:24], "big")
    return None


def _jpeg_size(data: bytes) -> tuple[int, int] | None:
    """Walk JPEG segments up to the first SOF and read its height/width."""
    if len(data) < 4 or data[0] != 0xFF or data[1] != 0xD8:
        return None
    total = len(data)
    index = 2
    while index + 2 <= total:
        if data[index] != 0xFF:
            index += 1
            continue
        marker = data[index + 1]
        index += 2
        while marker == 0xFF and index < total:  # fill bytes before the marker
            marker = data[index]
            index += 1
        if marker in (0xD8, 0xD9) or 0xD0 <= marker <= 0xD7:
            continue
        if marker == 0xDA:  # start of scan before any SOF: no header size available
            return None
        if index + 2 > total:
            return None
        length = int.from_bytes(data[index : index + 2], "big")
        if length < 2:
            return None
        if marker in _JPEG_SOF_MARKERS:
            if index + 7 > total:
                return None
            height = int.from_bytes(data[index + 3 : index + 5], "big")
            width = int.from_bytes(data[index + 5 : index + 7], "big")
            return width, height
        index += length
    return None


def encoded_size(path: str) -> tuple[int, int] | None:
    """Best-effort (width, height) from PNG IHDR / JPEG SOF, before decoding."""
    try:
        with open(path, "rb") as handle:
            data = handle.read(64 * 1024)
    except OSError:
        return None
    return _png_size(data) or _jpeg_size(data)


def _check_file_size(path: str) -> None:
    try:
        size = os.path.getsize(path)
    except OSError as exc:
        raise invalid("无法读取图片文件信息：" + path + "（" + str(exc) + "）", {"path": path}) from exc
    if size > MAX_IMAGE_FILE_BYTES:
        raise VisionError(
            "vision_image_too_large",
            "input",
            "图片文件 " + str(size) + " 字节超过 256 MiB 编码上限：" + path,
            fix="先缩小/重新导出图片，再重试；不要用压缩炸弹式图片。",
            retryable=False,
            details={"bytes": int(size), "limit": MAX_IMAGE_FILE_BYTES, "path": path},
        )


def read_image(path: Any, flags: int | None = None) -> np.ndarray:
    """Decode a local image (IMREAD_UNCHANGED by default) and enforce the size caps."""
    cv2 = _cv2()
    value = require_file(path)
    _check_file_size(value)
    header = encoded_size(value)
    if header is not None and header[0] > 0 and header[1] > 0:
        # Reject a declared size over the ceiling before allocating the pixel buffer.
        check_pixels(header[0], header[1], value)
    data = np.fromfile(value, dtype=np.uint8)
    if data.size == 0:
        raise invalid("图片文件为空：" + value, {"path": value})
    image = cv2.imdecode(data, cv2.IMREAD_UNCHANGED if flags is None else flags)
    if image is None:
        raise VisionError(
            "vision_invalid_image",
            "input",
            "无法解码图片：" + value,
            fix="确认文件是完整的 PNG/JPEG/BMP/TIFF，或重新导出。",
            retryable=False,
            details={"path": value},
        )
    height, width = image.shape[:2]
    check_pixels(width, height, value)
    return image


def split_bgra(image: np.ndarray) -> tuple[np.ndarray, np.ndarray | None, int]:
    """Return (BGR uint8, alpha uint8|None, source_bit_depth). Deep images scale to 8-bit."""
    cv2 = _cv2()
    bit_depth = int(image.dtype.itemsize * 8)
    source = image
    if source.ndim == 2:
        bgr = cv2.cvtColor(source, cv2.COLOR_GRAY2BGR)
        alpha = None
    elif source.shape[2] == 2:
        bgr = cv2.cvtColor(source[:, :, 0], cv2.COLOR_GRAY2BGR)
        alpha = source[:, :, 1]
    elif source.shape[2] == 4:
        bgr = source[:, :, :3]
        alpha = source[:, :, 3]
    else:
        bgr = source[:, :, :3]
        alpha = None
    if bgr.dtype != np.uint8:
        max_value = float(np.iinfo(bgr.dtype.name).max) if np.issubdtype(bgr.dtype, np.integer) else 65535.0
        bgr = np.clip(np.rint(bgr.astype(np.float64) / max_value * 255.0), 0, 255).astype(np.uint8)
        if alpha is not None:
            amax = float(np.iinfo(alpha.dtype.name).max) if np.issubdtype(alpha.dtype, np.integer) else 65535.0
            alpha = np.clip(np.rint(alpha.astype(np.float64) / amax * 255.0), 0, 255).astype(np.uint8)
    return bgr, alpha, bit_depth


def to_gray(bgr: np.ndarray) -> np.ndarray:
    cv2 = _cv2()
    if bgr.ndim == 2:
        return bgr
    if bgr.shape[2] == 1:
        return bgr[:, :, 0]
    return cv2.cvtColor(bgr, cv2.COLOR_BGR2GRAY)


def roi_box(shape: Sequence[int], roi: Any) -> tuple[int, int, int, int]:
    """Validate a {x,y,w,h} ROI against (height,width); None means the full frame."""
    height, width = int(shape[0]), int(shape[1])
    if roi is None:
        return 0, 0, width, height
    if not isinstance(roi, dict):
        raise invalid("roi 必须是 {x,y,w,h} 对象", {"roi": roi})
    values = []
    for key in ("x", "y", "w", "h"):
        value = roi.get(key)
        if not isinstance(value, int) or isinstance(value, bool):
            raise invalid("roi." + key + " 必须是整数像素", {"roi": roi})
        values.append(int(value))
    x, y, w, h = values
    if x < 0 or y < 0 or w <= 0 or h <= 0:
        raise invalid("roi 要求 x/y>=0 且 w/h>0", {"roi": roi})
    if x + w > width or y + h > height:
        raise invalid(
            "roi 超出图片分辨率：roi=" + json.dumps(roi) + "，resolution=" + str(width) + "x" + str(height),
            {"roi": roi, "resolution": [width, height]},
        )
    return x, y, w, h


def crop(image: np.ndarray, box: tuple[int, int, int, int]) -> np.ndarray:
    x, y, w, h = box
    return image[y : y + h, x : x + w]


def as_int(value: Any, default: int, minimum: int, maximum: int, label: str) -> int:
    if value is None:
        return default
    if not isinstance(value, int) or isinstance(value, bool) or value < minimum or value > maximum:
        raise invalid(label + " 必须是 " + str(minimum) + "–" + str(maximum) + " 的整数", {label: value})
    return int(value)


def as_float(value: Any, default: float, minimum: float, maximum: float, label: str) -> float:
    if value is None:
        return default
    if not isinstance(value, (int, float)) or isinstance(value, bool) or not math.isfinite(float(value)):
        raise invalid(label + " 必须是数字", {label: value})
    number = float(value)
    if number < minimum or number > maximum:
        raise invalid(label + " 必须在 " + str(minimum) + "–" + str(maximum) + " 之间", {label: value})
    return number


def as_choice(value: Any, allowed: Sequence[str], default: str, label: str) -> str:
    if value is None:
        return default
    if not isinstance(value, str) or value not in allowed:
        raise invalid(label + " 只能是 " + " | ".join(allowed), {label: value, "allowed": list(allowed)})
    return value


def round_num(value: Any, digits: int = 4) -> Any:
    if value is None:
        return None
    number = float(value)
    if not math.isfinite(number):
        return None
    return round(number, digits)


def clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def jsonable(value: Any) -> Any:
    """Recursively convert numpy values to JSON-safe Python scalars/small lists."""
    if isinstance(value, dict):
        return {str(key): jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [jsonable(item) for item in value]
    if isinstance(value, np.ndarray):
        return jsonable(value.tolist())
    if isinstance(value, np.generic):
        return jsonable(value.item())
    if isinstance(value, float):
        return round_num(value, 6)
    return value


def percentile(values: np.ndarray, q: float) -> float | None:
    if values.size == 0:
        return None
    return float(np.percentile(values, q))


def mask_bbox(mask: np.ndarray) -> list[int] | None:
    rows = np.where(np.any(mask > 0, axis=1))[0]
    cols = np.where(np.any(mask > 0, axis=0))[0]
    if rows.size == 0 or cols.size == 0:
        return None
    return [int(cols[0]), int(rows[0]), int(cols[-1]) + 1, int(rows[-1]) + 1]


def foreground_mask(
    bgr: np.ndarray,
    alpha: np.ndarray | None,
    *,
    min_area_ratio: float = 0.001,
) -> dict[str, Any]:
    """Best-effort foreground mask.

    Rule (documented in vision_help): an alpha channel wins (alpha > 0); otherwise
    Otsu on gray picks the smaller (minority) split as foreground, then morphological
    open and the largest connected component. Minority polarity matches the common
    case (one object on a larger background) and is reported as the method so callers
    never mistake it for a semantic mask. If nothing reliable is found the caller gets
    reliable=False and must not report a foreground bbox.
    """
    cv2 = _cv2()
    height, width = bgr.shape[:2]
    total = int(height) * int(width)
    if alpha is not None:
        mask = np.where(alpha > 0, 255, 0).astype(np.uint8)
        method = "alpha>0"
    else:
        gray = to_gray(bgr)
        _, threshold = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
        if int(np.count_nonzero(threshold)) > total / 2:
            # Otsu polarity is arbitrary; the foreground is assumed to be the minority split.
            threshold = 255 - threshold
        kernel = np.ones((3, 3), np.uint8)
        opened = cv2.morphologyEx(threshold, cv2.MORPH_OPEN, kernel)
        count, labels, stats, _ = cv2.connectedComponentsWithStats(opened, connectivity=8)
        method = "otsu_minority+largest_component"
        mask = np.zeros_like(opened)
        best = 0
        best_area = 0
        for index in range(1, count):
            area = int(stats[index, cv2.CC_STAT_AREA])
            if area > best_area:
                best, best_area = index, area
        if best and best_area / total >= min_area_ratio:
            mask[labels == best] = 255
        else:
            return {"mask": None, "method": method, "coverage": 0.0, "bbox": None, "reliable": False}
    area = int(np.count_nonzero(mask))
    coverage = area / float(total) if total else 0.0
    if area == 0 or coverage < min_area_ratio:
        return {"mask": None, "method": method, "coverage": coverage, "bbox": None, "reliable": False}
    return {
        "mask": mask,
        "method": method,
        "coverage": coverage,
        "bbox": mask_bbox(mask),
        "reliable": True,
    }


def contours(mask: np.ndarray, limit: int) -> tuple[list[dict[str, Any]], int]:
    """Return (bounded contours, total valid contours) so truncation is exact."""
    cv2 = _cv2()
    found, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    found = sorted(found, key=lambda item: float(cv2.contourArea(item)), reverse=True)
    out: list[dict[str, Any]] = []
    for item in found:
        area = float(cv2.contourArea(item))
        if area <= 0:
            continue
        x, y, w, h = cv2.boundingRect(item)
        moments = cv2.moments(item)
        if moments["m00"] > 0:
            cx = moments["m10"] / moments["m00"]
            cy = moments["m01"] / moments["m00"]
        else:
            cx = x + w / 2.0
            cy = y + h / 2.0
        out.append(
            {
                "area_px": round_num(area, 2),
                "perimeter_px": round_num(cv2.arcLength(item, True), 2),
                "bbox": [int(x), int(y), int(x + w), int(y + h)],
                "centroid": [round_num(cx, 2), round_num(cy, 2)],
            }
        )
    return out[:limit], len(out)


def hough_lines(gray: np.ndarray, limit: int) -> tuple[list[dict[str, Any]], int]:
    """Return (bounded lines, total valid lines) so truncation is exact."""
    cv2 = _cv2()
    edges = cv2.Canny(gray, 50, 150)
    raw = cv2.HoughLinesP(edges, 1, np.pi / 180.0, threshold=60, minLineLength=20, maxLineGap=4)
    if raw is None or np.asarray(raw).size == 0:
        return [], 0
    # OpenCV versions differ: (N, 1, 4) historically, (N, 4) in newer releases.
    lines = []
    for item in np.asarray(raw).reshape(-1, 4):
        x1, y1, x2, y2 = (int(v) for v in item)
        length = math.hypot(x2 - x1, y2 - y1)
        if length <= 0:
            continue
        angle = math.degrees(math.atan2(y2 - y1, x2 - x1))
        lines.append(
            {
                "x1": x1,
                "y1": y1,
                "x2": x2,
                "y2": y2,
                "length_px": round_num(length, 2),
                "angle_deg": round_num(angle, 2),
            }
        )
    lines.sort(key=lambda item: item["length_px"], reverse=True)
    return lines[:limit], len(lines)


def read_json_file(path: str) -> Any:
    try:
        with open(path, "r", encoding="utf-8") as handle:
            return json.load(handle)
    except OSError as exc:
        raise invalid("无法读取文件：" + path + "（" + str(exc) + "）", {"path": path}) from exc
    except ValueError as exc:
        raise invalid("JSON 解析失败：" + path + "（" + str(exc) + "）", {"path": path}) from exc


def preview_dir(home: str) -> str:
    """Return <home>/vision/previews; an empty home is rejected (no relative paths)."""
    value = str(home or "").strip()
    if not value:
        raise VisionError(
            "vision_unavailable",
            "vision",
            "worker 没有收到 vision home，无法写预览图",
            fix="重启 Gateway 并确认 vision home 已配置；预览图只写到 <home>/vision/previews。",
            retryable=True,
            details={"home": value},
        )
    target = os.path.join(value, "vision", "previews")
    os.makedirs(target, exist_ok=True)
    return target


def write_preview(home: str, image: np.ndarray, tag: str, index: int) -> dict[str, str]:
    """Write one bounded preview PNG under <home>/vision/previews and report its path."""
    cv2 = _cv2()
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    name = "vision_compare_" + stamp + "_" + tag + "_" + str(index) + ".png"
    directory = preview_dir(home)
    target = os.path.join(directory, name)
    base = os.path.realpath(directory)
    resolved = os.path.realpath(target)
    if resolved != base and not resolved.startswith(base + os.sep):
        raise VisionError(
            "vision_unavailable",
            "vision",
            "预览图路径逃出 <home>/vision/previews：" + resolved,
            retryable=False,
            details={"preview": resolved, "home": str(home or "")},
        )
    ok = cv2.imwrite(target, image)
    if not ok:
        raise VisionError(
            "vision_unavailable",
            "vision",
            "预览图写入失败：" + target,
            fix="检查 <home>/vision/previews 是否可写。",
            retryable=True,
            details={"preview": target},
        )
    return {"path": target, "kind": tag, "home": str(home or "")}
