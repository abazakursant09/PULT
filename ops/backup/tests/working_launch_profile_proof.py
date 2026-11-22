"""Native, offline source-profile proof. Never launches a working service.

Run only after review: sudo -n -- unshare --net /usr/bin/python3.12 -I -S -B
ops/backup/tests/working_launch_profile_proof.py [--wheels-dir DIRECTORY]
Portable mocks and injected close/rollback failures are not kernel evidence here.
"""

import ast
import errno
import hashlib
import io
import json
import os
from pathlib import Path, PurePosixPath
import selectors
import shutil
import signal
import stat
import struct
import subprocess
import sys
import tempfile
import threading
import time
from types import ModuleType
import zipfile

ENTRY = "ops/backup/working_launch_profile.py"
CAPTURE = "ops/backup/operator_pg/bootstrap.py"
COLD = "ops/backup/custodian_v2/cold/cold_host.py"
# Independent accepted inputs: do not derive approval from the runtime's map.
PINS = {
    CAPTURE: "419fd3b888e1824a916ca1072836d23722edc60ea27027bf17bafe0de64689c4",
    COLD: "091133edcafb1110e13e6a79f51d336d9a152f2bf3f96d485b2c38d3899f438c",
    "ops/backup/working_journal_v2.py": "d2138f1c572bd8d074c88de3db6c7535077fd82e9b4dcd3f000bcd758fccc83c",
    "ops/backup/custodian_v2/codec/event_codec.py": "c33ad596b91fe927df85fca61ae32d35bb1cf3a1d02da2b09dadb69fc361d04c",
    "ops/backup/erasure_journal_s3.py": "41c8da982e016730c740f826fa0ff1ebf00923d8f067fb86757783fc0773ca1d",
    "ops/backup/erasure_journal.py": "94eeba19e645e25ec8286baebe4092a29c541c6f12858e1ff7b303f6a1a4fc98",
    "ops/canary/canary.py": "aa4b357acc6e908341c5523db8f93a32fdf3c0305a7ddc36c4c68842fbe3ee4f",
    "ops/backup/custodian/requirements.lock": "cd1f0a681aae91a132427b88897782b0f2085c8a415cd39812f8e7a059361f08",
}
WHEELS = {
    "anyio-4.14.2-py3-none-any.whl": "9f505dda5ac9f0c8309b5e8bd445a8c2bf7246f3ce950121e45ea15bc41d1494",
    "certifi-2026.10.19-py3-none-any.whl": "42d35340cc5b9ba78e818ddbff0c5b1c2b779de448a340fd1b989f5fa8670e1b",
    "h11-0.16.0-py3-none-any.whl": "63cf8bbe7522de3bf65932fda1d9c2772064ffb3dae62d55932da54b31cb6c86",
    "httpcore-1.0.9-py3-none-any.whl": "2d400746a40668fc9dec9810239072b40b4484b640a8c38fd654a024c7a1bf55",
    "httpx-0.28.1-py3-none-any.whl": "d909fcccc110f8c7faf814ca82a9a4d816bc5a6dbfea25d6591d6985b8ba59ad",
    "idna-3.18-py3-none-any.whl": "7f952cbe720b688055e3f87de14f5c3e5fdaa8bc3928985c4077ca689de849a2",
    "typing_extensions-4.16.0-py3-none-any.whl": "481caa481374e813c1b176ada14e97f1f67a4539ce9cfeb3f350d78d6370c2e8",
}
UID, GID = 46201, 46202
PYTHON = "/usr/bin/python3.12"
SETPRIV = "/usr/bin/setpriv"
ENV = {"PATH": "/usr/bin:/bin", "LANG": "C.UTF-8"}
PAYLOAD = {"ops", "anchor_v2", "protocol_v2", "event_codec", "event_store",
           "owned_host", "host_core", "anyio", "certifi", "h11", "httpcore",
           "httpx", "idna", "typing_extensions"}
FLAGS = ("credentials_opened", "state_opened", "live_clients_created", "launch_admitted",
         "journal_dispatch_admitted", "purge_or_restore_admitted", "remote_io_admitted")
FORBIDDEN_EVENTS = ("socket.__new__", "socket.connect", "socket.bind", "socket.getaddrinfo",
                    "sqlite3.connect", "subprocess.Popen", "os.system", "os.fork", "os.posix_spawn")
