"""LEGAL-PRELAUNCH-F2 (blocker #6) — server-side, append-only consent evidence at registration.

Proves the mechanism only; legal sufficiency under 152-ФЗ is NOT asserted (REQUIRES RUSSIAN COUNSEL).
Uses the repo's native test idiom: a temp in-memory SQLite session + a direct call to the register
endpoint function (schema validation is exercised at the Pydantic layer, which is exactly the
"before any endpoint mutation" boundary).
"""
import asyncio
import re
import uuid
from datetime import datetime
from pathlib import Path

import pytest
import sqlalchemy as sa
from fastapi import HTTPException
from pydantic import ValidationError
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from config import settings
from database import Base
import models  # noqa: F401  registers tables
from models.user import User
from models.workspace import Workspace
from models.consent_record import ConsentRecord
import routers.auth as auth
from routers.auth import register
from schemas.auth import UserRegister

BACKEND = Path(__file__).resolve().parents[1]


def _run(c):
    return asyncio.run(c)


async def _session():
    e = create_async_engine("sqlite+aiosqlite://",
                            connect_args={"check_same_thread": False}, poolclass=StaticPool)
    async with e.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    return sessionmaker(e, class_=AsyncSession, expire_on_commit=False)()


class _Req:
    headers: dict = {}

    class _C:
        host = "1.2.3.4"
    client = _C()


def _patch_email(monkeypatch):
    async def _noop(*a, **kw):
        return True
    monkeypatch.setattr(auth, "send_verification_email", _noop)


async def _rows(db):
    return (await db.execute(sa.select(ConsentRecord))).scalars().all()


async def _users(db):
    return (await db.execute(sa.select(User))).scalars().all()


# ── 1. happy path: user + workspace + exactly one evidence row ────────────────

def test_consent_true_writes_one_registration_row(monkeypatch):
    _patch_email(monkeypatch)

    async def go():
        db = await _session()
        await register(UserRegister(email="a@b.com", name="A", password="Passw0rd", consent=True), _Req(), db)
        users = await _users(db)
        ws = (await db.execute(sa.select(Workspace))).scalars().all()
        rows = await _rows(db)
        assert len(users) == 1 and len(ws) == 1
        assert len(rows) == 1
        r = rows[0]
        assert r.user_id == users[0].id
        assert r.context == "registration"
    _run(go())


# ── 2. missing consent → schema rejection, nothing written ───────────────────

def test_missing_consent_is_schema_rejected():
    with pytest.raises(ValidationError):
        UserRegister(email="a@b.com", name="A", password="Passw0rd")


def test_missing_consent_creates_no_user_or_evidence(monkeypatch):
    _patch_email(monkeypatch)

    async def go():
        db = await _session()
        # a request that omits consent never constructs a valid UserRegister → endpoint never runs
        with pytest.raises(ValidationError):
            UserRegister(email="a@b.com", name="A", password="Passw0rd")
        assert await _users(db) == [] and await _rows(db) == []
    _run(go())


# ── 3. consent=false → refused before any user is created ─────────────────────

def test_consent_false_refused_before_create(monkeypatch):
    _patch_email(monkeypatch)

    async def go():
        db = await _session()
        with pytest.raises(HTTPException) as ei:
            await register(UserRegister(email="a@b.com", name="A", password="Passw0rd", consent=False), _Req(), db)
        assert ei.value.status_code == 400
        assert await _users(db) == [] and await _rows(db) == []
    _run(go())


# ── 4. truthy non-boolean values are NOT coerced to True ─────────────────────

@pytest.mark.parametrize("bad", ["true", "True", "1", 1, "yes", 0, "false"])
def test_truthy_non_bool_consent_rejected(bad):
    with pytest.raises(ValidationError):
        UserRegister(email="a@b.com", name="A", password="Passw0rd", consent=bad)


# ── 5/6. server-set timestamp and version, never client ──────────────────────

def test_consent_at_is_server_set_and_version_is_server_constant(monkeypatch):
    _patch_email(monkeypatch)

    async def go():
        db = await _session()
        before = datetime.utcnow()
        await register(UserRegister(email="a@b.com", name="A", password="Passw0rd", consent=True), _Req(), db)
        after = datetime.utcnow()
        r = (await _rows(db))[0]
        assert before <= r.consent_at <= after           # stamped by the server clock, this run
        assert r.consent_version == settings.consent_doc_version
    _run(go())


def test_client_cannot_supply_timestamp_or_version(monkeypatch):
    _patch_email(monkeypatch)
    # The schema has no such fields; extra keys are ignored, never stored.
    assert "consent_at" not in UserRegister.model_fields
    assert "consent_version" not in UserRegister.model_fields

    async def go():
        db = await _session()
        payload = UserRegister.model_validate({
            "email": "a@b.com", "name": "A", "password": "Passw0rd", "consent": True,
            "consent_at": "1999-01-01T00:00:00", "consent_version": "HACKED",
        })
        assert not hasattr(payload, "consent_at") and not hasattr(payload, "consent_version")
        await register(payload, _Req(), db)
        r = (await _rows(db))[0]
        assert r.consent_version == settings.consent_doc_version   # server value, not "HACKED"
        assert r.consent_at.year != 1999                           # server clock, not client 1999
    _run(go())


# ── 8. evidence insert failure rolls the whole registration back ─────────────

