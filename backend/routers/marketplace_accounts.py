"""Keyless cabinets & stores (PULT-LAUNCH-1.4.1).

Lets a seller declare a marketplace cabinet (MarketplaceAccount) and its store(s)
(MarketplaceStore) WITHOUT any API key. No credentials, no connection, no discovery
are created here — that is 1.5.

Two hard rules drive the shape:
  * A keyless cabinet is never reused automatically. Without the API we cannot know
    whether two keyless cabinets of the same marketplace are the same real cabinet,
    so every POST /marketplace-accounts mints a NEW MarketplaceAccount. A label is a
    human name, never an identity — we never match on it.
  * The store multiplicity is the DB's job (PULT-LAUNCH-1.3): WB/Ozon get exactly one
    store per cabinet (store_key='primary' + CHECK + UNIQUE), Yandex get one-or-many
    (store_key = local uuid). We simply attempt the write and translate the DB's
    IntegrityError into a clean 409 rather than a 500.
"""
from __future__ import annotations

import logging
import uuid

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from database import get_db
from dependencies import get_current_user
from models.marketplace_account import MarketplaceAccount
from models.marketplace_connection import MarketplaceConnection
from models.marketplace_store import MarketplaceStore
from models.user import User
from schemas.marketplace_store import (
    AccountCreate, AccountOut, StoreCreate, StoreOut, StoreStatusUpdate,
)
from services.workspace_resolver import WorkspaceMissing, resolve_workspace_id

log = logging.getLogger(__name__)
router = APIRouter()

# Identity layer speaks ONLY full canonical names. Short codes (wb / ym) belong to the
# parser/metric boundary and must be rejected here, so this is a strict membership test —
# NOT identity_normalize, which would helpfully map wb -> wildberries and accept it.
_VALID_MARKETPLACE = {"wildberries", "ozon", "yandex"}
_SINGLE_STORE = {"wildberries", "ozon"}     # exactly one 'primary' store per cabinet


def _clean_label(label: str) -> str:
    cleaned = (label or "").strip()
    if not cleaned:
        raise HTTPException(422, "Укажите название")
    return cleaned[:120]


async def _workspace_id(db: AsyncSession, user: User) -> str:
    try:
        return await resolve_workspace_id(db, str(user.id))
    except WorkspaceMissing:
        # Registration and the F1.0 backfill both guarantee a workspace, so this is a
        # broken lifecycle invariant, not something the caller did wrong or can fix.
        log.error("workspace missing for authenticated user")
        raise HTTPException(500, "Не удалось определить рабочее пространство")


async def _owned_account(db: AsyncSession, account_id: str, workspace_id: str) -> MarketplaceAccount:
    """Load an account the caller owns, or 404. The same 404 is returned for a missing
    account and for someone else's account: the response must never reveal that an id
    exists but belongs to another workspace."""
    acc = (
        await db.execute(
            select(MarketplaceAccount).where(
                MarketplaceAccount.id == account_id,
                MarketplaceAccount.workspace_id == workspace_id,
            )
        )
    ).scalars().first()
    if acc is None:
        raise HTTPException(404, "Кабинет не найден")
    return acc


def _new_store(account: MarketplaceAccount, label: str) -> MarketplaceStore:
    store_key = "primary" if account.marketplace in _SINGLE_STORE else uuid.uuid4().hex
    return MarketplaceStore(
        id=str(uuid.uuid4()),
        marketplace_account_id=account.id,
        marketplace=account.marketplace,       # from the cabinet, never from the client
        store_key=store_key,                   # server-assigned, never from the client
        external_store_id=None,
        label=label,
        source="manual",
        status="active",
    )


