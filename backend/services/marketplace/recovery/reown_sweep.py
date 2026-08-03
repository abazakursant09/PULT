"""SECURITY-2D-1C-C2 — OFF-by-default controlled RE-OWN sweep.

Feature OFF by default. While recovery_reown_enabled is not True this returns immediately — ZERO
ExecutionLog queries, ZERO advisory lock, ZERO DB writes, ZERO provider reads/writes, ZERO executor
calls. When enabled it ONLY transfers ownership of a stuck SAFE pending claim
(status='pending' AND dispatch_started_at IS NULL) by an atomic CAS that bumps claim_generation
(+ reown_count + last_reowned_at). It NEVER dispatches, NEVER calls the executor, NEVER sets in_flight,
NEVER changes status / dispatch_started_at / attempt_count / reconciliation. After the transfer the row
stays pending — a future 1C-C3 operator flow (not this slice) decides any re-dispatch.

Old worker that still holds the previous generation is fenced by the 1C-C1 CAS (RETURNING empty → 0
dispatch). Two re-owners → one winner (claim_generation=:seen). All logs are numeric only (no id / key /
fingerprint / payload / SQL / params / exception text).
"""
from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Optional

from sqlalchemy import DateTime, Integer, and_, bindparam, or_, select, text
from sqlalchemy.ext.asyncio import AsyncSession  # noqa: F401 — typing/parity with recovery_sweep

from config import settings
from database import engine, AsyncSessionLocal
from models.execution_log import ExecutionLog
# Canonical validators ONLY — no executor, no provider clients (see the source guard test).
from services.marketplace.operation_key import is_valid_v1_key
from services.marketplace.request_fingerprint import is_valid_fingerprint

logger = logging.getLogger(__name__)

# Advisory-lock key: same namespace as retention / read-only reconcile, but a THIRD distinct operation
# code so none of the three sweeps ever block each other. namespace = 0x50554C54 "PULT",
# operation = 0x52454F57 "REOW" (re-own).
_LOCK_NAMESPACE = 0x50554C54   # "PULT"
_LOCK_OPERATION = 0x52454F57   # "REOW"
assert 0 < _LOCK_NAMESPACE < 2 ** 31 and 0 < _LOCK_OPERATION < 2 ** 31

# Local (single-process) guard for SQLite tests ONLY — NOT a production concurrency invariant.
_local_lock = asyncio.Lock()

_STATEMENT_TIMEOUT = "30s"
_LOCK_TIMEOUT = "5s"
DEFAULT_MAX_DURATION_SECONDS = 600


@dataclass
class ReownSweepResult:
    enabled: bool
    lock_acquired: bool
    dry_run: bool
    candidates: int = 0        # rows the coarse SELECT returned
    eligible: int = 0          # candidates that passed canonical key + fingerprint validation
    reowned: int = 0           # ownership transfers committed (RETURNING a row)
    skipped_invalid: int = 0   # candidate failed canonical validation → no CAS
    skipped_race: int = 0      # CAS RETURNING empty (generation moved / no longer eligible)
    failed_batches: int = 0
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


def _candidate_where(cut_naive: datetime, cut_tz: datetime):
    # A safe re-own candidate: an un-dispatched pending claim with a v1 key, under the re-own limit, whose
    # (last_reowned_at, else created_at) anchor is older than the stale cutoff. Coarse SELECT filter only —
    # canonical key/fingerprint validation is applied per row before the CAS.
    return and_(
        ExecutionLog.status == "pending",
        ExecutionLog.dispatch_started_at.is_(None),
        ExecutionLog.reown_count < settings.recovery_max_reowns,
        ExecutionLog.idempotency_key.like("v1:%"),
        or_(
            and_(ExecutionLog.last_reowned_at.is_(None), ExecutionLog.created_at < cut_naive),
            and_(ExecutionLog.last_reowned_at.isnot(None), ExecutionLog.last_reowned_at < cut_tz),
        ),
    )


# Atomic ownership transfer. user_id is in the WHERE for tenant defence-in-depth. The type-safe OR-form
# stale predicate compares the NAIVE created_at against a naive cutoff and the tz last_reowned_at against
# a tz cutoff (no untyped COALESCE). RETURNING a row → exactly one transfer; empty → skip (lost the race
# or no longer eligible). Never touches status / dispatch_started_at / attempt_count / reconciliation.
# TOCTOU defence-in-depth: idempotency_key / request_fingerprint are immutable after the claim INSERT
# (no production writer ever changes them), but the CAS re-checks the exact key + fingerprint it validated
# so that even a hypothetical concurrent change of either → RETURNING empty (0 mutation).
_REOWN_CAS = text(
    "UPDATE execution_logs "
    "SET claim_generation = claim_generation + 1, "
    "    reown_count = reown_count + 1, "
    "    last_reowned_at = :now "
    "WHERE id = :id AND user_id = :uid "
    "  AND status = 'pending' AND dispatch_started_at IS NULL "
    "  AND claim_generation = :seen AND reown_count < :max "
    "  AND idempotency_key = :seen_key AND request_fingerprint = :seen_fp "
    "  AND ((last_reowned_at IS NULL AND created_at < :cut_naive) "
    "       OR (last_reowned_at IS NOT NULL AND last_reowned_at < :cut_tz)) "
    "RETURNING id, claim_generation, reown_count, last_reowned_at"
).bindparams(
    bindparam("now", type_=DateTime(timezone=True)),
    bindparam("cut_tz", type_=DateTime(timezone=True)),
    bindparam("cut_naive", type_=DateTime(timezone=False)),
    bindparam("seen", type_=Integer()),
    bindparam("max", type_=Integer()),
)

