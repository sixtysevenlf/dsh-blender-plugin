"""Minimal bpy stub for importing the DSH Blender Bridge add-on package.

This is intentionally small: it provides the modules, classes and properties the
package touches at import time and during register()/unregister(). It is not a
Blender emulator and must not be used as evidence that the add-on works inside
a real Blender session.
"""

from __future__ import annotations

import sys
import types


class _Property:
    def __init__(self, kind, **kwargs):
        self.kind = kind
        self.kwargs = kwargs

    def __repr__(self):
        return f"_Property({self.kind!r})"


class _Base:
    pass


class _Scene(_Base):
    pass


class _Collection(_Base):
    pass


class _Node(_Base):
    pass


class _ShaderNodeTexSky(_Base):
    pass


class _WindowManager(_Base):
    windows = []


class _Preferences:
    def __init__(self):
        self.addons = {}


class _Context:
    def __init__(self):
        self.scene = _Scene()
        self.scene.name = "Scene"
        self.scene.dshbb_ai_paused = False
        self.scene.dshbb_backend_url = ""
        self.scene.dshbb_port = 9876
        self.scene.dshbb_auto_start_server = False
        self.scene.dshbb_server_running = False
        self.scene.dshbb_selection_mode = "MULTI"
        self.scene.dshbb_collection_target = None
        self.scene.dshbb_use_polyhaven = False
        self.scene.dshbb_use_hyper3d = False
        self.scene.dshbb_use_sketchfab = False
        self.scene.dshbb_use_polypizza = False
        self.scene.dshbb_use_hunyuan3d = False
        self.scene.dshbb_use_tripo = False
        self.preferences = _Preferences()
        self.window_manager = _WindowManager()
        self.selected_objects = []
        self.active_object = None
        self.mode = "OBJECT"
        self.view_layer = types.SimpleNamespace(objects=types.SimpleNamespace(active=None))
        self.window = None
        self.screen = None
        self.area = None

    def temp_override(self, **kwargs):
        class _Override:
            def __enter__(self_inner):
                return self

            def __exit__(self_inner, *exc):
                return False

        return _Override()


class _Timers:
    def __init__(self):
        self._registered = {}

    def is_registered(self, function):
        return function in self._registered

    def register(self, function, first_interval=0.0, persistent=False):
        self._registered[function] = {
            "first_interval": first_interval,
            "persistent": persistent,
        }

    def unregister(self, function):
        self._registered.pop(function, None)


class _Handlers:
    def __init__(self):
        self.load_post = []
        self.undo_post = []
        self.redo_post = []
        self.depsgraph_update_post = []
        self.save_pre = []
        self.save_post = []
        self.frame_change_post = []


class _App:
    def __init__(self):
        self.version = (4, 2, 10)
        self.version_string = "4.2.10"
        self.background = True
        self.tempdir = None
        self.binary_path = ""
        self.handlers = _Handlers()
        self.timers = _Timers()


class _Utils:
    def __init__(self):
        self.registered = []

    def register_class(self, cls):
        if cls in self.registered:
            raise RuntimeError(f"class {cls!r} already registered")
        self.registered.append(cls)

    def unregister_class(self, cls):
        try:
            self.registered.remove(cls)
        except ValueError:
            raise RuntimeError(f"class {cls!r} is not registered") from None


def _make_prop(kind):
    def factory(*args, **kwargs):
        return _Property(kind, **kwargs)

    factory.__name__ = kind
    return factory


