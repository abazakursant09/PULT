"""
F1.2b-a — Verification audit and per-scope credential state.

Two rules carry this slice, and both exist to stop the system from lying about a seller's
credentials in the two ways that would hurt most:

  * A TEMPORARY outcome must never change persisted state. A Wildberries outage, a rate
    limit or a timeout means "we do not know" — if any of them could write state, an
    outage would mark every seller's working token invalid and demand a re-entry.

  * `decrypt_failure` is not a credential failure. It fires locally, before any request
    leaves us, and means OUR key cannot read OUR ciphertext — most plausibly because
    CRED_ENC_KEY changed (one Fernet key, no rotation support). Calling that
    `invalid_credentials` would turn a key rotation into a product-wide false alarm.

And because Wildberries tokens are CATEGORY-scoped, verification lives per credential
(= per scope), never per connection. The connection carries only a rollup, and that rollup
reads persisted credential states — never an attempt outcome, which is how a temporary
result could otherwise sneak back in through the side door.
"""
import asyncio
import os
import tempfile
import uuid
from datetime import datetime, timedelta
from pathlib import Path

import pytest
import sqlalchemy as sa
from alembic import command
from alembic.script import ScriptDirectory
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from database import Base
import models  # noqa: F401  (registers tables)
from models.api_credential import ApiCredential
from models.connection_verification_attempt import ConnectionVerificationAttempt
from models.marketplace_account import MarketplaceAccount
from models.marketplace_connection import MarketplaceConnection
from models.user import User
from models.workspace import Workspace

from routers.connections import create_connection
from schemas.marketplace import ConnectionCreate
from services.marketplace import credential_vault, executor
from services.marketplace.verification import projection
from services.marketplace.verification import service as vsvc
from services.marketplace.verification.taxonomy import (
    OUTCOME_META, ErrorCategory, VerificationOutcome, VerificationResult, meta,
)
from services.marketplace.verification.service import NullVerifier

REV = "pad1a2b3c4d01"
PRIOR = "cri1a2b3c4d01"
TABLE = "connection_verification_attempts"

O = VerificationOutcome

# Outcomes that are allowed to write ApiCredential.verification_status.
STATE_CHANGING = {
    O.VERIFIED, O.INVALID_CREDENTIALS, O.REVOKED,
    O.MISSING_SCOPE, O.TARIFF_RESTRICTED, O.OWNERSHIP_CONFLICT,
}
# Everything else must leave persisted state untouched.
NON_STATE_CHANGING = set(O) - STATE_CHANGING


# ── helpers ──────────────────────────────────────────────────────────────────

def _run(c):
    return asyncio.run(c)


def _rows(sync_url, stmt):
    eng = sa.create_engine(sync_url)
    try:
        with eng.connect() as c:
            return c.execute(sa.text(stmt)).fetchall()
    finally:
        eng.dispose()


def _migrated_db(monkeypatch, *, to="head"):
    tmp = os.path.join(tempfile.mkdtemp(), "verif_test.db")
    monkeypatch.setenv("ALEMBIC_DATABASE_URL", f"sqlite+aiosqlite:///{tmp}")
    sync_url = f"sqlite:///{tmp}"
    import db_migrations as dbm
    cfg = dbm._alembic_config()
    command.upgrade(cfg, to)
    return cfg, sync_url


async def _orm_session():
    e = create_async_engine("sqlite+aiosqlite://",
                            connect_args={"check_same_thread": False}, poolclass=StaticPool)
    async with e.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    return sessionmaker(e, class_=AsyncSession, expire_on_commit=False)()


async def _connection(db, *, marketplace="wildberries"):
    user = User(id=str(uuid.uuid4()), email=f"{uuid.uuid4()}@b.com", name="U",
                hashed_password="x")
    db.add(user)
    ws = Workspace(id=str(uuid.uuid4()), owner_user_id=user.id, created_at=datetime.utcnow())
    db.add(ws)
    account = MarketplaceAccount(id=str(uuid.uuid4()), workspace_id=ws.id,
                                 marketplace=marketplace, identity_status="unverified")
    db.add(account)
    conn = MarketplaceConnection(
        id=str(uuid.uuid4()), user_id=user.id, marketplace=marketplace,
        status="connected", scopes=[], workspace_id=ws.id,
        marketplace_account_id=account.id,
    )
    db.add(conn)
    await db.commit()
    return user, conn


