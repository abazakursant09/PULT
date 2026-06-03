"""Live binding activation (Sprint 81).

Turns the runtime_binding adapter into an automatic runtime participant: when a
UserEvent enters the system, the events ingestion path calls
`activate_from_track`, which canonicalizes the single event and produces a
deterministic runtime_binding_hash — no manual invocation.

Read-only with respect to the substrate and the database. Fail-closed: a
non-canonicalizable event yields no binding (returns None) and is never repaired;
activation never raises into the ingestion path.

The activation ledger is a process-local, append-only record of produced binding
hashes. It is NOT part of any constitutional hash — it only evidences that
activation occurred.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from .binding_adapter import build_runtime_binding
from .runtime_binding_boundary import BindingViolation


@dataclass
class _ActivationLedger:
    entries: list = field(default_factory=list)  # list[(sequence, binding_hash)]

    def record(self, binding_hash: str) -> int:
        seq = len(self.entries)
        self.entries.append((seq, binding_hash))
        return seq

    @property
    def count(self) -> int:
        return len(self.entries)

    def last(self):
        return self.entries[-1] if self.entries else None

    def reset(self) -> None:
        self.entries.clear()


ACTIVATION_LEDGER = _ActivationLedger()


def bind_single_event(event_type, entity_id, metadata) -> str:
    """Build a deterministic binding for one UserEvent. Raises BindingViolation
    (fail-closed) on non-canonical input."""
    raw = {"event_type": event_type, "entity_id": entity_id, "metadata_json": metadata}
    return build_runtime_binding([raw]).runtime_binding_hash


def activate_from_track(event_type, entity_id, metadata) -> str | None:
    """Automatic activation hook for the events ingestion path.

    Returns the produced binding hash, or None if the event is not
    canonicalizable (fail-closed — no binding, no repair). Never raises.
    """
    try:
        binding_hash = bind_single_event(event_type, entity_id, metadata)
    except BindingViolation:
        return None
    except Exception:  # ingestion must never break; activation is best-effort downstream
        return None
    ACTIVATION_LEDGER.record(binding_hash)
    return binding_hash


def last_binding_hash():
    last = ACTIVATION_LEDGER.last()
    return last[1] if last else None


def activation_count() -> int:
    return ACTIVATION_LEDGER.count
