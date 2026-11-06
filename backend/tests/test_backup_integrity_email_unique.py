"""Bounded integrity.sh shell regressions; mocked psql is not PostgreSQL proof."""
import hashlib
import os
from pathlib import Path
import shutil
import subprocess

import pytest


ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "ops/backup/integrity.sh"
TABLES = "alembic_version,auth_rate_limit_buckets,decisions,execution_logs,execution_recovery_audit,import_records,users,workspaces"


def _mocked_runner(case):
    shell = "C:/Program Files/Git/usr/bin/sh.exe" if os.name == "nt" else shutil.which("sh")
    assert shell and Path(shell).is_file(), "POSIX shell required for the focused runner proof"
    # The psql function consumes the actual script's SQL. No database executable,
    # inherited credentials, dotenv, application import or network is involved.
    stubs = r'''
psql() {
  if [ "${1-}" = "-tAc" ]; then
    sql="$2"
  else
    sql=""
    previous=""
    for argument in "$@"; do
      if [ "$previous" = "-c" ]; then sql="$argument"; fi
      previous="$argument"
    done
    if [ -z "$sql" ]; then sql="$(cat)"; fi
  fi
  case "$sql" in
    'SELECT version_num FROM alembic_version')
      if [ "$MOCK_CASE" = "head" ]; then printf 'wrong-head\n'; else printf 'synthetic-head\n'; fi ;;
    'SELECT string_agg(table_name,'*)
      if [ "$MOCK_CASE" = "schema" ]; then printf 'wrong-tables\n'; else printf '%s\n' "$MOCK_TABLES"; fi ;;
    'SELECT 1 FROM information_schema.tables'*)
      if [ "$MOCK_CASE" != "critical_table" ]; then printf '1\n'; fi ;;
    'SELECT count(*) FROM users')
      if [ "$MOCK_CASE" = "count" ]; then printf '9\n'; else printf '1\n'; fi ;;
    'SELECT md5('* )
      if [ "$MOCK_CASE" = "checksum" ]; then printf 'wrong-checksum\n'; else printf 'synthetic-checksum\n'; fi ;;
    *'user1@example.invalid'*)
      printf 'MOCK_DUPLICATE:%s\n' "$MOCK_CASE" >&2
      case "$sql" in
        *'GET STACKED DIAGNOSTICS'*)
          # Mock only process completion. Native SQL qualification is a separate gate.
          if [ "$MOCK_CASE" = "email_unique" ]; then return 0; else return 73; fi ;;
        *)
          if [ "$MOCK_CASE" = "duplicate_accepted" ]; then return 0; else return 73; fi ;;
      esac ;;
    *'fresh@example.invalid'*)
      printf 'MOCK_FRESH\n' >&2
      if [ "$MOCK_CASE" = "fresh_insert" ]; then return 74; fi ;;
    *) printf 'UNEXPECTED_SQL\n' >&2; return 99 ;;
  esac
}
'''
    env = {key: value for key, value in os.environ.items() if key.upper() in {"SYSTEMROOT", "WINDIR", "TEMP", "TMP"}}
    env.update(
        PATH="/usr/bin:/bin", PGHOST="synthetic.invalid", PGDATABASE="synthetic",
        PGUSER="synthetic", EXPECTED_ALEMBIC_HEAD="synthetic-head",
        EXPECTED_SCHEMA_SHA=hashlib.sha256(TABLES.encode()).hexdigest(),
        EXPECTED_USERS_COUNT="1", EXPECTED_USERS_CKSUM="synthetic-checksum",
        MOCK_CASE=case, MOCK_TABLES=TABLES,
    )
    source = SCRIPT.read_text(encoding="utf8").replace("\r\n", "\n")
    return subprocess.run([shell, "-c", stubs + "\n" + source], env=env,
                          capture_output=True, text=True, timeout=10)


@pytest.mark.parametrize("case", ["permission", "connection", "primary_key", "referral_unique", "check_constraint", "syntax"])
def test_duplicate_probe_does_not_accept_unrelated_psql_failure(case):
    result = _mocked_runner(case)
    assert result.returncode != 0, result.stdout + result.stderr
    assert "integrity OK" not in result.stdout
    assert "MOCK_FRESH" not in result.stderr


def test_mocked_email_unique_proof_preserves_fresh_insert():
    result = _mocked_runner("email_unique")
    assert result.returncode == 0, result.stderr
    assert "unique-enforced fresh-insert-ok" in result.stdout
    assert result.stderr.count("MOCK_FRESH") == 1


def test_mocked_accepted_duplicate_fails_before_fresh_insert():
    result = _mocked_runner("duplicate_accepted")
    assert result.returncode != 0
    assert "integrity OK" not in result.stdout
    assert "MOCK_FRESH" not in result.stderr


@pytest.mark.parametrize("case", ["head", "schema", "critical_table", "count", "checksum", "fresh_insert"])
def test_existing_integrity_failures_remain_fail_closed(case):
    result = _mocked_runner(case)
    assert result.returncode != 0, result.stdout + result.stderr
    assert "integrity OK" not in result.stdout