async def _credential(db, conn, scope, *, status="unverified", verified_at=None):
    cred = ApiCredential(
        id=str(uuid.uuid4()), connection_id=conn.id, scope=scope,
        secret_enc=b"ciphertext", verification_status=status, verified_at=verified_at,
    )
    db.add(cred)
    await db.commit()
    return cred


def _result(outcome, **kw):
    return VerificationResult(outcome=outcome, probe_key="test.probe",
                              adapter_version="1", **kw)


def _body(**kw):
    base = {"marketplace": "wildberries", "token": "t0ken", "scope": "prices"}
    base.update(kw)
    return ConnectionCreate(**base)


# ── A. schema / audit ────────────────────────────────────────────────────────

def test_alembic_single_head(monkeypatch):
    tmp = os.path.join(tempfile.mkdtemp(), "head.db")
    monkeypatch.setenv("ALEMBIC_DATABASE_URL", f"sqlite+aiosqlite:///{tmp}")
    import db_migrations as dbm
    heads = ScriptDirectory.from_config(dbm._alembic_config()).get_heads()
    assert heads == [REV], f"expected single head {REV}, got {heads}"


def test_attempt_table_holds_no_secret_and_no_response_body(monkeypatch):
    """An audit log is not a vault, and it is not a mirror of the marketplace's reply."""
    _cfg, sync_url = _migrated_db(monkeypatch)
    eng = sa.create_engine(sync_url)
    try:
        with eng.connect() as c:
            cols = {col["name"] for col in sa.inspect(c).get_columns(TABLE)}
    finally:
        eng.dispose()

    assert cols == {
        "id", "connection_id", "credential_id", "marketplace_account_id", "marketplace",
        "scope", "probe_key", "adapter_version", "outcome", "error_category",
        "http_status", "retry_after_seconds", "response_schema_status",
        "started_at", "finished_at", "created_at",
    }
    for forbidden in ("token", "secret", "secret_enc", "ciphertext", "response_body",
                      "body", "error_detail", "detail", "payload"):
        assert forbidden not in cols


def test_credential_verification_columns(monkeypatch):
    _cfg, sync_url = _migrated_db(monkeypatch)
    eng = sa.create_engine(sync_url)
    try:
        with eng.connect() as c:
            cols = {col["name"]: col for col in sa.inspect(c).get_columns("api_credentials")}
    finally:
        eng.dispose()

    assert cols["verification_status"]["nullable"] is False
    assert "unverified" in str(cols["verification_status"]["default"])
    assert cols["verified_at"]["nullable"] is True


def test_attempts_are_append_only_and_accumulate():
    async def go():
        db = await _orm_session()
        _u, conn = await _connection(db)
        cred = await _credential(db, conn, "prices")

        for outcome in (O.RATE_LIMITED, O.TIMEOUT, O.VERIFIED):
            await vsvc.record_attempt(db, connection=conn, credential=cred,
                                      result=_result(outcome))
        await db.commit()

        attempts = (await db.execute(
            sa.select(ConnectionVerificationAttempt)
            .order_by(ConnectionVerificationAttempt.created_at)
        )).scalars().all()
        assert len(attempts) == 3, "an earlier attempt was overwritten"
        assert [a.outcome for a in attempts] == ["rate_limited", "timeout", "verified"]
    _run(go())


def test_service_exposes_no_update_or_delete_of_attempts():
    """Append-only is a contract, so there must be no method that breaks it."""
    for name in dir(vsvc):
        assert not name.startswith(("update_attempt", "delete_attempt", "purge")), name


def test_attempt_without_credential_is_recorded():
    """A failure before any credential is selected is exactly what we must keep."""
    async def go():
        db = await _orm_session()
        _u, conn = await _connection(db)

        await vsvc.record_attempt(db, connection=conn, credential=None, scope="prices",
                                  result=_result(O.DECRYPT_FAILURE))
        await db.commit()

        attempt = (await db.execute(
            sa.select(ConnectionVerificationAttempt))).scalar_one()
        assert attempt.credential_id is None
        assert attempt.scope == "prices"
        assert attempt.outcome == "decrypt_failure"
        assert attempt.error_category == "internal"
    _run(go())


