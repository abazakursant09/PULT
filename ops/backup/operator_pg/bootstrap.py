"""Dormant synthetic-PG source envelope; no working DB/credential activation.

The fixed OS-trusted bootstrap and an independently supplied envelope digest are
trust roots. The immutable old SYNTHETIC profile is not widened. Native dependency
imports reopen root-protected files under the trusted-root/OS boundary; this is
not a hostile-root or malicious-kernel defence and grants no mutation authority.
"""
import hashlib
import io
import json
import os
from pathlib import Path, PurePosixPath
import re
import stat
import sys
import threading
from types import ModuleType
import zipfile

FORMAT = "pult-operator-pg-source-envelope-v1"
MODE = "SYNTHETIC_PG_REVIEW_SOURCE_ONLY"
FIELDS = {"format", "mode", "operation", "release", "legacy", "sources",
          "dependencies", "binding"}
PATHS = {
    "journal": "ops/backup/erasure_journal.py",
    "anchor": "ops/backup/erasure_anchor.py",
    "ipc": "ops/backup/erasure_custodian.py",
    "operator": "ops/backup/erasure_operator.py",
    "stop": "ops/backup/custodian_v2/stop/constructor_stop.py",
    "supervisor": "ops/backup/custodian_v2/controller/supervisor_v2.py",
    "core": "ops/backup/custodian_v2/controller/frozen/admission.py",
    **{key: "ops/backup/operator_host/"+key+".py"
       for key in ("profile", "host", "bootstrap", "worker")},
    "policy": "backend/services/account_closure_policy.py",
    "reviewer": "backend/services/erasure_operator_review.py",
    "pg_bootstrap": "ops/backup/operator_pg/bootstrap.py",
    "pg_runtime": "ops/backup/operator_pg/runtime.py",
}
PINS = {
    "journal": "94eeba19e645e25ec8286baebe4092a29c541c6f12858e1ff7b303f6a1a4fc98",
    "anchor": "b399803ac7a03aaa5a506cbb0036f98c153457769beabf46af44864b190861a8",
    "ipc": "3eb793a1ef1002c6def97b52c579b2ff051e834b44b802a75aca09d640a691d3",
    "operator": "c4be8ed53a803afe37cf6c72ac9fa462167ace9401575ee1f85faa2774a1365f",
    "stop": "5406c5a81498013884ac46d0279219f299db40576357692604fb4f08c251ca8c",
    "supervisor": "3d05d22163b11f815b29c222128c4e41987c9e4a83e7b4906cbe63a18941a37e",
    "core": "961cc3aef5dcfe9d6286b15a2d5c087a33dc1e5dafdc7d5a16f6eded961afea7",
    "profile": "0998c8922c470406d2789727b749ed9365f36f2fa3ee70eea7448d3693e31edf",
    "host": "5a47b32282fa4c6533ea0c4ed2602523dc2054cb8e567da571de3962b877e356",
    "bootstrap": "fbe97312685869a147f60f8acbf173692ca4047380c2db5b659ce7479f749519",
    "worker": "5e7993f35e9d50019af79ecb2335cb2fe5111f38f06c83f70fc01304d425a06c",
    "policy": "e606c8b81793a54caa94b1bf9d3c88a21dc7df0f211d60cca6b8a483d8e750cf",
    "reviewer": "efaa87935a5e6df3fdb8077cabba1b41f0cbb725d11430e896c454483ccc2606",
}
WHEELS = {
    "asyncpg-0.31.0-cp312-cp312-manylinux_2_28_x86_64.whl":
        "aad7a33913fb8bcb5454313377cc330fbb19a0cd5faa7272407d8a0c4257b671",
    "greenlet-3.5.5-cp312-cp312-manylinux_2_24_x86_64.manylinux_2_28_x86_64.whl":
        "147b25a42e5ca5be3d42356e8f608b37af715a1c196e9bf9d1627f3341adfe1d",
    "sqlalchemy-2.0.51-cp312-cp312-manylinux2014_x86_64.manylinux_2_17_x86_64.manylinux_2_28_x86_64.whl":
        "1d21ce524ab86c23046e992a5b81cb54c21079c6df6e78b8fc77d77cac70a6b9",
    "typing_extensions-4.16.0-py3-none-any.whl":
        "481caa481374e813c1b176ada14e97f1f67a4539ce9cfeb3f350d78d6370c2e8",
}
_TOKEN = object()
_LOAD_PHASE = "not_started"
_CAPTURE_MISMATCH = "none"


