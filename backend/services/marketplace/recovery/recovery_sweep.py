"""SECURITY-2D-1C-B — READ-ONLY recovery sweep: detect stuck operations and classify them.

Feature OFF by default. While recovery_reaper_enabled is not True this returns immediately — ZERO
ExecutionLog queries, ZERO provider calls, ZERO DB writes, no advisory lock. When enabled it:
  * takes a PostgreSQL session-level advisory lock (its own operation code, distinct from the retention
    sweep) so at most one sweep runs; SQLite uses a local lock for tests only,
  * processes candidates per user in a fresh session (one user's error never stops the others),
  * re-checks each candidate's fingerprint against the saved data (mismatch → fail-closed still_unknown),
  * asks reconcile_read.observe() for a READ-ONLY verdict (never a provider write, never a re-run),
  * in non-dry-run, writes ONLY reconciliation_status + the three scheduling columns via a status-guarded
    UPDATE (so a row the executor has since finalised is never stomped).

It NEVER changes ExecutionLog.status, claim_generation or dispatch_started_at, never calls a provider
WRITE, never re-dispatches. All logs are numeric only (no id / key / fingerprint / payload / SQL / params
/ exception text).
"""
from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Optional

from sqlalchemy import DateTime, and_, bindparam, or_, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from config import settings
from database import engine, AsyncSessionLocal
from models.execution_log import ExecutionLog
from services.marketplace.executor import _fingerprint      # pure, read-only fingerprint recompute
from services.marketplace.recovery import reconcile_read

logger = logging.getLogger(__name__)

# Advisory-lock key: same namespace as the retention sweep, DISTINCT operation code so the two never
# block each other. namespace = 0x50554C54 "PULT", operation = 0x5245434E "RECN" (recovery reconcile).
_LOCK_NAMESPACE = 0x50554C54   # 1347767380
_LOCK_OPERATION = 0x5245434E   # 1381193038
assert 0 < _LOCK_NAMESPACE < 2 ** 31 and 0 < _LOCK_OPERATION < 2 ** 31

# Local (single-process) guard for SQLite tests ONLY — NOT a production concurrency invariant.
_local_lock = asyncio.Lock()

_STATEMENT_TIMEOUT = "30s"
_LOCK_TIMEOUT = "5s"
DEFAULT_MAX_DURATION_SECONDS = 600

_CANDIDATE_STATUSES = ("pending", "in_flight", "ambiguous")
_IO = reconcile_read.INTENT_OBSERVED
_NO = reconcile_read.TARGET_NOT_OBSERVED
_SU = reconcile_read.STILL_UNKNOWN


@dataclass
class RecoverySweepResult:
    enabled: bool
    lock_acquired: bool
    dry_run: bool
    candidates: int = 0
    intent_observed: int = 0
    target_not_observed: int = 0
    still_unknown: int = 0
    reconciled: int = 0                 # rows whose reconciliation fields were written (non-dry-run)
    fingerprint_mismatches: int = 0
    failed_users: int = 0
    timed_out: bool = False
    duration_ms: int = 0


def _now_naive(now) -> datetime:
    if now is None:
        return datetime.utcnow()
    return now.replace(tzinfo=None) if now.tzinfo else now


def _now_tz(now) -> datetime:
    if now is None:
        return datetime.now(timezone.utc)
    return now if now.tzinfo else now.replace(tzinfo=timezone.utc)


def _candidate_where(now_naive: datetime, now_tz: datetime):
    pending_cut = now_naive - timedelta(seconds=settings.recovery_stale_pending_seconds)
    inflight_cut = now_naive - timedelta(seconds=settings.recovery_stale_in_flight_seconds)
    amb_cut = now_naive - timedelta(seconds=settings.recovery_ambiguous_seconds)
    return and_(
        ExecutionLog.status.in_(_CANDIDATE_STATUSES),
        or_(
            and_(ExecutionLog.status == "pending", ExecutionLog.dispatch_started_at.is_(None),
                 ExecutionLog.created_at < pending_cut),
            and_(ExecutionLog.status == "in_flight", ExecutionLog.dispatch_started_at.isnot(None),
                 ExecutionLog.created_at < inflight_cut),
            and_(ExecutionLog.status == "ambiguous", ExecutionLog.created_at < amb_cut),
        ),
        or_(ExecutionLog.next_reconcile_at.is_(None), ExecutionLog.next_reconcile_at <= now_tz),
        ExecutionLog.reconciliation_attempts < settings.recovery_max_reconcile_attempts,
    )


_WRITE_RECON = text(
    "UPDATE execution_logs SET reconciliation_status=:v, "
    "reconciliation_attempts = reconciliation_attempts + 1, last_reconciled_at=:now, next_reconcile_at=:nxt "
    "WHERE id=:id AND status IN ('pending','in_flight','ambiguous') RETURNING id"
).bindparams(bindparam("now", type_=DateTime(timezone=True)),
             bindparam("nxt", type_=DateTime(timezone=True)))


