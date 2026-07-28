"""
PULT-LAUNCH-2.5B — runtime protection evaluation service. Feature OFF.

Resolves the target (a product policy → its one placed product; a store-wide policy → each ACTIVE
placement, one Evaluation each, never an average, skipping products that have their own enabled
policy), gathers ContributionInputs from EXISTING readers, calls the UNCHANGED 2.4 engine, and writes
ONE append-only ProtectionEvaluation per (policy, product, run) in a single transaction.

Honesty rules enforced here (a green calculation is never bought by relaxing one of them):
  * STORE ISOLATION — every CSV read (units, returns, money) is scoped to ONE marketplace_store_id.
    user_id is not a sufficient key: a seller may own several stores and the same SKU may live in
    more than one, so two stores of one seller are never merged.
  * SOURCE CONSISTENCY — a money magnitude and the unit denominator it is divided by must come from
    the SAME source. API money is never divided by CSV quantity (or vice-versa); if no
    source-consistent quantity exists the metric is MISSING, never guessed.
  * RETURNS CONSISTENCY — sold and returned units share one store+product, one window and one source
    (CSV on master); API sold is never paired with CSV returns.
  * CURRENCY is PROVEN, never defaulted — no unconditional "RUB". The catalog price carries no stored
    currency, so it is flagged currency_unconfirmed.
  * FRESHNESS is recorded per input (source / fetched_at / age / threshold_seconds=null) with a
    top-level freshness_gate_configured=false; no configured threshold ⇒ nothing is called fresh and
    actionability never rises above manual_only.

On master the honest result is incomplete / manual_only: no proven promo price (catalog price is
warning-only → promo_price_proven=False), no official commission tariff, no provable per-return cost,
no confirmed provider capability. This service NEVER creates a ProtectionActionState, an ExecutionLog,
a provider call, or a notification, and it is not wired to a scheduler.
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal
from typing import List, Optional, Sequence, Tuple, Union

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from models.protection import (
    ProtectionPolicy, ProtectionEvaluation, ProtectionAdditionalCost, ProtectionTaxSetting,
)
from models.marketplace_store import MarketplaceStore
from models.product_placement import ProductPlacement
from models.product import Product
from models.product_listing import ProductListing
from models.physical_product import PhysicalProduct
from models.imported_product import ImportedProductRow
from models.imported_finance import ImportedFinanceRow
from models.imported_return import ImportedReturnRow
from models.marketplace_operation import MarketplaceOperation

from services.protection.contribution import (
    ContributionInputs, Thresholds, CostLine, ReturnsStats, compute,
    PROVIDER_OP, MANUAL_ADD, CONFIRMED_ZERO, MISSING, COST_KEYS, DEFAULT_RETURN_WINDOW_DAYS,
)
from services.source_policy.resolver import resolve_source
from services.source_policy.product_money_reader import api_product_count
from services.source_policy.money_reader import _period_filter
from services.source_policy import dataset_authority as da
from services.protection.cost_map import resolve_metric_money, MONEY_METRICS

WINDOW_DAYS = DEFAULT_RETURN_WINDOW_DAYS   # 90
# storage/acquiring/last_mile have no marketplace-operation source on master; they are resolved only
# from a seller ProtectionAdditionalCost whose name matches the key, else they stay MISSING.
_NO_SOURCE_KEYS = ("storage", "acquiring", "last_mile")

SKIP_POLICY_DISABLED = "policy_disabled"
SKIP_CONSENT_ABSENT = "consent_absent"
SKIP_CONSENT_REVOKED = "consent_revoked"
SKIP_STORE_ARCHIVED = "store_archived"
SKIP_PLACEMENT_INACTIVE = "placement_inactive"
SKIP_SCOPE_MISMATCH = "scope_mismatch"
SKIP_OVERRIDDEN = "overridden_by_product_policy"

# The engine's calculation_status vocabulary (complete|incomplete|stale|conflict) onto the
# ProtectionEvaluation.verdict CHECK vocabulary (…|conflicting|…). Only 'conflict' differs; without
# this map a conflict result would violate ck_evaluation_verdict on insert.
_VERDICT_MAP = {"conflict": "conflicting"}


@dataclass
class SkipResult:
    """A typed non-evaluation: a gate refused, so NO ProtectionEvaluation with invented values."""
    reason: str
    policy_id: Optional[str]
    marketplace_store_id: Optional[str] = None
    product_id: Optional[str] = None
    skipped: bool = True


EvalOutcome = Union[ProtectionEvaluation, SkipResult]


def _validate_run_id(run_id) -> str:
    """A runtime run id MUST be a real UUID. Reject before any gather/insert so an invalid id can
    never create an Evaluation. Returns the canonical UUID string."""
    try:
        return str(uuid.UUID(str(run_id)))
    except (ValueError, AttributeError, TypeError):
        raise ValueError(f"invalid evaluation_run_id: {run_id!r}")


def _period(now: datetime) -> tuple:
    end = now.date()
    return ((end - timedelta(days=WINDOW_DAYS)).isoformat(), end.isoformat())


def _fresh(source, fetched_at, now, extra=None) -> dict:
    """One per-input freshness record. threshold_seconds stays null until a threshold is approved, so
    a missing age can never be read as 'fresh'."""
    age = int((now - fetched_at).total_seconds()) if fetched_at is not None else None
    rec = {"source": source,
           "fetched_at": fetched_at.isoformat() if fetched_at is not None else None,
           "evaluated_at": now.isoformat(), "age_seconds": age, "threshold_seconds": None}
    if extra:
        rec.update(extra)
    return rec


def _policy_gate(policy: Optional[ProtectionPolicy]) -> Optional[str]:
    if policy is None:
        return SKIP_SCOPE_MISMATCH
    if not policy.enabled:
        return SKIP_POLICY_DISABLED
    if policy.consent_at is None:
        return SKIP_CONSENT_ABSENT
    if policy.consent_revoked_at is not None:
        return SKIP_CONSENT_REVOKED
    return None


async def _thresholds(policy: ProtectionPolicy) -> Thresholds:
    return Thresholds(
        emergency_abs=Decimal(str(policy.emergency_abs if policy.emergency_abs is not None else 0)),
        target_margin_pct=Decimal(str(policy.target_margin_pct if policy.target_margin_pct is not None else 10)),
        emergency_pct=(Decimal(str(policy.emergency_pct)) if policy.emergency_pct is not None else None))


# ── price (catalog, warning-only) ──────────────────────────────────────────────────────────────
async def _latest_price_row(db, store_id, product_id, source) -> Tuple[Optional[Decimal], Optional[datetime]]:
    row = (await db.execute(
        select(ImportedProductRow.price, ImportedProductRow.fetched_at).where(
            ImportedProductRow.marketplace_store_id == store_id,
            ImportedProductRow.product_id == product_id,
            ImportedProductRow.source == source,
            ImportedProductRow.price.isnot(None)).order_by(
            ImportedProductRow.fetched_at.desc()).limit(1))).first()
    if row is None:
        return None, None
    return Decimal(str(row[0])), row[1]


async def _resolve_price(db, *, store_id, product_id, marketplace, now):
    """Choose the catalog price through the SAME source policy as every other metric — API and CSV
    candidates resolved separately, conflict preserved, preference=api without coverage → no_data.
    The chosen price is ALWAYS warning-only (promo_price_proven=False) and its currency is unproven
    (ImportedProductRow stores no currency) → currency_unconfirmed."""
    csv_price, csv_fetched = await _latest_price_row(db, store_id, product_id, "csv")
    api_price, api_fetched = await _latest_price_row(db, store_id, product_id, "api")
    res = await resolve_source(db, store_id=store_id, marketplace=marketplace, metric_type="price",
                               period=None, api_value=api_price, csv_value=csv_price, now=now)
    fetched = api_fetched if res.source == "api" else csv_fetched if res.source == "csv" else None
    return res.value, res.source, bool(res.conflict), fetched


# ── cogs ───────────────────────────────────────────────────────────────────────────────────────
async def _cogs_line(db, product: Product) -> CostLine:
    ext = product.external_product_id or product.sku
    if not ext:
        return CostLine("cogs", MISSING)
    rows = (await db.execute(
        select(PhysicalProduct.cogs).select_from(ProductListing).join(
            PhysicalProduct, PhysicalProduct.id == ProductListing.physical_product_id).where(
            ProductListing.user_id == product.user_id,
            func.upper(ProductListing.external_id) == str(ext).upper(),
            PhysicalProduct.cogs.isnot(None)))).scalars().all()
    if len(rows) != 1:                       # missing OR ambiguous → never a guess
        return CostLine("cogs", MISSING)
    return CostLine("cogs", MANUAL_ADD, Decimal(str(rows[0])), source="manual")


# ── units (source-consistent denominators) ───────────────────────────────────────────────────────
async def _csv_units(db, *, store_id, product_id, period) -> int:
    """CSV units for ONE store+product+window. Store-scoped: never merged across stores."""
    total = (await db.execute(select(func.coalesce(func.sum(ImportedFinanceRow.quantity), 0)).where(
        ImportedFinanceRow.marketplace_store_id == store_id,
        ImportedFinanceRow.product_id == product_id,
        ImportedFinanceRow.source == "csv",
        ImportedFinanceRow.date >= period[0], ImportedFinanceRow.date <= period[1]))).scalar()
    return int(total or 0)


# ── real freshness provenance (MAX fetched_at of the CHOSEN source only) ─────────────────────────
async def _csv_fin_fetched_at(db, *, store_id, product_id, period) -> Optional[datetime]:
    return (await db.execute(select(func.max(ImportedFinanceRow.fetched_at)).where(
        ImportedFinanceRow.marketplace_store_id == store_id,
        ImportedFinanceRow.product_id == product_id,
        ImportedFinanceRow.source == "csv",
        ImportedFinanceRow.date >= period[0], ImportedFinanceRow.date <= period[1]))).scalar()


async def _api_op_fetched_at(db, *, store_id, product_id, marketplace, metric_type, period) -> Optional[datetime]:
    auth = da.authoritative(marketplace, metric_type)
    if auth is None:
        return None
    return (await db.execute(select(func.max(MarketplaceOperation.fetched_at)).where(
        MarketplaceOperation.marketplace_store_id == store_id,
        MarketplaceOperation.product_id == product_id,
        MarketplaceOperation.source == "api",
        MarketplaceOperation.provider_dataset.in_(list(auth.datasets)),
        MarketplaceOperation.operation_type.in_(list(auth.operation_types)),
        *_period_filter(period)))).scalar()


async def _csv_returns_fetched_at(db, *, store_id, product_id, period) -> Optional[datetime]:
    return (await db.execute(select(func.max(ImportedReturnRow.fetched_at)).where(
        ImportedReturnRow.marketplace_store_id == store_id,
        ImportedReturnRow.product_id == product_id,
        ImportedReturnRow.source == "csv",
        ImportedReturnRow.date >= period[0], ImportedReturnRow.date <= period[1]))).scalar()


def _freshness_note(source, fetched_at, now, reason) -> dict:
    """Per-metric freshness with the REAL fetched_at of the chosen source. A chosen value whose
    fetched_at cannot be proven is freshness_unknown (age stays null — never a fabricated age)."""
    note = _fresh(source, fetched_at, now)
    note["reason"] = reason
    if source is not None and fetched_at is None:
        note["freshness"] = "freshness_unknown"
    return note


# ── taxes ───────────────────────────────────────────────────────────────────────────────────────
def _tax_differs(rows: Sequence[ProtectionTaxSetting]) -> bool:
    keys = {(r.tax_mode, str(r.tax_rate), str(r.tax_per_unit), r.applied_to) for r in rows}
    return len(keys) > 1


def _pick_tax(rows: Sequence[ProtectionTaxSetting], evaluated_at) -> Tuple[Optional[ProtectionTaxSetting], bool, str]:
    """Latest confirmed setting effective at evaluated_at. Two rows sharing the max effective_from
    with different values → ambiguous (conflict). Pure/deterministic so the conflict branch is
    unit-testable even though the model's UNIQUE(policy_id) makes two persisted rows impossible."""
    elig = [r for r in rows
            if r.seller_confirmed_at is not None and (r.effective_from is None or r.effective_from <= evaluated_at)]
    if not elig:
        return None, False, "tax_setting_unconfirmed"
    elig.sort(key=lambda r: (r.effective_from or datetime.min), reverse=True)
    top_eff = elig[0].effective_from or datetime.min
    same = [r for r in elig if (r.effective_from or datetime.min) == top_eff]
    if len(same) > 1 and _tax_differs(same):
        return None, True, "tax_ambiguous"
    return elig[0], False, "ok"


