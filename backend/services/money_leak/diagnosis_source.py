"""
Money Leak Detection source (Phase 3.1) — READ-ONLY, DB-headless.

Answers ONLY "WHY does money leak?" per (user, marketplace, sku), from the seller's OWN
observed ImportedFinanceRow — the SILENT cost-structure drift the seller does not actively
manage: the commission share and the logistics share of revenue creeping upward over time.
Observed PRESENT only — never a forecast, never a prognosis, never an invented cause.

Independent contour. It does NOT own price (Pricing), ad spend (Advertising), stock
(Operations), or the revenue trajectory itself (Revenue Diagnosis). It looks at ONE thing
Pricing's absolute-margin state cannot: the DRIFT of the observable cost components as a
share of revenue.

Detection reuses the IDEA of the Revenue source (windowed confirmation + coefficient-of-
variation spike rejection + history/floor gating), reimplemented here independently — it
does NOT import from or modify services/revenue.

Problem types implemented (both are explicit, unambiguous observed finance columns):
  - commission_drift : commission/revenue share confirmed rising
  - logistics_drift  : logistics/revenue share confirmed rising

margin_erosion is deliberately NOT implemented: isolating "net margin fell AND it was not
caused by the seller's price change" needs price-change history that ImportedFinanceRow
does not carry. Emitting it would be attribution guesswork — a fabricated cause — so it is
omitted (honest absence over invention).

Honest absence (emit NOTHING) when: <6 distinct revenue-bearing days; oldest window revenue
below the floor; the cost share is not monotonically rising; or the recent window's daily
ratios are spikey (CV > 0.6). Evidence is a DISCIPLINE (ratio/revenue windows, delta, CV,
day count travel in the signal text + evidence dict) — not a new component.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from models.imported_finance import ImportedFinanceRow

# ── deterministic constants (no fabricated numbers, no external tuning) ───────
MIN_DISTINCT_DAYS = 6
RISE_STEP = 1.05          # each window's cost share must exceed prior × 1.05 to confirm drift
CV_MAX = 0.6              # most-recent-window daily-ratio CV above this = spikey → reject
FLOOR_3W = 50.0           # oldest-window revenue floor (9+ days, 3-window)
FLOOR_2W = 100.0          # oldest-window revenue floor (6–8 days, 2-window)
HIGH_REL = 50.0           # cost share grew ≥50% (relative) → high severity
MED_REL = 20.0            # ≥20% → medium; else low


@dataclass(frozen=True)
class MoneyLeakDiagnosis:
    problem_type: str          # commission_drift | logistics_drift
    marketplace: Optional[str]
    sku: Optional[str]
    priority_level: str        # high | medium | low
    effect_band: str           # high | medium | low
    ratio_growth_pct: int      # observed % rise of the cost share, oldest→newest window
    cv: float
    ratio_windows: Tuple[float, ...]   # observed per-window cost-share (evidence)
    distinct_days: int

    @property
    def signal_key(self) -> str:
        return f"money_leak_{self.problem_type}"

    @property
    def insight_key(self) -> str:
        return f"money_leak_{self.problem_type}:{self.marketplace or 'unknown'}:{self.sku or 'unknown'}"

    @property
    def evidence(self) -> dict:
        return {
            "ratio_windows": [round(r, 4) for r in self.ratio_windows],
            "distinct_days": self.distinct_days,
            "ratio_growth_pct": self.ratio_growth_pct,
            "cv": round(self.cv, 3),
            "periods": len(self.ratio_windows),
        }


async def build_cost_series(
    db: AsyncSession, user_id: str, marketplace: str, sku: str
) -> Dict[str, Tuple[float, float, float]]:
    """Daily (commission, logistics, revenue) for one (user, marketplace, sku), aggregated
    by date. ImportedFinanceRow only — DB-headless, no marketplace client."""
    rows = (await db.execute(
        select(ImportedFinanceRow.date, ImportedFinanceRow.commission,
               ImportedFinanceRow.logistics, ImportedFinanceRow.revenue).where(
            ImportedFinanceRow.user_id == user_id,
            ImportedFinanceRow.marketplace == marketplace,
            ImportedFinanceRow.sku == sku,
            ImportedFinanceRow.date.isnot(None)))).all()
    daily: Dict[str, Tuple[float, float, float]] = {}
    for date, commission, logistics, revenue in rows:
        c, l, r = daily.get(date, (0.0, 0.0, 0.0))
        daily[date] = (c + (commission or 0.0), l + (logistics or 0.0), r + (revenue or 0.0))
    return daily


def _detect(daily: Dict[str, Tuple[float, float, float]], *, cost_index: int, problem_type: str,
            marketplace: str, sku: str) -> Optional[MoneyLeakDiagnosis]:
    """Confirmed upward drift of one cost share (cost_index: 0=commission, 1=logistics)."""
    # revenue-bearing days only (ratio undefined when revenue <= 0)
    days = sorted(d for d, v in daily.items() if v[2] > 0)
    n = len(days)
    if n < MIN_DISTINCT_DAYS:
        return None

    if n >= 9:
        segs = (days[-9:-6], days[-6:-3], days[-3:]); floor = FLOOR_3W
    else:                                              # 6–8 days → 2-window
        segs = (days[-6:-3], days[-3:]); floor = FLOOR_2W

    win_ratio: List[float] = []
    rev_sums: List[float] = []
    for seg in segs:
        cost = sum(daily[d][cost_index] for d in seg)
        rev = sum(daily[d][2] for d in seg)
        if rev <= 0:
            return None
        win_ratio.append(cost / rev)
        rev_sums.append(rev)

    if rev_sums[0] < floor:
        return None                                    # sub-floor: too little revenue to diagnose

    rising = all(win_ratio[i + 1] > win_ratio[i] * RISE_STEP for i in range(len(win_ratio) - 1))
    if not rising:
        return None                                    # flat / falling / non-monotone

    recent_daily = [daily[d][cost_index] / daily[d][2] for d in segs[-1]]
    mean_r = sum(recent_daily) / len(recent_daily)
    cv = ((sum((x - mean_r) ** 2 for x in recent_daily) / len(recent_daily)) ** 0.5 / mean_r
          if mean_r > 0 else 1.0)
    if cv > CV_MAX:
        return None                                    # spikey recent window

    first, last = win_ratio[0], win_ratio[-1]
    growth = round((last - first) / first * 100) if first > 0 else 0
    if growth >= HIGH_REL:
        priority = band = "high"
    elif growth >= MED_REL:
        priority = band = "medium"
    else:
        priority = band = "low"

    return MoneyLeakDiagnosis(
        problem_type=problem_type, marketplace=marketplace, sku=sku,
        priority_level=priority, effect_band=band, ratio_growth_pct=growth, cv=cv,
        ratio_windows=tuple(win_ratio), distinct_days=n)


def classify_cost_drifts(
    daily: Dict[str, Tuple[float, float, float]], *, marketplace: str, sku: str
) -> List[MoneyLeakDiagnosis]:
    """0..2 confirmed cost-share drifts (commission and/or logistics). Independent per kind."""
    out: List[MoneyLeakDiagnosis] = []
    comm = _detect(daily, cost_index=0, problem_type="commission_drift",
                   marketplace=marketplace, sku=sku)
    if comm is not None:
        out.append(comm)
    logi = _detect(daily, cost_index=1, problem_type="logistics_drift",
                   marketplace=marketplace, sku=sku)
    if logi is not None:
        out.append(logi)
    return out
