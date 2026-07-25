"""API data ingestion scheduler (PULT-LAUNCH-1.4.5E/F/G) — Wildberries, Ozon, Yandex.

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
from models.marketplace_account import MarketplaceAccount
from models.marketplace_connection import MarketplaceConnection
from models.marketplace_store import MarketplaceStore
from services.marketplace import credential_vault
from services.marketplace.errors import ExecutionError
from services.marketplace.ingest import ozon as ozon_ingest
from services.marketplace.ingest import wb as wb_ingest
from services.marketplace.ingest import yandex as yandex_ingest

log = logging.getLogger(__name__)

# Provider per marketplace. Each exposes MARKETPLACE, DATA_TYPES and fetch_and_persist_page — so the
# scheduler stays marketplace-neutral and a new marketplace is one registry entry, not a branch.
# Optional hooks a provider MAY expose (absent = the neutral default):
#   * UNSUPPORTED (frozenset) — data_types that are declared but never called (parked as 'unsupported')
#   * sync_gate(account, store) -> (ok, reason) — a store the provider will not sync yet (Yandex needs
#     both a mapped businessId and campaignId; WB/Ozon have no such requirement).
_PROVIDERS = {
    wb_ingest.MARKETPLACE: wb_ingest,
    ozon_ingest.MARKETPLACE: ozon_ingest,
    yandex_ingest.MARKETPLACE: yandex_ingest,
}

# Conservative internal pacing — PULT's own settings, NOT any published marketplace limit.
_MAX_PAGES_PER_RUN = 20                 # a bounded slice of one type per pass
_BACKOFF_BASE_MIN = 5                   # first retry delay
_BACKOFF_CAP_MIN = 6 * 60              # never wait longer than this
_DEFAULT_CADENCE = 6 * 60
# A parked (unsupported / unmapped) state is not an error — re-check it on a slow, quiet cadence so a
# later mapping or a future implementation is picked up without hammering anything.
_PARKED_RECHECK_MIN = 6 * 60
_CADENCE_MIN = {                              # gap after a successful full sync, per type
    "card_content": 6 * 60, "prices": 60,
    "orders": 60, "sales": 60, "stocks": 60, "finance": 6 * 60,
    # Ozon
    "products": 6 * 60, "fbo_postings": 60, "fbs_postings": 60, "returns": 60,
    # Yandex
    "cards": 6 * 60,
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


async def _active_stores(db: AsyncSession, account_id: str) -> list[MarketplaceStore]:
    """Every ACTIVE store of the cabinet. WB/Ozon have exactly one (primary); Yandex has many
    campaign stores — the scheduler serves each, so per-store data never lands in the wrong store."""
    return list((await db.execute(
        select(MarketplaceStore).where(
            MarketplaceStore.marketplace_account_id == account_id,
            MarketplaceStore.status == "active"))).scalars().all())


async def _token_for(db: AsyncSession, connection_id: str) -> str | None:
    cred = (await db.execute(
        select(ApiCredential).where(ApiCredential.connection_id == connection_id))).scalars().first()
    if cred is None:
        return None
    return credential_vault.decrypt(cred.secret_enc)


async def _ensure_states(db: AsyncSession, conn_id: str, account_id: str,
                         store_id: str, provider) -> list[ApiSyncState]:
    """One ApiSyncState per supported data_type for this (connection, store). Idempotent.

    Takes plain ids, not ORM objects: it runs inside a store loop where a prior store's rollback may
    have expired the connection/store rows, and reading an expired attr here would raise."""
    out: list[ApiSyncState] = []
    for data_type in provider.DATA_TYPES:
        state = (await db.execute(
            select(ApiSyncState).where(
                ApiSyncState.marketplace_connection_id == conn_id,
                ApiSyncState.marketplace_store_id == store_id,
                ApiSyncState.data_type == data_type))).scalars().first()
        if state is None:
            state = ApiSyncState(
                marketplace_connection_id=conn_id, marketplace_account_id=account_id,
                marketplace_store_id=store_id, data_type=data_type, status="pending")
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
    # Captured while the attr is live: a rollback below EXPIRES every object in the session, and
    # reading an expired attr from plain (non-greenlet) code re-raises as MissingGreenlet — which is
    # NOT an ExecutionError and would escape to the connection-level handler, killing sibling stores.
    prev_fail = state.fail_count or 0
    await db.commit()

    for _ in range(_MAX_PAGES_PER_RUN):
        try:
            result = await provider.fetch_and_persist_page(db, state, token, client_id)
            # rows + advanced cursor commit together: the cursor never moves past unwritten data.
            await db.commit()
        except ExecutionError as exc:
            await db.rollback()
            _record_failure(state, exc, now, prev_fail)   # writes only — never reads an expired attr
            await db.commit()
            return
        prev_fail = 0   # a page came back clean — the failure streak is broken
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


def _provider_unsupported(provider) -> frozenset:
    return getattr(provider, "UNSUPPORTED", frozenset())


def _provider_gate(provider, account, store) -> tuple[bool, str | None]:
    """Ask the provider whether this store may sync. A provider without the hook (WB/Ozon) always
    may — the neutral default keeps the scheduler free of marketplace branching."""
    fn = getattr(provider, "sync_gate", None)
    return fn(account, store) if fn else (True, None)


def _park(state: ApiSyncState, status: str, reason: str | None, now: datetime) -> None:
    """A parked state is not a failure and makes ZERO API calls: a data_type the provider does not
    support ('unsupported'), or a store not yet mapped to its external id ('unmapped'). It is
    re-checked on a slow cadence so a later mapping / implementation is picked up on its own."""
    state.status = status
    state.last_safe_error_code = (str(reason)[:32] if reason else None)
    state.last_attempt_at = now
    state.next_run_at = now + timedelta(minutes=_PARKED_RECHECK_MIN)


def _record_failure(state: ApiSyncState, exc: ExecutionError, now: datetime, prev_fail: int) -> None:
    """Classify a safe error code and set a persistent backoff. Never stores the raw message.

    Write-only: `prev_fail` is the failure count captured while the row was live, so this runs after
    a rollback without touching an expired attribute (which would raise MissingGreenlet)."""
    code = getattr(exc, "code", None) or "marketplace_5xx"
    fail_count = (prev_fail or 0) + 1
    state.last_safe_error_code = str(code)[:32]
    state.fail_count = fail_count
    state.status = "paused"
    if code in (ExecutionError.AUTH, ExecutionError.MISSING_SCOPE):
        # The key is wrong (or lacks the scope) for this connection — a long, deliberate pause,
        # not a quick retry that would just fail again.
        state.next_run_at = now + timedelta(minutes=_BACKOFF_CAP_MIN)
    else:
        state.next_run_at = now + timedelta(minutes=_backoff_minutes(fail_count))


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
            # Capture the connection's scalars up front, while the row is guaranteed live. A later
            # store's _sync_state rolls back on failure and EXPIRES every object in the session, so
            # reading conn.user_id / account.* / store.* afterwards would lazy-load from plain code
            # and raise MissingGreenlet — which, not being an ExecutionError, would escape here and
            # kill the sibling stores. Ids (PKs) survive expiry; other columns are re-fetched fresh.
            conn_id, user_id = conn.id, conn.user_id
            client_id = conn.ozon_client_id   # None for WB/Yandex; the Ozon provider needs it
            account_id = conn.marketplace_account_id
            stores = await _active_stores(db, account_id)
            if not stores:
                continue
            token = await _token_for(db, conn_id)
            if not token:
                continue
            unsupported = _provider_unsupported(provider)
            unsupported_reason = getattr(provider, "UNSUPPORTED_REASON", "unsupported")
            store_ids = [s.id for s in stores]
            # Each active store is served independently: one store's trouble (or its being unmapped)
            # never blocks another. Yandex has many; WB/Ozon exactly one. Every ORM row is re-fetched
            # fresh inside the loop so a prior rollback's expiry never turns into a lazy-load crash.
            for store_id in store_ids:
                store = await db.get(MarketplaceStore, store_id)
                if store is None:
                    continue
                account = await db.get(MarketplaceAccount, account_id) if account_id else None
                gate_ok, gate_reason = _provider_gate(provider, account, store)
                states = await _ensure_states(db, conn_id, account_id, store_id, provider)
                state_ids = [s.id for s in states]
                for sid in state_ids:
                    state = await db.get(ApiSyncState, sid)
                    if state is None:
                        continue
                    state._owner_user_id = user_id
                    if state.data_type in unsupported:
                        _park(state, "unsupported", unsupported_reason, now)
                        await db.commit()
                        continue
                    if not gate_ok:
                        _park(state, "unmapped", gate_reason, now)
                        await db.commit()
                        continue
                    if not _due(state, now):
                        continue
                    await _sync_state(db, state, provider, token, client_id, now)
                    touched += 1
        except Exception:  # noqa: BLE001 — one connection's failure must not stop the others
            await db.rollback()
            log.warning("api sync: connection failed, continuing")   # no ids, no secrets
            continue
    return {"enabled": True, "connections": len(conns), "states_touched": touched}
