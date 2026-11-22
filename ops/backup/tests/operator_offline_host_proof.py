"""Local native synthetic operator lifecycle and refusal proof.

Fixed entry: sudo -n -- unshare --net /usr/bin/python3.12 -I -S -B <this file>
No accounts, installed units, network clients, database, credentials or cloud.
The trusted controller/client roles execute only a protected temporary copy.
"""
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import selectors
import signal
import stat
import subprocess
import sys
import tempfile
import time
from uuid import uuid4
from types import SimpleNamespace

# The protected role copy lives at /tmp/pult-offline-*/fixture.py. Only the
# repository entry assembles releases; role processes never discover a repo.
REPO = None if Path(__file__).name == "fixture.py" else Path(__file__).resolve().parents[3]
CASES = ("authorized", "freeze", "wrong_peer", "source_changed", "profile_replaced",
         "stale_controller", "term_descendant", "controller_death", "queued_late",
         "cleanup_unknown", "bad_approval")
ACTOR, GROUP, OPERATOR, STRANGER = 46201, 46202, 46202, 46203
FLAGS = dict(identity_enrollment_admitted=False, journal_dispatch_admitted=False,
             purge_or_restore_admitted=False)
PATHS = {
    "journal": "ops/backup/erasure_journal.py",
    "anchor": "ops/backup/erasure_anchor.py",
    "ipc": "ops/backup/erasure_custodian.py",
    "operator": "ops/backup/erasure_operator.py",
    "stop": "ops/backup/custodian_v2/stop/constructor_stop.py",
    "supervisor": "ops/backup/custodian_v2/controller/supervisor_v2.py",
    "core": "ops/backup/custodian_v2/controller/frozen/admission.py",
    **{n: "ops/backup/operator_host/"+n+".py" for n in ("profile", "host", "bootstrap", "worker")},
}
PINS = dict(journal="94eeba19e645e25ec8286baebe4092a29c541c6f12858e1ff7b303f6a1a4fc98",
    anchor="b399803ac7a03aaa5a506cbb0036f98c153457769beabf46af44864b190861a8",
    ipc="3eb793a1ef1002c6def97b52c579b2ff051e834b44b802a75aca09d640a691d3",
    operator="c4be8ed53a803afe37cf6c72ac9fa462167ace9401575ee1f85faa2774a1365f",
    stop="5406c5a81498013884ac46d0279219f299db40576357692604fb4f08c251ca8c",
    supervisor="3d05d22163b11f815b29c222128c4e41987c9e4a83e7b4906cbe63a18941a37e",
    core="961cc3aef5dcfe9d6286b15a2d5c087a33dc1e5dafdc7d5a16f6eded961afea7")
SYNTHETIC = dict(account_id="c7461350-9188-4bf1-bc6c-c4a5b9455bec",
    draft_id="ff636b10-6ccb-4b1b-9394-df59af74b793",
    closure_epoch="2026-10-21T12:00:00.000000Z", reviewed_at="2026-11-21T12:00:00.000000Z",
    policy="normal-close-30d-v1")
ENV = {"PATH": "/usr/bin:/bin", "LANG": "C.UTF-8"}


def require(ok, reason):
    if not ok:
        raise RuntimeError(reason)


def canonical(value):
    return (json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)+"\n").encode("ascii")


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def ident(path):
    s = path.lstat()
    return dict(device=s.st_dev, inode=s.st_ino)


def put(path, raw, mode=0o444):
    with path.open("xb") as f:
        os.fchmod(f.fileno(), mode)
        f.write(raw)
        f.flush()
        os.fsync(f.fileno())


def load(path, name):
    module = importlib.util.module_from_spec(importlib.util.spec_from_file_location(name, path))
    exec(compile(path.read_bytes(), str(path), "exec", dont_inherit=True, optimize=0), module.__dict__)
    return module


def unit_name(unit):
    require(type(unit) is str and re.fullmatch(r"pult-call-gate-[0-9a-f]{32}\.service", unit), "owned_unit_name_required")
    return unit


