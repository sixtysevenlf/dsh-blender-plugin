# -*- coding: utf-8 -*-
"""OpenCV models.yml reader for the private vision worker.

The host never parses YAML. This module reads vision/models/models.yml with
cv2.FileStorage (OpenCV 5 syntax), validates the documented subset and resolves
paths inside the models directory. Every failure is a VisionError with a stable
code so the host dispatcher can surface it unchanged.

The reader is split in two layers so it can be tested without OpenCV:
load_catalog opens the file through storage_factory (default: cv2.FileStorage)
and _parse_root turns the node tree into a ModelCatalog.
"""
from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from typing import Any, Callable

from protocol import VisionError

MODEL_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")
SUPPORTED_POSTPROCESSING = frozenset({"yolov5"})
DEFAULT_MEAN = (0.0, 0.0, 0.0)
DEFAULT_SCALE = 1.0
DEFAULT_STD = 1.0
MODELS_YML = "models.yml"
#: Upper bound for a DNN input edge; detect() allocates width*height*3*4 bytes.
MAX_INPUT_DIMENSION = 8192
#: DNN input edges must be multiples of this (letterbox/network stride).
INPUT_DIMENSION_STEP = 32
BOOL_TRUE = frozenset({"true", "yes", "on"})
BOOL_FALSE = frozenset({"false", "no", "off"})
_ERROR_LINE_RE = re.compile(r"\bline\s*=\s*(\d+)", re.IGNORECASE)

StorageFactory = Callable[[str], Any]


@dataclass(frozen=True)
class ModelEntry:
    """One validated models.yml entry."""

    id: str
    model: str
    path: str
    width: int
    height: int
    postprocessing: str
    mean: tuple[float, float, float] = DEFAULT_MEAN
    scale: float = DEFAULT_SCALE
    rgb: bool = False
    labels: tuple[str, ...] | None = None
    labels_path: str | None = None
    background_label_id: int | None = None

    @property
    def input_size(self) -> tuple[int, int]:
        return (self.width, self.height)


@dataclass(frozen=True)
class ModelIssue:
    """A models.yml entry that failed validation; surfaced by vision_status."""

    id: str
    code: str
    message: str
    fix: str | None = None
    details: dict[str, Any] = field(default_factory=dict)


@dataclass
class ModelCatalog:
    """Parsed models directory; one issue per invalid entry, never a hard stop."""

    models_dir: str
    models_yml: str
    entries: dict[str, ModelEntry] = field(default_factory=dict)
    issues: dict[str, ModelIssue] = field(default_factory=dict)
    onnx_files: tuple[str, ...] = ()

    def ids(self) -> list[str]:
        return sorted(self.entries)

    def has_onnx(self, model_id: str) -> bool:
        for path in self.onnx_files:
            if os.path.basename(path).casefold() == (model_id + ".onnx").casefold():
                return True
        return False

    def get(self, model_id: str) -> ModelEntry:
        """Resolve a requested model id or raise the documented error code."""
        if not validate_model_id(model_id):
            raise VisionError(
                "vision_model_unsupported",
                "input",
                "model id is not valid: " + repr(model_id),
                fix="Use a models.yml top-level id: letters/digits then [A-Za-z0-9._-]{0,63}; no paths or URLs.",
                retryable=False,
                details={"model": model_id, "availableModels": self.ids()},
            )
        entry = self.entries.get(model_id)
        if entry is not None:
            return entry
        issue = self.issues.get(model_id)
        if issue is not None:
            raise VisionError(
                issue.code,
                "config",
                issue.message,
                fix=issue.fix,
                retryable=False,
                details=dict(issue.details, model=model_id, availableModels=self.ids(), modelsYml=self.models_yml),
            )
        if self.has_onnx(model_id):
            raise VisionError(
                "vision_model_unconfigured",
                "config",
                "models.yml has no entry for " + model_id + " although " + model_id + ".onnx exists",
                fix=_example_fix(model_id),
                retryable=False,
                details={"model": model_id, "modelsYml": self.models_yml, "availableModels": self.ids()},
            )
        raise VisionError(
            "vision_model_missing",
            "config",
            "model id " + model_id + " is not configured and no matching .onnx exists",
            fix="Add a models.yml entry and place the .onnx under vision/models/, or pick an available id.",
            retryable=False,
            details={"model": model_id, "availableModels": self.ids(), "onnxCount": len(self.onnx_files)},
        )


