"""SECURITY-2D-1C-B — READ-ONLY reconciliation of a stuck operation against the marketplace.

`observe(db, row)` returns exactly one verdict and NEVER performs a provider WRITE, never re-runs an
operation, never mutates the ExecutionLog. It only calls existing provider READ methods (never a write
method — enforced by a source guard test) and compares the CURRENT marketplace state to the operation's
saved intent.

Honest semantics (Inal) — a verdict is a CLASSIFICATION, never an authorisation:
  * intent_observed     — the target end-state is observed NOW. NOT attribution: it does NOT prove PULT
                          made the change, only that the target is currently observed.
  * target_not_observed — the target end-state is NOT observed now. This is a CURRENT-STATE MISMATCH and
                          is NOT proof the original operation was never applied (it may have applied then
                          drifted, or the read lags). It may only lead to manual_attention / still_unknown
                          and NEVER by itself authorises a retry or a provider write. A plain current
                          price/status read is NOT a per-operation "not applied" proof.
  * still_unknown       — not enough evidence (no read path, incomplete/eventual data, or any error).
No verdict changes ExecutionLog.status, no verdict authorises a provider write, and NO verdict is a
"proven_not_applied" / "safe to retry" signal. Only actions with a PROVEN existing READ path are
reconciled; everything else is still_unknown with ZERO provider calls.
"""
from __future__ import annotations

import logging

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from models.marketplace_connection import MarketplaceConnection
from models.api_credential import ApiCredential
from services.marketplace import action_catalog, credential_vault
from services.marketplace.executor import _money  # pure, read-only helper (same money normalization)

log = logging.getLogger(__name__)

INTENT_OBSERVED = "intent_observed"
TARGET_NOT_OBSERVED = "target_not_observed"
STILL_UNKNOWN = "still_unknown"

# Actions with NO authoritative read path (see the 2D-1C design): never call a provider, never guess.
_NO_READ_PATH = frozenset({"ad_set_bid", "update_card", "stop_auto_promotion"})

_CANON = {"wildberries": "wb", "wb": "wb", "ozon": "ozon",
          "yandex": "yandex", "yandex_market": "yandex", "ym": "yandex"}


def _canon(mp) -> str:
    return _CANON.get((mp or "").lower(), (mp or "").lower())


async def observe(db: AsyncSession, row) -> str:
    """One read-only verdict for a stuck row. Any missing piece or error → still_unknown (fail-safe)."""
    action = row.action_type
    if action in _NO_READ_PATH:
        return STILL_UNKNOWN
    try:
        if action == "set_price":
            return await _observe_price(db, row)
        if action == "ad_set_state":
            return await _observe_ad_state(db, row)
        if action == "publish_review_response":
            return await _observe_review(db, row)
    except Exception:  # noqa: BLE001 — any read error / incomplete data is still_unknown, never a crash
        return STILL_UNKNOWN
    return STILL_UNKNOWN


async def _resolve(db: AsyncSession, row):
    """(marketplace_canon, token, account_ref, connection) for a read, or None if it cannot be resolved.
    Read-only: loads the connection + the scoped credential and decrypts only the read token."""
    if not row.connection_id:
        return None
    conn = (await db.execute(select(MarketplaceConnection).where(
        MarketplaceConnection.id == row.connection_id,
        MarketplaceConnection.user_id == row.user_id))).scalars().first()
    if conn is None or conn.status != "connected":
        return None
    try:
        scope = action_catalog.get(row.action_type).required_scope
    except Exception:  # noqa: BLE001
        return None
    cred = (await db.execute(select(ApiCredential).where(
        ApiCredential.connection_id == conn.id, ApiCredential.scope == scope))).scalars().first()
    if cred is None:
        return None
    token = credential_vault.decrypt(cred.secret_enc)
    account_ref = (cred.meta or {}).get("account_ref")
    return _canon(conn.marketplace), token, account_ref, conn


def _payload(row) -> dict:
    return row.payload or {}


