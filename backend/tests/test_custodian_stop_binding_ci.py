"""Portable stdlib contract tests; no service, subprocess, or runtime bypass.

Run on Windows: python -B -m unittest -v test_stop_contract
Each test imports fresh sibling sources, without writing bytecode.  The fcntl
shim only permits importing on Windows; using its flock is an assertion error.
Pure POSIX path tests replace Path locally, not the production runtime gate.
Manager negative tests mock only OS descriptors.  Real /proc/1/root identity
tests run only on Linux as root; they do not invoke systemd.
"""

from contextlib import ExitStack
import importlib.util
import json
import os
from pathlib import Path, PurePosixPath
import sys
import tempfile
from types import ModuleType, SimpleNamespace
import unittest
from unittest import mock


REPO = Path(__file__).resolve().parents[2]
HERE = REPO / 'ops/backup/custodian_v2/stop'
PROFILE_SHA = "a" * 64
HOST_SHA = "b" * 64
OBSERVATION_SHA = "c" * 64
FLAGS = ("write_admitted", "listener_admitted", "purge_or_restore_admitted")
LINUX_ROOT = sys.platform == "linux" and getattr(os, "geteuid", lambda: -1)() == 0


def fresh_source(stem):
    """Use importlib metadata but always compile source, not a stale .pyc."""
    spec = importlib.util.spec_from_file_location("_contract_" + stem, HERE / (stem + ".py"))
    module = importlib.util.module_from_spec(spec)
    with ExitStack() as stack:
        if sys.platform == "win32":
            shim = ModuleType("fcntl")
            shim.LOCK_EX, shim.LOCK_NB = 2, 4
            shim.flock = mock.Mock(side_effect=AssertionError("real flock out of test scope"))
            stack.enter_context(mock.patch.dict(sys.modules, {"fcntl": shim}))
        source = spec.loader.get_source(spec.name)
        exec(compile(source, str(HERE / (stem + ".py")), "exec", dont_inherit=True), module.__dict__)
    return module


def frame(ready=False):
    return dict(
        format="pult-construction-stop-ready-v1" if ready else "pult-construction-stop-reply-v1",
        status="CONSTRUCTED_HELD_NO_ADMISSION" if ready else "CONSTRUCTED_CLOSED_NO_ADMISSION",
        profile_sha256=PROFILE_SHA,
        host_profile_sha256=HOST_SHA,
        observation_sha256=OBSERVATION_SHA,
        write_admitted=False,
        listener_admitted=False,
        purge_or_restore_admitted=False,
    )


class SourceCase(unittest.TestCase):
    def setUp(self):
        self.stop = fresh_source("constructor_stop")
        self.child = fresh_source("constructor_child")
        # Accidental escape from any fake must fail before creating a process.
        self.process_guard = mock.patch.object(
            self.stop, "subprocess", SimpleNamespace(
                Popen=mock.Mock(side_effect=AssertionError("subprocess out of test scope")),
                run=mock.Mock(side_effect=AssertionError("subprocess out of test scope")),
            ),
        )
        self.process_guard.start()
        self.addCleanup(self.process_guard.stop)


