"""Portable contracts and explicit orchestration models, NOT native admission proof.

The model never executes captured helpers or payloads and never opens a protected
release. Native UID/ACL/descriptor/import isolation belongs to the separate proof.
All namespaces, IDs, archives and credentials here are artificial.
"""
from collections import Counter
import hashlib
import io
import json
from pathlib import Path, PurePosixPath
import socket
import stat
import sys
from types import SimpleNamespace
import zipfile

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from ops.backup import working_launch_profile as gate
from ops.backup import working_journal_v2 as storage
from ops.backup.custodian_v2.cold import cold_host
from ops.backup.tests import working_launch_profile_proof as native_proof

PINS = {
    "ops/backup/operator_pg/bootstrap.py": "419fd3b888e1824a916ca1072836d23722edc60ea27027bf17bafe0de64689c4",
    "ops/backup/custodian_v2/cold/cold_host.py": "091133edcafb1110e13e6a79f51d336d9a152f2bf3f96d485b2c38d3899f438c",
    "ops/backup/working_journal_v2.py": "d2138f1c572bd8d074c88de3db6c7535077fd82e9b4dcd3f000bcd758fccc83c",
    "ops/backup/custodian_v2/codec/event_codec.py": "c33ad596b91fe927df85fca61ae32d35bb1cf3a1d02da2b09dadb69fc361d04c",
    "ops/backup/erasure_journal_s3.py": "41c8da982e016730c740f826fa0ff1ebf00923d8f067fb86757783fc0773ca1d",
    "ops/backup/erasure_journal.py": "94eeba19e645e25ec8286baebe4092a29c541c6f12858e1ff7b303f6a1a4fc98",
    "ops/canary/canary.py": "aa4b357acc6e908341c5523db8f93a32fdf3c0305a7ddc36c4c68842fbe3ee4f",
    "ops/backup/custodian/requirements.lock": "cd1f0a681aae91a132427b88897782b0f2085c8a415cd39812f8e7a059361f08",
}
NAMESPACE = dict(endpoint="https://s3.ru-3.storage.selcloud.ru", region="ru-3",
                 bucket="pult-custodian-working-portable", scope="pult-work-portable",
                 generation="1e534b17-05e7-4529-8ffa-c32162c67ad5")
SECRET = "ARTIFICIAL_PRIVATE_EXCEPTION_DO_NOT_RENDER"
AUTHORITY_FLAGS = {"credentials_opened", "state_opened", "live_clients_created",
                   "launch_admitted", "journal_dispatch_admitted",
                   "purge_or_restore_admitted", "remote_io_admitted"}


def profile():
    return dict(format=gate.FORMAT, release=dict(root="/release", identity=dict(device=7, inode=9)),
                entry_sha256="a" * 64, actor=dict(uid=64012, gid=64016), namespace=dict(NAMESPACE))


class InjectedFailure(RuntimeError):
    pass


class StrAlias(str):
    pass


@pytest.fixture(autouse=True)
def portable_only(monkeypatch):
    monkeypatch.setattr(gate, "_ATTEMPTED", False)
    def denied(*args, **kwargs):
        raise InjectedFailure("portable tests must not access sockets")
    for name in ("connect", "connect_ex", "bind", "listen", "sendto", "sendmsg"):
        if hasattr(socket.socket, name):
            monkeypatch.setattr(socket.socket, name, denied)
    monkeypatch.setattr(socket, "create_connection", denied)
    monkeypatch.setattr(socket, "getaddrinfo", denied)


@pytest.mark.parametrize("relative,wanted", list(PINS.items()))
def test_eight_predecessors_are_exact_accepted_lf_bytes(relative, wanted):
    # Checkout normalization is catalogue-only, not installed-byte attestation.
    raw = (ROOT / relative).read_bytes().replace(b"\r\n", b"\n")
    assert hashlib.sha256(raw).hexdigest() == wanted
    assert gate.PINS == PINS and len(PINS) == 8


def test_fixed_profile_and_application_inventory_has_no_authority_fields():
    assert gate.FORMAT == "pult-working-launch-profile-check-v1"
    assert gate.FIELDS == {"format", "release", "entry_sha256", "actor", "namespace"}
    assert gate.ENTRY == "ops/backup/working_launch_profile.py"
    assert gate.APP_FILES == {
        "ops.backup.working_journal_v2": "ops/backup/working_journal_v2.py",
        "ops.backup.custodian_v2.codec.event_codec": "ops/backup/custodian_v2/codec/event_codec.py",
        "ops.backup.erasure_journal_s3": "ops/backup/erasure_journal_s3.py",
        "ops.backup.erasure_journal": "ops/backup/erasure_journal.py",
        "ops.canary.canary": "ops/canary/canary.py",
    }
    assert set(gate.APP_FILES.values()) < set(PINS)
    assert gate.DEPENDENCY_NAMES == {"anyio", "certifi", "h11", "httpcore", "httpx", "idna", "typing_extensions"}


@pytest.mark.parametrize("raw", [
    b"", b"{}", b"{}\r\n", b" {}\n", b'{"a":1, "b":2}\n',
    b'{"x":1,"x":2}\n', b'{"x":{"a":1,"a":2}}\n',
    b'{"x":NaN}\n', b'{"x":Infinity}\n', b'{"x":-Infinity}\n', b'{"x":1e400}\n',
    b"[]\n", b"null\n", b"true\n", b"1\n", b"\xff", b"{", b"{}\n{}\n",
    "{}\n", bytearray(b"{}\n"), None, True,
])
def test_document_refuses_noncanonical_duplicate_nonfinite_and_wrong_types(raw):
    with pytest.raises((gate.Refused, ValueError, UnicodeError)):
        gate.document(raw)


def test_document_exact_size_boundary_and_canonical_roundtrip():
    raw = b'{"x":"' + b"a" * 19991 + b'"}\n'
    assert len(raw) == 20000 and gate.canonical(gate.document(raw)) == raw
    with pytest.raises(gate.Refused):
        gate.document(raw[:-3] + b'a"}\n')
    value = profile()
    assert gate.document(gate.canonical(value)) == value


@pytest.mark.parametrize("bad", [None, True, 0, "A" * 64, "a" * 63, "a" * 65, "g" * 64, "a" * 64 + "\n", StrAlias("a" * 64)])
def test_independent_digest_requires_exact_canonical_string(bad):
    with pytest.raises(gate.Refused):
        gate.sha(bad)


@pytest.mark.parametrize("bad", [None, True, "/", "/a/", "/a//b", "/a/./b", "/a/../b", "relative", "C:/a", "/a\\b", "/a b", "/a\n", "/" + "a" * 4096, StrAlias("/a")])
def test_profile_paths_are_exact_bounded_posix_strings(bad):
    with pytest.raises(gate.Refused):
        gate.path(bad)


