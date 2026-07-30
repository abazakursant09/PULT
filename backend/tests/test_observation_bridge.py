"""PULT-LAUNCH-2.5F-B — read-only observations→protection-evaluation bridge. Feature OFF; advisory-only.

Proves:
  * BOTH flags gate the bridge — either off ⇒ ZERO observation SELECTs, unchanged CSV path;
  * total isolation — account/store/product + resolved + source='api'; unassigned never used; a second
    external_product_id ⇒ conflict (never the first);
  * current selection is deterministic (fetched_at, created_at, id) and catalog/promotion are separate;
  * a promotion counts only when active + in-window + store-attributed; expired/future/inactive don't;
    several differing active promotions ⇒ conflict;
  * Yandex account-wide promo is never attributed to a store; an exact-store mapped campaign is;
  * freshness is judged on last_verified_at; no threshold ⇒ 'unknown'; past threshold ⇒ stale fallback;
  * a proven currency removes the currency_unconfirmed conflict; a buyer price is never seller revenue;
    seller_revenue / commission / subsidy stay unknown; NULL is never 0;
  * the evaluation is NEVER executable and makes 0 executor/provider calls; the snapshot is self-contained;
  * the resolver is structurally read-only (no writes, no provider/executor/scheduler import).
"""
import asyncio
import inspect
import uuid
from datetime import datetime, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from database import Base
import models  # noqa: F401 — register metadata
from models.protection import ProtectionPolicy, ProtectionActionState
from models.marketplace_account import MarketplaceAccount
from models.marketplace_store import MarketplaceStore
from models.product import Product
from models.product_placement import ProductPlacement
from models.product_listing import ProductListing
from models.physical_product import PhysicalProduct
from models.imported_product import ImportedProductRow
from models.imported_finance import ImportedFinanceRow
from models.marketplace_price_observation import MarketplacePriceObservation as MPO
from models.marketplace_promotion_observation import (
    MarketplacePromotionObservation as MPromo, MarketplacePromotionStoreEvidence as MPromoStore)
from models.execution_log import ExecutionLog

from config import settings
from services.protection import observation_resolver as orv
from services.protection.observation_resolver import (
    resolve_current_observation, FOUND, MISSING, STALE, CONFLICT, UNSUPPORTED, CATALOG, PROMOTION)
from services.protection.evaluation import evaluate_policy_product

NOW = datetime(2026, 7, 26, 12, 0, 0)
D = Decimal
R1 = "11111111-1111-4111-8111-111111111111"


def _run(c):
    return asyncio.run(c)


async def _session():
    e = create_async_engine("sqlite+aiosqlite://",
                            connect_args={"check_same_thread": False}, poolclass=StaticPool)
    async with e.begin() as c:
        await c.run_sync(Base.metadata.create_all)
    return sessionmaker(e, class_=AsyncSession, expire_on_commit=False)()


async def _base(s, *, marketplace="wildberries", with_csv_price=True):
    uid = str(uuid.uuid4())
    s.add(models.user.User(id=uid, email=f"{uid}@x.c", name="A", hashed_password="x"))
    from models.workspace import Workspace
    s.add(Workspace(id="ws", owner_user_id=uid))
    s.add(MarketplaceAccount(id="acc", workspace_id="ws", marketplace=marketplace,
                             identity_status="unverified"))
    store_key = "primary" if marketplace in ("wildberries", "ozon") else "camp-1"   # yandex ≠ primary
    s.add(MarketplaceStore(id="s1", marketplace_account_id="acc", marketplace=marketplace,
                           store_key=store_key, label="S", status="active"))
    # NOTE: WB/Ozon allow exactly one store (store_key='primary' CHECK), so a second real store cannot be
    # seeded here. Store/account-isolation tests reference a non-seeded store/account id directly on the
    # observation (sqlite FK enforcement is off in these unit tests) — the resolver's WHERE excludes them.
    s.add(Product(id="p1", user_id=uid, name="N", marketplace=marketplace, sku="SKU1",
                  external_product_id="SKU1", marketplace_account_id="acc"))
    s.add(ProductPlacement(id="pp1", product_id="p1", marketplace_store_id="s1",
                           marketplace_account_id="acc", status="active"))
    s.add(PhysicalProduct(id="ph1", user_id=uid, title="t", cogs=D("300"), cogs_source="manual"))
    s.add(ProductListing(physical_product_id="ph1", user_id=uid, marketplace=marketplace, external_id="SKU1"))
    if with_csv_price:
        s.add(ImportedProductRow(id="ipr", import_id="imp", user_id=uid, marketplace=marketplace,
                                 sku="SKU1", price=1000.0, product_id="p1", marketplace_store_id="s1",
                                 source="csv", fetched_at=NOW))
        s.add(ImportedFinanceRow(id="fin", import_id="imp", user_id=uid, marketplace=marketplace,
                                 date="2026-07-01", sku="SKU1", revenue=1000.0, commission=150.0,
                                 logistics=100.0, quantity=1, product_id="p1", marketplace_store_id="s1",
                                 source="csv", fetched_at=NOW))
    s.add(ProtectionPolicy(id="pol1", marketplace_store_id="s1", product_id="p1", enabled=True,
                           target_margin_pct=D("10"), emergency_abs=D("0"), consent_at=NOW,
                           consent_version="v1"))
    await s.flush()
    return uid


