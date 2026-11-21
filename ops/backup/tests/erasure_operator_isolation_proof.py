"""Synthetic Linux multi-UID proof of the operator channel, not a PG proof.

Run as a root test orchestrator; only owned children use the artificial UIDs.
No accounts/services are installed, and no DB, registry or network is used.
The async reviewer writes one private marker per call and returns a synthetic
DraftObservation. Committed-DRAFT database semantics require the separate PG gate.
"""
import asyncio
from dataclasses import asdict
import json
import os
from pathlib import Path
import select
import shutil
import socket
import stat
import subprocess
import sys
import tempfile
import time
import unittest

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
from ops.backup import erasure_operator as operator  # noqa: E402 -- bootstrap copied, root-owned code

SERVICE, OPERATOR, STRANGER, GROUP = 46101, 46102, 46103, 46102
ACCOUNT = "c7461350-9188-4bf1-bc6c-c4a5b9455bec"
DRAFT = "ff636b10-6ccb-4b1b-9394-df59af74b793"
IMPOSTOR_NONCE = "0406cc9d-837f-440e-845f-9e941496ff8e"
EPOCH = "2026-10-21T12:00:00.000000Z"
REVIEWED = "2026-11-21T12:00:00.000000Z"
PROOF = "ops/backup/tests/erasure_operator_isolation_proof.py"
EXPECTED_TESTS = 10
CHILD_TIMEOUT = 12


def require(value, message):
    if not value:
        raise AssertionError(message)


def closed_without_frame(connection):
    try:
        first = connection.recv(1)
    except ConnectionResetError:
        first = b""
    require(first == b"", "unauthorized peer received a protocol frame")


def child(role, root, mode):
    path = root / "run" / "review.sock"
    if role == "serve":
        async def reviewer(account, draft, *, nonce):
            with (root / "private" / "reviewer.calls").open("ab") as marker:
                marker.write(b"REVIEW\n")
            require((account, draft) == (ACCOUNT, DRAFT), "unexpected synthetic IDs")
            return operator.DraftObservation(nonce=nonce, account_id=account,
                draft_id=draft, closure_epoch=EPOCH, policy=operator.POLICY,
                reviewed_at=REVIEWED)

        if mode == "without_server_peer_check":
            # Explicit mutation control in this owned child only. Source files
            # and the normal proof are unchanged; all sockets remain real.
            operator.ipc._peer = lambda connection, uid: None
        real_socket = socket.socket

        class ReadySocket(real_socket):
            def listen(self, backlog):
                super().listen(backlog)
                print("READY", flush=True)

        # serve_once blocks in accept, so notify only AFTER the real listen.
        # This observes readiness without consuming its one connection.
        socket.socket = ReadySocket
        server = operator.OperatorReviewServer(path,
            operator_uid=SERVICE if mode == "same_uid" else OPERATOR,
            reviewer=reviewer)
        asyncio.run(server.serve_once())
        print("SERVED_ONCE")
    elif role == "client":
        client = operator.OperatorReviewClient(path,
            reviewer_uid=STRANGER if mode == "wrong_server" else SERVICE)
        print(json.dumps(asdict(client.review(ACCOUNT, DRAFT)), sort_keys=True))
    elif role == "raw_peer":
        # Root deliberately bypasses the public client's _principal refusal.
        # Both root and shared-GID stranger must get no challenge from server.
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as connection:
            connection.settimeout(3)
            connection.connect(str(path))
            closed_without_frame(connection)
        print("KERNEL_REFUSED")
    elif role == "request":
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as connection:
            connection.settimeout(3)
            connection.connect(str(path))
            operator.ipc._peer(connection, SERVICE)
            deadline = time.monotonic() + 3
            challenge = operator._receive(connection, deadline)
            operator._check(challenge, {"nonce"})
            request = dict(protocol=operator.PROTOCOL, op="review",
                nonce=challenge["nonce"], account_id=ACCOUNT, draft_id=DRAFT)
            if mode.startswith("replay:"):
                request["nonce"] = mode.split(":", 1)[1]
                require(request["nonce"] != challenge["nonce"], "nonce unexpectedly reused")
            elif mode == "uid":
                request["uid"] = OPERATOR
            elif mode == "approved":
                request["approved"] = True
            elif mode in ("purge", "enroll"):
                request["op"] = mode
            else:
                raise AssertionError("unknown hostile request")
            operator._send(connection, request, deadline)
            closed_without_frame(connection)
        print("REQUEST_REFUSED")
    else:
        raise AssertionError("unknown proof child")


