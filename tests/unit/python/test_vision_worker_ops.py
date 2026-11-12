# -*- coding: utf-8 -*-
"""Vision worker op modules: pure helpers + detection post-processing.

OpenCV is part of the unit test stack (``requirements-dev.txt``): the worker
runtime ships it, and every contract here must run in CI rather than be gated
behind an import check.
"""
from __future__ import annotations

import io
import json
import os
import struct
import sys
import zlib
from pathlib import Path

import cv2
import numpy as np
import pytest

ROOT = Path(__file__).resolve().parents[3]
WORKER = ROOT / "runtime" / "vision" / "worker"
if str(WORKER) not in sys.path:
    sys.path.insert(0, str(WORKER))

import common  # noqa: E402
import detect  # noqa: E402
import track  # noqa: E402
from protocol import VisionError  # noqa: E402


def _error_code(exc: pytest.ExceptionInfo[VisionError]) -> str:
    return str(exc.value.code)


class TestCommonValidation:
    def test_require_file_rejects_url_glob_and_missing_paths(self, tmp_path: Path) -> None:
        for bad in ("https://example.com/a.png", "C:/shots/*.png", str(tmp_path / "missing.png")):
            with pytest.raises(VisionError) as exc:
                common.require_file(bad)
            assert _error_code(exc) == "vision_input_invalid"

    def test_require_file_accepts_an_existing_file(self, tmp_path: Path) -> None:
        target = tmp_path / "a.png"
        target.write_bytes(b"x")
        assert common.require_file(str(target)) == os.path.realpath(str(target))

    def test_require_file_accepts_glob_looking_literal_names(self, tmp_path: Path) -> None:
        # Only `*`, `?` and URLs are rejected; brackets are ordinary filename chars.
        target = tmp_path / "render_[0001].png"
        target.write_bytes(b"x")
        assert common.require_file(str(target)) == os.path.realpath(str(target))

    def test_check_pixels_enforces_the_64mp_limit(self) -> None:
        common.check_pixels(8000, 8000)
        with pytest.raises(VisionError) as exc:
            common.check_pixels(8001, 8000)
        assert _error_code(exc) == "vision_image_too_large"
        assert exc.value.stage == "input"

    def test_roi_box_defaults_to_full_frame_and_validates_bounds(self) -> None:
        assert common.roi_box((480, 640), None) == (0, 0, 640, 480)
        assert common.roi_box((480, 640), {"x": 10, "y": 20, "w": 100, "h": 50}) == (10, 20, 100, 50)
        for bad in (
            {"x": -1, "y": 0, "w": 4, "h": 4},
            {"x": 0, "y": 0, "w": 0, "h": 4},
            {"x": 600, "y": 0, "w": 100, "h": 4},
            {"x": 0.5, "y": 0, "w": 4, "h": 4},
        ):
            with pytest.raises(VisionError):
                common.roi_box((480, 640), bad)

    def test_bounded_argument_helpers(self) -> None:
        assert common.as_int(None, 7, 1, 10, "n") == 7
        assert common.as_int(3, 7, 1, 10, "n") == 3
        assert common.as_float(None, 0.25, 0.0, 1.0, "conf") == 0.25
        assert common.as_choice(None, ("a", "b"), "a", "m") == "a"
        for call in (
            lambda: common.as_int(11, 1, 1, 10, "n"),
            lambda: common.as_int(True, 1, 1, 10, "n"),
            lambda: common.as_float(2.0, 0.25, 0.0, 1.0, "conf"),
            lambda: common.as_float(float("nan"), 0.25, 0.0, 1.0, "conf"),
            lambda: common.as_choice("c", ("a", "b"), "a", "m"),
        ):
            with pytest.raises(VisionError) as exc:
                call()
            assert _error_code(exc) == "vision_input_invalid"

    def test_jsonable_rounds_numpy_values(self) -> None:
        value = {
            "scalar": np.float32(0.123456789),
            "int": np.int64(3),
            "array": np.array([1.0, 2.5]),
            "nested": [np.float64(1.0 / 3.0)],
            "text": "x",
        }
        assert common.jsonable(value) == {"scalar": 0.123457, "int": 3, "array": [1.0, 2.5], "nested": [0.333333], "text": "x"}

    def test_encoded_file_cap_is_enforced_before_decode(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        target = tmp_path / "small.png"
        assert cv2.imwrite(str(target), np.zeros((4, 4, 3), dtype=np.uint8))
        monkeypatch.setattr(common, "MAX_IMAGE_FILE_BYTES", target.stat().st_size - 1)
        with pytest.raises(VisionError) as exc:
            common.read_image(str(target))
        assert _error_code(exc) == "vision_image_too_large"
        assert exc.value.details["limit"] == target.stat().st_size - 1

    def test_declared_png_dimensions_are_rejected_before_decode(self, tmp_path: Path) -> None:
        ihdr = struct.pack(">IIBBBBB", 20000, 20000, 8, 2, 0, 0, 0)
        chunk = struct.pack(">I", len(ihdr)) + b"IHDR" + ihdr + struct.pack(">I", zlib.crc32(b"IHDR" + ihdr) & 0xFFFFFFFF)
        target = tmp_path / "bomb.png"
        target.write_bytes(b"\x89PNG\r\n\x1a\n" + chunk)
        with pytest.raises(VisionError) as exc:
            common.read_image(str(target))
        assert _error_code(exc) == "vision_image_too_large"
        assert exc.value.details["width"] == 20000

    def test_preview_writer_requires_home_and_rejects_escapes(self, tmp_path: Path) -> None:
        image = np.zeros((4, 4, 3), dtype=np.uint8)
        with pytest.raises(VisionError) as exc:
            common.write_preview("", image, "diff", 0)
        assert _error_code(exc) == "vision_unavailable"
        assert not list(tmp_path.glob("vision/**"))
        written = common.write_preview(str(tmp_path), image, "diff", 0)
        assert Path(written["path"]).resolve().is_relative_to((tmp_path / "vision" / "previews").resolve())


class TestYoloPostprocess:
    def test_objectness_layout_filters_and_runs_nms(self) -> None:
        # 2 classes: [cx, cy, w, h, obj, p0, p1]
        predictions = np.array(
            [
                [50, 50, 20, 20, 0.9, 0.9, 0.1],
                [51, 50, 20, 20, 0.85, 0.8, 0.2],  # same class, high IoU -> dropped by NMS
                [10, 10, 5, 5, 0.4, 0.1, 0.9],  # class 1, above default conf 0.25
                [10, 10, 5, 5, 0.1, 0.9, 0.1],  # below conf
                [0, 0, 1, 1, 0.0, 0.0, 0.0],
                [0, 0, 1, 1, 0.0, 0.0, 0.0],
                [0, 0, 1, 1, 0.0, 0.0, 0.0],
                [0, 0, 1, 1, 0.0, 0.0, 0.0],
            ],
            dtype=np.float32,
        )[None, :, :]
        detections, counters = detect.decode_yolo(
            predictions, conf=0.25, iou=0.45, max_detections=100, labels=("cat", "dog")
        )
        assert [item["label"] for item in detections] == ["cat", "dog"]
        assert detections[0]["class"] == 0
        assert detections[0]["confidence"] == pytest.approx(0.81, abs=1e-4)
        assert detections[1]["label"] == "dog"
        assert counters["raw_candidates"] == 8
        assert counters["above_conf"] == 3
        assert counters["after_nms"] == 2

    def test_no_objectness_layout_is_transposed(self) -> None:
        # 1 class, (1, 4+1, 10 anchors) -> decode must transpose to (anchors, attributes)
        predictions = np.zeros((1, 5, 10), dtype=np.float32)
        predictions[0, :, 0] = [30, 30, 10, 10, 0.7]
        predictions[0, :, 1] = [90, 90, 4, 4, 0.1]
        detections, _ = detect.decode_yolo(predictions, conf=0.25, iou=0.45, max_detections=100, labels=None)
        assert len(detections) == 1
        assert detections[0]["class"] == 0
        assert detections[0]["label"] is None
        assert detections[0]["xyxy"] == pytest.approx([25.0, 25.0, 35.0, 35.0])

    def test_max_detections_caps_the_result(self) -> None:
        rows = [[10 + index * 20, 50, 4, 4, 0.9, 0.9] for index in range(8)]
        predictions = np.array(rows, dtype=np.float32)[None, :, :]
        detections, _ = detect.decode_yolo(predictions, conf=0.25, iou=0.45, max_detections=3, labels=None)
        assert len(detections) == 3

    def test_yolov5_85_columns_are_decoded_without_a_fixed_25200_row_assumption(self) -> None:
        # (1, 3, 85): 3 anchors x (4 box + objectness + 80 classes), not the preset 25200.
        predictions = np.zeros((1, 3, 85), dtype=np.float32)
        predictions[0, 0, :5] = [10, 10, 4, 4, 0.9]
        predictions[0, 0, 5 + 7] = 0.8
        detections, counters = detect.decode_yolo(predictions, conf=0.25, iou=0.45, max_detections=100, labels=None)
        assert counters["raw_candidates"] == 3
        assert len(detections) == 1
        assert detections[0]["class"] == 7
        assert detections[0]["confidence"] == pytest.approx(0.72, abs=1e-4)

    def test_unexpected_output_shape_is_a_model_mismatch(self) -> None:
        with pytest.raises(VisionError) as exc:
            detect.decode_yolo(np.zeros((1, 3), dtype=np.float32), conf=0.25, iou=0.45, max_detections=10, labels=None)
        assert _error_code(exc) == "vision_model_unsupported"
        assert exc.value.stage == "config"

    def test_yolov8_shaped_output_with_labels_is_rejected_not_guessed(self) -> None:
        labels = tuple("c" + str(index) for index in range(80))
        # (1, 84, 100): 4 box + 80 class scores, no objectness column.
        predictions = np.zeros((1, 84, 100), dtype=np.float32)
        with pytest.raises(VisionError) as exc:
            detect.decode_yolo(predictions, conf=0.25, iou=0.45, max_detections=10, labels=labels, model="yolo")
        assert _error_code(exc) == "vision_model_unsupported"
        assert exc.value.stage == "config"
        assert "YOLOv8" in exc.value.message

    def test_explicit_coco_layout_is_accepted(self) -> None:
        labels = tuple("c" + str(index) for index in range(80))
        predictions = np.zeros((1, 3, 85), dtype=np.float32)
        predictions[0, 0, :5] = [10, 10, 4, 4, 0.9]
        predictions[0, 0, 5] = 0.8
        detections, _ = detect.decode_yolo(
            predictions, conf=0.25, iou=0.45, max_detections=10, labels=labels, model="coco"
        )
        assert len(detections) == 1
        assert detections[0]["label"] == "c0"

    def test_label_count_mismatch_is_a_config_error_with_counts(self) -> None:
        predictions = np.zeros((1, 3, 9), dtype=np.float32)
        with pytest.raises(VisionError) as exc:
            detect.decode_yolo(
                predictions, conf=0.25, iou=0.45, max_detections=10, labels=("a", "b"), model="yolo"
            )
        assert _error_code(exc) == "vision_model_unconfigured"
        assert exc.value.details["attributes"] == 9
        assert exc.value.details["labels"] == 2
        assert exc.value.details["model"] == "yolo"

    def test_letterbox_keeps_aspect_ratio_and_pads_with_114(self) -> None:
        image = np.full((50, 100, 3), 7, dtype=np.uint8)
        padded, scale, pad = detect.letterbox(image, (64, 64))
        assert padded.shape == (64, 64, 3)
        assert scale == pytest.approx(0.64)
        assert pad == (0.0, 16.0)
        assert int(padded[0, 0, 0]) == detect.PAD_VALUE
        assert int(padded[32, 32, 0]) == 7


class TestTrackStability:
    def test_stable_sequence_has_no_jumps(self) -> None:
        summaries = [{"frame": index, "mean_flow_px": 2.0} for index in range(1, 6)]
        result = track._stability(summaries, [], 640, 480)
        assert result["stable"] is True
        assert result["jump_frames"] == []
        assert result["flicker_px"] is None

    def test_jump_frame_breaks_stability(self) -> None:
        summaries = [
            {"frame": 1, "mean_flow_px": 1.0},
            {"frame": 2, "mean_flow_px": 1.2},
            {"frame": 3, "mean_flow_px": 90.0},
            {"frame": 4, "mean_flow_px": 1.1},
        ]
        result = track._stability(summaries, [], 640, 480)
        assert result["stable"] is False
        assert result["jump_frames"] == [3]
        assert result["jump_threshold_px"] == pytest.approx(max(8.0, 0.1 * float(np.hypot(640, 480))), abs=0.01)

    def test_missing_motion_is_reported_not_zero(self) -> None:
        result = track._stability([], [], 100, 100)
        assert result["stable"] is None
        assert result["mean_motion_px"] is None
        assert "reason" in result

    def test_flicker_is_brightness_not_motion(self) -> None:
        # Perfectly still scene, but frame 2 is 40 gray levels brighter on average.
        luma = [
            {"frame": 1, "mean_abs_diff": 40.0, "p95_abs_diff": 40.0, "median_abs_diff": 40.0, "mad_abs_diff": 0.0},
        ]
        result = track._stability([], luma, 100, 100)
        assert result["flicker_px"] == pytest.approx(40.0)
        assert result["flicker_p95_gray"] == pytest.approx(40.0)
        assert result["motion_std_px"] is None
        assert result["stable"] is False
        assert result["criteria"]["flicker_max_gray"] == track.STABLE_FLICKER_GRAY

    def test_luma_differences_reports_robust_statistics(self) -> None:
        first = np.zeros((4, 4), dtype=np.uint8)
        second = np.zeros((4, 4), dtype=np.uint8)
        second[:2, :] = 200  # eight pixels change by 200, eight stay at 0
        summaries = track._luma_differences([("a", first), ("b", second), ("c", second)])
        assert len(summaries) == 2
        assert summaries[0]["mean_abs_diff"] == pytest.approx(100.0, abs=1e-3)
        assert summaries[0]["p95_abs_diff"] == pytest.approx(200.0, abs=1e-3)
        assert summaries[0]["median_abs_diff"] == pytest.approx(100.0, abs=1e-3)
        assert summaries[0]["mad_abs_diff"] == pytest.approx(100.0, abs=1e-3)
        assert summaries[1]["mean_abs_diff"] == 0.0



def _png(path: Path, image: np.ndarray) -> str:
    assert cv2.imwrite(str(path), image)
    return str(path)


class TestAnalyzeOp:
    def test_analyze_reports_bounded_statistics(self, tmp_path: Path) -> None:
        import analyze

        image = np.full((80, 120, 3), 30, dtype=np.uint8)
        image[20:60, 30:90] = 255
        path = _png(tmp_path / "scene.png", image)
        out = analyze.run({"path": path}, {"home": str(tmp_path)})
        assert out["width"] == 120 and out["height"] == 80 and out["channels"] == 3
        assert out["foreground"]["bbox"] == [30, 20, 90, 60]
        assert out["foreground"]["coverage"] == pytest.approx(0.25, abs=0.01)
        assert len(out["contours"]) == 1
        assert out["sharpness"]["laplacian_variance"] > 0
        assert out["exposure"]["clipped_high_pct"] == pytest.approx(0.25, abs=0.01)
        assert set(out["color"]["mean"]) == {"b", "g", "r"}
        assert "mask" not in out and "pixels" not in out

    def test_truncation_is_reported_only_when_the_total_exceeds_the_limit(self, tmp_path: Path) -> None:
        import analyze

        image = np.zeros((80, 120, 3), dtype=np.uint8)
        cv2.rectangle(image, (60, 40), (90, 70), (255, 255, 255), -1)
        path = _png(tmp_path / "one.png", image)
        exact = analyze.run({"path": path, "maxContours": 1}, {})
        assert len(exact["contours"]) == 1
        assert exact["contours_truncated"] is False
        assert exact["contours_total"] == 1

    def test_contour_limit_reports_the_true_total(self) -> None:
        mask = np.zeros((60, 60), dtype=np.uint8)
        cv2.rectangle(mask, (5, 5), (15, 15), 255, -1)
        cv2.rectangle(mask, (30, 30), (50, 50), 255, -1)
        items, total = common.contours(mask, 2)
        assert (len(items), total) == (2, 2)
        items, total = common.contours(mask, 1)
        assert (len(items), total) == (1, 2)

    def test_analyze_roi_and_missing_file(self, tmp_path: Path) -> None:
        import analyze

        image = np.zeros((60, 60, 3), dtype=np.uint8)
        path = _png(tmp_path / "a.png", image)
        out = analyze.run({"path": path, "roi": {"x": 10, "y": 5, "w": 20, "h": 30}}, {})
        assert out["roi"] == {"x": 10, "y": 5, "w": 20, "h": 30, "full_frame": False}
        with pytest.raises(VisionError) as exc:
            analyze.run({"path": str(tmp_path / "nope.png")}, {})
        assert _error_code(exc) == "vision_input_invalid"


class TestCompareOp:
    def test_compare_aligned_valid_case(self, tmp_path: Path) -> None:
        import compare

        reference = np.zeros((120, 160, 3), dtype=np.uint8)
        cv2.rectangle(reference, (40, 30), (110, 90), (200, 120, 60), -1)
        candidate = np.roll(reference, 2, axis=1)
        ref_path = _png(tmp_path / "ref.png", reference)
        cand_path = _png(tmp_path / "candidate.png", candidate)
        out = compare.run({"ref": ref_path, "candidate": cand_path, "align": "ecc"}, {"home": str(tmp_path)})
        assert out["aligned"] is True
        assert out["align"]["used"] == "ecc"
        assert len(out["align"]["transform_2x3"]) == 2
        assert out["metrics"]["changed_pct"] >= 0
        assert out["metrics"]["iou"] is not None
        assert len(out["previews"]) == 2
        assert "pixels" not in out and "diff_image" not in out and "mask" not in out

    def test_compare_rejects_different_resolutions(self, tmp_path: Path) -> None:
        import compare

        first = _png(tmp_path / "a.png", np.zeros((40, 40, 3), dtype=np.uint8))
        second = _png(tmp_path / "b.png", np.zeros((40, 60, 3), dtype=np.uint8))
        with pytest.raises(VisionError) as exc:
            compare.run({"ref": first, "candidate": second}, {})
        assert _error_code(exc) == "vision_input_invalid"

    def test_diff_preview_scales_in_a_wide_type_without_uint8_wrap(self) -> None:
        import compare

        reference = np.zeros((1, 1, 3), dtype=np.uint8)
        aligned = np.full((1, 1, 3), 200, dtype=np.uint8)
        heat = compare._diff_preview(reference, aligned)
        expected = cv2.applyColorMap(np.array([[255]], dtype=np.uint8), cv2.COLORMAP_INFERNO)
        assert tuple(int(v) for v in heat[0, 0]) == tuple(int(v) for v in expected[0, 0])

    def test_alpha_masks_drive_iou(self) -> None:
        import compare

        image = np.zeros((64, 64, 3), dtype=np.uint8)
        reference_alpha = np.zeros((64, 64), dtype=np.uint8)
        reference_alpha[10:30, 10:30] = 255
        aligned_alpha = np.zeros((64, 64), dtype=np.uint8)
        aligned_alpha[10:30, 20:40] = 255
        metrics = compare._metrics(image, image, image, image, reference_alpha, aligned_alpha)
        # Intersection 200 px, union 600 px.
        assert metrics["iou"] == pytest.approx(1.0 / 3.0, abs=1e-4)

    def test_compare_without_home_keeps_the_result_and_records_a_note(self, tmp_path: Path) -> None:
        import compare

        yy, xx = np.mgrid[0:80, 0:80]
        checker = np.where(((xx // 8 + yy // 8) % 2 == 0)[..., None], 220, 40).astype(np.uint8).repeat(3, axis=2)
        path = _png(tmp_path / "ref.png", checker)
        out = compare.run({"ref": path, "candidate": path, "align": "ecc"}, {})
        assert out["aligned"] is True
        assert out["previews"] == []
        assert any("预览图未写入" in note for note in out["notes"])


class TestTrackOp:
    def test_farneback_sequence_returns_bounded_motion_and_stability(self, tmp_path: Path) -> None:
        import track

        base = np.zeros((80, 100, 3), dtype=np.uint8)
        base[20:60, 30:70] = 200
        frames = [_png(tmp_path / ("f" + str(index) + ".png"), np.roll(base, index * 2, axis=1)) for index in range(3)]
        out = track.run({"frames": frames, "method": "farneback"}, {})
        assert out["method"] == "farneback"
        assert [item["index"] for item in out["frames"]] == [0, 1, 2]
        assert len(out["motion"]) == 2
        assert out["motion"][0]["mean_flow_px"] > 0
        assert out["stability"]["stable"] is True
        assert out["limits"]["frames"] == 120 and out["limits"]["tracks"] == 20
        assert "flow" not in out and "points" not in out["frames"][0]

    def test_reference_mismatch_lists_missing_frames(self, tmp_path: Path) -> None:
        import track

        base = np.zeros((40, 40, 3), dtype=np.uint8)
        base[10:30, 10:30] = 180
        frames = [_png(tmp_path / ("f" + str(index) + ".png"), base) for index in range(3)]
        out = track.run({"frames": frames, "method": "lk", "reference": frames[:2]}, {})
        assert out["reference"]["missing_frames"] == [2]
        assert out["reference"]["matched_frames"] == 2
        assert out["reference"]["rmse"] == pytest.approx(0.0, abs=1e-6)

    def test_illegal_method_and_over_limit_frames_fail_explicitly(self, tmp_path: Path) -> None:
        import track

        frame = _png(tmp_path / "one.png", np.zeros((4, 4, 3), dtype=np.uint8))
        with pytest.raises(VisionError) as exc:
            track.run({"frames": [frame, frame], "method": "raft"}, {})
        assert _error_code(exc) == "vision_input_invalid"
        too_many = [frame] * 121
        with pytest.raises(VisionError) as exc:
            track.run({"frames": too_many}, {})
        assert _error_code(exc) == "vision_input_invalid"

    def test_reference_rmse_does_not_overflow_int16(self, tmp_path: Path) -> None:
        import track

        dark = _png(tmp_path / "dark.png", np.zeros((40, 40, 3), dtype=np.uint8))
        bright = _png(tmp_path / "bright.png", np.full((40, 40, 3), 255, dtype=np.uint8))
        out = track.run({"frames": [dark, bright], "method": "lk", "reference": [bright]}, {})
        assert out["reference"]["matched_frames"] == 1
        assert out["reference"]["rmse"] == pytest.approx(255.0, abs=1e-6)
        assert out["reference"]["max_error"] == pytest.approx(255.0, abs=1e-6)

    def test_total_pixel_budget_rejects_a_long_sequence(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        import track

        mono = _png(tmp_path / "mono.png", np.zeros((10, 10, 3), dtype=np.uint8))
        monkeypatch.setattr(track, "MAX_TOTAL_PIXELS", 100)
        with pytest.raises(VisionError) as exc:
            track.run({"frames": [mono, mono]}, {})
        assert _error_code(exc) == "vision_image_too_large"
        assert exc.value.details["totalPixels"] == 200
        assert exc.value.details["limit"] == 100
        assert exc.value.stage == "input"

    def test_stability_exposes_brightness_flicker_separately(self, tmp_path: Path) -> None:
        import track

        base = np.zeros((40, 40, 3), dtype=np.uint8)
        base[10:30, 10:30] = 200
        frames = [
            _png(tmp_path / "a.png", base),
            _png(tmp_path / "b.png", np.clip(base.astype(np.int16) + 60, 0, 255).astype(np.uint8)),
        ]
        out = track.run({"frames": frames, "method": "lk"}, {})
        stability = out["stability"]
        assert stability["flicker_px"] is not None and stability["flicker_px"] > 0
        assert stability["motion_std_px"] is not None
        assert stability["stable"] is False
        assert out["luma"][0]["mean_abs_diff"] == pytest.approx(stability["flicker_px"], abs=1e-6)


class TestSelftestOp:
    def test_dnn_disabled_is_skipped_not_failed(self) -> None:
        import selftest

        out = selftest.run({"dnn": False}, {})
        assert out["ok"] is True
        names = [item["name"] for item in out["checks"]]
        assert names == ["import", "synthetic_ops", "contours", "ecc", "flow", "dnn"]
        dnn = next(item for item in out["checks"] if item["name"] == "dnn")
        assert dnn["status"] == "skipped"
        assert out["summary"]["failed"] == 0
        assert out["notes"]

    def test_unavailable_backend_fails_the_dnn_check(self, tmp_path: Path) -> None:
        import selftest

        class Entry:
            id = "yolov5n"

        class Catalog:
            def get(self, model_id: str) -> Entry:
                assert model_id == "yolov5n"
                return Entry()

        class BadInference:
            name = "onnxruntime"

            def available(self) -> bool:
                return False

            def describe(self):
                return {"reason": "onnxruntime 未安装", "fix": "在 vision.venv 安装 onnxruntime"}

        out = selftest.run(
            {"dnn": True, "model": "yolov5n", "hasModels": True},
            {"catalog": Catalog(), "inference": BadInference(), "models_dir": str(tmp_path)},
        )
        assert out["ok"] is False
        dnn = next(item for item in out["checks"] if item["name"] == "dnn")
        assert dnn["status"] == "fail"
        assert dnn["code"] == "dnn_backend_unavailable"

    def test_ensure_error_code_survives_the_selftest(self, tmp_path: Path) -> None:
        import selftest
        from protocol import VisionError

        class Entry:
            id = "yolov5n"

        class Catalog:
            def get(self, model_id: str) -> Entry:
                return Entry()

        class BadInference:
            name = "onnxruntime"

            def ensure(self):
                raise VisionError("dnn_device_unavailable", "config", "GPU 不可用", details={"device": "gpu"})

        out = selftest.run(
            {"dnn": True, "model": "yolov5n", "hasModels": True},
            {"catalog": Catalog(), "inference": BadInference(), "models_dir": str(tmp_path)},
        )
        dnn = next(item for item in out["checks"] if item["name"] == "dnn")
        assert dnn["status"] == "fail"
        assert dnn["code"] == "dnn_device_unavailable"
        assert dnn["details"]["device"] == "gpu"


class TestDetectOp:
    def test_detect_uses_catalog_and_inference(self, tmp_path: Path) -> None:
        import detect

        class Entry:
            id = "yolo"
            path = str(tmp_path / "yolo.onnx")
            width = 64
            height = 64
            mean = (0.0, 0.0, 0.0)
            scale = 1.0 / 255.0
            rgb = True
            labels = ("person", "car")

        class Catalog:
            def get(self, model_id: str) -> Entry:
                assert model_id == "yolo"
                return Entry()

        class Inference:
            name = "opencv"
            device = "cpu"

            def available(self) -> bool:
                return True

            def providers(self):
                return []

            def describe(self):
                return {"backend": "opencv", "device": "cpu", "deviceUsed": "cpu", "provider": "cpu"}

            def load(self, model_path: str, input_size=None):
                assert input_size == (64, 64)
                return "handle"

            def infer(self, handle, blob):
                assert handle == "handle"
                assert blob.dtype == np.float32 and blob.shape == (1, 3, 64, 64)
                row = np.array([[32, 32, 10, 10, 0.9, 0.9, 0.1]], dtype=np.float32)
                return [row[None, :, :]]

        image = np.zeros((80, 100, 3), dtype=np.uint8)
        image[20:50, 30:70] = 255
        path = _png(tmp_path / "detect.png", image)
        out = detect.run(
            {"path": path, "model": "yolo", "conf": 0.3},
            {
                "home": str(tmp_path),
                "config": {"model": "yolo", "backend": "opencv", "device": "cpu"},
                "catalog": Catalog(),
                "inference": Inference(),
            },
        )
        assert out["backend"] == "opencv"
        assert out["device"] == {"requested": "cpu", "used": "cpu"}
        assert out["count"] == 1
        assert out["detections"][0]["label"] == "person"
        assert out["model"]["input_size"] == [64, 64]
        # letterboxed 32,32 centre maps back to the original image centre (50, 40)
        x0, y0, x1, y1 = out["detections"][0]["xyxy"]
        assert (x0 + x1) / 2 == pytest.approx(50.0, abs=1.5)
        assert (y0 + y1) / 2 == pytest.approx(40.0, abs=1.5)

    def test_backend_config_error_is_not_downgraded_to_an_input_error(self, tmp_path: Path) -> None:
        import detect

        class Entry:
            id = "yolo"
            path = str(tmp_path / "yolo.onnx")
            width = 64
            height = 64
            mean = (0.0, 0.0, 0.0)
            scale = 1.0 / 255.0
            rgb = True
            labels = None

        class Catalog:
            def get(self, model_id: str) -> Entry:
                return Entry()

        class Unavailable:
            name = "onnxruntime"
            device = "gpu"

            def available(self) -> bool:
                return False

            def ensure(self):
                raise VisionError("dnn_device_unavailable", "config", "GPU 不可用", details={"device": "gpu"})

            def describe(self):
                return {"reason": "dnn_device_unavailable"}

        image = np.zeros((40, 40, 3), dtype=np.uint8)
        path = _png(tmp_path / "in.png", image)
        with pytest.raises(VisionError) as exc:
            detect.run(
                {"path": path, "model": "yolo"},
                {"config": {"model": "yolo"}, "catalog": Catalog(), "inference": Unavailable()},
            )
        assert exc.value.code == "dnn_device_unavailable"
        assert exc.value.stage == "config"
        assert exc.value.details["device"] == "gpu"


class TestProbeValidation:
    def test_model_entry_failure_degrades_to_warn_when_dnn_is_disabled(self, tmp_path: Path) -> None:
        import probe

        (tmp_path / "models.yml").write_text("%YAML:1.0\n---\nbad:\n  width: 640\n", encoding="utf-8")
        checks: list[dict[str, object]] = []
        probe.validate_models_yml(cv2, str(tmp_path), "bad", checks, dnn_enabled=False)
        entry = next(item for item in checks if item["id"] == "vision_model")
        assert entry["status"] == "warn"
        assert "features.dnn=false" in str(entry["detail"])

    def test_model_entry_failure_stays_fail_when_dnn_is_enabled(self, tmp_path: Path) -> None:
        import probe

        (tmp_path / "models.yml").write_text("%YAML:1.0\n---\nbad:\n  width: 640\n", encoding="utf-8")
        checks: list[dict[str, object]] = []
        probe.validate_models_yml(cv2, str(tmp_path), "bad", checks, dnn_enabled=True)
        entry = next(item for item in checks if item["id"] == "vision_model")
        assert entry["status"] == "fail"

    def test_probe_emits_json_even_when_the_probe_crashes(self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
        import probe

        def boom(*_args):
            raise RuntimeError("kaboom")

        monkeypatch.setattr(probe, "probe", boom)
        monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps({"candidate": {"features": {"dnn": False}}})))
        assert probe.main() == 0
        payload = json.loads(capsys.readouterr().out)
        assert payload["ok"] is False
        assert payload["checks"][0]["id"] == "probe"
        assert "kaboom" in payload["checks"][0]["detail"]
