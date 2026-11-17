"""Synthetic fixtures and own-process crash checkpoints; inert on import."""
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import sys
from contextlib import contextmanager
from unittest.mock import patch
from uuid import UUID

if __name__ == '__main__':
    if len(sys.argv) != 4:
        raise SystemExit(2)
    sys.path[:0] = [str(Path(sys.argv[1]) / name) for name in
                   ('candidate', 'codec', 'store', 'ownership', 'frozen', 'deps')]

import httpx
import anchor_v2
import host_core
import owned_host
from event_codec import Binding, Event, Head
from event_store import EventReader, EventWriter
from protocol_v2 import AnchoredEvents, V2Custodian, location

CODE_ROOT = Path(__file__).resolve().parent.parent
DATA_ROOT = CODE_ROOT / 'data'
UID, GID = 47003, 47004
BINDING = Binding('synthetic-v2-proof', '11111111-1111-4111-8111-111111111111')
ENDPOINT = 'https://s3.ru-3.storage.selcloud.ru'
BUCKET = 'fake-v2-proof-bucket'
LOCATION = ENDPOINT + '/' + BUCKET + '/pult/erasure-v2/' + BINDING.scope + '/' + BINDING.generation + '/events/'
READER_KEY = {'access_key': 'FAKE-READER-V2', 'secret_key': 'invented-reader-only-material'}
WRITER_KEY = {'access_key': 'FAKE-WRITER-V2', 'secret_key': 'invented-writer-only-material'}
RAW_CONNECT = sqlite3.connect
RETAINED = []
CRASH_STATES = {
    'before-commit-1': (None, 0, 0), 'after-commit-1': ('PREPARED', 1, 0),
    'before-commit-2': ('PREPARED', 1, 0), 'after-commit-2': ('DISPATCHED', 2, 0),
    'before-commit-3': ('DISPATCHED', 2, 1), 'after-commit-3': ('COMMITTED', 3, 1),
    'before-put': ('DISPATCHED', 2, 0),
    'after-put-before-ack': ('DISPATCHED', 2, 1), 'before-get': ('DISPATCHED', 2, 1),
}


def identity(number):
    return str(UUID(int=number + 1, version=4))


def event(number=0, phase='intent'):
    return Event(identity(10000 + number), identity(20000 + number), phase)


ATTEMPT = identity(30000)


def reference(previous=None, item=None, binding=BINDING):
    def encode(value):
        return (json.dumps(value, sort_keys=True, separators=(',', ':'),
                           ensure_ascii=True, allow_nan=False) + '\n').encode('ascii')
    if previous is None:
        return Head(0, hashlib.sha256(encode({'format': 'pult-erasure-event-v2',
                    'kind': 'genesis', 'scope': binding.scope,
                    'generation': binding.generation})).hexdigest())
    value = {'format': 'pult-erasure-event-v2', 'scope': binding.scope,
             'generation': binding.generation, 'sequence': previous.sequence + 1,
             'previous': previous.digest, 'event': {'purge_id': item.purge_id,
             'subject_id': item.subject_id, 'phase': item.phase}}
    value['digest'] = hashlib.sha256(encode(value)).hexdigest()
    return encode(value), Head(value['sequence'], value['digest'])


