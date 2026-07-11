"""Workspace lookup (F1.1). Reads the ownership boundary; never writes it."""
from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from models.workspace import Workspace


class WorkspaceMissing(Exception):
    """A user has no workspace. Always a broken lifecycle invariant, never user error."""


async def resolve_workspace_id(db: AsyncSession, user_id: str) -> str:
    """Return the id of the workspace owned by `user_id`, or raise WorkspaceMissing.

    Every user is guaranteed a workspace by two independent paths: registration
    creates one in the same transaction as the user (routers/auth.py), and the F1.0
    migration backfilled one for every user that already existed. So a miss here is
    a bug in that lifecycle, not a state to recover from.

    This resolver therefore NEVER creates a workspace. Lazy creation would paper over
    exactly the bug it is meant to expose, and it would turn a lookup — reachable from
    read paths — into a writer. It also never commits, flushes, or otherwise mutates
    the session, and never falls back to returning `user_id`: the two ids are both
    uuid4 strings, so such a fallback would be silently accepted everywhere and would
    write evidence under the wrong owner.

    The exception carries no user id, email, or workspace id: it surfaces as an
    internal failure, and its message must be safe to log.
    """
    workspace_id = (
        await db.execute(
            select(Workspace.id).where(Workspace.owner_user_id == user_id)
        )
    ).scalars().first()

    if workspace_id is None:
        raise WorkspaceMissing("no workspace for the authenticated user")

    return workspace_id
