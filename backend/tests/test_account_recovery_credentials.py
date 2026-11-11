"""Ordinary 30-day recovery preserves owner credentials until and after email proof."""
from datetime import datetime, timedelta, timezone
from http.cookies import SimpleCookie
from types import SimpleNamespace
from unittest.mock import Mock

import bcrypt
import pytest
from fastapi import HTTPException, Request, Response

from test_erasure_inventory import inventory_db  # noqa: F401 -- existing disposable fixture
from auth_cookie import cookie_name
from models.user import User
from routers import auth
from routers.referrals import close_account
from schemas.account_closure import AccountClosureRequest
from schemas.auth import UserRegister
from services import account_closure as lifecycle
from services.account_closure_policy import POLICY_VERSION

START = datetime(2026, 11, 10, 12, tzinfo=timezone.utc)
OWNER_PASSWORD = 'Synthetic-Owner-Password-20261111!'
OWNER_NAME = 'Synthetic original owner'
OWNER_IP = '192.0.2.44'
PROPOSED = 'Untrusted-Proposed-Password-20261111!'
SECOND_PROPOSED = 'Another-Untrusted-Password-20261111!'
DEADLINE = (START + timedelta(days=30)).isoformat()


@pytest.fixture
def runtime(monkeypatch):
    value = SimpleNamespace(clock=[START], delivered=True, events=[])

    async def mail(email, name, token):
        value.events.append(dict(email=email, name=name, token=token, delivered=value.delivered))
        return value.delivered

    async def throttle(*args, **kwargs):
        return None

    value.hash_password = Mock(wraps=auth.hash_password)
    monkeypatch.setattr(lifecycle, 'utc_now', lambda: value.clock[0])
    monkeypatch.setattr(auth, 'send_verification_email', mail)
    monkeypatch.setattr(auth, '_throttle_or_429', throttle)
    monkeypatch.setattr(auth, 'hash_password', value.hash_password)
    return value


def request_from(ip):
    return Request({'type': 'http', 'method': 'POST', 'path': '/api/auth/register',
                    'scheme': 'https', 'headers': [], 'client': (ip, 12345),
                    'server': ('synthetic.example.test', 443)})


def credentials(user):
    return user.hashed_password, user.name, user.registered_ip


def assert_owner_credentials(user, original, runtime, proposed=(PROPOSED,)):
    # Field comparison comes first so baseline RED identifies the persisted mutation.
    assert credentials(user) == original
    assert auth.verify_password(OWNER_PASSWORD, user.hashed_password) is True
    for password in proposed:
        assert auth.verify_password(password, user.hashed_password) is False
    runtime.hash_password.assert_not_called()


async def seed_and_close(db, uid):
    user = await db.get(User, uid)
    stored_hash = bcrypt.hashpw(OWNER_PASSWORD.encode(), bcrypt.gensalt(rounds=4)).decode()
    original = stored_hash, OWNER_NAME, OWNER_IP
    user.hashed_password, user.name, user.registered_ip = original
    user.is_verified = True
    await db.commit()
    closed = await close_account(AccountClosureRequest(policy_version=POLICY_VERSION, confirm=True), db, user)
    await db.refresh(user)
    assert credentials(user) == original
    assert user.closure_policy_version == POLICY_VERSION and user.deleted_at is not None
    assert closed['state'] == 'closed_recoverable' and closed['erasure_complete'] is False
    assert closed['recovery_deadline'] == DEADLINE
    snapshot = dict(deleted_at=user.deleted_at, started_at=user.closure_started_at)
    return user, original, snapshot


async def request_recovery(db, user, *, name='UNTRUSTED recovery name', password=PROPOSED,
                           ip='203.0.113.91'):
    request = request_from(ip)
    assert auth._client_ip(request) == ip and ip != OWNER_IP
    return await auth.register(UserRegister(email=user.email, name=name, password=password, consent=True),
                               request, db)


def assert_pending(user, snapshot):
    assert user.deleted_at == snapshot['deleted_at'] and user.is_verified is False
    assert user.closure_policy_version == POLICY_VERSION
    assert user.closure_started_at == snapshot['started_at']
    assert user.closure_restore_requested_at is not None
    assert lifecycle.require_recovery_window(user)['recovery_deadline'] == DEADLINE


