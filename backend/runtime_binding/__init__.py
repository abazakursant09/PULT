"""Runtime Binding (Sprint 80) — first runtime-to-constitution bridge.

Reads the live UserEvent stream read-only, canonicalizes it deterministically
(strips timestamps/uuids/random/ordering), and feeds the existing constitutional
substrate without modifying any frozen layer. Additive only. Fail-closed.
"""
from __future__ import annotations

from .runtime_binding_boundary import (
    EXECUTION_AUTHORITY, MUTATION_AUTHORITY, RECOMMENDATION_AUTHORITY,
    PREDICTION_AUTHORITY, DESCRIPTIVE_ONLY, FAIL_CLOSED, DETERMINISTIC, READ_ONLY,
    BindingViolation,
)
from .runtime_binding_contract import (
    ADAPTER_VERSION, BINDING_DOMAIN, BASELINE_ANCHOR, ALLOWED_USEREVENT_FIELDS,
    REQUIRED_USEREVENT_FIELDS, CANONICAL_FIELDS, RuntimeBinding,
)
from .event_canonicalizer import (
    canonicalize_event, canonicalize_stream, strip_uuid,
)
from .binding_hash import runtime_binding_hash
from .binding_adapter import build_runtime_binding
from .binding_attestation import (
    BindingAttestation, attest_binding, verify_binding, VALID, INVALID,
)
from .live_activation import (
    activate_from_track, bind_single_event, last_binding_hash, activation_count,
    ACTIVATION_LEDGER,
)

__all__ = [
    "EXECUTION_AUTHORITY", "MUTATION_AUTHORITY", "RECOMMENDATION_AUTHORITY",
    "PREDICTION_AUTHORITY", "DESCRIPTIVE_ONLY", "FAIL_CLOSED", "DETERMINISTIC",
    "READ_ONLY", "BindingViolation",
    "ADAPTER_VERSION", "BINDING_DOMAIN", "BASELINE_ANCHOR", "ALLOWED_USEREVENT_FIELDS",
    "REQUIRED_USEREVENT_FIELDS", "CANONICAL_FIELDS", "RuntimeBinding",
    "canonicalize_event", "canonicalize_stream", "strip_uuid",
    "runtime_binding_hash", "build_runtime_binding",
    "BindingAttestation", "attest_binding", "verify_binding", "VALID", "INVALID",
    "activate_from_track", "bind_single_event", "last_binding_hash",
    "activation_count", "ACTIVATION_LEDGER",
]
