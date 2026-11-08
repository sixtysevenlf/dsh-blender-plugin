""" Poly Haven asset commands. """

from .download import PolyHavenDownloadMixin
from .hdri import PolyHavenHdriMixin
from .material import PolyHavenMaterialMixin
from .model import PolyHavenModelMixin


class PolyHavenImportMixin(PolyHavenDownloadMixin, PolyHavenHdriMixin,
                           PolyHavenMaterialMixin, PolyHavenModelMixin):
    """Composed Poly Haven download/import surface for PolyHavenCommands."""


__all__ = [
    "PolyHavenDownloadMixin",
    "PolyHavenHdriMixin",
    "PolyHavenImportMixin",
    "PolyHavenMaterialMixin",
    "PolyHavenModelMixin",
]
