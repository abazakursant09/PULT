"""Protected test-only PG/operator composition; never a production PG mode.

CLI: fixture.py controller ROOT CASE CONFIG_SHA BOOTSTRAP_SHA
     fixture.py worker|client ROOT CASE CONFIG_SHA BOOTSTRAP_SHA PROFILE_SHA
All third-party imports follow complete protected configuration/source/deps checks.
"""
import asyncio
from dataclasses import asdict
import hashlib
import json
import logging
import os
from pathlib import Path
import re
import signal
import socket
import stat
import sys
import time
from types import ModuleType
from uuid import uuid4
import threading

CASES = ("authorized", "close_then_raise", "blocked_close_hang",
         "controller_death", "wrong_peer", "restored")
FORMAT = "pult-operator-pg-joint-fixture-v1"
ACTOR, GROUP, OPERATOR = 46201, 46202, 46202
MARKERS = {
    "reviewer-called.json": ("monotonic_ns",),
    "connection-open.json": ("pid", "monotonic_ns"),
    "driver-close-enter.json": ("pid", "monotonic_ns"),
    "driver-closed.json": ("pid", "monotonic_ns"),
    "lock-timeout.json": ("sqlstate", "monotonic_ns"),
    "pool-state.json": ("connections", "cleanup_failed", "repeat_refused",
                        "connections_after_repeat", "native_closed_before_observation"),
}
BACKEND = {
    "policy": ("backend/services/account_closure_policy.py",
               "e606c8b81793a54caa94b1bf9d3c88a21dc7df0f211d60cca6b8a483d8e750cf"),
    "reviewer": ("backend/services/erasure_operator_review.py",
                 "efaa87935a5e6df3fdb8077cabba1b41f0cbb725d11430e896c454483ccc2606"),
}
BOOTSTRAP_SHA = "96141d0e26cfc718e9cb762f689d20cad90f22c815dadec587fcac8c7580d3fa"
BASELINE_SHA = "c51993af7ed4fc52dac8391a2a7139152c39fc469e412bc936cc71fc0e1fd0bb"
NEW_SOURCE_PINS = {
    "profile": "0998c8922c470406d2789727b749ed9365f36f2fa3ee70eea7448d3693e31edf",
    "host": "5a47b32282fa4c6533ea0c4ed2602523dc2054cb8e567da571de3962b877e356",
    "bootstrap": BOOTSTRAP_SHA,
    "worker": "5e7993f35e9d50019af79ecb2335cb2fe5111f38f06c83f70fc01304d425a06c",
}


def require(ok):
    if not ok:
        raise RuntimeError("joint_fixture_refused")


def canonical(value):
    return (json.dumps(value, sort_keys=True, separators=(",", ":"),
                       ensure_ascii=True, allow_nan=False)+"\n").encode("ascii")


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


def record(path, value):
    raw = canonical(value)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC, 0o600)
    try:
        os.fchmod(fd, 0o600)
        require(os.write(fd, raw) == len(raw))
        os.fsync(fd)
    finally:
        os.close(fd)


def _sha(value):
    require(type(value) is str and re.fullmatch("[0-9a-f]{64}", value))
    return value


def _stamp(info):
    return tuple(getattr(info, k) for k in (
        "st_dev", "st_ino", "st_mode", "st_uid", "st_gid", "st_nlink",
        "st_size", "st_mtime_ns", "st_ctime_ns"))


def protected(file, maximum=100000):
    file = Path(file)
    require(file.is_absolute() and str(file) == os.path.normpath(str(file)))
    for parent in file.parents:
        info = parent.lstat()
        require(stat.S_ISDIR(info.st_mode) and info.st_uid == 0
                and (not info.st_mode & 0o022 or info.st_mode & stat.S_ISVTX)
                and not any(n.startswith("system.posix_acl_")
                            for n in os.listxattr(parent, follow_symlinks=False)))
    fd = os.open(file, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC)
    try:
        before = os.fstat(fd)
        require(stat.S_ISREG(before.st_mode) and before.st_uid == 0
                and before.st_nlink == 1 and stat.S_IMODE(before.st_mode) == 0o444
                and 0 <= before.st_size <= maximum
                and not any(n.startswith("system.posix_acl_") for n in os.listxattr(fd)))
        raw = os.pread(fd, maximum+1, 0)
        require(len(raw) == before.st_size and _stamp(os.fstat(fd)) == _stamp(before)
                and _stamp(file.lstat()) == _stamp(before))
        return raw
    finally:
        os.close(fd)