# ── B. taxonomy ──────────────────────────────────────────────────────────────

def test_every_outcome_has_explicit_metadata():
    assert set(OUTCOME_META) == set(O), "an outcome has no explicit metadata entry"


@pytest.mark.parametrize("outcome", sorted(NON_STATE_CHANGING, key=lambda o: o.value))
def test_non_state_changing_outcomes_never_touch_persisted_state(outcome):
    """Temporary results, decrypt failures and 'no probe' must leave state untouched."""
    assert meta(outcome).changes_state is False

    async def go():
        db = await _orm_session()
        _u, conn = await _connection(db)
        stamp = datetime.utcnow()
        cred = await _credential(db, conn, "prices", status="verified", verified_at=stamp)

        await vsvc.record_attempt(db, connection=conn, credential=cred,
                                  result=_result(outcome))
        await db.commit()
        await db.refresh(cred)
        await db.refresh(conn)

        assert cred.verification_status == "verified", \
            f"{outcome.value} destroyed a proven credential"
        assert cred.verified_at == stamp
        assert conn.verification_status == "verified"

        # ...but it IS audited
        assert (await db.execute(sa.select(ConnectionVerificationAttempt))).scalars().all()
    _run(go())


def test_decrypt_failure_is_internal_not_a_credential_verdict():
    m = meta(O.DECRYPT_FAILURE)
    assert m.error_category is ErrorCategory.INTERNAL
    assert m.changes_state is False
    assert m.permanent is True          # retrying with the same broken key changes nothing


def test_temporary_outcomes_are_retryable_and_permanent_ones_are_not():
    for outcome in (O.RATE_LIMITED, O.TIMEOUT, O.MARKETPLACE_UNAVAILABLE):
        assert meta(outcome).permanent is False
        assert meta(outcome).retryable is True
    for outcome in (O.VERIFIED, O.INVALID_CREDENTIALS, O.REVOKED, O.MISSING_SCOPE,
                    O.TARIFF_RESTRICTED, O.OWNERSHIP_CONFLICT):
        assert meta(outcome).permanent is True
        assert meta(outcome).retryable is False, \
            f"{outcome.value} must not be retried — it is a settled answer"


def test_verification_unsupported_never_creates_a_verified_state():
    assert meta(O.VERIFICATION_UNSUPPORTED).changes_state is False

    async def go():
        db = await _orm_session()
        _u, conn = await _connection(db)
        cred = await _credential(db, conn, "prices")

        result = await NullVerifier().verify(marketplace="yandex", scope="prices")
        assert result.outcome is O.VERIFICATION_UNSUPPORTED

        await vsvc.record_attempt(db, connection=conn, credential=cred, result=result)
        await db.commit()
        await db.refresh(cred)

        assert cred.verification_status == "unverified"
        assert cred.verified_at is None
    _run(go())


@pytest.mark.parametrize("outcome", sorted(STATE_CHANGING, key=lambda o: o.value))
def test_state_changing_outcomes_write_exactly_their_own_value(outcome):
    async def go():
        db = await _orm_session()
        _u, conn = await _connection(db)
        cred = await _credential(db, conn, "prices")

        await vsvc.record_attempt(db, connection=conn, credential=cred,
                                  result=_result(outcome))
        await db.commit()
        await db.refresh(cred)

        assert cred.verification_status == outcome.value
        if outcome is O.VERIFIED:
            assert cred.verified_at is not None
        else:
            assert cred.verified_at is None, "a failed verification kept a success stamp"
    _run(go())


# ── C. per-scope state ───────────────────────────────────────────────────────