CASES = (
    "marker_control", "authorized", "wrong_profile_sha", "bad_format", "extra_profile_field",
    "wrong_actor", "wrong_inode", "extra_app_source", "extra_empty_dependency_directory",
    "helper_substitution", "payload_substitution", "dependency_substitution", "wheel_substitution",
    "profile_symlink", "profile_hardlink", "profile_fifo", "profile_owner", "profile_mode",
    "profile_acl", "source_symlink", "source_hardlink", "source_fifo", "source_owner",
    "source_mode", "source_acl", "preloaded_module", "missing_isolation", "thread_owner",
    "bounding_capability", "poison_environment_ignored",
)
ACCEPT = {"authorized", "poison_environment_ignored"}
MAX_OUTPUT = 16384
CHILD_TIMEOUT = 25
COMPLETED = []
CURRENT_CASE = None
CLEANUP_CONFIRMED = False
CHILD_CLEANUP_CONFIRMED = True
INPUT_CLEANUP_CONFIRMED = True
FIXTURE_PRESERVED = False
CANCELLATION_OBSERVED = False
OWNED_CHILDREN = []


class ProofError(Exception):
    pass


def need(value):
    if not value:
        raise ProofError("proof_contract_failed")


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def canonical(value):
    return (json.dumps(value, sort_keys=True, separators=(",", ":"),
                       ensure_ascii=True, allow_nan=False) + "\n").encode("ascii")


def file_stamp(info):
    return tuple(getattr(info, name) for name in (
        "st_dev", "st_ino", "st_mode", "st_uid", "st_gid", "st_nlink",
        "st_size", "st_mtime_ns", "st_ctime_ns"))


def bounded_file(file, maximum):
    """Read one regular input without following its final component or blocking.

    A cancelled open/uncertain close poisons fixture cleanup. A synchronous open
    OSError returned no descriptor; a Popen exception has no such guarantee.
    Exposed separately for portable fault models, not a kernel-proof substitute.
    """
    global INPUT_CLEANUP_CONFIRMED, CANCELLATION_OBSERVED
    need(type(maximum) is int and 0 < maximum <= 2 * 1024 * 1024)
    prior = INPUT_CLEANUP_CONFIRMED
    INPUT_CLEANUP_CONFIRMED = False
    fd, closed = None, False
    try:
        try:
            fd = os.open(file, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC)
        except OSError:
            closed = True
            raise
        before = os.fstat(fd)
        need(stat.S_ISREG(before.st_mode) and 0 < before.st_size <= maximum)
        raw = os.pread(fd, maximum + 1, 0)
        after = os.fstat(fd)
        linked = os.stat(file, follow_symlinks=False)
        need(len(raw) == before.st_size and file_stamp(before) == file_stamp(after) == file_stamp(linked))
        return raw
    except BaseException as error:
        if not isinstance(error, Exception):
            CANCELLATION_OBSERVED = True
        raise
    finally:
        if fd is not None:
            try:
                os.close(fd)
                closed = True
            finally:
                INPUT_CLEANUP_CONFIRMED = prior and closed
        else:
            INPUT_CLEANUP_CONFIRMED = prior and closed


def literal(raw, name):
    assignments = [(t.id, node.value) for node in ast.parse(raw).body if isinstance(node, ast.Assign)
                   for t in node.targets if isinstance(t, ast.Name)]
    matches = [value for key, value in assignments if key == name]
    need(len(matches) == 1)
    table = dict(assignments)
    node = matches[0]
    need(isinstance(node, ast.Dict))
    # The runtime uses its fixed CAPTURE/COLD string constants as map keys.
    # Resolve only literal assignments; never import or execute the runtime.
    keys = [ast.literal_eval(table[k.id] if isinstance(k, ast.Name) else k) for k in node.keys]
    need(len(keys) == len(set(keys)))
    return dict(zip(keys, (ast.literal_eval(v) for v in node.values)))


def write(file, raw, mode=0o444):
    file.parent.mkdir(parents=True, exist_ok=True)
    file.write_bytes(raw)
    file.chmod(mode)


def identity(path):
    info = path.lstat()
    return info.st_dev, info.st_ino


def canary(marker):
    return ("from pathlib import Path\nPath(" + repr(str(marker))
            + ").write_bytes(b'CANARY_EXECUTED')\n").encode("ascii")


