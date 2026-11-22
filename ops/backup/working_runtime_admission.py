"""One fresh, held local runtime admission; not a journal-operation permit.

Trusted OS/root, this entry, fixed CPython/stdlib and independent profile SHA
remain trust roots. The unchanged offline checker is a pinned helper, never a
saved receipt or an admission factory. Operational state is never initialized.
"""
import hashlib
from importlib.machinery import BuiltinImporter, FrozenImporter, PathFinder
import importlib
from ipaddress import IPv4Address
import os
from pathlib import Path
import stat
import sys
import threading
import time
from types import ModuleType

FORMAT = "pult-working-runtime-admission-v1"
ENTRY = "ops/backup/working_runtime_admission.py"
CHECKER = "ops/backup/working_launch_profile.py"
CHECKER_SHA = "01d4023ff8c3c2d44a481d2ee034704c4584259846592e23a3072be0bbf908da"
PAYLOAD = "ops/backup/working_runtime.py"
# The working payload pin is filled only after independent source review.
ADDITIONS = {
    CHECKER: "01d4023ff8c3c2d44a481d2ee034704c4584259846592e23a3072be0bbf908da",
    "ops/backup/custodian_v2/candidate/anchor_v2.py": "ce6bff676dd2d0eccc5857efe34316fdeb644fdb91df7fff20ef40f77dc17d73",
    "ops/backup/custodian_v2/ownership/owned_host.py": "a9d71db50be14bd9d57c2535748907a26bc75eabfe3af9d8e203f77a6f434bc3",
    "ops/backup/custodian_v2/ownership/host_core.py": "da3754a860495a5c193daaf27cde3c04b95c55fc58d37dea60af37a58626e1e8",
    "ops/backup/erasure_anchor.py": "b399803ac7a03aaa5a506cbb0036f98c153457769beabf46af44864b190861a8",
    "ops/backup/custodian_v2/synthetic_ssh_s3/live_event_store.py": "2e36b7e064b9ad8dbf1c324fcb57aee0b8e520f71f49c0c5ec6d01d38ba6ed84",
    PAYLOAD: "1e6dbd4fe3ad9897bfac8b91655fd2c3ec38155ac0bdd4149d38c16ee421f49a",
}
MODULES = {
    "event_codec": "ops/backup/custodian_v2/codec/event_codec.py",
    "anchor_v2": "ops/backup/custodian_v2/candidate/anchor_v2.py",
    "owned_host": "ops/backup/custodian_v2/ownership/owned_host.py",
    "host_core": "ops/backup/custodian_v2/ownership/host_core.py",
    "working_live_store": "ops/backup/custodian_v2/synthetic_ssh_s3/live_event_store.py",
    "ops.backup.erasure_anchor": "ops/backup/erasure_anchor.py",
    "ops.backup.working_runtime": PAYLOAD,
}
FIELDS = {"format", "release", "entry_sha256", "role", "actors", "namespace", "peer_ipv4", "resources"}
MAX_SECONDS = 40
ENVIRONMENT = {"PATH": "/usr/bin:/bin", "LANG": "C.UTF-8"}
_ATTEMPTED = False
_FAILED = []


class Refused(Exception):
    """No private source, state, identity or credential diagnostics."""


def require(value):
    if not value:
        raise Refused("working_runtime_refused")


def _digest(raw):
    return hashlib.sha256(raw).hexdigest()


