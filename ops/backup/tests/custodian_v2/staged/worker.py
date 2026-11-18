"""Private Linux staged-IPC proof worker; no production entry or live transport."""
import array
import json
import os
from pathlib import Path
import re
import select
import signal
import socket
import stat
import struct
import sys
import time
from uuid import uuid4

RPC_LIMIT = 16384
RPC_TIMEOUT = 3
TIMEOUT = 8


def canonical(value):
    return (json.dumps(value, sort_keys=True, separators=(',', ':'),
                       ensure_ascii=True, allow_nan=False) + '\n').encode('ascii')


def emit(event, **values):
    print(canonical(dict(event=event, **values)).decode('ascii'), end='', flush=True)


def no_network(event, args):
    if event == 'socket.__new__' and args[1] != socket.AF_UNIX:
        raise RuntimeError('UNIX_ONLY_TEST_WORKER')
    if event == 'socket.getaddrinfo':
        raise RuntimeError('NO_TEST_DNS')


def packet(connection):
    raw, items, flags, _ = connection.recvmsg(
        RPC_LIMIT + 1, socket.CMSG_SPACE(64 * array.array('i').itemsize), socket.MSG_CMSG_CLOEXEC)
    rights = []
    try:
        for level, kind, value in items:
            if level == socket.SOL_SOCKET and kind == socket.SCM_RIGHTS:
                descriptors = array.array('i')
                descriptors.frombytes(value[:len(value) // descriptors.itemsize * descriptors.itemsize])
                rights.extend(descriptors)
        if not raw or len(raw) > RPC_LIMIT or items or flags & (socket.MSG_TRUNC | socket.MSG_CTRUNC):
            raise RuntimeError('BOUNDED_RPC_PACKET_REQUIRED')
        def pairs(rows):
            result = {}
            for name, item in rows:
                if name in result:
                    raise ValueError('DUPLICATE_RPC_FIELD')
                result[name] = item
            return result
        value = json.loads(raw, object_pairs_hook=pairs)
        if type(value) is not dict or canonical(value) != raw:
            raise RuntimeError('CANONICAL_RPC_PACKET_REQUIRED')
        return value
    finally:
        for descriptor in rights:
            os.close(descriptor)


def send_packet(connection, value):
    raw = canonical(value)
    if not 0 < len(raw) <= RPC_LIMIT or connection.send(raw) != len(raw):
        raise RuntimeError('RPC_SEND_UNCERTAIN')


def read_key(file):
    value = json.loads(Path(file).read_bytes())
    if type(value) is not dict or set(value) != {'access_key', 'secret_key'}:
        raise RuntimeError('FIXTURE_KEY_FIELDS_REQUIRED')
    return value


def fd_count():
    # listdir's temporary /proc descriptor has closed before these fstat calls.
    count = 0
    for name in os.listdir('/proc/self/fd'):
        try:
            os.fstat(int(name))
        except OSError:
            continue
        count += 1
    return count


def remove_socket(path, identity):
    info = path.lstat()
    if not stat.S_ISSOCK(info.st_mode) or (info.st_dev, info.st_ino) != identity:
        raise RuntimeError('FIXTURE_SOCKET_IDENTITY_CHANGED')
    path.unlink()


class BoundListener:
    """Close and unlink only the socket inode this test worker created."""
    def __init__(self, path, *, credentials=False):
        self.path = Path(path)
        self.identity = None
        self.socket = socket.socket(socket.AF_UNIX, socket.SOCK_SEQPACKET)
        try:
            self.socket.settimeout(TIMEOUT)
            if credentials:
                self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_PASSCRED, 1)
            self.socket.bind(str(self.path))
            info = self.path.lstat()
            self.identity = (info.st_dev, info.st_ino)
            self.path.chmod(0o666)
            self.socket.listen(8 if not credentials else 1)
        except BaseException:
            self.close()
            raise

    def close(self):
        self.socket.close()
        if self.identity is not None:
            remove_socket(self.path, self.identity)
            self.identity = None

    def __enter__(self):
        return self.socket

    def __exit__(self, *_):
        self.close()


def broker_rpc(config, request):
    import httpx
    with socket.socket(socket.AF_UNIX, socket.SOCK_SEQPACKET) as connection:
        connection.settimeout(RPC_TIMEOUT)
        connection.connect(config['broker_path'])
        _, uid, _ = struct.unpack('3i', connection.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, 12))
        if uid != 0:
            raise RuntimeError('ROOT_MOCK_BROKER_REQUIRED')
        send_packet(connection, dict(method=request.method, url=str(request.url),
                                    headers=list(request.headers.multi_items()), body=request.read().hex()))
        reply = packet(connection)
    if reply == {'error': 'mock_io_failed'}:
        raise httpx.ReadError('mock_io_failed', request=request)
    if (set(reply) != {'status', 'body', 'headers'} or type(reply['status']) is not int
            or not 100 <= reply['status'] <= 599 or type(reply['body']) is not str
            or type(reply['headers']) is not list):
        raise RuntimeError('MOCK_RESPONSE_FIELDS_REQUIRED')
    return httpx.Response(reply['status'], content=bytes.fromhex(reply['body']),
                          headers=reply['headers'], request=request)


