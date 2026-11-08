"""Unit checks for the Python side of the availability envelope contract."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
ADDON = ROOT / "addon" / "dsh_blender_bridge"
sys.path.insert(0, str(ADDON))


def _load(name: str):
    spec = importlib.util.spec_from_file_location(name, ADDON / (name + ".py"))
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


schema = _load("availability_schema")
availability = _load("availability")


def test_complete_envelope_is_valid():
    envelope = availability.availability_failure(
        "config",
        root_cause="Blender executable is not configured",
        fix="set DSH_BLENDER_EXE",
        fallback={"kind": "unavailable", "summary": "no GUI session can start"},
        evidence={"field": "blenderExe"},
    )
    assert envelope["ok"] is False
    assert availability.validate_availability_envelope(envelope) == []


def test_missing_field_and_unknown_stage_are_rejected():
    envelope = availability.availability_failure(
        "config", "root", "fix", "unavailable fallback"
    )
    del envelope["fix"]
    assert availability.validate_availability_envelope(envelope) == [
        "fix must be a non-empty string"
    ]

    unknown = availability.availability_failure("nope", "root", "fix", "unavailable fallback")
    assert "stage is not in config/availability.json: nope" in (
        availability.validate_availability_envelope(unknown)
    )


def test_schema_matches_the_generated_constants():
    assert schema.AVAILABILITY_STAGES == (
        "config",
        "session",
        "spawn",
        "transport",
        "handshake",
        "addon",
        "bpy",
        "queue",
        "render",
    )
    assert schema.AVAILABILITY_BUDGETS_MS["config"] == 100
    assert schema.AVAILABILITY_BUDGETS_MS["render"] == 3000