@pytest.mark.parametrize("mutation", [
    lambda v: v.update(format="pult-offline-operator-host-v1"),
    lambda v: v.update(mode="SYNTHETIC_REVIEW_ENDPOINT_ONLY"),
    lambda v: v.pop("namespace"),
    lambda v: v.update(release=None),
    lambda v: v["release"].update(extra=1),
    lambda v: v["release"].update(root="/release/../other"),
    lambda v: v["release"].update(identity={"device": True, "inode": 9}),
    lambda v: v["release"].update(identity={"device": -1, "inode": 9}),
    lambda v: v["release"].update(identity={"device": 7, "inode": 0}),
    lambda v: v["release"].update(identity={"device": 7, "inode": 9, "mode": 493}),
    lambda v: v.update(entry_sha256="A" * 64),
    lambda v: v.update(actor=None),
    lambda v: v["actor"].update(uid=True),
    lambda v: v["actor"].update(uid=999),
    lambda v: v["actor"].update(gid=2**31),
    lambda v: v["actor"].update(groups=[]),
    lambda v: v["namespace"].update(key="journal.json"),
    lambda v: v["namespace"].pop("generation"),
])
def test_profile_closed_shape_rejects_legacy_extra_missing_and_bad_actor(mutation):
    value = profile()
    mutation(value)
    with pytest.raises((gate.Refused, ValueError)):
        gate.shape(value)


BAD_NAMESPACES = ([(key, bad) for key in NAMESPACE for bad in (None, True, 1, StrAlias(NAMESPACE[key]))]
    + [("endpoint", "http://s3.ru-3.storage.selcloud.ru"), ("endpoint", NAMESPACE["endpoint"] + "/"),
       ("region", "ru-7"), ("bucket", "pult-custodian-synth-unit"), ("bucket", "pult-custodian-working-"),
       ("bucket", "pult-custodian-working-A"), ("bucket", "pult-custodian-working-a/b"),
       ("bucket", "pult-custodian-working-" + "a" * (64 - len("pult-custodian-working-"))), ("scope", "synthetic"),
       ("scope", "pult-work-"), ("scope", "pult-work-" + "a" * 55), ("scope", "pult-work-a/b"),
       ("generation", NAMESPACE["generation"].upper()), ("generation", "not-a-uuid"),
       ("generation", "00000000-0000-0000-0000-000000000000")])


@pytest.mark.parametrize("key,bad", BAD_NAMESPACES)
def test_shape_agrees_with_real_working_namespace_rejections(key, bad):
    value = profile()
    value["namespace"][key] = bad
    with pytest.raises((gate.Refused, ValueError, TypeError, AttributeError)):
        gate.shape(value)
    with pytest.raises(storage.NamespaceError):
        storage.WorkingNamespace(**value["namespace"])


@pytest.mark.parametrize("scope", ["pult-work-a", "pult-work-A_z-9", "pult-work-" + "a" * 54])
@pytest.mark.parametrize("bucket", ["pult-custodian-working-a", "pult-custodian-working-" + "a" * (63 - len("pult-custodian-working-"))])
def test_shape_agrees_with_real_namespace_accepted_boundaries(monkeypatch, scope, bucket):
    monkeypatch.setattr(gate, "Path", PurePosixPath)
    value = profile()
    value["namespace"].update(scope=scope, bucket=bucket)
    assert gate.shape(value) == PurePosixPath("/release")
    ns = storage.WorkingNamespace(**value["namespace"])
    assert ns.location.endswith(f"/pult/erasure-v2/{scope}/{NAMESPACE['generation']}/events/")


def synthetic_wheels(first=None):
    bodies = {}
    for index in range(7):
        stream = io.BytesIO()
        with zipfile.ZipFile(stream, "w") as archive:
            rows = first if index == 0 and first is not None else [(f"httpx/unit{index}.py", b"# artificial\n", stat.S_IFREG)]
            for name, body, kind in rows:
                info = zipfile.ZipInfo(name)
                info.external_attr = (kind | 0o444) << 16
                archive.writestr(info, body)
        bodies[f"artificial-{index}.whl"] = stream.getvalue()
    cold = SimpleNamespace(WHEELS={n: gate.digest(b) for n, b in bodies.items()}, _relative=cold_host._relative)
    return cold, bodies


def test_seven_synthetic_wheels_derive_bytes_and_approved_empty_directories():
    cold, bodies = synthetic_wheels([("httpx/empty/", b"", stat.S_IFDIR), ("httpx/__init__.py", b"", stat.S_IFREG)])
    files, directories = gate.wheel_inventory(cold, bodies)
    assert files["httpx/__init__.py"] == b""
    assert set(files) == {"httpx/__init__.py", *(f"httpx/unit{i}.py" for i in range(1, 7))}
    assert directories == {"httpx", "httpx/empty"}
    assert len(cold_host.WHEELS) == 7


@pytest.mark.parametrize("mutation", ["missing", "extra", "tamper", "type", "empty", "archive_size"])
def test_wheel_set_and_exact_archive_digest_fail_before_zip(monkeypatch, mutation):
    cold, bodies = synthetic_wheels()
    if mutation == "missing":
        bodies.pop(next(iter(bodies)))
    elif mutation == "extra":
        bodies["extra.whl"] = b"extra"
    else:
        bodies[next(iter(bodies))] = {"tamper": b"altered", "type": bytearray(b"x"), "empty": b"", "archive_size": b"x" * (2 * 1024 * 1024 + 1)}[mutation]
    calls = []
    def opened(*args, **kwargs):
        calls.append(1)
        raise InjectedFailure("ZIP should not open")
    monkeypatch.setattr(gate.zipfile, "ZipFile", opened)
    with pytest.raises(gate.Refused):
        gate.wheel_inventory(cold, bodies)
    assert calls == []