async def _tax_line(db, policy_id, price, evaluated_at) -> Tuple[CostLine, bool, str]:
    """A single canonical 'taxes' cost line. Absence of a confirmed setting is NOT zero → MISSING
    (incomplete). tax_mode=none (confirmed) → explicit confirmed zero. Percent on seller_sale_revenue
    → price×rate/100 (needs a price). Percent on contribution is circular under Model A and cannot be
    proven pre-compute → MISSING. Tax carries no stored currency, so none is invented."""
    rows = (await db.execute(select(ProtectionTaxSetting).where(
        ProtectionTaxSetting.policy_id == policy_id))).scalars().all()
    setting, conflict, reason = _pick_tax(rows, evaluated_at)
    if conflict:
        return CostLine("taxes", MISSING), True, reason
    if setting is None:
        return CostLine("taxes", MISSING), False, reason
    if setting.tax_mode == "none":
        return CostLine("taxes", CONFIRMED_ZERO, source="manual"), False, "tax_none_confirmed"
    if setting.tax_mode == "per_unit":
        return CostLine("taxes", MANUAL_ADD, Decimal(str(setting.tax_per_unit)), source="manual"), False, "tax_per_unit"
    # percent
    if setting.applied_to == "seller_sale_revenue":
        if price is None:
            return CostLine("taxes", MISSING), False, "tax_percent_needs_price"
        amt = (Decimal(str(price)) * Decimal(str(setting.tax_rate)) / Decimal(100)).quantize(Decimal("0.01"))
        return CostLine("taxes", MANUAL_ADD, amt, source="manual"), False, "tax_percent_revenue"
    # percent on contribution — circular in Model A, cannot be resolved to a fixed per-unit cost
    return CostLine("taxes", MISSING), False, "tax_percent_on_contribution_unsupported"


