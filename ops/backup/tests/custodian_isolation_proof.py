"""Root-orchestrated, entirely synthetic Linux proof. No accounts or services installed.

Only children drop to numeric UIDs, without supplementary groups/capabilities.
All files live under one fresh TemporaryDirectory; no real keys, S3 or network.
Run explicitly: sudo -n python3 -B ops/backup/tests/custodian_isolation_proof.py
"""
import hashlib
import json
import os
from pathlib import Path
import shutil
import socket
import struct
import subprocess
import sys
import tempfile
import time
import unittest

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
from ops.backup.erasure_anchor import Custodian, SQLiteAnchor
from ops.backup.erasure_journal import Conflict, Event, Stored, WriteUnknown, empty, genesis
from ops.backup import erasure_custodian as ipc

SERVICE, WRITER, STRANGER, GROUP = 46001, 46002, 46003, 46002
SCOPE = "synthetic"
LOCATION = "synthetic://uid-test/journal"
GENERATION = "1e534b17-05e7-4529-8ffa-c32162c67ad5"
ATTEMPT = "a9e1135b-aab0-4b4e-a10d-8f96cc696d96"
OTHER = "82047569-5c91-4b10-87c4-3a8b121e1891"
EVENT = Event("73e1c70a-3840-40ec-b5a2-6380343d9cbf", "770d659f-c20f-46dc-b3bc-3f6000204bc6", "intent")


def anchor(root):
    return SQLiteAnchor(root / "private" / "anchor.sqlite", scope=SCOPE,
                        generation=GENERATION, location=LOCATION)


def etag(body):
    return '"' + hashlib.sha256(body).hexdigest() + '"'


class Reader:
    scope, location = SCOPE, LOCATION

    def __init__(self, root):
        self.root = root

    def read(self):
        assert (self.root / "private" / "reader.key").read_bytes() == b"synthetic-reader-only"
        body = (self.root / "remote" / "object").read_bytes()
        return Stored(body, etag(body))


class Writer:
    scope, location = SCOPE, LOCATION

    def __init__(self, root, fault="normal"):
        self.root, self.fault = root, fault

    def write(self, body, *, etag):
        assert (self.root / "writer-private" / "writer.key").read_bytes() == b"synthetic-writer-only"
        with (self.root / "remote" / "puts").open("ab") as handle:
            handle.write(b"PUT\n")
        if self.fault == "conflict":
            raise Conflict("synthetic conflict")
        path = self.root / "remote" / "object"
        if etag != globals()["etag"](path.read_bytes()):
            raise Conflict("synthetic stale write")
        with path.open("wb") as handle:
            handle.write(body)
            handle.flush()
            os.fsync(handle.fileno())
        if self.fault == "unknown":
            raise WriteUnknown("synthetic lost acknowledgement")
        if self.fault == "interrupt":
            raise SystemExit(42)


def client(root, **overrides):
    fields = dict(custodian_uid=SERVICE, scope=SCOPE, generation=GENERATION,
                  location=LOCATION, writer=Writer(root))
    fields.update(overrides)
    return ipc.UnixAnchoredJournal(root / "run" / "custodian.sock", **fields)