async def _observe_price(db, row) -> str:
    from services.marketplace.wb_client import wb_client
    from services.marketplace.ozon_client import ozon_client
    resolved = await _resolve(db, row)
    if resolved is None:
        return STILL_UNKNOWN
    mp, token, _account, conn = resolved
    p = _payload(row)
    offer = p.get("offer_id")
    target = _money(p.get("price"))
    if offer is None or target is None:
        return STILL_UNKNOWN
    current = None
    if mp == "wb":
        rows = await wb_client.list_prices(token=token)
        current = _find(rows, ("nmID", "nm_id", "offer_id"), offer, ("price",))
    elif mp == "ozon":
        rows = await ozon_client.product_prices(token=token, client_id=conn.ozon_client_id)
        current = _find(rows, ("offer_id", "product_id"), offer, ("price",))
    else:
        return STILL_UNKNOWN
    if current is None:
        return STILL_UNKNOWN
    return INTENT_OBSERVED if _money(current) == target else TARGET_NOT_OBSERVED


async def _observe_ad_state(db, row) -> str:
    from services.marketplace.wb_client import wb_client
    from services.marketplace.ozon_client import ozon_client
    resolved = await _resolve(db, row)
    if resolved is None:
        return STILL_UNKNOWN
    mp, token, _account, _conn = resolved
    p = _payload(row)
    campaign = p.get("campaign_id")
    want = p.get("action")   # "start" | "pause"
    if campaign is None or want is None:
        return STILL_UNKNOWN
    states = None
    if mp == "wb":
        states = await wb_client.list_adverts_for_nm(token=token, nm_id=campaign)
    elif mp == "ozon":
        states = await ozon_client.list_campaigns_for_sku(token=token, sku=campaign)
    else:
        return STILL_UNKNOWN
    st = _find(states, ("campaign_id", "id"), campaign, ("campaign_state", "state", "status"))
    if st is None:
        return STILL_UNKNOWN
    running = str(st).lower() in ("started", "active", "running", "on", "9", "campaign_state_running")
    paused = str(st).lower() in ("paused", "stopped", "off", "11", "campaign_state_stopped")
    if want == "start" and running:
        return INTENT_OBSERVED
    if want == "pause" and paused:
        return INTENT_OBSERVED
    if (want == "start" and paused) or (want == "pause" and running):
        return TARGET_NOT_OBSERVED
    return STILL_UNKNOWN


async def _observe_review(db, row) -> str:
    # Only Yandex exposes an authoritative "does this feedback already have OUR reply" read; WB/Ozon do
    # not (an answered feedback merely drops off the unanswered list — absence is not proof).
    resolved = await _resolve(db, row)
    if resolved is None:
        return STILL_UNKNOWN
    mp, token, _account, _conn = resolved
    if mp != "yandex":
        return STILL_UNKNOWN
    feedback_id = _payload(row).get("feedback_id")
    if not feedback_id:
        return STILL_UNKNOWN
    from services.marketplace.reviews import get_review_provider
    provider = get_review_provider("yandex")
    status = await provider.answer_status(token, str(feedback_id), None)
    if status in ("PUBLISHED", "UNMODERATED"):
        return INTENT_OBSERVED           # our reply is present (published or awaiting moderation)
    # None (reply not found — genuine unknown, may lag/moderate) and BANNED/DELETED are NOT proof of
    # "never applied"; stay still_unknown (the operator resolves in 1C-C, never an auto re-send here).
    return STILL_UNKNOWN


def _find(rows, key_fields, key_value, value_fields):
    """Read the first `value_fields` value from the row in `rows` whose any `key_fields` == key_value.
    Returns None when rows is not a usable list or no match/field is found."""
    if not isinstance(rows, (list, tuple)):
        return None
    target = str(key_value)
    for r in rows:
        if not isinstance(r, dict):
            continue
        if any(str(r.get(k)) == target for k in key_fields):
            for v in value_fields:
                if r.get(v) is not None:
                    return r.get(v)
    return None
