"""Private, fail-closed Linux CPython 3.12 SSH ingress (stdlib only).

Deployment contract: a root-controlled OS ForceCommand supplies exactly the
absolute config path and its independently pinned lowercase SHA-256 as argv.
Neither argument comes from SSH_ORIGINAL_COMMAND, SSH input, or environment.
The script/interpreter and OS account policy must also be root controlled. The
fixed ForceCommand invokes /usr/bin/setpriv --no-new-privs -- followed by
/usr/bin/python3.12 -I -S -B, this root-controlled ingress, and the fixed args.
Pre-shell environment/startup controls require an actual OpenSSH deployment
gate; this module cannot establish them after the shell has already run.
Run unoptimized in one main thread as dedicated UID/GID >=1000 and <2**31.
The supplementary vector may be empty or contain only that exact primary GID.
CapInh/CapPrm/CapEff/CapAmb must be zero and kernel NoNewPrivs must be 1.
CapBnd must be valid hex but may be nonzero: it is a ceiling, not active
authority. NoNewPrivs prevents exec-based privilege gains. This ingress-only
contract does not weaken the separate writer/custodian actor capability guards.
This module never elevates.

Wire contract: send TRIGGER once on a pinned AF_UNIX/SOCK_SEQPACKET connection.
Accept only STORAGE_ONLY_REPLY, then peer write-EOF, while the original root
controller's kernel pidfd remains live. The controller must keep its process
alive through this exchange. A reply is storage-only completion evidence, not
purge/restore authority. Controller lifecycle/storage proofs belong elsewhere.
Import and config parsing are portable; live admission deliberately is not.
"""

import array
from contextlib import contextmanager
import hashlib
import hmac
import json
import os
import re
import select
import socket
import stat
import struct
import sys
import threading
import time
from typing import NamedTuple


FORMAT = "pult-synthetic-ssh-ingress-v1"
COMMAND = "pult-synthetic-one-v1"
TRIGGER = b"RUN_ONE_SYNTHETIC_STORAGE_ACTION_V1\n"
STORAGE_ONLY_REPLY = (
    b'{"format":"pult-synthetic-storage-only-reply-v1",'
    b'"purge":false,"restore":false,"status":"complete","storage_only":true}\n'
)
SO_PEERPIDFD = 77  # Linux UAPI, not SO_PASSPIDFD (76) or a synthetic pidfd.
CONFIG_LIMIT = 8192
PROC_LIMIT = 16384
TOTAL_SECONDS = 30.0
INPUT_SECONDS = 3.0
ANCILLARY_LIMIT = socket.CMSG_SPACE(64 * struct.calcsize("i")) if hasattr(socket, "CMSG_SPACE") else 1024


class Denied(Exception):
    """No admitted action, or an uncertain result: never retry here."""


class Controller(NamedTuple):
    pid: int
    start_ticks: int
    boot: str


class SocketPin(NamedTuple):
    path: str
    device: int
    inode: int


class Config(NamedTuple):
    caller_uid: int
    caller_gid: int
    controller: Controller
    socket: SocketPin


class Deadline:
    def __init__(self, seconds, *, end=None):
        self.end = min(time.monotonic() + seconds, end) if end is not None else time.monotonic() + seconds

    def __enter__(self):
        self.remaining()
        return self

    def __exit__(self, *_exc):
        return False

    def remaining(self):
        remaining = self.end - time.monotonic()
        if remaining <= 0:
            raise Denied("deadline")
        return remaining


def _exact_keys(value, keys):
    if type(value) is not dict or set(value) != set(keys):
        raise Denied("schema")


def _integer(value, minimum=1, maximum=(1 << 64) - 1):
    if type(value) is not int or not minimum <= value <= maximum:
        raise Denied("integer")
    return value


