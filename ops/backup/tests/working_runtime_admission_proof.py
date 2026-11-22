"""Native offline resource-lifetime proof; no remote requests or operations.

Review before running under root unshare --net, fixed CPython 3.12 -I -S -B.
The two numeric actors are ephemeral process identities, never NSS accounts.
owned_host's registered at-fork callback persists: every role process must exit.
"""

import ast
import hashlib
import json
import os
from pathlib import Path
import shutil
import select
import signal
import sqlite3
import stat
import sys
import tempfile
import threading
import time
from types import ModuleType

ENTRY = "ops/backup/working_runtime_admission.py"
PAYLOAD = "ops/backup/working_runtime.py"
ENTRY_SHA = "bd3389566f01b456290379bb4d2facfa9a020a87e83d0de9ed09df5b92903e2d"
PAYLOAD_SHA = "1e6dbd4fe3ad9897bfac8b91655fd2c3ec38155ac0bdd4149d38c16ee421f49a"
PROOF = "ops/backup/tests/working_runtime_admission_proof.py"
HELPER = "ops/backup/tests/working_launch_profile_proof.py"
HELPER_SHA = "cf9ba8e6d094efbda6bfcde7688d08d2049abe006f79d2b9f936ffb04ea5ac57"
CHECKER = "ops/backup/working_launch_profile.py"
OLD_PINS = {
    "ops/backup/operator_pg/bootstrap.py": "419fd3b888e1824a916ca1072836d23722edc60ea27027bf17bafe0de64689c4",
    "ops/backup/custodian_v2/cold/cold_host.py": "091133edcafb1110e13e6a79f51d336d9a152f2bf3f96d485b2c38d3899f438c",
    "ops/backup/working_journal_v2.py": "d2138f1c572bd8d074c88de3db6c7535077fd82e9b4dcd3f000bcd758fccc83c",
    "ops/backup/custodian_v2/codec/event_codec.py": "c33ad596b91fe927df85fca61ae32d35bb1cf3a1d02da2b09dadb69fc361d04c",
    "ops/backup/erasure_journal_s3.py": "41c8da982e016730c740f826fa0ff1ebf00923d8f067fb86757783fc0773ca1d",
    "ops/backup/erasure_journal.py": "94eeba19e645e25ec8286baebe4092a29c541c6f12858e1ff7b303f6a1a4fc98",
    "ops/canary/canary.py": "aa4b357acc6e908341c5523db8f93a32fdf3c0305a7ddc36c4c68842fbe3ee4f",
    "ops/backup/custodian/requirements.lock": "cd1f0a681aae91a132427b88897782b0f2085c8a415cd39812f8e7a059361f08",
}
PARENTS = {
    CHECKER: "01d4023ff8c3c2d44a481d2ee034704c4584259846592e23a3072be0bbf908da",
    "ops/backup/custodian_v2/candidate/anchor_v2.py": "ce6bff676dd2d0eccc5857efe34316fdeb644fdb91df7fff20ef40f77dc17d73",
    "ops/backup/custodian_v2/ownership/owned_host.py": "a9d71db50be14bd9d57c2535748907a26bc75eabfe3af9d8e203f77a6f434bc3",
    "ops/backup/custodian_v2/ownership/host_core.py": "da3754a860495a5c193daaf27cde3c04b95c55fc58d37dea60af37a58626e1e8",
    "ops/backup/erasure_anchor.py": "b399803ac7a03aaa5a506cbb0036f98c153457769beabf46af44864b190861a8",
    "ops/backup/custodian_v2/synthetic_ssh_s3/live_event_store.py": "2e36b7e064b9ad8dbf1c324fcb57aee0b8e520f71f49c0c5ec6d01d38ba6ed84",
}
ACTORS = {"custodian": {"uid": 46201, "gid": 46202}, "writer": {"uid": 46203, "gid": 46204}}
ENV = {"PATH": "/usr/bin:/bin", "LANG": "C.UTF-8"}
NS = dict(endpoint="https://s3.ru-3.storage.selcloud.ru", region="ru-3",
          bucket="pult-custodian-working-runtime-proof", scope="pult-work-runtime-proof",
          generation="8d3c8e2f-d470-41cb-87ad-50bf698c433b")
CASES = (
    "custodian_positive", "writer_positive", "wrong_sha", "wrong_format", "unknown_field",
    "wrong_actor", "wrong_root", "poison_environment", "extra_source", "payload_substitution",
    "credential_mode", "credential_acl", "credential_symlink", "credential_wrong_identity",
    "credential_wrong_access", "wrong_base", "pending", "missing_state", "lease_contended",
    "source_drift", "module_drift", "thread_owner", "fork_owner", "close_lifetime",
    "checker_substitution", "credential_fifo", "credential_hardlink", "credential_oversize",
    "state_mode644", "state_hardlink",
)
EARLY = {"wrong_sha", "wrong_format", "unknown_field", "wrong_actor", "wrong_root",
         "poison_environment", "extra_source", "payload_substitution", "checker_substitution"}