def document(raw):
    def pairs(rows):
        result = {}
        for key, value in rows:
            require(key not in result)
            result[key] = value
        return result
    value = json.loads(raw, object_pairs_hook=pairs)
    require(type(value) is dict and canonical(value) == raw)
    return value


def configuration(root, case, approval, bootstrap_sha):
    require(sys.platform == "linux" and sys.implementation.name == "cpython"
            and sys.version_info[:2] == (3, 12)
            and Path(sys.executable).resolve() == Path("/usr/bin/python3.12")
            and sys.flags.isolated and sys.flags.no_site and sys.dont_write_bytecode
            and not sys.flags.optimize and threading.current_thread() is threading.main_thread()
            and len(os.listdir("/proc/self/task")) == 1)
    require(case in CASES and root.parent == Path("/tmp")
            and re.fullmatch(r"pult-op-pg-[A-Za-z0-9_-]+", root.name)
            and not root.is_symlink() and Path(__file__) == root/"fixture.py")
    info = root.lstat()
    require(stat.S_ISDIR(info.st_mode) and info.st_uid == 0
            and stat.S_IMODE(info.st_mode) == 0o755)
    raw = protected(root/"test.json", 16384)
    require(digest(raw) == _sha(approval) and bootstrap_sha == BOOTSTRAP_SHA)
    cfg = document(raw)
    require(set(cfg) == {"format", "case", "root", "fixture_sha256", "bootstrap_sha256",
                         "dependencies_manifest_sha256", "backend_sources", "pg"}
            and cfg["format"] == FORMAT and cfg["case"] == case and cfg["root"] == str(root)
            and cfg["bootstrap_sha256"] == bootstrap_sha
            and digest(protected(root/"fixture.py")) == _sha(cfg["fixture_sha256"]))
    require(type(cfg["backend_sources"]) is dict and set(cfg["backend_sources"]) == set(BACKEND))
    blobs = {}
    for role, (relative, wanted) in BACKEND.items():
        item = cfg["backend_sources"][role]
        require(type(item) is dict and set(item) == {"path", "sha256"}
                and item["path"] == str(root/"release"/relative) and item["sha256"] == wanted)
        blobs[role] = protected(Path(item["path"]))
        require(digest(blobs[role]) == wanted)
    require(digest(protected(root/"release/ops/backup/tests/operator_offline_host_proof.py")) == BASELINE_SHA)
    pg = cfg["pg"]
    require(type(pg) is dict and set(pg) == {"socket", "database", "database_oid",
            "reader_role", "reader_password", "application_name"})
    require(type(pg["socket"]) is str)
    socket_dir = Path(pg["socket"])
    require(socket_dir.is_absolute() and str(socket_dir) == os.path.normpath(str(socket_dir))
            and root in socket_dir.parents and not socket_dir.is_symlink())
    for key in ("database", "reader_role"):
        require(type(pg[key]) is str and re.fullmatch(r"[a-z_][a-z0-9_]{0,62}", pg[key]))
    require(type(pg["database_oid"]) is int and 16384 <= pg["database_oid"] < 2**32
            and type(pg["reader_password"]) is str and 1 <= len(pg["reader_password"]) <= 128
            and pg["reader_password"].isascii()
            and all(32 < ord(c) < 127 for c in pg["reader_password"])
            and type(pg["application_name"]) is str
            and re.fullmatch(r"[A-Za-z0-9_.-]{1,63}", pg["application_name"]))
    deps_raw = protected(root/"deps-manifest.json", 1048576)
    require(digest(deps_raw) == _sha(cfg["dependencies_manifest_sha256"]))
    deps = document(deps_raw)
    require(0 < len(deps) <= 2048)
    deps_info = (root/"deps").lstat()
    require(stat.S_ISDIR(deps_info.st_mode) and deps_info.st_uid == 0
            and stat.S_IMODE(deps_info.st_mode) == 0o755
            and not any(n.startswith("system.posix_acl_")
                        for n in os.listxattr(root/"deps", follow_symlinks=False)))
    actual = set()
    for item in (root/"deps").rglob("*"):
        info = item.lstat()
        if stat.S_ISDIR(info.st_mode):
            require(info.st_uid == 0 and stat.S_IMODE(info.st_mode) == 0o755
                    and not any(n.startswith("system.posix_acl_")
                                for n in os.listxattr(item, follow_symlinks=False)))
        else:
            relative = item.relative_to(root/"deps").as_posix()
            require(relative in deps and digest(protected(item, 33554432)) == _sha(deps[relative]))
            actual.add(relative)
    require(actual == set(deps))
    # These approved stdlib-only modules execute only after their own capture.
    bootstrap_path = root/"release/ops/backup/operator_host/bootstrap.py"
    body = protected(bootstrap_path)
    require(digest(body) == bootstrap_sha)
    bootstrap = ModuleType("_joint_trusted_bootstrap")
    bootstrap.__file__ = str(bootstrap_path)
    exec(compile(body, str(bootstrap_path), "exec", dont_inherit=True, optimize=0), bootstrap.__dict__)
    return cfg, blobs, bootstrap


