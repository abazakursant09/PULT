"""One root-approved synthetic storage action over the pinned authenticated channel.

The embedding trusted host owns source/profile/lifetime/Lease/credentials. This
library is not a cold host, listener, live S3 client, purge or restore permit.
"""
import hashlib
import json
import re
from uuid import UUID, uuid4

from anchor_v2 import V2Anchor
from event_codec import Binding, Event, Head, canonical, encode_candidate
from pult_synthetic_store_v1 import EventReader, EventWriter
from pult_synthetic_durable_v1 import V2Custodian, location
import staged_ipc as previous

FORMAT = 'pult-root-bound-erasure-action-ssh-s3-v1'
PROFILE = 'pult-single-synthetic-action-ssh-s3-v1'
FALSE_AUTHORITY = {'purge': False, 'restore': False}
FIELDS = {'format', 'approval', 'operation_id', 'controller', 'binding', 'attempt',
          'event', 'base', 'custodian', 'writer'}


class Refused(Exception):
    pass


class Unknown(Exception):
    pass


def _uuid(value):
    if type(value) is not str:
        raise Refused('canonical UUID4 required')
    try:
        parsed = UUID(value)
    except ValueError:
        raise Refused('canonical UUID4 required') from None
    if parsed.version != 4 or str(parsed) != value:
        raise Refused('canonical UUID4 required')
    return value


def _document(raw):
    if type(raw) is not bytes or not 0 < len(raw) <= previous.MAX_FRAME:
        raise Refused('bounded approved operation required')
    def pairs(rows):
        value = {}
        for key, item in rows:
            if key in value:
                raise Refused('duplicate operation field')
            value[key] = item
        return value
    try:
        value = json.loads(raw, object_pairs_hook=pairs)
        if type(value) is not dict or canonical(value) != raw:
            raise Refused('exact canonical operation required')
        return value
    except (ValueError, TypeError, UnicodeError, RecursionError):
        raise Refused('approved operation refused') from None


class Operation:
    """Immutable canonical descriptor supplied ONLY by the trusted host profile."""
    __slots__ = ('_raw', '_sha')

    def __init__(self, raw):
        self._raw = raw
        self._sha = hashlib.sha256(raw).hexdigest() if type(raw) is bytes else None
        self.value()

    def __repr__(self):
        return '<OneSyntheticAction no purge or restore>'

    def value(self):
        try:
            return self._value()
        except Refused:
            raise
        except Exception:
            raise Refused('approved operation refused') from None

    def _value(self):
        if type(self._raw) is not bytes or hashlib.sha256(self._raw).hexdigest() != self._sha:
            raise Refused('operation identity changed')
        value = _document(self._raw)
        if set(value) != FIELDS or value['format'] != PROFILE:
            raise Refused('distinct synthetic SSH/S3 action profile required')
        if type(value['approval']) is not str or not re.fullmatch('[0-9a-f]{64}', value['approval']):
            raise Refused('independent root profile SHA required')
        _uuid(value['operation_id']); _uuid(value['attempt'])
        controller = value['controller']
        if (type(controller) is not dict or set(controller) != {'unit', 'invocation_id', 'boot', 'pid', 'start_ticks'}
                or type(controller['unit']) is not str or not re.fullmatch('pult-[a-z0-9-]{1,100}\\.service', controller['unit'])
                or type(controller['invocation_id']) is not str or not re.fullmatch('[0-9a-f]{32}', controller['invocation_id'])
                or any(type(controller[k]) is not int or controller[k] <= 0 for k in ('pid', 'start_ticks'))):
            raise Refused('exact original controller identity required')
        _uuid(controller['boot'])
        for role in ('custodian', 'writer'):
            if (type(value[role]) is not dict or set(value[role]) != {'uid', 'gid'}
                    or any(type(v) is not int or not 1000 <= v < 2**31 for v in value[role].values())):
                raise Refused('fixed separate principals required')
        if value['custodian']['uid'] == value['writer']['uid']:
            raise Refused('separate writer and custodian required')
        bind = value['binding']
        if type(bind) is not dict or set(bind) != {'scope', 'generation', 'location'}:
            raise Refused('exact storage binding required')
        typed = Binding(bind['scope'], bind['generation'])
        prefix = r'https://s3[.]ru-3[.]storage[.]selcloud[.]ru/[a-z0-9][a-z0-9-]{1,61}[a-z0-9]'
        suffix = '/pult/erasure-v2/' + typed.scope + '/' + typed.generation + '/events/'
        if type(bind['location']) is not str or not re.fullmatch(prefix + re.escape(suffix), bind['location']):
            raise Refused('fixed ru3 synthetic location required')
        if type(value['event']) is not dict or set(value['event']) != {'purge_id', 'subject_id', 'phase'}:
            raise Refused('exact artificial event required')
        event = Event(**value['event'])
        if event.phase != 'intent':
            raise Refused('one artificial intent only')
        base = previous.head(value['base'])
        encode_candidate(typed, base, event)
        return value

    def context(self):
        value = self.value()
        binding = value['binding']
        _, target = encode_candidate(Binding(binding['scope'], binding['generation']),
                                      previous.head(value['base']), Event(**value['event']))
        return dict(format=FORMAT, approval=value['approval'], operation_id=value['operation_id'],
            controller=value['controller'], binding=binding, attempt=value['attempt'], event=value['event'],
            base=value['base'], target=previous.head_wire(target), custodian=value['custodian'], writer=value['writer'])

    def frame(self, **extra):
        context = self.context()
        if set(extra).intersection(context):
            raise Refused('root action context cannot be overridden')
        result = dict(context, **extra)
        if len(canonical(result)) > previous.MAX_FRAME:
            raise Refused('action frame bound')
        return result