def broker(case, config):
    import httpx
    import crash_worker  # The root mock broker alone imports FakeObjects.
    from event_codec import Binding
    crash_worker.ENDPOINT = config['endpoint']
    crash_worker.BUCKET = config['bucket']
    crash_worker.BINDING = Binding(**config['binding'])
    crash_worker.LOCATION = (config['endpoint'] + '/' + config['bucket'] + '/pult/erasure-v2/'
                             + crash_worker.BINDING.scope + '/' + crash_worker.BINDING.generation + '/events/')
    crash_worker.READER_KEY = read_key(config['reader_key'])
    crash_worker.WRITER_KEY = read_key(config['writer_key'])
    objects = crash_worker.FakeObjects(case / 'objects')
    objects.mode = config['mode'] if config['mode'] in {
        'write_412', 'write_500', 'write_unknown', 'read_fail', 'tamper'} else 'normal'
    path = case / 'broker.sock'
    if Path(config['broker_path']) != path:
        raise RuntimeError('CASE_BROKER_PATH_REQUIRED')
    with BoundListener(path) as listener:
        emit('BROKER_READY', pid=os.getpid())
        while True:
            ready, _, _ = select.select([listener, sys.stdin], [], [])
            if sys.stdin in ready:
                control = sys.stdin.readline()
                if not control or control.strip() == 'STOP':
                    return 0
                raise RuntimeError('BROKER_CONTROL_REFUSED')
            if listener not in ready:
                continue
            connection, _ = listener.accept()
            with connection:
                connection.settimeout(RPC_TIMEOUT)
                _, uid, _ = struct.unpack('3i', connection.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, 12))
                value = packet(connection)
                if set(value) != {'method', 'url', 'headers', 'body'}:
                    raise RuntimeError('MOCK_REQUEST_FIELDS_REQUIRED')
                role = {config['owner_uid']: ('reader', 'GET'), config['writer_uid']: ('writer', 'PUT')}.get(uid)
                if role is None or value['method'] != role[1]:
                    raise RuntimeError('MOCK_PEER_CAPABILITY_REFUSED')
                request = httpx.Request(value['method'], value['url'], headers=value['headers'],
                                        content=bytes.fromhex(value['body']))
                leaf = request.url.path.rsplit('/', 1)[-1]
                if not re.fullmatch('[0-9]{20}[.]json', leaf):
                    raise RuntimeError('MOCK_OBJECT_SEQUENCE_REQUIRED')
                raw = canonical(dict(method=request.method, uid=uid, sequence=int(leaf[:20])))
                fd = os.open(case / 'calls.jsonl', os.O_WRONLY | os.O_APPEND | os.O_CREAT | os.O_NOFOLLOW | os.O_CLOEXEC, 0o600)
                try:
                    remaining = memoryview(raw)
                    while remaining:
                        count = os.write(fd, remaining)
                        if count <= 0:
                            raise RuntimeError('MOCK_CALL_LOG_INCOMPLETE')
                        remaining = remaining[count:]
                    os.fsync(fd)
                finally:
                    os.close(fd)
                try:
                    response = objects.handle(role[0], request)
                except httpx.ReadError:
                    send_packet(connection, {'error': 'mock_io_failed'})
                else:
                    send_packet(connection, dict(status=response.status_code, body=response.content.hex(),
                                                 headers=list(response.headers.multi_items())))