def _price_obs(**kw):
    """Build a valid MarketplacePriceObservation with sane defaults for a resolved API catalog row."""
    base = dict(
        id=str(uuid.uuid4()), ingest_run_id=str(uuid.uuid4()),
        marketplace_account_id="acc", marketplace_store_id="s1", product_id="p1",
        external_product_id="SKU1", resolution_status="resolved",
        observation_kind="catalog", promotion_id=None, promotion_key="__none__",
        promotion_type=None, participation_status=None,
        catalog_price=D("900"), buyer_price=D("800"),
        currency="RUB", currency_status="proven",
        seller_revenue_status="unknown", commission_base_status="unknown", subsidy_status="unknown",
        source="api", fetched_at=NOW, last_verified_at=NOW, created_at=NOW,
        missing_fields=[],
    )
    base.update(kw)
    return MPO(**base)


def _enable(monkeypatch):
    monkeypatch.setattr(settings, "api_data_sync_enabled", True)
    monkeypatch.setattr(settings, "protection_use_observations", True)


# ── resolver: isolation & selection ────────────────────────────────────────────────────────────────
def test_resolver_missing_when_no_api_observation():
    async def go():
        s = await _session()
        await _base(s)
        await s.commit()
        r = await resolve_current_observation(
            s, marketplace_account_id="acc", marketplace_store_id="s1", product_id="p1",
            marketplace="wildberries", evaluated_at=NOW)
        return r
    r = _run(go())
    assert r.status == MISSING and r.candidate_buyer_price is None


def test_resolver_finds_latest_catalog_deterministic():
    async def go():
        s = await _session()
        await _base(s)
        s.add(_price_obs(catalog_price=D("900"), buyer_price=D("800"),
                         fetched_at=NOW - timedelta(days=2), last_verified_at=NOW - timedelta(days=2)))
        s.add(_price_obs(catalog_price=D("950"), buyer_price=D("850"),
                         fetched_at=NOW, last_verified_at=NOW))       # latest
        await s.commit()
        return await resolve_current_observation(
            s, marketplace_account_id="acc", marketplace_store_id="s1", product_id="p1",
            marketplace="wildberries", evaluated_at=NOW, freshness_threshold_seconds=3600)
    r = _run(go())
    assert r.status == FOUND and r.price_kind == CATALOG
    assert r.candidate_buyer_price == D("850") and r.buyer_price == D("850")
    assert r.currency == "RUB" and r.currency_proven is True


def test_resolver_excludes_other_store_and_account_and_unassigned():
    async def go():
        s = await _session()
        await _base(s)
        s.add(_price_obs(marketplace_store_id="s2", buyer_price=D("111")))       # other store (not seeded)
        s.add(_price_obs(marketplace_account_id="acc2", buyer_price=D("112")))   # other account (not seeded)
        s.add(_price_obs(product_id=None, resolution_status="unassigned", buyer_price=D("222")))  # unassigned
        await s.commit()
        return await resolve_current_observation(
            s, marketplace_account_id="acc", marketplace_store_id="s1", product_id="p1",
            marketplace="wildberries", evaluated_at=NOW)
    r = _run(go())
    assert r.status == MISSING            # nothing resolved for THIS store/product


