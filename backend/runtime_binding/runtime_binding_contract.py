"""Runtime Binding contract (Sprint 80) — FROZEN.

Defines the adapter version, hash domain, the accepted UserEvent fields, and the
immutable RuntimeBinding structure. No timestamps, randomness, uuid, env in the
canonical output.
"""
from __future__ import annotations

from dataclasses import dataclass

ADAPTER_VERSION = "runtime-binding/1"
BINDING_DOMAIN = "PULT-RUNTIME-BINDING/1"

# Baseline anchor used when projecting into the replay chain (schema constitution).
BASELINE_ANCHOR = "47beea1df0c1"

# Fields accepted on an incoming UserEvent record. Anything else -> fail-closed.
ALLOWED_USEREVENT_FIELDS = frozenset({
    "id", "user_id", "event_type", "event_scope", "entity_id",
    "metadata_json", "created_at",
})
REQUIRED_USEREVENT_FIELDS = ("event_type", "entity_id")

# Canonical constitutional event fields (matches substrate ingestion shape).
CANONICAL_FIELDS = ("event_type", "entity", "weight", "marketplace")


@dataclass(frozen=True)
class RuntimeBinding:
    adapter_version: str
    canonical_event_count: int
    canonical_events: tuple
    runtime_application_hash: str
    replay_chain_hash: str
    operational_review_hash: str
    runtime_binding_hash: str

    def summary(self) -> dict:
        return {
            "adapter_version": self.adapter_version,
            "canonical_event_count": self.canonical_event_count,
            "runtime_application_hash": self.runtime_application_hash,
            "replay_chain_hash": self.replay_chain_hash,
            "operational_review_hash": self.operational_review_hash,
            "runtime_binding_hash": self.runtime_binding_hash,
        }
