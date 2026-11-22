"""Portable admission models. They are not Linux ownership/provider evidence."""
from collections import Counter
import hashlib
import gc
import json
from pathlib import Path, PurePosixPath
import socket
from types import ModuleType, SimpleNamespace
import weakref

import pytest

ROOT = Path(__file__).resolve().parents[2]


def source_module(name, relative):
    file = ROOT / relative
    module = ModuleType(name)
    module.__file__ = str(file)
    # No exec_module/.pyc writes into protected predecessor source directories.
    exec(compile(file.read_bytes(), str(file), "exec", dont_inherit=True, optimize=0), module.__dict__)
    return module


gate = source_module("_test_working_runtime_entry", "ops/backup/working_runtime_admission.py")
checker = source_module("_test_unchanged_working_checker", "ops/backup/working_launch_profile.py")
PRIVATE = "ARTIFICIAL_PRIVATE_ERROR_DO_NOT_ECHO"


def profile(role="custodian"):
    credential = dict(path="/private-key/credential.json", identity=dict(device=7, inode=21),
                      access_sha256="d" * 64)
    resources = dict(credential=credential)
    if role == "custodian":
        resources.update(state=dict(path="/private-state/anchor.sqlite", identity=dict(device=7, inode=22)),
                         lease=dict(path="/private-state/owner.lock", identity=dict(device=7, inode=23)),
                         expected=dict(sequence=0, digest="e" * 64, revision=0))
    return dict(format=gate.FORMAT, release=dict(root="/release", identity=dict(device=7, inode=10)),
                entry_sha256="a" * 64, role=role,
                actors=dict(custodian=dict(uid=46111, gid=46112), writer=dict(uid=46113, gid=46114)),
                namespace=dict(endpoint="https://s3.ru-3.storage.selcloud.ru", region="ru-3",
                               bucket="pult-custodian-working-proof", scope="pult-work-proof",
                               generation="8d3c8e2f-d470-41cb-87ad-50bf698c433b"),
                peer_ipv4="1.1.1.1", resources=resources)


@pytest.fixture(autouse=True)
def no_external_io(monkeypatch):
    def denied(*args, **kwargs):
        raise AssertionError("portable test attempted external IO")
    monkeypatch.setattr(socket.socket, "connect", denied)
    monkeypatch.setattr(socket, "create_connection", denied)
    monkeypatch.setattr(gate, "_FAILED", [])
    monkeypatch.setattr(gate, "_ATTEMPTED", False)


@pytest.mark.parametrize("role", ["writer", "custodian"])
def test_exact_profile_shapes_reuse_the_accepted_namespace_grammar(monkeypatch, role):
    monkeypatch.setattr(checker, "Path", PurePosixPath)
    value = profile(role)
    assert gate._shape(checker, value, PurePosixPath("/approval/profile.json")) == PurePosixPath("/release")
    assert set(value) == gate.FIELDS


