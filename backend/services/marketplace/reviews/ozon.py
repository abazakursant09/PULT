"""Ozon review provider (R-OZ1).

Implements the marketplace-neutral `ReviewProvider` contract for Ozon. All Ozon-specific knowledge
— the dual credential (Api-Key + Client-Id), the review payload field names, the premium Reviews
API — lives here, never in the router or the executor.

Credential encoding: Ozon needs BOTH an Api-Key and a Client-Id, but the `ReviewProvider` contract
passes a single `token`. To keep the contract unchanged, this provider expects `token` in the
Ozon-specific composite form ``"<client_id>:<api_key>"`` and splits it internally. The R-OZ2
executor wiring builds that composite from `MarketplaceConnection.ozon_client_id` + the stored
Api-Key credential; the split lives here so the marketplace detail stays inside the provider.

Enablement: the fetch/publish methods are real and unit-tested, but `supports_reviews()` returns
False until R-OZ3 wires the publish dispatcher and flips `capability_registry.pult_supported`. That
keeps the /sync router honestly closed for Ozon (mirrors `pult_supported: false`) — no premature
enablement, WB behaviour unchanged.
"""
from __future__ import annotations

from datetime import datetime
from typing import Optional

from services.marketplace.ozon_client import ozon_client
from services.marketplace.errors import ExecutionError
from .base import NormalizedReview

_MARKETPLACE = "ozon"

# R-OZ3: Ozon reviews enabled, together with capability_registry pult_supported=true for ozon.
# The registry flag and this flag move as a pair so the executor gate (reads the registry) and the
# /sync router + dispatcher belt (read supports_reviews()) can never disagree.
_ENABLED = True

# Ozon review payload keys, tried defensively — we do not assume Ozon's exact field name.
_ID_KEYS = ("id", "review_id", "uuid")
_TEXT_KEYS = ("text", "comment")
_RATING_KEYS = ("rating", "mark", "grade")
_AUTHOR_KEYS = ("author_name", "author", "user_name")
_DATE_KEYS = ("published_at", "created_at", "publishedAt", "createdAt", "date")


def _first(d: dict, keys) -> Optional[object]:
    for k in keys:
        v = d.get(k)
        if v not in (None, ""):
            return v
    return None


def _parse_date(value) -> Optional[datetime]:
    if not value or not isinstance(value, str):
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def _split_credential(token: str) -> tuple[str, str]:
    """`"<client_id>:<api_key>"` → (client_id, api_key). Ozon-specific composite."""
    if not token or ":" not in token:
        raise ExecutionError(
            ExecutionError.AUTH,
            "Ozon review credential must be '<client_id>:<api_key>'",
        )
    client_id, api_key = token.split(":", 1)
    if not client_id or not api_key:
        raise ExecutionError(ExecutionError.AUTH, "Ozon review credential incomplete")
    return client_id, api_key


class OzonReviewProvider:
    def supports_reviews(self) -> bool:
        return _ENABLED

    async def fetch_reviews(self, token: str, product_ref: str) -> list[NormalizedReview]:
        client_id, api_key = _split_credential(token)
        rows = await ozon_client.list_reviews(
            token=api_key, client_id=client_id, product_ref=product_ref)
        out: list[NormalizedReview] = []
        for r in rows:
            if not isinstance(r, dict):
                continue
            ext_id = _first(r, _ID_KEYS)
            if not ext_id:
                continue
            rating = _first(r, _RATING_KEYS)
            out.append(NormalizedReview(
                marketplace=_MARKETPLACE,
                external_review_id=str(ext_id),
                product_ref=product_ref,
                rating=int(rating) if isinstance(rating, (int, float)) else None,
                text=_first(r, _TEXT_KEYS),
                author=(_first(r, _AUTHOR_KEYS) or None),
                review_created_at=_parse_date(_first(r, _DATE_KEYS)),
                metadata={"status": r.get("status")} if r.get("status") else {},
            ))
        return out

    async def publish_answer(self, token: str, external_review_id: str, text: str) -> dict:
        client_id, api_key = _split_credential(token)
        return await ozon_client.publish_feedback_answer(
            token=api_key, client_id=client_id, review_id=external_review_id, text=text)
