"""Runtime attestation (Sprint 73).

Assembles the immutable RuntimeEnvelope from the four upstream anchors, computes
the deterministic runtime_envelope_hash, and verifies an envelope reproduces its
own hash. Also derives the four anchors from frozen repo state (optional binding
to the live constitution: schema baseline + characterization snapshots).
"""
from __future__ import annotations

from pathlib import Path

from .envelope_contract import (
    CONTRACT_VERSION, EnvelopeComponents, RuntimeEnvelope,
)
from .envelope_hash import domain_hash, sha256_hex
from .boot_identity import compute_boot_id
from .runtime_session import compute_session_id
from .envelope_topology import topology_attestation
from .replay_boundary import assert_replay_safe

ENVELOPE_DOMAIN = "PULT-ENVELOPE/1"


def attest(components: EnvelopeComponents) -> RuntimeEnvelope:
    """Build and seal the runtime envelope from the four anchors. Pure."""
    boot_id = compute_boot_id(
        baseline_anchor=components.baseline_anchor,
        collector_signature=components.collector_signature,
        cognition_v2_runtime_hash=components.cognition_v2_runtime_hash,
    )
    session_id = compute_session_id(
        boot_id=boot_id,
        signal_set_signature=components.signal_set_signature,
    )
    topo = topology_attestation()

    payload = {
        "boot_id": boot_id,
        "session_id": session_id,
        "collector_signature": components.collector_signature,
        "signal_set_signature": components.signal_set_signature,
        "cognition_v2_runtime_hash": components.cognition_v2_runtime_hash,
        "baseline_anchor": components.baseline_anchor,
        "topology_attestation": topo,
    }
    assert_replay_safe(payload)  # nothing non-deterministic may enter the hash
    envelope_hash = domain_hash(ENVELOPE_DOMAIN, payload)

    return RuntimeEnvelope(
        contract_version=CONTRACT_VERSION,
        boot_id=boot_id,
        session_id=session_id,
        collector_signature=components.collector_signature,
        signal_set_signature=components.signal_set_signature,
        cognition_v2_runtime_hash=components.cognition_v2_runtime_hash,
        baseline_anchor=components.baseline_anchor,
        topology_attestation=topo,
        runtime_envelope_hash=envelope_hash,
    )


def verify_attestation(envelope: RuntimeEnvelope) -> bool:
    """Recompute the envelope from its anchors and confirm byte-identical hash.

    Detects any tampering with boot_id, session_id, topology, or the hash itself.
    """
    rebuilt = attest(EnvelopeComponents(
        collector_signature=envelope.collector_signature,
        signal_set_signature=envelope.signal_set_signature,
        cognition_v2_runtime_hash=envelope.cognition_v2_runtime_hash,
        baseline_anchor=envelope.baseline_anchor,
    ))
    return (
        rebuilt.boot_id == envelope.boot_id
        and rebuilt.session_id == envelope.session_id
        and rebuilt.topology_attestation == envelope.topology_attestation
        and rebuilt.runtime_envelope_hash == envelope.runtime_envelope_hash
    )


# ── Optional binding to live constitutional anchors (deterministic repo reads) ──
# Reads only frozen, committed artifacts. No clocks/env/randomness participate.
# Kept separate from the pure core; the constitutional tests do NOT depend on it.

_BACKEND = Path(__file__).resolve().parents[1]


def _hash_paths(paths: list[Path]) -> str:
    items = []
    for p in sorted(paths, key=lambda x: x.as_posix()):
        items.append({"path": p.relative_to(_BACKEND).as_posix(),
                      "sha256": sha256_hex(p.read_bytes())})
    return sha256_hex(items)


def derive_default_components() -> EnvelopeComponents:
    """Derive the four anchors from frozen repo state.

    - baseline_anchor: alembic baseline revision id (schema constitution, Sprint 69)
    - cognition_v2_runtime_hash: hash over all characterization snapshots (Sprint 70-72)
    - collector_signature: hash of the runtime signal collector surface (events router)
    - signal_set_signature: hash of the signal-set record model (user_event)
    """
    versions = sorted((_BACKEND / "alembic" / "versions").glob("*_baseline_full_schema.py"))
    baseline_anchor = versions[0].name.split("_", 1)[0] if versions else "unknown"

    snapshots = list((_BACKEND / "tests" / "characterization").glob("*/snapshot.json"))
    cognition_v2_runtime_hash = _hash_paths(snapshots) if snapshots else sha256_hex("none")

    collector_signature = sha256_hex((_BACKEND / "routers" / "events.py").read_bytes())
    signal_set_signature = sha256_hex((_BACKEND / "models" / "user_event.py").read_bytes())

    return EnvelopeComponents(
        collector_signature=collector_signature,
        signal_set_signature=signal_set_signature,
        cognition_v2_runtime_hash=cognition_v2_runtime_hash,
        baseline_anchor=baseline_anchor,
    )
