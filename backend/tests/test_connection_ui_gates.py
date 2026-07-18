"""CONNECTION-UI — the two backend guarantees a self-service connection screen needs.

The connection row is marked status="connected" the moment a key is saved, BEFORE anything is
verified (routers/connections.py). So "connected" is not evidence that the connection works, and the
UI must not be the only thing standing between a broken key and running automation. Two guards:

  1. Ozon cannot be saved without a Client-Id. Ozon authenticates with a PAIR; a key alone can never
     reach the marketplace, and the probe would report it as invalid credentials — blaming the key
     for a missing field. Wildberries needs no Client-Id.
  2. A feedbacks credential we have PROVEN bad cannot be used to switch Auto Reviews on.
     Proven-bad means a permanent, state-changing verification verdict. A key that is merely
     unverified — or unverifiable, which is Ozon feedbacks today — still passes: absence of evidence
     is not evidence of failure, and blocking on it would lock sellers out of a working marketplace.
"""
import asyncio
import uuid
from datetime import datetime

import pytest
from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from database import Base
import models  # noqa: F401  register tables
from models.user import User
from models.workspace import Workspace
from models.automation_rule import AutomationRule
from models.marketplace_connection import MarketplaceConnection
from models.api_credential import ApiCredential
from schemas.marketplace import ConnectionCreate
from services.marketplace import credential_vault
from services.marketplace import review_automation_gate as gate
from services.marketplace.review_automation_gate import CONSENT_VERSION as _CV
from routers.connections import create_connection
from routers.automation import toggle_rule

_LOOP = asyncio.new_event_loop()


def _run(coro):
    return _LOOP.run_until_complete(coro)


async def _new_db():
    e = create_async_engine("sqlite+aiosqlite://",
                            connect_args={"check_same_thread": False}, poolclass=StaticPool)
    async with e.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    return sessionmaker(e, class_=AsyncSession, expire_on_commit=False)()


async def _user(db):
    uid = str(uuid.uuid4())
    user = User(id=uid, email=f"{uid}@e.com", name="S", hashed_password="x", is_verified=True)
    db.add(user)
    db.add(Workspace(id=str(uuid.uuid4()), owner_user_id=uid))
    await db.commit()
    return user


# ── 1. Ozon requires a Client-Id, Wildberries does not ───────────────────────
def test_ozon_without_client_id_is_rejected():
    db = _run(_new_db())
    user = _run(_user(db))
    body = ConnectionCreate(marketplace="ozon", token="key", scope="feedbacks")

    with pytest.raises(HTTPException) as e:
        _run(create_connection(body=body, current_user=user, db=db))

    assert e.value.status_code == 422
    assert "ozon_client_id" in str(e.value.detail)
    # nothing was persisted — no half-built connection left behind
    assert _run(db.execute(select(MarketplaceConnection))).scalars().first() is None


def test_ozon_with_blank_client_id_is_rejected():
    db = _run(_new_db())
    user = _run(_user(db))
    body = ConnectionCreate(marketplace="ozon", token="key", scope="feedbacks", ozon_client_id="   ")

    with pytest.raises(HTTPException) as e:
        _run(create_connection(body=body, current_user=user, db=db))
    assert e.value.status_code == 422


def test_ozon_with_client_id_is_accepted():
    db = _run(_new_db())
    user = _run(_user(db))
    body = ConnectionCreate(marketplace="ozon", token="key", scope="feedbacks", ozon_client_id="CID")

    out = _run(create_connection(body=body, current_user=user, db=db))

    assert out.marketplace == "ozon"
    conn = _run(db.execute(select(MarketplaceConnection))).scalars().first()
    assert conn.ozon_client_id == "CID"


def test_wildberries_needs_no_client_id():
    db = _run(_new_db())
    user = _run(_user(db))
    body = ConnectionCreate(marketplace="wildberries", token="key", scope="feedbacks")

    out = _run(create_connection(body=body, current_user=user, db=db))

    assert out.marketplace == "wildberries"
    assert "feedbacks" in out.scopes


def test_saved_secret_is_encrypted_and_never_returned():
    db = _run(_new_db())
    user = _run(_user(db))
    body = ConnectionCreate(marketplace="wildberries", token="super-secret", scope="feedbacks")

    out = _run(create_connection(body=body, current_user=user, db=db))

    assert "super-secret" not in out.model_dump_json()
    cred = _run(db.execute(select(ApiCredential))).scalars().first()
    assert cred.secret_enc != b"super-secret"
    assert credential_vault.decrypt(cred.secret_enc) == "super-secret"