def command(argv, **kwargs):
    return subprocess.run(argv, env=ENV, stdin=kwargs.pop("stdin", subprocess.DEVNULL),
                          capture_output=True, timeout=kwargs.pop("timeout", 5), **kwargs)


def state(unit):
    r = command(["/usr/bin/systemctl", "show", unit_name(unit), "--no-pager",
        "--property=LoadState,ActiveState,SubState,MainPID,InvocationID,Description,Result,ExecMainCode,ExecMainStatus,ConditionResult"])
    require(r.returncode == 0, "unit_show_failed")
    return dict(row.split("=", 1) for row in r.stdout.decode().splitlines() if "=" in row)


def empty(unit):
    group = Path("/sys/fs/cgroup/system.slice") / unit_name(unit)
    try:
        return "populated 0" in (group/"cgroup.events").read_text().splitlines()
    except FileNotFoundError:
        require(not group.exists(), "cgroup_state_uncertain")
        return True


def process_identity(pid):
    fields = Path("/proc/"+str(pid)+"/stat").read_text().rsplit(") ", 1)[1].split()
    return dict(pid=pid, start_ticks=int(fields[19]))


def root_json(path):
    s = path.lstat()
    require(stat.S_ISREG(s.st_mode) and s.st_uid == 0 and s.st_nlink == 1
            and not s.st_mode & 0o022 and s.st_size < 16384, "owned_root_record_required")
    return json.loads(path.read_bytes())


def refused_peer(output, code):
    require(code != 0, 'hostile_or_frozen_peer_succeeded')
    value = json.loads(output)
    require(value.get('outcome') == 'FAIL' and 'observation' not in value,
            'failure_misreported_as_early_observation')


def client_failure(mode, code, output, error):
    # Synthetic harness only. Never echo raw subprocess text or exception paths.
    detail = dict(case=mode, returncode=code, stdout_bytes=len(output),
                  stderr_bytes=len(error), stdout_sha256=sha(output),
                  reason='unstructured_client_failure')
    try:
        value = json.loads(output)
    except (ValueError, UnicodeError):
        value = None
    if (type(value) is dict and set(value) == {'outcome', 'error_type', 'reason'}
            and value['outcome'] == 'FAIL'):
        known = {'offline_operator_bootstrap_refused', 'bootstrap_refused',
                 'trusted_bootstrap_changed', 'early_observation_admitted'}
        if type(value['reason']) is str and value['reason'] in known:
            detail['reason'] = value['reason']
        else:
            detail['reason'] = 'redacted_client_exception'
    return 'authorized_client_failed:'+json.dumps(detail, sort_keys=True)


def shared_tmp_capture(root, bootstrap_sha):
    """Actual descriptor check under /tmp sibling churn, before any worker."""
    bootstrap = root/'release'/PATHS['bootstrap']
    require(sha(bootstrap.read_bytes()) == bootstrap_sha, 'trusted_bootstrap_changed')
    module = load(bootstrap, 'trusted_shared_tmp_capture')
    fixed = root/'fixture.py'
    with module._Capture() as captured:
        body = captured.take(str(fixed), 100000)
        captured.check()
        with tempfile.TemporaryDirectory(prefix='pult-offline-sibling-', dir='/tmp') as sibling:
            shared = [before for _, _, leaf, before in captured.entries
                      if stat.S_ISDIR(before.st_mode) and before.st_uid == 0
                      and before.st_mode & stat.S_ISVTX and leaf == 'tmp']
            require(len(shared) == 1 and Path(sibling).is_dir()
                    and Path(sibling).parent == root.parent and Path(sibling) != root,
                    'shared_tmp_sibling_churn_not_observed')
            captured.check()
            require(fixed.read_bytes() == body, 'shared_tmp_source_changed')
    return True


