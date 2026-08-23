"""SECURITY-2D-1C-C3C1 — read-only resume eligibility: unit / SQLite matrix + source guards.

Real-PostgreSQL live-check coverage is in test_resume_eligibility_pg.py. This file proves the pure
structural matrix, the SQLite live checks, the reason-code contract, the single-dispatch invariant, and
that the recovery contour has NO dispatch path.
"""
import ast
import asyncio
import os
import pathlib
import tempfile
import uuid

import pytest
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import sessionmaker

from services.marketplace.operation_fingerprint import compute_fingerprint

_BE = pathlib.Path(__file__).resolve().parents[1]
_V1 = "v1:client:3f2504e0-4f89-41d3-9a0c-0305e82c3301"


def _run(coro):
    return asyncio.new_event_loop().run_until_complete(coro)


def _fresh(monkeypatch):
    d = tempfile.mkdtemp()
    db = os.path.join(d, "c3c1.db").replace("\\", "/")
    monkeypatch.setenv("ALEMBIC_DATABASE_URL", f"sqlite+aiosqlite:///{db}")
    from alembic import command
    from alembic.config import Config
    command.upgrade(Config("alembic.ini"), "head")
    eng = create_async_engine(f"sqlite+aiosqlite:///{db}")
    return eng, sessionmaker(eng, class_=AsyncSession, expire_on_commit=False)


async def _seed_conn(S, *, uid="tenant-A", cid="c1", marketplace="wb", status="connected",
                     scopes=None, scope="prices", with_cred=True):
    from models.marketplace_connection import MarketplaceConnection
    from models.api_credential import ApiCredential
    scopes = [scope] if scopes is None else scopes
    # Commit the connection FIRST (the api_credentials FK is enforced on real PG; no ORM relationship
    # orders the unit-of-work INSERTs).
    async with S() as db:
        db.add(MarketplaceConnection(id=cid, user_id=uid, marketplace=marketplace, status=status,
                                     scopes=scopes))
        await db.commit()
    if with_cred:
        async with S() as db:
            db.add(ApiCredential(id="cr-" + cid, connection_id=cid, scope=scope, secret_enc=b"x"))
            await db.commit()


async def _seed_row(S, *, rid=None, uid="tenant-A", cid="c1", marketplace="wb", action="set_price",
                    mode="manual_l3", status="pending", dsa=None, attempt=0, reown=0, key=None,
                    fp=None, payload=None, recon=None, manual=None):
    from models.execution_log import ExecutionLog
    rid = rid or str(uuid.uuid4())
    payload = {"offer_id": "O1", "price": 100} if payload is None else payload
    key = key or ("v1:client:" + str(uuid.uuid4()))
    if fp is None and isinstance(payload, dict):
        fp = compute_fingerprint(uid, cid, marketplace, action, mode, payload, None)
    from datetime import datetime, timezone
    dsa_val = datetime.now(timezone.utc) if dsa is True else dsa
    async with S() as db:
        db.add(ExecutionLog(id=rid, user_id=uid, connection_id=cid, marketplace=marketplace,
                            action_type=action, mode=mode, payload=payload, status=status,
                            idempotency_key=key, request_fingerprint=fp, attempt_count=attempt,
                            reown_count=reown, reconciliation_status=recon, manual_resolution=manual,
                            dispatch_started_at=dsa_val))
        await db.commit()
    return rid


async def _eval(S, rid, *, tenant="tenant-A", live=True):
    from services.marketplace.recovery import resume_eligibility as re
    from models.execution_log import ExecutionLog
    from sqlalchemy import select
    async with S() as db:
        row = (await db.execute(select(ExecutionLog).where(ExecutionLog.id == rid))).scalars().first()
        return await re.evaluate_resume(db, row, tenant_user_id=tenant, live=live)


# ── happy path ──────────────────────────────────────────────────────────────
def test_safe_pending_preliminary_eligible(monkeypatch):
    eng, S = _fresh(monkeypatch)

    async def go():
        try:
            await _seed_conn(S)
            rid = await _seed_row(S)
            r = await _eval(S, rid)
            assert r.eligible is True and r.reason_code == "preliminary_eligible"
            assert r.requires_live_token_resolution is True
            assert r.owned_generation == 0 and r.action_type == "set_price" and r.mode == "manual_l3"
        finally:
            await eng.dispose()
    _run(go())