def _bootstrap_checker():
    """Small fixed trust-root read, before any reusable helper executes."""
    require(dict(os.environ) == ENVIRONMENT)
    require(sys.platform == "linux" and sys.implementation.name == "cpython"
            and sys.version_info[:2] == (3, 12)
            and os.path.realpath(sys.executable) == "/usr/bin/python3.12"
            and sys.prefix == sys.base_prefix == "/usr"
            and sys.flags.isolated and sys.flags.no_site and sys.flags.no_user_site
            and sys.flags.ignore_environment and sys.flags.safe_path
            and sys.flags.dont_write_bytecode and sys.dont_write_bytecode and not sys.flags.optimize
            and threading.current_thread() is threading.main_thread()
            and tuple(sys.meta_path) == (BuiltinImporter, FrozenImporter, PathFinder))
    root = Path(__file__).absolute().parents[2]
    require(Path(__file__).absolute() == root / ENTRY)
    file = root / CHECKER
    descriptors, failed = [], False
    try:
        current = os.open("/", os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC)
        descriptors.append(current)
        for part in (None, *file.parts[1:-1]):
            if part is not None:
                current = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC,
                                  dir_fd=current)
                descriptors.append(current)
            info = os.fstat(current)
            require(stat.S_ISDIR(info.st_mode) and info.st_uid == 0
                    and (not info.st_mode & 0o022 or info.st_mode & stat.S_ISVTX)
                    and not any(n.startswith("system.posix_acl_") for n in os.listxattr(current)))
        fd = os.open(file.name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC,
                     dir_fd=current)
        descriptors.append(fd)
        before = os.fstat(fd)
        require(stat.S_ISREG(before.st_mode) and before.st_uid == 0 and before.st_nlink == 1
                and stat.S_IMODE(before.st_mode) == 0o444 and 0 < before.st_size <= 600000
                and not any(n.startswith("system.posix_acl_") for n in os.listxattr(fd)))
        raw = os.pread(fd, 600001, 0)
        after, linked = os.fstat(fd), os.stat(file.name, dir_fd=current, follow_symlinks=False)
        names = ("st_dev", "st_ino", "st_mode", "st_uid", "st_gid", "st_nlink", "st_size", "st_mtime_ns", "st_ctime_ns")
        require(len(raw) == before.st_size and _digest(raw) == CHECKER_SHA
                and all(getattr(before, n) == getattr(after, n) == getattr(linked, n) for n in names))
    finally:
        for fd in reversed(descriptors):
            try:
                os.close(fd)
            except OSError:
                failed = True
        require(not failed)
    module = ModuleType("_pult_fixed_working_checker")
    module.__file__ = str(file)
    exec(compile(raw, str(file), "exec", dont_inherit=True, optimize=0), module.__dict__)
    module.fresh_interpreter()
    require("working_live_store" not in sys.modules)
    return root, module


def _identity(value):
    require(type(value) is dict and set(value) == {"device", "inode"}
            and all(type(x) is int and x >= 0 for x in value.values()) and value["inode"] > 0)


def _shape(checker, spec, profile_file):
    require(set(spec) == FIELDS and spec["format"] == FORMAT
            and type(spec["role"]) is str and spec["role"] in ("custodian", "writer"))
    actors = spec["actors"]
    require(type(actors) is dict and set(actors) == {"custodian", "writer"})
    for actor in actors.values():
        require(type(actor) is dict and set(actor) == {"uid", "gid"}
                and all(type(x) is int and 1000 <= x < 2**31 for x in actor.values()))
    require(actors["custodian"]["uid"] != actors["writer"]["uid"]
            and actors["custodian"]["gid"] != actors["writer"]["gid"])
    # Reuse the predecessor's exact root/namespace grammar, not its receipt.
    common = {key: spec[key] for key in ("release", "entry_sha256", "namespace")}
    common.update(format=checker.FORMAT, actor=actors[spec["role"]])
    root = checker.shape(common)
    require(not profile_file.is_relative_to(root))
    peer = IPv4Address(spec["peer_ipv4"])
    require(type(spec["peer_ipv4"]) is str and str(peer) == spec["peer_ipv4"]
            and peer.is_global and not peer.is_multicast)
    resources = spec["resources"]
    expected_keys = {"credential"} | ({"state", "lease", "expected"} if spec["role"] == "custodian" else set())
    require(type(resources) is dict and set(resources) == expected_keys)
    credential = resources["credential"]
    require(type(credential) is dict and set(credential) == {"path", "identity", "access_sha256"})
    checker.sha(credential["access_sha256"])
    paths = [checker.path(credential["path"])]
    _identity(credential["identity"])
    if spec["role"] == "custodian":
        for name in ("state", "lease"):
            row = resources[name]
            require(type(row) is dict and set(row) == {"path", "identity"})
            paths.append(checker.path(row["path"]))
            _identity(row["identity"])
        state, lease = paths[1:]
        require(state.name == "anchor.sqlite" and lease == state.parent / "owner.lock")
        require(state.parent != paths[0].parent
                and not state.parent.is_relative_to(paths[0].parent)
                and not paths[0].parent.is_relative_to(state.parent))
        expected = resources["expected"]
        require(type(expected) is dict and set(expected) == {"sequence", "digest", "revision"}
                and type(expected["sequence"]) is int and 0 <= expected["sequence"] <= 1024
                and type(expected["revision"]) is int and expected["revision"] == expected["sequence"] * 3)
        checker.sha(expected["digest"])
    for file in paths:
        require(not file.is_relative_to(root) and not root.is_relative_to(file.parent)
                and profile_file != file and not profile_file.is_relative_to(file.parent))
    return root


