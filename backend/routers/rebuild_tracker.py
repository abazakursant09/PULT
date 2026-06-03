"""
Rebuild Tracker — /api/rebuild
Tracks every SEO card generation for style learning and winner detection.
Pure heuristics: no ML, no embeddings. Averages + thresholds only.
"""
from __future__ import annotations

import json
import uuid
from collections import defaultdict
from datetime import datetime
from typing import Optional, Literal

from fastapi import APIRouter, Depends, HTTPException
from rate_limit import limit_rebuild
from pydantic import BaseModel
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from database import get_db
from dependencies import get_current_user
from models.user import User
from models.seo_rebuild import SeoRebuild

router = APIRouter()

# ── Style derivation ───────────────────────────────────────────────────────────

_BIGGER_PRODUCT_PRESETS   = {"premium", "luxury", "marketplace", "tech"}
_MINIMAL_TEXT_PRESETS     = {"minimal", "wb-style", "wb"}
_WARM_GRADIENT_PRESETS    = {"beauty", "sale"}
_HIGH_CONTRAST_TYPOGRAPHY = {"aggressive", "bold", "contrast"}

REBUILD_REASONS = {
    "ctr_below_benchmark",
    "low_conversion",
    "high_ad_spend",
    "margin_crisis",
    "low_stock_warning",
    "manual_user_request",
    "auto_from_insight",
    "saved_project_load",
    "retry_slide",
    "seasonal_update",
    "mobile_visibility_problem",
}


def _derive_style(preset: str, typography_preset: Optional[str]) -> dict[str, bool]:
    tp = (typography_preset or "").lower()
    p  = (preset or "").lower()
    return {
        "bigger_product_mode": p in _BIGGER_PRODUCT_PRESETS,
        "minimal_text_mode":   p in _MINIMAL_TEXT_PRESETS,
        "warm_gradient_mode":  p in _WARM_GRADIENT_PRESETS,
        "high_contrast_mode":  any(kw in tp for kw in _HIGH_CONTRAST_TYPOGRAPHY),
    }


def _confidence(rebuild_count: int) -> Literal["low", "medium", "high"]:
    if rebuild_count >= 10: return "high"
    if rebuild_count >= 3:  return "medium"
    return "low"


def _style_label(bigger: bool, minimal: bool, warm: bool, contrast: bool) -> str:
    parts = []
    if bigger:   parts.append("Bigger Product")
    if minimal:  parts.append("Minimal Text")
    if warm:     parts.append("Warm Gradient")
    if contrast: parts.append("High Contrast")
    return " + ".join(parts) if parts else "Default Style"


# ── Schemas ────────────────────────────────────────────────────────────────────

class TrackRebuildRequest(BaseModel):
    product_name:      str
    marketplace:       Optional[str] = "all"
    category:          Optional[str] = "auto"
    preset:            Optional[str] = "premium"
    typography_preset: Optional[str] = None
    rebuild_reason:    Optional[str] = "manual_user_request"
    # Optional structured context from Action Engine
    expected_gain_rub: Optional[float] = None
    insight_key:       Optional[str]   = None
    impact_score:      Optional[int]   = None


class UpdateMetricsRequest(BaseModel):
    ctr_before:        Optional[float] = None
    ctr_after:         Optional[float] = None
    impressions_count: Optional[int]   = None
    revenue_before:    Optional[float] = None
    revenue_after:     Optional[float] = None
    actual_gain_rub:   Optional[float] = None


class RebuildResponse(BaseModel):
    id:                  str
    product_name:        str
    marketplace:         str
    preset:              str
    rebuild_reason:      str
    bigger_product_mode: Optional[bool]
    minimal_text_mode:   Optional[bool]
    warm_gradient_mode:  Optional[bool]
    high_contrast_mode:  Optional[bool]
    delta_ctr_percent:   Optional[float]
    winner:              bool
    confidence_level:    str
    expected_gain_rub:   Optional[float]
    impact_score:        Optional[int]
    created_at:          str

    class Config:
        from_attributes = True


class RecommendationResponse(BaseModel):
    has_data:        bool
    is_demo:         bool
    best_style_name: Optional[str]
    avg_ctr_delta:   Optional[float]
    total_rebuilds:  int
    rebuild_count:   int
    confidence:      Optional[str]
    winners_count:   int
    message:         Optional[str]


# ── Demo data ──────────────────────────────────────────────────────────────────

