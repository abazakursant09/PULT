"""Dormant Linux UID-authenticated custodian adapter. No deployment or purge API.

The host supplies an already-opened custodian and reader-only capability, never
untrusted configuration from the socket. Root/kernel, storage rollback and a
malicious authorized writer remain outside this local boundary's guarantee.
"""
import ctypes
import json
import os
from pathlib import Path
import socket
import stat
import struct
import sys
import time

from ops.backup.erasure_anchor import AnchorCommitUncertain, Custodian
from ops.backup.erasure_journal import (
    MAX_BYTES, Conflict, Event, Head, JournalError, Outcome, Prepared,
    _unique_pairs, _uuid, canonical, decode,
)

PROTOCOL = "pult-erasure-custodian-unix-v1"
MAX_FRAME = 2 * MAX_BYTES + 4096
SESSION_SECONDS = 60


class IsolationError(JournalError):
    """Fixed messages only; no socket payload, path or OS exception escapes."""


def _principal():
    if sys.platform != "linux" or not hasattr(socket, "SO_PEERCRED"):
        raise IsolationError("Linux peer credentials required")
    try:
        uid = os.getuid()
        fields = dict(line.split(":", 1) for line in Path("/proc/self/status").read_text().splitlines() if ":" in line)
        if (uid == 0 or [int(x) for x in fields["Uid"].split()] != [uid] * 4
                or any(int(fields[key], 16) for key in ("CapEff", "CapPrm", "CapInh", "CapAmb"))):
            raise IsolationError("unprivileged dedicated principal required")
        # This process may not gain privileges through a later exec.
        if ctypes.CDLL(None, use_errno=True).prctl(38, 1, 0, 0, 0) != 0:
            raise IsolationError("no-new-privileges required")
        return uid
    except (OSError, KeyError, ValueError):
        raise IsolationError("principal validation unavailable") from None


def _uid(value):
    if type(value) is not int or not 0 < value < 2**31:
        raise IsolationError("invalid dedicated principal")
    return value


def _path(value):
    try:
        path = Path(value)
    except (TypeError, ValueError, OSError):
        raise IsolationError("invalid local path") from None
    if not path.is_absolute() or ".." in path.parts or "\0" in str(path):
        raise IsolationError("absolute unlinked local path required")
    return path


def _ancestors(path, owner):
    for parent in path.parents:
        info = parent.lstat()
        sticky_root = info.st_uid == 0 and bool(info.st_mode & stat.S_ISVTX)
        if (not stat.S_ISDIR(info.st_mode) or info.st_uid not in (0, owner)
                or (info.st_mode & 0o022 and not sticky_root)):
            raise IsolationError("untrusted path ancestor")


def _private_file(path, owner):
    info = path.lstat()
    if (not stat.S_ISREG(info.st_mode) or info.st_uid != owner
            or stat.S_IMODE(info.st_mode) != 0o600 or info.st_nlink != 1):
        raise IsolationError("private custodian file required")


def _state_files(custodian, owner):
    path = _path(custodian._anchor._path)
    _ancestors(path, owner)
    directory = path.parent.lstat()
    if directory.st_uid != owner or stat.S_IMODE(directory.st_mode) != 0o700:
        raise IsolationError("private custodian directory required")
    _private_file(path, owner)
    for suffix in ("-journal", "-wal", "-shm"):
        sidecar = path.with_name(path.name + suffix)
        if os.path.lexists(sidecar):
            _private_file(sidecar, owner)
    custodian._anchor.load()  # existing schema/state only; never bootstrap/repair


def _socket_directory(path, owner):
    if len(os.fsencode(path)) > 100:
        raise IsolationError("socket path exceeds bound")
    _ancestors(path, owner)
    info = path.parent.lstat()
    if info.st_uid != owner or stat.S_IMODE(info.st_mode) != 0o750:
        raise IsolationError("private socket directory required")
    return info.st_gid


def _socket_file(path, owner, gid):
    info = path.lstat()
    if (not stat.S_ISSOCK(info.st_mode) or info.st_uid != owner or info.st_gid != gid
            or stat.S_IMODE(info.st_mode) != 0o660 or info.st_nlink != 1):
        raise IsolationError("pinned custodian socket required")


def _peer(connection, uid):
    _, actual, _ = struct.unpack("3i", connection.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, 12))
    if actual != uid:
        raise IsolationError("peer principal mismatch")


def _timeout(connection, deadline):
    left = deadline - time.monotonic()
    if left <= 0:
        raise IsolationError("session expired")
    connection.settimeout(left)


def _send(connection, value, deadline):
    body = canonical(value)
    if len(body) > MAX_FRAME:
        raise IsolationError("frame exceeds bound")
    _timeout(connection, deadline)
    connection.sendall(struct.pack("!I", len(body)) + body)


