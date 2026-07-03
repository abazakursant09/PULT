"""
Revenue Diagnosis source (Phase 2.1) — READ-ONLY, DB-headless.

Answers ONLY "WHERE is revenue disappearing?" per (user, marketplace, sku), from the
seller's OWN observed ImportedFinanceRow daily revenue series. Diagnoses the observed
PRESENT — never a forecast, never a prognosis, never an invented cause.

Temporal skeleton reuses the IDEA of routers/action_engine.py::_growth_maturity (windowed
confirmation + coefficient-of-variation spike rejection), reimplemented here symmetrically
(the legacy detects only upward growth). It is a fresh read-only function — action_engine
and _compute_insights are NOT touched.

Confirmed trajectories only:
  - acceleration       (revenue confirmed rising)
  - slowing            (mild confirmed decline, <20%)
  - sustained_decline  (confirmed decline ≥20%)
  - collapse           (confirmed decline ≥50%)

Honest absence (emit NOTHING) when: <6 distinct dated days; oldest window below the
revenue evidence floor; not monotone (flat/noisy/non-directional); or the most recent
window is spikey (CV > 0.6).

Evidence is a DISCIPLINE, not a component: the observed windows / sums / % change / CV /
distinct-day count travel inside the signal (doctrine text + evidence dict for the
reconciliation hash). No new Evidence table or service.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from models.imported_finance import ImportedFinanceRow

# ── deterministic constants (no fabricated numbers, no external tuning) ───────
MIN_DISTINCT_DAYS = 6
RISE_STEP = 1.05          # each window must rise > prior × 1.05 to confirm growth
FALL_STEP = 0.95          # each window must fall < prior × 0.95 to confirm decline
CV_MAX = 0.6              # most-recent-window coefficient of variation above this = spike
FLOOR_3W = 50.0           # oldest-window revenue floor (9+ days, 3-window)
FLOOR_2W = 100.0          # oldest-window revenue floor (6–8 days, 2-window)
COLLAPSE_RATIO = 0.5      # final window ≤ first × 0.5  → collapse (≥50% drop)
DECLINE_RATIO = 0.8       # final window ≤ first × 0.8  → sustained_decline (≥20% drop)


@dataclass(frozen=True)
class RevenueDiagnosis:
    problem_type: str          # acceleration | slowing | sustained_decline | collapse
    marketplace: Optional[str]
    sku: Optional[str]
    priority_level: str        # critical | high | medium | low
    effect_band: str           # high | medium | low
    growth_pct: int            # observed % change oldest→newest window (negative = decline)
    cv: float
    windows: tuple             # observed window sums (evidence)
    distinct_days: int

    @property
    def signal_key(self) -> str:
        return f"revenue_{self.problem_type}"

    @property
    def insight_key(self) -> str:
        return f"revenue_{self.problem_type}:{self.marketplace or 'unknown'}:{self.sku or 'unknown'}"

    @property
    def evidence(self) -> dict:
        return {
            "windows": [round(w, 2) for w in self.windows],
            "distinct_days": self.distinct_days,
            "growth_pct": self.growth_pct,
            "cv": round(self.cv, 3),
            "periods": len(self.windows),
        }


async def build_revenue_series(
    db: AsyncSession, user_id: str, marketplace: str, sku: str) -> Dict[str, float]:
    """Daily revenue series for one (user, marketplace, sku), aggregated by date.
    ImportedFinanceRow only — DB-headless, no marketplace client."""
    rows = (await db.execute(
        select(ImportedFinanceRow.date, ImportedFinanceRow.revenue).where(
            ImportedFinanceRow.user_id == user_id,
            ImportedFinanceRow.marketplace == marketplace,
            ImportedFinanceRow.sku == sku,
            ImportedFinanceRow.date.isnot(None)))).all()
    daily: Dict[str, float] = {}
    for date, revenue in rows:
        daily[date] = daily.get(date, 0.0) + (revenue or 0.0)
    return daily


def classify_revenue_trajectory(
    daily: Dict[str, float], *, marketplace: str, sku: str) -> Optional[RevenueDiagnosis]:
    """Confirmed revenue trajectory, or None (honest absence). Observed-only."""
    dates = sorted(daily.keys())
    n = len(dates)
    if n < MIN_DISTINCT_DAYS:
        return None                                        # insufficient history

    if n >= 9:
        w1v = [daily[d] for d in dates[-9:-6]]
        w2v = [daily[d] for d in dates[-6:-3]]
        w3v = [daily[d] for d in dates[-3:]]
        windows = (sum(w1v), sum(w2v), sum(w3v)); recent = w3v; floor = FLOOR_3W
    else:                                                  # 6–8 days → 2-window fallback
        w1v = [daily[d] for d in dates[-6:-3]]
        w2v = [daily[d] for d in dates[-3:]]
        windows = (sum(w1v), sum(w2v)); recent = w2v; floor = FLOOR_2W

    w_first, w_last = windows[0], windows[-1]
    if w_first < floor:
        return None                                        # sub-floor: not enough revenue to diagnose

    rising = all(windows[i + 1] > windows[i] * RISE_STEP for i in range(len(windows) - 1))
    falling = all(windows[i + 1] < windows[i] * FALL_STEP for i in range(len(windows) - 1))
    if not (rising or falling):
        return None                                        # flat / noisy / non-monotone → not confirmed

    mean_r = w_last / 3.0
    cv = (sum((v - mean_r) ** 2 for v in recent) / 3.0) ** 0.5 / mean_r if mean_r > 0 else 1.0
    if cv > CV_MAX:
        return None                                        # spike / noisy recent window

    growth_pct = round((w_last - w_first) / w_first * 100) if w_first > 0 else 0

    if rising:
        problem_type, priority = "acceleration", "low"
        band = "high" if growth_pct >= 50 else "medium"
    else:
        ratio = w_last / w_first if w_first > 0 else 1.0
        if ratio <= COLLAPSE_RATIO:
            problem_type, priority, band = "collapse", "critical", "high"
        elif ratio <= DECLINE_RATIO:
            problem_type, priority, band = "sustained_decline", "high", "medium"
        else:
            problem_type, priority, band = "slowing", "medium", "low"

    return RevenueDiagnosis(
        problem_type=problem_type, marketplace=marketplace, sku=sku,
        priority_level=priority, effect_band=band, growth_pct=growth_pct, cv=cv,
        windows=windows, distinct_days=n)
