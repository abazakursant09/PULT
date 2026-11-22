"""Portable worker/bootstrap/pipe models; NOT native PID1 or resource evidence.

Every Profile, runtime, private file descriptor and parent pipe is a stand-in.
No real root adapter, credential, SQLite, HTTP client or native child executes.
"""
from collections import Counter
import gc
import hashlib
import json
from pathlib import Path, PurePosixPath
import socket
import stat
import sys
from types import ModuleType, SimpleNamespace
import weakref

import pytest

ROOT = Path(__file__).resolve().parents[2]
FILE = ROOT / "ops/backup/working_runtime_stop_worker.py"
PRIVATE = "ARTIFICIAL_PRIVATE_FAILURE_NOT_FOR_OUTPUT"


def source_module():
    module = ModuleType("_portable_runtime_stop_worker")
    module.__file__ = str(FILE)
    exec(compile(FILE.read_bytes(), str(FILE), "exec", dont_inherit=True, optimize=0), module.__dict__)
    return module


@pytest.fixture
def worker():
    return source_module()


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    def denied(*args, **kwargs):
        raise AssertionError("portable worker model attempted network access")
    monkeypatch.setattr(socket.socket, "connect", denied)
    monkeypatch.setattr(socket, "create_connection", denied)
    monkeypatch.setattr(socket, "getaddrinfo", denied)


@pytest.fixture
def model(worker, monkeypatch):
    m = SimpleNamespace(worker=worker, events=[], counts=Counter(), writes=[], fail=None,
                        fail_guard=None, guards=0, partial=None, pid=321)
    def event(name):
        m.events.append(name)
        m.counts[name] += 1
        if m.fail == name:
            raise OSError(PRIVATE)
    class Owner:
        def snapshot(self):
            event("snapshot")
            return dict(status="RUNTIME_CONSTRUCTED_OPERATION_CLOSED", role="custodian",
                        client_constructed=True, credentials_opened=True, state_opened=True,
                        lease_held=True, **dict.fromkeys(worker.FLAGS, False))
        def close(self):
            event("close")
    class Opaque:
        pass
    class Runtime:
        def load_runtime(self, file, approved):
            m.load_args = file, approved
            partial = Opaque()
            m.partial = weakref.ref(partial)
            event("load")
            return m.owner
    class Profile:
        sha, role = "a" * 64, "custodian"
        actor = {"uid": 46201, "gid": 46202}
        spec = {"runtime_profile": {"path": "/approval/runtime.json", "sha256": "b" * 64}}
        inner = {"role": role}
        def check(self):
            event("profile_check")
            m.guards += 1
            if m.fail_guard == m.guards:
                raise OSError(PRIVATE)
        def actor_guard(self):
            event("actor_guard")
            return "pult-call-gate-" + "c" * 32 + ".service"
        def runtime_module(self):
            event("runtime_module")
            return m.runtime
        def frame(self, status, observation_sha256=None):
            event("frame:" + status)
            return dict(format="pult-working-runtime-stop-worker-v1", approval=self.sha,
                        role=self.role, unit=self.actor_guard(), pid=m.pid, start_ticks=12345,
                        status=status, observation_sha256=observation_sha256,
                        **dict.fromkeys(worker.FLAGS, False))
    m.owner, m.runtime, m.profile = Owner(), Runtime(), Profile()
    def load_profile(file, approved):
        event("profile_load")
        m.profile_args = file, approved
        return m.profile
    def write(fd, raw):
        assert fd == 1
        frame = json.loads(raw)
        event("write:" + frame["status"])
        m.writes.append(frame)
        return len(raw)
    def wait(token):
        assert token in (worker.GO, worker.CLOSE)
        event("wait:GO" if token == worker.GO else "wait:CLOSE")
    monkeypatch.setattr(worker, "os", SimpleNamespace(getpid=lambda: m.pid,
                        environ=dict(worker.ENVIRONMENT), write=write))
    monkeypatch.setattr(worker, "_load_profile", load_profile)
    monkeypatch.setattr(worker, "_wait_token", wait)
    monkeypatch.setattr(worker, "_parent_live", lambda: event("parent_live"))
    monkeypatch.setattr(worker, "held", lambda owner: (event("held"), setattr(m, "held_owner", owner)))
    return m


