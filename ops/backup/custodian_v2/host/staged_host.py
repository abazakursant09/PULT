"""Trusted construction ONLY, with fixed offline reader; no listener or admission.

Root/OS/interpreter/stdlib/this entry and an independent operator profile digest
are trust roots, not anti-root attestation. No argument comes from an RPC caller.
"""
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import sys
import threading
from types import ModuleType
from uuid import UUID

INTAKE_SHA = '48c238948c9f2c94ed485093b0a583ebcaf7dca760612622e5a01eebd063617c'
FORMAT = 'pult-staged-host-construction-profile-v1'
MODE = 'CONSTRUCT_ONLY_NO_ADMISSION'
BOUNDS = {'max_receipts': 1024, 'max_state_bytes': 2097152, 'record_bytes': 2048}
FIELDS = {'format', 'mode', 'source_intake', 'scope', 'generation', 'custodian_uid',
          'socket_gid', 'writer_uid', 'writer_gid', 'state_file', 'state_identity',
          'lease_file', 'lease_identity', 'reader_credential_file',
          'reader_credential_identity', 'reader_access_key', 's3', 'bounds'}
PAYLOAD = {'anchor_v2', 'protocol_v2', 'event_codec', 'event_store', 'owned_host',
           'host_core', 'ops', 'anyio', 'certifi', 'h11', 'httpcore', 'httpx', 'idna',
           'typing_extensions', 'staged_ipc'}
_ATTEMPTED = False
_FAILED = set()


class Refused(Exception):
    pass


def canonical(value):
    return (json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=True,
                       allow_nan=False) + '\n').encode('ascii')


def runtime():
    if (sys.platform != 'linux' or sys.implementation.name != 'cpython'
            or sys.version_info[:2] != (3, 12) or not sys.flags.isolated
            or not sys.flags.no_site or not sys.dont_write_bytecode or sys.flags.optimize
            or Path(sys.executable).resolve() != Path('/usr/bin/python3.12')
            or sys.prefix != '/usr' or sys.base_prefix != '/usr'
            or os.getuid() < 1000 or os.getuid() != os.geteuid()):
        raise Refused('fixed isolated runtime required')
    fields = dict(row.split(':', 1) for row in Path('/proc/self/status').read_text().splitlines() if ':' in row)
    if (fields['Threads'].strip() != '1' or
            any(int(fields[k], 16) for k in ('CapEff', 'CapPrm', 'CapInh', 'CapAmb'))):
        raise Refused('single capability-free process required')


def path(value):
    if type(value) is not str or '\0' in value:
        raise Refused('explicit path required')
    result = Path(value)
    if result.anchor != '/' or '..' in result.parts or str(result) != value:
        raise Refused('normalized absolute path required')
    return result


def ancestors(file, owners):
    for parent in file.parents:
        info = parent.lstat()
        sticky = info.st_uid == 0 and bool(info.st_mode & stat.S_ISVTX)
        if (not stat.S_ISDIR(info.st_mode) or info.st_uid not in owners
                or info.st_mode & 0o022 and not sticky
                or any(n.startswith('system.posix_acl_') for n in os.listxattr(parent, follow_symlinks=False))):
            raise Refused('protected ancestors required')


def root_bytes(file, maximum):
    ancestors(file, {0})
    fd = os.open(file, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC)
    try:
        before = os.fstat(fd)
        if (not stat.S_ISREG(before.st_mode) or before.st_uid != 0 or before.st_nlink != 1
                or stat.S_IMODE(before.st_mode) not in (0o444, 0o644)
                or not 0 < before.st_size <= maximum
                or any(n.startswith('system.posix_acl_') for n in os.listxattr(fd))):
            raise Refused('bounded root descriptor required')
        raw = bytearray()
        while len(raw) <= maximum:
            chunk = os.read(fd, min(8192, maximum + 1 - len(raw)))
            if not chunk:
                break
            raw.extend(chunk)
        after = os.fstat(fd)
        current = file.lstat()
        if (len(raw) != before.st_size or identity(current) != identity(before)
                or fingerprint(before) != fingerprint(after)):
            raise Refused('root input changed')
        return bytes(raw)
    finally:
        os.close(fd)


def identity(info):
    return {'device': info.st_dev, 'inode': info.st_ino}


def fingerprint(info):
    return tuple(getattr(info, k) for k in ('st_dev', 'st_ino', 'st_size', 'st_mtime_ns', 'st_ctime_ns'))


