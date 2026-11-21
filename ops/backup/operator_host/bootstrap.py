"""Fixed OS-trusted loader for one approved, captured offline source closure.

No repository import, path discovery, credentials, or privilege transition is
provided. The root controller uses load_profile(); the CLI is actor-only.
"""
import hashlib
import json
import os
import re
import stat
import sys
import threading
from types import ModuleType

PROFILE_MODULE = '_pult_operator_profile_v1'
FORMAT = 'pult-offline-operator-host-v1'
MODE = 'SYNTHETIC_REVIEW_ENDPOINT_ONLY'
FIELDS = {'format', 'mode', 'operation', 'release', 'controller', 'control',
          'actor', 'operator_uid', 'ipc', 'synthetic', 'sources'}
PATHS = {
    'journal': 'ops/backup/erasure_journal.py', 'anchor': 'ops/backup/erasure_anchor.py',
    'ipc': 'ops/backup/erasure_custodian.py', 'operator': 'ops/backup/erasure_operator.py',
    'stop': 'ops/backup/custodian_v2/stop/constructor_stop.py',
    'supervisor': 'ops/backup/custodian_v2/controller/supervisor_v2.py',
    'core': 'ops/backup/custodian_v2/controller/frozen/admission.py',
    **{name: 'ops/backup/operator_host/'+name+'.py'
       for name in ('profile', 'host', 'bootstrap', 'worker')},
}
PINS = dict(journal='94eeba19e645e25ec8286baebe4092a29c541c6f12858e1ff7b303f6a1a4fc98',
    anchor='b399803ac7a03aaa5a506cbb0036f98c153457769beabf46af44864b190861a8',
    ipc='3eb793a1ef1002c6def97b52c579b2ff051e834b44b802a75aca09d640a691d3',
    operator='c4be8ed53a803afe37cf6c72ac9fa462167ace9401575ee1f85faa2774a1365f',
    stop='5406c5a81498013884ac46d0279219f299db40576357692604fb4f08c251ca8c',
    supervisor='3d05d22163b11f815b29c222128c4e41987c9e4a83e7b4906cbe63a18941a37e',
    core='961cc3aef5dcfe9d6286b15a2d5c087a33dc1e5dafdc7d5a16f6eded961afea7')


class Refused(Exception):
    """Only the fixed bootstrap diagnostic is exposed by load_profile()."""


def _runtime():
    if (sys.platform != 'linux' or sys.implementation.name != 'cpython'
            or sys.version_info[:2] != (3, 12) or not sys.flags.isolated
            or not sys.flags.no_site or not sys.flags.no_user_site
            or not sys.flags.ignore_environment or not sys.flags.safe_path
            or not sys.flags.dont_write_bytecode or not sys.dont_write_bytecode
            or sys.flags.optimize or os.path.realpath(sys.executable) != '/usr/bin/python3.12'
            or threading.current_thread() is not threading.main_thread()
            or len(os.listdir('/proc/self/task')) != 1):
        raise Refused('bootstrap_refused')


def _path(value):
    if (type(value) is not str or not 1 < len(value) <= 4096
            or not re.fullmatch('/[A-Za-z0-9_./-]+', value)
            or any(part in ('', '.', '..') for part in value.split('/')[1:])):
        raise Refused('bootstrap_refused')
    return value


def _digest(raw):
    return hashlib.sha256(raw).hexdigest()


def _canonical(value):
    return (json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=True,
                       allow_nan=False)+'\n').encode('ascii')


def _pairs(rows):
    result = {}
    for key, value in rows:
        if key in result:
            raise Refused('bootstrap_refused')
        result[key] = value
    return result


def _stamp(info):
    # atime may change merely by reading; every security/content field is pinned.
    return tuple(getattr(info, key) for key in (
        'st_dev', 'st_ino', 'st_mode', 'st_uid', 'st_gid', 'st_nlink', 'st_size',
        'st_mtime_ns', 'st_ctime_ns'))


def _no_acl(fd):
    if any(name.startswith('system.posix_acl_') for name in os.listxattr(fd)):
        raise Refused('bootstrap_refused')


