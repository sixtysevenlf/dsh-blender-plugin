""" Single command registry for the Blender add-on.

One table owns every wire command: name, accepted parameter names, the
server attribute that handles it, the feature gate that must be enabled, and
whether the command stays available while AI operations are paused.

Counts are deliberately explicit and stable:

* 25 registered commands - 21 read-only commands the model-facing surface
  can inspect plus 4 internal transport commands.
* 26 wire commands - the same 25 plus the transport-level "ping" liveness
  probe, which is not part of the catalog.

The registry is pure data (no bpy import) so tests and tooling can read it
without Blender. Runtime composition helpers at the bottom import the command
mixins / recorders lazily to keep this module free of import cycles.
"""

from typing import NamedTuple, Optional

# Registered commands = 25. Wire commands = these 25 + ping (26).
REGISTERED_COMMAND_COUNT = 25
WIRE_COMMAND_COUNT = 26

GROUP_ORDER = ("base", "polyhaven", "hyper3d", "sketchfab", "polypizza", "hunyuan")

# Feature gate -> scene property that must be enabled for the command to exist.
GATE_PROPERTIES = {
    "polyhaven": "dshbb_use_polyhaven",
    "hyper3d": "dshbb_use_hyper3d",
    "sketchfab": "dshbb_use_sketchfab",
    "polypizza": "dshbb_use_polypizza",
    "hunyuan": "dshbb_use_hunyuan3d",
}

# Commands that stay available while the user has paused AI operations. Every
# other command starting with "get_" is pause-safe through the prefix rule in
# protocol.is_pause_safe_command(); write commands are never pause-safe.
PAUSE_SAFE_EXTRA = frozenset({
    "ping",
    "describe_node_type",
    "bpy_api_lookup",
    "get_activity_state",
    "get_selection",
    "get_collections",
})


class CommandSpec(NamedTuple):
    """One wire command. handler is the DSHBlenderBridgeServer attribute name."""

    name: str
    params: tuple
    handler: str
    gate: Optional[str]  # None for base commands, else a key of GATE_PROPERTIES
    pause_safe: bool
    network: bool = False  # requires Blender's online access / an external API


def _command(name, params=(), gate=None, pause_safe=None, handler=None, network=False):
    return CommandSpec(
        name=name,
        params=tuple(params),
        handler=handler or name,
        gate=gate,
        pause_safe=(name in PAUSE_SAFE_EXTRA) if pause_safe is None else bool(pause_safe),
        network=network,
    )


# The 25 registered commands, in protocol order (18 base + 7 gated).
REGISTERED_COMMANDS = (
    # -- base: always registered -------------------------------------------
    _command("get_scene_info"),
    _command("get_world_state_snapshot"),
    _command("get_addon_info"),
    _command("get_object_info", ("name",)),
    _command("get_viewport_screenshot", ("max_size", "filepath", "format")),
    _command("execute_code", ("code",)),
    _command("describe_node_type", ("bl_idname", "property_overrides")),
    _command("bpy_api_lookup", ("query",)),
    _command("get_activity_state"),
    _command("get_selection"),
    _command("send_selection_to_dsh", ("timeout",)),
    _command("get_collections", ("limit",)),
    _command("send_collection_to_dsh", ("collection", "timeout")),
    _command("get_polyhaven_status"),
    _command("get_hyper3d_status"),
    _command("get_sketchfab_status"),
    _command("get_polypizza_status"),
    _command("get_hunyuan3d_status"),
    # -- polyhaven gate ----------------------------------------------------
    _command("get_polyhaven_categories", ("asset_type",), gate="polyhaven"),
    _command("search_polyhaven_assets",
             ("asset_type", "category", "attributes", "query", "limit", "min_size_m"),
             gate="polyhaven", network=True),
    # -- hyper3d gate ------------------------------------------------------
    _command("poll_rodin_job_status", (), gate="hyper3d", network=True),
    # -- sketchfab gate ----------------------------------------------------
    _command("search_sketchfab_models", ("query", "categories", "count", "downloadable"),
             gate="sketchfab", network=True),
    _command("get_sketchfab_model_preview", ("uid",), gate="sketchfab", network=True),
    # -- polypizza gate ----------------------------------------------------
    _command("search_polypizza_models",
             ("query", "category", "licence", "animated", "limit", "page"), gate="polypizza",
             network=True),
    # -- hunyuan gate ------------------------------------------------------
    _command("poll_hunyuan_job_status", (), gate="hunyuan", network=True),
)

