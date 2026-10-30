""" Premium panel drawing helpers. """

from .client import PREMIUM_PRICING_URL, _premium_high_locked, _premium_status, premium_instance_id, premium_short_status, premium_summary

def _premium_draw_quality(prefs, layout, text="Default quality"):
    row = layout.row(align=True)
    row.label(text=text)
    row.prop_enum(prefs, "premium_default_quality", "standard")
    locked = _premium_high_locked()
    sub = row.row(align=True)
    sub.enabled = not locked
    sub.prop_enum(prefs, "premium_default_quality", "high", text="High (Pro)" if locked else "High")


def premium_draw_preferences(prefs, box):
    col = box.column()
    col.prop(prefs, "premium_license_key", text="License Key")
    row = col.row(align=True)
    if prefs.premium_instance_id:
        row.operator("dshbb.premium_deactivate", text="Deactivate", icon='UNLINKED')
        row.operator("dshbb.premium_refresh", text="Refresh", icon='FILE_REFRESH')
    else:
        row.operator("dshbb.premium_activate", text="Activate", icon='LINKED')
    if prefs.premium_instance_id:
        col.label(text=premium_summary() if _premium_status else "Click Refresh to load your plan.",
                  icon='INFO')
    _premium_draw_quality(prefs, col)
    row = col.row(align=True)
    row.operator("dshbb.premium_open_account", text="Manage account", icon='URL')
    row.operator("wm.url_open", text="Get Premium", icon='FUND').url = PREMIUM_PRICING_URL


def premium_draw_upsell(layout):
    """For own-key users: a single line saying Premium exists and where to get it."""
    row = layout.row(align=True)
    row.label(text="No API keys? Use Premium", icon='FUND')
    row.operator("wm.url_open", text="Get Premium").url = PREMIUM_PRICING_URL


def premium_draw_panel(layout, prefs):
    """Sidebar status in Premium mode. Reads the cache only."""
    box = layout.box()
    col = box.column(align=True)
    col.label(text=premium_short_status(), icon='FUND')
    if not premium_instance_id():
        col.label(text="Activate your license key in Preferences.")
        row = col.row(align=True)
        row.operator("screen.userpref_show", text="Open Preferences", icon='PREFERENCES').section = 'ADDONS'
        row.operator("wm.url_open", text="Get Premium", icon='FUND').url = PREMIUM_PRICING_URL
    elif not _premium_status:
        col.operator("dshbb.premium_refresh", text="Load plan", icon='FILE_REFRESH')
    elif not _premium_status.get("tier"):
        col.label(text="Your subscription isn't active.")
        row = col.row(align=True)
        row.operator("dshbb.premium_open_account", text="Manage account", icon='URL')
        row.operator("wm.url_open", text="Get Premium", icon='FUND').url = PREMIUM_PRICING_URL
    elif prefs is not None:
        # Quality only means something once there's a plan to spend.
        col.separator()
        _premium_draw_quality(prefs, col, text="Quality")