# ── seller additional costs ──────────────────────────────────────────────────────────────────────
async def _additional_lines(db, policy_id, price, now) -> Tuple[list, set]:
    rows = (await db.execute(select(ProtectionAdditionalCost).where(
        ProtectionAdditionalCost.policy_id == policy_id,
        ProtectionAdditionalCost.enabled.is_(True)))).scalars().all()
    lines, keys, dup = [], set(), set()
    for r in rows:
        if r.seller_confirmed_at is None or (r.effective_from and r.effective_from > now):
            continue
        name = (r.name or "").strip().lower()
        key = name if name in COST_KEYS else f"additional:{r.name}"
        if key in keys:
            dup.add(key)
        keys.add(key)
        if r.calculation_type == "per_unit":
            lines.append(CostLine(key, MANUAL_ADD, Decimal(str(r.amount)), source="manual", currency=r.currency))
        else:  # percent_of_revenue — needs the candidate price; a percent is NEVER applied as flat rubles
            if price is None:
                lines.append(CostLine(key, MISSING, source="manual", currency=r.currency))
            else:
                amt = (Decimal(str(price)) * Decimal(str(r.amount)) / Decimal(100)).quantize(Decimal("0.01"))
                lines.append(CostLine(key, MANUAL_ADD, amt, source="manual", currency=r.currency))
    return lines, dup


