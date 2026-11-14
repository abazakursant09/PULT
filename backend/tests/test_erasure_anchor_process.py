"""Synthetic offline process-exit proof, not network, isolation or power-loss proof.

The parent explicitly bootstraps real SQLite. Children use only the production
custodian/journal API and a location-bound, file-backed fake object store. Abrupt
os._exit calls skip Python cleanup; they do not simulate loss of machine power.
"""
from contextlib import closing, contextmanager
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import time

if __name__ != "__main__":
    import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from ops.backup import erasure_anchor as anchor_module
from ops.backup.erasure_anchor import AnchorError, AnchoredJournal, Custodian, SQLiteAnchor
from ops.backup.erasure_journal import Conflict, Event, Journal, Stored, decode, empty, genesis

SCOPE = "synthetic"
LOCATION = "synthetic://test/journal"
GENERATION = "1e534b17-05e7-4529-8ffa-c32162c67ad5"
PURGE = "73e1c70a-3840-40ec-b5a2-6380343d9cbf"
SUBJECT = "770d659f-c20f-46dc-b3bc-3f6000204bc6"
ATTEMPT = "a9e1135b-aab0-4b4e-a10d-8f96cc696d96"
OTHER_ATTEMPT = "82047569-5c91-4b10-87c4-3a8b121e1891"
OTHER_PURGE = "3dc27e44-3920-47a4-aa51-a63a2bf8ed90"
INTENT = Event(PURGE, SUBJECT, "intent")
CRASH_EXIT = 79


def _etag(body):
    return '"' + hashlib.sha256(body).hexdigest() + '"'


def _sync_write(path, body, *, append=False):
    with path.open("ab" if append else "wb") as stream:
        stream.write(body)
        stream.flush()
        os.fsync(stream.fileno())


def _anchor(directory):
    return SQLiteAnchor(directory / "custodian.sqlite", scope=SCOPE,
                        generation=GENERATION, location=LOCATION)


def _wait_for(paths):
    deadline = time.monotonic() + 10
    while not all(path.is_file() for path in paths):
        if time.monotonic() >= deadline:
            raise AssertionError("synthetic process barrier timed out")
        time.sleep(0.01)


class _FileReader:
    """Reader-only capability: deliberately no write method or _writer member."""
    scope = SCOPE
    location = LOCATION

    def __init__(self, directory, *, participant=None, winner=None):
        self.directory = directory
        self.participant, self.winner = participant, winner
        self.reads = 0

    def read(self):
        self.reads += 1
        body = (self.directory / "object.json").read_bytes()
        stored = Stored(body, _etag(body))
        if self.participant is not None and self.reads == 1:
            # Capture the actual body before releasing either stale-state reader.
            _sync_write(self.directory / (self.participant + ".snapshot"),
                        hashlib.sha256(body).hexdigest().encode("ascii"))
            _wait_for([self.directory / (name + ".snapshot") for name in ("left", "right")])
            if self.participant != self.winner:
                # Force a stale CAS, rather than accepting SQLITE_BUSY as proof.
                _wait_for([self.directory / "reserved"])
        return stored


class _FileWriter:
    scope = SCOPE
    location = LOCATION

    def __init__(self, directory, *, participant="worker", fault=None):
        self.directory, self.participant, self.fault = directory, participant, fault

    def write(self, body, *, etag):
        state = _anchor(self.directory).load()
        assert state.pending is not None and state.revision == 2
        _sync_write(self.directory / (self.participant + ".put-calls"), b"PUT\n", append=True)
        if self.fault == "before_put":
            os._exit(CRASH_EXIT)
        path = self.directory / "object.json"
        if etag != _etag(path.read_bytes()):
            raise Conflict("synthetic stale object")
        _sync_write(path, body)
        _sync_write(self.directory / (self.participant + ".put-persisted"), b"PUT\n", append=True)
        if self.fault == "after_put":
            os._exit(CRASH_EXIT)  # Persisted object, but no acknowledgement returned.


class _CommitFault:
    def __init__(self, directory, fault):
        self.directory, self.fault, self.number = directory, fault, 0

    def boundary(self, side):
        _sync_write(self.directory / "commits.trace",
                    f"{side}:{self.number}\n".encode("ascii"), append=True)
        if self.fault == f"{side}_commit_{self.number}":
            os._exit(CRASH_EXIT)


