"""
Insight → Decision bridge (Slice 1: promotion only) — tests.

Covers: promotion create/get idempotency, hard blocks, deterministic action_key,
spine resolution (hit + miss), no apply/measurement side effects, DB-level
uniqueness, architecture import guards, and caller wiring (exactly-once on the
/execute path; never inside _compute_insights).
"""
import ast
import asyncio
import inspect
import types
import uuid

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from database import Base
import models  # registers spine + decision + outcome + execution_log
from models.decision import Decision
from models.decision_outcome import DecisionOutcome
from models.execution_log import ExecutionLog
from models.product_listing import ProductListing
from services import insight_decision_bridge as bridge
from services.insight_decision_bridge import (
    InsightPromotionDTO, PromoteResult, promote_insight_to_decision, action_key_for,
)


def _run(c):
    return asyncio.run(c)


async def _engine():
    eng = create_async_engine("sqlite+aiosqlite://",
                              connect_args={"check_same_thread": False}, poolclass=StaticPool)
    async with eng.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    return sessionmaker(eng, class_=AsyncSession, expire_on_commit=False)()


def _dto(itype="margin_crisis", mp="wb", sku="SKU1", **kw):
    key = kw.pop("insight_key", f"{itype}:{mp}:{sku}")
    return InsightPromotionDTO(
        insight_key=key, itype=itype, marketplace=mp, sku=sku,
        problem=kw.pop("problem", "Маржа под давлением"),
        cause=kw.pop("cause", None), effect=kw.pop("effect", None),
        action=kw.pop("action", None), pnl_impact=kw.pop("pnl_impact", None),
        severity=kw.pop("severity", "warn"), is_demo=kw.pop("is_demo", False),
    )


# ── creation + idempotency ───────────────────────────────────────────────────

def test_promotable_insight_creates_decision():
    async def go():
        db = await _engine(); uid = str(uuid.uuid4())
        r = await promote_insight_to_decision(db, user_id=uid, insight=_dto())
        assert isinstance(r, PromoteResult)
        assert r.created is True and r.promotable is True and r.decision_id and r.reason is None
        row = (await db.execute(select(Decision).where(Decision.id == r.decision_id))).scalar_one()
        assert row.insight_key == "margin_crisis:wb:SKU1"
        assert row.status == "open" and row.source == "insight"
        assert row.action_key == "set_price"
    _run(go())


def test_same_key_twice_returns_same_decision_created_false():
    async def go():
        db = await _engine(); uid = str(uuid.uuid4())
        r1 = await promote_insight_to_decision(db, user_id=uid, insight=_dto())
        r2 = await promote_insight_to_decision(db, user_id=uid, insight=_dto())
        assert r1.created is True and r2.created is False
        assert r1.decision_id == r2.decision_id
        n = len((await db.execute(select(Decision).where(Decision.user_id == uid))).scalars().all())
        assert n == 1
    _run(go())


# ── hard blocks ──────────────────────────────────────────────────────────────

def test_unknown_sku_blocks():
    async def go():
        db = await _engine(); uid = str(uuid.uuid4())
        r = await promote_insight_to_decision(
            db, user_id=uid, insight=_dto(sku=None, insight_key="margin_crisis:wb:unknown"))
        assert r.created is False and r.decision_id is None and r.reason == "non_promotable_sku"
        assert (await db.execute(select(Decision))).scalars().first() is None
    _run(go())


def test_unknown_suffix_key_blocks():
    async def go():
        db = await _engine(); uid = str(uuid.uuid4())
        r = await promote_insight_to_decision(
            db, user_id=uid, insight=_dto(sku="SKU1", insight_key="low_stock:wb:unknown"))
        assert r.reason == "non_promotable_sku" and r.decision_id is None
    _run(go())


def test_demo_blocks():
    async def go():
        db = await _engine(); uid = str(uuid.uuid4())
        r = await promote_insight_to_decision(db, user_id=uid, insight=_dto(is_demo=True))
        assert r.reason == "demo" and r.decision_id is None
        assert (await db.execute(select(Decision))).scalars().first() is None
    _run(go())


def test_empty_user_id_blocks():
    async def go():
        db = await _engine()
        r = await promote_insight_to_decision(db, user_id="", insight=_dto())
        assert r.reason == "no_scope" and r.decision_id is None
    _run(go())


# ── deterministic action_key mapping ─────────────────────────────────────────

def test_mapped_action_keys():
    assert action_key_for("margin_crisis") == "set_price"
    assert action_key_for("high_ad_spend") == "ad_set_bid"
    assert action_key_for("seo_opportunity") == "update_card"


def test_unmapped_action_keys_are_none():
    for t in ("sales_growth", "low_stock", "high_rating"):
        assert action_key_for(t) is None

    async def go():
        db = await _engine(); uid = str(uuid.uuid4())
        r = await promote_insight_to_decision(
            db, user_id=uid, insight=_dto(itype="sales_growth", insight_key="sales_growth:wb:SKU1"))
        row = (await db.execute(select(Decision).where(Decision.id == r.decision_id))).scalar_one()
        assert row.action_key is None
    _run(go())


