"""Yandex Market Partner API client — reviews only.

Auth is a bare `Api-Key` header (no Bearer); the deprecated OAuth form is not used. Every
goods-feedback method is scoped to a CABINET (`businessId`) rather than a store, and the cabinet id
is discoverable from the key itself via GET /v2/campaigns — the seller never types it.

Throttling is HTTP 420 "Enhance Your Calm", not 429. base_client already retries it and maps it to
RATE_LIMIT, which the review-sync loop treats as connection-fatal, so a throttled cabinet stops
being called instead of being hammered.

Documented limits: reads 5 000/h, WRITES 1 000/h — the write ceiling is the binding one. Feedback
pages are cursor-based (`pageToken`) with a documented maximum page size of 50.
"""
from __future__ import annotations

from config import settings
from .base_client import BaseMarketplaceClient
from .errors import ExecutionError, MarketplacePaginationIncomplete
from .ym_review_read_hold import selected_yandex_operation

MARKETPLACE = "yandex"

# Documented maximum page size for goods-feedback. Asking for more is a client error, so this is a
# ceiling we clamp to rather than a preference.
MAX_PAGE_SIZE = 50

# Local resource bound, not a guarantee of completeness: a continuing cursor at the cap fails.
MAX_PAGES = 20


class YandexClient:
    def __init__(self):
        self._api = BaseMarketplaceClient(settings.yandex_partner_base)

    async def _post(self, path: str, *, token: str, json: dict | None = None,
                    params: dict | None = None, retry_safe: bool = False) -> dict:
        return await self._api.request("POST", path, token=token, auth_header="Api-Key",
                                       json=json, params=params, retry_safe=retry_safe)

    async def _get(self, path: str, *, token: str, params: dict | None = None,
                   retry_safe: bool = False) -> dict:
        return await self._api.request("GET", path, token=token, auth_header="Api-Key",
                                       params=params, retry_safe=retry_safe)

    # ── Cabinet discovery ─────────────────────────────────────────────────────
    async def resolve_business_id(self, *, token: str) -> str:
        """The cabinet id behind this Api-Key, from GET /v2/campaigns.

        An Api-Key is bound to ONE cabinet, so every campaign it returns should carry the same
        `business.id`. If it does not, we refuse rather than guess: picking the first would risk
        publishing a seller's replies into a cabinet that is not the one they meant.
        """
        with selected_yandex_operation("resolve_business_id", client=self._api):
            data = await self._get("/v2/campaigns", token=token, retry_safe=True)
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
        try:
            page = min(int(limit), MAX_PAGE_SIZE)
        except (TypeError, ValueError, OverflowError):
            raise ExecutionError(ExecutionError.VALIDATION,
                                 "yandex: review limit is not a positive number") from None
        if page <= 0:
            raise ExecutionError(ExecutionError.VALIDATION,
                                 "yandex: review limit is not a positive number")
        out: list[dict] = []
        token_param: str | None = None
        seen_tokens: set[str] = set()

        for _ in range(MAX_PAGES):
            params: dict = {"limit": page}
            if token_param:
                params["pageToken"] = token_param
            with selected_yandex_operation("reviews", business_id=business_id, client=self._api):
                data = await self._post(
                    f"/v2/businesses/{business_id}/goods-feedback",
                    token=token, json={"offerIds": [str(offer_id)]}, params=params, retry_safe=True,
                )
            rows, token_param = _pagination_page(data, "feedbacks")
            out.extend(rows)
            if token_param is None:
                return out
            if token_param in seen_tokens:
                raise MarketplacePaginationIncomplete("cycle")
            seen_tokens.add(token_param)
        raise MarketplacePaginationIncomplete("page_limit")

    async def list_comments(self, *, token: str, business_id: str, feedback_id: str) -> list[dict]:
        """Comments on one feedback — our own replies included.

        This is what makes a safe recovery possible where the API offers no idempotency key: after an
        ambiguous publish we look for the reply instead of sending it again.
        """
        out: list[dict] = []
        token_param: str | None = None
        seen_tokens: set[str] = set()
        for _ in range(MAX_PAGES):
            params: dict = {"limit": MAX_PAGE_SIZE}
            if token_param:
                params["pageToken"] = token_param
            with selected_yandex_operation("comments", business_id=business_id, client=self._api):
                data = await self._post(
                    f"/v2/businesses/{business_id}/goods-feedback/comments",
                    token=token, json={"feedbackId": _as_int(feedback_id)},
                    params=params, retry_safe=True,
                )
            rows, token_param = _pagination_page(data, "comments")
            out.extend(rows)
            if token_param is None:
                return out
            if token_param in seen_tokens:
                raise MarketplacePaginationIncomplete("cycle")
            seen_tokens.add(token_param)
        raise MarketplacePaginationIncomplete("page_limit")

    # ── Reviews (write) ───────────────────────────────────────────────────────
    async def publish_feedback_answer(self, *, token: str, business_id: str,
                                      feedback_id: str, text: str) -> dict:
        """Create a seller reply. Omitting `comment.id` means create; supplying it would mean edit.

        The response carries the whole comment — id and moderation status included — so the caller
        learns immediately whether the reply is live or still awaiting moderation.
        """
        request = build_publish_feedback_request(
            business_id=business_id, feedback_id=feedback_id, text=text)
        with selected_yandex_operation("publish_feedback_answer", business_id=business_id, client=self._api):
            return await self._post(request["path"], token=token, json=request["json"], retry_safe=False)

    # ── Campaign discovery (PULT-LAUNCH-1.4.5G) ───────────────────────────────
    async def list_campaigns(self, *, token: str) -> list[dict]:
        """Every campaign (store) the Api-Key can reach, from GET /v2/campaigns.

        A campaign is a Yandex STORE inside a business CABINET; the two ids are distinct axes
        (`business.id` = cabinet, `id` = store) and must never be conflated. Returns a SAFE
        projection only — ids and a display label — never the raw payload. The name is a label
        for a human to recognise, never used to auto-link anything.
        """
        with selected_yandex_operation("list_campaigns", client=self._api):
            data = await self._get("/v2/campaigns", token=token, retry_safe=True)
        campaigns = (data or {}).get("campaigns") or []
        out: list[dict] = []
        for c in campaigns:
            if not isinstance(c, dict):
                continue
            business = c.get("business") if isinstance(c.get("business"), dict) else {}
            cid = c.get("id")
            if cid is None:
                continue
            out.append({
                "campaign_id": str(cid),
                "business_id": (str(business["id"]) if business.get("id") is not None else None),
                "label": _s(c.get("clientId")) or _s((c.get("domain"))) or _s(business.get("name")),
                "placement_type": _s(c.get("placementType")),
            })
        return out

    # ── Ingest reads (PULT-LAUNCH-1.4.5G) ─────────────────────────────────────
    # Products/cards live at BUSINESS level (offer-mappings); prices/stocks/orders/returns live at
    # CAMPAIGN level. That split is real in the API and preserved here — the provider passes the
    # right id to the right read, never a conflated one.
    async def offer_mappings(self, *, token: str, business_id: str,
                             page_token: str | None = None, limit: int = 200) -> dict:
        """POST /v2/businesses/{businessId}/offer-mappings → cards+identity for the whole business.
        result.offerMappings[].offer (offerId, name, category, vendor, ...) + .mapping (marketSku)."""
        params: dict = {"limit": int(limit)}
        if page_token:
            params["page_token"] = page_token
        with selected_yandex_operation("offer_mappings", business_id=business_id, client=self._api):
            return await self._post(f"/v2/businesses/{business_id}/offer-mappings",
                                    token=token, json={}, params=params, retry_safe=True)

    async def campaign_prices(self, *, token: str, campaign_id: str,
                              page_token: str | None = None, limit: int = 500) -> dict:
        """GET /v2/campaigns/{campaignId}/offer-prices → result.offers[] {id, price:{value,
        currencyId, discountBase}, marketSku, updatedAt}, paging.nextPageToken. Store-level."""
        params: dict = {"limit": int(limit)}
        if page_token:
            params["pageToken"] = page_token
        with selected_yandex_operation("campaign_prices", campaign_id=campaign_id, client=self._api):
            return await self._get(f"/v2/campaigns/{campaign_id}/offer-prices",
                                   token=token, params=params, retry_safe=True)

    async def campaign_stocks(self, *, token: str, campaign_id: str,
                              page_token: str | None = None, limit: int = 200) -> dict:
        """POST /v2/campaigns/{campaignId}/offers/stocks → result.warehouses[] {warehouseId,
        offers[] {offerId, stocks[] {type, count}}}, paging.nextPageToken. Store-level."""
        params: dict = {"limit": int(limit)}
        if page_token:
            params["pageToken"] = page_token
        with selected_yandex_operation("campaign_stocks", campaign_id=campaign_id, client=self._api):
            return await self._post(f"/v2/campaigns/{campaign_id}/offers/stocks",
                                    token=token, json={}, params=params, retry_safe=True)

    async def campaign_orders(self, *, token: str, campaign_id: str, from_date: str, to_date: str,
                              page_token: str | None = None, limit: int = 50) -> dict:
        """GET /v2/campaigns/{campaignId}/orders → orders[] {id, status, substatus, creationDate,
        items[] {offerId, count}, itemsTotal}, paging.nextPageToken. Store-level. Dates DD-MM-YYYY."""
        params: dict = {"fromDate": from_date, "toDate": to_date, "limit": int(limit)}
        if page_token:
            params["pageToken"] = page_token
        with selected_yandex_operation("campaign_orders", campaign_id=campaign_id, client=self._api):
            return await self._get(f"/v2/campaigns/{campaign_id}/orders",
                                   token=token, params=params, retry_safe=True)

    async def campaign_returns(self, *, token: str, campaign_id: str,
                               page_token: str | None = None, limit: int = 50) -> dict:
        """GET /v2/campaigns/{campaignId}/returns?type=RETURN → result.returns[] {id, orderId,
        creationDate, items[]}, paging.nextPageToken. Store-level. type=RETURN excludes UNREDEEMED
        (a non-purchase is not a return)."""
        params: dict = {"type": "RETURN", "limit": int(limit)}
        if page_token:
            params["pageToken"] = page_token
        with selected_yandex_operation("campaign_returns", campaign_id=campaign_id, client=self._api):
            return await self._get(f"/v2/campaigns/{campaign_id}/returns",
                                   token=token, params=params, retry_safe=True)

    # ── Promotions READ (PULT-LAUNCH-2.5D-Yandex-B3) ──────────────────────────
    # BUSINESS-level, read-only, evidence-only. These NEVER change a product's promotion state —
    # adding/removing a product from a promo is the separate updatePromoOffers/deletePromoOffers write
    # path, which is out of scope and never called here.
    async def list_promos(self, *, token: str, business_id: str) -> list[dict]:
        """POST /v2/businesses/{businessId}/promos → data.promotions[] ({id, name, mechanicsInfo.type,
        participating, period{dateTimeFrom,dateTimeTo}, ...}). Market promos only (not seller-created)."""
        with selected_yandex_operation("list_promos", business_id=business_id, client=self._api):
            data = await self._post(f"/v2/businesses/{business_id}/promos", token=token, json={}, retry_safe=True)
        proms = ((data or {}).get("data") or {}).get("promotions")
        return proms if isinstance(proms, list) else []

    async def list_promo_offers(self, *, token: str, business_id: str, promo_id: str,
                                page_token: str | None = None, limit: int = 500) -> dict:
        """POST /v2/businesses/{businessId}/promos/offers → {result? / data:{offers[] {offerId, status,
        params.discountParams.{price,promoPrice,maxPromoPrice}, autoParticipatingDetails.campaignIds}},
        paging.nextPageToken}. status ∈ AUTO|PARTIALLY_AUTO|MANUAL|NOT_PARTICIPATING|… ; only
        PARTIALLY_AUTO carries campaignIds. limit≤500 + pageToken. Returns the raw dict (the ingest
        layer validates the shape and fails closed on a broken page)."""
        params: dict = {"limit": int(limit)}
        if page_token:
            params["pageToken"] = page_token
        with selected_yandex_operation("list_promo_offers", business_id=business_id, client=self._api):
            return await self._post(f"/v2/businesses/{business_id}/promos/offers",
                                    token=token, json={"promoId": str(promo_id)}, params=params, retry_safe=True)


