"""
Ozon client interface. Declared now so the action catalog can route Ozon
actions, but Ozon Reviews requires the premium API and Performance (ads) uses a
separate OAuth (Client-Id/Client-Secret) — deferred to later slices. Methods
raise until implemented so there is never a silent local no-op.
"""
from __future__ import annotations

from .errors import ExecutionError

MARKETPLACE = "ozon"


class OzonClient:
    async def publish_feedback_answer(self, **_):  # premium Reviews API
        raise ExecutionError(
            ExecutionError.UNKNOWN_ACTION,
            "ozon.publish_feedback_answer not implemented (premium Reviews API, later slice)",
        )

    async def set_price(self, **_):  # ME-3
        raise ExecutionError(ExecutionError.UNKNOWN_ACTION, "ozon.set_price not implemented (ME-3)")


ozon_client = OzonClient()
