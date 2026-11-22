"""Portable source-envelope negatives. NOT native protected-descriptor proof.

No loader/descriptor success is mocked. ZIP-only tests use in-memory archives
with a local synthetic hash catalogue to reach member validation; production
pins are independently checked and never edited. No sockets or subprocesses.
"""
import hashlib
import io
from pathlib import Path, PurePosixPath
import stat
import sys
from types import ModuleType, SimpleNamespace
import unittest
from unittest import mock
import zipfile

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))
BOOTSTRAP = REPO / "ops/backup/operator_pg/bootstrap.py"
RUNTIME = REPO / "ops/backup/operator_pg/runtime.py"
TOKEN = "0123456789abcdef0123456789abcdef"
SHA = "a" * 64
ACCEPTED = {
    "journal": ("ops/backup/erasure_journal.py", "94eeba19e645e25ec8286baebe4092a29c541c6f12858e1ff7b303f6a1a4fc98"),
    "anchor": ("ops/backup/erasure_anchor.py", "b399803ac7a03aaa5a506cbb0036f98c153457769beabf46af44864b190861a8"),
    "ipc": ("ops/backup/erasure_custodian.py", "3eb793a1ef1002c6def97b52c579b2ff051e834b44b802a75aca09d640a691d3"),
    "operator": ("ops/backup/erasure_operator.py", "c4be8ed53a803afe37cf6c72ac9fa462167ace9401575ee1f85faa2774a1365f"),
    "stop": ("ops/backup/custodian_v2/stop/constructor_stop.py", "5406c5a81498013884ac46d0279219f299db40576357692604fb4f08c251ca8c"),
    "supervisor": ("ops/backup/custodian_v2/controller/supervisor_v2.py", "3d05d22163b11f815b29c222128c4e41987c9e4a83e7b4906cbe63a18941a37e"),
    "core": ("ops/backup/custodian_v2/controller/frozen/admission.py", "961cc3aef5dcfe9d6286b15a2d5c087a33dc1e5dafdc7d5a16f6eded961afea7"),
    "profile": ("ops/backup/operator_host/profile.py", "0998c8922c470406d2789727b749ed9365f36f2fa3ee70eea7448d3693e31edf"),
    "host": ("ops/backup/operator_host/host.py", "5a47b32282fa4c6533ea0c4ed2602523dc2054cb8e567da571de3962b877e356"),
    "bootstrap": ("ops/backup/operator_host/bootstrap.py", "96141d0e26cfc718e9cb762f689d20cad90f22c815dadec587fcac8c7580d3fa"),
    "worker": ("ops/backup/operator_host/worker.py", "5e7993f35e9d50019af79ecb2335cb2fe5111f38f06c83f70fc01304d425a06c"),
    "policy": ("backend/services/account_closure_policy.py", "e606c8b81793a54caa94b1bf9d3c88a21dc7df0f211d60cca6b8a483d8e750cf"),
    "reviewer": ("backend/services/erasure_operator_review.py", "efaa87935a5e6df3fdb8077cabba1b41f0cbb725d11430e896c454483ccc2606"),
}
WHEELS = {
    "asyncpg-0.31.0-cp312-cp312-manylinux_2_28_x86_64.whl":
        "aad7a33913fb8bcb5454313377cc330fbb19a0cd5faa7272407d8a0c4257b671",
    "greenlet-3.5.5-cp312-cp312-manylinux_2_24_x86_64.manylinux_2_28_x86_64.whl":
        "147b25a42e5ca5be3d42356e8f608b37af715a1c196e9bf9d1627f3341adfe1d",
    "sqlalchemy-2.0.51-cp312-cp312-manylinux2014_x86_64.manylinux_2_17_x86_64.manylinux_2_28_x86_64.whl":
        "1d21ce524ab86c23046e992a5b81cb54c21079c6df6e78b8fc77d77cac70a6b9",
    "typing_extensions-4.16.0-py3-none-any.whl":
        "481caa481374e813c1b176ada14e97f1f67a4539ce9cfeb3f350d78d6370c2e8",
}


def fresh():
    module = ModuleType("_portable_pg_admission_contract")
    module.__file__ = str(BOOTSTRAP)
    exec(compile(BOOTSTRAP.read_bytes(), str(BOOTSTRAP), "exec", dont_inherit=True), module.__dict__)
    return module