class _ConnectionProxy:
    """Only intercept COMMIT; all SQL and transactions use real sqlite3."""
    def __init__(self, connection, fault):
        self.connection, self.fault = connection, fault

    @property
    def row_factory(self):
        return self.connection.row_factory

    @row_factory.setter
    def row_factory(self, value):
        self.connection.row_factory = value

    def __getattr__(self, name):
        return getattr(self.connection, name)

    def execute(self, sql, *args):
        if sql.strip().upper() != "COMMIT":
            return self.connection.execute(sql, *args)
        self.fault.number += 1
        self.fault.boundary("before")
        result = self.connection.execute(sql, *args)
        self.fault.boundary("after")
        return result


def _crash_child(directory, fault):
    # Install only after the parent's bootstrap; prepare/claim/confirm are #1/#2/#3.
    real_connect = sqlite3.connect
    injection = _CommitFault(directory, fault)

    def connect(*args, **kwargs):
        return _ConnectionProxy(real_connect(*args, **kwargs), injection)

    anchor_module.sqlite3.connect = connect
    custody = Custodian(_anchor(directory), _FileReader(directory))
    runner = AnchoredJournal(custody, _FileWriter(directory, fault=fault))
    result = runner.append(INTENT, attempt_id=ATTEMPT)
    if fault == "after_confirm":
        _sync_write(directory / "outcome.json", json.dumps({
            "status": result.status,
            "sequence": result.observed.sequence,
            "digest": result.observed.digest,
            "fields": sorted(vars(result)),
        }, sort_keys=True).encode("ascii"))
        os._exit(CRASH_EXIT)
    raise AssertionError("synthetic crash boundary was not reached")


def _race_child(directory, participant, winner):
    reader = _FileReader(directory, participant=participant, winner=winner)
    custody = Custodian(_anchor(directory), reader)
    writer = _FileWriter(directory, participant=participant)
    runner = AnchoredJournal(custody, writer)
    attempt = ATTEMPT if participant == "left" else OTHER_ATTEMPT
    try:
        custody.prepare(INTENT, attempt_id=attempt)
    except AnchorError as error:
        result = {"status": "BLOCKED", "reason": str(error), "reads": reader.reads}
    else:
        assert participant == winner
        # Return without claiming: the winning reservation must remain PREPARED.
        _sync_write(directory / "reserved", participant.encode("ascii"))
        result = {"status": "PREPARED", "reads": reader.reads}
    # Both processes must also fail closed on a fresh runner while pending.
    try:
        runner.append(INTENT, attempt_id=attempt)
    except AnchorError:
        result["new_append_blocked"] = True
    else:
        raise AssertionError("pending synthetic attempt permitted another append")
    print(json.dumps(result, sort_keys=True), flush=True)


def _child_environment(directory):
    # No inherited credentials, application settings, user site or parent PYTHONPATH.
    allowed = {"SYSTEMROOT", "WINDIR", "COMSPEC"}
    environment = {key: value for key, value in os.environ.items() if key.upper() in allowed}
    environment.update(PYTHONPATH=str(ROOT), PYTHONNOUSERSITE="1",
                       PYTHONDONTWRITEBYTECODE="1", PYTHONIOENCODING="utf-8",
                       TEMP=str(directory), TMP=str(directory))
    return environment


@contextmanager
def _children(directory, arguments):
    children = []
    try:
        for args in arguments:
            children.append(subprocess.Popen(
                [str(Path(sys.executable).resolve()), "-B", "-u", str(Path(__file__).resolve()),
                 args[0], str(directory), *args[1:]],
                cwd=directory, env=_child_environment(directory),
                stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                text=True, encoding="utf-8",
            ))
        yield children
    finally:
        # Never enumerate/kill unrelated processes, even when a barrier fails.
        for child in children:
            if child.poll() is None:
                child.kill()
            child.communicate(timeout=5)


