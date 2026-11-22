"""Portable root-adapter models, not PID1/Lease/provider evidence."""
from pathlib import Path, PurePosixPath
import socket
import sys
from types import ModuleType, SimpleNamespace
from unittest import mock

import pytest

ROOT = Path(__file__).resolve().parents[2]
FILE = ROOT / "ops/backup/working_runtime_stop.py"
gate = ModuleType("_portable_working_runtime_stop")
gate.__file__ = str(FILE)
fcntl_model = ModuleType("fcntl")
fcntl_model.LOCK_EX, fcntl_model.LOCK_NB = 2, 4
with mock.patch.dict(sys.modules, {"fcntl": fcntl_model} if sys.platform == "win32" else {}):
    exec(compile(FILE.read_bytes(), str(FILE), "exec", dont_inherit=True, optimize=0), gate.__dict__)


class Posix(PurePosixPath):
    def absolute(self):
        return self


@pytest.fixture(autouse=True)
def isolate(monkeypatch):
    monkeypatch.setattr(gate, "_ATTEMPTED", False)
    monkeypatch.setattr(gate, "_FAILED", [])
    def denied(*args, **kwargs):
        raise AssertionError("portable model attempted external IO")
    monkeypatch.setattr(socket, "create_connection", denied)
    monkeypatch.setattr(socket.socket, "connect", denied)


def modeled_profile(monkeypatch, role="custodian", mutate=None):
    monkeypatch.setattr(gate, "Path", Posix)
    monkeypatch.setattr(gate, "__file__", "/adapter/ops/backup/working_runtime_stop.py")
    monkeypatch.setattr(gate, "runtime", lambda root: None)
    blobs = {key: ("# artificial " + key).encode() for key in gate.PINS}
    blobs["stop"] = b"# artificial stopped root"
    monkeypatch.setattr(gate, "PINS", {key: gate.digest(raw) for key, raw in blobs.items() if key != "stop"})
    sources = {key: dict(path="/adapter/" + key + ".py", sha256=gate.digest(raw)) for key, raw in blobs.items()}
    sources["stop"]["path"] = gate.__file__
    sources["worker"]["path"] = "/adapter/ops/backup/working_runtime_stop_worker.py"
    sources["runtime"]["path"] = "/release/ops/backup/working_runtime_admission.py"
    actors = dict(custodian=dict(uid=46201, gid=46202), writer=dict(uid=46203, gid=46204))
    resources = dict(credential=dict(path="/private-key/credential.json", identity=dict(device=7, inode=4), access_sha256="b" * 64))
    if role == "custodian":
        resources.update(state=dict(path="/private-state/anchor.sqlite", identity=dict(device=7, inode=5)),
                         lease=dict(path="/private-state/owner.lock", identity=dict(device=7, inode=6)),
                         expected=dict(sequence=0, digest="c" * 64, revision=0))
    inner = dict(format="pult-working-runtime-admission-v1", release=dict(root="/release", identity=dict(device=7, inode=1)),
                 entry_sha256=gate.PINS["runtime"], role=role, actors=actors, namespace={}, peer_ipv4="1.1.1.1", resources=resources)
    spec = dict(format=gate.FORMAT, mode=gate.MODE, sources=sources,
                runtime_profile=dict(path="/approval/runtime.json", sha256=gate.digest(gate.canonical(inner))),
                controller=dict(unit="pult-proof-controller.service", invocation_id="a" * 32,
                                boot="8d3c8e2f-d470-41cb-87ad-50bf698c433b", pid=100, start_ticks=200),
                control=dict(root="/control", identity=dict(device=7, inode=2), lock_identity=dict(device=7, inode=3)),
                role=role, actor=dict(actors[role]))
    if mutate:
        mutate(spec, inner)
    inner_raw = gate.canonical(inner)
    spec["runtime_profile"]["sha256"] = gate.digest(inner_raw)
    raw = gate.canonical(spec)
    data = {row["path"]: blobs[key] for key, row in spec["sources"].items() if key in blobs}
    data["/approval/runtime.json"] = inner_raw
    data["/approval/stop.json"] = raw
    reads = []
    def read(file, *args, **kwargs):
        reads.append(str(file))
        return data[str(file)]
    monkeypatch.setattr(gate, "root_bytes", read)
    return raw, data, reads