def test_verifying_one_scope_does_not_verify_another():
    """The whole reason verification is per-credential: WB tokens are category-scoped."""
    async def go():
        db = await _orm_session()
        _u, conn = await _connection(db)
        prices = await _credential(db, conn, "prices")
        feedbacks = await _credential(db, conn, "feedbacks")

        await vsvc.record_attempt(db, connection=conn, credential=prices,
                                  result=_result(O.VERIFIED))
        await db.commit()
        await db.refresh(prices)
        await db.refresh(feedbacks)
        await db.refresh(conn)

        assert prices.verification_status == "verified"
        assert feedbacks.verification_status == "unverified"
        assert conn.verification_status == "unverified"   # not all scopes are proven
    _run(go())


def test_replacing_one_credential_resets_only_that_scope():
    async def go():
        db = await _orm_session()
        user, conn = await _connection(db)
        conn.scopes = ["prices", "feedbacks"]
        prices = await _credential(db, conn, "prices", status="verified",
                                   verified_at=datetime.utcnow())
        feedbacks = await _credential(db, conn, "feedbacks", status="verified",
                                      verified_at=datetime.utcnow())
        await db.commit()

        await create_connection(_body(scope="prices", token="rotated"), user, db)

        await db.refresh(prices)
        await db.refresh(feedbacks)
        assert prices.verification_status == "unverified", "rotated secret stayed 'verified'"
        assert prices.verified_at is None
        assert credential_vault.decrypt(prices.secret_enc) == "rotated"

        assert feedbacks.verification_status == "verified", \
            "an unrelated scope lost its proven state"
        assert feedbacks.verified_at is not None
    _run(go())


# ── D. rollup ────────────────────────────────────────────────────────────────

def test_rollup_no_credentials_is_unverified():
    assert projection.rollup_status([]) == "unverified"
    assert projection.rollup_verified_at([]) is None


def test_rollup_all_verified_is_verified_with_latest_timestamp():
    early = datetime(2026, 7, 1, 10, 0, 0)
    late = datetime(2026, 7, 5, 10, 0, 0)
    creds = [
        ApiCredential(scope="prices", verification_status="verified", verified_at=early),
        ApiCredential(scope="feedbacks", verification_status="verified", verified_at=late),
    ]
    assert projection.rollup_status(creds) == "verified"
    # the connection only became fully verified when its LAST scope did
    assert projection.rollup_verified_at(creds) == late


def test_rollup_one_unverified_among_verified_is_unverified():
    creds = [
        ApiCredential(scope="prices", verification_status="verified",
                      verified_at=datetime.utcnow()),
        ApiCredential(scope="feedbacks", verification_status="unverified", verified_at=None),
    ]
    assert projection.rollup_status(creds) == "unverified"
    assert projection.rollup_verified_at(creds) is None   # cleared, not stale


def test_rollup_failure_precedence_is_explicit_and_not_lexical():
    """ownership_conflict outranks everything — nothing the seller does here can fix it."""
    assert projection.FAILURE_PRECEDENCE == (
        "ownership_conflict", "invalid_credentials", "revoked",
        "missing_scope", "tariff_restricted",
    )

    def _creds(*states):
        return [ApiCredential(scope=f"s{i}", verification_status=s, verified_at=None)
                for i, s in enumerate(states)]

    # every failure beats verified and unverified
    for failure in projection.FAILURE_PRECEDENCE:
        assert projection.rollup_status(_creds("verified", failure)) == failure
        assert projection.rollup_status(_creds("unverified", failure)) == failure

    # and the precedence between failures holds pairwise, in order
    order = projection.FAILURE_PRECEDENCE
    for i, stronger in enumerate(order):
        for weaker in order[i + 1:]:
            assert projection.rollup_status(_creds(weaker, stronger)) == stronger
            assert projection.rollup_status(_creds(stronger, weaker)) == stronger

    # lexical ordering would have picked "invalid_credentials" over "ownership_conflict"
    assert projection.rollup_status(
        _creds("ownership_conflict", "invalid_credentials")) == "ownership_conflict"