class CanonicalDocumentTests(SourceCase):
    def test_canonical_golden_bytes_match_child(self):
        value = {"z": [True, False, None], "a": "\u00e9\n"}
        expected = b'{"a":"\\u00e9\\n","z":[true,false,null]}\n'
        for module in (self.stop, self.child):
            with self.subTest(module=module.__name__):
                self.assertIs(type(module.canonical(value)), bytes)
                self.assertEqual(module.canonical(value), expected)

    def test_canonical_is_independent_of_key_insertion_order(self):
        for module in (self.stop, self.child):
            self.assertEqual(module.canonical({"b": 2, "a": 1}), module.canonical({"a": 1, "b": 2}))

    def test_canonical_rejects_nonfinite_numbers(self):
        for module in (self.stop, self.child):
            for number in (float("nan"), float("inf"), float("-inf")):
                with self.subTest(module=module.__name__, number=repr(number)):
                    with self.assertRaises(ValueError):
                        module.canonical({"value": number})

    def test_canonical_rejects_non_json_values(self):
        for module in (self.stop, self.child):
            with self.subTest(module=module.__name__):
                with self.assertRaises(TypeError):
                    module.canonical({"value": object()})

    def test_document_round_trip_preserves_json_types(self):
        value = {"bool": False, "int": 0, "list": [], "null": None, "str": "0"}
        result = self.stop.document(self.stop.canonical(value))
        self.assertEqual(result, value)
        self.assertIs(type(result["bool"]), bool)
        self.assertIs(type(result["int"]), int)
        self.assertIs(type(result["str"]), str)

    def test_document_rejects_duplicate_top_level_fields(self):
        with self.assertRaisesRegex(self.stop.Refused, "duplicate"):
            self.stop.document(b'{"a":1,"a":2}\n')

    def test_document_rejects_duplicate_nested_fields(self):
        with self.assertRaisesRegex(self.stop.Refused, "duplicate"):
            self.stop.document(b'{"a":{"x":1,"x":2}}\n')

    def test_document_rejects_noncanonical_serializations(self):
        for raw in (b'{"b":2,"a":1}\n', b'{"a": 1}\n', b'{"a":1}',
                    b'{"a":1}\r\n', b'{"a":1}\n\n', b' {"a":1}\n',
                    b'{"a":1e0}\n', b'{"a":"\\u0061"}\n', '{"a":"\u00e9"}\n'.encode()):
            with self.subTest(raw=raw):
                with self.assertRaises(self.stop.Refused):
                    self.stop.document(raw)

    def test_document_rejects_non_object_roots(self):
        for value in (None, True, 0, "text", []):
            with self.subTest(value=value):
                with self.assertRaises(self.stop.Refused):
                    self.stop.document(self.stop.canonical(value))

    def test_document_rejects_malformed_and_multiple_documents(self):
        for raw in (b"", b"\xff\n", b"{\n", b"{}\n{}\n", b'{"x":NaN}\n'):
            with self.subTest(raw=raw):
                with self.assertRaises((ValueError, UnicodeError, self.stop.Refused)):
                    self.stop.document(raw)

    def test_digest_known_vector(self):
        self.assertEqual(self.stop.digest(b"abc"),
                         "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad")

    def test_import_refresh_isolates_attempt_and_failed_retention(self):
        self.stop._ATTEMPTED = True
        self.stop._FAILED.add(object())
        another = fresh_source("constructor_stop")
        self.assertIsNot(another, self.stop)
        self.assertFalse(another._ATTEMPTED)
        self.assertEqual(another._FAILED, set())


class PathTests(SourceCase):
    def test_normalized_safe_posix_paths(self):
        with mock.patch.object(self.stop, "Path", PurePosixPath):
            for value in ("/tmp/control", "/root/file_1.json", "/a-b/c.d"):
                with self.subTest(value=value):
                    self.assertEqual(str(self.stop.path(value)), value)

    def test_path_requires_exact_string_type(self):
        class StringSubclass(str):
            pass
        with mock.patch.object(self.stop, "Path", PurePosixPath):
            for value in (None, True, 1, b"/safe/path", PurePosixPath("/safe/path"), StringSubclass("/safe/path")):
                with self.subTest(value=repr(value)):
                    with self.assertRaises(self.stop.Refused):
                        self.stop.path(value)

    def test_path_rejects_relative_or_unsafe_characters(self):
        with mock.patch.object(self.stop, "Path", PurePosixPath):
            for value in ("", "relative", "C:/file", "/a b", "/a\\b", "/a\n", "/a\x00b", "/\u00e9", "/a:b"):
                with self.subTest(value=repr(value)):
                    with self.assertRaises(self.stop.Refused):
                        self.stop.path(value)

    def test_path_rejects_non_normalized_or_parent_components(self):
        with mock.patch.object(self.stop, "Path", PurePosixPath):
            for value in ("/", "//a", "/a//b", "/a/./b", "/a/../b", "/../a", "/a/", "/a/."):
                with self.subTest(value=value):
                    with self.assertRaises(self.stop.Refused):
                        self.stop.path(value)

    def test_native_windows_runtime_gate_remains_refused(self):
        if sys.platform != "win32":
            self.skipTest("native Windows runtime gate only")
        with self.assertRaises(self.stop.Refused):
            self.stop.runtime()
        self.assertEqual(self.stop._FAILED, set())
        self.assertFalse(self.stop._ATTEMPTED)