def child(role, root, mode):
    if role == "serve":
        class SlowReader(Reader):
            def read(self):
                time.sleep(0.15)
                return super().read()
        class LostReply(Custodian):
            def confirm(self, ticket):
                super().confirm(ticket)
                os._exit(79)
        if mode == "slow_reader":
            ipc.SESSION_SECONDS = 0.05
        custody = (LostReply if mode == "lost_reply" else Custodian)(
            anchor(root), (SlowReader if mode == "slow_reader" else Reader)(root))
        server = ipc.UnixCustodianServer(custody, root / "run" / "custodian.sock",
                                         writer_uid=SERVICE if mode == "same_uid" else WRITER)
        server.serve()
    elif role == "constructor_error":
        try:
            ipc.UnixCustodianServer(Custodian(anchor(root), Reader(root)), root / "run/custodian.sock", writer_uid=WRITER)
        except ipc.IsolationError as error:
            print(json.dumps({"error": str(error)}))
            return
        raise AssertionError("constructor failed to sanitize filesystem error")
    elif role == "unicode_connection":
        runner = ipc.UnixAnchoredJournal(root / "run" / "invalid\ud800", custodian_uid=SERVICE,
                                         scope=SCOPE, generation=GENERATION, location=LOCATION,
                                         writer=Writer(root))
        before = len(list(Path("/proc/self/fd").iterdir()))
        for _ in range(3):
            try:
                runner._connect()
            except ipc.IsolationError as error:
                assert str(error) == "custodian connection refused"
            else:
                raise AssertionError("invalid filesystem encoding accepted")
        assert len(list(Path("/proc/self/fd").iterdir())) == before
        print("FIXED_ERROR_NO_FD_LEAK")
    elif role == "append":
        runner = client(root, writer=Writer(root, mode))
        result = runner.append(EVENT, attempt_id=ATTEMPT)
        print(json.dumps(dict(status=result.status, sequence=None if result.observed is None else result.observed.sequence)))
    elif role == "observe":
        result = client(root).observe(ATTEMPT)
        print(json.dumps(dict(status=result.status, sequence=result.observed.sequence)))
    elif role == "raw":
        runner = client(root)
        with runner._connect() as connection:
            deadline = time.monotonic() + 3
            binding = runner.binding
            if mode in ("claim", "confirm"):
                ipc._send(connection, binding.wrap(op=mode), deadline)
                ipc._receive(connection, deadline)
            elif mode == "oversize":
                connection.sendall(struct.pack("!I", ipc.MAX_FRAME + 1))
                ipc._receive(connection, deadline)
            elif mode == "wrong_binding":
                bad = ipc._Binding(scope="other", generation=GENERATION, location=LOCATION)
                ipc._send(connection, bad.wrap(op="append", event=vars(EVENT), attempt_id=ATTEMPT), deadline)
                ipc._receive(connection, deadline)
            else:
                ipc._send(connection, binding.wrap(op="append", event=vars(EVENT), attempt_id=ATTEMPT), deadline)
                message = ipc._receive(connection, deadline)
                assert message["status"] == "PREPARED" and "body" not in message
                if mode != "before_claim":
                    ipc._send(connection, binding.wrap(op="claim"), deadline)
                    message = ipc._receive(connection, deadline)
                    assert message["status"] == "DISPATCHED"
                    if mode == "after_put":
                        Writer(root).write(message["body"].encode("ascii"), etag=message["etag"])
    elif role == "wrong_server":
        client(root, custodian_uid=STRANGER).append(EVENT, attempt_id=ATTEMPT)
    elif role == "probe":
        if mode == "service":
            try:
                (root / "writer-private" / "writer.key").read_bytes()
            except PermissionError:
                print("DENIED_WRITER_KEY")
                return
            raise AssertionError("custodian read writer key")
        operations = [
            lambda: (root / "private" / "reader.key").read_bytes(),
            lambda: (root / "private" / "anchor.sqlite").read_bytes(),
            lambda: (root / "private" / "anchor.sqlite-journal").read_bytes(),
            lambda: (root / "private" / "anchor.sqlite").unlink(),
            lambda: (root / "run" / "custodian.sock").unlink(),
            lambda: (root / "run" / "replacement").write_bytes(b"forged"),
            lambda: Path("/proc/" + mode + "/environ").read_bytes(),
            lambda: os.kill(int(mode), 0),
        ]
        for operation in operations:
            try:
                operation()
            except PermissionError:
                continue
            raise AssertionError("writer crossed custody boundary")
        assert (root / "writer-private" / "writer.key").read_bytes() == b"synthetic-writer-only"
        print("DENIED_8_POSITIVE_OWN_KEY")


