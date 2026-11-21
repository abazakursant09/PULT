"""Dormant operator-owned identity registry; never dispatch/purge/restore authority.

Keep this file outside application backup/rollback and writer access. This module
does not establish that OS boundary, authenticate a caller, prove a DRAFT commit
or eligibility, or protect joint rollback of the registry itself. Only a future
reviewed operator boundary may call record_reviewed; there is no CLI or app hook.
"""
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime
import os
import sqlite3
from uuid import uuid4

from ops.backup.erasure_anchor import AnchorError, SQLiteAnchor
from ops.backup.erasure_journal import JournalError, _uuid

APP_ID = 0x50454931
SCHEMA = 1
MAX_BINDINGS = 64  # Reference bound, NOT a production capacity/retention decision.
POLICY = "normal-close-30d-v1"
META_SQL = """CREATE TABLE identity_meta (
    id INTEGER PRIMARY KEY CHECK(id=1), scope TEXT NOT NULL,
    generation TEXT NOT NULL, registry_id TEXT NOT NULL, revision INTEGER NOT NULL)"""
SUBJECT_SQL = """CREATE TABLE identity_subjects (
    account_id TEXT PRIMARY KEY, subject_id TEXT NOT NULL UNIQUE)"""
REQUEST_SQL = """CREATE TABLE identity_requests (
    purge_id TEXT PRIMARY KEY, draft_id TEXT NOT NULL UNIQUE,
    account_id TEXT NOT NULL UNIQUE REFERENCES identity_subjects(account_id),
    closure_epoch TEXT NOT NULL, policy TEXT NOT NULL)"""


class IdentityError(Exception):
    """Fixed secret-free reason; no automatic initialization, repair or retry."""


class IdentityCommitUnknown(IdentityError):
    """COMMIT/cleanup was ambiguous. Inspect only; never infer dispatch admission."""


@dataclass(frozen=True)
class Binding:
    scope: str = field(repr=False)
    generation: str = field(repr=False)
    registry_id: str = field(repr=False)
    account_id: str = field(repr=False)
    draft_id: str = field(repr=False)
    closure_epoch: str = field(repr=False)
    policy: str
    subject_id: str = field(repr=False)
    purge_id: str = field(repr=False)


@dataclass(frozen=True)
class Receipt:
    binding: Binding = field(repr=False)
    status: str
    journal_dispatch_admitted: bool = field(default=False, init=False)
    purge_or_restore_admitted: bool = field(default=False, init=False)


def _id(value):
    if type(value) is not str:
        raise IdentityError("canonical opaque identifier required")
    try:
        return _uuid(value)
    except JournalError:
        raise IdentityError("canonical opaque identifier required") from None


def _epoch(value):
    if type(value) is not str or len(value) != 27:
        raise IdentityError("canonical UTC closure epoch required")
    try:
        parsed = datetime.strptime(value, "%Y-%m-%dT%H:%M:%S.%fZ")
        if parsed.strftime("%Y-%m-%dT%H:%M:%S.%fZ") != value:
            raise ValueError()
    except ValueError:
        raise IdentityError("canonical UTC closure epoch required") from None
    return value