@pytest.mark.parametrize("role", ["custodian", "writer"])
def test_exact_separate_role_profile_reads_no_private_resources(monkeypatch, role):
    raw, _, reads = modeled_profile(monkeypatch, role)
    profile = gate.Profile("/approval/stop.json", gate.digest(raw))
    assert profile.role == role and profile.check() is True
    assert profile.state == (Posix("/private-state") if role == "custodian" else None)
    assert all(not name.startswith(("/private-", "/control")) for name in reads)


@pytest.mark.parametrize("bad", ["format", "mode", "extra", "source-extra", "source-sha", "source-path",
    "same-source", "worker-name", "role", "actor-bool", "actor-extra", "actor-mismatch", "same-uid", "same-gid",
    "inner-format", "inner-extra", "inner-entry", "inner-role", "release-extra", "release-inode", "runtime-location",
    "controller-extra", "controller-unit", "controller-invocation", "controller-boot", "controller-pid",
    "control-extra", "control-inode", "lock-inode", "resource-extra", "state-key-overlap", "control-state-overlap",
    "source-in-control", "profile-in-release", "adapter-in-release"])
def test_invalid_outer_profiles_refuse_before_any_resource_open(monkeypatch, bad):
    def mutate(s, inner):
        if bad == "format": s["format"] = "pult-construction-stop-profile-v1"
        elif bad == "mode": s["mode"] = "OPERATIONAL"
        elif bad == "extra": s["dispatch"] = True
        elif bad == "source-extra": s["sources"]["plugin"] = dict(s["sources"]["guard"])
        elif bad == "source-sha": s["sources"]["guard"]["sha256"] = "f" * 64
        elif bad == "source-path": s["sources"]["guard"]["path"] = "/adapter/../guard.py"
        elif bad == "same-source": s["sources"]["guard"]["path"] = s["sources"]["core"]["path"]
        elif bad == "worker-name": s["sources"]["worker"]["path"] = "/adapter/plugin.py"
        elif bad == "role": s["role"] = "admin"
        elif bad == "actor-bool": s["actor"]["uid"] = True
        elif bad == "actor-extra": s["actor"]["groups"] = [0]
        elif bad == "actor-mismatch": s["actor"]["uid"] += 1
        elif bad in ("same-uid", "same-gid"):
            field = bad[5:]; inner["actors"]["writer"][field] = inner["actors"]["custodian"][field]
        elif bad == "inner-format": inner["format"] = "pult-trusted-working-profile-v1"
        elif bad == "inner-extra": inner["approved"] = True
        elif bad == "inner-entry": inner["entry_sha256"] = "e" * 64
        elif bad == "inner-role": inner["role"] = "writer"
        elif bad == "release-extra": inner["release"]["receipt"] = "old"
        elif bad == "release-inode": inner["release"]["identity"]["inode"] = False
        elif bad == "runtime-location": s["sources"]["runtime"]["path"] = "/elsewhere/runtime.py"
        elif bad == "controller-extra": s["controller"]["retry"] = True
        elif bad == "controller-unit": s["controller"]["unit"] = "other.service"
        elif bad == "controller-invocation": s["controller"]["invocation_id"] = "0" * 33
        elif bad == "controller-boot": s["controller"]["boot"] = "not-a-uuid"
        elif bad == "controller-pid": s["controller"]["pid"] = True
        elif bad == "control-extra": s["control"]["remove"] = True
        elif bad == "control-inode": s["control"]["identity"]["inode"] = 0
        elif bad == "lock-inode": s["control"]["lock_identity"]["device"] = False
        elif bad == "resource-extra": inner["resources"]["arbitrary"] = "path"
        elif bad == "state-key-overlap": inner["resources"]["credential"]["path"] = "/private-state/key.json"
        elif bad == "control-state-overlap": s["control"]["root"] = "/private-state/nested"
        elif bad == "source-in-control": s["sources"]["guard"]["path"] = "/control/guard.py"
        elif bad == "profile-in-release": s["runtime_profile"]["path"] = "/release/runtime.json"
        elif bad == "adapter-in-release": s["sources"]["guard"]["path"] = "/release/guard.py"
    raw, _, reads = modeled_profile(monkeypatch, mutate=mutate)
    with pytest.raises((gate.Refused, KeyError, ValueError, TypeError)):
        gate.Profile("/approval/stop.json", gate.digest(raw))
    assert all(not name.startswith(("/private-", "/control/constructor")) for name in reads)


