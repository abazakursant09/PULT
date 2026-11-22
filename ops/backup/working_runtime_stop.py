"""One independently stopped local construction; never a journal operation.

Trusted root/OS/PID1, fixed interpreter and independent profile approval are
trust roots. The old stateless controller guard and owned cleanup are reused;
no old admission profile, constructor or claim recovery is accepted here.
"""
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import selectors
import signal
import stat
import subprocess
import sys
import threading
import time
from types import ModuleType, SimpleNamespace
from uuid import UUID, uuid4

FORMAT = "pult-working-runtime-stop-v1"
MODE = "LOCAL_ROLE_CONSTRUCTION_ONLY"
FRAME = "pult-working-runtime-stop-worker-v1"
ENV = {"PATH": "/usr/bin:/bin", "LANG": "C.UTF-8"}
LOCK, FENCE = "working-runtime-stop.lock", "constructor-stop.claim"
GO, CLOSE = b"GO_WORKING_RUNTIME_V1\n", b"CLOSE_WORKING_RUNTIME_V1\n"
RUNTIME, STOP = 40, 2
PINS = {
    "runtime": "bd3389566f01b456290379bb4d2facfa9a020a87e83d0de9ed09df5b92903e2d",
    "guard": "5406c5a81498013884ac46d0279219f299db40576357692604fb4f08c251ca8c",
    "supervisor": "3d05d22163b11f815b29c222128c4e41987c9e4a83e7b4906cbe63a18941a37e",
    "core": "961cc3aef5dcfe9d6286b15a2d5c087a33dc1e5dafdc7d5a16f6eded961afea7",
    "worker": "56ca8faf66b8454833e709ddb4a9280b2a1cd468705a89ae141e4a138ca7e402",
}
FIELDS = {"format", "mode", "sources", "runtime_profile", "controller", "control", "role", "actor"}
FLAGS = dict(operation_admitted=False, identity_enrollment_admitted=False,
             journal_dispatch_admitted=False, purge_or_restore_admitted=False,
             remote_io_admitted=False, launch_admitted=False)
_ATTEMPTED = False
_FAILED = []


class Refused(Exception):
    pass


class Unknown(Exception):
    pass


def require(value):
    if not value:
        raise Refused("working_runtime_stop_refused")


def canonical(value):
    return (json.dumps(value, sort_keys=True, separators=(",", ":"),
                       ensure_ascii=True, allow_nan=False) + "\n").encode("ascii")


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


def document(raw):
    def pairs(rows):
        value = {}
        for name, item in rows:
            require(name not in value)
            value[name] = item
        return value
    value = json.loads(raw, object_pairs_hook=pairs)
    require(type(value) is dict and canonical(value) == raw)
    return value


def path(value):
    require(type(value) is str and 1 <= len(value) <= 4096 and re.fullmatch(r"/[A-Za-z0-9_./-]+", value))
    file = Path(value)
    require(file.anchor == "/" and str(file) == value and ".." not in file.parts and file != Path("/"))
    return file


def sha(value):
    require(type(value) is str and re.fullmatch("[0-9a-f]{64}", value))
    return value


def identity(info):
    return {"device": info.st_dev, "inode": info.st_ino}


def wanted_identity(value):
    require(type(value) is dict and set(value) == {"device", "inode"}
            and all(type(v) is int and v >= 0 for v in value.values()) and value["inode"] > 0)


def ancestors(file, owners=(0,)):
    for parent in file.parents:
        info = parent.lstat()
        sticky = info.st_uid == 0 and bool(info.st_mode & stat.S_ISVTX)
        require(stat.S_ISDIR(info.st_mode) and info.st_uid in owners
                and (not info.st_mode & 0o022 or sticky)
                and not any(n.startswith("system.posix_acl_")
                            for n in os.listxattr(parent, follow_symlinks=False)))


