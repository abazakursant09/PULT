"""Synthetic DB/route tests; no email or provider calls."""
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from fastapi import FastAPI, HTTPException, Response
from httpx import ASGITransport, AsyncClient
from pydantic import ValidationError
from sqlalchemy import select

from test_erasure_inventory import inventory_db  # noqa: F401  shared disposable fixture
from models.user import User
from models.api_credential import ApiCredential
from models.automation_rule import AutomationRule
from models.marketplace_connection import MarketplaceConnection
from database import get_db
from dependencies import get_current_user
from routers import auth, referrals
from routers.referrals import close_account, delete_account
from schemas.account_closure import AccountClosureRequest
from schemas.auth import UserRegister
from services import account_closure as lifecycle
from services.account_closure_policy import POLICY_VERSION

START = datetime(2026, 11, 10, 12, tzinfo=timezone.utc)
REQUEST = SimpleNamespace(headers={}, client=None)


@pytest.fixture
def runtime(monkeypatch):
    clock = [START]
    sent = []
    async def mail(email, name, token):
        sent.append(token)
        return True
    async def throttle(*args, **kwargs):
        return None
    monkeypatch.setattr(lifecycle, 'utc_now', lambda: clock[0])
    monkeypatch.setattr(auth, 'send_verification_email', mail)
    monkeypatch.setattr(auth, '_throttle_or_429', throttle)
    monkeypatch.setattr(auth, 'hash_password', lambda value: 'synthetic-hash')
    return clock, sent


def body(confirm=True):
    return AccountClosureRequest(policy_version=POLICY_VERSION, confirm=confirm)


async def request_restore(db, user):
    return await auth.register(UserRegister(email=user.email, name='synthetic',
        password='Synthetic-Password-123', consent=True), REQUEST, db)


def test_close_recovery_requires_new_email_and_preserves_disarmed_authority(inventory_db, runtime):
    loop, _, db, owners = inventory_db
    clock, sent = runtime
    a, b = db.info['synthetic_resources']
    async def exercise():
        user = await db.get(User, owners[0])
        user.verification_token = 'pre-closure'
        user.reset_token = 'pre-closure-reset'
        user.reset_token_expires = START.replace(tzinfo=None)+timedelta(days=1)
        db.add_all([AutomationRule(user_id=uid, contour='money', action_type='legacy', enabled=True)
                    for uid in owners])
        await db.commit()
        old_version = user.token_version
        result = await close_account(body(), db, user)
        await db.refresh(user)
        started = user.closure_started_at
        assert result['erasure_complete'] is False
        assert result['recovery_deadline'] == (START+timedelta(days=30)).isoformat()
        assert user.closure_policy_version == POLICY_VERSION
        assert user.token_version == old_version+1 and user.deleted_at is not None
        assert user.reset_token is None and user.verification_token is None
        with pytest.raises(HTTPException):
            await auth.verify_email(Response(), 'pre-closure', db)
        await db.rollback()
        user = await db.get(User, owners[0])
        await request_restore(db, user)
        await db.refresh(user)
        assert user.deleted_at is not None and user.is_verified is False
        assert user.closure_started_at == started
        assert user.closure_restore_requested_at is not None
        response = Response()
        confirmed = await auth.verify_email(response, sent[-1], db)
        await db.refresh(user)
        assert user.deleted_at is None and user.is_verified and user.is_restored
        assert user.closure_restore_requested_at is None
        assert confirmed.login_required is True
        assert 'Max-Age=0' in response.headers.get('set-cookie', '')
        assert (await db.get(ApiCredential, a['credential'])).secret_enc == b''
        assert (await db.get(ApiCredential, b['credential'])).secret_enc == b'SECRET-CIPHERTEXT'
        rules = (await db.execute(select(AutomationRule))).scalars().all()
        assert all(not rule.enabled for rule in rules if rule.user_id == owners[0])
        assert all(rule.enabled for rule in rules if rule.user_id == owners[1])
    loop.run_until_complete(exercise())


