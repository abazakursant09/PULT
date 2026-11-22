"""Portable resource ordering/failure models; NOT Linux, IAM or TLS proof.

All bare captured modules and private IO below are stand-ins. No Lease, SQLite
connection, HTTP transport, socket or credential/state file is actually opened.
The separate native proof owns filesystem/UID/flock/SSL/import-boundary evidence.
"""
from collections import Counter
import gc
import hashlib
import json
from pathlib import Path
import socket
import sys
from types import ModuleType, SimpleNamespace
import weakref

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from ops.backup.custodian_v2.codec import event_codec as codec

PAYLOAD = ROOT / "ops/backup/working_runtime.py"
CREDENTIAL = {"access_key": "FAKE_WORKING_READER_KEY", "secret_key": "FAKE_PRIVATE_SECRET_NOT_LIVE"}
SECRET = "ARTIFICIAL_EXCEPTION_DO_NOT_RENDER"
GENERATION = "1e534b17-05e7-4529-8ffa-c32162c67ad5"


def canonical(value):
    return (json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True) + "\n").encode("ascii")


def payload_module():
    module = ModuleType("_portable_working_resource_payload")
    module.__file__ = str(PAYLOAD)
    exec(compile(PAYLOAD.read_bytes(), str(PAYLOAD), "exec",
                 dont_inherit=True, optimize=0), module.__dict__)
    return module


def test_payload_loader_does_not_write_bytecode_when_enabled(tmp_path, monkeypatch):
    from importlib.machinery import SourceFileLoader

    source = b"import sys\nBYTECODE_ENABLED = not sys.dont_write_bytecode\n"
    monkeypatch.setattr(sys, "dont_write_bytecode", False)

    # Positive control: this filesystem really permits loader-created caches.
    control = tmp_path / "control" / "payload.py"
    control.parent.mkdir()
    control.write_bytes(source)
    cached = ModuleType("_portable_bytecode_control")
    SourceFileLoader(cached.__name__, str(control)).exec_module(cached)
    assert cached.BYTECODE_ENABLED is True
    assert len(list((control.parent / "__pycache__").glob("*.pyc"))) == 1

    target = tmp_path / "no_cache" / "payload.py"
    target.parent.mkdir()
    target.write_bytes(source)
    monkeypatch.setattr(sys.modules[__name__], "PAYLOAD", target)
    module = payload_module()
    assert module.BYTECODE_ENABLED is True
    assert module.__file__ == str(target)
    assert set(target.parent.iterdir()) == {target}


def profile(role="custodian"):
    credential = dict(path="/private/keys/key.json", identity=dict(device=7, inode=12),
                      access_sha256=hashlib.sha256(CREDENTIAL["access_key"].encode()).hexdigest())
    resources = dict(credential=credential)
    if role == "custodian":
        resources.update(state=dict(path="/private/state/anchor.sqlite", identity=dict(device=7, inode=10)),
            lease=dict(path="/private/state/owner.lock", identity=dict(device=7, inode=11)),
            expected=dict(sequence=1, digest="b" * 64, revision=3))
    return dict(format="pult-working-runtime-admission-v1", release=dict(root="/release", identity=dict(device=7, inode=9)),
        entry_sha256="a" * 64, role=role,
        actors=dict(custodian=dict(uid=46201, gid=46202), writer=dict(uid=46203, gid=46204)),
        namespace=dict(endpoint="https://s3.ru-3.storage.selcloud.ru", region="ru-3",
            bucket="pult-custodian-working-model", scope="pult-work-model", generation=GENERATION),
        peer_ipv4="8.8.8.8", resources=resources)


class InjectedFailure(RuntimeError):
    pass


@pytest.fixture(autouse=True)
def deny_network(monkeypatch):
    def denied(*args, **kwargs):
        raise InjectedFailure("portable resources must not access sockets")
    for name in ("connect", "connect_ex", "bind", "listen", "sendto", "sendmsg"):
        if hasattr(socket.socket, name):
            monkeypatch.setattr(socket.socket, name, denied)
    monkeypatch.setattr(socket, "create_connection", denied)
    monkeypatch.setattr(socket, "getaddrinfo", denied)


