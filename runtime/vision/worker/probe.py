"""One-shot, read-only DNN availability probe for the DSH host.

The host (src/host/dnn-probe.ts) resolves an interpreter and runs this file as
`python -B probe.py`, writes one JSON request on stdin and reads one JSON
response from stdout. It is NOT the resident vision worker: it must not start
the worker, load an ONNX file, create an inference session, allocate a GPU,
touch the network, install packages, or write anything (runtime files, model
directory, __pycache__).

Request:
  {"candidate": {"features":{"dnn":bool},
                 "vision":{"python":str,"venv":str,"model":str},
                 "dnn":{"backend":"opencv"|"onnxruntime","device":"cpu"|"auto"|"gpu"}},
   "home": str, "modelsDir": str, "workerDir": str}

Response:
  {"ok": bool, "checks":[{"id","label","status":"pass"|"warn"|"fail","detail","fix"}],
   "summary":{"pass":int,"warn":int,"fail":int}, "elapsedMs": int, "candidate": object}

`ok` means there is no fail. Missing models (or DNN disabled) are warnings and
must not block saving; a missing interpreter module, a required import or an
explicit GPU request without a provider are failures.
"""
from __future__ import annotations

import json
import os
import sys
import time
from typing import Iterable, cast

sys.dont_write_bytecode = True

PY_MIN = (3, 11)
PY_MAX = (3, 13)
GPU_PROVIDERS = (
    "CUDAExecutionProvider",
    "DmlExecutionProvider",
    "ROCMExecutionProvider",
    "MIGraphXExecutionProvider",
    "OpenVINOExecutionProvider",
    "TensorrtExecutionProvider",
    "CoreMLExecutionProvider",
    "AzureExecutionProvider",
)
DOC_URL = (
    "https://github.com/sixtysevenlf/dsh-blender-plugin/blob/main/"
    "docs/vision-custom-backend.md"
)


def check(checks, cid, label, status, detail, fix=""):
    checks.append({"id": cid, "label": label, "status": status, "detail": detail, "fix": fix})


def summarize(checks):
    out = {"pass": 0, "warn": 0, "fail": 0}
    for item in checks:
        out[item["status"]] = out.get(item["status"], 0) + 1
    return out


def version_tuple(version_info):
    return (version_info[0], version_info[1])


def import_version(module_name):
    try:
        module = __import__(module_name)
    except BaseException as exc:  # a broken install can raise more than ImportError
        return None, str(exc)
    return str(getattr(module, "__version__", "") or "unknown"), ""


def safe_relative_path(base, rel):
    """Return (absolute_real_path, error). A readable path must stay under base."""
    if not isinstance(rel, str) or not rel.strip():
        return None, "路径为空"
    if os.path.isabs(rel) or "://" in rel:
        return None, "必须是 models 目录内的相对路径，不能是绝对路径或 URL"
    norm = os.path.normpath(rel)
    if norm == ".." or norm.startswith(".." + os.sep) or norm.startswith("../"):
        return None, "路径不能包含 .."
    base_real = os.path.realpath(base)
    full = os.path.realpath(os.path.join(base_real, norm))
    try:
        inside = os.path.commonpath([base_real, full]) == base_real
    except ValueError:
        inside = False
    if not inside:
        return None, "解析后的路径逃出了 models 目录（symlink？）"
    return full, ""


def yaml_top_level_keys(path):
    keys = []
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as handle:
            for line in handle:
                line = line.rstrip("\r\n")
                if not line or line[0] in " \t#%":
                    continue
                if line in ("---", "..."):
                    continue
                if ":" in line:
                    key = line.split(":", 1)[0].strip()
                    if key:
                        keys.append(key)
    except OSError:
        return keys
    return keys


