"""Unit checks for the single add-on command registry.

The registry is the one table behind protocol, server dispatch and the command
matrix. These tests prove the counts, groups, gates, pause policy and handler
signatures stay consistent without starting Blender.
"""

from __future__ import annotations

import importlib
import inspect

import bpy_stub

bpy = bpy_stub.install()

registry = importlib.import_module("addon.dsh_blender_bridge.command_registry")
protocol = importlib.import_module("addon.dsh_blender_bridge.protocol")
server_module = importlib.import_module("addon.dsh_blender_bridge.server")


def test_command_counts_are_frozen():
    assert registry.REGISTERED_COMMAND_COUNT == 38
    assert registry.WIRE_COMMAND_COUNT == 39
    assert len(registry.REGISTERED_COMMANDS) == 38
    assert len(registry.WIRE_COMMANDS) == 39
    assert len(registry.COMMAND_NAMES) == 38
    assert len(set(registry.COMMAND_NAMES)) == 38
    assert len(registry.WIRE_SPECS) == 39
    assert set(registry.COMMAND_SPECS) == set(registry.COMMAND_NAMES)


def test_ping_is_wire_only():
    assert registry.PING_COMMAND.name == "ping"
    assert registry.PING_COMMAND.handler == "ping_response"
    assert registry.PING_COMMAND.pause_safe is True
    assert "ping" not in registry.COMMAND_NAMES
    assert "ping" not in registry.COMMAND_SPECS
    assert "ping" in registry.WIRE_SPECS
    assert {spec.name for spec in registry.WIRE_COMMANDS} == set(registry.COMMAND_NAMES) | {"ping"}


def test_dispatch_table_is_derived_from_the_registry(monkeypatch):
    """The server resolves commands through registry.WIRE_SPECS, not its own table."""
    server = server_module.DSHBlenderBridgeServer(port=0)
    scene = bpy.context.scene
    previous = {}
    for gate in registry.GATE_PROPERTIES.values():
        previous[gate] = getattr(scene, gate, False)
        setattr(scene, gate, True)
    try:
        for spec in registry.WIRE_COMMANDS:
            assert server._resolve_handler(spec.name) is not None, spec.name
        assert server._resolve_handler("not_a_command") is None
        # A spec injected into the registry is dispatchable without touching server code.
        extra = registry.CommandSpec("unit_injected_command", (), "ping_response", None, True)
        monkeypatch.setitem(registry.WIRE_SPECS, extra.name, extra)
        assert server._resolve_handler("unit_injected_command") is not None
        monkeypatch.delitem(registry.WIRE_SPECS, extra.name)
        assert server._resolve_handler("unit_injected_command") is None
    finally:
        for gate, value in previous.items():
            setattr(scene, gate, value)


def test_protocol_no_longer_reexports_the_command_registry():
    """Protocol keeps only PAUSE_SAFE_COMMANDS; command_registry owns the truth."""
    assert protocol.PAUSE_SAFE_COMMANDS == registry.PAUSE_SAFE_COMMANDS
    for name in ("COMMAND_NAMES", "COMMAND_GROUPS", "GATED_COMMAND_GROUPS"):
        assert not hasattr(protocol, name), name


def test_groups_cover_every_command_once():
    assert registry.GROUP_ORDER == (
        "base", "polyhaven", "hyper3d", "sketchfab", "polypizza", "hunyuan")
    flattened = [name for _group, names in registry.COMMAND_GROUPS for name in names]
    assert flattened == list(registry.COMMAND_NAMES)
    assert len(flattened) == len(set(flattened)) == 38
    groups = dict(registry.COMMAND_GROUPS)
    assert groups["base"] == tuple(name for name in registry.COMMAND_NAMES)[:22]
    assert sum(len(names) for names in groups.values()) == 38
    assert sum(len(names) for group, names in groups.items() if group != "base") == 16
    assert registry.GATED_COMMAND_GROUPS == frozenset(registry.GROUP_ORDER[1:])
    assert set(registry.GATE_PROPERTIES) == set(registry.GATED_COMMAND_GROUPS)


def test_every_gated_command_declares_a_known_gate():
    gates = set(registry.GATE_PROPERTIES)
    for spec in registry.REGISTERED_COMMANDS:
        property_name = registry.gate_property(spec)
        if spec.gate is None:
            assert property_name is None, spec.name
        else:
            assert spec.gate in gates, spec.name
            assert property_name == registry.GATE_PROPERTIES[spec.gate]
            assert property_name.startswith("dshbb_use_"), spec.name
    # Base commands are never gated; the gated groups name every one of them.
    for group, names in registry.COMMAND_GROUPS:
        for name in names:
            spec = registry.COMMAND_SPECS[name]
            assert spec.gate == (None if group == "base" else group), name