class FrameTests(SourceCase):
    def validators(self):
        return ((False, self.stop.valid_reply), (True, self.stop.valid_ready))

    def test_exact_reply_and_ready_are_valid(self):
        for ready, validate in self.validators():
            with self.subTest(ready=ready):
                self.assertIs(validate(self.stop.canonical(frame(ready)), PROFILE_SHA, HOST_SHA), True)

    def test_reply_and_ready_cannot_replace_each_other(self):
        for ready, validate in self.validators():
            self.assertIs(validate(self.stop.canonical(frame(not ready)), PROFILE_SHA, HOST_SHA), False)

    def test_missing_each_required_field_is_invalid(self):
        for ready, validate in self.validators():
            for key in frame(ready):
                with self.subTest(ready=ready, missing=key):
                    value = frame(ready)
                    del value[key]
                    self.assertIs(validate(self.stop.canonical(value), PROFILE_SHA, HOST_SHA), False)

    def test_extra_fields_are_invalid(self):
        for ready, validate in self.validators():
            for extra in ("extra", "version", "sha256", "write_allowed"):
                with self.subTest(ready=ready, extra=extra):
                    value = frame(ready)
                    value[extra] = False
                    self.assertIs(validate(self.stop.canonical(value), PROFILE_SHA, HOST_SHA), False)

    def test_admission_flags_require_false_identity(self):
        for ready, validate in self.validators():
            for key in FLAGS:
                for replacement in (True, 0, 1, None, "false", [], {}):
                    with self.subTest(ready=ready, key=key, value=replacement):
                        value = frame(ready)
                        value[key] = replacement
                        self.assertIs(validate(self.stop.canonical(value), PROFILE_SHA, HOST_SHA), False)

    def test_hash_fields_require_exact_lowercase_sha256(self):
        for ready, validate in self.validators():
            for key in ("profile_sha256", "host_profile_sha256", "observation_sha256"):
                for replacement in (None, False, 0, [], {}, "", "a" * 63, "a" * 65, "A" * 64, "g" * 64, "a" * 64 + "\n"):
                    with self.subTest(ready=ready, key=key, value=replacement):
                        value = frame(ready)
                        value[key] = replacement
                        self.assertIs(validate(self.stop.canonical(value), PROFILE_SHA, HOST_SHA), False)

    def test_profile_hashes_must_match_independent_expected_hashes(self):
        for ready, validate in self.validators():
            raw = self.stop.canonical(frame(ready))
            self.assertIs(validate(raw, "d" * 64, HOST_SHA), False)
            self.assertIs(validate(raw, PROFILE_SHA, "d" * 64), False)

    def test_expected_hashes_require_exact_string_type_and_grammar(self):
        class StringSubclass(str):
            pass
        invalid = (None, False, 0, b"a" * 64, "a" * 63, "A" * 64, "a" * 64 + "\n", StringSubclass(PROFILE_SHA))
        for ready, validate in self.validators():
            for value in invalid:
                with self.subTest(ready=ready, value=repr(value)):
                    raw = self.stop.canonical(frame(ready))
                    self.assertIs(validate(raw, value, HOST_SHA), False)
                    self.assertIs(validate(raw, PROFILE_SHA, value), False)

    def test_format_and_version_are_exact(self):
        for ready, validate in self.validators():
            for replacement in (None, False, 1, [], {}, "", frame(ready)["format"].replace("v1", "v2")):
                with self.subTest(ready=ready, value=replacement):
                    value = frame(ready)
                    value["format"] = replacement
                    self.assertIs(validate(self.stop.canonical(value), PROFILE_SHA, HOST_SHA), False)

    def test_status_is_exact_and_no_admission(self):
        for ready, validate in self.validators():
            for replacement in (None, False, 0, [], {}, "success", "CONSTRUCTED", frame(not ready)["status"]):
                with self.subTest(ready=ready, value=replacement):
                    value = frame(ready)
                    value["status"] = replacement
                    self.assertIs(validate(self.stop.canonical(value), PROFILE_SHA, HOST_SHA), False)

    def test_duplicate_fields_are_invalid_even_when_values_match(self):
        for ready, validate in self.validators():
            raw = self.stop.canonical(frame(ready))
            duplicate = b'{"write_admitted":false,' + raw[1:]
            self.assertIs(validate(duplicate, PROFILE_SHA, HOST_SHA), False)

    def test_noncanonical_frames_are_invalid(self):
        for ready, validate in self.validators():
            value = frame(ready)
            raw = self.stop.canonical(value)
            variants = (raw[:-1], raw + b"\n", raw.replace(b":", b": ", 1),
                        json.dumps(value).encode() + b"\n", raw.replace(b"\n", b"\r\n"))
            for variant in variants:
                with self.subTest(ready=ready, raw=variant):
                    self.assertIs(validate(variant, PROFILE_SHA, HOST_SHA), False)

    def test_bounded_frames_reject_empty_oversize_and_concatenation(self):
        for ready, validate in self.validators():
            raw = self.stop.canonical(frame(ready))
            for variant in (b"", b" " * 2048, b" " * 2049, raw + raw,
                            self.stop.canonical(dict(frame(ready), padding="x" * 2049))):
                with self.subTest(ready=ready, length=len(variant)):
                    self.assertIs(validate(variant, PROFILE_SHA, HOST_SHA), False)

    def test_reply_checks_2048_bound_before_parsing(self):
        with mock.patch.object(self.stop, "document", side_effect=AssertionError("must not parse oversize")) as parse:
            self.assertIs(self.stop.valid_reply(b"x" * 2049, PROFILE_SHA, HOST_SHA), False)
            parse.assert_not_called()

    def test_reply_allows_exact_2048_transport_bound_to_reach_parser(self):
        # No valid schema can pad to 2048; isolate only this transport boundary.
        with mock.patch.object(self.stop, "document", return_value=frame()) as parse:
            self.assertIs(self.stop.valid_reply(b"x" * 2048, PROFILE_SHA, HOST_SHA), True)
            parse.assert_called_once_with(b"x" * 2048)

    def test_malformed_frames_return_false_without_raising(self):
        for ready, validate in self.validators():
            for raw in (b"\xff\n", b"{", b"[]\n", b"null\n", b'{"a":NaN}\n', b"[" * 1500):
                with self.subTest(ready=ready, raw=raw[:30]):
                    self.assertIs(validate(raw, PROFILE_SHA, HOST_SHA), False)

    def test_reply_requires_exact_bytes_type(self):
        class BytesSubclass(bytes):
            pass
        raw = self.stop.canonical(frame())
        for variant in (None, 0, raw.decode(), bytearray(raw), memoryview(raw), BytesSubclass(raw)):
            with self.subTest(type=type(variant).__name__):
                self.assertIs(self.stop.valid_reply(variant, PROFILE_SHA, HOST_SHA), False)

    def test_ready_requires_exact_bytes_type(self):
        class BytesSubclass(bytes):
            pass
        raw = self.stop.canonical(frame(True))
        for variant in (None, 0, raw.decode(), bytearray(raw), memoryview(raw), BytesSubclass(raw)):
            with self.subTest(type=type(variant).__name__):
                self.assertIs(self.stop.valid_ready(variant, PROFILE_SHA, HOST_SHA), False)


