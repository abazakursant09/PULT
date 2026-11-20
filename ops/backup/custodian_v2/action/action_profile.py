"""Trusted v3 synthetic-action profile and lifetime checks, never caller input.

Root/OS/fixed bootstrap/independent profile digest are trust roots. This module
does not create state, keys, accounts, units, approvals or an admission permit.
"""
import hashlib
import json
import os
from pathlib import Path
import re
import select
import socket
import stat
import struct
import subprocess
import sys
import threading
import time
from types import ModuleType, SimpleNamespace
from uuid import UUID

FORMAT = 'pult-synthetic-storage-chain-profile-v3'
MODE = 'ONE_INTENT_MOCK_ONLY_NO_PERMIT'
FIELDS = {'format', 'mode', 'sources', 'controller', 'control', 'admission',
          'custodian', 'writer', 'operation', 'mock'}
SOURCES = {'profile', 'root', 'child', 'host', 'protocol', 'constructor', 'stop', 'supervisor', 'core'}
PINS = {'constructor': 'faf627fef860d560d01605e3717c9939fe81c2c4156b231b03e94c57320c2bae',
    'stop': '5406c5a81498013884ac46d0279219f299db40576357692604fb4f08c251ca8c',
    'supervisor': '3d05d22163b11f815b29c222128c4e41987c9e4a83e7b4906cbe63a18941a37e',
    'core': '961cc3aef5dcfe9d6286b15a2d5c087a33dc1e5dafdc7d5a16f6eded961afea7'}
FENCE = 'constructor-stop.claim'
LOCK = 'constructor-stop.lock'
RUNTIME, STOP = 40, 2
LAUNCH_CAPS = {'cap_setgid', 'cap_setuid', 'cap_setpcap'}
MASKS = {'/dev/shm', '/dev/mqueue', '/dev/pts', '/mnt', '/home', '/root'}


class Refused(Exception):
    pass


def canonical(value):
    return (json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=True, allow_nan=False) + '\n').encode('ascii')


def digest(raw): return hashlib.sha256(raw).hexdigest()
def identity(info): return {'device': info.st_dev, 'inode': info.st_ino}
def fingerprint(info): return tuple(getattr(info, k) for k in ('st_dev', 'st_ino', 'st_size', 'st_mtime_ns', 'st_ctime_ns'))


def path(value):
    if type(value) is not str or not re.fullmatch('/[A-Za-z0-9_./-]+', value):
        raise Refused('safe absolute root-controlled path required')
    file = Path(value)
    if file.anchor != '/' or str(file) != value or '..' in file.parts:
        raise Refused('normalized path required')
    return file


def ancestors(file):
    for parent in file.parents:
        info = parent.lstat()
        sticky = info.st_uid == 0 and bool(info.st_mode & stat.S_ISVTX)
        if (not stat.S_ISDIR(info.st_mode) or info.st_uid != 0 or info.st_mode & 0o022 and not sticky
                or any(n.startswith('system.posix_acl_') for n in os.listxattr(parent, follow_symlinks=False))):
            raise Refused('protected root ancestors required')


def root_bytes(file, maximum=100000, modes=(0o444, 0o644)):
    file = path(str(file)); ancestors(file)
    fd = os.open(file, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC)
    try:
        before = os.fstat(fd)
        if (not stat.S_ISREG(before.st_mode) or before.st_uid != 0 or before.st_nlink != 1
                or stat.S_IMODE(before.st_mode) not in modes or not 0 <= before.st_size <= maximum
                or any(n.startswith('system.posix_acl_') for n in os.listxattr(fd))):
            raise Refused('bounded root descriptor required')
        raw = os.pread(fd, maximum + 1, 0)
        if (len(raw) != before.st_size or fingerprint(os.fstat(fd)) != fingerprint(before)
                or identity(file.lstat()) != identity(before)):
            raise Refused('root input changed')
        return raw
    finally: os.close(fd)


def document(raw):
    def pairs(rows):
        value = {}
        for key, item in rows:
            if key in value: raise Refused('duplicate field')
            value[key] = item
        return value
    value = json.loads(raw, object_pairs_hook=pairs)
    if type(value) is not dict or canonical(value) != raw:
        raise Refused('canonical exact document required')
    return value


