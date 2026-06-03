"""Event canonicalizer (Sprint 80).

Transforms live UserEvent records into deterministic constitutional events:
- drops timestamps (created_at) and process/identity fields (id, user_id)
- strips uuid / long-hex tokens from entity identifiers
- extracts weight + marketplace from metadata (ignoring all volatile metadata)
- normalizes ordering to a canonical sort

Fail-closed: unknown fields, missing required fields, invalid metadata, or
non-canonical structures raise BindingViolation. Never repairs silently.

Identical logical event streams -> byte-identical canonical output.
"""
from __future__ import annotations

import json
import re

from .runtime_binding_boundary import BindingViolation
from .runtime_binding_contract import (
    ALLOWED_USEREVENT_FIELDS, REQUIRED_USEREVENT_FIELDS,
)

# uuid (with/without dashes) and bare long-hex tokens are non-deterministic ids.
_UUID_RE = re.compile(
    r"[0-9a-fA-F]{8}-?[0-9a-fA-F]{4}-?[0-9a-fA-F]{4}-?[0-9a-fA-F]{4}-?[0-9a-fA-F]{12}")
_HEX_RE = re.compile(r"[0-9a-fA-F]{16,}")


def _get(raw, field):
    if isinstance(raw, dict):
        return raw.get(field)
    return getattr(raw, field, None)


def _present_fields(raw) -> set:
    if isinstance(raw, dict):
        return set(raw.keys())
    return {f for f in ALLOWED_USEREVENT_FIELDS if hasattr(raw, f)}


def strip_uuid(text: str) -> str:
    """Remove uuid / long-hex tokens; collapse leftover separator artifacts."""
    out = _UUID_RE.sub("", text)
    out = _HEX_RE.sub("", out)
    parts = [seg.strip("-_") for seg in out.split(":")]
    return ":".join(p for p in parts if p)


def _extract_metadata(raw) -> dict:
    meta_raw = _get(raw, "metadata_json")
    if meta_raw is None:
        return {}
    if isinstance(meta_raw, dict):
        meta = meta_raw
    elif isinstance(meta_raw, str):
        try:
            meta = json.loads(meta_raw)
        except (ValueError, TypeError):
            raise BindingViolation("invalid metadata_json")
    else:
        raise BindingViolation("metadata_json must be JSON object or null")
    if not isinstance(meta, dict):
        raise BindingViolation("metadata_json must decode to an object")
    return meta


def canonicalize_event(raw) -> dict:
    """Canonicalize one UserEvent record into a constitutional event. Fail-closed."""
    if not isinstance(raw, dict) and not hasattr(raw, "event_type"):
        raise BindingViolation("event is neither dict nor UserEvent-like")

    # unknown-field rejection (dict inputs)
    if isinstance(raw, dict):
        extra = set(raw) - set(ALLOWED_USEREVENT_FIELDS)
        if extra:
            raise BindingViolation(f"unknown fields {sorted(extra)}")

    for field in REQUIRED_USEREVENT_FIELDS:
        if not _get(raw, field):
            raise BindingViolation(f"missing required field '{field}'")

    meta = _extract_metadata(raw)
    weight = meta.get("weight", 0)
    if not isinstance(weight, int) or isinstance(weight, bool):
        raise BindingViolation("metadata weight must be int")
    marketplace = meta.get("marketplace", "")
    if not isinstance(marketplace, str):
        raise BindingViolation("metadata marketplace must be str")

    entity = strip_uuid(str(_get(raw, "entity_id")))
    if not entity:
        raise BindingViolation("entity_id empty after canonicalization")

    return {
        "event_type": str(_get(raw, "event_type")),
        "entity": entity,
        "weight": weight,
        "marketplace": marketplace,
    }


def canonicalize_stream(raw_events) -> tuple:
    """Canonicalize + deterministically order a UserEvent stream.

    Ordering is normalized (canonical sort) so identical logical streams produce
    byte-identical output regardless of arrival order or timestamps.
    """
    canon = [canonicalize_event(ev) for ev in raw_events]
    canon.sort(key=lambda e: (e["event_type"], e["entity"], e["weight"], e["marketplace"]))
    return tuple(canon)
