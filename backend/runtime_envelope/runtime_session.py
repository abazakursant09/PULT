"""Runtime session identity (Sprint 73).

A runtime session is DETERMINISTIC: `session_id` is a pure function of the
boot_id and the signal-set signature. Identical (boot, signal_set) always
reproduce the identical session — sessions are replayable, not time-stamped.
"""
from __future__ import annotations

from dataclasses import dataclass

from .envelope_hash import domain_hash

SESSION_DOMAIN = "PULT-SESSION/1"


def compute_session_id(*, boot_id: str, signal_set_signature: str) -> str:
    """Reproducible session identity bound to a boot and a signal-set signature."""
    return domain_hash(SESSION_DOMAIN, {
        "boot_id": boot_id,
        "signal_set_signature": signal_set_signature,
    })


@dataclass(frozen=True)
class RuntimeSession:
    boot_id: str
    session_id: str
    signal_set_signature: str


def build_session(*, boot_id: str, signal_set_signature: str) -> RuntimeSession:
    return RuntimeSession(
        boot_id=boot_id,
        session_id=compute_session_id(boot_id=boot_id, signal_set_signature=signal_set_signature),
        signal_set_signature=signal_set_signature,
    )