@pytest.mark.parametrize("which", ["profile", "inner", "source"])
def test_retained_profile_refuses_drift_without_reloading_code(monkeypatch, which):
    raw, data, _ = modeled_profile(monkeypatch)
    p = gate.Profile("/approval/stop.json", gate.digest(raw))
    file = {"profile": "/approval/stop.json", "inner": "/approval/runtime.json", "source": "/adapter/core.py"}[which]
    data[file] += b" "
    with pytest.raises(gate.Refused): p.check()
    assert p.modules == {}


@pytest.mark.parametrize("raw", [b'{"a":1,"a":2}\n', b'{"a":1}', b'{ "a":1}\n', b'[]\n', b'{"a":NaN}\n'])
def test_documents_are_exact_canonical_objects(raw):
    with pytest.raises((gate.Refused, ValueError)): gate.document(raw)


@pytest.mark.parametrize("raw", ["relative", "/a/../b", "/a//b", "/a/./b", "/a b", "/a\\b", 1])
def test_paths_do_not_normalize_away_unsafe_input(monkeypatch, raw):
    monkeypatch.setattr(gate, "Path", Posix)
    with pytest.raises(gate.Refused): gate.path(raw)


def fake_profile(role="custodian"):
    p = SimpleNamespace(sha="a" * 64, role=role, actor=dict(uid=46201, gid=46202),
                        file=PurePosixPath("/approval/stop.json"), state=PurePosixPath("/private-state") if role == "custodian" else None,
                        control=PurePosixPath("/control"),
                        spec=dict(controller=dict(unit="pult-controller.service"),
                                  sources=dict(worker=dict(path="/adapter/ops/backup/working_runtime_stop_worker.py"))))
    p.root_guard = lambda: supervisor()
    return p


def supervisor():
    return SimpleNamespace(_state=lambda unit: dict(LoadState="not-found"), _empty=lambda unit: True)


@pytest.mark.parametrize("role", ["custodian", "writer"])
def test_fixed_launch_drop_and_independent_pid1_limits(role):
    p = fake_profile(role)
    h = gate.Bound(p)
    h.unit = "pult-call-gate-" + "d" * 32 + ".service"
    props, argv = h._properties(), h._argv()
    assert props["RuntimeMaxSec"] == "40" and props["TimeoutStopSec"] == "2"
    assert props["KillMode"] == "control-group" and props["ExitType"] == "cgroup"
    assert props["KillSignal"] == "SIGTERM" and props["FinalKillSignal"] == "SIGKILL"
    assert props["SendSIGKILL"] == "yes" and props["Restart"] == "no"
    assert all(props[k] == p.spec["controller"]["unit"] for k in ("BindsTo", "After", "PartOf"))
    assert props["ReadWritePaths"] == ("/private-state" if role == "custodian" else "")
    assert props["PrivateNetwork"] == "yes" and props["ProtectControlGroups"] == "yes"
    position = argv.index("/usr/bin/setpriv")
    assert argv[position:position + 8] == ["/usr/bin/setpriv", "--reuid=46201", "--regid=46202", "--clear-groups",
        "--bounding-set=-all", "--inh-caps=-all", "--ambient-caps=-all", "--no-new-privs"]
    assert argv[position + 8:position + 12] == ["/usr/bin/env", "-i", "PATH=/usr/bin:/bin", "LANG=C.UTF-8"]
    assert argv[-7:] == h._worker_argv()
    assert gate.ENV == {"PATH": "/usr/bin:/bin", "LANG": "C.UTF-8"}


