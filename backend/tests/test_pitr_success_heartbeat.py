"""Real POSIX source + synthetic boundary stubs; no PG, service, network or restore.

Windows Git sh is a functional shell check only, NOT Linux ownership/namespace proof.
Temporary records are confined to pytest's owned temporary directory.
"""
import os
from pathlib import Path
import shutil
import subprocess

import pytest

SCHED = Path(__file__).resolve().parents[2] / "ops/pitr/scheduler"
SHELL = ("C:/Program Files/Git/usr/bin/sh.exe" if os.name == "nt"
         else shutil.which("sh"))
pytestmark = pytest.mark.skipif(not SHELL or not Path(SHELL).is_file(), reason="POSIX sh unavailable")


def shell_path(path):
    value = Path(path).resolve().as_posix()
    return "/" + value[0].lower() + value[2:] if os.name == "nt" else value


def invoke(target, *, runner=False, kind="full", epoch="1700000000", fault="", command=0,
           enabled="1", lock=0):
    env = {key: value for key, value in os.environ.items()
           if key in {"SystemRoot", "WINDIR", "TEMP", "TMP"}}
    env.update(PATH="/usr/bin:/bin", TEST_LIB=shell_path(SCHED / "_lib.sh"),
               PITR_HEARTBEAT_FILE=target, TEST_EPOCH=epoch, TEST_FAULT=fault,
               TEST_COMMAND=str(command), TEST_LOCK=str(lock),
               PITR_SCHEDULER_ENABLED=enabled)
    prefix = r'''
. "$TEST_LIB"
date() { [ "${TEST_DONE:-1}" = 1 ] || return 74; command printf '%s\n' "$TEST_EPOCH"; }
mktemp() { [ "$TEST_FAULT" != alloc ] || return 71; command mktemp "$@"; }
printf() {
    if [ "$TEST_FAULT" = write ] && [ "${2:-}" = "$TEST_EPOCH" ]; then return 72; fi
    command printf "$@"
}
mv() {
    [ "$TEST_FAULT" != rename ] || return 73
    # Check the complete new record and the still-unchanged target BEFORE rename.
    IFS= read -r record < "$2" || return 75
    [ "$record" = "$TEST_EPOCH" ] || return 76
    command mv "$@"
}
'''
    if runner:
        source = (SCHED / "run-backup.sh").read_text(encoding="utf-8")
        load = '. "$(dirname "$0")/_lib.sh"'
        assert source.count(load) == 1
        # Source the real library once above, then mock lock and bounded command.
        # The actual runner's dormant gate, type check and success/failure branches execute.
        prefix += r'''
pitr_lock() { [ "$TEST_LOCK" = 0 ] || exit "$TEST_LOCK"; }
pitr_bounded() {
    [ "$1" = pgbackrest ] && [ "$4" = backup ] || return 77
    TEST_DONE=1
    return "$TEST_COMMAND"
}
TEST_DONE=0
'''
        script = prefix + "\nset -- " + kind + "\n" + source.replace(load, ":", 1)
    else:
        script = prefix + r'''
command -v pitr_publish_heartbeat >/dev/null || exit 88
umask 022
trap ':' 0
before_umask=$(umask)
before_traps=$(trap)
if pitr_publish_heartbeat; then result=0; else result=9; fi
[ "$(umask)" = "$before_umask" ] || exit 81
[ "$(trap)" = "$before_traps" ] || exit 82
exit "$result"
'''
    return subprocess.run([str(SHELL), "-c", script], env=env, capture_output=True,
                          text=True, timeout=15)


@pytest.mark.parametrize("kind", ["full", "diff"])
def test_success_publishes_after_command(tmp_path, kind):
    target = tmp_path / "last-success.epoch"
    target.write_bytes(b"old\n")
    result = invoke(shell_path(target), runner=True, kind=kind)
    assert result.returncode == 0, result.stderr
    assert target.read_bytes() == b"1700000000\n"
    assert f"pitr_backup=ok type={kind}" in result.stdout
    assert list(tmp_path.glob("*.tmp.*")) == []
    if os.name != "nt":
        assert target.stat().st_mode & 0o777 == 0o600


