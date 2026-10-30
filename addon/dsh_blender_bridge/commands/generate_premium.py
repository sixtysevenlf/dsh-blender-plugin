""" Premium generation command wrappers. """

import bpy

from ..premium.client import premium_active, premium_integration_status
from ..premium.jobs import premium_create_tripo_job, premium_import_job, premium_poll_fal_job

def premium_tripo_status():
    enabled = bool(getattr(bpy.context.scene, "dshbb_use_tripo", False))
    if premium_active():
        return premium_integration_status("tripo", enabled)
    return {"enabled": False, "message": "Tripo is only available with the upstream Premium service."}


def premium_tripo_handlers():
    """Tripo commands, registered in Premium mode when its checkbox is on."""
    if not (premium_active() and getattr(bpy.context.scene, "dshbb_use_tripo", False)):
        return {}
    return {
        "create_tripo_job": premium_create_tripo_job,
        "poll_tripo_job_status": premium_poll_fal_job,
        "import_generated_asset_tripo": premium_import_job,
    }