_DEMO_RECOMMENDATION = RecommendationResponse(
    has_data=True, is_demo=True,
    best_style_name="Bigger Product + High Contrast",
    avg_ctr_delta=11.0,
    total_rebuilds=14, rebuild_count=14,
    confidence="high", winners_count=3,
    message=None,
)

_DEMO_STYLES = [
    {"style": "Bigger Product",                   "avg_delta": 12.0, "count": 5, "winners": 2},
    {"style": "Minimal Text",                     "avg_delta":  4.0, "count": 4, "winners": 0},
    {"style": "Bigger Product + High Contrast",   "avg_delta": 11.0, "count": 3, "winners": 1},
    {"style": "Warm Gradient",                    "avg_delta":  6.5, "count": 2, "winners": 0},
]


# ── Helpers ────────────────────────────────────────────────────────────────────

def _to_response(r: SeoRebuild) -> RebuildResponse:
    return RebuildResponse(
        id=r.id,
        product_name=r.product_name,
        marketplace=r.marketplace,
        preset=r.preset,
        rebuild_reason=r.rebuild_reason,
        bigger_product_mode=r.bigger_product_mode,
        minimal_text_mode=r.minimal_text_mode,
        warm_gradient_mode=r.warm_gradient_mode,
        high_contrast_mode=r.high_contrast_mode,
        delta_ctr_percent=r.delta_ctr_percent,
        winner=r.winner or False,
        confidence_level=r.confidence_level or "low",
        expected_gain_rub=r.expected_gain_rub,
        impact_score=r.impact_score,
        created_at=r.created_at.strftime("%d.%m.%Y %H:%M") if r.created_at else "",
    )


# ── Routes ─────────────────────────────────────────────────────────────────────

@router.post("/rebuild/track", status_code=201)
async def track_rebuild(
    body: TrackRebuildRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    _rl: None = Depends(limit_rebuild),
):
    """Log a new SEO rebuild. Called automatically on generate/save."""
    uid    = str(current_user.id)
    style  = _derive_style(body.preset or "premium", body.typography_preset)
    reason = body.rebuild_reason if body.rebuild_reason in REBUILD_REASONS else "manual_user_request"

    context = None
    if body.expected_gain_rub or body.insight_key:
        context = json.dumps({
            "expected_gain_rub": body.expected_gain_rub,
            "insight_key": body.insight_key,
        }, ensure_ascii=False)

    # Derive confidence from total rebuild count for this user
    count_q = await db.execute(
        select(func.count()).select_from(SeoRebuild)
        .where(SeoRebuild.user_id == uid)
    )
    total = (count_q.scalar() or 0) + 1  # +1 for the one we're about to add
    conf  = _confidence(total)

    rebuild = SeoRebuild(
        id=str(uuid.uuid4()),
        user_id=uid,
        product_name=(body.product_name or "").strip(),
        marketplace=body.marketplace or "all",
        category=body.category or "auto",
        preset=body.preset or "premium",
        typography_preset=body.typography_preset,
        bigger_product_mode=style["bigger_product_mode"],
        minimal_text_mode=style["minimal_text_mode"],
        warm_gradient_mode=style["warm_gradient_mode"],
        high_contrast_mode=style["high_contrast_mode"],
        rebuild_reason=reason,
        rebuild_context_json=context,
        expected_gain_rub=body.expected_gain_rub,
        impact_score=body.impact_score,
        confidence_level=conf,
        winner=False,
        generated_at=datetime.utcnow(),
        created_at=datetime.utcnow(),
    )
    db.add(rebuild)
    await db.commit()
    await db.refresh(rebuild)
    return {"ok": True, "id": rebuild.id, "confidence_level": conf}


