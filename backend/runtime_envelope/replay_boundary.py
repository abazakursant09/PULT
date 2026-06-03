"""Replay boundary doctrine (Sprint 73) — FROZEN.

Defines EXACTLY what is inside the replay scope (deterministic, hashed, part of
the envelope identity) and what is outside it (non-deterministic runtime reality
that must never enter the envelope hash).

Doctrine: the runtime_envelope_hash must be byte-identical across replays.
Therefore nothing time-, process-, environment-, or network-dependent may
participate in it. `assert_replay_safe` enforces this at the boundary.
"""
from __future__ import annotations


class ReplayBoundaryViolation(ValueError):
    """Raised when non-deterministic state attempts to cross into replay scope."""


# INSIDE replay scope — deterministic, reproducible, part of the envelope hash.
REPLAY_SCOPE: tuple[str, ...] = (
    "boot_id",
    "session_id",
    "collector_signature",
    "signal_set_signature",
    "cognition_v2_runtime_hash",
    "baseline_anchor",
    "topology_attestation",
    "runtime_envelope_hash",
    "contract_version",
)

# OUTSIDE replay scope — non-deterministic runtime reality. Never hashed.
NON_REPLAY_SCOPE: tuple[str, ...] = (
    "wall_clock_time",
    "process_id",
    "thread_id",
    "random_seed",
    "environment_variables",
    "hostname",
    "network_state",
    "memory_address",
    "log_timestamp",
    "external_api_response",
    "telegram_delivery_receipt",
    "db_connection_state",
)

# Substrings that mark a key as non-deterministic. A payload key containing any
# of these may not enter the envelope hash.
_FORBIDDEN_SUBSTRINGS: tuple[str, ...] = (
    "time", "clock", "now", "timestamp", "date",
    "pid", "process", "thread",
    "random", "seed", "uuid", "nonce",
    "env", "host", "addr", "network", "socket", "connection",
    "receipt", "external", "response",
)


def classify(key: str) -> str:
    """Return 'outside' if the key is non-deterministic, else 'inside'.

    The fixed envelope fields are inside by definition (they ARE the replay
    identity); only keys NOT in REPLAY_SCOPE are substring-screened. The screen
    is intentionally conservative for unknown keys.
    """
    if key in REPLAY_SCOPE:
        return "inside"
    low = key.lower()
    if any(s in low for s in _FORBIDDEN_SUBSTRINGS):
        return "outside"
    return "inside"


def assert_replay_safe(payload: dict) -> None:
    """Raise ReplayBoundaryViolation if any payload key is outside replay scope."""
    offenders = sorted(k for k in payload if classify(k) == "outside")
    if offenders:
        raise ReplayBoundaryViolation(
            f"non-deterministic keys cannot enter replay scope: {offenders}"
        )
