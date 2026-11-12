# -*- coding: utf-8 -*-
"""vision_detect: YOLO-style ONNX detection on one local image.

The model is chosen by id from <home>/vision/models/models.yml; the worker never
invents a default path, never downloads and never falls back to a different
backend. The supported post-processing is the YOLOv5 layout
(1,N,5+classes with objectness). When models.yml has no labels file the class
count cannot be cross-checked, and the 4+classes layout without objectness is
also accepted; with labels present any other attribute count is rejected as
vision_model_unsupported so a YOLOv8-shaped head is never decoded as YOLOv5.
Results are at most maxDetections bounded boxes with class index + label.
"""
from __future__ import annotations

from typing import Any

import numpy as np

import common
from protocol import VisionError

DEFAULT_CONF = 0.25
DEFAULT_IOU = 0.45
DEFAULT_MAX_DETECTIONS = 100
MAX_DETECTIONS = 100
PAD_VALUE = 114


def _unwrap(outputs: Any) -> np.ndarray:
    value = outputs
    if isinstance(value, (list, tuple)):
        if not value:
            raise _unsupported("模型没有返回任何 output")
        value = value[0]
    array = np.asarray(value, dtype=np.float32)
    if array.ndim == 3 and array.shape[0] == 1:
        array = array[0]
    if array.ndim != 2:
        raise _unsupported("无法识别的模型输出形状：" + str(np.asarray(value).shape), {"shape": list(np.asarray(value).shape)})
    # (attributes, anchors) -> (anchors, attributes); YOLO exports sometimes transpose.
    # A YOLO attribute row is 4 box + objectness + at least one class (>= 5), so only a
    # first dimension that could plausibly be the attribute count is transposed.
    if 5 <= array.shape[0] < array.shape[1] and array.shape[0] <= 200:
        array = array.T
    return array


def _unsupported(message: str, details: dict[str, Any] | None = None) -> VisionError:
    return VisionError(
        "vision_model_unsupported",
        "config",
        message,
        fix="Use a YOLOv5 ONNX export whose output is (1,N,5+classes); check models.yml postprocessing/labels.",
        retryable=False,
        details=details,
    )


def _has_objectness(attributes: int, labels: tuple[str, ...] | None, model: str | None) -> bool:
    """Validate the attribute count against labels; never guess a YOLOv8 head."""
    if attributes < 5:
        raise _unsupported(
            "模型输出属性数过少：" + str(attributes) + "（YOLOv5 至少需要 5 列）",
            {"attributes": attributes, "model": model},
        )
    if labels is None:
        # Without labels the class count is unverifiable; accept the documented layouts.
        return attributes >= 6
    expected = 5 + len(labels)
    if attributes == expected:
        return True
    if attributes == 4 + len(labels):
        raise _unsupported(
            "模型输出 "
            + str(attributes)
            + " 列、labels "
            + str(len(labels))
            + " 行：缺少 objectness（YOLOv8 风格 head），postprocessing=yolov5 不支持",
            {"attributes": attributes, "classes": attributes - 4, "labels": len(labels), "model": model},
        )
    raise VisionError(
        "vision_model_unconfigured",
        "config",
        "模型输出 classes="
        + str(attributes - 5)
        + " 与 models.yml labels="
        + str(len(labels))
        + " 行不一致（输出 "
        + str(attributes)
        + " 列）",
        fix="让 labels 行数等于模型的类别数（或删掉 labels 只返回 class 索引）。",
        retryable=False,
        details={"attributes": attributes, "classes": attributes - 5, "labels": len(labels), "model": model},
    )


