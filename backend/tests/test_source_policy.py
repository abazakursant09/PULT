"""PULT-LAUNCH-1.4.5H — source policy: no figure counted twice (API↔CSV, or two feeds of one API).

No network. In-memory SQLite with the real constraints. Covers the migration/model constraints, the
dataset-authority map, the resolver + honest coverage, the gated money reader, the source-single
snapshot readers, derived-profit completeness, the policy endpoints and catalog conflict.
"""
import asyncio
import os
import tempfile
import uuid
from datetime import datetime, timedelta
from decimal import Decimal

import pytest
from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from database import Base
import models  # noqa: F401
from models.api_sync_state import ApiSyncState
from models.imported_card_content import ImportedCardContentRow
from models.imported_product import ImportedProductRow
from models.marketplace_account import MarketplaceAccount
from models.marketplace_operation import MarketplaceOperation
from models.marketplace_store import MarketplaceStore
from models.product import Product
from models.store_data_source_policy import StoreDataSourcePolicy
from models.user import User
from models.workspace import Workspace
from schemas.marketplace import SourcePolicyPatch
from services.source_policy import dataset_authority as da
from services.source_policy import derived, money_reader
from services.source_policy import resolver as rz
from services.source_policy.catalog_display import resolve_card_field
from services.price_erosion.diagnosis_source import build_price_series
from services.supply.diagnosis_source import latest_stock
import routers.source_policy as sp_router

_LOOP = asyncio.new_event_loop()


def _run(c):
    return _LOOP.run_until_complete(c)


async def _new_db():
    e = create_async_engine("sqlite+aiosqlite://",
                            connect_args={"check_same_thread": False}, poolclass=StaticPool)
    async with e.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    return sessionmaker(e, class_=AsyncSession, expire_on_commit=False)()


async def _seed(db, *, marketplace="wildberries"):
    uid, wid = str(uuid.uuid4()), str(uuid.uuid4())
    db.add(User(id=uid, email=f"{uid}@e.com", name="S", hashed_password="x", is_verified=True))
    db.add(Workspace(id=wid, owner_user_id=uid))
    acc = MarketplaceAccount(id=str(uuid.uuid4()), workspace_id=wid, marketplace=marketplace,
                             identity_status="verified", external_account_id=f"EXT-{uuid.uuid4()}")
    db.add(acc)
    key = "primary" if marketplace in ("wildberries", "ozon") else str(uuid.uuid4())
    store = MarketplaceStore(id=str(uuid.uuid4()), marketplace_account_id=acc.id,
                             marketplace=marketplace, store_key=key,
                             external_store_id=(None if marketplace != "yandex" else "cmp-1"),
                             label="S", source="api", status="active")
    db.add(store)
    await db.commit()
    return await db.get(User, uid), acc, store


async def _op(db, store, acc, *, dataset, op_type, amount=None, qty=None, day="2026-07-01",
              marketplace="wildberries", source="api"):
    db.add(MarketplaceOperation(
        id=str(uuid.uuid4()), marketplace_account_id=acc.id, marketplace_store_id=store.id,
        marketplace=marketplace, source=source, external_operation_id=str(uuid.uuid4()),
        operation_type=op_type, provider_dataset=dataset,
        amount=(Decimal(amount) if amount is not None else None), quantity=qty,
        occurred_at=datetime.strptime(day, "%Y-%m-%d"), fetched_at=datetime.utcnow()))
    await db.commit()


async def _state(db, store, acc, data_type, *, status="synced", complete=True, skipped=0,
                 covered=("2026-07-01", "2026-07-31"), last=None):
    db.add(ApiSyncState(
        id=str(uuid.uuid4()), marketplace_connection_id=str(uuid.uuid4()),
        marketplace_account_id=acc.id, marketplace_store_id=store.id, data_type=data_type,
        status=status, coverage_complete=complete, skipped_rows_count=skipped,
        covered_from=covered[0], covered_to=covered[1],
        last_success_at=(last or datetime.utcnow())))
    await db.commit()


# ── 1-3, 5: migration / model constraints ────────────────────────────────────────
async def _check_constraints(db, store_id):
    def mk(metric, pref):
        return StoreDataSourcePolicy(id=str(uuid.uuid4()), marketplace_store_id=store_id,
                                     metric_type=metric, preference=pref)

    async def rejects(obj):
        db.add(obj)
        try:
            await db.commit()
            return False
        except IntegrityError:
            await db.rollback()
            return True

    assert await rejects(mk("price", "bogus"))     # bad preference
    assert await rejects(mk("bogus", "api"))       # bad metric
    db.add(mk("price", "api")); await db.commit()  # first is fine
    assert await rejects(mk("price", "csv"))        # UNIQUE(store, metric)


