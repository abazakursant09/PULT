"""Trusted offline working-profile check; no service or operation admission.

OS/root, fixed CPython/stdlib, this entry and the independently supplied profile
SHA are trust roots. Only exact captured helper bytes execute before the payload
barrier. After that barrier only WorkingNamespace is instantiated: no clients,
credentials, SQLite, listener, controller, bootstrap or journal operation.
"""
import hashlib
from importlib.machinery import BuiltinImporter, FrozenImporter, PathFinder
import importlib
import io
import json
import os
from pathlib import Path, PurePosixPath
import re
import stat
import sys
import threading
from types import ModuleType
from uuid import UUID
import zipfile

FORMAT = "pult-working-launch-profile-check-v1"
FIELDS = {"format", "release", "entry_sha256", "actor", "namespace"}
ENTRY = "ops/backup/working_launch_profile.py"
CAPTURE = "ops/backup/operator_pg/bootstrap.py"
COLD = "ops/backup/custodian_v2/cold/cold_host.py"
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
APP_FILES = {
    "ops.backup.working_journal_v2": "ops/backup/working_journal_v2.py",
    "ops.backup.custodian_v2.codec.event_codec": "ops/backup/custodian_v2/codec/event_codec.py",
    "ops.backup.erasure_journal_s3": "ops/backup/erasure_journal_s3.py",
    "ops.backup.erasure_journal": "ops/backup/erasure_journal.py",
    "ops.canary.canary": "ops/canary/canary.py",
}
DEPENDENCY_NAMES = {"anyio", "certifi", "h11", "httpcore", "httpx", "idna", "typing_extensions"}
PAYLOAD_NAMES = DEPENDENCY_NAMES | {"ops", "anchor_v2", "protocol_v2", "event_codec",
                                      "event_store", "owned_host", "host_core"}
STDLIB_PATHS = {"/usr/lib/python312.zip", "/usr/lib/python3.12", "/usr/lib/python3.12/lib-dynload"}
_ATTEMPTED = False


class Refused(Exception):
    """Fixed diagnostics only; never echo paths, identifiers or provider data."""


def require(value):
    if not value:
        raise Refused("working_profile_refused")


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


def canonical(value):
    return (json.dumps(value, sort_keys=True, separators=(",", ":"),
                       ensure_ascii=True, allow_nan=False) + "\n").encode("ascii")


def path(value):
    require(type(value) is str and 1 < len(value) <= 4096
            and re.fullmatch(r"/[A-Za-z0-9_./-]+", value)
            and all(p not in ("", ".", "..") for p in value.split("/")[1:]))
    return Path(value)


def sha(value):
    require(type(value) is str and re.fullmatch("[0-9a-f]{64}", value))
    return value


def document(raw):
    require(type(raw) is bytes and 0 < len(raw) <= 20000)
    def pairs(rows):
        result = {}
        for key, value in rows:
            require(key not in result)
            result[key] = value
        return result
    value = json.loads(raw, object_pairs_hook=pairs,
                       parse_constant=lambda _: require(False))
    require(type(value) is dict and canonical(value) == raw)
    return value


def runtime():
    require(sys.platform == "linux" and sys.implementation.name == "cpython"
            and sys.version_info[:2] == (3, 12)
            and os.path.realpath(sys.executable) == "/usr/bin/python3.12"
            and sys.prefix == sys.base_prefix == "/usr"
            and sys.flags.isolated and sys.flags.no_site and sys.flags.no_user_site
            and sys.flags.ignore_environment and sys.flags.safe_path
            and sys.flags.dont_write_bytecode and sys.dont_write_bytecode and not sys.flags.optimize
            and threading.current_thread() is threading.main_thread()
            and len(os.listdir("/proc/self/task")) == 1)
    with open("/proc/self/status", "rb") as stream:
        raw = stream.read(65537)
    require(len(raw) <= 65536)
    fields = dict(row.split(":", 1) for row in raw.decode("ascii").splitlines() if ":" in row)
    uids, gids = [int(v) for v in fields["Uid"].split()], [int(v) for v in fields["Gid"].split()]
    require(len(uids) == len(gids) == 4 and 1000 <= uids[0] < 2**31 and 1000 <= gids[0] < 2**31
            and uids == [uids[0]] * 4 and gids == [gids[0]] * 4
            and os.getuid() == os.geteuid() == uids[0] and os.getgid() == os.getegid() == gids[0]
            and fields["Threads"].strip() == "1" and fields["NoNewPrivs"].strip() == "1"
            and not any(int(fields[k], 16) for k in ("CapEff", "CapPrm", "CapInh", "CapAmb", "CapBnd")))
    return uids[0], gids[0]


def fresh_interpreter():
    runtime()
    require(tuple(sys.meta_path) == (BuiltinImporter, FrozenImporter, PathFinder)
            and len(sys.path) == 3 and set(sys.path) == STDLIB_PATHS
            and not any(n.split(".", 1)[0] in PAYLOAD_NAMES for n in sys.modules))