def test_resolver_multiple_external_ids_is_conflict():
    async def go():
        s = await _session()
        await _base(s)
        s.add(_price_obs(external_product_id="SKU1", buyer_price=D("800")))
        s.add(_price_obs(external_product_id="SKU_OTHER", buyer_price=D("801")))
        await s.commit()
        return await resolve_current_observation(
            s, marketplace_account_id="acc", marketplace_store_id="s1", product_id="p1",
            marketplace="wildberries", evaluated_at=NOW)
    r = _run(go())
    assert r.status == CONFLICT and "multiple_external_ids" in r.conflict_reasons
    assert r.candidate_buyer_price is None


# ── resolver: promotion window / activity / conflict ────────────────────────────────────────────────
def _promo(**kw):
    base = dict(observation_kind="promotion", promotion_id="PR1", promotion_key="PR1",
                promotion_type="wb_calendar", participation_status="active",
                catalog_price=None, buyer_price=D("700"), seller_promo_price=None,
                provider_valid_from=NOW - timedelta(days=1), provider_valid_to=NOW + timedelta(days=1))
    base.update(kw)
    return _price_obs(**base)


def test_resolver_active_promotion_selected():
    async def go():
        s = await _session()
        await _base(s)
        s.add(_price_obs(buyer_price=D("850")))        # catalog
        s.add(_promo(buyer_price=D("700")))            # active promo in window
        await s.commit()
        return await resolve_current_observation(
            s, marketplace_account_id="acc", marketplace_store_id="s1", product_id="p1",
            marketplace="wildberries", evaluated_at=NOW, freshness_threshold_seconds=3600)
    r = _run(go())
    assert r.status == FOUND and r.price_kind == PROMOTION
    assert r.candidate_buyer_price == D("700") and r.promotion_price == D("700")
    assert r.catalog_price == D("900")             # catalog kept as evidence


@pytest.mark.parametrize("vf,vt,part", [
    (NOW + timedelta(days=1), NOW + timedelta(days=2), "active"),    # future
    (NOW - timedelta(days=2), NOW - timedelta(days=1), "active"),    # expired
    (NOW - timedelta(days=1), NOW + timedelta(days=1), "ended"),     # inactive
    (NOW - timedelta(days=1), NOW + timedelta(days=1), "not_participating"),
])
def test_resolver_promotion_excluded_when_not_current(vf, vt, part):
    async def go():
        s = await _session()
        await _base(s)
        s.add(_price_obs(buyer_price=D("850")))
        s.add(_promo(participation_status=part, provider_valid_from=vf, provider_valid_to=vt))
        await s.commit()
        return await resolve_current_observation(
            s, marketplace_account_id="acc", marketplace_store_id="s1", product_id="p1",
            marketplace="wildberries", evaluated_at=NOW, freshness_threshold_seconds=3600)
    r = _run(go())
    assert r.status == FOUND and r.price_kind == CATALOG      # falls back to catalog, promo not current


def test_resolver_multiple_active_promotions_is_conflict():
    async def go():
        s = await _session()
        await _base(s)
        s.add(_price_obs(buyer_price=D("850")))
        s.add(_promo(promotion_id="PR1", promotion_key="PR1", buyer_price=D("700")))
        s.add(_promo(promotion_id="PR2", promotion_key="PR2", buyer_price=D("650")))
        await s.commit()
        return await resolve_current_observation(
            s, marketplace_account_id="acc", marketplace_store_id="s1", product_id="p1",
            marketplace="wildberries", evaluated_at=NOW)
    r = _run(go())
    assert r.status == CONFLICT and "multiple_active_promotions" in r.conflict_reasons


# ── resolver: Yandex account-wide vs exact-store ────────────────────────────────────────────────────
def _yandex_base(s):
    return _base(s, marketplace="yandex")