def _receive(connection, deadline):
    def take(size):
        body = bytearray()
        while len(body) < size:
            _timeout(connection, deadline)
            chunk = connection.recv(min(size - len(body), 8192))
            if not chunk:
                raise IsolationError("session interrupted")
            body.extend(chunk)
        return bytes(body)
    size, = struct.unpack("!I", take(4))
    if not 0 < size <= MAX_FRAME:
        raise IsolationError("frame exceeds bound")
    body = take(size)
    try:
        value = json.loads(body, object_pairs_hook=_unique_pairs)
        if type(value) is not dict or canonical(value) != body:
            raise IsolationError("invalid canonical frame")
        return value
    except (ValueError, TypeError, UnicodeError, RecursionError):
        raise IsolationError("invalid canonical frame") from None


def _head(head):
    return {"sequence": head.sequence, "digest": head.digest}


def _parse_head(value):
    if type(value) is not dict or set(value) != {"sequence", "digest"}:
        raise IsolationError("invalid head response")
    return Head(**value)


class _Binding:
    def __init__(self, *, scope, generation, location):
        _uuid(generation)
        self.fields = dict(protocol=PROTOCOL, scope=scope, generation=generation, location=location)

    def wrap(self, **fields):
        return dict(self.fields, **fields)

    def check(self, message, keys):
        if set(message) != set(self.fields) | set(keys) or any(message[k] != v for k, v in self.fields.items()):
            raise IsolationError("session binding or schema mismatch")


class UnixCustodianServer:
    """One serial listener; one attempt per authenticated connection, no resume.

    Run only in the dedicated process. Does not spawn workers, unlink sockets,
    create directories, load credentials, bootstrap state, or expose reset APIs.
    """
    def __init__(self, custodian: Custodian, socket_path, *, writer_uid):
        self.uid, self.writer_uid = _principal(), _uid(writer_uid)
        if self.uid == self.writer_uid:
            raise IsolationError("separate writer and custodian principals required")
        self.custodian, self.path = custodian, _path(socket_path)
        self.binding = _Binding(scope=custodian.scope, generation=custodian._anchor.generation,
                                location=custodian.location)
        try:
            _state_files(custodian, self.uid)
            if _socket_directory(self.path, self.uid) != os.getegid():
                raise IsolationError("socket group must match dedicated process group")
        except (OSError, UnicodeError):
            raise IsolationError("custodian files unavailable") from None

    def _session(self, connection):
        prepared = claimed = None
        deadline = time.monotonic() + SESSION_SECONDS
        try:
            _peer(connection, self.writer_uid)  # before reading any untrusted frame
            _state_files(self.custodian, self.uid)
            message = _receive(connection, deadline)
            op = message.get("op")
            if op in ("current", "reconcile"):
                self.binding.check(message, {"op"} if op == "current" else {"op", "attempt_id"})
                result = (Outcome("OBSERVED_ONLY", self.custodian.current().head) if op == "current"
                          else self.custodian.reconcile(message["attempt_id"]))
                _send(connection, self.binding.wrap(status=result.status, head=_head(result.observed)), deadline)
                return
            self.binding.check(message, {"op", "event", "attempt_id"})
            if op != "append" or type(message["event"]) is not dict or set(message["event"]) != {"purge_id", "subject_id", "phase"}:
                raise IsolationError("unsupported session operation")
            prepared = self.custodian.prepare(Event(**message["event"]), attempt_id=message["attempt_id"])
            if isinstance(prepared, Outcome):
                _send(connection, self.binding.wrap(status=prepared.status, head=_head(prepared.observed)), deadline)
                prepared = None
                return
            _send(connection, self.binding.wrap(status="PREPARED"), deadline)
            message = _receive(connection, deadline)
            self.binding.check(message, {"op"})
            if message["op"] != "claim":
                raise IsolationError("dispatch claim required")
            claimed = self.custodian.claim(prepared)  # durable BEFORE any candidate bytes
            candidate = claimed.prepared
            _send(connection, self.binding.wrap(status="DISPATCHED", body=candidate.body.decode("ascii"),
                                                etag=candidate.etag, base=_head(candidate.base),
                                                head=_head(candidate.head)), deadline)
            message = _receive(connection, deadline)
            self.binding.check(message, {"op"})
            if message["op"] != "confirm":
                raise IsolationError("confirmation required")
            try:
                state = self.custodian.confirm(claimed)
            except AnchorCommitUncertain:
                _send(connection, self.binding.wrap(status="ANCHOR_RESPONSE_UNKNOWN"), deadline)
                return
            _send(connection, self.binding.wrap(status="ANCHORED_STORAGE_ONLY", head=_head(state.head)), deadline)
        except (JournalError, OSError, UnicodeError, ValueError, TypeError):
            # The caller cannot distinguish all lost-response boundaries. No raw
            # exception or candidate/credential is sent or logged; no retry.
            return
        finally:
            if prepared is not None:
                self.custodian.abandon(prepared)
            if claimed is not None:
                self.custodian.abandon(claimed)

    def serve(self):
        """Blocking dedicated-process entry. Existing path always fails closed."""
        previous = os.umask(0o077)
        try:
            _state_files(self.custodian, self.uid)
            _socket_directory(self.path, self.uid)
            if os.path.lexists(self.path):
                raise IsolationError("socket already exists; operator review required")
            with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as listener:
                listener.bind(str(self.path))
                os.chmod(self.path, 0o660)
                _socket_file(self.path, self.uid, os.getegid())
                listener.listen(4)
                while True:
                    connection, _ = listener.accept()
                    with connection:
                        self._session(connection)
        except OSError:
            raise IsolationError("custodian listener unavailable") from None
        finally:
            os.umask(previous)
        # Socket left for explicit operator review after exit/crash. Never unlink.