@router.patch("/rebuild/{rebuild_id}/metrics")
async def update_metrics(
    rebuild_id: str,
    body: UpdateMetricsRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Update post-measurement CTR and revenue data for a rebuild."""
    q = await db.execute(
        select(SeoRebuild).where(
            SeoRebuild.id == rebuild_id,
            SeoRebuild.user_id == str(current_user.id),
        )
    )
    rebuild = q.scalar_one_or_none()
    if not rebuild:
        raise HTTPException(status_code=404, detail="Rebuild не найден")

    if body.ctr_before is not None:   rebuild.ctr_before = body.ctr_before
    if body.ctr_after  is not None:   rebuild.ctr_after  = body.ctr_after
    if body.impressions_count is not None: rebuild.impressions_count = body.impressions_count
    if body.revenue_before is not None: rebuild.revenue_before = body.revenue_before
    if body.revenue_after  is not None: rebuild.revenue_after  = body.revenue_after
    if body.actual_gain_rub is not None: rebuild.actual_gain_rub = body.actual_gain_rub

    # Compute delta_ctr_percent
    if rebuild.ctr_before and rebuild.ctr_after and rebuild.ctr_before > 0:
        rebuild.delta_ctr_percent = round(
            (rebuild.ctr_after - rebuild.ctr_before) / rebuild.ctr_before * 100, 2
        )
        rebuild.delta_revenue = (
            (rebuild.revenue_after or 0) - (rebuild.revenue_before or 0)
            if rebuild.revenue_after is not None else None
        )

    # Auto winner detection
    if (
        rebuild.delta_ctr_percent is not None
        and rebuild.delta_ctr_percent > 7
        and (rebuild.impressions_count or 0) >= 100
        and rebuild.confidence_level != "low"
    ):
        rebuild.winner = True

    rebuild.measured_at = datetime.utcnow()
    await db.commit()
    return {"ok": True, "winner": rebuild.winner, "delta_ctr_percent": rebuild.delta_ctr_percent}


@router.get("/rebuild/recommendation", response_model=RecommendationResponse)
async def get_recommendation(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """
    Returns best style recommendation based on rebuild history.
    Demo data if user has < 3 rebuilds.
    """
    uid = str(current_user.id)

    q = await db.execute(
        select(SeoRebuild)
        .where(SeoRebuild.user_id == uid)
        .order_by(SeoRebuild.created_at.desc())
    )
    rebuilds = q.scalars().all()

    if len(rebuilds) < 3:
        return _DEMO_RECOMMENDATION

    # Count winners
    winners_count = sum(1 for r in rebuilds if r.winner)

    # Group by style combination, using rebuilds with measured CTR
    measured = [r for r in rebuilds if r.delta_ctr_percent is not None]

    if len(measured) < 3:
        # Not enough measured data: recommend by most-used style combination
        style_counts: dict[tuple, int] = defaultdict(int)
        for r in rebuilds:
            key = (r.bigger_product_mode, r.minimal_text_mode, r.warm_gradient_mode, r.high_contrast_mode)
            style_counts[key] += 1
        best_key = max(style_counts, key=lambda k: style_counts[k])
        label    = _style_label(*best_key)
        conf     = _confidence(len(rebuilds))
        return RecommendationResponse(
            has_data=True, is_demo=False,
            best_style_name=label,
            avg_ctr_delta=None,
            total_rebuilds=len(rebuilds), rebuild_count=style_counts[best_key],
            confidence=conf, winners_count=winners_count,
            message="Нет измеренных данных CTR — показываем наиболее часто используемый стиль",
        )

    # Group by style, compute avg delta_ctr
    style_groups: dict[tuple, list[float]] = defaultdict(list)
    for r in measured:
        key = (r.bigger_product_mode, r.minimal_text_mode, r.warm_gradient_mode, r.high_contrast_mode)
        style_groups[key].append(r.delta_ctr_percent)  # type: ignore[arg-type]

    # Best: highest avg delta, minimum 1 rebuild in group
    best_key  = max(style_groups, key=lambda k: sum(style_groups[k]) / max(len(style_groups[k]), 1))
    group     = style_groups[best_key]
    avg_delta = round(sum(group) / len(group), 1)
    label     = _style_label(*best_key)
    conf      = _confidence(len(group))

    return RecommendationResponse(
        has_data=True, is_demo=False,
        best_style_name=label,
        avg_ctr_delta=avg_delta,
        total_rebuilds=len(rebuilds), rebuild_count=len(group),
        confidence=conf, winners_count=winners_count,
        message=None,
    )


@router.get("/rebuild/history", response_model=list[RebuildResponse])
async def rebuild_history(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Last 30 rebuilds for the current user."""
    q = await db.execute(
        select(SeoRebuild)
        .where(SeoRebuild.user_id == str(current_user.id))
        .order_by(SeoRebuild.created_at.desc())
        .limit(30)
    )
    return [_to_response(r) for r in q.scalars().all()]


@router.get("/rebuild/style-stats")
async def style_stats(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Per-style performance breakdown. Uses demo data if insufficient rebuilds."""
    uid = str(current_user.id)

    q = await db.execute(
        select(SeoRebuild)
        .where(SeoRebuild.user_id == uid)
    )
    rebuilds = q.scalars().all()

    if len(rebuilds) < 3:
        return {"stats": _DEMO_STYLES, "is_demo": True, "total_rebuilds": len(rebuilds)}

    measured = [r for r in rebuilds if r.delta_ctr_percent is not None]
    if not measured:
        return {"stats": [], "is_demo": False, "total_rebuilds": len(rebuilds),
                "message": "Нет измеренных данных — добавьте CTR после сплит-теста"}

    style_groups: dict[str, list[float]] = defaultdict(list)
    winner_counts: dict[str, int]        = defaultdict(int)

    for r in measured:
        label = _style_label(
            r.bigger_product_mode or False,
            r.minimal_text_mode   or False,
            r.warm_gradient_mode  or False,
            r.high_contrast_mode  or False,
        )
        style_groups[label].append(r.delta_ctr_percent)  # type: ignore[arg-type]
        if r.winner: winner_counts[label] += 1

    stats = sorted([
        {
            "style":    label,
            "avg_delta": round(sum(vals) / len(vals), 1),
            "count":    len(vals),
            "winners":  winner_counts[label],
        }
        for label, vals in style_groups.items()
    ], key=lambda x: x["avg_delta"], reverse=True)  # type: ignore[return-value]

    return {"stats": stats, "is_demo": False, "total_rebuilds": len(rebuilds)}


# ── Helper for weekly report ───────────────────────────────────────────────────

async def get_weekly_rebuild_summary(user_id: str, db: AsyncSession) -> dict:
    """Called from scheduler. Returns rebuild stats for the past week."""
    from datetime import timedelta
    week_ago = datetime.utcnow() - timedelta(days=7)

    q = await db.execute(
        select(SeoRebuild)
        .where(
            SeoRebuild.user_id == user_id,
            SeoRebuild.created_at >= week_ago,
        )
    )
    rebuilds = q.scalars().all()

    measured      = [r for r in rebuilds if r.delta_ctr_percent is not None]
    avg_ctr       = round(sum(r.delta_ctr_percent for r in measured) / len(measured), 1) if measured else None  # type: ignore[arg-type]
    winners_count = sum(1 for r in rebuilds if r.winner)
    total_gain    = sum(r.expected_gain_rub for r in rebuilds if r.expected_gain_rub) or 0.0

    # Best style this week
    best_style: Optional[str] = None
    if measured:
        style_groups: dict[str, list[float]] = defaultdict(list)
        for r in measured:
            label = _style_label(
                r.bigger_product_mode or False,
                r.minimal_text_mode   or False,
                r.warm_gradient_mode  or False,
                r.high_contrast_mode  or False,
            )
            style_groups[label].append(r.delta_ctr_percent)  # type: ignore[arg-type]
        if style_groups:
            best_key   = max(style_groups, key=lambda k: sum(style_groups[k]) / len(style_groups[k]))
            if len(style_groups[best_key]) >= 1:
                best_style = best_key

    # All-time total for confidence calculation
    total_q = await db.execute(
        select(func.count()).select_from(SeoRebuild).where(SeoRebuild.user_id == user_id)
    )
    total_rebuilds = total_q.scalar() or 0

    return {
        "rebuild_count":        len(rebuilds),
        "total_rebuilds":       total_rebuilds,
        "avg_ctr_delta":        avg_ctr,
        "total_estimated_gain": round(total_gain, -2) if total_gain else 0,
        "winners_count":        winners_count,
        "best_style":           best_style,
    }


async def get_top_product_this_week(user_id: str, db: AsyncSession) -> dict | None:
    """Returns product with highest delta_ctr_percent in the past week."""
    from datetime import timedelta
    week_ago = datetime.utcnow() - timedelta(days=7)
    q = await db.execute(
        select(SeoRebuild)
        .where(
            SeoRebuild.user_id == user_id,
            SeoRebuild.created_at >= week_ago,
            SeoRebuild.delta_ctr_percent.isnot(None),
        )
        .order_by(SeoRebuild.delta_ctr_percent.desc())
        .limit(1)
    )
    r = q.scalar_one_or_none()
    if not r:
        return None
    return {"name": r.product_name, "delta_ctr_percent": r.delta_ctr_percent}
