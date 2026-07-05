"""
Advisory Runtime producer registry (Phase-0 foundation) — ProducerSpec + the
declarative ADVISORY_PRODUCERS list.

A producer is registered as ONE ProducerSpec. The Runtime knows only these four
fields — `key`, `run`, `cadence_seconds`, `enabled`. It does NOT know scope,
per-listing caps, snapshot builders, thresholds, or any contour-specific detail:
those live inside the producer adapter behind `run`. Adding a producer is therefore
one registry entry, with NO change to the Runtime.

This foundation slice ships an EMPTY registry — no producer adapter exists yet, so
nothing runs. Adapters are registered in later slices.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Awaitable, Callable, Tuple

from .runtime import RuntimeContext, ProducerResult
from .producers import (
    run_growth_producer, run_legal_producer, run_review_producer,
    run_operations_low_stock_producer, run_advertising_producer, run_pricing_producer,
    run_revenue_diagnosis_producer, run_money_leak_producer, run_supply_producer,
    run_rating_producer,
)


@dataclass(frozen=True)
class ProducerSpec:
    """One registered advisory producer. Runtime-visible surface only."""
    key: str
    run: Callable[[RuntimeContext], Awaitable[ProducerResult]]
    cadence_seconds: int
    enabled: bool


# Registered producers.
#  - growth: ENABLED (A13) — third LIVE-SAFE advisory producer. The permissive-default
#    blocker is gone: thresholds are derived read-only from the seller's OWN observed
#    finance (services/growth/threshold_source, A11), and the shadow run (A12) proved it
#    advisory-only. Growth is advisory-only and not bindable — it creates growth_signal
#    rows only (no Decision, no EngineSignalDecisionLink, no Apply, no executor, no
#    marketplace write). Hourly cadence.
#  - legal: ENABLED (A8) — the first LIVE-SAFE advisory producer. Advisory-only,
#    binding AUTO_FORBIDDEN (can never bind an executor), threshold-free, DB-headless,
#    no marketplace read. Running it on the daily cadence creates legal_signal rows
#    only — no Decision, no Apply, no executor, no marketplace write.
ADVISORY_PRODUCERS: Tuple[ProducerSpec, ...] = (
    ProducerSpec(key="growth", run=run_growth_producer, cadence_seconds=3600, enabled=True),
    ProducerSpec(key="legal", run=run_legal_producer, cadence_seconds=86400, enabled=True),
    # review: ENABLED (A10) — second live-safe advisory producer. Advisory-only;
    # publish_review_response is PAYLOAD_NOT_DERIVABLE and negatives are MANUAL_ONLY,
    # so it can never bind an executor. Running it creates review_signal rows only —
    # no Decision, no Apply, no executor, no marketplace write.
    ProducerSpec(key="review", run=run_review_producer, cadence_seconds=86400, enabled=True),
    # operations_low_stock: ENABLED (A22) — first canonical producer for a legacy
    # _compute_insights signal (low_stock), running ALONGSIDE the legacy on-read logic
    # (legacy untouched). NARROW name so future operations producers (ready_to_ship,
    # supply_risk, warehouse, orders) are each their own producer, not one bucket.
    # Advisory-only: operations_low_stock binds to no executor and is not in the
    # Decision-Outcome canonical set, so it creates operations_signal rows only — no
    # Decision, no EngineSignalDecisionLink, no Apply, no executor, no marketplace
    # write. operations_signal is read by the Decision Feed (A21) → flows into Today.
    ProducerSpec(key="operations_low_stock", run=run_operations_low_stock_producer,
                 cadence_seconds=86400, enabled=True),
    # advertising: ENABLED (Phase 1.2) — the Advisory Runtime is now the SOLE producer
    # of advertising_signal. Thin adapter over the existing advertising engine with an
    # adapter-owned threshold source. The /advertising/audit router no longer writes
    # directly — it delegates "recompute now" to run_one("advertising"). Advisory-only:
    # writes advertising_signal rows only (no Decision, no link, no executor, no
    # marketplace write).
    ProducerSpec(key="advertising", run=run_advertising_producer,
                 cadence_seconds=86400, enabled=True),
    # pricing: ENABLED (Phase 1.5) — the Advisory Runtime is the SOLE writer of
    # pricing_signal (pricing was a dormant contour with no prior production writer). Thin
    # adapter over the existing pricing generator with an adapter-owned threshold source;
    # advisory-only (no Decision / link / executor / marketplace write). NOTE: pricing_
    # signal is NOT yet read by the Decision Feed — surfacing it there is a separate
    # follow-up slice; this flip only starts scheduled production.
    ProducerSpec(key="pricing", run=run_pricing_producer,
                 cadence_seconds=86400, enabled=True),
    # revenue_diagnosis: ENABLED (Phase 2.3b) — first Business-Diagnosis contour (WHERE
    # revenue is disappearing) now the canonical SCHEDULED writer of revenue_signal. Pure
    # diagnosis over the seller's OWN observed finance series; confirmed trajectory only
    # (spike-rejected). Emits an evidence-backed revenue_signal ONLY — no treatment, no
    # executor binding (recommended_action_key=None), no Decision. Already read by the
    # Decision Feed (Phase 2.3a), so the full chain is now live: Advisory Runtime →
    # revenue_signal → Decision Feed → Today → Dashboard/Telegram/Copilot. Advisory-only.
    ProducerSpec(key="revenue_diagnosis", run=run_revenue_diagnosis_producer,
                 cadence_seconds=86400, enabled=True),
    # money_leak: ENABLED (Phase 3.3b) — Business-Diagnosis contour (WHY money leaks:
    # silent commission/logistics cost-share drift) now the canonical SCHEDULED writer of
    # money_leak_signal. Pure diagnosis over the seller's OWN observed finance; confirmed
    # upward cost-share drift only (spike-rejected). Emits an evidence-backed
    # money_leak_signal ONLY — no treatment, no executor binding (recommended_action_key=
    # None), no Decision. Independent — does NOT absorb Pricing/Advertising/Operations/
    # Revenue. Already read by the Decision Feed (Phase 3.3a), so the full chain is now
    # live: Advisory Runtime → money_leak_signal → Decision Feed → Today →
    # Dashboard/Telegram/Copilot. Advisory-only.
    ProducerSpec(key="money_leak", run=run_money_leak_producer,
                 cadence_seconds=86400, enabled=True),
    # supply: ENABLED (Phase 4.3b) — Supply/Replenishment Diagnosis (operations-breadth
    # stock-out RUNWAY: days_to_oos = observed stock ÷ observed sell-through velocity) now
    # the canonical SCHEDULED writer of supply_signal. Pure diagnosis over the seller's OWN
    # stock + finance; observed present runway, not a forecast. Emits an evidence-backed
    # supply_signal ONLY — no treatment, no executor binding (recommended_action_key=None),
    # no Decision. Independent — complements (does NOT replace) operations low_stock, does
    # NOT reuse operations_signal. Already read by the Decision Feed (Phase 4.3a), so the
    # full chain is now live: Advisory Runtime → supply_signal → Decision Feed → Today →
    # Dashboard/Telegram/Copilot. Advisory-only.
    ProducerSpec(key="supply", run=run_supply_producer,
                 cadence_seconds=86400, enabled=True),
    # rating: DISABLED (Phase 5.1, shadow) — Rating / Reputation Health Diagnosis (aggregate
    # rating DECLINE from the seller's OWN ImportedProductRow.rating across dated snapshots).
    # Pure diagnosis; confirmed decline only (history-gated, blip-rejected). Emits an
    # evidence-backed rating_signal ONLY — no treatment, no executor binding
    # (recommended_action_key=None), no Decision. DISTINCT from the Review contour (individual
    # review handling) — does NOT touch Review or reuse review_signal. ENABLED (Phase 5.3b):
    # the scheduler now runs it daily and it writes rating_signal, surfacing through the
    # Decision Feed reader wired in 5.3a. Advisory-only.
    ProducerSpec(key="rating", run=run_rating_producer,
                 cadence_seconds=86400, enabled=True),
)