def runtime():
    if (sys.platform != 'linux' or sys.implementation.name != 'cpython' or sys.version_info[:2] != (3, 12)
            or not sys.flags.isolated or not sys.flags.no_site or not sys.dont_write_bytecode or sys.flags.optimize
            or Path(sys.executable).resolve() != Path('/usr/bin/python3.12') or sys.prefix != '/usr' or sys.base_prefix != '/usr'
            or threading.current_thread() is not threading.main_thread() or len(os.listdir('/proc/self/task')) != 1):
        raise Refused('fixed isolated single-thread runtime required')


def inode(file, wanted, uid, gid, mode, directory=False):
    file = path(str(file)); info = file.lstat()
    if (type(wanted) is not dict or set(wanted) != {'device', 'inode'}
            or any(type(v) is not int or v < 0 for v in wanted.values()) or not wanted['inode']):
        raise Refused('explicit inode required')
    if ((stat.S_ISDIR if directory else stat.S_ISREG)(info.st_mode) is not True
            or identity(info) != wanted or info.st_uid != uid or info.st_gid != gid
            or stat.S_IMODE(info.st_mode) != mode or not directory and info.st_nlink != 1
            or any(n.startswith('system.posix_acl_') for n in os.listxattr(file, follow_symlinks=False))):
        raise Refused('pinned inode or permissions changed')


def show(unit, fields):
    if type(unit) is not str or not re.fullmatch('pult-[a-z0-9-]{1,100}\\.service', unit):
        raise Refused('fixed unit name required')
    result = subprocess.run(['/usr/bin/systemctl', 'show', unit, '--no-pager', '--property=' + fields],
        stdin=subprocess.DEVNULL, capture_output=True, timeout=2, check=True,
        env={'PATH': '/usr/bin:/bin', 'LANG': 'C.UTF-8'})
    return dict(row.split('=', 1) for row in result.stdout.decode().splitlines() if '=' in row)


def conditions(unit):
    encoded = ''.join(c if c.isascii() and c.isalnum() else '_' + format(ord(c), '02x') for c in unit)
    result = subprocess.run(['/usr/bin/busctl', '--json=short', 'get-property', 'org.freedesktop.systemd1',
        '/org/freedesktop/systemd1/unit/' + encoded, 'org.freedesktop.systemd1.Unit', 'Conditions'],
        stdin=subprocess.DEVNULL, capture_output=True, timeout=2, check=True,
        env={'PATH': '/usr/bin:/bin', 'LANG': 'C.UTF-8'})
    value = json.loads(result.stdout)
    if (type(value) is not dict or set(value) != {'type', 'data'} or value['type'] != 'a(sbbsi)'
            or type(value['data']) is not list or any(type(r) is not list or len(r) != 5
                or type(r[0]) is not str or type(r[1]) is not bool or type(r[2]) is not bool
                or type(r[3]) is not str or type(r[4]) is not int for r in value['data'])):
        raise Refused('typed manager conditions required')
    return value['data']


