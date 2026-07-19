"""Yandex Market Partner API client — reviews only.

Auth is a bare `Api-Key` header (no Bearer); the deprecated OAuth form is not used. Every
goods-feedback method is scoped to a CABINET (`businessId`) rather than a store, and the cabinet id
is discoverable from the key itself via GET /v2/campaigns — the seller never types it.

Throttling is HTTP 420 "Enhance Your Calm", not 429. base_client already retries it and maps it to
RATE_LIMIT, which the review-sync loop treats as connection-fatal, so a throttled cabinet stops
being called instead of being hammered.

Documented limits: reads 5 000/h, WRITES 1 000/h — the write ceiling is the binding one. Feedback
pages are cursor-based (`page_token`) with a documented maximum page size of 50.
"""
from __future__ import annotations

from config import settings
from .base_client import BaseMarketplaceClient
from .errors import ExecutionError

MARKETPLACE = "yandex"

# Documented maximum page size for goods-feedback. Asking for more is a client error, so this is a
# ceiling we clamp to rather than a preference.
MAX_PAGE_SIZE = 50

# A cursor that never terminates would spin forever on a malformed `nextPageToken`. 20 pages at 50
# per page is 1 000 reviews for one product — far past anything real, and a bounded failure.
MAX_PAGES = 20


class YandexClient:
    def __init__(self):
        self._api = BaseMarketplaceClient(settings.yandex_partner_base)

    async def _post(self, path: str, *, token: str, json: dict | None = None,
                    params: dict | None = None) -> dict:
        return await self._api.request("POST", path, token=token, auth_header="Api-Key",
                                       json=json, params=params)

    async def _get(self, path: str, *, token: str, params: dict | None = None) -> dict:
        return await self._api.request("GET", path, token=token, auth_header="Api-Key",
                                       params=params)

    # ── Cabinet discovery ─────────────────────────────────────────────────────
    async def resolve_business_id(self, *, token: str) -> str:
        """The cabinet id behind this Api-Key, from GET /v2/campaigns.

        An Api-Key is bound to ONE cabinet, so every campaign it returns should carry the same
        `business.id`. If it does not, we refuse rather than guess: picking the first would risk
        publishing a seller's replies into a cabinet that is not the one they meant.
        """
        data = await self._get("/v2/campaigns", token=token)
        campaigns = (data or {}).get("campaigns") or []
        ids = {
            str(c["business"]["id"])
            for c in campaigns
            if isinstance(c, dict) and isinstance(c.get("business"), dict)
            and c["business"].get("id") is not None
        }
        if not ids:
            raise ExecutionError(ExecutionError.AUTH,
                                 "yandex: no cabinet found for this Api-Key")
        if len(ids) > 1:
            # Never choose. The seller must resolve the ambiguity.
            raise ExecutionError(ExecutionError.VALIDATION,
                                 "yandex: the Api-Key covers more than one cabinet")
        return ids.pop()

    # ── Reviews (read) ────────────────────────────────────────────────────────
    async def list_reviews(self, *, token: str, business_id: str, offer_id: str,
                           limit: int = MAX_PAGE_SIZE) -> list[dict]:
        """Every feedback for one offer, following the page cursor to the end.

        `reactionStatus` is deliberately NOT used: a feedback leaves NEED_REACTION as soon as anyone
        opens it in the Yandex cabinet, so filtering on it would silently drop reviews because of
        something the seller did in another window. We read them all and let the DB unique index do
        the deduplication, exactly as WB and Ozon do.
        """
        page = min(int(limit), MAX_PAGE_SIZE)
        out: list[dict] = []
        token_param: str | None = None

        for _ in range(MAX_PAGES):
            params: dict = {"limit": page}
            if token_param:
                params["page_token"] = token_param
            data = await self._post(
                f"/v2/businesses/{business_id}/goods-feedback",
                token=token, json={"offerIds": [str(offer_id)]}, params=params,
            )
            result = (data or {}).get("result") or {}
            out.extend(result.get("feedbacks") or [])
            token_param = ((result.get("paging") or {}).get("nextPageToken")) or None
            if not token_param:
                break
        return out

    async def list_comments(self, *, token: str, business_id: str, feedback_id: str) -> list[dict]:
        """Comments on one feedback — our own replies included.

        This is what makes a safe recovery possible where the API offers no idempotency key: after an
        ambiguous publish we look for the reply instead of sending it again.
        """
        data = await self._post(
            f"/v2/businesses/{business_id}/goods-feedback/comments",
            token=token, json={"feedbackId": _as_int(feedback_id)},
            params={"limit": MAX_PAGE_SIZE},
        )
        return ((data or {}).get("result") or {}).get("comments") or []

    # ── Reviews (write) ───────────────────────────────────────────────────────
    async def publish_feedback_answer(self, *, token: str, business_id: str,
                                      feedback_id: str, text: str) -> dict:
        """Create a seller reply. Omitting `comment.id` means create; supplying it would mean edit.

        The response carries the whole comment — id and moderation status included — so the caller
        learns immediately whether the reply is live or still awaiting moderation.
        """
        return await self._post(
            f"/v2/businesses/{business_id}/goods-feedback/comments/update",
            token=token,
            json={"feedbackId": _as_int(feedback_id), "comment": {"text": text}},
        )


def _as_int(value) -> int:
    """Yandex feedback ids are integers; PULT stores external ids as strings."""
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        raise ExecutionError(ExecutionError.VALIDATION,
                             "yandex: feedback id is not a number") from None


yandex_client = YandexClient()
