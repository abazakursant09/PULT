"""
SECURITY-2C-2 — PostgreSQL-atomic auth throttle (brute-force / credential-stuffing / spraying).

Three independent dimensions per action — pair (identity+IP), identity-global, IP-global — each a
sliding window whose counter is advanced by a SINGLE atomic `INSERT ... ON CONFLICT DO UPDATE ...
RETURNING` (never SELECT→+1→UPDATE), so N concurrent requests are counted as N and the state survives
restarts and is shared across workers. The upsert commits immediately so a failed login's count is not
lost when the request rolls back.

No PII is stored: the row key is HMAC-SHA256(SECRET_KEY, "pult:auth-throttle:v1:<dim>:<value>") — an
email/IP cannot be recovered, unknown and real emails hash into the same space (no existence oracle),
and there is no User FK. Rotating SECRET_KEY changes every hash → all buckets reset (acceptable; a
rotation is an operator action). No new operator secret is introduced.
"""
from __future__ import annotations

import hashlib
import hmac
import ipaddress
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Optional

from sqlalchemy import DateTime, Integer, String, bindparam, text
from sqlalchemy.ext.asyncio import AsyncSession

from config import settings

_TZ = DateTime(timezone=True)

_HMAC_CONTEXT = "pult:auth-throttle:v1"

# action -> list of (dimension, config-limit-attr). "pair" is identity+IP; limits come from config.
_ACTION_DIMS: dict[str, list[tuple[str, str]]] = {
    "login": [("pair", "auth_throttle_login_pair_limit"),
              ("identity", "auth_throttle_login_identity_limit"),
              ("ip", "auth_throttle_login_ip_limit")],
    "register": [("identity", "auth_throttle_register_identity_limit"),
                 ("ip", "auth_throttle_register_ip_limit")],
    "email": [("identity", "auth_throttle_email_identity_limit"),
              ("ip", "auth_throttle_email_ip_limit")],
    "reset": [("ip", "auth_throttle_reset_ip_limit")],
    # SECURITY-2C-4A — MFA TOTP-guess throttle. identity = server-verified user_id (never email).
    "mfa_login": [("pair", "auth_throttle_mfa_login_pair_limit"),
                  ("identity", "auth_throttle_mfa_login_identity_limit"),
                  ("ip", "auth_throttle_mfa_login_ip_limit")],
    "mfa_manage": [("pair", "auth_throttle_mfa_manage_pair_limit"),
                   ("identity", "auth_throttle_mfa_manage_identity_limit"),
                   ("ip", "auth_throttle_mfa_manage_ip_limit")],
}

# module-level best-effort throttle for the opportunistic sweep (per worker). Not a security control.
_last_cleanup_monotonic: float = 0.0


@dataclass(frozen=True)
class ThrottleResult:
    blocked: bool
    retry_after_seconds: int


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _as_utc(value) -> Optional[datetime]:
    """RETURNING a timestamp through a raw text() query is typed by the driver, not SQLAlchemy: asyncpg
    hands back a datetime, aiosqlite a string. Normalise both to a tz-aware UTC datetime (None stays None)."""
    if value is None:
        return None
    if isinstance(value, str):
        try:
            value = datetime.fromisoformat(value)
        except ValueError:
            return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value


def normalize_email(email: str) -> str:
    return (email or "").strip().lower()


def normalize_ip(ip: Optional[str]) -> str:
    """Canonical throttle key for an IP. IPv4 → canonical; IPv4-mapped-IPv6 → the IPv4; IPv6 → its /64
    network (an attacker owns a whole /64); anything unparseable → the raw string (fail-closed: it still
    keys a bucket)."""
    if not ip:
        return "unknown"
    raw = ip.strip()
    try:
        addr = ipaddress.ip_address(raw)
    except ValueError:
        return raw
    if isinstance(addr, ipaddress.IPv6Address):
        mapped = addr.ipv4_mapped
        if mapped is not None:
            return str(mapped)
        return str(ipaddress.ip_network(f"{addr}/64", strict=False).network_address) + "/64"
    return str(addr)


