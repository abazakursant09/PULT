"""Dormant independent-custodian protocol; no purge, service or admission wiring.

SQLite is the custodian's trusted local persistence, NOT proof of OS isolation or
anti-rollback. Provision it outside application backups and the journal writer's
authority. Only the uninterrupted, single-use, acknowledged path can commit a head.
"""
from contextlib import contextmanager
from dataclasses import dataclass, field
import hashlib
import os
from pathlib import Path
import re
import sqlite3

from ops.backup.erasure_journal import (
    MAX_EVENTS, Conflict, Event, Head, Journal, JournalError, Outcome, Prepared,
    _uuid, decode, genesis,
)

_APP_ID = 0x50554C54
_SCHEMA = 1
_META = """CREATE TABLE anchor (
    id INTEGER PRIMARY KEY CHECK(id=1), scope TEXT NOT NULL, generation TEXT NOT NULL,
    location TEXT NOT NULL, boot_seq INTEGER NOT NULL, boot_hash TEXT NOT NULL,
    seq INTEGER NOT NULL, hash TEXT NOT NULL, revision INTEGER NOT NULL, pending TEXT)"""
_ATTEMPTS = """CREATE TABLE attempts (
    id TEXT PRIMARY KEY, base_seq INTEGER NOT NULL, base_hash TEXT NOT NULL,
    target_seq INTEGER NOT NULL UNIQUE, target_hash TEXT NOT NULL, body_hash TEXT NOT NULL,
    etag TEXT NOT NULL, phase TEXT NOT NULL CHECK(phase IN ('PREPARED','DISPATCHED','COMMITTED')))"""


class AnchorError(JournalError):
    """Fixed messages only; no UUID, payload, SQL, credentials or path in errors."""


class AnchorCommitUncertain(AnchorError):
    """Confirmation COMMIT was attempted; the receipt may already be COMMITTED."""


@dataclass(frozen=True)
class AnchorState:
    head: Head
    revision: int
    pending: str | None = field(repr=False)


def _body_hash(body):
    return hashlib.sha256(body).hexdigest()


