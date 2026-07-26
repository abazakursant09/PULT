"""PULT-LAUNCH-2.5B — runtime evaluation service. Feature OFF; never executable on master.

Proves the orchestration and the 2.5B correction invariants:
  * STORE ISOLATION — units / returns / CSV money scoped to ONE store; two stores of one seller never
    merged (regressions that FAIL on 59426c1);
  * SOURCE CONSISTENCY — API money is never divided by CSV units → incomplete, not a number;
  * RETURNS — sold+returned share one store+source+window;
  * TAX — percent / per_unit / none / unconfirmed / ambiguous;
  * ADDITIONAL COST — percent_of_revenue is a percent, not flat rubles;
  * CURRENCY — proven, never a hardcoded RUB;
  * CATALOG PRICE — chosen through the source policy, conflict preserved, warning-only;
  * FRESHNESS — per-input ages + top-level freshness_gate_configured=false;
  * UUID run id validated; idempotency per (policy, product, run) with append-only history;
  * zero side effects (no ActionState / ExecutionLog / provider call). The 2.4 engine is unchanged.
"""
import asyncio
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
from models.protection import (
    ProtectionPolicy, ProtectionEvaluation, ProtectionActionState, ProtectionAdditionalCost,
    ProtectionTaxSetting,
)
from models.marketplace_account import MarketplaceAccount
from models.marketplace_store import MarketplaceStore
from models.product import Product
from models.product_placement import ProductPlacement
from models.product_listing import ProductListing
from models.physical_product import PhysicalProduct
from models.imported_product import ImportedProductRow
from models.imported_finance import ImportedFinanceRow
from models.imported_return import ImportedReturnRow
from models.marketplace_operation import MarketplaceOperation
from models.api_sync_state import ApiSyncState
from models.store_data_source_policy import StoreDataSourcePolicy
from models.execution_log import ExecutionLog

from services.protection.evaluation import (
    evaluate_policy, evaluate_policy_product, SkipResult, _pick_tax,
    SKIP_POLICY_DISABLED, SKIP_CONSENT_ABSENT, SKIP_CONSENT_REVOKED,
    SKIP_STORE_ARCHIVED, SKIP_PLACEMENT_INACTIVE,
)

NOW = datetime(2026, 7, 26, 12, 0, 0)
D = Decimal
# Real, deterministic UUIDs (valid v4 shape); a runtime run id MUST be a UUID.
R1 = "11111111-1111-4111-8111-111111111111"
R2 = "22222222-2222-4222-8222-222222222222"


def _run(c):
    return asyncio.run(c)


async def _session():
    e = create_async_engine("sqlite+aiosqlite://",
                            connect_args={"check_same_thread": False}, poolclass=StaticPool)
    async with e.begin() as c:
        await c.run_sync(Base.metadata.create_all)
    return sessionmaker(e, class_=AsyncSession, expire_on_commit=False)()


async def _seed(s, *, store_status="active", placement_status="active", enabled=True,
                consent=True, revoked=False, store_wide=False, with_cogs=True, with_price=True,
                with_money=True, sold=1, extra_products=()):
    uid = str(uuid.uuid4())
    s.add(models.user.User(id=uid, email=f"{uid}@x.c", name="A", hashed_password="x"))
    from models.workspace import Workspace
    s.add(Workspace(id="ws", owner_user_id=uid))
    s.add(MarketplaceAccount(id="acc", workspace_id="ws", marketplace="wildberries",
                             identity_status="unverified"))
    s.add(MarketplaceStore(id="s1", marketplace_account_id="acc", marketplace="wildberries",
                           store_key="primary", label="S", status=store_status))
    await s.flush()

    async def _product(pid, sku):
        s.add(Product(id=pid, user_id=uid, name="N", marketplace="wildberries", sku=sku,
                      external_product_id=sku, marketplace_account_id="acc"))
        s.add(ProductPlacement(id=f"pp-{pid}", product_id=pid, marketplace_store_id="s1",
                               marketplace_account_id="acc", status=placement_status))
        if with_cogs:
            phys = f"ph-{pid}"
            s.add(PhysicalProduct(id=phys, user_id=uid, title="t", cogs=D("300"), cogs_source="manual"))
            s.add(ProductListing(physical_product_id=phys, user_id=uid, marketplace="wildberries",
                                 external_id=sku))
        if with_price:
            s.add(ImportedProductRow(id=f"ipr-{pid}", import_id="imp", user_id=uid,
                                     marketplace="wildberries", sku=sku, price=1000.0,
                                     product_id=pid, marketplace_store_id="s1", source="csv",
                                     fetched_at=NOW))
        if with_money:
            s.add(ImportedFinanceRow(id=f"fin-{pid}", import_id="imp", user_id=uid,
                                     marketplace="wildberries", date="2026-07-01", sku=sku,
                                     revenue=1000.0, commission=150.0, logistics=100.0, quantity=sold,
                                     product_id=pid, marketplace_store_id="s1", source="csv"))

    await _product("p1", "SKU1")
    for pid, sku in extra_products:
        await _product(pid, sku)

    pol = ProtectionPolicy(
        id="pol1", marketplace_store_id="s1", product_id=(None if store_wide else "p1"),
        enabled=enabled, target_margin_pct=D("10"), emergency_abs=D("0"),
        consent_at=(NOW if consent else None), consent_version=("v1" if consent else None),
        consent_revoked_at=(NOW if revoked else None))
    s.add(pol)
    await s.commit()
    return uid


