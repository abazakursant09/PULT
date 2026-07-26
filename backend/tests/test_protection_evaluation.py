"""PULT-LAUNCH-2.5B — runtime evaluation service. Feature OFF; never executable on master.

Proves the orchestration: typed skip gates (no Evaluation with invented values), target resolution
(product vs store-wide, one Evaluation per active placement, product override), idempotency per
(policy, product, run), honest incomplete result (no promo price / storage / return cost on master),
append-only evidence with no secrets, and zero side effects (no ActionState / ExecutionLog / provider
call). The pure 2.4 engine is unchanged.
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
)
from models.marketplace_account import MarketplaceAccount
from models.marketplace_store import MarketplaceStore
from models.product import Product
from models.product_placement import ProductPlacement
from models.product_listing import ProductListing
from models.physical_product import PhysicalProduct
from models.imported_product import ImportedProductRow
from models.imported_finance import ImportedFinanceRow
from models.execution_log import ExecutionLog

from services.protection.evaluation import (
    evaluate_policy, evaluate_policy_product, SkipResult,
    SKIP_POLICY_DISABLED, SKIP_CONSENT_ABSENT, SKIP_CONSENT_REVOKED,
    SKIP_STORE_ARCHIVED, SKIP_PLACEMENT_INACTIVE,
)

NOW = datetime(2026, 7, 26, 12, 0, 0)
D = Decimal


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
                                     product_id=pid, marketplace_store_id="s1", fetched_at=NOW))
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


async def _count(s, model):
    return (await s.execute(select(func.count()).select_from(model))).scalar()


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
        out = await evaluate_policy(s, policy_id="pol1", evaluation_run_id="r1", now=NOW)
        assert isinstance(out[0], SkipResult) and out[0].reason == reason
        assert await _count(s, ProtectionEvaluation) == 0
    _run(go())


# ── product policy → one Evaluation, incomplete, never executable ────────────
def test_product_policy_one_incomplete_evaluation():
    async def go():
        s = await _session(); await _seed(s)
        out = await evaluate_policy(s, policy_id="pol1", evaluation_run_id="r1", now=NOW)
        assert len(out) == 1
        ev = out[0]
        assert isinstance(ev, ProtectionEvaluation)
        assert ev.verdict == "incomplete"                 # storage/acquiring/return cost unprovable
        assert ev.actionability != "executable"
        assert ev.economic_verdict is None                # not complete → no economic verdict
        assert ev.evaluation_run_id == "r1" and ev.product_id == "p1"
        assert "storage_unconfirmed" in ev.missing_fields        # unknown expense is never 0
        assert "acquiring_unconfirmed" in ev.missing_fields
        assert "returns_history_insufficient" in ev.missing_fields   # only 1 sold < min 30
    _run(go())


def test_return_cost_unconfirmed_with_enough_sample():
    async def go():
        s = await _session(); await _seed(s, sold=30)   # sample ok, but per-return cost unprovable
        ev = (await evaluate_policy(s, policy_id="pol1", evaluation_run_id="r1", now=NOW))[0]
        assert ev.verdict == "incomplete"
        assert "return_cost_unconfirmed" in ev.missing_fields
        assert "returns_history_insufficient" not in ev.missing_fields
    _run(go())


def test_never_executable_on_master():
    async def go():
        s = await _session(); await _seed(s)
        out = await evaluate_policy(s, policy_id="pol1", evaluation_run_id="r1", now=NOW)
        assert out[0].actionability in ("manual_only", "unsupported")
        cap = out[0].inputs_snapshot["capability_snapshot"]
        assert cap["promo_price_proven"] is False and cap["commission_official_tariff"] is False
    _run(go())


# ── store-wide → one Evaluation per active placement; product override ───────
def test_store_wide_one_per_placement():
    async def go():
        s = await _session()
        await _seed(s, store_wide=True, extra_products=[("p2", "SKU2"), ("p3", "SKU3")])
        out = await evaluate_policy(s, policy_id="pol1", evaluation_run_id="r1", now=NOW)
        evals = [o for o in out if isinstance(o, ProtectionEvaluation)]
        assert {e.product_id for e in evals} == {"p1", "p2", "p3"}   # one each, no average
        assert await _count(s, ProtectionEvaluation) == 3
    _run(go())


def test_product_policy_overrides_store_wide():
    async def go():
        s = await _session()
        await _seed(s, store_wide=True, extra_products=[("p2", "SKU2")])
        # add an enabled product policy for p2 → the store-wide run must SKIP p2
        s.add(ProtectionPolicy(id="polP2", marketplace_store_id="s1", product_id="p2",
                               enabled=True, target_margin_pct=D("10"), emergency_abs=D("0"),
                               consent_at=NOW, consent_version="v1"))
        await s.commit()
        out = await evaluate_policy(s, policy_id="pol1", evaluation_run_id="r1", now=NOW)
        evals = [o for o in out if isinstance(o, ProtectionEvaluation)]
        skips = [o for o in out if isinstance(o, SkipResult)]
        assert {e.product_id for e in evals} == {"p1"}
        assert any(sk.product_id == "p2" and sk.reason == "overridden_by_product_policy" for sk in skips)
    _run(go())


# ── idempotency ──────────────────────────────────────────────────────────────
def test_same_run_is_idempotent():
    async def go():
        s = await _session(); await _seed(s)
        a = await evaluate_policy_product(s, policy_id="pol1", marketplace_store_id="s1",
                                          product_id="p1", evaluation_run_id="r1", now=NOW)
        b = await evaluate_policy_product(s, policy_id="pol1", marketplace_store_id="s1",
                                          product_id="p1", evaluation_run_id="r1", now=NOW)
        assert a.id == b.id                               # second returns the existing row
        assert await _count(s, ProtectionEvaluation) == 1
    _run(go())


def test_different_runs_append_only():
    async def go():
        s = await _session(); await _seed(s)
        await evaluate_policy_product(s, policy_id="pol1", marketplace_store_id="s1",
                                      product_id="p1", evaluation_run_id="r1", now=NOW)
        await evaluate_policy_product(s, policy_id="pol1", marketplace_store_id="s1",
                                      product_id="p1", evaluation_run_id="r2", now=NOW + timedelta(days=1))
        assert await _count(s, ProtectionEvaluation) == 2
    _run(go())


# ── evidence + honesty ───────────────────────────────────────────────────────
def test_evidence_shape_and_no_secrets():
    async def go():
        s = await _session(); await _seed(s)
        ev = (await evaluate_policy(s, policy_id="pol1", evaluation_run_id="r1", now=NOW))[0]
        snap = ev.inputs_snapshot
        for k in ("formula_version", "identity", "cost_lines", "capability_snapshot",
                  "threshold_snapshot", "returns_model", "advertising_included",
                  "calculation_status", "economic_verdict", "actionability", "rounding_mode"):
            assert k in snap, k
        blob = str(snap).lower()
        for bad in ("password", "token", "secret", "api-key", "@", "c:\\", "/home/"):
            assert bad not in blob, bad
    _run(go())


def test_catalog_price_is_warning_only():
    async def go():
        s = await _session(); await _seed(s)
        ev = (await evaluate_policy(s, policy_id="pol1", evaluation_run_id="r1", now=NOW))[0]
        assert ev.inputs_snapshot["promo_price_proven"] is False
        assert ev.actionability != "executable"
    _run(go())


def test_missing_cogs_incomplete():
    async def go():
        s = await _session(); await _seed(s, with_cogs=False)
        ev = (await evaluate_policy(s, policy_id="pol1", evaluation_run_id="r1", now=NOW))[0]
        assert ev.verdict == "incomplete" and "no_cogs" in ev.missing_fields
    _run(go())


def test_no_price_incomplete():
    async def go():
        s = await _session(); await _seed(s, with_price=False)
        ev = (await evaluate_policy(s, policy_id="pol1", evaluation_run_id="r1", now=NOW))[0]
        assert ev.verdict == "incomplete" and "revenue_unknown" in ev.missing_fields
    _run(go())


def test_deterministic_evidence():
    async def go():
        s = await _session(); await _seed(s)
        a = (await evaluate_policy(s, policy_id="pol1", evaluation_run_id="r1", now=NOW))[0].inputs_snapshot
        b = (await evaluate_policy(s, policy_id="pol1", evaluation_run_id="r2", now=NOW))[0].inputs_snapshot
        a2 = {k: v for k, v in a.items() if k not in ("evaluated_at",)}
        b2 = {k: v for k, v in b.items() if k not in ("evaluated_at",)}
        assert a2 == b2                                   # identical evidence bar the timestamp
    _run(go())


# ── zero side effects ────────────────────────────────────────────────────────
def test_no_action_state_or_execution_log():
    async def go():
        s = await _session(); await _seed(s)
        await evaluate_policy(s, policy_id="pol1", evaluation_run_id="r1", now=NOW)
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
                                          product_id="p1", evaluation_run_id="r1", now=NOW)
        assert await _count(s, ProtectionEvaluation) == 0   # no partial row
    _run(go())
