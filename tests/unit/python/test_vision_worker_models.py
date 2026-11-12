# -*- coding: utf-8 -*-
"""Unit tests for runtime/vision/worker/models.py.

Most contracts use a fake FileStorage/FileNode tree injected through
load_catalog(storage_factory=...); the file-level contracts additionally use the
real cv2.FileStorage, which requirements-dev.txt installs so CI runs them.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

WORKER = Path(__file__).resolve().parents[3] / "runtime" / "vision" / "worker"
if str(WORKER) not in sys.path:
    sys.path.insert(0, str(WORKER))

import models  # noqa: E402
from protocol import VisionError  # noqa: E402


class FakeNode:
    def __init__(self, value):
        self._value = value

    def isNone(self):
        return self._value is None

    def isMap(self):
        return isinstance(self._value, dict)

    def keys(self):
        return list(self._value.keys()) if isinstance(self._value, dict) else []

    def getNode(self, key):
        if isinstance(self._value, dict):
            return FakeNode(self._value.get(key))
        return FakeNode(None)

    def isString(self):
        return isinstance(self._value, str)

    def string(self):
        return self._value if isinstance(self._value, str) else ""

    def isInt(self):
        return isinstance(self._value, int) and not isinstance(self._value, bool)

    def isReal(self):
        return isinstance(self._value, float)

    def real(self):
        return float(self._value) if isinstance(self._value, (int, float)) else 0.0

    def isSeq(self):
        return isinstance(self._value, list)

    def size(self):
        return len(self._value) if isinstance(self._value, list) else 0

    def at(self, index):
        return FakeNode(self._value[index])


class FakeStorage:
    def __init__(self, tree, opened=True):
        self._tree = tree
        self._opened = opened
        self._root = FakeNode(tree)

    def isOpened(self):
        return self._opened

    def root(self):
        return self._root


def make_catalog(tmp_path, tree, *, text="placeholder", opened=True):
    (tmp_path / "models.yml").write_text(text, encoding="utf-8")

    def factory(_path):
        return FakeStorage(tree, opened=opened)

    return models.load_catalog(str(tmp_path), storage_factory=factory)


def full_tree(model_id="yolov5n", model="yolov5n.onnx", **overrides):
    entry = {
        "model": model,
        "width": 640,
        "height": 640,
        "postprocessing": "yolov5",
        "mean": 0.0,
        "scale": 0.00392,
        "rgb": 1,
        "labels": "coco80.txt",
    }
    entry.update(overrides)
    return {model_id: entry}


def write_model_files(tmp_path, onnx=("yolov5n.onnx",), labels=("coco80.txt",)):
    for relative in onnx:
        path = tmp_path / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"fake-onnx")
    for relative in labels:
        (tmp_path / relative).write_text("person\ncar\nbicycle\n", encoding="utf-8")


def test_valid_entry_is_parsed(tmp_path):
    write_model_files(tmp_path)
    catalog = make_catalog(tmp_path, full_tree())
    entry = catalog.get("yolov5n")
    assert entry.id == "yolov5n"
    assert entry.model == "yolov5n.onnx"
    assert entry.path == str((tmp_path / "yolov5n.onnx").resolve())
    assert (entry.width, entry.height) == (640, 640)
    assert entry.input_size == (640, 640)
    assert entry.mean == (0.0, 0.0, 0.0)
    assert entry.scale == pytest.approx(0.00392)
    assert entry.rgb is True
    assert entry.labels == ("person", "car", "bicycle")
    assert entry.labels_path == str((tmp_path / "coco80.txt").resolve())
    assert catalog.ids() == ["yolov5n"]
    assert catalog.issues == {}


def test_mean_accepts_bgr_triple_and_labels_are_optional(tmp_path):
    write_model_files(tmp_path)
    tree = full_tree(mean=[0.1, 0.2, 0.3])
    del tree["yolov5n"]["labels"]
    catalog = make_catalog(tmp_path, tree)
    entry = catalog.get("yolov5n")
    assert entry.mean == (0.1, 0.2, 0.3)
    assert entry.labels is None


def test_missing_models_yml_raises_unconfigured(tmp_path):
    with pytest.raises(VisionError) as raised:
        models.load_catalog(str(tmp_path))
    assert raised.value.code == "vision_model_unconfigured"
    assert raised.value.stage == "config"


def test_duplicate_top_level_key_reports_line(tmp_path):
    write_model_files(tmp_path)
    text = "yolov5n:\n  model: yolov5n.onnx\nyolov5n:\n  model: other.onnx\n"
    with pytest.raises(VisionError) as raised:
        make_catalog(tmp_path, full_tree(), text=text)
    assert raised.value.code == "vision_model_unconfigured"
    assert raised.value.details["line"] == 3
    assert raised.value.details["key"] == "yolov5n"


def test_unparsable_file_storage_raises_unconfigured(tmp_path):
    (tmp_path / "models.yml").write_text("not yaml", encoding="utf-8")
    with pytest.raises(VisionError) as raised:
        models.load_catalog(
            str(tmp_path),
            storage_factory=lambda _path: FakeStorage({}, opened=False),
        )
    assert raised.value.code == "vision_model_unconfigured"


def test_root_must_be_map(tmp_path):
    (tmp_path / "models.yml").write_text("---\n", encoding="utf-8")
    with pytest.raises(VisionError) as raised:
        models.load_catalog(str(tmp_path), storage_factory=lambda _path: FakeStorage([]))
    assert raised.value.code == "vision_model_unconfigured"


def test_missing_required_field_is_recorded_per_entry(tmp_path):
    write_model_files(tmp_path)
    broken = full_tree()
    del broken["yolov5n"]["width"]
    tree = dict(broken)
    tree["ok"] = {
        "model": "yolov5n.onnx",
        "width": 320,
        "height": 320,
        "postprocessing": "yolov5",
    }
    catalog = make_catalog(tmp_path, tree)
    assert catalog.ids() == ["ok"]
    assert catalog.issues["yolov5n"].code == "vision_model_unconfigured"
    with pytest.raises(VisionError) as raised:
        catalog.get("yolov5n")
    assert raised.value.code == "vision_model_unconfigured"
    assert raised.value.details["field"] == "width"


def test_unknown_postprocessing_is_unsupported(tmp_path):
    write_model_files(tmp_path)
    catalog = make_catalog(tmp_path, full_tree(postprocessing="ssd"))
    with pytest.raises(VisionError) as raised:
        catalog.get("yolov5n")
    assert raised.value.code == "vision_model_unsupported"
    assert raised.value.stage == "config"


def test_non_default_std_is_unsupported(tmp_path):
    write_model_files(tmp_path)
    catalog = make_catalog(tmp_path, full_tree(std=2.0))
    with pytest.raises(VisionError) as raised:
        catalog.get("yolov5n")
    assert raised.value.code == "vision_model_unsupported"
    assert raised.value.details["field"] == "std"


@pytest.mark.parametrize("mean", [[0.0, 0.0, 0.0, 0.0], [0.0, 0.0]])
def test_invalid_mean_is_unconfigured(tmp_path, mean):
    write_model_files(tmp_path)
    catalog = make_catalog(tmp_path, full_tree(mean=mean))
    with pytest.raises(VisionError) as raised:
        catalog.get("yolov5n")
    assert raised.value.code == "vision_model_unconfigured"


@pytest.mark.parametrize(
    "bad_model",
    [
        "../escape.onnx",
        "sub/../../escape.onnx",
        "/absolute.onnx",
        "C:\\windows\\absolute.onnx",
        "https://example.com/model.onnx",
    ],
)
def test_model_path_must_stay_inside_models_dir(tmp_path, bad_model):
    write_model_files(tmp_path)
    catalog = make_catalog(tmp_path, full_tree(model=bad_model))
    with pytest.raises(VisionError) as raised:
        catalog.get("yolov5n")
    assert raised.value.code == "vision_model_unconfigured"
    assert raised.value.details["field"] == "model"


def test_nested_model_path_is_allowed(tmp_path):
    write_model_files(tmp_path, onnx=("sub/my.onnx",))
    catalog = make_catalog(tmp_path, full_tree(model="sub/my.onnx"))
    assert catalog.get("yolov5n").model == "sub/my.onnx"


def test_missing_model_file_is_unconfigured(tmp_path):
    (tmp_path / "coco80.txt").write_text("person\n", encoding="utf-8")
    catalog = make_catalog(tmp_path, full_tree())
    with pytest.raises(VisionError) as raised:
        catalog.get("yolov5n")
    assert raised.value.code == "vision_model_unconfigured"
    assert raised.value.details["field"] == "model"


def test_labels_rules(tmp_path):
    write_model_files(tmp_path, labels=("coco80.txt",))
    (tmp_path / "coco80.txt").write_bytes(b"person\r\ncar\r\n")
    catalog = make_catalog(tmp_path, full_tree())
    assert catalog.get("yolov5n").labels == ("person", "car")

    (tmp_path / "coco80.txt").write_text("person\n\ncar\n", encoding="utf-8")
    catalog = make_catalog(tmp_path, full_tree())
    with pytest.raises(VisionError) as raised:
        catalog.get("yolov5n")
    assert raised.value.code == "vision_model_unconfigured"
    assert raised.value.details["line"] == 2

    (tmp_path / "coco80.txt").write_text("", encoding="utf-8")
    catalog = make_catalog(tmp_path, full_tree())
    with pytest.raises(VisionError) as raised:
        catalog.get("yolov5n")
    assert raised.value.code == "vision_model_unconfigured"


def test_missing_labels_file_is_unconfigured(tmp_path):
    write_model_files(tmp_path, labels=())
    catalog = make_catalog(tmp_path, full_tree())
    with pytest.raises(VisionError) as raised:
        catalog.get("yolov5n")
    assert raised.value.code == "vision_model_unconfigured"
    assert raised.value.details["field"] == "labels"


def test_invalid_model_id_key_is_unsupported(tmp_path):
    tree = {"bad/id": {"model": "bad.onnx", "width": 1, "height": 1, "postprocessing": "yolov5"}}
    catalog = make_catalog(tmp_path, tree)
    assert catalog.issues["bad/id"].code == "vision_model_unsupported"
    with pytest.raises(VisionError) as raised:
        catalog.get("bad/id")
    assert raised.value.code == "vision_model_unsupported"
    assert raised.value.stage == "input"


def test_requested_model_with_onnx_but_no_entry_is_unconfigured(tmp_path):
    write_model_files(tmp_path, onnx=("custom.onnx",), labels=())
    catalog = make_catalog(tmp_path, {})
    with pytest.raises(VisionError) as raised:
        catalog.get("custom")
    assert raised.value.code == "vision_model_unconfigured"


def test_requested_model_without_onnx_is_missing(tmp_path):
    write_model_files(tmp_path, onnx=(), labels=())
    catalog = make_catalog(tmp_path, {})
    with pytest.raises(VisionError) as raised:
        catalog.get("nope")
    assert raised.value.code == "vision_model_missing"
    assert raised.value.details["availableModels"] == []


def test_onnx_scan_is_recursive(tmp_path):
    write_model_files(tmp_path, onnx=("a.onnx", "sub/b.onnx"), labels=())
    catalog = make_catalog(tmp_path, {})
    names = sorted(Path(item).name for item in catalog.onnx_files)
    assert names == ["a.onnx", "b.onnx"]


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (1, True),
        (0, False),
        (1.0, True),
        (0.0, False),
        ("true", True),
        ("TRUE", True),
        ("yes", True),
        ("on", True),
        ("false", False),
        ("False", False),
        ("no", False),
        ("off", False),
    ],
)
def test_rgb_accepts_int_real_and_string_booleans(tmp_path, value, expected):
    write_model_files(tmp_path)
    catalog = make_catalog(tmp_path, full_tree(rgb=value))
    assert catalog.get("yolov5n").rgb is expected


@pytest.mark.parametrize("value", [2, -1, 0.5, "maybe", "", [1]])
def test_rgb_rejects_unbounded_values(tmp_path, value):
    write_model_files(tmp_path)
    catalog = make_catalog(tmp_path, full_tree(rgb=value))
    with pytest.raises(VisionError) as raised:
        catalog.get("yolov5n")
    assert raised.value.code == "vision_model_unconfigured"
    assert raised.value.details["field"] == "rgb"
    # The suggested fix must always be a complete, parseable file header + entry.
    assert "%YAML:1.0" in raised.value.fix
    assert "---" in raised.value.fix


def test_real_file_storage_reads_rgb_true(tmp_path):
    import cv2  # noqa: PLC0415 - the vision test stack installs OpenCV

    assert cv2  # imported for the side effect of failing loudly without OpenCV
    write_model_files(tmp_path)
    (tmp_path / "models.yml").write_text(
        "%YAML:1.0\n---\nyolov5n:\n  model: yolov5n.onnx\n  width: 640\n  height: 640\n  postprocessing: yolov5\n  rgb: true\n",
        encoding="utf-8",
    )
    # No storage_factory: this is the real cv2.FileStorage path used in production.
    catalog = models.load_catalog(str(tmp_path))
    assert catalog.get("yolov5n").rgb is True


def test_real_file_storage_reads_rgb_false(tmp_path):
    import cv2  # noqa: PLC0415 - the vision test stack installs OpenCV

    assert cv2
    write_model_files(tmp_path)
    (tmp_path / "models.yml").write_text(
        "%YAML:1.0\n---\nyolov5n:\n  model: yolov5n.onnx\n  width: 640\n  height: 640\n  postprocessing: yolov5\n  rgb: false\n",
        encoding="utf-8",
    )
    catalog = models.load_catalog(str(tmp_path))
    assert catalog.get("yolov5n").rgb is False


def test_missing_yaml_header_is_a_structured_config_error(tmp_path):
    import cv2  # noqa: PLC0415 - the vision test stack installs OpenCV

    assert cv2
    write_model_files(tmp_path)
    (tmp_path / "models.yml").write_text(
        "yolov5n:\n  model: yolov5n.onnx\n  width: 640\n  height: 640\n  postprocessing: yolov5\n",
        encoding="utf-8",
    )
    with pytest.raises(VisionError) as raised:
        models.load_catalog(str(tmp_path))
    assert raised.value.code == "vision_model_unconfigured"
    assert raised.value.stage == "config"
    assert raised.value.details["modelsYml"].endswith("models.yml")


def test_storage_construction_failure_is_mapped_not_raised_raw(tmp_path):
    (tmp_path / "models.yml").write_text("placeholder", encoding="utf-8")

    def factory(_path):
        raise SystemError("OpenCV(4.10.0) Error: bad header, file = models.yml, line = 1")

    with pytest.raises(VisionError) as raised:
        models.load_catalog(str(tmp_path), storage_factory=factory)
    assert raised.value.code == "vision_model_unconfigured"
    assert raised.value.details["line"] == 1
    assert raised.value.details["exceptionType"] == "SystemError"


@pytest.mark.parametrize(("field", "value"), [("width", 650), ("height", 641), ("width", 16384), ("height", 0)])
def test_input_dimensions_are_capped_and_stride_aligned(tmp_path, field, value):
    write_model_files(tmp_path)
    catalog = make_catalog(tmp_path, full_tree(**{field: value}))
    with pytest.raises(VisionError) as raised:
        catalog.get("yolov5n")
    assert raised.value.code == "vision_model_unconfigured"
    assert raised.value.details["field"] == field


def test_largest_allowed_input_dimension_is_8192(tmp_path):
    write_model_files(tmp_path)
    catalog = make_catalog(tmp_path, full_tree(width=8192, height=8192))
    assert catalog.get("yolov5n").input_size == (8192, 8192)


def test_example_fix_contains_a_parseable_header():
    fix = models._example_fix("yolov5n")
    assert fix.splitlines()[1] == "%YAML:1.0"
    assert fix.splitlines()[2] == "---"