@pytest.mark.parametrize("bad", [
    "format", "extra", "role", "actor-extra", "actor-bool", "same-uid", "same-gid", "namespace",
    "bucket", "scope", "generation", "root", "profile-inside-root", "ipv4-private", "ipv4-loopback",
    "ipv4-multicast", "ipv4-v6", "ipv4-integer", "resource-extra", "credential-extra", "credential-sha",
    "credential-bool-inode", "credential-inside-source", "profile-inside-key-parent", "state-path",
    "lease-path", "state-key-same-parent", "state-under-key", "key-under-state", "expected-bool",
    "expected-revision", "expected-bound", "expected-digest", "state-identity", "lease-identity",
])
def test_unsafe_profiles_refuse_without_opening_any_resource(monkeypatch, bad):
    monkeypatch.setattr(checker, "Path", PurePosixPath)
    value, file = profile(), PurePosixPath("/approval/profile.json")
    if bad == "format": value["format"] = checker.FORMAT
    elif bad == "extra": value["receipt"] = {"status": "VERIFIED_OFFLINE_NOT_LAUNCHED"}
    elif bad == "role": value["role"] = "admin"
    elif bad == "actor-extra": value["actors"]["root"] = dict(uid=0, gid=0)
    elif bad == "actor-bool": value["actors"]["writer"]["uid"] = True
    elif bad in ("same-uid", "same-gid"):
        field = bad[5:]
        value["actors"]["writer"][field] = value["actors"]["custodian"][field]
    elif bad == "namespace": value["namespace"]["endpoint"] = "http://unapproved"
    elif bad == "bucket": value["namespace"]["bucket"] = "pult-custodian-synth-old"
    elif bad == "scope": value["namespace"]["scope"] = "synthetic"
    elif bad == "generation": value["namespace"]["generation"] = "8D3C8E2F-D470-41CB-87AD-50BF698C433B"
    elif bad == "root": value["release"]["root"] = "/release/../other"
    elif bad == "profile-inside-root": file = PurePosixPath("/release/profile.json")
    elif bad.startswith("ipv4-"):
        value["peer_ipv4"] = {"private": "10.0.0.1", "loopback": "127.0.0.1", "multicast": "224.1.1.1",
                             "v6": "::1", "integer": 16843009}[bad[5:]]
    elif bad == "resource-extra": value["resources"]["bootstrap"] = True
    elif bad == "credential-extra": value["resources"]["credential"]["secret_key"] = PRIVATE
    elif bad == "credential-sha": value["resources"]["credential"]["access_sha256"] = "A" * 64
    elif bad == "credential-bool-inode": value["resources"]["credential"]["identity"]["inode"] = True
    elif bad == "credential-inside-source": value["resources"]["credential"]["path"] = "/release/key/credential.json"
    elif bad == "profile-inside-key-parent": file = PurePosixPath("/private-key/profile.json")
    elif bad == "state-path": value["resources"]["state"]["path"] = "/private-state/other.sqlite"
    elif bad == "lease-path": value["resources"]["lease"]["path"] = "/private-state/other.lock"
    elif bad == "state-key-same-parent": value["resources"]["credential"]["path"] = "/private-state/reader.json"
    elif bad == "state-under-key":
        value["resources"]["state"]["path"] = "/private-key/state/anchor.sqlite"
        value["resources"]["lease"]["path"] = "/private-key/state/owner.lock"
    elif bad == "key-under-state": value["resources"]["credential"]["path"] = "/private-state/keys/reader.json"
    elif bad == "expected-bool": value["resources"]["expected"]["sequence"] = False
    elif bad == "expected-revision": value["resources"]["expected"]["revision"] = 1
    elif bad == "expected-bound": value["resources"]["expected"].update(sequence=1025, revision=3075)
    elif bad == "expected-digest": value["resources"]["expected"]["digest"] = PRIVATE
    elif bad in ("state-identity", "lease-identity"):
        value["resources"][bad[:-9]]["identity"]["device"] = -1
    with pytest.raises((gate.Refused, checker.Refused, ValueError, TypeError, KeyError)):
        gate._shape(checker, value, file)


@pytest.mark.parametrize("receipt", [{"status": "VERIFIED_OFFLINE_NOT_LAUNCHED"}, {}, {"format": checker.FORMAT}])
def test_closed_check_receipts_are_not_runtime_envelopes(monkeypatch, receipt):
    monkeypatch.setattr(checker, "Path", PurePosixPath)
    with pytest.raises((gate.Refused, checker.Refused, KeyError)):
        gate._shape(checker, receipt, PurePosixPath("/approval/profile.json"))


