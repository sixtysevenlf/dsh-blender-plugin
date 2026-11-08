""" Hyper3D Rodin generation job submission. """

import base64
import json

import bpy
import requests

from ...integrations.hyper3d_client import (
    HYPER3D_FAL_QUEUE_URL,
    HYPER3D_MAIN_SITE_CREATE_URL,
)


class Hyper3DSubmitCommands:
    def create_rodin_job(self, *args, **kwargs):
        match bpy.context.scene.dshbb_hyper3d_mode:
            case "MAIN_SITE":
                return self.create_rodin_job_main_site(*args, **kwargs)
            case "FAL_AI":
                return self.create_rodin_job_fal_ai(*args, **kwargs)
            case _:
                return f"Error: Unknown Hyper3D Rodin mode!"

    def create_rodin_job_main_site(
            self,
            text_prompt: str | None = None,
            images: list[tuple[str, str]] | None = None,
            bbox_condition=None
        ):
        try:
            api_key = self._get_hyper3d_api_key()
            if not api_key:
                return {"error": "Hyper3D API key is not given"}
            if images is None:
                images = []
            """Call Rodin API, get the job uuid and subscription key"""
            files = [
                *[("images", (f"{i:04d}{img_suffix}", base64.b64decode(img) if isinstance(img, str) else img)) for i, (img_suffix, img) in enumerate(images)],
                ("tier", (None, "Sketch")),
                ("mesh_mode", (None, "Raw")),
                ("texture_mode", (None, "high")),
            ]
            if text_prompt:
                files.append(("prompt", (None, text_prompt)))
            if bbox_condition:
                files.append(("bbox_condition", (None, json.dumps(bbox_condition))))
            response = requests.post(
                HYPER3D_MAIN_SITE_CREATE_URL,
                headers={
                    "Authorization": f"Bearer {api_key}",
                },
                files=files,
                timeout=60,
            )
            data = response.json()
            return data
        except Exception as e:
            return {"error": str(e)}

    def create_rodin_job_fal_ai(
            self,
            text_prompt: str | None = None,
            images: list[tuple[str, str]] | None = None,
            bbox_condition=None
        ):
        try:
            api_key = self._get_hyper3d_api_key()
            if not api_key:
                return {"error": "Hyper3D API key is not given"}
            req_data: dict[str, object] = {
                "tier": "Sketch",
            }
            if images:
                req_data["input_image_urls"] = images
            if text_prompt:
                req_data["prompt"] = text_prompt
            if bbox_condition:
                req_data["bbox_condition"] = bbox_condition
            response = requests.post(
                HYPER3D_FAL_QUEUE_URL,
                headers={
                    "Authorization": f"Key {api_key}",
                    "Content-Type": "application/json",
                },
                json=req_data,
                timeout=60,
            )
            data = response.json()
            return data
        except Exception as e:
            return {"error": str(e)}
