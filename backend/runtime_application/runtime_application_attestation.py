"""Runtime Application attestation (Post-closure directive).

Binds an append-only event log to its sealed runtime application. Verification
recomputes the full application from the event log and confirms byte-identical
`runtime_application_hash`. Fail-closed and replay-compatible by construction.
"""
from __future__ import annotations

from dataclasses import dataclass

from .runtime_application_topology import RuntimeApplication, build_runtime_application

REPLAY_COMPATIBLE = True
FAIL_CLOSED = True
DETERMINISTIC = True


@dataclass(frozen=True)
class RuntimeApplicationAttestation:
    raw_events: tuple
    window_size: int
    application: RuntimeApplication


def attest(raw_events, window_size: int = 3) -> RuntimeApplicationAttestation:
    application = build_runtime_application(raw_events, window_size)
    return RuntimeApplicationAttestation(
        raw_events=tuple(raw_events),
        window_size=window_size,
        application=application,
    )


def verify_attestation(att: RuntimeApplicationAttestation) -> bool:
    """Recompute from the attested event log; any tamper -> False (fail-closed)."""
    rebuilt = build_runtime_application(att.raw_events, att.window_size)
    return rebuilt.runtime_application_hash == att.application.runtime_application_hash