PARTIAL = {"missing_state", "lease_contended", "state_mode644", "state_hardlink"}
RETAIN = {"credential_mode", "credential_acl", "credential_symlink", "credential_wrong_identity",
          "credential_wrong_access", "wrong_base", "pending", "source_drift", "module_drift", "thread_owner",
          "credential_fifo", "credential_hardlink", "credential_oversize"}
EVENTS = ("socket.__new__", "socket.connect", "socket.bind", "socket.getaddrinfo", "subprocess.Popen",
          "os.system", "os.posix_spawn")
RESULTS = []
PRESERVED = False
FAILED_CASE = None
UNRESOLVED = False


class ProofError(Exception):
    pass


def need(value):
    if not value:
        raise ProofError("working_runtime_proof_refused")


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


def canonical(value):
    return (json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False,
                       ensure_ascii=True) + "\n").encode("ascii")


def trust_read(file, maximum=600000):
    """Small bootstrap read BEFORE the hash-pinned shared helper executes."""
    fd = os.open(file, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC)
    try:
        before = os.fstat(fd)
        need(stat.S_ISREG(before.st_mode) and 0 < before.st_size <= maximum)
        raw = os.pread(fd, maximum + 1, 0)
        fields = ("st_dev", "st_ino", "st_mode", "st_uid", "st_gid", "st_nlink",
                  "st_size", "st_mtime_ns", "st_ctime_ns")
        after, linked = os.fstat(fd), os.stat(file, follow_symlinks=False)
        need(len(raw) == before.st_size and all(getattr(before, n) == getattr(after, n)
                                               == getattr(linked, n) for n in fields))
        return raw
    finally:
        os.close(fd)


def helper_at(file, *, assembly=False):
    raw = trust_read(file)
    if assembly:
        raw = raw.replace(b"\r\n", b"\n")
    need(digest(raw) == HELPER_SHA)
    module = ModuleType("_fixed_runtime_proof_utilities")
    module.__file__ = str(file)
    exec(compile(raw, str(file), "exec", dont_inherit=True, optimize=0), module.__dict__)
    need(module.PINS == OLD_PINS and len(module.CASES) == 30)
    # Only a trusted test deadline is changed. Old CASES/PINS/UID/PAYLOAD stay intact.
    module.CHILD_TIMEOUT = 55
    return module, raw


def constants(raw):
    return {t.id: ast.literal_eval(n.value) for n in ast.parse(raw).body
            if isinstance(n, ast.Assign) for t in n.targets
            if isinstance(t, ast.Name) and t.id in {"APP_ID", "SCHEMA", "META_SQL", "RECEIPTS_SQL"}}


def pair(file):
    info = file.lstat()
    return {"device": info.st_dev, "inode": info.st_ino}


def fixture_info(file):
    """Activate/check own-fixture type and protection without reading its body."""
    fd = os.open(file, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC)
    try:
        info = os.fstat(fd)
        need(pair(file) == {"device": info.st_dev, "inode": info.st_ino})
        return info
    finally:
        os.close(fd)


def private(file, raw, actor, helper):
    helper.write(file, raw, 0o600)
    os.chown(file, actor["uid"], actor["gid"])
    os.chown(file.parent, actor["uid"], actor["gid"])
    file.parent.chmod(0o700)


def codec_module(sources):
    relative = "ops/backup/custodian_v2/codec/event_codec.py"
    need(digest(sources[relative]) == OLD_PINS[relative])
    name = "_root_fixture_fixed_codec"
    module = ModuleType(name)
    sys.modules[name] = module
    try:
        exec(compile(sources[relative], "<fixed-fixture-codec>", "exec"), module.__dict__)
    finally:
        del sys.modules[name]
    return module


def create_state(file, sources, helper, case):
    values = constants(sources["ops/backup/custodian_v2/candidate/anchor_v2.py"])
    need(set(values) == {"APP_ID", "SCHEMA", "META_SQL", "RECEIPTS_SQL"}
         and values["APP_ID"] == 0x50553232 and values["SCHEMA"] == 2)
    codec = codec_module(sources)
    binding = codec.Binding(NS["scope"], NS["generation"])
    genesis = codec.genesis(binding)
    independent = digest(canonical(dict(format="pult-erasure-event-v2", kind="genesis",
                                        scope=NS["scope"], generation=NS["generation"])))
    need(genesis.sequence == 0 and genesis.digest == independent)
    location = NS["endpoint"] + "/" + NS["bucket"] + "/pult/erasure-v2/" + NS["scope"] + "/" + NS["generation"] + "/events/"
    file.parent.mkdir(mode=0o700)
    connection = sqlite3.connect(file)
    try:
        connection.execute("PRAGMA application_id=" + str(values["APP_ID"]))
        connection.execute("PRAGMA user_version=" + str(values["SCHEMA"]))
        connection.execute(values["META_SQL"])
        connection.execute(values["RECEIPTS_SQL"])
        connection.execute("INSERT INTO anchor_v2 VALUES(1,?,?,?,0,?,0,NULL)",
                           (NS["scope"], NS["generation"], location, genesis.digest))
        if case == "pending":
            attempt = "e497da70-9d9c-4dd0-83a7-5a5f785857af"
            event = codec.Event("b6f4b125-2df6-44dd-b32b-f8a34bfc0e54", "5d858a04-d059-4c60-b333-42fb7646265f", "intent")
            body, target = codec.encode_candidate(binding, genesis, event)
            connection.execute("INSERT INTO attempts_v2 VALUES(?,?,?,?,?,?,'PREPARED')",
                               (attempt, 0, genesis.digest, 1, target.digest, body))
            connection.execute("UPDATE anchor_v2 SET revision=1,pending=?", (attempt,))
        connection.commit()
    finally:
        connection.close()
    file.chmod(0o600)
    os.chown(file, ACTORS["custodian"]["uid"], ACTORS["custodian"]["gid"])
    os.chown(file.parent, ACTORS["custodian"]["uid"], ACTORS["custodian"]["gid"])
    file.parent.chmod(0o700)
    lease = file.parent / "owner.lock"
    private(lease, b"", ACTORS["custodian"], helper)
    return dict(sequence=0, digest=genesis.digest, revision=0)


