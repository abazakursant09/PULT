"""
Operations Signal Foundation (Slice 1) — observed-only operations_signal taken only
as far as Candidate / Decision. NO Apply, NO Effect, NO Learning wiring asserted here.

Signal condition is observed-only: Ozon listing in an auto-promotion AND observed
net_profit < 0 (a point value, not a trend / forecast). Other marketplaces never
produce a signal. The new contour rides the generic snapshot → candidate → bridge
path to a Decision (action_key = stop_auto_promotion).

────────────────────────────────────────────────────────────────────────────
Slice boundary (normative for review — do not widen these tests):

  Slice 1 chain  : Signal → Snapshot → Candidate → Decision         (covered here)
  Slice 2 chain  : Apply → Measure → Effect → Learning              (NOT this slice)

Slice 1 does NOT claim the full execution loop. It asserts only signal production,
generic-path promotion to a Decision, and action bindability.

Why BOUND is allowed at the binding registry already, before Slice 2 exists:
  * action_key = stop_auto_promotion is a pre-existing executable lever (Advertising
    contour) with its own Measure/Effect/Learning — operations reuses it, does not
    invent a new one;
  * the payload/action path is generic (snapshot → candidate → bridge), not
    operations-specific;
  * these tests verify only bindability + Decision creation, never Apply/Effect.
This matches Canonical Surface Doctrine §4 (Operations = Mixed; auto-promotion
margin drain is the sanctioned Ozon executable use-case).
────────────────────────────────────────────────────────────────────────────
"""
import asyncio
import inspect
import uuid
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from database import Base
import models  # registers tables
from models.operations_signal import OperationsSignal
from models.decision import Decision
from models.engine_signal_decision_link import EngineSignalDecisionLink

from services.operations.signal_builder import build_operations_signal, SIGNAL_KEY
from services.decision_outcome.registry import BY_SIGNAL_KEY
from services.decision_outcome.snapshot import build_signal_snapshot, EngineSignalSnapshot
from services.decision_outcome.promotion import promote_eligible_candidates
from services.decision_outcome.decision_bridge import bridge_links_to_decisions
from services.action_binding.registry import binding_for_action


def _run(c):
    return asyncio.run(c)


async def _engine():
    e = create_async_engine("sqlite+aiosqlite://",
                            connect_args={"check_same_thread": False}, poolclass=StaticPool)
    async with e.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    return sessionmaker(e, class_=AsyncSession, expire_on_commit=False)()


async def _produce(db, uid, **kw):
    """Default observed inputs that DO satisfy the gate; override per test."""
    params = dict(marketplace="ozon", sku="SKU1", net_profit=-100.0, in_auto_promotion=True)
    params.update(kw)
    sig = await build_operations_signal(db, user_id=uid, **params)
    await db.commit()
    return sig


async def _signals(db):
    return (await db.execute(select(OperationsSignal))).scalars().all()


# ── (1) table exists in the canonical metadata (migration shape) ─────────────

def test_operations_signal_table_registered():
    t = Base.metadata.tables.get("operations_signal")
    assert t is not None
    cols = set(t.columns.keys())
    for c in ("id", "user_id", "signal_key", "insight_key", "marketplace", "sku",
              "status", "evidence_hash", "created_at", "updated_at"):
        assert c in cols, f"missing column {c}"


# ── (2) no auto-promotion → no signal ────────────────────────────────────────

def test_no_signal_without_auto_promotion():
    async def go():
        db = await _engine(); uid = str(uuid.uuid4())
        sig = await _produce(db, uid, in_auto_promotion=False)
        assert sig is None and len(await _signals(db)) == 0
    _run(go())


# ── (3) net_profit >= 0 → no signal ──────────────────────────────────────────

def test_no_signal_without_loss():
    async def go():
        db = await _engine(); uid = str(uuid.uuid4())
        assert await _produce(db, uid, net_profit=0.0) is None
        assert await _produce(db, uid, net_profit=50.0) is None
        assert len(await _signals(db)) == 0
    _run(go())


# ── (4) Ozon + auto-promotion + loss → signal ────────────────────────────────

def test_ozon_signal_created():
    async def go():
        db = await _engine(); uid = str(uuid.uuid4())
        sig = await _produce(db, uid)
        assert sig is not None
        assert sig.signal_key == SIGNAL_KEY
        assert sig.insight_key == f"{SIGNAL_KEY}:ozon:SKU1"
        assert sig.marketplace == "ozon" and sig.status == "active"
        assert sig.evidence_hash                      # deterministic, non-empty
    _run(go())


# ── (5) WB / Yandex / Megamarket → no signal ─────────────────────────────────

def test_non_ozon_marketplaces_produce_nothing():
    async def go():
        db = await _engine(); uid = str(uuid.uuid4())
        for mp in ("wildberries", "wb", "yandex", "megamarket"):
            assert await _produce(db, uid, marketplace=mp) is None, mp
        assert len(await _signals(db)) == 0
    _run(go())


