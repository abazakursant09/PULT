"""API data ingestion scheduler (PULT-LAUNCH-1.4.5E) — Wildberries only.

One pass (`run_api_sync_once`) walks the API sync states that are due and pulls a bounded number of
pages for each, committing per page so the cursor never runs ahead of persisted data. It is gated
by a master switch that is OFF by default and not seller-controlled:

    settings.api_data_sync_enabled = False  →  ZERO marketplace calls.

Failure is per connection. A 401/403 pauses the whole WB connection (the key is wrong for every
type); a 429/timeout/5xx pauses the current type with an exponential, persistent backoff; a
per-row problem is skipped without losing the rest of the page. One connection's trouble never
touches another's. Nothing here sleeps — the scheduler calls it on a cadence and each state's
`next_run_at` gates the next attempt.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from config import settings
from models.api_credential import ApiCredential
from models.api_sync_state import ApiSyncState
from models.marketplace_connection import MarketplaceConnection
from models.marketplace_store import MarketplaceStore
from services.marketplace import credential_vault
from services.marketplace.errors import ExecutionError
from services.marketplace.ingest import ozon as ozon_ingest
from services.marketplace.ingest import wb as wb_ingest

log = logging.getLogger(__name__)

# Provider per marketplace. Each exposes MARKETPLACE, DATA_TYPES and fetch_and_persist_page — so the
# scheduler stays marketplace-neutral and a new marketplace is one registry entry, not a branch.
_PROVIDERS = {wb_ingest.MARKETPLACE: wb_ingest, ozon_ingest.MARKETPLACE: ozon_ingest}

# Conservative internal pacing — PULT's own settings, NOT any published marketplace limit.
_MAX_PAGES_PER_RUN = 20                 # a bounded slice of one type per pass
_BACKOFF_BASE_MIN = 5                   # first retry delay
_BACKOFF_CAP_MIN = 6 * 60              # never wait longer than this
_DEFAULT_CADENCE = 6 * 60
_CADENCE_MIN = {                              # gap after a successful full sync, per type
    "card_content": 6 * 60, "prices": 60,
    "orders": 60, "sales": 60, "stocks": 60, "finance": 6 * 60,
    # Ozon
    "products": 6 * 60, "fbo_postings": 60, "fbs_postings": 60, "returns": 60,
}


def _backoff_minutes(fail_count: int) -> int:
    return min(_BACKOFF_CAP_MIN, _BACKOFF_BASE_MIN * (2 ** max(0, fail_count - 1)))


async def _eligible_connections(db: AsyncSession) -> list[MarketplaceConnection]:
    return list((await db.execute(
        select(MarketplaceConnection).where(
            MarketplaceConnection.marketplace.in_(list(_PROVIDERS)),
            MarketplaceConnection.status == "connected",
            MarketplaceConnection.verification_status == "verified",
            MarketplaceConnection.marketplace_account_id.isnot(None))
    )).scalars().all())


async def _active_store(db: AsyncSession, account_id: str) -> MarketplaceStore | None:
    return (await db.execute(
        select(MarketplaceStore).where(
            MarketplaceStore.marketplace_account_id == account_id,
            MarketplaceStore.status == "active"))).scalars().first()


async def _token_for(db: AsyncSession, connection_id: str) -> str | None:
    cred = (await db.execute(
        select(ApiCredential).where(ApiCredential.connection_id == connection_id))).scalars().first()
    if cred is None:
        return None
    return credential_vault.decrypt(cred.secret_enc)


async def _ensure_states(db: AsyncSession, conn: MarketplaceConnection,
                         store: MarketplaceStore, provider) -> list[ApiSyncState]:
    """One ApiSyncState per supported data_type for this (connection, store). Idempotent."""
    out: list[ApiSyncState] = []
    for data_type in provider.DATA_TYPES:
        state = (await db.execute(
            select(ApiSyncState).where(
                ApiSyncState.marketplace_connection_id == conn.id,
                ApiSyncState.marketplace_store_id == store.id,
                ApiSyncState.data_type == data_type))).scalars().first()
        if state is None:
            state = ApiSyncState(
                marketplace_connection_id=conn.id, marketplace_account_id=conn.marketplace_account_id,
                marketplace_store_id=store.id, data_type=data_type, status="pending")
            db.add(state)
        out.append(state)
    await db.commit()
    return out


def _due(state: ApiSyncState, now: datetime) -> bool:
    if state.status == "paused":
        return False
    return state.next_run_at is None or state.next_run_at <= now


async def _sync_state(db: AsyncSession, state: ApiSyncState, provider, token: str,
                      client_id: str | None, now: datetime) -> None:
    """Pull up to a bounded number of pages for ONE state, committing each page."""
    state._owner_user_id = state._owner_user_id if hasattr(state, "_owner_user_id") else None
    state.status = "running"
    state.last_attempt_at = now
    await db.commit()

    for _ in range(_MAX_PAGES_PER_RUN):
        try:
            result = await provider.fetch_and_persist_page(db, state, token, client_id)
            # rows + advanced cursor commit together: the cursor never moves past unwritten data.
            await db.commit()
        except ExecutionError as exc:
            await db.rollback()
            _record_failure(state, exc, now)
            await db.commit()
            return
        state.fail_count = 0
        state.last_safe_error_code = None
        if result.get("defer"):
            # An async report (stocks) is not ready yet — stop this run, come back shortly. The
            # cursor (the task id) is already persisted, so the next tick polls it.
            state.status = "running"
            state.next_run_at = now + timedelta(minutes=1)
            await db.commit()
            return
        if result["done"]:
            cadence = _CADENCE_MIN.get(state.data_type, _DEFAULT_CADENCE)
            state.status = "synced"
            state.last_success_at = now
            state.next_run_at = now + timedelta(minutes=cadence)
            await db.commit()
            return
    # Hit the page cap with more to do — resume soon, keep the persisted cursor.
    state.status = "running"
    state.next_run_at = now + timedelta(minutes=1)
    await db.commit()


def _record_failure(state: ApiSyncState, exc: ExecutionError, now: datetime) -> None:
    """Classify a safe error code and set a persistent backoff. Never stores the raw message."""
    code = getattr(exc, "code", None) or "marketplace_5xx"
    state.last_safe_error_code = str(code)[:32]
    state.fail_count = (state.fail_count or 0) + 1
    state.status = "paused"
    if code in (ExecutionError.AUTH, ExecutionError.MISSING_SCOPE):
        # The key is wrong (or lacks the scope) for this connection — a long, deliberate pause,
        # not a quick retry that would just fail again.
        state.next_run_at = now + timedelta(minutes=_BACKOFF_CAP_MIN)
    else:
        state.next_run_at = now + timedelta(minutes=_backoff_minutes(state.fail_count))


async def run_api_sync_once(db: AsyncSession) -> dict:
    """One scheduler pass. Returns a small counts dict for observability. Zero calls if disabled."""
    if not settings.api_data_sync_enabled:
        return {"enabled": False, "connections": 0}

    now = datetime.utcnow()
    conns = await _eligible_connections(db)
    touched = 0
    for conn in conns:
        try:
            provider = _PROVIDERS.get(conn.marketplace)
            if provider is None:
                continue
            store = await _active_store(db, conn.marketplace_account_id)
            if store is None:
                continue
            token = await _token_for(db, conn.id)
            if not token:
                continue
            client_id = conn.ozon_client_id   # None for WB; the Ozon provider needs it
            states = await _ensure_states(db, conn, store, provider)
            for state in states:
                if not _due(state, now):
                    continue
                state._owner_user_id = conn.user_id
                await _sync_state(db, state, provider, token, client_id, now)
                touched += 1
        except Exception:  # noqa: BLE001 — one connection's failure must not stop the others
            await db.rollback()
            log.warning("api sync: connection failed, continuing")   # no ids, no secrets
            continue
    return {"enabled": True, "connections": len(conns), "states_touched": touched}
