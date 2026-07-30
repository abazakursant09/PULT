"""PULT-LAUNCH-2.5E-2B-2 — async observation-retention sweep (feature OFF).

Bounds the growth of the two APPEND-ONLY parent observation tables written by the change-only ingest,
WITHOUT ever losing the useful history or the current state:

  * resolved change-points older than 180 days are deleted — EXCEPT the latest of each logical series;
  * unassigned change-points older than 30 days are deleted — EXCEPT the latest of each series;
  * the LATEST row of every series is kept forever, regardless of age;
  * age is the evidence time `fetched_at` (UTC). `last_verified_at` NEVER extends a non-latest row's
    life; `created_at` is only a deterministic tie-break inside a series, never the cutoff.

Only the two PARENT tables are swept; the Yandex child evidence rows are removed by the database via the
parent's ON DELETE CASCADE (never deleted directly), so a parent never survives without its children and
children never survive without their parent.

Fully async on the existing engine / AsyncSessionLocal — NEVER asyncio.to_thread, never an AsyncSession
across threads. On PostgreSQL a session-level advisory lock (held on a dedicated connection for the whole
run) serialises concurrent sweeps; a second run gets lock_acquired=False and deletes nothing. Ingest only
INSERTs newer rows, so a row that is a delete candidate (rn>1, older than its cutoff) can never become
the latest between selection and DELETE — worst case one extra old row survives a cycle; the latest is
never lost.

Reached by NOTHING yet: no scheduler tick, no endpoint. `run_observation_retention` returns `disabled`
immediately (0 SQL, 0 rows) while observation_retention_enabled is False — even for dry_run.
"""
from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Optional

from sqlalchemy import text

from config import settings
from database import AsyncSessionLocal, engine

logger = logging.getLogger(__name__)

# Retention windows (days). Age is measured on fetched_at (UTC). The latest of each series is exempt.
RESOLVED_MAX_AGE_DAYS = 180
UNASSIGNED_MAX_AGE_DAYS = 30

# Batch bounds — one batch is one short transaction.
DEFAULT_BATCH_SIZE = 5000
_MAX_BATCH_SIZE = 100_000

# Advisory-lock key: a stable, documented pair of signed int32 (both < 2**31-1). NEVER Python hash().
#   namespace = 0x50554C54 = "PULT",  operation = 0x4F425352 = "OBSR" (observation retention).
_LOCK_NAMESPACE = 0x50554C54   # 1347767380
_LOCK_OPERATION = 0x4F425352   # 1329746770
assert 0 < _LOCK_NAMESPACE < 2 ** 31 and 0 < _LOCK_OPERATION < 2 ** 31

# Local (single-process) guard for SQLite tests ONLY — NOT a production concurrency invariant.
_local_lock = asyncio.Lock()


# Per-batch PostgreSQL guards (constant SQL — never operator/user input). SET LOCAL is re-issued inside
# every batch transaction (it lasts only until that transaction commits).
_STATEMENT_TIMEOUT = "30s"
_LOCK_TIMEOUT = "5s"
# A whole sweep is bounded so it can never run past its hourly slot; checked BETWEEN batches so a
# committed batch is never split. The scheduler passes this; the service default is None (no limit).
DEFAULT_MAX_DURATION_SECONDS = 600


@dataclass
class RetentionResult:
    enabled: bool
    lock_acquired: bool
    dry_run: bool
    price_candidates: int = 0
    promotion_candidates: int = 0
    price_removed: int = 0
    promotion_removed: int = 0
    # Children are removed by CASCADE; their count cannot be honestly read from the parent DELETE's
    # rowcount, so it is deliberately None rather than invented.
    child_rows_removed: Optional[int] = None
    batches: int = 0
    failed_batches: int = 0
    # True ONLY when a real per-run deadline was reached between batches (never for dry-run, disabled,
    # or a busy advisory lock). The scheduler treats timed_out as a failed run.
    timed_out: bool = False
    duration_ms: int = 0


@dataclass(frozen=True)
class _Table:
    name: str
    partition: str   # the logical-series key, as a SQL column list (fixed constants, never user input)


_PRICE = _Table(
    "marketplace_price_observations",
    "marketplace_store_id, external_product_id, observation_kind, promotion_key, source")
_PROMO = _Table(
    "marketplace_promotion_observations",
    "marketplace_account_id, external_product_id, promotion_id, source")

