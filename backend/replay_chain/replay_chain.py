"""End-to-end replay chain reconstruction (Sprint 74) — read-only.

Given an ordered event_log and a baseline_anchor, deterministically reconstruct:

    events -> signal_set -> cognition topology -> runtime envelope -> chain hash

This is a CONSTITUTIONAL VERIFIER, not a re-implementation of cognition. It does
NOT run logic/cognition modules or touch the DB. The signal_set and cognition
topology are deterministic STRUCTURAL projections of the event log, hashed and
bound — through the frozen Runtime Envelope (Sprint 73) — into a single
replay_chain_hash. No clocks, randomness, uuids, env, or network.
"""
from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass

from runtime_envelope import build_runtime_envelope
from runtime_envelope.replay_boundary import assert_replay_safe

from .replay_hash import (
    EVENTLOG_DOMAIN, SIGNALSET_DOMAIN, COGNITION_DOMAIN, CHAIN_DOMAIN, stage_hash,
)


def _category(entity: str) -> str:
    base = entity.split(":", 1)[0]
    return base[5:] if base.startswith("demo_") else base


def reconstruct_signal_set(event_log: tuple[dict, ...]) -> dict:
    """Deterministic structural aggregation of the ordered event log."""
    type_counts: Counter = Counter()
    category_weight: dict[str, int] = defaultdict(int)
    entities: set[str] = set()
    marketplaces: set[str] = set()

    for ev in event_log:
        assert_replay_safe(ev)  # no non-deterministic field may enter the chain
        type_counts[ev["event_type"]] += 1
        entity = ev["entity"]
        entities.add(entity)
        category_weight[_category(entity)] += int(ev.get("weight", 0))
        if ev.get("marketplace"):
            marketplaces.add(ev["marketplace"])

    return {
        "event_count": len(event_log),
        "type_counts": dict(sorted(type_counts.items())),
        "category_weight": dict(sorted(category_weight.items())),
        "entities": sorted(entities),
        "marketplaces": sorted(marketplaces),
    }


def reconstruct_cognition_topology(signal_set: dict) -> dict:
    """Deterministic projection of which cognition categories the signal_set
    touches and their relative pressure. Structural only — no cognition logic."""
    cw = signal_set["category_weight"]
    return {
        "categories": sorted(cw.keys()),
        "pressure_profile": dict(sorted(cw.items())),
        "signal_count": signal_set["event_count"],
        "marketplace_span": len(signal_set["marketplaces"]),
    }


@dataclass(frozen=True)
class ReplayChain:
    baseline_anchor: str
    event_count: int
    event_log_hash: str
    signal_set: dict
    signal_set_signature: str
    cognition_topology: dict
    cognition_hash: str
    envelope_hash: str
    replay_chain_hash: str

    def hash_summary(self) -> dict:
        return {
            "event_log_hash": self.event_log_hash,
            "signal_set_signature": self.signal_set_signature,
            "cognition_hash": self.cognition_hash,
            "envelope_hash": self.envelope_hash,
            "replay_chain_hash": self.replay_chain_hash,
        }


def build_replay_chain(event_log: tuple[dict, ...], baseline_anchor: str) -> ReplayChain:
    event_log = tuple(event_log)
    event_log_hash = stage_hash(EVENTLOG_DOMAIN, list(event_log))  # order-preserving

    signal_set = reconstruct_signal_set(event_log)
    signal_set_signature = stage_hash(SIGNALSET_DOMAIN, signal_set)

    cognition_topology = reconstruct_cognition_topology(signal_set)
    cognition_hash = stage_hash(COGNITION_DOMAIN, cognition_topology)

    envelope = build_runtime_envelope(
        collector_signature=event_log_hash,        # collector consumes the event log
        signal_set_signature=signal_set_signature,
        cognition_v2_runtime_hash=cognition_hash,
        baseline_anchor=baseline_anchor,
    )

    replay_chain_hash = stage_hash(CHAIN_DOMAIN, {
        "event_log_hash": event_log_hash,
        "signal_set_signature": signal_set_signature,
        "cognition_hash": cognition_hash,
        "envelope_hash": envelope.runtime_envelope_hash,
        "baseline_anchor": baseline_anchor,
    })

    return ReplayChain(
        baseline_anchor=baseline_anchor,
        event_count=len(event_log),
        event_log_hash=event_log_hash,
        signal_set=signal_set,
        signal_set_signature=signal_set_signature,
        cognition_topology=cognition_topology,
        cognition_hash=cognition_hash,
        envelope_hash=envelope.runtime_envelope_hash,
        replay_chain_hash=replay_chain_hash,
    )