# ── spine resolution ─────────────────────────────────────────────────────────

def test_unresolved_listing_still_creates_decision():
    async def go():
        db = await _engine(); uid = str(uuid.uuid4())
        r = await promote_insight_to_decision(db, user_id=uid, insight=_dto())
        row = (await db.execute(select(Decision).where(Decision.id == r.decision_id))).scalar_one()
        assert r.created is True
        assert row.physical_product_id is None and row.listing_id is None
    _run(go())


def test_resolved_listing_sets_ids():
    async def go():
        db = await _engine(); uid = str(uuid.uuid4())
        phys = str(uuid.uuid4())
        listing = ProductListing(id=str(uuid.uuid4()), physical_product_id=phys,
                                 user_id=uid, marketplace="wb", external_id="sku1")
        db.add(listing); await db.flush()
        # DTO sku "SKU1" (normalized upper) must match external_id "sku1" case-insensitively
        r = await promote_insight_to_decision(db, user_id=uid, insight=_dto(sku="SKU1"))
        row = (await db.execute(select(Decision).where(Decision.id == r.decision_id))).scalar_one()
        assert row.listing_id == listing.id and row.physical_product_id == phys
    _run(go())


# ── no apply / measurement side effects ──────────────────────────────────────

def test_no_apply_or_measurement_side_effects():
    async def go():
        db = await _engine(); uid = str(uuid.uuid4())
        await promote_insight_to_decision(db, user_id=uid, insight=_dto())
        assert (await db.execute(select(DecisionOutcome))).scalars().first() is None
        assert (await db.execute(select(ExecutionLog))).scalars().first() is None
    _run(go())


# ── DB-level uniqueness ──────────────────────────────────────────────────────

def test_unique_index_enforced_at_db():
    async def go():
        db = await _engine(); uid = str(uuid.uuid4())
        db.add(Decision(user_id=uid, insight_key="k:wb:A", problem="p", status="open"))
        await db.flush()
        db.add(Decision(user_id=uid, insight_key="k:wb:A", problem="p2", status="open"))
        raised = False
        try:
            await db.flush()
        except IntegrityError:
            raised = True
        assert raised, "unique (user_id, insight_key) not enforced"
    _run(go())


def test_insight_key_column_exists():
    assert "insight_key" in Decision.__table__.columns
    idx = {i.name for i in Decision.__table__.indexes}
    assert "uq_decision_user_insight" in idx


# ── architecture import guards ───────────────────────────────────────────────

_FORBIDDEN = ("executor", "wb_client", "ozon_client", "marketplace",
              "decision_validation", "attribution", "learning",
              "counterfactual", "action_engine")


def _imported_modules(mod) -> set[str]:
    tree = ast.parse(inspect.getsource(mod))
    mods: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            mods.update(a.name for a in node.names)
        elif isinstance(node, ast.ImportFrom):
            if node.module:
                mods.add(node.module)
    return mods


def test_bridge_does_not_import_forbidden():
    mods = _imported_modules(bridge)
    for m in mods:
        for bad in _FORBIDDEN:
            assert bad not in m, f"bridge must not import '{m}' (matched '{bad}')"


# ── caller wiring ────────────────────────────────────────────────────────────

def test_compute_insights_does_not_promote():
    from routers import action_engine as ae
    src = inspect.getsource(ae._compute_insights)
    assert "_promote_decision" not in src, "_compute_insights must not promote"


def test_execute_path_has_single_promotion_call_site():
    from routers import action_engine as ae
    full = inspect.getsource(ae)
    assert full.count("_promote_decision(") == 1
    assert "_promote_decision(" in inspect.getsource(ae.execute_insight)


def test_execute_promotes_exactly_once(monkeypatch):
    from routers import action_engine as ae

    calls = []

    async def fake_promote(db, *, user_id, insight):
        calls.append(insight.insight_key)
        return PromoteResult("d1", created=True, promotable=True, reason=None)

    plan = ae._imap.Plan(insight_key="margin_crisis:wb:SKU1", itype="margin_crisis",
                         action_type="set_price", payload={"price": 1})

    async def fake_resolve(db, uid, key, overrides):
        return plan

    class _Res:
        ok = True; status = "success"; log_id = "l1"; error = None; marketplace = "wb"

    async def fake_exec(**kw):
        return _Res()

    async def fake_open(db, **kw):   # Slice 3: isolate promotion from measurement
        return None

    monkeypatch.setattr(ae, "_promote_decision", fake_promote)
    monkeypatch.setattr(ae._imap, "resolve_plan", fake_resolve)
    monkeypatch.setattr(ae._executor, "execute", fake_exec)
    monkeypatch.setattr(ae, "_open_measurement", fake_open)

    body = types.SimpleNamespace(overrides={}, dry_run=False)
    user = types.SimpleNamespace(id="u1")
    resp = _run(ae.execute_insight("margin_crisis:wb:SKU1", body, current_user=user, db=None))
    assert calls == ["margin_crisis:wb:SKU1"]
    assert resp.success is True
