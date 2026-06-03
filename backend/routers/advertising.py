"""
Advertising execution (ME-4). PULT had NO ad backend — ad-strategy stopped at
recommendations. These endpoints push real campaign changes through the shared
executor (WB Advert API; Ozon Performance deferred to ME-4b). Detection of bad
ACOS lives in the Action Engine; here we execute the fix.
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


class SetBidRequest(BaseModel):
    marketplace: str = "wildberries"
    campaign_id: int
    cpm: int
    adv_type: int
    old_cpm: int | None = None
    insight_key: str | None = "high_ad_spend"
    dry_run: bool = False


class SetStateRequest(BaseModel):
    marketplace: str = "wildberries"
    campaign_id: int
    action: str               # start | pause
    insight_key: str | None = "high_ad_spend"
    dry_run: bool = False


def _to_resp(res) -> ExecuteResponse:
    return ExecuteResponse(
        log_id=res.log_id, status=res.status, action_type=res.action_type,
        marketplace=res.marketplace, api_request_id=res.api_request_id,
        result=res.result, error=res.error, reversible=res.reversible,
    )


@router.post("/advertising/bid", response_model=ExecuteResponse)
async def set_bid(
    body: SetBidRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """L3 — change a campaign bid (CPM) for real."""
    res = await executor.execute(
        db=db, user_id=current_user.id, action_type="ad_set_bid",
        payload={"marketplace": body.marketplace, "campaign_id": body.campaign_id,
                 "cpm": body.cpm, "adv_type": body.adv_type, "old_cpm": body.old_cpm},
        mode="manual_l3", insight_key=body.insight_key,
        idempotency_key=f"bid:{body.campaign_id}:{body.cpm}", dry_run=body.dry_run,
    )
    if not res.ok and res.status == "failed":
        raise HTTPException(502, detail={"error": res.error, "log_id": res.log_id})
    return _to_resp(res)


@router.post("/advertising/state", response_model=ExecuteResponse)
async def set_state(
    body: SetStateRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """L3 — start or pause a campaign for real."""
    res = await executor.execute(
        db=db, user_id=current_user.id, action_type="ad_set_state",
        payload={"marketplace": body.marketplace, "campaign_id": body.campaign_id,
                 "action": body.action},
        mode="manual_l3", insight_key=body.insight_key,
        idempotency_key=f"state:{body.campaign_id}:{body.action}", dry_run=body.dry_run,
    )
    if not res.ok and res.status == "failed":
        raise HTTPException(502, detail={"error": res.error, "log_id": res.log_id})
    return _to_resp(res)
