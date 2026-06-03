"""
SEO execution (ME-5). SEO tooling produced recommendations but never applied
them to the card. This endpoint pushes a real card update (title / description /
characteristics) to the marketplace through the shared executor — closing
recommendation -> Apply -> Updated Card.
"""
import logging

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from database import get_db
from dependencies import get_current_user
from models.user import User
from schemas.marketplace import ExecuteResponse
from services.marketplace import executor

log = logging.getLogger(__name__)
router = APIRouter()


class ApplyCardRequest(BaseModel):
    marketplace: str = "wildberries"
    offer_id: str                       # WB nmID / Ozon offer_id
    card: dict                          # new card fields (title, description, characteristics, ...)
    old_card: dict | None = None        # snapshot to enable revert
    insight_key: str | None = "seo_opportunity"
    dry_run: bool = False


@router.post("/seo/apply-card", response_model=ExecuteResponse)
async def apply_card(
    body: ApplyCardRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """L3 — apply SEO changes to the real marketplace card."""
    res = await executor.execute(
        db=db, user_id=current_user.id, action_type="update_card",
        payload={"marketplace": body.marketplace, "offer_id": body.offer_id,
                 "card": body.card, "old_card": body.old_card},
        mode="manual_l3", insight_key=body.insight_key,
        idempotency_key=f"card:{body.offer_id}:{hash(str(sorted(body.card.items())))}",
        dry_run=body.dry_run,
    )
    if not res.ok and res.status == "failed":
        raise HTTPException(502, detail={"error": res.error, "log_id": res.log_id})
    return ExecuteResponse(
        log_id=res.log_id, status=res.status, action_type=res.action_type,
        marketplace=res.marketplace, api_request_id=res.api_request_id,
        result=res.result, error=res.error, reversible=res.reversible,
    )