# ── structural ineligibility matrix (live=False, pure) ───────────────────────
@pytest.mark.parametrize("over,reason", [
    (dict(uid_mismatch=True), "cross_tenant"),
    (dict(status="in_flight"), "status_not_pending"),
    (dict(status="success"), "status_not_pending"),
    (dict(status="ambiguous"), "status_not_pending"),
    (dict(status="failed"), "status_not_pending"),
    (dict(status="reverted"), "status_not_pending"),
    (dict(dsa=True), "dispatch_already_started"),
    (dict(attempt=1), "attempt_already_recorded"),
    (dict(key="review:not-a-uuid"), "invalid_operation_key"),
    (dict(fp="not-a-fp"), "invalid_fingerprint"),
    (dict(fp="fp1:" + "b" * 64), "fingerprint_mismatch"),
    (dict(payload=["not", "a", "dict"]), "invalid_payload"),
    (dict(action="stop_auto_promotion"), "contained_action"),
    (dict(action="totally_unknown"), "unsupported_action"),
    (dict(payload={"price": 100}), "invalid_payload"),          # set_price requires offer_id
    (dict(reown=5), "reown_limit_reached"),
    (dict(recon="target_not_observed"), "reconciliation_conflict"),
    (dict(recon="still_unknown"), "reconciliation_conflict"),
    (dict(recon="manual_attention"), "reconciliation_conflict"),
    (dict(recon="intent_observed"), "reconciliation_conflict"),
    (dict(manual="confirmed_applied"), "manual_already_applied"),
    (dict(manual="retry_authorized"), "already_retry_authorized"),
])
def test_structural_ineligible_matrix(monkeypatch, over, reason):
    eng, S = _fresh(monkeypatch)

    async def go():
        try:
            tenant = "tenant-A"
            seed = dict(over)
            if seed.pop("uid_mismatch", False):
                seed["uid"] = "tenant-B"
            rid = await _seed_row(S, **seed)
            r = await _eval(S, rid, tenant=tenant, live=False)
            assert r.eligible is False and r.reason_code == reason
        finally:
            await eng.dispose()
    _run(go())


def test_manual_closed_and_not_applied_structurally_allowed(monkeypatch):
    eng, S = _fresh(monkeypatch)

    async def go():
        try:
            for m in ("manual_closed", "confirmed_not_applied", None):
                rid = await _seed_row(S, manual=m)
                r = await _eval(S, rid, live=False)
                assert r.eligible is True and r.reason_code == "preliminary_eligible"
        finally:
            await eng.dispose()
    _run(go())


# ── live (SQLite) checks ─────────────────────────────────────────────────────
def test_live_connection_and_credential_matrix(monkeypatch):
    eng, S = _fresh(monkeypatch)

    async def go():
        try:
            # disconnected connection
            await _seed_conn(S, cid="c_disc", status="revoked")
            rid = await _seed_row(S, cid="c_disc")
            assert (await _eval(S, rid)).reason_code == "connection_disconnected"
            # missing connection
            rid2 = await _seed_row(S, cid="c_absent")
            assert (await _eval(S, rid2)).reason_code == "connection_missing"
            # missing credential
            await _seed_conn(S, cid="c_nocred", with_cred=False)
            rid3 = await _seed_row(S, cid="c_nocred")
            assert (await _eval(S, rid3)).reason_code == "credential_missing"
            # scope missing on connection
            await _seed_conn(S, cid="c_noscope", scopes=["feedbacks"], scope="prices", with_cred=True)
            rid4 = await _seed_row(S, cid="c_noscope")
            assert (await _eval(S, rid4)).reason_code in ("credential_missing", "scope_missing")
        finally:
            await eng.dispose()
    _run(go())


def test_live_automation_off_for_l4(monkeypatch):
    from config import settings
    monkeypatch.setattr(settings, "automation_enabled", False)
    eng, S = _fresh(monkeypatch)

    async def go():
        try:
            await _seed_conn(S)
            rid = await _seed_row(S, mode="automated_l4")
            assert (await _eval(S, rid)).reason_code == "automation_disabled"
        finally:
            await eng.dispose()
    _run(go())


