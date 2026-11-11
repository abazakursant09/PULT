"""Network-free WB finance HOLD regressions; no ORM schema or real credentials."""

import asyncio
from datetime import datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from services.marketplace import ingest_read_guard
from services.marketplace.ingest import wb
from tasks import api_sync


REASON = "wb_finance_release_hold"
TOKEN = "synthetic-token-not-a-credential"
NOW = datetime(2026, 11, 11, 12, 0)
PRESERVED = (
    "cursor", "covered_from", "covered_to", "coverage_complete",
    "skipped_rows_count", "last_success_at", "fail_count",
)


class NoAccess:
    def __getattr__(self, name):
        pytest.fail(f"Held WB finance must not access DB/client attribute: {name}")


class FakeDB:
    def __init__(self, objects=None):
        self.objects = objects or {}
        self.commits = 0
        self.rollbacks = 0

    async def commit(self):
        self.commits += 1

    async def rollback(self):
        self.rollbacks += 1

    async def get(self, model, key):
        assert (model, key) in self.objects, "Unexpected fake DB lookup"
        return self.objects[(model, key)]


def state(data_type="finance", **overrides):
    values = dict(
        id=f"synthetic-{data_type}", data_type=data_type, status="pending",
        marketplace_connection_id="connection", marketplace_account_id="account",
        marketplace_store_id="store", cursor='{"from":"2026-11-01"}',
        covered_from="2026-11-01", covered_to="2026-11-06",
        coverage_complete=False, skipped_rows_count=2, fail_count=3,
        last_success_at=None, last_attempt_at=None, next_run_at=None,
        last_safe_error_code="previous_failure",
    )
    values.update(overrides)
    return SimpleNamespace(**values)


def preserved(value):
    return {name: getattr(value, name) for name in PRESERVED}


@pytest.fixture(autouse=True)
def isolated_ingest(monkeypatch):
    monkeypatch.setattr(wb, "wb_client", NoAccess())
    monkeypatch.setattr(ingest_read_guard, "settings", SimpleNamespace(marketplace_read_only=True))
    monkeypatch.setattr(api_sync, "settings", SimpleNamespace(api_data_sync_enabled=True))


def assert_parked(value, before, now):
    assert value.status == "unsupported"
    assert value.last_safe_error_code == REASON
    assert value.last_attempt_at == now
    assert value.next_run_at == now + timedelta(minutes=api_sync._PARKED_RECHECK_MIN)
    assert preserved(value) == before


def test_wb_declares_only_finance_held_without_removing_dataset():
    assert isinstance(wb.UNSUPPORTED, frozenset)
    assert wb.UNSUPPORTED == frozenset({"finance"})
    assert wb.UNSUPPORTED_REASON == REASON
    assert {"card_content", "prices", "orders", "sales", "stocks", "finance",
            "price_observations", "promotions"}.issubset(wb.DATA_TYPES)


def test_direct_finance_hold_returns_marker_without_db_client_or_state_changes():
    value = state()
    before = vars(value).copy()

    result = asyncio.run(wb.fetch_and_persist_page(NoAccess(), value, TOKEN))

    assert result == {"done": False, "count": 0, "defer": False, "unsupported": True}
    assert vars(value) == before


def test_sibling_prices_still_dispatches_to_existing_page_driver(monkeypatch):
    value, db = state("prices"), NoAccess()
    expected = {"done": True, "count": 1, "defer": False}
    prices = AsyncMock(return_value=expected)
    monkeypatch.setattr(wb, "_page_prices", prices)

    assert asyncio.run(wb.fetch_and_persist_page(db, value, TOKEN)) == expected
    prices.assert_awaited_once_with(db, value, TOKEN)


@pytest.mark.parametrize("done", [False, True], ids=["held", "legacy-done-true"])
def test_sync_state_parks_marker_before_success_or_failure_resets(monkeypatch, done):
    value, db = state(), FakeDB()
    before = preserved(value)
    provider = SimpleNamespace(
        UNSUPPORTED_REASON=REASON,
        fetch_and_persist_page=AsyncMock(return_value={
            "unsupported": True, "done": done, "count": 0, "defer": False,
        }),
    )
    park = Mock(wraps=api_sync._park)
    monkeypatch.setattr(api_sync, "_park", park)

    asyncio.run(api_sync._sync_state(db, value, provider, TOKEN, None, NOW))

    provider.fetch_and_persist_page.assert_awaited_once_with(db, value, TOKEN, None)
    park.assert_called_once_with(value, "unsupported", REASON, NOW)
    assert_parked(value, before, NOW)
    assert value.last_success_at is None
    assert db.commits > 0 and db.rollbacks == 0


def test_sync_state_actual_wb_hold_preserves_existing_success_and_coverage():
    value = state(last_success_at=NOW - timedelta(days=1), coverage_complete=True)
    before, db = preserved(value), FakeDB()

    asyncio.run(api_sync._sync_state(db, value, wb, TOKEN, None, NOW))

    assert_parked(value, before, NOW)
    assert db.rollbacks == 0


def test_enabled_scheduler_parks_finance_without_provider_call_and_syncs_prices(monkeypatch):
    finance, prices = state(), state("prices")
    before = preserved(finance)
    connection = SimpleNamespace(id="connection", user_id="synthetic-owner",
        marketplace="wildberries", marketplace_account_id="account",
        ozon_client_id=None, status="verified")
    store, account = SimpleNamespace(id="store"), SimpleNamespace(id="account")
    db = FakeDB({
        (api_sync.MarketplaceStore, "store"): store,
        (api_sync.MarketplaceAccount, "account"): account,
        (api_sync.ApiSyncState, finance.id): finance,
        (api_sync.ApiSyncState, prices.id): prices,
    })
    eligible = AsyncMock(return_value=[connection])
    token = AsyncMock(return_value=TOKEN)
    ensure = AsyncMock(return_value=[finance, prices])
    monkeypatch.setattr(api_sync, "_eligible_connections", eligible)
    monkeypatch.setattr(api_sync, "_active_stores", AsyncMock(return_value=[store]))
    monkeypatch.setattr(api_sync, "_token_for", token)
    monkeypatch.setattr(api_sync, "_ensure_states", ensure)
    monkeypatch.setattr(api_sync, "_PROVIDERS", {"wildberries": wb})
    price_page = AsyncMock(return_value={"done": True, "count": 1, "defer": False})
    monkeypatch.setattr(wb, "_page_prices", price_page)
    provider_page = AsyncMock(wraps=wb.fetch_and_persist_page)
    monkeypatch.setattr(wb, "fetch_and_persist_page", provider_page)

    result = asyncio.run(api_sync.run_api_sync_once(db))

    assert result == {"enabled": True, "connections": 1, "states_touched": 1}
    eligible.assert_awaited_once_with(db)
    token.assert_awaited_once_with(db, "connection")
    ensure.assert_awaited_once_with(db, "connection", "account", "store", wb)
    provider_page.assert_awaited_once_with(db, prices, TOKEN, None)
    price_page.assert_awaited_once_with(db, prices, TOKEN)
    assert_parked(finance, before, finance.last_attempt_at)
    assert finance.last_success_at is None
    assert prices.status == "synced" and prices.last_success_at is not None
    assert prices.fail_count == 0
    assert prices._owner_user_id == finance._owner_user_id == "synthetic-owner"
    assert db.rollbacks == 0