@pytest.mark.parametrize('elapsed,allowed', [
    (timedelta(days=30)-timedelta(microseconds=1), True),
    (timedelta(days=30), False), (timedelta(days=31), False),
])
def test_deadline_rechecked_at_email_confirmation(inventory_db, runtime, elapsed, allowed):
    loop, _, db, owners = inventory_db
    clock, sent = runtime
    async def exercise():
        user = await db.get(User, owners[0])
        await close_account(body(), db, user)
        await request_restore(db, user)
        clock[0] = START+elapsed
        response = Response()
        if allowed:
            await auth.verify_email(response, sent[-1], db)
        else:
            with pytest.raises(HTTPException) as error:
                await auth.verify_email(response, sent[-1], db)
            assert error.value.status_code == 403 and not response.headers.get('set-cookie')
            await db.rollback()
            user = await db.get(User, owners[0])
            assert user.deleted_at is not None
    loop.run_until_complete(exercise())


def test_expired_registration_cannot_extend_window(inventory_db, runtime):
    loop, _, db, owners = inventory_db
    clock, sent = runtime
    async def exercise():
        user = await db.get(User, owners[0])
        await close_account(body(), db, user)
        clock[0] = START+timedelta(days=30)
        with pytest.raises(HTTPException):
            await request_restore(db, user)
        assert not sent
        await db.rollback()
        user = await db.get(User, owners[0])
        assert user.deleted_at is not None
    loop.run_until_complete(exercise())


def test_purge_started_blocks_pending_recovery(inventory_db, runtime):
    loop, _, db, owners = inventory_db
    _, sent = runtime
    async def exercise():
        user = await db.get(User, owners[0])
        await close_account(body(), db, user)
        await request_restore(db, user)
        user.closure_purge_started_at = START
        await db.commit()
        with pytest.raises(HTTPException):
            await auth.verify_email(Response(), sent[-1], db)
        await db.rollback()
        assert (await db.get(User, owners[0])).deleted_at is not None
    loop.run_until_complete(exercise())


def test_close_requires_explicit_confirmation_before_mutation(inventory_db, runtime):
    loop, _, db, owners = inventory_db
    async def exercise():
        user = await db.get(User, owners[0])
        with pytest.raises(HTTPException):
            await close_account(body(False), db, user)
        assert user.deleted_at is None and user.closure_policy_version is None
    loop.run_until_complete(exercise())


@pytest.mark.parametrize('bad', [1, 'true', None])
def test_nonboolean_confirmation_rejected(bad):
    with pytest.raises(ValidationError):
        body(bad)


LEGACY_DELETE_DETAIL = 'Используйте подтверждённое закрытие аккаунта на 30 дней.'


def closure_http_app(db, owner=None, *, authenticated=True):
    app = FastAPI()
    app.include_router(referrals.router, prefix='/api')
    auth_calls = []

    async def database():
        yield db

    async def owner_auth():
        auth_calls.append(owner.id)
        return owner

    app.dependency_overrides[get_db] = database
    if authenticated:
        app.dependency_overrides[get_current_user] = owner_auth
    return app, auth_calls


