"""Fixed synthetic role bootstrap. No request chooses paths, identity or code."""
import hashlib
import json
import os
from pathlib import Path
import select
import socket
import stat
import struct
import sys
from types import ModuleType

def stable(info):
    return tuple(getattr(info,k) for k in ('st_dev','st_ino','st_mode','st_uid','st_gid','st_nlink','st_size','st_mtime_ns','st_ctime_ns'))

def bootstrap(file, approved):
    # This fixed OS/root-owned entry is a trust root. Capture the manifest and
    # common verifier as stable root descriptors BEFORE executing its code.
    def read(file, maximum):
        file = Path(file)
        if not file.is_absolute() or '..' in file.parts: raise RuntimeError('bootstrap path')
        for p in file.parents:
            s = p.lstat()
            if (not stat.S_ISDIR(s.st_mode) or s.st_uid != 0 or s.st_mode & 0o022 and not s.st_mode & stat.S_ISVTX
                    or any(x.startswith('system.posix_acl_') for x in os.listxattr(p, follow_symlinks=False))):
                raise RuntimeError('bootstrap ancestors')
        fd = os.open(file, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC)
        try:
            s = os.fstat(fd); raw = os.pread(fd, maximum + 1, 0)
            if (not stat.S_ISREG(s.st_mode) or s.st_uid != 0 or s.st_nlink != 1 or stat.S_IMODE(s.st_mode) not in (0o444, 0o644)
                    or not 0 < len(raw) == s.st_size <= maximum or stable(os.fstat(fd)) != stable(s) or stable(file.lstat()) != stable(s)
                    or any(x.startswith('system.posix_acl_') for x in os.listxattr(fd))):
                raise RuntimeError('bootstrap root descriptor')
            return raw
        finally: os.close(fd)
    raw = read(file, 32768)
    if hashlib.sha256(raw).hexdigest() != approved: raise RuntimeError('bootstrap approval')
    value = json.loads(raw)
    source = value['sources']['profile']
    body = read(source['path'], 100000)
    if hashlib.sha256(body).hexdigest() != source['sha256']: raise RuntimeError('bootstrap common source')
    if 'pult_synthetic_live_profile_v1' in sys.modules: raise RuntimeError('foreign verifier registration')
    common = ModuleType('pult_synthetic_live_profile_v1'); common.__file__ = source['path']
    sys.modules[common.__name__] = common
    exec(compile(body, common.__file__, 'exec', dont_inherit=True, optimize=0), common.__dict__)
    return common.Profile(file, approved)


def emit(profile, status, **values):
    raw = sys.modules['pult_synthetic_live_profile_v1'].canonical(dict(format='pult-action-role-result-ssh-s3-v1',
        approval=profile.sha, operation=profile.spec['operation']['id'], status=status,
        purge=False, restore=False, **values))
    if len(raw) > 2048 or os.write(1, raw) != len(raw): raise RuntimeError('role reply uncertain')


def custodian(profile):
    host = profile.capture('host').prepare(profile)
    directory = Path(profile.spec['custodian']['ipc_directory'])
    common = sys.modules['pult_synthetic_live_profile_v1']
    common.inode(directory, profile.spec['custodian']['ipc_identity'],
        profile.spec['custodian']['uid'], profile.spec['writer']['gid'], 0o750, True)
    listener = socket.socket(socket.AF_UNIX, socket.SOCK_SEQPACKET)
    listener.setsockopt(socket.SOL_SOCKET, socket.SO_PASSCRED, 1)
    listener.settimeout(7)
    file = directory / 'action.sock'
    # Never unlink/adopt a pre-existing listener.
    listener.bind(str(file)); os.chmod(file, 0o666); listener.listen(1)
    emit(profile, 'READY_OWNERSHIP_HELD')
    connection, _ = listener.accept()
    with connection:
        outcome = host.serve_one(connection)
    listener.close()
    if outcome != 'COMMITTED_STORAGE_ONLY': raise RuntimeError('storage action unknown')
    emit(profile, 'STORAGE_COMMITTED_OWNERSHIP_HELD')
    if not select.select([sys.stdin.buffer], [], [], 5)[0]: raise RuntimeError('close deadline')
    if sys.stdin.buffer.readline(64) != b'CLOSE_SINGLE_ACTION_V3\n': raise RuntimeError('exact close command')
    host.close()
    emit(profile, 'CLOSED_STORAGE_ONLY')


