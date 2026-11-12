# -*- coding: utf-8 -*-
"""vision_compare: align a candidate image to a reference and report bounded deltas.

Alignment order: ECC (MOTION_EUCLIDEAN) first; on failure ORB + homography. When
both fail the receipt says aligned=false and returns no IoU/delta numbers instead
of inventing them. Previews are written under <home>/vision/previews (at most 2).
"""
from __future__ import annotations

from typing import Any

import numpy as np

import common
from protocol import VisionError

CHANGED_LEVEL = 10  # 0-255 gray difference counted as changed
MAX_PREVIEWS = 2


def _metrics(
    reference_gray: np.ndarray,
    aligned_gray: np.ndarray,
    reference_bgr: np.ndarray,
    aligned_bgr: np.ndarray,
    reference_alpha: np.ndarray | None = None,
    aligned_alpha: np.ndarray | None = None,
) -> dict[str, Any]:
    cv2 = common._cv2()
    diff = cv2.absdiff(reference_gray, aligned_gray)
    changed = diff > CHANGED_LEVEL
    changed_pixels = int(np.count_nonzero(changed))
    total = int(diff.size)
    mad = float(diff.mean()) / 255.0

    # Alpha wins when present (it is warped with the same transform as the image).
    ref_fg = common.foreground_mask(reference_bgr, reference_alpha)
    aligned_fg = common.foreground_mask(aligned_bgr, aligned_alpha)
    iou = None
    if ref_fg["mask"] is not None and aligned_fg["mask"] is not None:
        a = ref_fg["mask"] > 0
        b = aligned_fg["mask"] > 0
        union = int(np.count_nonzero(a | b))
        if union:
            iou = float(np.count_nonzero(a & b)) / float(union)

    changed_bbox = None
    rows = np.where(np.any(changed, axis=1))[0]
    cols = np.where(np.any(changed, axis=0))[0]
    if rows.size and cols.size:
        changed_bbox = [int(cols[0]), int(rows[0]), int(cols[-1]) + 1, int(rows[-1]) + 1]

    return {
        "mad": common.round_num(mad, 6),
        "changed_pct": common.round_num(changed_pixels / float(total), 4) if total else 0.0,
        "changed_bbox": changed_bbox,
        "iou": common.round_num(iou, 4) if iou is not None else None,
        "changed_threshold": {"gray_abs_diff_gt": CHANGED_LEVEL, "scale": "0-255"},
    }


def _attempt_ecc(reference_bgr: np.ndarray, candidate_bgr: np.ndarray, candidate_alpha: np.ndarray | None = None) -> dict[str, Any] | None:
    cv2 = common._cv2()
    reference_gray = common.to_gray(reference_bgr)
    candidate_gray = common.to_gray(candidate_bgr)
    warp = np.eye(2, 3, dtype=np.float32)
    criteria = (cv2.TERM_CRITERIA_EPS | cv2.TERM_CRITERIA_COUNT, 200, 1e-6)
    try:
        cc, warp = cv2.findTransformECC(templateImage=reference_gray, inputImage=candidate_gray, warpMatrix=warp, motionType=cv2.MOTION_EUCLIDEAN, criteria=criteria)
    except cv2.error as exc:
        return {"ok": False, "reason": "ECC failed: " + str(exc).splitlines()[0][:200]}
    height, width = reference_bgr.shape[:2]
    aligned = cv2.warpAffine(
        candidate_bgr,
        warp,
        (width, height),
        flags=cv2.INTER_LINEAR | cv2.WARP_INVERSE_MAP,
        borderMode=cv2.BORDER_REPLICATE,
    )
    aligned_alpha = None
    if candidate_alpha is not None:
        aligned_alpha = cv2.warpAffine(
            candidate_alpha,
            warp,
            (width, height),
            flags=cv2.INTER_NEAREST | cv2.WARP_INVERSE_MAP,
            borderMode=cv2.BORDER_CONSTANT,
            borderValue=(0, 0, 0, 0),
        )
    return {
        "ok": True,
        "method": "ecc",
        "motion": "euclidean",
        "cc": common.round_num(float(cc), 6),
        "transform": [[common.round_num(v, 6) for v in row] for row in warp.tolist()],
        "aligns": "candidate -> reference (applied with WARP_INVERSE_MAP)",
        "aligned": aligned,
        "alignedAlpha": aligned_alpha,
    }


def _attempt_orb(reference_bgr: np.ndarray, candidate_bgr: np.ndarray, candidate_alpha: np.ndarray | None = None) -> dict[str, Any] | None:
    cv2 = common._cv2()
    reference_gray = common.to_gray(reference_bgr)
    candidate_gray = common.to_gray(candidate_bgr)
    orb = cv2.ORB.create(nfeatures=2000)
    # A full mask is equivalent to "no mask" and satisfies the MatLike signature.
    detection_mask = np.full(reference_gray.shape[:2], 255, dtype=np.uint8)
    key_ref, des_ref = orb.detectAndCompute(reference_gray, detection_mask)
    key_cand, des_cand = orb.detectAndCompute(candidate_gray, detection_mask)
    if des_ref is None or des_cand is None:
        return {"ok": False, "reason": "ORB found too few descriptors"}
    matcher = cv2.BFMatcher(cv2.NORM_HAMMING, crossCheck=False)
    pairs = matcher.knnMatch(des_cand, des_ref, k=2)
    good = [first for first, second in (pair for pair in pairs if len(pair) == 2) if first.distance < 0.75 * second.distance]
    if len(good) < 8:
        return {"ok": False, "reason": "ORB matches < 8 (" + str(len(good)) + ")"}
    src = np.asarray([key_cand[first.queryIdx].pt for first in good], dtype=np.float32).reshape(-1, 1, 2)
    dst = np.asarray([key_ref[first.trainIdx].pt for first in good], dtype=np.float32).reshape(-1, 1, 2)
    homography, mask = cv2.findHomography(src, dst, cv2.RANSAC, 3.0)
    if homography is None:
        return {"ok": False, "reason": "ORB homography RANSAC failed"}
    height, width = reference_bgr.shape[:2]
    aligned = cv2.warpPerspective(candidate_bgr, homography, (width, height), flags=cv2.INTER_LINEAR)
    aligned_alpha = None
    if candidate_alpha is not None:
        aligned_alpha = cv2.warpPerspective(
            candidate_alpha,
            homography,
            (width, height),
            flags=cv2.INTER_NEAREST,
            borderMode=cv2.BORDER_CONSTANT,
            borderValue=(0, 0, 0, 0),
        )
    inliers = int(mask.sum()) if mask is not None else 0
    return {
        "ok": True,
        "method": "orb",
        "motion": "homography",
        "inliers": inliers,
        "matches": len(good),
        "transform": [[common.round_num(v, 6) for v in row] for row in homography.tolist()],
        "aligns": "candidate -> reference (warpPerspective)",
        "aligned": aligned,
        "alignedAlpha": aligned_alpha,
    }


