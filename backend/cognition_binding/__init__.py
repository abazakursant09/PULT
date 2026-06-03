"""Cognition Binding (Sprint 83) — binds the cognition spine to the substrate.

Observes cognition output (InsightItem list) after computation, canonicalizes it
deterministically (strips uuid-derived ids/timestamps, normalizes ordering/sets),
produces a deterministic cognition_binding_hash, and feeds the existing substrate
read-only. Additive only; no frozen layer or cognition behavior changed.
"""
from __future__ import annotations

from .cognition_binding_boundary import (
    EXECUTION_AUTHORITY, MUTATION_AUTHORITY, RECOMMENDATION_AUTHORITY,
    PREDICTION_AUTHORITY, DESCRIPTIVE_ONLY, APPEND_ONLY, FAIL_CLOSED, DETERMINISTIC,
    READ_ONLY, CognitionBindingViolation,
)
from .cognition_projection import project_insights, project_insight
from .cognition_canonicalizer import canonicalize_projection, projection_to_events
from .cognition_binding_hash import (
    cognition_binding_hash, ADAPTER_VERSION, COGNITION_BINDING_DOMAIN,
)
from .cognition_binding_adapter import (
    CognitionBinding, observe, build_from_projection, observe_and_record,
    COGNITION_LEDGER, BASELINE_ANCHOR,
)
from .cognition_binding_attestation import (
    CognitionAttestation, attest, verify_binding, VALID, INVALID,
)

__all__ = [
    "EXECUTION_AUTHORITY", "MUTATION_AUTHORITY", "RECOMMENDATION_AUTHORITY",
    "PREDICTION_AUTHORITY", "DESCRIPTIVE_ONLY", "APPEND_ONLY", "FAIL_CLOSED",
    "DETERMINISTIC", "READ_ONLY", "CognitionBindingViolation",
    "project_insights", "project_insight",
    "canonicalize_projection", "projection_to_events",
    "cognition_binding_hash", "ADAPTER_VERSION", "COGNITION_BINDING_DOMAIN",
    "CognitionBinding", "observe", "build_from_projection", "observe_and_record",
    "COGNITION_LEDGER", "BASELINE_ANCHOR",
    "CognitionAttestation", "attest", "verify_binding", "VALID", "INVALID",
]