def assemble(scope, sources, wheels, dependencies, directories, helper):
    release = scope / "release"
    for relative, raw in sources.items():
        helper.write(release / relative, raw)
    for name, raw in wheels.items():
        helper.write(release / "wheels" / name, raw)
    for name in directories:
        (release / "deps" / name).mkdir(parents=True, exist_ok=True)
    for name, raw in dependencies.items():
        helper.write(release / "deps" / name, raw)
    for directory, _, _ in os.walk(release):
        Path(directory).chmod(0o755)
    return release


def fixture(scope, release, sources, helper, case):
    root = scope / case
    root.mkdir(mode=0o755)
    root.chmod(0o755)
    role = "writer" if case == "writer_positive" else "custodian"
    actor = ACTORS[role]
    marker_dir = root / "marker"
    marker_dir.mkdir(mode=0o700)
    marker_dir.chmod(0o700)
    os.chown(marker_dir, actor["uid"], actor["gid"])
    credential = root / "private-key" / "credential.json"
    access = "ARTIFICIAL_RUNTIME_" + role.upper()
    private(credential, canonical(dict(access_key=access, secret_key="artificial-runtime-secret-only")), actor, helper)
    observed_identity = dict(pair(credential))
    resources = dict(credential=dict(path=str(credential), identity=pair(credential),
                                     access_sha256=digest(access.encode("ascii"))))
    if role == "custodian":
        state = root / "keeper-state" / "anchor.sqlite"
        expected = create_state(state, sources, helper, case)
        resources.update(state=dict(path=str(state), identity=pair(state)),
                         lease=dict(path=str(state.parent / "owner.lock"), identity=pair(state.parent / "owner.lock")),
                         expected=expected)
    spec = dict(format="pult-working-runtime-admission-v1", release=dict(root=str(release), identity=pair(release)),
                entry_sha256=digest(sources[ENTRY]), role=role, actors=ACTORS,
                namespace=NS, peer_ipv4="1.1.1.1", resources=resources)
    if case == "wrong_format":
        spec["format"] = "old-offline-checker"
    elif case == "unknown_field":
        spec["admitted"] = True
    elif case == "wrong_actor":
        spec["actors"] = {k: dict(v) for k, v in ACTORS.items()}
        spec["actors"][role]["uid"] += 20
    elif case == "wrong_root":
        spec["release"]["identity"]["inode"] += 1
    elif case == "credential_mode":
        credential.chmod(0o644)
    elif case == "credential_acl":
        helper.acl(credential)
        credential.chmod(0o600)
        need("system.posix_acl_access" in os.listxattr(credential))
    elif case == "credential_symlink":
        alternate = credential.with_name("credential-alternate")
        credential.rename(alternate)
        credential.symlink_to(alternate)
    elif case == "credential_wrong_identity":
        resources["credential"]["identity"]["inode"] += 1
    elif case == "credential_wrong_access":
        resources["credential"]["access_sha256"] = "0" * 64
    elif case == "credential_fifo":
        credential.unlink()
        os.mkfifo(credential, 0o600)
        os.chown(credential, actor["uid"], actor["gid"])
        credential.chmod(0o600)
        info = fixture_info(credential)
        observed_identity = {"device": info.st_dev, "inode": info.st_ino}
        resources["credential"]["identity"] = dict(observed_identity)
        need(stat.S_ISFIFO(info.st_mode) and stat.S_IMODE(info.st_mode) == 0o600
             and (info.st_uid, info.st_gid) == (actor["uid"], actor["gid"])
             and info.st_nlink == 1 and info.st_size == 0)
    elif case == "credential_hardlink":
        extra = credential.with_name("credential-second-link")
        os.link(credential, extra)
        info = fixture_info(credential)
        need(stat.S_ISREG(info.st_mode) and stat.S_IMODE(info.st_mode) == 0o600
             and (info.st_uid, info.st_gid) == (actor["uid"], actor["gid"])
             and info.st_nlink == 2 and pair(extra) == resources["credential"]["identity"] == observed_identity)
    elif case == "credential_oversize":
        helper.write(credential, b"x" * 8193, 0o600)
        info = fixture_info(credential)
        need(stat.S_ISREG(info.st_mode) and stat.S_IMODE(info.st_mode) == 0o600
             and (info.st_uid, info.st_gid) == (actor["uid"], actor["gid"])
             and info.st_nlink == 1 and info.st_size == 8193
             and pair(credential) == resources["credential"]["identity"] == observed_identity)
    elif case == "wrong_base":
        resources["expected"]["digest"] = "0" * 64
    elif case == "missing_state":
        Path(resources["state"]["path"]).unlink()
    elif case in {"state_mode644", "state_hardlink"}:
        state = Path(resources["state"]["path"])
        if case == "state_mode644":
            state.chmod(0o644)
        else:
            extra = root / "state-second-link"
            os.link(state, extra)
            need(pair(extra) == resources["state"]["identity"])
        info = fixture_info(state)
        need(stat.S_ISREG(info.st_mode) and pair(state) == resources["state"]["identity"]
             and (info.st_uid, info.st_gid) == (actor["uid"], actor["gid"])
             and stat.S_IMODE(info.st_mode) == (0o644 if case == "state_mode644" else 0o600)
             and info.st_nlink == (1 if case == "state_mode644" else 2) and 0 < info.st_size <= 2 * 1024 * 1024)
    profile = root / "profile.json"
    raw = canonical(spec)
    helper.write(profile, raw)
    helper.write(root / "observer.json", canonical(dict(credential_identity=observed_identity)))
    return root, spec, profile, "0" * 64 if case == "wrong_sha" else digest(raw)


