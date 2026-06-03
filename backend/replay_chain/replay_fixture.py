"""Canonical replay fixtures (Sprint 74).

Each fixture is a deterministic, ordered event log + a baseline anchor. Events
carry NO clocks, uuids, or randomness — only fixed structural fields
(event_type, entity, weight, marketplace). The six fixtures model distinct
runtime regimes used to freeze the end-to-end replay chain.
"""
from __future__ import annotations

from dataclasses import dataclass

BASELINE_ANCHOR = "47beea1df0c1"  # alembic baseline (Sprint 69)


@dataclass(frozen=True)
class ReplayFixture:
    name: str
    baseline_anchor: str
    event_log: tuple[dict, ...]


def _ev(event_type: str, entity: str, weight: int, marketplace: str = "wildberries") -> dict:
    return {"event_type": event_type, "entity": entity, "weight": weight, "marketplace": marketplace}


# ── steady_state: low, stable activity ──────────────────────────────────────────
STEADY_STATE = ReplayFixture("steady_state", BASELINE_ANCHOR, (
    _ev("dashboard_opened", "user:1", 1),
    _ev("insight_opened", "margin_crisis:wildberries:A", 2),
    _ev("insight_resolved", "margin_crisis:wildberries:A", 3),
))

# ── cascading_failure: one failure spawning downstream pressure ─────────────────
CASCADING_FAILURE = ReplayFixture("cascading_failure", BASELINE_ANCHOR, (
    _ev("margin_pressure", "margin_crisis:wildberries:A", 8),
    _ev("ad_spend_spike", "high_ad_spend:wildberries:A", 7),
    _ev("margin_pressure", "margin_crisis:wildberries:B", 6),
    _ev("stock_drop", "low_stock:wildberries:A", 5),
))

# ── drift_storm: many drifting recurring signals ────────────────────────────────
DRIFT_STORM = ReplayFixture("drift_storm", BASELINE_ANCHOR, (
    _ev("recurrence", "margin_crisis:ozon:A", 4),
    _ev("recurrence", "margin_crisis:ozon:B", 4),
    _ev("recurrence", "high_ad_spend:ozon:C", 4),
    _ev("recurrence", "seo_opportunity:ozon:D", 3),
    _ev("recurrence", "margin_crisis:ozon:E", 4),
))

# ── intervention_collapse: interventions that fail repeatedly ───────────────────
INTERVENTION_COLLAPSE = ReplayFixture("intervention_collapse", BASELINE_ANCHOR, (
    _ev("intervention_started", "margin_crisis:wildberries:A", 5),
    _ev("intervention_failed", "margin_crisis:wildberries:A", 9),
    _ev("intervention_started", "margin_crisis:wildberries:A", 5),
    _ev("intervention_failed", "margin_crisis:wildberries:A", 9),
    _ev("reversal", "margin_crisis:wildberries:A", 6),
))

# ── propagation_fracture: pressure jumping across marketplaces ──────────────────
PROPAGATION_FRACTURE = ReplayFixture("propagation_fracture", BASELINE_ANCHOR, (
    _ev("margin_pressure", "margin_crisis:wildberries:A", 7, "wildberries"),
    _ev("margin_pressure", "margin_crisis:ozon:A", 7, "ozon"),
    _ev("margin_pressure", "margin_crisis:yandex_market:A", 7, "yandex_market"),
    _ev("cascade", "high_ad_spend:ozon:A", 6, "ozon"),
))

# ── replay_instability_burst: dense burst of mixed high-weight events ───────────
REPLAY_INSTABILITY_BURST = ReplayFixture("replay_instability_burst", BASELINE_ANCHOR, (
    _ev("margin_pressure", "margin_crisis:wildberries:A", 10),
    _ev("ad_spend_spike", "high_ad_spend:wildberries:A", 10),
    _ev("stock_drop", "low_stock:wildberries:A", 10),
    _ev("recurrence", "margin_crisis:wildberries:A", 9),
    _ev("intervention_failed", "margin_crisis:wildberries:A", 9),
    _ev("cascade", "high_ad_spend:wildberries:B", 8),
))

ALL_FIXTURES: tuple[ReplayFixture, ...] = (
    STEADY_STATE, CASCADING_FAILURE, DRIFT_STORM,
    INTERVENTION_COLLAPSE, PROPAGATION_FRACTURE, REPLAY_INSTABILITY_BURST,
)

FIXTURES_BY_NAME = {f.name: f for f in ALL_FIXTURES}
