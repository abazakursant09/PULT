"""Application-owned hold for selected managed YM review reads, not a quota limiter.

Imports deliberately register no ORM tables and acquire no database engine. Direct,
unmanaged, verification, discovery and publication calls are outside this policy.
"""
from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager, contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
import re

from .errors import ExecutionError

_DB_TIMEOUT = 2.0
# Injectable default boundary, resolved lazily ONLY for a managed binder.
AsyncSessionLocal = None
_UTC = timezone.utc
_GMT_DATE = re.compile(
    r"(?:Mon|Tue|Wed|Thu|Fri|Sat|Sun), [0-9]{2} "
    r"(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec) (?P<year>[0-9]{4}) "
    r"[0-9]{2}:[0-9]{2}:[0-9]{2} GMT"
)
_ASCTIME = re.compile(
    r"(?:Mon|Tue|Wed|Thu|Fri|Sat|Sun) "
    r"(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec) (?: [0-9]|[0-9]{2}) "
    r"[0-9]{2}:[0-9]{2}:[0-9]{2} (?P<year>[0-9]{4})"
)


def _utc(value):
    if isinstance(value, str):
        value = datetime.fromisoformat(value)
    if not isinstance(value, datetime):
        raise ValueError("invalid authority clock")
    return value.replace(tzinfo=_UTC) if value.tzinfo is None else value.astimezone(_UTC)


class YMReviewReadDeferred(ExecutionError):
    """A selected read was not admitted; never a write dispatch outcome."""

    def __init__(self, reason: str, not_before: datetime | None = None):
        self.reason = reason if reason in {"hold", "indeterminate", "authority", "identity"} else "authority"
        self.not_before = _utc(not_before) if not_before is not None else None
        super().__init__(self.RATE_LIMIT, f"selected review read deferred ({self.reason})")


@dataclass(frozen=True)
class _Managed:
    account_id: str
    workspace_id: str
    user_id: str
    business_id: str
    session_factory: object


@dataclass(frozen=True)
class _Selection:
    managed: _Managed
    client: object
    operation: str


_context: ContextVar[_Managed | None] = ContextVar("ym_review_read_context", default=None)
_selection: ContextVar[_Selection | None] = ContextVar("ym_review_read_selection", default=None)


@asynccontextmanager
async def bind_review_read_context(db, connection, account_ref, *, session_factory=None):
    """Resolve locally once; dedicated authority transactions never use this session."""
    managed = None
    try:
        if connection.marketplace == "yandex" and connection.marketplace_account_id is not None:
            from sqlalchemy import text

            with db.no_autoflush:
                async with asyncio.timeout(_DB_TIMEOUT):
                    row = (await db.execute(text(
                        "SELECT a.id, a.workspace_id, a.marketplace, a.identity_status, "
                        "a.external_account_id, w.owner_user_id FROM marketplace_accounts a "
                        "JOIN workspaces w ON w.id=a.workspace_id WHERE a.id=:id"
                    ), {"id": connection.marketplace_account_id})).mappings().one_or_none()
            if row is None:
                raise YMReviewReadDeferred("authority")
            if row["identity_status"] not in {"verified", "unverified", "unverified_legacy"}:
                raise YMReviewReadDeferred("authority")
            if row["identity_status"] == "verified":
                if (row["marketplace"] != "yandex" or row["workspace_id"] != connection.workspace_id
                        or row["owner_user_id"] != connection.user_id):
                    raise YMReviewReadDeferred("identity")
                business = row["external_account_id"]
                if not isinstance(business, str) or not business or account_ref != business:
                    raise YMReviewReadDeferred("identity")
                factory = session_factory or AsyncSessionLocal
                if factory is None:
                    from database import AsyncSessionLocal as factory
                managed = _Managed(row["id"], row["workspace_id"], connection.user_id, business, factory)
    except YMReviewReadDeferred:
        raise
    except Exception:
        raise YMReviewReadDeferred("authority") from None
    token = _context.set(managed)
    selected_token = _selection.set(None)
    try:
        yield
    finally:
        _selection.reset(selected_token)
        _context.reset(token)


@contextmanager
def selected_review_read(business_id, operation, *, client):
    managed = _context.get()
    selected = None
    if managed is not None:
        if business_id != managed.business_id or operation not in {"reviews", "comments"}:
            raise YMReviewReadDeferred("identity")
        selected = _Selection(managed, client, operation)
    token = _selection.set(selected)
    try:
        yield
    finally:
        _selection.reset(token)


def _managed_for(client):
    selected = _selection.get()
    if selected is None or selected.client is not client or selected.managed is not _context.get():
        return None
    return selected.managed


