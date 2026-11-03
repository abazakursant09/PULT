"""Application-owned YM cabinet hold, not a provider quota limiter.

Imports deliberately register no ORM tables and acquire no database engine. Direct,
unmanaged and bootstrap calls remain outside this policy. Historical B1 exports
retain their narrow review-read behavior; broader operations require a B2 binding.
"""
from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager, contextmanager, nullcontext
from contextvars import ContextVar
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
import re
import copy
import hmac
import json as jsonlib
import secrets

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
    admitted: list = field(default_factory=list, repr=False, compare=False)


_context: ContextVar[_Managed | None] = ContextVar("ym_review_read_context", default=None)
_selection: ContextVar[_Selection | None] = ContextVar("ym_review_read_selection", default=None)
_first_write = ContextVar("ym_cabinet_first_write", default=None)

# Written only by existing successful same-secret identity capture/save paths.
SECRET_GENERATION_KEY = "ym_secret_generation"
CABINET_PROOF_KEY = "ym_cabinet_proof"


@dataclass(frozen=True)
class CredentialSnapshot:
    credential_id: str
    connection_id: str
    scope: str
    secret_enc: bytes = field(repr=False)
    meta: dict = field(repr=False)
    verification_status: str
    verified_at: object
    updated_at: object

    @property
    def id(self):
        return self.credential_id


@dataclass(frozen=True)
class CabinetBinding(_Managed):
    connection_id: str
    credential_id: str
    scope: str
    owner_task: object = field(repr=False)
    witness: bytes = field(repr=False)
    credential_witness: bytes = field(repr=False)
    token_witness: bytes = field(repr=False)
    campaign_ids: tuple[str, ...]


@dataclass(frozen=True)
class ClaimStamp:
    log_id: str
    operation_key: str
    request_fingerprint: str
    claim_generation: int


@dataclass(frozen=True)
class WriteIntent:
    binding: CabinetBinding
    client: object = field(repr=False)
    operation: str
    request_witness: bytes = field(repr=False)


_PERMIT_SEAL = object()


@dataclass
class _PermitState:
    used: bool = False
    active: bool = False


@dataclass(frozen=True)
class FirstPermit:
    intent: WriteIntent
    stamp: ClaimStamp
    _seal: object = field(default=None, repr=False)
    _state: _PermitState = field(default_factory=_PermitState, repr=False, compare=False)

    @property
    def used(self):
        return self._state.used

    @property
    def active(self):
        return self._state.active


class YMWritePermitError(ExecutionError):
    def __init__(self):
        super().__init__("YM_WRITE_PERMIT_INVALID", "cabinet write permit mismatch", retryable=False)


class YMClaimCommitUncertain(ExecutionError):
    def __init__(self):
        self.transaction_unusable = True
        super().__init__("YM_CLAIM_COMMIT_UNCERTAIN", "claim commit not confirmed", retryable=False)


def _snapshot_material(snapshot):
    return _canonical((snapshot.credential_id, snapshot.connection_id, snapshot.scope,
                       snapshot.secret_enc.hex(), snapshot.meta, snapshot.verification_status,
                       str(snapshot.verified_at), str(snapshot.updated_at)))


def _canonical(value):
    return jsonlib.dumps(value, sort_keys=True, ensure_ascii=True, separators=(",", ":"),
                         allow_nan=False).encode("utf-8")


def _mac(key, value):
    return hmac.digest(key, value, "sha256")


async def load_credential_snapshot(db, *, connection_id, scope):
    from sqlalchemy import text

    with getattr(db, "no_autoflush", nullcontext()):
        row = (await db.execute(text(
            "SELECT id, connection_id, scope, secret_enc, meta, verification_status, verified_at, "
            "updated_at FROM api_credentials WHERE connection_id=:cid AND scope=:scope"
        ), {"cid": connection_id, "scope": scope})).mappings().one_or_none()
    if row is None:
        return None
    metadata = row["meta"]
    if isinstance(metadata, str):
        metadata = jsonlib.loads(metadata)
    if not isinstance(metadata, dict):
        raise YMReviewReadDeferred("authority")
    return CredentialSnapshot(row["id"], row["connection_id"], row["scope"],
                              bytes(row["secret_enc"]), copy.deepcopy(metadata),
                              row["verification_status"], row["verified_at"], row["updated_at"])


