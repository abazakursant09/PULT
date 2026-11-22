"""Native synthetic working-runtime stop proof; review before root execution.

The admission release stays exactly sixteen files. New stop code and all trusted
fault hooks live outside it. A forced stop never attests graceful resource close.
EOF/reap/cgroup postcondition injections follow an actual successful execute;
they are labeled uncertainty models, NOT claims of physically broken OS IO.
"""

import hashlib
import json
import os
from pathlib import Path
import re
import selectors
import signal
import socket
import stat
import subprocess
import struct
import sys
import threading
import time
from types import ModuleType
from uuid import uuid4

FORMAT = "pult-working-runtime-stop-native-proof-v1"
PROOF = "ops/backup/tests/working_runtime_stop_proof.py"
STOP = "ops/backup/working_runtime_stop.py"
WORKER = "ops/backup/working_runtime_stop_worker.py"
STOP_SHA = "09b54956e928cfbeb4e31c2d887c29b018e5152a208ee37c3c1b22c548c07d4c"
WORKER_SHA = "56ca8faf66b8454833e709ddb4a9280b2a1cd468705a89ae141e4a138ca7e402"
RUNTIME = "ops/backup/working_runtime_admission.py"
ENV = {"PATH": "/usr/bin:/bin", "LANG": "C.UTF-8"}
FIXED = {
    "fixture": ("ops/backup/tests/working_runtime_admission_proof.py", "bd218358e9a3792248fcc4e0606a50a22c09145c66145f11bd7d6f711bb91d0c"),
    "utilities": ("ops/backup/tests/working_launch_profile_proof.py", "cf9ba8e6d094efbda6bfcde7688d08d2049abe006f79d2b9f936ffb04ea5ac57"),
    "scope": ("ops/backup/tests/custodian_v2/controller/scope_control.py", "d46ee7c30b01c124d88a60f5b846d3cabcaf6b043b5dc2568a26b32615186234"),
    "ci": ("ops/backup/tests/custodian_v2/controller/ci_gate.py", "d30bb8ab0837bffccae10aeb2aa5e8bf891ad9b7923cdcbcd51afe15d865d61b"),
    "condition": ("ops/backup/tests/custodian_v2/stop/verify_receipt.py", "c4cb6627fddc15ded3e51dc571d46a402951d6a20018f102c658a8efb1bd823b"),
}
FROZEN = {
    "runtime": (RUNTIME, "bd3389566f01b456290379bb4d2facfa9a020a87e83d0de9ed09df5b92903e2d"),
    "guard": ("ops/backup/custodian_v2/stop/constructor_stop.py", "5406c5a81498013884ac46d0279219f299db40576357692604fb4f08c251ca8c"),
    "supervisor": ("ops/backup/custodian_v2/controller/supervisor_v2.py", "3d05d22163b11f815b29c222128c4e41987c9e4a83e7b4906cbe63a18941a37e"),
    "core": ("ops/backup/custodian_v2/controller/frozen/admission.py", "961cc3aef5dcfe9d6286b15a2d5c087a33dc1e5dafdc7d5a16f6eded961afea7"),
}
CASES = (
    "custodian_positive", "writer_positive", "freeze_actor", "freeze_controller",
    "term_ignoring_descendant", "kill_controller", "queued_go_death", "sticky_fence_restart",
    "foreign_unit_cleanup_refused", "wrong_sha", "wrong_format", "source_drift", "profile_drift",
    "close_unknown", "cleanup_unknown", "eof_unknown", "reap_unknown", "manager_unknown", "cgroup_unknown",
)
POSITIVE = {"custodian_positive", "writer_positive", "sticky_fence_restart", "foreign_unit_cleanup_refused"}
EARLY = {"wrong_sha", "wrong_format"}
NO_CONTROLLER_RECEIPT = {"kill_controller", "queued_go_death", "freeze_controller"}
POSTCONDITION_INJECTIONS = {"eof_unknown", "reap_unknown", "cgroup_unknown"}
PARENT_ONLY_FAULTS = POSTCONDITION_INJECTIONS | {"manager_unknown", "cleanup_unknown"}
FAULT_REACHED = PARENT_ONLY_FAULTS | {"close_unknown", "source_drift", "profile_drift", "freeze_actor",
    "freeze_controller", "kill_controller", "queued_go_death", "term_ignoring_descendant", "foreign_unit_cleanup_refused"}
WHEEL_PINS = {
    "anyio-4.14.2-py3-none-any.whl": "9f505dda5ac9f0c8309b5e8bd445a8c2bf7246f3ce950121e45ea15bc41d1494",
    "certifi-2026.10.19-py3-none-any.whl": "42d35340cc5b9ba78e818ddbff0c5b1c2b779de448a340fd1b989f5fa8670e1b",
    "h11-0.16.0-py3-none-any.whl": "63cf8bbe7522de3bf65932fda1d9c2772064ffb3dae62d55932da54b31cb6c86",
    "httpcore-1.0.9-py3-none-any.whl": "2d400746a40668fc9dec9810239072b40b4484b640a8c38fd654a024c7a1bf55",
    "httpx-0.28.1-py3-none-any.whl": "d909fcccc110f8c7faf814ca82a9a4d816bc5a6dbfea25d6591d6985b8ba59ad",
    "idna-3.18-py3-none-any.whl": "7f952cbe720b688055e3f87de14f5c3e5fdaa8bc3928985c4077ca689de849a2",
    "typing_extensions-4.16.0-py3-none-any.whl": "481caa481374e813c1b176ada14e97f1f67a4539ce9cfeb3f350d78d6370c2e8",
}
FLAGS = ("operation_admitted", "launch_admitted", "identity_enrollment_admitted",
         "journal_dispatch_admitted", "purge_or_restore_admitted", "remote_io_admitted")
RESULTS = []


class ProofError(Exception):
    pass


class ApprovalPending(ProofError):
    pass


def need(value):
    if not value:
        raise ProofError("working_runtime_stop_proof_refused")


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


def canonical(value):
    return (json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True,
                       allow_nan=False) + "\n").encode("ascii")


def initial_read(file, maximum=600000):
    """Bounded bootstrap read, before the accepted helper executes."""
    fd = os.open(file, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC)
    try:
        before = os.fstat(fd)
        need(stat.S_ISREG(before.st_mode) and 0 < before.st_size <= maximum)
        raw = os.pread(fd, maximum + 1, 0)
        fields = ("st_dev", "st_ino", "st_mode", "st_uid", "st_gid", "st_nlink",
                  "st_size", "st_mtime_ns", "st_ctime_ns")
        after, linked = os.fstat(fd), os.stat(file, follow_symlinks=False)
        need(len(raw) == before.st_size and all(getattr(before, k) == getattr(after, k)
                                               == getattr(linked, k) for k in fields))
        return raw
    finally:
        os.close(fd)


def captured(file, wanted, name, assembly=False):
    raw = initial_read(file)
    if assembly:
        raw = raw.replace(b"\r\n", b"\n")
    need(digest(raw) == wanted)
    module = ModuleType(name)
    module.__file__ = str(file)
    exec(compile(raw, str(file), "exec", dont_inherit=True, optimize=0), module.__dict__)
    return module, raw