class SQLiteAnchor:
    """Trusted custodian-only file. Missing/corrupt state never initializes itself."""
    def __init__(self, path, *, scope, generation, location):
        genesis(scope)
        _uuid(generation)
        if not isinstance(location, str) or not re.fullmatch(r"[A-Za-z0-9:/._-]{1,512}", location):
            raise AnchorError("invalid journal location identity")
        self._path = Path(path)
        if not self._path.is_absolute() or self._path.drive.startswith("\\\\"):
            raise AnchorError("custodian requires an absolute local path")
        self.scope, self.generation, self.location = scope, generation, location

    def __repr__(self):
        return "<SQLiteAnchor private custodian state>"

    def _check_path(self, *, creating=False):
        for part in (self._path, *self._path.parents):
            if part.is_symlink() or getattr(part, "is_junction", lambda: False)():
                raise AnchorError("linked custodian paths are forbidden")
        if not self._path.parent.is_dir():
            raise AnchorError("custodian directory must already exist")
        if not creating:
            if not self._path.is_file():
                raise AnchorError("custodian state missing; no automatic bootstrap")
            info = self._path.stat()
            if info.st_nlink != 1 or info.st_size > 2 * 1024 * 1024:
                raise AnchorError("invalid custodian file")

    @contextmanager
    def _connection(self, *, creating=False):
        connection = None
        try:
            self._check_path(creating=creating)
            connection = sqlite3.connect(self._path.as_uri() + "?mode=rw", uri=True,
                                         isolation_level=None, timeout=0)
            connection.row_factory = sqlite3.Row
            connection.execute("PRAGMA trusted_schema=OFF")
            connection.execute("PRAGMA synchronous=EXTRA")
            if connection.execute("PRAGMA synchronous").fetchone()[0] != 3:
                raise AnchorError("required custodian synchronization unavailable")
            if connection.execute("PRAGMA journal_mode").fetchone()[0] != "delete":
                raise AnchorError("unsupported custodian journal mode")
            yield connection
        except (sqlite3.Error, OSError, UnicodeError):
            raise AnchorError("custodian storage unavailable; no retry") from None
        finally:
            if connection is not None:
                try:
                    try:
                        if connection.in_transaction:
                            connection.rollback()
                    finally:
                        connection.close()
                except (sqlite3.Error, OSError, UnicodeError):
                    raise AnchorError("custodian cleanup unavailable; no retry") from None

    def create(self, trusted_bootstrap: Head):
        """Explicit operator bootstrap only. Never derive the pin from remote history."""
        if not isinstance(trusted_bootstrap, Head):
            raise AnchorError("explicit trusted bootstrap head required")
        if trusted_bootstrap.sequence == 0 and trusted_bootstrap != genesis(self.scope):
            raise AnchorError("bootstrap genesis does not match scope")
        try:
            self._check_path(creating=True)
            descriptor = os.open(self._path, os.O_CREAT | os.O_EXCL | os.O_WRONLY
                                 | getattr(os, "O_NOFOLLOW", 0), 0o600)
            os.close(descriptor)
        except (OSError, UnicodeError):
            raise AnchorError("custodian bootstrap refused; no overwrite") from None
        # Failed/partial bootstrap is intentionally left unusable, never auto-repaired.
        with self._connection(creating=True) as connection:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute(_META)
            connection.execute(_ATTEMPTS)
            connection.execute(f"PRAGMA application_id={_APP_ID}")
            connection.execute(f"PRAGMA user_version={_SCHEMA}")
            connection.execute("INSERT INTO anchor VALUES(1,?,?,?,?,?,?,?,?,NULL)",
                               (self.scope, self.generation, self.location,
                                trusted_bootstrap.sequence, trusted_bootstrap.digest,
                                trusted_bootstrap.sequence, trusted_bootstrap.digest, 0))
            connection.execute("COMMIT")
        state = self.load()
        if state != AnchorState(trusted_bootstrap, 0, None):
            raise AnchorError("bootstrap readback mismatch")
        return state

    def _load(self, connection):
        if (connection.execute("PRAGMA application_id").fetchone()[0] != _APP_ID
                or connection.execute("PRAGMA user_version").fetchone()[0] != _SCHEMA):
            raise AnchorError("unsupported custodian schema")
        objects = connection.execute("SELECT type,name FROM sqlite_schema WHERE type IN ('table','view','trigger')").fetchall()
        if {(row[0], row[1]) for row in objects} != {("table", "anchor"), ("table", "attempts")}:
            raise AnchorError("unsupported custodian objects")
        rows = connection.execute("SELECT * FROM anchor").fetchall()
        if len(rows) != 1:
            raise AnchorError("invalid custodian metadata")
        meta = rows[0]
        if (meta["id"], meta["scope"], meta["generation"], meta["location"]) != (
                1, self.scope, self.generation, self.location):
            raise AnchorError("custodian scope generation or location mismatch")
        head = Head(meta["boot_seq"], meta["boot_hash"])
        revision, pending = 0, None
        attempts = connection.execute("SELECT * FROM attempts ORDER BY target_seq LIMIT 130").fetchall()
        if len(attempts) > MAX_EVENTS:
            raise AnchorError("custodian history exceeds bound")
        for row in attempts:
            _uuid(row["id"])
            base, target = Head(row["base_seq"], row["base_hash"]), Head(row["target_seq"], row["target_hash"])
            if (pending is not None or base != head or target.sequence != head.sequence + 1
                    or type(row["body_hash"]) is not str or type(row["etag"]) is not str
                    or not re.fullmatch(r"[a-f0-9]{64}", row["body_hash"])
                    or not re.fullmatch(r'"[\x21\x23-\x7e]{1,126}"', row["etag"])):
                raise AnchorError("invalid custodian attempt chain")
            phase = row["phase"]
            if phase == "COMMITTED":
                revision += 3
                head = target
            elif phase in ("PREPARED", "DISPATCHED"):
                revision += 1 if phase == "PREPARED" else 2
                pending = row["id"]
            else:
                raise AnchorError("invalid custodian attempt phase")
        if (meta["seq"], meta["hash"], meta["revision"], meta["pending"]) != (
                head.sequence, head.digest, revision, pending):
            raise AnchorError("custodian metadata and receipts disagree")
        return AnchorState(head, revision, pending)

    def load(self):
        with self._connection() as connection:
            connection.execute("BEGIN")
            return self._load(connection)

    def inspect_attempt(self, attempt_id):
        _uuid(attempt_id)
        with self._connection() as connection:
            connection.execute("BEGIN")
            state = self._load(connection)
            row = connection.execute("SELECT phase FROM attempts WHERE id=?", (attempt_id,)).fetchone()
            return state, None if row is None else row["phase"]

    def prepare(self, expected: AnchorState, attempt_id: str, prepared: Prepared):
        _uuid(attempt_id)
        candidate = decode(prepared.body, self.scope)
        candidate.require_floor(expected.head)
        if candidate.head != prepared.head or prepared.base != expected.head or candidate.head.sequence != expected.head.sequence + 1:
            raise AnchorError("candidate does not extend trusted head exactly once")
        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            current = self._load(connection)
            if current != expected or current.pending is not None:
                raise AnchorError("custodian changed or has a pending attempt")
            connection.execute("INSERT INTO attempts VALUES(?,?,?,?,?,?,?,'PREPARED')",
                               (attempt_id, prepared.base.sequence, prepared.base.digest,
                                prepared.head.sequence, prepared.head.digest, _body_hash(prepared.body), prepared.etag))
            connection.execute("UPDATE anchor SET revision=revision+1,pending=? WHERE id=1", (attempt_id,))
            connection.execute("COMMIT")
        state = self.load()
        if state != AnchorState(expected.head, expected.revision + 1, attempt_id):
            raise AnchorError("prepared receipt readback mismatch")
        return state

    def transition(self, expected: AnchorState, prepared: Prepared, *, complete: bool):
        committing = False
        wanted = AnchorState(prepared.head if complete else expected.head, expected.revision + 1,
                             None if complete else expected.pending)
        try:
            with self._connection() as connection:
                connection.execute("BEGIN IMMEDIATE")
                if self._load(connection) != expected or expected.pending is None:
                    raise AnchorError("stale or missing custodian receipt")
                row = connection.execute("SELECT * FROM attempts WHERE id=?", (expected.pending,)).fetchone()
                phase = "DISPATCHED" if complete else "PREPARED"
                if (row["phase"] != phase or row["body_hash"] != _body_hash(prepared.body)
                        or (row["target_seq"], row["target_hash"], row["etag"]) != (
                            prepared.head.sequence, prepared.head.digest, prepared.etag)):
                    raise AnchorError("attempt identity or phase mismatch")
                connection.execute("UPDATE attempts SET phase=? WHERE id=?",
                                   ("COMMITTED" if complete else "DISPATCHED", expected.pending))
                if complete:
                    connection.execute("UPDATE anchor SET seq=?,hash=?,revision=revision+1,pending=NULL WHERE id=1",
                                       (prepared.head.sequence, prepared.head.digest))
                else:
                    connection.execute("UPDATE anchor SET revision=revision+1 WHERE id=1")
                committing = True
                connection.execute("COMMIT")
        except AnchorError:
            if complete and committing:
                raise AnchorCommitUncertain("confirmation response uncertain; inspect durable receipt, never resend") from None
            raise
        # COMMIT is the linearization point. A later observation may legitimately
        # see another reservation; do not turn that into a false failed commit.
        return wanted