async def load_probe_credential_snapshot(db, connection, *, scope):
    """Pin the exact YM probe secret; other providers keep their original runner path."""
    if connection.marketplace != "yandex":
        return None
    try:
        async with asyncio.timeout(_DB_TIMEOUT):
            snapshot = await load_credential_snapshot(db, connection_id=connection.id, scope=scope)
        if snapshot is None:
            # A disappeared selected credential is not permission to use stale ORM plaintext.
            raise YMReviewReadDeferred("authority")
        return snapshot
    except YMReviewReadDeferred:
        raise
    except Exception:
        raise YMReviewReadDeferred("authority") from None


def _proof(snapshot):
    proof = snapshot.meta.get(CABINET_PROOF_KEY)
    if proof is None:
        return None  # Historical/no-witness credentials stay explicitly unmanaged.
    nonce = snapshot.meta.get(SECRET_GENERATION_KEY)
    if (not isinstance(proof, dict) or type(proof.get("version")) is not int
            or proof["version"] != 1 or not isinstance(nonce, str) or not nonce
            or proof.get("generation") != nonce
            or proof.get("credential_id") != snapshot.credential_id
            or not isinstance(proof.get("account_id"), str) or not proof["account_id"]
            or not isinstance(proof.get("business_id"), str) or not proof["business_id"]):
        raise YMReviewReadDeferred("authority")
    if snapshot.verification_status != "verified":
        raise YMReviewReadDeferred("identity")
    return proof


@asynccontextmanager
async def bind_cabinet_context(db, connection, snapshot, *, session_factory=None):
    binding = None
    try:
        if connection.marketplace == "yandex" and snapshot is not None:
            if snapshot.connection_id != connection.id:
                raise YMReviewReadDeferred("identity")
            proof = _proof(snapshot)
            if proof is not None:
                from sqlalchemy import text
                from .credential_vault import decrypt

                async with bind_review_read_context(db, connection, proof["business_id"],
                                                    session_factory=session_factory):
                    managed = _context.get()
                    if managed is None:
                        # Existing proof cannot become bootstrap when its authority is lost.
                        raise YMReviewReadDeferred("identity")
                    if managed is not None:
                        if managed.account_id != proof["account_id"]:
                            raise YMReviewReadDeferred("identity")
                        current = await load_credential_snapshot(
                            db, connection_id=connection.id, scope=snapshot.scope)
                        if current is None or _snapshot_material(current) != _snapshot_material(snapshot):
                            raise YMReviewReadDeferred("identity")
                        with db.no_autoflush:
                            campaigns = (await db.execute(text(
                                "SELECT external_store_id FROM marketplace_stores WHERE "
                                "marketplace_account_id=:id AND marketplace='yandex' "
                                "AND external_store_id IS NOT NULL"
                            ), {"id": managed.account_id})).scalars().all()
                        key = secrets.token_bytes(32)
                        binding = CabinetBinding(
                            managed.account_id, managed.workspace_id, managed.user_id,
                            managed.business_id, managed.session_factory, connection.id,
                            snapshot.credential_id, snapshot.scope, asyncio.current_task(), key,
                            _mac(key, _snapshot_material(snapshot)),
                            _mac(key, decrypt(snapshot.secret_enc).encode("utf-8")), tuple(campaigns))
    except YMReviewReadDeferred:
        raise
    except Exception:
        raise YMReviewReadDeferred("authority") from None
    token = _context.set(binding)
    selected_token = _selection.set(None)
    permit_token = _first_write.set(None)
    try:
        yield binding
    finally:
        _first_write.reset(permit_token)
        _selection.reset(selected_token)
        _context.reset(token)


_B2_OPERATIONS = frozenset({
    "reviews", "comments", "publish_feedback_answer", "resolve_business_id", "list_campaigns",
    "offer_mappings", "campaign_prices", "campaign_stocks", "campaign_orders", "campaign_returns",
    "list_promos", "list_promo_offers", "probe_auth_token",
})


@contextmanager
def selected_yandex_operation(operation, *, client, business_id=None, campaign_id=None):
    managed = _context.get()
    selection = None
    if isinstance(managed, CabinetBinding):
        if _first_write.get() is not None and managed.owner_task is not asyncio.current_task():
            raise YMWritePermitError()
        if (managed.owner_task is not asyncio.current_task() or operation not in _B2_OPERATIONS
                or (business_id is not None and str(business_id) != managed.business_id)
                or (campaign_id is not None and str(campaign_id) not in managed.campaign_ids)):
            raise YMReviewReadDeferred("identity")
        selection = _Selection(managed, client, operation)
    elif managed is not None and operation in {"reviews", "comments"}:
        if business_id != managed.business_id:
            raise YMReviewReadDeferred("identity")
        selection = _Selection(managed, client, operation)
    token = _selection.set(selection)
    try:
        yield
    finally:
        _selection.reset(token)