def test_temporary_attempts_do_not_affect_the_rollup():
    async def go():
        db = await _orm_session()
        _u, conn = await _connection(db)
        stamp = datetime.utcnow() - timedelta(days=1)
        cred = await _credential(db, conn, "prices", status="verified", verified_at=stamp)
        await vsvc.refresh_connection_rollup(db, conn)
        await db.commit()
        assert conn.verification_status == "verified"

        for outcome in (O.RATE_LIMITED, O.TIMEOUT, O.MARKETPLACE_UNAVAILABLE,
                        O.MALFORMED_RESPONSE, O.SCHEMA_MISMATCH, O.ADAPTER_ERROR):
            await vsvc.record_attempt(db, connection=conn, credential=cred,
                                      result=_result(outcome, http_status=429))
        await db.commit()
        await db.refresh(conn)

        assert conn.verification_status == "verified", \
            "a marketplace outage flipped a verified connection"
        assert conn.verified_at == stamp
        assert len((await db.execute(
            sa.select(ConnectionVerificationAttempt))).scalars().all()) == 6
    _run(go())


def test_rollup_clears_connection_verified_at_on_failure():
    async def go():
        db = await _orm_session()
        _u, conn = await _connection(db)
        cred = await _credential(db, conn, "prices", status="verified",
                                 verified_at=datetime.utcnow())
        await vsvc.refresh_connection_rollup(db, conn)
        await db.commit()
        assert conn.verified_at is not None

        await vsvc.record_attempt(db, connection=conn, credential=cred,
                                  result=_result(O.INVALID_CREDENTIALS, http_status=401))
        await db.commit()
        await db.refresh(conn)

        assert conn.verification_status == "invalid_credentials"
        assert conn.verified_at is None
    _run(go())


# ── E. safety ────────────────────────────────────────────────────────────────

def test_verification_module_imports_no_marketplace_client():
    """The spine never reaches a marketplace client, a queue, or the diagnosis contour.

    HTTP and decryption each have exactly ONE sanctioned home in the spine (F1.2b-b):
    `transport.py` owns the probe's HTTP — deliberately not `base_client`, whose retries
    would turn a WB rate limit into a block — and `runner.py` owns the single decrypt
    boundary. Anywhere else they would be a leak, so the exemption is pinned to those two
    files by name rather than granted to the package.
    """
    root = Path(__file__).resolve().parents[1] / "services" / "marketplace" / "verification"
    exempt = {"transport.py": {"httpx"}, "runner.py": {"credential_vault"}}

    for path in root.glob("*.py"):
        imports = [ln for ln in path.read_text(encoding="utf-8").splitlines()
                   if ln.startswith(("import ", "from ")) or "import " in ln]
        allowed = exempt.get(path.name, set())
        for forbidden in ("wb_client", "ozon_client", "base_client", "httpx", "requests",
                          "credential_vault", "advisory_runtime", "ProducerSpec",
                          "AdvisoryRun", "redis"):
            if forbidden in allowed:
                continue
            assert not any(forbidden in ln for ln in imports), \
                f"{path.name} imports {forbidden} — the spine must not reach it"


def test_recording_an_attempt_never_decrypts(monkeypatch):
    calls = []
    monkeypatch.setattr(credential_vault, "decrypt",
                        lambda blob: calls.append(blob) or "PLAINTEXT")

    async def go():
        db = await _orm_session()
        _u, conn = await _connection(db)
        cred = await _credential(db, conn, "prices")
        await vsvc.record_attempt(db, connection=conn, credential=cred,
                                  result=_result(O.VERIFIED))
        await db.commit()
        assert calls == [], "the verification service decrypted a credential"
    _run(go())


def test_no_redis_dependency_added():
    reqs = (Path(__file__).resolve().parents[1] / "requirements.txt").read_text(
        encoding="utf-8").lower()
    assert "redis" not in reqs and "aioredis" not in reqs


# ── F. regression ────────────────────────────────────────────────────────────

def test_post_connections_stores_credentials_and_records_no_attempt():
    """Saving is not verifying: no probe ran, so there is nothing to audit."""
    async def go():
        db = await _orm_session()
        user = User(id=str(uuid.uuid4()), email="r@b.com", name="R", hashed_password="x")
        db.add(user)
        db.add(Workspace(id=str(uuid.uuid4()), owner_user_id=user.id,
                         created_at=datetime.utcnow()))
        await db.commit()

        out = await create_connection(_body(), user, db)

        conn = (await db.execute(sa.select(MarketplaceConnection))).scalar_one()
        cred = (await db.execute(sa.select(ApiCredential))).scalar_one()
        assert credential_vault.decrypt(cred.secret_enc) == "t0ken"
        assert cred.verification_status == "unverified"
        assert conn.status == "connected"                 # execution gate untouched
        assert conn.verification_status == "unverified"   # rollup of one unverified scope

        assert (await db.execute(
            sa.select(ConnectionVerificationAttempt))).scalars().all() == [], \
            "storing a credential fabricated a verification attempt"

        assert [s.scope for s in out.scopes_verification] == ["prices"]
        assert out.scopes_verification[0].verification_status == "unverified"
        assert out.scopes_verification[0].verified_at is None
    _run(go())