async def _locked_authority(db, managed):
    from sqlalchemy import text

    # Explicit SQL avoids ORM updated_at onupdate and serializes admission with observation.
    row = (await db.execute(text(
        "UPDATE marketplace_accounts SET ym_review_read_indeterminate=ym_review_read_indeterminate "
        "WHERE id=:id RETURNING workspace_id, marketplace, identity_status, external_account_id, "
        "ym_review_read_not_before, ym_review_read_indeterminate"
    ), {"id": managed.account_id})).mappings().one_or_none()
    if row is None:
        raise YMReviewReadDeferred("authority")
    owner = (await db.execute(text("SELECT owner_user_id FROM workspaces WHERE id=:id"),
                              {"id": managed.workspace_id})).scalar_one_or_none()
    if (row["marketplace"] != "yandex" or row["identity_status"] != "verified"
            or row["external_account_id"] != managed.business_id
            or row["workspace_id"] != managed.workspace_id or owner != managed.user_id):
        raise YMReviewReadDeferred("identity")
    # A separate clock query AFTER acquiring the row lock, never transaction-start NOW().
    sqlite = db.get_bind().dialect.name == "sqlite"
    clock = "SELECT strftime('%Y-%m-%d %H:%M:%S','now')" if sqlite else "SELECT clock_timestamp()"
    now = _utc((await db.execute(text(clock))).scalar_one())
    until = row["ym_review_read_not_before"]
    return (_utc(until) if until is not None else None, bool(row["ym_review_read_indeterminate"]), now, sqlite)


def _date(value, *, asctime=False):
    match = _GMT_DATE.fullmatch(value) or (asctime and _ASCTIME.fullmatch(value))
    if not match:
        raise ValueError("unsupported date")
    year = int(match.group("year"))
    if year == 0:
        raise ValueError("invalid calendar year")
    # Email parsing applies a century pivot even to four digits: restore the
    # literal Gregorian year and revalidate its month/day (including leap days).
    return _utc(parsedate_to_datetime(value).replace(year=year))


def _hints(headers, now, sqlite):
    bounds = []
    bad = False
    for name in ("X-RateLimit-Resource-Until", "Retry-After"):
        values = headers.get_list(name)
        if not values:
            continue
        if len(values) != 1:
            bad = True
            continue
        value = values[0].strip(" \t")
        try:
            if name == "Retry-After" and re.fullmatch(r"[0-9]+", value):
                digits = value.lstrip("0") or "0"
                if len(digits) > 12:
                    raise ValueError("overflow")
                seconds = int(digits)
                # SQLite clock floors seconds: add one conservatively for positive delays.
                bound = now + timedelta(seconds=seconds + (1 if sqlite and seconds else 0))
            else:
                bound = _date(value, asctime=name == "Retry-After")
            bounds.append(bound)
        except (ValueError, TypeError, OverflowError):
            bad = True
    return max(bounds) if bounds else None, bad


async def _authority(managed, response=None):
    """All work, commit and normal cleanup use an independent two-second boundary."""
    try:
        async with asyncio.timeout(_DB_TIMEOUT):
            async with managed.session_factory() as db:
                until, indeterminate, now, sqlite = await _locked_authority(db, managed)
                if response is not None:
                    from sqlalchemy import Boolean, DateTime, bindparam, text

                    candidate, bad = _hints(response.headers, now, sqlite)
                    stmt = text(
                        "UPDATE marketplace_accounts SET ym_review_read_not_before=CASE "
                        "WHEN :until IS NOT NULL AND (ym_review_read_not_before IS NULL "
                        "OR ym_review_read_not_before < :until) THEN :until "
                        "ELSE ym_review_read_not_before END, "
                        "ym_review_read_indeterminate=ym_review_read_indeterminate OR :bad "
                        "WHERE id=:id RETURNING ym_review_read_not_before, ym_review_read_indeterminate"
                    ).bindparams(bindparam("until", type_=DateTime(timezone=True)),
                                 bindparam("bad", type_=Boolean()))
                    # SQLite timestamps are stored without tz; keep comparison representation aligned.
                    value = candidate.replace(tzinfo=None) if sqlite and candidate is not None else candidate
                    saved = (await db.execute(stmt, {"id": managed.account_id, "until": value,
                                                     "bad": bad})).one_or_none()
                    if saved is None:
                        raise YMReviewReadDeferred("authority")
                    until = _utc(saved[0]) if saved[0] is not None else None
                    indeterminate = bool(saved[1])
                await db.commit()
        if indeterminate:
            raise YMReviewReadDeferred("indeterminate", until)
        if response is not None or (until is not None and until > now):
            raise YMReviewReadDeferred("hold", until)
    except YMReviewReadDeferred:
        raise
    except Exception:
        raise YMReviewReadDeferred("authority") from None


async def admit_selected_read(client):
    managed = _managed_for(client)
    if managed is not None:
        await _authority(managed)


async def observe_selected_read(client, response):
    managed = _managed_for(client)
    if managed is not None and response.status_code in {420, 429}:
        await _authority(managed, response)
