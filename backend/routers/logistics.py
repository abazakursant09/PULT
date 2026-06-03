from typing import Optional
from fastapi import APIRouter, Depends, Query
from sqlalchemy import select, or_
from sqlalchemy.ext.asyncio import AsyncSession
from pydantic import BaseModel, Field
from database import get_db
from models.transport_company import TransportCompany

router = APIRouter()


class TransportCompanyOut(BaseModel):
    id: str
    name: str
    region: Optional[str]
    delivery_types: Optional[str]
    description: Optional[str]
    phone: Optional[str]
    rating: float
    total_reviews: int
    price_per_kg: Optional[float]
    price_per_m3: Optional[float]
    min_transit_days: Optional[int]
    max_transit_days: Optional[int]

    class Config:
        from_attributes = True


class CompareRequest(BaseModel):
    weight_kg: float = Field(..., gt=0)
    volume_m3: float = Field(..., gt=0)
    from_city: str
    to_city: str
    delivery_type: Optional[str] = None   # auto | rail | air | express | cargo


class CompareResult(BaseModel):
    company_id: str
    company_name: str
    delivery_types: Optional[str]
    min_transit_days: Optional[int]
    max_transit_days: Optional[int]
    estimated_cost: float
    rating: float


_CITY_DISTANCES: dict[tuple[str, str], float] = {
    ("москва", "санкт-петербург"): 700,
    ("санкт-петербург", "москва"): 700,
    ("москва", "екатеринбург"):    1800,
    ("москва", "новосибирск"):     2900,
    ("москва", "казань"):          800,
    ("москва", "воронеж"):         520,
    ("москва", "челябинск"):       1900,
    ("москва", "барнаул"):         3500,
    ("екатеринбург", "новосибирск"): 1400,
}

_DEFAULT_DISTANCE = 2000.0  # km when pair not found


def _distance(a: str, b: str) -> float:
    key = (a.lower().strip(), b.lower().strip())
    return _CITY_DISTANCES.get(key, _CITY_DISTANCES.get((key[1], key[0]), _DEFAULT_DISTANCE))


@router.get("/logistics/companies", response_model=list[TransportCompanyOut])
async def list_companies(
    region:        Optional[str] = Query(None),
    delivery_type: Optional[str] = Query(None),
    db: AsyncSession = Depends(get_db),
):
    q = select(TransportCompany).order_by(TransportCompany.rating.desc())
    if region:
        q = q.where(TransportCompany.region.ilike(f"%{region}%"))
    if delivery_type:
        q = q.where(TransportCompany.delivery_types.ilike(f"%{delivery_type}%"))
    result = await db.execute(q)
    return result.scalars().all()


@router.post("/logistics/compare", response_model=list[CompareResult])
async def compare_delivery(body: CompareRequest, db: AsyncSession = Depends(get_db)):
    q = select(TransportCompany)
    if body.delivery_type:
        q = q.where(TransportCompany.delivery_types.ilike(f"%{body.delivery_type}%"))
    result = await db.execute(q)
    companies = result.scalars().all()

    dist = _distance(body.from_city, body.to_city)
    dist_factor = max(dist / 1000.0, 1.0)

    out: list[CompareResult] = []
    for tc in companies:
        if tc.price_per_kg is None:
            continue
        cost = (
            tc.price_per_kg * body.weight_kg +
            (tc.price_per_m3 or 0) * body.volume_m3
        ) * dist_factor
        out.append(CompareResult(
            company_id=tc.id,
            company_name=tc.name,
            delivery_types=tc.delivery_types,
            min_transit_days=tc.min_transit_days,
            max_transit_days=tc.max_transit_days,
            estimated_cost=round(cost, 2),
            rating=tc.rating,
        ))

    out.sort(key=lambda x: x.estimated_cost)
    return out