def test_repeated_post_reuses_connection_and_account():
    async def go():
        db = await _orm_session()
        user = User(id=str(uuid.uuid4()), email="rp@b.com", name="RP", hashed_password="x")
        db.add(user)
        db.add(Workspace(id=str(uuid.uuid4()), owner_user_id=user.id,
                         created_at=datetime.utcnow()))
        await db.commit()

        await create_connection(_body(scope="prices"), user, db)
        first = (await db.execute(sa.select(MarketplaceConnection))).scalar_one()
        account_id = first.marketplace_account_id

        await create_connection(_body(scope="advert"), user, db)

        conns = (await db.execute(sa.select(MarketplaceConnection))).scalars().all()
        accounts = (await db.execute(sa.select(MarketplaceAccount))).scalars().all()
        creds = (await db.execute(sa.select(ApiCredential))).scalars().all()
        assert len(conns) == 1 and conns[0].id == first.id
        assert len(accounts) == 1 and conns[0].marketplace_account_id == account_id
        assert {c.scope for c in creds} == {"prices", "advert"}
        assert conns[0].status == "connected"
    _run(go())


def test_executor_still_works_with_a_connected_unverified_connection():
    async def go():
        db = await _orm_session()
        user = User(id=str(uuid.uuid4()), email="ex@b.com", name="EX", hashed_password="x")
        db.add(user)
        db.add(Workspace(id=str(uuid.uuid4()), owner_user_id=user.id,
                         created_at=datetime.utcnow()))
        await db.commit()

        await create_connection(_body(scope="prices"), user, db)
        conn = (await db.execute(sa.select(MarketplaceConnection))).scalar_one()
        assert conn.verification_status == "unverified"

        resolved = await executor._resolve_connection(
            db, user_id=user.id, marketplace="wildberries", connection_id=None)
        assert resolved.id == conn.id
        assert await executor._resolve_token(db, conn.id, "prices") == "t0ken"
    _run(go())


def test_f1_2a_unique_constraint_still_holds():
    async def go():
        db = await _orm_session()
        _u, conn = await _connection(db)
        await _credential(db, conn, "prices")

        db.add(ApiCredential(id=str(uuid.uuid4()), connection_id=conn.id,
                             scope="prices", secret_enc=b"second"))
        with pytest.raises(sa.exc.IntegrityError):
            await db.commit()
        await db.rollback()
    _run(go())


# ── G. runner + API (F1.2b-b) ────────────────────────────────────────────────
#
# The runner is the spine's only decrypt boundary and its only adapter dispatch. These
# prove the rules that keep a probe from damaging a credential it never actually tested.

import ast                                                              # noqa: E402
from pathlib import Path                                                # noqa: E402

from fastapi import HTTPException                                       # noqa: E402
from services.marketplace.verification import runner as vrunner         # noqa: E402
from services.marketplace.verification.adapters.base import ProbeResponse   # noqa: E402
from services.marketplace.verification.transport import ProbeTransportError  # noqa: E402, F401
from routers.connections import verify_connection_scope                 # noqa: E402
from schemas.marketplace import VerifyRequest                           # noqa: E402

PING_OK = {"TS": "2026-07-12T11:19:05+03:00", "Status": "OK"}


class _Transport:
    """Scripted, offline. Records whether it was called at all."""

    def __init__(self, *responses):
        self.responses = list(responses)
        self.calls = 0

    async def request(self, req):
        self.calls += 1
        nxt = self.responses.pop(0)
        if isinstance(nxt, Exception):
            raise nxt
        return nxt