def _lifetime(check):
    if not callable(check):
        raise Refused('trusted lifetime verifier required')
    check()


class Server:
    """Exactly one authorized event/attempt, not arbitrary writer-role requests."""
    def __init__(self, anchor, reader, operation, lifetime):
        previous.runtime()
        if type(anchor) is not V2Anchor or type(reader) is not EventReader or type(operation) is not Operation:
            raise Refused('exact already-owned capabilities required')
        self._operation, self._lifetime = operation, lifetime
        expected = operation.value()
        if (previous.binding(anchor) != expected['binding'] or location(reader) != expected['binding']['location']):
            raise Refused('approved storage location mismatch')
        _lifetime(lifetime)
        self._anchor, self._custodian = anchor, V2Custodian(anchor, reader)
        self._used = False

    def serve(self, connection):
        previous.runtime()
        if self._used:
            raise Refused('one server action only')
        self._used = True
        prepared = claimed = channel = None
        try:
            op = self._operation
            expected = op.value()
            _lifetime(self._lifetime)
            channel = previous.Channel(connection, **expected['writer'])
            request = channel.receive()
            _lifetime(self._lifetime)
            if not previous.exact(request, op.frame(op='prepare')):
                raise Refused('exact root-approved action required')
            initial = self._anchor.load()
            base = previous.head(expected['base'])
            if initial.head != base or initial.pending is not None:
                raise Refused('expected head or pending changed')
            prepared = self._custodian.prepare(Event(**expected['event']), attempt_id=expected['attempt'])
            state, phase = self._anchor.inspect_attempt(expected['attempt'])
            if phase != 'PREPARED' or state.head != base or state.pending != expected['attempt'] or state.revision != initial.revision + 1:
                raise Unknown('preparation state uncertain')
            token = uuid4().hex
            _lifetime(self._lifetime)
            channel.send(op.frame(status='PREPARED', token=token, authority=dict(FALSE_AUTHORITY)))
            if not previous.exact(channel.receive(), op.frame(op='dispatch', token=token)):
                raise Refused('exact dispatch required')
            _lifetime(self._lifetime)
            claimed = self._custodian.claim(prepared)
            state, phase = self._anchor.inspect_attempt(expected['attempt'])
            if phase != 'DISPATCHED' or state.head != base or state.pending != expected['attempt'] or state.revision != initial.revision + 2:
                raise Unknown('dispatch state uncertain')
            token = uuid4().hex
            _lifetime(self._lifetime)
            channel.send(op.frame(status='DISPATCHED', token=token, authority=dict(FALSE_AUTHORITY)))
            if not previous.exact(channel.receive(), op.frame(op='confirm', token=token)):
                raise Refused('exact confirmation required')
            _lifetime(self._lifetime)
            state = self._custodian.confirm(claimed)
            if (previous.head_wire(state.head) != op.context()['target'] or state.pending is not None
                    or state.revision != initial.revision + 3):
                raise Unknown('confirmed head uncertain')
            _lifetime(self._lifetime)
            channel.send(op.frame(status='COMMITTED_STORAGE_ONLY', authority=dict(FALSE_AUTHORITY)))
            # Linearization is the final pre-ACK check above. A subsequent
            # lifetime check cannot retract a peer-visible committed-storage
            # fact. Host close/whole-group cleanup remain separate outer gates.
            return 'COMMITTED_STORAGE_ONLY'
        except (Exception, KeyboardInterrupt, SystemExit):
            return 'UNKNOWN_RETAIN_DURABLE_STATE'
        finally:
            if prepared is not None: self._custodian.abandon(prepared)
            if claimed is not None: self._custodian.abandon(claimed)
            if channel is not None: channel.close()