def frame(h, status):
    return dict(format=gate.FRAME, approval=h.profile.sha, role=h.profile.role, unit=h.unit,
                pid=123, start_ticks=456, status=status,
                observation_sha256=None if status == "READY" else "e" * 64, **gate.FLAGS)


@pytest.mark.parametrize("status", ["READY", "HELD", "CLOSED"])
def test_exact_nonoperational_frames(status):
    h = gate.Bound(fake_profile()); h.unit = "pult-call-gate-" + "d" * 32 + ".service"
    assert h._frame(gate.canonical(frame(h, status)), status) == frame(h, status)


@pytest.mark.parametrize("bad", ["format", "approval", "role", "unit", "status", "pid-bool", "pid-float", "ticks-bool",
    "extra", "operation_admitted", "remote_io_admitted", "missing", "observation", "old-format", "oversized", "duplicate"])
def test_ambiguous_or_premature_frames_refuse(bad):
    h = gate.Bound(fake_profile()); h.unit = "pult-call-gate-" + "d" * 32 + ".service"
    value = frame(h, "HELD")
    if bad in ("format", "approval", "role", "unit", "status"): value[bad] = "unapproved"
    elif bad == "pid-bool": value["pid"] = True
    elif bad == "pid-float": value["pid"] = 123.0
    elif bad == "ticks-bool": value["start_ticks"] = True
    elif bad == "extra": value["retry"] = True
    elif bad in gate.FLAGS: value[bad] = 0
    elif bad == "missing": del value["unit"]
    elif bad == "observation": value["observation_sha256"] = None
    elif bad == "old-format": value["format"] = "pult-construction-stop-reply-v1"
    raw = gate.canonical(value)
    if bad == "oversized": raw = b" " * 2049
    elif bad == "duplicate": raw = raw[:-2] + b',"pid":123}\n'
    with pytest.raises((gate.Refused, ValueError)): h._frame(raw, "HELD")


def policy(h):
    p = h.profile
    return dict(MainPID="123", InvocationID="f" * 32, Description="PULT working runtime " + p.sha,
                ActiveState="active", SubState="running", User="0", Group="0", BindsTo="pult-controller.service",
                After="pult-controller.service", PartOf="pult-controller.service", RuntimeMaxUSec="40s", TimeoutStopUSec="2s",
                ExitType="cgroup", KillMode="control-group", KillSignal="15", FinalKillSignal="9", SendSIGKILL="yes",
                Restart="no", NRestarts="0", PrivateNetwork="yes", ReadWritePaths="/private-state", ProtectSystem="strict",
                ProtectHome="yes", PrivateTmp="no", PrivateDevices="yes", ProtectControlGroups="yes",
                RestrictAddressFamilies="AF_UNIX", InaccessiblePaths="/dev/shm /dev/mqueue /dev/pts", TasksMax="16",
                MemoryMax="134217728", LimitFSIZE="2097152", UMask="0077", SupplementaryGroups="", NoNewPrivileges="yes",
                CapabilityBoundingSet="cap_setuid cap_setgid cap_setpcap", AmbientCapabilities="cap_setuid")


def setup_kernel(monkeypatch):
    h = gate.Bound(fake_profile()); h.unit = "pult-call-gate-" + "d" * 32 + ".service"
    actual = policy(h)
    h.profile.show = lambda *args: actual
    h.profile.kernel_actor = lambda pid: (456, "0::/system.slice/" + h.unit,
                                         [v.encode() for v in h._worker_argv()], "S")
    monkeypatch.setattr(gate.os, "pidfd_open", lambda pid: 71, raising=False)
    signals = []
    monkeypatch.setattr(gate.signal, "pidfd_send_signal", lambda fd, sig: signals.append((fd, sig)), raising=False)
    return h, actual, signals


def test_actual_kernel_identity_is_required_in_addition_to_systemd_user(monkeypatch):
    h, _, signals = setup_kernel(monkeypatch)
    h._kernel(frame(h, "READY"))
    assert h.invocation == "f" * 32 and h.actor_pidfd == 71 and signals == [(71, 0)]