# The candidate predicate, shared by the count (dry-run) and the delete: a NON-latest row (rn>1) whose
# fetched_at is older than the cutoff for its resolution_status. The latest (rn=1) can never match.
_CANDIDATE_PREDICATE = (
    "rn > 1 AND ("
    " (resolution_status = 'resolved'   AND fetched_at < :resolved_cutoff)"
    " OR (resolution_status = 'unassigned' AND fetched_at < :unassigned_cutoff) )")


def _ranked_cte(t: _Table, *, account_scoped: bool) -> str:
    where = "WHERE marketplace_account_id = :acc" if account_scoped else ""
    return (
        f"SELECT id, fetched_at, resolution_status, row_number() OVER ("
        f"PARTITION BY {t.partition} ORDER BY fetched_at DESC, created_at DESC, id DESC) AS rn "
        f"FROM {t.name} {where}")


def _count_sql(t: _Table):
    # global count of all candidates (a series never spans accounts, so no account filter is needed)
    return text(f"WITH ranked AS ({_ranked_cte(t, account_scoped=False)}) "
                f"SELECT count(*) FROM ranked WHERE {_CANDIDATE_PREDICATE}")


def _delete_sql(t: _Table, dialect: str):
    ranked = _ranked_cte(t, account_scoped=True)
    candidates = (f"SELECT id FROM ranked WHERE {_CANDIDATE_PREDICATE} "
                  f"ORDER BY fetched_at, id LIMIT :batch")
    if dialect == "postgresql":
        # one statement: candidate selection + delete, so ingest cannot change the set in between.
        return text(f"WITH ranked AS ({ranked}), candidates AS ({candidates}) "
                    f"DELETE FROM {t.name} AS target USING candidates WHERE target.id = candidates.id")
    # SQLite: DELETE ... USING is unsupported; a subquery IN is single-statement and equally atomic.
    return text(f"DELETE FROM {t.name} WHERE id IN (WITH ranked AS ({ranked}) {candidates})")


def _utc_naive(dt: Optional[datetime]) -> datetime:
    """Current-or-given time as naive UTC, matching the naive-UTC fetched_at column. An aware value is
    converted to UTC (astimezone), never merely stripped; a naive value is treated as already-UTC."""
    if dt is None:
        dt = datetime.now(timezone.utc)
    if dt.tzinfo is not None:
        dt = dt.astimezone(timezone.utc).replace(tzinfo=None)
    return dt


def _validate_batch(batch_size: int) -> int:
    if not isinstance(batch_size, int) or batch_size <= 0 or batch_size > _MAX_BATCH_SIZE:
        raise ValueError("batch_size out of range")
    return batch_size


async def _distinct_accounts(db) -> list[str]:
    rows = await db.execute(text(
        f"SELECT marketplace_account_id FROM {_PRICE.name} "
        f"UNION SELECT marketplace_account_id FROM {_PROMO.name}"))
    return [r[0] for r in rows.all()]


async def _sweep_table(account: str, t: _Table, params: dict, batch_size: int, dialect: str,
                       result: RetentionResult, *, price: bool, deadline: Optional[float] = None) -> str:
    """Delete candidates for ONE account/table in bounded batches. Returns 'ok' (clean), 'failed' (a
    batch errored → caller skips the rest of THIS account), or 'stopped' (the per-run deadline was
    reached between batches → caller stops the WHOLE sweep). One batch = one short transaction;
    counters advance only after a successful commit; a committed batch is never rolled back.

    Each PostgreSQL batch transaction first re-issues SET LOCAL statement_timeout / lock_timeout (they
    last only to the next commit); on SQLite these are skipped. A statement/lock timeout surfaces as a
    normal error → the current batch rolls back, one failed batch is counted, and 'failed' is returned.

    The error is a SAFE, NUMBER-ONLY log: never logger.exception / exc_info / the exception object /
    str(exc) / SQL / SQL parameters (any of which could carry account/store/product/SKU/external/
    promotion identifiers). The session is closed by its context manager and never reused."""
    stmt = _delete_sql(t, dialect)
    p = dict(params, acc=account, batch=batch_size)
    async with AsyncSessionLocal() as db:
        while True:
            if deadline is not None and time.monotonic() >= deadline:
                result.timed_out = True                    # checked BETWEEN batches — never mid-batch
                return "stopped"
            try:
                if dialect == "postgresql":
                    await db.execute(text(f"SET LOCAL statement_timeout = '{_STATEMENT_TIMEOUT}'"))
                    await db.execute(text(f"SET LOCAL lock_timeout = '{_LOCK_TIMEOUT}'"))
                res = await db.execute(stmt, p)
                removed = res.rowcount or 0
                await db.commit()
            except Exception:
                await db.rollback()
                result.failed_batches += 1
                logger.warning("observation retention: batch failed; failed_batches=%d",
                               result.failed_batches)
                return "failed"
            result.batches += 1
            if price:
                result.price_removed += removed
            else:
                result.promotion_removed += removed
            if removed < batch_size:
                return "ok"


