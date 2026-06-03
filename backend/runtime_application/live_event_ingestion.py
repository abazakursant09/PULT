"""Live event ingestion (Post-closure directive).

Normalizes external runtime events into a canonical, append-only runtime stream
with deterministic ordering. Ingestion order IS the caller-provided order (no
clocks, no sorting by time) — the assigned ordinal is the append index.

Fail-closed: malformed events or events carrying non-deterministic fields are
refused (never silently dropped or coerced).
"""
from __future__ import annotations

from dataclasses import dataclass

from runtime_envelope.replay_boundary import assert_replay_safe, ReplayBoundaryViolation

from .runtime_application_boundary import fail_closed

# Canonical event fields. Anything else is refused (fail-closed).
ALLOWED_FIELDS = ("event_type", "entity", "weight", "marketplace")
REQUIRED_FIELDS = ("event_type", "entity")


def normalize_event(raw: dict, ordinal: int) -> dict:
    """Normalize one raw event into the canonical, replay-safe form."""
    if not isinstance(raw, dict):
        raise fail_closed(f"event #{ordinal} is not a dict")
    for field in REQUIRED_FIELDS:
        if not raw.get(field):
            raise fail_closed(f"event #{ordinal} missing required field '{field}'")
    extra = set(raw) - set(ALLOWED_FIELDS)
    if extra:
        raise fail_closed(f"event #{ordinal} has unsupported fields {sorted(extra)}")
    try:
        assert_replay_safe(raw)
    except ReplayBoundaryViolation as exc:
        raise fail_closed(f"event #{ordinal}: {exc}")

    weight = raw.get("weight", 0)
    if not isinstance(weight, int) or isinstance(weight, bool):
        raise fail_closed(f"event #{ordinal} weight must be int")

    return {
        "ordinal": ordinal,
        "event_type": str(raw["event_type"]),
        "entity": str(raw["entity"]),
        "weight": weight,
        "marketplace": str(raw.get("marketplace", "")),
    }


@dataclass(frozen=True)
class RuntimeStream:
    """Append-only, immutable runtime event stream."""
    events: tuple

    @property
    def length(self) -> int:
        return len(self.events)

    def append(self, raw_event: dict) -> "RuntimeStream":
        """Return a NEW stream with one event appended (append-only lineage)."""
        return RuntimeStream(self.events + (normalize_event(raw_event, len(self.events)),))


def ingest(raw_events) -> RuntimeStream:
    """Build a RuntimeStream from an ordered iterable of raw events."""
    normalized = tuple(normalize_event(ev, i) for i, ev in enumerate(raw_events))
    return RuntimeStream(events=normalized)
