"""Eighteen-case native synthetic PG source-envelope proof; never production admission.

Run only on an owned Linux root/PID1/cgroup-v2 test host:
sudo -n -- unshare --net /usr/bin/python3.12 -I -S -B <this file> --wheels-dir ABSOLUTE
The accepted helpers are setup/cleanup only, never admitted worker sources.
"""
import argparse
from dataclasses import asdict
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import struct
import subprocess
import sys
import tempfile
import time
import traceback
from types import ModuleType
from uuid import uuid4

REPO = Path(__file__).resolve().parents[3] if Path(__file__).name != "proof.py" else None
CASES = ("extra_dependency_directory", "authorized", "bad_approval", "source_changed", "wrong_mode", "extra_dependency",
         "changed_native_extension", "profile_replaced_same_bytes",
         "dependency_replaced_same_bytes", "legacy_loader_rejects_new_envelope",
         "shared_tmp_churn", "source_symlink", "source_hardlink", "source_mode", "source_owner", "source_acl",
         "source_mutated_after_load", "dependency_mutated_after_load")
POSTLOAD = ("profile_replaced_same_bytes", "dependency_replaced_same_bytes",
            "source_mutated_after_load", "dependency_mutated_after_load")
HELPERS = {
    "gate": ("ops/backup/tests/operator_pg_lifetime/gate.py",
             "ee69907ff414ae1efd387778754a75d73185795b7f2cb6607e5e09e73eafb7b3"),
    "baseline": ("ops/backup/tests/operator_offline_host_proof.py",
                 "c51993af7ed4fc52dac8391a2a7139152c39fc469e412bc936cc71fc0e1fd0bb"),
    "pg": ("ops/backup/tests/operator_pg_lifetime/pg_fixture.py",
           "95f06b5a1b8b5fe6892ea192c7d2ac6959467b84aecc40f32aeed90b29c403ad"),
}
OLD = ("journal", "anchor", "ipc", "operator", "stop", "supervisor", "core",
       "profile", "host", "bootstrap", "worker")
NEW = {"pg_bootstrap": "ops/backup/operator_pg/bootstrap.py",
       "pg_runtime": "ops/backup/operator_pg/runtime.py"}
LEGACY_BOOTSTRAP = "96141d0e26cfc718e9cb762f689d20cad90f22c815dadec587fcac8c7580d3fa"
FLAGS = dict(identity_enrollment_admitted=False, journal_dispatch_admitted=False,
             purge_or_restore_admitted=False)
ENV = {"PATH": "/usr/bin:/bin", "LANG": "C.UTF-8"}
PYTHON = ["/usr/bin/python3.12", "-I", "-S", "-B"]


class Refused(Exception):
    """Fixed test diagnostics, never OS/SQL/driver/configuration text."""


def need(value, reason):
    if not value:
        raise Refused(reason)


def canonical(value):
    return (json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True,
                       allow_nan=False)+"\n").encode("ascii")


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def identity(path):
    info = path.lstat()
    return dict(device=info.st_dev, inode=info.st_ino)


def capture(path, maximum=150000, protected=False):
    if protected:
        for parent in path.parents:
            info = parent.lstat()
            need(stat.S_ISDIR(info.st_mode) and info.st_uid == 0
                 and (not info.st_mode & 0o022 or info.st_mode & stat.S_ISVTX)
                 and not any(n.startswith("system.posix_acl_")
                             for n in os.listxattr(parent, follow_symlinks=False)), "protected_role_parent")
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC)
    try:
        before = os.fstat(fd)
        need(stat.S_ISREG(before.st_mode) and before.st_nlink == 1
             and 0 <= before.st_size <= maximum, "bounded_regular_source")
        if protected:
            need(before.st_uid == 0 and stat.S_IMODE(before.st_mode) == 0o444
                 and not any(n.startswith("system.posix_acl_") for n in os.listxattr(fd)), "protected_role_file")
        raw = os.pread(fd, maximum+1, 0)
        fields = ("st_dev", "st_ino", "st_uid", "st_gid", "st_mode", "st_nlink",
                  "st_size", "st_mtime_ns", "st_ctime_ns")
        need(len(raw) == before.st_size and all(
            getattr(info, key) == getattr(before, key)
            for info in (os.fstat(fd), path.lstat()) for key in fields), "source_changed_during_capture")
        return raw
    finally:
        os.close(fd)


def module(name, body, file):
    need(name not in sys.modules, "foreign_proof_namespace")
    value = ModuleType(name)
    value.__file__ = str(file)
    sys.modules[name] = value
    exec(compile(body, str(file), "exec", dont_inherit=True, optimize=0), value.__dict__)
    return value


