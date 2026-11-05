"""Additive local Ozon cabinet/product selection, NOT an unanswered review provider.

No runtime caller, import, normalization, persistence or publish is changed here.
The verified local account identity must already exist; this reader never creates
one from a Client-Id. Catalog access with the selected current key then relates
product_id + exact seller article to the documented root SKU. This establishes
the selected read's binding, not absence of seller replies or a remote snapshot.
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import datetime, timezone

from cryptography.fernet import InvalidToken
from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession
from sqlalchemy.pool import QueuePool, NullPool

from config import settings
from models.api_credential import ApiCredential
from models.marketplace_account import MarketplaceAccount
from models.marketplace_connection import MarketplaceConnection
from models.product import Product
from models.user import User
from models.workspace import Workspace
from . import credential_vault
from .errors import ExecutionError
from .ozon_review_read import (
    OzonReviewReader, OzonReviewReadIncomplete, ReviewInfo, ReviewReadLimits, _Budget, _integer,
)


def _denied():
    # No caller identifiers, ciphertext, plaintext, or provider payload in errors.
    return ExecutionError.guard("OZON_REVIEW_BINDING", "Ozon review binding not current")


@dataclass(frozen=True)
class BoundOzonReviewSelection:
    product_id: str
    marketplace_account_id: str
    connection_id: str
    sku: int
    reviews: tuple[ReviewInfo, ...]

    @property
    def unanswered_proven(self) -> bool:
        # NEW/VIEWED/PROCESSED and comment counts cannot supply this proof.
        return False


@dataclass(frozen=True)
class _Snapshot:
    product_id: str
    user_id: str
    account_id: str
    workspace_id: str
    connection_id: str
    client_id: str
    external_product_id: str
    offer_id: str
    credential_id: str
    secret_enc: bytes = field(repr=False)
    expires_at: object = field(repr=False)
    credential_updated_at: object = field(repr=False)
    connection_updated_at: object = field(repr=False)


def _engine(db):
    # A shared-connection pool can expose/roll back another session's transaction.
    # Reject it rather than pretending that a second Session is independent.
    if (not isinstance(db.bind, AsyncEngine) or not isinstance(db.bind.pool, (QueuePool, NullPool))
            or db.in_transaction() or db.new or db.dirty or db.deleted):
        raise _denied()
    return db.bind


async def _snapshot(db, *, user_id: str, product_id: str, connection_id: str) -> _Snapshot:
    # A fresh session/transaction per check avoids the caller's identity map AND
    # repeatable-read snapshot. The engine is explicitly supplied by the caller,
    # never a global default DB. Caller-owned pending state is never flushed.
    engine = _engine(db)
    query = select(
        Product.id, Product.user_id, MarketplaceAccount.id, Workspace.id,
        MarketplaceConnection.id, MarketplaceConnection.ozon_client_id,
        Product.external_product_id, Product.sku, ApiCredential.id,
        ApiCredential.secret_enc, ApiCredential.expires_at, ApiCredential.updated_at,
        MarketplaceConnection.updated_at,
    ).select_from(Product).join(
        MarketplaceAccount, MarketplaceAccount.id == Product.marketplace_account_id,
    ).join(Workspace, Workspace.id == MarketplaceAccount.workspace_id).join(
        MarketplaceConnection,
        MarketplaceConnection.marketplace_account_id == MarketplaceAccount.id,
    ).join(ApiCredential, ApiCredential.connection_id == MarketplaceConnection.id).join(
        User, User.id == Workspace.owner_user_id,
    ).where(
        Product.id == product_id, Product.user_id == user_id, Product.marketplace == "ozon",
        MarketplaceConnection.id == connection_id, MarketplaceConnection.user_id == user_id,
        MarketplaceConnection.workspace_id == Workspace.id,
        MarketplaceConnection.marketplace == "ozon", MarketplaceConnection.status == "connected",
        MarketplaceAccount.marketplace == "ozon", MarketplaceAccount.identity_status == "verified",
        MarketplaceAccount.external_account_id == MarketplaceConnection.ozon_client_id,
        User.id == user_id, User.deleted_at.is_(None), ApiCredential.scope == "feedbacks",
    )
    try:
        async with AsyncSession(engine) as check:
            row = (await check.execute(query)).one_or_none()
    except SQLAlchemyError:
        raise _denied() from None
    _engine(db)
    if row is None:
        raise _denied()
    value = _Snapshot(*row)
    raw = value.external_product_id
    # Local canonical positive int64 admission. Never use seller SKU as product_id.
    if (not isinstance(raw, str) or not raw.isascii() or not raw.isdecimal()
            or len(raw) > 19 or raw.startswith("0")
            or int(raw) >= 2**63 or not isinstance(value.offer_id, str)
            or not value.offer_id.strip() or not isinstance(value.client_id, str)
            or not value.client_id.strip() or not isinstance(value.secret_enc, bytes)):
        raise _denied()
    if value.expires_at is not None:
        expires = value.expires_at
        if not isinstance(expires, datetime):
            raise _denied()
        if expires.tzinfo is None:
            expires = expires.replace(tzinfo=timezone.utc)
        if expires <= datetime.now(timezone.utc):
            raise _denied()
    return value


def _catalog_sku(data, binding: _Snapshot) -> int:
    rows = data.get("items") if isinstance(data, dict) else None
    if not isinstance(rows, list) or len(rows) != 1 or not isinstance(rows[0], dict):
        raise OzonReviewReadIncomplete("catalog_identity")
    row = rows[0]
    if (not _integer(row.get("id"), 64) or row["id"] != int(binding.external_product_id)
            or row.get("offer_id") != binding.offer_id or not _integer(row.get("sku"), 64)):
        raise OzonReviewReadIncomplete("catalog_identity")
    return row["sku"]


class _BoundReader(OzonReviewReader):
    def __init__(self, db, binding: _Snapshot):
        super().__init__(settings.ozon_seller_base)
        self.db, self.binding = db, binding

    async def current(self, budget: _Budget):
        b = self.binding
        try:
            current = await asyncio.wait_for(_snapshot(self.db, user_id=b.user_id,
                product_id=b.product_id, connection_id=b.connection_id), timeout=budget.remaining())
        except asyncio.TimeoutError:
            raise OzonReviewReadIncomplete("deadline") from None
        if current != b:
            raise _denied()
        budget.remaining()

    async def _request(self, path, *, token, headers, body, budget):
        await self.current(budget)
        result = await super()._request(path, token=token, headers=headers, body=body, budget=budget)
        await self.current(budget)
        return result


async def read_bound_product_selection(db, *, user_id: str, product_id: str,
                                       connection_id: str,
                                       limits: ReviewReadLimits | None = None) -> BoundOzonReviewSelection:
    """Read a bound raw product selection. Not wired to import/auto-draft.

    One invocation-local budget includes both catalog reads and all list/details.
    Local authority is rechecked before/after every HTTP request and at return.
    Checks are not DB locks or an atomic remote snapshot: revocation cannot recall
    an already in-flight read, and a remote change-and-revert is not detectable.
    Fresh read transactions use only the caller's explicitly bound AsyncEngine;
    caller transactions, shared-connection pools, pending changes or a session
    bound to one connection are refused. The caller must stay idle during reads.
    This function never flushes or commits caller state.
    """
    if any(not isinstance(x, str) or not x.strip() or len(x) > 255
           for x in (user_id, product_id, connection_id)):
        raise _denied()
    _engine(db)
    budget = _Budget(limits if limits is not None else ReviewReadLimits())
    try:
        binding = await asyncio.wait_for(_snapshot(db, user_id=user_id, product_id=product_id,
            connection_id=connection_id), timeout=budget.remaining())
    except asyncio.TimeoutError:
        raise OzonReviewReadIncomplete("deadline") from None
    try:
        token = credential_vault.decrypt(binding.secret_enc)
    except (InvalidToken, ValueError, TypeError):
        raise _denied() from None
    if not isinstance(token, str) or not token.strip():
        raise _denied()
    reader = _BoundReader(db, binding)
    headers = {"Client-Id": binding.client_id}
    body = {"product_id": [binding.external_product_id]}
    first = await reader._request("/v3/product/info/list", token=token, headers=headers,
                                   body=body, budget=budget)
    sku = _catalog_sku(first, binding)
    selection = await reader._selection(token=token, headers=headers, limit=100,
                                        sort_dir="ASC", expected_sku=None, budget=budget)
    last = await reader._request("/v3/product/info/list", token=token, headers=headers,
                                  body=body, budget=budget)
    if _catalog_sku(last, binding) != sku:
        raise OzonReviewReadIncomplete("catalog_changed")
    await reader.current(budget)
    return BoundOzonReviewSelection(binding.product_id, binding.account_id, binding.connection_id,
                                    sku, tuple(review for review in selection if review.sku == sku))
