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

from services.protection.contribution import (
    ContributionInputs, Thresholds, CostLine, ReturnsStats, compute,
    PROVIDER_OP, MANUAL_ADD, CONFIRMED_ZERO, MISSING, COST_KEYS, DEFAULT_RETURN_WINDOW_DAYS,
)
from services.source_policy.resolver import resolve_source
from services.source_policy.product_money_reader import api_product_count
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


async def _api_units(db, *, store_id, product_id, marketplace, period) -> Optional[int]:
    """API units (orders) for ONE store+product+window, or None when the API is not a source."""
    return await api_product_count(db, store_id=store_id, product_id=product_id,
                                   marketplace=marketplace, metric_type="orders", period=period)


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
async def _returns(db, *, store_id, product_id, period) -> ReturnsStats:
    """sold and returned units for ONE store+product, one window, one source (CSV). Returned units
    come from the returns feed only (cancellations are a different feed, never counted here). API sold
    is never paired with CSV returns. cost_per_return is NOT provable on master → cost_proven=False."""
    sold = await _csv_units(db, store_id=store_id, product_id=product_id, period=period)
    returned = int((await db.execute(select(func.coalesce(func.sum(ImportedReturnRow.returns_qty), 0)).where(
        ImportedReturnRow.marketplace_store_id == store_id,
        ImportedReturnRow.product_id == product_id,
        ImportedReturnRow.source == "csv",
        ImportedReturnRow.date >= period[0], ImportedReturnRow.date <= period[1]))).scalar() or 0)
    return ReturnsStats(sold_units=sold, returned_units=returned, cost_per_return=None,
                        cost_proven=False, window_days=WINDOW_DAYS)


# ── gather ───────────────────────────────────────────────────────────────────────────────────────
async def _gather(db, *, policy: ProtectionPolicy, store: MarketplaceStore, product: Product,
                  now: datetime) -> ContributionInputs:
    marketplace = store.marketplace
    period = _period(now)

    price, price_source, price_conflict, price_fetched = await _resolve_price(
        db, store_id=store.id, product_id=product.id, marketplace=marketplace, now=now)

    csv_units = await _csv_units(db, store_id=store.id, product_id=product.id, period=period)
    api_units = await _api_units(db, store_id=store.id, product_id=product.id,
                                 marketplace=marketplace, period=period)

    cost_lines: list[CostLine] = []
    conflicts: list[str] = []
    freshness: dict = {
        "candidate_price": _fresh(price_source, price_fetched, now,
                                  {"currency_unconfirmed": True, "warning_only": True}),
    }
    if price_conflict:
        conflicts.append("price")

    # money metrics — each divided by a SOURCE-CONSISTENT denominator (API money ⇒ API units, CSV
    # money ⇒ CSV units). No blending: a mismatch makes the metric MISSING, never a guessed rate.
    for metric_type, (cost_key, _csv) in MONEY_METRICS.items():
        mm = await resolve_metric_money(db, store_id=store.id, product_id=product.id,
                                        marketplace=marketplace, metric_type=metric_type,
                                        period=period, now=now)
        denom = csv_units if mm.source == "csv" else api_units if mm.source == "api" else None
        note = {"source": mm.source, "reason": mm.reason,
                "denominator_source": mm.source, "denominator_units": denom,
                "evaluated_at": now.isoformat(), "fetched_at": None,
                "age_seconds": None, "threshold_seconds": None}
        if mm.conflict:
            conflicts.append(cost_key)
            cost_lines.append(CostLine(cost_key, MISSING, source=mm.source))
        elif mm.amount is None:
            cost_lines.append(CostLine(cost_key, MISSING, source=mm.source))
        elif not denom or denom <= 0:
            note["reason"] = "no_source_consistent_quantity"
            cost_lines.append(CostLine(cost_key, MISSING, source=mm.source))
        else:
            per_unit = (mm.amount / Decimal(denom)).quantize(Decimal("0.01"))
            cost_lines.append(CostLine(cost_key, PROVIDER_OP, per_unit, source=mm.source))
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
    returns = await _returns(db, store_id=store.id, product_id=product.id, period=period)

    # currency is PROVEN from confirmed manual costs, never defaulted. One unanimous currency ⇒ that;
    # none ⇒ None; several different ⇒ None here and the engine raises currency_mismatch from the
    # per-line currencies. The catalog price is currency_unconfirmed and never forces RUB.
    manual_currencies = {ln.currency for ln in add_lines if ln.currency}
    currency = next(iter(manual_currencies)) if len(manual_currencies) == 1 else None

    return ContributionInputs(
        marketplace=marketplace, store_id=store.id, product_id=product.id, promo_id=None,
        candidate_buyer_price=price, promo_price_proven=False, currency=currency,
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