class Fixture:
    def __init__(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="pult-custodian-proof-")
        self.root = Path(self.temporary.name)
        self.root.chmod(0o755)
        self.code = self.root / "code"
        self.code.mkdir(mode=0o755)
        self.code.chmod(0o755)
        paths = ["ops/backup/erasure_journal.py", "ops/backup/erasure_anchor.py",
                 "ops/backup/erasure_custodian.py", "ops/backup/tests/custodian_isolation_proof.py"]
        for name in paths:
            target = self.code / name
            target.parent.mkdir(parents=True, exist_ok=True)
            for parent in target.parents:
                if parent == self.root:
                    break
                parent.chmod(0o755)
            shutil.copyfile(ROOT / name, target)
            target.chmod(0o444)
        for name, owner, mode in (("private", SERVICE, 0o700), ("run", SERVICE, 0o750),
                                  ("remote", WRITER, 0o770), ("writer-private", WRITER, 0o700)):
            path = self.root / name
            path.mkdir(mode=mode)
            os.chown(path, owner, GROUP)
            path.chmod(mode)
        anchor(self.root).create(genesis(SCOPE))  # explicit trusted synthetic bootstrap only
        for name, data, owner in (("private/reader.key", b"synthetic-reader-only", SERVICE),
                                   ("writer-private/writer.key", b"synthetic-writer-only", WRITER),
                                   ("remote/object", empty(SCOPE).encode(), WRITER),
                                   ("remote/puts", b"", WRITER)):
            path = self.root / name
            path.write_bytes(data)
            os.chown(path, owner, GROUP)
            path.chmod(0o660 if name.startswith("remote/") else 0o600)
        db = self.root / "private" / "anchor.sqlite"
        os.chown(db, SERVICE, GROUP)
        db.chmod(0o600)
        self.process = None

    def command(self, role, mode):
        return [sys.executable, "-B", str(self.code / "ops/backup/tests/custodian_isolation_proof.py"),
                role, str(self.root), mode]

    def options(self, uid):
        return dict(cwd=self.code, env={"PATH": "/usr/bin:/bin", "PYTHONDONTWRITEBYTECODE": "1"},
                    user=uid, group=GROUP, extra_groups=[], start_new_session=True,
                    stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)

    def run(self, role, mode="normal", uid=WRITER):
        return subprocess.run(self.command(role, mode), **self.options(uid), timeout=10)

    def start(self, mode="normal", uid=SERVICE):
        self.process = subprocess.Popen(self.command("serve", mode), **self.options(uid))
        deadline = time.monotonic() + 8
        path = self.root / "run" / "custodian.sock"
        while time.monotonic() < deadline:
            if self.process.poll() is not None:
                raise AssertionError("synthetic custodian failed startup: " + self.process.communicate()[0])
            if path.exists():
                # Empty root readiness connection is rejected before frame parsing.
                # This is not a retry of any protocol attempt or external write.
                with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as probe:
                    try:
                        probe.connect(str(path))
                        return
                    except ConnectionRefusedError:
                        pass
            time.sleep(0.01)
        raise AssertionError("synthetic custodian did not listen")

    def stop(self):
        if self.process is not None:
            if self.process.poll() is None:
                self.process.terminate()
            try:
                self.process.communicate(timeout=3)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.communicate(timeout=3)
            self.process = None

    def restart(self):
        self.stop()
        # Explicit trusted test-operator cleanup of its exact synthetic socket;
        # neither production server nor writer is allowed to do this.
        (self.root / "run" / "custodian.sock").unlink()
        self.start()

    def close(self):
        self.stop()
        self.temporary.cleanup()


