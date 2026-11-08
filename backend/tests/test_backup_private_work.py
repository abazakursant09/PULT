"""Offline ownership contract + mocked POSIX allocation; NOT Linux tmpfs/runtime proof.

No Docker, secrets, database tools or remote transports are invoked. Shell probes
execute real runner source with command functions substituted for all external work.
"""
import os
from pathlib import Path
import re
import shutil
import subprocess

import pytest
import yaml


ROOT = Path(__file__).resolve().parents[2]
OPS = ROOT / "ops/backup"


@pytest.mark.parametrize("target,size", [("/work", "2g"), ("/tmp", "64m")])
def test_restrictive_tmpfs_owned_by_nonroot_runner(target, size):
    service = yaml.safe_load((ROOT / "docker-compose.backup.yml").read_text())["services"]["pg-backup"]
    assert service["user"] == "10002:10002"
    mounts = dict(mount.split(":", 1) for mount in service["tmpfs"])
    assert set(mounts) == {"/work", "/tmp"}
    options = set(mounts[target].split(","))
    assert options == {"rw", "noexec", "nosuid", f"size={size}", "mode=1700", "uid=10002", "gid=10002"}
    assert service["read_only"] is True
    assert service["cap_drop"] == ["ALL"]
    assert service["security_opt"] == ["no-new-privileges:true"]
    assert service["secrets"] == ["backup_db_password"]
    assert not service.get("volumes")


def _script(name):
    return (OPS / name).read_text(encoding="utf8")


@pytest.mark.parametrize("name", ["backup.sh", "restore.sh"])
def test_allocation_explicit_private_work_template_without_path_override(name):
    text = _script(name)
    kind = name.removesuffix(".sh")
    calls = re.findall(r"mktemp[^\n)]*", text)
    assert calls == [f"mktemp -d /work/pult-{kind}.XXXXXX"]
    assert text.index("umask 077") < text.index("mktemp")
    assert "set -eu" in text
    assert "TMPDIR" not in calls[0] and "/tmp" not in calls[0]
    assert "trap 'rm -rf \"$TMP\"' EXIT INT TERM" in text


def _shell(source, **extra):
    sh = ("C:/Program Files/Git/usr/bin/sh.exe" if os.name == "nt" else shutil.which("sh"))
    assert sh and Path(sh).is_file(), "POSIX shell required for bounded offline probes"
    env = {key: value for key, value in os.environ.items() if key in {"SystemRoot", "WINDIR", "TEMP", "TMP"}}
    env.update(PATH="/usr/bin:/bin", TMPDIR="/not-approved", **extra)
    return subprocess.run([sh, "-c", source], env=env, capture_output=True, text=True, timeout=10)


@pytest.mark.parametrize("name", ["backup.sh", "restore.sh"])
def test_mocked_allocator_gets_only_approved_template_and_cleanup(name):
    # Extract the actual assignment + trap; no runtime path is created or removed.
    text = _script(name)
    start = text.index('TMP="$(')
    end = text.index("EXIT INT TERM", start) + len("EXIT INT TERM")
    allocation = text[start:end]
    kind = name.removesuffix(".sh")
    path = f"/work/pult-{kind}.ABC123"
    stubs = (
        'set -eu; umask 077; '
        'die() { printf "FAIL:%s\\n" "$*" >&2; exit 1; }; '
        'mktemp() { printf "ALLOC:%s:%s:%s\\n" "$#" "${1-}" "${2-}" >&2; '
        f'printf "%s\\n" "{path}"; }}; '
        'rm() { printf "CLEAN:%s:%s\\n" "$1" "$2"; }; '
    )
    result = _shell(stubs + allocation + '; printf "PRIVATE:%s MASK:%s\\n" "$TMP" "$(umask)"')
    assert result.returncode == 0, result.stderr
    assert result.stderr.strip() == f"ALLOC:2:-d:/work/pult-{kind}.XXXXXX"
    assert f"PRIVATE:{path} MASK:0077" in result.stdout or f"PRIVATE:{path} MASK:077" in result.stdout
    assert result.stdout.count(f"CLEAN:-rf:{path}") == 1


@pytest.mark.parametrize("name", ["backup.sh", "restore.sh"])
def test_mocked_allocation_failure_stops_full_runner_before_downstream(name, tmp_path):
    # Existing synthetic file satisfies restore's existence check; never read as a key.
    identity = tmp_path / "synthetic-identity-not-a-key"
    identity.write_text("synthetic", encoding="utf8")
    source = _script(name).replace("\r\n", "\n")
    stubs = (
        'mktemp() { printf "ALLOC:%s:%s:%s\\n" "$#" "${1-}" "${2-}" >&2; return 71; }; '
        'date() { printf "synthetic-time\\n"; }; '
    )
    for command in ["pg_dump", "pg_restore", "psql", "rclone", "age", "cat", "sha256sum", "integrity.sh", "rm"]:
        # Dotted shell function names are not portable; integrity.sh is reached only
        # after rclone/age/psql. Those stubs already stop any accidentally reached work.
        if command == "integrity.sh":
            continue
        stubs += f'{command}() {{ printf "DOWNSTREAM:{command}\\n" >&2; return 99; }}; '
    env = {
        "PGHOST": "synthetic.invalid", "PGDATABASE": "synthetic", "PGUSER": "synthetic",
        "BACKUP_S3_ENDPOINT": "https://synthetic.invalid", "BACKUP_S3_BUCKET": "synthetic",
        "BACKUP_S3_PREFIX": "synthetic", "BACKUP_S3_ACCESS_KEY_ID": "synthetic",
        "BACKUP_S3_SECRET_ACCESS_KEY": "synthetic", "BACKUP_ENCRYPTION_RECIPIENT": "synthetic",
        "BACKUP_VERIFY_ACCESS_KEY_ID": "synthetic-verifier",
        "BACKUP_VERIFY_SECRET_ACCESS_KEY": "synthetic-verifier-secret",
        "RESTORE_S3_ENDPOINT": "https://synthetic.invalid", "RESTORE_S3_BUCKET": "synthetic",
        "RESTORE_S3_ACCESS_KEY_ID": "synthetic", "RESTORE_S3_SECRET_ACCESS_KEY": "synthetic",
        "RESTORE_OBJECT_KEY": "synthetic", "BACKUP_RESTORE_IDENTITY_FILE": identity.as_posix(),
    }
    result = _shell(stubs + "\n" + source, **env)
    assert result.returncode != 0
    assert result.stderr.count("ALLOC:") == 1, result.stderr
    assert f"ALLOC:2:-d:/work/pult-{name.removesuffix('.sh')}.XXXXXX" in result.stderr
    assert "DOWNSTREAM:" not in result.stderr + result.stdout
    assert "SUCCESS" not in result.stdout