# Transport liveness probe, not a catalog command, but part of the wire surface.
PING_COMMAND = _command("ping", (), pause_safe=True, handler="ping_response")

WIRE_COMMANDS = REGISTERED_COMMANDS + (PING_COMMAND,)

COMMAND_SPECS = {spec.name: spec for spec in REGISTERED_COMMANDS}
WIRE_SPECS = {spec.name: spec for spec in WIRE_COMMANDS}

COMMAND_NAMES = tuple(spec.name for spec in REGISTERED_COMMANDS)
COMMAND_GROUPS = tuple(
    (
        group,
        tuple(spec.name for spec in REGISTERED_COMMANDS
              if (spec.gate is None if group == "base" else spec.gate == group)),
    )
    for group in GROUP_ORDER
)
GATED_COMMAND_GROUPS = frozenset(GROUP_ORDER[1:])
PAUSE_SAFE_COMMANDS = frozenset(
    spec.name for spec in WIRE_COMMANDS if spec.pause_safe
)

if len(REGISTERED_COMMANDS) != REGISTERED_COMMAND_COUNT:
    raise RuntimeError(
        "command_registry: expected %d registered commands, got %d"
        % (REGISTERED_COMMAND_COUNT, len(REGISTERED_COMMANDS))
    )
if len(WIRE_COMMANDS) != WIRE_COMMAND_COUNT:
    raise RuntimeError(
        "command_registry: expected %d wire commands, got %d"
        % (WIRE_COMMAND_COUNT, len(WIRE_COMMANDS))
    )


def wire_spec_rows():
    """Wire-level spec rows for the model-facing inspect help surface.

    Pure data, no bpy: the Gateway merges these with the host catalog so
    blender_inspect can list name / args / gate / pause_safe per command
    without a second hardcoded table.
    """
    return [
        {
            "name": spec.name,
            "params": list(spec.params),
            "gate": spec.gate,
            "pause_safe": spec.pause_safe,
            "network": spec.network,
        }
        for spec in WIRE_COMMANDS
    ]


def command_spec(name):
    """Return the CommandSpec for a wire command, or None."""
    return WIRE_SPECS.get(name)


def gate_property(spec):
    """Scene property that gates a command (None for base commands)."""
    return GATE_PROPERTIES.get(spec.gate) if spec and spec.gate else None


def command_requires_network(name):
    """True when a wire command calls an external API and needs online access."""
    spec = WIRE_SPECS.get(name)
    return bool(spec and spec.network)


# ---------------------------------------------------------------------------
# Runtime composition (delayed imports: keep this module import-cycle free)
# ---------------------------------------------------------------------------

def command_mixins():
    """Command mixin classes composed into DSHBlenderBridgeServer, in MRO order."""
    from .commands.activity import ActivityCommands
    from .commands.assets_polyhaven import PolyHavenCommands
    from .commands.core import CoreCommands
    from .commands.hunyuan import HunyuanCommands
    from .commands.hyper3d import Hyper3DCommands
    from .commands.polypizza import PolypizzaCommands
    from .commands.sketchfab import SketchfabCommands
    from .commands.material import MaterialCommands
    from .commands.scene import SceneCommands
    from .commands.selection import SelectionCommands

    return (
        CoreCommands,
        SceneCommands,
        SelectionCommands,
        ActivityCommands,
        MaterialCommands,
        PolyHavenCommands,
        SketchfabCommands,
        PolypizzaCommands,
        Hyper3DCommands,
        HunyuanCommands,
    )


def start_runtime_handlers():
    """Install the local activity handlers (main thread)."""
    from .recorders.activity import register_activity_handlers

    register_activity_handlers()


def stop_runtime_handlers():
    """Remove the runtime handlers installed by start_runtime_handlers()."""
    from .recorders.activity import unregister_activity_handlers

    unregister_activity_handlers()


def tick_activity():
    """Advance the activity tracker from the main-thread timer."""
    from .recorders.activity import get_activity_tracker

    get_activity_tracker().tick()


def drain_human_edits():
    """Return the human edit records collected since the last drain."""
    from .recorders.activity import get_activity_tracker

    return get_activity_tracker().drain_human_edits()


def begin_agent_command():
    """Context manager marking the current dispatch as agent-originated."""
    from .recorders.user_edit import get_edit_recorder

    return get_edit_recorder().agent_command()


def end_agent_command():
    """Record that the agent just acted and resync the human baseline."""
    from .recorders.activity import get_activity_tracker

    tracker = get_activity_tracker()
    tracker.note_agent_end()
    tracker._resync()
