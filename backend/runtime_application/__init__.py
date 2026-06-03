"""Operational Runtime Application Layer v1 (Post-closure directive).

The first external operational application layer ABOVE the frozen substrate.
Strictly descriptive, append-only, fail-closed, deterministic, replay-
reconstructable. Treats all substrate layers (replay_chain, runtime_envelope,
logic, schema) as READ-ONLY. No execution or alteration authority.
"""
from __future__ import annotations

from .runtime_application_boundary import (
    EXECUTION_AUTHORITY, MUTATION_AUTHORITY, DESCRIPTIVE_ONLY, FAIL_CLOSED,
    REPLAY_COMPATIBLE, DETERMINISTIC, BoundaryViolation, assert_descriptive_only,
)
from .live_event_ingestion import RuntimeStream, ingest, normalize_event, ALLOWED_FIELDS
from .operational_state_projection import project_state, category_of
from .pressure_accumulation_runtime import (
    accumulation_regions, dissipation_surfaces, pressure_map,
)
from .intervention_surface_runtime import (
    intervention_surfaces, INTERVENTION_VISIBILITY_THRESHOLD,
)
from .drift_visualization_runtime import drift_regions, instability_markers, drift_map
from .replay_runtime_window import timeline, windows, reconstruct
from .runtime_console_projection import console_view
from .runtime_application_topology import (
    RuntimeApplication, build_runtime_application, build_from_stream,
    RUNTIME_APP_DOMAIN,
)
from .runtime_application_attestation import (
    RuntimeApplicationAttestation, attest, verify_attestation,
)

__all__ = [
    "EXECUTION_AUTHORITY", "MUTATION_AUTHORITY", "DESCRIPTIVE_ONLY", "FAIL_CLOSED",
    "REPLAY_COMPATIBLE", "DETERMINISTIC", "BoundaryViolation", "assert_descriptive_only",
    "RuntimeStream", "ingest", "normalize_event", "ALLOWED_FIELDS",
    "project_state", "category_of",
    "accumulation_regions", "dissipation_surfaces", "pressure_map",
    "intervention_surfaces", "INTERVENTION_VISIBILITY_THRESHOLD",
    "drift_regions", "instability_markers", "drift_map",
    "timeline", "windows", "reconstruct", "console_view",
    "RuntimeApplication", "build_runtime_application", "build_from_stream",
    "RUNTIME_APP_DOMAIN", "RuntimeApplicationAttestation", "attest", "verify_attestation",
]
