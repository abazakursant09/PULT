"""Replay attestation (Sprint 74) — read-only verification.

A ReplayAttestation binds an event_log + baseline_anchor to the full set of
reconstructed chain hashes. `verify_attestation` re-runs the entire chain from
the event_log and confirms every stored hash matches — so tampering with the
event log, the signal-set signature, the cognition hash, the envelope hash, or
the chain hash is all detected.
"""
from __future__ import annotations

from dataclasses import dataclass

from .replay_chain import ReplayChain, build_replay_chain


@dataclass(frozen=True)
class ReplayAttestation:
    event_log: tuple[dict, ...]
    baseline_anchor: str
    chain: ReplayChain


def attest_replay(event_log: tuple[dict, ...], baseline_anchor: str) -> ReplayAttestation:
    chain = build_replay_chain(event_log, baseline_anchor)
    return ReplayAttestation(event_log=tuple(event_log), baseline_anchor=baseline_anchor, chain=chain)


def verify_attestation(att: ReplayAttestation) -> bool:
    """Recompute the chain from the attested event_log and confirm byte-identity
    of every stage hash. Any tamper -> False."""
    rebuilt = build_replay_chain(att.event_log, att.baseline_anchor)
    c = att.chain
    return (
        rebuilt.event_log_hash == c.event_log_hash
        and rebuilt.signal_set_signature == c.signal_set_signature
        and rebuilt.cognition_hash == c.cognition_hash
        and rebuilt.envelope_hash == c.envelope_hash
        and rebuilt.replay_chain_hash == c.replay_chain_hash
    )
