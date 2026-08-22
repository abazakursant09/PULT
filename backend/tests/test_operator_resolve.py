"""SECURITY-2D-1C-C3B — manual operator resolution: unit / SQLite behaviour + source guards.

Real-PostgreSQL concurrency, atomicity and the full HTTP matrix live in test_operator_resolve_pg.py
(postgres-explain CI). This file covers the pure maps, the transition matrix + idempotency on a temp
SQLite DB, the CSRF narrow-exemption predicate, the append-only / no-executor AST guards, the Sentry
scrubber, and the migration roundtrip.
"""
import ast
import asyncio
import os
import pathlib
import tempfile
import uuid
from datetime import datetime, timezone

import pytest
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import sessionmaker

from services.marketplace.operation_fingerprint import compute_fingerprint

_BE = pathlib.Path(__file__).resolve().parents[1]
_V1 = "v1:client:" + "3f2504e0-4f89-41d3-9a0c-0305e82c3301"


def _run(coro):
    return asyncio.new_event_loop().run_until_complete(coro)


def _fresh_db(monkeypatch):
    d = tempfile.mkdtemp()
    db = os.path.join(d, "c3b.db").replace("\\", "/")
    monkeypatch.setenv("ALEMBIC_DATABASE_URL", f"sqlite+aiosqlite:///{db}")
    from alembic import command
    from alembic.config import Config
    command.upgrade(Config("alembic.ini"), "head")
    eng = create_async_engine(f"sqlite+aiosqlite:///{db}")
    Session = sessionmaker(eng, class_=AsyncSession, expire_on_commit=False)
    from services.marketplace.recovery import operator_resolve as orv
    monkeypatch.setattr(orv, "AsyncSessionLocal", Session)
    return orv, eng, Session


async def _seed(Session, *, rid=None, uid="tenant-A", status="pending", recon=None,
                key=None, fp=None, payload=None):
    from models.execution_log import ExecutionLog
    rid = rid or str(uuid.uuid4())
    key = key or ("v1:client:" + str(uuid.uuid4()))     # unique v1 key — avoid the 1B-B partial UNIQUE
    payload = {"offer_id": "OFF-1", "price": 100} if payload is None else payload
    if fp is None:
        fp = compute_fingerprint(uid, "c1", "wb", "set_price", "manual_l3", payload, None)
    async with Session() as db:
        db.add(ExecutionLog(id=rid, user_id=uid, connection_id="c1", marketplace="wb",
                            action_type="set_price", mode="manual_l3", payload=payload, status=status,
                            idempotency_key=key, request_fingerprint=fp, reconciliation_status=recon))
        await db.commit()
    return rid


# ── pure maps ──────────────────────────────────────────────────────────────────
def test_action_maps_and_reason_allowlists():
    from services.marketplace.recovery import operator_resolve as orv
    assert orv.allowed_reasons("confirm_applied") == frozenset({"operator_confirmed_applied"})
    assert orv.allowed_reasons("confirm_not_applied") == frozenset({"operator_confirmed_not_applied"})
    assert orv.allowed_reasons("close") == frozenset(
        {"operator_closed_no_action", "stale_pending_review", "ambiguous_needs_review"})
    assert orv.default_reason("confirm_applied") == "operator_confirmed_applied"
    # C3B never produces retry_authorized / authorize_retry
    for a, cfg in orv._ACTIONS.items():
        assert cfg[0] != "retry_authorized" and cfg[1] != "authorize_retry"


