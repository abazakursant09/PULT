"""Runtime Envelope contract (Sprint 73) — FROZEN.

Defines the immutable container schema sitting ABOVE Runtime Signal Collector and
BELOW Operator Cognition V2. The envelope binds the identity of the runtime into
a single deterministic hash.

This contract is frozen: field set, field order, and contract version must not
change without an explicit governance unlock. See
docs/governance/runtime_envelope_constitution.md.
"""
from __future__ import annotations

from dataclasses import dataclass

CONTRACT_VERSION = "runtime-envelope/1"

# The seven envelope fields, in canonical order (task #2). Order is part of the
# contract; hashing is order-independent, but this tuple is the authoritative
# field set and is itself attested.
ENVELOPE_FIELDS: tuple[str, ...] = (
    "boot_id",
    "session_id",
    "collector_signature",
    "signal_set_signature",
    "cognition_v2_runtime_hash",
    "baseline_anchor",
    "topology_attestation",
)

# Components supplied from OUTSIDE the envelope (the four upstream anchors).
# boot_id / session_id / topology_attestation are DERIVED, never supplied.
INPUT_COMPONENTS: tuple[str, ...] = (
    "collector_signature",
    "signal_set_signature",
    "cognition_v2_runtime_hash",
    "baseline_anchor",
)


@dataclass(frozen=True)
class EnvelopeComponents:
    """The four externally-supplied anchors. Frozen — immutable once built."""
    collector_signature: str
    signal_set_signature: str
    cognition_v2_runtime_hash: str
    baseline_anchor: str


@dataclass(frozen=True)
class RuntimeEnvelope:
    """The immutable runtime envelope. All fields deterministic functions of the
    four input components. `runtime_envelope_hash` is the byte-identical replay
    identity of the whole runtime."""
    contract_version: str
    boot_id: str
    session_id: str
    collector_signature: str
    signal_set_signature: str
    cognition_v2_runtime_hash: str
    baseline_anchor: str
    topology_attestation: str
    runtime_envelope_hash: str

    def as_payload(self) -> dict:
        """Ordered field payload used for hashing / attestation (excludes the
        hash itself and the contract version metadata)."""
        return {f: getattr(self, f) for f in ENVELOPE_FIELDS}
