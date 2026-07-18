"""AR-VIS-1 — the seller can see the review-sync cadence of their own connections.

`GET /api/connections` already returns the seller's connections; AR-VIS-1 only widens the response
with the two cadence columns the scheduler already writes (review_sync_next_at,
review_sync_fail_count) so the UI can say "next check at HH:MM" or "paused, retry at HH:MM" instead
of leaving the seller in silence. Read-only: no migration, no router change, no writer touched.

The internal keyset `review_sync_cursor` stays unexposed — it is meaningless to a seller.
"""
import asyncio
import uuid
from datetime import datetime, timedelta

from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from database import Base
import models  # noqa: F401  register tables
from models.user import User
from models.marketplace_connection import MarketplaceConnection
from routers.connections import list_connections
from schemas.marketplace import ConnectionOut

_LOOP = asyncio.new_event_loop()


def _run(coro):
    return _LOOP.run_until_complete(coro)


async def _new_db():
    e = create_async_engine("sqlite+aiosqlite://",
                            connect_args={"check_same_thread": False}, poolclass=StaticPool)
    async with e.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    return sessionmaker(e, class_=AsyncSession, expire_on_commit=False)()


async def _seller(db, *, marketplace="wildberries", next_at=None, fail_count=0):
    """A seller with one connection carrying the given review-sync cadence state."""
    uid = str(uuid.uuid4())
    user = User(id=uid, email=f"{uid}@e.com", name="S", hashed_password="x", is_verified=True)
    db.add(user)
    conn = MarketplaceConnection(id=str(uuid.uuid4()), user_id=uid, marketplace=marketplace,
                                 status="connected", scopes=["feedbacks"],
                                 review_sync_next_at=next_at, review_sync_fail_count=fail_count)
    db.add(conn)
    await db.commit()
    return user, conn


def test_connection_out_exposes_review_sync_fields():
    """Both cadence fields are part of the connections response."""
    fields = ConnectionOut.model_fields
    assert "review_sync_next_at" in fields
    assert "review_sync_fail_count" in fields
    assert "review_sync_cursor" not in fields          # internal keyset — never shown to a seller


def test_values_match_the_stored_connection():
    db = _run(_new_db())
    when = datetime(2026, 7, 18, 15, 40, 0)
    user, conn = _run(_seller(db, next_at=when, fail_count=3))

    out = _run(list_connections(current_user=user, db=db))

    assert len(out) == 1
    assert out[0].id == conn.id
    assert out[0].review_sync_next_at == when          # exactly what the scheduler stored
    assert out[0].review_sync_fail_count == 3


def test_empty_cadence_does_not_break_the_response():
    """A connection that has never synced (NULL next_at) serializes fine — no 500, no invented time."""
    db = _run(_new_db())
    user, _ = _run(_seller(db))                        # defaults: next_at NULL, fail_count 0

    out = _run(list_connections(current_user=user, db=db))

    assert out[0].review_sync_next_at is None
    assert out[0].review_sync_fail_count == 0


def test_another_sellers_cadence_is_not_visible():
    """The new fields ride on the already owner-scoped query — one seller never sees another's."""
    db = _run(_new_db())
    mine, my_conn = _run(_seller(db, next_at=datetime(2026, 7, 18, 10, 0, 0), fail_count=1))
    _theirs, their_conn = _run(_seller(db, marketplace="ozon",
                                       next_at=datetime(2026, 7, 18, 23, 0, 0), fail_count=9))

    out = _run(list_connections(current_user=mine, db=db))

    assert [c.id for c in out] == [my_conn.id]         # their connection is absent entirely
    assert their_conn.id not in {c.id for c in out}
    assert out[0].review_sync_fail_count == 1          # not the other seller's 9
