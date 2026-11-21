"""Protected synthetic operator profile. No live DB, credentials or write mode.

Only a trusted fixed bootstrap loads this module after capturing approved bytes.
Root/kernel/PID1/system tools and an independently supplied manifest digest are
trust roots. A draft catalogue is not an approved operational manifest.
"""

import hashlib
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import sys
import threading
from types import ModuleType, SimpleNamespace
from uuid import UUID

FORMAT = "pult-offline-operator-host-v1"
MODE = "SYNTHETIC_REVIEW_ENDPOINT_ONLY"
FENCE = (
    "constructor-stop.claim"  # Frozen stateless guard's name; NEW control root/domain.
)
LOCK = "operator-host.lock"
RUNTIME, STOP = 15, 2
GO = b"GO_OFFLINE_REVIEW_V1\n"
FLAGS = dict(
    identity_enrollment_admitted=False,
    journal_dispatch_admitted=False,
    purge_or_restore_admitted=False,
)
SYNTHETIC = dict(
    account_id="c7461350-9188-4bf1-bc6c-c4a5b9455bec",
    draft_id="ff636b10-6ccb-4b1b-9394-df59af74b793",
    closure_epoch="2026-10-21T12:00:00.000000Z",
    reviewed_at="2026-11-21T12:00:00.000000Z",
    policy="normal-close-30d-v1",
)
PATHS = {
    "journal": "ops/backup/erasure_journal.py",
    "anchor": "ops/backup/erasure_anchor.py",
    "ipc": "ops/backup/erasure_custodian.py",
    "operator": "ops/backup/erasure_operator.py",
    "stop": "ops/backup/custodian_v2/stop/constructor_stop.py",
    "supervisor": "ops/backup/custodian_v2/controller/supervisor_v2.py",
    "core": "ops/backup/custodian_v2/controller/frozen/admission.py",
    **{
        name: "ops/backup/operator_host/" + name + ".py"
        for name in ("profile", "host", "bootstrap", "worker")
    },
}
PINS = dict(
    journal="94eeba19e645e25ec8286baebe4092a29c541c6f12858e1ff7b303f6a1a4fc98",
    anchor="b399803ac7a03aaa5a506cbb0036f98c153457769beabf46af44864b190861a8",
    ipc="3eb793a1ef1002c6def97b52c579b2ff051e834b44b802a75aca09d640a691d3",
    operator="c4be8ed53a803afe37cf6c72ac9fa462167ace9401575ee1f85faa2774a1365f",
    stop="5406c5a81498013884ac46d0279219f299db40576357692604fb4f08c251ca8c",
    supervisor="3d05d22163b11f815b29c222128c4e41987c9e4a83e7b4906cbe63a18941a37e",
    core="961cc3aef5dcfe9d6286b15a2d5c087a33dc1e5dafdc7d5a16f6eded961afea7",
)
FIELDS = {
    "format",
    "mode",
    "operation",
    "release",
    "controller",
    "control",
    "actor",
    "operator_uid",
    "ipc",
    "synthetic",
    "sources",
}


class Refused(Exception):
    """Fixed diagnostic only."""


def canonical(value):
    return (
        json.dumps(
            value,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=True,
            allow_nan=False,
        )
        + "\n"
    ).encode("ascii")


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


def document(raw):
    def pairs(rows):
        result = {}
        for key, value in rows:
            if key in result:
                raise Refused("duplicate_profile_field")
            result[key] = value
        return result

    try:
        result = json.loads(raw, object_pairs_hook=pairs)
        if type(result) is not dict or canonical(result) != raw:
            raise Refused("canonical_profile_required")
        return result
    except (ValueError, TypeError, UnicodeError, RecursionError):
        raise Refused("canonical_profile_required") from None


def runtime(root=None):
    if (
        sys.platform != "linux"
        or sys.implementation.name != "cpython"
        or sys.version_info[:2] != (3, 12)
        or not sys.flags.isolated
        or not sys.flags.no_site
        or not sys.dont_write_bytecode
        or sys.flags.optimize
        or Path(sys.executable).resolve() != Path("/usr/bin/python3.12")
        or threading.current_thread() is not threading.main_thread()
        or len(os.listdir("/proc/self/task")) != 1
        or root is True
        and (os.getuid() != 0 or os.geteuid() != 0)
    ):
        raise Refused("fixed_single_thread_runtime_required")