def put(path, body, mode=0o444):
    with path.open("xb") as output:
        os.fchmod(output.fileno(), mode)
        need(output.write(body) == len(body), "fixture_write_incomplete")
        output.flush()
        os.fsync(output.fileno())


def replace_same_bytes(root, target):
    before = identity(target)
    body = capture(target, 12000000, protected=True)
    replacement = root/"replacement.bin"
    put(replacement, body)
    os.replace(replacement, target)
    need(identity(target) != before and capture(target, 12000000, protected=True) == body,
         "same_byte_replacement_not_performed")


def role_root(value):
    root = Path(value)
    need(str(root) == value and root.is_absolute() and root.parent == Path("/tmp")
         and re.fullmatch(r"pult-op-pg-[A-Za-z0-9_-]+", root.name)
         and not root.is_symlink() and Path(__file__) == root/"proof.py", "protected_scope_role")
    info = root.lstat()
    need(stat.S_ISDIR(info.st_mode) and info.st_uid == info.st_gid == 0
         and stat.S_IMODE(info.st_mode) == 0o755, "protected_scope_metadata")
    return root


def protected_bootstrap(root, expected, legacy=False):
    relative = "ops/backup/operator_host/bootstrap.py" if legacy else NEW["pg_bootstrap"]
    path = root/"release"/relative
    body = capture(path, protected=True)
    need(re.fullmatch("[0-9a-f]{64}", expected) and sha(body) == expected, "role_bootstrap_pin")
    return module("_admission_proof_bootstrap", body, path)


def controller(root, case, bootstrap_sha):
    need(os.getuid() == os.geteuid() == 0 and case in CASES, "root_controller_role")
    print("CONTROLLER_READY "+str(os.getpid()), flush=True)
    supplied = sys.stdin.buffer.readline(66)
    need(re.fullmatch(rb"[0-9a-f]{64}\n", supplied), "independent_envelope_approval")
    approval = supplied[:-1].decode("ascii")
    old = case == "legacy_loader_rejects_new_envelope"
    bootstrap = protected_bootstrap(root, LEGACY_BOOTSTRAP if old else bootstrap_sha, old)
    started = time.monotonic()
    def timing(stage):
        target = root/"proof-timing.json"
        raw = canonical(dict(format="pult-pg-test-timing-v1", stage=stage,
                             seconds=round(time.monotonic()-started, 6)))
        fd = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW | os.O_CLOEXEC, 0o600)
        try:
            info = os.fstat(fd)
            need(info.st_uid == 0 and info.st_nlink == 1 and stat.S_IMODE(info.st_mode) == 0o600,
                 "owned_test_timing_file_required")
            need(os.write(fd, raw) == len(raw), "test_timing_write_incomplete")
            os.fsync(fd)
        finally:
            os.close(fd)
    timing("loading")
    input_fds_before = len(os.listdir("/proc/self/fd"))
    loaded = False
    try:
        if old:
            bootstrap.load_profile(root/"envelope.json", approval)
        else:
            admission = bootstrap.load_admission(root/"envelope.json", approval)
            loaded = True
            timing("loaded")
            if case == "profile_replaced_same_bytes":
                replace_same_bytes(root, admission.legacy.file)
            elif case == "dependency_replaced_same_bytes":
                target = next(iter(sorted(p for p in admission.deps.rglob("*") if p.is_file())))
                replace_same_bytes(root, target)
            elif case in ("source_mutated_after_load", "dependency_mutated_after_load"):
                target = Path(admission.spec["sources"]["pg_runtime"]["path"]) if case == "source_mutated_after_load" else next(
                    iter(sorted(admission.deps.rglob("*.so"))))
                original = capture(target, 12000000, protected=True)
                before = target.stat()
                altered = bytes([original[0]^1])+original[1:]
                target.chmod(0o644)
                with target.open("r+b") as changed:
                    need(changed.write(altered) == len(altered), "postload_mutation_incomplete")
                    changed.flush()
                    os.fsync(changed.fileno())
                target.chmod(0o444)
                os.utime(target, ns=(before.st_atime_ns,before.st_mtime_ns))
                need(target.stat().st_ino == before.st_ino and target.stat().st_size == before.st_size
                     and target.stat().st_mtime_ns == before.st_mtime_ns
                     and capture(target,12000000,protected=True) != original, "actual_inplace_preserved_mtime_mutation_missing")
            elif case == "shared_tmp_churn":
                target = Path(admission.spec["sources"]["pg_runtime"]["path"])
                noise = Path("/tmp")/("pult-pg-unrelated-"+uuid4().hex)
                try:
                    with bootstrap.Capture() as observed:
                        original = observed.take(target, 150000)
                        noise.mkdir(mode=0o700)
                        observed.check()
                        noise.rmdir()
                        observed.check()
                        need(capture(target, protected=True) == original, "protected_source_changed_under_churn")
                finally:
                    if noise.exists():
                        need(noise.parent == Path("/tmp") and noise.name.startswith("pult-pg-unrelated-"),
                             "owned_churn_scope_required")
                        noise.rmdir()
                admission.close()
                need(len(os.listdir("/proc/self/fd")) == input_fds_before,
                     "capture_only_source_fds_leaked")
                print(json.dumps(dict(outcome="CAPTURE_CHURN_ACCEPTED", case=case,
                                      loaded=True, run_called=False)), flush=True)
                return
            runtime = admission.runtime()
            timing("runtime_loaded")
    except bootstrap.Refused:
        if loaded:
            admission.close()
        need(len(os.listdir("/proc/self/fd")) == input_fds_before, "refused_source_fds_leaked")
        need(case != "authorized" and loaded == (case in POSTLOAD), "unexpected_refusal_stage")
        print(json.dumps(dict(outcome="REFUSED", case=case, loaded=loaded, run_called=False)), flush=True)
        return
    if case != "authorized" or old:
        if loaded:
            admission.close()
        print(json.dumps(dict(outcome="UNEXPECTED_ADMISSION", case=case,
                              loaded=loaded, run_called=False)), flush=True)
        raise Refused("unsafe_negative_admission")
    # Direct new entry: no subclass, handler injection, fault or payload hook.
    need(sys.gettrace() is None and sys.getprofile() is None, "untraced_direct_entry_required")
    receipt = runtime.run(admission)
    need(len(os.listdir("/proc/self/fd")) == input_fds_before, "root_source_fds_leaked_after_run")
    timing("returned")
    print(json.dumps(receipt, sort_keys=True), flush=True)


