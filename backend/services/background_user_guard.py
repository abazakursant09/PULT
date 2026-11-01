"""Eligibility for starting enumerated background actions; never a data-retention policy."""
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from models.user import User


async def is_active_background_user(db: AsyncSession, user_id: str) -> bool:
    """Fresh scalar query, not cached ORM state. DB failures propagate fail-closed.

    This is not an atomic cancellation guarantee for an already-started action.
    """
    return (await db.execute(select(User.id).where(
        User.id == user_id, User.deleted_at.is_(None),
    ))).scalar_one_or_none() is not None
