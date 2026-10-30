"""Integration contract matrix for the 39 add-on commands.

One row per command: request params, gate, pause semantics, handler source,
catalog entry, and the flat success/error envelope the dispatch path returns.
The rows are executed against the real DSHBlenderBridgeServer dispatch code
(handlers are replaced with recorders so no network or Blender data is touched),
and cross-checked against runtime/catalog.mjs COMMAND_CATALOG.

Set DSHBB_COMMAND_MATRIX_OUT to a markdown path to also write the T10 evidence
matrix (a sibling .json gets the machine-readable rows).
"""

from __future__ import annotations

import importlib
import json
import os
import re
from pathlib import Path
from types import SimpleNamespace

import pytest

import bpy_stub

bpy = bpy_stub.install()

server_module = importlib.import_module("addon.dsh_blender_bridge.server")
protocol = importlib.import_module("addon.dsh_blender_bridge.protocol")
generate_premium = importlib.import_module("addon.dsh_blender_bridge.commands.generate_premium")

ROOT = Path(__file__).resolve().parents[3]
CATALOG_PATH = ROOT / "runtime" / "catalog.mjs"
OFF_CATALOG = {"get_polyhaven_asset_preview"}
GATE_PROP = {
    "polyhaven": "dshbb_use_polyhaven",
    "hyper3d": "dshbb_use_hyper3d",
    "sketchfab": "dshbb_use_sketchfab",
    "polypizza": "dshbb_use_polypizza",
    "hunyuan": "dshbb_use_hunyuan3d",
}

# Sample params: every command is called with exactly these keyword arguments so
# the matrix proves params -> handler kwargs forwarding, not just name lookup.
PARAMS = {
    "ping": {},
    "get_scene_info": {},
    "get_world_state_snapshot": {},
    "get_addon_info": {},
    "get_object_info": {"name": "Cube"},
    "get_viewport_screenshot": {"max_size": 320, "format": "png"},
    "execute_code": {"code": "1 + 1"},
    "describe_node_type": {"bl_idname": "ShaderNodeTexSky"},
    "bpy_api_lookup": {"query": "bpy.ops.mesh.primitive_cube_add"},
    "drain_human_activity": {},
    "get_activity_state": {},
    "get_selection": {},
    "send_selection_to_dsh": {"timeout": 1.0},
    "get_collections": {"limit": 5},
    "send_collection_to_dsh": {"collection": "SetA", "timeout": 1.0},
    "get_telemetry_consent": {},
    "set_telemetry_consent": {"consent": True},
    "get_polyhaven_status": {},
    "get_hyper3d_status": {},
    "get_sketchfab_status": {},
    "get_polypizza_status": {},
    "get_hunyuan3d_status": {},
    "get_tripo_status": {},
    "export_scene": {"filepath": "/tmp/out.glb", "format": "glb"},
    "get_polyhaven_categories": {"asset_type": "models"},
    "search_polyhaven_assets": {"asset_type": "models"},
    "download_polyhaven_asset": {"asset_id": "asset-1", "asset_type": "models"},
    "get_polyhaven_asset_preview": {"asset_id": "asset-1"},
    "set_texture": {"object_name": "Cube", "texture_id": "tex-1"},
    "create_rodin_job": {"text_prompt": "a cube"},
    "poll_rodin_job_status": {"subscription_key": "sub-1"},
    "import_generated_asset": {},
    "search_sketchfab_models": {"query": "chair"},
    "get_sketchfab_model_preview": {"uid": "uid-1"},
    "download_sketchfab_model": {"uid": "uid-1"},
    "search_polypizza_models": {"query": "chair"},
    "download_polypizza_model": {"model_id": "model-1"},
    "create_hunyuan_job": {"text_prompt": "a cube"},
    "poll_hunyuan_job_status": {"job_id": "job-1"},
    "import_generated_asset_hunyuan": {},
}


def _load_catalog():
    source = CATALOG_PATH.read_text(encoding="utf-8")
    start = source.index("export const COMMAND_CATALOG = {")
    block = source[start:source.index("\n};", start)]
    entries = {}
    for match in re.finditer(r"^\s{2}([a-z0-9_]+):\s*\{([^}]*)\},?\s*$", block, re.MULTILINE):
        name, body = match.group(1), match.group(2)
        description = re.search(r"d:\s*'([^']*)'", body)
        gate = re.search(r"gate:\s*(null|'([a-z0-9_]+)')", body)
        assert description and gate, name
        entries[name] = {
            "description": description.group(1),
            "gate": gate.group(2),
        }
    return entries


