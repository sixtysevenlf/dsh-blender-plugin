# -*- coding: utf-8 -*-
"""vision_track: motion summaries + stability for an image sequence.

frames may point at render manifests (passes.beauty.path is used) or imgaes
directly; every resolved frame must exist and share one resolution. Two methods:
  * farneback (default): dense optical flow summarised per frame (no point tracks);
  * lk: sparse Lucas-Kanade points, at most 20 tracked trajectories (<=12 points
    each) + per-frame motion summaries.
A reference sequence (same length) yields per-frame RMSE/max error; a shorter
reference lists the missing frame indexes explicitly instead of treating them as 0.
"""
from __future__ import annotations

import math
from typing import Any

import numpy as np

import common
from protocol import VisionError

METHODS = ("farneback", "lk")
MAX_FRAMES = 120
MAX_TRACKS = 20
MAX_POLYLINE_POINTS = 12
#: Total decoded pixels for one sequence (sum of frames); a hard memory budget.
MAX_TOTAL_PIXELS = 256_000_000
STABLE_JITTER_PX = 2.0
STABLE_JUMP_RATIO = 0.1
STABLE_FLICKER_GRAY = 8.0
STABLE_FLICKER_P95_GRAY = 32.0


def _resolve_frame(value: Any, index: int) -> str:
    path = common.require_file(value, "frames[" + str(index) + "]")
    if not path.lower().endswith(".json"):
        return path
    manifest = common.read_json_file(path)
    if not isinstance(manifest, dict):
        raise common.invalid("帧 manifest 必须是 JSON 对象：" + path, {"path": path})
    if manifest.get("manifestVersion") == 1:
        passes = manifest.get("passes")
        if not isinstance(passes, dict) or not isinstance(passes.get("beauty"), dict):
            raise common.invalid(
                "track 需要 render manifest 的 beauty pass 才能做视觉运动分析：" + path,
                {"path": path, "available": list(passes.keys()) if isinstance(passes, dict) else []},
            )
        beauty = passes["beauty"].get("path")
        return common.require_file(beauty, "frames[" + str(index) + "].passes.beauty.path")
    image = manifest.get("image")
    return common.require_file(image, "frames[" + str(index) + "].image")


def _read_frames(values: Any) -> list[tuple[str, np.ndarray]]:
    if not isinstance(values, list) or not values:
        raise common.invalid("frames 必须是 1–" + str(MAX_FRAMES) + " 个图片/manifest 路径的数组", {"frames": values})
    if len(values) > MAX_FRAMES:
        raise common.invalid("frames 最多 " + str(MAX_FRAMES) + " 帧，收到 " + str(len(values)), {"count": len(values)})
    frames: list[tuple[str, np.ndarray]] = []
    resolution: tuple[int, int] | None = None
    total_pixels = 0
    for index, value in enumerate(values):
        path = _resolve_frame(value, index)
        raw = common.read_image(path, flags=1)  # cv2.IMREAD_COLOR
        gray = common.to_gray(raw)
        size = (int(gray.shape[1]), int(gray.shape[0]))
        if resolution is None:
            resolution = size
        elif size != resolution:
            raise common.invalid(
                "帧分辨率不一致：第 0 帧 " + str(resolution[0]) + "x" + str(resolution[1]) + "，第 " + str(index) + " 帧 " + str(size[0]) + "x" + str(size[1]),
                {"frame": index, "expected": list(resolution), "actual": list(size)},
            )
        total_pixels += size[0] * size[1]
        if total_pixels > MAX_TOTAL_PIXELS:
            raise VisionError(
                "vision_image_too_large",
                "input",
                "帧序列总像素 " + str(total_pixels) + " 超过 " + str(MAX_TOTAL_PIXELS) + " 像素预算（前 " + str(index + 1) + " 帧）",
                fix="缩短序列或降低单帧分辨率；一次 track 的帧会同时留在内存里。",
                retryable=False,
                details={
                    "totalPixels": total_pixels,
                    "limit": MAX_TOTAL_PIXELS,
                    "frames": index + 1,
                    "frameSize": [size[0], size[1]],
                },
            )
        frames.append((path, gray))
    return frames