@pytest.fixture
def model(monkeypatch):
    model = SimpleNamespace(events=[], counts=Counter(), failure=None, role="custodian", phase="active",
                            entry=None, lease=None, client=None, partial=None, guard_failure=None,
                            guard_after_event=None)
    def event(name):
        model.counts[name] += 1
        model.events.append(name)
        if model.failure == name:
            raise InjectedFailure(SECRET)
    def source_guard():
        event("source_guard:" + model.phase)
        if model.guard_failure == model.phase or model.guard_after_event in model.events:
            raise InjectedFailure(SECRET)
        return True
    class FakeLease:
        def __init__(self, host, spec):
            self.fd, self.spec = 900, spec
            model.lease = self
            event("lease_construct")
            if model.failure == "missing_lease":
                self.fd = None
        def check(self):
            event("lease_check")
            if self.fd is None:
                raise InjectedFailure("missing model lease")
        def close(self):
            event("lease_close")
            self.fd = None
    class FakeAnchor:
        def __init__(self, file, *, binding, location, owner):
            event("anchor_construct")
            if owner.fd is None:
                model.counts["SQL_without_lease"] += 1
                raise InjectedFailure("SQL without ownership")
            self.binding, self.location, self._owner = binding, location, owner
            self.scope, self.generation = binding.scope, binding.generation
            if model.failure == "namespace":
                self.location = "WRONG_ARTIFICIAL_LOCATION"
        def load(self):
            event("sql_load")
            if self._owner.fd is None:
                model.counts["SQL_without_lease"] += 1
                raise InjectedFailure("SQL without ownership")
            return SimpleNamespace(head=codec.Head(1, "c" * 64 if model.failure == "base" else "b" * 64),
                revision=6 if model.failure == "revision" else 3,
                pending="ARTIFICIAL_PENDING" if model.failure == "pending" else None)
    class FakeClient:
        def __init__(self, *, endpoint, region, bucket, binding, credential, peer_ipv4):
            event("client_construct")
            self._endpoint, self._bucket, self._binding = endpoint, bucket, binding
            self._peer_ipv4, self._closed = peer_ipv4, False
            if model.failure == "partial_client":
                # Do not retain self through the test model. Its traceback must
                # be retained by Resources after the opaque constructor raises.
                model.partial = weakref.ref(self)
                raise InjectedFailure(SECRET)
            model.client = self
        def close(self):
            self._closed = True  # Match the frozen store before inner close.
            event("client_close")
    class FakeReader(FakeClient):
        pass
    class FakeWriter(FakeClient):
        pass
    standins = {
        "anchor_v2": dict(V2Anchor=FakeAnchor),
        "event_codec": {name: getattr(codec, name) for name in ("Binding", "Head", "object_key")},
        "owned_host": dict(Lease=FakeLease), "host_core": {},
        "working_live_store": dict(EventReader=FakeReader, EventWriter=FakeWriter),
    }
    for name, values in standins.items():
        module = ModuleType(name)
        module.__dict__.update(values)
        monkeypatch.setitem(sys.modules, name, module)
    payload = payload_module()  # Definitions plus controlled stand-ins only.
    model.payload, model.guard = payload, source_guard
    model.exports = FakeAnchor, FakeLease, FakeReader, FakeWriter
    real_validator = payload._validate_credential
    def validate(value):
        event("key_validate")
        if model.role == "custodian" and (model.lease is None or model.lease.fd is None):
            model.counts["key_without_lease"] += 1
            raise InjectedFailure("key without ownership")
        return real_validator(value)
    monkeypatch.setattr(payload, "_validate_credential", validate)
    def open_private(self, kind, file_spec):
        self._guard()
        event("open_" + kind)
        fd = {"state": 10, "lease": 11, "credential": 12}[kind]
        row = dict(fd=fd, kind=kind, before=SimpleNamespace(st_dev=7, st_ino=fd,
                    st_size=len(canonical(CREDENTIAL))), content=None, closed=False, close_attempted=False)
        self._nodes.append(row)
        self._files.append(row)
        self._guard()
        return row
    def file_check(self, row):
        event("private_check_" + row["kind"])
    def layout(self):
        event("state_layout")
    def pread(fd, maximum, offset):
        event("credential_read")
        model.read_args = fd, maximum, offset
        if fd != 12 or maximum != 8193 or offset != 0:
            model.counts["unexpected_read"] += 1
            raise InjectedFailure("unexpected private read")
        if model.role == "custodian" and (model.lease is None or model.lease.fd is None):
            model.counts["key_without_lease"] += 1
            raise InjectedFailure("key without ownership")
        return canonical(CREDENTIAL)
    def fstat(fd):
        return SimpleNamespace(st_dev=7, st_ino=fd)
    def close(fd):
        event("fd_close:" + str(fd))
    def actor():
        return profile(model.role)["actors"][model.role]
    monkeypatch.setattr(payload, "os", SimpleNamespace(getpid=lambda: 4242, getuid=lambda: actor()["uid"],
        geteuid=lambda: actor()["uid"], getgid=lambda: actor()["gid"], getegid=lambda: actor()["gid"],
        pread=pread, fstat=fstat, close=close))
    monkeypatch.setattr(payload.Resources, "_open_private", open_private)
    monkeypatch.setattr(payload.Resources, "_file_check", file_check)
    monkeypatch.setattr(payload.Resources, "_state_layout", layout)
    def construct(role="custodian", value=None):
        model.role = role
        return payload.Resources(canonical(profile(role) if value is None else value), source_guard)
    model.construct = construct
    return model


