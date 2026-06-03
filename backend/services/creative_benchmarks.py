from __future__ import annotations
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func
from models.seo_rebuild import SeoRebuild

_MIN_SAMPLE = 3


async def get_benchmarks(user_id: str, db: AsyncSession) -> dict:
    """Aggregated benchmarks from user's own rebuild history. No cross-user data."""
    q = await db.execute(
        select(
            SeoRebuild.preset,
            SeoRebuild.category,
            SeoRebuild.marketplace,
            func.count(SeoRebuild.id).label("cnt"),
            func.avg(SeoRebuild.delta_ctr_percent).label("avg_ctr"),
        )
        .where(
            SeoRebuild.user_id == user_id,
            SeoRebuild.delta_ctr_percent.isnot(None),
        )
        .group_by(SeoRebuild.preset, SeoRebuild.category, SeoRebuild.marketplace)
    )
    rows = q.all()

    by_preset: dict[str, dict] = {}
    by_category: dict[str, dict] = {}
    total_count = 0

    for row in rows:
        preset = row.preset or "unknown"
        cat    = row.category or "unknown"
        cnt    = int(row.cnt or 0)
        ctr    = float(row.avg_ctr or 0)
        total_count += cnt

        if preset not in by_preset:
            by_preset[preset] = {"count": 0, "ctr_sum": 0.0}
        by_preset[preset]["count"]   += cnt
        by_preset[preset]["ctr_sum"] += ctr * cnt

        if cat not in by_category:
            by_category[cat] = {"count": 0, "ctr_sum": 0.0}
        by_category[cat]["count"]   += cnt
        by_category[cat]["ctr_sum"] += ctr * cnt

    preset_stats = sorted(
        [
            {
                "preset": p,
                "count": d["count"],
                "avg_ctr_uplift": round(d["ctr_sum"] / d["count"], 1),
            }
            for p, d in by_preset.items()
            if d["count"] >= _MIN_SAMPLE
        ],
        key=lambda x: x["avg_ctr_uplift"],
        reverse=True,
    )

    category_stats = sorted(
        [
            {
                "category": c,
                "count": d["count"],
                "avg_ctr_uplift": round(d["ctr_sum"] / d["count"], 1),
            }
            for c, d in by_category.items()
            if d["count"] >= _MIN_SAMPLE
        ],
        key=lambda x: x["avg_ctr_uplift"],
        reverse=True,
    )

    return {
        "has_data":       total_count >= _MIN_SAMPLE,
        "total_rebuilds": total_count,
        "preset_stats":   preset_stats,
        "category_stats": category_stats,
        "top_preset":     preset_stats[0]["preset"] if preset_stats else None,
        "top_category":   category_stats[0]["category"] if category_stats else None,
    }
