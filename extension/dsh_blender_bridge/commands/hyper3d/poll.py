""" Hyper3D Rodin generation job polling. """

import bpy
import requests

from ...integrations.hyper3d_client import (
    HYPER3D_FAL_STATUS_URL,
    HYPER3D_MAIN_SITE_STATUS_URL,
)


class Hyper3DPollCommands:
    def poll_rodin_job_status(self, *args, **kwargs):
        match bpy.context.scene.dshbb_hyper3d_mode:
            case "MAIN_SITE":
                return self.poll_rodin_job_status_main_site(*args, **kwargs)
            case "FAL_AI":
                return self.poll_rodin_job_status_fal_ai(*args, **kwargs)
            case _:
                return f"Error: Unknown Hyper3D Rodin mode!"

    def poll_rodin_job_status_main_site(self, subscription_key: str):
        """Call the job status API to get the job status"""
        api_key = self._get_hyper3d_api_key()
        if not api_key:
            return {"error": "Hyper3D API key is not given"}
        response = requests.post(
            HYPER3D_MAIN_SITE_STATUS_URL,
            headers={
                "Authorization": f"Bearer {api_key}",
            },
            json={
                "subscription_key": subscription_key,
            },
            timeout=30,
        )
        data = response.json()
        return {
            "status_list": [i["status"] for i in data["jobs"]]
        }

    def poll_rodin_job_status_fal_ai(self, request_id: str):
        """Call the job status API to get the job status"""
        api_key = self._get_hyper3d_api_key()
        if not api_key:
            return {"error": "Hyper3D API key is not given"}
        response = requests.get(
            HYPER3D_FAL_STATUS_URL.format(request_id=request_id),
            headers={
                "Authorization": f"KEY {api_key}",
            },
            timeout=30,
        )
        data = response.json()
        return data
