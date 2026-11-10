"""Explicit closure state; caller owns the user row lock and transaction."""
from datetime import datetime, timezone
from fastapi import HTTPException
from services.account_closure_policy import POLICY_VERSION, evaluate_closure


def utc_now():
    return datetime.now(timezone.utc)


def has_closure(user):
    return any(getattr(user, name, None) is not None for name in (
        'closure_policy_version', 'closure_started_at',
        'closure_restore_requested_at', 'closure_purge_started_at'))


def _database_utc(value):
    # SQLite drops tzinfo on DateTime(timezone=True); this column is written only in UTC.
    if isinstance(value, datetime) and value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value


def require_recovery_window(user):
    result = evaluate_closure(
        policy_version=user.closure_policy_version,
        closed_at=_database_utc(user.closure_started_at), now=utc_now(),
        purge_started=user.closure_purge_started_at is not None)
    if not result['recovery_eligible']:
        raise HTTPException(403, 'Восстановление аккаунта недоступно. Обратитесь в поддержку.')
    return result


def begin_closure(user):
    if user.closure_purge_started_at is not None:
        raise HTTPException(403, 'Очистка аккаунта уже начата.')
    now = utc_now()
    user.closure_policy_version = POLICY_VERSION
    user.closure_started_at = now
    user.closure_restore_requested_at = None
    user.closure_purge_started_at = None
    # Invalidate pre-closure email links; only a newly requested recovery may reopen it.
    user.verification_token = None
    user.reset_token = None
    user.reset_token_expires = None
    return require_recovery_window(user)['recovery_deadline']


def request_recovery(user):
    require_recovery_window(user)
    user.closure_restore_requested_at = utc_now()


def finish_recovery(user):
    require_recovery_window(user)
    if user.closure_restore_requested_at is None:
        raise HTTPException(403, 'Сначала запросите восстановление аккаунта.')
    user.deleted_at = None
    user.is_restored = True
    user.closure_restore_requested_at = None