def _communicate(child):
    try:
        stdout, stderr = child.communicate(timeout=20)
    except subprocess.TimeoutExpired:
        child.kill()
        child.communicate(timeout=5)
        pytest.fail("synthetic child exceeded bounded process deadline")
    assert not stderr, stderr
    return child.returncode, stdout


def _sql_state(directory):
    path = directory / "custodian.sqlite"
    with closing(sqlite3.connect(path.as_uri() + "?mode=ro", uri=True)) as connection:
        return (connection.execute("PRAGMA application_id").fetchone(),
                connection.execute("PRAGMA user_version").fetchone(),
                tuple(connection.iterdump()))


def _put_evidence(directory):
    return tuple((path.name, path.read_bytes())
                 for pattern in ("*.put-calls", "*.put-persisted")
                 for path in sorted(directory.glob(pattern)))


def _verify_reopen(directory, candidate, *, revision, phase, remote_sequence, attempt=ATTEMPT):
    reopened = _anchor(directory)
    state = reopened.load()  # Also lets SQLite recover any interrupted transaction.
    assert state.revision == revision
    assert state.head == (candidate.head if phase == "COMMITTED" else genesis(SCOPE))
    assert state.pending == (attempt if phase in {"PREPARED", "DISPATCHED"} else None)
    with closing(sqlite3.connect(directory.joinpath("custodian.sqlite").as_uri() + "?mode=ro",
                                 uri=True)) as connection:
        rows = connection.execute("SELECT * FROM attempts ORDER BY target_seq").fetchall()
    assert rows == ([] if phase is None else [(
        attempt, candidate.base.sequence, candidate.base.digest,
        candidate.head.sequence, candidate.head.digest,
        hashlib.sha256(candidate.body).hexdigest(), candidate.etag, phase,
    )])
    remote = (directory / "object.json").read_bytes()
    observed = decode(remote, SCOPE)
    assert observed.head.sequence == remote_sequence
    assert remote == (candidate.body if remote_sequence else empty(SCOPE).encode())
    before = _sql_state(directory), remote, _put_evidence(directory)
    reader = _FileReader(directory)
    writer = _FileWriter(directory, participant="reopened")
    custody = Custodian(reopened, reader)
    diagnosis = custody.reconcile()
    assert diagnosis.status == ("PENDING_REQUIRES_REVIEW" if state.pending else "OBSERVED_ONLY")
    assert diagnosis.observed == observed.head
    assert set(vars(diagnosis)) == {"status", "observed"}  # Observation, no permit/ticket.
    assert reopened.inspect_attempt(attempt) == (state, phase)
    receipt_diagnosis = custody.reconcile(attempt_id=attempt)
    expected_status = ("OBSERVED_COMMITTED_RECEIPT" if phase == "COMMITTED" else
                       "PENDING_REQUIRES_REVIEW" if state.pending else "NO_SUCH_RECEIPT")
    assert receipt_diagnosis.status == expected_status
    assert receipt_diagnosis.observed == observed.head
    assert set(vars(receipt_diagnosis)) == {"status", "observed"}
    assert _sql_state(directory) == before[0]
    for observation in (diagnosis, receipt_diagnosis):
        for operation in (custody.claim, custody.confirm):
            with pytest.raises(AnchorError):
                operation(observation)
    runner = AnchoredJournal(custody, writer)
    if state.pending:
        reads = reader.reads
        with pytest.raises(AnchorError, match="pending attempt"):
            custody.current()
        for event, attempt_id in ((INTENT, attempt),
                                  (Event(OTHER_PURGE, SUBJECT, "intent"), OTHER_ATTEMPT)):
            with pytest.raises(AnchorError, match="pending attempt"):
                runner.append(event, attempt_id=attempt_id)
        assert reader.reads == reads  # Not even an automatic read/retry of the write.
    else:
        assert custody.current() == state
        if phase == "COMMITTED":
            result = runner.append(INTENT, attempt_id=OTHER_ATTEMPT)
            assert result.status == "NO_NEW_ATTEMPT" and result.observed == candidate.head
            assert set(vars(result)) == {"status", "observed"}
    assert (_sql_state(directory), (directory / "object.json").read_bytes(),
            _put_evidence(directory)) == before