def validate_models_yml(cv2, models_dir, model_id, checks, dnn_enabled=True):
    yml = os.path.join(models_dir, "models.yml")

    def record(status, detail, fix=""):
        """Degrade a model-entry failure to warn when DNN is disabled.

        A broken models.yml must never block saving unrelated settings when
        features.dnn is off; the failure is still reported, just as a warning.
        """
        if status == "fail" and not dnn_enabled:
            return "warn", detail + "（features.dnn=false：仅警告，不阻塞保存）", fix
        return status, detail, fix

    if not os.path.isfile(yml):
        check(
            checks,
            "models_yml",
            "models.yml",
            "warn",
            "未找到 models.yml；可参考文档添加条目",
            "在 models 目录创建 models.yml，字段见 " + DOC_URL,
        )
        return None
    try:
        storage = cv2.FileStorage(yml, cv2.FILE_STORAGE_READ)
        opened = storage.isOpened()
    except BaseException as exc:
        status, detail, fix = record("fail", "解析失败: " + str(exc), "修复 YAML 语法后重试")
        check(checks, "models_yml", "models.yml", status, detail, fix)
        return None
    if not opened:
        status, detail, fix = record("fail", "cv.FileStorage 无法打开", "修复 YAML 语法后重试")
        check(checks, "models_yml", "models.yml", status, detail, fix)
        try:
            storage.release()
        except BaseException:
            pass
        return None
    keys = []
    try:
        getter = getattr(storage, "getNodeNames", None)
        if callable(getter):
            raw_keys = cast(Iterable[object], getter())
            keys = [str(k) for k in raw_keys]
    except BaseException:
        keys = []
    if not keys:
        keys = yaml_top_level_keys(yml)
    check(
        checks,
        "models_yml",
        "models.yml",
        "pass",
        "OpenCV FileStorage 解析成功；模型 id: " + (", ".join(keys[:20]) if keys else "(空)"),
    )
    if not model_id:
        return storage
    if model_id not in keys:
        check(
            checks,
            "vision_model",
            "vision.model",
            "warn",
            "models.yml 中没有 " + repr(model_id) + " 条目",
            "添加 " + repr(model_id) + " 条目，或把 vision.model 改成已配置的 id",
        )
        storage.release()
        return None
    node = None
    details = []
    status = "pass"
    fix = ""
    try:
        # OpenCV 4.x has no FileStorage.get(); getNode works on 4.x and 5.x and returns
        # a FileNode view (the storage must stay alive until the node is released).
        node = storage.getNode(model_id)
        if node is None or node.empty() or node.isNone():
            status = "fail"
            details.append("条目为空或无法用 getNode 读取")
        else:
            for field in ("model", "width", "height", "postprocessing"):
                child = node.getNode(field)
                if child is None or child.empty() or child.isNone():
                    status = "fail"
                    details.append("缺字段 " + field)
            if status == "pass":
                model_rel = node.getNode("model").string()
                width = node.getNode("width").real()
                height = node.getNode("height").real()
                post = node.getNode("postprocessing").string()
                labels = ""
                labels_node = node.getNode("labels")
                if labels_node is not None and not labels_node.empty() and not labels_node.isNone():
                    labels = labels_node.string()
                if post != "yolov5":
                    status = "fail"
                    details.append("postprocessing=" + repr(post) + "，本期只支持 yolov5")
                if width <= 0 or height <= 0:
                    status = "fail"
                    details.append("width/height 必须为正数")
                full, error = safe_relative_path(models_dir, model_rel)
                if error:
                    status = "fail"
                    details.append("model: " + error)
                elif not full or not os.path.isfile(full):
                    status = "fail"
                    details.append("model 文件不存在: " + model_rel)
                else:
                    details.append("model=" + model_rel)
                if labels:
                    label_full, label_error = safe_relative_path(models_dir, labels)
                    if label_error:
                        status = "fail"
                        details.append("labels: " + label_error)
                    elif not label_full or not os.path.isfile(label_full):
                        status = "fail"
                        details.append("labels 文件不存在: " + labels)
                    else:
                        try:
                            with open(label_full, "r", encoding="utf-8") as handle:
                                count = len([ln for ln in handle.read().splitlines() if ln.strip()])
                            details.append("labels=" + labels + "(" + str(count) + " 行)")
                            if count == 0:
                                status = "warn"
                                details.append("labels 为空")
                        except OSError as exc:
                            status = "fail"
                            details.append("labels 读取失败: " + str(exc))
    except BaseException as exc:
        status = "fail"
        details.append("读取条目失败: " + exc.__class__.__name__ + ": " + str(exc)[:200])
    finally:
        try:
            storage.release()
        except BaseException:
            pass
    if status != "pass":
        fix = "按 " + DOC_URL + " 的 models.yml 示例修正条目"
    status, detail, fix = record(status, "；".join(details) or "结构有效", fix)
    check(checks, "vision_model", "vision.model 条目", status, detail, fix)
    return None