def persist(path, body):
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    try:
        remaining = memoryview(body)
        while remaining:
            written = os.write(fd, remaining)
            if not written:
                raise OSError('synthetic persistence failed')
            remaining = remaining[written:]
        os.fsync(fd)
    finally:
        os.close(fd)
    fd = os.open(Path(path).parent, os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


class HandlerAbort(BaseException):
    pass


class FakeObjects:
    def __init__(self, directory, checkpoint=lambda stage: None):
        self.directory, self.checkpoint = Path(directory), checkpoint
        self.mode, self.calls = 'normal', []

    def contents(self):
        return {p.name: p.read_bytes() for p in sorted(self.directory.iterdir())}

    def handle(self, role, request):
        method = 'GET' if role == 'reader' else 'PUT'
        key = READER_KEY if role == 'reader' else WRITER_KEY
        prefix = LOCATION.removeprefix(ENDPOINT)
        suffix = request.url.path.removeprefix(prefix)
        if (request.method != method or str(request.url).split('?', 1)[0] != ENDPOINT + request.url.path
                or request.url.query or not request.url.path.startswith(prefix)
                or len(suffix) != 25 or not suffix.endswith('.json') or not suffix[:20].isdigit()
                or 'Credential=' + key['access_key'] + '/' not in request.headers.get('authorization', '')):
            raise AssertionError('synthetic capability or exact-key mismatch')
        self.calls.append((method, int(suffix[:20])))
        path = self.directory / suffix
        if method == 'PUT':
            if request.headers.get('if-none-match') != '*' or 'if-match' in request.headers:
                raise AssertionError('synthetic create-only header missing')
            self.checkpoint('before-put')
            if self.mode in ('write_412', 'write_500'):
                return httpx.Response(412 if self.mode == 'write_412' else 500)
            try:
                persist(path, request.content)
            except FileExistsError:
                return httpx.Response(412)
            self.checkpoint('after-put-before-ack')
            if self.mode == 'write_unknown':
                raise httpx.ReadError('synthetic lost response')
            if self.mode == 'abort_put':
                raise HandlerAbort()
            return httpx.Response(200)
        self.checkpoint('before-get')
        if request.content:
            raise AssertionError('synthetic GET body forbidden')
        if self.mode == 'read_fail':
            raise httpx.ReadError('synthetic unavailable read')
        if self.mode == 'abort_get':
            raise HandlerAbort()
        if not path.exists():
            return httpx.Response(404)
        body = path.read_bytes()
        return httpx.Response(200, content=body + b' ' if self.mode == 'tamper' else body)


class Fixture:
    def __init__(self, directory, checkpoint=lambda stage: None):
        self.root = Path(directory)
        self.db = self.root / 'private-state' / 'anchor.sqlite'
        self.lock = self.db.parent / 'owner.lock'
        self.spec = json.loads((self.root / 'fixture.json').read_bytes())
        self.objects = FakeObjects(self.root / 'objects', checkpoint)
        self.lease = self.anchor = self.reader = self.writer = None
        self.custodian = self.runner = None
        self.close_failed = False

    @classmethod
    def bootstrap(cls, directory):
        root = Path(directory)
        root.mkdir(mode=0o700, parents=False, exist_ok=True)
        root.chmod(0o700)
        state = root / 'private-state'
        state.mkdir(mode=0o700)
        (root / 'objects').mkdir(mode=0o700)
        db, lock = state / 'anchor.sqlite', state / 'owner.lock'
        persist(db, b'')
        with RAW_CONNECT(db, isolation_level=None) as connection:
            connection.execute('PRAGMA synchronous=EXTRA')
            connection.execute('BEGIN IMMEDIATE')
            connection.execute(anchor_v2.META_SQL)
            connection.execute(anchor_v2.RECEIPTS_SQL)
            connection.execute('PRAGMA application_id=' + str(anchor_v2.APP_ID))
            connection.execute('PRAGMA user_version=' + str(anchor_v2.SCHEMA))
            connection.execute('INSERT INTO anchor_v2 VALUES(1,?,?,?,?,?,?,NULL)',
                (BINDING.scope, BINDING.generation, LOCATION, 0, reference().digest, 0))
            connection.execute('COMMIT')
        connection.close()
        db.chmod(0o600)
        persist(lock, b'')
        def inode(path):
            info = path.stat()
            return {'device': info.st_dev, 'inode': info.st_ino}
        spec = {'state_file': str(db), 'state_identity': inode(db),
                'lease_file': str(lock), 'lease_identity': inode(lock),
                'custodian_uid': UID, 'socket_gid': GID}
        persist(root / 'fixture.json', json.dumps(spec, sort_keys=True).encode('ascii'))
        return cls(root)

    def capability(self, kind, **overrides):
        role = 'reader' if kind is EventReader else 'writer'
        args = dict(endpoint=ENDPOINT, region='ru-3', bucket=BUCKET, binding=BINDING,
                    credential=dict(READER_KEY if role == 'reader' else WRITER_KEY),
                    transport=httpx.MockTransport(lambda request: self.objects.handle(role, request)))
        args.update(overrides)
        return kind(**args)

    def open(self):
        if self.close_failed or self.lease is not None:
            raise RuntimeError('synthetic fixture cannot reopen active ownership')
        self.lease = owned_host.Lease(host_core, self.spec)
        try:
            self.anchor = anchor_v2.V2Anchor(self.db, binding=BINDING, location=LOCATION, owner=self.lease)
            self.reader = self.capability(EventReader)
            self.writer = self.capability(EventWriter)
            self.custodian = V2Custodian(self.anchor, self.reader)
            self.runner = AnchoredEvents(self.custodian, self.writer)
            return self
        except BaseException:
            self.close()
            raise

    def close(self):
        if self.close_failed:
            raise RuntimeError('synthetic cleanup uncertain; process exit required')
        if self.lease is None:
            return
        try:
            if self.reader is not None:
                self.reader.close()
            if self.writer is not None:
                self.writer.close()
            if self.lease.fd is not None:
                self.lease.check()
            self.lease.close()
        except BaseException:
            self.close_failed = True
            RETAINED.append(self)
            raise RuntimeError('synthetic cleanup uncertain; process exit required') from None
        self.lease = None

    def rows(self):
        connection = RAW_CONNECT(self.db)
        try:
            return (connection.execute('SELECT seq,hash,revision,pending FROM anchor_v2').fetchone(),
                    connection.execute('SELECT * FROM attempts_v2 ORDER BY target_seq').fetchall())
        finally:
            connection.close()

    def sql(self, statement, parameters=()):
        connection = RAW_CONNECT(self.db)
        try:
            connection.execute(statement, parameters)
            connection.commit()
        finally:
            connection.close()


class ConnectionProxy:
    def __init__(self, connection, control):
        self.connection, self.control = connection, control

    @property
    def row_factory(self):
        return self.connection.row_factory

    @row_factory.setter
    def row_factory(self, value):
        self.connection.row_factory = value

    def __getattr__(self, name):
        return getattr(self.connection, name)

    def execute(self, sql, parameters=()):
        if sql.strip().upper() != 'COMMIT':
            return self.connection.execute(sql, parameters)
        self.control.count += 1
        self.control.hit('before-commit-' + str(self.control.count))
        result = self.connection.execute(sql, parameters)
        self.control.hit('after-commit-' + str(self.control.count))
        return result


class CommitControl:
    def __init__(self, checkpoint=lambda stage: None, fault=None):
        self.checkpoint, self.fault, self.count, self.hits = checkpoint, fault, 0, []

    def hit(self, stage):
        self.hits.append(stage)
        self.checkpoint(stage)
        if stage == self.fault:
            raise sqlite3.OperationalError('synthetic commit response fault')

    @contextmanager
    def installed(self):
        def connect(*args, **kwargs):
            return ConnectionProxy(RAW_CONNECT(*args, **kwargs), self)
        with patch.object(sqlite3, 'connect', connect):
            yield self


def reopen(directory, stage):
    fixture = Fixture(directory).open()
    try:
        phase, revision, objects = CRASH_STATES[stage]
        before, blobs = fixture.rows(), fixture.objects.contents()
        meta, rows = before
        committed = phase == 'COMMITTED'
        assert meta[0] == int(committed) and meta[2] == revision
        assert len(rows) == int(phase is not None) and len(blobs) == objects
        assert not rows or rows[0][-1] == phase
        observed = fixture.custodian.inspect(ATTEMPT)
        expected = ('OBSERVED_COMMITTED_RECEIPT' if committed else
                    'PENDING_REQUIRES_REVIEW' if phase else 'NO_SUCH_RECEIPT')
        assert observed.status == expected and observed.purge_or_restore_admitted is False
        assert not fixture.objects.calls
        if phase is None:
            result = fixture.runner.append(event(), attempt_id=identity(30001))
            assert result.status == 'ANCHORED_STORAGE_ONLY'
            assert result.purge_or_restore_admitted is False
        else:
            for attempt in (ATTEMPT, identity(30001)):
                try:
                    fixture.runner.append(event(), attempt_id=attempt)
                except anchor_v2.AnchorError:
                    pass
                else:
                    raise AssertionError('synthetic replay unexpectedly admitted')
            assert fixture.rows() == before and fixture.objects.contents() == blobs
            assert not fixture.objects.calls and not fixture.custodian._active
        return {'ok': True, 'status': expected, 'explicit_new_attempt': phase is None}
    finally:
        fixture.close()


def main():
    directory, stage = Path(sys.argv[2]), sys.argv[3]
    def checkpoint(current):
        if current == stage:
            print('CHECKPOINT:' + current, flush=True)
            sys.stdin.buffer.read(1)
            raise RuntimeError('synthetic checkpoint released without kill')
    with patch('socket.socket', side_effect=AssertionError('synthetic socket forbidden')), \
         patch('socket.create_connection', side_effect=AssertionError('synthetic socket forbidden')), \
         patch('socket.getaddrinfo', side_effect=AssertionError('synthetic DNS forbidden')):
        if stage.startswith('reopen-'):
            print(json.dumps(reopen(directory, stage.removeprefix('reopen-')), sort_keys=True), flush=True)
            return 0
        if stage not in CRASH_STATES:
            return 2
        fixture = Fixture(directory, checkpoint).open()
        try:
            with CommitControl(checkpoint).installed():
                fixture.runner.append(event(), attempt_id=ATTEMPT)
        finally:
            fixture.close()
    return 3


if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except Exception:
        print('SYNTHETIC_WORKER_REFUSED', file=sys.stderr, flush=True)
        raise SystemExit(1) from None
