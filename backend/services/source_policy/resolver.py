"""Source resolver (PULT-LAUNCH-1.4.5H).

Given a (store, metric, period), decide which ONE source feeds it — API or CSV — and never both. The
decision has three inputs: the seller's stored preference, whether the API actually COVERS the period
(honestly, from ApiSyncState — a cursor is not coverage), and which candidate values exist. The two
sources are never summed; when both exist and differ under 'auto', CSV is the safe value and a
conflict is surfaced for the seller to resolve.

Manual-only metrics (cost of goods, external ad spend) are CSV/manual regardless — the API cannot
know them, so their absence is 'no data', never 0.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from config import settings
from models.api_sync_state import ApiSyncState
from models.store_data_source_policy import StoreDataSourcePolicy
from services.source_policy import dataset_authority as da

_MONEY_TOLERANCE = Decimal("0.01")


@dataclass
class Resolution:
    source: Optional[str]                       # 'api' | 'csv' | None
    value: Optional[object] = None              # the chosen value (when candidates supplied)
    completeness: str = "no_data"               # complete | incomplete | no_data
    missing_fields: list[str] = field(default_factory=list)
    conflict: bool = False
    conflict_candidates: Optional[dict] = None  # {'api': .., 'csv': ..} when conflict
    reason: str = ""


# ── Preference ────────────────────────────────────────────────────────────────
async def effective_preference(db: AsyncSession, store_id: str, metric_type: str) -> str:
    """The seller's stored preference, or 'csv' when none exists. Absent ⇒ csv deliberately keeps
    today's behaviour until the seller opts in (the UI default flips to 'auto' only in 1.4.5I)."""
    row = (await db.execute(select(StoreDataSourcePolicy).where(
        StoreDataSourcePolicy.marketplace_store_id == store_id,
        StoreDataSourcePolicy.metric_type == metric_type))).scalars().first()
    return row.preference if row else "csv"


# ── Coverage (honest) ─────────────────────────────────────────────────────────
def _backing_data_types(marketplace: str, metric_type: str) -> list[str]:
    """The ApiSyncState.data_type(s) that must be covered for this metric. Empty = no API backing."""
    if metric_type == "price":
        return ["prices"]
    if metric_type == "stock":
        return ["stocks"]
    if metric_type in ("catalog", "card_content"):
        return {"wildberries": ["card_content"], "ozon": ["products"],
                "yandex": ["products", "cards"]}.get(marketplace, [])
    auth = da.authoritative(marketplace, metric_type)
    return list(auth.datasets) if auth else []   # provider_dataset names == ApiSyncState.data_type names


async def api_coverage(db: AsyncSession, store_id: str, marketplace: str, metric_type: str,
                       period: Optional[tuple[str, str]], now: Optional[datetime] = None) -> tuple[bool, str]:
    """Is the API an eligible source for this (store, metric, period)? Snapshot metrics need a fresh
    successful sync; period metrics need proven contiguous coverage enclosing the window."""
    now = now or datetime.utcnow()
    data_types = _backing_data_types(marketplace, metric_type)
    if not data_types:
        return False, "api_unsupported_metric"

    is_snapshot = metric_type in da.SNAPSHOT_METRICS
    for dt in data_types:
        state = (await db.execute(select(ApiSyncState).where(
            ApiSyncState.marketplace_store_id == store_id,
            ApiSyncState.data_type == dt))).scalars().first()
        if state is None or state.status != "synced":
            return False, "not_synced"
        if is_snapshot:
            ttl = timedelta(hours=settings.api_snapshot_freshness_hours)
            if state.last_success_at is None or (now - state.last_success_at) > ttl:
                return False, "stale_snapshot"
        else:
            if not state.coverage_complete or (state.skipped_rows_count or 0) > 0:
                return False, "coverage_incomplete"
            if period is not None:
                p_from, p_to = period
                if not (state.covered_from and state.covered_to
                        and state.covered_from <= p_from and state.covered_to >= p_to):
                    return False, "period_not_covered"
    return True, "covered"


# ── Pure decision ─────────────────────────────────────────────────────────────
def _differ(a, b) -> bool:
    if isinstance(a, Decimal) or isinstance(b, Decimal):
        try:
            return abs(Decimal(str(a)) - Decimal(str(b))) > _MONEY_TOLERANCE
        except Exception:  # noqa: BLE001 — non-numeric falls through to plain inequality
            return a != b
    return a != b


def decide(*, marketplace: str, metric_type: str, preference: str, api_eligible: bool,
           api_value=None, csv_value=None) -> Resolution:
    """Choose one source from the preference + eligibility + presence. Never sums; never a hidden
    fallback under an explicit preference. Pure and fully unit-testable."""
    supported = da.api_supported(marketplace, metric_type)
    manual_only = metric_type in da.MANUAL_ONLY_METRICS
    api_present = api_eligible and supported and api_value is not None
    csv_present = csv_value is not None

    if manual_only:
        # Cost of goods / external ad spend — CSV/manual only; absence is 'no data', never 0.
        if csv_present:
            return Resolution("csv", csv_value, "complete", reason="manual_only")
        return Resolution(None, None, "no_data", missing_fields=[metric_type], reason="manual_only_missing")

    if preference == "csv":
        if csv_present:
            return Resolution("csv", csv_value, "complete", reason="pref_csv")
        return Resolution(None, None, "no_data", reason="pref_csv_no_data")

    if preference == "api":
        if not supported:
            # An explicit API choice for an unsupported metric (e.g. Yandex money) is honestly empty —
            # NOT a silent switch to CSV.
            return Resolution(None, None, "no_data", reason="api_unsupported")
        if api_present:
            return Resolution("api", api_value, "complete", reason="pref_api")
        return Resolution(None, None, "no_data", reason="api_no_coverage")

    # auto
    if api_present and csv_present:
        if _differ(api_value, csv_value):
            return Resolution("csv", csv_value, "complete", conflict=True,
                              conflict_candidates={"api": api_value, "csv": csv_value},
                              reason="conflict_safe_csv")
        return Resolution("api", api_value, "complete", reason="auto_api_agrees")
    if api_present:
        return Resolution("api", api_value, "complete", reason="auto_api")
    if csv_present:
        return Resolution("csv", csv_value, "complete", reason="auto_csv")
    return Resolution(None, None, "no_data", reason="auto_no_data")


# ── Orchestration ─────────────────────────────────────────────────────────────
async def resolve_source(db: AsyncSession, *, store_id: str, marketplace: str, metric_type: str,
                         period: Optional[tuple[str, str]] = None, api_value=None, csv_value=None,
                         now: Optional[datetime] = None) -> Resolution:
    """End-to-end: read the preference, compute honest coverage, then decide. Callers pass the two
    candidate values (or None) they already have; the resolver never sums them."""
    preference = await effective_preference(db, store_id, metric_type)
    api_eligible, _ = await api_coverage(db, store_id, marketplace, metric_type, period, now=now)
    return decide(marketplace=marketplace, metric_type=metric_type, preference=preference,
                  api_eligible=api_eligible, api_value=api_value, csv_value=csv_value)