@pytest.mark.parametrize("name,kind", [
    ("../escape.py", stat.S_IFREG), ("/absolute.py", stat.S_IFREG), ("pkg//x.py", stat.S_IFREG),
    ("pkg/./x.py", stat.S_IFREG), ("pkg/../x.py", stat.S_IFREG), ("pkg\\x.py", stat.S_IFREG),
    ("C:/x.py", stat.S_IFREG), ("httpx/hook.pth", stat.S_IFREG), ("httpx/x.pyc", stat.S_IFREG),
    ("httpx/x.cpython-312.so", stat.S_IFREG), ("httpx/link.py", stat.S_IFLNK),
    ("httpx/fifo.py", stat.S_IFIFO), ("httpx/socket.py", stat.S_IFSOCK),
])
def test_wheel_member_paths_links_special_files_and_native_code_refused(monkeypatch, name, kind):
    cold, bodies = synthetic_wheels([(name, b"x", kind)])
    reads = []
    original = zipfile.ZipFile
    class Archive(original):
        def infolist(self):
            rows = super().infolist()
            if "\\" in name:
                # ZipInfo normalizes Windows separators. Model the raw Linux
                # member name explicitly rather than claiming that is OS proof.
                rows[0].filename = name
            return rows
        def read(self, item, *args, **kwargs):
            reads.append(item.filename)
            return super().read(item, *args, **kwargs)
    monkeypatch.setattr(gate.zipfile, "ZipFile", Archive)
    with pytest.raises((gate.Refused, cold_host.ColdRefused)):
        gate.wheel_inventory(cold, bodies)
    assert reads == []


def test_duplicate_dependency_member_across_archives_is_not_last_writer_wins():
    cold, bodies = synthetic_wheels([("httpx/unit1.py", b"first", stat.S_IFREG)])
    with pytest.raises(gate.Refused):
        gate.wheel_inventory(cold, bodies)


@pytest.mark.parametrize("case,expected_reads", [("encrypted", 0), ("member_size", 0), ("length", 1), ("count", 5000), ("total", 8)])
def test_archive_metadata_bounds_with_independent_read_counters(monkeypatch, case, expected_reads):
    cold, bodies = synthetic_wheels()
    count = 5001 if case == "count" else 9 if case == "total" else 1
    size = 8 * 1024 * 1024 if case == "total" else 8 * 1024 * 1024 + 1 if case == "member_size" else 0
    items = [SimpleNamespace(filename=f"httpx/m{i}.py", flag_bits=int(case == "encrypted"),
        external_attr=(stat.S_IFREG | 0o444) << 16, file_size=size, is_dir=lambda: False) for i in range(count)]
    counts = Counter()
    chunk = b"x" * size if case == "total" else b"bad-length" if case == "length" else b""
    class Archive:
        def __enter__(self):
            return self
        def __exit__(self, *args):
            counts["close"] += 1
        def infolist(self):
            return items
        def read(self, item):
            counts["read"] += 1
            return chunk
    monkeypatch.setattr(gate.zipfile, "ZipFile", lambda *args: Archive())
    with pytest.raises(gate.Refused):
        gate.wheel_inventory(cold, bodies)
    assert counts == {"read": expected_reads, "close": 1} if expected_reads else counts == {"close": 1}


def test_fully_qualified_module_tables_use_captured_bodies_not_imports():
    blobs = {relative: b"# captured" for relative in PINS}
    modules, namespaces = gate.module_tables(PurePosixPath("/release"), blobs,
        {"httpx/__init__.py": b"# package", "httpx/child.py": b"# child", "httpx/data.txt": b"data"})
    assert set(modules) == set(gate.APP_FILES) | {"httpx", "httpx.child"}
    assert modules["httpx"][2] is True
    assert modules["ops.backup.working_journal_v2"][1] is blobs["ops/backup/working_journal_v2.py"]
    assert {"ops", "ops.backup", "ops.backup.custodian_v2", "ops.backup.custodian_v2.codec", "ops.canary"} <= set(namespaces)


@pytest.mark.parametrize("dependencies", [
    {"httpx.py": b"", "httpx/__init__.py": b""},
    {"httpx.py": b"", "httpx/child.py": b""},
    {"httpx/bad-name.py": b""}, {"foreign/__init__.py": b""},
])
def test_module_fullname_and_module_namespace_collisions_refused(dependencies):
    with pytest.raises(gate.Refused):
        gate.module_tables(PurePosixPath("/release"), {p: b"" for p in PINS}, dependencies)


@pytest.mark.parametrize("case", ["valid", "extra", "missing", "mode", "directory_guard"])
def test_closed_inventory_checks_actual_children_and_approved_directories(monkeypatch, case):
    tree = {"": {"a", "deps", "empty"}, "a": {"file.py"}, "deps": {"pkg"}, "deps/pkg": {"__init__.py"}, "empty": set()}
    if case == "extra":
        tree[""] = tree[""] | {"foreign"}
    if case == "missing":
        tree["a"] = set()
    calls = Counter()
    class Captured:
        directories = {}
        def directory(self, where):
            calls["directory"] += 1
            name = str(where.relative_to(PurePosixPath("/release")))
            name = "" if name == "." else name
            if case == "directory_guard" and name == "a":
                raise gate.Refused("working_profile_refused")
            self.directories[where.parts[1:]] = name
            return (7, 9, stat.S_IFDIR | (0o777 if case == "mode" else 0o755))
    class Scan:
        def __init__(self, name):
            self.name = name
        def __enter__(self):
            calls["scan"] += 1
            return iter(SimpleNamespace(name=n) for n in tree[self.name])
        def __exit__(self, *args):
            calls["scan_close"] += 1
    monkeypatch.setattr(gate, "os", SimpleNamespace(scandir=lambda fd: Scan(fd)))
    captured = Captured()
    def action():
        gate.closed_layout(captured, PurePosixPath("/release"), {"a/file.py", "deps/pkg/__init__.py"}, {"empty"})
    if case == "valid":
        action()
        assert calls == {"directory": 5, "scan": 5, "scan_close": 5}
    else:
        with pytest.raises(gate.Refused):
            action()
        assert calls["directory"] >= 1 and calls["scan"] == calls["scan_close"]


