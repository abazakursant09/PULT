"""Fixed disposable actor for local construction/stop; never dispatch authority.

Only stdlib is imported before the independently approved Profile and runtime
closure are captured. Failure retains the original graph until process exit.
"""
import hashlib
from importlib.machinery import BuiltinImporter, FrozenImporter, PathFinder
import json
import os
from pathlib import Path
import re
import select
import stat
import sys
import threading
import time
from types import ModuleType

WORKER = "ops/backup/working_runtime_stop_worker.py"
STOP = "ops/backup/working_runtime_stop.py"
ENVIRONMENT = {"PATH": "/usr/bin:/bin", "LANG": "C.UTF-8"}
GO = b"GO_WORKING_RUNTIME_V1\n"
CLOSE = b"CLOSE_WORKING_RUNTIME_V1\n"
FLAGS = {"operation_admitted", "identity_enrollment_admitted", "journal_dispatch_admitted",
         "purge_or_restore_admitted", "remote_io_admitted", "launch_admitted"}
FIELDS = {"format", "mode", "sources", "runtime_profile", "controller", "control", "role", "actor"}
SOURCES = {"stop", "worker", "runtime", "guard", "supervisor", "core"}
_ATTEMPTED = False
_CONTEXT = {}
_FAILED = []


class Refused(Exception):
    """Fixed private-input-safe failure only."""


def require(value):
    if not value:
        raise Refused("working_runtime_stop_worker_refused")


def canonical(value):
    return (json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True,
                       allow_nan=False) + "\n").encode("ascii")


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


def document(raw):
    def pairs(rows):
        value = {}
        for key, item in rows:
            require(key not in value)
            value[key] = item
        return value
    value = json.loads(raw.decode("ascii"), object_pairs_hook=pairs,
                       parse_constant=lambda value: require(False))
    require(type(value) is dict and canonical(value) == raw)
    return value


def path(value):
    require(type(value) is str and 1 <= len(value) <= 4096
            and re.fullmatch(r"/[A-Za-z0-9_./-]+", value) is not None)
    file = Path(value)
    require(str(file) == value and ".." not in file.parts and file != Path("/"))
    return file


def _environment():
    require(dict(os.environ) == ENVIRONMENT)


def _initial_runtime():
    _environment()  # No protected-file read before the exact environment check.
    require(sys.platform == "linux" and sys.implementation.name == "cpython"
            and sys.version_info[:2] == (3, 12)
            and os.path.realpath(sys.executable) == "/usr/bin/python3.12"
            and sys.prefix == sys.base_prefix == "/usr"
            and sys.flags.isolated and sys.flags.no_site and sys.flags.no_user_site
            and sys.flags.ignore_environment and sys.flags.safe_path
            and sys.flags.dont_write_bytecode and sys.dont_write_bytecode and not sys.flags.optimize
            and threading.current_thread() is threading.main_thread()
            and tuple(sys.meta_path) == (BuiltinImporter, FrozenImporter, PathFinder)
            and not any(n.split(".", 1)[0] in {"ops", "anchor_v2", "protocol_v2", "event_codec",
                "event_store", "owned_host", "host_core", "working_live_store", "httpx", "httpcore",
                "anyio", "certifi", "h11", "idna", "typing_extensions"} for n in sys.modules))


def _stamp(info):
    names = ("st_dev", "st_ino", "st_mode", "st_uid", "st_gid")
    if not (stat.S_ISDIR(info.st_mode) and info.st_uid == 0 and info.st_mode & stat.S_ISVTX):
        names += ("st_nlink", "st_size", "st_mtime_ns", "st_ctime_ns")
    return tuple(getattr(info, name) for name in names)


def _acl(fd):
    require(not any(n.startswith("system.posix_acl_") for n in os.listxattr(fd)))


def _root_read(file, maximum):
    """Bounded retained-chain read; no FIFO/symlink read or uncertain close retry."""
    descriptors, nodes = [], []
    try:
        current = os.open("/", os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC)
        descriptors.append(current)
        for part in (None, *file.parts[1:-1]):
            parent = current
            if part is not None:
                current = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC,
                                  dir_fd=parent)
                descriptors.append(current)
            info = os.fstat(current)
            require(stat.S_ISDIR(info.st_mode) and info.st_uid == 0
                    and (not info.st_mode & 0o022 or info.st_mode & stat.S_ISVTX))
            _acl(current)
            nodes.append((current, parent, part, info))
        fd = os.open(file.name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC,
                     dir_fd=current)
        descriptors.append(fd)
        before = os.fstat(fd)
        require(stat.S_ISREG(before.st_mode) and before.st_uid == 0 and before.st_nlink == 1
                and stat.S_IMODE(before.st_mode) in (0o444, 0o644) and 0 < before.st_size <= maximum)
        _acl(fd)
        raw = os.pread(fd, maximum + 1, 0)
        require(len(raw) == before.st_size and _stamp(before) == _stamp(os.fstat(fd))
                == _stamp(os.stat(file.name, dir_fd=current, follow_symlinks=False)))
        _acl(fd)
        for node, parent, part, previous in nodes:
            require(_stamp(previous) == _stamp(os.fstat(node)))
            if part is not None:
                require(_stamp(previous) == _stamp(os.stat(part, dir_fd=parent, follow_symlinks=False)))
            _acl(node)
        return raw
    finally:
        original, errors = sys.exc_info()[1], []
        for fd in reversed(descriptors):
            try:
                os.close(fd)
            except BaseException as error:
                errors.append(error)
        if errors:
            _FAILED.append((original, tuple(errors), tuple(descriptors)))
            raise Refused("working_runtime_stop_worker_refused") from None


