"""Portable v3 action UNIT/MOCKED contracts, NOT native IPC or durable proof.

Run here with ``python -B -m unittest -v test_action_protocol``.  The real
base82d89f9 event_codec and staged pure schema helpers are imported in an
isolated module-registration context.  Exact capability types, Custodian,
runtime admission and Channel are test doubles: no sockets, subprocesses,
services, credentials, object storage or databases are opened.  In-memory
phase/revision assertions are orchestration checks, never durable receipts.
"""

from collections import deque
from contextlib import contextmanager
from copy import deepcopy
import hashlib
import importlib.util
import json
from pathlib import Path
import sys
from types import ModuleType, SimpleNamespace
import unittest
from unittest.mock import patch


BASE = Path(__file__).resolve().parents[2]
V2 = BASE / "ops" / "backup" / "custodian_v2"
HERE = V2 / "action"
ACTION_NAME = "_root_bound_action_contract_under_test"
MODULE_NAMES = (
    "event_codec", "anchor_v2", "event_store", "protocol_v2", "staged_ipc",
    ACTION_NAME,
)
MISSING = object()
GENERATION = "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb"
OTHER_UUID = "22222222-2222-4222-8222-222222222222"
TOKEN1 = "1" * 32
TOKEN2 = "2" * 32
UNKNOWN_SERVER = "UNKNOWN_RETAIN_DURABLE_STATE"
UNKNOWN_CLIENT = {
    "status": "UNKNOWN", "head": None, "purge_or_restore_admitted": False,
}