def client(root, legacy_sha):
    bootstrap = protected_bootstrap(root, LEGACY_BOOTSTRAP, legacy=True)
    legacy = bootstrap.load_profile(root/"profile.json", legacy_sha)
    core = legacy.operator_module()
    value = core.OperatorReviewClient(legacy.ipc/"review.sock", reviewer_uid=46201).review(
        legacy.spec["synthetic"]["account_id"], legacy.spec["synthetic"]["draft_id"])
    need(type(value) is core.DraftObservation and all(getattr(value, k) is False for k in FLAGS),
         "nonadmitting_observation_required")
    print(json.dumps(dict(kind="EARLY_NONADMITTING_OBSERVATION_NOT_PARENT_RECEIPT",
                          observation=asdict(value)), sort_keys=True), flush=True)


def wait_socket(root, producer):
    path = root/"ipc/review.sock"
    # Include trusted intake/startup plus the unchanged 15s worker lifetime and
    # 2s stop window. This test wait does not extend either PID1 worker limit.
    until = time.monotonic()+30
    while not os.path.lexists(path):
        need(producer.poll() is None and time.monotonic() < until, "direct_worker_socket_missing")
        time.sleep(.025)
    info = path.lstat()
    need(stat.S_ISSOCK(info.st_mode) and info.st_uid == 46201 and info.st_gid == 46202
         and stat.S_IMODE(info.st_mode) == 0o660 and info.st_nlink == 1, "actual_actor_socket_required")


def checked_claim(root, profile, legacy_sha, controller_unit):
    path = root/"control/constructor-stop.claim"
    if not os.path.lexists(path):
        return None
    value = json.loads(capture(path, 4096, protected=True))
    need(type(value) is dict and set(value) == {"format", "approval", "operation", "controller", "unit"}
         and value["format"] == "pult-offline-operator-claim-v1"
         and value["approval"] == legacy_sha and value["operation"] == profile["operation"]
         and value["controller"] == profile["controller"] and value["unit"] != controller_unit
         and type(value["unit"]) is str
         and re.fullmatch(r"pult-call-gate-[0-9a-f]{32}\.service", value["unit"]), "owned_actor_claim_required")
    return value


def no_reader(pg):
    until = time.monotonic()+5
    value = pg.observe()
    while value["reader_backends"]:
        need(time.monotonic() < until, "reader_leak_before_teardown")
        time.sleep(.05)
        value = pg.observe()
    need(value["rows_sha256"] == pg.baseline, "synthetic_application_rows_changed")


