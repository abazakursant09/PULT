"""
Decision Apply — real end-to-end execution loop (integration).

Drives the WHOLE guarded path with nothing mocked except the marketplace network
boundary (wb_client.set_auto_promotion):

  build_apply_preview (dry-run gate, no execution)
    confirm_and_apply_decision
      -> record confirmed intent (append-only ledger)
      -> execute_bound_decision(dry_run=False)        [REAL bridge]
         -> apply_decision                            [REAL sanctioned path]
            -> executor.execute                       [REAL executor]
               -> connection / scope / payload validate / capability gate / guard
               -> ExecutionLog(pending) -> spec.dispatch (stubbed network) -> success
      -> open_effect_measurement                      [REAL Decision Outcome path]

Safety contract proven here: manual_approval only, no auto-apply, preview never
executes, missing capability / missing payload fail closed, executor failure is
recorded honestly as a failed ExecutionLog, and measurement opens only after a
real successful apply.
"""
import asyncio
import inspect
import uuid

from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from database import Base
import models  # registers tables
from models.decision import Decision
from models.engine_signal_decision_link import EngineSignalDecisionLink
from models.advertising_signal import AdvertisingSignal
from models.product_listing import ProductListing
from models.marketplace_connection import MarketplaceConnection
from models.api_credential import ApiCredential
from models.execution_log import ExecutionLog
from models.engine_effect_observation import EngineEffectObservation
from models.decision_apply_intent import DecisionApplyIntent

from services.marketplace import credential_vault, action_catalog, executor
from services.marketplace.errors import ExecutionError
from services.decision_apply_ux.preview import build_apply_preview
from services.decision_apply_ux.confirm import confirm_and_apply_decision
from services.action_binding.execution_bridge import execute_bound_decision
from services.decision_apply import apply_decision

IKEY = "adv_ad_on_low_stock:wildberries:SKU1"


def _run(c):
    return asyncio.run(c)


async def _engine():
    e = create_async_engine("sqlite+aiosqlite://",
                            connect_args={"check_same_thread": False}, poolclass=StaticPool)
    async with e.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    return sessionmaker(e, class_=AsyncSession, expire_on_commit=False)()


async def _seed(db, uid, *, mp="wildberries", sku="SKU1", ikey=IKEY,
                with_listing=True, with_connection=True):
    did = str(uuid.uuid4())
    db.add(Decision(id=did, user_id=uid, problem="adv", action_key="stop_auto_promotion",
                    insight_key=ikey, status="open"))
    db.add(EngineSignalDecisionLink(user_id=uid, contour="advertising",
           signal_table="advertising_signal", signal_id="sig1", insight_key=ikey,
           action_key="stop_auto_promotion", decision_id=did, link_status="promoted",
           marketplace=mp, sku=sku))
    db.add(AdvertisingSignal(audit_id=str(uuid.uuid4()), user_id=uid,
           signal_key="adv_ad_on_low_stock", problem_type="ad_on_low_stock",
           insight_key=ikey, marketplace=mp, sku=sku, status="promoted_to_decision"))
    if with_listing:
        db.add(ProductListing(physical_product_id="ph1", user_id=uid, marketplace="wb",
                              external_id=sku))
    if with_connection:
        cn = MarketplaceConnection(id=str(uuid.uuid4()), user_id=uid, marketplace=mp,
                                   status="connected", scopes=["promotions"])
        db.add(cn)
        db.add(ApiCredential(id=str(uuid.uuid4()), connection_id=cn.id, scope="promotions",
                             secret_enc=credential_vault.encrypt("t")))
    await db.commit()
    return did


async def _count(db, model):
    return (await db.execute(select(func.count()).select_from(model))).scalar()


def _stub_wb(monkeypatch, *, calls=None, raise_error=False):
    async def fake(*, token, offer_id, enabled):
        if calls is not None:
            calls.append({"offer_id": offer_id, "enabled": enabled})
        if raise_error:
            raise ExecutionError(ExecutionError.VALIDATION, "stubbed marketplace failure")
        return {"requestId": "wb-req-1"}
    monkeypatch.setattr(action_catalog.wb_client, "set_auto_promotion", fake)


# ── 1. preview does not execute ──────────────────────────────────────────────

def test_preview_does_not_execute(monkeypatch):
    calls = []
    _stub_wb(monkeypatch, calls=calls)
    async def go():
        db = await _engine(); uid = str(uuid.uuid4())
        await _seed(db, uid)
        p = await build_apply_preview(db, user_id=uid, decision_id=None or
                                      (await _only_decision_id(db)), marketplace="wildberries", sku="SKU1")
        assert p.applyable is True                       # eligible
        assert p.dry_run_status == "dry_run_ok"          # dry-run only
        assert calls == []                               # no marketplace call
        assert await _count(db, ExecutionLog) == 0       # no execution recorded
        assert await _count(db, EngineEffectObservation) == 0
        assert await _count(db, DecisionApplyIntent) == 0  # preview alone records no intent
    _run(go())


async def _only_decision_id(db):
    return (await db.execute(select(Decision.id))).scalars().one()


# ── 2. confirm executes exactly once ─────────────────────────────────────────

def test_confirm_executes_exactly_once(monkeypatch):
    calls = []
    _stub_wb(monkeypatch, calls=calls)
    async def go():
        db = await _engine(); uid = str(uuid.uuid4())
        did = await _seed(db, uid)
        r = await confirm_and_apply_decision(db, user_id=uid, decision_id=did,
                                             marketplace="wildberries", sku="SKU1",
                                             idempotency_key="idem-once")
        assert r.ok is True and r.status == "success"
        assert len(calls) == 1                                  # dispatched exactly once
        assert calls[0] == {"offer_id": "SKU1", "enabled": False}
        logs = (await db.execute(select(ExecutionLog.status))).scalars().all()
        assert logs == ["success"]                              # one success log
        intent = (await db.execute(select(DecisionApplyIntent))).scalars().one()
        assert intent.intent_status == "confirmed"
    _run(go())


