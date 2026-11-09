"""Serialize sensitive account writes with deactivation on PostgreSQL.

Caller owns the transaction and must hold this lock until its commit/rollback.
SQLite ignores FOR UPDATE: stale-state rejection works there, not row-lock proof.
"""
from fastapi import HTTPException
from sqlalchemy import select
from models.user import User


async def lock_active_account(db, authenticated_user):
    expected_version = getattr(authenticated_user, "token_version", None)
    if type(expected_version) is not int:
        raise HTTPException(401, "Сессия завершена")
    # Column tuples bypass the ORM identity cache: stale authenticated User objects
    # must not masquerade as freshly revalidated state. Never flush before locking.
    with db.no_autoflush:
        row = (await db.execute(select(User.deleted_at, User.token_version).where(
            User.id == authenticated_user.id).with_for_update())).first()
    if row is None:
        raise HTTPException(401, "Сессия завершена")
    if row.deleted_at is not None:
        raise HTTPException(403, "Аккаунт деактивирован")
    if row.token_version != expected_version:
        raise HTTPException(401, "Сессия завершена")
