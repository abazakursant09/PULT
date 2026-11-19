"""Trusted root construction-to-stop binding; no listener or write admission.

Root/OS/systemd/fixed interpreter and independently approved profile are trust
roots. This one-shot primitive never adopts a prior operation or deletes fences.
"""
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import selectors
import signal
import stat
import subprocess
import sys
import threading
import time
from types import ModuleType
from uuid import UUID, uuid4

SUPERVISOR_SHA = '3d05d22163b11f815b29c222128c4e41987c9e4a83e7b4906cbe63a18941a37e'
CORE_SHA = '961cc3aef5dcfe9d6286b15a2d5c087a33dc1e5dafdc7d5a16f6eded961afea7'
CONSTRUCTOR_SHA = 'faf627fef860d560d01605e3717c9939fe81c2c4156b231b03e94c57320c2bae'
HELPER_SHA = '5e54b650cfb44e4614eec0aafbcbc0c8faf7de985fd3344951b77c04c222322a'
FORMAT = 'pult-construction-stop-profile-v1'
MODE = 'STOP_BOUND_CONSTRUCTION_ONLY'
FIELDS = {'format', 'mode', 'stop_entry', 'stop_sha256', 'helper_entry', 'helper_sha256',
    'constructor_entry', 'host_profile', 'host_profile_sha256', 'supervisor_entry', 'legacy_core',
    'controller', 'control', 'actor', 'state_directory'}
LOCK = 'constructor-stop.lock'
FENCE = 'constructor-stop.claim'
PREFIX = 'pult-call-gate-'
RUNTIME, STOP = 40, 2
_ATTEMPTED = False
_FAILED = set()


class Refused(Exception):
    pass


class Unknown(Exception):
    pass


def canonical(value):
    return (json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=True,
                       allow_nan=False) + '\n').encode('ascii')


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


def runtime():
    if (sys.platform != 'linux' or sys.implementation.name != 'cpython'
            or sys.version_info[:2] != (3, 12) or not sys.flags.isolated or not sys.flags.no_site
            or not sys.dont_write_bytecode or sys.flags.optimize or os.getuid() != 0 or os.geteuid() != 0
            or Path(sys.executable).resolve() != Path('/usr/bin/python3.12')):
        raise Refused('fixed isolated nonoptimized root runtime required')
    if threading.current_thread() is not threading.main_thread() or len(os.listdir('/proc/self/task')) != 1:
        raise Refused('single original controller thread required')


def path(value):
    if type(value) is not str or not re.fullmatch('/[A-Za-z0-9_./-]+', value):
        raise Refused('safe absolute path required')
    file = Path(value)
    if file.anchor != '/' or str(file) != value or '..' in file.parts:
        raise Refused('normalized absolute path required')
    return file


def ancestors(file, owners=(0,)):
    for parent in file.parents:
        info = parent.lstat()
        sticky = info.st_uid == 0 and bool(info.st_mode & stat.S_ISVTX)
        if (not stat.S_ISDIR(info.st_mode) or info.st_uid not in owners
                or info.st_mode & 0o022 and not sticky
                or any(n.startswith('system.posix_acl_') for n in os.listxattr(parent, follow_symlinks=False))):
            raise Refused('protected ancestors required')


def identity(info):
    return {'device': info.st_dev, 'inode': info.st_ino}


def root_bytes(file, maximum=100000, modes=(0o444, 0o644)):
    file = path(str(file))
    ancestors(file)
    fd = os.open(file, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC)
    try:
        before = os.fstat(fd)
        if (not stat.S_ISREG(before.st_mode) or before.st_uid != 0 or before.st_nlink != 1
                or stat.S_IMODE(before.st_mode) not in modes or not 0 <= before.st_size <= maximum
                or any(n.startswith('system.posix_acl_') for n in os.listxattr(fd))):
            raise Refused('bounded root descriptor required')
        raw = os.pread(fd, maximum + 1, 0)
        after = os.fstat(fd)
        keys = ('st_dev', 'st_ino', 'st_size', 'st_mtime_ns', 'st_ctime_ns')
        if (len(raw) != before.st_size or any(getattr(before, k) != getattr(after, k) for k in keys)
                or identity(file.lstat()) != identity(before)):
            raise Refused('root input changed')
        return raw
    finally:
        os.close(fd)