def _farneback_distance(frames: list[tuple[str, np.ndarray]]) -> list[dict[str, Any]]:
    cv2 = common._cv2()
    out = []
    for index in range(1, len(frames)):
        prev = frames[index - 1][1]
        current = frames[index][1]
        flow = np.empty((prev.shape[0], prev.shape[1], 2), dtype=np.float32)
        cv2.calcOpticalFlowFarneback(prev, current, flow, 0.5, 3, 15, 3, 5, 1.2, 0)
        magnitude, _ = cv2.cartToPolar(flow[..., 0], flow[..., 1])
        out.append(
            {
                "frame": index,
                "mean_flow_px": common.round_num(float(np.mean(magnitude)), 3),
                "p95_flow_px": common.round_num(float(np.percentile(magnitude, 95)), 3),
                "mean_dx_px": common.round_num(float(np.mean(flow[..., 0])), 3),
                "mean_dy_px": common.round_num(float(np.mean(flow[..., 1])), 3),
            }
        )
    return out


def _lk_tracks(frames: list[tuple[str, np.ndarray]]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    cv2 = common._cv2()
    first = frames[0][1]
    points = cv2.goodFeaturesToTrack(first, maxCorners=MAX_TRACKS, qualityLevel=0.01, minDistance=10)
    if points is None:
        return [], []
    tracks: list[dict[str, Any]] = []
    for track_id in range(int(points.shape[0])):
        tracks.append({"id": track_id, "points": [tuple(float(v) for v in points[track_id, 0])], "alive": True})
    frame_summaries: list[dict[str, Any]] = []
    previous = first
    for index in range(1, len(frames)):
        current = frames[index][1]
        summaries = []
        displacements = []
        for track in tracks:
            if not track["alive"]:
                continue
            point = np.asarray([[track["points"][-1]]], dtype=np.float32)
            next_buffer = np.empty_like(point)
            next_point, status, _ = cv2.calcOpticalFlowPyrLK(previous, current, point, next_buffer)
            if status is None or int(status[0][0]) != 1 or next_point is None:
                track["alive"] = False
                continue
            x, y = float(next_point[0][0][0]), float(next_point[0][0][1])
            height, width = current.shape[:2]
            if not (0 <= x < width and 0 <= y < height):
                track["alive"] = False
                continue
            dx = x - track["points"][-1][0]
            dy = y - track["points"][-1][1]
            track["points"].append((x, y))
            displacements.append((dx, dy))
        if displacements:
            magnitudes = [math.hypot(dx, dy) for dx, dy in displacements]
            summaries = {
                "frame": index,
                "points_tracked": len(displacements),
                "mean_flow_px": common.round_num(float(np.mean(magnitudes)), 3),
                "p95_flow_px": common.round_num(float(np.percentile(magnitudes, 95)), 3),
                "mean_dx_px": common.round_num(float(np.mean([dx for dx, _ in displacements])), 3),
                "mean_dy_px": common.round_num(float(np.mean([dy for _, dy in displacements])), 3),
            }
        else:
            summaries = {
                "frame": index,
                "points_tracked": 0,
                "mean_flow_px": None,
                "p95_flow_px": None,
                "mean_dx_px": None,
                "mean_dy_px": None,
            }
        frame_summaries.append(summaries)
        previous = current
    finished = []
    for track in tracks:
        points = track["points"]
        if len(points) < 2:
            continue
        step = max(1, int(math.ceil(len(points) / MAX_POLYLINE_POINTS)))
        sampled = points[::step]
        if sampled[-1] != points[-1]:
            sampled.append(points[-1])
        path_length = sum(math.hypot(points[i][0] - points[i - 1][0], points[i][1] - points[i - 1][1]) for i in range(1, len(points)))
        finished.append(
            {
                "id": track["id"],
                "first_frame": 0,
                "last_frame": len(points) - 1,
                "frames_visible": len(points),
                "displacement_px": [
                    common.round_num(points[-1][0] - points[0][0], 2),
                    common.round_num(points[-1][1] - points[0][1], 2),
                ],
                "path_length_px": common.round_num(path_length, 2),
                "points": [[common.round_num(x, 2), common.round_num(y, 2)] for x, y in sampled[:MAX_POLYLINE_POINTS]],
            }
        )
    return frame_summaries, finished


def _luma_differences(frames: list[tuple[str, np.ndarray]]) -> list[dict[str, Any]]:
    """Per-transition brightness statistics: mean(|gray_t - gray_{t-1}|), p95, median/MAD."""
    out: list[dict[str, Any]] = []
    for index in range(1, len(frames)):
        previous = frames[index - 1][1].astype(np.int16)
        current = frames[index][1].astype(np.int16)
        diff = np.abs(current - previous)  # int16 keeps 0-255 differences exact
        median = float(np.median(diff))
        mad = float(np.median(np.abs(diff.astype(np.float64) - median)))
        out.append(
            {
                "frame": index,
                "mean_abs_diff": common.round_num(float(diff.mean(dtype=np.float64)), 3),
                "p95_abs_diff": common.round_num(float(np.percentile(diff, 95)), 3),
                "median_abs_diff": common.round_num(median, 3),
                "mad_abs_diff": common.round_num(mad, 3),
            }
        )
    return out


def _stability(summaries: list[dict[str, Any]], luma: list[dict[str, Any]], width: int, height: int) -> dict[str, Any]:
    speeds = [item["mean_flow_px"] for item in summaries if item["mean_flow_px"] is not None]
    luma_means = [item["mean_abs_diff"] for item in luma if item.get("mean_abs_diff") is not None]
    luma_p95 = [item["p95_abs_diff"] for item in luma if item.get("p95_abs_diff") is not None]
    luma_mads = [item["mad_abs_diff"] for item in luma if item.get("mad_abs_diff") is not None]
    diagonal = math.hypot(width, height)
    jump_threshold = max(8.0, STABLE_JUMP_RATIO * diagonal)
    jumps = [item["frame"] for item in summaries if item["mean_flow_px"] is not None and item["mean_flow_px"] > jump_threshold]
    flicker_gray = float(np.mean(luma_means)) if luma_means else None
    flicker_p95 = float(max(luma_p95)) if luma_p95 else None
    flicker_mad = float(np.median(luma_mads)) if luma_mads else None
    if not speeds and flicker_gray is None:
        return {
            "mean_motion_px": None,
            "motion_std_px": None,
            "flicker_px": None,
            "flicker_p95_gray": None,
            "flicker_mad_gray": None,
            "jitter_px": None,
            "jump_frames": [],
            "jump_threshold_px": common.round_num(jump_threshold, 2),
            "stable": None,
            "reason": "没有可用的运动估计或亮度差（帧数不足或所有点丢失）。",
        }
    mean_speed = float(np.mean(speeds)) if speeds else None
    motion_std = float(np.std(speeds)) if speeds else None
    deltas = [abs(speeds[i] - speeds[i - 1]) for i in range(1, len(speeds))]
    jitter = float(np.mean(deltas)) if deltas else 0.0
    stable = (
        not jumps
        and (motion_std is None or motion_std <= STABLE_JITTER_PX)
        and (flicker_gray is None or flicker_gray <= STABLE_FLICKER_GRAY)
        and (flicker_p95 is None or flicker_p95 <= STABLE_FLICKER_P95_GRAY)
    )
    return {
        "mean_motion_px": common.round_num(mean_speed, 3),
        "motion_std_px": common.round_num(motion_std, 3),
        "flicker_px": common.round_num(flicker_gray, 3),
        "flicker_p95_gray": common.round_num(flicker_p95, 3),
        "flicker_mad_gray": common.round_num(flicker_mad, 3),
        "jitter_px": common.round_num(jitter, 3),
        "jump_frames": jumps,
        "jump_threshold_px": common.round_num(jump_threshold, 2),
        "stable": stable,
        "criteria": {
            "jump_over_px": common.round_num(jump_threshold, 2),
            "motion_std_max_px": STABLE_JITTER_PX,
            "flicker_max_gray": STABLE_FLICKER_GRAY,
            "flicker_p95_max_gray": STABLE_FLICKER_P95_GRAY,
            "note": "flicker_px = 逐帧 mean(|gray_t - gray_{t-1}|) 的平均值（灰度 0-255，衡量亮度变化）；motion_std_px 才是运动量的标准差。stable = 无跳变帧、亮度与运动统计均不超阈值；启发式标记，不是物理真值。",
        },
    }


def _reference_error(frames: list[tuple[str, np.ndarray]], reference: Any) -> dict[str, Any] | None:
    if reference is None:
        return None
    if not isinstance(reference, list) or not reference:
        raise common.invalid("reference 必须是参考图片路径数组", {"reference": reference})
    missing: list[int] = []
    rmses: list[float] = []
    max_error = 0.0
    matched = 0
    for index in range(len(frames)):
        if index >= len(reference):
            missing.append(index)
            continue
        ref_path = common.require_file(reference[index], "reference[" + str(index) + "]")
        ref_raw = common.read_image(ref_path, flags=1)
        ref_gray = common.to_gray(ref_raw)
        frame_gray = frames[index][1]
        if ref_gray.shape != frame_gray.shape:
            raise common.invalid(
                "reference 与 frame 分辨率不一致：第 " + str(index) + " 帧",
                {"frame": index, "reference": [int(ref_gray.shape[1]), int(ref_gray.shape[0])], "frame_size": [int(frame_gray.shape[1]), int(frame_gray.shape[0])]},
            )
        diff = np.abs(ref_gray.astype(np.int32) - frame_gray.astype(np.int32)).astype(np.float64)
        rmse = float(np.sqrt(np.mean(np.square(diff))))
        rmses.append(rmse)
        max_error = max(max_error, float(np.max(diff)))
        matched += 1
    if len(reference) > len(frames):
        extra = list(range(len(frames), len(reference)))
    else:
        extra = []
    return {
        "provided": True,
        "matched_frames": matched,
        "missing_frames": missing,
        "extra_reference_frames": extra,
        "rmse": common.round_num(float(np.mean(rmses)) if rmses else None, 3),
        "max_error": common.round_num(max_error, 3),
        "units": "0-255 gray absolute difference; rmse is over matched pixels",
        "note": "缺失参考帧按缺失列出，不当作 0 误差。" if missing else "参考序列与帧数一致。",
    }


def run(args: dict[str, Any], ctx: dict[str, Any]) -> dict[str, Any]:
    method = common.as_choice(args.get("method"), METHODS, "farneback", "method")
    frames = _read_frames(args.get("frames"))
    width, height = int(frames[0][1].shape[1]), int(frames[0][1].shape[0])

    frame_summaries: list[dict[str, Any]] = []
    tracks: list[dict[str, Any]] = []
    if len(frames) >= 2:
        if method == "farneback":
            frame_summaries = _farneback_distance(frames)
        else:
            frame_summaries, tracks = _lk_tracks(frames)
    luma_summaries = _luma_differences(frames)
    stability = _stability(frame_summaries, luma_summaries, width, height)
    reference = _reference_error(frames, args.get("reference"))
    notes = []
    if len(frames) < 2:
        notes.append("只有 1 帧：没有帧间运动/亮度差可估计，stability 为 null。")
    if method == "farneback":
        notes.append("farneback 是稠密光流，只返回每帧运动摘要，不生成点轨迹；需要轨迹请用 method=lk。")
    notes.append("luma 是逐帧亮度差（0-255 灰度），与 motion 分开报告；flicker_px 反映亮度变化，不是运动量。")
    return {
        "method": method,
        "frames": [
            {"index": index, "path": path}
            for index, (path, _) in enumerate(frames)
        ],
        "motion": frame_summaries,
        "motion_units": "pixels per frame",
        "luma": luma_summaries,
        "luma_units": "0-255 gray absolute difference per pixel, aggregated per frame",
        "tracks": tracks,
        "tracks_limit": MAX_TRACKS,
        "stability": stability,
        "reference": reference,
        "limits": {
            "frames": MAX_FRAMES,
            "tracks": MAX_TRACKS,
            "polyline_points": MAX_POLYLINE_POINTS,
            "total_pixels": MAX_TOTAL_PIXELS,
        },
        "notes": notes,
    }
