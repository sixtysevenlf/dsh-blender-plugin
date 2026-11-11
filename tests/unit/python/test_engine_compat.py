"""Regression tests for the EEVEE engine identifier across Blender versions.

Blender 4.2-4.9 renamed BLENDER_EEVEE to BLENDER_EEVEE_NEXT; Blender 5.0 changed
the identifier back to BLENDER_EEVEE (release notes 5.0). The headless worker,
the perf helper and the GPU prelude previously hard-coded one name, so EEVEE
raytracing/samples were silently skipped on the other segment. These tests pin
the bounded, version-aware selection at each call site.
"""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
CALL_SITES = (
    "runtime/py/core/worker.py",
    "runtime/py/core/perf.py",
    "runtime/paths.ts",
)
BOUNDED_EXPRESSION = "(4, 2, 0) <= bpy.app.version < (5, 0, 0)"


def _source(relative):
    return (ROOT / relative).read_text(encoding="utf-8")


def test_call_sites_do_not_assign_a_single_engine_identifier():
    for relative in CALL_SITES:
        for line in _source(relative).splitlines():
            if "sc.render.engine" not in line or "BLENDER_EEVEE" not in line:
                continue
            stripped = line.strip()
            assert stripped not in (
                'sc.render.engine = "BLENDER_EEVEE"',
                'sc.render.engine = "BLENDER_EEVEE_NEXT"',
            ), (relative, line)
            assert "if" in line and "else" in line, (relative, line)


def test_call_sites_cover_both_identifier_eras():
    for relative in CALL_SITES:
        text = _source(relative)
        assert "BLENDER_EEVEE_NEXT" in text, relative
        assert "BLENDER_EEVEE" in text, relative
        assert "bpy.app.version" in text, relative
        assert BOUNDED_EXPRESSION in text, relative


def _engine_identifier(version):
    return "BLENDER_EEVEE_NEXT" if (4, 2, 0) <= version < (5, 0, 0) else "BLENDER_EEVEE"


def test_selection_boundaries():
    assert _engine_identifier((4, 1, 9)) == "BLENDER_EEVEE"
    assert _engine_identifier((4, 2, 0)) == "BLENDER_EEVEE_NEXT"
    assert _engine_identifier((4, 5, 2)) == "BLENDER_EEVEE_NEXT"
    assert _engine_identifier((4, 9, 9)) == "BLENDER_EEVEE_NEXT"
    assert _engine_identifier((5, 0, 0)) == "BLENDER_EEVEE"
    assert _engine_identifier((5, 3, 0)) == "BLENDER_EEVEE"