@dataclass(frozen=True)
class _Ticket:
    prepared: Prepared = field(repr=False)
    state: AnchorState


class Custodian:
    """Trusted custodian process owns this object/file and its reader-only key.

    Tickets are in-process single-use capabilities, not RPC credentials. No ticket
    is resurrected from SQLite after a process restart or serialized by this code.
    """
    def __init__(self, anchor: SQLiteAnchor, reader):
        self._anchor, self._reader = anchor, reader
        self._active = {}
        self._check_location()

    def _check_location(self):
        if (hasattr(self._reader, "write") or getattr(self._reader, "_writer", None) is not None):
            raise AnchorError("custodian requires a reader-only capability")
        if self._reader.location != self._anchor.location or self._reader.scope != self._anchor.scope:
            raise AnchorError("custodian reader location mismatch")

    @property
    def location(self):
        return self._anchor.location

    @property
    def scope(self):
        return self._anchor.scope

    def current(self):
        self._check_location()
        state = self._anchor.load()
        if state.pending is not None:
            raise AnchorError("pending attempt; anchor is only a floor")
        snapshot, _ = Journal(self._reader, self._anchor.scope).observe(state.head)
        if snapshot.head != state.head or self._anchor.load() != state:
            raise AnchorError("journal or custodian advanced; no current-state proof")
        return state  # observation only, never a purge/admission permit or lease

    def prepare(self, event: Event, *, attempt_id: str):
        _uuid(attempt_id)
        self._check_location()
        state = self._anchor.load()
        if state.pending is not None:
            raise AnchorError("pending attempt blocks new preparation")
        prepared = Journal(self._reader, self._anchor.scope).prepare(event, state.head)
        if isinstance(prepared, Outcome):
            if prepared.observed != state.head or self._anchor.load() != state:
                raise AnchorError("unanchored history cannot be adopted")
            return Outcome("NO_NEW_ATTEMPT", state.head)
        reserved = self._anchor.prepare(state, attempt_id, prepared)
        ticket = _Ticket(prepared, reserved)
        self._active[id(ticket)] = (ticket, False)
        return ticket

    def claim(self, ticket):
        if self._active.pop(id(ticket), None) != (ticket, False):
            raise AnchorError("dispatch ticket is stale consumed or foreign")
        self._check_location()
        claimed = self._anchor.transition(ticket.state, ticket.prepared, complete=False)
        claimed_ticket = _Ticket(ticket.prepared, claimed)
        self._active[id(claimed_ticket)] = (claimed_ticket, True)
        return claimed_ticket

    def abandon(self, ticket):
        # Revoke the entire in-memory lineage, including a claim whose return was
        # interrupted before the runner could bind the derived ticket locally.
        for key, (active, _) in list(self._active.items()):
            if active.prepared is ticket.prepared and active.state.pending == ticket.state.pending:
                self._active.pop(key, None)

    def confirm(self, ticket):
        if self._active.pop(id(ticket), None) != (ticket, True):
            raise AnchorError("confirmation ticket is stale consumed or foreign")
        self._check_location()
        snapshot, _ = Journal(self._reader, self._anchor.scope).observe(ticket.prepared.base)
        if snapshot.head != ticket.prepared.head or snapshot.encode() != ticket.prepared.body:
            raise AnchorError("independent reader did not verify exact candidate")
        return self._anchor.transition(ticket.state, ticket.prepared, complete=True)

    def reconcile(self, attempt_id=None):
        """Read-only diagnosis: never claim, confirm, reset pending, resend or adopt."""
        self._check_location()
        state, phase = ((self._anchor.load(), None) if attempt_id is None
                        else self._anchor.inspect_attempt(attempt_id))
        snapshot, _ = Journal(self._reader, self._anchor.scope).observe(state.head)
        if self._anchor.load() != state:
            raise AnchorError("custodian changed during reconciliation")
        if phase == "COMMITTED":
            status = "OBSERVED_COMMITTED_RECEIPT"
        elif state.pending:
            status = "PENDING_REQUIRES_REVIEW"
        else:
            status = "OBSERVED_ONLY" if attempt_id is None else "NO_SUCH_RECEIPT"
        return Outcome(status, snapshot.head)