def _helper_bytes(file):
    """Small trust-root read before the reusable Capture implementation executes."""
    descriptors, raw, failed = [], None, False
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
                and stat.S_IMODE(before.st_mode) == 0o444 and 0 < before.st_size <= 150000
                and not any(n.startswith("system.posix_acl_") for n in os.listxattr(fd)))
        raw = os.pread(fd, 150001, 0)
        after, linked = os.fstat(fd), os.stat(file.name, dir_fd=current, follow_symlinks=False)
        names = ("st_dev", "st_ino", "st_mode", "st_uid", "st_gid", "st_nlink", "st_size", "st_mtime_ns", "st_ctime_ns")
        require(len(raw) == before.st_size
                and all(getattr(before, n) == getattr(after, n) == getattr(linked, n) for n in names)
                and digest(raw) == PINS[CAPTURE])
    finally:
        for fd in reversed(descriptors):
            try:
                os.close(fd)
            except OSError:
                failed = True
        require(not failed)
    return raw


def _module(name, file, raw):
    module = ModuleType(name)
    module.__file__ = str(file)
    exec(compile(raw, str(file), "exec", dont_inherit=True, optimize=0), module.__dict__)
    return module


def shape(value):
    require(set(value) == FIELDS and value["format"] == FORMAT)
    release = value["release"]
    require(type(release) is dict and set(release) == {"root", "identity"})
    root = path(release["root"])
    identity = release["identity"]
    require(type(identity) is dict and set(identity) == {"device", "inode"}
            and all(type(v) is int and v >= 0 for v in identity.values()) and identity["inode"] > 0)
    sha(value["entry_sha256"])
    actor = value["actor"]
    require(type(actor) is dict and set(actor) == {"uid", "gid"}
            and all(type(v) is int and 1000 <= v < 2**31 for v in actor.values()))
    ns = value["namespace"]
    require(type(ns) is dict and set(ns) == {"endpoint", "region", "bucket", "scope", "generation"}
            and all(type(v) is str for v in ns.values())
            and ns["endpoint"] == "https://s3.ru-3.storage.selcloud.ru" and ns["region"] == "ru-3"
            and re.fullmatch(r"[a-z0-9][a-z0-9-]{1,61}[a-z0-9]", ns["bucket"])
            and ns["bucket"].startswith("pult-custodian-working-")
            and re.fullmatch(r"pult-work-[A-Za-z0-9_-]{1,54}", ns["scope"]))
    parsed = UUID(ns["generation"])
    require(parsed.version == 4 and str(parsed) == ns["generation"])
    return root


def _actor(spec):
    uid, gid = runtime()
    require(spec["actor"] == {"uid": uid, "gid": gid} and set(os.getgroups()).issubset({gid}))


def wheel_inventory(cold, bodies):
    require(set(bodies) == set(cold.WHEELS) and len(bodies) == 7)
    files, directories, total = {}, set(), 0
    for name, wanted in cold.WHEELS.items():
        body = bodies[name]
        require(type(body) is bytes and 0 < len(body) <= 2 * 1024 * 1024 and digest(body) == wanted)
        with zipfile.ZipFile(io.BytesIO(body)) as archive:
            for item in archive.infolist():
                relative = cold._relative(item.filename[:-1] if item.is_dir() else item.filename)
                kind = stat.S_IFMT(item.external_attr >> 16)
                require(item.filename == relative + ("/" if item.is_dir() else "")
                        and not item.flag_bits & 1
                        and kind in (0, stat.S_IFDIR if item.is_dir() else stat.S_IFREG))
                directories.update(str(p) for p in PurePosixPath(relative).parents if str(p) != ".")
                if item.is_dir():
                    directories.add(relative)
                    continue
                total += item.file_size
                require(relative not in files and 0 <= item.file_size <= 8 * 1024 * 1024
                        and total <= 64 * 1024 * 1024 and len(files) < 5000
                        and not relative.endswith((".pth", ".pyc", ".so")))
                raw = archive.read(item)
                require(len(raw) == item.file_size)
                files[relative] = raw
    require(files)
    return files, directories


def closed_layout(captured, root, files, extra_directories):
    directories = {"", *extra_directories}
    for name in files:
        directories.update(str(p) for p in PurePosixPath(name).parents if str(p) != ".")
    for name in sorted(directories):
        where = root / name
        info = captured.directory(where)
        require(stat.S_IMODE(info[2]) == 0o755)
        fd = captured.directories[where.parts[1:]]
        expected = {PurePosixPath(p).relative_to(name or ".").parts[0]
                    for p in files | directories - {""} if not name or p.startswith(name + "/")}
        actual = set()
        with os.scandir(fd) as entries:
            for entry in entries:
                require(len(actual) <= len(expected))
                actual.add(entry.name)
        require(actual == expected)