async def _add_second_store(s, uid, *, account_id, store_id, marketplace="wildberries",
                            store_key="primary", commission=0.0, logistics=0.0, sold=0,
                            returns_qty=0, product_id="p1", sku="SKU1"):
    """A DIFFERENT store (another account, or another keyless store) carrying rows for the SAME
    product_id. Correct isolation must NEVER count these when evaluating store s1."""
    s.add(MarketplaceAccount(id=account_id, workspace_id="ws", marketplace=marketplace,
                             identity_status="unverified"))
    s.add(MarketplaceStore(id=store_id, marketplace_account_id=account_id, marketplace=marketplace,
                           store_key=store_key, label="B", status="active"))
    await s.flush()
    if sold or commission or logistics:
        s.add(ImportedFinanceRow(id=f"finB-{store_id}", import_id="impB", user_id=uid,
                                 marketplace=marketplace, date="2026-07-02", sku=sku,
                                 revenue=1.0, commission=commission, logistics=logistics,
                                 quantity=sold, product_id=product_id,
                                 marketplace_store_id=store_id, source="csv"))
    if returns_qty:
        s.add(ImportedReturnRow(id=f"retB-{store_id}", import_id="impB", user_id=uid,
                                marketplace=marketplace, date="2026-07-02", sku=sku,
                                returns_qty=returns_qty, product_id=product_id,
                                marketplace_store_id=store_id, source="csv"))
    await s.commit()


async def _count(s, model):
    return (await s.execute(select(func.count()).select_from(model))).scalar()


def _line(snap, key):
    for ln in snap["cost_lines"]:
        if ln["cost_key"] == key:
            return ln
    return None


# ── gates → typed skip, no Evaluation ────────────────────────────────────────
@pytest.mark.parametrize("kw,reason", [
    (dict(enabled=False), SKIP_POLICY_DISABLED),
    (dict(consent=False), SKIP_CONSENT_ABSENT),
    (dict(revoked=True), SKIP_CONSENT_REVOKED),
    (dict(store_status="archived"), SKIP_STORE_ARCHIVED),
    (dict(placement_status="detached"), SKIP_PLACEMENT_INACTIVE),
])
def test_gate_skips(kw, reason):
    async def go():
        s = await _session(); await _seed(s, **kw)
        out = await evaluate_policy(s, policy_id="pol1", evaluation_run_id=R1, now=NOW)
        assert isinstance(out[0], SkipResult) and out[0].reason == reason
        assert await _count(s, ProtectionEvaluation) == 0
    _run(go())


# ── product policy → one Evaluation, incomplete, never executable ────────────
def test_product_policy_one_incomplete_evaluation():
    async def go():
        s = await _session(); await _seed(s)
        out = await evaluate_policy(s, policy_id="pol1", evaluation_run_id=R1, now=NOW)
        assert len(out) == 1
        ev = out[0]
        assert isinstance(ev, ProtectionEvaluation)
        assert ev.verdict == "incomplete"
        assert ev.actionability != "executable"
        assert ev.economic_verdict is None
        assert ev.evaluation_run_id == R1 and ev.product_id == "p1"
        assert "storage_unconfirmed" in ev.missing_fields
        assert "acquiring_unconfirmed" in ev.missing_fields
        assert "returns_history_insufficient" in ev.missing_fields
    _run(go())


