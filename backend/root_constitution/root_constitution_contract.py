"""Root Constitution contract (Sprint 77) — FROZEN.

Defines the canonical ordering of the seven constitutional anchors, the fixed
canonical inputs used to derive each layer's constitutional hash, and the
immutable RootConstitution structure.

No timestamps, randomness, uuid, env, process state, network, or filesystem
metadata participate. Canonical ordering only.
"""
from __future__ import annotations

from dataclasses import dataclass

ROOT_DOMAIN = "PULT-ROOT-CONSTITUTION/1"

# Canonical ordering of the seven constitutional anchors (frozen).
CONSTITUTION_ORDER: tuple[str, ...] = (
    "schema_baseline_revision",
    "logic_characterization_hash",
    "runtime_envelope_hash",
    "replay_chain_hash",
    "runtime_application_hash",
    "operator_console_hash",
    "operational_review_hash",
)

# Fixed canonical inputs used to derive layer hashes deterministically.
CANONICAL_ENVELOPE_COMPONENTS = {
    "collector_signature": "COLLECTOR_SIG",
    "signal_set_signature": "SIGNALSET_SIG",
    "cognition_v2_runtime_hash": "COGNITION_HASH",
    "baseline_anchor": "47beea1df0c1",
}

CANONICAL_EVENT_LOG = (
    {"event_type": "margin_pressure", "entity": "margin_crisis:wildberries:A", "weight": 8},
    {"event_type": "ad_spend_spike", "entity": "high_ad_spend:wildberries:A", "weight": 7},
    {"event_type": "recurrence", "entity": "margin_crisis:wildberries:A", "weight": 6},
    {"event_type": "stock_drop", "entity": "low_stock:wildberries:B", "weight": 5},
    {"event_type": "insight_resolved", "entity": "high_ad_spend:wildberries:A", "weight": 3},
    {"event_type": "cascade", "entity": "margin_crisis:ozon:C", "weight": 6, "marketplace": "ozon"},
)


@dataclass(frozen=True)
class RootConstitution:
    schema_baseline_revision: str
    logic_characterization_hash: str
    runtime_envelope_hash: str
    replay_chain_hash: str
    runtime_application_hash: str
    operator_console_hash: str
    operational_review_hash: str
    root_constitutional_hash: str

    def anchors(self) -> dict:
        return {name: getattr(self, name) for name in CONSTITUTION_ORDER}

    def ordered_pairs(self) -> list:
        return [[name, getattr(self, name)] for name in CONSTITUTION_ORDER]
