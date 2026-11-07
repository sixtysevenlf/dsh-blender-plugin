# Code created by Siddharth Ahuja: www.github.com/ahujasid © 2025

""" DSH Blender Bridge add-on entry point. """

from .protocol import ADDON_PROTOCOL_VERSION as ADDON_PROTOCOL_VERSION, ADDON_VERSION as ADDON_VERSION
from .registration import register, unregister as unregister

bl_info = {
    "name": "DSH Blender Bridge",
    "author": "Siddharth Ahuja",
    "version": (2, 0, 0),
    "blender": (4, 2, 0),
    "location": "View3D > Sidebar > DSH Blender Bridge",
    "description": "Connect Blender to DSH through the local DSH Gateway",
    "doc_url": "https://github.com/sixtysevenlf/dsh-blender-plugin",
    "category": "Interface",
}


if __name__ == "__main__":
    register()
