"""Synthetic custodian state machine; no service, cloud credentials or purge."""
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from copy import copy
import hashlib
from pathlib import Path
import sqlite3
import socket
import sys
from threading import Barrier

import httpx
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from ops.backup.erasure_anchor import AnchorError, AnchoredJournal, Custodian, SQLiteAnchor
from ops.backup.erasure_journal import Conflict, Event, Head, Journal, JournalError, Prepared, Stored, WriteUnknown, empty, genesis
from ops.backup.erasure_journal_s3 import S3JournalReader, S3JournalStore

SCOPE = "synthetic"
GEN = "1e534b17-05e7-4529-8ffa-c32162c67ad5"
PURGE = "73e1c70a-3840-40ec-b5a2-6380343d9cbf"
SUBJECT = "770d659f-c20f-46dc-b3bc-3f6000204bc6"
ATTEMPT = "a9e1135b-aab0-4b4e-a10d-8f96cc696d96"
NEXT = "82047569-5c91-4b10-87c4-3a8b121e1891"
INTENT, COMPLETE = Event(PURGE, SUBJECT, "intent"), Event(PURGE, SUBJECT, "complete")
LOCATION = "synthetic://test/journal"


class Reader:
    location = LOCATION
    scope = SCOPE
    def __init__(self, remote):
        self.remote = remote
        self.reads = 0
        self.barrier = None
        self.after_read = None

    def read(self):
        self.reads += 1
        if self.remote.get("read_fail"):
            raise JournalError("synthetic unavailable")
        body = self.remote["body"]
        if self.barrier:
            self.barrier.wait(timeout=5)
        if self.after_read:
            self.after_read()
        return Stored(body, '"' + hashlib.sha256(body).hexdigest() + '"')


class Writer:
    location = LOCATION
    scope = SCOPE
    def __init__(self, remote, anchor=None):
        self.remote, self.anchor = remote, anchor
        self.writes = 0
        self.fault = None

    def write(self, body, *, etag):
        self.writes += 1
        if self.anchor:
            assert self.anchor.load().revision % 3 == 2
            assert self.anchor.load().pending is not None
        if self.fault == "before":
            raise WriteUnknown("synthetic unknown")
        if self.fault == "conflict":
            raise Conflict("synthetic conflict")
        expected = '"' + hashlib.sha256(self.remote["body"]).hexdigest() + '"'
        if etag != expected:
            raise Conflict("synthetic stale writer")
        self.remote["body"] = body
        if self.fault == "after":
            raise WriteUnknown("synthetic lost acknowledgement")
        if self.fault == "readback":
            self.remote["read_fail"] = True


@pytest.fixture(autouse=True)
def no_real_sockets(monkeypatch):
    def blocked(*args, **kwargs):
        raise AssertionError("synthetic test must not open a socket")
    monkeypatch.setattr(socket.socket, "connect", blocked)
    monkeypatch.setattr(socket, "create_connection", blocked)


def make(tmp_path):
    anchor = SQLiteAnchor(tmp_path / "custodian.sqlite", scope=SCOPE, generation=GEN, location=LOCATION)
    anchor.create(genesis(SCOPE))
    remote = {"body": empty(SCOPE).encode()}
    reader, writer = Reader(remote), Writer(remote, anchor)
    custodian = Custodian(anchor, reader)
    return anchor, remote, reader, writer, custodian, AnchoredJournal(custodian, writer)


def test_success_receipts_survive_reopen_and_only_storage_is_anchored(tmp_path):
    anchor, remote, reader, writer, custody, runner = make(tmp_path)
    first = runner.append(INTENT, attempt_id=ATTEMPT)
    assert first.status == "ANCHORED_STORAGE_ONLY" and first.observed.sequence == 1
    assert anchor.load().revision == 3 and anchor.load().pending is None
    reopened = SQLiteAnchor(tmp_path / "custodian.sqlite", scope=SCOPE, generation=GEN, location=LOCATION)
    assert Custodian(reopened, Reader(remote)).current().head == first.observed
    second = runner.append(COMPLETE, attempt_id=NEXT)
    assert second.status == "ANCHORED_STORAGE_ONLY" and second.observed.sequence == 2
    assert reopened.load().revision == 6 and writer.writes == 2
    before = reopened.load()
    assert runner.append(INTENT, attempt_id=NEXT).status == "NO_NEW_ATTEMPT"
    assert reopened.load() == before and writer.writes == 2
    assert SUBJECT not in repr(before) + repr(anchor)