class Profile:
    def __init__(self, file, approved_sha):
        runtime()
        if type(approved_sha) is not str or not re.fullmatch('[0-9a-f]{64}', approved_sha):
            raise Refused('independent v3 profile approval required')
        self.file = path(str(file)); self.raw = root_bytes(self.file, 32768)
        self.sha = approved_sha; self.pid = os.getpid(); self.modules = {}; self.gate_identity = None; self.attestation_identities = {}
        if digest(self.raw) != approved_sha:
            raise Refused('v3 approval mismatch')
        self._shape(document(self.raw)); self.check()

    @property
    def spec(self): return document(self.raw)

    def _shape(self, value):
        if set(value) != FIELDS or value['format'] != FORMAT or value['mode'] != MODE:
            raise Refused('distinct one-intent synthetic v3 profile required')
        sources = value['sources']
        if type(sources) is not dict or set(sources) != SOURCES:
            raise Refused('closed exact entry sources required')
        for name, source in sources.items():
            if (type(source) is not dict or set(source) != {'path', 'sha256'}
                    or type(source['sha256']) is not str or not re.fullmatch('[0-9a-f]{64}', source['sha256'])):
                raise Refused('source identity required')
            path(source['path'])
            if name in PINS and source['sha256'] != PINS[name]:
                raise Refused('fixed predecessor changed')
        if sources['profile']['path'] != str(Path(__file__)) or len({s['path'] for s in sources.values()}) != len(SOURCES):
            raise Refused('separate fixed source entries required')
        for group in ('control', 'admission'):
            wanted = {'root', 'identity', 'lock_identity'} if group == 'control' else {'root', 'identity'}
            if type(value[group]) is not dict or set(value[group]) != wanted:
                raise Refused('explicit control/admission ownership required')
            path(value[group]['root'])
        control, admission = path(value['control']['root']), path(value['admission']['root'])
        if control == admission or control.is_relative_to(admission) or admission.is_relative_to(control):
            raise Refused('private permanent fence and public read-only gate must be separate')
        for role in ('custodian', 'writer'):
            common = {'uid', 'gid', 'unit'}
            extra = {'host_profile', 'host_profile_sha256', 'ipc_directory', 'ipc_identity'} if role == 'custodian' else {'credential_file', 'credential_identity', 'access_key'}
            if type(value[role]) is not dict or set(value[role]) != common | extra:
                raise Refused('fixed role fields required')
            data = value[role]
            if (any(type(data[k]) is not int or not 1000 <= data[k] < 2**31 for k in ('uid', 'gid'))
                    or type(data['unit']) is not str or not re.fullmatch('pult-call-gate-[0-9a-f]{32}\\.service', data['unit'])):
                raise Refused('dedicated numeric roles required')
        if value['custodian']['uid'] == value['writer']['uid'] or value['custodian']['unit'] == value['writer']['unit']:
            raise Refused('separate role processes required')
        for file in (value['custodian']['host_profile'], value['custodian']['ipc_directory'], value['writer']['credential_file']): path(file)
        if not re.fullmatch('[0-9a-f]{64}', value['custodian']['host_profile_sha256']):
            raise Refused('host profile identity required')
        if type(value['writer']['access_key']) is not str or not re.fullmatch('[\x21-\x7e]{1,256}', value['writer']['access_key']):
            raise Refused('writer identity required')
        if type(value['operation']) is not dict or set(value['operation']) != {'id', 'attempt', 'event', 'base'}:
            raise Refused('one fixed operation required')
        controller = value['controller']
        if type(controller) is not dict or set(controller) != {'unit', 'invocation_id', 'boot', 'pid', 'start_ticks'}:
            raise Refused('original controller binding required')
        if (type(controller['unit']) is not str or not re.fullmatch('pult-[a-z0-9-]{1,100}\\.service', controller['unit'])
                or type(controller['invocation_id']) is not str or not re.fullmatch('[0-9a-f]{32}', controller['invocation_id'])
                or any(type(controller[k]) is not int or controller[k] <= 0 for k in ('pid', 'start_ticks'))
                or type(controller['boot']) is not str or str(UUID(controller['boot'])) != controller['boot']):
            raise Refused('original controller identity required')
        mock = value['mock']
        if type(mock) is not dict or set(mock) != {'path', 'identity', 'pid', 'start_ticks'}:
            raise Refused('explicit independent root mock fixture required')
        path(mock['path'])
        if any(type(mock[k]) is not int or mock[k] <= 0 for k in ('pid', 'start_ticks')):
            raise Refused('original mock process required')
        host = document(root_bytes(path(value['custodian']['host_profile']), 8192))
        state = path(host['state_file']).parent
        ipc = path(value['custodian']['ipc_directory'])
        writable = (state, ipc)
        protected = [self.file, path(value['custodian']['host_profile']),
            path(value['writer']['credential_file']), path(host['reader_credential_file']),
            control, admission, path(mock['path'])]
        protected += [path(s['path']) for s in sources.values()]
        if (state == ipc or state.is_relative_to(ipc) or ipc.is_relative_to(state)
                or any(p == w or p.is_relative_to(w) or w.is_relative_to(p)
                    for w in writable for p in protected)
                or (host['custodian_uid'], host['socket_gid'], host['writer_uid'], host['writer_gid']) !=
                   (value['custodian']['uid'], value['custodian']['gid'], value['writer']['uid'], value['writer']['gid'])):
            raise Refused('writable state/IPC must not contain trusted inputs or keys')

    def check(self):
        runtime()
        if self.pid != os.getpid() or digest(self.raw) != self.sha or root_bytes(self.file, 32768) != self.raw:
            raise Refused('original profile unavailable')
        s = self.spec
        for source in s['sources'].values():
            if digest(root_bytes(path(source['path']))) != source['sha256']:
                raise Refused('source closure changed')
        host = path(s['custodian']['host_profile'])
        if digest(root_bytes(host, 8192)) != s['custodian']['host_profile_sha256']:
            raise Refused('construction input changed')

    def capture(self, name):
        self.check()
        if name not in self.modules:
            source = self.spec['sources'][name]
            raw = root_bytes(path(source['path']))
            if digest(raw) != source['sha256']: raise Refused('captured source changed')
            module = ModuleType('_pult_action_' + name); module.__file__ = source['path']
            exec(compile(raw, module.__file__, 'exec', dont_inherit=True, optimize=0), module.__dict__)
            self.modules[name] = module
        return self.modules[name]

    def controller_guard(self):
        if os.getuid() != 0 or os.geteuid() != 0:
            raise Refused('root controller required')
        self.check()
        old = self.capture('stop'); supervisor = self.capture('supervisor')
        supervisor.load_core(path(self.spec['sources']['core']['path']))
        # Reuse the pinned stateless identity invariant ONLY. Never run/claim/
        # execute the old construction-only binding or override its test hooks.
        view = SimpleNamespace(spec={'controller': self.spec['controller'], 'control': self.spec['control']},
            pid=os.getpid(), supervisor=supervisor)
        view._state = lambda unit: supervisor._core()._state(unit)
        view._extra = lambda unit, fields: show(unit, fields)
        old.Bound._controller(view)
        inode(path(self.spec['control']['root']), self.spec['control']['identity'], 0, 0, 0o700, True)
        old.manager_view(path(self.spec['control']['root']), self.spec['control']['identity'])

    def gate_bytes(self):
        s = self.spec
        return canonical(dict(format='pult-one-action-admission-v3', approval=self.sha,
            operation=s['operation']['id'], controller=s['controller'],
            units={k: s[k]['unit'] for k in ('custodian', 'writer')}))

    def gate_check(self):
        s = self.spec; directory = path(s['admission']['root'])
        inode(directory, s['admission']['identity'], 0, 0, 0o755, True); ancestors(directory)
        file = directory / FENCE
        if root_bytes(file, 2048, (0o444,)) != self.gate_bytes():
            raise Refused('one-operation admission is closed or changed')
        current = identity(file.lstat())
        if self.gate_identity is None: self.gate_identity = current
        if current != self.gate_identity: raise Refused('admission inode changed')

    def kernel_role(self, role, pid):
        """Root-only real PID1 metadata check, never delegated D-Bus authority."""
        self.controller_guard()
        s = self.spec; expected = s[role]; controller = s['controller']
        fields = dict(r.split(':',1) for r in Path('/proc/'+str(pid)+'/status').read_text().splitlines() if ':' in r)
        if ([int(x) for x in fields['Uid'].split()] != [expected['uid']] * 4
                or [int(x) for x in fields['Gid'].split()] != [expected['gid']] * 4 or fields['Groups'].split()
                or fields['Threads'].strip() != '1' or fields['NoNewPrivs'].strip() != '1'
                or any(int(fields[k],16) for k in ('CapEff','CapPrm','CapInh','CapAmb','CapBnd'))):
            raise Refused('actual dropped principal not yet available')
        if Path('/proc/'+str(pid)+'/cgroup').read_text().strip() != '0::/system.slice/'+expected['unit']:
            raise Refused('original actor cgroup required')
        self._unit_properties(role, pid)

    def _unit_properties(self, role, pid):
        s = self.spec; expected = s[role]; controller = s['controller']
        own = show(expected['unit'], 'MainPID,User,Group,Type,ExitType,Restart,NRestarts,BindsTo,After,PartOf,RuntimeMaxUSec,TimeoutStopUSec,KillMode,KillSignal,FinalKillSignal,SendSIGKILL,NoNewPrivileges,PrivateNetwork,PrivateDevices,PrivateTmp,RestrictAddressFamilies,ProtectSystem,ProtectControlGroups,CapabilityBoundingSet,AmbientCapabilities,ReadWritePaths,InaccessiblePaths,RootDirectory,RootImage,BindPaths,BindReadOnlyPaths,TemporaryFileSystem')
        fixed = dict(MainPID=str(pid), User='0', Group='0', Type='exec', ExitType='cgroup',
            Restart='no', NRestarts='0', RuntimeMaxUSec='40s', TimeoutStopUSec='2s', KillMode='control-group',
            KillSignal='15', FinalKillSignal='9', SendSIGKILL='yes', PrivateDevices='yes', PrivateTmp='no',
            RestrictAddressFamilies='AF_UNIX',
            NoNewPrivileges='yes', PrivateNetwork='yes', ProtectSystem='strict', ProtectControlGroups='yes',
            AmbientCapabilities='cap_setuid', RootDirectory='', RootImage='', BindPaths='', BindReadOnlyPaths='', TemporaryFileSystem='')
        if any(own.get(k) != v for k,v in fixed.items()): raise Refused('fixed role launcher/lifecycle required')
        if (set(own.get('CapabilityBoundingSet','').split()) != LAUNCH_CAPS
                or set(own.get('InaccessiblePaths','').split()) != MASKS
                or any(controller['unit'] not in own.get(k,'').split() for k in ('BindsTo','After','PartOf'))):
            raise Refused('bounded privilege-drop launcher required')
        config = document(root_bytes(path(s['custodian']['host_profile']),8192))
        writable = {str(path(config['state_file']).parent),s['custodian']['ipc_directory']} if role=='custodian' else set()
        if set(own.get('ReadWritePaths','').split()) != writable: raise Refused('fixed writable directories required')
        if ['ConditionPathExists',False,False,str(path(s['admission']['root'])/FENCE),1] not in conditions(expected['unit']):
            raise Refused('mandatory positive admission condition required')

    def attestation_bytes(self, role, pid, start):
        return canonical(dict(format='pult-role-kernel-attestation-v3',approval=self.sha,
            controller=self.spec['controller'],role=role,unit=self.spec[role]['unit'],pid=pid,start_ticks=start))

    def actor_guard(self, role):
        if role not in ('custodian', 'writer'): raise Refused('fixed role required')
        self.check(); self.gate_check()
        s = self.spec; expected = s[role]; controller = s['controller']
        fields = dict(r.split(':', 1) for r in Path('/proc/self/status').read_text().splitlines() if ':' in r)
        if ([int(x) for x in fields['Uid'].split()] != [expected['uid']] * 4
                or [int(x) for x in fields['Gid'].split()] != [expected['gid']] * 4 or os.getgroups()
                or fields['Threads'].strip() != '1' or fields['NoNewPrivs'].strip() != '1'
                or any(int(fields[k], 16) for k in ('CapEff', 'CapPrm', 'CapInh', 'CapAmb', 'CapBnd'))):
            raise Refused('credential drop before Python required')
        if Path('/proc/sys/kernel/random/boot_id').read_text().strip() != controller['boot']:
            raise Refused('controller boot changed')
        process = Path('/proc/' + str(controller['pid']) + '/stat').read_text().rsplit(') ', 1)[1].split()
        parent_fields = dict(r.split(':',1) for r in Path('/proc/'+str(controller['pid'])+'/status').read_text().splitlines() if ':' in r)
        if (process[0] in ('Z','T','t','X') or int(process[19]) != controller['start_ticks']
                or parent_fields['Uid'].split() != ['0']*4
                or Path('/proc/'+str(controller['pid'])+'/cgroup').read_text().strip() != '0::/system.slice/'+controller['unit']):
            raise Refused('controller process changed')
        if Path('/proc/self/cgroup').read_text().strip() != '0::/system.slice/' + expected['unit']:
            raise Refused('owned role cgroup required')
        start = int(Path('/proc/self/stat').read_text().rsplit(') ',1)[1].split()[19])
        file = path(s['admission']['root'])/(role+'.attested')
        if root_bytes(file,4096,(0o444,)) != self.attestation_bytes(role,os.getpid(),start):
            raise Refused('root/kernel attestation for this exact actor required')
        current = identity(file.lstat())
        previous = self.attestation_identities.setdefault(role,current)
        if previous != current: raise Refused('actor attestation inode changed')
        self.gate_check()

    def await_actor(self, role):
        deadline=time.monotonic()+5
        while True:
            try: return self.actor_guard(role)
            except FileNotFoundError:
                if time.monotonic() >= deadline: raise Refused('root/kernel attestation deadline') from None
                time.sleep(.02)

    def operation_bytes(self):
        s = self.spec; host = document(root_bytes(path(s['custodian']['host_profile']), 8192))
        s3 = host['s3']; scope, generation = host['scope'], host['generation']
        location = s3['endpoint'] + '/' + s3['bucket'] + '/pult/erasure-v2/' + scope + '/' + generation + '/events/'
        return canonical(dict(format='pult-single-synthetic-action-v3', approval=self.sha,
            operation_id=s['operation']['id'], controller=s['controller'],
            binding=dict(scope=scope, generation=generation, location=location), attempt=s['operation']['attempt'],
            event=s['operation']['event'], base=s['operation']['base'],
            custodian={k: s['custodian'][k] for k in ('uid', 'gid')}, writer={k: s['writer'][k] for k in ('uid', 'gid')}))