def controller(root, mode, bootstrap_sha):
    print("CONTROLLER_READY "+str(os.getpid()), flush=True)
    approval = sys.stdin.buffer.readline(66).decode("ascii").strip()
    file = root/"profile.json"
    bootstrap = root/"release"/PATHS["bootstrap"]
    require(sha(bootstrap.read_bytes()) == bootstrap_sha, "trusted_bootstrap_changed")
    try:
        profile = load(bootstrap, "trusted_test_bootstrap").load_profile(file, approval)
        if mode == "source_changed":
            target = Path(profile.spec['sources']['worker']['path'])
            target.chmod(0o644)
            with target.open('ab') as altered:
                altered.write(b'\n# synthetic protected-source substitution\n')
            target.chmod(0o444)
        elif mode == "profile_replaced":
            replacement = root/'replacement.json'
            put(replacement, file.read_bytes())
            os.replace(replacement, file)  # Same bytes, different pinned inode.
        host = profile.capture("host")
        # Verify the controller invocation before entering the caught run API.
        profile.root_guard()
    except Exception:
        require(mode in ("source_changed", "profile_replaced", "stale_controller", "bad_approval"),
                "unexpected_prelaunch_refusal")
        print(json.dumps(dict(rejected_before_launch=True, mode=mode)), flush=True)
        return
    require(mode not in ("source_changed", "profile_replaced", "stale_controller", "bad_approval"),
            "unsafe_prelaunch_acceptance")

    class Recorded(host.Bound):
        descendant = None
        def command(self, argv):
            put(root/"actor.json", canonical(dict(unit=self.unit, approval=profile.sha)), 0o600)
            return argv

        def ready(self, unit, pid):
            ticks = profile.kernel_actor(pid)
            require(Path("/proc/"+str(pid)+"/cgroup").read_text().strip()
                    == "0::/system.slice/"+unit, "actual_actor_cgroup_required")
            put(root/"ready.json", canonical(dict(unit=unit, pid=pid, start_ticks=ticks,
                invocation=state(unit)["InvocationID"])), 0o600)
            if mode in ("freeze", "queued_late"):
                fd = os.pidfd_open(pid)
                try:
                    require(process_identity(pid)["start_ticks"] == ticks, "actor_pid_reused")
                    signal.pidfd_send_signal(fd, signal.SIGSTOP)
                finally:
                    os.close(fd)
            if mode == "term_descendant":
                self.descendant = subprocess.Popen([
                    '/usr/bin/setpriv', '--reuid='+str(ACTOR), '--regid='+str(GROUP),
                    '--clear-groups', '--bounding-set=-all', '--inh-caps=-all',
                    '--ambient-caps=-all', '--no-new-privs', '/usr/bin/python3.12',
                    '-I', '-S', '-B', str(root/'fixture.py'), '--term-descendant'],
                    stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                    close_fds=True, env=ENV)
                require(line(self.descendant, 3) == 'TERM_IGNORED', 'descendant_not_ready')
                child = process_identity(self.descendant.pid)
                require(state(unit)['InvocationID'] == root_json(root/'ready.json')['invocation'],
                        'actor_invocation_changed_before_fault')
                (Path('/sys/fs/cgroup/system.slice')/unit/'cgroup.procs').write_text(str(child['pid']))
                require(Path('/proc/'+str(child['pid'])+'/cgroup').read_text().strip()
                    == '0::/system.slice/'+unit, 'descendant_not_in_owned_actor_cgroup')
                put(root/'descendant.json', canonical(child), 0o600)
                self.descendant.stdout.close()
            if mode == "controller_death":
                os.kill(os.getpid(), signal.SIGKILL)

        def _execute(self):
            original = host.os
            if mode == 'queued_late':
                def queued(fd, body):
                    count = original.write(fd, body)
                    if body == profile.capture('host').GO:
                        ready = root_json(root/'ready.json')
                        require(count == len(body) and
                            Path('/proc/'+str(ready['pid'])+'/stat').read_text().rsplit(') ',1)[1].split()[0] == 'T',
                            'GO_not_queued_for_frozen_actor')
                        put(root/'go-queued.json', canonical(dict(bytes=count)), 0o600)
                        os.kill(os.getpid(), signal.SIGKILL)
                    return count
                host.os = SimpleNamespace(**dict(vars(original), write=queued))
            try:
                return super()._execute()
            finally:
                host.os = original

        def cleanup(self):
            put(root/"precleanup.json", canonical(state(self.unit)), 0o600)
            result = super().cleanup()
            if mode == 'cleanup_unknown':
                raise RuntimeError('injected_owned_cleanup_uncertainty')
            return result

    bound = Recorded(profile)
    receipt = bound.run()
    descendant_killed = None
    if bound.descendant is not None:
        descendant_killed = bound.descendant.wait(timeout=3) == -signal.SIGKILL
        require(descendant_killed, 'TERM_ignoring_descendant_not_killed_by_PID1')
    require(bound.closed and all(getattr(bound, n) is None for n in ("pidfd", "fence", "lock")), "descriptor_cleanup_required")
    claim = (profile.control/"constructor-stop.claim").read_bytes()
    repeat = False
    try:
        bound.run()
    except host.Refused:
        repeat = True
    second = host.Bound(profile).run()
    require(repeat and second["status"] == "UNKNOWN_REQUIRES_REVIEW"
            and not second["unit"] and (profile.control/"constructor-stop.claim").read_bytes() == claim,
            "permanent_claim_reuse_not_refused")
    print(json.dumps(dict(parent_receipt=receipt, repeat_refused=True,
                         descendant_killed=descendant_killed), sort_keys=True), flush=True)