def profile_for(root, bootstrap, approval):
    profile = bootstrap.load_profile(root/"profile.json", _sha(approval))
    require(profile.spec["actor"] == dict(uid=ACTOR, gid=GROUP)
            and profile.spec["operator_uid"] == OPERATOR
            and profile.release == root/"release" and profile.control == root/"control"
            and profile.ipc == root/"ipc")
    require(all(profile.spec["sources"][name]["sha256"] == pin
                for name, pin in NEW_SOURCE_PINS.items()))
    return profile


def controller(root, case, config_sha, bootstrap_sha, cfg, bootstrap):
    require(os.getuid() == os.geteuid() == 0)
    print("CONTROLLER_READY "+str(os.getpid()), flush=True)
    raw = sys.stdin.buffer.readline(66)
    require(re.fullmatch(rb"[0-9a-f]{64}\n", raw))
    profile = profile_for(root, bootstrap, raw[:-1].decode("ascii"))
    host = profile.capture("host")

    class Recorded(host.Bound):
        def command(self, argv):
            expected = ["/usr/bin/python3.12", "-I", "-S", "-B",
                        profile.spec["sources"]["bootstrap"]["path"], str(profile.file), profile.sha]
            require(argv[-7:] == expected)
            record(root/"actor.json", dict(unit=self.unit, approval=profile.sha))
            return argv[:-7]+["/usr/bin/python3.12", "-I", "-S", "-B", str(root/"fixture.py"),
                             "worker", str(root), case, config_sha, bootstrap_sha, profile.sha]

        def ready(self, unit, pid):
            ticks = profile.kernel_actor(pid)
            invocation = profile.worker_policy(unit, pid)
            record(root/"ready.json", dict(unit=unit, pid=pid, start_ticks=ticks, invocation=invocation))

        def cleanup(self):
            record(root/"precleanup.json", self.supervisor._state(self.unit))
            return super().cleanup()

    bound = Recorded(profile)
    receipt = bound.run()
    require(bound.closed and all(getattr(bound, name) is None for name in ("pidfd", "fence", "lock")))
    repeated = False
    try:
        bound.run()
    except host.Refused:
        repeated = True
    claim = (profile.control/'constructor-stop.claim').read_bytes()
    again = host.Bound(profile).run()
    require(repeated and again['status'] == 'UNKNOWN_REQUIRES_REVIEW' and again['unit'] is None
            and (profile.control/'constructor-stop.claim').read_bytes() == claim)
    print(json.dumps(dict(parent_receipt=receipt, repeat_refused=True), sort_keys=True), flush=True)


