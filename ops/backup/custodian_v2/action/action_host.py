"""New one-action owner over the frozen public source/Lease primitives.

Only the fixed, root-pinned child calls this module. Its ``bridge`` closure is
trusted embedding, NEVER an RPC/profile-selected transport or a live fallback.
No Constructed/ColdReady object is created, inspected, or promoted here.
"""
import hashlib
import os
import re
import sys
import threading
from types import ModuleType

import pult_action_profile_v3 as common
from pult_action_profile_v3 import Profile

# The bootstrap registers this one common module for the process. Keeping the
# attempt latch and private failures there also survives recapture of this file.
_RETAINED = common.__dict__.setdefault('_action_host_retained', [])


class Refused(Exception):
    pass


def _profile_guard(profile, role):
    if (type(common) is not ModuleType or sys.modules.get('pult_action_profile_v3') is not common
            or common.Profile is not Profile or type(profile) is not Profile
            or type(role) is not str or role not in ('custodian', 'writer')):
        raise Refused('exact trusted profile and fixed role required')
    profile.actor_guard(role)


def _registrations(constructor):
    return (tuple(sys.meta_path), {name: module for name, module in tuple(sys.modules.items())
        if name.split('.', 1)[0] in constructor.PAYLOAD})


def _retain(owner, error, registrations=None):
    """Root opaque objects/tracebacks through exit; never render their contents."""
    if not owner._failed:
        _RETAINED.append(owner)
    owner._failed = True
    if not any(saved is error for saved in owner._failures):
        owner._failures.append(error)
    if registrations is not None:
        owner._registrations = registrations
        try:
            meta, modules = registrations
            if any(name in sys.modules and sys.modules[name] is not module for name, module in modules.items()):
                raise Refused('foreign import registration during cleanup')
            for name, module in modules.items():
                sys.modules[name] = module
            sys.meta_path[:] = meta
        except BaseException as restore_error:
            owner._restore_error = restore_error


class Payload:
    """Closed captured sources, not a state/credential or write capability.

    The writer owns its own credential, EventWriter, and cleanup. It must close
    that capability successfully before closing this source capsule.
    """
    def __init__(self, profile, role, constructor, file, raw):
        self.profile, self.role, self.constructor = profile, role, constructor
        self.file, self.raw = file, raw
        self.capsule = self.protocol = self.intake = None
        self._pid, self._thread = os.getpid(), threading.current_thread()
        self._closed = self._failed = False
        self._failures = []
        self._registrations = self._restore_error = None

    def __repr__(self):
        return '<ActionPayload sources only; no purge or restore>'

    @property
    def value(self):
        # Do not expose the owner's mutable construction snapshot.
        return self.constructor.document(self.raw)

    def check(self):
        try:
            if (self._closed or self._failed or os.getpid() != self._pid
                    or threading.current_thread() is not self._thread):
                raise Refused('source owner unavailable')
            _profile_guard(self.profile, self.role)
            if (self.profile.modules.get('constructor') is not self.constructor
                    or self.constructor.root_bytes(self.file, 8192) != self.raw
                    or self.protocol is not None and self.profile.modules.get('protocol') is not self.protocol):
                raise Refused('captured source identity changed')
            if self.capsule is not None:
                self.capsule.check()
            return True
        except BaseException as error:
            _retain(self, error)
            if isinstance(error, (KeyboardInterrupt, SystemExit)):
                raise
            raise Refused('source lifetime refused; process exit required') from None

    def close(self):
        if self._closed:
            return
        if self._failed:
            raise Refused('source cleanup uncertain; process exit required')
        registrations = None
        try:
            self.check()
            registrations = _registrations(self.constructor)
            self.capsule.close()
        except BaseException as error:
            _retain(self, error, registrations)
            if isinstance(error, (KeyboardInterrupt, SystemExit)):
                raise
            raise Refused('source cleanup uncertain; process exit required') from None
        self._closed = True