async def _wb_conn(db, *, scope="prices", secret="t0ken"):
    user = User(id=str(uuid.uuid4()), email=f"{uuid.uuid4()}@b.com", name="U",
                hashed_password="x")
    db.add(user)
    ws = Workspace(id=str(uuid.uuid4()), owner_user_id=user.id, created_at=datetime.utcnow())
    db.add(ws)
    acc = MarketplaceAccount(id=str(uuid.uuid4()), workspace_id=ws.id,
                             marketplace="wildberries", identity_status="unverified")
    db.add(acc)
    conn = MarketplaceConnection(
        id=str(uuid.uuid4()), user_id=user.id, marketplace="wildberries",
        status="connected", scopes=[scope], workspace_id=ws.id,
        marketplace_account_id=acc.id,
    )
    db.add(conn)
    cred = ApiCredential(id=str(uuid.uuid4()), connection_id=conn.id, scope=scope,
                         secret_enc=credential_vault.encrypt(secret))
    db.add(cred)
    await db.commit()
    return user, conn, cred


def test_runner_decrypt_failure_records_attempt_calls_nothing_and_keeps_state(monkeypatch):
    """A changed CRED_ENC_KEY must not be reported to the seller as a bad token."""
    async def go():
        db = await _orm_session()
        user, conn, cred = await _wb_conn(db)
        cred.verification_status = "verified"
        cred.verified_at = datetime.utcnow()
        await db.commit()

        def _boom(_blob):
            raise ValueError("credential decryption failed (bad key or tampered)")
        monkeypatch.setattr(credential_vault, "decrypt", _boom)

        transport = _Transport()      # nothing scripted: any call would raise
        _c, credential, result = await vrunner.verify_credential(
            db, user_id=user.id, connection_id=conn.id, scope="prices", transport=transport)

        assert result.outcome is O.DECRYPT_FAILURE
        assert transport.calls == 0, "a decrypt failure still called the marketplace"

        await db.refresh(credential)
        assert credential.verification_status == "verified", \
            "a decrypt failure was reported as a credential problem"

        attempt = (await db.execute(
            sa.select(ConnectionVerificationAttempt))).scalar_one()
        assert attempt.outcome == "decrypt_failure"
        assert attempt.error_category == "internal"
    _run(go())


def test_runner_verified_updates_only_the_probed_scope():
    async def go():
        db = await _orm_session()
        user, conn, prices = await _wb_conn(db, scope="prices")
        feedbacks = ApiCredential(id=str(uuid.uuid4()), connection_id=conn.id,
                                  scope="feedbacks",
                                  secret_enc=credential_vault.encrypt("other"))
        db.add(feedbacks)
        await db.commit()

        transport = _Transport(ProbeResponse(status=200, headers={}, json=PING_OK))
        await vrunner.verify_credential(db, user_id=user.id, connection_id=conn.id,
                                        scope="prices", transport=transport)

        await db.refresh(prices)
        await db.refresh(feedbacks)
        await db.refresh(conn)
        assert prices.verification_status == "verified"
        assert feedbacks.verification_status == "unverified"
        assert conn.verification_status == "unverified"   # not every scope is proven
    _run(go())


def test_runner_temporary_result_preserves_credential_state():
    async def go():
        db = await _orm_session()
        user, conn, cred = await _wb_conn(db)
        cred.verification_status = "verified"
        stamp = datetime.utcnow()
        cred.verified_at = stamp
        await db.commit()

        transport = _Transport(
            ProbeResponse(status=429, headers={"x-ratelimit-retry": "30"}, json=None))
        _c, credential, result = await vrunner.verify_credential(
            db, user_id=user.id, connection_id=conn.id, scope="prices", transport=transport)

        assert result.outcome is O.RATE_LIMITED
        await db.refresh(credential)
        assert credential.verification_status == "verified"
        assert credential.verified_at == stamp
    _run(go())


