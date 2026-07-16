"""
L4 automation — auto-publish SAFE review answers (AR4-hardened).

Runs the SAME executor path a user would for L3, in `automated_l4` mode. Only acts for users with
an ENABLED AutomationRule(action=publish_review_response, mode=auto), only on reviews the safety
policy marked SAFE (a 5★ with a complaint marker is RISK and never qualifies), and only within the
rule's data-driven policy (marketplaces / min_rating / caps). Every failure is recorded with a
backoff and a hard attempt ceiling; a run of consecutive failures trips a circuit breaker that
skips the rule. Wired into the scheduler; gated by settings.automation_enabled (OFF by default).
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta

from sqlalchemy import select

from config import settings
from database import AsyncSessionLocal
from models.automation_rule import AutomationRule
from models.review_response import ReviewResponse
from models.product import Product
from models.user import User
from models.execution_log import ExecutionLog
from services.marketplace import executor
from services.marketplace.guard import NEGATIVE_RATING_MAX

log = logging.getLogger(__name__)

# Statuses a review may hold before it is really published. A row moved to a terminal "failed"
# (attempt ceiling reached) is not here, so it is never re-selected.
_PUBLISHABLE = {"pending", "generated", "draft", "approved"}

_SAFE = "SAFE"
_MAX_ATTEMPTS = 5                 # hard ceiling — after this the row is terminally failed
_BACKOFF_BASE_MIN = 5             # exponential backoff base (minutes)
_BACKOFF_CAP_MIN = 24 * 60        # never wait more than a day
_BREAKER_FAILS = 3                # consecutive recent failures that trip the breaker
_BREAKER_WINDOW_MIN = 60          # window the breaker looks back over


def _backoff_minutes(attempts: int) -> int:
    return min(_BACKOFF_BASE_MIN * (2 ** max(0, attempts - 1)), _BACKOFF_CAP_MIN)


async def _breaker_tripped(db, user_id: str) -> bool:
    """True if the most recent review-publish attempts for this user are a run of failures.

    Computed from the append-only ExecutionLog — no new table. If the last _BREAKER_FAILS attempts
    within the window are all non-success, the rule is skipped this tick (fail-closed).
    """
    since = datetime.utcnow() - timedelta(minutes=_BREAKER_WINDOW_MIN)
    recent = (await db.execute(
        select(ExecutionLog.status).where(
            ExecutionLog.user_id == user_id,
            ExecutionLog.action_type == "publish_review_response",
            ExecutionLog.mode == "automated_l4",
            ExecutionLog.created_at >= since,
        ).order_by(ExecutionLog.created_at.desc()).limit(_BREAKER_FAILS)
    )).scalars().all()
    return len(recent) >= _BREAKER_FAILS and all(s != "success" for s in recent)


async def run_auto_publish_reviews() -> dict:
    if not settings.automation_enabled:
        return {"ran": False, "reason": "automation disabled globally"}

    published, skipped, deferred, terminal = 0, 0, 0, 0
    now = datetime.utcnow()

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
            # Circuit breaker: a run of consecutive failures skips the rule until it clears.
            if await _breaker_tripped(db, rule.user_id):
                log.warning("auto-publish breaker tripped for user=%s — skipping rule", rule.user_id)
                continue

            policy = rule.guard or {}
            marketplaces = policy.get("marketplaces") or []
            min_rating = policy.get("min_rating")
            rule_dict = {"enabled": rule.enabled, "guard": policy}

            # Candidate SAFE, synced reviews for this user, within the rule's data-driven policy.
            # Ownership is via product → user; soft-deleted sellers are excluded; a row still in
            # backoff (retry_next_at in the future) is not re-selected.
            stmt = (
                select(ReviewResponse)
                .join(Product, ReviewResponse.product_id == Product.id)
                .join(User, User.id == Product.user_id)
                .where(
                    Product.user_id == rule.user_id,
                    User.deleted_at.is_(None),                          # G5: not soft-deleted
                    ReviewResponse.status.in_(_PUBLISHABLE),
                    ReviewResponse.external_review_id.isnot(None),
                    ReviewResponse.safety_category == _SAFE,             # G1: SAFE only
                    ReviewResponse.rating > NEGATIVE_RATING_MAX,
                )
            )
            if marketplaces:                                            # G6: data-driven scope
                stmt = stmt.where(ReviewResponse.marketplace.in_(marketplaces))
            if min_rating is not None:
                stmt = stmt.where(ReviewResponse.rating >= int(min_rating))
            reviews = (await db.execute(stmt)).scalars().all()

            for review in reviews:
                # Respect backoff: skip a row waiting for its next attempt.
                if review.retry_next_at is not None and review.retry_next_at > now:
                    deferred += 1
                    continue
                if not (review.response_text or "").strip():
                    skipped += 1
                    continue

                res = await executor.execute(
                    db=db, user_id=rule.user_id,
                    action_type="publish_review_response",
                    payload={
                        "marketplace": review.marketplace,   # R-OZ2: routes to the review's provider
                        "feedback_id": review.external_review_id,
                        "text": review.response_text,
                        "rating": review.rating,
                        "safety_category": review.safety_category,      # for the guard SAFE-gate
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
                elif res.status in ("ambiguous", "needs_reconcile"):
                    # AR5: the write may have committed on the marketplace — NEVER auto-retry.
                    # Mark terminal and tell the seller to verify the cabinet by hand.
                    review.publication_attempts = (review.publication_attempts or 0) + 1
                    review.failure_reason = "публикация не подтверждена — проверьте кабинет маркетплейса"
                    review.status = "failed"
                    review.retry_next_at = None
                    terminal += 1
                else:
                    review.publication_attempts = (review.publication_attempts or 0) + 1
                    review.failure_reason = (str(res.error) if res.error else "auto-publish failed")[:255]
                    if review.publication_attempts >= _MAX_ATTEMPTS:
                        # Terminal: stop retrying — move to a non-publishable status so it is never
                        # re-selected, and surface it as a Failed row in the workspace.
                        review.status = "failed"
                        review.retry_next_at = None
                        terminal += 1
                    else:
                        review.retry_next_at = now + timedelta(minutes=_backoff_minutes(review.publication_attempts))
                        skipped += 1
            await db.commit()

    log.info("auto-publish reviews: published=%s skipped=%s deferred=%s terminal=%s",
             published, skipped, deferred, terminal)
    return {"ran": True, "published": published, "skipped": skipped,
            "deferred": deferred, "terminal": terminal}