def test_preparation_and_claim_are_durable_distinct_single_use_stages(tmp_path):
    anchor, remote, reader, writer, custody, runner = make(tmp_path)
    ticket = custody.prepare(INTENT, attempt_id=ATTEMPT)
    assert anchor.load().revision == 1 and writer.writes == 0
    with pytest.raises(AnchorError):
        custody.confirm(ticket)
    # Wrong-phase confirmation sacrifices the ticket, not the durable blocker.
    # A fresh attempt is not allowed once a durable receipt exists.
    with pytest.raises(AnchorError):
        custody.prepare(COMPLETE, attempt_id=NEXT)


def test_duplicate_or_copied_tickets_never_dispatch_or_confirm_twice(tmp_path):
    anchor, remote, reader, writer, custody, runner = make(tmp_path)
    ticket = custody.prepare(INTENT, attempt_id=ATTEMPT)
    with pytest.raises(AnchorError):
        custody.claim(copy(ticket))
    claimed = custody.claim(ticket)
    with pytest.raises(AnchorError):
        custody.claim(ticket)
    assert anchor.load().revision == 2 and writer.writes == 0
    writer.write(claimed.prepared.body, etag=claimed.prepared.etag)
    assert custody.confirm(claimed).head.sequence == 1
    with pytest.raises(AnchorError):
        custody.confirm(claimed)
    assert writer.writes == 1 and anchor.load().revision == 3


@pytest.mark.parametrize("fault,expected", [("before", "UNKNOWN"), ("after", "UNKNOWN"),
                                         ("readback", "UNKNOWN"), ("conflict", "CONFLICT")])
def test_ambiguous_or_conflicting_write_stays_pending_across_restart(tmp_path, fault, expected):
    anchor, remote, reader, writer, custody, runner = make(tmp_path)
    writer.fault = fault
    assert runner.append(INTENT, attempt_id=ATTEMPT).status == expected
    assert writer.writes == 1 and anchor.load().revision == 2
    assert anchor.load().head == genesis(SCOPE) and anchor.load().pending == ATTEMPT
    remote.pop("read_fail", None)
    fresh = Custodian(anchor, Reader(remote))
    before = anchor.load()
    assert fresh.reconcile().status == "PENDING_REQUIRES_REVIEW"
    assert anchor.load() == before
    with pytest.raises(AnchorError):
        fresh.current()
    with pytest.raises(AnchorError):
        AnchoredJournal(fresh, writer).append(INTENT, attempt_id=NEXT)
    assert writer.writes == 1


def test_restart_cannot_resurrect_prepared_or_dispatched_ticket(tmp_path):
    anchor, remote, reader, writer, custody, runner = make(tmp_path)
    ticket = custody.prepare(INTENT, attempt_id=ATTEMPT)
    fresh = Custodian(anchor, Reader(remote))
    with pytest.raises(AnchorError):
        fresh.claim(ticket)
    claimed = custody.claim(ticket)
    writer.write(claimed.prepared.body, etag=claimed.prepared.etag)
    with pytest.raises(AnchorError):
        fresh.confirm(claimed)
    custody.abandon(claimed)
    with pytest.raises(AnchorError):
        custody.confirm(claimed)
    assert fresh.reconcile().observed.sequence == 1
    assert anchor.load().head.sequence == 0 and anchor.load().pending


def test_existing_unanchored_descendant_is_not_adopted_even_exact_event_repeat(tmp_path):
    anchor, remote, reader, writer, custody, runner = make(tmp_path)
    assert Journal(reader, SCOPE).prepare(INTENT, genesis(SCOPE)).base == genesis(SCOPE)
    unanchored = Journal(reader, SCOPE).prepare(INTENT, genesis(SCOPE))
    remote["body"] = unanchored.body
    with pytest.raises(AnchorError):
        runner.append(INTENT, attempt_id=ATTEMPT)
    with pytest.raises(AnchorError):
        custody.current()
    assert anchor.load().revision == writer.writes == 0


