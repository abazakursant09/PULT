"""
Automatic Decision-Outcome measurement close.

Driven by the existing scheduler loop (tasks/scheduler.run_scheduler) — NOT a new
scheduler, NOT cron. Each tick it closes the effect observations whose measurement
window has elapsed: read the OBSERVED after value, classify the qualitative band,
flip the link to measured. Exactly the same path the manual POST
/api/decision-outcome/close uses, but window-gated (only_expired) so an effect is
never read before its window is over.

Honest, idempotent, flush-only: only still-open + expired observations are touched;
insufficient data closes as not_evaluated; no forecast, no ROI, no fabricated value.
The proven band then surfaces automatically in the Decision Outcome API and the
Daily Decision Feed (build_effect_summaries reads the closed observation).
"""
from __future__ import annotations

import logging
from datetime import datetime
from typing import Optional

from sqlalchemy import select

from database import AsyncSessionLocal
from models.engine_effect_observation import EngineEffectObservation
from services.decision_outcome.effect_measurement import close_effect_measurement

logger = logging.getLogger(__name__)


async def run_measurement_close(now: Optional[datetime] = None) -> int:
    """Close every user's expired-but-open observations. Returns observations closed.

    Owns its own session. Never raises into the scheduler — errors are logged and
    swallowed so one bad row never stops the tick."""
    closed = 0
    try:
        async with AsyncSessionLocal() as db:
            user_ids = (await db.execute(
                select(EngineEffectObservation.user_id)
                .where(EngineEffectObservation.measured_at.is_(None))
                .distinct())).scalars().all()
            for uid in user_ids:
                res = await close_effect_measurement(db, user_id=uid, now=now, only_expired=True)
                closed += res.closed
            if closed:
                await db.commit()
    except Exception:
        logger.exception("measurement_close tick error")
    return closed
