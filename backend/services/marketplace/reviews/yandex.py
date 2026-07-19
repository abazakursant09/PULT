"""Yandex Market review provider.

Two things here carry real risk and are worth reading before changing anything.

1. THE TEXT. Yandex splits a review into three separate fields — `advantages`, `disadvantages` and
   a free-form `comment`. The safety classifier reads the review TEXT and looks for complaint
   markers in it. Build the text from `comment` alone and a review whose complaint lives entirely in
   `disadvantages` classifies as SAFE and gets auto-published. So all three are joined, always, and
   a test pins it.

2. THE CABINET. Every goods-feedback call is scoped to a `businessId`, which is discovered from the
   Api-Key rather than typed by the seller. It travels here inside the credential string
   ("<business_id>:<api_key>"), the same shape Ozon uses for its Client-Id — so the provider
   contract stays `(token, product_ref)` and nothing upstream learns what Yandex is.
"""
from __future__ import annotations

import logging
from datetime import datetime
from typing import Optional

from ..errors import ExecutionError
from ..yandex_client import yandex_client
from .base import NormalizedReview

log = logging.getLogger(__name__)

MARKETPLACE = "yandex"

_ADVANTAGES_LABEL = "Достоинства"
_DISADVANTAGES_LABEL = "Недостатки"


def _split_credential(credential: str) -> tuple[str, str]:
    """Unpack "<business_id>:<api_key>". A missing half is an auth problem, not a parse problem."""
    raw = credential or ""
    if ":" not in raw:
        raise ExecutionError(
            ExecutionError.AUTH,
            "Yandex review credential must be '<business_id>:<api_key>'",
        )
    business_id, _, api_key = raw.partition(":")
    if not business_id.strip() or not api_key.strip():
        raise ExecutionError(ExecutionError.AUTH, "Yandex review credential incomplete")
    return business_id.strip(), api_key.strip()


def compose_text(description: dict | None) -> Optional[str]:
    """Join the three parts of a Yandex review into the one text PULT stores and classifies.

    Empty parts are skipped entirely — no label without a body, no stray "None", no blank lines. If
    nothing at all was written, the result is None (a rating-only review), which the classifier
    already understands.
    """
    d = description if isinstance(description, dict) else {}
    parts: list[str] = []
    for label, key in ((_ADVANTAGES_LABEL, "advantages"), (_DISADVANTAGES_LABEL, "disadvantages")):
        value = (d.get(key) or "").strip() if isinstance(d.get(key), str) else ""
        if value:
            parts.append(f"{label}: {value}")
    comment = (d.get("comment") or "").strip() if isinstance(d.get("comment"), str) else ""
    if comment:
        parts.append(comment)
    return "\n".join(parts) if parts else None


def _parse_date(value) -> Optional[datetime]:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).replace(tzinfo=None)
    except ValueError:
        return None


def _rating(statistics) -> Optional[int]:
    """`statistics.rating` is nested — a flat key lookup would silently find nothing."""
    if not isinstance(statistics, dict):
        return None
    value = statistics.get("rating")
    try:
        return int(value) if value is not None else None
    except (TypeError, ValueError):
        return None


class YandexReviewProvider:
    """Reads reviews and publishes seller replies through the Partner API."""

    def supports_reviews(self) -> bool:
        return True

    async def fetch_reviews(self, token: str, product_ref: str) -> list[NormalizedReview]:
        business_id, api_key = _split_credential(token)
        raw = await yandex_client.list_reviews(
            token=api_key, business_id=business_id, offer_id=product_ref,
        )

        out: list[NormalizedReview] = []
        for item in raw:
            if not isinstance(item, dict):
                log.warning("yandex review skipped: payload is %s, not an object", type(item).__name__)
                continue
            ext_id = item.get("feedbackId")
            if ext_id in (None, ""):
                # Never drop a review in silence. Log the SHAPE only — keys, never values — so the
                # payload can be diagnosed without putting a buyer's words in the log.
                log.warning("yandex review skipped: no feedbackId; payload keys=%s",
                            sorted(item.keys()))
                continue
            identifiers = item.get("identifiers") if isinstance(item.get("identifiers"), dict) else {}
            out.append(NormalizedReview(
                marketplace=MARKETPLACE,
                external_review_id=str(ext_id),
                product_ref=str(identifiers.get("offerId") or product_ref),
                rating=_rating(item.get("statistics")),
                text=compose_text(item.get("description")),
                author=item.get("author") or None,
                review_created_at=_parse_date(item.get("createdAt")),
                metadata={"need_reaction": item.get("needReaction")},
            ))
        return out

    async def publish_answer(self, token: str, external_review_id: str, text: str) -> dict:
        business_id, api_key = _split_credential(token)
        return await yandex_client.publish_feedback_answer(
            token=api_key, business_id=business_id,
            feedback_id=external_review_id, text=text,
        )

    # ── Cabinet discovery (optional provider capability) ──────────────────────
    async def resolve_account_ref(self, token: str) -> str:
        """The cabinet id for this Api-Key. Cached by the caller on the credential row."""
        return await yandex_client.resolve_business_id(token=token)

    # ── Moderation (optional provider capability) ─────────────────────────────
    async def answer_status(self, token: str, external_review_id: str,
                            comment_id: str | None) -> Optional[str]:
        """The current state of OUR reply to this review: PUBLISHED / UNMODERATED / BANNED / DELETED.

        Returns None when the reply cannot be found — which is a genuine "unknown", not a failure.
        The caller must never turn that into a re-send: this method also exists so an ambiguous
        publish can be resolved by LOOKING instead of posting again, since the Partner API offers no
        idempotency key.
        """
        business_id, api_key = _split_credential(token)
        comments = await yandex_client.list_comments(
            token=api_key, business_id=business_id, feedback_id=external_review_id,
        )
        wanted = str(comment_id) if comment_id is not None else None
        for c in comments:
            if not isinstance(c, dict):
                continue
            if wanted is not None and str(c.get("id")) == wanted:
                return c.get("status")
        if wanted is None:
            # No id to match on (an ambiguous publish never returned one). A reply authored by the
            # BUSINESS is ours; anything else belongs to the buyer.
            for c in comments:
                author = c.get("author") if isinstance(c, dict) else None
                if isinstance(author, dict) and author.get("type") == "BUSINESS":
                    return c.get("status")
        return None