def client(root, approval, bootstrap_sha):
    from dataclasses import asdict
    bootstrap = root/"release"/PATHS["bootstrap"]
    require(sha(bootstrap.read_bytes()) == bootstrap_sha, "trusted_bootstrap_changed")
    profile = load(bootstrap, "trusted_client_bootstrap").load_profile(root/"profile.json", approval)
    core = profile.operator_module()
    observed = core.OperatorReviewClient(profile.ipc/"review.sock", reviewer_uid=ACTOR).review(
        SYNTHETIC["account_id"], SYNTHETIC["draft_id"])
    value = asdict(observed)
    require(all(value[n] is False for n in FLAGS), "early_observation_admitted")
    print(json.dumps(dict(kind="EARLY_NONADMITTING_OBSERVATION_NOT_PARENT_RECEIPT", observation=value)), flush=True)


def line(process, timeout=8):
    selector = selectors.DefaultSelector()
    raw = bytearray()
    try:
        selector.register(process.stdout, selectors.EVENT_READ)
        until = time.monotonic()+timeout
        while len(raw) < 4096:
            require(selector.select(max(0, until-time.monotonic())), "controller_ready_timeout")
            chunk = os.read(process.stdout.fileno(), 1)
            require(chunk, "controller_ready_eof")
            if chunk == b"\n":
                return raw.decode("ascii")
            raw.extend(chunk)
        raise RuntimeError("controller_ready_bound")
    finally:
        selector.close()


def stop_owned(unit, description):
    def stopped(view):
        return view.get('LoadState') == 'not-found' or view.get('ActiveState') in ('inactive', 'failed')
    view = state(unit)
    if view.get("LoadState") != "not-found":
        require(view.get("Description") == description, "foreign_unit_cleanup_refused")
        command(["/usr/bin/systemctl", "stop", unit_name(unit)], timeout=7)
        if not stopped(state(unit)) or not empty(unit):
            command(["/usr/bin/systemctl", "kill", "--kill-whom=all", "--signal=SIGKILL", unit], timeout=3)
            command(["/usr/bin/systemctl", "stop", unit], timeout=7)
        require(stopped(state(unit)) and empty(unit), "owned_unit_not_stopped_and_empty")
        command(["/usr/bin/systemctl", "reset-failed", unit])
    require(stopped(state(unit)) and empty(unit), "owned_unit_cleanup_uncertain")


