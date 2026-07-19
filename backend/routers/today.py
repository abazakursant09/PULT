"""
Today API (One Morning Truth, A19) — read-only canonical "what to do today".

A thin HTTP surface over the Today service (services/decision_feed/today), which is
itself a read-only projection of the canonical Daily Decision Feed (build_feed). This
endpoint exists so non-feed surfaces (Copilot) can read the SAME canonical source as
the Dashboard feed and the Telegram top action — one morning truth.

Read-only: GET only. No writes, no signal creation, no Decision/Apply, no executor.
It NEVER touches the Decision Feed API, build_feed, or the Today service semantics —
it only serializes their output. The legacy on-read insights engine is not imported
or called here.
"""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from database import get_db
from dependencies import get_current_user
from models.user import User

from services.decision_feed.today import build_today, TodayAction
from services.decision_feed.builder import build_feed

router = APIRouter()


class TodayItemView(BaseModel):
    item_key: str
    contour: str
    marketplace: Optional[str]
    sku: Optional[str]
    title: Optional[str]
    what_happened: Optional[str]
    why_it_matters: Optional[str]
    meaning: Optional[str]
    recommended_action: Optional[str]
    expected_effect: Optional[str]
    source_status: Optional[str]
    attention_state: str
    effect_status: Optional[str]
    effect_band: Optional[str]
    group_key: Optional[str] = None
    action_key: Optional[str] = None
    action_role: Optional[str] = None
    learning_context: Optional[str] = None
    created_at: Optional[str] = None
    updated_at: Optional[str] = None


def _view(a: TodayAction) -> TodayItemView:
    return TodayItemView(
        item_key=a.item_key, contour=a.contour, marketplace=a.marketplace, sku=a.sku,
        title=a.title, what_happened=a.what_happened, why_it_matters=a.why_it_matters,
        meaning=a.meaning, recommended_action=a.recommended_action,
        expected_effect=a.expected_effect, source_status=a.source_status,
        attention_state=a.attention_state, effect_status=a.effect_status,
        effect_band=a.effect_band, group_key=a.group_key, action_key=a.action_key,
        action_role=a.action_role, learning_context=a.learning_context,
        created_at=a.created_at.isoformat() if a.created_at else None,
        updated_at=a.updated_at.isoformat() if a.updated_at else None,
    )


class TodayResponse(BaseModel):
    items: list[TodayItemView]
    total: int
    # convenience: the single highest-priority item (first), or null when empty
    top_action: Optional[TodayItemView] = None


@router.get("/today", response_model=TodayResponse)
async def get_today(
    contour: Optional[str] = None,
    limit: int = 50,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> TodayResponse:
    """Canonical 'what to do today' — the Today projection over build_feed. Read-only.
    `top_action` is always the first item (or null when there is nothing live)."""
    items = await build_today(db, user_id=current_user.id, contour=contour, limit=limit)
    views = [_view(i) for i in items]
    return TodayResponse(items=views, total=len(views),
                         top_action=views[0] if views else None)


class TodaySummaryView(BaseModel):
    """'Состояние бизнеса сегодня' — read-only assembly of EXISTING aggregates. No new
    analytics: money comes verbatim from get_daily_summary, loss count from
    get_loss_products, the three feed counts are plain len() over build_feed."""
    revenue_today: float
    profit_today: float
    margin_pct: Optional[float]
    delta_revenue_pct: Optional[float]
    critical_count: int
    loss_products_count: int
    growth_opportunities_count: int
    low_stock_count: int
    is_demo: bool
    has_data: bool


class FirstRunView(BaseModel):
    """The seller's real position, so the first screen can stop guessing.

    `state` is one of no_data / analyzing / ready / insufficient / failed. `missing` is populated
    only for `insufficient`, and names the concrete gap rather than apologising in general terms.
    No completion estimate is returned: the runtime does not guarantee one, so promising one would
    be inventing a fact.
    """
    state: str
    finance_days: int
    product_snapshots: int
    has_products: bool
    has_returns: bool
    missing: list[str]


@router.get("/today/first-run", response_model=FirstRunView)
async def get_first_run_state(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> FirstRunView:
    """Read-only. Counts existing rows; computes no diagnosis and writes nothing."""
    from services.first_run_state import compute_first_run_state

    # Same source the dashboard's card check uses, so "ready" here and a rendered diagnosis can
    # never disagree.
    feed = await build_feed(db, user_id=current_user.id, limit=1)
    st = await compute_first_run_state(db, str(current_user.id), has_diagnosis=bool(feed))

    return FirstRunView(
        state=st.state,
        finance_days=st.finance_days,
        product_snapshots=st.product_snapshots,
        has_products=st.has_products,
        has_returns=st.has_returns,
        missing=list(st.missing),
    )


@router.get("/today/summary", response_model=TodaySummaryView)
async def get_today_summary(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> TodaySummaryView:
    """Assemble the daily business state from already-computed sources. Read-only — no
    DB write, no new computation beyond counting ready lists. Money: get_daily_summary
    (verbatim). Loss count: get_loss_products(days=1). Feed counts: build_feed filtered
    by priority_level/contour."""
    from services.finance_aggregator import get_daily_summary, get_loss_products

    ds = await get_daily_summary(current_user.id)
    loss = await get_loss_products(current_user.id, days=1, limit=10000)
    feed = await build_feed(db, user_id=current_user.id, limit=10000)

    critical = sum(1 for i in feed if i._priority_bucket == "critical")
    growth = sum(1 for i in feed if i.contour == "growth")
    low_stock = sum(1 for i in feed if i.contour == "operations")

    return TodaySummaryView(
        revenue_today=ds.data.revenue,
        profit_today=ds.data.effective_profit,
        margin_pct=ds.data.margin_pct,
        delta_revenue_pct=ds.delta_revenue_pct,
        critical_count=critical,
        loss_products_count=len(loss),
        growth_opportunities_count=growth,
        low_stock_count=low_stock,
        is_demo=ds.is_demo,
        has_data=ds.has_data,
    )