def inputs(repo):
    modules, blobs = {}, {}
    for role, (relative, wanted) in FIXED.items():
        modules[role], blobs[role] = captured(repo / relative, wanted, "_stop_proof_" + role, True)
    fixture = modules["fixture"]
    helper, utility_raw = fixture.helper_at(repo / FIXED["utilities"][0], assembly=True)
    need(utility_raw == blobs["utilities"] and helper.WHEELS == WHEEL_PINS)
    names = {*fixture.OLD_PINS, *fixture.PARENTS, fixture.ENTRY, fixture.PAYLOAD}
    need(len(names) == 16 and STOP not in names and WORKER not in names)
    raw = {n: helper.bounded_file(repo / n, 600000) for n in names | {STOP, WORKER, PROOF}
           | {v[0] for v in FIXED.values()} | {v[0] for v in FROZEN.values()}}
    if STOP_SHA == "0" * 64:
        raise ApprovalPending("independent_stop_review_pending")
    need(digest(raw[STOP].replace(b"\r\n", b"\n")) == STOP_SHA
         and digest(raw[WORKER].replace(b"\r\n", b"\n")) == WORKER_SHA)
    sources = {n: raw[n].replace(b"\r\n", b"\n") for n in names}
    hashes = {n: digest(body) for n, body in sources.items()}
    need(all(hashes[n] == wanted for n, wanted in (fixture.OLD_PINS | fixture.PARENTS).items()))
    need(hashes[fixture.ENTRY] == fixture.ENTRY_SHA and hashes[fixture.PAYLOAD] == fixture.PAYLOAD_SHA)
    for relative, wanted in FROZEN.values():
        need(digest(raw[relative].replace(b"\r\n", b"\n")) == wanted)
    return modules, blobs, helper, raw, sources


def prepare_case(base, case, modules, blobs, helper, raw, sources, wheels, dependencies, directories):
    """Actual artificial state and credentials exist before worker/controller start."""
    root = base / case
    root.mkdir(mode=0o755)
    root.chmod(0o755)
    fixture = modules["fixture"]
    release = fixture.assemble(root, sources, wheels, dependencies, directories, helper)
    role_case = "writer_positive" if case == "writer_positive" else "custodian_positive"
    role_root, runtime_spec, runtime_file, runtime_sha = fixture.fixture(root, release, sources, helper, role_case)
    code = root / "stop-code"
    helper.write(code / STOP, raw[STOP].replace(b"\r\n", b"\n"))
    helper.write(code / WORKER, raw[WORKER].replace(b"\r\n", b"\n"))
    for name in ("guard", "supervisor", "core"):
        relative, _ = FROZEN[name]
        helper.write(code / (name + ".py"), raw[relative].replace(b"\r\n", b"\n"))
    for name, body in blobs.items():
        helper.write(code / (name + "-proof.py"), body)
    helper.write(code / "native-proof.py", raw[PROOF].replace(b"\r\n", b"\n"))
    for directory, _, _ in os.walk(code):
        Path(directory).chmod(0o755)
    control = root / "control"
    control.mkdir(mode=0o700)
    control.chmod(0o700)
    helper.write(control / "working-runtime-stop.lock", b"", 0o600)
    return dict(root=root, role_root=role_root, code=code, control=control, release=release,
                runtime_spec=runtime_spec, runtime_file=runtime_file, runtime_sha=runtime_sha)


def validate_results(value, expected_inputs=None):
    """Pure closed receipt check, never a native-proof substitute."""
    fields = {"format", "status", "scope", "tests", "skips", "cases", "cleanup_confirmed",
              "source_hashes_before", "source_hashes_after", "repository_byte_hashes_before",
              "repository_byte_hashes_after", "wheel_hashes_before", "wheel_hashes_after",
              "owned_units_empty", "private_test_ipc_used", *FLAGS}
    need(type(value) is dict and set(value) == fields and value["format"] == FORMAT
         and value["status"] == "PASS" and value["scope"] == "ARTIFICIAL_LOCAL_CONSTRUCTION_AND_STOP_ONLY"
         and type(value["tests"]) is int and value["tests"] == len(CASES)
         and type(value["skips"]) is int and value["skips"] == 0
         and all(value[k] is True for k in ("cleanup_confirmed", "owned_units_empty", "private_test_ipc_used"))
         and all(value[k] is False for k in FLAGS))
    for before, after in (("source_hashes_before", "source_hashes_after"),
                          ("repository_byte_hashes_before", "repository_byte_hashes_after")):
        need(type(value[before]) is dict and bool(value[before]) and value[before] == value[after]
             and all(type(k) is str and type(v) is str and re.fullmatch("[0-9a-f]{64}", v)
                     for k, v in value[before].items()))
        if expected_inputs is not None:
            need(set(value[before]) == set(expected_inputs))
    need(type(value["wheel_hashes_before"]) is dict and type(value["wheel_hashes_after"]) is dict
         and value["wheel_hashes_before"] == value["wheel_hashes_after"] == WHEEL_PINS)
    rows = value["cases"]
    need(type(rows) is list and len(rows) == len(CASES))
    for case, row in zip(CASES, rows):
        expected = "CONFIRMED" if case in POSITIVE else "REFUSED" if case in EARLY else "NO_PARENT_RECEIPT" if case in NO_CONTROLLER_RECEIPT else "UNKNOWN"
        need(type(row) is dict and set(row) == {"case", "outcome", "root_status", "held", "state_unchanged",
             "credential_unchanged", "lease_released", "cgroup_empty", "launcher_reaped", "fence_preserved",
             "kernel_restart_refused", "sentinel_untouched", "lineage", "closed_evidence",
             "fault_reached", "postcondition_injection", "deadline_before_cleanup", "descendant_sigkill_confirmed",
             "base_execute_succeeded"}
             and row["case"] == case and row["outcome"] == "PASS" and row["root_status"] == expected)
        for key in ("held", "state_unchanged", "credential_unchanged", "lease_released", "cgroup_empty",
                    "launcher_reaped", "fence_preserved", "kernel_restart_refused", "sentinel_untouched", "closed_evidence",
                    "deadline_before_cleanup", "descendant_sigkill_confirmed", "base_execute_succeeded"):
            need(type(row[key]) is bool)
        need(all(row[k] is True for k in ("state_unchanged", "credential_unchanged", "lease_released", "cgroup_empty", "launcher_reaped")))
        need(row["lineage"] in ("NONE", "ACTUAL_FORK_DESCENDANT"))
        need((row["lineage"] == "ACTUAL_FORK_DESCENDANT") == (case == "term_ignoring_descendant"))
        need(row["held"] is (case not in EARLY | {"queued_go_death"}))
        need(row["fence_preserved"] is (case not in EARLY))
        need(row["kernel_restart_refused"] is (case == "sticky_fence_restart"))
        need(row["sentinel_untouched"] is (case == "foreign_unit_cleanup_refused"))
        need(row["fault_reached"] == (case if case in FAULT_REACHED else None))
        need(row["postcondition_injection"] == (case if case in POSTCONDITION_INJECTIONS else None))
        need(row["deadline_before_cleanup"] is (case in {"freeze_actor", "freeze_controller", "term_ignoring_descendant"}))
        need(row["descendant_sigkill_confirmed"] is (case == "term_ignoring_descendant"))
        if case in POSITIVE | PARENT_ONLY_FAULTS:
            need(row["closed_evidence"] is True)
        if case in POSITIVE | POSTCONDITION_INJECTIONS:
            need(row["base_execute_succeeded"] is True)
    return True


