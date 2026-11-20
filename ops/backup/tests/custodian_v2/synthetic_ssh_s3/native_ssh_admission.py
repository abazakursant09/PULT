"""ROOT TEST ONLY: real OpenSSH AUTH/INGRESS/HELPER COMPONENT, NEVER storage.

Main reviews/runs on Ubuntu with /usr/bin/python3.12 -I -S -B. No download,
installation, maintainer scripts, host accounts/services/settings or routes.
--child/--endpoint are private harness plumbing, not production entry hooks.
The root endpoint deliberately repeats a WIRE FIXTURE; it is not a controller.
All keys are newly generated artificial fixtures and are never printed.
"""
import array
import base64
import hashlib
import json
import os
from pathlib import Path
import select
import signal
import socket
import stat
import struct
import subprocess
import sys
import tempfile
import time

HERE = Path(__file__).resolve().parent
PREFIX = 'pult-ssh-admission-'
HOST = '1.1.1.2'
COMMAND = 'pult-synthetic-one-v1'
TRIGGER = b'RUN_ONE_SYNTHETIC_STORAGE_ACTION_V1\n'
REPLY = (b'{"format":"pult-synthetic-storage-only-reply-v1",'
         b'"purge":false,"restore":false,"status":"complete","storage_only":true}\n')
SOURCES = ('native_ssh_admission.py', 'ssh_ingress.py', 'ssh_helper.py', 'bounded_ssh.py', 'openssh-supply/SUPPLY.json')
PACKAGES = (
    ('openssh-server_1%3a9.6p1-3ubuntu13.20_amd64.deb', 511546, '5d0d5eb672ddc27601e2da4e8465c5736dc73375a21bc1cd1834fcb08fb155bf'),
    ('libwrap0_7.6.q-33_amd64.deb', 47874, '36118b862539868408ee056411d6e470068d14c46e5296b29bfb744770f5d2ce'))
LIMITS = dict(synthetic_reply_fixture_only=True, storage_action_not_executed=True,
              full_chain_proven=False, provider_proven=False)
STAGE='entry'
DIAGNOSTIC={}


class Refused(Exception): pass
def need(value):
    if not value: raise Refused('COMPONENT_FIXTURE_REFUSED')
def sha(raw): return hashlib.sha256(raw).hexdigest()
def source_file(name):
    return (HERE.parents[4]/'ops/backup/custodian_v2/synthetic_ssh_s3'/name
        if name in ('ssh_ingress.py','ssh_helper.py','bounded_ssh.py') else HERE/name)
def wire(value): return (json.dumps(value, sort_keys=True, separators=(',', ':')) + '\n').encode()
def put(file, raw, mode=0o644, uid=0, gid=0):
    file.parent.mkdir(parents=True, exist_ok=True)
    with file.open('xb') as out: out.write(raw)
    os.chown(file, uid, gid); file.chmod(mode)
def ns(): return {name: os.readlink('/proc/self/ns/' + name) for name in ('mnt', 'net', 'pid')}
def run(argv, timeout=8, env=None):
    result = subprocess.run(argv, stdin=subprocess.DEVNULL, capture_output=True, timeout=timeout,
                            env=env or {'PATH': '/usr/bin:/bin', 'LANG': 'C.UTF-8'})
    need(result.returncode == 0 and len(result.stdout) <= 65536 and len(result.stderr) <= 65536)
    return result.stdout
def gate(root, init=False):
    need(sys.platform == 'linux' and sys.version_info[:2] == (3, 12) and os.getuid() == os.geteuid() == 0)
    need(sys.flags.isolated and sys.flags.no_site and sys.dont_write_bytecode and not sys.flags.optimize)
    need(root == root.resolve() and root.parent == Path('/tmp') and root.name.startswith(PREFIX))
    info = root.lstat(); need(stat.S_ISDIR(info.st_mode) and info.st_uid == 0 and stat.S_IMODE(info.st_mode) == 0o700)
    context = json.loads((root / 'context.json').read_bytes())
    need(all(ns()[name] != context['outer_ns'][name] for name in ('mnt', 'net', 'pid')))
    need(os.getpid() == 1 if init else os.getppid() == 1)
    need(all(sha((root / 'code' / name).read_bytes()) == digest for name, digest in context['sources'].items()))
    return context