def _actor(checker, spec):
    uid, gid = checker.runtime()
    require(spec["actors"][spec["role"]] == {"uid": uid, "gid": gid}
            and set(os.getgroups()).issubset({gid}))


def _tables(checker, root, blobs, dependencies):
    modules, namespaces = checker.module_tables(root, blobs, dependencies)
    for name, relative in MODULES.items():
        require(name not in modules)
        file = root / relative
        modules[name] = (file, blobs[relative], False)
        parts = name.split(".")
        for index in range(1, len(parts)):
            namespaces.setdefault(".".join(parts[:index]), file.parents[len(parts) - index - 1])
    require(all(name not in modules or modules[name][2] for name in namespaces))
    return modules, namespaces


class RuntimeAdmission:
    """Retained source/resource owner, never a remote operation capability."""

    def __init__(self, checker, captured, raw, approved, deadline):
        self._checker, self._capture, self._raw, self._approved = checker, captured, raw, approved
        self._pid, self._thread = os.getpid(), threading.current_thread()
        self._deadline = deadline
        self._finder = self._resources = None
        self._phase = "capturing"
        self._failed = self._closed = False
        self._failure = None

    def __repr__(self):
        return "<WorkingRuntimeAdmission local construction; operations closed>"

    def _poison(self, error):
        if not self._failed:
            self._failed = True
            self._failure = error
            _FAILED.append(self)

    def _guard(self):
        try:
            require(not self._failed and not self._closed and os.getpid() == self._pid
                    and threading.current_thread() is self._thread
                    and time.monotonic() <= self._deadline
                    and self._phase in ("active", "closing", "unloading"))
            checker = self._checker
            require(dict(os.environ) == ENVIRONMENT)
            require(_digest(self._raw) == self._approved)
            _actor(checker, checker.document(self._raw))
            self._capture.check()
            if self._phase == "unloading":
                checker.fresh_interpreter()
                require("working_live_store" not in sys.modules)
            else:
                self._finder.check()
                names = checker.PAYLOAD_NAMES | {"working_live_store"}
                require({n for n in sys.modules if n.split(".", 1)[0] in names} == set(self._finder.owned))
            return True
        except BaseException as error:
            self._poison(error)
            raise Refused("working_runtime_refused") from None

    def check(self):
        try:
            require(self._phase == "active")
            self._guard()
            self._resources.check()
            self._guard()
            return True
        except BaseException as error:
            self._poison(error)
            raise Refused("working_runtime_refused") from None

    def snapshot(self):
        try:
            self.check()
            result = self._resources.snapshot()
            self.check()
            return result
        except BaseException as error:
            self._poison(error)
            raise Refused("working_runtime_refused") from None

    def close(self):
        if self._closed:
            require(os.getpid() == self._pid and threading.current_thread() is self._thread)
            return
        try:
            self.check()
            self._phase = "closing"
            self._resources.close_client()
            self._guard()
            self._finder.rollback()
            self._phase = "unloading"
            self._guard()
            # Lease and private FDs stay owned through client cleanup and unload.
            self._resources.close()
            self._guard()
            self._capture.close()
            self._phase, self._closed = "closed", True
        except BaseException as error:
            self._poison(error)
            raise Refused("working_runtime_refused") from None