def install():
    """Install fake bpy/mathutils modules into sys.modules and return bpy."""
    if "bpy" in sys.modules and getattr(sys.modules["bpy"], "_dshbb_stub", False):
        return sys.modules["bpy"]

    bpy = types.ModuleType("bpy")
    bpy.__path__ = []
    bpy._dshbb_stub = True

    types_mod = types.ModuleType("bpy.types")
    for name in (
        "AddonPreferences",
        "Collection",
        "Node",
        "Operator",
        "Panel",
        "PropertyGroup",
        "Scene",
        "ShaderNodeTexSky",
        "UIList",
        "WindowManager",
    ):
        setattr(types_mod, name, globals().get(name, _Base))
    types_mod.Collection = _Collection
    types_mod.Node = _Node
    types_mod.Scene = _Scene
    types_mod.ShaderNodeTexSky = _ShaderNodeTexSky
    types_mod.WindowManager = _WindowManager

    props_mod = types.ModuleType("bpy.props")
    for kind in (
        "BoolProperty",
        "CollectionProperty",
        "EnumProperty",
        "FloatProperty",
        "IntProperty",
        "PointerProperty",
        "StringProperty",
    ):
        setattr(props_mod, kind, _make_prop(kind))

    utils_mod = types.ModuleType("bpy.utils")
    utils = _Utils()
    utils_mod.register_class = utils.register_class
    utils_mod.unregister_class = utils.unregister_class
    utils_mod.registered = utils.registered

    app_mod = types.ModuleType("bpy.app")
    app = _App()
    app_mod.version = app.version
    app_mod.version_string = app.version_string
    app_mod.background = app.background
    app_mod.tempdir = app.tempdir
    app_mod.handlers = app.handlers
    app_mod.timers = app.timers

    handlers_mod = types.ModuleType("bpy.app.handlers")
    handlers_mod.persistent = lambda function: function
    handlers_mod.load_post = app.handlers.load_post
    handlers_mod.undo_post = app.handlers.undo_post
    handlers_mod.redo_post = app.handlers.redo_post
    handlers_mod.depsgraph_update_post = app.handlers.depsgraph_update_post
    handlers_mod.save_pre = app.handlers.save_pre
    handlers_mod.save_post = app.handlers.save_post
    handlers_mod.frame_change_post = app.handlers.frame_change_post

    data_mod = types.ModuleType("bpy.data")
    for name in (
        "collections",
        "filepath",
        "images",
        "libraries",
        "materials",
        "node_groups",
        "objects",
        "worlds",
    ):
        setattr(data_mod, name, {} if name != "filepath" else "")
    data_mod.filepath = ""

    ops_mod = types.ModuleType("bpy.ops")
    for name in (
        "export_scene",
        "import_scene",
        "mesh",
        "object",
        "screen",
        "wm",
    ):
        setattr(ops_mod, name, types.SimpleNamespace())

    path_mod = types.ModuleType("bpy.path")
    path_mod.abspath = lambda value, **kwargs: value

    context = _Context()

    bpy.types = types_mod
    bpy.props = props_mod
    bpy.utils = utils_mod
    bpy.app = app_mod
    bpy.data = data_mod
    bpy.ops = ops_mod
    bpy.context = context
    bpy.path = path_mod

    sys.modules["bpy"] = bpy
    sys.modules["bpy.types"] = types_mod
    sys.modules["bpy.props"] = props_mod
    sys.modules["bpy.utils"] = utils_mod
    sys.modules["bpy.app"] = app_mod
    sys.modules["bpy.app.handlers"] = handlers_mod
    sys.modules["bpy.data"] = data_mod
    sys.modules["bpy.ops"] = ops_mod
    sys.modules["bpy.path"] = path_mod

    if "requests" not in sys.modules:
        requests_stub = types.ModuleType("requests")
        requests_stub.utils = types.SimpleNamespace(
            default_headers=lambda: {"User-Agent": "dshbb-test"}
        )
        exceptions = types.SimpleNamespace(
            RequestException=type("RequestException", (Exception,), {}),
            Timeout=type("Timeout", (Exception,), {}),
        )
        requests_stub.exceptions = exceptions

        def _no_network(*args, **kwargs):
            raise AssertionError("test attempted a network request")

        requests_stub.request = _no_network
        requests_stub.get = _no_network
        requests_stub.post = _no_network
        sys.modules["requests"] = requests_stub

    if "mathutils" not in sys.modules:
        mathutils = types.ModuleType("mathutils")

        class Vector:
            def __init__(self, values=(0.0, 0.0, 0.0)):
                self.values = tuple(values)

            def __iter__(self):
                return iter(self.values)

            def __len__(self):
                return len(self.values)

            def __getitem__(self, index):
                return self.values[index]

            def __add__(self, other):
                return Vector(a + b for a, b in zip(self.values, other))

            def __sub__(self, other):
                return Vector(a - b for a, b in zip(self.values, other))

            def __mul__(self, scalar):
                return Vector(value * scalar for value in self.values)

            __rmul__ = __mul__

            def __truediv__(self, scalar):
                return Vector(value / scalar for value in self.values)

            def __repr__(self):
                return f"Vector({self.values!r})"

        mathutils.Vector = Vector
        mathutils.Matrix = type("Matrix", (), {})
        mathutils.Euler = type("Euler", (), {})
        sys.modules["mathutils"] = mathutils

    return bpy


def uninstall():
    """Remove the stub and the imported add-on package (for isolated tests)."""
    for name in list(sys.modules):
        if name == "bpy" or name.startswith("bpy.") or name == "mathutils":
            del sys.modules[name]
        if name == "addon.dsh_blender_bridge" or name.startswith("addon.dsh_blender_bridge."):
            del sys.modules[name]
