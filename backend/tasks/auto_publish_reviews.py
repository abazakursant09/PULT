"""
L4 automation — auto-publish positive review answers.

Runs the SAME executor path a user would for L3, in `automated_l4` mode. Only
acts for users with an ENABLED AutomationRule(action=publish_review_response,
mode=auto) and only on positive reviews — the guard hard-blocks non-positive
auto-publish (negative → draft + escalate, never auto).

Wire into the scheduler; gated by settings.automation_enabled.
"""
from __future__ import annotations

import logging

from sqlalchemy import select

from config import settings
from datetime import datetime

from database import AsyncSessionLocal
from models.automation_rule import AutomationRule
from models.review_response import ReviewResponse
from models.product import Product
from services.marketplace import executor
from services.marketplace.guard import NEGATIVE_RATING_MAX

log = logging.getLogger(__name__)

_PUBLISHABLE = {"pending", "generated", "draft", "approved"}


async def run_auto_publish_reviews() -> dict:
    if not settings.automation_enabled:
        return {"ran": False, "reason": "automation disabled globally"}

    published, skipped = 0, 0
    async with AsyncSessionLocal() as db:
        rules = (
            await db.execute(
                select(AutomationRule).where(
                    AutomationRule.action_type == "publish_review_response",
                    AutomationRule.mode == "auto",
                    AutomationRule.enabled.is_(True),
                )
            )
        ).scalars().all()

        for rule in rules:
            rule_dict = {"enabled": rule.enabled, "guard": rule.guard or {}}
            # candidate positive, drafted, synced reviews for this user
            reviews = (
                await db.execute(
                    select(ReviewResponse)
                    .join(Product, ReviewResponse.product_id == Product.id)
                    .where(
                        Product.user_id == rule.user_id,
                        ReviewResponse.status.in_(_PUBLISHABLE),
                        ReviewResponse.external_review_id.isnot(None),
                        ReviewResponse.rating > NEGATIVE_RATING_MAX,
                    )
                )
            ).scalars().all()

            for review in reviews:
                if not (review.response_text or "").strip():
                    skipped += 1
                    continue
                res = await executor.execute(
                    db=db, user_id=rule.user_id,
                    action_type="publish_review_response",
                    payload={
                        "feedback_id": review.external_review_id,
                        "text": review.response_text,
                        "rating": review.rating,
                    },
                    mode="automated_l4",
                    insight_key="rating_good",
                    idempotency_key=f"review:{review.id}",
                    rule=rule_dict,
                )
                if res.ok:
                    review.status = "published"
                    review.execution_log_id = res.log_id
                    review.published_at = datetime.utcnow()
                    published += 1
                else:
                    skipped += 1
            await db.commit()

    log.info("auto-publish reviews: published=%s skipped=%s", published, skipped)
    return {"ran": True, "published": published, "skipped": skipped}
