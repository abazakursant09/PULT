"""Cognition projection (Sprint 83).

Projects each cognition InsightItem into a fixed allowlist of descriptive fields.
Identity fields (id / record_id / chain_id — uuid-derived) and nested objects
that may carry ids/params are dropped. Read-only; never touches the insight.
"""
from __future__ import annotations

from .cognition_binding_boundary import CognitionBindingViolation

# Scalar descriptive fields kept from each InsightItem.
_SCALAR_FIELDS = (
    "type", "status", "confidence", "confidence_level", "impact_score",
    "marketplace", "is_demo", "is_secondary", "signal_state",
    "resolution_difficulty", "intervention_tier", "automation_level",
)
# String-list descriptive fields (order preserved; values uuid-stripped later).
_LIST_FIELDS = ("reasons", "recommendations", "marketplace_patterns")

# Explicitly dropped uuid-derived identifiers.
_DROPPED_IDS = ("id", "record_id", "chain_id")


def _get(ins, field):
    if isinstance(ins, dict):
        return ins.get(field)
    return getattr(ins, field, None)


def _category(key: str) -> str:
    base = key.split(":", 1)[0]
    return base[5:] if base.startswith("demo_") else base


def project_insight(ins) -> dict:
    """Project one InsightItem into a descriptive dict. Fail-closed on no key."""
    key = _get(ins, "key")
    if not key:
        raise CognitionBindingViolation("insight missing 'key'")
    out = {"category": _category(str(key)), "key": str(key)}
    for f in _SCALAR_FIELDS:
        out[f] = _get(ins, f)
    for f in _LIST_FIELDS:
        val = _get(ins, f) or []
        out[f] = [str(x) for x in val]
    return out


def project_insights(insights) -> list:
    return [project_insight(i) for i in insights]