# Bound the number of rows any single user's batch may SCAN in one run (keyset advances past invalid /
# race rows so they can never starve valid candidates behind them; this cap keeps a pathological volume
# of poison rows from blowing the run's deadline). Realistic candidate sets are far smaller.
_MAX_SCAN_PER_USER = 10_000


async def run_reown_sweep(*, dry_run: Optional[bool] = None, now: Optional[datetime] = None,
                          batch_size: Optional[int] = None,
                          max_duration: Optional[float] = None) -> ReownSweepResult:
    """Controlled ownership transfer of stuck safe pending claims (feature OFF by default). Numeric only."""
    started = time.monotonic()
    # Fail-closed master gate: nothing at all happens unless explicitly enabled.
    if settings.recovery_reown_enabled is not True:
        return ReownSweepResult(enabled=False, lock_acquired=False,
                                dry_run=(settings.recovery_reown_dry_run is True))
    if dry_run is None:
        dry_run = settings.recovery_reown_dry_run is True
    batch_size = int(batch_size or settings.recovery_reown_batch_size)
    deadline = (started + max_duration) if max_duration is not None else None
    result = ReownSweepResult(enabled=True, lock_acquired=False, dry_run=dry_run)
    dialect = engine.dialect.name

    try:
        if dialect == "postgresql":
            async with engine.connect() as lock_conn:
                acquired = bool((await lock_conn.execute(
                    text("SELECT pg_try_advisory_lock(:ns, :op)"),
                    {"ns": _LOCK_NAMESPACE, "op": _LOCK_OPERATION})).scalar())
                if not acquired:
                    logger.info("reown sweep: another run is active")
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
                logger.info("reown sweep: another run is active")
                result.duration_ms = int((time.monotonic() - started) * 1000)
                return result
            async with _local_lock:
                result.lock_acquired = True
                await _sweep(dry_run, now, batch_size, dialect, result, deadline)
    finally:
        result.duration_ms = int((time.monotonic() - started) * 1000)

    logger.info("reown sweep%s: candidates=%d eligible=%d reowned=%d skipped_invalid=%d skipped_race=%d "
                "failed_batches=%d timed_out=%d dur=%dms", " dry-run" if dry_run else "",
                result.candidates, result.eligible, result.reowned, result.skipped_invalid,
                result.skipped_race, result.failed_batches, int(result.timed_out), result.duration_ms)
    return result


async def _sweep(dry_run, now, batch_size, dialect, result: ReownSweepResult, deadline):
    now_naive = _now_naive(now)
    now_tz = _now_tz(now)
    cut_naive = now_naive - timedelta(seconds=settings.recovery_reown_stale_seconds)
    cut_tz = now_tz - timedelta(seconds=settings.recovery_reown_stale_seconds)

    async with AsyncSessionLocal() as db0:
        users = [u for (u,) in (await db0.execute(
            select(ExecutionLog.user_id).where(_candidate_where(cut_naive, cut_tz)).distinct())).all()]

    for uid in users:
        if deadline is not None and time.monotonic() >= deadline:
            result.timed_out = True
            return
        try:
            async with AsyncSessionLocal() as db:
                if dialect == "postgresql":
                    await db.execute(text(f"SET LOCAL statement_timeout = '{_STATEMENT_TIMEOUT}'"))
                    await db.execute(text(f"SET LOCAL lock_timeout = '{_LOCK_TIMEOUT}'"))
                # Keyset pagination on the stable, indexable (created_at, id) order. The cursor advances
                # past EVERY row it scans — invalid (skipped) and race-lost (CAS empty) rows included — so a
                # poison row at the front of the order can never starve valid candidates behind it. Each row
                # is scanned at most once per run. Bounded by _MAX_SCAN_PER_USER and the deadline.
                last_created = None
                last_id = None
                scanned = 0
                while scanned < _MAX_SCAN_PER_USER:
                    if deadline is not None and time.monotonic() >= deadline:
                        result.timed_out = True
                        break
                    q = (select(ExecutionLog.id, ExecutionLog.claim_generation,
                                ExecutionLog.idempotency_key, ExecutionLog.request_fingerprint,
                                ExecutionLog.created_at)
                         .where(ExecutionLog.user_id == uid)
                         .where(_candidate_where(cut_naive, cut_tz)))
                    if last_id is not None:
                        q = q.where(or_(ExecutionLog.created_at > last_created,
                                        and_(ExecutionLog.created_at == last_created,
                                             ExecutionLog.id > last_id)))
                    rows = (await db.execute(
                        q.order_by(ExecutionLog.created_at, ExecutionLog.id).limit(batch_size))).all()
                    if not rows:
                        break
                    for rid, seen_gen, key, fp, created in rows:
                        scanned += 1
                        last_created, last_id = created, rid    # advance past this row regardless of outcome
                        result.candidates += 1
                        # Canonical validation is the final eligibility gate (coarse SQL LIKE is not enough).
                        if not (is_valid_v1_key(key) and is_valid_fingerprint(fp)):
                            result.skipped_invalid += 1
                            continue
                        result.eligible += 1
                        if dry_run:
                            continue
                        res = await db.execute(_REOWN_CAS, {
                            "id": rid, "uid": uid, "seen": int(seen_gen),
                            "max": settings.recovery_max_reowns, "now": now_tz,
                            "cut_naive": cut_naive, "cut_tz": cut_tz,
                            "seen_key": key, "seen_fp": fp})
                        if res.fetchone() is not None:
                            result.reowned += 1
                        else:
                            result.skipped_race += 1
                    if len(rows) < batch_size:
                        break
                if not dry_run:
                    await db.commit()
        except Exception:  # noqa: BLE001 — SAFE: no exception text/ids logged; the next user still runs
            result.failed_batches += 1
            logger.warning("reown sweep: a user batch failed; failed_batches=%d", result.failed_batches)