@pytest.fixture
def held_model(monkeypatch):
    value = profile()
    raw = checker.canonical(value)
    model = SimpleNamespace(events=[], fail=None, pid=123, now=1, groups=[], ids=(46111, 46112))
    module = object()
    modules = {"ops.payload": module}
    host_sys = SimpleNamespace(modules=modules, meta_path=["base"])
    host_os = SimpleNamespace(getpid=lambda: model.pid, getgroups=lambda: model.groups,
                              environ=dict(gate.ENVIRONMENT))
    monkeypatch.setattr(gate, "os", host_os)
    monkeypatch.setattr(gate, "sys", host_sys)
    monkeypatch.setattr(gate, "time", SimpleNamespace(monotonic=lambda: model.now))
    def event(name):
        model.events.append(name)
        if model.fail == name:
            raise OSError(PRIVATE)
    class Capture:
        closed = False
        def check(self): event("capture_check")
        def close(self): event("capture_close"); self.closed = True
    class Finder:
        owned = {"ops.payload": module}
        def check(self):
            event("finder_check")
            if host_sys.modules != self.owned or host_sys.meta_path != [self, "base"]:
                raise OSError(PRIVATE)
        def rollback(self):
            event("rollback")
            host_sys.meta_path.remove(self)
            host_sys.modules.clear()
    def fresh():
        event("fresh_interpreter")
        assert host_sys.meta_path == ["base"] and host_sys.modules == {}
    support = SimpleNamespace(document=json.loads, runtime=lambda: model.ids,
                              PAYLOAD_NAMES={"ops"}, fresh_interpreter=fresh)
    owner = gate.RuntimeAdmission(support, Capture(), raw, hashlib.sha256(raw).hexdigest(), 40)
    owner._finder = Finder()
    host_sys.meta_path.insert(0, owner._finder)
    owner._phase = "active"
    class Resources:
        client_closed = released = False
        def check(self): event("resource_check")
        def snapshot(self):
            event("resource_snapshot")
            return dict(role="custodian", journal_dispatch_admitted=False)
        def close_client(self):
            event("client_close")
            self.client_closed = True
        def close(self):
            owner._guard()  # Guard must remain valid after namespace rollback.
            event("resource_release")
            self.released = True
    owner._resources = Resources()
    model.owner, model.sys, model.os = owner, host_sys, host_os
    return model


def test_success_keeps_capture_until_client_unload_and_resource_release(held_model):
    m, owner = held_model, held_model.owner
    assert owner.check() is True
    assert owner.snapshot() == {"role": "custodian", "journal_dispatch_admitted": False}
    assert owner._capture.closed is False
    owner.close()
    assert owner._closed is True and owner._phase == "closed" and owner._capture.closed is True
    assert m.events.index("client_close") < m.events.index("rollback") < m.events.index("resource_release") < m.events.index("capture_close")
    assert owner._resources.released is True and gate._FAILED == []
    before = list(m.events)
    owner.close()
    assert m.events == before


@pytest.mark.parametrize("bad", ["pid", "thread", "deadline", "uid", "gid", "groups", "environment", "module", "finder"])
def test_retained_owner_refuses_runtime_identity_drift_and_never_releases(held_model, monkeypatch, bad):
    m = held_model
    if bad == "pid": m.pid += 1
    elif bad == "thread": monkeypatch.setattr(gate, "threading", SimpleNamespace(current_thread=lambda: object()))
    elif bad == "deadline": m.now = 41
    elif bad == "uid": m.ids = (0, 46112)
    elif bad == "gid": m.ids = (46111, 0)
    elif bad == "groups": m.groups = [1234]
    elif bad == "environment": m.os.environ["SSL_CERT_FILE"] = "/unapproved.pem"
    elif bad == "module": m.sys.modules["ops.payload"] = object()
    else: m.sys.meta_path.append("foreign")
    with pytest.raises(gate.Refused, match="^working_runtime_refused$"):
        m.owner.check()
    assert m.owner._failed is True and gate._FAILED == [m.owner]
    assert m.owner._capture.closed is False and not m.owner._resources.released
    with pytest.raises(gate.Refused): m.owner.close()
    assert "client_close" not in m.events and "capture_close" not in m.events


@pytest.mark.parametrize("failure", ["capture_check", "finder_check", "resource_check", "client_close", "rollback", "resource_release", "capture_close"])
def test_cleanup_uncertainty_retains_graph_and_does_not_retry(held_model, failure):
    m, owner = held_model, held_model.owner
    m.fail = failure
    with pytest.raises(gate.Refused) as error:
        owner.close()
    assert str(error.value) == "working_runtime_refused" and error.value.__suppress_context__
    assert owner._failed is True and owner._failure is not None and gate._FAILED == [owner]
    assert owner._closed is False
    before = list(m.events)
    with pytest.raises(gate.Refused): owner.close()
    assert m.events == before
    if failure != "capture_close": assert "capture_close" not in m.events
    if failure in ("client_close", "rollback"): assert owner._resources.released is False


