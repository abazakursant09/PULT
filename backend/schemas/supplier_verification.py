from __future__ import annotations
from datetime import datetime
from typing import Optional
from pydantic import BaseModel, field_validator


class SupplierVerificationCreate(BaseModel):
    company_name:  str
    country:       str = "russia"   # russia | china

    # Russia
    inn:           Optional[str] = None
    ogrn:          Optional[str] = None
    legal_address: Optional[str] = None
    phone:         Optional[str] = None
    website:       Optional[str] = None

    # China
    uscc:           Optional[str] = None
    business_scope: Optional[str] = None
    founded_year:   Optional[int] = None

    @field_validator("company_name")
    @classmethod
    def name_not_empty(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("Название компании не может быть пустым")
        return v.strip()

    @field_validator("country")
    @classmethod
    def country_valid(cls, v: str) -> str:
        if v not in ("russia", "china"):
            raise ValueError("country должен быть 'russia' или 'china'")
        return v

    @field_validator("inn", mode="before")
    @classmethod
    def inn_format(cls, v: Optional[str]) -> Optional[str]:
        if v is None:
            return v
        v = str(v).strip()
        if v and not (v.isdigit() and len(v) in (10, 12)):
            raise ValueError("ИНН должен содержать 10 или 12 цифр")
        return v or None

    @field_validator("uscc", mode="before")
    @classmethod
    def uscc_format(cls, v: Optional[str]) -> Optional[str]:
        if v is None:
            return v
        v = str(v).strip().upper()
        if v and len(v) != 18:
            raise ValueError("USCC должен содержать ровно 18 символов")
        return v or None


class SupplierVerificationOut(BaseModel):
    id:                  str
    user_id:             str
    company_name:        str
    country:             str
    inn:                 Optional[str]
    ogrn:                Optional[str]
    legal_address:       Optional[str]
    phone:               Optional[str]
    website:             Optional[str]
    uscc:                Optional[str]
    business_scope:      Optional[str]
    founded_year:        Optional[int]
    status:              str
    verification_source: Optional[str]
    rejection_reason:    Optional[str]
    verified_at:         Optional[datetime]
    created_at:          datetime

    model_config = {"from_attributes": True}