@pytest.fixture
def verify_model(monkeypatch):
    """Model only: no helper exec/import, real capture, clients or filesystem IO."""
    model = SimpleNamespace(count=Counter(), events=[], fail=None, changed=None, unexpected=0)
    spec = profile()
    entry = (ROOT / gate.ENTRY).read_bytes().replace(b"\r\n", b"\n")
    spec["entry_sha256"] = gate.digest(entry)
    raw = gate.canonical(spec)
    model.approval, model.spec = gate.digest(raw), spec
    blobs = {relative: (ROOT / relative).read_bytes().replace(b"\r\n", b"\n") for relative in PINS}
    cold_spec, wheels = synthetic_wheels()
    dependencies = {f"httpx/unit{i}.py": b"# artificial\n" for i in range(7)}
    bodies = {"/approval/profile.json": raw, "/release/" + gate.ENTRY: entry,
              **{"/release/" + p: b for p, b in blobs.items()},
              **{"/release/wheels/" + n: b for n, b in wheels.items()},
              **{"/release/deps/" + n: b for n, b in dependencies.items()}}
    model.bodies = bodies
    def event(name, detail=None):
        model.count[name] += 1
        model.events.append((name, detail))
        if model.fail == name or model.fail == (name, model.count[name]):
            raise InjectedFailure(SECRET)
    class Capture:
        def __enter__(self):
            event("capture_enter")
            return self
        def __exit__(self, *args):
            event("close")
        def take(self, where, maximum):
            event("take", str(where))
            body = bodies[str(where)]
            return body + b"changed" if model.changed == str(where) else body
        def directory(self, where):
            event("directory", str(where))
            return (7, 9, stat.S_IFDIR | 0o755)
        def check(self):
            event("capture_check")
    class ModelSys:
        meta_path = []
        modules = {}
        @property
        def stdout(self):
            return sys.stdout
        @property
        def stderr(self):
            return sys.stderr
    fake_sys = ModelSys()
    model.sys = fake_sys
    class Finder:
        def __init__(self, modules, namespaces):
            event("finder")
            self.owned = {}
            model.finder = self
        def check(self):
            event("finder_check")
        def rollback(self):
            event("rollback")
            if self in fake_sys.meta_path:
                fake_sys.meta_path.remove(self)
            fake_sys.modules.clear()
    def forbidden_authority(*args, **kwargs):
        event("authority")
        raise InjectedFailure(SECRET)
    class CountedNamespace(storage.WorkingNamespace):
        def __init__(self, **kwargs):
            event("namespace")
            super().__init__(**kwargs)
    payload = SimpleNamespace(WorkingNamespace=CountedNamespace, Binding=storage.Binding,
        Head=storage.Head, Event=storage.Event, _validate_credential=forbidden_authority,
        SelectelS3Transport=forbidden_authority, WorkingEventReader=forbidden_authority,
        WorkingEventWriter=forbidden_authority)
    def imported(name):
        event("payload_import", name)
        rows = {"ops.backup.working_journal_v2": payload,
                "ops.backup.custodian_v2.codec.event_codec": storage,
                "ops.backup.erasure_journal_s3": SimpleNamespace(_validate_credential=forbidden_authority),
                "ops.canary.canary": SimpleNamespace(SelectelS3Transport=forbidden_authority)}
        fake_sys.modules.update(rows)
        model.finder.owned.update(rows)
        return payload
    def module(name, file, body):
        key = gate.CAPTURE if "capture" in name else gate.COLD
        if body != blobs[key]:
            model.unexpected += 1
            raise InjectedFailure("incorrect helper body")
        event("helper_exec" if key == gate.CAPTURE else "cold_exec")
        return SimpleNamespace(Capture=Capture) if key == gate.CAPTURE else SimpleNamespace(
            _runtime=lambda: event("cold_runtime"), WHEELS=cold_spec.WHEELS,
            _relative=cold_spec._relative, _Finder=Finder)
    def helper_bytes(file):
        event("helper_open", str(file))
        return blobs[gate.CAPTURE]
    def layout(captured, root, files, directories):
        event("layout")
        expected = set(PINS) | {gate.ENTRY} | {"wheels/" + n for n in wheels} | {"deps/" + n for n in dependencies}
        if files != expected or directories != {"deps/httpx"}:
            model.unexpected += 1
            raise InjectedFailure("incorrect closed inventory")
    class ModelPath(PurePosixPath):
        def absolute(self):
            return self
    monkeypatch.setattr(gate, "Path", ModelPath)
    monkeypatch.setattr(gate, "__file__", "/release/" + gate.ENTRY)
    monkeypatch.setattr(gate, "fresh_interpreter", lambda: event("fresh"))
    monkeypatch.setattr(gate, "runtime", lambda: (64012, 64016))
    monkeypatch.setattr(gate, "os", SimpleNamespace(getgroups=lambda: []))
    monkeypatch.setattr(gate, "_helper_bytes", helper_bytes)
    monkeypatch.setattr(gate, "_module", module)
    monkeypatch.setattr(gate, "closed_layout", layout)
    monkeypatch.setattr(gate, "sys", fake_sys)
    monkeypatch.setattr(gate, "importlib", SimpleNamespace(import_module=imported))
    return model


def test_portable_model_success_has_exact_zero_authority_receipt_and_closed_barriers(verify_model):
    model = verify_model
    receipt = gate.verify_profile("/approval/profile.json", model.approval)
    assert receipt == dict(format="pult-working-profile-preflight-result-v1", status="VERIFIED_OFFLINE_NOT_LAUNCHED",
        profile_sha256=model.approval, source_files_verified=9, dependency_files_verified=7,
        namespace_validated=True, **{flag: False for flag in AUTHORITY_FLAGS})
    assert model.count["helper_open"] == model.count["helper_exec"] == model.count["cold_exec"] == 1
    assert model.count["payload_import"] == model.count["namespace"] == model.count["rollback"] == model.count["close"] == 1
    assert model.count["authority"] == model.unexpected == 0
    assert model.sys.meta_path == [] and model.sys.modules == {}
    first_check = model.events.index(("capture_check", None))
    assert first_check < model.events.index(("cold_exec", None))
    payload_index = model.events.index(("payload_import", "ops.backup.working_journal_v2"))
    assert sum(name == "capture_check" for name, _ in model.events[:payload_index]) == 2
    assert {detail for name, detail in model.events[:payload_index] if name == "take"} == set(model.bodies)
    assert model.events[-1] == ("close", None)


@pytest.mark.parametrize("bad", [None, True, "A" * 64, "a" * 63])
def test_bad_independent_sha_latches_before_any_helper_intake(verify_model, bad):
    model = verify_model
    with pytest.raises(gate.Refused, match="^working_profile_refused$"):
        gate.verify_profile("/approval/profile.json", bad)
    assert model.count["helper_open"] == model.count["helper_exec"] == model.count["take"] == 0
    with pytest.raises(gate.Refused):
        gate.verify_profile("/approval/profile.json", model.approval)
    assert model.count["helper_open"] == 0 and model.count["fresh"] == 1


@pytest.mark.parametrize("violation", ["renamed_entry", "profile_inside_release"])
def test_exact_entry_path_and_external_profile_refused_before_helper(verify_model, monkeypatch, violation):
    model = verify_model
    if violation == "renamed_entry":
        monkeypatch.setattr(gate, "__file__", "/release/ops/backup/renamed.py")
    file = "/release/profile.json" if violation == "profile_inside_release" else "/approval/profile.json"
    with pytest.raises(gate.Refused, match="^working_profile_refused$"):
        gate.verify_profile(file, model.approval)
    assert model.count["fresh"] == 1
    assert model.count["helper_open"] == model.count["helper_exec"] == model.count["take"] == 0
    assert model.count["payload_import"] == model.count["namespace"] == model.count["authority"] == 0