def test_journal_rollback_after_confirmed_head_is_rejected(tmp_path):
    anchor, remote, reader, writer, custody, runner = make(tmp_path)
    runner.append(INTENT, attempt_id=ATTEMPT)
    remote["body"] = empty(SCOPE).encode()
    with pytest.raises(JournalError):
        custody.current()
    with pytest.raises(JournalError):
        runner.append(COMPLETE, attempt_id=NEXT)
    assert writer.writes == 1 and anchor.load().head.sequence == 1


def test_generation_scope_location_and_path_mismatches_fail_closed(tmp_path):
    anchor, remote, reader, writer, custody, runner = make(tmp_path)
    for changes in ({"generation": NEXT}, {"scope": "other"}, {"location": "synthetic://wrong/journal"}):
        config = dict(scope=SCOPE, generation=GEN, location=LOCATION) | changes
        with pytest.raises(AnchorError):
            SQLiteAnchor(tmp_path / "custodian.sqlite", **config).load()
    reader.location = "synthetic://wrong/journal"
    with pytest.raises(AnchorError):
        custody.prepare(INTENT, attempt_id=ATTEMPT)
    reader.location = LOCATION
    writer.location = "synthetic://wrong/journal"
    with pytest.raises(AnchorError):
        runner.append(INTENT, attempt_id=ATTEMPT)
    assert writer.writes == anchor.load().revision == 0


@pytest.mark.parametrize("bad", ["missing", "empty", "garbage", "wal", "schema", "metadata"])
def test_bad_existing_state_never_reboots_or_adopts_remote(tmp_path, bad):
    anchor = SQLiteAnchor(tmp_path / "custodian.sqlite", scope=SCOPE, generation=GEN, location=LOCATION)
    if bad == "empty":
        (tmp_path / "custodian.sqlite").touch()
    elif bad == "garbage":
        (tmp_path / "custodian.sqlite").write_bytes(b"not a database")
    elif bad not in {"missing", "empty", "garbage"}:
        anchor.create(genesis(SCOPE))
        with closing(sqlite3.connect(tmp_path / "custodian.sqlite")) as connection:
            if bad == "wal":
                connection.execute("PRAGMA journal_mode=WAL")
            elif bad == "schema":
                connection.execute("PRAGMA user_version=9")
            else:
                connection.execute("UPDATE anchor SET seq=1")
            connection.commit()
    with pytest.raises(JournalError):
        anchor.load()
    with pytest.raises(AnchorError):
        anchor.create(genesis(SCOPE)) if bad != "missing" else Custodian(anchor, Reader({"body": empty(SCOPE).encode()})).current()


def test_bootstrap_is_explicit_no_overwrite_and_extra_sync_is_enforced(tmp_path):
    anchor, remote, reader, writer, custody, runner = make(tmp_path)
    with pytest.raises(AnchorError):
        anchor.create(genesis(SCOPE))
    with anchor._connection() as connection:
        assert connection.execute("PRAGMA synchronous").fetchone()[0] == 3
        assert connection.execute("PRAGMA journal_mode").fetchone()[0] == "delete"
        assert connection.execute("PRAGMA trusted_schema").fetchone()[0] == 0
    assert not hasattr(reader, "write")


def test_two_readers_of_same_head_only_one_can_prepare(tmp_path):
    anchor, remote, reader, writer, custody, runner = make(tmp_path)
    barrier = Barrier(2)
    r1, r2 = Reader(remote), Reader(remote)
    r1.barrier = r2.barrier = barrier
    c1, c2 = Custodian(anchor, r1), Custodian(anchor, r2)
    def prepare(c, attempt):
        try:
            return c.prepare(INTENT, attempt_id=attempt)
        except AnchorError:
            return "BLOCKED"
    with ThreadPoolExecutor(max_workers=2) as pool:
        a = pool.submit(prepare, c1, ATTEMPT)
        b = pool.submit(prepare, c2, NEXT)
        results = [a.result(timeout=10), b.result(timeout=10)]
    assert sum(result == "BLOCKED" for result in results) == 1
    assert anchor.load().revision == 1 and anchor.load().pending is not None
    assert writer.writes == 0


