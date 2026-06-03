"""Runtime binding hash (Sprint 80).

Deterministic SHA-256 over the canonical event stream + adapter version, domain-
separated. Reuses the frozen Runtime Envelope canonical hashing. No timestamps,
randomness, uuid, env, process state.
"""
from __future__ import annotations

from runtime_envelope.envelope_hash import domain_hash

from .runtime_binding_contract import BINDING_DOMAIN, ADAPTER_VERSION


def runtime_binding_hash(canonical_events) -> str:
    return domain_hash(BINDING_DOMAIN, {
        "adapter_version": ADAPTER_VERSION,
        "canonical_event_stream": [dict(e) for e in canonical_events],
    })