def test_positive_protocol_keeps_exact_owner_until_one_confirmed_close(model, capsys):
    m, w = model, model.worker
    assert w.main(["/approval/outer.json", "a" * 64]) == 0
    assert [row["status"] for row in m.writes] == ["READY", "HELD", "CLOSED"]
    assert m.writes[0]["observation_sha256"] is None
    assert m.writes[1]["observation_sha256"] == m.writes[2]["observation_sha256"]
    assert len(m.writes[1]["observation_sha256"]) == 64
    assert all(row[k] is False for row in m.writes for k in w.FLAGS)
    for before, after in (("write:READY", "wait:GO"), ("wait:GO", "runtime_module"),
                          ("runtime_module", "load"), ("load", "snapshot"),
                          ("snapshot", "write:HELD"), ("write:HELD", "held"),
                          ("wait:CLOSE", "close"), ("close", "write:CLOSED")):
        assert m.events.index(before) < m.events.index(after)
    assert m.held_owner is w._CONTEXT["owner"] is m.owner
    assert w._CONTEXT["runtime"] is m.runtime and w._FAILED == []
    assert m.counts["load"] == m.counts["snapshot"] == m.counts["close"] == 1
    assert m.guards == 6 and m.load_args == ("/approval/runtime.json", "b" * 64)
    assert capsys.readouterr().err == ""


@pytest.mark.parametrize("stage", ["profile_load", "profile_check", "actor_guard", "write:READY",
    "wait:GO", "runtime_module", "load", "snapshot", "write:HELD", "held", "wait:CLOSE", "close", "write:CLOSED"])
def test_failure_retains_original_without_retry_or_forging_closed(model, capsys, stage):
    m, w = model, model.worker
    m.fail = stage
    assert w.main(["/approval/outer.json", "a" * 64]) == 1
    output = capsys.readouterr()
    assert output.out == "" and output.err == "WORKING_RUNTIME_STOP_WORKER_REFUSED\n"
    assert PRIVATE not in output.err and len(w._FAILED) == 1
    original = w._FAILED[0]
    assert isinstance(original, OSError) and original.args == (PRIVATE,) and original.__traceback__ is not None
    assert not any(row["status"] == "CLOSED" for row in m.writes)
    assert m.counts["close"] == int(stage in ("close", "write:CLOSED"))
    if m.counts["load"] and stage != "load":
        assert w._CONTEXT["owner"] is m.owner
    if stage == "load":
        gc.collect()
        assert m.partial() is not None  # Only the original traceback retains it.
        assert w._CONTEXT["runtime"] is m.runtime and "owner" not in w._CONTEXT
    before = list(m.events)
    assert w.main(["/approval/outer.json", "a" * 64]) == 1
    assert m.events == before and w._FAILED[0] is original


@pytest.mark.parametrize("guard", [2, 3, 4, 5, 6])
def test_each_source_parent_guard_failure_refuses_later_effects(model, guard):
    m, w = model, model.worker
    m.fail_guard = guard
    assert w.main(["/approval/outer.json", "a" * 64]) == 1
    assert m.guards == guard and not any(row["status"] == "CLOSED" for row in m.writes)
    assert m.counts["load"] == int(guard >= 4)
    assert m.counts["close"] == int(guard == 6)
    if guard >= 4:
        assert w._CONTEXT["owner"] is m.owner


@pytest.mark.parametrize("args", [[], ["one"], ["one", "two", "three"]])
def test_cli_wrong_arity_has_no_profile_or_resource_effects(model, args):
    assert model.worker.main(args) == 1
    assert model.events == []


def test_success_also_consumes_process_attempt(model):
    assert model.worker.main(["outer", "sha"]) == 0
    before = list(model.events)
    assert model.worker.main(["outer", "sha"]) == 1
    assert model.events == before