class IsolationProof(unittest.TestCase):
    def fixture(self):
        fixture = Fixture()
        self.addCleanup(fixture.close)
        return fixture

    def okay(self, result):
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        return json.loads(result.stdout) if result.stdout.startswith("{") else result.stdout.strip()

    def test_real_distinct_uids_deny_files_credentials_process_inspection_signals_and_socket_tampering(self):
        f = self.fixture()
        f.start()
        self.assertEqual(self.okay(f.run("probe", str(f.process.pid))), "DENIED_8_POSITIVE_OWN_KEY")
        self.assertEqual(self.okay(f.run("probe", "service", uid=SERVICE)), "DENIED_WRITER_KEY")
        self.assertEqual(self.okay(f.run("append"))["status"], "ANCHORED_STORAGE_ONLY")
        self.assertEqual(anchor(f.root).load().head.sequence, 1)
        self.assertEqual((f.root / "remote/puts").read_bytes(), b"PUT\n")
        self.assertEqual(self.okay(f.run("observe"))["status"], "OBSERVED_COMMITTED_RECEIPT")
        self.assertEqual(self.okay(f.run("append"))["status"], "NO_NEW_ATTEMPT")
        self.assertEqual((f.root / "remote/puts").read_bytes(), b"PUT\n")

    def test_kernel_uid_pins_reject_shared_group_stranger_root_and_wrong_server(self):
        f = self.fixture()
        f.start()
        for uid in (STRANGER, 0):
            with self.subTest(uid=uid):
                self.assertNotEqual(f.run("append", uid=uid).returncode, 0) if uid == 0 else self.assertEqual(
                    self.okay(f.run("append", uid=uid))["status"], "UNKNOWN")
        self.assertNotEqual(f.run("wrong_server").returncode, 0)
        self.assertEqual(anchor(f.root).load().revision, 0)
        self.assertEqual((f.root / "remote/puts").read_bytes(), b"")
        self.assertEqual(self.okay(f.run("append"))["status"], "ANCHORED_STORAGE_ONLY")

    def test_client_kernel_peer_check_rejects_forged_inode_owner_before_event_disclosure(self):
        f = self.fixture()
        path = f.root / "run/custodian.sock"
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as impostor:
            impostor.bind(str(path))
            os.chown(path, SERVICE, GROUP)  # test operator forges metadata, not kernel credentials
            path.chmod(0o660)
            impostor.listen(1)
            impostor.settimeout(2)
            self.assertEqual(f.run("append").returncode, 23)
            connection, _ = impostor.accept()
            with connection:
                connection.settimeout(2)
                self.assertEqual(connection.recv(1), b"")  # no event or any frame disclosed
        self.assertEqual(anchor(f.root).load().revision, 0)
        self.assertEqual((f.root / "remote/puts").read_bytes(), b"")

    def test_invalid_connection_encoding_is_sanitized_and_socket_is_closed(self):
        f = self.fixture()
        self.assertEqual(self.okay(f.run("unicode_connection")), "FIXED_ERROR_NO_FD_LEAK")

    def test_wrong_binding_oversize_and_forged_claim_confirm_do_not_prepare(self):
        f = self.fixture()
        f.start()
        for mode in ("wrong_binding", "oversize", "claim", "confirm"):
            with self.subTest(mode=mode):
                self.assertNotEqual(f.run("raw", mode).returncode, 0)
                self.assertEqual(anchor(f.root).load().revision, 0)
                self.assertEqual((f.root / "remote/puts").read_bytes(), b"")

    def test_disconnect_and_restart_never_restore_tickets_or_adopt_remote_bytes(self):
        for mode, revision in (("before_claim", 1), ("after_claim", 2), ("after_put", 2)):
            with self.subTest(mode=mode):
                f = self.fixture()
                f.start()
                self.assertEqual(f.run("raw", mode).returncode, 0)
                self.assertEqual(anchor(f.root).load().revision, revision)
                f.restart()
                self.assertEqual(self.okay(f.run("observe"))["status"], "PENDING_REQUIRES_REVIEW")
                self.assertEqual(self.okay(f.run("append"))["status"], "UNKNOWN")
                self.assertNotEqual(f.run("raw", "confirm").returncode, 0)
                self.assertEqual(anchor(f.root).load().revision, revision)
                self.assertEqual(anchor(f.root).load().head.sequence, 0)
                self.assertEqual((f.root / "remote/puts").read_bytes(), b"PUT\n" if mode == "after_put" else b"")

    def test_conflict_unknown_and_cancellation_never_confirm_or_retry(self):
        for mode, status in (("conflict", "CONFLICT"), ("unknown", "UNKNOWN"), ("interrupt", None)):
            with self.subTest(mode=mode):
                f = self.fixture()
                f.start()
                result = f.run("append", mode)
                if status:
                    self.assertEqual(self.okay(result)["status"], status)
                else:
                    self.assertEqual(result.returncode, 42)
                f.restart()
                self.assertEqual(self.okay(f.run("observe"))["status"], "PENDING_REQUIRES_REVIEW")
                self.assertEqual(anchor(f.root).load().revision, 2)
                self.assertEqual(anchor(f.root).load().head.sequence, 0)
                self.assertEqual((f.root / "remote/puts").read_bytes(), b"PUT\n")

    def test_lost_confirmation_reply_is_uncertain_and_read_only_receipt_diagnoses_commit(self):
        f = self.fixture()
        f.start("lost_reply")
        self.assertEqual(self.okay(f.run("append"))["status"], "ANCHOR_RESPONSE_UNKNOWN")
        f.restart()
        self.assertEqual(anchor(f.root).load().revision, 3)
        self.assertEqual(self.okay(f.run("observe"))["status"], "OBSERVED_COMMITTED_RECEIPT")
        self.assertEqual((f.root / "remote/puts").read_bytes(), b"PUT\n")

    def test_reader_io_is_not_preempted_but_expired_session_cannot_dispatch(self):
        f = self.fixture()
        f.start("slow_reader")
        self.assertEqual(self.okay(f.run("append"))["status"], "UNKNOWN")
        self.assertEqual(anchor(f.root).load().revision, 1)
        self.assertEqual((f.root / "remote/puts").read_bytes(), b"")
        f.restart()
        self.assertEqual(self.okay(f.run("observe"))["status"], "PENDING_REQUIRES_REVIEW")

    def test_constructor_filesystem_errors_are_fixed_and_private_paths_do_not_escape(self):
        f = self.fixture()
        (f.root / "private/anchor.sqlite").unlink()
        result = self.okay(f.run("constructor_error", uid=SERVICE))
        self.assertEqual(result, {"error": "custodian files unavailable"})
        self.assertFalse((f.root / "private/anchor.sqlite").exists())

    def test_unsafe_permissions_links_ownership_root_and_shared_uid_fail_closed(self):
        for mode in ("permissions", "owner", "hardlink", "symlink", "run_writable", "stale", "root", "same_uid"):
            with self.subTest(mode=mode):
                f = self.fixture()
                db = f.root / "private/anchor.sqlite"
                before = db.read_bytes()
                if mode == "permissions":
                    db.chmod(0o640)
                elif mode == "owner":
                    os.chown(db, WRITER, GROUP)
                elif mode == "hardlink":
                    os.link(db, f.root / "private/alias")
                elif mode == "symlink":
                    db.rename(f.root / "private/real")
                    db.symlink_to(f.root / "private/real")
                elif mode == "run_writable":
                    (f.root / "run").chmod(0o770)
                elif mode == "stale":
                    (f.root / "run/custodian.sock").write_bytes(b"do-not-delete")
                result = f.run("serve", "same_uid" if mode == "same_uid" else "normal", uid=0 if mode == "root" else SERVICE)
                self.assertNotEqual(result.returncode, 0)
                self.assertEqual(db.read_bytes(), before)
                if mode == "stale":
                    self.assertEqual((f.root / "run/custodian.sock").read_bytes(), b"do-not-delete")


if __name__ == "__main__":
    if len(sys.argv) == 4:
        try:
            child(sys.argv[1], Path(sys.argv[2]), sys.argv[3])
        except Exception:
            print("REFUSED")
            sys.exit(23)
    else:
        if sys.platform != "linux" or os.geteuid() != 0:
            raise SystemExit("FAIL: Linux root test orchestrator required; no skipped proof")
        unittest.main(argv=[sys.argv[0]], verbosity=2)
