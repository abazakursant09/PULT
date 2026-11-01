"""Deterministic page boundaries with tied timestamps, on synthetic SQLite only."""
import asyncio
from datetime import datetime, timedelta
from types import SimpleNamespace

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import models  # noqa: F401
from database import Base
from models.notification import Notification
from models.user import User
from routers.notifications import list_notifications


@pytest.mark.parametrize("reverse_insertion", [False, True])
def test_tied_notification_pages_have_unique_deterministic_order(reverse_insertion):
    async def scenario():
        engine = create_async_engine("sqlite+aiosqlite://", poolclass=StaticPool)
        try:
            async with engine.begin() as conn:
                await conn.run_sync(Base.metadata.create_all)
            factory = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
            async with factory() as db:
                for uid in ("owner", "other"):
                    db.add(User(id=uid, name="Synthetic", email=f"{uid}@example.invalid", hashed_password="not-a-login-hash"))
                stamp = datetime(2026, 1, 1)
                numbers = list(range(21))
                for number in reversed(numbers) if reverse_insertion else numbers:
                    db.add(Notification(id=f"notice-{number:03}", user_id="owner", type="new_review", title="Synthetic", message="Synthetic", is_read=number % 2 == 0, created_at=stamp))
                db.add(Notification(id="notice-newest", user_id="owner", type="new_review", title="Synthetic", message="Synthetic", is_read=True, created_at=stamp + timedelta(seconds=1)))
                db.add(Notification(id="foreign", user_id="other", type="new_review", title="Foreign", message="Foreign", is_read=False, created_at=stamp + timedelta(days=1)))
                await db.commit()
                expected = ["notice-newest"] + [f"notice-{n:03}" for n in reversed(numbers)]
                for _ in range(2):
                    pages = [await list_notifications(page=p, per_page=10, current_user=SimpleNamespace(id="owner"), db=db) for p in (1, 2, 3)]
                    ids = [item["id"] for page in pages for item in page["items"]]
                    assert ids == expected
                    assert len(ids) == len(set(ids)) == 22
                    assert all(page["total"] == 22 and page["unread_count"] == 10 for page in pages)
                empty = await list_notifications(page=4, per_page=10, current_user=SimpleNamespace(id="owner"), db=db)
                assert empty["items"] == []
        finally:
            await engine.dispose()
    asyncio.run(scenario())