def writer(profile):
    module = profile.capture('host')
    capsule = module.capture_payload(profile, 'writer')
    import event_codec, host_core
    import pult_synthetic_store_v1 as event_store
    old = profile.capture('constructor'); s = profile.spec; cfg = old.document(old.root_bytes(Path(s['custodian']['host_profile']), 8192))
    for denied in (cfg['reader_credential_file'],cfg['state_file'],cfg['lease_file']):
        try: fd=os.open(denied,os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK|os.O_CLOEXEC)
        except PermissionError: pass
        else:
            os.close(fd)
            raise RuntimeError('writer must not open custodian key/state/Lease')
    # The same descriptor parser is reused with the writer's distinct private
    # inode/uid/gid/access ID, never by opening the reader's credential.
    key_view = dict(reader_credential_file=s['writer']['credential_file'],
        reader_credential_identity=s['writer']['credential_identity'], reader_access_key=s['writer']['access_key'],
        custodian_uid=s['writer']['uid'], socket_gid=s['writer']['gid'])
    profile.actor_guard('writer')
    key, _ = old.credential(host_core, key_view)
    capability = event_store.EventWriter(**cfg['s3'], binding=event_codec.Binding(cfg['scope'], cfg['generation']),
        credential=key, peer_ipv4=profile.spec['network']['peer_ipv4'])
    protocol = profile.capture('protocol')
    connection = socket.socket(socket.AF_UNIX, socket.SOCK_SEQPACKET)
    connection.setsockopt(socket.SOL_SOCKET, socket.SO_PASSCRED, 1)
    connection.settimeout(5)
    connection.connect(str(Path(s['custodian']['ipc_directory']) / 'action.sock'))
    answer = protocol.Client(capability, protocol.Operation(profile.operation_bytes()),
        lambda: profile.actor_guard('writer')).append(connection)
    connection.close()
    if answer['status'] != 'COMMITTED_STORAGE_ONLY': raise RuntimeError('writer action unknown')
    capability.close(); capsule.close()
    emit(profile, 'WRITER_CLOSED_STORAGE_ONLY')


def main():
    if len(sys.argv) != 4 or sys.argv[1] not in ('custodian', 'writer'): raise RuntimeError('fixed role invocation')
    profile = bootstrap(sys.argv[2], sys.argv[3]); role = sys.argv[1]
    profile.await_actor(role)
    {'custodian': custodian, 'writer': writer}[role](profile)


if __name__ == '__main__':
    try: main()
    except BaseException as error:
        # Never export credentials, payload data or an opaque constructor's self.
        # Bounded code locations/types only, never exception messages/locals.
        errors=[error]
        common=sys.modules.get('pult_synthetic_live_profile_v1')
        if common is not None:
            for owner in common.__dict__.get('_action_host_retained', [])[-4:]:
                if isinstance(owner, BaseException): errors.append(owner)
                else: errors.extend(owner.__dict__.get('_failures', [])[-2:])
        locations=[]
        for saved in errors[-4:]:
            tb=saved.__traceback__
            while tb is not None:
                locations.append([type(saved).__name__,Path(tb.tb_frame.f_code.co_filename).name,tb.tb_frame.f_code.co_name,tb.tb_lineno])
                tb=tb.tb_next
        data=json.dumps(dict(role_unknown_locations=locations[-7:]),separators=(',',':')).encode()[:900]+b'\n'
        os.write(2,data)
        os.write(1, b'ROLE_UNKNOWN_RETAIN_STATE\n')
        raise SystemExit(70) from None