def root_bytes(value, maximum=100000, modes=(0o444,)):
    file = path(str(value))
    ancestors(file)
    fd = os.open(file, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC)
    try:
        before = os.fstat(fd)
        require(stat.S_ISREG(before.st_mode) and before.st_uid == 0 and before.st_nlink == 1
                and stat.S_IMODE(before.st_mode) in modes and 0 <= before.st_size <= maximum
                and not any(n.startswith("system.posix_acl_") for n in os.listxattr(fd)))
        raw = os.pread(fd, maximum + 1, 0)
        after, linked = os.fstat(fd), file.lstat()
        keys = ("st_dev", "st_ino", "st_uid", "st_gid", "st_mode", "st_nlink",
                "st_size", "st_mtime_ns", "st_ctime_ns")
        require(len(raw) == before.st_size
                and all(getattr(before, k) == getattr(after, k) == getattr(linked, k) for k in keys))
        return raw
    finally:
        os.close(fd)


def runtime(root):
    require(sys.platform == "linux" and sys.implementation.name == "cpython"
            and sys.version_info[:2] == (3, 12) and sys.flags.isolated and sys.flags.no_site
            and sys.flags.ignore_environment and sys.flags.safe_path and sys.flags.no_user_site
            and sys.dont_write_bytecode and sys.flags.dont_write_bytecode and not sys.flags.optimize
            and os.path.realpath(sys.executable) == "/usr/bin/python3.12"
            and sys.prefix == sys.base_prefix == "/usr"
            and threading.current_thread() is threading.main_thread()
            and len(os.listdir("/proc/self/task")) == 1)
    require((os.getuid() == os.geteuid() == 0) if root else
            (os.getuid() == os.geteuid() >= 1000 and dict(os.environ) == ENV))


def module(file, raw, name):
    value = ModuleType(name)
    value.__file__ = str(file)
    exec(compile(raw, str(file), "exec", dont_inherit=True, optimize=0), value.__dict__)
    return value