def _path(value, limit=4095):
    if type(value) is not str or not value.startswith("/"):
        raise Denied("absolute path")
    if any(part in ("", ".", "..") for part in value[1:].split("/")):
        raise Denied("path components")
    if any(ord(char) <= 32 or ord(char) == 127 for char in value):
        raise Denied("path characters")
    try:
        encoded = value.encode("utf-8", "strict")
    except UnicodeError as exc:
        raise Denied("path encoding") from exc
    if len(encoded) > limit:
        raise Denied("path length")
    return value


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise Denied("duplicate key")
        result[key] = value
    return result


def _no_constant(_value):
    raise Denied("non-JSON number")


def parse_config(raw):
    """Validate untrusted bytes without filesystem or Linux dependencies."""
    if type(raw) is not bytes or not 0 < len(raw) <= CONFIG_LIMIT:
        raise Denied("config size")
    try:
        value = json.loads(raw.decode("utf-8", "strict"), object_pairs_hook=_unique_object,
                           parse_constant=_no_constant)
    except (ValueError, UnicodeError, RecursionError) as exc:
        raise Denied("config encoding") from exc
    _exact_keys(value, ("format", "caller_uid", "caller_gid", "controller", "socket"))
    if value["format"] != FORMAT:
        raise Denied("format")
    uid = _integer(value["caller_uid"], minimum=1000, maximum=(1 << 31) - 1)
    gid = _integer(value["caller_gid"], minimum=1000, maximum=(1 << 31) - 1)
    controller, endpoint = value["controller"], value["socket"]
    _exact_keys(controller, ("pid", "start_ticks", "boot"))
    _exact_keys(endpoint, ("path", "device", "inode"))
    boot = controller["boot"]
    if type(boot) is not str or not re.fullmatch(r"[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}", boot):
        raise Denied("boot")
    return Config(uid, gid, Controller(_integer(controller["pid"], maximum=(1 << 31) - 1),
                                      _integer(controller["start_ticks"]), boot),
                  SocketPin(_path(endpoint["path"], 107), _integer(endpoint["device"], 0),
                            _integer(endpoint["inode"])))


def check_command(environment):
    if environment.get("SSH_ORIGINAL_COMMAND") != COMMAND:
        raise Denied("command")


def _check_runtime():
    flags = sys.flags
    if (sys.platform != "linux" or sys.implementation.name != "cpython"
            or sys.version_info[:2] != (3, 12)
            or sys.executable != "/usr/bin/python3.12" or flags.optimize != 0
            or not all((flags.isolated, flags.no_site, flags.dont_write_bytecode,
                        flags.ignore_environment, flags.no_user_site, flags.safe_path))
            or threading.current_thread() is not threading.main_thread()
            or threading.active_count() != 1):
        raise Denied("runtime")
    # Include native threads not registered in Python's threading registry.
    if _field(_read_proc("/proc/self/status"), b"Threads") != b"1":
        raise Denied("runtime threads")


def _read_bounded_fd(fd, limit):
    chunks, size = [], 0
    while True:
        data = os.read(fd, limit + 1 - size)
        if not data:
            return b"".join(chunks)
        chunks.append(data)
        size += len(data)
        if size > limit:
            raise Denied("file size")