def pg_evidence(case, started, verified):
    """Never report a DB observation for an intake-only test without a DB."""
    need(case in CASES and type(started) is bool and type(verified) is bool,
         "exact_pg_evidence_required")
    expected = case == "authorized"
    need(started is expected and verified is expected, "pg_case_evidence_mismatch")
    return dict(pg_fixture_started=expected, no_pg_started_for_intake=not expected,
                reader_backends_absent_before_pg_teardown=True if expected else None,
                application_rows_unchanged=True if expected else None)


def safe_child_locations(raw):
    """Only fixture-owned structural diagnostics, never exception values."""
    records = []
    for line in raw[:16000].splitlines():
        try:
            value = json.loads(line)
        except (ValueError, UnicodeError):
            continue
        if (type(value) is dict and set(value) == {"outcome", "exception_type", "locations"}
                and value["outcome"] == "FAIL" and type(value["exception_type"]) is str
                and re.fullmatch(r"[A-Za-z_]{1,80}", value["exception_type"])
                and type(value["locations"]) is list and len(value["locations"]) <= 30
                and all(type(row) is dict and set(row) == {"file", "line", "function"}
                        and type(row["file"]) is str and re.fullmatch(r"[A-Za-z0-9_.-]{1,100}", row["file"])
                        and type(row["line"]) is int and 0 < row["line"] < 1000000
                        and type(row["function"]) is str and re.fullmatch(r"[A-Za-z0-9_<>]{1,100}", row["function"])
                        for row in value["locations"])):
            records.append(value)
    return records


def worker_failure_records(raw):
    records = []
    for line in raw[:16000].splitlines():
        try:
            value = json.loads(line)
        except (ValueError, UnicodeError):
            continue
        if (type(value) is dict and set(value) == {"format", "stage", "capture", "exception_type"}
                and value["format"] == "pult-synthetic-pg-worker-failure-v1"
                and type(value["stage"]) is str and re.fullmatch(r"[a-z_]{1,80}", value["stage"])
                and value["capture"] in ("none", "shared_tmp_directory", "protected_directory", "protected_file")
                and type(value["exception_type"]) is str
                and re.fullmatch(r"[A-Za-z_]{1,80}", value["exception_type"])):
            records.append(value)
    return records