@pytest.mark.parametrize("method", ["check", "snapshot"])
def test_closed_owner_cannot_be_reused_as_a_capability(held_model, method):
    owner = held_model.owner
    owner.close()
    with pytest.raises(gate.Refused): getattr(owner, method)()
    assert owner._closed and owner._failed


@pytest.mark.parametrize("args", [[], ["check"], ["serve", "path", "sha"], ["check", "path", "sha", "extra"]])
def test_cli_has_no_serve_or_implicit_mode(monkeypatch, capsys, args):
    calls = []
    monkeypatch.setattr(gate, "load_runtime", lambda *a: calls.append(a))
    assert gate.main(args) == 1 and calls == []
    output = capsys.readouterr()
    assert output.out == "" and output.err == "WORKING_RUNTIME_REFUSED\n"


@pytest.mark.parametrize("stage", ["snapshot", "close"])
def test_cli_emits_no_success_when_snapshot_or_cleanup_raises(monkeypatch, capsys, stage):
    calls = Counter()
    class Owner:
        _checker = checker
        def snapshot(self):
            calls["snapshot"] += 1
            if stage == "snapshot": raise OSError(PRIVATE)
            return {"status": "NOT_YET_CLOSED"}
        def close(self): calls["close"] += 1; raise OSError(PRIVATE)
    monkeypatch.setattr(gate, "load_runtime", lambda *a: Owner())
    assert gate.main(["check", "path", "sha"]) == 1
    output = capsys.readouterr()
    assert output.out == "" and output.err == "WORKING_RUNTIME_REFUSED\n"
    assert calls["snapshot"] == 1 and calls["close"] == int(stage == "close")


def test_cli_closed_receipt_does_not_report_a_still_held_lease(monkeypatch, capsys):
    events = []
    class Owner:
        _checker = checker
        def snapshot(self): events.append("snapshot"); return dict(lease_held=True, operation_admitted=False)
        def close(self): events.append("close")
    monkeypatch.setattr(gate, "load_runtime", lambda *a: Owner())
    assert gate.main(["check", "path", "sha"]) == 0
    output = capsys.readouterr()
    value = json.loads(output.out)
    assert output.err == "" and events == ["snapshot", "close"]
    assert value == dict(lease_held=False, operation_admitted=False, cleanup_confirmed=True,
                         status="LOCAL_RUNTIME_CHECKED_AND_CLOSED_NOT_OPERATIONAL")


@pytest.mark.parametrize("first_error", [OSError, gate.Refused])
def test_failed_process_attempt_latch_is_consumed_before_bootstrap(monkeypatch, first_error):
    calls = []
    def bootstrap():
        calls.append("bootstrap")
        raise first_error(PRIVATE)
    monkeypatch.setattr(gate, "_bootstrap_checker", bootstrap)
    for _ in range(2):
        with pytest.raises(gate.Refused, match="^working_runtime_refused$"):
            gate.load_runtime("path", "sha")
    assert calls == ["bootstrap"] and gate._ATTEMPTED is True


def test_predecessor_source_bytes_and_global_attributes_are_unchanged():
    assert hashlib.sha256((ROOT/gate.CHECKER).read_bytes().replace(b"\r\n", b"\n")).hexdigest() == gate.CHECKER_SHA
    assert hashlib.sha256((ROOT/".gitattributes").read_bytes()).hexdigest() == "d8e7c02e359d7166b2846337616608bd1bf7e45d5540df004efe5d060fea2cec"
    for name, wanted in gate.ADDITIONS.items():
        if name == gate.PAYLOAD: continue  # Separate CI guard requires a final non-placeholder payload pin.
        assert hashlib.sha256((ROOT/name).read_bytes().replace(b"\r\n", b"\n")).hexdigest() == wanted


@pytest.mark.parametrize("name", ["SSL_CERT_FILE", "SSL_CERT_DIR", "SSLKEYLOGFILE", "HTTP_PROXY", "HTTPS_PROXY",
                                 "ALL_PROXY", "PYTHONPATH", "PYTHONHOME", "HOME", "INVOCATION_ID"])
