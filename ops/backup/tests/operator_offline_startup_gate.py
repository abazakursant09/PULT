"""Private CI startup barrier around exact frozen operator proof bytes.

No source rewriting, new cases, runtime policy or retry. Only the synthetic
term-descendant fixture delays its external READY publication until setup ends.
"""
import hashlib
import json
import os
from pathlib import Path
import stat
from types import ModuleType

LEGACY_SHA = "70ed55b2fc5ee22aa0f90391a99f817728079b77726b793e8212fa18da312568"
LEGACY = "operator_offline_host_proof.py"
CHILD_LEGACY = "legacy-operator-proof.py"
ENTRY = "operator_offline_startup_gate.py"
MAX_SOURCE = 100000


class StartupGateRefused(RuntimeError):
    pass


def _require(value):
    if not value:
        raise StartupGateRefused("operator_startup_gate_refused")


def _read_file(file, child=False):
    """Bounded no-follow read, before any captured library definitions execute."""
    try:
        if child:
            _require(file.parent.parent == Path("/tmp") and file.parent.name.startswith("pult-offline-"))
            for parent in (file.parent, Path("/tmp"), Path("/")):
                info = parent.lstat()
                _require(stat.S_ISDIR(info.st_mode) and info.st_uid == 0
                         and (not info.st_mode & 0o022 or info.st_mode & stat.S_ISVTX)
                         and not any(n.startswith("system.posix_acl_")
                                     for n in os.listxattr(parent, follow_symlinks=False)))
        fd = os.open(file, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC)
        try:
            before = os.fstat(fd)
            _require(stat.S_ISREG(before.st_mode) and before.st_nlink == 1
                     and 0 < before.st_size <= MAX_SOURCE)
            if child:
                _require(before.st_uid == 0 and stat.S_IMODE(before.st_mode) == 0o444
                         and not any(n.startswith("system.posix_acl_") for n in os.listxattr(fd)))
            raw = os.pread(fd, MAX_SOURCE + 1, 0)
            after, linked = os.fstat(fd), file.lstat()
            fields = ("st_dev", "st_ino", "st_mode", "st_uid", "st_gid", "st_nlink",
                      "st_size", "st_mtime_ns", "st_ctime_ns")
            _require(len(raw) == before.st_size
                     and all(getattr(before, n) == getattr(after, n) == getattr(linked, n) for n in fields))
            return raw
        finally:
            os.close(fd)  # No retry after an uncertain close.
    except OSError:
        raise StartupGateRefused("operator_startup_gate_refused") from None


def _load_legacy():
    here = Path(__file__)
    child = here.name == "fixture.py"
    _require(child or here.name == ENTRY)
    raw = _read_file(here.with_name(CHILD_LEGACY if child else LEGACY), child)
    source = raw if child else raw.replace(b"\r\n", b"\n")
    _require(hashlib.sha256(source).hexdigest() == LEGACY_SHA)
    module = ModuleType("_pult_frozen_operator_startup_proof")
    # Old REPO/path-mode logic and fixture copying intentionally see the gate.
    module.__file__ = str(here)
    exec(compile(source, str(here.with_name(CHILD_LEGACY if child else LEGACY)), "exec",
                 dont_inherit=True, optimize=0), module.__dict__)
    return module, source, child


def _fixture_put(original, wrapper, legacy):
    copied = set()
    def put(file, raw, mode=0o444):
        if file.name != "fixture.py":
            return original(file, raw, mode)
        _require(file.is_absolute() and file.parent.parent == Path("/tmp")
                 and file.parent.name.startswith("pult-offline-") and not file.parent.is_symlink()
                 and type(raw) is bytes and raw == wrapper and mode == 0o444 and file not in copied)
        # Both files exist before old native_case can request controller startup.
        original(file.with_name(CHILD_LEGACY), legacy, 0o444)
        result = original(file, raw, mode)
        copied.add(file)
        return result
    return put