def _request_material(*, method, path, token, auth_header, extra_headers, json, params):
    return _canonical((method, path, token, auth_header, extra_headers, json, params))


def make_write_intent(binding, *, client, request, token):
    if binding is None:
        return None
    if not isinstance(binding, CabinetBinding) or binding.owner_task is not asyncio.current_task():
        raise YMWritePermitError()
    if not hmac.compare_digest(binding.token_witness, _mac(binding.witness, token.encode("utf-8"))):
        raise YMWritePermitError()
    # request is the shared pure publish builder's exact HTTP descriptor.
    material = _request_material(token=token, **request)
    return WriteIntent(binding, client, "publish_feedback_answer", _mac(binding.witness, material))


@contextmanager
def activate_first_write(permit, *, stamp):
    if permit is None:
        yield
        return
    if (not isinstance(permit, FirstPermit) or permit._seal is not _PERMIT_SEAL
            or permit.used or permit.active or permit.stamp != stamp
            or permit.intent.binding.owner_task is not asyncio.current_task()
            or _context.get() is not permit.intent.binding):
        raise YMWritePermitError()
    permit._state.active = True
    token = _first_write.set(permit)
    try:
        yield
    finally:
        permit._state.active = False
        # Leaving dispatch without consumption cannot revive permission in another dispatch.
        permit._state.used = True
        _first_write.reset(token)


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
    dialect = db.dialect if hasattr(db, "dialect") else db.get_bind().dialect
    sqlite = dialect.name == "sqlite"
    clock = "SELECT strftime('%Y-%m-%d %H:%M:%S','now')" if sqlite else "SELECT clock_timestamp()"
    now = _utc((await db.execute(text(clock))).scalar_one())
    until = row["ym_review_read_not_before"]
    return (_utc(until) if until is not None else None, bool(row["ym_review_read_indeterminate"]), now, sqlite)


async def _revalidate_binding(db, binding, *, credential=True):
    if not isinstance(binding, CabinetBinding):
        return
    from sqlalchemy import text

    if binding.owner_task is not asyncio.current_task():
        raise YMReviewReadDeferred("identity")
    row = (await db.execute(text(
        "SELECT marketplace_account_id, workspace_id, user_id, marketplace FROM "
        "marketplace_connections WHERE id=:id"
    ), {"id": binding.connection_id})).mappings().one_or_none()
    if (row is None or row["marketplace_account_id"] != binding.account_id
            or row["workspace_id"] != binding.workspace_id or row["user_id"] != binding.user_id
            or row["marketplace"] != "yandex"):
        raise YMReviewReadDeferred("identity")
    if not credential:
        return  # Already-admitted response: rotation cannot erase this cabinet's observation.
    current = await load_credential_snapshot(db, connection_id=binding.connection_id, scope=binding.scope)
    if current is None or not hmac.compare_digest(
            _mac(binding.witness, _snapshot_material(current)), binding.credential_witness):
        raise YMReviewReadDeferred("identity")