async def _run_sweep(dry_run: bool, params: dict, batch_size: int, dialect: str,
                     result: RetentionResult, deadline: Optional[float] = None) -> None:
    async with AsyncSessionLocal() as db:
        result.price_candidates = int((await db.execute(_count_sql(_PRICE), params)).scalar() or 0)
        result.promotion_candidates = int((await db.execute(_count_sql(_PROMO), params)).scalar() or 0)
        if dry_run:
            return
        accounts = await _distinct_accounts(db)
    for account in accounts:                                   # each account independent, its own session(s)
        st = await _sweep_table(account, _PRICE, params, batch_size, dialect, result,
                                price=True, deadline=deadline)
        if st == "stopped":                                    # deadline reached -> stop the WHOLE sweep
            return
        if st == "failed":                                     # price failed -> skip THIS account's promo
            continue
        if await _sweep_table(account, _PROMO, params, batch_size, dialect, result,
                              price=False, deadline=deadline) == "stopped":
            return


async def run_observation_retention(*, dry_run: bool = False, now: Optional[datetime] = None,
                                    batch_size: int = DEFAULT_BATCH_SIZE,
                                    max_duration: Optional[float] = None) -> RetentionResult:
    """Prune old non-latest observation change-points (feature OFF by default).

    While observation_retention_enabled is False this returns immediately with enabled=False — no lock is
    requested, no SQL runs, no row changes — even for dry_run. `now` is for tests only; production uses
    the current UTC. `max_duration` (seconds) bounds one whole sweep: it is checked BETWEEN batches (never
    mid-batch, so a committed batch is never split), and when reached the whole sweep stops with
    timed_out=True — the next run continues idempotently. There is no endpoint."""
    started = time.monotonic()
    if not settings.observation_retention_enabled:
        return RetentionResult(enabled=False, lock_acquired=False, dry_run=dry_run)

    batch_size = _validate_batch(batch_size)
    now_utc = _utc_naive(now)
    params = {
        "resolved_cutoff": now_utc - timedelta(days=RESOLVED_MAX_AGE_DAYS),
        "unassigned_cutoff": now_utc - timedelta(days=UNASSIGNED_MAX_AGE_DAYS),
    }
    deadline = (started + max_duration) if max_duration is not None else None
    result = RetentionResult(enabled=True, lock_acquired=False, dry_run=dry_run)
    dialect = engine.dialect.name

    try:
        if dialect == "postgresql":
            # session-level advisory lock on a DEDICATED connection held for the whole run; the same
            # connection unlocks in finally, and connection close is the backstop.
            async with engine.connect() as lock_conn:
                acquired = bool((await lock_conn.execute(
                    text("SELECT pg_try_advisory_lock(:ns, :op)"),
                    {"ns": _LOCK_NAMESPACE, "op": _LOCK_OPERATION})).scalar())
                if not acquired:
                    logger.info("observation retention: another run is active")
                    result.duration_ms = int((time.monotonic() - started) * 1000)
                    return result
                result.lock_acquired = True
                try:
                    await _run_sweep(dry_run, params, batch_size, dialect, result, deadline)
                finally:
                    await lock_conn.execute(text("SELECT pg_advisory_unlock(:ns, :op)"),
                                            {"ns": _LOCK_NAMESPACE, "op": _LOCK_OPERATION})
        else:
            # SQLite (tests only): a local asyncio lock — NOT a production concurrency invariant.
            if _local_lock.locked():
                logger.info("observation retention: another run is active")
                result.duration_ms = int((time.monotonic() - started) * 1000)
                return result
            async with _local_lock:
                result.lock_acquired = True
                await _run_sweep(dry_run, params, batch_size, dialect, result, deadline)
    finally:
        result.duration_ms = int((time.monotonic() - started) * 1000)

    if dry_run:
        logger.info("observation retention dry-run: candidates %d price, %d promotion; duration %d ms",
                    result.price_candidates, result.promotion_candidates, result.duration_ms)
    else:
        logger.info("observation retention: removed %d price, %d promotion; batches %d; failed %d; "
                    "duration %d ms", result.price_removed, result.promotion_removed,
                    result.batches, result.failed_batches, result.duration_ms)
    return result