def archive(rows):
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w") as result:
        for name, body in rows:
            result.writestr(name, body)
    return output.getvalue()


class AdmissionContracts(unittest.TestCase):
    def setUp(self):
        self.b = fresh()
        # Accidental IO must fail; pure tests never impersonate native capture.
        self.b.os = SimpleNamespace()

    def envelope(self):
        root = "/tmp/pult-op-pg-unit"
        return dict(
            format=self.b.FORMAT, mode=self.b.MODE,
            operation="38db967c-cd67-4d26-9e18-01b285a4dc5d",
            release=dict(root=root + "/release", identity=dict(device=1, inode=2)),
            legacy=dict(path=root + "/profile.json", sha256=SHA),
            sources={role: dict(path=root + "/release/" + relative,
                               sha256=self.b.PINS.get(role, SHA))
                     for role, relative in self.b.PATHS.items()},
            dependencies=dict(root=root + "/deps", identity=dict(device=1, inode=3),
                              wheels_root=root + "/wheels", wheels=dict(WHEELS)),
            binding=dict(path=root + "/binding.json", sha256=SHA),
        )

    def binding(self):
        return dict(format="pult-synthetic-pg-binding-v1", fixture_only=True,
            socket="/tmp/pult-op-pg-unit/pgsocket", database="op_pg_" + TOKEN,
            database_oid=16384, reader_role="op_reader_" + TOKEN,
            reader_password="synthetic_" + "f" * 32, application_name="op_review_" + TOKEN)

    def shape(self, spec):
        with mock.patch.object(self.b, "Path", PurePosixPath):
            return self.b.shape(spec)

    def binding_value(self, value):
        with mock.patch.object(self.b, "Path", PurePosixPath):
            return self.b._binding(self.b.canonical(value))

    def test_canonical_golden_ascii_sorted_and_single_lf(self):
        self.assertEqual(self.b.canonical({"z": False, "a": 1}), b'{"a":1,"z":false}\n')
        self.assertEqual(self.b.canonical({"x": "\u20ac"}), b'{"x":"\\u20ac"}\n')

    def test_document_roundtrip(self):
        value = self.envelope()
        self.assertEqual(self.b.document(self.b.canonical(value)), value)

    def test_duplicate_keys_rejected_at_every_depth(self):
        for raw in (b'{"a":1,"a":2}\n', b'{"nested":{"x":1,"x":2}}\n',
                    b'{"nested":[{"x":1,"x":2}]}\n'):
            with self.subTest(raw=raw), self.assertRaises(self.b.Refused):
                self.b.document(raw)

    def test_noncanonical_or_nonobject_document_rejected(self):
        for raw in (b'{}', b'{}\n\n', b'{ "a":1}\n', b'{"z":1,"a":2}\n',
                    b'[]\n', b'null\n', b'false\n', b'\xff', b'{"x": "\xe2\x82\xac"}\n'):
            with self.subTest(raw=raw), self.assertRaises((self.b.Refused, ValueError, UnicodeError)):
                self.b.document(raw)

    def test_nan_and_infinity_never_serialize_or_parse_as_canonical(self):
        for value in (float("nan"), float("inf"), -float("inf")):
            with self.subTest(value=value), self.assertRaises(ValueError):
                self.b.canonical({"x": value})
        for literal in (b"NaN", b"Infinity", b"-Infinity"):
            with self.subTest(literal=literal), self.assertRaises((self.b.Refused, ValueError)):
                self.b.document(b'{"x":' + literal + b'}\n')

    def test_exact_fifteen_roles_without_fixture_or_baseline(self):
        expected = {role: item[0] for role, item in ACCEPTED.items()}
        expected.update(pg_bootstrap="ops/backup/operator_pg/bootstrap.py",
                        pg_runtime="ops/backup/operator_pg/runtime.py")
        self.assertEqual(self.b.PATHS, expected)
        self.assertEqual(len(self.b.PATHS), 15)
        self.assertFalse(any("fixture" in path or "baseline" in path or "/tests/" in path
                             for path in self.b.PATHS.values()))

    def test_old_thirteen_accepted_pins_and_lf_sources_unchanged(self):
        self.assertEqual(self.b.PINS, {role: item[1] for role, item in ACCEPTED.items()})
        for role, (relative, wanted) in ACCEPTED.items():
            with self.subTest(role=role):
                body = (REPO / relative).read_bytes().replace(b"\r\n", b"\n")
                self.assertEqual(hashlib.sha256(body).hexdigest(), wanted)

    def test_exact_four_accepted_wheel_pins(self):
        self.assertEqual(self.b.WHEELS, WHEELS)
        self.assertEqual(len(self.b.WHEELS), 4)

    def test_pure_shape_accepts_only_fixture_envelope_not_io_proof(self):
        self.assertEqual(self.b.FORMAT, "pult-operator-pg-source-envelope-v1")
        self.assertEqual(self.b.MODE, "SYNTHETIC_PG_REVIEW_SOURCE_ONLY")
        result = self.shape(self.envelope())
        self.assertEqual(result, tuple(PurePosixPath("/tmp/pult-op-pg-unit") / name
                                       for name in ("release", "deps", "wheels")))

    def test_missing_and_extra_envelope_fields_refused(self):
        for field in self.b.FIELDS:
            value = self.envelope()
            del value[field]
            with self.subTest(missing=field), self.assertRaises(self.b.Refused):
                self.shape(value)
        value = self.envelope()
        value["approved"] = True
        with self.assertRaises(self.b.Refused):
            self.shape(value)

    def test_legacy_or_live_format_mode_refused(self):
        for field, bad in (("format", "pult-offline-operator-host-v1"),
                           ("mode", "SYNTHETIC_REVIEW_ENDPOINT_ONLY"), ("mode", "LIVE_PG")):
            value = self.envelope()
            value[field] = bad
            with self.subTest(field=field, bad=bad), self.assertRaises(self.b.Refused):
                self.shape(value)

    def test_missing_extra_source_roles_refused(self):
        for role in self.b.PATHS:
            value = self.envelope()
            del value["sources"][role]
            with self.subTest(role=role), self.assertRaises(self.b.Refused):
                self.shape(value)
        for role in ("fixture", "baseline_helper"):
            value = self.envelope()
            value["sources"][role] = dict(path="/tmp/forbidden.py", sha256=SHA)
            with self.subTest(extra=role), self.assertRaises(self.b.Refused):
                self.shape(value)

    def test_frozen_hash_substitution_refused_for_every_old_role(self):
        for role in ACCEPTED:
            value = self.envelope()
            value["sources"][role]["sha256"] = "0" * 64
            with self.subTest(role=role), self.assertRaises(self.b.Refused):
                self.shape(value)

    def test_source_path_and_row_field_substitution_refused(self):
        for change in ({"path": "/tmp/foreign.py"}, {"approved": True}, {"sha256": "A" * 64}):
            value = self.envelope()
            value["sources"]["pg_runtime"].update(change)
            with self.subTest(change=change), self.assertRaises(self.b.Refused):
                self.shape(value)

    def test_protected_reference_shapes_refuse_missing_or_extra_fields(self):
        for field in ("release", "legacy", "dependencies", "binding"):
            for mode in ("missing", "extra"):
                value = self.envelope()
                if mode == "missing":
                    value[field].pop(next(iter(value[field])))
                else:
                    value[field]["url"] = "forbidden"
                with self.subTest(field=field, mode=mode), self.assertRaises(self.b.Refused):
                    self.shape(value)

    def test_dependency_and_release_roots_cannot_alias_or_nest(self):
        for field, bad in (("root", "/tmp/pult-op-pg-unit/release"),
                           ("root", "/tmp/pult-op-pg-unit/release/nested"),
                           ("root", "/tmp/pult-op-pg-unit"),
                           ("wheels_root", "/tmp/pult-op-pg-unit/deps")):
            value = self.envelope()
            value["dependencies"][field] = bad
            with self.subTest(field=field, bad=bad), self.assertRaises(self.b.Refused):
                self.shape(value)

    def test_wheel_catalogue_missing_extra_or_repinned_refused(self):
        for mode in ("missing", "extra", "repin"):
            value = self.envelope()
            wheels = value["dependencies"]["wheels"]
            if mode == "missing":
                wheels.pop(next(iter(wheels)))
            elif mode == "extra":
                wheels["extra.whl"] = SHA
            else:
                wheels[next(iter(wheels))] = "0" * 64
            with self.subTest(mode=mode), self.assertRaises(self.b.Refused):
                self.shape(value)

    def test_paths_refuse_url_tcp_relative_and_unnormalized_inputs(self):
        for bad in (None, True, "relative", "postgresql://host/db", "/tmp/a/../b",
                    "/tmp//b", "/tmp/./b", "/tmp/b/", "C:/tmp/b", "/tmp/a\\b", "/tmp/a b"):
            with self.subTest(path=bad), mock.patch.object(self.b, "Path", PurePosixPath), \
                 self.assertRaises(self.b.Refused):
                self.b.path(bad)

    def test_binding_returns_only_six_fixed_pg_fields(self):
        value = self.binding()
        self.assertEqual(self.binding_value(value), {key: value[key] for key in (
            "socket", "database", "database_oid", "reader_role", "reader_password", "application_name")})

    def test_binding_cannot_add_alias_authority_or_transport_fields(self):
        for field in ("dsn", "database_url", "url", "host", "port", "user", "password",
                      "uid", "approved", "ssl", "query", "connect_args"):
            value = self.binding()
            value[field] = "forbidden"
            with self.subTest(field=field), self.assertRaises(self.b.Refused):
                self.binding_value(value)
        for field in self.binding():
            value = self.binding()
            value.pop(field)
            with self.subTest(missing=field), self.assertRaises(self.b.Refused):
                self.binding_value(value)

    def test_binding_requires_fixture_only_true_and_exact_format(self):
        for field, bad in (("fixture_only", False), ("fixture_only", 1),
                           ("format", self.b.FORMAT)):
            value = self.binding()
            value[field] = bad
            with self.subTest(field=field, bad=bad), self.assertRaises(self.b.Refused):
                self.binding_value(value)

    def test_binding_database_role_and_application_are_same_token(self):
        for field, bad in (("database", "production"), ("database", "op_pg_" + "A" * 32),
                           ("database", "op_pg_" + TOKEN + ";DROP DATABASE x"),
                           ("reader_role", "op_reader_" + "f" * 32),
                           ("reader_role", "postgres"), ("application_name", "op_review_" + "f" * 32)):
            value = self.binding()
            value[field] = bad
            with self.subTest(field=field, bad=bad), self.assertRaises(self.b.Refused):
                self.binding_value(value)

    def test_binding_password_is_synthetic_lowercase_hex_only(self):
        for bad in (None, True, "working-password", "synthetic_" + "A" * 32,
                    "synthetic_" + "a" * 31, "synthetic_" + "a" * 32 + "\n"):
            value = self.binding()
            value["reader_password"] = bad
            with self.subTest(password_type=type(bad).__name__), self.assertRaises(self.b.Refused):
                self.binding_value(value)

    def test_binding_oid_requires_nonboolean_pg_database_oid(self):
        for bad in (True, False, "16384", 16383, 2**32, None):
            value = self.binding()
            value["database_oid"] = bad
            with self.subTest(oid=bad), self.assertRaises(self.b.Refused):
                self.binding_value(value)

    def test_binding_socket_is_exact_native_fixture_child_only(self):
        for bad in ("/tmp/pgsocket", "/tmp/not-owned/pgsocket", "/tmp/pult-op-pg-/pgsocket",
                    "/tmp/pult-op-pg-unit/nested/pgsocket", "/var/run/postgresql",
                    "/tmp/pult-op-pg-unit/other", "127.0.0.1", "localhost", "/tmp/pult-op-pg-unit/../pgsocket"):
            value = self.binding()
            value["socket"] = bad
            with self.subTest(socket=bad), self.assertRaises(self.b.Refused):
                self.binding_value(value)

    def test_real_wheel_pin_mismatch_refused_before_archive_parse(self):
        bodies = {name: b"not an approved wheel" for name in WHEELS}
        with mock.patch.object(self.b, "zipfile", SimpleNamespace(ZipFile=mock.Mock())) as parser:
            with self.assertRaises(self.b.Refused):
                self.b.wheel_inventory(bodies)
            parser.ZipFile.assert_not_called()

    def _zip_bodies(self, first=None, second=None):
        bodies = {}
        for index, name in enumerate(WHEELS):
            rows = [("pkg" + str(index) + "/module.py", b"# memory only\n")]
            if index == 0 and first is not None:
                rows = first
            if index == 1 and second is not None:
                rows = second
            bodies[name] = archive(rows)
        return bodies

    def _inventory_memory_only(self, bodies):
        # Isolate ZIP traversal, not admission of unapproved wheels/dependencies.
        pins = {name: hashlib.sha256(body).hexdigest() for name, body in bodies.items()}
        with mock.patch.object(self.b, "WHEELS", pins):
            return self.b.wheel_inventory(bodies)

    def test_memory_archive_inventory_hashes_no_filesystem_proof(self):
        result = self._inventory_memory_only(self._zip_bodies())
        self.assertEqual(set(result), {"pkg" + str(i) + "/module.py" for i in range(4)})
        self.assertEqual(set(result.values()), {hashlib.sha256(b"# memory only\n").hexdigest()})

    def test_directory_inventory_is_derived_from_approved_archive_members_only(self):
        bodies = self._zip_bodies(first=[("pkg0/nested/module.py", b"# memory only\n"),
                                        ("approved_empty/", b"")])
        pins = {name: hashlib.sha256(body).hexdigest() for name, body in bodies.items()}
        with mock.patch.object(self.b, "WHEELS", pins):
            files, directories = self.b.wheel_inventory(bodies, with_directories=True)
        self.assertEqual(directories, {"pkg0", "pkg0/nested", "approved_empty", "pkg1", "pkg2", "pkg3"})
        self.assertNotIn("unapproved_namespace", directories)
        self.assertEqual(len(files), 4)

    def test_archive_traversal_absolute_and_noncanonical_names_refused(self):
        for bad in ("../outside.py", "/outside.py", "pkg/../outside.py", "./pkg/x.py",
                    "pkg//x.py"):
            bodies = self._zip_bodies(first=[(bad, b"x")])
            with self.subTest(member=bad), self.assertRaises(self.b.Refused):
                self._inventory_memory_only(bodies)

    def test_archive_bytecode_and_pth_refused(self):
        for bad in ("pkg/x.pyc", "activate.pth"):
            bodies = self._zip_bodies(first=[(bad, b"import forbidden")])
            with self.subTest(member=bad), self.assertRaises(self.b.Refused):
                self._inventory_memory_only(bodies)

    def test_archive_symlink_refused(self):
        info = zipfile.ZipInfo("pkg/link.py")
        info.create_system = 3
        info.external_attr = (stat.S_IFLNK | 0o777) << 16
        with self.assertRaises(self.b.Refused):
            self._inventory_memory_only(self._zip_bodies(first=[(info, b"../../outside")]))

    def test_archive_cross_wheel_collision_refused(self):
        bodies = self._zip_bodies(first=[("shared.py", b"one")], second=[("shared.py", b"two")])
        with self.assertRaises(self.b.Refused):
            self._inventory_memory_only(bodies)

    def test_empty_archive_closure_refused(self):
        with self.assertRaises(self.b.Refused):
            self._inventory_memory_only({name: archive([]) for name in WHEELS})

    def test_encrypted_or_oversize_member_refused_before_read(self):
        for encrypted, size in ((1, 1), (0, 50000001)):
            info = zipfile.ZipInfo("package.py")
            info.flag_bits, info.file_size = encrypted, size
            with self.subTest(encrypted=encrypted, size=size):
                self._reject_member_before_read(info)

    def test_archive_original_backslash_member_refused_before_read(self):
        # Windows ZipInfo construction normalizes os.sep. Set the metadata AFTER
        # construction to exercise the exact raw name Linux admission would see.
        info = zipfile.ZipInfo("package.py")
        info.filename = "pkg\\x.py"
        self._reject_member_before_read(info)

    def _reject_member_before_read(self, info):
        fake = mock.MagicMock()
        fake.__enter__.return_value = fake
        fake.infolist.return_value = [info]
        fake.read.side_effect = AssertionError("invalid member must never be read")
        bodies = {name: b"memory archive marker" for name in WHEELS}
        pins = {name: hashlib.sha256(body).hexdigest() for name, body in bodies.items()}
        with mock.patch.object(self.b, "WHEELS", pins), \
             mock.patch.object(self.b, "zipfile", SimpleNamespace(ZipFile=lambda unused: fake)), \
             self.assertRaises(self.b.Refused):
            self.b.wheel_inventory(bodies)
        fake.read.assert_not_called()

    def test_loader_denial_hides_exception_context_without_claiming_io_success(self):
        with mock.patch.object(self.b, "_load", side_effect=RuntimeError("synthetic-private-detail")):
            with self.assertRaises(self.b.Refused) as caught:
                self.b.load_admission("/not-opened", SHA)
        self.assertEqual(str(caught.exception), "synthetic_pg_source_refused")
        self.assertIsNone(caught.exception.__cause__)
        self.assertIsNone(caught.exception.__context__)

    def test_admission_cannot_be_constructed_with_untrusted_token(self):
        with self.assertRaises(self.b.Refused):
            self.b.Admission(object(), file=None, approved=SHA, raw=b"", spec={}, blobs={},
                files={}, directories={}, deps=None, binding={}, legacy=None)

    def test_shared_sticky_directory_churn_never_relaxes_protection_or_file_stamps(self):
        fields = dict(st_dev=1, st_ino=2, st_mode=stat.S_IFDIR|0o1777, st_uid=0,
                      st_gid=0, st_nlink=3, st_size=100, st_mtime_ns=10, st_ctime_ns=20)
        before = SimpleNamespace(**fields)
        changed = SimpleNamespace(**dict(fields, st_nlink=4, st_size=200, st_mtime_ns=11, st_ctime_ns=21))
        self.assertEqual(self.b.capture_stamp(before), self.b.capture_stamp(changed))
        for key, value in (("st_dev", 2), ("st_ino", 3), ("st_uid", 1000),
                           ("st_gid", 1000), ("st_mode", stat.S_IFDIR|0o0777)):
            with self.subTest(protection=key):
                self.assertNotEqual(self.b.capture_stamp(before),
                                    self.b.capture_stamp(SimpleNamespace(**dict(fields, **{key:value}))))
        for mode in (stat.S_IFDIR|0o755, stat.S_IFREG|0o444):
            own = SimpleNamespace(**dict(fields, st_mode=mode))
            mutated = SimpleNamespace(**dict(fields, st_mode=mode, st_mtime_ns=11))
            self.assertNotEqual(self.b.capture_stamp(own), self.b.capture_stamp(mutated))