class _StartupBarrier:
    def __init__(self, legacy, root):
        self.original_put, self.original_json = legacy.put, legacy.root_json
        self.canonical = legacy.canonical
        self.ready, self.descendant = root / "ready.json", root / "descendant.json"
        self.temporary = root / ".operator-startup-ready.pending"
        self.pending = None
        self.seen_ready = self.seen_descendant = self.released = False

    def _publish(self):
        _require(self.pending is not None and not os.path.lexists(self.ready))
        fd = os.open(self.temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC, 0o600)
        try:
            os.fchmod(fd, 0o600)
            before = os.fstat(fd)
            _require(stat.S_ISREG(before.st_mode) and before.st_uid == 0 and before.st_nlink == 1
                     and stat.S_IMODE(before.st_mode) == 0o600 and before.st_size == 0)
            offset = 0
            while offset < len(self.pending):
                count = os.write(fd, self.pending[offset:])
                _require(type(count) is int and 0 < count <= len(self.pending) - offset)
                offset += count
            os.fsync(fd)
            after = os.fstat(fd)
            keys = ("st_dev", "st_ino", "st_uid", "st_gid", "st_mode", "st_nlink")
            _require(all(getattr(before, k) == getattr(after, k) for k in keys)
                     and after.st_size == len(self.pending))
        finally:
            os.close(fd)  # A failed close never authorizes rename or a retry.
        linked = self.temporary.lstat()
        _require(all(getattr(after, k) == getattr(linked, k) for k in (*keys, "st_size"))
                 and not os.path.lexists(self.ready))
        os.replace(self.temporary, self.ready)
        directory = os.open(self.ready.parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC)
        try:
            info, current = os.fstat(directory), self.ready.parent.lstat()
            _require(stat.S_ISDIR(info.st_mode) and info.st_uid == 0
                     and info.st_dev == current.st_dev and info.st_ino == current.st_ino)
            os.fsync(directory)
        finally:
            os.close(directory)
        # Rename consumed staging; no directory-entry cleanup follows READY.
        self.pending, self.released = None, True

    def put(self, file, raw, mode=0o444):
        if file == self.ready:
            _require(not self.seen_ready and self.pending is None and mode == 0o600
                     and type(raw) is bytes and 0 < len(raw) < 16384)
            value = json.loads(raw)
            _require(type(value) is dict and self.canonical(value) == raw)
            self.pending, self.seen_ready = raw, True
            return None
        if file != self.descendant:
            return self.original_put(file, raw, mode)
        _require(self.pending is not None and not self.seen_descendant and not self.released)
        result = self.original_put(file, raw, mode)
        self.seen_descendant = True
        self._publish()
        return result

    def root_json(self, file):
        if file == self.ready and not self.released:
            _require(self.pending is not None)
            return json.loads(self.pending)  # Fresh value for old InvocationID check.
        return self.original_json(file)

    def finish(self):
        _require(self.seen_ready and self.seen_descendant and self.released and self.pending is None)


def _controller(legacy, original):
    def controller(root, mode, bootstrap_sha):
        if mode != "term_descendant":
            return original(root, mode, bootstrap_sha)
        barrier = _StartupBarrier(legacy, root)
        legacy.put, legacy.root_json = barrier.put, barrier.root_json
        try:
            result = original(root, mode, bootstrap_sha)
            barrier.finish()
            return result
        finally:
            legacy.put, legacy.root_json = barrier.original_put, barrier.original_json
    return controller


def main():
    legacy, source, child = _load_legacy()
    put, controller = legacy.put, legacy.controller
    if not child:
        wrapper = _read_file(Path(__file__)).replace(b"\r\n", b"\n")
        legacy.put = _fixture_put(put, wrapper, source)
    legacy.controller = _controller(legacy, controller)
    try:
        return legacy.main()  # Frozen main owns all existing arguments/eleven cases.
    finally:
        legacy.put, legacy.controller = put, controller


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception:
        print(json.dumps(dict(outcome="FAIL", reason="operator_startup_gate_refused")))
        raise SystemExit(1)