def document(raw):
    def pairs(rows):
        result = {}
        for key, value in rows:
            if key in result:
                raise Refused('duplicate profile field')
            result[key] = value
        return result
    value = json.loads(raw, object_pairs_hook=pairs)
    if type(value) is not dict or canonical(value) != raw:
        raise Refused('canonical profile required')
    return value


def profile(raw, profile_file):
    value = document(raw)
    if set(value) != FIELDS or value['format'] != FORMAT or value['mode'] != MODE:
        raise Refused('distinct construction profile required')
    for key in ('custodian_uid', 'socket_gid', 'writer_uid', 'writer_gid'):
        if type(value[key]) is not int or not 1000 <= value[key] < 2**31:
            raise Refused('dedicated principal required')
    if value['custodian_uid'] == value['writer_uid']:
        raise Refused('separate writer required')
    fields = dict(row.split(':', 1) for row in Path('/proc/self/status').read_text().splitlines() if ':' in row)
    if ([int(x) for x in fields['Uid'].split()] != [value['custodian_uid']] * 4
            or [int(x) for x in fields['Gid'].split()] != [value['socket_gid']] * 4
            or not set(os.getgroups()).issubset({value['socket_gid']})):
        raise Refused('profile principal mismatch')
    if type(value['scope']) is not str or not re.fullmatch('[A-Za-z0-9_-]{1,64}', value['scope']):
        raise Refused('scope required')
    generation = UUID(value['generation']) if type(value['generation']) is str else None
    if generation is None or generation.version != 4 or str(generation) != value['generation']:
        raise Refused('canonical generation required')
    if (type(value['bounds']) is not dict or value['bounds'] != BOUNDS
            or any(type(v) is not int for v in value['bounds'].values())):
        raise Refused('fixed protective bounds required')
    source = value['source_intake']
    if (type(source) is not dict or set(source) != {'entry', 'profile_file', 'profile_sha256'}
            or type(source['profile_sha256']) is not str
            or not re.fullmatch('[0-9a-f]{64}', source['profile_sha256'])):
        raise Refused('explicit source intake required')
    entry = path(source['entry'])
    source_profile = path(source['profile_file'])
    intake = document(root_bytes(source_profile, 8192))
    if hashlib.sha256(canonical(intake)).hexdigest() != source['profile_sha256']:
        raise Refused('source profile binding mismatch')
    root = path(intake['release_root'])
    for key in ('cold_entry', 'staged_source'):
        path(intake[key])
    state, lease, secret = (path(value[k]) for k in ('state_file', 'lease_file', 'reader_credential_file'))
    if (lease != state.parent / 'owner.lock' or
            any(p.is_relative_to(root) for p in (state, lease, secret, profile_file, source_profile, entry))
            or secret.parent == state.parent or secret.parent in state.parents or state.parent in secret.parents
            or len({state, lease, secret, profile_file, source_profile, entry}) != 6):
        raise Refused('separate closed inputs required')
    identities = []
    for key in ('state_identity', 'lease_identity', 'reader_credential_identity'):
        current = value[key]
        if (type(current) is not dict or set(current) != {'device', 'inode'}
                or any(type(v) is not int or v < 0 for v in current.values()) or not current['inode']):
            raise Refused('explicit inode identity required')
        identities.append((current['device'], current['inode']))
    if len(set(identities)) != 3:
        raise Refused('separate inode identities required')
    if type(value['reader_access_key']) is not str or not re.fullmatch('[\x21-\x7e]{1,256}', value['reader_access_key']):
        raise Refused('explicit reader identity required')
    s3 = value['s3']
    if (type(s3) is not dict or set(s3) != {'endpoint', 'region', 'bucket'}
            or s3['region'] != 'ru-3' or s3['endpoint'] != 'https://s3.ru-3.storage.selcloud.ru'
            or type(s3['bucket']) is not str or not re.fullmatch('[a-z0-9][a-z0-9-]{1,61}[a-z0-9]', s3['bucket'])):
        raise Refused('fixed ru3 location required')
    return value