def acl(file):
    unknown = 0xffffffff
    rows = ((1, 4, unknown), (2, 4, UID), (4, 4, unknown), (16, 4, unknown), (32, 4, unknown))
    body = struct.pack("<I", 2) + b"".join(struct.pack("<HHI", *row) for row in rows)
    os.setxattr(file, "system.posix_acl_access", body)
    need("system.posix_acl_access" in os.listxattr(file))


def inventory(wheels):
    files, directories, total = {}, set(), 0
    for body in wheels.values():
        with zipfile.ZipFile(io.BytesIO(body)) as archive:
            for item in archive.infolist():
                name = item.filename[:-1] if item.is_dir() else item.filename
                path = PurePosixPath(name)
                need(name and not path.is_absolute() and str(path) == name
                     and all(p not in ("", ".", "..") for p in name.split("/"))
                     and not any(c in name for c in ("\\", ":", "\0"))
                     and not item.flag_bits & 1
                     and stat.S_IFMT(item.external_attr >> 16) in
                     (0, stat.S_IFDIR if item.is_dir() else stat.S_IFREG))
                directories.update(str(p) for p in path.parents if str(p) != ".")
                if item.is_dir():
                    directories.add(name)
                    continue
                total += item.file_size
                need(name not in files and 0 <= item.file_size <= 8 * 1024 * 1024
                     and total <= 64 * 1024 * 1024 and len(files) < 5000
                     and not name.endswith((".pth", ".pyc", ".so")))
                files[name] = archive.read(item)
                need(len(files[name]) == item.file_size)
    need(files and not set(files) & directories)
    return files, directories


def source_bytes(repo):
    # Assemble canonical LF Linux release bytes, never normalize deployed reads.
    return {name: bounded_file(repo / name, 600000).replace(b"\r\n", b"\n") for name in (*PINS, ENTRY)}


def bundle(scope, sources, wheels, dependencies, directories, case):
    root = scope / case
    root.mkdir(mode=0o755)
    release = root / "release"
    release.mkdir(mode=0o755)
    for name, raw in sources.items():
        write(release / name, raw)
    for name, raw in wheels.items():
        write(release / "wheels" / name, raw)
    for name in directories:
        (release / "deps" / name).mkdir(parents=True, exist_ok=True)
    for name, raw in dependencies.items():
        write(release / "deps" / name, raw)
    for directory, _, _ in os.walk(root):
        Path(directory).chmod(0o755)
    writable = root / "actor-marker"
    writable.mkdir(mode=0o750)
    os.chown(writable, UID, GID)
    writable.chmod(0o750)
    marker = writable / "executed"
    info = release.stat()
    spec = dict(format="pult-working-launch-profile-check-v1",
                release=dict(root=str(release), identity=dict(device=info.st_dev, inode=info.st_ino)),
                entry_sha256=sha(sources[ENTRY]), actor=dict(uid=UID, gid=GID),
                namespace=dict(endpoint="https://s3.ru-3.storage.selcloud.ru", region="ru-3",
                               bucket="pult-custodian-working-proof", scope="pult-work-proof",
                               generation="8d3c8e2f-d470-41cb-87ad-50bf698c433b"))
    if case == "bad_format":
        spec["format"] = "not-a-working-profile"
    elif case == "extra_profile_field":
        spec["credentials"] = None
    elif case == "wrong_actor":
        spec["actor"]["uid"] += 10
    elif case == "wrong_inode":
        spec["release"]["identity"]["inode"] += 1
    profile = root / "profile.json"
    raw = canonical(spec)
    write(profile, raw)
    approval = sha(raw) if case != "wrong_profile_sha" else "0" * 64
    target = release / "ops/backup/working_journal_v2.py"
    if case == "extra_app_source":
        write(release / "ops/backup/unapproved.py", canary(marker))
    elif case == "extra_empty_dependency_directory":
        extra = release / "deps/unapproved_empty"
        extra.mkdir(mode=0o755)
        extra.chmod(0o755)
    elif case == "helper_substitution":
        write(release / CAPTURE, canary(marker))
    elif case == "payload_substitution":
        write(target, canary(marker))
    elif case == "dependency_substitution":
        write(release / "deps/httpx/__init__.py", canary(marker))
    elif case == "wheel_substitution":
        name = next(iter(WHEELS))
        write(release / "wheels" / name, wheels[name] + b"unapproved")
    for prefix, file in (("profile", profile), ("source", target)):
        if case == prefix + "_symlink":
            alternate = root / "alternate"
            file.rename(alternate)
            file.symlink_to(alternate)
        elif case == prefix + "_hardlink":
            os.link(file, root / "second-link")
            need(file.stat().st_nlink == 2)
        elif case == prefix + "_fifo":
            file.unlink()
            os.mkfifo(file, 0o444)
        elif case == prefix + "_owner":
            os.chown(file, UID, GID)
        elif case == prefix + "_mode":
            file.chmod(0o666)
        elif case == prefix + "_acl":
            acl(file)
    if case == "poison_environment_ignored":
        for name in ("sitecustomize.py", "usercustomize.py", "startup.py"):
            write(root / "poison" / name, canary(marker))
        (root / "poison").chmod(0o755)
    return root, release / ENTRY, profile, approval, marker