@pytest.mark.parametrize("options,expected", [
    ({"command": 1}, 1), ({"command": 124}, 1), ({"enabled": "0"}, 3),
    ({"kind": "invalid"}, 2), ({"lock": 4}, 4),
])
def test_no_refresh_without_command_success(tmp_path, options, expected):
    target = tmp_path / "last-success.epoch"
    target.write_bytes(b"old\n")
    before = target.stat().st_mtime_ns
    result = invoke(shell_path(target), runner=True, **options)
    assert result.returncode == expected, result.stderr
    assert target.read_bytes() == b"old\n" and target.stat().st_mtime_ns == before
    assert "pitr_backup=ok" not in result.stdout
    assert list(tmp_path.glob("*.tmp.*")) == []


@pytest.mark.parametrize("fault", ["alloc", "write", "rename"])
def test_publication_error_is_observable_and_cleans_own_temp(tmp_path, fault):
    target = tmp_path / "last-success.epoch"
    target.write_bytes(b"old\n")
    unrelated = tmp_path / "unrelated.tmp.keep"
    unrelated.write_bytes(b"keep")
    result = invoke(shell_path(target), runner=True, fault=fault)
    assert result.returncode != 0
    assert "reason=heartbeat_publish" in result.stdout
    assert "pitr_backup=ok" not in result.stdout
    assert target.read_bytes() == b"old\n"
    assert unrelated.read_bytes() == b"keep"
    assert sorted(p.name for p in tmp_path.iterdir()) == ["last-success.epoch", "unrelated.tmp.keep"]


@pytest.mark.parametrize("epoch", ["", "0", "-1", "+1", "01", "1x0", " 123", "123 ",
                                  "123\n456", "10000000000"])
def test_invalid_epoch_never_replaces_old_record(tmp_path, epoch):
    target = tmp_path / "last-success.epoch"
    target.write_bytes(b"old\n")
    result = invoke(shell_path(target), epoch=epoch)
    assert result.returncode == 9, result.stderr
    assert target.read_bytes() == b"old\n"
    assert list(tmp_path.glob("*.tmp.*")) == []


def test_helper_is_private_and_preserves_caller_state(tmp_path):
    target = tmp_path / "last-success.epoch"
    result = invoke(shell_path(target))
    assert result.returncode == 0, result.stderr
    assert target.read_bytes() == b"1700000000\n"
    assert list(tmp_path.glob("*.tmp.*")) == []


@pytest.mark.parametrize("destination", ["relative", "directory", "missing-parent", "dotdot", "trailing"])
def test_unsupported_destination_is_refused(tmp_path, destination):
    target = tmp_path / "last-success.epoch"
    if destination == "relative":
        value = "relative.epoch"
    elif destination == "directory":
        target.mkdir()
        value = shell_path(target)
    elif destination == "missing-parent":
        value = shell_path(tmp_path / "absent" / "heartbeat")
    elif destination == "dotdot":
        value = shell_path(tmp_path) + "/../unexpected.epoch"
    else:
        value = shell_path(tmp_path) + "/"
    result = invoke(value)
    assert result.returncode == 9, result.stderr
    assert list(tmp_path.glob("*.tmp.*")) == []


@pytest.mark.skipif(os.name == "nt", reason="Windows symlink privilege is not a Linux permission proof")
@pytest.mark.parametrize("dangling", [False, True])
def test_symlink_destination_is_refused(tmp_path, dangling):
    other = tmp_path / "other"
    if not dangling:
        other.write_bytes(b"keep")
    target = tmp_path / "last-success.epoch"
    target.symlink_to(other)
    result = invoke(str(target))
    assert result.returncode == 9, result.stderr
    assert target.is_symlink()
    if not dangling:
        assert other.read_bytes() == b"keep"