class ManagerViewTests(SourceCase):
    def manager_os(self, control_identity, fence_identity=None):
        infos = {11: SimpleNamespace(**control_identity)}
        if fence_identity is not None:
            infos[12] = SimpleNamespace(**fence_identity)
            infos[99] = SimpleNamespace(st_dev=7, st_ino=30)
        return SimpleNamespace(
            O_RDONLY=0, O_DIRECTORY=0x10000, O_CLOEXEC=0x80000,
            O_NOFOLLOW=0x20000, O_NONBLOCK=0x800,
            open=mock.Mock(side_effect=[10, 11, 12]),
            fstat=mock.Mock(side_effect=infos.__getitem__), close=mock.Mock(),
        )

    def test_identity_is_device_and_inode_only(self):
        self.assertEqual(self.stop.identity(SimpleNamespace(st_dev=7, st_ino=20, st_size=0)),
                         {"device": 7, "inode": 20})

    def test_control_device_or_inode_mismatch_refuses_and_closes(self):
        for dev, ino in ((8, 20), (7, 21)):
            with self.subTest(device=dev, inode=ino):
                fake_os = self.manager_os(dict(st_dev=dev, st_ino=ino))
                with mock.patch.object(self.stop, "os", fake_os), mock.patch.object(self.stop, "Path", PurePosixPath):
                    with self.assertRaisesRegex(self.stop.Refused, "control view differs"):
                        self.stop.manager_view("/control", {"device": 7, "inode": 20}, 99)
                self.assertEqual(fake_os.close.call_args_list, [mock.call(10), mock.call(11)])
                self.assertEqual(fake_os.open.call_count, 2)
                self.assertEqual(fake_os.open.call_args_list, [
                    mock.call("/proc/1/root", fake_os.O_DIRECTORY | fake_os.O_CLOEXEC),
                    mock.call("control", fake_os.O_DIRECTORY | fake_os.O_NOFOLLOW | fake_os.O_CLOEXEC, dir_fd=10),
                ])

    def test_fence_device_or_inode_mismatch_is_unknown_and_closes(self):
        for dev, ino in ((8, 30), (7, 31)):
            with self.subTest(device=dev, inode=ino):
                fake_os = self.manager_os(dict(st_dev=7, st_ino=20), dict(st_dev=dev, st_ino=ino))
                with mock.patch.object(self.stop, "os", fake_os), mock.patch.object(self.stop, "Path", PurePosixPath):
                    with self.assertRaisesRegex(self.stop.Unknown, "fence view differs"):
                        self.stop.manager_view("/control", {"device": 7, "inode": 20}, 99)
                self.assertEqual(fake_os.close.call_args_list, [mock.call(10), mock.call(12), mock.call(11)])
                fake_os.open.assert_called_with(self.stop.FENCE,
                    fake_os.O_NOFOLLOW | fake_os.O_NONBLOCK | fake_os.O_CLOEXEC, dir_fd=11)
                self.assertNotIn(mock.call(99), fake_os.close.call_args_list)

    def test_component_open_failure_closes_parent_and_propagates(self):
        fake_os = self.manager_os(dict(st_dev=7, st_ino=20))
        original = OSError("component unavailable")
        fake_os.open.side_effect = [10, original]
        with mock.patch.object(self.stop, "os", fake_os), mock.patch.object(self.stop, "Path", PurePosixPath):
            with self.assertRaises(OSError) as caught:
                self.stop.manager_view("/control", {"device": 7, "inode": 20})
        self.assertIs(caught.exception, original)
        fake_os.close.assert_called_once_with(10)

    @unittest.skipUnless(LINUX_ROOT, "real /proc/1/root identity requires Linux root")
    def test_real_manager_control_identity(self):
        with tempfile.TemporaryDirectory() as directory:
            control = Path(directory)
            self.assertIsNone(self.stop.manager_view(control, self.stop.identity(control.stat())))

    @unittest.skipUnless(LINUX_ROOT, "real /proc/1/root identity requires Linux root")
    def test_real_manager_control_mismatch(self):
        with tempfile.TemporaryDirectory() as directory:
            control = Path(directory)
            wanted = self.stop.identity(control.stat())
            wanted["inode"] += 1
            with self.assertRaises(self.stop.Refused):
                self.stop.manager_view(control, wanted)

    @unittest.skipUnless(LINUX_ROOT, "real /proc/1/root identity requires Linux root")
    def test_real_manager_fence_identity(self):
        with tempfile.TemporaryDirectory() as directory:
            control = Path(directory)
            with (control / self.stop.FENCE).open("xb") as fence:
                self.assertIsNone(self.stop.manager_view(control, self.stop.identity(control.stat()), fence.fileno()))

    @unittest.skipUnless(LINUX_ROOT, "real /proc/1/root identity requires Linux root")
    def test_real_manager_fence_mismatch(self):
        with tempfile.TemporaryDirectory() as directory:
            control = Path(directory)
            with (control / self.stop.FENCE).open("xb"), (control / "other").open("xb") as other:
                with self.assertRaises(self.stop.Unknown):
                    self.stop.manager_view(control, self.stop.identity(control.stat()), other.fileno())


