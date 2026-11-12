"""Offline executable shell proof; no S3, database, credentials or restore admission."""
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess

import pytest

ROOT = Path(__file__).resolve().parents[2]
OPS = ROOT / 'ops/backup'
SHA = 'a' * 64


def checkpoint(scope='synthetic', sequence='7', cipher=SHA, records=''):
    return (f'pult-erasure-checkpoint-v1\nscope {scope}\nsequence {sequence}\n'
            f'backup-sha256 {cipher}\ncoverage complete\n{records}')


def run_guard(tmp_path, content, **changes):
    path = tmp_path / 'checkpoint'
    path.write_bytes(content.encode('ascii'))
    env = {k: v for k, v in os.environ.items() if k in {'SystemRoot', 'WINDIR', 'TEMP', 'TMP'}}
    env.update(PATH='/usr/bin:/bin', RESTORE_ERASURE_CHECKPOINT_SHA256=hashlib.sha256(path.read_bytes()).hexdigest(),
               RESTORE_ERASURE_EXPECTED_SCOPE='synthetic', RESTORE_ERASURE_EXPECTED_SEQUENCE='7',
               RESTORE_ERASURE_EXPECTED_CIPHER_SHA256=SHA)
    env.update(changes)
    shell = 'C:/Program Files/Git/usr/bin/sh.exe' if os.name == 'nt' else shutil.which('sh')
    return subprocess.run([shell, (OPS / 'erasure-checkpoint.sh').as_posix(), path.as_posix()],
                          capture_output=True, text=True, env=env, timeout=10)


def test_authoritative_empty_checkpoint_passes_only_quarantine_preflight(tmp_path):
    result = run_guard(tmp_path, checkpoint())
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == 'erasure checkpoint: EMPTY_CHECKPOINT_QUARANTINE_ONLY'


@pytest.mark.parametrize('content', [
    '', checkpoint(sequence='6'), checkpoint(sequence='8'), checkpoint(scope='other'),
    checkpoint(cipher='b' * 64), checkpoint().replace('v1', 'v2'),
    checkpoint().replace('coverage complete', 'coverage partial'),
    checkpoint() + '\n', checkpoint() + 'garbage\n',
    checkpoint().replace('sequence 7', 'sequence 7\nsequence 7'),
    checkpoint().replace('sequence 7', 'sequence 07'), checkpoint(sequence='7; SELECT 1'),
    checkpoint(records='00000000-0000-0000-0000-000000000001 completed\n'),
    checkpoint(records='00000000-0000-0000-0000-000000000001 intent\n'),
    checkpoint(records='00000000-0000-0000-0000-000000000001 restored\n'),
    checkpoint() + 'x' * 65537,
    checkpoint().rstrip('\n'), checkpoint().replace('coverage complete', 'coverage complete\x00'),
], ids=[f'case-{index}' for index in range(18)])
def test_bad_or_nonempty_checkpoint_never_passes_or_logs_ids(tmp_path, content):
    result = run_guard(tmp_path, content)
    assert result.returncode != 0
    assert 'EMPTY_CHECKPOINT' not in result.stdout
    assert '00000000-0000-0000-0000-000000000001' not in result.stdout + result.stderr


@pytest.mark.parametrize('name', ['RESTORE_ERASURE_CHECKPOINT_SHA256', 'RESTORE_ERASURE_EXPECTED_SCOPE',
                                  'RESTORE_ERASURE_EXPECTED_SEQUENCE', 'RESTORE_ERASURE_EXPECTED_CIPHER_SHA256'])
def test_missing_or_wrong_trust_pin_fails(tmp_path, name):
    assert run_guard(tmp_path, checkpoint(), **{name: ''}).returncode != 0
    assert run_guard(tmp_path, checkpoint(), **{name: 'wrong'}).returncode != 0


def test_well_formed_but_wrong_checkpoint_digest_rejected(tmp_path):
    result=run_guard(tmp_path,checkpoint(),RESTORE_ERASURE_CHECKPOINT_SHA256='b'*64)
    assert result.returncode!=0 and 'checkpoint digest mismatch' in result.stderr


def test_restore_wires_guard_before_fetch_and_quarantine_before_terminal_outcome():
    text = (OPS / 'restore.sh').read_text()
    assert text.index('erasure-checkpoint.sh') < text.index('log "fetch manifest')
    assert '[ "$MAN_CIPHER_SHA" = "$RESTORE_ERASURE_EXPECTED_CIPHER_SHA256" ]' in text
    assert text.index('integrity.sh ||') < text.index('ALLOW_CONNECTIONS false')
    assert text.index('ALLOW_CONNECTIONS false') < text.index('QUARANTINED restored')
    assert '-d postgres -v "target_db=$PGDATABASE"' in text
    assert "current_setting('pult.restore_target')" in text
    assert 'log "SUCCESS restored' not in text
    assert 'erasure-checkpoint.sh' in (OPS / 'Dockerfile').read_text()
    workflow=(ROOT/'.github/workflows/backup_restore_synthetic.yml').read_text()
    assert 'if restore_target occupied_restore' in workflow
    assert "application_name='restore_guard_held_peer'" in workflow
    assert 'test "$CLOSED" = f' in workflow and 'pg_terminate_backend(pid)' in workflow