def run_case(case, gate, baseline, pg_module, manifest, source_rows, bodies, wheels, proof_body):
    temporary = tempfile.TemporaryDirectory(prefix="pult-op-pg-", dir="/tmp")
    root = Path(temporary.name).resolve()
    root.chmod(0o755)
    root_id = identity(root)
    pg = pg_module.PGFixture(root, "admission-"+case)
    unit = "pult-call-gate-"+uuid4().hex+".service"
    description = "PULT PG admission proof "+uuid4().hex
    producer = peer = None
    profile = legacy_sha = envelope_sha = evidence = None
    passed = pg_ready = False
    began = time.monotonic()
    try:
        for name in ("release", "control", "ipc", "wheels"):
            (root/name).mkdir(mode=0o755)
            (root/name).chmod(0o755)
        os.chown(root/"ipc", 46201, 46202)
        (root/"ipc").chmod(0o750)
        gate.put(root/"control/operator-host.lock", b"", 0o600)
        for name, body in bodies.items():
            gate.put(root/"release"/source_rows[name]["path"], body)
        put(root/"proof.py", proof_body)
        gate.install_wheels(root, wheels)
        for name, body in wheels.items():
            put(root/"wheels"/name, body)
        for tree in (root/"release", root/"deps"):
            tree.chmod(0o755)
            for directory in tree.rglob("*"):
                if directory.is_dir():
                    need(not directory.is_symlink(), "owned_release_directory_required")
                    directory.chmod(0o755)
        if case == "authorized":
            pg_config = pg.start("authorized")
            pg_ready = True
        else:
            # These cases must stop before runtime.run(), imports or SQL. The
            # fixture object only generates fake names; never starts Docker/PG.
            pg_config = dict(socket=str(pg.socket), database=pg.database, database_oid=16384,
                reader_role=pg.reader, reader_password=pg.password, application_name=pg.application)
        binding = dict(format="pult-synthetic-pg-binding-v1", fixture_only=True, **pg_config)
        put(root/"binding.json", canonical(binding))
        props = dict(Type="exec", ExitType="main", RemainAfterExit="no", User="0", Group="0",
            KillMode="control-group", KillSignal="SIGTERM", SendSIGKILL="yes", FinalKillSignal="SIGKILL",
            TimeoutStopSec="2", Restart="no", RuntimeMaxSec="45", PrivateNetwork="yes",
            NoNewPrivileges="yes", ConditionPathExists="!"+str(root/"control/constructor-stop.claim"),
            Description=description)
        argv = ["/usr/bin/systemd-run", "--quiet", "--pipe", "--no-ask-password",
                "--expand-environment=no", "--unit="+unit]
        argv += ["--property="+k+"="+v for k, v in sorted(props.items())]
        argv += [*PYTHON, str(root/"proof.py"), "--controller", str(root), case,
                 source_rows["pg_bootstrap"]["sha256"]]
        need(baseline.state(unit).get("LoadState") == "not-found", "controller_collision_never_adopt")
        producer = subprocess.Popen(argv, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=subprocess.PIPE, close_fds=True, env=ENV)
        first = baseline.line(producer, 8)
        view = baseline.state(unit)
        pid = int(view["MainPID"])
        need(first == "CONTROLLER_READY "+str(pid) and view["ActiveState"] == "active",
             "original_controller_identity_required")
        rows = {name: dict(path=str(root/"release"/row["path"]), sha256=row["sha256"])
                for name, row in source_rows.items()}
        profile = dict(format="pult-offline-operator-host-v1", mode="SYNTHETIC_REVIEW_ENDPOINT_ONLY",
            operation=str(uuid4()), release=dict(root=str(root/"release"), identity=identity(root/"release")),
            controller=dict(unit=unit, invocation_id=view["InvocationID"], **baseline.process_identity(pid),
                            boot=Path("/proc/sys/kernel/random/boot_id").read_text().strip()),
            control=dict(root=str(root/"control"), identity=identity(root/"control"),
                         lock_identity=identity(root/"control/operator-host.lock")),
            actor=dict(uid=46201, gid=46202), operator_uid=46202,
            ipc=dict(path=str(root/"ipc"), identity=identity(root/"ipc")),
            synthetic=gate.SYNTHETIC, sources={name: rows[name] for name in OLD})
        legacy_sha = sha(canonical(profile))
        put(root/"profile.json", canonical(profile))
        envelope = dict(format="pult-operator-pg-source-envelope-v1", mode="SYNTHETIC_PG_REVIEW_SOURCE_ONLY",
            operation=profile["operation"], release=profile["release"],
            legacy=dict(path=str(root/"profile.json"), sha256=legacy_sha), sources=rows,
            dependencies=dict(root=str(root/"deps"), identity=identity(root/"deps"),
                              wheels_root=str(root/"wheels"), wheels=manifest["wheels"]),
            binding=dict(path=str(root/"binding.json"), sha256=sha(canonical(binding))))
        if case == "wrong_mode":
            envelope["mode"] = "SYNTHETIC_REVIEW_ENDPOINT_ONLY"
        envelope_sha = sha(canonical(envelope))
        put(root/"envelope.json", canonical(envelope))
        if case == "extra_dependency":
            put(root/"deps/unapproved_extra.py", b"raise RuntimeError('must_not_import')\n")
        elif case == "extra_dependency_directory":
            (root/"deps/unapproved_namespace").mkdir(mode=0o755)
            (root/"deps/unapproved_namespace").chmod(0o755)
        elif case in ("source_changed", "changed_native_extension"):
            target = root/"release"/NEW["pg_runtime"] if case == "source_changed" else next(
                iter(sorted((root/"deps").rglob("*.so"))))
            need(target.is_file() and not target.is_symlink(), "owned_substitution_target_required")
            body = capture(target, 12000000, protected=True)
            target.chmod(0o644)
            with target.open("ab") as altered:
                altered.write(b"\n# unapproved synthetic substitution\n")
                altered.flush()
                os.fsync(altered.fileno())
            target.chmod(0o444)
            need(capture(target, 12000000, protected=True) != body, "preload_substitution_missing")
        elif case in ("source_symlink", "source_hardlink", "source_mode", "source_owner", "source_acl"):
            target = root/"release"/NEW["pg_runtime"]
            if case == "source_symlink":
                alternate = root/"same-source.py"
                put(alternate, capture(target, protected=True))
                target.unlink()  # Only this newly created artificial source copy.
                target.symlink_to(alternate)
            elif case == "source_hardlink":
                os.link(target, root/"extra-hardlink.py")
                need(target.stat().st_nlink == 2, "actual_hardlink_not_created")
            elif case == "source_mode":
                target.chmod(0o644)
            elif case == "source_owner":
                os.chown(target, 46203, 0)
            else:
                entries = ((1,4,0xffffffff), (2,4,46203), (4,4,0xffffffff),
                           (16,4,0xffffffff), (32,4,0xffffffff))
                acl = struct.pack("<I",2)+b"".join(struct.pack("<HHI",*entry) for entry in entries)
                os.setxattr(target, "system.posix_acl_access", acl)
                need("system.posix_acl_access" in os.listxattr(target), "actual_acl_not_created")
        supplied = "0"*64 if case == "bad_approval" else envelope_sha
        producer.stdin.write((supplied+"\n").encode("ascii"))
        producer.stdin.flush()
        if case == "authorized":
            wait_socket(root, producer)
            peer = subprocess.Popen(["/usr/bin/setpriv", "--reuid=46202", "--regid=46202",
                "--clear-groups", "--bounding-set=-all", "--inh-caps=-all", "--ambient-caps=-all",
                "--no-new-privs", *PYTHON, str(root/"proof.py"), "--client", str(root), legacy_sha],
                stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                close_fds=True, env=ENV)
            early_raw, peer_error = peer.communicate(timeout=14)
            need(peer.returncode == 0 and not peer_error and len(early_raw) <= 4096, "authorized_client_failed")
            early = json.loads(early_raw)
            need(early["kind"] == "EARLY_NONADMITTING_OBSERVATION_NOT_PARENT_RECEIPT"
                 and all(early["observation"].get(k) is False for k in FLAGS), "early_observation_not_receipt")
        output, error = producer.communicate(timeout=24)
        if producer.returncode != 0:
            # Only this exact fixture-owned schema may cross the diagnostic
            # boundary. Never echo raw stderr, SQL, binding or exception text.
            try:
                diagnostic = json.loads(output)
            except (ValueError, UnicodeError):
                diagnostic = None
            if diagnostic == dict(outcome="UNEXPECTED_ADMISSION", case=case,
                                  loaded=True, run_called=False):
                print(json.dumps(dict(child=diagnostic), sort_keys=True), file=sys.stderr, flush=True)
            print(json.dumps(dict(case=case, controller_returncode=producer.returncode,
                                  child_locations=safe_child_locations(error)), sort_keys=True),
                  file=sys.stderr, flush=True)
        need(producer.returncode == 0 and not error and len(output) <= 8192, "controller_role_failed")
        result = json.loads(output)
        if case == "authorized":
            claim = checked_claim(root, profile, legacy_sha, unit)
            need(claim is not None and set(result) == {"format", "approval", "legacy_approval",
                    "operation", "unit", "status", "observation_sha256", "cleanup_confirmed",
                    "cgroup_empty", "pipe_eof_confirmed", "child_reaped",
                    "production_pg_source_admission_proved", *FLAGS}
                 and result.get("format") == "pult-operator-pg-source-supervision-v1"
                 and result.get("approval") == envelope_sha and result.get("legacy_approval") == legacy_sha
                 and envelope_sha != legacy_sha and result.get("operation") == profile["operation"]
                 and result.get("unit") == claim["unit"]
                 and result.get("status") == "SYNTHETIC_PG_REVIEW_STOP_CONFIRMED_NO_ADMISSION"
                 and result.get("observation_sha256") == sha(canonical(early["observation"]))
                 and all(result.get(k) is True for k in
                         ("cleanup_confirmed", "cgroup_empty", "pipe_eof_confirmed", "child_reaped"))
                 and all(result.get(k) is False for k in FLAGS)
                 and result.get("production_pg_source_admission_proved") is False
                 and baseline.empty(claim["unit"]), "distinct_outer_approval_or_cleanup_failed")
        elif case == "shared_tmp_churn":
            need(result == dict(outcome="CAPTURE_CHURN_ACCEPTED", case=case, loaded=True, run_called=False)
                 and {p.name for p in (root/"control").iterdir()} == {"operator-host.lock"}
                 and not list((root/"ipc").iterdir()), "churn_proof_created_actor_or_socket")
        else:
            need(result == dict(outcome="REFUSED", case=case, loaded=case in POSTLOAD, run_called=False)
                 and {p.name for p in (root/"control").iterdir()} == {"operator-host.lock"}
                 and not list((root/"ipc").iterdir()), "negative_admission_created_actor_or_socket")
        evidence = dict(case=case, outcome="PASS", outer_receipt=result if case == "authorized" else None,
            rejected_before_run=case not in ("authorized", "shared_tmp_churn"),
            capture_only=case == "shared_tmp_churn", approval=envelope_sha, legacy_approval=legacy_sha,
            production_pg_source_admission_proved=False, **FLAGS)
        passed = True
    finally:
        primary = sys.exc_info()[1]
        try:
            if not passed:
                snapshot = dict(case=case, seconds=round(time.monotonic()-began, 3),
                                controller=baseline.state(unit))
                chain, current = [], primary
                while current is not None and len(chain) < 4:
                    row = dict(exception_type=type(current).__name__, locations=[
                        dict(file=Path(frame.filename).name, line=frame.lineno, function=frame.name)
                        for frame in traceback.extract_tb(current.__traceback__)])
                    if (type(current) in (Refused, gate.Refused, pg_module.Refused)
                            and len(current.args) == 1 and type(current.args[0]) is str
                            and re.fullmatch(r"[a-z0-9_]{1,80}", current.args[0])):
                        row["fixed_code"] = current.args[0]
                    chain.append(row)
                    current = current.__cause__ or current.__context__
                snapshot["primary_failure_chain"] = chain
                timing_file = root/"proof-timing.json"
                if timing_file.exists():
                    try:
                        measured = json.loads(capture(timing_file, 1024))
                    except (ValueError, UnicodeError):
                        measured = None
                    if (type(measured) is dict and set(measured) == {"format", "stage", "seconds"}
                            and measured["format"] == "pult-pg-test-timing-v1"
                            and measured["stage"] in ("loading", "loaded", "runtime_loaded", "returned")
                            and type(measured["seconds"]) in (int, float)):
                        snapshot["trusted_timing"] = measured
                if profile is not None and legacy_sha is not None:
                    claim = checked_claim(root, profile, legacy_sha, unit)
                    if claim is not None:
                        snapshot["actor"] = baseline.state(claim["unit"])
                print(json.dumps(dict(pre_cleanup_failure=snapshot), sort_keys=True),
                      file=sys.stderr, flush=True)
            # Stop the sole unit producer before reading the permanent actor
            # claim. Never address a child by a guessed/prefix-only identity.
            baseline.stop_owned(unit, description)
            claim_path = root/"control/constructor-stop.claim"
            if os.path.lexists(claim_path):
                need(profile is not None and legacy_sha is not None, "unbound_actor_claim_retained")
                claim = checked_claim(root, profile, legacy_sha, unit)
                baseline.stop_owned(claim["unit"], "PULT offline operator "+legacy_sha)
            for child in (peer, producer):
                if child is not None:
                    if child.poll() is None:
                        child.kill()  # Still-owned unreaped direct Popen child.
                    child_output, child_error = child.communicate(timeout=3)
                    if not passed:
                        records = safe_child_locations(child_error)+worker_failure_records(child_error)
                        if records:
                            print(json.dumps(dict(collected_child_diagnostics=records), sort_keys=True),
                                  file=sys.stderr, flush=True)
            if pg_ready:
                if not pg.closed:
                    no_reader(pg)  # Reader/row verdict BEFORE server teardown.
                pg.close()
                need(pg.closed is True and pg.cid is None and pg.probe is None and pg.locker is None,
                     "owned_pg_resources_cleanup_uncertain")
            else:
                if case == "authorized":
                    # start() removes its identified partial fixture itself.
                    # Never forge observed/closed or mask the primary failure.
                    need(not passed and producer is None and peer is None and pg.started,
                         "failed_setup_resources_not_confirmed")
                else:
                    need(not pg.started and pg.pg is None and not pg.observed and not pg.closed,
                         "intake_case_unexpectedly_started_pg")
                need(pg.cid is None and pg.probe is None and pg.locker is None,
                     "unstarted_pg_resources_not_confirmed")
            need(root.parent == Path("/tmp") and re.fullmatch(r"pult-op-pg-[A-Za-z0-9_-]+", root.name)
                 and not root.is_symlink() and identity(root) == root_id, "scope_cleanup_identity_changed")
            if passed:
                temporary.cleanup()
            else:
                temporary._finalizer.detach()
                put(root/"failure-scope.json", canonical(dict(outcome="FAIL", case=case,
                    evidence_root=str(root), scope_identity=root_id, controller_unit=unit,
                    controller_description=description, container_id=pg.cid, probe_id=pg.probe)), 0o600)
                print(json.dumps(dict(outcome="FAIL", case=case, evidence_root=str(root))), file=sys.stderr)
        except BaseException:
            temporary._finalizer.detach()
            if root.exists() and not (root/"failure-scope.json").exists():
                put(root/"failure-scope.json", canonical(dict(outcome="FAIL", case=case,
                    evidence_root=str(root), scope_identity=root_id, controller_unit=unit,
                    controller_description=description, container_id=pg.cid, probe_id=pg.probe)), 0o600)
            print(json.dumps(dict(outcome="FAIL", case=case, evidence_root=str(root))), file=sys.stderr)
            raise
    evidence.update(owned_resources_cleanup_confirmed=True,
                    **pg_evidence(case, pg_ready, pg_ready and pg.closed))
    return evidence