def validate_model_id(model_id: object) -> bool:
    return isinstance(model_id, str) and MODEL_ID_RE.fullmatch(model_id) is not None


def load_catalog(models_dir: str, *, storage_factory: StorageFactory | None = None) -> ModelCatalog:
    """Read and validate models.yml under models_dir.

    File-level problems (missing file, duplicate keys, unparsable YAML) raise
    vision_model_unconfigured. Entry-level problems are recorded in
    ModelCatalog.issues and raised only when that model is requested.
    """
    root_dir = os.path.abspath(models_dir)
    yml_path = os.path.join(root_dir, MODELS_YML)
    onnx_files = _scan_onnx(root_dir)
    if not os.path.isfile(yml_path):
        raise VisionError(
            "vision_model_unconfigured",
            "config",
            "vision/models/models.yml does not exist",
            fix=_example_fix("yolov5n"),
            retryable=False,
            details={"modelsDir": root_dir, "modelsYml": yml_path, "onnxCount": len(onnx_files)},
        )
    try:
        with open(yml_path, "r", encoding="utf-8-sig") as handle:
            text = handle.read()
    except OSError as exc:
        raise VisionError(
            "vision_model_unconfigured",
            "config",
            "cannot read models.yml: " + str(exc),
            retryable=False,
            details={"modelsYml": yml_path},
        ) from exc
    duplicate = _duplicate_top_level_key(text)
    if duplicate is not None:
        key, line = duplicate
        raise VisionError(
            "vision_model_unconfigured",
            "config",
            "duplicate top-level key " + repr(key) + " in models.yml (line " + str(line) + ")",
            fix="Remove the duplicate entry; every model id must appear exactly once.",
            retryable=False,
            details={"modelsYml": yml_path, "line": line, "key": key},
        )
    storage = _open_storage(yml_path, storage_factory)
    try:
        node = storage.root()
    except VisionError:
        raise
    except BaseException as exc:
        raise _storage_error(yml_path, exc) from exc
    if node is None or node.isNone() or not node.isMap():
        raise VisionError(
            "vision_model_unconfigured",
            "config",
            "models.yml root must be a map of model ids",
            fix=_example_fix("yolov5n"),
            retryable=False,
            details={"modelsYml": yml_path},
        )
    try:
        return _parse_root(node, root_dir, yml_path, onnx_files)
    except VisionError:
        raise
    except BaseException as exc:
        raise _storage_error(yml_path, exc) from exc


def _storage_error(yml_path: str, exc: BaseException) -> VisionError:
    """Map a FileStorage/FileNode failure (SystemError, cv2.error, ...) to config."""
    message = str(exc).splitlines()[0][:300] if str(exc) else exc.__class__.__name__
    details: dict[str, Any] = {"modelsYml": yml_path, "exceptionType": exc.__class__.__name__}
    line = _error_line(str(exc))
    if line is not None:
        details["line"] = line
    return VisionError(
        "vision_model_unconfigured",
        "config",
        "cannot parse models.yml: " + message,
        fix="Fix the YAML syntax (a missing %YAML:1.0 header also fails) or copy the documented example.\n" + _example_fix("yolov5n"),
        retryable=False,
        details=details,
    )


def _error_line(message: str) -> int | None:
    match = _ERROR_LINE_RE.search(message or "")
    if match is None:
        return None
    try:
        return int(match.group(1))
    except ValueError:  # pragma: no cover - regex only captures digits
        return None


def _open_storage(yml_path: str, storage_factory: StorageFactory | None) -> Any:
    """Open models.yml and return the FileStorage object.

    The caller must keep the storage object alive while using its nodes: an
    OpenCV FileNode is a view into the FileStorage and crashes when the owning
    FileStorage has been garbage collected.
    """
    if storage_factory is None:
        try:
            import cv2  # type: ignore[import-not-found]
        except ImportError as exc:
            raise VisionError(
                "vision_worker_failed",
                "vision",
                "OpenCV (cv2) is not available in the worker interpreter",
                fix="Reinstall the vision environment; models.yml is read with cv2.FileStorage.",
                retryable=False,
                details={"exceptionType": "ImportError"},
            ) from exc
        try:
            storage = cv2.FileStorage(yml_path, cv2.FILE_STORAGE_READ)
        except BaseException as exc:
            raise _storage_error(yml_path, exc) from exc
    else:
        try:
            storage = storage_factory(yml_path)
        except BaseException as exc:
            raise _storage_error(yml_path, exc) from exc
    try:
        opened = bool(storage.isOpened())
    except BaseException as exc:  # pragma: no cover - defensive against odd bindings
        raise _storage_error(yml_path, exc) from exc
    if not opened:
        raise VisionError(
            "vision_model_unconfigured",
            "config",
            "models.yml cannot be parsed by OpenCV FileStorage (OpenCV 5 YAML syntax expected)",
            fix="Validate the file with cv2.FileStorage or copy the documented example.",
            retryable=False,
            details={"modelsYml": yml_path},
        )
    return storage