def owner_injection(staged, mode):
    original = staged.Channel.send
    def injected(channel, value):
        status = value.get('status')
        value = json.loads(canonical(value))
        if status == 'PREPARED':
            if mode == 'bad_prepared':
                value['authority']['purge'] = True
            elif mode == 'bool_prepared':
                value['target']['sequence'] = True
            elif mode == 'child_ack_prepared':
                pid = os.fork()
                if pid == 0:
                    try:
                        raw = canonical(value)
                        if channel.connection.send(raw) != len(raw):
                            os._exit(71)
                        os._exit(0)
                    except BaseException:
                        os._exit(71)
                _, status_code = os.waitpid(pid, 0)
                if status_code != 0:
                    raise RuntimeError('CHILD_ACK_SEND_FAILED')
                return
        elif status == 'DISPATCHED' and mode == 'bad_dispatched':
            value['event_sha256'] = '0' * 64
        elif status == 'COMMITTED_STORAGE_ONLY':
            if mode == 'lost_committed':
                channel.connection.close()
                raise OSError('SYNTHETIC_LOST_COMMIT_ACK')
            if mode == 'bad_commit':
                value['authority'] = True
        result = original(channel, value)
        if status == 'PREPARED' and mode == 'timeout_after_prepared':
            time.sleep(5.5)
        if ((status == 'PREPARED' and mode == 'kill_after_prepared')
                or (status == 'DISPATCHED' and mode == 'kill_after_dispatched')):
            os.kill(os.getpid(), signal.SIGKILL)
        return result
    staged.Channel.send = injected
    return original


def owner(config):
    import httpx
    import host_core
    import staged_ipc as staged
    from anchor_v2 import V2Anchor
    from event_codec import Binding
    from event_store import EventReader
    from owned_host import Lease
    from protocol_v2 import location
    lease = reader = listener = connection = None
    original = staged.Channel.send
    try:
        lease = Lease(host_core, config['spec'])  # Before any private key read or SQL.
        writer_denied = denied(config['writer_key'])
        if not writer_denied:
            raise RuntimeError('OWNER_WRITER_KEY_ACCESS_NOT_DENIED')
        reader = EventReader(endpoint=config['endpoint'], region=config['region'], bucket=config['bucket'],
                             binding=Binding(**config['binding']), credential=read_key(config['reader_key']),
                             transport=httpx.MockTransport(lambda request: broker_rpc(config, request)))
        anchor = V2Anchor(config['spec']['state_file'], binding=Binding(**config['binding']),
                          location=location(reader), owner=lease)
        server = staged.Server(anchor, reader, config['writer_uid'], config['writer_gid'])
        owner_injection(staged, config['mode'])
        path = Path(config['owner_path'])
        listener = BoundListener(path, credentials=True)
        emit('OWNER_READY', pid=os.getpid())
        connection, _ = listener.socket.accept()
        before = fd_count()
        connection.settimeout(TIMEOUT)
        connection.setsockopt(socket.SOL_SOCKET, socket.SO_PASSCRED, 1)
        status = server.serve(connection)
        repeat_refused = False
        try:
            server.serve(connection)
        except staged.Refused:
            repeat_refused = True
        # lost_committed deliberately closed the accepted fd in its injection.
        closed = 1 if connection.fileno() == -1 else 0
        rights_fd_delta = fd_count() - before + closed
        # Fixture-only lifetime barrier: keep peer PID and Lease alive until the
        # root driver has collected the writer's receipt and closes our stdin.
        control = sys.stdin.readline()
        if control not in ('', 'STOP\n', 'STOP'):
            raise RuntimeError('OWNER_CONTROL_REFUSED')
    finally:
        staged.Channel.send = original
        if connection is not None:
            connection.close()
        if listener is not None:
            listener.close()
        if reader is not None:
            reader.close()
        held_before_close = False
        if lease is not None:
            lease.check()
            held_before_close = True
            lease.close()
    emit('OWNER_DONE', status=status, held_before_close=held_before_close,
         rights_fd_delta=rights_fd_delta, repeat_refused=repeat_refused, writer_denied=writer_denied)
    return 0


