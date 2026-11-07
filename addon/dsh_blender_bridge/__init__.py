# Code created by Siddharth Ahuja: www.github.com/ahujasid © 2025

""" DSH Blender Bridge add-on entry point. """

from .protocol import ADDON_PROTOCOL_VERSION as ADDON_PROTOCOL_VERSION, ADDON_VERSION as ADDON_VERSION
from .registration import register, unregister as unregister


if __name__ == "__main__":
    register()