def _read_proc(path, *, root=False):
    fd = os.open(path, os.O_RDONLY | os.O_CLOEXEC | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or (root and info.st_uid != 0):
            raise Denied("proc metadata")
        return _read_bounded_fd(fd, PROC_LIMIT)
    finally:
        os.close(fd)


def _field(raw, name):
    prefix = name + b":"
    rows = [line[len(prefix):].strip() for line in raw.splitlines() if line.startswith(prefix)]
    if len(rows) != 1:
        raise Denied("proc field")
    return rows[0]


def _check_identity(config):
    if (os.getresuid() != (config.caller_uid,) * 3
            or os.getresgid() != (config.caller_gid,) * 3
            or os.getgroups() not in ([], [config.caller_gid])):
        raise Denied("identity")
    status = _read_proc("/proc/self/status")
    if (_field(status, b"Uid").split() != [str(config.caller_uid).encode("ascii")] * 4
            or _field(status, b"Gid").split() != [str(config.caller_gid).encode("ascii")] * 4):
        raise Denied("filesystem identity")
    if _field(status, b"NoNewPrivs") != b"1":
        raise Denied("no_new_privs")
    for name in (b"CapInh", b"CapPrm", b"CapEff", b"CapBnd", b"CapAmb"):
        value = _field(status, name)
        if (not re.fullmatch(rb"[0-9a-fA-F]{16}", value)
                or (name != b"CapBnd" and int(value, 16) != 0)):
            raise Denied("capabilities")


def _protected_directory(fd):
    info = os.fstat(fd)
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != 0 or info.st_mode & 0o022:
        raise Denied("directory protection")


@contextmanager
def _root_parent(path):
    """Walk using owned dirfds, never following symlinks or writable parents."""
    _path(path)
    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC
    fd = os.open("/", flags)
    try:
        _protected_directory(fd)
        parts = path[1:].split("/")
        for part in parts[:-1]:
            child = os.open(part, flags, dir_fd=fd)
            try:
                _protected_directory(child)
            except BaseException:
                os.close(child)
                raise
            previous, fd = fd, child
            os.close(previous)
        yield fd, parts[-1]
    finally:
        os.close(fd)


def _file_identity(info):
    return (info.st_dev, info.st_ino, info.st_mode, info.st_uid, info.st_gid,
            info.st_size, info.st_mtime_ns, info.st_ctime_ns)


def load_pinned_config(path, digest):
    _path(path)
    if type(digest) is not str or not re.fullmatch(r"[0-9a-f]{64}", digest):
        raise Denied("independent hash")
    with _root_parent(path) as (directory, name):
        fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC | os.O_NONBLOCK, dir_fd=directory)
        try:
            before = os.fstat(fd)
            if (not stat.S_ISREG(before.st_mode) or before.st_uid != 0
                    or before.st_mode & 0o022 or not 0 < before.st_size <= CONFIG_LIMIT):
                raise Denied("config protection")
            raw = _read_bounded_fd(fd, CONFIG_LIMIT)
            if _file_identity(before) != _file_identity(os.fstat(fd)):
                raise Denied("config changed")
        finally:
            os.close(fd)
    if not hmac.compare_digest(hashlib.sha256(raw).hexdigest(), digest):
        raise Denied("config hash")
    return parse_config(raw)


def _wait(fd, deadline, *, write=False):
    readable, writable, exceptional = select.select([] if write else [fd], [fd] if write else [],
                                                   [fd], deadline.remaining())
    if exceptional or not (writable if write else readable):
        raise Denied("I/O deadline")
    deadline.remaining()


def _reject_ancillary(ancillary, flags):
    # recvmsg installs SCM_RIGHTS descriptors even when the message is rejected.
    close_failed = False
    for level, kind, raw in ancillary:
        if level == socket.SOL_SOCKET and kind == socket.SCM_RIGHTS:
            rights = array.array("i")
            rights.frombytes(raw[:len(raw) - len(raw) % rights.itemsize])
            for fd in rights:
                try:
                    os.close(fd)
                except OSError:
                    close_failed = True
    if close_failed or ancillary or flags & (socket.MSG_TRUNC | socket.MSG_CTRUNC):
        raise Denied("ancillary or truncation")


def _collect_trigger(read_once, wait, deadline):
    data = b""
    while True:
        wait(deadline)
        try:
            part = read_once(len(TRIGGER) + 1 - len(data))
        except BlockingIOError:
            # Readiness is not admission; no trigger has been sent.
            continue
        deadline.remaining()
        if not part:
            if data != TRIGGER:
                raise Denied("input frame")
            return
        data += part
        if len(data) > len(TRIGGER) or not TRIGGER.startswith(data):
            raise Denied("input frame")


