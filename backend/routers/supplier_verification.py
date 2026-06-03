from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from database import get_db
from dependencies import get_current_user
from models.user import User
from models.supplier_verification import SupplierVerification
from schemas.supplier_verification import SupplierVerificationCreate, SupplierVerificationOut
from services.verification import run_verification

router = APIRouter()


@router.post("/suppliers/verify", response_model=SupplierVerificationOut, status_code=201)
async def submit_verification(
    data: SupplierVerificationCreate,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    # One active (pending/verified) application per user is enough
    existing_q = await db.execute(
        select(SupplierVerification)
        .where(SupplierVerification.user_id == str(current_user.id))
        .where(SupplierVerification.status.in_(["pending", "verified"]))
        .order_by(SupplierVerification.created_at.desc())
    )
    existing = existing_q.scalars().first()
    if existing:
        raise HTTPException(
            status_code=409,
            detail="У вас уже есть активная или верифицированная заявка. "
                   "Для повторной подачи отзовите текущую.",
        )

    # Run stub verification
    result = run_verification(
        country        = data.country,
        inn            = data.inn,
        legal_address  = data.legal_address,
        uscc           = data.uscc,
        business_scope = data.business_scope,
        founded_year   = data.founded_year,
    )

    sv = SupplierVerification(
        user_id             = str(current_user.id),
        company_name        = data.company_name,
        country             = data.country,
        inn                 = data.inn,
        ogrn                = data.ogrn,
        legal_address       = data.legal_address,
        phone               = data.phone,
        website             = data.website,
        uscc                = data.uscc,
        business_scope      = data.business_scope,
        founded_year        = data.founded_year,
        status              = "verified" if result.ok else "rejected",
        verification_source = result.source,
        rejection_reason    = result.reason if not result.ok else None,
        verified_at         = datetime.utcnow() if result.ok else None,
    )
    db.add(sv)
    await db.commit()
    await db.refresh(sv)
    return sv


@router.get("/suppliers/verify/my", response_model=list[SupplierVerificationOut])
async def my_verifications(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Return all verification applications submitted by the current user."""
    q = await db.execute(
        select(SupplierVerification)
        .where(SupplierVerification.user_id == str(current_user.id))
        .order_by(SupplierVerification.created_at.desc())
    )
    return q.scalars().all()


@router.get("/suppliers/verify/{verification_id}", response_model=SupplierVerificationOut)
async def get_verification(
    verification_id: str,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    q = await db.execute(
        select(SupplierVerification)
        .where(SupplierVerification.id == verification_id)
        .where(SupplierVerification.user_id == str(current_user.id))
    )
    sv = q.scalar_one_or_none()
    if not sv:
        raise HTTPException(status_code=404, detail="Заявка не найдена")
    return sv


@router.delete("/suppliers/verify/{verification_id}")
async def revoke_verification(
    verification_id: str,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Revoke a pending or rejected application so a new one can be submitted."""
    q = await db.execute(
        select(SupplierVerification)
        .where(SupplierVerification.id == verification_id)
        .where(SupplierVerification.user_id == str(current_user.id))
    )
    sv = q.scalar_one_or_none()
    if not sv:
        raise HTTPException(status_code=404, detail="Заявка не найдена")
    if sv.status == "verified":
        raise HTTPException(status_code=409, detail="Нельзя отозвать верифицированную заявку")
    await db.delete(sv)
    await db.commit()
    return {"ok": True}


@router.get("/suppliers/verified", response_model=list[SupplierVerificationOut])
async def list_verified(
    db: AsyncSession = Depends(get_db),
):
    """Public list of verified suppliers."""
    q = await db.execute(
        select(SupplierVerification)
        .where(SupplierVerification.status == "verified")
        .order_by(SupplierVerification.verified_at.desc())
    )
    return q.scalars().all()