class ClaimGrant:
    """Caller-local provisional admission. Never stored in ContextVars or permits."""

    def __init__(self, db, binding, intent, savepoint=None, authority=None):
        self._db, self.binding, self.intent = db, binding, intent
        self._savepoint, self._authority = savepoint, authority
        self._state = "open"
        self._deadline = None
        self._timer = None
        self._timer_active = False

    def _stop_timer(self):
        if self._timer_active and self._timer is not None and not self._timer.expired():
            self._timer.reschedule(None)

    async def _discard(self):
        self._state = "uncertain"
        self._stop_timer()
        # Never commit caller work. Invalidate the affected transaction/connection;
        # callers must still treat transaction_unusable as a reconcile boundary.
        async with asyncio.timeout(_DB_TIMEOUT):
            await self._db.invalidate()

    async def rollback_unclaimed(self):
        if self._state != "open":
            raise YMWritePermitError()
        self._state = "cleaning"
        self._stop_timer()
        try:
            if self._savepoint is not None:
                async with asyncio.timeout(_DB_TIMEOUT):
                    await self._savepoint.rollback()
        except BaseException as exc:
            try:
                await self._discard()
            except BaseException:
                if isinstance(exc, asyncio.CancelledError):
                    raise exc
                raise YMClaimCommitUncertain() from None
            if isinstance(exc, asyncio.CancelledError):
                raise
            raise YMClaimCommitUncertain() from None
        self._state = "rolled_back"

    async def allow_new_claim(self):
        if self._state != "open":
            raise YMWritePermitError()
        if self._authority is not None:
            until, indeterminate, now, _ = self._authority
            if indeterminate or (until is not None and until > now):
                await self.rollback_unclaimed()
                raise YMReviewReadDeferred("indeterminate" if indeterminate else "hold", until)
        if self._savepoint is not None:
            await self._savepoint.commit()  # RELEASE; parent transaction retains authority lock.
        self._state = "granted"

    async def commit_claim(self, commit, *, stamp):
        from sqlalchemy.exc import IntegrityError

        if self._state != "granted" or not isinstance(stamp, ClaimStamp):
            raise YMWritePermitError()
        if self.binding is not None and (
                self.intent is None or self.intent.binding is not self.binding
                or self.binding.owner_task is not asyncio.current_task()
                or not stamp.log_id or not stamp.operation_key or not stamp.request_fingerprint
                or type(stamp.claim_generation) is not int):
            raise YMWritePermitError()
        self._state = "committing"
        try:
            if self._deadline is not None and asyncio.get_running_loop().time() >= self._deadline:
                raise YMClaimCommitUncertain()
            await commit()  # Exactly the original caller-supplied parent commit, once.
            if self._deadline is not None and asyncio.get_running_loop().time() >= self._deadline:
                raise YMClaimCommitUncertain()
        except asyncio.CancelledError:
            self._state = "uncertain"
            raise
        except IntegrityError:
            self._state = "insert_conflict"
            raise  # Preserve caller's original unique-INSERT winner resolution.
        except Exception:
            self._state = "uncertain"
            raise YMClaimCommitUncertain() from None
        self._state = "committed"
        self._stop_timer()
        return FirstPermit(self.intent, stamp, _PERMIT_SEAL) if self.binding is not None else None


@asynccontextmanager
async def claim_admission(db, *, binding, intent):
    from sqlalchemy.exc import IntegrityError

    if binding is None:
        yield ClaimGrant(db, None, None)
        return
    if (not isinstance(binding, CabinetBinding) or not isinstance(intent, WriteIntent)
            or intent.binding is not binding or binding.owner_task is not asyncio.current_task()
            or _context.get() is not binding):
        raise YMWritePermitError()
    # No implicit flush/lock inversion for any unrelated ExecutionLog work.
    for row in (*db.new, *db.dirty, *db.deleted):
        if getattr(type(row), "__tablename__", None) == "execution_logs":
            raise YMReviewReadDeferred("authority")
    grant = None
    try:
        # This surrounds the yielded claim flush AND original commit, not just admission SQL.
        deadline = asyncio.get_running_loop().time() + _DB_TIMEOUT
        async with asyncio.timeout_at(deadline) as timer:
            with db.no_autoflush:
                connection = await db.connection()
                if connection.dialect.name == "sqlite":
                    # aiosqlite legacy mode does not BEGIN for SELECT/SAVEPOINT. Ensure
                    # RELEASE cannot commit the authority update outside caller commit.
                    raw = await connection.get_raw_connection()
                    if not raw.driver_connection.in_transaction:
                        await connection.exec_driver_sql("BEGIN")
                savepoint = await connection.begin_nested()
                grant = ClaimGrant(db, binding, intent, savepoint)
                grant._deadline = deadline
                grant._timer = timer
                grant._timer_active = True
                grant._authority = await _locked_authority(connection, binding)
                await _revalidate_binding(connection, binding)
            yield grant
        grant._timer_active = False
    except BaseException as exc:
        if grant is not None:
            grant._timer_active = False  # timeout.__aexit__ has already run.
        if isinstance(exc, IntegrityError) and grant is not None and grant._state == "granted":
            grant._state = "insert_conflict"
        if grant is not None and grant._state == "insert_conflict":
            raise  # Caller retains original aborted-transaction rollback + winner lookup.
        if grant is not None and grant._state == "open":
            await grant.rollback_unclaimed()
        elif grant is not None and grant._state in {"granted", "committing", "uncertain"}:
            try:
                await grant._discard()
            except BaseException:
                if isinstance(exc, asyncio.CancelledError):
                    raise exc
                raise YMClaimCommitUncertain() from None
            if not isinstance(exc, asyncio.CancelledError):
                raise YMClaimCommitUncertain() from None
        if isinstance(exc, (YMReviewReadDeferred, YMWritePermitError, YMClaimCommitUncertain,
                            asyncio.CancelledError)):
            raise
        raise YMReviewReadDeferred("authority") from None
    finally:
        if grant is not None:
            grant._timer_active = False
        if grant is not None and grant._state == "open":
            await grant.rollback_unclaimed()


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
        try:
            if hasattr(headers, "get_list"):
                values = headers.get_list(name)
            else:
                # Injected transports may return their original mapping container.
                # Preserve differently cased duplicates/list values; never split date commas.
                values = []
                for key, raw in headers.items():
                    if isinstance(key, str) and key.lower() == name.lower():
                        values.extend(raw if isinstance(raw, (list, tuple)) else [raw])
        except (AttributeError, TypeError, ValueError):
            bad = True
            continue
        if not values:
            continue
        if len(values) != 1:
            bad = True
            continue
        if not isinstance(values[0], str):
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
                await _revalidate_binding(db, managed, credential=response is None)
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
        selected = _selection.get()
        if isinstance(managed, CabinetBinding):
            selected.admitted[:] = [asyncio.current_task()]