class Refused(Exception):
    """Only fixed, secret-free diagnostics cross the loader boundary."""


def require(value):
    if not value:
        raise Refused("synthetic_pg_source_refused")


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


def canonical(value):
    return (json.dumps(value, sort_keys=True, separators=(",", ":"),
                       ensure_ascii=True, allow_nan=False)+"\n").encode("ascii")


def document(raw):
    def pairs(rows):
        result = {}
        for key, value in rows:
            require(key not in result)
            result[key] = value
        return result
    value = json.loads(raw, object_pairs_hook=pairs)
    require(type(value) is dict and canonical(value) == raw)
    return value


def runtime():
    require(sys.platform == "linux" and sys.implementation.name == "cpython"
            and sys.version_info[:2] == (3, 12)
            and os.path.realpath(sys.executable) == "/usr/bin/python3.12"
            and sys.flags.isolated and sys.flags.no_site and sys.flags.no_user_site
            and sys.flags.ignore_environment and sys.flags.safe_path
            and sys.dont_write_bytecode and not sys.flags.optimize
            and threading.current_thread() is threading.main_thread()
            and len(os.listdir("/proc/self/task")) == 1)


def path(value):
    require(type(value) is str and 1 < len(value) <= 4096
            and re.fullmatch(r"/[A-Za-z0-9_./-]+", value)
            and all(part not in ("", ".", "..") for part in value.split("/")[1:]))
    return Path(value)


def sha(value):
    require(type(value) is str and re.fullmatch("[0-9a-f]{64}", value))
    return value


def stamp(info):
    return tuple(getattr(info, key) for key in (
        "st_dev", "st_ino", "st_mode", "st_uid", "st_gid", "st_nlink", "st_size",
        "st_mtime_ns", "st_ctime_ns"))


def capture_stamp(info):
    # A root-owned sticky shared ancestor permits unrelated sibling churn.
    # Pin its protection/identity, not directory-content timestamps/link count.
    # The descriptor-relative child chain and every file/owned-tree stamp stay
    # exact. ACLs are rechecked separately on the same retained descriptor.
    if stat.S_ISDIR(info.st_mode) and info.st_uid == 0 and info.st_mode & stat.S_ISVTX:
        return tuple(getattr(info, key) for key in ("st_dev", "st_ino", "st_mode", "st_uid", "st_gid"))
    return stamp(info)


def no_acl(fd):
    require(not any(name.startswith("system.posix_acl_") for name in os.listxattr(fd)))