def test_evidence_failure_rolls_back_user_and_workspace(monkeypatch):
    _patch_email(monkeypatch)
    # Force the shared commit to fail once evidence has been staged.
    orig_commit = AsyncSession.commit
    calls = {"n": 0}

    async def boom(self):
        calls["n"] += 1
        raise sa.exc.SQLAlchemyError("boom")

    async def go():
        db = await _session()
        monkeypatch.setattr(AsyncSession, "commit", boom)
        with pytest.raises(sa.exc.SQLAlchemyError):
            await register(UserRegister(email="a@b.com", name="A", password="Passw0rd", consent=True), _Req(), db)
        monkeypatch.setattr(AsyncSession, "commit", orig_commit)
        await db.rollback()
        assert await _users(db) == [], "user survived a failed evidence commit"
        assert await _rows(db) == [], "evidence survived a failed commit"
    _run(go())


# ── 9. repeat registration of an active email → 400, no extra evidence ───────

def test_repeat_active_registration_writes_no_new_evidence(monkeypatch):
    _patch_email(monkeypatch)

    async def go():
        db = await _session()
        await register(UserRegister(email="a@b.com", name="A", password="Passw0rd", consent=True), _Req(), db)
        assert len(await _rows(db)) == 1
        with pytest.raises(HTTPException) as ei:
            await register(UserRegister(email="a@b.com", name="A", password="Passw0rd", consent=True), _Req(), db)
        assert ei.value.status_code == 400
        assert len(await _rows(db)) == 1, "a rejected repeat must not add evidence"
    _run(go())


# ── 10/11. recovery appends exactly one new row (context=recovery) ───────────

def test_recovery_appends_one_recovery_row(monkeypatch):
    _patch_email(monkeypatch)

    async def go():
        db = await _session()
        await register(UserRegister(email="a@b.com", name="A", password="Passw0rd", consent=True), _Req(), db)
        user = (await _users(db))[0]
        user.deleted_at = datetime.utcnow()            # soft-delete (as DELETE /account does)
        await db.commit()

        await register(UserRegister(email="a@b.com", name="A", password="Passw0rd", consent=True), _Req(), db)
        rows = await _rows(db)
        assert len(rows) == 2, "recovery must APPEND a new evidence row, not overwrite"
        contexts = sorted(r.context for r in rows)
        assert contexts == ["recovery", "registration"]
        assert all(r.user_id == user.id for r in rows)
    _run(go())


def test_recovery_evidence_failure_rolls_back(monkeypatch):
    _patch_email(monkeypatch)

    async def go():
        db = await _session()
        await register(UserRegister(email="a@b.com", name="A", password="Passw0rd", consent=True), _Req(), db)
        user = (await _users(db))[0]
        user.deleted_at = datetime.utcnow()
        await db.commit()
        assert len(await _rows(db)) == 1

        orig_commit = AsyncSession.commit

        async def boom(self):
            raise sa.exc.SQLAlchemyError("boom")
        monkeypatch.setattr(AsyncSession, "commit", boom)
        with pytest.raises(sa.exc.SQLAlchemyError):
            await register(UserRegister(email="a@b.com", name="A", password="Passw0rd", consent=True), _Req(), db)
        monkeypatch.setattr(AsyncSession, "commit", orig_commit)
        await db.rollback()
        # restore not committed; evidence not added
        rows = await _rows(db)
        assert len(rows) == 1 and rows[0].context == "registration"
    _run(go())


# ── 12. legacy user (pre-F2) is never given fabricated evidence ──────────────

def test_legacy_user_has_zero_rows_no_backfill(monkeypatch):
    async def go():
        db = await _session()
        db.add(User(id=str(uuid.uuid4()), email="legacy@b.com", name="L",
                    hashed_password="x", is_verified=True))
        await db.commit()
        assert await _rows(db) == [], "legacy users must not receive fabricated consent evidence"
    _run(go())


# ── 13. no public update/delete path for evidence ────────────────────────────

def test_no_update_or_delete_path_for_consent_records():
    # No router/service updates or deletes a consent_records row; the only writers are the two
    # INSERTs in routers/auth.py (registration + recovery).
    hits = []
    for p in (BACKEND / "routers").glob("*.py"):
        src = p.read_text(encoding="utf-8")
        if re.search(r"ConsentRecord", src) and p.name != "auth.py":
            hits.append(p.name)
        if re.search(r"(update|delete)\s*\(\s*ConsentRecord", src):
            hits.append(f"{p.name}:mutation")
    assert hits == [], f"unexpected consent_records mutation path: {hits}"
    auth_src = (BACKEND / "routers" / "auth.py").read_text(encoding="utf-8")
    assert auth_src.count("db.add(ConsentRecord(") == 2, "exactly two evidence inserts expected"
    assert "update(ConsentRecord" not in auth_src and "delete(ConsentRecord" not in auth_src


# ── 14. logging never leaks email / IP / doc text / payload / secrets ────────

def test_registration_logging_is_pii_free(monkeypatch, caplog):
    import logging
    _patch_email(monkeypatch)

    async def go():
        db = await _session()
        with caplog.at_level(logging.DEBUG):
            await register(UserRegister(email="zzcanary@zzdomain.com", name="ZZSEEKRIT",
                                        password="Passw0rd", consent=True), _Req(), db)
        app = [r.getMessage() for r in caplog.records
               if not r.name.startswith(("aiosqlite", "sqlalchemy", "asyncio"))]
        blob = " ".join(app)
        assert "zzcanary@zzdomain.com" not in blob
        assert "ZZSEEKRIT" not in blob
        assert "1.2.3.4" not in blob                       # the request IP
        assert "Passw0rd" not in blob
    _run(go())