class IdentityRegistry:
    """Separate explicit registry, not an extension/migration of custodian state.

    Reuses the reviewed SQLiteAnchor connection/path safeguards only, as the v2
    anchor already does. Never calls its bootstrap, schema or journal mutations.
    registry_id/scope/generation are independent operator pins, NOT app input.
    """
    def __init__(self, path, *, scope, generation, registry_id):
        self.registry_id = _id(registry_id)
        try:
            self._storage = SQLiteAnchor(path, scope=scope, generation=generation,
                                         location="identity-registry:" + registry_id)
        except (JournalError, TypeError, ValueError, OSError):
            raise IdentityError("invalid independent registry binding") from None
        self.scope, self.generation = scope, generation

    def __repr__(self):
        return "<IdentityRegistry private independent state>"

    @contextmanager
    def _connection(self, *, creating=False):
        try:
            with self._storage._connection(creating=creating) as connection:
                connection.execute("PRAGMA foreign_keys=ON")
                if connection.execute("PRAGMA foreign_keys").fetchone()[0] != 1:
                    raise IdentityError("registry foreign-key enforcement unavailable")
                yield connection
        except (AnchorError, sqlite3.Error, OSError):
            raise IdentityError("registry storage unavailable; no retry") from None

    def create(self):
        """Explicit independent operator bootstrap; never called on missing load."""
        try:
            self._storage._check_path(creating=True)
            fd = os.open(self._storage._path, os.O_CREAT | os.O_EXCL | os.O_WRONLY
                         | getattr(os, "O_NOFOLLOW", 0), 0o600)
            os.close(fd)
        except (AnchorError, OSError):
            raise IdentityError("registry bootstrap refused; no overwrite") from None
        # Any partial/unknown bootstrap stays unusable; do not delete or repair it.
        with self._connection(creating=True) as connection:
            connection.execute("BEGIN IMMEDIATE")
            for sql in (META_SQL, SUBJECT_SQL, REQUEST_SQL):
                connection.execute(sql)
            connection.execute(f"PRAGMA application_id={APP_ID}")
            connection.execute(f"PRAGMA user_version={SCHEMA}")
            connection.execute("INSERT INTO identity_meta VALUES(1,?,?,?,0)",
                               (self.scope, self.generation, self.registry_id))
            connection.execute("COMMIT")
        return self.inspect()

    def _load(self, connection):
        if (connection.execute("PRAGMA application_id").fetchone()[0] != APP_ID
                or connection.execute("PRAGMA user_version").fetchone()[0] != SCHEMA):
            raise IdentityError("unsupported registry schema")
        objects = connection.execute(
            "SELECT type,name,sql FROM sqlite_schema WHERE name NOT GLOB 'sqlite_*'").fetchall()
        if {(r[0], r[1]): r[2] for r in objects} != {
            ("table", "identity_meta"): META_SQL,
            ("table", "identity_subjects"): SUBJECT_SQL,
            ("table", "identity_requests"): REQUEST_SQL,
        }:
            raise IdentityError("unsupported registry objects")
        meta = connection.execute("SELECT * FROM identity_meta LIMIT 2").fetchall()
        if len(meta) != 1 or tuple(meta[0])[:4] != (
                1, self.scope, self.generation, self.registry_id):
            raise IdentityError("registry binding mismatch")
        subjects = connection.execute("SELECT * FROM identity_subjects LIMIT 65").fetchall()
        requests = connection.execute("SELECT * FROM identity_requests LIMIT 65").fetchall()
        if (len(subjects) != len(requests) or len(requests) > MAX_BINDINGS
                or type(meta[0]["revision"]) is not int or meta[0]["revision"] != len(requests)):
            raise IdentityError("registry evidence or capacity mismatch")
        mapping = {}
        for row in subjects:
            mapping[_id(row["account_id"])] = _id(row["subject_id"])
        bindings = {}
        for row in requests:
            account = _id(row["account_id"])
            if account not in mapping or account in bindings or row["policy"] != POLICY:
                raise IdentityError("registry evidence mismatch")
            bindings[account] = Binding(self.scope, self.generation, self.registry_id,
                                        account, _id(row["draft_id"]), _epoch(row["closure_epoch"]),
                                        POLICY, mapping[account], _id(row["purge_id"]))
        if set(mapping) != set(bindings):
            raise IdentityError("registry orphan evidence")
        opaque = [value for b in bindings.values() for value in
                  (b.account_id, b.draft_id, b.subject_id, b.purge_id)]
        if len(set(opaque)) != len(opaque):
            raise IdentityError("registry identity alias")
        return bindings

    def inspect(self, account_id=None):
        """Read-only diagnostic observation; never a receipt or an admission ticket."""
        if account_id is not None:
            _id(account_id)
        with self._connection() as connection:
            connection.execute("BEGIN")
            bindings = self._load(connection)
            return len(bindings) if account_id is None else bindings.get(account_id)

    def record_reviewed(self, *, account_id, draft_id, closure_epoch, policy):
        """Trusted operator call only; eligibility/committed-DRAFT proof is external.

        One unresolved binding per account. No supersession, release, TTL, reset,
        app hook, process launcher or external IO exists in this slice.
        """
        _id(account_id)
        _id(draft_id)
        _epoch(closure_epoch)
        if account_id == draft_id:
            raise IdentityError("registry identity alias")
        if type(policy) is not str or policy != POLICY:
            raise IdentityError("reviewed closure policy required")
        committing = False
        try:
            with self._connection() as connection:
                connection.execute("BEGIN IMMEDIATE")
                bindings = self._load(connection)
                old = bindings.get(account_id)
                if old is not None:
                    if (old.draft_id, old.closure_epoch, old.policy) != (draft_id, closure_epoch, policy):
                        raise IdentityError("conflicting enrollment requires independent review")
                    return Receipt(old, "OBSERVED_BINDING_ONLY")
                if any(b.draft_id == draft_id for b in bindings.values()):
                    raise IdentityError("conflicting enrollment requires independent review")
                used = {value for b in bindings.values() for value in
                        (b.account_id, b.draft_id, b.subject_id, b.purge_id)}
                if account_id in used or draft_id in used:
                    raise IdentityError("registry identity alias")
                if len(bindings) >= MAX_BINDINGS:
                    raise IdentityError("registry capacity reached; retain evidence")
                subject_id, purge_id = _id(str(uuid4())), _id(str(uuid4()))
                reserved = used | {account_id, draft_id}
                if subject_id == purge_id or subject_id in reserved or purge_id in reserved:
                    raise IdentityError("independent identity allocation failed; no retry")
                binding = Binding(self.scope, self.generation, self.registry_id,
                                  account_id, draft_id, closure_epoch, policy, subject_id, purge_id)
                connection.execute("INSERT INTO identity_subjects VALUES(?,?)", (account_id, subject_id))
                connection.execute("INSERT INTO identity_requests VALUES(?,?,?,?,?)",
                                   (purge_id, draft_id, account_id, closure_epoch, policy))
                connection.execute("UPDATE identity_meta SET revision=revision+1 WHERE id=1")
                committing = True
                connection.execute("COMMIT")
        except IdentityError:
            if committing:
                raise IdentityCommitUnknown("registry commit response unknown; inspect only") from None
            raise
        return Receipt(binding, "RECORDED_BINDING_ONLY")