def path(value):
    if type(value) is not str or not re.fullmatch("/[A-Za-z0-9_./-]+", value):
        raise Refused("normalized_root_path_required")
    result = Path(value)
    if str(result) != value or ".." in result.parts or result.anchor != "/":
        raise Refused("normalized_root_path_required")
    return result


def identity(info):
    return dict(device=info.st_dev, inode=info.st_ino)


def ancestors(file, owners=(0,)):
    for parent in file.parents:
        info = parent.lstat()
        sticky = info.st_uid == 0 and bool(info.st_mode & stat.S_ISVTX)
        if (
            not stat.S_ISDIR(info.st_mode)
            or info.st_uid not in owners
            or info.st_mode & 0o022
            and not sticky
            or any(
                name.startswith("system.posix_acl_")
                for name in os.listxattr(parent, follow_symlinks=False)
            )
        ):
            raise Refused("protected_ancestors_required")


def root_bytes(file, maximum=100000, modes=(0o444, 0o644)):
    file = path(str(file))
    ancestors(file)
    fd = os.open(file, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC)
    try:
        before = os.fstat(fd)
        if (
            not stat.S_ISREG(before.st_mode)
            or before.st_uid != 0
            or before.st_nlink != 1
            or stat.S_IMODE(before.st_mode) not in modes
            or not 0 <= before.st_size <= maximum
            or any(name.startswith("system.posix_acl_") for name in os.listxattr(fd))
        ):
            raise Refused("protected_root_descriptor_required")
        raw = os.pread(fd, maximum + 1, 0)
        after = os.fstat(fd)
        if (
            len(raw) != before.st_size
            or any(
                getattr(before, key) != getattr(after, key)
                for key in ("st_dev", "st_ino", "st_size", "st_mtime_ns", "st_ctime_ns")
            )
            or identity(file.lstat()) != identity(before)
        ):
            raise Refused("root_input_changed")
        return raw
    finally:
        os.close(fd)


def inode(file, wanted, uid, gid, mode, directory=False):
    file = path(str(file))
    ancestors(file, (0, uid))
    if (
        type(wanted) is not dict
        or set(wanted) != {"device", "inode"}
        or any(type(value) is not int or value < 0 for value in wanted.values())
        or not wanted["inode"]
    ):
        raise Refused("pinned_inode_required")
    info = file.lstat()
    if (
        not (stat.S_ISDIR if directory else stat.S_ISREG)(info.st_mode)
        or info.st_uid != uid
        or info.st_gid != gid
        or stat.S_IMODE(info.st_mode) != mode
        or identity(info) != wanted
        or not directory
        and info.st_nlink != 1
        or any(
            name.startswith("system.posix_acl_")
            for name in os.listxattr(file, follow_symlinks=False)
        )
    ):
        raise Refused("pinned_inode_changed")


def show(unit, fields):
    if type(unit) is not str or not re.fullmatch(
        "pult-[a-z0-9-]{1,100}\\.service", unit
    ):
        raise Refused("owned_unit_required")
    result = subprocess.run(
        ["/usr/bin/systemctl", "show", unit, "--no-pager", "--property=" + fields],
        stdin=subprocess.DEVNULL,
        capture_output=True,
        timeout=3,
        check=True,
        env={"PATH": "/usr/bin:/bin", "LANG": "C.UTF-8"},
    )
    return dict(
        row.split("=", 1) for row in result.stdout.decode().splitlines() if "=" in row
    )


