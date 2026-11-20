"""Versioned fixed SSH child primitive, only trusted dedicated helper wiring."""
import ipaddress
import math
import os
from pathlib import Path
import selectors
import signal
import subprocess
import sys
import time

from types import SimpleNamespace
proto=SimpleNamespace(COMMAND='pult-synthetic-one-v1', MAX_REQUEST=128, MAX_RESPONSE=512)

MAX_STDERR=1024
CLEANUP_SECONDS=2.0
IDENTITY='/etc/pult-synthetic-client/id_ed25519'
KNOWN_HOSTS='/etc/pult-synthetic-client/known_hosts'
SSH_OPTIONS=(
    'BatchMode=yes','NumberOfPasswordPrompts=0','PreferredAuthentications=publickey',
    'PubkeyAuthentication=yes','PasswordAuthentication=no','KbdInteractiveAuthentication=no',
    'GSSAPIAuthentication=no','HostbasedAuthentication=no','IdentitiesOnly=yes',
    'IdentityAgent=none','AddKeysToAgent=no','PKCS11Provider=none','CertificateFile=none',
    'HostKeyAlgorithms=ssh-ed25519','PubkeyAcceptedAlgorithms=ssh-ed25519',
    'StrictHostKeyChecking=yes','UserKnownHostsFile='+KNOWN_HOSTS,
    'GlobalKnownHostsFile=/dev/null','KnownHostsCommand=none','UpdateHostKeys=no',
    'VerifyHostKeyDNS=no','CheckHostIP=yes','CanonicalizeHostname=no',
    'ClearAllForwardings=yes','ForwardAgent=no','ForwardX11=no','RequestTTY=no',
    'ControlMaster=no','ControlPath=none','ControlPersist=no','ProxyCommand=none','ProxyJump=none',
    'PermitLocalCommand=no','LocalCommand=none','RemoteCommand=none','SendEnv=-*',
    'ForkAfterAuthentication=no','StdinNull=no','SessionType=default',
    'ConnectionAttempts=1','ConnectTimeout=5','ServerAliveInterval=0','EscapeChar=none','LogLevel=ERROR',
)

class TransportRefused(Exception): pass

class ChildCleanupUncertain(TransportRefused):
    """Owned child cleanup not confirmed; caller must not infer any success."""

class _StopExchange(Exception): pass

def build_ssh_argv(host):
    """Pure proposal: no file access or SSH invocation; one literal IPv4 target.

    Root-controlled key/pin validation and actual forced-command authentication
    remain absent. No remote command/path/user/port/option comes from a request.
    """
    try:
        address=ipaddress.IPv4Address(host) if type(host) is str else None
        if (address is None or str(address)!=host or address.is_unspecified
                or address.is_loopback or address.is_multicast or address.is_link_local
                or address.is_reserved or int(address)>>24==0):
            raise ValueError
    except (ValueError,TypeError,ipaddress.AddressValueError):
        raise TransportRefused('literal approved IPv4 required') from None
    options=tuple(value for option in SSH_OPTIONS for value in ('-o',option))
    return ('/usr/bin/ssh','-F','none','-4','-T','-x',*options,
            '-i',IDENTITY,'-p','22','-l','pult-synthetic-trigger','--',host,proto.COMMAND)

def _exited_without_reaping(process):
    # Keep the owned leader PID reserved (even as a zombie) until killpg. Calling
    # Popen.poll()/wait() here would release the PID before group cleanup.
    return os.waitid(os.P_PID,process.pid,os.WEXITED|os.WNOHANG|os.WNOWAIT) is not None

def _owner_environment(*,initialize=False):
    """Dedicated fresh CPython3.12 helper, never a threaded application worker.

    Resetting default SIGCHLD using the pinned CPython sigaction path clears
    native SA_NOCLDWAIT before spawn. No other code may reap or change this
    environment while the operation runs; this is not a hostile-code sandbox.
    """
    try:
        if (sys.platform!='linux' or sys.implementation.name!='cpython'
                or sys.version_info[:2]!=(3,12)
                or {entry.name for entry in Path('/proc/self/task').iterdir()}!={str(os.getpid())}
                or signal.getsignal(signal.SIGCHLD)!=signal.SIG_DFL):
            raise TransportRefused('dedicated exclusive child owner required')
        for number in signal.valid_signals():
            handler=signal.getsignal(number)
            if handler not in (None,signal.SIG_DFL,signal.SIG_IGN) and not (
                    number==signal.SIGINT and handler is signal.default_int_handler):
                raise TransportRefused('custom signal reaper not supported')
        if initialize:
            try:
                os.waitid(os.P_ALL,0,os.WEXITED|os.WNOHANG|os.WNOWAIT)
            except ChildProcessError:
                pass  # no existing child, not even a running one
            else:
                raise TransportRefused('fresh child owner required')
            signal.signal(signal.SIGCHLD,signal.SIG_DFL)
    except (OSError,ValueError):
        raise TransportRefused('exclusive child owner unavailable') from None