def _add_yandex_promo(s, *, attribution, mapped_store=None):
    pid = str(uuid.uuid4())
    s.add(MPromo(id=pid, ingest_run_id=str(uuid.uuid4()), marketplace_account_id="acc",
                 marketplace="yandex", product_id="p1", external_product_id="SKU1",
                 resolution_status="resolved", promotion_id="YP1", promotion_type="yandex_promo",
                 provider_status=("PARTIALLY_AUTO" if attribution == "exact_stores" else "AUTO"),
                 participation_status="active", auto_participation=True, attribution_status=attribution,
                 promo_buyer_price=D("500"), currency="RUB", currency_status="proven",
                 source="api", provider_dataset="promos", fetched_at=NOW, last_verified_at=NOW, created_at=NOW,
                 missing_fields=[]))
    if mapped_store is not None:
        s.add(MPromoStore(id=str(uuid.uuid4()), promotion_observation_id=pid, marketplace_account_id="acc",
                          external_store_id="CAMP1", marketplace_store_id=mapped_store,
                          mapping_status="mapped", created_at=NOW))
    return pid


def test_resolver_yandex_account_wide_not_store_attributed():
    async def go():
        s = await _session()
        await _yandex_base(s)
        s.add(_price_obs(buyer_price=D("850"), currency="RUB", currency_status="proven"))
        _add_yandex_promo(s, attribution="account_wide")
        await s.commit()
        return await resolve_current_observation(
            s, marketplace_account_id="acc", marketplace_store_id="s1", product_id="p1",
            marketplace="yandex", evaluated_at=NOW, freshness_threshold_seconds=3600)
    r = _run(go())
    assert r.price_kind == CATALOG                 # promo NOT applied to the store
    assert r.promotion_evidence is not None
    assert r.promotion_evidence["store_attributed"] is False
    assert r.promotion_evidence["note"] == "account_wide_not_store_attributed"


def test_resolver_yandex_exact_store_evidence_present():
    async def go():
        s = await _session()
        await _yandex_base(s)
        s.add(_price_obs(buyer_price=D("850"), currency="RUB", currency_status="proven"))
        _add_yandex_promo(s, attribution="exact_stores", mapped_store="s1")
        await s.commit()
        return await resolve_current_observation(
            s, marketplace_account_id="acc", marketplace_store_id="s1", product_id="p1",
            marketplace="yandex", evaluated_at=NOW, freshness_threshold_seconds=3600)
    r = _run(go())
    assert r.promotion_evidence["store_attributed"] is True
    assert r.promotion_evidence["note"] == "exact_store_evidence"


# ── resolver: freshness / currency / unknown ────────────────────────────────────────────────────────
def test_resolver_freshness_uses_last_verified_at_not_fetched_at():
    async def go():
        s = await _session()
        await _base(s)
        # old change-point but freshly re-verified → NOT stale under a 1-day threshold
        s.add(_price_obs(fetched_at=NOW - timedelta(days=90), last_verified_at=NOW))
        await s.commit()
        return await resolve_current_observation(
            s, marketplace_account_id="acc", marketplace_store_id="s1", product_id="p1",
            marketplace="wildberries", evaluated_at=NOW, freshness_threshold_seconds=86400)
    r = _run(go())
    assert r.status == FOUND and r.freshness == "fresh"


def test_resolver_threshold_none_is_unknown():
    async def go():
        s = await _session()
        await _base(s)
        s.add(_price_obs())
        await s.commit()
        return await resolve_current_observation(
            s, marketplace_account_id="acc", marketplace_store_id="s1", product_id="p1",
            marketplace="wildberries", evaluated_at=NOW, freshness_threshold_seconds=None)
    r = _run(go())
    assert r.status == FOUND and r.freshness == "unknown" and r.promo_price_proven is False


def test_resolver_stale_when_past_threshold():
    async def go():
        s = await _session()
        await _base(s)
        s.add(_price_obs(fetched_at=NOW - timedelta(days=10), last_verified_at=NOW - timedelta(days=10)))
        await s.commit()
        return await resolve_current_observation(
            s, marketplace_account_id="acc", marketplace_store_id="s1", product_id="p1",
            marketplace="wildberries", evaluated_at=NOW, freshness_threshold_seconds=86400)
    r = _run(go())
    assert r.status == STALE