@pytest.mark.parametrize("field", list(policy(gate.Bound(fake_profile()))))
def test_every_attested_kernel_property_is_enforced(monkeypatch, field):
    h, actual, signals = setup_kernel(monkeypatch)
    actual[field] = "unexpected"
    with pytest.raises(gate.Refused): h._kernel(frame(h, "READY"))
    assert not signals


@pytest.mark.parametrize("bad", ["ticks", "cgroup", "argv", "frozen", "invocation"])
def test_pid_reuse_cgroup_command_and_invocation_substitution_refuse(monkeypatch, bad):
    h, _, _ = setup_kernel(monkeypatch)
    record = [456, "0::/system.slice/" + h.unit, [v.encode() for v in h._worker_argv()], "S"]
    if bad == "ticks": record[0] += 1
    elif bad == "cgroup": record[1] = "0::/other"
    elif bad == "argv": record[2] += [b"--injected"]
    elif bad == "frozen": record[3] = "T"
    else: h.invocation = "0" * 32
    h.profile.kernel_actor = lambda pid: record
    with pytest.raises(gate.Refused): h._kernel(frame(h, "READY"))


@pytest.mark.parametrize("bad", ["description", "invocation", "fence"])
def test_foreign_or_unproven_unit_never_reaches_cleanup_core(bad):
    h = gate.Bound(fake_profile()); h.unit = "pult-call-gate-" + "d" * 32 + ".service"
    h.invocation = "f" * 32
    calls = []
    h._fence_check = lambda: None
    h.profile.show = lambda *args: dict(InvocationID="f" * 32, Description="PULT working runtime " + h.profile.sha)
    h.supervisor._state = lambda unit: dict(LoadState="loaded")
    h.supervisor._core = lambda: SimpleNamespace(Manager=SimpleNamespace(cleanup=lambda bound, unit: calls.append(unit)))
    if bad == "description": h.profile.show = lambda *args: dict(InvocationID="f" * 32, Description="foreign")
    elif bad == "invocation": h.profile.show = lambda *args: dict(InvocationID="0" * 32, Description="PULT working runtime " + h.profile.sha)
    else:
        def unknown(): raise gate.Unknown("artificial fence uncertainty")
        h._fence_check = unknown
    with pytest.raises((gate.Refused, gate.Unknown)): h.cleanup()
    assert calls == []


def modeled_completion(monkeypatch, failure=None):
    h = gate.Bound(fake_profile()); calls = []
    h._guard = lambda: calls.append("guard")
    h._claim = lambda: setattr(h, "unit", "pult-call-gate-" + "d" * 32 + ".service")
    h._fence_check_after_close = lambda: calls.append("fence-remains")
    def execute():
        h.clean = h.eof = h.reaped = True
        h.actor_pidfd, h.pidfd, h.fence, h.lock = 71, 72, 73, 74
        if failure == "execute": raise gate.Unknown("private artificial fault")
        if failure in ("clean", "eof", "reaped"): setattr(h, failure, False)
        return "e" * 64
    h._execute = execute
    h.supervisor._empty = lambda unit: failure != "empty"
    def close(fd):
        calls.append(("close", fd))
        if failure == "fd-close" and fd == 71: raise OSError("private close fault")
    monkeypatch.setattr(gate.os, "close", close)
    return h, calls


def test_completion_selected_only_after_all_independent_descriptor_closes(monkeypatch):
    h, calls = modeled_completion(monkeypatch)
    result = h.run()
    assert result["status"] == "WORKING_RUNTIME_STOP_CONFIRMED_NO_OPERATION"
    assert result["cleanup_confirmed"] and result["cgroup_empty"] and result["pipe_eof_confirmed"] and result["child_reaped"]
    assert calls[-5:] == [("close", 71), ("close", 72), ("close", 73), ("close", 74), "fence-remains"]
    assert all(result[k] is False for k in gate.FLAGS)
    with pytest.raises(gate.Refused): h.run()
    other, _ = modeled_completion(monkeypatch)
    with pytest.raises(gate.Refused): other.run()