def input_hashes(raw):
    return ({name: digest(body.replace(b"\r\n", b"\n")) for name, body in raw.items()},
            {name: digest(body) for name, body in raw.items()})


def wheel_hashes(directory, helper):
    actual = {name: digest(helper.bounded_file(directory / name, 2 * 1024 * 1024)) for name in WHEEL_PINS}
    need(actual == WHEEL_PINS)
    return actual


def validate_input_evidence(value, before, after, wheels_before, wheels_after):
    """Compare child claims with independently re-read parent inputs, not keys."""
    validate_results(value, set(before))
    normalized_before, raw_before = input_hashes(before)
    normalized_after, raw_after = input_hashes(after)
    need(normalized_before == normalized_after and raw_before == raw_after
         and value["source_hashes_before"] == value["source_hashes_after"] == normalized_before
         and value["repository_byte_hashes_before"] == value["repository_byte_hashes_after"] == raw_before
         and value["wheel_hashes_before"] == value["wheel_hashes_after"] == wheels_before == wheels_after == WHEEL_PINS)
    return True


def unit_state(unit):
    need(type(unit) is str and re.fullmatch(r"pult-call-gate-[0-9a-f]{32}\.service", unit))
    result = subprocess.run(["/usr/bin/systemctl", "show", unit, "--no-pager",
        "--property=LoadState,ActiveState,SubState,MainPID,InvocationID,ControlGroup,Result,ExecMainCode,ExecMainStatus"],
        stdin=subprocess.DEVNULL, capture_output=True, env=ENV, timeout=3, check=True)
    need(len(result.stdout) <= 8192 and not result.stderr)
    return dict(row.split("=", 1) for row in result.stdout.decode("ascii").splitlines() if "=" in row)


def cgroup_empty(unit):
    need(re.fullmatch(r"pult-call-gate-[0-9a-f]{32}\.service", unit))
    group = Path("/sys/fs/cgroup/system.slice") / unit
    try:
        return "populated 0" in (group / "cgroup.events").read_text().splitlines()
    except FileNotFoundError:
        need(not group.exists())
        return True


def load_local(file, name):
    raw = initial_read(file)
    value = ModuleType(name)
    value.__file__ = str(file)
    exec(compile(raw, str(file), "exec", dont_inherit=True, optimize=0), value.__dict__)
    return value


def read_json(file):
    raw = initial_read(file, 32768)
    value = json.loads(raw)
    need(type(value) is dict and canonical(value) == raw)
    return value


def proc_identity(pid):
    base = Path("/proc") / str(pid)
    before = (base / "stat").read_text().rsplit(") ", 1)[1].split()
    group = (base / "cgroup").read_text().strip()
    after = (base / "stat").read_text().rsplit(") ", 1)[1].split()
    need(before[19] == after[19] and after[0] not in ("Z", "X", "x"))
    return dict(pid=pid, parent=int(after[1]), start_ticks=int(after[19]), cgroup=group, phase=after[0])


def ignores_term(pid):
    fields = dict(v.split(":", 1) for v in (Path("/proc") / str(pid) / "status").read_text().splitlines() if ":" in v)
    return bool(int(fields["SigIgn"], 16) & (1 << (signal.SIGTERM - 1)))


def valid_sigkill(record, unit, descendant, boot, now_us):
    """Exact PID1/boot/unit/PID attribution, after this owned child's start."""
    if (type(record) is not dict or record.get("_PID") != "1" or record.get("_BOOT_ID") != boot
            or record.get("UNIT") != unit or type(record.get("MESSAGE")) is not str
            or type(record.get("__MONOTONIC_TIMESTAMP")) is not str
            or not record["__MONOTONIC_TIMESTAMP"].isdigit()):
        return False
    began_us = descendant["start_ticks"] * 1000000 // os.sysconf("SC_CLK_TCK")
    when = int(record["__MONOTONIC_TIMESTAMP"])
    pattern = r"(?:" + re.escape(unit) + r": )?Killing process " + str(descendant["pid"]) + r" \([^\r\n]{1,128}\) with signal SIGKILL\."
    return began_us <= when <= now_us and re.fullmatch(pattern, record["MESSAGE"]) is not None


def sigkill_receipt(unit, descendant):
    boot = Path("/proc/sys/kernel/random/boot_id").read_text().strip().replace("-", "")
    deadline = time.monotonic() + 3
    while time.monotonic() < deadline:
        reply = subprocess.run(["/usr/bin/journalctl", "--no-pager", "-o", "json", "-u", unit],
            stdin=subprocess.DEVNULL, capture_output=True, env=ENV, timeout=2, check=True)
        need(len(reply.stdout) <= 131072)
        if any(valid_sigkill(json.loads(row), unit, descendant, boot, int(time.monotonic() * 1000000))
               for row in reply.stdout.splitlines()):
            return True
        time.sleep(0.05)
    return False  # Missing attribution is a failed proof, never inferred SIGKILL.


class Telemetry:
    """Private fixture IPC only; kernel peer credentials, bounded closed records."""
    def __init__(self, file, output, actor, helper):
        self.file, self.output, self.actor, self.helper = file, output, actor, helper
        self.records, self.errors, self.stop = [], [], threading.Event()
        self.socket = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.socket.settimeout(0.2)
        self.socket.bind(str(file))
        os.chown(file, actor["uid"], actor["gid"])
        file.chmod(0o600)
        self.socket.listen(8)
        helper.write(output, canonical(dict(records=[])))
        self.thread = threading.Thread(target=self.collect)
        self.thread.start()

    def collect(self):
        try:
            while not self.stop.is_set():
                try:
                    peer, _ = self.socket.accept()
                except socket.timeout:
                    continue
                with peer:
                    peer.settimeout(2)
                    pid, uid, gid = struct.unpack("3i", peer.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, 12))
                    need((uid, gid) == (self.actor["uid"], self.actor["gid"]))
                    raw = bytearray()
                    while True:
                        chunk = peer.recv(4096)
                        if not chunk:
                            break
                        raw.extend(chunk)
                        need(len(raw) <= 16384)
                    value = json.loads(raw)
                    need(canonical(value) == raw and type(value) is dict
                         and set(value) == {"phase", "pid", "counts", "snapshot", "descendant", "restored"}
                         and type(value["pid"]) is int and value["pid"] == pid
                         and value["phase"] in {"START", "FIRST_SQL", "FIRST_KEY", "HELD", "RETURN"}
                         and len(self.records) < 16)
                    self.records.append(value)
                    temporary = self.output.with_name("telemetry-next")
                    self.helper.write(temporary, canonical(dict(records=self.records)))
                    os.replace(temporary, self.output)  # Only this separate observation leaf changes.
        except BaseException:
            self.errors.append(True)

    def close(self):
        self.stop.set()
        self.thread.join(3)
        self.socket.close()
        need(not self.thread.is_alive() and not self.errors)


