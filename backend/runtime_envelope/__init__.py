"""Runtime Envelope (Sprint 73).

Immutable, deterministic identity container sitting ABOVE the Runtime Signal
Collector and BELOW Operator Cognition V2. Produces a byte-identical
runtime_envelope_hash across replays — independent of clocks, environment,
process state, and randomness.

Public API:
    build_runtime_envelope(...)  -> RuntimeEnvelope
    verify_attestation(envelope) -> bool
    derive_default_components()  -> EnvelopeComponents   (binds to repo anchors)
"""
from __future__ import annotations

from .envelope_contract import (
    CONTRACT_VERSION, ENVELOPE_FIELDS, INPUT_COMPONENTS,
    EnvelopeComponents, RuntimeEnvelope,
)
from .envelope_hash import canonical_bytes, sha256_hex, domain_hash
from .boot_identity import compute_boot_id
from .runtime_session import RuntimeSession, build_session, compute_session_id
from .envelope_topology import TOPOLOGY, TOPOLOGY_EDGES, topology_attestation
from .replay_boundary import (
    REPLAY_SCOPE, NON_REPLAY_SCOPE, ReplayBoundaryViolation,
    assert_replay_safe, classify,
)
from .runtime_attestation import attest, verify_attestation, derive_default_components

__all__ = [
    "build_runtime_envelope", "attest", "verify_attestation",
    "derive_default_components", "EnvelopeComponents", "RuntimeEnvelope",
    "CONTRACT_VERSION", "ENVELOPE_FIELDS", "INPUT_COMPONENTS",
    "canonical_bytes", "sha256_hex", "domain_hash",
    "compute_boot_id", "compute_session_id", "build_session", "RuntimeSession",
    "TOPOLOGY", "TOPOLOGY_EDGES", "topology_attestation",
    "REPLAY_SCOPE", "NON_REPLAY_SCOPE", "ReplayBoundaryViolation",
    "assert_replay_safe", "classify",
]


def build_runtime_envelope(
    *,
    collector_signature: str,
    signal_set_signature: str,
    cognition_v2_runtime_hash: str,
    baseline_anchor: str,
) -> RuntimeEnvelope:
    """Assemble and seal a RuntimeEnvelope from the four upstream anchors."""
    return attest(EnvelopeComponents(
        collector_signature=collector_signature,
        signal_set_signature=signal_set_signature,
        cognition_v2_runtime_hash=cognition_v2_runtime_hash,
        baseline_anchor=baseline_anchor,
    ))