class AnchoredJournal:
    """Synchronous reference runner. Production needs a separate authenticated custodian boundary."""
    def __init__(self, custodian: Custodian, writer):
        self._custodian, self._writer = custodian, writer

    def append(self, event: Event, *, attempt_id: str):
        if (self._writer.location != self._custodian.location
                or self._writer.scope != self._custodian.scope):
            raise AnchorError("writer location mismatch")
        prepared = self._custodian.prepare(event, attempt_id=attempt_id)
        if isinstance(prepared, Outcome):
            return prepared
        claimed = None
        try:
            claimed = self._custodian.claim(prepared)  # durable single-use claim BEFORE any PUT
            try:
                if (self._writer.location != self._custodian.location
                        or self._writer.scope != self._custodian.scope):
                    raise AnchorError("writer changed after dispatch claim")
                self._writer.write(claimed.prepared.body, etag=claimed.prepared.etag)
            except Conflict:
                return Outcome("CONFLICT")
            except Exception:
                return Outcome("UNKNOWN")
            try:
                state = self._custodian.confirm(claimed)
            except AnchorCommitUncertain:
                return Outcome("ANCHOR_RESPONSE_UNKNOWN")
            except Exception:
                return Outcome("UNKNOWN")
            return Outcome("ANCHORED_STORAGE_ONLY", state.head)
        finally:
            # BaseException cancellation propagates, but cannot leave a live
            # capability that could later confirm an unacknowledged dispatch.
            self._custodian.abandon(prepared)
            if claimed is not None:
                self._custodian.abandon(claimed)
