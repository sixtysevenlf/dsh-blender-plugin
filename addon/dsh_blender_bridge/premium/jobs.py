""" Premium job creation, polling and import. """

import re
import bpy
import uuid
import hashlib, hmac, base64
import os.path as osp
from urllib.parse import quote, urlencode, urlparse, urlunparse, parse_qsl

from ..assets.io import import_model_files
from .client import PREMIUM_MAX_IMAGE_BYTES, _premium_files_by_url, _premium_quality, premium_active, premium_integration_status, premium_request, premium_summary

def _premium_image_payload(image):
    """URLs go to the server as-is; local files are read here, since the
    server cannot see the user's disk."""
    if isinstance(image, (tuple, list)) and len(image) == 2:
        suffix, encoded = image
        raw = base64.b64decode(encoded) if isinstance(encoded, str) else encoded
        return {"image_base64": base64.b64encode(raw).decode("ascii"),
                "image_mime": _premium_mime(suffix)}, len(raw)
    if re.match(r'^https?://', str(image), re.IGNORECASE):
        return {"image_url": image}, 0
    with open(image, "rb") as f:
        raw = f.read()
    return {"image_base64": base64.b64encode(raw).decode("ascii"),
            "image_mime": _premium_mime(osp.splitext(image)[1])}, len(raw)


def _premium_mime(suffix):
    suffix = str(suffix or "").lower().lstrip(".")
    return {"jpg": "image/jpeg", "jpeg": "image/jpeg", "webp": "image/webp"}.get(suffix, "image/png")


def premium_create_job(model, quality="standard", text_prompt=None, image=None, bbox_condition=None):
    if bool(text_prompt) == bool(image):
        return {"error": "Give exactly one of a text prompt or an image."}
    if text_prompt and len(text_prompt) > 1024:
        return {"error": "Prompt exceeds 1024 characters limit"}
    if quality not in ("standard", "high"):
        return {"error": "quality must be 'standard' or 'high'"}
    payload = {"client_request_id": str(uuid.uuid4()), "operation": "generate",
               "model": model, "quality": quality}
    if text_prompt:
        payload["prompt"] = text_prompt
    else:
        try:
            image_fields, size = _premium_image_payload(image)
        except Exception as e:
            return {"error": f"Image encoding failed: {e}"}
        if size > PREMIUM_MAX_IMAGE_BYTES:
            return {"error": "Image is larger than 8 MB; use a smaller image or an image URL."}
        payload.update(image_fields)
    if bbox_condition:
        payload["bbox_condition"] = bbox_condition
    result = premium_request("POST", "/jobs", payload)
    if result.get("code") == "NETWORK_ERROR":
        # The first attempt may have reached the server; the same
        # client_request_id returns that job instead of charging again.
        result = premium_request("POST", "/jobs", payload)
    return result


def _premium_job(job_id):
    return premium_request("GET", f"/jobs/{quote(str(job_id), safe='')}")


def _premium_job_files(job):
    files = job.get("files")
    if not files and job.get("result_url"):
        # Jobs finished before the server sent file lists were always one GLB.
        files = [{"role": "model", "format": "glb", "url": job["result_url"], "file_name": "model.glb"}]
    return files or None


def premium_import_job(request_id, name):
    if not request_id:
        return {"succeed": False, "error": "request_id is required"}
    job = _premium_job(request_id)
    if job.get("code"):
        return {"succeed": False, **job}
    files = _premium_job_files(job)
    if job.get("status") != "succeeded" or not files:
        return {"succeed": False, "error": "Generation isn't finished yet. Poll until COMPLETED, then import."}
    return import_model_files(files, name)


# Rodin and Tripo: fal-style shapes, so the tools treat Premium like fal mode.

_PREMIUM_FAL_STATUS = {"reserved": "IN_QUEUE", "succeeded": "COMPLETED", "failed": "FAILED"}


def premium_poll_fal_job(request_id):
    if not request_id:
        return {"error": "request_id is required"}
    job = _premium_job(request_id)
    if job.get("code") or "status" not in job:
        return job
    status = _PREMIUM_FAL_STATUS.get(job["status"])
    if status is None:
        status = "IN_QUEUE" if job.get("provider_status") == "IN_QUEUE" else "IN_PROGRESS"
    reply = {"status": status}
    if status == "FAILED":
        reply["error"] = (job.get("error") or "Generation failed") + ". This attempt wasn't counted."
    return reply


def _premium_fal_reply(result, note=None):
    if not result.get("job_id"):
        return result
    reply = {"request_id": result["job_id"], "status": "IN_QUEUE", "message": premium_summary()}
    if note:
        reply["note"] = note
    return reply


def premium_create_rodin_job(text_prompt=None, images=None, bbox_condition=None):
    image = images[0] if images else None
    result = premium_create_job("rodin", "high", text_prompt=text_prompt, image=image,
                                bbox_condition=bbox_condition)
    return _premium_fal_reply(result, "Premium uses the first image only." if images and len(images) > 1 else None)


def premium_create_tripo_job(text_prompt=None, image=None, quality=None):
    quality = _premium_quality("tripo", quality, bool(image))
    return _premium_fal_reply(premium_create_job("tripo", quality, text_prompt=text_prompt, image=image))


# Hunyuan: Tencent-style shapes, so server.py wraps JobId as job_<id> and
# reads Status / ResultFile3Ds exactly as it does for OFFICIAL_API.

_PREMIUM_TENCENT_STATUS = {"reserved": "WAIT", "running": "RUN", "succeeded": "DONE", "failed": "FAIL"}


def premium_create_hunyuan_job(text_prompt=None, image=None, quality=None):
    quality = _premium_quality("hunyuan", quality, bool(image))
    result = premium_create_job("hunyuan", quality, text_prompt=text_prompt, image=image)
    if not result.get("job_id"):
        return result
    return {"Response": {"JobId": result["job_id"]}, "message": premium_summary()}


def premium_poll_hunyuan_job_status(job_id=None):
    if not job_id:
        return {"error": "JobId is required"}
    job = _premium_job(str(job_id).removeprefix("job_"))
    if job.get("code") or "status" not in job:
        return job
    response = {"Status": _PREMIUM_TENCENT_STATUS.get(job["status"], "RUN")}
    files = _premium_job_files(job) if job["status"] == "succeeded" else None
    if files:
        model = next((f for f in files if f.get("role") == "model"), files[0])
        _premium_files_by_url[model["url"]] = files
        response["ResultFile3Ds"] = [{"Type": str(model.get("format") or "glb").upper(), "Url": model["url"]}]
    if job["status"] == "failed":
        response["ErrorMessage"] = (job.get("error") or "Generation failed") + ". This attempt wasn't counted."
    return {"Response": response}


def premium_import_generated_asset_hunyuan(name, zip_file_url):
    if not zip_file_url or not re.match(r'^https?://', zip_file_url, re.IGNORECASE):
        return {"error": "Invalid URL format. Must start with http:// or https://"}
    files = _premium_files_by_url.get(zip_file_url)
    if not files:
        # Blender restarted since the poll: import the URL on its own.
        path = zip_file_url.split("?", 1)[0].split("#", 1)[0].lower()
        fmt = "obj" if path.endswith(".obj") else "glb"
        files = [{"role": "model", "format": fmt, "url": zip_file_url, "file_name": f"model.{fmt}"}]
    return import_model_files(files, name)