def _parse_root(root: Any, models_dir: str, yml_path: str, onnx_files: tuple[str, ...]) -> ModelCatalog:
    catalog = ModelCatalog(models_dir=models_dir, models_yml=yml_path, onnx_files=onnx_files)
    for key in root.keys():
        model_id = str(key)
        if not validate_model_id(model_id):
            catalog.issues[model_id] = ModelIssue(
                id=model_id,
                code="vision_model_unsupported",
                message="models.yml key is not a valid model id: " + repr(model_id),
                fix="Use letters/digits then [A-Za-z0-9._-]{0,63}; paths and URLs are rejected.",
                details={"modelsYml": yml_path},
            )
            continue
        node = root.getNode(model_id)
        try:
            catalog.entries[model_id] = _parse_entry(model_id, node, models_dir, yml_path)
        except VisionError as exc:
            catalog.issues[model_id] = ModelIssue(
                id=model_id,
                code=exc.code,
                message=exc.message,
                fix=exc.fix,
                details=exc.details,
            )
        except BaseException as exc:  # a broken FileNode view must not crash the catalog
            wrapped = _storage_error(yml_path, exc)
            catalog.issues[model_id] = ModelIssue(
                id=model_id,
                code=wrapped.code,
                message=wrapped.message,
                fix=wrapped.fix,
                details=dict(wrapped.details, model=model_id),
            )
    return catalog


def _parse_entry(model_id: str, node: Any, models_dir: str, yml_path: str) -> ModelEntry:
    if node.isNone() or not node.isMap():
        raise _unconfigured(model_id, "entry must be a map", yml_path)
    model_raw = _require_string(node, "model", model_id, yml_path)
    model_path = _resolve_child(models_dir, model_raw, "model", model_id, yml_path)
    width = _require_int(node, "width", model_id, yml_path)
    height = _require_int(node, "height", model_id, yml_path)
    for field_name, value in (("width", width), ("height", height)):
        if value <= 0:
            raise _unconfigured(model_id, field_name + " must be a positive integer", yml_path, field_name=field_name)
        if value > MAX_INPUT_DIMENSION:
            raise _unconfigured(
                model_id,
                field_name + " must be <= " + str(MAX_INPUT_DIMENSION) + " (got " + str(value) + ")",
                yml_path,
                field_name=field_name,
                extra={"value": value, "limit": MAX_INPUT_DIMENSION},
            )
        if value % INPUT_DIMENSION_STEP != 0:
            raise _unconfigured(
                model_id,
                field_name + " must be a multiple of " + str(INPUT_DIMENSION_STEP) + " (got " + str(value) + ")",
                yml_path,
                field_name=field_name,
                extra={"value": value, "step": INPUT_DIMENSION_STEP},
            )
    postprocessing = _require_string(node, "postprocessing", model_id, yml_path)
    if postprocessing not in SUPPORTED_POSTPROCESSING:
        raise VisionError(
            "vision_model_unsupported",
            "config",
            "unsupported postprocessing " + repr(postprocessing) + " for model " + model_id,
            fix="Only postprocessing: yolov5 is supported in this version.",
            retryable=False,
            details={"model": model_id, "field": "postprocessing", "modelsYml": yml_path},
        )
    mean = _read_mean(node, model_id, yml_path)
    scale = _read_number(node, "scale", DEFAULT_SCALE, model_id, yml_path)
    if scale <= 0 or not _finite(scale):
        raise _unconfigured(model_id, "scale must be a positive finite number", yml_path, field_name="scale")
    rgb = _read_bool(node, "rgb", False, model_id, yml_path)
    std_node = node.getNode("std")
    if not std_node.isNone():
        std = _read_number(node, "std", DEFAULT_STD, model_id, yml_path)
        if abs(std - DEFAULT_STD) > 1e-9:
            raise VisionError(
                "vision_model_unsupported",
                "config",
                "non-default std is not supported for model " + model_id,
                fix="Remove std from models.yml; this version only supports the documented OpenCV DNN defaults.",
                retryable=False,
                details={"model": model_id, "field": "std", "modelsYml": yml_path},
            )
    labels_raw = node.getNode("labels")
    labels: tuple[str, ...] | None = None
    labels_path: str | None = None
    if not labels_raw.isNone():
        labels_value = _require_string(node, "labels", model_id, yml_path)
        labels_path = _resolve_child(models_dir, labels_value, "labels", model_id, yml_path)
        labels = _read_labels(labels_path, model_id, yml_path)
    background_label_id: int | None = None
    background_node = node.getNode("background_label_id")
    if not background_node.isNone():
        if not background_node.isInt():
            raise _unconfigured(model_id, "background_label_id must be an integer", yml_path, field_name="background_label_id")
        background_label_id = int(background_node.real())
    if not os.path.isfile(model_path):
        raise _unconfigured(
            model_id,
            "model file does not exist: " + model_raw,
            yml_path,
            field_name="model",
            extra={"modelPath": model_path},
        )
    return ModelEntry(
        id=model_id,
        model=model_raw,
        path=model_path,
        width=width,
        height=height,
        postprocessing=postprocessing,
        mean=mean,
        scale=scale,
        rgb=rgb,
        labels=labels,
        labels_path=labels_path,
        background_label_id=background_label_id,
    )


