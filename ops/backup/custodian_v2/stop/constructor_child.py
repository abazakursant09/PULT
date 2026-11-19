"""Fixed isolated constructor helper; no listener, admission or live transport."""
import hashlib
import json
import os
from pathlib import Path
import re
import select
import stat
import subprocess
import sys
from types import ModuleType

CONSTRUCTOR_SHA = 'faf627fef860d560d01605e3717c9939fe81c2c4156b231b03e94c57320c2bae'


def canonical(value):
    return (json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=True,
                       allow_nan=False) + '\n').encode('ascii')


def root_bytes(value, maximum=100000):
    if type(value) is not str or not re.fullmatch('/[A-Za-z0-9_./-]+', value):
        raise ValueError('fixed root path required')
    file = Path(value)
    if file.anchor != '/' or str(file) != value or '..' in file.parts:
        raise ValueError('normalized root path required')
    for parent in file.parents:
        info = parent.lstat()
        sticky = info.st_uid == 0 and bool(info.st_mode & stat.S_ISVTX)
        if (not stat.S_ISDIR(info.st_mode) or info.st_uid != 0
                or info.st_mode & 0o022 and not sticky
                or any(n.startswith('system.posix_acl_') for n in os.listxattr(parent, follow_symlinks=False))):
            raise ValueError('protected root ancestors required')
    fd = os.open(file, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC)
    try:
        before = os.fstat(fd)
        if (not stat.S_ISREG(before.st_mode) or before.st_uid != 0 or before.st_nlink != 1
                or stat.S_IMODE(before.st_mode) not in (0o444, 0o644) or not 0 < before.st_size <= maximum
                or any(n.startswith('system.posix_acl_') for n in os.listxattr(fd))):
            raise ValueError('bounded root source required')
        raw = os.pread(fd, maximum + 1, 0)
        after = os.fstat(fd)
        keys = ('st_dev', 'st_ino', 'st_size', 'st_mtime_ns', 'st_ctime_ns')
        if (len(raw) != before.st_size or any(getattr(before, k) != getattr(after, k) for k in keys)
                or (file.lstat().st_dev, file.lstat().st_ino) != (before.st_dev, before.st_ino)):
            raise ValueError('source changed')
        return raw
    finally:
        os.close(fd)


def controller_guard(spec):
    """No constructor access under a replacement controller or an unbound helper."""
    controller = spec['controller']
    if Path('/proc/sys/kernel/random/boot_id').read_text().strip() != controller['boot']:
        raise ValueError('controller boot changed')
    def show(unit, fields):
        if not re.fullmatch('pult-[a-z0-9-]{1,100}\\.service', unit):
            raise ValueError('fixed unit required')
        result = subprocess.run(['/usr/bin/systemctl', 'show', unit, '--no-pager', '--property=' + fields],
            stdin=subprocess.DEVNULL, capture_output=True, timeout=2, check=True,
            env={'PATH': '/usr/bin:/bin', 'LANG': 'C.UTF-8'})
        return dict(row.split('=', 1) for row in result.stdout.decode().splitlines() if '=' in row)
    parent = show(controller['unit'], 'InvocationID,MainPID,ActiveState,SubState')
    if parent != dict(InvocationID=controller['invocation_id'], MainPID=str(controller['pid']),
                      ActiveState='active', SubState='running'):
        raise ValueError('original controller invocation unavailable')
    proc = Path('/proc/' + str(controller['pid']))
    start = int((proc / 'stat').read_text().rsplit(') ', 1)[1].split()[19])
    if start != controller['start_ticks']:
        raise ValueError('controller process changed')
    cgroup = Path('/proc/self/cgroup').read_text().strip()
    if not re.fullmatch('0::/system.slice/pult-call-gate-[0-9a-f]{32}\\.service', cgroup):
        raise ValueError('stop-bound helper cgroup required')
    own = show(cgroup.split('/')[-1], 'BindsTo,After,PartOf,User,Group,RuntimeMaxUSec,TimeoutStopUSec,Restart,ExitType,ReadWritePaths,InaccessiblePaths')
    if (any(controller['unit'] not in own.get(k, '').split() for k in ('BindsTo', 'After', 'PartOf'))
            or own.get('User') != str(spec['actor']['uid']) or own.get('Group') != str(spec['actor']['gid'])
            or own.get('RuntimeMaxUSec') != '40s' or own.get('TimeoutStopUSec') != '2s'
            or own.get('Restart') != 'no' or own.get('ExitType') != 'cgroup'
            or own.get('ReadWritePaths') != spec['state_directory']['path']
            or set(own.get('InaccessiblePaths', '').split()) != {'/dev/shm', '/dev/mqueue', '/dev/pts'}):
        raise ValueError('helper kernel stop profile mismatch')