def credential(host, value):
    file = path(value['reader_credential_file'])
    ancestors(file, {0, value['custodian_uid']})
    fd = os.open(file, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC)
    try:
        before = os.fstat(fd)
        if (not stat.S_ISREG(before.st_mode) or before.st_uid != value['custodian_uid']
                or before.st_gid != value['socket_gid'] or before.st_nlink != 1
                or stat.S_IMODE(before.st_mode) not in (0o400, 0o600)
                or not 0 < before.st_size <= 8192 or host._acl(fd) is not None
                or identity(before) != value['reader_credential_identity']):
            raise Refused('pinned private reader descriptor required')
        raw = bytearray()
        while len(raw) <= 8192:
            chunk = os.read(fd, min(4096, 8193 - len(raw)))
            if not chunk:
                break
            raw.extend(chunk)
        if (len(raw) != before.st_size or fingerprint(before) != fingerprint(os.fstat(fd))
                or identity(file.lstat()) != identity(before)):
            raise Refused('reader input changed')
        result = host._json(bytes(raw))
        if set(result) != {'access_key', 'secret_key'} or result['access_key'] != value['reader_access_key']:
            raise Refused('reader identity mismatch')
        return result, fingerprint(before)
    finally:
        os.close(fd)


def no_recovery_state(value):
    """Reject recovery inputs without ever opening a SQLite connection.

    The dedicated principal and already-held Lease are trust roots here. No
    live append handler is exposed by this construction-only stage.
    """
    file = path(value['state_file'])
    def no_sidecars():
        for suffix in ('-journal', '-wal', '-shm'):
            try:
                Path(str(file) + suffix).lstat()
            except FileNotFoundError:
                continue
            raise Refused('recovery sidecar requires separate offline review')
    no_sidecars()
    fd = os.open(file, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC)
    try:
        before = os.fstat(fd)
        if (not stat.S_ISREG(before.st_mode) or before.st_nlink != 1
                or before.st_uid != value['custodian_uid'] or before.st_gid != value['socket_gid']
                or stat.S_IMODE(before.st_mode) != 0o600 or not 100 <= before.st_size <= BOUNDS['max_state_bytes']
                or identity(before) != value['state_identity']
                or any(n.startswith('system.posix_acl_') for n in os.listxattr(fd))):
            raise Refused('existing private state descriptor required')
        header = os.read(fd, 100)
        if (len(header) != 100 or header[:16] != b'SQLite format 3\0'
                or header[18:20] != b'\x01\x01'
                or fingerprint(before) != fingerprint(os.fstat(fd))
                or identity(file.lstat()) != identity(before)):
            raise Refused('stable rollback-mode state without recovery required')
        no_sidecars()
    finally:
        os.close(fd)


class Constructed:
    def __init__(self, file, raw, value):
        self._file, self._raw, self._value = file, raw, value
        self._capsule = self._owner = self._anchor = self._reader = self._server = None
        self._credential_stat = None
        self._failure = self._restore_error = self._registrations = None
        self._pid, self._thread = os.getpid(), threading.current_thread()
        self._closed = self._failed = False

    def __repr__(self):
        return '<StagedHostConstructed no listener or admission>'

    def _check(self):
        runtime()
        if (self._closed or self._failed or os.getpid() != self._pid
                or threading.current_thread() is not self._thread
                or root_bytes(self._file, 8192) != self._raw):
            raise Refused('construction handle unavailable')
        if self._capsule is not None:
            self._capsule.check()
        if self._owner is not None:
            self._owner.check()
            no_recovery_state(self._value)
            self._owner.check()
        if self._credential_stat is not None:
            secret = path(self._value['reader_credential_file'])
            ancestors(secret, {0, self._value['custodian_uid']})
            if fingerprint(secret.lstat()) != self._credential_stat:
                raise Refused('reader identity drift')

    def check(self):
        try:
            self._check()
            state = self._anchor.load()
            self._check()
            return dict(status='STAGED_HOST_CONSTRUCTED_NO_ADMISSION',
                        head={'sequence': state.head.sequence, 'digest': state.head.digest},
                        revision=state.revision, pending=state.pending is not None,
                        write_admitted=False, listener_admitted=False, purge_or_restore_admitted=False)
        except Exception:
            raise Refused('construction check refused') from None

    def close(self):
        if self._closed:
            return
        if self._failed or os.getpid() != self._pid or threading.current_thread() is not self._thread:
            raise Refused('cleanup uncertain; process exit required')
        registrations = None
        try:
            if self._reader is not None:
                self._reader.close()
            self._check()
            registrations = (tuple(sys.meta_path), {n: m for n, m in sys.modules.items()
                if n.split('.', 1)[0] in PAYLOAD})
            if self._capsule is not None:
                self._capsule.close()
            if self._owner is not None:
                self._owner.check()
                self._owner.close()
        except BaseException as error:
            self._failed = True
            self._failure = error
            self._registrations = registrations
            _FAILED.add(self)
            if registrations is not None:
                try:
                    meta, modules = registrations
                    if any(n in sys.modules and sys.modules[n] is not m for n, m in modules.items()):
                        raise Refused('foreign import registration during cleanup')
                    for name, module in modules.items():
                        sys.modules[name] = module
                    sys.meta_path[:] = meta
                except BaseException as restore_error:
                    # Keep both tracebacks and the full owned snapshot rooted;
                    # no retry/admission is available even if restoration fails.
                    self._restore_error = restore_error
            raise Refused('cleanup uncertain; process exit required') from None
        self._failure = self._registrations = None
        self._closed = True


