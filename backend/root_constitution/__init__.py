"""Root Constitution (Sprint 77).

The canonical identity of the entire PULT constitution: one deterministic
root_constitutional_hash aggregating the seven frozen constitutional anchors
(schema baseline, logic characterization, runtime envelope, replay chain, runtime
application, operator console, operational review). Read-only; no authority to
mutate, execute, recommend, or predict.
"""
from __future__ import annotations

from .root_constitution_boundary import (
    EXECUTION_AUTHORITY, MUTATION_AUTHORITY, RECOMMENDATION_AUTHORITY,
    PREDICTION_AUTHORITY, DESCRIPTIVE_ONLY, DETERMINISTIC, RootConstitutionViolation,
)
from .root_constitution_contract import (
    ROOT_DOMAIN, CONSTITUTION_ORDER, RootConstitution,
    CANONICAL_ENVELOPE_COMPONENTS, CANONICAL_EVENT_LOG,
)
from .root_constitution_hash import (
    compute_anchors, fold_root, schema_baseline_revision, logic_characterization_hash,
    runtime_envelope_hash, replay_chain_hash, runtime_application_hash,
    operator_console_hash, operational_review_hash,
)
from .root_constitution_attestation import (
    build_root_constitution, verify_root_constitution, VALID, INVALID,
)

__all__ = [
    "EXECUTION_AUTHORITY", "MUTATION_AUTHORITY", "RECOMMENDATION_AUTHORITY",
    "PREDICTION_AUTHORITY", "DESCRIPTIVE_ONLY", "DETERMINISTIC",
    "RootConstitutionViolation",
    "ROOT_DOMAIN", "CONSTITUTION_ORDER", "RootConstitution",
    "CANONICAL_ENVELOPE_COMPONENTS", "CANONICAL_EVENT_LOG",
    "compute_anchors", "fold_root", "schema_baseline_revision",
    "logic_characterization_hash", "runtime_envelope_hash", "replay_chain_hash",
    "runtime_application_hash", "operator_console_hash", "operational_review_hash",
    "build_root_constitution", "verify_root_constitution", "VALID", "INVALID",
]