def load_registered(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module  # Required by the REAL codec's dataclasses.
    spec.loader.exec_module(module)
    return module


@contextmanager
def isolated_protocol():
    """Restore precisely the names we own, including pre-existing objects."""
    saved = {name: sys.modules.get(name, MISSING) for name in MODULE_NAMES}
    try:
        codec = load_registered("event_codec", V2 / "codec" / "event_codec.py")
        trace = []

        class V2Anchor:
            def __init__(self, binding, target_location, head):
                self.binding = binding
                self.location = target_location
                self.state = SimpleNamespace(head=head, pending=None, revision=40)
                self.phase = None
                self.loads = 0

            def load(self):
                self.loads += 1
                return self.state

            def inspect_attempt(self, attempt):
                return self.state, self.phase

        class EventReader:
            def __init__(self, binding, target_location):
                self._binding = binding
                self.location = target_location
                self.mock_reads = 0

        class EventWriter:
            def __init__(self, binding, target_location):
                self._binding = binding
                self.location = target_location
                self.creates = []
                self.result = "PUT_HTTP200_UNCONFIRMED"

            def create(self, base, event):
                trace.append("mock_create")
                self.creates.append((base, event))
                codec.encode_candidate(self._binding, base, event)
                if isinstance(self.result, BaseException):
                    raise self.result
                return self.result

        class V2Custodian:
            """Memory-only stage model, deliberately NOT durable storage."""
            def __init__(self, anchor, reader):
                if reader._binding != anchor.binding or reader.location != anchor.location:
                    raise ValueError("mock capability binding mismatch")
                self.anchor, self.reader = anchor, reader
                self.calls = []
                self.abandoned = []

            def prepare(self, event, *, attempt_id):
                trace.append("mock_prepare")
                self.calls.append(("prepare", event, attempt_id))
                self.prepared = SimpleNamespace(event=event, base=self.anchor.state.head)
                old = self.anchor.state
                self.anchor.state = SimpleNamespace(
                    head=old.head, pending=attempt_id, revision=old.revision + 1)
                self.anchor.phase = "PREPARED"
                return self.prepared

            def claim(self, prepared):
                if prepared is not self.prepared:
                    raise ValueError("foreign mock ticket")
                trace.append("mock_claim")
                self.calls.append(("claim", prepared))
                old = self.anchor.state
                self.anchor.state = SimpleNamespace(
                    head=old.head, pending=old.pending, revision=old.revision + 1)
                self.anchor.phase = "DISPATCHED"
                self.claimed = object()
                return self.claimed

            def confirm(self, claimed):
                if claimed is not self.claimed:
                    raise ValueError("foreign mock claim")
                trace.append("mock_confirm")
                self.calls.append(("confirm", claimed))
                self.reader.mock_reads += 1
                _, target = codec.encode_candidate(
                    self.anchor.binding, self.prepared.base, self.prepared.event)
                self.anchor.state = SimpleNamespace(
                    head=target, pending=None, revision=self.anchor.state.revision + 1)
                self.anchor.phase = "COMMITTED"
                return self.anchor.state

            def abandon(self, ticket):
                self.abandoned.append(ticket)  # Does NOT reset simulated state.

        anchor_module = ModuleType("anchor_v2")
        anchor_module.V2Anchor = V2Anchor
        store_module = ModuleType("event_store")
        store_module.EventReader, store_module.EventWriter = EventReader, EventWriter
        protocol_module = ModuleType("protocol_v2")
        protocol_module.V2Custodian = V2Custodian

        def location(store):
            if type(store) not in (EventReader, EventWriter):
                raise ValueError("exact mock event capability required")
            return store.location

        protocol_module.location = location
        sys.modules.update(anchor_v2=anchor_module, event_store=store_module,
                           protocol_v2=protocol_module)
        previous = load_registered("staged_ipc", V2 / "staged" / "staged_ipc.py")
        with patch.object(previous, "runtime") as runtime:
            action = load_registered(ACTION_NAME, HERE / "action_protocol.py")
            yield SimpleNamespace(
                codec=codec, action=action, previous=previous, runtime=runtime,
                Anchor=V2Anchor, Reader=EventReader, Writer=EventWriter, trace=trace)
    finally:
        for name in reversed(MODULE_NAMES):
            if saved[name] is MISSING:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = saved[name]


class ScriptedChannel:
    """Scripted Python calls only: this is NOT a transport."""
    def __init__(self, responses=(), *, send_failure=None):
        self.responses = deque(responses)
        self.sent = []
        self.received = []
        self.closes = 0
        self.send_failure = send_failure

    def receive(self):
        item = self.responses.popleft()
        if isinstance(item, BaseException):
            raise item
        value = item(self) if callable(item) else deepcopy(item)
        self.received.append(deepcopy(value))
        return value

    def send(self, value):
        if self.send_failure == len(self.sent) + 1:
            raise OSError("scripted send failure")
        self.sent.append(deepcopy(value))

    def close(self):
        self.closes += 1


class Lifetime:
    def __init__(self, fail_at=None, error=RuntimeError):
        self.calls = 0
        self.fail_at = fail_at
        self.error = error

    def __call__(self):
        self.calls += 1
        if self.calls == self.fail_at:
            raise self.error("scripted trusted lifetime loss")


def set_nested(value, path, replacement):
    for part in path[:-1]:
        value = value[part]
    value[path[-1]] = replacement


class ProtocolCase(unittest.TestCase):
    def setUp(self):
        context = isolated_protocol()
        self.env = context.__enter__()
        self.addCleanup(context.__exit__, None, None, None)
        self.a, self.codec = self.env.action, self.env.codec
        self.binding = self.codec.Binding("unit_synthetic", GENERATION)
        self.base = self.codec.genesis(self.binding)
        self.location = (
            "https://s3.ru-3.storage.selcloud.ru/unit-bucket/pult/erasure-v2/"
            + self.binding.scope + "/" + GENERATION + "/events/")
        self.value = {
            "format": "pult-single-synthetic-action-v3",
            "approval": "a" * 64,
            "operation_id": "cccccccc-cccc-4ccc-8ccc-cccccccccccc",
            "attempt": "dddddddd-dddd-4ddd-8ddd-dddddddddddd",
            "controller": {
                "unit": "pult-unit-test.service",
                "invocation_id": "b" * 32,
                "boot": "11111111-1111-4111-8111-111111111111",
                "pid": 101, "start_ticks": 202,
            },
            "binding": {
                "scope": self.binding.scope, "generation": GENERATION,
                "location": self.location,
            },
            "event": {
                "purge_id": "eeeeeeee-eeee-4eee-8eee-eeeeeeeeeeee",
                "subject_id": "ffffffff-ffff-4fff-8fff-ffffffffffff",
                "phase": "intent",
            },
            "base": {"sequence": self.base.sequence, "digest": self.base.digest},
            "custodian": {"uid": 1001, "gid": 1002},
            "writer": {"uid": 1003, "gid": 1004},
        }

    def operation(self, value=None):
        return self.a.Operation(self.codec.canonical(self.value if value is None else value))

    def refused_change(self, path, replacement):
        value = deepcopy(self.value)
        set_nested(value, path, replacement)
        with self.assertRaises(self.a.Refused):
            self.operation(value)

    def server(self, lifetime=None, operation=None):
        anchor = self.env.Anchor(self.binding, self.location, self.base)
        reader = self.env.Reader(self.binding, self.location)
        server = self.a.Server(anchor, reader, operation or self.operation(), lifetime or Lifetime())
        return server, anchor, reader

    def client(self, lifetime=None, operation=None):
        writer = self.env.Writer(self.binding, self.location)
        return self.a.Client(writer, operation or self.operation(), lifetime or Lifetime()), writer

    def server_channel(self, operation=None):
        operation = operation or self.operation()

        def response(op):
            return lambda channel: operation.frame(op=op, token=channel.sent[-1]["token"])

        return ScriptedChannel([
            operation.frame(op="prepare"), response("dispatch"), response("confirm")])

    def client_channel(self, operation=None):
        operation = operation or self.operation()
        return ScriptedChannel([
            operation.frame(status="PREPARED", token=TOKEN1, authority={"purge": False, "restore": False}),
            operation.frame(status="DISPATCHED", token=TOKEN2, authority={"purge": False, "restore": False}),
            operation.frame(status="COMMITTED_STORAGE_ONLY", authority={"purge": False, "restore": False}),
        ])

    def serve(self, server, channel):
        with patch.object(self.env.previous, "Channel", return_value=channel) as factory:
            result = server.serve(object())
        return result, factory

    def append(self, client, channel):
        with patch.object(self.env.previous, "Channel", return_value=channel) as factory:
            result = client.append(object())
        return result, factory


class OperationSchemaTests(ProtocolCase):
    def test_real_codec_is_loaded_and_target_is_independently_derived(self):
        self.assertEqual(Path(self.codec.__file__).resolve(), (V2 / "codec" / "event_codec.py").resolve())
        operation = self.operation()
        body, target = self.codec.encode_candidate(
            self.binding, self.base, self.codec.Event(**self.value["event"]))
        decoded_event, decoded_head = self.codec.decode_record(body, self.binding, self.base)
        self.assertEqual(decoded_event, self.codec.Event(**self.value["event"]))
        self.assertEqual(decoded_head, target)
        context = operation.context()
        expected = deepcopy(self.value)
        expected["format"] = "pult-root-bound-erasure-action-v3"
        expected["target"] = {"sequence": target.sequence, "digest": target.digest}
        self.assertEqual(context, expected)
        self.assertEqual(operation.value(), self.value)
        self.assertEqual(operation._sha, hashlib.sha256(self.codec.canonical(self.value)).hexdigest())

    def test_exact_top_level_fields(self):
        self.assertEqual(self.a.FIELDS, set(self.value))
        for key in self.value:
            with self.subTest(missing=key):
                value = deepcopy(self.value)
                del value[key]
                with self.assertRaises(self.a.Refused):
                    self.operation(value)
        self.refused_change(("target",), {})
        self.refused_change(("authority",), {"purge": True})

    def test_exact_nested_fields(self):
        for container in ("controller", "binding", "event", "base", "custodian", "writer"):
            for key in self.value[container]:
                with self.subTest(container=container, missing=key):
                    value = deepcopy(self.value)
                    del value[container][key]
                    with self.assertRaises(self.a.Refused):
                        self.operation(value)
            with self.subTest(container=container, extra=True):
                value = deepcopy(self.value)
                value[container]["extra"] = False
                with self.assertRaises(self.a.Refused):
                    self.operation(value)

    def test_nested_containers_have_exact_dict_type(self):
        for key in ("controller", "binding", "event", "base", "custodian", "writer"):
            for bad in (None, [], "", 0, True):
                with self.subTest(key=key, bad=bad):
                    self.refused_change((key,), bad)

    def test_old_or_foreign_versions_refused(self):
        for bad in ("pult-staged-erasure-ipc-v2", self.a.FORMAT,
                    "pult-single-synthetic-action-v2", "pult-single-synthetic-action-v4", None, 3, True):
            with self.subTest(bad=bad):
                self.refused_change(("format",), bad)

    def test_raw_exact_bytes_type_and_bound(self):
        raw = self.codec.canonical(self.value)

        class BytesSubclass(bytes):
            pass

        for bad in (None, raw.decode("ascii"), bytearray(raw), memoryview(raw),
                    BytesSubclass(raw), b"", b" " * (self.env.previous.MAX_FRAME + 1)):
            with self.subTest(kind=type(bad).__name__):
                with self.assertRaises(self.a.Refused):
                    self.a.Operation(bad)

    def test_exact_raw_boundary_and_frame_boundary(self):
        value = deepcopy(self.value)
        value["controller"]["pid"] = 1
        digits = self.env.previous.MAX_FRAME - len(self.codec.canonical(value)) + 1
        value["controller"]["pid"] = int("1" * digits)
        raw = self.codec.canonical(value)
        self.assertEqual(len(raw), self.env.previous.MAX_FRAME)
        operation = self.a.Operation(raw)
        with self.assertRaises(self.a.Refused):
            self.a.Operation(raw + b" ")
        with self.assertRaises(self.a.Refused):
            operation.frame(op="prepare")
        with self.assertRaises(self.a.Refused):
            self.operation().frame(extra="x" * self.env.previous.MAX_FRAME)

    def test_noncanonical_and_invalid_documents_refused(self):
        raw = self.codec.canonical(self.value)
        for bad in (raw[:-1], raw + b"\n", b" " + raw,
                    json.dumps(self.value, sort_keys=True).encode("ascii") + b"\n",
                    b"[]\n", b"null\n", b"true\n", b"{}\n", b"{", b"\xff\n",
                    b'{"bad":NaN}\n', b'{"bad":Infinity}\n', b"[" * 1100 + b"]" * 1100):
            with self.subTest(raw=bad[:30]):
                with self.assertRaises(self.a.Refused):
                    self.a.Operation(bad)

    def test_duplicate_fields_at_every_nested_level_refused(self):
        raw = self.codec.canonical(self.value)
        for key in ("approval", "pid", "scope", "phase", "sequence", "uid"):
            marker = ('"' + key + '":').encode("ascii")
            duplicate = raw.replace(marker, marker + b"null," + marker, 1)
            with self.subTest(key=key):
                with self.assertRaises(self.a.Refused):
                    self.a.Operation(duplicate)

    def test_all_uuid_fields_are_canonical_version_four_strings(self):
        paths = (("operation_id",), ("attempt",), ("controller", "boot"),
                 ("binding", "generation"), ("event", "purge_id"), ("event", "subject_id"))
        for path in paths:
            for bad in (None, False, 4, "", "not-a-uuid", OTHER_UUID.upper().replace("2", "A"),
                        OTHER_UUID.replace("-", ""), "22222222-2222-1222-8222-222222222222",
                        "22222222-2222-4222-0222-222222222222", "{" + OTHER_UUID + "}"):
                with self.subTest(path=path, bad=bad):
                    self.refused_change(path, bad)

    def test_approval_is_exact_lowercase_sha256_string(self):
        for bad in (None, False, 0, [], {}, "", "a" * 63, "a" * 65,
                    "A" * 64, "g" * 64, "a" * 64 + "\n"):
            with self.subTest(bad=bad):
                self.refused_change(("approval",), bad)

    def test_event_only_allows_one_intent(self):
        for bad in ("complete", "INTENT", "intent\n", "", None, False, 1):
            with self.subTest(bad=bad):
                self.refused_change(("event", "phase"), bad)

    def test_controller_exact_identity_types_and_positive_bounds(self):
        for key in ("pid", "start_ticks"):
            for bad in (True, False, 0, -1, 1.0, "1", None):
                with self.subTest(key=key, bad=bad):
                    self.refused_change(("controller", key), bad)
        for bad in (None, False, 1, "", "b" * 31, "b" * 33, "B" * 32, "g" * 32):
            with self.subTest(invocation=bad):
                self.refused_change(("controller", "invocation_id"), bad)
        for bad in (None, 1, "pult-.service", "pult-" + "a" * 101 + ".service",
                    "pult-UPPER.service", "other-a.service", "pult-aXservice",
                    "pult-a.service/extra", "pult-a.service\n"):
            with self.subTest(unit=bad):
                self.refused_change(("controller", "unit"), bad)

    def test_controller_unit_length_boundaries_admitted(self):
        for count in (1, 100):
            value = deepcopy(self.value)
            value["controller"]["unit"] = "pult-" + "a" * count + ".service"
            value["controller"].update(pid=1, start_ticks=1)
            self.assertEqual(self.operation(value).value(), value)

    def test_roles_exact_integer_bounds_and_separate_uids(self):
        for role in ("custodian", "writer"):
            for key in ("uid", "gid"):
                for bad in (True, False, 999, 0, -1, 2**31, 1000.0, "1000", None):
                    with self.subTest(role=role, key=key, bad=bad):
                        self.refused_change((role, key), bad)
        self.refused_change(("writer", "uid"), self.value["custodian"]["uid"])
        value = deepcopy(self.value)
        value["custodian"] = {"uid": 1000, "gid": 1000}
        value["writer"] = {"uid": 2**31 - 1, "gid": 2**31 - 1}
        self.assertEqual(self.operation(value).value(), value)

    def test_scope_bounds_and_characters(self):
        for bad in (None, False, 1, "", "a" * 65, "with/slash", "with.dot", "space x", "ю"):
            with self.subTest(bad=bad):
                self.refused_change(("binding", "scope"), bad)
        for scope in ("a", "A_9-" + "a" * 60):
            value = deepcopy(self.value)
            binding = self.codec.Binding(scope, GENERATION)
            value["binding"]["scope"] = scope
            value["binding"]["location"] = self.location.replace(self.binding.scope, scope)
            value["base"] = self.env.previous.head_wire(self.codec.genesis(binding))
            self.assertEqual(self.operation(value).value(), value)

    def test_fixed_ru3_synthetic_location_only(self):
        bad_locations = (None, False, 1, self.location.replace("https:", "http:"),
            self.location.replace("ru-3", "ru-1"), self.location.replace("s3.", "user@s3."),
            self.location.replace(".ru/", ".ru:443/"), self.location + "extra",
            self.location + "?x=1", self.location + "#x", self.location[:-1],
            self.location.replace("unit-bucket", "aa"),
            self.location.replace("unit-bucket", "a" * 64),
            self.location.replace("unit-bucket", "Bad_bucket"),
            self.location.replace(GENERATION, OTHER_UUID),
            self.location.replace("/pult/", "/other/"))
        for bad in bad_locations:
            with self.subTest(bad=bad):
                self.refused_change(("binding", "location"), bad)
        for count in (3, 63):
            value = deepcopy(self.value)
            value["binding"]["location"] = self.location.replace("unit-bucket", "a" * count)
            self.assertEqual(self.operation(value).value(), value)

    def test_head_exact_types_bounds_genesis_and_exhaustion(self):
        for bad in (True, False, -1, 0.0, "0", None, 2**63 - 1, 2**63):
            with self.subTest(sequence=bad):
                self.refused_change(("base", "sequence"), bad)
        for bad in (None, 0, False, "", "a" * 63, "a" * 65, "A" * 64, "g" * 64, "0" * 64):
            with self.subTest(digest=bad):
                self.refused_change(("base", "digest"), bad)
        value = deepcopy(self.value)
        value["base"] = {"sequence": 2**63 - 2, "digest": "a" * 64}
        self.assertEqual(self.operation(value).context()["target"]["sequence"], 2**63 - 1)

    def test_value_and_context_are_fresh_and_raw_mutation_refused(self):
        operation = self.operation()
        value = operation.value()
        value["event"]["phase"] = "complete"
        context = operation.context()
        context["controller"]["pid"] = 999
        self.assertEqual(operation.value(), self.value)
        operation._raw = operation._raw + b" "
        with self.assertRaises(self.a.Refused):
            operation.value()
        with self.assertRaises(self.a.Refused):
            operation.context()


class ServerMockedContractTests(ProtocolCase):
    def test_exact_already_owned_capabilities_required(self):
        anchor = self.env.Anchor(self.binding, self.location, self.base)
        reader = self.env.Reader(self.binding, self.location)
        operation = self.operation()
        variants = ((object(), reader, operation), (anchor, object(), operation),
                    (anchor, reader, object()))
        for args in variants:
            with self.subTest(types=[type(x).__name__ for x in args]):
                with self.assertRaises(self.a.Refused):
                    self.a.Server(*args, Lifetime())
        for cls, index, args in ((self.env.Anchor, 0, (self.binding, self.location, self.base)),
                                 (self.env.Reader, 1, (self.binding, self.location)),
                                 (self.a.Operation, 2, (self.codec.canonical(self.value),))):
            subclass = type("NotExact", (cls,), {})
            values = [anchor, reader, operation]
            values[index] = subclass(*args)
            with self.assertRaises(self.a.Refused):
                self.a.Server(*values, Lifetime())

    def test_constructor_binding_mismatch_before_custodian_creation(self):
        for target in ("anchor_location", "anchor_binding", "reader_location"):
            anchor = self.env.Anchor(self.binding, self.location, self.base)
            reader = self.env.Reader(self.binding, self.location)
            if target == "anchor_location":
                anchor.location += "foreign/"
            elif target == "anchor_binding":
                anchor.binding = self.codec.Binding("foreign", GENERATION)
            else:
                reader.location += "foreign/"
            with self.subTest(target=target), patch.object(self.a, "V2Custodian") as custodian:
                with self.assertRaises(self.a.Refused):
                    self.a.Server(anchor, reader, self.operation(), Lifetime())
                custodian.assert_not_called()
                self.assertEqual(anchor.loads, 0)

    def test_prepare_request_mismatches_refused_before_any_state_access(self):
        operation = self.operation()
        paths = {
            ("approval",): "b" * 64, ("operation_id",): OTHER_UUID,
            ("attempt",): OTHER_UUID, ("event", "purge_id"): OTHER_UUID,
            ("event", "subject_id"): OTHER_UUID, ("event", "phase"): "complete",
            ("binding", "scope"): "foreign", ("binding", "generation"): OTHER_UUID,
            ("binding", "location"): self.location + "foreign/",
            ("base", "sequence"): 1, ("base", "digest"): "0" * 64,
            ("target", "digest"): "0" * 64, ("target", "sequence"): True,
            ("controller", "unit"): "pult-foreign.service",
            ("controller", "invocation_id"): "c" * 32,
            ("controller", "boot"): OTHER_UUID, ("controller", "pid"): 102,
            ("controller", "start_ticks"): 203,
            ("writer", "uid"): 1005, ("custodian", "gid"): 1006,
            ("format",): "pult-staged-erasure-ipc-v2", ("op",): "dispatch",
        }
        requests = []
        for path, replacement in paths.items():
            request = operation.frame(op="prepare")
            set_nested(request, path, replacement)
            requests.append((str(path), request))
        for key in operation.frame(op="prepare"):
            request = operation.frame(op="prepare")
            del request[key]
            requests.append(("missing " + key, request))
        requests.extend([("extra", operation.frame(op="prepare", authority={"purge": False})),
                         ("not_dict", []), ("none", None)])
        for label, request in requests:
            with self.subTest(label=label):
                server, anchor, reader = self.server(operation=operation)
                channel = ScriptedChannel([request])
                result, factory = self.serve(server, channel)
                self.assertEqual(result, UNKNOWN_SERVER)
                self.assertEqual(anchor.loads, 0)
                self.assertEqual(server._custodian.calls, [])
                self.assertEqual(reader.mock_reads, 0)
                self.assertEqual(channel.sent, [])
                self.assertEqual(channel.closes, 1)
                self.assertEqual(factory.call_args.kwargs, self.value["writer"])

    def test_stale_head_or_pending_refused_before_prepare(self):
        for mode in ("head", "pending"):
            with self.subTest(mode=mode):
                server, anchor, _ = self.server()
                if mode == "head":
                    anchor.state.head = self.codec.Head(1, "0" * 64)
                else:
                    anchor.state.pending = OTHER_UUID
                result, _ = self.serve(server, self.server_channel())
                self.assertEqual(result, UNKNOWN_SERVER)
                self.assertEqual(server._custodian.calls, [])
                self.assertEqual(anchor.state.revision, 40)

    def test_scripted_success_exact_event_attempt_stages_and_false_authority(self):
        lifetime = Lifetime()
        operation = self.operation()
        server, anchor, reader = self.server(lifetime, operation)
        channel = self.server_channel(operation)
        with patch.object(self.a, "uuid4", side_effect=[SimpleNamespace(hex=TOKEN1), SimpleNamespace(hex=TOKEN2)]):
            result, factory = self.serve(server, channel)
        self.assertEqual(result, "COMMITTED_STORAGE_ONLY")
        self.assertEqual(channel.sent, list(self.client_channel(operation).responses))
        self.assertEqual([call[0] for call in server._custodian.calls], ["prepare", "claim", "confirm"])
        self.assertEqual(server._custodian.calls[0][1:],
                         (self.codec.Event(**self.value["event"]), self.value["attempt"]))
        self.assertEqual(anchor.state.head, self.env.previous.head(operation.context()["target"]))
        self.assertIsNone(anchor.state.pending)
        self.assertEqual(anchor.state.revision, 43)
        self.assertEqual(reader.mock_reads, 1)
        self.assertEqual(len(server._custodian.abandoned), 2)
        self.assertEqual(channel.closes, 1)
        self.assertEqual(lifetime.calls, 8)
        self.assertEqual(factory.call_args.kwargs, self.value["writer"])
        for frame in channel.sent:
            self.assertIs(frame["authority"]["purge"], False)
            self.assertIs(frame["authority"]["restore"], False)

    def test_dispatch_or_confirmation_token_and_context_mismatch_stops_next_stage(self):
        for stage in (1, 2):
            for path, replacement in ((("token",), "0" * 32),
                                      (("approval",), "b" * 64),
                                      (("attempt",), OTHER_UUID),
                                      (("event", "phase"), "complete")):
                with self.subTest(stage=stage, path=path):
                    server, anchor, _ = self.server()
                    channel = self.server_channel()
                    responses = list(channel.responses)
                    original = responses[stage]

                    def bad_response(current, original=original, path=path, replacement=replacement):
                        frame = original(current)
                        set_nested(frame, path, replacement)
                        return frame

                    responses[stage] = bad_response
                    channel.responses = deque(responses)
                    result, _ = self.serve(server, channel)
                    self.assertEqual(result, UNKNOWN_SERVER)
                    self.assertEqual([c[0] for c in server._custodian.calls],
                                     ["prepare"] if stage == 1 else ["prepare", "claim"])
                    self.assertEqual(anchor.phase, "PREPARED" if stage == 1 else "DISPATCHED")
                    self.assertNotIn("COMMITTED_STORAGE_ONLY", [f["status"] for f in channel.sent])

    def test_bad_prepared_or_dispatched_snapshot_cannot_advance(self):
        for stage in ("PREPARED", "DISPATCHED"):
            for field, bad in (("phase", "FOREIGN"), ("head", self.codec.Head(1, "0" * 64)),
                               ("pending", OTHER_UUID), ("revision", 999)):
                with self.subTest(stage=stage, field=field):
                    server, anchor, _ = self.server()
                    original = anchor.inspect_attempt

                    def inspect(attempt, original=original, stage=stage, field=field, bad=bad):
                        state, phase = original(attempt)
                        if phase == stage:
                            state = SimpleNamespace(**vars(state))
                            if field == "phase":
                                phase = bad
                            else:
                                setattr(state, field, bad)
                        return state, phase

                    with patch.object(anchor, "inspect_attempt", side_effect=inspect):
                        result, _ = self.serve(server, self.server_channel())
                    self.assertEqual(result, UNKNOWN_SERVER)
                    self.assertEqual([c[0] for c in server._custodian.calls],
                                     ["prepare"] if stage == "PREPARED" else ["prepare", "claim"])

    def test_bad_confirmed_snapshot_cannot_report_commit(self):
        for field, bad in (("head", self.base), ("pending", OTHER_UUID), ("revision", 999)):
            with self.subTest(field=field):
                server, _, _ = self.server()
                confirm = server._custodian.confirm

                def uncertain(ticket, confirm=confirm, field=field, bad=bad):
                    state = SimpleNamespace(**vars(confirm(ticket)))
                    setattr(state, field, bad)
                    return state

                channel = self.server_channel()
                with patch.object(server._custodian, "confirm", side_effect=uncertain):
                    result, _ = self.serve(server, channel)
                self.assertEqual(result, UNKNOWN_SERVER)
                self.assertEqual([f["status"] for f in channel.sent], ["PREPARED", "DISPATCHED"])

    def test_server_one_attempt_after_success_or_unknown(self):
        for success in (True, False):
            with self.subTest(success=success):
                server, _, _ = self.server()
                channel = self.server_channel() if success else ScriptedChannel([OSError("receive")])
                self.serve(server, channel)
                calls = list(server._custodian.calls)
                with patch.object(self.env.previous, "Channel") as factory:
                    with self.assertRaises(self.a.Refused):
                        server.serve(object())
                    factory.assert_not_called()
                self.assertEqual(server._custodian.calls, calls)

    def test_channel_construction_failure_consumes_server_attempt(self):
        server, _, _ = self.server()
        with patch.object(self.env.previous, "Channel", side_effect=OSError("channel")) as factory:
            self.assertEqual(server.serve(object()), UNKNOWN_SERVER)
            with self.assertRaises(self.a.Refused):
                server.serve(object())
            self.assertEqual(factory.call_count, 1)
        self.assertEqual(server._custodian.calls, [])

    def test_custodian_exceptions_do_not_retry_or_reset_simulated_state(self):
        for method, phase in (("prepare", None), ("claim", "PREPARED"), ("confirm", "DISPATCHED")):
            with self.subTest(method=method):
                server, anchor, _ = self.server()
                channel = self.server_channel()
                with patch.object(server._custodian, method, side_effect=RuntimeError("uncertain")) as failing:
                    result, _ = self.serve(server, channel)
                self.assertEqual(result, UNKNOWN_SERVER)
                self.assertEqual(failing.call_count, 1)
                self.assertEqual(anchor.phase, phase)
                if phase is not None:
                    self.assertEqual(anchor.state.pending, self.value["attempt"])
                self.assertNotIn("COMMITTED_STORAGE_ONLY", [f["status"] for f in channel.sent])

    def test_every_server_lifetime_exception_returns_unknown(self):
        for error in (RuntimeError, KeyboardInterrupt, SystemExit):
            for fail_at in range(2, 9):
                with self.subTest(error=error.__name__, fail_at=fail_at):
                    lifetime = Lifetime(fail_at, error)
                    server, _, _ = self.server(lifetime)
                    channel = self.server_channel()
                    result, _ = self.serve(server, channel)
                    self.assertEqual(result, UNKNOWN_SERVER)
                    self.assertEqual(lifetime.calls, fail_at)
                    self.assertEqual(channel.closes, 0 if fail_at == 2 else 1)
                    for name in ("prepare", "claim", "confirm"):
                        self.assertLessEqual(sum(c[0] == name for c in server._custodian.calls), 1)

    def test_lifetime_failure_must_not_publish_a_positive_commit_ack(self):
        # Every checked pre-ACK failure, including the final check at call 8,
        # must remain UNKNOWN without a peer-visible positive commit frame.
        for fail_at in range(2, 9):
            with self.subTest(fail_at=fail_at):
                lifetime = Lifetime(fail_at)
                server, _, _ = self.server(lifetime)
                channel = self.server_channel()
                result, _ = self.serve(server, channel)
                self.assertEqual(result, UNKNOWN_SERVER)
                self.assertEqual(lifetime.calls, fail_at)
                self.assertNotIn("COMMITTED_STORAGE_ONLY", [f["status"] for f in channel.sent])

    def test_commit_ack_has_no_late_lifetime_check_or_result_retraction(self):
        # Pre-ACK is the protocol linearization point. This does not prove
        # Host.close/whole-cgroup cleanup or admit any purge/restore operation.
        lifetime = Lifetime(fail_at=9)
        server, _, _ = self.server(lifetime)
        channel = self.server_channel()
        calls_at_ack = []
        send = channel.send

        def record_terminal_ack(frame):
            send(frame)
            if frame["status"] == "COMMITTED_STORAGE_ONLY":
                calls_at_ack.append(lifetime.calls)

        with patch.object(channel, "send", side_effect=record_terminal_ack):
            result, _ = self.serve(server, channel)
        self.assertEqual(result, "COMMITTED_STORAGE_ONLY")
        self.assertEqual(calls_at_ack, [8])
        self.assertEqual(lifetime.calls, calls_at_ack[0])
        self.assertEqual(lifetime.fail_at, 9)
        self.assertEqual([frame["status"] for frame in channel.sent],
                         ["PREPARED", "DISPATCHED", "COMMITTED_STORAGE_ONLY"])
        self.assertIs(channel.sent[-1]["authority"]["purge"], False)
        self.assertIs(channel.sent[-1]["authority"]["restore"], False)
        self.assertEqual(channel.closes, 1)

    def test_noncallable_lifetime_refused_before_protocol(self):
        for bad in (None, False, 1, object()):
            with self.subTest(bad=type(bad).__name__):
                anchor = self.env.Anchor(self.binding, self.location, self.base)
                reader = self.env.Reader(self.binding, self.location)
                with self.assertRaises(self.a.Refused):
                    self.a.Server(anchor, reader, self.operation(), bad)
                self.assertEqual(anchor.loads, 0)


class ClientMockedContractTests(ProtocolCase):
    def test_exact_writer_and_operation_capabilities_required(self):
        writer = self.env.Writer(self.binding, self.location)
        for candidate, operation in ((object(), self.operation()), (writer, object()),
                                     (self.env.Reader(self.binding, self.location), self.operation())):
            with self.subTest(candidate=type(candidate).__name__):
                with self.assertRaises(self.a.Refused):
                    self.a.Client(candidate, operation, Lifetime())
        subclass = type("NotExactWriter", (self.env.Writer,), {})
        with self.assertRaises(self.a.Refused):
            self.a.Client(subclass(self.binding, self.location), self.operation(), Lifetime())

    def test_writer_location_or_binding_mismatch_refused_before_create(self):
        for mismatch in ("location", "binding"):
            writer = self.env.Writer(self.binding, self.location)
            if mismatch == "location":
                writer.location += "foreign/"
            else:
                writer._binding = self.codec.Binding("foreign", GENERATION)
            with self.subTest(mismatch=mismatch), self.assertRaises(self.a.Refused):
                self.a.Client(writer, self.operation(), Lifetime())
            self.assertEqual(writer.creates, [])

    def test_scripted_success_one_create_and_storage_only_result(self):
        lifetime = Lifetime()
        operation = self.operation()
        client, writer = self.client(lifetime, operation)
        channel = self.client_channel(operation)
        result, factory = self.append(client, channel)
        self.assertEqual(result, {"status": "COMMITTED_STORAGE_ONLY", "head": operation.context()["target"],
                                  "purge_or_restore_admitted": False})
        self.assertIs(result["purge_or_restore_admitted"], False)
        self.assertEqual(writer.creates, [(self.base, self.codec.Event(**self.value["event"]))])
        self.assertEqual(channel.sent, [operation.frame(op="prepare"),
            operation.frame(op="dispatch", token=TOKEN1), operation.frame(op="confirm", token=TOKEN2)])
        self.assertEqual(factory.call_args.kwargs, self.value["custodian"])
        self.assertEqual(channel.closes, 1)
        self.assertEqual(lifetime.calls, 6)

    def test_false_authority_strict_types_at_every_ack_stage(self):
        for stage in range(3):
            for key in ("purge", "restore"):
                for bad in (0, 0.0, None, "false", True, [], {}):
                    with self.subTest(stage=stage, key=key, bad=bad):
                        client, writer = self.client()
                        channel = self.client_channel()
                        channel.responses[stage]["authority"][key] = bad
                        result, _ = self.append(client, channel)
                        self.assertEqual(result, UNKNOWN_CLIENT)
                        self.assertIs(result["purge_or_restore_admitted"], False)
                        self.assertEqual(len(writer.creates), 1 if stage == 2 else 0)
                        self.assertEqual(len(channel.sent), stage + 1)
                        self.assertEqual(channel.closes, 1)

    def test_ack_authority_exact_schema(self):
        for stage in range(3):
            for authority in ({}, {"purge": False}, {"purge": False, "restore": False, "extra": False},
                              False, None, []):
                with self.subTest(stage=stage, authority=authority):
                    client, _ = self.client()
                    channel = self.client_channel()
                    channel.responses[stage]["authority"] = authority
                    self.assertEqual(self.append(client, channel)[0], UNKNOWN_CLIENT)

    def test_ack_root_binding_event_attempt_or_status_mismatch_refused(self):
        changes = ((("approval",), "b" * 64), (("operation_id",), OTHER_UUID),
                   (("attempt",), OTHER_UUID), (("event", "purge_id"), OTHER_UUID),
                   (("binding", "scope"), "foreign"), (("controller", "pid"), 102),
                   (("base", "sequence"), False), (("target", "sequence"), True),
                   (("status",), "PURGE_ALLOWED"), (("format",), "pult-staged-erasure-ipc-v2"))
        for stage in range(3):
            for path, bad in changes:
                with self.subTest(stage=stage, path=path):
                    client, writer = self.client()
                    channel = self.client_channel()
                    set_nested(channel.responses[stage], path, bad)
                    result, _ = self.append(client, channel)
                    self.assertEqual(result, UNKNOWN_CLIENT)
                    self.assertEqual(len(writer.creates), 1 if stage == 2 else 0)

    def test_ack_missing_or_extra_fields_refused(self):
        for stage in range(3):
            template = list(self.client_channel().responses)[stage]
            for key in list(template) + ["EXTRA"]:
                with self.subTest(stage=stage, key=key):
                    client, _ = self.client()
                    channel = self.client_channel()
                    if key == "EXTRA":
                        channel.responses[stage][key] = False
                    else:
                        del channel.responses[stage][key]
                    self.assertEqual(self.append(client, channel)[0], UNKNOWN_CLIENT)

    def test_ack_tokens_are_exact_lowercase_32_character_strings(self):
        for stage in (0, 1):
            for bad in (None, False, 0, "", "a" * 31, "a" * 33, "A" * 32, "g" * 32):
                with self.subTest(stage=stage, bad=bad):
                    client, writer = self.client()
                    channel = self.client_channel()
                    channel.responses[stage]["token"] = bad
                    self.assertEqual(self.append(client, channel)[0], UNKNOWN_CLIENT)
                    self.assertEqual(writer.creates, [])

    def test_non_dict_ack_returns_unknown(self):
        for stage in range(3):
            for bad in (None, [], False, 0, "COMMITTED_STORAGE_ONLY"):
                with self.subTest(stage=stage, bad=bad):
                    client, _ = self.client()
                    channel = self.client_channel()
                    channel.responses[stage] = bad
                    self.assertEqual(self.append(client, channel)[0], UNKNOWN_CLIENT)

    def test_unconfirmed_put_result_cannot_be_promoted_or_retried(self):
        class StringSubclass(str):
            pass

        for bad in (None, True, 200, "HTTP200", "COMMITTED_STORAGE_ONLY",
                    StringSubclass("PUT_HTTP200_UNCONFIRMED"), RuntimeError("put uncertain")):
            with self.subTest(bad=repr(bad)):
                client, writer = self.client()
                writer.result = bad
                channel = self.client_channel()
                result, _ = self.append(client, channel)
                self.assertEqual(result, UNKNOWN_CLIENT)
                self.assertEqual(len(writer.creates), 1)
                self.assertEqual([f["op"] for f in channel.sent], ["prepare", "dispatch"])
                with self.assertRaises(self.a.Refused):
                    client.append(object())
                self.assertEqual(len(writer.creates), 1)

    def test_client_one_attempt_after_success_or_unknown(self):
        for success in (True, False):
            with self.subTest(success=success):
                client, writer = self.client()
                channel = self.client_channel() if success else ScriptedChannel([OSError("receive")])
                self.append(client, channel)
                creates = list(writer.creates)
                with patch.object(self.env.previous, "Channel") as factory:
                    with self.assertRaises(self.a.Refused):
                        client.append(object())
                    factory.assert_not_called()
                self.assertEqual(writer.creates, creates)

    def test_channel_construction_failure_consumes_client_attempt(self):
        client, writer = self.client()
        with patch.object(self.env.previous, "Channel", side_effect=OSError("channel")) as factory:
            self.assertEqual(client.append(object()), UNKNOWN_CLIENT)
            with self.assertRaises(self.a.Refused):
                client.append(object())
            self.assertEqual(factory.call_count, 1)
        self.assertEqual(writer.creates, [])

    def test_every_client_lifetime_exception_returns_unknown_without_positive_result(self):
        for error in (RuntimeError, KeyboardInterrupt, SystemExit):
            for fail_at in range(2, 7):
                with self.subTest(error=error.__name__, fail_at=fail_at):
                    lifetime = Lifetime(fail_at, error)
                    client, writer = self.client(lifetime)
                    channel = self.client_channel()
                    result, _ = self.append(client, channel)
                    self.assertEqual(result, UNKNOWN_CLIENT)
                    self.assertIs(result["purge_or_restore_admitted"], False)
                    self.assertEqual(lifetime.calls, fail_at)
                    self.assertLessEqual(len(writer.creates), 1)
                    self.assertEqual(channel.closes, 0 if fail_at == 2 else 1)
                    with self.assertRaises(self.a.Refused):
                        client.append(object())

    def test_send_and_receive_failures_are_unknown_with_no_retry(self):
        for stage in range(3):
            for direction in ("send", "receive"):
                with self.subTest(stage=stage, direction=direction):
                    client, writer = self.client()
                    channel = self.client_channel()
                    if direction == "send":
                        channel.send_failure = stage + 1
                    else:
                        channel.responses[stage] = OSError("scripted receive failure")
                    self.assertEqual(self.append(client, channel)[0], UNKNOWN_CLIENT)
                    self.assertLessEqual(len(writer.creates), 1)
                    self.assertEqual(channel.closes, 1)
                    with self.assertRaises(self.a.Refused):
                        client.append(object())

    def test_noncallable_lifetime_refused_before_create(self):
        for bad in (None, False, 1, object()):
            writer = self.env.Writer(self.binding, self.location)
            with self.subTest(bad=type(bad).__name__), self.assertRaises(self.a.Refused):
                self.a.Client(writer, self.operation(), bad)
            self.assertEqual(writer.creates, [])


class ModuleIsolationTests(unittest.TestCase):
    def test_existing_module_objects_are_restored_by_identity(self):
        original = {name: sys.modules.get(name, MISSING) for name in MODULE_NAMES}
        sentinels = {name: ModuleType(name) for name in MODULE_NAMES}
        try:
            sys.modules.update(sentinels)
            for _ in range(2):
                with isolated_protocol() as env:
                    self.assertIs(sys.modules["event_codec"], env.codec)
                    self.assertIs(sys.modules[ACTION_NAME], env.action)
                    self.assertIsNot(env.codec, sentinels["event_codec"])
                for name, sentinel in sentinels.items():
                    self.assertIs(sys.modules[name], sentinel)
        finally:
            for name, value in original.items():
                if value is MISSING:
                    sys.modules.pop(name, None)
                else:
                    sys.modules[name] = value

    def test_absent_module_names_stay_absent_and_exception_restores_registry(self):
        original = {name: sys.modules.get(name, MISSING) for name in MODULE_NAMES}
        try:
            for name in MODULE_NAMES:
                sys.modules.pop(name, None)
            with self.assertRaisesRegex(RuntimeError, "body failure"):
                with isolated_protocol():
                    raise RuntimeError("body failure")
            for name in MODULE_NAMES:
                self.assertNotIn(name, sys.modules)
        finally:
            for name, value in original.items():
                if value is not MISSING:
                    sys.modules[name] = value

    def test_failed_import_restores_every_owned_name(self):
        original = {name: sys.modules.get(name, MISSING) for name in MODULE_NAMES}
        actual_loader = load_registered

        def fail_action(name, path):
            if name == ACTION_NAME:
                sys.modules[name] = ModuleType(name)
                raise ImportError("scripted source import failure")
            return actual_loader(name, path)

        with patch(__name__ + ".load_registered", side_effect=fail_action):
            with self.assertRaisesRegex(ImportError, "scripted source import failure"):
                with isolated_protocol():
                    self.fail("unreachable")
        for name, value in original.items():
            if value is MISSING:
                self.assertNotIn(name, sys.modules)
            else:
                self.assertIs(sys.modules[name], value)


if __name__ == "__main__":
    unittest.main()