class _Capture:
    """Keep descriptor-anchored paths and metadata through the execution barrier."""
    def __init__(self):
        self.entries = []
        self.directories = {}

    def __enter__(self):
        return self

    def __exit__(self, *unused):
        failed = False
        for fd, _, _, _ in reversed(self.entries):
            try:
                os.close(fd)
            except OSError:
                failed = True
        if failed:
            raise Refused('bootstrap_refused')

    def _open_directory(self, parent, name):
        fd = os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC,
                     dir_fd=parent)
        info = os.fstat(fd)
        self.entries.append((fd, parent, name, info))
        sticky_root = info.st_uid == 0 and bool(info.st_mode & stat.S_ISVTX)
        if (not stat.S_ISDIR(info.st_mode) or info.st_uid != 0
                or info.st_mode & 0o022 and not sticky_root):
            raise Refused('bootstrap_refused')
        _no_acl(fd)
        return fd

    def _directory(self, parts):
        if () not in self.directories:
            self.directories[()] = self._open_directory(None, '/')
        prefix = ()
        for part in parts:
            child = prefix+(part,)
            if child not in self.directories:
                self.directories[child] = self._open_directory(self.directories[prefix], part)
            prefix = child
        return self.directories[prefix]

    def take(self, file, maximum):
        parts = _path(file).split('/')[1:]
        parent = self._directory(parts[:-1])
        fd = os.open(parts[-1], os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC,
                     dir_fd=parent)
        before = os.fstat(fd)
        self.entries.append((fd, parent, parts[-1], before))
        if (not stat.S_ISREG(before.st_mode) or before.st_uid != 0 or before.st_nlink != 1
                or stat.S_IMODE(before.st_mode) not in (0o444, 0o644)
                or not 0 <= before.st_size <= maximum):
            raise Refused('bootstrap_refused')
        _no_acl(fd)
        raw = os.pread(fd, maximum+1, 0)
        if len(raw) != before.st_size or _stamp(os.fstat(fd)) != _stamp(before):
            raise Refused('bootstrap_refused')
        return raw

    def check(self):
        for fd, parent, name, before in self.entries:
            _no_acl(fd)
            linked = os.stat(name, dir_fd=parent, follow_symlinks=False)
            if _stamp(os.fstat(fd)) != _stamp(before) or _stamp(linked) != _stamp(before):
                raise Refused('bootstrap_refused')


def _manifest(raw):
    value = json.loads(raw, object_pairs_hook=_pairs)
    if (type(value) is not dict or _canonical(value) != raw or set(value) != FIELDS
            or value['format'] != FORMAT or value['mode'] != MODE
            or type(value['release']) is not dict
            or set(value['release']) != {'root', 'identity'}
            or type(value['sources']) is not dict or set(value['sources']) != set(PATHS)):
        raise Refused('bootstrap_refused')
    release = _path(value['release']['root'])
    for name, relative in PATHS.items():
        item = value['sources'][name]
        if (type(item) is not dict or set(item) != {'path', 'sha256'}
                or item['path'] != release+'/'+relative or type(item['sha256']) is not str
                or not re.fullmatch('[0-9a-f]{64}', item['sha256'])
                or name in PINS and item['sha256'] != PINS[name]):
            raise Refused('bootstrap_refused')
        _path(item['path'])
    return value


def _load(file, approved_sha):
    _runtime()
    file = _path(os.fspath(file))
    if type(approved_sha) is not str or not re.fullmatch('[0-9a-f]{64}', approved_sha):
        raise Refused('bootstrap_refused')
    if PROFILE_MODULE in sys.modules:
        raise Refused('bootstrap_refused')
    module = None
    try:
        with _Capture() as captured:
            raw = captured.take(file, 16384)
            if _digest(raw) != approved_sha:
                raise Refused('bootstrap_refused')
            spec = _manifest(raw)
            blobs = {}
            for name in PATHS:
                item = spec['sources'][name]
                body = captured.take(item['path'], 100000)
                if _digest(body) != item['sha256']:
                    raise Refused('bootstrap_refused')
                blobs[name] = body
            # Every source, even profile.py, is captured and approved BEFORE exec.
            captured.check()
            if PROFILE_MODULE in sys.modules:
                raise Refused('bootstrap_refused')
            module = ModuleType(PROFILE_MODULE)
            module.__file__ = spec['sources']['profile']['path']
            module.__package__ = ''
            sys.modules[PROFILE_MODULE] = module
            exec(compile(blobs['profile'], module.__file__, 'exec', dont_inherit=True,
                         optimize=0), module.__dict__)
            if (module.PATHS != PATHS or module.PINS != PINS or module.FIELDS != FIELDS
                    or module.FORMAT != FORMAT or module.MODE != MODE):
                raise Refused('bootstrap_refused')
            profile = module.Profile(file, approved_sha, blobs)
            if type(profile.raw) is not bytes or profile.raw != raw or profile.sha != approved_sha:
                raise Refused('bootstrap_refused')
            captured.check()
        return profile
    except BaseException:
        if module is not None and sys.modules.get(PROFILE_MODULE) is module:
            del sys.modules[PROFILE_MODULE]
        raise


def load_profile(file, approved_sha):
    """Root or actor API; all failures expose one fixed, secret-free exception."""
    try:
        return _load(file, approved_sha)
    except BaseException:
        pass
    # Raise outside the handler: no raw failure survives as exception context.
    raise Refused('offline_operator_bootstrap_refused') from None


def main():
    try:
        if len(sys.argv) != 3:
            raise Refused('bootstrap_refused')
        profile = load_profile(sys.argv[1], sys.argv[2])
        # No worker body executes while this process is root or otherwise unsafe.
        profile.actor_guard()
        profile.capture('worker').run(profile)
        return 0
    except BaseException:
        try:
            os.write(2, b'offline_operator_worker_refused\n')
        except BaseException:
            pass
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