def test_runner_cannot_apply_a_stale_result_after_the_secret_was_replaced():
    """The verdict describes a secret that no longer exists. It is recorded, not applied."""
    async def go():
        db = await _orm_session()
        user, conn, cred = await _wb_conn(db)

        class _ReplacingTransport:
            calls = 0

            async def request(self, req):
                # the seller rotates the token while the probe is in flight
                cred.secret_enc = credential_vault.encrypt("rotated")
                cred.updated_at = datetime.utcnow() + timedelta(seconds=1)
                await db.commit()
                self.calls += 1
                return ProbeResponse(status=200, headers={}, json=PING_OK)

        _c, credential, result = await vrunner.verify_credential(
            db, user_id=user.id, connection_id=conn.id, scope="prices",
            transport=_ReplacingTransport())

        assert result.outcome is O.VERIFIED          # the marketplace really did say this
        await db.refresh(credential)
        assert credential.verification_status == "unverified", \
            "a verdict about the OLD secret was applied to the new one"
        assert credential.verified_at is None

        # ...but the evidence is kept, honestly attributed and with its true outcome
        attempt = (await db.execute(
            sa.select(ConnectionVerificationAttempt))).scalar_one()
        assert attempt.outcome == "verified"
        assert attempt.credential_id == credential.id
    _run(go())


def test_runner_rollup_recomputes_through_the_existing_projection():
    async def go():
        db = await _orm_session()
        user, conn, _cred = await _wb_conn(db)

        transport = _Transport(ProbeResponse(status=200, headers={}, json=PING_OK))
        await vrunner.verify_credential(db, user_id=user.id, connection_id=conn.id,
                                        scope="prices", transport=transport)

        await db.refresh(conn)
        creds = (await db.execute(sa.select(ApiCredential))).scalars().all()
        assert conn.verification_status == projection.rollup_status(creds)
        assert conn.verified_at == projection.rollup_verified_at(creds)
    _run(go())


def test_verify_endpoint_is_owner_scoped():
    async def go():
        db = await _orm_session()
        _owner, conn, _cred = await _wb_conn(db)

        stranger = User(id=str(uuid.uuid4()), email="x@b.com", name="X", hashed_password="x")
        db.add(stranger)
        db.add(Workspace(id=str(uuid.uuid4()), owner_user_id=stranger.id,
                         created_at=datetime.utcnow()))
        await db.commit()

        with pytest.raises(HTTPException) as exc:
            await verify_connection_scope(conn.id, VerifyRequest(scope="prices"),
                                          stranger, db)
        assert exc.value.status_code == 404      # neutral: does not reveal it exists
    _run(go())


def test_verify_endpoint_reads_the_token_from_storage_and_returns_neutral_fields(monkeypatch):
    async def go():
        db = await _orm_session()
        user, conn, _cred = await _wb_conn(db, secret="stored-secret")

        seen = {}

        class _Capture:
            async def request(self, req):
                seen["auth"] = req.headers.get("Authorization")
                return ProbeResponse(status=200, headers={}, json=PING_OK)

        monkeypatch.setattr(vrunner, "ProbeTransport", lambda *a, **k: _Capture())

        out = await verify_connection_scope(conn.id, VerifyRequest(scope="prices"), user, db)

        # the token came from the vault, never from the request body
        assert seen["auth"] == "stored-secret"
        assert not hasattr(VerifyRequest(scope="prices"), "token")

        assert out.outcome == "verified"
        assert out.verification_status == "verified"
        assert out.connection_verification_status == "verified"
        assert out.marketplace == "wildberries"
        assert out.scope == "prices"

        # marketplace-neutral: no WB field, no token, no body, no identity
        payload = out.model_dump()
        assert set(payload) == {
            "connection_id", "marketplace", "scope", "outcome", "verification_status",
            "verified_at", "connection_verification_status", "connection_verified_at",
            "retry_after_seconds",
        }
        assert "stored-secret" not in str(payload)
    _run(go())


def test_verify_router_contains_no_marketplace_branch():
    src = (Path(__file__).resolve().parents[1] / "routers" / "connections.py").read_text(
        encoding="utf-8")
    tree = ast.parse(src)
    for node in ast.walk(tree):
        if isinstance(node, ast.Compare):
            code = ast.unparse(node).lower()
            if "marketplace" in code:
                for mp in ("wildberries", "ozon", "yandex"):
                    assert mp not in code, f"router branches on the marketplace: {code!r}"