def document(raw):
    def pairs(rows):
        value = {}
        for name, item in rows:
            if name in value:
                raise Refused('duplicate field')
            value[name] = item
        return value
    value = json.loads(raw, object_pairs_hook=pairs)
    if type(value) is not dict or canonical(value) != raw:
        raise Refused('canonical exact document required')
    return value


def check_inode(file, wanted, uid, gid, mode, directory=False):
    file = path(str(file))
    ancestors(file, (0, uid))
    if (type(wanted) is not dict or set(wanted) != {'device', 'inode'}
            or any(type(v) is not int or v < 0 for v in wanted.values()) or not wanted['inode']):
        raise Refused('explicit inode identity required')
    info = file.lstat()
    kind = stat.S_ISDIR if directory else stat.S_ISREG
    if (not kind(info.st_mode) or info.st_uid != uid or info.st_gid != gid
            or stat.S_IMODE(info.st_mode) != mode or identity(info) != wanted
            or not directory and info.st_nlink != 1
            or any(n.startswith('system.posix_acl_') for n in os.listxattr(file, follow_symlinks=False))):
        raise Refused('pinned private inode changed')


def captured(file, expected, name):
    raw = root_bytes(file)
    if digest(raw) != expected:
        raise Refused('fixed source required')
    module = ModuleType(name)
    module.__file__ = str(file)
    exec(compile(raw, str(file), 'exec', dont_inherit=True, optimize=0), module.__dict__)
    return module