def test_return_cost_unconfirmed_with_enough_sample():
    async def go():
        s = await _session(); await _seed(s, sold=30)
        ev = (await evaluate_policy(s, policy_id="pol1", evaluation_run_id=R1, now=NOW))[0]
        assert ev.verdict == "incomplete"
        assert "return_cost_unconfirmed" in ev.missing_fields
        assert "returns_history_insufficient" not in ev.missing_fields
    _run(go())


def test_never_executable_on_master():
    async def go():
        s = await _session(); await _seed(s)
        out = await evaluate_policy(s, policy_id="pol1", evaluation_run_id=R1, now=NOW)
        assert out[0].actionability in ("manual_only", "unsupported")
        cap = out[0].inputs_snapshot["capability_snapshot"]
        assert cap["promo_price_proven"] is False and cap["commission_official_tariff"] is False
    _run(go())


# ── store-wide → one Evaluation per active placement; product override ───────
def test_store_wide_one_per_placement():
    async def go():
        s = await _session()
        await _seed(s, store_wide=True, extra_products=[("p2", "SKU2"), ("p3", "SKU3")])
        out = await evaluate_policy(s, policy_id="pol1", evaluation_run_id=R1, now=NOW)
        evals = [o for o in out if isinstance(o, ProtectionEvaluation)]
        assert {e.product_id for e in evals} == {"p1", "p2", "p3"}
        assert await _count(s, ProtectionEvaluation) == 3
    _run(go())


def test_product_policy_overrides_store_wide():
    async def go():
        s = await _session()
        await _seed(s, store_wide=True, extra_products=[("p2", "SKU2")])
        s.add(ProtectionPolicy(id="polP2", marketplace_store_id="s1", product_id="p2",
                               enabled=True, target_margin_pct=D("10"), emergency_abs=D("0"),
                               consent_at=NOW, consent_version="v1"))
        await s.commit()
        out = await evaluate_policy(s, policy_id="pol1", evaluation_run_id=R1, now=NOW)
        evals = [o for o in out if isinstance(o, ProtectionEvaluation)]
        skips = [o for o in out if isinstance(o, SkipResult)]
        assert {e.product_id for e in evals} == {"p1"}
        assert any(sk.product_id == "p2" and sk.reason == "overridden_by_product_policy" for sk in skips)
    _run(go())


# ── idempotency + UUID run id ─────────────────────────────────────────────────
def test_same_run_is_idempotent():
    async def go():
        s = await _session(); await _seed(s)
        a = await evaluate_policy_product(s, policy_id="pol1", marketplace_store_id="s1",
                                          product_id="p1", evaluation_run_id=R1, now=NOW)
        b = await evaluate_policy_product(s, policy_id="pol1", marketplace_store_id="s1",
                                          product_id="p1", evaluation_run_id=R1, now=NOW)
        assert a.id == b.id
        assert await _count(s, ProtectionEvaluation) == 1
    _run(go())


def test_concurrent_same_run_yields_one_row_same_id():
    # SQLite cannot prove true PostgreSQL row-level concurrency; this proves the DB-level guarantee:
    # a second call for the same (policy, product, run) returns the SAME row id, exactly one row.
    async def go():
        s = await _session(); await _seed(s)
        ids = []
        for _ in range(3):
            ev = await evaluate_policy_product(s, policy_id="pol1", marketplace_store_id="s1",
                                               product_id="p1", evaluation_run_id=R1, now=NOW)
            ids.append(ev.id)
        assert len(set(ids)) == 1
        assert await _count(s, ProtectionEvaluation) == 1
    _run(go())


def test_different_runs_append_only():
    async def go():
        s = await _session(); await _seed(s)
        await evaluate_policy_product(s, policy_id="pol1", marketplace_store_id="s1",
                                      product_id="p1", evaluation_run_id=R1, now=NOW)
        await evaluate_policy_product(s, policy_id="pol1", marketplace_store_id="s1",
                                      product_id="p1", evaluation_run_id=R2, now=NOW + timedelta(days=1))
        assert await _count(s, ProtectionEvaluation) == 2
    _run(go())