def capture_payload(profile: Profile, role):
    """Activate the same pinned intake, without opening SQL or either key.

    The public constructor.profile is custodian-only: the writer must not spoof
    that principal merely to parse a document. The common Profile has already
    bound the nested uid/gid pairs; the pinned intake validates its own closure.
    """
    payload = None
    try:
        if common.__dict__.get('_action_host_attempted', False):
            raise Refused('one source/host attempt per process')
        common.__dict__['_action_host_attempted'] = True
        _profile_guard(profile, role)
        constructor = profile.capture('constructor')
        if type(constructor) is not ModuleType:
            raise Refused('exact pinned constructor helper required')
        constructor.runtime()
        spec = profile.spec['custodian']
        file = constructor.path(spec['host_profile'])
        raw = constructor.root_bytes(file, 8192)
        if hashlib.sha256(raw).hexdigest() != spec['host_profile_sha256']:
            raise Refused('nested construction approval mismatch')
        payload = Payload(profile, role, constructor, file, raw)
        value = constructor.profile(raw, file) if role == 'custodian' else constructor.document(raw)
        if (set(value) != constructor.FIELDS or value['format'] != constructor.FORMAT
                or value['mode'] != constructor.MODE):
            raise Refused('exact nested construction profile required')
        source = value['source_intake']
        if (type(source) is not dict or set(source) != {'entry', 'profile_file', 'profile_sha256'}
                or type(source['profile_sha256']) is not str
                or not re.fullmatch('[0-9a-f]{64}', source['profile_sha256'])):
            raise Refused('exact source intake approval required')
        entry_file = constructor.path(source['entry'])
        entry_raw = constructor.root_bytes(entry_file, 100000)
        if hashlib.sha256(entry_raw).hexdigest() != constructor.INTAKE_SHA:
            raise Refused('fixed source intake required')
        payload.intake = ModuleType('_pult_action_fixed_source_intake')
        payload.intake.__file__ = str(entry_file)
        exec(compile(entry_raw, str(entry_file), 'exec', dont_inherit=True, optimize=0), payload.intake.__dict__)
        payload.check()
        payload.capsule = payload.intake.capture(source['profile_file'], source['profile_sha256'])
        payload.capsule.activate()
        # This source imports payload modules; capture it ONLY after activation.
        payload.protocol = profile.capture('protocol')
        if type(payload.protocol) is not ModuleType:
            raise Refused('exact action protocol required')
        payload.check()
        return payload
    except BaseException as error:
        if payload is not None:
            _retain(payload, error)
        else:
            _RETAINED.append(error)
        # No attempted cleanup of an opaque/partial constructor. Its original
        # traceback can own resources that never returned to this caller.
        if isinstance(error, (KeyboardInterrupt, SystemExit)):
            raise
        raise Refused('action source construction refused; process exit required') from None


class OwnedHost:
    """Actual new Lease/anchor/reader/server ownership for exactly one action."""
    def __init__(self, payload, bridge):
        self._payload, self._bridge = payload, bridge
        self._value = payload.value
        self._owner = self._anchor = self._reader = self._server = self._operation = None
        self._credential_stat = None
        self._pid, self._thread = os.getpid(), threading.current_thread()
        self._closed = self._failed = self._ready = self._used = self._serving = False
        self._failures = []
        self._registrations = self._restore_error = None

    def __repr__(self):
        return '<SingleActionOwnedHost no purge or restore; no retry>'

    def check(self):
        """Transaction-safe lifetime callback: no SQLite open or sidecar probe."""
        try:
            if (self._closed or self._failed or os.getpid() != self._pid
                    or threading.current_thread() is not self._thread):
                raise Refused('action owner unavailable')
            self._payload.check()
            if self._owner is not None:
                self._owner.check()
            if self._credential_stat is not None:
                helper = self._payload.constructor
                secret = helper.path(self._value['reader_credential_file'])
                helper.ancestors(secret, {0, self._value['custodian_uid']})
                if helper.fingerprint(secret.lstat()) != self._credential_stat:
                    raise Refused('reader credential identity drift')
            return True
        except BaseException as error:
            _retain(self, error)
            if isinstance(error, (KeyboardInterrupt, SystemExit)):
                raise
            raise Refused('action lifetime refused; process exit required') from None

    def _boundary(self):
        # A legitimate transaction may temporarily create a rollback journal.
        # This guard is never installed as the server/anchor lifetime callback.
        self.check()
        if self._serving or self._owner is None:
            raise Refused('quiescent owned state required')
        self._payload.constructor.no_recovery_state(self._value)
        self.check()

    def serve_one(self, connection):
        try:
            if not self._ready or self._used:
                raise Refused('one prepared host action only')
            self._used = True  # consume even a preflight failure; never retry
            self._boundary()
            self._serving = True
            outcome = self._server.serve(connection)
            self._serving = False
            if type(outcome) is not str or outcome != 'COMMITTED_STORAGE_ONLY':
                # Server deliberately returns no private exception. This local
                # traceback still roots the entire owned graph and connection.
                raise Refused('one action outcome uncertain')
            # The server's final pre-ACK lifetime check is its linearization.
            # Close/whole-group cleanup are separate gates, not a retraction of
            # a committed-storage fact already visible to the authenticated peer.
            return outcome
        except BaseException as error:
            _retain(self, error)
            if isinstance(error, (KeyboardInterrupt, SystemExit)):
                raise
            return 'UNKNOWN_RETAIN_DURABLE_STATE'

    def close(self):
        if self._closed:
            return
        if self._failed or self._serving:
            raise Refused('cleanup uncertain; process exit required')
        registrations = None
        try:
            # Ownership spans the reader's complete cleanup and capsule unload.
            self.check()
            if self._reader is not None:
                self._reader.close()
            self._boundary()
            registrations = _registrations(self._payload.constructor)
            self._payload.close()
            self._owner.check()
            self._owner.close()
        except BaseException as error:
            _retain(self, error, registrations)
            if isinstance(error, (KeyboardInterrupt, SystemExit)):
                raise
            raise Refused('cleanup uncertain; process exit required') from None
        self._closed = True


