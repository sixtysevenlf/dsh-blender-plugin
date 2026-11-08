""" Hunyuan3D status and request signing. """

import bpy

from ...integrations.hunyuan_client import get_tencent_cloud_sign_headers


class HunyuanClientMixin:
    @staticmethod
    def get_tencent_cloud_sign_headers(
        method: str,
        path: str,
        headParams: dict,
        data: dict,
        service: str,
        region: str,
        secret_id: str,
        secret_key: str,
        host: str | None = None
    ):
        return get_tencent_cloud_sign_headers(
            method, path, headParams, data, service, region, secret_id, secret_key, host
        )

    def get_hunyuan3d_status(self):
        """Get the current status of Hunyuan3D integration"""
        enabled = bpy.context.scene.dshbb_use_hunyuan3d
        hunyuan3d_mode = bpy.context.scene.dshbb_hunyuan3d_mode
        secret_id = self._get_hunyuan3d_secret_id()
        secret_key = self._get_hunyuan3d_secret_key()
        api_url = self._get_hunyuan3d_api_url()
        if enabled:
            match hunyuan3d_mode:
                case "OFFICIAL_API":
                    if not secret_id or not secret_key:
                        return {
                            "enabled": False,
                            "mode": hunyuan3d_mode,
                            "message": """Hunyuan3D integration is currently enabled, but SecretId or SecretKey is not given. To enable it:
                                1. In the 3D Viewport, find the DSH Blender Bridge panel in the sidebar (press N if hidden)
                                2. Keep the 'Use Tencent Hunyuan 3D model generation' checkbox checked
                                3. Choose the right platform and fill in the SecretId and SecretKey
                                4. Restart the DSH Blender Bridge connection"""
                        }
                case "LOCAL_API":
                    if not api_url:
                        return {
                            "enabled": False,
                            "mode": hunyuan3d_mode,
                            "message": """Hunyuan3D integration is currently enabled, but API URL  is not given. To enable it:
                                1. In the 3D Viewport, find the DSH Blender Bridge panel in the sidebar (press N if hidden)
                                2. Keep the 'Use Tencent Hunyuan 3D model generation' checkbox checked
                                3. Choose the right platform and fill in the API URL
                                4. Restart the DSH Blender Bridge connection"""
                        }
                case _:
                    return {
                        "enabled": False,
                        "message": "Hunyuan3D integration is enabled and mode is not supported."
                    }
            return {
                "enabled": True,
                "mode": hunyuan3d_mode,
                "message": "Hunyuan3D integration is enabled and ready to use."
            }
        return {
            "enabled": False,
            "message": """Hunyuan3D integration is currently disabled. To enable it:
                        1. In the 3D Viewport, find the DSH Blender Bridge panel in the sidebar (press N if hidden)
                        2. Check the 'Use Tencent Hunyuan 3D model generation' checkbox
                        3. Restart the DSH Blender Bridge connection"""
        }