def test_short_ready_write_cannot_reach_go_or_runtime(model, monkeypatch):
    writes = Counter()
    def short(fd, raw):
        writes["attempts"] += 1
        return len(raw) - 1
    monkeypatch.setattr(model.worker.os, "write", short)
    assert model.worker.main(["outer", "sha"]) == 1
    assert writes["attempts"] == 1
    assert model.counts["wait:GO"] == model.counts["runtime_module"] == model.counts["load"] == 0


@pytest.mark.parametrize("flag", sorted(source_module().FLAGS))
def test_authoritative_snapshot_never_emits_held_or_releases_owner(model, monkeypatch, flag):
    value = dict.fromkeys(model.worker.FLAGS, False)
    value[flag] = True
    monkeypatch.setattr(model.owner, "snapshot", lambda: value)
    assert model.worker.main(["outer", "sha"]) == 1
    assert [row["status"] for row in model.writes] == ["READY"]
    assert model.worker._CONTEXT["owner"] is model.owner and model.counts["close"] == 0


@pytest.mark.parametrize("change", [{"launch_admitted": True}, {"pid": True}, {"approval": "x"},
                                   {"extra": PRIVATE}, {"status": "OTHER"}])
def test_unsafe_frame_is_refused_before_any_stdout_write(model, monkeypatch, change):
    original = model.profile.frame
    monkeypatch.setattr(model.profile, "frame", lambda *a: dict(original(*a), **change))
    assert model.worker.main(["outer", "sha"]) == 1
    assert model.writes == [] and model.counts["load"] == 0


@pytest.mark.parametrize("bad", ["environment", "pid", "thread"])
def test_guard_refuses_drift_before_profile_or_resource_checks(model, monkeypatch, bad):
    w = model.worker
    w._CONTEXT.update(pid=model.pid, thread=w.threading.current_thread())
    if bad == "environment": w.os.environ["SSL_CERT_FILE"] = "/private/unapproved.pem"
    elif bad == "pid": model.pid += 1
    else: monkeypatch.setattr(w, "threading", SimpleNamespace(current_thread=lambda: object()))
    with pytest.raises(w.Refused): w._guard(model.profile)
    assert model.events == []


@pytest.mark.parametrize("bad", [b'{"a":1,"a":1}\n', b'{"nested":{"a":1,"a":2}}\n',
    b'{"a":NaN}\n', b'{"a":Infinity}\n', b'{ "a":1}\n', b'{"a":1}', b'[]\n', b'\xff'])
def test_noncanonical_or_duplicate_documents_refuse(worker, bad):
    with pytest.raises((worker.Refused, ValueError, UnicodeError)):
        worker.document(bad)


def test_canonical_document_has_exact_ascii_lf_roundtrip(worker):
    raw = worker.canonical({"z": "\u044f", "a": [False, 1, None]})
    assert raw == b'{"a":[false,1,null],"z":"\\u044f"}\n'
    assert worker.document(raw) == {"z": "\u044f", "a": [False, 1, None]}


@pytest.mark.parametrize("name", ["SSL_CERT_FILE", "SSL_CERT_DIR", "SSLKEYLOGFILE", "HTTPS_PROXY", "PYTHONPATH", "INVOCATION_ID"])
def test_exact_environment_precedes_any_protected_file_read(worker, monkeypatch, name):
    calls = Counter()
    monkeypatch.setattr(worker, "os", SimpleNamespace(environ=dict(worker.ENVIRONMENT, **{name: PRIVATE})))
    monkeypatch.setattr(worker, "_root_read", lambda *a: calls.update(read=1))
    with pytest.raises(worker.Refused): worker._load_profile("/approval/profile.json", "a" * 64)
    assert calls["read"] == 0


