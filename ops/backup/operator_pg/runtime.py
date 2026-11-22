"""Fixed synthetic-PG source-envelope adapter. No production PG admission.

Only the verified bootstrap injects ADMISSION_TYPE and REFUSED. Root verifies
and supervises; only the capability-free actor imports the approved PG stack.
No CLI, test hooks, handler selection, retries, or credential discovery.
"""
import asyncio
from dataclasses import asdict
import hashlib
import json
import logging
import os
from pathlib import Path
import re
import socket
import sys
import threading
from types import ModuleType

FLAGS = ("identity_enrollment_admitted", "journal_dispatch_admitted",
         "purge_or_restore_admitted")
CLEANUP = ("cleanup_confirmed", "cgroup_empty", "pipe_eof_confirmed", "child_reaped")
PYTHON = ("/usr/bin/python3.12", "-I", "-S", "-B")
FAILURE_PHASE = "not_started"


def _require(value):
    if not value:
        raise globals().get("REFUSED", RuntimeError)("pg_source_runtime_refused") from None


def _admission(value):
    expected = globals().get("ADMISSION_TYPE")
    _require(isinstance(expected, type) and type(value) is expected)


def _worker_argv(admission):
    return [*PYTHON, admission.spec["sources"]["pg_bootstrap"]["path"],
            "worker", str(admission.file), admission.sha]


def _actual_argv(pid):
    fd = os.open("/proc/"+str(pid)+"/cmdline", os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC)
    try:
        raw = os.read(fd, 16385)
        _require(0 < len(raw) <= 16384 and raw.endswith(b"\0"))
        return raw[:-1].split(b"\0")
    finally:
        os.close(fd)


def _legacy_success(value, legacy, unit):
    keys = {"format", "approval", "operation", "unit", "status", "observation_sha256",
            *CLEANUP, *FLAGS}
    return (type(value) is dict and set(value) == keys
            and value["format"] == "pult-offline-operator-supervision-v1"
            and value["approval"] == legacy.sha
            and value["operation"] == legacy.spec["operation"]
            and type(unit) is str and re.fullmatch(r"pult-call-gate-[0-9a-f]{32}\.service", unit)
            and value["unit"] == unit
            and value["status"] == "OFFLINE_REVIEW_STOP_CONFIRMED_NO_ADMISSION"
            and all(value[key] is True for key in CLEANUP)
            and all(value[key] is False for key in FLAGS)
            and type(value["observation_sha256"]) is str
            and re.fullmatch("[0-9a-f]{64}", value["observation_sha256"]) is not None)


def run(admission):
    """Consume one source closure; return no receipt if its FD close is uncertain."""
    _admission(admission)
    try:
        return _run(admission)
    finally:
        admission.close()


def _run(admission):
    """Original live root controller API; never forward a legacy PG approval."""
    _admission(admission)
    pid, thread = os.getpid(), threading.current_thread()
    legacy = admission.legacy
    approval, legacy_approval = admission.sha, legacy.sha
    operation = legacy.spec["operation"]

    def guard():
        _require(os.getuid() == os.geteuid() == 0 and os.getpid() == pid
                 and threading.current_thread() is thread
                 and admission.legacy is legacy and admission.sha == approval
                 and legacy.sha == legacy_approval and legacy.spec["operation"] == operation)
        admission.root_guard()

    guard()
    host = legacy.capture("host")
    expected = _worker_argv(admission)

    class FixedBound(host.Bound):
        def _guard(self):
            guard()
            super()._guard()

        def command(self, argv):
            guard()
            suffix = [*PYTHON, legacy.spec["sources"]["bootstrap"]["path"],
                      str(legacy.file), legacy.sha]
            _require(type(argv) is list and argv[-7:] == suffix
                     and _worker_argv(admission) == expected)
            # Every PID1/setpriv argument is retained verbatim.
            return argv[:-7]+expected

        def ready(self, unit, actor_pid):
            guard()
            before = legacy.kernel_actor(actor_pid)
            actual = _actual_argv(actor_pid)
            _require(actual == [os.fsencode(item) for item in expected])
            invocation = legacy.worker_policy(unit, actor_pid)
            _require(self.invocation == invocation and legacy.kernel_actor(actor_pid) == before)
            guard()

    bound, receipt = None, None
    failed = False
    try:
        bound = FixedBound(legacy)
        receipt = bound.run()
    except BaseException:
        failed = True
    # Bound.run/_execute own all process, pipe, claim and lock cleanup. No outer
    # result is selected until those finally blocks and their close checks end.
    closed = bool(bound is not None and bound.closed is True
                  and all(getattr(bound, key) is None for key in ("pidfd", "fence", "lock")))
    clean = bool(bound is not None and bound.clean is True)
    eof = bool(bound is not None and bound.eof is True)
    reaped = bool(bound is not None and bound.reaped is True)
    empty = False
    try:
        guard()
        empty = bool(bound is not None and bound.unit is not None
                     and bound.supervisor._empty(bound.unit))
    except BaseException:
        failed = True
    unit = bound.unit if bound is not None else None
    success = (not failed and closed and clean and eof and reaped and empty
               and _legacy_success(receipt, legacy, unit))
    return dict(format="pult-operator-pg-source-supervision-v1", approval=approval,
        legacy_approval=legacy_approval, operation=operation, unit=unit,
        status="SYNTHETIC_PG_REVIEW_STOP_CONFIRMED_NO_ADMISSION" if success
               else "UNKNOWN_REQUIRES_REVIEW",
        observation_sha256=receipt["observation_sha256"] if success else None,
        cleanup_confirmed=clean, cgroup_empty=empty, pipe_eof_confirmed=eof,
        child_reaped=reaped, production_pg_source_admission_proved=False,
        **dict.fromkeys(FLAGS, False))