class CaptureLifecycleContracts(unittest.TestCase):
    """Fake FD ownership mechanics only, not native filesystem protection proof."""
    def setUp(self):
        self.b = fresh()
        self.state = dict(pid=101, closed=[])
        self.info = {fd: SimpleNamespace(st_dev=1, st_ino=100+fd) for fd in (3,4)}
        self.b.os = SimpleNamespace(getpid=lambda:self.state["pid"],
            fstat=lambda fd:self.info[fd], close=lambda fd:self.state["closed"].append(fd))

    def guard(self):
        value = self.b.Capture()
        value.entries = [(fd,None,"fake",self.info[fd]) for fd in (3,4)]
        return value

    def test_owned_close_once_then_closed_guard_refuses(self):
        value = self.guard()
        value.close()
        self.assertEqual(self.state["closed"], [4,3])
        value.close()
        self.assertEqual(self.state["closed"], [4,3])
        self.assertTrue(value.closed)
        self.assertFalse(value.close_failed)
        with self.assertRaises(self.b.Refused):
            value.check()

    def test_different_object_fd_is_not_closed_and_failure_remains_poisoned(self):
        value = self.guard()
        self.info[4] = SimpleNamespace(st_dev=1,st_ino=999)
        with self.assertRaises(self.b.Refused):
            value.close()
        self.assertEqual(self.state["closed"], [3])
        with self.assertRaises(self.b.Refused):
            value.close()
        self.assertEqual(self.state["closed"], [3])
        self.assertTrue(value.close_failed)

    def test_uncertain_close_is_fixed_error_without_automatic_retry(self):
        def fail(fd):
            self.state["closed"].append(fd)
            raise OSError("synthetic-private-error-value")
        self.b.os.close = fail
        value = self.guard()
        with self.assertRaises(self.b.Refused) as caught:
            value.close()
        self.assertEqual(str(caught.exception), "synthetic_pg_source_refused")
        self.assertIsNone(caught.exception.__context__)
        with self.assertRaises(self.b.Refused):
            value.close()
        self.assertEqual(self.state["closed"], [4,3])

    def test_transfer_moves_single_owner_and_rejects_foreign_process_close(self):
        value = self.guard()
        value.check = mock.Mock()  # Ownership movement, explicitly NOT FD validation.
        held = value.transfer()
        self.assertFalse(value.entries)
        value.close()
        self.assertFalse(self.state["closed"])
        self.state["pid"] = 202
        with self.assertRaises(self.b.Refused):
            held.close()
        self.assertFalse(self.state["closed"])
        self.state["pid"] = 101
        held.close()
        self.assertEqual(self.state["closed"], [4,3])

    def test_content_hash_refuses_changed_bytes_even_when_metadata_matches(self):
        before = SimpleNamespace(st_dev=1, st_ino=103, st_mode=stat.S_IFREG|0o444,
            st_uid=0, st_gid=0, st_nlink=1, st_size=8, st_mtime_ns=10, st_ctime_ns=20)
        self.info[3] = before
        self.b.os.stat = lambda *args, **kwargs: before
        self.b.os.listxattr = lambda fd: ()
        self.b.os.pread = lambda *args: b"tampered"
        value = self.b.Capture()
        value.entries = [(3,None,"fake",before)]
        value.content_pins[3] = (8,hashlib.sha256(b"original").hexdigest())
        with self.assertRaises(self.b.Refused):
            value.check()
        value.close()