@pytest.mark.parametrize('scenario', ['empty', 'nonempty', 'oversized', 'missing_pin', 'substituted_backup', 'quarantine_failure', 'changed_checkpoint', 'system_target', 'conninfo_target'])
def test_full_runner_mocked_fail_closed_boundaries(tmp_path, scenario):
    """Execute real restore source/helper with only external S3/PG/age commands stubbed."""
    tools = tmp_path / 'bin'
    tools.mkdir()
    work = tmp_path / 'private-work'
    work.mkdir()
    cipher, plain = b'synthetic-cipher', b'synthetic-plain'
    cipher_sha = hashlib.sha256(cipher).hexdigest()
    for name, data in [('cipher', cipher), ('plain', plain), ('identity', b'not-a-real-identity')]:
        (tmp_path / name).write_bytes(data)
    manifest = {'ciphertext_sha256': cipher_sha, 'ciphertext_size':len(cipher),
                'plaintext_sha256':hashlib.sha256(plain).hexdigest()}
    (tmp_path / 'manifest').write_text(json.dumps(manifest))
    records = '00000000-0000-0000-0000-000000000001 completed\n' if scenario == 'nonempty' else ''
    cp = tmp_path / 'checkpoint'
    # Substituted ciphertext+manifest are self-consistent, but not the trusted backup.
    trusted_cipher = SHA if scenario == 'substituted_backup' else cipher_sha
    payload=checkpoint(cipher=trusted_cipher, records=records)
    if scenario=='oversized':
        payload+='x'*70000
    cp.write_bytes(payload.encode())
    scripts = {
        'mktemp':'printf "%s\\n" "$FIX/private-work"',
        'rclone':'''printf 'rclone\\n' >> "$FIX/trace"
case "$1" in
copyto) case "$2" in *.manifest.json) cp "$FIX/manifest" "$3";; *) cp "$FIX/cipher" "$3";; esac;;
lsjson) printf '{"Size":16}';;
esac''',
        'age':'printf "age\\n" >> "$FIX/trace"; cp "$FIX/plain" "$5"',
        'pg_restore':'printf "pg_restore\\n" >> "$FIX/trace"',
        'psql':'''printf 'psql\\n' >> "$FIX/trace"
if [ "$1" = '-tAc' ]; then printf '0'; else
cat > "$FIX/quarantine-sql"
[ "$SCENARIO" != quarantine_failure ] || exit 7
if [ "$SCENARIO" = changed_checkpoint ]; then printf 'changed' >> "$FIX/checkpoint"; fi
fi''',
        'integrity.sh':'printf "integrity\\n" >> "$FIX/trace"',
        'erasure-checkpoint.sh': 'wc -c < "$1" > "$FIX/snapshot_size"; exec /bin/sh "$GUARD" "$@"',
    }
    for name, body in scripts.items():
        path = tools / name
        path.write_bytes(('#!/bin/sh\nset -eu\n'+body+'\n').encode())
        path.chmod(0o755)
    env = {k:v for k,v in os.environ.items() if k in {'SystemRoot','WINDIR','TEMP','TMP'}}
    env.update(PATH='/usr/bin:/bin', FIX=tmp_path.as_posix(), TOOL_DIR=tools.as_posix(),
               GUARD=(OPS/'erasure-checkpoint.sh').as_posix(), SCENARIO=scenario,
               PGHOST='synthetic.invalid',PGDATABASE='synthetic',PGUSER='synthetic',
               RESTORE_S3_ENDPOINT='https://synthetic.invalid', RESTORE_S3_BUCKET='synthetic',
               RESTORE_S3_ACCESS_KEY_ID='synthetic', RESTORE_S3_SECRET_ACCESS_KEY='synthetic',
               RESTORE_OBJECT_KEY='synthetic', BACKUP_RESTORE_IDENTITY_FILE=(tmp_path/'identity').as_posix(),
               RESTORE_ERASURE_CHECKPOINT_FILE=cp.as_posix(),
               RESTORE_ERASURE_CHECKPOINT_SHA256=hashlib.sha256(cp.read_bytes()).hexdigest(),
               RESTORE_ERASURE_EXPECTED_SCOPE='synthetic', RESTORE_ERASURE_EXPECTED_SEQUENCE='7',
               RESTORE_ERASURE_EXPECTED_CIPHER_SHA256=trusted_cipher)
    if scenario == 'missing_pin':
        env.pop('RESTORE_ERASURE_EXPECTED_SEQUENCE')
    if scenario=='system_target':
        env['PGDATABASE']='postgres'
    if scenario=='conninfo_target':
        env['PGDATABASE']='host=other dbname=synthetic'
    shell='C:/Program Files/Git/usr/bin/sh.exe' if os.name=='nt' else shutil.which('sh')
    prefix='TOOL_DIR=$(cygpath -u "$TOOL_DIR"); ' if os.name=='nt' else ''
    source=prefix+'export PATH="$TOOL_DIR:$PATH"; exec /bin/sh "$RESTORE"'
    env['RESTORE']=(OPS/'restore.sh').as_posix()
    result=subprocess.run([shell,'-c',source],env=env,capture_output=True,text=True,timeout=15)
    trace=(tmp_path/'trace').read_text().splitlines() if (tmp_path/'trace').exists() else []
    if scenario=='empty':
        assert result.returncode==0, result.stderr
        assert trace[-2:]==['integrity','psql']
        assert 'QUARANTINED restored' in result.stdout and 'SUCCESS' not in result.stdout
        assert 'ALLOW_CONNECTIONS false' in (tmp_path/'quarantine-sql').read_text()
    else:
        assert result.returncode!=0, result.stdout
        assert 'QUARANTINED restored' not in result.stdout
        if scenario in {'missing_pin','nonempty','oversized','system_target','conninfo_target'}:
            assert trace==[]
        if scenario=='oversized':
            assert int((tmp_path/'snapshot_size').read_text())==65537
        if scenario=='substituted_backup':
            assert 'age' not in trace and 'psql' not in trace
        if scenario=='changed_checkpoint':
            assert trace[-1]=='psql' and 'ALLOW_CONNECTIONS false' in (tmp_path/'quarantine-sql').read_text()
    assert '00000000-0000-0000-0000-000000000001' not in result.stdout+result.stderr