def lease_busy(file):
    import fcntl
    fd = os.open(file, os.O_RDWR | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC)
    try:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return True
        return False
    finally:
        os.close(fd)


class Observer:
    """Trusted fixture instrumentation; never a sandbox or operation authority."""

    def __init__(self, spec, credential_identity):
        self.spec = spec
        self.credential_identity = credential_identity
        self.counts = dict.fromkeys(EVENTS, 0)
        self.counts.update(sql_opens=0, sql_handles=0, credential_opens=0, credential_reads=0,
                           credential_read_attempts=0, fixture_forks=0)
        self.ordered = True
        self.fixture_fork = False
        self.original_read, self.original_pread = os.read, os.pread

    def audit(self, event, args):
        if event == "os.fork":
            self.counts["fixture_forks"] += 1
            need(self.fixture_fork)
        if event in EVENTS:
            self.counts[event] += 1
            raise ProofError("offline_io_forbidden")
        if event == "sqlite3.connect":
            need(self.spec["role"] == "custodian")
            database = str(args[0])
            need(database == Path(self.spec["resources"]["state"]["path"]).as_uri() + "?mode=rw")
            self.counts["sql_opens"] += 1
            need(lease_busy(self.spec["resources"]["lease"]["path"]))
        elif event == "sqlite3.connect/handle":
            self.counts["sql_handles"] += 1
        elif event == "open" and isinstance(args[0], (str, bytes)):
            file = os.fsdecode(args[0])
            if Path(file).name == "credential.json":
                self.counts["credential_opens"] += 1
                if self.spec["role"] == "custodian":
                    self.ordered = self.ordered and self.counts["sql_handles"] > 0

    def seen_read(self, fd, raw):
        info = os.fstat(fd)
        expected = self.credential_identity
        if raw and (info.st_dev, info.st_ino) == (expected["device"], expected["inode"]):
            self.counts["credential_reads"] += 1
            if self.spec["role"] == "custodian":
                self.ordered = self.ordered and self.counts["sql_handles"] > 0
                need(lease_busy(self.spec["resources"]["lease"]["path"]))

    def before_read(self, fd):
        info = os.fstat(fd)
        expected = self.credential_identity
        if (info.st_dev, info.st_ino) == (expected["device"], expected["inode"]):
            self.counts["credential_read_attempts"] += 1

    def read(self, fd, size):
        self.before_read(fd)
        raw = self.original_read(fd, size)
        self.seen_read(fd, raw)
        return raw

    def pread(self, fd, size, offset):
        self.before_read(fd)
        raw = self.original_pread(fd, size, offset)
        self.seen_read(fd, raw)
        return raw


def refused(call, kind):
    try:
        call()
    except kind as error:
        need(str(error) == "working_runtime_refused")
        return True
    return False