class Capture:
    """Descriptor-relative protected capture, following the accepted old loader.

    Verify bytes before the execution barrier, then retain the same read-only
    descriptors and parent chains through one Admission lifetime. No fallback.
    """
    def __init__(self):
        self.entries, self.directories, self.files = [], {}, {}
        self.content_pins = {}
        self.pid, self.thread = os.getpid(), threading.current_thread()
        self.closed = self.close_failed = False

    def __enter__(self):
        return self

    def __exit__(self, *unused):
        self.close()

    def close(self):
        require(os.getpid() == self.pid and threading.current_thread() is self.thread)
        if self.closed:
            require(not self.close_failed)
            return
        self.closed = True
        for fd, _, _, before in reversed(self.entries):
            try:
                current = os.fstat(fd)
                if (current.st_dev, current.st_ino) != (before.st_dev, before.st_ino):
                    # A different underlying object is foreign. Same-object FD
                    # replacement by hostile in-process code is outside this
                    # trusted-process model, not a capability claim.
                    self.close_failed = True
                    continue
                os.close(fd)
            except OSError:
                self.close_failed = True
        self.entries.clear()
        self.directories.clear()
        self.content_pins.clear()
        require(not self.close_failed)

    def transfer(self):
        """Move the already verified descriptor closure to one Admission owner."""
        self.check()
        held = Capture()
        held.entries, self.entries = self.entries, []
        held.directories, self.directories = self.directories, {}
        held.files, self.files = self.files, {}
        held.content_pins, self.content_pins = self.content_pins, {}
        return held

    def _directory(self, parts):
        prefix = ()
        if prefix not in self.directories:
            fd = os.open("/", os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC)
            info = os.fstat(fd)
            self.entries.append((fd, None, "/", info))
            self._dir_guard(fd, info)
            self.directories[prefix] = fd
        for part in parts:
            child = prefix+(part,)
            if child not in self.directories:
                parent = self.directories[prefix]
                fd = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC,
                             dir_fd=parent)
                info = os.fstat(fd)
                self.entries.append((fd, parent, part, info))
                self._dir_guard(fd, info)
                self.directories[child] = fd
            prefix = child
        return self.directories[prefix]

    @staticmethod
    def _dir_guard(fd, info):
        require(stat.S_ISDIR(info.st_mode) and info.st_uid == 0
                and (not info.st_mode & 0o022 or info.st_mode & stat.S_ISVTX))
        no_acl(fd)

    def directory(self, file):
        file = path(str(file))
        fd = self._directory(file.parts[1:])
        return stamp(os.fstat(fd))

    def take(self, file, maximum):
        file = path(str(file))
        parent = self._directory(file.parts[1:-1])
        fd = os.open(file.name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC,
                     dir_fd=parent)
        before = os.fstat(fd)
        self.entries.append((fd, parent, file.name, before))
        require(stat.S_ISREG(before.st_mode) and before.st_uid == 0
                and before.st_nlink == 1 and stat.S_IMODE(before.st_mode) == 0o444
                and 0 <= before.st_size <= maximum)
        no_acl(fd)
        raw = os.pread(fd, maximum+1, 0)
        require(len(raw) == before.st_size and stamp(os.fstat(fd)) == stamp(before))
        wanted = digest(raw)
        self.files[str(file)] = (stamp(before), wanted, maximum)
        self.content_pins[fd] = (len(raw), wanted)
        return raw

    def check(self):
        global _CAPTURE_MISMATCH
        require(not self.closed and os.getpid() == self.pid
                and threading.current_thread() is self.thread)
        for fd, parent, name, before in self.entries:
            no_acl(fd)
            linked = os.stat(name, dir_fd=parent, follow_symlinks=False)
            if capture_stamp(os.fstat(fd)) != capture_stamp(before) or capture_stamp(linked) != capture_stamp(before):
                _CAPTURE_MISMATCH = ("shared_tmp_directory" if stat.S_ISDIR(before.st_mode)
                                     and name == "tmp" else "protected_directory"
                                     if stat.S_ISDIR(before.st_mode) else "protected_file")
                require(False)
            if stat.S_ISREG(before.st_mode):
                require(fd in self.content_pins)
                length, wanted = self.content_pins[fd]
                body = os.pread(fd, length+1, 0)
                require(len(body) == length and digest(body) == wanted
                        and stamp(os.fstat(fd)) == stamp(before)
                        and stamp(os.stat(name, dir_fd=parent, follow_symlinks=False)) == stamp(before))


def shape(spec):
    require(set(spec) == FIELDS and spec["format"] == FORMAT and spec["mode"] == MODE)
    require(type(spec["release"]) is dict and set(spec["release"]) == {"root", "identity"})
    release = path(spec["release"]["root"])
    require(type(spec["legacy"]) is dict and set(spec["legacy"]) == {"path", "sha256"})
    path(spec["legacy"]["path"])
    sha(spec["legacy"]["sha256"])
    require(type(spec["sources"]) is dict and set(spec["sources"]) == set(PATHS))
    for name, relative in PATHS.items():
        row = spec["sources"][name]
        require(type(row) is dict and set(row) == {"path", "sha256"}
                and row["path"] == str(release/relative))
        sha(row["sha256"])
        require(name not in PINS or row["sha256"] == PINS[name])
    deps = spec["dependencies"]
    require(type(deps) is dict and set(deps) == {"root", "identity", "wheels_root", "wheels"}
            and deps["wheels"] == WHEELS)
    dep_root, wheel_root = path(deps["root"]), path(deps["wheels_root"])
    require(all(a != b and a not in b.parents and b not in a.parents
                for a, b in ((release, dep_root), (release, wheel_root), (dep_root, wheel_root))))
    require(type(spec["binding"]) is dict and set(spec["binding"]) == {"path", "sha256"})
    path(spec["binding"]["path"])
    sha(spec["binding"]["sha256"])
    return release, dep_root, wheel_root


