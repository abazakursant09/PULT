"""
Finance ingestion freshness — READ-ONLY observed facts (Measure Quality).

Surfaces, for one (user, marketplace, sku), the OBSERVED freshness of the finance
data that Effect/Measure depends on (ImportedFinanceRow): the latest data date, the
latest import time, and how many rows fall inside the measurement window.

Honesty: observed facts ONLY. No freshness score, no staleness threshold, no
"outdated" verdict, no forecast/AI/competitor. SELECT / MAX / COUNT only — never a
write. Uses the SAME marketplace-alias + sku filter as finance_metric_reader, so the
"rows_in_window" count matches exactly what the reader would see.
"""
from __future__ import annotations

from datetime import datetime, timedelta
from typing import Optional

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from models.imported_finance import ImportedFinanceRow
from services.marketplace.finance_metric_reader import _MP_ALIASES


async def finance_freshness(
    db: Optional[AsyncSession],
    *,
    user_id: Optional[str],
    marketplace: Optional[str],
    sku,
    window_days: int = 14,
    now: Optional[datetime] = None,
) -> Optional[dict]:
    """Observed freshness facts for the finance data behind a measurement, or None
    when the lookup cannot run (no db / user / sku). Never writes."""
    if db is None or not user_id or not sku:
        return None

    now = now or datetime.utcnow()
    window_start = (now - timedelta(days=window_days)).date().isoformat()
    window_end = now.date().isoformat()
    aliases = _MP_ALIASES.get((marketplace or "").lower(), ((marketplace or "").lower(),))

    base = (
        ImportedFinanceRow.user_id == user_id,
        ImportedFinanceRow.marketplace.in_(aliases),
        ImportedFinanceRow.sku == str(sku),
    )

    # latest available data date + latest import time over ALL rows for this entity
    agg = (await db.execute(select(
        func.max(ImportedFinanceRow.date),
        func.max(ImportedFinanceRow.created_at),
    ).where(*base))).one()
    last_finance_date = agg[0]
    last_import_at = agg[1].isoformat() if agg[1] is not None else None

    # rows that fall inside the measurement window (same filter the reader applies)
    rows_in_window = (await db.execute(select(func.count()).where(
        *base,
        ImportedFinanceRow.date.isnot(None),
        ImportedFinanceRow.date >= window_start,
        ImportedFinanceRow.date <= window_end,
    ))).scalar() or 0

    return {
        "last_finance_date": last_finance_date,     # YYYY-MM-DD or None
        "last_import_at": last_import_at,            # ISO datetime or None
        "rows_in_window": int(rows_in_window),       # observed count, 0 when none
        "window_start": window_start,
        "window_end": window_end,
    }
