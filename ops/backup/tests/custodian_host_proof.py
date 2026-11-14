"""Synthetic real-UID host-entry proof; no service/account installation or live IO.

Requires Linux root test orchestrator and the seven hash-pinned host packages.
All source/config/keys/state are copies under a fresh temporary fixture. Network
and subprocess entry points are instrumented to fail before external IO.
"""
import hashlib
import importlib.util
import json
import marshal
import os
from pathlib import Path
import runpy
import shutil
import socket
import struct
import subprocess
import sys
import tempfile
import time
import unittest

ROOT = Path(__file__).absolute().parents[3]

SERVICE, WRITER, GROUP = 46011, 46012, 46012
GENERATION = "1e534b17-05e7-4529-8ffa-c32162c67ad5"
SCOPE = "synthetic"
S3 = dict(endpoint="https://s3.ru-3.storage.selcloud.ru", region="ru-3",
          bucket="synthetic-custodian", key="pult/erasure-test/synthetic/journal.json")
LOCATION = S3["endpoint"] + "/" + S3["bucket"] + "/" + S3["key"]
CREDENTIAL = dict(access_key="SYNTHETIC_READER", secret_key="SYNTHETIC_SECRET_NEVER_PRINT")
ATTEMPT = "a9e1135b-aab0-4b4e-a10d-8f96cc696d96"


def anchor(root):
    return SQLiteAnchor(root / "state/anchor.sqlite", scope=SCOPE, generation=GENERATION, location=LOCATION)


def acl(uid, *, extra=False):
    entries = [(1, 4, 0xffffffff), (2, 4, uid)]
    if extra:
        entries.append((2, 4, WRITER))
    entries.extend([(4, 0, 0xffffffff), (16, 4, 0xffffffff), (32, 0, 0xffffffff)])
    return struct.pack("<I", 2) + b"".join(struct.pack("<HHI", *item) for item in entries)


def child(root, mode, target_args):
    if mode == "read_credential":
        try:
            (root / "credential/reader.json").read_bytes()
        except PermissionError:
            print("PRIVATE_CREDENTIAL_DENIED")
            return 0
        return 9

    def blocked(*args, **kwargs):
        (root / "state/io-attempt").write_text("FORBIDDEN_IO")
        raise AssertionError("external IO attempted")

    original_connect = socket.socket.connect
    original_bind = socket.socket.bind

    def unix_connect(connection, *args, **kwargs):
        if mode == "serve" and connection.family == socket.AF_UNIX:
            return original_connect(connection, *args, **kwargs)
        return blocked()

    def unix_bind(connection, *args, **kwargs):
        if mode == "serve" and connection.family == socket.AF_UNIX:
            return original_bind(connection, *args, **kwargs)
        return blocked()

    socket.socket.connect = unix_connect
    socket.socket.connect_ex = blocked
    socket.socket.bind = unix_bind
    if mode != "serve":
        socket.socket.listen = blocked
    socket.getaddrinfo = blocked
    socket.create_connection = blocked
    subprocess.Popen = blocked
    subprocess.run = blocked
    sys.argv = [str(root / "code/ops/backup/erasure_custodian_host.py"), *target_args]
    runpy.run_path(sys.argv[0], run_name="__main__")


if __name__ == "__main__" and len(sys.argv) >= 5 and sys.argv[1] == "child":
    raise SystemExit(child(Path(sys.argv[2]), sys.argv[3], sys.argv[4:]))

sys.path.insert(0, str(ROOT))
from ops.backup.erasure_anchor import SQLiteAnchor
from ops.backup.erasure_journal import Event, Journal, Stored, empty, genesis
from ops.backup import erasure_custodian_host as host


