"""Trusted synthetic fault injection only; never a production source entry."""
import json
import os
from pathlib import Path
import signal
import sys
import time

mode, helper, profile, approval = sys.argv[1:]
spec = json.loads(Path(profile).read_bytes())
if mode == 'late_probe':
    (Path(spec['state_directory']['path']) / 'late-child-executed').write_bytes(b'EXECUTED\n')
    os.execv('/usr/bin/python3.12', ['/usr/bin/python3.12', '-I', '-S', '-B', helper, profile, approval])
if mode == 'tail':
    signal.signal(signal.SIGTERM, signal.SIG_IGN)
    if os.fork() == 0:
        while True:
            time.sleep(60)
    os.execv('/usr/bin/python3.12', ['/usr/bin/python3.12', '-I', '-S', '-B', helper, profile, approval])
if mode != 'filesystem':
    raise SystemExit(2)
token = 'pult-construction-probe-' + approval[:24]
results = {}
for directory in ('/dev/shm', '/dev/mqueue', '/dev/pts', '/tmp', str(Path(helper).parent)):
    target = Path(directory) / token
    try:
        fd = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    except OSError:
        results[directory] = 'DENIED'
    else:
        os.close(fd)
        target.unlink()  # Only the exact exclusively-created synthetic probe.
        results[directory] = 'UNEXPECTED_WRITABLE'
host = json.loads(Path(spec['host_profile']).read_bytes())
try:
    fd = os.open(host['reader_credential_file'], os.O_WRONLY | os.O_NOFOLLOW)
except OSError:
    results['credential_write_open'] = 'DENIED'
else:
    os.close(fd)  # Never modify even the dummy credential.
    results['credential_write_open'] = 'UNEXPECTED_WRITABLE'
state = Path(spec['state_directory']['path'])
probe = state / token
fd = os.open(probe, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
os.close(fd)
probe.unlink()
results['state_directory'] = 'WRITABLE'
(state / 'filesystem-proof.json').write_text(json.dumps(results, sort_keys=True))
raise SystemExit(3)
