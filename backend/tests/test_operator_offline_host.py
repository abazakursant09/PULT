"""Portable contracts with mocks. NOT kernel proof; no sockets or subprocesses."""
import ast
from pathlib import Path, PurePosixPath
import stat
import sys
from types import ModuleType, SimpleNamespace
import unittest
from unittest import mock

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))  # Same convention as adjacent backend contract tests.
HERE = REPO/"ops/backup/operator_host"
SHA, OBS = "a"*64, "b"*64
UNIT = "pult-call-gate-"+"c"*32+".service"
OPERATION = "38db967c-cd67-4d26-9e18-01b285a4dc5d"


def fresh(name, imports=None):
    file = HERE/(name+".py")
    module = ModuleType("portable_"+name)
    module.__file__ = str(file)
    shim = ModuleType("fcntl")
    shim.LOCK_EX, shim.LOCK_NB = 2, 4
    shim.flock = mock.Mock(side_effect=AssertionError("real flock forbidden"))
    with mock.patch.dict(sys.modules, {"fcntl": shim, **(imports or {})}):
        exec(compile(file.read_bytes(), str(file), "exec", dont_inherit=True), module.__dict__)
    return module


class Contracts(unittest.TestCase):
    def setUp(self):
        self.p = fresh("profile")
        self.h = fresh("host", {"_pult_operator_profile_v1": self.p})
        self.w = fresh("worker", {"_pult_operator_profile_v1": self.p})
        self.b = fresh("bootstrap")
        forbidden = mock.Mock(side_effect=AssertionError("NOT kernel proof: real OS IO forbidden"))
        self.h.subprocess = SimpleNamespace(Popen=forbidden, run=forbidden, PIPE=-1)
        self.p.subprocess = SimpleNamespace(run=forbidden)
        self.w.socket = SimpleNamespace(socket=forbidden)

    def bound(self):
        bound = object.__new__(self.h.Bound)
        bound.profile = SimpleNamespace(sha=SHA, spec={"operation": OPERATION}, root_guard=mock.Mock())
        bound.supervisor = SimpleNamespace(_empty=mock.Mock(return_value=True))
        bound.unit, bound.used, bound.closed = UNIT, False, False
        bound.clean = bound.eof = bound.reaped = False
        bound.pidfd = bound.fence = bound.lock = None
        bound.process = None
        bound.submitted = False
        bound._guard = mock.Mock()
        bound._claim = mock.Mock()
        return bound

    def frame(self, status="READY_OFFLINE_ONLY", **changes):
        value = dict(format="pult-offline-operator-worker-v1", approval=SHA,
            operation=OPERATION, unit=UNIT, pid=123, status=status,
            observation_sha256=None if status == "READY_OFFLINE_ONLY" else OBS, **self.p.FLAGS)
        value.update(changes)
        return value

    def test_canonical_document_roundtrip(self):
        value = {"z": False, "a": [1, None]}
        self.assertEqual(self.p.document(self.p.canonical(value)), value)

    def test_noncanonical_and_duplicate_documents_refused(self):
        for raw in (b'{}', b'{"a":1,"a":1}\n', b'{ "a":1}\n', b'[]\n', b'null\n', b'\xff'):
            with self.subTest(raw=raw), self.assertRaises(self.p.Refused):
                self.p.document(raw)

    def test_profile_and_bootstrap_share_exact_eleven_source_catalogue(self):
        self.assertEqual(self.p.PATHS, self.b.PATHS)
        self.assertEqual(self.p.PINS, self.b.PINS)
        self.assertEqual(len(self.p.PATHS), 11)
        self.assertEqual(len(self.p.PINS), 7)
        self.assertEqual(self.p.FORMAT, "pult-offline-operator-host-v1")
        self.assertEqual(self.p.MODE, "SYNTHETIC_REVIEW_ENDPOINT_ONLY")

    @staticmethod
    def _capture_info(**changes):
        values = dict(st_dev=1, st_ino=2, st_mode=stat.S_IFDIR | 0o1777,
                      st_uid=0, st_gid=0, st_nlink=3, st_size=4096,
                      st_mtime_ns=10, st_ctime_ns=20)
        values.update(changes)
        return SimpleNamespace(**values)

    def _capture_check(self, before, after, acl=()):
        captured = self.b._Capture()
        captured.entries = [(99, 98, "synthetic", before)]
        fake_os = SimpleNamespace(fstat=mock.Mock(return_value=after),
                                  stat=mock.Mock(return_value=after),
                                  listxattr=mock.Mock(return_value=acl))
        with mock.patch.object(self.b, "os", fake_os):
            captured.check()

    def test_shared_sticky_ancestor_sibling_content_churn_is_not_source_change(self):
        before = self._capture_info()
        self._capture_check(before, self._capture_info(
            st_nlink=4, st_size=8192, st_mtime_ns=11, st_ctime_ns=21))

    def test_shared_sticky_ancestor_identity_and_protection_changes_remain_refused(self):
        for field, value in (("st_dev", 8), ("st_ino", 9), ("st_mode", stat.S_IFDIR | 0o0777),
                             ("st_uid", 42), ("st_gid", 42)):
            with self.subTest(field=field), self.assertRaises(self.b.Refused):
                self._capture_check(self._capture_info(), self._capture_info(**{field: value}))

    def test_nonsticky_or_nonroot_directory_content_churn_remains_refused(self):
        for shape in (dict(st_mode=stat.S_IFDIR | 0o755), dict(st_uid=42)):
            with self.subTest(shape=shape), self.assertRaises(self.b.Refused):
                self._capture_check(self._capture_info(**shape),
                                    self._capture_info(**dict(shape, st_mtime_ns=11)))

    def test_file_content_metadata_and_hardlink_changes_remain_refused(self):
        for field in ("st_size", "st_mtime_ns", "st_ctime_ns", "st_nlink"):
            before = self._capture_info(st_mode=stat.S_IFREG | 0o444, st_nlink=1)
            changed = dict(vars(before), **{field: getattr(before, field) + 1})
            with self.subTest(field=field), self.assertRaises(self.b.Refused):
                self._capture_check(before, SimpleNamespace(**changed))

    def test_shared_sticky_ancestor_acl_change_remains_refused(self):
        with self.assertRaises(self.b.Refused):
            self._capture_check(self._capture_info(), self._capture_info(),
                                ("system.posix_acl_access",))

    def _native_module(self):
        file = REPO / 'ops/backup/tests/operator_offline_host_proof.py'
        module = ModuleType('portable_native_diagnostic_only')
        module.__file__ = str(file)
        exec(compile(file.read_bytes(), str(file), 'exec', dont_inherit=True), module.__dict__)
        return module

    def test_client_failure_preserves_fixed_reason_and_exit_status_not_raw_text(self):
        import json
        native = self._native_module()
        raw = b'{"outcome":"FAIL","error_type":"Refused","reason":"offline_operator_bootstrap_refused"}'
        detail = json.loads(native.client_failure('authorized', 1, raw, b'').split(':', 1)[1])
        self.assertEqual(detail['reason'], 'offline_operator_bootstrap_refused')
        self.assertEqual(detail['case'], 'authorized')
        self.assertEqual(detail['returncode'], 1)
        self.assertEqual(detail['stdout_bytes'], len(raw))
        self.assertEqual(detail['stderr_bytes'], 0)
        self.assertEqual(detail['stdout_sha256'], native.sha(raw))

    def test_client_failure_redacts_unknown_text_paths_and_unstructured_bytes(self):
        import json
        native = self._native_module()
        secret = b'NOT_FOR_PUBLIC_LOG'
        for output in (secret, b'\xff', b'[]', b'null', b'{}',
                       json.dumps(dict(outcome='FAIL', error_type='Refused',
                                       reason='/private/NOT_FOR_PUBLIC_LOG')).encode()):
            with self.subTest(output=output):
                result = native.client_failure('authorized', -9, output, secret)
                self.assertNotIn(secret.decode(), result)
                self.assertNotIn('/private', result)
                self.assertEqual(json.loads(result.split(':', 1)[1])['returncode'], -9)

    def test_all_seven_accepted_lf_source_pins(self):
        import hashlib
        for name, wanted in self.p.PINS.items():
            with self.subTest(source=name):
                raw = (REPO/self.p.PATHS[name]).read_bytes().replace(b"\r\n", b"\n")
                self.assertEqual(hashlib.sha256(raw).hexdigest(), wanted)

    def test_draft_source_catalogue_matches_exact_roles_pins_and_current_lf_bytes(self):
        import hashlib
        import json
        catalogue = json.loads((HERE/"source-profile.json").read_bytes())
        self.assertEqual(catalogue["format"], "pult-offline-operator-source-draft-v1")
        self.assertEqual(catalogue["status"], "DRAFT_NOT_OPERATOR_APPROVED_OR_ACTIVATED")
        self.assertIsNone(catalogue["operator_approval"])
        self.assertEqual(catalogue["admitted_operations"], [])
        self.assertEqual(catalogue["flags"], self.p.FLAGS)
        for field in ("operational_manifest_included", "working_credentials_included",
                      "database_or_pending_included", "cloud_or_publication_authorized",
                      "deployment_performed"):
            self.assertIs(catalogue[field], False, field)
        self.assertIs(catalogue["lifecycle"]["live_authority"], False)
        candidates = {"profile", "host", "bootstrap", "worker"}
        self.assertCountEqual(catalogue["candidate_source_roles"], candidates)
        self.assertCountEqual(catalogue["immutable_source_roles"], self.p.PINS)
        self.assertEqual(set(self.p.PATHS) - set(self.p.PINS), candidates)
        self.assertEqual(len(self.p.PINS), 7)
        sources = catalogue["sources"]
        self.assertEqual(set(sources), set(self.p.PATHS))
        self.assertEqual(len(sources), 11)
        for role, relative in self.p.PATHS.items():
            with self.subTest(role=role):
                self.assertEqual(set(sources[role]), {"path", "sha256"})
                self.assertEqual(sources[role]["path"], relative)
                if role in self.p.PINS:
                    self.assertEqual(sources[role]["sha256"], self.p.PINS[role])
                else:
                    raw = (REPO/relative).read_bytes().replace(b"\r\n", b"\n")
                    self.assertEqual(sources[role]["sha256"], hashlib.sha256(raw).hexdigest(),
                                     "source catalogue refresh required: " + role)

    def test_worker_frames_accept_only_matching_ready_and_closed(self):
        bound = self.bound()
        for status in ("READY_OFFLINE_ONLY", "CLOSED_OFFLINE_ONLY"):
            value = self.frame(status)
            self.assertEqual(bound._worker_frame(self.p.canonical(value), status), value)

    def test_worker_frame_authority_or_identity_substitution_refused(self):
        changes = [{k: True} for k in self.p.FLAGS]
        changes += [{"pid": True}, {"pid": 0}, {"approval": OBS}, {"operation": "other"},
                    {"unit": UNIT+"x"}, {"extra": True}, {"observation_sha256": OBS}]
        bound = self.bound()
        for change in changes:
            with self.subTest(change=change), self.assertRaises(self.h.Unknown):
                bound._worker_frame(self.p.canonical(self.frame(**change)), "READY_OFFLINE_ONLY")

    def test_closed_frame_requires_lowercase_sha(self):
        bound = self.bound()
        for value in (None, True, "B"*64, "a"*63, "g"*64):
            with self.subTest(value=value), self.assertRaises(self.h.Unknown):
                bound._worker_frame(self.p.canonical(self.frame("CLOSED_OFFLINE_ONLY", observation_sha256=value)), "CLOSED_OFFLINE_ONLY")

    def test_claim_is_exact_new_operator_format(self):
        profile = object.__new__(self.p.Profile)
        profile.control = PurePosixPath("/synthetic/control")
        profile.sha = SHA
        profile.spec = dict(operation=OPERATION, controller={"synthetic": "controller"})
        value = dict(format="pult-offline-operator-claim-v1", approval=SHA,
            operation=OPERATION, controller=profile.spec["controller"], unit=UNIT)
        with mock.patch.object(self.p, "root_bytes", return_value=self.p.canonical(value)) as read:
            self.assertEqual(profile.claim(UNIT), value)
            self.assertEqual(read.call_args.args[1:], (4096, (0o444,)))
        for change in ({"format": "legacy"}, {"approval": OBS}, {"unit": "foreign.service"}, {"extra": 1}):
            with self.subTest(change=change), mock.patch.object(self.p, "root_bytes", return_value=self.p.canonical(dict(value, **change))), self.assertRaises(self.p.Refused):
                profile.claim(UNIT)

    def test_parent_positive_selected_after_all_descriptor_closes(self):
        bound = self.bound()
        events = []
        bound.pidfd, bound.fence, bound.lock = 101, 102, 103
        def execute():
            bound.clean = bound.eof = bound.reaped = True
            return OBS
        bound._execute = execute
        self.h.os = SimpleNamespace(close=lambda fd: events.append(fd))
        bound.profile.root_guard.side_effect = lambda: self.assertEqual(events, [101, 102, 103])
        result = bound.run()
        self.assertEqual(result["status"], "OFFLINE_REVIEW_STOP_CONFIRMED_NO_ADMISSION")
        self.assertTrue(bound.closed)
        self.assertEqual(result["observation_sha256"], OBS)
        self.assertTrue(all(result[n] is False for n in self.p.FLAGS))

    def test_parent_uncertainty_never_passes(self):
        for missing in ("clean", "eof", "reaped", "empty", "observation", "final_guard", "fd_close"):
            with self.subTest(missing=missing):
                bound = self.bound()
                bound.lock = 103
                def execute():
                    bound.clean = missing != "clean"
                    bound.eof = missing != "eof"
                    bound.reaped = missing != "reaped"
                    return None if missing == "observation" else OBS
                bound._execute = execute
                bound.supervisor._empty.return_value = missing != "empty"
                if missing == "final_guard":
                    bound.profile.root_guard.side_effect = RuntimeError("uncertain")
                self.h.os = SimpleNamespace(close=mock.Mock(side_effect=OSError("uncertain") if missing == "fd_close" else None))
                result = bound.run()
                self.assertEqual(result["status"], "UNKNOWN_REQUIRES_REVIEW")
                self.assertIsNone(result["observation_sha256"])
                self.assertTrue(bound.closed)
                self.assertIsNone(bound.lock)

    def test_repeat_same_bound_refused(self):
        bound = self.bound()
        bound.used = True
        with self.assertRaises(self.h.Refused):
            bound.run()
        bound._claim.assert_not_called()

    def test_pidfd_open_failure_does_not_invent_pipe_eof(self):
        self._early_execute_failure("pidfd")

    def test_selector_registration_failure_does_not_invent_pipe_eof(self):
        self._early_execute_failure("registration")

    def _early_execute_failure(self, failure):
        bound = self.bound()
        bound._argv = lambda: ["fake-owned-unit-only"]
        bound.cleanup = mock.Mock()
        bound.supervisor._state = mock.Mock(return_value={"LoadState": "not-found"})
        streams = [SimpleNamespace(fileno=lambda: 20+n, closed=False, close=mock.Mock()) for n in range(3)]
        child = SimpleNamespace(pid=123, stdin=streams[0], stdout=streams[1], stderr=streams[2],
            poll=mock.Mock(return_value=1), wait=mock.Mock(), returncode=1)
        selector = SimpleNamespace(register=mock.Mock(side_effect=RuntimeError("registration") if failure == "registration" else None),
            get_map=lambda: {}, close=mock.Mock())
        self.h.selectors = SimpleNamespace(DefaultSelector=lambda: selector, EVENT_READ=1)
        self.h.subprocess = SimpleNamespace(Popen=mock.Mock(return_value=child), PIPE=-1)
        self.h.os = SimpleNamespace(pidfd_open=mock.Mock(side_effect=OSError("pidfd") if failure == "pidfd" else None, return_value=300),
            set_blocking=mock.Mock())
        with self.assertRaises((OSError, RuntimeError)):
            bound._execute()
        self.assertFalse(bound.eof, "empty selector is not evidence of either pipe EOF")
        bound.cleanup.assert_called_once()
        for stream in streams:
            stream.close.assert_called_once()

    def test_go_followed_by_hangup_is_refused_without_read(self):
        read = mock.Mock(side_effect=AssertionError("queued GO must not be consumed after HUP"))
        poller = SimpleNamespace(register=mock.Mock(), poll=lambda timeout: [(0, 17)])
        self.w.os = SimpleNamespace(fstat=lambda fd: SimpleNamespace(st_mode=stat.S_IFIFO), read=read)
        self.w.select = SimpleNamespace(poll=lambda: poller, POLLIN=1, POLLHUP=16, POLLERR=8, POLLNVAL=32)
        with self.assertRaises(self.p.Refused):
            self.w._wait_go()
        read.assert_not_called()

    @staticmethod
    def _proc_stat(state="S", ticks=98765):
        return "321 (trusted parent) " + " ".join([state] + ["0"] * 18 + [str(ticks)])

    def _proc_paths(self, records):
        # Pure mock /proc; an unknown path is a test failure, never filesystem IO.
        def fake_path(value):
            key = str(value)
            def read_text():
                result = records[key]
                if isinstance(result, BaseException):
                    raise result
                return result() if callable(result) else result
            return SimpleNamespace(
                read_text=read_text,
                joinpath=lambda child: fake_path(str(PurePosixPath(key) / child)),
            )
        return mock.patch.object(self.p, "Path", side_effect=fake_path)

    def _actor_fixture(self):
        profile = object.__new__(self.p.Profile)
        parent_unit = "pult-call-gate-" + "d" * 32 + ".service"
        boot = "89fd18ee-9acb-46d1-9a6c-d01410dca840"
        profile.spec = dict(controller=dict(
            unit=parent_unit, pid=321, start_ticks=98765, boot=boot,
        ))
        profile.check = mock.Mock()
        profile.kernel_actor = mock.Mock(return_value=12345)
        profile.claim = mock.Mock(return_value={"unit": UNIT})
        forbidden = mock.Mock(side_effect=AssertionError("actor must not access PID1 D-Bus"))
        self.p.show = forbidden
        profile.worker_policy = forbidden
        self.p.os = SimpleNamespace(getpid=lambda: 123)
        records = {
            "/proc/self/cgroup": "0::/system.slice/" + UNIT + "\n",
            "/proc/sys/kernel/random/boot_id": boot + "\n",
            "/proc/321/stat": self._proc_stat(),
            "/proc/321/status": "Uid:\t0 0 0 0\nGid:\t0 0 0 0\n",
            "/proc/321/cgroup": "0::/system.slice/" + parent_unit + "\n",
        }
        return profile, records, forbidden

    def test_actor_guard_accepts_pinned_proc_parent_without_show(self):
        profile, records, forbidden = self._actor_fixture()
        with self._proc_paths(records):
            self.assertEqual(profile.actor_guard(), UNIT)
        profile.kernel_actor.assert_called_once_with(123)
        forbidden.assert_not_called()

    def test_actor_guard_refuses_stopped_zombie_or_dead_parent_without_show(self):
        for state in ("T", "t", "Z", "X", "x"):
            with self.subTest(state=state):
                profile, records, forbidden = self._actor_fixture()
                records["/proc/321/stat"] = self._proc_stat(state=state)
                with self._proc_paths(records), self.assertRaisesRegex(
                    self.p.Refused, "^original_controller_unavailable$"
                ):
                    profile.actor_guard()
                forbidden.assert_not_called()

    def test_actor_guard_requires_all_four_parent_uids_and_gids_zero(self):
        for field in ("Uid", "Gid"):
            for index in range(4):
                with self.subTest(field=field, index=index):
                    profile, records, forbidden = self._actor_fixture()
                    credentials = dict(Uid=[0] * 4, Gid=[0] * 4)
                    credentials[field][index] = 46201
                    records["/proc/321/status"] = "".join(
                        key + ":\t" + " ".join(map(str, values)) + "\n"
                        for key, values in credentials.items()
                    )
                    with self._proc_paths(records), self.assertRaisesRegex(
                        self.p.Refused, "^original_controller_unavailable$"
                    ):
                        profile.actor_guard()
                    forbidden.assert_not_called()

    def test_actor_guard_refuses_different_parent_cgroup_without_show(self):
        profile, records, forbidden = self._actor_fixture()
        records["/proc/321/cgroup"] = "0::/system.slice/foreign.service\n"
        with self._proc_paths(records), self.assertRaisesRegex(
            self.p.Refused, "^original_controller_unavailable$"
        ):
            profile.actor_guard()
        forbidden.assert_not_called()

    def test_actor_guard_refuses_stale_or_racing_parent_start_ticks(self):
        for mode in ("stale", "race"):
            with self.subTest(mode=mode):
                profile, records, forbidden = self._actor_fixture()
                records["/proc/321/stat"] = (
                    self._proc_stat(ticks=98766) if mode == "stale" else
                    mock.Mock(side_effect=[self._proc_stat(), self._proc_stat(ticks=98766)])
                )
                with self._proc_paths(records), self.assertRaisesRegex(
                    self.p.Refused, "^original_controller_unavailable$"
                ):
                    profile.actor_guard()
                forbidden.assert_not_called()

    def test_actor_guard_refuses_changed_boot_without_show(self):
        profile, records, forbidden = self._actor_fixture()
        records["/proc/sys/kernel/random/boot_id"] = "31de941d-9465-4c6b-a5a5-902f281f6bf5\n"
        with self._proc_paths(records), self.assertRaisesRegex(
            self.p.Refused, "^controller_boot_changed$"
        ):
            profile.actor_guard()
        forbidden.assert_not_called()

    def _policy_fixture(self):
        profile = object.__new__(self.p.Profile)
        profile.sha, profile.ipc = SHA, PurePosixPath("/synthetic/ipc")
        parent = "pult-call-gate-" + "d" * 32 + ".service"
        profile.spec = {"controller": {"unit": parent}}
        profile.kernel_actor = mock.Mock(return_value=12345)
        own = dict(
            ActiveState="active", SubState="running", MainPID="123",
            RuntimeMaxUSec="15s", TimeoutStopUSec="2s", ExitType="cgroup",
            KillMode="control-group", KillSignal="15", FinalKillSignal="9",
            SendSIGKILL="yes", Restart="no", NRestarts="0", PrivateNetwork="yes",
            ReadWritePaths="/synthetic/ipc", ProtectSystem="strict", ProtectHome="yes",
            PrivateTmp="no", PrivateDevices="yes", ProtectControlGroups="yes",
            RestrictAddressFamilies="AF_UNIX", TasksMax="16", MemoryMax="134217728",
            LimitFSIZE="2097152", UMask="0077", SupplementaryGroups="",
            NoNewPrivileges="yes", AmbientCapabilities="cap_setuid",
            Description="PULT offline operator " + SHA, User="0", Group="0",
            CapabilityBoundingSet="cap_setuid cap_setgid cap_setpcap",
            BindsTo=parent, After=parent, PartOf=parent, InvocationID="e" * 32,
        )
        return profile, own

    def test_worker_policy_requires_root_before_manager_or_proc_access(self):
        profile, _ = self._policy_fixture()
        with mock.patch.object(self.p, "runtime", side_effect=self.p.Refused("root_required")) as runtime, \
             mock.patch.object(self.p, "show") as show, self._proc_paths({}):
            with self.assertRaisesRegex(self.p.Refused, "^root_required$"):
                profile.worker_policy(UNIT, 123)
        runtime.assert_called_once_with(root=True)
        show.assert_not_called()
        profile.kernel_actor.assert_not_called()

    def test_worker_policy_requires_actual_actor_cgroup_not_just_metadata(self):
        profile, own = self._policy_fixture()
        records = {"/proc/123/cgroup": "0::/system.slice/" + UNIT + "\n"}
        with mock.patch.object(self.p, "runtime") as runtime, \
             mock.patch.object(self.p, "show", return_value=own), self._proc_paths(records):
            self.assertEqual(profile.worker_policy(UNIT, 123), own["InvocationID"])
            for bad in ("0::/system.slice/foreign.service\n", "", FileNotFoundError("gone")):
                with self.subTest(cgroup=bad):
                    records["/proc/123/cgroup"] = bad
                    with self.assertRaises((self.p.Refused, FileNotFoundError)):
                        profile.worker_policy(UNIT, 123)
            for field, bad in (("ActiveState", "inactive"), ("SubState", "exited"), ("MainPID", "124")):
                with self.subTest(field=field), mock.patch.dict(own, {field: bad}), \
                     self.assertRaisesRegex(self.p.Refused, "^kernel_actor_lifecycle_refused$"):
                    profile.worker_policy(UNIT, 123)
        self.assertTrue(all(call == mock.call(root=True) for call in runtime.call_args_list))
        profile.kernel_actor.assert_has_calls([mock.call(123)] * 4)

    def _shape_fixture(self):
        profile = object.__new__(self.p.Profile)
        identity = {"device": 1, "inode": 2}
        profile.spec = dict(
            format=self.p.FORMAT, mode=self.p.MODE, operation=OPERATION,
            release=dict(root="/synthetic/release", identity=dict(identity)),
            control=dict(root="/synthetic/control", identity=dict(identity), lock_identity=dict(identity)),
            actor=dict(uid=46201, gid=46202), operator_uid=46202,
            controller=dict(unit=UNIT, invocation_id="e" * 32, pid=321, start_ticks=98765,
                boot="89fd18ee-9acb-46d1-9a6c-d01410dca840"),
            ipc=dict(path="/synthetic/ipc", identity=dict(identity)),
            synthetic=dict(self.p.SYNTHETIC),
            sources={name: dict(path="/synthetic/release/" + relative,
                sha256=self.p.PINS.get(name, SHA)) for name, relative in self.p.PATHS.items()},
        )
        return profile

    def test_canonical_but_mutated_profile_refused_before_descriptor_checks(self):
        profile = self._shape_fixture()
        profile.raw = self.p.canonical(profile.spec)
        profile.file = SimpleNamespace(lstat=mock.Mock(side_effect=AssertionError("mutation must fail first")))
        profile.spec["actor"]["uid"] = 46204  # Still canonical and valid-shaped, but not approved.
        with mock.patch.object(self.p, "Path", PurePosixPath), \
             mock.patch.object(self.p, "runtime"), \
             mock.patch.object(self.p, "root_bytes", return_value=profile.raw), \
             mock.patch.object(self.p, "inode") as inode:
            with self.assertRaisesRegex(self.p.Refused, "^profile_changed$"):
                profile.check()
        inode.assert_not_called()
        profile.file.lstat.assert_not_called()

    def test_minimal_profile_shape_denies_mode_synthetic_alias_and_source_changes(self):
        for mode in ("mode", "synthetic", "alias", "boolean_uid", "extra", "source"):
            with self.subTest(mode=mode):
                profile = self._shape_fixture()
                with mock.patch.object(self.p, "Path", PurePosixPath):
                    profile._shape()  # The negative fixture must first be valid.
                    if mode == "mode":
                        profile.spec["mode"] = "LIVE"
                    elif mode == "synthetic":
                        profile.spec["synthetic"]["policy"] = "other"
                    elif mode == "alias":
                        profile.spec["operator_uid"] = profile.spec["actor"]["uid"]
                    elif mode == "boolean_uid":
                        profile.spec["actor"]["uid"] = True
                    elif mode == "extra":
                        profile.spec["approved"] = True
                    else:
                        profile.spec["sources"]["host"]["path"] = "/foreign/host.py"
                    with self.assertRaises(self.p.Refused):
                        profile._shape()

    def test_native_inventory_truthfully_counts_eleven_no_skips(self):
        file = REPO/"ops/backup/tests/operator_offline_host_proof.py"
        tree = ast.parse(file.read_bytes())
        inventory = next(ast.literal_eval(n.value) for n in tree.body if isinstance(n, ast.Assign)
            and any(isinstance(t, ast.Name) and t.id == "CASES" for t in n.targets))
        self.assertEqual(inventory, (
            "authorized", "freeze", "wrong_peer", "source_changed", "profile_replaced",
            "stale_controller", "term_descendant", "controller_death", "queued_late",
            "cleanup_unknown", "bad_approval",
        ))
        self.assertEqual(len(inventory), 11)
        self.assertEqual(len(set(inventory)), 11)
        text = file.read_text()
        self.assertIn("OFFLINE_SYNTHETIC_OPERATOR_LIFECYCLE_ONLY", text)
        self.assertIn("optimized_operator_proof_refused", text)
        self.assertNotIn("unittest.skip", text)
        self.assertIn("tests=11, skips=0", text)

    def test_native_cleanup_does_not_treat_active_empty_unit_as_stopped(self):
        file = REPO / "ops/backup/tests/operator_offline_host_proof.py"
        native = ModuleType("portable_native_cleanup_only")
        native.__file__ = str(file)
        exec(compile(file.read_bytes(), str(file), "exec", dont_inherit=True), native.__dict__)
        native.state = mock.Mock(return_value={
            "LoadState": "loaded", "ActiveState": "active", "SubState": "exited",
            "Description": "owned synthetic description",
        })
        native.empty = mock.Mock(return_value=True)
        native.command = mock.Mock(return_value=SimpleNamespace(returncode=0))
        with self.assertRaisesRegex(RuntimeError, "^owned_unit_not_stopped_and_empty$"):
            native.stop_owned(UNIT, "owned synthetic description")
        self.assertEqual(native.command.call_count, 3)
        for call in native.command.call_args_list:
            self.assertIn(UNIT, call.args[0])

    def test_mandatory_ci_steps_are_not_conditional_or_continue_on_error(self):
        file = REPO/".github/workflows/backup_restore_synthetic.yml"
        if not file.exists():
            file = REPO/".github/workflows/backup_restore_synthetic.yaml"
        text = file.read_text()
        for name, entry in (("Offline operator host profile contracts", "python -B -m pytest backend/tests/test_operator_offline_host.py -q"),
            ("Native offline operator forced-stop proof", "sudo -n -- unshare --net /usr/bin/python3.12 -I -S -B ops/backup/tests/operator_offline_host_proof.py")):
            start = text.index("- name: "+name)
            tail = text[start+1:]
            end = tail.find("\n      - name:")
            block = text[start:] if end < 0 else text[start:start+1+end]
            self.assertIn(entry, block)
            self.assertNotRegex(block, r"(?m)^\s*(if|continue-on-error):")


if __name__ == "__main__":
    unittest.main(verbosity=2)
