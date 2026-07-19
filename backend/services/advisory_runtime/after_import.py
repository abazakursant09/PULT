"""Run the advisory producers for ONE seller right after their import lands.

Why this exists: producers are otherwise reached only by the scheduler tick, which is due-based
(24h for most contours, 1h for growth). A seller who had just uploaded their first report was
therefore told "PULT анализирует" and then shown "Нет данных для анализа" on the very next screen,
because nothing had run yet and nothing would for hours.

What this is NOT: a second scheduler. There is no loop, no cadence, no due-check, and no
enumeration of other sellers. It runs the enabled producers once, for the one user whose import
just committed, through `AdvisoryRuntime.run_one` — the same entrypoint the four "recompute now"
engine routers already use, with the `triggered_by="import"` value the RuntimeContext has
documented since it was written.

Isolation matters twice over. `user_id` is passed straight through, so no other seller's data is
ever touched; and one producer raising must not stop the rest, because a seller whose growth
contour fails should still get their revenue diagnosis.
"""
from __future__ import annotations

import logging
from typing import Optional

from database import AsyncSessionLocal
from .runtime import AdvisoryRuntime

log = logging.getLogger(__name__)

TRIGGER = "import"


async def run_producers_for_user(user_id: str, *, logger: Optional[logging.Logger] = None) -> dict:
    """Run every enabled producer once for this seller. Never raises.

    Opens its own session: the request's session is already closed by the time a background task
    runs, and borrowing a closed one would fail silently at exactly the moment the seller is
    waiting for their first result.
    """
    lg = logger or log
    from .registry import ADVISORY_PRODUCERS      # lazy: same import-cycle break the runtime uses

    ran = errors = 0
    runtime = AdvisoryRuntime(logger=lg)
    try:
        async with AsyncSessionLocal() as db:
            for spec in ADVISORY_PRODUCERS:
                if not spec.enabled:
                    continue
                try:
                    await runtime.run_one(db, user_id=user_id, producer_key=spec.key,
                                          triggered_by=TRIGGER)
                    ran += 1
                except Exception:
                    # Error-isolated per producer, exactly as the scheduler tick is. The ledger
                    # row is already committed by run_one before it re-raises, so the failure
                    # stays visible in AdvisoryRun rather than only in this log line.
                    errors += 1
                    lg.exception("after-import producer %s failed for user %s", spec.key, user_id)
    except Exception:
        # A background task that raises dies silently in the event loop; the seller would be left
        # on "разбор готовится" forever with nothing recorded. Log loudly instead.
        lg.exception("after-import analysis could not run for user %s", user_id)

    lg.info("after-import analysis: user=%s ran=%s errors=%s", user_id, ran, errors)
    return {"ran": ran, "errors": errors}