def test_resolver_unknown_currency_flags_missing_and_unproven():
    async def go():
        s = await _session()
        await _base(s)
        s.add(_price_obs(currency=None, currency_status="unknown"))
        await s.commit()
        return await resolve_current_observation(
            s, marketplace_account_id="acc", marketplace_store_id="s1", product_id="p1",
            marketplace="wildberries", evaluated_at=NOW, freshness_threshold_seconds=3600)
    r = _run(go())
    assert r.currency is None and r.currency_proven is False and "currency" in r.missing_fields


def test_resolver_revenue_commission_subsidy_stay_unknown_null_not_zero():
    async def go():
        s = await _session()
        await _base(s)
        s.add(_price_obs())     # no revenue/commission/subsidy proof
        await s.commit()
        return await resolve_current_observation(
            s, marketplace_account_id="acc", marketplace_store_id="s1", product_id="p1",
            marketplace="wildberries", evaluated_at=NOW, freshness_threshold_seconds=3600)
    r = _run(go())
    assert r.seller_revenue_status == "unknown" and r.commission_base_status == "unknown"
    assert r.subsidy_status == "unknown"
    assert "seller_revenue" in r.missing_fields and "commission_base" in r.missing_fields
    # evidence keeps NULLs as null, never 0
    ev = r.as_evidence()
    assert ev["promotion_price"] is None and ev["candidate_buyer_price"] == "800.00"


def test_resolver_unsupported_marketplace():
    async def go():
        s = await _session()
        await _base(s, marketplace="wildberries")
        await s.commit()
        return await resolve_current_observation(
            s, marketplace_account_id="acc", marketplace_store_id="s1", product_id="p1",
            marketplace="mercadolibre", evaluated_at=NOW)
    r = _run(go())
    assert r.status == UNSUPPORTED


# ── evaluation wiring: flags gate the bridge ────────────────────────────────────────────────────────
def test_both_flags_off_no_observation_select(monkeypatch):
    called = {"n": 0}
    async def boom(*a, **k):
        called["n"] += 1
        raise AssertionError("resolver must not be called with a flag off")
    monkeypatch.setattr("services.protection.evaluation.resolve_current_observation", boom)
    async def go():
        s = await _session()
        await _base(s)
        s.add(_price_obs())
        await s.commit()
        return await evaluate_policy_product(
            s, policy_id="pol1", marketplace_store_id="s1", product_id="p1",
            evaluation_run_id=R1, now=NOW)
    ev = _run(go())
    assert called["n"] == 0 and ev.actionability in ("manual_only", "unsupported")


def test_api_only_flag_still_off(monkeypatch):
    monkeypatch.setattr(settings, "api_data_sync_enabled", True)
    monkeypatch.setattr(settings, "protection_use_observations", False)
    async def boom(*a, **k):
        raise AssertionError("resolver must not be called when protection_use_observations is False")
    monkeypatch.setattr("services.protection.evaluation.resolve_current_observation", boom)
    async def go():
        s = await _session()
        await _base(s)
        s.add(_price_obs())
        await s.commit()
        return await evaluate_policy_product(
            s, policy_id="pol1", marketplace_store_id="s1", product_id="p1",
            evaluation_run_id=R1, now=NOW)
    ev = _run(go())
    assert ev.actionability in ("manual_only", "unsupported")


def test_bridge_on_proven_currency_removes_currency_unconfirmed(monkeypatch):
    _enable(monkeypatch)
    async def go():
        s = await _session()
        await _base(s)
        s.add(_price_obs(buyer_price=D("850"), currency="RUB", currency_status="proven"))
        await s.commit()
        ev = await evaluate_policy_product(
            s, policy_id="pol1", marketplace_store_id="s1", product_id="p1",
            evaluation_run_id=R1, now=NOW)
        return ev
    ev = _run(go())
    assert "currency_unconfirmed" not in (ev.reasons or [])
    snap = ev.inputs_snapshot
    assert snap["currency"] == "RUB"
    obs_ev = snap["freshness_ages"]["observation"]
    assert obs_ev["candidate_buyer_price"] == "850.00" and obs_ev["currency_proven"] is True
    # buyer price never becomes seller revenue → still unknown
    assert obs_ev["seller_revenue_status"] == "unknown"