@pytest.mark.parametrize("failure", ["execute", "clean", "eof", "reaped", "empty", "fd-close"])
def test_unknown_cleanup_never_returns_success_or_retry(monkeypatch, failure):
    h, calls = modeled_completion(monkeypatch, failure)
    result = h.run()
    assert result["status"] == "UNKNOWN_REQUIRES_REVIEW" and result["observation_sha256"] is None
    assert [item for item in calls if isinstance(item, tuple)] == [("close", 71), ("close", 72), ("close", 73), ("close", 74)]
    assert gate._FAILED == [h]
    with pytest.raises(gate.Refused): h.run()


def test_source_import_uses_no_bytecode_writes_when_interpreter_allows_them(monkeypatch, tmp_path):
    monkeypatch.setattr(gate.sys, "dont_write_bytecode", False)
    before = set(tmp_path.rglob("*"))
    source = b"VALUE = 3\n"
    value = gate.module(tmp_path / "synthetic.py", source, "_captured_only")
    assert value.VALUE == 3 and set(tmp_path.rglob("*")) == before


def test_preparation_failure_has_fixed_private_safe_diagnostic(monkeypatch, capsys):
    def broken(*args, **kwargs): raise ValueError("ARTIFICIAL_PRIVATE_NOT_FOR_LOGS")
    monkeypatch.setattr(gate, "Profile", broken)
    assert gate.main(["/private/approval", "e" * 64]) == 1
    captured = capsys.readouterr()
    assert captured.out == "" and captured.err == "WORKING_RUNTIME_STOP_REFUSED\n"
    assert "ARTIFICIAL_PRIVATE" not in repr(captured)


def execute_model(monkeypatch, fault=None):
    """Execute the real pump/finally on fake pipes/manager, never OS processes."""
    h = gate.Bound(fake_profile()); h.unit = "pult-call-gate-" + "d" * 32 + ".service"
    events, closes, writes = [], [], []
    h._guard = lambda: events.append("guard")
    h._kernel = lambda value: events.append("kernel-" + value["status"])
    h.ready = lambda *args: events.append("ready-hook")
    h.held = lambda *args: events.append("held-hook")
    h._fence_check = lambda: None
    packets = [("stdout", gate.canonical(frame(h, name))) for name in ("READY", "HELD", "CLOSED")]
    if fault == "early-held": packets[0:2] = [("stdout", packets[0][1] + packets[1][1])]
    if fault == "early-closed": packets[1:3] = [("stdout", packets[1][1] + packets[2][1])]
    if fault == "stderr": packets.insert(2, ("stderr", b"PRIVATE_ARTIFICIAL_ERROR"))
    if fault == "extra-closed": packets.append(("stdout", gate.canonical(frame(h, "CLOSED"))))
    if fault == "identity":
        value = frame(h, "HELD"); value["pid"] += 1
        packets[1] = ("stdout", gate.canonical(value))
    if fault == "close-observation":
        value = frame(h, "CLOSED"); value["observation_sha256"] = "f" * 64
        packets[2] = ("stdout", gate.canonical(value))
    packets.extend([("stdout", b""), ("stderr", b"")])
    pending = {}
    delivered_closed = False
    launched = False

    class Pipe:
        def __init__(self, name, fd):
            self.name, self.fd, self.closed = name, fd, False
        def fileno(self): return self.fd
        def close(self):
            closes.append(self.name)
            if fault == self.name + "-close":
                raise OSError("ARTIFICIAL_PRIVATE_CLOSE")
            self.closed = True

    pipes = {name: Pipe(name, fd) for name, fd in (("stdin", 11), ("stdout", 12), ("stderr", 13))}
    class Selector:
        def __init__(self): self.keys = {}
        def register(self, stream, mask, name): self.keys[name] = SimpleNamespace(fileobj=stream, data=name)
        def unregister(self, stream): self.keys.pop(stream.name)
        def get_map(self): return self.keys
        def select(self, timeout):
            nonlocal delivered_closed
            if not packets or fault in ("selector-close", "signal", "wait", "stdin-close", "stdout-close", "stderr-close", "eof") and delivered_closed:
                return []
            name, raw = packets.pop(0)
            pending[pipes[name].fd] = raw
            if b'"status":"CLOSED"' in raw: delivered_closed = True
            return [(self.keys[name], gate.selectors.EVENT_READ)]
        def close(self):
            events.append("selector-close")
            if fault == "selector-close": raise OSError("ARTIFICIAL_SELECTOR_CLOSE")

    class Process:
        pid = 700
        returncode = None
        stdin, stdout, stderr = pipes["stdin"], pipes["stdout"], pipes["stderr"]
        def wait(self, timeout):
            events.append("wait")
            if fault == "wait": raise gate.subprocess.TimeoutExpired("ARTIFICIAL", timeout)
            self.returncode = 0
            return 0
        def poll(self): return self.returncode
        def kill(self): events.append("direct-owned-kill")
    child = Process()
    def start(*args, **kwargs):
        nonlocal launched
        assert kwargs["env"] == gate.ENV and kwargs["close_fds"] is True
        launched = True
        return child
    def state(unit):
        if not launched: return dict(LoadState="not-found")
        return dict(LoadState="loaded", ActiveState="active", SubState="exited" if delivered_closed else "running",
                    Result="success", ExecMainCode="1", ExecMainStatus="0")
    h.supervisor._state = state
    h.cleanup = lambda: events.append("owned-cleanup")
    monotonic = [0.0]
    def now():
        monotonic[0] += 0.3
        return monotonic[0]
    monkeypatch.setattr(gate.time, "monotonic", now)
    monkeypatch.setattr(gate.selectors, "DefaultSelector", Selector)
    monkeypatch.setattr(gate.subprocess, "Popen", start)
    monkeypatch.setattr(gate.os, "pidfd_open", lambda pid: 71, raising=False)
    monkeypatch.setattr(gate.os, "set_blocking", lambda *args: None)
    monkeypatch.setattr(gate.os, "read", lambda fd, maximum: pending.pop(fd))
    def write(fd, raw): writes.append(raw); return len(raw)
    monkeypatch.setattr(gate.os, "write", write)
    def send(fd, sig):
        events.append("owned-pidfd-signal")
        if fault == "signal": raise ProcessLookupError("ARTIFICIAL_EXIT_RACE")
    monkeypatch.setattr(gate.signal, "pidfd_send_signal", send, raising=False)
    return h, child, events, closes, writes


