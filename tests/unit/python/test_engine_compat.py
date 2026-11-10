"""Regression tests for the EEVEE engine identifier rename (Blender 4.2+).

Blender 4.2 renamed ``BLENDER_EEVEE`` to ``BLENDER_EEVEE_NEXT``. The headless
worker, the perf helper and the GPU prelude all assigned the retired name and
swallowed the TypeError, so EEVEE raytracing/samples were never applied on
4.2+ (reproduced on Blender 4.5.2 LTS). These tests pin the version-aware
selection at each call site so the literal does not come back.
"""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
CALL_SITES = (
    "runtime/py/core/worker.py",
    "runtime/py/core/perf.py",
    "runtime/paths.ts",
)


def _source(relative):
    return (ROOT / relative).read_text(encoding="utf-8")


def test_call_sites_do_not_assign_the_retired_engine_identifier():
    for relative in CALL_SITES:
        assert 'sc.render.engine = "BLENDER_EEVEE"' not in _source(relative), relative


def test_call_sites_select_the_identifier_by_blender_version():
    for relative in CALL_SITES:
        text = _source(relative)
        assert "BLENDER_EEVEE_NEXT" in text, relative
        assert "bpy.app.version" in text, relative