def _require_string(node: Any, field_name: str, model_id: str, yml_path: str) -> str:
    child = node.getNode(field_name)
    if child.isNone() or not child.isString():
        raise _unconfigured(model_id, "missing or non-string field " + field_name, yml_path, field_name=field_name)
    value = child.string()
    if not value:
        raise _unconfigured(model_id, "empty field " + field_name, yml_path, field_name=field_name)
    return value


def _require_int(node: Any, field_name: str, model_id: str, yml_path: str) -> int:
    child = node.getNode(field_name)
    if child.isNone() or not child.isInt():
        raise _unconfigured(model_id, "missing or non-integer field " + field_name, yml_path, field_name=field_name)
    return int(child.real())


def _read_number(node: Any, field_name: str, default: float, model_id: str, yml_path: str) -> float:
    child = node.getNode(field_name)
    if child.isNone():
        return default
    if not (child.isInt() or child.isReal()):
        raise _unconfigured(model_id, "field " + field_name + " must be a number", yml_path, field_name=field_name)
    return float(child.real())


def _read_bool(node: Any, field_name: str, default: bool, model_id: str, yml_path: str) -> bool:
    """Accept int/real 0-1 and the string forms OpenCV yields for YAML `true`.

    cv2.FileStorage parses `rgb: true` as the *string* "true" (isInt() is false),
    while the host writes exactly that, so string booleans are part of the contract.
    """
    child = node.getNode(field_name)
    if child is None or child.isNone():
        return default
    if child.isInt() or child.isReal():
        value = float(child.real())
        if value == 0.0:
            return False
        if value == 1.0:
            return True
        raise _unconfigured(
            model_id,
            "field " + field_name + " must be 0/1 or true/false (got " + repr(value) + ")",
            yml_path,
            field_name=field_name,
            extra={"value": value},
        )
    if child.isString():
        text = str(child.string()).strip().casefold()
        if text in BOOL_TRUE:
            return True
        if text in BOOL_FALSE:
            return False
        raise _unconfigured(
            model_id,
            "field " + field_name + " must be 0/1 or true/false/yes/no/on/off (got " + repr(child.string()) + ")",
            yml_path,
            field_name=field_name,
            extra={"value": str(child.string())},
        )
    raise _unconfigured(
        model_id,
        "field " + field_name + " must be 0/1 or true/false",
        yml_path,
        field_name=field_name,
    )


def _read_mean(node: Any, model_id: str, yml_path: str) -> tuple[float, float, float]:
    child = node.getNode("mean")
    if child.isNone():
        return DEFAULT_MEAN
    if child.isInt() or child.isReal():
        value = float(child.real())
        return (value, value, value)
    if child.isSeq() and child.size() == 3:
        values = tuple(float(child.at(index).real()) for index in range(3))
        return (values[0], values[1], values[2])
    raise _unconfigured(model_id, "mean must be a number or a 3-element BGR sequence", yml_path, field_name="mean")