def test_invalid_run_uuid_rejected_no_evaluation():
    async def go():
        s = await _session(); await _seed(s)
        with pytest.raises(ValueError):
            await evaluate_policy(s, policy_id="pol1", evaluation_run_id="r1", now=NOW)
        with pytest.raises(ValueError):
            await evaluate_policy_product(s, policy_id="pol1", marketplace_store_id="s1",
                                          product_id="p1", evaluation_run_id="not-a-uuid", now=NOW)
        assert await _count(s, ProtectionEvaluation) == 0
    _run(go())


# ── STORE ISOLATION (regressions: FAIL on 59426c1) ───────────────────────────
def test_cross_store_units_isolation():
    async def go():
        s = await _session(); await _seed(s, sold=30)
        await _add_second_store(s, (await _uid(s)), account_id="accB", store_id="s2", sold=999)
        ev = (await evaluate_policy(s, policy_id="pol1", evaluation_run_id=R1, now=NOW))[0]
        assert ev.inputs_snapshot["returns_model"]["sold_units"] == 30   # store B's 999 excluded
    _run(go())


def test_cross_store_returns_isolation():
    async def go():
        s = await _session(); await _seed(s, sold=30)
        uid = await _uid(s)
        # store A returns
        s.add(ImportedReturnRow(id="retA", import_id="imp", user_id=uid, marketplace="wildberries",
                                date="2026-07-03", sku="SKU1", returns_qty=3, product_id="p1",
                                marketplace_store_id="s1", source="csv"))
        await s.commit()
        await _add_second_store(s, uid, account_id="accB", store_id="s2", returns_qty=777)
        ev = (await evaluate_policy(s, policy_id="pol1", evaluation_run_id=R1, now=NOW))[0]
        assert ev.inputs_snapshot["returns_model"]["returned_units"] == 3   # store B's 777 excluded
    _run(go())


def test_cross_store_csv_money_isolation():
    async def go():
        s = await _session(); await _seed(s, sold=30)   # store A commission=150, units=30 → 5.00/unit
        await _add_second_store(s, (await _uid(s)), account_id="accB", store_id="s2",
                                commission=99999.0, logistics=88888.0, sold=999)
        ev = (await evaluate_policy(s, policy_id="pol1", evaluation_run_id=R1, now=NOW))[0]
        assert _line(ev.inputs_snapshot, "commission")["amount"] == "5.00"   # not blended with store B
    _run(go())


def test_two_yandex_stores_same_account_isolated():
    async def go():
        s = await _session()
        uid = str(uuid.uuid4())
        s.add(models.user.User(id=uid, email=f"{uid}@x.c", name="A", hashed_password="x"))
        from models.workspace import Workspace
        s.add(Workspace(id="ws", owner_user_id=uid))
        s.add(MarketplaceAccount(id="accY", workspace_id="ws", marketplace="yandex",
                                 identity_status="unverified"))
        # two keyless Yandex stores under ONE account
        s.add(MarketplaceStore(id="sy1", marketplace_account_id="accY", marketplace="yandex",
                               store_key="k1", label="Y1", status="active"))
        s.add(MarketplaceStore(id="sy2", marketplace_account_id="accY", marketplace="yandex",
                               store_key="k2", label="Y2", status="active"))
        await s.flush()
        s.add(Product(id="py", user_id=uid, name="N", marketplace="yandex", sku="SKY",
                      external_product_id="SKY", marketplace_account_id="accY"))
        s.add(ProductPlacement(id="ppy", product_id="py", marketplace_store_id="sy1",
                               marketplace_account_id="accY", status="active"))
        # money for py under store sy1 (units 20) and sy2 (units 900 — must be excluded)
        s.add(ImportedFinanceRow(id="fy1", import_id="i", user_id=uid, marketplace="yandex",
                                 date="2026-07-01", sku="SKY", quantity=20, product_id="py",
                                 marketplace_store_id="sy1", source="csv"))
        s.add(ImportedFinanceRow(id="fy2", import_id="i", user_id=uid, marketplace="yandex",
                                 date="2026-07-01", sku="SKY", quantity=900, product_id="py",
                                 marketplace_store_id="sy2", source="csv"))
        s.add(ProtectionPolicy(id="poly", marketplace_store_id="sy1", product_id="py",
                               enabled=True, target_margin_pct=D("10"), emergency_abs=D("0"),
                               consent_at=NOW, consent_version="v1"))
        await s.commit()
        ev = (await evaluate_policy(s, policy_id="poly", evaluation_run_id=R1, now=NOW))[0]
        assert ev.inputs_snapshot["returns_model"]["sold_units"] == 20   # sy2's 900 excluded
    _run(go())


