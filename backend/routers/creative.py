from __future__ import annotations
from typing import Optional
from fastapi import APIRouter, Depends, Request
from rate_limit import limit_ai
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession
from database import get_db
from dependencies import get_current_user
from models.user import User
from services.creative_scorer import (
    score_creative, get_optimization_variants,
    score_for_both_marketplaces, CreativeScore,
)

router = APIRouter()


# ── Schemas ───────────────────────────────────────────────────────────────────

class ScoreRequest(BaseModel):
    product_name:      str
    category:          str = "auto"
    preset:            str = "premium"
    marketplace:       str = "all"
    advantages:        list[str] = []
    has_product_photo: bool = False


class ScoreComponent(BaseModel):
    label:     str
    score:     int
    max_score: int


class AutoFixOut(BaseModel):
    action: str
    value:  str
    label:  str


class IssueDetailOut(BaseModel):
    issue_type:   str
    severity:     str
    description:  str
    fix_hint:     str
    score_impact: int
    auto_fix:     Optional[AutoFixOut] = None


class ScoreResponse(BaseModel):
    total:                 int
    grade:                 str
    predicted_ctr_uplift:  float
    improvement_potential: int
    best_preset_for_cat:   str
    components:            list[ScoreComponent]
    strengths:             list[str]
    issues:                list[IssueDetailOut]


class VariantItem(BaseModel):
    variant_name: str
    preset:       str
    rank:         int
    score:        ScoreResponse


class OptimizeRequest(BaseModel):
    product_name:      str
    category:          str = "auto"
    marketplace:       str = "all"
    advantages:        list[str] = []
    has_product_photo: bool = False


class OptimizeResponse(BaseModel):
    variants:     list[VariantItem]
    best_variant: str
    best_preset:  str


class MarketplaceCompareResponse(BaseModel):
    wb:          ScoreResponse
    ozon:        ScoreResponse
    delta:       int    # ozon.total - wb.total (positive = Ozon wins)
    better_for:  str    # "wb" | "ozon" | "equal"


# ── Helpers ───────────────────────────────────────────────────────────────────

def _sc_to_response(sc: CreativeScore) -> ScoreResponse:
    return ScoreResponse(
        total=sc.total,
        grade=sc.grade,
        predicted_ctr_uplift=sc.predicted_ctr_uplift,
        improvement_potential=sc.improvement_potential,
        best_preset_for_cat=sc.best_preset_for_cat,
        components=[
            ScoreComponent(label="Товар",     score=sc.product_coverage, max_score=30),
            ScoreComponent(label="Текст",     score=sc.text_density,     max_score=20),
            ScoreComponent(label="Контраст",  score=sc.visual_contrast,  max_score=25),
            ScoreComponent(label="Мобайл",    score=sc.mobile_safety,    max_score=15),
            ScoreComponent(label="Категория", score=sc.category_fit,     max_score=10),
        ],
        strengths=sc.strengths,
        issues=[
            IssueDetailOut(
                issue_type=i.issue_type,
                severity=i.severity,
                description=i.description,
                fix_hint=i.fix_hint,
                score_impact=i.score_impact,
                auto_fix=AutoFixOut(action=i.auto_fix.action, value=i.auto_fix.value, label=i.auto_fix.label)
                    if i.auto_fix else None,
            )
            for i in sc.issues
        ],
    )


# ── Endpoints ─────────────────────────────────────────────────────────────────

@router.post("/creative/score", response_model=ScoreResponse)
async def creative_score(
    body: ScoreRequest,
    request: Request,
    current_user: User = Depends(get_current_user),
    _rl: None = Depends(limit_ai),
):
    sc = score_creative(
        product_name=body.product_name,
        category=body.category,
        preset=body.preset,
        marketplace=body.marketplace,
        advantages=body.advantages,
        has_product_photo=body.has_product_photo,
    )
    return _sc_to_response(sc)


@router.post("/creative/optimize", response_model=OptimizeResponse)
async def creative_optimize(
    body: OptimizeRequest,
    request: Request,
    current_user: User = Depends(get_current_user),
    _rl: None = Depends(limit_ai),
):
    raw = get_optimization_variants(
        product_name=body.product_name,
        category=body.category,
        marketplace=body.marketplace,
        advantages=body.advantages,
        has_product_photo=body.has_product_photo,
    )
    variants = [
        VariantItem(
            variant_name=v["variant_name"],
            preset=v["preset"],
            rank=rank,
            score=_sc_to_response(v["score"]),
        )
        for rank, v in enumerate(raw, 1)
    ]
    best = raw[0]
    return OptimizeResponse(
        variants=variants,
        best_variant=best["variant_name"],
        best_preset=best["preset"],
    )


@router.post("/creative/compare-marketplaces", response_model=MarketplaceCompareResponse)
async def compare_marketplaces(
    body: ScoreRequest,
    current_user: User = Depends(get_current_user),
):
    scores = score_for_both_marketplaces(
        product_name=body.product_name,
        category=body.category,
        preset=body.preset,
        advantages=body.advantages,
        has_product_photo=body.has_product_photo,
    )
    wb_r   = _sc_to_response(scores["wb"])
    ozon_r = _sc_to_response(scores["ozon"])
    delta  = ozon_r.total - wb_r.total
    better = "ozon" if delta > 2 else ("wb" if delta < -2 else "equal")
    return MarketplaceCompareResponse(wb=wb_r, ozon=ozon_r, delta=delta, better_for=better)


@router.get("/creative/benchmarks")
async def creative_benchmarks(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    from services.creative_benchmarks import get_benchmarks
    return await get_benchmarks(current_user.id, db)