def wheel_inventory(bodies, *, with_directories=False):
    require(type(bodies) is dict and set(bodies) == set(WHEELS))
    require(type(with_directories) is bool)
    entries, directories, count, total = {}, set(), 0, 0
    for name in WHEELS:
        raw = bodies[name]
        require(type(raw) is bytes and digest(raw) == WHEELS[name])
        with zipfile.ZipFile(io.BytesIO(raw)) as archive:
            for info in archive.infolist():
                relative = PurePosixPath(info.filename)
                require(not relative.is_absolute() and relative.parts
                        and str(relative) == info.filename.rstrip("/")
                        and all(p not in (".", "..", "") for p in relative.parts)
                        and "\\" not in info.filename and not info.flag_bits & 1
                        and not stat.S_ISLNK(info.external_attr >> 16)
                        and relative.suffix not in (".pyc", ".pth"))
                directories.update(str(parent) for parent in relative.parents if parent.parts)
                if info.is_dir():
                    directories.add(str(relative))
                    continue
                count += 1
                total += info.file_size
                require(count <= 5000 and total <= 50000000 and str(relative) not in entries)
                body = archive.read(info)
                require(len(body) == info.file_size)
                entries[str(relative)] = digest(body)
    require(entries and count == len(entries))
    return (entries, directories) if with_directories else entries


def _binding(raw):
    value = document(raw)
    fields = {"format", "fixture_only", "socket", "database", "database_oid",
              "reader_role", "reader_password", "application_name"}
    require(set(value) == fields and value["format"] == "pult-synthetic-pg-binding-v1"
            and value["fixture_only"] is True)
    require(type(value["database"]) is str
            and re.fullmatch("op_pg_[0-9a-f]{32}", value["database"]))
    token = value["database"][6:]
    require(value["reader_role"] == "op_reader_"+token
            and value["application_name"] == "op_review_"+token
            and type(value["reader_password"]) is str
            and re.fullmatch("synthetic_[0-9a-f]{32}", value["reader_password"])
            and type(value["database_oid"]) is int and 16384 <= value["database_oid"] < 2**32)
    socket = path(value["socket"])
    require(socket.name == "pgsocket" and socket.parent.parent == Path("/tmp")
            and re.fullmatch("pult-op-pg-[A-Za-z0-9_-]+", socket.parent.name))
    return {key: value[key] for key in fields-{"format", "fixture_only"}}


def _identity(info_stamp, wanted):
    require(type(wanted) is dict and set(wanted) == {"device", "inode"}
            and all(type(wanted[k]) is int for k in wanted)
            and wanted == {"device": info_stamp[0], "inode": info_stamp[1]})


def dependency_tree(captured, deps):
    """Exact files AND directories; directory symlinks/ACLs are not invisible."""
    directories = {deps: captured.directory(deps)}
    files = set()
    for current, subdirs, leaves in os.walk(deps, followlinks=False):
        require(len(directories)+len(files) <= 6000)
        for name in subdirs:
            child = Path(current)/name
            info = captured.directory(child)  # O_NOFOLLOW on every component.
            require(stat.S_IMODE(info[2]) == 0o755)
            directories[child] = info
        for name in leaves:
            files.add(str((Path(current)/name).relative_to(deps)))
    return directories, files


