"""Unwired committed-DRAFT observation for a trusted authenticated operator.

The host supplies a dedicated native asyncpg/NullPool engine and identity pins,
never a request-selected URL or a caller's session/transaction. Each call owns
one new physical connection and one read-only REPEATABLE READ snapshot. Database
name/OID/login checks are routing checks, not clone or anti-rollback evidence.
A fresh observation is neither a mutation permit nor a live recovery/purge fence.
No lifecycle, enrollment, journal, restore or purge operation is reachable here.
"""
import asyncio
import re
from datetime import datetime, timezone

from sqlalchemy import text
from sqlalchemy.dialects.postgresql.asyncpg import PGDialect_asyncpg
from sqlalchemy.ext.asyncio import AsyncEngine
from sqlalchemy.pool import NullPool

from ops.backup.erasure_operator import DraftObservation, ReviewError, _epoch, _uuid
from services.account_closure_policy import evaluate_closure


# All identifiers and settings are static. Catalog lookups are parameterized;
# qualified application tables cannot be redirected through search_path.
_IDENTITY = text("""
SELECT pg_catalog.current_database() AS database_name, d.oid AS database_oid,
       session_user AS login_role, current_user AS effective_role,
       r.rolsuper, r.rolbypassrls, r.rolcreatedb, r.rolcreaterole,
       r.rolreplication, r.rolinherit,
       pg_catalog.pg_has_role(r.oid, d.datdba, 'MEMBER') AS database_owner
FROM pg_catalog.pg_database AS d
JOIN pg_catalog.pg_roles AS r ON r.rolname = session_user
WHERE d.datname = pg_catalog.current_database()
""")

_TARGET_PRIVILEGES = text("""
SELECT c.relname, CAST(c.relkind AS text) AS relkind,
       pg_catalog.pg_has_role(current_user, c.relowner, 'MEMBER') AS table_owner,
       pg_catalog.has_table_privilege(current_user, c.oid,
           'INSERT,UPDATE,DELETE,TRUNCATE,REFERENCES,TRIGGER') AS table_write,
       pg_catalog.has_any_column_privilege(current_user, c.oid,
           'INSERT,UPDATE,REFERENCES') AS column_write
FROM pg_catalog.pg_class AS c
JOIN pg_catalog.pg_namespace AS n ON n.oid = c.relnamespace
WHERE n.nspname = 'public' AND c.relname IN ('users', 'erasure_preparations')
""")

_DRAFT = text("""
SELECT u.id AS account_id, u.deleted_at, u.closure_policy_version AS user_policy,
       u.closure_started_at AS user_epoch, u.closure_restore_requested_at,
       u.closure_purge_started_at, p.id AS draft_id, p.user_id,
       p.closure_policy_version AS draft_policy,
       p.closure_started_at AS draft_epoch, p.prepared_at, p.state,
       pg_catalog.transaction_timestamp() AS reviewed_at
FROM public.users AS u
JOIN public.erasure_preparations AS p ON p.user_id = u.id
WHERE u.id = :account_id AND p.id = :draft_id
""")


def _timestamp(value):
    """Only real aware PG timestamptz values, rendered in the shared wire form."""
    try:
        if type(value) is not datetime or value.utcoffset() is None:
            raise ValueError
        value = value.astimezone(timezone.utc)
        rendered = value.strftime('%Y-%m-%dT%H:%M:%S.%fZ')
        _epoch(rendered)
        return value, rendered
    except (ValueError, TypeError, OverflowError, ReviewError):
        raise ReviewError('committed_draft_not_reviewable') from None