CATALOG = _load_catalog()
GATE_BY_NAME = {}
for group, names in protocol.COMMAND_GROUPS:
    for name in names:
        GATE_BY_NAME[name] = None if group == "base" else GATE_PROP[group]


def _handler_source(name):
    if name == "get_tripo_status":
        function = generate_premium.premium_tripo_status
        return function.__module__ + "." + function.__qualname__
    function = getattr(server_module.DSHBlenderBridgeServer, name, None)
    if function is None:
        return None
    return function.__module__ + "." + function.__qualname__


MATRIX = [
    {
        "index": index,
        "command": name,
        "params": PARAMS[name],
        "gate": GATE_BY_NAME[name],
        "pause_safe": protocol._is_pause_safe_command(name),
        "catalog": "catalog" if name in CATALOG else "off-catalog",
        "handler": _handler_source(name),
        "envelope": "flat success {status:success,result} / error {status:error,message}",
    }
    for index, name in enumerate(protocol.COMMAND_NAMES, start=1)
]
MATRIX_BY_NAME = {row["command"]: row for row in MATRIX}


def _set_toggles(value):
    for prop in GATE_PROP.values():
        setattr(bpy.context.scene, prop, value)


def _patched_server(monkeypatch):
    monkeypatch.delenv("DSHBB_PREMIUM_LICENSE_KEY", raising=False)
    monkeypatch.delenv("BLENDERMCP_PREMIUM_LICENSE_KEY", raising=False)
    monkeypatch.setattr(server_module, "premium_tripo_status",
                        lambda: {"handled": "get_tripo_status"})
    server = server_module.DSHBlenderBridgeServer(port=0)

    def handler(name):
        def call(**kwargs):
            return {"called": name, "kwargs": kwargs}

        call.__name__ = "handler_" + name
        return call

    for row in MATRIX:
        if row["command"] == "ping":
            continue
        if row["command"] == "get_tripo_status":
            monkeypatch.setattr(server_module, "premium_tripo_status",
                                handler("get_tripo_status"))
        else:
            setattr(server, row["command"], handler(row["command"]))
    return server