class Admission:
    """Captured synthetic source closure, not a DB/mutation capability."""
    def __init__(self, token, *, file, approved, raw, spec, blobs, files,
                 directories, deps, binding, legacy, captured_guard=None):
        require(token is _TOKEN)
        require(type(captured_guard) is Capture and not captured_guard.closed)
        self.file, self.sha, self.raw, self.spec = file, approved, raw, spec
        self.blobs, self.deps, self.binding, self.legacy = blobs, deps, binding, legacy
        self._files, self._directories, self._module = files, directories, None
        self._legacy_type = type(legacy)
        self._capture = captured_guard

    def __repr__(self):
        return "<SyntheticPGSourceClosure no working DB or mutation admission>"

    def check(self):
        runtime()
        require(canonical(self.spec) == self.raw and digest(self.raw) == self.sha)
        _, deps, _ = shape(self.spec)
        require(self.deps == deps and self._files[str(self.file)][1] == self.sha
                and type(self.blobs) is dict and set(self.blobs) == set(PATHS)
                and all(type(self.blobs[k]) is bytes and digest(self.blobs[k]) ==
                        self.spec["sources"][k]["sha256"] for k in PATHS)
                and type(self.legacy) is self._legacy_type
                and str(self.legacy.file) == self.spec["legacy"]["path"]
                and self.legacy.sha == self.spec["legacy"]["sha256"])
        binding_raw = canonical(dict(format="pult-synthetic-pg-binding-v1",
                                     fixture_only=True, **self.binding))
        require(digest(binding_raw) == self.spec["binding"]["sha256"])
        _binding(binding_raw)
        # Preserve full content hashes at every check, but read through the
        # already approved descriptors instead of reopening/walking hundreds
        # of paths. Protection, ACLs, full stamps and path links are rechecked;
        # owned-tree directory stamps preserve the exact installed inventory.
        self._capture.check()
        self.legacy.check()

    def close(self):
        """One-shot descriptor release; any close uncertainty stays poisoned."""
        self._capture.close()

    def __enter__(self):
        self.check()
        return self

    def __exit__(self, *unused):
        self.close()

    def actor_guard(self):
        self.check()
        return self.legacy.actor_guard()

    def root_guard(self):
        self.check()
        return self.legacy.root_guard()

    def runtime(self):
        self.check()
        if self._module is None:
            name = "_pult_operator_pg_runtime_v1"
            require(name not in sys.modules)
            module = ModuleType(name)
            module.__file__ = self.spec["sources"]["pg_runtime"]["path"]
            module.__dict__.update(ADMISSION_TYPE=Admission, REFUSED=Refused)
            sys.modules[name] = module
            try:
                exec(compile(self.blobs["pg_runtime"], module.__file__, "exec",
                             dont_inherit=True, optimize=0), module.__dict__)
                self.check()
            except BaseException:
                if sys.modules.get(name) is module:
                    del sys.modules[name]
                raise
            self._module = module
        return self._module


