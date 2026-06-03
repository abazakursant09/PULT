"""Cognition binding attestation (Sprint 83).

attest(insights) seals a CognitionBinding from cognition output.
verify_binding(att) recomputes the binding from the attested canonical projection
and confirms byte-identical hashes. Returns only VALID or INVALID. Fail-closed.
"""
from __future__ import annotations

from dataclasses import dataclass

from .cognition_binding_adapter import CognitionBinding, observe, build_from_projection

VALID = "VALID"
INVALID = "INVALID"


@dataclass(frozen=True)
class CognitionAttestation:
    canonical_projection: tuple
    binding: CognitionBinding


def attest(insights) -> CognitionAttestation:
    binding = observe(insights)
    return CognitionAttestation(canonical_projection=binding.canonical_projection, binding=binding)


def verify_binding(att: CognitionAttestation) -> str:
    try:
        rebuilt = build_from_projection(att.canonical_projection)
    except Exception:
        return INVALID
    b = att.binding
    if (rebuilt.cognition_binding_hash == b.cognition_binding_hash
            and rebuilt.runtime_application_hash == b.runtime_application_hash
            and rebuilt.replay_chain_hash == b.replay_chain_hash
            and rebuilt.operational_review_hash == b.operational_review_hash
            and rebuilt.canonical_projection == b.canonical_projection):
        return VALID
    return INVALID
