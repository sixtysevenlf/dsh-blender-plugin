""" Hunyuan3D generation commands. """

from .client import HunyuanClientMixin
from .importing import HunyuanImportCommands
from .poll import HunyuanPollCommands
from .submit import HunyuanSubmitCommands


class HunyuanCommands(HunyuanClientMixin, HunyuanSubmitCommands,
                      HunyuanPollCommands, HunyuanImportCommands):
    """Composed Tencent Hunyuan3D command surface."""


__all__ = ["HunyuanCommands"]
