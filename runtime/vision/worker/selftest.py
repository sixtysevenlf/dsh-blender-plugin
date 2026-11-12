# -*- coding: utf-8 -*-
"""vision_selftest: bounded synthetic checks plus a non-loading DNN readiness probe.

The selftest never needs Blender and never writes outside memory. DNN is optional:
when features.dnn is off, or no model is configured, the DNN check is reported as
skipped and does not fail the run (vision contract).
"""
from __future__ import annotations

import time
from typing import Any

import numpy as np

from protocol import VisionError

import common

CHECK_NAMES = ("import", "synthetic_ops", "contours", "ecc", "flow", "dnn")


def _check(name: str, status: str, detail: str, **extra: Any) -> dict[str, Any]:
    return {"name": name, "status": status, "detail": detail, **extra}


def _import_check() -> dict[str, Any]:
    try:
        cv2 = common._cv2()
    except VisionError as exc:
        return _check("import", "fail", str(exc), fix=exc.fix)
    return _check(
        "import",
        "pass",
        "cv2 " + str(cv2.__version__) + " / numpy " + str(np.__version__),
        cv2=cv2.__version__,
        numpy=np.__version__,
    )


def _synthetic_checks() -> list[dict[str, Any]]:
    cv2 = common._cv2()
    checks: list[dict[str, Any]] = []
    gray = np.full((64, 64), 32, dtype=np.uint8)
    gray[16:48, 16:48] = 224
    mean = float(gray.mean())
    checks.append(
        _check(
            "synthetic_ops",
            "pass" if abs(mean - ((32 * 3072 + 224 * 1024) / 4096)) < 0.01 else "fail",
            "合成图均值 " + str(round(mean, 2)),
        )
    )

    _, mask = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    found, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    mask_pixels = int(cv2.countNonZero(mask))
    area = float(cv2.contourArea(found[0])) if found else 0.0
    # contourArea() of a pixel-aligned 32x32 square is 31*31 on some builds, so the
    # pass criterion counts mask pixels (version-independent) and only reports area.
    checks.append(
        _check(
            "contours",
            "pass" if len(found) == 1 and mask_pixels == 1024 else "fail",
            "Otsu+findContours：contours=" + str(len(found)) + "，mask_pixels=" + str(mask_pixels) + "，area=" + str(round(area, 2)),
        )
    )

    base = np.zeros((96, 96), dtype=np.uint8)
    base[30:60, 30:60] = 200
    shifted = np.roll(base, 3, axis=1)
    warp = np.eye(2, 3, dtype=np.float32)
    criteria = (cv2.TERM_CRITERIA_EPS | cv2.TERM_CRITERIA_COUNT, 100, 1e-5)
    try:
        cc, _ = cv2.findTransformECC(templateImage=base, inputImage=shifted, warpMatrix=warp, motionType=cv2.MOTION_EUCLIDEAN, criteria=criteria)
        checks.append(_check("ecc", "pass" if cc > 0.9 else "fail", "平移 3px 后 ECC cc=" + str(round(float(cc), 4))))
    except cv2.error as exc:
        checks.append(_check("ecc", "fail", "ECC 抛错：" + str(exc).splitlines()[0][:160]))

    first = np.zeros((96, 96), dtype=np.uint8)
    first[40:56, 20:40] = 255
    second = np.zeros((96, 96), dtype=np.uint8)
    second[40:56, 24:44] = 255
    flow = np.empty((96, 96, 2), dtype=np.float32)
    cv2.calcOpticalFlowFarneback(first, second, flow, 0.5, 3, 15, 3, 5, 1.2, 0)
    mean_dx = float(np.mean(flow[..., 0]))
    nonzero_dx = flow[..., 0][np.abs(flow[..., 0]) > 0.5]
    median_dx = float(np.median(nonzero_dx)) if nonzero_dx.size else 0.0
    # The block really moved +4 px, so assert the sign and the magnitude (not just
    # "some small number"), otherwise an all-zero flow field would pass.
    flow_ok = nonzero_dx.size > 0 and mean_dx > 0.0 and 2.0 <= median_dx <= 6.0
    checks.append(
        _check(
            "flow",
            "pass" if flow_ok else "fail",
            "平移 4px 的 farneback：mean dx="
            + str(round(mean_dx, 3))
            + "，median dx（非零区）="
            + str(round(median_dx, 3)),
        )
    )
    return checks


