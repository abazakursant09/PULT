"""Captured local resource construction; no remote or journal operation API.

The fixed entry owns source/profile approval, the closed environment and the
single-attempt process lifetime. Failed/partial graphs and original tracebacks
are retained until process exit. This module never repairs or initializes state.
"""
import hashlib
from ipaddress import IPv4Address
import json
import os
from pathlib import Path
import re
import stat
import threading

from anchor_v2 import V2Anchor
from event_codec import Binding, Head, object_key
from owned_host import Lease
import host_core
from working_live_store import EventReader, EventWriter
from ops.backup.erasure_journal_s3 import _validate_credential

FORMAT = "pult-working-runtime-admission-v1"
_RETAINED = []
_FLAGS = ("operation_admitted", "launch_admitted", "identity_enrollment_admitted",
          "journal_dispatch_admitted", "purge_or_restore_admitted", "remote_io_admitted")


class Refused(Exception):
    """Fixed diagnostics only; private objects are never rendered."""


def require(value):
    if not value:
        raise Refused("working_runtime_refused")


def _digest(raw):
    return hashlib.sha256(raw).hexdigest()


def _canonical(value):
    return (json.dumps(value, sort_keys=True, separators=(",", ":"),
                       ensure_ascii=True, allow_nan=False) + "\n").encode("ascii")


def _document(raw):
    def pairs(rows):
        result = {}
        for key, value in rows:
            require(key not in result)
            result[key] = value
        return result
    return json.loads(raw, object_pairs_hook=pairs, parse_constant=lambda _: require(False))


def _path(value):
    require(type(value) is str and 1 < len(value) <= 4096
            and re.fullmatch(r"/[A-Za-z0-9_./-]+", value)
            and all(p not in ("", ".", "..") for p in value.split("/")[1:]))
    return Path(value)


def _identity(info):
    return {"device": info.st_dev, "inode": info.st_ino}


def _stamp(info):
    return tuple(getattr(info, name) for name in (
        "st_dev", "st_ino", "st_mode", "st_uid", "st_gid", "st_nlink",
        "st_size", "st_mtime_ns", "st_ctime_ns"))


def _protection(info):
    return info.st_dev, info.st_ino, info.st_mode, info.st_uid, info.st_gid


def _no_acl(fd):
    require(not any(name.startswith("system.posix_acl_") for name in os.listxattr(fd)))


def _file_spec(row, credential=False):
    require(type(row) is dict and set(row) == ({"path", "identity", "access_sha256"}
                                             if credential else {"path", "identity"}))
    _path(row["path"])
    identity = row["identity"]
    require(type(identity) is dict and set(identity) == {"device", "inode"}
            and all(type(v) is int and v >= 0 for v in identity.values()) and identity["inode"] > 0)
    if credential:
        require(type(row["access_sha256"]) is str and re.fullmatch("[a-f0-9]{64}", row["access_sha256"]))


