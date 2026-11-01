""" Blender version helpers shared across the addon package. """

import bpy


ADDON_MODULE = __package__


def bpy_version():
    try:
        return tuple(bpy.app.version)
    except Exception:
        return (0, 0, 0)


def is_bpy_at_least(major, minor=0, patch=0):
    version = bpy_version()
    return version >= (major, minor, patch)