def wait_record(context, phase, pid=None, timeout=2):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        records = read_json(Path(context["telemetry"]))["records"]
        matches = [v for v in records if v["phase"] == phase and (pid is None or v["pid"] == pid)]
        if matches:
            need(len(matches) == 1)
            return matches[0]
        time.sleep(0.01)
    raise ProofError("native_milestone_missing")


def actor_main(args):
    need(len(args) == 3)
    profile_file, approval, context_file = args
    context = read_json(Path(context_file))
    need(context["case"] in CASES and dict(os.environ) == ENV)
    code = Path(context["code"])
    fixture, _ = captured(code / "fixture-proof.py", FIXED["fixture"][1], "_actor_fixed_fixture")
    helper, _ = captured(code / "utilities-proof.py", FIXED["utilities"][1], "_actor_fixed_utilities")
    worker_file = code / WORKER
    worker, _ = captured(worker_file, context["sources"]["worker"]["sha256"], "_native_actual_stop_worker")
    inner = read_json(Path(context["runtime_file"]))
    control = read_json(Path(context["observer_file"]))
    original_read, original_pread = os.read, os.pread
    private_ipc = [False]
    notified = set()

    class Observed(fixture.Observer):
        def audit(self, event, values):
            if private_ipc[0] and event in {"socket.__new__", "socket.connect"}:
                need(values[1] == socket.AF_UNIX if event == "socket.__new__" else values[1] == context["socket"])
                return
            super().audit(event, values)
            if event == "sqlite3.connect/handle" and "sql" not in notified:
                notified.add("sql")
                emit("FIRST_SQL")

        def before_read(self, fd):
            super().before_read(fd)
            if self.counts["credential_read_attempts"] and "key" not in notified:
                notified.add("key")
                emit("FIRST_KEY")

    observer = Observed(inner, control["credential_identity"])
    descendant = [None]
    close_reached = [False]

    def emit(phase, snapshot=None, restored=None):
        value = dict(phase=phase, pid=os.getpid(), counts=dict(observer.counts), snapshot=snapshot,
                     descendant=descendant[0], restored=restored)
        private_ipc[0] = True
        try:
            with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as connection:
                connection.settimeout(2)
                connection.connect(context["socket"])
                connection.sendall(canonical(value))
        finally:
            private_ipc[0] = False

    sys.addaudithook(observer.audit)
    os.read, os.pread = observer.read, observer.pread
    emit("START")
    before, meta, paths = helper.descriptors(), tuple(sys.meta_path), tuple(sys.path)

    def held(owner):
        snapshot = owner.snapshot()
        resources = owner._resources
        need(type(resources._client._client) is sys.modules["httpx"].Client
             and resources._client._client.is_closed is False)
        if context["case"] == "term_ignoring_descendant":
            observer.fixture_fork = True
            try:
                pid = os.fork()
            finally:
                observer.fixture_fork = False
            if pid == 0:
                signal.signal(signal.SIGTERM, signal.SIG_IGN)
                while True:
                    time.sleep(60)  # PID1 owns the fixed 40s/2s whole-cgroup bound.
            deadline = time.monotonic() + 2
            while not ignores_term(pid) and time.monotonic() < deadline:
                time.sleep(0.01)
            need(ignores_term(pid))
            descendant[0] = proc_identity(pid)
            descendant[0]["sigterm_ignored"] = True
            need(descendant[0]["parent"] == os.getpid())
        if context["case"] == "close_unknown":
            original = resources._client.close
            def uncertain_close():
                original()
                close_reached[0] = True
                raise ProofError("trusted_close_uncertainty")
            resources._client.close = uncertain_close
        emit("HELD", snapshot)

    worker.held = held
    try:
        result = worker.main([profile_file, approval])
        names = helper.PAYLOAD | {"working_live_store"}
        restored = dict(fds=helper.descriptors() == before,
                        imports=not any(n.split(".", 1)[0] in names for n in sys.modules)
                        and tuple(sys.meta_path) == meta and tuple(sys.path) == paths,
                        exit_code=result)
        if close_reached[0]:
            restored["fault_reached"] = "close_unknown"
        emit("RETURN", restored=restored)
        return result
    finally:
        os.read, os.pread = original_read, original_pread