# ── 3. missing capability blocks (yandex → impossible, fail closed) ──────────

def test_missing_capability_blocks(monkeypatch):
    calls = []
    _stub_wb(monkeypatch, calls=calls)
    async def go():
        db = await _engine(); uid = str(uuid.uuid4())
        did = await _seed(db, uid, mp="yandex", sku="SKU9",
                          ikey="adv_ad_on_low_stock:yandex:SKU9")
        r = await confirm_and_apply_decision(db, user_id=uid, decision_id=did,
                                             marketplace="yandex", sku="SKU9",
                                             idempotency_key="idem-y")
        assert r.ok is False and r.reason == "unsupported_capability"
        assert calls == []                                      # never dispatched
        assert await _count(db, ExecutionLog) == 0              # blocked before executor
        assert await _count(db, EngineEffectObservation) == 0
        intent = (await db.execute(select(DecisionApplyIntent))).scalars().one()
        assert intent.intent_status == "rejected"
    _run(go())


# ── 4. missing payload blocks (no listing → offer_id not derivable) ──────────

def test_missing_payload_blocks(monkeypatch):
    calls = []
    _stub_wb(monkeypatch, calls=calls)
    async def go():
        db = await _engine(); uid = str(uuid.uuid4())
        did = await _seed(db, uid, with_listing=False)
        r = await confirm_and_apply_decision(db, user_id=uid, decision_id=did,
                                             marketplace="wildberries", sku="SKU1",
                                             idempotency_key="idem-p")
        assert r.ok is False and r.reason == "payload_not_derivable"
        assert calls == []                                      # never dispatched
        assert await _count(db, ExecutionLog) == 0
        intent = (await db.execute(select(DecisionApplyIntent))).scalars().one()
        assert intent.intent_status == "rejected"
    _run(go())


# ── 5. executor failure recorded honestly ────────────────────────────────────

def test_executor_failure_recorded_honestly(monkeypatch):
    _stub_wb(monkeypatch, raise_error=True)
    async def go():
        db = await _engine(); uid = str(uuid.uuid4())
        did = await _seed(db, uid)
        r = await confirm_and_apply_decision(db, user_id=uid, decision_id=did,
                                             marketplace="wildberries", sku="SKU1",
                                             idempotency_key="idem-f")
        assert r.ok is False and r.status == "failed"          # honest failure
        log = (await db.execute(select(ExecutionLog))).scalars().one()
        assert log.status == "failed" and log.action_type == "stop_auto_promotion"
        assert r.measurement_opened is False                   # no measurement on failure
        assert await _count(db, EngineEffectObservation) == 0
        intent = (await db.execute(select(DecisionApplyIntent))).scalars().one()
        assert intent.intent_status == "confirmed"             # user confirmed; apply failed downstream
    _run(go())


# ── 6. successful apply opens measurement ────────────────────────────────────

def test_success_opens_measurement(monkeypatch):
    _stub_wb(monkeypatch)
    async def go():
        db = await _engine(); uid = str(uuid.uuid4())
        did = await _seed(db, uid)
        r = await confirm_and_apply_decision(db, user_id=uid, decision_id=did,
                                             marketplace="wildberries", sku="SKU1",
                                             idempotency_key="idem-m")
        assert r.ok is True and r.execution_log_id is not None
        assert r.measurement_opened is True
        assert await _count(db, EngineEffectObservation) == 1  # opened only after real success
    _run(go())


# ── 7. no auto-apply path exists ─────────────────────────────────────────────

def test_no_auto_apply_path():
    import services.decision_apply_ux.confirm as confirm_mod
    import services.decision_apply_ux.preview as preview_mod

    # The bound execution entry point defaults to dry_run=True — nothing applies
    # for real unless a caller explicitly opts in with dry_run=False.
    assert inspect.signature(execute_bound_decision).parameters["dry_run"].default is True

    # Preview only ever dry-runs (it is the read-only gate).
    psrc = inspect.getsource(preview_mod)
    assert "dry_run=True" in psrc and "dry_run=False" not in psrc

    # The single real apply is gated behind a recorded confirmation: confirm
    # records a confirmed intent BEFORE it calls execute_bound_decision(dry_run=False).
    csrc = inspect.getsource(confirm_mod.confirm_and_apply_decision)
    assert csrc.index('intent_status="confirmed"') < csrc.index("execute_bound_decision(")
    assert "dry_run=False" in csrc
    # confirm exposes no dry_run knob to skip the gate.
    assert "dry_run" not in inspect.signature(confirm_and_apply_decision).parameters

    # No OTHER module performs a real bound apply: confirm.py is the only source
    # that calls execute_bound_decision with dry_run=False.
    import pathlib
    root = pathlib.Path(execute_bound_decision.__globals__["__file__"]).parent.parent
    offenders = []
    for py in root.rglob("*.py"):
        if py.name in ("execution_bridge.py",) or "/tests/" in py.as_posix() or "\\tests\\" in str(py):
            continue
        text = py.read_text(encoding="utf-8")
        if "execute_bound_decision(" in text and "dry_run=False" in text:
            if py.name != "confirm.py":
                offenders.append(py.name)
    assert offenders == [], f"unexpected real-apply callers: {offenders}"
