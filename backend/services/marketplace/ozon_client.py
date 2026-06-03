"""
Ozon client. Ozon Seller API uses headers Api-Key + Client-Id. Prices/Content
are on api-seller.ozon.ru; Reviews and Advertising (Performance API) use a
separate base + OAuth (Client-Id/Client-Secret) — deferred. Unimplemented
methods raise (never a silent local no-op).
"""
from __future__ import annotations

from config import settings
from .base_client import BaseMarketplaceClient
from .errors import ExecutionError

MARKETPLACE = "ozon"


class OzonClient:
    def __init__(self):
        self._seller = BaseMarketplaceClient(settings.ozon_seller_base)

    def _headers(self, client_id: str | None) -> dict:
        if not client_id:
            raise ExecutionError(ExecutionError.AUTH, "Ozon requires ozon_client_id on the connection")
        return {"Client-Id": str(client_id)}

    # ── Prices (ME-3) ─────────────────────────────────────────────────────────
    async def set_price(self, *, token: str, client_id: str | None,
                        offer_id: str, price: float) -> dict:
        """
        Ozon: POST /v1/product/import/prices. Api-Key passed via Authorization
        (base_client sets it); Client-Id is an extra header. Price is a string.
        """
        return await self._seller.request(
            "POST", "/v1/product/import/prices", token=token,
            auth_header="Api-Key", extra_headers=self._headers(client_id),
            json={"prices": [{"offer_id": str(offer_id), "price": str(int(round(price)))}]},
        )

    # ── deferred ──────────────────────────────────────────────────────────────
    async def publish_feedback_answer(self, **_):  # premium Reviews API
        raise ExecutionError(
            ExecutionError.UNKNOWN_ACTION,
            "ozon.publish_feedback_answer not implemented (premium Reviews API)",
        )

    async def set_bid(self, **_):  # Performance API, separate OAuth (ME-4b)
        raise ExecutionError(
            ExecutionError.UNKNOWN_ACTION,
            "ozon.set_bid not implemented (Performance API needs separate OAuth — ME-4b)",
        )

    async def update_card(self, **_):  # ME-5
        raise ExecutionError(ExecutionError.UNKNOWN_ACTION, "ozon.update_card not implemented (ME-5)")


ozon_client = OzonClient()