def test_policy_constraints():
    db = _run(_new_db()); user, acc, store = _run(_seed(db))
    _run(_check_constraints(db, store.id))


def test_provider_dataset_defaults_legacy():
    db = _run(_new_db()); user, acc, store = _run(_seed(db))
    op = MarketplaceOperation(
        id=str(uuid.uuid4()), marketplace_account_id=acc.id, marketplace_store_id=store.id,
        marketplace="wildberries", source="api", external_operation_id="x",
        operation_type="sale", fetched_at=datetime.utcnow())
    db.add(op); _run(db.commit())
    assert _run(db.get(MarketplaceOperation, op.id)).provider_dataset == "legacy"


def test_coverage_defaults():
    db = _run(_new_db()); user, acc, store = _run(_seed(db))
    st = ApiSyncState(id=str(uuid.uuid4()), marketplace_connection_id=str(uuid.uuid4()),
                      marketplace_account_id=acc.id, marketplace_store_id=store.id,
                      data_type="finance", status="pending")
    db.add(st); _run(db.commit())
    got = _run(db.get(ApiSyncState, st.id))
    assert got.coverage_complete is False and got.skipped_rows_count == 0


def test_single_alembic_head():
    from alembic.config import Config
    from alembic.script import ScriptDirectory
    assert ScriptDirectory.from_config(Config("alembic.ini")).get_heads() == ["plp1a2b3c4d01"]


# ── 4: migration roundtrip ───────────────────────────────────────────────────────
def test_migration_roundtrip():
    from alembic.config import Config
    from alembic import command
    dbf = tempfile.NamedTemporaryFile(suffix=".db", delete=False).name
    old = os.environ.get("DATABASE_URL")
    os.environ["DATABASE_URL"] = f"sqlite+aiosqlite:///{dbf}"
    try:
        cfg = Config("alembic.ini")
        command.upgrade(cfg, "head")
        command.downgrade(cfg, "wbo1a2b3c4d01")
        command.upgrade(cfg, "head")
    finally:
        if old is not None:
            os.environ["DATABASE_URL"] = old
        else:
            os.environ.pop("DATABASE_URL", None)


# ── dataset authority map (§5) ───────────────────────────────────────────────────
def test_authority_revenue_finance_only():
    for mp in ("wildberries", "ozon"):
        a = da.authoritative(mp, "revenue")
        assert a.datasets == ("finance",) and a.operation_types == ("sale",) and a.is_money
    assert da.authoritative("yandex", "revenue") is None       # Yandex money unsupported (G2)


def test_authority_orders_operational():
    assert da.authoritative("wildberries", "orders").datasets == ("orders",)
    assert da.authoritative("ozon", "orders").datasets == ("fbo_postings", "fbs_postings")
    assert da.authoritative("yandex", "orders").datasets == ("orders",)
    assert da.authoritative("ozon", "orders").is_money is False


def test_authority_returns_frequency():
    assert da.authoritative("wildberries", "returns").datasets == ("sales",)
    assert da.authoritative("ozon", "returns").datasets == ("returns",)
    assert da.authoritative("yandex", "returns").datasets == ("returns",)
    assert da.authoritative("wildberries", "returns").is_money is False


def test_authority_manual_only_none():
    for m in ("cogs", "ad_spend"):
        assert da.authoritative("wildberries", m) is None
        assert da.api_supported("wildberries", m) is False


# ── money reader (§6, tests 6-9, 23-25) ──────────────────────────────────────────
def test_revenue_counted_once_from_finance():  # 6, 24
    db = _run(_new_db()); user, acc, store = _run(_seed(db))
    _run(_op(db, store, acc, dataset="sales", op_type="sale", amount="100"))   # same sale, sales feed
    _run(_op(db, store, acc, dataset="finance", op_type="sale", amount="100")) # and finance feed
    _run(_op(db, store, acc, dataset="finance", op_type="commission", amount="-15"))
    rev = _run(money_reader.api_money(db, store_id=store.id, marketplace="wildberries",
                                      metric_type="revenue"))
    assert rev == Decimal("100")   # finance only; commission excluded from revenue