def main():
    # -O and -OO refuse before helper loading, PG setup, or scope creation.
    if sys.flags.optimize:
        print('{"outcome":"NOT_RUN","reason":"optimized_pg_admission_proof_refused"}')
        return 2
    if len(sys.argv) == 5 and sys.argv[1] == "--controller":
        controller(role_root(sys.argv[2]), sys.argv[3], sys.argv[4])
        return 0
    if len(sys.argv) == 4 and sys.argv[1] == "--client":
        client(role_root(sys.argv[2]), sys.argv[3])
        return 0
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--wheels-dir", type=Path, required=True)
    args = parser.parse_args()
    need(REPO is not None, "setup_from_reviewed_source_only")
    snapshots, loaded = {}, {}
    for role, (relative, pin) in HELPERS.items():
        file = REPO/relative
        raw = capture(file)
        snapshots[str(file)] = sha(raw)
        body = raw.replace(b"\r\n", b"\n")
        need(sha(body) == pin, "accepted_setup_helper_pin_changed")
        loaded[role] = module("_pg_admission_setup_"+role, body, file)
    gate, baseline, pg_module = (loaded[k] for k in ("gate", "baseline", "pg"))
    gate.preflight()
    manifest, captured = gate.sources()
    need(set(captured) == {*OLD, "policy", "reviewer", "baseline_helper"}
         and sha(captured["baseline_helper"]) == HELPERS["baseline"][1]
         and pg_module.IMAGE == manifest["postgres_image"], "exact_accepted_setup_inventory")
    bodies = {k: v for k, v in captured.items() if k != "baseline_helper"}
    rows = {k: manifest["sources"][k] for k in bodies}
    for name, relative in NEW.items():
        raw = capture(REPO/relative)
        snapshots[str(REPO/relative)] = sha(raw)
        bodies[name] = raw.replace(b"\r\n", b"\n")
        rows[name] = dict(path=relative, sha256=sha(bodies[name]))
    raw = capture(Path(__file__))
    snapshots[str(Path(__file__))] = sha(raw)
    proof_body = raw.replace(b"\r\n", b"\n")
    need(len(bodies) == len(rows) == 15, "exact_runtime_closure_required")
    wheels = gate.wheel_bodies(args.wheels_dir, manifest)
    results = [run_case(case, gate, baseline, pg_module, manifest, rows, bodies, wheels, proof_body)
               for case in CASES]
    _, after = gate.sources()
    need(all(captured[k] == after[k] for k in captured)
         and all(sha(capture(Path(p))) == wanted for p, wanted in snapshots.items())
         and len(results) == len(CASES) == 18 and [r["case"] for r in results] == list(CASES)
         and all(r["outcome"] == "PASS" and r["owned_resources_cleanup_confirmed"] is True
                 and all(r[key] is value for key, value in
                         pg_evidence(r["case"], r["pg_fixture_started"],
                                     r["reader_backends_absent_before_pg_teardown"] is True).items())
                 and r["production_pg_source_admission_proved"] is False
                 and all(r[k] is False for k in FLAGS) for r in results), "complete_unchanged_eighteen_case_gate")
    gate.preflight()
    print(json.dumps(dict(format="pult-operator-pg-admission-native-v1", outcome="PASS",
        scope="SYNTHETIC_PG_SOURCE_ENVELOPE_ONLY", tests=18, skips=0, failures=[], results=results,
        runtime_source_hashes={k: sha(v) for k, v in bodies.items()},
        test_helper_hashes={k: v[1] for k, v in HELPERS.items()},
        proof_sha256=sha(proof_body), wheel_hashes=manifest["wheels"], sources_unchanged=True,
        remaining_owned_resources=[], production_pg_source_admission_proved=False, **FLAGS), sort_keys=True))
    return 0


if __name__ == "__main__":
    try:
        result = main()
    except BaseException as error:
        locations = [dict(file=Path(row.filename).name, line=row.lineno, function=row.name)
                     for row in traceback.extract_tb(error.__traceback__)]
        print(json.dumps(dict(outcome="FAIL", exception_type=type(error).__name__,
                              locations=locations), sort_keys=True), file=sys.stderr, flush=True)
        print('{"outcome":"FAIL","reason":"pg_admission_proof_refused"}', file=sys.stderr, flush=True)
        raise SystemExit(1) from None
    raise SystemExit(result)