def _diff_preview(reference_bgr: np.ndarray, aligned_bgr: np.ndarray) -> np.ndarray:
    cv2 = common._cv2()
    diff = cv2.absdiff(reference_bgr, aligned_bgr)
    gray = cv2.cvtColor(diff, cv2.COLOR_BGR2GRAY)
    heat = cv2.applyColorMap(np.clip(gray.astype(np.int32) * 3, 0, 255).astype(np.uint8), cv2.COLORMAP_INFERNO)
    return heat


def run(args: dict[str, Any], ctx: dict[str, Any]) -> dict[str, Any]:
    cv2 = common._cv2()
    reference_path = common.require_file(args.get("ref"), "ref")
    candidate_path = common.require_file(args.get("candidate"), "candidate")
    align = common.as_choice(args.get("align"), ("ecc", "orb"), "ecc", "align")

    reference_raw = common.read_image(reference_path)
    candidate_raw = common.read_image(candidate_path)
    reference_bgr, reference_alpha, _ = common.split_bgra(reference_raw)
    candidate_bgr, candidate_alpha, _ = common.split_bgra(candidate_raw)
    if reference_bgr.shape[:2] != candidate_bgr.shape[:2]:
        raise common.invalid(
            "ref 与 candidate 分辨率必须一致：ref="
            + str(reference_bgr.shape[1])
            + "x"
            + str(reference_bgr.shape[0])
            + "，candidate="
            + str(candidate_bgr.shape[1])
            + "x"
            + str(candidate_bgr.shape[0]),
            {
                "ref": [int(reference_bgr.shape[1]), int(reference_bgr.shape[0])],
                "candidate": [int(candidate_bgr.shape[1]), int(candidate_bgr.shape[0])],
            },
        )
    height, width = reference_bgr.shape[:2]
    box = common.roi_box((height, width), args.get("roi"))
    reference = common.crop(reference_bgr, box)
    candidate = common.crop(candidate_bgr, box)
    reference_alpha = common.crop(reference_alpha, box) if reference_alpha is not None else None
    candidate_alpha = common.crop(candidate_alpha, box) if candidate_alpha is not None else None

    attempts: list[dict[str, Any]] = []
    order = ("ecc", "orb") if align == "ecc" else ("orb", "ecc")
    result: dict[str, Any] | None = None
    for method in order:
        attempt = _attempt_ecc(reference, candidate, candidate_alpha) if method == "ecc" else _attempt_orb(reference, candidate, candidate_alpha)
        if attempt is None:
            continue
        if attempt["ok"]:
            result = attempt
            break
        attempts.append({"method": method, "failed": True, "reason": attempt["reason"]})

    if result is None:
        return {
            "aligned": False,
            "roi": {"x": box[0], "y": box[1], "w": box[2], "h": box[3]},
            "align": {"requested": align, "used": None, "attempts": attempts},
            "metrics": None,
            "previews": [],
            "notes": ["两种对齐都失败；按约定不返回 IoU/差分数字，避免把未对齐的图当已对齐。"],
        }

    reference_gray = common.to_gray(reference)
    aligned = result.pop("aligned")
    aligned_alpha = result.pop("alignedAlpha", None)
    aligned_gray = common.to_gray(aligned)
    metrics = _metrics(reference_gray, aligned_gray, reference, aligned, reference_alpha, aligned_alpha)
    notes = ["IoU 用 Otsu/alpha 前景近似，不是像素级实例掩码；变化阈值见 metrics.changed_threshold。"]
    previews: list[dict[str, str]] = []
    try:
        previews.append(common.write_preview(str(ctx.get("home") or ""), aligned, "aligned", 0))
        previews.append(common.write_preview(str(ctx.get("home") or ""), _diff_preview(reference, aligned), "diff", 1))
    except (OSError, VisionError) as exc:
        # Previews are a convenience; a read-only/missing home must not fail the comparison.
        previews = []
        notes.append("预览图未写入（" + exc.__class__.__name__ + "）：" + str(exc))
    return {
        "aligned": True,
        "roi": {"x": box[0], "y": box[1], "w": box[2], "h": box[3]},
        "align": {
            "requested": align,
            "used": result["method"],
            "motion": result["motion"],
            "cc": result.get("cc"),
            "inliers": result.get("inliers"),
            "matches": result.get("matches"),
            "transform_2x3": result["transform"],
            "transform_maps": result["aligns"],
            "attempts": attempts,
        },
        "metrics": metrics,
        "previews": previews[:MAX_PREVIEWS],
        "notes": notes,
    }
