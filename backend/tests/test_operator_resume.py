"""SECURITY-2D-1C-C3C2 — authorize-and-resume: unit / SQLite behaviour + source guards.

Exhaustive real-PostgreSQL concurrency / crash / lost-ACK / full matrix lives in
test_operator_resume_pg.py. This file covers the happy dispatch, idempotency (cached/mismatch), the
CAS-empty conflict, ineligibility, provider-outcome classification, the two-flag gate, and the source
guards. A frozen ActionSpec is stubbed via dataclasses.replace on the catalog so the "provider dispatch"
is a local counter — never a live marketplace call.
"""
import asyncio
import os
import pathlib
import tempfile
import uuid
from dataclasses import replace
from datetime import datetime, timezone

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import sessionmaker

from services.marketplace.operation_fingerprint import compute_fingerprint

_BE = pathlib.Path(__file__).resolve().parents[1]


def _run(coro):
    return asyncio.new_event_loop().run_until_complete(coro)


def _fresh(monkeypatch):
    d = tempfile.mkdtemp()
    db = os.path.join(d, "c3c2.db").replace("\\", "/")
    monkeypatch.setenv("ALEMBIC_DATABASE_URL", f"sqlite+aiosqlite:///{db}")
    from alembic import command
    from alembic.config import Config
    command.upgrade(Config("alembic.ini"), "head")
    eng = create_async_engine(f"sqlite+aiosqlite:///{db}")
    S = sessionmaker(eng, class_=AsyncSession, expire_on_commit=False)
    from services.marketplace.recovery import operator_resume as orr
    monkeypatch.setattr(orr, "AsyncSessionLocal", S)
    from config import settings
    monkeypatch.setattr(settings, "recovery_operator_enabled", True)
    monkeypatch.setattr(settings, "recovery_redispatch_enabled", True)
    monkeypatch.setattr(settings, "recovery_operator_id", "operator-1")
    monkeypatch.setattr(settings, "recovery_operator_user_id", "tenant-A")
    monkeypatch.setattr(settings, "recovery_operator_api_key", "OP-KEY")
    return eng, S


def _stub_dispatch(monkeypatch, action="set_price", behavior="ok"):
    from services.marketplace import action_catalog
    from services.marketplace.errors import ExecutionError
    counter = {"n": 0}

    async def stub(token, payload, ctx):
        counter["n"] += 1
        if behavior == "ok":
            return {"api_request_id": "stub-req"}
        if behavior == "reject":
            raise ExecutionError(ExecutionError.VALIDATION, "deterministic provider rejection")
        if behavior == "timeout":
            raise ExecutionError(ExecutionError.TIMEOUT, "provider timeout")
        raise AssertionError("unknown behavior")

    orig = action_catalog.get(action)
    monkeypatch.setitem(action_catalog._CATALOG, action, replace(orig, dispatch=stub))
    return counter


async def _seed_conn(S, *, uid="tenant-A", cid="c1", marketplace="wb", status="connected",
                     scope="prices"):
    from models.marketplace_connection import MarketplaceConnection
    from models.api_credential import ApiCredential
    from services.marketplace import credential_vault
    async with S() as db:
        db.add(MarketplaceConnection(id=cid, user_id=uid, marketplace=marketplace, status=status,
                                     scopes=[scope]))
        await db.commit()
    async with S() as db:
        db.add(ApiCredential(id="cr-" + cid, connection_id=cid, scope=scope,
                             secret_enc=credential_vault.encrypt("realtoken")))
        await db.commit()


async def _seed_row(S, *, rid=None, uid="tenant-A", cid="c1", marketplace="wb", action="set_price",
                    mode="manual_l3", status="pending", dsa=None, attempt=0, key=None, payload=None):
    from models.execution_log import ExecutionLog
    rid = rid or str(uuid.uuid4())
    payload = {"offer_id": "O1", "price": 100} if payload is None else payload
    key = key or ("v1:client:" + str(uuid.uuid4()))
    fp = compute_fingerprint(uid, cid, marketplace, action, mode, payload, None)
    async with S() as db:
        db.add(ExecutionLog(id=rid, user_id=uid, connection_id=cid, marketplace=marketplace,
                            action_type=action, mode=mode, payload=payload, status=status,
                            idempotency_key=key, request_fingerprint=fp, attempt_count=attempt,
                            dispatch_started_at=dsa))
        await db.commit()
    return rid


async def _resume(rid, *, tenant="tenant-A", actor="operator-1", cid=None, reason=None):
    from services.marketplace.recovery import operator_resume as orr
    return await orr.resume(log_id=rid, tenant_user_id=tenant, actor_id=actor,
                            correlation_id=cid or str(uuid.uuid4()), reason_code=reason,
                            now=datetime.now(timezone.utc))


async def _row(S, rid):
    from models.execution_log import ExecutionLog
    async with S() as db:
        return (await db.execute(select(ExecutionLog).where(ExecutionLog.id == rid))).scalars().first()