@pytest.fixture
def bootstrap(worker, monkeypatch):
    """Only an artificial root-module definition executes, never the real adapter."""
    m = SimpleNamespace(worker=worker, counts=Counter(), reads=[], bad=None)
    class ModelPath(PurePosixPath):
        def absolute(self):
            assert self.is_absolute(), "the portable model does not resolve relative paths"
            return self
    worker_file = PurePosixPath("/adapter/ops/backup/working_runtime_stop_worker.py")
    stop_file = PurePosixPath("/adapter/ops/backup/working_runtime_stop.py")
    stop = b"EFFECTS['stop_exec'] += 1\nclass Profile:\n def __init__(self,file,approved,root):\n  EFFECTS['construct'] += 1\n  assert root is False\n  self.raw=RAW\n  self.sha=APPROVAL\n  self.spec=SPEC\n"
    bodies = {"stop": stop, "worker": b"ARTIFICIAL_SELF_SOURCE", "runtime": b"RUNTIME", "guard": b"GUARD",
              "supervisor": b"SUPERVISOR", "core": b"CORE"}
    sources = {key: dict(path=str(stop_file if key == "stop" else worker_file if key == "worker" else PurePosixPath("/parents")/(key+".py")),
                        sha256=hashlib.sha256(raw).hexdigest()) for key, raw in bodies.items()}
    spec = dict(format="pult-working-runtime-stop-v1", mode="LOCAL_ROLE_CONSTRUCTION_ONLY", sources=sources,
                runtime_profile={}, controller={}, control={}, role="custodian", actor={})
    m.spec, m.profile_file, m.bodies = spec, "/approval/outer.json", bodies
    m.raw = worker.canonical(spec)
    m.approved = worker.digest(m.raw)
    real_module = ModuleType
    def module(name):
        m.counts["module_create"] += 1
        value = real_module(name)
        value.__dict__.update(EFFECTS=m.counts, RAW=m.raw, APPROVAL=m.approved, SPEC=m.spec)
        return value
    def read(file, maximum):
        m.reads.append((str(file), maximum))
        if str(file) == m.profile_file:
            return m.raw
        key = next(key for key, row in m.spec["sources"].items() if row["path"] == str(file))
        return b"CHANGED" if m.bad == key else bodies[key]
    monkeypatch.setattr(worker, "Path", ModelPath)
    monkeypatch.setattr(worker, "__file__", str(worker_file))
    monkeypatch.setattr(worker, "ModuleType", module)
    monkeypatch.setattr(worker, "_initial_runtime", lambda: m.counts.update(initial=1))
    monkeypatch.setattr(worker, "_root_read", read)
    return m


def test_independent_bootstrap_compiles_only_approved_fixed_stop_after_self_check(bootstrap):
    m, w = bootstrap, bootstrap.worker
    before = set(sys.modules)
    result = w._load_profile(m.profile_file, m.approved)
    assert result.spec == m.spec and result.raw == m.raw
    assert m.counts == dict(initial=1, module_create=1, stop_exec=1, construct=1)
    assert [row[0] for row in m.reads] == [m.profile_file, m.spec["sources"]["worker"]["path"], m.spec["sources"]["stop"]["path"]]
    assert set(sys.modules) == before and w._CONTEXT["stop"].__file__ == m.spec["sources"]["stop"]["path"]


@pytest.mark.parametrize("bad", ["approval", "worker", "stop", "stop-location", "worker-location", "format", "mode", "sources", "source-sha", "extra", "duplicate-path"])
def test_bad_bootstrap_input_never_compiles_or_constructs_profile(bootstrap, bad):
    m, w = bootstrap, bootstrap.worker
    if bad in ("worker", "stop"): m.bad = bad
    elif bad == "stop-location": m.spec["sources"]["stop"]["path"] = "/foreign/working_runtime_stop.py"
    elif bad == "worker-location": m.spec["sources"]["worker"]["path"] = "/foreign/working_runtime_stop_worker.py"
    elif bad == "format": m.spec["format"] = "old"
    elif bad == "mode": m.spec["mode"] = "LIVE"
    elif bad == "sources": del m.spec["sources"]["guard"]
    elif bad == "source-sha": m.spec["sources"]["core"]["sha256"] = "A" * 64
    elif bad == "extra": m.spec["dispatch"] = True
    elif bad == "duplicate-path": m.spec["sources"]["guard"]["path"] = m.spec["sources"]["core"]["path"]
    m.raw = w.canonical(m.spec)
    m.approved = w.digest(m.raw)
    with pytest.raises(w.Refused): w._load_profile(m.profile_file, "0" * 64 if bad == "approval" else m.approved)
    assert m.counts["module_create"] == m.counts["stop_exec"] == m.counts["construct"] == 0