if __name__ != "__main__":
    @pytest.fixture
    def process_directory(tmp_path):
        _anchor(tmp_path).create(genesis(SCOPE))
        _sync_write(tmp_path / "object.json", empty(SCOPE).encode())
        return tmp_path


    @pytest.mark.parametrize("fault,revision,phase,remote_sequence,put_calls", [
        ("before_commit_1", 0, None, 0, 0),
        ("after_commit_1", 1, "PREPARED", 0, 0),
        ("before_commit_2", 1, "PREPARED", 0, 0),
        ("after_commit_2", 2, "DISPATCHED", 0, 0),
        ("before_commit_3", 2, "DISPATCHED", 1, 1),
        ("after_commit_3", 3, "COMMITTED", 1, 1),
        ("before_put", 2, "DISPATCHED", 0, 1),
        ("after_put", 2, "DISPATCHED", 1, 1),
        ("after_confirm", 3, "COMMITTED", 1, 1),
    ])
    def test_process_exit_at_durable_protocol_boundaries(
            process_directory, fault, revision, phase, remote_sequence, put_calls):
        directory = process_directory
        candidate = Journal(_FileReader(directory), SCOPE).prepare(INTENT, genesis(SCOPE))
        with _children(directory, [("crash", fault)]) as (child,):
            returncode, stdout = _communicate(child)
        assert returncode == CRASH_EXIT and stdout == ""
        trace = (directory / "commits.trace").read_text(encoding="ascii").splitlines()
        complete_trace = [f"{side}:{number}" for number in (1, 2, 3)
                          for side in ("before", "after")]
        if "_commit_" in fault:
            side, _, number = fault.split("_")
            assert trace == complete_trace[:complete_trace.index(f"{side}:{number}") + 1]
        else:
            assert trace == complete_trace[:(6 if fault == "after_confirm" else 4)]
        calls = directory / "worker.put-calls"
        persisted = directory / "worker.put-persisted"
        assert (calls.read_bytes() if calls.exists() else b"") == b"PUT\n" * put_calls
        assert (persisted.read_bytes() if persisted.exists() else b"") == b"PUT\n" * remote_sequence
        if fault == "after_confirm":
            assert json.loads((directory / "outcome.json").read_text(encoding="ascii")) == {
                "status": "ANCHORED_STORAGE_ONLY", "sequence": candidate.head.sequence,
                "digest": candidate.head.digest, "fields": ["observed", "status"],
            }
        _verify_reopen(directory, candidate, revision=revision, phase=phase,
                       remote_sequence=remote_sequence)


    @pytest.mark.parametrize("winner", ["left", "right"])
    def test_separate_process_stale_cas_has_one_durable_reservation_and_no_put(
            process_directory, winner):
        directory = process_directory
        candidate = Journal(_FileReader(directory), SCOPE).prepare(INTENT, genesis(SCOPE))
        with _children(directory, [("race", name, winner) for name in ("left", "right")]) as children:
            results = {}
            for name, child in zip(("left", "right"), children):
                returncode, stdout = _communicate(child)
                assert returncode == 0
                results[name] = json.loads(stdout)
        loser = "right" if winner == "left" else "left"
        assert results[winner] == {"status": "PREPARED", "reads": 1, "new_append_blocked": True}
        assert results[loser] == {
            "status": "BLOCKED", "reason": "custodian changed or has a pending attempt",
            "reads": 1, "new_append_blocked": True,
        }
        expected = hashlib.sha256(empty(SCOPE).encode()).hexdigest()
        for name in ("left", "right"):
            assert (directory / (name + ".snapshot")).read_text(encoding="ascii") == expected
        assert (directory / "reserved").read_text(encoding="ascii") == winner
        assert _put_evidence(directory) == ()
        attempt = ATTEMPT if winner == "left" else OTHER_ATTEMPT
        _verify_reopen(directory, candidate, revision=1, phase="PREPARED",
                       remote_sequence=0, attempt=attempt)


if __name__ == "__main__":
    mode, directory, *arguments = sys.argv[1:]
    if mode == "crash":
        _crash_child(Path(directory), *arguments)
    elif mode == "race":
        _race_child(Path(directory), *arguments)
    else:
        raise AssertionError("unknown synthetic process mode")