@pytest.mark.parametrize("target", ["approval", *PINS, "entry"])
def test_digest_tamper_refused_before_cold_or_payload_execution(verify_model, target):
    model = verify_model
    model.changed = "/approval/profile.json" if target == "approval" else "/release/" + (gate.ENTRY if target == "entry" else target)
    with pytest.raises(gate.Refused, match="^working_profile_refused$"):
        gate.verify_profile("/approval/profile.json", model.approval)
    assert model.count["cold_exec"] == model.count["payload_import"] == model.count["namespace"] == model.count["authority"] == 0
    assert model.count["helper_exec"] == model.count["close"] == 1
    assert model.unexpected == 0


@pytest.mark.parametrize("failure", ["layout", ("capture_check", 1), ("capture_check", 2),
    ("capture_check", 3), ("capture_check", 4), ("capture_check", 5), "payload_import", "namespace", "rollback", "close"])
def test_model_failure_never_returns_receipt_and_closes_owned_capture(verify_model, failure):
    model = verify_model
    model.fail = failure
    with pytest.raises(gate.Refused) as error:
        gate.verify_profile("/approval/profile.json", model.approval)
    assert str(error.value) == "working_profile_refused" and error.value.__suppress_context__
    assert model.count["close"] == 1 and model.count["authority"] == model.unexpected == 0
    if failure in ("layout", ("capture_check", 1), ("capture_check", 2)):
        assert model.count["payload_import"] == model.count["namespace"] == 0
    else:
        assert model.count["rollback"] == 1
    if failure != "rollback":
        assert model.sys.meta_path == [] and model.sys.modules == {}


@pytest.mark.parametrize("first_succeeds", [True, False])
def test_one_shot_latch_consumes_success_and_failure_before_second_verify(monkeypatch, first_succeeds):
    calls = []
    def verified(*args):
        calls.append(args)
        if not first_succeeds:
            raise InjectedFailure(SECRET)
        return {"portable": True}
    monkeypatch.setattr(gate, "_verify", verified)
    if first_succeeds:
        assert gate.verify_profile("fake", "fake") == {"portable": True}
    else:
        with pytest.raises(gate.Refused):
            gate.verify_profile("fake", "fake")
    with pytest.raises(gate.Refused):
        gate.verify_profile("fake", "fake")
    assert calls == [("fake", "fake")]


@pytest.mark.parametrize("args", [[], ["check"], ["serve", "file", "sha"], ["check", "file", "sha", "extra"]])
def test_cli_bad_shape_has_fixed_diagnostic_without_intake(monkeypatch, capsys, args):
    calls = []
    def verify(*values):
        calls.append(values)
        raise InjectedFailure(SECRET)
    monkeypatch.setattr(gate, "verify_profile", verify)
    assert gate.main(args) == 1
    captured = capsys.readouterr()
    assert captured.out == "" and captured.err == "WORKING_PROFILE_REFUSED\n" and calls == []


def test_cli_failure_suppresses_private_exception_and_emits_no_receipt(monkeypatch, capsys):
    calls = []
    def verify(*args):
        calls.append(args)
        raise InjectedFailure(SECRET)
    monkeypatch.setattr(gate, "verify_profile", verify)
    assert gate.main(["check", "file", "sha"]) == 1
    captured = capsys.readouterr()
    assert captured.out == "" and captured.err == "WORKING_PROFILE_REFUSED\n"
    assert calls == [("file", "sha")]


def test_cli_serializes_only_closed_model_receipt(verify_model, capsys):
    model = verify_model
    assert gate.main(["check", "/approval/profile.json", model.approval]) == 0
    captured = capsys.readouterr()
    receipt = json.loads(captured.out)
    assert captured.err == "" and gate.canonical(receipt).decode("ascii") == captured.out
    assert receipt["status"] == "VERIFIED_OFFLINE_NOT_LAUNCHED"
    assert all(receipt[flag] is False for flag in AUTHORITY_FLAGS)
    assert model.count["close"] == model.count["rollback"] == 1 and model.count["authority"] == 0


@pytest.mark.parametrize("groups,accepted", [([], True), ([64016], True), ([64012], False), ([64016, 64012], False)])
def test_actor_requires_profile_ids_and_no_private_runtime_group(monkeypatch, groups, accepted):
    monkeypatch.setattr(gate, "runtime", lambda: (64012, 64016))
    monkeypatch.setattr(gate, "os", SimpleNamespace(getgroups=lambda: groups))
    if accepted:
        gate._actor(profile())
    else:
        with pytest.raises(gate.Refused):
            gate._actor(profile())
    wrong = profile()
    wrong["actor"]["gid"] = 64012
    with pytest.raises(gate.Refused):
        gate._actor(wrong)


@pytest.mark.parametrize("case", ["valid", "sticky", "directory_owner", "directory_acl", "file_mode",
    "hardlink", "file_acl", "replace", "file_change", "hash", "close"])
def test_helper_descriptor_model_hashes_before_return_and_closes_every_owned_fd(monkeypatch, case):
    # These are synthetic descriptors; this does not test OS enforcement of ACLs.
    raw = (ROOT / gate.CAPTURE).read_bytes().replace(b"\r\n", b"\n")
    if case == "hash":
        raw = b"X" + raw[1:]
    counts, opened, infos = Counter(), [], {}
    def open_fd(name, flags, **kwargs):
        fd = len(opened) + 1
        opened.append((fd, name, flags, kwargs))
        is_dir = bool(flags & 1)
        info = dict(st_dev=7, st_ino=fd, st_mode=(stat.S_IFDIR | (0o1777 if case == "sticky" else 0o755))
            if is_dir else stat.S_IFREG | (0o644 if case == "file_mode" else 0o444),
            st_uid=1000 if is_dir and case == "directory_owner" else 0, st_gid=0,
            st_nlink=2 if is_dir or case == "hardlink" else 1,
            st_size=0 if is_dir else len(raw), st_mtime_ns=1, st_ctime_ns=1)
        infos[fd] = info
        counts["open"] += 1
        return fd
    def fstat(fd):
        counts[("fstat", fd)] += 1
        info = dict(infos[fd])
        if case == "file_change" and stat.S_ISREG(info["st_mode"]) and counts[("fstat", fd)] > 1:
            info["st_mtime_ns"] = 2
        return SimpleNamespace(**info)
    def linked(name, **kwargs):
        info = dict(infos[opened[-1][0]])
        if case == "replace":
            info["st_ino"] += 1
        return SimpleNamespace(**info)
    def acl(fd):
        is_dir = stat.S_ISDIR(infos[fd]["st_mode"])
        return ["system.posix_acl_access"] if (case == "directory_acl" and is_dir or case == "file_acl" and not is_dir) else []
    def pread(fd, maximum, offset):
        counts["read"] += 1
        counts["read_contract"] += int(maximum == 150001 and offset == 0)
        return raw
    def close(fd):
        counts[("closed", fd)] += 1
        if case == "close" and fd == opened[-1][0]:
            raise OSError(SECRET)
    monkeypatch.setattr(gate, "os", SimpleNamespace(O_RDONLY=0, O_DIRECTORY=1, O_NOFOLLOW=2,
        O_CLOEXEC=4, O_NONBLOCK=8, open=open_fd, fstat=fstat, stat=linked,
        listxattr=acl, pread=pread, close=close))
    if case in ("valid", "sticky"):
        assert gate._helper_bytes(PurePosixPath("/release/capture.py")) == raw
    else:
        with pytest.raises(gate.Refused):
            gate._helper_bytes(PurePosixPath("/release/capture.py"))
    assert counts["open"] == len(opened)
    assert all(counts[("closed", fd)] == 1 for fd, *_ in opened)
    assert all(flags & 2 and flags & 4 for _, _, flags, _ in opened)
    early = case in ("directory_owner", "directory_acl", "file_mode", "hardlink", "file_acl")
    assert counts["read"] == counts["read_contract"] == int(not early)