def _load_profile(file, approved):
    _initial_runtime()
    file = path(file)
    require(type(approved) is str and re.fullmatch("[0-9a-f]{64}", approved) is not None)
    raw = _root_read(file, 20000)
    require(digest(raw) == approved)
    spec = document(raw)
    require(set(spec) == FIELDS and spec["format"] == "pult-working-runtime-stop-v1"
            and spec["mode"] == "LOCAL_ROLE_CONSTRUCTION_ONLY"
            and type(spec["sources"]) is dict and set(spec["sources"]) == SOURCES)
    source_paths = []
    for row in spec["sources"].values():
        require(type(row) is dict and set(row) == {"path", "sha256"}
                and type(row["sha256"]) is str and re.fullmatch("[0-9a-f]{64}", row["sha256"]) is not None)
        source_paths.append(path(row["path"]))
    require(len(set(source_paths)) == len(SOURCES))
    own = Path(__file__).absolute()
    root = own.parents[2]
    require(own == root / WORKER and path(spec["sources"]["worker"]["path"]) == own
            and path(spec["sources"]["stop"]["path"]) == root / STOP)
    require(digest(_root_read(own, 600000)) == spec["sources"]["worker"]["sha256"])
    stop_file = root / STOP
    source = _root_read(stop_file, 600000)
    require(digest(source) == spec["sources"]["stop"]["sha256"])
    module = ModuleType("_pult_working_runtime_stop")
    module.__file__ = str(stop_file)
    _CONTEXT["stop"] = module  # Root the module before approved definitions execute.
    exec(compile(source, str(stop_file), "exec", dont_inherit=True, optimize=0), module.__dict__)
    profile = module.Profile(file, approved, root=False)
    _CONTEXT["profile"] = profile
    require(profile.raw == raw and profile.sha == approved and profile.spec == spec)
    return profile


def _guard(profile):
    _environment()
    require(os.getpid() == _CONTEXT["pid"] and threading.current_thread() is _CONTEXT["thread"])
    profile.check()
    profile.actor_guard()


def _parent():
    _environment()
    require(stat.S_ISFIFO(os.fstat(0).st_mode))
    poller = select.poll()
    poller.register(0, select.POLLIN | select.POLLHUP | select.POLLERR | select.POLLNVAL)
    return poller


def _parent_live():
    require(not _parent().poll(0))


def _wait_token(token):
    poller = _parent()
    deadline, body = time.monotonic() + 2, bytearray()
    while bytes(body) != token:
        _environment()
        left = deadline - time.monotonic()
        require(left > 0)
        events = poller.poll(max(1, min(2000, int(left * 1000))))
        if not events:
            continue
        require(all(fd == 0 and mask & select.POLLIN
                    and not mask & (select.POLLHUP | select.POLLERR | select.POLLNVAL)
                    for fd, mask in events))
        chunk = os.read(0, len(token) + 1 - len(body))
        require(bool(chunk))
        body.extend(chunk)
        require(token.startswith(body))
    _parent_live()  # A queued token plus parent EOF must never arm the actor.


def _emit(profile, status, observation=None):
    value = profile.frame(status, observation)
    require(type(value) is dict and set(value) == FLAGS | {"format", "approval", "role", "unit", "pid",
        "start_ticks", "status", "observation_sha256"}
        and value["format"] == "pult-working-runtime-stop-worker-v1" and value["approval"] == profile.sha
        and value["role"] == profile.role and value["status"] == status
        and value["pid"] == os.getpid() and type(value["pid"]) is int
        and value["observation_sha256"] == observation and all(value[k] is False for k in FLAGS))
    raw = canonical(value)
    require(0 < len(raw) <= 2048 and os.write(1, raw) == len(raw))


def held(owner):
    """Trusted native proof hook only; never selected by a profile/caller."""


def main(argv=None):
    global _ATTEMPTED
    try:
        require(not _ATTEMPTED)
        _ATTEMPTED = True
        _CONTEXT.update(pid=os.getpid(), thread=threading.current_thread())
        args = sys.argv[1:] if argv is None else argv
        require(len(args) == 2)
        profile = _load_profile(*args)
        _guard(profile)
        _emit(profile, "READY")
        _wait_token(GO)
        _guard(profile)
        _parent_live()
        runtime = profile.runtime_module()
        _CONTEXT["runtime"] = runtime
        _guard(profile)
        _parent_live()
        inner = profile.spec["runtime_profile"]
        owner = runtime.load_runtime(inner["path"], inner["sha256"])
        _CONTEXT["owner"] = owner
        snapshot = owner.snapshot()
        require(type(snapshot) is dict and all(snapshot.get(k) is False for k in FLAGS))
        observation = digest(canonical(snapshot))
        _guard(profile)
        _parent_live()
        _emit(profile, "HELD", observation)
        held(owner)
        _wait_token(CLOSE)
        _guard(profile)
        _parent_live()
        owner.close()
        _guard(profile)
        _parent_live()
        _emit(profile, "CLOSED", observation)
        return 0
    except BaseException as error:
        _FAILED.append(error)  # Includes the original traceback and opaque partial graph.
        try:
            print("WORKING_RUNTIME_STOP_WORKER_REFUSED", file=sys.stderr)
        except BaseException as diagnostic_error:
            _FAILED.append(diagnostic_error)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
