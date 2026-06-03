"""Marketplace cabinet connections (ME-1). Stores encrypted API tokens."""
import logging
import uuid
from datetime import datetime
from typing import List

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from database import get_db
from dependencies import get_current_user
from models.user import User
from models.marketplace_connection import MarketplaceConnection
from models.api_credential import ApiCredential
from schemas.marketplace import ConnectionCreate, ConnectionOut
from services.marketplace import credential_vault

log = logging.getLogger(__name__)
router = APIRouter()

_VALID_MP = {"wildberries", "ozon"}
_VALID_SCOPES = {"feedbacks", "prices", "advert", "content", "stocks", "promotions"}


@router.get("/connections", response_model=List[ConnectionOut])
async def list_connections(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    rows = (
        await db.execute(
            select(MarketplaceConnection)
            .where(MarketplaceConnection.user_id == current_user.id)
            .order_by(MarketplaceConnection.created_at.desc())
        )
    ).scalars().all()
    return rows


@router.post("/connections", response_model=ConnectionOut, status_code=201)
async def create_connection(
    body: ConnectionCreate,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    if body.marketplace not in _VALID_MP:
        raise HTTPException(422, f"unknown marketplace: {body.marketplace}")
    if body.scope not in _VALID_SCOPES:
        raise HTTPException(422, f"unknown scope: {body.scope}")
    if not body.token.strip():
        raise HTTPException(422, "token is required")

    # reuse an existing cabinet for this marketplace, or create one
    conn = (
        await db.execute(
            select(MarketplaceConnection).where(
                MarketplaceConnection.user_id == current_user.id,
                MarketplaceConnection.marketplace == body.marketplace,
            )
        )
    ).scalars().first()

    if conn is None:
        conn = MarketplaceConnection(
            id=str(uuid.uuid4()),
            user_id=current_user.id,
            marketplace=body.marketplace,
            label=body.label,
            status="connected",
            scopes=[body.scope],
            ozon_client_id=body.ozon_client_id,
        )
        db.add(conn)
    else:
        if body.scope not in (conn.scopes or []):
            conn.scopes = [*(conn.scopes or []), body.scope]
        if body.label:
            conn.label = body.label
        if body.ozon_client_id:
            conn.ozon_client_id = body.ozon_client_id
        conn.updated_at = datetime.utcnow()

    await db.flush()

    # store/replace the encrypted token for this scope
    existing = (
        await db.execute(
            select(ApiCredential).where(
                ApiCredential.connection_id == conn.id,
                ApiCredential.scope == body.scope,
            )
        )
    ).scalars().first()
    enc = credential_vault.encrypt(body.token.strip())
    if existing:
        existing.secret_enc = enc
        existing.updated_at = datetime.utcnow()
    else:
        db.add(ApiCredential(id=str(uuid.uuid4()), connection_id=conn.id,
                             scope=body.scope, secret_enc=enc))

    await db.commit()
    await db.refresh(conn)
    log.info("connection saved: user=%s mp=%s scope=%s", current_user.id,
             body.marketplace, body.scope)  # token never logged
    return conn


@router.delete("/connections/{connection_id}", status_code=204)
async def delete_connection(
    connection_id: str,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    conn = (
        await db.execute(
            select(MarketplaceConnection).where(
                MarketplaceConnection.id == connection_id,
                MarketplaceConnection.user_id == current_user.id,
            )
        )
    ).scalars().first()
    if conn is None:
        raise HTTPException(404, "connection not found")
    conn.status = "revoked"
    await db.commit()