def probe(candidate, home, models_dir, worker_dir):
    checks = []
    started = time.time()
    features = candidate.get("features") if isinstance(candidate, dict) else {}
    vision = candidate.get("vision") if isinstance(candidate, dict) else {}
    dnn = candidate.get("dnn") if isinstance(candidate, dict) else {}
    features = features if isinstance(features, dict) else {}
    vision = vision if isinstance(vision, dict) else {}
    dnn = dnn if isinstance(dnn, dict) else {}

    # The host normally adds feature/env-lock checks; keep a safe fallback so a
    # stand-alone invocation still reports the flag.
    if not features:
        features = {"dnn": False}

    major_minor = version_tuple(sys.version_info)
    if PY_MIN <= major_minor <= PY_MAX:
        check(checks, "python", "解释器版本", "pass", sys.version.split()[0] + " (" + sys.executable + ")")
    else:
        check(
            checks,
            "python",
            "解释器版本",
            "fail",
            sys.version.split()[0],
            "使用 Python 3.11–3.13；当前解释器不在支持范围",
        )

    cv2 = None
    cv_version, cv_error = import_version("cv2")
    if cv_version is None:
        check(checks, "import_cv2", "import cv2", "fail", cv_error, "在该 venv 安装 opencv-python-headless；见 " + DOC_URL)
    else:
        import cv2 as cv2  # type: ignore[no-redef]
        check(checks, "import_cv2", "import cv2", "pass", cv_version)

    np_version, np_error = import_version("numpy")
    if np_version is None:
        check(checks, "import_numpy", "import numpy", "fail", np_error, "在该 venv 安装 numpy")
    else:
        check(checks, "import_numpy", "import numpy", "pass", np_version)

    backend = str(dnn.get("backend") or "opencv")
    device = str(dnn.get("device") or "cpu")
    if backend == "onnxruntime":
        ort_version, ort_error = import_version("onnxruntime")
        if ort_version is None:
            check(
                checks,
                "import_onnxruntime",
                "import onnxruntime",
                "fail",
                ort_error,
                "在自定义 venv 安装 onnxruntime（或对应平台的 onnxruntime-gpu/directml）；见 " + DOC_URL,
            )
        else:
            check(checks, "import_onnxruntime", "import onnxruntime", "pass", ort_version)
            try:
                import onnxruntime as ort  # type: ignore[no-redef]

                providers = [str(p) for p in ort.get_available_providers()]
            except BaseException as exc:
                providers = []
                check(checks, "ort_providers", "ONNX Runtime providers", "fail", str(exc), "重装 onnxruntime / 检查 CUDA/cuDNN 版本")
            gpu_providers = [p for p in providers if p in GPU_PROVIDERS]
            if device == "cpu":
                ok = "CPUExecutionProvider" in providers
                check(
                    checks,
                    "ort_providers",
                    "providers × device",
                    "pass" if ok else "fail",
                    "可用 providers: " + (", ".join(providers) or "(none)"),
                    "" if ok else "该 onnxruntime 构建缺少 CPUExecutionProvider",
                )
            elif device == "gpu":
                check(
                    checks,
                    "ort_providers",
                    "providers × device",
                    "pass" if gpu_providers else "fail",
                    "GPU providers: " + (", ".join(gpu_providers) or "(none)") + "；全部: " + ", ".join(providers),
                    "" if gpu_providers else "device=gpu 但没有 GPU provider；改装 onnxruntime-gpu/directml，或改成 auto/cpu",
                )
            else:  # auto
                if gpu_providers:
                    check(checks, "ort_providers", "providers × device", "pass", "auto 将使用 " + ", ".join(gpu_providers))
                else:
                    check(
                        checks,
                        "ort_providers",
                        "providers × device",
                        "warn",
                        "auto 没有 GPU provider，将回退 CPU：" + ", ".join(providers),
                        "如需 GPU，安装对应平台的 onnxruntime-gpu/directml；否则保持 auto 或 cpu",
                    )
    else:
        check(
            checks,
            "import_onnxruntime",
            "import onnxruntime",
            "pass",
            "backend=opencv，不需要也未导入 onnxruntime",
        )

    # Worker package / protocol importability. Importing protocol must stay
    # side-effect free; never import main.py (it can start the worker).
    protocol = None
    if os.path.isdir(worker_dir):
        sys.path.insert(0, worker_dir)
        try:
            import protocol as protocol  # type: ignore[no-redef]

            version = getattr(protocol, "PROTOCOL_VERSION", None)
            has_frame = getattr(protocol, "MAX_FRAME_BYTES", None)
            detail = "worker/protocol.py 可导入"
            if version is not None:
                detail += "；PROTOCOL_VERSION=" + str(version)
            if has_frame is not None:
                detail += "；MAX_FRAME_BYTES=" + str(has_frame)
            status = "pass" if (version is not None or has_frame is not None) else "warn"
            check(checks, "worker_protocol", "worker 模块/协议", status, detail, "" if status == "pass" else "worker/protocol.py 缺少版本/帧上限常量，请重新安装插件")
        except BaseException as exc:
            check(checks, "worker_protocol", "worker 模块/协议", "fail", str(exc), "插件安装不完整；重新安装/更新插件")
        finally:
            if sys.path and sys.path[0] == worker_dir:
                sys.path.pop(0)
    else:
        check(checks, "worker_protocol", "worker 模块/协议", "fail", "worker 目录不存在: " + worker_dir, "插件安装不完整")

    onnx_files = []
    if not os.path.isdir(models_dir):
        check(
            checks,
            "models_dir",
            "models 目录",
            "warn",
            "目录不存在: " + models_dir,
            "启用 DNN 并下载预设，或把模型与 models.yml 放进该目录",
        )
    else:
        check(checks, "models_dir", "models 目录", "pass", models_dir)
        for root, _dirs, files in os.walk(models_dir):
            for name in files:
                if name.lower().endswith(".onnx"):
                    onnx_files.append(os.path.join(root, name))
        if onnx_files:
            check(checks, "onnx_count", "ONNX 模型", "pass", str(len(onnx_files)) + " 个 .onnx")
        else:
            check(checks, "onnx_count", "ONNX 模型", "warn", "目录内没有任何 .onnx", "下载预设或放入自己的 ONNX（见 " + DOC_URL + "）")
        if cv2 is not None:
            validate_models_yml(
                cv2,
                models_dir,
                str(vision.get("model") or ""),
                checks,
                dnn_enabled=bool(features.get("dnn")),
            )
        else:
            check(checks, "models_yml", "models.yml", "fail", "缺少 cv2，无法用 cv.FileStorage 解析", "先修复 import cv2")
    return checks, started


