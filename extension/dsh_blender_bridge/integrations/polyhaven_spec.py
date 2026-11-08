""" Poly Haven asset policy: formats, map roles, taxonomy and search limits.

Pure data; the only bridge import is the public site link from config, so both
the HTTP client and the command layer can share this without a cycle.
"""

import re

from ..config import POLYHAVEN_SITE as POLYHAVEN_SITE


POLYHAVEN_SUPPORTED_FORMATS = {
    "hdris": ("hdr", "exr"),
    "textures": ("jpg", "png", "exr"),
    "models": ("blend",),
}

POLYHAVEN_DEFAULT_FORMATS = {"hdris": "hdr", "textures": "jpg", "models": "blend"}

# Models are imported from the .blend and nothing else. Poly Haven authors its
# models in Blender and generates every other format from that file, so glTF and
# FBX are lossy renderings of a material that is sitting right there - node
# groups collapse to a base colour, and procedural setups do not survive at all.
#
# glTF stays as a fallback for one case only: a .blend written by a newer
# Blender than the one running, which cannot be opened at all. See
# _polyhaven_blend_version.
POLYHAVEN_MODEL_FALLBACK_FORMAT = "gltf"

# Poly Haven's /files map keys, and what each map drives. Their casing is
# inconsistent and load-bearing - "Diffuse", "Rough", "Metal" and
# "Displacement" are capitalised while "nor_gl" and "arm" are not - so these
# are matched exactly instead of being lower-cased and guessed at.
#
# This is the set that drives a Principled BSDF directly, and it covers what
# Poly Haven's own .blend materials use for the large majority of the library.
# Of the rest the API offers, "arm" is an ORM repacking of maps already here,
# "rough_ao" is roughness with AO baked in, and "nor_dx" is the other normal map
# convention. "AO", "spec" and "Bump" need extra nodes to be worth anything.
#
# It is not a complete match for every asset: measured across the 860 published
# textures, 114 ship a .blend referencing a map not in this table - 74 use "AO",
# and 30 fabrics drive Anisotropic, Anisotropic Rotation and IOR from
# "anisotropy_strength", "anisotropy_rotation" and "spec_ior". Those materials
# come out flatter here than the artist built them.
#
# Downloading every map and then leaving most of them unconnected is what cost
# 7.4MB to build a 1k material that connected 1.9MB of it. This table brings
# that asset down to 3.9MB, all of it wired.
POLYHAVEN_TEXTURE_MAPS = {
    "Diffuse": "base_color",
    "Rough": "roughness",
    "Metal": "metallic",
    "Displacement": "displacement",
    "nor_gl": "normal",
    "nor_dx": "normal",
}

# Only the albedo is colour data; every other map is values the shader reads.
POLYHAVEN_COLOR_ROLES = {"base_color"}

POLYHAVEN_SLUG_RE = re.compile(r"^[A-Za-z0-9_-]{1,80}$")

# `type` is an integer in the API's asset records.
POLYHAVEN_ASSET_TYPES = {0: "hdris", 1: "textures", 2: "models"}

POLYHAVEN_SEARCH_LIMIT = 20
POLYHAVEN_SEARCH_MAX_LIMIT = 50

# Levels of the category tree returned when every asset type is asked for at
# once. Filtering on a category is inclusive, so a parent still selects
# everything nested beneath it.
POLYHAVEN_TAXONOMY_DEPTH_ALL = 2

# Poly Haven publishes thumbnails at 256px. The CDN resizes from the query
# string, so a preview worth looking at costs no stored file.
POLYHAVEN_PREVIEW_SIZE = 512