def prepare(profile_file, approved_sha256):
    global _ATTEMPTED
    ready = None
    try:
        if _ATTEMPTED:
            raise Refused('one construction attempt per process')
        _ATTEMPTED = True
        runtime()
        if type(approved_sha256) is not str or not re.fullmatch('[0-9a-f]{64}', approved_sha256):
            raise Refused('independent host profile approval required')
        file = path(profile_file)
        raw = root_bytes(file, 8192)
        if hashlib.sha256(raw).hexdigest() != approved_sha256:
            raise Refused('host profile approval mismatch')
        value = profile(raw, file)
        source = value['source_intake']
        entry_file = path(source['entry'])
        entry_raw = root_bytes(entry_file, 100000)
        if hashlib.sha256(entry_raw).hexdigest() != INTAKE_SHA:
            raise Refused('fixed source intake required')
        entry = ModuleType('_staged_fixed_source_intake')
        entry.__file__ = str(entry_file)
        exec(compile(entry_raw, str(entry_file), 'exec', dont_inherit=True, optimize=0), entry.__dict__)
        ready = Constructed(file, raw, value)
        ready._capsule = entry.capture(source['profile_file'], source['profile_sha256'])
        ready._capsule.activate()
        import anchor_v2, event_codec, event_store, owned_host, host_core, staged_ipc, httpx
        if anchor_v2.MAX_RECEIPTS != BOUNDS['max_receipts'] or event_codec.MAX_RECORD_BYTES != BOUNDS['record_bytes']:
            raise Refused('loaded protective bounds mismatch')
        ready._check()
        spec = {k: value[k] for k in ('state_file', 'state_identity', 'lease_file', 'lease_identity', 'custodian_uid', 'socket_gid')}
        for name in ('state_file', 'lease_file'):
            current = path(value[name])
            ancestors(current, {0, value['custodian_uid']})
            if any(n.startswith('system.posix_acl_') for n in os.listxattr(current, follow_symlinks=False)):
                raise Refused('private state without ACL required')
        ready._owner = owned_host.Lease(host_core, spec)
        ready._check()
        binding = event_codec.Binding(value['scope'], value['generation'])
        s3 = value['s3']
        location = s3['endpoint'] + '/' + s3['bucket'] + '/pult/erasure-v2/' + binding.scope + '/' + binding.generation + '/events/'
        ready._anchor = anchor_v2.V2Anchor(value['state_file'], binding=binding, location=location, owner=ready._owner)
        ready._anchor.load()
        ready._check()
        secret, ready._credential_stat = credential(host_core, value)
        ready._check()
        # Opaque reader construction may fail after acquiring internal resources.
        # Retain the owner if it did not return a provably closeable object.
        ready._failed = True
        _FAILED.add(ready)
        def no_io(request):
            raise Refused('remote I/O not admitted')
        ready._reader = event_store.EventReader(**s3, binding=binding, credential=secret,
                                               transport=httpx.MockTransport(no_io))
        ready._failed = False
        _FAILED.discard(ready)
        ready._check()
        ready._server = staged_ipc.Server(ready._anchor, ready._reader, value['writer_uid'], value['writer_gid'])
        ready.check()
        return ready
    except BaseException as error:
        if ready is not None:
            # Includes an opaque constructor's self in its original traceback.
            # Never stringify or export this retained private failure object.
            ready._failure = error
            try:
                ready.close()
            except BaseException:
                pass
        if isinstance(error, (KeyboardInterrupt, SystemExit)):
            raise
        raise Refused('staged host construction refused') from None