def load_runtime(file, independently_approved_sha256):
    """Trusted fixed entry only. No source/profile repair, callback or retry."""
    global _ATTEMPTED
    captured = owner = None
    try:
        require(not _ATTEMPTED)
        _ATTEMPTED = True
        deadline = time.monotonic() + MAX_SECONDS
        root, checker = _bootstrap_checker()
        file, approved = checker.path(file), checker.sha(independently_approved_sha256)
        helper_file = root / checker.CAPTURE
        helper = checker._module("_pult_working_runtime_capture", helper_file, checker._helper_bytes(helper_file))
        captured = helper.Capture()
        raw = captured.take(file, 20000)
        require(_digest(raw) == approved)
        spec = checker.document(raw)
        require(_shape(checker, spec, file) == root)
        _actor(checker, spec)
        identity = captured.directory(root)
        require(spec["release"]["identity"] == {"device": identity[0], "inode": identity[1]})
        pins = dict(checker.PINS)
        require(not set(pins) & set(ADDITIONS))
        pins.update(ADDITIONS)
        blobs = {name: captured.take(root / name, 600000) for name in pins}
        require(all(_digest(blobs[name]) == wanted for name, wanted in pins.items()))
        require(_digest(captured.take(root / ENTRY, 600000)) == spec["entry_sha256"])
        captured.check()
        cold = checker._module("_pult_working_runtime_import", root / checker.COLD, blobs[checker.COLD])
        cold._runtime()
        wheels = {name: captured.take(root / "wheels" / name, 2 * 1024 * 1024) for name in cold.WHEELS}
        dependencies, directories = checker.wheel_inventory(cold, wheels)
        for name, body in dependencies.items():
            require(captured.take(root / "deps" / name, 8 * 1024 * 1024) == body)
        files = set(pins) | {ENTRY} | {"wheels/" + n for n in wheels} | {"deps/" + n for n in dependencies}
        checker.closed_layout(captured, root, files, {"deps/" + n for n in directories})
        captured.check()
        modules, namespaces = _tables(checker, root, blobs, dependencies)
        owner = RuntimeAdmission(checker, captured, raw, approved, deadline)
        owner._finder = cold._Finder(modules, namespaces)
        checker.fresh_interpreter()
        require("working_live_store" not in sys.modules)
        sys.meta_path.insert(0, owner._finder)
        owner._phase = "active"
        owner._guard()
        payload = importlib.import_module("ops.backup.working_runtime")
        require(payload.V2Anchor is sys.modules["anchor_v2"].V2Anchor
                and payload.Lease is sys.modules["owned_host"].Lease
                and payload.EventReader is sys.modules["working_live_store"].EventReader
                and payload.EventWriter is sys.modules["working_live_store"].EventWriter
                and sys.modules["anchor_v2"].Lease is sys.modules["owned_host"].Lease
                and sys.modules["anchor_v2"].Binding is sys.modules["event_codec"].Binding
                and sys.modules["working_live_store"].Binding is sys.modules["event_codec"].Binding)
        owner._guard()
        # Retain the graph before opaque construction may raise.
        owner._resources = payload.Resources.__new__(payload.Resources)
        payload.Resources.__init__(owner._resources, raw, owner._guard)
        owner.check()
        return owner
    except BaseException as error:
        if owner is not None:
            owner._poison(error)
        elif captured is not None:
            try:
                captured.close()
            except BaseException as close_error:
                _FAILED.append((captured, error, close_error))
        raise Refused("working_runtime_refused") from None


def main(argv=None):
    try:
        args = sys.argv[1:] if argv is None else argv
        require(len(args) == 3 and args[0] == "check")
        owner = load_runtime(args[1], args[2])
        result = owner.snapshot()
        owner.close()
        result = dict(result, lease_held=False, cleanup_confirmed=True,
                      status="LOCAL_RUNTIME_CHECKED_AND_CLOSED_NOT_OPERATIONAL")
        sys.stdout.buffer.write(owner._checker.canonical(result))
        return 0
    except BaseException:
        print("WORKING_RUNTIME_REFUSED", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