def sync_directory(file):
    fd = os.open(file, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def manager_view(control, wanted, fence_fd=None):
    """The system manager's condition must refer to our actual durable inode."""
    fd = os.open('/proc/1/root', os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC)
    try:
        for component in path(str(control)).parts[1:]:
            child = os.open(component, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC, dir_fd=fd)
            os.close(fd)
            fd = child
        if identity(os.fstat(fd)) != wanted:
            raise Refused('system manager control view differs')
        if fence_fd is not None:
            other = os.open(FENCE, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC, dir_fd=fd)
            try:
                if identity(os.fstat(other)) != identity(os.fstat(fence_fd)):
                    raise Unknown('system manager durable fence view differs')
            finally:
                os.close(other)
    finally:
        os.close(fd)


def valid_reply(raw, profile_sha, host_sha):
    try:
        if (type(raw) is not bytes or not 0 < len(raw) <= 2048
                or any(type(v) is not str or not re.fullmatch('[0-9a-f]{64}', v) for v in (profile_sha, host_sha))):
            return False
        value = document(raw)
        if set(value) != {'format', 'status', 'profile_sha256', 'host_profile_sha256',
                         'observation_sha256', 'write_admitted', 'listener_admitted', 'purge_or_restore_admitted'}:
            return False
        return (value['format'] == 'pult-construction-stop-reply-v1'
            and value['status'] == 'CONSTRUCTED_CLOSED_NO_ADMISSION'
            and value['profile_sha256'] == profile_sha and value['host_profile_sha256'] == host_sha
            and type(value['observation_sha256']) is str and re.fullmatch('[0-9a-f]{64}', value['observation_sha256']) is not None
            and all(value[k] is False for k in ('write_admitted', 'listener_admitted', 'purge_or_restore_admitted')))
    except (Refused, ValueError, TypeError, KeyError, UnicodeError, RecursionError):
        return False


def valid_ready(raw, profile_sha, host_sha):
    try:
        if type(raw) is not bytes or not 0 < len(raw) <= 2048:
            return False
        value = document(raw)
        if value['format'] != 'pult-construction-stop-ready-v1' or value['status'] != 'CONSTRUCTED_HELD_NO_ADMISSION':
            return False
        value['format'] = 'pult-construction-stop-reply-v1'
        value['status'] = 'CONSTRUCTED_CLOSED_NO_ADMISSION'
        return valid_reply(canonical(value), profile_sha, host_sha)
    except (Refused, ValueError, TypeError, KeyError, UnicodeError, RecursionError):
        return False


class Bound:
    """One root-owned construction attempt. No request-controlled launch API."""
    def __init__(self, file, raw, spec, supervisor):
        self.file, self.raw, self.spec, self.supervisor = file, raw, spec, supervisor
        self.profile_sha = digest(raw)
        self.pid, self.thread = os.getpid(), threading.current_thread()
        self.lock = self.fence = self.process = self.pidfd = None
        self.unit = self.fence_raw = self.fence_identity = None
        self._failure = None
        self.failed = self.closed = self.submitted = False

    def __repr__(self):
        return '<StopBoundConstruction one attempt; no admission>'

    def _state(self, unit):
        return self.supervisor._core()._state(unit)

    def _extra(self, unit, fields):
        raw = self.supervisor._cmd(('/usr/bin/systemctl', 'show', unit, '--no-pager', '--property=' + fields)).stdout
        return dict(row.split('=', 1) for row in raw.decode().splitlines() if '=' in row)

    def _controller(self):
        value = self.spec['controller']
        unit = value['unit']
        if type(unit) is not str or not re.fullmatch('pult-[a-z0-9-]{1,100}\\.service', unit):
            raise Refused('dedicated controller unit required')
        if (type(value['invocation_id']) is not str or not re.fullmatch('[0-9a-f]{32}', value['invocation_id'])
                or type(value['pid']) is not int or value['pid'] != self.pid
                or type(value['start_ticks']) is not int or value['start_ticks'] <= 0
                or int(Path('/proc/self/stat').read_text().rsplit(') ', 1)[1].split()[19]) != value['start_ticks']
                or str(UUID(value['boot'])) != value['boot']
                or Path('/proc/sys/kernel/random/boot_id').read_text().strip() != value['boot']):
            raise Refused('current controller invocation and boot required')
        state = self._state(unit)
        extra = self._extra(unit, 'InvocationID,Type,RemainAfterExit')
        expected = {'LoadState': 'loaded', 'ActiveState': 'active', 'SubState': 'running',
            'MainPID': str(self.pid), 'ControlGroup': '/system.slice/' + unit,
            'ExitType': 'main', 'KillMode': 'control-group', 'Restart': 'no', 'NRestarts': '0',
            'KillSignal': '15', 'FinalKillSignal': '9', 'SendSIGKILL': 'yes', 'TimeoutStopUSec': '2s'}
        if (any(state.get(k) != v for k, v in expected.items())
                or state.get('User') not in ('0', 'root') or state.get('Group') not in ('0', 'root')
                or extra != {'InvocationID': value['invocation_id'], 'Type': 'exec', 'RemainAfterExit': 'no'}
                or Path('/proc/self/cgroup').read_text().strip() != '0::/system.slice/' + unit):
            raise Refused('original main-PID controller lifecycle required')
        unit_path = ''.join(c if c.isascii() and c.isalnum() else '_' + format(ord(c), '02x') for c in unit)
        raw = self.supervisor._cmd(('/usr/bin/busctl', '--json=short', 'get-property',
            'org.freedesktop.systemd1', '/org/freedesktop/systemd1/unit/' + unit_path,
            'org.freedesktop.systemd1.Unit', 'Conditions')).stdout
        conditions = json.loads(raw)
        if (type(conditions) is not dict or set(conditions) != {'type', 'data'}
                or conditions['type'] != 'a(sbbsi)' or type(conditions['data']) is not list):
            raise Refused('typed kernel controller conditions required')
        fence = str(path(self.spec['control']['root']) / FENCE)
        required = ['ConditionPathExists', False, True, fence, 1]
        if (sum(row == required for row in conditions['data']) != 1
                or any(type(row) is not list or len(row) != 5 or type(row[0]) is not str
                    or type(row[1]) is not bool or type(row[2]) is not bool or type(row[3]) is not str
                    or type(row[4]) is not int for row in conditions['data'])):
            raise Refused('mandatory negative permanent-fence condition required')

    def _validate(self):
        runtime()
        if self.pid != os.getpid() or self.thread is not threading.current_thread() or self.closed or self.failed:
            raise Refused('original live controller required')
        if root_bytes(self.file, 16384) != self.raw or canonical(self.spec) != self.raw:
            raise Refused('profile changed')
        value = self.spec
        for name, expected in (('stop_entry', value['stop_sha256']), ('helper_entry', HELPER_SHA),
                ('constructor_entry', CONSTRUCTOR_SHA), ('supervisor_entry', SUPERVISOR_SHA),
                ('legacy_core', CORE_SHA), ('host_profile', value['host_profile_sha256'])):
            if digest(root_bytes(path(value[name]))) != expected:
                raise Refused('approved source closure changed')
        control = path(value['control']['root'])
        check_inode(control, value['control']['identity'], 0, 0, 0o700, True)
        manager_view(control, value['control']['identity'])
        check_inode(control / LOCK, value['control']['lock_identity'], 0, 0, 0o600)
        if root_bytes(control / LOCK, 1, (0o600,)) != b'':
            raise Refused('existing empty control lock required')
        if not {f.name for f in control.iterdir()}.issubset({LOCK, FENCE}):
            raise Refused('foreign control state')
        state = value['state_directory']
        check_inode(path(state['path']), state['identity'], value['actor']['uid'], value['actor']['gid'], 0o700, True)
        import pwd, grp
        try:
            actor = pwd.getpwuid(value['actor']['uid'])
            group = grp.getgrgid(value['actor']['gid'])
        except KeyError:
            raise Refused('existing static service principal required') from None
        if actor.pw_gid != group.gr_gid or set(os.getgrouplist(actor.pw_name, group.gr_gid)) != {group.gr_gid}:
            raise Refused('existing isolated service principal required')
        self._controller()

    def _fence_check(self):
        if self.fence is None:
            raise Unknown('durable fence missing')
        current = path(self.spec['control']['root']) / FENCE
        manager_view(current.parent, self.spec['control']['identity'], self.fence)
        info = os.fstat(self.fence)
        if (identity(info) != self.fence_identity or identity(current.lstat()) != self.fence_identity
                or os.pread(self.fence, 4097, 0) != self.fence_raw
                or root_bytes(current, 4096, (0o600,)) != self.fence_raw
                or document(self.fence_raw)['unit'] != self.unit):
            raise Unknown('durable fence changed')

    def _claim(self):
        control = path(self.spec['control']['root'])
        self.lock = os.open(control / LOCK, os.O_RDWR | os.O_NOFOLLOW | os.O_CLOEXEC)
        if identity(os.fstat(self.lock)) != self.spec['control']['lock_identity']:
            raise Refused('control lock descriptor changed')
        try:
            fcntl.flock(self.lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise Refused('controller already owned') from None
        self._validate()
        self.unit = PREFIX + uuid4().hex + '.service'
        self.fence_raw = canonical(dict(format='pult-construction-stop-fence-v1', profile_sha256=self.profile_sha,
            host_profile_sha256=self.spec['host_profile_sha256'], controller=self.spec['controller'], unit=self.unit))
        self.fence = os.open(control / FENCE, os.O_RDWR | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC, 0o600)
        os.fchmod(self.fence, 0o600)
        self.fence_identity = identity(os.fstat(self.fence))
        offset = 0
        while offset < len(self.fence_raw):
            count = os.write(self.fence, self.fence_raw[offset:])
            if count <= 0:
                raise Unknown('fence write incomplete')
            offset += count
        os.fsync(self.fence)
        sync_directory(control)
        self._fence_check()

    def command(self, argv):
        return argv  # Trusted test subclass may add the existing owned CI scope.

    def started(self, unit, state):
        pass  # Trusted native proof only; never selected by a caller/profile.

    def constructed(self, unit, ready):
        pass  # Trusted proof hook after the real ownership-bearing handle exists.

    def _cleanup(self):
        self._fence_check()
        self.supervisor._core().Manager.cleanup(self, self.unit)

    stop = STOP

    def _execute(self):
        actor, controller = self.spec['actor'], self.spec['controller']['unit']
        properties = {'Type': 'exec', 'ExitType': 'cgroup', 'RemainAfterExit': 'yes',
            'User': str(actor['uid']), 'Group': str(actor['gid']), 'SupplementaryGroups': '',
            'BindsTo': controller, 'After': controller, 'PartOf': controller,
            'RuntimeMaxSec': str(RUNTIME), 'TimeoutStartSec': '2s', 'TimeoutStopSec': str(STOP),
            'KillMode': 'control-group', 'KillSignal': 'SIGTERM', 'SendSIGKILL': 'yes',
            'FinalKillSignal': 'SIGKILL', 'Restart': 'no', 'NoNewPrivileges': 'yes',
            'CapabilityBoundingSet': '', 'AmbientCapabilities': '', 'PrivateNetwork': 'yes',
            'PrivateTmp': 'no', 'PrivateDevices': 'yes', 'ProtectSystem': 'strict', 'ProtectHome': 'yes',
            'InaccessiblePaths': '/dev/shm /dev/mqueue /dev/pts',
            'ProtectControlGroups': 'yes', 'RestrictAddressFamilies': 'AF_UNIX',
            'ReadWritePaths': self.spec['state_directory']['path'], 'TasksMax': '16',
            'MemoryMax': '128M', 'LimitFSIZE': '2097152', 'UMask': '0077',
            'Environment': 'PATH=/usr/bin:/bin LANG=C.UTF-8'}
        argv = ['/usr/bin/systemd-run', '--quiet', '--pipe', '--no-ask-password', '--expand-environment=no', '--unit=' + self.unit]
        argv += ['--property=' + k + '=' + v for k, v in sorted(properties.items())]
        argv += ['/usr/bin/python3.12', '-I', '-S', '-B', self.spec['helper_entry'], str(self.file), self.profile_sha]
        if self._state(self.unit).get('LoadState') != 'not-found':
            raise Refused('unit collision; never adopt')
        selector = selectors.DefaultSelector()
        buffers = {'stdout': bytearray(), 'stderr': bytearray()}
        final = None
        ready_frame = None
        close_sent = False
        began = time.monotonic()
        def pump():
            for key, _ in selector.select(0.025):
                try:
                    chunk = os.read(key.fileobj.fileno(), 4096)
                except BlockingIOError:
                    continue
                if not chunk:
                    selector.unregister(key.fileobj)
                    key.fileobj.close()
                    continue
                buffers[key.data].extend(chunk)
                if len(buffers[key.data]) > (2048 if key.data == 'stdout' else 1024):
                    raise Unknown('output bound exceeded')
        clean = False
        try:
            self._validate()
            self._fence_check()
            self.submitted = True
            self.process = subprocess.Popen(self.command(argv), stdin=subprocess.PIPE,
                stdout=subprocess.PIPE, stderr=subprocess.PIPE, close_fds=True,
                env={'PATH': '/usr/bin:/bin', 'LANG': 'C.UTF-8'})
            self.pidfd = os.pidfd_open(self.process.pid)
            for name in ('stdout', 'stderr'):
                stream = getattr(self.process, name)
                os.set_blocking(stream.fileno(), False)
                selector.register(stream, selectors.EVENT_READ, name)
            while True:
                self._validate()
                self._fence_check()
                pump()
                initial = self._state(self.unit)
                if initial.get('LoadState') == 'loaded' and initial.get('SubState') not in ('dead', 'start-pre', 'start', 'start-post'):
                    break
                if time.monotonic() - began > 3:
                    raise Unknown('start deadline')
            expected = {'RuntimeMaxUSec': str(RUNTIME) + 's', 'TimeoutStopUSec': str(STOP) + 's',
                'ExitType': 'cgroup', 'KillMode': 'control-group', 'KillSignal': '15', 'FinalKillSignal': '9',
                'SendSIGKILL': 'yes', 'Restart': 'no', 'NRestarts': '0', 'User': str(actor['uid']),
                'Group': str(actor['gid']), 'NoNewPrivileges': 'yes', 'ProtectControlGroups': 'yes',
                'PrivateNetwork': 'yes', 'CapabilityBoundingSet': ''}
            extra = self._extra(self.unit, 'BindsTo,After,PartOf,ReadWritePaths,ProtectSystem,PrivateTmp,InaccessiblePaths')
            if (any(initial.get(k) != v for k, v in expected.items())
                    or any(controller not in extra.get(k, '').split() for k in ('BindsTo', 'After', 'PartOf'))
                    or extra.get('ReadWritePaths') != self.spec['state_directory']['path']
                    or set(extra.get('InaccessiblePaths', '').split()) != {'/dev/shm', '/dev/mqueue', '/dev/pts'}
                    or extra.get('ProtectSystem') != 'strict' or extra.get('PrivateTmp') != 'no'):
                raise Unknown('kernel lifecycle profile mismatch')
            self.started(self.unit, initial)
            while True:
                self._validate()
                self._fence_check()
                pump()
                if ready_frame is None and b'\n' in buffers['stdout']:
                    first, rest = bytes(buffers['stdout']).split(b'\n', 1)
                    ready_frame = first + b'\n'
                    if not valid_ready(ready_frame, self.profile_sha, self.spec['host_profile_sha256']) or rest:
                        raise Unknown('exact held-constructor milestone required')
                    buffers['stdout'].clear()
                    self.constructed(self.unit, document(ready_frame))
                    self._validate()
                    self._fence_check()
                    if os.write(self.process.stdin.fileno(), b'CLOSE_CONSTRUCTION_V1\n') != 22:
                        raise Unknown('close command delivery uncertain')
                    self.process.stdin.close()
                    close_sent = True
                final = self._state(self.unit)
                if final.get('SubState') == 'exited' or final.get('ActiveState') in ('failed', 'inactive'):
                    break
                if time.monotonic() - began > RUNTIME + STOP + 5:
                    raise Unknown('runtime deadline')
        finally:
            interrupted = sys.exc_info()[1]
            try:
                if self.submitted:
                    self._cleanup()
                    clean = True
                if self.process is not None:
                    deadline = time.monotonic() + 2
                    while selector.get_map():
                        if time.monotonic() >= deadline:
                            raise Unknown('pipe EOF deadline')
                        pump()
                    self.process.wait(timeout=max(0.01, deadline - time.monotonic()))
            finally:
                cleanup_interrupted = sys.exc_info()[1]
                try:
                    selector.close()
                    if self.process is not None:
                        try:
                            if self.process.poll() is None:
                                if self.pidfd is None:
                                    self.process.kill()
                                else:
                                    signal.pidfd_send_signal(self.pidfd, signal.SIGKILL)
                            self.process.wait(timeout=2)
                        finally:
                            for name in ('stdin', 'stdout', 'stderr'):
                                stream = getattr(self.process, name)
                                if stream is not None and not stream.closed:
                                    stream.close()
                finally:
                    for error in (interrupted, cleanup_interrupted):
                        if error is not None and not isinstance(error, Exception):
                            raise error
        self._validate()
        self._fence_check()
        stdout = bytes(buffers['stdout'])
        if (ready_frame is None or not close_sent or not clean or not self.supervisor._empty(self.unit) or self.process.returncode != 0
                or buffers['stderr'] or final is None or final.get('Result') != 'success'
                or final.get('ExecMainCode') != '1' or final.get('ExecMainStatus') != '0'
                or final.get('SubState') != 'exited'
                or not valid_reply(stdout, self.profile_sha, self.spec['host_profile_sha256'])):
            raise Unknown('construction or cleanup not confirmed')
        if document(stdout)['observation_sha256'] != document(ready_frame)['observation_sha256']:
            raise Unknown('constructor observation changed during close')
        return stdout

    def run(self):
        global _ATTEMPTED
        if _ATTEMPTED:
            raise Refused('one construction operation per controller process')
        _ATTEMPTED = True
        try:
            self._validate()
            self._claim()
            answer = self._execute()
            self.close()
        except BaseException as error:
            self.failed = True
            self._failure = error
            _FAILED.add(self)
            if not isinstance(error, Exception):
                raise
            if self.submitted:
                raise Unknown('construction outcome uncertain; process exit required') from None
            raise Refused('construction launch refused') from None
        return answer

    def close(self):
        if self.closed:
            return
        if self.failed or self.pid != os.getpid() or self.thread is not threading.current_thread():
            raise Unknown('uncertain operation retained until process exit')
        try:
            self._fence_check()
            for name in ('pidfd', 'fence', 'lock'):
                fd = getattr(self, name)
                if fd is not None:
                    setattr(self, name, None)
                    os.close(fd)
        except BaseException as error:
            self.failed = True
            self._failure = error
            _FAILED.add(self)
            raise
        self.closed = True


def _prepare(manifest, approved_sha256):
    runtime()
    file = path(str(manifest))
    raw = root_bytes(file, 16384)
    value = document(raw)
    if (type(approved_sha256) is not str or not re.fullmatch('[0-9a-f]{64}', approved_sha256)
            or digest(raw) != approved_sha256 or set(value) != FIELDS or value['format'] != FORMAT or value['mode'] != MODE
            or value['helper_sha256'] != HELPER_SHA or value['stop_entry'] != str(Path(__file__))
            or digest(root_bytes(path(value['stop_entry']))) != value['stop_sha256']):
        raise Refused('distinct independently approved stop profile required')
    for key in ('stop_sha256', 'host_profile_sha256'):
        if type(value[key]) is not str or not re.fullmatch('[0-9a-f]{64}', value[key]):
            raise Refused('explicit source approval required')
    if (type(value['actor']) is not dict or set(value['actor']) != {'uid', 'gid'}
            or any(type(v) is not int or not 1000 <= v < 2**31 for v in value['actor'].values())
            or type(value['controller']) is not dict or set(value['controller']) != {'unit', 'invocation_id', 'boot', 'pid', 'start_ticks'}
            or type(value['control']) is not dict or set(value['control']) != {'root', 'identity', 'lock_identity'}
            or type(value['state_directory']) is not dict or set(value['state_directory']) != {'path', 'identity'}):
        raise Refused('explicit fixed principals and ownership required')
    control, state = path(value['control']['root']), path(value['state_directory']['path'])
    sources = [file] + [path(value[k]) for k in ('stop_entry', 'helper_entry', 'constructor_entry',
        'supervisor_entry', 'legacy_core', 'host_profile')]
    if (state == control or state.is_relative_to(control) or control.is_relative_to(state)
            or len(set(sources)) != 7 or any(p.is_relative_to(state) or p.is_relative_to(control) for p in sources)):
        raise Refused('separate code, approval, control and state required')
    host = document(root_bytes(path(value['host_profile']), 8192))
    if (digest(canonical(host)) != value['host_profile_sha256'] or host['format'] != 'pult-staged-host-construction-profile-v1'
            or host['mode'] != 'CONSTRUCT_ONLY_NO_ADMISSION' or host['custodian_uid'] != value['actor']['uid']
            or host['socket_gid'] != value['actor']['gid'] or path(host['state_file']).parent != state):
        raise Refused('exact existing constructor profile required')
    for key in ('reader_credential_file',):
        if path(host[key]).is_relative_to(state) or path(host[key]).is_relative_to(control):
            raise Refused('private reader outside writable state/control required')
    # Verify the entire fixed executable closure before executing either core.
    for name, expected in (('helper_entry', HELPER_SHA), ('constructor_entry', CONSTRUCTOR_SHA),
                          ('supervisor_entry', SUPERVISOR_SHA), ('legacy_core', CORE_SHA)):
        if digest(root_bytes(path(value[name]))) != expected:
            raise Refused('fixed source closure refused')
    supervisor = captured(path(value['supervisor_entry']), SUPERVISOR_SHA, '_stop_pinned_supervisor')
    supervisor.load_core(path(value['legacy_core']))
    result = Bound(file, raw, value, supervisor)
    result._validate()
    return result


def prepare(manifest, approved_sha256):
    try:
        return _prepare(manifest, approved_sha256)
    except Refused:
        raise
    except Exception:
        raise Refused('trusted construction stop profile refused') from None