class Fixture:
    def __init__(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="pult-op-proof-", dir="/tmp")
        self.root = Path(self.temporary.name)
        self.processes = {}
        try:
            self.root.chmod(0o755)
            self.code = self.root / "code"
            for name in ("ops/backup/erasure_journal.py", "ops/backup/erasure_anchor.py",
                         "ops/backup/erasure_custodian.py", "ops/backup/erasure_operator.py", PROOF):
                target = self.code / name
                target.parent.mkdir(parents=True, exist_ok=True)
                for parent in target.parents:
                    if parent == self.root:
                        break
                    parent.chmod(0o755)
                shutil.copyfile(ROOT / name, target)
                target.chmod(0o444)
                require(target.stat().st_uid == 0, "proof code must be root-owned")
            for name, mode in (("private", 0o700), ("run", 0o750)):
                directory = self.root / name
                directory.mkdir(mode=mode)
                os.chown(directory, SERVICE, GROUP)
                directory.chmod(mode)
            self.marker = self.root / "private" / "reviewer.calls"
            self.marker.write_bytes(b"")
            os.chown(self.marker, SERVICE, GROUP)
            self.marker.chmod(0o600)
            self.path = self.root / "run" / "review.sock"
        except BaseException:
            self.temporary.cleanup()
            raise

    def spawn(self, role, mode="normal", uid=OPERATOR):
        command = [sys.executable, "-I", "-B", str(self.code / PROOF), role, str(self.root), mode]
        process = subprocess.Popen(command, cwd=self.code,
            env={"PATH": "/usr/bin:/bin", "PYTHONDONTWRITEBYTECODE": "1"},
            user=uid, group=GROUP, extra_groups=[], start_new_session=True,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        self.processes[process] = time.monotonic() + CHILD_TIMEOUT
        return process

    def wait(self, process):
        remaining = max(0.01, self.processes[process] - time.monotonic())
        stdout, stderr = process.communicate(timeout=remaining)
        return subprocess.CompletedProcess(process.args, process.returncode, stdout, stderr)

    def run(self, role, mode="normal", uid=OPERATOR):
        return self.wait(self.spawn(role, mode, uid))

    def start(self, mode="normal"):
        process = self.spawn("serve", mode, SERVICE)
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            readable, _, _ = select.select([process.stdout], [], [], 0.05)
            if readable:
                line = process.stdout.readline()
                require(line == "READY\n", "review server failed before listening: " + line)
                info = self.path.lstat()
                require(stat.S_ISSOCK(info.st_mode) and info.st_uid == SERVICE
                    and info.st_gid == GROUP and stat.S_IMODE(info.st_mode) == 0o660,
                    "unexpected ready socket metadata")
                return process
            require(process.poll() is None, "review server exited before readiness")
        raise AssertionError("review server readiness timed out")

    def calls(self):
        return self.marker.read_bytes()

    def close(self):
        try:
            for process in self.processes:
                if process.poll() is None:
                    process.terminate()
                try:
                    process.communicate(timeout=2)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.communicate(timeout=2)
        finally:
            # Root test orchestrator removes only its exact fresh temp tree.
            self.temporary.cleanup()


class OperatorIsolationProof(unittest.TestCase):
    def fixture(self):
        value = Fixture()
        self.addCleanup(value.close)
        return value

    def okay(self, result, output):
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(result.stderr, "")
        self.assertEqual(result.stdout, output + "\n")

    def refused(self, result, reason="operator_review_refused"):
        self.assertEqual(result.returncode, 23, result.stdout + result.stderr)
        self.assertEqual(result.stderr, "")
        self.assertEqual(result.stdout, "REFUSED:" + reason + "\n")

    def authorized(self, fixture):
        server = fixture.start()
        result = fixture.run("client")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(result.stderr, "")
        value = json.loads(result.stdout)
        self.assertEqual(value, dict(nonce=value["nonce"], account_id=ACCOUNT,
            draft_id=DRAFT, closure_epoch=EPOCH, policy=operator.POLICY,
            reviewed_at=REVIEWED, status=operator.STATUS,
            **{key: False for key in operator.FLAGS}))
        self.assertEqual(operator._uuid(value["nonce"]), value["nonce"])
        self.assertNotIn(value["nonce"], (ACCOUNT, DRAFT))
        for key in operator.FLAGS:
            self.assertIs(value[key], False)
        self.okay(fixture.wait(server), "SERVED_ONCE")
        self.assertEqual(fixture.calls(), b"REVIEW\n")
        return value

    def kernel_refused(self, fixture, uid):
        self.okay(fixture.run("raw_peer", uid=uid), "KERNEL_REFUSED")
        self.assertEqual(fixture.calls(), b"")

    def test_authorized_review_is_exactly_once_with_all_flags_false(self):
        fixture = self.fixture()
        self.authorized(fixture)
        info = fixture.marker.lstat()
        self.assertEqual((info.st_uid, info.st_gid, stat.S_IMODE(info.st_mode)), (SERVICE, GROUP, 0o600))
        self.assertTrue(stat.S_ISSOCK(fixture.path.lstat().st_mode))

    def test_kernel_rejects_shared_group_stranger_before_challenge(self):
        fixture = self.fixture()
        server = fixture.start()
        self.kernel_refused(fixture, STRANGER)
        self.refused(fixture.wait(server))

    def test_root_public_client_refuses_and_raw_root_gets_no_challenge(self):
        fixture = self.fixture()
        self.refused(fixture.run("client", uid=0), "operator_client_refused")
        self.assertFalse(fixture.path.exists())
        server = fixture.start()
        self.kernel_refused(fixture, 0)
        self.refused(fixture.wait(server))

    def test_client_wrong_server_pin_refuses_before_connect(self):
        fixture = self.fixture()
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as impostor:
            impostor.bind(str(fixture.path))
            os.chown(fixture.path, SERVICE, GROUP)
            fixture.path.chmod(0o660)
            impostor.listen(1)
            impostor.settimeout(0.2)
            self.refused(fixture.run("client", "wrong_server"))
            with self.assertRaises(socket.timeout):
                impostor.accept()
        self.assertEqual(fixture.calls(), b"")

    def test_kernel_server_pin_rejects_forged_inode_owner_before_disclosure(self):
        fixture = self.fixture()
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as impostor:
            impostor.bind(str(fixture.path))
            os.chown(fixture.path, SERVICE, GROUP)  # Metadata is not kernel identity (root).
            fixture.path.chmod(0o660)
            impostor.listen(1)
            impostor.settimeout(3)
            client = fixture.spawn("client")
            connection, _ = impostor.accept()
            with connection:
                connection.settimeout(3)
                # A valid challenge makes removal of the client peer check
                # disclose IDs, rather than merely timing out on a silent peer.
                try:
                    operator._send(connection, dict(protocol=operator.PROTOCOL,
                        nonce=IMPOSTOR_NONCE), time.monotonic() + 3)
                except (BrokenPipeError, ConnectionResetError):
                    pass
                closed_without_frame(connection)
            self.refused(fixture.wait(client))
        self.assertEqual(fixture.calls(), b"")

    def test_nonce_from_previous_success_cannot_replay_into_new_session(self):
        previous = self.authorized(self.fixture())["nonce"]
        fixture = self.fixture()
        server = fixture.start()
        self.okay(fixture.run("request", "replay:" + previous), "REQUEST_REFUSED")
        self.refused(fixture.wait(server))
        self.assertEqual(fixture.calls(), b"")

    def test_extra_authority_fields_and_mutating_operations_never_call_reviewer(self):
        for mode in ("uid", "approved", "purge", "enroll"):
            with self.subTest(mode=mode):
                fixture = self.fixture()
                server = fixture.start()
                self.okay(fixture.run("request", mode), "REQUEST_REFUSED")
                self.refused(fixture.wait(server))
                self.assertEqual(fixture.calls(), b"")

    def test_existing_socket_is_refused_without_replacement(self):
        fixture = self.fixture()
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as existing:
            existing.bind(str(fixture.path))
            os.chown(fixture.path, SERVICE, GROUP)
            fixture.path.chmod(0o660)
            before = fixture.path.lstat()
            self.refused(fixture.run("serve", uid=SERVICE), "operator_socket_exists_requires_review")
            after = fixture.path.lstat()
            self.assertEqual((before.st_dev, before.st_ino, before.st_uid, before.st_gid, before.st_mode),
                             (after.st_dev, after.st_ino, after.st_uid, after.st_gid, after.st_mode))
        self.assertEqual(fixture.calls(), b"")

    def test_same_uid_and_root_constructors_refuse_before_socket_creation(self):
        for role, mode, uid, reason in (
            ("serve", "same_uid", SERVICE, "dedicated_operator_and_reviewer_required"),
            ("serve", "normal", 0, "operator_host_refused"),
            ("client", "normal", SERVICE, "separate_reviewer_required"),
        ):
            with self.subTest(role=role, uid=uid):
                fixture = self.fixture()
                self.refused(fixture.run(role, mode, uid), reason)
                self.assertFalse(fixture.path.exists())
                self.assertEqual(fixture.calls(), b"")

    def test_wrong_uid_assertion_fails_when_server_peer_check_is_disabled(self):
        fixture = self.fixture()
        server = fixture.start("without_server_peer_check")
        with self.assertRaisesRegex(AssertionError, "unauthorized peer received a protocol frame"):
            self.kernel_refused(fixture, STRANGER)
        self.refused(fixture.wait(server))
        self.assertEqual(fixture.calls(), b"")


if __name__ == "__main__":
    if not __debug__:
        raise SystemExit("FAIL: optimized proof runtime refused")
    if len(sys.argv) == 4:
        try:
            child(sys.argv[1], Path(sys.argv[2]), sys.argv[3])
        except operator.ReviewError as error:
            print("REFUSED:" + str(error))
            sys.exit(23)
    else:
        if len(sys.argv) != 1 or sys.platform != "linux" or os.geteuid() != 0 or not hasattr(socket, "SO_PEERCRED"):
            raise SystemExit("FAIL: Linux root test orchestrator with SO_PEERCRED required; no skipped proof")
        suite = unittest.defaultTestLoader.loadTestsFromTestCase(OperatorIsolationProof)
        if suite.countTestCases() != EXPECTED_TESTS:
            raise SystemExit("FAIL: unexpected isolation proof test count")
        result = unittest.TextTestRunner(verbosity=2).run(suite)
        print("ERASURE_OPERATOR_ISOLATION_PROOF tests={} failures={} errors={} skipped={}".format(
            result.testsRun, len(result.failures), len(result.errors), len(result.skipped)), flush=True)
        sys.exit(0 if result.wasSuccessful() and result.testsRun == EXPECTED_TESTS and not result.skipped else 1)