class BoundContractTests(SourceCase):
    def fake(self, *, fail_at=None, error=None, submitted=True):
        """Trusted orchestration fake: never validates roots or executes code."""
        module = self.stop

        class TrustedBound(module.Bound):
            def __init__(self):
                super().__init__(PurePosixPath("/unused/profile"), b"{}\n", {}, None)
                self.events = []
                self.answer = b"trusted-result"

            def step(self, name):
                self.events.append(name)
                if fail_at == name:
                    raise error

            def _validate(self):
                self.step("validate")

            def _claim(self):
                self.step("claim")

            def _execute(self):
                self.submitted = submitted
                self.step("execute")
                return self.answer

            def close(self):
                self.step("close")
                self.closed = True

        return TrustedBound()

    def assert_retained(self, bound, original):
        self.assertTrue(bound.failed)
        self.assertFalse(bound.closed)
        self.assertIs(bound._failure, original)
        self.assertIn(bound, self.stop._FAILED)
        self.assertTrue(self.stop._ATTEMPTED)

    def test_success_calls_stages_in_order_and_closes(self):
        bound = self.fake()
        self.assertIs(bound.run(), bound.answer)
        self.assertEqual(bound.events, ["validate", "claim", "execute", "close"])
        self.assertTrue(bound.closed)
        self.assertFalse(bound.failed)
        self.assertIsNone(bound._failure)
        self.assertEqual(self.stop._FAILED, set())

    def test_second_run_same_object_is_refused_without_reexecution(self):
        bound = self.fake()
        bound.run()
        events = list(bound.events)
        with self.assertRaises(self.stop.Refused):
            bound.run()
        self.assertEqual(bound.events, events)
        self.assertFalse(bound.failed)
        self.assertEqual(self.stop._FAILED, set())

    def test_second_object_is_refused_without_validation(self):
        self.fake().run()
        another = self.fake()
        with self.assertRaises(self.stop.Refused):
            another.run()
        self.assertEqual(another.events, [])
        self.assertFalse(another.failed)
        self.assertIsNone(another._failure)

    def test_validate_failure_is_refused_and_retains_original(self):
        original = ValueError("private validation detail")
        bound = self.fake(fail_at="validate", error=original)
        with self.assertRaises(self.stop.Refused) as caught:
            bound.run()
        self.assertNotIn(str(original), str(caught.exception))
        self.assertIsNone(caught.exception.__cause__)
        self.assertTrue(caught.exception.__suppress_context__)
        self.assertEqual(bound.events, ["validate"])
        self.assert_retained(bound, original)

    def test_claim_failure_is_refused_even_if_original_unknown(self):
        original = self.stop.Unknown("claim incomplete")
        bound = self.fake(fail_at="claim", error=original)
        with self.assertRaises(self.stop.Refused):
            bound.run()
        self.assertEqual(bound.events, ["validate", "claim"])
        self.assertFalse(bound.submitted)
        self.assert_retained(bound, original)

    def test_pre_submit_execute_failure_is_refused(self):
        original = OSError("not submitted")
        bound = self.fake(fail_at="execute", error=original, submitted=False)
        with self.assertRaises(self.stop.Refused):
            bound.run()
        self.assertEqual(bound.events, ["validate", "claim", "execute"])
        self.assert_retained(bound, original)

    def test_post_submit_execute_failure_is_unknown(self):
        original = self.stop.Refused("gate changed after submission")
        bound = self.fake(fail_at="execute", error=original)
        with self.assertRaises(self.stop.Unknown) as caught:
            bound.run()
        self.assertNotIn(str(original), str(caught.exception))
        self.assertIsNone(caught.exception.__cause__)
        self.assertTrue(caught.exception.__suppress_context__)
        self.assertEqual(bound.events, ["validate", "claim", "execute"])
        self.assert_retained(bound, original)

    def test_close_failure_after_submit_is_unknown_and_retained(self):
        original = OSError("close did not confirm")
        bound = self.fake(fail_at="close", error=original)
        with self.assertRaises(self.stop.Unknown):
            bound.run()
        self.assertEqual(bound.events, ["validate", "claim", "execute", "close"])
        self.assert_retained(bound, original)

    def test_failed_attempt_permanently_consumes_one_attempt(self):
        original = ValueError("refusal")
        bound = self.fake(fail_at="validate", error=original)
        with self.assertRaises(self.stop.Refused):
            bound.run()
        another = self.fake()
        with self.assertRaises(self.stop.Refused):
            another.run()
        self.assertEqual(another.events, [])
        self.assertEqual(self.stop._FAILED, {bound})
        self.assertIs(bound._failure, original)

    def test_retry_does_not_overwrite_original_failure(self):
        original = OSError("first failure")
        bound = self.fake(fail_at="execute", error=original)
        with self.assertRaises(self.stop.Unknown):
            bound.run()
        events = list(bound.events)
        with self.assertRaises(self.stop.Refused):
            bound.run()
        self.assertEqual(bound.events, events)
        self.assert_retained(bound, original)

    def test_baseexception_is_propagated_unchanged_at_every_stage(self):
        class Halt(BaseException):
            pass
        for stage in ("validate", "claim", "execute", "close"):
            for error_type in (KeyboardInterrupt, SystemExit, GeneratorExit, Halt):
                with self.subTest(stage=stage, error=error_type.__name__):
                    # Each independent scenario gets its own process-global state.
                    self.stop = fresh_source("constructor_stop")
                    original = error_type("interrupt")
                    bound = self.fake(fail_at=stage, error=original)
                    with self.assertRaises(error_type) as caught:
                        bound.run()
                    self.assertIs(caught.exception, original)
                    self.assert_retained(bound, original)
                    self.assertEqual(bound.events[-1], stage)

    def test_real_close_on_failed_run_retains_original_failure(self):
        original = ValueError("original execution failure")
        bound = self.fake(fail_at="execute", error=original)
        with self.assertRaises(self.stop.Unknown):
            bound.run()
        with mock.patch.object(bound, "_fence_check", side_effect=AssertionError("failed close must not inspect")) as check:
            with self.assertRaises(self.stop.Unknown):
                self.stop.Bound.close(bound)
            check.assert_not_called()
        self.assert_retained(bound, original)

    def test_real_close_failure_is_retained_and_repeat_close_preserves_it(self):
        original = OSError("fence verification failure")
        bound = self.fake()
        with mock.patch.object(bound, "_fence_check", side_effect=original) as check:
            with self.assertRaises(OSError) as caught:
                self.stop.Bound.close(bound)
            self.assertIs(caught.exception, original)
            self.assertTrue(bound.failed)
            self.assertFalse(bound.closed)
            self.assertIn(bound, self.stop._FAILED)
            self.assertIs(bound._failure, original)
            with self.assertRaises(self.stop.Unknown):
                self.stop.Bound.close(bound)
            check.assert_called_once_with()
        self.assertIs(bound._failure, original)
        self.assertFalse(self.stop._ATTEMPTED)

    def test_real_close_propagates_baseexception_and_retains_it(self):
        original = KeyboardInterrupt("close interrupted")
        bound = self.fake()
        with mock.patch.object(bound, "_fence_check", side_effect=original):
            with self.assertRaises(KeyboardInterrupt) as caught:
                self.stop.Bound.close(bound)
        self.assertIs(caught.exception, original)
        self.assertIs(bound._failure, original)
        self.assertIn(bound, self.stop._FAILED)
        self.assertTrue(bound.failed)
        self.assertFalse(bound.closed)

    def test_real_close_is_idempotent_when_already_closed(self):
        bound = self.fake()
        bound.closed = True
        with mock.patch.object(bound, "_fence_check", side_effect=AssertionError("closed handle must not inspect")) as check:
            self.assertIsNone(self.stop.Bound.close(bound))
            check.assert_not_called()
        self.assertFalse(bound.failed)
        self.assertEqual(self.stop._FAILED, set())