def rejected(model, function):
    with pytest.raises(model.payload.Refused) as caught:
        function()
    assert str(caught.value) == "working_runtime_refused"
    assert caught.value.__suppress_context__
    assert SECRET not in str(caught.value)
    return model.payload._RETAINED[-1]


def test_exports_are_exact_captured_class_aliases(model):
    p = model.payload
    assert (p.V2Anchor, p.Lease, p.EventReader, p.EventWriter) == model.exports


def test_custodian_owns_lease_and_checks_existing_base_before_any_key_or_client(model):
    resource = model.construct()
    sequence = [model.events.index(name) for name in (
        "open_state", "open_lease", "lease_construct", "anchor_construct", "sql_load",
        "open_credential", "credential_read", "key_validate", "client_construct")]
    assert sequence == sorted(sequence)
    for start, end in zip(sequence, sequence[1:]):
        assert "source_guard:active" in model.events[start + 1:end]
    assert "source_guard:active" in model.events[sequence[-1] + 1:]
    assert model.counts["SQL_without_lease"] == model.counts["key_without_lease"] == model.counts["unexpected_read"] == 0
    assert resource._lease is model.lease and model.lease.fd == 900
    assert model.read_args == (12, 8193, 0)
    assert model.counts["source_guard:active"] > len(sequence)
    snapshot = resource.snapshot()
    assert snapshot["status"] == "RUNTIME_CONSTRUCTED_OPERATION_CLOSED"
    assert snapshot["role"] == "custodian" and snapshot["lease_held"] is True
    assert snapshot["state_opened"] is snapshot["credentials_opened"] is snapshot["client_constructed"] is True
    assert all(snapshot[name] is False for name in model.payload._FLAGS)
    assert not any(key in snapshot for key in ("uid", "gid", "path", "access_key", "secret_key", "digest", "profile_sha256"))


def test_writer_constructs_only_its_credential_and_exact_writer_client(model):
    resource = model.construct("writer")
    assert type(resource._client) is model.payload.EventWriter
    for event in ("open_state", "open_lease", "lease_construct", "anchor_construct", "sql_load"):
        assert model.counts[event] == 0
    snapshot = resource.snapshot()
    assert snapshot["state_opened"] is snapshot["lease_held"] is False
    assert snapshot["credentials_opened"] is snapshot["client_constructed"] is True


@pytest.mark.parametrize("failure", ["base", "revision", "pending", "namespace", "missing_lease", "state_layout", "anchor_construct", "sql_load"])
def test_bad_state_ownership_or_namespace_refuses_before_key_and_retains_lease(model, failure):
    model.failure = failure
    resource = rejected(model, model.construct)
    assert resource._failed is True
    assert model.counts["open_credential"] == model.counts["credential_read"] == model.counts["key_validate"] == model.counts["client_construct"] == 0
    assert model.counts["lease_close"] == sum(v for k, v in model.counts.items() if k.startswith("fd_close:")) == 0
    if failure not in ("state_layout", "missing_lease"):
        assert resource._lease is model.lease and model.lease.fd == 900
    assert resource in model.payload._RETAINED


def test_partial_opaque_client_failure_keeps_original_traceback_graph_and_lease(model):
    model.failure = "partial_client"
    resource = rejected(model, model.construct)
    assert resource._client is None and resource._client_attempted is True
    assert resource._lease is model.lease and model.lease.fd == 900
    assert model.client is None and model.partial is not None
    gc.collect()
    assert model.partial() is not None  # Only retained constructor traceback owns it.
    assert any(error.__traceback__ is not None for error in resource._failures)
    for function in (resource.check, resource.close_client, resource.close, resource.snapshot):
        rejected(model, function)
    assert model.counts["client_close"] == model.counts["lease_close"] == 0
    assert all(not row["closed"] for row in resource._nodes)


def test_client_close_failure_retains_lease_private_fds_and_never_retries(model):
    resource = model.construct()
    model.failure = "client_close"
    assert rejected(model, resource.close_client) is resource
    assert model.client._closed is True and resource._client_closed is False
    assert model.lease.fd == 900 and all(not row["closed"] for row in resource._nodes)
    for function in (resource.close_client, resource.close, resource.check):
        rejected(model, function)
    assert model.counts["client_close"] == 1 and model.counts["lease_close"] == 0
    assert sum(v for k, v in model.counts.items() if k.startswith("fd_close:")) == 0


