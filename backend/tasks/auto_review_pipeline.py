"""AR-AUTO-FILL — scheduled review ingestion + drafting.

Fills the two steps that used to be manual so Auto Reviews needs no seller click:
  * auto-sync  — fetch new reviews for every seller with an enabled + consented review rule
  * auto-draft — build a deterministic reply for the SAFE/ATTENTION reviews it just synced

Both run for BOTH modes (confirm and auto) and are NOT gated by the global kill switch — a
confirm-mode seller keeps receiving reviews and drafts. Only the PUBLISH step
(tasks/auto_publish_reviews.py) is gated by settings.automation_enabled.

Hand-off to the publish worker: a SAFE draft under an AUTO-mode rule is set to `approved` (the
seller's standing auto rule + consent is the approval) so the existing publish worker picks it up;
everything else stays `drafted` and waits for the seller. No new status is introduced. RISK reviews
get no draft (build_draft returns None) and stay manual. Each connection is processed independently:
one failing store never stops the rest.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from config import settings
from database import AsyncSessionLocal
from models.automation_rule import AutomationRule
from models.marketplace_connection import MarketplaceConnection
from models.product import Product
from models.review_response import ReviewResponse
from services.review import ingest as review_ingest
from services.review.draft import build_draft
from services.marketplace import review_automation_gate as gate
from services.marketplace.errors import ExecutionError

log = logging.getLogger(__name__)

_ACTION = gate.REVIEW_ACTION

# Review-sync cadence. The scheduler ticks every minute, but reviews do not change that fast — a
# connection syncs at most one batch per SYNC_INTERVAL (15 min) and backs off exponentially after
# consecutive failures up to SYNC_BACKOFF_CAP, so a broken store is never hit every minute. A success
# resets the backoff. The batch is capped by settings.review_sync_product_batch_size and a persistent
# keyset cursor advances over the products across cycles.
SYNC_INTERVAL_SEC = 15 * 60          # 15 min between one connection's review-sync batches
SYNC_BACKOFF_CAP_SEC = 6 * 60 * 60   # never wait more than 6 h after repeated connection failures


def _next_sync_delay(fail_count: int) -> int:
    """Exponential connection backoff after consecutive failures (base = the sync interval)."""
    return min(SYNC_INTERVAL_SEC * (2 ** max(1, fail_count)), SYNC_BACKOFF_CAP_SEC)


async def _candidate_rules(db: AsyncSession) -> list[AutomationRule]:
    """Enabled review rules with active consent bound to a connection (either mode)."""
    rules = (
        await db.execute(
            select(AutomationRule).where(
                AutomationRule.action_type == _ACTION,
                AutomationRule.enabled.is_(True),
            )
        )
    ).scalars().all()
    return [r for r in rules if r.connection_id and gate.consent_active(r)]


async def _publishable_connection(db: AsyncSession, rule: AutomationRule):
    """The rule's connection if it is owned + connected + supported + has feedbacks; else None."""
    conn = (
        await db.execute(
            select(MarketplaceConnection).where(
                MarketplaceConnection.id == rule.connection_id,
                MarketplaceConnection.user_id == rule.user_id,
            )
        )
    ).scalars().first()
    try:
        gate.check_connection_publishable(conn)
    except gate.AutoPublishBlocked as blocked:
        log.info("auto-pipeline skip rule=%s: %s", rule.id, blocked.reason)
        return None
    return conn


async def _next_batch(db: AsyncSession, user_id: str, marketplace: str, cursor, batch_size: int):
    """The next `batch_size` products after `cursor` (by stable Product.id). Wraps to the start of the
    list when the cursor is past the last product, so a round eventually covers every product."""
    def _q(after):
        stmt = select(Product).where(Product.user_id == user_id, Product.marketplace == marketplace)
        if after is not None:
            stmt = stmt.where(Product.id > after)
        return stmt.order_by(Product.id.asc()).limit(batch_size)

    rows = (await db.execute(_q(cursor))).scalars().all()
    if not rows and cursor is not None:                      # past the end → wrap to the start
        rows = (await db.execute(_q(None))).scalars().all()
    return rows