def _dnn_check(args: dict[str, Any], ctx: dict[str, Any]) -> dict[str, Any]:
    enabled = bool(args.get("dnn"))
    if not enabled:
        return _check("dnn", "skipped", "features.dnn 关闭；DNN 检查跳过，不影响自检结果。")
    models_dir = ctx.get("models_dir")
    has_models = bool(args.get("hasModels"))
    if not has_models:
        return _check("dnn", "skipped", "模型目录没有 .onnx 模型；DNN 检查跳过，不影响自检结果。", modelsDir=models_dir)
    model_id = args.get("model")
    if not model_id:
        return _check("dnn", "skipped", "尚未选择模型；DNN 检查跳过，选择模型后再测。", modelsDir=models_dir)
    try:
        import models  # noqa: PLC0415 - flat import inside the worker directory

        catalog = ctx.get("catalog")
        if catalog is None:
            catalog = models.load_catalog(str(models_dir or ""))
        entry = catalog.get(str(model_id))
    except VisionError as exc:
        return _check("dnn", "fail", str(exc), code=exc.code, modelsDir=models_dir)
    inference = ctx.get("inference")
    if inference is None:
        return _check("dnn", "fail", "worker 没有注入 inference 适配器")
    description = inference.describe() if hasattr(inference, "describe") else {}
    ensure = getattr(inference, "ensure", None)
    try:
        if callable(ensure):
            # Re-raise the stored VisionError so dnn_device_unavailable /
            # dnn_config_invalid are reported with their real code.
            ensure()
        elif not inference.available():
            raise VisionError(
                "dnn_backend_unavailable",
                "config",
                "推理后端不可用：" + str(description.get("reason") or inference.name),
                fix=description.get("fix"),
            )
    except VisionError as exc:
        return _check(
            "dnn",
            "fail",
            str(exc),
            code=exc.code,
            backend=inference.name,
            device=description.get("device"),
            fix=exc.fix if exc.fix is not None else description.get("fix"),
            details=exc.details,
        )
    return _check(
        "dnn",
        "pass",
        "模型条目 "
        + str(entry.id)
        + " 有效，backend="
        + str(inference.name)
        + "，deviceUsed="
        + str(description.get("deviceUsed") or "cpu")
        + "；自检不建 session、不加载 .onnx（首次真正加载发生在 vision_detect）。",
        model=str(entry.id),
        backend=str(inference.name),
        provider=description.get("provider"),
        deviceRequested=description.get("device"),
        deviceUsed=description.get("deviceUsed"),
        loaded=False,
    )


def run(args: dict[str, Any], ctx: dict[str, Any]) -> dict[str, Any]:
    started = time.perf_counter()
    checks: list[dict[str, Any]] = [_import_check()]
    if checks[0]["status"] == "pass":
        checks.extend(_synthetic_checks())
    else:
        for name in CHECK_NAMES[1:5]:
            checks.append(_check(name, "skipped", "cv2 不可用，跳过依赖 OpenCV 的检查。"))
    checks.append(_dnn_check(args, ctx))

    failed = [item for item in checks if item["status"] == "fail"]
    skipped = [item for item in checks if item["status"] == "skipped"]
    duration_ms = int(round((time.perf_counter() - started) * 1000))
    versions = ctx.get("versions") or {}
    return {
        "ok": not failed,
        "checks": checks,
        "summary": {
            "passed": sum(1 for item in checks if item["status"] == "pass"),
            "failed": len(failed),
            "skipped": len(skipped),
        },
        "versions": common.jsonable(versions),
        "backend": (ctx.get("config") or {}).get("backend"),
        "device": (ctx.get("config") or {}).get("device"),
        "duration_ms": duration_ms,
        "notes": [
            "DNN 关闭、无模型或未选模型时 dnn 检查为 skipped，不算失败（vision 契约）。",
            "自检使用内存合成图，不读写模型、不联网、不写工作区。",
        ],
    }
