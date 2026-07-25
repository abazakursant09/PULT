"""Source-policy backend endpoints (PULT-LAUNCH-1.4.5H).

Lets a seller choose, per metric, whether a store's numbers come from the API or from CSV — the
backend contract the mapping UI (1.4.5I) will drive. No screen here.

  * GET   /api/marketplace-stores/{store_id}/source-policy
  * PATCH /api/marketplace-stores/{store_id}/source-policy/{metric_type}

Ownership is Store → Account → Workspace. A foreign store and a missing one return the SAME 404, so
the reply cannot be used to probe for ids. No secret or internal id is ever returned.
"""
from __future__ import annotations

import uuid
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from database import get_db
from dependencies import get_current_user
from models.marketplace_account import MarketplaceAccount
from models.marketplace_store import MarketplaceStore
from models.store_data_source_policy import METRIC_TYPES, PREFERENCES, StoreDataSourcePolicy
from models.user import User
from schemas.marketplace import SourcePolicyMetric, SourcePolicyOut, SourcePolicyPatch
from services.source_policy import dataset_authority as da
from services.source_policy import resolver
from services.workspace_resolver import WorkspaceMissing, resolve_workspace_id

router = APIRouter()


async def _owned_store(db: AsyncSession, user: User, store_id: str) -> MarketplaceStore:
    try:
        workspace_id = await resolve_workspace_id(db, str(user.id))
    except WorkspaceMissing:
        raise HTTPException(500, "workspace could not be resolved")
    store = (await db.execute(
        select(MarketplaceStore)
        .join(MarketplaceAccount, MarketplaceAccount.id == MarketplaceStore.marketplace_account_id)
        .where(MarketplaceStore.id == store_id,
               MarketplaceAccount.workspace_id == workspace_id))).scalars().first()
    if store is None:
        raise HTTPException(404, "store not found")
    return store


def _limitation(marketplace: str, metric_type: str) -> str | None:
    if metric_type in da.MANUAL_ONLY_METRICS:
        return "manual_only_csv"
    if not da.api_supported(marketplace, metric_type):
        # An operation metric with no authoritative dataset here — notably every Yandex money metric.
        if marketplace == "yandex" and metric_type in (
                "revenue", "marketplace_fees", "logistics", "penalties", "deductions"):
            return "yandex_finance_unsupported"
        return "api_unsupported"
    return None


@router.get("/marketplace-stores/{store_id}/source-policy", response_model=SourcePolicyOut)
async def get_source_policy(
    store_id: str,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    store = await _owned_store(db, current_user, store_id)
    rows = {r.metric_type: r.preference for r in (await db.execute(
        select(StoreDataSourcePolicy).where(
            StoreDataSourcePolicy.marketplace_store_id == store.id))).scalars().all()}

    metrics: list[SourcePolicyMetric] = []
    for metric_type in METRIC_TYPES:
        supported = da.api_supported(store.marketplace, metric_type)
        available = False
        if supported:
            available, _ = await resolver.api_coverage(
                db, store.id, store.marketplace, metric_type, period=None)
        metrics.append(SourcePolicyMetric(
            metric_type=metric_type,
            preference=rows.get(metric_type, "csv"),   # absent ⇒ effective csv
            api_supported=supported,
            api_available=available,
            limitation=_limitation(store.marketplace, metric_type)))
    return SourcePolicyOut(store_id=store.id, marketplace=store.marketplace, metrics=metrics)


@router.patch("/marketplace-stores/{store_id}/source-policy/{metric_type}",
              response_model=SourcePolicyMetric)
async def set_source_policy(
    store_id: str,
    metric_type: str,
    body: SourcePolicyPatch,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    if metric_type not in METRIC_TYPES:
        raise HTTPException(422, "unknown metric_type")
    if body.preference not in PREFERENCES:
        raise HTTPException(422, "preference must be auto, api or csv")

    store = await _owned_store(db, current_user, store_id)
    # An explicit API choice for a metric the API cannot authoritatively source (Yandex money,
    # cost of goods, external ad spend) is refused rather than silently honoured.
    if body.preference == "api" and not da.api_supported(store.marketplace, metric_type):
        raise HTTPException(422, "API is not an available source for this metric")

    row = (await db.execute(select(StoreDataSourcePolicy).where(
        StoreDataSourcePolicy.marketplace_store_id == store.id,
        StoreDataSourcePolicy.metric_type == metric_type))).scalars().first()
    if row is None:
        row = StoreDataSourcePolicy(id=str(uuid.uuid4()), marketplace_store_id=store.id,
                                    metric_type=metric_type, preference=body.preference)
        db.add(row)
    else:
        row.preference = body.preference
        row.updated_at = datetime.utcnow()
    await db.commit()

    supported = da.api_supported(store.marketplace, metric_type)
    available = False
    if supported:
        available, _ = await resolver.api_coverage(
            db, store.id, store.marketplace, metric_type, period=None)
    return SourcePolicyMetric(metric_type=metric_type, preference=body.preference,
                              api_supported=supported, api_available=available,
                              limitation=_limitation(store.marketplace, metric_type))