def test_real_s3_reader_and_writer_adapters_anchor_via_separate_signed_requests(tmp_path):
    remote, requests = {"body": empty(SCOPE).encode()}, []
    def handler(request):
        requests.append(request)
        if request.method == "GET":
            return httpx.Response(200, content=remote["body"], headers={"etag": '"' + hashlib.sha256(remote["body"]).hexdigest() + '"'})
        assert request.method == "PUT"
        assert request.headers["if-match"] == '"' + hashlib.sha256(remote["body"]).hexdigest() + '"'
        remote["body"] = request.content
        return httpx.Response(200)
    config = dict(endpoint="https://s3.ru-3.storage.selcloud.ru", region="ru-3", bucket="synthetic-bucket",
                  key="pult/erasure-test/synthetic/journal.json", reader={"access_key": "synthetic-reader", "secret_key": "synthetic-read-secret"})
    reader = S3JournalReader(**config, _test_transport=httpx.MockTransport(handler))
    writer = S3JournalStore(**config, writer={"access_key": "synthetic-writer", "secret_key": "synthetic-write-secret"}, _test_transport=httpx.MockTransport(handler))
    anchor = SQLiteAnchor(tmp_path / "custodian.sqlite", scope=SCOPE, generation=GEN, location=reader.location)
    anchor.create(genesis(SCOPE))
    assert AnchoredJournal(Custodian(anchor, reader), writer).append(INTENT, attempt_id=ATTEMPT).status == "ANCHORED_STORAGE_ONLY"
    assert [request.method for request in requests] == ["GET", "PUT", "GET"]
    for request in (requests[0], requests[2]):
        assert "Credential=synthetic-reader/" in request.headers["authorization"]
    assert "Credential=synthetic-writer/" in requests[1].headers["authorization"]
    reader.close()
    writer.close()


def test_custodian_rejects_writer_capability_and_wrong_reader_scope(tmp_path):
    anchor, remote, reader, writer, custody, runner = make(tmp_path)
    reader.write = writer.write
    with pytest.raises(AnchorError):
        Custodian(anchor, reader)
    del reader.write
    reader.scope = "other"
    with pytest.raises(AnchorError):
        custody.current()
    assert writer.writes == anchor.load().revision == 0


def test_readback_descendant_does_not_anchor_unreviewed_extra_history(tmp_path):
    anchor, remote, reader, writer, custody, runner = make(tmp_path)
    ticket = custody.claim(custody.prepare(INTENT, attempt_id=ATTEMPT))
    writer.write(ticket.prepared.body, etag=ticket.prepared.etag)
    descendant = Journal(reader, SCOPE).prepare(Event(NEXT, SUBJECT, "intent"), ticket.prepared.head)
    remote["body"] = descendant.body
    with pytest.raises(AnchorError):
        custody.confirm(ticket)
    assert anchor.load().head.sequence == 0 and anchor.load().revision == 2


def test_failed_or_missing_readback_consumes_confirmation_not_the_pending_blocker(tmp_path):
    anchor, remote, reader, writer, custody, runner = make(tmp_path)
    ticket = custody.claim(custody.prepare(INTENT, attempt_id=ATTEMPT))
    # A successful-looking response without a remote update must not advance anchor.
    with pytest.raises(AnchorError):
        custody.confirm(ticket)
    writer.write(ticket.prepared.body, etag=ticket.prepared.etag)
    with pytest.raises(AnchorError):
        custody.confirm(ticket)
    assert anchor.load().revision == 2


def test_current_observation_rechecks_pending_after_independent_read(tmp_path):
    anchor, remote, reader, writer, custody, runner = make(tmp_path)
    rival = Custodian(anchor, Reader(remote))
    reader.after_read = lambda: rival.prepare(INTENT, attempt_id=ATTEMPT)
    with pytest.raises(AnchorError):
        custody.current()
    assert anchor.load().pending == ATTEMPT and writer.writes == 0