@pytest.mark.parametrize("violation", [None, "finder", "path", "duplicate_path", "payload"])
def test_fresh_interpreter_portable_model_refuses_path_and_module_substitution(monkeypatch, violation):
    paths = sorted(gate.STDLIB_PATHS)
    meta = [gate.BuiltinImporter, gate.FrozenImporter, gate.PathFinder]
    modules = {}
    if violation == "finder":
        meta.append(object())
    elif violation == "path":
        paths[0] = "/unapproved/site-packages"
    elif violation == "duplicate_path":
        paths.append(paths[0])
    elif violation == "payload":
        modules["ops.backup.working_journal_v2"] = object()
    calls = []
    monkeypatch.setattr(gate, "runtime", lambda: calls.append("runtime"))
    monkeypatch.setattr(gate, "sys", SimpleNamespace(meta_path=meta, path=paths, modules=modules))
    if violation is None:
        gate.fresh_interpreter()
    else:
        with pytest.raises(gate.Refused):
            gate.fresh_interpreter()
    assert calls == ["runtime"]


@pytest.mark.parametrize("violation", [None, "uid", "gid", "nnp", "threads", "CapEff", "CapPrm", "CapInh", "CapAmb", "CapBnd", "bound"])
def test_runtime_proc_contract_model_checks_all_ids_caps_and_read_bound(monkeypatch, violation):
    fields = {"Uid": "64012 64012 64012 64012", "Gid": "64016 64016 64016 64016",
              "Threads": "1", "NoNewPrivs": "1", **{k: "0" for k in ("CapEff", "CapPrm", "CapInh", "CapAmb", "CapBnd")}}
    if violation in fields:
        fields[violation] = "1"
    elif violation in ("uid", "gid"):
        fields["Uid" if violation == "uid" else "Gid"] = "0 0 0 0"
    elif violation == "nnp":
        fields["NoNewPrivs"] = "0"
    elif violation == "threads":
        fields["Threads"] = "2"
    raw = "".join(f"{k}: {v}\n" for k, v in fields.items()).encode("ascii")
    if violation == "bound":
        raw += b"X" * 65537
    flags = SimpleNamespace(**{k: True for k in ("isolated", "no_site", "no_user_site", "ignore_environment", "safe_path", "dont_write_bytecode")}, optimize=0)
    monkeypatch.setattr(gate, "sys", SimpleNamespace(platform="linux", implementation=SimpleNamespace(name="cpython"),
        version_info=(3, 12), executable="/usr/bin/python3.12", prefix="/usr", base_prefix="/usr", flags=flags, dont_write_bytecode=True))
    monkeypatch.setattr(gate, "os", SimpleNamespace(path=SimpleNamespace(realpath=lambda p: p), listdir=lambda p: ["1"],
        getuid=lambda: 64012, geteuid=lambda: 64012, getgid=lambda: 64016, getegid=lambda: 64016))
    calls = []
    def opened(file, mode):
        calls.append((file, mode))
        return io.BytesIO(raw)
    monkeypatch.setattr(gate, "open", opened, raising=False)
    if violation is None:
        assert gate.runtime() == (64012, 64016)
    else:
        with pytest.raises((gate.Refused, ValueError)):
            gate.runtime()
    assert calls == [("/proc/self/status", "rb")]


@pytest.fixture
def proof_fixture_model(monkeypatch):
    """Exercise the real deletion gate against a marker in a virtual fixture.

    No subprocess, signal, descriptor or filesystem deletion is real here.
    The cleanup flags below are only initialized; the proof APIs own updates.
    """
    model = SimpleNamespace(calls=Counter(), marker=b"ARTIFICIAL_EVIDENCE", exists=True,
                            before_fds={11: (7, 9)}, failure=None)
    monkeypatch.setattr(native_proof, "OWNED_CHILDREN", [])
    monkeypatch.setattr(native_proof, "CHILD_CLEANUP_CONFIRMED", True)
    monkeypatch.setattr(native_proof, "INPUT_CLEANUP_CONFIRMED", True)
    monkeypatch.setattr(native_proof, "CANCELLATION_OBSERVED", False)
    monkeypatch.setattr(native_proof, "CLEANUP_CONFIRMED", False)
    monkeypatch.setattr(native_proof, "FIXTURE_PRESERVED", True)
    class Scope:
        parent = PurePosixPath("/tmp")
        name = "pult-working-profile-portable-model"
        def is_symlink(self):
            return False
        def lstat(self):
            return SimpleNamespace(st_uid=0, st_mode=stat.S_IFDIR | 0o755)
    model.scope = Scope()
    def remove(scope):
        model.calls["delete"] += 1
        if scope is not model.scope:
            model.calls["foreign_delete"] += 1
            raise InjectedFailure("foreign fixture deletion")
        model.marker, model.exists = None, False
    monkeypatch.setattr(native_proof, "Path", PurePosixPath)
    monkeypatch.setattr(native_proof, "identity", lambda scope: (7, 9))
    monkeypatch.setattr(native_proof, "descriptors", lambda: dict(model.before_fds))
    monkeypatch.setattr(native_proof, "shutil", SimpleNamespace(rmtree=remove))
    return model


