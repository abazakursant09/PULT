from typing import Optional
from datetime import datetime
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession
from pydantic import BaseModel
from database import get_db
from models.promo_code import PromoCode, PromoCodeActivation
from models.user import User
from dependencies import get_current_user

router = APIRouter()

PLAN_PRICES = {
    "master":  990.0,
    "profi":  1990.0,
    "maximum": 3990.0,
}


class ValidateRequest(BaseModel):
    code: str
    plan: str


class ValidateResponse(BaseModel):
    valid:           bool
    promo_id:        Optional[str] = None
    code:            Optional[str] = None
    type:            Optional[str] = None
    value:           Optional[float] = None
    description:     Optional[str] = None
    original_price:  Optional[float] = None
    final_price:     Optional[float] = None
    discount_amount: Optional[float] = None
    trial_days:      Optional[int] = None
    error:           Optional[str] = None


class ApplyRequest(BaseModel):
    code: str
    plan: str


class PromoOut(BaseModel):
    id:                  str
    code:                str
    type:                str
    value:               float
    description:         Optional[str]
    applicable_plans:    str
    max_activations:     Optional[int]
    current_activations: int
    is_active:           bool
    blogger_name:        Optional[str]
    expires_at:          Optional[datetime]
    created_at:          datetime

    class Config:
        from_attributes = True


class PromoCreate(BaseModel):
    code:             str
    type:             str
    value:            float
    description:      Optional[str] = None
    applicable_plans: str = "all"
    max_activations:  Optional[int] = None
    blogger_name:     Optional[str] = None
    expires_at:       Optional[datetime] = None


class BloggerStat(BaseModel):
    blogger_name: str
    total_codes:  int
    total_activations: int
    codes: list[str]


def _calc(promo: PromoCode, plan: str) -> tuple[float, float, float]:
    """Returns (original, final, discount)."""
    original = PLAN_PRICES.get(plan, 0.0)
    if promo.type == "percent":
        discount = round(original * promo.value / 100, 2)
        final    = max(original - discount, 0.0)
    elif promo.type == "fixed":
        discount = min(promo.value, original)
        final    = max(original - promo.value, 0.0)
    else:
        discount = 0.0
        final    = original
    return original, final, discount


def _check_promo(promo: PromoCode, plan: str) -> Optional[str]:
    if not promo.is_active:
        return "Промокод неактивен"
    if promo.expires_at and promo.expires_at < datetime.utcnow():
        return "Промокод истёк"
    if promo.max_activations is not None and promo.current_activations >= promo.max_activations:
        return "Лимит активаций исчерпан"
    if promo.applicable_plans != "all":
        allowed = [p.strip() for p in promo.applicable_plans.split(",")]
        if plan not in allowed:
            return f"Промокод действует только для тарифов: {', '.join(allowed)}"
    return None


@router.post("/promo/validate", response_model=ValidateResponse)
async def validate_promo(
    body: ValidateRequest,
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(PromoCode).where(PromoCode.code == body.code.upper().strip())
    )
    promo = result.scalars().first()
    if not promo:
        return ValidateResponse(valid=False, error="Промокод не найден")

    err = _check_promo(promo, body.plan)
    if err:
        return ValidateResponse(valid=False, error=err)

    original, final, discount = _calc(promo, body.plan)

    return ValidateResponse(
        valid=True,
        promo_id=promo.id,
        code=promo.code,
        type=promo.type,
        value=promo.value,
        description=promo.description,
        original_price=original,
        final_price=final,
        discount_amount=discount,
        trial_days=int(promo.value) if promo.type in ("extended_trial", "blogger_free") else None,
    )