def test_single_generation_does_not_claim_to_detect_joint_trusted_store_rollback(tmp_path):
    """Explicit trust-root limitation: this cannot be fixed by a same-file checksum."""
    anchor, remote, reader, writer, custody, runner = make(tmp_path)
    old_file = (tmp_path / "custodian.sqlite").read_bytes()
    runner.append(INTENT, attempt_id=ATTEMPT)
    # An administrator replacing BOTH trusted store and journal is outside the model.
    (tmp_path / "custodian.sqlite").write_bytes(old_file)
    remote["body"] = empty(SCOPE).encode()
    assert Custodian(anchor, Reader(remote)).current().head == genesis(SCOPE)


def test_sqlite_busy_prepare_does_not_dispatch_or_retry(tmp_path):
    anchor, remote, reader, writer, custody, runner = make(tmp_path)
    with closing(sqlite3.connect(tmp_path / "custodian.sqlite", isolation_level=None)) as holder:
        holder.execute("BEGIN IMMEDIATE")
        with pytest.raises(AnchorError):
            runner.append(INTENT, attempt_id=ATTEMPT)
        holder.execute("ROLLBACK")
    assert writer.writes == anchor.load().revision == 0


@pytest.mark.parametrize("stage,after", [("prepare", False), ("prepare", True), ("claim", False),
                                         ("claim", True), ("confirm", False), ("confirm", True)])
def test_storage_error_at_commit_boundary_never_retries_put_or_claims_success(tmp_path, monkeypatch, stage, after):
    anchor, remote, reader, writer, custody, runner = make(tmp_path)
    real_connect = sqlite3.connect
    wanted = {"prepare": 1, "claim": 2, "confirm": 3}[stage]
    commits = 0
    class Proxy:
        def __init__(self, connection):
            object.__setattr__(self, "connection", connection)
        def __getattr__(self, name):
            return getattr(self.connection, name)
        def __setattr__(self, name, value):
            setattr(self.connection, name, value)
        def execute(self, statement, *args):
            nonlocal commits
            if statement == "COMMIT":
                commits += 1
                if commits == wanted and not after:
                    raise sqlite3.OperationalError("synthetic storage failure")
                result = self.connection.execute(statement, *args)
                if commits == wanted and after:
                    raise sqlite3.OperationalError("synthetic acknowledgement loss")
                return result
            return self.connection.execute(statement, *args)
    monkeypatch.setattr(sqlite3, "connect", lambda *args, **kwargs: Proxy(real_connect(*args, **kwargs)))
    if stage == "confirm":
        assert runner.append(INTENT, attempt_id=ATTEMPT).status == "ANCHOR_RESPONSE_UNKNOWN"
    else:
        with pytest.raises(AnchorError):
            runner.append(INTENT, attempt_id=ATTEMPT)
    monkeypatch.setattr(sqlite3, "connect", real_connect)
    state = anchor.load()
    assert writer.writes == (1 if stage == "confirm" else 0)
    assert state.head.sequence == (1 if stage == "confirm" and after else 0)
    if stage != "prepare" or after:
        assert state.pending is not None or state.head.sequence == 1


@pytest.mark.parametrize("cancel", [KeyboardInterrupt, SystemExit])
def test_cancellation_after_put_persistence_revokes_live_confirmation_capability(tmp_path, monkeypatch, cancel):
    anchor, remote, reader, writer, custody, runner = make(tmp_path)
    original_write = writer.write
    def interrupted(body, *, etag):
        original_write(body, etag=etag)
        raise cancel("synthetic cancellation before acknowledgement")
    monkeypatch.setattr(writer, "write", interrupted)
    with pytest.raises(cancel):
        runner.append(INTENT, attempt_id=ATTEMPT)
    assert custody._active == {}
    assert anchor.load().head.sequence == 0 and anchor.load().pending == ATTEMPT