def backend_modules(root, blobs):
    require(not any(n == "services" or n.startswith("services.") or n == "sqlalchemy"
                    or n.startswith("sqlalchemy.") or n == "asyncpg" or n.startswith("asyncpg.")
                    for n in sys.modules))
    sys.path.insert(0, str(root/"deps"))
    services = ModuleType("services")
    services.__path__ = []
    sys.modules["services"] = services
    result = None
    for role, leaf in (("policy", "account_closure_policy"), ("reviewer", "erasure_operator_review")):
        name = "services."+leaf
        module = ModuleType(name)
        module.__file__ = str(root/"release"/BACKEND[role][0])
        module.__package__ = "services"
        sys.modules[name] = module
        setattr(services, leaf, module)
        exec(compile(blobs[role], module.__file__, "exec", dont_inherit=True, optimize=0), module.__dict__)
        result = module
    return result


async def worker_session(root, case, cfg, blobs, profile):
    unit = profile.actor_guard()
    core = profile.operator_module()
    seams = profile.capture("worker")
    reviewer_module = backend_modules(root, blobs)
    # Protected dependency closure was verified before the first such import.
    import asyncpg
    from sqlalchemy import event
    from sqlalchemy.engine import URL
    from sqlalchemy.ext.asyncio import create_async_engine
    from sqlalchemy.pool import NullPool

    pg = cfg["pg"]
    url = URL.create("postgresql+asyncpg", username=pg["reader_role"], password=pg["reader_password"],
                     database=pg["database"], query={"host": pg["socket"]})
    engine = create_async_engine(url, poolclass=NullPool, hide_parameters=True,
        echo=False, connect_args={"timeout": 3, "server_settings": {"application_name": pg["application_name"]}})
    review = reviewer_module.CommittedDraftReviewer(engine, database_name=pg["database"],
        database_oid=pg["database_oid"], reader_role=pg["reader_role"])
    # Never allow SQLAlchemy diagnostics to expose synthetic connection values.
    review._cleanup.logger = logging.Logger("joint-private-pool", logging.CRITICAL+1)
    handles, observed = [], []
    calls, repeat_refused, after_repeat, closed_before = 0, False, 0, False
    hang = case in ("blocked_close_hang", "controller_death")
    original_close = asyncpg.Connection.close
    original_terminate = asyncpg.Connection.terminate

    def marker(name, **values):
        require(set(values) == set(MARKERS[name]))
        record(profile.ipc/name, values)

    def connected(dbapi, _record):
        native = dbapi.driver_connection
        require(type(native) is asyncpg.Connection)
        handles.append(native)
        marker("connection-open.json", pid=native.get_server_pid(), monotonic_ns=time.monotonic_ns())

    def error(context):
        if context.engine is engine.sync_engine and getattr(context.original_exception, "sqlstate", None) == "55P03":
            marker("lock-timeout.json", sqlstate="55P03", monotonic_ns=time.monotonic_ns())

    async def native_close(native, *args, **kwargs):
        if native not in handles:
            return await original_close(native, *args, **kwargs)
        pid = native.get_server_pid()
        marker("driver-close-enter.json", pid=pid, monotonic_ns=time.monotonic_ns())
        if hang:
            signal.signal(signal.SIGTERM, signal.SIG_IGN)
            while True:
                try:
                    await asyncio.sleep(3600)
                except asyncio.CancelledError:
                    continue
        await original_close(native, *args, **kwargs)
        require(native.is_closed() is True)
        marker("driver-closed.json", pid=pid, monotonic_ns=time.monotonic_ns())
        if case == "close_then_raise":
            raise RuntimeError("injected_owned_native_close_failure")

    def native_terminate(native):
        # Only owned handles in the deliberate non-cooperative fault resist
        # reviewer fallback. PID1, not a fixture-side backend kill, must end IO.
        if not (hang and native in handles):
            original_terminate(native)

    async def callback(account, draft, *, nonce):
        nonlocal calls, repeat_refused, after_repeat, closed_before
        seams._parent_live()
        require(profile.actor_guard() == unit)
        calls += 1
        require(calls == 1 and (account, draft) == (profile.spec["synthetic"]["account_id"],
                                                  profile.spec["synthetic"]["draft_id"]))
        marker("reviewer-called.json", monotonic_ns=time.monotonic_ns())
        try:
            value = await review(account, draft, nonce=nonce)
        except BaseException:
            if case == "close_then_raise":
                before = len(handles)
                try:
                    await review(account, draft, nonce=str(uuid4()))
                except core.ReviewError:
                    repeat_refused = True
                after_repeat = len(handles)
                require(repeat_refused and before == after_repeat == 1 and review._cleanup.failed)
            raise
        closed_before = len(handles) == 1 and all(h.is_closed() is True for h in handles)
        require(closed_before and type(value) is core.DraftObservation
                and all(getattr(value, key) is False for key in sys.modules["_pult_operator_profile_v1"].FLAGS))
        seams._parent_live()
        require(profile.actor_guard() == unit)
        observed.append(value)
        return value

    event.listen(engine.sync_engine, "connect", connected)
    event.listen(engine.sync_engine, "handle_error", error)
    asyncpg.Connection.close = native_close
    asyncpg.Connection.terminate = native_terminate
    previous = os.umask(0o077)
    try:
        server = core.OperatorReviewServer(profile.ipc/"review.sock",
            operator_uid=profile.spec["operator_uid"], reviewer=callback)
        core.ipc._socket_directory(server.path, server.uid)
        require(not os.path.lexists(server.path))
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as listener:
            listener.bind(str(server.path))
            os.chmod(server.path, 0o660)
            core.ipc._socket_file(server.path, server.uid, os.getegid())
            listener.listen(1)
            listener.settimeout(core.SESSION_SECONDS)
            require(profile.actor_guard() == unit)
            seams._emit(profile, unit, "READY_OFFLINE_ONLY", None)
            seams._wait_go()
            seams._parent_live()
            require(profile.actor_guard() == unit)
            connection, _ = listener.accept()
            with connection:
                seams._parent_live()
                require(profile.actor_guard() == unit)
                await server._session(connection)
    finally:
        try:
            await engine.dispose()
            marker("pool-state.json", connections=len(handles), cleanup_failed=review._cleanup.failed,
                   repeat_refused=repeat_refused, connections_after_repeat=after_repeat,
                   native_closed_before_observation=closed_before)
        finally:
            os.umask(previous)
            asyncpg.Connection.close = original_close
            asyncpg.Connection.terminate = original_terminate
            event.remove(engine.sync_engine, "connect", connected)
            event.remove(engine.sync_engine, "handle_error", error)
    require(calls == len(observed) == 1 and not review._cleanup.failed
            and closed_before and all(h.is_closed() is True for h in handles))
    seams._parent_live()
    require(profile.actor_guard() == unit)
    seams._emit(profile, unit, "CLOSED_OFFLINE_ONLY", digest(canonical(asdict(observed[0]))))


