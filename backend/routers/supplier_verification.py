from typing import NoReturn

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from database import get_db
from dependencies import get_current_user
from models.user import User
from schemas.supplier_verification import SupplierVerificationCreate, SupplierVerificationOut


def _supplier_verification_unavailable() -> NoReturn:
    """No verified provider: preserve legacy records without exposing assertions."""
    raise HTTPException(status_code=404, detail="Not found")


router = APIRouter(dependencies=[Depends(_supplier_verification_unavailable)])


@router.post("/suppliers/verify", response_model=SupplierVerificationOut, status_code=201)
async def submit_verification(
    data: SupplierVerificationCreate,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    _supplier_verification_unavailable()


@router.get("/suppliers/verify/my", response_model=list[SupplierVerificationOut])
async def my_verifications(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    _supplier_verification_unavailable()


@router.get("/suppliers/verify/{verification_id}", response_model=SupplierVerificationOut)
async def get_verification(
    verification_id: str,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    _supplier_verification_unavailable()


@router.delete("/suppliers/verify/{verification_id}")
async def revoke_verification(
    verification_id: str,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    _supplier_verification_unavailable()


@router.get("/suppliers/verified", response_model=list[SupplierVerificationOut])
async def list_verified(
    db: AsyncSession = Depends(get_db),
):
    _supplier_verification_unavailable()