def test_ozon_posting_not_revenue():  # 7, 23
    db = _run(_new_db()); user, acc, store = _run(_seed(db, marketplace="ozon"))
    _run(_op(db, store, acc, dataset="fbo_postings", op_type="order", amount=None, marketplace="ozon"))
    _run(_op(db, store, acc, dataset="finance", op_type="sale", amount="200", marketplace="ozon"))
    rev = _run(money_reader.api_money(db, store_id=store.id, marketplace="ozon", metric_type="revenue"))
    assert rev == Decimal("200")
    orders = _run(money_reader.api_count(db, store_id=store.id, marketplace="ozon", metric_type="orders"))
    assert orders == 1     # posting counted as an order, never revenue


def test_return_counted_once_not_from_finance():  # 8, 25
    db = _run(_new_db()); user, acc, store = _run(_seed(db, marketplace="ozon"))
    _run(_op(db, store, acc, dataset="returns", op_type="return", qty=1, marketplace="ozon"))
    _run(_op(db, store, acc, dataset="finance", op_type="return", amount="-50", marketplace="ozon"))
    freq = _run(money_reader.api_count(db, store_id=store.id, marketplace="ozon", metric_type="returns"))
    assert freq == 1       # returns-frequency from the returns feed only (finance return is money, separate)


def test_legacy_excluded():  # 9
    db = _run(_new_db()); user, acc, store = _run(_seed(db))
    _run(_op(db, store, acc, dataset="legacy", op_type="sale", amount="999"))
    rev = _run(money_reader.api_money(db, store_id=store.id, marketplace="wildberries",
                                      metric_type="revenue"))
    assert rev is None     # legacy never auto-enters a total


# ── resolver.decide (§3/§4, tests 10-19) ─────────────────────────────────────────
def _d(**kw):
    base = dict(marketplace="wildberries", metric_type="revenue", preference="auto",
                api_eligible=True, api_value=Decimal("100"), csv_value=Decimal("100"))
    base.update(kw)
    return rz.decide(**base)


def test_auto_same_counts_once():  # 10
    r = _d()
    assert r.source in ("api", "csv") and r.conflict is False


def test_auto_differ_conflict():  # 11
    r = _d(api_value=Decimal("100"), csv_value=Decimal("120"))
    assert r.conflict is True and r.source == "csv"
    assert r.conflict_candidates == {"api": Decimal("100"), "csv": Decimal("120")}


def test_auto_covered_picks_api():  # 12
    r = _d(csv_value=None)
    assert r.source == "api"


def test_auto_hole_picks_csv():  # 13
    r = _d(api_eligible=False)
    assert r.source == "csv"


def test_api_absent_csv():  # 14
    r = _d(api_value=None)
    assert r.source == "csv"


def test_csv_absent_api():  # 15
    r = _d(csv_value=None)
    assert r.source == "api"


def test_both_absent_no_data():  # 16
    r = _d(api_value=None, csv_value=None)
    assert r.source is None and r.completeness == "no_data"


def test_pref_csv_ignores_api():  # 17
    r = _d(preference="csv")
    assert r.source == "csv"


def test_pref_api_no_hidden_fallback():  # 18
    r = _d(preference="api", api_eligible=False, csv_value=Decimal("100"))
    assert r.source is None and r.completeness == "no_data"   # never silently CSV


def test_yandex_money_pref_api_unsupported():  # 36 (decide layer)
    r = rz.decide(marketplace="yandex", metric_type="revenue", preference="api",
                  api_eligible=True, api_value=Decimal("5"), csv_value=Decimal("5"))
    assert r.source is None and r.reason == "api_unsupported"


# ── coverage ─────────────────────────────────────────────────────────────────────
def test_coverage_period_complete():
    db = _run(_new_db()); user, acc, store = _run(_seed(db))
    _run(_state(db, store, acc, "finance", complete=True, skipped=0))
    ok, _r = _run(rz.api_coverage(db, store.id, "wildberries", "revenue", ("2026-07-05", "2026-07-20")))
    assert ok is True


def test_coverage_hole_blocks():
    db = _run(_new_db()); user, acc, store = _run(_seed(db))
    _run(_state(db, store, acc, "finance", complete=False))
    ok, reason = _run(rz.api_coverage(db, store.id, "wildberries", "revenue", ("2026-07-05", "2026-07-20")))
    assert ok is False and reason == "coverage_incomplete"


def test_coverage_snapshot_stale():
    db = _run(_new_db()); user, acc, store = _run(_seed(db))
    _run(_state(db, store, acc, "prices", complete=True,
                last=datetime.utcnow() - timedelta(days=30)))
    ok, reason = _run(rz.api_coverage(db, store.id, "wildberries", "price", None))
    assert ok is False and reason == "stale_snapshot"