# ── SOURCE CONSISTENCY: API money must NOT be divided by CSV units ───────────
def test_api_money_not_blended_with_csv_units():
    async def go():
        s = await _session(); await _seed(s, sold=30)   # CSV units exist (30); NO API orders exist
        uid = await _uid(s)
        # seller prefers API for commission
        s.add(StoreDataSourcePolicy(marketplace_store_id="s1", metric_type="marketplace_fees",
                                    preference="api"))
        # API commission operation, attributed to p1, fully covered/synced
        s.add(MarketplaceOperation(id="op1", marketplace_account_id="acc", marketplace_store_id="s1",
                                   product_id="p1", marketplace="wildberries", source="api",
                                   external_operation_id="rrd1", operation_type="commission",
                                   provider_dataset="finance", amount=D("-500.00"),
                                   occurred_at=datetime(2026, 7, 1)))
        s.add(ApiSyncState(id="ss1", marketplace_connection_id="cx", marketplace_account_id="acc",
                           marketplace_store_id="s1", data_type="finance", status="synced",
                           coverage_complete=True, skipped_rows_count=0,
                           covered_from="2026-01-01", covered_to="2026-12-31",
                           last_success_at=NOW))
        await s.commit()
        ev = (await evaluate_policy(s, policy_id="pol1", evaluation_run_id=R1, now=NOW))[0]
        cl = _line(ev.inputs_snapshot, "commission")
        assert cl["origin"] == "missing"                       # NOT a per-unit number
        assert "commission_unconfirmed" in ev.missing_fields
        fr = ev.inputs_snapshot["freshness_ages"]["marketplace_fees"]
        assert fr["reason"] == "no_source_consistent_quantity"
    _run(go())


# ── TAX ──────────────────────────────────────────────────────────────────────
def _add_tax(s, **kw):
    s.add(ProtectionTaxSetting(id=kw.pop("id", "tax1"), policy_id="pol1", **kw))


def test_tax_percent_on_revenue():
    async def go():
        s = await _session(); await _seed(s)
        _add_tax(s, tax_mode="percent", tax_rate=D("7"), applied_to="seller_sale_revenue",
                 effective_from=NOW - timedelta(days=1), seller_confirmed_at=NOW)
        await s.commit()
        ev = (await evaluate_policy(s, policy_id="pol1", evaluation_run_id=R1, now=NOW))[0]
        assert _line(ev.inputs_snapshot, "taxes")["amount"] == "70.00"   # 1000 × 7%
    _run(go())


def test_tax_per_unit():
    async def go():
        s = await _session(); await _seed(s)
        _add_tax(s, tax_mode="per_unit", tax_per_unit=D("12.50"), applied_to="contribution",
                 effective_from=NOW - timedelta(days=1), seller_confirmed_at=NOW)
        await s.commit()
        ev = (await evaluate_policy(s, policy_id="pol1", evaluation_run_id=R1, now=NOW))[0]
        assert _line(ev.inputs_snapshot, "taxes")["amount"] == "12.50"
    _run(go())


def test_tax_none_confirmed_is_explicit_zero():
    async def go():
        s = await _session(); await _seed(s)
        _add_tax(s, tax_mode="none", applied_to="contribution",
                 effective_from=NOW - timedelta(days=1), seller_confirmed_at=NOW)
        await s.commit()
        ev = (await evaluate_policy(s, policy_id="pol1", evaluation_run_id=R1, now=NOW))[0]
        assert _line(ev.inputs_snapshot, "taxes")["origin"] == "confirmed_zero"
        assert "taxes_unattributed" not in ev.missing_fields
    _run(go())


