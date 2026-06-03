"""Deterministic hashing for the replay chain (Sprint 74).

Reuses the frozen Runtime Envelope hashing primitives (Sprint 73) — same
canonicalization, same SHA-256 discipline. No clocks, randomness, uuids, env, or
network. Domain tags keep each chain stage's hash distinct.
"""
from __future__ import annotations

from typing import Any

from runtime_envelope.envelope_hash import canonical_bytes, sha256_hex, domain_hash

__all__ = ["canonical_bytes", "sha256_hex", "domain_hash", "stage_hash"]

# Replay-chain stage domains (frozen).
EVENTLOG_DOMAIN = "PULT-REPLAY-EVENTLOG/1"
SIGNALSET_DOMAIN = "PULT-REPLAY-SIGNALSET/1"
COGNITION_DOMAIN = "PULT-REPLAY-COGNITION/1"
CHAIN_DOMAIN = "PULT-REPLAY-CHAIN/1"


def stage_hash(domain: str, payload: Any) -> str:
    return domain_hash(domain, payload)
