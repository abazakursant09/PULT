"""Exact labels must describe existing windows, never change their money or selection."""
import asyncio
from contextlib import asynccontextmanager
from datetime import date, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

import services.finance_aggregator as fa
import routers.today as today_router


@pytest.mark.parametrize("anchor", [date(2026, 10, 30), date(2027, 1, 1), date(2028, 3, 1)])
@pytest.mark.parametrize("mode", ["yesterday", "today", "range", "empty"])
def test_selected_window_and_comparison_are_reported_without_changing_queries(monkeypatch, anchor, mode):
    class FrozenDate(date):
        @classmethod
        def today(cls):
            return anchor

    @asynccontextmanager
    async def session():
        yield object()

    monkeypatch.setattr(fa, "date", FrozenDate)
    monkeypatch.setattr(fa, "AsyncSessionLocal", session)
    monkeypatch.setattr(fa, "_has_any_data", AsyncMock(return_value=True))
    monkeypatch.setattr(fa, "_top_products", AsyncMock(return_value=[]))
    monkeypatch.setattr(fa, "_avg_rating", AsyncMock(return_value=None))
    monkeypatch.setattr(fa, "_active_products_count", AsyncMock(return_value=0))
    yesterday = (anchor - timedelta(days=1)).isoformat()
    previous = (anchor - timedelta(days=2)).isoformat()
    current = anchor.isoformat()
    start = (anchor - timedelta(days=30)).isoformat()
    selected = {"yesterday": (yesterday, yesterday), "today": (current, current),
                "range": (start, current), "empty": None}[mode]
    calls = []

    async def aggregate(user_id, first, last, db):
        calls.append((first, last))
        if (first, last) == selected:
            return fa.FinancePeriod(revenue=100, profit=30, orders=2)
        if mode == "yesterday" and first == previous:
            return fa.FinancePeriod(revenue=80, profit=20, orders=1)
        return fa.FinancePeriod()

    monkeypatch.setattr(fa, "_agg_period", aggregate)
    result = asyncio.run(fa.get_daily_summary("synthetic"))
    expected_calls = [(yesterday, yesterday)]
    if mode == "yesterday":
        expected_calls.append((previous, previous))
    else:
        expected_calls.append((current, current))
        if mode != "today":
            expected_calls.append((start, current))
    assert calls == expected_calls
    assert (result.period_from, result.period_to) == (selected or (None, None))
    assert result.is_partial is (mode in ("today", "range"))
    comparison = (previous, previous) if mode == "yesterday" else (None, None)
    assert (result.comparison_from, result.comparison_to) == comparison
    assert result.delta_revenue_pct == (25 if mode == "yesterday" else None)
    assert result.data.revenue == (0 if mode == "empty" else 100)
    assert result.data.effective_profit == (0 if mode == "empty" else 30)
    if mode == "range":
        assert (date.fromisoformat(result.period_to) - date.fromisoformat(result.period_from)).days == 30
        # Inclusive SQL range intentionally stays31dates; do not silently shorten it.


def test_no_records_has_no_guessed_period(monkeypatch):
    @asynccontextmanager
    async def session():
        yield object()
    monkeypatch.setattr(fa, "AsyncSessionLocal", session)
    monkeypatch.setattr(fa, "_has_any_data", AsyncMock(return_value=False))
    result = asyncio.run(fa.get_daily_summary("synthetic"))
    assert result.period_from is None and result.period_to is None
    assert result.comparison_from is None and result.comparison_to is None
    assert result.is_partial is False


@pytest.mark.parametrize("money", [0, -50, None])
def test_today_api_forwards_metadata_and_money_verbatim(monkeypatch, money):
    summary = SimpleNamespace(data=fa.FinancePeriod(revenue=money, profit=money, profit_resolved=True),
        period_from="2026-10-29", period_to="2026-10-29", comparison_from="2026-10-28",
        comparison_to="2026-10-28", is_partial=False, delta_revenue_pct=None, is_demo=False, has_data=True)
    monkeypatch.setattr(fa, "get_daily_summary", AsyncMock(return_value=summary))
    monkeypatch.setattr(fa, "get_loss_products", AsyncMock(return_value=[]))
    monkeypatch.setattr(today_router, "build_feed", AsyncMock(return_value=[]))
    result = asyncio.run(today_router.get_today_summary(current_user=SimpleNamespace(id="synthetic"), db=object()))
    assert result.revenue_today == money and result.profit_today == money
    assert result.period_from == "2026-10-29" and result.period_to == "2026-10-29"
    assert result.comparison_from == "2026-10-28" and result.comparison_to == "2026-10-28"
    assert result.is_partial is False