def proc_fields():
    with open("/proc/self/status", "rb") as stream:
        raw = stream.read(65537)
    need(len(raw) <= 65536)
    fields = dict(row.split(":", 1) for row in raw.decode("ascii").splitlines() if ":" in row)
    return dict(uid=[int(v) for v in fields["Uid"].split()],
                gid=[int(v) for v in fields["Gid"].split()], groups=os.getgroups(),
                nnp=int(fields["NoNewPrivs"]),
                caps={k: int(fields[k], 16) for k in ("CapEff", "CapPrm", "CapInh", "CapAmb", "CapBnd")})


def descriptors():
    result = {}
    for name in os.listdir("/proc/self/fd"):
        try:
            fd = int(name)
            info = os.fstat(fd)
            result[fd] = (info.st_dev, info.st_ino, info.st_mode, os.get_inheritable(fd))
        except OSError:
            # The directory iterator's own descriptor is already closed.
            continue
    return result


def payload_modules():
    return {n: id(m) for n, m in sys.modules.items() if n.split(".", 1)[0] in PAYLOAD}


def harness_input_controls(scope):
    """Actual own-temp controls, additional to the unchanged 30 profile cases."""
    before = descriptors()
    root = scope / "bounded-input-controls"
    root.mkdir(mode=0o755)
    regular, oversized, fifo, link = (root / n for n in ("regular", "oversized", "fifo", "link"))
    write(regular, b"INPUT_CONTROL")
    write(oversized, b"x" * 65)
    os.mkfifo(fifo, 0o444)
    link.symlink_to(regular)
    need(stat.S_ISFIFO(fifo.lstat().st_mode) and oversized.lstat().st_size == 65
         and stat.S_ISLNK(link.lstat().st_mode))
    need(bounded_file(regular, 64) == b"INPUT_CONTROL")
    for file in (fifo, oversized):
        refused = False
        try:
            bounded_file(file, 64)
        except ProofError:
            refused = True
        need(refused and INPUT_CLEANUP_CONFIRMED is True and descriptors() == before)
    refused = False
    try:
        bounded_file(link, 64)
    except OSError as error:
        refused = error.errno == errno.ELOOP
    need(refused and INPUT_CLEANUP_CONFIRMED is True and descriptors() == before)
    return dict(regular=True, fifo_refused=True, oversize_refused=True, symlink_refused=True)