# ── (6) signal_key registered in the canonical registry ──────────────────────

def test_registry_has_operations_signal():
    e = BY_SIGNAL_KEY.get(SIGNAL_KEY)
    assert e is not None
    assert e.contour == "operations"
    assert e.action_keys == ("stop_auto_promotion",)
    assert e.default_metric_key == "net_profit"


# ── (7) snapshot normalizes operations_signal ────────────────────────────────

def test_snapshot_normalizes_operations_signal():
    async def go():
        db = await _engine(); uid = str(uuid.uuid4())
        await _produce(db, uid)
        snaps = await build_signal_snapshot(db, user_id=uid, contour="operations")
        valid = [s for s in snaps if isinstance(s, EngineSignalSnapshot)]
        assert len(valid) == 1
        s = valid[0]
        assert s.contour == "operations"
        assert s.canonical_insight_key == f"{SIGNAL_KEY}:ozon:SKU1"
        assert s.action_key == "stop_auto_promotion"
        assert s.metric_key == "net_profit"
    _run(go())


# ── (8) candidate / proposed link carries action_key=stop_auto_promotion ──────

def test_candidate_link_has_action_key():
    async def go():
        db = await _engine(); uid = str(uuid.uuid4())
        await _produce(db, uid)
        await promote_eligible_candidates(db, user_id=uid); await db.commit()
        link = (await db.execute(select(EngineSignalDecisionLink).where(
            EngineSignalDecisionLink.user_id == uid))).scalars().one()
        assert link.contour == "operations"
        assert link.action_key == "stop_auto_promotion"
        assert link.link_status == "proposed"
    _run(go())


# ── (9) promotion creates a Decision ─────────────────────────────────────────

def test_promotion_creates_decision():
    async def go():
        db = await _engine(); uid = str(uuid.uuid4())
        await _produce(db, uid)
        await promote_eligible_candidates(db, user_id=uid); await db.commit()
        res = await bridge_links_to_decisions(db, user_id=uid); await db.commit()
        assert res.promoted == 1
        ds = (await db.execute(select(Decision))).scalars().all()
        assert len(ds) == 1
        assert ds[0].insight_key == f"{SIGNAL_KEY}:ozon:SKU1"
        assert ds[0].action_key == "stop_auto_promotion"
        sig = (await db.execute(select(OperationsSignal))).scalars().one()
        assert sig.status == "promoted_to_decision" and sig.decision_id == ds[0].id
    _run(go())


# ── (10) repeated promotion does not duplicate the Decision ──────────────────

def test_promotion_idempotent():
    async def go():
        db = await _engine(); uid = str(uuid.uuid4())
        await _produce(db, uid)
        await promote_eligible_candidates(db, user_id=uid); await db.commit()
        await bridge_links_to_decisions(db, user_id=uid); await db.commit()
        # second full pass — no new signal (idempotent producer), no new Decision
        again = await build_operations_signal(
            db, user_id=uid, marketplace="ozon", sku="SKU1", net_profit=-100.0,
            in_auto_promotion=True); await db.commit()
        await promote_eligible_candidates(db, user_id=uid); await db.commit()
        await bridge_links_to_decisions(db, user_id=uid); await db.commit()
        assert len(await _signals(db)) == 1
        assert len((await db.execute(select(Decision))).scalars().all()) == 1
        assert again is not None   # returns the existing active row, not a duplicate
    _run(go())


# ── (11) binding resolves to stop_auto_promotion ─────────────────────────────

def test_binding_returns_stop_auto_promotion():
    b = binding_for_action(SIGNAL_KEY, "stop_auto_promotion")
    assert b is not None and b.bindable
    assert b.action_key == "stop_auto_promotion"
    assert b.binding_status == "bound"


# ── (12) doctrine guard — producer never USES forecast/AI/competitor sources ──
# Scan referenced NAMES (imports + attribute/identifier usage) via AST, not prose,
# so the module's own "no forecast / no competitor" doctrine comments don't trip it.

def test_producer_no_forbidden_sources():
    import ast
    import services.operations.signal_builder as b
    tree = ast.parse(inspect.getsource(b))
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for a in node.names:
                names.add(a.name)
        elif isinstance(node, ast.ImportFrom):
            names.add(node.module or "")
            for a in node.names:
                names.add(a.name)
        elif isinstance(node, ast.Attribute):
            names.add(node.attr)
        elif isinstance(node, ast.Name):
            names.add(node.id)
        elif isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
            names.add(node.func.id)
    blob = " ".join(names).lower()
    for bad in ("forecast", "predict", "competitor", "compute_recommendation",
                "openai", "llm", "anthropic"):
        assert bad not in blob, f"producer USES forbidden source: {bad}"