@pytest.fixture
def pipe(worker, monkeypatch):
    m = SimpleNamespace(worker=worker, reads=Counter(), events=[], chunks=[], after=[], clock=0.0)
    flags = SimpleNamespace(POLLIN=1, POLLHUP=16, POLLERR=8, POLLNVAL=32)
    class Poll:
        def register(self, fd, mask): assert fd == 0
        def poll(self, timeout):
            if timeout == 0: return m.after
            return m.events.pop(0) if m.events else []
    flags.poll = Poll
    def read(fd, maximum):
        assert fd == 0
        m.reads["read"] += 1
        return m.chunks.pop(0)
    def now(): m.clock += 0.25; return m.clock
    monkeypatch.setattr(worker, "os", SimpleNamespace(environ=dict(worker.ENVIRONMENT),
                        fstat=lambda fd: SimpleNamespace(st_mode=stat.S_IFIFO), read=read))
    monkeypatch.setattr(worker, "select", flags)
    monkeypatch.setattr(worker, "time", SimpleNamespace(monotonic=now))
    return m


@pytest.mark.parametrize("token_name", ["GO", "CLOSE"])
def test_exact_fragmented_tokens_are_accepted_without_extra_reads(pipe, token_name):
    token = getattr(pipe.worker, token_name)
    pipe.chunks = [token[:3], token[3:]]
    pipe.events = [[(0, 1)], [(0, 1)]]
    pipe.worker._wait_token(token)
    assert pipe.reads["read"] == 2


@pytest.mark.parametrize("event", [[(0, 17)], [(0, 8)], [(0, 32)], [(3, 1)]])
def test_parent_hup_error_or_foreign_fd_refuses_before_queued_data_read(pipe, event):
    pipe.events, pipe.chunks = [event], [pipe.worker.GO]
    with pytest.raises(pipe.worker.Refused): pipe.worker._wait_token(pipe.worker.GO)
    assert pipe.reads["read"] == 0


@pytest.mark.parametrize("chunk", [b"", b"BAD\n", b"GO_WORKING_RUNTIME_V1\nX"])
def test_empty_wrong_or_extended_token_refuses(pipe, chunk):
    pipe.events, pipe.chunks = [[(0, 1)]], [chunk]
    with pytest.raises(pipe.worker.Refused): pipe.worker._wait_token(pipe.worker.GO)
    assert pipe.reads["read"] == 1


@pytest.mark.parametrize("after", [[(0, 16)], [(0, 1)]])
def test_exact_queued_go_followed_by_eof_or_extra_data_never_arms(pipe, after):
    pipe.events, pipe.chunks, pipe.after = [[(0, 1)]], [pipe.worker.GO], after
    with pytest.raises(pipe.worker.Refused): pipe.worker._wait_token(pipe.worker.GO)
    assert pipe.reads["read"] == 1


def test_token_deadline_expires_without_read_or_unbounded_wait(pipe):
    with pytest.raises(pipe.worker.Refused): pipe.worker._wait_token(pipe.worker.GO)
    assert pipe.reads["read"] == 0 and 2 < pipe.clock < 3


def info(mode, inode, size=10, uid=0, links=1):
    return SimpleNamespace(st_dev=7, st_ino=inode, st_mode=mode, st_uid=uid, st_gid=0,
                           st_nlink=links, st_size=size, st_mtime_ns=100, st_ctime_ns=200)