def decode_yolo(
    outputs: Any,
    *,
    conf: float,
    iou: float,
    max_detections: int,
    labels: tuple[str, ...] | None = None,
    model: str | None = None,
) -> tuple[list[dict[str, Any]], dict[str, int]]:
    """Turn raw ONNX output into bounded detections (letterbox coordinates)."""
    predictions = _unwrap(outputs)
    attributes = int(predictions.shape[1])
    has_objectness = _has_objectness(attributes, labels, model)
    classes = attributes - 5 if has_objectness else attributes - 4

    boxes_xywh = predictions[:, 0:4]
    if has_objectness:
        objectness = predictions[:, 4]
        class_scores = predictions[:, 5 : 5 + classes]
        class_index = np.argmax(class_scores, axis=1)
        scores = objectness * class_scores[np.arange(class_scores.shape[0]), class_index]
    else:
        class_scores = predictions[:, 4 : 4 + classes]
        class_index = np.argmax(class_scores, axis=1)
        scores = class_scores[np.arange(class_scores.shape[0]), class_index]

    keep = scores >= conf
    raw_count = int(scores.size)
    filtered = int(np.count_nonzero(keep))
    if filtered == 0:
        return [], {"raw_candidates": raw_count, "above_conf": 0, "after_nms": 0}
    boxes_xywh = boxes_xywh[keep]
    scores = scores[keep]
    class_index = class_index[keep]

    cx, cy, width, height = boxes_xywh[:, 0], boxes_xywh[:, 1], boxes_xywh[:, 2], boxes_xywh[:, 3]
    boxes = np.stack([cx - width / 2.0, cy - height / 2.0, cx + width / 2.0, cy + height / 2.0], axis=1)
    order = np.argsort(-scores)
    boxes = boxes[order]
    scores = scores[order]
    class_index = class_index[order]

    selected_boxes: list[np.ndarray] = []
    selected_scores: list[float] = []
    selected_classes: list[int] = []
    while boxes.shape[0] > 0 and len(selected_boxes) < max_detections:
        best = int(np.argmax(scores))
        best_box = boxes[best]
        best_class = int(class_index[best])
        selected_boxes.append(best_box)
        selected_scores.append(float(scores[best]))
        selected_classes.append(best_class)
        rest = np.delete(boxes, best, axis=0)
        rest_classes = np.delete(class_index, best)
        inter = np.maximum(0.0, np.minimum(rest[:, 2], best_box[2]) - np.maximum(rest[:, 0], best_box[0])) * np.maximum(
            0.0, np.minimum(rest[:, 3], best_box[3]) - np.maximum(rest[:, 1], best_box[1])
        )
        area_best = max(0.0, best_box[2] - best_box[0]) * max(0.0, best_box[3] - best_box[1])
        areas = np.maximum(0.0, rest[:, 2] - rest[:, 0]) * np.maximum(0.0, rest[:, 3] - rest[:, 1])
        union = area_best + areas - inter
        overlap = np.divide(inter, union, out=np.zeros_like(inter), where=union > 0)
        # Class-aware suppression: boxes of another class never suppress each other.
        keep_mask = (overlap <= iou) | (rest_classes != best_class)
        boxes = rest[keep_mask]
        scores = np.delete(scores, best)[keep_mask]
        class_index = rest_classes[keep_mask]

    detections = []
    for box, score, cls in zip(selected_boxes, selected_scores, selected_classes):
        # labels is either absent (class index only) or validated against classes above.
        label = labels[cls] if labels is not None else None
        detections.append(
            {
                "class": cls,
                "label": label,
                "confidence": common.round_num(score, 4),
                "xyxy": [common.round_num(float(v), 2) for v in box.tolist()],
            }
        )
    return detections, {"raw_candidates": raw_count, "above_conf": filtered, "after_nms": len(detections)}


def letterbox(image: np.ndarray, size: tuple[int, int]) -> tuple[np.ndarray, float, tuple[float, float]]:
    """Resize with unchanged aspect ratio and pad to size; returns (image, scale, (pad_x, pad_y))."""
    cv2 = common._cv2()
    target_w, target_h = int(size[0]), int(size[1])
    height, width = image.shape[:2]
    scale = min(target_w / float(width), target_h / float(height))
    new_w = max(1, int(round(width * scale)))
    new_h = max(1, int(round(height * scale)))
    resized = cv2.resize(image, (new_w, new_h), interpolation=cv2.INTER_LINEAR)
    canvas = np.full((target_h, target_w, 3), PAD_VALUE, dtype=np.uint8)
    pad_x = (target_w - new_w) // 2
    pad_y = (target_h - new_h) // 2
    canvas[pad_y : pad_y + new_h, pad_x : pad_x + new_w] = resized
    return canvas, scale, (float(pad_x), float(pad_y))


def _map_back(detections: list[dict[str, Any]], scale: float, pad: tuple[float, float], width: int, height: int) -> list[dict[str, Any]]:
    pad_x, pad_y = pad
    out = []
    for item in detections:
        x0, y0, x1, y1 = item["xyxy"]
        box = [
            common.clamp((x0 - pad_x) / scale, 0.0, float(width)),
            common.clamp((y0 - pad_y) / scale, 0.0, float(height)),
            common.clamp((x1 - pad_x) / scale, 0.0, float(width)),
            common.clamp((y1 - pad_y) / scale, 0.0, float(height)),
        ]
        out.append({**item, "xyxy": [common.round_num(v, 2) for v in box]})
    return out


