"""
Money Leak Detection source (Phase 3.1) — READ-ONLY, DB-headless.

Answers ONLY "WHY does money leak?" per (user, marketplace, sku), from the seller's OWN
observed ImportedFinanceRow — the SILENT cost-structure drift the seller does not actively
manage: the commission share and the logistics share of revenue creeping upward over time.
Observed PRESENT only — never a forecast, never a prognosis, never an invented cause.

Independent contour. It does NOT own price (Pricing), stock (Operations), or the revenue
trajectory itself (Revenue Diagnosis). It looks at ONE thing Pricing's absolute-margin state
cannot: the DRIFT of the observable cost components as a share of revenue.

MONEY LEAK ↔ ADVERTISING BOUNDARY: Money Leak MAY diagnose the observed DRIFT of any finance
cost share over time, INCLUDING ad_spend (DRR = ad_spend/revenue rising = ad_cost_drift). The
Advertising contour owns the EXECUTABLE, point-in-time ad STATE and its levers (stop/reduce
budget) — "ads are bad right now". Money Leak owns the advisory TREND — "ad efficiency is
deteriorating over time" — with no lever bound. STATE-vs-TREND, the same split as
Pricing (executable) ↔ Price-Erosion (diagnostic). No overlap: different math, different
surface, different question.

Detection reuses the IDEA of the Revenue source (windowed confirmation + coefficient-of-
variation spike rejection + history/floor gating), reimplemented here independently — it
does NOT import from or modify services/revenue.

Problem types implemented (all are explicit, unambiguous observed finance columns):
  - commission_drift : commission/revenue share confirmed rising
  - logistics_drift  : logistics/revenue share confirmed rising
  - ad_cost_drift    : ad_spend/revenue share (DRR) confirmed rising — advisory-only, with an
                       onset guard so STARTING to advertise is never misread as erosion (only
                       deterioration of CONTINUOUS advertising counts).

margin_erosion is deliberately NOT implemented: isolating "net margin fell AND it was not
caused by the seller's price change" needs price-change history that ImportedFinanceRow
does not carry. Emitting it would be attribution guesswork — a fabricated cause — so it is
omitted (honest absence over invention). ad_cost_drift does NOT have this problem: ad_spend
is a directly observed column and DRR is directly computed — no attribution guesswork.

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
MIN_AD_SPEND = 10.0       # ad onset guard: oldest window ad_spend must reach this small,
                          # deterministic floor AND ad_spend must be present in EVERY window —
                          # starting to advertise (0 → positive) is NOT erosion, and a tiny
                          # base must not inflate DRR growth into a false diagnosis.


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
) -> Dict[str, Tuple[float, float, float, float]]:
    """Daily (commission, logistics, ad_spend, revenue) for one (user, marketplace, sku),
    aggregated by date. ImportedFinanceRow only — DB-headless, no marketplace client.
    Revenue is the LAST tuple element (index _REV); cost columns are indexed 0..2."""
    rows = (await db.execute(
        select(ImportedFinanceRow.date, ImportedFinanceRow.commission,
               ImportedFinanceRow.logistics, ImportedFinanceRow.ad_spend,
               ImportedFinanceRow.revenue).where(
            ImportedFinanceRow.user_id == user_id,
            ImportedFinanceRow.marketplace == marketplace,
            ImportedFinanceRow.sku == sku,
            ImportedFinanceRow.date.isnot(None)))).all()
    daily: Dict[str, Tuple[float, float, float, float]] = {}
    for date, commission, logistics, ad_spend, revenue in rows:
        c, l, a, r = daily.get(date, (0.0, 0.0, 0.0, 0.0))
        daily[date] = (c + (commission or 0.0), l + (logistics or 0.0),
                       a + (ad_spend or 0.0), r + (revenue or 0.0))
    return daily


# revenue is the LAST element of the daily tuple (commission, logistics, ad_spend, revenue)
_REV = 3


def _detect(daily: Dict[str, Tuple[float, float, float, float]], *, cost_index: int,
            problem_type: str, marketplace: str, sku: str,
            min_first_window_cost: float = 0.0,
            require_cost_all_windows: bool = False) -> Optional[MoneyLeakDiagnosis]:
    """Confirmed upward drift of one cost share (cost_index: 0=commission, 1=logistics,
    2=ad_spend). The two ad-onset guard params default off (commission/logistics are
    marketplace-imposed and effectively always present); ad_cost_drift passes them to reject
    "just started advertising" and tiny-base inflation."""
    # revenue-bearing days only (ratio undefined when revenue <= 0)
    days = sorted(d for d, v in daily.items() if v[_REV] > 0)
    n = len(days)
    if n < MIN_DISTINCT_DAYS:
        return None

    if n >= 9:
        segs = (days[-9:-6], days[-6:-3], days[-3:]); floor = FLOOR_3W
    else:                                              # 6–8 days → 2-window
        segs = (days[-6:-3], days[-3:]); floor = FLOOR_2W

    win_ratio: List[float] = []
    rev_sums: List[float] = []
    win_cost: List[float] = []
    for seg in segs:
        cost = sum(daily[d][cost_index] for d in seg)
        rev = sum(daily[d][_REV] for d in seg)
        if rev <= 0:
            return None
        win_ratio.append(cost / rev)
        rev_sums.append(rev)
        win_cost.append(cost)

    if rev_sums[0] < floor:
        return None                                    # sub-floor: too little revenue to diagnose

    # ── ad-onset guard (only when the caller asks) ────────────────────────────
    # oldest window's cost must clear a small deterministic floor AND the cost must be
    # present in EVERY window — so onset (0 → positive) and tiny-base inflation are rejected.
    if win_cost[0] < min_first_window_cost:
        return None
    if require_cost_all_windows and any(c <= 0 for c in win_cost):
        return None

    rising = all(win_ratio[i + 1] > win_ratio[i] * RISE_STEP for i in range(len(win_ratio) - 1))
    if not rising:
        return None                                    # flat / falling / non-monotone

    recent_daily = [daily[d][cost_index] / daily[d][_REV] for d in segs[-1]]
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
    daily: Dict[str, Tuple[float, float, float, float]], *, marketplace: str, sku: str
) -> List[MoneyLeakDiagnosis]:
    """0..3 confirmed cost-share drifts (commission, logistics, ad_spend). Independent per
    kind — a (mp, sku) may carry any combination, each its own insight_key."""
    out: List[MoneyLeakDiagnosis] = []
    comm = _detect(daily, cost_index=0, problem_type="commission_drift",
                   marketplace=marketplace, sku=sku)
    if comm is not None:
        out.append(comm)
    logi = _detect(daily, cost_index=1, problem_type="logistics_drift",
                   marketplace=marketplace, sku=sku)
    if logi is not None:
        out.append(logi)
    # ad_cost_drift: DRR = ad_spend/revenue. Onset guard ON — advertising must be present in
    # every window and clear MIN_AD_SPEND in the oldest, so onset/tiny-base is not erosion.
    ad = _detect(daily, cost_index=2, problem_type="ad_cost_drift",
                 marketplace=marketplace, sku=sku,
                 min_first_window_cost=MIN_AD_SPEND, require_cost_all_windows=True)
    if ad is not None:
        out.append(ad)
    return out