def child(args):
    need(len(args) == 5 and args[0] in CASES)
    case, entry, profile, approval, marker = args
    module = ModuleType("_working_launch_proof_entry")
    module.__file__ = entry
    raw = bounded_file(Path(entry), 600000)
    attempts = dict.fromkeys(FORBIDDEN_EVENTS, 0)

    def offline_audit(event, unused):
        if event in attempts:
            attempts[event] += 1
            raise ProofError("offline_resource_forbidden")

    sys.addaudithook(offline_audit)
    exec(compile(raw, entry, "exec", dont_inherit=True, optimize=0), module.__dict__)
    need(module.PINS == PINS and module.ENTRY == ENTRY)
    before_fds, before_modules = descriptors(), payload_modules()
    before_meta, before_path = tuple(sys.meta_path), tuple(sys.path)
    need(not before_modules)
    principal = proc_fields()
    # Exercise this exact location as this exact actor, not a root-only marker.
    exec(compile(canary(Path(marker)), "<marker-positive-control>", "exec"), {})
    need(bounded_file(Path(marker), 64) == b"CANARY_EXECUTED" and Path(marker).lstat().st_uid == UID)
    if case != "marker_control":
        Path(marker).unlink()
    result, status = None, "REFUSED"
    if case == "marker_control":
        status = "CONTROL"
    else:
        injected = None
        if case == "preloaded_module":
            injected = ModuleType("httpx")
            sys.modules["httpx"] = injected
        caught = []

        def verify():
            nonlocal result, status
            try:
                result = module.verify_profile(profile, approval)
                status = "VERIFIED"
            except module.Refused as error:
                need(str(error) == "working_profile_refused")
            except BaseException:
                caught.append(True)

        try:
            if case == "thread_owner":
                worker = threading.Thread(target=verify)
                worker.start()
                worker.join(5)
                need(not worker.is_alive())
            else:
                verify()
            need(not caught)
        finally:
            if case == "preloaded_module":
                need(sys.modules.get("httpx") is injected)
                del sys.modules["httpx"]
    value = dict(case=case, status=status, result=result, principal=principal,
                 marker_control_confirmed=True,
                 resource_attempts=attempts,
                 modules_restored=payload_modules() == before_modules,
                 fds_restored=descriptors() == before_fds,
                 imports_restored=tuple(sys.meta_path) == before_meta and tuple(sys.path) == before_path)
    need(value["modules_restored"] and value["fds_restored"] and value["imports_restored"])
    need(all(v == 0 for v in attempts.values()))
    sys.stdout.buffer.write(canonical(value))
    return 0


class ChildOwnership:
    """One spawn attempt; uncertainty is permanent, never inferred as no child."""

    def __init__(self):
        self.process = self.pidfd = self.selector = None
        self.spawn_attempted = self.spawn_confirmed = False
        self.pidfd_attempted = self.selector_attempted = False
        self.reaped = self.cleanup_attempted = self.cleanup_confirmed = False
        self.cleanup_failed = self.cancelled = False
        self.pipes_closed = self.pidfd_closed = self.selector_closed = False


def cleanup_owned_child(owned):
    """Best effort ALL independent actions; no retry after uncertain FD close.

    Returns a strict bool and retains the facts for portable failure models.
    Unknown spawn/open and cancellation never authorize fixture deletion.
    """
    if owned.cleanup_attempted:
        return owned.cleanup_confirmed
    owned.cleanup_attempted = True

    def failed(error):
        global CANCELLATION_OBSERVED
        owned.cleanup_failed = True
        if not isinstance(error, Exception):
            owned.cancelled = True
            CANCELLATION_OBSERVED = True

    process = owned.process
    if process is not None:
        if not owned.reaped:
            try:
                code = process.poll()
                if type(code) is int:
                    owned.reaped = True
            except BaseException as error:
                failed(error)
        if not owned.reaped:
            signalled = False
            if owned.pidfd is not None:
                try:
                    signal.pidfd_send_signal(owned.pidfd, signal.SIGKILL)
                    signalled = True
                except BaseException as error:
                    failed(error)
            if not signalled:
                try:
                    process.kill()
                except BaseException as error:
                    failed(error)
        try:
            code = process.wait(timeout=5)
            need(type(code) is int)
            owned.reaped = True
        except BaseException as error:
            failed(error)

    # Close the selector and every pipe independently, even if kill/wait failed.
    if owned.selector is not None:
        try:
            owned.selector.close()
            owned.selector_closed = True
        except BaseException as error:
            failed(error)
    else:
        owned.selector_closed = not owned.selector_attempted
    closed = []
    if process is not None:
        for name in ("stdin", "stdout", "stderr"):
            try:
                stream = getattr(process, name)
                if stream is not None:
                    stream.close()
                    need(stream.closed is True)
                closed.append(name)
            except BaseException as error:
                failed(error)
    owned.pipes_closed = closed == ["stdin", "stdout", "stderr"]
    if owned.pidfd is not None:
        try:
            os.close(owned.pidfd)
            owned.pidfd_closed = True
        except BaseException as error:
            failed(error)
    else:
        owned.pidfd_closed = not owned.pidfd_attempted
    owned.cleanup_confirmed = bool(
        owned.spawn_attempted and owned.spawn_confirmed and owned.reaped
        and owned.pipes_closed and owned.selector_closed and owned.pidfd_closed
        and not owned.cleanup_failed and not owned.cancelled)
    return owned.cleanup_confirmed