class Client:
    def __init__(self, writer, operation, lifetime):
        previous.runtime()
        if type(writer) is not EventWriter or type(operation) is not Operation:
            raise Refused('exact writer capability and approved action required')
        value = operation.value()
        if location(writer) != value['binding']['location'] or writer._binding != Binding(value['binding']['scope'], value['binding']['generation']):
            raise Refused('approved writer binding mismatch')
        _lifetime(lifetime)
        self._writer, self._operation, self._lifetime = writer, operation, lifetime
        self._used = False

    def append(self, connection):
        previous.runtime()
        if self._used:
            raise Refused('one client action only')
        self._used = True
        channel = None
        try:
            op = self._operation
            value = op.value()
            _lifetime(self._lifetime)
            channel = previous.Channel(connection, **value['custodian'])
            channel.send(op.frame(op='prepare'))
            ack = channel.receive(); token = previous.token(ack.get('token'))
            if not previous.exact(ack, op.frame(status='PREPARED', token=token, authority=dict(FALSE_AUTHORITY))):
                raise Refused('preparation acknowledgement refused')
            _lifetime(self._lifetime)
            channel.send(op.frame(op='dispatch', token=token))
            ack = channel.receive(); token = previous.token(ack.get('token'))
            if not previous.exact(ack, op.frame(status='DISPATCHED', token=token, authority=dict(FALSE_AUTHORITY))):
                raise Refused('dispatch acknowledgement refused')
            _lifetime(self._lifetime)
            wire = self._writer.create(previous.head(value['base']), Event(**value['event']))
            if type(wire) is not str or wire != 'PUT_HTTP200_UNCONFIRMED':
                raise Unknown('PUT outcome uncertain')
            _lifetime(self._lifetime)
            channel.send(op.frame(op='confirm', token=token))
            if not previous.exact(channel.receive(), op.frame(status='COMMITTED_STORAGE_ONLY', authority=dict(FALSE_AUTHORITY))):
                raise Refused('independent commit acknowledgement refused')
            _lifetime(self._lifetime)
            return dict(status='COMMITTED_STORAGE_ONLY', head=op.context()['target'], purge_or_restore_admitted=False)
        except (Exception, KeyboardInterrupt, SystemExit):
            return dict(status='UNKNOWN', head=None, purge_or_restore_admitted=False)
        finally:
            if channel is not None: channel.close()