@pytest.fixture
def legacy_delete_state(inventory_db, monkeypatch, tmp_path):
    """Persisted two-owner controls plus poison DB: rejection may touch none."""
    loop, _, db, owners = inventory_db
    models = (User, ApiCredential, MarketplaceConnection, AutomationRule)

    async def setup():
        users = [await db.get(User, uid) for uid in owners]
        for index, user in enumerate(users):
            user.name = f'SYNTHETIC_OWNER_{index}'
            user.registered_ip = f'192.0.2.{index + 1}'
            user.hashed_password = f'SYNTHETIC_ORIGINAL_HASH_{index}'
            db.add(AutomationRule(user_id=user.id, contour='money', action_type='legacy', enabled=True))
        await db.commit()
        objects = []
        for model in models:
            objects.extend((await db.execute(select(model).order_by(model.id))).scalars().all())
        return users, objects

    users, objects = loop.run_until_complete(setup())
    memory_before = deepcopy([tuple(getattr(obj, col.key) for col in obj.__table__.columns) for obj in objects])

    async def persisted():
        with db.no_autoflush:
            return [list((await db.execute(select(*model.__table__.columns).order_by(model.id))).mappings())
                    for model in models]

    rows_before = deepcopy(loop.run_until_complete(persisted()))
    files = []
    for index, owner in enumerate(owners):
        directory = tmp_path / owner
        directory.mkdir()
        path = directory / 'synthetic.csv'
        path.write_bytes(f'owner,amount\nsynthetic-{index},{900 + index}\n'.encode())
        files.append((path, path.read_bytes()))
    from routers import csv_import
    monkeypatch.setattr(csv_import, '_UPLOAD_DIR', str(tmp_path))
    deactivate = AsyncMock(side_effect=AssertionError('legacy DELETE reached deactivation'))
    uploads = Mock(side_effect=AssertionError('legacy DELETE reached upload cleanup'))
    monkeypatch.setattr(referrals, '_deactivate_account', deactivate)
    monkeypatch.setattr(referrals, '_remove_upload_dir', uploads)
    poison = SimpleNamespace(**{
        name: AsyncMock(side_effect=AssertionError('legacy DELETE touched database'))
        for name in ('execute', 'get', 'commit', 'flush', 'refresh', 'delete', 'rollback')})
    poison.add = Mock(side_effect=AssertionError('legacy DELETE added a row'))
    poison.add_all = Mock(side_effect=AssertionError('legacy DELETE added rows'))
    yield SimpleNamespace(loop=loop, owner=users[0], db=poison, deactivate=deactivate, uploads=uploads)
    # Check persisted and in-memory values independently; never refresh away a mutation.
    assert memory_before == [tuple(getattr(obj, col.key) for col in obj.__table__.columns) for obj in objects]
    assert rows_before == loop.run_until_complete(persisted())
    assert not db.new and not db.dirty and not db.deleted
    assert all(path.is_file() and path.read_bytes() == content for path, content in files)


def assert_legacy_helpers_untouched(state):
    state.deactivate.assert_not_called()
    state.uploads.assert_not_called()
    for spy in vars(state.db).values():
        spy.assert_not_called()


def test_legacy_delete_direct_rejects_without_mutation(legacy_delete_state):
    state = legacy_delete_state

    async def exercise():
        with pytest.raises(HTTPException) as error:
            await delete_account(db=state.db, current_user=state.owner)
        assert error.value.status_code == 409
        assert error.value.detail == LEGACY_DELETE_DETAIL
        assert_legacy_helpers_untouched(state)

    state.loop.run_until_complete(exercise())


def test_legacy_delete_http_rejects_without_mutation(legacy_delete_state):
    state = legacy_delete_state
    app, authenticated = closure_http_app(state.db, state.owner)

    async def exercise():
        async with AsyncClient(transport=ASGITransport(app=app, raise_app_exceptions=False),
                               base_url='http://testserver') as client:
            response = await client.request('DELETE', '/api/account?confirm=true',
                json={'confirm': True, 'policy_version': POLICY_VERSION},
                headers={'Origin': 'http://testserver'})
        assert response.status_code == 409
        assert response.json() == {'detail': LEGACY_DELETE_DETAIL}
        assert 'set-cookie' not in response.headers and 'location' not in response.headers
        assert authenticated == [state.owner.id]
        assert_legacy_helpers_untouched(state)

    state.loop.run_until_complete(exercise())


def test_legacy_delete_http_retains_authentication(legacy_delete_state):
    state = legacy_delete_state
    app, _ = closure_http_app(state.db, authenticated=False)

    async def exercise():
        async with AsyncClient(transport=ASGITransport(app=app), base_url='http://testserver') as client:
            response = await client.delete('/api/account', headers={'Origin': 'http://testserver'})
        assert response.status_code == 401
        assert 'set-cookie' not in response.headers
        assert_legacy_helpers_untouched(state)

    state.loop.run_until_complete(exercise())


@pytest.mark.parametrize('confirm,status', [(False, 400), (1, 422), ('true', 422), (None, 422), ('missing', 422)],
                         ids=['false', 'integer', 'string', 'null', 'missing'])
def test_close_http_requires_strict_confirmation_without_mutation(legacy_delete_state, confirm, status):
    state = legacy_delete_state
    app, _ = closure_http_app(state.db, state.owner)

    async def exercise():
        payload = {'policy_version': POLICY_VERSION}
        if confirm != 'missing':
            payload['confirm'] = confirm
        async with AsyncClient(transport=ASGITransport(app=app), base_url='http://testserver') as client:
            response = await client.post('/api/account/close', json=payload,
                                         headers={'Origin': 'http://testserver'})
        assert response.status_code == status
        assert_legacy_helpers_untouched(state)

    state.loop.run_until_complete(exercise())