def stop(process):
    # Keep our direct-child leader reserved with WNOWAIT before group signalling.
    need(process.returncode is None)
    os.waitid(os.P_PID, process.pid, os.WEXITED | os.WNOHANG | os.WNOWAIT)
    need(os.getpgid(process.pid) == process.pid)
    try: os.killpg(process.pid, signal.SIGKILL)
    except ProcessLookupError: pass
    return process.wait(timeout=2)


def endpoint(root):
    gate(root); directory = Path('/run/pult-admission'); events = directory / 'events.jsonl'
    with socket.socket(socket.AF_UNIX, socket.SOCK_SEQPACKET) as listener:
        listener.bind(str(directory / 'control.sock')); os.chown(directory / 'control.sock', 0, 50002)
        os.chmod(directory / 'control.sock', 0o660); listener.listen(2); listener.settimeout(60)
        pin = (directory / 'control.sock').stat()
        config = dict(format='pult-synthetic-ssh-ingress-v1', caller_uid=50001, caller_gid=50002,
            controller=dict(pid=os.getpid(), start_ticks=int(Path('/proc/self/stat').read_text().rsplit(') ', 1)[1].split()[19]),
                            boot=Path('/proc/sys/kernel/random/boot_id').read_text().strip()),
            socket=dict(path=str(directory / 'control.sock'), device=pin.st_dev, inode=pin.st_ino))
        put(events, b'', 0o600); put(directory / 'config.json', wire(config), 0o444)
        while True:
            connection, _ = listener.accept()
            with connection:
                connection.settimeout(5)
                pid, uid, gid = struct.unpack('3i', connection.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, 12))
                fields = dict(row.split(':', 1) for row in Path('/proc/' + str(pid) + '/status').read_text().splitlines() if ':' in row)
                observation = {key: fields[key].strip() for key in ('Uid', 'Gid', 'Groups', 'CapInh', 'CapPrm', 'CapEff', 'CapAmb', 'CapBnd', 'NoNewPrivs', 'Threads')}
                with events.open('ab') as log: log.write(wire(dict(pid=pid, uid=uid, gid=gid, actual_proc=observation, **LIMITS)))
                need((uid, gid) == (50001, 50002) and fields['Uid'].split() == ['50001'] * 4 and fields['Gid'].split() == ['50002'] * 4)
                need(fields['Groups'].split() in ([], ['50002']) and fields['NoNewPrivs'].strip() == '1' and fields['Threads'].strip() == '1')
                need(all(int(fields[key], 16) == 0 for key in ('CapInh', 'CapPrm', 'CapEff', 'CapAmb')))
                packet, ancillary, flags, _ = connection.recvmsg(len(TRIGGER) + 1, socket.CMSG_SPACE(256), socket.MSG_CMSG_CLOEXEC)
                for level, kind, data in ancillary:
                    if (level, kind) == (socket.SOL_SOCKET, socket.SCM_RIGHTS):
                        rights = array.array('i'); rights.frombytes(data[:len(data) // rights.itemsize * rights.itemsize])
                        for fd in rights: os.close(fd)
                need(packet == TRIGGER and not ancillary and not flags & (socket.MSG_TRUNC | socket.MSG_CTRUNC))
                need(connection.send(REPLY) == len(REPLY)); connection.shutdown(socket.SHUT_WR)
                need(connection.recv(1) == b'')  # Keep original root fixture PID alive through peer's check.


def child(root):
    global STAGE
    STAGE='namespace_setup'
    context = gate(root, True); fake = root / 'fake'; mounted = []; owned = []; rows = []; sequence = 0
    def start(argv, env=None, input_pipe=False):
        nonlocal sequence
        sequence += 1; output = root / ('out-' + str(sequence)); error = root / ('err-' + str(sequence))
        with output.open('xb') as out, error.open('xb') as err:
            process = subprocess.Popen(argv, stdin=subprocess.PIPE if input_pipe else subprocess.DEVNULL,
                stdout=out, stderr=err, close_fds=True, start_new_session=True,
                env=env or {'PATH': '/usr/bin:/bin', 'LANG': 'C.UTF-8'}, cwd='/', umask=0o077)
        owned.append(process); return process, output, error
    def exchange(argv, body, hold=False):
        process, output, error = start(argv, input_pipe=True); started = time.monotonic()
        try:
            process.stdin.write(body); process.stdin.flush()
            if not hold: process.stdin.close()
            while os.waitid(os.P_PID, process.pid, os.WEXITED | os.WNOHANG | os.WNOWAIT) is None:
                need(time.monotonic() - started < 8); time.sleep(.02)
            code = stop(process); owned.remove(process)
            out, err = output.read_bytes(), error.read_bytes(); need(len(out) <= 4096 and len(err) <= 8192)
            return code, out, err, time.monotonic() - started
        finally:
            if process.stdin is not None and not process.stdin.closed: process.stdin.close()
    def records(): return [json.loads(line) for line in Path('/run/pult-admission/events.jsonl').read_bytes().splitlines()]
    drop = ['/usr/bin/setpriv', '--reuid=50003', '--regid=50004', '--clear-groups', '--bounding-set=-all',
            '--inh-caps=-all', '--ambient-caps=-all', '--no-new-privs', '--']
    try:
        run(['/bin/mount', '--make-rprivate', '/'])
        for name, target in (('etc', '/etc'), ('run', '/run'), ('opt', '/opt'), ('varlib', '/var/lib')):
            backing = fake / name; backing.mkdir(parents=True, mode=0o755); backing.chmod(0o755)
            run(['/bin/mount', '--bind', str(backing), target]); mounted.append(target)
        for name, mode in (('/run/sshd', 0o755), ('/var/lib/sshd', 0o755), ('/opt/pult-admission', 0o755),
                           ('/etc/pult-synthetic-client', 0o755), ('/run/pult-admission', 0o750)):
            Path(name).mkdir(parents=True, mode=mode); Path(name).chmod(mode)
        # Empty ROOT-controlled homes avoid a chdir warning without admitting
        # caller-controlled shell rc/environment files before ForceCommand.
        for name in ('no-home','no-client-home'):
            (Path('/opt/pult-admission')/name).mkdir(mode=0o755)
        os.chown('/run/pult-admission', 0, 50002)
        STAGE='accounts_sources_keys'
        put(Path('/etc/passwd'), b'root:x:0:0::/root:/bin/dash\npult-synthetic-trigger:x:50001:50002::/opt/pult-admission/no-home:/bin/dash\npult-synthetic-client:x:50003:50004::/opt/pult-admission/no-client-home:/bin/dash\nsshd:x:104:65534::/run/sshd:/usr/sbin/nologin\n')
        put(Path('/etc/group'), b'root:x:0:\ntrigger:x:50002:\nclient:x:50004:\nnogroup:x:65534:\n')
        # NP is a deliberately invalid, non-lock-marker hash; password auth is disabled.
        put(Path('/etc/shadow'), b'root:!:20000:0:99999:7:::\npult-synthetic-trigger:NP:20000:0:99999:7:::\npult-synthetic-client:!:20000:0:99999:7:::\nsshd:!:20000:0:99999:7:::\n', 0o600)
        put(Path('/etc/nsswitch.conf'), b'passwd: files\ngroup: files\nshadow: files\nhosts: files\nservices: files\n')
        put(Path('/etc/hosts'), b'127.0.0.1 localhost\n1.1.1.2 pult-admission\n')
        for name in ('ssh_ingress.py', 'ssh_helper.py', 'bounded_ssh.py'):
            raw = (root / 'code' / name).read_bytes(); need(sha(raw) == context['sources'][name])
            put(Path('/opt/pult-admission') / name, raw)
        keys = Path('/etc/pult-synthetic-client')
        for name in ('host', 'id_ed25519', 'wrong'):
            run(['/usr/bin/ssh-keygen', '-q', '-t', 'ed25519', '-N', '', '-C', '', '-f', str(keys / name)])
            os.chown(keys / name, 0 if name == 'host' else 50003, 0 if name == 'host' else 50004); (keys / name).chmod(0o400)
        def public(name): return b' '.join((keys / (name + '.pub')).read_bytes().split()[:2])
        def fingerprint(name): return 'SHA256:' + base64.b64encode(hashlib.sha256(base64.b64decode(public(name).split()[1], validate=True)).digest()).rstrip(b'=').decode()
        put(keys / 'known_hosts', HOST.encode() + b' ' + public('host') + b'\n')
        put(keys / 'wrong_hosts', HOST.encode() + b' ' + public('wrong') + b'\n')
        put(Path('/etc/authorized_keys'), b'restrict ' + public('id_ed25519') + b'\n')
        run(['/usr/sbin/ip', 'link', 'set', 'lo', 'up']); run(['/usr/sbin/ip', 'addr', 'add', HOST + '/32', 'dev', 'lo'])
        STAGE='endpoint_start'
        start(['/usr/bin/python3.12', '-I', '-S', '-B', str(root / 'code/native_ssh_admission.py'), '--endpoint', str(root)])
        deadline = time.monotonic() + 5
        while not Path('/run/pult-admission/config.json').exists(): need(time.monotonic() < deadline); time.sleep(.02)
        approval = sha(Path('/run/pult-admission/config.json').read_bytes())
        force = '/usr/bin/setpriv --no-new-privs -- /usr/bin/python3.12 -I -S -B /opt/pult-admission/ssh_ingress.py /run/pult-admission/config.json ' + approval
        config = ('Port 22\nListenAddress ' + HOST + '\nPidFile /run/fixture-sshd.pid\nHostKey ' + str(keys / 'host') + '\n'
            'UsePAM no\nUseDNS no\nPasswordAuthentication no\nKbdInteractiveAuthentication no\nPubkeyAuthentication yes\nAuthenticationMethods publickey\n'
            'HostbasedAuthentication no\nGSSAPIAuthentication no\nPermitRootLogin no\nAllowUsers pult-synthetic-trigger\nAuthorizedKeysFile /etc/authorized_keys\n'
            'StrictModes yes\nDisableForwarding yes\nPermitTTY no\nPermitUserEnvironment no\nPermitUserRC no\nPermitTunnel no\nPrintMotd no\nPrintLastLog no\nLogLevel ERROR\nForceCommand ' + force + '\n')
        need('AcceptEnv' not in config); put(Path('/run/fixture-sshd.conf'), config.encode())
        library = ':'.join(str(root / 'unpacked' / prefix / 'x86_64-linux-gnu') for prefix in ('lib', 'usr/lib'))
        daemon_env = dict(PATH='/usr/bin:/bin', LANG='C.UTF-8', LD_LIBRARY_PATH=library)
        daemon = str(root / 'unpacked/usr/sbin/sshd')
        STAGE='daemon_config_check'
        run([daemon, '-t', '-f', '/run/fixture-sshd.conf'], env=daemon_env)
        effective=run([daemon,'-T','-f','/run/fixture-sshd.conf'],env=daemon_env).decode('ascii').splitlines()
        fields=dict(row.split(' ',1) for row in effective if ' ' in row)
        need(all(fields.get(key)==value for key,value in dict(disableforwarding='yes',permittty='no',
            permituserrc='no',permituserenvironment='no',passwordauthentication='no',
            kbdinteractiveauthentication='no',usepam='no',allowusers='pult-synthetic-trigger').items()))
        need(fields.get('forcecommand')==force and not any(row.startswith('acceptenv ') for row in effective))
        STAGE='daemon_start'
        start([daemon, '-D', '-e', '-f', '/run/fixture-sshd.conf'], env=daemon_env)
        deadline = time.monotonic() + 5
        while True:
            try:
                with socket.create_connection((HOST, 22), timeout=.1): break
            except OSError: need(time.monotonic() < deadline); time.sleep(.02)
        options = ('BatchMode=yes NumberOfPasswordPrompts=0 PreferredAuthentications=publickey PasswordAuthentication=no '
            'KbdInteractiveAuthentication=no IdentitiesOnly=yes IdentityAgent=none StrictHostKeyChecking=yes '
            'GlobalKnownHostsFile=/dev/null UserKnownHostsFile=/etc/pult-synthetic-client/known_hosts '
            'KnownHostsCommand=none VerifyHostKeyDNS=no UpdateHostKeys=no HostKeyAlgorithms=ssh-ed25519 '
            'PubkeyAcceptedAlgorithms=ssh-ed25519 ProxyCommand=none ProxyJump=none ForwardAgent=no ForwardX11=no '
            'ClearAllForwardings=yes ControlMaster=no ControlPath=none PermitLocalCommand=no SendEnv=-* '
            'ConnectionAttempts=1 ConnectTimeout=3 LogLevel=ERROR')
        base = ['/usr/bin/ssh', '-F', 'none', '-4', '-T', '-x'] + [part for option in options.split() for part in ('-o', option)]
        base += ['-i', str(keys / 'id_ed25519'), '-p', '22', '-l', 'pult-synthetic-trigger', '--', HOST, COMMAND]
        def case(name, argv, body, positive=False, hold=False, reason=None):
            global STAGE,DIAGNOSTIC
            STAGE='case_'+name
            before = len(records()); code, out, err, elapsed = exchange(drop + argv, body, hold)
            DIAGNOSTIC=dict(returncode=code,stdout_bytes=len(out),stderr_bytes=len(err),
                fixture_dispatches=len(records())-before,
                stderr_class='_'.join(label for label,marker in (
                    ('admin',b'administratively prohibited'),('stdio',b'stdio forwarding failed'),
                    ('badoption',b'Bad'),('permission',b'Permission denied'),('connect',b'connect failed'),
                    ('closed',b'Connection closed'),('hostkey',b'Host key verification')) if marker in err) or 'none')
            need(code == 0 and out == REPLY if positive else code != 0 and not out)
            need(len(records()) - before == int(positive))
            if reason: need(any(marker in err for marker in reason))
            if hold: need(2 <= elapsed < 8)
            rows.append(dict(case=name, expected_accept=positive, fixture_dispatches=int(positive), elapsed_seconds=round(elapsed, 3)))
        case('exact_real_ssh_ingress', base, TRIGGER, True)
        wrong = base.copy(); wrong[wrong.index('-i') + 1] = str(keys / 'wrong')
        case('wrong_client_key', wrong, TRIGGER, reason=(b'Permission denied',))
        wrong = [arg.replace('known_hosts', 'wrong_hosts') if arg.startswith('UserKnownHostsFile=') else arg for arg in base]
        case('wrong_host_pin', wrong, TRIGGER, reason=(b'Host key verification failed', b'HOST IDENTIFICATION HAS CHANGED'))
        case('wrong_original_command', base[:-1] + ['not-the-fixed-command'], TRIGGER)
        case('extra_input', base, TRIGGER + b'X'); case('truncated_input', base, TRIGGER[:-1])
        case('no_eof_bounded', base, TRIGGER, hold=True)
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as trap:
            trap.bind((HOST, 2222)); trap.listen(1)
            forwarding = base[:-3] + ['-W', HOST + ':2222', '--', HOST]
            # ERROR log level can omit the channel-detail text. The actual
            # sshd -T denial above and zero trap connections remain required.
            case('forwarding_blocked', forwarding, b'', reason=(b'administratively prohibited',b'stdio forwarding failed'))
            need(not select.select([trap], [], [], 0)[0])
        helper = dict(format='pult-synthetic-fixed-ssh-helper-v1', client_uid=50003, client_gid=50004,
            custody_host=HOST, server_key_sha256=fingerprint('host'), client_key_sha256=fingerprint('id_ed25519'),
            identity_file=str(keys / 'id_ed25519'), known_hosts_file=str(keys / 'known_hosts'),
            source=dict(path='/opt/pult-admission/bounded_ssh.py', sha256=context['sources']['bounded_ssh.py']))
        put(Path('/run/helper.json'), wire(helper))
        case('exact_real_helper_ingress', ['/usr/bin/python3.12', '-I', '-S', '-B', '/opt/pult-admission/ssh_helper.py',
             '/run/helper.json', sha(wire(helper))], b'', True)
        observed = records(); need(len(observed) == 2)
        need(all(os.waitid(os.P_PID, process.pid, os.WEXITED | os.WNOHANG | os.WNOWAIT) is None for process in owned))
        need(all(sha(Path('/opt/pult-admission', name).read_bytes()) == context['sources'][name]
                 for name in ('ssh_ingress.py', 'ssh_helper.py', 'bounded_ssh.py')))
        report = dict(format='pult-real-openssh-component-proof-v1', component_status='PASS', cases=rows,
            actual_ingress_proc_observations=observed, forwarding_target_connections=0, **LIMITS)
    finally:
        # /proc belongs ONLY to this new PID namespace (gate requires PID 1).
        # Inventory fixture daemon/session descendant groups before teardown.
        gate(root, True); groups = set(); cleanup_errors = []
        try:
            for entry in Path('/proc').iterdir():
                if entry.name.isdigit() and int(entry.name) > 1:
                    try: group = os.getpgid(int(entry.name))
                    except ProcessLookupError: continue
                    need(group > 1); groups.add(group)
        except BaseException as error: cleanup_errors.append(type(error).__name__)
        for process in reversed(owned):
            try: stop(process)
            except BaseException as error: cleanup_errors.append(type(error).__name__)
        for group in groups:
            try: os.killpg(group, signal.SIGKILL)
            except ProcessLookupError: pass
            except BaseException as error: cleanup_errors.append(type(error).__name__)
        try:
            deadline = time.monotonic() + 3
            while True:
                try: pid, _ = os.waitpid(-1, os.WNOHANG)
                except ChildProcessError: break
                if not pid: need(time.monotonic() < deadline); time.sleep(.02)
            need(not any(entry.name.isdigit() and int(entry.name) > 1 for entry in Path('/proc').iterdir()))
        except BaseException as error: cleanup_errors.append(type(error).__name__)
        for target in reversed(mounted):
            try: run(['/bin/umount', target], timeout=2)
            except BaseException as error: cleanup_errors.append(type(error).__name__)
        need(not cleanup_errors)
    report.update(owned_fixture_groups_reaped=True, owned_bind_mounts_unmounted=True)
    return report


def outer():
    global STAGE,DIAGNOSTIC
    STAGE='outer_preflight'
    need(sys.platform == 'linux' and os.getuid() == os.geteuid() == 0 and sys.version_info[:2] == (3, 12))
    need(sys.flags.isolated and sys.flags.no_site and sys.dont_write_bytecode and not sys.flags.optimize)
    deadline = time.monotonic() + 95
    snapshots = {name: source_file(name).read_bytes() for name in SOURCES}; hashes = {name: sha(raw) for name, raw in snapshots.items()}
    supply = json.loads(snapshots['openssh-supply/SUPPLY.json'])
    need(supply == dict(format='pult-openssh-component-supply-v1', component_only=True, install_allowed=False,
         maintainer_scripts_allowed=False, unpack_only=True, packages=[dict(file=n, bytes=s, sha256=h) for n, s, h in PACKAGES]))
    with tempfile.TemporaryDirectory(prefix=PREFIX, dir='/tmp') as temporary:
        root = Path(temporary); need(root.resolve() == root and root.parent == Path('/tmp')); root.chmod(0o700)
        for name, raw in snapshots.items(): put(root / 'code' / name, raw)
        put(root / 'context.json', wire(dict(outer_ns=ns(), sources=hashes)), 0o600)
        for name, size, digest in PACKAGES:
            raw = (HERE / 'openssh-supply' / name).read_bytes(); need(len(raw) == size and sha(raw) == digest)
            archive = root / 'debs' / name; put(archive, raw)
            run(['/usr/bin/dpkg-deb', '-x', str(archive), str(root / 'unpacked')], timeout=min(10, deadline-time.monotonic()))
        argv = ['/usr/bin/unshare', '--mount', '--net', '--pid', '--fork', '--mount-proc', '--kill-child=KILL',
                '/usr/bin/python3.12', '-I', '-S', '-B', str(root / 'code/native_ssh_admission.py'), '--child', str(root)]
        with (root / 'child.out').open('xb') as out, (root / 'child.err').open('xb') as err:
            process = subprocess.Popen(argv, stdin=subprocess.DEVNULL, stdout=out, stderr=err, close_fds=True,
                                       start_new_session=True, env={'PATH': '/usr/bin:/bin', 'LANG': 'C.UTF-8'})
        try:
            while os.waitid(os.P_PID, process.pid, os.WEXITED | os.WNOHANG | os.WNOWAIT) is None:
                need(time.monotonic() < deadline); time.sleep(.02)
        finally: code = stop(process)
        raw = (root / 'child.out').read_bytes()
        if code!=0 and len(raw)<=65536:
            try:
                diagnostic=json.loads(raw).get('failure_stage','unknown')
                if type(diagnostic) is str and len(diagnostic)<=80 and all(c.isascii() and (c.isalnum() or c=='_') for c in diagnostic):
                    STAGE='child_'+diagnostic
                detail=json.loads(raw).get('diagnostic',{})
                if (type(detail) is dict and len(detail)<=6 and all(type(k) is str and len(k)<=30 for k in detail)
                        and all(type(v) is int or type(v) is str and len(v)<=80 and all(c.isascii() and (c.isalnum() or c=='_') for c in v) for v in detail.values())):
                    DIAGNOSTIC=detail
            except (ValueError,AttributeError):pass
        need(code == 0 and len(raw) <= 65536 and not (root / 'child.err').read_bytes())
        report = json.loads(raw); need(report['component_status'] == 'PASS' and all(report[k] is v for k, v in LIMITS.items()))
        need({name: sha(source_file(name).read_bytes()) for name in SOURCES} == hashes)
        need(all(len((HERE / 'openssh-supply' / n).read_bytes()) == s and sha((HERE / 'openssh-supply' / n).read_bytes()) == h for n, s, h in PACKAGES))
    need(not root.exists())
    report.update(sources_unchanged=True, source_sha256=hashes, owned_namespace_exited=True, owned_temporary_tree_removed=True)
    return report


if __name__ == '__main__':
    try:
        if len(sys.argv) == 3 and sys.argv[1] == '--endpoint': endpoint(Path(sys.argv[2])); raise Refused('endpoint returned')
        elif len(sys.argv) == 3 and sys.argv[1] == '--child': result = child(Path(sys.argv[2]))
        else: need(len(sys.argv) == 1); result = outer()
        print(json.dumps(result, sort_keys=True), flush=True)
    except BaseException as error:
        print(json.dumps(dict(format='pult-real-openssh-component-proof-v1', component_status='FAIL',
                             failure_type=type(error).__name__,failure_stage=STAGE,diagnostic=DIAGNOSTIC, **LIMITS)), flush=True)
        raise SystemExit(1) from None