@pytest.fixture
def files(worker, monkeypatch):
    m = SimpleNamespace(worker=worker, counts=Counter(), closed=[], fail_close=None, acl=False, bad_link=False)
    m.nodes = {10: info(stat.S_IFDIR | 0o755, 10), 11: info(stat.S_IFDIR | 0o755, 11),
               12: info(stat.S_IFREG | 0o444, 12, 4)}
    def open_file(name, flags, *, dir_fd=None):
        m.counts["open"] += 1
        return {(None, "/"): 10, (10, "release"): 11, (11, "profile.json"): 12}[(dir_fd, str(name))]
    def read(fd, maximum, offset):
        assert fd == 12 and offset == 0
        m.counts["pread"] += 1
        return b"DATA"
    def close(fd):
        m.closed.append(fd)
        if fd == m.fail_close: raise OSError(PRIVATE)
    def linked(name, *, dir_fd, follow_symlinks):
        assert follow_symlinks is False
        value = m.nodes[{(10, "release"): 11, (11, "profile.json"): 12}[(dir_fd, name)]]
        return info(value.st_mode, 999, value.st_size) if m.bad_link else value
    fake_os = SimpleNamespace(open=open_file, fstat=lambda fd: m.nodes[fd], pread=read, close=close, stat=linked,
                              listxattr=lambda fd: ["system.posix_acl_access"] if m.acl else [],
                              O_RDONLY=0, O_DIRECTORY=1, O_NOFOLLOW=2, O_CLOEXEC=4, O_NONBLOCK=8)
    monkeypatch.setattr(worker, "os", fake_os)
    return m


def test_bounded_root_file_read_retains_chain_then_closes_every_descriptor(files):
    assert files.worker._root_read(PurePosixPath("/release/profile.json"), 100) == b"DATA"
    assert files.counts == dict(open=3, pread=1) and files.closed == [12, 11, 10]


@pytest.mark.parametrize("bad", ["fifo", "symlink", "oversize", "empty", "owner", "mode", "hardlink"])
def test_unsafe_file_is_denied_before_any_content_read(files, bad):
    leaf = files.nodes[12]
    if bad == "fifo": leaf.st_mode = stat.S_IFIFO | 0o444
    elif bad == "symlink": leaf.st_mode = stat.S_IFLNK | 0o777
    elif bad == "oversize": leaf.st_size = 101
    elif bad == "empty": leaf.st_size = 0
    elif bad == "owner": leaf.st_uid = 46201
    elif bad == "mode": leaf.st_mode = stat.S_IFREG | 0o664
    elif bad == "hardlink": leaf.st_nlink = 2
    with pytest.raises(files.worker.Refused): files.worker._root_read(PurePosixPath("/release/profile.json"), 100)
    assert files.counts["pread"] == 0 and files.closed == [12, 11, 10]


def test_acl_is_refused_before_content_read_and_still_closes(files):
    files.acl = True
    with pytest.raises(files.worker.Refused): files.worker._root_read(PurePosixPath("/release/profile.json"), 100)
    assert files.counts["pread"] == 0 and files.closed == [10]


def test_path_identity_race_refuses_after_read_without_leaking_fds(files):
    files.bad_link = True
    with pytest.raises(files.worker.Refused): files.worker._root_read(PurePosixPath("/release/profile.json"), 100)
    assert files.counts["pread"] == 1 and files.closed == [12, 11, 10]


def test_close_uncertainty_attempts_all_closes_and_retains_original(files):
    files.fail_close = 11
    files.nodes[12].st_mode = stat.S_IFIFO | 0o444
    with pytest.raises(files.worker.Refused): files.worker._root_read(PurePosixPath("/release/profile.json"), 100)
    assert files.counts["pread"] == 0 and files.closed == [12, 11, 10]
    original, errors, retained = files.worker._FAILED[0]
    assert isinstance(original, files.worker.Refused) and len(errors) == 1 and retained == (10, 11, 12)
    assert errors[0].args == (PRIVATE,)


def test_sticky_directory_stamp_ignores_only_shared_content_metadata(worker):
    before = info(stat.S_IFDIR | 0o1777, 1)
    after = info(before.st_mode, 1, size=1000, links=9)
    after.st_mtime_ns, after.st_ctime_ns = 333, 444
    assert worker._stamp(before) == worker._stamp(after)
    for field in ("st_ino", "st_mode", "st_uid", "st_gid"):
        changed = SimpleNamespace(**vars(before))
        setattr(changed, field, getattr(changed, field) + 1)
        assert worker._stamp(before) != worker._stamp(changed)
    normal = info(stat.S_IFDIR | 0o755, 1)
    altered = SimpleNamespace(**vars(normal))
    altered.st_size += 1
    assert worker._stamp(normal) != worker._stamp(altered)