def fork_owner(owner, kind):
    """Owned direct fork with bounded EOF/reap; no project code in its branch."""
    read_fd, write_fd = os.pipe2(os.O_CLOEXEC | os.O_NONBLOCK)
    pid, pidfd, reaped = None, None, False
    closed, errors = [], []
    try:
        pid = os.fork()
        if pid == 0:
            signal.alarm(5)
            try:
                os.close(read_fd)
                okay = refused(owner.check, kind) and refused(owner.close, kind)
                os.write(write_fd, b"REFUSED" if okay else b"BAD")
                os.close(write_fd)
                os._exit(0 if okay else 1)
            except BaseException:
                os._exit(1)
        pidfd = os.pidfd_open(pid)
        os.close(write_fd)
        closed.append(write_fd)
        body, eof = bytearray(), False
        deadline = time.monotonic() + 6
        while not eof and time.monotonic() < deadline:
            if not select.select([read_fd], [], [], 0.1)[0]:
                continue
            chunk = os.read(read_fd, 64)
            eof = chunk == b""
            body.extend(chunk)
            need(len(body) <= 64)
        need(eof and body == b"REFUSED")
        while time.monotonic() < deadline:
            waited, status = os.waitpid(pid, os.WNOHANG)
            if waited == pid:
                reaped = True
                need(os.waitstatus_to_exitcode(status) == 0)
                break
            time.sleep(0.01)
        need(reaped)
    finally:
        if pid and not reaped:
            try:
                if pidfd is not None:
                    signal.pidfd_send_signal(pidfd, signal.SIGKILL)
                else:
                    os.kill(pid, signal.SIGKILL)
            except BaseException:
                errors.append(True)
            deadline = time.monotonic() + 5
            while time.monotonic() < deadline:
                try:
                    waited, _ = os.waitpid(pid, os.WNOHANG)
                    if waited == pid:
                        reaped = True
                        break
                except BaseException:
                    errors.append(True)
                    break
                time.sleep(0.01)
        for fd in (read_fd, write_fd, pidfd):
            if fd is not None and fd not in closed:
                try:
                    os.close(fd)
                except BaseException:
                    errors.append(True)
        need(reaped and not errors)
    need(owner.check() is True)
    return True


def role_child(args):
    import resource
    need(len(args) == 5)
    case, entry_file, profile, approval, utility_file = args
    need(case in CASES)
    helper, _ = helper_at(Path(utility_file))
    resource.setrlimit(resource.RLIMIT_CPU, (12, 13))
    signal.alarm(50)
    spec = json.loads(helper.bounded_file(Path(profile), 20000))
    root = Path(profile).parent
    role = spec["role"]
    actor = ACTORS[role]
    principal = helper.proc_fields()
    need(principal["uid"] == [actor["uid"]] * 4 and principal["gid"] == [actor["gid"]] * 4
         and principal["groups"] == [] and principal["nnp"] == 1 and not any(principal["caps"].values()))
    marker = root / "marker" / "executed"
    exec(compile(helper.canary(marker), "<actual-canary-positive-control>", "exec"), {})
    need(helper.bounded_file(marker, 64) == b"CANARY_EXECUTED" and marker.lstat().st_uid == actor["uid"])
    marker.unlink()
    observer_control = json.loads(helper.bounded_file(root / "observer.json", 1000))
    need(set(observer_control) == {"credential_identity"})
    observer = Observer(spec, observer_control["credential_identity"])
    sys.addaudithook(observer.audit)
    os.read, os.pread = observer.read, observer.pread
    module = ModuleType("_runtime_admission_native_entry")
    module.__file__ = entry_file
    exec(compile(helper.bounded_file(Path(entry_file), 600000), entry_file, "exec"), module.__dict__)
    names = helper.PAYLOAD | {"working_live_store"}

    def modules():
        return {n: id(v) for n, v in sys.modules.items() if n.split(".", 1)[0] in names}

    baseline, meta, paths = helper.descriptors(), tuple(sys.meta_path), tuple(sys.path)
    need(not modules())
    owner, snapshot, retained, forked = None, None, False, False
    status = "REFUSED"
    try:
        try:
            owner = module.load_runtime(profile, approval)
        except module.Refused as error:
            need(str(error) == "working_runtime_refused")
            if module._FAILED and isinstance(module._FAILED[-1], module.RuntimeAdmission):
                owner = module._FAILED[-1]
        else:
            need(case not in EARLY | PARTIAL and case not in RETAIN - {"source_drift", "module_drift", "thread_owner"})
            need(owner.check() is True)
            snapshot = owner.snapshot()
            resources = owner._resources
            httpx = sys.modules["httpx"]
            need(type(resources._client._client) is httpx.Client and resources._client._client.is_closed is False)
            if case == "source_drift":
                helper.write(root / "marker/ready", b"READY", 0o600)
                deadline = time.monotonic() + 8
                while not (root / "marker/mutated").exists() and time.monotonic() < deadline:
                    time.sleep(0.01)
                need((root / "marker/mutated").exists() and refused(owner.check, module.Refused))
            elif case == "module_drift":
                sys.modules["httpx"] = ModuleType("httpx")
                need(refused(owner.check, module.Refused))
            elif case == "thread_owner":
                answer = []
                thread = threading.Thread(target=lambda: answer.append(refused(owner.check, module.Refused)))
                thread.start()
                thread.join(5)
                need(not thread.is_alive() and answer == [True])
            elif case == "fork_owner":
                observer.fixture_fork = True
                try:
                    forked = fork_owner(owner, module.Refused)
                finally:
                    observer.fixture_fork = False
            if case not in RETAIN:
                owner.close()
                need(resources._client._client.is_closed is True and refused(owner.check, module.Refused))
                if role == "custodian":
                    need(not lease_busy(spec["resources"]["lease"]["path"]))
                status = "CLOSED"
        if case in PARTIAL:
            need(owner is not None and refused(owner.close, module.Refused))
            need(owner._failed is True and owner._capture.closed is False
                 and owner._finder in sys.meta_path and bool(modules())
                 and owner._resources is not None and owner._resources._lease is None)
            status = "PRE_IO_RETAINED_SOURCE"
        elif case in RETAIN:
            need(owner is not None and refused(owner.close, module.Refused))
            retained = lease_busy(spec["resources"]["lease"]["path"])
            need(retained)
            status = "RETAINED_UNTIL_PROCESS_EXIT"
        fd_restored = helper.descriptors() == baseline
        imports_restored = not modules() and tuple(sys.meta_path) == meta and tuple(sys.path) == paths
        if status == "CLOSED" or case in EARLY:
            need(fd_restored and imports_restored)
        need(not marker.exists() and observer.ordered and not any(observer.counts[k] for k in EVENTS))
        value = dict(case=case, role=role, status=status, snapshot=snapshot,
                     counts=observer.counts, ordered=True, lease_retained=retained,
                     fork_reaped=forked, fds_restored=fd_restored, imports_restored=imports_restored,
                     marker_control_confirmed=True, process_exit_required=True,
                     owner_failed=owner is not None and owner._failed is True,
                     capture_open=owner is not None and owner._capture.closed is False,
                     source_imports_held=bool(modules()),
                     lease_owned=owner is not None and owner._resources is not None
                     and owner._resources._lease is not None and owner._resources._lease.fd is not None)
    finally:
        os.read, os.pread = observer.original_read, observer.original_pread
    sys.stdout.buffer.write(canonical(value))
    return 0