@pytest.fixture(scope="session", autouse=True)
def emit_matrix_artifact():
    yield
    target = os.environ.get("DSHBB_COMMAND_MATRIX_OUT")
    if not target:
        return
    path = Path(target)
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = [
        "# T10 add-on command matrix (39 commands)",
        "",
        "Generated by tests/integration/python/test_addon_command_matrix.py from the real",
        "protocol registry, dispatch table and runtime/catalog.mjs COMMAND_CATALOG.",
        "Envelope: success is {status: 'success', result: <handler result>}; every failure is",
        "{status: 'error', message: ...} and the pause gate adds error='AI_PAUSED'.",
        "",
        "| # | command | params | gate | pause-safe | catalog | handler |",
        "|---|---|---|---|---|---|---|",
    ]
    for row in MATRIX:
        params = ", ".join("%s=%r" % item for item in sorted(row["params"].items())) or "-"
        lines.append("| {index} | {command} | {params} | {gate} | {pause} | {catalog} | {handler} |".format(
            index=row["index"], command=row["command"], params=params,
            gate=row["gate"] or "-", pause="yes" if row["pause_safe"] else "no",
            catalog=row["catalog"], handler=row["handler"],
        ))
    lines.append("")
    path.write_text("\n".join(lines), encoding="utf-8")
    path.with_suffix(".json").write_text(
        json.dumps({"commands": MATRIX, "catalog": CATALOG}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def test_matrix_covers_the_39_protocol_commands_in_order():
    assert len(MATRIX) == len(protocol.COMMAND_NAMES) == 39
    assert [row["command"] for row in MATRIX] == list(protocol.COMMAND_NAMES)
    assert len({row["command"] for row in MATRIX}) == 39
    assert set(PARAMS) == set(protocol.COMMAND_NAMES) | {"ping"}


def test_matrix_handlers_are_real_and_unique_sources():
    assert all(row["handler"] for row in MATRIX)
    assert all("addon.dsh_blender_bridge." in row["handler"] for row in MATRIX)
    unique = {row["handler"] for row in MATRIX}
    assert len(unique) == 39
    assert "addon.dsh_blender_bridge.commands.generate_premium.premium_tripo_status" in unique


def test_matrix_gate_and_pause_columns_match_the_registry():
    for row in MATRIX:
        assert row["gate"] == GATE_BY_NAME[row["command"]]
        assert row["pause_safe"] is protocol._is_pause_safe_command(row["command"])
        if row["command"].startswith("get_") or row["command"] in protocol.PAUSE_SAFE_COMMANDS:
            assert row["pause_safe"] is True
        else:
            assert row["pause_safe"] is False, row["command"]
    base = [row for row in MATRIX if row["gate"] is None]
    assert len(base) == 23


def test_matrix_cross_checks_runtime_catalog():
    matrix_names = set(MATRIX_BY_NAME)
    catalog_names = set(CATALOG)
    assert len(catalog_names) == 39
    assert catalog_names == (matrix_names | {"ping"}) - OFF_CATALOG
    assert OFF_CATALOG == {"get_polyhaven_asset_preview"}
    assert matrix_names - catalog_names == OFF_CATALOG
    assert catalog_names - matrix_names == {"ping"}
    for name, entry in CATALOG.items():
        assert entry["description"].strip(), name
        if name in MATRIX_BY_NAME:
            assert entry["gate"] == MATRIX_BY_NAME[name]["gate"], name
        else:
            assert name == "ping" and entry["gate"] is None
    for name in OFF_CATALOG:
        assert MATRIX_BY_NAME[name]["catalog"] == "off-catalog"
        assert MATRIX_BY_NAME[name]["gate"] == "dshbb_use_polyhaven"


@pytest.mark.parametrize("row", MATRIX, ids=[row["command"] for row in MATRIX])
def test_command_forwards_params_and_returns_the_flat_success_envelope(row, monkeypatch):
    server = _patched_server(monkeypatch)
    _set_toggles(True)
    response = server.execute_command({"type": row["command"], "params": row["params"]})
    if row["command"] == "ping":
        assert response == {"status": "success", "result": {"pong": True}}
        return
    assert response == {"status": "success",
                        "result": {"called": row["command"], "kwargs": row["params"]}}
    assert set(response) == {"status", "result"}
    assert json.loads(json.dumps(response)) == response


@pytest.mark.parametrize(
    "row",
    [row for row in MATRIX if row["command"] != "ping"],
    ids=[row["command"] for row in MATRIX if row["command"] != "ping"],
)
def test_command_error_is_a_flat_error_envelope(row, monkeypatch):
    server = _patched_server(monkeypatch)
    _set_toggles(True)

    def boom(**kwargs):
        raise RuntimeError("boom-" + row["command"])

    if row["command"] == "get_tripo_status":
        monkeypatch.setattr(server_module, "premium_tripo_status", boom)
    else:
        setattr(server, row["command"], boom)
    response = server.execute_command({"type": row["command"], "params": {}})
    assert response == {"status": "error", "message": "boom-" + row["command"]}
    assert "result" not in response
    assert json.loads(json.dumps(response)) == response


@pytest.mark.parametrize("row", MATRIX, ids=[row["command"] for row in MATRIX])
def test_pause_semantics_match_the_matrix(row, monkeypatch):
    server = _patched_server(monkeypatch)
    _set_toggles(True)
    bpy.context.scene.dshbb_ai_paused = True
    try:
        response = server.execute_command({"type": row["command"], "params": row["params"]})
    finally:
        bpy.context.scene.dshbb_ai_paused = False
    if row["pause_safe"]:
        assert response["status"] == "success", (row["command"], response)
    else:
        assert response == {
            "status": "error",
            "error": "AI_PAUSED",
            "message": "AI 操作已暂停：请在 Blender N 面板「活动状态」里点「继续 AI 操作」",
        }
        assert "result" not in response


@pytest.mark.parametrize(
    "row",
    [row for row in MATRIX if row["gate"]],
    ids=[row["command"] for row in MATRIX if row["gate"]],
)
def test_gated_commands_are_unknown_while_their_switch_is_off(row, monkeypatch):
    server = _patched_server(monkeypatch)
    _set_toggles(False)
    response = server.execute_command({"type": row["command"], "params": row["params"]})
    assert response == {"status": "error",
                        "message": "Unknown command type: " + row["command"]}


def test_base_commands_dispatch_with_every_gate_off(monkeypatch):
    server = _patched_server(monkeypatch)
    _set_toggles(False)
    for row in MATRIX:
        if row["gate"] is None and row["command"] != "ping":
            response = server.execute_command({"type": row["command"], "params": row["params"]})
            assert response["status"] == "success", (row["command"], response)