# ── transition matrix + write scope (SQLite) ────────────────────────────────────
def test_transition_matrix_and_denorm_write(monkeypatch):
    orv, eng, S = _fresh_db(monkeypatch)
    from models.execution_log import ExecutionLog
    from sqlalchemy import select

    async def go():
        try:
            rid = await _seed(S)
            now = datetime.now(timezone.utc)
            # NULL → confirmed_applied
            o = await orv.resolve(log_id=rid, tenant_user_id="tenant-A", action="confirm_applied",
                                  actor_id="op", correlation_id=str(uuid.uuid4()), reason_code=None, now=now)
            assert o.status == "ok" and o.audit["new_resolution"] == "confirmed_applied"
            assert o.audit["previous_resolution"] is None
            # confirmed_applied → confirmed_not_applied (correction)
            o2 = await orv.resolve(log_id=rid, tenant_user_id="tenant-A", action="confirm_not_applied",
                                   actor_id="op", correlation_id=str(uuid.uuid4()), reason_code=None, now=now)
            assert o2.status == "ok" and o2.audit["previous_resolution"] == "confirmed_applied"
            # any → manual_closed, then manual_closed → confirmed_applied (re-open)
            o3 = await orv.resolve(log_id=rid, tenant_user_id="tenant-A", action="close",
                                   actor_id="op", correlation_id=str(uuid.uuid4()), reason_code=None, now=now)
            assert o3.status == "ok" and o3.audit["new_resolution"] == "manual_closed"
            o4 = await orv.resolve(log_id=rid, tenant_user_id="tenant-A", action="confirm_applied",
                                   actor_id="op", correlation_id=str(uuid.uuid4()), reason_code=None, now=now)
            assert o4.status == "ok" and o4.audit["previous_resolution"] == "manual_closed"
            # denormalized current value + resolved_by/at written; nothing else touched
            async with S() as db:
                row = (await db.execute(select(ExecutionLog).where(ExecutionLog.id == rid))).scalars().first()
                assert row.manual_resolution == "confirmed_applied" and row.resolved_by == "op"
                assert row.resolved_at is not None
                assert row.status == "pending" and row.reconciliation_status is None
                assert row.claim_generation == 0 and row.dispatch_started_at is None
                cnt = (await db.execute(select(__import__("models").ExecutionRecoveryAudit))).scalars().all()
                assert len(cnt) == 4                       # 4 logical requests → 4 append-only rows
        finally:
            await eng.dispose()
    _run(go())


def test_not_open_and_not_found_and_unverified(monkeypatch):
    orv, eng, S = _fresh_db(monkeypatch)

    async def go():
        try:
            now = datetime.now(timezone.utc)
            # non-disputed (success) → not_open
            done = await _seed(S, status="success")
            assert (await orv.resolve(log_id=done, tenant_user_id="tenant-A", action="close",
                    actor_id="op", correlation_id=str(uuid.uuid4()), reason_code=None, now=now)).status == "not_open"
            # cross-tenant → not_found
            mine = await _seed(S, uid="tenant-B")
            assert (await orv.resolve(log_id=mine, tenant_user_id="tenant-A", action="close",
                    actor_id="op", correlation_id=str(uuid.uuid4()), reason_code=None, now=now)).status == "not_found"
            # unverified pending (bad fingerprint): confirm denied, close allowed
            bad = await _seed(S, fp="fp1:" + "b" * 64)
            assert (await orv.resolve(log_id=bad, tenant_user_id="tenant-A", action="confirm_applied",
                    actor_id="op", correlation_id=str(uuid.uuid4()), reason_code=None, now=now)).status == "unverified"
            assert (await orv.resolve(log_id=bad, tenant_user_id="tenant-A", action="close",
                    actor_id="op", correlation_id=str(uuid.uuid4()), reason_code=None, now=now)).status == "ok"
        finally:
            await eng.dispose()
    _run(go())


def test_idempotency_cached_and_mismatch(monkeypatch):
    orv, eng, S = _fresh_db(monkeypatch)
    from models import ExecutionRecoveryAudit
    from sqlalchemy import select, func

    async def go():
        try:
            now = datetime.now(timezone.utc)
            r1 = await _seed(S)
            r2 = await _seed(S)
            cid = str(uuid.uuid4())
            a = await orv.resolve(log_id=r1, tenant_user_id="tenant-A", action="close", actor_id="op",
                                  correlation_id=cid, reason_code=None, now=now)
            assert a.status == "ok"
            # same key+log+action+reason+actor+tenant → cached, no second audit
            b = await orv.resolve(log_id=r1, tenant_user_id="tenant-A", action="close", actor_id="op",
                                  correlation_id=cid, reason_code=None, now=now)
            assert b.status == "cached" and b.audit["audit_id"] == a.audit["audit_id"]
            # same key + different action → mismatch
            assert (await orv.resolve(log_id=r1, tenant_user_id="tenant-A", action="confirm_applied",
                    actor_id="op", correlation_id=cid, reason_code=None, now=now)).status == "mismatch"
            # same key + different log → mismatch
            assert (await orv.resolve(log_id=r2, tenant_user_id="tenant-A", action="close",
                    actor_id="op", correlation_id=cid, reason_code=None, now=now)).status == "mismatch"
            # same key + different reason → mismatch
            assert (await orv.resolve(log_id=r1, tenant_user_id="tenant-A", action="close",
                    actor_id="op", correlation_id=cid, reason_code="stale_pending_review", now=now)).status == "mismatch"
            async with S() as db:
                n = (await db.execute(select(func.count()).select_from(ExecutionRecoveryAudit))).scalar()
                assert n == 1                              # only the first request ever wrote a row
        finally:
            await eng.dispose()
    _run(go())