def controller_main(args):
    need(len(args) == 1)
    context = read_json(Path(args[0]))
    case, code = context["case"], Path(context["code"])
    need(case in CASES)
    control, _ = captured(code / "scope-proof.py", FIXED["scope"][1], "_controller_fixed_scope")
    fixture, _ = captured(code / "fixture-proof.py", FIXED["fixture"][1], "_controller_fixed_fixture")
    stop, _ = captured(code / STOP, context["sources"]["stop"]["sha256"], "_native_actual_stop")
    print("CONTROLLER_READY " + str(os.getpid()), flush=True)
    approval = sys.stdin.readline(66).strip()
    need(re.fullmatch("[0-9a-f]{64}", approval))
    profile = bound = None
    evidence = dict(fault_reached=None, base_execute_succeeded=False, deadline_before_cleanup=False,
                    descendant_sigkill_confirmed=False, held_verified=False)

    def mark(**values):
        evidence.update(values)
        (Path(context["telemetry"]).parent / "controller-evidence.json").write_bytes(canonical(evidence))

    try:
        profile = stop.Profile(context["outer_profile"], approval)

        class NativeBound(stop.Bound):
            def command(self, argv):
                return control.bound_command(argv)

            def _worker_argv(self):
                return ["/usr/bin/python3.12", "-I", "-S", "-B", str(code / "native-proof.py"),
                        "--actor", str(profile.file), profile.sha, str(args[0])]

            def ready(self, unit, pid):
                record = wait_record(context, "START", pid)
                need(record["counts"]["sql_handles"] == record["counts"]["credential_reads"] == 0)
                if case == "queued_go_death":
                    signal.pidfd_send_signal(self.actor_pidfd, signal.SIGSTOP)
                    need(os.write(self.process.stdin.fileno(), stop.GO) == len(stop.GO))
                    mark(fault_reached=case)
                    os.kill(os.getpid(), signal.SIGKILL)

            def held(self, unit, pid):
                record = wait_record(context, "HELD", pid)
                snapshot = record["snapshot"]
                need(snapshot["credentials_opened"] is True and snapshot["client_constructed"] is True
                     and snapshot["state_opened"] is (profile.role == "custodian")
                     and snapshot["lease_held"] is (profile.role == "custodian")
                     and all(snapshot[k] is False for k in FLAGS))
                if profile.role == "custodian":
                    need(fixture.lease_busy(profile.inner["resources"]["lease"]["path"]))
                    need(record["counts"]["sql_handles"] > 0)
                else:
                    need(record["counts"]["sql_handles"] == 0)
                mark(held_verified=True)
                if case == "term_ignoring_descendant":
                    child = record["descendant"]
                    current = proc_identity(child["pid"])
                    need(current["parent"] == pid and current["start_ticks"] == child["start_ticks"]
                         and current["cgroup"] == "0::/system.slice/" + unit
                         and child.get("sigterm_ignored") is True and ignores_term(child["pid"]))
                    self.native_descendant = child
                    mark(fault_reached=case)
                elif case == "freeze_actor":
                    signal.pidfd_send_signal(self.actor_pidfd, signal.SIGSTOP)
                    mark(fault_reached=case)
                    until = time.monotonic() + 47
                    while time.monotonic() < until:
                        if unit_state(unit).get("ActiveState") in ("inactive", "failed"):
                            break
                        time.sleep(0.1)
                    need(unit_state(unit).get("Result") == "timeout" and cgroup_empty(unit))
                    mark(deadline_before_cleanup=True)
                elif case in {"kill_controller", "freeze_controller"}:
                    if case == "freeze_controller":
                        signal.pidfd_send_signal(self.actor_pidfd, signal.SIGSTOP)
                    mark(fault_reached=case)
                    os.kill(os.getpid(), signal.SIGKILL if case == "kill_controller" else signal.SIGSTOP)
                elif case == "source_drift":
                    file = Path(profile.spec["sources"]["worker"]["path"])
                    file.write_bytes(profile.blobs["worker"] + b"\n# trusted native source drift\n")
                    mark(fault_reached=case)
                elif case == "profile_drift":
                    profile.file.write_bytes(profile.raw + b" ")
                    mark(fault_reached=case)
                elif case == "foreign_unit_cleanup_refused":
                    original_unit, original_invocation = self.unit, self.invocation
                    try:
                        self.unit, self.invocation = context["sentinel"], None
                        try:
                            self.cleanup()
                        except (stop.Refused, stop.Unknown):
                            need(unit_state(self.unit).get("ActiveState") == "active")
                            mark(fault_reached=case)
                        else:
                            raise ProofError("foreign_cleanup_was_not_refused")
                    finally:
                        self.unit, self.invocation = original_unit, original_invocation

            def cleanup(self):
                if case == "term_ignoring_descendant":
                    need(evidence["held_verified"] and hasattr(self, "native_descendant"))
                    deadline = time.monotonic() + 3
                    while not cgroup_empty(self.unit) and time.monotonic() < deadline:
                        time.sleep(0.05)
                    need(cgroup_empty(self.unit) and unit_state(self.unit).get("Result") == "timeout")
                    need(sigkill_receipt(self.unit, self.native_descendant))
                    mark(deadline_before_cleanup=True, descendant_sigkill_confirmed=True)
                target = Path(context["telemetry"]).parent / "kernel-before-cleanup.json"
                target.write_bytes(canonical(dict(unit=self.unit, state=unit_state(self.unit))))
                super().cleanup()
                if case == "cleanup_unknown":
                    need(evidence["held_verified"])
                    mark(fault_reached=case)
                    raise ProofError("trusted_cleanup_uncertainty")

            def _execute(self):
                if case == "manager_unknown":
                    original = self.supervisor._state
                    def uncertain(unit):
                        state = original(unit)
                        if unit == self.unit and all(state.get(k) == v for k, v in
                            dict(Result="success", ExecMainCode="1", ExecMainStatus="0", SubState="exited").items()):
                            need(evidence["held_verified"])
                            mark(fault_reached=case)
                            state = dict(state, Result="unconfirmed")
                        return state
                    self.supervisor._state = uncertain
                answer = super()._execute()
                need(evidence["held_verified"])
                mark(base_execute_succeeded=True)
                if case in POSTCONDITION_INJECTIONS:
                    mark(fault_reached=case)
                if case == "eof_unknown": self.eof = False
                elif case == "reap_unknown": self.reaped = False
                elif case == "cgroup_unknown": self.supervisor._empty = lambda unit: False
                return answer

        bound = NativeBound(profile)
        result = bound.run()
        repeat = False
        if case == "sticky_fence_restart" and result["status"] == "WORKING_RUNTIME_STOP_CONFIRMED_NO_OPERATION":
            try:
                NativeBound(stop.Profile(context["outer_profile"], approval)).run()
            except stop.Refused:
                repeat = True
            need(repeat)
        value = dict(outcome="RESULT", result=result, repeat_refused=repeat)
    except (stop.Refused, stop.Unknown):
        value = dict(outcome="REFUSED", result=None, repeat_refused=False)
    sys.stdout.buffer.write(canonical(value))
    return 0 if value["result"] and value["result"]["status"] == "WORKING_RUNTIME_STOP_CONFIRMED_NO_OPERATION" else 1


def controller_command(unit, context, control, code):
    argv = ["/usr/bin/systemd-run", "--quiet", "--pipe", "--no-ask-password", "--expand-environment=no",
            "--unit=" + unit, "--property=Type=exec", "--property=ExitType=main", "--property=RemainAfterExit=no",
            "--property=User=0", "--property=Group=0", "--property=KillMode=control-group", "--property=KillSignal=SIGTERM",
            "--property=SendSIGKILL=yes", "--property=FinalKillSignal=SIGKILL", "--property=TimeoutStopSec=2",
            "--property=Restart=no", "--property=RuntimeMaxSec=70", "--property=PrivateNetwork=yes",
            "--property=NoNewPrivileges=yes", "--property=PrivateTmp=no",
            "--property=ConditionPathExists=!" + str(Path(context["control"]) / "constructor-stop.claim"),
            "--setenv=PATH=/usr/bin:/bin", "--setenv=LANG=C.UTF-8",
            "--setenv=PULT_CONTROLLER_CI_SCOPE=" + os.environ["PULT_CONTROLLER_CI_SCOPE"],
            "/usr/bin/python3.12", "-I", "-S", "-B", str(code / "native-proof.py"),
            "--controller", context["context_file"]]
    return control.bound_command(argv)