# ── Create cabinet ────────────────────────────────────────────────────────────
@router.post("/marketplace-accounts", response_model=AccountOut, status_code=201)
async def create_account(
    body: AccountCreate,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    marketplace = (body.marketplace or "").strip().lower()
    if marketplace not in _VALID_MARKETPLACE:
        raise HTTPException(422, "Маркетплейс должен быть wildberries, ozon или yandex")
    label = _clean_label(body.label)
    workspace_id = await _workspace_id(db, user)

    # Always a NEW cabinet — never reuse a keyless account by marketplace or label.
    account = MarketplaceAccount(
        id=str(uuid.uuid4()),
        workspace_id=workspace_id,
        marketplace=marketplace,
        external_account_id=None,
        identity_status="unverified",
        label=label,
    )
    db.add(account)

    stores: list[MarketplaceStore] = []
    if marketplace in _SINGLE_STORE:
        # WB/Ozon: cabinet + its one primary store are atomic — a store failure must not
        # leave an empty cabinet behind.
        db.add(_new_store(account, label))
    try:
        await db.commit()
    except IntegrityError:
        await db.rollback()
        raise HTTPException(409, "Не удалось создать кабинет")
    await db.refresh(account)

    if marketplace in _SINGLE_STORE:
        stores = list((await db.execute(
            select(MarketplaceStore).where(MarketplaceStore.marketplace_account_id == account.id)
        )).scalars().all())

    return _account_out(account, has_connection=False, stores=stores)


# ── Create store in a chosen cabinet ──────────────────────────────────────────
@router.post("/marketplace-accounts/{account_id}/stores", response_model=StoreOut, status_code=201)
async def create_store(
    account_id: str,
    body: StoreCreate,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    workspace_id = await _workspace_id(db, user)
    account = await _owned_account(db, account_id, workspace_id)
    label = _clean_label(body.label)

    # Capture before the write: a failed commit + rollback EXPIRES `account`, and reading an
    # expired ORM attribute afterwards triggers a lazy load (async IO) outside the greenlet,
    # which would mask the clean 409 with a MissingGreenlet error.
    is_single = account.marketplace in _SINGLE_STORE
    store = _new_store(account, label)
    db.add(store)
    try:
        await db.commit()
    except IntegrityError:
        await db.rollback()
        # WB/Ozon already have their single primary store (UNIQUE(account, store_key)).
        if is_single:
            raise HTTPException(409, "У этого кабинета уже есть магазин")
        raise HTTPException(409, "Не удалось создать магазин")
    await db.refresh(store)
    return StoreOut.model_validate(store)


# ── List cabinets (+ stores) ──────────────────────────────────────────────────
@router.get("/marketplace-accounts", response_model=list[AccountOut])
async def list_accounts(
    include_stores: bool = Query(False),
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    workspace_id = await _workspace_id(db, user)
    accounts = list((await db.execute(
        select(MarketplaceAccount).where(MarketplaceAccount.workspace_id == workspace_id)
        .order_by(MarketplaceAccount.created_at)
    )).scalars().all())
    if not accounts:
        return []

    acc_ids = [a.id for a in accounts]
    connected = set((await db.execute(
        select(MarketplaceConnection.marketplace_account_id)
        .where(MarketplaceConnection.marketplace_account_id.in_(acc_ids))
    )).scalars().all())

    stores_by_account: dict[str, list[MarketplaceStore]] = {}
    if include_stores:
        for s in (await db.execute(
            select(MarketplaceStore).where(MarketplaceStore.marketplace_account_id.in_(acc_ids))
            .order_by(MarketplaceStore.created_at)
        )).scalars().all():
            stores_by_account.setdefault(s.marketplace_account_id, []).append(s)

    return [
        _account_out(
            a,
            has_connection=a.id in connected,
            stores=stores_by_account.get(a.id, []) if include_stores else None,
        )
        for a in accounts
    ]


# ── Archive / restore a store ─────────────────────────────────────────────────
@router.patch("/marketplace-stores/{store_id}", response_model=StoreOut)
async def set_store_status(
    store_id: str,
    body: StoreStatusUpdate,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    workspace_id = await _workspace_id(db, user)
    # ownership through the store's cabinet; a foreign/missing store is the same 404
    store = (await db.execute(
        select(MarketplaceStore)
        .join(MarketplaceAccount, MarketplaceAccount.id == MarketplaceStore.marketplace_account_id)
        .where(MarketplaceStore.id == store_id, MarketplaceAccount.workspace_id == workspace_id)
    )).scalars().first()
    if store is None:
        raise HTTPException(404, "Магазин не найден")

    # Idempotent: setting the same status is a no-op success. store_key / external_store_id
    # are never touched — archiving is a soft state, not a delete.
    if store.status != body.status:
        store.status = body.status
        await db.commit()
        await db.refresh(store)
    return StoreOut.model_validate(store)


def _account_out(account: MarketplaceAccount, *, has_connection: bool,
                 stores: list[MarketplaceStore] | None) -> AccountOut:
    return AccountOut(
        id=account.id,
        marketplace=account.marketplace,
        label=account.label,
        identity_status=account.identity_status,
        external_account_id=account.external_account_id,   # owner viewing own cabinet; NULL for keyless
        has_connection=has_connection,
        stores=[StoreOut.model_validate(s) for s in stores] if stores is not None else None,
    )