def test_confirmed_close_http_keeps_ordinary_30_day_contract(inventory_db, runtime, monkeypatch):
    loop, _, db, owners = inventory_db
    uploads = Mock()
    monkeypatch.setattr(referrals, '_remove_upload_dir', uploads)

    async def exercise():
        user = await db.get(User, owners[0])
        old_version = user.token_version
        original = (user.hashed_password, user.name, user.registered_ip)
        app, authenticated = closure_http_app(db, user)
        async with AsyncClient(transport=ASGITransport(app=app), base_url='http://testserver') as client:
            response = await client.post('/api/account/close',
                json={'policy_version': POLICY_VERSION, 'confirm': True},
                headers={'Origin': 'http://testserver'})
        assert response.status_code == 200
        result = response.json()
        assert result['ok'] is True and result['state'] == 'closed_recoverable'
        assert result['erasure_complete'] is False
        assert result['recovery_deadline'] == (START + timedelta(days=30)).isoformat()
        await db.refresh(user)
        assert user.deleted_at is not None and user.closure_policy_version == POLICY_VERSION
        assert user.token_version == old_version + 1
        assert (user.hashed_password, user.name, user.registered_ip) == original
        a, b = db.info['synthetic_resources']
        assert (await db.get(ApiCredential, a['credential'])).secret_enc == b''
        assert (await db.get(ApiCredential, b['credential'])).secret_enc == b'SECRET-CIPHERTEXT'
        assert (await db.get(User, owners[1])).deleted_at is None
        uploads.assert_called_once_with(owners[0])
        assert authenticated == [owners[0]]

    loop.run_until_complete(exercise())


def test_failed_closure_commit_rolls_back(inventory_db, runtime, monkeypatch):
    loop, _, db, owners = inventory_db
    async def exercise():
        user = await db.get(User, owners[0])
        async def fail():
            raise RuntimeError('synthetic commit failure')
        with monkeypatch.context() as scoped:
            scoped.setattr(db, 'commit', fail)
            with pytest.raises(RuntimeError):
                await close_account(body(), db, user)
        await db.rollback()
        user = await db.get(User, owners[0])
        assert user.deleted_at is None and user.closure_started_at is None
    loop.run_until_complete(exercise())


def test_pending_recovery_can_resend_but_expired_cannot(inventory_db, runtime):
    loop, _, db, owners = inventory_db
    clock, sent = runtime
    async def exercise():
        user = await db.get(User, owners[0])
        await close_account(body(), db, user)
        await request_restore(db, user)
        old_token = sent[-1]
        response = await auth.resend_verification(auth.ResendVerificationIn(email=user.email), REQUEST, db)
        assert len(sent) == 2 and sent[-1] != old_token
        with pytest.raises(HTTPException):
            await auth.verify_email(Response(), old_token, db)
        await db.rollback()
        user = await db.get(User, owners[0])
        clock[0] = START+timedelta(days=30)
        expired = await auth.resend_verification(auth.ResendVerificationIn(email=user.email), REQUEST, db)
        assert response == expired and len(sent) == 2
    loop.run_until_complete(exercise())


def test_closed_explicit_account_cannot_reset_password(inventory_db, runtime, monkeypatch):
    from services.reset_token import hash_reset_token
    loop, _, db, owners = inventory_db
    async def exercise():
        user = await db.get(User, owners[0])
        await close_account(body(), db, user)
        async def forbidden(*args):
            raise AssertionError('reset email must not be sent')
        monkeypatch.setattr(auth, 'send_password_reset_email', forbidden)
        await auth.forgot_password(auth.ForgotPasswordIn(email=user.email), REQUEST, db)
        assert user.reset_token is None
        # Even an already-minted/inconsistent token must fail at atomic consume.
        user.reset_token = hash_reset_token('synthetic-reset')
        user.reset_token_expires = datetime.utcnow()+timedelta(hours=1)
        await db.commit()
        with pytest.raises(HTTPException):
            await auth.reset_password(auth.ResetPasswordIn(token='synthetic-reset', password='Another-Password-123'), REQUEST, db)
        user = await db.get(User, owners[0])
        assert user.deleted_at is not None
    loop.run_until_complete(exercise())