def _backend(admission):
    blocked = ("services", "sqlalchemy", "asyncpg", "greenlet", "typing_extensions")
    _require(not any(name == root or name.startswith(root+".")
                     for name in sys.modules for root in blocked))
    # -I -S leaves only the fixed system standard library. Do not retain a
    # caller-added repository/site directory while admitting verified deps.
    library = Path(sys.base_prefix)/"lib"
    standard = library/"python3.12"
    allowed = {str(library/"python312.zip"), str(standard), str(standard/"lib-dynload")}
    _require(all(type(item) is str and item in allowed for item in sys.path))
    sys.path.insert(0, str(admission.deps))
    services = ModuleType("services")
    services.__path__ = []
    sys.modules["services"] = services
    for role, leaf in (("policy", "account_closure_policy"), ("reviewer", "erasure_operator_review")):
        name = "services."+leaf
        module = ModuleType(name)
        module.__file__ = admission.spec["sources"][role]["path"]
        module.__package__ = "services"
        sys.modules[name] = module
        setattr(services, leaf, module)
        exec(compile(admission.blobs[role], module.__file__, "exec", dont_inherit=True,
                     optimize=0), module.__dict__)
    return sys.modules["services.erasure_operator_review"]


async def _serve(admission):
    global FAILURE_PHASE
    FAILURE_PHASE = "actor_guard"
    legacy = admission.legacy
    unit = admission.actor_guard()
    core = legacy.operator_module()
    seams = legacy.capture("worker")
    FAILURE_PHASE = "backend_import"
    backend = _backend(admission)
    from sqlalchemy.engine import URL
    from sqlalchemy.ext.asyncio import create_async_engine
    from sqlalchemy.pool import NullPool

    FAILURE_PHASE = "engine"
    binding = dict(admission.binding)
    _require(set(binding) == {"socket", "database", "database_oid",
                             "reader_role", "reader_password", "application_name"})
    url = URL.create("postgresql+asyncpg", username=binding["reader_role"],
        password=binding["reader_password"], database=binding["database"],
        query={"host": binding["socket"]})
    engine = create_async_engine(url, poolclass=NullPool, echo=False, hide_parameters=True,
        connect_args={"timeout": 3, "server_settings": {"application_name": binding["application_name"]}})
    review, observation = None, None
    calls = 0
    previous = os.umask(0o077)

    def guard():
        seams._parent_live()
        _require(admission.legacy is legacy and admission.actor_guard() == unit)

    try:
        review = backend.CommittedDraftReviewer(engine, database_name=binding["database"],
            database_oid=binding["database_oid"], reader_role=binding["reader_role"])
        # Keep the accepted dedicated-pool latch installed; suppress only its
        # private sink, never global logging or third-party implementation.
        review._cleanup.logger = logging.Logger("synthetic-pg-source-pool", logging.CRITICAL+1)

        async def callback(account, draft, *, nonce):
            nonlocal calls, observation
            guard()
            calls += 1
            fixed = legacy.spec["synthetic"]
            _require(calls == 1 and (account, draft) == (fixed["account_id"], fixed["draft_id"]))
            value = await review(account, draft, nonce=nonce)
            _require(type(value) is core.DraftObservation and review._cleanup.failed is False
                     and all(getattr(value, key) is False for key in FLAGS))
            guard()
            observation = value
            return value

        FAILURE_PHASE = "listener"
        server = core.OperatorReviewServer(legacy.ipc/"review.sock",
            operator_uid=legacy.spec["operator_uid"], reviewer=callback)
        core.ipc._socket_directory(server.path, server.uid)
        _require(not os.path.lexists(server.path))
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as listener:
            listener.bind(str(server.path))
            os.chmod(server.path, 0o660)
            core.ipc._socket_file(server.path, server.uid, os.getegid())
            listener.listen(1)
            listener.settimeout(core.SESSION_SECONDS)
            # GO has not been consumed yet, so do not require an empty pipe.
            _require(admission.actor_guard() == unit)
            seams._emit(legacy, unit, "READY_OFFLINE_ONLY", None)
            FAILURE_PHASE = "go"
            seams._wait_go()
            guard()
            connection, _ = listener.accept()
            with connection:
                guard()
                FAILURE_PHASE = "session"
                await server._session(connection)
    finally:
        try:
            # Preserve the earlier failed stage for secret-free diagnostics.
            await engine.dispose()
        finally:
            os.umask(previous)
    # A successful core observation is still not process supervision. Both
    # sockets and the owned engine have closed before this private CLOSED line.
    FAILURE_PHASE = "closed"
    _require(calls == 1 and observation is not None and review is not None
             and review._cleanup.failed is False)
    guard()
    raw = (json.dumps(asdict(observation), sort_keys=True, separators=(",", ":"),
                      ensure_ascii=True, allow_nan=False)+"\n").encode("ascii")
    admission.close()
    seams._emit(legacy, unit, "CLOSED_OFFLINE_ONLY", hashlib.sha256(raw).hexdigest())


def worker(admission):
    """Fixed capability-free actor API; no root or configurable reviewer path."""
    _admission(admission)
    try:
        admission.actor_guard()
        asyncio.run(_serve(admission))
    finally:
        admission.close()
