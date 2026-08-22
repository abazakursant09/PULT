#!/bin/sh
# SECURITY-2D-3E1B-3A — integrity verification of a RESTORED target database. Runs against
# the target via PG* env. Expected values (captured from the SOURCE before backup) arrive as
# env: EXPECTED_ALEMBIC_HEAD, EXPECTED_SCHEMA_SHA, EXPECTED_USERS_COUNT, EXPECTED_USERS_CKSUM.
# Fail-closed: any mismatch exits non-zero.
set -eu
: "${PGHOST:?}"; : "${PGDATABASE:?}"; : "${PGUSER:?}"
if [ -n "${PGPASSWORD_FILE:-}" ]; then PGPASSWORD="$(cat "$PGPASSWORD_FILE")"; export PGPASSWORD; fi
EXPECTED_ALEMBIC_HEAD="${EXPECTED_ALEMBIC_HEAD:-csr1a2b3c4d01}"
: "${EXPECTED_SCHEMA_SHA:?}"; : "${EXPECTED_USERS_COUNT:?}"; : "${EXPECTED_USERS_CKSUM:?}"
q() { psql -tAc "$1"; }
fail() { echo "integrity FAIL: $1" >&2; exit 1; }

# 1. Alembic head
HEAD="$(q 'SELECT version_num FROM alembic_version')"
[ "$HEAD" = "$EXPECTED_ALEMBIC_HEAD" ] || fail "alembic head $HEAD != $EXPECTED_ALEMBIC_HEAD"

# 2. Full schema set preserved (every table incl. all critical tables). Compare a sha256 of
#    the sorted public table-name set against the source.
TABLES="$(q "SELECT string_agg(table_name, ',' ORDER BY table_name) FROM information_schema.tables WHERE table_schema='public' AND table_type='BASE TABLE'")"
GOT_SCHEMA_SHA="$(printf '%s' "$TABLES" | sha256sum | cut -d' ' -f1)"
[ "$GOT_SCHEMA_SHA" = "$EXPECTED_SCHEMA_SHA" ] || fail "schema set sha $GOT_SCHEMA_SHA != $EXPECTED_SCHEMA_SHA"

# 3. Explicit presence of the named critical tables (defence-in-depth over the set sha).
for t in users workspaces execution_logs execution_recovery_audit auth_rate_limit_buckets import_records decisions; do
  EX="$(q "SELECT 1 FROM information_schema.tables WHERE table_schema='public' AND table_name='$t'")"
  [ "$EX" = "1" ] || fail "critical table missing after restore: $t"
done

# 4. users data round-trip: count + deterministic row-level checksum.
CNT="$(q 'SELECT count(*) FROM users')"
[ "$CNT" = "$EXPECTED_USERS_COUNT" ] || fail "users count $CNT != $EXPECTED_USERS_COUNT"
CKSUM="$(q "SELECT md5(coalesce(string_agg(id||'|'||email||'|'||name||'|'||hashed_password, ',' ORDER BY id),'')) FROM users")"
[ "$CKSUM" = "$EXPECTED_USERS_CKSUM" ] || fail "users checksum mismatch"

# 5. UNIQUE constraint restored — duplicate email must be rejected.
if psql -v ON_ERROR_STOP=1 -q -c "INSERT INTO users (id,email,name,hashed_password) VALUES ('ffffffff-0000-0000-0000-000000000000','user1@example.invalid','dup','x')" 2>/dev/null; then
  fail "UNIQUE(email) not enforced after restore (duplicate accepted)"
fi

# 6. Fresh synthetic user insert succeeds — sequences/defaults/constraints usable post-restore.
psql -v ON_ERROR_STOP=1 -q -c "INSERT INTO users (id,email,name,hashed_password) VALUES ('aaaaaaaa-0000-0000-0000-000000000000','fresh@example.invalid','Fresh','x')" \
  || fail "fresh synthetic insert failed after restore"

echo "integrity OK: head=$HEAD tables_sha=$GOT_SCHEMA_SHA users=$CNT checksum-match unique-enforced fresh-insert-ok"