async def run_auto_sync_reviews() -> dict:
    """Fetch new reviews for eligible rules' connections. Not gated by the kill switch.

    A connection syncs at most one batch per SYNC_INTERVAL (15 min). The batch size is capped by
    settings.review_sync_product_batch_size and a persistent keyset cursor (review_sync_cursor, by
    Product.id) advances across cycles so a large store is covered over several cycles with a bounded
    burst and no product starves. One failing product never stops the rest of the batch; an
    auth/permission failure stops the connection immediately; any failure backs the connection off
    exponentially (persisted). A clean batch resets the backoff and keeps the 15-min cadence.
    """
    imported = 0
    errors = 0
    throttled = 0
    batch_size = max(1, int(settings.review_sync_product_batch_size))
    now = datetime.utcnow()
    async with AsyncSessionLocal() as db:
        for rule in await _candidate_rules(db):
            conn = await _publishable_connection(db, rule)
            if conn is None:
                continue
            # Cadence gate: not yet due (15-min interval or an active error backoff) → skip.
            if conn.review_sync_next_at is not None and conn.review_sync_next_at > now:
                throttled += 1
                continue

            products = await _next_batch(db, rule.user_id, conn.marketplace,
                                         conn.review_sync_cursor, batch_size)
            conn_errored = False
            auth_stop = False
            advanced_to = conn.review_sync_cursor
            for product in products:
                try:
                    res = await review_ingest.sync_product_reviews(db, product)
                    imported += res["imported"]
                    advanced_to = product.id                 # cursor moves past processed products
                except ExecutionError as e:
                    errors += 1
                    conn_errored = True
                    if e.code == ExecutionError.AUTH:        # auth/permission → stop this connection now
                        auth_stop = True
                        log.warning("auto-sync auth error conn=%s — stopping connection", conn.id)
                        break
                    advanced_to = product.id                 # transient (timeout/429/5xx): skip, keep going
                    log.warning("auto-sync transient error product=%s: %s", product.id, e)
                except Exception as e:                       # one product never stops the rest of the batch
                    errors += 1
                    conn_errored = True
                    advanced_to = product.id
                    log.warning("auto-sync failed product=%s: %s", product.id, e)

            # Advance / wrap the cursor. A short batch means we reached the end of the list → wrap.
            if not auth_stop and len(products) < batch_size:
                conn.review_sync_cursor = None               # next round starts from the beginning
            else:
                conn.review_sync_cursor = advanced_to

            if conn_errored:
                conn.review_sync_fail_count = (conn.review_sync_fail_count or 0) + 1
                conn.review_sync_next_at = now + timedelta(seconds=_next_sync_delay(conn.review_sync_fail_count))
            else:
                conn.review_sync_fail_count = 0
                conn.review_sync_next_at = now + timedelta(seconds=SYNC_INTERVAL_SEC)  # 15-min cadence
            await db.commit()
    log.info("auto-sync reviews: imported=%s errors=%s throttled=%s", imported, errors, throttled)
    return {"imported": imported, "errors": errors, "throttled": throttled}


async def run_auto_draft_reviews() -> dict:
    """Draft a reply for freshly-synced SAFE/ATTENTION reviews. Not gated by the kill switch.

    SAFE + auto rule → status 'approved' (handed to the publish worker). SAFE + confirm, or ATTENTION
    (any mode) → status 'drafted' (waits for the seller). RISK → no draft, stays manual.
    """
    drafted = 0
    async with AsyncSessionLocal() as db:
        for rule in await _candidate_rules(db):
            conn = await _publishable_connection(db, rule)
            if conn is None:
                continue
            rows = (
                await db.execute(
                    select(ReviewResponse, Product.name)
                    .join(Product, ReviewResponse.product_id == Product.id)
                    .where(
                        Product.user_id == rule.user_id,
                        ReviewResponse.marketplace == conn.marketplace,
                        ReviewResponse.status == "pending",
                        ReviewResponse.external_review_id.isnot(None),
                    )
                )
            ).all()
            for review, product_name in rows:
                if (review.response_text or "").strip():
                    continue
                draft = build_draft(review.rating, review.review_text, product_name)
                if draft is None:                            # RISK — never auto-drafted for sending
                    continue
                review.response_text = draft
                if review.safety_category == "SAFE" and rule.mode == "auto":
                    review.status = "approved"               # hand to the publish worker
                else:
                    review.status = "drafted"                # waits for the seller's confirmation
                drafted += 1
            await db.commit()
    log.info("auto-draft reviews: drafted=%s", drafted)
    return {"drafted": drafted}
