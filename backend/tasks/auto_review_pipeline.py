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

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from database import AsyncSessionLocal
from models.automation_rule import AutomationRule
from models.marketplace_connection import MarketplaceConnection
from models.product import Product
from models.review_response import ReviewResponse
from services.review import ingest as review_ingest
from services.review.draft import build_draft
from services.marketplace import review_automation_gate as gate

log = logging.getLogger(__name__)

_ACTION = gate.REVIEW_ACTION


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


async def run_auto_sync_reviews() -> dict:
    """Fetch new reviews for every eligible rule's connection. Not gated by the kill switch."""
    imported = 0
    errors = 0
    async with AsyncSessionLocal() as db:
        for rule in await _candidate_rules(db):
            conn = await _publishable_connection(db, rule)
            if conn is None:
                continue
            products = (
                await db.execute(
                    select(Product).where(
                        Product.user_id == rule.user_id,
                        Product.marketplace == conn.marketplace,
                    )
                )
            ).scalars().all()
            for product in products:
                try:
                    res = await review_ingest.sync_product_reviews(db, product)
                    imported += res["imported"]
                except Exception as e:                       # one product/store never stops the rest
                    errors += 1
                    log.warning("auto-sync failed product=%s: %s", product.id, e)
    log.info("auto-sync reviews: imported=%s errors=%s", imported, errors)
    return {"imported": imported, "errors": errors}


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