def main():
    if (sys.platform != 'linux' or sys.implementation.name != 'cpython'
            or sys.version_info[:2] != (3, 12) or not sys.flags.isolated or not sys.flags.no_site
            or not sys.dont_write_bytecode or sys.flags.optimize or os.getuid() < 1000
            or os.getuid() != os.geteuid() or Path(sys.executable).resolve() != Path('/usr/bin/python3.12')
            or len(sys.argv) != 3 or not re.fullmatch('[0-9a-f]{64}', sys.argv[2])):
        return 2
    raw = root_bytes(sys.argv[1], 16384)
    if hashlib.sha256(raw).hexdigest() != sys.argv[2]:
        return 2
    def pairs(rows):
        value = {}
        for name, item in rows:
            if name in value:
                raise ValueError('duplicate field')
            value[name] = item
        return value
    spec = json.loads(raw, object_pairs_hook=pairs)
    if (canonical(spec) != raw or spec['format'] != 'pult-construction-stop-profile-v1'
            or spec['mode'] != 'STOP_BOUND_CONSTRUCTION_ONLY'
            or spec['helper_entry'] != str(Path(__file__))
            or hashlib.sha256(root_bytes(spec['helper_entry'])).hexdigest() != spec['helper_sha256']
            or os.getuid() != spec['actor']['uid'] or os.getgid() != spec['actor']['gid']):
        return 2
    controller_guard(spec)
    source = root_bytes(spec['constructor_entry'])
    if hashlib.sha256(source).hexdigest() != CONSTRUCTOR_SHA:
        return 2
    module = ModuleType('_stop_bound_existing_constructor')
    module.__file__ = spec['constructor_entry']
    exec(compile(source, module.__file__, 'exec', dont_inherit=True, optimize=0), module.__dict__)
    phase = 'construct'
    try:
        ready = module.prepare(spec['host_profile'], spec['host_profile_sha256'])
        controller_guard(spec)
        phase = 'check'
        observation = ready.check()
        controller_guard(spec)
        answer = dict(format='pult-construction-stop-ready-v1', status='CONSTRUCTED_HELD_NO_ADMISSION',
            profile_sha256=sys.argv[2], host_profile_sha256=spec['host_profile_sha256'],
            observation_sha256=hashlib.sha256(canonical(observation)).hexdigest(),
            write_admitted=False, listener_admitted=False, purge_or_restore_admitted=False)
        os.write(1, canonical(answer))
        phase = 'close_command'
        if not select.select([0], [], [], 2)[0] or os.read(0, 33) != b'CLOSE_CONSTRUCTION_V1\n':
            raise ValueError('fixed bounded close command required')
        controller_guard(spec)
        phase = 'close'
        ready.close()
        controller_guard(spec)
    except BaseException as error:
        # Fixed stage and exception TYPE only; no message, path, traceback or key.
        names = sorted({type(item._failure).__name__ for item in module._FAILED})
        os.write(2, ('CONSTRUCTOR_REFUSED ' + phase + ' ' + type(error).__name__ + ' ' + ','.join(names) + '\n').encode('ascii'))
        raise
    answer = dict(format='pult-construction-stop-reply-v1', status='CONSTRUCTED_CLOSED_NO_ADMISSION',
        profile_sha256=sys.argv[2], host_profile_sha256=spec['host_profile_sha256'],
        observation_sha256=hashlib.sha256(canonical(observation)).hexdigest(),
        write_admitted=False, listener_admitted=False, purge_or_restore_admitted=False)
    os.write(1, canonical(answer))
    return 0


if __name__ == '__main__':
    try:
        result = main()
    except BaseException:
        # No private traceback, credential or SQL content crosses the pipe.
        result = 1
    os._exit(result)