class Launcher:
    """Owned systemd-run CLI, explicit EOFs and independent cleanup attempts."""
    def __init__(self, helper, argv):
        self.helper, self.owned = helper, helper.ChildOwnership()
        helper.OWNED_CHILDREN.append(self.owned)
        helper.CHILD_CLEANUP_CONFIRMED = False
        self.buffers, self.ended = {"stdout": bytearray(), "stderr": bytearray()}, set()
        try:
            self.owned.spawn_attempted = True
            self.owned.process = subprocess.Popen(argv, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                stderr=subprocess.PIPE, close_fds=True, start_new_session=True, env=ENV)
            self.owned.spawn_confirmed = True
            self.owned.pidfd_attempted = True
            self.owned.pidfd = os.pidfd_open(self.owned.process.pid)
            self.owned.selector_attempted = True
            self.owned.selector = selectors.DefaultSelector()
            for name in self.buffers:
                stream = getattr(self.owned.process, name)
                os.set_blocking(stream.fileno(), False)
                self.owned.selector.register(stream, selectors.EVENT_READ, name)
        except BaseException:
            self.close()
            raise

    def pump(self):
        for key, _ in self.owned.selector.select(0.05):
            try:
                body = os.read(key.fileobj.fileno(), 4096)
            except BlockingIOError:
                continue
            if body == b"":
                self.ended.add(key.data)
                self.owned.selector.unregister(key.fileobj)
            else:
                self.buffers[key.data].extend(body)
                need(len(self.buffers[key.data]) <= (16384 if key.data == "stdout" else 2048))

    def ready(self):
        deadline = time.monotonic() + 12
        while b"\n" not in self.buffers["stdout"]:
            need(time.monotonic() < deadline and "stdout" not in self.ended)
            self.pump()
        line, rest = bytes(self.buffers["stdout"]).split(b"\n", 1)
        self.buffers["stdout"][:] = rest
        need(re.fullmatch(rb"CONTROLLER_READY [1-9][0-9]*", line))
        return int(line.split()[-1])

    def finish(self, context, root_pidfd):
        deadline, observed_double_stop, killed = time.monotonic() + 65, False, False
        while self.ended != {"stdout", "stderr"}:
            need(time.monotonic() < deadline)
            self.pump()
            if context["case"] == "freeze_controller" and not killed:
                try:
                    root = proc_identity(context["controller_pid"])
                    if root["phase"] in ("T", "t"):
                        need(root["start_ticks"] == context["controller_start_ticks"])
                        claim = read_json(Path(context["control"]) / "constructor-stop.claim")
                        record = wait_record(context, "HELD", timeout=0.2)
                        state = unit_state(claim["unit"])
                        try:
                            actor = proc_identity(record["pid"])
                            if actor["phase"] in ("T", "t"):
                                need(actor["cgroup"] == "0::/system.slice/" + claim["unit"])
                                observed_double_stop = True
                        except FileNotFoundError:
                            pass
                        if state.get("Result") == "timeout" and cgroup_empty(claim["unit"]):
                            need(observed_double_stop and proc_identity(context["controller_pid"])["phase"] in ("T", "t"))
                            context["freeze_deadline_proved"] = True
                            signal.pidfd_send_signal(root_pidfd, signal.SIGKILL)
                            killed = True
                except FileNotFoundError:
                    pass
        self.owned.process.wait(timeout=max(0.01, deadline - time.monotonic()))
        self.owned.reaped = True
        if context["case"] == "freeze_controller":
            need(killed and context.get("freeze_deadline_proved") is True)
        need(not self.buffers["stderr"])
        raw = bytes(self.buffers["stdout"])
        if not raw:
            return None
        value = json.loads(raw)
        need(canonical(value) == raw and type(value) is dict
             and set(value) == {"outcome", "result", "repeat_refused"})
        return value

    def close(self):
        self.helper.cleanup_owned_child(self.owned)
        self.helper.CHILD_CLEANUP_CONFIRMED = all(v.cleanup_confirmed for v in self.helper.OWNED_CHILDREN)
        need(self.owned.cleanup_confirmed)


def profile_value(prepared, controller, helper):
    code = prepared["code"]
    paths = dict(stop=code / STOP, worker=code / WORKER, runtime=prepared["release"] / RUNTIME,
                 guard=code / "guard.py", supervisor=code / "supervisor.py", core=code / "core.py")
    return dict(format="pult-working-runtime-stop-v1", mode="LOCAL_ROLE_CONSTRUCTION_ONLY",
        sources={k: dict(path=str(v), sha256=digest(helper.bounded_file(v, 600000))) for k, v in paths.items()},
        runtime_profile=dict(path=str(prepared["runtime_file"]), sha256=prepared["runtime_sha"]), controller=controller,
        control=dict(root=str(prepared["control"]), identity=helper.identity(prepared["control"]),
                     lock_identity=helper.identity(prepared["control"] / "working-runtime-stop.lock")),
        role=prepared["runtime_spec"]["role"], actor=prepared["runtime_spec"]["actors"][prepared["runtime_spec"]["role"]])


def recorded_sentinel(scope, control):
    unit = "pult-call-gate-" + uuid4().hex + ".service"
    argv = ["/usr/bin/systemd-run", "--quiet", "--unit=" + unit, "--property=Type=exec",
            "--property=RuntimeMaxSec=60", "--property=TimeoutStopSec=2", "--property=KillMode=control-group",
            "--property=SendSIGKILL=yes", "--property=Restart=no", "--property=PrivateNetwork=yes",
            "--property=Description=fixture-owned-foreign-sentinel", "/bin/sleep", "60"]
    result = subprocess.run(control.bound_command(argv), stdin=subprocess.DEVNULL,
                            capture_output=True, env=ENV, timeout=5, check=True)
    need(not result.stdout and not result.stderr and unit_state(unit).get("ActiveState") == "active")
    return unit


def restart_refused(command, unit, control, verifier, fence):
    retry = "pult-call-gate-" + uuid4().hex + ".service"
    args = ["--unit=" + retry if v == "--unit=" + unit else v for v in command]
    # Record independently; use original unwrapped argv to avoid duplicate ledger registration.
    result = subprocess.run(control.bound_command(args), stdin=subprocess.DEVNULL,
                            capture_output=True, env=ENV, timeout=8, check=False)
    need(not result.stdout and cgroup_empty(retry))
    reply = subprocess.run(["/usr/bin/journalctl", "--no-pager", "-o", "json", "-u", retry],
                           capture_output=True, env=ENV, timeout=3, check=True)
    need(len(reply.stdout) <= 131072)
    fields = ("UNIT", "MESSAGE", "MESSAGE_ID", "JOB_TYPE", "JOB_RESULT", "_PID")
    records = [json.loads(v) for v in reply.stdout.splitlines()]
    return any(verifier.valid_condition_receipt({k: v.get(k) for k in fields}, retry, str(fence)) for v in records)