class RuntimeContracts(unittest.TestCase):
    def setUp(self):
        self.r = ModuleType("_portable_pg_fixed_dispatch")
        self.r.__file__ = str(RUNTIME)
        exec(compile(RUNTIME.read_bytes(), str(RUNTIME), "exec", dont_inherit=True), self.r.__dict__)
        self.legacy = SimpleNamespace(sha=SHA, spec={"operation": "synthetic-operation"})
        self.unit = "pult-call-gate-"+TOKEN+".service"

    def receipt(self):
        return dict(format="pult-offline-operator-supervision-v1", approval=SHA,
            operation="synthetic-operation", unit=self.unit,
            status="OFFLINE_REVIEW_STOP_CONFIRMED_NO_ADMISSION", observation_sha256="f"*64,
            **dict.fromkeys(self.r.CLEANUP, True), **dict.fromkeys(self.r.FLAGS, False))

    def test_exact_legacy_receipt_is_only_inner_test_supervision(self):
        self.assertIs(self.r._legacy_success(self.receipt(), self.legacy, self.unit), True)

    def test_every_cleanup_boolean_and_mutation_flag_is_exact(self):
        for key in self.r.CLEANUP+self.r.FLAGS:
            for bad in (None, 0, 1, "true", not self.receipt()[key]):
                value = self.receipt()
                value[key] = bad
                with self.subTest(key=key, bad=bad):
                    self.assertFalse(self.r._legacy_success(value, self.legacy, self.unit))

    def test_missing_extra_unknown_or_unbound_receipt_never_succeeds(self):
        for key in self.receipt():
            value = self.receipt()
            del value[key]
            with self.subTest(missing=key):
                self.assertFalse(self.r._legacy_success(value, self.legacy, self.unit))
        for key, bad in (("extra", True), ("approval", "b"*64),
                         ("operation", "other"), ("unit", self.unit+".extra"),
                         ("status", "UNKNOWN_REQUIRES_REVIEW"),
                         ("observation_sha256", None), ("observation_sha256", "F"*64)):
            value = self.receipt()
            value[key] = bad
            with self.subTest(key=key, bad=bad):
                self.assertFalse(self.r._legacy_success(value, self.legacy, self.unit))

    def test_dispatch_argv_contains_only_fixed_bootstrap_and_approved_envelope(self):
        admission = SimpleNamespace(file=PurePosixPath("/owned/envelope.json"), sha=SHA,
            spec={"sources": {"pg_bootstrap": {"path": "/owned/release/bootstrap.py"}}})
        self.assertEqual(self.r._worker_argv(admission),
            ["/usr/bin/python3.12", "-I", "-S", "-B", "/owned/release/bootstrap.py",
             "worker", "/owned/envelope.json", SHA])

    def test_foreign_backend_namespace_refused_before_dependency_import(self):
        for name in ("services", "services.foreign", "sqlalchemy", "asyncpg.connection",
                     "greenlet", "typing_extensions"):
            with self.subTest(name=name), mock.patch.object(self.r, "sys",
                    SimpleNamespace(modules={name: object()})), self.assertRaises(RuntimeError):
                self.r._backend(SimpleNamespace())

    def test_repository_sys_path_refused_before_dependency_import(self):
        with mock.patch.object(self.r, "sys", SimpleNamespace(modules={}, base_prefix="/usr",
                path=["/tmp/unapproved-repository"])), \
             mock.patch.object(self.r, "Path", PurePosixPath), self.assertRaises(RuntimeError):
            self.r._backend(SimpleNamespace())


if __name__ == "__main__":
    unittest.main(verbosity=2)
