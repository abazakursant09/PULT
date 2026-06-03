"""
SEO Intelligence — /api/seo-intelligence
Style leaderboard and per-style analytics derived from SeoRebuild history.
All aggregations are done in Python after a single indexed query — no N+1 queries.
Minimum sample_size = 5 for statistical validity.
"""
from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timedelta
from typing import Optional

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from database import get_db
from dependencies import get_current_user
from models.user import User
from models.seo_rebuild import SeoRebuild

router = APIRouter()

_MIN_SAMPLE = 5   # minimum rebuilds for statistical validity


# ── Shared helpers ─────────────────────────────────────────────────────────────

def _style_label(bigger: bool, minimal: bool, warm: bool, contrast: bool) -> str:
    parts = []
    if bigger:   parts.append("Bigger Product")
    if minimal:  parts.append("Minimal Text")
    if warm:     parts.append("Warm Gradient")
    if contrast: parts.append("High Contrast")
    return " + ".join(parts) if parts else "Default Style"


def _flags_from_name(name: str) -> tuple[bool, bool, bool, bool]:
    n = name.lower()
    return (
        "bigger product"  in n,
        "minimal text"    in n or "minimal clean" in n,
        "warm gradient"   in n,
        "high contrast"   in n,
    )


def _category_label(raw: str) -> str:
    return {
        "tools":       "Tools",
        "auto":        "Auto",
        "cosmetics":   "Cosmetics",
        "electronics": "Electronics",
        "home":        "Home",
        "auto":        "Auto",
    }.get((raw or "").lower(), raw.capitalize() if raw else "Other")


def _mp_label(mp: str) -> str:
    return {
        "wildberries":   "Wildberries",
        "ozon":          "Ozon",
        "yandex_market": "Яндекс Маркет",
        "all":           "Все",
    }.get(mp, mp)


def _compute_style_stats(rebuilds: list[SeoRebuild]) -> list[dict]:
    """
    Group rebuilds by style combination.
    Returns list of style stats dicts, sorted by avg_ctr_uplift desc.
    Only includes styles with >= _MIN_SAMPLE measured rebuilds.
    """
    groups: dict[tuple, list[SeoRebuild]] = defaultdict(list)
    for r in rebuilds:
        key = (r.bigger_product_mode, r.minimal_text_mode, r.warm_gradient_mode, r.high_contrast_mode)
        groups[key].append(r)

    results = []
    for key, rows in groups.items():
        measured = [r for r in rows if r.delta_ctr_percent is not None]
        if len(measured) < _MIN_SAMPLE:
            continue

        wins      = sum(1 for r in rows if r.winner)
        avg_delta = round(sum(r.delta_ctr_percent for r in measured) / len(measured), 1)  # type: ignore[arg-type]
        win_rate  = round(wins / len(measured) * 100)

        # Best categories (by avg delta, min 2 samples)
        cat_groups: dict[str, list[float]] = defaultdict(list)
        for r in measured:
            cat_groups[r.category or "other"].append(r.delta_ctr_percent)  # type: ignore[arg-type]
        best_cats = sorted(
            [c for c, v in cat_groups.items() if len(v) >= 2],
            key=lambda c: sum(cat_groups[c]) / len(cat_groups[c]),
            reverse=True,
        )[:3]

        # Best marketplaces (by avg delta, min 2 samples)
        mp_groups: dict[str, list[float]] = defaultdict(list)
        for r in measured:
            mp_groups[r.marketplace or "all"].append(r.delta_ctr_percent)  # type: ignore[arg-type]
        best_mps = sorted(
            [m for m, v in mp_groups.items() if len(v) >= 2],
            key=lambda m: sum(mp_groups[m]) / len(mp_groups[m]),
            reverse=True,
        )[:2]

        results.append({
            "style_name":       _style_label(*key),
            "win_rate":         win_rate,
            "avg_ctr_uplift":   avg_delta,
            "sample_size":      len(measured),
            "total_rebuilds":   len(rows),
            "best_categories":  [_category_label(c) for c in best_cats],
            "best_marketplaces": [_mp_label(m) for m in best_mps],
            "winners_count":    wins,
        })

    results.sort(key=lambda x: x["avg_ctr_uplift"], reverse=True)
    return results