def denied(file):
    try:
        fd = os.open(file, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC)
    except PermissionError:
        return True
    else:
        os.close(fd)
        return False


def writer_injection(staged, mode):
    original = staged.Channel.send
    prepared = False
    def injected(channel, value):
        nonlocal prepared
        value = json.loads(canonical(value))
        if value.get('op') == 'prepare' and not prepared:
            prepared = True
            raw = None
            if mode == 'bad_format':
                value['format'] = 'SYNTHETIC_INVALID_FORMAT'
            elif mode == 'extra_prepare':
                value['extra'] = True
            elif mode == 'oversize_prepare':
                raw = b'x' * 4097
            elif mode == 'noncanonical_prepare':
                raw = canonical(value)[:-1]
            elif mode == 'duplicate_prepare':
                raw = b'{"format":' + json.dumps(value['format']).encode('ascii') + b',' + canonical(value)[1:]
            elif mode == 'rights_prepare':
                channel.check()
                fd = os.open('/dev/null', os.O_RDONLY | os.O_CLOEXEC)
                try:
                    body = canonical(value)
                    sent = channel.connection.sendmsg([body], [(socket.SOL_SOCKET, socket.SCM_RIGHTS, array.array('i', [fd]))])
                    if sent != len(body):
                        raise OSError('SYNTHETIC_RIGHTS_SEND_UNCERTAIN')
                finally:
                    os.close(fd)
                return
            if raw is not None:
                channel.check()
                if channel.connection.send(raw) != len(raw):
                    raise OSError('SYNTHETIC_RAW_SEND_UNCERTAIN')
                return
        elif value.get('op') == 'dispatch' and mode == 'wrong_dispatch':
            value['op'] = 'confirm'
        elif value.get('op') == 'confirm' and mode == 'wrong_confirm':
            value['token'] = '0' * 32
        return original(channel, value)
    staged.Channel.send = injected
    return original