# ── CSRF narrow exemption predicate ──────────────────────────────────────────────
def test_csrf_exemption_is_narrow():
    from csrf import _is_recovery_resolution_post as ex
    uid = "3f2504e0-4f89-41d3-9a0c-0305e82c3301"
    # authorize-retry became a valid exempt action in C3C2 (still one exact POST pattern per action).
    for act in ("confirm-applied", "confirm-not-applied", "close", "authorize-retry"):
        assert ex("POST", f"/api/internal/recovery/operations/{uid}/{act}") is True
    # not POST
    assert ex("GET", f"/api/internal/recovery/operations/{uid}/close") is False
    assert ex("DELETE", f"/api/internal/recovery/operations/{uid}/close") is False
    # unknown action / extra suffix / missing log id / lookalikes
    assert ex("POST", f"/api/internal/recovery/operations/{uid}/some-other-action") is False
    assert ex("POST", f"/api/internal/recovery/operations/{uid}/close/extra") is False
    assert ex("POST", "/api/internal/recovery/operations//close") is False
    assert ex("POST", f"/api/internal/recovery/operations/{uid}") is False
    assert ex("POST", f"/api/internal/recovery/evil/{uid}/close") is False
    assert ex("POST", f"/api/seller/operations/{uid}/close") is False


# ── AST / source guards ───────────────────────────────────────────────────────
def _src(*rel):
    return _BE.joinpath(*rel).read_text(encoding="utf-8")


def _imports(src):
    tree = ast.parse(src)
    mods = set()
    for n in ast.walk(tree):
        if isinstance(n, ast.Import):
            mods.update(a.name for a in n.names)
        elif isinstance(n, ast.ImportFrom):
            mods.add(n.module or "")
            mods.update(f"{n.module}.{a.name}" for a in n.names)
    return mods


def test_writer_and_router_no_executor_or_provider_imports():
    banned = ("executor", "wb_client", "ozon_client", "yandex", "ozon_performance", "action_catalog",
              "credential_vault", "reviews", "reown_sweep", "recovery_sweep", "reconcile_read")
    for src in (_src("services", "marketplace", "recovery", "operator_resolve.py"),
                _src("routers", "internal_recovery.py")):
        for m in _imports(src):
            assert not any(b in m for b in banned), f"C3B must not import {m}"


def test_no_dispatch_retry_authorize_tokens():
    # The C3B writer must never dispatch or authorize a retry. Scoped to operator_resolve.py only — the
    # router now also hosts the SEPARATE C3C2 authorize-retry endpoint (its own module/guards), so the
    # router legitimately references authorize_retry / retry_authorized.
    src = _src("services", "marketplace", "recovery", "operator_resolve.py")
    for tok in ("executor.execute", ".execute(db=", "spec.dispatch", ".dispatch(", ".revert(",
                "publish_feedback_answer", "run_reown_sweep", "retry_authorized", "authorize_retry",
                "supported_for_retry ="):
        assert tok not in src, f"C3B writer must not reference {tok}"


def test_audit_is_append_only_in_source():
    src = _src("services", "marketplace", "recovery", "operator_resolve.py")
    low = src.lower()
    for verb in ("update execution_recovery_audit", "delete from execution_recovery_audit",
                 "delete(executionrecoveryaudit", ".delete()"):
        assert verb not in low, f"audit must be append-only — found {verb!r}"
    # the ONLY execution_logs SET writes are the three denormalized fields (guarded UPDATE not used here;
    # ORM attribute writes) — assert no forbidden field is assigned in the writer.
    for forbidden in ("row.status =", "row.dispatch_started_at =", "row.claim_generation =",
                      "row.attempt_count =", "row.reown_count =", "row.reconciliation_status =",
                      "row.idempotency_key =", "row.request_fingerprint =", "row.payload =",
                      "row.result =", "row.reverted_from ="):
        assert forbidden not in src, f"C3B must not write {forbidden}"
    assert "row.manual_resolution =" in src and "row.resolved_by =" in src and "row.resolved_at =" in src


def test_scrubber_masks_internal_and_idempotency_keys():
    from services.sentry_setup import _is_sensitive_key
    assert _is_sensitive_key("X-Internal-Key") and _is_sensitive_key("Idempotency-Key")


def test_body_forbids_extra_fields():
    from routers.internal_recovery import ResolveBody
    with pytest.raises(Exception):
        ResolveBody(reason_code="operator_closed_no_action", user_id="x")


def test_single_head_is_rob():
    from alembic.config import Config
    from alembic.script import ScriptDirectory
    assert ScriptDirectory.from_config(Config("alembic.ini")).get_heads() == ["csr1a2b3c4d01"]