def validate(case, value):
    need(type(value) is dict and set(value) == {"case", "role", "status", "snapshot", "counts", "ordered",
         "lease_retained", "fork_reaped", "fds_restored", "imports_restored", "marker_control_confirmed", "process_exit_required",
         "owner_failed", "capture_open", "source_imports_held", "lease_owned"})
    role = "writer" if case == "writer_positive" else "custodian"
    need(value["case"] == case and value["role"] == role)
    for key in ("ordered", "lease_retained", "fork_reaped", "fds_restored", "imports_restored", "marker_control_confirmed", "process_exit_required",
                "owner_failed", "capture_open", "source_imports_held", "lease_owned"):
        need(type(value[key]) is bool)
    need(value["ordered"] and value["marker_control_confirmed"] and value["process_exit_required"])
    counts = value["counts"]
    need(type(counts) is dict and set(counts) == set(EVENTS) | {"sql_opens", "sql_handles", "credential_opens", "credential_reads", "credential_read_attempts", "fixture_forks"}
         and all(type(v) is int and 0 <= v <= 10000 for v in counts.values()))
    need(not any(counts[k] for k in EVENTS))
    need(counts["fixture_forks"] == (1 if case == "fork_owner" else 0))
    if case in {"credential_fifo", "credential_hardlink", "credential_oversize", "state_mode644", "state_hardlink"}:
        need(counts["credential_read_attempts"] == 0)
    if case in EARLY:
        need(value["status"] == "REFUSED" and value["snapshot"] is None and counts["credential_reads"] == 0
             and counts["credential_opens"] == 0 and counts["sql_handles"] == 0)
    elif case in PARTIAL:
        need(value["status"] == "PRE_IO_RETAINED_SOURCE" and value["snapshot"] is None
             and counts["credential_reads"] == counts["credential_opens"] == counts["sql_handles"] == 0)
        need(value["owner_failed"] and value["capture_open"] and value["source_imports_held"]
             and not value["lease_owned"] and not value["lease_retained"]
             and not value["fds_restored"] and not value["imports_restored"])
    elif case in RETAIN:
        need(value["status"] == "RETAINED_UNTIL_PROCESS_EXIT" and value["lease_retained"] is True)
        if case not in {"credential_wrong_access", "source_drift", "module_drift", "thread_owner"}:
            need(counts["credential_reads"] == 0)
        need(counts["sql_handles"] > 0)
        if case == "credential_wrong_access":
            need(counts["credential_reads"] > 0)
    else:
        need(value["status"] == "CLOSED" and value["fds_restored"] and value["imports_restored"])
    if value["snapshot"] is not None:
        snapshot = value["snapshot"]
        flags = {"operation_admitted", "launch_admitted", "identity_enrollment_admitted", "journal_dispatch_admitted", "purge_or_restore_admitted", "remote_io_admitted"}
        need(type(snapshot) is dict and set(snapshot) == flags | {"status", "role", "state_opened", "credentials_opened", "client_constructed", "lease_held"})
        need(snapshot["status"] == "RUNTIME_CONSTRUCTED_OPERATION_CLOSED" and snapshot["role"] == role
             and snapshot["credentials_opened"] is True and snapshot["client_constructed"] is True
             and snapshot["state_opened"] is (role == "custodian") and snapshot["lease_held"] is (role == "custodian")
             and all(snapshot[k] is False for k in flags))
        need(counts["credential_reads"] > 0 and (counts["sql_handles"] > 0) == (role == "custodian"))
    need(value["fork_reaped"] is (case == "fork_owner"))