def test_bootstrap_refuses_hidden_environment_before_any_source_read(monkeypatch, name):
    calls = []
    env = dict(gate.ENVIRONMENT, **{name: "/unapproved"})
    monkeypatch.setattr(gate, "os", SimpleNamespace(environ=env, open=lambda *a, **k: calls.append(a)))
    with pytest.raises(gate.Refused, match="^working_runtime_refused$"):
        gate._bootstrap_checker()
    assert calls == []


@pytest.fixture
def load_model(monkeypatch):
    """Real entry control-flow, fake IO/importers; not source-custody evidence."""
    monkeypatch.setattr(checker, "Path", PurePosixPath)
    m = SimpleNamespace(events=[], bad=None, fail=None, resource_ref=None)
    value = profile()
    raw = checker.canonical(value)
    body = {name: ("CAPTURED:" + name).encode() for name in {*checker.PINS, *gate.ADDITIONS, gate.ENTRY}}
    pins = {name: hashlib.sha256(body[name]).hexdigest() for name in checker.PINS}
    additions = {name: hashlib.sha256(body[name]).hexdigest() for name in gate.ADDITIONS}
    value["entry_sha256"] = hashlib.sha256(body[gate.ENTRY]).hexdigest()
    raw = checker.canonical(value)
    approval = hashlib.sha256(raw).hexdigest()
    body["/approval/profile.json"] = raw
    host_sys = SimpleNamespace(meta_path=["base"], modules={})
    host_os = SimpleNamespace(getpid=lambda: 123, getgroups=lambda: [], environ=dict(gate.ENVIRONMENT))
    monkeypatch.setattr(gate, "os", host_os)
    monkeypatch.setattr(gate, "sys", host_sys)
    monkeypatch.setattr(gate, "time", SimpleNamespace(monotonic=lambda: 1))
    monkeypatch.setattr(gate, "ADDITIONS", additions)
    def event(name):
        m.events.append(name)
        if m.fail == name: raise OSError(PRIVATE)
    class Capture:
        closed = False
        def __init__(self): m.capture = self
        def take(self, file, maximum):
            name = str(file)
            name = name[len("/release/"):] if name.startswith("/release/") else name
            event(("take", name))
            return b"CHANGED" if name == m.bad else body[name]
        def directory(self, root): return (7, 10)
        def check(self): event("capture_check")
        def close(self): event("capture_close"); self.closed = True
    class Finder:
        def __init__(self, *args): self.owned = {}; m.finder = self
        def check(self):
            event("finder_check")
            assert host_sys.meta_path == [self, "base"] and host_sys.modules == self.owned
        def rollback(self):
            event("rollback")
            host_sys.meta_path.remove(self)
            host_sys.modules.clear()
    cold = SimpleNamespace(_runtime=lambda: event("cold_runtime"), WHEELS={}, _Finder=Finder)
    def fresh():
        event("fresh")
        assert host_sys.meta_path == ["base"] and host_sys.modules == {}
    def helper_module(name, file, blob):
        event(("helper", name))
        return SimpleNamespace(Capture=Capture) if "capture" in name else cold
    support = SimpleNamespace(path=checker.path, sha=checker.sha, shape=checker.shape, FORMAT=checker.FORMAT,
        document=checker.document, canonical=checker.canonical, runtime=lambda: (46111, 46112),
        CAPTURE=checker.CAPTURE, COLD=checker.COLD, PINS=pins, PAYLOAD_NAMES=checker.PAYLOAD_NAMES,
        _module=helper_module, _helper_bytes=lambda file: b"PINNED_HELPER", fresh_interpreter=fresh,
        wheel_inventory=lambda *a: ({}, set()), closed_layout=lambda *a: event("layout"))
    monkeypatch.setattr(gate, "_bootstrap_checker", lambda: (PurePosixPath("/release"), support))
    monkeypatch.setattr(gate, "_tables", lambda *a: ({}, {}))
    class Opaque: pass
    class Resources:
        def __init__(self, raw, guard):
            event("construct")
            self.guard = guard
            self.opaque = Opaque()
            m.resource_ref = weakref.ref(self.opaque)
            if m.fail == "opaque_after_allocation": raise OSError(PRIVATE)
        def check(self): event("resource_check"); self.guard()
        def snapshot(self): self.check(); return {"operation_admitted": False}
        def close_client(self): event("client_close"); self.guard()
        def close(self): event("resource_release"); self.guard()
    binding, lease, anchor, reader, writer = (type(n, (), {}) for n in ("Binding", "Lease", "Anchor", "Reader", "Writer"))
    payload = SimpleNamespace(V2Anchor=anchor, Lease=lease, EventReader=reader, EventWriter=writer, Resources=Resources)
    def imports(name):
        event("payload_import")
        assert name == "ops.backup.working_runtime"
        values = {name: payload, "anchor_v2": SimpleNamespace(V2Anchor=anchor, Lease=lease, Binding=binding),
                  "owned_host": SimpleNamespace(Lease=lease), "event_codec": SimpleNamespace(Binding=binding),
                  "working_live_store": SimpleNamespace(EventReader=reader, EventWriter=writer, Binding=binding)}
        host_sys.modules.update(values)
        m.finder.owned.update(values)
        return payload
    monkeypatch.setattr(gate, "importlib", SimpleNamespace(import_module=imports))
    m.approval, m.raw, m.body, m.sys = approval, raw, body, host_sys
    return m