# ── Schemas ────────────────────────────────────────────────────────────────────

class StyleLeaderboardItem(BaseModel):
    style_name:        str
    win_rate:          int
    avg_ctr_uplift:    float
    sample_size:       int
    total_rebuilds:    int
    best_categories:   list[str]
    best_marketplaces: list[str]
    winners_count:     int


class StyleLeaderboardResponse(BaseModel):
    leaderboard:   list[StyleLeaderboardItem]
    total_styles:  int
    has_data:      bool
    is_filtered:   bool
    min_sample:    int = _MIN_SAMPLE


class RebuildExample(BaseModel):
    product_name:    str
    category:        str
    marketplace:     str
    delta_ctr:       float
    winner:          bool
    date:            str


class StyleDetailResponse(BaseModel):
    style_name:        str
    win_rate:          int
    avg_ctr_uplift:    float
    sample_size:       int
    total_rebuilds:    int
    best_categories:   list[str]
    best_marketplaces: list[str]
    explanation_lines: list[str]
    recent_examples:   list[RebuildExample]
    has_data:          bool


# ── Routes ─────────────────────────────────────────────────────────────────────

@router.get("/seo-intelligence/leaderboard", response_model=StyleLeaderboardResponse)
async def leaderboard(
    marketplace: str = Query("all"),
    category:    str = Query("all"),
    limit:       int = Query(10, ge=1, le=50),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    uid = str(current_user.id)

    q = select(SeoRebuild).where(SeoRebuild.user_id == uid)
    if marketplace != "all":
        q = q.where(SeoRebuild.marketplace == marketplace)
    if category != "all":
        q = q.where(SeoRebuild.category == category)

    rows = (await db.execute(q)).scalars().all()
    stats = _compute_style_stats(list(rows))

    return StyleLeaderboardResponse(
        leaderboard=[StyleLeaderboardItem(**s) for s in stats[:limit]],
        total_styles=len(stats),
        has_data=len(stats) > 0,
        is_filtered=(marketplace != "all" or category != "all"),
    )


@router.get("/seo-intelligence/style/{style_name:path}", response_model=StyleDetailResponse)
async def style_detail(
    style_name:  str,
    marketplace: str = Query("all"),
    category:    str = Query("all"),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    uid = str(current_user.id)

    # Parse style name back to feature flags for filtering
    bigger, minimal, warm, contrast = _flags_from_name(style_name)

    q = select(SeoRebuild).where(
        SeoRebuild.user_id          == uid,
        SeoRebuild.bigger_product_mode == bigger,
        SeoRebuild.minimal_text_mode   == minimal,
        SeoRebuild.warm_gradient_mode  == warm,
        SeoRebuild.high_contrast_mode  == contrast,
    )
    if marketplace != "all":
        q = q.where(SeoRebuild.marketplace == marketplace)
    if category != "all":
        q = q.where(SeoRebuild.category == category)

    rows     = (await db.execute(q)).scalars().all()
    measured = [r for r in rows if r.delta_ctr_percent is not None]

    if not measured:
        return StyleDetailResponse(
            style_name=style_name, win_rate=0, avg_ctr_uplift=0.0,
            sample_size=0, total_rebuilds=len(rows),
            best_categories=[], best_marketplaces=[],
            explanation_lines=[f"Недостаточно данных. Нужно минимум {_MIN_SAMPLE} измеренных rebuilds."],
            recent_examples=[], has_data=False,
        )

    stats_list = _compute_style_stats(list(rows))
    if not stats_list:
        return StyleDetailResponse(
            style_name=style_name, win_rate=0, avg_ctr_uplift=0.0,
            sample_size=len(measured), total_rebuilds=len(rows),
            best_categories=[], best_marketplaces=[],
            explanation_lines=[f"Недостаточно данных. Нужно минимум {_MIN_SAMPLE} измеренных rebuilds."],
            recent_examples=[], has_data=False,
        )

    s = stats_list[0]

    # Build explanation lines
    lines: list[str] = []
    if s["avg_ctr_uplift"] > 0:
        lines.append(f"+{s['avg_ctr_uplift']:.1f}% CTR в среднем по всем rebuilds")
    if s["best_categories"]:
        cat_str = ", ".join(s["best_categories"][:2])
        lines.append(f"Лучшие категории: {cat_str}")
    if s["best_marketplaces"]:
        mp_str = ", ".join(s["best_marketplaces"][:2])
        lines.append(f"Лучше работает на: {mp_str}")
    if s["win_rate"] >= 50:
        lines.append(f"Побеждает в {s['win_rate']}% A/B-тестов")
    if not lines:
        lines.append(f"Основано на {s['sample_size']} измеренных rebuilds")

    # Recent examples (last 5 with measured CTR)
    recent = sorted(measured, key=lambda r: r.created_at or datetime.min, reverse=True)[:5]
    examples = [
        RebuildExample(
            product_name=r.product_name or "—",
            category=_category_label(r.category or ""),
            marketplace=_mp_label(r.marketplace or ""),
            delta_ctr=round(r.delta_ctr_percent, 1),  # type: ignore[arg-type]
            winner=r.winner or False,
            date=(r.created_at.strftime("%d.%m.%Y") if r.created_at else ""),
        )
        for r in recent
    ]

    return StyleDetailResponse(
        style_name=style_name,
        win_rate=s["win_rate"],
        avg_ctr_uplift=s["avg_ctr_uplift"],
        sample_size=s["sample_size"],
        total_rebuilds=s["total_rebuilds"],
        best_categories=s["best_categories"],
        best_marketplaces=s["best_marketplaces"],
        explanation_lines=lines,
        recent_examples=examples,
        has_data=True,
    )


# ── Scheduler helper (called from tasks/scheduler.py) ─────────────────────────

async def get_weekly_learning_insights(user_id: str, db: AsyncSession) -> dict | None:
    """
    Returns style learning bullets for the weekly Telegram report.
    Returns None if insufficient data (< _MIN_SAMPLE measured rebuilds this week).
    """
    week_ago = datetime.utcnow() - timedelta(days=7)

    q = await db.execute(
        select(SeoRebuild).where(
            SeoRebuild.user_id    == user_id,
            SeoRebuild.created_at >= week_ago,
            SeoRebuild.delta_ctr_percent.isnot(None),
        )
    )
    measured = q.scalars().all()

    if len(measured) < _MIN_SAMPLE:
        return None

    # Style win statistics
    style_stats: dict[str, dict] = defaultdict(lambda: {"total": 0, "wins": 0, "deltas": [], "mps": defaultdict(list), "cats": defaultdict(list)})
    for r in measured:
        key = (r.bigger_product_mode, r.minimal_text_mode, r.warm_gradient_mode, r.high_contrast_mode)
        label = _style_label(*key)
        ss    = style_stats[label]
        ss["total"] += 1
        if r.winner:
            ss["wins"] += 1
        ss["deltas"].append(r.delta_ctr_percent)
        ss["mps"][r.marketplace or "all"].append(r.delta_ctr_percent)
        ss["cats"][r.category or "other"].append(r.delta_ctr_percent)

    # Best category overall this week
    cat_all: dict[str, list[float]] = defaultdict(list)
    for r in measured:
        cat_all[r.category or "other"].append(r.delta_ctr_percent)  # type: ignore[arg-type]
    best_cat        = max(cat_all, key=lambda c: sum(cat_all[c]) / len(cat_all[c])) if cat_all else None
    best_cat_delta  = round(sum(cat_all[best_cat]) / len(cat_all[best_cat]), 0) if best_cat else None

    # Build bullets (only for styles with sufficient samples)
    bullets: list[str] = []
    for style, ss in sorted(style_stats.items(), key=lambda x: -(sum(x[1]["deltas"]) / len(x[1]["deltas"]))):
        if ss["total"] < _MIN_SAMPLE:
            continue
        win_rate  = round(ss["wins"] / ss["total"] * 100)
        avg_delta = sum(ss["deltas"]) / len(ss["deltas"])

        # Best marketplace for this style
        best_mp     = max(ss["mps"], key=lambda m: sum(ss["mps"][m]) / len(ss["mps"][m])) if ss["mps"] else None
        best_mp_lbl = _mp_label(best_mp) if best_mp else None

        # Best category for this style
        best_cat_style = max(ss["cats"], key=lambda c: sum(ss["cats"][c]) / len(ss["cats"][c])) if ss["cats"] else None

        if win_rate >= 60:
            bullets.append(f"{style} выигрывает в {win_rate}% rebuilds")
        elif best_mp_lbl and best_cat_style and best_cat_style != "other":
            bullets.append(f"{style} лучше работает на {best_mp_lbl} в {_category_label(best_cat_style)}")
        elif avg_delta > 5:
            bullets.append(f"{style} даёт в среднем +{avg_delta:.0f}% CTR")

        if len(bullets) >= 3:
            break

    if not bullets:
        return None

    return {
        "bullets":             bullets,
        "best_category":       _category_label(best_cat) if best_cat else None,
        "best_category_delta": int(best_cat_delta) if best_cat_delta else None,
        "measured_count":      len(measured),
    }


# ── Recommendation helper (used by action_engine) ─────────────────────────────

async def get_style_recommendation(
    user_id:    str,
    category:   str,
    marketplace: str,
    db:         AsyncSession,
) -> dict | None:
    """
    Returns best style recommendation for a specific category/marketplace.
    Used by Action Engine to explain WHY a style is recommended.
    Returns None if insufficient data.
    """
    q = await db.execute(
        select(SeoRebuild).where(
            SeoRebuild.user_id           == user_id,
            SeoRebuild.delta_ctr_percent.isnot(None),
        )
    )
    all_measured = q.scalars().all()

    if not all_measured:
        return None

    # Prefer category/marketplace match, fall back to all data
    filtered = [
        r for r in all_measured
        if (category == "auto" or r.category == category)
        and (marketplace == "all" or r.marketplace == marketplace)
    ]
    pool = filtered if len(filtered) >= _MIN_SAMPLE else all_measured

    stats = _compute_style_stats(pool)
    if not stats:
        return None

    best = stats[0]
    lines: list[str] = []
    if best["avg_ctr_uplift"] > 0:
        if best["best_categories"]:
            lines.append(f"+{best['avg_ctr_uplift']:.1f}% CTR в категории {best['best_categories'][0]}")
        else:
            lines.append(f"+{best['avg_ctr_uplift']:.1f}% CTR в среднем")
    if best["win_rate"] > 0:
        lines.append(f"{best['win_rate']}% успешных rebuilds")
    if best["best_marketplaces"]:
        lines.append(f"Лучше работает на {best['best_marketplaces'][0]}")

    return {
        "style_name":        best["style_name"],
        "win_rate":          best["win_rate"],
        "avg_ctr_uplift":    best["avg_ctr_uplift"],
        "sample_size":       best["sample_size"],
        "best_categories":   best["best_categories"],
        "best_marketplaces": best["best_marketplaces"],
        "explanation_lines": lines,
        "is_sufficient":     best["sample_size"] >= _MIN_SAMPLE,
    }