def native_case(mode):
    require(mode in CASES, "unknown_native_case")
    temporary = tempfile.TemporaryDirectory(prefix="pult-offline-", dir="/tmp")
    root = Path(temporary.name).resolve()
    root.chmod(0o755)
    root_id = ident(root)
    controller_unit = "pult-call-gate-"+uuid4().hex+".service"
    description = "PULT offline proof "+uuid4().hex
    process = peer = None
    approval = None
    try:
        release, control, ipc = (root/n for n in ("release", "control", "ipc"))
        for directory in (release, control, ipc):
            directory.mkdir(mode=0o755)
            directory.chmod(0o755)
        ipc.chmod(0o750)
        os.chown(ipc, ACTOR, GROUP)
        put(control/"operator-host.lock", b"", 0o600)
        sources = {}
        for name, relative in PATHS.items():
            target = release/relative
            target.parent.mkdir(parents=True, exist_ok=True, mode=0o755)
            # Checkout normalization is fixture assembly ONLY. Runtime bytes are
            # exact protected LF bytes; the loader performs no normalization.
            raw = (REPO/relative).read_bytes().replace(b"\r\n", b"\n")
            if name in PINS:
                require(sha(raw) == PINS[name], "accepted_source_pin_changed:"+name)
            put(target, raw)
            sources[name] = dict(path=str(target), sha256=sha(raw))
        for directory in release.rglob("*"):
            if directory.is_dir():
                directory.chmod(0o755)
        script = root/"fixture.py"
        put(script, Path(__file__).read_bytes().replace(b"\r\n", b"\n"))
        bootstrap_sha = sources["bootstrap"]["sha256"]
        shared_tmp_churn = shared_tmp_capture(root, bootstrap_sha) if mode == 'authorized' else None
        props = dict(Type="exec", ExitType="main", RemainAfterExit="no", User="0", Group="0",
            KillMode="control-group", KillSignal="SIGTERM", SendSIGKILL="yes", FinalKillSignal="SIGKILL",
            TimeoutStopSec="2", Restart="no", RuntimeMaxSec="45", PrivateNetwork="yes",
            NoNewPrivileges="yes", ConditionPathExists="!"+str(control/"constructor-stop.claim"),
            Description=description)
        argv = ["/usr/bin/systemd-run", "--quiet", "--pipe", "--no-ask-password",
            "--expand-environment=no", "--unit="+controller_unit]
        argv += ["--property="+k+"="+v for k, v in sorted(props.items())]
        argv += ["/usr/bin/python3.12", "-I", "-S", "-B", str(script), "--controller", str(root), mode, bootstrap_sha]
        require(state(controller_unit).get("LoadState") == "not-found", "controller_unit_collision")
        process = subprocess.Popen(argv, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=subprocess.PIPE, close_fds=True, env=ENV)
        first = line(process)
        view = state(controller_unit)
        pid = int(view["MainPID"])
        require(first == "CONTROLLER_READY "+str(pid) and view["ActiveState"] == "active", "controller_identity_mismatch")
        spec = dict(format="pult-offline-operator-host-v1", mode="SYNTHETIC_REVIEW_ENDPOINT_ONLY",
            operation=str(uuid4()), release=dict(root=str(release), identity=ident(release)),
            controller=dict(unit=controller_unit, invocation_id=view["InvocationID"],
                **process_identity(pid), boot=Path("/proc/sys/kernel/random/boot_id").read_text().strip()),
            control=dict(root=str(control), identity=ident(control), lock_identity=ident(control/"operator-host.lock")),
            actor=dict(uid=ACTOR, gid=GROUP), operator_uid=OPERATOR,
            ipc=dict(path=str(ipc), identity=ident(ipc)), synthetic=SYNTHETIC, sources=sources)
        if mode == 'stale_controller':
            spec['controller']['invocation_id'] = '0'*32
        raw = canonical(spec)
        approval = sha(raw)
        put(root/"profile.json", raw)
        began = time.monotonic()
        submitted_approval = '0'*64 if mode == 'bad_approval' else approval
        process.stdin.write((submitted_approval+"\n").encode("ascii"))
        process.stdin.flush()
        if mode in ('source_changed', 'profile_replaced', 'stale_controller', 'bad_approval'):
            output, error = process.communicate(timeout=10)
            require(process.returncode == 0 and not error
                and json.loads(output) == dict(rejected_before_launch=True, mode=mode)
                and not (root/'actor.json').exists() and not (control/'constructor-stop.claim').exists()
                and not list(ipc.iterdir()), 'prelaunch_substitution_not_refused')
            return dict(case=mode, outcome='PASS', rejected_before_launch=True)
        until = time.monotonic()+8
        while not (root/"ready.json").exists():
            if process.poll() is not None or time.monotonic() >= until:
                diagnostics = {}
                for name in ("actor.json", "precleanup.json"):
                    if (root/name).exists():
                        diagnostics[name] = root_json(root/name)
                if process.poll() is not None:
                    output, error = process.communicate(timeout=2)
                    diagnostics.update(output=output.decode("utf8", "replace")[:2000],
                                       error=error.decode("utf8", "replace")[:1000])
                raise RuntimeError("actor_ready_missing:"+json.dumps(diagnostics, sort_keys=True))
            time.sleep(.025)
        ready = root_json(root/"ready.json")
        if mode in ('controller_death', 'queued_late'):
            output, error = process.communicate(timeout=10)
            require(process.returncode != 0 and not output and not (ipc/'synthetic.reviewed').exists(),
                    'dead_controller_emitted_receipt_or_review')
            until = time.monotonic()+8
            while not empty(ready['unit']):
                require(time.monotonic() < until, 'dead_controller_actor_not_stopped')
                time.sleep(.025)
            require((control/'constructor-stop.claim').is_file(), 'dead_controller_claim_lost')
            if mode == 'queued_late':
                require(root_json(root/'go-queued.json') == dict(bytes=len(b'GO_OFFLINE_REVIEW_V1\n')),
                        'late_GO_not_physically_queued')
            require(state(controller_unit).get('LoadState') == 'loaded', 'restart_unit_missing')
            command(['/usr/bin/systemctl', 'start', controller_unit], timeout=5)
            restarted = state(controller_unit)
            require(restarted['MainPID'] == '0' and restarted['ConditionResult'] == 'no'
                and empty(controller_unit) and empty(ready['unit']), 'permanent_fence_restart_not_refused')
            return dict(case=mode, outcome='PASS', no_parent_receipt=True, cgroup_empty=True,
                        restart_refused=True, queued_GO_proved=mode == 'queued_late')
        peer_uid = STRANGER if mode == 'wrong_peer' else OPERATOR
        peer_argv = ["/usr/bin/setpriv", "--reuid="+str(peer_uid), "--regid="+str(GROUP),
            "--clear-groups", "--bounding-set=-all", "--inh-caps=-all", "--ambient-caps=-all", "--no-new-privs",
            "/usr/bin/python3.12", "-I", "-S", "-B", str(script), "--client", str(root), approval, bootstrap_sha]
        peer = subprocess.Popen(peer_argv, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
            stderr=subprocess.PIPE, close_fds=True, env=ENV)
        observation, peer_error = peer.communicate(timeout=13)
        output, error = process.communicate(timeout=25)
        elapsed = time.monotonic()-began
        require(process.returncode == 0 and not error, "controller_failed:"+error.decode("utf8", "replace")[:1000])
        answer = json.loads(output)
        receipt = answer["parent_receipt"]
        terminal = root_json(root/"precleanup.json")
        require(all(receipt[n] is False for n in FLAGS) and answer["repeat_refused"], "receipt_or_repeat_refused")
        require(receipt["unit"] == ready["unit"] and empty(ready["unit"]), "actor_cleanup_unconfirmed")
        claim = root_json(control/"constructor-stop.claim")
        require(claim == dict(format="pult-offline-operator-claim-v1", approval=approval,
            operation=spec["operation"], controller=spec["controller"], unit=ready["unit"])
            and stat.S_IMODE((control/"constructor-stop.claim").stat().st_mode) == 0o444, "retained_claim_changed")
        if mode in ("authorized", 'term_descendant', 'cleanup_unknown'):
            require(peer.returncode == 0 and not peer_error,
                    client_failure(mode, peer.returncode, observation, peer_error))
            early = json.loads(observation)
            require(early["kind"] == "EARLY_NONADMITTING_OBSERVATION_NOT_PARENT_RECEIPT", "early_receipt_conflated")
            require((ipc/"synthetic.reviewed").read_bytes() == b"OFFLINE_SYNTHETIC_REVIEW\n", "callback_marker_missing")
        if mode == 'authorized':
            require(receipt["status"] == "OFFLINE_REVIEW_STOP_CONFIRMED_NO_ADMISSION"
                and all(receipt[k] is True for k in ("cleanup_confirmed", "cgroup_empty", "pipe_eof_confirmed", "child_reaped"))
                and receipt["observation_sha256"] == sha(canonical(early["observation"]))
                and terminal["Result"] == "success" and terminal["ExecMainCode"] == "1"
                and terminal["ExecMainStatus"] == "0" and terminal["SubState"] == "exited", "positive_cleanup_gate_failed")
        elif mode == 'freeze':
            refused_peer(observation, peer.returncode)
            require(receipt["status"] == "UNKNOWN_REQUIRES_REVIEW" and receipt["observation_sha256"] is None
                and not (ipc/"synthetic.reviewed").exists(),
                "frozen_actor_callback_or_pass:"+json.dumps(dict(receipt=receipt,
                    observation_bytes=len(observation), marker=(ipc/"synthetic.reviewed").exists(),
                    terminal=terminal), sort_keys=True))
            require(14 <= elapsed < 30 and terminal["Result"] == "timeout"
                and terminal["ExecMainCode"] == "2" and terminal["ExecMainStatus"] in ('15', '9'),
                "PID1_frozen_worker_stop_not_proved")
        elif mode == 'term_descendant':
            require(receipt['status'] == 'UNKNOWN_REQUIRES_REVIEW' and receipt['observation_sha256'] is None
                and terminal['Result'] == 'timeout' and answer['descendant_killed'] is True
                and 16 <= elapsed < 30, 'TERM_ignoring_descendant_not_hard_stopped')
        elif mode == 'cleanup_unknown':
            require(receipt['status'] == 'UNKNOWN_REQUIRES_REVIEW' and receipt['observation_sha256'] is None
                and receipt['cleanup_confirmed'] is False and terminal['Result'] == 'success',
                'cleanup_uncertainty_falsely_confirmed')
        elif mode == 'wrong_peer':
            refused_peer(observation, peer.returncode)
            require(receipt['status'] == 'UNKNOWN_REQUIRES_REVIEW' and receipt['observation_sha256'] is None
                and not (ipc/'synthetic.reviewed').exists() and terminal['Result'] == 'exit-code',
                'wrong_UID_peer_not_refused_before_callback')
        return dict(case=mode, outcome="PASS", seconds=round(elapsed, 3), parent_receipt=receipt,
            precleanup=terminal, actual_actor=ready, descendant_sigkill_confirmed=answer['descendant_killed'],
            shared_tmp_sibling_churn_proved=shared_tmp_churn)
    finally:
        # Stop the only producer before reading its exact durable child ledger.
        try:
            stop_owned(controller_unit, description)
            if (root/"actor.json").exists():
                actor = root_json(root/"actor.json")
                require(actor["approval"] == approval, "actor_ledger_approval_changed")
                stop_owned(actor["unit"], "PULT offline operator "+approval)
            for child in (peer, process):
                if child is not None:
                    if child.poll() is None:
                        child.kill()  # Still-owned unreaped direct Popen child.
                    child.communicate(timeout=3)
            require(root.parent == Path("/tmp") and root.name.startswith("pult-offline-")
                    and ident(root) == root_id, "temp_root_identity_changed")
            temporary.cleanup()
        except BaseException:
            temporary._finalizer.detach()  # Uncertainty retains evidence, never deletes a live scope.
            raise


