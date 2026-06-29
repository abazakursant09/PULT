"""
Advisory Runtime contracts (Phase-0 foundation) — RuntimeContext + ProducerResult.

The two stable contracts the Runtime hands to / receives from a producer. Deliberately
minimal so the Runtime stays decoupled from every contour:

  * RuntimeContext carries ONLY runtime dependencies. It must NOT carry config,
    thresholds, limits, marketplace, sku, listing_id, snapshots, or any
    contour-specific field — the producer fetches everything it needs itself.

  * ProducerResult is ok + an OPAQUE stats blob. The Runtime never reads the
    semantics of `stats`; it stores it verbatim in the AdvisoryRun ledger.

Plus AdvisoryRuntime.run_one — the MANUAL orchestration runner (A5). It runs ONE
producer for ONE user through the single contract `spec.run(ctx)`, records an
AdvisoryRun ledger row, and commits. It is NOT a scheduler and reads no `enabled`
flag — that gate belongs to a future scheduler tick. The Runtime knows only the
registry + ProducerResult; it never knows a producer's internals (audit_and_persist,
snapshot, thresholds, marketplace, sku, listing).
"""
from __future__ import annotations

import json
import logging
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from logging import Logger
from typing import Mapping, Optional

from datetime import timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from models.advisory_run import AdvisoryRun
from models.imported_finance import ImportedFinanceRow


@dataclass(frozen=True)
class RuntimeContext:
    """Runtime dependencies handed to a producer. Runtime-owned only."""
    db: AsyncSession
    user_id: str
    now: datetime
    run_id: str
    logger: Logger
    triggered_by: str            # scheduled | import | manual


@dataclass(frozen=True)
class ProducerResult:
    """A producer's return value. `stats` is OPAQUE to the Runtime."""
    ok: bool
    stats: Mapping[str, object] = field(default_factory=dict)


class AdvisoryRuntimeError(Exception):
    """Runtime-level failure (e.g. unknown producer_key). Not a producer error."""


@dataclass(frozen=True)
class RuntimeTickResult:
    """Aggregate of one run_due_producers pass. Counts only — no producer semantics."""
    ran: int = 0
    skipped: int = 0
    errors: int = 0


async def _active_user_ids(db: AsyncSession):
    """Honest minimal active-user source: sellers that have imported finance data —
    the advisory anchor (mirrors measurement_close enumerating its own table). No new
    active-user mechanism invented."""
    return (await db.execute(
        select(ImportedFinanceRow.user_id).distinct())).scalars().all()


class AdvisoryRuntime:
    """Manual orchestration layer (A5). NOT a scheduler.

    `run_one` resolves a registered producer by key, hands it a RuntimeContext,
    records an AdvisoryRun ledger row (always — including on producer failure), and
    commits. The Runtime is flush-agnostic about the producer: the adapter is
    flush-only, the Runtime owns the commit."""

    def __init__(self, logger: Optional[Logger] = None) -> None:
        self._logger = logger or logging.getLogger("advisory_runtime")

    async def run_one(
        self, db: AsyncSession, *, user_id: str, producer_key: str,
        now: Optional[datetime] = None, triggered_by: str = "manual",
    ) -> AdvisoryRun:
        """Run ONE producer for ONE user. Returns the persisted AdvisoryRun. Reads no
        `enabled` flag — manual run. Re-raises a producer exception AFTER the ledger
        row is committed."""
        # lazy import breaks the registry<->runtime import cycle
        from .registry import ADVISORY_PRODUCERS

        spec = next((s for s in ADVISORY_PRODUCERS if s.key == producer_key), None)
        if spec is None:
            raise AdvisoryRuntimeError(f"unknown producer_key: {producer_key!r}")

        ts = now or datetime.utcnow()
        run_id = str(uuid.uuid4())
        row = AdvisoryRun(
            run_id=run_id, user_id=user_id, producer_key=producer_key,
            started_at=ts, status="running", triggered_by=triggered_by)

        ctx = RuntimeContext(db=db, user_id=user_id, now=ts, run_id=run_id,
                             logger=self._logger, triggered_by=triggered_by)

        t0 = time.monotonic()
        producer_error: Optional[BaseException] = None
        try:
            result = await spec.run(ctx)          # Runtime knows ONLY this contract
            row.status = "ok" if result.ok else "error"
            # stats stored VERBATIM — Runtime never reads its keys
            row.stats = json.dumps(dict(result.stats), ensure_ascii=False)
        except Exception as exc:                  # producer failure → ledger must persist
            producer_error = exc
            row.status = "error"
            row.error = str(exc)
            self._logger.exception("advisory producer %s failed for user %s",
                                   producer_key, user_id)
        finally:
            row.duration_ms = int((time.monotonic() - t0) * 1000)
            row.finished_at = datetime.utcnow()
            db.add(row)
            await db.commit()

        if producer_error is not None:
            raise producer_error
        return row

    async def _is_due(self, db: AsyncSession, user_id: str, spec, ts: datetime) -> bool:
        """Due = no SUCCESSFUL AdvisoryRun for (user, producer) within cadence_seconds."""
        cutoff = ts - timedelta(seconds=spec.cadence_seconds)
        recent = (await db.execute(select(AdvisoryRun.id).where(
            AdvisoryRun.user_id == user_id,
            AdvisoryRun.producer_key == spec.key,
            AdvisoryRun.status == "ok",
            AdvisoryRun.started_at >= cutoff,
        ).limit(1))).scalar()
        return recent is None

    async def run_due_producers(
        self, db: AsyncSession, *, now: Optional[datetime] = None,
        slot_budget: int = 10, triggered_by: str = "scheduled",
    ) -> RuntimeTickResult:
        """Run every DUE (active-user × enabled-producer) pair, capped at slot_budget.
        Shadow-safe: when no ProducerSpec is enabled it returns immediately, touching
        no user. One producer/user failure never aborts the tick (error-isolated)."""
        from .registry import ADVISORY_PRODUCERS          # lazy: break import cycle

        ts = now or datetime.utcnow()
        enabled = [s for s in ADVISORY_PRODUCERS if s.enabled]
        if not enabled:
            return RuntimeTickResult(ran=0, skipped=0, errors=0)

        users = await _active_user_ids(db)
        ran = skipped = errors = 0
        for spec in enabled:
            for uid in users:
                if not await self._is_due(db, uid, spec, ts):
                    skipped += 1
                    continue
                if ran >= slot_budget:          # budget exhausted — remaining due wait
                    skipped += 1
                    continue
                try:
                    await self.run_one(db, user_id=uid, producer_key=spec.key,
                                       now=ts, triggered_by=triggered_by)
                    ran += 1
                except Exception:               # producer/user failure isolated
                    errors += 1
        return RuntimeTickResult(ran=ran, skipped=skipped, errors=errors)
