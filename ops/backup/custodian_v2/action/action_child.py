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
                    or not 0 < len(raw) == s.st_size <= maximum or os.fstat(fd) != s or file.lstat() != s
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
    if 'pult_action_profile_v3' in sys.modules: raise RuntimeError('foreign verifier registration')
    common = ModuleType('pult_action_profile_v3'); common.__file__ = source['path']
    sys.modules[common.__name__] = common
    exec(compile(body, common.__file__, 'exec', dont_inherit=True, optimize=0), common.__dict__)
    return common.Profile(file, approved)


def emit(profile, status, **values):
    raw = sys.modules['pult_action_profile_v3'].canonical(dict(format='pult-action-role-result-v3',
        approval=profile.sha, operation=profile.spec['operation']['id'], status=status,
        purge=False, restore=False, **values))
    if len(raw) > 2048 or os.write(1, raw) != len(raw): raise RuntimeError('role reply uncertain')


def bridge(profile, role):
    def call(request):
        import httpx
        profile.actor_guard(role)
        value = profile.spec['mock']; file = Path(value['path']); st = file.lstat()
        if (not stat.S_ISSOCK(st.st_mode) or st.st_uid != 0 or
                dict(device=st.st_dev, inode=st.st_ino) != value['identity']):
            raise RuntimeError('mock socket changed')
        with socket.socket(socket.AF_UNIX, socket.SOCK_SEQPACKET) as connection:
            connection.settimeout(2)
            connection.connect(str(file))
            pid, uid, gid = struct.unpack('3i', connection.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, 12))
            peer = connection.getsockopt(socket.SOL_SOCKET, 77, 4)
            fd = struct.unpack('i', peer)[0]
            os.set_inheritable(fd, False)
            try:
                info = Path('/proc/' + str(pid) + '/stat').read_text().rsplit(') ', 1)[1].split()
                if (pid != value['pid'] or uid != 0 or gid != 0 or info[0] == 'Z'
                        or int(info[19]) != value['start_ticks'] or select.select([fd], [], [], 0)[0]):
                    raise RuntimeError('original root mock peer required')
                common = sys.modules['pult_action_profile_v3']
                raw = common.canonical(dict(method=request.method, url=str(request.url),
                    headers=list(request.headers.multi_items()), body=request.read().hex()))
                if len(raw) > 16384 or connection.send(raw) != len(raw): raise RuntimeError('mock request bound')
                response, ancillary, flags, _ = connection.recvmsg(16385, socket.CMSG_SPACE(64))
                for level, kind, body in ancillary:
                    if level == socket.SOL_SOCKET and kind == socket.SCM_RIGHTS:
                        for n in range(0, len(body) - 3, 4): os.close(struct.unpack('i', body[n:n+4])[0])
                if flags or ancillary or len(response) > 16384: raise RuntimeError('mock reply bound')
                reply = common.document(response)
            finally: os.close(fd)
        profile.actor_guard(role)
        if reply == {'error': 'mock_io_failed'}: raise httpx.ReadError('mock uncertain', request=request)
        if (set(reply) != {'status', 'body', 'headers'} or type(reply['status']) is not int
                or not 100 <= reply['status'] <= 599 or type(reply['body']) is not str or type(reply['headers']) is not list):
            raise RuntimeError('mock reply fields')
        return httpx.Response(reply['status'], content=bytes.fromhex(reply['body']), headers=reply['headers'], request=request)
    return call


def custodian(profile):
    host = profile.capture('host').prepare(profile, bridge(profile, 'custodian'))
    directory = Path(profile.spec['custodian']['ipc_directory'])
    common = sys.modules['pult_action_profile_v3']
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
    import event_store, event_codec, host_core, httpx
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
        credential=key, transport=httpx.MockTransport(bridge(profile, 'writer')))
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
        common=sys.modules.get('pult_action_profile_v3')
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
