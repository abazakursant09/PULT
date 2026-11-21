"""One offline operator endpoint under PID1. No storage or application transport."""

import fcntl
import os
import selectors
import signal
import subprocess
import sys
import threading
import time
from uuid import uuid4

from _pult_operator_profile_v1 import (
    FLAGS,
    FENCE,
    GO,
    LOCK,
    RUNTIME,
    STOP,
    Profile,
    Refused,
    canonical,
    document,
    identity,
    root_bytes,
    runtime,
    show,
)


class Unknown(Exception):
    pass


def sync(directory):
    fd = os.open(directory, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def write_all(fd, raw):
    while raw:
        count = os.write(fd, raw)
        if count <= 0:
            raise Unknown("write_incomplete")
        raw = raw[count:]


class Bound:
    """Trusted root controller only. Permanent claim, never adoption or rearm.

    The core's early observation is not this parent's supervision receipt. Only
    the latter is held until cleanup, pipe EOF, reaping and descriptor closure.
    """

    stop = STOP

    def __init__(self, profile):
        if type(profile) is not Profile:
            raise Refused("exact_protected_profile_required")
        self.profile = profile
        self.supervisor = profile.root_guard()
        self.old = profile.capture("stop")
        self.pid, self.thread = os.getpid(), threading.current_thread()
        self.lock = self.fence = self.pidfd = self.process = None
        self.unit = self.claim_raw = self.claim_identity = self.invocation = None
        self.submitted = self.used = self.closed = False
        self.clean = self.eof = self.reaped = False

    def __repr__(self):
        return "<OfflineOperatorHost one shot; no live admission>"

    def command(self, argv):
        return argv  # Trusted proof may add its owned CI scope, never socket input.

    def ready(self, unit, pid):
        pass  # Trusted native fault proof only, not a runtime extension/profile mode.

    def _guard(self):
        runtime(root=True)
        if (
            self.closed
            or self.pid != os.getpid()
            or self.thread is not threading.current_thread()
        ):
            raise Refused("original_live_controller_required")
        self.profile.root_guard()
        if self.fence is not None:
            if (
                identity(os.fstat(self.fence)) != self.claim_identity
                or identity((self.profile.control / FENCE).lstat())
                != self.claim_identity
                or os.pread(self.fence, 4097, 0) != self.claim_raw
                or root_bytes(self.profile.control / FENCE, 4096, (0o444,))
                != self.claim_raw
            ):
                raise Unknown("permanent_claim_changed")
            self.old.manager_view(
                self.profile.control,
                self.profile.spec["control"]["identity"],
                self.fence,
            )
            self.profile.claim(self.unit)

    def _claim(self):
        p = self.profile
        if {item.name for item in p.control.iterdir()} != {LOCK} or os.path.lexists(
            p.ipc / "review.sock"
        ):
            raise Refused("fresh_one_shot_scope_required")
        self.lock = os.open(p.control / LOCK, os.O_RDWR | os.O_NOFOLLOW | os.O_CLOEXEC)
        if identity(os.fstat(self.lock)) != p.spec["control"]["lock_identity"]:
            raise Refused("lock_changed")
        fcntl.flock(self.lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        self._guard()
        self.unit = "pult-call-gate-" + uuid4().hex + ".service"
        self.claim_raw = canonical(
            dict(
                format="pult-offline-operator-claim-v1",
                approval=p.sha,
                operation=p.spec["operation"],
                controller=p.spec["controller"],
                unit=self.unit,
            )
        )
        self.fence = os.open(
            p.control / FENCE,
            os.O_RDWR | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC,
            0o600,
        )
        os.fchmod(self.fence, 0o600)
        self.claim_identity = identity(os.fstat(self.fence))
        write_all(self.fence, self.claim_raw)
        os.fsync(self.fence)
        os.fchmod(self.fence, 0o444)
        os.fsync(self.fence)
        sync(p.control)
        self._guard()

    def _argv(self):
        p = self.profile
        s = p.spec
        controller = s["controller"]["unit"]
        who = s["actor"]
        props = dict(
            Type="exec",
            ExitType="cgroup",
            RemainAfterExit="yes",
            User="0",
            Group="0",
            SupplementaryGroups="",
            BindsTo=controller,
            After=controller,
            PartOf=controller,
            RuntimeMaxSec=str(RUNTIME),
            TimeoutStartSec="2s",
            TimeoutStopSec=str(STOP),
            KillMode="control-group",
            KillSignal="SIGTERM",
            SendSIGKILL="yes",
            FinalKillSignal="SIGKILL",
            Restart="no",
            NoNewPrivileges="yes",
            # Exact previously proved OS launcher: systemd255 needs ambient SETUID;
            # setpriv clears ALL five sets BEFORE any actor Python is executed.
            CapabilityBoundingSet="CAP_SETUID CAP_SETGID CAP_SETPCAP",
            AmbientCapabilities="CAP_SETUID",
            PrivateNetwork="yes",
            PrivateTmp="no",
            PrivateDevices="yes",
            ProtectSystem="strict",
            ProtectHome="yes",
            ProtectControlGroups="yes",
            RestrictAddressFamilies="AF_UNIX",
            InaccessiblePaths="/dev/shm /dev/mqueue /dev/pts",
            ReadWritePaths=str(p.ipc),
            TasksMax="16",
            MemoryMax="128M",
            LimitFSIZE="2097152",
            UMask="0077",
            ConditionPathExists=str(p.control / FENCE),
            Description="PULT offline operator " + p.sha,
            Environment="PATH=/usr/bin:/bin LANG=C.UTF-8",
        )
        args = [
            "/usr/bin/systemd-run",
            "--quiet",
            "--pipe",
            "--no-ask-password",
            "--expand-environment=no",
            "--unit=" + self.unit,
        ]
        args += [
            "--property=" + key + "=" + value for key, value in sorted(props.items())
        ]
        args += [
            "/usr/bin/setpriv",
            "--reuid=" + str(who["uid"]),
            "--regid=" + str(who["gid"]),
            "--clear-groups",
            "--bounding-set=-all",
            "--inh-caps=-all",
            "--ambient-caps=-all",
            "--no-new-privs",
            "/usr/bin/python3.12",
            "-I",
            "-S",
            "-B",
            s["sources"]["bootstrap"]["path"],
            str(p.file),
            p.sha,
        ]
        return args

    def _worker_frame(self, raw, status):
        value = document(raw)
        p = self.profile
        wanted = dict(
            format="pult-offline-operator-worker-v1",
            approval=p.sha,
            operation=p.spec["operation"],
            unit=self.unit,
            pid=value.get("pid"),
            status=status,
            observation_sha256=value.get("observation_sha256"),
            **FLAGS,
        )
        if (
            value != wanted
            or any(value.get(key) is not False for key in FLAGS)
            or type(value["pid"]) is not int
            or value["pid"] <= 0
            or status == "READY_OFFLINE_ONLY"
            and value["observation_sha256"] is not None
            or status == "CLOSED_OFFLINE_ONLY"
            and (
                type(value["observation_sha256"]) is not str
                or len(value["observation_sha256"]) != 64
                or any(c not in "0123456789abcdef" for c in value["observation_sha256"])
            )
        ):
            raise Unknown("worker_frame_refused")
        return value

    def _kernel(self, frame):
        invocation = self.profile.worker_policy(self.unit, frame["pid"])
        if self.invocation is not None and invocation != self.invocation:
            raise Unknown("worker_invocation_changed")
        self.invocation = invocation  # Actual UID/GID/all caps, NOT User=0 metadata.

    def cleanup(self):
        if self.unit is None:
            return
        current = self.supervisor._state(self.unit)
        if current.get("LoadState") != "not-found":
            extra = show(self.unit, "InvocationID,Description")
            if (
                extra.get("Description") != "PULT offline operator " + self.profile.sha
                or self.invocation is not None
                and extra.get("InvocationID") != self.invocation
            ):
                raise Unknown("foreign_unit_cleanup_refused")
        self.supervisor._core().Manager.cleanup(self, self.unit)

    def _execute(self):
        selector = selectors.DefaultSelector()
        buffers = dict(stdout=bytearray(), stderr=bytearray())
        ended = set()
        frames = []
        final = None
        go_sent = False
        began = time.monotonic()

        def pump():
            for key, _ in selector.select(0.025):
                try:
                    chunk = os.read(key.fileobj.fileno(), 4096)
                except BlockingIOError:
                    continue
                if not chunk:
                    ended.add(key.data)
                    selector.unregister(key.fileobj)
                    key.fileobj.close()
                    continue
                buffers[key.data].extend(chunk)
                if len(buffers[key.data]) > (4096 if key.data == "stdout" else 1024):
                    raise Unknown("worker_output_bound")

        try:
            self._guard()
            if self.supervisor._state(self.unit).get("LoadState") != "not-found":
                raise Refused("unit_collision_never_adopt")
            self.submitted = True
            self.process = subprocess.Popen(
                self.command(self._argv()),
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                close_fds=True,
                env={"PATH": "/usr/bin:/bin", "LANG": "C.UTF-8"},
            )
            self.pidfd = os.pidfd_open(self.process.pid)
            for name in ("stdout", "stderr"):
                stream = getattr(self.process, name)
                os.set_blocking(stream.fileno(), False)
                selector.register(stream, selectors.EVENT_READ, name)
            while True:
                self._guard()
                pump()
                while b"\n" in buffers["stdout"]:
                    line, rest = bytes(buffers["stdout"]).split(b"\n", 1)
                    buffers["stdout"][:] = rest
                    if len(frames) >= 2:
                        raise Unknown("extra_worker_frame")
                    frame = self._worker_frame(
                        line + b"\n",
                        "READY_OFFLINE_ONLY" if not frames else "CLOSED_OFFLINE_ONLY",
                    )
                    frames.append(frame)
                    if len(frames) == 1:
                        self._kernel(frame)
                        self.ready(self.unit, frame["pid"])
                        self._guard()
                        if os.write(self.process.stdin.fileno(), GO) != len(GO):
                            raise Unknown("go_delivery_uncertain")
                        # Keep the original parent pipe alive until cleanup. A
                        # queued GO followed by EOF must never arm a late worker.
                        go_sent = True
                final = self.supervisor._state(self.unit)
                if final.get("SubState") == "exited" or final.get("ActiveState") in (
                    "failed",
                    "inactive",
                ):
                    break
                if time.monotonic() - began > RUNTIME + STOP + 5:
                    raise Unknown("worker_deadline")
        finally:
            original = sys.exc_info()[1]
            failures = []
            try:
                if self.submitted:
                    try:
                        self.cleanup()
                        self.clean = True
                    except BaseException as error:
                        failures.append(error)
                if self.process is not None:
                    try:
                        deadline = time.monotonic() + 2
                        while selector.get_map():
                            if time.monotonic() >= deadline:
                                raise Unknown("worker_pipe_eof_unknown")
                            pump()
                        self.eof = ended == {"stdout", "stderr"}
                        if not self.eof:
                            raise Unknown("both_worker_pipe_eofs_required")
                        self.process.wait(
                            timeout=max(0.01, deadline - time.monotonic())
                        )
                        self.reaped = True
                    except BaseException as error:
                        failures.append(error)
            finally:
                try:
                    selector.close()
                    if self.process is not None:
                        if self.process.poll() is None:
                            if self.pidfd is None:
                                self.process.kill()  # Still-owned, unreaped direct child only.
                            else:
                                signal.pidfd_send_signal(self.pidfd, signal.SIGKILL)
                            self.process.wait(timeout=2)
                        for name in ("stdin", "stdout", "stderr"):
                            stream = getattr(self.process, name)
                            if stream is not None and not stream.closed:
                                stream.close()
                except BaseException as error:
                    failures.append(error)
                for error in ([original] if original is not None else []) + failures:
                    if error is not None:
                        raise error
        # A fast successful worker can exit before its final bytes were pumped.
        # Draining may accept CLOSED, but never arms a late READY after cleanup.
        if (
            len(frames) == 1
            and buffers["stdout"].count(b"\n") == 1
            and buffers["stdout"].endswith(b"\n")
        ):
            frames.append(
                self._worker_frame(bytes(buffers["stdout"]), "CLOSED_OFFLINE_ONLY")
            )
            buffers["stdout"].clear()
        # Saved terminal status is from BEFORE reset-failed/cleanup.
        if (
            not go_sent
            or len(frames) != 2
            or buffers["stdout"]
            or buffers["stderr"]
            or frames[0]["pid"] != frames[1]["pid"]
            or not self.clean
            or not self.eof
            or not self.reaped
            or self.process.returncode != 0
            or not self.supervisor._empty(self.unit)
            or final is None
            or final.get("Result") != "success"
            or final.get("ExecMainCode") != "1"
            or final.get("ExecMainStatus") != "0"
            or final.get("SubState") != "exited"
        ):
            raise Unknown("worker_or_cleanup_not_confirmed")
        return frames[1]["observation_sha256"]

    def run(self):
        if self.used or self.closed:
            raise Refused("one_shot_profile_already_used")
        self.used = True
        accepted = None
        failed = False
        try:
            self._guard()
            self._claim()
            accepted = self._execute()
            self._guard()
        except Exception:
            failed = True
        finally:
            errors = []
            for name in ("pidfd", "fence", "lock"):
                fd = getattr(self, name)
                setattr(self, name, None)
                if fd is not None:
                    try:
                        os.close(fd)
                    except Exception:
                        errors.append(True)
            self.closed = True
            if errors:
                failed = True
        # No success is selected until every finally/descriptor cleanup finished.
        try:
            self.profile.root_guard()
        except Exception:
            failed = True
        empty = False
        try:
            empty = bool(self.unit and self.supervisor._empty(self.unit))
        except Exception:
            failed = True
        if (
            not empty
            or not accepted
            or not self.clean
            or not self.eof
            or not self.reaped
        ):
            failed = True
        return dict(
            format="pult-offline-operator-supervision-v1",
            approval=self.profile.sha,
            operation=self.profile.spec["operation"],
            unit=self.unit,
            status="UNKNOWN_REQUIRES_REVIEW"
            if failed
            else "OFFLINE_REVIEW_STOP_CONFIRMED_NO_ADMISSION",
            cleanup_confirmed=self.clean,
            cgroup_empty=empty,
            pipe_eof_confirmed=self.eof,
            child_reaped=self.reaped,
            observation_sha256=None if failed else accepted,
            **FLAGS,
        )
