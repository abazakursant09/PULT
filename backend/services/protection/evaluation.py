"""
PULT-LAUNCH-2.5B — runtime protection evaluation service. Feature OFF.

Resolves the target (a product policy → its one placed product; a store-wide policy → each ACTIVE
placement, one Evaluation each, never an average, skipping products that have their own enabled
policy), gathers ContributionInputs from EXISTING readers (source policy per metric, never summing
API+CSV, conflicts preserved), calls the UNCHANGED 2.4 engine, and writes ONE append-only
ProtectionEvaluation per (policy, product, run) in a single transaction.

On master the honest result is incomplete / manual_only: there is no proven promo price (catalog
price is warning-only → promo_price_proven=False), no official commission tariff
(commission_official_tariff=False), no provable per-return cost, and no way to confirm
storage/acquiring — so `executable` never appears. That is correct, not a reason to relax a gate.

This service NEVER creates a ProtectionActionState, an ExecutionLog, a provider call, or a
notification, and it is not wired to a scheduler.
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal
from typing import List, Optional, Union

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from models.protection import (
    ProtectionPolicy, ProtectionEvaluation, ProtectionAdditionalCost,
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
    PROVIDER_OP, MANUAL_ADD, MISSING, COST_KEYS, DEFAULT_RETURN_WINDOW_DAYS,
)
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


@dataclass
class SkipResult:
    """A typed non-evaluation: a gate refused, so NO ProtectionEvaluation with invented values."""
    reason: str
    policy_id: Optional[str]
    marketplace_store_id: Optional[str] = None
    product_id: Optional[str] = None
    skipped: bool = True


EvalOutcome = Union[ProtectionEvaluation, SkipResult]


def _period(now: datetime) -> tuple:
    end = now.date()
    return ((end - timedelta(days=WINDOW_DAYS)).isoformat(), end.isoformat())


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


async def _latest_price(db, store_id, product_id) -> Optional[Decimal]:
    row = (await db.execute(
        select(ImportedProductRow.price).where(
            ImportedProductRow.marketplace_store_id == store_id,
            ImportedProductRow.product_id == product_id,
            ImportedProductRow.price.isnot(None)).order_by(ImportedProductRow.fetched_at.desc()).limit(1))).scalar()
    return None if row is None else Decimal(str(row))   # CATALOG price (warning-only), never promo


async def _cogs_line(db, product: Product) -> CostLine:
    # cogs from PhysicalProduct via ProductListing, matched on the seller's own listing identity.
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


async def _units_sold(db, *, user_id, marketplace, product_id, period) -> int:
    total = (await db.execute(select(func.coalesce(func.sum(ImportedFinanceRow.quantity), 0)).where(
        ImportedFinanceRow.user_id == user_id, ImportedFinanceRow.product_id == product_id,
        ImportedFinanceRow.source == "csv",
        ImportedFinanceRow.date >= period[0], ImportedFinanceRow.date <= period[1]))).scalar()
    return int(total or 0)


async def _additional_lines(db, policy_id, now) -> tuple[list, set]:
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
        lines.append(CostLine(key, MANUAL_ADD, Decimal(str(r.amount)), source="manual",
                              currency=r.currency))
    return lines, dup


async def _returns(db, *, user_id, marketplace, store_id, product_id, period) -> ReturnsStats:
    sold = await _units_sold(db, user_id=user_id, marketplace=marketplace, product_id=product_id, period=period)
    returned = int((await db.execute(select(func.coalesce(func.sum(ImportedReturnRow.returns_qty), 0)).where(
        ImportedReturnRow.user_id == user_id, ImportedReturnRow.product_id == product_id,
        ImportedReturnRow.date >= period[0], ImportedReturnRow.date <= period[1]))).scalar() or 0)
    # cost_per_return is NOT provable on master (no proven reverse-logistics / non-recoverable value):
    # cost_proven stays False → compute() marks return_cost_unconfirmed. Never full cogs, never a
    # return_amount with unproven semantics.
    return ReturnsStats(sold_units=sold, returned_units=returned, cost_per_return=None,
                        cost_proven=False, window_days=WINDOW_DAYS)


async def _gather(db, *, policy: ProtectionPolicy, store: MarketplaceStore, product: Product,
                  now: datetime) -> ContributionInputs:
    marketplace = store.marketplace
    period = _period(now)
    price = await _latest_price(db, store.id, product.id)
    units = await _units_sold(db, user_id=product.user_id, marketplace=marketplace,
                              product_id=product.id, period=period)

    cost_lines: list[CostLine] = []
    conflicts: list[str] = []
    freshness: dict = {}

    # money metrics (commission/logistics/penalties/deductions) via the source policy, per unit
    for metric_type, (cost_key, _csv) in MONEY_METRICS.items():
        mm = await resolve_metric_money(db, user_id=product.user_id, store_id=store.id,
                                        product_id=product.id, marketplace=marketplace,
                                        metric_type=metric_type, period=period, now=now)
        freshness[metric_type] = {"source": mm.source, "reason": mm.reason}
        if mm.conflict:
            conflicts.append(cost_key)
            cost_lines.append(CostLine(cost_key, MISSING, source=mm.source))
        elif mm.amount is None or units <= 0:
            cost_lines.append(CostLine(cost_key, MISSING, source=mm.source))
        else:
            per_unit = (mm.amount / Decimal(units)).quantize(Decimal("0.01"))
            cost_lines.append(CostLine(cost_key, PROVIDER_OP, per_unit, source=mm.source))

    # storage / acquiring / last_mile: only from a seller additional cost; else MISSING (never 0)
    add_lines, add_dups = await _additional_lines(db, policy.id, now)
    conflicts += list(add_dups)
    declared = {ln.cost_key for ln in add_lines}
    for key in _NO_SOURCE_KEYS:
        if key not in declared:
            cost_lines.append(CostLine(key, MISSING))
    cost_lines += add_lines

    # advertising only when the seller opted in
    if policy.include_ad_spend:
        cost_lines.append(CostLine("advertising", MISSING))   # attribution unproven on master

    cogs = await _cogs_line(db, product)
    returns = await _returns(db, user_id=product.user_id, marketplace=marketplace,
                             store_id=store.id, product_id=product.id, period=period)

    return ContributionInputs(
        marketplace=marketplace, store_id=store.id, product_id=product.id, promo_id=None,
        candidate_buyer_price=price, promo_price_proven=False, currency="RUB",
        cogs=cogs, cost_lines=cost_lines, commission_official_tariff=False,
        returns=returns, include_ad_spend=policy.include_ad_spend,
        source_conflicts=conflicts, provider_capability_confirmed=False,
        thresholds=await _thresholds(policy), evaluated_at=now, freshness_ages=freshness)


async def evaluate_policy_product(
    session: AsyncSession, *, policy_id: str, marketplace_store_id: str, product_id: str,
    evaluation_run_id: str, now: Optional[datetime] = None,
) -> EvalOutcome:
    """Evaluate ONE product under ONE policy and persist one append-only Evaluation (idempotent per
    (policy, product, run)). Gates return a typed SkipResult and write nothing."""
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
        id=str(uuid.uuid4()), evaluation_run_id=evaluation_run_id, policy_id=policy_id,
        marketplace_store_id=marketplace_store_id, product_id=product_id,
        projected_contribution=res.projected_contribution, contribution_pct=res.contribution_pct,
        verdict=res.calculation_status, economic_verdict=res.economic_verdict,
        actionability=res.actionability, missing_fields=res.missing_fields, reasons=res.reasons,
        inputs_snapshot=res.evidence, evaluated_at=now)
    try:
        async with session.begin_nested():   # savepoint: a duplicate run rolls back only this insert
            session.add(ev)
            await session.flush()
    except IntegrityError:
        existing = (await session.execute(select(ProtectionEvaluation).where(
            ProtectionEvaluation.policy_id == policy_id,
            ProtectionEvaluation.product_id == product_id,
            ProtectionEvaluation.evaluation_run_id == evaluation_run_id))).scalars().first()
        return existing
    return ev


async def evaluate_policy(
    session: AsyncSession, *, policy_id: str, evaluation_run_id: str, now: Optional[datetime] = None,
) -> List[EvalOutcome]:
    """Resolve the policy's target(s) and evaluate each. A product policy → one product; a store-wide
    policy → each ACTIVE placement, one Evaluation each, skipping a product that has its own enabled
    policy (override). Never an average store-wide Evaluation."""
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
            product_id=policy.product_id, evaluation_run_id=evaluation_run_id, now=now)]

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
            out.append(SkipResult("overridden_by_product_policy", policy_id, store.id, pid))
            continue
        out.append(await evaluate_policy_product(
            session, policy_id=policy_id, marketplace_store_id=store.id, product_id=pid,
            evaluation_run_id=evaluation_run_id, now=now))
    return out
