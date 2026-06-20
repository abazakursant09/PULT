"""
Wildberries client. ME-2 implements the Feedbacks domain (list unanswered
feedbacks, publish an answer). Other domains (prices, advert, content) are
declared as method stubs to make the next vertical slices explicit; they raise
until implemented so nothing silently no-ops.

WB Feedbacks API (https://feedbacks-api.wildberries.ru):
  GET  /api/v1/feedbacks?isAnswered=false&take=&skip=[&nmId=]
  POST /api/v1/feedbacks/answer            body: {"id": "<feedbackId>", "text": "<answer>"}
Auth: the API token is passed in the Authorization header (token value as-is).
"""
from __future__ import annotations

from config import settings
from .base_client import BaseMarketplaceClient
from .errors import ExecutionError

MARKETPLACE = "wildberries"


class WBClient:
    def __init__(self):
        self._feedbacks = BaseMarketplaceClient(settings.wb_feedbacks_base)
        self._prices = BaseMarketplaceClient(settings.wb_prices_base)

    # ── Feedbacks (ME-2) ──────────────────────────────────────────────────────
    async def list_unanswered_feedbacks(
        self, *, token: str, nm_id: str | None = None, take: int = 50, skip: int = 0
    ) -> list[dict]:
        params = {"isAnswered": "false", "take": take, "skip": skip}
        if nm_id:
            params["nmId"] = nm_id
        data = await self._feedbacks.request(
            "GET", "/api/v1/feedbacks", token=token, params=params
        )
        # WB wraps payload in {"data": {"feedbacks": [...]}}
        return (data.get("data") or {}).get("feedbacks", []) if isinstance(data, dict) else []

    async def publish_feedback_answer(
        self, *, token: str, feedback_id: str, text: str
    ) -> dict:
        """Publish an answer to a single feedback. Returns the API response."""
        return await self._feedbacks.request(
            "POST",
            "/api/v1/feedbacks/answer",
            token=token,
            json={"id": feedback_id, "text": text},
        )

    # ── Prices (ME-3) ─────────────────────────────────────────────────────────
    async def set_price(self, *, token: str, offer_id: str, price: float,
                         discount: float | None = None) -> dict:
        """
        WB Discounts-Prices API. offer_id == nmID. Prices are integer rubles.
            POST /api/v2/upload/task  body {"data":[{"nmID":..,"price":..,"discount":..}]}
        """
        try:
            nm_id = int(offer_id)
        except (TypeError, ValueError):
            raise ExecutionError(
                ExecutionError.VALIDATION,
                "Wildberries requires a numeric nmID as offer_id (SKU is not numeric)",
            )
        item: dict = {"nmID": nm_id, "price": int(round(price))}
        if discount is not None:
            item["discount"] = int(discount)
        return await self._prices.request(
            "POST", "/api/v2/upload/task", token=token, json={"data": [item]}
        )

    async def set_discount(self, *, token: str, offer_id: str, discount: float) -> dict:
        """
        WB Discounts-Prices API — update the discount only (A2 reduce_discount).
            POST /api/v2/upload/task  body {"data":[{"nmID":..,"discount":..}]}
        """
        try:
            nm_id = int(offer_id)
        except (TypeError, ValueError):
            raise ExecutionError(
                ExecutionError.VALIDATION,
                "Wildberries requires a numeric nmID as offer_id (SKU is not numeric)",
            )
        return await self._prices.request(
            "POST", "/api/v2/upload/task", token=token,
            json={"data": [{"nmID": nm_id, "discount": int(discount)}]},
        )

    async def set_auto_promotion(self, *, token: str, offer_id: str, enabled: bool) -> dict:
        """
        WB promotions participation (A3 stop_auto_promotion). enabled=False
        declines automatic promotion participation for the listing.
            POST /api/v1/promotions/participation  {"nmID":.., "participate": bool}
        """
        try:
            nm_id = int(offer_id)
        except (TypeError, ValueError):
            raise ExecutionError(
                ExecutionError.VALIDATION,
                "Wildberries requires a numeric nmID as offer_id (SKU is not numeric)",
            )
        return await self._prices.request(
            "POST", "/api/v1/promotions/participation", token=token,
            json={"nmID": nm_id, "participate": bool(enabled)},
        )

    # ── Advertising (ME-4) ────────────────────────────────────────────────────
    async def set_bid(self, *, token: str, campaign_id: int, cpm: int,
                      adv_type: int, param: int | None = None) -> dict:
        body: dict = {"advertId": int(campaign_id), "type": int(adv_type), "cpm": int(cpm)}
        if param is not None:
            body["param"] = int(param)
        return await self._advert().request("POST", "/adv/v0/cpm", token=token, json=body)

    async def set_campaign_state(self, *, token: str, campaign_id: int, action: str) -> dict:
        # action: "start" | "pause"
        path = "/adv/v0/start" if action == "start" else "/adv/v0/pause"
        return await self._advert().request(
            "GET", path, token=token, params={"id": int(campaign_id)}
        )

    def _advert(self) -> "BaseMarketplaceClient":
        # lazy: advert base only needed for ME-4
        if not hasattr(self, "_advert_client"):
            self._advert_client = BaseMarketplaceClient(settings.wb_advert_base)
        return self._advert_client

    # ── Content / SEO (ME-5) ──────────────────────────────────────────────────
    async def update_card(self, *, token: str, card: dict) -> dict:
        """WB Content API: POST /content/v2/cards/update  body [card]."""
        return await self._content().request(
            "POST", "/content/v2/cards/update", token=token, json=[card]
        )

    def _content(self) -> "BaseMarketplaceClient":
        if not hasattr(self, "_content_client"):
            self._content_client = BaseMarketplaceClient(settings.wb_content_base)
        return self._content_client

    # ── Statistics (read side, Metric Catalog) ────────────────────────────────
    async def get_sales(self, *, token: str, date_from: str, flag: int = 0) -> list[dict]:
        """
        WB Statistics API: GET /api/v1/supplier/sales?dateFrom=&flag=
        Returns a JSON array of sale rows (each row carries nmId + forPay).
        """
        data = await self._statistics().request(
            "GET", "/api/v1/supplier/sales", token=token,
            params={"dateFrom": date_from, "flag": flag},
        )
        return data if isinstance(data, list) else []

    def _statistics(self) -> "BaseMarketplaceClient":
        if not hasattr(self, "_statistics_client"):
            self._statistics_client = BaseMarketplaceClient(settings.wb_statistics_base)
        return self._statistics_client


wb_client = WBClient()
