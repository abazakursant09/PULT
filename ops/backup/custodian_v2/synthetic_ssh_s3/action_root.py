"""One trusted synthetic operation, permanent prelaunch fence, paired teardown.

This separate synthetic SSH/S3 version is not a purge/restore/public-launch API.
"""
import fcntl
import ctypes
import os
from pathlib import Path
import selectors
import signal
import subprocess
import sys
import time
from types import SimpleNamespace

from pult_synthetic_live_profile_v1 import Profile, Refused, canonical, document, root_bytes, inode, identity, path, FENCE, LOCK, RUNTIME, STOP, MASKS

_ATTEMPTED = False
_FAILED = set()


class Unknown(Exception): pass


def sync(directory):
    fd = os.open(directory, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC)
    try: os.fsync(fd)
    finally: os.close(fd)


def write_all(fd, raw):
    while raw:
        n = os.write(fd, raw)
        if n <= 0: raise Unknown('write incomplete')
        raw = raw[n:]


def publish_attestation(directory, role, raw):
    """One complete inode becomes visible atomically; never replace/adopt.

    link+unlink is NOT enough: it exposes a transient nlink=2 which the stable
    root-descriptor reader rightly refuses. Linux renameat2 NOREPLACE keeps the
    fully written inode at nlink=1 throughout publication.
    """
    if role not in ('custodian','writer'): raise Refused('fixed attestation role')
    temporary=directory/(role+'.pending'); final=directory/(role+'.attested')
    fd=os.open(temporary,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW|os.O_CLOEXEC,0o444)
    try:
        os.fchmod(fd,0o444);write_all(fd,raw);os.fsync(fd)
    finally:os.close(fd)
    library=ctypes.CDLL(None,use_errno=True)
    rename=library.renameat2
    rename.argtypes=[ctypes.c_int,ctypes.c_char_p,ctypes.c_int,ctypes.c_char_p,ctypes.c_uint]
    rename.restype=ctypes.c_int
    if rename(-100,os.fsencode(temporary),-100,os.fsencode(final),1)!=0:
        raise OSError(ctypes.get_errno(),'attestation publication refused')
    sync(directory)
    return final


