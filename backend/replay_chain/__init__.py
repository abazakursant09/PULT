"""Replay Chain constitutional verifier (Sprint 74).

Read-only. Reconstructs events -> signal_set -> cognition topology -> runtime
envelope -> replay_chain_hash, deterministically and byte-identically across
replays. Does not modify or execute cognition/collector/logic.
"""
from __future__ import annotations

from .replay_chain import (
    ReplayChain, build_replay_chain,
    reconstruct_signal_set, reconstruct_cognition_topology,
)
from .replay_attestation import ReplayAttestation, attest_replay, verify_attestation
from .replay_fixture import (
    ReplayFixture, ALL_FIXTURES, FIXTURES_BY_NAME, BASELINE_ANCHOR,
)
from .replay_hash import canonical_bytes, sha256_hex, domain_hash, stage_hash

__all__ = [
    "ReplayChain", "build_replay_chain",
    "reconstruct_signal_set", "reconstruct_cognition_topology",
    "ReplayAttestation", "attest_replay", "verify_attestation",
    "ReplayFixture", "ALL_FIXTURES", "FIXTURES_BY_NAME", "BASELINE_ANCHOR",
    "canonical_bytes", "sha256_hex", "domain_hash", "stage_hash",
]