def _load(file, approved):
    global _LOAD_PHASE
    _LOAD_PHASE = "runtime"
    runtime()
    file, approved = path(os.fspath(file)), sha(approved)
    with Capture() as captured:
        _LOAD_PHASE = "envelope"
        raw = captured.take(file, 20000)
        require(digest(raw) == approved)
        spec = document(raw)
        release, deps, wheel_root = shape(spec)
        require(spec["sources"]["pg_bootstrap"]["path"] == str(Path(__file__)))
        release_stamp, deps_stamp = captured.directory(release), captured.directory(deps)
        _identity(release_stamp, spec["release"]["identity"])
        _identity(deps_stamp, spec["dependencies"]["identity"])
        require(stat.S_IMODE(release_stamp[2]) == stat.S_IMODE(deps_stamp[2]) == 0o755)
        blobs = {}
        _LOAD_PHASE = "sources"
        for name in PATHS:
            row = spec["sources"][name]
            body = captured.take(row["path"], 150000)
            require(digest(body) == row["sha256"])
            blobs[name] = body
        legacy_raw = captured.take(spec["legacy"]["path"], 16384)
        require(digest(legacy_raw) == spec["legacy"]["sha256"])
        legacy_spec = document(legacy_raw)
        require(spec["operation"] == legacy_spec["operation"]
                and spec["release"] == legacy_spec["release"]
                and legacy_spec["sources"] == {k: spec["sources"][k] for k in PATHS
                                               if k not in ("policy", "reviewer", "pg_bootstrap", "pg_runtime")})
        _LOAD_PHASE = "dependencies"
        bodies = {name: captured.take(wheel_root/name, 12000000) for name in WHEELS}
        inventory, expected_directories = wheel_inventory(bodies, with_directories=True)
        del bodies
        # Reject extras, including bytecode, namespace shadows, symlinks and ACLs.
        dep_directories, actual = dependency_tree(captured, deps)
        require(actual == set(inventory)
                and set(dep_directories) == {deps, *(deps/relative for relative in expected_directories)})
        for relative, wanted in inventory.items():
            require(digest(captured.take(deps/relative, 12000000)) == wanted)
        _LOAD_PHASE = "binding"
        binding_raw = captured.take(spec["binding"]["path"], 4096)
        require(digest(binding_raw) == spec["binding"]["sha256"])
        binding = _binding(binding_raw)
        _LOAD_PHASE = "pre_exec_check"
        captured.check()  # No application code executes before this barrier.
        name = "_pult_operator_pg_legacy_bootstrap_v1"
        require(name not in sys.modules)
        old = ModuleType(name)
        old.__file__ = spec["sources"]["bootstrap"]["path"]
        sys.modules[name] = old
        try:
            _LOAD_PHASE = "legacy_load"
            exec(compile(blobs["bootstrap"], old.__file__, "exec", dont_inherit=True,
                         optimize=0), old.__dict__)
            legacy = old.load_profile(spec["legacy"]["path"], spec["legacy"]["sha256"])
            _LOAD_PHASE = "post_exec_check"
            captured.check()
        except BaseException:
            if sys.modules.get(name) is old:
                del sys.modules[name]
            raise
        directories = {release: release_stamp, **dep_directories}
        files = dict(captured.files)
        held = captured.transfer()
        try:
            result = Admission(_TOKEN, file=file, approved=approved, raw=raw, spec=spec,
                               blobs=blobs, files=files, directories=directories,
                               deps=deps, binding=binding, legacy=legacy, captured_guard=held)
        except BaseException:
            held.close()
            raise
    _LOAD_PHASE = "post_load_check"
    try:
        result.check()
    except BaseException:
        result.close()
        raise
    _LOAD_PHASE = "loaded"
    return result


def load_admission(file, approved_sha):
    try:
        return _load(file, approved_sha)
    except BaseException:
        pass
    raise Refused("synthetic_pg_source_refused") from None


def main():
    phase, entry, admission, status = "entry", None, None, 1
    try:
        require(len(sys.argv) == 4 and sys.argv[1] == "worker")
        phase = "load"
        admission = load_admission(sys.argv[2], sys.argv[3])
        phase = "actor_guard"
        admission.actor_guard()
        phase = "runtime_load"
        entry = admission.runtime()
        phase = "worker"
        entry.worker(admission)
        status = 0
    except BaseException as error:
        try:
            # Only fixed implementation stages/types, never paths, IDs, values,
            # driver messages, SQL or exception text. Public API stays fixed.
            stage = "load_"+_LOAD_PHASE if phase == "load" else phase
            if phase == "worker" and entry is not None:
                stage = "worker_"+entry.FAILURE_PHASE
            require(re.fullmatch("[a-z_]{1,80}", stage)
                    and _CAPTURE_MISMATCH in ("none", "shared_tmp_directory",
                                             "protected_directory", "protected_file"))
            os.write(2, canonical(dict(format="pult-synthetic-pg-worker-failure-v1",
                stage=stage, capture=_CAPTURE_MISMATCH, exception_type=type(error).__name__)))
        except BaseException:
            pass
    finally:
        if admission is not None:
            try:
                admission.close()
            except BaseException:
                status = 1
    return status


if __name__ == "__main__":
    raise SystemExit(main())