def read_trigger(fd, deadline):
    owned = os.dup(fd)
    stream = None
    blocking = None
    try:
        os.set_inheritable(owned, False)
        blocking = os.get_blocking(owned)
        os.set_blocking(owned, False)
        mode = os.fstat(owned).st_mode
        if stat.S_ISSOCK(mode):
            stream = socket.socket(fileno=owned)
            owned = None  # stream now owns the descriptor.
            if stream.family != socket.AF_UNIX or stream.type != socket.SOCK_STREAM:
                raise Denied("stdin socket type")

            def read_once(size):
                data, ancillary, flags, _address = stream.recvmsg(size, ANCILLARY_LIMIT, socket.MSG_CMSG_CLOEXEC)
                _reject_ancillary(ancillary, flags)
                return data

            source = stream.fileno()
        elif stat.S_ISFIFO(mode) or stat.S_ISREG(mode):
            source = owned
            read_once = lambda size: os.read(source, size)
        else:
            raise Denied("stdin type")
        _collect_trigger(read_once, lambda clock: _wait(source, clock), deadline)
    finally:
        source = stream.fileno() if stream is not None else owned
        try:
            if source is not None and blocking is not None:
                os.set_blocking(source, blocking)
        finally:
            if stream is not None:
                stream.close()
            elif owned is not None:
                os.close(owned)


def _check_socket(directory, name, config):
    info = os.stat(name, dir_fd=directory, follow_symlinks=False)
    pin = config.socket
    if (not stat.S_ISSOCK(info.st_mode) or info.st_uid != 0 or info.st_gid != config.caller_gid
            or stat.S_IMODE(info.st_mode) != 0o660
            or (info.st_dev, info.st_ino) != (pin.device, pin.inode)):
        raise Denied("socket pin")


def _pidfd_live(fd):
    poller = select.poll()
    poller.register(fd, select.POLLIN | select.POLLHUP | select.POLLERR | select.POLLNVAL)
    if poller.poll(0):
        raise Denied("controller exited")


def _start_ticks(raw, expected_pid):
    # comm (field 2) may itself contain spaces, newlines and closing parentheses.
    boundary = raw.rfind(b")")
    if boundary < 0 or not raw.startswith(str(expected_pid).encode("ascii") + b" ("):
        raise Denied("proc stat")
    fields = raw[boundary + 1:].split()
    if len(fields) < 20 or not fields[19].isdigit():
        raise Denied("proc start")
    return _integer(int(fields[19]))


def _check_controller_live(pidfd, controller, deadline):
    deadline.remaining()
    _pidfd_live(pidfd)
    fd_pid = _field(_read_proc("/proc/self/fdinfo/" + str(pidfd)), b"Pid")
    if fd_pid != str(controller.pid).encode("ascii"):
        raise Denied("pidfd identity")
    if _read_proc("/proc/sys/kernel/random/boot_id", root=True) != controller.boot.encode("ascii") + b"\n":
        raise Denied("controller boot")
    prefix = "/proc/" + str(controller.pid)
    if _start_ticks(_read_proc(prefix + "/stat", root=True), controller.pid) != controller.start_ticks:
        raise Denied("controller start")
    if _field(_read_proc(prefix + "/status", root=True), b"Uid").split() != [b"0"] * 4:
        raise Denied("controller no longer root")
    _pidfd_live(pidfd)  # Pins the proc reads against exit/PID reuse.
    deadline.remaining()