async def run_recovery_sweep(*, dry_run: Optional[bool] = None, now: Optional[datetime] = None,
                             batch_size: Optional[int] = None,
                             max_duration: Optional[float] = None) -> RecoverySweepResult:
    """Read-only recovery classification (feature OFF by default). Returns numeric counters only."""
    started = time.monotonic()
    # Fail-closed master gate: nothing at all happens unless explicitly enabled.
    if settings.recovery_reaper_enabled is not True:
        return RecoverySweepResult(enabled=False, lock_acquired=False,
                                   dry_run=(settings.recovery_reaper_dry_run is True))
    if dry_run is None:
        dry_run = settings.recovery_reaper_dry_run is True
    batch_size = int(batch_size or settings.recovery_batch_size)
    deadline = (started + max_duration) if max_duration is not None else None
    result = RecoverySweepResult(enabled=True, lock_acquired=False, dry_run=dry_run)
    dialect = engine.dialect.name

    try:
        if dialect == "postgresql":
            async with engine.connect() as lock_conn:
                acquired = bool((await lock_conn.execute(
                    text("SELECT pg_try_advisory_lock(:ns, :op)"),
                    {"ns": _LOCK_NAMESPACE, "op": _LOCK_OPERATION})).scalar())
                if not acquired:
                    logger.info("recovery sweep: another run is active")
                    result.duration_ms = int((time.monotonic() - started) * 1000)
                    return result
                result.lock_acquired = True
                try:
                    await _sweep(dry_run, now, batch_size, dialect, result, deadline)
                finally:
                    await lock_conn.execute(text("SELECT pg_advisory_unlock(:ns, :op)"),
                                            {"ns": _LOCK_NAMESPACE, "op": _LOCK_OPERATION})
        else:
            if _local_lock.locked():
                logger.info("recovery sweep: another run is active")
                result.duration_ms = int((time.monotonic() - started) * 1000)
                return result
            async with _local_lock:
                result.lock_acquired = True
                await _sweep(dry_run, now, batch_size, dialect, result, deadline)
    finally:
        result.duration_ms = int((time.monotonic() - started) * 1000)

    logger.info("recovery sweep%s: candidates=%d reconciled=%d io=%d tno=%d su=%d fpm=%d failed_users=%d "
                "timed_out=%d dur=%dms", " dry-run" if dry_run else "", result.candidates,
                result.reconciled, result.intent_observed, result.target_not_observed, result.still_unknown,
                result.fingerprint_mismatches, result.failed_users, int(result.timed_out),
                result.duration_ms)
    return result


async def _sweep(dry_run, now, batch_size, dialect, result: RecoverySweepResult, deadline):
    now_naive = _now_naive(now)
    now_tz = _now_tz(now)
    async with AsyncSessionLocal() as db0:
        users = [u for (u,) in (await db0.execute(
            select(ExecutionLog.user_id).where(_candidate_where(now_naive, now_tz)).distinct())).all()]

    for uid in users:
        if deadline is not None and time.monotonic() >= deadline:
            result.timed_out = True
            return
        try:
            # READ phase: candidates + fingerprint re-check + read-only provider observation.
            verdicts = []
            async with AsyncSessionLocal() as rdb:
                rows = (await rdb.execute(
                    select(ExecutionLog).where(ExecutionLog.user_id == uid)
                    .where(_candidate_where(now_naive, now_tz))
                    .order_by(ExecutionLog.created_at).limit(batch_size))).scalars().all()
                for row in rows:
                    result.candidates += 1
                    verdict = await _classify(rdb, row, result)
                    if verdict == _IO:
                        result.intent_observed += 1
                    elif verdict == _NO:
                        result.target_not_observed += 1
                    else:
                        result.still_unknown += 1
                    verdicts.append((row.id, int(row.reconciliation_attempts or 0), verdict))

            # WRITE phase (short, status-guarded) — skipped entirely in dry-run.
            if not dry_run and verdicts:
                async with AsyncSessionLocal() as wdb:
                    if dialect == "postgresql":
                        await wdb.execute(text(f"SET LOCAL statement_timeout = '{_STATEMENT_TIMEOUT}'"))
                        await wdb.execute(text(f"SET LOCAL lock_timeout = '{_LOCK_TIMEOUT}'"))
                    for rid, attempts, verdict in verdicts:
                        nxt = _next_reconcile(verdict, attempts + 1, now_tz)
                        res = await wdb.execute(_WRITE_RECON,
                                                {"v": verdict, "now": now_tz, "nxt": nxt, "id": rid})
                        result.reconciled += len(res.fetchall())
                    await wdb.commit()
        except Exception:  # noqa: BLE001 — SAFE: no exception text/ids logged; the next user still runs
            result.failed_users += 1
            logger.warning("recovery sweep: a user batch failed; failed_users=%d", result.failed_users)


async def _classify(db: AsyncSession, row, result: RecoverySweepResult) -> str:
    """Fingerprint self-consistency re-check, then a read-only provider verdict. Mismatch is fail-closed."""
    try:
        recomputed = _fingerprint(row.user_id, row.connection_id, row.marketplace, row.action_type,
                                  row.mode, row.payload or {}, row.reverted_from)
    except Exception:  # noqa: BLE001 — cannot recompute → cannot trust → still_unknown, no provider read
        result.fingerprint_mismatches += 1
        return _SU
    if row.request_fingerprint is None or recomputed != row.request_fingerprint:
        result.fingerprint_mismatches += 1
        return _SU                          # fail-closed: never observe/promote on a fingerprint mismatch
    return await reconcile_read.observe(db, row)


def _next_reconcile(verdict: str, new_attempts: int, now_tz: datetime):
    # intent_observed → goal met, stop rechecking. Exhausted attempts → stop (1C-C operator picks it up).
    # Otherwise schedule the next read after a backoff.
    if verdict == _IO or new_attempts >= settings.recovery_max_reconcile_attempts:
        return None
    return now_tz + timedelta(seconds=settings.recovery_recheck_backoff_seconds)
