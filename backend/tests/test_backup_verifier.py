"""Offline execution of the real runner's credential and verification sections."""
import os
import json
from pathlib import Path
import shutil
import subprocess

import pytest

ROOT = Path(__file__).resolve().parents[2]
SOURCE = (ROOT / "ops/backup/backup.sh").read_text(encoding="utf8")


@pytest.mark.parametrize("role,actions", [
    ("writer", {"s3:PutObject", "s3:AbortMultipartUpload"}),
    ("verifier", {"s3:GetObject"}),
])
def test_ci_policy_is_prefix_scoped_without_extra_grants(role, actions):
    policy = json.loads((ROOT / f"ops/backup/ci-{role}-policy.json").read_text())
    assert policy["Version"] == "2012-10-17"
    assert len(policy["Statement"]) == 1
    rule = policy["Statement"][0]
    assert rule["Effect"] == "Allow"
    assert set(rule["Action"]) == actions
    assert rule["Resource"] == ["arn:aws:s3:::pult-backup-ci/pult/pg/*"]


def shell(code, **values):
    sh = "C:/Program Files/Git/usr/bin/sh.exe" if os.name == "nt" else shutil.which("sh")
    env = {k: v for k, v in os.environ.items() if k in {"SystemRoot", "WINDIR", "TEMP", "TMP"}}
    env.update(PATH="/usr/bin:/bin", **values)
    return subprocess.run([sh, "-c", code], env=env, capture_output=True, text=True, timeout=10)


def test_upload_uses_only_writer_without_implicit_read_or_bucket_creation():
    uploads = [line for line in SOURCE.splitlines() if line.startswith("rclone copyto")]
    assert len(uploads) == 2
    for line in uploads:
        assert '"bk:' in line
        for flag in ("--no-check-dest", "--s3-no-head", "--s3-no-check-bucket"):
            assert flag in line
    assert 'RCLONE_CONFIG_BV_ACCESS_KEY_ID="$BACKUP_VERIFY_ACCESS_KEY_ID"' in SOURCE
    assert 'RCLONE_CONFIG_BV_SECRET_ACCESS_KEY="$BACKUP_VERIFY_SECRET_ACCESS_KEY"' in SOURCE


def test_same_access_key_is_rejected_before_database_work():
    check = '[ "$BACKUP_VERIFY_ACCESS_KEY_ID" != "$BACKUP_S3_ACCESS_KEY_ID" ] || die "writer and verifier must use distinct access keys"'
    assert check in SOURCE
    assert SOURCE.index(check) < SOURCE.index('TMP="$(')
    result = shell('die() { exit 1; }; ' + check,
                   BACKUP_VERIFY_ACCESS_KEY_ID="same", BACKUP_S3_ACCESS_KEY_ID="same")
    assert result.returncode != 0


@pytest.mark.parametrize("missing", ["BACKUP_VERIFY_ACCESS_KEY_ID", "BACKUP_VERIFY_SECRET_ACCESS_KEY"])
def test_missing_verifier_fails_before_dump_or_upload(missing):
    env = dict(PGHOST="test", PGDATABASE="test", PGUSER="test",
               BACKUP_S3_ENDPOINT="https://test.invalid", BACKUP_S3_BUCKET="test",
               BACKUP_S3_PREFIX="test", BACKUP_S3_ACCESS_KEY_ID="writer",
               BACKUP_S3_SECRET_ACCESS_KEY="writer-secret", BACKUP_ENCRYPTION_RECIPIENT="test",
               BACKUP_VERIFY_ACCESS_KEY_ID="reader", BACKUP_VERIFY_SECRET_ACCESS_KEY="reader-secret")
    del env[missing]
    result = shell(SOURCE, **env)
    assert result.returncode != 0
    assert missing in result.stderr
    assert "pg_dump (custom format) start" not in result.stdout


@pytest.mark.parametrize("mode,ok", [("ok", True), ("request-failed", False),
                                      ("malformed", False), ("size-mismatch", False)])
def test_metadata_verification_checks_both_objects_and_fails_closed(tmp_path, mode, ok):
    start = SOURCE.index("verify_size() {")
    end = SOURCE.index('\nEND="', start)
    code = SOURCE[start:end]
    manifest = tmp_path / "manifest.json"
    manifest.write_text("{}", encoding="utf8")
    stub = '''set -eu
die() { printf 'FAIL:%s\n' "$1" >&2; exit 1; }
rclone() {
  printf 'CALL:%s:%s:%s\n' "$1" "$2" "$3" >> "$TMP/calls"
  case "$MODE" in
    request-failed) printf '{"Size":10}'; return 7;;
    malformed) printf '{}';;
    size-mismatch) printf '{"Size":999}';;
    *) case "$3" in *.manifest.json) printf '{"Size":2}';;
         *) printf '{"Size":10}';; esac;;
  esac
}
'''
    result = shell(stub + code, TMP=tmp_path.as_posix(), MANIFEST=manifest.as_posix(),
                   KEY="prefix/test.age", MKEY="prefix/test.age.manifest.json",
                   CIPHER_SIZE="10", BACKUP_S3_BUCKET="bucket", MODE=mode)
    assert (result.returncode == 0) == ok, result.stderr
    calls = (tmp_path / "calls").read_text()
    assert "CALL:lsjson:--stat:bv:bucket/prefix/test.age" in calls
    if ok:
        assert "CALL:lsjson:--stat:bv:bucket/prefix/test.age.manifest.json" in calls