def writer(config):
    import httpx
    import staged_ipc as staged
    from event_codec import Binding, Event, Head
    from event_store import EventWriter
    connection = socket.socket(socket.AF_UNIX, socket.SOCK_SEQPACKET)
    capability = None
    original = staged.Channel.send
    inherited_child = False
    try:
        connection.settimeout(TIMEOUT)
        connection.connect(config['owner_path'])  # Establish real peer identity before forks/probes.
        reader_denied = denied(config['reader_key'])
        db_denied = denied(config['spec']['state_file'])
        if not reader_denied or not db_denied:
            raise RuntimeError('WRITER_PRIVATE_ACCESS_NOT_DENIED')
        try:
            credential = read_key(config['writer_key'])
        except PermissionError:
            if config['mode'] != 'wrong_uid' or os.getuid() != 48004:
                raise
            emit('WRITER_DONE', outcome=dict(status='UNKNOWN', head=None, purge_or_restore_admitted=False), reader_denied=reader_denied,
                 db_denied=db_denied, repeat_refused=False)
            return 0
        binding = Binding(**config['binding'])
        if config['mode'] == 'wrong_binding':
            binding = Binding(binding.scope, str(uuid4()))
        capability = EventWriter(endpoint=config['endpoint'], region=config['region'], bucket=config['bucket'],
                                 binding=binding, credential=credential,
                                 transport=httpx.MockTransport(lambda request: broker_rpc(config, request)))
        client = staged.Client(capability, config['owner_uid'], config['owner_gid'])
        writer_injection(staged, config['mode'])
        item = Event(**config['event'])
        base = Head(**config['expected_head'])
        if config['mode'] == 'stale_base':
            base = Head(1, 'a' * 64)
        if config['mode'] == 'inherited_fd':
            pid = os.fork()
            if pid:
                _, status_code = os.waitpid(pid, 0)
                if status_code != 0:
                    raise RuntimeError('INHERITED_WRITER_CHILD_FAILED')
                return 0
            inherited_child = True
        result = client.append(connection, item, attempt_id=config['attempt'], expected_head=base)
        repeat_refused = False
        if config['mode'] == 'repeat_client' and result['status'] == 'COMMITTED_STORAGE_ONLY':
            try:
                client.append(connection, item, attempt_id=config['attempt'], expected_head=base)
            except staged.Refused:
                repeat_refused = True
        emit('WRITER_DONE', outcome=result, reader_denied=reader_denied,
             db_denied=db_denied, repeat_refused=repeat_refused)
    finally:
        staged.Channel.send = original
        connection.close()
        if capability is not None:
            capability.close()
    if inherited_child:
        os._exit(0)
    return 0


def main():
    if (len(sys.argv) != 4 or sys.platform != 'linux' or sys.implementation.name != 'cpython'
            or sys.version_info[:2] != (3, 12) or not sys.flags.isolated or not sys.flags.no_site
            or not sys.dont_write_bytecode or sys.flags.optimize
            or Path(sys.executable).resolve() != Path('/usr/bin/python3.12')):
        raise RuntimeError('FIXED_TEST_RUNTIME_REQUIRED')
    role, snapshot, case = sys.argv[1], Path(sys.argv[2]), Path(sys.argv[3])
    if role not in ('broker', 'owner', 'writer') or not snapshot.is_absolute() or not case.is_absolute():
        raise RuntimeError('FIXED_TEST_ARGUMENTS_REQUIRED')
    sys.addaudithook(no_network)
    os.environ.clear()
    os.environ.update(PATH='/usr/bin:/bin', LANG='C.UTF-8')
    sys.path[:0] = [str(snapshot / name) for name in ('candidate', 'codec', 'store', 'ownership', 'frozen', 'deps')]
    file = case / 'config.json'
    info = file.lstat()
    if (not stat.S_ISREG(info.st_mode) or info.st_uid != 0 or stat.S_IMODE(info.st_mode) != 0o644
            or info.st_nlink != 1 or not 0 < info.st_size <= RPC_LIMIT):
        raise RuntimeError('ROOT_FIXTURE_CONFIG_REQUIRED')
    config = json.loads(file.read_bytes())
    if ((config['writer_uid'], config['writer_gid'], config['owner_uid'], config['owner_gid'])
            != (48002, 48003, 47003, 47004)):
        raise RuntimeError('FIXED_FIXTURE_IDENTITIES_REQUIRED')
    expected = {'broker': (0, 0), 'owner': (47003, 47004), 'writer': (48002, 48003)}[role]
    if role == 'writer' and config['mode'] == 'wrong_uid':
        expected = (48004, 48003)
    if (os.getuid(), os.getgid()) != expected or os.getuid() != os.geteuid() or os.getgid() != os.getegid():
        raise RuntimeError('FIXTURE_ROLE_IDENTITY_REQUIRED')
    if role != 'broker' and os.getgroups():
        raise RuntimeError('EMPTY_SUPPLEMENTARY_GROUPS_REQUIRED')
    return {'broker': lambda: broker(case, config), 'owner': lambda: owner(config),
            'writer': lambda: writer(config)}[role]()


if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except SystemExit:
        raise
    except BaseException as error:
        emit('WORKER_FAILURE', type=type(error).__name__)
        raise SystemExit(70) from None
