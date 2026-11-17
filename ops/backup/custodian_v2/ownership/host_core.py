"""Explicit Linux host preparation/entry point; no bootstrap, installer or live probe.

--check validates local inputs only. Opening existing SQLite may perform engine
crash recovery, never a protocol reset. Only --serve opens the authenticated
listener; neither startup mode reads the remote journal. Host provisioning and
real credential/IAM approval are separate operator gates.
"""
import argparse
import errno
import importlib.metadata
import json
import os
from pathlib import Path
import re
import stat
import struct
import sys
from uuid import UUID

FORMAT = "pult-erasure-custodian-host-v1"
MAX_INPUT = 8192
PACKAGES = {"anyio", "certifi", "h11", "httpcore", "httpx", "idna", "typing-extensions"}
CODE_FILES = ("ops/backup/erasure_custodian_host.py", "ops/backup/erasure_custodian.py",
              "ops/backup/erasure_anchor.py", "ops/backup/erasure_journal.py",
              "ops/backup/erasure_journal_s3.py", "ops/canary/canary.py",
              "ops/backup/custodian/requirements.lock")


class HostError(Exception):
    """Fixed diagnostics only; no input, credential, path or chained OS error."""


def _path(value):
    if type(value) is not str or "\0" in value:
        raise HostError("invalid host path")
    path = Path(value)
    if not path.is_absolute() or ".." in path.parts:
        raise HostError("invalid host path")
    return path


def _ancestors(path, owners):
    for parent in path.parents:
        info = parent.lstat()
        sticky_root = info.st_uid == 0 and bool(info.st_mode & stat.S_ISVTX)
        if (not stat.S_ISDIR(info.st_mode) or info.st_uid not in owners
                or (info.st_mode & 0o022 and not sticky_root)):
            raise HostError("untrusted host path")


def _acl(fd):
    try:
        raw = os.getxattr(fd, "system.posix_acl_access")
    except OSError as error:
        if error.errno in (errno.ENODATA, errno.ENOTSUP):
            return None
        raise HostError("credential access validation unavailable") from None
    if len(raw) < 4 or (len(raw) - 4) % 8 or struct.unpack("<I", raw[:4]) != (2,):
        raise HostError("unsupported credential access")
    result = {}
    for offset in range(4, len(raw), 8):
        tag, permission, identity = struct.unpack("<HHI", raw[offset:offset + 8])
        if (tag, identity) in result:
            raise HostError("unsupported credential access")
        result[tag, identity] = permission
    return result


def _read_file(path, *, service_uid=None):
    """Descriptor validation before bounded read, with no symlink/FIFO blocking."""
    descriptor = None
    try:
        path = _path(str(path))
        owners = {0} if service_uid is None else {0, service_uid}
        _ancestors(path, owners)
        descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC)
        info = os.fstat(descriptor)
        mode = stat.S_IMODE(info.st_mode)
        if (not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_uid not in owners
                or not 0 < info.st_size <= MAX_INPUT):
            raise HostError("untrusted host file")
        if service_uid is None:
            if mode not in (0o444, 0o644):
                raise HostError("root-owned non-secret configuration required")
        else:
            acl = _acl(descriptor)
            ordinary = info.st_uid == service_uid and mode in (0o400, 0o600) and acl is None
            undefined = 0xffffffff
            projected = (info.st_uid == 0 and mode == 0o440 and acl == {
                (1, undefined): 4, (2, service_uid): 4, (4, undefined): 0,
                (16, undefined): 4, (32, undefined): 0})
            if not (ordinary or projected):
                raise HostError("private reader credential required")
        body = bytearray()
        while len(body) <= MAX_INPUT:
            chunk = os.read(descriptor, min(4096, MAX_INPUT + 1 - len(body)))
            if not chunk:
                break
            body.extend(chunk)
        after = os.fstat(descriptor)
        if (len(body) > MAX_INPUT or len(body) != info.st_size
                or (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns)
                != (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns, after.st_ctime_ns)):
            raise HostError("host file changed or exceeded bound")
        return bytes(body)
    except (OSError, UnicodeError, ValueError, TypeError):
        raise HostError("host file unavailable") from None
    finally:
        if descriptor is not None:
            os.close(descriptor)