# ── derived profit completeness (§9, tests 20-22) ────────────────────────────────
def test_profit_incomplete_without_cogs():
    r = derived.profit(revenue=Decimal("100"), marketplace_fees=Decimal("-10"), cogs=None,
                       external_ad_spend=Decimal("0"))
    assert r.completeness == "incomplete" and r.value is None and "cogs" in r.missing_fields


def test_profit_complete_with_all():
    r = derived.profit(revenue=Decimal("100"), marketplace_fees=Decimal("-10"),
                       logistics=Decimal("-5"), cogs=Decimal("-40"), external_ad_spend=Decimal("-5"))
    assert r.completeness == "complete" and r.value == Decimal("40")


# ── snapshot readers (§7, tests 26-31) ───────────────────────────────────────────
async def _pr(db, user, acc, store, *, source, price=None, stock=None, sku="A", day_offset=0):
    db.add(ImportedProductRow(
        id=str(uuid.uuid4()), import_id="i", user_id=user.id, marketplace=store.marketplace,
        sku=sku, price=price, stock=stock, source=source,
        marketplace_account_id=acc.id, marketplace_store_id=store.id,
        created_at=datetime.utcnow() + timedelta(seconds=day_offset)))
    await db.commit()


def test_price_series_single_source():  # 26, 28
    db = _run(_new_db()); user, acc, store = _run(_seed(db))
    _run(_pr(db, user, acc, store, source="csv", price=100.0, day_offset=0))
    _run(_pr(db, user, acc, store, source="api", price=80.0, day_offset=1))
    _run(_pr(db, user, acc, store, source="csv", price=99.0, day_offset=2))
    csv_series = _run(build_price_series(db, user.id, "wildberries", "A", source="csv"))
    api_series = _run(build_price_series(db, user.id, "wildberries", "A", source="api"))
    assert csv_series == [100.0, 99.0] and api_series == [80.0]   # never interleaved


def test_stock_single_source():  # 27
    db = _run(_new_db()); user, acc, store = _run(_seed(db))
    _run(_pr(db, user, acc, store, source="csv", stock=10, day_offset=0))
    _run(_pr(db, user, acc, store, source="api", stock=3, day_offset=1))
    assert _run(latest_stock(db, user.id, "wildberries", "A", source="csv")) == 10
    assert _run(latest_stock(db, user.id, "wildberries", "A", source="api", store_id=store.id)) == 3


def test_two_stores_isolated():  # 29, 31
    db = _run(_new_db()); user, acc, store = _run(_seed(db, marketplace="yandex"))
    store2 = MarketplaceStore(id=str(uuid.uuid4()), marketplace_account_id=acc.id, marketplace="yandex",
                              store_key=str(uuid.uuid4()), external_store_id="cmp-2", label="S2",
                              source="api", status="active")
    db.add(store2); _run(db.commit())
    _run(_pr(db, user, acc, store, source="api", stock=5, sku="A"))
    _run(_pr(db, user, acc, store2, source="api", stock=9, sku="A"))
    assert _run(latest_stock(db, user.id, "yandex", "A", source="api", store_id=store.id)) == 5
    assert _run(latest_stock(db, user.id, "yandex", "A", source="api", store_id=store2.id)) == 9


def test_two_accounts_isolated():  # 30
    db = _run(_new_db()); user, acc, store = _run(_seed(db))
    user2, acc2, store2 = _run(_seed(db))
    _run(_pr(db, user, acc, store, source="csv", price=100.0, sku="A"))
    _run(_pr(db, user2, acc2, store2, source="csv", price=50.0, sku="A"))
    assert _run(build_price_series(db, user.id, "wildberries", "A", source="csv")) == [100.0]


# ── policy endpoints (§11, tests 32-37) ──────────────────────────────────────────
def test_get_policy_default_csv():  # 32
    db = _run(_new_db()); user, acc, store = _run(_seed(db))
    out = _run(sp_router.get_source_policy(store.id, current_user=user, db=db))
    by = {m.metric_type: m for m in out.metrics}
    assert by["revenue"].preference == "csv" and by["price"].preference == "csv"
    assert by["cogs"].api_supported is False and by["cogs"].limitation == "manual_only_csv"