# ── returns ──────────────────────────────────────────────────────────────────────────────────────
async def _returns(db, *, store_id, product_id, marketplace, period, now) -> Tuple[Optional[ReturnsStats], dict, bool]:
    """Returns follow the SAME source policy as every metric (metric_type='returns').

      * CSV candidate: sold=CSV finance units, returned=CSV returns feed (cancellations excluded).
      * API candidate: returned=API returns count; the API sold denominator is UNAVAILABLE (orders are
        not sales), so an API-sourced return rate cannot be formed — NO CSV sold fallback.
      * preference=csv → the CSV pair; preference=api → API returned with no sold denom = incomplete;
        auto → the resolver, conflict preserved. No hidden CSV for a non-CSV marketplace.

    cost_per_return stays unconfirmed on master, so a complete calculation never arises here."""
    csv_sold = await _csv_units(db, store_id=store_id, product_id=product_id, period=period)
    csv_returned = int((await db.execute(select(func.coalesce(func.sum(ImportedReturnRow.returns_qty), 0)).where(
        ImportedReturnRow.marketplace_store_id == store_id,
        ImportedReturnRow.product_id == product_id,
        ImportedReturnRow.source == "csv",
        ImportedReturnRow.date >= period[0], ImportedReturnRow.date <= period[1]))).scalar() or 0)
    # csv_returned is the CSV pair's numerator (0 real returns is DATA, not absence) whenever CSV sales
    # exist — so a CSV preference forms a real (sold, returned) pair instead of collapsing to no_data.
    api_returned = await api_product_count(db, store_id=store_id, product_id=product_id,
                                           marketplace=marketplace, metric_type="returns", period=period)
    res = await resolve_source(db, store_id=store_id, marketplace=marketplace, metric_type="returns",
                               period=period, api_value=api_returned, csv_value=csv_returned, now=now)
    if res.source == "csv":
        stats = ReturnsStats(sold_units=csv_sold, returned_units=csv_returned, cost_per_return=None,
                             cost_proven=False, window_days=WINDOW_DAYS)
        fetched = await _csv_returns_fetched_at(db, store_id=store_id, product_id=product_id, period=period)
        note = _freshness_note("csv", fetched, now, res.reason or "returns_csv")
    elif res.source == "api":
        # API returned, but NO compatible API sold denominator (orders ≠ sales) → cannot form a rate,
        # and NEVER a CSV sold fallback. Recorded as incomplete with the honest reason.
        stats = ReturnsStats(sold_units=0, returned_units=int(api_returned or 0), cost_per_return=None,
                             cost_proven=False, window_days=WINDOW_DAYS)
        fetched = await _api_op_fetched_at(db, store_id=store_id, product_id=product_id,
                                           marketplace=marketplace, metric_type="returns", period=period)
        note = _freshness_note("api", fetched, now, "api_sale_quantity_unavailable")
    else:
        stats = ReturnsStats(sold_units=0, returned_units=0, cost_per_return=None,
                             cost_proven=False, window_days=WINDOW_DAYS)
        note = _freshness_note(None, None, now, res.reason or "returns_no_data")
    note["returns_source"] = res.source
    return stats, note, bool(res.conflict)