def test_real_entry_model_retains_all_inputs_and_resources_until_explicit_close(load_model):
    m = load_model
    owner = gate.load_runtime("/approval/profile.json", m.approval)
    assert owner.check() is True and m.capture.closed is False
    assert m.resource_ref() is owner._resources.opaque
    before_import = m.events[:m.events.index("payload_import")]
    expected = set(m.body)
    assert {x[1] for x in before_import if type(x) is tuple and x[0] == "take"} == expected
    assert m.events.index("layout") < m.events.index("payload_import") < m.events.index("construct")
    with pytest.raises(gate.Refused): gate.load_runtime("/approval/profile.json", m.approval)
    assert m.events.count("construct") == 1
    owner.close()
    assert m.capture.closed is True and gate._FAILED == []
    assert m.events.index("client_close") < m.events.index("rollback") < m.events.index("resource_release") < m.events.index("capture_close")


@pytest.mark.parametrize("bad", ["approval", checker.COLD, gate.CHECKER, gate.PAYLOAD, gate.ENTRY])
def test_unapproved_bytes_never_reach_payload_and_close_only_pre_resource_capture(load_model, bad):
    m = load_model
    m.bad = bad
    with pytest.raises(gate.Refused, match="^working_runtime_refused$"):
        gate.load_runtime("/approval/profile.json", "0" * 64 if bad == "approval" else m.approval)
    assert m.capture.closed is True and m.events.count("capture_close") == 1
    assert "payload_import" not in m.events and "construct" not in m.events
    assert gate._FAILED == [] and m.sys.meta_path == ["base"] and m.sys.modules == {}


def test_opaque_construction_failure_roots_original_graph_and_source_ownership(load_model):
    m = load_model
    m.fail = "opaque_after_allocation"
    with pytest.raises(gate.Refused, match="^working_runtime_refused$"):
        gate.load_runtime("/approval/profile.json", m.approval)
    assert len(gate._FAILED) == 1
    owner = gate._FAILED[0]
    gc.collect()
    assert m.resource_ref() is owner._resources.opaque
    assert owner._failure is not None and owner._capture is m.capture and m.capture.closed is False
    assert m.finder.owned and m.sys.modules == m.finder.owned
    before = list(m.events)
    with pytest.raises(gate.Refused): owner.close()
    with pytest.raises(gate.Refused): gate.load_runtime("/approval/profile.json", m.approval)
    assert m.events == before


def test_pre_resource_close_uncertainty_is_retained_without_claiming_cleanup(load_model):
    m = load_model
    m.bad, m.fail = gate.PAYLOAD, "capture_close"
    with pytest.raises(gate.Refused): gate.load_runtime("/approval/profile.json", m.approval)
    assert "construct" not in m.events and "payload_import" not in m.events
    assert len(gate._FAILED) == 1 and gate._FAILED[0][0] is m.capture and not m.capture.closed