async def _audit_count(S):
    from models import ExecutionRecoveryAudit
    from sqlalchemy import func
    async with S() as db:
        return (await db.execute(select(func.count()).select_from(ExecutionRecoveryAudit))).scalar()


# ── happy path: exactly one dispatch, atomic audit+fence, terminal success ──────
def test_happy_one_dispatch_success(monkeypatch):
    eng, S = _fresh(monkeypatch)
    prov = _stub_dispatch(monkeypatch)

    async def go():
        try:
            await _seed_conn(S)
            rid = await _seed_row(S)
            out = await _resume(rid)
            assert out.status == "dispatched" and out.dispatch_attempted is True
            assert out.terminal_status == "success" and prov["n"] == 1
            row = await _row(S, rid)
            assert row.status == "success" and row.manual_resolution == "retry_authorized"
            assert row.attempt_count == 1 and row.dispatch_started_at is not None
            assert row.resolved_by == "operator-1"
            assert await _audit_count(S) == 1
        finally:
            await eng.dispose()
    _run(go())


# ── idempotency: same correlation → cached, 0 second dispatch ───────────────────
def test_idempotency_cached_no_second_dispatch(monkeypatch):
    eng, S = _fresh(monkeypatch)
    prov = _stub_dispatch(monkeypatch)

    async def go():
        try:
            await _seed_conn(S)
            rid = await _seed_row(S)
            cid = str(uuid.uuid4())
            a = await _resume(rid, cid=cid)
            assert a.status == "dispatched" and prov["n"] == 1
            b = await _resume(rid, cid=cid)
            assert b.status == "cached" and b.dispatch_attempted is None and prov["n"] == 1
            assert b.audit["audit_id"] == a.audit["audit_id"]
            assert await _audit_count(S) == 1
        finally:
            await eng.dispose()
    _run(go())


def test_idempotency_mismatch(monkeypatch):
    eng, S = _fresh(monkeypatch)
    prov = _stub_dispatch(monkeypatch)

    async def go():
        try:
            await _seed_conn(S)
            r1 = await _seed_row(S)
            r2 = await _seed_row(S)
            cid = str(uuid.uuid4())
            await _resume(r1, cid=cid)
            out = await _resume(r2, cid=cid)          # same key, different log
            assert out.status == "conflict" and out.reason_code == "idempotency_mismatch"
            assert prov["n"] == 1
        finally:
            await eng.dispose()
    _run(go())


# ── ineligible rows never dispatch ──────────────────────────────────────────────
@pytest.mark.parametrize("over", [
    dict(status="in_flight"), dict(status="success"), dict(status="ambiguous"),
    dict(dsa=datetime.now(timezone.utc)), dict(attempt=1), dict(action="stop_auto_promotion"),
])
def test_ineligible_zero_dispatch(monkeypatch, over):
    eng, S = _fresh(monkeypatch)
    prov = _stub_dispatch(monkeypatch, action=over.get("action", "set_price"))

    async def go():
        try:
            await _seed_conn(S)
            rid = await _seed_row(S, **over)
            out = await _resume(rid)
            assert out.status == "conflict" and prov["n"] == 0
            assert await _audit_count(S) == 0
        finally:
            await eng.dispose()
    _run(go())


# ── provider outcome classification ──────────────────────────────────────────────
def test_provider_deterministic_rejection_failed(monkeypatch):
    eng, S = _fresh(monkeypatch)
    prov = _stub_dispatch(monkeypatch, behavior="reject")

    async def go():
        try:
            await _seed_conn(S)
            rid = await _seed_row(S)
            out = await _resume(rid)
            assert out.terminal_status == "failed" and prov["n"] == 1
            assert (await _row(S, rid)).status == "failed"
        finally:
            await eng.dispose()
    _run(go())


def test_provider_timeout_ambiguous(monkeypatch):
    eng, S = _fresh(monkeypatch)
    prov = _stub_dispatch(monkeypatch, behavior="timeout")

    async def go():
        try:
            await _seed_conn(S)
            rid = await _seed_row(S)
            out = await _resume(rid)
            assert out.terminal_status == "ambiguous" and prov["n"] == 1
            assert (await _row(S, rid)).status == "ambiguous"
        finally:
            await eng.dispose()
    _run(go())


# ── missing connection/credential ────────────────────────────────────────────────
def test_missing_connection_zero_dispatch(monkeypatch):
    eng, S = _fresh(monkeypatch)
    prov = _stub_dispatch(monkeypatch)

    async def go():
        try:
            rid = await _seed_row(S, cid="c_absent")            # no connection seeded
            out = await _resume(rid)
            assert out.status == "conflict" and prov["n"] == 0 and await _audit_count(S) == 0
        finally:
            await eng.dispose()
    _run(go())