# ── gather ───────────────────────────────────────────────────────────────────────────────────────
async def _gather(db, *, policy: ProtectionPolicy, store: MarketplaceStore, product: Product,
                  now: datetime) -> ContributionInputs:
    marketplace = store.marketplace
    period = _period(now)

    price, price_source, price_conflict, price_fetched = await _resolve_price(
        db, store_id=store.id, product_id=product.id, marketplace=marketplace, now=now)

    csv_units = await _csv_units(db, store_id=store.id, product_id=product.id, period=period)

    cost_lines: list[CostLine] = []
    conflicts: list[str] = []
    pf = _fresh(price_source, price_fetched, now, {"currency_unconfirmed": True, "warning_only": True})
    if price_source is not None and price_fetched is None:
        pf["freshness"] = "freshness_unknown"
    freshness: dict = {"candidate_price": pf}
    if price_conflict:
        conflicts.append("price")
    # The sale currency of the catalog price cannot be proven (no stored currency anywhere), so the
    # ECONOMIC result must be blocked: a currency_unconfirmed conflict forces calculation_status to
    # conflict → economic_verdict / projected_contribution stay NULL and the price is warning-only.
    if price is not None:
        conflicts.append("currency_unconfirmed")

    # money metrics — a per-unit cost needs the money magnitude AND a proven SALE-quantity denominator
    # from a COMPATIBLE source. CSV money ⇒ CSV finance units (same finance source). API money has NO
    # proven sale denominator (orders are operational, not sales; an order may be cancelled or unbought)
    # → MISSING (api_sale_quantity_unavailable). Never orders-as-sales, never API money ÷ CSV units.
    for metric_type, (cost_key, _csv) in MONEY_METRICS.items():
        mm = await resolve_metric_money(db, store_id=store.id, product_id=product.id,
                                        marketplace=marketplace, metric_type=metric_type,
                                        period=period, now=now)
        if mm.source == "csv":
            fetched = await _csv_fin_fetched_at(db, store_id=store.id, product_id=product.id, period=period)
        elif mm.source == "api":
            fetched = await _api_op_fetched_at(db, store_id=store.id, product_id=product.id,
                                               marketplace=marketplace, metric_type=metric_type, period=period)
        else:
            fetched = None

        if mm.conflict:
            conflicts.append(cost_key)
            cost_lines.append(CostLine(cost_key, MISSING, source=mm.source))
            reason = "source_conflict"
        elif mm.amount is None:
            cost_lines.append(CostLine(cost_key, MISSING, source=mm.source))
            reason = mm.reason or "no_data"
        elif mm.source == "csv":
            if not csv_units or csv_units <= 0:
                cost_lines.append(CostLine(cost_key, MISSING, source="csv"))
                reason = "no_source_consistent_quantity"
            else:
                per_unit = (mm.amount / Decimal(csv_units)).quantize(Decimal("0.01"))
                cost_lines.append(CostLine(cost_key, PROVIDER_OP, per_unit, source="csv"))
                reason = mm.reason or "csv"
        else:   # api money — no proven sale-quantity denominator
            cost_lines.append(CostLine(cost_key, MISSING, source="api"))
            reason = "api_sale_quantity_unavailable"
        note = _freshness_note(mm.source, fetched, now, reason)
        note["denominator_source"] = "csv" if mm.source == "csv" else None
        freshness[metric_type] = note

    # storage / acquiring / last_mile: only from a seller additional cost; else MISSING (never 0)
    add_lines, add_dups = await _additional_lines(db, policy.id, price, now)
    conflicts += list(add_dups)
    declared = {ln.cost_key for ln in add_lines}
    for key in _NO_SOURCE_KEYS:
        if key not in declared:
            cost_lines.append(CostLine(key, MISSING))
    cost_lines += add_lines

    # taxes — never assumed zero; a confirmed setting is required for a complete calculation
    tax_line, tax_conflict, tax_reason = await _tax_line(db, policy.id, price, now)
    if tax_conflict:
        conflicts.append("taxes")
    cost_lines.append(tax_line)
    freshness["taxes"] = {"reason": tax_reason, "source": "manual",
                          "evaluated_at": now.isoformat(), "fetched_at": None,
                          "age_seconds": None, "threshold_seconds": None}

    # advertising only when the seller opted in
    if policy.include_ad_spend:
        cost_lines.append(CostLine("advertising", MISSING))   # attribution unproven on master

    cogs = await _cogs_line(db, product)
    returns, ret_note, ret_conflict = await _returns(
        db, store_id=store.id, product_id=product.id, marketplace=marketplace, period=period, now=now)
    freshness["returns"] = ret_note
    if ret_conflict:
        conflicts.append("returns")

    # provenance (NOT marketplace freshness) for seller-confirmed inputs
    freshness["cogs"] = {"source": "manual", "provenance": "physical_product_cogs",
                         "evaluated_at": now.isoformat(), "threshold_seconds": None}
    freshness["taxes"]["provenance"] = "seller_confirmed_tax_setting"

    # currency of the SALE is never inferred — not from a manual-cost currency, not a default RUB.
    # It is unprovable on master (see currency_unconfirmed conflict above), so it stays None.
    return ContributionInputs(
        marketplace=marketplace, store_id=store.id, product_id=product.id, promo_id=None,
        candidate_buyer_price=price, promo_price_proven=False, currency=None,
        cogs=cogs, cost_lines=cost_lines, commission_official_tariff=False,
        returns=returns, include_ad_spend=policy.include_ad_spend,
        source_conflicts=conflicts, provider_capability_confirmed=False,
        thresholds=await _thresholds(policy), evaluated_at=now, freshness_ages=freshness)


