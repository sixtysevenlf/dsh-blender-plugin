""" Telemetry consent commands. """

import bpy

from ..compat import ADDON_MODULE
from ..recorders.edit_capture import sync_edit_capture_handlers

class TelemetryCommands:
    def get_telemetry_consent(self):
        """Get the current telemetry consent status.

        Fails closed: if preferences cannot be read we report no consent. Not
        being able to read the preference means we do not know the user's
        answer, which is not the same as them having said yes.
        """
        try:
            # Get addon preferences - use the module name
            addon_prefs = bpy.context.preferences.addons.get(ADDON_MODULE)
            if addon_prefs:
                consent = bool(addon_prefs.preferences.telemetry_consent)
            else:
                consent = False
        except (AttributeError, KeyError):
            consent = False
        return {"consent": consent}

    def set_telemetry_consent(self, consent=False):
        """Write the telemetry consent preference.

        Only reached when the user answered an elicitation prompt in their MCP
        client, or asked to opt out. Assigning the property in code skips the
        BoolProperty update= callback, so the manual-edit handlers are
        re-synced explicitly.
        """
        try:
            addon_prefs = bpy.context.preferences.addons.get(ADDON_MODULE)
            if not addon_prefs:
                return {"error": "Could not read addon preferences"}
            addon_prefs.preferences.telemetry_consent = bool(consent)
        except (AttributeError, KeyError) as e:
            return {"error": f"Could not set telemetry consent: {e}"}

        try:
            sync_edit_capture_handlers()
        except Exception as e:
            print(f"DSH Blender Bridge: could not sync manual edit handlers: {e}")

        return {"consent": bool(consent)}
