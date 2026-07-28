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

    async def list_adverts_for_nm(self, *, token: str, nm_id) -> list[dict]:
        """WB Promotion API read (campaign identity, A2.2-pre-a). Returns ONLY the
        adverts observed from WB that actually target ``nm_id`` — never a fabricated
        campaign.

            GET /adv/v1/promotion/adverts  ->  [ {advertId, type, status, params:[{nms:[..]}]}, .. ]

        Each WB advert carries the nmIDs it promotes inside ``params[].nms``. We
        filter to adverts whose nms include nm_id and normalize to the campaign
        identity fields, leaving any field WB does not return as None (no guessing).
        """
        nm = int(nm_id)
        data = await self._advert().request("GET", "/adv/v1/promotion/adverts", token=token)
        adverts = data if isinstance(data, list) else []
        out: list[dict] = []
        for adv in adverts:
            if not isinstance(adv, dict):
                continue
            nms: set[int] = set()
            for p in adv.get("params") or []:
                if isinstance(p, dict):
                    for n in p.get("nms") or []:
                        try:
                            nms.add(int(n))
                        except (TypeError, ValueError):
                            continue
            if nm not in nms:
                continue
            out.append({
                "campaign_id": adv.get("advertId"),
                "campaign_type": adv.get("type"),
                "campaign_state": adv.get("status"),
            })
        return out

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

    async def list_cards(self, *, token: str, cursor: dict | None = None,
                         limit: int = 100) -> dict:
        """Read a page of the seller's product cards (PULT-LAUNCH-1.4.5E ingest).

            POST /content/v2/get/cards/list   (Content category token)

        Cursor pagination: the first request sends only {limit}; each next request copies
        `updatedAt` + `nmID` from the previous response's `cursor` block. A page shorter than the
        limit is the last one. Returns the raw {cards, cursor} object for the provider to normalize
        and to persist the next cursor.
        """
        cur: dict = {"limit": int(limit)}
        if cursor:
            if cursor.get("updatedAt") is not None:
                cur["updatedAt"] = cursor["updatedAt"]
            if cursor.get("nmID") is not None:
                cur["nmID"] = cursor["nmID"]
        body = {"settings": {"cursor": cur, "filter": {"withPhoto": -1}}}
        data = await self._content().request(
            "POST", "/content/v2/get/cards/list", token=token, json=body)
        if not isinstance(data, dict):
            return {"cards": [], "cursor": {}}
        return data

    async def list_prices(self, *, token: str, offset: int = 0, limit: int = 1000) -> list[dict]:
        """Read a page of the seller's prices (PULT-LAUNCH-1.4.5E ingest).

            GET /api/v2/list/goods/filter?limit=&offset=   (Prices & Discounts category token)

        Offset pagination: repeat with offset += limit until an empty list. Each item carries the
        nmID and its price/discount. Returns the list of goods for this page.
        """
        data = await self._prices.request(
            "GET", "/api/v2/list/goods/filter", token=token,
            params={"limit": int(limit), "offset": int(offset)})
        if isinstance(data, dict):
            return ((data.get("data") or {}).get("listGoods")) or []
        return data if isinstance(data, list) else []

    def _content(self) -> "BaseMarketplaceClient":
        if not hasattr(self, "_content_client"):
            self._content_client = BaseMarketplaceClient(settings.wb_content_base)
        return self._content_client

    # ── Календарь акций READ (PULT-LAUNCH-2.5D-WB-B) ───────────────────────────
    # Read-only, evidence-only. On the dp-calendar-api host (Цены и скидки token). These NEVER change
    # a product's promotion state — entering an action is the separate write /promotions/upload path,
    # which is out of scope and never called here.
    def _calendar(self) -> "BaseMarketplaceClient":
        if not hasattr(self, "_calendar_client"):
            self._calendar_client = BaseMarketplaceClient(settings.wb_calendar_base)
        return self._calendar_client

    async def list_promotions(self, *, token: str, start_date_time: str, end_date_time: str,
                              all_promo: bool = False, limit: int = 1000, offset: int = 0) -> list[dict]:
        """GET /api/v1/calendar/promotions → data.promotions[] ({id, name, startDateTime, endDateTime,
        type, ...}). all_promo=false = акции, доступные для участия. limit/offset pagination; `id` is
        the ONLY promotion identity — the title is never identity."""
        data = await self._calendar().request(
            "GET", "/api/v1/calendar/promotions", token=token,
            params={"startDateTime": start_date_time, "endDateTime": end_date_time,
                    "allPromo": "true" if all_promo else "false", "limit": int(limit), "offset": int(offset)})
        proms = (data.get("data") or {}).get("promotions") if isinstance(data, dict) else None
        return proms if isinstance(proms, list) else []

    async def promotion_details(self, *, token: str, promotion_ids: list) -> list[dict]:
        """GET /api/v1/calendar/promotions/details?promotionIDs=..&.. → data.promotions[] with the
        proven start/end window and `type` per promotion."""
        params = [("promotionIDs", int(p)) for p in promotion_ids if str(p).isdigit()]
        data = await self._calendar().request(
            "GET", "/api/v1/calendar/promotions/details", token=token, params=params)
        proms = (data.get("data") or {}).get("promotions") if isinstance(data, dict) else None
        return proms if isinstance(proms, list) else []

    async def promotion_nomenclatures(self, *, token: str, promotion_id, in_action: bool = True,
                                      limit: int = 1000, offset: int = 0) -> list[dict]:
        """GET /api/v1/calendar/promotions/nomenclatures → data.nomenclatures[] ({id(nmID), inAction,
        price, currencyCode, planPrice, discount, planDiscount}). inAction=true ⇒ actually participating;
        false ⇒ candidate. Неприменим для автоакций — never call for a type='auto' promotion."""
        data = await self._calendar().request(
            "GET", "/api/v1/calendar/promotions/nomenclatures", token=token,
            params={"promotionID": int(promotion_id), "inAction": "true" if in_action else "false",
                    "limit": int(limit), "offset": int(offset)})
        noms = (data.get("data") or {}).get("nomenclatures") if isinstance(data, dict) else None
        return noms if isinstance(noms, list) else []

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

    # ── Ingest reads (PULT-LAUNCH-1.4.5E2) ────────────────────────────────────
    async def list_orders(self, *, token: str, date_from: str, flag: int = 0) -> list[dict]:
        """WB Statistics: GET /api/v1/supplier/orders?dateFrom=&flag=. One row per order line;
        `srid` is the stable order id, `lastChangeDate` drives incremental paging."""
        data = await self._statistics().request(
            "GET", "/api/v1/supplier/orders", token=token,
            params={"dateFrom": date_from, "flag": flag})
        return data if isinstance(data, list) else []

    async def list_sales(self, *, token: str, date_from: str, flag: int = 0) -> list[dict]:
        """WB Statistics: GET /api/v1/supplier/sales?dateFrom=&flag=. One row = one sale OR return;
        `saleID` (S… sale / R… return) is the stable per-line id, `srid` links back to the order."""
        return await self.get_sales(token=token, date_from=date_from, flag=flag)

    async def create_warehouse_remains_report(self, *, token: str) -> str:
        """WB Analytics: POST /api/v1/warehouse_remains → task id. The stocks snapshot is an async
        report (create → poll → download); the current supplier/stocks sync method was retired."""
        data = await self._analytics().request(
            "POST", "/api/v1/warehouse_remains", token=token,
            params={"groupByNm": "true"})
        return str(((data or {}).get("data") or {}).get("taskId") or (data or {}).get("taskId") or "")

    async def warehouse_remains_status(self, *, token: str, task_id: str) -> str:
        """GET /api/v1/warehouse_remains/tasks/{id}/status → 'new'|'processing'|'done'|... ."""
        data = await self._analytics().request(
            "GET", f"/api/v1/warehouse_remains/tasks/{task_id}/status", token=token)
        return str(((data or {}).get("data") or {}).get("status") or (data or {}).get("status") or "")

    async def download_warehouse_remains(self, *, token: str, task_id: str) -> list[dict]:
        """GET /api/v1/warehouse_remains/tasks/{id}/download → rows (nmId, warehouseName, quantity)."""
        data = await self._analytics().request(
            "GET", f"/api/v1/warehouse_remains/tasks/{task_id}/download", token=token)
        if isinstance(data, list):
            return data
        return ((data or {}).get("data")) or [] if isinstance(data, dict) else []

    async def finance_sales_report_detailed(self, *, token: str, date_from: str,
                                            date_to: str) -> list[dict]:
        """NEW WB Finance API (replaces the retired v5 reportDetailByPeriod):
            POST /api/finance/v1/sales-reports/detailed  (Finance category token)
        Each row carries rrd_id (stable finance row id), srid, supplier_oper_name, quantity and the
        signed money components."""
        data = await self._finance().request(
            "POST", "/api/finance/v1/sales-reports/detailed", token=token,
            json={"period": {"begin": date_from, "end": date_to}})
        if isinstance(data, list):
            return data
        return ((data or {}).get("data")) or [] if isinstance(data, dict) else []

    def _statistics(self) -> "BaseMarketplaceClient":
        if not hasattr(self, "_statistics_client"):
            self._statistics_client = BaseMarketplaceClient(settings.wb_statistics_base)
        return self._statistics_client

    def _analytics(self) -> "BaseMarketplaceClient":
        if not hasattr(self, "_analytics_client"):
            self._analytics_client = BaseMarketplaceClient(settings.wb_analytics_base)
        return self._analytics_client

    def _finance(self) -> "BaseMarketplaceClient":
        if not hasattr(self, "_finance_client"):
            self._finance_client = BaseMarketplaceClient(settings.wb_finance_base)
        return self._finance_client


wb_client = WBClient()