# ── persistence ──────────────────────────────────────────────────────────────────────────────────
def _snapshot(res_evidence: dict) -> dict:
    """Stored inputs_snapshot = the engine evidence plus a top-level freshness_gate_configured=false.
    No threshold is configured yet, so the gate is explicitly off and actionability cannot be
    executable on freshness grounds."""
    snap = dict(res_evidence)
    snap["freshness_gate_configured"] = False
    return snap


async def evaluate_policy_product(
    session: AsyncSession, *, policy_id: str, marketplace_store_id: str, product_id: str,
    evaluation_run_id: str, now: Optional[datetime] = None,
) -> EvalOutcome:
    """Evaluate ONE product under ONE policy and persist one append-only Evaluation (idempotent per
    (policy, product, run)). Gates return a typed SkipResult and write nothing. An invalid run id is
    rejected before any gather/insert."""
    run_id = _validate_run_id(evaluation_run_id)
    now = now or datetime.utcnow()
    policy = (await session.execute(select(ProtectionPolicy).where(
        ProtectionPolicy.id == policy_id).with_for_update())).scalars().first()
    gate = _policy_gate(policy)
    if gate:
        return SkipResult(gate, policy_id, marketplace_store_id, product_id)
    if policy.product_id is not None and policy.product_id != product_id:
        return SkipResult(SKIP_SCOPE_MISMATCH, policy_id, marketplace_store_id, product_id)
    store = (await session.execute(select(MarketplaceStore).where(
        MarketplaceStore.id == marketplace_store_id))).scalars().first()
    if store is None or store.status != "active" or store.id != policy.marketplace_store_id:
        return SkipResult(SKIP_STORE_ARCHIVED if (store and store.status != "active")
                          else SKIP_SCOPE_MISMATCH, policy_id, marketplace_store_id, product_id)
    placement = (await session.execute(select(ProductPlacement).where(
        ProductPlacement.marketplace_store_id == marketplace_store_id,
        ProductPlacement.product_id == product_id))).scalars().first()
    if placement is None or placement.status != "active":
        return SkipResult(SKIP_PLACEMENT_INACTIVE, policy_id, marketplace_store_id, product_id)
    product = (await session.execute(select(Product).where(Product.id == product_id))).scalars().first()
    if product is None:
        return SkipResult(SKIP_SCOPE_MISMATCH, policy_id, marketplace_store_id, product_id)

    inp = await _gather(session, policy=policy, store=store, product=product, now=now)
    res = compute(inp)

    ev = ProtectionEvaluation(
        id=str(uuid.uuid4()), evaluation_run_id=run_id, policy_id=policy_id,
        marketplace_store_id=marketplace_store_id, product_id=product_id,
        projected_contribution=res.projected_contribution, contribution_pct=res.contribution_pct,
        verdict=_VERDICT_MAP.get(res.calculation_status, res.calculation_status),
        economic_verdict=res.economic_verdict,
        actionability=res.actionability, missing_fields=res.missing_fields, reasons=res.reasons,
        inputs_snapshot=_snapshot(res.evidence), evaluated_at=now)
    try:
        async with session.begin_nested():   # savepoint: a duplicate run rolls back only this insert
            session.add(ev)
            await session.flush()
    except IntegrityError:
        existing = (await session.execute(select(ProtectionEvaluation).where(
            ProtectionEvaluation.policy_id == policy_id,
            ProtectionEvaluation.product_id == product_id,
            ProtectionEvaluation.evaluation_run_id == run_id))).scalars().first()
        return existing
    return ev