@contextmanager
def _controller_peer(connection, config, deadline):
    credentials = connection.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, struct.calcsize("=iII"))
    if len(credentials) != struct.calcsize("=iII"):
        raise Denied("peer credentials size")
    pid, uid, _gid = struct.unpack("=iII", credentials)
    if pid != config.controller.pid or uid != 0:
        raise Denied("peer credentials")
    # This getsockopt creates a NEW, kernel-pinned fd. Never substitute pidfd_open
    # on a caller/config PID, or treat 77 as a PID/fd supplied by the caller.
    raw = connection.getsockopt(socket.SOL_SOCKET, SO_PEERPIDFD, struct.calcsize("i"))
    if len(raw) != struct.calcsize("i"):
        raise Denied("peer pidfd size")
    pidfd = struct.unpack("i", raw)[0]
    if pidfd < 0:
        raise Denied("peer pidfd")
    try:
        os.set_inheritable(pidfd, False)
        _check_controller_live(pidfd, config.controller, deadline)
        yield pidfd
    finally:
        os.close(pidfd)


def _receive_reply(connection, deadline):
    connection.settimeout(deadline.remaining())
    data, ancillary, flags, _address = connection.recvmsg(len(STORAGE_ONLY_REPLY) + 1, ANCILLARY_LIMIT,
                                                       socket.MSG_CMSG_CLOEXEC)
    _reject_ancillary(ancillary, flags)
    if data != STORAGE_ONLY_REPLY:
        raise Denied("storage-only reply")
    # No trailing nonempty record, authority extension, ancillary data, or hang.
    connection.settimeout(deadline.remaining())
    tail, ancillary, flags, _address = connection.recvmsg(1, ANCILLARY_LIMIT, socket.MSG_CMSG_CLOEXEC)
    _reject_ancillary(ancillary, flags)
    if tail:
        raise Denied("reply trailing record")
    # A zero-length SEQPACKET record is not by itself proof of write-EOF.
    poller = select.poll()
    poller.register(connection.fileno(), select.POLLRDHUP | select.POLLHUP | select.POLLERR | select.POLLNVAL)
    events = poller.poll(0)
    if not events or any(mask & (select.POLLERR | select.POLLNVAL) for _, mask in events):
        raise Denied("reply not closed")
    if not any(mask & (select.POLLRDHUP | select.POLLHUP) for _, mask in events):
        raise Denied("reply not EOF")
    deadline.remaining()
    return STORAGE_ONLY_REPLY


def _emit_reply(deadline):
    fd = os.dup(1)
    blocking = None
    try:
        os.set_inheritable(fd, False)
        blocking = os.get_blocking(fd)
        os.set_blocking(fd, False)
        _wait(fd, deadline, write=True)
        if os.write(fd, STORAGE_ONLY_REPLY) != len(STORAGE_ONLY_REPLY):
            raise Denied("short terminal reply")
        deadline.remaining()
    finally:
        try:
            if blocking is not None:
                os.set_blocking(fd, blocking)
        finally:
            os.close(fd)


def _run(path, digest):
    _check_runtime()
    check_command(os.environ)
    with Deadline(TOTAL_SECONDS) as deadline:
        config = load_pinned_config(path, digest)
        _check_identity(config)
        with Deadline(INPUT_SECONDS, end=deadline.end) as input_deadline:
            read_trigger(0, input_deadline)
        with _root_parent(config.socket.path) as (directory, name):
            _check_socket(directory, name, config)
            with socket.socket(socket.AF_UNIX, socket.SOCK_SEQPACKET | socket.SOCK_CLOEXEC) as connection:
                connection.settimeout(deadline.remaining())
                connection.connect(config.socket.path)
                _check_socket(directory, name, config)
                with _controller_peer(connection, config, deadline) as pidfd:
                    connection.settimeout(deadline.remaining())
                    if connection.send(TRIGGER) != len(TRIGGER):
                        raise Denied("short trigger")
                    _receive_reply(connection, deadline)
                    _check_controller_live(pidfd, config.controller, deadline)
        _emit_reply(deadline)


def main(argv=None):
    arguments = sys.argv[1:] if argv is None else argv
    if len(arguments) != 2:
        return 1
    try:
        _run(arguments[0], arguments[1])
    except (Denied, OSError, ValueError, OverflowError):
        # No variable diagnostic, completion, command, or retry on stdout/stderr.
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