def owned_groups_empty(helper):
    """Read-only signal-0 probe of exact PGIDs created by pinned start_new_session.

    Never kill/adopt a group. Reuse, permissions, or any live descendant means
    uncertainty and preservation, even if it could belong to an unrelated actor.
    """
    for owned in helper.OWNED_CHILDREN:
        need(owned.cleanup_confirmed is True and owned.reaped is True and owned.process is not None)
        pgid = owned.process.pid
        need(type(pgid) is int and pgid > 0 and pgid != os.getpgrp())
        try:
            os.killpg(pgid, 0)
        except ProcessLookupError:
            continue
        except OSError:
            return False
        return False
    return True


def cleanup_scope(scope, original, baseline, helper):
    global PRESERVED
    need(not UNRESOLVED and helper.fixture_cleanup_allowed() and helper.descriptors() == baseline
         and owned_groups_empty(helper))
    info = scope.lstat()
    need(scope.parent == Path("/tmp") and scope.name.startswith("pult-working-runtime-")
         and not scope.is_symlink() and (info.st_dev, info.st_ino) == original
         and info.st_uid == 0 and stat.S_ISDIR(info.st_mode))
    shutil.rmtree(scope)
    need(not os.path.lexists(scope) and helper.descriptors() == baseline)
    PRESERVED = False


def run(helper, helper_raw, wheels_dir):
    import fcntl
    global PRESERVED, FAILED_CASE, UNRESOLVED
    repo = Path(__file__).resolve().parents[3]
    names = {*OLD_PINS, *PARENTS, ENTRY, PAYLOAD}
    need(len(names) == 16)
    raw_inputs = {n: helper.bounded_file(repo / n, 600000) for n in names | {HELPER, PROOF}}
    raw_hashes = {n: digest(raw) for n, raw in raw_inputs.items()}
    need(raw_inputs[HELPER].replace(b"\r\n", b"\n") == helper_raw)
    sources = {n: raw_inputs[n].replace(b"\r\n", b"\n") for n in names}
    hashes = {n: digest(raw) for n, raw in sources.items()}
    need(hashes[ENTRY] == ENTRY_SHA and hashes[PAYLOAD] == PAYLOAD_SHA)
    need(all(hashes[n] == pin for n, pin in (OLD_PINS | PARENTS).items()))
    additions = helper.literal(sources[ENTRY], "ADDITIONS")
    need(additions == PARENTS | {PAYLOAD: PAYLOAD_SHA})
    wheels = {n: helper.bounded_file(wheels_dir / n, 2 * 1024 * 1024) for n in helper.WHEELS}
    need(all(digest(raw) == helper.WHEELS[n] for n, raw in wheels.items()))
    dependencies, directories = helper.inventory(wheels)
    need(os.readlink("/proc/self/ns/net") != os.readlink("/proc/1/ns/net"))
    scope = Path(tempfile.mkdtemp(prefix="pult-working-runtime-", dir="/tmp"))
    PRESERVED = True
    scope.chmod(0o755)
    original, baseline = helper.identity(scope), helper.descriptors()
    try:
        release = assemble(scope, sources, wheels, dependencies, directories, helper)
        helper.write(scope / "utilities.py", helper_raw)
        helper.write(scope / "proof.py", raw_inputs[PROOF].replace(b"\r\n", b"\n"))
        for case in CASES:
            FAILED_CASE = case
            root, spec, profile, approval = fixture(scope, release, sources, helper, case)
            state = Path(spec["resources"]["state"]["path"]) if spec["role"] == "custodian" else None
            state_before = digest(helper.bounded_file(state, 2 * 1024 * 1024)) if state is not None and state.exists() else None
            held, thread, stop, mutation_errors = None, None, threading.Event(), []
            target = release / PAYLOAD
            checker_target = release / CHECKER
            extra = release / "ops/backup/unapproved.py"
            if case == "payload_substitution":
                helper.write(target, helper.canary(root / "marker/executed"))
            elif case == "checker_substitution":
                body = helper.canary(root / "marker/executed")
                helper.write(checker_target, body)
                info = fixture_info(checker_target)
                need(stat.S_ISREG(info.st_mode) and stat.S_IMODE(info.st_mode) == 0o444
                     and info.st_uid == 0 and info.st_nlink == 1
                     and helper.bounded_file(checker_target, 600000) == body
                     and digest(body) != PARENTS[CHECKER])
            elif case == "extra_source":
                helper.write(extra, helper.canary(root / "marker/executed"))
            if case == "lease_contended":
                held = os.open(spec["resources"]["lease"]["path"], os.O_RDWR | os.O_NOFOLLOW | os.O_CLOEXEC)
                fcntl.flock(held, fcntl.LOCK_EX | fcntl.LOCK_NB)
            if case == "source_drift":
                def mutate():
                    try:
                        deadline = time.monotonic() + 25
                        while not stop.is_set() and time.monotonic() < deadline:
                            if (root / "marker/ready").exists():
                                helper.write(target, sources[PAYLOAD] + b"\n# actual root fixture drift\n")
                                helper.write(root / "marker/mutated", b"MUTATED")
                                return
                            stop.wait(0.01)
                        if not stop.is_set():
                            mutation_errors.append(True)
                    except BaseException:
                        mutation_errors.append(True)
                thread = threading.Thread(target=mutate)
                thread.start()
            actor = ACTORS[spec["role"]]
            argv = ["/usr/bin/setpriv", "--reuid=" + str(actor["uid"]), "--regid=" + str(actor["gid"]),
                    "--clear-groups", "--bounding-set=-all", "--inh-caps=-all", "--ambient-caps=-all", "--no-new-privs", "--",
                    "/usr/bin/python3.12", "-I", "-S", "-B", str(scope / "proof.py"), "--role", case,
                    str(release / ENTRY), str(profile), approval, str(scope / "utilities.py")]
            env = dict(ENV)
            if case == "poison_environment":
                env["SSL_CERT_FILE"] = str(root / "marker/unapproved-ca")
            try:
                value = helper.bounded_child(argv, env)
                validate(case, value)
                need(owned_groups_empty(helper))
            except BaseException:
                UNRESOLVED = True
                raise
            finally:
                stop.set()
                if thread is not None:
                    thread.join(2)
                    if thread.is_alive() or mutation_errors:
                        UNRESOLVED = True
                if held is not None:
                    try:
                        os.close(held)
                    except BaseException:
                        UNRESOLVED = True
                        raise
            need(not UNRESOLVED and not mutation_errors)
            if case in {"payload_substitution", "source_drift"}:
                helper.write(target, sources[PAYLOAD])
            elif case == "checker_substitution":
                helper.write(checker_target, sources[CHECKER])
                need(helper.bounded_file(checker_target, 600000) == sources[CHECKER]
                     and digest(sources[CHECKER]) == PARENTS[CHECKER])
            if extra.exists():
                extra.unlink()
            if spec["role"] == "custodian" and case != "missing_state":
                need(not lease_busy(spec["resources"]["lease"]["path"]))
                need(digest(helper.bounded_file(state, 2 * 1024 * 1024)) == state_before)
            need(helper.descriptors() == baseline)
            RESULTS.append(dict(value, role_process_group_empty=True))
        raw_after_inputs = {n: helper.bounded_file(repo / n, 600000) for n in raw_inputs}
        raw_after = {n: digest(raw) for n, raw in raw_after_inputs.items()}
        after = {n: digest(raw_after_inputs[n].replace(b"\r\n", b"\n")) for n in names}
        wheel_after = {n: digest(helper.bounded_file(wheels_dir / n, 2 * 1024 * 1024)) for n in helper.WHEELS}
        need(after == hashes and raw_after == raw_hashes and wheel_after == helper.WHEELS
             and [v["case"] for v in RESULTS] == list(CASES))
    except BaseException:
        UNRESOLVED = True
        raise
    finally:
        cleanup_scope(scope, original, baseline, helper)
    return dict(format="pult-working-runtime-native-proof-v1", status="PASS", tests=len(CASES), skips=0,
                cases=RESULTS, source_hashes_before=hashes, source_hashes_after=after, helper_sha256=HELPER_SHA,
                repository_byte_hashes_before=raw_hashes, repository_byte_hashes_after=raw_after,
                wheel_hashes=helper.WHEELS, wheel_hashes_after=wheel_after,
                cleanup_confirmed=True, owned_process_groups_empty=True, dedicated_process_exit_required=True,
                atfork_registration_rollback_claimed=False, remote_io_admitted=False,
                journal_dispatch_admitted=False, launch_admitted=False, purge_or_restore_admitted=False)


