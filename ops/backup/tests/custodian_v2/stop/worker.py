"""Synthetic root controller only; independent parent supplies profile approval."""
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import sys
import subprocess
import fcntl
import signal

def load(file, name):
    spec = importlib.util.spec_from_file_location(name, file)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module

profile = Path(sys.argv[1])
mode = sys.argv[2] if len(sys.argv) == 3 else 'happy'
startup = profile.parent / 'controller-starts'
fd = os.open(startup, os.O_WRONLY | os.O_CREAT | os.O_APPEND | os.O_NOFOLLOW | os.O_CLOEXEC, 0o600)
try:
    os.write(fd, b'START\n')
    os.fsync(fd)
finally:
    os.close(fd)
print('CONTROLLER_READY ' + str(os.getpid()), flush=True)
approval = sys.stdin.readline().strip()
assert len(approval) == 64
raw = profile.read_bytes()
assert hashlib.sha256(raw).hexdigest() == approval
spec = json.loads(raw)
source = Path(spec['stop_entry'])
assert hashlib.sha256(source.read_bytes()).hexdigest() == spec['stop_sha256']
module = load(source, 'synthetic_stop_bound_entry')
control = load(source.parent / 'scope_control.py', 'synthetic_owned_scope')
unit_path = ''.join(c if c.isascii() and c.isalnum() else '_' + format(ord(c), '02x')
                    for c in spec['controller']['unit'])
conditions = subprocess.run(['/usr/bin/busctl', '--json=short', 'get-property', 'org.freedesktop.systemd1',
    '/org/freedesktop/systemd1/unit/' + unit_path, 'org.freedesktop.systemd1.Unit', 'Conditions'],
    capture_output=True, text=True, timeout=5, check=False)
result = None
try:
    ready = module.prepare(str(profile), approval)
    class Recorded(type(ready)):
        def command(self, argv):
            if mode in ('tail', 'filesystem', 'late_launch'):
                cut = argv.index('/usr/bin/python3.12')
                argv = argv[:cut] + ['/usr/bin/python3.12', '-I', '-S', '-B',
                    str(source.parent / 'actor_fault.py'),
                    ('late_probe' if mode == 'late_launch' else mode), *argv[cut + 4:]]
            argv = control.bound_command(argv)
            if mode == 'late_launch':
                target = profile.parent / 'deferred-command.json'
                with target.open('xb') as file:
                    file.write(module.canonical(argv)); file.flush(); os.fsync(file.fileno())
                os.kill(os.getpid(), signal.SIGKILL)
            return argv
        def constructed(self, unit, frame):
            lock = os.open(Path(spec['state_directory']['path']) / 'owner.lock', os.O_RDWR)
            try:
                try:
                    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                except BlockingIOError:
                    held = True
                else:
                    held = False
            finally:
                os.close(lock)
            assert held, 'REAL_CONSTRUCTOR_OWNERSHIP_NOT_HELD'
            group = Path('/sys/fs/cgroup/system.slice') / unit
            pids = sorted(int(p) for file in group.rglob('cgroup.procs') for p in file.read_text().split())
            with (profile.parent / 'constructed-proof.json').open('xb') as file:
                file.write(module.canonical(dict(owner_lock_held=held, frame=frame, pids=pids)))
                file.flush(); os.fsync(file.fileno())
            if mode == 'kill_controller':
                os.kill(os.getpid(), signal.SIGKILL)
            if mode == 'freeze':
                os.kill(int(self._state(unit)['MainPID']), signal.SIGSTOP)
            if mode == 'cleanup_unknown':
                self.fence_raw += b' '  # Trusted negative injection, never a source/profile mode.
    ready.__class__ = Recorded
    answer = ready.run()
    repeat = False
    try:
        module.prepare(str(profile), approval).run()
    except module.Refused:
        repeat = True
    result = dict(outcome='PASS', unit=ready.unit, reply=json.loads(answer), same_process_repeat_refused=repeat,
                  fence_preserved=(Path(spec['control']['root']) / module.FENCE).is_file(),
                  synthetic_controller_conditions=conditions.stdout, conditions_exit=conditions.returncode)
except BaseException as error:
    details = {}
    original = getattr(locals().get('ready'), '_failure', None) or error
    tb = original.__traceback__
    while tb is not None:
        if tb.tb_frame.f_code.co_name == '_execute':
            values = tb.tb_frame.f_locals
            details = dict(final=values.get('final'), clean=values.get('clean'),
                output={k: bytes(v).decode('utf-8', 'replace') for k, v in values.get('buffers', {}).items()},
                cli_exit=getattr(values.get('self').process, 'returncode', None))
        tb = tb.tb_next
    result = dict(outcome='FAIL', error_type=type(error).__name__, diagnostic=str(error),
                  unit=getattr(locals().get('ready'), 'unit', None), synthetic_diagnostics=details,
                  original_error_type=type(original).__name__, original_diagnostic=str(original),
                  submitted=getattr(locals().get('ready'), 'submitted', False),
                  synthetic_controller_conditions=conditions.stdout, conditions_exit=conditions.returncode)
print(json.dumps(result, sort_keys=True), flush=True)
raise SystemExit(0 if result['outcome'] == 'PASS' else 1)