class CommittedDraftReviewer:
    """Trusted backend construction only; request arguments carry no authority.

    NullPool must remain on the supplied engine: no ORM identity map, borrowed
    connection, application pool or previously retained snapshot is accepted.
    The host owns engine disposal; this object owns/cleans up each checkout.
    Target-table privileges are audited, not all grants throughout the cluster.
    """

    def __init__(self, engine, *, database_name, database_oid, reader_role):
        if (type(database_name) is not str
                or re.fullmatch(r'[a-z_][a-z0-9_]{0,62}', database_name) is None
                or type(database_oid) is not int or not 16384 <= database_oid < 2**32
                or type(reader_role) is not str
                or re.fullmatch(r'[a-z_][a-z0-9_]{0,62}', reader_role) is None):
            raise ReviewError('review_database_binding_required')
        self._engine = engine
        self._database_name = database_name
        self._database_oid = database_oid
        self._reader_role = reader_role
        self._validate_engine()

    def __repr__(self):
        return '<CommittedDraftReviewer private read-only binding>'

    def _validate_engine(self):
        engine = self._engine
        if (type(engine) is not AsyncEngine
                or type(engine.dialect) is not PGDialect_asyncpg
                or type(engine.pool) is not NullPool
                or engine.url.drivername != 'postgresql+asyncpg'
                or engine.url.database != self._database_name
                or engine.url.username != self._reader_role
                or engine.echo not in (None, False) or engine.pool.echo not in (None, False)
                or engine.sync_engine.hide_parameters is not True):
            raise ReviewError('trusted_review_engine_required')

    async def __call__(self, account_id, draft_id, *, nonce):
        # Validate before checkout (including aliases rejected by the wire type).
        for value in (account_id, draft_id, nonce):
            _uuid(value)
        if len({account_id, draft_id, nonce}) != 3:
            raise ReviewError('review_identity_alias_refused')
        self._validate_engine()
        try:
            # The outer deadline also bounds physical checkout/connect and close,
            # not only SQL execution. Timeout/cancellation never emits an observation.
            async with asyncio.timeout(5), self._engine.connect() as connection:
                async with connection.begin():
                    await connection.execute(text(
                        'SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY'))
                    await connection.execute(text("SET LOCAL statement_timeout = '2000ms'"))
                    await connection.execute(text("SET LOCAL lock_timeout = '500ms'"))
                    await connection.execute(text(
                        "SET LOCAL idle_in_transaction_session_timeout = '3000ms'"))
                    identity = (await connection.execute(_IDENTITY)).mappings().one()
                    if ((identity['database_name'], identity['database_oid'],
                         identity['login_role'], identity['effective_role']) !=
                            (self._database_name, self._database_oid,
                             self._reader_role, self._reader_role)):
                        raise ReviewError('review_database_identity_refused')
                    if any(identity[key] is not False for key in (
                            'rolsuper', 'rolbypassrls', 'rolcreatedb', 'rolcreaterole',
                            'rolreplication', 'rolinherit', 'database_owner')):
                        raise ReviewError('review_reader_privileges_refused')
                    targets = (await connection.execute(_TARGET_PRIVILEGES)).mappings().all()
                    if ({row['relname'] for row in targets} != {'users', 'erasure_preparations'}
                            or len(targets) != 2 or any(
                                row['relkind'] != 'r' or any(row[key] is not False for key in
                                    ('table_owner', 'table_write', 'column_write'))
                                for row in targets)):
                        raise ReviewError('review_reader_privileges_refused')
                    row = (await connection.execute(_DRAFT, {
                        'account_id': account_id, 'draft_id': draft_id,
                    })).mappings().one_or_none()
                    if (row is None or row['account_id'] != account_id
                            or row['draft_id'] != draft_id or row['user_id'] != account_id
                            or row['state'] != 'DRAFT' or row['deleted_at'] is None
                            or row['closure_restore_requested_at'] is not None
                            or row['closure_purge_started_at'] is not None
                            or row['user_policy'] != row['draft_policy']):
                        raise ReviewError('committed_draft_not_reviewable')
                    epoch, closure_epoch = _timestamp(row['user_epoch'])
                    draft_epoch, _ = _timestamp(row['draft_epoch'])
                    prepared_at, _ = _timestamp(row['prepared_at'])
                    reviewed_at, rendered_review = _timestamp(row['reviewed_at'])
                    if draft_epoch != epoch or not epoch <= prepared_at <= reviewed_at:
                        raise ReviewError('committed_draft_not_reviewable')
                    # A DRAFT must have been sensible when prepared as well as
                    # currently due. Reuse the policy, including its exact boundary.
                    for clock in (prepared_at, reviewed_at):
                        policy = evaluate_closure(policy_version=row['user_policy'],
                            closed_at=epoch, now=clock, purge_started=False)
                        if policy['erasure_review_due'] is not True:
                            raise ReviewError('committed_draft_not_reviewable')
                    observation = DraftObservation(nonce=nonce, account_id=account_id,
                        draft_id=draft_id, closure_epoch=closure_epoch,
                        policy=row['user_policy'], reviewed_at=rendered_review)
            # Never expose a success if commit, rollback or physical close failed.
            return observation
        except ReviewError as error:
            # Only our own fixed refusal codes may cross the backend boundary;
            # a driver/cleanup hook raising this public exception is not trusted.
            reasons = ('review_database_identity_refused',
                       'review_reader_privileges_refused', 'committed_draft_not_reviewable')
            reason = (error.args[0] if type(error) is ReviewError and len(error.args) == 1
                      and type(error.args[0]) is str and error.args[0] in reasons
                      else 'review_storage_failed')
            raise ReviewError(reason) from None
        except Exception:
            raise ReviewError('review_storage_failed') from None