def fixture_cleanup_allowed():
    return (CHILD_CLEANUP_CONFIRMED is True and INPUT_CLEANUP_CONFIRMED is True
            and CANCELLATION_OBSERVED is False
            and all(owned.cleanup_confirmed is True for owned in OWNED_CHILDREN))


def cleanup_fixture(scope, scope_id, before_fds):
    """Delete only this checked scope after all process/FD ownership is resolved."""
    global CLEANUP_CONFIRMED, FIXTURE_PRESERVED
    CLEANUP_CONFIRMED = False
    need(fixture_cleanup_allowed() and descriptors() == before_fds)
    need(scope.parent == Path("/tmp") and scope.name.startswith("pult-working-profile-")
         and not scope.is_symlink() and identity(scope) == scope_id
         and scope.lstat().st_uid == 0 and stat.S_ISDIR(scope.lstat().st_mode))
    shutil.rmtree(scope)
    need(not os.path.lexists(scope) and descriptors() == before_fds)
    CLEANUP_CONFIRMED, FIXTURE_PRESERVED = True, False
    return True


def bounded_child(argv, env):
    global CHILD_CLEANUP_CONFIRMED, CANCELLATION_OBSERVED
    owned = ChildOwnership()
    OWNED_CHILDREN.append(owned)
    CHILD_CLEANUP_CONFIRMED = False
    output = {"stdout": bytearray(), "stderr": bytearray()}
    ended = set()
    deadline = time.monotonic() + CHILD_TIMEOUT
    try:
        # Latch BEFORE Popen: an exception/cancellation can conceal a real child.
        owned.spawn_attempted = True
        owned.process = subprocess.Popen(
            argv, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
            stderr=subprocess.PIPE, env=env, close_fds=True, start_new_session=True)
        process = owned.process
        need(type(process.pid) is int and process.pid > 0)
        owned.spawn_confirmed = True
        owned.pidfd_attempted = True
        owned.pidfd = os.pidfd_open(process.pid)
        need(type(owned.pidfd) is int and owned.pidfd >= 0)
        owned.selector_attempted = True
        owned.selector = selectors.DefaultSelector()
        selector = owned.selector
        for name in output:
            stream = getattr(process, name)
            os.set_blocking(stream.fileno(), False)
            selector.register(stream, selectors.EVENT_READ, name)
        while len(ended) != 2:
            need(time.monotonic() < deadline)
            for key, _ in selector.select(min(0.2, max(0, deadline - time.monotonic()))):
                chunk = os.read(key.fileobj.fileno(), 4096)
                if chunk == b"":
                    ended.add(key.data)
                    selector.unregister(key.fileobj)
                else:
                    output[key.data].extend(chunk)
                    need(sum(map(len, output.values())) <= MAX_OUTPUT)
        code = process.wait(timeout=max(0.01, deadline - time.monotonic()))
        need(type(code) is int)
        owned.reaped = True
        need(code == 0)
        need(ended == {"stdout", "stderr"} and not output["stderr"])
        raw = bytes(output["stdout"])
        value = json.loads(raw)
        need(canonical(value) == raw)
        return value
    except BaseException as error:
        if not isinstance(error, Exception):
            owned.cancelled = True
            CANCELLATION_OBSERVED = True
        raise
    finally:
        cleanup_owned_child(owned)
        CHILD_CLEANUP_CONFIRMED = all(o.cleanup_confirmed is True for o in OWNED_CHILDREN)
        need(owned.cleanup_confirmed)