def run_case(prepared, case, modules, helper, scope):
    fixture, control, ci = modules["fixture"], modules["scope"], modules["ci"]
    root, code = prepared["root"], prepared["code"]
    role, actor = prepared["runtime_spec"]["role"], prepared["runtime_spec"]["actors"][prepared["runtime_spec"]["role"]]
    before_records = set(Path(scope["units"]).iterdir())
    observation = root / "observations"
    observation.mkdir(mode=0o755)
    observation.chmod(0o755)
    outer_file, context_file = root / "stop-profile.json", root / "context.json"
    ipc_file = Path(scope["ipc"]) / ("s" + str(CASES.index(case)) + ".sock")
    telemetry_file = observation / "telemetry.json"
    telemetry = None
    sources = profile_value(prepared, dict(unit="pending", invocation_id="0" * 32, boot="pending", pid=1, start_ticks=1), helper)["sources"]
    sentinel = recorded_sentinel(scope, control) if case == "foreign_unit_cleanup_refused" else None
    sentinel_identity = unit_state(sentinel).get("MainPID") if sentinel else None
    context = dict(case=case, code=str(code), control=str(prepared["control"]), context_file=str(context_file),
        sources=sources, runtime_file=str(prepared["runtime_file"]), observer_file=str(prepared["role_root"] / "observer.json"),
        outer_profile=str(outer_file), telemetry=str(telemetry_file), socket=str(ipc_file), sentinel=sentinel)
    helper.write(context_file, canonical(context))
    unit = "pult-call-gate-" + uuid4().hex + ".service"
    launcher, root_pidfd, answer = None, None, None
    fence, raw_fence, profile_raw = prepared["control"] / "constructor-stop.claim", None, None
    state = Path(prepared["runtime_spec"]["resources"]["state"]["path"]) if role == "custodian" else None
    credential = Path(prepared["runtime_spec"]["resources"]["credential"]["path"])
    state_before = digest(helper.bounded_file(state, 2 * 1024 * 1024)) if state else None
    credential_before = digest(helper.bounded_file(credential, 8192))
    command = controller_command(unit, context, control, code)
    try:
        telemetry = Telemetry(ipc_file, telemetry_file, actor, helper)
        launcher = Launcher(helper, command)
        pid = launcher.ready()
        actual = unit_state(unit)
        need(actual.get("MainPID") == str(pid) and actual.get("ActiveState") == "active")
        identity = proc_identity(pid)
        root_pidfd = os.pidfd_open(pid)
        context["controller_pid"] = pid
        context["controller_start_ticks"] = identity["start_ticks"]
        # The file is final before Profile/Capture starts; only observations change afterwards.
        helper.write(context_file, canonical(context))
        controller = dict(unit=unit, invocation_id=actual["InvocationID"], pid=pid,
            start_ticks=identity["start_ticks"], boot=Path("/proc/sys/kernel/random/boot_id").read_text().strip())
        value = profile_value(prepared, controller, helper)
        if case == "wrong_format": value["format"] = "old-offline-profile"
        profile_raw = canonical(value)
        helper.write(outer_file, profile_raw)
        approval = "0" * 64 if case == "wrong_sha" else digest(profile_raw)
        need(os.write(launcher.owned.process.stdin.fileno(), (approval + "\n").encode("ascii")) == 65)
        answer = launcher.finish(context, root_pidfd)
        if fence.exists():
            raw_fence = helper.bounded_file(fence, 4096)
        if case in NO_CONTROLLER_RECEIPT:
            need(answer is None and launcher.owned.process.returncode != 0)
            root_status = "NO_PARENT_RECEIPT"
        elif case in EARLY:
            need(answer["outcome"] == "REFUSED" and answer["result"] is None and not fence.exists())
            root_status = "REFUSED"
        else:
            result = answer["result"]
            need(type(result) is dict and set(result) == set(FLAGS) | {"format", "approval", "role", "unit", "status",
                 "cleanup_confirmed", "cgroup_empty", "pipe_eof_confirmed", "child_reaped", "observation_sha256"}
                 and result["format"] == "pult-working-runtime-stop-result-v1" and result["approval"] == digest(profile_raw)
                 and result["role"] == role and all(result[k] is False for k in FLAGS))
            need(raw_fence is not None and json.loads(raw_fence)["unit"] == result["unit"])
            root_status = "CONFIRMED" if case in POSITIVE else "UNKNOWN"
            need(result["status"] == ("WORKING_RUNTIME_STOP_CONFIRMED_NO_OPERATION" if case in POSITIVE else "UNKNOWN_REQUIRES_REVIEW"))
            if case in POSITIVE:
                need(all(result[k] is True for k in ("cleanup_confirmed", "cgroup_empty", "pipe_eof_confirmed", "child_reaped")))
                terminal = read_json(observation / "kernel-before-cleanup.json")
                need(terminal["unit"] == result["unit"] and all(terminal["state"].get(k) == v for k, v in
                     dict(Result="success", ExecMainCode="1", ExecMainStatus="0", SubState="exited").items()))
            elif case in {"freeze_actor", "term_ignoring_descendant"}:
                terminal = read_json(observation / "kernel-before-cleanup.json")
                need(terminal["unit"] == result["unit"] and terminal["state"].get("Result") == "timeout")
        if sentinel:
            need(unit_state(sentinel).get("ActiveState") == "active" and unit_state(sentinel).get("MainPID") == sentinel_identity)
        restart = False
        if case == "sticky_fence_restart":
            need(answer["repeat_refused"] is True)
            # Strip the three trusted scope properties; bound_command publishes the retry's fresh record.
            plain = [v for v in command if v not in ("--property=ConditionPathExists=" + scope["active"],
                "--property=PartOf=" + scope["parent_unit"], "--property=After=" + scope["parent_unit"])]
            restart = restart_refused(plain, unit, control, modules["condition"], fence)
            need(restart)
        if case in POSITIVE | PARENT_ONLY_FAULTS | {"close_unknown"}:
            wait_record(context, "RETURN")
        evidence = read_json(observation / "controller-evidence.json") if case not in EARLY else dict(
            fault_reached=None, base_execute_succeeded=False, deadline_before_cleanup=False,
            descendant_sigkill_confirmed=False, held_verified=False)
        need(set(evidence) == {"fault_reached", "base_execute_succeeded", "deadline_before_cleanup",
                              "descendant_sigkill_confirmed", "held_verified"})
        if case == "freeze_controller":
            need(context.get("freeze_deadline_proved") is True)
            evidence["deadline_before_cleanup"] = True
        if case in FAULT_REACHED - {"close_unknown"}:
            need(evidence["fault_reached"] == case)
        if case in POSITIVE | POSTCONDITION_INJECTIONS:
            need(evidence["base_execute_succeeded"] is True)
        if case in {"freeze_actor", "freeze_controller", "term_ignoring_descendant"}:
            need(evidence["deadline_before_cleanup"] is True)
        if case == "term_ignoring_descendant":
            need(evidence["descendant_sigkill_confirmed"] is True)
    finally:
        # Stop the only producer before inspecting the durable ledger, even after SIGKILL.
        errors = []
        quiesced = False
        try:
            ci.stop_owned(unit)
            need(cgroup_empty(unit))
            quiesced = True
        except BaseException:
            errors.append(True)
        if launcher is not None:
            try: launcher.close()
            except BaseException: errors.append(True)
        if root_pidfd is not None:
            try: os.close(root_pidfd)
            except BaseException: errors.append(True)
        if quiesced:
            try:
                records = set(Path(scope["units"]).iterdir()) - before_records
                for record in records:
                    try:
                        owned_unit = read_json(record)["unit"]
                        ci.stop_owned(owned_unit)
                        need(cgroup_empty(owned_unit))
                    except BaseException:
                        errors.append(True)
                need(set(Path(scope["units"]).iterdir()) - before_records == records)
            except BaseException:
                errors.append(True)
        if telemetry is not None:
            try: telemetry.close()
            except BaseException: errors.append(True)
        need(not errors)
    need(telemetry is not None)
    records = telemetry.records
    held = [v for v in records if v["phase"] == "HELD"]
    returned = [v for v in records if v["phase"] == "RETURN"]
    need(len(held) == (0 if case in EARLY | {"queued_go_death"} else 1))
    if case == "queued_go_death":
        need(records and all(v["counts"]["sql_handles"] == v["counts"]["credential_read_attempts"] == 0 for v in records))
    if state:
        need(digest(helper.bounded_file(state, 2 * 1024 * 1024)) == state_before)
        need(not fixture.lease_busy(prepared["runtime_spec"]["resources"]["lease"]["path"]))
    need(digest(helper.bounded_file(credential, 8192)) == credential_before)
    if raw_fence is not None:
        need(helper.bounded_file(fence, 4096) == raw_fence)
    closed = bool(returned and returned[-1]["restored"] == dict(fds=True, imports=True, exit_code=0))
    if case in POSITIVE | PARENT_ONLY_FAULTS: need(closed)
    if case == "close_unknown":
        need(returned and returned[-1]["restored"].get("fault_reached") == case)
        evidence["fault_reached"] = case
    return dict(case=case, outcome="PASS", root_status=root_status, held=bool(held), state_unchanged=True,
        credential_unchanged=True, lease_released=True, cgroup_empty=True, launcher_reaped=True,
        fence_preserved=fence.exists(), kernel_restart_refused=restart, sentinel_untouched=bool(sentinel),
        lineage="ACTUAL_FORK_DESCENDANT" if case == "term_ignoring_descendant" else "NONE", closed_evidence=closed,
        fault_reached=evidence["fault_reached"],
        postcondition_injection=case if case in POSTCONDITION_INJECTIONS else None,
        deadline_before_cleanup=evidence["deadline_before_cleanup"],
        descendant_sigkill_confirmed=evidence["descendant_sigkill_confirmed"],
        base_execute_succeeded=evidence["base_execute_succeeded"])