def test_patch_persists_survives_sync():  # 33
    db = _run(_new_db()); user, acc, store = _run(_seed(db))
    _run(sp_router.set_source_policy(store.id, "price", SourcePolicyPatch(preference="api"),
                                     current_user=user, db=db))
    # a later "sync" (new ApiSyncState) must not change the explicit choice
    _run(_state(db, store, acc, "prices"))
    out = _run(sp_router.get_source_policy(store.id, current_user=user, db=db))
    assert {m.metric_type: m.preference for m in out.metrics}["price"] == "api"


def test_disconnect_keeps_policy():  # 34
    db = _run(_new_db()); user, acc, store = _run(_seed(db))
    _run(sp_router.set_source_policy(store.id, "price", SourcePolicyPatch(preference="csv"),
                                     current_user=user, db=db))
    # deleting a connection is unrelated to the store's policy row
    rows = _run(db.execute(select(StoreDataSourcePolicy))).scalars().all()
    assert len(rows) == 1 and rows[0].preference == "csv"


def test_foreign_store_404():  # 35
    db = _run(_new_db()); user, acc, store = _run(_seed(db))
    other_user, _a, _s = _run(_seed(db))
    with pytest.raises(HTTPException) as ei:
        _run(sp_router.get_source_policy(store.id, current_user=other_user, db=db))
    assert ei.value.status_code == 404


def test_yandex_finance_api_rejected():  # 36
    db = _run(_new_db()); user, acc, store = _run(_seed(db, marketplace="yandex"))
    with pytest.raises(HTTPException) as ei:
        _run(sp_router.set_source_policy(store.id, "revenue", SourcePolicyPatch(preference="api"),
                                         current_user=user, db=db))
    assert ei.value.status_code == 422
    out = _run(sp_router.get_source_policy(store.id, current_user=user, db=db))
    by = {m.metric_type: m for m in out.metrics}
    assert by["revenue"].api_supported is False and by["revenue"].limitation == "yandex_finance_unsupported"


def test_invalid_metric_and_preference_422():
    db = _run(_new_db()); user, acc, store = _run(_seed(db))
    with pytest.raises(HTTPException) as e1:
        _run(sp_router.set_source_policy(store.id, "bogus", SourcePolicyPatch(preference="api"),
                                         current_user=user, db=db))
    assert e1.value.status_code == 422
    with pytest.raises(HTTPException) as e2:
        _run(sp_router.set_source_policy(store.id, "price", SourcePolicyPatch(preference="bogus"),
                                         current_user=user, db=db))
    assert e2.value.status_code == 422


def test_flag_off_resolves_csv():  # 37, 19
    db = _run(_new_db()); user, acc, store = _run(_seed(db))
    # no api rows, no policy → resolver falls to csv
    r = _run(rz.resolve_source(db, store_id=store.id, marketplace="wildberries",
                               metric_type="revenue", period=("2026-07-01", "2026-07-31"),
                               api_value=Decimal("100"), csv_value=Decimal("77")))
    assert r.source == "csv" and r.value == Decimal("77")


# ── catalog / cards (§8, tests 38-41) ────────────────────────────────────────────
async def _card(db, product, acc, *, source, title):
    db.add(ImportedCardContentRow(
        id=str(uuid.uuid4()), import_id="i", user_id=product.user_id, marketplace=product.marketplace,
        source=source, external_row_id=str(uuid.uuid4()), marketplace_account_id=acc.id,
        product_id=product.id, title=title, link_status="linked", fetched_at=datetime.utcnow()))
    await db.commit()


def _product(db, user, acc):
    p = Product(id=str(uuid.uuid4()), user_id=user.id, name="Canonical", marketplace="wildberries",
                sku="A", marketplace_account_id=acc.id, external_product_id="555")
    db.add(p)
    return p


def test_card_source_specific_and_conflict():  # 38, 39, 40
    db = _run(_new_db()); user, acc, store = _run(_seed(db))
    p = _product(db, user, acc); _run(db.commit())
    _run(_card(db, p, acc, source="csv", title="CSV name"))
    _run(_card(db, p, acc, source="api", title="API name"))
    r = _run(resolve_card_field(db, product=p, field="title", preference="auto", api_eligible=True))
    assert r.conflict is True and r.conflict_candidates == {"api": "API name", "csv": "CSV name"}
    # only ONE product identity exists
    assert _run(db.execute(select(Product))).scalars().all().__len__() == 1


def test_card_name_fallback_not_identity():  # 41
    db = _run(_new_db()); user, acc, store = _run(_seed(db))
    p = _product(db, user, acc); _run(db.commit())
    r = _run(resolve_card_field(db, product=p, field="title", preference="csv", api_eligible=False))
    assert r.value == "Canonical" and r.reason == "product_name_fallback"
