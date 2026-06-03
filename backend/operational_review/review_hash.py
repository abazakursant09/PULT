"""Deterministic hashing for the operational review layer (Sprint 76).

Reuses the frozen Runtime Envelope hashing discipline (canonical SHA-256). No
clocks, randomness, env, or network. Domain-separated per review structure.
"""
from __future__ import annotations

from typing import Any

from runtime_envelope.envelope_hash import canonical_bytes, sha256_hex, domain_hash

__all__ = ["canonical_bytes", "sha256_hex", "domain_hash",
           "FINDING_DOMAIN", "SNAPSHOT_DOMAIN", "LEDGER_DOMAIN", "REVIEW_DOMAIN"]

FINDING_DOMAIN = "PULT-REVIEW-FINDING/1"
SNAPSHOT_DOMAIN = "PULT-REVIEW-SNAPSHOT/1"
LEDGER_DOMAIN = "PULT-REVIEW-LEDGER/1"
REVIEW_DOMAIN = "PULT-OPERATIONAL-REVIEW/1"


def review_domain_hash(domain: str, payload: Any) -> str:
    return domain_hash(domain, payload)