def native_matrix():
    repo = Path(__file__).resolve().parents[3]
    modules, blobs, helper, raw, sources = inputs(repo)
    control = modules["scope"]
    scope = control.read_scope()
    launch = read_json(Path(os.environ[control.ENV]).parent / "working-stop-launch.json")
    wheels_dir = Path(launch["wheels_dir"])
    wheels = {n: helper.bounded_file(wheels_dir / n, 2 * 1024 * 1024) for n in helper.WHEELS}
    wheels_before = {n: digest(v) for n, v in wheels.items()}
    need(wheels_before == WHEEL_PINS)
    dependencies, directories = helper.inventory(wheels)
    baseline = helper.descriptors()
    source_before = {n: digest(v.replace(b"\r\n", b"\n")) for n, v in raw.items()}
    raw_before = {n: digest(v) for n, v in raw.items()}
    base = Path(scope["work"]) / "working-stop-matrix"
    base.mkdir(mode=0o755)
    base.chmod(0o755)
    rows = RESULTS
    for case in CASES:
        prepared = prepare_case(base, case, modules, blobs, helper, raw, sources, wheels, dependencies, directories)
        rows.append(run_case(prepared, case, modules, helper, scope))
        need(helper.descriptors() == baseline and helper.fixture_cleanup_allowed())
    after_raw = {n: helper.bounded_file(repo / n, 600000) for n in raw}
    wheels_after = wheel_hashes(wheels_dir, helper)
    result = dict(format=FORMAT, status="PASS", scope="ARTIFICIAL_LOCAL_CONSTRUCTION_AND_STOP_ONLY",
        tests=len(CASES), skips=0, cases=rows, cleanup_confirmed=True, owned_units_empty=True,
        private_test_ipc_used=True, source_hashes_before=source_before,
        source_hashes_after={n: digest(v.replace(b"\r\n", b"\n")) for n, v in after_raw.items()},
        repository_byte_hashes_before=raw_before, repository_byte_hashes_after={n: digest(v) for n, v in after_raw.items()},
        wheel_hashes_before=wheels_before, wheel_hashes_after=wheels_after,
        **dict.fromkeys(FLAGS, False))
    validate_input_evidence(result, raw, after_raw, wheels_before, wheels_after)
    sys.stdout.buffer.write(canonical(result))
    return 0


def main():
    if sys.argv[1:2] in (["--actor"], ["--controller"]):
        try:
            return actor_main(sys.argv[2:]) if sys.argv[1] == "--actor" else controller_main(sys.argv[2:])
        except BaseException:
            print("WORKING_STOP_NATIVE_ROLE_REFUSED", file=sys.stderr)
            return 1
    try:
        need(sys.platform == "linux" and sys.version_info[:2] == (3, 12) and os.geteuid() == 0
             and sys.flags.isolated and sys.flags.no_site and sys.flags.dont_write_bytecode and not sys.flags.optimize
             and sys.implementation.name == "cpython" and sys.prefix == sys.base_prefix == "/usr"
             and sys.flags.ignore_environment and sys.flags.no_user_site and sys.flags.safe_path
             and os.getgid() == os.getegid() == os.getuid() == 0
             and os.path.realpath(sys.executable) == "/usr/bin/python3.12")
        if sys.argv[1:] != ["--reviewed-by-main"]: need(dict(os.environ) == ENV)
    except BaseException:
        sys.stdout.buffer.write(canonical(dict(format=FORMAT, status="NOT_RUN", tests=0, skips=0)))
        return 2
    if sys.argv[1:] == ["--reviewed-by-main"]:
        try:
            return native_matrix()
        except ApprovalPending:
            sys.stdout.buffer.write(canonical(dict(format=FORMAT, status="NOT_RUN", tests=0, skips=0,
                                                  reason="independent_stop_review_pending")))
            return 2
        except BaseException:
            sys.stdout.buffer.write(canonical(dict(format=FORMAT, status="FAIL", tests=len(RESULTS), skips=0,
                                                  cleanup_confirmed=False)))
            return 1
    try:
        repo = Path(__file__).resolve().parents[3]
        args = sys.argv[1:]
        need(not args or len(args) == 2 and args[0] == "--wheels-dir")
        modules, _, helper, raw, _ = inputs(repo)
        stop = load_local(repo / STOP, "_source_api_preflight")
        need(stop.PINS["worker"] == digest(raw[WORKER].replace(b"\r\n", b"\n")))
        wheels_dir = Path(args[1]) if args else repo / "ops/backup/tests/custodian_v2/wheels"
        wheels_before = wheel_hashes(wheels_dir, helper)
        ci, control = modules["ci"], modules["scope"]
        with ci.create_scope(control) as scope:
            target = Path(os.environ[control.ENV]).parent / "working-stop-launch.json"
            helper.write(target, canonical(dict(wheels_dir=str(wheels_dir))), 0o600)
            result, clean, reason = ci.supervised_run(scope, control, Path(__file__), timeout=850)
        need(clean and not reason and result is not None and result.returncode == 0 and not result.stderr
             and len(result.stdout.encode("ascii")) <= 131072)
        value = json.loads(result.stdout)
        after_raw = {n: helper.bounded_file(repo / n, 600000) for n in raw}
        wheels_after = wheel_hashes(wheels_dir, helper)
        validate_input_evidence(value, raw, after_raw, wheels_before, wheels_after)
        need(not Path(scope["work"]).exists() and not Path(scope["active"]).parent.exists())
        sys.stdout.buffer.write(canonical(value))
        return 0
    except ApprovalPending:
        sys.stdout.buffer.write(canonical(dict(format=FORMAT, status="NOT_RUN", tests=0, skips=0,
                                              reason="independent_stop_review_pending")))
        return 2
    except BaseException:
        sys.stdout.buffer.write(canonical(dict(format=FORMAT, status="FAIL", tests=0, skips=0,
                                              cleanup_confirmed=False)))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