def test_evaluate_resume_does_not_mutate(monkeypatch):
    eng, S = _fresh(monkeypatch)
    from models.execution_log import ExecutionLog
    from models import ExecutionRecoveryAudit
    from sqlalchemy import select, func

    async def go():
        try:
            await _seed_conn(S)
            rid = await _seed_row(S)
            async with S() as db:
                before = (await db.execute(select(ExecutionLog.status, ExecutionLog.manual_resolution,
                          ExecutionLog.dispatch_started_at, ExecutionLog.attempt_count,
                          ExecutionLog.claim_generation).where(ExecutionLog.id == rid))).first()
            await _eval(S, rid)
            await _eval(S, rid)
            async with S() as db:
                after = (await db.execute(select(ExecutionLog.status, ExecutionLog.manual_resolution,
                         ExecutionLog.dispatch_started_at, ExecutionLog.attempt_count,
                         ExecutionLog.claim_generation).where(ExecutionLog.id == rid))).first()
                n = (await db.execute(select(func.count()).select_from(ExecutionRecoveryAudit))).scalar()
            assert tuple(before) == tuple(after) and n == 0
        finally:
            await eng.dispose()
    _run(go())


# ── source guards ─────────────────────────────────────────────────────────────
def _src(*rel):
    return _BE.joinpath(*rel).read_text(encoding="utf-8")


def test_single_spec_dispatch_call_site_in_backend():
    # Count the actual CALL form `await spec.dispatch(` (comments/docstrings that mention the name are
    # not calls). Exactly one, in executor.py inside _dispatch_and_finalize.
    hits = []
    for p in _BE.rglob("*.py"):
        if "tests" in p.parts or "alembic" in p.parts:
            continue
        for i, line in enumerate(p.read_text(encoding="utf-8").splitlines(), 1):
            if "await spec.dispatch(" in line:
                hits.append(f"{p.as_posix().split('/backend/')[-1]}:{i}")
    assert len(hits) == 1, hits
    assert hits[0].startswith("services/marketplace/executor.py"), hits


def test_dispatch_call_site_inside_finalize():
    src = _src("services", "marketplace", "executor.py")
    idx = src.index("async def _dispatch_and_finalize")
    nxt = src.index("\nasync def ", idx + 1)
    assert "spec.dispatch(" in src[idx:nxt]
    # execute() delegates to the helper, does not itself call spec.dispatch
    ex = src.index("async def execute(")
    ex_end = src.index("\nasync def ", ex + 1)
    assert "spec.dispatch(" not in src[ex:ex_end]
    assert "_dispatch_and_finalize(" in src[ex:ex_end]


def test_resume_eligibility_is_read_only_and_dispatch_free():
    src = _src("services", "marketplace", "recovery", "resume_eligibility.py")
    for tok in ("spec.dispatch", ".execute(db=", "executor.execute", ".revert(", "_dispatch_and_finalize",
                "_FENCE_CAS", "_RESUME_CAS", "db.add", ".commit(", ".flush(", "credential_vault.decrypt",
                "acquire_bearer", "wb_client", "ozon_client", "run_reown_sweep"):
        assert tok not in src, f"resume_eligibility must not contain {tok!r}"
    low = src.lower()
    for verb in ("update execution_logs", "insert into", "delete from"):
        assert verb not in low, f"resume_eligibility must not write ({verb})"


def test_resume_eligibility_ast_no_mutation_calls():
    src = _src("services", "marketplace", "recovery", "resume_eligibility.py")
    tree = ast.parse(src)
    banned_attr = {"add", "commit", "flush", "delete", "merge"}
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
            assert node.func.attr not in banned_attr, f"forbidden ORM mutation call .{node.func.attr}()"


def test_c3c2_authorize_endpoint_present_and_gated():
    # C3C2 added the authorize-retry endpoint behind the redispatch gate. (This supersedes the C3C1
    # "no endpoint yet" guard.)
    router = _src("routers", "internal_recovery.py")
    assert "authorize-retry" in router and "_require_operator_redispatch" in router


def test_flag_default_off_and_eligibility_flag_agnostic():
    from config import Settings
    assert Settings().recovery_redispatch_enabled is False
    # The read-only eligibility module stays flag-agnostic (the real gate is the C3C2 router dependency,
    # which DOES read recovery_redispatch_enabled).
    assert "recovery_redispatch_enabled" not in _src("services", "marketplace", "recovery",
                                                     "resume_eligibility.py")


def test_reason_codes_closed_and_short():
    from services.marketplace.recovery import resume_eligibility as re
    assert max(len(x) for x in re._REASONS) <= 40


def test_single_head_rob():
    from alembic.config import Config
    from alembic.script import ScriptDirectory
    assert ScriptDirectory.from_config(Config("alembic.ini")).get_heads() == ["csr1a2b3c4d01"]
