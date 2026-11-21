"""One synthetic AF_UNIX review; no storage transport or admission authority."""
import asyncio
from dataclasses import asdict
import os
import select
import socket
import stat
import sys
import time

# This name must already be installed by the fixed, verifying bootstrap. Never
# try a filesystem import or a repository/package fallback for profile code.
_profile = sys.modules.get('_pult_operator_profile_v1')
if _profile is None:
    raise RuntimeError('verified_offline_profile_required')
FLAGS, GO, Refused = _profile.FLAGS, _profile.GO, _profile.Refused
canonical, digest = _profile.canonical, _profile.digest


def _guard(profile, unit=None):
    actual = profile.actor_guard()
    if unit is not None and actual != unit:
        raise Refused('original_active_actor_required')
    return actual


def _emit(profile, unit, status, observation_sha256):
    raw = canonical(dict(format='pult-offline-operator-worker-v1', approval=profile.sha,
        operation=profile.spec['operation'], unit=unit, pid=os.getpid(), status=status,
        observation_sha256=observation_sha256, **FLAGS))
    if len(raw) > 2048 or os.write(1, raw) != len(raw):
        raise Refused('bounded_worker_frame_required')


def _poll_parent():
    if not stat.S_ISFIFO(os.fstat(0).st_mode):
        raise Refused('original_controller_pipe_required')
    poller = select.poll()
    poller.register(0, select.POLLIN | select.POLLHUP | select.POLLERR | select.POLLNVAL)
    return poller


def _parent_live():
    # A queued GO with a dead/closed parent must not arm a late worker. Any data
    # after the exact one-shot token is also a refusal, never another operation.
    if _poll_parent().poll(0):
        raise Refused('live_controller_pipe_required')


def _wait_go():
    poller = _poll_parent()
    deadline = time.monotonic()+3
    received = bytearray()
    while bytes(received) != GO:
        left = deadline-time.monotonic()
        if left <= 0:
            raise Refused('controller_go_required')
        events = poller.poll(max(1, min(3000, int(left*1000))))
        if not events:
            continue
        if any(fd != 0 or mask & (select.POLLHUP | select.POLLERR | select.POLLNVAL)
               or not mask & select.POLLIN for fd, mask in events):
            raise Refused('live_controller_go_required')
        chunk = os.read(0, len(GO)+1-len(received))
        if not chunk:
            raise Refused('controller_go_required')
        received.extend(chunk)
        if not GO.startswith(received):
            raise Refused('exact_controller_go_required')
    _parent_live()


def _synthetic_record(profile):
    """Effect marker for offline fault proofs only; never an authority record."""
    raw = b'OFFLINE_SYNTHETIC_REVIEW\n'
    fd = os.open(profile.ipc/'synthetic.reviewed',
        os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC, 0o600)
    try:
        info = os.fstat(fd)
        if (not stat.S_ISREG(info.st_mode) or info.st_nlink != 1
                or info.st_uid != os.getuid() or info.st_gid != os.getegid()
                or stat.S_IMODE(info.st_mode) != 0o600):
            raise Refused('exclusive_synthetic_record_required')
        if os.write(fd, raw) != len(raw):
            raise Refused('synthetic_record_incomplete')
        os.fsync(fd)
    finally:
        os.close(fd)
    # A partial record survives failure for review; no overwrite, retry or unlink.


async def _serve(profile, core, unit):
    calls = 0
    observation = None

    async def synthetic(account_id, draft_id, *, nonce):
        nonlocal calls, observation
        calls += 1
        _parent_live()
        _guard(profile, unit)
        fixed = profile.spec['synthetic']
        if (calls != 1 or account_id != fixed['account_id'] or draft_id != fixed['draft_id']):
            raise Refused('fixed_synthetic_review_only')
        _synthetic_record(profile)
        observed = core.DraftObservation(nonce=nonce, **fixed)
        _parent_live()
        _guard(profile, unit)
        observation = observed
        return observed

    server = core.OperatorReviewServer(profile.ipc/'review.sock',
        operator_uid=profile.spec['operator_uid'], reviewer=synthetic)
    previous = os.umask(0o077)
    try:
        # Same directory, socket, ownership, mode and peer checks as old core;
        # explicit lifecycle exposes actual readiness without monkeypatching it.
        core.ipc._socket_directory(server.path, server.uid)
        if os.path.lexists(server.path):
            raise Refused('operator_socket_exists_requires_review')
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as listener:
            listener.bind(str(server.path))
            os.chmod(server.path, 0o660)
            core.ipc._socket_file(server.path, server.uid, os.getegid())
            listener.listen(1)
            listener.settimeout(core.SESSION_SECONDS)
            _guard(profile, unit)
            _emit(profile, unit, 'READY_OFFLINE_ONLY', None)
            _wait_go()
            _guard(profile, unit)
            _parent_live()
            connection, _ = listener.accept()
            with connection:
                _parent_live()
                _guard(profile, unit)
                await server._session(connection)
    finally:
        os.umask(previous)
    # Both real sockets have closed. The path and synthetic artifact remain.
    if calls != 1 or observation is None:
        raise Refused('exactly_one_synthetic_observation_required')
    observation_sha256 = digest(canonical(asdict(observation)))
    _parent_live()
    _guard(profile, unit)
    _emit(profile, unit, 'CLOSED_OFFLINE_ONLY', observation_sha256)


def run(profile):
    unit = _guard(profile)
    core = profile.operator_module()
    asyncio.run(_serve(profile, core, unit))