def main():
    started = time.time()
    try:
        payload = json.loads(sys.stdin.read() or "{}")
    except BaseException as exc:
        print(json.dumps({"ok": False, "error": "invalid_request: " + str(exc)}))
        return 2
    candidate = payload.get("candidate") if isinstance(payload, dict) else {}
    home = str(payload.get("home") or "")
    models_dir = str(payload.get("modelsDir") or os.path.join(home, "vision", "models"))
    worker_dir = str(payload.get("workerDir") or os.path.dirname(os.path.abspath(__file__)))
    try:
        checks, _ = probe(candidate if isinstance(candidate, dict) else {}, home, models_dir, worker_dir)
    except BaseException as exc:
        # The probe is a one-shot diagnostic: it must always emit parseable JSON,
        # even for an unexpected failure (or features.dnn=false).
        checks = [
            {
                "id": "probe",
                "label": "probe",
                "status": "fail",
                "detail": "probe failed: " + exc.__class__.__name__ + ": " + str(exc)[:300],
                "fix": "重新安装插件后重试；这是 probe 自身错误，不是配置错误。",
            }
        ]
    summary = summarize(checks)
    result = {
        "ok": summary["fail"] == 0,
        "checks": checks,
        "summary": summary,
        "elapsedMs": int((time.time() - started) * 1000),
        "candidate": candidate if isinstance(candidate, dict) else {},
    }
    sys.stdout.write(json.dumps(result, ensure_ascii=False))
    sys.stdout.write("\n")
    sys.stdout.flush()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