# ── 2. A proven-bad feedbacks key cannot switch automation on ────────────────
async def _seed_rule(db, *, marketplace="wildberries", cred_status="unverified"):
    """A consented, disabled review rule whose connection carries a feedbacks key in `cred_status`."""
    user = await _user(db)
    conn = MarketplaceConnection(id=str(uuid.uuid4()), user_id=user.id, marketplace=marketplace,
                                 status="connected", scopes=["feedbacks"],
                                 ozon_client_id="CID" if marketplace == "ozon" else None)
    db.add(conn)
    await db.flush()
    db.add(ApiCredential(id=str(uuid.uuid4()), connection_id=conn.id, scope="feedbacks",
                         secret_enc=credential_vault.encrypt("tok"), meta={},
                         verification_status=cred_status))
    rule = AutomationRule(id=str(uuid.uuid4()), user_id=user.id, contour="reputation",
                          action_type="publish_review_response", mode="confirm", enabled=False,
                          guard={}, connection_id=conn.id,
                          consent_at=datetime.utcnow(), consent_version=_CV)
    db.add(rule)
    await db.commit()
    return user, conn, rule


@pytest.mark.parametrize("bad", ["invalid_credentials", "revoked", "missing_scope", "ownership_conflict"])
def test_proven_bad_credential_blocks_enabling(bad):
    db = _run(_new_db())
    user, _conn, rule = _run(_seed_rule(db, cred_status=bad))

    with pytest.raises(HTTPException) as e:
        _run(toggle_rule(rule_id=rule.id, current_user=user, db=db))

    assert e.value.status_code == 409
    assert "CREDENTIAL_INVALID" in str(e.value.detail)
    assert _run(db.execute(select(AutomationRule))).scalars().first().enabled is False


def test_unverified_credential_still_allows_enabling():
    """Not yet checked is not the same as broken — the seller is not locked out."""
    db = _run(_new_db())
    user, _conn, rule = _run(_seed_rule(db, cred_status="unverified"))

    out = _run(toggle_rule(rule_id=rule.id, current_user=user, db=db))

    assert out.enabled is True


def test_unverifiable_ozon_credential_still_allows_enabling():
    """Ozon feedbacks cannot be probed at all today; that absence must not block the seller."""
    db = _run(_new_db())
    user, _conn, rule = _run(_seed_rule(db, marketplace="ozon",
                                        cred_status="verification_unsupported"))

    out = _run(toggle_rule(rule_id=rule.id, current_user=user, db=db))

    assert out.enabled is True


def test_verified_credential_allows_enabling():
    db = _run(_new_db())
    user, _conn, rule = _run(_seed_rule(db, cred_status="verified"))

    assert _run(toggle_rule(rule_id=rule.id, current_user=user, db=db)).enabled is True


def test_disabling_is_never_blocked_by_a_bad_credential():
    """A seller must always be able to switch automation OFF, whatever the key's state."""
    db = _run(_new_db())
    user, _conn, rule = _run(_seed_rule(db, cred_status="invalid_credentials"))
    rule.enabled = True
    _run(db.commit())

    out = _run(toggle_rule(rule_id=rule.id, current_user=user, db=db))

    assert out.enabled is False


def test_worker_gate_also_refuses_a_proven_bad_credential():
    """The same verdict stops the publish worker, not just the toggle — fail closed on both paths."""
    db = _run(_new_db())
    user, _conn, rule = _run(_seed_rule(db, cred_status="revoked"))
    rule.enabled = True
    rule.mode = "auto"
    _run(db.commit())

    from config import settings
    original = settings.automation_enabled
    settings.automation_enabled = True
    try:
        with pytest.raises(gate.AutoPublishBlocked) as e:
            _run(gate.assert_auto_publish_allowed(db, rule.id, user.id))
        assert e.value.reason == "CREDENTIAL_INVALID"
    finally:
        settings.automation_enabled = original


def test_gate_helper_passes_when_no_credential_row_exists():
    """No key stored yet is not a proven failure — other gates cover that case."""
    db = _run(_new_db())
    user = _run(_user(db))
    conn = MarketplaceConnection(id=str(uuid.uuid4()), user_id=user.id, marketplace="wildberries",
                                 status="connected", scopes=[])
    db.add(conn)
    _run(db.commit())

    _run(gate.assert_feedbacks_credential_not_broken(db, conn.id))   # must not raise