class UnixAnchoredJournal:
    """Writer-side single-session runner. The writer never receives a live ticket."""
    def __init__(self, socket_path, *, custodian_uid, scope, generation, location, writer):
        self.uid, self.custodian_uid = _principal(), _uid(custodian_uid)
        if self.uid == self.custodian_uid:
            raise IsolationError("separate writer and custodian principals required")
        self.path, self.writer = _path(socket_path), writer
        self.binding = _Binding(scope=scope, generation=generation, location=location)

    def _connect(self):
        connection = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        try:
            gid = _socket_directory(self.path, self.custodian_uid)
            _socket_file(self.path, self.custodian_uid, gid)
            connection.settimeout(SESSION_SECONDS)
            connection.connect(str(self.path))
            _peer(connection, self.custodian_uid)  # server identity before event disclosure
            return connection
        except (OSError, UnicodeError, JournalError):
            connection.close()
            raise IsolationError("custodian connection refused") from None

    def _exchange(self, connection, deadline, **fields):
        _send(connection, self.binding.wrap(**fields), deadline)
        return _receive(connection, deadline)

    def observe(self, attempt_id=None, *, current=False):
        if attempt_id is not None:
            _uuid(attempt_id)
        with self._connect() as connection:
            deadline = time.monotonic() + SESSION_SECONDS
            message = self._exchange(connection, deadline, **({"op": "current"} if current else
                                      {"op": "reconcile", "attempt_id": attempt_id}))
            self.binding.check(message, {"status", "head"})
            if message["status"] not in {"OBSERVED_ONLY", "NO_SUCH_RECEIPT", "PENDING_REQUIRES_REVIEW", "OBSERVED_COMMITTED_RECEIPT"}:
                raise IsolationError("invalid observation response")
            return Outcome(message["status"], _parse_head(message["head"]))

    def append(self, event: Event, *, attempt_id):
        _uuid(attempt_id)
        if not isinstance(event, Event):
            raise IsolationError("invalid event")
        fields = self.binding.fields
        if self.writer.scope != fields["scope"] or self.writer.location != fields["location"]:
            raise IsolationError("writer location mismatch")
        with self._connect() as connection:
            deadline, confirming = time.monotonic() + SESSION_SECONDS, False
            try:
                message = self._exchange(connection, deadline, op="append", attempt_id=attempt_id,
                                         event=dict(purge_id=event.purge_id, subject_id=event.subject_id, phase=event.phase))
                if message.get("status") == "NO_NEW_ATTEMPT":
                    self.binding.check(message, {"status", "head"})
                    return Outcome("NO_NEW_ATTEMPT", _parse_head(message["head"]))
                self.binding.check(message, {"status"})
                if message["status"] != "PREPARED":
                    raise IsolationError("preparation refused")
                message = self._exchange(connection, deadline, op="claim")
                self.binding.check(message, {"status", "body", "etag", "base", "head"})
                if message["status"] != "DISPATCHED" or type(message["body"]) is not str:
                    raise IsolationError("dispatch refused")
                candidate = Prepared(message["body"].encode("ascii"), message["etag"],
                                     _parse_head(message["base"]), _parse_head(message["head"]))
                snapshot = decode(candidate.body, fields["scope"])
                snapshot.require_floor(candidate.base)
                if (snapshot.head != candidate.head or not snapshot.events
                        or any(snapshot.events[-1][key] != getattr(event, key)
                               for key in ("purge_id", "subject_id", "phase"))):
                    raise IsolationError("candidate response mismatch")
                if self.writer.scope != fields["scope"] or self.writer.location != fields["location"]:
                    raise IsolationError("writer changed after claim")
                try:
                    self.writer.write(candidate.body, etag=candidate.etag)  # exactly one PUT
                except Conflict:
                    return Outcome("CONFLICT")
                except Exception:
                    return Outcome("UNKNOWN")
                confirming = True  # send itself can fail after remote processing
                message = self._exchange(connection, deadline, op="confirm")
                if message.get("status") == "ANCHOR_RESPONSE_UNKNOWN":
                    self.binding.check(message, {"status"})
                    return Outcome("ANCHOR_RESPONSE_UNKNOWN")
                self.binding.check(message, {"status", "head"})
                if message["status"] != "ANCHORED_STORAGE_ONLY" or _parse_head(message["head"]) != candidate.head:
                    raise IsolationError("confirmation response mismatch")
                return Outcome("ANCHORED_STORAGE_ONLY", candidate.head)
            except (JournalError, OSError, UnicodeError, ValueError, TypeError):
                return Outcome("ANCHOR_RESPONSE_UNKNOWN" if confirming else "UNKNOWN")
