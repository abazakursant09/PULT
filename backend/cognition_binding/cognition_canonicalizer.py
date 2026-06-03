"""Cognition canonicalizer (Sprint 83).

Turns a raw cognition projection into a deterministic canonical form:
- strips uuid-derived tokens from the entity and all string values
- drops temporal metadata (no timestamp fields survive the projection allowlist)
- normalizes set-like list (marketplace_patterns) ordering
- normalizes the top-level insight ordering (canonical sort)

Goal: same logical cognition output -> byte-identical canonical projection,
regardless of structure ordering or embedded uuids.
"""
from __future__ import annotations

from runtime_binding import strip_uuid

from .cognition_binding_boundary import CognitionBindingViolation

_SET_LIKE = ("marketplace_patterns",)


def canonicalize_projection(projection: list) -> tuple:
    """Canonicalize + deterministically order a list of projected insights."""
    canon = []
    for item in projection:
        if not isinstance(item, dict) or not item.get("key"):
            raise CognitionBindingViolation("non-canonical projection item")
        entity = strip_uuid(str(item["key"]))
        if not entity:
            raise CognitionBindingViolation("entity empty after canonicalization")
        rec = {
            "category": item["category"],
            "entity": entity,
        }
        for k, v in item.items():
            if k in ("key",):
                continue
            if isinstance(v, str):
                rec[k] = strip_uuid(v)
            elif isinstance(v, list):
                vals = [strip_uuid(str(x)) for x in v]
                rec[k] = sorted(vals) if k in _SET_LIKE else vals
            else:
                rec[k] = v
        canon.append(rec)
    canon.sort(key=lambda r: (r.get("category", ""), r.get("entity", ""),
                              str(r.get("type", "")), str(r.get("status", ""))))
    return tuple(canon)


def projection_to_events(canonical: tuple) -> tuple:
    """Map the canonical cognition projection to substrate event records
    ({event_type, entity, weight, marketplace}) — the shape the substrate accepts."""
    events = []
    for rec in canonical:
        weight = rec.get("impact_score")
        weight = int(weight) if isinstance(weight, int) else 0
        events.append({
            "event_type": str(rec.get("category", "")),
            "entity": rec["entity"],
            "weight": weight,
            "marketplace": str(rec.get("marketplace") or ""),
        })
    return tuple(events)
