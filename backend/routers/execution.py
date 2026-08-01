"""Generic L3 execution endpoint + audit log (ME-1). Thin wrapper over executor."""
import logging
from typing import List

from fastapi import APIRouter, Depends, Header
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from database import get_db
from dependencies import get_current_user
from models.user import User
from models.execution_log import ExecutionLog
from fastapi import HTTPException
from schemas.marketplace import (
    ExecuteRequest, ExecuteResponse, ExecutionLogOut, ExecutionLogDetailOut,
)
from services.marketplace import executor
from ._op_http import resolve_client_key, raise_if_reconcile

log = logging.getLogger(__name__)
router = APIRouter()


@router.post("/execute", response_model=ExecuteResponse)
async def execute_action(
    body: ExecuteRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    idempotency_key: str | None = Header(None, alias="Idempotency-Key"),
):
    op_key = resolve_client_key(idempotency_key, dry_run=body.dry_run,
                                body_key=body.idempotency_key)
    res = await executor.execute(
        db=db,
        user_id=current_user.id,
        action_type=body.action_type,
        payload=body.payload,
        mode="manual_l3",
        connection_id=body.connection_id,
        insight_key=body.insight_key,
        idempotency_key=op_key,
        dry_run=body.dry_run,
    )
    raise_if_reconcile(res)
    return ExecuteResponse(
        log_id=res.log_id, status=res.status, action_type=res.action_type,
        marketplace=res.marketplace, api_request_id=res.api_request_id,
        result=res.result, error=res.error, reversible=res.reversible,
    )


@router.get("/executions", response_model=List[ExecutionLogOut])
@router.get("/execution-log", response_model=List[ExecutionLogOut])
async def execution_log(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    limit: int = 50,
):
    """Execution history — 'what PULT did for me', newest first."""
    rows = (
        await db.execute(
            select(ExecutionLog)
            .where(ExecutionLog.user_id == current_user.id)
            .order_by(ExecutionLog.created_at.desc())
            .limit(min(limit, 200))
        )
    ).scalars().all()
    return rows


@router.get("/executions/{execution_id}", response_model=ExecutionLogDetailOut)
async def execution_detail(
    execution_id: str,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Full record for the details drawer (payload, result, guard outcome)."""
    rec = (
        await db.execute(
            select(ExecutionLog).where(
                ExecutionLog.id == execution_id,
                ExecutionLog.user_id == current_user.id,
            )
        )
    ).scalars().first()
    if rec is None:
        raise HTTPException(404, "execution not found")
    return rec


@router.post("/execute/{log_id}/revert", response_model=ExecuteResponse)
async def revert_action(
    log_id: str,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    res = await executor.revert(db=db, user_id=current_user.id, log_id=log_id)
    return ExecuteResponse(
        log_id=res.log_id, status=res.status, action_type=res.action_type,
        marketplace=res.marketplace, api_request_id=res.api_request_id,
        result=res.result, error=res.error, reversible=res.reversible,
    )