async def evaluate_policy(
    session: AsyncSession, *, policy_id: str, evaluation_run_id: str, now: Optional[datetime] = None,
) -> List[EvalOutcome]:
    """Resolve the policy's target(s) and evaluate each. A product policy → one product; a store-wide
    policy → each ACTIVE placement, one Evaluation each, skipping a product that has its own enabled
    policy (override). Never an average store-wide Evaluation. Invalid run id rejected up front."""
    run_id = _validate_run_id(evaluation_run_id)
    now = now or datetime.utcnow()
    policy = (await session.execute(select(ProtectionPolicy).where(
        ProtectionPolicy.id == policy_id))).scalars().first()
    gate = _policy_gate(policy)
    if gate:
        return [SkipResult(gate, policy_id)]
    store = (await session.execute(select(MarketplaceStore).where(
        MarketplaceStore.id == policy.marketplace_store_id))).scalars().first()
    if store is None or store.status != "active":
        return [SkipResult(SKIP_STORE_ARCHIVED, policy_id, policy.marketplace_store_id)]

    if policy.product_id is not None:
        return [await evaluate_policy_product(
            session, policy_id=policy_id, marketplace_store_id=store.id,
            product_id=policy.product_id, evaluation_run_id=run_id, now=now)]

    # store-wide: one Evaluation per ACTIVE placement, skipping products with their own enabled policy
    placements = (await session.execute(select(ProductPlacement.product_id).where(
        ProductPlacement.marketplace_store_id == store.id,
        ProductPlacement.status == "active"))).scalars().all()
    overridden = set((await session.execute(select(ProtectionPolicy.product_id).where(
        ProtectionPolicy.marketplace_store_id == store.id,
        ProtectionPolicy.product_id.isnot(None),
        ProtectionPolicy.enabled.is_(True)))).scalars().all())
    out: List[EvalOutcome] = []
    for pid in placements:
        if pid in overridden:
            out.append(SkipResult(SKIP_OVERRIDDEN, policy_id, store.id, pid))
            continue
        out.append(await evaluate_policy_product(
            session, policy_id=policy_id, marketplace_store_id=store.id, product_id=pid,
            evaluation_run_id=run_id, now=now))
    return out
