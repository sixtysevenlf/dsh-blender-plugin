""" Hyper3D Rodin generation commands. """

from .client import Hyper3DClientMixin
from .importing import Hyper3DImportCommands
from .poll import Hyper3DPollCommands
from .submit import Hyper3DSubmitCommands


class Hyper3DCommands(Hyper3DClientMixin, Hyper3DSubmitCommands,
                      Hyper3DPollCommands, Hyper3DImportCommands):
    """Composed Hyper3D Rodin command surface."""


__all__ = ["Hyper3DCommands"]