class Resources:
    """One actor's owned existing resources, with all operations still closed.

    close_client precedes source import rollback; close follows rollback while
    the entry's unloading guard still owns Capture. At-fork registrations from
    the accepted Lease module persist: the dedicated process must then exit.
    """
    def __init__(self, raw: bytes, lifetime):
        # Install every resource slot before any opaque constructor can fail.
        self._raw, self._lifetime = raw, lifetime
        self._pid, self._thread = os.getpid(), threading.current_thread()
        self._failed = self._closed = self._ready = False
        self._failures, self._nodes, self._directories, self._files = [], [], {}, []
        self._spec = self._role = self._actor = self._raw_sha = None
        self._binding = self._location = None
        self._lease = self._anchor = self._client = self._credential = None
        self._lease_attempted = self._sql_attempted = self._client_attempted = False
        self._state_opened = self._credential_opened = self._client_constructed = False
        self._client_close_attempted = self._client_closed = False
        self._fd_close_attempted = self._lease_close_attempted = self._lease_closed = False
        try:
            require(type(raw) is bytes and 0 < len(raw) <= 20000 and callable(lifetime))
            self._raw_sha = _digest(raw)
            self._spec = _document(raw)
            require(type(self._spec) is dict and _canonical(self._spec) == raw)
            self._shape()
            self._guard()
            self._construct()
            self._ready = True
            self.check()
        except BaseException as error:
            self._poison(error)
            raise Refused("working_runtime_refused") from None

    def __repr__(self):
        return "<WorkingResources local construction; operations closed>"

    def _poison(self, error):
        if not self._failed:
            _RETAINED.append(self)
        self._failed = True
        if not any(item is error for item in self._failures):
            self._failures.append(error)  # Preserve traceback-owned partial IO.

    def _shape(self):
        s = self._spec
        require(set(s) == {"format", "release", "entry_sha256", "role", "actors",
                           "namespace", "peer_ipv4", "resources"} and s["format"] == FORMAT
                and type(s["role"]) is str and s["role"] in ("custodian", "writer"))
        actors = s["actors"]
        require(type(actors) is dict and set(actors) == {"custodian", "writer"})
        for actor in actors.values():
            require(type(actor) is dict and set(actor) == {"uid", "gid"}
                    and all(type(v) is int and 1000 <= v < 2**31 for v in actor.values()))
        require(actors["custodian"]["uid"] != actors["writer"]["uid"]
                and actors["custodian"]["gid"] != actors["writer"]["gid"])
        self._role, self._actor = s["role"], dict(actors[s["role"]])
        ns = s["namespace"]
        require(type(ns) is dict and set(ns) == {"endpoint", "region", "bucket", "scope", "generation"}
                and all(type(v) is str for v in ns.values())
                and ns["endpoint"] == "https://s3.ru-3.storage.selcloud.ru" and ns["region"] == "ru-3"
                and re.fullmatch(r"pult-custodian-working-[a-z0-9-]+", ns["bucket"])
                and re.fullmatch(r"[a-z0-9][a-z0-9-]{1,61}[a-z0-9]", ns["bucket"])
                and re.fullmatch(r"pult-work-[A-Za-z0-9_-]{1,54}", ns["scope"]))
        self._binding = Binding(ns["scope"], ns["generation"])
        prefix = object_key(self._binding, 1).rsplit("/", 1)[0] + "/"
        self._location = ns["endpoint"] + "/" + ns["bucket"] + "/" + prefix
        peer = IPv4Address(s["peer_ipv4"])
        require(type(s["peer_ipv4"]) is str and str(peer) == s["peer_ipv4"]
                and peer.is_global and not peer.is_multicast)
        r = s["resources"]
        require(type(r) is dict and set(r) == ({"credential", "state", "lease", "expected"}
                                               if self._role == "custodian" else {"credential"}))
        _file_spec(r["credential"], credential=True)
        if self._role == "custodian":
            _file_spec(r["state"])
            _file_spec(r["lease"])
            state, lease = _path(r["state"]["path"]), _path(r["lease"]["path"])
            require(state.name == "anchor.sqlite" and lease == state.parent / "owner.lock")
            expected = r["expected"]
            require(type(expected) is dict and set(expected) == {"sequence", "digest", "revision"}
                    and type(expected["sequence"]) is int and 0 <= expected["sequence"] <= 1024
                    and type(expected["revision"]) is int and expected["revision"] == 3 * expected["sequence"]
                    and type(expected["digest"]) is str and re.fullmatch("[a-f0-9]{64}", expected["digest"]))

    def _guard(self):
        require(not self._failed and not self._closed and os.getpid() == self._pid
                and threading.current_thread() is self._thread
                and type(self._raw) is bytes and _digest(self._raw) == self._raw_sha)
        require((os.getuid(), os.geteuid(), os.getgid(), os.getegid()) ==
                (self._actor["uid"], self._actor["uid"], self._actor["gid"], self._actor["gid"]))
        self._lifetime()

    def _directory(self, parts):
        prefix = ()
        for name in ("/", *parts):
            key = prefix if name == "/" else prefix + (name,)
            if key not in self._directories:
                parent = None if name == "/" else self._directories[prefix]["fd"]
                row = dict(fd=None, parent=parent, name=name, before=None, closed=False, close_attempted=False)
                self._nodes.append(row)
                row["fd"] = os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC,
                                    dir_fd=parent)
                row["before"] = os.fstat(row["fd"])
                self._directory_check(row)
                self._directories[key] = row
            prefix = key
        return self._directories[prefix]

    def _directory_check(self, row):
        require(row["fd"] is not None and not row["closed"] and not row["close_attempted"])
        info = os.fstat(row["fd"])
        linked = os.stat(row["name"], dir_fd=row["parent"], follow_symlinks=False)
        require(stat.S_ISDIR(info.st_mode) and info.st_uid in (0, self._actor["uid"])
                and (not info.st_mode & 0o022 or info.st_uid == 0 and info.st_mode & stat.S_ISVTX)
                and _protection(info) == _protection(row["before"]) == _protection(linked))
        _no_acl(row["fd"])

    def _open_private(self, kind, spec):
        self._guard()
        file = _path(spec["path"])
        parent = self._directory(file.parts[1:-1])
        row = dict(fd=None, parent=parent["fd"], name=file.name, path=file, kind=kind,
                   identity=spec["identity"], before=None, content=None, closed=False, close_attempted=False)
        self._nodes.append(row)
        self._files.append(row)
        row["fd"] = os.open(file.name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC,
                            dir_fd=parent["fd"])
        row["before"] = os.fstat(row["fd"])
        self._file_check(row)
        self._guard()
        return row

    def _file_check(self, row):
        require(row["fd"] is not None and not row["closed"] and not row["close_attempted"])
        before = os.fstat(row["fd"])
        linked = os.stat(row["name"], dir_fd=row["parent"], follow_symlinks=False)
        kind = row["kind"]
        modes = (0o400, 0o600) if kind == "credential" else (0o600,)
        for info in (before, linked):
            require(stat.S_ISREG(info.st_mode) and info.st_nlink == 1
                    and (info.st_uid, info.st_gid) == (self._actor["uid"], self._actor["gid"])
                    and stat.S_IMODE(info.st_mode) in modes and _identity(info) == row["identity"])
            require(info.st_size == 0 if kind == "lease" else
                    0 < info.st_size <= (8192 if kind == "credential" else 2 * 1024 * 1024))
        _no_acl(row["fd"])
        parent = os.fstat(row["parent"])
        require((parent.st_uid, parent.st_gid, stat.S_IMODE(parent.st_mode)) ==
                (self._actor["uid"], self._actor["gid"], 0o700))
        if kind != "state":
            require(_stamp(before) == _stamp(linked) == _stamp(row["before"]))
        else:
            # Size/times/bytes may change in future legitimate transactions.
            require(_protection(before) == _protection(linked) == _protection(row["before"]))
        if kind == "credential" and row["content"] is not None:
            body = os.pread(row["fd"], 8193, 0)
            require(body == row["content"] and _stamp(os.fstat(row["fd"])) == _stamp(before)
                    and _stamp(os.stat(row["name"], dir_fd=row["parent"], follow_symlinks=False)) == _stamp(before))

    def _state_layout(self):
        row = next(row for row in self._files if row["kind"] == "state")
        names = set()
        with os.scandir(row["parent"]) as entries:
            for item in entries:
                require(len(names) < 3)
                names.add(item.name)
        require(names == {row["name"], "owner.lock"})

    def _private_check(self):
        for row in self._directories.values():
            self._directory_check(row)
        for row in self._files:
            self._file_check(row)
        if self._role == "custodian":
            self._state_layout()
        if self._lease is not None:
            self._lease.check()

    def _construct(self):
        r, ns = self._spec["resources"], self._spec["namespace"]
        if self._role == "custodian":
            self._open_private("state", r["state"])
            self._open_private("lease", r["lease"])
            self._private_check()
            self._guard()
            spec = dict(state_file=r["state"]["path"], state_identity=r["state"]["identity"],
                        lease_file=r["lease"]["path"], lease_identity=r["lease"]["identity"],
                        custodian_uid=self._actor["uid"], socket_gid=self._actor["gid"])
            self._lease_attempted = True
            self._lease = Lease(host_core, spec)
            self._guard()
            self._private_check()
            self._sql_attempted = True
            self._anchor = V2Anchor(r["state"]["path"], binding=self._binding,
                                    location=self._location, owner=self._lease)
            self._guard()
            self._private_check()
            self._state_opened = True
            state = self._anchor.load()
            self._guard()
            self._private_check()
            expected = r["expected"]
            require(self._anchor.binding == self._binding and self._anchor.location == self._location
                    and self._anchor.scope == ns["scope"] and self._anchor.generation == ns["generation"]
                    and self._anchor._owner is self._lease
                    and state.head == Head(expected["sequence"], expected["digest"])
                    and type(state.revision) is int and state.revision == expected["revision"]
                    and state.pending is None)
        self._guard()
        credential = self._open_private("credential", r["credential"])
        self._credential_opened = True
        raw = os.pread(credential["fd"], 8193, 0)
        require(len(raw) == credential["before"].st_size)
        credential["content"] = raw
        self._file_check(credential)
        self._guard()
        self._credential = _document(raw)
        _validate_credential(self._credential)
        require(_digest(self._credential["access_key"].encode("utf-8")) == r["credential"]["access_sha256"])
        self._guard()
        self._private_check()
        self._client_attempted = True
        cls = EventReader if self._role == "custodian" else EventWriter
        self._client = cls(endpoint=ns["endpoint"], region=ns["region"], bucket=ns["bucket"],
                           binding=self._binding, credential=self._credential,
                           peer_ipv4=self._spec["peer_ipv4"])
        self._client_constructed = True
        self._guard()
        self._private_check()

    def check(self):
        try:
            require(self._ready and not self._fd_close_attempted)
            self._guard()
            self._private_check()
            require(type(self._client) is (EventReader if self._role == "custodian" else EventWriter)
                    and self._client._binding == self._binding
                    and self._client._endpoint == self._spec["namespace"]["endpoint"]
                    and self._client._bucket == self._spec["namespace"]["bucket"]
                    and self._client._peer_ipv4 == self._spec["peer_ipv4"]
                    and self._client._closed is self._client_closed)
            self._guard()
            return True
        except BaseException as error:
            self._poison(error)
            raise Refused("working_runtime_refused") from None

    def close_client(self):
        try:
            self.check()
            if self._client_closed:
                return
            require(not self._client_close_attempted)
            self._client_close_attempted = True
            self._client.close()
            self._guard()
            self._private_check()
            self._client_closed = True
            self.check()
        except BaseException as error:
            self._poison(error)
            raise Refused("working_runtime_refused") from None

    def close(self):
        try:
            require(not self._failed and os.getpid() == self._pid
                    and threading.current_thread() is self._thread)
            if self._closed:
                return
            require(self._client_closed and not self._fd_close_attempted)
            self.check()
            self._fd_close_attempted = True
            errors = []
            for row in reversed(self._nodes):
                row["close_attempted"] = True
                try:
                    require(_identity(os.fstat(row["fd"])) == _identity(row["before"]))
                    os.close(row["fd"])
                    row["closed"] = True
                except BaseException as error:
                    errors.append(error)
            if errors:
                for error in errors:
                    self._poison(error)
                raise Refused("working_runtime_refused")
            self._guard()
            if self._lease is not None:
                self._lease.check()
                self._guard()
                self._lease_close_attempted = True
                self._lease.close()  # LAST: no uncertain client/SQL/private FD remains.
                self._lease_closed = True
                self._guard()
            self._closed = True
            self._lifetime()
        except BaseException as error:
            self._poison(error)
            raise Refused("working_runtime_refused") from None

    def snapshot(self):
        try:
            require(self._ready and not self._failed and os.getpid() == self._pid
                    and threading.current_thread() is self._thread)
            if not self._closed:
                self.check()
            return dict(status="RUNTIME_CONSTRUCTED_OPERATION_CLOSED", role=self._role,
                        state_opened=self._state_opened, credentials_opened=self._credential_opened,
                        client_constructed=self._client_constructed,
                        lease_held=self._lease is not None and not self._lease_closed,
                        **dict.fromkeys(_FLAGS, False))
        except BaseException as error:
            self._poison(error)
            raise Refused("working_runtime_refused") from None