def _hash(dimension: str, value: str) -> str:
    msg = f"{_HMAC_CONTEXT}:{dimension}:{value}".encode("utf-8")
    return hmac.new(settings.secret_key.encode("utf-8"), msg, hashlib.sha256).hexdigest()


def _key(dimension: str, identity: str, ip_norm: str) -> str:
    if dimension == "pair":
        return _hash("pair", f"{identity}|{ip_norm}")
    if dimension == "identity":
        return _hash("identity", identity)
    return _hash("ip", ip_norm)


# Portable atomic upsert (PostgreSQL + SQLite both support ON CONFLICT DO UPDATE + RETURNING). Uses CASE
# (not GREATEST/MAX) so blocked_until never decreases and the window resets atomically when expired.
_UPSERT = text("""
INSERT INTO auth_rate_limit_buckets
    (action, dimension, key_hash, window_started_at, attempts, blocked_until, updated_at, expires_at)
VALUES
    (:action, :dim, :kh, :now,
     1,
     CASE WHEN 1 >= :limit THEN :block_until ELSE NULL END,
     :now, :expires)
ON CONFLICT (action, dimension, key_hash) DO UPDATE SET
    attempts = CASE WHEN auth_rate_limit_buckets.window_started_at < :window_cutoff
                    THEN 1 ELSE auth_rate_limit_buckets.attempts + 1 END,
    window_started_at = CASE WHEN auth_rate_limit_buckets.window_started_at < :window_cutoff
                             THEN :now ELSE auth_rate_limit_buckets.window_started_at END,
    blocked_until = CASE
        WHEN (CASE WHEN auth_rate_limit_buckets.window_started_at < :window_cutoff
                   THEN 1 ELSE auth_rate_limit_buckets.attempts + 1 END) >= :limit
             AND (auth_rate_limit_buckets.blocked_until IS NULL
                  OR auth_rate_limit_buckets.blocked_until < :block_until)
        THEN :block_until
        ELSE auth_rate_limit_buckets.blocked_until END,
    updated_at = :now,
    expires_at = :expires
RETURNING attempts, blocked_until
""").bindparams(
    # asyncpg cannot infer the type of a bare bind inside `CASE ... THEN :block_until ELSE NULL END`
    # and defaults it to text, which PostgreSQL then refuses against the timestamptz column. Declaring
    # the types makes PG cast correctly and is a no-op on SQLite.
    bindparam("now", type_=_TZ), bindparam("block_until", type_=_TZ),
    bindparam("expires", type_=_TZ), bindparam("window_cutoff", type_=_TZ),
    bindparam("limit", type_=Integer()),
    bindparam("action", type_=String()), bindparam("dim", type_=String()),
    bindparam("kh", type_=String()),
)

_RELEASE = text("""
UPDATE auth_rate_limit_buckets
SET attempts = CASE WHEN attempts > 0 THEN attempts - 1 ELSE 0 END, updated_at = :now
WHERE action = :action AND dimension = :dim AND key_hash = :kh
""").bindparams(bindparam("now", type_=_TZ))