def verifier():
    file = REPO / 'ops/backup/tests/custodian_v2/stop/verify_receipt.py'
    spec = importlib.util.spec_from_file_location('_stop_verifier_contract', file)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def proof_summary():
    v = verifier()
    hashes = {str(i): 'a' * 64 for i in range(45)}
    def condition():
        unit = 'pult-call-gate-' + 'a' * 32 + '.service'
        fence = '/tmp/synthetic/control/constructor-stop.claim'
        return dict(unit=unit, fence=fence, record=dict(UNIT=unit, _PID='1', JOB_TYPE='start', JOB_RESULT='done',
            MESSAGE_ID='39f53479d3a045ac8e11786248231fbf',
            MESSAGE=unit + ' - synthetic was skipped because of an unmet condition check (ConditionPathExists=!' + fence + ').'))
    rows = [dict(case=name, outcome='PASS', seconds=42 if name in ('freeze', 'tail') else 1,
        database_unchanged=True, credential_unchanged=True, owner_lock_released=True,
        descendants_empty=True, construction_held_proved=name in v.HELD, permanent_fence=name in v.FENCED,
        kernel_fence_receipt=condition() if name in ('happy', 'late_launch') else None)
        for name in sorted(v.CASES)]
    return dict(format='pult-stop-bound-construction-native-v1', scope='LOCAL_SYNTHETIC_CONSTRUCTION_AND_STOP_ONLY',
        outcome='PASS', tests=21, skips=0, failures=[], results=rows,
        source_hashes_before=hashes, source_hashes_after=dict(hashes), sources_unchanged=True,
        private_network=True, native_tree_removed=True, remaining_owned_processes=[],
        journal_write_admitted=False, listener_admitted=False, deployment_admitted=False,
        remote_io_admitted=False, new_paid_resources=0, additional_spend_rub=0)