def prepare(profile: Profile, bridge) -> OwnedHost:
    """Trusted child wiring only; acquire the actual Lease BEFORE SQL/key."""
    payload = host = None
    try:
        payload = capture_payload(profile, 'custodian')
        host = OwnedHost(payload, bridge)
        if not callable(bridge):
            raise Refused('fixed trusted child bridge required')
        import anchor_v2, event_codec, event_store, owned_host, host_core, httpx
        helper, protocol, value = payload.constructor, payload.protocol, host._value
        if (anchor_v2.MAX_RECEIPTS != helper.BOUNDS['max_receipts']
                or event_codec.MAX_RECORD_BYTES != helper.BOUNDS['record_bytes']
                or protocol.V2Anchor is not anchor_v2.V2Anchor
                or protocol.EventReader is not event_store.EventReader):
            raise Refused('fixed public payload identities or bounds changed')
        host.check()
        spec = {key: value[key] for key in ('state_file', 'state_identity', 'lease_file',
                                           'lease_identity', 'custodian_uid', 'socket_gid')}
        for name in ('state_file', 'lease_file'):
            file = helper.path(value[name])
            helper.ancestors(file, {0, value['custodian_uid']})
            if any(n.startswith('system.posix_acl_') for n in os.listxattr(file, follow_symlinks=False)):
                raise Refused('private state and Lease without ACL required')
        host._owner = owned_host.Lease(host_core, spec)
        host._boundary()
        host._operation = protocol.Operation(profile.operation_bytes())
        approved = host._operation.value()
        binding = event_codec.Binding(value['scope'], value['generation'])
        location = approved['binding']['location']
        host._anchor = anchor_v2.V2Anchor(value['state_file'], binding=binding,
                                         location=location, owner=host._owner)
        state = host._anchor.load()
        host._boundary()
        if state.head != protocol.previous.head(approved['base']) or state.pending is not None:
            raise Refused('expected base and no pending attempt required before key')
        secret, host._credential_stat = helper.credential(host_core, value)
        host.check()

        def mock_only(request):
            host.check()
            response = host._bridge(request)
            host.check()
            return response

        # No default/live client exists. On any opaque constructor exception the
        # original traceback and actual Lease/capsule are retained through exit.
        host._reader = event_store.EventReader(**value['s3'], binding=binding, credential=secret,
                                               transport=httpx.MockTransport(mock_only))
        host.check()
        host._server = protocol.Server(host._anchor, host._reader, host._operation, lifetime=host.check)
        host._ready = True
        host.check()
        return host
    except BaseException as error:
        if host is not None:
            _retain(host, error)
        elif payload is not None:
            _retain(payload, error)
        else:
            _RETAINED.append(error)
        if isinstance(error, (KeyboardInterrupt, SystemExit)):
            raise
        raise Refused('new owned host construction refused; process exit required') from None