def main():
    if len(sys.argv) > 1 and sys.argv[1] == "--role":
        try:
            return role_child(sys.argv[2:])
        except BaseException:
            print("WORKING_RUNTIME_PROOF_CHILD_REFUSED", file=sys.stderr)
            return 1
    helper = None
    try:
        need(sys.platform == "linux" and sys.version_info[:2] == (3, 12) and not sys.flags.optimize)
        repo = Path(__file__).resolve().parents[3]
        helper, helper_raw = helper_at(repo / HELPER, assembly=True)
        need(helper.supported() and dict(os.environ) == ENV)
    except BaseException:
        sys.stdout.buffer.write(canonical(dict(format="pult-working-runtime-native-proof-v1", status="NOT_RUN", tests=0, skips=0)))
        return 2
    try:
        args = sys.argv[1:]
        need(not args or len(args) == 2 and args[0] == "--wheels-dir")
        wheels = Path(args[1]) if args else repo / "ops/backup/tests/custodian_v2/wheels"
        value = run(helper, helper_raw, wheels)
        sys.stdout.buffer.write(canonical(value))
        return 0
    except BaseException:
        sys.stdout.buffer.write(canonical(dict(format="pult-working-runtime-native-proof-v1", status="FAIL",
            tests=len(RESULTS), expected_tests=len(CASES), skips=0, failed_case=FAILED_CASE,
            cleanup_confirmed=False, fixture_preserved=PRESERVED, unresolved=UNRESOLVED)))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