def test_bootstrap_filesystem_error_is_sanitized(tmp_path, monkeypatch):
    anchor = SQLiteAnchor(tmp_path / "custodian.sqlite", scope=SCOPE, generation=GEN, location=LOCATION)
    def denied(**kwargs):
        raise PermissionError("synthetic private absolute path must not escape")
    monkeypatch.setattr(anchor, "_check_path", denied)
    with pytest.raises(AnchorError) as error:
        anchor.create(genesis(SCOPE))
    assert "private absolute path" not in str(error.value) and error.value.__suppress_context__


def test_committed_anchor_lost_response_has_distinct_uncertain_outcome(tmp_path, monkeypatch):
    anchor, remote, reader, writer, custody, runner = make(tmp_path)
    real_connect, commits = sqlite3.connect, 0
    class Proxy:
        def __init__(self, connection):
            object.__setattr__(self, "connection", connection)
        def __getattr__(self, name):
            return getattr(self.connection, name)
        def __setattr__(self, name, value):
            setattr(self.connection, name, value)
        def execute(self, statement, *args):
            nonlocal commits
            result = self.connection.execute(statement, *args)
            if statement == "COMMIT":
                commits += 1
                if commits == 3:
                    raise sqlite3.OperationalError("synthetic reply lost after commit")
            return result
    monkeypatch.setattr(sqlite3, "connect", lambda *args, **kwargs: Proxy(real_connect(*args, **kwargs)))
    result = runner.append(INTENT, attempt_id=ATTEMPT)
    assert result.status == "ANCHOR_RESPONSE_UNKNOWN"
    assert result.observed is None and custody._active == {} and writer.writes == 1
    monkeypatch.setattr(sqlite3, "connect", real_connect)
    assert anchor.load().head.sequence == 1 and anchor.load().pending is None
    before = anchor.load()
    assert custody.reconcile(ATTEMPT).status == "OBSERVED_COMMITTED_RECEIPT"
    assert anchor.load() == before and writer.writes == 1


def test_cancellation_after_claim_returns_revokes_derived_ticket(tmp_path, monkeypatch):
    anchor, remote, reader, writer, custody, runner = make(tmp_path)
    original_claim = custody.claim
    def interrupted(ticket):
        original_claim(ticket)
        raise KeyboardInterrupt("synthetic interrupted return")
    monkeypatch.setattr(custody, "claim", interrupted)
    with pytest.raises(KeyboardInterrupt):
        runner.append(INTENT, attempt_id=ATTEMPT)
    assert custody._active == {} and writer.writes == 0
    assert anchor.load().revision == 2 and anchor.load().pending == ATTEMPT


def test_successful_confirmation_has_no_postcommit_reload_race(tmp_path, monkeypatch):
    anchor, remote, reader, writer, custody, runner = make(tmp_path)
    ticket = custody.claim(custody.prepare(INTENT, attempt_id=ATTEMPT))
    writer.write(ticket.prepared.body, etag=ticket.prepared.etag)
    def forbidden_load():
        raise AssertionError("do not reread unrelated state after successful COMMIT")
    monkeypatch.setattr(anchor, "load", forbidden_load)
    assert custody.confirm(ticket).head.sequence == 1


@pytest.mark.parametrize("change", ["mutable", "empty", "etag", "head", "base"])
def test_prepared_candidate_fields_are_validated_before_persistence(tmp_path, change):
    anchor, remote, reader, writer, custody, runner = make(tmp_path)
    prepared = Journal(reader, SCOPE).prepare(INTENT, genesis(SCOPE))
    fields = vars(prepared) | {
        "mutable": {"body": bytearray(prepared.body)}, "empty": {"body": b""},
        "etag": {"etag": "*"}, "head": {"head": prepared.base}, "base": {"base": None},
    }[change]
    with pytest.raises(JournalError):
        Prepared(**fields)
    assert anchor.load().revision == writer.writes == 0


def test_wrong_genesis_pin_does_not_create_an_anchor_file(tmp_path):
    path = tmp_path / "custodian.sqlite"
    anchor = SQLiteAnchor(path, scope=SCOPE, generation=GEN, location=LOCATION)
    with pytest.raises(AnchorError):
        anchor.create(Head(0, "a" * 64))
    assert not path.exists()