def sync(directory):
    fd = os.open(directory, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def write_all(fd, raw):
    offset = 0
    while offset < len(raw):
        count = os.write(fd, raw[offset:])
        require(count > 0)
        offset += count


def process(pid):
    require(type(pid) is int and pid > 0)
    base = Path("/proc") / str(pid)
    before = (base / "stat").read_text().rsplit(") ", 1)[1].split()
    status = dict(row.split(":", 1) for row in (base / "status").read_text().splitlines() if ":" in row)
    group = (base / "cgroup").read_text().strip()
    command = (base / "cmdline").read_bytes().split(b"\0")[:-1]
    after = (base / "stat").read_text().rsplit(") ", 1)[1].split()
    require(before[19] == after[19] and after[0] not in ("Z", "X", "x"))
    return int(after[19]), status, group, command, after[0]


class Profile:
    """Trusted source/profile view; no private state/key is opened in root."""

    def __init__(self, file, independently_approved_sha256, root=True):
        self.root_role = root
        runtime(root)
        self.file, self.sha = path(str(file)), sha(independently_approved_sha256)
        self.raw = root_bytes(self.file, 20000)
        require(digest(self.raw) == self.sha)
        self.spec = document(self.raw)
        self.pid, self.thread = os.getpid(), threading.current_thread()
        self.modules, self.blobs = {}, {}
        s = self.spec
        require(set(s) == FIELDS and s["format"] == FORMAT and s["mode"] == MODE)
        require(type(s["sources"]) is dict and set(s["sources"]) == set(PINS) | {"stop"})
        for key, value in s["sources"].items():
            require(type(value) is dict and set(value) == {"path", "sha256"})
            source = path(value["path"])
            wanted = sha(value["sha256"])
            require(key == "stop" or wanted == PINS[key])
            self.blobs[key] = root_bytes(source, 600000)
            require(digest(self.blobs[key]) == wanted)
        require(path(s["sources"]["stop"]["path"]) == Path(__file__).absolute())
        require(path(s["sources"]["worker"]["path"]).name == "working_runtime_stop_worker.py")
        self.role, self.actor = s["role"], s["actor"]
        require(self.role in ("custodian", "writer") and type(self.actor) is dict
                and set(self.actor) == {"uid", "gid"}
                and all(type(v) is int and 1000 <= v < 2**31 for v in self.actor.values()))
        rp = s["runtime_profile"]
        require(type(rp) is dict and set(rp) == {"path", "sha256"})
        self.runtime_file, self.runtime_sha = path(rp["path"]), sha(rp["sha256"])
        self.inner_raw = root_bytes(self.runtime_file, 20000)
        require(digest(self.inner_raw) == self.runtime_sha)
        self.inner = document(self.inner_raw)
        inner = self.inner
        require(set(inner) == {"format", "release", "entry_sha256", "role", "actors", "namespace", "peer_ipv4", "resources"}
                and inner["format"] == "pult-working-runtime-admission-v1"
                and inner["role"] == self.role and inner["entry_sha256"] == PINS["runtime"]
                and type(inner["actors"]) is dict and set(inner["actors"]) == {"custodian", "writer"}
                and inner["actors"][self.role] == self.actor)
        for actor in inner["actors"].values():
            require(type(actor) is dict and set(actor) == {"uid", "gid"}
                    and all(type(v) is int and 1000 <= v < 2**31 for v in actor.values()))
        require(all(inner["actors"]["custodian"][key] != inner["actors"]["writer"][key] for key in ("uid", "gid")))
        require(type(inner["release"]) is dict and set(inner["release"]) == {"root", "identity"})
        release = path(inner["release"]["root"])
        wanted_identity(inner["release"]["identity"])
        require(path(s["sources"]["runtime"]["path"]) == release / "ops/backup/working_runtime_admission.py")
        c, control = s["controller"], s["control"]
        require(type(c) is dict and set(c) == {"unit", "invocation_id", "boot", "pid", "start_ticks"}
                and type(c["unit"]) is str and re.fullmatch(r"pult-[a-z0-9-]{1,100}\.service", c["unit"])
                and type(c["invocation_id"]) is str and re.fullmatch("[0-9a-f]{32}", c["invocation_id"])
                and type(c["boot"]) is str and str(UUID(c["boot"])) == c["boot"]
                and all(type(c[k]) is int and c[k] > 0 for k in ("pid", "start_ticks")))
        require(type(control) is dict and set(control) == {"root", "identity", "lock_identity"})
        self.control = path(control["root"])
        wanted_identity(control["identity"])
        wanted_identity(control["lock_identity"])
        resources = inner["resources"]
        expected = {"credential", "state", "lease", "expected"} if self.role == "custodian" else {"credential"}
        require(type(resources) is dict and set(resources) == expected)
        credential = path(resources["credential"]["path"])
        self.state = path(resources["state"]["path"]).parent if self.role == "custodian" else None
        sources = [self.file, self.runtime_file] + [path(v["path"]) for v in s["sources"].values()]
        require(len(set(sources)) == len(sources))
        private = [self.control, credential.parent] + ([self.state] if self.state else [])
        require(all(a != b and not a.is_relative_to(b) and not b.is_relative_to(a)
                    for i, a in enumerate(private) for b in private[i + 1:]))
        require(all(not f.is_relative_to(d) for f in sources for d in private))
        require(all(not path(s["sources"][key]["path"]).is_relative_to(release)
                    for key in ("stop", "worker", "guard", "supervisor", "core")))
        require(not self.file.is_relative_to(release) and not self.runtime_file.is_relative_to(release))
        self.check()

    def check(self):
        runtime(self.root_role)
        require(self.pid == os.getpid() and self.thread is threading.current_thread()
                and root_bytes(self.file, 20000) == self.raw and canonical(self.spec) == self.raw
                and root_bytes(self.runtime_file, 20000) == self.inner_raw)
        for key, value in self.spec["sources"].items():
            require(root_bytes(value["path"], 600000) == self.blobs[key])
        return True

    def capture(self, key):
        if key not in self.modules:
            self.modules[key] = module(path(self.spec["sources"][key]["path"]), self.blobs[key], "_working_stop_" + key)
        return self.modules[key]

    def runtime_module(self):
        self.check()
        return self.capture("runtime")

    def supervisor(self):
        require(self.root_role)
        value = self.capture("supervisor")
        value.load_core(path(self.spec["sources"]["core"]["path"]))
        return value

    def show(self, unit, fields):
        require(type(unit) is str and re.fullmatch(r"pult-[a-z0-9-]{1,100}\.service", unit))
        raw = self.supervisor()._cmd(("/usr/bin/systemctl", "show", unit, "--no-pager", "--property=" + fields)).stdout
        require(len(raw) <= 16384)
        rows = [row.split("=", 1) for row in raw.decode("ascii").splitlines() if "=" in row]
        require(len({row[0] for row in rows}) == len(rows))
        return dict(rows)

    def root_guard(self):
        self.check()
        require(self.root_role)
        guard, supervisor = self.capture("guard"), self.supervisor()
        view = SimpleNamespace(spec={"controller": self.spec["controller"], "control": self.spec["control"]},
                               pid=self.pid, supervisor=supervisor)
        view._state = lambda unit: supervisor._state(unit)
        view._extra = self.show
        guard.Bound._controller(view)
        guard.manager_view(self.control, self.spec["control"]["identity"])
        guard.check_inode(self.control, self.spec["control"]["identity"], 0, 0, 0o700, True)
        guard.check_inode(self.control / LOCK, self.spec["control"]["lock_identity"], 0, 0, 0o600)
        require(root_bytes(self.control / LOCK, 1, (0o600,)) == b""
                and {f.name for f in self.control.iterdir()}.issubset({LOCK, FENCE}))
        if self.state:
            info = self.state.lstat()
            ancestors(self.state, (0, self.actor["uid"]))
            require(stat.S_ISDIR(info.st_mode) and info.st_uid == self.actor["uid"]
                    and info.st_gid == self.actor["gid"] and stat.S_IMODE(info.st_mode) == 0o700
                    and not any(n.startswith("system.posix_acl_") for n in os.listxattr(self.state, follow_symlinks=False)))
        return supervisor

    def kernel_actor(self, pid):
        ticks, status, group, command, phase = process(pid)
        require([int(v) for v in status["Uid"].split()] == [self.actor["uid"]] * 4
                and [int(v) for v in status["Gid"].split()] == [self.actor["gid"]] * 4
                and not status["Groups"].split() and status["NoNewPrivs"].strip() == "1"
                and all(int(status[name], 16) == 0 for name in ("CapInh", "CapPrm", "CapEff", "CapBnd", "CapAmb")))
        return ticks, group, command, phase

    def actor_guard(self):
        self.check()
        require(not self.root_role)
        _, group, _, phase = self.kernel_actor(os.getpid())
        require(phase not in ("T", "t") and re.fullmatch(r"0::/system.slice/pult-call-gate-[0-9a-f]{32}\.service", group))
        c = self.spec["controller"]
        require(Path("/proc/sys/kernel/random/boot_id").read_text().strip() == c["boot"])
        ticks, status, parent_group, _, parent_phase = process(c["pid"])
        require(ticks == c["start_ticks"] and parent_phase not in ("T", "t")
                and [int(v) for v in status["Uid"].split()] == [0] * 4
                and [int(v) for v in status["Gid"].split()] == [0] * 4
                and parent_group == "0::/system.slice/" + c["unit"])
        return group.split("/")[-1]

    def frame(self, status, observation_sha256=None):
        unit = self.actor_guard()
        ticks = process(os.getpid())[0]
        return dict(format=FRAME, approval=self.sha, role=self.role, unit=unit,
                    pid=os.getpid(), start_ticks=ticks, status=status,
                    observation_sha256=observation_sha256, **FLAGS)


class Bound:
    """Fixed trusted root wiring, not an RPC or callback-selected launcher."""

    def __init__(self, profile):
        self.profile = profile
        self.supervisor = profile.root_guard()
        self.unit = self.invocation = self.claim_raw = self.claim_id = None
        self.lock = self.fence = self.pidfd = self.actor_pidfd = self.process = None
        self.used = self.closed = self.submitted = False
        self.clean = self.eof = self.reaped = False
        self.stop = STOP
        self.failure = None

    def command(self, argv):
        return argv  # Fixed trusted CI subclass records owned unit before launch.

    def ready(self, unit, pid):
        pass  # Fixed trusted proof hook only; never selected by a profile/caller.

    def held(self, unit, pid):
        pass  # Same boundary: production performs no fault or extra operation.

    def _guard(self):
        require(not self.closed)
        self.profile.root_guard()
        if self.fence is not None:
            self._fence_check()

    def _fence_check(self):
        p = self.profile
        require(self.fence is not None and identity(os.fstat(self.fence)) == self.claim_id
                and identity((p.control / FENCE).lstat()) == self.claim_id
                and os.pread(self.fence, 4097, 0) == self.claim_raw
                and root_bytes(p.control / FENCE, 4096) == self.claim_raw)
        p.capture("guard").manager_view(p.control, p.spec["control"]["identity"], self.fence)

    def _claim(self):
        p = self.profile
        self.lock = os.open(p.control / LOCK, os.O_RDWR | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC)
        require(identity(os.fstat(self.lock)) == p.spec["control"]["lock_identity"])
        fcntl.flock(self.lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        self._guard()
        self.unit = "pult-call-gate-" + uuid4().hex + ".service"
        require(self.supervisor._state(self.unit).get("LoadState") == "not-found")
        self.claim_raw = canonical(dict(format="pult-working-runtime-stop-claim-v1", approval=p.sha,
                                        runtime_profile_sha256=p.runtime_sha,
                                        controller=p.spec["controller"], role=p.role, unit=self.unit))
        self.fence = os.open(p.control / FENCE, os.O_RDWR | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC, 0o600)
        os.fchmod(self.fence, 0o600)
        self.claim_id = identity(os.fstat(self.fence))
        write_all(self.fence, self.claim_raw)
        os.fsync(self.fence)
        os.fchmod(self.fence, 0o444)
        os.fsync(self.fence)
        sync(p.control)
        self._fence_check()

    def _worker_argv(self):
        p = self.profile
        return ["/usr/bin/python3.12", "-I", "-S", "-B", p.spec["sources"]["worker"]["path"], str(p.file), p.sha]

    def _properties(self):
        p, c = self.profile, self.profile.spec["controller"]["unit"]
        return dict(Type="exec", ExitType="cgroup", RemainAfterExit="yes", User="0", Group="0",
                    SupplementaryGroups="", BindsTo=c, After=c, PartOf=c, RuntimeMaxSec=str(RUNTIME),
                    TimeoutStartSec="2s", TimeoutStopSec=str(STOP), KillMode="control-group",
                    KillSignal="SIGTERM", SendSIGKILL="yes", FinalKillSignal="SIGKILL", Restart="no",
                    NoNewPrivileges="yes", CapabilityBoundingSet="CAP_SETUID CAP_SETGID CAP_SETPCAP",
                    AmbientCapabilities="CAP_SETUID", PrivateNetwork="yes", PrivateTmp="no", PrivateDevices="yes",
                    ProtectSystem="strict", ProtectHome="yes", ProtectControlGroups="yes",
                    RestrictAddressFamilies="AF_UNIX", InaccessiblePaths="/dev/shm /dev/mqueue /dev/pts",
                    ReadWritePaths=str(p.state) if p.state else "", TasksMax="16", MemoryMax="128M",
                    LimitFSIZE="2097152", UMask="0077", ConditionPathExists=str(p.control / FENCE),
                    Description="PULT working runtime " + p.sha, Environment="PATH=/usr/bin:/bin LANG=C.UTF-8")

    def _argv(self):
        actor = self.profile.actor
        args = ["/usr/bin/systemd-run", "--quiet", "--pipe", "--no-ask-password", "--expand-environment=no", "--unit=" + self.unit]
        args += ["--property=" + k + "=" + v for k, v in sorted(self._properties().items())]
        args += ["/usr/bin/setpriv", "--reuid=" + str(actor["uid"]), "--regid=" + str(actor["gid"]),
                 "--clear-groups", "--bounding-set=-all", "--inh-caps=-all", "--ambient-caps=-all", "--no-new-privs",
                 "/usr/bin/env", "-i", "PATH=/usr/bin:/bin", "LANG=C.UTF-8", *self._worker_argv()]
        return args

    def _frame(self, raw, status):
        require(type(raw) is bytes and 0 < len(raw) <= 2048)
        value, p = document(raw), self.profile
        wanted = dict(format=FRAME, approval=p.sha, role=p.role, unit=self.unit,
                      pid=value.get("pid"), start_ticks=value.get("start_ticks"), status=status,
                      observation_sha256=value.get("observation_sha256"), **FLAGS)
        require(value == wanted and set(value) == set(wanted)
                and all(type(value[k]) is int and value[k] > 0 for k in ("pid", "start_ticks"))
                and all(value[k] is False for k in FLAGS))
        require(value["observation_sha256"] is None if status == "READY" else
                type(value["observation_sha256"]) is str and re.fullmatch("[0-9a-f]{64}", value["observation_sha256"]))
        return value

    def _kernel(self, frame):
        p = self.profile
        fields = ("MainPID,InvocationID,Description,ActiveState,SubState,User,Group,BindsTo,After,PartOf,"
                  "RuntimeMaxUSec,TimeoutStopUSec,ExitType,KillMode,KillSignal,FinalKillSignal,SendSIGKILL,"
                  "Restart,NRestarts,PrivateNetwork,ReadWritePaths,ProtectSystem,ProtectHome,PrivateTmp,PrivateDevices,"
                  "ProtectControlGroups,RestrictAddressFamilies,InaccessiblePaths,TasksMax,MemoryMax,LimitFSIZE,"
                  "UMask,SupplementaryGroups,NoNewPrivileges,CapabilityBoundingSet,AmbientCapabilities")
        own = p.show(self.unit, fields)
        wanted = dict(MainPID=str(frame["pid"]), Description="PULT working runtime " + p.sha,
                      ActiveState="active", SubState="running", RuntimeMaxUSec="40s", TimeoutStopUSec="2s",
                      ExitType="cgroup", KillMode="control-group", KillSignal="15", FinalKillSignal="9",
                      SendSIGKILL="yes", Restart="no", NRestarts="0", PrivateNetwork="yes",
                      ReadWritePaths=str(p.state) if p.state else "", ProtectSystem="strict", ProtectHome="yes",
                      PrivateTmp="no", PrivateDevices="yes", ProtectControlGroups="yes", RestrictAddressFamilies="AF_UNIX",
                      TasksMax="16", MemoryMax="134217728", LimitFSIZE="2097152", UMask="0077",
                      SupplementaryGroups="", NoNewPrivileges="yes", AmbientCapabilities="cap_setuid")
        require(all(own.get(k) == v for k, v in wanted.items())
                and own.get("User") in ("0", "root") and own.get("Group") in ("0", "root")
                and set(own.get("CapabilityBoundingSet", "").split()) == {"cap_setuid", "cap_setgid", "cap_setpcap"}
                and set(own.get("InaccessiblePaths", "").split()) == {"/dev/shm", "/dev/mqueue", "/dev/pts"}
                and all(p.spec["controller"]["unit"] in own.get(k, "").split() for k in ("BindsTo", "After", "PartOf"))
                and type(own.get("InvocationID")) is str and re.fullmatch("[0-9a-f]{32}", own["InvocationID"]))
        require(self.invocation is None or self.invocation == own["InvocationID"])
        self.invocation = own["InvocationID"]
        ticks, group, command, phase = p.kernel_actor(frame["pid"])
        require(ticks == frame["start_ticks"] and phase not in ("T", "t")
                and group == "0::/system.slice/" + self.unit
                and command == [os.fsencode(v) for v in self._worker_argv()])
        if self.actor_pidfd is None:
            self.actor_pidfd = os.pidfd_open(frame["pid"])
        signal.pidfd_send_signal(self.actor_pidfd, 0)

    def cleanup(self):
        require(self.unit is not None)
        self._fence_check()
        if self.supervisor._state(self.unit).get("LoadState") != "not-found":
            own = self.profile.show(self.unit, "InvocationID,Description")
            require(own.get("Description") == "PULT working runtime " + self.profile.sha
                    and (self.invocation is None or own.get("InvocationID") == self.invocation))
        self.supervisor._core().Manager.cleanup(self, self.unit)

    def _execute(self):
        selector = selectors.DefaultSelector()
        buffers, ended, frames = {"stdout": bytearray(), "stderr": bytearray()}, set(), []
        final = None
        began = time.monotonic()
        original = None
        failures = []
        close_attempted = set()

        def close_stream(stream):
            if id(stream) not in close_attempted:
                close_attempted.add(id(stream))
                stream.close()

        def pump():
            for key, _ in selector.select(0.025):
                stream = key.fileobj
                try:
                    chunk = os.read(stream.fileno(), 4096)
                except BlockingIOError:
                    continue
                if not chunk:
                    ended.add(key.data)
                    selector.unregister(stream)
                    close_stream(stream)
                else:
                    buffers[key.data].extend(chunk)
                    require(len(buffers[key.data]) <= (8192 if key.data == "stdout" else 512))

        try:
            self._guard()
            require(self.supervisor._state(self.unit).get("LoadState") == "not-found")
            argv = self.command(self._argv())
            self._guard()
            self.submitted = True
            self.process = subprocess.Popen(argv, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                            stderr=subprocess.PIPE, close_fds=True, env=ENV)
            self.pidfd = os.pidfd_open(self.process.pid)
            for name in ("stdout", "stderr"):
                stream = getattr(self.process, name)
                os.set_blocking(stream.fileno(), False)
                selector.register(stream, selectors.EVENT_READ, name)
            while True:
                self._guard()
                pump()
                while b"\n" in buffers["stdout"]:
                    raw, rest = bytes(buffers["stdout"]).split(b"\n", 1)
                    buffers["stdout"][:] = rest
                    require(len(frames) < 3)
                    frame = self._frame(raw + b"\n", ("READY", "HELD", "CLOSED")[len(frames)])
                    if frames:
                        require(all(frame[k] == frames[0][k] for k in ("pid", "start_ticks")))
                    frames.append(frame)
                    if len(frames) <= 2:
                        require(not buffers["stdout"])  # No milestone may precede its root token.
                        self._kernel(frame)
                        (self.ready if len(frames) == 1 else self.held)(self.unit, frame["pid"])
                        self._guard()
                        self._kernel(frame)
                        data = GO if len(frames) == 1 else CLOSE
                        require(os.write(self.process.stdin.fileno(), data) == len(data))
                    else:
                        require(frame["observation_sha256"] == frames[1]["observation_sha256"])
                final = self.supervisor._state(self.unit)
                if final.get("SubState") == "exited" or final.get("ActiveState") in ("failed", "inactive"):
                    break
                require(time.monotonic() - began <= RUNTIME + STOP + 5)
        except BaseException as error:
            original = error
        finally:
            if self.submitted:
                try:
                    self.cleanup()
                    self.clean = True
                except BaseException as error:
                    failures.append(error)
            if self.process is not None:
                try:
                    deadline = time.monotonic() + 2
                    while selector.get_map():
                        require(time.monotonic() < deadline)
                        pump()
                    self.eof = ended == {"stdout", "stderr"}
                    require(self.eof)
                    self.process.wait(timeout=max(0.01, deadline - time.monotonic()))
                    self.reaped = True
                except BaseException as error:
                    failures.append(error)
            try:
                selector.close()
            except BaseException as error:
                failures.append(error)
            if self.process is not None:
                try:
                    if self.process.poll() is None:
                        if self.pidfd is None:
                            self.process.kill()  # Still-owned unreaped direct child only.
                        else:
                            signal.pidfd_send_signal(self.pidfd, signal.SIGKILL)
                except BaseException as error:
                    failures.append(error)
                try:
                    self.process.wait(timeout=2)
                except BaseException as error:
                    failures.append(error)
                for name in ("stdin", "stdout", "stderr"):
                    stream = getattr(self.process, name)
                    if stream is not None and not stream.closed:
                        try:
                            close_stream(stream)
                        except BaseException as error:
                            failures.append(error)
        if original is not None or failures:
            self.failure = (original, tuple(failures))
            raise Unknown("working_runtime_stop_unknown") from None
        # Drain only the terminal CLOSED after cleanup. Never arm a late READY/HELD.
        if len(frames) == 2 and buffers["stdout"].count(b"\n") == 1 and buffers["stdout"].endswith(b"\n"):
            frames.append(self._frame(bytes(buffers["stdout"]), "CLOSED"))
            buffers["stdout"].clear()
        require(len(frames) == 3 and not buffers["stdout"] and not buffers["stderr"]
                and all(frames[2][k] == frames[1][k] for k in ("pid", "start_ticks", "observation_sha256"))
                and self.clean and self.eof and self.reaped and self.process.returncode == 0
                and self.supervisor._empty(self.unit) and final is not None
                and all(final.get(k) == v for k, v in dict(Result="success", ExecMainCode="1",
                                                          ExecMainStatus="0", SubState="exited").items()))
        return frames[-1]["observation_sha256"]

    def run(self):
        global _ATTEMPTED
        require(not _ATTEMPTED and not self.used and not self.closed)
        _ATTEMPTED = self.used = True
        result = None
        failed = False
        try:
            self._guard()
            self._claim()
            result = self._execute()
            self._guard()
        except BaseException as error:
            failed = True
            self.failure = (self.failure, error)
        finally:
            # Each independent close is attempted once; no hidden close retry.
            for name in ("actor_pidfd", "pidfd", "fence", "lock"):
                fd = getattr(self, name)
                if fd is not None:
                    setattr(self, name, None)
                    try:
                        os.close(fd)
                    except BaseException as error:
                        failed = True
                        self.failure = (self.failure, name, fd, error)
            self.closed = True
        empty = False
        try:
            self.profile.root_guard()
            self._fence_check_after_close()
            empty = bool(self.unit and self.supervisor._empty(self.unit))
        except BaseException as error:
            failed = True
            self.failure = (self.failure, error)
        failed = failed or not (result and empty and self.clean and self.eof and self.reaped)
        if failed:
            _FAILED.append(self)
        return dict(format="pult-working-runtime-stop-result-v1", approval=self.profile.sha,
                    role=self.profile.role, unit=self.unit,
                    status="UNKNOWN_REQUIRES_REVIEW" if failed else "WORKING_RUNTIME_STOP_CONFIRMED_NO_OPERATION",
                    cleanup_confirmed=self.clean, cgroup_empty=empty, pipe_eof_confirmed=self.eof,
                    child_reaped=self.reaped, observation_sha256=None if failed else result, **FLAGS)

    def _fence_check_after_close(self):
        require(self.claim_raw is not None
                and identity((self.profile.control / FENCE).lstat()) == self.claim_id
                and root_bytes(self.profile.control / FENCE, 4096) == self.claim_raw)


def prepare(file, independently_approved_sha256):
    try:
        return Bound(Profile(file, independently_approved_sha256, root=True))
    except BaseException as error:
        _FAILED.append(error)
        raise Refused("working_runtime_stop_refused") from None


def main(argv=None):
    try:
        args = sys.argv[1:] if argv is None else argv
        require(len(args) == 2)
        result = prepare(args[0], args[1]).run()
        sys.stdout.buffer.write(canonical(result))
        return 0 if result["status"] == "WORKING_RUNTIME_STOP_CONFIRMED_NO_OPERATION" else 1
    except BaseException:
        print("WORKING_RUNTIME_STOP_REFUSED", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
