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

    # ── Declared for next slices (not yet implemented) ────────────────────────
    async def set_price(self, **_):  # ME-3
        raise ExecutionError(ExecutionError.UNKNOWN_ACTION, "wb.set_price not implemented (ME-3)")

    async def set_bid(self, **_):    # ME-4
        raise ExecutionError(ExecutionError.UNKNOWN_ACTION, "wb.set_bid not implemented (ME-4)")

    async def leave_promotion(self, **_):  # ME-5
        raise ExecutionError(ExecutionError.UNKNOWN_ACTION, "wb.leave_promotion not implemented (ME-5)")


wb_client = WBClient()