def test_tax_unconfirmed_is_incomplete_not_zero():
    async def go():
        s = await _session(); await _seed(s)
        _add_tax(s, tax_mode="percent", tax_rate=D("7"), applied_to="seller_sale_revenue",
                 effective_from=NOW - timedelta(days=1), seller_confirmed_at=None)  # NOT confirmed
        await s.commit()
        ev = (await evaluate_policy(s, policy_id="pol1", evaluation_run_id=R1, now=NOW))[0]
        assert _line(ev.inputs_snapshot, "taxes")["origin"] == "missing"
        assert "taxes_unattributed" in ev.missing_fields
    _run(go())


def test_tax_ambiguous_same_effective_from_different_values():
    # UNIQUE(policy_id) forbids two persisted rows, so the ambiguity branch is proven purely.
    class _T:
        def __init__(self, mode, rate, per_unit, applied, eff, conf):
            self.tax_mode, self.tax_rate, self.tax_per_unit = mode, rate, per_unit
            self.applied_to, self.effective_from, self.seller_confirmed_at = applied, eff, conf
    eff = NOW - timedelta(days=1)
    a = _T("percent", D("7"), None, "seller_sale_revenue", eff, NOW)
    b = _T("percent", D("9"), None, "seller_sale_revenue", eff, NOW)
    setting, conflict, reason = _pick_tax([a, b], NOW)
    assert setting is None and conflict is True and reason == "tax_ambiguous"


# ── ADDITIONAL COST: percent_of_revenue is a percent, not flat rubles ────────
def test_additional_cost_percent_of_revenue():
    async def go():
        s = await _session(); await _seed(s)
        s.add(ProtectionAdditionalCost(id="ac1", policy_id="pol1", name="packaging", amount=D("5"),
                                       calculation_type="percent_of_revenue", currency="RUB",
                                       enabled=True, effective_from=NOW - timedelta(days=1),
                                       seller_confirmed_at=NOW))
        await s.commit()
        ev = (await evaluate_policy(s, policy_id="pol1", evaluation_run_id=R1, now=NOW))[0]
        assert _line(ev.inputs_snapshot, "additional:packaging")["amount"] == "50.00"   # 1000 × 5%
    _run(go())


# ── CURRENCY: proven, never hardcoded RUB ────────────────────────────────────
def test_currency_not_hardcoded_rub():
    async def go():
        s = await _session(); await _seed(s)   # no manual costs → currency is unproven, NOT "RUB"
        ev = (await evaluate_policy(s, policy_id="pol1", evaluation_run_id=R1, now=NOW))[0]
        assert ev.inputs_snapshot["currency"] is None
        fp = ev.inputs_snapshot["freshness_ages"]["candidate_price"]
        assert fp["currency_unconfirmed"] is True
    _run(go())


# ── CATALOG PRICE via the source policy (conflict preserved) ─────────────────
def test_catalog_price_source_policy_conflict():
    async def go():
        s = await _session(); await _seed(s)   # CSV price 1000
        # a differing API price + a fresh synced snapshot + auto preference → conflict
        s.add(ImportedProductRow(id="ipr-api", import_id="imp", user_id=(await _uid(s)),
                                 marketplace="wildberries", sku="SKU1", price=2000.0,
                                 product_id="p1", marketplace_store_id="s1", source="api",
                                 fetched_at=NOW, external_row_id="nm1"))
        s.add(StoreDataSourcePolicy(marketplace_store_id="s1", metric_type="price", preference="auto"))
        s.add(ApiSyncState(id="ssp", marketplace_connection_id="cx", marketplace_account_id="acc",
                           marketplace_store_id="s1", data_type="prices", status="synced",
                           coverage_complete=True, skipped_rows_count=0, last_success_at=NOW))
        await s.commit()
        ev = (await evaluate_policy(s, policy_id="pol1", evaluation_run_id=R1, now=NOW))[0]
        assert ev.verdict == "conflicting"
        assert any("price" in r for r in ev.reasons)
    _run(go())


def test_catalog_price_is_warning_only():
    async def go():
        s = await _session(); await _seed(s)
        ev = (await evaluate_policy(s, policy_id="pol1", evaluation_run_id=R1, now=NOW))[0]
        assert ev.inputs_snapshot["promo_price_proven"] is False
        assert ev.actionability != "executable"
    _run(go())