class ReceiptAndWorkflowTests(unittest.TestCase):
    def test_resource_failure_or_spoofed_log_is_not_a_fence_rejection(self):
        good = next(row['kernel_fence_receipt'] for row in proof_summary()['results'] if row['case'] == 'happy')
        v = verifier()
        self.assertTrue(v.valid_condition_receipt(good['record'], good['unit'], good['fence']))
        for field in good['record']:
            with self.subTest(field=field):
                changed = dict(good['record']); changed[field] = 'RESOURCE_FAILURE'
                self.assertFalse(v.valid_condition_receipt(changed, good['unit'], good['fence']))
        self.assertFalse(v.valid_condition_receipt({}, good['unit'], good['fence']))
        self.assertFalse(v.valid_condition_receipt(good['record'], 'other.service', good['fence']))
        self.assertFalse(v.valid_condition_receipt(good['record'], good['unit'], '/tmp/other/constructor-stop.claim'))

    def test_complete_shape_only_accepts_construction_stop_scope(self):
        self.assertTrue(verifier().valid_summary(proof_summary()))

    def test_all_semantic_evidence_is_mandatory(self):
        for case in verifier().CASES:
            for field in ('database_unchanged', 'credential_unchanged', 'owner_lock_released', 'descendants_empty',
                          'construction_held_proved', 'permanent_fence'):
                with self.subTest(case=case, field=field):
                    value = proof_summary()
                    row = next(r for r in value['results'] if r['case'] == case)
                    row[field] = not row[field]
                    self.assertFalse(verifier().valid_summary(value))

    def test_false_permissions_and_strict_zero_cost_fields(self):
        for field in ('journal_write_admitted', 'listener_admitted', 'deployment_admitted', 'remote_io_admitted'):
            for wrong in (True, 0, None):
                with self.subTest(field=field, wrong=wrong):
                    value = proof_summary(); value[field] = wrong
                    self.assertFalse(verifier().valid_summary(value))
        for field in ('skips', 'new_paid_resources', 'additional_spend_rub'):
            for wrong in (False, 1, 0.0):
                with self.subTest(field=field, wrong=wrong):
                    value = proof_summary(); value[field] = wrong
                    self.assertFalse(verifier().valid_summary(value))

    def test_incomplete_duplicate_extra_or_changed_proof_is_refused(self):
        for mutation in ('less', 'duplicate', 'extra', 'bool_count', 'hash', 'survivor', 'nan', 'short_timeout', 'wrong_scope'):
            with self.subTest(mutation=mutation):
                value = proof_summary()
                if mutation == 'less': value['results'].pop()
                elif mutation == 'duplicate': value['results'][0] = dict(value['results'][1])
                elif mutation == 'extra': value['operator_approval'] = True
                elif mutation == 'bool_count': value['tests'] = True
                elif mutation == 'hash': value['source_hashes_after']['0'] = 'b' * 64
                elif mutation == 'survivor': value['remaining_owned_processes'] = [123]
                elif mutation == 'nan': value['results'][0]['seconds'] = float('nan')
                elif mutation == 'short_timeout': next(r for r in value['results'] if r['case'] == 'freeze')['seconds'] = 0
                elif mutation == 'wrong_scope': value['scope'] = 'JOURNAL_WRITER_DEPLOYED'
                self.assertFalse(verifier().valid_summary(value))

    def test_draft_is_not_activation_and_pins_actual_sources(self):
        import hashlib
        value = json.loads((HERE / 'source-profile.json').read_bytes())
        self.assertEqual(value['status'], 'DRAFT_NOT_OPERATOR_APPROVED_OR_ACTIVATED')
        self.assertIsNone(value['operator_approval'])
        self.assertEqual(value['admitted_operations'], [])
        self.assertIs(value['working_credentials_included'], False)
        self.assertIs(value['installation_service_or_network_actions'], False)
        self.assertEqual(value['sources'], {name: hashlib.sha256((HERE / name).read_bytes()).hexdigest()
            for name in ('constructor_stop.py', 'constructor_child.py')})
        module = fresh_source('constructor_stop')
        self.assertEqual(module.HELPER_SHA, value['sources']['constructor_child.py'])
        self.assertEqual(module.MODE, 'STOP_BOUND_CONSTRUCTION_ONLY')

    def test_workflow_runs_native_gate_with_read_only_permissions(self):
        import re
        import yaml
        file = REPO / '.github/workflows/custodian_stop_binding.yml'
        text = file.read_text()
        doc = yaml.safe_load(text)
        self.assertEqual(doc['permissions'], {'contents': 'read'})
        self.assertEqual(set(doc['jobs']), {'construction-safe-stop'})
        job = doc['jobs']['construction-safe-stop']
        self.assertEqual(job['runs-on'], 'ubuntu-24.04')
        self.assertEqual(job['timeout-minutes'], 20)
        self.assertEqual(set(doc[True]), {'pull_request', 'push', 'workflow_dispatch'})
        self.assertEqual(doc[True]['push']['branches'], ['master'])
        for event in ('pull_request', 'push'):
            self.assertEqual(set(doc[True][event]['paths']), {'ops/backup/**', 'ops/canary/canary.py',
                'backend/tests/test_custodian_stop_binding_ci.py', '.github/workflows/custodian_stop_binding.yml', '.gitattributes'})
        for step in job['steps']:
            if 'uses' in step: self.assertRegex(step['uses'], r'@[0-9a-f]{40}$')
            self.assertNotIn('continue-on-error', step)
            self.assertNotIn('if', step)
        self.assertFalse(job['steps'][0]['with']['persist-credentials'])
        self.assertIn('sudo -n -- unshare --net /usr/bin/python3.12 -I -S -B ops/backup/tests/custodian_v2/stop/ci_gate.py --reviewed-by-main', text)
        self.assertIn('--only-binary=:all: --no-deps --require-hashes', text)
        for reason in ('optimized_stop_binding_ci_refused', 'optimized_stop_binding_gate_refused', 'optimized_stop_binding_verifier_refused'):
            self.assertIn(reason, text)
        self.assertNotRegex(text, r'(?i)(secrets\.|deploy|ssh |curl |wget |--privileged|chmod 777|pytest.*-k)')

    def test_native_matrix_and_independent_verifier_have_identical_cases(self):
        import ast
        file = REPO / 'ops/backup/tests/custodian_v2/stop/run_gate.py'
        tree = ast.parse(file.read_bytes())
        modes = next(ast.literal_eval(n.value) for n in tree.body if isinstance(n, ast.Assign)
            and any(isinstance(t, ast.Name) and t.id == 'MODES' for t in n.targets))
        self.assertEqual(len(modes), 21)
        self.assertEqual(set(modes), verifier().CASES)
        self.assertNotIn('/mnt/c/', file.read_text())
        self.assertNotIn('run_smoke', file.read_text())

    def test_primitives_and_constructor_stay_hash_pinned(self):
        import hashlib
        module = fresh_source('constructor_stop')
        for relative, wanted in (
            ('ops/backup/custodian_v2/host/staged_host.py', module.CONSTRUCTOR_SHA),
            ('ops/backup/custodian_v2/controller/supervisor_v2.py', module.SUPERVISOR_SHA),
            ('ops/backup/custodian_v2/controller/frozen/admission.py', module.CORE_SHA)):
            self.assertEqual(hashlib.sha256((REPO / relative).read_bytes()).hexdigest(), wanted)


if __name__ == '__main__':
    unittest.main(verbosity=2)