def assert_fixture_preserved(model):
    # Call the actual gate, not a replacement that returns a desired flag.
    with pytest.raises(native_proof.ProofError):
        native_proof.cleanup_fixture(model.scope, (7, 9), model.before_fds)
    assert model.calls["delete"] == model.calls["foreign_delete"] == 0
    assert model.marker == b"ARTIFICIAL_EVIDENCE" and model.exists is True
    assert native_proof.CLEANUP_CONFIRMED is False
    assert native_proof.FIXTURE_PRESERVED is True


@pytest.fixture
def child_cleanup_model(monkeypatch, proof_fixture_model):
    model = proof_fixture_model
    model.spawn_observations = []
    streams = {}
    class Pipe:
        def __init__(self, name, fd):
            self.name, self.fd, self.closed = name, fd, False
            streams[fd] = self
        def fileno(self):
            return self.fd
        def close(self):
            model.calls[self.name + "_close"] += 1
            if model.failure == self.name + "_close":
                raise OSError(SECRET)
            self.closed = True
    class Process:
        pid = 12345
        def __init__(self):
            self.stdin, self.stdout, self.stderr = Pipe("stdin", 20), Pipe("stdout", 21), Pipe("stderr", 22)
        def poll(self):
            model.calls["poll"] += 1
            return None
        def kill(self):
            model.calls["kill"] += 1
            if model.failure == "kill":
                raise OSError(SECRET)
        def wait(self, timeout):
            model.calls["wait"] += 1
            if model.failure == "wait":
                raise InjectedFailure(SECRET)
            return 0
    def popen(*args, **kwargs):
        model.calls["spawn"] += 1
        row = native_proof.OWNED_CHILDREN[-1]
        model.spawn_observations.append((native_proof.CHILD_CLEANUP_CONFIRMED,
                                         row.spawn_attempted, row.spawn_confirmed, row.process))
        if model.failure in ("spawn", "spawn_cancel"):
            raise KeyboardInterrupt() if model.failure == "spawn_cancel" else OSError(SECRET)
        model.process = Process()
        return model.process
    class Selector:
        def __init__(self):
            self.rows = {}
            model.calls["selector"] += 1
        def register(self, stream, event, name):
            self.rows[name] = stream
        def unregister(self, stream):
            self.rows.pop(stream.name)
        def select(self, timeout):
            model.calls["select"] += 1
            if model.failure != "success":
                raise InjectedFailure("artificial child I/O failure")
            return [(SimpleNamespace(fileobj=stream, data=name), 1) for name, stream in list(self.rows.items())]
        def close(self):
            model.calls["selector_close"] += 1
            if model.failure == "selector_close":
                raise OSError(SECRET)
    def read(fd, maximum):
        model.calls[("read", fd)] += 1
        return b'{"portable":true}\n' if fd == 21 and model.calls[("read", fd)] == 1 else b""
    def signal_child(fd, sig):
        model.calls["signal"] += 1
        if model.failure == "kill":
            raise OSError(SECRET)
    def close_pidfd(fd):
        model.calls["pidfd_close"] += 1
        if model.failure == "pidfd_close":
            raise OSError(SECRET)
    def open_pidfd(pid):
        model.calls["pidfd_open"] += 1
        return 99
    monkeypatch.setattr(native_proof, "subprocess", SimpleNamespace(Popen=popen, DEVNULL=-3, PIPE=-1))
    monkeypatch.setattr(native_proof, "selectors", SimpleNamespace(DefaultSelector=Selector, EVENT_READ=1))
    monkeypatch.setattr(native_proof, "signal", SimpleNamespace(pidfd_send_signal=signal_child, SIGKILL=9))
    monkeypatch.setattr(native_proof, "os", SimpleNamespace(pidfd_open=open_pidfd, close=close_pidfd,
        read=read, set_blocking=lambda fd, value: None, path=SimpleNamespace(lexists=lambda scope: model.exists)))
    monkeypatch.setattr(native_proof, "time", SimpleNamespace(monotonic=lambda: 0))
    return model


@pytest.mark.parametrize("failure", ["spawn", "spawn_cancel"])
def test_native_child_model_latches_ownership_before_popen_and_preserves_fixture(child_cleanup_model, failure):
    model = child_cleanup_model
    model.failure = failure
    with pytest.raises(native_proof.ProofError):
        native_proof.bounded_child(["ARTIFICIAL_CHILD"], {})
    assert model.spawn_observations == [(False, True, False, None)]
    assert model.calls["spawn"] == 1
    assert model.calls["pidfd_open"] == model.calls["wait"] == model.calls["kill"] == 0
    owned = native_proof.OWNED_CHILDREN[0]
    assert owned.cleanup_attempted is True and owned.cleanup_confirmed is False and owned.reaped is False
    assert owned.cancelled is (failure == "spawn_cancel")
    assert native_proof.CANCELLATION_OBSERVED is (failure == "spawn_cancel")
    assert native_proof.CHILD_CLEANUP_CONFIRMED is False
    assert_fixture_preserved(model)


@pytest.mark.parametrize("failure", ["kill", "wait", "stdin_close", "stdout_close", "stderr_close", "selector_close", "pidfd_close"])
def test_native_child_model_attempts_every_cleanup_action_after_independent_failure(child_cleanup_model, failure):
    model = child_cleanup_model
    model.failure = failure
    with pytest.raises(native_proof.ProofError):
        native_proof.bounded_child(["ARTIFICIAL_CHILD"], {})
    assert model.calls["signal"] == model.calls["wait"] == model.calls["selector_close"] == 1
    assert model.calls["kill"] == int(failure == "kill")
    assert [model.calls[n + "_close"] for n in ("stdin", "stdout", "stderr")] == [1, 1, 1]
    assert model.calls["pidfd_close"] == 1
    owned = native_proof.OWNED_CHILDREN[0]
    assert owned.cleanup_failed is True and owned.cleanup_confirmed is False
    assert owned.reaped is (failure != "wait")
    assert native_proof.CHILD_CLEANUP_CONFIRMED is False
    before = model.calls.copy()
    assert native_proof.cleanup_owned_child(owned) is False
    assert model.calls == before  # No second close/signal after uncertain ownership.
    assert_fixture_preserved(model)