def build_publish_feedback_request(*, business_id, feedback_id, text):
    """Pure exact publish request shared by claim preparation and actual dispatch."""
    return {"method": "POST", "path": f"/v2/businesses/{business_id}/goods-feedback/comments/update",
            "auth_header": "Api-Key", "extra_headers": None,
            "json": {"feedbackId": _as_int(feedback_id), "comment": {"text": text}}, "params": None}


def _pagination_page(data, rows_key: str) -> tuple[list[dict], str | None]:
    """Validate one read page without treating malformed/partial data as empty."""
    if not isinstance(data, dict) or ("status" in data and data["status"] != "OK"):
        raise MarketplacePaginationIncomplete("schema")
    result = data.get("result")
    if not isinstance(result, dict) or not isinstance(result.get(rows_key), list):
        raise MarketplacePaginationIncomplete("schema")
    paging = result.get("paging", {})
    if not isinstance(paging, dict):
        raise MarketplacePaginationIncomplete("schema")
    next_token = paging.get("nextPageToken")
    if next_token is None or next_token == "":
        return result[rows_key], None
    if not isinstance(next_token, str):
        raise MarketplacePaginationIncomplete("schema")
    return result[rows_key], next_token


def _s(value) -> str | None:
    if value is None:
        return None
    s = str(value).strip()
    return s or None


def _as_int(value) -> int:
    """Yandex feedback ids are integers; PULT stores external ids as strings."""
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        raise ExecutionError(ExecutionError.VALIDATION,
                             "yandex: feedback id is not a number") from None


yandex_client = YandexClient()