class Fixture:
    def __init__(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="pult-custodian-host-")
        self.root = Path(self.temporary.name)
        self.root.chmod(0o755)
        self.process = None
        self.python = sys.executable
        for name in host.CODE_FILES:
            target = self.root / "code" / name
            target.parent.mkdir(parents=True, exist_ok=True)
            for parent in target.parents:
                if parent == self.root:
                    break
                parent.chmod(0o755)
            shutil.copyfile(ROOT / name, target)
            target.chmod(0o444)
        support = self.root / "support"
        support.mkdir(mode=0o755)
        shutil.copyfile(ROOT / "ops/backup/tests/custodian_host_proof.py", support / "host_proof.py")
        (support / "host_proof.py").chmod(0o444)
        for name, uid, mode in (("state", SERVICE, 0o700), ("run", SERVICE, 0o750),
                                ("config", 0, 0o755), ("credential", 0, 0o755)):
            path = self.root / name
            path.mkdir()
            os.chown(path, uid, GROUP)
            path.chmod(mode)
        self.config = dict(format=host.FORMAT, scope=SCOPE, generation=GENERATION,
                           custodian_uid=SERVICE, writer_uid=WRITER, socket_gid=GROUP,
                           state_file=str(self.root / "state/anchor.sqlite"),
                           socket_file=str(self.root / "run/custodian.sock"), s3=S3)
        self.write_config()
        self.credential = self.root / "credential/reader.json"
        self.credential.write_text(json.dumps(CREDENTIAL))
        os.chown(self.credential, SERVICE, GROUP)
        self.credential.chmod(0o400)
        anchor(self.root).create(genesis(SCOPE))
        db = self.root / "state/anchor.sqlite"
        os.chown(db, SERVICE, GROUP)
        db.chmod(0o600)
        self.environment = {
            "PATH": "/usr/bin:/bin", "PYTHONDONTWRITEBYTECODE": "1",
            "PYTHONPATH": str(self.root / "poison"),
            "AWS_ACCESS_KEY_ID": "ENV_SECRET_NEVER_PRINT", "AWS_SECRET_ACCESS_KEY": "ENV_SECRET_NEVER_PRINT",
            "BACKUP_S3_ACCESS_KEY_ID": "ENV_SECRET_NEVER_PRINT", "BACKUP_S3_SECRET_ACCESS_KEY": "ENV_SECRET_NEVER_PRINT",
            "HTTPS_PROXY": "http://invalid.invalid:1", "SSL_CERT_FILE": "/missing/not-used",
        }
        poison = self.root / "poison"
        poison.mkdir()
        (poison / "sitecustomize.py").write_text("raise RuntimeError('PYTHONPATH_INJECTION')\n")

    def write_config(self):
        target = self.root / "config/host.json"
        target.write_text(json.dumps(self.config))
        target.chmod(0o644)

    def command(self, mode="check", *, invalid_cli=False):
        arguments = ["--config", str(self.root / "config/host.json"), "--reader-credential-file", str(self.credential),
                     "--serve" if mode == "serve" else "--check"]
        if invalid_cli:
            arguments.extend(["--unexpected", "ARGV_SECRET_NEVER_PRINT"])
        return [self.python, "-I", "-S", "-B", str(self.root / "support/host_proof.py"),
                "child", str(self.root), mode, *arguments]

    def options(self, uid=SERVICE, groups=()):
        return dict(user=uid, group=GROUP, extra_groups=list(groups), env=self.environment,
                    cwd=self.root / "code", stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)

    def run(self, mode="check", *, uid=SERVICE, groups=(), invalid_cli=False):
        return subprocess.run(self.command(mode, invalid_cli=invalid_cli), **self.options(uid, groups), timeout=15)

    def start(self):
        self.process = subprocess.Popen(self.command("serve"), **self.options())
        deadline = time.monotonic() + 8
        while time.monotonic() < deadline:
            if self.process.poll() is not None:
                raise AssertionError("host startup failed: " + str(self.process.communicate()))
            if Path(self.config["socket_file"]).exists():
                with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as probe:
                    try:
                        probe.connect(self.config["socket_file"])  # empty root peer, no frame/protocol call
                        return
                    except ConnectionRefusedError:
                        pass
            time.sleep(0.01)
        raise AssertionError("host did not create listener")

    def close(self):
        if self.process is not None:
            if self.process.poll() is None:
                self.process.terminate()
            try:
                self.process.communicate(timeout=3)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.communicate(timeout=3)
        self.temporary.cleanup()