def run(args: dict[str, Any], ctx: dict[str, Any]) -> dict[str, Any]:
    cv2 = common._cv2()
    path = common.require_file(args.get("path"))
    raw = common.read_image(path)
    bgr, _, _ = common.split_bgra(raw)
    height, width = bgr.shape[:2]

    config = ctx.get("config") or {}
    model_id = args.get("model") or config.get("model") or None
    if not model_id:
        raise common.invalid(
            "没有选择模型：传 model（models.yml 的 id），或在 vision 配置里设置 vision.model。",
            {"model": model_id},
        )
    catalog = ctx.get("catalog")
    if catalog is None:
        import models  # noqa: PLC0415 - flat import inside the worker directory

        catalog = models.load_catalog(str(ctx.get("models_dir") or ""))
    entry = catalog.get(str(model_id))

    inference = ctx.get("inference")
    if inference is None:
        raise common.invalid("worker 没有注入 inference 适配器", {"ctx": sorted(ctx.keys())})
    conf = common.as_float(args.get("conf"), DEFAULT_CONF, 0.0, 1.0, "conf")
    iou = common.as_float(args.get("iou"), DEFAULT_IOU, 0.0, 1.0, "iou")
    max_detections = common.as_int(args.get("maxDetections"), DEFAULT_MAX_DETECTIONS, 1, MAX_DETECTIONS, "maxDetections")

    ensure = getattr(inference, "ensure", None)
    if callable(ensure):
        # Re-raise the stored dnn_backend_unavailable/dnn_device_unavailable/dnn_config_invalid.
        ensure()
    elif not inference.available():
        description = inference.describe() if hasattr(inference, "describe") else {}
        raise VisionError(
            "dnn_backend_unavailable",
            "config",
            "推理后端不可用：" + str(description.get("reason") or inference.name),
            fix=description.get("fix"),
            retryable=False,
            details={"inference": description},
        )

    input_size = (int(entry.width), int(entry.height))
    padded, scale, pad = letterbox(bgr, input_size)
    mean = tuple(float(v) for v in (entry.mean or (0.0, 0.0, 0.0)))
    blob = cv2.dnn.blobFromImage(
        padded,
        scalefactor=float(entry.scale),
        size=input_size,
        mean=mean,
        swapRB=bool(entry.rgb),
        crop=False,
    )
    blob = np.ascontiguousarray(blob, dtype=np.float32)
    handle = inference.load(entry.path, input_size)
    outputs = inference.infer(handle, blob)
    detections, counters = decode_yolo(
        outputs,
        conf=conf,
        iou=iou,
        max_detections=max_detections,
        labels=tuple(entry.labels) if entry.labels is not None else None,
        model=str(entry.id),
    )
    detections = _map_back(detections, scale, pad, width, height)

    description = inference.describe() if hasattr(inference, "describe") else {}
    providers = []
    if hasattr(inference, "providers"):
        try:
            providers = list(inference.providers())
        except Exception:
            providers = []
    return {
        "path": path,
        "width": int(width),
        "height": int(height),
        "model": {
            "id": str(entry.id),
            "path": str(entry.path),
            "input_size": [input_size[0], input_size[1]],
            "classes": len(entry.labels) if entry.labels is not None else None,
        },
        "backend": str(inference.name),
        "device": {
            "requested": str(description.get("device") or getattr(inference, "device", "cpu")),
            "used": str(description.get("deviceUsed") or "cpu"),
        },
        "provider": description.get("provider") or (providers[0] if providers else "cpu"),
        "availableProviders": providers,
        "parameters": {"conf": conf, "iou": iou, "maxDetections": max_detections},
        "detections": detections,
        "count": len(detections),
        "counters": counters,
        "notes": [
            "xyxy 使用原图像素坐标、0-based、[x0,y0,x1,y1)；类别索引来自模型 head，label 仅在 models.yml 提供 labels 时给出。",
            "最多返回 " + str(MAX_DETECTIONS) + " 条检测；原始输出数组不会离开 worker。",
        ],
    }