def preflight():
    require(sys.platform == "linux" and os.getuid() == os.geteuid() == 0
        and sys.version_info[:2] == (3, 12) and sys.implementation.name == "cpython"
        and Path(sys.executable).resolve() == Path("/usr/bin/python3.12")
        and sys.flags.isolated and sys.flags.no_site and sys.flags.dont_write_bytecode
        and not sys.flags.optimize, "fixed_native_runtime_required")
    require(Path("/proc/1/comm").read_text().strip() == "systemd"
        and Path("/sys/fs/cgroup/cgroup.controllers").is_file(), "native_PID1_systemd_cgroup2_required")
    require(os.readlink("/proc/self/ns/net") != os.readlink("/proc/1/ns/net"), "private_network_namespace_required")
    require([r.split(":", 1)[0].strip() for r in Path("/proc/self/net/dev").read_text().splitlines()[2:]] == ["lo"], "loopback_only_required")
    import pwd
    import grp
    for value in (ACTOR, OPERATOR, STRANGER):
        for lookup in (pwd.getpwuid, grp.getgrgid):
            try:
                lookup(value)
            except KeyError:
                continue
            raise RuntimeError("synthetic_NSS_id_already_exists")


def main():
    if sys.flags.optimize:
        print('{"outcome":"NOT_RUN","reason":"optimized_operator_proof_refused"}')
        return 2
    if sys.argv[1:] == ['--term-descendant']:
        require(os.getuid() == ACTOR and os.geteuid() == ACTOR, 'synthetic_descendant_uid_required')
        signal.signal(signal.SIGTERM, signal.SIG_IGN)
        print('TERM_IGNORED', flush=True)
        while True:
            time.sleep(1)
    if len(sys.argv) == 5 and sys.argv[1] in ("--controller", "--client"):
        root = Path(sys.argv[2])
        require(root.is_absolute() and root.parent == Path("/tmp")
            and root.name.startswith("pult-offline-") and not root.is_symlink(), "owned_fixture_required")
        if sys.argv[1] == "--controller":
            controller(root, sys.argv[3], sys.argv[4])
        else:
            client(root, sys.argv[3], sys.argv[4])
        return 0
    require(sys.argv[1:] == [], "unexpected_proof_arguments")
    preflight()
    before = {n: sha((REPO/p).read_bytes()) for n, p in PATHS.items()}
    results = [native_case(mode) for mode in CASES]
    after = {n: sha((REPO/p).read_bytes()) for n, p in PATHS.items()}
    installed = {n: sha((REPO/p).read_bytes().replace(b'\r\n', b'\n')) for n, p in PATHS.items()}
    require(before == after and [r["case"] for r in results] == list(CASES)
        and len(results) == 11 and all(r["outcome"] == "PASS" for r in results), "exact_native_inventory_required")
    require(all(installed[n] == wanted for n, wanted in PINS.items()), 'frozen_dependencies_changed')
    preflight()  # Includes postcheck that no NSS principals were installed.
    print(json.dumps(dict(format="pult-offline-operator-native-sidecar-v1", outcome="PASS",
        scope="OFFLINE_SYNTHETIC_OPERATOR_LIFECYCLE_ONLY", tests=11, skips=0,
        failures=[], results=results, sources_unchanged=True, checkout_byte_hashes=after,
        installed_LF_source_hashes=installed,
        remaining_owned_processes=[], **FLAGS), sort_keys=True))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as error:
        print(json.dumps(dict(outcome="FAIL", error_type=type(error).__name__, reason=str(error))))
        raise SystemExit(1)
