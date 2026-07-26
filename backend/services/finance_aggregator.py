"""
Finance Aggregator — SQL aggregates for Telegram reports.
Uses ImportedFinanceRow + ImportedProductRow.
All queries are aggregations (no full-table Python loops).
Public functions manage their own sessions.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from decimal import Decimal
from typing import Optional

from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from config import settings
from database import AsyncSessionLocal
from models.imported_finance import ImportedFinanceRow
from models.imported_product import ImportedProductRow
from models.marketplace_store import MarketplaceStore
from models.product import Product
from services.source_policy.money_reader import api_money as _api_money
from services.source_policy.product_totals import resolved_products as _resolved_products
from services.source_policy.resolver import resolve_source as _resolve_source
from services.source_policy.store_totals import _canon as _canon_mp
from services.source_policy.store_totals import resolved_finance_period


# ── Data classes ───────────────────────────────────────────────────────────────

@dataclass
class FinancePeriod:
    revenue:    float = 0.0
    profit:     float = 0.0   # net_profit from import; computed fallback if 0
    orders:     int   = 0
    ad_spend:   float = 0.0
    commission: float = 0.0
    logistics:  float = 0.0
    # Source-policy meta (PULT-LAUNCH-1.4.5H2). Additive/optional: with the flag off every store
    # resolves to CSV, so these carry the default 'csv'/'complete' values and existing callers see
    # exactly the numbers they saw before.
    source:         str  = "csv"
    completeness:   str  = "complete"        # complete | incomplete
    missing_fields: list = field(default_factory=list)
    conflict:       bool = False

    @property
    def has_data(self) -> bool:
        return self.revenue > 0 or self.orders > 0

    @property
    def effective_profit(self) -> float:
        """Use imported net_profit if set; otherwise revenue minus costs."""
        if abs(self.profit) > 0.01:
            return self.profit
        return self.revenue - self.commission - self.logistics - self.ad_spend

    @property
    def margin_pct(self) -> Optional[float]:
        if self.revenue > 0:
            return round(self.effective_profit / self.revenue * 100, 1)
        return None

    @property
    def drr_pct(self) -> Optional[float]:
        if self.revenue > 0:
            return round(self.ad_spend / self.revenue * 100, 1)
        return None

    @property
    def ad_efficiency(self) -> Optional[float]:
        if self.ad_spend > 0:
            return round(self.revenue / self.ad_spend, 2)
        return None


@dataclass
class DailySummary:
    has_data:          bool
    is_demo:           bool
    period_label:      str = ""                   # "вчера" / "14.05.2026"
    data:              FinancePeriod = field(default_factory=FinancePeriod)
    prev:              Optional[FinancePeriod] = None
    delta_revenue_pct: Optional[float] = None
    delta_orders_pct:  Optional[float] = None
    top_product:       Optional[str] = None
    active_products:   int = 0
    avg_rating:        Optional[float] = None


@dataclass
class WeeklySummary:
    has_data:              bool
    is_demo:               bool
    week_label:            str = ""               # "14.05 – 20.05.2026"
    data:                  FinancePeriod = field(default_factory=FinancePeriod)
    prev:                  Optional[FinancePeriod] = None
    delta_revenue_pct:     Optional[float] = None
    delta_profit_pct:      Optional[float] = None
    delta_orders_pct:      Optional[float] = None
    top_products:          list[dict] = field(default_factory=list)
    loss_count:            int = 0
    marketplace_breakdown: list[dict] = field(default_factory=list)
    active_products:       int = 0
    avg_rating:            Optional[float] = None


# ── Private helpers ────────────────────────────────────────────────────────────

def _delta_pct(current: float, previous: float) -> Optional[float]:
    if not previous:
        return None
    return round((current - previous) / previous * 100, 1)


async def _has_any_data(user_id: str, db: AsyncSession) -> bool:
    q = await db.execute(
        select(func.count()).select_from(ImportedFinanceRow)
        .where(ImportedFinanceRow.user_id == user_id)
    )
    return (q.scalar() or 0) > 0


async def _agg_period(
    user_id:   str,
    date_from: str,
    date_to:   str,
    db:        AsyncSession,
) -> FinancePeriod:
    """One period total, built store-by-store through the source resolver then summed
    (PULT-LAUNCH-1.4.5H2). With the flag off this is exactly the whole-user CSV sum."""
    t = await resolved_finance_period(db, user_id, date_from, date_to)
    return FinancePeriod(
        revenue=    t.revenue,
        profit=     (t.net_profit if t.net_profit is not None else 0.0),
        orders=     t.orders,
        ad_spend=   t.ad_spend,
        commission= t.commission,
        logistics=  t.logistics,
        source=     t.revenue_source or "csv",
        completeness=t.completeness,
        missing_fields=list(t.missing_fields),
        conflict=   t.conflict,
    )


async def _top_products(
    user_id:   str,
    date_from: str,
    date_to:   str,
    db:        AsyncSession,
    limit:     int = 5,
) -> list[dict]:
    """Top products by revenue, grouped by (sku, title, marketplace)."""
    rows = (await db.execute(
        select(
            ImportedFinanceRow.sku,
            ImportedFinanceRow.title,
            ImportedFinanceRow.marketplace,
            func.sum(ImportedFinanceRow.revenue).label("revenue"),
            func.sum(ImportedFinanceRow.net_profit).label("profit"),
            func.sum(ImportedFinanceRow.quantity).label("orders"),
        )
        .where(
            ImportedFinanceRow.user_id == user_id,
            ImportedFinanceRow.source  == "csv",   # single-source: never blend API money in (1.4.5H2)
            ImportedFinanceRow.date    >= date_from,
            ImportedFinanceRow.date    <= date_to,
        )
        .group_by(
            ImportedFinanceRow.sku,
            ImportedFinanceRow.title,
            ImportedFinanceRow.marketplace,
        )
        .order_by(func.sum(ImportedFinanceRow.revenue).desc())
        .limit(limit)
    )).all()
    comp = await ranking_completeness(user_id, db)   # 'incomplete' once a store sources via API (1.4.5H3)
    return [
        {
            "sku":         r.sku,
            "title":       r.title or r.sku,
            "marketplace": r.marketplace,
            "revenue":     float(r.revenue   or 0),
            "profit":      float(r.profit    or 0),
            "orders":      int(r.orders      or 0),
            "completeness": comp,
        }
        for r in rows
    ]


async def _loss_products(
    user_id:   str,
    date_from: str,
    date_to:   str,
    db:        AsyncSession,
    limit:     int = 5,
) -> list[dict]:
    """Products with negative net_profit in period."""
    rows = (await db.execute(
        select(
            ImportedFinanceRow.sku,
            ImportedFinanceRow.title,
            ImportedFinanceRow.marketplace,
            func.sum(ImportedFinanceRow.net_profit).label("profit"),
            func.sum(ImportedFinanceRow.revenue).label("revenue"),
        )
        .where(
            ImportedFinanceRow.user_id == user_id,
            ImportedFinanceRow.source  == "csv",   # single-source (1.4.5H2)
            ImportedFinanceRow.date    >= date_from,
            ImportedFinanceRow.date    <= date_to,
        )
        .group_by(
            ImportedFinanceRow.sku,
            ImportedFinanceRow.title,
            ImportedFinanceRow.marketplace,
        )
        .having(func.sum(ImportedFinanceRow.net_profit) < 0)
        .order_by(func.sum(ImportedFinanceRow.net_profit).asc())
        .limit(limit)
    )).all()
    comp = await ranking_completeness(user_id, db)   # (1.4.5H3)
    return [
        {
            "sku":         r.sku,
            "title":       r.title or r.sku,
            "marketplace": r.marketplace,
            "profit":      float(r.profit  or 0),
            "revenue":     float(r.revenue or 0),
            "completeness": comp,
        }
        for r in rows
    ]


async def _marketplace_breakdown(
    user_id:   str,
    date_from: str,
    date_to:   str,
    db:        AsyncSession,
) -> list[dict]:
    rows = (await db.execute(
        select(
            ImportedFinanceRow.marketplace,
            func.sum(ImportedFinanceRow.revenue).label("revenue"),
            func.sum(ImportedFinanceRow.net_profit).label("profit"),
            func.sum(ImportedFinanceRow.quantity).label("orders"),
        )
        .where(
            ImportedFinanceRow.user_id == user_id,
            ImportedFinanceRow.source  == "csv",   # single-source (1.4.5H2)
            ImportedFinanceRow.date    >= date_from,
            ImportedFinanceRow.date    <= date_to,
        )
        .group_by(ImportedFinanceRow.marketplace)
        .order_by(func.sum(ImportedFinanceRow.revenue).desc())
    )).all()
    comp = await ranking_completeness(user_id, db)   # (1.4.5H3)
    return [
        {
            "marketplace": r.marketplace,
            "revenue":     float(r.revenue or 0),
            "profit":      float(r.profit  or 0),
            "orders":      int(r.orders    or 0),
            "completeness": comp,
        }
        for r in rows
    ]


async def _avg_rating(user_id: str, db: AsyncSession) -> Optional[float]:
    q = await db.execute(
        select(func.avg(ImportedProductRow.rating))
        .where(
            ImportedProductRow.user_id == user_id,
            ImportedProductRow.rating.isnot(None),
            # CSV only, until the source policy (1.4.5H) decides how API and CSV combine. API rows
            # live in the same table but must not silently enter a user-facing total.
            ImportedProductRow.source == "csv",
        )
    )
    val = q.scalar()
    return round(float(val), 1) if val else None


async def _active_products_count(user_id: str, db: AsyncSession) -> int:
    q = await db.execute(
        select(func.count(func.distinct(ImportedProductRow.sku)))
        .where(
            ImportedProductRow.user_id == user_id,
            # CSV only until 1.4.5H (see _avg_rating).
            ImportedProductRow.source == "csv",
        )
    )
    return q.scalar() or 0


# ── Public API ─────────────────────────────────────────────────────────────────

async def get_daily_summary(user_id: str) -> DailySummary:
    """
    Yesterday's financials vs day-before-yesterday.
    Falls back to latest available date if yesterday has no data.
    Returns is_demo=True if no data exists at all.
    """
    today     = date.today()
    yest      = today - timedelta(days=1)
    prev_day  = today - timedelta(days=2)

    yest_str     = yest.strftime("%Y-%m-%d")
    prev_str     = prev_day.strftime("%Y-%m-%d")
    today_str    = today.strftime("%Y-%m-%d")

    async with AsyncSessionLocal() as db:
        if not await _has_any_data(user_id, db):
            return DailySummary(has_data=False, is_demo=True, period_label="")

        yest_data = await _agg_period(user_id, yest_str, yest_str, db)

        if yest_data.has_data:
            prev_data     = await _agg_period(user_id, prev_str, prev_str, db)
            main          = yest_data
            comp          = prev_data if prev_data.has_data else None
            period_label  = f"вчера, {yest.strftime('%d.%m.%Y')}"
            top_range     = (yest_str, yest_str)
        else:
            # No yesterday data — try today partial
            today_data = await _agg_period(user_id, today_str, today_str, db)
            if today_data.has_data:
                main         = today_data
                comp         = yest_data if yest_data.has_data else None
                period_label = f"сегодня, {today.strftime('%d.%m.%Y')}"
                top_range    = (today_str, today_str)
            else:
                # No daily-level data (monthly imports) — show last 30 days
                month_ago = (today - timedelta(days=30)).strftime("%Y-%m-%d")
                main = await _agg_period(user_id, month_ago, today_str, db)
                if not main.has_data:
                    return DailySummary(has_data=False, is_demo=True, period_label="")
                comp         = None
                period_label = "последние 30 дней"
                top_range    = (month_ago, today_str)

        top_rows = await _top_products(user_id, top_range[0], top_range[1], db, limit=1)
        top_name = top_rows[0]["title"] if top_rows else None

        avg_rating      = await _avg_rating(user_id, db)
        active_products = await _active_products_count(user_id, db)

    delta_rev = _delta_pct(main.revenue, comp.revenue) if comp else None
    delta_ord = _delta_pct(float(main.orders), float(comp.orders)) if comp else None

    return DailySummary(
        has_data=True, is_demo=False,
        period_label=period_label,
        data=main, prev=comp,
        delta_revenue_pct=delta_rev,
        delta_orders_pct=delta_ord,
        top_product=top_name,
        active_products=active_products,
        avg_rating=avg_rating,
    )


async def get_weekly_summary(user_id: str) -> WeeklySummary:
    """
    Last 7 complete days vs previous 7 days.
    Returns is_demo=True if no data exists at all.
    """
    today     = date.today()
    yest      = today - timedelta(days=1)

    curr_end   = yest
    curr_start = curr_end - timedelta(days=6)   # 7 days ending yesterday
    prev_end   = curr_start - timedelta(days=1)
    prev_start = prev_end - timedelta(days=6)

    curr_end_s   = curr_end.strftime("%Y-%m-%d")
    curr_start_s = curr_start.strftime("%Y-%m-%d")
    prev_end_s   = prev_end.strftime("%Y-%m-%d")
    prev_start_s = prev_start.strftime("%Y-%m-%d")

    week_label = f"{curr_start.strftime('%d.%m')} – {curr_end.strftime('%d.%m.%Y')}"

    async with AsyncSessionLocal() as db:
        if not await _has_any_data(user_id, db):
            return WeeklySummary(has_data=False, is_demo=True, week_label=week_label)

        curr = await _agg_period(user_id, curr_start_s, curr_end_s, db)
        prev = await _agg_period(user_id, prev_start_s, prev_end_s, db)

        if not curr.has_data:
            # Fall back to last 30 days window
            month_ago_s  = (today - timedelta(days=30)).strftime("%Y-%m-%d")
            curr = await _agg_period(user_id, month_ago_s, curr_end_s, db)
            prev = None
            if not curr.has_data:
                return WeeklySummary(has_data=False, is_demo=True, week_label=week_label)

        prev_out = prev if (prev and prev.has_data) else None

        top_prods = await _top_products(user_id, curr_start_s, curr_end_s, db, limit=3)
        loss_rows = await _loss_products(user_id, curr_start_s, curr_end_s, db)
        mp_breakdown = await _marketplace_breakdown(user_id, curr_start_s, curr_end_s, db)
        avg_rating   = await _avg_rating(user_id, db)
        active       = await _active_products_count(user_id, db)

    delta_rev = _delta_pct(curr.revenue, prev_out.revenue) if prev_out else None
    delta_pro = _delta_pct(curr.effective_profit, prev_out.effective_profit) if prev_out else None
    delta_ord = _delta_pct(float(curr.orders), float(prev_out.orders)) if prev_out else None

    return WeeklySummary(
        has_data=True, is_demo=False,
        week_label=week_label,
        data=curr, prev=prev_out,
        delta_revenue_pct=delta_rev,
        delta_profit_pct=delta_pro,
        delta_orders_pct=delta_ord,
        top_products=top_prods,
        loss_count=len(loss_rows),
        marketplace_breakdown=mp_breakdown,
        active_products=active,
        avg_rating=avg_rating,
    )


# ── Ledger = canonical money (Step 2). Session passed in → testable. ──────────
# imported_finance_rows is the ONE money source. FinancialSnapshot is never read.
# Period = substr(date,1,7) (YYYY-MM). avg_margin is the WEIGHTED aggregate
# (Σnet / Σrevenue), not an average-of-monthly-margins.

_PERIOD = func.substr(ImportedFinanceRow.date, 1, 7)


def _margin(net: float, rev: float) -> float:
    return round(net / rev * 100, 2) if rev else 0.0


async def store_financial_totals(store_id: str, db: AsyncSession) -> dict:
    """Revenue / net profit for ONE store (PULT-LAUNCH-1.4.3).

    Filtered strictly by marketplace_store_id, so store A's figures never include store B's rows.
    Rows with product_id IS NULL (money that did not resolve to a Product) ARE included in the
    store total — the store's money must not disappear because a product is unmatched — but they
    are simply not attributed to any product. The existing user-scoped aggregators are left
    unchanged: a cabinet-wide figure sums stores explicitly, it does not silently blend them.
    """
    # conflict rows await the seller and count in nothing until resolved; unassigned money still
    # belongs to the store total (it is simply not attributed to a product). CSV candidate is
    # source='csv' only — never blended with API (1.4.5H2).
    row = (await db.execute(
        select(
            func.coalesce(func.sum(ImportedFinanceRow.revenue), 0.0).label("revenue"),
            func.coalesce(func.sum(ImportedFinanceRow.net_profit), 0.0).label("net"),
            func.coalesce(func.sum(ImportedFinanceRow.revenue).filter(
                ImportedFinanceRow.product_id.is_(None)), 0.0).label("unassigned_revenue"),
        ).where(ImportedFinanceRow.marketplace_store_id == store_id,
                ImportedFinanceRow.source == "csv",
                ImportedFinanceRow.link_status != "conflict")
    )).one()
    csv_rev, csv_net = float(row.revenue), float(row.net)
    out = {"revenue": round(csv_rev, 2), "net_profit": round(csv_net, 2),
           "unassigned_revenue": round(float(row.unassigned_revenue), 2),
           "source": "csv", "completeness": "complete", "missing_fields": [], "conflict": False}

    # Resolve API vs CSV for this store's revenue (flag off ⇒ CSV, byte-identical to before).
    if settings.api_data_sync_enabled:
        store = await db.get(MarketplaceStore, store_id)
        marketplace = _canon_mp(store.marketplace) if store else None
        if marketplace:
            api_rev = await _api_money(db, store_id=store_id, marketplace=marketplace,
                                       metric_type="revenue", period=None)
            res = await _resolve_source(db, store_id=store_id, marketplace=marketplace,
                                        metric_type="revenue", period=None,
                                        api_value=(Decimal(str(api_rev)) if api_rev is not None else None),
                                        csv_value=Decimal(str(csv_rev)))
            out["source"] = res.source or "csv"
            out["conflict"] = res.conflict
            if res.source == "api":
                out["revenue"] = round(float(res.value), 2) if res.value is not None else 0.0
                # net profit follows the revenue source; API has no cost of goods → suppress exact.
                out["net_profit"] = None
                out["completeness"] = "incomplete"
                out["missing_fields"] = ["cogs"]
    return out


async def summary_by_product(user_id: str, db: AsyncSession) -> tuple[list[dict], dict]:
    """
    Per-product money summary from the ledger, JOINed to the canonical Product.
    Returns (items, totals). `items` reconstructs FinanceSummaryItem; a synthetic
    "unassigned" item carries ledger rows whose product_id is NULL so that
    Σ(items) == whole-ledger total (no drift between strip and breakdown).

    With the flag on, each product's money is resolved per store through the source policy
    (PULT-LAUNCH-1.4.5H3); an API product with incomplete attribution is marked incomplete and its
    exact profit is suppressed. Flag off is byte-identical to the CSV query below.
    """
    if settings.api_data_sync_enabled:
        return await _resolved_summary_by_product(user_id, db)

    # Resolved rows → per canonical product
    rows = (await db.execute(
        select(
            Product.id.label("pid"),
            Product.name.label("pname"),
            func.coalesce(func.sum(ImportedFinanceRow.revenue),    0.0).label("revenue"),
            func.coalesce(func.sum(ImportedFinanceRow.net_profit), 0.0).label("net"),
            func.count(func.distinct(_PERIOD)).label("periods"),
        )
        .join(Product, Product.id == ImportedFinanceRow.product_id)
        .where(ImportedFinanceRow.user_id == user_id,
               ImportedFinanceRow.source == "csv")   # single-source (1.4.5H2)
        .group_by(Product.id, Product.name)
    )).all()

    items: list[dict] = []
    for r in rows:
        rev, net = float(r.revenue), float(r.net)
        items.append({
            "product_id":         r.pid,
            "product_name":       r.pname,
            "total_revenue":      round(rev, 2),
            "total_net_profit":   round(net, 2),
            "avg_margin_percent": _margin(net, rev),
            "snapshots_count":    int(r.periods or 0),
        })

    # Unassigned (product_id IS NULL) — real money with no catalog link
    un = (await db.execute(
        select(
            func.coalesce(func.sum(ImportedFinanceRow.revenue),    0.0).label("revenue"),
            func.coalesce(func.sum(ImportedFinanceRow.net_profit), 0.0).label("net"),
            func.count(func.distinct(_PERIOD)).label("periods"),
        ).where(
            ImportedFinanceRow.user_id == user_id,
            ImportedFinanceRow.source == "csv",       # single-source (1.4.5H2)
            ImportedFinanceRow.product_id.is_(None),
        )
    )).one()
    if float(un.revenue) or float(un.net):
        items.append({
            "product_id":         "__unassigned__",
            "product_name":       "Без привязки к товару",
            "total_revenue":      round(float(un.revenue), 2),
            "total_net_profit":   round(float(un.net), 2),
            "avg_margin_percent": _margin(float(un.net), float(un.revenue)),
            "snapshots_count":    int(un.periods or 0),
        })

    items.sort(key=lambda x: x["total_revenue"], reverse=True)

    # Authoritative whole-ledger total (single query) — strip must equal this.
    tot = (await db.execute(
        select(
            func.coalesce(func.sum(ImportedFinanceRow.revenue),    0.0),
            func.coalesce(func.sum(ImportedFinanceRow.net_profit), 0.0),
        ).where(ImportedFinanceRow.user_id == user_id,
                ImportedFinanceRow.source == "csv")   # single-source (1.4.5H2)
    )).one()
    totals = {"revenue": round(float(tot[0]), 2), "net_profit": round(float(tot[1]), 2)}
    return items, totals


async def _resolved_summary_by_product(user_id: str, db: AsyncSession) -> tuple[list[dict], dict]:
    """summary_by_product with each product's money resolved per store (flag on, 1.4.5H3)."""
    products = await _resolved_products(db, user_id, None, None)   # all-time (period-less coverage)
    items: list[dict] = []
    for pm in products.values():
        rev, net = pm.revenue, pm.net_profit
        items.append({
            "product_id":         pm.product_id,
            "product_name":       pm.name or pm.product_id,
            "total_revenue":      round(rev, 2),
            "total_net_profit":   (round(net, 2) if net is not None else None),
            "avg_margin_percent": (_margin(net, rev) if net is not None else None),
            "snapshots_count":    0,
            "source":             pm.source,
            "completeness":       pm.completeness,
            "missing_fields":     list(pm.missing_fields),
            "conflict":           pm.conflict,
        })
    # Unassigned CSV money (product_id NULL) stays CSV and is never split across products.
    un = (await db.execute(
        select(func.coalesce(func.sum(ImportedFinanceRow.revenue), 0.0),
               func.coalesce(func.sum(ImportedFinanceRow.net_profit), 0.0))
        .where(ImportedFinanceRow.user_id == user_id, ImportedFinanceRow.source == "csv",
               ImportedFinanceRow.product_id.is_(None)))).one()
    if float(un[0]) or float(un[1]):
        items.append({
            "product_id": "__unassigned__", "product_name": "Без привязки к товару",
            "total_revenue": round(float(un[0]), 2), "total_net_profit": round(float(un[1]), 2),
            "avg_margin_percent": _margin(float(un[1]), float(un[0])), "snapshots_count": 0,
            "source": "csv", "completeness": "complete", "missing_fields": [], "conflict": False})
    items.sort(key=lambda x: x["total_revenue"], reverse=True)
    tot_rev = round(sum(i["total_revenue"] for i in items), 2)
    tot_net = round(sum((i["total_net_profit"] or 0.0) for i in items), 2)
    return items, {"revenue": tot_rev, "net_profit": tot_net}


