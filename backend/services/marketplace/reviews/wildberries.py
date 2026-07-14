"""Wildberries review provider.

Holds all WB review knowledge — the payload field names and the read call — that used to sit
inline in the /sync router. Reading is real (wb_client.list_unanswered_feedbacks). Publishing is
interface-only in AR1: the existing /publish executor path is unchanged, so this method is not
wired into it yet.
"""
from __future__ import annotations

from datetime import datetime
from typing import Optional

from services.marketplace.wb_client import wb_client
from .base import NormalizedReview

_MARKETPLACE = "wildberries"

# WB feedback payload keys we read. Documented so the mapping lives here, not in the router.
_KEY_ID = "id"
_KEY_TEXT = "text"
_KEY_AUTHOR = "userName"
_KEY_RATING = "productValuation"
# Candidate date keys, tried defensively. We do NOT assume WB's exact field name — if none of
# these is present, review_created_at stays None rather than inventing a value.
_DATE_KEYS = ("createdDate", "created_date", "date", "createdAt")


def _parse_date(value) -> Optional[datetime]:
    if not value or not isinstance(value, str):
        return None
    try:
        # WB timestamps are ISO-8601, sometimes with a trailing Z.
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


class WildberriesReviewProvider:
    def supports_reviews(self) -> bool:
        return True

    async def fetch_reviews(self, token: str, product_ref: str) -> list[NormalizedReview]:
        feedbacks = await wb_client.list_unanswered_feedbacks(token=token, nm_id=product_ref)
        out: list[NormalizedReview] = []
        for fb in feedbacks:
            ext_id = str(fb.get(_KEY_ID) or "")
            if not ext_id:
                continue
            review_created_at = None
            for k in _DATE_KEYS:
                review_created_at = _parse_date(fb.get(k))
                if review_created_at is not None:
                    break
            out.append(NormalizedReview(
                marketplace=_MARKETPLACE,
                external_review_id=ext_id,
                product_ref=product_ref,
                rating=fb.get(_KEY_RATING),
                text=fb.get(_KEY_TEXT),
                author=(fb.get(_KEY_AUTHOR) or None),
                review_created_at=review_created_at,
                metadata={},
            ))
        return out

    async def publish_answer(self, token: str, external_review_id: str, text: str) -> dict:
        # Interface only for AR1. The live publish path stays on the executor route; this exists
        # so the contract is complete and a later phase can route through it.
        return await wb_client.publish_feedback_answer(
            token=token, feedback_id=external_review_id, text=text)
