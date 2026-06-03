"""Runtime Application topology (Post-closure directive) — top-level aggregator.

Builds the full deterministic runtime application from raw events and seals it
with a canonical `runtime_application_hash` (domain-separated SHA-256, reusing
the frozen Runtime Envelope hashing discipline). Reconstructable: same events
always produce the same hash, across processes.
"""
from __future__ import annotations

from dataclasses import dataclass

from runtime_envelope.envelope_hash import domain_hash

from .live_event_ingestion import ingest, RuntimeStream
from .operational_state_projection import project_state
from .pressure_accumulation_runtime import pressure_map
from .intervention_surface_runtime import intervention_surfaces
from .drift_visualization_runtime import drift_map
from .replay_runtime_window import reconstruct

RUNTIME_APP_DOMAIN = "PULT-RUNTIME-APP/1"


@dataclass(frozen=True)
class RuntimeApplication:
    runtime_event_count: int
    pressure_region_count: int
    intervention_surface_count: int
    drift_region_count: int
    state: dict
    pressure: dict
    interventions: list
    drift: dict
    replay: dict
    runtime_application_hash: str

    def summary(self) -> dict:
        return {
            "runtime_application_hash": self.runtime_application_hash,
            "runtime_event_count": self.runtime_event_count,
            "pressure_region_count": self.pressure_region_count,
            "intervention_surface_count": self.intervention_surface_count,
            "drift_region_count": self.drift_region_count,
        }


def build_from_stream(stream: RuntimeStream, window_size: int = 3) -> RuntimeApplication:
    state = project_state(stream)
    pressure = pressure_map(stream)
    interventions = intervention_surfaces(stream)
    drift = drift_map(stream)
    replay = reconstruct(stream, window_size)

    runtime_application_hash = domain_hash(RUNTIME_APP_DOMAIN, {
        "state": state,
        "pressure": pressure,
        "interventions": interventions,
        "drift": drift,
        "replay": replay,
    })

    return RuntimeApplication(
        runtime_event_count=stream.length,
        pressure_region_count=len(pressure["accumulation_regions"]),
        intervention_surface_count=len(interventions),
        drift_region_count=len(drift["drift_regions"]),
        state=state,
        pressure=pressure,
        interventions=interventions,
        drift=drift,
        replay=replay,
        runtime_application_hash=runtime_application_hash,
    )


def build_runtime_application(raw_events, window_size: int = 3) -> RuntimeApplication:
    """Top-level entry: ingest raw events (append-only, fail-closed) and seal."""
    return build_from_stream(ingest(raw_events), window_size)