def _close_pipes(process):
    uncertain=False
    for stream in (process.stdin,process.stdout,process.stderr):
        try:
            if stream is not None and not stream.closed: stream.close()
        except OSError:
            uncertain=True
    return uncertain

def _cleanup_owned_child(process):
    # Never signal using a PID that has lost its direct-child reservation.
    # In particular, ECHILD must not be swallowed then followed by killpg.
    try:
        _owner_environment()
        if process.returncode is not None: raise ChildProcessError
        _exited_without_reaping(process)
    except (OSError,TransportRefused):
        _close_pipes(process)
        raise ChildCleanupUncertain('child ownership not confirmed; no signal sent') from None
    uncertain=False
    try:
        os.killpg(process.pid,signal.SIGKILL)
    except ProcessLookupError:
        pass  # owned group has no remaining live processes
    except OSError:
        uncertain=True
    try:
        code=process.wait(timeout=CLEANUP_SECONDS)
    except (OSError,subprocess.TimeoutExpired):
        uncertain=True;code=None
    uncertain=_close_pipes(process) or uncertain
    if uncertain:
        raise ChildCleanupUncertain('owned child cleanup not confirmed') from None
    return code

def _pump(process,body,deadline):
    sent=0;stdout=bytearray();stderr_count=0
    with selectors.DefaultSelector() as selector:
        for stream,events,label in ((process.stdin,selectors.EVENT_WRITE,'in'),
                                    (process.stdout,selectors.EVENT_READ,'out'),
                                    (process.stderr,selectors.EVENT_READ,'err')):
            if label=='in' and not body:
                stream.close();continue
            os.set_blocking(stream.fileno(),False)
            selector.register(stream,events,label)
        while True:
            remaining=deadline-time.monotonic()
            if remaining<=0: raise _StopExchange
            if not selector.get_map() and _exited_without_reaping(process):
                return bytes(stdout)
            events=selector.select(min(remaining,.02))
            for key,_ in events:
                stream=key.fileobj
                try:
                    if key.data=='in':
                        count=os.write(stream.fileno(),body[sent:])
                        if count<=0: raise _StopExchange
                        sent+=count
                        if sent==len(body):
                            selector.unregister(stream);stream.close()
                        continue
                    capacity=(proto.MAX_RESPONSE-len(stdout)) if key.data=='out' else (MAX_STDERR-stderr_count)
                    chunk=os.read(stream.fileno(),min(8192,capacity+1))
                except BlockingIOError:
                    continue
                if not chunk:
                    selector.unregister(stream);stream.close();continue
                if len(chunk)>capacity: raise _StopExchange
                if key.data=='out': stdout.extend(chunk)
                else:
                    stderr_count+=len(chunk)  # never decode, return or log stderr
                    raise _StopExchange  # exact silent success only

def _run_child(body,argv,*,timeout=80.0):
    """Private bounded primitive for compiled SSH/key-inspection invocations.

    Returns (stdout, exact exit) or None only after confirmed cleanup. No raw
    command/timeout is ever accepted from a protocol request or JSON config.
    """
    if (sys.platform!='linux' or not hasattr(os,'WNOWAIT')
            or type(argv) is not tuple or not 0<len(argv)<=128
            or any(type(arg) is not str or '\0' in arg or len(arg)>1024
                   or (not arg and not (index==3 and argv[:3]==('/usr/bin/ssh-keygen','-y','-P')))
                   for index,arg in enumerate(argv))
            or not os.path.isabs(argv[0]) or type(timeout) not in (int,float)
            or not math.isfinite(timeout) or not .05<=timeout<=90
            or type(body) is not bytes or len(body)>proto.MAX_REQUEST):
        raise TransportRefused('bounded Linux child parameters required')
    _owner_environment(initialize=True)
    deadline=time.monotonic()+timeout
    try:
        process=subprocess.Popen(argv,stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.PIPE,
            bufsize=0,shell=False,close_fds=True,restore_signals=True,start_new_session=True,
            cwd='/',env={'PATH':'/usr/bin:/bin','LANG':'C.UTF-8'},umask=0o077)
    except (OSError,ValueError):
        return None
    complete=False;output=b''
    try:
        try:
            output=_pump(process,body,deadline)
            complete=True
        except (OSError,ValueError,_StopExchange):
            pass
    finally:
        # Also runs for BaseException cancellation. Uncertain cleanup raises a
        # separate fixed error and cannot release a parsed successful response.
        code=_cleanup_owned_child(process)
    if not complete or time.monotonic()>deadline:
        return None
    return output,code