def module_tables(root, blobs, dependencies):
    modules, namespaces = {}, {}
    def add(name, file, body, package):
        require(name not in modules and all(p.isidentifier() for p in name.split(".")))
        modules[name] = (file, body, package)
        parts = name.split(".")
        for index in range(1, len(parts)):
            namespaces.setdefault(".".join(parts[:index]), file.parents[len(parts)-index if package else len(parts)-index-1])
    for name, relative in APP_FILES.items():
        add(name, root / relative, blobs[relative], False)
    for relative, body in dependencies.items():
        if relative.endswith(".py"):
            package = relative.endswith("/__init__.py")
            name = (relative[:-12] if package else relative[:-3]).replace("/", ".")
            require(name.split(".", 1)[0] in DEPENDENCY_NAMES)
            add(name, root / "deps" / relative, body, package)
    require(all(name not in modules or modules[name][2] for name in namespaces))
    return modules, namespaces


def _verify(file, approved):
    fresh_interpreter()
    file, approved = path(file), sha(approved)
    root = path(str(Path(__file__).absolute().parents[2]))
    require(path(str(Path(__file__).absolute())) == root / ENTRY and not file.is_relative_to(root))
    helper_file = root / CAPTURE
    helper = _module("_pult_working_capture_fixed_v1", helper_file, _helper_bytes(helper_file))
    with helper.Capture() as captured:
        raw = captured.take(file, 20000)
        require(digest(raw) == approved)
        spec = document(raw)
        require(shape(spec) == root)
        _actor(spec)
        identity = captured.directory(root)
        require(spec["release"]["identity"] == {"device": identity[0], "inode": identity[1]})
        blobs = {relative: captured.take(root / relative, 600000) for relative in PINS}
        require(all(digest(blobs[name]) == wanted for name, wanted in PINS.items()))
        entry = captured.take(root / ENTRY, 600000)
        require(digest(entry) == spec["entry_sha256"])
        captured.check()
        cold = _module("_pult_working_import_fixed_v1", root / COLD, blobs[COLD])
        cold._runtime()
        wheels = {name: captured.take(root / "wheels" / name, 2 * 1024 * 1024) for name in cold.WHEELS}
        dependencies, dependency_dirs = wheel_inventory(cold, wheels)
        for name, body in dependencies.items():
            require(captured.take(root / "deps" / name, 8 * 1024 * 1024) == body)
        files = set(PINS) | {ENTRY} | {"wheels/" + name for name in wheels} | {"deps/" + n for n in dependencies}
        closed_layout(captured, root, files, {"deps/" + n for n in dependency_dirs})
        captured.check()
        _actor(spec)
        modules, namespaces = module_tables(root, blobs, dependencies)
        finder = cold._Finder(modules, namespaces)
        try:
            fresh_interpreter()
            sys.meta_path.insert(0, finder)
            module = importlib.import_module("ops.backup.working_journal_v2")
            codec = sys.modules["ops.backup.custodian_v2.codec.event_codec"]
            require(module.Binding is codec.Binding and module.Event is codec.Event and module.Head is codec.Head
                    and module._validate_credential is sys.modules["ops.backup.erasure_journal_s3"]._validate_credential
                    and module.SelectelS3Transport is sys.modules["ops.canary.canary"].SelectelS3Transport)
            finder.check()
            captured.check()
            ns = module.WorkingNamespace(**spec["namespace"])
            key = "pult/erasure-v2/" + spec["namespace"]["scope"] + "/" + spec["namespace"]["generation"] + "/events/"
            require(type(ns) is module.WorkingNamespace and ns.key(1) == key + "00000000000000000001.json"
                    and ns.location == spec["namespace"]["endpoint"] + "/" + spec["namespace"]["bucket"] + "/" + key)
            finder.check()
            require({n for n in sys.modules if n.split(".", 1)[0] in PAYLOAD_NAMES} == set(finder.owned))
            captured.check()
            _actor(spec)
        finally:
            finder.rollback()
            fresh_interpreter()
            captured.check()
    # No success escapes before import rollback and descriptor close succeed.
    return dict(format="pult-working-profile-preflight-result-v1", status="VERIFIED_OFFLINE_NOT_LAUNCHED",
                profile_sha256=approved, source_files_verified=len(PINS) + 1,
                dependency_files_verified=len(dependencies), namespace_validated=True,
                credentials_opened=False, state_opened=False, live_clients_created=False,
                launch_admitted=False, journal_dispatch_admitted=False,
                purge_or_restore_admitted=False, remote_io_admitted=False)


def verify_profile(file, approved_sha256):
    global _ATTEMPTED
    try:
        require(not _ATTEMPTED)
        _ATTEMPTED = True
        return _verify(file, approved_sha256)
    except BaseException:
        raise Refused("working_profile_refused") from None


def main(argv=None):
    try:
        args = sys.argv[1:] if argv is None else argv
        require(len(args) == 3 and args[0] == "check")
        result = verify_profile(args[1], args[2])
        sys.stdout.buffer.write(canonical(result))
        return 0
    except BaseException:
        print("WORKING_PROFILE_REFUSED", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