def client(profile):
    core = profile.operator_module()
    observed = core.OperatorReviewClient(profile.ipc/"review.sock", reviewer_uid=ACTOR).review(
        profile.spec["synthetic"]["account_id"], profile.spec["synthetic"]["draft_id"])
    require(type(observed) is core.DraftObservation)
    print(json.dumps(dict(kind="EARLY_NONADMITTING_OBSERVATION_NOT_PARENT_RECEIPT",
                          observation=asdict(observed)), sort_keys=True), flush=True)


def main():
    require(len(sys.argv) in (6, 7))
    role, root_text, case, config_sha, bootstrap_sha = sys.argv[1:6]
    require(role in ("controller", "worker", "client")
            and len(sys.argv) == (6 if role == "controller" else 7))
    root = Path(root_text)
    require(str(root) == root_text and root.is_absolute())
    cfg, blobs, bootstrap = configuration(root, case, config_sha, bootstrap_sha)
    if role == "controller":
        controller(root, case, config_sha, bootstrap_sha, cfg, bootstrap)
    else:
        profile = profile_for(root, bootstrap, sys.argv[6])
        if role == "worker":
            profile.actor_guard()
            asyncio.run(worker_session(root, case, cfg, blobs, profile))
        else:
            client(profile)
    return 0


if __name__ == "__main__":
    try:
        result = main()
    except BaseException:
        try:
            os.write(2, b"joint_fixture_refused\n")
        except BaseException:
            pass
        raise SystemExit(1) from None
    raise SystemExit(result)