@router.post("/promo/apply")
async def apply_promo(
    body: ApplyRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    result = await db.execute(
        select(PromoCode).where(PromoCode.code == body.code.upper().strip())
    )
    promo = result.scalars().first()
    if not promo:
        raise HTTPException(404, "Промокод не найден")

    err = _check_promo(promo, body.plan)
    if err:
        raise HTTPException(400, err)

    # Check duplicate
    dup = await db.execute(
        select(PromoCodeActivation).where(
            PromoCodeActivation.promo_id == promo.id,
            PromoCodeActivation.user_id  == current_user.id,
        )
    )
    if dup.scalars().first():
        raise HTTPException(400, "Вы уже использовали этот промокод")

    # Record activation
    activation = PromoCodeActivation(
        promo_id=promo.id,
        user_id=current_user.id,
        plan=body.plan,
    )
    db.add(activation)
    promo.current_activations += 1
    await db.commit()

    original, final, discount = _calc(promo, body.plan)
    return {
        "ok": True,
        "type": promo.type,
        "value": promo.value,
        "description": promo.description,
        "original_price": original,
        "final_price": final,
        "discount_amount": discount,
        "trial_days": int(promo.value) if promo.type in ("extended_trial", "blogger_free") else None,
    }


# ── Admin endpoints ─────────────────────────────────────────────────────────────

@router.get("/admin/promo", response_model=list[PromoOut])
async def admin_list_promos(
    db: AsyncSession = Depends(get_db),
    _: User = Depends(get_current_user),   # any auth required; real apps check admin role
):
    result = await db.execute(select(PromoCode).order_by(PromoCode.created_at.desc()))
    return result.scalars().all()


@router.post("/admin/promo", response_model=PromoOut)
async def admin_create_promo(
    body: PromoCreate,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(get_current_user),
):
    existing = await db.execute(
        select(PromoCode).where(PromoCode.code == body.code.upper().strip())
    )
    if existing.scalars().first():
        raise HTTPException(400, "Промокод с таким кодом уже существует")

    promo = PromoCode(
        code=body.code.upper().strip(),
        type=body.type,
        value=body.value,
        description=body.description,
        applicable_plans=body.applicable_plans,
        max_activations=body.max_activations,
        blogger_name=body.blogger_name,
        expires_at=body.expires_at,
    )
    db.add(promo)
    await db.commit()
    await db.refresh(promo)
    return promo


@router.patch("/admin/promo/{promo_id}/toggle", response_model=PromoOut)
async def admin_toggle_promo(
    promo_id: str,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(get_current_user),
):
    promo = await db.get(PromoCode, promo_id)
    if not promo:
        raise HTTPException(404, "Не найден")
    promo.is_active = not promo.is_active
    await db.commit()
    await db.refresh(promo)
    return promo


@router.get("/admin/promo/stats")
async def admin_promo_stats(
    db: AsyncSession = Depends(get_db),
    _: User = Depends(get_current_user),
):
    promos_result = await db.execute(select(PromoCode).order_by(PromoCode.created_at.desc()))
    promos = promos_result.scalars().all()

    total_activations = sum(p.current_activations for p in promos)
    active_count      = sum(1 for p in promos if p.is_active)

    # Blogger breakdown
    bloggers: dict[str, dict] = {}
    for p in promos:
        if not p.blogger_name:
            continue
        if p.blogger_name not in bloggers:
            bloggers[p.blogger_name] = {"total_codes": 0, "total_activations": 0, "codes": []}
        bloggers[p.blogger_name]["total_codes"]      += 1
        bloggers[p.blogger_name]["total_activations"] += p.current_activations
        bloggers[p.blogger_name]["codes"].append(p.code)

    return {
        "total_promos":      len(promos),
        "active_promos":     active_count,
        "total_activations": total_activations,
        "bloggers": [
            {"blogger_name": name, **data}
            for name, data in sorted(bloggers.items(), key=lambda x: -x[1]["total_activations"])
        ],
    }


@router.get("/promo/stats", response_model=dict)
async def user_promo_stats(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    result = await db.execute(
        select(PromoCodeActivation).where(PromoCodeActivation.user_id == current_user.id)
    )
    activations = result.scalars().all()
    return {"activations": len(activations), "used_codes": [a.promo_id for a in activations]}