async def reserve(db: AsyncSession, action: str, *, identity: str = "", ip: Optional[str] = None) -> ThrottleResult:
    """Register one attempt and report whether the caller is currently blocked. Commits immediately (so a
    later request rollback cannot erase the count).

    The dimensions are checked NARROWEST-first (pair → identity → IP) and the walk SHORT-CIRCUITS on the
    first blocked dimension: a broader bucket is only ever incremented by attempts that got past every
    narrower gate. This is what stops a single source from globally locking a victim — once one email+IP
    pair is blocked at its low limit (5), further attempts from that same pair are rejected at the pair
    gate and no longer feed the identity-global counter, so one IP alone can never drive an email's global
    lock (20). Reaching the identity-global limit therefore REQUIRES failures spread across several
    independent pairs/IPs (a genuine distributed attack); likewise the IP-global limit (50) is only
    reached by attempts that cleared both the pair and identity gates — i.e. one IP spraying many emails."""
    now = _now()
    win = settings.auth_throttle_window_seconds
    block = settings.auth_throttle_block_seconds
    window_cutoff = now - timedelta(seconds=win)
    block_until = now + timedelta(seconds=block)
    expires = now + timedelta(seconds=win + block)
    ident = normalize_email(identity)
    ip_norm = normalize_ip(ip)

    blocked = False
    max_retry = 0
    for dimension, limit_attr in _ACTION_DIMS[action]:
        limit = int(getattr(settings, limit_attr))
        row = (await db.execute(_UPSERT, {
            "action": action, "dim": dimension, "kh": _key(dimension, ident, ip_norm),
            "now": now, "limit": limit, "block_until": block_until, "expires": expires,
            "window_cutoff": window_cutoff,
        })).first()
        bu = _as_utc(row.blocked_until) if row is not None else None
        if bu is not None and bu > now:
            blocked = True
            max_retry = int((bu - now).total_seconds()) + 1
            break  # narrower gate blocked → do NOT increment the broader buckets (single-source lock guard)
    await db.commit()
    await _maybe_cleanup(db)
    return ThrottleResult(blocked=blocked, retry_after_seconds=max_retry)


async def _maybe_cleanup(db: AsyncSession) -> None:
    """Opportunistic: at most one bounded sweep per configured interval PER WORKER (a best-effort
    module timer — not a security control). Concurrent sweeps are safe: the batch is bounded and the
    delete is idempotent. A cleanup failure never breaks auth."""
    global _last_cleanup_monotonic
    now_m = time.monotonic()
    if now_m - _last_cleanup_monotonic < settings.auth_throttle_cleanup_interval_seconds:
        return
    _last_cleanup_monotonic = now_m
    try:
        await cleanup(db)
    except Exception:
        pass


async def release(db: AsyncSession, action: str, *, identity: str = "", ip: Optional[str] = None) -> None:
    """On a SUCCESSFUL auth, compensate THIS request's own +1 on every dimension (so only failures net-
    accumulate). Never clears blocked_until, so prior abuse evidence for the IP survives a later success."""
    now = _now()
    ident = normalize_email(identity)
    ip_norm = normalize_ip(ip)
    for dimension, _ in _ACTION_DIMS[action]:
        await db.execute(_RELEASE, {"action": action, "dim": dimension,
                                    "kh": _key(dimension, ident, ip_norm), "now": now})
    await db.commit()


# Bounded delete of fully-expired rows. PG has no DELETE ... LIMIT, and ctid/rowid are dialect-specific,
# so we bound via a subquery on the PRIMARY KEY tuple — portable across PostgreSQL 16 and SQLite (both
# support row-value `IN (SELECT ... LIMIT n)`), with no dialect detection.
_CLEANUP = text("""
DELETE FROM auth_rate_limit_buckets
WHERE (action, dimension, key_hash) IN (
  SELECT action, dimension, key_hash FROM auth_rate_limit_buckets
  WHERE expires_at < :now AND (blocked_until IS NULL OR blocked_until < :now)
  LIMIT :batch)
""").bindparams(bindparam("now", type_=_TZ), bindparam("batch", type_=Integer()))


async def cleanup(db: AsyncSession) -> int:
    """Delete a bounded batch of buckets whose window AND block have both elapsed. Never removes an
    active block. Returns the number deleted (a numeric-only signal; no key_hash is logged)."""
    now = _now()
    batch = int(settings.auth_throttle_cleanup_batch)
    res = await db.execute(_CLEANUP, {"now": now, "batch": batch})
    await db.commit()
    return res.rowcount or 0