@pytest.mark.parametrize("role", ["custodian", "writer"])
def test_successful_close_orders_client_then_unload_then_private_fds_then_lease(model, role):
    resource = model.construct(role)
    resource.close_client()
    assert model.counts["client_close"] == 1
    assert all(not row["closed"] for row in resource._nodes)
    model.events.append("rollback_imports")  # Entry phase model, not actual import proof.
    model.phase = "unloading"
    resource.close()
    assert model.events.index("client_close") < model.events.index("rollback_imports")
    fd_positions = [i for i, name in enumerate(model.events) if name.startswith("fd_close:")]
    assert fd_positions and min(fd_positions) > model.events.index("rollback_imports")
    if role == "custodian":
        assert max(fd_positions) < model.events.index("lease_close")
        assert model.lease.fd is None and model.counts["lease_close"] == 1
    else:
        assert model.counts["lease_close"] == 0
    assert model.events[-1] == "source_guard:unloading"
    assert resource._closed is True and resource.snapshot()["lease_held"] is False
    before = model.counts.copy()
    resource.close()
    assert model.counts == before


def test_close_before_client_cleanup_poisoning_does_not_release_any_resource(model):
    resource = model.construct()
    rejected(model, resource.close)
    assert resource._failed is True and model.lease.fd == 900
    assert model.counts["client_close"] == model.counts["lease_close"] == 0
    assert all(not row["closed"] for row in resource._nodes)


def test_private_fd_close_failure_attempts_other_fds_but_holds_lease_without_retry(model):
    resource = model.construct()
    resource.close_client()
    model.phase, model.failure = "unloading", "fd_close:12"
    rejected(model, resource.close)
    assert all(model.counts["fd_close:" + str(fd)] == 1 for fd in (10, 11, 12))
    assert model.counts["lease_close"] == 0 and model.lease.fd == 900
    before = model.counts.copy()
    rejected(model, resource.close)
    assert model.counts == before


@pytest.mark.parametrize("phase,method", [("active", "check"), ("active", "close_client"), ("unloading", "close")])
def test_source_guard_failure_poisoning_holds_lease_and_stops_further_opaque_work(model, phase, method):
    resource = model.construct()
    if method == "close":
        resource.close_client()
    model.phase, model.guard_failure = phase, phase
    before = model.counts.copy()
    rejected(model, getattr(resource, method))
    assert model.lease.fd == 900 and model.counts["lease_close"] == 0
    assert model.counts["client_close"] == before["client_close"]
    assert sum(v for k, v in model.counts.items() if k.startswith("fd_close:")) == 0
    assert resource._failed is True


def test_credential_mismatch_refuses_client_and_keeps_custodian_ownership(model):
    value = profile()
    value["resources"]["credential"]["access_sha256"] = "0" * 64
    resource = rejected(model, lambda: model.construct(value=value))
    assert model.counts["credential_read"] == model.counts["key_validate"] == 1
    assert model.counts["client_construct"] == model.counts["lease_close"] == 0
    assert resource._lease is model.lease and model.lease.fd == 900


@pytest.mark.parametrize("stage", ["open_state", "open_lease", "lease_construct", "anchor_construct",
    "sql_load", "open_credential", "credential_read", "key_validate", "client_construct"])
def test_each_constructor_guard_boundary_stops_the_next_opaque_stage(model, stage):
    order = ["open_state", "open_lease", "lease_construct", "anchor_construct", "sql_load",
             "open_credential", "credential_read", "key_validate", "client_construct"]
    model.guard_after_event = stage
    resource = rejected(model, model.construct)
    assert model.counts[stage] == 1 and resource._failed is True
    for later in order[order.index(stage) + 1:]:
        assert model.counts[later] == 0
    assert model.counts["lease_close"] == model.counts["client_close"] == 0
    assert sum(v for k, v in model.counts.items() if k.startswith("fd_close:")) == 0
    if model.lease is not None:
        assert model.lease.fd == 900


def test_post_client_close_guard_failure_retains_lease_and_forbids_retry(model):
    resource = model.construct()
    model.guard_after_event = "client_close"
    rejected(model, resource.close_client)
    assert model.counts["client_close"] == 1 and model.lease.fd == 900
    assert model.client._closed is True and resource._client_closed is False
    before = model.counts.copy()
    rejected(model, resource.close_client)
    assert model.counts == before
    assert all(not row["closed"] for row in resource._nodes)


def test_post_private_fd_guard_failure_prevents_lease_release(model):
    resource = model.construct()
    resource.close_client()
    model.phase, model.guard_after_event = "unloading", "fd_close:10"
    rejected(model, resource.close)
    assert all(row["closed"] for row in resource._nodes)
    assert model.counts["lease_close"] == 0 and model.lease.fd == 900
    assert resource._failed is True
    before = model.counts.copy()
    rejected(model, resource.close)
    assert model.counts == before