class Profile:
    """One exact protected offline manifest; no discovery/defaults/activation."""

    def __init__(self, file, approved, blobs):
        runtime()
        self.file, self.raw = path(str(file)), root_bytes(file, 16384)
        if (
            type(approved) is not str
            or not re.fullmatch("[0-9a-f]{64}", approved)
            or digest(self.raw) != approved
        ):
            raise Refused("independent_profile_approval_required")
        self.sha, self.spec = approved, document(self.raw)
        self.modules, self.blobs, self.source_ids = {}, blobs, {}
        self._shape()
        if type(blobs) is not dict or set(blobs) != set(PATHS):
            raise Refused("complete_captured_source_closure_required")
        self.file_identity = identity(self.file.lstat())
        for name, item in self.spec["sources"].items():
            body = blobs[name]
            if type(body) is not bytes or digest(body) != item["sha256"]:
                raise Refused("captured_source_hash_refused")
            self.source_ids[name] = identity(path(item["path"]).lstat())
        self.check()

    def __repr__(self):
        return "<OfflineOperatorProfile no live capability>"

    def _shape(self):
        s = self.spec
        if (
            set(s) != FIELDS
            or s["format"] != FORMAT
            or s["mode"] != MODE
            or s["synthetic"] != SYNTHETIC
        ):
            raise Refused("exact_offline_profile_required")
        if (
            type(s["operation"]) is not str
            or str(UUID(s["operation"])) != s["operation"]
            or UUID(s["operation"]).version != 4
        ):
            raise Refused("canonical_operation_required")
        if any(
            type(s[key]) is not dict
            for key in ("release", "control", "actor", "controller", "ipc", "sources")
        ):
            raise Refused("exact_profile_shape_required")
        if (
            set(s["release"]) != {"root", "identity"}
            or set(s["control"]) != {"root", "identity", "lock_identity"}
            or set(s["ipc"]) != {"path", "identity"}
            or set(s["actor"]) != {"uid", "gid"}
            or set(s["controller"])
            != {"unit", "invocation_id", "pid", "start_ticks", "boot"}
        ):
            raise Refused("exact_profile_shape_required")
        for value in (s["actor"]["uid"], s["actor"]["gid"], s["operator_uid"]):
            if type(value) is not int or not 1000 <= value < 2**31:
                raise Refused("synthetic_principal_required")
        if s["actor"]["uid"] == s["operator_uid"]:
            raise Refused("separate_principals_required")
        c = s["controller"]
        if (
            type(c["unit"]) is not str
            or not re.fullmatch("pult-call-gate-[0-9a-f]{32}\\.service", c["unit"])
            or type(c["invocation_id"]) is not str
            or not re.fullmatch("[0-9a-f]{32}", c["invocation_id"])
            or any(type(c[k]) is not int or c[k] <= 0 for k in ("pid", "start_ticks"))
            or type(c["boot"]) is not str
            or str(UUID(c["boot"])) != c["boot"]
        ):
            raise Refused("explicit_controller_identity_required")
        self.release = path(s["release"]["root"])
        self.control = path(s["control"]["root"])
        self.ipc = path(s["ipc"]["path"])
        dirs = (self.release, self.control, self.ipc)
        if any(
            a == b or a in b.parents or b in a.parents
            for n, a in enumerate(dirs)
            for b in dirs[n + 1 :]
        ):
            raise Refused("separate_owned_directories_required")
        if len(os.fsencode(self.ipc / "review.sock")) > 100 or set(s["sources"]) != set(
            PATHS
        ):
            raise Refused("bounded_exact_source_closure_required")
        for name, relative in PATHS.items():
            item = s["sources"][name]
            if (
                type(item) is not dict
                or set(item) != {"path", "sha256"}
                or item["path"] != str(self.release / relative)
                or type(item["sha256"]) is not str
                or not re.fullmatch("[0-9a-f]{64}", item["sha256"])
                or name in PINS
                and item["sha256"] != PINS[name]
            ):
                raise Refused("approved_source_closure_required")

    def check(self):
        runtime()
        self._shape()
        if (
            root_bytes(self.file, 16384) != self.raw
            or canonical(self.spec) != self.raw
            or identity(self.file.lstat()) != self.file_identity
        ):
            raise Refused("profile_changed")
        s = self.spec
        inode(self.release, s["release"]["identity"], 0, 0, 0o755, True)
        inode(self.control, s["control"]["identity"], 0, 0, 0o755, True)
        inode(self.control / LOCK, s["control"]["lock_identity"], 0, 0, 0o600)
        if os.getuid() == 0 and root_bytes(self.control / LOCK, 1, (0o600,)) != b"":
            raise Refused("empty_root_control_lock_required")
        if not {f.name for f in self.control.iterdir()} <= {LOCK, FENCE}:
            raise Refused("foreign_control_state")
        inode(
            self.ipc,
            s["ipc"]["identity"],
            s["actor"]["uid"],
            s["actor"]["gid"],
            0o750,
            True,
        )
        for name, item in s["sources"].items():
            if (
                digest(self.blobs[name]) != item["sha256"]
                or root_bytes(path(item["path"])) != self.blobs[name]
                or identity(path(item["path"]).lstat()) != self.source_ids[name]
            ):
                raise Refused("source_changed")

    def kernel_actor(self, pid):
        if type(pid) is not int or pid <= 0:
            raise Refused("actor_process_required")
        base = Path("/proc/" + str(pid))
        actor = self.spec["actor"]
        before = base.joinpath("stat").read_text().rsplit(") ", 1)[1].split()
        fields = dict(
            row.split(":", 1)
            for row in base.joinpath("status").read_text().splitlines()
            if ":" in row
        )
        after = base.joinpath("stat").read_text().rsplit(") ", 1)[1].split()
        if (
            before[19] != after[19]
            or after[0] == "Z"
            or [int(v) for v in fields["Uid"].split()] != [actor["uid"]] * 4
            or [int(v) for v in fields["Gid"].split()] != [actor["gid"]] * 4
            or fields["Groups"].split()
            or fields["NoNewPrivs"].strip() != "1"
            or any(
                int(fields[k], 16)
                for k in ("CapEff", "CapPrm", "CapInh", "CapAmb", "CapBnd")
            )
        ):
            raise Refused("fully_unprivileged_actor_required")
        return int(after[19])

    def capture(self, name):
        self.check()
        if name not in ("stop", "supervisor", "host", "worker"):
            raise Refused("fixed_module_role_required")
        if name not in self.modules:
            module = ModuleType("_pult_operator_" + name + "_v1")
            module.__file__ = self.spec["sources"][name]["path"]
            exec(
                compile(
                    self.blobs[name],
                    module.__file__,
                    "exec",
                    dont_inherit=True,
                    optimize=0,
                ),
                module.__dict__,
            )
            self.modules[name] = module
        return self.modules[name]

    def operator_module(self):
        self.check()
        if "operator_core" not in self.modules:
            if any(name == "ops" or name.startswith("ops.") for name in sys.modules):
                raise Refused("foreign_operator_namespace")
            ops = ModuleType("ops")
            ops.__path__ = []
            backup = ModuleType("ops.backup")
            backup.__path__ = []
            ops.backup = backup
            sys.modules["ops"] = ops
            sys.modules["ops.backup"] = backup
            for key, leaf in (
                ("journal", "erasure_journal"),
                ("anchor", "erasure_anchor"),
                ("ipc", "erasure_custodian"),
                ("operator", "erasure_operator"),
            ):
                name = "ops.backup." + leaf
                module = ModuleType(name)
                module.__file__ = self.spec["sources"][key]["path"]
                module.__package__ = "ops.backup"
                sys.modules[name] = module
                setattr(backup, leaf, module)
                exec(
                    compile(
                        self.blobs[key],
                        module.__file__,
                        "exec",
                        dont_inherit=True,
                        optimize=0,
                    ),
                    module.__dict__,
                )
            self.modules["operator_core"] = sys.modules["ops.backup.erasure_operator"]
        return self.modules["operator_core"]

    def root_guard(self):
        runtime(root=True)
        self.check()
        supervisor = self.capture("supervisor")
        old = self.capture("stop")
        supervisor.load_core(path(self.spec["sources"]["core"]["path"]))
        view = SimpleNamespace(
            spec={
                "controller": self.spec["controller"],
                "control": self.spec["control"],
            },
            pid=os.getpid(),
            supervisor=supervisor,
        )
        view._state = lambda unit: supervisor._state(unit)
        view._extra = lambda unit, fields: show(unit, fields)
        old.Bound._controller(view)  # Stateless invariant only; never old run/claim.
        old.manager_view(self.control, self.spec["control"]["identity"])
        return supervisor

    def claim(self, unit=None):
        raw = root_bytes(self.control / FENCE, 4096, (0o444,))
        value = document(raw)
        if (
            set(value) != {"format", "approval", "operation", "controller", "unit"}
            or value["format"] != "pult-offline-operator-claim-v1"
            or value["approval"] != self.sha
            or value["operation"] != self.spec["operation"]
            or value["controller"] != self.spec["controller"]
            or type(value["unit"]) is not str
            or not re.fullmatch("pult-call-gate-[0-9a-f]{32}\\.service", value["unit"])
            or unit is not None
            and value["unit"] != unit
        ):
            raise Refused("exact_operator_claim_required")
        return value

    def worker_policy(self, unit, pid):
        # PID1 metadata is attested by the trusted root parent BEFORE GO. The
        # disposable actor UID intentionally has no NSS account / D-Bus access.
        runtime(root=True)
        c = self.spec["controller"]["unit"]
        fields = (
            "MainPID,InvocationID,User,Group,BindsTo,After,PartOf,RuntimeMaxUSec,TimeoutStopUSec,"
            "ExitType,KillMode,KillSignal,FinalKillSignal,SendSIGKILL,Restart,NRestarts,PrivateNetwork,"
            "ReadWritePaths,ProtectSystem,ProtectHome,PrivateTmp,PrivateDevices,ProtectControlGroups,"
            "RestrictAddressFamilies,TasksMax,MemoryMax,LimitFSIZE,UMask,SupplementaryGroups,"
            "NoNewPrivileges,CapabilityBoundingSet,AmbientCapabilities,Description,ActiveState,SubState"
        )
        own = show(unit, fields)
        wanted = dict(
            ActiveState="active",
            SubState="running",
            MainPID=str(pid),
            RuntimeMaxUSec=str(RUNTIME) + "s",
            TimeoutStopUSec=str(STOP) + "s",
            ExitType="cgroup",
            KillMode="control-group",
            KillSignal="15",
            FinalKillSignal="9",
            SendSIGKILL="yes",
            Restart="no",
            NRestarts="0",
            PrivateNetwork="yes",
            ReadWritePaths=str(self.ipc),
            ProtectSystem="strict",
            ProtectHome="yes",
            PrivateTmp="no",
            PrivateDevices="yes",
            ProtectControlGroups="yes",
            RestrictAddressFamilies="AF_UNIX",
            TasksMax="16",
            MemoryMax="134217728",
            LimitFSIZE="2097152",
            UMask="0077",
            SupplementaryGroups="",
            NoNewPrivileges="yes",
            AmbientCapabilities="cap_setuid",
            Description="PULT offline operator " + self.sha,
        )
        if (
            any(own.get(key) != value for key, value in wanted.items())
            or own.get("User") not in ("0", "root")
            or own.get("Group") not in ("0", "root")
            or set(own.get("CapabilityBoundingSet", "").split())
            != {"cap_setuid", "cap_setgid", "cap_setpcap"}
            or any(
                c not in own.get(k, "").split() for k in ("BindsTo", "After", "PartOf")
            )
            or type(own.get("InvocationID")) is not str
            or not re.fullmatch("[0-9a-f]{32}", own["InvocationID"])
        ):
            raise Refused("kernel_actor_lifecycle_refused")
        self.kernel_actor(pid)
        if (
            Path("/proc/" + str(pid) + "/cgroup").read_text().strip()
            != "0::/system.slice/" + unit
        ):
            raise Refused("original_actor_cgroup_required")
        return own["InvocationID"]

    def actor_guard(self):
        self.check()
        s = self.spec
        c = s["controller"]
        self.kernel_actor(os.getpid())
        claim = self.claim()
        unit = claim["unit"]
        if Path("/proc/self/cgroup").read_text().strip() != "0::/system.slice/" + unit:
            raise Refused("original_actor_cgroup_required")
        if Path("/proc/sys/kernel/random/boot_id").read_text().strip() != c["boot"]:
            raise Refused("controller_boot_changed")
        parent = Path("/proc/" + str(c["pid"]))
        before = parent.joinpath("stat").read_text().rsplit(") ", 1)[1].split()
        credentials = dict(
            row.split(":", 1)
            for row in parent.joinpath("status").read_text().splitlines()
            if ":" in row
        )
        parent_group = parent.joinpath("cgroup").read_text().strip()
        after = parent.joinpath("stat").read_text().rsplit(") ", 1)[1].split()
        if (
            int(before[19]) != c["start_ticks"]
            or before[19] != after[19]
            or after[0] in ("T", "t", "Z", "X", "x")
            or [int(v) for v in credentials["Uid"].split()] != [0] * 4
            or [int(v) for v in credentials["Gid"].split()] != [0] * 4
            or parent_group != "0::/system.slice/" + c["unit"]
        ):
            raise Refused("original_controller_unavailable")
        return unit