def test_real_pump_model_requires_ready_go_held_close_and_terminal_closure(monkeypatch):
    h, child, events, closes, writes = execute_model(monkeypatch)
    assert h._execute() == "e" * 64
    assert writes == [gate.GO, gate.CLOSE]
    assert h.clean and h.eof and h.reaped and child.returncode == 0
    assert closes.count("stdin") == closes.count("stdout") == closes.count("stderr") == 1
    assert events.index("kernel-READY") < events.index("ready-hook") < events.index("kernel-HELD")


@pytest.mark.parametrize("fault", ["early-held", "early-closed", "stderr", "extra-closed", "identity", "close-observation"])
def test_pump_refuses_premature_extra_or_changed_milestones(monkeypatch, fault):
    h, _, events, closes, writes = execute_model(monkeypatch, fault)
    with pytest.raises((gate.Refused, gate.Unknown)):
        h._execute()
    assert "owned-cleanup" in events
    assert set(closes) == {"stdin", "stdout", "stderr"}
    if fault == "early-held": assert writes == []
    if fault == "early-closed": assert writes == [gate.GO]


@pytest.mark.parametrize("fault", ["selector-close", "signal", "wait", "stdin-close", "stdout-close", "stderr-close", "eof"])
def test_each_cleanup_fault_still_attempts_other_independent_resources_once(monkeypatch, fault):
    h, _, events, closes, _ = execute_model(monkeypatch, fault)
    with pytest.raises((gate.Refused, gate.Unknown)):
        h._execute()
    assert "selector-close" in events and "wait" in events and "owned-cleanup" in events
    assert set(closes) == {"stdin", "stdout", "stderr"}
    assert all(closes.count(name) == 1 for name in ("stdin", "stdout", "stderr"))
    assert h.failure is not None
