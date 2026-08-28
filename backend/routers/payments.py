import uuid
import logging
from datetime import datetime, timedelta
from decimal import Decimal

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from config import settings
from database import get_db
from models.payment import Payment
from models.user import User
from dependencies import get_current_user

logger = logging.getLogger(__name__)

def _require_billing_enabled() -> None:
    """Hide the complete commercial surface until the operator explicitly releases it."""
    if settings.billing_enabled is not True:
        raise HTTPException(status_code=404, detail="Not found")


router = APIRouter(dependencies=[Depends(_require_billing_enabled)])

TARIFF_CONFIG = {
    "basic": {"amount": Decimal("990.00"),  "plan": "master"},
    "pro":   {"amount": Decimal("4990.00"), "plan": "profi"},
}

YOOKASSA_API = "https://api.yookassa.ru/v3"


def _yk_auth() -> tuple[str, str]:
    return settings.yookassa_shop_id, settings.yookassa_secret_key


# ── Schemas ───────────────────────────────────────────────────────────────────

class CreatePaymentRequest(BaseModel):
    tariff: str  # basic | pro


class CreatePaymentResponse(BaseModel):
    confirmation_url: str
    payment_id: str


class PaymentStatusResponse(BaseModel):
    payment_id: str
    status: str
    tariff: str
    amount: str


class PaymentHistoryItem(BaseModel):
    id: str
    yookassa_payment_id: str
    amount: str
    tariff: str
    plan: str
    status: str
    created_at: str


# ── Endpoints ─────────────────────────────────────────────────────────────────

@router.post("/payments/create", response_model=CreatePaymentResponse)
async def create_payment(
    body: CreatePaymentRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    if body.tariff not in TARIFF_CONFIG:
        raise HTTPException(status_code=400, detail="Неизвестный тариф")

    cfg = TARIFF_CONFIG[body.tariff]
    idempotence_key = str(uuid.uuid4())

    payload = {
        "amount": {"value": str(cfg["amount"]), "currency": "RUB"},
        "confirmation": {
            "type": "redirect",
            "return_url": settings.yookassa_return_url,
        },
        "capture": True,
        "description": f"Бизнес-Пульт · тариф {body.tariff}",
        "metadata": {
            "user_id": current_user.id,
            "tariff": body.tariff,
            "plan": cfg["plan"],
        },
    }

    async with httpx.AsyncClient(timeout=15) as client:
        resp = await client.post(
            f"{YOOKASSA_API}/payments",
            auth=_yk_auth(),
            headers={"Idempotence-Key": idempotence_key},
            json=payload,
        )

    if resp.status_code not in (200, 201):
        logger.error("YooKassa create error %s: %s", resp.status_code, resp.text)
        raise HTTPException(status_code=502, detail="Ошибка платёжного шлюза")

    data = resp.json()
    yk_id = data["id"]
    confirmation_url = data["confirmation"]["confirmation_url"]

    payment = Payment(
        user_id=current_user.id,
        yookassa_payment_id=yk_id,
        amount=cfg["amount"],
        tariff=body.tariff,
        plan=cfg["plan"],
        status="pending",
    )
    db.add(payment)
    await db.commit()

    return CreatePaymentResponse(confirmation_url=confirmation_url, payment_id=yk_id)


@router.get("/payments/status/{payment_id}", response_model=PaymentStatusResponse)
async def payment_status(
    payment_id: str,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(Payment).where(
            Payment.yookassa_payment_id == payment_id,
            Payment.user_id == current_user.id,
        )
    )
    payment = result.scalar_one_or_none()
    if not payment:
        raise HTTPException(status_code=404, detail="Платёж не найден")

    # Refresh from YooKassa
    async with httpx.AsyncClient(timeout=10) as client:
        resp = await client.get(
            f"{YOOKASSA_API}/payments/{payment_id}",
            auth=_yk_auth(),
        )

    if resp.status_code == 200:
        yk_status = resp.json().get("status", payment.status)
        if yk_status != payment.status:
            payment.status = yk_status
            if yk_status == "succeeded":
                await _activate_plan(current_user, payment, db)
            await db.commit()

    return PaymentStatusResponse(
        payment_id=payment.yookassa_payment_id,
        status=payment.status,
        tariff=payment.tariff,
        amount=str(payment.amount),
    )


@router.get("/payments/history", response_model=list[PaymentHistoryItem])
async def payment_history(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(Payment)
        .where(Payment.user_id == current_user.id)
        .order_by(Payment.created_at.desc())
        .limit(20)
    )
    payments = result.scalars().all()
    return [
        PaymentHistoryItem(
            id=p.id,
            yookassa_payment_id=p.yookassa_payment_id,
            amount=str(p.amount),
            tariff=p.tariff,
            plan=p.plan,
            status=p.status,
            created_at=p.created_at.isoformat(),
        )
        for p in payments
    ]


async def _yk_fetch_status(yk_id: str) -> str | None:
    """Authoritatively fetch a payment's status from YooKassa (server-to-server,
    authenticated with the shop credentials). Returns the status string or None if
    it cannot be confirmed. The webhook trusts THIS, never the request body."""
    if not settings.yookassa_shop_id or not settings.yookassa_secret_key:
        return None
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get(f"{YOOKASSA_API}/payments/{yk_id}", auth=_yk_auth())
    except Exception as exc:  # pragma: no cover - network guard
        logger.error("YooKassa webhook verify error for %s: %s", yk_id, exc)
        return None
    if resp.status_code != 200:
        logger.warning("YooKassa webhook verify non-200 for %s: %s", yk_id, resp.status_code)
        return None
    return resp.json().get("status")


@router.post("/payments/webhook")
async def yookassa_webhook(request: Request, db: AsyncSession = Depends(get_db)):
    # P7.1 — do NOT trust the webhook body's event/status (unauthenticated, forgeable).
    # Take only the payment id from the body, then re-verify the real status directly
    # with YooKassa before changing any plan.
    try:
        data = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON")

    obj = data.get("object", {})
    yk_id = obj.get("id")
    if not yk_id:
        return {"ok": True}

    result = await db.execute(
        select(Payment).where(Payment.yookassa_payment_id == yk_id)
    )
    payment = result.scalar_one_or_none()
    if not payment:
        return {"ok": True}

    verified_status = await _yk_fetch_status(yk_id)
    if verified_status is None:
        # Could not confirm with YooKassa → change nothing (fail closed).
        return {"ok": True}

    if verified_status == "succeeded" and payment.status != "succeeded":
        payment.status = "succeeded"
        user_result = await db.execute(select(User).where(User.id == payment.user_id))
        user = user_result.scalar_one_or_none()
        if user:
            await _activate_plan(user, payment, db)
        await db.commit()
    elif verified_status == "canceled" and payment.status == "pending":
        payment.status = "canceled"
        await db.commit()

    return {"ok": True}


async def _activate_plan(user: User, payment: Payment, db: AsyncSession):
    user.plan = payment.plan
    user.subscription_end_date = datetime.utcnow() + timedelta(days=30)
    # LEGAL-1B: the behavioral analytics side-write ("subscription_started") was removed.
    # Subscription state lives in user.plan / subscription_end_date + Payment; billing never
    # depended on that analytics record.
    logger.info("Plan %s activated for user %s until %s", payment.plan, user.id, user.subscription_end_date)