def test_native_child_model_success_allows_only_checked_fixture_deletion(child_cleanup_model):
    model = child_cleanup_model
    model.failure = "success"
    assert native_proof.bounded_child(["ARTIFICIAL_CHILD"], {}) == {"portable": True}
    owned = native_proof.OWNED_CHILDREN[0]
    assert owned.reaped is owned.pipes_closed is owned.pidfd_closed is owned.selector_closed is True
    assert owned.cleanup_confirmed is True and native_proof.CHILD_CLEANUP_CONFIRMED is True
    assert model.calls["wait"] == 2 and model.calls["signal"] == model.calls["kill"] == 0
    assert native_proof.cleanup_fixture(model.scope, (7, 9), model.before_fds) is True
    assert model.calls["delete"] == 1 and model.marker is None and model.exists is False
    assert native_proof.CLEANUP_CONFIRMED is True and native_proof.FIXTURE_PRESERVED is False


def test_later_clean_child_does_not_erase_prior_unknown_spawn_ownership(child_cleanup_model):
    model = child_cleanup_model
    model.failure = "spawn"
    with pytest.raises(native_proof.ProofError):
        native_proof.bounded_child(["ARTIFICIAL_CHILD"], {})
    model.failure = "success"
    assert native_proof.bounded_child(["ARTIFICIAL_CHILD"], {}) == {"portable": True}
    assert [row.cleanup_confirmed for row in native_proof.OWNED_CHILDREN] == [False, True]
    assert native_proof.CHILD_CLEANUP_CONFIRMED is False
    assert_fixture_preserved(model)


def test_fixture_deletion_requires_descriptor_snapshot_even_with_no_children(monkeypatch, child_cleanup_model):
    model = child_cleanup_model
    assert native_proof.fixture_cleanup_allowed() is True
    monkeypatch.setattr(native_proof, "descriptors", lambda: {999: "UNRESOLVED_FD"})
    assert_fixture_preserved(model)


@pytest.fixture
def bounded_input_model(monkeypatch, proof_fixture_model):
    model = proof_fixture_model
    model.raw = b"MODEL_INPUT"
    base = dict(st_dev=7, st_ino=9, st_mode=stat.S_IFREG | 0o444, st_uid=0, st_gid=0,
                st_nlink=1, st_size=len(model.raw), st_mtime_ns=1, st_ctime_ns=1)
    def opened(file, flags):
        model.calls["open"] += 1
        model.open_flags = flags
        if model.failure == "open_symlink":
            raise OSError(SECRET)
        if model.failure == "open_cancel":
            raise KeyboardInterrupt()
        return 77
    def info(fd):
        model.calls["fstat"] += 1
        row = dict(base)
        if model.failure in ("fifo", "symlink"):
            row["st_mode"] = stat.S_IFIFO if model.failure == "fifo" else stat.S_IFLNK
        elif model.failure == "oversize":
            row["st_size"] = 65
        elif model.failure == "stamp" and model.calls["fstat"] > 1:
            row["st_ctime_ns"] += 1
        return SimpleNamespace(**row)
    def linked(file, follow_symlinks):
        model.calls["stat"] += 1
        row = dict(base)
        if model.failure == "replacement":
            row["st_ino"] += 1
        model.follow_symlinks = follow_symlinks
        return SimpleNamespace(**row)
    def pread(fd, maximum, offset):
        model.calls["pread"] += 1
        model.read_args = fd, maximum, offset
        return model.raw
    def closed(fd):
        model.calls["close"] += 1
        if model.failure == "close":
            raise OSError(SECRET)
    monkeypatch.setattr(native_proof, "os", SimpleNamespace(O_RDONLY=0, O_NOFOLLOW=1, O_NONBLOCK=2,
        O_CLOEXEC=4, open=opened, fstat=info, pread=pread, stat=linked, close=closed,
        path=SimpleNamespace(lexists=lambda scope: model.exists)))
    return model


@pytest.mark.parametrize("failure", ["fifo", "symlink", "open_symlink", "oversize"])
def test_native_bounded_input_model_refuses_unsafe_input_before_pread(bounded_input_model, failure):
    model = bounded_input_model
    model.failure = failure
    with pytest.raises((native_proof.ProofError, OSError)):
        native_proof.bounded_file("/ARTIFICIAL_INPUT", 64)
    assert model.calls["open"] == 1 and model.open_flags == 7
    assert model.calls["pread"] == model.calls["stat"] == 0
    assert model.calls["fstat"] == model.calls["close"] == int(failure != "open_symlink")
    assert native_proof.INPUT_CLEANUP_CONFIRMED is True  # No unresolved descriptor.
    assert model.calls["delete"] == 0 and model.marker == b"ARTIFICIAL_EVIDENCE"


@pytest.mark.parametrize("failure", [None, "stamp", "replacement"])
def test_native_bounded_input_model_reads_bounded_bytes_and_compares_stamps(bounded_input_model, failure):
    model = bounded_input_model
    model.failure = failure
    if failure is None:
        assert native_proof.bounded_file("/ARTIFICIAL_INPUT", 64) == model.raw
    else:
        with pytest.raises(native_proof.ProofError):
            native_proof.bounded_file("/ARTIFICIAL_INPUT", 64)
    assert model.calls["pread"] == model.calls["stat"] == model.calls["close"] == 1
    assert model.calls["fstat"] == 2 and model.read_args == (77, 65, 0)
    assert model.follow_symlinks is False and native_proof.INPUT_CLEANUP_CONFIRMED is True


@pytest.mark.parametrize("failure", ["close", "open_cancel"])
def test_native_input_cleanup_uncertainty_preserves_fixture_and_cannot_be_refaked(bounded_input_model, failure):
    model = bounded_input_model
    model.failure = failure
    with pytest.raises(OSError if failure == "close" else KeyboardInterrupt):
        native_proof.bounded_file("/ARTIFICIAL_INPUT", 64)
    assert native_proof.INPUT_CLEANUP_CONFIRMED is False
    assert native_proof.CANCELLATION_OBSERVED is (failure == "open_cancel")
    assert model.calls["pread"] == model.calls["close"] == int(failure == "close")
    model.failure = None
    assert native_proof.bounded_file("/ARTIFICIAL_INPUT", 64) == model.raw
    assert native_proof.INPUT_CLEANUP_CONFIRMED is False
    assert native_proof.CANCELLATION_OBSERVED is (failure == "open_cancel")
    assert_fixture_preserved(model)


@pytest.mark.parametrize("maximum", [None, True, 0, -1, 2 * 1024 * 1024 + 1])
def test_native_bounded_input_invalid_limit_refuses_before_open(bounded_input_model, maximum):
    model = bounded_input_model
    with pytest.raises(native_proof.ProofError):
        native_proof.bounded_file("/ARTIFICIAL_INPUT", maximum)
    assert model.calls["open"] == model.calls["pread"] == model.calls["close"] == 0
    assert native_proof.INPUT_CLEANUP_CONFIRMED is True
