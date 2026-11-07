"""Shared availability failure envelope (spec: every failure answers four questions).

config/availability.json is the single source for the stage enum, the required
fields, the fallback kinds and the diagnostic budgets; availability_schema.py is
generated from it. The Gateway is TypeScript, but the add-on and Python-side
dispatch also need to emit and validate the same shape.
"""

from __future__ import annotations

from typing import Any, Mapping

try:  # Blender add-on package import
    from .availability_schema import (
        AVAILABILITY_BUDGETS_MS,
        AVAILABILITY_FALLBACK_KINDS,
        AVAILABILITY_REQUIRED_FIELDS,
        AVAILABILITY_STAGES,
    )
except ImportError:  # loaded straight from disk by the unit tests
    from availability_schema import (  # type: ignore[no-redef]
        AVAILABILITY_BUDGETS_MS,
        AVAILABILITY_FALLBACK_KINDS,
        AVAILABILITY_REQUIRED_FIELDS,
        AVAILABILITY_STAGES,
    )


def _text(value: Any) -> str:
    return "" if value is None else str(value)


def availability_failure(
    stage: str,
    root_cause: str,
    fix: str,
    fallback: str | Mapping[str, Any],
    evidence: Mapping[str, Any] | None = None,
    code: str = "",
) -> dict[str, Any]:
    """Build the canonical failure envelope; callers may add evidence, not drop fields."""
    if isinstance(fallback, str):
        fallback_kind = AVAILABILITY_FALLBACK_KINDS[0]
        fallback_summary = fallback
    else:
        fallback_kind = _text(fallback.get("kind")) or AVAILABILITY_FALLBACK_KINDS[0]
        fallback_summary = _text(fallback.get("summary"))
    return {
        "ok": False,
        "code": _text(code) or f"{stage}_failure",
        "stage": stage,
        "root_cause": _text(root_cause),
        "fix": _text(fix),
        "fallback": {"kind": fallback_kind, "summary": fallback_summary},
        "evidence": dict(evidence or {}),
    }


def validate_availability_envelope(value: Any) -> list[str]:
    """Return every contract violation; an empty list means the envelope is valid."""
    if not isinstance(value, dict):
        return ["envelope must be an object"]
    violations: list[str] = []
    stage = value.get("stage")
    if not isinstance(stage, str) or not stage.strip():
        violations.append("stage is missing")
    elif stage not in AVAILABILITY_STAGES:
        violations.append(f"stage is not in config/availability.json: {stage}")
    for field in AVAILABILITY_REQUIRED_FIELDS:
        if field in ("fallback", "evidence"):
            continue
        candidate = value.get(field)
        if not isinstance(candidate, str) or not candidate.strip():
            violations.append(f"{field} must be a non-empty string")
    fallback = value.get("fallback")
    if not isinstance(fallback, dict):
        violations.append("fallback must be an object")
    else:
        kind = fallback.get("kind")
        if not isinstance(kind, str) or not kind.strip():
            violations.append("fallback.kind is missing")
        elif kind not in AVAILABILITY_FALLBACK_KINDS:
            violations.append(f"fallback.kind is not in config/availability.json: {kind}")
        summary = fallback.get("summary")
        if not isinstance(summary, str) or not summary.strip():
            violations.append("fallback.summary is missing")
    if not isinstance(value.get("evidence"), dict):
        violations.append("evidence must be an object")
    return violations


def availability_budget_ms(stage: str) -> int:
    return int(AVAILABILITY_BUDGETS_MS[stage])