async def confirm(db, user, token, original, snapshot, runtime, proposed=(PROPOSED,)):
    response = Response()
    result = await auth.verify_email(response, token, db)
    await db.refresh(user)
    assert result.login_required is True and 'user' not in result.model_dump()
    cookie = SimpleCookie()
    cookie.load(response.headers['set-cookie'])
    assert cookie[cookie_name()].value == '' and cookie[cookie_name()]['max-age'] == '0'
    assert_owner_credentials(user, original, runtime, proposed)
    assert user.deleted_at is None and user.is_verified is True and user.is_restored is True
    assert user.verification_token is None and user.closure_restore_requested_at is None
    assert user.closure_policy_version == POLICY_VERSION
    assert user.closure_started_at == snapshot['started_at']
    assert lifecycle.require_recovery_window(user)['recovery_deadline'] == DEADLINE


@pytest.mark.parametrize('delivered', [True, False], ids=['mail-captured', 'mail-failed'])
def test_ordinary_recovery_request_never_replaces_credentials_or_profile(inventory_db, runtime, delivered):
    loop, _, db, owners = inventory_db
    runtime.delivered = delivered

    async def exercise():
        user, original, snapshot = await seed_and_close(db, owners[0])
        assert_owner_credentials(user, original, runtime)
        runtime.clock[0] = START + timedelta(days=3)
        response = await request_recovery(db, user)
        await db.refresh(user)
        assert_owner_credentials(user, original, runtime)
        assert_pending(user, snapshot)
        assert response.verification_email_sent is delivered
        assert len(runtime.events) == 1
        event = runtime.events[0]
        assert event['email'] == user.email and event['name'] == OWNER_NAME
        assert event['delivered'] is delivered and event['token'] == user.verification_token

    loop.run_until_complete(exercise())


def test_email_confirmation_preserves_original_credentials_profile_and_ip(inventory_db, runtime):
    loop, _, db, owners = inventory_db

    async def exercise():
        user, original, snapshot = await seed_and_close(db, owners[0])
        runtime.clock[0] = START + timedelta(days=7)
        response = await request_recovery(db, user)
        await db.refresh(user)
        assert_owner_credentials(user, original, runtime)
        assert_pending(user, snapshot)
        assert response.verification_email_sent is True and len(runtime.events) == 1
        await confirm(db, user, runtime.events[0]['token'], original, snapshot, runtime)

    loop.run_until_complete(exercise())


def test_repeated_recovery_rotates_only_email_proof_without_extending_window(inventory_db, runtime):
    loop, _, db, owners = inventory_db

    async def exercise():
        user, original, snapshot = await seed_and_close(db, owners[0])
        runtime.clock[0] = START + timedelta(days=2)
        await request_recovery(db, user)
        await db.refresh(user)
        assert_owner_credentials(user, original, runtime)
        assert_pending(user, snapshot)
        first_token = runtime.events[0]['token']
        runtime.clock[0] = START + timedelta(days=29)
        await request_recovery(db, user, name='UNTRUSTED second profile', password=SECOND_PROPOSED,
                               ip='203.0.113.92')
        await db.refresh(user)
        assert_owner_credentials(user, original, runtime, (PROPOSED, SECOND_PROPOSED))
        assert_pending(user, snapshot)
        assert len(runtime.events) == 2
        latest_token = runtime.events[1]['token']
        assert latest_token != first_token and user.verification_token == latest_token
        assert all(event['email'] == user.email and event['name'] == OWNER_NAME for event in runtime.events)
        with pytest.raises(HTTPException) as error:
            await auth.verify_email(Response(), first_token, db)
        assert error.value.status_code == 400
        await db.rollback()
        user = await db.get(User, owners[0])
        assert_owner_credentials(user, original, runtime, (PROPOSED, SECOND_PROPOSED))
        assert_pending(user, snapshot)
        await confirm(db, user, latest_token, original, snapshot, runtime, (PROPOSED, SECOND_PROPOSED))

    loop.run_until_complete(exercise())