def test_bridge_on_never_executable_and_no_side_effects(monkeypatch):
    _enable(monkeypatch)
    async def go():
        s = await _session()
        await _base(s)
        # a deep emergency price would tempt an action; it must stay non-executable
        s.add(_price_obs(buyer_price=D("1"), currency="RUB", currency_status="proven"))
        await s.commit()
        ev = await evaluate_policy_product(
            s, policy_id="pol1", marketplace_store_id="s1", product_id="p1",
            evaluation_run_id=R1, now=NOW)
        n_action = (await s.execute(select(func.count()).select_from(ProtectionActionState))).scalar_one()
        n_log = (await s.execute(select(func.count()).select_from(ExecutionLog))).scalar_one()
        return ev, n_action, n_log
    ev, n_action, n_log = _run(go())
    assert ev.actionability in ("manual_only", "unsupported")
    snap = ev.inputs_snapshot
    assert snap["capability_snapshot"]["provider_capability_confirmed"] is False
    assert snap["capability_snapshot"]["commission_official_tariff"] is False
    assert snap["capability_snapshot"]["promo_price_proven"] is False
    assert n_action == 0 and n_log == 0


def test_bridge_conflict_suppresses_and_records_fallback(monkeypatch):
    _enable(monkeypatch)
    async def go():
        s = await _session()
        await _base(s)
        s.add(_price_obs(external_product_id="SKU1", buyer_price=D("800")))
        s.add(_price_obs(external_product_id="SKU_X", buyer_price=D("801")))
        await s.commit()
        return await evaluate_policy_product(
            s, policy_id="pol1", marketplace_store_id="s1", product_id="p1",
            evaluation_run_id=R1, now=NOW)
    ev = _run(go())
    snap = ev.inputs_snapshot
    assert snap["freshness_ages"]["observation"]["fallback_reason"] == "observation_conflict"
    assert ev.actionability in ("manual_only", "unsupported")


def test_snapshot_self_contained_after_observation_deleted(monkeypatch):
    _enable(monkeypatch)
    async def go():
        s = await _session()
        await _base(s)
        s.add(_price_obs(buyer_price=D("850"), currency="RUB", currency_status="proven"))
        await s.commit()
        ev = await evaluate_policy_product(
            s, policy_id="pol1", marketplace_store_id="s1", product_id="p1",
            evaluation_run_id=R1, now=NOW)
        snap = dict(ev.inputs_snapshot)
        # physically delete every observation → the snapshot must still be readable
        for row in (await s.execute(select(MPO))).scalars().all():
            await s.delete(row)
        await s.commit()
        remaining = (await s.execute(select(func.count()).select_from(MPO))).scalar_one()
        return snap, remaining
    snap, remaining = _run(go())
    assert remaining == 0
    obs_ev = snap["freshness_ages"]["observation"]
    assert obs_ev["candidate_buyer_price"] == "850.00" and obs_ev["observation_id"]
    assert obs_ev["currency"] == "RUB"     # value copied, not a live FK


# ── structural guard: resolver is read-only, no dangerous imports ───────────────────────────────────
def test_resolver_is_structurally_read_only():
    import ast
    src = inspect.getsource(orv)
    low = src.lower()
    # no write call anywhere in the module body (these tokens never appear in the docstring)
    for verb in ("insert(", "update(", "delete(", ".add(", ".commit(", ".flush("):
        assert verb not in low, f"resolver must not call {verb}"
    # no dangerous import — checked on the parsed import graph, not raw text (docstring mentions them)
    imported = []
    for node in ast.walk(ast.parse(src)):
        if isinstance(node, ast.Import):
            imported += [n.name for n in node.names]
        elif isinstance(node, ast.ImportFrom):
            imported.append(node.module or "")
    joined = " ".join(imported).lower()
    for banned in ("wb_client", "ozon_client", "yandex_client", "base_client", "client",
                   "executor", "scheduler", "action_catalog", "provider"):
        assert banned not in joined, f"resolver must not import {banned}"
    assert "select(" in low     # the only sqlalchemy verb it uses is select