def validate(case, value, approval, dependency_count, marker):
    need(type(value) is dict and set(value) == {"case", "status", "result", "principal",
                                               "modules_restored", "fds_restored", "imports_restored",
                                               "marker_control_confirmed", "resource_attempts"})
    need(value["case"] == case and all(value[k] is True for k in
                                     ("modules_restored", "fds_restored", "imports_restored",
                                      "marker_control_confirmed")))
    need(type(value["resource_attempts"]) is dict
         and set(value["resource_attempts"]) == set(FORBIDDEN_EVENTS)
         and all(type(v) is int and v == 0 for v in value["resource_attempts"].values()))
    principal = value["principal"]
    need(type(principal) is dict and set(principal) == {"uid", "gid", "groups", "nnp", "caps"})
    need(principal["uid"] == [UID] * 4 and principal["gid"] == [GID] * 4
         and all(type(principal[k]) is list and all(type(v) is int for v in principal[k])
                 for k in ("uid", "gid", "groups"))
         and principal["groups"] == [] and type(principal["nnp"]) is int and principal["nnp"] == 1)
    caps = principal["caps"]
    need(type(caps) is dict and set(caps) == {"CapEff", "CapPrm", "CapInh", "CapAmb", "CapBnd"}
         and all(type(v) is int and v >= 0 for v in caps.values()))
    need(caps == {k: (1 if case == "bounding_capability" and k == "CapBnd" else 0) for k in caps})
    expected = "CONTROL" if case == "marker_control" else "VERIFIED" if case in ACCEPT else "REFUSED"
    need(value["status"] == expected)
    if case in ACCEPT:
        receipt = value["result"]
        expected_receipt = dict(format="pult-working-profile-preflight-result-v1",
                                status="VERIFIED_OFFLINE_NOT_LAUNCHED", profile_sha256=approval,
                                source_files_verified=9, dependency_files_verified=dependency_count,
                                namespace_validated=True, **dict.fromkeys(FLAGS, False))
        need(type(receipt) is dict and receipt == expected_receipt
             and all(receipt[k] is False for k in FLAGS)
             and type(receipt["source_files_verified"]) is int
             and type(receipt["dependency_files_verified"]) is int
             and receipt["namespace_validated"] is True)
    else:
        need(value["result"] is None)
    need(marker.exists() == (case == "marker_control"))
    if case == "marker_control":
        need(bounded_file(marker, 64) == b"CANARY_EXECUTED" and marker.lstat().st_uid == UID)


def supported():
    return (sys.platform == "linux" and sys.implementation.name == "cpython"
            and sys.version_info[:2] == (3, 12) and os.geteuid() == os.getuid() == 0
            and os.getegid() == os.getgid() == 0 and os.path.realpath(sys.executable) == PYTHON
            and sys.prefix == sys.base_prefix == "/usr" and sys.flags.isolated and sys.flags.no_site
            and sys.flags.no_user_site and sys.flags.ignore_environment and sys.flags.safe_path
            and sys.flags.dont_write_bytecode and not sys.flags.optimize
            and hasattr(os, "pidfd_open") and hasattr(signal, "pidfd_send_signal"))


