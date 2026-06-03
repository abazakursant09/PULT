"""Constitutional Enforcement Layer (Sprint 78).

Makes the PULT constitution automatically enforceable: verify_full_constitution()
returns VALID/INVALID by confirming the live substrate reproduces the pinned
constitution-of-record (the seven layer anchors + root). The CI gate fails the
pipeline on any violation. Read-only; fail-closed; deterministic.
"""
from __future__ import annotations

from .constitution_boundary import (
    EXECUTION_AUTHORITY, MUTATION_AUTHORITY, RECOMMENDATION_AUTHORITY,
    PREDICTION_AUTHORITY, DESCRIPTIVE_ONLY, FAIL_CLOSED, DETERMINISTIC,
    EnforcementViolation,
)
from .constitution_contract import (
    VALID, INVALID, ENFORCED_LAYERS, EXPECTED_ANCHORS, EXPECTED_ROOT,
    ConstitutionReport, ENFORCEMENT_DOMAIN,
)
from .constitution_verifier import verify_full_constitution, verify_layers, _verify
from .constitution_report import build_constitution_report
from .constitution_gate import gate, main

__all__ = [
    "EXECUTION_AUTHORITY", "MUTATION_AUTHORITY", "RECOMMENDATION_AUTHORITY",
    "PREDICTION_AUTHORITY", "DESCRIPTIVE_ONLY", "FAIL_CLOSED", "DETERMINISTIC",
    "EnforcementViolation",
    "VALID", "INVALID", "ENFORCED_LAYERS", "EXPECTED_ANCHORS", "EXPECTED_ROOT",
    "ConstitutionReport", "ENFORCEMENT_DOMAIN",
    "verify_full_constitution", "verify_layers", "_verify",
    "build_constitution_report", "gate", "main",
]
