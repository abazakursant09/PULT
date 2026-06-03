"""Binding attestation (Sprint 80).

attest_binding(raw_events) seals a RuntimeBinding from a raw UserEvent stream.
verify_binding(att) recomputes the binding from the attested raw stream and
confirms byte-identical hashes. Returns only VALID or INVALID. Fail-closed: a raw
stream that cannot be canonicalized makes verification INVALID.
"""
from __future__ import annotations

from dataclasses import dataclass

from .binding_adapter import build_runtime_binding
from .runtime_binding_contract import RuntimeBinding
from .runtime_binding_boundary import BindingViolation

VALID = "VALID"
INVALID = "INVALID"


@dataclass(frozen=True)
class BindingAttestation:
    raw_events: tuple
    binding: RuntimeBinding


def attest_binding(raw_events) -> BindingAttestation:
    raw = tuple(raw_events)
    return BindingAttestation(raw_events=raw, binding=build_runtime_binding(raw))


def verify_binding(att: BindingAttestation) -> str:
    try:
        rebuilt = build_runtime_binding(att.raw_events)
    except BindingViolation:
        return INVALID
    b = att.binding
    if (rebuilt.runtime_binding_hash == b.runtime_binding_hash
            and rebuilt.runtime_application_hash == b.runtime_application_hash
            and rebuilt.replay_chain_hash == b.replay_chain_hash
            and rebuilt.operational_review_hash == b.operational_review_hash
            and rebuilt.canonical_events == b.canonical_events):
        return VALID
    return INVALID