# ── FRESHNESS ────────────────────────────────────────────────────────────────
def test_freshness_gate_unconfigured_and_per_metric_ages():
    async def go():
        s = await _session(); await _seed(s)
        snap = (await evaluate_policy(s, policy_id="pol1", evaluation_run_id=R1, now=NOW))[0].inputs_snapshot
        assert snap["freshness_gate_configured"] is False
        fa = snap["freshness_ages"]
        assert fa["candidate_price"]["threshold_seconds"] is None
        assert "age_seconds" in fa["candidate_price"]
        assert fa["marketplace_fees"]["threshold_seconds"] is None
    _run(go())


# ── evidence + honesty ───────────────────────────────────────────────────────
def test_evidence_shape_and_no_secrets():
    async def go():
        s = await _session(); await _seed(s)
        ev = (await evaluate_policy(s, policy_id="pol1", evaluation_run_id=R1, now=NOW))[0]
        snap = ev.inputs_snapshot
        for k in ("formula_version", "identity", "cost_lines", "capability_snapshot",
                  "threshold_snapshot", "returns_model", "advertising_included",
                  "calculation_status", "economic_verdict", "actionability", "rounding_mode",
                  "freshness_gate_configured"):
            assert k in snap, k
        blob = str(snap).lower()
        for bad in ("password", "token", "secret", "api-key", "@", "c:\\", "/home/"):
            assert bad not in blob, bad
    _run(go())


def test_missing_cogs_incomplete():
    async def go():
        s = await _session(); await _seed(s, with_cogs=False)
        ev = (await evaluate_policy(s, policy_id="pol1", evaluation_run_id=R1, now=NOW))[0]
        assert ev.verdict == "incomplete" and "no_cogs" in ev.missing_fields
    _run(go())


def test_no_price_incomplete():
    async def go():
        s = await _session(); await _seed(s, with_price=False)
        ev = (await evaluate_policy(s, policy_id="pol1", evaluation_run_id=R1, now=NOW))[0]
        assert ev.verdict == "incomplete" and "revenue_unknown" in ev.missing_fields
    _run(go())


def test_deterministic_evidence():
    async def go():
        s = await _session(); await _seed(s)
        a = (await evaluate_policy(s, policy_id="pol1", evaluation_run_id=R1, now=NOW))[0].inputs_snapshot
        b = (await evaluate_policy(s, policy_id="pol1", evaluation_run_id=R2, now=NOW))[0].inputs_snapshot
        a2 = {k: v for k, v in a.items() if k not in ("evaluated_at",)}
        b2 = {k: v for k, v in b.items() if k not in ("evaluated_at",)}
        assert a2 == b2
    _run(go())


# ── zero side effects ────────────────────────────────────────────────────────
def test_no_action_state_or_execution_log():
    async def go():
        s = await _session(); await _seed(s)
        await evaluate_policy(s, policy_id="pol1", evaluation_run_id=R1, now=NOW)
        assert await _count(s, ProtectionActionState) == 0
        assert await _count(s, ExecutionLog) == 0
    _run(go())


def test_engine_import_has_no_provider_or_network():
    import ast
    import inspect
    import services.protection.evaluation as ev
    import services.protection.cost_map as cm
    for mod in (ev, cm):
        tree = ast.parse(inspect.getsource(mod))
        mods = []
        for n in ast.walk(tree):
            if isinstance(n, ast.Import):
                mods += [a.name for a in n.names]
            elif isinstance(n, ast.ImportFrom) and n.module:
                mods.append(n.module)
        for bad in ("wb_client", "ozon_client", "executor", "action_catalog",
                    "marketplace.ingest", "httpx", "requests", "aiohttp"):
            assert not any(bad in m for m in mods), (mod.__name__, bad)


def test_rollback_leaves_no_partial_evaluation(monkeypatch):
    async def go():
        s = await _session(); await _seed(s)
        import services.protection.evaluation as ev
        def boom(_inp):
            raise RuntimeError("compute failed")
        monkeypatch.setattr(ev, "compute", boom)
        with pytest.raises(RuntimeError):
            await evaluate_policy_product(s, policy_id="pol1", marketplace_store_id="s1",
                                          product_id="p1", evaluation_run_id=R1, now=NOW)
        assert await _count(s, ProtectionEvaluation) == 0
    _run(go())


async def _uid(s):
    from models.user import User
    return (await s.execute(select(User.id))).scalars().first()