async def observe_selected_read(client, response):
    managed = _managed_for(client)
    if isinstance(managed, CabinetBinding):
        selected = _selection.get()
        if selected.admitted != [asyncio.current_task()]:
            raise YMReviewReadDeferred("identity")
        selected.admitted.clear()  # One actual response per successfully admitted attempt.
    if managed is not None and response.status_code in {420, 429}:
        await _authority(managed, response)


async def admit_yandex_attempt(client, *, method, path, token, auth_header,
                               extra_headers, json, params, attempt):
    managed = _managed_for(client)
    if managed is None:
        return
    if not isinstance(managed, CabinetBinding):
        await _authority(managed)
        return
    selected = _selection.get()
    selected.admitted.clear()
    if (managed.owner_task is not asyncio.current_task()
            or not hmac.compare_digest(managed.token_witness,
                                       _mac(managed.witness, token.encode("utf-8")))):
        raise YMWritePermitError()
    permit = _first_write.get()
    if permit is not None:
        if (not permit.active or permit.used or attempt != 1 or permit.intent.binding is not managed
                or permit.intent.client is not client or selected.operation != permit.intent.operation
                or not hmac.compare_digest(permit.intent.request_witness, _mac(
                    managed.witness, _request_material(method=method, path=path, token=token,
                        auth_header=auth_header, extra_headers=extra_headers, json=json, params=params)))):
            raise YMWritePermitError()
        permit._state.used = True
        selected.admitted[:] = [asyncio.current_task()]
        return  # Pure pre-admitted first dispatch: NO DB read/fresh hold check in fence gap.
    if selected.operation == "publish_feedback_answer":
        raise YMWritePermitError()
    await _authority(managed)
    selected.admitted[:] = [asyncio.current_task()]


async def observe_yandex_response(client, response):
    await observe_selected_read(client, response)


async def admit_probe_request(client, request):
    """Validate the explicitly selected probe instance/secret before any custom HTTP seam."""
    selected = _selection.get()
    if selected is None or selected.operation != "probe_auth_token":
        return
    if not isinstance(selected.managed, CabinetBinding):
        return
    if selected.client is not client or selected.managed is not _context.get():
        raise YMReviewReadDeferred("identity")
    import httpx

    try:
        headers = httpx.Headers(request.headers)
        keys = headers.get_list("Api-Key")
        if (len(keys) != 1 or request.method != "POST"
                or request.url != "https://api.partner.market.yandex.ru/v2/auth/token"
                or request.json != {}):
            raise YMReviewReadDeferred("identity")
        token = keys[0]
    except YMReviewReadDeferred:
        raise
    except Exception:
        raise YMReviewReadDeferred("identity") from None
    await admit_yandex_attempt(
        client, method=request.method, path="/v2/auth/token", token=token,
        auth_header="Api-Key", extra_headers=None, json=request.json, params=None, attempt=1,
    )