def _read_labels(path: str, model_id: str, yml_path: str) -> tuple[str, ...]:
    try:
        with open(path, "r", encoding="utf-8-sig") as handle:
            text = handle.read()
    except OSError as exc:
        raise _unconfigured(
            model_id,
            "labels file cannot be read: " + str(exc),
            yml_path,
            field_name="labels",
            extra={"labelsPath": path},
        ) from exc
    if text.endswith("\n"):
        text = text[:-1]
    lines = text.split("\n") if text else []
    for index, line in enumerate(lines, start=1):
        if line.endswith("\r"):
            line = line[:-1]
            lines[index - 1] = line
        if line == "":
            raise _unconfigured(
                model_id,
                "labels file has an empty line at line " + str(index) + " (class ids are line numbers)",
                yml_path,
                field_name="labels",
                extra={"labelsPath": path, "line": index},
            )
    if not lines:
        raise _unconfigured(model_id, "labels file is empty", yml_path, field_name="labels", extra={"labelsPath": path})
    return tuple(lines)


def _resolve_child(models_dir: str, raw: str, field_name: str, model_id: str, yml_path: str) -> str:
    if not isinstance(raw, str) or not raw.strip():
        raise _unconfigured(model_id, field_name + " must be a non-empty relative path", yml_path, field_name=field_name)
    if "://" in raw or raw.lower().startswith(("http:", "https:")):
        raise _unconfigured(model_id, field_name + " must not be a URL", yml_path, field_name=field_name, extra={"value": raw})
    normalized = raw.replace("\\", "/")
    drive, _tail = os.path.splitdrive(normalized)
    if drive or normalized.startswith("/"):
        raise _unconfigured(
            model_id,
            field_name + " must be relative to the models directory",
            yml_path,
            field_name=field_name,
            extra={"value": raw},
        )
    segments = [segment for segment in normalized.split("/") if segment not in ("", ".")]
    if ".." in segments:
        raise _unconfigured(
            model_id,
            field_name + " must not contain '..'",
            yml_path,
            field_name=field_name,
            extra={"value": raw},
        )
    candidate = os.path.realpath(os.path.join(models_dir, *segments))
    base = os.path.realpath(models_dir)
    if candidate != base and not candidate.startswith(base + os.sep):
        raise _unconfigured(
            model_id,
            field_name + " escapes the models directory",
            yml_path,
            field_name=field_name,
            extra={"value": raw, "resolved": candidate},
        )
    return candidate


def _scan_onnx(models_dir: str) -> tuple[str, ...]:
    if not os.path.isdir(models_dir):
        return ()
    found: list[str] = []
    for current, dirnames, filenames in os.walk(models_dir, followlinks=False):
        dirnames.sort()
        for name in sorted(filenames):
            if name.casefold().endswith(".onnx"):
                found.append(os.path.abspath(os.path.join(current, name)))
    return tuple(found)


def _duplicate_top_level_key(text: str) -> tuple[str, int] | None:
    seen: dict[str, int] = {}
    for index, line in enumerate(text.splitlines(), start=1):
        if not line or line[0].isspace():
            continue
        stripped = line.split("#", 1)[0].rstrip()
        if not stripped or stripped.startswith(("%", "---")):
            continue
        if ":" not in stripped:
            continue
        key = stripped.split(":", 1)[0].strip().strip("'\"")
        if not key:
            continue
        if key in seen:
            return (key, index)
        seen[key] = index
    return None


def _unconfigured(
    model_id: str,
    message: str,
    yml_path: str,
    field_name: str | None = None,
    extra: dict[str, Any] | None = None,
) -> VisionError:
    details: dict[str, Any] = {"model": model_id, "modelsYml": yml_path}
    if field_name is not None:
        details["field"] = field_name
    if extra:
        details.update(extra)
    return VisionError(
        "vision_model_unconfigured",
        "config",
        "model " + model_id + ": " + message,
        fix=_example_fix(model_id),
        retryable=False,
        details=details,
    )


def _example_fix(model_id: str) -> str:
    lines = [
        "Add this entry to vision/models/models.yml (keep the %YAML:1.0 header):",
        "%YAML:1.0",
        "---",
        model_id + ":",
        "  model: " + model_id + ".onnx",
        "  width: 640",
        "  height: 640",
        "  postprocessing: yolov5",
        "  mean: 0.0",
        "  scale: 0.00392",
        "  rgb: true",
        "  # rgb accepts true/false, 0/1, yes/no and on/off (case-insensitive).",
    ]
    return "\n".join(lines)


def _finite(value: float) -> bool:
    return value == value and value not in (float("inf"), float("-inf"))