async def ranking_completeness(user_id: str, db: AsyncSession) -> str:
    """'incomplete' when a CSV-built ranking (top/loss/breakdown) can no longer be presented as an
    exact total because a store now sources money from the API (1.4.5H3). Flag off ⇒ always
    'complete'. Marks the ranking honestly instead of silently mixing sources."""
    if not settings.api_data_sync_enabled:
        return "complete"
    from models.store_data_source_policy import StoreDataSourcePolicy  # local import to avoid cycle
    from models.marketplace_store import MarketplaceStore as _Store
    from models.marketplace_account import MarketplaceAccount as _Acc
    rows = (await db.execute(
        select(StoreDataSourcePolicy.preference)
        .join(_Store, _Store.id == StoreDataSourcePolicy.marketplace_store_id)
        .join(_Acc, _Acc.id == _Store.marketplace_account_id)
        .join(Product, Product.marketplace_account_id == _Acc.id)
        .join(ImportedFinanceRow, ImportedFinanceRow.product_id == Product.id)
        .where(ImportedFinanceRow.user_id == user_id,
               StoreDataSourcePolicy.metric_type == "revenue",
               StoreDataSourcePolicy.preference.in_(("api", "auto"))))).scalars().first()
    return "incomplete" if rows else "complete"


async def product_monthly_rollup(product_id: str, user_id: str, db: AsyncSession) -> list[dict]:
    """
    Per-product monthly rollup from the ledger, shaped as FinancialSnapshotOut.
    cogs is the RESIDUAL (revenue - net - commission - ad_spend) so the breakdown
    sums to the real ledger net_profit. Rows with no date are skipped.
    """
    rows = (await db.execute(
        select(
            _PERIOD.label("period"),
            func.sum(ImportedFinanceRow.revenue).label("revenue"),
            func.sum(ImportedFinanceRow.commission).label("commission"),
            func.sum(ImportedFinanceRow.ad_spend).label("ad_spend"),
            func.sum(ImportedFinanceRow.net_profit).label("net"),
        )
        .where(
            ImportedFinanceRow.product_id == product_id,
            ImportedFinanceRow.user_id == user_id,
            ImportedFinanceRow.source == "csv",       # single-source (1.4.5H2)
            ImportedFinanceRow.date.isnot(None),
        )
        .group_by(_PERIOD)
        .order_by(_PERIOD)
    )).all()

    comp = await ranking_completeness(user_id, db)   # (1.4.5H3)
    out: list[dict] = []
    for r in rows:
        rev = float(r.revenue or 0); net = float(r.net or 0)
        fee = float(r.commission or 0); ad = float(r.ad_spend or 0)
        out.append({
            "id":              f"{product_id}:{r.period}",
            "product_id":      product_id,
            "period":          r.period,
            "revenue":         round(rev, 2),
            "marketplace_fee": round(fee, 2),
            "ad_spend":        round(ad, 2),
            "cogs":            round(rev - net - fee - ad, 2),   # residual (incl logistics + COGS)
            "net_profit":      round(net, 2),
            "margin_percent":  _margin(net, rev),
            "completeness":    comp,
            "created_at":      datetime.utcnow(),
        })
    return out


# ── Standalone helpers (for router use) ───────────────────────────────────────

async def get_top_products(user_id: str, days: int = 7, limit: int = 5) -> list[dict]:
    today = date.today()
    d_from = (today - timedelta(days=days)).strftime("%Y-%m-%d")
    d_to   = today.strftime("%Y-%m-%d")
    async with AsyncSessionLocal() as db:
        return await _top_products(user_id, d_from, d_to, db, limit)


async def get_loss_products(user_id: str, days: int = 7, limit: int = 5) -> list[dict]:
    today = date.today()
    d_from = (today - timedelta(days=days)).strftime("%Y-%m-%d")
    d_to   = today.strftime("%Y-%m-%d")
    async with AsyncSessionLocal() as db:
        return await _loss_products(user_id, d_from, d_to, db, limit)


async def get_marketplace_breakdown(user_id: str, days: int = 7) -> list[dict]:
    today = date.today()
    d_from = (today - timedelta(days=days)).strftime("%Y-%m-%d")
    d_to   = today.strftime("%Y-%m-%d")
    async with AsyncSessionLocal() as db:
        return await _marketplace_breakdown(user_id, d_from, d_to, db)