def run(wheel_dir):
    global CURRENT_CASE, FIXTURE_PRESERVED, CANCELLATION_OBSERVED
    repo = Path(__file__).resolve().parents[3]
    raw_hashes = {name: sha(bounded_file(repo / name, 600000)) for name in (*PINS, ENTRY)}
    sources = source_bytes(repo)
    hashes = {name: sha(raw) for name, raw in sources.items()}
    need({n: hashes[n] for n in PINS} == PINS)
    need(literal(sources[ENTRY], "PINS") == PINS and literal(sources[COLD], "WHEELS") == WHEELS)
    wheels = {name: bounded_file(wheel_dir / name, 2 * 1024 * 1024) for name in WHEELS}
    need(all(0 < len(raw) <= 2 * 1024 * 1024 and sha(raw) == WHEELS[name]
             for name, raw in wheels.items()))
    dependencies, directories = inventory(wheels)
    # Require an actually separate network namespace, not just a caller assertion.
    need(os.readlink("/proc/self/ns/net") != os.readlink("/proc/1/ns/net"))
    need(Path(SETPRIV).is_file() and len(os.listdir("/proc/self/task")) == 1)
    scope = Path(tempfile.mkdtemp(prefix="pult-working-profile-", dir="/tmp"))
    FIXTURE_PRESERVED = True
    scope.chmod(0o755)
    scope_id = identity(scope)
    before_fds = descriptors()
    results, cleanup = COMPLETED, False
    try:
        # Fail closed if this filesystem cannot demonstrate a real POSIX ACL.
        probe = scope / "acl-probe"
        write(probe, b"proof\n")
        acl(probe)
        probe.unlink()
        input_controls = harness_input_controls(scope)
        worker = scope / "proof.py"
        write(worker, bounded_file(Path(__file__), 600000))
        for case in CASES:
            CURRENT_CASE = case
            root, entry, profile, approval, marker = bundle(
                scope, sources, wheels, dependencies, directories, case)
            bounding = "-all,+chown" if case == "bounding_capability" else "-all"
            argv = [SETPRIV, "--reuid=" + str(UID), "--regid=" + str(GID), "--clear-groups",
                    "--bounding-set=" + bounding, "--inh-caps=-all", "--ambient-caps=-all",
                    "--no-new-privs", "--", PYTHON]
            argv += ["-S", "-B"] if case == "missing_isolation" else ["-I", "-S", "-B"]
            argv += [str(worker), "--child", case, str(entry), str(profile), approval, str(marker)]
            env = dict(ENV)
            if case == "poison_environment_ignored":
                env.update(PYTHONPATH=str(root / "poison"), PYTHONSTARTUP=str(root / "poison/startup.py"),
                           PYTHONUSERBASE=str(root / "poison"), PYTHONINSPECT="1")
            value = bounded_child(argv, env)
            validate(case, value, approval, len(dependencies), marker)
            need(descriptors() == before_fds)
            results.append(dict(case=case, status=value["status"],
                                modules_restored=value["modules_restored"],
                                fds_restored=value["fds_restored"],
                                imports_restored=value["imports_restored"],
                                marker_control_confirmed=value["marker_control_confirmed"],
                                resource_attempts=value["resource_attempts"]))
        # Validate all final source reads while preservation is still possible.
        after = {name: sha(raw) for name, raw in source_bytes(repo).items()}
        raw_after = {name: sha(bounded_file(repo / name, 600000)) for name in (*PINS, ENTRY)}
        need(after == hashes and raw_after == raw_hashes and [r["case"] for r in results] == list(CASES))
    except BaseException as error:
        if not isinstance(error, Exception):
            CANCELLATION_OBSERVED = True
        raise
    finally:
        cleanup = cleanup_fixture(scope, scope_id, before_fds)
    return dict(format="pult-working-launch-profile-proof-v1", status="PASS",
                scope="OFFLINE_WORKING_PROFILE_CHECK_ONLY", tests=len(CASES), skips=0,
                cases=results, source_hashes_before=hashes, source_hashes_after=after,
                repository_byte_hashes_before=raw_hashes, repository_byte_hashes_after=raw_after,
                harness_input_controls=input_controls,
                wheel_hashes=WHEELS, cleanup_confirmed=cleanup, **dict.fromkeys(FLAGS, False),
                fault_injection_kernel_proof=False, pid_owner_fork_proof=False)


def main():
    if len(sys.argv) > 1 and sys.argv[1] == "--child":
        try:
            return child(sys.argv[2:])
        except BaseException:
            print("PROOF_CHILD_REFUSED", file=sys.stderr)
            return 1
    if not supported():
        sys.stdout.buffer.write(canonical(dict(format="pult-working-launch-profile-proof-v1",
                                              status="NOT_RUN", tests=0, skips=0,
                                              reason="fixed_native_root_runtime_required")))
        return 2
    try:
        repo = Path(__file__).resolve().parents[3]
        args = sys.argv[1:]
        need(not args or len(args) == 2 and args[0] == "--wheels-dir")
        wheels = Path(args[1]) if args else repo / "ops/backup/tests/custodian_v2/wheels"
        value = run(wheels)
        sys.stdout.buffer.write(canonical(value))
        return 0
    except BaseException:
        sys.stdout.buffer.write(canonical(dict(format="pult-working-launch-profile-proof-v1",
                                              status="FAIL", tests=len(COMPLETED), skips=0,
                                              expected_tests=len(CASES), cases=COMPLETED,
                                              failed_case=CURRENT_CASE,
                                              cleanup_confirmed=CLEANUP_CONFIRMED and fixture_cleanup_allowed(),
                                              fixture_preserved=FIXTURE_PRESERVED,
                                              child_cleanup_confirmed=CHILD_CLEANUP_CONFIRMED,
                                              input_cleanup_confirmed=INPUT_CLEANUP_CONFIRMED,
                                              cancellation_observed=CANCELLATION_OBSERVED,
                                              reason="native_proof_not_confirmed")))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
