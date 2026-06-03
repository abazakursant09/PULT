"""Cognition binding hash (Sprint 83).

Deterministic SHA-256 over the canonical cognition projection + adapter version,
domain-separated. Reuses the frozen Runtime Envelope canonical hashing. No
clocks, randomness, uuid, env, process state.
"""
from __future__ import annotations

from runtime_envelope.envelope_hash import domain_hash

ADAPTER_VERSION = "cognition-binding/1"
COGNITION_BINDING_DOMAIN = "PULT-COGNITION-BINDING/1"


def cognition_binding_hash(canonical_projection) -> str:
    return domain_hash(COGNITION_BINDING_DOMAIN, {
        "adapter_version": ADAPTER_VERSION,
        "canonical_cognition_projection": [dict(r) for r in canonical_projection],
    })
