"""Latest producer failures remain visible without hiding usable diagnoses."""
import asyncio
import uuid
from datetime import datetime, timedelta

from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from database import Base
import models  # noqa: F401
from models.advisory_run import AdvisoryRun
from services.first_run_state import compute_first_run_state


def test_latest_enabled_attempts_expose_partial_failure_and_clear_after_recovery():
    async def go():
        engine = create_async_engine("sqlite+aiosqlite://", poolclass=StaticPool)
        try:
            async with engine.begin() as conn:
                await conn.run_sync(Base.metadata.create_all)
            async with sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)() as db:
                uid, other = str(uuid.uuid4()), str(uuid.uuid4())
                now = datetime(2026, 10, 31, 12)
                def attempt(user, key, status, offset):
                    db.add(AdvisoryRun(run_id=str(uuid.uuid4()), user_id=user, producer_key=key,
                                       status=status, started_at=now+timedelta(seconds=offset),
                                       finished_at=now+timedelta(seconds=offset+1), triggered_by="import"))
                attempt(uid, "revenue_diagnosis", "error", 0)
                attempt(uid, "operations_low_stock", "ok", 0)
                attempt(other, "growth", "error", 2)
                attempt(uid, "retired-unknown-producer", "error", 3)
                await db.commit()
                partial = await compute_first_run_state(db, uid, has_diagnosis=True)
                assert partial.state == "ready"
                assert partial.analysis_error_count == 1
                attempt(uid, "revenue_diagnosis", "ok", 10)
                await db.commit()
                recovered = await compute_first_run_state(db, uid, has_diagnosis=True)
                assert recovered.state == "ready"
                assert recovered.analysis_error_count == 0
                # Append-only history must not disappear to make a warning clear.
                assert len((await db.execute(models.advisory_run.AdvisoryRun.__table__.select())).all()) == 5
        finally:
            await engine.dispose()
    asyncio.run(go())