def _pairs(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise HostError("duplicate host field")
        result[key] = value
    return result


def _constant(value):
    raise HostError("non-finite host value")


def _json(body):
    if type(body) is not bytes or not 0 < len(body) <= MAX_INPUT:
        raise HostError("invalid host input size")
    try:
        value = json.loads(body.decode("utf-8"), object_pairs_hook=_pairs, parse_constant=_constant)
        if type(value) is not dict:
            raise HostError("host object required")
        return value
    except (ValueError, TypeError, UnicodeError, RecursionError):
        raise HostError("invalid host JSON") from None


def _id(value):
    if type(value) is not int or not 0 < value < 2**31:
        raise HostError("invalid dedicated identity")
    return value


def parse_config(body):
    value = _json(body)
    keys = {"format", "scope", "generation", "custodian_uid", "writer_uid", "socket_gid",
            "state_file", "socket_file", "s3"}
    if set(value) != keys or value["format"] != FORMAT:
        raise HostError("unsupported host configuration")
    if type(value["scope"]) is not str or not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", value["scope"]):
        raise HostError("invalid host scope")
    try:
        generation = UUID(value["generation"]) if type(value["generation"]) is str else None
    except ValueError:
        generation = None
    if generation is None or generation.version != 4 or str(generation) != value["generation"]:
        raise HostError("invalid host generation")
    for field in ("custodian_uid", "writer_uid", "socket_gid"):
        _id(value[field])
    if value["custodian_uid"] == value["writer_uid"]:
        raise HostError("separate host principals required")
    state, runtime = _path(value["state_file"]).parent, _path(value["socket_file"]).parent
    if state == runtime or state in runtime.parents or runtime in state.parents:
        raise HostError("separate state and socket directories required")
    s3 = value["s3"]
    if (type(s3) is not dict or set(s3) != {"endpoint", "region", "bucket", "key"}
            or any(type(item) is not str for item in s3.values())
            or s3["key"] != "pult/erasure-test/" + value["scope"] + "/journal.json"):
        raise HostError("invalid explicit journal location")
    return value


def _trusted_code():
    root = Path(__file__).absolute().parents[2]
    try:
        if any(name == "ops" or name.startswith("ops.") for name in sys.modules):
            raise HostError("fresh isolated host process required")
        # Validate the entire importable source tree before adding it to sys.path.
        # Suffix checks alone miss same-stem packages and root dependency shadows.
        children = {}
        for name in CODE_FILES:
            parts = Path(name).parts
            for index, part in enumerate(parts):
                children.setdefault(Path(*parts[:index]), set()).add(part)
        runtime = Path(sys.executable).absolute().parent.parent
        for relative, expected in children.items():
            directory = root / relative
            _ancestors(directory / "entry", {0})
            info = directory.lstat()
            if (not stat.S_ISDIR(info.st_mode) or info.st_uid != 0 or info.st_mode & 0o022):
                raise HostError("root-controlled source directory required")
            allowed = expected | ({"venv"} if relative == Path() and runtime == root / "venv" else set())
            if {entry.name for entry in directory.iterdir()} != allowed:
                raise HostError("unexpected source tree entry")
        for name in CODE_FILES:
            path = root / name
            _ancestors(path, {0})
            info = path.lstat()
            if (not stat.S_ISREG(info.st_mode) or info.st_uid != 0 or info.st_nlink != 1
                    or stat.S_IMODE(info.st_mode) not in (0o444, 0o644)):
                raise HostError("root-controlled service code required")
        return root
    except (OSError, UnicodeError):
        raise HostError("service code unavailable") from None


def _runtime(root):
    if not sys.flags.isolated or not sys.flags.no_site or not sys.dont_write_bytecode or sys.version_info < (3, 12):
        raise HostError("isolated no-site Python runtime required")
    text = (root / "ops/backup/custodian/requirements.lock").read_text(encoding="ascii")
    pins = re.findall(r"^([a-z][a-z0-9-]*)==([0-9.]+) \\$", text, re.MULTILINE)
    if len(pins) != len(PACKAGES) or {name for name, _ in pins} != PACKAGES:
        raise HostError("unsupported service dependency set")
    runtime = Path(sys.executable).absolute().parent.parent
    configuration = _read_file(runtime / "pyvenv.cfg").decode("utf-8")
    if not re.search(r"^include-system-site-packages\s*=\s*false\s*$", configuration, re.MULTILINE):
        raise HostError("closed virtual environment required")
    site = runtime / "lib" / ("python%d.%d" % sys.version_info[:2]) / "site-packages"
    try:
        _ancestors(site / "entry", {0})
        expected = dict(pins)
        top = {"anyio", "certifi", "h11", "httpcore", "httpx", "idna", "typing_extensions.py", "__pycache__"}
        top |= {name.replace("-", "_") + "-" + version + ".dist-info" for name, version in pins}
        if not {entry.name for entry in site.iterdir()}.issubset(top):
            raise HostError("unexpected runtime entry")
        pending, count, total = [site], 0, 0
        while pending:
            path = pending.pop()
            info = path.lstat()
            count += 1
            if (count > 5000 or info.st_uid != 0 or info.st_mode & 0o022
                    or not (stat.S_ISDIR(info.st_mode) or stat.S_ISREG(info.st_mode))):
                raise HostError("untrusted runtime entry")
            if stat.S_ISDIR(info.st_mode):
                pending.extend(path.iterdir())
            else:
                total += info.st_size
                if info.st_nlink != 1 or total > 64 * 1024 * 1024 or path.suffix == ".pth":
                    raise HostError("untrusted runtime file")
        distributions = list(importlib.metadata.distributions(path=[str(site)]))
        found = {row.metadata["Name"].lower().replace("_", "-"): row.version for row in distributions}
        if found != expected or len(distributions) != len(expected):
            raise HostError("service dependency version mismatch")
        sys.path[:0] = [str(root), str(site)]  # no site/.pth execution or environment path
    except (OSError, UnicodeError, ValueError, KeyError):
        raise HostError("service runtime unavailable") from None


def build_host(config_file, credential_file, *, _config_snapshot=None, _ownership_guard=None):
    """No remote I/O/listener/protocol writes; returns a ready local adapter only."""
    if sys.platform != "linux":
        raise HostError("Linux host required")
    if type(_config_snapshot) is not bytes or not callable(_ownership_guard):
        raise HostError("owned entry and approved snapshot required")
    root = _trusted_code()
    _runtime(root)
    from ops.backup.erasure_custodian import UnixCustodianServer, _principal
    from ops.backup.erasure_anchor import Custodian, SQLiteAnchor
    from ops.backup.erasure_journal_s3 import S3JournalReader
    for name in ("erasure_custodian", "erasure_anchor", "erasure_journal", "erasure_journal_s3"):
        if Path(sys.modules["ops.backup." + name].__file__).absolute() != root / "ops/backup" / (name + ".py"):
            raise HostError("unexpected host module origin")
    if Path(sys.modules["ops.canary.canary"].__file__).absolute() != root / "ops/canary/canary.py":
        raise HostError("unexpected signer module origin")
    uid = _principal()
    config = parse_config(_config_snapshot)
    groups = dict(line.split(":", 1) for line in Path("/proc/self/status").read_text().splitlines() if ":" in line)
    if (config["custodian_uid"] != uid or [int(x) for x in groups["Gid"].split()] != [config["socket_gid"]] * 4
            or not set(os.getgroups()).issubset({config["socket_gid"]})):
        raise HostError("host process identity mismatch")
    credential = _json(_read_file(credential_file, service_uid=uid))
    reader = S3JournalReader(**config["s3"], reader=credential)
    try:
        anchor = SQLiteAnchor(config["state_file"], scope=config["scope"],
                              generation=config["generation"], location=reader.location)
        _ownership_guard(anchor)  # before server constructor performs its first state load
        server = UnixCustodianServer(Custodian(anchor, reader), config["socket_file"], writer_uid=config["writer_uid"])
        if os.path.lexists(server.path):
            raise HostError("existing socket requires operator review")
        return server, reader
    except BaseException:
        reader.close()
        raise


class _Parser(argparse.ArgumentParser):
    def error(self, message):
        raise HostError("invalid host invocation")


def main(argv=None):
    reader = None
    status, checked = 1, False
    try:
        parser = _Parser(description="Custodian host entry; --check is offline, not deployment/IAM approval")
        parser.add_argument("--config", required=True)
        parser.add_argument("--reader-credential-file", required=True)
        mode = parser.add_mutually_exclusive_group(required=True)
        mode.add_argument("--check", action="store_true")
        mode.add_argument("--serve", action="store_true")
        args = parser.parse_args(argv)
        server, reader = build_host(args.config, args.reader_credential_file)
        checked = args.check
        if not checked:
            server.serve()
        status = 0
    except Exception:
        status = 1
    finally:
        if reader is not None:
            try:
                reader.close()
            except Exception:
                status = 1
    if status:
        print("HOST_REFUSED", file=sys.stderr)
    elif checked:
        print("HOST_PREFLIGHT_OK_OFFLINE_ONLY")
    return status


if __name__ == "__main__":
    raise SystemExit(main())