# ── two-flag gate (dependency, no DB) ─────────────────────────────────────────────
def test_gate_flags_and_key(monkeypatch):
    from fastapi import HTTPException
    from config import settings
    from routers import internal_recovery as ir
    monkeypatch.setattr(settings, "recovery_operator_id", "op")
    monkeypatch.setattr(settings, "recovery_operator_user_id", "u1")
    monkeypatch.setattr(settings, "recovery_operator_api_key", "K")
    # operator OFF → 404
    monkeypatch.setattr(settings, "recovery_operator_enabled", False)
    monkeypatch.setattr(settings, "recovery_redispatch_enabled", True)
    with pytest.raises(HTTPException) as e:
        ir._require_operator_redispatch(x_internal_key="K")
    assert e.value.status_code == 404
    # operator ON, redispatch OFF → 404
    monkeypatch.setattr(settings, "recovery_operator_enabled", True)
    monkeypatch.setattr(settings, "recovery_redispatch_enabled", False)
    with pytest.raises(HTTPException) as e2:
        ir._require_operator_redispatch(x_internal_key="K")
    assert e2.value.status_code == 404
    # both ON, wrong key → 403
    monkeypatch.setattr(settings, "recovery_redispatch_enabled", True)
    with pytest.raises(HTTPException) as e3:
        ir._require_operator_redispatch(x_internal_key="WRONG")
    assert e3.value.status_code == 403
    # both ON, right key → ctx
    ctx = ir._require_operator_redispatch(x_internal_key="K")
    assert ctx.operator_id == "op" and ctx.user_id == "u1"


# ── source guards ─────────────────────────────────────────────────────────────
def _src(*rel):
    return _BE.joinpath(*rel).read_text(encoding="utf-8")


def test_single_dispatch_call_site():
    hits = []
    for p in _BE.rglob("*.py"):
        if "tests" in p.parts or "alembic" in p.parts:
            continue
        for i, line in enumerate(p.read_text(encoding="utf-8").splitlines(), 1):
            if "await spec.dispatch(" in line:
                hits.append(f"{p.as_posix().split('/backend/')[-1]}:{i}")
    assert len(hits) == 1 and hits[0].startswith("services/marketplace/executor.py"), hits


def test_resume_module_guards():
    src = _src("services", "marketplace", "recovery", "operator_resume.py")
    # resume must NOT call a provider client or spec.dispatch directly; it uses the shared helper.
    for tok in ("await spec.dispatch(", "wb_client.", "ozon_client.", "acquire_bearer(",
                ".execute(db="):
        assert tok not in src, f"operator_resume must not contain {tok!r}"
    # it DOES use the shared helper + its own resume CAS (not the ordinary fence CAS execution).
    assert "_dispatch_and_finalize(" in src and "_RESUME_CAS" in src
    assert "db.execute(_FENCE_CAS" not in src


def test_resume_cas_sets_only_allowed_fields():
    src = _src("services", "marketplace", "recovery", "operator_resume.py")
    seg = src.split("_RESUME_CAS = text(", 1)[1].split("RETURNING", 1)[0]
    set_clause = seg.split("SET ", 1)[1].split("WHERE", 1)[0]
    for f in ("status='in_flight'", "dispatch_started_at=:now", "attempt_count=attempt_count+1",
              "last_attempt_at=:now", "manual_resolution='retry_authorized'", "resolved_by=:actor",
              "resolved_at=:now"):
        assert f in set_clause, f"resume CAS SET missing {f}"
    for forbidden in ("claim_generation", "reown_count", "reconciliation_status", "idempotency_key",
                      "request_fingerprint", "payload", "result"):
        assert forbidden not in set_clause, f"resume CAS must not SET {forbidden}"


def test_fence_cas_unchanged_vs_master():
    import subprocess
    out = subprocess.run(["git", "diff", "f7a29d28a4566e70a7b2c63b50a50ad322d18dc7..HEAD", "--",
                          "backend/services/marketplace/executor.py"],
                         cwd=str(_BE.parent), capture_output=True, text=True)
    # C3C2 must not touch executor.py at all (extraction was C3C1, already on master).
    assert out.stdout.strip() == "", "C3C2 must not modify executor.py"


def test_csrf_authorize_retry_narrow():
    from csrf import _is_recovery_resolution_post as ex
    uid = str(uuid.uuid4())
    assert ex("POST", f"/api/internal/recovery/operations/{uid}/authorize-retry") is True
    assert ex("GET", f"/api/internal/recovery/operations/{uid}/authorize-retry") is False
    assert ex("POST", f"/api/internal/recovery/operations/{uid}/authorize-retry/x") is False
    assert ex("POST", "/api/internal/recovery/operations//authorize-retry") is False


def test_no_migration_single_head():
    from alembic.config import Config
    from alembic.script import ScriptDirectory
    assert ScriptDirectory.from_config(Config("alembic.ini")).get_heads() == ["rob1a2b3c4d01"]