class HostProof(unittest.TestCase):
    def fixture(self):
        result = Fixture()
        self.addCleanup(result.close)
        return result

    def status(self, fixture, expected=0, **kwargs):
        before = (fixture.root / "state/anchor.sqlite").read_bytes() if Path(fixture.config["state_file"]).is_file() else None
        result = fixture.run(**kwargs)
        self.assertEqual(result.returncode, expected, result.stdout + result.stderr)
        self.assertEqual(result.stdout.strip(), "HOST_PREFLIGHT_OK_OFFLINE_ONLY" if expected == 0 else "")
        self.assertEqual(result.stderr.strip(), "" if expected == 0 else "HOST_REFUSED")
        self.assertFalse((fixture.root / "state/io-attempt").exists())
        self.assertFalse(Path(fixture.config["socket_file"]).exists())
        for marker in ("SYNTHETIC_SECRET_NEVER_PRINT", "ENV_SECRET_NEVER_PRINT", "ARGV_SECRET_NEVER_PRINT", str(fixture.root)):
            self.assertNotIn(marker, result.stdout + result.stderr)
        if before is not None:
            self.assertEqual((fixture.root / "state/anchor.sqlite").read_bytes(), before)

    def test_real_unprivileged_entry_preflight_is_offline_despite_poisoned_environment(self):
        f = self.fixture()
        self.status(f)
        self.assertEqual(f.run("read_credential", uid=WRITER).stdout.strip(), "PRIVATE_CREDENTIAL_DENIED")

    def test_exact_source_tree_allows_only_its_actual_adjacent_venv(self):
        f = self.fixture()
        runtime = f.root / "code/venv"
        shutil.copytree(Path(sys.executable).absolute().parent.parent, runtime, symlinks=True)
        f.python = str(runtime / "bin/python")
        self.status(f)

    def test_actual_systemd_style_acl_accepts_only_the_named_service_reader(self):
        f = self.fixture()
        os.chown(f.credential, 0, GROUP)
        f.credential.chmod(0o400)
        os.setxattr(f.credential, "system.posix_acl_access", acl(SERVICE))
        self.assertEqual(f.credential.stat().st_mode & 0o777, 0o440)
        self.status(f)
        self.assertEqual(f.run("read_credential", uid=WRITER).stdout.strip(), "PRIVATE_CREDENTIAL_DENIED")
        os.setxattr(f.credential, "system.posix_acl_access", acl(SERVICE, extra=True))
        self.status(f, 1)  # service can still read; extra reader must be rejected by the loader

    def test_missing_explicit_credential_never_falls_back_to_environment(self):
        f = self.fixture()
        f.credential.unlink()
        self.status(f, 1)

    def test_private_input_type_permissions_ownership_and_links_fail_without_repair(self):
        for mode in ("world", "group", "owner", "hardlink", "symlink", "fifo", "oversize", "unknown", "duplicate"):
            with self.subTest(mode=mode):
                f = self.fixture()
                if mode in ("world", "group"):
                    f.credential.chmod(0o644 if mode == "world" else 0o640)
                elif mode == "owner":
                    os.chown(f.credential, WRITER, GROUP)
                elif mode == "hardlink":
                    os.link(f.credential, f.root / "credential/alias")
                elif mode == "symlink":
                    f.credential.rename(f.root / "credential/original")
                    f.credential.symlink_to(f.root / "credential/original")
                elif mode == "fifo":
                    f.credential.unlink()
                    os.mkfifo(f.credential, 0o600)
                    os.chown(f.credential, SERVICE, GROUP)
                elif mode == "oversize":
                    f.credential.write_bytes(b"x" * (host.MAX_INPUT + 1))
                elif mode == "unknown":
                    f.credential.write_text(json.dumps(dict(CREDENTIAL, writer="FORBIDDEN")))
                else:
                    f.credential.write_text('{"access_key":"A","access_key":"B","secret_key":"C"}')
                metadata = f.credential.lstat()
                self.status(f, 1)
                after = f.credential.lstat()
                for field in ("st_mode", "st_uid", "st_gid", "st_nlink", "st_ino", "st_size"):
                    self.assertEqual(getattr(after, field), getattr(metadata, field))

    def test_root_config_mismatch_unknown_fields_and_writer_owned_inputs_are_refused(self):
        for mode in ("owner", "group_write", "generation", "scope", "key", "endpoint", "unknown", "boolean"):
            with self.subTest(mode=mode):
                f = self.fixture()
                path = f.root / "config/host.json"
                if mode == "owner":
                    os.chown(path, WRITER, GROUP)
                elif mode == "group_write":
                    path.chmod(0o664)
                else:
                    if mode == "generation": f.config["generation"] = "82047569-5c91-4b10-87c4-3a8b121e1891"
                    elif mode == "scope": f.config["scope"] = "other"
                    elif mode == "key": f.config["s3"] = dict(S3, key="pult/other/journal.json")
                    elif mode == "endpoint": f.config["s3"] = dict(S3, endpoint="http://127.0.0.1:1")
                    elif mode == "unknown": f.config["bootstrap"] = True
                    else: f.config["writer_uid"] = True
                    f.write_config()
                self.status(f, 1)

    def test_missing_state_is_not_bootstrapped_and_stale_socket_is_not_deleted(self):
        f = self.fixture()
        Path(f.config["state_file"]).unlink()
        self.status(f, 1)
        self.assertFalse(Path(f.config["state_file"]).exists())
        f = self.fixture()
        path = Path(f.config["socket_file"])
        path.write_text("STALE_DO_NOT_REMOVE")
        result = f.run()
        self.assertEqual(result.returncode, 1)
        self.assertEqual(result.stderr.strip(), "HOST_REFUSED")
        self.assertEqual(path.read_text(), "STALE_DO_NOT_REMOVE")
        self.assertFalse((f.root / "state/io-attempt").exists())

    def test_wrong_root_and_supplementary_identities_are_refused(self):
        for options in ({"uid": 0}, {"uid": WRITER}, {"groups": (0,)}, {"groups": (12345,)}):
            with self.subTest(options=options):
                self.status(self.fixture(), 1, **options)

    def test_pending_state_survives_preflight_without_reset_or_remote_adoption(self):
        f = self.fixture()
        class Reader:
            def read(self): return Stored(empty(SCOPE).encode(), '"synthetic"')
        candidate = Journal(Reader(), SCOPE).prepare(Event("73e1c70a-3840-40ec-b5a2-6380343d9cbf", "770d659f-c20f-46dc-b3bc-3f6000204bc6", "intent"), genesis(SCOPE))
        store = anchor(f.root)
        store.prepare(store.load(), ATTEMPT, candidate)
        before = store.load()
        self.status(f)
        self.assertEqual(store.load(), before)
        self.assertEqual(before.pending, ATTEMPT)

    def test_serve_is_explicit_creates_only_local_listener_and_stops_without_cleanup(self):
        f = self.fixture()
        self.status(f)
        f.start()
        self.assertFalse((f.root / "state/io-attempt").exists())
        self.assertEqual(anchor(f.root).load().revision, 0)
        f.process.terminate()
        f.process.communicate(timeout=3)
        self.assertTrue(Path(f.config["socket_file"]).exists())
        result = f.run()
        self.assertEqual(result.returncode, 1)
        self.assertTrue(Path(f.config["socket_file"]).exists())

    def test_argument_errors_never_echo_supplied_secrets(self):
        self.status(self.fixture(), 1, invalid_cli=True)

    def test_foreign_package_initializer_is_rejected_before_execution(self):
        for name in ("ops/__init__.py", "ops.py", "ops/backup/__init__.py", "ops/backup.py"):
            with self.subTest(name=name):
                f = self.fixture()
                marker = f.root / "state/foreign-initializer"
                initializer = f.root / "code" / name
                initializer.write_text("from pathlib import Path\nPath(" + repr(str(marker)) + ").write_text('EXECUTED')\n")
                initializer.chmod(0o444)
                self.status(f, 1)
                self.assertFalse(marker.exists())

    def test_untrusted_runtime_initializer_and_pth_are_rejected_before_side_effects(self):
        for mode in ("initializer", "pth"):
            with self.subTest(mode=mode):
                f = self.fixture()
                runtime = f.root / "isolated-runtime"
                shutil.copytree(Path(sys.executable).absolute().parent.parent, runtime, symlinks=True)
                f.python = str(runtime / "bin/python")
                site = runtime / "lib" / ("python%d.%d" % sys.version_info[:2]) / "site-packages"
                marker = f.root / "state/runtime-injection"
                code = "from pathlib import Path; Path(" + repr(str(marker)) + ").write_text('EXECUTED')\n"
                if mode == "initializer":
                    target = site / "httpx/__init__.py"
                    target.write_text(code)
                    os.chown(target, WRITER, GROUP)
                else:
                    (site / "unexpected.pth").write_text("import pathlib; pathlib.Path(" + repr(str(marker)) + ").write_text('EXECUTED')\n")
                self.status(f, 1)
                self.assertFalse(marker.exists())

    def test_valid_foreign_core_bytecode_is_rejected_before_side_effects(self):
        f = self.fixture()
        source = f.root / "code/ops/backup/erasure_journal.py"
        marker = f.root / "state/core-cache-injection"
        cache = Path(importlib.util.cache_from_source(str(source)))
        cache.parent.mkdir(mode=0o755)
        metadata = source.stat()
        code = compile("from pathlib import Path\nPath(" + repr(str(marker)) + ").write_text('EXECUTED')\n", str(source), "exec")
        # Valid timestamp-based CPython cache: a malformed cache would only prove
        # that the interpreter ignored it, not rejection before code execution.
        header = importlib.util.MAGIC_NUMBER + struct.pack("<III", 0, int(metadata.st_mtime) & 0xffffffff, metadata.st_size)
        cache.write_bytes(header + marshal.dumps(code))
        os.chown(cache, WRITER, GROUP)
        cache.chmod(0o644)
        self.status(f, 1)
        self.assertFalse(marker.exists())

    def test_core_package_replacements_are_rejected_before_side_effects(self):
        for name in ("erasure_custodian", "erasure_anchor", "erasure_journal", "erasure_journal_s3"):
            with self.subTest(name=name):
                f = self.fixture()
                marker = f.root / "state/core-package-injection"
                directory = f.root / "code/ops/backup" / name
                directory.mkdir(mode=0o755)
                source = directory / "__init__.py"
                source.write_text("from pathlib import Path\nPath(" + repr(str(marker)) + ").write_text('EXECUTED')\n")
                os.chown(source, WRITER, GROUP)
                source.chmod(0o644)
                self.status(f, 1)
                self.assertFalse(marker.exists())

    def test_source_root_dependency_shadows_are_rejected_before_side_effects(self):
        for name in ("httpx.py", "httpx/__init__.py", "sqlite3.py"):
            with self.subTest(name=name):
                f = self.fixture()
                marker = f.root / "state/root-module-injection"
                source = f.root / "code" / name
                source.parent.mkdir(parents=True, exist_ok=True)
                source.write_text("from pathlib import Path\nPath(" + repr(str(marker)) + ").write_text('EXECUTED')\nraise RuntimeError('FOREIGN_MODULE')\n")
                os.chown(source, WRITER, GROUP)
                source.chmod(0o644)
                self.status(f, 1)
                self.assertFalse(marker.exists())

    def test_unit_template_parses_without_installing_or_starting_a_service(self):
        f = self.fixture()
        text = (ROOT / "ops/backup/custodian/pult-erasure-custodian.service").read_text()
        text = text.replace("/opt/pult-erasure-custodian/venv/bin/python", sys.executable)
        text = text.replace("/opt/pult-erasure-custodian", str(f.root / "code"))
        text = text.replace("/etc/pult-erasure-custodian/reader.json", str(f.credential))
        text = text.replace("/var/lib/pult-erasure-custodian", str(f.root / "state"))
        text = text.replace("/run/pult-erasure-custodian", str(f.root / "run"))
        target = f.root / "pult-erasure-custodian.service"
        target.write_text(text)
        result = subprocess.run(["systemd-analyze", "verify", "--man=no", str(target)],
                                env={"PATH": "/usr/bin:/bin"}, capture_output=True, text=True, timeout=15)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertFalse(Path(f.config["socket_file"]).exists())


if __name__ == "__main__":
    if sys.platform != "linux" or os.geteuid() != 0:
        raise SystemExit("FAIL: Linux root synthetic orchestrator required; no skipped host proof")
    unittest.main(argv=[sys.argv[0]], verbosity=2)