class Action:
    def __init__(self, profile):
        if type(profile) is not Profile: raise Refused('trusted exact profile required')
        self.profile = profile; self.lock = self.fence = self.gate = None
        self.fence_raw = None; self.gate_raw = None; self.fence_id = self.gate_id = None
        self.processes = {}; self.submitted = []; self.failure = None
        self.selector = selectors.DefaultSelector(); self.buffers = {}; self.frames = {}
        self.closed = self.failed = False
        self._role_slots_claimed = False

    def claim_role_slots(self):
        # Refuse existing units BEFORE any fence establishes teardown ownership.
        # Root/OS is trusted; the permanent fence serializes this controller,
        # never permission to adopt an unrelated pre-existing manager object.
        self.check(); supervisor=self.profile.capture('supervisor')
        supervisor.load_core(path(self.profile.spec['sources']['core']['path']))
        for role in ('custodian','writer'):
            unit=self.profile.spec[role]['unit']
            if supervisor._state(unit).get('LoadState')!='not-found' or not supervisor._empty(unit):
                raise Refused('pre-existing prospective unit or group; no adoption')
        self._role_slots_claimed = True

    def check(self):
        if self.failed or self.closed: raise Refused('one original action required')
        self.profile.controller_guard()
        if self.fence is not None:
            s = self.profile.spec; file = path(s['control']['root']) / FENCE
            if (identity(os.fstat(self.fence)) != self.fence_id or identity(file.lstat()) != self.fence_id
                    or os.pread(self.fence, 4097, 0) != self.fence_raw or root_bytes(file, 4096, (0o600,)) != self.fence_raw):
                raise Unknown('permanent fence changed')
            self.profile.capture('stop').manager_view(file.parent, s['control']['identity'], self.fence)

    def claim(self):
        self.claim_role_slots(); s = self.profile.spec
        control = path(s['control']['root'])
        inode(control / LOCK, s['control']['lock_identity'], 0, 0, 0o600)
        if root_bytes(control / LOCK, 1, (0o600,)) != b'' or {f.name for f in control.iterdir()} != {LOCK}:
            raise Refused('fresh empty permanent control directory required')
        self.lock = os.open(control / LOCK, os.O_RDWR | os.O_NOFOLLOW | os.O_CLOEXEC)
        if identity(os.fstat(self.lock)) != s['control']['lock_identity']: raise Refused('lock changed')
        fcntl.flock(self.lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        self.check()
        self.fence_raw = canonical(dict(format='pult-one-storage-action-permanent-fence-ssh-s3-v1',
            approval=self.profile.sha, operation=s['operation']['id'], controller=s['controller'],
            units={r:s[r]['unit'] for r in ('custodian', 'writer')}))
        self.fence = os.open(control / FENCE, os.O_RDWR | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC, 0o600)
        os.fchmod(self.fence, 0o600); self.fence_id = identity(os.fstat(self.fence))
        write_all(self.fence, self.fence_raw); os.fsync(self.fence); sync(control); self.check()
        admission = path(s['admission']['root'])
        inode(admission, s['admission']['identity'], 0, 0, 0o755, True)
        if list(admission.iterdir()): raise Refused('fresh admission directory required')
        self.gate_raw = self.profile.gate_bytes()
        self.gate = os.open(admission / FENCE, os.O_RDWR | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC, 0o444)
        os.fchmod(self.gate, 0o444); self.gate_id = identity(os.fstat(self.gate))
        write_all(self.gate, self.gate_raw); os.fsync(self.gate); sync(admission)
        self.profile.gate_check()
        self.profile.capture('stop').manager_view(admission, s['admission']['identity'], self.gate)

    def close_admission(self):
        # Stop producer admission BEFORE scanning/stopping even already-known
        # units. Queued starts while this controller is still active must fail.
        if self.gate is None: return
        s = self.profile.spec; directory = path(s['admission']['root']); file = directory / FENCE
        inode(directory, s['admission']['identity'], 0, 0, 0o755, True)
        if (identity(file.lstat()) != self.gate_id or identity(os.fstat(self.gate)) != self.gate_id
                or root_bytes(file, 2048, (0o444,)) != self.gate_raw):
            raise Unknown('admission closure uncertain; retain all handles')
        file.unlink(); sync(directory)
        try: file.lstat()
        except FileNotFoundError: return
        raise Unknown('admission still present')

    def command(self, role):
        s = self.profile.spec; cfg = document(root_bytes(path(s['custodian']['host_profile']), 8192)); who = s[role]
        props = dict(Type='exec', ExitType='cgroup', RemainAfterExit='yes', User='0', Group='0', SupplementaryGroups='',
            BindsTo=s['controller']['unit'], After=s['controller']['unit'], PartOf=s['controller']['unit'],
            RuntimeMaxSec=str(RUNTIME), TimeoutStartSec='2', TimeoutStopSec=str(STOP), KillMode='control-group',
            KillSignal='SIGTERM', FinalKillSignal='SIGKILL', SendSIGKILL='yes', Restart='no',
            # systemd255 seccomp setup explicitly drops CAP_SETUID unless it is
            # ambient. Keep it ONLY in the fixed OS setpriv launcher; setpriv
            # clears all five sets before executing any Python/payload code.
            NoNewPrivileges='yes', CapabilityBoundingSet='CAP_SETUID CAP_SETGID CAP_SETPCAP', AmbientCapabilities='CAP_SETUID',
            PrivateNetwork='no', PrivateDevices='yes', PrivateTmp='no', ProtectSystem='strict', ProtectHome='yes',
            InaccessiblePaths=' '.join(sorted(MASKS)), ProtectControlGroups='yes', RestrictAddressFamilies='AF_UNIX AF_INET',
            IPAddressDeny='any', IPAddressAllow=s['network']['peer_ipv4'] + '/32',
            TasksMax='16', MemoryMax='128M', LimitFSIZE='2097152', UMask='0077',
            ConditionPathExists=str(path(s['admission']['root']) / FENCE),
            Environment='PATH=/usr/bin:/bin LANG=C.UTF-8')
        if role == 'custodian': props['ReadWritePaths'] = str(path(cfg['state_file']).parent) + ' ' + s['custodian']['ipc_directory']
        args = ['/usr/bin/systemd-run','--quiet','--pipe','--no-ask-password','--expand-environment=no','--unit=' + who['unit']]
        args += ['--property=' + k + '=' + v for k,v in sorted(props.items())]
        args += ['/usr/bin/setpriv','--reuid=' + str(who['uid']),'--regid=' + str(who['gid']), '--clear-groups',
            '--bounding-set=-all','--inh-caps=-all','--ambient-caps=-all','--no-new-privs',
            '/usr/bin/python3.12','-I','-S','-B',s['sources']['child']['path'],role,str(self.profile.file),self.profile.sha]
        return args

    def launch(self, role):
        self.check(); self.profile.gate_check()
        s = self.profile.spec; unit = s[role]['unit']; supervisor = self.profile.capture('supervisor')
        if supervisor._state(unit).get('LoadState') != 'not-found': raise Refused('unit collision; no adoption')
        # The permanent record already contains BOTH exact prospective units.
        # Mark submitted before Popen: manager submission may be uncertain.
        self.submitted.append(role)
        proc = subprocess.Popen(self.command(role), stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            close_fds=True, env={'PATH':'/usr/bin:/bin','LANG':'C.UTF-8'})
        self.processes[role] = (proc, os.pidfd_open(proc.pid))
        self.frames[role] = []; self.buffers[role] = {'stdout':bytearray(),'stderr':bytearray()}
        for stream in ('stdout', 'stderr'):
            file = getattr(proc,stream); os.set_blocking(file.fileno(),False)
            self.selector.register(file,selectors.EVENT_READ,(role,stream))
        deadline=time.monotonic()+4
        while True:
            self.check(); self.profile.gate_check()
            state=supervisor._state(unit)
            pid=int(state.get('MainPID','0'))
            if pid:
                try: self.profile.kernel_role(role,pid)
                except Refused:
                    if time.monotonic() >= deadline: raise
                else:
                    start=int(Path('/proc/'+str(pid)+'/stat').read_text().rsplit(') ',1)[1].split()[19])
                    publish_attestation(path(s['admission']['root']),role,self.profile.attestation_bytes(role,pid,start))
                    self.check();self.profile.gate_check()
                    return
            if proc.poll() is not None or time.monotonic() >= deadline: raise Unknown('actor attestation startup failed')
            time.sleep(.02)

    def pump(self):
        for key,_ in self.selector.select(.025):
            role,stream = key.data
            data = os.read(key.fileobj.fileno(),4096)
            if not data:
                self.selector.unregister(key.fileobj); key.fileobj.close(); continue
            buffer = self.buffers[role][stream]; buffer.extend(data)
            if len(buffer) > (8192 if stream == 'stdout' else 1024): raise Unknown('role output bound')
            if stream == 'stderr' and buffer: raise Unknown('role stderr is not an acknowledgement')
            if stream == 'stdout':
                while b'\n' in buffer:
                    raw,rest = bytes(buffer).split(b'\n',1); buffer[:] = rest
                    if raw == b'ROLE_UNKNOWN_RETAIN_STATE': raise Unknown('role explicitly unknown; retain durable state')
                    frame = document(raw+b'\n')
                    if (set(frame) != {'format','approval','operation','status','purge','restore'}
                            or frame['format'] != 'pult-action-role-result-ssh-s3-v1' or frame['approval'] != self.profile.sha
                            or frame['operation'] != self.profile.spec['operation']['id']
                            or frame['purge'] is not False or frame['restore'] is not False):
                        raise Unknown('exact one-operation role result required')
                    expected = {'custodian':['READY_OWNERSHIP_HELD','STORAGE_COMMITTED_OWNERSHIP_HELD','CLOSED_STORAGE_ONLY'],
                                'writer':['WRITER_CLOSED_STORAGE_ONLY']}[role]
                    n = len(self.frames[role])
                    if n >= len(expected) or frame['status'] != expected[n]: raise Unknown('role transition refused')
                    self.frames[role].append(frame['status'])

    def wait_for(self, predicate, seconds=12):
        deadline = time.monotonic()+seconds
        while not predicate():
            self.check(); self.profile.gate_check(); self.pump()
            if time.monotonic() >= deadline: raise Unknown('bounded role deadline')
            for role,(proc,_) in self.processes.items():
                if proc.poll() is not None and proc.returncode != 0: raise Unknown('role CLI failed')
        self.check(); self.profile.gate_check()

    def run(self):
        global _ATTEMPTED
        if _ATTEMPTED: raise Refused('one attempt in original root process')
        _ATTEMPTED=True; successful=False; cleanup=False
        try:
            self.claim(); self.launch('custodian')
            self.wait_for(lambda:len(self.frames.get('custodian',[])) == 1)
            self.launch('writer')
            self.wait_for(lambda:len(self.frames['custodian']) == 2 and len(self.frames['writer']) == 1)
            proc,_ = self.processes['custodian']
            write_all(proc.stdin.fileno(),b'CLOSE_SINGLE_ACTION_V3\n'); proc.stdin.close()
            self.wait_for(lambda:len(self.frames['custodian']) == 3)
            for role in ('writer','custodian'):
                self.wait_for(lambda r=role:self.profile.capture('supervisor')._state(self.profile.spec[r]['unit']).get('SubState') == 'exited')
            successful=True
        except BaseException as error:
            self.failure=error
        finally:
            # Closing admission and stopping each role are independent safety
            # obligations: failure of one must never skip the others.
            cleanup_errors=[]
            try:
                self.close_admission()
            except BaseException as error:cleanup_errors.append(error)
            supervisor=None
            try:
                supervisor=self.profile.capture('supervisor'); supervisor.load_core(path(self.profile.spec['sources']['core']['path']))
            except BaseException as error:cleanup_errors.append(error);supervisor=None
            if supervisor is not None:
                for role in ('writer','custodian') if self.fence is not None and self._role_slots_claimed else reversed(self.submitted):
                    try:
                        unit=self.profile.spec[role]['unit']
                        if supervisor._state(unit).get('LoadState')=='not-found':
                            if not supervisor._empty(unit):raise Unknown('absent unit has nonempty group')
                            continue
                        supervisor._core().Manager.cleanup(SimpleNamespace(stop=STOP),unit)
                    except BaseException as error:cleanup_errors.append(error)
            try:
                deadline=time.monotonic()+3
                while self.selector.get_map():
                    if time.monotonic()>deadline: raise Unknown('role pipes not reaped')
                    self.pump()
                for proc,_ in self.processes.values(): proc.wait(timeout=2)
                cleanup=supervisor is not None and all(supervisor._empty(self.profile.spec[r]['unit'])
                    for r in (('writer','custodian') if self.fence is not None and self._role_slots_claimed else self.submitted))
                self.check()
            except BaseException as error:
                self.failure=error; cleanup=False
            finally:
                self.selector.close()
                for proc,fd in self.processes.values():
                    try:
                        if proc.poll() is None: signal.pidfd_send_signal(fd,signal.SIGKILL)
                        proc.wait(timeout=2)
                    except BaseException as error: self.failure=error; cleanup=False
                    finally:
                        os.close(fd)
                        for name in ('stdin','stdout','stderr'):
                            file=getattr(proc,name)
                            if file is not None and not file.closed: file.close()
            if cleanup_errors:
                self.failure=cleanup_errors[-1];cleanup=False
        if (not successful or not cleanup or self.failure is not None
                or any(p.returncode != 0 for p,_ in self.processes.values())
                or any(v for streams in self.buffers.values() for v in streams.values())):
            self.failed=True; _FAILED.add(self)
            raise Unknown('action or whole-group cleanup unconfirmed; fence retained') from None
        # Descriptors may now close; the permanent fence is NEVER unlinked.
        for fd in (self.gate,self.fence,self.lock):
            if fd is not None: os.close(fd)
        self.closed=True
        return dict(format='pult-single-action-completion-ssh-s3-v1',status='COMMITTED_STORAGE_ONLY_AND_STOPPED',
            approval=self.profile.sha,operation=self.profile.spec['operation']['id'],
            purge=False,restore=False,whole_groups_empty=True,permanent_fence_retained=True)