def test_pause_policy_is_derived_from_names_and_extras():
    # The registry flags the explicit allow-list; the get_* prefix rule lives in
    # protocol.is_pause_safe_command(), which combines both.
    flagged = {spec.name for spec in registry.WIRE_COMMANDS if spec.pause_safe}
    assert flagged == set(registry.PAUSE_SAFE_EXTRA)
    assert set(registry.PAUSE_SAFE_COMMANDS) == set(registry.PAUSE_SAFE_EXTRA) == flagged
    # Write commands must never be pause-safe.
    for name in (
        "execute_code", "export_scene", "send_selection_to_dsh", "send_collection_to_dsh",
        "set_texture", "set_telemetry_consent", "download_polyhaven_asset",
        "download_sketchfab_model", "download_polypizza_model", "create_rodin_job",
        "poll_rodin_job_status", "import_generated_asset", "create_hunyuan_job",
        "poll_hunyuan_job_status", "import_generated_asset_hunyuan",
    ):
        assert registry.WIRE_SPECS[name].pause_safe is False, name
    for name in ("get_scene_info", "get_selection", "get_polyhaven_status", "ping"):
        assert protocol.is_pause_safe_command(name) is True, name
    for name in ("execute_code", "export_scene", "download_sketchfab_model"):
        assert protocol.is_pause_safe_command(name) is False, name


def test_command_spec_and_gate_property_helpers():
    assert registry.command_spec("ping") is registry.PING_COMMAND
    assert registry.command_spec("get_object_info").params == ("name",)
    assert registry.command_spec("does_not_exist") is None
    assert registry.gate_property(registry.command_spec("ping")) is None
    assert registry.gate_property(registry.command_spec("search_sketchfab_models")) == \
        "dshbb_use_sketchfab"


def _handler_function(handler_name):
    """Resolve the class-level handler function for a registry handler name."""
    sources = (server_module.DSHBlenderBridgeServer,) + registry.command_mixins()
    functions = []
    for source in sources:
        for klass in source.__mro__:
            if handler_name in klass.__dict__:
                functions.append(klass.__dict__[handler_name])
                break
    assert functions, "no class defines handler " + handler_name
    # Everything that defines this handler must agree on the same function.
    assert len({id(function) for function in functions}) == 1, handler_name
    return functions[0]


def test_every_wire_handler_exists_on_the_server():
    for spec in registry.WIRE_COMMANDS:
        handler = getattr(server_module.DSHBlenderBridgeServer, spec.handler, None)
        assert callable(handler), spec.name
        assert _handler_function(spec.handler) is not None
    # Handler names are unique across the wire surface.
    handlers = [spec.handler for spec in registry.WIRE_COMMANDS]
    assert len(handlers) == len(set(handlers)) == 39


def test_fixed_parameter_lists_match_handler_signatures():
    for spec in registry.REGISTERED_COMMANDS:
        function = _handler_function(spec.handler)
        if not spec.params:
            continue
        parameters = [
            name for name in inspect.signature(function).parameters
            if name != "self"
        ]
        # Wrappers with *args/**kwargs accept anything (params is empty then).
        positional = [
            name for name in parameters
            if inspect.signature(function).parameters[name].kind in (
                inspect.Parameter.POSITIONAL_OR_KEYWORD, inspect.Parameter.KEYWORD_ONLY)
        ]
        assert tuple(positional[:len(spec.params)]) == spec.params, (
            spec.name + ": registry says " + repr(spec.params) +
            " but handler accepts " + repr(tuple(positional)))


def test_mixin_composition_is_complete_and_acyclic():
    mixins = registry.command_mixins()
    assert len(mixins) == 11
    assert len(set(mixins)) == 11
    assert all(inspect.isclass(mixin) for mixin in mixins)
    for mixin in mixins:
        assert mixin.__mro__[-1] is object
    server_class = server_module.DSHBlenderBridgeServer
    for mixin in mixins:
        assert mixin in server_class.__mro__, mixin
    # No handler is defined twice in the composed class's MRO.
    seen = {}
    for klass in server_class.__mro__:
        for name in klass.__dict__:
            if name.startswith("__"):
                continue
            seen.setdefault(name, []).append(klass.__name__)
    duplicates = {name: owners for name, owners in seen.items()
                  if len(owners) > 1 and name not in ("ping_response",)}
    assert duplicates == {}, duplicates


def test_ping_handler_returns_pong_without_extra_parameters():
    response = server_module.DSHBlenderBridgeServer.ping_response(object(), unexpected=True)
    assert response == {"pong": True}
